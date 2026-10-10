"""Story 5.3 against the test database — the seal is one transaction.

Every test runs in its own schema (``db_session``, dropped afterwards), so the
seeds are **committed**: that is what lets a refusal be rolled back exactly as
the CLI rolls it back and still leave the seeded session and trades behind to
count. Read-backs are raw SQL after ``expire_all()``, so an assertion is about
what Postgres holds, never about this session's identity map.

Trade rows are written through ``insert_trade_if_absent`` — the recorder's own
insert path (Story 3.6) — with the column mapping ``SqlTradeRecord.persist``
uses: session-owned, ``backtest_run_id`` NULL until the seal links it.

The open-position reader is a double (an empty view unless a test says
otherwise): the real one reads Redis and is proven against a real Redis by
Story 4.6's ``tests/integration/core/test_live_session_view_redis.py``.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from click.testing import CliRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.cli.commands import live_seal
from src.cli.commands.live import live
from src.config import get_settings
from src.core.live_session_view import SessionViewFailure, SessionViewUnavailableError
from src.db.exceptions import (
    DuplicateRecordError,
    InvalidSessionTransition,
    SealRefusedError,
    ValidationError,
)
from src.db.models.trade import Trade
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.repositories.trading_session_repository import TradingSessionRepository
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.backtest_result import BacktestResult
from src.models.reconciliation import SessionView, ViewPosition
from src.models.session import SessionSpec, SessionStatus, StrategySpec
from src.services import session_seal_service as seal_module
from src.services.backtest_persistence import BacktestPersistenceService
from src.services.session_seal_service import PAPER_DATA_SOURCE, seal_json_payload, seal_session
from src.services.session_service import SessionService

pytestmark = pytest.mark.integration

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
CAPITAL = Decimal("100000.00")
BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
SPEC = SessionSpec(
    strategies=(StrategySpec(strategy_id="sma_crossover", bar_types=(BAR_TYPE,)),)
).to_stored()
#: (side, entry, exit, P&L) — two winners, two losers, a day apart.
LEGS = [
    ("BUY", "100", "110", "1000"),
    ("SELL", "100", "104", "-400"),
    ("BUY", "100", "97", "-300"),
    ("SELL", "100", "95", "500"),
]


def _flat(session_id: UUID) -> SessionView:
    return SessionView("PAPER-flat", (), ())


#: Every non-schema status path a test seeds a session through, as transitions.
PATHS = {
    SessionStatus.CREATED: (),
    SessionStatus.RUNNING: (SessionStatus.RUNNING,),
    SessionStatus.STOPPED: (SessionStatus.RUNNING, SessionStatus.STOPPED),
    SessionStatus.SEALED: (SessionStatus.RUNNING, SessionStatus.STOPPED, SessionStatus.SEALED),
}


async def _seed(
    db: AsyncSession,
    *,
    status: SessionStatus = SessionStatus.STOPPED,
    legs: list = LEGS,
    linked: UUID | None = None,
    name: str = "seal-alpha",
):
    """A session moved through the real state machine, plus its trade rows."""
    row = await TradingSessionRepository(db).create(
        name=name, spec=SPEC, linked_backtest_run_id=linked
    )

    def move(sync_session):
        service = SessionService(SyncTradingSessionRepository(sync_session))
        for target in PATHS[status]:
            service.transition(row.session_id, to=target)

    await db.run_sync(move)
    repository = TradingSessionRepository(db)
    for index, (side, entry, exit_, pnl) in enumerate(legs):
        closed_at = row.created_at + timedelta(days=index + 1)
        await repository.insert_trade_if_absent(
            Trade(
                backtest_run_id=None,
                session_id=row.id,
                instrument_id="AAPL.NASDAQ",
                trade_id=f"AAPL.NASDAQ-SMACrossover-000-{index}",
                venue_order_id=f"V-{index}",
                client_order_id=f"O-{index}",
                order_side=side,
                # 100 shares: each leg's P&L is (exit − entry) × 100, side-adjusted.
                quantity=Decimal("100"),
                entry_price=Decimal(entry),
                exit_price=Decimal(exit_),
                commission_amount=Decimal("1.00"),
                commission_currency="USD",
                fees_amount=Decimal("0"),
                entry_timestamp=closed_at - timedelta(hours=2),
                exit_timestamp=closed_at,
                profit_loss=Decimal(pnl),
                profit_pct=None,
                holding_period_seconds=7200,
            )
        )
    await db.commit()
    # Plain values: the tests `expire_all()` before every read-back, and an
    # expired ORM attribute would lazy-load outside a greenlet.
    return SimpleNamespace(
        id=row.id, session_id=row.session_id, name=row.name, created_at=row.created_at
    )


async def _failed_backtest(db: AsyncSession, run_id: UUID) -> int:
    """A committed failed backtest run (no metrics), returning its internal id."""
    run = await BacktestPersistenceService(BacktestRepository(db)).save_failed_backtest(
        run_id=run_id,
        strategy_name="Sma Crossover",
        strategy_type="sma_crossover",
        instrument_symbol="AAPL",
        start_date=datetime(2016, 1, 1, tzinfo=UTC),
        end_date=datetime(2024, 1, 1, tzinfo=UTC),
        initial_capital=CAPITAL,
        data_source="catalog",
        execution_duration_seconds=Decimal("1"),
        config_snapshot={"strategy_path": "m:S", "config_path": "m:C", "config": {}},
        error_message="seeded failure",
    )
    run_pk = run.id
    await db.commit()
    return run_pk


def _clock(row):
    sealed_at = row.created_at + timedelta(days=30)
    return lambda: sealed_at


async def _seal(db: AsyncSession, row, **overrides):
    kwargs = dict(
        starting_capital=CAPITAL, read_open_positions=_flat, clock=_clock(row), new_run_id=uuid4
    )
    kwargs.update(overrides)
    return await seal_session(db, row.name, **kwargs)


async def _scalar(db: AsyncSession, sql: str, **params):
    return (await db.execute(text(sql), params)).scalar_one()


async def _counts(db: AsyncSession, session_pk: int) -> tuple[int, int, str]:
    """(run rows, linked trade rows, the session's status) — straight from Postgres."""
    db.expire_all()
    runs = await _scalar(db, "SELECT count(*) FROM backtest_runs")
    linked = await _scalar(
        db,
        "SELECT count(*) FROM trades WHERE session_id = :pk AND backtest_run_id IS NOT NULL",
        pk=session_pk,
    )
    status = await _scalar(
        db, "SELECT status::text FROM trading_sessions WHERE id = :pk", pk=session_pk
    )
    return runs, linked, status


async def _refused(db: AsyncSession, row, error: type[Exception], **overrides) -> None:
    """Seal, expect ``error``, roll back as the CLI does, prove nothing was written."""
    before = await _counts(db, row.id)
    with pytest.raises(error):
        await _seal(db, row, **overrides)
    await db.rollback()
    assert await _counts(db, row.id) == before


class TestASealWritesOneComparableRecord:
    @pytest.mark.acceptance_criterion(
        "5.3a",
        "seal on a stopped session writes the run + metrics, backfills trades, sets "
        "sealed_run_id/sealed_at and transitions to sealed",
        SOURCE,
    )
    async def test_a_stopped_session_becomes_one_run_with_its_trades_linked(self, db_session):
        row = await _seed(db_session)

        report = await _seal(db_session, row)
        await db_session.commit()

        db_session.expire_all()
        run = (
            await db_session.execute(
                text(
                    "SELECT id, run_id, execution_status, run_type, initial_capital, start_date, "
                    "end_date, data_source, strategy_type, strategy_name, instrument_symbol, "
                    "config_snapshot FROM backtest_runs"
                )
            )
        ).one()
        metrics = (
            await db_session.execute(
                text(
                    "SELECT total_trades, winning_trades, losing_trades, final_balance "
                    "FROM performance_metrics WHERE backtest_run_id = :id"
                ),
                {"id": run.id},
            )
        ).one()
        session = (
            await db_session.execute(
                text(
                    "SELECT status::text, sealed_run_id, sealed_at FROM trading_sessions "
                    "WHERE id = :pk"
                ),
                {"pk": row.id},
            )
        ).one()
        linked = await _scalar(
            db_session,
            "SELECT count(*) FROM trades WHERE session_id = :pk AND backtest_run_id = :run",
            pk=row.id,
            run=run.id,
        )

        assert run.run_id == report.run_id
        assert run.execution_status == "success"
        assert (metrics.total_trades, metrics.winning_trades, metrics.losing_trades) == (4, 2, 2)
        assert metrics.final_balance == CAPITAL + Decimal("800")
        assert linked == len(LEGS) == report.trades_backfilled
        assert session.status == "sealed"
        assert session.sealed_run_id == run.run_id
        assert session.sealed_at == run.end_date == report.sealed_at
        assert (run.strategy_type, run.strategy_name, run.instrument_symbol) == (
            "sma_crossover",
            "Sma Crossover",
            "AAPL",
        )
        series = run.config_snapshot["session_conditions"]["equity_series"]
        assert series["closed_trades"] == 4 == report.closed_trade_count

    @pytest.mark.acceptance_criterion(
        "5.3d", "the created run record's run_type is 'paper'", SOURCE
    )
    async def test_the_sealed_run_reads_paper_beside_a_backtest_that_reads_backtest(
        self, db_session
    ):
        row = await _seed(db_session)
        backtest_id = uuid4()
        await BacktestPersistenceService(BacktestRepository(db_session)).save_backtest_results(
            run_id=backtest_id,
            strategy_name="Sma Crossover",
            strategy_type="sma_crossover",
            instrument_symbol="AAPL",
            start_date=datetime(2016, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 1, tzinfo=UTC),
            initial_capital=CAPITAL,
            data_source="catalog",
            execution_duration_seconds=Decimal("1"),
            config_snapshot={"strategy_path": "m:S", "config_path": "m:C", "config": {}},
            backtest_result=BacktestResult(total_trades=0, final_balance=100000.0),
        )

        report = await _seal(db_session, row)
        await db_session.commit()

        db_session.expire_all()
        types = dict(
            (await db_session.execute(text("SELECT run_id, run_type FROM backtest_runs"))).all()
        )
        assert types == {report.run_id: "paper", backtest_id: "backtest"}

    async def test_the_run_columns_carry_the_rulings(self, db_session):
        """R1a: capital as given. R3a: created_at. The seal instant ends it."""
        row = await _seed(db_session)

        report = await _seal(db_session, row, starting_capital=Decimal("123456.78"))
        await db_session.commit()

        db_session.expire_all()
        run = (
            await db_session.execute(
                text("SELECT initial_capital, start_date, end_date, data_source FROM backtest_runs")
            )
        ).one()
        assert run.initial_capital == Decimal("123456.78")
        assert run.start_date == row.created_at
        assert run.end_date == report.sealed_at == row.created_at + timedelta(days=30)
        assert run.data_source == PAPER_DATA_SOURCE

    async def test_a_session_with_no_closed_trades_seals_explicitly(self, db_session):
        """Story 5.2's R3a, through the whole seal."""
        row = await _seed(db_session, legs=[])

        report = await _seal(db_session, row)
        await db_session.commit()

        db_session.expire_all()
        metrics = (
            await db_session.execute(
                text("SELECT total_trades, win_rate, max_drawdown FROM performance_metrics")
            )
        ).one()
        assert tuple(metrics) == (0, None, None)
        assert report.closed_trade_count == 0
        assert report.trades_backfilled == 0


class TestRefusalsWriteNothing:
    @pytest.mark.acceptance_criterion("5.3b", "seal of a running session is refused", SOURCE)
    async def test_a_running_session_is_refused(self, db_session):
        row = await _seed(db_session, status=SessionStatus.RUNNING)

        await _refused(db_session, row, InvalidSessionTransition)

    @pytest.mark.acceptance_criterion(
        "5.3c", "seal of an already-sealed session is refused (sealed is terminal)", SOURCE
    )
    async def test_a_sealed_session_cannot_be_sealed_again(self, db_session):
        row = await _seed(db_session)
        first = await _seal(db_session, row)
        await db_session.commit()

        await _refused(db_session, row, InvalidSessionTransition)

        db_session.expire_all()
        sealed_run_id = await _scalar(
            db_session, "SELECT sealed_run_id FROM trading_sessions WHERE id = :pk", pk=row.id
        )
        assert sealed_run_id == first.run_id
        assert await _scalar(db_session, "SELECT count(*) FROM backtest_runs") == 1

    async def test_a_created_session_is_refused(self, db_session):
        row = await _seed(db_session, status=SessionStatus.CREATED, legs=[])

        await _refused(db_session, row, InvalidSessionTransition)

    async def test_an_open_position_refuses_and_writes_nothing(self, db_session):
        row = await _seed(db_session)
        held = SessionView("PAPER-held", (ViewPosition("AAPL.NASDAQ", Decimal("10")),), ())

        await _refused(db_session, row, SealRefusedError, read_open_positions=lambda _: held)

    async def test_positions_on_another_account_refuse(self, db_session):
        row = await _seed(db_session)
        skipped = SessionView("PAPER-other", (), (), positions_skipped=1)

        await _refused(db_session, row, SealRefusedError, read_open_positions=lambda _: skipped)

    async def test_an_unreadable_view_refuses_and_writes_nothing(self, db_session):
        """R2a: an engine cache that cannot be read is never read as flat (DN2a:
        it stays refused; 5.5's ``--force`` is the way out)."""
        row = await _seed(db_session)

        def unreadable(_):
            raise SessionViewUnavailableError(
                SessionViewFailure.NO_ENGINE_STATE, "no keys under the namespace"
            )

        await _refused(db_session, row, SessionViewUnavailableError, read_open_positions=unreadable)

    @pytest.mark.parametrize("capital", ["-1", "0", "100000.001", "1E+15"])
    async def test_a_capital_the_record_cannot_carry_writes_nothing(self, db_session, capital):
        row = await _seed(db_session)

        await _refused(db_session, row, SealRefusedError, starting_capital=Decimal(capital))

    async def test_a_metric_beyond_its_column_refuses_by_name_and_writes_nothing(self, db_session):
        """Code review (Edge Case Hunter's probe): one day at +6% compounds to a
        CAGR of ~1.75e9 — past ``Numeric(15,6)``. Refused naming the field,
        never an unexplained ``DataError`` from Postgres."""
        row = await _seed(db_session, legs=[("BUY", "100", "106", "600")])
        one_day = row.created_at + timedelta(days=1)

        before = await _counts(db_session, row.id)
        with pytest.raises(SealRefusedError, match=r"cagr .*Numeric\(15,6\)"):
            await _seal(
                db_session, row, starting_capital=Decimal("10000.00"), clock=lambda: one_day
            )
        await db_session.rollback()
        assert await _counts(db_session, row.id) == before


class TestTheBacktestPathsOwnFailuresRollEverythingBack:
    @pytest.mark.acceptance_criterion(
        "5.3e",
        "the seal reuses BacktestPersistenceService NaN/Infinity validation and "
        "DuplicateRecordError handling verbatim",
        SOURCE,
    )
    async def test_a_nan_past_the_guard_is_the_persistence_services_own_refusal(
        self, db_session, monkeypatch
    ):
        """Story 5.2 refuses a non-finite metric first; this stubs past that
        guard to prove the second line — the backtest path's own validation —
        still stands, and that its refusal unwinds the whole seal."""
        real = seal_module.reconstruct_session_results

        def with_a_nan(*args, **kwargs):
            results = real(*args, **kwargs)
            results.result.sharpe_ratio = float("nan")
            return results

        monkeypatch.setattr(seal_module, "reconstruct_session_results", with_a_nan)
        row = await _seed(db_session)

        await _refused(db_session, row, ValidationError)

    async def test_a_colliding_run_id_is_the_repositorys_own_duplicate_error(self, db_session):
        row = await _seed(db_session)
        taken = uuid4()
        await _failed_backtest(db_session, taken)

        await _refused(db_session, row, DuplicateRecordError, new_run_id=lambda: taken)

    async def test_a_trade_already_linked_elsewhere_refuses_rather_than_being_re_pointed(
        self, db_session
    ):
        """The backfill links only rows not yet linked, and the seal checks the
        count against the rows its metrics came from — so a row it read but
        cannot link refuses the seal instead of being moved or missed."""
        row = await _seed(db_session)
        elsewhere = await _failed_backtest(db_session, uuid4())
        await db_session.execute(
            text(
                "UPDATE trades SET backtest_run_id = :run "
                "WHERE id = (SELECT min(id) FROM trades WHERE session_id = :pk)"
            ),
            {"run": elsewhere, "pk": row.id},
        )
        await db_session.commit()

        await _refused(db_session, row, SealRefusedError)

        db_session.expire_all()
        assert (
            await _scalar(
                db_session,
                "SELECT count(*) FROM trades WHERE session_id = :pk AND backtest_run_id = :run",
                pk=row.id,
                run=elsewhere,
            )
            == 1
        )


class TestARunRowExistsOnlyFromSealOnward:
    @pytest.mark.acceptance_criterion(
        "5.3f", "no path creates a backtest_runs row at session creation or start", SOURCE
    )
    async def test_create_start_and_stop_write_no_run_and_seal_writes_one(self, db_session):
        row = await _seed(db_session, status=SessionStatus.CREATED)
        assert await _scalar(db_session, "SELECT count(*) FROM backtest_runs") == 0

        def start_then_stop(sync_session):
            service = SessionService(SyncTradingSessionRepository(sync_session))
            service.transition(row.session_id, to=SessionStatus.RUNNING)
            assert sync_session.execute(text("SELECT count(*) FROM backtest_runs")).scalar() == 0
            service.transition(row.session_id, to=SessionStatus.STOPPED)

        await db_session.run_sync(start_then_stop)
        await db_session.commit()
        assert await _scalar(db_session, "SELECT count(*) FROM backtest_runs") == 0

        await _seal(db_session, row)
        await db_session.commit()

        assert await _scalar(db_session, "SELECT count(*) FROM backtest_runs") == 1


class TestTheLinkedBacktestIsReported:
    async def test_a_failed_linked_backtest_warns_and_still_seals(self, db_session):
        """PO ruling R4a, refined: a warning in the report, never a refusal."""
        linked = uuid4()
        await _failed_backtest(db_session, linked)
        row = await _seed(db_session, linked=linked)

        report = await _seal(db_session, row)
        await db_session.commit()

        assert report.linked_backtest_run_id == linked
        assert "failed" in report.linked_backtest_warning
        _, linked_trades, status = await _counts(db_session, row.id)
        assert (linked_trades, status) == (len(LEGS), "sealed")

    async def test_a_healthy_linked_backtest_warns_nothing(self, db_session):
        """A real ``success`` run with its metrics row, read through the real
        repository — ``metrics`` is eager-loaded, so no lazy load in async."""
        linked = uuid4()
        await BacktestPersistenceService(BacktestRepository(db_session)).save_backtest_results(
            run_id=linked,
            strategy_name="Sma Crossover",
            strategy_type="sma_crossover",
            instrument_symbol="AAPL",
            start_date=datetime(2016, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 1, 1, tzinfo=UTC),
            initial_capital=CAPITAL,
            data_source="catalog",
            execution_duration_seconds=Decimal("1"),
            config_snapshot={"strategy_path": "m:S", "config_path": "m:C", "config": {}},
            backtest_result=BacktestResult(total_trades=0, final_balance=100000.0),
        )
        await db_session.commit()
        row = await _seed(db_session, linked=linked)

        report = await _seal(db_session, row)

        assert report.linked_backtest_run_id == linked
        assert report.linked_backtest_warning is None


class TestTheCapitalIsStoredAsGivenAndReportedAsStored:
    async def test_a_capital_typed_in_exponent_form_is_stored_and_reported_in_cents(
        self, db_session
    ):
        """PO constraint: no normalisation in the seal — the column stores what
        it stores, and the report reads it back rather than echoing the input."""
        row = await _seed(db_session)

        report = await _seal(db_session, row, starting_capital=Decimal("1E+5"))
        await db_session.commit()

        db_session.expire_all()
        stored = await _scalar(db_session, "SELECT initial_capital FROM backtest_runs")
        assert stored == Decimal("100000.00")
        assert report.initial_capital == stored
        assert seal_json_payload(report)["execution_conditions"]["initial_capital"] == "100000.00"


class TestTheAsyncTwinsNewMethods:
    """AR9 keeps both twins; the seal drives the sync one through ``run_sync``,
    so the async one is exercised here, against the same Postgres."""

    async def test_find_and_backfill_on_the_async_twin(self, db_session):
        row = await _seed(db_session)
        run_pk = await _failed_backtest(db_session, uuid4())
        repository = TradingSessionRepository(db_session)

        trades = await repository.find_session_trades(row.id)
        linked = await repository.backfill_trades_run_id(row.id, run_pk)
        again = await repository.backfill_trades_run_id(row.id, run_pk)

        assert [trade.id for trade in trades] == sorted(trade.id for trade in trades)
        assert len(trades) == len(LEGS)
        assert (linked, again) == (len(LEGS), 0)
        assert await repository.find_session_trades(row.id + 999) == []


def _database_url() -> str:
    return get_settings().database_url.replace("postgresql://", "postgresql+asyncpg://")


@asynccontextmanager
async def _schema_session(schema: str):
    """A session on ``schema`` whose engine lives and dies in the current loop —
    asyncpg connections cannot cross event loops."""
    engine = create_async_engine(_database_url())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(text(f"SET search_path TO {schema}"))
            yield session
    finally:
        await engine.dispose()


def _in_fresh_loop(schema: str, work):
    async def run():
        async with _schema_session(schema) as session:
            return await work(session)

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(run())
    finally:
        loop.close()


class TestTheCommandEndToEnd:
    """``ntrader live seal`` against the test schema: a sync test, because the
    command runs ``asyncio.run`` itself and cannot be called from a running loop."""

    @pytest.mark.acceptance_criterion(
        "5.3g",
        "the seal report names run id, closed-trade count, open positions and disposition, "
        "and the execution-conditions summary, human and --json with identical fields",
        SOURCE,
    )
    def test_live_seal_json_reports_the_row_it_committed(self, test_db_schema, monkeypatch):
        import json

        schema, _ = test_db_schema

        async def seed(session):
            row = await _seed(session, name="seal-cli")
            return row.id

        session_pk = _in_fresh_loop(schema, seed)
        monkeypatch.setattr(live_seal, "get_session", lambda: _schema_session(schema))
        monkeypatch.setattr(live_seal, "read_open_positions", _flat)

        result = CliRunner().invoke(
            live, ["seal", "seal-cli", "--starting-capital", "100000.00", "--json"]
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)

        async def stored(session):
            run_id = await _scalar(session, "SELECT run_id FROM backtest_runs")
            sealed = await _scalar(
                session, "SELECT sealed_run_id FROM trading_sessions WHERE id = :pk", pk=session_pk
            )
            return run_id, sealed

        run_id, sealed_run_id = _in_fresh_loop(schema, stored)
        assert payload["run_id"] == str(run_id) == str(sealed_run_id)
        assert payload["closed_trade_count"] == 4
        assert payload["open_positions"] == []
        assert payload["execution_conditions"]["initial_capital"] == "100000.00"
