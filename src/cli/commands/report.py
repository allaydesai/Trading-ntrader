"""Report generation and viewing CLI commands."""

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from pathlib import Path

from src.services.results_store import (
    ResultsStore,
    ResultNotFoundError,
    ResultsStoreError,
)
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

    RESULT_ID: Unique identifier of the backtest result
    """
    try:
        # Load result from store
        store = ResultsStore()
        result = store.get(result_id)

        # Display summary using Rich formatting
        _display_summary_panel(result)

        # Display key metrics table
        _display_metrics_table(result)

    except ResultNotFoundError:
        console.print(f"❌ Result not found: {result_id}", style="red bold")
        console.print("\n💡 Use 'report list' to see available results")
    except ResultsStoreError as e:
        console.print(f"❌ Error loading result: {e}", style="red bold")
    except Exception as e:
        console.print(f"❌ Unexpected error: {e}", style="red bold")


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
        # Load result from store
        store = ResultsStore()
        result = store.get(result_id)

        console.print(
            f"📊 Generating {output_format.upper()} report for {result_id[:8]}..."
        )

        if output_format == "text":
            _generate_text_report(result, output)
        elif output_format == "csv":
            _generate_csv_report(result, output or f"report_{result_id}.csv")
        elif output_format == "json":
            _generate_json_report(result, output or f"report_{result_id}.json")

    except ResultNotFoundError:
        console.print(f"❌ Result not found: {result_id}", style="red bold")
        console.print("\n💡 Use 'report list' to see available results")
    except ResultsStoreError as e:
        console.print(f"❌ Error loading result: {e}", style="red bold")
    except Exception as e:
        console.print(f"❌ Error generating report: {e}", style="red bold")


@report.command()
@click.option("--limit", default=10, help="Maximum number of results to display")
@click.option("--strategy", help="Filter by strategy name")
@click.option("--symbol", help="Filter by trading symbol")
def list(limit: int, strategy: str, symbol: str):
    """
    List all available backtest results.

    Results are displayed in a formatted table with key metrics.
    """
    try:
        store = ResultsStore()

        # Get results with optional filtering
        if strategy:
            results = store.find_by_strategy(strategy)
        elif symbol:
            results = store.find_by_symbol(symbol)
        else:
            results = store.list(limit=limit)

        if not results:
            console.print("📭 No backtest results found", style="yellow")
            console.print("\n💡 Run a backtest to create results:")
            console.print(
                "   uv run python -m src.cli backtest run --strategy sma --symbol AAPL ..."
            )
            return

        # Display results table
        _display_results_table(results)

        # Show storage info
        info = store.get_storage_info()
        console.print(
            f"\n💾 Storage: {info['result_count']} results, "
            f"{info['total_size_mb']}MB in {info['storage_dir']}"
        )

    except ResultsStoreError as e:
        console.print(f"❌ Error listing results: {e}", style="red bold")
    except Exception as e:
        console.print(f"❌ Unexpected error: {e}", style="red bold")


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

    RESULT_ID: Unique identifier of the backtest result
    """
    try:
        # Load result from store
        store = ResultsStore()
        result = store.get(result_id)

        # Create output directory
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        console.print(f"📦 Exporting all formats for {result_id[:8]} to {output_dir}/")

        # Export text report
        text_file = output_path / f"{result_id}_report.txt"
        _generate_text_report(result, str(text_file))

        # Export CSV
        csv_file = output_path / f"{result_id}_report.csv"
        _generate_csv_report(result, str(csv_file))

        # Export JSON
        json_file = output_path / f"{result_id}_report.json"
        _generate_json_report(result, str(json_file))

        # Display summary of exported files
        console.print("\n✅ Export complete!", style="green bold")
        console.print("\nExported files:")
        console.print(f"  📄 Text:  {text_file.name}")
        console.print(f"  📊 CSV:   {csv_file.name}")
        console.print(f"  📋 JSON:  {json_file.name}")

    except ResultNotFoundError:
        console.print(f"❌ Result not found: {result_id}", style="red bold")
    except ResultsStoreError as e:
        console.print(f"❌ Error loading result: {e}", style="red bold")
    except Exception as e:
        console.print(f"❌ Error exporting: {e}", style="red bold")


# Helper functions


def _display_summary_panel(result):
    """Display summary panel with key metrics."""
    summary = result.get_summary_dict()

    summary_text = f"""
    [bold cyan]Backtest ID:[/] {summary["backtest_id"][:12]}...
    [bold cyan]Strategy:[/] {summary["strategy"]}
    [bold cyan]Symbol:[/] {summary["symbol"]}
    [bold cyan]Period:[/] {summary["period"]}

    [bold green]Total Return:[/] ${summary["total_return"]}
    [bold]Final Balance:[/] ${summary["final_balance"]}
    [bold]Total Trades:[/] {summary["total_trades"]}
    [bold]Win Rate:[/] {summary["win_rate"]}
    [bold]Sharpe Ratio:[/] {summary["sharpe_ratio"] or "N/A"}
    [bold]Max Drawdown:[/] {_format_percentage(summary["max_drawdown"])}
    """

    panel = Panel(
        summary_text.strip(),
        title="📊 Backtest Performance Summary",
        border_style="blue",
        padding=(1, 2),
    )
    console.print(panel)


def _display_metrics_table(result):
    """Display detailed metrics table."""
    table = Table(title="Performance Metrics", style="cyan")
    table.add_column("Metric", style="white", no_wrap=True)
    table.add_column("Value", style="green")

    metrics = [
        ("Sharpe Ratio", _format_number(result.sharpe_ratio)),
        ("Sortino Ratio", _format_number(result.sortino_ratio)),
        ("Calmar Ratio", _format_number(result.calmar_ratio)),
        ("Profit Factor", _format_number(result.profit_factor)),
        ("Volatility", _format_percentage(result.volatility)),
        ("Total Return", f"${result.total_return}"),
        ("Winning Trades", str(result.winning_trades)),
        ("Losing Trades", str(result.losing_trades)),
        ("Avg Win", f"${result.avg_win}" if result.avg_win else "N/A"),
        ("Avg Loss", f"${result.avg_loss}" if result.avg_loss else "N/A"),
    ]

    for metric, value in metrics:
        table.add_row(metric, value)

    console.print(table)


def _display_results_table(results):
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

    for result in results:
        table.add_row(
            result["result_id"][:12],
            _format_timestamp(result["timestamp"]),
            result["strategy"],
            result["symbol"],
            result["total_return"],
            str(result["total_trades"]),
            _format_percentage(result["win_rate"]),
            _format_number(result["sharpe_ratio"]),
        )

    console.print(table)


def _generate_text_report(result, output_path=None):
    """Generate text report using TextReportGenerator."""
    generator = TextReportGenerator()

    # Prepare metrics dictionary
    metrics = {
        "total_return": float(result.total_return) / 100.0,  # Convert to decimal
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "max_drawdown": result.max_drawdown,
        "calmar_ratio": result.calmar_ratio,
        "volatility": result.volatility,
        "profit_factor": result.profit_factor,
        "win_rate": result.win_rate / 100.0,  # Convert to decimal
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "avg_win": float(result.avg_win) if result.avg_win else 0.0,
        "avg_loss": float(result.avg_loss) if result.avg_loss else 0.0,
        "largest_win": float(result.largest_win),
        "largest_loss": float(result.largest_loss),
        "final_balance": float(result.final_balance),
        "expectancy": result.expectancy,
    }

    # Generate report
    report_content = generator.generate_performance_report(metrics)

    if output_path:
        # Save to file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        console.print(f"✅ Text report saved to {output_path}", style="green")
    else:
        # Display to console
        console.print(report_content)


def _generate_csv_report(result, output_path):
    """Generate CSV report using CSVExporter."""
    exporter = CSVExporter()

    # Export metrics
    metrics = result.to_dict()["summary"]
    success = exporter.export_metrics(metrics, output_path)

    if success:
        console.print(f"✅ CSV report exported to {output_path}", style="green")
    else:
        console.print("❌ Failed to export CSV report", style="red")


def _generate_json_report(result, output_path):
    """Generate JSON report using JSONExporter."""
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

    # Get full result dictionary
    result_data = result.to_dict()

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


def _format_timestamp(value) -> str:
    """Format timestamp."""
    if not value:
        return "N/A"
    try:
        from datetime import datetime

        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = value
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)[:16]
