"""Backtest commands for running strategies with real data."""

import asyncio
import sys
import time
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table

from src.cli.commands._backtest_helpers import (
    display_backtest_results,
    execute_backtest,
    execute_multi_backtest,
    load_backtest_data,
    load_many_backtest_data,
    resolve_backtest_request,
)
from src.cli.commands.compare import compare_backtests
from src.cli.commands.reproduce import reproduce_backtest
from src.cli.commands.show import show_backtest_details
from src.core.strategy_registry import StrategyRegistry
from src.services.data_catalog import DataCatalogService
from src.services.exceptions import (
    CatalogCorruptionError,
    CatalogError,
    DataNotFoundError,
    IBKRConnectionError,
    KrakenConnectionError,
    RateLimitExceededError,
    UnknownCatalogError,
)
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
    type=click.Choice(["catalog", "ibkr", "kraken", "mock"], case_sensitive=False),
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
    "--catalog",
    "catalog_name",
    default=None,
    type=str,
    help=(
        "Named FirstRate catalog (e.g., 'e2e-test'). Overrides the default "
        "NAUTILUS_PATH catalog and resolves ticker via the import DB."
    ),
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
    catalog_name: str | None,
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
                catalog_name=catalog_name,
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
            elif resolved_data_source == "kraken":
                data_result = await load_backtest_data(
                    data_source="kraken",
                    instrument_id=request.instrument_id,
                    bar_type_spec=request.bar_type,
                    start=request.start_date,
                    end=request.end_date,
                    console=console,
                )
            else:
                # Load from catalog (or ibkr via catalog auto-fetch)
                data_result = await load_backtest_data(
                    data_source="catalog",
                    instrument_id=request.instrument_id,
                    bar_type_spec=request.bar_type,
                    start=request.start_date,
                    end=request.end_date,
                    console=console,
                    catalog_name=request.catalog_name,
                )

            bars = data_result.bars
            instrument = data_result.instrument
            data_source_used = data_result.data_source_used

        except DataNotFoundError as e:
            console.print()
            # Story 3.3 AC #10: surface the exception's specific message
            # (e.g., "Ticker 'X' not found in catalog 'Y'..." or
            # "No bars... metadata covers 2010 → 2024.") so users can
            # distinguish missing-ticker vs empty-window cases.
            console.print(str(e), style="red")
            error_msg = format_error_with_context(
                DATA_NOT_FOUND_NO_IBKR,
                instrument=request.instrument_id,
                start_date=request.start_date.strftime("%Y-%m-%d"),
                end_date=request.end_date.strftime("%Y-%m-%d"),
            )
            error_formatter.format_error(error_msg)
            sys.exit(error_formatter.get_exit_code(error_msg))
        except UnknownCatalogError as e:
            # Story 3.3 AC #10: surface as a Click usage error (exit 2).
            raise click.UsageError(str(e)) from e
        except IBKRConnectionError as e:
            console.print()
            error_msg = format_error_with_context(
                IBKR_CONNECTION_FAILED,
                connection_details=str(e),
            )
            error_formatter.format_error(error_msg)
            sys.exit(error_formatter.get_exit_code(error_msg))
        except KrakenConnectionError as e:
            console.print()
            error_formatter.print_warning(
                f"Kraken connection failed: {str(e)}",
                "Check your network connection and Kraken API availability",
            )
            sys.exit(3)
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
                    "Catalog": request.catalog_name or "(default)",
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
                    "Catalog": request.catalog_name or "(default)",
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
            # Named-catalog resolution raises ValueError with the
            # "Unknown catalog" prefix when the catalog directory is missing;
            # surface as a usage error (exit code 2) per AC #6.
            msg = str(e)
            if msg.startswith("Unknown catalog"):
                raise click.UsageError(msg) from e
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


@backtest.command("run-multi")
@click.option(
    "--symbols",
    "-sym",
    required=True,
    help="Comma-separated ETF symbols, e.g. 'SPY,IVV,TQQQ' (at least two).",
)
@click.option(
    "--strategy",
    "-s",
    default="sma_crossover",
    callback=validate_strategy,
    help="Strategy to run (applied to every instrument). Default: sma_crossover.",
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
    "--catalog",
    "catalog_name",
    required=True,
    type=str,
    help="Named FirstRate catalog holding the ETFs (multi-ETF requires a named catalog).",
)
@click.option(
    "--starting-balance", "-sb", type=float, default=None, help="Starting balance per venue."
)
@click.option("--fast-period", "-f", default=None, type=int, help="Fast SMA period (default: 10)")
@click.option("--slow-period", "-sl", default=None, type=int, help="Slow SMA period (default: 20)")
@click.option(
    "--timeframe",
    "-t",
    default="1-DAY",
    type=click.Choice(
        ["1-MINUTE", "5-MINUTE", "15-MINUTE", "30-MINUTE", "1-HOUR", "4-HOUR", "1-DAY", "1-WEEK"],
        case_sensitive=False,
    ),
    help="Bar timeframe applied to every instrument (default: 1-DAY).",
)
def run_multi_backtest(
    symbols: str,
    strategy: str,
    start: datetime,
    end: datetime,
    catalog_name: str,
    starting_balance: float | None,
    fast_period: int | None,
    slow_period: int | None,
    timeframe: str,
):
    """Run ONE backtest spanning MULTIPLE ETFs (Story 5.3, multi-ETF).

    Routes several venue-qualified ETFs through the existing BacktestOrchestrator
    in a single engine run — each instrument keeps its own venue and whole-share
    sizing (no cross-instrument venue bleed). Results are NOT persisted yet;
    database persistence for multi-ETF runs lands in Story 5.4.

    \b
    Example:
      backtest run-multi --symbols IVV,TQQQ --catalog etf-full \\
          --start 2018-01-01 --end 2018-12-31 --strategy sma_crossover
    """
    from decimal import Decimal

    async def run_multi_async():
        tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if len(tickers) < 2:
            raise click.UsageError(
                "run-multi needs at least two --symbols (comma-separated). "
                "For a single instrument use 'backtest run'."
            )
        duplicates = sorted({t for t in tickers if tickers.count(t) > 1})
        if duplicates:
            raise click.UsageError(
                f"Duplicate --symbols not allowed: {', '.join(duplicates)}. "
                "Each ETF may appear at most once."
            )

        strategy_params: dict[str, int] = {}
        if fast_period is not None:
            strategy_params["fast_period"] = fast_period
        if slow_period is not None:
            strategy_params["slow_period"] = slow_period

        from src.models.backtest_request import BacktestRequest

        request = BacktestRequest.from_cli_args(
            strategy=strategy,
            symbol=tickers[0],  # representative — the instrument list is passed to execute_multi
            start=start,
            end=end,
            bar_type_spec=f"{timeframe.upper()}-LAST",
            persist=False,  # multi-ETF persistence lands in Story 5.4
            starting_balance=Decimal(str(starting_balance))
            if starting_balance is not None
            else Decimal("1000000"),
            data_source="catalog",
            catalog_name=catalog_name,
            **strategy_params,
        )

        console.print(
            f"🚀 Running {strategy.upper()} multi-ETF backtest for {', '.join(tickers)}",
            style="cyan bold",
        )
        console.print(
            f"   Period: {request.start_date.strftime('%Y-%m-%d')} to "
            f"{request.end_date.strftime('%Y-%m-%d')}"
        )
        console.print(f"   Catalog: {catalog_name}")
        console.print(
            "ℹ️  Multi-ETF results are not persisted yet — coming in Story 5.4.", style="yellow"
        )
        console.print()

        try:
            load_results = await load_many_backtest_data(
                catalog_name=catalog_name,
                tickers=tickers,
                bar_type_spec=request.bar_type,
                start=request.start_date,
                end=request.end_date,
                console=console,
            )
        except DataNotFoundError as e:
            console.print(str(e), style="red")
            sys.exit(1)
        except UnknownCatalogError as e:
            raise click.UsageError(str(e)) from e

        instruments_data = [(r.bars, r.instrument) for r in load_results]

        try:
            result, run_id = await execute_multi_backtest(
                request=request,
                instruments_data=instruments_data,
                console=console,
            )
        except ValueError as e:
            console.print(f"Multi-ETF backtest failed: {e}", style="red")
            return False

        strategy_name = strategy.replace("_", " ").title()
        display_backtest_results(
            result=result,
            console=console,
            run_id=run_id,
            persist=False,
            context_rows={
                "Strategy": strategy_name,
                "Catalog": catalog_name,
                "Symbols": ", ".join(tickers),
                "Instruments": str(len(tickers)),
                "Period": (
                    f"{request.start_date.strftime('%Y-%m-%d')} to "
                    f"{request.end_date.strftime('%Y-%m-%d')}"
                ),
            },
            table_title="Multi-ETF Backtest Results",
        )
        return True

    try:
        ok = asyncio.run(run_multi_async())
    except click.UsageError:
        raise

    if not ok:
        raise click.ClickException("Multi-ETF backtest failed")


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
