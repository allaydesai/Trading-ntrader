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
