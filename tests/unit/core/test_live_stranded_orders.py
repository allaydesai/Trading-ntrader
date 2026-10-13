"""Unit tests for clearing an order IBKR no longer lists open (Story 4.8, AC #1-#5).

Real Nautilus order *values* (``TestExecStubs`` + ``TestEventStubs``) in a
duck-typed cache and exec engine, an imitation IB client whose
``get_open_orders`` registers its ``OpenOrders`` request the way the adapter
does, and an injected clock. No engine runs here: the real
``LiveExecutionEngine`` and the real IB adapter are driven in
``tests/component/core/test_live_stranded_orders_engine.py``.

Every "nothing happened" assertion has a sibling proving the same spy records
when something does (Epic 2 retro: a test that cannot fail).
"""

import ast
import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import OrderSide, OrderStatus, TimeInForce, TriggerType
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    StrategyId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import StopLimitOrder
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from structlog.testing import capture_logs

from src.core import live_stranded_orders
from src.core.live_stranded_orders import (
    CHECK_FAILED_EVENT,
    CLEAR_FAILED_EVENT,
    CLEARED_EVENT,
    EMITTED_STALE_ORDER_EVENTS,
    OPEN_ORDERS_DEADLINE_SECONDS,
    OPEN_ORDERS_REQUEST,
    STALE_ORDER_DEBOUNCE_SECONDS,
    UNDETERMINED,
    BrokerOpenOrders,
    Inconclusive,
    StaleOrderWatch,
    broker_open_orders,
    canceled_report,
    stale_accepted_orders,
)

pytestmark = pytest.mark.unit

RAW_ACCOUNT = "DU4076626"
ACCOUNT = AccountId(f"INTERACTIVE_BROKERS-{RAW_ACCOUNT}")
TRADER = TraderId("PAPER-0e8f1c2a")
STRATEGY = StrategyId("SMACrossover-000")
NVDA = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
SCOPE = "runtime"


# --- doubles ------------------------------------------------------------------


def _order(
    coid: str,
    *,
    instrument=AAPL,
    status: str = "ACCEPTED",
    venue_order_id: str | None = None,
):
    """A real limit order driven to ``status`` by the framework's own events."""
    order = TestExecStubs.limit_order(
        instrument=instrument,
        order_side=OrderSide.BUY,
        quantity=Quantity.from_int(10),
        price=Price.from_str("90.00"),
        trader_id=TRADER,
        strategy_id=STRATEGY,
        client_order_id=ClientOrderId(coid),
    )
    if status == "INITIALIZED":
        return order
    order.apply(TestEventStubs.order_submitted(order, account_id=ACCOUNT))
    if status == "SUBMITTED":
        return order
    order.apply(
        TestEventStubs.order_accepted(
            order, account_id=ACCOUNT, venue_order_id=VenueOrderId(venue_order_id or f"V-{coid}")
        )
    )
    if status == "PARTIALLY_FILLED":
        order.apply(
            TestEventStubs.order_filled(
                order, instrument, account_id=ACCOUNT, last_qty=Quantity.from_int(4)
            )
        )
    elif status == "PENDING_CANCEL":
        order.apply(TestEventStubs.order_pending_cancel(order))
    elif status == "PENDING_UPDATE":
        order.apply(TestEventStubs.order_pending_update(order))
    elif status == "CANCELED":
        order.apply(TestEventStubs.order_canceled(order))
    elif status == "FILLED":
        order.apply(TestEventStubs.order_filled(order, instrument, account_id=ACCOUNT))
    return order


def _triggered(coid: str = "O-TRIG"):
    """A real stop-limit order, accepted at ``ts_event=777`` and then triggered."""
    order = StopLimitOrder(
        TRADER,
        STRATEGY,
        AAPL.id,
        ClientOrderId(coid),
        OrderSide.BUY,
        Quantity.from_int(10),
        Price.from_str("90.00"),
        Price.from_str("91.00"),
        TriggerType.DEFAULT,
        UUID4(),
        0,
        time_in_force=TimeInForce.GTC,
    )
    order.apply(TestEventStubs.order_submitted(order, account_id=ACCOUNT))
    order.apply(
        TestEventStubs.order_accepted(
            order, account_id=ACCOUNT, venue_order_id=VenueOrderId(f"V-{coid}"), ts_event=777
        )
    )
    order.apply(TestEventStubs.order_triggered(order))
    return order


class _Cache:
    """The three order views the module reads — by the orders' own state."""

    def __init__(self, *orders) -> None:
        self.orders = {order.client_order_id: order for order in orders}

    def orders_open(self):
        return [order for order in self.orders.values() if order.is_open]

    def orders_inflight(self):
        return [order for order in self.orders.values() if order.is_inflight]

    def order(self, client_order_id):
        if getattr(self, "lookup_raises", None) is not None:
            raise self.lookup_raises
        return self.orders.get(client_order_id)


class _Engine:
    """Records every report; by default applies it the way Nautilus does —
    an ``OrderCanceled`` on the cached order (Task 1.3)."""

    def __init__(self, cache: _Cache) -> None:
        self.cache = cache
        self.reports: list = []
        self.applies = True
        self.returns = True
        self.raises: BaseException | None = None

    def reconcile_execution_report(self, report) -> bool:
        self.reports.append(report)
        if self.raises is not None:
            raise self.raises
        order = self.cache.order(report.client_order_id)
        if self.applies and order is not None and order.is_open:
            order.apply(TestEventStubs.order_canceled(order))
        return self.returns


def _node(*orders):
    cache = _Cache(*orders)
    return SimpleNamespace(cache=cache, kernel=SimpleNamespace(exec_engine=_Engine(cache)))


class _Reader:
    """The broker read the watch is given: a settable answer and a call log."""

    def __init__(self, *listed: str) -> None:
        self.answer: BrokerOpenOrders | Inconclusive = BrokerOpenOrders(
            frozenset(listed), frozenset()
        )
        self.raises: BaseException | None = None
        self.calls = 0

    async def __call__(self, node):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.answer


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0


def _check(watch: StaleOrderWatch, node, clock: _Clock, *, skip=frozenset()) -> None:
    asyncio.run(watch.check(node, _log(), clock.now, skip=skip, scope=SCOPE))


def _cycles(watch, node, clock, times: int, *, gap=STALE_ORDER_DEBOUNCE_SECONDS, **kwargs):
    for _ in range(times):
        _check(watch, node, clock, **kwargs)
        clock.now += gap


def _log():
    import structlog

    return structlog.get_logger("test").bind(session_id="s-1")


def _events(logs, name):
    return [e for e in logs if e["event"] == name]


def _status(node, coid: str) -> str:
    return node.cache.order(ClientOrderId(coid)).status_string()


# --- the imitation IB client ----------------------------------------------------


class _Request:
    def __init__(self) -> None:
        self.future: asyncio.Future = asyncio.get_running_loop().create_future()


class _Requests:
    def __init__(self) -> None:
        self.by_name: dict[str, _Request] = {}

    def get(self, *, name):
        return self.by_name.get(name)


class _IBClient:
    """``get_open_orders`` the adapter's way: register the ``OpenOrders``
    request synchronously, send, then await its future — swallowing a timeout
    or a lost connection to ``[]`` (``client/order.py:99-131``)."""

    def __init__(self, *, connected: bool = True) -> None:
        self._requests = _Requests()
        flag = SimpleNamespace(is_set=lambda: connected)
        self._is_ib_connected = flag
        self._is_client_ready = flag
        #: a callable(request) run on send, or None for a silent broker
        self.answer: Any = None
        self.send_raises: BaseException | None = None
        #: raised before the request is registered (e.g. allocating its id)
        self.register_raises: BaseException | None = None
        self.calls = 0

    async def get_open_orders(self, account_id: str):
        self.calls += 1
        if self.register_raises is not None:
            raise self.register_raises
        request = self._requests.get(name=OPEN_ORDERS_REQUEST)
        if request is None:
            request = _Request()
            self._requests.by_name[OPEN_ORDERS_REQUEST] = request
            if self.send_raises is not None:
                raise self.send_raises
            if self.answer is not None:
                asyncio.get_running_loop().call_soon(self.answer, request)
        try:
            rows = await asyncio.wait_for(request.future, 30)
        except (TimeoutError, ConnectionError):
            rows = None
        self._requests.by_name.pop(OPEN_ORDERS_REQUEST, None)
        return [row for row in rows or [] if row.account == account_id]


def _ib_node(ib: _IBClient, *, exec_connected: bool = True):
    exec_client = SimpleNamespace(_client=ib, account_id=ACCOUNT, is_connected=exec_connected)
    engine = SimpleNamespace(_clients={"INTERACTIVE_BROKERS": exec_client})
    return SimpleNamespace(kernel=SimpleNamespace(exec_engine=engine))


def _ib_order(order_ref: str, order_id: int, account: str = RAW_ACCOUNT):
    return SimpleNamespace(orderRef=order_ref, orderId=order_id, account=account)


def _answers(*rows):
    def answer(request) -> None:
        request.future.set_result(list(rows))

    return answer


def _fails(exc: BaseException):
    def answer(request) -> None:
        request.future.set_exception(exc)

    return answer


def _read(ib: _IBClient, **kwargs):
    async def go():
        return await broker_open_orders(_ib_node(ib, **kwargs.pop("node", {})), **kwargs)

    return asyncio.run(go())


# --- candidates (D-D) -----------------------------------------------------------


class TestCandidates:
    def test_exactly_the_open_orders_at_the_broker_and_not_in_flight(self):
        """Task 1.1's measured set: ``ACCEPTED``, ``PARTIALLY_FILLED`` and
        ``TRIGGERED`` are candidates; in-flight, closed and never-sent are not."""
        cache = _Cache(
            _order("O-ACC", status="ACCEPTED"),
            _order("O-PART", status="PARTIALLY_FILLED"),
            _triggered("O-TRIG"),
            _order("O-SUB", status="SUBMITTED"),
            _order("O-PCAN", status="PENDING_CANCEL"),
            _order("O-PUPD", status="PENDING_UPDATE"),
            _order("O-INIT", status="INITIALIZED"),
            _order("O-DONE", status="CANCELED"),
            _order("O-FILL", status="FILLED"),
        )

        found = {order.client_order_id.value for order in stale_accepted_orders(cache)}

        assert found == {"O-ACC", "O-PART", "O-TRIG"}

    def test_an_open_order_without_a_venue_order_id_is_never_a_candidate(self):
        order = SimpleNamespace(client_order_id=ClientOrderId("O-EMU"), venue_order_id=None)
        cache = SimpleNamespace(orders_open=lambda: [order], orders_inflight=lambda: [])

        assert stale_accepted_orders(cache) == ()


# --- the broker read (D-D as amended, AC #2) -----------------------------------------


class TestTheBrokersOpenOrders:
    def test_ibkrs_answer_is_read_by_order_ref_and_by_order_id_for_this_account_only(self):
        ib = _IBClient()
        ib.answer = _answers(
            _ib_order("O-OURS-1", 11),
            _ib_order("", 12),  # a manual TWS order: no orderRef (F12)
            _ib_order("O-OTHER-ACCOUNT", 13, account="DU9999999"),
        )

        answer = _read(ib)

        assert answer == BrokerOpenOrders(frozenset({"O-OURS-1"}), frozenset({"11", "12"}))

    def test_an_empty_answer_is_ibkrs_own_and_conclusive(self):
        ib = _IBClient()
        ib.answer = _answers()

        assert _read(ib) == BrokerOpenOrders(frozenset(), frozenset())

    def test_an_order_is_listed_by_either_key(self):
        listed = BrokerOpenOrders(frozenset({"O-A"}), frozenset({"V-B"}))

        assert listed.lists(_order("O-A"))
        assert listed.lists(_order("O-X", venue_order_id="V-B"))
        assert not listed.lists(_order("O-C"))

    @pytest.mark.parametrize(
        "exc", [ValueError("bad orderRef"), TimeoutError(), ConnectionError(), Exception()]
    )
    def test_an_answer_that_is_not_ibkrs_is_inconclusive_never_empty(self, exc):
        """The adapter swallows a timeout or a lost connection into ``[]``;
        reading the request's own future is what keeps that from passing as
        "IBKR lists nothing" (D-D amended)."""
        ib = _IBClient()
        ib.answer = _fails(exc)

        answer = _read(ib)

        assert answer == Inconclusive(type(exc).__name__)

    def test_premise_the_adapters_own_return_value_would_have_said_empty(self):
        """The sibling of the test above: the same failure, read the adapter's
        way, is indistinguishable from a broker with no open orders."""
        ib = _IBClient()
        ib.answer = _fails(TimeoutError())

        async def go():
            return await ib.get_open_orders(RAW_ACCOUNT)

        assert asyncio.run(go()) == []

    @pytest.mark.parametrize("where", ["register_raises", "send_raises"])
    def test_a_send_that_fails_before_or_after_registering_is_inconclusive(self, where):
        """Before: no request ever exists. After: one exists that nothing will
        answer (the adapter's ``request.handle()`` raising) — the task is done,
        so it is not a timeout either."""
        ib = _IBClient()
        setattr(ib, where, OSError("socket"))

        assert _read(ib) == Inconclusive("unanswered")
        assert ib.calls == 1, "premise: the send was attempted"

    def test_a_raise_outside_the_adapter_call_is_contained_by_type(self):
        ib = _IBClient()
        node = _ib_node(ib)
        node.kernel.exec_engine._clients["INTERACTIVE_BROKERS"].account_id = None

        async def go():
            return await broker_open_orders(node)

        assert asyncio.run(go()) == Inconclusive("AttributeError")

    def test_a_silent_broker_is_inconclusive_at_the_deadline_and_the_request_left_alone(self):
        ib = _IBClient()

        async def go():
            node = _ib_node(ib)
            answer = await broker_open_orders(node, deadline_seconds=0.05)
            request = ib._requests.get(name=OPEN_ORDERS_REQUEST)
            return answer, request is not None and not request.future.done()

        answer, still_pending = asyncio.run(go())

        assert answer == Inconclusive("timed_out")
        assert still_pending, "giving up cancelled a request Nautilus may share"

    def test_a_request_someone_else_started_is_joined_and_its_failure_is_inconclusive(self):
        ib = _IBClient()

        async def go():
            request = _Request()
            ib._requests.by_name[OPEN_ORDERS_REQUEST] = request
            asyncio.get_running_loop().call_soon(request.future.cancel)
            return await broker_open_orders(_ib_node(ib))

        assert asyncio.run(go()) == Inconclusive("cancelled")

    @pytest.mark.parametrize("node", [{"exec_connected": False}])
    def test_a_client_that_is_not_connected_is_inconclusive_and_asks_nothing(self, node):
        ib = _IBClient()

        assert _read(ib, node=node) == Inconclusive("not_connected")
        assert ib.calls == 0

    def test_a_socket_that_is_down_is_inconclusive_and_asks_nothing(self):
        ib = _IBClient(connected=False)

        assert _read(ib) == Inconclusive("not_connected")
        assert ib.calls == 0

    def test_a_node_without_the_ib_client_is_inconclusive_by_type_only(self):
        engine = SimpleNamespace(_clients={})
        node = SimpleNamespace(kernel=SimpleNamespace(exec_engine=engine))

        async def go():
            return await broker_open_orders(node)

        assert asyncio.run(go()) == Inconclusive("BrokerStateAdapterError")


# --- the report (D-C RULED (A), D-E, AC #4) ---------------------------------------------


class TestTheReportIsCanceledFromTheOrdersOwnFields:
    def test_every_field_is_the_orders_own(self):
        order = _order("O-PART", status="PARTIALLY_FILLED")

        report = canceled_report(order, ts_ns=123)

        assert report.order_status == OrderStatus.CANCELED
        assert (
            report.account_id,
            report.instrument_id,
            report.venue_order_id,
            report.client_order_id,
            report.order_side,
            report.order_type,
            report.time_in_force,
            report.quantity,
            report.filled_qty,
            report.price,
        ) == (
            order.account_id,
            order.instrument_id,
            order.venue_order_id,
            order.client_order_id,
            order.side,
            order.order_type,
            order.time_in_force,
            order.quantity,
            order.filled_qty,
            order.price,
        )
        assert report.avg_px == Decimal(str(order.avg_px))
        assert report.ts_accepted == order.ts_accepted, "acceptance is the order's, not now"
        assert (report.ts_last, report.ts_init) == (123, 123)

    def test_a_triggered_stop_limit_carries_its_own_trigger_price_and_acceptance_time(self):
        order = _triggered()

        report = canceled_report(order, ts_ns=123)

        assert (report.price, report.trigger_price) == (order.price, order.trigger_price)
        assert report.ts_accepted == 777

    def test_an_order_without_a_price_carries_none(self):
        order = TestExecStubs.market_order(instrument=AAPL, client_order_id=ClientOrderId("O-M"))
        order.apply(TestEventStubs.order_submitted(order, account_id=ACCOUNT))
        order.apply(TestEventStubs.order_accepted(order, account_id=ACCOUNT))

        report = canceled_report(order, ts_ns=1)

        assert (report.price, report.trigger_price) == (None, None)


def _module_tree() -> ast.Module:
    return ast.parse(Path(live_stranded_orders.__file__).read_text())


class TestNeverAFabricatedFill:
    """AC #4: one direction is structurally impossible, not merely untested."""

    def test_the_only_order_status_the_module_builds_is_canceled(self):
        built = [
            ast.unparse(keyword.value)
            for node in ast.walk(_module_tree())
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "order_status"
        ]

        assert built == ["OrderStatus.CANCELED"]

    def test_no_fill_status_is_named_anywhere_in_the_module(self):
        named = {
            node.attr
            for node in ast.walk(_module_tree())
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            if node.value.id == "OrderStatus"
        }

        assert named == {"CANCELED"}


# --- the watch: debounce, cadence, skip (D-E, AC #1, #3, #5) -------------------------------


class TestDebounce:
    def test_seen_absent_once_nothing_is_written(self):
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(_Reader()), _Clock()

        with capture_logs() as logs:
            _cycles(watch, node, clock, 1)

        assert node.kernel.exec_engine.reports == []
        assert _status(node, "O-1") == "ACCEPTED"
        assert not _events(logs, CLEARED_EVENT)

    def test_seen_absent_twice_a_debounce_apart_it_is_cleared(self):
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(_Reader()), _Clock()

        with capture_logs() as logs:
            _cycles(watch, node, clock, 2)

        assert _status(node, "O-1") == "CANCELED"
        (report,) = node.kernel.exec_engine.reports
        assert report.order_status == OrderStatus.CANCELED
        assert len(_events(logs, CLEARED_EVENT)) == 1

    def test_twice_but_closer_than_the_debounce_nothing_is_written(self):
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(_Reader()), _Clock()

        _cycles(watch, node, clock, 2, gap=STALE_ORDER_DEBOUNCE_SECONDS - 1)

        assert node.kernel.exec_engine.reports == []

    def test_listed_in_between_the_count_starts_over(self):
        node, reader, clock = _node(_order("O-1")), _Reader(), _Clock()
        watch = StaleOrderWatch(reader)

        _cycles(watch, node, clock, 1)
        reader.answer = BrokerOpenOrders(frozenset({"O-1"}), frozenset())
        _cycles(watch, node, clock, 1)
        reader.answer = BrokerOpenOrders(frozenset(), frozenset())
        _cycles(watch, node, clock, 1)

        assert node.kernel.exec_engine.reports == []
        _cycles(watch, node, clock, 1)
        assert _status(node, "O-1") == "CANCELED"

    def test_an_order_the_broker_lists_is_never_touched(self):
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(_Reader("O-1")), _Clock()

        _cycles(watch, node, clock, 5)

        assert node.kernel.exec_engine.reports == []
        assert _status(node, "O-1") == "ACCEPTED"

    def test_an_inconclusive_read_confirms_nothing_and_resets_nothing(self):
        node, reader, clock = _node(_order("O-1")), _Reader(), _Clock()
        watch = StaleOrderWatch(reader)

        _cycles(watch, node, clock, 1)
        reader.answer = Inconclusive("timed_out")
        _cycles(watch, node, clock, 3)
        assert node.kernel.exec_engine.reports == [], "an inconclusive read confirmed"

        reader.answer = BrokerOpenOrders(frozenset(), frozenset())
        _cycles(watch, node, clock, 1)
        assert _status(node, "O-1") == "CANCELED", "the inconclusive reads restarted the count"


class TestForget:
    def test_a_sighting_before_forget_does_not_count_after_it(self):
        """A connection loss: an absence seen before it must be seen twice again."""
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(_Reader()), _Clock()

        _cycles(watch, node, clock, 1)
        watch.forget()
        _cycles(watch, node, clock, 1)

        assert node.kernel.exec_engine.reports == []
        _cycles(watch, node, clock, 1)
        assert _status(node, "O-1") == "CANCELED", "premise: the count does restart"


class TestNoCandidateNoBrokerRequest:
    """AC #3."""

    def test_nothing_to_check_the_broker_is_never_asked(self):
        reader, clock = _Reader(), _Clock()
        node = _node(_order("O-SUB", status="SUBMITTED"), _order("O-DONE", status="CANCELED"))

        _cycles(StaleOrderWatch(reader), node, clock, 4)

        assert reader.calls == 0

    def test_premise_a_candidate_is_asked_about_once_per_cycle(self):
        reader, clock = _Reader(), _Clock()
        node = _node(_order("O-1"), _order("O-2"))

        _cycles(StaleOrderWatch(reader), node, clock, 1)

        assert reader.calls == 1


class TestAnInstrumentWithAPositionIssueIsSkipped:
    """AC #5: order hygiene never runs ahead of position correctness."""

    def test_only_the_clean_instruments_order_is_cleared(self):
        node = _node(_order("O-NVDA", instrument=NVDA), _order("O-AAPL", instrument=AAPL))
        watch, clock = StaleOrderWatch(_Reader()), _Clock()

        _cycles(watch, node, clock, 3, skip=frozenset({str(NVDA.id)}))

        assert _status(node, "O-NVDA") == "ACCEPTED"
        assert _status(node, "O-AAPL") == "CANCELED"

    def test_every_candidate_skipped_the_broker_is_not_asked(self):
        reader = _Reader()
        node = _node(_order("O-NVDA", instrument=NVDA))

        _cycles(StaleOrderWatch(reader), node, _Clock(), 3, skip=frozenset({str(NVDA.id)}))

        assert reader.calls == 0

    def test_a_skipped_cycle_restarts_the_count(self):
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(_Reader()), _Clock()

        _cycles(watch, node, clock, 1)
        _cycles(watch, node, clock, 1, skip=frozenset({str(AAPL.id)}))
        _cycles(watch, node, clock, 1)

        assert node.kernel.exec_engine.reports == []


# --- containment and the re-read (AC #2, D-E) --------------------------------------------


class TestContainment:
    @pytest.mark.parametrize("exc", [ValueError("x"), TimeoutError(), Exception()])
    def test_a_read_that_raises_never_leaves_the_check(self, exc):
        reader = _Reader()
        reader.raises = exc
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(reader), _Clock()

        with capture_logs() as logs:
            _cycles(watch, node, clock, 3)

        assert node.kernel.exec_engine.reports == []
        (record,) = _events(logs, CHECK_FAILED_EVENT)
        assert record["reason"] == type(exc).__name__

    def test_premise_the_reader_itself_does_raise(self):
        reader = _Reader()
        reader.raises = ValueError("x")

        with pytest.raises(ValueError):
            asyncio.run(reader(None))

    def test_one_check_failed_record_per_streak(self):
        reader = _Reader()
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(reader), _Clock()

        with capture_logs() as logs:
            reader.answer = Inconclusive("timed_out")
            _cycles(watch, node, clock, 3)
            reader.answer = BrokerOpenOrders(frozenset({"O-1"}), frozenset())
            _cycles(watch, node, clock, 1)
            reader.answer = Inconclusive("cancelled")
            _cycles(watch, node, clock, 2)

        assert [r["reason"] for r in _events(logs, CHECK_FAILED_EVENT)] == [
            "timed_out",
            "cancelled",
        ]

    def test_a_streak_ended_by_an_empty_candidate_set_lets_the_next_one_log(self):
        """A failure streak ends when there is nothing left to ask about, not
        only on a conclusive read: the next streak must not be silent."""
        reader = _Reader()
        reader.answer = Inconclusive("timed_out")
        order = _order("O-1")
        node, watch, clock = _node(order), StaleOrderWatch(reader), _Clock()

        with capture_logs() as logs:
            _cycles(watch, node, clock, 2)
            order.apply(TestEventStubs.order_canceled(order))  # closed by the strategy
            _cycles(watch, node, clock, 1)
            node.cache.orders[ClientOrderId("O-2")] = _order("O-2")
            reader.answer = Inconclusive("cancelled")
            _cycles(watch, node, clock, 2)

        assert [r["reason"] for r in _events(logs, CHECK_FAILED_EVENT)] == [
            "timed_out",
            "cancelled",
        ]

    def test_a_raise_after_the_read_never_leaves_the_check(self):
        """``check`` never raises — including in the confirm and clear steps
        that run after a conclusive read."""
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(_Reader()), _Clock()
        _cycles(watch, node, clock, 1)
        node.cache.lookup_raises = RuntimeError(f"no {RAW_ACCOUNT}")

        with capture_logs() as logs:
            _cycles(watch, node, clock, 1)

        (record,) = _events(logs, CHECK_FAILED_EVENT)
        assert record["reason"] == "RuntimeError"
        assert RAW_ACCOUNT not in repr(logs)

    def test_the_failure_record_never_carries_the_exceptions_text(self):
        """NFR26: an adapter message may carry the raw account."""
        reader = _Reader()
        reader.raises = ValueError(f"order for {RAW_ACCOUNT} rejected")
        node, watch, clock = _node(_order("O-1")), StaleOrderWatch(reader), _Clock()

        with capture_logs() as logs:
            _cycles(watch, node, clock, 1)

        (record,) = _events(logs, CHECK_FAILED_EVENT)
        assert record["reason"] == "ValueError"
        assert RAW_ACCOUNT not in repr(logs)


class TestTheClearIsLoggedOnlyOnceItTook:
    def _cleared(self, node, **engine):
        for name, value in engine.items():
            setattr(node.kernel.exec_engine, name, value)
        with capture_logs() as logs:
            _cycles(StaleOrderWatch(_Reader()), node, _Clock(), 2)
        return logs

    def test_the_cleared_record_names_the_order_and_never_a_fill(self):
        logs = self._cleared(_node(_order("O-1")))

        (record,) = _events(logs, CLEARED_EVENT)
        assert record["log_level"] == "warning"
        assert {
            key: record[key]
            for key in ("scope", "instrument_id", "client_order_id", "side", "quantity", "detail")
        } == {
            "scope": SCOPE,
            "instrument_id": str(AAPL.id),
            "client_order_id": "O-1",
            "side": "BUY",
            "quantity": "10",
            "detail": UNDETERMINED,
        }
        assert not {"price", "avg_px", "last_px", "commission"} & set(record)
        assert RAW_ACCOUNT not in repr(record)

    @pytest.mark.parametrize(
        ("engine", "reason"),
        [
            ({"returns": False}, "returned_false"),
            ({"raises": RuntimeError(f"no {RAW_ACCOUNT}")}, "RuntimeError"),
            ({"applies": False}, "not_canceled"),
        ],
    )
    def test_a_refusal_or_a_clear_that_did_not_take_is_named_and_contained(self, engine, reason):
        node = _node(_order("O-1"))

        logs = self._cleared(node, **engine)

        assert not _events(logs, CLEARED_EVENT)
        (record,) = _events(logs, CLEAR_FAILED_EVENT)
        assert (record["client_order_id"], record["reason"]) == ("O-1", reason)
        assert RAW_ACCOUNT not in repr(logs)

    def test_a_refusal_is_retried_every_cycle_and_logged_once_per_reason(self):
        node, clock = _node(_order("O-1")), _Clock()
        engine = node.kernel.exec_engine
        engine.returns, engine.applies = False, False
        watch = StaleOrderWatch(_Reader())

        with capture_logs() as logs:
            _cycles(watch, node, clock, 5)
            engine.returns, engine.raises = True, RuntimeError("x")
            _cycles(watch, node, clock, 2)

        assert len(engine.reports) == 6, "premise: every confirmed cycle retried the clear"
        assert [r["reason"] for r in _events(logs, CLEAR_FAILED_EVENT)] == [
            "returned_false",
            "RuntimeError",
        ]

    def test_a_refusal_streak_that_ends_lets_the_next_one_log(self):
        reader = _Reader()
        node, clock = _node(_order("O-1")), _Clock()
        node.kernel.exec_engine.returns = False
        node.kernel.exec_engine.applies = False
        watch = StaleOrderWatch(reader)

        with capture_logs() as logs:
            _cycles(watch, node, clock, 3)
            reader.answer = BrokerOpenOrders(frozenset({"O-1"}), frozenset())
            _cycles(watch, node, clock, 1)
            reader.answer = BrokerOpenOrders(frozenset(), frozenset())
            _cycles(watch, node, clock, 2)

        assert len(_events(logs, CLEAR_FAILED_EVENT)) == 2

    @pytest.mark.parametrize("status", ["PENDING_CANCEL", "PENDING_UPDATE"])
    def test_an_order_the_strategy_put_in_flight_meanwhile_is_left_alone(self, status):
        """A cancel or modify sent during the read belongs to Nautilus's own
        sweep: a synthetic ``CANCELED`` would land before IBKR's own answer."""
        order = _order("O-1")
        node, clock = _node(order), _Clock()
        watch = StaleOrderWatch(_Reader())
        _cycles(watch, node, clock, 1)

        async def _in_flight_meanwhile(node_):
            stub = getattr(TestEventStubs, f"order_{status.lower()}")
            order.apply(stub(order))
            return BrokerOpenOrders(frozenset(), frozenset())

        watch._read = _in_flight_meanwhile
        with capture_logs() as logs:
            _cycles(watch, node, clock, 1)

        assert node.kernel.exec_engine.reports == []
        assert _status(node, "O-1") == status
        assert not _events(logs, CLEAR_FAILED_EVENT)

    def test_an_order_closed_meanwhile_is_left_alone(self):
        """A fill can land between the read and the clear; nothing to clear."""
        order = _order("O-1")
        node, reader, clock = _node(order), _Reader(), _Clock()
        watch = StaleOrderWatch(reader)
        _cycles(watch, node, clock, 1)

        async def _fills_meanwhile(node_):
            order.apply(TestEventStubs.order_filled(order, AAPL, account_id=ACCOUNT))
            return BrokerOpenOrders(frozenset(), frozenset())

        watch._read = _fills_meanwhile
        with capture_logs() as logs:
            _cycles(watch, node, clock, 1)

        assert node.kernel.exec_engine.reports == []
        assert not _events(logs, CLEAR_FAILED_EVENT)


# --- the tick's budget (code review 2026-10-06) ---------------------------------------------


def test_a_runtime_tick_with_both_reads_timing_out_still_stamps_inside_the_staleness_window():
    """The heartbeat stamps activity once per tick, then runs the cycle: the
    broker-state read and this read can both run to their deadlines before the
    next stamp. That gap must stay inside the window that makes a session
    reclaimable (NFR6) — with margin, at the defaults."""
    from src.core.live_broker_state import DEFAULT_BROKER_STATE_TIMEOUT_SECONDS
    from src.models.session import DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    from src.services.session_service import DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS

    worst_gap = (
        DEFAULT_HEARTBEAT_INTERVAL_SECONDS
        + DEFAULT_BROKER_STATE_TIMEOUT_SECONDS
        + OPEN_ORDERS_DEADLINE_SECONDS
    )

    assert worst_gap <= DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS - DEFAULT_HEARTBEAT_INTERVAL_SECONDS


# --- records (D-F) ----------------------------------------------------------------------------


def _emitted_names_in_source() -> set[str]:
    """Every record name the module passes to ``_emit``, resolved from its constants."""
    tree = _module_tree()
    constants = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_emit":
            event = node.args[2]
            assert isinstance(event, ast.Name), f"_emit with a non-constant name: {ast.dump(event)}"
            names.add(constants[event.id])
    return names


class TestEveryEmittedStaleOrderRecordIsPinned:
    """CLAUDE.md "Membership-pinned lists": pinned against the code, both ways."""

    def test_the_names_the_source_emits_are_exactly_the_constant(self):
        assert _emitted_names_in_source() == set(EMITTED_STALE_ORDER_EVENTS)

    def test_driving_every_path_emits_exactly_the_constant(self):
        seen: set[str] = set()
        with capture_logs() as logs:
            _cycles(StaleOrderWatch(_Reader()), _node(_order("O-1")), _Clock(), 2)
            node = _node(_order("O-2"))
            node.kernel.exec_engine.returns = False
            _cycles(StaleOrderWatch(_Reader()), node, _Clock(), 2)
            reader = _Reader()
            reader.answer = Inconclusive("timed_out")
            _cycles(StaleOrderWatch(reader), _node(_order("O-3")), _Clock(), 1)
        seen = {record["event"] for record in logs}

        assert seen == set(EMITTED_STALE_ORDER_EVENTS)

    def test_the_constant_is_the_three_names(self):
        assert EMITTED_STALE_ORDER_EVENTS == (
            "reconcile.stale_order_cleared",
            "reconcile.stale_order_clear_failed",
            "reconcile.stale_order_check_failed",
        )


class TestNoOrderMethodIsCalled:
    """F10: the clear is a report to the engine, never an order method. No
    glob-driven scan covers ``live_*`` modules for these names (measured by
    mutation), so this module carries its own, with the stop-path constant."""

    def test_no_forbidden_order_method_is_called(self):
        from tests.unit.core.test_live_stop_path_is_inert import (
            FORBIDDEN_ORDER_METHODS,
            _called_names,
        )

        called = _called_names(Path(live_stranded_orders.__file__).read_text(encoding="utf-8"))

        assert "reconcile_execution_report" in called, "premise: the scan sees this module's calls"
        assert called & FORBIDDEN_ORDER_METHODS == set()
