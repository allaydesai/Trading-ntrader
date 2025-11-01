"""Reproduce backtest command for re-running previous backtests."""

import asyncio
import sys
import time
from uuid import UUID

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.db.session import get_session
from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_query import BacktestQueryService
from src.core.backtest_runner import MinimalBacktestRunner
from src.services.exceptions import DataNotFoundError

console = Console()


@click.command(name="reproduce")
@click.argument("run_id", type=str)
def reproduce_backtest(run_id: str):
    """
    Reproduce a previous backtest with its exact same configuration.

    Re-runs a backtest using the same strategy, parameters, and date range
    as a previous run. The new backtest creates a separate record linked
    to the original via reproduced_from_run_id.

    Arguments:
        run_id: UUID of the original backtest to reproduce

    Example:
        ntrader backtest reproduce a1b2c3d4-e5f6-7890-abcd-ef1234567890
    """
    asyncio.run(_reproduce_backtest_async(run_id))


async def _reproduce_backtest_async(run_id_str: str):
    """
    Async implementation of reproduce backtest command.

    Args:
        run_id_str: String representation of run_id UUID
    """
    try:
        # Step 1: Validate and parse UUID
        try:
            original_run_id = UUID(run_id_str)
        except ValueError:
            console.print(
                f"[red]❌ Invalid UUID format: {run_id_str}[/red]\n"
                "[yellow]💡 Run IDs should be in UUID format:[/yellow]\n"
                "   Example: a1b2c3d4-e5f6-7890-abcd-ef1234567890\n"
                "[dim]Use 'ntrader backtest history' to see available run IDs[/dim]"
            )
            sys.exit(1)

        # Step 2: Retrieve original backtest
        console.print(
            f"[cyan]🔍 Retrieving original backtest...[/cyan] [dim]{str(original_run_id)[:8]}...[/dim]"
        )

        async with get_session() as session:
            repository = BacktestRepository(session)
            query_service = BacktestQueryService(repository)

            original_backtest = await query_service.get_backtest_by_id(original_run_id)

        # Step 3: Handle not found
        if original_backtest is None:
            console.print(
                f"[red]❌ Backtest not found: {str(original_run_id)[:12]}...[/red]\n"
                "[yellow]💡 The specified backtest does not exist in the database.[/yellow]\n"
                "[dim]Use 'ntrader backtest history' to see available backtests[/dim]"
            )
            sys.exit(1)

        # Step 4: Extract configuration
        config_snapshot = original_backtest.config_snapshot
        strategy_type = original_backtest.strategy_type
        symbol = original_backtest.instrument_symbol
        start_date = original_backtest.start_date
        end_date = original_backtest.end_date
        strategy_params = config_snapshot.get("config", {})

        # Display original backtest info
        console.print(
            Panel.fit(
                f"[bold cyan]Original Backtest[/bold cyan]\n"
                f"Run ID: [yellow]{str(original_run_id)[:12]}...[/yellow]\n"
                f"Strategy: [magenta]{original_backtest.strategy_name}[/magenta] ({strategy_type})\n"
                f"Symbol: [blue]{symbol}[/blue]\n"
                f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}\n"
                f"Parameters: {strategy_params}",
                title="📋 Configuration to Reproduce",
                border_style="cyan",
            )
        )

        # Step 5: Validate strategy type
        valid_strategies = ["sma_crossover", "sma", "mean_reversion", "momentum"]
        if strategy_type not in valid_strategies:
            console.print(
                f"[red]❌ Unsupported strategy type: {strategy_type}[/red]\n"
                f"[yellow]💡 Supported strategies: {', '.join(valid_strategies)}[/yellow]\n"
                "[dim]The original strategy may have been removed or renamed.[/dim]"
            )
            sys.exit(1)

        # Step 6: Load data from catalog
        console.print("\n[cyan]📦 Loading data from catalog...[/cyan]")

        try:
            from src.services.data_catalog import DataCatalogService

            catalog_service = DataCatalogService()

            # Determine bar type based on date range (same logic as backtest run command)
            duration_days = (end_date - start_date).days
            if duration_days <= 2:
                bar_type_spec = "1-MINUTE-LAST"
            elif duration_days <= 7:
                bar_type_spec = "5-MINUTE-LAST"
            elif duration_days <= 30:
                bar_type_spec = "15-MINUTE-LAST"
            elif duration_days <= 90:
                bar_type_spec = "1-HOUR-LAST"
            else:
                bar_type_spec = "1-DAY-LAST"

            # Construct instrument_id with venue (matching backtest.py format)
            instrument_id = f"{symbol.upper()}.NASDAQ"

            # Load bars and instrument from catalog
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Loading data from catalog...", total=None)

                bars = await catalog_service.fetch_or_load(
                    instrument_id=instrument_id,
                    start=start_date,
                    end=end_date,
                    bar_type_spec=bar_type_spec,
                    correlation_id=f"reproduce-{str(original_run_id)[:8]}",
                )

                instrument = catalog_service.load_instrument(instrument_id)

                progress.update(task, completed=True)

            console.print(f"[green]✅ Loaded {len(bars):,} bars from catalog[/green]\n")

        except DataNotFoundError:
            console.print(
                f"[red]❌ Data not found in catalog for {symbol}[/red]\n"
                f"[yellow]💡 The market data may need to be imported first.[/yellow]\n"
                "[dim]Use 'ntrader data import' to import data[/dim]"
            )
            sys.exit(1)

        # Step 7: Execute reproduced backtest
        console.print("[cyan]⚡ Executing reproduced backtest...[/cyan]")

        start_time = time.time()

        try:
            # Initialize backtest runner with catalog data source
            runner = MinimalBacktestRunner(data_source="catalog")

            # Run backtest with original parameters using loaded catalog data
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Running backtest...", total=None)

                result, new_run_id = await runner.run_backtest_with_catalog_data(
                    bars=bars,
                    strategy_type=strategy_type,
                    symbol=symbol,
                    start=start_date,
                    end=end_date,
                    instrument=instrument,
                    reproduced_from_run_id=original_run_id,
                    **strategy_params,
                )

                progress.update(task, completed=True)

            execution_duration = time.time() - start_time

            # Step 8: Display results
            # new_run_id is already set from the tuple unpacking above

            console.print(
                Panel.fit(
                    f"[bold green]✅ Backtest Reproduced Successfully![/bold green]\n\n"
                    f"[bold]Original Run ID:[/bold] [yellow]{str(original_run_id)[:12]}...[/yellow]\n"
                    f"[bold]New Run ID:[/bold] [green]{str(new_run_id)[:12]}...[/green]\n\n"
                    f"[dim]Execution time: {execution_duration:.2f}s[/dim]\n\n"
                    f"[bold cyan]Quick Commands:[/bold cyan]\n"
                    f"  View details: [dim]ntrader backtest show {new_run_id}[/dim]\n"
                    f"  Compare runs: [dim]ntrader backtest compare {original_run_id} {new_run_id}[/dim]",
                    title="🎯 Reproduction Complete",
                    border_style="green",
                )
            )

            # Display key metrics comparison
            if result.total_return is not None:
                original_return = None
                original_trades = None

                if original_backtest.metrics:
                    original_return = float(original_backtest.metrics.total_return)
                    original_trades = original_backtest.metrics.total_trades

                console.print("\n[bold]Performance Summary:[/bold]")
                console.print(
                    f"  Total Return: [{'green' if result.total_return > 0 else 'red'}]{result.total_return:.2%}[/]"
                )

                if original_return is not None:
                    diff = result.total_return - original_return
                    console.print(
                        f"  Original Return: {original_return:.2%} "
                        f"[dim](diff: {diff:+.2%})[/dim]"
                    )

                if result.total_trades is not None:
                    trades_display = f"  Total Trades: {result.total_trades}"
                    if (
                        original_trades is not None
                        and original_trades != result.total_trades
                    ):
                        trades_display += f" [dim](original: {original_trades})[/dim]"
                    console.print(trades_display)

                if (
                    result.winning_trades is not None
                    or result.losing_trades is not None
                ):
                    console.print(f"  Win Rate: {result.win_rate:.1f}%")

        except DataNotFoundError as e:
            console.print(
                f"[red]❌ Data not available: {e}[/red]\n"
                f"[yellow]💡 The required market data for {symbol} may not be in the catalog.[/yellow]\n"
                "[dim]Try importing data first using 'ntrader data import'[/dim]"
            )
            sys.exit(1)

        except ValueError as e:
            error_msg = str(e)
            console.print(
                f"[red]❌ Backtest execution failed: {error_msg}[/red]\n"
                "[yellow]💡 Check that the strategy parameters are valid and data is available.[/yellow]"
            )
            sys.exit(1)

        except Exception as e:
            console.print(
                f"[red]❌ Unexpected error during reproduction: {e}[/red]\n"
                "[yellow]💡 This may indicate a system issue or corrupted configuration.[/yellow]\n"
                "[dim]Original backtest configuration:[/dim]\n"
                f"[dim]  Strategy: {strategy_type}[/dim]\n"
                f"[dim]  Symbol: {symbol}[/dim]\n"
                f"[dim]  Period: {start_date} to {end_date}[/dim]"
            )
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]❌ Error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    reproduce_backtest()
