"""Unit tests for the Riptide pullback mean-reversion strategy pure logic.

These tests exercise the pure decision helpers on ``Riptide`` without the
Nautilus C engine. The predicates are ``staticmethod``s (no ``self`` needed);
position sizing is invoked unbound against a duck-typed ``self`` following the
pattern in ``tests/unit/strategies/test_sma_crossover_position_sizing.py``.

Covers the spec rules:
- Buy limit 2% below close
- 2.5x ATR(14) hard-stop price
- 15-day-low pullback filter
- $10M average-dollar-volume liquidity filter
- Green-candle profit-exit detection
- 10-day time-stop boundary (exactly 10 held days)
- ROC-100 ranking value
- 10%-of-portfolio position sizing (whole shares, min 1)
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.strategies.riptide import Riptide


@pytest.mark.unit
class TestLimitPrice:
    """Buy limit is placed 2% below the setup-day close."""

    def test_two_percent_below_close(self):
        assert Riptide._compute_limit_price(100.0, Decimal("2.0")) == Decimal("98.00")

    def test_rounds_to_two_decimals(self):
        # 155.37 * 0.98 = 152.2626 -> 152.26
        assert Riptide._compute_limit_price(155.37, Decimal("2.0")) == Decimal("152.26")

    def test_custom_offset(self):
        assert Riptide._compute_limit_price(200.0, Decimal("1.5")) == Decimal("197.00")


@pytest.mark.unit
class TestStopPrice:
    """Hard stop sits 2.5 ATR below the exact entry price."""

    def test_stop_two_and_half_atr_below_entry(self):
        # entry 100, ATR 4 -> 100 - 2.5*4 = 90.00
        stop = Riptide._compute_stop_price(Decimal("100.00"), 4.0, Decimal("2.5"))
        assert stop == Decimal("90.00")

    def test_stop_rounds_to_two_decimals(self):
        stop = Riptide._compute_stop_price(Decimal("152.26"), 3.137, Decimal("2.5"))
        # 152.26 - 7.8425 = 144.4175 -> 144.42
        assert stop == Decimal("144.42")


@pytest.mark.unit
class TestPullbackLow:
    """Close must be the lowest close of the lookback window."""

    def test_is_new_low(self):
        closes = [110.0, 108.0, 105.0, 103.0, 100.0]
        assert Riptide._is_pullback_low(100.0, closes) is True

    def test_not_a_low(self):
        closes = [110.0, 108.0, 105.0, 103.0, 104.0]
        assert Riptide._is_pullback_low(104.0, closes) is False

    def test_ties_count_as_low(self):
        closes = [100.0, 105.0, 100.0]
        assert Riptide._is_pullback_low(100.0, closes) is True


@pytest.mark.unit
class TestLiquidityFilter:
    """Average dollar volume must clear the ADV threshold."""

    def test_liquid_when_above_threshold(self):
        vols = [12_000_000.0, 15_000_000.0, 11_000_000.0]
        assert Riptide._is_liquid(vols, Decimal("10000000")) is True

    def test_illiquid_when_below_threshold(self):
        vols = [5_000_000.0, 6_000_000.0, 4_000_000.0]
        assert Riptide._is_liquid(vols, Decimal("10000000")) is False

    def test_empty_window_is_illiquid(self):
        assert Riptide._is_liquid([], Decimal("10000000")) is False


@pytest.mark.unit
class TestGreenCandle:
    """Profit exit triggers when today's close exceeds yesterday's."""

    def test_green_candle(self):
        assert Riptide._is_green_candle(105.0, 104.0) is True

    def test_red_candle(self):
        assert Riptide._is_green_candle(103.0, 104.0) is False

    def test_flat_is_not_green(self):
        assert Riptide._is_green_candle(104.0, 104.0) is False

    def test_no_prev_close_is_not_green(self):
        assert Riptide._is_green_candle(104.0, None) is False


@pytest.mark.unit
class TestTimeStop:
    """Time stop fires once exactly 10 full days have been held."""

    def test_not_yet_at_limit(self):
        assert Riptide._should_time_exit(9, 10) is False

    def test_at_limit(self):
        assert Riptide._should_time_exit(10, 10) is True

    def test_past_limit(self):
        assert Riptide._should_time_exit(11, 10) is True


@pytest.mark.unit
class TestRoc:
    """ROC-100 ranking value (informational this phase)."""

    def test_roc_computed_when_enough_history(self):
        # len 101, reference = closes[-1-100] = closes[0] = 100, last = 110 -> ROC 0.10
        closes = [100.0] + [110.0] * 100
        assert Riptide._compute_roc(closes, 100) == pytest.approx(0.10)

    def test_roc_none_when_insufficient_history(self):
        closes = [100.0] * 50
        assert Riptide._compute_roc(closes, 100) is None


@pytest.mark.unit
class TestPositionSizing:
    """10% of portfolio value, whole shares, floored at 1."""

    @staticmethod
    def _sizing_self(
        portfolio_value: Decimal = Decimal("1000000"),
        position_size_pct: Decimal = Decimal("10"),
    ):
        return SimpleNamespace(
            portfolio_value=portfolio_value,
            position_size_pct=position_size_pct,
            log=MagicMock(),
        )

    def test_whole_shares(self):
        # $1M * 10% / $98 = 1020.4 -> 1020 whole shares
        shares = Riptide._position_shares(self._sizing_self(), Decimal("98.00"))
        assert shares == 1020

    def test_minimum_one_share(self):
        shares = Riptide._position_shares(
            self._sizing_self(portfolio_value=Decimal("1")), Decimal("500.00")
        )
        assert shares == 1
