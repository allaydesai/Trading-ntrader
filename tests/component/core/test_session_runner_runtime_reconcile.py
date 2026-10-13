"""The runtime reconciler wired into the session (Story 4.3, AC #1, #2, #4).

Component tier, against ``TestLiveNode`` and the steady state's own doubles —
never a ``TradingNode``. The reconciler's policy is proven at unit tier
(``tests/unit/core/test_live_runtime_reconcile.py``) and against a real
``LiveExecutionEngine`` (``test_live_runtime_reconcile_engine.py``); this file
proves the *wiring*: that the tick drives it after observing the connection,
that a reconnect is re-established before permission returns, and that a
runtime strategy contradiction stops the session through ``runner.run()``'s
ordinary teardown, positions untouched (PO ruling 2A).
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
import structlog
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from structlog.testing import capture_logs

from src.core import live_runtime_reconcile
from src.core.exit_outcome import EXIT_CODES
from src.core.live_bar_resubscribe import FAILURES_ATTRIBUTE, BarFeedLostError
from src.core.live_broker_state import (
    BrokerStateAdapterError,
    BrokerStateFailure,
    BrokerStateUnavailableError,
)
from src.core.live_check import classify_failure, failure_message
from src.core.live_connection_monitor import ConnectionMonitor, ConnectionState, ConnectionStatus
from src.core.live_order_path import install_order_path
from src.core.live_runtime_reconcile import (
    ACTION_SESSION_STOPPED,
    ACTION_TRADING_WITHHELD,
    CYCLE_FAILED_EVENT,
    SCOPE_RECONNECT,
    SCOPE_RUNTIME,
    RuntimeReconciler,
)
from src.core.live_startup_reconcile import (
    DISCREPANCY_EVENT,
    OK_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
)
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from tests.component.core.test_live_order_path import CLOSES, _new_strategy, make_live_bar
from tests.component.core.test_session_runner_phases import (
    PAPER_ACCOUNT,
    SpyRecord,
    _open_position,
    _pairs,
    _runner,
    _settings,
)
from tests.component.core.test_session_steady_state import (
    FakeClock,
    _run_ticks,
    _steady_state,
)
from tests.component.core.test_session_steady_state import (
    SpyRecord as SteadySpyRecord,
)
from tests.component.doubles import TestIBAccountsClient, TestLiveNode, flat_broker_state_reader

pytestmark = pytest.mark.component

UP = ConnectionStatus(connected=True, detail="ib socket connected, client ready")
DOWN = ConnectionStatus(connected=False, detail="ib socket not connected")
NVDA = "NVDA.NASDAQ"
NVDA_EQUITY = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
STRATEGY = "SMACrossover-000"


@pytest.fixture
def registered_accounts(monkeypatch):
    """``test_session_runner_phases.py``'s fixture: an account-naming client in
    the adapter's process-global cache, so ``gate:account`` runs for real."""

    def _register(settings, accounts=(PAPER_ACCOUNT,)):
        key = (settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id)
        monkeypatch.setitem(IB_CLIENTS, key, TestIBAccountsClient(accounts))

    return _register


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, "this component test constructed a TradingNode"


def _state(*held: tuple[str, str]) -> BrokerState:
    return BrokerState(
        account="***626",
        positions=tuple(
            BrokerPosition(
                instrument_id=instrument_id,
                quantity=Decimal(quantity),
                average_price=Decimal("101.25"),
                con_id=4815747,
                symbol=instrument_id.split(".")[0],
            )
            for instrument_id, quantity in held
        ),
        cash=(CashBalance(currency="USD", total_cash=Decimal("100000")),),
        retrieved_at=FakeClock().now,
    )


def _flat_node() -> SimpleNamespace:
    cache = SimpleNamespace(
        positions_open=lambda: [],
        orders_open=lambda: [],
        orders_inflight=lambda: [],
    )
    return SimpleNamespace(cache=cache, kernel=SimpleNamespace(exec_engine=object()))


class TestTheTickDrivesTheReconciler:
    """AC #1 — the cycle runs throughout the session, on the heartbeat."""

    async def test_a_connected_session_reconciles_every_n_ticks(self):
        reads = []

        async def _reader(node, *, log):
            reads.append(node)
            return _state()

        monitor = ConnectionMonitor(session_id="a-session")
        monitor.confirm_state_reestablished(UP)
        reconciler = RuntimeReconciler(
            node=_flat_node(),
            monitor=monitor,
            log=structlog.get_logger("test"),
            connection_reader=lambda s: UP,
            settings=object(),
            read_state=_reader,
        )
        state = _steady_state(
            FakeClock(),
            SteadySpyRecord(),
            structlog.get_logger("test"),
            monitor=monitor,
            reconciler=reconciler,
        )

        await _run_ticks(state, 3 * live_runtime_reconcile.RUNTIME_RECONCILE_EVERY_TICKS)

        assert len(reads) == 3

    async def test_without_a_reconciler_the_tick_is_unchanged(self):
        """Every existing construction site passes none — the parameter is
        defaulted, and a steady state without one reads no broker."""
        state = _steady_state(FakeClock(), SteadySpyRecord(), structlog.get_logger("test"))

        await _run_ticks(state, 4)

        assert state.ticks == 4


class TestAReconnectIsReEstablishedBeforePermissionReturns:
    """AC #4 — through the real tick: observe, then the reconciler."""

    async def test_lost_then_back_is_withheld_until_a_clean_cycle_grants(self):
        monitor = ConnectionMonitor(session_id="a-session")
        monitor.confirm_state_reestablished(UP)
        readings = iter([DOWN, UP, UP])
        withheld_when_the_reconciler_ran: list[tuple[str, bool]] = []
        reconciler = RuntimeReconciler(
            node=_flat_node(),
            monitor=monitor,
            log=structlog.get_logger("test"),
            connection_reader=lambda s: UP,
            settings=object(),
            read_state=flat_broker_state_reader,
        )

        class _Spy:
            async def on_tick(self):
                withheld_when_the_reconciler_ran.append(
                    (monitor.state.value, monitor.submission_withheld)
                )
                await reconciler.on_tick()

        state = _steady_state(
            FakeClock(),
            SteadySpyRecord(),
            structlog.get_logger("test"),
            monitor=monitor,
            connection_reader=lambda s: next(readings),
            reconciler=_Spy(),
        )

        with capture_logs() as logs:
            await _run_ticks(state, 2)

        assert withheld_when_the_reconciler_ran == [("lost", True), ("recovering", True)]
        assert monitor.state is ConnectionState.CONNECTED
        assert monitor.submission_withheld is False
        order = [
            e["event"]
            for e in logs
            if e["event"] in ("connection.lost", OK_EVENT, "connection.restored")
        ]
        assert order == ["connection.lost", OK_EVENT, "connection.restored"]
        (ok,) = [e for e in logs if e["event"] == OK_EVENT]
        assert ok["scope"] == SCOPE_RECONNECT


class TestARuntimeStrategyContradictionStopsTheSession:
    """D-D, PO ruling 2A — refuse and stop, through ``runner.run()``, against a
    broker double: nothing is written to the cache, the stop is the ordinary
    teardown (which closes, flattens and resizes nothing), exit 1 with the
    instrument and quantities in the message."""

    def test_the_session_stops_with_its_positions_untouched(self, registered_accounts, monkeypatch):
        # The debounce's 60 s is proven at unit tier with an injected clock;
        # here the runner's real clock is used, so the window is closed.
        monkeypatch.setattr(live_runtime_reconcile, "DEBOUNCE_SECONDS", 0.0)
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=30.0)
        position = _open_position(NVDA, STRATEGY, "22")
        node.cache.open_positions = [position]
        reads: list[BrokerState] = []

        async def _broker(node_arg, *, log):
            # Startup agrees with the cache; then the operator closes the
            # position in TWS, and every later read says flat.
            state = _state((NVDA, "22")) if not reads else _state()
            reads.append(state)
            return state

        async def _yield(seconds: float) -> None:
            await asyncio.sleep(0)

        record = SpyRecord()
        runner = _runner(
            node,
            settings=settings,
            record=record,
            broker_state_reader=_broker,
            connection_reader=lambda s: UP,
            sleeper=_yield,
        )

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                runner.run()

        error = caught.value
        assert error.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert error.scope == SCOPE_RUNTIME
        assert NVDA in str(error) and "+22" in str(error) and "broker +0" in str(error)
        assert EXIT_CODES[classify_failure(error)] == 1
        # It traded first — this is a runtime stop, not a startup refusal.
        assert ("trading", "ok") in _pairs(logs)
        assert not [pair for pair in _pairs(logs) if pair[1] == "failed"]
        # Nothing written: no correction reached the framework; the cache
        # still holds exactly the strategy's own position.
        assert node.kernel.exec_engine.reconcile_reports == []
        assert node.cache.open_positions == [position]
        refused = [e for e in logs if e["event"] == DISCREPANCY_EVENT]
        assert [(e["scope"], e["resolution"], e["kind"]) for e in refused] == [
            (SCOPE_RUNTIME, "refused", "strategy_position")
        ]
        # The ordinary teardown ran: node stopped and disposed, row released.
        assert node.disposed is True
        assert record.calls[-1] == "mark_stopped"


AC_5_8B = (
    "a BrokerStateAdapterError stops the session with exit 1 on its first occurrence, as "
    "startup treats adapter drift; the hourly throttle does not apply"
)
SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"


def _drift() -> BrokerStateAdapterError:
    return BrokerStateAdapterError(
        "distinct contracts resolve to one instrument, so positions cannot be told apart"
    )


class _WithheldWriteFailsOnTheTick(SpyRecord):
    """The tick's write of a withheld period fails (a DB hiccup); every later
    write — the teardown's — succeeds, and is recorded with its fields."""

    def __init__(self) -> None:
        super().__init__()
        self.withheld_attempts = 0
        self.withheld_written: list[dict] = []

    def record_trading_withheld(self, **fields) -> None:
        self.withheld_attempts += 1
        if self.withheld_attempts == 1:
            raise RuntimeError("postgres is down")
        self.calls.append("record_trading_withheld")
        self.withheld_written.append(fields)


class TestAdapterDriftStopsTheSession:
    """Story 5.8, AC #2, through ``runner.run()`` (code review P3): drift
    found by the running session's cycle stops it on its first occurrence,
    exit 1, through the ordinary teardown — positions untouched — and (code
    review DN1, PO ruling 1a) the teardown writes the withheld period the tick
    could not, before ``-> stopped`` closes what it finds open."""

    @staticmethod
    def _run(reads, record):
        settings = _settings()
        node = TestLiveNode(run_seconds=30.0)
        position = _open_position(NVDA, STRATEGY, "22")
        node.cache.open_positions = [position]
        script = iter(reads)

        async def _broker(node_arg, *, log):
            outcome = next(script)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        async def _yield(seconds: float) -> None:
            await asyncio.sleep(0)

        runner = _runner(
            node,
            settings=settings,
            record=record,
            broker_state_reader=_broker,
            connection_reader=lambda s: UP,
            sleeper=_yield,
        )
        return node, position, runner, settings

    @pytest.mark.acceptance_criterion("5.8b", AC_5_8B, SOURCE)
    def test_drift_on_the_first_runtime_cycle_stops_with_exit_1(self, registered_accounts):
        record = SpyRecord()
        node, position, runner, settings = self._run([_state((NVDA, "22")), _drift()], record)
        registered_accounts(settings)

        with capture_logs() as logs:
            with pytest.raises(BrokerStateAdapterError) as caught:
                runner.run()

        assert EXIT_CODES[classify_failure(caught.value)] == 1
        assert ("trading", "ok") in _pairs(logs), "a runtime stop, not a startup refusal"
        (failed,) = [e for e in logs if e["event"] == CYCLE_FAILED_EVENT]
        assert (failed["log_level"], failed["action"], failed["scope"]) == (
            "error",
            ACTION_SESSION_STOPPED,
            SCOPE_RUNTIME,
        )
        # The ordinary teardown, positions untouched: nothing reached the
        # framework, the cache still holds the strategy's own position.
        assert node.kernel.exec_engine.reconcile_reports == []
        assert node.cache.open_positions == [position]
        assert node.disposed is True
        assert record.calls[-1] == "mark_stopped"

    def test_the_teardown_writes_the_withheld_period_before_the_stop(self, registered_accounts):
        record = _WithheldWriteFailsOnTheTick()
        unanswered = BrokerStateUnavailableError(
            BrokerStateFailure.POSITIONS_UNANSWERED, "the positions request was not answered"
        )
        reads = [_state((NVDA, "22")), unanswered, unanswered, unanswered, _drift()]
        node, _, runner, settings = self._run(reads, record)
        registered_accounts(settings)

        with capture_logs() as logs:
            with pytest.raises(BrokerStateAdapterError):
                runner.run()

        withheld = [e for e in logs if e.get("action") == ACTION_TRADING_WITHHELD]
        assert len(withheld) == 1, "three failed cycles withdrew trading"
        assert record.withheld_attempts == 2  # the tick's (failed), then the teardown's
        (period,) = record.withheld_written
        assert (period["failed_cycles"], period["reason"]) == (3, "positions_unanswered")
        calls = record.calls
        assert calls.index("record_trading_withheld") < calls.index("mark_stopped")
        assert runner.shutdown_problems == []


class TestARuntimeForwardSplitDoesNotStopTheSession:
    """Story 4.7, D-A (PO ruling A) — the sibling of the stop above, through
    ``runner.run()``: a 2:1 split mid-session grows the broker's position in
    the strategy's direction. It is corrected broker-ward, named, and the
    session runs on to its ordinary end; the strategy's own position object is
    never touched."""

    def test_the_session_runs_on_and_the_split_is_named(self, registered_accounts, monkeypatch):
        monkeypatch.setattr(live_runtime_reconcile, "DEBOUNCE_SECONDS", 0.0)
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.5)
        position = _open_position(NVDA, STRATEGY, "22")
        node.cache.open_positions = [position]
        node.cache.instruments_by_id = {InstrumentId.from_str(NVDA): NVDA_EQUITY}
        engine = node.kernel.exec_engine
        recorded = engine.reconcile_execution_report

        def _apply(report):
            # The real engine's netting correction (measured 1.2): the
            # difference lands in an INTERNAL-DIFF position, never the strategy's.
            recorded(report)
            node.cache.open_positions.append(_open_position(NVDA, "INTERNAL-DIFF", "22"))
            return True

        engine.reconcile_execution_report = _apply
        reads: list[BrokerState] = []

        async def _broker(node_arg, *, log):
            state = _state((NVDA, "22")) if not reads else _state((NVDA, "44"))
            reads.append(state)
            return state

        async def _yield(seconds: float) -> None:
            await asyncio.sleep(0)

        record = SpyRecord()
        runner = _runner(
            node,
            settings=settings,
            record=record,
            broker_state_reader=_broker,
            connection_reader=lambda s: UP,
            sleeper=_yield,
        )

        with capture_logs() as logs:
            runner.run()

        assert len(reads) > 2, "no runtime cycle ran"
        # One correction, broker-ward, for the whole position (the strategy's
        # lot is untouched by construction here — the real engine proves that in
        # test_live_corporate_actions_engine.py).
        (report,) = engine.reconcile_reports
        assert report.signed_decimal_qty == Decimal("44")
        records = [e for e in logs if e["event"] == DISCREPANCY_EVENT]
        assert [(e["scope"], e["resolution"], e["kind"]) for e in records] == [
            (SCOPE_RUNTIME, "broker", "position")
        ]
        assert (records[0]["local_quantity"], records[0]["broker_quantity"]) == ("22", "44")
        # AC #1: the session kept running past the correction — a later runtime
        # cycle came back clean — and nothing refused or failed on the way.
        events = [e["event"] for e in logs]
        after = events[events.index(DISCREPANCY_EVENT) + 1 :]
        assert [
            e
            for e in logs[events.index(DISCREPANCY_EVENT) + 1 :]
            if e["event"] == OK_EVENT and e["scope"] == SCOPE_RUNTIME
        ], f"no clean runtime cycle after the correction: {after}"
        assert not [e for e in records if e["resolution"] == "refused"]
        assert not [pair for pair in _pairs(logs) if pair[1] == "failed"]
        # The stop is the ordinary end of the run (`run_seconds`), not a refusal.
        assert record.calls[-1] == "mark_stopped"


class TestTheGateWithholdsWhileStateIsNotReEstablished:
    """AC #4 end to end: the real monitor, the real order wrapper, a real
    strategy's crossover — ``RECOVERING`` suppresses; the grant releases."""

    def _recovering_monitor(self) -> ConnectionMonitor:
        monitor = ConnectionMonitor(session_id="a-session")
        monitor.confirm_state_reestablished(UP)
        monitor.observe(DOWN)
        monitor.observe(UP)
        assert monitor.state is ConnectionState.RECOVERING
        return monitor

    def test_a_crossover_while_recovering_submits_nothing(self):
        strategy = _new_strategy()
        install_order_path(strategy, self._recovering_monitor(), structlog.get_logger("test"))
        strategy.start()

        with capture_logs() as logs:
            for index, close in enumerate(CLOSES):
                strategy.handle_bar(make_live_bar(index, close))

        assert list(strategy.cache.orders()) == []
        assert [e for e in logs if e["event"] == "order.suppressed"]
        strategy.stop()

    def test_the_same_crossover_submits_once_state_is_re_established(self):
        """The sibling: the only difference is the grant."""
        strategy = _new_strategy()
        monitor = self._recovering_monitor()
        monitor.confirm_state_reestablished(UP)
        install_order_path(strategy, monitor, structlog.get_logger("test"))
        strategy.start()

        for index, close in enumerate(CLOSES):
            strategy.handle_bar(make_live_bar(index, close))

        assert len(list(strategy.cache.orders())) == 1
        strategy.stop()


class TestARefusedStartupGrantIsNotAPhaseFailure:
    def test_a_disconnected_reading_at_reconcile_leaves_submission_withheld(
        self, registered_accounts, monkeypatch
    ):
        from src.core.live_session_runner import LiveSessionRunner

        withheld_at_trading = []
        original = LiveSessionRunner._phase_trading

        def _spy(self_):
            withheld_at_trading.append(self_._monitor.submission_withheld)
            return original(self_)

        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _spy)
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)

        with capture_logs() as logs:
            _runner(node, settings=settings, connection_reader=lambda s: DOWN).run()

        assert ("reconcile", "ok") in _pairs(logs)
        assert withheld_at_trading == [True]


AC_5_9B = (
    "a failed bar resubscribe is recorded once as bars.resubscribe_failed with the bar type and "
    "error type, and the session does not report a healthy, trading state while deaf"
)
SOURCE_5_9 = "_bmad-output/planning-artifacts/epics.md"


class TestALostBarFeedStopsTheSession:
    """Story 5.9, AC #2 (PO ruling R2), through ``runner.run()``: a bar
    subscription the shim could not restore after a reconnect stops the session
    on the next tick, exit 1, through the ordinary teardown — positions
    untouched, nothing sent — exactly as adapter drift does (Story 5.8). The
    watch runs before the reconciler, so no reconnect cycle re-grants trading
    first."""

    @staticmethod
    def _run(record, failures, run_seconds=30.0):
        settings = _settings()
        node = TestLiveNode(run_seconds=run_seconds)
        position = _open_position(NVDA, STRATEGY, "22")
        node.cache.open_positions = [position]
        exec_client = node.kernel.exec_engine._clients["INTERACTIVE_BROKERS"]
        exec_client._client = SimpleNamespace(**{FAILURES_ATTRIBUTE: failures})
        reads = []

        async def _broker(node_arg, *, log):
            reads.append(node_arg)
            return _state((NVDA, "22"))

        async def _yield(seconds: float) -> None:
            await asyncio.sleep(0)

        runner = _runner(
            node,
            settings=settings,
            record=record,
            broker_state_reader=_broker,
            connection_reader=lambda s: UP,
            sleeper=_yield,
        )
        return node, position, runner, settings, reads

    @pytest.mark.acceptance_criterion("5.9b", AC_5_9B, SOURCE_5_9)
    def test_a_failed_resubscribe_stops_the_session_with_exit_1(self, registered_accounts):
        record = SpyRecord()
        failures = [("NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL", "TypeError")]
        node, position, runner, settings, reads = self._run(record, failures)
        registered_accounts(settings)

        with capture_logs() as logs:
            with pytest.raises(BarFeedLostError) as caught:
                runner.run()

        assert EXIT_CODES[classify_failure(caught.value)] == 1
        message = failure_message(caught.value)
        assert "NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL" in message and "TypeError" in message
        assert PAPER_ACCOUNT not in message
        assert ("trading", "ok") in _pairs(logs), "a runtime stop, not a startup refusal"
        assert len(reads) == 1, "only the startup read: the watch stopped the first tick"
        assert node.kernel.exec_engine.reconcile_reports == []
        assert node.cache.open_positions == [position]
        assert node.disposed is True
        assert record.calls[-1] == "mark_stopped"

    def test_a_restored_feed_lets_the_reconciler_run(self, registered_accounts):
        """The sibling: with no failure noted, the tick reaches the reconciler,
        so the stop above is the watch's doing and not the harness's."""
        record = SpyRecord()
        node, _, runner, settings, reads = self._run(record, [], run_seconds=0.3)
        registered_accounts(settings)

        runner.run()

        assert len(reads) >= 2, "the tick ran the runtime reconciler after startup's read"
