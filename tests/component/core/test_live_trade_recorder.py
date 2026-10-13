"""Component tests for the trade recorder (Story 3.5, Tasks 3 and 4).

Component tier: real ``nautilus_trader.model.position.Position`` objects
built by hand-applying real ``OrderFilled`` events — a stronger double than a
hand-rolled fake (3.3's precedent) — plus ``TestEventStubs.position_closed``.
Never constructs a ``TradingNode``, ``BacktestEngine``, exec client or
socket; the autouse guard below is copied verbatim from
``test_live_order_path.py:91-101``.
"""

import itertools
import threading
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

from src.core.live_session_record import SessionReclaimedError
from src.core.live_trade_recorder import (
    AGGREGATED_EVENT,
    COMMISSION_MIXED_EVENT,
    COMMISSION_UNAVAILABLE_EVENT,
    EMITTED_TRADE_EVENTS,
    PERSIST_DROPPED_EVENT,
    PERSIST_REFUSED_EVENT,
    PERSIST_SKIPPED_EVENT,
    PERSISTED_EVENT,
    POSITION_EVENTS_TOPIC,
    RECORDER_FAILED_EVENT,
    RecordedTrade,
    TradeRecorder,
    aggregate_closed_position,
    position_vouches_for,
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
        position, closed_event = _round_trip(
            entry_fills=[(40, "100.00", _money("1.25")), (60, "110.00", _money("2.50"))],
            exit_fills=[(100, "120.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

        expected = (
            (Decimal(40) * Decimal("100.00") + Decimal(60) * Decimal("110.00")) / Decimal(100)
        ).quantize(QUANTUM)
        assert recorded.trade.entry_price == expected
        assert recorded.trade.entry_price == Decimal("106.00000000")
        assert recorded.trade.entry_price != Decimal("100.00000000")
        assert recorded.trade.entry_price != Decimal("110.00000000")

    def test_non_terminating_vwap(self):
        position, closed_event = _round_trip(
            entry_fills=[(100, "10.00", _money("0.00")), (200, "10.01", _money("0.00"))],
            exit_fills=[(300, "10.50", _money("0.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

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
        position, closed_event = _round_trip(
            entry_fills=[(100, "100.00", _money("0.00"))],
            exit_fills=[(50, "120.00", _money("1.00")), (50, "130.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

        assert recorded.trade.exit_price == Decimal("125.00000000")
        assert recorded.trade.exit_price != Decimal("120.00000000")
        assert recorded.trade.exit_price != Decimal("130.00000000")

    def test_non_terminating_vwap(self):
        position, closed_event = _round_trip(
            entry_fills=[(300, "10.00", _money("0.00"))],
            exit_fills=[(100, "10.00", _money("0.00")), (200, "10.01", _money("0.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

        assert recorded.trade.exit_price == Decimal("10.00666667")


class TestMixedRoundTrip:
    """AC #2 — every field, both directions."""

    def test_long_round_trip_every_field(self):
        position, closed_event = _round_trip(
            side=OrderSide.BUY,
            entry_fills=[(40, "100.00", _money("1.25")), (60, "110.00", _money("2.50"))],
            exit_fills=[(50, "120.00", _money("1.00")), (50, "130.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

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
        position, closed_event = _round_trip(
            side=OrderSide.SELL,
            entry_fills=[(40, "100.00", _money("1.25")), (60, "90.00", _money("2.50"))],
            exit_fills=[(50, "80.00", _money("1.00")), (50, "70.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

        assert recorded.trade.order_side == "SELL"
        assert recorded.trade.entry_price == Decimal("94.00000000")
        assert recorded.trade.exit_price == Decimal("75.00000000")


class TestCommissionIsSummedAcrossFills:
    """AC #3."""

    def test_the_575_case(self):
        position, closed_event = _round_trip(
            entry_fills=[(40, "100.00", _money("1.25")), (60, "110.00", _money("2.50"))],
            exit_fills=[(50, "120.00", _money("1.00")), (50, "130.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

        assert recorded.trade.commission_amount == Decimal("5.75")
        assert recorded.trade.commission_currency == "USD"

    def test_zero_amount_commissions_yield_zero_in_settlement_currency(self):
        """AC #3 pin (i), at the tier a real ``Position`` allows. Zero-amount
        fills do NOT produce an empty ``commissions()`` — Nautilus returns
        ``[Money(0.00, USD)]`` (measured 2026-09-12, review) — so this goes
        through ``select_commission``'s single-entry path, not its empty
        branch. A real ``Position`` cannot yield ``[]`` at all; the empty
        branch is pinned only at the unit tier, with a hand-rolled ``[]``.
        """
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("0.00"))],
            exit_fills=[(10, "110.00", _money("0.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

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

        warnings = [entry for entry in logs if entry["event"] == COMMISSION_MIXED_EVENT]
        assert len(warnings) == 1, "the settlement-absent fallback to [0] must still be loud"
        assert warnings[0]["commissions"] == ("1.23 EUR", "2.46 GBP")

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
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

        assert recorded.trade.entry_price == Decimal("100.00000000")
        assert recorded.trade.exit_price == Decimal("110.00000000")
        assert recorded.trade.commission_amount == Decimal("2.00")
        assert recorded.fill_count == 2

    def test_the_float_artifact_price_comes_out_exact(self):
        """M8 (documented in the unit-tier test as the value that actually
        distinguishes ``Decimal(value)`` from ``Decimal(str(value))``): a
        realistic-looking price whose binary repr is not exact.
        """
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.10", _money("0.00"))],
            exit_fills=[(10, "110.10", _money("0.00"))],
        )
        recorded = aggregate_closed_position(closed_event, position)

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

    def test_position_closed_produces_aggregated_then_sink_then_persisted(self):
        """D-E (3.5 review resolution): ``trade.aggregated`` is a statement
        about aggregation, which always succeeds by this point — it is
        emitted BEFORE the sink, so a raising sink cannot erase the computed
        values from the transcript. ``trade.persisted`` is the one that must
        never claim success for a failed write, so it comes after.
        """
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        order = []
        sink_calls: list[RecordedTrade] = []
        sink_threads: list[threading.Thread] = []

        def sink(recorded: RecordedTrade) -> bool:
            order.append("sink")
            sink_calls.append(recorded)
            sink_threads.append(threading.current_thread())
            return True

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
        persisted = [entry for entry in logs if entry["event"] == PERSISTED_EVENT]
        assert len(aggregated) == 1
        assert len(persisted) == 1
        assert persisted[0]["inserted"] == "True"
        assert persisted[0]["attempt"] == "first"
        assert len(sink_calls) == 1
        assert isinstance(sink_calls[0], RecordedTrade)
        assert order == ["log", "sink", "log"], "aggregated before the sink, persisted after"
        # AC #1: "immediately" — inside the handler call, on the caller's own
        # thread (no task, no executor, no thread).
        assert sink_threads == [threading.current_thread()]

    def test_a_raising_sink_produces_one_recorder_failed_no_raise(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )

        def raising_sink(recorded: RecordedTrade) -> bool:
            raise RuntimeError("sink boom")

        recorder = TradeRecorder(
            _StubCache({position.id: position}), structlog.get_logger("test"), sink=raising_sink
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)  # must not raise

        failed = [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT]
        assert len(failed) == 1
        assert failed[0]["stage"] == "sink"
        assert failed[0]["trade_key"] == f"{position.id}:{closed_event.closing_order_id}"
        assert failed[0]["error_type"] == "RuntimeError"
        assert failed[0]["pending"] == "1"
        # Review 2026-09-12: the record is logged after `_attempt_sink`'s
        # `except` block has returned, where `exc_info=True` would attach
        # nothing — the exception object itself must be what is bound.
        assert isinstance(failed[0]["exc_info"], RuntimeError)
        assert str(failed[0]["exc_info"]) == "sink boom"
        # D-E: aggregated is emitted regardless of what the sink then does.
        assert len(logs) == 2
        assert [entry for entry in logs if entry["event"] == AGGREGATED_EVENT] != []
        assert [entry for entry in logs if entry["event"] == PERSISTED_EVENT] == []

    def test_a_missing_cached_position_records_the_leg_with_commission_unknown(self):
        """Review 2026-09-12 (flip decision, option 1): every snapshot field
        lives on the event, so a cache miss no longer costs the trade — only
        its commission, which is marked unknown, loudly.
        """
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        sink_calls: list[RecordedTrade] = []
        recorder = TradeRecorder(
            _StubCache({}), structlog.get_logger("test"), sink=sink_calls.append
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)  # must not raise

        assert [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT] == []
        unavailable = [entry for entry in logs if entry["event"] == COMMISSION_UNAVAILABLE_EVENT]
        assert len(unavailable) == 1
        assert unavailable[0]["reason"] == "no cached position"
        assert unavailable[0]["position_id"] == str(position.id)
        aggregated = [entry for entry in logs if entry["event"] == AGGREGATED_EVENT]
        assert len(aggregated) == 1
        assert aggregated[0]["commission"] is None
        assert aggregated[0]["currency"] is None
        assert aggregated[0]["fill_count"] is None
        assert aggregated[0]["exit_price"] == "110.00000000"
        assert len(sink_calls) == 1
        assert sink_calls[0].trade.commission_amount is None
        assert sink_calls[0].fill_count is None

    def test_a_cached_position_that_is_not_the_closed_leg_is_not_trusted(self):
        """The flip shape, at the double level: the cache answers with an
        OPEN position under the same id (``closing_order_id`` is ``None``).
        ``TestAgainstARealExecutionEngine`` proves the real engine produces
        exactly this; here the guard is pinned in isolation.
        """
        closed_position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        reopened = _open_position(entry_fills=[(5, "120.00", _money("9.99"))])
        assert not position_vouches_for(closed_event, reopened)
        assert position_vouches_for(closed_event, closed_position)
        recorder = TradeRecorder(
            _StubCache({closed_event.position_id: reopened}), structlog.get_logger("test")
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        assert [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT] == []
        unavailable = [entry for entry in logs if entry["event"] == COMMISSION_UNAVAILABLE_EVENT]
        assert len(unavailable) == 1
        assert "re-opened" in unavailable[0]["reason"]
        aggregated = [entry for entry in logs if entry["event"] == AGGREGATED_EVENT]
        assert len(aggregated) == 1
        assert aggregated[0]["commission"] is None, "9.99 belongs to the new leg, never to this one"

    def test_an_unconvertible_avg_px_produces_one_recorder_failed_no_raise(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )

        class PositionClosed:  # the class NAME is what the dispatch map keys on
            def __getattr__(self, name):
                return getattr(closed_event, name)

            @property
            def avg_px_open(self):
                return float("nan")

        recorder = TradeRecorder(_StubCache({position.id: position}), structlog.get_logger("test"))

        with capture_logs() as logs:
            recorder.handle_position_event(PositionClosed())  # must not raise

        failed = [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT]
        assert len(failed) == 1
        assert failed[0]["stage"] == "aggregate"
        assert failed[0]["error_type"] == "ValueError"
        assert [entry for entry in logs if entry["event"] == AGGREGATED_EVENT] == []


class TestNoRecordEverCarriesAnAccountId:
    """NFR26, parametrized from ``EMITTED_TRADE_EVENTS``."""

    @pytest.mark.parametrize("event_name", sorted(EMITTED_TRADE_EVENTS))
    def test_the_record_carries_no_account_id_field(self, event_name):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=lambda recorded: True,
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        matching = [entry for entry in logs if entry["event"] == event_name]
        assert len(matching) == 1
        assert "account_id" not in matching[0]


def _diagnostic_record_scenarios():
    """Every diagnostic/boundary record the persistence path emits, each
    produced by driving the recorder — parametrized so the NFR26 scan covers
    them alongside ``EMITTED_TRADE_EVENTS`` (review 2026-09-12, AC #6
    "extended to every new record").
    """

    def _reclaiming(recorded):
        raise SessionReclaimedError("reclaimed")

    def _invalid(recorded):
        raise ValueError("bad record")

    def _failing(recorded):
        raise RuntimeError("db down")

    return [
        pytest.param(PERSIST_REFUSED_EVENT, _reclaiming, None, id="persist_refused"),
        pytest.param(PERSIST_DROPPED_EVENT, _invalid, None, id="persist_dropped"),
        pytest.param(RECORDER_FAILED_EVENT, _failing, None, id="recorder_failed_sink"),
        pytest.param(PERSIST_SKIPPED_EVENT, lambda r: True, "EXTERNAL", id="persist_skipped"),
    ]


class TestNoDiagnosticRecordEverCarriesAnAccountId:
    @pytest.mark.parametrize("event_name, sink, strategy_id", _diagnostic_record_scenarios())
    def test_the_record_carries_no_account_id_field(self, event_name, sink, strategy_id):
        position, real_closed = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        closed_event = real_closed
        if strategy_id is not None:
            closed_event = _with_strategy_id(real_closed, strategy_id)
        recorder = TradeRecorder(
            _StubCache({position.id: position}), structlog.get_logger("test"), sink=sink
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        matching = [entry for entry in logs if entry["event"] == event_name]
        assert len(matching) == 1
        assert "account_id" not in matching[0]
        assert matching[0]["trade_key"] == f"{position.id}:{real_closed.closing_order_id}"

    def test_the_ownership_lost_skip_carries_no_account_id_field(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=lambda recorded: True,
        )
        recorder._reclaimed = True

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        skipped = [entry for entry in logs if entry["event"] == PERSIST_SKIPPED_EVENT]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "ownership_lost"
        assert "account_id" not in skipped[0]


def _with_strategy_id(real_closed, strategy_id: str):
    """A ``PositionClosed`` proxy whose ``strategy_id`` is overridden — the
    class NAME is what the dispatch map keys on."""

    class PositionClosed:
        def __getattr__(self, name):
            return getattr(real_closed, name)

        @property
        def strategy_id(self):
            return StrategyId(strategy_id)

    return PositionClosed()


class TestRecordNameLiteralsArePinned:
    """3.3's M3 lesson: every other test compares a record to the imported
    constant and cannot see a misspelt constant. These literals can.
    """

    def test_the_persistence_record_names_are_exactly_these_strings(self):
        assert PERSISTED_EVENT == "trade.persisted"
        assert PERSIST_REFUSED_EVENT == "trade.persist_refused"
        assert PERSIST_SKIPPED_EVENT == "trade.persist_skipped"
        assert PERSIST_DROPPED_EVENT == "trade.persist_dropped"
        assert AGGREGATED_EVENT == "trade.aggregated"
        assert RECORDER_FAILED_EVENT == "trade.recorder_failed"


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
        unavailable_recorder = TradeRecorder(_StubCache({}), structlog.get_logger("test"))
        with capture_logs() as unavailable_logs:
            unavailable_recorder.handle_position_event(missing_closed)
        by_event = {entry["event"]: entry for entry in unavailable_logs}
        assert by_event[COMMISSION_UNAVAILABLE_EVENT]["log_level"] == "warning"

        def raising_sink(recorded: RecordedTrade) -> bool:
            raise RuntimeError("sink boom")

        failing_recorder = TradeRecorder(
            _StubCache({missing_position.id: missing_position}),
            structlog.get_logger("test"),
            sink=raising_sink,
        )
        with capture_logs() as failure_logs:
            failing_recorder.handle_position_event(missing_closed)
        by_event = {entry["event"]: entry for entry in failure_logs}
        assert by_event[AGGREGATED_EVENT]["log_level"] == "info"
        assert by_event[RECORDER_FAILED_EVENT]["log_level"] == "error"

    def test_persisted_is_info_persist_refused_and_persist_skipped_are_warning(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=lambda recorded: True,
        )
        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)
        assert {e["event"]: e["log_level"] for e in logs}[PERSISTED_EVENT] == "info"

        def reclaiming_sink(recorded: RecordedTrade) -> bool:
            raise SessionReclaimedError("reclaimed")

        refused_position, refused_closed = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        refused_recorder = TradeRecorder(
            _StubCache({refused_position.id: refused_position}),
            structlog.get_logger("test"),
            sink=reclaiming_sink,
        )
        with capture_logs() as refused_logs:
            refused_recorder.handle_position_event(refused_closed)
        assert {e["event"]: e["log_level"] for e in refused_logs}[
            PERSIST_REFUSED_EVENT
        ] == "warning"

        external_position, real_closed = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )

        class PositionClosed:  # the class NAME is what the dispatch map keys on
            def __getattr__(self, name):
                return getattr(real_closed, name)

            @property
            def strategy_id(self):
                return StrategyId("EXTERNAL")

        external_closed = PositionClosed()
        skipped_recorder = TradeRecorder(
            _StubCache({external_position.id: external_position}),
            structlog.get_logger("test"),
            sink=lambda recorded: True,
        )
        with capture_logs() as skipped_logs:
            skipped_recorder.handle_position_event(external_closed)
        assert {e["event"]: e["log_level"] for e in skipped_logs}[
            PERSIST_SKIPPED_EVENT
        ] == "warning"


class TestEmittedTradeEventsMembership:
    """Membership-pinned (CLAUDE.md Anti-Patterns)."""

    def test_the_set_is_pinned_exactly(self):
        assert EMITTED_TRADE_EVENTS == (AGGREGATED_EVENT, PERSISTED_EVENT)


class TestNoSinkConfigured:
    """The 3.5 production state (before this story wires a real sink)."""

    def test_aggregated_only_no_persisted_nothing_queued(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(_StubCache({position.id: position}), structlog.get_logger("test"))

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        assert [e["event"] for e in logs] == [AGGREGATED_EVENT]
        assert recorder.flush_pending() == 0


class TestSinkReturnsFalse:
    def test_inserted_is_false_severity_still_info(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=lambda recorded: False,
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        persisted = next(e for e in logs if e["event"] == PERSISTED_EVENT)
        assert persisted["inserted"] == "False"
        assert persisted["log_level"] == "info"


class TestPendingRetryAndOwnershipLoss:
    """AC #4 (queue + FIFO retry) and AC #8b (a reclaim is terminal)."""

    def test_a_failed_sink_is_retried_on_the_next_event_and_succeeds(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        calls = {"n": 0}

        def flaky_sink(recorded: RecordedTrade) -> bool:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db down")
            return True

        recorder = TradeRecorder(
            _StubCache({position.id: position}), structlog.get_logger("test"), sink=flaky_sink
        )
        with capture_logs():
            recorder.handle_position_event(closed_event)
        assert calls["n"] == 1

        opened_position = _open_position(entry_fills=[(5, "50.00", _money("0.00"))])
        opened_event = TestEventStubs.position_opened(opened_position)
        with capture_logs() as logs:
            recorder.handle_position_event(opened_event)

        assert calls["n"] == 2
        persisted = [e for e in logs if e["event"] == PERSISTED_EVENT]
        assert len(persisted) == 1
        assert persisted[0]["attempt"] == "retry"
        assert persisted[0]["trade_key"] == f"{position.id}:{closed_event.closing_order_id}"
        # PositionOpened itself still produces no record of its own.
        assert [e for e in logs if e["event"] not in (PERSISTED_EVENT,)] == []

    def test_fifo_order_and_stop_on_first_failure(self):
        msft = TestInstrumentProvider.equity(symbol="MSFT", venue="NASDAQ")
        first_position, first_closed = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        second_position, second_closed = _round_trip(
            entry_fills=[(20, "200.00", _money("1.00"))],
            exit_fills=[(20, "210.00", _money("1.00"))],
            instrument=msft,
        )
        first_key = f"{first_position.id}:{first_closed.closing_order_id}"
        second_key = f"{second_position.id}:{second_closed.closing_order_id}"
        recorder = TradeRecorder(
            _StubCache({first_position.id: first_position, second_position.id: second_position}),
            structlog.get_logger("test"),
            sink=lambda recorded: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        with capture_logs():
            recorder.handle_position_event(first_closed)
        with capture_logs():
            recorder.handle_position_event(second_closed)
        assert len(recorder._pending) == 2
        assert recorder._pending[0].trade_key == first_key
        assert recorder._pending[1].trade_key == second_key

        attempts: list[str] = []

        def fails_first_then_succeeds(recorded: RecordedTrade) -> bool:
            attempts.append(recorded.trade_key)
            if recorded.trade_key == first_key:
                raise RuntimeError("still down")
            return True

        recorder._sink = fails_first_then_succeeds
        remaining = recorder.flush_pending()

        assert attempts == [first_key]
        assert remaining == 2, "the first item's failure stops the drain; nothing after it is tried"

    def test_a_reclaimed_write_is_not_retried_and_calls_ownership_lost_once(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        lost_calls = []

        def reclaiming_sink(recorded: RecordedTrade) -> bool:
            raise SessionReclaimedError("reclaimed")

        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=reclaiming_sink,
            on_ownership_lost=lambda: lost_calls.append(1),
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_event)

        refused = [e for e in logs if e["event"] == PERSIST_REFUSED_EVENT]
        assert len(refused) == 1
        assert refused[0]["trade_key"] == f"{position.id}:{closed_event.closing_order_id}"
        assert [e for e in logs if e["event"] == RECORDER_FAILED_EVENT] == []
        assert recorder._pending == []
        assert lost_calls == [1]

    def test_the_sink_is_never_called_again_after_a_reclaim(self):
        """Review 2026-09-12: "called at most once" is now enforced by a
        latch. A second close after the reclaim is aggregated and logged,
        then skipped loudly — no second sink call, no second callback.
        """
        position_one, closed_one = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        position_two, closed_two = _round_trip(
            entry_fills=[(5, "50.00", _money("1.00"))],
            exit_fills=[(5, "55.00", _money("1.00"))],
        )
        lost_calls = []
        sink_calls = []

        def reclaiming_sink(recorded: RecordedTrade) -> bool:
            sink_calls.append(recorded.trade_key)
            raise SessionReclaimedError("reclaimed")

        recorder = TradeRecorder(
            _StubCache({position_one.id: position_one, position_two.id: position_two}),
            structlog.get_logger("test"),
            sink=reclaiming_sink,
            on_ownership_lost=lambda: lost_calls.append(1),
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_one)
            recorder.handle_position_event(closed_two)

        assert len(sink_calls) == 1
        assert lost_calls == [1]
        assert len([e for e in logs if e["event"] == AGGREGATED_EVENT]) == 2
        assert len([e for e in logs if e["event"] == PERSIST_REFUSED_EVENT]) == 1
        skipped = [e for e in logs if e["event"] == PERSIST_SKIPPED_EVENT]
        assert len(skipped) == 1
        assert skipped[0]["reason"] == "ownership_lost"
        assert skipped[0]["trade_key"] == f"{position_two.id}:{closed_two.closing_order_id}"
        assert recorder._pending == []

    def test_a_reclaim_found_while_draining_stops_the_same_delivery_from_hitting_the_sink(self):
        """Two queued trades, then a reclaim on the pre-dispatch drain: the
        first pending entry is refused (and discarded), the second stays
        queued and is named by ``pending_trade_keys``, the delivery's own
        close never reaches the sink, and the callback fires exactly once.
        """
        position_a, closed_a = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        position_b, closed_b = _round_trip(
            entry_fills=[(5, "50.00", _money("1.00"))],
            exit_fills=[(5, "55.00", _money("1.00"))],
        )
        position_c, closed_c = _round_trip(
            entry_fills=[(1, "10.00", _money("1.00"))],
            exit_fills=[(1, "11.00", _money("1.00"))],
        )
        cache = _StubCache(
            {position_a.id: position_a, position_b.id: position_b, position_c.id: position_c}
        )
        lost_calls = []
        recorder = TradeRecorder(
            cache,
            structlog.get_logger("test"),
            sink=lambda recorded: (_ for _ in ()).throw(RuntimeError("db down")),
            on_ownership_lost=lambda: lost_calls.append(1),
        )
        with capture_logs():
            recorder.handle_position_event(closed_a)
            recorder.handle_position_event(closed_b)
        assert len(recorder._pending) == 2

        sink_calls = []

        def reclaiming_sink(recorded: RecordedTrade) -> bool:
            sink_calls.append(recorded.trade_key)
            raise SessionReclaimedError("reclaimed")

        recorder._sink = reclaiming_sink
        with capture_logs() as logs:
            recorder.handle_position_event(closed_c)

        assert sink_calls == [f"{position_a.id}:{closed_a.closing_order_id}"]
        assert lost_calls == [1]
        assert recorder.pending_trade_keys == (f"{position_b.id}:{closed_b.closing_order_id}",)
        skipped = [e for e in logs if e["event"] == PERSIST_SKIPPED_EVENT]
        assert [e["trade_key"] for e in skipped] == [f"{position_c.id}:{closed_c.closing_order_id}"]

    def test_an_invalid_record_is_dropped_loudly_and_never_queued(self):
        """Review 2026-09-12 (decision 2): a ``ValueError``/``TypeError`` from
        the sink is permanent (``TradeCreate`` validation, a caller bug), so
        queueing it would head-of-line-block every later trade for the life
        of the session. It is dropped with every aggregated field and the
        exception attached; a later close still persists.
        """
        position_one, closed_one = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        position_two, closed_two = _round_trip(
            entry_fills=[(5, "50.00", _money("1.00"))],
            exit_fills=[(5, "55.00", _money("1.00"))],
        )
        outcomes = iter([ValueError("quantity must be positive"), True])

        def sink(recorded: RecordedTrade) -> bool:
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        recorder = TradeRecorder(
            _StubCache({position_one.id: position_one, position_two.id: position_two}),
            structlog.get_logger("test"),
            sink=sink,
        )

        with capture_logs() as logs:
            recorder.handle_position_event(closed_one)
            recorder.handle_position_event(closed_two)

        dropped = [e for e in logs if e["event"] == PERSIST_DROPPED_EVENT]
        assert len(dropped) == 1
        assert dropped[0]["log_level"] == "error"
        assert dropped[0]["reason"] == "invalid_record"
        assert dropped[0]["error_type"] == "ValueError"
        assert dropped[0]["trade_key"] == f"{position_one.id}:{closed_one.closing_order_id}"
        assert dropped[0]["entry_price"] == "100.00000000"
        assert isinstance(dropped[0]["exc_info"], ValueError)
        assert [e for e in logs if e["event"] == RECORDER_FAILED_EVENT] == []
        assert recorder._pending == []
        persisted = [e for e in logs if e["event"] == PERSISTED_EVENT]
        assert [e["trade_key"] for e in persisted] == [
            f"{position_two.id}:{closed_two.closing_order_id}"
        ]

    def test_a_retry_failure_carries_the_exception_not_a_bare_true(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=lambda recorded: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        with capture_logs():
            recorder.handle_position_event(closed_event)

        recorder._sink = lambda recorded: (_ for _ in ()).throw(OSError("still down"))
        with capture_logs() as logs:
            remaining = recorder.flush_pending()

        assert remaining == 1
        failed = [e for e in logs if e["event"] == RECORDER_FAILED_EVENT]
        assert len(failed) == 1
        assert failed[0]["error_type"] == "OSError"
        assert isinstance(failed[0]["exc_info"], OSError)

    def test_flush_pending_drains_without_an_event_and_returns_the_remaining_count(self):
        position, closed_event = _round_trip(
            entry_fills=[(10, "100.00", _money("1.00"))],
            exit_fills=[(10, "110.00", _money("1.00"))],
        )
        recorder = TradeRecorder(
            _StubCache({position.id: position}),
            structlog.get_logger("test"),
            sink=lambda recorded: (_ for _ in ()).throw(RuntimeError("db down")),
        )
        with capture_logs():
            recorder.handle_position_event(closed_event)
        assert len(recorder._pending) == 1

        recorder._sink = lambda recorded: True
        remaining = recorder.flush_pending()

        assert remaining == 0
        assert recorder._pending == []


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
                _StubCache({position.id: position}),
                structlog.get_logger("test"),
                sink=lambda recorded: True,
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


# --------------------------------------------------------------------------
# Review 2026-09-12: the recorder driven by a real ``ExecutionEngine`` over a
# real ``MessageBus`` — the only fixture that reproduces Nautilus's deferred
# position-event publication (``execution/engine.pyx:1171-1186``) and the flip
# path (``:1516-1600``), which hand-applied fills cannot.
# --------------------------------------------------------------------------


def _engine_stack():
    from nautilus_trader.common.component import MessageBus, TestClock
    from nautilus_trader.execution.engine import ExecutionEngine
    from nautilus_trader.portfolio.portfolio import Portfolio
    from nautilus_trader.test_kit.stubs.component import TestComponentStubs
    from nautilus_trader.test_kit.stubs.execution import TestExecStubs
    from nautilus_trader.trading.strategy import Strategy

    clock = TestClock()
    msgbus = MessageBus(trader_id=TRADER_ID, clock=clock)
    cache = TestComponentStubs.cache()
    portfolio = Portfolio(msgbus=msgbus, cache=cache, clock=clock)
    engine = ExecutionEngine(msgbus=msgbus, cache=cache, clock=clock)
    cache.add_instrument(AAPL_EQUITY)
    account = TestExecStubs.cash_account()
    cache.add_account(account)
    portfolio.update_account(TestEventStubs.cash_account_state())
    strategy = Strategy()
    strategy.register(
        trader_id=TRADER_ID, portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock
    )
    engine.start()
    return msgbus, cache, engine, strategy, account, clock


def _engine_fill(stack, side, qty, px, commission):
    """Submit-accept-fill one market order through the real engine; return its
    ``ClientOrderId``. The engine assigns the NETTING ``PositionId`` itself."""
    _, cache, engine, strategy, account, clock = stack
    order = strategy.order_factory.market(AAPL_EQUITY.id, side, Quantity.from_int(qty))
    order.apply(TestEventStubs.order_submitted(order))
    cache.add_order(order, None)
    order.apply(TestEventStubs.order_accepted(order))
    cache.update_order(order)
    fill = OrderFilled(
        trader_id=order.trader_id,
        strategy_id=order.strategy_id,
        instrument_id=AAPL_EQUITY.id,
        client_order_id=order.client_order_id,
        venue_order_id=VenueOrderId(f"V-{order.client_order_id}"),
        account_id=account.id,
        trade_id=TradeId(f"T-{next(_COUNTER)}"),
        position_id=None,
        order_side=side,
        order_type=OrderType.MARKET,
        last_qty=Quantity.from_int(qty),
        last_px=Price.from_str(px),
        currency=USD,
        commission=commission,
        liquidity_side=LiquiditySide.TAKER,
        event_id=UUID4(),
        ts_event=clock.timestamp_ns(),
        ts_init=clock.timestamp_ns(),
    )
    clock.advance_time(clock.timestamp_ns() + 1_000_000_000)
    engine.process(fill)
    return order.client_order_id


class TestAgainstARealExecutionEngine:
    """The recorder subscribed on ``events.position*`` of a real bus, fed by a
    real ``ExecutionEngine`` — production wiring, no node."""

    def _subscribe(self, stack):
        msgbus, cache, *_ = stack
        sink_calls: list[RecordedTrade] = []
        recorder = TradeRecorder(cache, structlog.get_logger("test"), sink=sink_calls.append)
        msgbus.subscribe(topic=POSITION_EVENTS_TOPIC, handler=recorder.handle_position_event)
        return sink_calls

    def test_a_plain_round_trip_is_recorded_with_its_summed_commission(self):
        stack = _engine_stack()
        sink_calls = self._subscribe(stack)

        with capture_logs() as logs:
            _engine_fill(stack, OrderSide.BUY, 100, "100.00", _money("1.25"))
            closing = _engine_fill(stack, OrderSide.SELL, 100, "110.00", _money("1.00"))

        aggregated = [entry for entry in logs if entry["event"] == AGGREGATED_EVENT]
        assert [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT] == []
        assert len(aggregated) == 1
        assert aggregated[0]["entry_price"] == "100.00000000"
        assert aggregated[0]["exit_price"] == "110.00000000"
        assert aggregated[0]["commission"] == "2.25"
        assert aggregated[0]["fill_count"] == "2"
        assert aggregated[0]["closing_order_id"] == str(closing)
        assert len(sink_calls) == 1
        assert sink_calls[0].trade.commission_amount == Decimal("2.25")

    def test_a_flip_records_the_closed_leg_from_the_event_with_commission_unknown(self):
        """Measured 2026-09-12: on a flip Nautilus closes the original
        ``Position``, builds a NEW one under the same NETTING id
        (``_open_position(instrument, None, fill_split2)``) and only then
        publishes ``PositionClosed`` — so at dispatch time the cache holds the
        new, open position (``avg_px_close 0.0``, ``ts_closed 0``,
        ``closing_order_id None``, the residual leg's commission). The closed
        leg's own commission is unrecoverable from the cache; every other
        field is a snapshot on the event itself.
        """
        stack = _engine_stack()
        sink_calls = self._subscribe(stack)

        with capture_logs() as logs:
            _engine_fill(stack, OrderSide.BUY, 50, "120.00", _money("0.50"))
            flipping = _engine_fill(stack, OrderSide.SELL, 150, "130.00", _money("3.00"))

        assert [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT] == []
        aggregated = [entry for entry in logs if entry["event"] == AGGREGATED_EVENT]
        assert len(aggregated) == 1
        record = aggregated[0]
        assert record["entry_price"] == "120.00000000"
        assert record["exit_price"] == "130.00000000"
        assert record["quantity"] == "50.00000000"
        assert record["order_side"] == "BUY"
        assert record["closing_order_id"] == str(flipping)
        assert Decimal(record["realized_pnl"]) == Decimal("498.50"), (
            "50 x 10 gross, less 0.50 + 1.00"
        )
        assert record["commission"] is None
        assert record["currency"] is None

        assert COMMISSION_UNAVAILABLE_EVENT == "trade.commission_unavailable"
        unavailable = [entry for entry in logs if entry["event"] == COMMISSION_UNAVAILABLE_EVENT]
        assert len(unavailable) == 1
        assert unavailable[0]["log_level"] == "warning"
        assert unavailable[0]["position_id"] == record["position_id"]
        assert unavailable[0]["closing_order_id"] == str(flipping)

        assert len(sink_calls) == 1
        assert sink_calls[0].trade.commission_amount is None
        assert sink_calls[0].trade.commission_currency is None
        assert sink_calls[0].trade.exit_price == Decimal("130.00000000")
        assert sink_calls[0].fill_count is None


class TestAnOrderEventCanNeverReachThisRecorder:
    """Story 3.7, AC #4(b) — FR24's structural half: a rejection is not a trade.

    The scenario half lives in
    ``tests/component/core/test_live_order_rejections_engine.py`` (a real
    engine rejects a real order and no position event is published, beside a
    control where a filled round trip *does* reach the sink). This class pins
    the two structural barriers that make that outcome true by construction
    rather than by the scenario happening to exercise it, because each catches
    a different mutation: adding an ``Order*`` key to the dispatch map, and
    widening the topic pattern.
    """

    def test_the_dispatch_keys_are_position_events_only(self):
        recorder = TradeRecorder(_StubCache({}), structlog.get_logger("test"))

        assert set(recorder._dispatch) <= {
            "PositionOpened",
            "PositionChanged",
            "PositionClosed",
        }
        assert not any(name.startswith("Order") for name in recorder._dispatch)

    def test_the_topic_pattern_cannot_match_an_order_topic(self):
        from fnmatch import fnmatch

        assert fnmatch("events.order.SMACrossover-000", POSITION_EVENTS_TOPIC) is False
        assert fnmatch("events.order*", POSITION_EVENTS_TOPIC) is False
        # The control, so the assertions above are testing the pattern rather
        # than a typo that matches nothing at all.
        assert fnmatch("events.position.SMACrossover-000", POSITION_EVENTS_TOPIC) is True

    def test_an_order_shaped_event_handed_straight_to_the_handler_records_nothing(self):
        """Belt and braces: even if a future wiring change subscribed this
        handler on the wrong topic, an order event produces no record and no
        sink call — it falls off the closed dispatch set in silence.
        """
        sink_calls: list[RecordedTrade] = []
        recorder = TradeRecorder(
            _StubCache({}), structlog.get_logger("test"), sink=sink_calls.append
        )
        rejected = type("OrderRejected", (), {"position_id": None})()

        with capture_logs() as logs:
            recorder.handle_position_event(rejected)

        assert sink_calls == []
        assert [entry for entry in logs if entry["event"] == AGGREGATED_EVENT] == []
        assert [entry for entry in logs if entry["event"] == RECORDER_FAILED_EVENT] == []
