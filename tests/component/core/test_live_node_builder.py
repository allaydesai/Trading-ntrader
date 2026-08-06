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
from pathlib import Path

import pytest
from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
    InteractiveBrokersExecClientConfig,
)
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.config import TradingNodeConfig

from src.config import IBKRSettings
from src.core import live_node_builder
from src.core.live_gate import GateRefusalReason
from src.core.live_node_builder import (
    GateRefusedError,
    LiveNodeConfigError,
    build_trading_node_config,
)

TRADER_ID = "PAPER-a1b2c3d4"


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
) -> IBKRSettings:
    """Build settings with every field this module reads, passed explicitly.

    Init kwargs outrank the environment in pydantic-settings, so the
    developer's shell cannot become test input (mirrors
    tests/unit/core/test_live_gate.py:26-46).
    """
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
