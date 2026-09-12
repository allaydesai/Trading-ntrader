"""Integration tests for ``SqlTradeRecord`` against real Postgres (Story 3.6).

CI ``--ignore``s this whole directory (D2 not landed), so nothing here gates a
PR — this file is behavioural *evidence*, not a gate. Every falsifiable
property already lives in the unit tier
(``tests/unit/services/test_trade_record_adapter.py``); what only a real
database can prove is covered here: the AC #1 proof through
``trade_counts_by_session``, the AC #4 idempotent re-persist, the AC #7
refusal after a real concurrent reclaim, AC #8a's post-``stopped`` refusal,
and AC #3's structural proof (a factory that raises on commit leaves no row).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from src.core.live_session_record import SessionReclaimedError
from src.core.live_trade_recorder import RecordedTrade
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.session import SessionSpec, SessionStatus, StrategySpec
from src.models.trade import TradeBase
from src.services.session_record import SqlSessionRecord
from src.services.session_service import SessionService
from src.services.trade_record import SqlTradeRecord


def _second_connection(request):
    """A second, independent pg8000 connection into the same scratch schema
    (the ``test_session_service.py`` helper, reproduced). The caller closes
    and disposes both."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.config import get_settings

    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    schema_name = f"test_{worker_id}".replace("-", "_")
    pg8000_url = get_settings().database_url.replace("postgresql://", "postgresql+pg8000://")
    engine = create_engine(pg8000_url, echo=False)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.execute(text(f"SET search_path TO {schema_name}"))
    return session, engine


def _spec() -> SessionSpec:
    from src.config import get_settings

    return SessionSpec(
        strategies=(
            StrategySpec.from_overrides(
                strategy_id="sma_crossover",
                overrides={"fast_period": 12},
                settings=get_settings(),
                bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
            ),
        )
    )


def _recorded_trade(
    *, trade_id="AAPL.NASDAQ-SMACrossover-000", client_order_id="O-2"
) -> RecordedTrade:
    trade = TradeBase(
        instrument_id="AAPL.NASDAQ",
        trade_id=trade_id,
        venue_order_id="O-1",
        client_order_id=client_order_id,
        order_side="BUY",
        quantity=Decimal("10.00000000"),
        entry_price=Decimal("100.00000000"),
        exit_price=Decimal("110.00000000"),
        commission_amount=Decimal("1.50000000"),
        commission_currency="USD",
        fees_amount=Decimal("0.00"),
        entry_timestamp=datetime(2026, 9, 12, 14, 0, 0, tzinfo=UTC),
        exit_timestamp=datetime(2026, 9, 12, 14, 5, 0, tzinfo=UTC),
    )
    return RecordedTrade(
        trade=trade,
        profit_loss=Decimal("98.50000000"),
        profit_pct=Decimal("10.00000000"),
        holding_period_seconds=300,
        position_id=trade_id,
        strategy_id="SMACrossover-000",
        fill_count=2,
        trade_key=f"{trade_id}:{client_order_id}",
    )


def _committing_factory(sync_db_session):
    """A ``session_factory`` that reuses the fixture's session but commits on
    clean exit, matching ``get_sync_session``'s real contract.

    Plain ``lambda: sync_db_session`` is wrong here: ``Session`` is itself a
    context manager whose ``__exit__`` **closes** (and therefore rolls back
    an uncommitted transaction) rather than commits — using it directly as
    the factory silently rolled back every write ``persist()`` made, the
    instant its own ``with`` block exited (measured: every AC #1/#2/#4 proof
    failed with an empty table before this fix).
    """
    from contextlib import contextmanager

    @contextmanager
    def _factory():
        yield sync_db_session
        sync_db_session.commit()

    return _factory


def _claimed_running_session(sync_db_session, *, name: str):
    """A real session, claimed to ``running`` through the guarded path — the
    same shape ``claim_session`` produces, so the sink is exercised the way
    the CLI actually constructs it.
    """
    repository = SyncTradingSessionRepository(sync_db_session)
    created = repository.create(name=name, spec=_spec().to_stored())
    sync_db_session.commit()
    service = SessionService(repository)
    started = service.transition(created.session_id, to=SessionStatus.RUNNING)
    sync_db_session.commit()
    return started


@pytest.mark.integration
class TestSqlTradeRecordAgainstRealPostgres:
    def test_ac1_a_persisted_trade_is_counted_by_trade_counts_by_session(self, sync_db_session):
        session = _claimed_running_session(sync_db_session, name="trade-record-ac1")
        record = SqlTradeRecord(
            session.id,
            owner_epoch=session.owner_epoch,
            session_factory=_committing_factory(sync_db_session),
        )

        inserted = record.persist(_recorded_trade())
        sync_db_session.commit()

        assert inserted is True
        repository = SyncTradingSessionRepository(sync_db_session)
        counts = repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (1, 0)

    def test_ac2_every_field_round_trips_through_postgres_as_the_same_decimal(
        self, sync_db_session
    ):
        session = _claimed_running_session(sync_db_session, name="trade-record-ac2")
        record = SqlTradeRecord(
            session.id,
            owner_epoch=session.owner_epoch,
            session_factory=_committing_factory(sync_db_session),
        )
        recorded = _recorded_trade()

        record.persist(recorded)
        sync_db_session.commit()

        row = sync_db_session.execute(
            text(
                "SELECT entry_price, exit_price, quantity, commission_amount, commission_currency, "
                "fees_amount, profit_loss, profit_pct, holding_period_seconds, venue_order_id, "
                "client_order_id, order_side, instrument_id, trade_id, backtest_run_id, "
                "entry_timestamp, exit_timestamp FROM trades WHERE session_id = :sid"
            ),
            {"sid": session.id},
        ).one()
        # Review 2026-09-12: every Decimal the AC names, not four of them.
        assert Decimal(row.entry_price) == recorded.trade.entry_price
        assert Decimal(row.exit_price) == recorded.trade.exit_price
        assert Decimal(row.quantity) == recorded.trade.quantity
        assert Decimal(row.commission_amount) == recorded.trade.commission_amount
        assert Decimal(row.fees_amount) == recorded.trade.fees_amount
        assert Decimal(row.profit_loss) == recorded.profit_loss
        assert Decimal(row.profit_pct) == recorded.profit_pct
        assert row.holding_period_seconds == recorded.holding_period_seconds
        assert row.commission_currency == recorded.trade.commission_currency
        assert row.venue_order_id == recorded.trade.venue_order_id
        assert row.client_order_id == recorded.trade.client_order_id
        assert row.order_side == recorded.trade.order_side
        assert row.instrument_id == recorded.trade.instrument_id
        assert row.trade_id == recorded.trade.trade_id
        assert row.backtest_run_id is None
        assert row.entry_timestamp.utcoffset() is not None, "must be tz-aware"
        assert row.exit_timestamp.utcoffset() is not None, "must be tz-aware"
        assert row.entry_timestamp == recorded.trade.entry_timestamp
        assert row.exit_timestamp == recorded.trade.exit_timestamp

    def test_ac4_a_retried_persist_is_idempotent(self, sync_db_session):
        session = _claimed_running_session(sync_db_session, name="trade-record-ac4")
        record = SqlTradeRecord(
            session.id,
            owner_epoch=session.owner_epoch,
            session_factory=_committing_factory(sync_db_session),
        )
        recorded = _recorded_trade()

        first = record.persist(recorded)
        sync_db_session.commit()
        second = record.persist(recorded)
        sync_db_session.commit()

        assert (first, second) == (True, False)
        repository = SyncTradingSessionRepository(sync_db_session)
        counts = repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (1, 0), "the retry must not have created a second row"

    def test_ac7_a_reclaimed_session_refuses_the_write_and_inserts_nothing(
        self, sync_db_session, request
    ):
        """A second `transition(to=RUNNING)` wins the reclaim; the first
        adapter's `persist` — still bound to the stale epoch — raises
        `SessionReclaimedError` and the trade table is left empty.
        """
        session = _claimed_running_session(sync_db_session, name="trade-record-ac7")
        stale_epoch = session.owner_epoch
        record = SqlTradeRecord(
            session.id,
            owner_epoch=stale_epoch,
            session_factory=_committing_factory(sync_db_session),
        )

        # Force the heartbeat stale, then reclaim from a second connection.
        session.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=200)
        sync_db_session.commit()

        session_two, engine_two = _second_connection(request)
        try:
            SessionService(SyncTradingSessionRepository(session_two)).transition(
                session.session_id, to=SessionStatus.RUNNING
            )
            session_two.commit()

            with pytest.raises(SessionReclaimedError):
                record.persist(_recorded_trade())
            sync_db_session.rollback()

            repository = SyncTradingSessionRepository(sync_db_session)
            counts = repository.trade_counts_by_session([session.id])
            assert counts[session.id] == (0, 0)
        finally:
            session_two.rollback()
            session_two.close()
            engine_two.dispose()

    def test_ac7_every_port_write_of_the_dispossessed_process_is_refused(
        self, sync_db_session, request
    ):
        """AC #7's named integration proof, in full (review 2026-09-12): after
        a real second-connection reclaim, the first process's ``record_activity``,
        ``mark_stopped``, ``record_strategy_failure`` **and** ``persist`` all
        raise ``SessionReclaimedError``, and the row and the trade table are
        unchanged — the winner's epoch, ``running``, no ``runtime_flags``, no
        trades.
        """
        session = _claimed_running_session(sync_db_session, name="trade-record-ac7-all")
        stale_epoch = session.owner_epoch
        factory = _committing_factory(sync_db_session)
        session_record = SqlSessionRecord(
            session.session_id, owner_epoch=stale_epoch, session_factory=factory
        )
        trade_record = SqlTradeRecord(session.id, owner_epoch=stale_epoch, session_factory=factory)
        session.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=200)
        sync_db_session.commit()

        session_two, engine_two = _second_connection(request)
        try:
            winner = SessionService(SyncTradingSessionRepository(session_two)).transition(
                session.session_id, to=SessionStatus.RUNNING
            )
            session_two.commit()
            assert winner.owner_epoch == stale_epoch + 1

            now = datetime.now(UTC)
            with pytest.raises(SessionReclaimedError) as refused:
                session_record.record_activity(at=now, bar_seen_at=now)
            sync_db_session.rollback()
            # The wording must name the epoch that refused, not the stale
            # identity-mapped copy (review 2026-09-12).
            assert f"owner_epoch is {stale_epoch + 1}, not this process's {stale_epoch}" in str(
                refused.value
            )
            with pytest.raises(SessionReclaimedError):
                session_record.record_strategy_failure(
                    strategy_id="SMACrossover-000",
                    spec_strategy_id="sma_crossover",
                    error_type="RuntimeError",
                    handler="on_bar",
                    at=now,
                    detail="boom",
                    all_failed=False,
                )
            sync_db_session.rollback()
            with pytest.raises(SessionReclaimedError):
                trade_record.persist(_recorded_trade())
            sync_db_session.rollback()
            with pytest.raises(SessionReclaimedError):
                session_record.mark_stopped()
            sync_db_session.rollback()

            row = sync_db_session.execute(
                text(
                    "SELECT status::text, owner_epoch, runtime_flags, last_heartbeat_at "
                    "FROM trading_sessions WHERE id = :pk"
                ),
                {"pk": session.id},
            ).one()
            assert row[0] == "running"
            assert row[1] == stale_epoch + 1
            assert row[2] is None
            assert row[3] == winner.last_heartbeat_at, "the loser's heartbeat never landed"
            repository = SyncTradingSessionRepository(sync_db_session)
            assert repository.trade_counts_by_session([session.id])[session.id] == (0, 0)
        finally:
            session_two.rollback()
            session_two.close()
            engine_two.dispose()

    def test_ac8a_a_post_stopped_write_refuses(self, sync_db_session):
        session = _claimed_running_session(sync_db_session, name="trade-record-ac8a")
        record = SqlTradeRecord(
            session.id,
            owner_epoch=session.owner_epoch,
            session_factory=_committing_factory(sync_db_session),
        )
        service = SessionService(SyncTradingSessionRepository(sync_db_session))
        service.transition(
            session.session_id, to=SessionStatus.STOPPED, owner_epoch=session.owner_epoch
        )
        sync_db_session.commit()

        with pytest.raises(SessionReclaimedError):
            record.persist(_recorded_trade())
        sync_db_session.rollback()

        repository = SyncTradingSessionRepository(sync_db_session)
        counts = repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (0, 0)

    def test_ac3_a_factory_that_raises_on_commit_leaves_no_row(
        self, sync_db_session, request, monkeypatch
    ):
        """Structural proof: the factory context manager owns the commit —
        a failure there must leave nothing behind, not a half-written row.

        Review 2026-09-12: drives the **real** ``get_sync_session`` (its
        ``SyncSessionLocal`` pointed at this worker's scratch schema on a
        session whose ``commit`` raises), then reads back on the fixture's
        own connection. The earlier version's stub rolled back itself before
        raising and so proved only the stub.
        """
        from sqlalchemy.orm import Session, sessionmaker

        import src.db.session_sync as session_sync

        session = _claimed_running_session(sync_db_session, name="trade-record-ac3")
        worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
        schema_name = f"test_{worker_id}".replace("-", "_")
        seen: dict[str, bool] = {}

        class _CommitExplodes(Session):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.execute(text(f"SET search_path TO {schema_name}"))

            def commit(self):
                seen["commit_attempted"] = True
                raise RuntimeError("commit exploded")

            def rollback(self):
                seen["rolled_back"] = True
                super().rollback()

        monkeypatch.setattr(
            session_sync,
            "SyncSessionLocal",
            sessionmaker(
                bind=sync_db_session.get_bind(), class_=_CommitExplodes, expire_on_commit=False
            ),
        )
        record = SqlTradeRecord(
            session.id,
            owner_epoch=session.owner_epoch,
            session_factory=session_sync.get_sync_session,
        )

        with pytest.raises(RuntimeError, match="commit exploded"):
            record.persist(_recorded_trade())

        assert seen == {"commit_attempted": True, "rolled_back": True}
        repository = SyncTradingSessionRepository(sync_db_session)
        counts = repository.trade_counts_by_session([session.id])
        assert counts[session.id] == (0, 0)
