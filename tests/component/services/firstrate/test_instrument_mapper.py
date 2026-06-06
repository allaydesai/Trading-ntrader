"""Component tests for InstrumentMapper with in-memory SQLite DB round-trip."""

from datetime import date
from pathlib import Path

import pytest
from nautilus_trader.model.identifiers import InstrumentId
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.db.exceptions import InstrumentMappingError
from src.db.repositories.catalog_instrument_repository import (
    SyncCatalogInstrumentRepository,
)
from src.services.firstrate.instrument_mapper import InstrumentMapper

CATALOG_NAME = "firstrate-etf"
ASSET_CLASS = "ETF"


_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS catalog_instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker VARCHAR(20) NOT NULL,
    nautilus_id VARCHAR(50),
    asset_class VARCHAR(20) NOT NULL,
    catalog_name VARCHAR(50) NOT NULL,
    exchange VARCHAR(20),
    name VARCHAR(200),
    sector VARCHAR(100),
    industry VARCHAR(100),
    ipo_date DATE,
    country VARCHAR(100),
    state VARCHAR(100),
    date_range_start TIMESTAMP,
    date_range_end TIMESTAMP,
    bar_count_daily INTEGER NOT NULL DEFAULT 0,
    bar_count_hourly INTEGER NOT NULL DEFAULT 0,
    bar_count_minute INTEGER NOT NULL DEFAULT 0,
    bar_count_5min INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(catalog_name, ticker)
);
"""


@pytest.fixture()
def sync_session():
    """In-memory SQLite session with catalog_instruments table created."""
    engine = create_engine("sqlite:///:memory:")

    with engine.connect() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
        conn.commit()

    with Session(engine) as session:
        yield session


@pytest.fixture()
def repo(sync_session):
    """SyncCatalogInstrumentRepository backed by in-memory SQLite."""
    return SyncCatalogInstrumentRepository(sync_session)


@pytest.fixture()
def mapper(repo):
    """InstrumentMapper with real repo."""
    return InstrumentMapper(repo=repo)


@pytest.fixture()
def company_profiles_csv(tmp_path: Path) -> Path:
    """Create a realistic company_profiles.csv."""
    content = (
        "SPY,SPDR S&P 500 ETF Trust,US,NY,ARCA,"
        "Financial Services,Asset Management,1993-01-22\n"
        "QQQ,Invesco QQQ Trust,US,NY,NASDAQ,"
        "Financial Services,Asset Management,1999-03-10\n"
        "IWM,iShares Russell 2000 ETF,US,NY,ARCA,"
        "Financial Services,Asset Management,2000-05-22\n"
    )
    csv_file = tmp_path / "company_profiles.csv"
    csv_file.write_text(content)
    return csv_file


class TestInstrumentMapperDBRoundTrip:
    """Component tests: load CSV → verify DB → resolve → verify InstrumentId."""

    @pytest.mark.component
    def test_load_and_resolve(self, mapper, company_profiles_csv):
        """Load CSV, then resolve ticker to correct InstrumentId."""
        count = mapper.load_company_profiles(company_profiles_csv, CATALOG_NAME, ASSET_CLASS)
        assert count == 3

        result = mapper.resolve_instrument_id("SPY", CATALOG_NAME)
        assert result == InstrumentId.from_str("SPY.ARCA")

    @pytest.mark.component
    def test_load_populates_all_fields(self, mapper, repo, company_profiles_csv):
        """Loaded instruments have all CSV fields persisted in DB."""
        mapper.load_company_profiles(company_profiles_csv, CATALOG_NAME, ASSET_CLASS)

        instrument = repo.get_by_ticker(CATALOG_NAME, "SPY")
        assert instrument is not None
        assert instrument.ticker == "SPY"
        assert instrument.nautilus_id == "SPY.ARCA"
        assert instrument.exchange == "ARCA"
        assert instrument.name == "SPDR S&P 500 ETF Trust"
        assert instrument.sector == "Financial Services"
        assert instrument.industry == "Asset Management"
        assert instrument.ipo_date == date(1993, 1, 22)
        assert instrument.asset_class == ASSET_CLASS
        assert instrument.catalog_name == CATALOG_NAME

    @pytest.mark.component
    def test_is_loaded_after_load(self, mapper, company_profiles_csv):
        """is_loaded returns True after loading profiles."""
        assert mapper.is_loaded(CATALOG_NAME) is False

        mapper.load_company_profiles(company_profiles_csv, CATALOG_NAME, ASSET_CLASS)

        assert mapper.is_loaded(CATALOG_NAME) is True

    @pytest.mark.component
    def test_upsert_updates_existing_records(self, mapper, repo, tmp_path):
        """Re-loading same catalog updates existing records, not duplicates."""
        csv1 = tmp_path / "v1.csv"
        csv1.write_text(
            "SPY,SPDR S&P 500 ETF Trust,US,NY,ARCA,Financial Services,Asset Management,1993-01-22\n"
        )
        mapper.load_company_profiles(csv1, CATALOG_NAME, ASSET_CLASS)

        # Update name in v2
        csv2 = tmp_path / "v2.csv"
        csv2.write_text(
            "SPY,SPDR S&P 500 Updated,US,NY,ARCA,Financial Services,Asset Management,1993-01-22\n"
        )
        mapper.load_company_profiles(csv2, CATALOG_NAME, ASSET_CLASS)

        # Should have 1 record, not 2
        instruments = repo.list_by_catalog(CATALOG_NAME)
        assert len(instruments) == 1
        assert instruments[0].name == "SPDR S&P 500 Updated"

    @pytest.mark.component
    def test_resolve_unknown_ticker_raises(self, mapper, company_profiles_csv):
        """Resolving unknown ticker raises InstrumentMappingError."""
        mapper.load_company_profiles(company_profiles_csv, CATALOG_NAME, ASSET_CLASS)

        with pytest.raises(InstrumentMappingError, match="ZZZZZ"):
            mapper.resolve_instrument_id("ZZZZZ", CATALOG_NAME)

    @pytest.mark.component
    def test_resolve_all_loaded_tickers(self, mapper, company_profiles_csv):
        """All loaded tickers can be resolved to correct InstrumentIds."""
        mapper.load_company_profiles(company_profiles_csv, CATALOG_NAME, ASSET_CLASS)

        expected = {
            "SPY": "SPY.ARCA",
            "QQQ": "QQQ.NASDAQ",
            "IWM": "IWM.ARCA",
        }
        for ticker, expected_id in expected.items():
            result = mapper.resolve_instrument_id(ticker, CATALOG_NAME)
            assert result == InstrumentId.from_str(expected_id)
