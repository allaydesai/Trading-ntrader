"""Backtest commands for running strategies with real data."""

import asyncio
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.core.backtest_runner import MinimalBacktestRunner
from src.services.data_service import DataService
from src.db.session import test_connection

console = Console()


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
    help="Trade size in base currency units (default: 1,000,000)",
)
def run_backtest(
    strategy: str,
    symbol: str,
    start: datetime,
    end: datetime,
    fast_period: int,
    slow_period: int,
    trade_size: int,
):
    """Run backtest with real market data from database."""

    async def run_backtest_async():
        # Ensure start and end dates are timezone-aware (UTC) for database comparison
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

        # Check database connection
        if not await test_connection():
            console.print(
                "❌ Database not accessible. Please check your database configuration.",
                style="red",
            )
            console.print(
                "   Make sure PostgreSQL is running and DATABASE_URL is configured."
            )
            return False

        try:
            # Get adjusted date range if needed
            data_service = DataService()

            # Validate data availability first (this handles all validation scenarios)
            validation = await data_service.validate_data_availability(
                symbol.upper(), start, end
            )

            if not validation["valid"]:
                console.print("❌ Data validation failed", style="red")
                console.print(f"   {validation['reason']}")

                if (
                    "available_symbols" in validation
                    and validation["available_symbols"]
                ):
                    symbols = validation["available_symbols"]
                    console.print(f"   Available symbols: {', '.join(symbols[:10])}")
                    if len(symbols) > 10:
                        console.print(f"   ... and {len(symbols) - 10} more")
                elif (
                    "available_symbols" in validation
                    and not validation["available_symbols"]
                ):
                    console.print(
                        "   No data available in database. Try importing some CSV data first:"
                    )
                    console.print(
                        "   ntrader data import-csv --file data/sample_AAPL.csv --symbol AAPL"
                    )

                if "available_range" in validation:
                    range_info = validation["available_range"]
                    console.print(
                        f"   Available range: {range_info['start']} to {range_info['end']}"
                    )

                return False

            # Get adjusted date range (handles date-only inputs intelligently)
            adjusted_range = await data_service.get_adjusted_date_range(
                symbol.upper(), start, end
            )

            if not adjusted_range:
                console.print(
                    f"❌ No data available for {symbol.upper()} in the specified date range",
                    style="red",
                )
                # Show available range
                data_range = await data_service.get_data_range(symbol.upper())
                if data_range:
                    console.print(
                        f"   Available range: {data_range['start']} to {data_range['end']}"
                    )
                return False

            # Check if dates were adjusted
            dates_adjusted = (adjusted_range["start"] != start) or (
                adjusted_range["end"] != end
            )

            # Use adjusted dates for validation and backtest
            adjusted_start = adjusted_range["start"]
            adjusted_end = adjusted_range["end"]

            # Show data info
            console.print("✅ Data validation passed", style="green")

            # If dates were adjusted, show both original request and actual range
            if dates_adjusted:
                console.print(
                    f"   Requested period: {start.strftime('%Y-%m-%d %H:%M:%S')} to {end.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                console.print(
                    f"   Adjusted to actual data: {adjusted_start.strftime('%Y-%m-%d %H:%M:%S')} to {adjusted_end.strftime('%Y-%m-%d %H:%M:%S')}",
                    style="yellow",
                )
            else:
                console.print(
                    f"   Using exact period: {adjusted_start.strftime('%Y-%m-%d %H:%M:%S')} to {adjusted_end.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            console.print()

            # Run backtest with progress indicator
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Running backtest...", total=None)

                # Initialize backtest runner with database data source
                runner = MinimalBacktestRunner(data_source="database")

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

                # Run the backtest with adjusted dates using new dynamic method
                result = await runner.run_backtest_with_strategy_type(
                    strategy_type=strategy,
                    symbol=symbol.upper(),
                    start=adjusted_start,
                    end=adjusted_end,
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

            table.add_row("Strategy", strategy_description)
            table.add_row("Symbol", symbol.upper())
            table.add_row(
                "Period", f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}"
            )
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

            # Check database connection
            if not await test_connection():
                console.print(
                    "❌ Database not accessible. Please check your database configuration.",
                    style="red",
                )
                return False
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
        if await test_connection():
            try:
                data_service = DataService()
                symbols = await data_service.get_available_symbols()

                if symbols:
                    console.print("📈 Available Data", style="cyan bold")
                    console.print(f"   Symbols: {', '.join(symbols[:10])}")
                    if len(symbols) > 10:
                        console.print(f"   ... and {len(symbols) - 10} more")

                    # Show sample data range
                    sample_symbol = symbols[0]
                    range_info = await data_service.get_data_range(sample_symbol)
                    if range_info:
                        console.print(
                            f"   Sample range ({sample_symbol}): {range_info['start']} to {range_info['end']}"
                        )
                else:
                    console.print("⚠️  No market data available", style="yellow")
                    console.print(
                        "   Import some data first: ntrader data import-csv --file sample.csv --symbol AAPL"
                    )
            except Exception as e:
                console.print(f"⚠️  Could not fetch data info: {e}", style="yellow")
        else:
            console.print("⚠️  Database not accessible", style="yellow")

    asyncio.run(show_data_info())
