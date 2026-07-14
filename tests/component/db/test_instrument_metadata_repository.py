"""Component tests for InstrumentMetadata repositories using in-memory SQLite."""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.db.models.instrument_metadata import InstrumentMetadata
from src.db.repositories.instrument_metadata_repository import InstrumentMetadataRepository
from src.db.repositories.instrument_metadata_repository_sync import (
    SyncInstrumentMetadataRepository,
)
from src.models.instrument_metadata import NA_SENTINEL, ResolutionStatus

# SQLite DDL — ticker is the PK; SQLite has no ENUM so the generic sa.Enum
# binds/returns plain strings, making VARCHAR(20) the correct rendering.
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS instrument_metadata (
    ticker VARCHAR(20) PRIMARY KEY,
    metadata_provider VARCHAR(20) NOT NULL,
    venue VARCHAR(20),
    currency VARCHAR(10),
    asset_type VARCHAR(20),
    company_name VARCHAR(200),
    sector VARCHAR(100),
    industry VARCHAR(100),
    country VARCHAR(100),
    ipo_date DATE,
    resolution_status VARCHAR(20) NOT NULL,
    resolved_at TIMESTAMP,
    updated_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
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


def _make_metadata(**overrides) -> dict:
    """Create default ORM kwargs with optional overrides."""
    defaults = {
        "ticker": "SPY",
        "metadata_provider": "FMP",
        "resolution_status": ResolutionStatus.UNRESOLVED,
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.component
class TestAsyncInstrumentMetadataRepository:
    """Tests for async InstrumentMetadataRepository."""

    async def test_upsert_creates_new_record(self, async_session):
        """upsert inserts a new metadata record."""
        repo = InstrumentMetadataRepository(async_session)
        result = await repo.upsert(InstrumentMetadata(**_make_metadata()))
        await async_session.commit()

        assert result.ticker == "SPY"
        assert result.metadata_provider == "FMP"

    async def test_upsert_updates_existing_record(self, async_session):
        """upsert on the same ticker updates rather than duplicating (idempotent)."""
        repo = InstrumentMetadataRepository(async_session)
        await repo.upsert(
            InstrumentMetadata(**_make_metadata(resolution_status=ResolutionStatus.UNRESOLVED))
        )
        await async_session.commit()

        await repo.upsert(
            InstrumentMetadata(
                **_make_metadata(
                    venue="ARCA",
                    currency="USD",
                    sector="Financial Services",
                    resolution_status=ResolutionStatus.RESOLVED,
                )
            )
        )
        await async_session.commit()

        result = await repo.get_by_ticker("SPY")
        assert result is not None
        assert result.venue == "ARCA"
        assert result.currency == "USD"
        assert result.resolution_status == ResolutionStatus.RESOLVED

    async def test_get_by_ticker_hit(self, async_session):
        """get_by_ticker returns the matching record."""
        repo = InstrumentMetadataRepository(async_session)
        await repo.upsert(InstrumentMetadata(**_make_metadata()))
        await async_session.commit()

        result = await repo.get_by_ticker("SPY")
        assert result is not None
        assert result.ticker == "SPY"

    async def test_get_by_ticker_miss_returns_none(self, async_session):
        """get_by_ticker returns None for an unknown ticker."""
        repo = InstrumentMetadataRepository(async_session)
        result = await repo.get_by_ticker("MISSING")
        assert result is None

    async def test_round_trip_three_state_fields(self, async_session):
        """Round-trips resolution_status, an NA_SENTINEL descriptive field, None venue."""
        repo = InstrumentMetadataRepository(async_session)
        await repo.upsert(
            InstrumentMetadata(
                **_make_metadata(
                    venue=None,
                    sector=NA_SENTINEL,
                    resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
                )
            )
        )
        await async_session.commit()

        result = await repo.get_by_ticker("SPY")
        assert result is not None
        assert result.venue is None
        assert result.sector == NA_SENTINEL
        assert result.resolution_status == ResolutionStatus.VENUE_UNRESOLVED

    async def test_upsert_defaults_resolution_status_to_unresolved(self, async_session):
        """Omitting resolution_status persists UNRESOLVED (ORM default), not a NOT NULL error."""
        repo = InstrumentMetadataRepository(async_session)
        await repo.upsert(InstrumentMetadata(ticker="SPY", metadata_provider="FMP"))
        await async_session.commit()

        result = await repo.get_by_ticker("SPY")
        assert result is not None
        assert result.resolution_status == ResolutionStatus.UNRESOLVED


@pytest.mark.component
class TestSyncInstrumentMetadataRepository:
    """Tests for sync SyncInstrumentMetadataRepository."""

    def test_upsert_creates_new_record(self, sync_session):
        """upsert inserts a new metadata record."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        result = repo.upsert(InstrumentMetadata(**_make_metadata()))
        sync_session.commit()

        assert result.ticker == "SPY"
        assert result.metadata_provider == "FMP"

    def test_upsert_updates_existing_record(self, sync_session):
        """upsert on the same ticker updates rather than duplicating (idempotent)."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(InstrumentMetadata(**_make_metadata()))
        sync_session.commit()

        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(venue="XNAS", resolution_status=ResolutionStatus.RESOLVED)
            )
        )
        sync_session.commit()

        result = repo.get_by_ticker("SPY")
        assert result is not None
        assert result.venue == "XNAS"
        assert result.resolution_status == ResolutionStatus.RESOLVED

    def test_get_by_ticker_hit(self, sync_session):
        """get_by_ticker returns the matching record."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(InstrumentMetadata(**_make_metadata()))
        sync_session.commit()

        result = repo.get_by_ticker("SPY")
        assert result is not None
        assert result.ticker == "SPY"

    def test_get_by_ticker_miss_returns_none(self, sync_session):
        """get_by_ticker returns None for an unknown ticker."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        result = repo.get_by_ticker("MISSING")
        assert result is None

    def test_round_trip_three_state_fields(self, sync_session):
        """Round-trips resolution_status, an NA_SENTINEL descriptive field, None venue."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(
                    venue=None,
                    industry=NA_SENTINEL,
                    resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
                )
            )
        )
        sync_session.commit()

        result = repo.get_by_ticker("SPY")
        assert result is not None
        assert result.venue is None
        assert result.industry == NA_SENTINEL
        assert result.resolution_status == ResolutionStatus.VENUE_UNRESOLVED

    def test_upsert_defaults_resolution_status_to_unresolved(self, sync_session):
        """Omitting resolution_status persists UNRESOLVED (ORM default), not a NOT NULL error."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(InstrumentMetadata(ticker="SPY", metadata_provider="FMP"))
        sync_session.commit()

        result = repo.get_by_ticker("SPY")
        assert result is not None
        assert result.resolution_status == ResolutionStatus.UNRESOLVED

    def test_list_by_status_filters_and_orders(self, sync_session):
        """list_by_status returns only matching rows, ordered by ticker (Story 3.2)."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(ticker="AAA", resolution_status=ResolutionStatus.RESOLVED)
            )
        )
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(
                    ticker="CCC", venue=None, resolution_status=ResolutionStatus.VENUE_UNRESOLVED
                )
            )
        )
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(
                    ticker="BBB", venue=None, resolution_status=ResolutionStatus.VENUE_UNRESOLVED
                )
            )
        )
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(ticker="DDD", resolution_status=ResolutionStatus.UNRESOLVED)
            )
        )
        sync_session.commit()

        rows = repo.list_by_status(ResolutionStatus.VENUE_UNRESOLVED)

        assert [r.ticker for r in rows] == ["BBB", "CCC"]

    def test_list_by_status_empty_returns_empty_list(self, sync_session):
        """list_by_status returns [] when no rows match the status."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(ticker="AAA", resolution_status=ResolutionStatus.RESOLVED)
            )
        )
        sync_session.commit()

        assert repo.list_by_status(ResolutionStatus.VENUE_UNRESOLVED) == []

    def test_list_by_status_translates_operational_error(self):
        """A DB OperationalError becomes DatabaseConnectionError (mirrors upsert)."""
        from unittest.mock import MagicMock

        from sqlalchemy.exc import OperationalError

        from src.db.exceptions import DatabaseConnectionError

        session = MagicMock()
        session.execute.side_effect = OperationalError("SELECT ...", {}, Exception("down"))
        repo = SyncInstrumentMetadataRepository(session)

        with pytest.raises(DatabaseConnectionError):
            repo.list_by_status(ResolutionStatus.VENUE_UNRESOLVED)

    def test_count_by_status_groups_and_counts(self, sync_session):
        """count_by_status returns a per-status count map (Story 3.4)."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(ticker="AAA", resolution_status=ResolutionStatus.RESOLVED)
            )
        )
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(ticker="BBB", resolution_status=ResolutionStatus.RESOLVED)
            )
        )
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(
                    ticker="CCC", venue=None, resolution_status=ResolutionStatus.VENUE_UNRESOLVED
                )
            )
        )
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(ticker="DDD", resolution_status=ResolutionStatus.UNRESOLVED)
            )
        )
        sync_session.commit()

        assert repo.count_by_status() == {
            ResolutionStatus.RESOLVED: 2,
            ResolutionStatus.VENUE_UNRESOLVED: 1,
            ResolutionStatus.UNRESOLVED: 1,
        }

    def test_count_by_status_empty_returns_empty_dict(self, sync_session):
        """count_by_status returns {} when the store is empty."""
        repo = SyncInstrumentMetadataRepository(sync_session)
        assert repo.count_by_status() == {}

    def test_count_by_status_translates_operational_error(self):
        """A DB OperationalError becomes DatabaseConnectionError (mirrors upsert)."""
        from unittest.mock import MagicMock

        from sqlalchemy.exc import OperationalError

        from src.db.exceptions import DatabaseConnectionError

        session = MagicMock()
        session.execute.side_effect = OperationalError("SELECT ...", {}, Exception("down"))
        repo = SyncInstrumentMetadataRepository(session)

        with pytest.raises(DatabaseConnectionError):
            repo.count_by_status()

    def test_apply_venue_override_flips_unresolved_to_resolved(self, sync_session):
        """An override sets venue + RESOLVED on an existing VENUE_UNRESOLVED row (Story 3.3)."""
        from datetime import datetime, timezone

        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(
                    ticker="SPY", venue=None, resolution_status=ResolutionStatus.VENUE_UNRESOLVED
                )
            )
        )
        sync_session.commit()

        ts = datetime(2026, 7, 13, tzinfo=timezone.utc)
        outcome = repo.apply_venue_override("SPY", "ARCA", ts)
        sync_session.commit()

        assert outcome == "applied"
        row = repo.get_by_ticker("SPY")
        assert row is not None
        assert row.venue == "ARCA"
        assert row.resolution_status == ResolutionStatus.RESOLVED
        # SQLite's TIMESTAMP column round-trips tz-naive; compare the wall value.
        assert row.resolved_at == ts.replace(tzinfo=None)

    def test_apply_venue_override_is_idempotent(self, sync_session):
        """Re-applying the same override is a no-op — no resolved_at churn (AC3)."""
        from datetime import datetime, timezone

        repo = SyncInstrumentMetadataRepository(sync_session)
        repo.upsert(
            InstrumentMetadata(
                **_make_metadata(
                    ticker="SPY", venue=None, resolution_status=ResolutionStatus.VENUE_UNRESOLVED
                )
            )
        )
        sync_session.commit()

        ts1 = datetime(2026, 7, 13, tzinfo=timezone.utc)
        repo.apply_venue_override("SPY", "ARCA", ts1)
        sync_session.commit()

        ts2 = datetime(2026, 7, 14, tzinfo=timezone.utc)
        outcome = repo.apply_venue_override("SPY", "ARCA", ts2)
        sync_session.commit()

        assert outcome == "unchanged"
        row = repo.get_by_ticker("SPY")
        assert row is not None
        assert row.resolved_at == ts1.replace(tzinfo=None)  # not overwritten by 2nd call

    def test_apply_venue_override_unmatched_ticker(self, sync_session):
        """An override for an absent ticker returns 'unmatched' and creates no row."""
        from datetime import datetime, timezone

        repo = SyncInstrumentMetadataRepository(sync_session)
        ts = datetime(2026, 7, 13, tzinfo=timezone.utc)

        outcome = repo.apply_venue_override("NOPE", "ARCA", ts)

        assert outcome == "unmatched"
        assert repo.get_by_ticker("NOPE") is None
