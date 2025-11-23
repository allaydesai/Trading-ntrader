"""
CLI command for viewing complete backtest details.

Provides detailed view of a single backtest execution including
all metadata, configuration snapshot, and performance metrics.
"""

import json
from uuid import UUID

import click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.db.session_sync import get_sync_session

console = Console()


@click.command(name="show")
@click.argument("run_id", type=str, required=True)
def show_backtest_details(run_id: str):
    """
    Display complete details of a specific backtest execution.

    Shows all metadata, configuration parameters, and performance metrics
    for a single backtest run identified by its UUID.

    Arguments:
        RUN_ID: UUID of the backtest execution to display

    Examples:
        ntrader backtest show a1b2c3d4-e5f6-7890-abcd-ef1234567890
        ntrader backtest show <run_id>
    """
    try:
        # Validate and parse UUID
        try:
            run_id_uuid = UUID(run_id)
        except ValueError:
            console.print(
                f"[red]Error:[/red] Invalid UUID format: {run_id}",
                style="bold red",
            )
            console.print(
                "\n[yellow]Tip:[/yellow] UUIDs should be in format: "
                "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            )
            return

        # Query database synchronously
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)
            backtest = repository.find_by_run_id(run_id_uuid)

            if backtest is None:
                console.print(
                    f"\n[red]Error:[/red] Backtest with ID {run_id} not found",
                    style="bold red",
                )
                console.print(
                    "\n[yellow]Tip:[/yellow] Use 'ntrader backtest history' "
                    "to see available backtests"
                )
                return

            # Display the backtest details
            _display_backtest(backtest)

    except Exception as e:
        console.print(f"\n[red]Error:[/red] {str(e)}", style="bold red")
        raise


def _display_backtest(backtest):
    """
    Display formatted backtest details using Rich components.

    Args:
        backtest: BacktestRun model instance with optional metrics
    """
    console.print()  # Add blank line

    # Header with status indicator
    status_color = "green" if backtest.execution_status == "success" else "red"
    status_text = Text(backtest.execution_status.upper(), style=f"bold {status_color}")

    header = Text()
    header.append("Backtest Details: ", style="bold cyan")
    header.append(backtest.strategy_name, style="bold white")
    header.append(" | Status: ", style="dim")
    header.append(status_text)

    console.print(Panel(header, border_style="cyan"))

    # If failed, show error message prominently
    if backtest.execution_status == "failed" and backtest.error_message:
        error_panel = Panel(
            backtest.error_message,
            title="[bold red]Execution Error[/bold red]",
            border_style="red",
            padding=(1, 2),
        )
        console.print(error_panel)
        console.print()  # Add spacing

    # Execution Metadata
    _display_metadata_table(backtest)

    # Configuration Snapshot
    _display_config_snapshot(backtest.config_snapshot)

    # Performance Metrics (only for successful backtests)
    if backtest.execution_status == "success" and backtest.metrics:
        _display_performance_metrics(backtest.metrics)
    elif backtest.execution_status == "success":
        console.print(
            "\n[yellow]Note:[/yellow] No performance metrics available "
            "(metrics may not have been calculated)"
        )

    console.print()  # Add blank line at end


def _display_metadata_table(backtest):
    """
    Display execution metadata in a formatted table.

    Args:
        backtest: BacktestRun model instance
    """
    table = Table(
        title="Execution Metadata",
        show_header=False,
        box=None,
        padding=(0, 2),
    )
    table.add_column("Field", style="cyan", width=30)
    table.add_column("Value", style="white")

    table.add_row("Run ID", str(backtest.run_id))
    table.add_row("Strategy", backtest.strategy_name)
    table.add_row("Strategy Type", backtest.strategy_type)
    table.add_row("Instrument", backtest.instrument_symbol)
    table.add_row(
        "Period",
        f"{backtest.start_date.strftime('%Y-%m-%d')} to {backtest.end_date.strftime('%Y-%m-%d')}",
    )
    table.add_row("Initial Capital", f"${backtest.initial_capital:,.2f}")
    table.add_row("Data Source", backtest.data_source)
    table.add_row("Duration", f"{backtest.execution_duration_seconds:.3f} seconds")
    table.add_row("Created At", backtest.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"))

    if backtest.reproduced_from_run_id:
        table.add_row(
            "Reproduced From",
            str(backtest.reproduced_from_run_id),
            style="dim",
        )

    console.print(table)
    console.print()


def _display_config_snapshot(config_snapshot: dict):
    """
    Display configuration snapshot with syntax highlighting.

    Args:
        config_snapshot: JSONB configuration dictionary
    """
    # Pretty-print JSON with syntax highlighting
    json_str = json.dumps(config_snapshot, indent=2, sort_keys=False)
    syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)

    panel = Panel(
        syntax,
        title="[bold cyan]Configuration Snapshot[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)
    console.print()


def _display_performance_metrics(metrics):
    """
    Display performance metrics in organized tables.

    Args:
        metrics: PerformanceMetrics model instance
    """
    console.print("[bold cyan]Performance Metrics[/bold cyan]\n")

    # Returns & Risk Metrics
    returns_table = Table(
        title="Returns & Risk",
        show_header=True,
        box=None,
        padding=(0, 2),
    )
    returns_table.add_column("Metric", style="cyan", width=25)
    returns_table.add_column("Value", style="white", justify="right")

    returns_table.add_row("Total Return", f"{metrics.total_return * 100:.2f}%")
    returns_table.add_row("Final Balance", f"${metrics.final_balance:,.2f}")

    if metrics.cagr is not None:
        returns_table.add_row("CAGR", f"{metrics.cagr * 100:.2f}%")

    if metrics.sharpe_ratio is not None:
        sharpe_color = "green" if metrics.sharpe_ratio > 1 else "yellow"
        returns_table.add_row(
            "Sharpe Ratio",
            f"[{sharpe_color}]{metrics.sharpe_ratio:.2f}[/{sharpe_color}]",
        )

    if metrics.sortino_ratio is not None:
        returns_table.add_row("Sortino Ratio", f"{metrics.sortino_ratio:.2f}")

    if metrics.max_drawdown is not None:
        dd_color = "red" if metrics.max_drawdown < -0.2 else "yellow"
        returns_table.add_row(
            "Max Drawdown",
            f"[{dd_color}]{metrics.max_drawdown * 100:.2f}%[/{dd_color}]",
        )

    if metrics.calmar_ratio is not None:
        returns_table.add_row("Calmar Ratio", f"{metrics.calmar_ratio:.2f}")

    if metrics.volatility is not None:
        returns_table.add_row("Volatility", f"{metrics.volatility * 100:.2f}%")

    console.print(returns_table)
    console.print()

    # Trading Statistics
    trading_table = Table(
        title="Trading Statistics",
        show_header=True,
        box=None,
        padding=(0, 2),
    )
    trading_table.add_column("Metric", style="cyan", width=25)
    trading_table.add_column("Value", style="white", justify="right")

    trading_table.add_row("Total Trades", str(metrics.total_trades))
    trading_table.add_row("Winning Trades", f"[green]{metrics.winning_trades}[/green]")
    trading_table.add_row("Losing Trades", f"[red]{metrics.losing_trades}[/red]")

    if metrics.win_rate is not None:
        win_rate_color = "green" if metrics.win_rate > 0.5 else "yellow"
        trading_table.add_row(
            "Win Rate",
            f"[{win_rate_color}]{metrics.win_rate * 100:.2f}%[/{win_rate_color}]",
        )

    if metrics.profit_factor is not None:
        pf_color = "green" if metrics.profit_factor > 2 else "yellow"
        trading_table.add_row(
            "Profit Factor", f"[{pf_color}]{metrics.profit_factor:.2f}[/{pf_color}]"
        )

    if metrics.expectancy is not None:
        exp_color = "green" if metrics.expectancy > 0 else "red"
        trading_table.add_row("Expectancy", f"[{exp_color}]${metrics.expectancy:.2f}[/{exp_color}]")

    if metrics.avg_win is not None:
        trading_table.add_row("Average Win", f"[green]${metrics.avg_win:.2f}[/green]")

    if metrics.avg_loss is not None:
        trading_table.add_row("Average Loss", f"[red]${metrics.avg_loss:.2f}[/red]")

    console.print(trading_table)
