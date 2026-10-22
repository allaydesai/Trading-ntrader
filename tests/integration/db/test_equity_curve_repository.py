"""A run's equity curve has a table of its own (research MCP, phase 2 pre-work)."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, select

from src.db.models.backtest import BacktestRun, RunEquityCurve
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.repositories.equity_curve_repository import EquityCurveRepository
from src.services.backtest_persistence import store_equity_curve

POINTS = [{"time": 1704067200, "value": 100000.0}, {"time": 1704153600, "value": 100450.5}]


async def _run(session) -> BacktestRun:
    return await BacktestRepository(session).create_backtest_run(
        run_id=uuid4(),
        strategy_name="Sma Crossover",
        strategy_type="sma_crossover",
        instrument_symbol="AAPL",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000"),
        data_source="mock",
        execution_status="success",
        execution_duration_seconds=Decimal("1"),
        config_snapshot={"strategy_path": "a.b:C", "config_path": "a.b:D"},
    )


async def test_a_saved_curve_reads_back_by_run_id(db_session):
    run = await _run(db_session)
    repository = EquityCurveRepository(db_session)

    await repository.save(run.id, POINTS)

    assert await repository.find_points(run.run_id) == POINTS


async def test_a_run_without_a_curve_reads_as_none(db_session):
    run = await _run(db_session)

    assert await EquityCurveRepository(db_session).find_points(run.run_id) is None
    assert await EquityCurveRepository(db_session).find_points(uuid4()) is None


async def test_deleting_the_run_deletes_its_curve(db_session):
    run = await _run(db_session)
    await EquityCurveRepository(db_session).save(run.id, POINTS)

    await db_session.execute(delete(BacktestRun).where(BacktestRun.id == run.id))

    remaining = await db_session.scalar(select(func.count()).select_from(RunEquityCurve))
    assert remaining == 0


async def test_an_empty_curve_is_not_stored(db_session):
    run = await _run(db_session)

    await store_equity_curve(db_session, run.id, [])

    assert await EquityCurveRepository(db_session).find_points(run.run_id) is None


async def test_a_failed_curve_write_leaves_the_run_usable(db_session):
    """Storing the curve is best-effort: a run that does not exist fails the FK."""
    run = await _run(db_session)

    await store_equity_curve(db_session, run.id + 10_000, POINTS)

    # The session survived the failed write: the run is still there to commit.
    found = await BacktestRepository(db_session).find_by_run_id(run.run_id)
    assert found is not None
