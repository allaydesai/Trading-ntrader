"""Instrument ID mapping service using company_profiles.csv.

Parses FirstRate company_profiles.csv into catalog_instruments records
and resolves ticker symbols to Nautilus-qualified instrument IDs via DB lookup.
"""

import csv
from datetime import date
from pathlib import Path
from typing import Optional

import structlog
from nautilus_trader.model.identifiers import InstrumentId

from src.db.exceptions import InstrumentMappingError
from src.db.models.catalog_instrument import CatalogInstrument
from src.db.repositories.catalog_instrument_repository import (
    SyncCatalogInstrumentRepository,
)

logger = structlog.get_logger(__name__)

# Column indices for the 8-column CSV
# Format: ticker, name, country, state, exchange, sector, industry, ipo_date
# File may or may not have a header row — auto-detected and skipped
_COL_TICKER = 0
_COL_NAME = 1
_COL_COUNTRY = 2
_COL_STATE = 3
_COL_EXCHANGE = 4
_COL_SECTOR = 5
_COL_INDUSTRY = 6
_COL_IPO_DATE = 7


def _parse_ipo_date(value: str) -> Optional[date]:
    """Parse IPO date string to date object.

    Args:
        value: Date string in YYYY-MM-DD format, or empty.

    Returns:
        Parsed date or None if empty/invalid.
    """
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        logger.warning("invalid_ipo_date", value=value)
        return None


class InstrumentMapper:
    """Maps ticker symbols to Nautilus InstrumentIds via DB lookup.

    Parses FirstRate company_profiles.csv and upserts instrument records
    into catalog_instruments. Resolves tickers to InstrumentIds by
    querying the DB as the authoritative source.

    Args:
        repo: Sync catalog instrument repository for DB operations.
    """

    def __init__(self, repo: SyncCatalogInstrumentRepository) -> None:
        self._repo = repo

    def load_company_profiles(self, file_path: Path, catalog_name: str, asset_class: str) -> int:
        """Parse company_profiles.csv and upsert all rows into DB.

        Args:
            file_path: Path to the 8-column CSV file (header auto-detected).
            catalog_name: Catalog name for these instruments.
            asset_class: Asset class string (e.g., "ETF").

        Returns:
            Number of rows loaded.
        """
        count = 0
        with open(file_path, newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or all(field.strip() == "" for field in row):
                    continue

                # Skip header row if present
                if row[_COL_TICKER].strip().lower() == "ticker":
                    continue

                instrument = CatalogInstrument(
                    ticker=row[_COL_TICKER].strip(),
                    name=row[_COL_NAME].strip(),
                    exchange=row[_COL_EXCHANGE].strip(),
                    nautilus_id=(f"{row[_COL_TICKER].strip()}.{row[_COL_EXCHANGE].strip()}"),
                    asset_class=asset_class,
                    catalog_name=catalog_name,
                    sector=row[_COL_SECTOR].strip() or None,
                    industry=row[_COL_INDUSTRY].strip() or None,
                    ipo_date=_parse_ipo_date(row[_COL_IPO_DATE]),
                    country=row[_COL_COUNTRY].strip() or None,
                    state=row[_COL_STATE].strip() or None,
                    bar_count_daily=0,
                    bar_count_hourly=0,
                    bar_count_minute=0,
                    bar_count_5min=0,
                    bar_count_30min=0,
                )
                self._repo.upsert(instrument)
                count += 1

        logger.info(
            "company_profiles_loaded",
            file=str(file_path),
            catalog=catalog_name,
            count=count,
        )
        return count

    @staticmethod
    def qualified_instrument_id(ticker: str, venue: Optional[str]) -> Optional[InstrumentId]:
        """Compute a Nautilus-qualified ``InstrumentId`` from a resolved venue (Story 3.1).

        Pure: the venue is the authoritative code resolved by ``InstrumentMetadataService``
        (Epic 1) — never a guessed/CSV value. A falsy venue (``None`` or blank, i.e.
        ``VENUE_UNRESOLVED``) yields ``None`` so no identity is fabricated (AC3). ``venue``
        is already guaranteed a real code or ``None`` by the domain model's
        ``venue_never_na_sentinel`` validator, so the ``NA_SENTINEL`` is not re-checked here.

        Args:
            ticker: Trading symbol (e.g. "SPY").
            venue: Resolved Nautilus venue code (e.g. "ARCA"), or ``None``/blank.

        Returns:
            ``InstrumentId`` (e.g. ``SPY.ARCA``), or ``None`` when the venue is unresolved.
        """
        if not venue:
            return None
        return InstrumentId.from_str(f"{ticker}.{venue}")

    def sync_qualification(
        self, ticker: str, catalog_name: str, venue: Optional[str]
    ) -> Optional[InstrumentId]:
        """Sync the resolved venue onto ``catalog_instruments`` (ADR-3 qualification sync).

        The import pipeline (single writer) calls this after resolving a ticker's metadata:
        the authoritative resolved ``venue`` overwrites the provisional CSV exchange on the
        ``catalog_instruments`` identity row and recomputes ``nautilus_id``. ``instrument_metadata``
        stays the source of truth for resolved metadata; ``catalog_instruments`` for bar
        counts/date ranges. A ``VENUE_UNRESOLVED`` ticker (``venue is None``) leaves
        ``nautilus_id`` ``None`` — unqualified, not fabricated (AC3). Nulling the identity is
        the exclusion mechanism the backtest loader honors (it gates on ``nautilus_id``), so an
        unresolved-venue ticker is kept out of every backtest; the on-disk Parquet is untouched
        (Story 3.5 AC2 — bars present, not dropped), just unreachable-by-id until the venue
        resolves and this re-runs with a real venue. See ``backtest_loader.load_from_catalog``.

        Args:
            ticker: Trading symbol.
            catalog_name: Catalog scoping the identity row.
            venue: Resolved Nautilus venue code, or ``None`` when unresolved.

        Returns:
            The qualified ``InstrumentId``, or ``None`` when the venue is unresolved or no
            ``catalog_instruments`` row exists (profiles not loaded).
        """
        instrument = self._repo.get_by_ticker(catalog_name, ticker)
        if instrument is None:
            logger.warning(
                "qualification_sync_skipped",
                ticker=ticker,
                catalog=catalog_name,
                reason="no catalog_instruments row — company profiles not loaded?",
            )
            return None

        qid = self.qualified_instrument_id(ticker, venue)
        instrument.nautilus_id = str(qid) if qid is not None else None
        instrument.exchange = venue
        self._repo.upsert(instrument)
        logger.debug(
            "qualification_synced",
            ticker=ticker,
            venue=venue,
            nautilus_id=instrument.nautilus_id,
        )
        return qid

    def resolve_instrument_id(self, ticker: str, catalog_name: str) -> InstrumentId:
        """Look up ticker in DB and return Nautilus InstrumentId.

        Args:
            ticker: Trading symbol (e.g., "SPY").
            catalog_name: Catalog to search in.

        Returns:
            Nautilus InstrumentId (e.g., "SPY.ARCA").

        Raises:
            InstrumentMappingError: If ticker not found in DB.
        """
        instrument = self._repo.get_by_ticker(catalog_name, ticker)
        if instrument is None:
            raise InstrumentMappingError(
                f"Cannot map ticker '{ticker}' to instrument ID — "
                f"not found in catalog '{catalog_name}'. "
                f"Ensure company_profiles.csv has been loaded."
            )
        return InstrumentId.from_str(instrument.nautilus_id)

    def is_loaded(self, catalog_name: str) -> bool:
        """Check if company profiles have been loaded for a catalog.

        Args:
            catalog_name: Catalog name to check.

        Returns:
            True if instruments exist for this catalog.
        """
        instruments = self._repo.list_by_catalog(catalog_name, limit=1)
        return len(instruments) > 0
