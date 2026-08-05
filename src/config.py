"""Configuration settings for NTrader."""

import base64
import os
from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

from ibapi.common import MarketDataTypeEnum  # type: ignore
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

# How far above its configured base the historical client may rotate when a connect
# attempt times out. Source of truth is IBKRHistoricalClient.connect(max_id_rotations=5)
# in src/services/ibkr_client.py; it cannot be imported here (that module imports this
# one), so the two must be kept in step by hand.
HISTORICAL_CLIENT_ID_ROTATION_SPAN = 5


class IBKRSettings(BaseSettings):
    """Interactive Brokers configuration settings."""

    # Connection settings
    ibkr_host: str = Field(default="127.0.0.1", description="IB Gateway/TWS host address")
    ibkr_port: int = Field(default=7497, description="Connection port (7497=TWS paper)")
    ibkr_client_id: int = Field(
        default=1,
        ge=1,
        description=(
            "Client ID for the historical data client (catalog fetch). Reservation: "
            "historical = ibkr_client_id, which rotates through ibkr_client_id .. "
            "ibkr_client_id + 5 on connect retries, so its effective range is 1-6 at the "
            "default. That whole range must stay clear of ibkr_live_client_id and "
            "ibkr_live_client_id + 1 — at the default live ID, keep this at 4 or below."
        ),
    )
    ibkr_live_client_id: int = Field(
        default=10,
        ge=1,
        description=(
            "Client ID for the live trading session's IBKR data + execution clients. "
            "Reservation: historical fetch = ibkr_client_id (which rotates up to "
            "ibkr_client_id + 5 on connect retries), live session = ibkr_live_client_id, "
            "on-demand reconcile = ibkr_live_client_id + 1. Both reserved IDs must sit "
            "outside the historical client's rotation range."
        ),
    )

    # Gateway mode
    ibkr_trading_mode: Literal["paper", "live"] = Field(
        default="paper", description="Trading mode (paper or live)"
    )
    ibkr_read_only: bool = Field(
        default=True, description="True = data only, False = allow trading"
    )

    # Credentials (from environment)
    tws_username: str = Field(default="", description="IBKR username")
    tws_password: str = Field(default="", repr=False, description="IBKR password")
    tws_account: str = Field(default="", repr=False, description="IBKR account ID")
    ntrader_real_money_account: str = Field(
        default="",
        repr=False,
        description=(
            "Exact IBKR account ID the operator has explicitly authorized for real-money "
            "trading. Empty = paper only. Must be accompanied by the CLI --real-money flag; "
            "neither declaration alone permits a real-money connection."
        ),
    )

    # Timeouts
    ibkr_connection_timeout: int = Field(
        default=300, description="Connection timeout in seconds (5 minutes)"
    )
    ibkr_request_timeout: int = Field(
        default=60, description="Request timeout in seconds (1 minute)"
    )

    # Rate limiting
    ibkr_rate_limit: int = Field(default=45, description="Requests per second (90% of 50 limit)")

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

    @model_validator(mode="after")
    def validate_client_ids_distinct(self) -> "IBKRSettings":
        """The historical client's rotation range must not touch either reserved live ID.

        A second connection presenting a client ID already in use is refused with
        error 326 when the incumbent is still live on it, and takes the ID over when
        the incumbent's socket is stale (a SIGKILL'd run leaves the Gateway holding
        an ID for tens of seconds). Either outcome breaks a running session or an
        in-flight import, so the two allocations must not overlap at all (FR5).

        Equality is not sufficient: IBKRHistoricalClient.connect() rotates
        ibkr_client_id .. ibkr_client_id + 5 on connect timeouts, and the live
        session reserves both ibkr_live_client_id and ibkr_live_client_id + 1 (for
        on-demand reconcile). This rejects any intersection of the two.
        """
        historical_last = self.ibkr_client_id + HISTORICAL_CLIENT_ID_ROTATION_SPAN
        reconcile_id = self.ibkr_live_client_id + 1

        if self.ibkr_client_id <= reconcile_id and self.ibkr_live_client_id <= historical_last:
            safe_ceiling = self.ibkr_live_client_id - HISTORICAL_CLIENT_ID_ROTATION_SPAN - 1
            remedy = (
                f"set ibkr_client_id to {safe_ceiling} or below"
                if safe_ceiling >= 1
                else f"set ibkr_live_client_id to {historical_last + 1} or above"
            )
            raise ValueError(
                f"ibkr_client_id and ibkr_live_client_id overlap: historical client "
                f"{self.ibkr_client_id} rotates {self.ibkr_client_id}-{historical_last} on "
                f"connect retries, which reaches the live session's reserved IDs "
                f"{self.ibkr_live_client_id} (live) and {reconcile_id} (reconcile). "
                f"To fix, {remedy}."
            )
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
        # pydantic-settings passes the entire environment in as the model input, so
        # without this a config error renders unrelated secrets into the traceback.
        "hide_input_in_errors": True,
    }


class KrakenSettings(BaseSettings):
    """Kraken exchange configuration settings."""

    kraken_api_key: str = Field(default="", description="Kraken API key")
    kraken_api_secret: str = Field(default="", description="Kraken API secret (base64)", repr=False)
    kraken_rate_limit: int = Field(
        default=10, ge=1, le=20, description="Max requests per second (1-20)"
    )
    kraken_default_maker_fee: Decimal = Field(
        default=Decimal("0.0016"), ge=0, le=1, description="Maker fee (0-1)"
    )
    kraken_default_taker_fee: Decimal = Field(
        default=Decimal("0.0026"), ge=0, le=1, description="Taker fee (0-1)"
    )

    @model_validator(mode="after")
    def validate_key_secret_pair(self) -> "KrakenSettings":
        """Both key and secret must be set together or both empty.

        When credentials are provided, also validates:
        - API key contains at least 1 non-whitespace character
        - API secret is valid base64
        """
        has_key = bool(self.kraken_api_key)
        has_secret = bool(self.kraken_api_secret)
        if has_key != has_secret:
            raise ValueError(
                "kraken_api_key and kraken_api_secret must both be set or both be empty"
            )
        if has_key:
            if not self.kraken_api_key.strip():
                raise ValueError("kraken_api_key must contain non-whitespace characters")
            try:
                base64.b64decode(self.kraken_api_secret, validate=True)
            except Exception:
                raise ValueError("kraken_api_secret must be valid base64-encoded data")
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


class FMPSettings(BaseSettings):
    """Financial Modeling Prep (FMP) metadata provider configuration settings."""

    fmp_api_key: str = Field(default="", repr=False, description="FMP API key")
    fmp_base_url: str = Field(
        default="https://financialmodelingprep.com/stable", description="FMP API base URL"
    )
    fmp_rate_limit: int = Field(
        default=300, ge=1, description="Max FMP requests per minute (Starter quota)"
    )
    fmp_request_timeout: int = Field(default=30, ge=1, description="Per-request timeout in seconds")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


class FirstRateSettings(BaseSettings):
    """FirstRate Data source configuration settings."""

    firstrate_source_path: str = Field(
        default="", description="Default source directory for CSV imports"
    )
    firstrate_catalog_name: str = Field(
        default="firstrate-etf",
        description="Default catalog name for FirstRate imports",
    )
    firstrate_venue_overrides_path: str = Field(
        default="venue_overrides.csv",
        description="Path to the git-tracked venue overrides CSV (header: ticker,venue)",
    )
    firstrate_venue_exclusions_path: str = Field(
        default="venue_exclusions.csv",
        description=(
            "Path to the git-tracked venue exclusion register (header: ticker,reason,evidence)"
        ),
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


class CatalogSettings(BaseSettings):
    """Catalog configuration settings.

    Catalogs are discovered at runtime from the base path directory.
    Each subdirectory is a named catalog (Parquet format).
    """

    catalog_base_path: str = Field(
        default="",
        description="Base directory containing Parquet catalog subdirectories",
    )
    default_catalog_name: str = Field(
        default="",
        description="Default catalog name to pre-select (empty if none)",
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
    default_currency: str = Field(default="USD", description="Default currency for accounts")
    default_balance: Decimal = Field(
        default=Decimal("1000000"), description="Default starting balance"
    )

    # Backtest settings
    fast_ema_period: int = Field(default=10, description="Fast EMA period for strategies")
    slow_ema_period: int = Field(default=20, description="Slow EMA period for strategies")
    portfolio_value: Decimal = Field(
        default=Decimal("1000000"),
        description="Starting portfolio value in USD for position sizing calculations",
    )
    position_size_pct: Decimal = Field(
        default=Decimal("10.0"),
        description="Position size as percentage of portfolio (e.g., 10.0 = 10%)",
    )
    trade_size: Decimal = Field(
        default=Decimal("1000000"),
        description="Default trade size in SHARES (not USD notional)",
    )

    # Commission settings (IBKR US Equities Tiered)
    commission_per_share: Decimal = Field(
        default=Decimal("0.005"), description="Commission per share"
    )
    commission_min_per_order: Decimal = Field(
        default=Decimal("1.00"), description="Minimum commission per order"
    )
    commission_max_rate: Decimal = Field(
        default=Decimal("0.005"),
        description="Maximum commission as % of order value (0.005 = 0.5%)",
    )

    # Data settings
    data_directory: Path = Field(default=Path("data"), description="Directory for data files")
    mock_data_bars: int = Field(default=1000, description="Number of mock data bars to generate")

    # Database settings
    database_url: Optional[str] = Field(
        default="postgresql://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader",
        description="PostgreSQL database URL",
    )
    database_pool_size: int = Field(default=10, description="Database connection pool size")
    database_max_overflow: int = Field(default=20, description="Maximum overflow connections")
    database_pool_timeout: int = Field(default=30, description="Pool connection timeout in seconds")

    # Logging settings
    log_level: str = Field(default="INFO", description="Logging level")

    # IBKR settings
    ibkr: IBKRSettings = Field(
        default_factory=IBKRSettings, description="Interactive Brokers settings"
    )

    # Kraken settings
    kraken: KrakenSettings = Field(
        default_factory=KrakenSettings, description="Kraken exchange settings"
    )

    # FMP metadata provider settings
    fmp: FMPSettings = Field(
        default_factory=FMPSettings, description="FMP metadata provider settings"
    )

    # FirstRate settings
    firstrate: FirstRateSettings = Field(
        default_factory=FirstRateSettings, description="FirstRate Data settings"
    )

    # Catalog settings
    catalog: CatalogSettings = Field(
        default_factory=CatalogSettings, description="Named catalog settings"
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
    """
    Get application settings.

    Uses ENV environment variable to select configuration file:
    - ENV=dev → loads .env.dev
    - ENV=qa → loads .env.qa
    - ENV not set → loads .env (default)
    """
    env = os.getenv("ENV", "").lower()

    if env in ("dev", "qa", "prod"):
        env_file_path = Path(f".env.{env}")
        if env_file_path.exists():
            return Settings(_env_file=str(env_file_path))  # type: ignore[call-arg]
        else:
            import logging

            logging.warning(f"ENV={env} specified but .env.{env} not found, using .env")

    return Settings()
