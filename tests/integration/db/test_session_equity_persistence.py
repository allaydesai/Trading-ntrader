"""Story 5.2, AC #1, #3, #4 — the reconstructed record survives a real write.

Against the test database (schema-isolated ``db_session``, dropped afterwards):
a :class:`~src.core.session_equity.SessionResults` goes through the
**unmodified** ``BacktestPersistenceService.save_backtest_results`` — its
config-snapshot validation and NaN/Infinity validation reused verbatim (AR12) —
and is read back with raw SQL, so the assertion is about what Postgres holds,
not about an ORM identity map.

``run_type`` is left at its server default: stamping ``'paper'`` is the seal
service's (Story 5.3), and nothing here is a seal.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from src.core.session_equity import EQUITY_SERIES_KEY, SessionResults, reconstruct_session_results
from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_persistence import BacktestPersistenceService

pytestmark = pytest.mark.integration

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2027, 1, 1, tzinfo=UTC)
CAPITAL = Decimal("100000")
#: What a backtest path hands the validator (``backtest_orchestrator.py:522-537``).
BACKTEST_SNAPSHOT = {
    "strategy_path": "src.core.strategies.sma_crossover.SMACrossover",
    "config_path": "config/x.yaml",
    "version": "1.0",
    "config": {"fast_period": 10},
    "bar_type": "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL",
    "equity_curve": [{"time": 1, "value": 2.0}],
}


@dataclass(frozen=True)
class _Row:
    id: int
    order_side: str
    entry_price: Decimal
    exit_price: Decimal | None
    profit_loss: Decimal | None
    exit_timestamp: datetime | None
    commission_currency: str | None = "USD"


ROWS = [
    _Row(
        1, "BUY", Decimal("100"), Decimal("110"), Decimal("1000"), datetime(2026, 1, 2, tzinfo=UTC)
    ),
    _Row(
        2, "SELL", Decimal("100"), Decimal("104"), Decimal("-400"), datetime(2026, 1, 3, tzinfo=UTC)
    ),
    _Row(
        3, "BUY", Decimal("100"), Decimal("97"), Decimal("-300"), datetime(2026, 1, 4, tzinfo=UTC)
    ),
]


async def _write(db_session, config_snapshot: dict, result) -> UUID:
    run_id = uuid4()
    await BacktestPersistenceService(BacktestRepository(db_session)).save_backtest_results(
        run_id=run_id,
        strategy_name="Sma Crossover",
        strategy_type="sma_crossover",
        instrument_symbol="AAPL",
        start_date=START,
        end_date=END,
        initial_capital=CAPITAL,
        data_source="IBKR",
        execution_duration_seconds=Decimal("0"),
        config_snapshot=config_snapshot,
        backtest_result=result,
    )
    return run_id


async def _stored(db_session, run_id: UUID):
    return (
        await db_session.execute(
            text(
                "SELECT r.config_snapshot, m.total_trades, m.win_rate, m.max_drawdown, "
                "m.calmar_ratio, m.cagr, m.final_balance "
                "FROM backtest_runs r JOIN performance_metrics m ON m.backtest_run_id = r.id "
                "WHERE r.run_id = :run_id"
            ),
            {"run_id": run_id},
        )
    ).one()


#: Stand-ins for Story 5.4's flat AR7 keys, already in the snapshot the seal
#: builds — ``merged_into`` must put the series beside them, not over them.
STORY_5_4_KEYS = {"schema_version": 1, "rth_only": True}


async def _write_sealed(db_session, sealed: SessionResults):
    snapshot = {
        "strategy_path": "src.core.strategies.sma_crossover.SMACrossover",
        "config_path": "session_spec",
        "version": "1.0",
        "config": {},
        "session_conditions": dict(STORY_5_4_KEYS),
    }
    merged = sealed.merged_into(snapshot)
    return await _stored(db_session, await _write(db_session, merged, sealed.result))


class TestTheSeriesAndItsLimitationAreStored:
    @pytest.mark.acceptance_criterion(
        "5.2a", "the equity series is starting capital plus the closed-trade sequence", SOURCE
    )
    async def test_the_series_reaches_postgres_intact(self, db_session):
        sealed = reconstruct_session_results(ROWS, starting_capital=CAPITAL, start=START, end=END)
        stored = await _write_sealed(db_session, sealed)

        assert stored.config_snapshot["session_conditions"] == {
            **STORY_5_4_KEYS,
            **sealed.session_conditions,
        }
        series = stored.config_snapshot["session_conditions"][EQUITY_SERIES_KEY]
        # 100,000 × 1.10 × 0.96 × 0.97 — three closes at three distinct times.
        assert [point["value"] for point in series["curve"]] == [110000.0, 105600.0, 102432.0]
        assert stored.total_trades == 3
        assert stored.final_balance == Decimal("100300.00")
        assert stored.max_drawdown is not None and stored.max_drawdown < 0

    @pytest.mark.acceptance_criterion(
        "5.2c", "the trade-granular limitation is recorded in session_conditions", SOURCE
    )
    async def test_the_trade_granular_limitation_reaches_postgres(self, db_session):
        sealed = reconstruct_session_results(ROWS, starting_capital=CAPITAL, start=START, end=END)
        stored = await _write_sealed(db_session, sealed)

        series = stored.config_snapshot["session_conditions"][EQUITY_SERIES_KEY]
        assert series["granularity"] == "closed_trade"
        assert series["basis"] == "per_trade_price_return"
        assert series["closed_trades"] == 3
        assert series["excludes"] == ["open_positions", "reconciliation_owned_activity"]

    async def test_a_backtest_row_is_stored_exactly_as_before(self, db_session):
        """PO ruling R2a, at the database: no ``session_conditions`` key appears
        on a backtest row, and its ``equity_curve`` is dropped as it always was.

        ``metrics_basis`` and ``fill_seed`` are the research MCP's two backtest
        fields, null here because this row names neither; they belong to every
        backtest row (PO ruling 2026-10-11, pinned in ``test_config_snapshot``)."""
        sealed = reconstruct_session_results(ROWS, starting_capital=CAPITAL, start=START, end=END)
        stored = await _stored(
            db_session, await _write(db_session, dict(BACKTEST_SNAPSHOT), sealed.result)
        )
        assert stored.config_snapshot == {
            "strategy_path": "src.core.strategies.sma_crossover.SMACrossover",
            "config_path": "config/x.yaml",
            "version": "1.0",
            "config": {"fast_period": 10},
            "bar_type": "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL",
            "metrics_basis": None,
            "fill_seed": None,
        }


class TestAZeroTradeRecord:
    @pytest.mark.acceptance_criterion(
        "5.2d", "zero closed trades: an explicit zero-trade record, no NaN", SOURCE
    )
    async def test_it_is_written_with_its_zero_trade_state_explicit(self, db_session):
        open_only = [_Row(1, "BUY", Decimal("100"), None, None, None)]
        sealed = reconstruct_session_results(
            open_only, starting_capital=CAPITAL, start=START, end=END
        )
        stored = await _write_sealed(db_session, sealed)

        assert stored.config_snapshot["session_conditions"][EQUITY_SERIES_KEY]["closed_trades"] == 0
        assert stored.config_snapshot["session_conditions"][EQUITY_SERIES_KEY]["curve"] == []
        assert stored.total_trades == 0
        assert stored.win_rate is None
        assert stored.max_drawdown is None
        assert stored.calmar_ratio is None
        assert stored.cagr == Decimal("0")
        assert stored.final_balance == Decimal("100000.00")
