"""Unit tests for ComponentResult and SignalEvaluation.

Tests cover:
- ComponentResult immutability and validation
- SignalEvaluation creation and derived properties
- Near-miss detection
- Validation error cases
"""

import pytest

from src.core.signals.evaluation import ComponentResult, SignalEvaluation


class TestComponentResult:
    """Tests for ComponentResult dataclass."""

    def test_create_component_result_with_valid_data(self) -> None:
        """Test creating a valid ComponentResult."""
        result = ComponentResult(
            name="rsi_oversold",
            value=8.5,
            triggered=True,
            reason="RSI(2) = 8.5 < threshold 10",
        )

        assert result.name == "rsi_oversold"
        assert result.value == 8.5
        assert result.triggered is True
        assert result.reason == "RSI(2) = 8.5 < threshold 10"

    def test_component_result_is_frozen(self) -> None:
        """Test that ComponentResult is immutable (frozen dataclass)."""
        result = ComponentResult(
            name="trend_filter",
            value=152.30,
            triggered=True,
            reason="Close > SMA",
        )

        with pytest.raises(AttributeError):
            result.name = "modified"  # type: ignore[misc]

        with pytest.raises(AttributeError):
            result.triggered = False  # type: ignore[misc]

    def test_component_result_with_nan_value(self) -> None:
        """Test ComponentResult with NaN value for insufficient data."""
        result = ComponentResult(
            name="rsi_threshold",
            value=float("nan"),
            triggered=False,
            reason="Insufficient data: RSI requires 14 bars",
        )

        assert result.triggered is False
        assert "Insufficient data" in result.reason

    def test_component_result_empty_name_raises_error(self) -> None:
        """Test that empty name raises ValueError."""
        with pytest.raises(ValueError, match="Component name cannot be empty"):
            ComponentResult(
                name="",
                value=0.0,
                triggered=False,
                reason="Test",
            )

    def test_component_result_equality(self) -> None:
        """Test ComponentResult equality comparison."""
        result1 = ComponentResult("test", 1.0, True, "reason")
        result2 = ComponentResult("test", 1.0, True, "reason")
        result3 = ComponentResult("test", 2.0, True, "reason")

        assert result1 == result2
        assert result1 != result3


class TestSignalEvaluation:
    """Tests for SignalEvaluation dataclass."""

    @pytest.fixture
    def sample_components(self) -> list[ComponentResult]:
        """Provide sample component results for testing."""
        return [
            ComponentResult("trend_filter", 152.30, True, "Close > SMA(200)"),
            ComponentResult("rsi_oversold", 8.5, True, "RSI(2) < 10"),
            ComponentResult("volume_confirm", 1.2, True, "Volume 20% above average"),
            ComponentResult("fib_level", 0.62, False, "Price not near Fib 0.618"),
        ]

    def test_create_signal_evaluation_with_valid_data(
        self, sample_components: list[ComponentResult]
    ) -> None:
        """Test creating a valid SignalEvaluation."""
        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=sample_components,
            signal=False,
            strength=0.75,
            blocking_component="fib_level",
        )

        assert evaluation.timestamp == 1704067200000000000
        assert evaluation.bar_type == "AAPL.XNAS-1-DAY-LAST"
        assert len(evaluation.components) == 4
        assert evaluation.signal is False
        assert evaluation.strength == 0.75
        assert evaluation.blocking_component == "fib_level"

    def test_passed_count_property(self, sample_components: list[ComponentResult]) -> None:
        """Test passed_count returns correct count of triggered components."""
        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=sample_components,
            signal=False,
            strength=0.75,
            blocking_component="fib_level",
        )

        assert evaluation.passed_count == 3  # 3 of 4 components triggered

    def test_total_count_property(self, sample_components: list[ComponentResult]) -> None:
        """Test total_count returns total number of components."""
        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=sample_components,
            signal=False,
            strength=0.75,
            blocking_component="fib_level",
        )

        assert evaluation.total_count == 4

    def test_is_near_miss_default_threshold(self, sample_components: list[ComponentResult]) -> None:
        """Test is_near_miss with default 75% threshold."""
        # Near miss: strength >= 0.75 and signal = False
        near_miss = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=sample_components,
            signal=False,
            strength=0.75,
            blocking_component="fib_level",
        )

        assert near_miss.is_near_miss is True

        # Not a near miss: signal = True
        triggered = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[ComponentResult("c1", 1.0, True, "r1")],
            signal=True,
            strength=1.0,
        )

        assert triggered.is_near_miss is False

    def test_is_near_miss_custom_threshold(self, sample_components: list[ComponentResult]) -> None:
        """Test is_near_miss_with_threshold for custom threshold."""
        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=sample_components[:2],  # 2 components, 1 triggered
            signal=False,
            strength=0.5,
            blocking_component="rsi_oversold",
        )

        # 0.5 is below default 0.75 threshold
        assert evaluation.is_near_miss is False

        # But above 0.4 threshold
        assert evaluation.is_near_miss_with_threshold(0.4) is True

    def test_signal_true_with_all_passed(self) -> None:
        """Test evaluation where all components pass (signal=True)."""
        all_passed = [
            ComponentResult("c1", 1.0, True, "passed"),
            ComponentResult("c2", 2.0, True, "passed"),
        ]

        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=all_passed,
            signal=True,
            strength=1.0,
            blocking_component=None,
        )

        assert evaluation.signal is True
        assert evaluation.strength == 1.0
        assert evaluation.blocking_component is None
        assert evaluation.passed_count == 2

    def test_validation_empty_components_raises_error(self) -> None:
        """Test that empty components list raises ValueError."""
        with pytest.raises(ValueError, match="must have at least one component"):
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="AAPL.XNAS-1-DAY-LAST",
                components=[],
                signal=False,
                strength=0.0,
            )

    def test_validation_negative_timestamp_raises_error(self) -> None:
        """Test that negative timestamp raises ValueError."""
        with pytest.raises(ValueError, match="Timestamp must be positive"):
            SignalEvaluation(
                timestamp=-1,
                bar_type="AAPL.XNAS-1-DAY-LAST",
                components=[ComponentResult("c1", 1.0, True, "r")],
                signal=True,
                strength=1.0,
            )

    def test_validation_invalid_strength_raises_error(self) -> None:
        """Test that strength outside 0.0-1.0 raises ValueError."""
        with pytest.raises(ValueError, match="Strength must be between"):
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="AAPL.XNAS-1-DAY-LAST",
                components=[ComponentResult("c1", 1.0, True, "r")],
                signal=True,
                strength=1.5,
            )

        with pytest.raises(ValueError, match="Strength must be between"):
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="AAPL.XNAS-1-DAY-LAST",
                components=[ComponentResult("c1", 1.0, True, "r")],
                signal=True,
                strength=-0.1,
            )

    def test_validation_blocking_component_when_signal_true_raises_error(self) -> None:
        """Test that blocking_component must be None when signal=True."""
        with pytest.raises(ValueError, match="blocking_component must be None"):
            SignalEvaluation(
                timestamp=1704067200000000000,
                bar_type="AAPL.XNAS-1-DAY-LAST",
                components=[ComponentResult("c1", 1.0, True, "r")],
                signal=True,
                strength=1.0,
                blocking_component="c1",  # Invalid: signal=True but blocker set
            )

    def test_signal_type_and_correlation_fields(self) -> None:
        """Test optional signal_type, order_id, and trade_id fields."""
        evaluation = SignalEvaluation(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=[ComponentResult("c1", 1.0, True, "r")],
            signal=True,
            strength=1.0,
            signal_type="entry",
            order_id="O-123",
            trade_id="T-456",
        )

        assert evaluation.signal_type == "entry"
        assert evaluation.order_id == "O-123"
        assert evaluation.trade_id == "T-456"
