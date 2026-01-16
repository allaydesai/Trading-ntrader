"""Signal validation integration for Nautilus strategies.

This module provides:
- SignalValidationMixin: A mixin class that adds signal validation to existing strategies

Example:
    >>> class MyStrategy(Strategy, SignalValidationMixin):
    ...     def __init__(self, config):
    ...         super().__init__(config)
    ...         self.init_signal_validation()
    ...
    ...     def on_start(self):
    ...         # Create entry signal with 3 components
    ...         self.create_entry_signal(
    ...             name="entry",
    ...             components=[trend, rsi, volume],
    ...             logic=CombinationLogic.AND,
    ...         )
    ...
    ...     def on_bar(self, bar):
    ...         # Evaluate entry signal (automatically recorded)
    ...         result = self.evaluate_entry_signal(bar, self.cache)
    ...         if result.signal:
    ...             self.submit_order(order)
"""

from pathlib import Path
from typing import Any

from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.data import Bar

from src.core.signals.analysis import SignalAnalyzer, SignalStatistics
from src.core.signals.collector import SignalCollector
from src.core.signals.components import SignalComponent
from src.core.signals.composite import CombinationLogic, CompositeSignalGenerator
from src.core.signals.evaluation import SignalEvaluation
from src.models.signal import BlockingAnalysisResponse


class SignalValidationMixin:
    """Mixin class that adds signal validation capabilities to strategies.

    This mixin provides methods for creating and evaluating composite signals
    with automatic audit trail capture. It is designed to be used with
    Nautilus Trader Strategy classes.

    Attributes:
        _signal_collector: Collector for recording signal evaluations
        _entry_signal: Optional entry signal generator
        _exit_signal: Optional exit signal generator
        _near_miss_threshold: Threshold for near-miss detection

    Example:
        >>> class MyStrategy(Strategy, SignalValidationMixin):
        ...     def __init__(self, config):
        ...         super().__init__(config)
        ...         self.init_signal_validation()
    """

    _signal_collector: SignalCollector
    _entry_signal: CompositeSignalGenerator | None
    _exit_signal: CompositeSignalGenerator | None
    _near_miss_threshold: float

    def init_signal_validation(
        self,
        flush_threshold: int = 10000,
        near_miss_threshold: float = 0.75,
    ) -> None:
        """Initialize signal validation components.

        Args:
            flush_threshold: Number of evaluations before flushing to disk
            near_miss_threshold: Threshold for near-miss detection (0.0-1.0)
        """
        self._signal_collector = SignalCollector(flush_threshold=flush_threshold)
        self._entry_signal = None
        self._exit_signal = None
        self._near_miss_threshold = near_miss_threshold

    def create_entry_signal(
        self,
        name: str,
        components: list[SignalComponent],
        logic: CombinationLogic = CombinationLogic.AND,
    ) -> CompositeSignalGenerator:
        """Create and register an entry signal generator.

        Args:
            name: Name for the composite signal
            components: List of signal components
            logic: Combination logic (AND/OR)

        Returns:
            The created CompositeSignalGenerator
        """
        self._entry_signal = CompositeSignalGenerator(
            name=name,
            components=components,
            logic=logic,
        )
        return self._entry_signal

    def create_exit_signal(
        self,
        name: str,
        components: list[SignalComponent],
        logic: CombinationLogic = CombinationLogic.OR,
    ) -> CompositeSignalGenerator:
        """Create and register an exit signal generator.

        Args:
            name: Name for the composite signal
            components: List of signal components
            logic: Combination logic (AND/OR), defaults to OR for exits

        Returns:
            The created CompositeSignalGenerator
        """
        self._exit_signal = CompositeSignalGenerator(
            name=name,
            components=components,
            logic=logic,
        )
        return self._exit_signal

    def evaluate_entry_signal(
        self,
        bar: Bar,
        cache: Cache,
        **context: Any,
    ) -> SignalEvaluation:
        """Evaluate the entry signal and record the result.

        Args:
            bar: Current bar data
            cache: Nautilus cache
            **context: Additional context for component evaluation

        Returns:
            SignalEvaluation with the evaluation result

        Raises:
            ValueError: If no entry signal has been created
        """
        if self._entry_signal is None:
            raise ValueError("No entry signal has been created. Call create_entry_signal first.")

        evaluation = self._entry_signal.evaluate(bar, cache, **context)
        self._signal_collector.record(evaluation)
        return evaluation

    def evaluate_exit_signal(
        self,
        bar: Bar,
        cache: Cache,
        **context: Any,
    ) -> SignalEvaluation:
        """Evaluate the exit signal and record the result.

        Args:
            bar: Current bar data
            cache: Nautilus cache
            **context: Additional context for component evaluation

        Returns:
            SignalEvaluation with the evaluation result

        Raises:
            ValueError: If no exit signal has been created
        """
        if self._exit_signal is None:
            raise ValueError("No exit signal has been created. Call create_exit_signal first.")

        evaluation = self._exit_signal.evaluate(bar, cache, **context)
        self._signal_collector.record(evaluation)
        return evaluation

    def get_signal_statistics(self) -> SignalStatistics:
        """Get aggregate statistics for all recorded signals.

        Returns:
            SignalStatistics with trigger rates, blocking rates, etc.
        """
        evaluations = self._signal_collector.evaluations
        analyzer = SignalAnalyzer(
            evaluations,
            near_miss_threshold=self._near_miss_threshold,
        )
        return analyzer.get_statistics()

    def get_blocking_analysis(self) -> BlockingAnalysisResponse:
        """Get blocking condition analysis for failed signals.

        Returns:
            BlockingAnalysisResponse with per-component blocking statistics
        """
        evaluations = self._signal_collector.evaluations
        analyzer = SignalAnalyzer(
            evaluations,
            near_miss_threshold=self._near_miss_threshold,
        )
        return analyzer.get_blocking_analysis()

    def get_near_misses(self) -> list[SignalEvaluation]:
        """Get evaluations that were near-misses.

        Near-misses are evaluations where signal=False but strength >= threshold.

        Returns:
            List of near-miss evaluations
        """
        evaluations = self._signal_collector.evaluations
        analyzer = SignalAnalyzer(
            evaluations,
            near_miss_threshold=self._near_miss_threshold,
        )
        return analyzer.get_near_misses()

    def export_signal_audit(self, output_path: str) -> None:
        """Export signal audit trail to CSV.

        Args:
            output_path: Path for the output CSV file
        """
        self._signal_collector.export_csv(Path(output_path))

    def finalize_signals(self, output_path: str | None = None) -> None:
        """Finalize signal collection, merging any chunks.

        Args:
            output_path: Optional path for final merged CSV file

        Note:
            Call this when the strategy stops or backtest completes.
        """
        if output_path:
            self._signal_collector.finalize(Path(output_path))
        # Mark as finalized even without output
        self._signal_collector._finalized = True
