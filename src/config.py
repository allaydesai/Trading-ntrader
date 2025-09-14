"""Configuration settings for NTrader."""

from decimal import Decimal
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with validation."""

    # Application settings
    app_name: str = "NTrader"
    app_version: str = "0.1.0"
    debug: bool = Field(default=False, description="Enable debug mode")

    # Trading settings
    default_currency: str = Field(
        default="USD", description="Default currency for accounts"
    )
    default_balance: Decimal = Field(
        default=Decimal("1000000"), description="Default starting balance"
    )

    # Backtest settings
    fast_ema_period: int = Field(
        default=10, description="Fast EMA period for strategies"
    )
    slow_ema_period: int = Field(
        default=20, description="Slow EMA period for strategies"
    )
    trade_size: Decimal = Field(
        default=Decimal("1000000"), description="Default trade size"
    )

    # Data settings
    data_directory: Path = Field(
        default=Path("data"), description="Directory for data files"
    )
    mock_data_bars: int = Field(
        default=1000, description="Number of mock data bars to generate"
    )

    # Logging settings
    log_level: str = Field(default="INFO", description="Logging level")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
