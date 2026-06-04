"""Component tests for CatalogStockSplit repositories using in-memory SQLite."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.db.models.catalog_stock_split import CatalogStockSplit
from src.db.repositories.catalog_stock_split_repository import (
    CatalogStockSplitRepository,
    SyncCatalogStockSplitRepository,
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS catalog_stock_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_name VARCHAR(50) NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    effective_date DATE NOT NULL,
    ratio NUMERIC(20, 8) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (catalog_name, ticker, effective_date)
)
"""


@pytest.fixture
async def async_session():
    """Create an async in-memory SQLite session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(_CREATE_TABLE_SQL))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def sync_session():
    """Create a sync in-memory SQLite session."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(_CREATE_TABLE_SQL))
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()
    engine.dispose()


def _split(effective_date: date, ratio: str) -> CatalogStockSplit:
    return CatalogStockSplit(effective_date=effective_date, ratio=Decimal(ratio))


@pytest.mark.component
class TestSyncCatalogStockSplitRepository:
    """Tests for sync SyncCatalogStockSplitRepository."""

    def test_replace_inserts_aapl_splits(self, sync_session):
        """replace_for_ticker inserts the four real AAPL splits."""
        repo = SyncCatalogStockSplitRepository(sync_session)
        rows = [
            _split(date(2020, 8, 31), "4"),
            _split(date(2014, 6, 9), "7"),
            _split(date(2005, 2, 28), "2"),
            _split(date(2000, 6, 21), "2"),
        ]
        repo.replace_for_ticker("cat", "AAPL", rows)
        sync_session.commit()

        result = repo.list_by_ticker("cat", "AAPL")
        assert len(result) == 4
        assert result[0].effective_date == date(2020, 8, 31)
        assert result[0].ratio == Decimal("4")

    def test_reverse_split_ratio_below_one(self, sync_session):
        """Reverse split ratio (< 1) round-trips as Decimal."""
        repo = SyncCatalogStockSplitRepository(sync_session)
        repo.replace_for_ticker("cat", "RVRS", [_split(date(2023, 5, 1), "0.5")])
        sync_session.commit()

        result = repo.list_by_ticker("cat", "RVRS")
        assert result[0].ratio == Decimal("0.5")
        assert result[0].ratio < 1

    def test_replace_on_rerun_is_idempotent(self, sync_session):
        """Re-running replace keeps row count constant (AC-5)."""
        repo = SyncCatalogStockSplitRepository(sync_session)
        repo.replace_for_ticker(
            "cat", "AAPL", [_split(date(2020, 8, 31), "4"), _split(date(2014, 6, 9), "7")]
        )
        sync_session.commit()
        repo.replace_for_ticker(
            "cat", "AAPL", [_split(date(2020, 8, 31), "4"), _split(date(2014, 6, 9), "7")]
        )
        sync_session.commit()

        assert len(repo.list_by_ticker("cat", "AAPL")) == 2

    def test_list_ordering_descending(self, sync_session):
        """list_by_ticker orders by effective_date descending."""
        repo = SyncCatalogStockSplitRepository(sync_session)
        repo.replace_for_ticker(
            "cat", "AAPL", [_split(date(2000, 6, 21), "2"), _split(date(2020, 8, 31), "4")]
        )
        sync_session.commit()

        result = repo.list_by_ticker("cat", "AAPL")
        assert [r.effective_date for r in result] == [date(2020, 8, 31), date(2000, 6, 21)]

    def test_catalog_scoping_isolated(self, sync_session):
        """Two catalogs with the same ticker stay isolated."""
        repo = SyncCatalogStockSplitRepository(sync_session)
        repo.replace_for_ticker("cat-a", "AAPL", [_split(date(2020, 8, 31), "4")])
        repo.replace_for_ticker("cat-b", "AAPL", [_split(date(2014, 6, 9), "7")])
        sync_session.commit()

        a = repo.list_by_ticker("cat-a", "AAPL")
        b = repo.list_by_ticker("cat-b", "AAPL")
        assert a[0].ratio == Decimal("4")
        assert b[0].ratio == Decimal("7")


@pytest.mark.component
class TestAsyncCatalogStockSplitRepository:
    """Tests for async CatalogStockSplitRepository."""

    async def test_replace_and_list(self, async_session):
        """Async replace + list round-trip ordered descending."""
        repo = CatalogStockSplitRepository(async_session)
        await repo.replace_for_ticker(
            "cat", "AAPL", [_split(date(2000, 6, 21), "2"), _split(date(2020, 8, 31), "4")]
        )
        await async_session.commit()

        result = await repo.list_by_ticker("cat", "AAPL")
        assert [r.effective_date for r in result] == [date(2020, 8, 31), date(2000, 6, 21)]

    async def test_has_for_ticker_true_when_rows_exist(self, async_session):
        """has_for_ticker returns True when split rows exist."""
        repo = CatalogStockSplitRepository(async_session)
        await repo.replace_for_ticker("cat", "AAPL", [_split(date(2020, 8, 31), "4")])
        await async_session.commit()

        assert await repo.has_for_ticker("cat", "AAPL") is True

    async def test_has_for_ticker_false_when_none(self, async_session):
        """has_for_ticker returns False when no split rows exist for the ticker."""
        repo = CatalogStockSplitRepository(async_session)
        assert await repo.has_for_ticker("cat", "AAPL") is False

    async def test_has_for_ticker_catalog_scoped(self, async_session):
        """has_for_ticker is scoped to the given catalog."""
        repo = CatalogStockSplitRepository(async_session)
        await repo.replace_for_ticker("cat-a", "AAPL", [_split(date(2020, 8, 31), "4")])
        await async_session.commit()

        assert await repo.has_for_ticker("cat-a", "AAPL") is True
        assert await repo.has_for_ticker("cat-b", "AAPL") is False
