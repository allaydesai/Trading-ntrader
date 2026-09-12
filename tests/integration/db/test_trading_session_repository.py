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


def _running_session(repository, sync_db_session, *, name: str, owner_epoch: int = 0):
    """A row forced to ``running`` at a given epoch, for exercising the
    qualified heartbeat write directly (Story 3.6) — bypassing
    ``SessionService`` on purpose, since these tests are about the
    repository's own SQL, not the service's validation.
    """
    session = repository.create(name=name, spec=_spec().to_stored())
    sync_db_session.flush()
    session.status = SessionStatus.RUNNING
    session.owner_epoch = owner_epoch
    sync_db_session.commit()
    return session


@pytest.mark.integration
class TestSyncStampActivityIfOwner:
    """Story 3.6, retrospective D1: the heartbeat's whole contract in one
    epoch- and status-qualified ``UPDATE``, proven against real Postgres.
    """

    def test_a_matching_epoch_on_a_running_row_updates_and_returns_one(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = _running_session(repository, sync_db_session, name="stamp-hit", owner_epoch=5)
        at = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)

        rowcount = repository.stamp_activity_if_owner(session.id, owner_epoch=5, at=at)
        sync_db_session.commit()

        assert rowcount == 1
        refreshed = repository.find_by_session_id(session.session_id)
        assert refreshed is not None
        assert refreshed.last_heartbeat_at == at
        assert refreshed.last_bar_at is None

    def test_bar_seen_at_is_set_only_when_given_and_never_cleared(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = _running_session(repository, sync_db_session, name="stamp-bar", owner_epoch=0)
        first_bar = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        second_at = datetime(2026, 9, 12, 12, 0, 30, tzinfo=timezone.utc)

        repository.stamp_activity_if_owner(
            session.id, owner_epoch=0, at=first_bar, bar_seen_at=first_bar
        )
        sync_db_session.commit()
        repository.stamp_activity_if_owner(session.id, owner_epoch=0, at=second_at)
        sync_db_session.commit()

        refreshed = repository.find_by_session_id(session.session_id)
        assert refreshed is not None
        assert refreshed.last_heartbeat_at == second_at
        assert refreshed.last_bar_at == first_bar, (
            "a quiet interval must not clear the last real bar"
        )

    def test_a_mismatched_epoch_returns_zero_and_changes_nothing(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = _running_session(
            repository, sync_db_session, name="stamp-epoch-miss", owner_epoch=5
        )

        rowcount = repository.stamp_activity_if_owner(
            session.id, owner_epoch=999, at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        )
        sync_db_session.commit()

        assert rowcount == 0
        refreshed = repository.find_by_session_id(session.session_id)
        assert refreshed is not None
        assert refreshed.last_heartbeat_at is None

    def test_a_non_running_status_returns_zero_even_with_the_right_epoch(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = repository.create(name="stamp-status-miss", spec=_spec().to_stored())
        sync_db_session.commit()  # status stays CREATED, owner_epoch defaults to 0

        rowcount = repository.stamp_activity_if_owner(
            session.id, owner_epoch=0, at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        )

        assert rowcount == 0

    def test_a_missing_row_returns_zero(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)

        rowcount = repository.stamp_activity_if_owner(
            999_999_999, owner_epoch=0, at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        )

        assert rowcount == 0

    def test_a_post_stopped_write_refuses_ac8a(self, sync_db_session):
        """AC #8a: an adapter whose write runs after ``mark_stopped``
        committed gets rowcount 0 — the row is no longer ``running``.
        """
        repository = SyncTradingSessionRepository(sync_db_session)
        session = _running_session(repository, sync_db_session, name="stamp-post-stopped")
        session.status = SessionStatus.STOPPED
        sync_db_session.commit()

        rowcount = repository.stamp_activity_if_owner(
            session.id, owner_epoch=0, at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        )

        assert rowcount == 0


@pytest.mark.integration
class TestSyncInsertTradeIfAbsent:
    """Story 3.6: idempotent on ``(session_id, trade_id, client_order_id)`` —
    already two existing columns plus the FK, no new column (fact 2).
    """

    @staticmethod
    def _trade_row(session_id, *, trade_id="AAPL.NASDAQ-SMACrossover-000", client_order_id="O-1"):
        return Trade(
            session_id=session_id,
            instrument_id="AAPL.NASDAQ",
            trade_id=trade_id,
            venue_order_id="O-0",
            client_order_id=client_order_id,
            order_side="BUY",
            quantity=Decimal("10"),
            entry_price=Decimal("100.00"),
            exit_price=Decimal("110.00"),
            entry_timestamp=datetime(2026, 9, 12, 11, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=datetime(2026, 9, 12, 11, 5, 0, tzinfo=timezone.utc),
        )

    def test_a_new_trade_inserts_and_returns_true(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = repository.create(name="insert-trade-new", spec=_spec().to_stored())
        sync_db_session.flush()

        inserted = repository.insert_trade_if_absent(self._trade_row(session.id))
        sync_db_session.commit()

        assert inserted is True
        counts = repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (1, 0)

    def test_a_repeat_key_returns_false_and_leaves_exactly_one_row(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        session = repository.create(name="insert-trade-repeat", spec=_spec().to_stored())
        sync_db_session.flush()

        first = repository.insert_trade_if_absent(self._trade_row(session.id))
        sync_db_session.commit()
        second = repository.insert_trade_if_absent(self._trade_row(session.id))
        sync_db_session.commit()

        assert (first, second) == (True, False)
        counts = repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (1, 0), "the retry must not have created a second row"

    def test_a_backtest_owned_row_with_the_same_key_never_conflicts(self, sync_db_session):
        """NULL is distinct in a unique index — a backtest row (``session_id``
        NULL) inserts freely against a session-owned row sharing the same
        ``trade_id``/``client_order_id``.
        """
        repository = SyncTradingSessionRepository(sync_db_session)
        backtest_repo = SyncBacktestRepository(sync_db_session)
        run = _backtest_run(backtest_repo)
        sync_db_session.flush()
        session = repository.create(name="insert-trade-null-distinct", spec=_spec().to_stored())
        sync_db_session.flush()
        repository.insert_trade_if_absent(self._trade_row(session.id))

        backtest_row = self._trade_row(None)
        backtest_row.backtest_run_id = run.id

        inserted = repository.insert_trade_if_absent(backtest_row)
        sync_db_session.commit()

        assert inserted is True
        counts = repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (1, 0), "the backtest row must not have joined this session"

    def test_a_row_with_neither_owner_still_violates_chk_trades_owner(self, sync_db_session):
        """The pre-existing CHECK constraint is untouched by this story's
        index — still enforced, not silently loosened.

        pg8000 (this fixture's driver) surfaces a CHECK violation as
        ``ProgrammingError``, not ``IntegrityError`` — measured here, not
        assumed from psycopg2's mapping.
        """
        from sqlalchemy.exc import DatabaseError

        repository = SyncTradingSessionRepository(sync_db_session)
        orphan_row = self._trade_row(None)
        orphan_row.backtest_run_id = None

        with pytest.raises(DatabaseError, match="chk_trades_owner"):
            repository.insert_trade_if_absent(orphan_row)
        sync_db_session.rollback()


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


async def _async_running_session(repository, async_session, *, name: str, owner_epoch: int = 0):
    from src.models.session import SessionStatus

    session = await repository.create(name=name, spec=_spec().to_stored())
    await async_session.flush()
    session.status = SessionStatus.RUNNING
    session.owner_epoch = owner_epoch
    await async_session.commit()
    return session


@pytest.mark.integration
class TestAsyncStampActivityIfOwner:
    """AR9's async twin — Story 3.6, retrospective D1. Tested against real
    Postgres, not "for symmetry" (the story's own words): the async fixture
    is a genuinely separate code path (asyncpg, not pg8000/psycopg2).
    """

    async def test_a_matching_epoch_on_a_running_row_updates_and_returns_one(self, async_session):
        repository = TradingSessionRepository(async_session)
        session = await _async_running_session(
            repository, async_session, name="async-stamp-hit", owner_epoch=5
        )
        at = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)

        rowcount = await repository.stamp_activity_if_owner(session.id, owner_epoch=5, at=at)
        await async_session.commit()

        assert rowcount == 1
        refreshed = await repository.find_by_session_id(session.session_id)
        assert refreshed is not None
        assert refreshed.last_heartbeat_at == at

    async def test_a_mismatched_epoch_returns_zero_and_changes_nothing(self, async_session):
        repository = TradingSessionRepository(async_session)
        session = await _async_running_session(
            repository, async_session, name="async-stamp-miss", owner_epoch=5
        )

        rowcount = await repository.stamp_activity_if_owner(
            session.id, owner_epoch=999, at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
        )

        assert rowcount == 0
        refreshed = await repository.find_by_session_id(session.session_id)
        assert refreshed is not None
        assert refreshed.last_heartbeat_at is None


@pytest.mark.integration
class TestAsyncInsertTradeIfAbsent:
    """AR9's async twin for the idempotent trade insert."""

    async def test_a_new_trade_inserts_and_a_repeat_key_returns_false(self, async_session):
        repository = TradingSessionRepository(async_session)
        session = await repository.create(name="async-insert-trade", spec=_spec().to_stored())
        await async_session.flush()
        trade = Trade(
            session_id=session.id,
            instrument_id="AAPL.NASDAQ",
            trade_id="AAPL.NASDAQ-SMACrossover-000",
            venue_order_id="O-0",
            client_order_id="O-1",
            order_side="BUY",
            quantity=Decimal("10"),
            entry_price=Decimal("100.00"),
            exit_price=Decimal("110.00"),
            entry_timestamp=datetime(2026, 9, 12, 11, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=datetime(2026, 9, 12, 11, 5, 0, tzinfo=timezone.utc),
        )

        first = await repository.insert_trade_if_absent(trade)
        await async_session.commit()

        second_trade = Trade(
            session_id=session.id,
            instrument_id="AAPL.NASDAQ",
            trade_id="AAPL.NASDAQ-SMACrossover-000",
            venue_order_id="O-0",
            client_order_id="O-1",
            order_side="BUY",
            quantity=Decimal("10"),
            entry_price=Decimal("100.00"),
            exit_price=Decimal("110.00"),
            entry_timestamp=datetime(2026, 9, 12, 11, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=datetime(2026, 9, 12, 11, 5, 0, tzinfo=timezone.utc),
        )
        second = await repository.insert_trade_if_absent(second_trade)
        await async_session.commit()

        assert (first, second) == (True, False)
        counts = await repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (1, 0)
