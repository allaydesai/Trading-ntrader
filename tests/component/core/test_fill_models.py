"""Unit tests for GapAwareFillModel pure decision helpers.

The model corrects two L1 bar-data artifacts of the Nautilus SimulatedExchange:

1. A stop-market order gapped through overnight fills at the bar's open, not at
   its trigger price (the engine default, which overstates stop protection).
2. A market order tagged ``MOO_EXIT_TAG`` fills at the open of the bar it executes on.
   With next-bar execution (``NextBarExecutionModule``) that is the bar after the signal,
   emulating a market-on-open order (unsupported by the sim with bar data).

These tests exercise the decision helpers without running the Nautilus engine.
"""

from types import SimpleNamespace

import pytest
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.enums import OrderSide, OrderType
from nautilus_trader.model.identifiers import InstrumentId

from src.core.fill_models import MOO_EXIT_TAG, GapAwareFillModel, bind_fill_model


@pytest.mark.component
class TestStopFillPrice:
    """Stop fills at the trigger intrabar, but at the open on a gap through."""

    def test_sell_stop_gap_below_trigger_fills_at_open(self):
        # Overnight gap: bar opens at 90, far below the 110.39 trigger.
        assert GapAwareFillModel._stop_fill_price(OrderSide.SELL, 110.39, 90.0) == 90.0

    def test_sell_stop_intrabar_fills_at_trigger(self):
        # Bar opens above the trigger; the intrabar decline fills at the trigger.
        assert GapAwareFillModel._stop_fill_price(OrderSide.SELL, 110.39, 116.0) == 110.39

    def test_sell_stop_open_exactly_at_trigger(self):
        assert GapAwareFillModel._stop_fill_price(OrderSide.SELL, 110.39, 110.39) == 110.39

    def test_buy_stop_gap_above_trigger_fills_at_open(self):
        assert GapAwareFillModel._stop_fill_price(OrderSide.BUY, 105.0, 112.0) == 112.0

    def test_buy_stop_intrabar_fills_at_trigger(self):
        assert GapAwareFillModel._stop_fill_price(OrderSide.BUY, 105.0, 100.0) == 105.0


@pytest.mark.component
class TestBarOpenLookup:
    """Current-bar and next-bar open lookups against sorted (ts, open) series."""

    TS = [100, 200, 300]
    OPENS = [10.0, 11.0, 12.0]

    def test_open_at_exact_ts(self):
        assert GapAwareFillModel._open_at(self.TS, self.OPENS, 200) == 11.0

    def test_open_at_unknown_ts_is_none(self):
        assert GapAwareFillModel._open_at(self.TS, self.OPENS, 150) is None

    def test_empty_series(self):
        assert GapAwareFillModel._open_at([], [], 100) is None


@pytest.mark.component
def test_moo_exit_tag_is_stable():
    """Strategies tag exit orders with this constant; it must not drift."""
    assert MOO_EXIT_TAG == "MOO_EXIT"


class _Clock:
    def __init__(self, ts: int) -> None:
        self.ts = ts

    def timestamp_ns(self) -> int:
        return self.ts


def _bar(
    ts: int, open_: float, instrument: str = "AAPL.NASDAQ", ts_event: int | None = None
) -> SimpleNamespace:
    """A duck-typed bar; the engine clock reads ``ts_init``, which may differ from ts_event."""
    bar_type = SimpleNamespace(instrument_id=InstrumentId.from_str(instrument))
    return SimpleNamespace(
        bar_type=bar_type,
        ts_init=ts,
        ts_event=ts if ts_event is None else ts_event,
        open=SimpleNamespace(as_double=lambda: open_),
    )


def _market(tags: list[str] | None) -> SimpleNamespace:
    return SimpleNamespace(order_type=OrderType.MARKET, tags=tags, instrument_id="AAPL.NASDAQ")


@pytest.mark.component
class TestMooExitPrice:
    """An MOO exit fills at the open of the bar being processed when it executes.

    Next-bar execution releases the order at the start of the bar after the signal, so the
    clock then reads that bar's timestamp. Repricing to the bar *after* the clock (the old
    behaviour) would fill a bar late; repricing on the signal bar would fill a bar early.
    """

    @staticmethod
    def _model(now: int) -> GapAwareFillModel:
        model = GapAwareFillModel()
        model.register_clock(_Clock(now))
        model.register_bars([_bar(100, 10.0), _bar(200, 11.0), _bar(300, 12.0)])
        return model

    def test_fills_at_the_open_of_the_bar_it_executes_on(self):
        assert self._model(200)._fill_price_for(_market([MOO_EXIT_TAG])) == 11.0

    def test_no_bar_at_the_clock_uses_the_default_fill(self):
        assert self._model(250)._fill_price_for(_market([MOO_EXIT_TAG])) is None

    def test_bars_are_keyed_by_ts_init_the_clock_reads(self):
        # The engine clock is set to each bar's ts_init; a ts_event lookup would miss.
        model = GapAwareFillModel()
        model.register_clock(_Clock(200))
        model.register_bars([_bar(100, 10.0, ts_event=99), _bar(200, 11.0, ts_event=199)])
        assert model._fill_price_for(_market([MOO_EXIT_TAG])) == 11.0

    def test_an_untagged_market_order_uses_the_default_fill(self):
        assert self._model(200)._fill_price_for(_market(None)) is None
        assert self._model(200)._fill_price_for(_market(["OTHER"])) is None


@pytest.mark.component
class TestBindFillModel:
    """Next-bar execution releases a whole venue's commands on any of its bars.

    So every instrument on one gap-aware venue must have bars at the same timestamps,
    or an MOO exit would be released, and filled at a stale price, on another
    instrument's bar.
    """

    @staticmethod
    def _engine() -> SimpleNamespace:
        return SimpleNamespace(kernel=SimpleNamespace(clock=_Clock(0)))

    def test_aligned_series_on_one_venue_are_accepted(self):
        bars = [_bar(ts, 1.0, sym) for ts in (100, 200) for sym in ("AAPL.NASDAQ", "MSFT.NASDAQ")]
        model = GapAwareFillModel()
        bind_fill_model(model, self._engine(), bars)
        assert model._bar_ts["MSFT.NASDAQ"] == [100, 200]

    def test_misaligned_series_on_one_venue_are_rejected(self):
        bars = [_bar(100, 1.0), _bar(200, 1.0), _bar(100, 1.0, "MSFT.NASDAQ")]
        with pytest.raises(ValueError, match="same timestamps"):
            bind_fill_model(GapAwareFillModel(), self._engine(), bars)

    def test_series_on_different_venues_may_differ(self):
        bars = [_bar(100, 1.0), _bar(200, 1.0), _bar(150, 1.0, "ES.CME")]
        bind_fill_model(GapAwareFillModel(), self._engine(), bars)

    def test_a_default_model_is_not_checked(self):
        bars = [_bar(100, 1.0), _bar(200, 1.0), _bar(100, 1.0, "MSFT.NASDAQ")]
        bind_fill_model(FillModel(), self._engine(), bars)
