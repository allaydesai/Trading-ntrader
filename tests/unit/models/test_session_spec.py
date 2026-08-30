"""Unit tests for SessionSpec, StrategySpec, and SessionStatus."""

import ast
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.models import session as session_module
from src.models.session import SessionSpec, SessionStatus, StrategySpec


def _spec(
    strategy_id="sma_crossover",
    overrides=None,
    bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
):
    from src.config import get_settings

    return StrategySpec.from_overrides(
        strategy_id=strategy_id,
        overrides=overrides or {},
        settings=get_settings(),
        bar_types=bar_types,
    )


class TestSessionSpecShape:
    """AC #1: an ordered, non-empty collection of StrategySpec."""

    @pytest.mark.unit
    def test_two_entry_spec_validates_and_preserves_order(self):
        # Arrange — two *different* strategies; a session may not list one twice.
        first = _spec(bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))
        second = _spec(strategy_id="momentum", bar_types=("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",))

        # Act
        spec = SessionSpec(strategies=(first, second))

        # Assert
        assert len(spec.strategies) == 2
        assert spec.strategies[0] is first
        assert spec.strategies[1] is second

    @pytest.mark.unit
    def test_same_instrument_in_two_entries_is_legal_and_deduped_once(self):
        # Arrange — two distinct strategies legitimately trading one instrument.
        first = _spec(bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))
        second = _spec(strategy_id="momentum", bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))

        # Act
        spec = SessionSpec(strategies=(first, second))

        # Assert
        assert len(spec.strategies) == 2
        assert spec.subscription_bar_types.count("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL") == 1

    @pytest.mark.unit
    def test_empty_strategies_collection_rejected_as_too_short(self):
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            SessionSpec(strategies=())

        errors = exc_info.value.errors()
        assert any(error["type"] == "too_short" for error in errors)

    @pytest.mark.unit
    def test_duplicate_strategy_id_rejected(self):
        """Two entries naming one strategy collide on Nautilus' order_id_tag."""
        # Arrange
        first = _spec(bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))
        second = _spec(bar_types=("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",))

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            SessionSpec(strategies=(first, second))

        message = str(exc_info.value)
        assert "sma_crossover" in message
        assert "more than once" in message


class TestOrderIdTagCollision:
    """Task 6.4 (Story 3.2) — a create-time validator closing
    ``deferred-work.md:1748-1757``: ``Trader.add_strategy`` assigns
    ``order_id_tag = f"{len(existing):03d}"`` to any strategy whose tag is
    unset, then raises ``RuntimeError`` if the resolved tag is already taken
    — a failure that used to surface only at ``trading``, for an operator's
    *choice of strategy order*, on a spec that had validated perfectly at
    create time.

    ``sma_crossover`` has no ``order_id_tag`` field at all (resolves
    positionally); ``momentum``'s own parameter model declares one with
    default ``"002"`` (always explicit, once ``_normalise_parameters``
    dumps it) — the only two built-in strategies, so every case here is
    built from them.
    """

    @pytest.mark.unit
    def test_an_explicit_tag_colliding_with_an_auto_assigned_one_is_refused(self):
        """The exact shape deferred-work.md names: an explicit-tag strategy
        colliding with an auto-positional one, order-dependent.
        """
        first = _spec(
            strategy_id="sma_crossover", bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",)
        )
        second = _spec(
            strategy_id="momentum",
            overrides={"order_id_tag": "000"},
            bar_types=("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
        )

        with pytest.raises(ValidationError) as exc_info:
            SessionSpec(strategies=(first, second))

        message = str(exc_info.value)
        assert "000" in message
        assert "momentum" in message

    @pytest.mark.unit
    def test_reordering_the_same_two_strategies_avoids_the_collision(self):
        """Proves the simulation walks in **spec order**, matching
        ``Trader.add_strategy``'s own registration-order dependence: with
        ``momentum`` first, its explicit ``"000"`` is taken before
        ``sma_crossover`` resolves positionally — to ``"001"``, not ``"000"``.
        """
        first = _spec(
            strategy_id="momentum",
            overrides={"order_id_tag": "000"},
            bar_types=("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
        )
        second = _spec(
            strategy_id="sma_crossover", bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",)
        )

        spec = SessionSpec(strategies=(first, second))

        assert len(spec.strategies) == 2

    @pytest.mark.unit
    def test_the_ordinary_default_construction_never_collides(self):
        """Sanity: crossover's positional "000" and momentum's default
        "002" never collide, so the common case is unaffected.
        """
        first = _spec(bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))
        second = _spec(strategy_id="momentum", bar_types=("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",))

        spec = SessionSpec(strategies=(first, second))

        assert len(spec.strategies) == 2

    @pytest.mark.unit
    def test_the_collision_message_names_the_tag_and_the_colliding_strategy(self):
        first = _spec(
            strategy_id="sma_crossover", bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",)
        )
        second = _spec(
            strategy_id="momentum",
            overrides={"order_id_tag": "000"},
            bar_types=("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
        )

        with pytest.raises(ValidationError) as exc_info:
            SessionSpec(strategies=(first, second))

        # Review fix, 2026-08-30: this asserted only
        # `any(error["type"] == "value_error")`, which passes with an empty
        # message, the wrong strategy named, or the whole operator-facing
        # explanation block deleted — while its name promised otherwise.
        message = str(exc_info.value)
        assert "'000'" in message, "the colliding resolved tag must be quoted in the message"
        assert "momentum" in message, "the strategy that collides must be named"
        assert "order_id_tag" in message
        # The message's whole job is telling the operator what to do about it.
        assert "reorder" in message.lower() or "explicit" in message.lower()
        assert any(error["type"] == "value_error" for error in exc_info.value.errors())


class TestStrategyCanonicalisation:
    """AC #2: strategy identifiers resolve through StrategyRegistry to canonical form."""

    @pytest.mark.unit
    def test_alias_resolves_to_canonical_name(self):
        # Act
        spec = _spec(strategy_id="smacrossover")

        # Assert
        assert spec.strategy_id == "sma_crossover"

    @pytest.mark.unit
    def test_alias_and_canonical_specs_are_equal(self):
        # Act
        alias_spec = _spec(strategy_id="smacrossover")
        canonical_spec = _spec(strategy_id="sma_crossover")

        # Assert
        assert alias_spec == canonical_spec

    @pytest.mark.unit
    def test_unknown_strategy_raises_validation_error_listing_registered_names(self):
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(
                strategy_id="nope",
                bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
            )

        message = str(exc_info.value)
        assert "nope" in message
        assert "sma_crossover" in message
        assert "momentum" in message


class TestParameterChain:
    """AC #3: parameters resolve through StrategyLoader.build_strategy_params and freeze."""

    @pytest.mark.unit
    def test_override_wins_and_settings_fill_the_rest_as_decimal(self):
        # Act
        spec = _spec(overrides={"fast_period": 12})

        # Assert
        assert spec.parameters == {
            "fast_period": 12,
            "slow_period": 20,
            "portfolio_value": Decimal("1000000"),
            "position_size_pct": Decimal("10.0"),
        }
        assert type(spec.parameters["portfolio_value"]) is Decimal

    @pytest.mark.unit
    def test_invalid_parameters_raise_validation_error_naming_the_field(self):
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(
                strategy_id="sma_crossover",
                parameters={"fast_period": 50, "slow_period": 10},
                bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
            )

        assert "slow_period" in str(exc_info.value)

    @pytest.mark.unit
    def test_parameter_error_is_not_a_nested_pydantic_dump(self):
        """The inner ValidationError must be reformatted, not stringified whole."""
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(
                strategy_id="sma_crossover",
                parameters={"fast_period": 50, "slow_period": 10},
                bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
            )

        message = str(exc_info.value)
        assert "slow_period" in message
        assert message.count("https://errors.pydantic.dev") <= 1


class TestNoCredentials:
    """AC #4: no connection-secret fields anywhere in the spec."""

    FORBIDDEN_SUBSTRINGS = (
        "host",
        "port",
        "username",
        "password",
        "account",
        "secret",
        "api_key",
        "token",
    )

    # "port" is omitted deliberately: a legitimate parameter such as
    # ``portfolio_value`` contains it, and parameter names are strategy-authored.
    FORBIDDEN_PARAMETER_SUBSTRINGS = (
        "username",
        "password",
        "secret",
        "api_key",
        "token",
    )

    @pytest.mark.unit
    def test_no_field_name_resembles_a_credential(self):
        # Arrange
        field_names = set(SessionSpec.model_fields) | set(StrategySpec.model_fields)

        # Act & Assert
        for name in field_names:
            lowered = name.lower()
            for forbidden in self.FORBIDDEN_SUBSTRINGS:
                assert forbidden not in lowered, f"field {name!r} resembles a credential"

    @pytest.mark.unit
    def test_no_parameter_key_resembles_a_credential(self):
        """``parameters`` is the only free-form surface a secret could reach."""
        # Arrange
        spec = _spec()

        # Act & Assert
        for key in spec.parameters:
            lowered = key.lower()
            for forbidden in self.FORBIDDEN_PARAMETER_SUBSTRINGS:
                assert forbidden not in lowered, f"parameter {key!r} resembles a credential"

    @pytest.mark.unit
    def test_injected_settings_secrets_never_reach_the_dump(self):
        """Sentinel-based, so it asserts something even when the env is unset.

        The previous form read live settings behind ``if value:``; with no ``.env``
        (CI, a fresh clone) ``tws_account``/``tws_password`` are ``""`` and the
        check silently passed without testing anything.
        """
        # Arrange
        sentinel = "SENTINEL-must-not-leak-9f3a"
        settings = SimpleNamespace(
            fast_ema_period=10,
            slow_ema_period=20,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("10.0"),
            tws_account=sentinel,
            tws_password=sentinel,
            tws_username=sentinel,
            ibkr_host=sentinel,
            ibkr_port=sentinel,
        )
        spec = SessionSpec(
            strategies=(
                StrategySpec.from_overrides(
                    strategy_id="sma_crossover",
                    overrides={},
                    settings=settings,
                    bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
                ),
            )
        )

        # Act
        dumped = spec.model_dump_json()

        # Assert
        assert sentinel not in dumped

    @pytest.mark.unit
    def test_json_dump_never_contains_real_credential_values(self):
        # Arrange
        from src.config import get_settings

        settings = get_settings()
        spec = SessionSpec(strategies=(_spec(),))

        # Act
        dumped = spec.model_dump_json()

        # Assert — username added; the port is covered by the sentinel test above,
        # because a bare 4-digit number is not safe to substring-search in JSON.
        for value in (
            settings.ibkr.tws_account,
            settings.ibkr.tws_password,
            settings.ibkr.tws_username,
            settings.ibkr.ibkr_host,
        ):
            if value:
                assert value not in dumped


class TestFrozen:
    """AC #5: rebinding an attribute or appending to the strategy collection is rejected."""

    @pytest.mark.unit
    def test_session_spec_attribute_rebind_rejected(self):
        # Arrange
        spec = SessionSpec(strategies=(_spec(),))

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            spec.strategies = ()
        assert any(error["type"] == "frozen_instance" for error in exc_info.value.errors())

    @pytest.mark.unit
    def test_session_spec_strategies_tuple_has_no_append(self):
        # Arrange
        spec = SessionSpec(strategies=(_spec(),))

        # Act & Assert
        with pytest.raises(AttributeError):
            spec.strategies.append(_spec())

    @pytest.mark.unit
    def test_strategy_spec_attribute_rebind_rejected(self):
        # Arrange
        strategy_spec = _spec()

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            strategy_spec.strategy_id = "momentum"
        assert any(error["type"] == "frozen_instance" for error in exc_info.value.errors())

    @pytest.mark.unit
    def test_strategy_spec_bar_types_tuple_has_no_append(self):
        # Arrange
        strategy_spec = _spec()

        # Act & Assert
        with pytest.raises(AttributeError):
            strategy_spec.bar_types.append("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL")


class TestJSONRoundTrip:
    """AC #5: the model round-trips losslessly, Decimal included."""

    @pytest.mark.unit
    def test_round_trip_equality_and_decimal_type_preserved(self):
        # Arrange
        spec = SessionSpec(strategies=(_spec(overrides={"fast_period": 12}),))

        # Act
        restored = SessionSpec.model_validate_json(spec.model_dump_json())

        # Assert
        assert restored == spec
        assert type(restored.strategies[0].parameters["portfolio_value"]) is Decimal

    @pytest.mark.unit
    def test_round_trip_is_byte_identical(self):
        """Equality alone cannot see Decimal precision loss.

        ``Decimal("10.0") == Decimal("10")`` is True and the type check confirms
        only the class, so a round trip that mangles the exponent passes both
        assertions above. Comparing the serialised forms does not.
        """
        # Arrange
        spec = SessionSpec(strategies=(_spec(overrides={"fast_period": 12}),))

        # Act
        restored = SessionSpec.model_validate_json(spec.model_dump_json())

        # Assert
        assert restored.model_dump_json() == spec.model_dump_json()


class TestSessionStatus:
    """AC #6: SessionStatus is a StrEnum with exactly four values, in order."""

    @pytest.mark.unit
    def test_exactly_four_values_in_order(self):
        assert [m.value for m in SessionStatus] == ["created", "running", "stopped", "sealed"]

    @pytest.mark.unit
    def test_str_renders_the_bare_value(self):
        assert str(SessionStatus.CREATED) == "created"
        assert f"{SessionStatus.SEALED}" == "sealed"

    @pytest.mark.unit
    def test_constructed_from_its_own_value(self):
        assert SessionStatus("created") is SessionStatus.CREATED


class TestBarTypeValidation:
    """AC #7: bar types are validated through resolve_live_bar_types."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "bar_types",
        [
            ("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",),
            ("AAPL.NASDAQ-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL",),
            ("AAPL.NASDAQ-100-TICK-LAST-EXTERNAL",),
            ("garbage",),
            (
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                "aapl.nasdaq-1-minute-last-external",
            ),
            (),
        ],
        ids=[
            "internal-aggregated",
            "composite",
            "not-time-aggregated",
            "unparseable",
            "intra-spec-duplicate",
            "empty",
        ],
    )
    def test_refused_bar_types_raise_validation_error(self, bar_types):
        with pytest.raises(ValidationError):
            StrategySpec(strategy_id="sma_crossover", bar_types=bar_types)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("bar_types", "expected"),
        [
            (("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",), "INTERNAL-aggregated"),
            (
                ("AAPL.NASDAQ-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL",),
                "INTERNAL-aggregated",
            ),
            (("AAPL.NASDAQ-100-TICK-LAST-EXTERNAL",), "not time-aggregated"),
            (("garbage",), "Cannot subscribe to bar type"),
            (
                (
                    "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                    "aapl.nasdaq-1-minute-last-external",
                ),
                "more than once",
            ),
        ],
        ids=[
            "internal-aggregated",
            "composite",
            "not-time-aggregated",
            "unparseable",
            "intra-spec-duplicate",
        ],
    )
    def test_refused_bar_types_carry_the_resolvers_own_message(self, bar_types, expected):
        """AC #7 says "with that function's own message" — assert it, not just the type.

        Without this, replacing the wrapper with ``raise ValueError("bad bar type")``
        keeps every other test in this file green while destroying the operator
        diagnostics the criterion is about.
        """
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(strategy_id="sma_crossover", bar_types=bar_types)

        assert expected in str(exc_info.value)


class TestImportPurity:
    """AC #8: no top-level import of a framework/IO library."""

    FORBIDDEN = {"nautilus_trader", "ibapi", "sqlalchemy", "redis", "psycopg2", "asyncpg"}

    @pytest.mark.unit
    def test_top_level_imports_contain_no_framework_library(self):
        # Arrange
        tree = ast.parse(Path(session_module.__file__).read_text())

        # Act & Assert
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in self.FORBIDDEN
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in self.FORBIDDEN

    @pytest.mark.unit
    def test_importing_src_models_loads_no_framework_library(self):
        """The property AC #8 actually names, measured rather than proxied.

        The AST check above cannot see a *transitive* import: adding
        ``from src.core.strategy_registry import StrategyRegistry`` at the top of
        ``session.py`` passes it while loading all of ``nautilus_trader``. A fresh
        subprocess sidesteps the reason the AST proxy was chosen (pytest has
        already imported half the world by the time an in-process check runs).
        """
        # Arrange
        code = (
            "import sys, src.models;"
            "print(','.join(sorted(m for m in "
            "('nautilus_trader','ibapi','sqlalchemy','redis','psycopg2','asyncpg') "
            "if m in sys.modules)))"
        )

        # Act
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(session_module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        # Assert
        assert result.stdout.strip() == ""

    @pytest.mark.unit
    def test_module_never_reaches_for_settings_or_environment(self):
        """AC #3 clause 2, enforced by a test instead of a one-off grep."""
        # Arrange
        source = Path(session_module.__file__).read_text()
        tree = ast.parse(source)

        # Act
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        # Assert
        assert "get_settings" not in called
        assert "os.environ" not in source
        assert "getenv" not in source


class TestBarTypeCanonicalisation:
    """Decision 1: bar types are stored in the canonical upper-case form."""

    @pytest.mark.unit
    def test_lower_case_bar_type_is_stored_canonically(self):
        # Act
        spec = _spec(bar_types=("aapl.nasdaq-1-minute-last-external",))

        # Assert
        assert spec.bar_types == ("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",)

    @pytest.mark.unit
    def test_differing_case_across_strategies_dedupes_to_one_canonical_entry(self):
        """Without canonicalisation the lowercase form wins and IBKR cannot resolve it."""
        # Arrange
        first = _spec(bar_types=("aapl.nasdaq-1-minute-last-external",))
        second = _spec(strategy_id="momentum", bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))

        # Act
        spec = SessionSpec(strategies=(first, second))

        # Assert
        assert spec.subscription_bar_types == ("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",)

    @pytest.mark.unit
    def test_subscription_bar_types_preserves_first_seen_order(self):
        # Arrange
        first = _spec(
            bar_types=(
                "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            )
        )
        second = _spec(
            strategy_id="momentum",
            bar_types=(
                "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
                "TSLA.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            ),
        )

        # Act
        spec = SessionSpec(strategies=(first, second))

        # Assert
        assert spec.subscription_bar_types == (
            "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
            "TSLA.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        )


class TestInputShapeGuards:
    """Inputs that must fail as a ValidationError rather than leak through."""

    BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

    @pytest.mark.unit
    def test_whitespace_only_strategy_id_rejected(self):
        """A blank id used to skip the registry, the params, and the bar types."""
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(strategy_id="   ", bar_types=(self.BAR_TYPE,))

        assert "blank" in str(exc_info.value).lower()

    @pytest.mark.unit
    def test_empty_strategy_id_still_reports_too_short(self):
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(strategy_id="", bar_types=(self.BAR_TYPE,))

        assert any(e["type"] == "string_too_short" for e in exc_info.value.errors())

    @pytest.mark.unit
    def test_bare_string_bar_types_rejected_with_a_sequence_message(self):
        """A bare string must not be exploded into one bar type per character."""
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(strategy_id="sma_crossover", bar_types=self.BAR_TYPE)

        message = str(exc_info.value)
        assert "single string" in message
        assert "bar type 'A'" not in message

    @pytest.mark.unit
    @pytest.mark.parametrize("bad", [5, 3.2, None], ids=["int", "float", "none"])
    def test_non_sequence_bar_types_raise_validation_error(self, bad):
        # Act & Assert
        with pytest.raises(ValidationError):
            StrategySpec(strategy_id="sma_crossover", bar_types=bad)

    @pytest.mark.unit
    def test_unordered_bar_types_collection_rejected(self):
        """A set has no order, so the stored tuple would vary run to run."""
        # Act & Assert
        with pytest.raises(ValidationError):
            StrategySpec(strategy_id="sma_crossover", bar_types={self.BAR_TYPE})

    @pytest.mark.unit
    @pytest.mark.parametrize("bad", [[], 0, "", False], ids=["list", "zero", "str", "false"])
    def test_falsy_non_dict_parameters_rejected(self, bad):
        """``or {}`` used to turn each of these into a silent all-defaults spec."""
        # Act & Assert
        with pytest.raises(ValidationError):
            StrategySpec(strategy_id="sma_crossover", parameters=bad, bar_types=(self.BAR_TYPE,))

    @pytest.mark.unit
    def test_unknown_key_on_strategy_spec_rejected(self):
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(
                strategy_id="sma_crossover",
                parametrs={"fast_period": 3},
                bar_types=(self.BAR_TYPE,),
            )

        assert any(e["type"] == "extra_forbidden" for e in exc_info.value.errors())

    @pytest.mark.unit
    def test_unknown_key_on_session_spec_rejected(self):
        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            SessionSpec(strategies=(_spec(),), schema_versoin=2)

        assert any(e["type"] == "extra_forbidden" for e in exc_info.value.errors())


class TestFromOverridesFailurePaths:
    """``from_overrides`` runs before pydantic, so its failures need their own cover."""

    BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

    @staticmethod
    def _stub_registry(monkeypatch, definition):
        from src.core.strategy_registry import StrategyRegistry

        monkeypatch.setattr(StrategyRegistry, "get", staticmethod(lambda _id: definition))

    @pytest.mark.unit
    def test_fuzzy_identifier_resolves_like_the_constructor(self):
        """``build_strategy_params`` lacks the fuzzy branch ``get()`` has."""
        # Act
        spec = _spec(strategy_id="sma-crossover")

        # Assert
        assert spec.strategy_id == "sma_crossover"

    @pytest.mark.unit
    def test_unknown_strategy_names_the_registered_set(self):
        # Arrange
        from src.config import get_settings

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            StrategySpec.from_overrides(
                strategy_id="nope",
                overrides={},
                settings=get_settings(),
                bar_types=(self.BAR_TYPE,),
            )

        assert "sma_crossover" in str(exc_info.value)

    @pytest.mark.unit
    def test_strategy_without_param_model_reports_remediation(self, monkeypatch):
        """Used to die at strategy_factory.py with a bare AttributeError."""
        # Arrange
        from src.config import get_settings

        self._stub_registry(monkeypatch, SimpleNamespace(name="fake_noparam", param_model=None))

        # Act & Assert
        with pytest.raises(ValueError) as exc_info:
            StrategySpec.from_overrides(
                strategy_id="fake_noparam",
                overrides={},
                settings=get_settings(),
                bar_types=(self.BAR_TYPE,),
            )

        assert "set_param_model" in str(exc_info.value)

    @pytest.mark.unit
    def test_constructor_reports_remediation_for_missing_param_model(self, monkeypatch):
        # Arrange
        self._stub_registry(monkeypatch, SimpleNamespace(name="fake_noparam", param_model=None))

        # Act & Assert
        with pytest.raises(ValidationError) as exc_info:
            StrategySpec(strategy_id="fake_noparam", bar_types=(self.BAR_TYPE,))

        assert "set_param_model" in str(exc_info.value)


class TestSettingsChain:
    """AC #3 link 2: the ``_settings_map`` step, made falsifiable."""

    @pytest.mark.unit
    def test_settings_values_fill_unspecified_parameters(self):
        """Every value here differs from SMAParameters' own default.

        The repo's real settings happen to equal those defaults, so an assertion
        against live settings passes even if ``build_strategy_params`` skipped the
        settings layer entirely.
        """
        # Arrange
        settings = SimpleNamespace(
            fast_ema_period=7,
            slow_ema_period=33,
            portfolio_value=Decimal("250000"),
            position_size_pct=Decimal("2.5"),
        )

        # Act
        spec = StrategySpec.from_overrides(
            strategy_id="sma_crossover",
            overrides={},
            settings=settings,
            bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
        )

        # Assert
        assert spec.parameters == {
            "fast_period": 7,
            "slow_period": 33,
            "portfolio_value": Decimal("250000"),
            "position_size_pct": Decimal("2.5"),
        }


class TestKnownLimits:
    """Limits the module docstring discloses, pinned so they stay honest."""

    @pytest.mark.unit
    def test_specs_are_not_hashable(self):
        """``frozen=True`` generates a ``__hash__``; the dict field makes it raise."""
        # Arrange
        spec = _spec()

        # Act & Assert
        with pytest.raises(TypeError, match="unhashable"):
            hash(spec)

    @pytest.mark.unit
    def test_model_copy_update_bypasses_validation(self):
        """A documented hole: ``model_copy(update=)`` skips frozen and the validator."""
        # Arrange
        spec = SessionSpec(strategies=(_spec(),))

        # Act
        bypassed = spec.model_copy(update={"strategies": ()})

        # Assert
        assert bypassed.strategies == ()

    @pytest.mark.unit
    def test_parameters_dict_is_mutable_in_place(self):
        # Arrange
        spec = _spec()

        # Act
        spec.parameters["fast_period"] = 999

        # Assert
        assert spec.parameters["fast_period"] == 999

    @pytest.mark.unit
    def test_schema_version_defaults_to_one_and_rejects_zero(self):
        # Assert
        assert SessionSpec(strategies=(_spec(),)).schema_version == 1
        with pytest.raises(ValidationError):
            SessionSpec(strategies=(_spec(),), schema_version=0)


class TestStoredForm:
    """Story 2.2, AC #6 and #10: the to_stored()/from_stored() persistence pair."""

    @pytest.mark.unit
    def test_from_stored_of_to_stored_round_trips_equal(self):
        # Arrange
        spec = SessionSpec(strategies=(_spec(overrides={"fast_period": 12}),))

        # Act
        restored = SessionSpec.from_stored(spec.to_stored())

        # Assert
        assert restored == spec

    @pytest.mark.unit
    def test_decimal_survives_the_stored_round_trip(self):
        # Arrange
        spec = SessionSpec(strategies=(_spec(overrides={"fast_period": 12}),))

        # Act
        restored = SessionSpec.from_stored(spec.to_stored())

        # Assert
        assert type(restored.strategies[0].parameters["portfolio_value"]) is Decimal
        assert restored.strategies[0].parameters["portfolio_value"] == Decimal("1000000")

    @pytest.mark.unit
    def test_to_stored_is_json_dumpable_without_raising(self):
        """The guard that the serialiser is mode="json", not a bare model_dump()."""
        import json

        # Arrange
        spec = SessionSpec(strategies=(_spec(),))

        # Act & Assert — must not raise TypeError on Decimal.
        json.dumps(spec.to_stored())

    @pytest.mark.unit
    def test_to_stored_bare_model_dump_would_raise_on_decimal(self):
        """Documents *why* to_stored exists: bare model_dump() cannot be JSON-dumped."""
        import json

        # Arrange
        spec = SessionSpec(strategies=(_spec(),))

        # Act & Assert
        with pytest.raises(TypeError):
            json.dumps(spec.model_dump())

    @pytest.mark.unit
    def test_from_stored_refuses_a_too_new_schema_version(self):
        # Arrange
        spec = SessionSpec(strategies=(_spec(),))
        payload = spec.to_stored()
        payload["schema_version"] = 999

        # Act & Assert
        with pytest.raises(ValueError, match="schema_version"):
            SessionSpec.from_stored(payload)

    @pytest.mark.unit
    def test_from_stored_accepts_a_payload_with_no_schema_version_key(self):
        """A hand-written or pre-versioning row must not raise a bare KeyError."""
        # Arrange
        spec = SessionSpec(strategies=(_spec(),))
        payload = spec.to_stored()
        del payload["schema_version"]

        # Act
        restored = SessionSpec.from_stored(payload)

        # Assert
        assert restored.schema_version == 1

    @pytest.mark.unit
    def test_from_stored_rejects_a_non_dict_payload(self):
        # Act & Assert
        with pytest.raises(ValueError):
            SessionSpec.from_stored("not-a-dict")

    @pytest.mark.unit
    @pytest.mark.parametrize("version", ["2", "1", None, 1.5, [1]])
    def test_from_stored_refuses_a_non_integer_schema_version_legibly(self, version):
        """A hand-edited JSONB row must refuse with ValueError, never a bare TypeError.

        ``>`` against a str/None/float raises ``TypeError``, which is the same
        class of illegible failure the ``payload.get(...)`` default exists to
        prevent. The gate must name the offending value instead.
        """
        # Arrange
        payload = SessionSpec(strategies=(_spec(),)).to_stored()
        payload["schema_version"] = version

        # Act & Assert
        with pytest.raises(ValueError, match="schema_version") as excinfo:
            SessionSpec.from_stored(payload)
        assert not isinstance(excinfo.value, TypeError)

    @pytest.mark.unit
    def test_from_stored_refuses_a_boolean_schema_version(self):
        """``True`` is an ``int`` subclass and would otherwise pass as version 1."""
        # Arrange
        payload = SessionSpec(strategies=(_spec(),)).to_stored()
        payload["schema_version"] = True

        # Act & Assert
        with pytest.raises(ValueError, match="schema_version"):
            SessionSpec.from_stored(payload)

    @pytest.mark.unit
    def test_written_schema_version_is_sourced_from_the_module_constant(self):
        """The writer and the read gate must not be able to drift.

        ``SPEC_SCHEMA_VERSION`` gates reads; the field default is what every
        written row carries. Two independent literals would let a future bump
        stamp new rows with a stale version the gate then never fires on.
        """
        # Act
        spec = SessionSpec(strategies=(_spec(),))

        # Assert
        assert spec.schema_version == session_module.SPEC_SCHEMA_VERSION
        assert spec.to_stored()["schema_version"] == session_module.SPEC_SCHEMA_VERSION

        # The two assertions above are necessary but NOT sufficient: while
        # SPEC_SCHEMA_VERSION == 1, they pass identically against a hard-coded
        # `default=1` — verified by mutation. The coupling is a structural
        # property, so assert it structurally, on the source.
        module = ast.parse(Path(session_module.__file__).read_text(encoding="utf-8"))
        session_spec_class = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "SessionSpec"
        )
        field_call = next(
            node.value
            for node in session_spec_class.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "schema_version"
        )
        default = next(kw.value for kw in field_call.keywords if kw.arg == "default")
        assert isinstance(default, ast.Name) and default.id == "SPEC_SCHEMA_VERSION", (
            "SessionSpec.schema_version must default to the SPEC_SCHEMA_VERSION constant, not a "
            "second literal — otherwise a future bump stamps rows with a version the gate in "
            "from_stored() never fires on."
        )
