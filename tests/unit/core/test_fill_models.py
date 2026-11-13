"""Unit tests for GapAwareFillModel pure decision helpers.

The model corrects two L1 bar-data artifacts of the Nautilus SimulatedExchange:

1. A stop-market order gapped through overnight fills at the bar's open, not at
   its trigger price (the engine default, which overstates stop protection).
2. A market order tagged ``MOO_EXIT_TAG`` is repriced to the *next* bar's open,
   emulating a market-on-open order (unsupported by the sim with bar data).

These tests exercise the pure static helpers without the Nautilus engine.
"""

import pytest
from nautilus_trader.model.enums import OrderSide

from src.core.fill_models import MOO_EXIT_TAG, GapAwareFillModel


@pytest.mark.unit
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


@pytest.mark.unit
class TestBarOpenLookup:
    """Current-bar and next-bar open lookups against sorted (ts, open) series."""

    TS = [100, 200, 300]
    OPENS = [10.0, 11.0, 12.0]

    def test_open_at_exact_ts(self):
        assert GapAwareFillModel._open_at(self.TS, self.OPENS, 200) == 11.0

    def test_open_at_unknown_ts_is_none(self):
        assert GapAwareFillModel._open_at(self.TS, self.OPENS, 150) is None

    def test_open_after_mid_series(self):
        assert GapAwareFillModel._open_after(self.TS, self.OPENS, 200) == 12.0

    def test_open_after_between_bars(self):
        assert GapAwareFillModel._open_after(self.TS, self.OPENS, 150) == 11.0

    def test_open_after_last_bar_is_none(self):
        assert GapAwareFillModel._open_after(self.TS, self.OPENS, 300) is None

    def test_empty_series(self):
        assert GapAwareFillModel._open_at([], [], 100) is None
        assert GapAwareFillModel._open_after([], [], 100) is None


@pytest.mark.unit
def test_moo_exit_tag_is_stable():
    """Strategies tag exit orders with this constant; it must not drift."""
    assert MOO_EXIT_TAG == "MOO_EXIT"
