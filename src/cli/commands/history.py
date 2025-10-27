"""
CLI command for viewing backtest history.

Provides user-friendly interface for querying and displaying
past backtest executions with performance metrics.
"""

import asyncio
import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.db.session import get_session
from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_query import BacktestQueryService

console = Console()


@click.command(name="history")
@click.option(
    "--limit",
    default=20,
    type=int,
    help="Number of results to display (default: 20, max: 1000)",
)
@click.option("--strategy", default=None, type=str, help="Filter by strategy name")
@click.option(
    "--instrument", default=None, type=str, help="Filter by instrument symbol"
)
@click.option(
    "--status",
    type=click.Choice(["success", "failed"], case_sensitive=False),
    default=None,
    help="Filter by execution status",
)
@click.option(
    "--sort",
    type=click.Choice(["date", "return", "sharpe"], case_sensitive=False),
    default="date",
    help="Sort by: date (default), return, or sharpe",
)
def list_backtest_history(
    limit: int,
    strategy: str | None,
    instrument: str | None,
    status: str | None,
    sort: str,
):
    """
    List recent backtest executions with performance metrics.

    Displays a formatted table of backtest runs with key metrics including
    total return, Sharpe ratio, and execution status. Results can be sorted
    by date (default), total return, or Sharpe ratio.

    Examples:
        ntrader history                           # Show 20 most recent backtests
        ntrader history --limit 50                # Show 50 most recent
        ntrader history --strategy "SMA Crossover"  # Filter by strategy
        ntrader history --status success          # Show only successful backtests
        ntrader history --sort sharpe             # Sort by Sharpe ratio (best first)
        ntrader history --sort return --limit 10  # Top 10 by return
    """
    asyncio.run(_list_history_async(limit, strategy, instrument, status, sort))


async def _list_history_async(
    limit: int,
    strategy: str | None,
    instrument: str | None,
    status: str | None,
    sort: str,
):
    """
    Async implementation of history listing.

    Args:
        limit: Maximum number of results
        strategy: Optional strategy name filter
        instrument: Optional instrument symbol filter
        status: Optional execution status filter
        sort: Sort order (date, return, sharpe)
    """
    try:
        async with get_session() as session:
            repository = BacktestRepository(session)
            service = BacktestQueryService(repository)

            # Show progress spinner during query
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Loading backtest history...", total=None)

                # Query based on sort option
                if sort == "sharpe":
                    backtests = await service.find_top_performers(
                        metric="sharpe_ratio", limit=limit
                    )
                    # Apply strategy filter if provided
                    if strategy:
                        backtests = [
                            bt for bt in backtests if bt.strategy_name == strategy
                        ]
                elif sort == "return":
                    backtests = await service.find_top_performers(
                        metric="total_return", limit=limit
                    )
                    # Apply strategy filter if provided
                    if strategy:
                        backtests = [
                            bt for bt in backtests if bt.strategy_name == strategy
                        ]
                else:  # sort == "date" (default)
                    # Query based on filters
                    if strategy:
                        backtests = await service.list_by_strategy(
                            strategy_name=strategy, limit=limit
                        )
                    else:
                        backtests = await service.list_recent_backtests(limit=limit)

                # Apply additional filters (instrument, status) in memory
                if instrument:
                    backtests = [
                        bt for bt in backtests if bt.instrument_symbol == instrument
                    ]

                if status:
                    backtests = [
                        bt for bt in backtests if bt.execution_status == status
                    ]

                progress.update(task, completed=True)

        # Handle empty results
        if not backtests:
            console.print("📭 No backtests found", style="yellow")
            return

        # Format results in Rich table with sort indicator
        sort_indicator = {
            "date": "📅 ↓",
            "return": "💰 ↓",
            "sharpe": "📈 ↓",
        }
        sort_label = sort_indicator.get(sort, "")

        table = Table(
            title=f"📋 Backtest History ({len(backtests)} results) {sort_label}",
            show_header=True,
            header_style="bold cyan",
            show_lines=True,
        )

        # Add columns with highlighting for sorted column
        table.add_column("Run ID", style="cyan", max_width=12, no_wrap=True)

        # Highlight sorted column
        date_style = "bold yellow" if sort == "date" else "white"
        return_style_base = "bold yellow" if sort == "return" else "green"
        sharpe_style_base = "bold yellow" if sort == "sharpe" else "white"

        table.add_column("Date", style=date_style, no_wrap=True)
        table.add_column("Strategy", style="magenta")
        table.add_column("Symbol", style="blue", no_wrap=True)
        table.add_column(
            "Return", style=return_style_base, justify="right", no_wrap=True
        )
        table.add_column(
            "Sharpe", style=sharpe_style_base, justify="right", no_wrap=True
        )
        table.add_column("Status", justify="center", no_wrap=True)

        # Add rows
        for bt in backtests:
            metrics = bt.metrics

            # Format status with emoji
            if bt.execution_status == "success":
                status_display = "✅"
                status_style = "green"
            else:
                status_display = "❌"
                status_style = "red"

            # Format metrics (handle None values)
            return_display = f"{float(metrics.total_return):.2%}" if metrics else "N/A"
            sharpe_display = (
                f"{float(metrics.sharpe_ratio):.2f}"
                if metrics and metrics.sharpe_ratio
                else "N/A"
            )

            # Colorize return based on value
            if metrics and metrics.total_return:
                if metrics.total_return > 0:
                    return_style = "green"
                elif metrics.total_return < 0:
                    return_style = "red"
                else:
                    return_style = "white"
            else:
                return_style = "dim"

            table.add_row(
                f"[dim]{str(bt.run_id)[:8]}...[/dim]",
                bt.created_at.strftime("%Y-%m-%d %H:%M"),
                bt.strategy_name,
                bt.instrument_symbol,
                f"[{return_style}]{return_display}[/{return_style}]",
                sharpe_display,
                f"[{status_style}]{status_display}[/{status_style}]",
            )

        console.print(table)

        # Add summary footer
        console.print(
            f"\n✨ Showing {len(backtests)} of {limit} requested", style="dim"
        )

        # Add helpful hints
        if len(backtests) == limit:
            console.print(
                "💡 Tip: Use --limit to see more results (max 1000)", style="dim italic"
            )

    except Exception as e:
        console.print(f"❌ Error: {e}", style="red")
        console.print(
            "💡 Check database is running and credentials are correct", style="dim"
        )
        raise
