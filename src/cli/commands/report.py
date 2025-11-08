"""Report generation and viewing CLI commands using PostgreSQL storage."""

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from pathlib import Path

from src.db.session_sync import get_sync_session
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.services.reports.text_report import TextReportGenerator
from src.services.reports.csv_exporter import CSVExporter

console = Console()


@click.group()
def report():
    """Report generation and viewing commands."""
    pass


@report.command()
@click.argument("result_id")
def summary(result_id: str):
    """
    Display quick performance summary for a backtest result.

    RESULT_ID: UUID of the backtest run (from 'backtest history')
    """
    try:
        # Load result from PostgreSQL
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)
            backtest = repository.find_by_run_id(result_id)

            if not backtest:
                console.print(f"❌ Result not found: {result_id}", style="red bold")
                console.print("\n💡 Use 'backtest history' to see available results")
                return

            # Display summary using Rich formatting
            _display_summary_panel(backtest)

            # Display key metrics table
            _display_metrics_table(backtest)

    except Exception as e:
        console.print(f"❌ Error loading result: {e}", style="red bold")


@report.command()
@click.option(
    "--result-id", required=True, help="Backtest result ID to generate report for"
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "csv", "json"]),
    default="text",
    help="Report output format",
)
@click.option("--output", "-o", type=click.Path(), help="Output file path")
def generate(result_id: str, output_format: str, output: str):
    """
    Generate comprehensive backtest report in various formats.

    Supports text (Rich formatted), CSV, and JSON output formats.
    """
    try:
        # Load result from PostgreSQL
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)
            backtest = repository.find_by_run_id(result_id)

            if not backtest:
                console.print(f"❌ Result not found: {result_id}", style="red bold")
                console.print("\n💡 Use 'backtest history' to see available results")
                return

            console.print(
                f"📊 Generating {output_format.upper()} report for {str(backtest.run_id)[:8]}..."
            )

            if output_format == "text":
                _generate_text_report(backtest, output)
            elif output_format == "csv":
                _generate_csv_report(
                    backtest, output or f"report_{backtest.run_id}.csv"
                )
            elif output_format == "json":
                _generate_json_report(
                    backtest, output or f"report_{backtest.run_id}.json"
                )

    except Exception as e:
        console.print(f"❌ Error generating report: {e}", style="red bold")


@report.command()
@click.option("--limit", default=10, help="Maximum number of results to display")
@click.option("--strategy", help="Filter by strategy name")
@click.option("--symbol", help="Filter by trading symbol")
def list(limit: int, strategy: str, symbol: str):
    """
    List all available backtest results from PostgreSQL.

    Results are displayed in a formatted table with key metrics.
    """
    try:
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)

            # Get results with optional filtering
            if strategy:
                backtests = repository.find_by_strategy(strategy, limit=limit)
            elif symbol:
                backtests = repository.find_by_instrument(symbol, limit=limit)
            else:
                backtests = repository.find_recent(limit=limit)

            if not backtests:
                console.print("📭 No backtest results found", style="yellow")
                console.print("\n💡 Run a backtest to create results:")
                console.print(
                    "   uv run python -m src.cli backtest run --strategy sma_crossover --symbol AAPL ..."
                )
                return

            # Display results table
            _display_results_table(backtests)

            # Show storage info
            total_count = repository.count_all()
            console.print(
                f"\n💾 Storage: {total_count} total backtests in PostgreSQL database"
            )

    except Exception as e:
        console.print(f"❌ Error listing results: {e}", style="red bold")


@report.command("export-all")
@click.argument("result_id")
@click.option(
    "--output-dir",
    "-o",
    default="exports",
    type=click.Path(),
    help="Output directory for exports",
)
def export_all(result_id: str, output_dir: str):
    """
    Export all report formats for a backtest result.

    Creates text, CSV, and JSON reports in the specified directory.

    RESULT_ID: UUID of the backtest run
    """
    try:
        # Load result from PostgreSQL
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)
            backtest = repository.find_by_run_id(result_id)

            if not backtest:
                console.print(f"❌ Result not found: {result_id}", style="red bold")
                console.print("\n💡 Use 'backtest history' to see available results")
                return

            # Create output directory
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            console.print(
                f"📦 Exporting all formats for {str(backtest.run_id)[:8]} to {output_dir}/"
            )

            # Export text report
            text_file = output_path / f"{backtest.run_id}_report.txt"
            _generate_text_report(backtest, str(text_file))

            # Export CSV
            csv_file = output_path / f"{backtest.run_id}_report.csv"
            _generate_csv_report(backtest, str(csv_file))

            # Export JSON
            json_file = output_path / f"{backtest.run_id}_report.json"
            _generate_json_report(backtest, str(json_file))

            # Display summary of exported files
            console.print("\n✅ Export complete!", style="green bold")
            console.print("\nExported files:")
            console.print(f"  📄 Text:  {text_file.name}")
            console.print(f"  📊 CSV:   {csv_file.name}")
            console.print(f"  📋 JSON:  {json_file.name}")

    except Exception as e:
        console.print(f"❌ Error exporting: {e}", style="red bold")


# Helper functions


def _display_summary_panel(backtest):
    """Display summary panel with key metrics."""
    metrics = backtest.metrics

    # Format dates
    start = backtest.start_date.strftime("%Y-%m-%d")
    end = backtest.end_date.strftime("%Y-%m-%d")

    # Format metrics (handle None values)
    total_return = f"{float(metrics.total_return):.2%}" if metrics else "N/A"
    sharpe = (
        f"{float(metrics.sharpe_ratio):.2f}"
        if metrics and metrics.sharpe_ratio
        else "N/A"
    )
    max_dd = (
        f"{float(metrics.max_drawdown):.2%}"
        if metrics and metrics.max_drawdown
        else "N/A"
    )
    win_rate = (
        f"{float(metrics.win_rate):.2%}" if metrics and metrics.win_rate else "N/A"
    )

    summary_text = f"""
    [bold cyan]Backtest ID:[/] {str(backtest.run_id)[:12]}...
    [bold cyan]Strategy:[/] {backtest.strategy_name}
    [bold cyan]Symbol:[/] {backtest.instrument_symbol}
    [bold cyan]Period:[/] {start} to {end}

    [bold green]Total Return:[/] {total_return}
    [bold]Final Balance:[/] ${float(metrics.final_balance):,.2f}
    [bold]Total Trades:[/] {metrics.total_trades if metrics else 0}
    [bold]Win Rate:[/] {win_rate}
    [bold]Sharpe Ratio:[/] {sharpe}
    [bold]Max Drawdown:[/] {max_dd}
    """

    panel = Panel(
        summary_text.strip(),
        title="📊 Backtest Performance Summary",
        border_style="blue",
        padding=(1, 2),
    )
    console.print(panel)


def _display_metrics_table(backtest):
    """Display detailed metrics table."""
    metrics = backtest.metrics

    if not metrics:
        console.print("⚠️  No metrics available for this backtest", style="yellow")
        return

    table = Table(title="Performance Metrics", style="cyan")
    table.add_column("Metric", style="white", no_wrap=True)
    table.add_column("Value", style="green")

    metrics_data = [
        ("Sharpe Ratio", _format_number(metrics.sharpe_ratio)),
        ("Sortino Ratio", _format_number(metrics.sortino_ratio)),
        ("Calmar Ratio", _format_number(metrics.calmar_ratio)),
        ("Profit Factor", _format_number(metrics.profit_factor)),
        ("Volatility", _format_percentage(metrics.volatility)),
        ("Total Return", _format_percentage(metrics.total_return)),
        ("CAGR", _format_percentage(metrics.cagr)),
        ("Winning Trades", str(metrics.winning_trades)),
        ("Losing Trades", str(metrics.losing_trades)),
        ("Avg Win", f"${float(metrics.avg_win):,.2f}" if metrics.avg_win else "N/A"),
        (
            "Avg Loss",
            f"${float(metrics.avg_loss):,.2f}" if metrics.avg_loss else "N/A",
        ),
    ]

    for metric, value in metrics_data:
        table.add_row(metric, value)

    console.print(table)


def _display_results_table(backtests):
    """Display table of backtest results."""
    table = Table(
        title="📋 Backtest Results", show_header=True, header_style="bold cyan"
    )
    table.add_column("ID", style="cyan", no_wrap=True, max_width=12)
    table.add_column("Timestamp", style="white")
    table.add_column("Strategy", style="magenta")
    table.add_column("Symbol", style="blue")
    table.add_column("Return", style="green", justify="right")
    table.add_column("Trades", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("Sharpe", justify="right")

    for bt in backtests:
        metrics = bt.metrics

        # Format values
        total_return = f"{float(metrics.total_return):.2%}" if metrics else "N/A"
        win_rate = (
            f"{float(metrics.win_rate):.2%}" if metrics and metrics.win_rate else "N/A"
        )
        sharpe = (
            f"{float(metrics.sharpe_ratio):.2f}"
            if metrics and metrics.sharpe_ratio
            else "N/A"
        )
        total_trades = str(metrics.total_trades) if metrics else "0"

        table.add_row(
            str(bt.run_id)[:12],
            bt.created_at.strftime("%Y-%m-%d %H:%M"),
            bt.strategy_name,
            bt.instrument_symbol,
            total_return,
            total_trades,
            win_rate,
            sharpe,
        )

    console.print(table)


def _generate_text_report(backtest, output_path=None):
    """Generate text report using TextReportGenerator."""
    metrics = backtest.metrics

    if not metrics:
        console.print("⚠️  Cannot generate report: No metrics available", style="yellow")
        return

    generator = TextReportGenerator()

    # Prepare metrics dictionary
    metrics_dict = {
        "total_return": float(metrics.total_return),
        "sharpe_ratio": float(metrics.sharpe_ratio) if metrics.sharpe_ratio else None,
        "sortino_ratio": (
            float(metrics.sortino_ratio) if metrics.sortino_ratio else None
        ),
        "max_drawdown": float(metrics.max_drawdown) if metrics.max_drawdown else None,
        "calmar_ratio": float(metrics.calmar_ratio) if metrics.calmar_ratio else None,
        "volatility": float(metrics.volatility) if metrics.volatility else None,
        "profit_factor": (
            float(metrics.profit_factor) if metrics.profit_factor else None
        ),
        "win_rate": float(metrics.win_rate) if metrics.win_rate else None,
        "total_trades": metrics.total_trades,
        "winning_trades": metrics.winning_trades,
        "losing_trades": metrics.losing_trades,
        "avg_win": float(metrics.avg_win) if metrics.avg_win else 0.0,
        "avg_loss": float(metrics.avg_loss) if metrics.avg_loss else 0.0,
        "largest_win": 0.0,  # Not currently stored in DB
        "largest_loss": 0.0,  # Not currently stored in DB
        "final_balance": float(metrics.final_balance),
        "expectancy": float(metrics.expectancy) if metrics.expectancy else None,
    }

    # Generate report
    report_content = generator.generate_performance_report(metrics_dict)

    if output_path:
        # Save to file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        console.print(f"✅ Text report saved to {output_path}", style="green")
    else:
        # Display to console
        console.print(report_content)


def _generate_csv_report(backtest, output_path):
    """Generate CSV report using CSVExporter."""
    metrics = backtest.metrics

    if not metrics:
        console.print("⚠️  Cannot generate CSV: No metrics available", style="yellow")
        return

    exporter = CSVExporter()

    # Build metrics dictionary for CSV export
    metrics_dict = {
        "backtest_id": str(backtest.run_id),
        "strategy": backtest.strategy_name,
        "symbol": backtest.instrument_symbol,
        "start_date": backtest.start_date.isoformat(),
        "end_date": backtest.end_date.isoformat(),
        "total_return": float(metrics.total_return),
        "final_balance": float(metrics.final_balance),
        "sharpe_ratio": float(metrics.sharpe_ratio) if metrics.sharpe_ratio else None,
        "sortino_ratio": (
            float(metrics.sortino_ratio) if metrics.sortino_ratio else None
        ),
        "calmar_ratio": float(metrics.calmar_ratio) if metrics.calmar_ratio else None,
        "max_drawdown": float(metrics.max_drawdown) if metrics.max_drawdown else None,
        "volatility": float(metrics.volatility) if metrics.volatility else None,
        "total_trades": metrics.total_trades,
        "winning_trades": metrics.winning_trades,
        "losing_trades": metrics.losing_trades,
        "win_rate": float(metrics.win_rate) if metrics.win_rate else None,
        "profit_factor": (
            float(metrics.profit_factor) if metrics.profit_factor else None
        ),
        "expectancy": float(metrics.expectancy) if metrics.expectancy else None,
        "avg_win": float(metrics.avg_win) if metrics.avg_win else None,
        "avg_loss": float(metrics.avg_loss) if metrics.avg_loss else None,
    }

    success = exporter.export_metrics(metrics_dict, output_path)

    if success:
        console.print(f"✅ CSV report exported to {output_path}", style="green")
    else:
        console.print("❌ Failed to export CSV report", style="red")


def _generate_json_report(backtest, output_path):
    """Generate JSON report."""
    import json
    from decimal import Decimal
    from datetime import datetime

    def json_serializer(obj):
        """Custom JSON serializer."""
        if isinstance(obj, Decimal):
            return str(obj)
        elif isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    metrics = backtest.metrics

    # Build complete result dictionary
    result_data = {
        "backtest_id": str(backtest.run_id),
        "strategy": backtest.strategy_name,
        "strategy_type": backtest.strategy_type,
        "symbol": backtest.instrument_symbol,
        "start_date": backtest.start_date,
        "end_date": backtest.end_date,
        "initial_capital": backtest.initial_capital,
        "data_source": backtest.data_source,
        "execution_status": backtest.execution_status,
        "execution_duration_seconds": backtest.execution_duration_seconds,
        "config_snapshot": backtest.config_snapshot,
        "created_at": backtest.created_at,
        "metrics": (
            {
                "total_return": metrics.total_return,
                "final_balance": metrics.final_balance,
                "cagr": metrics.cagr,
                "sharpe_ratio": metrics.sharpe_ratio,
                "sortino_ratio": metrics.sortino_ratio,
                "max_drawdown": metrics.max_drawdown,
                "max_drawdown_date": metrics.max_drawdown_date,
                "calmar_ratio": metrics.calmar_ratio,
                "volatility": metrics.volatility,
                "total_trades": metrics.total_trades,
                "winning_trades": metrics.winning_trades,
                "losing_trades": metrics.losing_trades,
                "win_rate": metrics.win_rate,
                "profit_factor": metrics.profit_factor,
                "expectancy": metrics.expectancy,
                "avg_win": metrics.avg_win,
                "avg_loss": metrics.avg_loss,
            }
            if metrics
            else None
        ),
    }

    # Write to JSON file
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, default=json_serializer)

    console.print(f"✅ JSON report exported to {output_path}", style="green")


# Formatting helpers


def _format_number(value) -> str:
    """Format numeric value."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2f}"
    except (ValueError, TypeError):
        return "N/A"


def _format_percentage(value) -> str:
    """Format percentage value."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2%}"
    except (ValueError, TypeError):
        return "N/A"
