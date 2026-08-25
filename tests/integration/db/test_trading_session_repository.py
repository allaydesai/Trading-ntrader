"""Integration tests for the trading-session repositories (Story 2.2).

Sync tests use the shared ``sync_db_session`` fixture (pg8000, per-worker schema
isolation). Async tests define their own inline engine/session fixtures, matching
this directory's house style for async coverage (``test_backtest_repository.py``)
rather than the shared conftest, which only offers a sync fixture for this pattern.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.exceptions import DuplicateRecordError
from src.db.models.trade import Trade
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


def _trade(session_id: int, *, exit_timestamp=None, trade_id: str) -> Trade:
    """A minimal ``Trade`` row owned by a session (Story 2.8, AC #8)."""
    return Trade(
        session_id=session_id,
        instrument_id="AAPL",
        trade_id=trade_id,
        venue_order_id=f"order-{trade_id}",
        order_side="BUY",
        quantity=Decimal("10"),
        entry_price=Decimal("100.00"),
        exit_price=Decimal("110.00") if exit_timestamp is not None else None,
        entry_timestamp=datetime(2026, 8, 24, 10, 0, 0, tzinfo=timezone.utc),
        exit_timestamp=exit_timestamp,
    )


@pytest.mark.integration
class TestSyncTradeCountsBySession:
    """AC #8: closed/open trade counts, joined on the typed BigInteger key."""

    def test_counts_closed_and_open_trades_separately(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = repository.create(name="counts-session", spec=_spec().to_stored())
        sync_db_session.flush()

        closed_at = datetime(2026, 8, 24, 11, 0, 0, tzinfo=timezone.utc)
        sync_db_session.add_all(
            [
                _trade(session.id, trade_id="t1", exit_timestamp=closed_at),
                _trade(session.id, trade_id="t2", exit_timestamp=closed_at),
                _trade(session.id, trade_id="t3", exit_timestamp=None),
            ]
        )
        sync_db_session.commit()

        counts = repository.trade_counts_by_session([session.id])

        assert counts[session.id] == (2, 1)

    def test_a_session_with_no_trades_reports_zero_and_zero(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = repository.create(name="empty-counts-session", spec=_spec().to_stored())
        sync_db_session.commit()

        counts = repository.trade_counts_by_session([session.id])

        assert counts[session.id] == (0, 0)

    def test_counts_do_not_leak_across_sessions(self, sync_db_session):
        """The join is per-session — one session's trades must not bleed into another's."""
        repository = SyncTradingSessionRepository(sync_db_session)
        first = repository.create(name="counts-session-a", spec=_spec().to_stored())
        second = repository.create(name="counts-session-b", spec=_spec().to_stored())
        sync_db_session.flush()

        closed_at = datetime(2026, 8, 24, 11, 0, 0, tzinfo=timezone.utc)
        sync_db_session.add_all(
            [
                _trade(first.id, trade_id="a1", exit_timestamp=closed_at),
                _trade(second.id, trade_id="b1", exit_timestamp=None),
                _trade(second.id, trade_id="b2", exit_timestamp=None),
            ]
        )
        sync_db_session.commit()

        counts = repository.trade_counts_by_session([first.id, second.id])

        assert counts[first.id] == (1, 0)
        assert counts[second.id] == (0, 2)

    def test_no_argument_covers_every_session(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = repository.create(name="all-sessions-counts", spec=_spec().to_stored())
        sync_db_session.flush()
        sync_db_session.add(
            _trade(
                session.id,
                trade_id="all1",
                exit_timestamp=datetime(2026, 8, 24, 11, 0, 0, tzinfo=timezone.utc),
            )
        )
        sync_db_session.commit()

        counts = repository.trade_counts_by_session()

        assert counts[session.id] == (1, 0)

    def test_the_join_is_on_the_internal_id_not_the_business_uuid(self, sync_db_session):
        """AC #8's typed-key check, in two halves — the first of which was
        missing.

        1. **The repository's own query returns the right counts.** The first
           version of this test built its own deliberately-wrong ``select()``
           and asserted Postgres refused it, without ever calling
           ``trade_counts_by_session`` or asserting any count — so it would
           have passed unchanged had the repository joined on the UUID. It
           tested the database, filed under a name that claimed to test the
           code.
        2. **The wrong join cannot silently return a wrong answer.**
           ``trades.session_id`` is a ``BigInteger`` FK to
           ``trading_sessions.id``; the UUID business key shares its name but
           not its type, so Postgres rejects the comparison outright rather
           than quietly matching nothing.
        """
        from sqlalchemy import select
        from sqlalchemy.exc import DBAPIError

        from src.db.models.trading_session import TradingSession

        repository = SyncTradingSessionRepository(sync_db_session)
        session = repository.create(name="typed-key-session", spec=_spec().to_stored())
        sync_db_session.flush()
        sync_db_session.add_all(
            [
                _trade(
                    session.id,
                    trade_id="typed1",
                    exit_timestamp=datetime(2026, 8, 24, 11, 0, 0, tzinfo=timezone.utc),
                ),
                _trade(session.id, trade_id="typed2", exit_timestamp=None),
            ]
        )
        sync_db_session.commit()

        # Half 1: the production query, exercised.
        counts = repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (1, 1)

        # Half 2: the alternative join is a type error, not a silent zero.
        wrong_join = (
            select(Trade.id)
            .select_from(Trade)
            .join(TradingSession, Trade.session_id == TradingSession.session_id)
        )
        with pytest.raises(DBAPIError):
            sync_db_session.execute(wrong_join)
        sync_db_session.rollback()


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


@pytest.mark.integration
class TestAsyncTradeCountsBySession:
    """AC #8, AR9's async twin: same behaviour, same typed-key join."""

    async def test_counts_closed_and_open_trades_separately(self, async_session):
        repository = TradingSessionRepository(async_session)
        session = await repository.create(name="async-counts-session", spec=_spec().to_stored())
        await async_session.flush()

        closed_at = datetime(2026, 8, 24, 11, 0, 0, tzinfo=timezone.utc)
        async_session.add_all(
            [
                _trade(session.id, trade_id="async-t1", exit_timestamp=closed_at),
                _trade(session.id, trade_id="async-t2", exit_timestamp=None),
            ]
        )
        await async_session.commit()

        counts = await repository.trade_counts_by_session([session.id])

        assert counts[session.id] == (1, 1)

    async def test_a_session_with_no_trades_reports_zero_and_zero(self, async_session):
        repository = TradingSessionRepository(async_session)
        session = await repository.create(
            name="async-empty-counts-session", spec=_spec().to_stored()
        )
        await async_session.commit()

        counts = await repository.trade_counts_by_session([session.id])

        assert counts[session.id] == (0, 0)
