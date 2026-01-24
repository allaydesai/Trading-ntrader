"""Helper functions for backtest CLI commands.

This module extracts common logic shared between run_backtest() and run_config_backtest()
to reduce code duplication and improve maintainability.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

import click
from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.core.backtest_orchestrator import BacktestOrchestrator
from src.models.backtest_request import BacktestRequest
from src.models.backtest_result import BacktestResult
from src.services.data_catalog import DataCatalogService
from src.utils.mock_data import generate_mock_data_from_yaml


@dataclass
class DataLoadResult:
    """Result of loading backtest data from catalog or mock generation.

    Attributes:
        bars: List of loaded or generated Bar objects
        instrument: The instrument for the backtest
        data_source_used: Source description ("Parquet Catalog", "IBKR Auto-fetch", "Mock")
    """

    bars: list[Bar]
    instrument: Instrument
    data_source_used: str


def apply_cli_overrides(
    request: BacktestRequest,
    *,
    symbol: str | None,
    start: datetime | None,
    end: datetime | None,
    starting_balance: float | None,
) -> BacktestRequest:
    """Apply CLI argument overrides to a config-based BacktestRequest.

    When using config mode with CLI overrides (e.g., `backtest run config.yaml --start 2024-06-01`),
    this function applies the override values to the base request from the config file.

    Args:
        request: The base BacktestRequest loaded from YAML config
        symbol: Optional symbol override (rebuilds instrument_id if provided)
        start: Optional start date override
        end: Optional end date override
        starting_balance: Optional starting balance override

    Returns:
        A new BacktestRequest with overrides applied

    Raises:
        ValueError: If overrides create an invalid date range
    """
    updates: dict = {}

    if symbol is not None:
        # Rebuild instrument_id from symbol
        symbol_upper = symbol.upper()
        if "." in symbol_upper:
            instrument_id = symbol_upper
        else:
            instrument_id = f"{symbol_upper}.NASDAQ"
        updates["symbol"] = symbol_upper.split(".")[0]
        updates["instrument_id"] = instrument_id

    if start is not None:
        # Ensure timezone-aware
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        updates["start_date"] = start

    if end is not None:
        # Ensure timezone-aware
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        updates["end_date"] = end

    if starting_balance is not None:
        updates["starting_balance"] = Decimal(str(starting_balance))

    if not updates:
        return request

    return request.model_copy(update=updates)


def resolve_backtest_request(
    *,
    config_file: str | None,
    symbol: str | None,
    strategy: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str | None,
    starting_balance: float | None,
    persist: bool,
    console: Console,
    fast_period: int | None = None,
    slow_period: int | None = None,
    trade_size: int | None = None,
    timeframe: str | None = None,
) -> tuple[BacktestRequest, str]:
    """Resolve inputs into BacktestRequest based on mode (config vs CLI).

    This function determines whether to use config mode (YAML file) or CLI mode
    (all parameters from command line) and builds the appropriate BacktestRequest.

    Args:
        config_file: Path to YAML config file (if provided, uses config mode)
        symbol: Trading symbol (required in CLI mode, optional override in config mode)
        strategy: Strategy name (required in CLI mode, ignored in config mode)
        start: Start date (required in CLI mode, optional override in config mode)
        end: End date (required in CLI mode, optional override in config mode)
        data_source: Data source to use ("catalog" or "mock")
        starting_balance: Starting balance (optional override in both modes)
        persist: Whether to persist results to database
        console: Rich console for output
        fast_period: Fast SMA period (CLI mode only)
        slow_period: Slow SMA period (CLI mode only)
        trade_size: Trade size in shares (CLI mode only)
        timeframe: Bar timeframe (CLI mode only)

    Returns:
        Tuple of (BacktestRequest, resolved_data_source)

    Raises:
        click.UsageError: If required parameters are missing for the mode
        ValueError: If config file has invalid content
    """
    # Determine mode based on config_file presence
    if config_file is not None:
        return _resolve_config_mode(
            config_file=config_file,
            symbol=symbol,
            start=start,
            end=end,
            data_source=data_source,
            starting_balance=starting_balance,
            persist=persist,
            console=console,
        )
    else:
        return _resolve_cli_mode(
            symbol=symbol,
            strategy=strategy,
            start=start,
            end=end,
            data_source=data_source,
            starting_balance=starting_balance,
            persist=persist,
            console=console,
            fast_period=fast_period,
            slow_period=slow_period,
            trade_size=trade_size,
            timeframe=timeframe,
        )


def _resolve_config_mode(
    *,
    config_file: str,
    symbol: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str | None,
    starting_balance: float | None,
    persist: bool,
    console: Console,
) -> tuple[BacktestRequest, str]:
    """Resolve request from YAML config file with optional CLI overrides."""
    import yaml

    # Validate data source for mock mode
    resolved_data_source = data_source or "catalog"

    # Load YAML config
    config_path = Path(config_file)
    if not config_path.exists():
        raise click.UsageError(f"Configuration file not found: {config_file}")

    with open(config_path, "r") as f:
        yaml_data = yaml.safe_load(f)

    # Build base request from YAML
    request = BacktestRequest.from_yaml_config(
        yaml_data=yaml_data,
        persist=persist,
        config_file_path=str(config_path.absolute()),
        data_source=resolved_data_source,
    )

    # Apply CLI overrides
    request = apply_cli_overrides(
        request,
        symbol=symbol,
        start=start,
        end=end,
        starting_balance=starting_balance,
    )

    return request, resolved_data_source


def _resolve_cli_mode(
    *,
    symbol: str | None,
    strategy: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str | None,
    starting_balance: float | None,
    persist: bool,
    console: Console,
    fast_period: int | None,
    slow_period: int | None,
    trade_size: int | None,
    timeframe: str | None,
) -> tuple[BacktestRequest, str]:
    """Resolve request from CLI arguments (traditional mode)."""
    # Validate required parameters for CLI mode
    if symbol is None:
        raise click.UsageError(
            "Missing required option '--symbol' / '-sym'. "
            "In CLI mode, --symbol is required. "
            "Use a config file for config mode: backtest run config.yaml"
        )
    if start is None:
        raise click.UsageError(
            "Missing required option '--start' / '-st'. "
            "In CLI mode, --start is required. "
            "Use a config file for config mode: backtest run config.yaml"
        )
    if end is None:
        raise click.UsageError(
            "Missing required option '--end' / '-e'. "
            "In CLI mode, --end is required. "
            "Use a config file for config mode: backtest run config.yaml"
        )

    # Mock data source requires a config file
    resolved_data_source = data_source or "catalog"
    if resolved_data_source == "mock":
        raise click.UsageError(
            "Mock data source requires a YAML config file. "
            "Use: backtest run config.yaml --data-source mock"
        )

    # Ensure timezone-aware dates
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    # Determine bar type from timeframe or auto-detect
    if timeframe:
        bar_type_spec = f"{timeframe}-LAST"
    else:
        # Auto-detect: Date-only (YYYY-MM-DD) → 1-DAY, Date-time → 1-MINUTE
        if start.time().hour == 0 and start.time().minute == 0 and start.time().second == 0:
            bar_type_spec = "1-DAY-LAST"
        else:
            bar_type_spec = "1-MINUTE-LAST"

    # Use defaults for optional CLI parameters
    resolved_strategy = strategy or "sma_crossover"
    resolved_fast_period = fast_period or 10
    resolved_slow_period = slow_period or 20
    resolved_trade_size = trade_size or 1000000

    # Calculate portfolio value and starting balance
    # Constants for portfolio sizing (10x multiplier for percentage-based sizing)
    portfolio_size_multiplier = 10
    portfolio_value = Decimal(str(resolved_trade_size * portfolio_size_multiplier))
    resolved_starting_balance = (
        Decimal(str(starting_balance)) if starting_balance else portfolio_value
    )

    # Build strategy parameters based on strategy type
    if resolved_strategy in ["sma", "sma_crossover", "sma_crossover_long_only"]:
        strategy_params = {
            "portfolio_value": portfolio_value,
            "position_size_pct": Decimal("10.0"),
            "fast_period": resolved_fast_period,
            "slow_period": resolved_slow_period,
        }
    elif resolved_strategy == "bollinger_reversal":
        strategy_params = {"portfolio_value": portfolio_value}
    else:
        strategy_params = {"trade_size": resolved_trade_size}

    # Build BacktestRequest from CLI args
    request = BacktestRequest.from_cli_args(
        strategy=resolved_strategy,
        symbol=symbol.upper(),
        start=start,
        end=end,
        bar_type_spec=bar_type_spec,
        persist=persist,
        starting_balance=resolved_starting_balance,
        **strategy_params,
    )

    return request, resolved_data_source


async def load_backtest_data(
    *,
    data_source: Literal["catalog", "mock"],
    instrument_id: str,
    bar_type_spec: str,
    start: datetime,
    end: datetime,
    console: Console,
    catalog_service: DataCatalogService | None = None,
    yaml_data: dict | None = None,
) -> DataLoadResult:
    """Load backtest data from catalog or generate mock data.

    Unified data loading that handles:
    - Catalog data with availability checking
    - IBKR auto-fetch when catalog data is incomplete
    - Mock data generation from YAML configuration

    Args:
        data_source: Either "catalog" for real data or "mock" for generated data
        instrument_id: The instrument identifier (e.g., "AAPL.NASDAQ")
        bar_type_spec: Bar type specification (e.g., "1-DAY-LAST")
        start: Start datetime for the data range
        end: End datetime for the data range
        console: Rich console for output
        catalog_service: Optional catalog service instance (created if not provided)
        yaml_data: YAML configuration dict (required for mock data source)

    Returns:
        DataLoadResult containing bars, instrument, and source description

    Raises:
        ValueError: If mock data source is used without yaml_data
        DataNotFoundError: If no data can be found or fetched
        IBKRConnectionError: If IBKR connection fails during fetch
    """
    if data_source == "mock":
        return await _load_mock_data(yaml_data=yaml_data, console=console)
    else:
        return await _load_catalog_data(
            instrument_id=instrument_id,
            bar_type_spec=bar_type_spec,
            start=start,
            end=end,
            console=console,
            catalog_service=catalog_service,
        )


async def _load_mock_data(
    *,
    yaml_data: dict | None,
    console: Console,
) -> DataLoadResult:
    """Load mock data from YAML configuration."""
    if yaml_data is None:
        raise ValueError("yaml_data is required for mock data source")

    bars, instrument, _, _ = generate_mock_data_from_yaml(yaml_data)

    console.print(f"   Generated {len(bars):,} mock bars")

    return DataLoadResult(
        bars=bars,
        instrument=instrument,
        data_source_used="Mock",
    )


async def _load_catalog_data(
    *,
    instrument_id: str,
    bar_type_spec: str,
    start: datetime,
    end: datetime,
    console: Console,
    catalog_service: DataCatalogService | None,
) -> DataLoadResult:
    """Load data from catalog with IBKR fallback."""
    # Initialize catalog service if not provided
    if catalog_service is None:
        catalog_service = DataCatalogService()

    # Check availability
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Checking data availability...", total=None)
        availability = catalog_service.get_availability(instrument_id, bar_type_spec)
        progress.update(task, completed=True)

    # Determine data source based on availability
    data_source_used = "Parquet Catalog"

    if not availability:
        console.print(
            f"   No data in catalog for {instrument_id}",
            style="yellow",
        )
        console.print("   Will attempt to fetch from IBKR...", style="yellow")
        data_source_used = "IBKR Auto-fetch"
    elif not availability.covers_range(start, end):
        console.print(
            "   Requested date range partially available in catalog",
            style="yellow",
        )
        console.print(f"   Requested: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
        console.print(
            f"   Available: {availability.start_date.strftime('%Y-%m-%d')} to "
            f"{availability.end_date.strftime('%Y-%m-%d')}"
        )
        console.print("   Will attempt to fetch missing data from IBKR...", style="yellow")
        data_source_used = "IBKR Auto-fetch"
    else:
        console.print("   Data available in catalog", style="green")
        console.print(f"   Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
        if availability.file_count:
            console.print(
                f"   Files: {availability.file_count} | Rows: ~{availability.total_rows:,}"
            )

    # Load/fetch data
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Loading/fetching data...", total=None)

        bars = await catalog_service.fetch_or_load(
            instrument_id=instrument_id,
            start=start,
            end=end,
            bar_type_spec=bar_type_spec,
            correlation_id=f"backtest-{instrument_id}-{start.strftime('%Y%m%d')}",
        )

        progress.update(task, completed=True)

    # Load instrument
    instrument = catalog_service.load_instrument(instrument_id)

    # Fetch instrument from IBKR if not in catalog
    if instrument is None:
        console.print(
            f"   Instrument {instrument_id} not in catalog, fetching from IBKR...",
            style="yellow",
        )
        try:
            instrument = await catalog_service.fetch_instrument_from_ibkr(instrument_id)
            console.print(
                "   Instrument fetched and saved to catalog",
                style="green",
            )
        except Exception as e:
            console.print(
                f"   Failed to fetch instrument from IBKR: {e}",
                style="red",
            )
            # Create fallback test instrument
            from src.utils.mock_data import create_test_instrument

            symbol = instrument_id.split(".")[0]
            venue = instrument_id.split(".")[-1] if "." in instrument_id else "SIM"
            instrument, _ = create_test_instrument(symbol, venue)
            console.print("   Using fallback test instrument", style="yellow")

    console.print(f"   Loaded {len(bars):,} bars")

    return DataLoadResult(
        bars=bars,
        instrument=instrument,
        data_source_used=data_source_used,
    )


async def execute_backtest(
    *,
    request: BacktestRequest,
    bars: list[Bar],
    instrument: Instrument,
    console: Console,
    progress_message: str = "Running backtest...",
) -> tuple[BacktestResult, UUID | None]:
    """Execute backtest with progress indicator and proper cleanup.

    Wraps BacktestOrchestrator execution with:
    - Progress spinner display
    - Guaranteed orchestrator disposal (even on error)

    Args:
        request: The backtest request configuration
        bars: List of Bar objects for the backtest
        instrument: The instrument to trade
        console: Rich console for progress display
        progress_message: Custom message for the progress spinner

    Returns:
        Tuple of (BacktestResult, run_id if persisted else None)

    Raises:
        ValueError: If bars are empty or strategy cannot be created
    """
    orchestrator = BacktestOrchestrator()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task(progress_message, total=None)
            result, run_id = await orchestrator.execute(request, bars, instrument)
            progress.update(task, completed=True)

        return result, run_id
    finally:
        orchestrator.dispose()


def display_backtest_results(
    *,
    result: BacktestResult,
    console: Console,
    run_id: UUID | None,
    persist: bool,
    context_rows: dict[str, str],
    table_title: str = "Backtest Results",
    execution_time: float | None = None,
) -> None:
    """Display backtest results in a Rich table with performance summary.

    Creates a formatted table with:
    - Context rows (strategy, symbol, period, etc.) first
    - Performance metrics (return, trades, win rate, etc.)
    - Persistence status
    - Final performance summary (profitable/losing/break-even)

    Args:
        result: The backtest result to display
        console: Rich console for output
        run_id: UUID of the persisted run (if any)
        persist: Whether persistence was requested
        context_rows: Dict of label->value pairs to show at top of table
        table_title: Title for the results table
        execution_time: Optional execution time in seconds to display
    """
    console.print("Backtest Results", style="cyan bold")
    console.print()

    # Create results table
    table = Table(title=table_title)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    # Add context rows first
    for label, value in context_rows.items():
        table.add_row(label, value)

    # Add metric rows
    table.add_row("Total Return", f"{result.total_return * 100:.2f}%")
    table.add_row("Total Trades", str(result.total_trades))
    table.add_row("Winning Trades", str(result.winning_trades))
    table.add_row("Losing Trades", str(result.losing_trades))
    table.add_row("Win Rate", f"{result.win_rate:.1f}%")
    table.add_row("Largest Win", f"${result.largest_win:.2f}")
    table.add_row("Largest Loss", f"${result.largest_loss:.2f}")
    table.add_row("Final Balance", f"${result.final_balance:,.2f}")

    # Add execution time if provided
    if execution_time is not None:
        table.add_row("Execution Time", f"{execution_time:.2f}s")

    # Add persistence info
    if persist and run_id:
        table.add_row("Persisted", f"Yes (Run ID: {str(run_id)[:8]}...)")
    elif persist:
        table.add_row("Persisted", "Failed")
    else:
        table.add_row("Persisted", "No (--no-persist)")

    console.print(table)
    console.print()

    # Performance summary
    if result.total_return > 0:
        console.print("Strategy was profitable!", style="green bold")
    elif result.total_return < 0:
        console.print("Strategy lost money", style="red bold")
    else:
        console.print("Strategy broke even", style="yellow bold")
