"""Component tests for CatalogDividend repositories using in-memory SQLite."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.db.models.catalog_dividend import CatalogDividend
from src.db.repositories.catalog_dividend_repository import (
    CatalogDividendRepository,
    SyncCatalogDividendRepository,
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS catalog_dividends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_name VARCHAR(50) NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    ex_date DATE NOT NULL,
    amount NUMERIC(20, 8) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (catalog_name, ticker, ex_date)
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


def _div(ex_date: date, amount: str) -> CatalogDividend:
    return CatalogDividend(ex_date=ex_date, amount=Decimal(amount))


@pytest.mark.component
class TestSyncCatalogDividendRepository:
    """Tests for sync SyncCatalogDividendRepository."""

    def test_replace_inserts_rows(self, sync_session):
        """replace_for_ticker inserts the provided rows."""
        repo = SyncCatalogDividendRepository(sync_session)
        rows = [_div(date(2026, 2, 9), "0.26"), _div(date(2025, 8, 11), "0.260")]

        repo.replace_for_ticker("cat", "AAPL", rows)
        sync_session.commit()

        result = repo.list_by_ticker("cat", "AAPL")
        assert len(result) == 2
        assert {r.amount for r in result} == {Decimal("0.26"), Decimal("0.260")}

    def test_replace_preserves_decimal_precision(self, sync_session):
        """Amount round-trips with exact Decimal precision (no float)."""
        repo = SyncCatalogDividendRepository(sync_session)
        repo.replace_for_ticker("cat", "AAPL", [_div(date(2025, 8, 11), "0.260")])
        sync_session.commit()

        result = repo.list_by_ticker("cat", "AAPL")
        assert result[0].amount == Decimal("0.260")
        assert isinstance(result[0].amount, Decimal)

    def test_replace_on_rerun_is_idempotent(self, sync_session):
        """Re-running replace_for_ticker keeps the row count constant (AC-5)."""
        repo = SyncCatalogDividendRepository(sync_session)
        rows = [_div(date(2026, 2, 9), "0.26"), _div(date(2025, 8, 11), "0.26")]
        repo.replace_for_ticker("cat", "AAPL", rows)
        sync_session.commit()

        # Re-run with an updated set (one amount changed, one row added).
        new_rows = [
            _div(date(2026, 2, 9), "0.30"),
            _div(date(2025, 8, 11), "0.26"),
            _div(date(2024, 2, 9), "0.24"),
        ]
        repo.replace_for_ticker("cat", "AAPL", new_rows)
        sync_session.commit()

        result = repo.list_by_ticker("cat", "AAPL")
        assert len(result) == 3
        by_date = {r.ex_date: r.amount for r in result}
        assert by_date[date(2026, 2, 9)] == Decimal("0.30")

    def test_replace_shrinking_history_drops_stale_rows(self, sync_session):
        """A shrinking history leaves no orphan rows."""
        repo = SyncCatalogDividendRepository(sync_session)
        repo.replace_for_ticker(
            "cat",
            "AAPL",
            [_div(date(2026, 2, 9), "0.26"), _div(date(2025, 8, 11), "0.26")],
        )
        sync_session.commit()

        repo.replace_for_ticker("cat", "AAPL", [_div(date(2026, 2, 9), "0.26")])
        sync_session.commit()

        assert len(repo.list_by_ticker("cat", "AAPL")) == 1

    def test_list_ordering_descending(self, sync_session):
        """list_by_ticker returns rows ordered by ex_date descending."""
        repo = SyncCatalogDividendRepository(sync_session)
        repo.replace_for_ticker(
            "cat",
            "AAPL",
            [_div(date(2024, 2, 9), "0.24"), _div(date(2026, 2, 9), "0.26")],
        )
        sync_session.commit()

        result = repo.list_by_ticker("cat", "AAPL")
        assert [r.ex_date for r in result] == [date(2026, 2, 9), date(2024, 2, 9)]

    def test_catalog_scoping_isolated(self, sync_session):
        """Two catalogs with the same ticker stay isolated."""
        repo = SyncCatalogDividendRepository(sync_session)
        repo.replace_for_ticker("cat-a", "AAPL", [_div(date(2026, 2, 9), "0.26")])
        repo.replace_for_ticker("cat-b", "AAPL", [_div(date(2025, 1, 1), "0.10")])
        sync_session.commit()

        a = repo.list_by_ticker("cat-a", "AAPL")
        b = repo.list_by_ticker("cat-b", "AAPL")
        assert len(a) == 1 and len(b) == 1
        assert a[0].amount == Decimal("0.26")
        assert b[0].amount == Decimal("0.10")

    def test_replace_other_ticker_untouched(self, sync_session):
        """Replacing one ticker leaves another ticker's rows intact."""
        repo = SyncCatalogDividendRepository(sync_session)
        repo.replace_for_ticker("cat", "AAPL", [_div(date(2026, 2, 9), "0.26")])
        repo.replace_for_ticker("cat", "MSFT", [_div(date(2026, 3, 1), "0.75")])
        sync_session.commit()

        repo.replace_for_ticker("cat", "AAPL", [_div(date(2026, 2, 9), "0.30")])
        sync_session.commit()

        assert len(repo.list_by_ticker("cat", "MSFT")) == 1


@pytest.mark.component
class TestAsyncCatalogDividendRepository:
    """Tests for async CatalogDividendRepository."""

    async def test_replace_and_list(self, async_session):
        """Async replace_for_ticker + list_by_ticker round-trip."""
        repo = CatalogDividendRepository(async_session)
        await repo.replace_for_ticker(
            "cat",
            "AAPL",
            [_div(date(2026, 2, 9), "0.26"), _div(date(2024, 2, 9), "0.24")],
        )
        await async_session.commit()

        result = await repo.list_by_ticker("cat", "AAPL")
        assert [r.ex_date for r in result] == [date(2026, 2, 9), date(2024, 2, 9)]

    async def test_async_replace_idempotent(self, async_session):
        """Async re-run keeps row count constant."""
        repo = CatalogDividendRepository(async_session)
        rows = [_div(date(2026, 2, 9), "0.26")]
        await repo.replace_for_ticker("cat", "AAPL", rows)
        await async_session.commit()
        await repo.replace_for_ticker("cat", "AAPL", [_div(date(2026, 2, 9), "0.30")])
        await async_session.commit()

        result = await repo.list_by_ticker("cat", "AAPL")
        assert len(result) == 1
        assert result[0].amount == Decimal("0.30")

    async def test_has_for_ticker_true_when_rows_exist(self, async_session):
        """has_for_ticker returns True when dividend rows exist."""
        repo = CatalogDividendRepository(async_session)
        await repo.replace_for_ticker("cat", "AAPL", [_div(date(2026, 2, 9), "0.26")])
        await async_session.commit()

        assert await repo.has_for_ticker("cat", "AAPL") is True

    async def test_has_for_ticker_false_when_none(self, async_session):
        """has_for_ticker returns False when no rows exist for the ticker."""
        repo = CatalogDividendRepository(async_session)
        assert await repo.has_for_ticker("cat", "AAPL") is False

    async def test_has_for_ticker_catalog_scoped(self, async_session):
        """has_for_ticker is scoped to the given catalog."""
        repo = CatalogDividendRepository(async_session)
        await repo.replace_for_ticker("cat-a", "AAPL", [_div(date(2026, 2, 9), "0.26")])
        await async_session.commit()

        assert await repo.has_for_ticker("cat-a", "AAPL") is True
        assert await repo.has_for_ticker("cat-b", "AAPL") is False
