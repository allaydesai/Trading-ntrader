"""The on-demand reconcile driver, against node and broker doubles (Story 4.6, AC #1/#2/#4/#6).

``src/core/live_reconcile.py`` runs ``ntrader live reconcile``'s whole sequence
(D-F): Redis preflight → ``gate:static`` → a node on ``ibkr_live_client_id + 1``
→ connect → ``gate:account`` → the broker read → the session-view read →
compare → teardown. Every broker-facing step is a seam, so every branch is
reachable here without a broker (NFR32); the real ``build_trading_node_config``
is exercised once, to pin the ``+ 1`` client id where it actually lands (AC #2).

Nothing here constructs a ``TradingNode`` or a ``Logger``.
"""

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.common.component import is_logging_initialized

from src.config import IBKRSettings, RedisSettings
from src.core.live_broker_state import BrokerStateFailure, BrokerStateUnavailableError
from src.core.live_cache import RedisUnreachableError
from src.core.live_check import EXIT_CODES, BrokerUnreachableError, classify_failure
from src.core.live_gate import (
    GateDecision,
    GateMode,
    GateRefusal,
    GateRefusalReason,
    build_refusal,
)
from src.core.live_node_builder import GateRefusedError, build_trading_node_config
from src.core.live_reconcile import (
    CONNECT_TIMEOUT_SECONDS,
    RECONCILE_BUDGET_SECONDS,
    RECONCILE_TRADER_ID,
    TEARDOWN_RESERVE_SECONDS,
    ReconcileBudget,
    ReconcileTarget,
    exit_code_for,
    reconcile_settings,
    run_reconcile,
)
from src.core.live_session_view import SessionViewFailure, SessionViewUnavailableError
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from src.models.reconciliation import SessionView, ViewPosition
from src.services.reconciliation_service import compare
from tests.component.doubles import TestIBAccountsClient, TestLiveNode

pytestmark = pytest.mark.component

PAPER_ACCOUNT = "DU4076626"
TARGET = ReconcileTarget(
    name="swing-1", session_id=UUID("0e8f1c2a-1111-2222-3333-444455556666"), status="running"
)
AT = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)
FAST = ReconcileBudget(total=3.0, connect=0.5, teardown_reserve=0.5, broker_read=1.0)


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()})"
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """``build_clients`` writes ``IB_MAX_CONNECTION_ATTEMPTS`` into the process
    environment; keep it (and the fields the builder reads) out of other tests."""
    for name in ("IBKR_RATE_LIMIT", "IBKR_MARKET_DATA_TYPE", "IBKR_USE_RTH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IB_MAX_CONNECTION_ATTEMPTS", "1")


def _settings(*, mode: str = "paper", live_client_id: int = 10) -> IBKRSettings:
    return IBKRSettings(
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=7497,
        ibkr_client_id=1,
        ibkr_live_client_id=live_client_id,
        ibkr_trading_mode=mode,
        tws_account=PAPER_ACCOUNT,
        ntrader_real_money_account="",
    )


def _redis() -> RedisSettings:
    return RedisSettings(_env_file=None)


def _broker(*positions: tuple[str, str], cash: str = "1000.00") -> BrokerState:
    return BrokerState(
        account="***626",
        positions=tuple(
            BrokerPosition(
                instrument_id=i, quantity=Decimal(q), average_price=None, con_id=n + 1, symbol="X"
            )
            for n, (i, q) in enumerate(positions)
        ),
        cash=(CashBalance("USD", Decimal(cash)),),
        retrieved_at=AT,
    )


def _view(*positions: tuple[str, str], cash: str | None = "1000.00") -> SessionView:
    return SessionView(
        trader_id="PAPER-0e8f1c2a",
        positions=tuple(ViewPosition(i, Decimal(q)) for i, q in positions),
        cash=() if cash is None else (CashBalance("USD", Decimal(cash)),),
    )


class RecordingLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def __getattr__(self, level: str):
        def _record(event: str, **fields) -> None:
            self.records.append((level, event, fields))

        return _record


class CountingNode(TestLiveNode):
    """``TestLiveNode`` that counts its teardown calls, optionally slowly."""

    def __init__(self, *, dispose_seconds: float = 0.0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.stop_calls = 0
        self.dispose_calls = 0
        self._dispose_seconds = dispose_seconds

    def stop(self) -> None:
        self.stop_calls += 1
        super().stop()

    def dispose(self) -> None:
        self.dispose_calls += 1
        if self._dispose_seconds:
            time.sleep(self._dispose_seconds)
        super().dispose()


class Harness:
    """Every seam, recording the order in which the driver reaches them."""

    def __init__(
        self,
        *,
        node: CountingNode | None = None,
        broker: BrokerState | BaseException | None = None,
        view: SessionView | BaseException | None = None,
        verifier_raises: BaseException | None = None,
        verifier_refusal: GateRefusal | None = None,
        verifier_delay: float = 0.0,
        state_raises: BaseException | None = None,
        state_delay: float = 0.0,
    ) -> None:
        self.node = node or CountingNode()
        self.broker = broker if broker is not None else _broker()
        self.view = view if view is not None else _view()
        self.verifier_raises = verifier_raises
        self.verifier_refusal = verifier_refusal
        self.verifier_delay = verifier_delay
        self.state_raises = state_raises
        self.state_delay = state_delay
        self.steps: list[str] = []
        self.factory_calls: list[tuple[IBKRSettings, dict]] = []
        self.verifier_calls: list[tuple[IBKRSettings, dict]] = []
        self.reader_timeouts: list[float] = []
        self.view_calls: list[tuple] = []
        self.log = RecordingLog()

    def state_check(self, session_id, redis, *, log=None) -> None:
        self.steps.append("state")
        if self.state_delay:
            time.sleep(self.state_delay)
        if self.state_raises is not None:
            raise self.state_raises

    def node_factory(self, settings, **kwargs):
        self.steps.append("node:build")
        self.factory_calls.append((settings, kwargs))
        return self.node

    async def account_verifier(self, node, settings, **kwargs) -> GateDecision:
        self.steps.append("gate:account")
        self.verifier_calls.append((settings, kwargs))
        if self.verifier_delay:
            await asyncio.sleep(self.verifier_delay)
        if self.verifier_raises is not None:
            raise self.verifier_raises
        if self.verifier_refusal is not None:
            return GateDecision(permitted=False, mode=None, refusal=self.verifier_refusal)
        return GateDecision(permitted=True, mode=GateMode.PAPER)

    async def broker_reader(self, node, *, log=None, timeout_seconds: float):
        self.steps.append("broker")
        self.reader_timeouts.append(timeout_seconds)
        if isinstance(self.broker, BaseException):
            raise self.broker
        return self.broker

    def view_reader(self, session_id, account, redis, *, log=None):
        self.steps.append("view")
        self.view_calls.append((session_id, account, redis))
        if isinstance(self.view, BaseException):
            raise self.view
        return self.view

    def run(self, settings: IBKRSettings | None = None, *, budget: ReconcileBudget = FAST):
        return run_reconcile(
            settings or _settings(),
            _redis(),
            TARGET,
            compare=compare,
            node_factory=self.node_factory,
            account_verifier=self.account_verifier,
            broker_reader=self.broker_reader,
            view_reader=self.view_reader,
            state_check=self.state_check,
            budget=budget,
            log=self.log,
        )


class TestTheSequence:
    def test_the_steps_run_in_d_f_order_and_the_node_is_torn_down(self):
        harness = Harness()

        report = harness.run()

        assert harness.steps == ["state", "node:build", "gate:account", "broker", "view"]
        assert harness.node.built and harness.node.stopped and harness.node.disposed
        assert report.is_clean is True

    def test_the_view_is_read_for_the_target_session_and_configured_account(self):
        harness = Harness()

        harness.run()

        [(session_id, account, _)] = harness.view_calls
        assert (session_id, account) == (TARGET.session_id, PAPER_ACCOUNT)

    def test_a_clean_check_is_ok_and_exits_zero(self):
        report = Harness(
            broker=_broker(("NVDA.NASDAQ", "10")), view=_view(("NVDA.NASDAQ", "10"))
        ).run()

        assert report.is_clean is True
        assert exit_code_for(report) == 0

    def test_a_discrepancy_is_a_finding_and_exits_five(self):
        report = Harness(broker=_broker(("AAPL.NASDAQ", "4")), view=_view()).run()

        assert report.is_clean is False
        assert exit_code_for(report) == 5

    def test_the_callers_event_loop_is_restored(self):
        callers = asyncio.new_event_loop()
        asyncio.set_event_loop(callers)
        try:
            Harness().run()

            assert asyncio.get_event_loop_policy().get_event_loop() is callers
        finally:
            asyncio.set_event_loop(None)
            callers.close()


class TestTheNodeIsReadOnlyAndNeverTheSessions:
    """AC #2 / D-E: ``+ 1``, no strategy, no observer, no controller, no Redis cache."""

    def test_the_node_and_the_account_gate_see_client_id_plus_one(self, monkeypatch):
        settings = _settings(live_client_id=10)
        key = (settings.ibkr_host, settings.ibkr_port, 11)
        monkeypatch.setitem(IB_CLIENTS, key, TestIBAccountsClient((PAPER_ACCOUNT,)))
        harness = Harness()

        harness.run(settings)

        [(node_settings, _)] = harness.factory_calls
        [(gate_settings, gate_kwargs)] = harness.verifier_calls
        assert node_settings.ibkr_live_client_id == 11
        assert gate_settings.ibkr_live_client_id == 11
        assert gate_kwargs["reported_accounts"] == frozenset({PAPER_ACCOUNT})
        assert settings.ibkr_live_client_id == 10, "the caller's settings must be untouched"

    def test_the_node_carries_nothing_that_could_trade_or_write_redis(self):
        harness = Harness()

        harness.run()

        [(_, kwargs)] = harness.factory_calls
        assert kwargs["trader_id"] == RECONCILE_TRADER_ID == "PAPER-RECONCILE"
        assert kwargs["cli_flags"] is None
        for forbidden in ("bar_types", "bar_observer", "controller", "cache"):
            assert kwargs.get(forbidden) in (None, ()), forbidden

    def test_the_real_builder_puts_both_ib_clients_on_plus_one(self):
        config = build_trading_node_config(
            reconcile_settings(_settings(live_client_id=10)), trader_id=RECONCILE_TRADER_ID
        )

        [data] = config.data_clients.values()
        [execution] = config.exec_clients.values()
        assert data.ibg_client_id == 11
        assert execution.ibg_client_id == 11
        assert config.strategies == []
        assert config.actors == []
        assert config.controller is None
        assert config.cache is None or config.cache.database is None

    def test_reconcile_settings_copies_rather_than_mutates(self):
        settings = _settings(live_client_id=20)

        copy = reconcile_settings(settings)

        assert (settings.ibkr_live_client_id, copy.ibkr_live_client_id) == (20, 21)
        assert copy.tws_account == settings.tws_account


class TestFailuresAreRaisedAndTheNodeIsAlwaysTornDown:
    def test_a_gate_refusal_opens_no_socket_at_all(self):
        """Layer 1 runs first, so a refusal is exit 3 whatever else is down (FR11)."""
        harness = Harness(state_raises=RedisUnreachableError("Redis down"))

        with pytest.raises(GateRefusedError) as raised:
            harness.run(_settings(mode="live"))

        assert harness.steps == [], "no Redis PING, no node, before Layer 1 has ruled"
        assert EXIT_CODES[classify_failure(raised.value)] == 3

    def test_an_unreachable_redis_builds_no_node(self):
        harness = Harness(state_raises=RedisUnreachableError("Redis down"))

        with pytest.raises(RedisUnreachableError):
            harness.run()

        assert harness.steps == ["state"]

    def test_a_session_with_no_engine_state_costs_no_ib_connection(self):
        harness = Harness(
            state_raises=SessionViewUnavailableError(SessionViewFailure.NO_ENGINE_STATE, "empty")
        )

        with pytest.raises(SessionViewUnavailableError) as raised:
            harness.run()

        assert harness.steps == ["state"]
        assert EXIT_CODES[classify_failure(raised.value)] == 1

    def test_a_returned_account_refusal_is_a_refusal(self):
        """The verifier seam is typed ``-> GateDecision``: a *returned* refusal
        must stop the check exactly like a raised one (``live check``'s rule)."""
        refusal = GateRefusal(
            reason=GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER, message="reported account is live"
        )
        harness = Harness(verifier_refusal=refusal)

        with pytest.raises(GateRefusedError) as raised:
            harness.run()

        assert raised.value.refusal == refusal
        assert EXIT_CODES[classify_failure(raised.value)] == 3
        assert "broker" not in harness.steps
        assert harness.node.disposed

    def test_the_account_gate_refusal_is_raised_after_teardown(self):
        refusal = build_refusal(GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER, "not paper").refusal
        assert refusal is not None
        harness = Harness(verifier_raises=GateRefusedError(refusal))

        with pytest.raises(GateRefusedError) as raised:
            harness.run()

        assert EXIT_CODES[classify_failure(raised.value)] == 3
        assert harness.node.disposed

    def test_a_node_that_never_connects_is_broker_unreachable(self):
        harness = Harness(node=CountingNode(connects_after=-1))

        with pytest.raises(BrokerUnreachableError) as raised:
            harness.run()

        assert EXIT_CODES[classify_failure(raised.value)] == 4
        assert "broker" not in harness.steps
        assert harness.node.disposed

    @pytest.mark.parametrize(
        ("failure", "code"),
        [
            (BrokerStateUnavailableError(BrokerStateFailure.CONNECTION_LOST, "x"), 4),
            (SessionViewUnavailableError(SessionViewFailure.NO_ENGINE_STATE, "x"), 1),
        ],
    )
    def test_a_read_failure_is_raised_with_its_class(self, failure, code):
        harness = (
            Harness(broker=failure)
            if isinstance(failure, BrokerStateUnavailableError)
            else Harness(view=failure)
        )

        with pytest.raises(type(failure)) as raised:
            harness.run()

        assert EXIT_CODES[classify_failure(raised.value)] == code
        assert harness.node.disposed

    def test_an_interrupt_still_tears_the_node_down(self):
        harness = Harness(broker=KeyboardInterrupt())

        with pytest.raises(KeyboardInterrupt):
            harness.run()

        assert harness.node.stopped and harness.node.disposed

    @pytest.mark.parametrize(
        "path",
        ["clean", "discrepancy", "account_refused", "broker_failed", "view_failed", "interrupt"],
    )
    def test_teardown_runs_exactly_once_on_every_path(self, path):
        """AC #1: ``shutdown`` on every path — the discrepancy path included."""
        refusal = GateRefusal(reason=GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER, message="x")
        harness = {
            "clean": lambda: Harness(),
            "discrepancy": lambda: Harness(broker=_broker(("AAPL.NASDAQ", "4"))),
            "account_refused": lambda: Harness(verifier_refusal=refusal),
            "broker_failed": lambda: Harness(
                broker=BrokerStateUnavailableError(BrokerStateFailure.CONNECTION_LOST, "x")
            ),
            "view_failed": lambda: Harness(
                view=SessionViewUnavailableError(SessionViewFailure.UNREADABLE, "x")
            ),
            "interrupt": lambda: Harness(broker=KeyboardInterrupt()),
        }[path]()

        try:
            report = harness.run()
        except (Exception, KeyboardInterrupt):
            report = None

        assert (harness.node.stop_calls, harness.node.dispose_calls) == (1, 1)
        if path == "discrepancy":
            assert report is not None and report.is_clean is False


class TestTheCheckIsBounded:
    """AC #6 / NFR5."""

    def test_the_default_budget_fits_nfr5(self):
        budget = ReconcileBudget()

        assert RECONCILE_BUDGET_SECONDS == 30.0
        assert CONNECT_TIMEOUT_SECONDS + TEARDOWN_RESERVE_SECONDS < RECONCILE_BUDGET_SECONDS
        assert budget.read_timeout(elapsed=0.0) == 20.0
        assert budget.read_timeout(elapsed=10.0) == 15.0

    def test_the_broker_read_is_clamped_to_what_is_left(self):
        harness = Harness()

        harness.run(
            budget=ReconcileBudget(total=3.0, connect=0.5, teardown_reserve=0.5, broker_read=20.0)
        )

        [timeout] = harness.reader_timeouts
        assert 0 < timeout <= 2.5

    def test_a_spent_budget_never_calls_the_reader(self):
        """F15: the reader refuses a non-positive timeout with an unmarked
        ``ValueError`` — the driver must raise its own typed timeout first.
        The account gate is the step no connect deadline bounds: a slow one
        spends what the read was left."""
        # 1.2 s total − 0.5 s reserve leaves 0.7 s; a 0.8 s account gate spends it.
        harness = Harness(verifier_delay=0.8)
        budget = ReconcileBudget(total=1.2, connect=0.5, teardown_reserve=0.5, broker_read=1.0)

        with pytest.raises(BrokerStateUnavailableError) as raised:
            harness.run(budget=budget)

        assert raised.value.reason is BrokerStateFailure.TIMEOUT
        assert "broker" not in harness.steps
        assert EXIT_CODES[classify_failure(raised.value)] == 4

    def test_a_never_connecting_node_ends_within_its_connect_budget(self):
        harness = Harness(node=CountingNode(connects_after=-1))
        started = time.monotonic()

        with pytest.raises(BrokerUnreachableError):
            harness.run(budget=FAST)

        assert time.monotonic() - started < FAST.connect + 0.5

    def test_the_connect_deadline_never_outlives_the_budget(self):
        """Time already spent (a slow state check) shortens the connect window:
        the deadline is the earlier of ``connect`` and what the budget leaves."""
        # Without the clamp the node (connected after ~0.5 s) would connect
        # inside its own 0.9 s window and the check would fail later, at the
        # read, as a TIMEOUT; with it, the 0.2 s left is the connect deadline.
        harness = Harness(state_delay=0.8, node=CountingNode(connects_after=1))
        budget = ReconcileBudget(total=1.2, connect=0.9, teardown_reserve=0.2, broker_read=1.0)

        with pytest.raises(BrokerUnreachableError):
            harness.run(budget=budget)

        assert "broker" not in harness.steps

    def test_the_reader_is_always_handed_a_positive_timeout_inside_the_budget(self):
        """What the driver owns: the bound it passes. The reader's own deadline
        is Story 4.1's, proven in ``test_live_broker_state.py``'s
        ``TestTheReadIsBounded`` — not re-proven against a double here."""
        harness = Harness()

        harness.run(budget=FAST)

        [timeout] = harness.reader_timeouts
        assert 0 < timeout <= FAST.total - FAST.teardown_reserve
        assert timeout <= FAST.broker_read

    def test_the_report_carries_elapsed_ms(self):
        report = Harness().run()

        assert 0 <= report.elapsed_ms < FAST.total * 1000

    def test_an_overrun_is_recorded_not_silent(self):
        """Teardown is synchronous and cannot be bounded from here; a check that
        overran NFR5's budget says so (``reconcile.budget_exceeded``)."""
        harness = Harness(node=CountingNode(dispose_seconds=1.3))
        budget = ReconcileBudget(total=1.2, connect=0.5, teardown_reserve=0.5, broker_read=1.0)

        harness.run(budget=budget)

        [(level, _, fields)] = [
            r for r in harness.log.records if r[1] == "reconcile.budget_exceeded"
        ]
        assert level == "warning"
        assert fields["total_ms"] > fields["budget_ms"] == 1200

    def test_a_run_inside_its_budget_records_no_overrun(self):
        harness = Harness()

        harness.run(budget=FAST)

        assert not [r for r in harness.log.records if r[1] == "reconcile.budget_exceeded"]

    @pytest.mark.parametrize(
        "fields",
        [
            {"total": float("nan")},
            {"broker_read": float("nan")},
            {"connect": float("inf")},
            {"teardown_reserve": 0},
            {"connect": -1.0},
            {"broker_read": 31.0},
            {"total": 10.0, "connect": 6.0, "teardown_reserve": 4.0},
        ],
    )
    def test_an_invalid_budget_is_refused(self, fields):
        with pytest.raises(ValueError, match="budget"):
            ReconcileBudget(**fields)


class TestTheVerdictIsRecorded:
    """D-G: AR41's ``reconcile.ok`` / ``reconcile.discrepancy``, tagged on-demand."""

    def test_a_clean_check_emits_one_reconcile_ok(self):
        harness = Harness()

        harness.run()

        oks = [r for r in harness.log.records if r[1] == "reconcile.ok"]
        [(level, _, fields)] = oks
        assert level == "info"
        assert fields["trigger"] == "on_demand"
        assert fields["account"] == "***626"
        assert fields["session_id"] == str(TARGET.session_id)
        assert "elapsed_ms" in fields
        assert not [r for r in harness.log.records if r[1] == "reconcile.discrepancy"]

    def test_each_discrepant_line_emits_one_reconcile_discrepancy(self):
        harness = Harness(
            broker=_broker(("AAPL.NASDAQ", "4"), cash="990.00"), view=_view(cash="1000.00")
        )

        harness.run()

        records = [r for r in harness.log.records if r[1] == "reconcile.discrepancy"]
        assert [r[0] for r in records] == ["warning", "warning"]
        position, cash = (r[2] for r in records)
        assert position["kind"] == "position"
        assert (
            position["instrument_id"],
            position["local_quantity"],
            position["broker_quantity"],
        ) == (
            "AAPL.NASDAQ",
            "0",
            "4",
        )
        assert position["difference"] == "4"
        assert cash["kind"] == "cash"
        assert (cash["currency"], cash["local_cash"], cash["broker_cash"], cash["difference"]) == (
            "USD",
            "1000.00",
            "990.00",
            "-10.00",
        )
        assert all(r[2]["trigger"] == "on_demand" for r in records)
        assert not [r for r in harness.log.records if r[1] == "reconcile.ok"]

    def test_unknown_local_cash_is_recorded_as_unknown(self):
        harness = Harness(view=_view(cash=None))

        harness.run()

        [record] = [r for r in harness.log.records if r[1] == "reconcile.discrepancy"]
        assert record[2]["local_cash"] == "unknown"

    def test_no_raw_account_in_any_record(self):
        harness = Harness(broker=_broker(("AAPL.NASDAQ", "4")))

        harness.run()

        assert PAPER_ACCOUNT not in repr(harness.log.records)
