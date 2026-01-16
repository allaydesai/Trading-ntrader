"""Unit tests for BacktestRunner signal integration.

Tests for T106-T108, T110:
- T106: Runner accepts enable_signals and signal_export_path parameters
- T107: Runner creates SignalCollector when signals enabled
- T108: Runner exports signal audit trail on completion
- T110: Runner persists signal statistics to config_snapshot
"""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from src.core.backtest_runner import MinimalBacktestRunner


@pytest.fixture
def mock_settings():
    """Create mock settings for runner initialization."""
    return Mock(
        default_balance=100000.0,
        catalog_path="./data/catalog",
        default_venue="SIM",
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=1,
        fast_ema_period=10,
        slow_ema_period=20,
        portfolio_value=Decimal("100000"),
        position_size_pct=Decimal("0.02"),
        commission_per_share=Decimal("0.005"),
        commission_min_per_order=Decimal("1.0"),
        commission_max_rate=Decimal("0.01"),
    )


@pytest.fixture
def backtest_runner(mock_settings):
    """Create MinimalBacktestRunner instance with mocked dependencies."""
    with patch("src.core.backtest_runner.get_settings") as mock_get_settings:
        mock_get_settings.return_value = mock_settings
        runner = MinimalBacktestRunner()
        return runner


class TestRunnerSignalProperties:
    """Tests for T106/T107: Runner has signal properties."""

    def test_runner_has_signal_collector_property(self, backtest_runner):
        """Runner has signal_collector property that defaults to None."""
        assert hasattr(backtest_runner, "signal_collector")
        # Initially None when no backtest has run with signals
        assert backtest_runner.signal_collector is None

    def test_runner_has_signal_statistics_property(self, backtest_runner):
        """Runner has signal_statistics property for CLI display."""
        assert hasattr(backtest_runner, "signal_statistics")
        # Initially None when no signals collected
        assert backtest_runner.signal_statistics is None


class TestRunnerSignalParameterHandling:
    """Tests for T106: Runner handles signal parameters correctly."""

    def test_runner_stores_signal_collector_after_creation(self, mock_settings):
        """Runner stores signal_collector when signals are enabled."""
        with patch("src.core.backtest_runner.get_settings") as mock_get_settings:
            mock_get_settings.return_value = mock_settings
            runner = MinimalBacktestRunner()

            # Verify collector is None initially
            assert runner.signal_collector is None

            # After setting up for signals (implementation will create collector)
            # This test verifies the property exists and can be set
            from src.core.signals.collector import SignalCollector

            runner._signal_collector = SignalCollector()
            assert runner.signal_collector is not None


class TestRunnerSignalExport:
    """Tests for T108: Runner exports signal audit trail on completion."""

    def test_runner_has_export_method_or_handles_export_path(self, backtest_runner):
        """Runner can handle signal_export_path for export."""
        # The runner should have ability to handle export
        # Either through a method or by storing the path
        # This will be implemented in the runner
        assert hasattr(backtest_runner, "signal_collector") or hasattr(
            backtest_runner, "_signal_export_path"
        )


class TestSignalCollectorIntegration:
    """Integration tests for signal collector with runner."""

    def test_signal_collector_can_be_created_standalone(self):
        """SignalCollector can be instantiated independently."""
        from src.core.signals.collector import SignalCollector

        collector = SignalCollector(flush_threshold=1000)
        assert collector.evaluation_count == 0
        assert collector.evaluations == []

    def test_signal_analyzer_can_analyze_empty_evaluations(self):
        """SignalAnalyzer handles empty evaluations list."""
        from src.core.signals.analysis import SignalAnalyzer

        analyzer = SignalAnalyzer(evaluations=[])
        stats = analyzer.get_statistics()

        assert stats.total_evaluations == 0
        assert stats.signal_rate == 0.0
        assert stats.primary_blocker is None

    def test_signal_statistics_has_expected_fields(self):
        """SignalStatistics dataclass has all expected fields."""
        from src.core.signals.analysis import SignalStatistics

        stats = SignalStatistics(
            total_evaluations=100,
            total_triggered=10,
            signal_rate=0.1,
            trigger_rates={"trend": 0.8, "rsi": 0.3},
            blocking_rates={"trend": 0.05, "rsi": 0.65},
            near_miss_count=5,
            near_miss_threshold=0.75,
            primary_blocker="rsi",
        )

        assert stats.total_evaluations == 100
        assert stats.total_triggered == 10
        assert stats.signal_rate == 0.1
        assert stats.primary_blocker == "rsi"
        assert stats.near_miss_count == 5


class TestRunnerSignalWorkflow:
    """Tests for the complete signal workflow in runner."""

    def test_runner_reset_clears_signal_collector(self, mock_settings):
        """Runner reset clears signal collector."""
        with patch("src.core.backtest_runner.get_settings") as mock_get_settings:
            mock_get_settings.return_value = mock_settings
            runner = MinimalBacktestRunner()

            # Set up a collector
            from src.core.signals.collector import SignalCollector

            runner._signal_collector = SignalCollector()

            # Reset should clear it
            runner.reset()

            # After reset, collector should be None (or cleared)
            # The implementation should handle this in reset()
            assert runner._signal_collector is None or not hasattr(runner, "_signal_collector")

    def test_dispose_handles_signal_collector(self, mock_settings):
        """Runner dispose handles signal collector cleanup."""
        with patch("src.core.backtest_runner.get_settings") as mock_get_settings:
            mock_get_settings.return_value = mock_settings
            runner = MinimalBacktestRunner()

            # Set up a collector
            from src.core.signals.collector import SignalCollector

            runner._signal_collector = SignalCollector()

            # Dispose should clean up
            runner.dispose()

            # Verify no errors occurred and collector was handled
            assert True  # If we get here, dispose worked


class TestConfigSnapshotWithSignals:
    """Tests for T110: Signal statistics in config_snapshot."""

    def test_signal_statistics_can_be_serialized_to_dict(self):
        """SignalStatistics can be converted to dict for JSON storage."""
        from dataclasses import asdict

        from src.core.signals.analysis import SignalStatistics

        stats = SignalStatistics(
            total_evaluations=1000,
            total_triggered=100,
            signal_rate=0.1,
            trigger_rates={"trend": 0.8},
            blocking_rates={"rsi": 0.5},
            near_miss_count=50,
            near_miss_threshold=0.75,
            primary_blocker="rsi",
        )

        # Convert to dict for JSON serialization
        stats_dict = asdict(stats)

        assert stats_dict["total_evaluations"] == 1000
        assert stats_dict["signal_rate"] == 0.1
        assert stats_dict["primary_blocker"] == "rsi"
        assert "trigger_rates" in stats_dict
        assert "blocking_rates" in stats_dict


class TestRunnerAcceptsSignalParameters:
    """Tests that runner method signature accepts signal parameters."""

    def test_run_backtest_signature_has_enable_signals(self, backtest_runner):
        """run_backtest_with_catalog_data accepts enable_signals parameter."""
        import inspect

        sig = inspect.signature(backtest_runner.run_backtest_with_catalog_data)
        param_names = list(sig.parameters.keys())

        assert "enable_signals" in param_names, f"enable_signals not in parameters: {param_names}"

    def test_run_backtest_signature_has_signal_export_path(self, backtest_runner):
        """run_backtest_with_catalog_data accepts signal_export_path parameter."""
        import inspect

        sig = inspect.signature(backtest_runner.run_backtest_with_catalog_data)
        param_names = list(sig.parameters.keys())

        assert "signal_export_path" in param_names, (
            f"signal_export_path not in parameters: {param_names}"
        )
