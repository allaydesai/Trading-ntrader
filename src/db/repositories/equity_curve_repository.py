"""Data access for a run's stored equity curve (async for writers, sync for the MCP)."""

from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.db.models.backtest import BacktestRun, RunEquityCurve

EquityPoints = list[dict[str, Any]]


def _points_of_run(run_id: UUID) -> Select[tuple[EquityPoints]]:
    return (
        select(RunEquityCurve.points)
        .join(BacktestRun, BacktestRun.id == RunEquityCurve.backtest_run_id)
        .where(BacktestRun.run_id == run_id)
    )


class EquityCurveRepository:
    """Stores and reads equity curves on an async session."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, backtest_run_id: int, points: EquityPoints) -> None:
        """Store the curve of the run with internal id ``backtest_run_id``."""
        self.session.add(RunEquityCurve(backtest_run_id=backtest_run_id, points=points))
        await self.session.flush()

    async def find_points(self, run_id: UUID) -> EquityPoints | None:
        """The run's curve, or None when none was stored (runs before the table existed)."""
        return (await self.session.execute(_points_of_run(run_id))).scalar_one_or_none()


class SyncEquityCurveRepository:
    """Reads equity curves on a sync session."""

    def __init__(self, session: Session):
        self.session = session

    def find_points(self, run_id: UUID) -> EquityPoints | None:
        """The run's curve, or None when none was stored (runs before the table existed)."""
        return self.session.execute(_points_of_run(run_id)).scalar_one_or_none()
