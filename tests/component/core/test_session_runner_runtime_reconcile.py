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
from structlog.testing import capture_logs

from src.core import live_runtime_reconcile
from src.core.exit_outcome import EXIT_CODES
from src.core.live_check import classify_failure
from src.core.live_connection_monitor import ConnectionMonitor, ConnectionState, ConnectionStatus
from src.core.live_order_path import install_order_path
from src.core.live_runtime_reconcile import SCOPE_RECONNECT, SCOPE_RUNTIME, RuntimeReconciler
from src.core.live_startup_reconcile import (
    DISCREPANCY_EVENT,
    OK_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
)
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from tests.component.core.test_live_order_path import CLOSES, _new_strategy, make_bar
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
                strategy.handle_bar(make_bar(index, close))

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
            strategy.handle_bar(make_bar(index, close))

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
