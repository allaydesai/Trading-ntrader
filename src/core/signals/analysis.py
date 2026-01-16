"""Signal analysis utilities for post-backtest evaluation.

This module contains:
- SignalStatistics: Dataclass for aggregate signal statistics
- SignalAnalyzer: Analyzes signal evaluations for blocking conditions and trigger rates
"""

from collections import Counter
from dataclasses import dataclass, field

from src.core.signals.evaluation import SignalEvaluation
from src.models.signal import BlockingAnalysisResponse, BlockingComponentStats


@dataclass
class SignalStatistics:
    """Aggregate statistics for signal evaluations.

    Attributes:
        total_evaluations: Total number of signal evaluations
        total_triggered: Number of signals that triggered (signal=True)
        signal_rate: Percentage of evaluations that triggered
        trigger_rates: Per-component trigger rates (component_name -> rate)
        blocking_rates: Per-component blocking rates (component_name -> rate)
        near_miss_count: Number of near-miss evaluations
        near_miss_threshold: Threshold used for near-miss detection
        primary_blocker: Component that blocked most often
    """

    total_evaluations: int = 0
    total_triggered: int = 0
    signal_rate: float = 0.0
    trigger_rates: dict[str, float] = field(default_factory=dict)
    blocking_rates: dict[str, float] = field(default_factory=dict)
    near_miss_count: int = 0
    near_miss_threshold: float = 0.75
    primary_blocker: str | None = None


class SignalAnalyzer:
    """Analyzes signal evaluations for patterns and statistics.

    Provides post-backtest analysis including:
    - Blocking condition identification
    - Trigger rates per component
    - Near-miss detection
    - Primary blocker identification

    Example:
        >>> analyzer = SignalAnalyzer(evaluations)
        >>> stats = analyzer.get_statistics()
        >>> print(f"Signal rate: {stats.signal_rate:.1%}")
        >>> blocking = analyzer.get_blocking_analysis()
        >>> print(f"Primary blocker: {blocking.primary_blocker}")
    """

    def __init__(
        self,
        evaluations: list[SignalEvaluation],
        near_miss_threshold: float = 0.75,
    ) -> None:
        """Initialize the signal analyzer.

        Args:
            evaluations: List of signal evaluations to analyze
            near_miss_threshold: Threshold for near-miss detection (default 0.75)
        """
        self._evaluations = evaluations
        self._near_miss_threshold = near_miss_threshold

    def get_blocking_analysis(self) -> BlockingAnalysisResponse:
        """Analyze blocking conditions across all evaluations.

        Returns:
            BlockingAnalysisResponse with per-component blocking statistics
        """
        # Count failed signals
        failed_evaluations = [e for e in self._evaluations if not e.signal]
        total_failed = len(failed_evaluations)

        if total_failed == 0:
            return BlockingAnalysisResponse(
                total_failed_signals=0,
                components=[],
                primary_blocker=None,
            )

        # Count blocks per component
        blocking_counts: Counter[str] = Counter()
        strength_sums: dict[str, float] = {}
        strength_counts: dict[str, int] = {}

        for evaluation in failed_evaluations:
            if evaluation.blocking_component:
                blocker = evaluation.blocking_component
                blocking_counts[blocker] += 1

                # Track strength for average calculation
                if blocker not in strength_sums:
                    strength_sums[blocker] = 0.0
                    strength_counts[blocker] = 0
                strength_sums[blocker] += evaluation.strength
                strength_counts[blocker] += 1

        # Build component stats
        component_stats = []
        for component_name, block_count in blocking_counts.most_common():
            avg_strength = strength_sums[component_name] / strength_counts[component_name]
            component_stats.append(
                BlockingComponentStats(
                    component_name=component_name,
                    block_count=block_count,
                    block_rate=block_count / total_failed,
                    avg_strength_when_blocking=avg_strength,
                )
            )

        # Primary blocker is the most frequent
        primary_blocker = blocking_counts.most_common(1)[0][0] if blocking_counts else None

        return BlockingAnalysisResponse(
            total_failed_signals=total_failed,
            components=component_stats,
            primary_blocker=primary_blocker,
        )

    def get_statistics(self) -> SignalStatistics:
        """Calculate aggregate signal statistics.

        Returns:
            SignalStatistics with trigger rates, blocking rates, and near-miss info
        """
        total = len(self._evaluations)

        if total == 0:
            return SignalStatistics(
                total_evaluations=0,
                total_triggered=0,
                signal_rate=0.0,
                trigger_rates={},
                blocking_rates={},
                near_miss_count=0,
                near_miss_threshold=self._near_miss_threshold,
                primary_blocker=None,
            )

        # Count triggered signals
        triggered_count = sum(1 for e in self._evaluations if e.signal)
        signal_rate = triggered_count / total

        # Calculate trigger rates per component
        component_trigger_counts: Counter[str] = Counter()
        component_total_counts: Counter[str] = Counter()

        for evaluation in self._evaluations:
            for component in evaluation.components:
                component_total_counts[component.name] += 1
                if component.triggered:
                    component_trigger_counts[component.name] += 1

        trigger_rates = {
            name: component_trigger_counts[name] / count
            for name, count in component_total_counts.items()
        }

        # Get blocking analysis for blocking rates
        blocking_analysis = self.get_blocking_analysis()
        blocking_rates = {c.component_name: c.block_rate for c in blocking_analysis.components}

        # Count near misses
        near_miss_count = sum(
            1 for e in self._evaluations if not e.signal and e.strength >= self._near_miss_threshold
        )

        return SignalStatistics(
            total_evaluations=total,
            total_triggered=triggered_count,
            signal_rate=signal_rate,
            trigger_rates=trigger_rates,
            blocking_rates=blocking_rates,
            near_miss_count=near_miss_count,
            near_miss_threshold=self._near_miss_threshold,
            primary_blocker=blocking_analysis.primary_blocker,
        )

    def get_near_misses(self) -> list[SignalEvaluation]:
        """Get all near-miss evaluations.

        Returns:
            List of evaluations where signal=False but strength >= threshold
        """
        return [
            e for e in self._evaluations if not e.signal and e.strength >= self._near_miss_threshold
        ]
