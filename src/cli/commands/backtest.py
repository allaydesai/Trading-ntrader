"""Backtest commands for running strategies with real data."""

import asyncio
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.cli.commands.compare import compare_backtests
from src.cli.commands.reproduce import reproduce_backtest
from src.cli.commands.show import show_backtest_details
from src.core.backtest_orchestrator import BacktestOrchestrator
from src.core.strategy_registry import StrategyRegistry
from src.models.backtest_request import BacktestRequest
from src.services.data_catalog import DataCatalogService
from src.services.exceptions import (
    CatalogCorruptionError,
    CatalogError,
    DataNotFoundError,
    IBKRConnectionError,
    RateLimitExceededError,
)
from src.utils.bar_type_utils import parse_bar_type_spec
from src.utils.error_formatter import ErrorFormatter
from src.utils.error_messages import (
    CATALOG_CORRUPTION_DETECTED,
    DATA_NOT_FOUND_NO_IBKR,
    IBKR_CONNECTION_FAILED,
    RATE_LIMIT_EXCEEDED,
    format_error_with_context,
)

console = Console()
error_formatter = ErrorFormatter(console)

# Constants for portfolio sizing
# When using percentage-based sizing, multiply trade_size by this factor
# to get portfolio_value (e.g., for 10% position sizing)
PORTFOLIO_SIZE_MULTIPLIER = 10
DEFAULT_POSITION_SIZE_PCT = Decimal("10.0")


def validate_strategy(ctx, param, value):
    """Validate strategy name against registry."""
    if value is None:
        return "sma_crossover"  # default

    # Ensure strategies are discovered
    StrategyRegistry.discover()

    if not StrategyRegistry.exists(value):
        available = StrategyRegistry.get_names()
        raise click.BadParameter(
            f"Unknown strategy '{value}'. Available strategies: {', '.join(available)}"
        )
    return value


def get_strategy_names_help() -> str:
    """Get help text with available strategy names."""
    StrategyRegistry.discover()
    names = StrategyRegistry.get_names()
    return f"Strategy to run. Available: {', '.join(names)}"


@click.group()
def backtest():
    """Backtest commands for running strategies with real data."""
    pass


@backtest.command("run")
@click.option(
    "--strategy",
    "-s",
    default="sma_crossover",
    callback=validate_strategy,
    help="Strategy to run. Use 'backtest list' to see available strategies.",
)
@click.option("--symbol", "-sym", required=True, help="Trading symbol (e.g., AAPL, EUR/USD)")
@click.option(
    "--start",
    "-st",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]),
    help="Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
)
@click.option(
    "--end",
    "-e",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]),
    help="End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
)
@click.option("--fast-period", "-f", default=10, type=int, help="Fast SMA period (default: 10)")
@click.option("--slow-period", "-sl", default=20, type=int, help="Slow SMA period (default: 20)")
@click.option(
    "--trade-size",
    "-ts",
    default=1000000,
    type=int,
    help=(
        "Trade size in SHARES (default: 1,000,000 shares). Note: 1M shares @ $180 = $180M notional"
    ),
)
@click.option(
    "--timeframe",
    "-t",
    default=None,
    type=click.Choice(
        ["1-MINUTE", "5-MINUTE", "15-MINUTE", "1-HOUR", "4-HOUR", "1-DAY", "1-WEEK"],
        case_sensitive=False,
    ),
    help="Bar timeframe (auto-detected from date format if not specified)",
)
@click.option(
    "--persist/--no-persist",
    default=True,
    help="Save backtest results to database (default: persist)",
)
def run_backtest(
    strategy: str,
    symbol: str,
    start: datetime,
    end: datetime,
    fast_period: int,
    slow_period: int,
    trade_size: int,
    timeframe: str | None,
    persist: bool,
):
    """Run backtest with real market data from database."""

    async def run_backtest_async():
        # Reason: Track execution time for performance reporting
        execution_start_time = time.time()

        # Ensure start and end dates are timezone-aware (UTC) for catalog comparison
        nonlocal start, end
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        # Handle strategy alias
        display_strategy = strategy
        if strategy == "sma":
            display_strategy = "SMA_CROSSOVER"

        console.print(
            f"🚀 Running {display_strategy.upper()} backtest for {symbol.upper()}",
            style="cyan bold",
        )
        console.print(f"   Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")

        # Display strategy-specific parameters
        if strategy in ["sma", "sma_crossover"]:
            console.print(f"   Strategy: Fast SMA({fast_period}) vs Slow SMA({slow_period})")
        else:
            console.print(f"   Strategy: {display_strategy.replace('_', ' ').title()}")

        console.print(f"   Trade Size: {trade_size:,}")
        console.print()

        try:
            # Reason: Initialize catalog service for data access
            catalog_service = DataCatalogService()

            # Reason: Convert symbol to instrument_id format (e.g., "AAPL" -> "AAPL.NASDAQ")
            # If venue is provided in symbol (e.g., "SPY.ARCA"), use it.
            # Otherwise default to NASDAQ.
            if "." in symbol:
                instrument_id = symbol.upper()
            else:
                instrument_id = f"{symbol.upper()}.NASDAQ"

            # Reason: Determine bar type from explicit timeframe or auto-detect from date format
            if timeframe:
                # Use explicit timeframe if provided
                bar_type_spec = f"{timeframe}-LAST"
            else:
                # Auto-detect: Date-only (YYYY-MM-DD) → 1-DAY, Date-time → 1-MINUTE
                # Click parses date-only as midnight UTC (00:00:00)
                if start.time().hour == 0 and start.time().minute == 0 and start.time().second == 0:
                    bar_type_spec = "1-DAY-LAST"
                else:
                    bar_type_spec = "1-MINUTE-LAST"

            # Reason: Check catalog availability and fetch from IBKR if needed
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Checking data availability...", total=None)
                availability = catalog_service.get_availability(instrument_id, bar_type_spec)
                progress.update(task, completed=True)

            # Reason: Determine data source and adjust dates if needed
            adjusted_start = start
            adjusted_end = end
            data_source = "Parquet Catalog"

            if not availability:
                console.print(
                    f"⚠️  No data in catalog for {symbol.upper()}",
                    style="yellow",
                )
                console.print(f"   Instrument: {instrument_id}")
                console.print(f"   Bar type: {bar_type_spec}")
                console.print()
                console.print("   Will attempt to fetch from IBKR...", style="yellow")
                console.print()
                data_source = "IBKR Auto-fetch"
            elif not availability.covers_range(start, end):
                console.print(
                    "⚠️  Requested date range partially available in catalog",
                    style="yellow",
                )
                console.print(
                    f"   Requested: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
                )
                console.print(
                    f"   Available: {availability.start_date.strftime('%Y-%m-%d')} to "
                    f"{availability.end_date.strftime('%Y-%m-%d')}"
                )
                console.print()
                console.print("   Will attempt to fetch missing data from IBKR...", style="yellow")
                console.print()
                data_source = "IBKR Auto-fetch"
            else:
                console.print("✅ Data available in catalog", style="green")
                console.print(
                    f"   Period: {adjusted_start.strftime('%Y-%m-%d')} to "
                    f"{adjusted_end.strftime('%Y-%m-%d')}"
                )
                console.print(
                    f"   Files: {availability.file_count} | Rows: ~{availability.total_rows:,}"
                )
                console.print()

            # Reason: Load data from catalog or fetch from IBKR with progress indicator
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Loading/fetching data...", total=None)

                data_load_start = time.time()
                try:
                    # Reason: Use fetch_or_load() to automatically fetch from IBKR if needed
                    bars = await catalog_service.fetch_or_load(
                        instrument_id=instrument_id,
                        start=adjusted_start,
                        end=adjusted_end,
                        bar_type_spec=bar_type_spec,
                        correlation_id=f"backtest-{symbol}-{start.strftime('%Y%m%d')}",
                    )
                    data_load_time = time.time() - data_load_start

                    # Reason: Load instrument from catalog after bars are loaded/fetched
                    instrument = catalog_service.load_instrument(instrument_id)

                    # Reason: If instrument not in catalog, fetch it from IBKR (one-time operation)
                    if instrument is None:
                        console.print(
                            f"⚠️  Instrument {instrument_id} not in catalog, fetching from IBKR...",
                            style="yellow",
                        )
                        try:
                            instrument = await catalog_service.fetch_instrument_from_ibkr(
                                instrument_id
                            )
                            console.print(
                                "✅ Instrument fetched and saved to catalog",
                                style="green",
                            )
                        except Exception as e:
                            console.print(
                                f"❌ Failed to fetch instrument from IBKR: {e}",
                                style="red",
                            )
                            console.print(
                                "   Using fallback test instrument",
                                style="yellow",
                            )

                    progress.update(task, completed=True)

                    # Reason: Show data load/fetch performance with source indication
                    if data_source == "IBKR Auto-fetch":
                        console.print(
                            f"✅ Fetched {len(bars):,} bars from IBKR in {data_load_time:.2f}s",
                            style="green bold",
                        )
                        console.print(
                            "   💾 Data saved to catalog - future backtests will use cached data",
                            style="cyan",
                        )
                        console.print()
                    else:
                        console.print(
                            f"✅ Loaded {len(bars):,} bars from catalog in {data_load_time:.2f}s",
                            style="green",
                        )
                        console.print()
                except DataNotFoundError:
                    progress.update(task, completed=True)
                    console.print()
                    error_msg = format_error_with_context(
                        DATA_NOT_FOUND_NO_IBKR,
                        instrument=instrument_id,
                        start_date=adjusted_start.strftime("%Y-%m-%d"),
                        end_date=adjusted_end.strftime("%Y-%m-%d"),
                    )
                    error_formatter.format_error(error_msg)
                    sys.exit(error_formatter.get_exit_code(error_msg))

                except IBKRConnectionError as e:
                    progress.update(task, completed=True)
                    console.print()
                    error_msg = format_error_with_context(
                        IBKR_CONNECTION_FAILED,
                        connection_details=str(e),
                    )
                    error_formatter.format_error(error_msg)
                    sys.exit(error_formatter.get_exit_code(error_msg))

                except RateLimitExceededError as e:
                    progress.update(task, completed=True)
                    console.print()
                    error_msg = format_error_with_context(
                        RATE_LIMIT_EXCEEDED,
                        retry_after=str(e.retry_after),
                        request_count=str(e.request_count or "unknown"),
                    )
                    error_formatter.format_error(error_msg)
                    sys.exit(error_formatter.get_exit_code(error_msg))

                except CatalogCorruptionError as e:
                    progress.update(task, completed=True)
                    console.print()
                    error_msg = format_error_with_context(
                        CATALOG_CORRUPTION_DETECTED,
                        file_path=str(e),
                    )
                    error_formatter.format_error(error_msg)
                    sys.exit(error_formatter.get_exit_code(error_msg))

                except CatalogError as e:
                    progress.update(task, completed=True)
                    console.print()
                    error_formatter.print_warning(
                        f"Catalog error: {str(e)}",
                        "Check logs for more details",
                    )
                    sys.exit(4)

                except Exception as e:
                    progress.update(task, completed=True)
                    console.print()
                    error_formatter.print_warning(
                        f"Unexpected error: {str(e)}",
                        "Check logs for stack trace",
                    )
                    sys.exit(4)

            # Reason: Run backtest with catalog data using BacktestOrchestrator
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Running backtest...", total=None)

                # Prepare strategy parameters
                # Note: SMA uses percentage-based sizing, others use trade_size
                portfolio_value = Decimal(str(trade_size * PORTFOLIO_SIZE_MULTIPLIER))
                if strategy in ["sma", "sma_crossover", "sma_crossover_long_only"]:
                    # SMA uses portfolio_value and position_size_pct
                    strategy_params = {
                        "portfolio_value": portfolio_value,
                        "position_size_pct": DEFAULT_POSITION_SIZE_PCT,
                        "fast_period": fast_period,
                        "slow_period": slow_period,
                    }
                elif strategy == "bollinger_reversal":
                    # Use defaults for now, can add CLI args later
                    strategy_params = {
                        "portfolio_value": portfolio_value,
                    }
                else:
                    # Other strategies use trade_size
                    strategy_params = {
                        "trade_size": trade_size,
                    }

                # Build BacktestRequest from CLI args
                request = BacktestRequest.from_cli_args(
                    strategy=strategy,
                    symbol=symbol.upper(),
                    start=adjusted_start,
                    end=adjusted_end,
                    bar_type_spec=bar_type_spec,
                    persist=persist,
                    starting_balance=portfolio_value,
                    **strategy_params,
                )

                # Run backtest with orchestrator
                orchestrator = BacktestOrchestrator()
                try:
                    result, run_id = await orchestrator.execute(request, bars, instrument)
                finally:
                    # Ensure cleanup happens even on error
                    orchestrator.dispose()

                progress.update(task, completed=True)

            # Display results
            console.print("🎯 Backtest Results", style="cyan bold")
            console.print()

            # Create results table
            strategy_name = display_strategy.replace("_", " ").title()
            table = Table(title=f"{symbol.upper()} {strategy_name} Strategy Results")
            table.add_column("Metric", style="cyan", no_wrap=True)
            table.add_column("Value", style="green")

            # Add strategy-specific description
            if strategy in ["sma", "sma_crossover"]:
                strategy_description = f"SMA({fast_period}/{slow_period})"
            else:
                strategy_description = display_strategy.replace("_", " ").title()

            # Reason: Calculate total execution time
            total_execution_time = time.time() - execution_start_time

            table.add_row("Strategy", strategy_description)
            table.add_row("Symbol", symbol.upper())
            table.add_row("Period", f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
            table.add_row("Data Source", "Parquet Catalog")
            table.add_row("Total Return", f"{result.total_return * 100:.2f}%")
            table.add_row("Total Trades", str(result.total_trades))
            table.add_row("Winning Trades", str(result.winning_trades))
            table.add_row("Losing Trades", str(result.losing_trades))
            table.add_row("Win Rate", f"{result.win_rate:.1f}%")
            table.add_row("Largest Win", f"${result.largest_win:.2f}")
            table.add_row("Largest Loss", f"${result.largest_loss:.2f}")
            table.add_row("Final Balance", f"${result.final_balance:.2f}")
            table.add_row("Execution Time", f"{total_execution_time:.2f}s")

            # Show persistence info
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
                console.print("📈 Strategy was profitable!", style="green bold")
            elif result.total_return < 0:
                console.print("📉 Strategy lost money", style="red bold")
            else:
                console.print("➡️  Strategy broke even", style="yellow bold")

            return True

        except ValueError as e:
            console.print(f"❌ Backtest failed: {e}", style="red")
            return False
        except Exception as e:
            console.print(f"❌ Unexpected error: {e}", style="red")
            return False

    # Run async function
    result = asyncio.run(run_backtest_async())

    if not result:
        raise click.ClickException("Backtest failed")


@backtest.command("run-config")
@click.argument("config_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--symbol", "-sym", help="Trading symbol (overrides config if using database data)")
@click.option(
    "--start",
    "-st",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]),
    help="Start date for database data (overrides mock data)",
)
@click.option(
    "--end",
    "-e",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]),
    help="End date for database data (overrides mock data)",
)
@click.option(
    "--data-source",
    "-ds",
    default="catalog",
    type=click.Choice(["mock", "database", "catalog"]),
    help="Data source to use (default: catalog)",
)
@click.option(
    "--persist/--no-persist",
    default=True,
    help="Save backtest results to database (default: persist)",
)
def run_config_backtest(
    config_file: str,
    symbol: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str,
    persist: bool,
):
    """Run backtest using YAML configuration file."""

    async def run_config_backtest_async():
        nonlocal start, end
        console.print(f"🚀 Running backtest from config: {config_file}", style="cyan bold")
        console.print(f"   Data source: {data_source}")

        if data_source == "database":
            if not symbol or not start or not end:
                console.print(
                    "❌ Database data source requires --symbol, --start, and --end",
                    style="red",
                )
                return False

            # Ensure start and end dates are timezone-aware (UTC) for database comparison
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)

            console.print(f"   Symbol: {symbol.upper()}")
            console.print(
                f"   Period: {start.strftime('%Y-%m-%d %H:%M:%S')} to "
                f"{end.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # Note: Database connection check removed - using catalog for new implementation
            console.print(
                "⚠️  Database data source deprecated - use 'mock' or upgrade to catalog",
                style="yellow",
            )
        elif data_source == "mock":
            console.print("   Using mock data for testing")
        else:
            console.print("   Using Parquet catalog data")

        console.print()

        try:
            # Initialize variables for results tracking
            run_id = None
            runner = None  # Only used for mock data source

            if data_source == "mock":
                # Import MinimalBacktestRunner only when needed for mock data
                from src.core.backtest_runner import MinimalBacktestRunner

                runner = MinimalBacktestRunner(data_source=data_source)

                # Run with mock data using config file
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True,
                ) as progress:
                    task = progress.add_task("Running backtest with config...", total=None)
                    result = runner.run_from_config_file(config_file)
                    progress.update(task, completed=True)
            elif data_source == "catalog":
                # Run with catalog data using BacktestOrchestrator
                import yaml

                # Load YAML config to get instrument_id and bar_type
                with open(config_file, "r") as f:
                    yaml_data = yaml.safe_load(f)

                config_section = yaml_data.get("config", {})
                instrument_id_str = str(config_section.get("instrument_id", ""))
                bar_type_str = str(config_section.get("bar_type", ""))

                console.print(f"   Instrument: {instrument_id_str}")
                console.print(f"   Bar Type: {bar_type_str}")
                console.print(f"   Persist: {'Yes' if persist else 'No'}")

                # Initialize catalog service
                catalog_service = DataCatalogService()

                # Parse bar type to get spec for catalog lookup
                bar_type_spec = parse_bar_type_spec(bar_type_str)

                console.print(f"   Looking for: {bar_type_spec} bars")

                # Check availability
                availability = catalog_service.get_availability(instrument_id_str, bar_type_spec)
                if not availability:
                    console.print(
                        f"❌ No data found in catalog for {instrument_id_str} with {bar_type_spec}",
                        style="red",
                    )
                    console.print("   Run 'data list' to see available data")
                    return False

                avail_start = availability.start_date.date()
                avail_end = availability.end_date.date()
                console.print(f"   Available: {avail_start} to {avail_end}")

                # Load bars from catalog
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True,
                ) as progress:
                    task = progress.add_task("Loading data from catalog...", total=None)
                    bars = await catalog_service.fetch_or_load(
                        instrument_id=instrument_id_str,
                        bar_type_spec=bar_type_spec,
                        start=availability.start_date,
                        end=availability.end_date,
                    )
                    progress.update(task, completed=True)

                if not bars:
                    console.print("❌ Failed to load bars from catalog", style="red")
                    return False

                console.print(f"   Loaded {len(bars):,} bars")

                # Load instrument from catalog
                instrument = catalog_service.load_instrument(instrument_id_str)
                if not instrument:
                    console.print(
                        "⚠️  Instrument not found in catalog, creating mock instrument",
                        style="yellow",
                    )
                    console.print(
                        "   Note: Mock instrument may have different tick size/lot size",
                        style="dim",
                    )
                    # Create fallback instrument
                    from src.utils.mock_data import create_test_instrument

                    symbol_str = instrument_id_str.split(".")[0]
                    venue_str = (
                        instrument_id_str.split(".")[-1] if "." in instrument_id_str else "SIM"
                    )
                    instrument, _ = create_test_instrument(symbol_str, venue_str)

                # Build BacktestRequest from YAML config
                request = BacktestRequest.from_yaml_config(
                    yaml_data=yaml_data,
                    persist=persist,
                    config_file_path=config_file,
                )

                # Run backtest with orchestrator
                orchestrator = BacktestOrchestrator()
                run_id = None

                try:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        transient=True,
                    ) as progress:
                        task = progress.add_task(
                            "Running backtest with catalog data...", total=None
                        )
                        result, run_id = await orchestrator.execute(request, bars, instrument)
                        progress.update(task, completed=True)

                    # Show persistence status
                    if persist and run_id:
                        console.print(f"   Run ID: {run_id}", style="dim")
                finally:
                    # Ensure cleanup happens even on error
                    orchestrator.dispose()
            else:
                # Run with database data - deprecated
                console.print(
                    "❌ Database data source deprecated - use 'catalog' or 'mock'",
                    style="red",
                )
                return False

            # Display results
            console.print("🎯 Backtest Results", style="cyan bold")
            console.print()

            # Create results table
            table = Table(title="Strategy Configuration Backtest Results")
            table.add_column("Metric", style="cyan", no_wrap=True)
            table.add_column("Value", style="green")

            table.add_row("Configuration File", config_file)
            table.add_row("Data Source", data_source.title())
            table.add_row("Total Return", f"{result.total_return * 100:.2f}%")
            table.add_row("Total Trades", str(result.total_trades))
            table.add_row("Winning Trades", str(result.winning_trades))
            table.add_row("Losing Trades", str(result.losing_trades))
            table.add_row("Win Rate", f"{result.win_rate:.1f}%")
            table.add_row("Largest Win", f"${result.largest_win:.2f}")
            table.add_row("Largest Loss", f"${result.largest_loss:.2f}")
            table.add_row("Final Balance", f"${result.final_balance:.2f}")

            # Show persistence info for catalog runs
            if data_source == "catalog":
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
                console.print("📈 Strategy was profitable!", style="green bold")
            elif result.total_return < 0:
                console.print("📉 Strategy lost money", style="red bold")
            else:
                console.print("➡️  Strategy broke even", style="yellow bold")

            # Clean up runner (only used for mock data)
            if runner is not None:
                runner.dispose()
            return True

        except FileNotFoundError as e:
            console.print(f"❌ Configuration file not found: {e}", style="red bold")
            sys.exit(1)
        except ValueError as e:
            console.print(f"❌ Configuration error: {e}", style="red bold")
            sys.exit(2)
        except Exception as e:
            console.print(f"❌ Unexpected error: {e}", style="red bold")
            sys.exit(3)

    # Run async function
    result = asyncio.run(run_config_backtest_async())

    if not result:
        raise click.ClickException("Backtest failed")


@backtest.command("list")
def list_backtests():
    """List available strategies and data."""
    console.print("📊 Available Strategies", style="cyan bold")
    console.print()

    # Ensure strategies are discovered
    StrategyRegistry.discover()

    # Strategy table from registry
    strategy_table = Table(title="Supported Strategies")
    strategy_table.add_column("Name", style="cyan")
    strategy_table.add_column("Description", style="white")
    strategy_table.add_column("Aliases", style="yellow")

    for defn in StrategyRegistry.get_all().values():
        aliases = ", ".join(defn.aliases) if defn.aliases else "-"
        strategy_table.add_row(defn.name, defn.description, aliases)

    console.print(strategy_table)
    console.print()

    # Data info
    async def show_data_info():
        try:
            catalog_service = DataCatalogService()
            # Reason: Scan catalog for available data
            console.print("📈 Available Data from Catalog", style="cyan bold")
            console.print(f"   Catalog: {catalog_service.catalog_path}", style="dim")
            console.print(f"   Instruments: {len(catalog_service.availability_cache)}")

            if catalog_service.availability_cache:
                # Show first few instruments
                items = list(catalog_service.availability_cache.items())[:5]
                for key, avail in items:
                    console.print(
                        f"   • {avail.instrument_id} ({avail.bar_type_spec}): "
                        f"{avail.start_date.strftime('%Y-%m-%d')} to "
                        f"{avail.end_date.strftime('%Y-%m-%d')}"
                    )
                if len(catalog_service.availability_cache) > 5:
                    remaining = len(catalog_service.availability_cache) - 5
                    console.print(f"   ... and {remaining} more")
            else:
                console.print("⚠️  No market data in catalog", style="yellow")
                console.print(
                    "   Import data: ntrader data import-csv --file sample.csv --symbol AAPL"
                )
        except Exception as e:
            console.print(f"⚠️  Could not fetch data info: {e}", style="yellow")

    asyncio.run(show_data_info())


# Add show, compare, and reproduce commands to backtest group
backtest.add_command(show_backtest_details)
backtest.add_command(compare_backtests)
backtest.add_command(reproduce_backtest)
