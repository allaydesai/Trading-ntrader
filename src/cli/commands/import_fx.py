"""``import --format firstrate --asset-class fx`` runner (FX M2).

Spot FX needs no ``company_profiles.csv``: identity and contract fields come from the pair
spec (``src/services/fx/pair_specs.yaml``). Bars are written as ``MID`` (decision D2), so
each ``--timeframe`` spec is mapped from ``LAST`` to ``MID``. As for futures, the
``catalog_instruments`` writes are committed after every timeframe pass, so a long run keeps
the series it has finished if a later pass fails.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from src.cli.commands.import_futures import ensure_catalog_dir
from src.cli.commands.import_reporting import (
    _print_progress_line,
    _print_summary,
    determine_exit_code,
)
from src.models.catalog import AssetClass, ImportResult

console = Console()


def run_fx_import(catalog: str, source_path: Path, timeframes: list[str]) -> int:
    """Import every ``{PAIR}_full_{tf}.txt`` under ``source_path``; return the exit code."""
    from src.config import get_settings
    from src.db.repositories.catalog_instrument_repository import (
        SyncCatalogInstrumentRepository,
    )
    from src.db.session_sync import get_sync_session_maker
    from src.services.firstrate.catalog_manager import CatalogManager
    from src.services.firstrate.fx_import_service import FxImportService, mid_timeframe
    from src.services.firstrate.metadata_service import MetadataService

    all_results: list[ImportResult] = []
    session = None
    try:
        session_maker = get_sync_session_maker()
        if session_maker is None:
            console.print("❌ Database not configured. Check DATABASE_URL in .env", style="red")
            return 2
        catalog_base = Path(get_settings().catalog.catalog_base_path)
        session = session_maker()
        ensure_catalog_dir(catalog_base, catalog)
        metadata = MetadataService(sync_repo=SyncCatalogInstrumentRepository(session))
        service = FxImportService(CatalogManager(catalog_base), metadata)
        click.echo(f"\n--- {AssetClass.FX.value} ---")
        for tf in (mid_timeframe(spec) for spec in timeframes):
            results = service.import_directory(source_path, catalog, AssetClass.FX, tf)
            for result in results:
                _print_progress_line(result, tf)
            session.commit()
            all_results.extend(results)
    except Exception as e:  # noqa: BLE001 — any fatal error (incl. a bad DATABASE_URL) maps to 2
        if session is not None:
            session.rollback()
        console.print(f"❌ Fatal error: {e}", style="red")
        return 2
    finally:
        if session is not None:
            session.close()
    _print_summary(all_results)
    return determine_exit_code(all_results)
