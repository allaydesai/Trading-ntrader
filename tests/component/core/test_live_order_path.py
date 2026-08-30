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
#: Kept as its own literal so a change to either list is a deliberate, visible
#: edit in both files.
#:
#: ⚠️ Review fix, 2026-08-30. The original comment justified the copy with
#: "production code cannot import from `tests/`" — true, but a non-sequitur
#: here: this is a *test* file, `tests/__init__.py` exists, and this very
#: module already does `from tests.component.doubles import TestLiveNode`.
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
        # Deliberately NOT equal to `ts_event` (review fix, 2026-08-30).
        # These were identical, which made the two plausible anchors
        # indistinguishable: mutating `note_bar` from `bar.ts_event` to
        # `bar.ts_init` — the realistic wrong anchor, and the one that
        # *excludes* the delivery lag NFR1 exists to measure — left every
        # test green. Task 9.1's M4 only ever caught `time.time_ns()`, which
        # is off by ~1.8e12 and would have been caught by anything. A real
        # bar's `ts_init` is its arrival instant, strictly after the venue
        # close it reports.
        ts_init=index * 60_000_000_000 + 7_000_000_000,
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
            observer.handle_order_event(self._submitted(traded.ts_event + 500_000_000))

        submitted = [entry for entry in logs if entry["event"] == SUBMITTED_EVENT][0]
        assert submitted["bar_close_to_submit_ms"] == pytest.approx(500.0)
        assert submitted["latency_anchor_bar_type"] == str(BAR_TYPE)

    def test_the_emitted_latency_names_the_bar_type_it_was_measured_from(self):
        observer = OrderEventObserver(structlog.get_logger("test"))
        bar = make_bar(3, "100.00")
        observer.note_bar(bar)

        with capture_logs() as logs:
            observer.handle_order_event(self._submitted(bar.ts_event + 250_000_000))

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
            observer.handle_order_event(self._submitted(make_bar(1, "100.00").ts_event + 1_000_000))

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
            observer.handle_order_event(self._submitted(bar.ts_event + offset_ns))

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


def _make_order():
    return TestExecStubs.market_order(
        instrument=TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ"),
        strategy_id=StrategyId("SMACrossover-000"),
    )
