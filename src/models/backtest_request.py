"""
Unified backtest request model.

This module provides a single model representing all inputs needed to execute
a backtest, regardless of whether parameters come from CLI arguments or YAML config.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field, field_validator, model_validator

logger = structlog.get_logger(__name__)


def _resolve_instrument_id(symbol: str) -> str:
    """Resolve a bare symbol to a full instrument ID using the catalog.

    Checks the catalog availability cache for an existing entry matching
    the symbol (e.g., finds "GDX.ARCA" when given "GDX"). Falls back to
    "{symbol}.NASDAQ" if no match is found.

    Args:
        symbol: Uppercase trading symbol (e.g., "GDX", "AAPL")

    Returns:
        Full instrument ID (e.g., "GDX.ARCA" or "AAPL.NASDAQ")
    """
    try:
        from src.services.data_catalog import DataCatalogService

        catalog = DataCatalogService()
        prefix = f"{symbol}."
        for key in catalog.availability_cache:
            # Cache keys are "{instrument_id}_{bar_type_spec}"
            instrument_part = key.rsplit("_", 1)[0]
            if instrument_part.startswith(prefix):
                resolved = instrument_part
                logger.info(
                    "instrument_id_resolved_from_catalog",
                    symbol=symbol,
                    resolved=resolved,
                )
                return resolved
    except Exception:
        pass

    # Default: assume NASDAQ (IBKR will resolve to the correct exchange)
    return f"{symbol}.NASDAQ"


class BacktestRequest(BaseModel):
    """
    Unified request model for backtest execution.

    This model can be constructed from CLI arguments or YAML configuration files,
    providing a consistent interface for the BacktestOrchestrator.

    Attributes:
        strategy_type: Strategy identifier (e.g., "sma_crossover", "apolo_rsi")
        strategy_path: Module path for strategy class (e.g., "src.core.strategies:SMA")
        config_path: Module path for config class (e.g., "src.core.strategies:SMAConfig")
        strategy_config: Strategy-specific parameters as a dictionary
        symbol: Trading symbol (e.g., "AAPL")
        instrument_id: Full instrument identifier (e.g., "AAPL.NASDAQ")
        start_date: Backtest start date (UTC)
        end_date: Backtest end date (UTC)
        bar_type: Bar specification (e.g., "1-DAY-LAST")
        persist: Whether to persist results to database
        config_file_path: Source config file path (for tracking)
        starting_balance: Initial account balance
    """

    strategy_type: str = Field(..., min_length=1, description="Strategy identifier")
    strategy_path: str = Field(..., min_length=1, description="Module path to strategy class")
    config_path: str | None = Field(default=None, description="Module path to config class")
    strategy_config: dict[str, Any] = Field(
        default_factory=dict, description="Strategy-specific parameters"
    )
    symbol: str = Field(..., min_length=1, description="Trading symbol")
    instrument_id: str = Field(..., min_length=1, description="Full instrument identifier")
    start_date: datetime = Field(..., description="Backtest start date")
    end_date: datetime = Field(..., description="Backtest end date")
    bar_type: str = Field(..., min_length=1, description="Bar type specification")

    # Execution options
    persist: bool = Field(default=True, description="Persist results to database")
    config_file_path: str | None = Field(
        default=None, description="Source config file path for tracking"
    )
    data_source: str = Field(
        default="catalog", description="Data source: catalog, ibkr, kraken, mock"
    )

    @field_validator("data_source")
    @classmethod
    def validate_data_source(cls, v: str) -> str:
        """Validate data_source against allowed values."""
        allowed = {"catalog", "ibkr", "kraken", "mock"}
        if v not in allowed:
            raise ValueError(f"Invalid data_source '{v}'. Allowed: {', '.join(sorted(allowed))}")
        return v

    # Account settings
    starting_balance: Decimal = Field(
        default=Decimal("1000000"), ge=0, description="Initial account balance"
    )

    model_config = {"arbitrary_types_allowed": True}

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def ensure_timezone_aware(cls, v: datetime) -> datetime:
        """Ensure dates are timezone-aware (UTC)."""
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @model_validator(mode="after")
    def validate_date_range(self) -> "BacktestRequest":
        """Validate that start_date is before end_date."""
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        return self

    @classmethod
    def from_yaml_config(
        cls,
        yaml_data: dict[str, Any],
        persist: bool = True,
        config_file_path: str | None = None,
        data_source: str = "catalog",
    ) -> "BacktestRequest":
        """
        Build BacktestRequest from loaded YAML configuration.

        Args:
            yaml_data: Parsed YAML configuration dictionary
            persist: Whether to persist results to database
            config_file_path: Path to the source config file
            data_source: Data source used (catalog, ibkr, kraken, mock)

        Returns:
            BacktestRequest instance

        Raises:
            ValueError: If required fields are missing or invalid
        """
        # Extract required fields
        strategy_path = yaml_data.get("strategy_path")
        config_path = yaml_data.get("config_path")
        config_section = yaml_data.get("config", {})

        if not strategy_path or not config_path:
            raise ValueError("YAML must contain 'strategy_path' and 'config_path'")

        # Extract strategy type from strategy_path (e.g., "src.core.strategies.apolo_rsi:ApoloRSI")
        strategy_type = strategy_path.split(":")[-1].lower() if ":" in strategy_path else "unknown"

        # Extract instrument_id from config
        instrument_id = config_section.get("instrument_id")
        if not instrument_id:
            raise ValueError("config section must contain 'instrument_id'")
        instrument_id_str = str(instrument_id)

        # Extract symbol from instrument_id (e.g., "AAPL.NASDAQ" -> "AAPL")
        symbol = instrument_id_str.split(".")[0]

        # Extract bar_type from config
        bar_type = config_section.get("bar_type")
        if not bar_type:
            raise ValueError("config section must contain 'bar_type'")
        bar_type_str = str(bar_type)

        # Parse bar_type to get spec (e.g., "AMD.NASDAQ-1-DAY-LAST-EXTERNAL" -> "1-DAY-LAST")
        parts = bar_type_str.split("-")
        if len(parts) >= 4:
            bar_type_spec = f"{parts[1]}-{parts[2]}-{parts[3]}"
        else:
            bar_type_spec = "1-DAY-LAST"

        # Extract dates from backtest section (optional)
        backtest_section = yaml_data.get("backtest", {})
        start_date_str = backtest_section.get("start_date")
        end_date_str = backtest_section.get("end_date")

        # Parse dates or use defaults
        if start_date_str:
            start_date = datetime.fromisoformat(start_date_str).replace(tzinfo=timezone.utc)
        else:
            raise ValueError("backtest section must contain 'start_date'")

        if end_date_str:
            end_date = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
        else:
            raise ValueError("backtest section must contain 'end_date'")

        # Extract starting balance
        initial_capital = backtest_section.get("initial_capital", 1000000)

        # Build strategy_config (excluding already extracted fields)
        excluded_keys = {"instrument_id", "bar_type"}
        strategy_config = {k: v for k, v in config_section.items() if k not in excluded_keys}

        return cls(
            strategy_type=strategy_type,
            strategy_path=strategy_path,
            config_path=config_path,
            strategy_config=strategy_config,
            symbol=symbol,
            instrument_id=instrument_id_str,
            start_date=start_date,
            end_date=end_date,
            bar_type=bar_type_spec,
            persist=persist,
            config_file_path=config_file_path,
            starting_balance=Decimal(str(initial_capital)),
            data_source=data_source,
        )

    @classmethod
    def from_yaml_file(
        cls,
        config_file: str | Path,
        persist: bool = True,
    ) -> "BacktestRequest":
        """
        Load BacktestRequest from a YAML configuration file.

        Args:
            config_file: Path to YAML configuration file
            persist: Whether to persist results to database

        Returns:
            BacktestRequest instance

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If config is invalid
        """
        import yaml

        config_path = Path(config_file)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")

        with open(config_path, "r") as f:
            yaml_data = yaml.safe_load(f)

        return cls.from_yaml_config(
            yaml_data=yaml_data,
            persist=persist,
            config_file_path=str(config_path.absolute()),
        )

    @classmethod
    def from_cli_args(
        cls,
        strategy: str,
        symbol: str,
        start: datetime,
        end: datetime,
        bar_type_spec: str = "1-DAY-LAST",
        persist: bool = True,
        starting_balance: Decimal = Decimal("1000000"),
        data_source: str = "catalog",
        **strategy_params: Any,
    ) -> "BacktestRequest":
        """
        Build BacktestRequest from CLI arguments.

        Args:
            strategy: Strategy name/identifier (e.g., "sma_crossover", "apolo_rsi")
            symbol: Trading symbol (e.g., "AAPL")
            start: Backtest start date
            end: Backtest end date
            bar_type_spec: Bar type specification (e.g., "1-DAY-LAST")
            persist: Whether to persist results to database
            starting_balance: Initial account balance
            data_source: Data source (catalog, ibkr, kraken, mock)
            **strategy_params: Strategy-specific parameters

        Returns:
            BacktestRequest instance
        """
        from src.core.strategy_registry import StrategyRegistry

        # Ensure strategies are discovered
        StrategyRegistry.discover()

        # Resolve strategy to get paths
        strategy_def = StrategyRegistry.get(strategy)
        if not strategy_def:
            raise ValueError(f"Unknown strategy: {strategy}")

        # Build instrument_id from symbol
        if "." in symbol:
            instrument_id = symbol.upper()
        elif data_source == "kraken":
            instrument_id = f"{symbol.upper()}.KRAKEN"
        else:
            # Try to resolve the exchange from the catalog's availability
            # cache (e.g., GDX may be stored as GDX.ARCA, not GDX.NASDAQ)
            instrument_id = _resolve_instrument_id(symbol.upper())

        return cls(
            strategy_type=strategy,
            strategy_path=strategy_def.strategy_path,
            config_path=strategy_def.config_path,
            strategy_config=strategy_params,
            symbol=symbol.upper(),
            instrument_id=instrument_id,
            start_date=start,
            end_date=end,
            bar_type=bar_type_spec,
            persist=persist,
            config_file_path=None,
            starting_balance=starting_balance,
            data_source=data_source,
        )

    def to_config_snapshot(self) -> dict[str, Any]:
        """
        Convert request to a config snapshot for database storage.

        Returns:
            Dictionary suitable for JSONB storage in backtest_runs.config_snapshot
        """
        return {
            "strategy_type": self.strategy_type,
            "strategy_path": self.strategy_path,
            "config_path": self.config_path,
            "strategy_config": self.strategy_config,
            "symbol": self.symbol,
            "instrument_id": self.instrument_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "bar_type": self.bar_type,
            "config_file_path": self.config_file_path,
            "starting_balance": str(self.starting_balance),
            "data_source": self.data_source,
        }
