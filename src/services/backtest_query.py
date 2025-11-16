"""
Service for querying backtest results.

Provides business logic for retrieving, filtering, and comparing
backtest execution records with proper limit enforcement and pagination.
"""

import structlog
from datetime import datetime
from typing import List, Optional, Tuple
from uuid import UUID

from src.db.repositories.backtest_repository import BacktestRepository
from src.db.models.backtest import BacktestRun
from src.api.models.dashboard import DashboardSummary, to_recent_item
from src.api.models.backtest_list import (
    BacktestListPage,
    FilteredBacktestListPage,
    to_list_item,
)
from src.api.models.filter_models import FilterState

logger = structlog.get_logger(__name__)


class BacktestQueryService:
    """
    Service for querying backtest results.

    Provides business logic layer for backtest queries with limit enforcement,
    pagination support, and consistent error handling.

    Attributes:
        repository: BacktestRepository for database operations

    Example:
        >>> async with get_session() as session:
        ...     repository = BacktestRepository(session)
        ...     service = BacktestQueryService(repository)
        ...     backtests = await service.list_recent_backtests(limit=20)
    """

    def __init__(self, repository: BacktestRepository):
        """
        Initialize with repository dependency.

        Args:
            repository: BacktestRepository instance for database access
        """
        self.repository = repository

    async def get_backtest_by_id(self, run_id: UUID) -> Optional[BacktestRun]:
        """
        Retrieve complete backtest details by ID.

        Args:
            run_id: Unique business identifier

        Returns:
            BacktestRun with metrics loaded, or None if not found

        Example:
            >>> run_id = UUID("12345678-1234-5678-1234-567812345678")
            >>> backtest = await service.get_backtest_by_id(run_id)
            >>> if backtest:
            ...     print(f"Strategy: {backtest.strategy_name}")
        """
        logger.debug("Fetching backtest by ID", run_id=str(run_id))
        return await self.repository.find_by_run_id(run_id)

    async def list_recent_backtests(
        self,
        limit: int = 20,
        cursor: Optional[Tuple[datetime, int]] = None,
    ) -> List[BacktestRun]:
        """
        List recent backtests with pagination.

        Enforces maximum limit of 1000 to prevent excessive database load.

        Args:
            limit: Maximum records to return (default 20, max 1000)
            cursor: Pagination cursor (created_at, id) from last result

        Returns:
            List of BacktestRun instances with metrics loaded

        Example:
            >>> backtests = await service.list_recent_backtests(limit=50)
            >>> for bt in backtests:
            ...     print(f"{bt.strategy_name}: {bt.metrics.total_return}")
        """
        # Enforce maximum limit
        limit = min(limit, 1000)

        logger.debug(
            "Listing recent backtests", limit=limit, has_cursor=cursor is not None
        )
        return await self.repository.find_recent(limit=limit, cursor=cursor)

    async def list_by_strategy(
        self,
        strategy_name: str,
        limit: int = 20,
        cursor: Optional[Tuple[datetime, int]] = None,
    ) -> List[BacktestRun]:
        """
        List backtests filtered by strategy name.

        Args:
            strategy_name: Strategy to filter by
            limit: Maximum records (default 20, max 1000)
            cursor: Pagination cursor

        Returns:
            List of matching BacktestRun instances

        Example:
            >>> backtests = await service.list_by_strategy("SMA Crossover")
            >>> print(f"Found {len(backtests)} SMA Crossover backtests")
        """
        # Enforce maximum limit
        limit = min(limit, 1000)

        logger.debug(
            "Listing backtests by strategy", strategy=strategy_name, limit=limit
        )
        return await self.repository.find_by_strategy(
            strategy_name=strategy_name, limit=limit, cursor=cursor
        )

    async def compare_backtests(self, run_ids: List[UUID]) -> List[BacktestRun]:
        """
        Retrieve multiple backtests for comparison.

        Args:
            run_ids: List of business identifiers (2-10)

        Returns:
            List of BacktestRun instances

        Raises:
            ValueError: If fewer than 2 or more than 10 IDs provided

        Example:
            >>> ids = [uuid1, uuid2, uuid3]
            >>> backtests = await service.compare_backtests(ids)
            >>> for bt in backtests:
            ...     print(f"{bt.strategy_name}: Sharpe={bt.metrics.sharpe_ratio}")
        """
        if len(run_ids) < 2:
            raise ValueError("Must compare at least 2 backtests")
        if len(run_ids) > 10:
            raise ValueError("Cannot compare more than 10 backtests")

        logger.debug("Comparing backtests", count=len(run_ids))
        return await self.repository.find_by_run_ids(run_ids)

    async def find_top_performers(
        self, metric: str = "sharpe_ratio", limit: int = 20
    ) -> List[BacktestRun]:
        """
        Find top performing backtests by metric.

        Supports sorting by multiple performance metrics including Sharpe ratio
        and total return.

        Args:
            metric: Metric to sort by ("sharpe_ratio", "total_return")
            limit: Maximum records (default 20, max 1000)

        Returns:
            List of top performing BacktestRun instances ordered by metric DESC

        Raises:
            ValueError: If unsupported metric specified

        Example:
            >>> # Find top 10 by Sharpe ratio
            >>> top = await service.find_top_performers(metric="sharpe_ratio", limit=10)
            >>> best = top[0]
            >>> print(f"Best: {best.strategy_name} (Sharpe: {best.metrics.sharpe_ratio})")
            >>>
            >>> # Find top 5 by total return
            >>> top_returns = await service.find_top_performers(metric="total_return", limit=5)
            >>> print(f"Best return: {top_returns[0].metrics.total_return:.2%}")
        """
        # Enforce maximum limit
        limit = min(limit, 1000)

        logger.debug("Finding top performers", metric=metric, limit=limit)

        if metric == "sharpe_ratio":
            return await self.repository.find_top_performers_by_sharpe(limit)
        elif metric == "total_return":
            return await self.repository.find_top_performers_by_return(limit)
        else:
            raise ValueError(
                f"Unsupported metric: {metric}. Supported: sharpe_ratio, total_return"
            )

    async def get_dashboard_stats(self) -> DashboardSummary:
        """
        Get aggregate statistics for dashboard display.

        Retrieves total count, best Sharpe ratio, worst drawdown, and recent activity.

        Returns:
            DashboardSummary with aggregate statistics

        Example:
            >>> stats = await service.get_dashboard_stats()
            >>> print(f"Total: {stats.total_backtests}")
            >>> print(f"Best Sharpe: {stats.best_sharpe_ratio}")
        """
        logger.debug("Fetching dashboard statistics")

        # Get total count
        total = await self._count_all_backtests()

        # Initialize with empty state
        summary = DashboardSummary(total_backtests=total)

        if total == 0:
            return summary

        # Get best Sharpe ratio
        top_sharpe = await self.repository.find_top_performers_by_sharpe(limit=1)
        if top_sharpe and top_sharpe[0].metrics:
            summary.best_sharpe_ratio = top_sharpe[0].metrics.sharpe_ratio
            summary.best_sharpe_strategy = top_sharpe[0].strategy_name

        # Get worst drawdown (smallest value since drawdowns are negative)
        # We need to find the MINIMUM (most negative) drawdown
        # The repository sorts DESC, so we scan all runs to find the worst
        all_runs = await self.repository.find_recent(limit=1000)
        if all_runs:
            worst_drawdown_run = None
            worst_drawdown_value = None
            for run in all_runs:
                if run.metrics and run.metrics.max_drawdown is not None:
                    if (
                        worst_drawdown_value is None
                        or run.metrics.max_drawdown < worst_drawdown_value
                    ):
                        worst_drawdown_value = run.metrics.max_drawdown
                        worst_drawdown_run = run

            if worst_drawdown_run and worst_drawdown_value is not None:
                summary.worst_max_drawdown = worst_drawdown_value
                summary.worst_drawdown_strategy = worst_drawdown_run.strategy_name

        # Get recent activity
        recent = await self.get_recent_activity(limit=5)
        summary.recent_backtests = recent

        return summary

    async def get_recent_activity(self, limit: int = 5) -> list:
        """
        Get recent backtest activity for dashboard display.

        Args:
            limit: Maximum number of recent backtests to return (default 5)

        Returns:
            List of RecentBacktestItem instances

        Example:
            >>> recent = await service.get_recent_activity(limit=5)
            >>> for item in recent:
            ...     print(f"{item.strategy_name}: {item.run_id_short}")
        """
        limit = min(limit, 100)  # Cap at 100 for safety

        logger.debug("Fetching recent activity", limit=limit)

        backtests = await self.repository.find_recent(limit=limit)
        return [to_recent_item(bt) for bt in backtests]

    async def _count_all_backtests(self) -> int:
        """
        Count total number of backtests in the system.

        Returns:
            Total count of all backtest runs
        """
        # Get count by loading a limited set and checking if more exist
        # This is a simple approach; for production, add a count method to repository
        from sqlalchemy import func, select
        from src.db.models.backtest import BacktestRun

        stmt = select(func.count(BacktestRun.id))
        result = await self.repository.session.execute(stmt)
        return result.scalar_one()

    async def get_backtest_list_page(
        self, page: int = 1, page_size: int = 20
    ) -> BacktestListPage:
        """
        Get paginated backtest list for table display.

        Args:
            page: Page number (1-indexed)
            page_size: Results per page (default 20)

        Returns:
            BacktestListPage with items and pagination info

        Example:
            >>> page = await service.get_backtest_list_page(page=1, page_size=20)
            >>> print(f"Page {page.page} of {page.total_pages}")
            >>> for item in page.backtests:
            ...     print(f"{item.strategy_name}: {item.total_return}")
        """
        # Validate inputs
        page = max(1, page)
        page_size = min(max(10, page_size), 100)

        logger.debug("Fetching backtest list page", page=page, page_size=page_size)

        # Get total count
        total_count = await self._count_all_backtests()

        # Calculate offset
        offset = (page - 1) * page_size

        # Get backtests for current page using offset-based pagination
        all_backtests = await self.repository.find_recent(limit=offset + page_size)
        page_backtests = all_backtests[offset : offset + page_size]

        # Convert to view models
        items = [to_list_item(bt) for bt in page_backtests]

        return BacktestListPage(
            backtests=items,
            page=page,
            page_size=page_size,
            total_count=total_count,
        )

    async def get_filtered_backtest_list_page(
        self, filter_state: FilterState
    ) -> FilteredBacktestListPage:
        """
        Get filtered, sorted, and paginated backtest list.

        Applies filters, sorting, and pagination from FilterState and returns
        complete response with available options for filter dropdowns.

        Args:
            filter_state: Complete filter/sort/pagination state

        Returns:
            FilteredBacktestListPage with items, pagination, and filter options

        Example:
            >>> state = FilterState(
            ...     strategy="SMA Crossover",
            ...     sort=SortColumn.SHARPE_RATIO,
            ...     order=SortOrder.DESC
            ... )
            >>> page = await service.get_filtered_backtest_list_page(state)
            >>> print(f"Found {page.total_count} matching backtests")
        """
        logger.debug(
            "Fetching filtered backtest list",
            strategy=filter_state.strategy,
            instrument=filter_state.instrument,
            status=filter_state.status,
            sort=filter_state.sort,
            order=filter_state.order,
            page=filter_state.page,
        )

        # Get filtered backtests and total count
        backtests, total_count = await self.repository.get_filtered_backtests(
            filter_state
        )

        # Get available filter options
        available_strategies = await self.repository.get_distinct_strategies()
        available_instruments = await self.repository.get_distinct_instruments()

        # Convert to view models
        items = [to_list_item(bt) for bt in backtests]

        return FilteredBacktestListPage(
            backtests=items,
            page=filter_state.page,
            page_size=filter_state.page_size,
            total_count=total_count,
            filter_state=filter_state,
            available_strategies=available_strategies,
            available_instruments=available_instruments,
        )
