"""Pydantic models for signal validation.

This module contains:
- CombinationLogic and ComponentType enums
- Request/Response models for API endpoints
- Configuration models for signal components
"""

from enum import Enum

from pydantic import BaseModel, Field


class CombinationLogic(str, Enum):
    """How to combine multiple signal conditions."""

    AND = "and"  # All conditions must pass
    OR = "or"  # At least one condition must pass


class ComponentType(str, Enum):
    """Built-in signal component types."""

    TREND_FILTER = "trend_filter"
    RSI_THRESHOLD = "rsi_threshold"
    PRICE_BREAKOUT = "price_breakout"
    VOLUME_CONFIRM = "volume_confirm"
    FIBONACCI_LEVEL = "fibonacci_level"
    TIME_STOP = "time_stop"
    CUSTOM = "custom"


class ComponentResultResponse(BaseModel):
    """API response for a single component evaluation."""

    name: str
    value: float
    triggered: bool
    reason: str

    model_config = {"from_attributes": True}


class SignalEvaluationResponse(BaseModel):
    """API response for a signal evaluation."""

    id: int | None = None
    timestamp: int  # Nanoseconds (Nautilus format)
    bar_type: str
    components: list[ComponentResultResponse]
    signal: bool
    strength: float = Field(ge=0.0, le=1.0)
    blocking_component: str | None = None
    signal_type: str | None = None  # "entry" or "exit"
    order_id: str | None = None
    trade_id: str | None = None

    model_config = {"from_attributes": True}


class SignalStatisticsResponse(BaseModel):
    """API response for post-backtest signal statistics."""

    total_evaluations: int = Field(ge=0)
    total_triggered: int = Field(ge=0)
    signal_rate: float = Field(ge=0.0, le=1.0)
    trigger_rates: dict[str, float]
    blocking_rates: dict[str, float]
    near_miss_count: int = Field(ge=0)
    near_miss_threshold: float = Field(ge=0.0, le=1.0)
    primary_blocker: str | None = None

    model_config = {"from_attributes": True}


class BlockingComponentStats(BaseModel):
    """Statistics for a single blocking component."""

    component_name: str
    block_count: int = Field(ge=0)
    block_rate: float = Field(ge=0.0, le=1.0)
    avg_strength_when_blocking: float = Field(ge=0.0, le=1.0)

    model_config = {"from_attributes": True}


class BlockingAnalysisResponse(BaseModel):
    """API response for blocking condition analysis."""

    total_failed_signals: int = Field(ge=0)
    components: list[BlockingComponentStats]
    primary_blocker: str | None = None

    model_config = {"from_attributes": True}


class ComponentConfig(BaseModel):
    """Configuration for a single signal component.

    Used for config-driven signal definition via YAML or Pydantic.

    Attributes:
        name: Unique identifier for this component
        component_type: Type of component (TREND_FILTER, RSI_THRESHOLD, etc.)
        parameters: Type-specific configuration parameters
    """

    name: str = Field(..., min_length=1)
    component_type: ComponentType
    parameters: dict[str, float | int | str | bool] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class CompositeSignalConfig(BaseModel):
    """Configuration for a composite signal with multiple components.

    Used for config-driven signal definition via YAML or Pydantic.

    Attributes:
        name: Unique identifier for this composite signal
        logic: How to combine components (AND = all must pass, OR = any must pass)
        components: List of component configurations
        near_miss_threshold: Threshold for near-miss detection (default 0.75)
    """

    name: str = Field(..., min_length=1)
    logic: CombinationLogic
    components: list[ComponentConfig] = Field(..., min_length=1)
    near_miss_threshold: float = Field(default=0.75, ge=0.0, le=1.0)

    model_config = {"from_attributes": True}
