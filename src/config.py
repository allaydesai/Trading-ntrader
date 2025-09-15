"""Configuration settings for NTrader."""

from decimal import Decimal
from pathlib import Path
from typing import Optional

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

    # Database settings
    database_url: Optional[str] = Field(
        default="postgresql://ntrader:ntrader@localhost:5432/ntrader_dev",
        description="PostgreSQL database URL",
    )
    database_pool_size: int = Field(
        default=10, description="Database connection pool size"
    )
    database_max_overflow: int = Field(
        default=20, description="Maximum overflow connections"
    )
    database_pool_timeout: int = Field(
        default=30, description="Pool connection timeout in seconds"
    )

    # Logging settings
    log_level: str = Field(default="INFO", description="Logging level")

    @property
    def is_database_available(self) -> bool:
        """Check if database is configured."""
        return bool(self.database_url)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
