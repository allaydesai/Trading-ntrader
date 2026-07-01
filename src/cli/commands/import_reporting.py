"""Summary and progress reporting for the FirstRate import command.

Extracted from ``src.cli.commands.import_data`` so the CLI command file stays
under the 500-line size limit. These helpers render progress lines and Rich
summary tables; they hold no import logic.
"""

from typing import TYPE_CHECKING

import click
import structlog
from rich.console import Console
from rich.table import Table

from src.models.catalog import ImportResult
from src.models.instrument_metadata import InstrumentMetadata, ResolutionSummary

if TYPE_CHECKING:
    from src.services.firstrate.supplementary_loader import SupplementaryLoadResult

console = Console()
logger = structlog.get_logger(__name__)


def determine_exit_code(results: list[ImportResult]) -> int:
    """Determine CLI exit code from import results.

    Story 1-7 (AC-5): skipped tickers are a success outcome and must not
    trigger exit code 1. Only ``status == "failed"`` flips to 1. Exit code
    2 is reserved for fatal errors in ``_run_import``.

    Args:
        results: List of import results.

    Returns:
        0 if all success/skipped, 1 if any failures.
    """
    if any(r.status == "failed" for r in results):
        return 1
    return 0


def _bucket_results(results: list[ImportResult]) -> dict[str, int]:
    """Return the four Story 1-7 outcome buckets plus totals.

    Counts by ``r.outcome`` so future outcome additions are cheap. Falls
    back to ``r.status`` only for the "failed" bucket (failed results may
    not carry an outcome — classification might never have run).
    """
    new = sum(1 for r in results if r.outcome == "new")
    reimported = sum(1 for r in results if r.outcome == "reimported")
    skipped = sum(1 for r in results if r.outcome == "skipped")
    failed = sum(1 for r in results if r.status == "failed")
    # Story 2.5 AC4: tickers carrying non-blocking verification flags
    # (OHLC-sanity violations) on an otherwise-successful import. Scoped to
    # non-failed results so a ticker that also failed a blocking check (row
    # count / sample point) is reported once — under Failures — rather than
    # double-counted here. Flagged tickers' violations are still logged
    # (ohlc_sanity_flagged) regardless of final status, so nothing is silent.
    flagged = sum(1 for r in results if r.warnings and r.status != "failed")
    # "Total processed" (AC-4) excludes skipped + failed.
    processed_rows = sum(r.row_count for r in results if r.outcome in ("new", "reimported"))
    return {
        "total": len(results),
        "new": new,
        "reimported": reimported,
        "skipped": skipped,
        "failed": failed,
        "flagged": flagged,
        "total_rows": processed_rows,
        "total_processed": new + reimported,
    }


def build_summary_text(results: list[ImportResult]) -> str:
    """Build plain-text summary of import results.

    Story 1-7: surface New / Re-imported / Skipped / Failed counts plus
    a Total processed line (= new + reimported). The skipped bucket is
    not counted as "processed" but IS counted in Total tickers.

    Args:
        results: List of import results.

    Returns:
        Formatted summary string.
    """
    buckets = _bucket_results(results)

    lines = [
        "",
        "Import Summary",
        "━" * 27,
        f"Total tickers:   {buckets['total']}",
        f"New:             {buckets['new']}",
        f"Re-imported:     {buckets['reimported']}",
        f"Skipped:         {buckets['skipped']}",
        f"Failed:          {buckets['failed']}",
        f"Flagged (OHLC):  {buckets['flagged']}",
        f"Total rows:      {buckets['total_rows']:,}",
        f"Total processed: {buckets['total_processed']}",
    ]

    failed_results = [r for r in results if r.status == "failed"]
    if failed_results:
        lines.append("")
        lines.append("Failures:")
        for r in failed_results:
            lines.append(f"  {r.ticker} — {r.error or 'Unknown error'}")

    # Story 2.5 AC4: surface non-blocking verification flags (OHLC sanity) so a
    # high<low / negative-volume row never silently passes. Scoped to non-failed
    # tickers — a failed ticker is reported under Failures, not twice.
    flagged_results = [r for r in results if r.warnings and r.status != "failed"]
    if flagged_results:
        lines.append("")
        lines.append("Verification Warnings:")
        for r in flagged_results:
            for issue in r.warnings:
                lines.append(f"  {r.ticker} — {issue}")

    return "\n".join(lines)


def _print_progress_line(result: ImportResult, timeframe: str) -> None:
    """Print a single ticker progress line.

    Three-way glyph split (Story 1-7, AC-4): ``✓`` for successful
    imports (new or reimported), ``⟳`` for skipped tickers so operators
    can visually scan a long idempotent re-run, and ``✗`` for failures.

    Args:
        result: Import result for one ticker.
        timeframe: Timeframe spec used for this import.
    """
    if result.status == "skipped":
        click.echo(f"{result.ticker} — {timeframe} — skipped (already complete) ⟳")
    elif result.status == "success":
        # Append a ⚠ when the import carried non-blocking OHLC-sanity flags
        # (Story 2.5 AC4) so a long run is scannable for verification issues.
        flag = " ⚠ verification warnings" if result.warnings else ""
        click.echo(f"{result.ticker} — {timeframe} — {result.row_count:,} rows ✓{flag}")
    else:
        click.echo(f"{result.ticker} — {timeframe} — {result.error or 'Unknown error'} ✗")


def _print_summary(
    results: list[ImportResult],
    supplementary: list["SupplementaryLoadResult"] | None = None,
) -> None:
    """Print Rich-formatted summary table.

    Story 1-7 rows: Total tickers, New, Re-imported, Skipped, Failed,
    Total rows, Total processed. ``Total processed`` equals
    ``new + reimported`` per AC-4. When ``supplementary`` results are present
    (Story 4-1) an informational dividends/splits line is appended; it never
    affects the exit code.

    Args:
        results: All import results across timeframes.
        supplementary: Per-ticker dividend/split load results (Story 4-1).
    """
    buckets = _bucket_results(results)

    console.print()
    table = Table(title="Import Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total tickers", str(buckets["total"]))
    table.add_row("New", str(buckets["new"]))
    table.add_row("Re-imported", str(buckets["reimported"]))
    table.add_row("Skipped", str(buckets["skipped"]))
    table.add_row("Failed", str(buckets["failed"]))
    table.add_row("Flagged (OHLC)", str(buckets["flagged"]))
    table.add_row("Total rows", f"{buckets['total_rows']:,}")
    table.add_row("Total processed", str(buckets["total_processed"]))
    console.print(table)

    failed_results = [r for r in results if r.status == "failed"]
    if failed_results:
        console.print()
        fail_table = Table(title="Failures")
        fail_table.add_column("Ticker", style="red")
        fail_table.add_column("Reason", style="yellow")
        for r in failed_results:
            fail_table.add_row(r.ticker, r.error or "Unknown error")
        console.print(fail_table)

    # Story 2.5 AC4: non-blocking verification flags (OHLC sanity). Bars still
    # imported, but the violation is surfaced here so it never silently passes.
    # Scoped to non-failed tickers — a failed ticker is reported under Failures.
    flagged_results = [r for r in results if r.warnings and r.status != "failed"]
    if flagged_results:
        console.print()
        warn_table = Table(title="Verification Warnings")
        warn_table.add_column("Ticker", style="yellow")
        warn_table.add_column("Issue", style="yellow")
        for r in flagged_results:
            for issue in r.warnings:
                warn_table.add_row(r.ticker, issue)
        console.print(warn_table)

    _print_supplementary_summary(supplementary)


def _print_supplementary_summary(
    supplementary: list["SupplementaryLoadResult"] | None,
) -> None:
    """Print an informational dividends/splits summary line (Story 4-1).

    Counts tickers/records loaded and any isolated per-ticker failures. This
    output is informational only and must not affect ``determine_exit_code``.

    Args:
        supplementary: Per-ticker dividend/split load results, or None.
    """
    if not supplementary:
        return

    div_tickers = sum(1 for r in supplementary if r.dividend_count > 0)
    split_tickers = sum(1 for r in supplementary if r.split_count > 0)
    div_records = sum(r.dividend_count for r in supplementary)
    split_records = sum(r.split_count for r in supplementary)
    supp_failures = [r for r in supplementary if r.status == "failed"]

    console.print()
    supp_table = Table(title="Supplementary Data")
    supp_table.add_column("Metric", style="cyan")
    supp_table.add_column("Value", style="green")
    supp_table.add_row("Dividends", f"{div_tickers} tickers / {div_records} records")
    supp_table.add_row("Splits", f"{split_tickers} tickers / {split_records} records")
    supp_table.add_row("Failed (non-blocking)", str(len(supp_failures)))
    console.print(supp_table)

    if supp_failures:
        console.print()
        fail_table = Table(title="Supplementary Failures (non-blocking)")
        fail_table.add_column("Ticker", style="red")
        fail_table.add_column("Reason", style="yellow")
        for r in supp_failures:
            fail_table.add_row(r.ticker, r.error or "Unknown error")
        console.print(fail_table)


def build_resolution_summary_text(summary: ResolutionSummary) -> str:
    """Build a plain-text block of the per-import resolution counts (Story 1.6).

    Mirrors the ``build_summary_text`` style (title + ``━`` rule + aligned lines).
    Pure — returns a string and prints nothing.

    Args:
        summary: Aggregated resolution counters from ``ResolutionSummary.from_results``.

    Returns:
        Formatted resolution-summary string.
    """
    lines = [
        "",
        "Resolution Summary",
        "━" * 27,
        f"Resolved:         {summary.resolved}",
        f"Descriptive gaps: {summary.descriptive_gaps}",
        f"Venue unresolved: {summary.venue_unresolved}",
    ]
    return "\n".join(lines)


def _print_resolution_summary(summary: ResolutionSummary) -> None:
    """Print the Rich-formatted resolution summary table (Story 1.6).

    Mirrors the ``_print_summary`` Rich idiom (Metric/Value columns).

    Args:
        summary: Aggregated resolution counters.
    """
    console.print()
    table = Table(title="Resolution Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Resolved", str(summary.resolved))
    table.add_row("Descriptive gaps", str(summary.descriptive_gaps))
    table.add_row("Venue unresolved", str(summary.venue_unresolved))
    console.print(table)


def log_resolution_results(results: list[InstrumentMetadata]) -> None:
    """Emit one structlog outcome event per resolved ticker (Story 1.6, AC #2).

    The four-field schema (``ticker``, ``provider``, ``resolution_status``,
    ``error``) is fixed. ``error`` is always ``None`` here: the domain model
    carries no error string, and genuine per-ticker failures are already logged
    by ``InstrumentMetadataService.resolve_batch`` as ``metadata_resolution_failed``.
    These two complementary streams are not duplicated.

    Args:
        results: Already-resolved metadata (the output of ``resolve_batch``).
    """
    for r in results:
        logger.info(
            "metadata_resolution_outcome",
            ticker=r.ticker,
            provider=r.metadata_provider,
            resolution_status=r.resolution_status.value,
            error=None,
        )
