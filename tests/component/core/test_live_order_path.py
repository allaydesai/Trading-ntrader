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
from nautilus_trader.model.enums import LiquiditySide, OmsType, OrderSide, OrderType
from nautilus_trader.model.events.order import (
    OrderDenied,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    InstrumentId,
    PositionId,
    StrategyId,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from structlog.testing import capture_logs

from src.core.live_order_path import (
    ACCEPTED_EVENT,
    CANCELED_EVENT,
    DENIED_EVENT,
    EMITTED_ORDER_EVENTS,
    EXPIRED_EVENT,
    FILLED_EVENT,
    ORDER_CREATING_METHODS,
    REJECTED_EVENT,
    SUBMITTED_EVENT,
    OrderEventObserver,
    install_order_path,
)
from src.core.live_session_node import materialise_strategy
from src.models.session import StrategySpec

AAPL_EQUITY = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")

pytestmark = pytest.mark.component

AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
BAR_TYPE = BarType.from_str(AAPL_1MIN)

#: Duplicated from `tests/unit/core/test_live_stop_path_is_inert.py:53-62`.
#: Kept as its own literal so a change to either list is a deliberate, visible
#: edit in both files.
#:
#: ⚠️ Review fix, 2026-08-30. The original comment justified the copy with
#: "production code cannot import from `tests/`" — true, but a non-sequitur
#: here: this is a *test* file, `tests/__init__.py` exists, and a sibling
#: module already does `from tests.component.doubles import TestLiveNode`
#: (`test_session_runner_order_path.py:24`).
#: With nothing tying the copy to its source, a *shrinking* of the real set —
#: exactly the failure mode CLAUDE.md's "Membership-pinned lists" entry was
#: written for — would leave the removed name here and the subset assertion
#: below passing against a stale relationship. So the copy stays (it is the
#: visible-edit tripwire) but is now pinned equal to its source; see
#: `TestOrderCreatingMethodsIsPinned.test_the_local_copy_has_not_drifted`.
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


#: The IB adapter stamps `ts_event` as the bar's OPEN (see
#: `live_bar_observer.py`, fact (1)); the close is one interval later. Every
#: latency test below anchors on the close, never on `ts_event` itself —
#: anchoring on `ts_event` was the Story 3.2 defect (off by one interval).
BAR_INTERVAL_NS = 60_000_000_000
#: How long after the venue CLOSE `make_bar`'s arrival (`ts_init`) sits —
#: the IB delivery lag measured live on 2026-09-01 (5.28s/5.53s/5.68s on
#: consecutive bars). Strictly after the close, as a real bar's is; and far
#: enough from it that a test cannot pass with the wrong anchor.
BAR_ARRIVAL_AFTER_CLOSE_NS = 5_500_000_000


def bar_close_ns(bar: Bar) -> int:
    return bar.ts_event + BAR_INTERVAL_NS


def make_bar(index: int, close: str) -> Bar:
    price = Price.from_str(close)
    return Bar(
        bar_type=BAR_TYPE,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_int(1_000),
        ts_event=index * BAR_INTERVAL_NS,
        # Deliberately NOT equal to `ts_event` (review fix, 2026-08-30).
        # These were identical, which made the two anchors indistinguishable:
        # mutating `note_bar` from `bar.ts_event` to `bar.ts_init` left every
        # test green. Task 9.1's M4 only ever caught `time.time_ns()`, which
        # is off by ~1.8e12 and would have been caught by anything. A real
        # bar's `ts_init` is its arrival instant, strictly after the venue
        # close it reports. (Until the 2026-09-10 NFR1 ruling this sat 7s
        # after the OPEN — i.e. 53s *before* the close — the same open/close
        # confusion the ruling corrected in the observer.)
        ts_init=index * BAR_INTERVAL_NS + BAR_INTERVAL_NS + BAR_ARRIVAL_AFTER_CLOSE_NS,
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

    def test_suppression_names_the_strategy_that_was_withheld(self):
        """Task 3.1's fourth mandated field (review 2026-08-30).

        Task 3.1 names `session_id`, `strategy_id`, `instrument_id` and
        `client_order_id`; only three were emitted, and the original
        assertion block was written to match. `SessionSpec` permits two
        strategies on one instrument, so without this an operator reading
        `order.suppressed` cannot tell which one was withheld.

        Read from the strategy at **call** time, never captured at install
        time: `Trader.add_strategy` rewrites `strategy.id` when it
        auto-assigns an `order_id_tag` (`trading/trader.py:406-412`), and
        `install_order_path` runs *before* that rewrite.
        """
        strategy = _new_strategy()
        install_order_path(strategy, _StubMonitor(withheld=True), structlog.get_logger("test"))
        strategy.start()

        with capture_logs() as logs:
            for index, close in enumerate(CLOSES):
                strategy.on_bar(make_bar(index, close))

        suppressed = [entry for entry in logs if entry["event"] == "order.suppressed"]
        assert len(suppressed) == 1
        assert suppressed[0]["strategy_id"] == str(strategy.id)
        strategy.stop()

    def test_the_strategy_id_is_read_after_nautilus_rewrites_it(self):
        """The install-time-capture trap, pinned directly.

        Capturing `str(strategy.id)` inside `install_order_path` would log
        the pre-registration id forever. Simulating the rewrite Nautilus
        performs in `add_strategy` proves the value is read per call.
        """
        strategy = _new_strategy()
        install_order_path(strategy, _StubMonitor(withheld=True), structlog.get_logger("test"))
        strategy.change_id(StrategyId("SMACrossover-007"))
        strategy.start()

        with capture_logs() as logs:
            for index, close in enumerate(CLOSES):
                strategy.on_bar(make_bar(index, close))

        suppressed = [entry for entry in logs if entry["event"] == "order.suppressed"]
        assert suppressed[0]["strategy_id"] == "SMACrossover-007"
        strategy.stop()

    def test_installing_twice_does_not_nest_the_wrappers(self):
        """Review 2026-08-30: `getattr` returns the already-wrapped function
        on a second call, so a re-install would consult the predicate twice
        and emit two records for one call. The runner installs once per
        strategy today, but the function is public and framework-free.
        """
        strategy = _new_strategy()
        log = structlog.get_logger("test")
        install_order_path(strategy, _StubMonitor(withheld=True), log)
        first = strategy.submit_order
        install_order_path(strategy, _StubMonitor(withheld=True), log)

        assert strategy.submit_order is first, "re-install must be a no-op, not another layer"

        strategy.start()
        with capture_logs() as logs:
            for index, close in enumerate(CLOSES):
                strategy.on_bar(make_bar(index, close))

        assert len([e for e in logs if e["event"] == "order.suppressed"]) == 1
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

    def test_the_local_copy_has_not_drifted_from_the_stop_paths_own_list(self):
        """Review fix, 2026-08-30 — the subset assertion above was checked
        against a hand-copied literal that nothing tied to its source, so if
        the real `FORBIDDEN_ORDER_METHODS` ever *shrank*, the copy would keep
        the removed name and the subset relationship would keep passing while
        meaning nothing.

        A test file may import from another test file (`tests/__init__.py`
        exists, and this module already imports `tests.component.doubles`), so
        the relationship is now checked by reference rather than asserted
        about a copy. The copy stays as the deliberate-visible-edit tripwire
        CLAUDE.md's "Membership-pinned lists" entry asks for; this test is
        what makes the tripwire honest.
        """
        from tests.unit.core.test_live_stop_path_is_inert import FORBIDDEN_ORDER_METHODS

        assert _STOP_PATH_FORBIDDEN_ORDER_METHODS == FORBIDDEN_ORDER_METHODS, (
            "the local copy and tests/unit/core/test_live_stop_path_is_inert.py's "
            "FORBIDDEN_ORDER_METHODS have diverged — update both, deliberately"
        )

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


def _order_rejected(order, *, reason: str, due_post_only: bool = False) -> OrderRejected:
    """`TestEventStubs.order_rejected` hardcodes `reason="ORDER_REJECTED"`
    with no override (`test_kit/stubs/events.py:238`) — build directly
    whenever the reason must be distinctive.
    """
    return OrderRejected(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        account_id=AccountId("SIM-001"),
        reason=reason,
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
        due_post_only=due_post_only,
    )


def _order_denied(order, *, reason: str) -> OrderDenied:
    """No `order_denied` stub exists (Dev Notes) — the ctor takes `ts_init`
    only, no separate `ts_event`.
    """
    return OrderDenied(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=order.instrument_id,
        client_order_id=order.client_order_id,
        reason=reason,
        event_id=UUID4(),
        ts_init=0,
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
            ts_init=bar.ts_init + 500_000_000,  # 500ms after ARRIVAL, 6s after the CLOSE
        )

        with capture_logs() as logs:
            observer.handle_order_event(event)

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT]
        assert len(submitted) == 1
        assert submitted[0]["session_id"] == "s1"
        assert submitted[0]["client_order_id"] == str(self.CLIENT_ORDER_ID)
        assert submitted[0]["instrument_id"] == str(self.INSTRUMENT_ID)
        assert submitted[0]["bar_close_to_submit_ms"] == pytest.approx(6_000.0)
        assert submitted[0]["bar_arrival_to_submit_ms"] == pytest.approx(500.0)

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
        assert "bar_arrival_to_submit_ms" not in submitted[0]

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

    def test_an_order_rejected_is_logged_with_the_venue_reason_and_never_raises(self):
        """AC #3's rejection-tolerance guard, inverted (Story 3.3 Task 2.1):
        3.2 dropped rejections silently; this story requires exactly one
        `order.rejected` record carrying the venue's reason verbatim, with
        containment preserved. `TestEventStubs.order_rejected` hardcodes
        `reason="ORDER_REJECTED"` with no override
        (`test_kit/stubs/events.py:238`), so a swallow-and-reword mutation
        needs a distinctive reason to be catchable — build the event
        directly.
        """
        observer = OrderEventObserver(structlog.get_logger("test").bind(session_id="s1"))
        order = _make_order()
        rejected = _order_rejected(order, reason="INSUFFICIENT_BUYING_POWER_DISTINCTIVE")

        with capture_logs() as logs:
            observer.handle_order_event(rejected)  # must not raise

        records = [entry for entry in logs if entry["event"] == REJECTED_EVENT]
        assert len(records) == 1
        record = records[0]
        assert record["venue_reason"] == "INSUFFICIENT_BUYING_POWER_DISTINCTIVE"
        assert record["client_order_id"] == str(order.client_order_id)
        assert record["instrument_id"] == str(order.instrument_id)
        assert record["strategy_id"] == str(order.strategy_id)
        assert record["due_post_only"] is False
        assert record["session_id"] == "s1"
        assert record["log_level"] == "warning"
        assert [entry for entry in logs if entry["event"] == "order.observer_failed"] == []

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


class TestTheLatencyAnchorIsUnambiguous:
    """Review 2026-08-30 — NFR1's anchor was keyed on the instrument alone
    while the observer subscribes to the wildcard ``data.bars.*``, so a second
    aggregation on the same instrument silently overwrote it.

    Two halves, both pinned here: the observer only anchors on the
    aggregations the strategies actually trade, and every emitted latency
    names the bar type it was measured from.
    """

    INSTRUMENT_ID = InstrumentId.from_str("AAPL.NASDAQ")
    STRATEGY_ID = StrategyId("SMACrossover-000")
    CLIENT_ORDER_ID = ClientOrderId("O-20260830-193805-621bb88c-000-1")
    HOURLY = BarType.from_str("AAPL.NASDAQ-1-HOUR-LAST-EXTERNAL")

    def _submitted(self, ts_init: int):
        return _order_submitted(
            instrument_id=self.INSTRUMENT_ID,
            client_order_id=self.CLIENT_ORDER_ID,
            strategy_id=self.STRATEGY_ID,
            ts_event=ts_init,
            ts_init=ts_init,
        )

    def test_an_untraded_aggregation_never_becomes_the_anchor(self):
        """The defect itself: a 1-HOUR bar arriving after the traded 1-MINUTE
        bar used to overwrite the anchor, so the latency was measured from a
        close the signal never saw.
        """
        observer = OrderEventObserver(
            structlog.get_logger("test"), traded_bar_types=(str(BAR_TYPE),)
        )
        traded = make_bar(100, "100.00")  # far enough in that the hourly anchor stays positive
        observer.note_bar(traded)
        observer.note_bar(
            Bar(
                bar_type=self.HOURLY,
                open=Price.from_str("100.00"),
                high=Price.from_str("100.00"),
                low=Price.from_str("100.00"),
                close=Price.from_str("100.00"),
                volume=Quantity.from_int(1_000),
                ts_event=traded.ts_event - 3_600_000_000_000,
                ts_init=traded.ts_event,
            )
        )

        with capture_logs() as logs:
            observer.handle_order_event(self._submitted(bar_close_ns(traded) + 500_000_000))

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT][0]
        assert submitted["bar_close_to_submit_ms"] == pytest.approx(500.0)
        assert submitted["latency_anchor_bar_type"] == str(BAR_TYPE)

    def test_the_emitted_latency_names_the_bar_type_it_was_measured_from(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        bar = make_bar(3, "100.00")
        observer.note_bar(bar)

        with capture_logs() as logs:
            observer.handle_order_event(self._submitted(bar_close_ns(bar) + 250_000_000))

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT][0]
        assert submitted["latency_anchor_bar_type"] == str(BAR_TYPE)

    def test_with_no_traded_set_every_bar_still_anchors(self):
        """Back-compatible default: `traded_bar_types=None` keeps the
        original behaviour, so a caller that does not know its aggregations
        is not silently left with no latency at all.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        observer.note_bar(make_bar(1, "100.00"))

        with capture_logs() as logs:
            observer.handle_order_event(
                self._submitted(bar_close_ns(make_bar(1, "100.00")) + 1_000_000)
            )

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT][0]
        assert submitted["bar_close_to_submit_ms"] == pytest.approx(1.0)

    @pytest.mark.parametrize(
        ("label", "offset_ns"),
        [
            ("negative — bar close after the submission", -1_000_000_000),
            ("absurd — an epoch-zero or backfill anchor", 7_200_000_000_000),
        ],
    )
    def test_an_implausible_interval_is_not_emitted_as_the_nfr1_measurement(self, label, offset_ns):
        """It is still recorded — under a different key, so a transcript
        reader cannot mistake it for the measurement NFR1 is judged on.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        bar = make_bar(100, "100.00")
        observer.note_bar(bar)

        with capture_logs() as logs:
            observer.handle_order_event(self._submitted(bar_close_ns(bar) + offset_ns))

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT][0]
        assert "bar_close_to_submit_ms" not in submitted, label
        assert submitted["implausible_latency_ms"] == pytest.approx(offset_ns / 1_000_000)
        assert submitted["latency_anchor_bar_type"] == str(BAR_TYPE)

    def test_a_zero_ts_event_bar_does_not_produce_a_fifty_five_year_latency(self):
        """The concrete shape: a bar carrying an unset `ts_event` of 0 used to
        yield `ts_init / 1e6` — roughly 55 years — logged as the measurement.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        observer.note_bar(
            Bar(
                bar_type=BAR_TYPE,
                open=Price.from_str("100.00"),
                high=Price.from_str("100.00"),
                low=Price.from_str("100.00"),
                close=Price.from_str("100.00"),
                volume=Quantity.from_int(1_000),
                ts_event=0,
                ts_init=0,
            )
        )

        with capture_logs() as logs:
            observer.handle_order_event(self._submitted(1_756_000_000_000_000_000))

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT][0]
        assert "bar_close_to_submit_ms" not in submitted
        assert "bar_arrival_to_submit_ms" not in submitted


class TestNfr1IsJudgedOnBarArrival:
    """NFR1 ruling, 2026-09-10 (option (c) of the deferred-work item dated
    2026-09-01): two latencies are logged from one submission.

    * ``bar_close_to_submit_ms`` — anchored on the venue **close**
      (``ts_event + interval``, because this adapter's ``ts_event`` is the
      OPEN). It is the live-vs-backtest divergence window, and it *includes*
      IB's structural ~5.5s delivery lag. Logged, never gated on.
    * ``bar_arrival_to_submit_ms`` — anchored on the bar's arrival in this
      process (``bar.ts_init``). It is what this system controls, and it is
      the number NFR1's ``< 1s`` bound is judged on.

    The pre-ruling field measured from the open, so on the 2026-09-01 fill it
    logged 65682ms for a 60s bar: one whole interval of nothing plus the lag.
    """

    INSTRUMENT_ID = InstrumentId.from_str("AAPL.NASDAQ")
    STRATEGY_ID = StrategyId("SMACrossover-000")
    CLIENT_ORDER_ID = ClientOrderId("O-20260830-193805-621bb88c-000-1")

    def _submitted(self, ts_init: int):
        return _order_submitted(
            instrument_id=self.INSTRUMENT_ID,
            client_order_id=self.CLIENT_ORDER_ID,
            strategy_id=self.STRATEGY_ID,
            ts_event=ts_init,
            ts_init=ts_init,
        )

    def _submit_after_arrival(self, bar: Bar, after_arrival_ns: int) -> dict:
        observer = OrderEventObserver(structlog.get_logger("test"))
        observer.note_bar(bar)
        with capture_logs() as logs:
            observer.handle_order_event(self._submitted(bar.ts_init + after_arrival_ns))
        return [entry for entry in logs if entry["event"] == SUBMITTED_EVENT][0]

    def test_the_close_anchor_is_one_interval_after_ts_event_not_ts_event_itself(self):
        """The 2026-09-01 shape, in miniature: a bar that arrives 5.5s after
        its close and a signal 3.5ms after arrival must log ~5503.5ms from the
        close — not ~65503.5ms from the open.
        """
        bar = Bar(
            bar_type=BAR_TYPE,
            open=Price.from_str("100.00"),
            high=Price.from_str("100.00"),
            low=Price.from_str("100.00"),
            close=Price.from_str("100.00"),
            volume=Quantity.from_int(1_000),
            ts_event=100 * BAR_INTERVAL_NS,
            ts_init=100 * BAR_INTERVAL_NS + BAR_INTERVAL_NS + 5_500_000_000,
        )

        submitted = self._submit_after_arrival(bar, 3_500_000)

        assert submitted["bar_close_to_submit_ms"] == pytest.approx(5_503.5)
        assert submitted["bar_arrival_to_submit_ms"] == pytest.approx(3.5)

    def test_the_arrival_latency_is_independent_of_the_bar_interval(self):
        """Swap the 1-MINUTE bar for a 1-HOUR one: the close anchor moves by
        an hour, the arrival anchor does not move at all.
        """
        hourly = BarType.from_str("AAPL.NASDAQ-1-HOUR-LAST-EXTERNAL")
        bar = Bar(
            bar_type=hourly,
            open=Price.from_str("100.00"),
            high=Price.from_str("100.00"),
            low=Price.from_str("100.00"),
            close=Price.from_str("100.00"),
            volume=Quantity.from_int(1_000),
            ts_event=10 * 3_600_000_000_000,
            ts_init=11 * 3_600_000_000_000 + 5_000_000_000,
        )

        submitted = self._submit_after_arrival(bar, 2_000_000)

        assert submitted["bar_close_to_submit_ms"] == pytest.approx(5_002.0)
        assert submitted["bar_arrival_to_submit_ms"] == pytest.approx(2.0)
        assert submitted["latency_anchor_bar_type"] == str(hourly)

    def test_an_implausible_arrival_interval_is_recorded_under_its_own_key(self):
        """A submission stamped *before* the bar arrived is clock nonsense,
        not a sub-millisecond latency. Same band as the close anchor, same
        rule: visible, under a key that cannot be mistaken for NFR1's number.
        """
        bar = make_bar(100, "100.00")

        submitted = self._submit_after_arrival(bar, -1_000_000_000)

        assert "bar_arrival_to_submit_ms" not in submitted
        assert submitted["implausible_arrival_latency_ms"] == pytest.approx(-1_000.0)

    def test_both_latencies_come_from_the_same_bar(self):
        """Two bars, one traded aggregation: both fields are measured from
        the bar the signal saw, never mixed across bars.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        observer.note_bar(make_bar(5, "100.00"))
        latest = make_bar(6, "101.00")
        observer.note_bar(latest)

        with capture_logs() as logs:
            observer.handle_order_event(self._submitted(latest.ts_init + 4_000_000))

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT][0]
        assert submitted["bar_close_to_submit_ms"] == pytest.approx(5_504.0)
        assert submitted["bar_arrival_to_submit_ms"] == pytest.approx(4.0)


def _make_order():
    return TestExecStubs.market_order(
        instrument=TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ"),
        strategy_id=StrategyId("SMACrossover-000"),
    )


class TestOrderAcceptedDispatch:
    def test_order_accepted_logs_the_acknowledgement_identity(self):
        """AC #5's "acknowledgement" — the first moment a venue-side
        identity exists (`OrderSubmitted.venue_order_id` is hardcoded
        `None`).
        """
        observer = OrderEventObserver(structlog.get_logger("test").bind(session_id="s1"))
        order = _make_order()
        accepted = TestEventStubs.order_accepted(order, venue_order_id=VenueOrderId("V-77"))

        with capture_logs() as logs:
            observer.handle_order_event(accepted)

        records = [entry for entry in logs if entry["event"] == ACCEPTED_EVENT]
        assert len(records) == 1
        record = records[0]
        assert record["session_id"] == "s1"
        assert record["client_order_id"] == str(order.client_order_id)
        assert record["instrument_id"] == str(order.instrument_id)
        assert record["strategy_id"] == str(order.strategy_id)
        assert record["venue_order_id"] == "V-77"


class TestOrderCanceledAndExpiredDispatch:
    """`OrderCanceled`/`OrderExpired` share an identical 10-field shape;
    `venue_order_id` is nullable on both — log what exists, never invent.
    """

    def test_order_canceled_omits_venue_order_id_when_absent(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        canceled = TestEventStubs.order_canceled(order)

        with capture_logs() as logs:
            observer.handle_order_event(canceled)

        records = [entry for entry in logs if entry["event"] == CANCELED_EVENT]
        assert len(records) == 1
        assert "venue_order_id" not in records[0]
        assert records[0]["client_order_id"] == str(order.client_order_id)
        assert records[0]["strategy_id"] == str(order.strategy_id)
        # AC #2's mandatory triple — `instrument_id` was unasserted on this
        # path, so deleting it from `_log_terminal` left the suite green.
        assert records[0]["instrument_id"] == str(order.instrument_id)

    def test_order_canceled_logs_venue_order_id_when_present(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        order.apply(TestEventStubs.order_submitted(order))
        order.apply(TestEventStubs.order_accepted(order, venue_order_id=VenueOrderId("V-42")))
        canceled = TestEventStubs.order_canceled(order)

        with capture_logs() as logs:
            observer.handle_order_event(canceled)

        records = [entry for entry in logs if entry["event"] == CANCELED_EVENT]
        assert len(records) == 1, "every sibling pins the count; a double emit slipped past here"
        assert records[0]["venue_order_id"] == "V-42"

    def test_order_expired_omits_venue_order_id_when_absent(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        expired = TestEventStubs.order_expired(order)

        with capture_logs() as logs:
            observer.handle_order_event(expired)

        records = [entry for entry in logs if entry["event"] == EXPIRED_EVENT]
        assert len(records) == 1
        assert "venue_order_id" not in records[0]
        # AC #2's mandatory triple: this test asserted only a count and an
        # absence, so `order.expired` had no guarded field at all.
        assert records[0]["client_order_id"] == str(order.client_order_id)
        assert records[0]["instrument_id"] == str(order.instrument_id)
        assert records[0]["strategy_id"] == str(order.strategy_id)

    def test_order_expired_logs_venue_order_id_when_present(self):
        """The present-`venue_order_id` case was tested for cancel but not for
        expiry. `_log_terminal` makes them share a shape today; nothing pins
        that they keep sharing one.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        order.apply(TestEventStubs.order_submitted(order))
        order.apply(TestEventStubs.order_accepted(order, venue_order_id=VenueOrderId("V-99")))

        with capture_logs() as logs:
            observer.handle_order_event(TestEventStubs.order_expired(order))

        records = [entry for entry in logs if entry["event"] == EXPIRED_EVENT]
        assert len(records) == 1
        assert records[0]["venue_order_id"] == "V-99"


class TestOrderFilledDispatch:
    def test_order_filled_logs_the_full_field_contract(self):
        observer = OrderEventObserver(structlog.get_logger("test").bind(session_id="s1"))
        order = _make_order()
        filled = TestEventStubs.order_filled(
            order,
            AAPL_EQUITY,
            last_qty=Quantity.from_int(100),
            position_id=PositionId(f"P-{order.client_order_id}"),
        )

        with capture_logs() as logs:
            observer.handle_order_event(filled)

        records = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        assert len(records) == 1
        record = records[0]
        assert record["session_id"] == "s1"
        assert record["client_order_id"] == str(order.client_order_id)
        assert record["instrument_id"] == str(order.instrument_id)
        assert record["strategy_id"] == str(order.strategy_id)
        assert record["fill_qty"] == "100"
        assert record["cum_qty"] == "100"
        assert record["last_px"] == str(filled.last_px)
        assert record["commission"] == str(filled.commission)
        assert record["currency"] == str(filled.currency)
        assert record["trade_id"] == str(filled.trade_id)
        assert record["venue_order_id"] == str(filled.venue_order_id)
        assert record["position_id"] == str(filled.position_id)
        assert "order_qty" not in record, "no OrderInitialized was ever observed for this order"


class TestOrderDeniedDispatch:
    def test_order_denied_logs_reason_not_venue_reason(self):
        """`reason`, deliberately not `venue_reason` — a denial is local
        (risk/exec engine), no venue was involved.
        """
        observer = OrderEventObserver(structlog.get_logger("test").bind(session_id="s1"))
        order = _make_order()
        denied = _order_denied(order, reason="RISK_LIMIT_EXCEEDED_DISTINCTIVE")

        with capture_logs() as logs:
            observer.handle_order_event(denied)

        records = [entry for entry in logs if entry["event"] == DENIED_EVENT]
        assert len(records) == 1
        record = records[0]
        assert record["reason"] == "RISK_LIMIT_EXCEEDED_DISTINCTIVE"
        assert "venue_reason" not in record
        assert record["client_order_id"] == str(order.client_order_id)
        assert record["instrument_id"] == str(order.instrument_id)
        assert record["strategy_id"] == str(order.strategy_id)
        assert record["log_level"] == "warning"


class TestUnknownEventTypesAreASilentClosedSet:
    """Story 3.3 owns exactly the six lifecycle states plus denied — the
    dispatch is a closed set, not a default-log, since no modify or
    cancel-request path exists in this repo to make the others meaningful.
    """

    def test_an_order_updated_event_produces_no_record(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        updated = TestEventStubs.order_updated(order)

        with capture_logs() as logs:
            observer.handle_order_event(updated)

        assert logs == []

    def test_an_order_pending_cancel_event_produces_no_record(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        pending_cancel = TestEventStubs.order_pending_cancel(order)

        with capture_logs() as logs:
            observer.handle_order_event(pending_cancel)

        assert logs == []


class TestContainmentExtendsToEveryNewDispatchBranch:
    """A raise from a msgbus handler ends the process with zero output —
    every new dispatch branch must live inside the existing containment.
    """

    def test_a_malformed_filled_event_is_contained(self):
        class _FakeFilled:
            """Duck-shaped as `OrderFilled` by name only — no attributes."""

        observer = OrderEventObserver(structlog.get_logger("test"))
        fake = _FakeFilled()
        fake.__class__.__name__ = "OrderFilled"

        with capture_logs() as logs:
            observer.handle_order_event(fake)  # must not raise

        failed = [entry for entry in logs if entry["event"] == "order.observer_failed"]
        assert len(failed) == 1
        assert failed[0]["stage"] == "handle_order_event"

    def test_a_malformed_rejected_event_is_contained(self):
        class _FakeRejected:
            """Duck-shaped as `OrderRejected` by name only — no attributes."""

        observer = OrderEventObserver(structlog.get_logger("test"))
        fake = _FakeRejected()
        fake.__class__.__name__ = "OrderRejected"

        with capture_logs() as logs:
            observer.handle_order_event(fake)  # must not raise

        failed = [entry for entry in logs if entry["event"] == "order.observer_failed"]
        assert len(failed) == 1
        assert failed[0]["stage"] == "handle_order_event"


class TestPartialFillSeries:
    """AC #1's representation: a partial fill is an ordinary `OrderFilled`
    whose `cum_qty` is below the order's quantity — no dedicated event.
    """

    def test_a_partial_fill_series_produces_a_rising_cum_qty(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        fill_a = TestEventStubs.order_filled(
            order, AAPL_EQUITY, last_qty=Quantity.from_int(30), trade_id=TradeId("E-1")
        )
        fill_b = TestEventStubs.order_filled(
            order, AAPL_EQUITY, last_qty=Quantity.from_int(70), trade_id=TradeId("E-2")
        )

        with capture_logs() as logs:
            observer.handle_order_event(fill_a)
            observer.handle_order_event(fill_b)

        records = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        assert len(records) == 2
        assert records[0]["fill_qty"] == "30"
        assert records[0]["cum_qty"] == "30"
        assert records[1]["fill_qty"] == "70"
        assert records[1]["cum_qty"] == "100"

    def test_a_different_client_order_id_accumulates_independently(self):
        """Fixture trap: two `TestExecStubs.market_order(...)` calls yield
        the SAME frozen `client_order_id` — an explicit override is
        required or the "independent" order is the same order.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order_a = _make_order()
        order_b = TestExecStubs.market_order(
            instrument=AAPL_EQUITY,
            strategy_id=StrategyId("SMACrossover-000"),
            client_order_id=ClientOrderId("O-OTHER-1"),
        )
        fill_a = TestEventStubs.order_filled(order_a, AAPL_EQUITY, last_qty=Quantity.from_int(30))
        fill_b = TestEventStubs.order_filled(order_b, AAPL_EQUITY, last_qty=Quantity.from_int(15))

        with capture_logs() as logs:
            observer.handle_order_event(fill_a)
            observer.handle_order_event(fill_b)

        records = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        assert records[0]["cum_qty"] == "30"
        assert records[1]["cum_qty"] == "15"


class TestPartialFillThenCancel:
    def test_a_late_fill_across_the_cancel_ack_still_accumulates(self):
        """A real venue sequence: `OrderFilled(30) -> OrderCanceled` leaves
        the prior fill record standing; a late fill crossing the cancel ack
        logs `cum_qty` "50", not "20" — the entry is retained, not reset,
        because pruning on cancel would log a false `cum_qty` counting the
        late fill alone.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        first_fill = TestEventStubs.order_filled(
            order, AAPL_EQUITY, last_qty=Quantity.from_int(30), trade_id=TradeId("E-1")
        )
        canceled = TestEventStubs.order_canceled(order)
        late_fill = TestEventStubs.order_filled(
            order, AAPL_EQUITY, last_qty=Quantity.from_int(20), trade_id=TradeId("E-2")
        )

        with capture_logs() as logs:
            observer.handle_order_event(first_fill)
            observer.handle_order_event(canceled)
            observer.handle_order_event(late_fill)

        filled = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        canceled_records = [entry for entry in logs if entry["event"] == CANCELED_EVENT]
        assert len(canceled_records) == 1
        assert len(filled) == 2
        assert filled[0]["cum_qty"] == "30"
        assert filled[1]["cum_qty"] == "50"


def _build_submitted_event():
    return TestEventStubs.order_submitted(_make_order())


def _build_accepted_event():
    return TestEventStubs.order_accepted(_make_order())


def _build_rejected_event():
    return _order_rejected(_make_order(), reason="SCAN")


def _build_filled_event():
    return TestEventStubs.order_filled(_make_order(), AAPL_EQUITY)


def _build_canceled_event():
    return TestEventStubs.order_canceled(_make_order())


def _build_expired_event():
    return TestEventStubs.order_expired(_make_order())


def _build_denied_event():
    return _order_denied(_make_order(), reason="SCAN")


#: Every emitted event name, mapped to a zero-arg builder of a representative
#: instance — the NFR26 anti-field scan iterates this so a seventh emitted
#: type cannot join the dispatch without joining the scan.
_EVENT_BUILDERS = {
    SUBMITTED_EVENT: _build_submitted_event,
    ACCEPTED_EVENT: _build_accepted_event,
    REJECTED_EVENT: _build_rejected_event,
    FILLED_EVENT: _build_filled_event,
    CANCELED_EVENT: _build_canceled_event,
    EXPIRED_EVENT: _build_expired_event,
    DENIED_EVENT: _build_denied_event,
}


class TestEventNameLiteralsArePinned:
    """AC #2 pins the exact dotted past-tense spelling — `order.canceled` is
    single-l, matching the Nautilus event class name (`OrderCanceled`), so a
    transcript grep for the class name and the structured record agree. A
    test that only compares captured records against the imported constant
    cannot catch a wrong literal (the constant and the assertion drift
    together); these assert the literal string value directly.
    """

    def test_the_literal_spellings_are_exact(self):
        assert SUBMITTED_EVENT == "order.submitted"
        assert ACCEPTED_EVENT == "order.accepted"
        assert REJECTED_EVENT == "order.rejected"
        assert FILLED_EVENT == "order.filled"
        assert CANCELED_EVENT == "order.canceled"
        assert EXPIRED_EVENT == "order.expired"
        assert DENIED_EVENT == "order.denied"


class TestEmittedOrderEventsMembership:
    """`EMITTED_ORDER_EVENTS` is its own membership-pinned list (CLAUDE.md
    Anti-Patterns, the `ORDER_CREATING_METHODS` precedent): every consumer
    (here, the NFR26 anti-field scan below) only iterates it, so an
    exact-set pin is what makes a silently dropped name visible.
    """

    def test_the_set_is_pinned_exactly(self):
        assert frozenset(EMITTED_ORDER_EVENTS) == frozenset(
            {
                SUBMITTED_EVENT,
                ACCEPTED_EVENT,
                REJECTED_EVENT,
                FILLED_EVENT,
                CANCELED_EVENT,
                EXPIRED_EVENT,
                DENIED_EVENT,
            }
        )


class TestNoRecordEverCarriesAnAccountId:
    """NFR26: `account_id` rides on every venue-sourced order event
    (`OrderDenied`'s own property is the hardcoded exception —
    `order.pyx:781`), and a naive field dump would leak it. This scan pins
    that no lifecycle record carries a *key* named `account_id`,
    parametrized from `EMITTED_ORDER_EVENTS`.

    ⚠️ Scope corrected by code review 2026-08-30. This docstring claimed the
    scan pinned "the stricter never-logged-at-all". It does not, and cannot:
    it inspects key names, not values. NFR26's normative form is value-level —
    *"any account identifier in it is masked to its last 3 characters"*, where
    "it" is a rendered message (`epics.md:549`, `:650`) — and `order.rejected`
    carries `venue_reason` verbatim per AC #3, which is a channel this scan
    cannot see and which IBKR rejection text can embed an account code into.
    That AC-vs-NFR conflict is recorded in `_log_rejected`'s docstring and
    escalated for an epic-level ruling; it is deliberately not settled by
    widening this test's claim.
    """

    @pytest.mark.parametrize("event_name", sorted(EMITTED_ORDER_EVENTS))
    def test_the_record_carries_no_account_id_field(self, event_name):
        observer = OrderEventObserver(structlog.get_logger("test"))
        event = _EVENT_BUILDERS[event_name]()

        with capture_logs() as logs:
            observer.handle_order_event(event)

        matching = [entry for entry in logs if entry["event"] == event_name]
        assert len(matching) == 1
        assert "account_id" not in matching[0]


class TestLifecycleReconstruction:
    """AC #5 — a full lifecycle reconstructed from the captured records
    alone, with no access to the observer's internal state.
    """

    def test_the_fill_path_reconstructs_end_to_end_from_records_alone(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(TestEventStubs.order_submitted(order))
            observer.handle_order_event(TestEventStubs.order_accepted(order))
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=Quantity.from_int(30), trade_id=TradeId("E-1")
                )
            )
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=Quantity.from_int(70), trade_id=TradeId("E-2")
                )
            )

        assert [entry["event"] for entry in logs] == [
            SUBMITTED_EVENT,
            ACCEPTED_EVENT,
            FILLED_EVENT,
            FILLED_EVENT,
        ], "OrderInitialized harvests state silently and emits nothing"
        assert {entry["client_order_id"] for entry in logs} == {str(order.client_order_id)}
        assert "venue_order_id" not in logs[0], "OrderSubmitted's venue_order_id is never assigned"
        assert all("venue_order_id" in entry for entry in logs[1:])
        final = logs[-1]
        assert final["cum_qty"] == "100"
        assert final["order_qty"] == "100", "fill-path finality: cum_qty == order_qty"

    def test_the_rejection_path_reconstructs_submission_and_terminal_state(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        rejected = _order_rejected(order, reason="A_DISTINCTIVE_TERMINAL_REASON")

        with capture_logs() as logs:
            observer.handle_order_event(TestEventStubs.order_submitted(order))
            observer.handle_order_event(rejected)

        assert [entry["event"] for entry in logs] == [SUBMITTED_EVENT, REJECTED_EVENT]
        assert logs[-1]["venue_reason"] == "A_DISTINCTIVE_TERMINAL_REASON"
        assert logs[-1]["client_order_id"] == str(order.client_order_id)

    def test_a_reconciliation_fill_is_distinguishable_from_a_live_fill(self):
        """The transcript reader's defence against counting an inferred
        reconciliation fill as a live fill
        (`docs/qa/phase3-live-verification.md:757`). `TestEventStubs.order_filled`
        has no `reconciliation` parameter — build directly.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        account = TestExecStubs.cash_account()
        commission = account.calculate_commission(
            instrument=AAPL_EQUITY,
            last_qty=order.quantity,
            last_px=Price.from_str("100.00"),
            liquidity_side=LiquiditySide.TAKER,
        )
        reconciled_fill = OrderFilled(
            trader_id=order.trader_id,
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=VenueOrderId("V-1"),
            account_id=AccountId("SIM-001"),
            trade_id=TradeId("E-RECONCILED-1"),
            position_id=None,
            order_side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            last_qty=Quantity.from_int(100),
            last_px=Price.from_str("100.00"),
            currency=AAPL_EQUITY.quote_currency,
            commission=commission,
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            ts_event=0,
            ts_init=0,
            reconciliation=True,
        )
        live_fill = TestEventStubs.order_filled(order, AAPL_EQUITY, last_qty=Quantity.from_int(50))

        with capture_logs() as logs:
            observer.handle_order_event(reconciled_fill)
            observer.handle_order_event(live_fill)

        filled = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        assert filled[0]["reconciliation"] is True
        assert "reconciliation" not in filled[1]


#: Every dispatch key, mapped to a builder of a representative instance.
#: `TestEveryDispatchedRecordNameIsPinned` derives the emitted-name set from
#: THIS map rather than from a hand-written list, which is what closes the
#: one-directional hole the original `EMITTED_ORDER_EVENTS` pin left open —
#: see that class's docstring.
_DISPATCH_BUILDERS = {
    "OrderInitialized": lambda order: order.init_event,
    "OrderUpdated": lambda order: TestEventStubs.order_updated(order),
    "OrderSubmitted": lambda order: TestEventStubs.order_submitted(order),
    "OrderAccepted": lambda order: TestEventStubs.order_accepted(order),
    "OrderRejected": lambda order: _order_rejected(order, reason="SCAN"),
    "OrderFilled": lambda order: TestEventStubs.order_filled(order, AAPL_EQUITY),
    "OrderCanceled": lambda order: TestEventStubs.order_canceled(order),
    "OrderExpired": lambda order: TestEventStubs.order_expired(order),
    "OrderDenied": lambda order: _order_denied(order, reason="SCAN"),
}


class TestEveryDispatchedRecordNameIsPinned:
    """Closes the one-directional hole in the original membership pin (code
    review 2026-08-30).

    `TestEmittedOrderEventsMembership` compares `EMITTED_ORDER_EVENTS` against
    a literal set built from the *same* imported constants, so it catches a
    name **dropped from the tuple** but not a lifecycle record **never added**
    to it — which is the failure Task 2.3 actually names ("a seventh emitted
    type cannot appear without joining the scan"). Adding a handler that emits
    `order.triggered` would leave that pin green and the NFR26 anti-field scan
    blind to the new record.

    These two derive the answer from the dispatch map itself, the only artifact
    that enumerates emitters, so both directions now go red.
    """

    def test_the_builders_cover_every_dispatch_entry(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        assert set(_DISPATCH_BUILDERS) == set(observer._dispatch), (
            "a dispatch entry was added or removed without updating "
            "_DISPATCH_BUILDERS — update both, deliberately"
        )

    def test_the_emitted_names_are_exactly_emitted_order_events(self):
        emitted: set[str] = set()
        for build in _DISPATCH_BUILDERS.values():
            observer = OrderEventObserver(structlog.get_logger("test"))
            with capture_logs() as logs:
                observer.handle_order_event(build(_make_order()))
            assert [entry for entry in logs if entry["event"] == "order.observer_failed"] == []
            emitted.update(entry["event"] for entry in logs)
        assert emitted == set(EMITTED_ORDER_EVENTS), (
            "the dispatch map emits a record name that is not in "
            "EMITTED_ORDER_EVENTS, so the NFR26 anti-field scan does not cover it"
        )


class TestEveryLifecycleRecordCarriesTsEvent:
    """Task 2.2: "every record also carries `strategy_id` ... and `ts_event`
    as the venue-side nanos". structlog's own `TimeStamper`
    (`src/utils/logging.py:54`) records host log-write time, not the venue
    instant, so without this field the transcript cannot tell a delayed or
    reconciliation-sourced event from a freshly delivered one.
    """

    @pytest.mark.parametrize("event_name", sorted(EMITTED_ORDER_EVENTS))
    def test_the_record_carries_the_venue_side_ts_event(self, event_name):
        observer = OrderEventObserver(structlog.get_logger("test"))
        event = _EVENT_BUILDERS[event_name]()

        with capture_logs() as logs:
            observer.handle_order_event(event)

        matching = [entry for entry in logs if entry["event"] == event_name]
        assert len(matching) == 1
        assert matching[0]["ts_event"] == event.ts_event


class TestSeverityIsPinned:
    """Task 3.3: "`info` for accepted/filled/canceled/expired; `warning` for
    rejected/denied". Only the two warnings were pinned; flipping
    `_log_accepted` to `warning` used to pass.
    """

    @pytest.mark.parametrize(
        ("event_name", "expected"),
        [
            (ACCEPTED_EVENT, "info"),
            (FILLED_EVENT, "info"),
            (CANCELED_EVENT, "info"),
            (EXPIRED_EVENT, "info"),
            (SUBMITTED_EVENT, "info"),
            (REJECTED_EVENT, "warning"),
            (DENIED_EVENT, "warning"),
        ],
    )
    def test_the_record_uses_the_specified_severity(self, event_name, expected):
        observer = OrderEventObserver(structlog.get_logger("test"))

        with capture_logs() as logs:
            observer.handle_order_event(_EVENT_BUILDERS[event_name]())

        matching = [entry for entry in logs if entry["event"] == event_name]
        assert len(matching) == 1
        assert matching[0]["log_level"] == expected


class TestDuplicateFillsAreNotDoubleCounted:
    """Nautilus publishes a fill it has itself REFUSED to apply:
    `ExecutionEngine._apply_event_to_order` catches the duplicate-`trade_id`
    `KeyError` (`execution/engine.pyx:1357-1369`) and returns, but the
    `_msgbus.publish_c` at `:1174-1177` sits outside that guard. So the
    framework's own duplicate-fill protection is invisible downstream and this
    observer must keep its own `trade_ids`.

    Fixture note: `TestEventStubs.order_filled` derives `trade_id` from the
    `client_order_id`, so two stub fills on one order share an execution id
    unless overridden — a shape no venue produces. The partial-fill tests above
    now pass explicit distinct ids for that reason.
    """

    def test_a_redelivered_trade_id_does_not_advance_cum_qty(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        fill = TestEventStubs.order_filled(
            order, AAPL_EQUITY, last_qty=Quantity.from_int(40), trade_id=TradeId("E-1")
        )

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(fill)
            observer.handle_order_event(fill)

        records = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        assert len(records) == 2
        assert records[0]["cum_qty"] == "40"
        assert records[1]["cum_qty"] == "40", "a redelivered fill must not advance the total"

    def test_the_redelivered_record_is_marked_and_the_first_is_not(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        fill = TestEventStubs.order_filled(
            order, AAPL_EQUITY, last_qty=Quantity.from_int(40), trade_id=TradeId("E-1")
        )

        with capture_logs() as logs:
            observer.handle_order_event(fill)
            observer.handle_order_event(fill)

        records = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        assert "duplicate" not in records[0]
        assert records[1]["duplicate"] is True, (
            "NFR21 wants the evidence of a redelivery, not just the right total"
        )

    def test_a_duplicate_after_completion_is_still_detected(self):
        """The reason entries are no longer pruned on completion. A single-fill
        order redelivered once is the commonest shape of the problem, and the
        old completion prune threw away the `trade_ids` that detect it.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        fill = TestEventStubs.order_filled(
            order, AAPL_EQUITY, last_qty=order.quantity, trade_id=TradeId("E-1")
        )

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(fill)
            observer.handle_order_event(fill)

        records = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        assert records[0]["cum_qty"] == records[0]["order_qty"], "the order completed"
        assert records[1]["duplicate"] is True
        assert records[1]["cum_qty"] == records[0]["cum_qty"]
        assert "over_fill" not in records[1], "a duplicate is not an over-fill"


class TestOrderUpdatedRefreshesTheHarvestedQuantity:
    """`order_qty` was harvested once from `OrderInitialized` and never
    refreshed, but `OrderUpdated` carries the order's current quantity and is
    live-published by the IBKR adapter's `openOrder` callback
    (`adapters/interactive_brokers/execution.py:968-978`) and by reconciliation
    (`live/execution_engine.py:1812-1829`).
    """

    def test_a_downsized_order_reports_completion_against_the_new_quantity(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        half = Quantity.from_int(int(order.quantity.as_decimal() / 2))

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(TestEventStubs.order_updated(order, quantity=half))
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=half, trade_id=TradeId("E-1")
                )
            )

        record = [entry for entry in logs if entry["event"] == FILLED_EVENT][0]
        assert record["order_qty"] == str(half.as_decimal())
        assert record["cum_qty"] == record["order_qty"], (
            "a downsized order that fills its new total is complete, not half-filled"
        )
        assert "over_fill" not in record

    def test_an_upsized_order_does_not_report_completion_early(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        doubled = Quantity.from_int(int(order.quantity.as_decimal() * 2))

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(TestEventStubs.order_updated(order, quantity=doubled))
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=order.quantity, trade_id=TradeId("E-1")
                )
            )

        record = [entry for entry in logs if entry["event"] == FILLED_EVENT][0]
        assert record["order_qty"] == str(doubled.as_decimal())
        assert record["cum_qty"] != record["order_qty"], "still working, not complete"

    def test_an_order_updated_still_emits_no_record(self):
        """The Task 2.6 silence pin stays true: the harvest is silent, so
        `OrderUpdated` gains no AR41 name and needs no namespace ruling.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))

        with capture_logs() as logs:
            observer.handle_order_event(TestEventStubs.order_updated(_make_order()))

        assert logs == []


class TestTheAccumulatorIsNeverPruned:
    """Every prune the module used to do destroyed state a later event still
    needed, and none of the four `del`/`pop` statements had a test that could
    fail on its removal. Retention is now asserted through the records, which
    is the only surface a component test can see.
    """

    def test_a_cancel_with_no_prior_fills_keeps_the_order_quantity(self):
        """The common form of the fill-crosses-the-cancel-ack race: a working
        order usually has NO prior fill when the cancel is sent. The old
        `_prune_if_no_fills` deleted exactly this entry, taking the harvested
        `order_qty` with it.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(TestEventStubs.order_canceled(order))
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=order.quantity, trade_id=TradeId("E-1")
                )
            )

        record = [entry for entry in logs if entry["event"] == FILLED_EVENT][0]
        assert record["order_qty"] == str(order.quantity.as_decimal())
        assert record["cum_qty"] == record["order_qty"], (
            "AC #5 finality must stay readable for a fill that crosses a cancel ack"
        )
        assert "order_qty_unknown" not in record

    def test_a_rejection_does_not_discard_the_harvested_quantity(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(_order_rejected(order, reason="TOO_LATE"))
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=order.quantity, trade_id=TradeId("E-1")
                )
            )

        record = [entry for entry in logs if entry["event"] == FILLED_EVENT][0]
        assert record["order_qty"] == str(order.quantity.as_decimal())

    def test_a_denial_does_not_discard_the_harvested_quantity(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(_order_denied(order, reason="RISK"))
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=order.quantity, trade_id=TradeId("E-1")
                )
            )

        record = [entry for entry in logs if entry["event"] == FILLED_EVENT][0]
        assert record["order_qty"] == str(order.quantity.as_decimal())


class TestPartialKnowledgeAndOverFillAreMarked:
    def test_a_fill_with_no_harvest_is_marked_order_qty_unknown(self):
        """Reachable in a live session, not only across a restart: the runner
        starts the node and lets reconciliation run before the observer
        subscribes, and a reconciliation-sourced order never publishes
        `OrderInitialized` at all. Without the marker such a record is
        byte-identical to a first fill on a fresh order.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()

        with capture_logs() as logs:
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=Quantity.from_int(10), trade_id=TradeId("E-1")
                )
            )

        record = [entry for entry in logs if entry["event"] == FILLED_EVENT][0]
        assert record["order_qty_unknown"] is True
        assert "order_qty" not in record

    def test_an_over_fill_is_marked_rather_than_reading_as_exact_completion(self):
        """The completion comparison is `>=`, so an over-fill used to emit a
        record shaped exactly like an exact completion.
        """
        observer = OrderEventObserver(structlog.get_logger("test"))
        order = _make_order()
        too_much = Quantity.from_int(int(order.quantity.as_decimal() + 1))

        with capture_logs() as logs:
            observer.handle_order_event(order.init_event)
            observer.handle_order_event(
                TestEventStubs.order_filled(
                    order, AAPL_EQUITY, last_qty=too_much, trade_id=TradeId("E-1")
                )
            )

        record = [entry for entry in logs if entry["event"] == FILLED_EVENT][0]
        assert record["over_fill"] is True
        assert record["cum_qty"] != record["order_qty"]


class TestObserverFailureNamesTheEventAndTheOrder:
    """The dispatch went from one handler to nine behind a single `except`.
    `stage` + `error_type` alone cannot tell an operator which event type is
    broken: if a builder raises on every fill, no `order.filled` record is ever
    written and the transcript is an undifferentiated stream of
    `error_type=AttributeError`.
    """

    def test_the_failure_record_identifies_the_event_type_and_order(self):
        class _FakeFilled:
            client_order_id = "O-ONLY-THIS-ONE"

        observer = OrderEventObserver(structlog.get_logger("test"))
        fake = _FakeFilled()
        fake.__class__.__name__ = "OrderFilled"

        with capture_logs() as logs:
            observer.handle_order_event(fake)

        failed = [entry for entry in logs if entry["event"] == "order.observer_failed"]
        assert len(failed) == 1
        assert failed[0]["event_type"] == "OrderFilled"
        assert failed[0]["client_order_id"] == "O-ONLY-THIS-ONE"

    def test_the_diagnostic_survives_an_event_with_no_client_order_id(self):
        class _FakeDenied:
            """Nothing at all — the diagnostic must not raise on its own read."""

        observer = OrderEventObserver(structlog.get_logger("test"))
        fake = _FakeDenied()
        fake.__class__.__name__ = "OrderDenied"

        with capture_logs() as logs:
            observer.handle_order_event(fake)  # must not raise

        failed = [entry for entry in logs if entry["event"] == "order.observer_failed"]
        assert len(failed) == 1
        assert failed[0]["event_type"] == "OrderDenied"


class TestContainmentLeavesNoPhantomCumQty:
    """Task 2.7's prescribed shape — "a stub with `last_qty` access raising" —
    rather than an attribute-less object, which raises at the FIRST read and so
    never reaches the accumulate point at all.

    The accumulator used to be mutated before the record was built, so a raise
    in any of the twelve field reads left the counter advanced with no record
    accounting for it, and the next fill logged an inflated `cum_qty`.
    """

    def test_a_raise_after_the_accumulate_point_leaves_no_phantom_total(self):
        real = TestEventStubs.order_filled(
            _make_order(), AAPL_EQUITY, last_qty=Quantity.from_int(30), trade_id=TradeId("E-1")
        )

        class _RaisesOnCommission:
            def __getattr__(self, name):
                if name == "commission":
                    raise RuntimeError("adapter field missing")
                return getattr(real, name)

        broken = _RaisesOnCommission()
        broken.__class__.__name__ = "OrderFilled"
        good = TestEventStubs.order_filled(
            _make_order(), AAPL_EQUITY, last_qty=Quantity.from_int(30), trade_id=TradeId("E-2")
        )

        observer = OrderEventObserver(structlog.get_logger("test"))
        with capture_logs() as logs:
            observer.handle_order_event(broken)  # must not raise
            observer.handle_order_event(good)

        failed = [entry for entry in logs if entry["event"] == "order.observer_failed"]
        records = [entry for entry in logs if entry["event"] == FILLED_EVENT]
        assert len(failed) == 1
        assert len(records) == 1
        assert records[0]["cum_qty"] == "30", (
            "the contained failure must not leave a total no record explains"
        )
