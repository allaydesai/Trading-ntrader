"""``search_runs``: find runs by strategy, symbol, study, role and date (S8.2).

Headline metrics only. A run's study and role come from the research ledger;
a run with neither is unattributed.
"""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from src.db.models.backtest import BacktestRun, PerformanceMetrics
from src.db.models.research import ResearchStudy, ResearchTrial
from src.db.session_sync import get_sync_session
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.strategies import resolve_strategy

HEADLINE = ("total_return", "cagr", "sharpe_ratio", "max_drawdown", "profit_factor", "total_trades")


def study_filter(key: str) -> Any:
    """Match a study by id when ``key`` is a UUID, else by slug."""
    try:
        return ResearchStudy.study_id == UUID(key.strip())
    except ValueError:
        return ResearchStudy.slug == key.strip()


def search_runs(
    *,
    strategy: str | None = None,
    symbol: str | None = None,
    study: str | None = None,
    role: str | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Runs matching every filter, newest first."""
    metrics = [getattr(PerformanceMetrics, name) for name in HEADLINE]
    stmt = (
        select(BacktestRun, ResearchStudy.slug, ResearchTrial.role, *metrics)
        .outerjoin(PerformanceMetrics, PerformanceMetrics.backtest_run_id == BacktestRun.id)
        .outerjoin(ResearchTrial, ResearchTrial.run_id == BacktestRun.run_id)
        .outerjoin(ResearchStudy, ResearchStudy.id == ResearchTrial.study_pk)
    )
    if strategy:
        stmt = stmt.where(BacktestRun.strategy_type == resolve_strategy(strategy).name)
    if symbol:
        stmt = stmt.where(BacktestRun.instrument_symbol == symbol.strip().upper())
    if study:
        stmt = stmt.where(
            (ResearchStudy.slug == study)
            | (func.cast(ResearchStudy.study_id, ResearchStudy.slug.type) == study)
        )
    if role:
        stmt = stmt.where(ResearchTrial.role == role)
    if created_from:
        stmt = stmt.where(func.date(BacktestRun.created_at) >= created_from)
    if created_to:
        stmt = stmt.where(func.date(BacktestRun.created_at) <= created_to)
    stmt = stmt.order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
    stmt = stmt.limit(max(1, min(limit, 200)))
    with get_sync_session() as session:
        rows = session.execute(stmt).all()
    runs = [
        {
            "run_id": run.run_id,
            "strategy": run.strategy_type,
            "symbol": run.instrument_symbol,
            "start": run.start_date,
            "end": run.end_date,
            "status": run.execution_status,
            "created_at": run.created_at,
            "study": slug,
            "role": trial_role,
            "git_dirty": run.git_dirty,
            "headline": dict(zip(HEADLINE, values)),
        }
        for run, slug, trial_role, *values in rows
    ]
    return {"runs": to_jsonable(runs), "count": len(runs)}
