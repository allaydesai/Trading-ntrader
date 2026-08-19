"""Integration tests for the trading-session repositories (Story 2.2).

Sync tests use the shared ``sync_db_session`` fixture (pg8000, per-worker schema
isolation). Async tests define their own inline engine/session fixtures, matching
this directory's house style for async coverage (``test_backtest_repository.py``)
rather than the shared conftest, which only offers a sync fixture for this pattern.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.exceptions import DuplicateRecordError
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.db.repositories.trading_session_repository import TradingSessionRepository
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.session import SessionSpec, SessionStatus, StrategySpec


def _spec(strategy_id="sma_crossover", overrides=None, bar_types=None):
    from src.config import get_settings

    return SessionSpec(
        strategies=(
            StrategySpec.from_overrides(
                strategy_id=strategy_id,
                overrides=overrides or {"fast_period": 12},
                settings=get_settings(),
                bar_types=bar_types or ("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
            ),
        )
    )


def _backtest_run(repository: SyncBacktestRepository):
    from datetime import datetime, timezone

    return repository.create_backtest_run(
        run_id=uuid4(),
        strategy_name="SMA Crossover",
        strategy_type="trend_following",
        instrument_symbol="AAPL",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="IBKR",
        execution_status="success",
        execution_duration_seconds=Decimal("1.0"),
        config_snapshot={"version": "1.0"},
    )


@pytest.mark.integration
class TestSyncTradingSessionRepository:
    def test_create_persists_a_session_with_status_created(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        spec = _spec()

        session = repository.create(name="alpha-session", spec=spec.to_stored())
        sync_db_session.commit()

        assert session.id is not None
        assert session.session_id is not None
        assert session.name == "alpha-session"
        assert session.status == SessionStatus.CREATED
        assert session.linked_backtest_run_id is None

    def test_create_with_linked_backtest_run_id(self, sync_db_session):
        backtest_repo = SyncBacktestRepository(sync_db_session)
        run = _backtest_run(backtest_repo)
        sync_db_session.flush()

        repository = SyncTradingSessionRepository(sync_db_session)
        session = repository.create(
            name="compare-session", spec=_spec().to_stored(), linked_backtest_run_id=run.run_id
        )
        sync_db_session.commit()

        assert session.linked_backtest_run_id == run.run_id

    def test_create_duplicate_name_raises_duplicate_record_error(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        repository.create(name="dup-session", spec=_spec().to_stored())
        sync_db_session.commit()

        with pytest.raises(DuplicateRecordError, match="dup-session"):
            repository.create(name="dup-session", spec=_spec().to_stored())

    def test_find_by_session_id_returns_the_row(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="findable-session", spec=_spec().to_stored())
        sync_db_session.commit()

        found = repository.find_by_session_id(created.session_id)

        assert found is not None
        assert found.id == created.id

    def test_find_by_session_id_returns_none_when_missing(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)

        assert repository.find_by_session_id(uuid4()) is None

    def test_find_by_name_returns_the_row(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="named-session", spec=_spec().to_stored())
        sync_db_session.commit()

        found = repository.find_by_name("named-session")

        assert found is not None
        assert found.id == created.id

    def test_find_by_name_returns_none_when_missing(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)

        assert repository.find_by_name("does-not-exist") is None

    def test_find_all_returns_every_session(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        repository.create(name="session-one", spec=_spec().to_stored())
        repository.create(name="session-two", spec=_spec().to_stored())
        sync_db_session.commit()

        all_sessions = repository.find_all()

        assert {s.name for s in all_sessions} == {"session-one", "session-two"}

    def test_find_all_returns_newest_first(self, sync_db_session):
        """Story 2.8's `live list` renders this order; it must be deterministic."""
        repository = SyncTradingSessionRepository(sync_db_session)
        first = repository.create(name="older-session", spec=_spec().to_stored())
        second = repository.create(name="newer-session", spec=_spec().to_stored())
        sync_db_session.commit()

        names = [s.name for s in repository.find_all()]

        # Both rows share a created_at (now() is transaction-scoped), so the
        # id tiebreaker is what actually orders them here.
        assert second.id > first.id
        assert names == ["newer-session", "older-session"]

    def test_jsonb_round_trip_preserves_decimal(self, sync_db_session):
        """AC #6: the spec round-trips losslessly through the JSONB column."""
        repository = SyncTradingSessionRepository(sync_db_session)
        original = _spec(overrides={"fast_period": 12})

        created = repository.create(name="roundtrip-session", spec=original.to_stored())
        sync_db_session.commit()
        sync_db_session.expire_all()

        row = repository.find_by_session_id(created.session_id)
        restored = SessionSpec.from_stored(row.spec)

        assert restored == original
        assert type(restored.strategies[0].parameters["portfolio_value"]) is Decimal

    # AC #7/#8 shape guards (no update path; matching capability sets) moved to
    # tests/unit/db/test_trading_session_repository_shape.py — they need no
    # database, and CI --ignore's this whole directory, so they gated nothing here.

    def test_create_does_not_commit(self, sync_db_session):
        """The caller's context manager owns the transaction, not the repository."""
        repository = SyncTradingSessionRepository(sync_db_session)
        repository.create(name="uncommitted-session", spec=_spec().to_stored())

        sync_db_session.rollback()

        assert repository.find_by_name("uncommitted-session") is None


def _worker_id(request):
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


@pytest.fixture
async def async_test_engine(request):
    """Async test database engine with schema isolation (this directory's house style)."""
    from src.config import get_settings

    settings = get_settings()
    async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    schema_name = f"test_{_worker_id(request)}".replace("-", "_")

    engine = create_async_engine(async_url, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        await conn.execute(text(f"SET search_path TO {schema_name}"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
    await engine.dispose()


@pytest.fixture
async def async_session(async_test_engine, request):
    schema_name = f"test_{_worker_id(request)}".replace("-", "_")
    async_session_maker = async_sessionmaker(
        async_test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_maker() as session:
        await session.execute(text(f"SET search_path TO {schema_name}"))
        yield session


@pytest.mark.integration
class TestAsyncTradingSessionRepository:
    async def test_create_persists_a_session_with_status_created(self, async_session):
        repository = TradingSessionRepository(async_session)
        spec = _spec()

        session = await repository.create(name="async-alpha-session", spec=spec.to_stored())
        await async_session.commit()

        assert session.id is not None
        assert session.status == SessionStatus.CREATED

    async def test_create_duplicate_name_raises_duplicate_record_error(self, async_session):
        repository = TradingSessionRepository(async_session)
        await repository.create(name="async-dup-session", spec=_spec().to_stored())
        await async_session.commit()

        with pytest.raises(DuplicateRecordError, match="async-dup-session"):
            await repository.create(name="async-dup-session", spec=_spec().to_stored())

    async def test_find_by_session_id_returns_the_row(self, async_session):
        repository = TradingSessionRepository(async_session)
        created = await repository.create(name="async-findable", spec=_spec().to_stored())
        await async_session.commit()

        found = await repository.find_by_session_id(created.session_id)

        assert found is not None
        assert found.id == created.id

    async def test_find_by_name_returns_the_row(self, async_session):
        repository = TradingSessionRepository(async_session)
        created = await repository.create(name="async-named", spec=_spec().to_stored())
        await async_session.commit()

        found = await repository.find_by_name("async-named")

        assert found is not None
        assert found.id == created.id

    async def test_find_all_returns_every_session(self, async_session):
        repository = TradingSessionRepository(async_session)
        await repository.create(name="async-one", spec=_spec().to_stored())
        await repository.create(name="async-two", spec=_spec().to_stored())
        await async_session.commit()

        all_sessions = await repository.find_all()

        assert {s.name for s in all_sessions} == {"async-one", "async-two"}

    async def test_find_all_returns_newest_first(self, async_session):
        """Story 2.8's `live list` renders this order; it must be deterministic."""
        repository = TradingSessionRepository(async_session)
        first = await repository.create(name="async-older", spec=_spec().to_stored())
        second = await repository.create(name="async-newer", spec=_spec().to_stored())
        await async_session.commit()

        names = [s.name for s in await repository.find_all()]

        # Both rows share a created_at (now() is transaction-scoped), so the
        # id tiebreaker is what actually orders them here.
        assert second.id > first.id
        assert names == ["async-newer", "async-older"]

    # AC #7/#8 shape guards moved to
    # tests/unit/db/test_trading_session_repository_shape.py — see the note in
    # the sync class above.
