"""
Repository for backtest persistence and retrieval.

This module provides the data access layer for backtest metadata and performance
metrics, implementing async database operations with SQLAlchemy.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple, Union
from uuid import UUID

from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.models.backtest import BacktestRun, PerformanceMetrics
from src.api.models.filter_models import FilterState, SortColumn, SortOrder


class BacktestRepository:
    """
    Repository for backtest persistence and retrieval.

    Provides async database operations for creating, querying, and managing
    backtest execution records and performance metrics.

    Attributes:
        session: Async SQLAlchemy session for database operations

    Example:
        >>> async with get_session() as session:
        ...     repository = BacktestRepository(session)
        ...     run = await repository.create_backtest_run(...)
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session
        """
        self.session = session

    async def create_backtest_run(
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
            await self.session.flush()  # Get ID before commit
            await self.session.refresh(backtest_run)

            return backtest_run

        except IntegrityError as e:
            if "unique constraint" in str(e.orig).lower():
                raise DuplicateRecordError(f"Run ID {run_id} already exists") from e
            raise

        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    async def create_performance_metrics(
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
        total_trades: int = 0,
        winning_trades: int = 0,
        losing_trades: int = 0,
        win_rate: Optional[Decimal] = None,
        profit_factor: Optional[Decimal] = None,
        expectancy: Optional[Decimal] = None,
        avg_win: Optional[Decimal] = None,
        avg_loss: Optional[Decimal] = None,
        # Additional returns-based metrics (from get_performance_stats_returns)
        risk_return_ratio: Optional[Decimal] = None,
        avg_return: Optional[Decimal] = None,
        avg_win_return: Optional[Decimal] = None,
        avg_loss_return: Optional[Decimal] = None,
        # Additional PnL-based metrics (from get_performance_stats_pnls)
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
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            expectancy=expectancy,
            avg_win=avg_win,
            avg_loss=avg_loss,
            # Additional returns-based metrics
            risk_return_ratio=risk_return_ratio,
            avg_return=avg_return,
            avg_win_return=avg_win_return,
            avg_loss_return=avg_loss_return,
            # Additional PnL-based metrics
            total_pnl=total_pnl,
            total_pnl_percentage=total_pnl_percentage,
            max_winner=max_winner,
            max_loser=max_loser,
            min_winner=min_winner,
            min_loser=min_loser,
        )

        self.session.add(metrics)
        await self.session.flush()
        await self.session.refresh(metrics)

        return metrics

    async def find_by_run_id(self, run_id: UUID) -> Optional[BacktestRun]:
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

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_recent(
        self,
        limit: int = 20,
        cursor: Optional[Union[datetime, Tuple[datetime, int]]] = None,
    ) -> List[BacktestRun]:
        """
        Find recent backtests with cursor pagination.

        Uses cursor-based pagination for efficient large dataset queries.

        Args:
            limit: Maximum number of records to return
            cursor: Pagination cursor - either datetime (created_at) or tuple (created_at, id)

        Returns:
            List of BacktestRun instances with metrics loaded
        """
        stmt = select(BacktestRun).options(selectinload(BacktestRun.metrics))

        if cursor:
            # Handle both datetime and tuple cursor formats
            if isinstance(cursor, tuple):
                created_at, id = cursor
                stmt = stmt.where(
                    tuple_(BacktestRun.created_at, BacktestRun.id) < (created_at, id)
                )
            else:
                # If cursor is just datetime, filter by created_at only
                stmt = stmt.where(BacktestRun.created_at < cursor)

        stmt = stmt.order_by(
            BacktestRun.created_at.desc(), BacktestRun.id.desc()
        ).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_strategy(
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

        stmt = stmt.order_by(
            BacktestRun.created_at.desc(), BacktestRun.id.desc()
        ).limit(limit)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_run_ids(self, run_ids: List[UUID]) -> List[BacktestRun]:
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

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_top_performers(
        self,
        metric: str = "sharpe_ratio",
        limit: int = 20,
    ) -> List[BacktestRun]:
        """
        Find top performing backtests by specified metric.

        Generic method that delegates to specific metric methods.

        Args:
            metric: Metric to sort by ('sharpe_ratio', 'total_return', 'sortino_ratio')
            limit: Maximum records to return

        Returns:
            List of BacktestRun instances with metrics loaded, ordered by metric DESC

        Raises:
            ValueError: If metric is not supported

        Example:
            >>> repository = BacktestRepository(session)
            >>> top_3 = await repository.find_top_performers(metric="sharpe_ratio", limit=3)
        """
        metric_column_map = {
            "sharpe_ratio": PerformanceMetrics.sharpe_ratio,
            "total_return": PerformanceMetrics.total_return,
            "sortino_ratio": PerformanceMetrics.sortino_ratio,
            "max_drawdown": PerformanceMetrics.max_drawdown,
        }

        if metric not in metric_column_map:
            raise ValueError(
                f"Unsupported metric: {metric}. "
                f"Supported metrics: {list(metric_column_map.keys())}"
            )

        metric_column = metric_column_map[metric]

        stmt = (
            select(BacktestRun)
            .join(
                PerformanceMetrics, BacktestRun.id == PerformanceMetrics.backtest_run_id
            )
            .options(joinedload(BacktestRun.metrics))
            .where(metric_column.isnot(None))
            .order_by(metric_column.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        backtests = list(result.scalars().unique().all())

        # Ensure metrics are loaded for each backtest
        for backtest in backtests:
            await self.session.refresh(backtest, ["metrics"])

        return backtests

    async def find_top_performers_by_sharpe(
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
            >>> repository = BacktestRepository(session)
            >>> top_3 = await repository.find_top_performers_by_sharpe(limit=3)
            >>> top_3[0].metrics.sharpe_ratio  # Highest Sharpe ratio
        """
        stmt = (
            select(BacktestRun)
            .join(
                PerformanceMetrics, BacktestRun.id == PerformanceMetrics.backtest_run_id
            )
            .options(joinedload(BacktestRun.metrics))
            .where(PerformanceMetrics.sharpe_ratio.isnot(None))
            .order_by(PerformanceMetrics.sharpe_ratio.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        backtests = list(result.scalars().unique().all())

        # Ensure metrics are loaded for each backtest
        for backtest in backtests:
            await self.session.refresh(backtest, ["metrics"])

        return backtests

    async def find_top_performers_by_return(
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
            >>> repository = BacktestRepository(session)
            >>> top_3 = await repository.find_top_performers_by_return(limit=3)
            >>> top_3[0].metrics.total_return  # Highest total return
        """
        stmt = (
            select(BacktestRun)
            .join(
                PerformanceMetrics, BacktestRun.id == PerformanceMetrics.backtest_run_id
            )
            .options(joinedload(BacktestRun.metrics))
            .order_by(PerformanceMetrics.total_return.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        backtests = list(result.scalars().unique().all())

        # Ensure metrics are loaded for each backtest
        for backtest in backtests:
            await self.session.refresh(backtest, ["metrics"])

        return backtests

    async def count_by_strategy(self, strategy_name: str) -> int:
        """
        Count backtests for a specific strategy.

        Args:
            strategy_name: Strategy to count

        Returns:
            Total count of matching backtests
        """
        stmt = select(func.count(BacktestRun.id)).where(
            BacktestRun.strategy_name == strategy_name
        )

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def get_filtered_backtests(
        self,
        filter_state: FilterState,
    ) -> Tuple[List[BacktestRun], int]:
        """
        Get filtered, sorted, and paginated backtests.

        Applies all filters from FilterState and returns matching results
        with total count for pagination.

        Args:
            filter_state: Complete filter/sort/pagination state

        Returns:
            Tuple of (list of BacktestRun instances, total count)

        Example:
            >>> state = FilterState(strategy="SMA", sort=SortColumn.SHARPE_RATIO)
            >>> runs, total = await repo.get_filtered_backtests(state)
        """
        # Build base query
        query = select(BacktestRun).options(selectinload(BacktestRun.metrics))
        count_query = select(func.count(BacktestRun.id))

        # Apply filters
        conditions = []

        if filter_state.strategy:
            conditions.append(BacktestRun.strategy_name == filter_state.strategy)

        if filter_state.instrument:
            # Case-insensitive partial match
            conditions.append(
                BacktestRun.instrument_symbol.ilike(f"%{filter_state.instrument}%")
            )

        if filter_state.date_from:
            conditions.append(
                func.date(BacktestRun.created_at) >= filter_state.date_from
            )

        if filter_state.date_to:
            conditions.append(func.date(BacktestRun.created_at) <= filter_state.date_to)

        if filter_state.status:
            conditions.append(BacktestRun.execution_status == filter_state.status.value)

        # Apply conditions to both queries
        if conditions:
            query = query.where(and_(*conditions))
            count_query = count_query.where(and_(*conditions))

        # Get total count (before pagination)
        count_result = await self.session.execute(count_query)
        total_count = count_result.scalar_one()

        # Apply sorting
        sort_column = self._get_sort_column(filter_state.sort)
        needs_join = filter_state.sort in [
            SortColumn.TOTAL_RETURN,
            SortColumn.SHARPE_RATIO,
            SortColumn.MAX_DRAWDOWN,
        ]

        if needs_join:
            query = query.outerjoin(
                PerformanceMetrics, BacktestRun.id == PerformanceMetrics.backtest_run_id
            )

        if filter_state.order == SortOrder.ASC:
            query = query.order_by(sort_column.asc().nulls_last())
        else:
            query = query.order_by(sort_column.desc().nulls_last())

        # Apply pagination
        offset = (filter_state.page - 1) * filter_state.page_size
        query = query.offset(offset).limit(filter_state.page_size)

        # Execute query
        result = await self.session.execute(query)
        backtests = list(result.scalars().unique().all())

        return backtests, total_count

    def _get_sort_column(self, sort: SortColumn):
        """
        Map SortColumn enum to SQLAlchemy column.

        Args:
            sort: SortColumn enum value

        Returns:
            SQLAlchemy column for ordering
        """
        column_map = {
            SortColumn.CREATED_AT: BacktestRun.created_at,
            SortColumn.STRATEGY_NAME: BacktestRun.strategy_name,
            SortColumn.INSTRUMENT_SYMBOL: BacktestRun.instrument_symbol,
            SortColumn.EXECUTION_STATUS: BacktestRun.execution_status,
            SortColumn.TOTAL_RETURN: PerformanceMetrics.total_return,
            SortColumn.SHARPE_RATIO: PerformanceMetrics.sharpe_ratio,
            SortColumn.MAX_DRAWDOWN: PerformanceMetrics.max_drawdown,
        }
        return column_map[sort]

    async def get_distinct_strategies(self) -> List[str]:
        """
        Get all distinct strategy names.

        Returns:
            List of unique strategy names, sorted alphabetically
        """
        stmt = (
            select(BacktestRun.strategy_name)
            .distinct()
            .order_by(BacktestRun.strategy_name)
        )

        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_distinct_instruments(self) -> List[str]:
        """
        Get all distinct instrument symbols.

        Returns:
            List of unique instrument symbols, sorted alphabetically
        """
        stmt = (
            select(BacktestRun.instrument_symbol)
            .distinct()
            .order_by(BacktestRun.instrument_symbol)
        )

        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def bulk_create_trades(self, trades: List) -> None:
        """
        Bulk insert trades for a backtest run.

        Uses SQLAlchemy's add_all() for efficient bulk insertion.

        Args:
            trades: List of Trade model instances to insert

        Raises:
            DatabaseConnectionError: If database operation fails
        """
        try:
            self.session.add_all(trades)
            await self.session.flush()

        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e
