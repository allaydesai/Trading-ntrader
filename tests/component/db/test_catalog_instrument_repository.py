"""Component tests for CatalogInstrument repositories using in-memory SQLite."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.db.models.catalog_instrument import CatalogInstrument
from src.db.repositories.catalog_instrument_repository import (
    CatalogInstrumentRepository,
    SyncCatalogInstrumentRepository,
)

# SQLite DDL for catalog_instruments (INTEGER PK enables autoincrement)
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS catalog_instruments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker VARCHAR(20) NOT NULL,
    nautilus_id VARCHAR(50) NOT NULL,
    asset_class VARCHAR(20) NOT NULL,
    catalog_name VARCHAR(50) NOT NULL,
    exchange VARCHAR(20) NOT NULL,
    name VARCHAR(200) NOT NULL,
    sector VARCHAR(100),
    industry VARCHAR(100),
    ipo_date DATE,
    date_range_start TIMESTAMP,
    date_range_end TIMESTAMP,
    bar_count_daily INTEGER NOT NULL DEFAULT 0,
    bar_count_hourly INTEGER NOT NULL DEFAULT 0,
    bar_count_minute INTEGER NOT NULL DEFAULT 0,
    bar_count_5min INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (catalog_name, ticker)
)
"""


@pytest.fixture
async def async_session():
    """Create an async in-memory SQLite session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(_CREATE_TABLE_SQL))
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def sync_session():
    """Create a sync in-memory SQLite session."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
    session_maker = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_maker()
    yield session
    session.close()
    engine.dispose()


def _make_instrument(**overrides) -> dict:
    """Create default instrument kwargs with optional overrides."""
    defaults = {
        "ticker": "SPY",
        "nautilus_id": "SPY.ARCA",
        "asset_class": "ETF",
        "catalog_name": "firstrate-etf",
        "exchange": "ARCA",
        "name": "SPDR S&P 500 ETF Trust",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.component
class TestAsyncCatalogInstrumentRepository:
    """Tests for async CatalogInstrumentRepository."""

    async def test_upsert_creates_new_record(self, async_session):
        """upsert creates a new instrument record."""
        repo = CatalogInstrumentRepository(async_session)
        instrument = CatalogInstrument(**_make_instrument())

        result = await repo.upsert(instrument)
        await async_session.commit()

        assert result.id is not None
        assert result.ticker == "SPY"

    async def test_get_by_ticker(self, async_session):
        """get_by_ticker returns matching instrument."""
        repo = CatalogInstrumentRepository(async_session)
        instrument = CatalogInstrument(**_make_instrument())
        await repo.upsert(instrument)
        await async_session.commit()

        result = await repo.get_by_ticker("firstrate-etf", "SPY")
        assert result is not None
        assert result.ticker == "SPY"
        assert result.catalog_name == "firstrate-etf"

    async def test_get_by_ticker_not_found(self, async_session):
        """get_by_ticker returns None for missing instrument."""
        repo = CatalogInstrumentRepository(async_session)
        result = await repo.get_by_ticker("firstrate-etf", "MISSING")
        assert result is None

    async def test_list_by_catalog(self, async_session):
        """list_by_catalog returns instruments filtered by catalog."""
        repo = CatalogInstrumentRepository(async_session)
        await repo.upsert(CatalogInstrument(**_make_instrument(ticker="SPY")))
        await repo.upsert(
            CatalogInstrument(**_make_instrument(ticker="QQQ", nautilus_id="QQQ.XNAS"))
        )
        await repo.upsert(
            CatalogInstrument(
                **_make_instrument(
                    ticker="BTC",
                    catalog_name="other",
                    nautilus_id="BTC.KRAKEN",
                )
            )
        )
        await async_session.commit()

        results = await repo.list_by_catalog("firstrate-etf")
        assert len(results) == 2
        tickers = {r.ticker for r in results}
        assert tickers == {"SPY", "QQQ"}

    async def test_list_by_catalog_with_asset_class(self, async_session):
        """list_by_catalog can filter by asset_class."""
        repo = CatalogInstrumentRepository(async_session)
        await repo.upsert(CatalogInstrument(**_make_instrument(ticker="SPY")))
        await repo.upsert(
            CatalogInstrument(
                **_make_instrument(
                    ticker="AAPL",
                    asset_class="STOCK",
                    nautilus_id="AAPL.XNAS",
                )
            )
        )
        await async_session.commit()

        results = await repo.list_by_catalog("firstrate-etf", asset_class="ETF")
        assert len(results) == 1
        assert results[0].ticker == "SPY"

    async def test_search(self, async_session):
        """search finds instruments by partial ticker match."""
        repo = CatalogInstrumentRepository(async_session)
        await repo.upsert(CatalogInstrument(**_make_instrument(ticker="SPY")))
        await repo.upsert(
            CatalogInstrument(**_make_instrument(ticker="SPYG", nautilus_id="SPYG.ARCA"))
        )
        await repo.upsert(
            CatalogInstrument(**_make_instrument(ticker="QQQ", nautilus_id="QQQ.XNAS"))
        )
        await async_session.commit()

        results = await repo.search("SPY")
        assert len(results) == 2
        tickers = {r.ticker for r in results}
        assert tickers == {"SPY", "SPYG"}


@pytest.mark.component
class TestSyncCatalogInstrumentRepository:
    """Tests for sync SyncCatalogInstrumentRepository."""

    def test_upsert_creates_new_record(self, sync_session):
        """upsert creates a new instrument record."""
        repo = SyncCatalogInstrumentRepository(sync_session)
        instrument = CatalogInstrument(**_make_instrument())

        result = repo.upsert(instrument)
        sync_session.commit()

        assert result.id is not None
        assert result.ticker == "SPY"

    def test_get_by_ticker(self, sync_session):
        """get_by_ticker returns matching instrument."""
        repo = SyncCatalogInstrumentRepository(sync_session)
        instrument = CatalogInstrument(**_make_instrument())
        repo.upsert(instrument)
        sync_session.commit()

        result = repo.get_by_ticker("firstrate-etf", "SPY")
        assert result is not None
        assert result.ticker == "SPY"

    def test_get_by_ticker_not_found(self, sync_session):
        """get_by_ticker returns None for missing instrument."""
        repo = SyncCatalogInstrumentRepository(sync_session)
        result = repo.get_by_ticker("firstrate-etf", "MISSING")
        assert result is None

    def test_list_by_catalog(self, sync_session):
        """list_by_catalog returns instruments filtered by catalog."""
        repo = SyncCatalogInstrumentRepository(sync_session)
        repo.upsert(CatalogInstrument(**_make_instrument(ticker="SPY")))
        repo.upsert(CatalogInstrument(**_make_instrument(ticker="QQQ", nautilus_id="QQQ.XNAS")))
        sync_session.commit()

        results = repo.list_by_catalog("firstrate-etf")
        assert len(results) == 2

    def test_search(self, sync_session):
        """search finds instruments by partial ticker match."""
        repo = SyncCatalogInstrumentRepository(sync_session)
        repo.upsert(CatalogInstrument(**_make_instrument(ticker="SPY")))
        repo.upsert(CatalogInstrument(**_make_instrument(ticker="QQQ", nautilus_id="QQQ.XNAS")))
        sync_session.commit()

        results = repo.search("SP")
        assert len(results) == 1
        assert results[0].ticker == "SPY"
