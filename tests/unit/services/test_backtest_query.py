"""
Unit tests for BacktestQueryService.

Tests the business logic layer for querying backtest results,
including limit enforcement and pagination handling.
"""

import pytest
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

from src.services.backtest_query import BacktestQueryService
from src.db.models.backtest import BacktestRun


class TestBacktestQueryService:
    """Test suite for BacktestQueryService business logic."""

    @pytest.fixture
    def mock_repository(self):
        """Create mock repository for testing."""
        return AsyncMock()

    @pytest.fixture
    def service(self, mock_repository):
        """Create service instance with mock repository."""
        return BacktestQueryService(mock_repository)

    @pytest.mark.asyncio
    async def test_list_recent_backtests_enforces_max_limit(
        self, service, mock_repository
    ):
        """
        Test that list_recent_backtests enforces maximum limit of 1000.

        Given: User requests 2000 backtests
        When: Service processes the request
        Then: Only 1000 backtests are queried from repository
        """
        # Setup mock
        mock_repository.find_recent.return_value = []

        # Call service with excessive limit
        await service.list_recent_backtests(limit=2000)

        # Verify repository was called with max limit
        mock_repository.find_recent.assert_called_once_with(limit=1000, cursor=None)

    @pytest.mark.asyncio
    async def test_list_recent_backtests_respects_reasonable_limit(
        self, service, mock_repository
    ):
        """
        Test that reasonable limits are passed through unchanged.

        Given: User requests 50 backtests
        When: Service processes the request
        Then: Exactly 50 backtests are requested from repository
        """
        # Setup mock
        mock_repository.find_recent.return_value = []

        # Call service with reasonable limit
        await service.list_recent_backtests(limit=50)

        # Verify repository was called with requested limit
        mock_repository.find_recent.assert_called_once_with(limit=50, cursor=None)

    @pytest.mark.asyncio
    async def test_list_recent_backtests_with_cursor(self, service, mock_repository):
        """
        Test that cursor pagination is passed through to repository.

        Given: User provides pagination cursor
        When: Service processes the request
        Then: Cursor is passed to repository unchanged
        """
        # Setup cursor
        cursor = (datetime(2023, 1, 1, tzinfo=timezone.utc), 123)

        # Setup mock
        mock_repository.find_recent.return_value = []

        # Call service with cursor
        await service.list_recent_backtests(limit=20, cursor=cursor)

        # Verify cursor was passed through
        mock_repository.find_recent.assert_called_once_with(limit=20, cursor=cursor)

    @pytest.mark.asyncio
    async def test_list_by_strategy_enforces_max_limit(self, service, mock_repository):
        """
        Test that list_by_strategy enforces maximum limit.

        Given: User requests 1500 backtests for a strategy
        When: Service processes the request
        Then: Only 1000 backtests are queried
        """
        # Setup mock
        mock_repository.find_by_strategy.return_value = []

        # Call service with excessive limit
        await service.list_by_strategy("SMA Crossover", limit=1500)

        # Verify max limit enforced
        mock_repository.find_by_strategy.assert_called_once_with(
            strategy_name="SMA Crossover", limit=1000, cursor=None
        )

    @pytest.mark.asyncio
    async def test_list_by_strategy_filters_by_name(self, service, mock_repository):
        """
        Test that strategy name is passed correctly to repository.

        Given: User queries for specific strategy
        When: Service processes the request
        Then: Correct strategy name is used in repository call
        """
        # Setup mock
        mock_repository.find_by_strategy.return_value = []

        # Call service
        await service.list_by_strategy("RSI Mean Reversion", limit=20)

        # Verify strategy name passed correctly
        mock_repository.find_by_strategy.assert_called_once_with(
            strategy_name="RSI Mean Reversion", limit=20, cursor=None
        )

    @pytest.mark.asyncio
    async def test_get_backtest_by_id_returns_result(self, service, mock_repository):
        """
        Test that get_backtest_by_id returns repository result.

        Given: Repository returns a backtest
        When: Service queries by ID
        Then: Same backtest is returned to caller
        """
        # Create mock backtest
        run_id = uuid4()
        mock_backtest = MagicMock(spec=BacktestRun)
        mock_backtest.run_id = run_id

        # Setup mock
        mock_repository.find_by_run_id.return_value = mock_backtest

        # Call service
        result = await service.get_backtest_by_id(run_id)

        # Verify result
        assert result == mock_backtest
        mock_repository.find_by_run_id.assert_called_once_with(run_id)

    @pytest.mark.asyncio
    async def test_get_backtest_by_id_returns_none_when_not_found(
        self, service, mock_repository
    ):
        """
        Test that get_backtest_by_id returns None for missing backtest.

        Given: Repository returns None (backtest not found)
        When: Service queries by ID
        Then: None is returned to caller
        """
        # Setup mock
        mock_repository.find_by_run_id.return_value = None

        # Call service
        result = await service.get_backtest_by_id(uuid4())

        # Verify None returned
        assert result is None
