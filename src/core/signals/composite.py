"""Composite signal generator with AND/OR logic.

This module contains:
- CombinationLogic: Enum for combining conditions
- CompositeSignalGenerator: Orchestrates evaluation of multiple components
"""

from enum import Enum
from typing import Any

from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.data import Bar

from src.core.signals.components import SignalComponent
from src.core.signals.evaluation import ComponentResult, SignalEvaluation


class CombinationLogic(str, Enum):
    """How to combine multiple signal conditions."""

    AND = "and"  # All conditions must pass
    OR = "or"  # At least one condition must pass


class CompositeSignalGenerator:
    """Orchestrates evaluation of multiple signal components.

    Evaluates each component in sequence, combines results using configured
    logic (AND/OR), calculates signal strength, and identifies blocking
    conditions.

    Attributes:
        name: Unique identifier for this signal generator
        components: List of SignalComponent implementations
        logic: CombinationLogic (AND or OR)
        bar_type: Optional bar type identifier for evaluations
        signal_type: Optional signal type ("entry" or "exit")

    Example:
        >>> generator = CompositeSignalGenerator(
        ...     name="entry_signal",
        ...     components=[trend_filter, rsi_component],
        ...     logic=CombinationLogic.AND,
        ... )
        >>> evaluation = generator.evaluate(bar, cache)
        >>> if evaluation.signal:
        ...     # Execute trade
    """

    def __init__(
        self,
        name: str,
        components: list[SignalComponent],
        logic: CombinationLogic = CombinationLogic.AND,
        bar_type: str = "unknown",
        signal_type: str | None = None,
    ) -> None:
        """Initialize composite signal generator.

        Args:
            name: Unique identifier for this signal generator
            components: List of SignalComponent implementations to evaluate
            logic: How to combine component results (AND/OR)
            bar_type: Bar type identifier for evaluations
            signal_type: Optional signal type ("entry" or "exit")

        Raises:
            ValueError: If components list is empty
        """
        if not components:
            raise ValueError("CompositeSignalGenerator requires at least one component")

        self.name = name
        self.components = components
        self.logic = logic
        self.bar_type = bar_type
        self.signal_type = signal_type

    def evaluate(
        self,
        bar: Bar | Any,
        cache: Cache | Any,
        **context: Any,
    ) -> SignalEvaluation:
        """Evaluate all components and produce a signal evaluation.

        Evaluates each component in order, tracks pass/fail results,
        identifies the first failing component as the blocker (for AND logic),
        and calculates signal strength.

        Args:
            bar: Current bar data (Nautilus Bar or mock)
            cache: Nautilus cache for accessing indicators
            **context: Additional context passed to components (e.g., bars_held)

        Returns:
            SignalEvaluation with complete evaluation state
        """
        results: list[ComponentResult] = []
        blocking_component: str | None = None
        passed_count = 0

        for component in self.components:
            result = component.evaluate(bar, cache, **context)
            results.append(result)

            if result.triggered:
                passed_count += 1
            elif blocking_component is None:
                # Track first failing component
                blocking_component = result.name

        total_count = len(results)
        strength = passed_count / total_count if total_count > 0 else 0.0

        # Determine final signal based on logic
        if self.logic == CombinationLogic.AND:
            final_signal = passed_count == total_count
        else:  # OR logic
            final_signal = passed_count >= 1

        # Clear blocking component if signal triggered
        if final_signal:
            blocking_component = None

        # Get timestamp from bar if available
        timestamp = getattr(bar, "ts_event", 1)

        # Get bar_type from bar if available, otherwise use configured default
        bar_type_attr = getattr(bar, "bar_type", None)
        bar_type = str(bar_type_attr) if bar_type_attr is not None else self.bar_type

        return SignalEvaluation(
            timestamp=timestamp,
            bar_type=bar_type,
            components=results,
            signal=final_signal,
            strength=strength,
            blocking_component=blocking_component,
            signal_type=self.signal_type,
        )

    def __repr__(self) -> str:
        """Return string representation."""
        component_names = [c.name for c in self.components]
        return (
            f"CompositeSignalGenerator(name={self.name!r}, "
            f"logic={self.logic.value}, "
            f"components={component_names})"
        )
