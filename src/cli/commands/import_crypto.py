"""``import --format firstrate --asset-class crypto`` runner (crypto M2).

Spot crypto needs no ``company_profiles.csv``: identity and contract fields come from the
coin spec (``src/services/crypto/coin_specs.yaml``). Bars are trade prices (``LAST``), so
each ``--timeframe`` spec is mapped to ``LAST``. As for futures and FX, the
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


def run_crypto_import(catalog: str, source_path: Path, timeframes: list[str]) -> int:
    """Import every ``{COIN}_full_{tf}.txt`` under ``source_path``; return the exit code."""
    from src.config import get_settings
    from src.db.repositories.catalog_instrument_repository import (
        SyncCatalogInstrumentRepository,
    )
    from src.db.session_sync import get_sync_session_maker
    from src.services.firstrate.catalog_manager import CatalogManager
    from src.services.firstrate.crypto_import_service import (
        CryptoImportService,
        last_timeframe,
    )
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
        service = CryptoImportService(CatalogManager(catalog_base), metadata)
        click.echo(f"\n--- {AssetClass.CRYPTO.value} ---")
        for tf in (last_timeframe(spec) for spec in timeframes):
            results = service.import_directory(source_path, catalog, AssetClass.CRYPTO, tf)
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
