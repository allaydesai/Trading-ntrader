"""Clearing a stranded order against the real engine and the real IB adapter (Story 4.8).

Component tier, two halves, never a ``TradingNode`` (the C-logging guard below):

- **The engine half** drives :class:`StaleOrderWatch` against Story 4.2's
  harness — a real message bus, ``Cache``, ``Portfolio`` and
  ``LiveExecutionEngine`` — with the broker read stubbed, proving the clear lands
  as the framework's own ``OrderCanceled(reconciliation=True)`` and never
  touches a position (AC #1, #4, #5).
- **The adapter half** drives :func:`broker_open_orders` against Story 4.1's
  real ``InteractiveBrokersClient`` / ``InteractiveBrokersExecutionClient``,
  with only the socket a stand-in that answers ``reqOpenOrders`` through the
  adapter's own ``process_open_order`` / ``process_open_order_end`` handlers —
  so the swallowing of a timeout or a dropped socket into ``[]`` that the read
  exists to see through is the adapter's real behaviour (AC #2, D-D amended).
  Its canaries pin every private member the read depends on, and the measured
  reason ``generate_order_status_reports`` is not used.
"""

import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from ibapi.order import Order as IBOrder
from ibapi.order_state import OrderState
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import GenerateOrderStatusReports
from nautilus_trader.model.enums import OrderSide, TimeInForce, TriggerType
from nautilus_trader.model.events import OrderCanceled, OrderFilled, OrderUpdated
from nautilus_trader.model.identifiers import ClientOrderId, VenueOrderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import StopLimitOrder
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from structlog.testing import capture_logs

from src.core import live_stranded_orders
from src.core.live_stranded_orders import (
    CLEAR_FAILED_EVENT,
    CLEARED_EVENT,
    OPEN_ORDERS_REQUEST,
    STALE_ORDER_DEBOUNCE_SECONDS,
    BrokerOpenOrders,
    Inconclusive,
    StaleOrderWatch,
    broker_open_orders,
)
from tests.component.core.test_live_broker_state_adapter import (
    ACCOUNT as RAW_ACCOUNT,
)
from tests.component.core.test_live_broker_state_adapter import (
    NVDA_CON_ID,
    SocketStandIn,
    _contract,
    _positions,
    _stack,
)
from tests.component.core.test_live_runtime_reconcile_engine import _session_exec_config
from tests.component.core.test_live_startup_reconcile_engine import (
    AAPL,
    ACCOUNT,
    NVDA,
    STRATEGY,
    TRADER,
    _engine_method_calls,
    _Harness,
)

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, "this component test constructed a TradingNode"


# --- the engine half ------------------------------------------------------------


@pytest.fixture
def harness():
    made: list[_Harness] = []

    def _make() -> _Harness:
        made.append(_Harness(None, **_session_exec_config()))
        return made[-1]

    yield _make
    for each in made:
        if not each.loop.is_closed():
            each.close()


def _resting(h: _Harness, instrument, coid: str, *, partial: bool = False):
    """A limit order IBKR acknowledged before the session went away."""
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
    order.apply(
        TestEventStubs.order_accepted(order, account_id=ACCOUNT, venue_order_id=VenueOrderId(coid))
    )
    if partial:
        order.apply(
            TestEventStubs.order_filled(
                order, instrument, account_id=ACCOUNT, last_qty=Quantity.from_int(4)
            )
        )
    h.cache.update_order(order)
    return order


class _Reader:
    def __init__(self, *listed: str) -> None:
        self.answer: BrokerOpenOrders | Inconclusive = BrokerOpenOrders(
            frozenset(listed), frozenset()
        )
        self.calls = 0

    async def __call__(self, node):
        self.calls += 1
        return self.answer


def _cycles(h: _Harness, watch: StaleOrderWatch, times: int, *, skip=frozenset()) -> None:
    import structlog

    now = 1000.0
    for _ in range(times):
        h.loop.run_until_complete(
            watch.check(h.node, structlog.get_logger("test"), now, skip=skip, scope="runtime")
        )
        now += STALE_ORDER_DEBOUNCE_SECONDS


def _status(h: _Harness, coid: str) -> str:
    return h.cache.order(ClientOrderId(coid)).status_string()


def _order_events(h: _Harness) -> list:
    seen: list = []
    h.engine._msgbus.subscribe(f"events.order.{STRATEGY}", seen.append)
    return seen


class TestTheClearIsTheFrameworksOwnCancel:
    """AC #1 and #4 against the real engine and cache."""

    def test_absent_on_two_reads_a_debounce_apart_it_becomes_canceled(self, harness):
        h = harness()
        _resting(h, AAPL, "O-STRANDED-1")
        events = _order_events(h)

        with capture_logs() as logs:
            _cycles(h, StaleOrderWatch(_Reader()), 2)

        assert _status(h, "O-STRANDED-1") == "CANCELED"
        assert h.cache.orders_open() == []
        assert [type(e).__name__ for e in events] == ["OrderCanceled"]
        assert events[0].reconciliation is True
        (record,) = [e for e in logs if e["event"] == CLEARED_EVENT]
        assert (record["client_order_id"], record["instrument_id"]) == (
            "O-STRANDED-1",
            str(AAPL.id),
        )

    def test_seen_absent_once_the_order_stays_open(self, harness):
        h = harness()
        _resting(h, AAPL, "O-STRANDED-1")

        _cycles(h, StaleOrderWatch(_Reader()), 1)

        assert _status(h, "O-STRANDED-1") == "ACCEPTED"

    def test_an_order_the_broker_lists_is_never_touched(self, harness):
        h = harness()
        _resting(h, AAPL, "O-LIVE-1")
        events = _order_events(h)

        _cycles(h, StaleOrderWatch(_Reader("O-LIVE-1")), 4)

        assert _status(h, "O-LIVE-1") == "ACCEPTED"
        assert events == []

    def test_a_partly_filled_order_is_canceled_with_no_fill_and_no_amendment(self, harness):
        """AC #4: never a fabricated fill — the 4 the strategy already holds
        stays exactly as recorded, and the order's own price means the engine
        has nothing to amend first (Task 1.3)."""
        h = harness()
        _resting(h, AAPL, "O-PART-1", partial=True)
        events = _order_events(h)

        _cycles(h, StaleOrderWatch(_Reader()), 2)

        order = h.cache.order(ClientOrderId("O-PART-1"))
        assert order.status_string() == "CANCELED"
        assert order.filled_qty == Quantity.from_int(4)
        assert not [e for e in events if isinstance(e, OrderFilled | OrderUpdated)]
        assert [e for e in events if isinstance(e, OrderCanceled)]

    def test_a_triggered_stop_limit_is_canceled_with_no_amendment(self, harness):
        """Code review 2026-10-06: the report once carried a trigger price with
        no trigger type, which ``OrderStatusReport`` refuses — a stranded stop
        order could never clear. Its own trigger type, so nothing to amend."""
        h = harness()
        stop = StopLimitOrder(
            TRADER,
            STRATEGY,
            AAPL.id,
            ClientOrderId("O-STOP-1"),
            OrderSide.BUY,
            Quantity.from_int(10),
            Price.from_str("90.00"),
            Price.from_str("91.00"),
            TriggerType.DEFAULT,
            UUID4(),
            0,
            time_in_force=TimeInForce.GTC,
        )
        h.cache.add_order(stop, None)
        stop.apply(TestEventStubs.order_submitted(stop, account_id=ACCOUNT))
        stop.apply(
            TestEventStubs.order_accepted(
                stop, account_id=ACCOUNT, venue_order_id=VenueOrderId("O-STOP-1")
            )
        )
        stop.apply(TestEventStubs.order_triggered(stop))
        h.cache.update_order(stop)
        events = _order_events(h)

        with capture_logs() as logs:
            _cycles(h, StaleOrderWatch(_Reader()), 2)

        assert _status(h, "O-STOP-1") == "CANCELED"
        assert [type(e).__name__ for e in events] == ["OrderCanceled"]
        assert not [e for e in logs if e["event"] == CLEAR_FAILED_EVENT]


class TestAnInstrumentWithAPositionIssueWaits:
    """AC #5 on the real engine: only the clean instrument's order clears."""

    def test_the_skipped_instruments_order_stays_open(self, harness):
        h = harness()
        _resting(h, NVDA, "O-NVDA-1")
        _resting(h, AAPL, "O-AAPL-1")

        _cycles(h, StaleOrderWatch(_Reader()), 3, skip=frozenset({str(NVDA.id)}))

        assert _status(h, "O-NVDA-1") == "ACCEPTED"
        assert _status(h, "O-AAPL-1") == "CANCELED"


# --- the adapter half -------------------------------------------------------------


class _OpenOrdersSocket(SocketStandIn):
    """Story 4.1's socket stand-in, also answering ``reqOpenOrders`` through
    the adapter's own handlers — or not at all, when ``open_orders`` is None."""

    def __init__(self) -> None:
        super().__init__()
        self.open_orders: list[IBOrder] | None = []
        self.drop = False
        self.open_order_requests = 0

    def reqOpenOrders(self) -> None:
        self.open_order_requests += 1
        client, rows, drop = self.client, self.open_orders, self.drop

        async def answer() -> None:
            if drop:
                client.process_connection_closed()
                return
            for row in rows or []:
                await client.process_open_order(
                    order_id=row.orderId,
                    contract=_contract("NVDA", NVDA_CON_ID),
                    order=row,
                    order_state=_submitted(),
                )
            await client.process_open_order_end()

        if rows is not None:
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(answer()))


def _submitted() -> OrderState:
    state = OrderState()
    state.status = "Submitted"
    return state


def _ib_order(order_ref: str, order_id: int, *, account: str = RAW_ACCOUNT) -> IBOrder:
    """A resting limit order as IBKR describes it; ``orderRef=""`` is one
    placed by hand in TWS."""
    row = IBOrder()
    row.orderRef = order_ref
    row.orderId = order_id
    row.account = account
    row.action, row.orderType, row.tif = "BUY", "LMT", "DAY"
    row.totalQuantity, row.lmtPrice = Decimal(1), 90.0
    return row


def _adapter(*, connected: bool = True):
    stack = _stack(connected=connected)
    socket = _OpenOrdersSocket()
    socket.client = stack.ib
    stack.ib._eclient = socket
    stack.socket = socket
    return stack


class TestAgainstTheRealAdapter:
    async def test_our_order_is_read_by_its_order_ref_beside_a_manual_one(self):
        """F12's landmine — a manual order with no ``orderRef`` — is read past,
        not raised: raw strings are compared, no ``ClientOrderId`` is built."""
        stack = _adapter()
        stack.socket.open_orders = [
            _ib_order("O-20261006-000000-000-001-1", 7),
            _ib_order("", 8),
            _ib_order("O-ELSEWHERE", 9, account="DU9999999"),
        ]

        answer = await broker_open_orders(stack.node, deadline_seconds=5)

        assert answer == BrokerOpenOrders(
            frozenset({"O-20261006-000000-000-001-1"}), frozenset({"7", "8"})
        )

    async def test_ibkr_listing_nothing_is_a_conclusive_empty_answer(self):
        stack = _adapter()

        answer = await broker_open_orders(stack.node, deadline_seconds=5)

        assert answer == BrokerOpenOrders(frozenset(), frozenset())
        assert stack.socket.open_order_requests == 1

    async def test_a_dropped_socket_is_inconclusive_where_the_adapter_says_empty(self):
        stack = _adapter()
        stack.socket.drop = True

        answer = await broker_open_orders(stack.node, deadline_seconds=5)

        assert answer == Inconclusive("ConnectionError")

    async def test_premise_the_adapters_own_answer_to_a_dropped_socket_is_empty(self):
        stack = _adapter()
        stack.socket.drop = True

        assert await stack.ib.get_open_orders(RAW_ACCOUNT) == []

    async def test_the_adapters_own_timeout_on_a_joined_request_is_inconclusive(self):
        """Someone else's request (Nautilus's in-flight sweep) times out while
        the read is joined: its ``wait_for`` cancels the shared future."""
        stack = _adapter()
        ib = stack.ib
        request = ib._requests.add(
            req_id=ib._next_req_id(), name=OPEN_ORDERS_REQUEST, handle=lambda: None
        )
        originator = asyncio.ensure_future(ib._await_request(request, 0.1))

        answer = await broker_open_orders(stack.node, deadline_seconds=5)

        assert answer == Inconclusive("cancelled")
        assert await originator is None
        assert stack.socket.open_order_requests == 0, "the read joined; it issued nothing"

    async def test_a_silent_broker_is_inconclusive_and_the_request_is_left_running(self):
        stack = _adapter()
        stack.socket.open_orders = None

        answer = await broker_open_orders(stack.node, deadline_seconds=0.2)

        assert answer == Inconclusive("timed_out")
        request = stack.ib._requests.get(name=OPEN_ORDERS_REQUEST)
        assert request is not None and not request.future.done()
        await stack.ib.process_open_order_end()  # IBKR answers late: nothing broken
        assert request.future.result() == []

    async def test_a_socket_that_is_down_issues_nothing(self):
        stack = _adapter(connected=False)

        answer = await broker_open_orders(stack.node, deadline_seconds=5)

        assert answer == Inconclusive("not_connected")
        assert stack.socket.open_order_requests == 0


class TestAdapterCanaries:
    """Each premise of the read, pinned by name against the installed wheel."""

    async def test_get_open_orders_registers_its_request_before_its_first_await(self):
        stack = _adapter()
        stack.socket.open_orders = None
        task = asyncio.ensure_future(stack.ib.get_open_orders(RAW_ACCOUNT))
        await asyncio.sleep(0)

        request = stack.ib._requests.get(name=OPEN_ORDERS_REQUEST)

        assert request is not None, f"get_open_orders no longer registers {OPEN_ORDERS_REQUEST!r}"
        assert isinstance(request.future, asyncio.Future)
        request.future.set_result([])
        assert await task == []

    def test_an_ib_order_still_has_the_fields_the_read_reads(self):
        row = IBOrder()

        assert (row.orderRef, row.orderId, row.account) == ("", 0, "")

    async def test_generate_order_status_reports_never_asks_a_flat_account_for_orders(self):
        """Task 1.2b — the measured reason D-D was amended. If an upgrade makes
        the adapter ask for open orders on a flat account, this goes red by name
        and the original D-D design may be reconsidered."""
        stack = _adapter()
        stack.socket.script = _positions()
        stack.socket.open_orders = [_ib_order("O-LIVE-AT-IB", 7)]
        command = GenerateOrderStatusReports(
            instrument_id=None, start=None, end=None, open_only=True, command_id=UUID4(), ts_init=0
        )

        reports = await stack.exec_client.generate_order_status_reports(command)

        assert reports == []
        assert stack.socket.open_order_requests == 0

    async def test_one_manual_order_makes_generate_order_status_reports_raise(self):
        """F12, against the real parse: a manual TWS order fails the whole call."""
        stack = _adapter()
        stack.socket.script = _positions((RAW_ACCOUNT, "NVDA", NVDA_CON_ID, 1, 180.5))
        stack.socket.open_orders = [_ib_order("", 8)]
        # What the provider holds once it has resolved NVDA's contract live.
        stack.exec_client.instrument_provider.contract_details[NVDA.id] = SimpleNamespace(
            priceMagnifier=1
        )
        command = GenerateOrderStatusReports(
            instrument_id=None, start=None, end=None, open_only=True, command_id=UUID4(), ts_init=0
        )

        with pytest.raises(ValueError, match="'value' string was invalid, was ''"):
            await stack.exec_client.generate_order_status_reports(command)


class TestTheOnlyEngineMutationIsTheFrameworksOwnEntryPoint:
    """Story 4.2's scan, applied to this module: no order method, no cache write."""

    def test_reconcile_execution_report_is_the_only_engine_method_called(self):
        source = Path(live_stranded_orders.__file__).read_text()

        assert _engine_method_calls(source) == {"reconcile_execution_report"}
