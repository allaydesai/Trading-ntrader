"""
CLI command for comparing multiple backtest executions side-by-side.

Provides visual comparison of key performance metrics across 2-10
backtests to help identify the best performing strategies and parameters.
"""

from typing import List
from uuid import UUID

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.db.session_sync import get_sync_session

console = Console()


@click.command(name="compare")
@click.argument("run_ids", nargs=-1, required=True)
def compare_backtests(run_ids: tuple[str]):
    """
    Compare multiple backtests side-by-side.

    Displays key performance metrics for 2-10 backtests in a comparison table,
    making it easy to identify the best performing configuration.

    Arguments:
        RUN_IDS: 2-10 backtest UUIDs to compare

    Examples:
        ntrader backtest compare <uuid1> <uuid2>
        ntrader backtest compare <uuid1> <uuid2> <uuid3>

    The comparison highlights:
        - Strategy names and symbols
        - Total returns and final balance
        - Risk metrics (Sharpe ratio, Max Drawdown)
        - Trading statistics (Win Rate, Total Trades)
        - Best performer by Sharpe ratio
    """
    _compare_backtests_sync(run_ids)


def _compare_backtests_sync(run_ids_str: tuple[str]):
    """
    Synchronous implementation of backtest comparison.

    Args:
        run_ids_str: Tuple of string UUIDs to compare

    Handles:
        - UUID validation and parsing
        - Count validation (2-10 backtests)
        - Database queries
        - Rich table formatting
        - Missing backtest handling
    """
    try:
        # Validate minimum count
        if len(run_ids_str) < 2:
            console.print(
                "\n[red]Error:[/red] Must provide at least 2 run IDs to compare",
                style="bold red",
            )
            console.print(
                "\n[yellow]Usage:[/yellow] ntrader backtest compare <uuid1> <uuid2> [uuid3...]"
            )
            return

        # Validate maximum count
        if len(run_ids_str) > 10:
            console.print(
                "\n[red]Error:[/red] Cannot compare more than 10 backtests at once",
                style="bold red",
            )
            console.print(
                f"\n[yellow]Note:[/yellow] You provided {len(run_ids_str)} UUIDs. "
                "Please limit to 10."
            )
            return

        # Parse and validate UUIDs
        try:
            run_ids = [UUID(run_id_str) for run_id_str in run_ids_str]
        except ValueError as e:
            console.print(
                f"\n[red]Error:[/red] Invalid UUID format: {str(e)}",
                style="bold red",
            )
            console.print(
                "\n[yellow]Tip:[/yellow] UUIDs should be in format: "
                "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            )
            return

        # Query database
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)

            # Find backtests by IDs
            backtests = repository.find_by_run_ids(run_ids)

            # Check if any backtests were found
            if not backtests:
                console.print(
                    "\n[red]Error:[/red] No backtests found matching the provided IDs",
                    style="bold red",
                )
                console.print(
                    "\n[yellow]Tip:[/yellow] Use 'ntrader backtest history' "
                    "to see available backtests"
                )
                return

            # Handle partial results (some UUIDs not found)
            if len(backtests) < len(run_ids):
                found_ids = {bt.run_id for bt in backtests}
                missing_ids = set(run_ids) - found_ids

                console.print(
                    f"\n[yellow]Warning:[/yellow] {len(missing_ids)} backtest(s) not found:",
                    style="yellow",
                )
                for missing_id in missing_ids:
                    console.print(f"  - {missing_id}", style="dim")
                console.print()

            # Display comparison
            _display_comparison_table(backtests)

    except ValueError as e:
        # Catch validation errors from service layer
        console.print(f"\n[red]Error:[/red] {str(e)}", style="bold red")
        return
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {str(e)}", style="bold red")
        raise


def _display_comparison_table(backtests: List):
    """
    Display comparison table with Rich formatting.

    Args:
        backtests: List of BacktestRun instances with metrics loaded

    Creates a side-by-side comparison table showing:
        - Metadata (Strategy, Symbol, Date)
        - Key performance metrics (Return, Sharpe, Drawdown, Win Rate)
        - Highlights best performer by Sharpe ratio
    """
    console.print()  # Add blank line

    # Create comparison table
    table = Table(
        title=f"Backtest Comparison ({len(backtests)} runs)",
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
        padding=(0, 1),
    )

    # First column: metric names
    table.add_column("Metric", style="bold white", width=20)

    # Add column for each backtest (truncated UUID)
    for bt in backtests:
        table.add_column(
            str(bt.run_id)[:8] + "...",
            justify="right",
            style="white",
            width=15,
        )

    # Helper function to format metric row
    def add_metric_row(metric_name: str, extractor, formatter=str):
        """Add a row to the comparison table."""
        row = [metric_name]
        for bt in backtests:
            try:
                value = extractor(bt)
                row.append(formatter(value) if value is not None else "N/A")
            except (AttributeError, TypeError):
                row.append("N/A")
        table.add_row(*row)

    # Metadata rows
    add_metric_row("Strategy", lambda bt: bt.strategy_name, lambda x: x)
    add_metric_row("Symbol", lambda bt: bt.instrument_symbol, lambda x: x)
    add_metric_row("Date", lambda bt: bt.created_at, lambda x: x.strftime("%Y-%m-%d"))

    # Performance metric rows
    add_metric_row(
        "Total Return",
        lambda bt: bt.metrics.total_return if bt.metrics else None,
        lambda x: f"{x * 100:.2f}%",
    )
    add_metric_row(
        "Final Balance",
        lambda bt: bt.metrics.final_balance if bt.metrics else None,
        lambda x: f"${x:,.2f}",
    )
    add_metric_row(
        "Sharpe Ratio",
        lambda bt: bt.metrics.sharpe_ratio if bt.metrics else None,
        lambda x: f"{x:.2f}",
    )
    add_metric_row(
        "Max Drawdown",
        lambda bt: bt.metrics.max_drawdown if bt.metrics else None,
        lambda x: f"{x * 100:.2f}%",
    )
    add_metric_row(
        "Win Rate",
        lambda bt: bt.metrics.win_rate if bt.metrics else None,
        lambda x: f"{x * 100:.1f}%",
    )
    add_metric_row(
        "Total Trades",
        lambda bt: bt.metrics.total_trades if bt.metrics else None,
        lambda x: str(int(x)),
    )

    console.print(table)

    # Highlight best performer by Sharpe ratio
    _highlight_best_performer(backtests)

    console.print()  # Add blank line at end


def _highlight_best_performer(backtests: List):
    """
    Highlight the best performing backtest by Sharpe ratio.

    Args:
        backtests: List of BacktestRun instances

    Displays a summary message identifying the backtest with the
    highest Sharpe ratio, if available.
    """
    # Filter backtests with metrics and valid Sharpe ratio
    backtests_with_sharpe = [
        bt for bt in backtests if bt.metrics and bt.metrics.sharpe_ratio is not None
    ]

    if not backtests_with_sharpe:
        console.print(
            "\n[yellow]Note:[/yellow] No Sharpe ratios available for comparison",
            style="dim",
        )
        return

    # Find best performer
    best = max(backtests_with_sharpe, key=lambda bt: bt.metrics.sharpe_ratio)

    # Create highlight message
    best_text = Text()
    best_text.append("\nBest Performer by Sharpe Ratio: ", style="bold white")
    best_text.append(str(best.run_id)[:12] + "... ", style="bold green")
    best_text.append(f"(Sharpe: {best.metrics.sharpe_ratio:.2f}) ", style="green")
    best_text.append(f"- {best.strategy_name} on {best.instrument_symbol}", style="dim")

    console.print(best_text)
