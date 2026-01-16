"""Unit tests for Pydantic signal models.

Tests cover:
- CombinationLogic and ComponentType enums
- Request/Response model validation
- Model serialization and from_attributes
"""

import pytest
from pydantic import ValidationError

from src.models.signal import (
    BlockingAnalysisResponse,
    BlockingComponentStats,
    CombinationLogic,
    ComponentResultResponse,
    ComponentType,
    SignalEvaluationResponse,
    SignalStatisticsResponse,
)


class TestEnums:
    """Tests for signal enums."""

    def test_combination_logic_values(self) -> None:
        """Test CombinationLogic enum values."""
        assert CombinationLogic.AND.value == "and"
        assert CombinationLogic.OR.value == "or"

    def test_component_type_values(self) -> None:
        """Test ComponentType enum values."""
        assert ComponentType.TREND_FILTER.value == "trend_filter"
        assert ComponentType.RSI_THRESHOLD.value == "rsi_threshold"
        assert ComponentType.PRICE_BREAKOUT.value == "price_breakout"
        assert ComponentType.VOLUME_CONFIRM.value == "volume_confirm"
        assert ComponentType.FIBONACCI_LEVEL.value == "fibonacci_level"
        assert ComponentType.TIME_STOP.value == "time_stop"
        assert ComponentType.CUSTOM.value == "custom"


class TestComponentResultResponse:
    """Tests for ComponentResultResponse model."""

    def test_create_valid_response(self) -> None:
        """Test creating a valid ComponentResultResponse."""
        response = ComponentResultResponse(
            name="trend_filter",
            value=152.30,
            triggered=True,
            reason="Close (152.30) > SMA(200) (148.50)",
        )

        assert response.name == "trend_filter"
        assert response.value == 152.30
        assert response.triggered is True
        assert "152.30" in response.reason

    def test_serialization_to_dict(self) -> None:
        """Test model serialization to dictionary."""
        response = ComponentResultResponse(
            name="rsi_oversold",
            value=8.5,
            triggered=True,
            reason="RSI < 10",
        )

        data = response.model_dump()

        assert data["name"] == "rsi_oversold"
        assert data["value"] == 8.5
        assert data["triggered"] is True
        assert data["reason"] == "RSI < 10"


class TestSignalEvaluationResponse:
    """Tests for SignalEvaluationResponse model."""

    @pytest.fixture
    def sample_components(self) -> list[ComponentResultResponse]:
        """Provide sample component responses."""
        return [
            ComponentResultResponse(
                name="trend_filter", value=152.30, triggered=True, reason="Close > SMA"
            ),
            ComponentResultResponse(
                name="rsi_oversold", value=8.5, triggered=True, reason="RSI < 10"
            ),
        ]

    def test_create_valid_response(self, sample_components: list[ComponentResultResponse]) -> None:
        """Test creating a valid SignalEvaluationResponse."""
        response = SignalEvaluationResponse(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=sample_components,
            signal=True,
            strength=1.0,
        )

        assert response.timestamp == 1704067200000000000
        assert response.bar_type == "AAPL.XNAS-1-DAY-LAST"
        assert len(response.components) == 2
        assert response.signal is True
        assert response.strength == 1.0
        assert response.blocking_component is None

    def test_strength_validation_min(
        self, sample_components: list[ComponentResultResponse]
    ) -> None:
        """Test strength minimum validation (0.0)."""
        with pytest.raises(ValidationError):
            SignalEvaluationResponse(
                timestamp=1704067200000000000,
                bar_type="AAPL.XNAS-1-DAY-LAST",
                components=sample_components,
                signal=False,
                strength=-0.1,  # Invalid: below 0.0
            )

    def test_strength_validation_max(
        self, sample_components: list[ComponentResultResponse]
    ) -> None:
        """Test strength maximum validation (1.0)."""
        with pytest.raises(ValidationError):
            SignalEvaluationResponse(
                timestamp=1704067200000000000,
                bar_type="AAPL.XNAS-1-DAY-LAST",
                components=sample_components,
                signal=True,
                strength=1.5,  # Invalid: above 1.0
            )

    def test_optional_fields(self, sample_components: list[ComponentResultResponse]) -> None:
        """Test optional signal_type, order_id, trade_id fields."""
        response = SignalEvaluationResponse(
            timestamp=1704067200000000000,
            bar_type="AAPL.XNAS-1-DAY-LAST",
            components=sample_components,
            signal=True,
            strength=1.0,
            signal_type="entry",
            order_id="O-123",
            trade_id="T-456",
        )

        assert response.signal_type == "entry"
        assert response.order_id == "O-123"
        assert response.trade_id == "T-456"


class TestSignalStatisticsResponse:
    """Tests for SignalStatisticsResponse model."""

    def test_create_valid_response(self) -> None:
        """Test creating a valid SignalStatisticsResponse."""
        response = SignalStatisticsResponse(
            total_evaluations=1000,
            total_triggered=45,
            signal_rate=0.045,
            trigger_rates={
                "trend_filter": 0.72,
                "rsi_threshold": 0.08,
            },
            blocking_rates={
                "trend_filter": 0.15,
                "rsi_threshold": 0.65,
            },
            near_miss_count=23,
            near_miss_threshold=0.75,
            primary_blocker="rsi_threshold",
        )

        assert response.total_evaluations == 1000
        assert response.total_triggered == 45
        assert response.signal_rate == 0.045
        assert response.trigger_rates["trend_filter"] == 0.72
        assert response.blocking_rates["rsi_threshold"] == 0.65
        assert response.near_miss_count == 23
        assert response.near_miss_threshold == 0.75
        assert response.primary_blocker == "rsi_threshold"

    def test_validation_negative_counts(self) -> None:
        """Test that negative counts raise validation error."""
        with pytest.raises(ValidationError):
            SignalStatisticsResponse(
                total_evaluations=-1,  # Invalid
                total_triggered=0,
                signal_rate=0.0,
                trigger_rates={},
                blocking_rates={},
                near_miss_count=0,
                near_miss_threshold=0.75,
            )

    def test_validation_signal_rate_range(self) -> None:
        """Test signal_rate must be between 0.0 and 1.0."""
        with pytest.raises(ValidationError):
            SignalStatisticsResponse(
                total_evaluations=100,
                total_triggered=50,
                signal_rate=1.5,  # Invalid: above 1.0
                trigger_rates={},
                blocking_rates={},
                near_miss_count=0,
                near_miss_threshold=0.75,
            )


class TestBlockingAnalysisResponse:
    """Tests for BlockingAnalysisResponse model."""

    def test_create_valid_response(self) -> None:
        """Test creating a valid BlockingAnalysisResponse."""
        components = [
            BlockingComponentStats(
                component_name="rsi_threshold",
                block_count=65,
                block_rate=0.65,
                avg_strength_when_blocking=0.6,
            ),
            BlockingComponentStats(
                component_name="trend_filter",
                block_count=35,
                block_rate=0.35,
                avg_strength_when_blocking=0.4,
            ),
        ]

        response = BlockingAnalysisResponse(
            total_failed_signals=100,
            components=components,
            primary_blocker="rsi_threshold",
        )

        assert response.total_failed_signals == 100
        assert len(response.components) == 2
        assert response.primary_blocker == "rsi_threshold"

    def test_blocking_component_stats_validation(self) -> None:
        """Test BlockingComponentStats field validation."""
        with pytest.raises(ValidationError):
            BlockingComponentStats(
                component_name="test",
                block_count=-1,  # Invalid: negative
                block_rate=0.5,
                avg_strength_when_blocking=0.5,
            )

        with pytest.raises(ValidationError):
            BlockingComponentStats(
                component_name="test",
                block_count=10,
                block_rate=1.5,  # Invalid: above 1.0
                avg_strength_when_blocking=0.5,
            )


class TestComponentConfig:
    """Tests for ComponentConfig model."""

    def test_create_trend_filter_config(self) -> None:
        """Test creating a TrendFilterComponent config."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="sma_trend",
            component_type=ComponentType.TREND_FILTER,
            parameters={"direction": "above"},
        )

        assert config.name == "sma_trend"
        assert config.component_type == ComponentType.TREND_FILTER
        assert config.parameters["direction"] == "above"

    def test_create_rsi_threshold_config(self) -> None:
        """Test creating an RSIThresholdComponent config."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="rsi_oversold",
            component_type=ComponentType.RSI_THRESHOLD,
            parameters={"threshold": 30.0, "direction": "below"},
        )

        assert config.name == "rsi_oversold"
        assert config.component_type == ComponentType.RSI_THRESHOLD
        assert config.parameters["threshold"] == 30.0
        assert config.parameters["direction"] == "below"

    def test_create_volume_confirm_config(self) -> None:
        """Test creating a VolumeConfirmComponent config."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="volume_surge",
            component_type=ComponentType.VOLUME_CONFIRM,
            parameters={"multiplier": 1.5},
        )

        assert config.name == "volume_surge"
        assert config.component_type == ComponentType.VOLUME_CONFIRM
        assert config.parameters["multiplier"] == 1.5

    def test_create_fibonacci_level_config(self) -> None:
        """Test creating a FibonacciLevelComponent config."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="fib_618",
            component_type=ComponentType.FIBONACCI_LEVEL,
            parameters={"level": 0.618, "tolerance": 0.02},
        )

        assert config.name == "fib_618"
        assert config.component_type == ComponentType.FIBONACCI_LEVEL
        assert config.parameters["level"] == 0.618
        assert config.parameters["tolerance"] == 0.02

    def test_create_price_breakout_config(self) -> None:
        """Test creating a PriceBreakoutComponent config."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="breakout_above",
            component_type=ComponentType.PRICE_BREAKOUT,
            parameters={"comparison": "above"},
        )

        assert config.name == "breakout_above"
        assert config.component_type == ComponentType.PRICE_BREAKOUT
        assert config.parameters["comparison"] == "above"

    def test_create_time_stop_config(self) -> None:
        """Test creating a TimeStopComponent config."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="time_exit",
            component_type=ComponentType.TIME_STOP,
            parameters={"max_bars": 5},
        )

        assert config.name == "time_exit"
        assert config.component_type == ComponentType.TIME_STOP
        assert config.parameters["max_bars"] == 5

    def test_custom_component_config(self) -> None:
        """Test creating a custom component config."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="my_custom",
            component_type=ComponentType.CUSTOM,
            parameters={"custom_param": "value"},
        )

        assert config.component_type == ComponentType.CUSTOM
        assert config.parameters["custom_param"] == "value"

    def test_empty_parameters_allowed(self) -> None:
        """Test that empty parameters dict is allowed."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="simple",
            component_type=ComponentType.CUSTOM,
            parameters={},
        )

        assert config.parameters == {}

    def test_serialization_to_dict(self) -> None:
        """Test ComponentConfig serialization."""
        from src.models.signal import ComponentConfig

        config = ComponentConfig(
            name="rsi_oversold",
            component_type=ComponentType.RSI_THRESHOLD,
            parameters={"threshold": 30.0, "direction": "below"},
        )

        data = config.model_dump()

        assert data["name"] == "rsi_oversold"
        assert data["component_type"] == "rsi_threshold"
        assert data["parameters"]["threshold"] == 30.0


class TestCompositeSignalConfig:
    """Tests for CompositeSignalConfig model."""

    def test_create_and_logic_config(self) -> None:
        """Test creating a composite signal with AND logic."""
        from src.models.signal import ComponentConfig, CompositeSignalConfig

        components = [
            ComponentConfig(
                name="trend",
                component_type=ComponentType.TREND_FILTER,
                parameters={"direction": "above"},
            ),
            ComponentConfig(
                name="rsi",
                component_type=ComponentType.RSI_THRESHOLD,
                parameters={"threshold": 30.0, "direction": "below"},
            ),
        ]

        config = CompositeSignalConfig(
            name="entry_signal",
            logic=CombinationLogic.AND,
            components=components,
        )

        assert config.name == "entry_signal"
        assert config.logic == CombinationLogic.AND
        assert len(config.components) == 2
        assert config.components[0].name == "trend"
        assert config.components[1].name == "rsi"

    def test_create_or_logic_config(self) -> None:
        """Test creating a composite signal with OR logic."""
        from src.models.signal import ComponentConfig, CompositeSignalConfig

        components = [
            ComponentConfig(
                name="breakout",
                component_type=ComponentType.PRICE_BREAKOUT,
                parameters={"comparison": "above"},
            ),
            ComponentConfig(
                name="time_stop",
                component_type=ComponentType.TIME_STOP,
                parameters={"max_bars": 5},
            ),
        ]

        config = CompositeSignalConfig(
            name="exit_signal",
            logic=CombinationLogic.OR,
            components=components,
        )

        assert config.name == "exit_signal"
        assert config.logic == CombinationLogic.OR
        assert len(config.components) == 2

    def test_near_miss_threshold_optional(self) -> None:
        """Test that near_miss_threshold is optional with default."""
        from src.models.signal import ComponentConfig, CompositeSignalConfig

        config = CompositeSignalConfig(
            name="test_signal",
            logic=CombinationLogic.AND,
            components=[
                ComponentConfig(
                    name="c1",
                    component_type=ComponentType.CUSTOM,
                    parameters={},
                ),
            ],
        )

        assert config.near_miss_threshold == 0.75  # Default

    def test_near_miss_threshold_custom(self) -> None:
        """Test setting custom near_miss_threshold."""
        from src.models.signal import ComponentConfig, CompositeSignalConfig

        config = CompositeSignalConfig(
            name="test_signal",
            logic=CombinationLogic.AND,
            components=[
                ComponentConfig(
                    name="c1",
                    component_type=ComponentType.CUSTOM,
                    parameters={},
                ),
            ],
            near_miss_threshold=0.6,
        )

        assert config.near_miss_threshold == 0.6

    def test_near_miss_threshold_validation(self) -> None:
        """Test near_miss_threshold must be between 0.0 and 1.0."""
        from src.models.signal import ComponentConfig, CompositeSignalConfig

        with pytest.raises(ValidationError):
            CompositeSignalConfig(
                name="test",
                logic=CombinationLogic.AND,
                components=[
                    ComponentConfig(
                        name="c1",
                        component_type=ComponentType.CUSTOM,
                        parameters={},
                    ),
                ],
                near_miss_threshold=1.5,  # Invalid: above 1.0
            )

        with pytest.raises(ValidationError):
            CompositeSignalConfig(
                name="test",
                logic=CombinationLogic.AND,
                components=[
                    ComponentConfig(
                        name="c1",
                        component_type=ComponentType.CUSTOM,
                        parameters={},
                    ),
                ],
                near_miss_threshold=-0.1,  # Invalid: below 0.0
            )

    def test_minimum_one_component_validation(self) -> None:
        """Test that at least one component is required."""
        from src.models.signal import CompositeSignalConfig

        with pytest.raises(ValidationError):
            CompositeSignalConfig(
                name="empty_signal",
                logic=CombinationLogic.AND,
                components=[],  # Invalid: empty list
            )

    def test_serialization_to_dict(self) -> None:
        """Test CompositeSignalConfig serialization."""
        from src.models.signal import ComponentConfig, CompositeSignalConfig

        config = CompositeSignalConfig(
            name="entry_signal",
            logic=CombinationLogic.AND,
            components=[
                ComponentConfig(
                    name="trend",
                    component_type=ComponentType.TREND_FILTER,
                    parameters={"direction": "above"},
                ),
            ],
            near_miss_threshold=0.8,
        )

        data = config.model_dump()

        assert data["name"] == "entry_signal"
        assert data["logic"] == "and"
        assert len(data["components"]) == 1
        assert data["components"][0]["name"] == "trend"
        assert data["near_miss_threshold"] == 0.8

    def test_four_condition_entry_signal(self) -> None:
        """Test typical 4-condition entry signal from spec example."""
        from src.models.signal import ComponentConfig, CompositeSignalConfig

        components = [
            ComponentConfig(
                name="trend_filter",
                component_type=ComponentType.TREND_FILTER,
                parameters={"direction": "above"},
            ),
            ComponentConfig(
                name="rsi_oversold",
                component_type=ComponentType.RSI_THRESHOLD,
                parameters={"threshold": 10.0, "direction": "below"},
            ),
            ComponentConfig(
                name="volume_confirm",
                component_type=ComponentType.VOLUME_CONFIRM,
                parameters={"multiplier": 1.5},
            ),
            ComponentConfig(
                name="fib_level",
                component_type=ComponentType.FIBONACCI_LEVEL,
                parameters={"level": 0.618, "tolerance": 0.02},
            ),
        ]

        config = CompositeSignalConfig(
            name="mean_reversion_entry",
            logic=CombinationLogic.AND,
            components=components,
        )

        assert len(config.components) == 4
        assert config.logic == CombinationLogic.AND
