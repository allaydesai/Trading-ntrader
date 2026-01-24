"""Backtest commands for running strategies with real data."""

import asyncio
import sys
import time
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.table import Table

from src.cli.commands._backtest_helpers import (
    display_backtest_results,
    execute_backtest,
    load_backtest_data,
    resolve_backtest_request,
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


def validate_strategy(ctx, param, value):
    """Validate strategy name against registry.

    Returns None for config mode (strategy comes from YAML), validates against
    registry for CLI mode.
    """
    if value is None:
        return None  # Allow None for config mode - will use YAML strategy

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
@click.argument("config_file", type=click.Path(exists=True, dir_okay=False), required=False)
@click.option(
    "--symbol",
    "-sym",
    help="Trading symbol (required in CLI mode, override in config mode)",
)
@click.option(
    "--strategy",
    "-s",
    default=None,
    callback=validate_strategy,
    help="Strategy to run (CLI mode only). Use 'backtest list' to see available strategies.",
)
@click.option(
    "--start",
    "-st",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]),
    help="Start date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
)
@click.option(
    "--end",
    "-e",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%d %H:%M:%S"]),
    help="End date (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)",
)
@click.option(
    "--data-source",
    "-ds",
    type=click.Choice(["catalog", "mock"], case_sensitive=False),
    default=None,
    help="Data source to use (default: catalog)",
)
@click.option(
    "--starting-balance",
    "-sb",
    type=float,
    default=None,
    help="Starting balance (overrides config)",
)
@click.option("--fast-period", "-f", default=None, type=int, help="Fast SMA period (default: 10)")
@click.option("--slow-period", "-sl", default=None, type=int, help="Slow SMA period (default: 20)")
@click.option(
    "--trade-size",
    "-ts",
    default=None,
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
    config_file: str | None,
    symbol: str | None,
    strategy: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str | None,
    starting_balance: float | None,
    fast_period: int | None,
    slow_period: int | None,
    trade_size: int | None,
    timeframe: str | None,
    persist: bool,
):
    """Run backtest with real market data.

    Supports two modes:

    \b
    CONFIG MODE: backtest run config.yaml [--start DATE] [--end DATE] ...
      Uses YAML config file for strategy parameters.
      CLI options override config values.

    \b
    CLI MODE: backtest run --symbol AAPL --start DATE --end DATE [--strategy NAME] ...
      All parameters from command line.
      --symbol, --start, and --end are required.

    \b
    Examples:
      backtest run configs/apolo_rsi_amd.yaml
      backtest run configs/apolo_rsi_amd.yaml --start 2024-06-01
      backtest run --symbol AAPL --start 2024-01-01 --end 2024-12-31
      backtest run --symbol AAPL --start 2024-01-01 --end 2024-12-31 --strategy sma_crossover
    """

    async def run_backtest_async():
        # Reason: Track execution time for performance reporting
        execution_start_time = time.time()

        try:
            # Resolve request based on mode (config vs CLI)
            request, resolved_data_source = resolve_backtest_request(
                config_file=config_file,
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
        except click.UsageError:
            # Re-raise UsageError to let Click handle it
            raise

        # Display mode indicator
        if config_file:
            console.print(f"🚀 Running backtest from config: {config_file}", style="cyan bold")
        else:
            console.print(
                f"🚀 Running {request.strategy_type.upper()} backtest for {request.symbol}",
                style="cyan bold",
            )

        console.print(
            f"   Period: {request.start_date.strftime('%Y-%m-%d')} to "
            f"{request.end_date.strftime('%Y-%m-%d')}"
        )
        console.print(f"   Data source: {resolved_data_source}")
        console.print(f"   Starting Balance: ${request.starting_balance:,.0f}")
        console.print()

        try:
            # Load data based on data source
            if resolved_data_source == "mock":
                # Load YAML for mock data generation
                import yaml

                with open(config_file, "r") as f:
                    yaml_data = yaml.safe_load(f)

                data_result = await load_backtest_data(
                    data_source="mock",
                    instrument_id=request.instrument_id,
                    bar_type_spec=request.bar_type,
                    start=request.start_date,
                    end=request.end_date,
                    console=console,
                    yaml_data=yaml_data,
                )
            else:
                # Load from catalog
                data_result = await load_backtest_data(
                    data_source="catalog",
                    instrument_id=request.instrument_id,
                    bar_type_spec=request.bar_type,
                    start=request.start_date,
                    end=request.end_date,
                    console=console,
                )

            bars = data_result.bars
            instrument = data_result.instrument
            data_source_used = data_result.data_source_used

        except DataNotFoundError:
            console.print()
            error_msg = format_error_with_context(
                DATA_NOT_FOUND_NO_IBKR,
                instrument=request.instrument_id,
                start_date=request.start_date.strftime("%Y-%m-%d"),
                end_date=request.end_date.strftime("%Y-%m-%d"),
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

        try:
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
            if config_file:
                context_rows = {
                    "Configuration File": config_file,
                    "Symbol": request.symbol,
                    "Period": (
                        f"{request.start_date.strftime('%Y-%m-%d')} to "
                        f"{request.end_date.strftime('%Y-%m-%d')}"
                    ),
                    "Data Source": data_source_used,
                }
                table_title = "Strategy Configuration Backtest Results"
            else:
                # CLI mode - show strategy details
                if request.strategy_type in ["sma", "sma_crossover"]:
                    fast = request.strategy_config.get("fast_period", 10)
                    slow = request.strategy_config.get("slow_period", 20)
                    strategy_description = f"SMA({fast}/{slow})"
                else:
                    strategy_description = request.strategy_type.replace("_", " ").title()

                context_rows = {
                    "Strategy": strategy_description,
                    "Symbol": request.symbol,
                    "Period": (
                        f"{request.start_date.strftime('%Y-%m-%d')} to "
                        f"{request.end_date.strftime('%Y-%m-%d')}"
                    ),
                    "Data Source": data_source_used,
                }
                strategy_name = request.strategy_type.replace("_", " ").title()
                table_title = f"{request.symbol} {strategy_name} Strategy Results"

            # Display results using helper
            display_backtest_results(
                result=result,
                console=console,
                run_id=run_id,
                persist=persist,
                context_rows=context_rows,
                table_title=table_title,
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
    try:
        result = asyncio.run(run_backtest_async())
    except click.UsageError:
        raise

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
