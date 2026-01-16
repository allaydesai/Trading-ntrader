"""Unit tests for SignalAnalyzer.

Tests cover:
- Blocking component tracking
- Blocking rate calculation
- Primary blocker identification
- Trigger rate calculation per component
- Near-miss filtering
- Signal statistics
"""

import pytest

from src.core.signals.analysis import SignalAnalyzer
from src.core.signals.evaluation import ComponentResult, SignalEvaluation


@pytest.fixture
def sample_evaluations() -> list[SignalEvaluation]:
    """Provide sample evaluations for testing.

    Creates a mix of signals:
    - 2 triggered (signal=True)
    - 3 blocked by 'trend' component
    - 2 blocked by 'rsi' component
    - 1 blocked by 'volume' component
    """
    return [
        # Triggered signals
        SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 152.0, True, "Close > SMA"),
                ComponentResult("rsi", 8.5, True, "RSI < 10"),
                ComponentResult("volume", 1.5, True, "Volume > 1.5x avg"),
            ],
            signal=True,
            strength=1.0,
        ),
        SignalEvaluation(
            timestamp=1704153600000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 155.0, True, "Close > SMA"),
                ComponentResult("rsi", 9.0, True, "RSI < 10"),
                ComponentResult("volume", 1.6, True, "Volume > 1.5x avg"),
            ],
            signal=True,
            strength=1.0,
        ),
        # Blocked by trend (3 times)
        SignalEvaluation(
            timestamp=1704240000000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 148.0, False, "Close < SMA"),
                ComponentResult("rsi", 7.0, True, "RSI < 10"),
                ComponentResult("volume", 1.8, True, "Volume > 1.5x avg"),
            ],
            signal=False,
            strength=2 / 3,
            blocking_component="trend",
        ),
        SignalEvaluation(
            timestamp=1704326400000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 145.0, False, "Close < SMA"),
                ComponentResult("rsi", 6.0, True, "RSI < 10"),
                ComponentResult("volume", 2.0, True, "Volume > 1.5x avg"),
            ],
            signal=False,
            strength=2 / 3,
            blocking_component="trend",
        ),
        SignalEvaluation(
            timestamp=1704412800000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 147.0, False, "Close < SMA"),
                ComponentResult("rsi", 8.0, True, "RSI < 10"),
                ComponentResult("volume", 1.7, True, "Volume > 1.5x avg"),
            ],
            signal=False,
            strength=2 / 3,
            blocking_component="trend",
        ),
        # Blocked by rsi (2 times)
        SignalEvaluation(
            timestamp=1704499200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 160.0, True, "Close > SMA"),
                ComponentResult("rsi", 15.0, False, "RSI >= 10"),
                ComponentResult("volume", 1.5, True, "Volume > 1.5x avg"),
            ],
            signal=False,
            strength=2 / 3,
            blocking_component="rsi",
        ),
        SignalEvaluation(
            timestamp=1704585600000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 162.0, True, "Close > SMA"),
                ComponentResult("rsi", 20.0, False, "RSI >= 10"),
                ComponentResult("volume", 1.6, True, "Volume > 1.5x avg"),
            ],
            signal=False,
            strength=2 / 3,
            blocking_component="rsi",
        ),
        # Blocked by volume (1 time)
        SignalEvaluation(
            timestamp=1704672000000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[
                ComponentResult("trend", 158.0, True, "Close > SMA"),
                ComponentResult("rsi", 9.5, True, "RSI < 10"),
                ComponentResult("volume", 1.0, False, "Volume < 1.5x avg"),
            ],
            signal=False,
            strength=2 / 3,
            blocking_component="volume",
        ),
    ]


class TestSignalAnalyzerBlockingTracking:
    """Tests for blocking component tracking."""

    def test_count_blocked_signals(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test counting total blocked signals."""
        analyzer = SignalAnalyzer(sample_evaluations)

        blocking_analysis = analyzer.get_blocking_analysis()

        assert blocking_analysis.total_failed_signals == 6  # 8 total - 2 triggered

    def test_blocking_counts_per_component(
        self, sample_evaluations: list[SignalEvaluation]
    ) -> None:
        """Test blocking counts for each component."""
        analyzer = SignalAnalyzer(sample_evaluations)

        blocking_analysis = analyzer.get_blocking_analysis()

        # Convert to dict for easier testing
        block_counts = {c.component_name: c.block_count for c in blocking_analysis.components}

        assert block_counts["trend"] == 3
        assert block_counts["rsi"] == 2
        assert block_counts["volume"] == 1

    def test_blocking_only_counts_when_component_is_blocker(self) -> None:
        """Test that blocking only counts when component is the blocking_component."""
        evaluations = [
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[
                    ComponentResult("c1", 1.0, False, "failed"),
                    ComponentResult("c2", 2.0, False, "failed"),
                ],
                signal=False,
                strength=0.0,
                blocking_component="c1",  # Only c1 is the blocker
            ),
        ]

        analyzer = SignalAnalyzer(evaluations)
        blocking_analysis = analyzer.get_blocking_analysis()

        block_counts = {c.component_name: c.block_count for c in blocking_analysis.components}

        assert block_counts["c1"] == 1
        assert block_counts.get("c2", 0) == 0  # c2 is not the blocker


class TestSignalAnalyzerBlockingRates:
    """Tests for blocking rate calculation."""

    def test_blocking_rate_calculation(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test blocking rate as percentage of failed signals."""
        analyzer = SignalAnalyzer(sample_evaluations)

        blocking_analysis = analyzer.get_blocking_analysis()

        # Convert to dict for easier testing
        block_rates = {c.component_name: c.block_rate for c in blocking_analysis.components}

        # 6 failed signals total
        assert block_rates["trend"] == pytest.approx(3 / 6, rel=0.01)  # 50%
        assert block_rates["rsi"] == pytest.approx(2 / 6, rel=0.01)  # 33.3%
        assert block_rates["volume"] == pytest.approx(1 / 6, rel=0.01)  # 16.7%

    def test_blocking_rate_zero_when_no_failures(self) -> None:
        """Test blocking rate is 0 when no failed signals."""
        evaluations = [
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[ComponentResult("c1", 1.0, True, "passed")],
                signal=True,
                strength=1.0,
            ),
        ]

        analyzer = SignalAnalyzer(evaluations)
        blocking_analysis = analyzer.get_blocking_analysis()

        assert blocking_analysis.total_failed_signals == 0
        assert len(blocking_analysis.components) == 0


class TestSignalAnalyzerPrimaryBlocker:
    """Tests for primary blocker identification."""

    def test_primary_blocker_is_most_frequent(
        self, sample_evaluations: list[SignalEvaluation]
    ) -> None:
        """Test primary blocker is the component that blocks most often."""
        analyzer = SignalAnalyzer(sample_evaluations)

        blocking_analysis = analyzer.get_blocking_analysis()

        assert blocking_analysis.primary_blocker == "trend"  # Blocks 3 times

    def test_primary_blocker_none_when_no_failures(self) -> None:
        """Test primary blocker is None when no failures."""
        evaluations = [
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[ComponentResult("c1", 1.0, True, "passed")],
                signal=True,
                strength=1.0,
            ),
        ]

        analyzer = SignalAnalyzer(evaluations)
        blocking_analysis = analyzer.get_blocking_analysis()

        assert blocking_analysis.primary_blocker is None

    def test_primary_blocker_tie_breaker(self) -> None:
        """Test tie-breaker for primary blocker (first in evaluation order)."""
        evaluations = [
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[
                    ComponentResult("c1", 1.0, False, "failed"),
                    ComponentResult("c2", 2.0, True, "passed"),
                ],
                signal=False,
                strength=0.5,
                blocking_component="c1",
            ),
            SignalEvaluation(
                timestamp=1704153600000000000,
                bar_type="TEST",
                components=[
                    ComponentResult("c1", 1.0, True, "passed"),
                    ComponentResult("c2", 2.0, False, "failed"),
                ],
                signal=False,
                strength=0.5,
                blocking_component="c2",
            ),
        ]

        analyzer = SignalAnalyzer(evaluations)
        blocking_analysis = analyzer.get_blocking_analysis()

        # Both block once, should pick alphabetically first or first encountered
        assert blocking_analysis.primary_blocker in ["c1", "c2"]


class TestSignalAnalyzerTriggerRates:
    """Tests for trigger rate calculation per component."""

    def test_trigger_rate_calculation(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test trigger rate as percentage of evaluations where component passed."""
        analyzer = SignalAnalyzer(sample_evaluations)

        statistics = analyzer.get_statistics()

        # 8 total evaluations
        # trend: triggered in 2 + 3 (blocked by other) = 5 times, failed in 3 = 62.5%
        # Wait - let me recalculate:
        # From sample_evaluations:
        # - trend triggered: 2 (signal=True) + 2 (blocked by rsi) + 1 (blocked by volume) = 5
        # - trend not triggered: 3 (blocked by trend)
        # So trend trigger rate = 5/8 = 62.5%

        assert statistics.trigger_rates["trend"] == pytest.approx(5 / 8, rel=0.01)

        # rsi: triggered in 2 + 3 + 1 = 6, failed in 2 = 75%
        assert statistics.trigger_rates["rsi"] == pytest.approx(6 / 8, rel=0.01)

        # volume: triggered in 2 + 3 + 2 = 7, failed in 1 = 87.5%
        assert statistics.trigger_rates["volume"] == pytest.approx(7 / 8, rel=0.01)

    def test_trigger_rate_all_pass(self) -> None:
        """Test trigger rate is 1.0 when component always passes."""
        evaluations = [
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[ComponentResult("c1", 1.0, True, "passed")],
                signal=True,
                strength=1.0,
            ),
            SignalEvaluation(
                timestamp=1704153600000000000,
                bar_type="TEST",
                components=[ComponentResult("c1", 2.0, True, "passed")],
                signal=True,
                strength=1.0,
            ),
        ]

        analyzer = SignalAnalyzer(evaluations)
        statistics = analyzer.get_statistics()

        assert statistics.trigger_rates["c1"] == 1.0

    def test_trigger_rate_none_pass(self) -> None:
        """Test trigger rate is 0.0 when component never passes."""
        evaluations = [
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[ComponentResult("c1", 1.0, False, "failed")],
                signal=False,
                strength=0.0,
                blocking_component="c1",
            ),
            SignalEvaluation(
                timestamp=1704153600000000000,
                bar_type="TEST",
                components=[ComponentResult("c1", 2.0, False, "failed")],
                signal=False,
                strength=0.0,
                blocking_component="c1",
            ),
        ]

        analyzer = SignalAnalyzer(evaluations)
        statistics = analyzer.get_statistics()

        assert statistics.trigger_rates["c1"] == 0.0


class TestSignalAnalyzerNearMiss:
    """Tests for near-miss identification."""

    def test_near_miss_count_default_threshold(
        self, sample_evaluations: list[SignalEvaluation]
    ) -> None:
        """Test near-miss count with default 0.75 threshold."""
        analyzer = SignalAnalyzer(sample_evaluations)

        statistics = analyzer.get_statistics()

        # Near miss: strength >= 0.75 and signal=False
        # All failed signals have strength 2/3 = 0.667 < 0.75
        assert statistics.near_miss_count == 0

    def test_near_miss_count_custom_threshold(self) -> None:
        """Test near-miss count with custom threshold."""
        evaluations = [
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[
                    ComponentResult("c1", 1.0, True, "passed"),
                    ComponentResult("c2", 2.0, True, "passed"),
                    ComponentResult("c3", 3.0, False, "failed"),
                ],
                signal=False,
                strength=2 / 3,  # 0.667
                blocking_component="c3",
            ),
        ]

        analyzer = SignalAnalyzer(evaluations, near_miss_threshold=0.6)
        statistics = analyzer.get_statistics()

        assert statistics.near_miss_count == 1
        assert statistics.near_miss_threshold == 0.6

    def test_near_miss_does_not_count_triggered_signals(self) -> None:
        """Test that triggered signals are never counted as near misses."""
        evaluations = [
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[ComponentResult("c1", 1.0, True, "passed")],
                signal=True,
                strength=1.0,
            ),
        ]

        analyzer = SignalAnalyzer(evaluations, near_miss_threshold=0.5)
        statistics = analyzer.get_statistics()

        assert statistics.near_miss_count == 0

    def test_get_near_misses_returns_evaluations(self) -> None:
        """Test get_near_misses returns the actual near-miss evaluations."""
        near_miss_eval = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="TEST",
            components=[
                ComponentResult("c1", 1.0, True, "passed"),
                ComponentResult("c2", 2.0, True, "passed"),
                ComponentResult("c3", 3.0, True, "passed"),
                ComponentResult("c4", 4.0, False, "failed"),
            ],
            signal=False,
            strength=0.75,
            blocking_component="c4",
        )

        evaluations = [near_miss_eval]

        analyzer = SignalAnalyzer(evaluations)
        near_misses = analyzer.get_near_misses()

        assert len(near_misses) == 1
        assert near_misses[0] is near_miss_eval


class TestSignalStatistics:
    """Tests for SignalStatistics dataclass."""

    def test_statistics_basic_values(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test basic statistics values."""
        analyzer = SignalAnalyzer(sample_evaluations)

        statistics = analyzer.get_statistics()

        assert statistics.total_evaluations == 8
        assert statistics.total_triggered == 2
        assert statistics.signal_rate == pytest.approx(2 / 8, rel=0.01)

    def test_statistics_empty_evaluations(self) -> None:
        """Test statistics with empty evaluations list."""
        analyzer = SignalAnalyzer([])

        statistics = analyzer.get_statistics()

        assert statistics.total_evaluations == 0
        assert statistics.total_triggered == 0
        assert statistics.signal_rate == 0.0
        assert statistics.near_miss_count == 0


class TestSignalAnalyzerAvgStrengthWhenBlocking:
    """Tests for average strength when blocking calculation."""

    def test_avg_strength_when_blocking(self, sample_evaluations: list[SignalEvaluation]) -> None:
        """Test average signal strength when component is blocking."""
        analyzer = SignalAnalyzer(sample_evaluations)

        blocking_analysis = analyzer.get_blocking_analysis()

        # Find trend component stats
        trend_stats = next(c for c in blocking_analysis.components if c.component_name == "trend")

        # All trend blocks have strength 2/3
        assert trend_stats.avg_strength_when_blocking == pytest.approx(2 / 3, rel=0.01)
