"""Unit tests for signal component implementations.

Tests cover:
- TrendFilterComponent
- RSIThresholdComponent
- VolumeConfirmComponent
- FibonacciLevelComponent
- PriceBreakoutComponent
- TimeStopComponent
"""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from src.core.signals.components import (
    FibonacciLevelComponent,
    PriceBreakoutComponent,
    RSIThresholdComponent,
    TimeStopComponent,
    TrendFilterComponent,
    VolumeConfirmComponent,
)


@dataclass
class MockIndicator:
    """Mock indicator for testing."""

    value: float
    period: int = 14
    initialized: bool = True


class MockBar:
    """Mock bar for testing without Nautilus dependency."""

    def __init__(
        self,
        close: float = 100.0,
        high: float = 105.0,
        low: float = 95.0,
        volume: float = 1000.0,
    ) -> None:
        self.close = close
        self.high = high
        self.low = low
        self.volume = volume


class TestTrendFilterComponent:
    """Tests for TrendFilterComponent."""

    def test_trend_above_sma_triggered(self) -> None:
        """Test trend filter triggers when close > SMA."""
        sma = MockIndicator(value=95.0, period=200)
        component = TrendFilterComponent(name="trend_filter", sma_indicator=sma)

        bar = MockBar(close=100.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is True
        assert result.name == "trend_filter"
        assert result.value == 100.0
        assert ">" in result.reason

    def test_trend_below_sma_not_triggered(self) -> None:
        """Test trend filter does not trigger when close < SMA."""
        sma = MockIndicator(value=105.0, period=200)
        component = TrendFilterComponent(name="trend_filter", sma_indicator=sma)

        bar = MockBar(close=100.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert "<=" in result.reason

    def test_trend_below_direction(self) -> None:
        """Test trend filter with 'below' direction."""
        sma = MockIndicator(value=105.0, period=200)
        component = TrendFilterComponent(name="trend_below", sma_indicator=sma, direction="below")

        bar = MockBar(close=100.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is True
        assert "<" in result.reason

    def test_trend_insufficient_data(self) -> None:
        """Test trend filter returns insufficient data when SMA not initialized."""
        sma = MockIndicator(value=0.0, period=200, initialized=False)
        component = TrendFilterComponent(name="trend_filter", sma_indicator=sma)

        bar = MockBar(close=100.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert "Insufficient data" in result.reason


class TestRSIThresholdComponent:
    """Tests for RSIThresholdComponent."""

    def test_rsi_below_threshold_triggered(self) -> None:
        """Test RSI triggers when below threshold."""
        rsi = MockIndicator(value=8.5, period=2)
        component = RSIThresholdComponent(
            name="rsi_oversold", rsi_indicator=rsi, threshold=10.0, direction="below"
        )

        bar = MockBar()
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is True
        assert result.value == 8.5
        assert "<" in result.reason

    def test_rsi_above_threshold_not_triggered(self) -> None:
        """Test RSI does not trigger when above threshold."""
        rsi = MockIndicator(value=15.0, period=2)
        component = RSIThresholdComponent(
            name="rsi_oversold", rsi_indicator=rsi, threshold=10.0, direction="below"
        )

        bar = MockBar()
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert ">=" in result.reason

    def test_rsi_above_direction(self) -> None:
        """Test RSI with 'above' direction (overbought)."""
        rsi = MockIndicator(value=85.0, period=14)
        component = RSIThresholdComponent(
            name="rsi_overbought", rsi_indicator=rsi, threshold=80.0, direction="above"
        )

        bar = MockBar()
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is True
        assert ">" in result.reason

    def test_rsi_insufficient_data(self) -> None:
        """Test RSI returns insufficient data when not initialized."""
        rsi = MockIndicator(value=0.0, period=14, initialized=False)
        component = RSIThresholdComponent(name="rsi_oversold", rsi_indicator=rsi, threshold=10.0)

        bar = MockBar()
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert "Insufficient data" in result.reason


class TestVolumeConfirmComponent:
    """Tests for VolumeConfirmComponent."""

    def test_volume_above_average_triggered(self) -> None:
        """Test volume confirmation triggers when volume > avg * multiplier."""
        volume_sma = MockIndicator(value=1000.0, period=20)
        component = VolumeConfirmComponent(
            name="volume_surge", volume_sma_indicator=volume_sma, multiplier=1.5
        )

        bar = MockBar(volume=1600.0)  # 1.6x average
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is True
        assert result.value == pytest.approx(1.6, rel=0.01)

    def test_volume_below_average_not_triggered(self) -> None:
        """Test volume confirmation does not trigger when volume < avg * multiplier."""
        volume_sma = MockIndicator(value=1000.0, period=20)
        component = VolumeConfirmComponent(
            name="volume_surge", volume_sma_indicator=volume_sma, multiplier=1.5
        )

        bar = MockBar(volume=1200.0)  # 1.2x average, below 1.5x threshold
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert result.value == pytest.approx(1.2, rel=0.01)

    def test_volume_insufficient_data(self) -> None:
        """Test volume returns insufficient data when SMA not initialized."""
        volume_sma = MockIndicator(value=0.0, period=20, initialized=False)
        component = VolumeConfirmComponent(name="volume_surge", volume_sma_indicator=volume_sma)

        bar = MockBar(volume=1000.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert "Insufficient data" in result.reason


class TestFibonacciLevelComponent:
    """Tests for FibonacciLevelComponent."""

    def test_near_fib_level_triggered(self) -> None:
        """Test Fibonacci triggers when price is near level."""
        component = FibonacciLevelComponent(name="fib_618", level=0.618, tolerance=0.02)
        component.set_swing_range(swing_high=100.0, swing_low=80.0)

        # Fib 0.618 level = 100 - (20 * 0.618) = 87.64
        bar = MockBar(close=87.5)  # Within 2% of 87.64
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is True
        assert "Fib 0.618" in result.reason

    def test_far_from_fib_level_not_triggered(self) -> None:
        """Test Fibonacci does not trigger when price is far from level."""
        component = FibonacciLevelComponent(name="fib_618", level=0.618, tolerance=0.02)
        component.set_swing_range(swing_high=100.0, swing_low=80.0)

        # Fib 0.618 level = 87.64, but close is 95.0 (8% away)
        bar = MockBar(close=95.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False

    def test_fib_level_from_context(self) -> None:
        """Test Fibonacci can receive swing range from context."""
        component = FibonacciLevelComponent(name="fib_382", level=0.382, tolerance=0.02)

        # Fib 0.382 = 100 - (20 * 0.382) = 92.36
        bar = MockBar(close=92.5)
        cache = MagicMock()

        result = component.evaluate(bar, cache, swing_high=100.0, swing_low=80.0)

        assert result.triggered is True

    def test_fib_insufficient_data(self) -> None:
        """Test Fibonacci returns insufficient data when swing not set."""
        component = FibonacciLevelComponent(name="fib_618", level=0.618)

        bar = MockBar(close=90.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert "Insufficient data" in result.reason


class TestPriceBreakoutComponent:
    """Tests for PriceBreakoutComponent."""

    def test_breakout_above_triggered(self) -> None:
        """Test breakout triggers when close > prev_high."""
        component = PriceBreakoutComponent(name="breakout_high", comparison="above")
        component.set_previous_bar(prev_high=100.0, prev_low=95.0)

        bar = MockBar(close=101.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is True
        assert ">" in result.reason
        assert "prev_high" in result.reason

    def test_breakout_above_not_triggered(self) -> None:
        """Test breakout does not trigger when close <= prev_high."""
        component = PriceBreakoutComponent(name="breakout_high", comparison="above")
        component.set_previous_bar(prev_high=100.0, prev_low=95.0)

        bar = MockBar(close=99.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert "<=" in result.reason

    def test_breakout_below_triggered(self) -> None:
        """Test breakout triggers when close < prev_low."""
        component = PriceBreakoutComponent(name="breakout_low", comparison="below")
        component.set_previous_bar(prev_high=100.0, prev_low=95.0)

        bar = MockBar(close=94.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is True
        assert "<" in result.reason
        assert "prev_low" in result.reason

    def test_breakout_from_context(self) -> None:
        """Test breakout can receive prev bar from context."""
        component = PriceBreakoutComponent(name="breakout_high", comparison="above")

        bar = MockBar(close=101.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache, prev_high=100.0, prev_low=95.0)

        assert result.triggered is True

    def test_breakout_insufficient_data(self) -> None:
        """Test breakout returns insufficient data when prev bar not set."""
        component = PriceBreakoutComponent(name="breakout_high")

        bar = MockBar(close=100.0)
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert "Insufficient data" in result.reason


class TestTimeStopComponent:
    """Tests for TimeStopComponent."""

    def test_time_stop_triggered(self) -> None:
        """Test time stop triggers when bars_held >= max_bars."""
        component = TimeStopComponent(name="time_stop", max_bars=5)

        bar = MockBar()
        cache = MagicMock()

        result = component.evaluate(bar, cache, bars_held=5)

        assert result.triggered is True
        assert result.value == 5.0
        assert "≥" in result.reason

    def test_time_stop_not_triggered(self) -> None:
        """Test time stop does not trigger when bars_held < max_bars."""
        component = TimeStopComponent(name="time_stop", max_bars=5)

        bar = MockBar()
        cache = MagicMock()

        result = component.evaluate(bar, cache, bars_held=3)

        assert result.triggered is False
        assert result.value == 3.0
        assert "<" in result.reason

    def test_time_stop_default_bars_held(self) -> None:
        """Test time stop uses 0 when bars_held not provided."""
        component = TimeStopComponent(name="time_stop", max_bars=5)

        bar = MockBar()
        cache = MagicMock()

        result = component.evaluate(bar, cache)

        assert result.triggered is False
        assert result.value == 0.0
