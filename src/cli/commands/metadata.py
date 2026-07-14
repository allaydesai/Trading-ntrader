"""CLI commands for instrument metadata inspection (Story 3.2+).

Read-only reports over the local ``instrument_metadata`` cache table. No network
or provider call — the resolved metadata is produced by the Epic-1 resolution
service and the Story 3.1 import qualification.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from sqlalchemy.exc import SQLAlchemyError

from src.config import get_settings
from src.db.exceptions import DatabaseConnectionError
from src.db.models.instrument_metadata import InstrumentMetadata
from src.db.repositories.catalog_instrument_repository import (
    SyncCatalogInstrumentRepository,
)
from src.db.repositories.instrument_metadata_repository_sync import (
    SyncInstrumentMetadataRepository,
)
from src.db.session_sync import get_sync_session
from src.models.instrument_metadata import ResolutionStatus
from src.services.metadata.backtestable import NonBacktestableTicker, total_bars
from src.services.metadata.coverage_report import VenueCoverage
from src.services.metadata.unresolved_report import unresolved_reason
from src.services.metadata.venue_overrides import (
    VenueOverrideError,
    VenueOverrideMergeResult,
    load_venue_overrides,
    merge_venue_overrides,
)

console = Console()


@click.group()
def metadata() -> None:
    """Instrument metadata inspection commands."""


@metadata.command("unresolved")
def unresolved() -> None:
    """List every ticker whose venue is unresolved (VENUE_UNRESOLVED).

    A worklist for driving the ETF universe to 100% venue coverage — each row
    carries a derived reason and is the basis for filling in venue_overrides.csv
    (Story 3.3).
    """
    try:
        with get_sync_session() as session:
            rows = SyncInstrumentMetadataRepository(session).list_by_status(
                ResolutionStatus.VENUE_UNRESOLVED
            )
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        # DB unset (RuntimeError), unreachable/missing-table/bad-URL (SQLAlchemyError),
        # or a translated connection failure — degrade to a warning, never a traceback.
        console.print(f"[yellow]Metadata DB not available — {escape(str(exc))}[/yellow]")
        return
    _render_unresolved(rows)


def _render_unresolved(rows: list[InstrumentMetadata]) -> None:
    """Render the unresolved-venue rows as a Rich table (or an all-clear line)."""
    if not rows:
        console.print("[green]✓ All venues resolved — no unresolved-venue tickers.[/green]")
        return
    table = Table(title="Unresolved-venue tickers")
    table.add_column("Ticker", style="cyan")
    table.add_column("Provider")
    table.add_column("Reason")
    for row in rows:
        # ticker/provider are DB-sourced (String(20)) — escape so a stray Rich
        # markup metacharacter can't raise MarkupError or mis-render.
        table.add_row(escape(row.ticker), escape(row.metadata_provider), unresolved_reason(row))
    console.print(table)
    console.print(f"{len(rows)} ticker(s) with unresolved venue")
    console.print("Add the correct venue for each in venue_overrides.csv (Story 3.3) to resolve.")


@metadata.command("apply-overrides")
def apply_overrides() -> None:
    """Merge venue_overrides.csv into the metadata store, with precedence (Story 3.3).

    The metadata-refresh path: fill the CSV from the ``metadata unresolved``
    worklist, then apply the corrections without re-importing bars. Each override
    flips a matching row to RESOLVED; re-running is idempotent.
    """
    path = Path(get_settings().firstrate.firstrate_venue_overrides_path)
    try:
        overrides = load_venue_overrides(path)
    except VenueOverrideError as exc:
        console.print(f"[yellow]Venue overrides not applied — {escape(str(exc))}[/yellow]")
        return
    if not overrides:
        console.print(
            f"[green]✓ No venue overrides to apply ({escape(str(path))} is empty/absent).[/green]"
        )
        return
    try:
        with get_sync_session() as session:
            result = merge_venue_overrides(
                overrides,
                SyncInstrumentMetadataRepository(session),
                datetime.now(timezone.utc),
            )
            session.commit()
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        console.print(f"[yellow]Metadata DB not available — {escape(str(exc))}[/yellow]")
        return
    _render_override_summary(result)


def _render_override_summary(result: VenueOverrideMergeResult) -> None:
    """Print a one-line venue-override merge summary (+ unmatched tickers, if any)."""
    console.print(
        f"Venue overrides: {result.applied} applied, "
        f"{result.unchanged} unchanged, {len(result.unmatched)} unmatched"
    )
    if result.unmatched:
        joined = ", ".join(escape(t) for t in result.unmatched)
        console.print(f"[yellow]Unmatched (no metadata row): {joined}[/yellow]")


@metadata.command("coverage")
@click.option(
    "--gate/--no-gate",
    default=False,
    help="Exit non-zero unless venue coverage is 100% (for CI/automation).",
)
def coverage(gate: bool) -> None:
    """Report venue coverage and the completeness verdict (Story 3.4).

    Answers "is any ticker missing a venue?" from a single indexed grouped
    count. A VENUE_UNRESOLVED row fails the gate — no venue is ever guessed to
    make it pass. With --gate the process exits 1 when INCOMPLETE (and 2 when
    the DB cannot be evaluated, so CI never mistakes "unreachable" for "passed").
    """
    try:
        with get_sync_session() as session:
            counts = SyncInstrumentMetadataRepository(session).count_by_status()
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        # DB unset / unreachable / missing-table — degrade to a warning, never a
        # traceback. Under --gate an unevaluable gate must NOT read as passing.
        console.print(f"[yellow]Metadata DB not available — {escape(str(exc))}[/yellow]")
        if gate:
            sys.exit(2)
        return
    cov = VenueCoverage.from_counts(counts)
    _render_coverage(cov)
    if gate and not cov.is_complete:
        sys.exit(1)


def _render_coverage(cov: VenueCoverage) -> None:
    """Render the venue-coverage breakdown + PASS/FAIL verdict (no exit logic)."""
    table = Table(title="Venue coverage")
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("RESOLVED", str(cov.resolved))
    table.add_row("VENUE_UNRESOLVED", str(cov.venue_unresolved))
    table.add_row("UNRESOLVED", str(cov.unresolved))
    console.print(table)
    console.print(f"Total metadata rows: {cov.total}")
    # Clamp the *displayed* percent so it can never read 100.0% while the verdict
    # is FAIL: f"{x:.1f}" rounds 99.95–99.99…% up to "100.0%". is_complete (the
    # exact float) drives the exit code; this only keeps the printed line honest.
    pct = cov.coverage_pct
    if not cov.is_complete and pct >= 99.95:
        pct = 99.9
    console.print(f"Venue coverage: {pct:.1f}% ({cov.resolved}/{cov.decided} decided)")
    console.print(f"Unresolved venues (VENUE_UNRESOLVED): {cov.venue_unresolved}")
    if cov.unresolved > 0:
        console.print(f"Not yet attempted (UNRESOLVED): {cov.unresolved}")
    if cov.total == 0:
        console.print("[yellow]No metadata rows yet — nothing to gate (vacuous PASS).[/yellow]")
    elif cov.decided == 0:
        # Only never-attempted rows — resolution never ran, so 100%/PASS is vacuous.
        console.print(
            "[yellow]No venues decided yet — resolution has not run on any row; "
            "PASS is vacuous.[/yellow]"
        )
    if cov.is_complete:
        console.print("[green]✓ COMPLETE — venue coverage 100% (gate PASS)[/green]")
    else:
        console.print(
            f"[red]✗ INCOMPLETE — {cov.venue_unresolved} ticker(s) VENUE_UNRESOLVED "
            f"(gate FAIL)[/red]"
        )


@metadata.command("backtestable")
@click.option(
    "--catalog",
    default=None,
    help="Catalog to read bar-retention evidence from (default: firstrate_catalog_name).",
)
def backtestable(catalog: Optional[str]) -> None:
    """Enumerate the backtestable universe and flag non-backtestable tickers (Story 3.5).

    Backtestable ⟺ a RESOLVED venue. Tickers with VENUE_UNRESOLVED are excluded
    and flagged non-backtestable — but their imported bars are retained (excluded
    ≠ dropped). Resolving a venue (venue_overrides.csv, Story 3.3) transitions a
    ticker back in with no re-import.
    """
    catalog_name = catalog or get_settings().firstrate.firstrate_catalog_name
    try:
        with get_sync_session() as session:
            meta_repo = SyncInstrumentMetadataRepository(session)
            cat_repo = SyncCatalogInstrumentRepository(session)
            counts = meta_repo.count_by_status()
            unresolved_rows = meta_repo.list_by_status(ResolutionStatus.VENUE_UNRESOLVED)
            flagged = _collect_non_backtestable(unresolved_rows, cat_repo, catalog_name)
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        console.print(f"[yellow]Metadata DB not available — {escape(str(exc))}[/yellow]")
        return
    _render_backtestable(
        catalog_name,
        counts.get(ResolutionStatus.RESOLVED, 0),
        counts.get(ResolutionStatus.UNRESOLVED, 0),
        flagged,
    )


def _collect_non_backtestable(
    rows: list[InstrumentMetadata],
    cat_repo: SyncCatalogInstrumentRepository,
    catalog_name: str,
) -> list[NonBacktestableTicker]:
    """Pair each unresolved ticker with the bars still retained for it (0 if no row)."""
    flagged: list[NonBacktestableTicker] = []
    for row in rows:
        inst = cat_repo.get_by_ticker(catalog_name, row.ticker)
        bars = total_bars(inst) if inst is not None else 0
        flagged.append(NonBacktestableTicker(row.ticker, bars))
    return flagged


def _render_backtestable(
    catalog_name: str,
    backtestable_count: int,
    never_attempted: int,
    flagged: list[NonBacktestableTicker],
) -> None:
    """Render the backtestable count + the flagged non-backtestable set (no exit logic)."""
    total = backtestable_count + never_attempted + len(flagged)
    if total == 0:
        console.print("[yellow]No metadata rows yet — nothing to enumerate.[/yellow]")
        return
    if not flagged and never_attempted == 0:
        console.print(
            f"[green]✓ All venue-resolved — backtestable universe = {backtestable_count} "
            f"ticker(s), 0 non-backtestable.[/green]"
        )
        return
    if flagged:
        table = Table(title="Non-backtestable tickers (venue unresolved)")
        table.add_column("Ticker", style="cyan")
        table.add_column(f"Bars retained ({escape(catalog_name)})", justify="right")
        for t in flagged:
            table.add_row(escape(t.ticker), str(t.bars_retained))
        console.print(table)
    console.print(
        f"Backtestable: {backtestable_count} · Non-backtestable (venue unresolved): "
        f"{len(flagged)} (bars retained, not dropped)"
    )
    if never_attempted > 0:
        # UNRESOLVED = resolution never ran; also non-backtestable, but not the
        # venue-unresolved flagged set (no override fixes a not-yet-attempted row).
        console.print(
            f"[yellow]Not yet attempted (UNRESOLVED): {never_attempted} — resolution has "
            f"not run on these; they are not backtestable either.[/yellow]"
        )
    if flagged:
        console.print(
            "Bars are kept on disk — resolve each venue in venue_overrides.csv (Story 3.3) "
            "to make it backtestable."
        )
