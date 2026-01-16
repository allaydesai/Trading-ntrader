"""Unit tests for SignalValidationMixin.

Tests cover:
- Creating composite signals from components
- Signal evaluation with automatic audit capture
- Entry and exit signal distinction
- Order-signal linking
"""

from unittest.mock import Mock

from src.core.signals.collector import SignalCollector
from src.core.signals.components import (
    RSIThresholdComponent,
    TrendFilterComponent,
)
from src.core.signals.composite import CombinationLogic, CompositeSignalGenerator
from src.core.signals.evaluation import ComponentResult, SignalEvaluation


class TestSignalValidationMixin:
    """Tests for SignalValidationMixin class."""

    def test_import_mixin(self) -> None:
        """Test SignalValidationMixin can be imported."""
        from src.core.signals.integration import SignalValidationMixin

        assert SignalValidationMixin is not None

    def test_mixin_has_collector(self) -> None:
        """Test mixin initializes with a SignalCollector."""
        from src.core.signals.integration import SignalValidationMixin

        # Create a mock strategy with the mixin
        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()
        assert hasattr(strategy, "_signal_collector")
        assert isinstance(strategy._signal_collector, SignalCollector)

    def test_create_entry_signal_generator(self) -> None:
        """Test creating an entry signal generator."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()

        # Create mock indicators
        mock_sma = Mock()
        mock_sma.initialized = True
        mock_sma.value = 150.0
        mock_sma.period = 200

        mock_rsi = Mock()
        mock_rsi.initialized = True
        mock_rsi.value = 8.0
        mock_rsi.period = 2

        # Create components
        trend = TrendFilterComponent("trend", mock_sma, direction="above")
        rsi = RSIThresholdComponent("rsi", mock_rsi, threshold=10.0, direction="below")

        # Create entry signal
        entry_signal = strategy.create_entry_signal(
            name="entry",
            components=[trend, rsi],
            logic=CombinationLogic.AND,
        )

        assert entry_signal is not None
        assert isinstance(entry_signal, CompositeSignalGenerator)
        assert strategy._entry_signal is entry_signal

    def test_create_exit_signal_generator(self) -> None:
        """Test creating an exit signal generator."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()

        # Create mock indicators
        mock_sma = Mock()
        mock_sma.initialized = True
        mock_sma.value = 150.0
        mock_sma.period = 200

        # Create components
        trend = TrendFilterComponent("trend", mock_sma, direction="below")

        # Create exit signal
        exit_signal = strategy.create_exit_signal(
            name="exit",
            components=[trend],
            logic=CombinationLogic.OR,
        )

        assert exit_signal is not None
        assert isinstance(exit_signal, CompositeSignalGenerator)
        assert strategy._exit_signal is exit_signal

    def test_evaluate_entry_signal_records_to_collector(self) -> None:
        """Test that evaluating entry signal records evaluation to collector."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()

        # Create mock bar - bar_type needs __str__ for string conversion
        mock_bar = Mock()
        mock_bar_type = Mock()
        mock_bar_type.__str__ = Mock(return_value="AAPL.XNAS-1-DAY-LAST")
        mock_bar.bar_type = mock_bar_type
        mock_bar.ts_event = 1704067200000000000
        mock_bar.close = Mock()
        mock_bar.close.__float__ = Mock(return_value=152.0)
        mock_bar.volume = Mock()
        mock_bar.volume.__float__ = Mock(return_value=1000000.0)

        # Create mock cache
        mock_cache = Mock()

        # Create mock indicators
        mock_sma = Mock()
        mock_sma.initialized = True
        mock_sma.value = 150.0
        mock_sma.period = 200

        mock_rsi = Mock()
        mock_rsi.initialized = True
        mock_rsi.value = 8.0
        mock_rsi.period = 2

        # Create components and entry signal
        trend = TrendFilterComponent("trend", mock_sma, direction="above")
        rsi = RSIThresholdComponent("rsi", mock_rsi, threshold=10.0, direction="below")

        strategy.create_entry_signal(
            name="entry",
            components=[trend, rsi],
            logic=CombinationLogic.AND,
        )

        # Evaluate entry signal
        result = strategy.evaluate_entry_signal(mock_bar, mock_cache)

        # Verify result
        assert result.signal is True
        assert result.strength == 1.0

        # Verify recorded to collector
        evaluations = strategy._signal_collector.evaluations
        assert len(evaluations) == 1
        assert evaluations[0].signal is True
        assert evaluations[0].bar_type == "AAPL.XNAS-1-DAY-LAST"

    def test_evaluate_exit_signal_records_to_collector(self) -> None:
        """Test that evaluating exit signal records evaluation to collector."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()

        # Create mock bar
        mock_bar = Mock()
        mock_bar.bar_type = "AAPL.XNAS-1-DAY-LAST"
        mock_bar.ts_event = 1704067200000000000
        mock_bar.close = Mock()
        mock_bar.close.__float__ = Mock(return_value=152.0)

        # Create mock cache
        mock_cache = Mock()

        # Create mock indicator
        mock_sma = Mock()
        mock_sma.initialized = True
        mock_sma.value = 155.0  # Close < SMA, so "below" triggers
        mock_sma.period = 200

        # Create exit signal with "below" direction
        trend = TrendFilterComponent("trend", mock_sma, direction="below")
        strategy.create_exit_signal(
            name="exit",
            components=[trend],
            logic=CombinationLogic.OR,
        )

        # Evaluate exit signal
        result = strategy.evaluate_exit_signal(mock_bar, mock_cache)

        # Close (152) < SMA (155), so "below" triggers = True
        assert result.signal is True

        # Verify recorded to collector
        evaluations = strategy._signal_collector.evaluations
        assert len(evaluations) == 1

    def test_get_signal_statistics(self) -> None:
        """Test getting signal statistics from mixin."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()

        # Get statistics (empty)
        stats = strategy.get_signal_statistics()

        assert stats.total_evaluations == 0
        assert stats.total_triggered == 0

    def test_export_signal_audit_trail(self) -> None:
        """Test exporting signal audit trail to CSV."""
        import tempfile
        from pathlib import Path

        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()

        # Record some evaluations manually
        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[ComponentResult("trend", 152.0, True, "Close > SMA")],
            signal=True,
            strength=1.0,
        )
        strategy._signal_collector.record(evaluation)

        # Export to temp file
        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = Path(tmpdir) / "signals.csv"
            strategy.export_signal_audit(str(export_path))

            assert export_path.exists()

    def test_finalize_signals_on_stop(self) -> None:
        """Test that finalize is called when strategy stops."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()
                self._stopped = False

            def on_stop_base(self) -> None:
                self._stopped = True

        strategy = MockStrategy()

        # Record an evaluation
        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[ComponentResult("trend", 152.0, True, "Close > SMA")],
            signal=True,
            strength=1.0,
        )
        strategy._signal_collector.record(evaluation)

        # Call finalize
        strategy.finalize_signals()

        # Should have been finalized
        assert strategy._signal_collector._finalized is True

    def test_evaluate_with_context(self) -> None:
        """Test evaluating signals with additional context."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()

        # Create mock bar
        mock_bar = Mock()
        mock_bar.bar_type = "AAPL.XNAS-1-DAY-LAST"
        mock_bar.ts_event = 1704067200000000000
        mock_bar.close = Mock()
        mock_bar.close.__float__ = Mock(return_value=152.0)

        # Create mock cache
        mock_cache = Mock()

        # Create mock indicator
        mock_sma = Mock()
        mock_sma.initialized = True
        mock_sma.value = 150.0
        mock_sma.period = 200

        # Create entry signal
        trend = TrendFilterComponent("trend", mock_sma, direction="above")
        strategy.create_entry_signal(
            name="entry",
            components=[trend],
            logic=CombinationLogic.AND,
        )

        # Evaluate with context
        result = strategy.evaluate_entry_signal(
            mock_bar,
            mock_cache,
            position_size=1000,
        )

        assert result.signal is True

    def test_mixin_initialization_options(self) -> None:
        """Test mixin can be initialized with custom options."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation(
                    flush_threshold=5000,
                    near_miss_threshold=0.8,
                )

        strategy = MockStrategy()

        assert strategy._signal_collector._flush_threshold == 5000
        assert strategy._near_miss_threshold == 0.8


class TestSignalValidationMixinBlocking:
    """Tests for blocking analysis in SignalValidationMixin."""

    def test_get_blocking_analysis(self) -> None:
        """Test getting blocking analysis from collected signals."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation()

        strategy = MockStrategy()

        # Record some blocked evaluations
        strategy._signal_collector.record(
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[
                    ComponentResult("trend", 148.0, False, "Close < SMA"),
                    ComponentResult("rsi", 8.0, True, "RSI < 10"),
                ],
                signal=False,
                strength=0.5,
                blocking_component="trend",
            )
        )
        strategy._signal_collector.record(
            SignalEvaluation(
                timestamp=1704153600000000000,
                bar_type="TEST",
                components=[
                    ComponentResult("trend", 152.0, True, "Close > SMA"),
                    ComponentResult("rsi", 15.0, False, "RSI >= 10"),
                ],
                signal=False,
                strength=0.5,
                blocking_component="rsi",
            )
        )

        # Get blocking analysis
        analysis = strategy.get_blocking_analysis()

        assert analysis.total_failed_signals == 2
        assert len(analysis.components) == 2


class TestSignalValidationMixinNearMiss:
    """Tests for near-miss detection in SignalValidationMixin."""

    def test_get_near_misses(self) -> None:
        """Test getting near-miss evaluations."""
        from src.core.signals.integration import SignalValidationMixin

        class MockStrategy(SignalValidationMixin):
            def __init__(self) -> None:
                self.init_signal_validation(near_miss_threshold=0.6)

        strategy = MockStrategy()

        # Record a near-miss evaluation (strength >= threshold but signal=False)
        strategy._signal_collector.record(
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="TEST",
                components=[
                    ComponentResult("c1", 1.0, True, "passed"),
                    ComponentResult("c2", 2.0, True, "passed"),
                    ComponentResult("c3", 3.0, False, "failed"),
                ],
                signal=False,
                strength=2 / 3,  # 0.667 >= 0.6 threshold
                blocking_component="c3",
            )
        )

        # Get near misses
        near_misses = strategy.get_near_misses()

        assert len(near_misses) == 1
        assert near_misses[0].strength >= 0.6
