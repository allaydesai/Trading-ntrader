"""Backtest commands for running strategies with real data."""

import asyncio
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import click
from rich.console import Console
from rich.table import Table

from src.cli.commands._backtest_helpers import (
    display_backtest_results,
    execute_backtest,
    load_backtest_data,
)
from src.cli.commands.compare import compare_backtests
from src.cli.commands.reproduce import reproduce_backtest
from src.cli.commands.show import show_backtest_details
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
            # Convert symbol to instrument_id format (e.g., "AAPL" -> "AAPL.NASDAQ")
            if "." in symbol:
                instrument_id = symbol.upper()
            else:
                instrument_id = f"{symbol.upper()}.NASDAQ"

            # Determine bar type from explicit timeframe or auto-detect from date format
            if timeframe:
                bar_type_spec = f"{timeframe}-LAST"
            else:
                # Auto-detect: Date-only (YYYY-MM-DD) → 1-DAY, Date-time → 1-MINUTE
                if start.time().hour == 0 and start.time().minute == 0 and start.time().second == 0:
                    bar_type_spec = "1-DAY-LAST"
                else:
                    bar_type_spec = "1-MINUTE-LAST"

            # Load data from catalog with IBKR fallback
            try:
                data_result = await load_backtest_data(
                    data_source="catalog",
                    instrument_id=instrument_id,
                    bar_type_spec=bar_type_spec,
                    start=start,
                    end=end,
                    console=console,
                )
                bars = data_result.bars
                instrument = data_result.instrument
                data_source_used = data_result.data_source_used
            except DataNotFoundError:
                console.print()
                error_msg = format_error_with_context(
                    DATA_NOT_FOUND_NO_IBKR,
                    instrument=instrument_id,
                    start_date=start.strftime("%Y-%m-%d"),
                    end_date=end.strftime("%Y-%m-%d"),
                )
                error_formatter.format_error(error_msg)
                sys.exit(error_formatter.get_exit_code(error_msg))
            except IBKRConnectionError as e:
                console.print()
                error_msg = format_error_with_context(
                    IBKR_CONNECTION_FAILED,
                    connection_details=str(e),
                )
                error_formatter.format_error(error_msg)
                sys.exit(error_formatter.get_exit_code(error_msg))
            except RateLimitExceededError as e:
                console.print()
                error_msg = format_error_with_context(
                    RATE_LIMIT_EXCEEDED,
                    retry_after=str(e.retry_after),
                    request_count=str(e.request_count or "unknown"),
                )
                error_formatter.format_error(error_msg)
                sys.exit(error_formatter.get_exit_code(error_msg))
            except CatalogCorruptionError as e:
                console.print()
                error_msg = format_error_with_context(
                    CATALOG_CORRUPTION_DETECTED,
                    file_path=str(e),
                )
                error_formatter.format_error(error_msg)
                sys.exit(error_formatter.get_exit_code(error_msg))
            except CatalogError as e:
                console.print()
                error_formatter.print_warning(
                    f"Catalog error: {str(e)}",
                    "Check logs for more details",
                )
                sys.exit(4)

            # Prepare strategy parameters
            portfolio_value = Decimal(str(trade_size * PORTFOLIO_SIZE_MULTIPLIER))
            if strategy in ["sma", "sma_crossover", "sma_crossover_long_only"]:
                strategy_params = {
                    "portfolio_value": portfolio_value,
                    "position_size_pct": DEFAULT_POSITION_SIZE_PCT,
                    "fast_period": fast_period,
                    "slow_period": slow_period,
                }
            elif strategy == "bollinger_reversal":
                strategy_params = {"portfolio_value": portfolio_value}
            else:
                strategy_params = {"trade_size": trade_size}

            # Build BacktestRequest from CLI args
            request = BacktestRequest.from_cli_args(
                strategy=strategy,
                symbol=symbol.upper(),
                start=start,
                end=end,
                bar_type_spec=bar_type_spec,
                persist=persist,
                starting_balance=portfolio_value,
                **strategy_params,
            )

            # Execute backtest with orchestrator
            result, run_id = await execute_backtest(
                request=request,
                bars=bars,
                instrument=instrument,
                console=console,
                progress_message="Running backtest...",
            )

            # Calculate total execution time
            total_execution_time = time.time() - execution_start_time

            # Build context rows for display
            if strategy in ["sma", "sma_crossover"]:
                strategy_description = f"SMA({fast_period}/{slow_period})"
            else:
                strategy_description = display_strategy.replace("_", " ").title()

            strategy_name = display_strategy.replace("_", " ").title()
            context_rows = {
                "Strategy": strategy_description,
                "Symbol": symbol.upper(),
                "Period": f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}",
                "Data Source": data_source_used,
            }

            # Display results using helper
            display_backtest_results(
                result=result,
                console=console,
                run_id=run_id,
                persist=persist,
                context_rows=context_rows,
                table_title=f"{symbol.upper()} {strategy_name} Strategy Results",
                execution_time=total_execution_time,
            )

            return True

        except ValueError as e:
            console.print(f"Backtest failed: {e}", style="red")
            return False
        except Exception as e:
            console.print(f"Unexpected error: {e}", style="red")
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
            import yaml

            # Load YAML config
            with open(config_file, "r") as f:
                yaml_data = yaml.safe_load(f)

            config_section = yaml_data.get("config", {})
            instrument_id_str = str(config_section.get("instrument_id", ""))
            bar_type_str = str(config_section.get("bar_type", ""))

            if data_source == "mock":
                # Load mock data using helper
                data_result = await load_backtest_data(
                    data_source="mock",
                    instrument_id=instrument_id_str,
                    bar_type_spec=bar_type_str,
                    start=datetime.now(timezone.utc),
                    end=datetime.now(timezone.utc),
                    console=console,
                    yaml_data=yaml_data,
                )
                bars = data_result.bars
                instrument = data_result.instrument

                # Build BacktestRequest with mock dates
                request = BacktestRequest.from_yaml_config(
                    yaml_data=yaml_data,
                    persist=persist,
                    config_file_path=config_file,
                    data_source="mock",
                )
                # Get dates from mock bars
                if bars:
                    from datetime import datetime as dt

                    mock_start = dt.fromtimestamp(bars[0].ts_event / 1_000_000_000)
                    mock_end = dt.fromtimestamp(bars[-1].ts_event / 1_000_000_000)
                    request = request.model_copy(
                        update={"start_date": mock_start, "end_date": mock_end}
                    )

                console.print(f"   Persist: {'Yes' if persist else 'No'}")

            elif data_source == "catalog":
                # Build BacktestRequest FIRST to get dates from YAML config
                request = BacktestRequest.from_yaml_config(
                    yaml_data=yaml_data,
                    persist=persist,
                    config_file_path=config_file,
                )

                console.print(f"   Instrument: {instrument_id_str}")
                console.print(f"   Bar Type: {bar_type_str}")
                console.print(
                    f"   Period: {request.start_date.strftime('%Y-%m-%d')} to "
                    f"{request.end_date.strftime('%Y-%m-%d')}"
                )
                console.print(f"   Starting Balance: ${request.starting_balance:,.0f}")
                console.print(f"   Persist: {'Yes' if persist else 'No'}")

                # Parse bar type to get spec for catalog lookup
                bar_type_spec = parse_bar_type_spec(bar_type_str)
                console.print(f"   Looking for: {bar_type_spec} bars")

                # Load catalog data using helper
                data_result = await load_backtest_data(
                    data_source="catalog",
                    instrument_id=instrument_id_str,
                    bar_type_spec=bar_type_spec,
                    start=request.start_date,
                    end=request.end_date,
                    console=console,
                )
                bars = data_result.bars
                instrument = data_result.instrument

                if not bars:
                    console.print("Failed to load bars from catalog", style="red")
                    return False

            else:
                # Database data source - deprecated
                console.print(
                    "Database data source deprecated - use 'catalog' or 'mock'",
                    style="red",
                )
                return False

            # Execute backtest using helper
            progress_msg = f"Running backtest with {data_source} data..."
            result, run_id = await execute_backtest(
                request=request,
                bars=bars,
                instrument=instrument,
                console=console,
                progress_message=progress_msg,
            )

            # Build context rows for display
            context_rows = {
                "Configuration File": config_file,
                "Data Source": data_source.title(),
            }

            # Display results using helper
            display_backtest_results(
                result=result,
                console=console,
                run_id=run_id,
                persist=persist,
                context_rows=context_rows,
                table_title="Strategy Configuration Backtest Results",
            )

            return True

        except FileNotFoundError as e:
            console.print(f"Configuration file not found: {e}", style="red bold")
            sys.exit(1)
        except ValueError as e:
            console.print(f"Configuration error: {e}", style="red bold")
            sys.exit(2)
        except Exception as e:
            console.print(f"Unexpected error: {e}", style="red bold")
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
