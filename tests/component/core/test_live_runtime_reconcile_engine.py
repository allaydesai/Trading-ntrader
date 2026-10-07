"""The running session's reconciliation against a real ``LiveExecutionEngine``
(Story 4.3, AC #1, #2).

Component tier on Story 4.2's harness: a real message bus, ``Cache``,
``Portfolio`` and ``LiveExecutionEngine`` with an IB-shaped NETTING client —
never a ``TradingNode``. The engine's own continuous loop reads the **engine
clock** (a ``TestClock`` here), so every test that needs it to fire advances
that clock explicitly (Task 1's harness note).

Three groups. *AC #1* pins that the session's pinned engine configuration runs
Nautilus's continuous reconciliation for the whole session, and that the
open-order check it leaves off is off for the measured reason (a canary).
*AC #2* drives :class:`RuntimeReconciler` against the real engine: every
correction broker-ward, through ``reconcile_execution_report`` only, and a
contradicted strategy position refused with nothing written.
"""

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
import structlog
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.live.config import LiveExecEngineConfig
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientOrderId, StrategyId, VenueOrderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from structlog.testing import capture_logs

from src.core import live_runtime_reconcile
from src.core.live_connection_monitor import ConnectionMonitor, ConnectionStatus
from src.core.live_node_builder import build_trading_node_config
from src.core.live_runtime_reconcile import (
    DEBOUNCE_SECONDS,
    RUNTIME_RECONCILE_EVERY_TICKS,
    RuntimeReconciler,
)
from src.core.live_startup_reconcile import (
    DISCREPANCY_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
)
from src.core.live_stranded_orders import CLEARED_EVENT, BrokerOpenOrders
from tests.component.core.test_live_node_builder import _settings as _ibkr_settings
from tests.component.core.test_live_startup_reconcile_engine import (
    AAPL,
    ACCOUNT,
    NVDA,
    STRATEGY,
    TRADER,
    _engine_method_calls,
    _Harness,
    _state,
)

pytestmark = pytest.mark.component

UP = ConnectionStatus(connected=True, detail="up")


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, "this component test constructed a TradingNode"


@pytest.fixture
def harness():
    made: list[_Harness] = []

    def _make(broker=None, **engine_config) -> _Harness:
        made.append(_Harness(broker, **engine_config))
        return made[-1]

    yield _make
    for each in made:
        if not each.loop.is_closed():
            each.close()


def _session_exec_config() -> dict:
    """The exec engine settings a real session is built with, as kwargs."""
    built = build_trading_node_config(_ibkr_settings(), trader_id="PAPER-0e8f1c2a").exec_engine
    assert isinstance(built, LiveExecEngineConfig)
    return {
        name: getattr(built, name)
        for name in (
            "reconciliation",
            "inflight_check_interval_ms",
            "inflight_check_threshold_ms",
            "inflight_check_retries",
            "open_check_interval_secs",
            "filter_unclaimed_external_orders",
        )
    }


def _advance(h: _Harness, seconds: float, *, steps: int = 10) -> None:
    """Advance the engine clock and let its loop run.

    The loop *decides* on the engine clock but *sleeps* ``min(intervals)`` of
    real time between passes, so each step waits a little longer than the
    50 ms test interval the loop-driven tests below use.
    """
    clock = h.engine._clock
    for _ in range(steps):
        clock.advance_time(clock.timestamp_ns() + int(seconds / steps * 1e9))
        h.loop.run_until_complete(asyncio.sleep(0.06))


def _working_order(h: _Harness, instrument, coid: str, *, accept: bool = True):
    order = TestExecStubs.limit_order(
        instrument=instrument,
        order_side=OrderSide.BUY,
        quantity=Quantity.from_int(10),
        price=Price.from_str("90.00"),
        trader_id=TRADER,
        strategy_id=STRATEGY,
        client_order_id=ClientOrderId(coid),
    )
    h.cache.add_order(order, None)
    order.apply(TestEventStubs.order_submitted(order, account_id=ACCOUNT))
    if accept:
        order.apply(
            TestEventStubs.order_accepted(
                order, account_id=ACCOUNT, venue_order_id=VenueOrderId("9001")
            )
        )
    h.cache.update_order(order)
    return order


class TestContinuousReconciliationRunsForTheWholeSession:
    """AC #1, as amended by PO ruling 1A: the native in-flight sweep."""

    def test_the_sessions_config_starts_the_continuous_task_and_stop_ends_it(self, harness):
        h = harness(**_session_exec_config())

        task = h.engine.get_reconciliation_task()
        assert task is not None and not task.done()
        _advance(h, 30.0)
        assert not task.done(), "the continuous task ended mid-session"

        h.close()
        assert h.engine.get_reconciliation_task() is None
        assert task.cancelled() or task.done()

    def test_the_in_flight_sweep_queries_the_venue_for_an_order_gone_quiet(self, harness):
        """The machinery the task runs: a SUBMITTED order older than the
        threshold is queried with the venue, never resubmitted (Story 3.4)."""
        # The session's threshold and retries; a 50 ms interval so the loop
        # wakes inside a test (its real value is pinned by the builder tests).
        config = _session_exec_config()
        config["inflight_check_interval_ms"] = 50
        h = harness(**config)
        queried = []
        original = h.client.generate_order_status_report

        async def _spy(command):
            queried.append(command.client_order_id)
            return await original(command)

        h.client.generate_order_status_report = _spy
        _working_order(h, AAPL, "O-QUIET-1", accept=False)

        _advance(h, 10.0)

        assert ClientOrderId("O-QUIET-1") in queried

    def test_the_open_order_check_is_off_in_the_sessions_config(self):
        assert _session_exec_config()["open_check_interval_secs"] is None


class TestTheOpenOrderCheckStaysOffCanary:
    """PO ruling 1A's measured reason, pinned (Task 1.1b). If an upgrade makes
    the check correct a locally-cancelled order — or stop republishing for it —
    this goes red by name, and turning the check on becomes a decision."""

    def test_a_locally_cancelled_order_the_venue_lists_open_is_republished_every_check(
        self, harness
    ):
        h = harness({}, open_check_interval_secs=0.05, inflight_check_interval_ms=0)
        order = _working_order(h, AAPL, "O-CLOSED-2")
        order.apply(TestEventStubs.order_canceled(order))
        h.cache.update_order(order)
        h.client.open_order_reports = [_open_report(AAPL, "O-CLOSED-2")]
        accepted = []
        h.engine._msgbus.subscribe(
            f"events.order.{STRATEGY}",
            lambda event: accepted.append(type(event).__name__),
        )

        _advance(h, 5.0)

        assert accepted.count("OrderAccepted") >= 2
        assert h.cache.order(ClientOrderId("O-CLOSED-2")).status_string() == "CANCELED"


def _open_report(instrument, coid: str):
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.execution.reports import OrderStatusReport
    from nautilus_trader.model.enums import OrderStatus, OrderType, TimeInForce

    return OrderStatusReport(
        account_id=ACCOUNT,
        instrument_id=instrument.id,
        venue_order_id=VenueOrderId("9001"),
        order_side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        order_status=OrderStatus.ACCEPTED,
        quantity=Quantity.from_int(10),
        filled_qty=Quantity.from_int(0),
        avg_px=Decimal(0),
        price=Price.from_str("90.00"),
        report_id=UUID4(),
        ts_accepted=1,
        ts_last=1,
        ts_init=1,
        client_order_id=ClientOrderId(coid),
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _reconciler(h: _Harness, state, *listed: str) -> tuple[RuntimeReconciler, _Clock]:
    """``listed``: the client order ids IBKR lists open (Story 4.8's read)."""
    clock = _Clock()
    monitor = ConnectionMonitor(session_id="s-1", time_source=clock)
    monitor.confirm_state_reestablished(UP)

    async def _read(node, *, log):
        return state

    async def _open_orders(node):
        return BrokerOpenOrders(frozenset(listed), frozenset())

    return (
        RuntimeReconciler(
            node=h.node,
            monitor=monitor,
            log=structlog.get_logger("test"),
            connection_reader=lambda settings: UP,
            settings=object(),
            read_state=_read,
            clock=clock,
            read_open_orders=_open_orders,
        ),
        clock,
    )


def _cycles(h: _Harness, reconciler: RuntimeReconciler, clock: _Clock, count: int) -> None:
    """Drive ``count`` runtime cycles, ``DEBOUNCE_SECONDS`` apart."""
    for _ in range(count):
        clock.now += DEBOUNCE_SECONDS
        for _ in range(RUNTIME_RECONCILE_EVERY_TICKS):
            h.loop.run_until_complete(reconciler.on_tick())


class TestRuntimeCorrectionsAreBrokerWard:
    """AC #2 against the real engine and cache."""

    def test_a_broker_position_the_cache_lacks_is_hydrated_at_the_brokers_price(self, harness):
        h = harness()
        reconciler, clock = _reconciler(h, _state((NVDA.id, "5", "101.25")))

        with capture_logs() as logs:
            _cycles(h, reconciler, clock, 2)

        assert h.net(NVDA) == 5
        assert h.portfolio.net_position(NVDA.id) == 5
        (position,) = h.cache.positions_open()
        assert str(position.strategy_id) == "INTERNAL-DIFF"
        assert Decimal(str(position.avg_px_open)) == Decimal("101.25")
        (record,) = [e for e in logs if e["event"] == DISCREPANCY_EVENT]
        assert (
            record["scope"],
            record["resolution"],
            record["instrument_id"],
            record["local_quantity"],
            record["broker_quantity"],
        ) == ("runtime", "broker", str(NVDA.id), "0", "5")

    def test_a_stale_synthetic_position_the_broker_closed_is_flattened(self, harness):
        h = harness()
        h.seed(AAPL, 4, StrategyId("EXTERNAL"))
        reconciler, clock = _reconciler(h, _state())

        _cycles(h, reconciler, clock, 2)

        assert h.net(AAPL) == 0
        assert h.portfolio.net_position(AAPL.id) == 0

    def test_seen_once_nothing_is_written(self, harness):
        h = harness()
        reconciler, clock = _reconciler(h, _state((NVDA.id, "5", "101.25")))

        _cycles(h, reconciler, clock, 1)

        assert h.cache.positions_open() == []

    def test_a_contradicted_strategy_position_is_refused_and_nothing_is_written(self, harness):
        """D-D (PO ruling 2A), never the reverse: the strategy's +22 stays
        exactly as the cache recorded it, no synthetic position appears."""
        h = harness()
        h.seed(NVDA, 22)
        reconciler, clock = _reconciler(h, _state())

        with pytest.raises(ReconciliationFailedError) as caught:
            _cycles(h, reconciler, clock, 2)

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert h.open_positions() == {str(STRATEGY): Decimal(22)}


class TestAStrandedOrderIsClearedByTheRunningSession:
    """Story 4.8, AC #1 end to end: the runtime cycle, the real engine and cache."""

    def test_an_order_ibkr_no_longer_lists_is_canceled_after_two_cycles(self, harness):
        h = harness()
        _working_order(h, AAPL, "O-STRANDED-1")
        reconciler, clock = _reconciler(h, _state())

        _cycles(h, reconciler, clock, 1)
        assert h.cache.order(ClientOrderId("O-STRANDED-1")).status_string() == "ACCEPTED"
        with capture_logs() as logs:
            _cycles(h, reconciler, clock, 1)

        assert h.cache.order(ClientOrderId("O-STRANDED-1")).status_string() == "CANCELED"
        assert [e["event"] for e in logs if e["event"] == CLEARED_EVENT] == [CLEARED_EVENT]
        assert h.cache.positions_open() == []

    def test_an_order_ibkr_still_lists_is_left_open(self, harness):
        h = harness()
        _working_order(h, AAPL, "O-LIVE-1")
        reconciler, clock = _reconciler(h, _state(), "O-LIVE-1")

        _cycles(h, reconciler, clock, 4)

        assert h.cache.order(ClientOrderId("O-LIVE-1")).status_string() == "ACCEPTED"

    def test_filled_while_away_the_position_is_corrected_first_then_the_order_cleared(
        self, harness
    ):
        """Task 1.1's case: IBKR holds the 10 the stranded order bought. The
        position row is corrected broker-ward (cycle 2) while the order waits
        (AC #5); the order is first seen absent on the first clean cycle (3)
        and cleared on the next (4) — ``CANCELED``, never a fabricated fill (D-C)."""
        h = harness()
        _working_order(h, AAPL, "O-STRANDED-2")
        reconciler, clock = _reconciler(h, _state((AAPL.id, "10", "90.00")))

        _cycles(h, reconciler, clock, 3)
        assert h.cache.order(ClientOrderId("O-STRANDED-2")).status_string() == "ACCEPTED"
        assert h.open_positions() == {"INTERNAL-DIFF": Decimal(10)}
        _cycles(h, reconciler, clock, 1)

        order = h.cache.order(ClientOrderId("O-STRANDED-2"))
        assert order.status_string() == "CANCELED"
        assert order.filled_qty == Quantity.from_int(0)
        assert h.open_positions() == {"INTERNAL-DIFF": Decimal(10)}


class TestTheOnlyEngineMutationIsTheFrameworksOwnEntryPoint:
    """Story 4.2's scan, applied to the runtime module."""

    def test_reconcile_execution_report_is_the_only_engine_method_called(self):
        source = Path(live_runtime_reconcile.__file__).read_text()

        assert _engine_method_calls(source) == {"reconcile_execution_report"}
