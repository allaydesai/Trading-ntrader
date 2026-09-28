"""Component tests proving NFR6: a working order is queried, never resent
(Story 3.4, AC #1/#3/#4).

Component tier: constructs a real ``LiveExecutionEngine``/``LiveRiskEngine``/
``Trader`` and a real materialised ``sma_crossover`` strategy, with
``MockLiveExecutionClient`` (or a thin subclass of it) standing in for the
broker — never a ``TradingNode``, which is the only thing in this stack that
touches Nautilus C logging (measured, and the same C-logging guard every
sibling live suite copies).

**Two measured corrections to the story's stated design** (Task 1, recorded
in full in the Dev Agent Record):

1. ``MockLiveExecutionClient.query_order`` is a pure call-recorder — it never
   reaches ``generate_order_status_report`` (the mock overrides ``query_order``
   itself, short-circuiting the base ``LiveExecutionClient.query_order``'s
   ``create_task(self._query_order(...))``). So the timeout path's assertion
   is ``"query_order" in client.calls`` (the mock's own visible entry point),
   not ``"generate_order_status_report"``. The reconnect path, which DOES need
   a real ``OrderStatusReport`` to come back, uses :class:`_AnsweringClient`
   below, which overrides ``query_order`` to actually drive the base client's
   real async resolution chain.
2. ``strategy.submit_order(restored_order)`` on the literal restored object
   does not produce ``OrderDenied`` — it raises ``ValueError`` at Nautilus's
   own ``Condition.is_true(order.status_c() == OrderStatus.INITIALIZED, ...)``
   precondition (``trading/strategy.pyx:794-796``), which runs *before* the
   duplicate-``client_order_id`` check that follows it
   (``trading/strategy.pyx:806-808``) — a restored order is ``ACCEPTED``, not
   ``INITIALIZED``, so it can never reach ``submit_order`` again by
   definition. The scenario AC #3 actually describes — a resumed strategy
   attempting to place a lookalike order without checking ``orders_open``
   first — is a **new** ``INITIALIZED`` order carrying the same
   ``client_order_id``, which *does* reach the duplicate check and is denied.
   :func:`_duplicate_of` builds exactly that.
"""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
import structlog
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common import Environment
from nautilus_trader.common.component import (
    LiveClock,
    MessageBus,
    TestClock,
    is_logging_initialized,
)
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.engine import DataEngine, DataEngineConfig
from nautilus_trader.execution.messages import GenerateOrderStatusReport
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.live.config import LiveExecEngineConfig
from nautilus_trader.live.execution_engine import LiveExecutionEngine
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OrderSide, OrderStatus, OrderType, TimeInForce
from nautilus_trader.model.identifiers import ClientId, TraderId, VenueOrderId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.risk.engine import RiskEngine, RiskEngineConfig
from nautilus_trader.test_kit.mocks.cache_database import MockCacheDatabase
from nautilus_trader.test_kit.mocks.exec_clients import MockLiveExecutionClient
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.trading.trader import Trader
from structlog.testing import capture_logs

from src.core.live_order_path import ORDER_EVENTS_TOPIC, OrderEventObserver, install_order_path
from src.core.live_session_node import materialise_strategy
from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from src.models.session import StrategySpec

pytestmark = pytest.mark.component

AAPL_EQUITY = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
TRADER_ID = TraderId("PAPER-0e8f1c2a")
SMA_SPEC = StrategySpec(
    strategy_id="sma_crossover",
    parameters={"fast_period": 2, "slow_period": 3},
    bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
)
#: The threshold and retry budget measured off the installed wheel (Task
#: 1.4) — passed explicitly here so the test is independent of whatever
#: Task 5 ends up naming as the builder's own constants.
INFLIGHT_THRESHOLD_MS = 5_000
INFLIGHT_RETRIES = 5


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_live_order_path.py:91-100``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — this file must never construct a real "
        "TradingNode."
    )


@pytest.fixture(autouse=True)
def _a_current_event_loop():
    """Test isolation (added by Story 4.7): ``_harness`` and two scenarios read
    the *implicit* current loop, and the Story 4.2/4.3/4.7 real-engine harnesses
    clear it on close (``asyncio.set_event_loop(None)``). Under ``-n auto`` this
    file failed with "There is no current event loop" whenever one of those
    files ran first on the same worker — reproduced with Story 4.2's
    ``test_live_startup_reconcile_engine.py`` alone ahead of it; Story 4.7's
    engine file is one more such predecessor."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()
    asyncio.set_event_loop(None)


class _AnsweringClient(MockLiveExecutionClient):
    """A ``MockLiveExecutionClient`` whose ``query_order`` actually reaches
    ``generate_order_status_report`` (measured correction #1 above), keyed
    by ``client_order_id`` rather than ``venue_order_id`` — a ``SUBMITTED``
    order's ``venue_order_id`` is hardcoded ``None``
    (``model/events/order.pyx:1534-1543``, Task 1.2), and this is what the IB
    adapter actually keys its own reconciliation on (``orderRef ==
    client_order_id.value``, ``execution.py:274-284``).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reports_by_client_order_id: dict[Any, OrderStatusReport] = {}

    def query_order(self, command: Any) -> None:
        self.calls.append("query_order")
        self.commands.append(command)
        self.create_task(self._query_order(command), log_msg="query_order (test double)")

    async def generate_order_status_report(
        self,
        command: GenerateOrderStatusReport,
    ) -> OrderStatusReport | None:
        self.calls.append("generate_order_status_report")
        return self.reports_by_client_order_id.get(command.client_order_id)


def _harness(
    clock: TestClock | LiveClock,
    *,
    client_factory: Callable[..., MockLiveExecutionClient] = MockLiveExecutionClient,
    cache_database: Any = None,
) -> dict[str, Any]:
    """The 3.4 live-engine harness: a real message bus, cache, portfolio,
    data/risk/exec engines and ``Trader`` — never a ``TradingNode``. Extends
    ``test_client_order_id_determinism.py``'s ``_new_trader`` shape (Task
    3.1) with the pieces that shape lacks: a real ``LiveExecutionEngine``
    (for the in-flight sweep) and a registered client.
    """
    loop = asyncio.get_event_loop()
    msgbus = MessageBus(trader_id=TRADER_ID, clock=clock)
    cache = Cache(database=cache_database)
    cache.add_instrument(AAPL_EQUITY)
    portfolio = Portfolio(msgbus, cache, clock)
    data_engine = DataEngine(msgbus=msgbus, cache=cache, clock=clock, config=DataEngineConfig())
    risk_engine = RiskEngine(
        portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock, config=RiskEngineConfig()
    )
    exec_engine = LiveExecutionEngine(
        loop=loop,
        msgbus=msgbus,
        cache=cache,
        clock=clock,
        config=LiveExecEngineConfig(
            inflight_check_threshold_ms=INFLIGHT_THRESHOLD_MS,
            inflight_check_retries=INFLIGHT_RETRIES,
        ),
    )
    client = client_factory(
        loop=loop,
        client_id=ClientId("IB"),
        venue=None,  # multi-venue default routing — AAPL.NASDAQ routes through it
        account_type=AccountType.CASH,
        base_currency=USD,
        instrument_provider=InstrumentProvider(),
        msgbus=msgbus,
        cache=cache,
        clock=clock,
    )
    exec_engine.register_client(client)
    trader = Trader(
        trader_id=TRADER_ID,
        instance_id=UUID4(),
        msgbus=msgbus,
        cache=cache,
        portfolio=portfolio,
        data_engine=data_engine,
        risk_engine=risk_engine,
        exec_engine=exec_engine,
        clock=clock,
        environment=Environment.BACKTEST,
    )
    return {
        "msgbus": msgbus,
        "cache": cache,
        "portfolio": portfolio,
        "risk_engine": risk_engine,
        "exec_engine": exec_engine,
        "client": client,
        "trader": trader,
    }


async def _start(harness: dict[str, Any]) -> None:
    harness["risk_engine"].start()
    harness["exec_engine"].start()

    await asyncio.sleep(0)


async def _process(exec_engine: LiveExecutionEngine, event: Any) -> None:
    """``LiveExecutionEngine.process()`` enqueues onto ``_evt_queue`` rather
    than applying synchronously (measured: it delegates to
    ``ThrottledEnqueuer.enqueue``, drained by the ``_run_evt_queue`` task
    started in ``_on_start``) — so, exactly like ``submit_order``, it needs
    event-loop ticks after the call before the order's state actually
    changes.
    """
    exec_engine.process(event)
    for _ in range(3):
        await asyncio.sleep(0)


def _duplicate_of(restored, strategy_id) -> MarketOrder:
    """A **new** ``INITIALIZED`` order carrying the restored order's
    ``client_order_id`` — the shape that actually reaches
    ``Strategy.submit_order``'s duplicate check (measured correction #2
    above). Re-submitting ``restored`` itself raises ``ValueError`` instead.
    """
    return MarketOrder(
        trader_id=TRADER_ID,
        strategy_id=strategy_id,
        instrument_id=restored.instrument_id,
        client_order_id=restored.client_order_id,
        order_side=restored.side,
        quantity=restored.quantity,
        init_id=UUID4(),
        ts_init=0,
    )


class TestTheTimeoutPathQueriesAndNeverResubmits:
    """AC #1 / AC #4's timeout path: the venue never answers, and the
    engine resolves the order locally instead of resending it.
    """

    async def test_one_submit_a_venue_query_and_a_local_rejection_after_retries_exhaust(self):
        clock = TestClock()
        harness = _harness(clock)
        client = harness["client"]
        strategy = materialise_strategy(SMA_SPEC)
        harness["trader"].add_strategy(strategy)
        await _start(harness)

        order = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.BUY, quantity=Quantity.from_int(10)
        )
        strategy.submit_order(order)
        for _ in range(3):
            await asyncio.sleep(0)
        assert client.calls.count("submit_order") == 1

        # The ack is "lost": only OrderSubmitted is delivered, never OrderAccepted.
        await _process(harness["exec_engine"], TestEventStubs.order_submitted(order))
        assert order.status_string() == "SUBMITTED"
        after_first_sweep_index = len(client.calls)

        clock.set_time(order.last_event.ts_event + (INFLIGHT_THRESHOLD_MS + 1_000) * 1_000_000)
        await harness["exec_engine"]._check_inflight_orders()
        # Measured correction #1: the mock's query_order is the visible venue
        # ask — generate_order_status_report is never reached by the stock mock.
        assert "query_order" in client.calls
        assert order.status_string() == "SUBMITTED"
        assert client.calls.count("submit_order") == 1

        for _ in range(INFLIGHT_RETRIES):
            await harness["exec_engine"]._check_inflight_orders()

        assert order.status_string() == "REJECTED"
        assert type(order.last_event).__name__ == "OrderRejected"
        assert order.last_event.reason == "UNKNOWN"
        assert order.last_event.reconciliation is True
        assert client.calls.count("submit_order") == 1, "the order must never be resubmitted"
        assert "submit_order" not in client.calls[after_first_sweep_index:], (
            "no resubmit-then-cancel mutation may hide behind a total count"
        )

    async def test_the_observer_logs_exactly_one_order_rejected_with_the_venue_reason(self):
        clock = TestClock()
        harness = _harness(clock)
        strategy = materialise_strategy(SMA_SPEC)
        harness["trader"].add_strategy(strategy)
        observer = OrderEventObserver(structlog.get_logger("test"))
        harness["msgbus"].subscribe(topic=ORDER_EVENTS_TOPIC, handler=observer.handle_order_event)
        await _start(harness)

        order = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.BUY, quantity=Quantity.from_int(10)
        )
        strategy.submit_order(order)
        for _ in range(3):
            await asyncio.sleep(0)
        await _process(harness["exec_engine"], TestEventStubs.order_submitted(order))
        clock.set_time(order.last_event.ts_event + (INFLIGHT_THRESHOLD_MS + 1_000) * 1_000_000)

        with capture_logs() as logs:
            for _ in range(INFLIGHT_RETRIES + 1):
                await harness["exec_engine"]._check_inflight_orders()

        rejected = [entry for entry in logs if entry["event"] == "order.rejected"]
        assert len(rejected) == 1
        assert rejected[0]["venue_reason"] == "UNKNOWN"
        assert rejected[0]["reconciliation"] is True


class TestTheReconnectPathQueriesAndNeverResubmits:
    """AC #1 / AC #4's reconnect path: the venue DOES answer, and the
    order's own cached state resolves to ACCEPTED without a second submit.
    """

    async def test_one_sweep_resolves_to_accepted_with_one_submit_total(self):
        clock = TestClock()
        harness = _harness(clock, client_factory=_AnsweringClient)
        client = harness["client"]
        strategy = materialise_strategy(SMA_SPEC)
        harness["trader"].add_strategy(strategy)
        await _start(harness)

        order = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.BUY, quantity=Quantity.from_int(10)
        )
        strategy.submit_order(order)
        for _ in range(3):
            await asyncio.sleep(0)
        await _process(harness["exec_engine"], TestEventStubs.order_submitted(order))
        assert client.calls.count("submit_order") == 1

        client.reports_by_client_order_id[order.client_order_id] = OrderStatusReport(
            account_id=client.account_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("1"),
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            order_status=OrderStatus.ACCEPTED,
            quantity=order.quantity,
            filled_qty=Quantity.from_int(0),
            report_id=UUID4(),
            ts_accepted=clock.timestamp_ns(),
            ts_last=clock.timestamp_ns(),
            ts_init=clock.timestamp_ns(),
        )

        clock.set_time(order.last_event.ts_event + (INFLIGHT_THRESHOLD_MS + 1_000) * 1_000_000)
        await harness["exec_engine"]._check_inflight_orders()
        for _ in range(5):
            await asyncio.sleep(0)

        assert "generate_order_status_report" in client.calls
        assert order.status_string() == "ACCEPTED"
        assert order in harness["cache"].orders_open()
        assert order.client_order_id not in harness["exec_engine"]._inflight_check_retries
        assert client.calls.count("submit_order") == 1

    async def test_a_fresh_signal_may_submit_a_distinct_new_order(self):
        clock = TestClock()
        harness = _harness(clock, client_factory=_AnsweringClient)
        client = harness["client"]
        strategy = materialise_strategy(SMA_SPEC)
        harness["trader"].add_strategy(strategy)
        await _start(harness)

        first = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.BUY, quantity=Quantity.from_int(10)
        )
        strategy.submit_order(first)
        for _ in range(3):
            await asyncio.sleep(0)
        await _process(harness["exec_engine"], TestEventStubs.order_submitted(first))
        client.reports_by_client_order_id[first.client_order_id] = OrderStatusReport(
            account_id=client.account_id,
            instrument_id=first.instrument_id,
            client_order_id=first.client_order_id,
            venue_order_id=VenueOrderId("1"),
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            order_status=OrderStatus.ACCEPTED,
            quantity=first.quantity,
            filled_qty=Quantity.from_int(0),
            report_id=UUID4(),
            ts_accepted=clock.timestamp_ns(),
            ts_last=clock.timestamp_ns(),
            ts_init=clock.timestamp_ns(),
        )
        clock.set_time(first.last_event.ts_event + (INFLIGHT_THRESHOLD_MS + 1_000) * 1_000_000)
        await harness["exec_engine"]._check_inflight_orders()
        for _ in range(5):
            await asyncio.sleep(0)

        second = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.SELL, quantity=Quantity.from_int(5)
        )
        strategy.submit_order(second)
        for _ in range(3):
            await asyncio.sleep(0)

        assert second.client_order_id != first.client_order_id
        assert client.calls.count("submit_order") == 2

    async def test_a_canceled_report_resolves_without_a_resubmit(self):
        """Task 9's M9: the third answer the venue can give besides
        "still working" and "silence" — it can say the order never made it
        (CANCELED). A resubmit-after-cancel mutation would show up as a
        second ``submit_order`` call here; there must never be one.
        """
        clock = TestClock()
        harness = _harness(clock, client_factory=_AnsweringClient)
        client = harness["client"]
        strategy = materialise_strategy(SMA_SPEC)
        harness["trader"].add_strategy(strategy)
        await _start(harness)

        order = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.BUY, quantity=Quantity.from_int(10)
        )
        strategy.submit_order(order)
        for _ in range(3):
            await asyncio.sleep(0)
        await _process(harness["exec_engine"], TestEventStubs.order_submitted(order))
        assert client.calls.count("submit_order") == 1

        client.reports_by_client_order_id[order.client_order_id] = OrderStatusReport(
            account_id=client.account_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("1"),
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            order_status=OrderStatus.CANCELED,
            quantity=order.quantity,
            filled_qty=Quantity.from_int(0),
            report_id=UUID4(),
            ts_accepted=clock.timestamp_ns(),
            ts_last=clock.timestamp_ns(),
            ts_init=clock.timestamp_ns(),
        )

        clock.set_time(order.last_event.ts_event + (INFLIGHT_THRESHOLD_MS + 1_000) * 1_000_000)
        await harness["exec_engine"]._check_inflight_orders()
        for _ in range(5):
            await asyncio.sleep(0)

        assert order.status_string() == "CANCELED"
        assert type(order.last_event).__name__ == "OrderCanceled"
        assert order not in harness["cache"].orders_open()
        assert client.calls.count("submit_order") == 1, (
            "a canceled report must never trigger a resubmit"
        )


class TestASuppressedCallIsNeverReplayed:
    """AC #4's third named path folded in here: the suppression wrapper has
    no queue, no timer, no replay — flipping ``withheld`` back to ``False``
    must never resend the call it swallowed, not immediately AND not on the
    next legitimate call either.

    Task 9's mutation sweep (M8) found the second half was the load-bearing
    one: a "queue-and-replay-on-next-call" mutation left the original,
    static-only version of this test green, because it never drove a call
    *after* flipping the flag — a replay wired to fire on the next
    invocation has nothing to trigger it. :meth:`test_a_later_call_does_not
    _also_resend_the_earlier_suppressed_one` is what actually catches it.
    """

    def _new_strategy(self):
        clock = LiveClock()
        trader_id = TraderId("TESTER-000")
        msgbus = MessageBus(trader_id=trader_id, clock=clock)
        cache = Cache(database=None)
        cache.add_instrument(AAPL_EQUITY)
        portfolio = Portfolio(msgbus, cache, clock)
        strategy = materialise_strategy(SMA_SPEC)
        strategy.register(trader_id, portfolio, msgbus, cache, clock)
        return strategy

    class _StubMonitor:
        def __init__(self) -> None:
            self.submission_withheld = True

    def test_flipping_withheld_to_false_does_not_immediately_replay(self):
        strategy = self._new_strategy()
        monitor = self._StubMonitor()
        install_order_path(strategy, monitor, structlog.get_logger("test"))
        strategy.start()

        order = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.BUY, quantity=Quantity.from_int(10)
        )
        with capture_logs() as logs:
            strategy.submit_order(order)

        assert list(strategy.cache.orders()) == []
        suppressed = [entry for entry in logs if entry["event"] == "order.suppressed"]
        assert len(suppressed) == 1

        monitor.submission_withheld = False
        # Drive nothing else — no call is made that could trigger a replay.

        assert list(strategy.cache.orders()) == [], (
            "flipping withheld must not resend the swallowed call"
        )
        strategy.stop()

    def test_a_later_call_does_not_also_resend_the_earlier_suppressed_one(self):
        """The mutation-sweep catch: reconnect, then a fresh strategy signal
        submits a NEW order — the swallowed one must not ride along with it.
        """
        strategy = self._new_strategy()
        monitor = self._StubMonitor()
        install_order_path(strategy, monitor, structlog.get_logger("test"))
        strategy.start()

        withheld_order = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.BUY, quantity=Quantity.from_int(10)
        )
        strategy.submit_order(withheld_order)
        assert list(strategy.cache.orders()) == []

        monitor.submission_withheld = False
        fresh_order = strategy.order_factory.market(
            instrument_id=AAPL_EQUITY.id, order_side=OrderSide.SELL, quantity=Quantity.from_int(5)
        )
        strategy.submit_order(fresh_order)

        submitted = list(strategy.cache.orders())
        assert submitted == [fresh_order], (
            f"only the fresh signal may reach the exec layer, got: {submitted}"
        )
        assert withheld_order.client_order_id not in {o.client_order_id for o in submitted}
        strategy.stop()


class TestTheWrapperClosureHasNoQueueAttribute:
    """Two structural pins on ``_wrap``'s own AST subtree, proven directly
    against the module's source (never by inspecting a live closure's
    ``__dict__``, which pure-Python closures do not meaningfully expose):

    1. No list/deque/Queue construction at all — a "remember and resend on
       reconnect" mutation needs somewhere to hold the call.
    2. The ``base(...)`` forwarding call itself is not inside a loop or a
       ``try`` — an in-place retry needs no queue, and Task 9's M5 mutation
       (a bare ``for ...: try: ... except: continue`` around the
       pass-through) is invisible to both the first pin above AND to the
       unit-tier AST scan (``test_order_path_has_no_retry.py``), because
       that scan matches on the literal callee name ``submit_order`` /
       ``submit_order_list`` and the forwarding call here is always the
       generic ``base`` parameter, never a literal name.
    """

    def test_wrap_constructs_no_list_deque_or_queue(self):
        import ast
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[3]
        source = (project_root / "src" / "core" / "live_order_path.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        wrap_functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_wrap"
        ]
        assert len(wrap_functions) == 1, "expected exactly one _wrap function definition"

        forbidden_constructors = {"list", "deque", "Queue", "PriorityQueue", "LifoQueue"}
        offenders = []
        for node in ast.walk(wrap_functions[0]):
            if isinstance(node, ast.List | ast.ListComp):
                offenders.append(f"list literal at line {node.lineno}")
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name in forbidden_constructors:
                    offenders.append(f"{name}() call at line {node.lineno}")

        assert offenders == [], f"_wrap must have no queue-like state: {offenders}"

    def test_the_pin_would_catch_a_planted_deque(self):
        import ast

        source = (
            "from collections import deque\n\n\n"
            "def _wrap(method_name, base, strategy, monitor, log):\n"
            "    pending = deque()\n\n"
            "    def wrapped(*args, **kwargs):\n"
            "        pending.append(args)\n"
            "        return base(*args, **kwargs)\n\n"
            "    return wrapped\n"
        )
        tree = ast.parse(source)
        wrap = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_wrap"
        )

        hits = [
            n
            for n in ast.walk(wrap)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "deque"
        ]

        assert hits, "the planted deque() call must be detectable by the same shape the pin checks"

    def _base_call_has_loop_or_try_ancestor(self, wrap_node) -> bool:
        """Does ``wrapped``'s own ``base(...)`` forwarding call sit inside a
        loop or a ``try`` — the shape a bare in-place retry (no queue at
        all) would take? Complements
        :func:`test_wrap_constructs_no_list_deque_or_queue`: a mutation
        found during the Task 9 sweep (M5) wraps the pass-through call in
        ``for ...: try: ... except Exception: continue`` with no state to
        hold — invisible to the queue-shaped pin, and invisible to the
        unit-tier AST scan too, because the callee there is the generic
        ``base`` parameter, never the literal name ``submit_order`` that
        scan matches on. This is the guard that actually covers it.
        """
        import ast

        parents: dict = {}
        for parent in ast.walk(wrap_node):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        wrapped_fn = next(
            n for n in ast.walk(wrap_node) if isinstance(n, ast.FunctionDef) and n.name == "wrapped"
        )
        for node in ast.walk(wrapped_fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "base"
            ):
                current = parents.get(node)
                while current is not None and current is not wrapped_fn:
                    if isinstance(current, ast.For | ast.AsyncFor | ast.While | ast.Try):
                        return True
                    current = parents.get(current)
        return False

    def test_the_base_forwarding_call_is_not_inside_a_loop_or_try(self):
        import ast
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[3]
        source = (project_root / "src" / "core" / "live_order_path.py").read_text(encoding="utf-8")
        wrap = next(
            n
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.FunctionDef) and n.name == "_wrap"
        )

        assert not self._base_call_has_loop_or_try_ancestor(wrap), (
            "the pass-through call must never sit inside a loop or a try/except — "
            "that shape is an in-place retry with no queue at all"
        )

    def test_the_pin_would_catch_a_planted_in_place_retry_loop(self):
        """Task 9's M5 mutation, applied to the real module source: wrap the
        forwarding call in a bare retry loop and confirm THIS pin (not the
        queue-shaped one, which cannot see it) goes red.
        """
        import ast
        from pathlib import Path

        project_root = Path(__file__).resolve().parents[3]
        source = (project_root / "src" / "core" / "live_order_path.py").read_text(encoding="utf-8")
        mutated = source.replace(
            "        if withheld:\n            return None\n        return base(*args, **kwargs)",
            "        if withheld:\n            return None\n"
            "        for _ in range(3):\n"
            "            try:\n"
            "                return base(*args, **kwargs)\n"
            "            except Exception:\n"
            "                continue",
        )
        assert mutated != source, "the planted shape did not match the real source text"
        wrap = next(
            n
            for n in ast.walk(ast.parse(mutated))
            if isinstance(n, ast.FunctionDef) and n.name == "_wrap"
        )

        assert self._base_call_has_loop_or_try_ancestor(wrap)


class TestARestartedProcessSeesItsWorkingOrderBeforeOnStart:
    """AC #3 / AC #4's restart path: the Redis-restored (here,
    ``MockCacheDatabase``-restored) working order is visible from *inside*
    ``on_start``, before the strategy's own signal logic ever runs — and a
    lookalike resubmission is denied as a duplicate with zero client calls.
    """

    def test_the_restored_order_is_seen_before_on_start_and_a_lookalike_is_denied(self):
        async def scenario() -> dict[str, Any]:
            db = MockCacheDatabase()

            # Process A: submits once, explicit order_id_tag="000" (Task 4.1's
            # own wording — not left to positional auto-assignment).
            harness_a = _harness(TestClock(), cache_database=db)
            config_a = SMAConfig(
                instrument_id=AAPL_EQUITY.id,
                bar_type=materialise_strategy(SMA_SPEC).bar_type,
                fast_period=2,
                slow_period=3,
                order_id_tag="000",
            )
            strategy_a = SMACrossover(config=config_a)
            harness_a["trader"].add_strategy(strategy_a)
            await _start(harness_a)
            order = strategy_a.order_factory.market(
                instrument_id=AAPL_EQUITY.id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(7),
            )
            strategy_a.submit_order(order)
            for _ in range(3):
                await asyncio.sleep(0)
            await _process(harness_a["exec_engine"], TestEventStubs.order_submitted(order))
            await _process(
                harness_a["exec_engine"],
                TestEventStubs.order_accepted(order, venue_order_id=VenueOrderId("1")),
            )
            assert order.status_string() == "ACCEPTED"
            assert harness_a["client"].calls.count("submit_order") == 1

            # Process B: a brand-new engine stack on the SAME database, the
            # kernel's real restore path (load_cache()), then a probe
            # strategy that records what it sees from inside on_start.
            #
            # Measured correction: the probe MUST be registered under the
            # SAME StrategyId as process A ("SMACrossover-000"), not the
            # probe subclass's own name — Nautilus derives the default
            # strategy_id from the *instantiated class*, so an unqualified
            # _ProbeStrategy(SMACrossover) lands on "_ProbeStrategy-000" and
            # the restore is invisible to it. strategy_id="SMACrossover" is
            # what makes cache.orders_open(strategy_id=self.id) match.
            harness_b = _harness(TestClock(), cache_database=db)
            harness_b["exec_engine"].load_cache()
            seen: dict[str, Any] = {}

            class _ProbeStrategy(SMACrossover):
                def on_start(self) -> None:
                    seen["open"] = tuple(self.cache.orders_open(strategy_id=self.id))
                    seen["strategy_id"] = str(self.id)
                    seen["count_before_start"] = self.order_factory.get_client_order_id_count()
                    super().on_start()

            probe_config = SMAConfig(
                instrument_id=AAPL_EQUITY.id,
                bar_type=materialise_strategy(SMA_SPEC).bar_type,
                fast_period=2,
                slow_period=3,
                strategy_id="SMACrossover",
                order_id_tag="000",
            )
            probe = _ProbeStrategy(config=probe_config)
            harness_b["trader"].add_strategy(probe)
            await _start(harness_b)
            probe.start()

            next_order = probe.order_factory.market(
                instrument_id=AAPL_EQUITY.id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(1),
            )

            restored = harness_b["cache"].order(order.client_order_id)
            duplicate = _duplicate_of(restored, probe.id)
            probe.submit_order(duplicate)
            for _ in range(3):
                await asyncio.sleep(0)

            return {
                "seen": seen,
                "next_client_order_id": str(next_order.client_order_id),
                "duplicate_status": duplicate.status_string(),
                "duplicate_reason": getattr(duplicate.last_event, "reason", None),
                "client_calls": harness_b["client"].calls.count("submit_order"),
                "restored_client_order_id": str(restored.client_order_id) if restored else None,
                "restored_status": restored.status_string() if restored else None,
            }

        result = asyncio.get_event_loop().run_until_complete(scenario())

        assert result["seen"]["strategy_id"] == "SMACrossover-000"
        assert len(result["seen"]["open"]) == 1
        assert result["seen"]["open"][0].status_string() == "ACCEPTED"
        assert result["restored_status"] == "ACCEPTED"
        assert result["seen"]["count_before_start"] == 1
        assert result["next_client_order_id"].endswith("-000-2")
        assert result["duplicate_status"] == "DENIED"
        assert "duplicate" in result["duplicate_reason"]
        assert result["client_calls"] == 0, "the resumed strategy must place zero client calls"


class TestTheRestoreGuaranteeIsLoadBearing:
    """Anti-tautology twin (Task 4.2): the SAME probe shape on a **fresh**
    database sees nothing — proving the assertions above can genuinely fail.
    """

    def test_a_fresh_database_shows_no_restored_order(self):
        async def scenario() -> dict[str, Any]:
            harness_b = _harness(TestClock(), cache_database=MockCacheDatabase())
            harness_b["exec_engine"].load_cache()
            seen: dict[str, Any] = {}

            class _ProbeStrategy(SMACrossover):
                def on_start(self) -> None:
                    seen["open"] = tuple(self.cache.orders_open(strategy_id=self.id))
                    seen["count_before_start"] = self.order_factory.get_client_order_id_count()
                    super().on_start()

            probe_config = SMAConfig(
                instrument_id=AAPL_EQUITY.id,
                bar_type=materialise_strategy(SMA_SPEC).bar_type,
                fast_period=2,
                slow_period=3,
                strategy_id="SMACrossover",
                order_id_tag="000",
            )
            probe = _ProbeStrategy(config=probe_config)
            harness_b["trader"].add_strategy(probe)
            await _start(harness_b)
            probe.start()

            next_order = probe.order_factory.market(
                instrument_id=AAPL_EQUITY.id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(1),
            )
            return {"seen": seen, "next_client_order_id": str(next_order.client_order_id)}

        result = asyncio.get_event_loop().run_until_complete(scenario())

        assert result["seen"]["open"] == ()
        assert result["seen"]["count_before_start"] == 0
        assert result["next_client_order_id"].endswith("-000-1")
