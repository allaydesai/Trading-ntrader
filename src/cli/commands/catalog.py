"""CLI commands for catalog maintenance.

A new group rather than an addition to ``data`` (685 lines) or ``import_data``
(732) — both already exceed the 500-line guideline.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from sqlalchemy.exc import SQLAlchemyError

from src.config import CatalogSettings, get_settings
from src.db.exceptions import DatabaseConnectionError
from src.db.repositories.catalog_instrument_repository import (
    SyncCatalogInstrumentRepository,
)
from src.db.repositories.instrument_metadata_repository_sync import (
    SyncInstrumentMetadataRepository,
)
from src.db.session_sync import get_sync_session
from src.models.instrument_metadata import ResolutionStatus
from src.services.firstrate.venue_restamp import execute_plan
from src.services.firstrate.venue_restamp_plan import RestampPlan, build_restamp_plan

console = Console()

_BYTES_PER_GIB = 1024**3


@click.group()
def catalog() -> None:
    """Catalog maintenance commands."""


def _load_targets(catalog_name: str) -> tuple[dict[str, str], frozenset[str]]:
    """Read the authoritative venue per ticker, plus the excluded set.

    The DB is the authority here, deliberately: this must run *after* the venue
    decisions have been applied, so the plan reflects adjudicated state rather than
    whatever a CSV happens to say at the moment.
    """
    with get_sync_session() as session:
        cat_repo = SyncCatalogInstrumentRepository(session)
        meta_repo = SyncInstrumentMetadataRepository(session)
        targets = {
            row.ticker: row.exchange
            for row in cat_repo.iter_by_catalog(catalog_name)
            if row.nautilus_id and row.exchange
        }
        excluded = frozenset(
            row.ticker for row in meta_repo.list_by_status(ResolutionStatus.EXCLUDED)
        )
    return targets, excluded


def _render_plan(plan: RestampPlan, *, workers: int, min_free_gb: float) -> None:
    """Print the plan and the disk arithmetic behind it."""
    table = Table(title=f"Restamp plan — catalog '{escape(plan.catalog)}'")
    table.add_column("Classification", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("To restamp", str(len(plan.actions)))
    table.add_row("No-op (venue unchanged)", str(plan.no_ops))
    table.add_row("Excluded (register)", str(len(plan.excluded)))
    table.add_row("Orphan (no venue on record)", str(len(plan.orphans)))
    table.add_row("Collision (target exists)", str(len(plan.collisions)))
    table.add_row("Unsafe (unexpected contents)", str(len(plan.unsafe)))
    table.add_row("Unrecognised name", str(len(plan.unparsed)))
    console.print(table)

    if plan.actions:
        moves = Table(title="Venue moves")
        moves.add_column("From → To", style="cyan")
        moves.add_column("Dirs", justify="right")
        moves.add_column("Size", justify="right")
        for (old, new), (count, size) in sorted(
            plan.moves_by_venue_pair().items(), key=lambda kv: -kv[1][1]
        ):
            moves.add_row(f"{old} → {new}", str(count), f"{size / _BYTES_PER_GIB:.2f} GiB")
        console.print(moves)

    console.print(f"Tickers affected: {plan.tickers_affected}")
    console.print(f"Bytes to rewrite: {plan.bytes_to_rewrite / _BYTES_PER_GIB:.2f} GiB")

    peak = plan.peak_extra_bytes(workers)
    free = shutil.disk_usage(plan.catalog_root).free
    headroom_ok = free - peak > min_free_gb * _BYTES_PER_GIB
    console.print(
        f"Peak extra disk @{workers} workers: {peak / _BYTES_PER_GIB:.2f} GiB · "
        f"free {free / _BYTES_PER_GIB:.1f} GiB · "
        + ("[green]OK[/green]" if headroom_ok else "[red]INSUFFICIENT[/red]")
    )

    for label, items in (
        ("Orphan", plan.orphans),
        ("Collision", plan.collisions),
        ("Unsafe", plan.unsafe),
        ("Unrecognised", plan.unparsed),
    ):
        if items:
            shown = ", ".join(escape(i) for i in items[:5])
            more = f" (+{len(items) - 5} more)" if len(items) > 5 else ""
            console.print(f"[yellow]{label}: {shown}{more}[/yellow]")


def _write_plan_json(plan: RestampPlan, path: Path) -> None:
    """Persist the plan — it doubles as the rollback manifest.

    Written even on a dry run. ``data/catalogs/`` is gitignored, so if the moves
    ever need undoing this file is the only record of what went where.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "catalog": plan.catalog,
        "catalog_root": str(plan.catalog_root),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "actions": [
            {
                "ticker": a.ticker,
                "old_venue": a.old_venue,
                "new_venue": a.new_venue,
                "spec": a.spec,
                "src_dir": str(a.src_dir),
                "dst_dir": str(a.dst_dir),
                "old_bar_type": a.old_bar_type,
                "new_bar_type": a.new_bar_type,
                "total_bytes": a.total_bytes,
            }
            for a in plan.actions
        ],
        "no_ops": plan.no_ops,
        "excluded": list(plan.excluded),
        "orphans": list(plan.orphans),
        "collisions": list(plan.collisions),
        "unsafe": list(plan.unsafe),
        "unparsed": list(plan.unparsed),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    console.print(f"Plan → {escape(str(path))}")


@catalog.command("restamp-venues")
@click.option("--catalog", "catalog_name", default=None, help="Catalog to re-stamp.")
@click.option("--dry-run", is_flag=True, help="Print the plan and write it; move nothing.")
@click.option("--workers", default=6, show_default=True, help="Parallel rewrite processes.")
@click.option("--min-free-gb", default=20.0, show_default=True, help="Required disk headroom.")
@click.option("--limit-tickers", default=None, help="Comma-separated subset (staged rollout).")
@click.option("--deep-verify", is_flag=True, help="Also byte-hash first/last row groups.")
@click.option("--plan-out", default=None, help="Where to write the plan JSON.")
@click.option("--force", is_flag=True, help="Proceed despite orphan/collision/unsafe entries.")
def restamp_venues(
    catalog_name: Optional[str],
    dry_run: bool,
    workers: int,
    min_free_gb: float,
    limit_tickers: Optional[str],
    deep_verify: bool,
    plan_out: Optional[str],
    force: bool,
) -> None:
    """Move bar partitions onto their corrected venue.

    A ticker's venue appears both in its directory name and in each parquet file's
    schema metadata. When a venue is corrected, both must change together or the
    bars are orphaned — present on disk, unreachable by identity.

    Bars are copied verbatim: the OHLCV columns are raw encodings that carry no
    venue, so nothing is re-derived and the values are bit-identical to what was
    imported. The original partition is deleted only after every file in its
    replacement has been written and verified.

    Run --dry-run first. It reports exactly what would move and writes the plan
    JSON, which is also the rollback manifest.
    """
    settings = get_settings()
    name = catalog_name or settings.firstrate.firstrate_catalog_name
    base = CatalogSettings().catalog_base_path
    catalog_root = Path(base) / name
    if not catalog_root.is_dir():
        console.print(f"[red]Catalog directory not found: {escape(str(catalog_root))}[/red]")
        raise SystemExit(1)

    try:
        targets, excluded = _load_targets(name)
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        console.print(f"[yellow]Metadata DB not available — {escape(str(exc))}[/yellow]")
        raise SystemExit(2) from exc

    only = (
        frozenset(t.strip().upper() for t in limit_tickers.split(",") if t.strip())
        if limit_tickers
        else None
    )
    plan = build_restamp_plan(
        catalog=name,
        catalog_root=catalog_root,
        target_venues=targets,
        excluded_tickers=excluded,
        only_tickers=only,
    )
    _render_plan(plan, workers=workers, min_free_gb=min_free_gb)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _write_plan_json(plan, Path(plan_out or f"logs/venue-restamp/plan-{stamp}.json"))

    if dry_run:
        return
    if not plan.actions:
        console.print("[green]✓ Nothing to re-stamp — every partition is on its venue.[/green]")
        return

    if not plan.is_safe and not force:
        # A surprise in the tree means the plan's assumptions do not hold. Rewriting
        # tens of GB on that basis is not worth the minutes it saves.
        console.print(
            "[red]✗ Refusing to run — the tree contains entries the plan cannot "
            "account for (see above). Investigate; --force overrides.[/red]"
        )
        raise SystemExit(1)

    peak = plan.peak_extra_bytes(workers)
    if shutil.disk_usage(catalog_root).free - peak <= min_free_gb * _BYTES_PER_GIB:
        console.print("[red]✗ Refusing to run — insufficient disk headroom.[/red]")
        raise SystemExit(1)

    console.print(f"\nRe-stamping {len(plan.actions)} partition(s) with {workers} worker(s)...")
    outcome = execute_plan(
        plan,
        workers=workers,
        deep_verify=deep_verify,
        journal_path=Path(f"logs/venue-restamp/restamp-{stamp}.jsonl"),
        on_progress=_progress,
    )

    console.print(
        f"\nCompleted {outcome.completed}, skipped {outcome.skipped}, "
        f"failed {len(outcome.failed)} · {outcome.bytes_written / _BYTES_PER_GIB:.2f} GiB rewritten"
    )
    if outcome.failed:
        for failure in outcome.failed[:10]:
            console.print(f"[red]  {escape(failure)}[/red]")
        console.print(
            "[yellow]Source partitions for failed actions were left intact — "
            "re-run to retry.[/yellow]"
        )
        raise SystemExit(1)
    console.print("[green]✓ All partitions re-stamped and verified.[/green]")


def _progress(done: int, total: int, name: str) -> None:
    """Report every 25 partitions — a silent half-hour looks like a hang."""
    if done % 25 == 0 or done == total:
        console.print(f"  [{done}/{total}] {escape(name)}")
