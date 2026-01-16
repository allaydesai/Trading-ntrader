"""Core evaluation data structures for signal validation.

This module contains:
- ComponentResult: Immutable result of a single condition evaluation
- SignalEvaluation: Complete state of all conditions at a point in time
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ComponentResult:
    """Immutable result of a single signal condition evaluation.

    Attributes:
        name: Component identifier (e.g., "trend_filter", "rsi_threshold")
        value: The calculated value (e.g., RSI=8.5, Close=152.30)
        triggered: Did the condition pass?
        reason: Human-readable explanation of result

    Example:
        >>> result = ComponentResult(
        ...     name="rsi_oversold",
        ...     value=8.5,
        ...     triggered=True,
        ...     reason="RSI(2) = 8.5 < threshold 10"
        ... )
        >>> result.triggered
        True
    """

    name: str
    value: float
    triggered: bool
    reason: str

    def __post_init__(self) -> None:
        """Validate component result fields."""
        if not self.name:
            raise ValueError("Component name cannot be empty")


@dataclass
class SignalEvaluation:
    """Complete state of all conditions at a single point in time.

    Attributes:
        timestamp: Bar timestamp in nanoseconds (Nautilus format)
        bar_type: Bar type identifier (e.g., "AAPL.XNAS-1-DAY-LAST")
        components: Ordered list of component evaluations
        signal: Final composite signal result
        strength: Ratio of passed conditions to total (0.0-1.0)
        blocking_component: Name of first failing component (if signal=False)
        signal_type: Type of signal ("entry" or "exit")
        order_id: Optional order ID if signal triggered an order
        trade_id: Optional trade ID if order resulted in a trade

    Example:
        >>> evaluation = SignalEvaluation(
        ...     timestamp=1704067200000000000,
        ...     bar_type="AAPL.XNAS-1-DAY-LAST",
        ...     components=[
        ...         ComponentResult("trend", 152.3, True, "Close > SMA"),
        ...         ComponentResult("rsi", 8.5, True, "RSI < 10"),
        ...     ],
        ...     signal=True,
        ...     strength=1.0,
        ... )
        >>> evaluation.passed_count
        2
    """

    timestamp: int
    bar_type: str
    components: list[ComponentResult]
    signal: bool
    strength: float
    blocking_component: str | None = None
    signal_type: str | None = None
    order_id: str | None = None
    trade_id: str | None = None
    _id: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate signal evaluation fields."""
        if not self.components:
            raise ValueError("SignalEvaluation must have at least one component")
        if self.timestamp <= 0:
            raise ValueError("Timestamp must be positive")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError("Strength must be between 0.0 and 1.0")
        if self.signal and self.blocking_component is not None:
            raise ValueError("blocking_component must be None when signal is True")

    @property
    def passed_count(self) -> int:
        """Number of components where triggered=True."""
        return sum(1 for c in self.components if c.triggered)

    @property
    def total_count(self) -> int:
        """Total number of components."""
        return len(self.components)

    @property
    def is_near_miss(self) -> bool:
        """Check if this is a near-miss (high strength but signal=False).

        Default threshold is 75% (0.75).
        """
        return self.strength >= 0.75 and not self.signal

    def is_near_miss_with_threshold(self, threshold: float = 0.75) -> bool:
        """Check if this is a near-miss with custom threshold.

        Args:
            threshold: Minimum strength to consider as near-miss (default 0.75)

        Returns:
            True if strength >= threshold and signal is False
        """
        return self.strength >= threshold and not self.signal
