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
from src.services.firstrate.instrument_mapper import InstrumentMapper
from src.services.metadata.backtestable import NonBacktestableTicker, total_bars
from src.services.metadata.coverage_report import QualificationCoverage, VenueCoverage
from src.services.metadata.qualification_sync import (
    QualificationSyncResult,
    sync_resolved_qualifications,
)
from src.services.metadata.unresolved_report import unresolved_reason
from src.services.metadata.venue_exclusions import (
    VenueExclusionError,
    VenueExclusionMergeResult,
    drop_overridden_exclusions,
    load_venue_exclusions,
    merge_venue_exclusions,
)
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
@click.option(
    "--sync-catalog/--no-sync-catalog",
    default=True,
    help=(
        "Also converge catalog_instruments.nautilus_id onto the venue verdict "
        "(default on — without it the coverage gate can pass while every ticker "
        "is still unloadable by backtest_loader)."
    ),
)
@click.option(
    "--exclusions/--no-exclusions",
    default=True,
    help="Also apply venue_exclusions.csv (default on).",
)
@click.option(
    "--catalog",
    default=None,
    help="Catalog whose identity rows are synced (default: firstrate_catalog_name).",
)
def apply_overrides(sync_catalog: bool, exclusions: bool, catalog: Optional[str]) -> None:
    """Apply every venue decision: overrides, exclusions, and qualification sync.

    The metadata-refresh path: fill venue_overrides.csv from the ``metadata
    unresolved`` worklist (and venue_exclusions.csv for tickers nothing can
    qualify), then apply the corrections without re-importing bars.

    All three steps run in one transaction. That coupling is deliberate — a venue
    written without the matching catalog identity produces a green gate over a
    universe no backtest can load, which is the exact defect this command exists to
    prevent. The name is kept for continuity; it applies all venue decisions.
    """
    settings = get_settings().firstrate
    catalog_name = catalog or settings.firstrate_catalog_name
    override_path = Path(settings.firstrate_venue_overrides_path)
    exclusion_path = Path(settings.firstrate_venue_exclusions_path)

    try:
        overrides = load_venue_overrides(override_path)
        exclusion_records = load_venue_exclusions(exclusion_path) if exclusions else {}
    except (VenueOverrideError, VenueExclusionError) as exc:
        console.print(f"[yellow]Venue decisions not applied — {escape(str(exc))}[/yellow]")
        return

    exclusion_records, shadowed = drop_overridden_exclusions(exclusion_records, overrides)
    if not overrides and not exclusion_records and not sync_catalog:
        console.print(
            f"[green]✓ No venue decisions to apply ({escape(str(override_path))} "
            f"is empty/absent).[/green]"
        )
        return

    try:
        with get_sync_session() as session:
            meta_repo = SyncInstrumentMetadataRepository(session)
            now = datetime.now(timezone.utc)
            override_result = merge_venue_overrides(overrides, meta_repo, now)
            exclusion_result = merge_venue_exclusions(exclusion_records, meta_repo, now)
            sync_result = None
            if sync_catalog:
                sync_result = sync_resolved_qualifications(
                    meta_repo=meta_repo,
                    catalog_repo=SyncCatalogInstrumentRepository(session),
                    mapper=InstrumentMapper(SyncCatalogInstrumentRepository(session)),
                    catalog_name=catalog_name,
                )
            session.commit()
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        console.print(f"[yellow]Metadata DB not available — {escape(str(exc))}[/yellow]")
        return

    _render_override_summary(override_result)
    _render_exclusion_summary(exclusion_result, shadowed)
    if sync_result is not None:
        _render_qualification_summary(sync_result, catalog_name)


def _render_override_summary(result: VenueOverrideMergeResult) -> None:
    """Print a one-line venue-override merge summary (+ unmatched tickers, if any)."""
    console.print(
        f"Venue overrides: {result.applied} applied, "
        f"{result.unchanged} unchanged, {len(result.unmatched)} unmatched"
    )
    if result.unmatched:
        joined = ", ".join(escape(t) for t in result.unmatched)
        console.print(f"[yellow]Unmatched (no metadata row): {joined}[/yellow]")


def _render_exclusion_summary(result: VenueExclusionMergeResult, shadowed: list[str]) -> None:
    """Print the exclusion-register summary, staying silent when the register is empty."""
    if result.total == 0 and not shadowed:
        return
    console.print(
        f"Venue exclusions: {result.applied} applied, "
        f"{result.unchanged} unchanged, {len(result.unmatched)} unmatched"
    )
    if result.unmatched:
        joined = ", ".join(escape(t) for t in result.unmatched)
        console.print(f"[yellow]Unmatched (no metadata row): {joined}[/yellow]")
    if shadowed:
        joined = ", ".join(escape(t) for t in shadowed)
        console.print(
            f"[yellow]Exclusions overridden by a resolved venue (override wins): {joined}[/yellow]"
        )


def _render_qualification_summary(result: QualificationSyncResult, catalog_name: str) -> None:
    """Print the catalog-identity sync summary (+ any metadata row lacking a catalog row)."""
    console.print(
        f"Catalog qualification ({escape(catalog_name)}): {result.synced} synced, "
        f"{result.cleared} cleared, {result.unchanged} unchanged"
    )
    if result.missing_row:
        shown = ", ".join(escape(t) for t in result.missing_row[:10])
        more = f" (+{len(result.missing_row) - 10} more)" if len(result.missing_row) > 10 else ""
        console.print(
            f"[yellow]Resolved but no catalog_instruments row: {shown}{more} — "
            f"company profiles not loaded for this catalog?[/yellow]"
        )


@metadata.command("coverage")
@click.option(
    "--gate/--no-gate",
    default=False,
    help="Exit non-zero unless venue coverage is 100% and every resolved ticker is qualified.",
)
@click.option(
    "--catalog",
    default=None,
    help="Catalog to check qualification against (default: firstrate_catalog_name).",
)
def coverage(gate: bool, catalog: Optional[str]) -> None:
    """Report venue coverage, catalog qualification, and the completeness verdict.

    Two terms, both required to pass:

    \b
    1. Venue coverage — no ticker left VENUE_UNRESOLVED. No venue is ever guessed
       to make this pass; a ticker nothing can qualify leaves via the audited
       exclusion register instead.
    2. Catalog qualification — every RESOLVED ticker carries a nautilus_id. Without
       this term the gate passed while backtest_loader rejected the whole universe,
       because the venue and the identity live in different tables.

    With --gate the process exits 1 when INCOMPLETE, and 2 when the DB cannot be
    evaluated, so CI never mistakes "unreachable" for "passed".
    """
    catalog_name = catalog or get_settings().firstrate.firstrate_catalog_name
    try:
        with get_sync_session() as session:
            meta_repo = SyncInstrumentMetadataRepository(session)
            cat_repo = SyncCatalogInstrumentRepository(session)
            counts = meta_repo.count_by_status()
            qual = QualificationCoverage(
                catalog=catalog_name,
                resolved=counts.get(ResolutionStatus.RESOLVED, 0),
                gap=cat_repo.count_unqualified_resolved(catalog_name),
            )
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        # DB unset / unreachable / missing-table — degrade to a warning, never a
        # traceback. Under --gate an unevaluable gate must NOT read as passing.
        console.print(f"[yellow]Metadata DB not available — {escape(str(exc))}[/yellow]")
        if gate:
            sys.exit(2)
        return
    cov = VenueCoverage.from_counts(counts)
    _render_coverage(cov)
    _render_qualification_coverage(qual)
    if gate and not (cov.is_complete and qual.is_complete):
        sys.exit(1)


def _render_qualification_coverage(qual: QualificationCoverage) -> None:
    """Render the qualification term of the gate (no exit logic)."""
    if qual.is_complete:
        console.print(
            f"[green]✓ QUALIFIED — all {qual.resolved} resolved ticker(s) carry a "
            f"nautilus_id in '{escape(qual.catalog)}'[/green]"
        )
        return
    console.print(
        f"[red]✗ UNQUALIFIED — {qual.gap} of {qual.resolved} resolved ticker(s) in "
        f"'{escape(qual.catalog)}' have no nautilus_id (gate FAIL)[/red]"
    )
    console.print(
        "These tickers have a venue but no catalog identity, so backtest_loader "
        "will reject them. Run 'metadata apply-overrides' to sync qualification."
    )


def _render_coverage(cov: VenueCoverage) -> None:
    """Render the venue-coverage breakdown + PASS/FAIL verdict (no exit logic)."""
    table = Table(title="Venue coverage")
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("RESOLVED", str(cov.resolved))
    table.add_row("VENUE_UNRESOLVED", str(cov.venue_unresolved))
    table.add_row("UNRESOLVED", str(cov.unresolved))
    table.add_row("EXCLUDED", str(cov.excluded))
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
    if cov.excluded > 0:
        # Always printed when non-zero: an exclusion is a permitted gate exit, so
        # the register's size must stay in front of whoever reads the verdict.
        console.print(
            f"[yellow]Excluded (EXCLUDED): {cov.excluded} — audited in "
            f"venue_exclusions.csv; bars retained, never backtestable[/yellow]"
        )
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
