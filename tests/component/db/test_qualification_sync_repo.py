"""Component tests for qualification sync against a real SQLAlchemy session.

The unit tests use fakes to pin the decision logic. These exercise the parts fakes
cannot: the real cross-table join behind the gate's qualification term, the real
streaming reader that must not truncate at 100 rows, and the real
``InstrumentMapper``/``upsert`` write path — so "0 changed on a second run" is
proven against a database, not a spy.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.db.models.catalog_instrument import CatalogInstrument
from src.db.models.instrument_metadata import InstrumentMetadata
from src.db.repositories.catalog_instrument_repository import SyncCatalogInstrumentRepository
from src.db.repositories.instrument_metadata_repository_sync import (
    SyncInstrumentMetadataRepository,
)
from src.models.instrument_metadata import ResolutionStatus
from src.services.firstrate.instrument_mapper import InstrumentMapper
from src.services.metadata.qualification_sync import sync_resolved_qualifications

CATALOG = "firstrate-etf"


# SQLite DDL, matching the convention of the sibling component tests. Hand-written
# rather than create_all for two reasons: a whole-metadata create_all drags in
# backtest_runs, whose JSONB column SQLite cannot render; and the ORM's BigInteger
# primary key does not autoincrement on SQLite, which only does that for INTEGER.
#
# Note nautilus_id/exchange/name are NULLable here — unlike the older sibling DDL,
# which still carries the pre-repair NOT NULL shape. Nulling those columns is how a
# ticker is held out of backtests, so a test that could not store NULL could not
# exercise the reverse-direction sync at all.
_CREATE_CATALOG_INSTRUMENTS = """
CREATE TABLE catalog_instruments (
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
    date_range_end_daily TIMESTAMP,
    date_range_end_hourly TIMESTAMP,
    date_range_end_minute TIMESTAMP,
    date_range_end_5min TIMESTAMP,
    date_range_end_30min TIMESTAMP,
    bar_count_daily INTEGER NOT NULL DEFAULT 0,
    bar_count_hourly INTEGER NOT NULL DEFAULT 0,
    bar_count_minute INTEGER NOT NULL DEFAULT 0,
    bar_count_5min INTEGER NOT NULL DEFAULT 0,
    bar_count_30min INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (catalog_name, ticker)
)
"""

_CREATE_INSTRUMENT_METADATA = """
CREATE TABLE instrument_metadata (
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
def session():
    """In-memory SQLite session with both tables the sync spans."""
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(_CREATE_CATALOG_INSTRUMENTS))
        conn.execute(text(_CREATE_INSTRUMENT_METADATA))
    with sessionmaker(bind=engine)() as s:
        yield s


def _meta(ticker: str, venue, status: ResolutionStatus) -> InstrumentMetadata:
    return InstrumentMetadata(
        ticker=ticker,
        metadata_provider="FMP",
        venue=venue,
        resolution_status=status,
    )


def _catalog_row(ticker: str, nautilus_id, exchange) -> CatalogInstrument:
    return CatalogInstrument(
        ticker=ticker,
        nautilus_id=nautilus_id,
        exchange=exchange,
        name=f"{ticker} Fund",
        asset_class="ETF",
        catalog_name=CATALOG,
        bar_count_daily=0,
        bar_count_hourly=0,
        bar_count_minute=0,
        bar_count_5min=0,
        bar_count_30min=0,
    )


def _sync(session):
    cat_repo = SyncCatalogInstrumentRepository(session)
    return sync_resolved_qualifications(
        meta_repo=SyncInstrumentMetadataRepository(session),
        catalog_repo=cat_repo,
        mapper=InstrumentMapper(cat_repo),
        catalog_name=CATALOG,
    )


@pytest.mark.component
class TestQualificationSyncAgainstRealSession:
    """End-to-end convergence through the real mapper and repositories."""

    def test_resolved_ticker_gets_its_identity_written(self, session):
        session.add(_meta("AAA", "ARCA", ResolutionStatus.RESOLVED))
        session.add(_catalog_row("AAA", None, None))
        session.flush()

        result = _sync(session)

        row = SyncCatalogInstrumentRepository(session).get_by_ticker(CATALOG, "AAA")
        assert result.synced == 1
        assert row.nautilus_id == "AAA.ARCA"
        assert row.exchange == "ARCA"

    def test_excluded_ticker_loses_its_stale_identity(self, session):
        session.add(_meta("ZZZZ", None, ResolutionStatus.EXCLUDED))
        session.add(_catalog_row("ZZZZ", "ZZZZ.AMEX", "AMEX"))
        session.flush()

        result = _sync(session)

        row = SyncCatalogInstrumentRepository(session).get_by_ticker(CATALOG, "ZZZZ")
        assert result.cleared == 1
        assert row.nautilus_id is None

    def test_second_run_writes_nothing(self, session):
        """Convergence proven against a database, not a spy."""
        session.add(_meta("AAA", "ARCA", ResolutionStatus.RESOLVED))
        session.add(_meta("ZZZZ", None, ResolutionStatus.EXCLUDED))
        session.add(_catalog_row("AAA", None, None))
        session.add(_catalog_row("ZZZZ", "ZZZZ.AMEX", "AMEX"))
        session.flush()

        first = _sync(session)
        second = _sync(session)

        assert first.changed == 2
        assert second.changed == 0
        assert second.unchanged == 2

    def test_bar_counts_and_date_ranges_survive_a_sync(self, session):
        """upsert copies every column — the sweep must not clobber import evidence."""
        session.add(_meta("AAA", "ARCA", ResolutionStatus.RESOLVED))
        row = _catalog_row("AAA", None, None)
        row.bar_count_daily = 5432
        row.bar_count_minute = 987654
        session.add(row)
        session.flush()

        _sync(session)

        fresh = SyncCatalogInstrumentRepository(session).get_by_ticker(CATALOG, "AAA")
        assert fresh.bar_count_daily == 5432
        assert fresh.bar_count_minute == 987654


@pytest.mark.component
class TestCatalogRepositoryAdditions:
    """The two new readers the sync and the gate depend on."""

    def test_iter_by_catalog_returns_more_than_the_list_page_size(self, session):
        """list_by_catalog defaults to limit=100 and would silently truncate."""
        for i in range(250):
            session.add(_catalog_row(f"T{i:04d}", None, None))
        session.flush()

        streamed = list(SyncCatalogInstrumentRepository(session).iter_by_catalog(CATALOG))

        assert len(streamed) == 250
        assert [r.ticker for r in streamed] == sorted(r.ticker for r in streamed)

    def test_iter_by_catalog_is_scoped_to_one_catalog(self, session):
        session.add(_catalog_row("AAA", None, None))
        other = _catalog_row("BBB", None, None)
        other.catalog_name = "firstrate-stocks"
        session.add(other)
        session.flush()

        streamed = list(SyncCatalogInstrumentRepository(session).iter_by_catalog(CATALOG))

        assert [r.ticker for r in streamed] == ["AAA"]

    def test_count_unqualified_resolved_counts_only_resolved_gaps(self, session):
        """EXCLUDED and VENUE_UNRESOLVED rows are *supposed* to have a NULL id."""
        session.add(_meta("GAP", "ARCA", ResolutionStatus.RESOLVED))
        session.add(_catalog_row("GAP", None, None))
        session.add(_meta("OK", "ARCA", ResolutionStatus.RESOLVED))
        session.add(_catalog_row("OK", "OK.ARCA", "ARCA"))
        session.add(_meta("EXCL", None, ResolutionStatus.EXCLUDED))
        session.add(_catalog_row("EXCL", None, None))
        session.add(_meta("UNRES", None, ResolutionStatus.VENUE_UNRESOLVED))
        session.add(_catalog_row("UNRES", None, None))
        session.flush()

        gap = SyncCatalogInstrumentRepository(session).count_unqualified_resolved(CATALOG)

        assert gap == 1

    def test_count_unqualified_resolved_is_scoped_to_one_catalog(self, session):
        session.add(_meta("GAP", "ARCA", ResolutionStatus.RESOLVED))
        other = _catalog_row("GAP", None, None)
        other.catalog_name = "firstrate-stocks"
        session.add(other)
        session.flush()

        repo = SyncCatalogInstrumentRepository(session)

        assert repo.count_unqualified_resolved(CATALOG) == 0
        assert repo.count_unqualified_resolved("firstrate-stocks") == 1


@pytest.mark.component
class TestExclusionRepository:
    """`apply_venue_exclusion` against a real session."""

    def test_apply_sets_excluded_and_leaves_venue_null(self, session):
        from datetime import datetime, timezone

        session.add(_meta("ZZZZ", "AMEX", ResolutionStatus.VENUE_UNRESOLVED))
        session.flush()
        repo = SyncInstrumentMetadataRepository(session)

        outcome = repo.apply_venue_exclusion("ZZZZ", "delisted", datetime.now(timezone.utc))

        row = repo.get_by_ticker("ZZZZ")
        assert outcome == "applied"
        assert row.resolution_status == ResolutionStatus.EXCLUDED
        assert row.venue is None

    def test_reapply_is_unchanged(self, session):
        from datetime import datetime, timezone

        session.add(_meta("ZZZZ", None, ResolutionStatus.EXCLUDED))
        session.flush()
        repo = SyncInstrumentMetadataRepository(session)

        assert (
            repo.apply_venue_exclusion("ZZZZ", "delisted", datetime.now(timezone.utc))
            == "unchanged"
        )

    def test_unknown_ticker_is_unmatched_not_fabricated(self, session):
        from datetime import datetime, timezone

        repo = SyncInstrumentMetadataRepository(session)

        outcome = repo.apply_venue_exclusion("NOPE", "delisted", datetime.now(timezone.utc))

        assert outcome == "unmatched"
        assert repo.get_by_ticker("NOPE") is None

    def test_list_all_returns_every_status(self, session):
        session.add(_meta("AAA", "ARCA", ResolutionStatus.RESOLVED))
        session.add(_meta("BBB", None, ResolutionStatus.VENUE_UNRESOLVED))
        session.add(_meta("CCC", None, ResolutionStatus.EXCLUDED))
        session.flush()

        rows = SyncInstrumentMetadataRepository(session).list_all()

        assert [r.ticker for r in rows] == ["AAA", "BBB", "CCC"]
