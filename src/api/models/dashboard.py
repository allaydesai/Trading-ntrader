"""
Dashboard view models for web UI rendering.

Provides data structures for dashboard summary statistics and recent activity.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field

from src.db.models.backtest import BacktestRun


class RecentBacktestItem(BaseModel):
    """
    Condensed backtest info for dashboard activity feed.

    Attributes:
        run_id: Business identifier (UUID)
        run_id_short: First 8 chars of UUID for display
        strategy_name: Strategy display name
        instrument_symbol: Trading instrument
        execution_status: "success" or "failed"
        created_at: When backtest was run
        total_return: Return percentage (if success)

    Example:
        >>> item = RecentBacktestItem(
        ...     run_id=UUID("12345678-1234-5678-1234-567812345678"),
        ...     strategy_name="SMA Crossover",
        ...     instrument_symbol="AAPL",
        ...     execution_status="success",
        ...     created_at=datetime.now(),
        ...     total_return=Decimal("0.25")
        ... )
        >>> print(item.run_id_short)
        12345678
    """

    run_id: UUID = Field(..., description="Business identifier")
    strategy_name: str = Field(..., description="Strategy display name")
    instrument_symbol: str = Field(..., description="Trading instrument")
    execution_status: str = Field(..., description="success or failed")
    created_at: datetime = Field(..., description="When backtest was run")
    total_return: Optional[Decimal] = Field(
        None, description="Return percentage (if success)"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def run_id_short(self) -> str:
        """First 8 characters of UUID for display."""
        return str(self.run_id)[:8]


class DashboardSummary(BaseModel):
    """
    Aggregate statistics displayed on the home dashboard.

    Attributes:
        total_backtests: Count of all backtest runs
        best_sharpe_ratio: Highest Sharpe ratio achieved (None if no success)
        best_sharpe_strategy: Strategy name with best Sharpe
        worst_max_drawdown: Worst (most negative) drawdown (None if no success)
        worst_drawdown_strategy: Strategy with worst drawdown
        recent_backtests: Last 5 executed backtests

    Example:
        >>> summary = DashboardSummary(
        ...     total_backtests=42,
        ...     best_sharpe_ratio=Decimal("2.15"),
        ...     best_sharpe_strategy="SMA Crossover",
        ...     worst_max_drawdown=Decimal("-0.25"),
        ...     worst_drawdown_strategy="RSI Mean Reversion",
        ...     recent_backtests=[]
        ... )
    """

    total_backtests: int = Field(
        default=0, ge=0, description="Count of all backtest runs"
    )
    best_sharpe_ratio: Optional[Decimal] = Field(
        default=None, description="Highest Sharpe ratio achieved"
    )
    best_sharpe_strategy: Optional[str] = Field(
        default=None, description="Strategy name with best Sharpe"
    )
    worst_max_drawdown: Optional[Decimal] = Field(
        default=None, description="Worst (most negative) drawdown"
    )
    worst_drawdown_strategy: Optional[str] = Field(
        default=None, description="Strategy with worst drawdown"
    )
    recent_backtests: list[RecentBacktestItem] = Field(
        default_factory=list, description="Last 5 executed backtests"
    )


def to_recent_item(run: BacktestRun) -> RecentBacktestItem:
    """
    Map BacktestRun database model to RecentBacktestItem view model.

    Args:
        run: BacktestRun database instance with metrics loaded

    Returns:
        RecentBacktestItem for dashboard display

    Example:
        >>> # From database
        >>> run = await repository.find_recent(limit=1)[0]
        >>> # To view model
        >>> item = to_recent_item(run)
        >>> print(f"{item.strategy_name}: {item.run_id_short}")
    """
    total_return = None
    if run.metrics and run.execution_status == "success":
        total_return = run.metrics.total_return

    return RecentBacktestItem(
        run_id=run.run_id,
        strategy_name=run.strategy_name,
        instrument_symbol=run.instrument_symbol,
        execution_status=run.execution_status,
        created_at=run.created_at,
        total_return=total_return,
    )
