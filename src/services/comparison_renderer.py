"""Stdout renderer for ComparisonReport.

Produces a side-by-side rich.Table comparing legacy vs FirstRate metrics
plus a per-metric verdict column. On any tolerance breach, emits an
``❌ TOLERANCE BREACH`` line per failed metric so the user (or CI log
scraper) can spot failures without parsing the JSON.
"""

import io

from rich.console import Console
from rich.table import Table

from src.models.comparison_report import ComparisonReport


def render_comparison_table(report: ComparisonReport) -> str:
    """Render a ComparisonReport as a colored, side-by-side table string.

    Args:
        report: The comparison report to render.

    Returns:
        Rendered string suitable for printing to stdout. Includes a header,
        a metrics table, and one ``❌ TOLERANCE BREACH`` line per failed
        metric (or ``✅ ALL TOLERANCES PASSED`` on full pass).
    """
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=120)

    table = Table(title=f"Reference Comparison: {report.dataset}")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Legacy CSV", style="white")
    table.add_column("FirstRate", style="white")
    table.add_column("Δ (abs)", style="white")
    table.add_column("Δ (%)", style="white")
    table.add_column("Verdict", style="white", no_wrap=True)

    legacy = report.legacy_metrics
    firstrate = report.firstrate_metrics

    bar_verdict = "✅ pass" if report.bar_count_passed else "❌ breach"
    table.add_row(
        "Bar Count",
        f"{legacy.bar_count:,}",
        f"{firstrate.bar_count:,}",
        f"{abs(legacy.bar_count - firstrate.bar_count):,}",
        f"{report.bar_count_delta * 100:.4f}%",
        bar_verdict,
    )

    trade_verdict = "✅ pass" if report.trade_count_passed else "❌ breach"
    table.add_row(
        "Trade Count",
        f"{legacy.total_trades:,}",
        f"{firstrate.total_trades:,}",
        f"{report.trade_count_delta:,}",
        "—",
        trade_verdict,
    )

    pnl_verdict = "✅ pass" if report.pnl_passed else "❌ breach"
    table.add_row(
        "Total PnL",
        f"{legacy.total_pnl:,.2f}",
        f"{firstrate.total_pnl:,.2f}",
        f"{abs(legacy.total_pnl - firstrate.total_pnl):,.2f}",
        f"{report.pnl_delta_pct * 100:.4f}%",
        pnl_verdict,
    )

    table.add_row(
        "Final Balance",
        f"{legacy.final_balance:,.2f}",
        f"{firstrate.final_balance:,.2f}",
        f"{abs(legacy.final_balance - firstrate.final_balance):,.2f}",
        "—",
        "—",
    )

    console.print(table)

    if not report.bar_count_passed:
        console.print(
            f"❌ TOLERANCE BREACH: bar_count Δ={report.bar_count_delta:.4%} (threshold 0.50%)",
            style="red bold",
        )
    if not report.trade_count_passed:
        console.print(
            f"❌ TOLERANCE BREACH: trade_count Δ={report.trade_count_delta} (threshold 0)",
            style="red bold",
        )
    if not report.pnl_passed:
        console.print(
            f"❌ TOLERANCE BREACH: pnl Δ={report.pnl_delta_pct:.4%} (threshold 0.10%)",
            style="red bold",
        )
    if report.overall_passed:
        console.print("✅ ALL TOLERANCES PASSED", style="green bold")

    return buffer.getvalue()
