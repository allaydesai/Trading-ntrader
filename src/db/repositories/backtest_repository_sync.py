"""Synchronous repository for backtest database operations (CLI use).

This module provides synchronous database access for CLI commands.
For async operations (future API endpoints), use backtest_repository.py instead.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, joinedload, selectinload

from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.models.backtest import BacktestRun, PerformanceMetrics


class SyncBacktestRepository:
    """
    Synchronous repository for backtest operations.

    Used by CLI commands that don't need async capabilities.
    For async operations (API endpoints), use BacktestRepository instead.

    Attributes:
        session: Synchronous SQLAlchemy session for database operations

    Example:
        >>> with get_sync_session() as session:
        ...     repository = SyncBacktestRepository(session)
        ...     run = repository.create_backtest_run(...)
    """

    def __init__(self, session: Session):
        """
        Initialize repository with database session.

        Args:
            session: Synchronous SQLAlchemy session
        """
        self.session = session

    def create_backtest_run(
        self,
        run_id: UUID,
        strategy_name: str,
        strategy_type: str,
        instrument_symbol: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: Decimal,
        data_source: str,
        execution_status: str,
        execution_duration_seconds: Decimal,
        config_snapshot: dict,
        error_message: Optional[str] = None,
        reproduced_from_run_id: Optional[UUID] = None,
    ) -> BacktestRun:
        """
        Create a new backtest run record.

        Args:
            run_id: Unique business identifier
            strategy_name: Human-readable strategy name
            strategy_type: Strategy category
            instrument_symbol: Trading symbol
            start_date: Backtest period start
            end_date: Backtest period end
            initial_capital: Starting account balance
            data_source: Data provider
            execution_status: "success" or "failed"
            execution_duration_seconds: Time taken to run
            config_snapshot: Complete configuration (JSONB)
            error_message: Error details if failed
            reproduced_from_run_id: Original run if reproduction

        Returns:
            Created BacktestRun instance with ID assigned

        Raises:
            DuplicateRecordError: If run_id already exists
            DatabaseConnectionError: If database operation fails
        """
        try:
            backtest_run = BacktestRun(
                run_id=run_id,
                strategy_name=strategy_name,
                strategy_type=strategy_type,
                instrument_symbol=instrument_symbol,
                start_date=start_date,
                end_date=end_date,
                initial_capital=initial_capital,
                data_source=data_source,
                execution_status=execution_status,
                execution_duration_seconds=execution_duration_seconds,
                error_message=error_message,
                config_snapshot=config_snapshot,
                reproduced_from_run_id=reproduced_from_run_id,
            )

            self.session.add(backtest_run)
            self.session.flush()  # Get ID before commit
            self.session.refresh(backtest_run)

            return backtest_run

        except IntegrityError as e:
            if "unique constraint" in str(e.orig).lower():
                raise DuplicateRecordError(f"Run ID {run_id} already exists") from e
            raise

        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    def create_performance_metrics(
        self,
        backtest_run_id: int,
        total_return: Decimal,
        final_balance: Decimal,
        cagr: Optional[Decimal] = None,
        sharpe_ratio: Optional[Decimal] = None,
        sortino_ratio: Optional[Decimal] = None,
        max_drawdown: Optional[Decimal] = None,
        max_drawdown_date: Optional[datetime] = None,
        calmar_ratio: Optional[Decimal] = None,
        volatility: Optional[Decimal] = None,
        # Additional returns-based metrics
        risk_return_ratio: Optional[Decimal] = None,
        avg_return: Optional[Decimal] = None,
        avg_win_return: Optional[Decimal] = None,
        avg_loss_return: Optional[Decimal] = None,
        # Trading metrics
        total_trades: int = 0,
        winning_trades: int = 0,
        losing_trades: int = 0,
        win_rate: Optional[Decimal] = None,
        profit_factor: Optional[Decimal] = None,
        expectancy: Optional[Decimal] = None,
        avg_win: Optional[Decimal] = None,
        avg_loss: Optional[Decimal] = None,
        # Additional PnL-based metrics
        total_pnl: Optional[Decimal] = None,
        total_pnl_percentage: Optional[Decimal] = None,
        max_winner: Optional[Decimal] = None,
        max_loser: Optional[Decimal] = None,
        min_winner: Optional[Decimal] = None,
        min_loser: Optional[Decimal] = None,
    ) -> PerformanceMetrics:
        """
        Create performance metrics for a backtest run.

        Args:
            backtest_run_id: Foreign key to backtest_runs.id
            total_return: Total return percentage
            final_balance: Final account balance
            cagr: Compound annual growth rate
            sharpe_ratio: Risk-adjusted return
            sortino_ratio: Downside risk-adjusted return
            max_drawdown: Maximum peak-to-trough decline
            max_drawdown_date: When max drawdown occurred
            calmar_ratio: Return / max drawdown ratio
            volatility: Returns standard deviation
            total_trades: Total trade count
            winning_trades: Count of profitable trades
            losing_trades: Count of losing trades
            win_rate: Percentage of winning trades
            profit_factor: Gross profit / gross loss
            expectancy: Expected profit per trade
            avg_win: Average winning trade amount
            avg_loss: Average losing trade amount

        Returns:
            Created PerformanceMetrics instance

        Raises:
            IntegrityError: If backtest_run_id already has metrics
        """
        metrics = PerformanceMetrics(
            backtest_run_id=backtest_run_id,
            total_return=total_return,
            final_balance=final_balance,
            cagr=cagr,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            max_drawdown_date=max_drawdown_date,
            calmar_ratio=calmar_ratio,
            volatility=volatility,
            # Additional returns-based metrics
            risk_return_ratio=risk_return_ratio,
            avg_return=avg_return,
            avg_win_return=avg_win_return,
            avg_loss_return=avg_loss_return,
            # Trading metrics
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            expectancy=expectancy,
            avg_win=avg_win,
            avg_loss=avg_loss,
            # Additional PnL-based metrics
            total_pnl=total_pnl,
            total_pnl_percentage=total_pnl_percentage,
            max_winner=max_winner,
            max_loser=max_loser,
            min_winner=min_winner,
            min_loser=min_loser,
        )

        self.session.add(metrics)
        self.session.flush()
        self.session.refresh(metrics)

        return metrics

    def find_by_run_id(self, run_id: UUID) -> Optional[BacktestRun]:
        """
        Find backtest by business identifier.

        Eagerly loads associated performance metrics to avoid N+1 queries.

        Args:
            run_id: Unique business identifier

        Returns:
            BacktestRun with metrics loaded, or None if not found
        """
        stmt = (
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.run_id == run_id)
        )

        result = self.session.execute(stmt)
        return result.scalar_one_or_none()

    def find_recent(
        self,
        limit: int = 20,
        cursor: Optional[Tuple[datetime, int]] = None,
    ) -> List[BacktestRun]:
        """
        Find recent backtests with cursor pagination.

        Uses cursor-based pagination for efficient large dataset queries.

        Args:
            limit: Maximum number of records to return
            cursor: Pagination cursor (created_at, id) from last record

        Returns:
            List of BacktestRun instances with metrics loaded
        """
        stmt = select(BacktestRun).options(selectinload(BacktestRun.metrics))

        if cursor:
            created_at, id = cursor
            stmt = stmt.where(tuple_(BacktestRun.created_at, BacktestRun.id) < (created_at, id))

        stmt = stmt.order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()).limit(limit)

        result = self.session.execute(stmt)
        return list(result.scalars().all())

    def find_by_strategy(
        self,
        strategy_name: str,
        limit: int = 20,
        cursor: Optional[Tuple[datetime, int]] = None,
    ) -> List[BacktestRun]:
        """
        Find backtests filtered by strategy name.

        Args:
            strategy_name: Strategy to filter by
            limit: Maximum records
            cursor: Pagination cursor

        Returns:
            List of matching BacktestRun instances
        """
        stmt = (
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.strategy_name == strategy_name)
        )

        if cursor:
            created_at, id = cursor
            stmt = stmt.where(
                and_(
                    BacktestRun.strategy_name == strategy_name,
                    tuple_(BacktestRun.created_at, BacktestRun.id) < (created_at, id),
                )
            )

        stmt = stmt.order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc()).limit(limit)

        result = self.session.execute(stmt)
        return list(result.scalars().all())

    def find_by_run_ids(self, run_ids: List[UUID]) -> List[BacktestRun]:
        """
        Find multiple backtests by IDs (for comparison).

        Args:
            run_ids: List of business identifiers

        Returns:
            List of BacktestRun instances (may be fewer than requested)
        """
        stmt = (
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.run_id.in_(run_ids))
            .order_by(BacktestRun.created_at.desc())
        )

        result = self.session.execute(stmt)
        return list(result.scalars().all())

    def find_top_performers_by_sharpe(
        self,
        limit: int = 20,
    ) -> List[BacktestRun]:
        """
        Find top performing backtests by Sharpe ratio.

        Returns backtests ordered by Sharpe ratio in descending order,
        excluding any backtests with NULL Sharpe ratios.

        Args:
            limit: Maximum records to return

        Returns:
            List of BacktestRun instances with metrics loaded, ordered by Sharpe ratio DESC

        Example:
            >>> repository = SyncBacktestRepository(session)
            >>> top_3 = repository.find_top_performers_by_sharpe(limit=3)
            >>> top_3[0].metrics.sharpe_ratio  # Highest Sharpe ratio
        """
        stmt = (
            select(BacktestRun)
            .join(PerformanceMetrics, BacktestRun.id == PerformanceMetrics.backtest_run_id)
            .options(joinedload(BacktestRun.metrics))
            .where(PerformanceMetrics.sharpe_ratio.isnot(None))
            .order_by(PerformanceMetrics.sharpe_ratio.desc())
            .limit(limit)
        )

        result = self.session.execute(stmt)
        backtests = list(result.scalars().unique().all())

        # Ensure metrics are loaded for each backtest
        for backtest in backtests:
            self.session.refresh(backtest, ["metrics"])

        return backtests

    def find_top_performers_by_return(
        self,
        limit: int = 20,
    ) -> List[BacktestRun]:
        """
        Find top performing backtests by total return.

        Returns backtests ordered by total return in descending order.

        Args:
            limit: Maximum records to return

        Returns:
            List of BacktestRun instances with metrics loaded, ordered by total return DESC

        Example:
            >>> repository = SyncBacktestRepository(session)
            >>> top_3 = repository.find_top_performers_by_return(limit=3)
            >>> top_3[0].metrics.total_return  # Highest total return
        """
        stmt = (
            select(BacktestRun)
            .join(PerformanceMetrics, BacktestRun.id == PerformanceMetrics.backtest_run_id)
            .options(joinedload(BacktestRun.metrics))
            .order_by(PerformanceMetrics.total_return.desc())
            .limit(limit)
        )

        result = self.session.execute(stmt)
        backtests = list(result.scalars().unique().all())

        # Ensure metrics are loaded for each backtest
        for backtest in backtests:
            self.session.refresh(backtest, ["metrics"])

        return backtests

    def count_by_strategy(self, strategy_name: str) -> int:
        """
        Count backtests for a specific strategy.

        Args:
            strategy_name: Strategy to count

        Returns:
            Total count of matching backtests
        """
        stmt = select(func.count(BacktestRun.id)).where(BacktestRun.strategy_name == strategy_name)

        result = self.session.execute(stmt)
        return result.scalar_one()

    def count_all(self) -> int:
        """
        Count all backtests in the database.

        Returns:
            Total count of all backtests
        """
        stmt = select(func.count(BacktestRun.id))
        result = self.session.execute(stmt)
        return result.scalar_one()

    def find_by_instrument(self, instrument_symbol: str, limit: int = 20) -> List[BacktestRun]:
        """
        Find backtests by instrument symbol.

        Args:
            instrument_symbol: Trading symbol to filter by
            limit: Maximum records to return

        Returns:
            List of BacktestRun instances matching the symbol
        """
        stmt = (
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.instrument_symbol == instrument_symbol)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .limit(limit)
        )

        result = self.session.execute(stmt)
        return list(result.scalars().all())
