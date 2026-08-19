"""Component tests for TradingNode config assembly (Story 1.3).

Component tier, not unit: this module imports ``nautilus_trader.config``, which
the unit tier is defined to exclude (pytest.ini:25). Assembling a
``TradingNodeConfig`` plus both IB client configs does not touch the Nautilus C
logging subsystem — verified empirically, ``is_logging_initialized()`` stays
``False`` throughout this file — so it is safe for the parallel, non-forked
component suite. Anything that constructs a ``TradingNode`` belongs in the
integration tier instead (tests/integration/core/test_live_node_lifecycle.py).
"""

import ast
import importlib
from pathlib import Path

import pytest
from ibapi.common import MarketDataTypeEnum  # type: ignore[import-untyped]
from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
    InteractiveBrokersExecClientConfig,
)
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.model.data import BarType

from src.config import IBKRSettings
from src.core import live_node_builder
from src.core.live_bar_observer import (
    LiveBarObserver,
    LiveBarObserverConfig,
    build_bar_observer_config,
)
from src.core.live_gate import GateRefusalReason
from src.core.live_market_data import LiveMarketDataError
from src.core.live_node_builder import (
    GateRefusedError,
    LiveNodeConfigError,
    build_trading_node_config,
)

TRADER_ID = "PAPER-a1b2c3d4"
AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
MSFT_1MIN = "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce the claim this file's tier placement rests on.

    The module docstring justifies living in the parallel, non-forked component
    suite on config assembly never touching the Nautilus C logging subsystem.
    That was verified once by hand; this fixture keeps it verified. A Nautilus
    upgrade — or a new import in ``live_node_builder`` — that initialised C
    logging at assembly time would otherwise turn this suite into intermittent
    native crashes with nothing pointing here.

    The assertion is on the *delta*, not the absolute state: under ``-n auto``
    this file shares a worker process with the rest of the component tier, and
    something else in that tier does initialise C logging (observed 2026-08-05
    — an absolute ``not is_logging_initialized()`` check errors all 38 tests
    here in a full run while passing standalone). What this file must never do
    is *change* the state, and that is exactly what is checked.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — config assembly must not touch the "
        "C logging subsystem, and the non-forked, parallel component tier cannot "
        "host anything that does. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_market_data_env(monkeypatch):
    """Keep the shell out of the fields whose *set-ness* the builder reads.

    ``_env_file=None`` disables the dotenv file but not ``os.environ``, and
    ``resolve_live_market_data_type`` branches on ``model_fields_set`` — so an
    exported ``IBKR_MARKET_DATA_TYPE`` marks the field set and turns every
    ``market_data_type=None`` build in this file into a refusal. Both casings are
    cleared; ``case_sensitive: False`` makes them equal aliases.
    """
    for name in ("IBKR_MARKET_DATA_TYPE", "IBKR_USE_RTH", "IBKR_MARKET_DATA_LINES"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(
    *,
    mode: str = "paper",
    port: int = 4002,
    host: str = "127.0.0.1",
    account: str = "DU4076626",
    real_money_account: str = "",
    live_client_id: int = 10,
    client_id: int = 1,
    read_only: bool = True,
    connection_timeout: int = 300,
    request_timeout: int = 60,
    market_data_type: str | None = None,
    use_rth: bool = True,
    market_data_lines: int = 100,
) -> IBKRSettings:
    """Build settings with every field this module reads, passed explicitly.

    Init kwargs outrank the environment in pydantic-settings, so the
    developer's shell cannot become test input (mirrors
    tests/unit/core/test_live_gate.py:26-46).

    ``market_data_type`` is the one deliberate exception, and defaults to
    ``None`` = *leave the field unset*. That is not laziness: the live path
    distinguishes "unset, so override the DELAYED_FROZEN fetch default" from
    "explicitly configured", and it reads ``model_fields_set`` to do it. Passing
    the string ``"DELAYED_FROZEN"`` here would mark the field set and turn every
    build in this file into a refusal.
    """
    overrides = {} if market_data_type is None else {"ibkr_market_data_type": market_data_type}
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode=mode,
        ibkr_port=port,
        ibkr_host=host,
        tws_account=account,
        ntrader_real_money_account=real_money_account,
        ibkr_live_client_id=live_client_id,
        ibkr_client_id=client_id,
        ibkr_read_only=read_only,
        ibkr_connection_timeout=connection_timeout,
        ibkr_request_timeout=request_timeout,
        ibkr_use_rth=use_rth,
        ibkr_market_data_lines=market_data_lines,
        **overrides,
    )


class TestConfigShape:
    """AC #1 — a permitted build returns exactly one IB data and exec client."""

    @pytest.mark.component
    def test_permitted_build_returns_configured_ib_clients(self):
        # Arrange
        settings = _settings()

        # Act
        cfg = build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert
        assert isinstance(cfg, TradingNodeConfig)
        assert set(cfg.data_clients) == {IB}
        assert set(cfg.exec_clients) == {IB}
        assert isinstance(cfg.data_clients[IB], InteractiveBrokersDataClientConfig)
        assert isinstance(cfg.exec_clients[IB], InteractiveBrokersExecClientConfig)

    @pytest.mark.component
    def test_every_setting_lands_on_the_config_it_belongs_to(self):
        """Without this, swapping the two timeouts keeps the suite green."""
        # Arrange — values distinct from every default, so a mis-wire shows up
        settings = _settings(host="10.0.0.7", port=7497, connection_timeout=123, request_timeout=45)

        # Act
        cfg = build_trading_node_config(settings, trader_id=TRADER_ID)
        data, exec_ = cfg.data_clients[IB], cfg.exec_clients[IB]

        # Assert
        assert str(cfg.trader_id) == TRADER_ID
        assert (data.ibg_host, data.ibg_port) == ("10.0.0.7", 7497)
        assert (exec_.ibg_host, exec_.ibg_port) == ("10.0.0.7", 7497)
        assert data.connection_timeout == 123
        assert data.request_timeout == 45
        assert exec_.connection_timeout == 123
        assert exec_.account_id == "DU4076626"

    @pytest.mark.component
    def test_account_is_stripped_before_it_reaches_the_exec_client(self):
        # Arrange
        settings = _settings(account="  DU4076626  ")

        # Act
        cfg = build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert
        assert cfg.exec_clients[IB].account_id == "DU4076626"


class TestTraderIdValidation:
    """A malformed trader_id must not reach Nautilus.

    ``TradingNodeConfig`` coerces to ``TraderId``, which **panics in Rust and
    aborts the process** when the value contains no ``-`` — uncatchable, even by
    ``except BaseException``. Guarding here mirrors the ``TWS_ACCOUNT`` guard:
    turn a framework trap into a legible error. Epic 2 still owns *deriving* the
    value (AR10); this only validates what it is handed.
    """

    @pytest.mark.component
    @pytest.mark.parametrize("bad", ["PAPER_a1b2c3d4", "probe", "trader1", "BAD"])
    def test_trader_id_without_a_hyphen_raises_live_node_config_error(self, bad):
        # Act
        with pytest.raises(LiveNodeConfigError) as exc_info:
            build_trading_node_config(_settings(), trader_id=bad)

        # Assert — names the offending parameter and the rule it broke
        assert "trader_id" in str(exc_info.value)
        assert "-" in str(exc_info.value)

    @pytest.mark.component
    @pytest.mark.parametrize("bad", ["", "   "])
    def test_empty_trader_id_raises_live_node_config_error(self, bad):
        # Act & Assert — a bare ValueError here would break the Raises: contract
        with pytest.raises(LiveNodeConfigError):
            build_trading_node_config(_settings(), trader_id=bad)

    @pytest.mark.component
    def test_the_guard_runs_before_any_client_config_is_constructed(self, monkeypatch):
        """Same ordering discipline as the gate: fail before building anything."""

        # Arrange
        def _boom(*args, **kwargs):
            raise AssertionError("client config constructed despite a bad trader_id")

        monkeypatch.setattr(live_node_builder, "InteractiveBrokersDataClientConfig", _boom)
        monkeypatch.setattr(live_node_builder, "InteractiveBrokersExecClientConfig", _boom)

        # Act & Assert
        with pytest.raises(LiveNodeConfigError):
            build_trading_node_config(_settings(), trader_id="no-hyphen-here".replace("-", "_"))


class TestAccountNormalisation:
    """The gate upper-cases for its prefix check; the account_id must match."""

    @pytest.mark.component
    @pytest.mark.parametrize("account", ["du4076626", "Du4076626", "dU4076626"])
    def test_lowercase_account_is_normalised_before_reaching_the_exec_client(self, account):
        # Arrange — live_gate.py:214 compares account.upper().startswith(...),
        # so these all pass the gate. IBKR reports account ids uppercase, so
        # Story 1.4's Layer 2 check would mismatch on the un-normalised form.
        settings = _settings(account=account)

        # Act
        cfg = build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert
        assert cfg.exec_clients[IB].account_id == "DU4076626"


class TestTimeoutValidation:
    """Non-positive timeouts mean 'expire immediately' to asyncio.wait_for."""

    @pytest.mark.component
    @pytest.mark.parametrize("timeout", [0, -5])
    def test_non_positive_connection_timeout_raises_live_node_config_error(self, timeout):
        # Act
        with pytest.raises(LiveNodeConfigError) as exc_info:
            build_trading_node_config(_settings(connection_timeout=timeout), trader_id=TRADER_ID)

        # Assert
        assert "IBKR_CONNECTION_TIMEOUT" in str(exc_info.value)

    @pytest.mark.component
    @pytest.mark.parametrize("timeout", [0, -5])
    def test_non_positive_request_timeout_raises_live_node_config_error(self, timeout):
        # Act
        with pytest.raises(LiveNodeConfigError) as exc_info:
            build_trading_node_config(_settings(request_timeout=timeout), trader_id=TRADER_ID)

        # Assert
        assert "IBKR_REQUEST_TIMEOUT" in str(exc_info.value)


class TestClientId:
    """AC #5 — both clients carry ibkr_live_client_id, never ibkr_client_id."""

    @pytest.mark.component
    @pytest.mark.parametrize(("client_id", "live_client_id"), [(1, 10), (2, 20)])
    def test_both_clients_use_the_live_client_id_not_the_historical_one(
        self, client_id, live_client_id
    ):
        # Arrange
        settings = _settings(client_id=client_id, live_client_id=live_client_id)

        # Act
        cfg = build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert
        assert cfg.data_clients[IB].ibg_client_id == live_client_id
        assert cfg.exec_clients[IB].ibg_client_id == live_client_id
        assert cfg.data_clients[IB].ibg_client_id != client_id
        assert cfg.exec_clients[IB].ibg_client_id != client_id


class TestGateOrdering:
    """AC #2 — the gate runs first, and a refusal short-circuits before any
    client config is constructed. Ordering, not just outcome."""

    @pytest.mark.component
    def test_refusing_configuration_raises_gate_refused_error_with_refusal(self):
        # Arrange — a non-paper port refuses
        settings = _settings(port=7496)

        # Act
        with pytest.raises(GateRefusedError) as exc_info:
            build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert
        refusal = exc_info.value.refusal
        assert refusal.reason is GateRefusalReason.NON_PAPER_PORT
        assert str(exc_info.value) == refusal.message

    @pytest.mark.component
    def test_no_client_config_constructed_before_gate_runs(self, monkeypatch):
        """The only test that proves ordering: asserting 'it raised' alone
        would pass even if the configs were built first and thrown away."""

        # Arrange — sentinels raise if the builder ever calls them
        def _boom(*args, **kwargs):
            raise AssertionError("client config constructed despite gate refusal")

        monkeypatch.setattr(live_node_builder, "InteractiveBrokersDataClientConfig", _boom)
        monkeypatch.setattr(live_node_builder, "InteractiveBrokersExecClientConfig", _boom)
        settings = _settings(port=7496)

        # Act & Assert — the refusal path raises GateRefusedError, not the sentinel
        with pytest.raises(GateRefusedError):
            build_trading_node_config(settings, trader_id=TRADER_ID)

    @pytest.mark.component
    def test_the_sentinels_must_be_able_to_fire(self, monkeypatch):
        """Positive control for the test above.

        Without this, the ordering test's passing condition is exactly the
        weaker one its own docstring disparages — "GateRefusedError was
        raised" — and it would stay green if the monkeypatch ever stopped
        intercepting (e.g. someone moves the imports inside the function, or
        aliases them). This proves the sentinels are the objects production
        actually calls, so the ordering test is really testing ordering.
        """

        # Arrange — same sentinels, but a configuration the gate PERMITS
        def _boom(*args, **kwargs):
            raise AssertionError("client config constructed")

        monkeypatch.setattr(live_node_builder, "InteractiveBrokersDataClientConfig", _boom)
        monkeypatch.setattr(live_node_builder, "InteractiveBrokersExecClientConfig", _boom)

        # Act & Assert — past the gate, the builder must reach the sentinels
        with pytest.raises(AssertionError, match="client config constructed"):
            build_trading_node_config(_settings(), trader_id=TRADER_ID)


class TestReadOnlyDerivedView:
    """AC #3 — ibkr_read_only=False is an in-process derived view only."""

    @pytest.mark.component
    def test_permitted_build_yields_exec_client_even_when_input_read_only_is_true(self):
        # Arrange — the gate is the control, not this flag (AR43)
        settings = _settings(read_only=True)

        # Act
        cfg = build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert — a usable exec client, not merely a populated dict key:
        # `is not None` alone holds for every input, including one where the
        # read-only handling was deleted outright.
        exec_config = cfg.exec_clients[IB]
        assert isinstance(exec_config, InteractiveBrokersExecClientConfig)
        assert exec_config.account_id == "DU4076626"
        assert exec_config.ibg_client_id == 10

    @pytest.mark.component
    def test_callers_settings_object_is_not_mutated(self):
        # Arrange
        settings = _settings(read_only=True)

        # Act
        build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert — the False is a derived view, never written back
        assert settings.ibkr_read_only is True


def _ibkr_read_only_occurrences(tree: ast.Module) -> list[ast.AST]:
    """Every node referencing ``ibkr_read_only``, in any spelling.

    Three forms count: ``settings.ibkr_read_only`` (an ``ast.Attribute``),
    ``update={"ibkr_read_only": False}`` (an ``ast.Constant`` string key), and
    ``update=dict(ibkr_read_only=False)`` (an ``ast.keyword``, whose ``arg`` is
    a bare ``str`` field rather than a node — miss it and the equivalent
    rewrite reads as *zero* occurrences, failing the count assertion for the
    opposite reason and masking the intent).
    """
    occurrences: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "ibkr_read_only":
            occurrences.append(node)
        elif isinstance(node, ast.Constant) and node.value == "ibkr_read_only":
            occurrences.append(node)
        elif isinstance(node, ast.keyword) and node.arg == "ibkr_read_only":
            occurrences.append(node)
    return occurrences


def _is_inside_boolean_test(tree: ast.Module, target: ast.AST) -> bool:
    """True if ``target`` is evaluated as a condition anywhere.

    Covers every shape that branches on a value: ``if``/``while`` tests,
    ``assert`` tests, ``match`` subjects and case guards, comprehension ``if``
    clauses, boolean operators, unary ``not``, and the *condition* of a ternary.

    Note the ternary is deliberately narrowed to ``node.test``: walking the
    whole ``ast.IfExp`` would flag ``x = settings.ibkr_read_only if cond else
    None``, where the flag is the produced *value*, not the condition.
    """
    condition_ids: set[int] = set()

    def _mark(expr: ast.AST | None) -> None:
        if expr is not None:
            condition_ids.update(id(descendant) for descendant in ast.walk(expr))

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.Assert, ast.IfExp)):
            _mark(node.test)
        elif isinstance(node, ast.Match):
            _mark(node.subject)
        elif isinstance(node, ast.match_case):
            _mark(node.guard)
        elif isinstance(node, ast.comprehension):
            for condition in node.ifs:
                _mark(condition)
        elif isinstance(node, ast.BoolOp):
            _mark(node)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            _mark(node.operand)
    return id(target) in condition_ids


class TestReadOnlyNeverBranchedOn:
    """AC #3 — ibkr_read_only is used exactly once and never as a condition.

    tests/unit/core/test_live_gate.py:373-500 is the in-repo precedent for an
    AST-based structural assertion, including its own "the guard must be able
    to fail" meta-test — this follows that shape.
    """

    @pytest.mark.component
    def test_ibkr_read_only_appears_exactly_once_and_never_in_a_boolean_test(self):
        # Arrange — encoding pinned: the module is full of em dashes, and
        # read_text() would otherwise use the locale encoding and fail on any
        # non-UTF-8 CI image for a reason unrelated to this assertion.
        tree = ast.parse(Path(live_node_builder.__file__).read_text(encoding="utf-8"))

        # Act
        occurrences = _ibkr_read_only_occurrences(tree)

        # Assert
        assert len(occurrences) == 1, (
            f"expected exactly one reference to ibkr_read_only, found {len(occurrences)}"
        )
        assert not _is_inside_boolean_test(tree, occurrences[0])

    @pytest.mark.component
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            ("if", "def f(s):\n    if s.ibkr_read_only:\n        return 1\n    return 2\n"),
            ("while", "def f(s):\n    while s.ibkr_read_only:\n        break\n"),
            ("assert", "def f(s):\n    assert s.ibkr_read_only\n"),
            ("not", "def f(s):\n    if not s.ibkr_read_only:\n        return 1\n"),
            ("boolop", "def f(s):\n    return s.ibkr_read_only and True\n"),
            ("ternary", "def f(s):\n    return 1 if s.ibkr_read_only else 2\n"),
            ("comprehension", "def f(s, xs):\n    return [x for x in xs if s.ibkr_read_only]\n"),
            (
                "match",
                "def f(s):\n    match s.ibkr_read_only:\n        case True:\n            pass\n",
            ),
            (
                "match-guard",
                "def f(s, x):\n    match x:\n        case 1 if s.ibkr_read_only:\n"
                "            pass\n",
            ),
        ],
    )
    def test_the_guard_must_be_able_to_fail_on_a_branch(self, label, source):
        """The check must actually reject every shape that branches on the flag.

        Each of these was verified to slip past the original ``if``/``while``
        -only implementation; they are the regression net for that gap.
        """
        # Arrange
        tree = ast.parse(source)

        # Act
        occurrences = _ibkr_read_only_occurrences(tree)

        # Assert
        assert len(occurrences) == 1, label
        assert _is_inside_boolean_test(tree, occurrences[0]), f"{label} branch not detected"

    @pytest.mark.component
    def test_the_guard_does_not_flag_a_ternary_that_merely_produces_the_value(self):
        """A value-position ternary is not a branch *on* the flag."""
        # Arrange
        tree = ast.parse("def f(s, cond):\n    return s.ibkr_read_only if cond else None\n")

        # Act
        occurrences = _ibkr_read_only_occurrences(tree)

        # Assert
        assert len(occurrences) == 1
        assert not _is_inside_boolean_test(tree, occurrences[0])

    @pytest.mark.component
    @pytest.mark.parametrize(
        ("label", "source"),
        [
            ("attribute+dict-key", "x = s.ibkr_read_only\ny = {'ibkr_read_only': False}\n"),
            ("attribute+kwarg", "x = s.ibkr_read_only\ny = dict(ibkr_read_only=False)\n"),
        ],
    )
    def test_the_guard_must_be_able_to_fail_on_more_than_one_occurrence(self, label, source):
        """The check must reject a second reference, in any spelling.

        The ``dict(ibkr_read_only=...)`` form is an ``ast.keyword`` whose
        ``arg`` is a bare string field, not a node — invisible to an
        Attribute/Constant-only walk.
        """
        # Arrange
        tree = ast.parse(source)

        # Act
        occurrences = _ibkr_read_only_occurrences(tree)

        # Assert
        assert len(occurrences) == 2, label


class TestMissingAccount:
    """An empty TWS_ACCOUNT passes the gate but cannot build an exec client."""

    @pytest.mark.component
    def test_empty_tws_account_raises_live_node_config_error_naming_tws_account(self):
        # Arrange — the gate permits an empty account on the paper path
        settings = _settings(account="")

        # Act
        with pytest.raises(LiveNodeConfigError) as exc_info:
            build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert — legible error naming the missing variable, nothing to leak
        assert "TWS_ACCOUNT" in str(exc_info.value)


class TestMarketDataConfiguration:
    """Story 1.5 AC #1/#2 — REALTIME and RTH reach the data client explicitly."""

    @pytest.mark.component
    def test_data_client_requests_realtime_not_the_fetch_default(self):
        """The DELAYED_FROZEN default belongs to catalog fetching, not to a session."""
        # Arrange
        settings = _settings()
        assert settings.ibkr_market_data_type == "DELAYED_FROZEN"

        # Act
        cfg = build_trading_node_config(settings, trader_id=TRADER_ID)

        # Assert
        assert cfg.data_clients[IB].market_data_type == MarketDataTypeEnum.REALTIME

    @pytest.mark.component
    def test_data_client_restricts_bars_to_regular_trading_hours(self):
        # Act
        cfg = build_trading_node_config(_settings(), trader_id=TRADER_ID)

        # Assert
        assert cfg.data_clients[IB].use_regular_trading_hours is True

    @pytest.mark.component
    def test_both_fields_are_passed_explicitly_not_left_to_adapter_defaults(self):
        """The adapter's own defaults agree today — relying on them is the risk.

        ``InteractiveBrokersDataClientConfig`` already defaults
        ``market_data_type`` to REALTIME and ``use_regular_trading_hours`` to
        True (config.py:228-229), so the two assertions above would stay green if
        the builder passed neither and a future adapter release flipped either
        one. This assertion would not.
        """
        # Arrange
        tree = ast.parse(Path(live_node_builder.__file__).read_text(encoding="utf-8"))

        # Act
        keywords = _data_client_call_keywords(tree)

        # Assert
        assert "market_data_type" in keywords
        assert "use_regular_trading_hours" in keywords

    @pytest.mark.component
    def test_the_explicitness_guard_can_fail(self):
        """A guard that cannot fail is worse than no guard (Story 1.3's lesson)."""
        # Arrange — a call site that leaves both to the adapter's defaults
        tree = ast.parse(
            "InteractiveBrokersDataClientConfig(ibg_host=host, ibg_port=port)\n",
        )

        # Act
        keywords = _data_client_call_keywords(tree)

        # Assert
        assert "market_data_type" not in keywords
        assert "use_regular_trading_hours" not in keywords

    @pytest.mark.component
    def test_an_explicit_delayed_configuration_is_refused(self):
        # Arrange
        settings = _settings(market_data_type="DELAYED_FROZEN")

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as exc_info:
            build_trading_node_config(settings, trader_id=TRADER_ID)

        assert "REALTIME" in str(exc_info.value)

    @pytest.mark.component
    def test_disabling_rth_is_refused(self):
        # Arrange
        settings = _settings(use_rth=False)

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as exc_info:
            build_trading_node_config(settings, trader_id=TRADER_ID)

        assert "IBKR_USE_RTH" in str(exc_info.value)

    @pytest.mark.component
    def test_market_data_resolution_runs_before_any_client_config_is_constructed(self, monkeypatch):
        """Ordering is the contract, not merely the outcome (mirrors AC #2 of Story 1.3)."""

        # Arrange
        def _boom(*args, **kwargs):
            raise AssertionError("client config constructed despite an unusable market-data config")

        monkeypatch.setattr(live_node_builder, "InteractiveBrokersDataClientConfig", _boom)
        monkeypatch.setattr(live_node_builder, "InteractiveBrokersExecClientConfig", _boom)

        # Act / Assert
        with pytest.raises(LiveMarketDataError):
            build_trading_node_config(_settings(market_data_type="DELAYED"), trader_id=TRADER_ID)


class TestInstrumentLoading:
    """Bars never arrive for a contract the instrument provider did not load."""

    @pytest.mark.component
    def test_no_bar_types_leaves_load_ids_empty(self):
        """Story 1.3's callers build a node with no subscriptions; that stays legal."""
        # Act
        cfg = build_trading_node_config(_settings(), trader_id=TRADER_ID)

        # Assert
        assert not cfg.data_clients[IB].instrument_provider.load_ids

    @pytest.mark.component
    def test_bar_types_become_distinct_instrument_load_ids(self):
        """Without load_ids the adapter logs "instrument not found" and returns.

        No exception, no retry, no bars — see
        adapters/interactive_brokers/data.py:248-254.
        """
        # Act
        cfg = build_trading_node_config(
            _settings(),
            trader_id=TRADER_ID,
            bar_types=[AAPL_1MIN, "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL", MSFT_1MIN],
        )

        # Assert
        assert cfg.data_clients[IB].instrument_provider.load_ids == frozenset(
            {"AAPL.NASDAQ", "MSFT.NASDAQ"}
        )

    @pytest.mark.component
    def test_an_unparseable_bar_type_is_refused(self):
        # Act / Assert
        with pytest.raises(LiveMarketDataError):
            build_trading_node_config(_settings(), trader_id=TRADER_ID, bar_types=["nonsense"])


class TestMarketDataLineBudgetAtStartup:
    """Story 1.5 AC #4 — refuse before a socket opens, naming the limit."""

    @pytest.mark.component
    def test_a_session_within_budget_builds(self):
        # Act
        cfg = build_trading_node_config(
            _settings(market_data_lines=2),
            trader_id=TRADER_ID,
            bar_types=[AAPL_1MIN, MSFT_1MIN],
        )

        # Assert
        assert isinstance(cfg, TradingNodeConfig)

    @pytest.mark.component
    def test_exceeding_the_budget_fails_at_startup_naming_limit_and_count(self):
        # Act / Assert
        with pytest.raises(LiveMarketDataError) as exc_info:
            build_trading_node_config(
                _settings(market_data_lines=1),
                trader_id=TRADER_ID,
                bar_types=[AAPL_1MIN, MSFT_1MIN],
            )

        message = str(exc_info.value)
        assert "2 streaming subscription" in message
        assert "1 concurrent market-data line" in message

    @pytest.mark.component
    def test_the_budget_check_runs_before_any_client_config_is_constructed(self, monkeypatch):
        """A refusal must precede anything that could open a connection."""

        # Arrange
        def _boom(*args, **kwargs):
            raise AssertionError("client config constructed despite an over-budget session")

        monkeypatch.setattr(live_node_builder, "InteractiveBrokersDataClientConfig", _boom)
        monkeypatch.setattr(live_node_builder, "InteractiveBrokersExecClientConfig", _boom)

        # Act / Assert
        with pytest.raises(LiveMarketDataError):
            build_trading_node_config(
                _settings(market_data_lines=1),
                trader_id=TRADER_ID,
                bar_types=[AAPL_1MIN, MSFT_1MIN],
            )

    @pytest.mark.component
    def test_the_gate_still_runs_first(self):
        """Safety ordering is unchanged: a refused connection never reaches this story."""
        # Arrange — over budget AND a non-paper port
        settings = _settings(port=7496, market_data_lines=1)

        # Act / Assert — the gate's refusal, not the budget's
        with pytest.raises(GateRefusedError) as exc_info:
            build_trading_node_config(
                settings, trader_id=TRADER_ID, bar_types=[AAPL_1MIN, MSFT_1MIN]
            )

        assert exc_info.value.refusal.reason is GateRefusalReason.NON_PAPER_PORT


class TestBarObserverWiring:
    """Story 1.5 AC #3 — the observer reaches the node declaratively."""

    @pytest.mark.component
    def test_no_observer_config_leaves_the_node_without_actors(self):
        # Act
        cfg = build_trading_node_config(_settings(), trader_id=TRADER_ID)

        # Assert
        assert cfg.actors == []

    @pytest.mark.component
    def test_an_observer_config_becomes_an_importable_actor_config(self):
        # Arrange
        observer = build_bar_observer_config(_settings(), [AAPL_1MIN])

        # Act
        cfg = build_trading_node_config(
            _settings(), trader_id=TRADER_ID, bar_types=[AAPL_1MIN], bar_observer=observer
        )

        # Assert
        assert len(cfg.actors) == 1
        importable = cfg.actors[0]
        assert importable.actor_path == "src.core.live_bar_observer:LiveBarObserver"
        assert importable.config_path == "src.core.live_bar_observer:LiveBarObserverConfig"
        assert importable.config["bar_types"] == (AAPL_1MIN,)
        assert importable.config["requests_per_second"] == 45

    @pytest.mark.component
    def test_the_importable_paths_actually_resolve(self):
        """A typo'd dotted path only surfaces when the kernel builds the node."""
        # Arrange
        observer = build_bar_observer_config(_settings(), [AAPL_1MIN])
        cfg = build_trading_node_config(
            _settings(), trader_id=TRADER_ID, bar_types=[AAPL_1MIN], bar_observer=observer
        )
        importable = cfg.actors[0]

        # Act
        actor_cls = _resolve_dotted(importable.actor_path)
        config_cls = _resolve_dotted(importable.config_path)

        # Assert
        assert actor_cls is LiveBarObserver
        assert config_cls is LiveBarObserverConfig

    @pytest.mark.component
    def test_an_observer_alone_supplies_the_subscription_set(self):
        """Naming the subscriptions once must be enough, and must still be checked."""
        # Arrange — bar_types omitted entirely
        observer = build_bar_observer_config(_settings(), [AAPL_1MIN, MSFT_1MIN])

        # Act
        cfg = build_trading_node_config(_settings(), trader_id=TRADER_ID, bar_observer=observer)

        # Assert — the observer's set drove load_ids, so nothing is silently unloaded
        assert cfg.data_clients[IB].instrument_provider.load_ids == frozenset(
            {"AAPL.NASDAQ", "MSFT.NASDAQ"}
        )

    @pytest.mark.component
    def test_an_observer_alone_is_still_checked_against_the_line_budget(self):
        """Otherwise the budget is bypassable by naming subscriptions only on the actor."""
        # Arrange
        observer = build_bar_observer_config(_settings(), [AAPL_1MIN, MSFT_1MIN])

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as exc_info:
            build_trading_node_config(
                _settings(market_data_lines=1), trader_id=TRADER_ID, bar_observer=observer
            )

        assert "2 streaming subscription" in str(exc_info.value)

    @pytest.mark.component
    def test_disagreeing_bar_types_and_observer_are_refused(self):
        """The worst outcome this module can produce is a session that sees nothing.

        A subscription whose instrument was never loaded is dropped by the IBKR
        adapter with a log line and no error, so the node would connect, report
        healthy, and never receive a bar.
        """
        # Arrange
        observer = build_bar_observer_config(_settings(), [MSFT_1MIN])

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as exc_info:
            build_trading_node_config(
                _settings(),
                trader_id=TRADER_ID,
                bar_types=[AAPL_1MIN],
                bar_observer=observer,
            )

        message = str(exc_info.value)
        assert AAPL_1MIN in message
        assert MSFT_1MIN in message

    @pytest.mark.component
    def test_matching_bar_types_and_observer_are_accepted_regardless_of_order(self):
        # Arrange
        observer = build_bar_observer_config(_settings(), [MSFT_1MIN, AAPL_1MIN])

        # Act
        cfg = build_trading_node_config(
            _settings(),
            trader_id=TRADER_ID,
            bar_types=[AAPL_1MIN, MSFT_1MIN],
            bar_observer=observer,
        )

        # Assert
        assert cfg.data_clients[IB].instrument_provider.load_ids == frozenset(
            {"AAPL.NASDAQ", "MSFT.NASDAQ"}
        )

    @pytest.mark.component
    def test_the_observer_subscribes_to_the_instruments_the_node_loads(self):
        """A mismatch here is a session that connects and then sees nothing."""
        # Arrange
        bar_types = [AAPL_1MIN, MSFT_1MIN]
        observer = build_bar_observer_config(_settings(), bar_types)

        # Act
        cfg = build_trading_node_config(
            _settings(), trader_id=TRADER_ID, bar_types=bar_types, bar_observer=observer
        )

        # Assert
        subscribed = {
            str(BarType.from_str(entry).instrument_id)
            for entry in cfg.actors[0].config["bar_types"]
        }
        assert subscribed == set(cfg.data_clients[IB].instrument_provider.load_ids)


def _resolve_dotted(path: str):
    """Resolve a Nautilus ``module:Attribute`` path the way the kernel does."""
    module_name, _, attribute = path.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def _data_client_call_keywords(tree: ast.Module) -> set[str]:
    """Keyword names passed to the single InteractiveBrokersDataClientConfig call."""
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "InteractiveBrokersDataClientConfig"
    ]
    assert len(calls) == 1, f"expected exactly one data-client construction, found {len(calls)}"
    return {keyword.arg for keyword in calls[0].keywords if keyword.arg is not None}


class TestRedisCacheWiring:
    """Story 2.4, AC #3/#6 — the cache reaches the node, and never hangs it.

    ``cache`` is keyword-only and defaults to ``None`` deliberately. A ``None``
    cache means an in-memory Nautilus cache, which is what ``ntrader live check``
    wants: a diagnostic answering "can I reach the broker?" must not start
    requiring Redis. It also keeps ``NodeFactory``
    (``live_check_driver.py:85``, ``Callable[..., TradingNode]``) satisfied and
    every pre-existing call site in this file working unmodified.
    """

    @pytest.mark.component
    def test_omitting_the_cache_leaves_the_node_in_memory(self):
        """Every existing caller keeps its current behaviour."""
        # Act
        config = build_trading_node_config(_settings(), trader_id=TRADER_ID)

        # Assert
        assert config.cache is None

    @pytest.mark.component
    def test_a_supplied_cache_reaches_the_node_config(self):
        """AC #3 — the config Nautilus branches on in kernel.py:300."""
        # Arrange
        from src.config import RedisSettings
        from src.core.live_cache import build_cache_config

        cache = build_cache_config(RedisSettings(_env_file=None))

        # Act
        config = build_trading_node_config(_settings(), trader_id=TRADER_ID, cache=cache)

        # Assert
        assert config.cache is cache
        assert config.cache.database.type == "redis"

    @pytest.mark.component
    def test_the_namespace_is_the_trader_id_the_config_carries(self):
        """AC #3's "namespaced by trader_id".

        Nautilus takes the namespace from ``TradingNodeConfig.trader_id`` and
        hands it to ``CacheDatabaseAdapter`` itself (``kernel.py:303-311``) —
        the ``CacheConfig`` carries no trader id of its own. So the property
        this story needs is that the node config's ``trader_id`` is the derived
        one, which is what a session-scoped namespace reduces to.
        """
        # Arrange
        from uuid import UUID

        from src.config import RedisSettings
        from src.core.live_cache import build_cache_config
        from src.core.live_trader_id import derive_trader_id

        session_id = UUID("0e8f1c2a-3b4d-4e6f-8081-920304050607")
        derived = derive_trader_id(session_id)

        # Act
        config = build_trading_node_config(
            _settings(),
            trader_id=derived,
            cache=build_cache_config(RedisSettings(_env_file=None)),
        )

        # Assert
        assert config.trader_id.value == "PAPER-0e8f1c2a"

    @pytest.mark.component
    def test_building_the_config_alone_never_touches_redis(self, monkeypatch):
        """Config assembly is pure; only node construction needs a live Redis.

        This is what keeps this whole file in the parallel component tier with
        no Redis service anywhere near it.
        """
        # Arrange
        from src.config import RedisSettings
        from src.core import live_cache

        def _boom(*args, **kwargs):
            raise AssertionError("config assembly performed a Redis reachability check")

        monkeypatch.setattr(live_cache, "check_redis_reachable", _boom)

        # Act / Assert — must not raise
        build_trading_node_config(
            _settings(),
            trader_id=TRADER_ID,
            cache=live_cache.build_cache_config(RedisSettings(_env_file=None)),
        )

    @pytest.mark.component
    def test_an_unreachable_redis_is_refused_before_a_node_is_constructed(self, monkeypatch):
        """AC #6 — ordering is the contract.

        ``TradingNode(config=...)`` is the call that hangs forever against an
        unreachable Redis (``CacheDatabaseAdapter.__init__`` blocks and
        ``DatabaseConfig(timeout=...)`` does not bound it). So the preflight
        must raise strictly before that constructor is reached, and asserting
        the exception type alone would not prove it.
        """
        # Arrange
        from src.config import RedisSettings
        from src.core.live_cache import RedisUnreachableError, build_cache_config

        def _refuse(*args, **kwargs):
            raise RedisUnreachableError("redis is down")

        def _boom(*args, **kwargs):
            raise AssertionError("TradingNode constructed despite an unreachable Redis")

        monkeypatch.setattr(live_node_builder, "check_redis_reachable", _refuse)
        monkeypatch.setattr(live_node_builder, "TradingNode", _boom)

        # Act / Assert
        with pytest.raises(RedisUnreachableError):
            live_node_builder.build_trading_node(
                _settings(),
                trader_id=TRADER_ID,
                cache=build_cache_config(RedisSettings(_env_file=None)),
            )

    @pytest.mark.component
    def test_no_preflight_runs_when_no_cache_is_configured(self, monkeypatch):
        """A node with an in-memory cache must never require Redis."""

        # Arrange
        def _boom(*args, **kwargs):
            raise AssertionError("Redis preflight ran for an in-memory cache")

        monkeypatch.setattr(live_node_builder, "check_redis_reachable", _boom)
        monkeypatch.setattr(live_node_builder, "TradingNode", lambda **kwargs: _StubNode())

        # Act / Assert — must not raise
        live_node_builder.build_trading_node(_settings(), trader_id=TRADER_ID)

    @pytest.mark.component
    def test_the_gate_still_runs_before_the_redis_preflight(self, monkeypatch):
        """A refused connection must not first announce that Redis is down.

        The gate is the safety-critical control (FR9). A gate refusal has to
        surface as a gate refusal, with its own exit code, regardless of what
        else is misconfigured.
        """
        # Arrange
        from src.config import RedisSettings
        from src.core.live_cache import build_cache_config

        def _boom(*args, **kwargs):
            raise AssertionError("Redis preflight ran before the safety gate")

        monkeypatch.setattr(live_node_builder, "check_redis_reachable", _boom)

        # Act / Assert
        with pytest.raises(GateRefusedError):
            live_node_builder.build_trading_node(
                _settings(port=7496),
                trader_id=TRADER_ID,
                cache=build_cache_config(RedisSettings(_env_file=None)),
            )


class _StubNode:
    """Stands in for a TradingNode so no kernel is constructed in this tier."""

    class _Kernel:
        def get_log_guard(self):
            return None

    def __init__(self) -> None:
        self.kernel = _StubNode._Kernel()

    def add_data_client_factory(self, *args, **kwargs) -> None:
        return None

    def add_exec_client_factory(self, *args, **kwargs) -> None:
        return None
