"""Trading strategy models."""

from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class StrategyType(str, Enum):
    """Types of trading strategies available."""
    SMA_CROSSOVER = "sma_crossover"
    MEAN_REVERSION = "mean_reversion"
    MOMENTUM = "momentum"


class StrategyStatus(str, Enum):
    """Status of a trading strategy."""
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SMAParameters(BaseModel):
    """Parameters specific to SMA crossover strategy."""
    fast_period: int = Field(
        default=10,
        ge=1,
        le=200,
        description="Fast SMA period"
    )
    slow_period: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Slow SMA period"
    )
    trade_size: Decimal = Field(
        default=Decimal("1000000"),
        gt=0,
        description="Size of each trade"
    )

    @field_validator('fast_period', 'slow_period')
    @classmethod
    def validate_periods(cls, v: int) -> int:
        """Validate SMA periods are positive."""
        if v <= 0:
            raise ValueError("SMA periods must be positive")
        return v

    @field_validator('slow_period')
    @classmethod
    def validate_slow_greater_than_fast(cls, v: int, info) -> int:
        """Validate slow period is greater than fast period."""
        if 'fast_period' in info.data and v <= info.data['fast_period']:
            raise ValueError("Slow period must be greater than fast period")
        return v


class TradingStrategy(BaseModel):
    """Trading strategy entity with configuration and metadata."""

    id: UUID = Field(default_factory=uuid4, description="Unique identifier")
    name: str = Field(..., min_length=1, max_length=100, description="Strategy name")
    strategy_type: StrategyType = Field(..., description="Type of strategy")
    parameters: Dict[str, Any] = Field(..., description="Strategy-specific parameters")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last modification timestamp")
    is_active: bool = Field(default=True, description="Whether strategy can be used")
    status: StrategyStatus = Field(default=StrategyStatus.DRAFT, description="Strategy status")

    @field_validator('parameters')
    @classmethod
    def validate_parameters(cls, v: Dict[str, Any], info) -> Dict[str, Any]:
        """Validate parameters match strategy type."""
        if not v:
            raise ValueError("At least one parameter required")

        strategy_type = info.data.get('strategy_type')

        if strategy_type == StrategyType.SMA_CROSSOVER:
            # Validate SMA parameters
            try:
                SMAParameters.model_validate(v)
            except Exception as e:
                raise ValueError(f"Invalid SMA parameters: {e}")

        return v

    def update_timestamp(self) -> None:
        """Update the modification timestamp."""
        self.updated_at = datetime.now()

    def activate(self) -> None:
        """Activate the strategy."""
        if self.status == StrategyStatus.ARCHIVED:
            raise ValueError("Cannot activate archived strategy")
        self.status = StrategyStatus.ACTIVE
        self.is_active = True
        self.update_timestamp()

    def archive(self) -> None:
        """Archive the strategy (read-only)."""
        self.status = StrategyStatus.ARCHIVED
        self.is_active = False
        self.update_timestamp()

    model_config = {
        "json_encoders": {
            datetime: lambda v: v.isoformat(),
            Decimal: str,
            UUID: str,
        },
        "use_enum_values": True,
    }