"""Unit tests for SessionSpec, StrategySpec, and SessionStatus."""

import ast
from decimal import Decimal
from pathlib import Path

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
        # Arrange
        first = _spec(bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))
        second = _spec(bar_types=("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",))

        # Act
        spec = SessionSpec(strategies=(first, second))

        # Assert
        assert len(spec.strategies) == 2
        assert spec.strategies[0] is first
        assert spec.strategies[1] is second

    @pytest.mark.unit
    def test_same_instrument_in_two_entries_is_legal_and_deduped_once(self):
        # Arrange
        first = _spec(bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))
        second = _spec(bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",))

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
    def test_json_dump_never_contains_real_credential_values(self):
        # Arrange
        from src.config import get_settings

        settings = get_settings()
        spec = SessionSpec(strategies=(_spec(),))

        # Act
        dumped = spec.model_dump_json()

        # Assert
        for value in (
            settings.ibkr.tws_account,
            settings.ibkr.tws_password,
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
