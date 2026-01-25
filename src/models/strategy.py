"""Trading strategy models."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class StrategyStatus(str, Enum):
    """Status of a trading strategy."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SMAParameters(BaseModel):
    """
    Parameters specific to SMA crossover strategy.

    Matches src.core.strategies.sma_crossover.SMAConfig.
    """

    fast_period: int = Field(default=10, ge=1, le=200, description="Fast SMA period")
    slow_period: int = Field(default=20, ge=1, le=200, description="Slow SMA period")
    portfolio_value: Decimal = Field(
        default=Decimal("1000000"), gt=0, description="Starting portfolio value in USD"
    )
    position_size_pct: Decimal = Field(
        default=Decimal("10.0"),
        ge=0.1,
        le=100.0,
        description="Position size as percentage of portfolio",
    )

    # Mapping from model fields to global Settings attributes
    _settings_map = {
        "fast_period": "fast_ema_period",
        "slow_period": "slow_ema_period",
        "portfolio_value": "portfolio_value",
        "position_size_pct": "position_size_pct",
    }

    @field_validator("fast_period", "slow_period")
    @classmethod
    def validate_periods(cls, v: int) -> int:
        """Validate SMA periods are positive."""
        if v <= 0:
            raise ValueError("SMA periods must be positive")
        return v

    @field_validator("slow_period")
    @classmethod
    def validate_slow_greater_than_fast(cls, v: int, info) -> int:
        """Validate slow period is greater than fast period."""
        if "fast_period" in info.data and v <= info.data["fast_period"]:
            raise ValueError("Slow period must be greater than fast period")
        return v


class MomentumParameters(BaseModel):
    """
    Parameters specific to momentum strategy.

    Matches src.core.strategies.sma_momentum.SMAMomentumConfig.
    """

    trade_size: Decimal = Field(default=Decimal("1000000"), gt=0, description="Size of each trade")
    order_id_tag: str = Field(default="002", description="Tag for order IDs")
    fast_period: int = Field(default=20, ge=1, description="Fast SMA period")
    slow_period: int = Field(default=50, ge=1, description="Slow SMA period")
    warmup_days: int = Field(default=1, ge=0, description="Days of data to load for warmup")
    allow_short: bool = Field(default=False, description="Allow short selling")

    # Mapping from model fields to global Settings attributes
    _settings_map = {
        "trade_size": "trade_size",
        "fast_period": "fast_ema_period",  # Reuse generic settings if applicable
        "slow_period": "slow_ema_period",
    }

    @field_validator("slow_period")
    @classmethod
    def validate_slow_greater_than_fast(cls, v: int, info) -> int:
        """Validate slow period is greater than fast period."""
        if "fast_period" in info.data and v <= info.data["fast_period"]:
            raise ValueError("Slow period must be greater than fast period")
        return v


class TradingStrategy(BaseModel):
    """Trading strategy entity with configuration and metadata."""

    id: UUID = Field(default_factory=uuid4, description="Unique identifier")
    name: str = Field(..., min_length=1, max_length=100, description="Strategy name")
    strategy_type: str = Field(..., description="Type of strategy (e.g., 'sma_crossover')")
    parameters: Dict[str, Any] = Field(..., description="Strategy-specific parameters")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(
        default_factory=datetime.now, description="Last modification timestamp"
    )
    is_active: bool = Field(default=True, description="Whether strategy can be used")
    status: StrategyStatus = Field(default=StrategyStatus.DRAFT, description="Strategy status")

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, v: Dict[str, Any], info) -> Dict[str, Any]:
        """Validate parameters match strategy type using registry lookup."""
        if not v:
            raise ValueError("At least one parameter required")

        strategy_type = info.data.get("strategy_type")
        if not strategy_type:
            return v

        # Dynamic validation using StrategyRegistry
        # Import here to avoid circular imports
        from src.core.strategy_registry import StrategyRegistry

        try:
            StrategyRegistry.discover()  # Ensure strategies are loaded
            definition = StrategyRegistry.get(strategy_type)
            if definition.param_model:
                try:
                    definition.param_model.model_validate(v)
                except Exception as e:
                    strategy_name = strategy_type.replace("_", " ").title()
                    raise ValueError(f"Invalid {strategy_name} parameters: {e}")
        except KeyError:
            # Unknown strategy type - skip validation (allows external strategies)
            pass

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
