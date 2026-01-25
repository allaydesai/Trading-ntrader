"""Trading strategy models."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class StrategyType(str, Enum):
    """Types of trading strategies available."""

    SMA_CROSSOVER = "sma_crossover"
    SMA_CROSSOVER_LONG_ONLY = "sma_crossover_long_only"
    MEAN_REVERSION = "mean_reversion"
    MOMENTUM = "momentum"
    BOLLINGER_REVERSAL = "bollinger_reversal"
    CONNORS_RSI_MEAN_REV = "connors_rsi_mean_rev"
    APOLO_RSI = "apolo_rsi"


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


class MeanReversionParameters(BaseModel):
    """
    Parameters specific to mean reversion strategy.

    Matches src.core.strategies.rsi_mean_reversion.RSIMeanRevConfig.
    """

    trade_size: Decimal = Field(default=Decimal("1000000"), gt=0, description="Size of each trade")
    order_id_tag: str = Field(default="001", description="Tag for order IDs")
    rsi_period: int = Field(default=2, ge=2, le=100, description="RSI calculation period")
    rsi_buy_threshold: float = Field(default=10.0, ge=0, le=100, description="RSI buy threshold")
    exit_rsi: float = Field(default=50.0, ge=0, le=100, description="RSI exit threshold")
    sma_trend_period: int = Field(default=200, ge=1, description="Trend filter SMA period")
    warmup_days: int = Field(default=400, ge=1, description="Days of data to load for warmup")
    cooldown_bars: int = Field(default=0, ge=0, description="Bars to wait after exit")

    # Mapping from model fields to global Settings attributes
    _settings_map = {
        "trade_size": "trade_size",
    }

    @field_validator("rsi_buy_threshold", "exit_rsi")
    @classmethod
    def validate_thresholds(cls, v: float) -> float:
        """Validate RSI thresholds are in valid range."""
        if not (0 <= v <= 100):
            raise ValueError("RSI thresholds must be between 0 and 100")
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


class BollingerReversalParameters(BaseModel):
    """
    Parameters specific to Bollinger Reversal strategy.

    Matches src.core.strategies.bollinger_reversal.BollingerReversalConfig.
    """

    portfolio_value: Decimal = Field(
        default=Decimal("1000000"), gt=0, description="Starting portfolio value in USD"
    )
    daily_bb_period: int = Field(
        default=20, ge=1, le=100, description="Daily Bollinger Bands period"
    )
    daily_bb_std_dev: float = Field(
        default=2.0, ge=0.1, le=10.0, description="Daily Bollinger Bands standard deviation"
    )
    weekly_ma_period: int = Field(
        default=20, ge=1, le=200, description="Weekly Moving Average period"
    )
    weekly_ma_tolerance_pct: float = Field(
        default=0.05, ge=0.0, le=1.0, description="Tolerance for price near Weekly MA (0.05 = 5%)"
    )
    max_risk_pct: Decimal = Field(
        default=Decimal("1.0"), ge=0.1, le=10.0, description="% of equity risked per trade"
    )
    stop_loss_atr_mult: Decimal = Field(
        default=Decimal("2.0"), ge=0.5, le=10.0, description="ATR multiplier for stop loss"
    )
    atr_period: int = Field(default=14, ge=1, le=100, description="ATR period for stop loss")

    # Mapping from model fields to global Settings attributes
    _settings_map = {
        "portfolio_value": "portfolio_value",
    }


class ConnorsRSIParameters(BaseModel):
    """
    Parameters specific to Larry Connors RSI Mean Reversion strategy.

    Matches src.core.strategies.larry_connors_RSI_mean_rev.ConnorsRSIMeanRevConfig.
    """

    trade_size: Decimal = Field(
        default=Decimal("1000.0"), gt=0, description="Size of each trade in shares"
    )
    rsi_period: int = Field(default=2, ge=2, description="RSI period")
    buy_threshold: float = Field(default=10.0, ge=0, le=100, description="Buy threshold (RSI < X)")
    sma_trend_period: int = Field(default=200, ge=1, description="Trend filter SMA period")
    max_holding_days: int = Field(default=5, ge=1, description="Max holding days (time stop)")

    # Mapping from model fields to global Settings attributes
    _settings_map = {
        "trade_size": "trade_size",
    }

    @field_validator("buy_threshold")
    @classmethod
    def validate_buy_threshold(cls, v: float) -> float:
        """Validate buy threshold is in valid range."""
        if not (0 <= v <= 100):
            raise ValueError("Buy threshold must be between 0 and 100")
        return v


class ApoloRSIParameters(BaseModel):
    """
    Parameters for Apolo RSI Mean Reversion strategy.

    Simple 2-period RSI strategy:
    - Buy: RSI(2) < buy_threshold (oversold after sharp decline)
    - Sell: RSI(2) > sell_threshold (mean reversion back up)
    - Long only

    Note: Nautilus Trader's RSI indicator returns values in 0-1 range (not 0-100).
    Thresholds should be specified as decimals (e.g., 0.10 for RSI < 10).

    Matches src.core.strategies.apolo_rsi.ApoloRSIConfig.
    """

    trade_size: Decimal = Field(
        default=Decimal("100.0"), gt=0, description="Size of each trade in shares"
    )
    order_id_tag: str = Field(default="APOLO", description="Unique tag for order identification")
    rsi_period: int = Field(default=2, ge=2, description="RSI calculation period")
    buy_threshold: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Buy when RSI < threshold (0.10 = RSI < 10)",
    )
    sell_threshold: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Sell when RSI > threshold (0.50 = RSI > 50)",
    )

    # Mapping from model fields to global Settings attributes
    _settings_map = {
        "trade_size": "trade_size",
    }

    @field_validator("buy_threshold", "sell_threshold")
    @classmethod
    def validate_thresholds(cls, v: float) -> float:
        """Validate thresholds are in valid range (0-1 for Nautilus RSI)."""
        if not (0.0 <= v <= 1.0):
            raise ValueError("Thresholds must be between 0.0 and 1.0 (Nautilus RSI range)")
        return v

    @field_validator("sell_threshold")
    @classmethod
    def validate_sell_greater_than_buy(cls, v: float, info) -> float:
        """Validate sell threshold is greater than buy threshold."""
        if "buy_threshold" in info.data and v <= info.data["buy_threshold"]:
            raise ValueError("Buy threshold must be less than sell threshold")
        return v


class TradingStrategy(BaseModel):
    """Trading strategy entity with configuration and metadata."""

    id: UUID = Field(default_factory=uuid4, description="Unique identifier")
    name: str = Field(..., min_length=1, max_length=100, description="Strategy name")
    strategy_type: StrategyType = Field(..., description="Type of strategy")
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
        """Validate parameters match strategy type."""
        if not v:
            raise ValueError("At least one parameter required")

        strategy_type = info.data.get("strategy_type")

        if strategy_type == StrategyType.SMA_CROSSOVER:
            # Validate SMA parameters
            try:
                SMAParameters.model_validate(v)
            except Exception as e:
                raise ValueError(f"Invalid SMA parameters: {e}")

        elif strategy_type == StrategyType.SMA_CROSSOVER_LONG_ONLY:
            # Validate SMA Crossover Long-Only parameters (uses same params as SMA)
            try:
                SMAParameters.model_validate(v)
            except Exception as e:
                raise ValueError(f"Invalid SMA Crossover Long-Only parameters: {e}")

        elif strategy_type == StrategyType.MEAN_REVERSION:
            # Validate Mean Reversion parameters
            try:
                MeanReversionParameters.model_validate(v)
            except Exception as e:
                raise ValueError(f"Invalid Mean Reversion parameters: {e}")

        elif strategy_type == StrategyType.MOMENTUM:
            # Validate Momentum parameters
            try:
                MomentumParameters.model_validate(v)
            except Exception as e:
                raise ValueError(f"Invalid Momentum parameters: {e}")

        elif strategy_type == StrategyType.BOLLINGER_REVERSAL:
            # Validate Bollinger Reversal parameters
            try:
                BollingerReversalParameters.model_validate(v)
            except Exception as e:
                raise ValueError(f"Invalid Bollinger Reversal parameters: {e}")

        elif strategy_type == StrategyType.CONNORS_RSI_MEAN_REV:
            # Validate Connors RSI Mean Reversion parameters
            try:
                ConnorsRSIParameters.model_validate(v)
            except Exception as e:
                raise ValueError(f"Invalid Connors RSI Mean Reversion parameters: {e}")

        elif strategy_type == StrategyType.APOLO_RSI:
            # Validate Apolo RSI parameters
            try:
                ApoloRSIParameters.model_validate(v)
            except Exception as e:
                raise ValueError(f"Invalid Apolo RSI parameters: {e}")

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
