"""
SQLAlchemy ORM models for backtest execution metadata and performance metrics.

This module defines the database schema for persisting backtest results,
including execution metadata, configuration snapshots, and performance metrics.
"""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from src.db.models.trade import Trade

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin


class BacktestRun(Base, TimestampMixin):
    """
    Backtest execution record with complete metadata and configuration.

    Represents a single execution of a backtesting run, storing all metadata
    needed to reproduce the test and query results later.

    Attributes:
        id: Internal database primary key
        run_id: Business identifier (UUID) for external references
        strategy_name: Human-readable strategy name
        strategy_type: Strategy category (e.g., "trend_following")
        instrument_symbol: Trading symbol (e.g., "AAPL")
        start_date: Backtest period start (inclusive)
        end_date: Backtest period end (inclusive)
        initial_capital: Starting account balance
        data_source: Data provider (e.g., "IBKR", "CSV")
        execution_status: Outcome ("success" or "failed")
        execution_duration_seconds: Time taken to run backtest
        error_message: Error details if status = "failed"
        config_snapshot: Complete strategy configuration (JSONB)
        reproduced_from_run_id: Reference to original run if reproduction
        created_at: When record was created
        metrics: Associated performance metrics (one-to-one)

    Example:
        >>> run = BacktestRun(
        ...     run_id=uuid4(),
        ...     strategy_name="SMA Crossover",
        ...     strategy_type="trend_following",
        ...     instrument_symbol="AAPL",
        ...     start_date=datetime(2023, 1, 1),
        ...     end_date=datetime(2023, 12, 31),
        ...     initial_capital=Decimal("100000.00"),
        ...     data_source="IBKR",
        ...     execution_status="success",
        ...     execution_duration_seconds=Decimal("45.5"),
        ...     config_snapshot={...}
        ... )
    """

    __tablename__ = "backtest_runs"

    # Primary key (internal)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Business identifier (external)
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        default=uuid4,
        unique=True,
        nullable=False,
        index=True,
    )

    # Metadata fields
    strategy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(100), nullable=False)
    instrument_symbol: Mapped[str] = mapped_column(String(50), nullable=False)

    # Date range
    start_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    # Execution details
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    data_source: Mapped[str] = mapped_column(String(100), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_duration_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Configuration snapshot (JSONB)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Reproduction tracking
    reproduced_from_run_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    # Relationships
    metrics: Mapped[Optional["PerformanceMetrics"]] = relationship(
        "PerformanceMetrics",
        back_populates="backtest_run",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    trades: Mapped[list["Trade"]] = relationship(
        "Trade",
        back_populates="backtest_run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Table constraints
    __table_args__ = (
        # Date range validation
        CheckConstraint("end_date > start_date", name="chk_date_range"),
        # Capital validation
        CheckConstraint("initial_capital > 0", name="chk_initial_capital"),
        # Duration validation
        CheckConstraint("execution_duration_seconds >= 0", name="chk_execution_duration"),
        # Composite index for time-based queries (Phase 1)
        Index("idx_backtest_runs_created_id", "created_at", "id"),
        # Composite index for strategy filtering (Phase 1)
        Index(
            "idx_backtest_runs_strategy_created_id",
            "strategy_name",
            "created_at",
            "id",
        ),
        # Index for instrument filtering (Phase 2)
        Index("idx_backtest_runs_instrument", "instrument_symbol"),
        # Index for status filtering (Phase 2)
        Index("idx_backtest_runs_status", "execution_status"),
    )

    def __repr__(self) -> str:
        """Return string representation of BacktestRun."""
        return (
            f"<BacktestRun(run_id={self.run_id}, "
            f"strategy={self.strategy_name}, "
            f"status={self.execution_status})>"
        )


class PerformanceMetrics(Base, TimestampMixin):
    """
    Performance metrics for successful backtest executions.

    Stores comprehensive performance analytics including returns, risk metrics,
    and trading statistics. Only created for successful backtests.

    Attributes:
        id: Internal database primary key
        backtest_run_id: Foreign key to parent backtest run
        total_return: Total return percentage
        final_balance: Final account balance
        cagr: Compound annual growth rate
        sharpe_ratio: Risk-adjusted return metric
        sortino_ratio: Downside risk-adjusted return
        max_drawdown: Maximum peak-to-trough decline
        max_drawdown_date: When max drawdown occurred
        calmar_ratio: Return / max drawdown ratio
        volatility: Returns standard deviation
        total_trades: Total number of trades executed
        winning_trades: Count of profitable trades
        losing_trades: Count of losing trades
        win_rate: Percentage of winning trades
        profit_factor: Gross profit / gross loss
        expectancy: Expected profit per trade
        avg_win: Average winning trade amount
        avg_loss: Average losing trade amount
        created_at: When record was created
        backtest_run: Associated backtest run (one-to-one)

    Example:
        >>> metrics = PerformanceMetrics(
        ...     backtest_run_id=1,
        ...     total_return=Decimal("0.25"),
        ...     final_balance=Decimal("125000.00"),
        ...     sharpe_ratio=Decimal("1.85"),
        ...     total_trades=100,
        ...     winning_trades=60,
        ...     losing_trades=40
        ... )
    """

    __tablename__ = "performance_metrics"

    # Primary key
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Foreign key to backtest_runs
    backtest_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    # Return metrics
    total_return: Mapped[Decimal] = mapped_column(Numeric(15, 6), nullable=False)
    final_balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    cagr: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)

    # Risk metrics
    sharpe_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    sortino_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    max_drawdown: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    max_drawdown_date: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    calmar_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    volatility: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)

    # Additional returns-based metrics (from get_performance_stats_returns)
    risk_return_ratio: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    avg_return: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    avg_win_return: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    avg_loss_return: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)

    # Trading metrics
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losing_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    profit_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    expectancy: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    avg_win: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    avg_loss: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)

    # Additional PnL-based metrics (from get_performance_stats_pnls)
    total_pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    total_pnl_percentage: Mapped[Optional[Decimal]] = mapped_column(Numeric(15, 6), nullable=True)
    max_winner: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    max_loser: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    min_winner: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)
    min_loser: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2), nullable=True)

    # Relationships
    backtest_run: Mapped[BacktestRun] = relationship("BacktestRun", back_populates="metrics")

    # Table constraints
    __table_args__ = (
        # Trade count consistency
        CheckConstraint(
            "total_trades = winning_trades + losing_trades",
            name="chk_trade_count_consistency",
        ),
        # Index for backtest_run_id lookups
        Index("idx_metrics_backtest_run_id", "backtest_run_id"),
    )

    def __repr__(self) -> str:
        """Return string representation of PerformanceMetrics."""
        return (
            f"<PerformanceMetrics(backtest_run_id={self.backtest_run_id}, "
            f"return={self.total_return}, sharpe={self.sharpe_ratio})>"
        )
