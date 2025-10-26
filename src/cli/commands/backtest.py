"""Backtest commands for running strategies with real data."""

import asyncio
import sys
import time
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.core.backtest_runner import MinimalBacktestRunner
from src.services.data_catalog import DataCatalogService
from src.services.exceptions import (
    CatalogCorruptionError,
    CatalogError,
    DataNotFoundError,
    IBKRConnectionError,
    RateLimitExceededError,
)
from src.utils.error_formatter import ErrorFormatter
from src.utils.error_messages import (
    CATALOG_CORRUPTION_DETECTED,
    DATA_NOT_FOUND_NO_IBKR,
    IBKR_CONNECTION_FAILED,
    RATE_LIMIT_EXCEEDED,
    format_error_with_context,
)
from src.cli.commands.show import show_backtest_details
from src.cli.commands.compare import compare_backtests

console = Console()
error_formatter = ErrorFormatter(console)


@click.group()
def backtest():
    """Backtest commands for running strategies with real data."""
    pass


@backtest.command("run")
@click.option(
    "--strategy",
    "-s",
    default="sma_crossover",
    type=click.Choice(["sma", "sma_crossover", "mean_reversion", "momentum"]),
    help="Strategy to run (sma is alias for sma_crossover)",
)
@click.option(
    "--symbol", "-sym", required=True, help="Trading symbol (e.g., AAPL, EUR/USD)"
)
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
@click.option(
    "--fast-period", "-f", default=10, type=int, help="Fast SMA period (default: 10)"
)
@click.option(
    "--slow-period", "-sl", default=20, type=int, help="Slow SMA period (default: 20)"
)
@click.option(
    "--trade-size",
    "-ts",
    default=1000000,
    type=int,
    help="Trade size in SHARES (default: 1,000,000 shares). Note: 1M shares @ $180 = $180M notional",
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
def run_backtest(
    strategy: str,
    symbol: str,
    start: datetime,
    end: datetime,
    fast_period: int,
    slow_period: int,
    trade_size: int,
    timeframe: str | None,
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
        console.print(
            f"   Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
        )

        # Display strategy-specific parameters
        if strategy in ["sma", "sma_crossover"]:
            console.print(
                f"   Strategy: Fast SMA({fast_period}) vs Slow SMA({slow_period})"
            )
        else:
            console.print(f"   Strategy: {display_strategy.replace('_', ' ').title()}")

        console.print(f"   Trade Size: {trade_size:,}")
        console.print()

        try:
            # Reason: Initialize catalog service for data access
            catalog_service = DataCatalogService()

            # Reason: Convert symbol to instrument_id format (e.g., "AAPL" -> "AAPL.NASDAQ")
            # For now, assume NASDAQ venue for US equities
            # TODO: Make venue configurable or derive from symbol
            instrument_id = f"{symbol.upper()}.NASDAQ"

            # Reason: Determine bar type from explicit timeframe or auto-detect from date format
            if timeframe:
                # Use explicit timeframe if provided
                bar_type_spec = f"{timeframe}-LAST"
            else:
                # Auto-detect: Date-only (YYYY-MM-DD) → 1-DAY, Date-time → 1-MINUTE
                # Click parses date-only as midnight UTC (00:00:00)
                if (
                    start.time().hour == 0
                    and start.time().minute == 0
                    and start.time().second == 0
                ):
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
                availability = catalog_service.get_availability(
                    instrument_id, bar_type_spec
                )
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
                    f"   Available: {availability.start_date.strftime('%Y-%m-%d')} to {availability.end_date.strftime('%Y-%m-%d')}"
                )
                console.print()
                console.print(
                    "   Will attempt to fetch missing data from IBKR...", style="yellow"
                )
                console.print()
                data_source = "IBKR Auto-fetch"
            else:
                console.print("✅ Data available in catalog", style="green")
                console.print(
                    f"   Period: {adjusted_start.strftime('%Y-%m-%d')} to {adjusted_end.strftime('%Y-%m-%d')}"
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
                            instrument = (
                                await catalog_service.fetch_instrument_from_ibkr(
                                    instrument_id
                                )
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

            # Reason: Run backtest with catalog data
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Running backtest...", total=None)

                # Reason: Initialize backtest runner with catalog data
                # Note: Using "catalog" data source (will need to update runner)
                runner = MinimalBacktestRunner(data_source="catalog")

                # Prepare strategy parameters
                strategy_params = {
                    "trade_size": trade_size,
                }

                # Add strategy-specific parameters
                if strategy in ["sma", "sma_crossover"]:
                    strategy_params.update(
                        {
                            "fast_period": fast_period,
                            "slow_period": slow_period,
                        }
                    )

                # Reason: Run the backtest with catalog bars and instrument
                result = await runner.run_backtest_with_catalog_data(
                    bars=bars,
                    strategy_type=strategy,
                    symbol=symbol.upper(),
                    start=adjusted_start,
                    end=adjusted_end,
                    instrument=instrument,
                    **strategy_params,
                )

                progress.update(task, completed=True)

            # Display results
            console.print("🎯 Backtest Results", style="cyan bold")
            console.print()

            # Create results table
            table = Table(
                title=f"{symbol.upper()} {display_strategy.replace('_', ' ').title()} Strategy Results"
            )
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
            table.add_row(
                "Period", f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
            )
            table.add_row("Data Source", "Parquet Catalog")
            table.add_row("Total Return", f"${result.total_return:.2f}")
            table.add_row("Total Trades", str(result.total_trades))
            table.add_row("Winning Trades", str(result.winning_trades))
            table.add_row("Losing Trades", str(result.losing_trades))
            table.add_row("Win Rate", f"{result.win_rate:.1f}%")
            table.add_row("Largest Win", f"${result.largest_win:.2f}")
            table.add_row("Largest Loss", f"${result.largest_loss:.2f}")
            table.add_row("Final Balance", f"${result.final_balance:.2f}")
            table.add_row("Execution Time", f"{total_execution_time:.2f}s")

            console.print(table)
            console.print()

            # Performance summary
            if result.total_return > 0:
                console.print("📈 Strategy was profitable!", style="green bold")
            elif result.total_return < 0:
                console.print("📉 Strategy lost money", style="red bold")
            else:
                console.print("➡️  Strategy broke even", style="yellow bold")

            # Clean up
            runner.dispose()
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
@click.option(
    "--symbol", "-sym", help="Trading symbol (overrides config if using database data)"
)
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
    default="mock",
    type=click.Choice(["mock", "database"]),
    help="Data source to use (default: mock)",
)
def run_config_backtest(
    config_file: str,
    symbol: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str,
):
    """Run backtest using YAML configuration file."""

    async def run_config_backtest_async():
        nonlocal start, end
        console.print(
            f"🚀 Running backtest from config: {config_file}", style="cyan bold"
        )
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
                f"   Period: {start.strftime('%Y-%m-%d %H:%M:%S')} to {end.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # Note: Database connection check removed - using catalog for new implementation
            console.print(
                "⚠️  Database data source deprecated - use 'mock' or upgrade to catalog",
                style="yellow",
            )
        else:
            console.print("   Using mock data for testing")

        console.print()

        try:
            # Initialize backtest runner with specified data source
            runner = MinimalBacktestRunner(data_source=data_source)

            if data_source == "mock":
                # Run with mock data using config file
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True,
                ) as progress:
                    task = progress.add_task(
                        "Running backtest with config...", total=None
                    )
                    result = runner.run_from_config_file(config_file)
                    progress.update(task, completed=True)
            else:
                # Run with database data - need to extend the runner for this
                console.print(
                    "❌ Database data source with config files not yet implemented",
                    style="red",
                )
                console.print(
                    "   Use: backtest run --strategy <type> --symbol <symbol> for database backtests"
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
            table.add_row("Total Return", f"${result.total_return:.2f}")
            table.add_row("Total Trades", str(result.total_trades))
            table.add_row("Winning Trades", str(result.winning_trades))
            table.add_row("Losing Trades", str(result.losing_trades))
            table.add_row("Win Rate", f"{result.win_rate:.1f}%")
            table.add_row("Largest Win", f"${result.largest_win:.2f}")
            table.add_row("Largest Loss", f"${result.largest_loss:.2f}")
            table.add_row("Final Balance", f"${result.final_balance:.2f}")

            console.print(table)
            console.print()

            # Performance summary
            if result.total_return > 0:
                console.print("📈 Strategy was profitable!", style="green bold")
            elif result.total_return < 0:
                console.print("📉 Strategy lost money", style="red bold")
            else:
                console.print("➡️  Strategy broke even", style="yellow bold")

            # Clean up
            runner.dispose()
            return True

        except FileNotFoundError as e:
            console.print(f"❌ Configuration file not found: {e}", style="red")
            return False
        except ValueError as e:
            console.print(f"❌ Configuration error: {e}", style="red")
            return False
        except Exception as e:
            console.print(f"❌ Unexpected error: {e}", style="red")
            return False

    # Run async function
    result = asyncio.run(run_config_backtest_async())

    if not result:
        raise click.ClickException("Backtest failed")


@backtest.command("list")
def list_backtests():
    """List available strategies and data."""
    console.print("📊 Available Strategies", style="cyan bold")
    console.print()

    # Strategy table
    strategy_table = Table(title="Supported Strategies")
    strategy_table.add_column("Name", style="cyan")
    strategy_table.add_column("Description", style="white")
    strategy_table.add_column("Parameters", style="yellow")

    strategy_table.add_row(
        "sma_crossover",
        "Simple Moving Average Crossover",
        "fast_period, slow_period, trade_size",
    )
    strategy_table.add_row(
        "mean_reversion",
        "RSI Mean Reversion with Trend Filter",
        "trade_size, rsi_period, rsi_buy_threshold",
    )
    strategy_table.add_row(
        "momentum",
        "SMA Momentum Strategy (Golden/Death Cross)",
        "trade_size, fast_period, slow_period",
    )
    strategy_table.add_row(
        "sma",
        "Alias for sma_crossover (backward compatibility)",
        "fast_period, slow_period, trade_size",
    )

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
                        f"{avail.start_date.strftime('%Y-%m-%d')} to {avail.end_date.strftime('%Y-%m-%d')}"
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


# Add show and compare commands to backtest group
backtest.add_command(show_backtest_details)
backtest.add_command(compare_backtests)
