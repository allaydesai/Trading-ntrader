"""Component tests for the order-path suppression wrap (Story 3.2, Task 3).

Component tier: constructs a real ``MessageBus``/``Cache``/``Portfolio`` and a
real materialised strategy (``SMACrossover``), but never a ``TradingNode`` —
``MessageBus`` construction alone does not touch Nautilus C logging, the same
measured precedent ``test_session_runner_phases.py``'s dispatch-order test
relies on.
"""

import pytest
import structlog
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus, is_logging_initialized
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OmsType, OrderSide
from nautilus_trader.model.events.order import OrderSubmitted
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    InstrumentId,
    PositionId,
    StrategyId,
    TraderId,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from structlog.testing import capture_logs

from src.core.live_order_path import (
    ORDER_CREATING_METHODS,
    SUBMITTED_EVENT,
    OrderEventObserver,
    install_order_path,
)
from src.core.live_session_node import materialise_strategy
from src.models.session import StrategySpec

pytestmark = pytest.mark.component

AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
BAR_TYPE = BarType.from_str(AAPL_1MIN)

#: Duplicated from `tests/unit/core/test_live_stop_path_is_inert.py:53-62`.
#: Production code cannot import from `tests/`, so the relationship is
#: asserted here instead of shared by reference. Kept as its own literal
#: (not the story's other module-level constant) so a change to either list
#: is a deliberate, visible edit in both files.
_STOP_PATH_FORBIDDEN_ORDER_METHODS = frozenset(
    {
        "close_all_positions",
        "close_position",
        "cancel_all_orders",
        "cancel_order",
        "submit_order",
        "submit_order_list",
    }
)


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_session_runner_phases.py:61-78``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — this file must never construct a real "
        "TradingNode."
    )


class _StubMonitor:
    """The one member `install_order_path` duck-types against."""

    def __init__(self, *, withheld: bool) -> None:
        self.submission_withheld = withheld


def make_bar(index: int, close: str) -> Bar:
    price = Price.from_str(close)
    return Bar(
        bar_type=BAR_TYPE,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_int(1_000),
        ts_event=index * 60_000_000_000,
        ts_init=index * 60_000_000_000,
    )


def _new_strategy():
    clock = LiveClock()
    trader_id = TraderId("TESTER-000")
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    cache = Cache(database=None)
    cache.add_instrument(TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ"))
    portfolio = Portfolio(msgbus, cache, clock)
    strategy_spec = StrategySpec(
        strategy_id="sma_crossover",
        parameters={"fast_period": 2, "slow_period": 3},
        bar_types=(AAPL_1MIN,),
    )
    strategy = materialise_strategy(strategy_spec)
    strategy.register(trader_id, portfolio, msgbus, cache, clock)
    return strategy


#: Drives one bullish crossover at bar index 5 (see Task 1's probe for the
#: arithmetic this sequence is built from).
CLOSES = ["100.00", "95.00", "90.00", "85.00", "90.00", "95.00"]


class TestSuppressionWhenWithheld:
    def test_a_signal_submits_nothing_when_withheld(self):
        strategy = _new_strategy()
        install_order_path(strategy, _StubMonitor(withheld=True), structlog.get_logger("test"))
        strategy.start()

        for index, close in enumerate(CLOSES):
            strategy.on_bar(make_bar(index, close))

        assert list(strategy.cache.orders()) == []
        strategy.stop()

    def test_suppression_logs_one_record_with_the_fields_that_exist(self):
        strategy = _new_strategy()
        monitor = _StubMonitor(withheld=True)
        install_order_path(strategy, monitor, structlog.get_logger("test").bind(session_id="s1"))
        strategy.start()

        with capture_logs() as logs:
            for index, close in enumerate(CLOSES):
                strategy.on_bar(make_bar(index, close))

        suppressed = [entry for entry in logs if entry["event"] == "order.suppressed"]
        assert len(suppressed) == 1
        assert suppressed[0]["session_id"] == "s1"
        assert suppressed[0]["method"] == "submit_order"
        assert suppressed[0]["instrument_id"] == "AAPL.NASDAQ"
        # submit_order's own order object exists at call time -- logged.
        assert "client_order_id" in suppressed[0]
        strategy.stop()

    def test_a_signal_reaches_the_exec_layer_unchanged_when_healthy(self):
        """With the monitor healthy, the wrap must not alter behaviour at all
        — AC #1's zero-diff contract in miniature.
        """
        strategy = _new_strategy()
        install_order_path(strategy, _StubMonitor(withheld=False), structlog.get_logger("test"))
        strategy.start()

        for index, close in enumerate(CLOSES):
            strategy.on_bar(make_bar(index, close))

        assert len(list(strategy.cache.orders())) == 1
        strategy.stop()

    def test_close_position_suppression_logs_no_client_order_id(self):
        """`close_position` never fabricates a `client_order_id` — there is
        no order yet at the point suppression decides not to create one.

        This harness has no `ExecutionEngine`, so a submitted order never
        fills (Story 3.2's Task 1.1 probe measured this: `cache.positions()`
        stays empty after a plain `submit_order`). A real open `Position` is
        built by hand instead — the same order-lifecycle-then-`Position`
        construction the story's Task 1.1 probe used to measure the
        `close_position` residual.
        """
        strategy = _new_strategy()
        instrument = strategy.cache.instrument(strategy.instrument_id)
        order = strategy.order_factory.market(
            instrument_id=instrument.id, order_side=OrderSide.BUY, quantity=Quantity.from_int(10)
        )
        strategy.cache.add_order(order)
        order.apply(TestEventStubs.order_submitted(order))
        order.apply(TestEventStubs.order_accepted(order))
        fill = TestEventStubs.order_filled(
            order, instrument, position_id=PositionId(f"P-{order.client_order_id}")
        )
        order.apply(fill)
        strategy.cache.update_order(order)
        position = Position(instrument=instrument, fill=fill)
        strategy.cache.add_position(position, OmsType.NETTING)

        monitor = _StubMonitor(withheld=True)
        install_order_path(strategy, monitor, structlog.get_logger("test"))
        strategy.start()

        with capture_logs() as logs:
            strategy.close_position(position)
        strategy.stop()

        suppressed = [entry for entry in logs if entry["event"] == "order.suppressed"]
        assert len(suppressed) == 1
        assert suppressed[0]["method"] == "close_position"
        assert suppressed[0]["instrument_id"] == "AAPL.NASDAQ"
        assert "client_order_id" not in suppressed[0]
        assert list(strategy.cache.orders()) == [order], "no new closing order may be created"


class TestTheProbeCanActuallyFail:
    """Anti-tautology twin (Task 3.1): the SAME harness without the wrap
    installed must record the order — proving the suppression assertions
    above can genuinely fail.
    """

    def test_without_the_wrap_the_order_still_submits(self):
        strategy = _new_strategy()
        strategy.start()

        for index, close in enumerate(CLOSES):
            strategy.on_bar(make_bar(index, close))

        assert len(list(strategy.cache.orders())) == 1
        strategy.stop()


class TestOrderCreatingMethodsMembership:
    """`ORDER_CREATING_METHODS` is its own membership-pinned list (CLAUDE.md
    Anti-Patterns): every other consumer only intersects with it, so an
    exact-set pin is what makes a silent shrink visible.
    """

    def test_is_a_subset_of_the_stop_paths_forbidden_set(self):
        assert ORDER_CREATING_METHODS <= _STOP_PATH_FORBIDDEN_ORDER_METHODS

    def test_cancels_are_deliberately_excluded(self):
        """Cancelling while disconnected creates no exposure and cannot
        "blindly trade" — only order/position-*creating* calls are wrapped.
        """
        assert "cancel_order" not in ORDER_CREATING_METHODS
        assert "cancel_all_orders" not in ORDER_CREATING_METHODS

    def test_the_set_is_pinned_exactly(self):
        assert ORDER_CREATING_METHODS == frozenset(
            {"submit_order", "submit_order_list", "close_position", "close_all_positions"}
        )


def _order_submitted(*, instrument_id, client_order_id, strategy_id, ts_event, ts_init):
    return OrderSubmitted(
        trader_id=TraderId("TESTER-000"),
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        client_order_id=client_order_id,
        account_id=AccountId("INTERACTIVE_BROKERS-DU4076626"),
        event_id=UUID4(),
        ts_event=ts_event,
        ts_init=ts_init,
    )


class TestOrderEventObserver:
    """Task 4 — the first order-event consumer, plus the latency it anchors
    on the venue bar close (Task 4.3).
    """

    INSTRUMENT_ID = InstrumentId.from_str("AAPL.NASDAQ")
    STRATEGY_ID = StrategyId("SMACrossover-000")
    CLIENT_ORDER_ID = ClientOrderId("O-20260830-193805-621bb88c-000-1")

    def test_order_submitted_with_a_known_bar_logs_the_latency(self):
        observer = OrderEventObserver(structlog.get_logger("test").bind(session_id="s1"))
        bar = make_bar(0, "100.00")
        observer.note_bar(bar)
        event = _order_submitted(
            instrument_id=self.INSTRUMENT_ID,
            client_order_id=self.CLIENT_ORDER_ID,
            strategy_id=self.STRATEGY_ID,
            ts_event=bar.ts_event,
            ts_init=bar.ts_event + 500_000_000,  # +500ms
        )

        with capture_logs() as logs:
            observer.handle_order_event(event)

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT]
        assert len(submitted) == 1
        assert submitted[0]["session_id"] == "s1"
        assert submitted[0]["client_order_id"] == str(self.CLIENT_ORDER_ID)
        assert submitted[0]["instrument_id"] == str(self.INSTRUMENT_ID)
        assert submitted[0]["bar_close_to_submit_ms"] == pytest.approx(500.0)

    def test_order_submitted_with_no_known_bar_omits_the_latency_field(self):
        """Never a fabricated value — the field is simply absent."""
        observer = OrderEventObserver(structlog.get_logger("test"))
        event = _order_submitted(
            instrument_id=self.INSTRUMENT_ID,
            client_order_id=self.CLIENT_ORDER_ID,
            strategy_id=self.STRATEGY_ID,
            ts_event=1_000,
            ts_init=2_000,
        )

        with capture_logs() as logs:
            observer.handle_order_event(event)

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT]
        assert len(submitted) == 1
        assert "bar_close_to_submit_ms" not in submitted[0]

    def test_an_order_initialized_on_the_same_topic_is_ignored_silently(self):
        """`submit_order` publishes `OrderInitialized` on the same topic
        before `OrderSubmitted` ever exists (Task 1.2) — the observer must
        not mistake it for one.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)

        assert logs == []

    def test_an_order_rejected_is_contained_without_crashing_or_logging(self):
        """AC #3's rejection-tolerance guard, doubled up with this scan."""
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        rejected = TestEventStubs.order_rejected(order)

        with capture_logs() as logs:
            observer.handle_order_event(rejected)  # must not raise

        assert logs == []

    def test_note_bar_never_raises_on_a_malformed_bar(self):
        observer = OrderEventObserver(structlog.get_logger("test"))

        with capture_logs() as logs:
            observer.note_bar(object())  # no `.bar_type` at all

        failed = [entry for entry in logs if entry["event"] == "order.observer_failed"]
        assert len(failed) == 1
        assert failed[0]["stage"] == "note_bar"

    def test_handle_order_event_never_raises_on_a_malformed_submitted_event(self):
        class _FakeSubmitted:
            """Duck-shaped as `OrderSubmitted` by name only — no attributes."""

        observer = OrderEventObserver(structlog.get_logger("test"))
        fake = _FakeSubmitted()
        fake.__class__.__name__ = "OrderSubmitted"

        with capture_logs() as logs:
            observer.handle_order_event(fake)  # must not raise

        failed = [entry for entry in logs if entry["event"] == "order.observer_failed"]
        assert len(failed) == 1
        assert failed[0]["stage"] == "handle_order_event"


def _make_order():
    return TestExecStubs.market_order(
        instrument=TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ"),
        strategy_id=StrategyId("SMACrossover-000"),
    )
