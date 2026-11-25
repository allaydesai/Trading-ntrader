"""``import --format firstrate|kraken --asset-class crypto`` runners (crypto M2, M7).

Spot crypto needs no ``company_profiles.csv``: identity and contract fields come from the
coin spec (``src/services/crypto/coin_specs.yaml``). Bars are trade prices (``LAST``), so
each ``--timeframe`` spec is mapped to ``LAST``. As for futures and FX, the
``catalog_instruments`` writes are committed after every timeframe pass, so a long run keeps
the series it has finished if a later pass fails.

``--format kraken`` (M7) reads Kraken's own OHLCVT history (``XBTUSD_60.csv``, see
``src/services/crypto/kraken_import.py``) through the same runner. Kraken publishes 1, 5, 60
and 1440-minute candles that the catalog records, but no 30-minute ones.
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


#: The ``--format kraken`` default: every catalog timeframe Kraken publishes.
KRAKEN_TIMEFRAMES = ["1-DAY-LAST", "1-HOUR-LAST", "5-MINUTE-LAST", "1-MINUTE-LAST"]


def run_kraken_import(
    catalog: str, source_path: Path, asset_class: str | None, timeframe: str | None
) -> int:
    """``import --format kraken``: validate the options, then run the crypto import."""
    from src.cli.commands.import_data import parse_timeframes

    if (asset_class or "crypto").lower() != "crypto":
        console.print("❌ --format kraken imports spot crypto only (--asset-class crypto).")
        return 2
    timeframes = parse_timeframes(timeframe) if timeframe else KRAKEN_TIMEFRAMES
    missing = [tf for tf in timeframes if tf not in KRAKEN_TIMEFRAMES]
    if missing:
        console.print(f"❌ Kraken publishes no {', '.join(missing)} candles.", style="red")
        return 2
    return run_crypto_import(catalog, source_path, timeframes, source="kraken")


def run_crypto_import(
    catalog: str, source_path: Path, timeframes: list[str], source: str = "firstrate"
) -> int:
    """Import every ``{COIN}_full_{tf}.txt`` (or Kraken ``{ALTNAME}_{minutes}.csv`` with
    ``source="kraken"``) under ``source_path``; return the exit code."""
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
        if source == "kraken":
            from src.services.crypto.kraken_import import KrakenImportService

            service_class: type[CryptoImportService] = KrakenImportService
        else:
            service_class = CryptoImportService
        service = service_class(CatalogManager(catalog_base), metadata)
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
