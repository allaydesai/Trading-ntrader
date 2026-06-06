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
