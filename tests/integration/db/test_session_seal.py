"""Story 5.3 against the test database — the seal is one transaction.

Every test runs in its own schema (``db_session``, dropped afterwards), so the
seeds are **committed**: that is what lets a refusal be rolled back exactly as
the CLI rolls it back and still leave the seeded session and trades behind to
count. Read-backs are raw SQL after ``expire_all()``, so an assertion is about
what Postgres holds, never about this session's identity map.

Trade rows are written through ``insert_trade_if_absent`` — the recorder's own
insert path (Story 3.6) — with the column mapping ``SqlTradeRecord.persist``
uses: session-owned, ``backtest_run_id`` NULL until the seal links it.

The holdings reader is a double (flat unless a test says otherwise): the real
one reads Redis and is proven against a real Redis by
``tests/integration/core/test_live_session_view_redis.py`` (Stories 4.6, 5.5).
Story 5.5's open trades are proven here, against Postgres: a forced seal's rows,
their link to the run, that they change no metric, and that a seal which fails
after writing them leaves none behind.
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
from src.models.reconciliation import OpenLeg, SessionHoldings, SessionView, ViewPosition
from src.models.session import SessionSpec, SessionStatus, StrategySpec
from src.services import session_seal_service as seal_module
from src.services.backtest_persistence import BacktestPersistenceService
from src.services.seal_open_positions import (
    RECORDED_OPEN_DISPOSITION,
    UNKNOWN_HOLDINGS_DISPOSITION,
)
from src.services.session_conditions import AR7_KEYS, LAST_RUN_FLAGS_KEY, START_DATE_BASIS_KEY
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


def _holdings(*legs: OpenLeg, skipped: int = 0) -> SessionHoldings:
    """Holdings whose view is the legs' own net — the reader's invariant."""
    net: dict[str, Decimal] = {}
    for leg in legs:
        net[leg.instrument_id] = net.get(leg.instrument_id, Decimal(0)) + leg.quantity
    positions = tuple(ViewPosition(i, q) for i, q in sorted(net.items()) if q != 0)
    view = SessionView("PAPER-held", positions, (), positions_skipped=skipped)
    return SessionHoldings(view=view, legs=legs)


def _leg(quantity: str, owner: str = "SMACrossover-000", instrument: str = "AAPL.NASDAQ"):
    signed = Decimal(quantity)
    return OpenLeg(
        position_id=f"{instrument}-{owner}",
        instrument_id=instrument,
        strategy_id=owner,
        side="BUY" if signed > 0 else "SELL",
        quantity=signed,
        avg_px_open=Decimal("187.25000000"),
        opened_at=datetime(2026, 9, 2, 14, 30, tzinfo=UTC),
        opening_order_id=f"O-open-{owner}",
        realized_pnl=Decimal("-1.00"),
        commission_amount=Decimal("1.00"),
        commission_currency="USD",
    )


#: Story 5.5: the strategy's own long and a reconciliation-owned one beside it (R1a).
HELD = _holdings(_leg("10"), _leg("5", owner="EXTERNAL"))


def _flat(session_id: UUID) -> SessionHoldings:
    return _holdings()


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


async def _sealed_row(db: AsyncSession):
    """The one run row's columns and conditions, straight from Postgres."""
    db.expire_all()
    return (
        await db.execute(
            text(
                "SELECT r.initial_capital, r.start_date, r.end_date, r.data_source, "
                "r.config_snapshot, r.config_snapshot -> 'session_conditions' AS conditions, "
                "m.total_trades FROM backtest_runs r "
                "JOIN performance_metrics m ON m.backtest_run_id = r.id "
                "WHERE r.run_type = 'paper'"
            )
        )
    ).one()


class TestTheRecordCarriesItsConditions:
    """Story 5.4 (FR43, FR45, AR7) against Postgres: what the record says it
    ran under, read back through fresh SQL — never the identity map."""

    @pytest.mark.acceptance_criterion(
        "5.4a",
        "a sealed session's config_snapshot holds session_conditions with starting_capital, "
        "rth_only, price_basis, market_data_tier, closed_trade_count, open_positions_at_seal "
        "and schema_version: 1",
        SOURCE,
    )
    async def test_every_condition_is_stored_beside_the_series(self, db_session):
        row = await _seed(db_session)

        await _seal(db_session, row)
        await db_session.commit()

        conditions = (await _sealed_row(db_session)).conditions
        assert set(conditions) == set(AR7_KEYS) | {
            START_DATE_BASIS_KEY,
            LAST_RUN_FLAGS_KEY,
            "equity_series",
        }
        assert conditions["schema_version"] == 1
        assert conditions["starting_capital"] == "100000.00"
        assert conditions["rth_only"] is True
        assert conditions["market_data_tier"] == "REALTIME"
        assert conditions["open_positions_at_seal"] == []
        assert conditions["last_run_flags"] is None

    @pytest.mark.acceptance_criterion(
        "5.4b",
        "price_basis records ibkr_unadjusted_smart for a paper session, distinct from a "
        "backtest's firstrate_adjusted",
        SOURCE,
    )
    async def test_the_paper_basis_is_stored_and_a_backtest_carries_no_conditions(self, db_session):
        row = await _seed(db_session)
        await BacktestPersistenceService(BacktestRepository(db_session)).save_backtest_results(
            run_id=uuid4(),
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

        await _seal(db_session, row)
        await db_session.commit()

        db_session.expire_all()
        snapshots = dict(
            (
                await db_session.execute(
                    text("SELECT run_type, config_snapshot FROM backtest_runs")
                )
            ).all()
        )
        assert snapshots["paper"]["session_conditions"]["price_basis"] == "ibkr_unadjusted_smart"
        assert "session_conditions" not in snapshots["backtest"]

    @pytest.mark.acceptance_criterion(
        "5.4c",
        "the closed-trade count travels with the record so a difference can be read against "
        "sampling noise",
        SOURCE,
    )
    @pytest.mark.parametrize("legs", [LEGS, []], ids=["four_closed", "none_closed"])
    async def test_the_closed_trade_count_is_one_number_in_three_places(self, db_session, legs):
        row = await _seed(db_session, legs=legs)

        report = await _seal(db_session, row)
        await db_session.commit()

        stored = await _sealed_row(db_session)
        conditions = stored.conditions
        assert (
            conditions["closed_trade_count"]
            == conditions["equity_series"]["closed_trades"]
            == stored.total_trades
            == report.closed_trade_count
            == len(legs)
        )

    @pytest.mark.acceptance_criterion(
        "5.4d",
        "initial_capital matches the starting capital, start_date is the session's creation "
        "(R1a, recorded as start_date_basis), end_date is the seal time, data_source "
        "identifies the live IBKR source",
        SOURCE,
    )
    async def test_the_run_columns_agree_with_the_conditions(self, db_session):
        row = await _seed(db_session)

        report = await _seal(db_session, row, starting_capital=Decimal("123456.78"))
        await db_session.commit()

        stored = await _sealed_row(db_session)
        assert stored.initial_capital == Decimal(stored.conditions["starting_capital"])
        assert stored.start_date == row.created_at
        assert stored.conditions["start_date_basis"] == "session_created_at"
        assert stored.end_date == report.sealed_at
        assert stored.data_source == "ibkr_live"

    @pytest.mark.acceptance_criterion(
        "5.4e",
        "the paper session's own starting balance is stored as-is, with no normalization",
        SOURCE,
    )
    async def test_a_capital_typed_as_an_exponent_is_stored_as_the_column_stores_it(
        self, db_session
    ):
        row = await _seed(db_session)

        await _seal(db_session, row, starting_capital=Decimal("1E+5"))
        await db_session.commit()

        stored = await _sealed_row(db_session)
        assert stored.conditions["starting_capital"] == "100000.00" == str(stored.initial_capital)

    @pytest.mark.acceptance_criterion(
        "5.4i",
        "last_run_flags is the sealed row's runtime_flags as stored, or null",
        # Story 5.4's own AC #9 (from the routed debt), not the PRD's.
        "_bmad-output/implementation-artifacts/"
        "5-4-record-the-conditions-the-session-actually-ran-under.md",
    )
    async def test_the_last_runs_flags_are_copied_as_stored(self, db_session):
        """PO ruling R2a, written through the real ``SessionService`` while the
        session ran, then stopped and sealed."""
        row = await _seed(db_session, status=SessionStatus.RUNNING)

        def fail_then_stop(sync_session):
            service = SessionService(SyncTradingSessionRepository(sync_session))
            service.record_strategy_failure(
                row.session_id,
                owner_epoch=1,
                strategy_id="SMACrossover-000",
                spec_strategy_id="sma_crossover",
                error_type="ValueError",
                handler="on_bar",
                at=row.created_at + timedelta(days=2),
                detail="indicator not ready",
                all_failed=True,
            )
            service.transition(row.session_id, to=SessionStatus.STOPPED)

        await db_session.run_sync(fail_then_stop)
        await db_session.commit()
        db_session.expire_all()
        flags = await _scalar(
            db_session, "SELECT runtime_flags FROM trading_sessions WHERE id = :pk", pk=row.id
        )

        await _seal(db_session, row)
        await db_session.commit()

        recorded = (await _sealed_row(db_session)).conditions["last_run_flags"]
        assert flags["all_failed"] is True
        assert recorded == flags


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

    @pytest.mark.acceptance_criterion(
        "5.5a",
        "seal without --force refuses a stopped session with open positions, listing each "
        "open position with its instrument and quantity",
        SOURCE,
    )
    async def test_an_open_position_refuses_and_writes_nothing(self, db_session):
        row = await _seed(db_session)
        trades_before = await _scalar(db_session, "SELECT count(*) FROM trades")

        with pytest.raises(SealRefusedError) as raised:
            await _seal(db_session, row, read_open_positions=lambda _: HELD)
        await db_session.rollback()

        message = str(raised.value)
        assert "AAPL.NASDAQ 10 (SMACrossover-000)" in message
        assert "AAPL.NASDAQ 5 (EXTERNAL)" in message
        assert "--force" in message
        assert await _counts(db_session, row.id) == (0, 0, "stopped")
        assert await _scalar(db_session, "SELECT count(*) FROM trades") == trades_before

    async def test_positions_on_another_account_refuse(self, db_session):
        row = await _seed(db_session)
        skipped = _holdings(skipped=1)

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


async def _open_rows(db: AsyncSession, session_pk: int) -> list:
    db.expire_all()
    return (
        await db.execute(
            text(
                "SELECT trade_id, venue_order_id, client_order_id, order_side, quantity, "
                "entry_price, exit_price, exit_timestamp, profit_loss, profit_pct, "
                "holding_period_seconds, backtest_run_id FROM trades "
                "WHERE session_id = :pk AND exit_timestamp IS NULL ORDER BY id"
            ),
            {"pk": session_pk},
        )
    ).all()


async def _metrics(db: AsyncSession, run_id: UUID) -> dict:
    """Every ``performance_metrics`` value of one run, ids and timestamps aside."""
    db.expire_all()
    row = (
        await db.execute(
            text(
                "SELECT m.* FROM performance_metrics m JOIN backtest_runs r "
                "ON r.id = m.backtest_run_id WHERE r.run_id = :run"
            ),
            {"run": run_id},
        )
    ).one()
    return {
        key: value
        for key, value in row._mapping.items()
        if key not in {"id", "backtest_run_id", "created_at", "updated_at"}
    }


class TestOpenPositionsAtSeal:
    """Story 5.5 (FR47, AR30) against Postgres: ``--force`` records, never exits."""

    @pytest.mark.acceptance_criterion(
        "5.5b",
        "seal --force seals and persists each open position as an open trade with a null exit, "
        "the shape backtests use for positions open at the end of data",
        SOURCE,
    )
    async def test_a_forced_seal_records_each_open_position_as_an_open_trade(self, db_session):
        row = await _seed(db_session)

        report = await _seal(db_session, row, read_open_positions=lambda _: HELD, force=True)
        await db_session.commit()

        rows = await _open_rows(db_session, row.id)
        run_pk = await _scalar(db_session, "SELECT id FROM backtest_runs")
        assert [tuple(r) for r in rows] == [
            (
                "AAPL.NASDAQ-SMACrossover-000",
                "O-open-SMACrossover-000",
                None,
                "BUY",
                Decimal("10"),
                Decimal("187.25"),
                None,
                None,
                Decimal("-1.00"),
                None,
                None,
                run_pk,
            ),
            (
                "AAPL.NASDAQ-EXTERNAL",
                "O-open-EXTERNAL",
                None,
                "BUY",
                Decimal("5"),
                Decimal("187.25"),
                None,
                None,
                Decimal("-1.00"),
                None,
                None,
                run_pk,
            ),
        ]
        counts = await db_session.run_sync(
            lambda sync: SyncTradingSessionRepository(sync).trade_counts_by_session([row.id])
        )
        assert counts == {row.id: (len(LEGS), 2)}
        assert report.trades_backfilled == len(LEGS) + 2
        assert report.closed_trade_count == len(LEGS)
        assert report.open_trades_recorded == 2
        assert report.open_positions_disposition == RECORDED_OPEN_DISPOSITION
        _, linked, status = await _counts(db_session, row.id)
        assert (linked, status) == (len(LEGS) + 2, "sealed")

    async def test_open_trades_change_no_metric(self, db_session):
        """F9 / D-E: the same closed trades seal to the same metrics, open rows or not."""
        flat = await _seed(db_session, name="seal-flat")
        held = await _seed(db_session, name="seal-held")

        plain = await _seal(db_session, flat)
        forced = await _seal(db_session, held, read_open_positions=lambda _: HELD, force=True)
        await db_session.commit()

        assert await _metrics(db_session, forced.run_id) == await _metrics(db_session, plain.run_id)
        assert len(await _open_rows(db_session, held.id)) == 2

    async def test_a_forced_seal_that_fails_later_leaves_no_open_trade_behind(self, db_session):
        """D-G: the rows are written in the seal's own transaction."""
        row = await _seed(db_session)
        taken = uuid4()
        await _failed_backtest(db_session, taken)

        with pytest.raises(DuplicateRecordError):
            await _seal(
                db_session,
                row,
                read_open_positions=lambda _: HELD,
                force=True,
                new_run_id=lambda: taken,
            )
        await db_session.rollback()

        assert await _open_rows(db_session, row.id) == []
        assert await _counts(db_session, row.id) == (1, 0, "stopped")

    async def test_no_engine_state_with_force_seals_with_unknown_holdings(self, db_session):
        """PO ruling R2a (DN2a's way out): sealed, nothing recorded, never "none open"."""
        row = await _seed(db_session)

        def empty(_):
            raise SessionViewUnavailableError(
                SessionViewFailure.NO_ENGINE_STATE, "no keys under the namespace"
            )

        report = await _seal(db_session, row, read_open_positions=empty, force=True)
        await db_session.commit()

        assert report.open_positions_disposition == UNKNOWN_HOLDINGS_DISPOSITION
        assert report.session_conditions["open_positions_at_seal"] is None
        assert await _open_rows(db_session, row.id) == []
        assert (await _counts(db_session, row.id))[1:] == (len(LEGS), "sealed")

    @pytest.mark.acceptance_criterion(
        "5.5e", "a stopped session with no open positions seals without requiring --force", SOURCE
    )
    async def test_a_flat_session_seals_without_force(self, db_session):
        row = await _seed(db_session)

        report = await _seal(db_session, row)
        await db_session.commit()

        assert report.forced is False
        assert await _open_rows(db_session, row.id) == []
        assert (await _counts(db_session, row.id))[2] == "sealed"


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

    @pytest.mark.acceptance_criterion(
        "5.5c",
        "a --force seal's report states the disposition explicitly: the positions remain at "
        "the broker and are no longer owned by any session",
        SOURCE,
    )
    def test_live_seal_force_json_reports_the_open_trades_it_committed(
        self, test_db_schema, monkeypatch
    ):
        import json

        schema, _ = test_db_schema

        async def seed(session):
            row = await _seed(session, name="seal-cli-force")
            return row.id

        session_pk = _in_fresh_loop(schema, seed)
        monkeypatch.setattr(live_seal, "get_session", lambda: _schema_session(schema))
        monkeypatch.setattr(live_seal, "read_open_positions", lambda _: HELD)

        result = CliRunner().invoke(
            live,
            ["seal", "seal-cli-force", "--starting-capital", "100000.00", "--force", "--json"],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)

        async def stored(session):
            return await _scalar(
                session,
                "SELECT count(*) FROM trades WHERE session_id = :pk AND exit_timestamp IS NULL "
                "AND backtest_run_id IS NOT NULL",
                pk=session_pk,
            )

        assert _in_fresh_loop(schema, stored) == 2
        assert payload["forced"] is True
        assert payload["open_trades_recorded"] == 2
        assert payload["open_positions"] == [{"instrument_id": "AAPL.NASDAQ", "quantity": "15"}]
        assert "remain at the broker" in payload["open_positions_disposition"]
        assert "no longer owned by any session" in payload["open_positions_disposition"]
