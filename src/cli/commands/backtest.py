"""Backtest commands for running strategies with real data."""

import asyncio
from datetime import datetime
from decimal import Decimal

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
    "--strategy", "-s",
    default="sma",
    type=click.Choice(["sma"]),
    help="Strategy to run (currently only SMA available)"
)
@click.option(
    "--symbol", "-sym",
    required=True,
    help="Trading symbol (e.g., AAPL, EUR/USD)"
)
@click.option(
    "--start", "-st",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]),
    help="Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)"
)
@click.option(
    "--end", "-e",
    required=True,
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]),
    help="End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)"
)
@click.option(
    "--fast-period", "-f",
    default=10,
    type=int,
    help="Fast SMA period (default: 10)"
)
@click.option(
    "--slow-period", "-sl",
    default=20,
    type=int,
    help="Slow SMA period (default: 20)"
)
@click.option(
    "--trade-size", "-ts",
    default=1000000,
    type=int,
    help="Trade size in base currency units (default: 1,000,000)"
)
def run_backtest(
    strategy: str,
    symbol: str,
    start: datetime,
    end: datetime,
    fast_period: int,
    slow_period: int,
    trade_size: int
):
    """Run backtest with real market data from database."""
    async def run_backtest_async():
        console.print(f"🚀 Running {strategy.upper()} backtest for {symbol.upper()}", style="cyan bold")
        console.print(f"   Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
        console.print(f"   Strategy: Fast SMA({fast_period}) vs Slow SMA({slow_period})")
        console.print(f"   Trade Size: {trade_size:,}")
        console.print()

        # Check database connection
        if not await test_connection():
            console.print("❌ Database not accessible. Please check your database configuration.", style="red")
            console.print("   Make sure PostgreSQL is running and DATABASE_URL is configured.")
            return False

        try:
            # Validate data availability first
            data_service = DataService()
            validation = await data_service.validate_data_availability(symbol.upper(), start, end)

            if not validation['valid']:
                console.print(f"❌ Data validation failed: {validation['reason']}", style="red")

                if 'available_symbols' in validation:
                    available = validation['available_symbols']
                    if available:
                        console.print(f"   Available symbols: {', '.join(available[:10])}")
                        if len(available) > 10:
                            console.print(f"   ... and {len(available) - 10} more")
                    else:
                        console.print("   No data available in database. Try importing some CSV data first:")
                        console.print("   ntrader data import-csv --file sample.csv --symbol AAPL")

                if 'available_range' in validation:
                    range_info = validation['available_range']
                    console.print(f"   Available range: {range_info['start']} to {range_info['end']}")

                return False

            # Show data info
            console.print("✅ Data validation passed", style="green")
            range_info = validation['available_range']
            console.print(f"   Available data range: {range_info['start']} to {range_info['end']}")
            console.print()

            # Run backtest with progress indicator
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Running backtest...", total=None)

                # Initialize backtest runner with database data source
                runner = MinimalBacktestRunner(data_source='database')

                # Run the backtest
                result = await runner.run_backtest_with_database(
                    symbol=symbol.upper(),
                    start=start,
                    end=end,
                    fast_period=fast_period,
                    slow_period=slow_period,
                    trade_size=Decimal(str(trade_size))
                )

                progress.update(task, completed=True)

            # Display results
            console.print("🎯 Backtest Results", style="cyan bold")
            console.print()

            # Create results table
            table = Table(title=f"{symbol.upper()} SMA Crossover Strategy Results")
            table.add_column("Metric", style="cyan", no_wrap=True)
            table.add_column("Value", style="green")

            table.add_row("Strategy", f"SMA({fast_period}/{slow_period})")
            table.add_row("Symbol", symbol.upper())
            table.add_row("Period", f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
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
        "sma",
        "Simple Moving Average Crossover",
        "fast_period, slow_period, trade_size"
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
                        console.print(f"   Sample range ({sample_symbol}): {range_info['start']} to {range_info['end']}")
                else:
                    console.print("⚠️  No market data available", style="yellow")
                    console.print("   Import some data first: ntrader data import-csv --file sample.csv --symbol AAPL")
            except Exception as e:
                console.print(f"⚠️  Could not fetch data info: {e}", style="yellow")
        else:
            console.print("⚠️  Database not accessible", style="yellow")

    asyncio.run(show_data_info())