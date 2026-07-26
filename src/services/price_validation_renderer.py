"""Stdout renderer for PriceValidationReport.

Produces a per-ticker rich.Table (matched days, mean/max deviation, verdict)
plus a per-ticker ``❌ TOLERANCE BREACH`` line with notes on any failure, or
``✅ ALL TICKERS PASSED`` on a full pass. Mirrors ``comparison_renderer.py``.
"""

import io

from rich.console import Console
from rich.table import Table

from src.models.price_validation_report import PriceValidationReport


def render_price_validation_table(report: PriceValidationReport) -> str:
    """Render a PriceValidationReport as a colored table string.

    Args:
        report: The price validation report to render.

    Returns:
        Rendered string suitable for printing to stdout.
    """
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=120)

    table = Table(title=f"FMP Price Validation: {report.catalog_name}")
    table.add_column("Ticker", style="cyan", no_wrap=True)
    table.add_column("Instrument", style="white")
    table.add_column("Matched Days", justify="right")
    table.add_column("Mean Δ%", justify="right")
    table.add_column("Max Δ% (date)", justify="right")
    table.add_column("Verdict", no_wrap=True)

    for result in report.results:
        verdict = "✅ pass" if result.passed else "❌ breach"
        max_col = f"{result.max_abs_pct_diff:.4%}"
        if result.max_abs_pct_diff_date is not None:
            max_col += f" ({result.max_abs_pct_diff_date})"
        table.add_row(
            result.ticker,
            result.instrument_id,
            f"{result.matched_day_count:,}",
            f"{result.mean_abs_pct_diff:.4%}",
            max_col,
            verdict,
        )

    console.print(table)

    for result in report.results:
        if not result.passed:
            notes = "; ".join(result.notes) if result.notes else "no detail"
            console.print(f"❌ TOLERANCE BREACH: {result.ticker} — {notes}", style="red bold")

    if report.overall_passed:
        console.print("✅ ALL TICKERS PASSED", style="green bold")

    return buffer.getvalue()
