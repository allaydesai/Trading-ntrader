"""Signal component protocol and concrete implementations.

This module contains:
- SignalComponent: Protocol defining the interface for signal conditions
- Concrete component implementations for common trading conditions
"""

from typing import Any, Protocol

from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.data import Bar

from src.core.signals.evaluation import ComponentResult


class SignalComponent(Protocol):
    """Protocol for signal condition components.

    All signal components must implement this interface to be usable
    with CompositeSignalGenerator.

    Attributes:
        name: Unique identifier for this component

    Example:
        >>> class MyComponent:
        ...     name = "my_component"
        ...     def evaluate(self, bar: Bar, cache: Cache) -> ComponentResult:
        ...         return ComponentResult("my_component", 1.0, True, "Always passes")
    """

    name: str

    def evaluate(self, bar: Bar, cache: Cache, **context: Any) -> ComponentResult:
        """Evaluate the condition against current bar state.

        Args:
            bar: Current bar data
            cache: Nautilus cache for accessing indicators and state
            **context: Additional context (e.g., position state for exit signals)

        Returns:
            ComponentResult with evaluation outcome
        """
        ...


class TrendFilterComponent:
    """Check if price is above/below a moving average.

    Evaluates whether the close price is above or below a simple moving
    average (SMA) indicator.

    Attributes:
        name: Component identifier
        sma_indicator: Reference to SMA indicator (must be registered with strategy)
        direction: "above" or "below"
    """

    def __init__(
        self,
        name: str,
        sma_indicator: Any,
        direction: str = "above",
    ) -> None:
        """Initialize trend filter component.

        Args:
            name: Component identifier
            sma_indicator: Reference to Nautilus SMA indicator
            direction: "above" (Close > SMA) or "below" (Close < SMA)
        """
        self.name = name
        self._sma = sma_indicator
        self._direction = direction

    def evaluate(self, bar: Bar, cache: Cache, **context: Any) -> ComponentResult:
        """Evaluate trend filter condition.

        Args:
            bar: Current bar data
            cache: Nautilus cache (unused for this component)
            **context: Additional context (unused)

        Returns:
            ComponentResult indicating if trend condition is met
        """
        if not self._sma.initialized:
            return ComponentResult(
                name=self.name,
                value=float("nan"),
                triggered=False,
                reason=f"Insufficient data: SMA requires {self._sma.period} bars",
            )

        close_price = float(bar.close)
        sma_value = float(self._sma.value)

        if self._direction == "above":
            triggered = close_price > sma_value
            comparison = ">" if triggered else "<="
        else:
            triggered = close_price < sma_value
            comparison = "<" if triggered else ">="

        reason = f"Close ({close_price:.2f}) {comparison} SMA({self._sma.period}) ({sma_value:.2f})"
        return ComponentResult(
            name=self.name,
            value=close_price,
            triggered=triggered,
            reason=reason,
        )


class RSIThresholdComponent:
    """Check if RSI is above/below a threshold.

    Evaluates whether the RSI indicator is above or below a specified
    threshold value.

    Attributes:
        name: Component identifier
        rsi_indicator: Reference to RSI indicator (must be registered with strategy)
        threshold: RSI threshold value (0-100)
        direction: "below" (RSI < threshold) or "above" (RSI > threshold)
    """

    def __init__(
        self,
        name: str,
        rsi_indicator: Any,
        threshold: float = 30.0,
        direction: str = "below",
    ) -> None:
        """Initialize RSI threshold component.

        Args:
            name: Component identifier
            rsi_indicator: Reference to Nautilus RSI indicator
            threshold: RSI threshold value (0-100)
            direction: "below" (RSI < threshold) or "above" (RSI > threshold)
        """
        self.name = name
        self._rsi = rsi_indicator
        self._threshold = threshold
        self._direction = direction

    def evaluate(self, bar: Bar, cache: Cache, **context: Any) -> ComponentResult:
        """Evaluate RSI threshold condition.

        Args:
            bar: Current bar data
            cache: Nautilus cache (unused for this component)
            **context: Additional context (unused)

        Returns:
            ComponentResult indicating if RSI condition is met
        """
        if not self._rsi.initialized:
            return ComponentResult(
                name=self.name,
                value=float("nan"),
                triggered=False,
                reason=f"Insufficient data: RSI requires {self._rsi.period} bars",
            )

        rsi_value = float(self._rsi.value)

        if self._direction == "below":
            triggered = rsi_value < self._threshold
            comparison = "<" if triggered else ">="
        else:
            triggered = rsi_value > self._threshold
            comparison = ">" if triggered else "<="

        return ComponentResult(
            name=self.name,
            value=rsi_value,
            triggered=triggered,
            reason=f"RSI({self._rsi.period}) = {rsi_value:.2f} {comparison} {self._threshold}",
        )


class VolumeConfirmComponent:
    """Check if volume exceeds average by a multiplier.

    Evaluates whether the current bar's volume exceeds the average
    volume by a specified multiplier.

    Attributes:
        name: Component identifier
        volume_sma_indicator: Reference to volume SMA indicator
        multiplier: Required multiple of average volume (e.g., 1.5 = 50% above average)
    """

    def __init__(
        self,
        name: str,
        volume_sma_indicator: Any,
        multiplier: float = 1.5,
    ) -> None:
        """Initialize volume confirmation component.

        Args:
            name: Component identifier
            volume_sma_indicator: Reference to SMA indicator on volume
            multiplier: Required multiple of average volume
        """
        self.name = name
        self._volume_sma = volume_sma_indicator
        self._multiplier = multiplier

    def evaluate(self, bar: Bar, cache: Cache, **context: Any) -> ComponentResult:
        """Evaluate volume confirmation condition.

        Args:
            bar: Current bar data
            cache: Nautilus cache (unused for this component)
            **context: Additional context (unused)

        Returns:
            ComponentResult indicating if volume condition is met
        """
        if not self._volume_sma.initialized:
            return ComponentResult(
                name=self.name,
                value=float("nan"),
                triggered=False,
                reason=f"Insufficient data: Volume SMA requires {self._volume_sma.period} bars",
            )

        current_volume = float(bar.volume)
        avg_volume = float(self._volume_sma.value)
        threshold = avg_volume * self._multiplier

        triggered = current_volume >= threshold
        ratio = current_volume / avg_volume if avg_volume > 0 else 0.0

        return ComponentResult(
            name=self.name,
            value=ratio,
            triggered=triggered,
            reason=f"Volume {current_volume:.0f} is {ratio:.2f}x average ({avg_volume:.0f}), "
            f"required {self._multiplier}x",
        )


class FibonacciLevelComponent:
    """Check if price is near a Fibonacci retracement level.

    Evaluates whether the current price is within tolerance of a
    Fibonacci retracement level calculated from swing high/low.

    Attributes:
        name: Component identifier
        level: Fibonacci level (e.g., 0.382, 0.5, 0.618)
        tolerance: Percentage tolerance (e.g., 0.02 = 2%)
        swing_high: Reference to swing high value
        swing_low: Reference to swing low value
    """

    def __init__(
        self,
        name: str,
        level: float = 0.618,
        tolerance: float = 0.02,
    ) -> None:
        """Initialize Fibonacci level component.

        Args:
            name: Component identifier
            level: Fibonacci level (0.0-1.0)
            tolerance: Percentage tolerance for "near" calculation
        """
        self.name = name
        self._level = level
        self._tolerance = tolerance
        self._swing_high: float | None = None
        self._swing_low: float | None = None

    def set_swing_range(self, swing_high: float, swing_low: float) -> None:
        """Set the swing high and low for Fibonacci calculation.

        Args:
            swing_high: The swing high price
            swing_low: The swing low price
        """
        self._swing_high = swing_high
        self._swing_low = swing_low

    def evaluate(self, bar: Bar, cache: Cache, **context: Any) -> ComponentResult:
        """Evaluate Fibonacci level condition.

        Args:
            bar: Current bar data
            cache: Nautilus cache (unused for this component)
            **context: May contain swing_high and swing_low

        Returns:
            ComponentResult indicating if price is near Fib level
        """
        # Get swing range from context or stored values
        swing_high = context.get("swing_high", self._swing_high)
        swing_low = context.get("swing_low", self._swing_low)

        if swing_high is None or swing_low is None:
            return ComponentResult(
                name=self.name,
                value=float("nan"),
                triggered=False,
                reason="Insufficient data: Swing high/low not set",
            )

        # Calculate Fibonacci level price
        swing_range = swing_high - swing_low
        fib_price = swing_high - (swing_range * self._level)

        close_price = float(bar.close)
        distance = abs(close_price - fib_price) / fib_price if fib_price > 0 else 1.0

        triggered = distance <= self._tolerance

        return ComponentResult(
            name=self.name,
            value=distance,
            triggered=triggered,
            reason=f"Price ({close_price:.2f}) is {distance:.1%} from Fib {self._level} "
            f"({fib_price:.2f}), tolerance {self._tolerance:.1%}",
        )


class PriceBreakoutComponent:
    """Check if price breaks previous high or low.

    Evaluates whether the current close price is above the previous
    high or below the previous low.

    Attributes:
        name: Component identifier
        comparison: "above" (close > prev_high) or "below" (close < prev_low)
    """

    def __init__(
        self,
        name: str,
        comparison: str = "above",
    ) -> None:
        """Initialize price breakout component.

        Args:
            name: Component identifier
            comparison: "above" (break prev high) or "below" (break prev low)
        """
        self.name = name
        self._comparison = comparison
        self._prev_high: float | None = None
        self._prev_low: float | None = None

    def set_previous_bar(self, prev_high: float, prev_low: float) -> None:
        """Set the previous bar's high and low.

        Args:
            prev_high: Previous bar's high price
            prev_low: Previous bar's low price
        """
        self._prev_high = prev_high
        self._prev_low = prev_low

    def evaluate(self, bar: Bar, cache: Cache, **context: Any) -> ComponentResult:
        """Evaluate price breakout condition.

        Args:
            bar: Current bar data
            cache: Nautilus cache (unused)
            **context: May contain prev_high and prev_low

        Returns:
            ComponentResult indicating if breakout occurred
        """
        prev_high = context.get("prev_high", self._prev_high)
        prev_low = context.get("prev_low", self._prev_low)

        if prev_high is None or prev_low is None:
            return ComponentResult(
                name=self.name,
                value=float("nan"),
                triggered=False,
                reason="Insufficient data: Previous bar not set",
            )

        close_price = float(bar.close)

        if self._comparison == "above":
            triggered = close_price > prev_high
            level = prev_high
            comparison_str = ">" if triggered else "<="
            level_name = "prev_high"
        else:
            triggered = close_price < prev_low
            level = prev_low
            comparison_str = "<" if triggered else ">="
            level_name = "prev_low"

        return ComponentResult(
            name=self.name,
            value=close_price,
            triggered=triggered,
            reason=f"Close ({close_price:.2f}) {comparison_str} {level_name} ({level:.2f})",
        )


class TimeStopComponent:
    """Check if position has been held for maximum bars.

    Evaluates whether a position has been held for longer than
    the specified maximum number of bars.

    Attributes:
        name: Component identifier
        max_bars: Maximum bars to hold position
    """

    def __init__(
        self,
        name: str,
        max_bars: int = 5,
    ) -> None:
        """Initialize time stop component.

        Args:
            name: Component identifier
            max_bars: Maximum bars to hold position
        """
        self.name = name
        self._max_bars = max_bars

    def evaluate(self, bar: Bar, cache: Cache, **context: Any) -> ComponentResult:
        """Evaluate time stop condition.

        Args:
            bar: Current bar data
            cache: Nautilus cache (unused)
            **context: Must contain bars_held (int)

        Returns:
            ComponentResult indicating if time stop triggered
        """
        bars_held = context.get("bars_held", 0)

        triggered = bars_held >= self._max_bars

        return ComponentResult(
            name=self.name,
            value=float(bars_held),
            triggered=triggered,
            reason=f"Bars held: {bars_held} {'≥' if triggered else '<'} max {self._max_bars}",
        )
