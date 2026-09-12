"""Component tests for the trade recorder (Story 3.5, Tasks 3 and 4).

Component tier: real ``nautilus_trader.model.position.Position`` objects
built by hand-applying real ``OrderFilled`` events — a stronger double than a
hand-rolled fake (3.3's precedent) — plus ``TestEventStubs.position_closed``.
Never constructs a ``TradingNode``, ``BacktestEngine``, exec client or
socket; the autouse guard below is copied verbatim from
``test_live_order_path.py:91-101``.
"""

import itertools
from decimal import Decimal

import pytest
import structlog
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import EUR, GBP, USD
from nautilus_trader.model.enums import LiquiditySide, OrderSide, OrderType
from nautilus_trader.model.events.order import OrderFilled
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    PositionId,
    StrategyId,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from structlog.testing import capture_logs

from src.core.live_trade_recorder import (
    AGGREGATED_EVENT,
    COMMISSION_MIXED_EVENT,
    EMITTED_TRADE_EVENTS,
    POSITION_EVENTS_TOPIC,
    RECORDER_FAILED_EVENT,
    RecordedTrade,
    TradeRecorder,
    aggregate_closed_position,
    select_commission,
)

pytestmark = pytest.mark.component

AAPL_EQUITY = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
TRADER_ID = TraderId("TESTER-000")
STRATEGY_ID = StrategyId("SMACrossover-000")
ACCOUNT_ID = AccountId("INTERACTIVE_BROKERS-DU4076626")
QUANTUM = Decimal("0.00000001")
_COUNTER = itertools.count()


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_live_order_path.py:91-101``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — this file must never construct a "
        "TradingNode."
    )


def _fill(*, client_order_id, side, qty, px, commission, ts, instrument=AAPL_EQUITY):
    return OrderFilled(
        trader_id=TRADER_ID,
        strategy_id=STRATEGY_ID,
        instrument_id=instrument.id,
        client_order_id=client_order_id,
        venue_order_id=VenueOrderId(f"V-{client_order_id}"),
        account_id=ACCOUNT_ID,
        trade_id=TradeId(f"T-{next(_COUNTER)}"),
        position_id=PositionId(f"{instrument.id}-{STRATEGY_ID}"),
        order_side=side,
        order_type=OrderType.MARKET,
        last_qty=Quantity.from_int(qty),
        last_px=Price.from_str(px),
        currency=instrument.quote_currency,
        commission=commission,
        liquidity_side=LiquiditySide.TAKER,
        event_id=UUID4(),
        ts_event=ts,
        ts_init=ts,
    )


def _round_trip(entry_fills, exit_fills, *, side=OrderSide.BUY, instrument=AAPL_EQUITY):
    """Build a real, closed ``Position`` from ``(qty, px, commission)`` tuples.

    Each leg gets its own ``ClientOrderId`` and every fill its own
    ``TradeId`` — two ``TestExecStubs.market_order`` calls would share one
    frozen ID (3.3:186-189), so the IDs are built explicitly here instead.
    """
    opposite = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
    opening_order_id = ClientOrderId(f"O-{next(_COUNTER)}")
    closing_order_id = ClientOrderId(f"O-{next(_COUNTER)}")

    position = None
    ts = 1_000_000_000
    for qty, px, commission in entry_fills:
        fill = _fill(
            client_order_id=opening_order_id,
            side=side,
            qty=qty,
            px=px,
            commission=commission,
            ts=ts,
            instrument=instrument,
        )
        if position is None:
            position = Position(instrument=instrument, fill=fill)
        else:
            position.apply(fill)
        ts += 500_000_000

    assert position is not None, "entry_fills must not be empty"
    for qty, px, commission in exit_fills:
        fill = _fill(
            client_order_id=closing_order_id,
            side=opposite,
            qty=qty,
            px=px,
            commission=commission,
            ts=ts,
            instrument=instrument,
        )
        position.apply(fill)
        ts += 500_000_000

    return position, TestEventStubs.position_closed(position)


def _open_position(entry_fills, *, side=OrderSide.BUY, instrument=AAPL_EQUITY):
    """A still-open ``Position`` — for ``PositionOpened``/``PositionChanged``,
    which assert internally that the position has not closed.
    """
    opening_order_id = ClientOrderId(f"O-{next(_COUNTER)}")
    position = None
    ts = 1_000_000_000
    for qty, px, commission in entry_fills:
        fill = _fill(
            client_order_id=opening_order_id,
            side=side,
            qty=qty,
            px=px,
            commission=commission,
            ts=ts,
            instrument=instrument,
        )
        if position is None:
            position = Position(instrument=instrument, fill=fill)
        else:
            position.apply(fill)
        ts += 500_000_000
    assert position is not None, "entry_fills must not be empty"
    return position


def _money(amount: str, currency=USD) -> Money:
    return Money(Decimal(amount), currency)


class TestEntryPriceIsVolumeWeighted:
    """AC #1."""

    def test_terminating_vwap(self):
        position, _ = _round_trip(
            entry_fills=[(40, "100.00", _money("1.25")), (60, "110.00", _money("2.50"))],
            exit_fills=[(100, "120.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(position)

        expected = (
            (Decimal(40) * Decimal("100.00") + Decimal(60) * Decimal("110.00")) / Decimal(100)
        ).quantize(QUANTUM)
        assert recorded.trade.entry_price == expected
        assert recorded.trade.entry_price == Decimal("106.00000000")
        assert recorded.trade.entry_price != Decimal("100.00000000")
        assert recorded.trade.entry_price != Decimal("110.00000000")

    def test_non_terminating_vwap(self):
        position, _ = _round_trip(
            entry_fills=[(100, "10.00", _money("0.00")), (200, "10.01", _money("0.00"))],
            exit_fills=[(300, "10.50", _money("0.00"))],
        )
        recorded = aggregate_closed_position(position)

        expected = (
            (Decimal(100) * Decimal("10.00") + Decimal(200) * Decimal("10.01")) / Decimal(300)
        ).quantize(QUANTUM)
        assert recorded.trade.entry_price == expected
        assert recorded.trade.entry_price == Decimal("10.00666667")
        assert recorded.trade.entry_price != Decimal("10.00000000")
        assert recorded.trade.entry_price != Decimal("10.01000000")


class TestExitPriceIsVolumeWeighted:
    """AC #2."""

    def test_terminating_vwap(self):
        position, _ = _round_trip(
            entry_fills=[(100, "100.00", _money("0.00"))],
            exit_fills=[(50, "120.00", _money("1.00")), (50, "130.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(position)

        assert recorded.trade.exit_price == Decimal("125.00000000")
        assert recorded.trade.exit_price != Decimal("120.00000000")
        assert recorded.trade.exit_price != Decimal("130.00000000")

    def test_non_terminating_vwap(self):
        position, _ = _round_trip(
            entry_fills=[(300, "10.00", _money("0.00"))],
            exit_fills=[(100, "10.00", _money("0.00")), (200, "10.01", _money("0.00"))],
        )
        recorded = aggregate_closed_position(position)

        assert recorded.trade.exit_price == Decimal("10.00666667")


class TestMixedRoundTrip:
    """AC #2 — every field, both directions."""

    def test_long_round_trip_every_field(self):
        position, closed_event = _round_trip(
            side=OrderSide.BUY,
            entry_fills=[(40, "100.00", _money("1.25")), (60, "110.00", _money("2.50"))],
            exit_fills=[(50, "120.00", _money("1.00")), (50, "130.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(position)

        assert recorded.trade.entry_price == Decimal("106.00000000")
        assert recorded.trade.exit_price == Decimal("125.00000000")
        assert recorded.trade.quantity == Decimal("100.00000000")
        assert recorded.trade.order_side == "BUY"
        assert recorded.trade.commission_amount == Decimal("5.75")
        assert recorded.trade.commission_currency == "USD"
        assert recorded.fill_count == 4
        assert recorded.trade.trade_id == str(position.id)
        assert recorded.trade.venue_order_id == str(position.opening_order_id)
        assert recorded.trade.client_order_id == str(position.closing_order_id)
        assert recorded.trade.entry_timestamp.tzinfo is not None
        assert recorded.trade.exit_timestamp.tzinfo is not None
        assert recorded.holding_period_seconds == position.duration_ns // 1_000_000_000
        assert recorded.profit_loss == position.realized_pnl.as_decimal()
        expected_pct = (
            (recorded.trade.exit_price - recorded.trade.entry_price) / recorded.trade.entry_price
        ) * Decimal("100")
        assert recorded.profit_pct == expected_pct
        assert recorded.position_id == str(position.id)
        assert recorded.strategy_id == str(position.strategy_id)
        assert recorded.trade_key == f"{position.id}:{position.closing_order_id}"
        assert closed_event.position_id == position.id

    def test_short_round_trip_every_field(self):
        position, _ = _round_trip(
            side=OrderSide.SELL,
            entry_fills=[(40, "100.00", _money("1.25")), (60, "90.00", _money("2.50"))],
            exit_fills=[(50, "80.00", _money("1.00")), (50, "70.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(position)

        assert recorded.trade.order_side == "SELL"
        assert recorded.trade.entry_price == Decimal("94.00000000")
        assert recorded.trade.exit_price == Decimal("75.00000000")


class TestCommissionIsSummedAcrossFills:
    """AC #3."""

    def test_the_575_case(self):
        position, _ = _round_trip(
            entry_fills=[(40, "100.00", _money("1.25")), (60, "110.00", _money("2.50"))],
            exit_fills=[(50, "120.00", _money("1.00")), (50, "130.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(position)

        assert recorded.trade.commission_amount == Decimal("5.75")
        assert recorded.trade.commission_currency == "USD"

    def test_empty_commissions_yield_zero_in_settlement_currency(self):
        position, _ = _round_trip(
            entry_fills=[(10, "100.00", _money("0.00"))],
            exit_fills=[(10, "110.00", _money("0.00"))],
        )
        recorded = aggregate_closed_position(position)

        assert recorded.trade.commission_amount == Decimal("0")
        assert recorded.trade.commission_currency == str(position.settlement_currency)

    def test_mixed_currency_commission_picks_the_settlement_entry_and_warns(self):
        """``Money.as_decimal()`` normalizes away trailing zeros (measured:
        ``Money(Decimal("1.00"), USD).as_decimal() == Decimal("1")``, not
        ``Decimal("1.00")``) — amounts here deliberately avoid a trailing
        zero so the assertions are not tripped by that unrelated quirk.

        The settlement currency (USD) is deliberately on the EXIT leg, not
        the entry — ``position.commissions()`` returns entries in
        insertion order, so a fixture with USD first would pass even under
        the M4 mutation (``commissions()[0]`` unconditionally): the mutant
        and the correct implementation would coincidentally agree. Putting
        the non-settlement currency (EUR) first is what makes this test
        capable of killing that mutation.
        """
        position, closed_event = _round_trip(
            entry_fills=[(50, "100.00", _money("2.46", EUR))],
            exit_fills=[(50, "110.00", _money("1.23", USD))],
        )
        recorder = TradeRecorder(_StubCache({position.id: position}), structlog.get_logger("test"))

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        warnings = [entry for entry in logs if entry["event"] == COMMISSION_MIXED_EVENT]
        assert len(warnings) == 1
        assert warnings[0]["commissions"] == ("2.46 EUR", "1.23 USD")

        aggregated = [entry for entry in logs if entry["event"] == AGGREGATED_EVENT]
        assert len(aggregated) == 1
        assert aggregated[0]["commission"] == "1.23"
        assert aggregated[0]["currency"] == "USD"

    def test_mixed_currency_with_settlement_absent_picks_the_first_and_warns(self):
        position, closed_event = _round_trip(
            entry_fills=[(50, "100.00", _money("1.23", EUR))],
            exit_fills=[(50, "110.00", _money("2.46", GBP))],
        )
        recorder = TradeRecorder(_StubCache({position.id: position}), structlog.get_logger("test"))

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        aggregated = [entry for entry in logs if entry["event"] == AGGREGATED_EVENT]
        assert aggregated[0]["commission"] == "1.23"
        assert aggregated[0]["currency"] == "EUR"

    def test_read_time_pin_a_late_read_gives_a_different_wrong_answer(self):
        """AC #3 pin (iii). The recorder itself always reads synchronously
        inside ``handle_position_event`` — this test proves *why* that
        matters by manufacturing the late read a future refactor could
        introduce and showing it is observably wrong.
        """
        position, closed_event = _round_trip(
            entry_fills=[(40, "100.00", _money("1.25")), (60, "110.00", _money("2.50"))],
            exit_fills=[(50, "120.00", _money("1.00")), (50, "130.00", _money("1.00"))],
        )

        sink_calls: list[RecordedTrade] = []
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=sink_calls.append,
        )
        recorder.handle_position_event(closed_event)

        assert len(sink_calls) == 1
        assert sink_calls[0].trade.commission_amount == Decimal("5.75"), (
            "the synchronous, correctly-timed read"
        )

        # Now mutate the SAME (now-FLAT) position with a fresh opening fill —
        # fact 3's reset-in-place — and read again, late, directly.
        reentry = _fill(
            client_order_id=ClientOrderId(f"O-{next(_COUNTER)}"),
            side=OrderSide.BUY,
            qty=20,
            px="90.00",
            commission=_money("0.50"),
            ts=9_000_000_000,
        )
        position.apply(reentry)
        # `aggregate_closed_position` requires a genuinely CLOSED position
        # (`avg_px_close` must convert to a positive Decimal) — the reset
        # position is open again, so the read-time hazard is demonstrated
        # directly on `commissions()`, the field the hazard is actually about.
        late_amount, _, _ = select_commission(
            position.commissions(), position.settlement_currency.code
        )

        assert late_amount == Decimal("0.50"), (
            "a read AFTER the reset sees only the new leg's commission — "
            "observably different from the correct 5.75, proving the read must not be deferred"
        )


class TestSingleFillEachSideIsTheSimpleCase:
    """AC #4."""

    def test_one_fill_in_one_fill_out_reduces_to_the_simple_case(self):
        position, _ = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(position)

        assert recorded.trade.entry_price == Decimal("100.00000000")
        assert recorded.trade.exit_price == Decimal("110.00000000")
        assert recorded.trade.commission_amount == Decimal("2.00")
        assert recorded.fill_count == 2

    def test_the_float_artifact_price_comes_out_exact(self):
        """M8 (documented in the unit-tier test as the value that actually
        distinguishes ``Decimal(value)`` from ``Decimal(str(value))``): a
        realistic-looking price whose binary repr is not exact.
        """
        position, _ = _round_trip(
            entry_fills=[(10, "100.10", _money("0.00"))],
            exit_fills=[(10, "110.10", _money("0.00"))],
        )
        recorded = aggregate_closed_position(position)

        assert recorded.trade.entry_price == Decimal("100.10000000")
        assert recorded.trade.exit_price == Decimal("110.10000000")


class _StubCache:
    def __init__(self, positions: dict) -> None:
        self._positions = positions

    def position(self, position_id):
        return self._positions.get(position_id)


class TestTheHandlerNeverRaises:
    """AC #5 — the handler contract."""

    def test_position_opened_produces_no_record_and_no_sink_call(self):
        position = _open_position(entry_fills=[(10, "100.00", _money("0.00"))])
        opened_event = TestEventStubs.position_opened(position)
        sink_calls = []
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=sink_calls.append,
        )

        with capture_logs() as logs:
            recorder.handle_position_event(opened_event)

        assert logs == []
        assert sink_calls == []

    def test_position_changed_produces_no_record_and_no_sink_call(self):
        position = _open_position(
            entry_fills=[(40, "100.00", _money("0.00")), (60, "110.00", _money("0.00"))]
        )
        changed_event = TestEventStubs.position_changed(position)
        sink_calls = []
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=sink_calls.append,
        )

        with capture_logs() as logs:
            recorder.handle_position_event(changed_event)

        assert logs == []
        assert sink_calls == []

    def test_position_closed_produces_exactly_one_record_and_one_sink_call_after_it(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        order = []
        sink_calls: list[RecordedTrade] = []

        def sink(recorded: RecordedTrade) -> None:
            order.append("sink")
            sink_calls.append(recorded)

        class _LoggingProxy:
            def info(self, *args, **kwargs):
                order.append("log")
                logger.info(*args, **kwargs)

            def warning(self, *args, **kwargs):
                logger.warning(*args, **kwargs)

            def error(self, *args, **kwargs):
                logger.error(*args, **kwargs)

        logger = structlog.get_logger("test")
        recorder = TradeRecorder(_StubCache({position.id: position}), _LoggingProxy(), sink=sink)

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        aggregated = [entry for entry in logs if entry["event"] == AGGREGATED_EVENT]
        assert len(aggregated) == 1
        assert len(sink_calls) == 1
        assert isinstance(sink_calls[0], RecordedTrade)
        assert order == ["sink", "log"], "the record must be emitted AFTER the sink returns"

    def test_a_raising_sink_produces_one_recorder_failed_no_raise(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )

        def raising_sink(recorded: RecordedTrade) -> None:
            raise RuntimeError("sink boom")

        recorder = TradeRecorder(
            _StubCache({position.id: position}), structlog.get_logger("test"), sink=raising_sink
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)  # must not raise

        failed = [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT]
        assert len(failed) == 1
        assert failed[0]["stage"] == "sink"
        assert failed[0]["position_id"] == str(position.id)
        assert failed[0]["error_type"] == "RuntimeError"
        assert failed[0]["exc_info"] is True
        assert [entry for entry in logs if entry["event"] == AGGREGATED_EVENT] == []

    def test_a_missing_cached_position_produces_one_recorder_failed_no_raise(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(_StubCache({}), structlog.get_logger("test"))

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)  # must not raise

        failed = [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT]
        assert len(failed) == 1
        assert failed[0]["stage"] == "lookup"

    def test_an_unconvertible_avg_px_produces_one_recorder_failed_no_raise(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )

        class _NanPosition:
            def __getattr__(self, name):
                return getattr(position, name)

            @property
            def avg_px_open(self):
                return float("nan")

        recorder = TradeRecorder(
            _StubCache({position.id: _NanPosition()}), structlog.get_logger("test")
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)  # must not raise

        failed = [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT]
        assert len(failed) == 1
        assert failed[0]["stage"] == "aggregate"


class TestNoRecordEverCarriesAnAccountId:
    """NFR26, parametrized from ``EMITTED_TRADE_EVENTS``."""

    @pytest.mark.parametrize("event_name", sorted(EMITTED_TRADE_EVENTS))
    def test_the_record_carries_no_account_id_field(self, event_name):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(_StubCache({position.id: position}), structlog.get_logger("test"))

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        matching = [entry for entry in logs if entry["event"] == event_name]
        assert len(matching) == 1
        assert "account_id" not in matching[0]


class TestEveryDecimalInARecordIsAStr:
    def test_no_decimal_object_reaches_the_aggregated_record(self):
        position, closed_event = _round_trip(
            entry_fills=[(40, "100.00", _money("1.25")), (60, "110.00", _money("2.50"))],
            exit_fills=[(50, "120.00", _money("1.00")), (50, "130.00", _money("1.00"))],
        )
        recorder = TradeRecorder(_StubCache({position.id: position}), structlog.get_logger("test"))

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        aggregated = next(entry for entry in logs if entry["event"] == AGGREGATED_EVENT)
        assert not any(isinstance(value, Decimal) for value in aggregated.values())
        assert "ts_event" in aggregated


class TestSeverityIsPinned:
    def test_aggregated_is_info_recorder_failed_is_error_mixed_currency_is_warning(self):
        position, closed_event = _round_trip(
            entry_fills=[(50, "100.00", _money("1.00", USD))],
            exit_fills=[(50, "110.00", _money("2.00", EUR))],
        )
        recorder = TradeRecorder(_StubCache({position.id: position}), structlog.get_logger("test"))

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        by_event = {entry["event"]: entry for entry in logs}
        assert by_event[AGGREGATED_EVENT]["log_level"] == "info"
        assert by_event[COMMISSION_MIXED_EVENT]["log_level"] == "warning"

        missing_position, missing_closed = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        failing_recorder = TradeRecorder(_StubCache({}), structlog.get_logger("test"))
        with capture_logs() as failure_logs:
            failing_recorder.handle_position_event(missing_closed)
        assert failure_logs[0]["log_level"] == "error"


class TestEmittedTradeEventsMembership:
    """Membership-pinned (CLAUDE.md Anti-Patterns)."""

    def test_the_set_is_pinned_exactly(self):
        assert EMITTED_TRADE_EVENTS == (AGGREGATED_EVENT,)


def _build_position_opened():
    position = _open_position(entry_fills=[(10, "100.00", _money("0.00"))])
    return position, TestEventStubs.position_opened(position)


def _build_position_changed():
    position = _open_position(
        entry_fills=[(10, "100.00", _money("0.00")), (10, "110.00", _money("0.00"))]
    )
    return position, TestEventStubs.position_changed(position)


def _build_position_closed():
    return _round_trip(
        entry_fills=[(10, "100.00", _money("0.00"))],
        exit_fills=[(10, "110.00", _money("0.00"))],
    )


#: Every event type the recorder's own ``_dispatch`` map distinguishes,
#: each with a zero-arg builder of a representative ``(position, event)``
#: pair — the NFR26 two-directional pin iterates this.
_DISPATCH_BUILDERS = {
    "PositionOpened": _build_position_opened,
    "PositionChanged": _build_position_changed,
    "PositionClosed": _build_position_closed,
}


class TestEveryDispatchedRecordNameIsPinned:
    """The two-directional pin CLAUDE.md requires — derives the emitted set
    from the recorder's own ``_dispatch`` map rather than a hand-written
    list, so a name never added cannot escape the NFR26 scan either.
    """

    def test_the_builders_cover_every_dispatch_entry(self):
        position, _ = _build_position_closed()
        recorder = TradeRecorder(_StubCache({position.id: position}), structlog.get_logger("test"))
        assert set(_DISPATCH_BUILDERS) == set(recorder._dispatch), (
            "a dispatch entry was added or removed without updating _DISPATCH_BUILDERS"
        )

    def test_the_emitted_names_are_exactly_emitted_trade_events(self):
        emitted: set[str] = set()
        for build in _DISPATCH_BUILDERS.values():
            position, event = build()
            recorder = TradeRecorder(
                _StubCache({position.id: position}), structlog.get_logger("test")
            )
            with capture_logs() as logs:
                recorder.handle_position_event(event)
            assert [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT] == []
            emitted.update(
                entry["event"] for entry in logs if entry["event"] != COMMISSION_MIXED_EVENT
            )
        assert emitted == set(EMITTED_TRADE_EVENTS)


class TestPositionEventsTopicIsTheAr41Wildcard:
    def test_the_literal_is_exact(self):
        assert POSITION_EVENTS_TOPIC == "events.position*"
