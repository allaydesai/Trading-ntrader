"""Configuration settings for NTrader."""

from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

from ibapi.common import MarketDataTypeEnum  # type: ignore
from pydantic import Field
from pydantic_settings import BaseSettings


class IBKRSettings(BaseSettings):
    """Interactive Brokers configuration settings."""

    # Connection settings
    ibkr_host: str = Field(
        default="127.0.0.1", description="IB Gateway/TWS host address"
    )
    ibkr_port: int = Field(default=7497, description="Connection port (7497=TWS paper)")
    ibkr_client_id: int = Field(default=1, description="Unique client identifier")

    # Gateway mode
    ibkr_trading_mode: Literal["paper", "live"] = Field(
        default="paper", description="Trading mode (paper or live)"
    )
    ibkr_read_only: bool = Field(
        default=True, description="True = data only, False = allow trading"
    )

    # Credentials (from environment)
    tws_username: str = Field(default="", description="IBKR username")
    tws_password: str = Field(default="", description="IBKR password")
    tws_account: str = Field(default="", description="IBKR account ID")

    # Timeouts
    ibkr_connection_timeout: int = Field(
        default=300, description="Connection timeout in seconds (5 minutes)"
    )
    ibkr_request_timeout: int = Field(
        default=60, description="Request timeout in seconds (1 minute)"
    )

    # Rate limiting
    ibkr_rate_limit: int = Field(
        default=45, description="Requests per second (90% of 50 limit)"
    )

    # Data settings
    ibkr_use_rth: bool = Field(default=True, description="Regular Trading Hours only")
    ibkr_market_data_type: str = Field(
        default="DELAYED_FROZEN", description="Market data type for paper trading"
    )

    def get_market_data_type_enum(self) -> MarketDataTypeEnum:
        """
        Convert market data type string to MarketDataTypeEnum.

        Returns:
            MarketDataTypeEnum corresponding to the configured string

        Note:
            Valid values: REALTIME, FROZEN, DELAYED, DELAYED_FROZEN
            Defaults to DELAYED_FROZEN for paper trading
        """
        market_data_map = {
            "REALTIME": MarketDataTypeEnum.REALTIME,
            "FROZEN": MarketDataTypeEnum.FROZEN,
            "DELAYED": MarketDataTypeEnum.DELAYED,
            "DELAYED_FROZEN": MarketDataTypeEnum.DELAYED_FROZEN,
        }
        return market_data_map.get(
            self.ibkr_market_data_type.upper(), MarketDataTypeEnum.DELAYED_FROZEN
        )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


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
        default="postgresql://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader",
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

    # IBKR settings
    ibkr: IBKRSettings = Field(
        default_factory=IBKRSettings, description="Interactive Brokers settings"
    )

    @property
    def is_database_available(self) -> bool:
        """Check if database is configured."""
        return bool(self.database_url)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


def get_settings() -> Settings:
    """Get application settings singleton."""
    return Settings()
