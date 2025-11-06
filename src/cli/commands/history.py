"""
CLI command for viewing backtest history.

Provides user-friendly interface for querying and displaying
past backtest executions with performance metrics.
"""

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.db.session_sync import get_sync_session
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository

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
@click.option(
    "--strategy-summary",
    is_flag=True,
    default=False,
    help="Show aggregate statistics for the strategy (requires --strategy)",
)
@click.option(
    "--show-params",
    is_flag=True,
    default=False,
    help="Display parameter variations across runs",
)
def list_backtest_history(
    limit: int,
    strategy: str | None,
    instrument: str | None,
    status: str | None,
    sort: str,
    strategy_summary: bool,
    show_params: bool,
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
    _list_history_sync(
        limit, strategy, instrument, status, sort, strategy_summary, show_params
    )


def _list_history_sync(
    limit: int,
    strategy: str | None,
    instrument: str | None,
    status: str | None,
    sort: str,
    strategy_summary: bool,
    show_params: bool,
):
    """
    Synchronous implementation of history listing.

    Args:
        limit: Maximum number of results
        strategy: Optional strategy name filter
        instrument: Optional instrument symbol filter
        status: Optional execution status filter
        sort: Sort order (date, return, sharpe)
    """
    try:
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)

            # Show progress spinner during query
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
            ) as progress:
                task = progress.add_task("Loading backtest history...", total=None)

                # Enforce maximum limit
                limit = min(limit, 1000)

                # Query based on sort option
                if sort == "sharpe":
                    backtests = repository.find_top_performers_by_sharpe(limit=limit)
                    # Apply strategy filter if provided
                    if strategy:
                        backtests = [
                            bt for bt in backtests if bt.strategy_name == strategy
                        ]
                elif sort == "return":
                    backtests = repository.find_top_performers_by_return(limit=limit)
                    # Apply strategy filter if provided
                    if strategy:
                        backtests = [
                            bt for bt in backtests if bt.strategy_name == strategy
                        ]
                else:  # sort == "date" (default)
                    # Query based on filters
                    if strategy:
                        backtests = repository.find_by_strategy(
                            strategy_name=strategy, limit=limit
                        )
                    else:
                        backtests = repository.find_recent(limit=limit)

                # Apply additional filters (instrument, status) in memory
                if instrument:
                    backtests = [
                        bt for bt in backtests if bt.instrument_symbol == instrument
                    ]

                if status:
                    backtests = [
                        bt for bt in backtests if bt.execution_status == status
                    ]

                # Get total count if filtering by strategy (T161)
                total_count = None
                if strategy and sort == "date":
                    total_count = repository.count_by_strategy(strategy)

                progress.update(task, completed=True)

        # Handle empty results
        if not backtests:
            console.print("📭 No backtests found", style="yellow")
            return

        # Display strategy summary if requested (T162)
        if strategy_summary:
            if not strategy:
                console.print(
                    "❌ Error: --strategy-summary requires --strategy flag",
                    style="red",
                )
                return

            _display_strategy_summary(backtests, strategy)
            return

        # Format results in Rich table with sort indicator
        sort_indicator = {
            "date": "📅 ↓",
            "return": "💰 ↓",
            "sharpe": "📈 ↓",
        }
        sort_label = sort_indicator.get(sort, "")

        # Build table title with count info (T161)
        if total_count is not None:
            title = f"📋 Backtest History for '{strategy}' (showing {len(backtests)} of {total_count} total) {sort_label}"
        else:
            title = f"📋 Backtest History ({len(backtests)} results) {sort_label}"

        table = Table(
            title=title,
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

        # Add parameters column if requested (T163)
        if show_params:
            table.add_column("Parameters", style="dim", max_width=30)

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

            # Extract parameters if requested (T163)
            row_data = [
                f"[dim]{str(bt.run_id)[:8]}...[/dim]",
                bt.created_at.strftime("%Y-%m-%d %H:%M"),
                bt.strategy_name,
                bt.instrument_symbol,
                f"[{return_style}]{return_display}[/{return_style}]",
                sharpe_display,
                f"[{status_style}]{status_display}[/{status_style}]",
            ]

            if show_params:
                params = _extract_parameters(bt.config_snapshot)
                row_data.append(params)

            table.add_row(*row_data)

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


def _display_strategy_summary(backtests: list, strategy_name: str):
    """
    Display aggregate statistics for a strategy (T162).

    Args:
        backtests: List of BacktestRun instances
        strategy_name: Name of the strategy being summarized
    """
    from rich.panel import Panel
    from rich.text import Text

    # Filter out failed backtests for statistics
    successful_backtests = [bt for bt in backtests if bt.metrics is not None]

    if not successful_backtests:
        console.print(
            f"📊 No successful backtests found for strategy '{strategy_name}'",
            style="yellow",
        )
        return

    # Calculate aggregate statistics
    total_runs = len(backtests)
    successful_runs = len(successful_backtests)
    failed_runs = total_runs - successful_runs

    # Extract metrics
    returns = [float(bt.metrics.total_return) for bt in successful_backtests]
    sharpes = [
        float(bt.metrics.sharpe_ratio)
        for bt in successful_backtests
        if bt.metrics.sharpe_ratio is not None
    ]
    max_drawdowns = [
        float(bt.metrics.max_drawdown)
        for bt in successful_backtests
        if bt.metrics.max_drawdown is not None
    ]
    win_rates = [
        float(bt.metrics.win_rate)
        for bt in successful_backtests
        if bt.metrics.win_rate is not None
    ]

    # Calculate aggregates
    avg_return = sum(returns) / len(returns) if returns else 0
    best_return = max(returns) if returns else 0
    worst_return = min(returns) if returns else 0

    avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0
    best_sharpe = max(sharpes) if sharpes else 0

    avg_drawdown = sum(max_drawdowns) / len(max_drawdowns) if max_drawdowns else 0
    worst_drawdown = min(max_drawdowns) if max_drawdowns else 0

    avg_win_rate = sum(win_rates) / len(win_rates) if win_rates else 0

    # Build summary panel
    summary_text = Text()
    summary_text.append(f"Strategy: {strategy_name}\n\n", style="bold cyan")

    summary_text.append("📊 Execution Summary\n", style="bold white")
    summary_text.append(f"  Total Runs: {total_runs}\n", style="white")
    summary_text.append(f"  Successful: {successful_runs} ✅\n", style="green")
    summary_text.append(f"  Failed: {failed_runs} ❌\n\n", style="red")

    summary_text.append("💰 Return Statistics\n", style="bold white")
    summary_text.append(f"  Average Return: {avg_return:.2%}\n", style="white")
    summary_text.append(f"  Best Return: {best_return:.2%}\n", style="green")
    summary_text.append(f"  Worst Return: {worst_return:.2%}\n\n", style="red")

    summary_text.append("📈 Risk-Adjusted Performance\n", style="bold white")
    summary_text.append(f"  Average Sharpe Ratio: {avg_sharpe:.2f}\n", style="white")
    summary_text.append(f"  Best Sharpe Ratio: {best_sharpe:.2f}\n\n", style="green")

    summary_text.append("📉 Risk Metrics\n", style="bold white")
    summary_text.append(f"  Average Max Drawdown: {avg_drawdown:.2%}\n", style="white")
    summary_text.append(f"  Worst Max Drawdown: {worst_drawdown:.2%}\n\n", style="red")

    summary_text.append("🎯 Trading Performance\n", style="bold white")
    summary_text.append(f"  Average Win Rate: {avg_win_rate:.1%}\n", style="white")

    panel = Panel(
        summary_text,
        title=f"[bold cyan]Strategy Summary: {strategy_name}[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    )

    console.print(panel)


def _extract_parameters(config_snapshot: dict) -> str:
    """
    Extract and format parameters from config snapshot (T163).

    Args:
        config_snapshot: JSONB config snapshot from database

    Returns:
        Formatted string of key parameters
    """
    try:
        config = config_snapshot.get("config", {})

        if not config:
            return "N/A"

        # Extract key parameters (limit to most important ones)
        params = []
        for key, value in list(config.items())[:3]:  # Show max 3 params
            # Format parameter name and value
            if isinstance(value, (int, float)):
                params.append(f"{key}={value}")
            elif isinstance(value, str):
                # Truncate long strings
                value_str = value[:10] + "..." if len(value) > 10 else value
                params.append(f"{key}={value_str}")
            else:
                params.append(f"{key}={str(value)[:10]}")

        if len(config) > 3:
            params.append("...")

        return ", ".join(params)
    except Exception:
        return "N/A"
