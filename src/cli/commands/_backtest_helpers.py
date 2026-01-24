"""Helper functions for backtest CLI commands.

This module extracts common logic shared between run_backtest() and run_config_backtest()
to reduce code duplication and improve maintainability.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

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
