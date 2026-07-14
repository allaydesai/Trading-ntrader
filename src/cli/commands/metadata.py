"""CLI commands for instrument metadata inspection (Story 3.2+).

Read-only reports over the local ``instrument_metadata`` cache table. No network
or provider call — the resolved metadata is produced by the Epic-1 resolution
service and the Story 3.1 import qualification.
"""

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from sqlalchemy.exc import SQLAlchemyError

from src.db.exceptions import DatabaseConnectionError
from src.db.models.instrument_metadata import InstrumentMetadata
from src.db.repositories.instrument_metadata_repository_sync import (
    SyncInstrumentMetadataRepository,
)
from src.db.session_sync import get_sync_session
from src.models.instrument_metadata import ResolutionStatus
from src.services.metadata.unresolved_report import unresolved_reason

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
