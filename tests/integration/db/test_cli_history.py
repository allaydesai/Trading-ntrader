"""
Integration tests for backtest history functionality.

Tests the complete workflow of querying backtest history,
including database operations and service layer integration.
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_query import BacktestQueryService


@pytest.mark.asyncio
async def test_history_command_lists_recent_backtests(db_session):
    """
    Test that history service lists 20 recent backtests by default.

    Given: Database with 25 backtest runs
    When: Service queries for recent backtests
    Then: Returns 20 most recent backtests
    """
    # Create 25 test backtest runs
    repository = BacktestRepository(db_session)

    for i in range(25):
        run_id = uuid4()

        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name=f"Test Strategy {i}",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_status="success",
            execution_duration_seconds=Decimal("10.5"),
            config_snapshot={"version": "1.0", "config": {}},
        )

        # Add metrics for successful backtests
        await repository.create_performance_metrics(
            backtest_run_id=backtest_run.id,
            total_return=Decimal("0.15"),
            final_balance=Decimal("115000.00"),
            sharpe_ratio=Decimal("1.5"),
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

    await db_session.commit()

    # Test service layer
    service = BacktestQueryService(repository)
    backtests = await service.list_recent_backtests(limit=20)

    # Assertions
    assert len(backtests) == 20
    assert all("Test Strategy" in bt.strategy_name for bt in backtests)


@pytest.mark.asyncio
async def test_history_command_filters_by_strategy(db_session):
    """
    Test that history service filters backtests by strategy name.

    Given: Database with multiple strategies
    When: Service queries with strategy filter
    Then: Returns only backtests for that strategy
    """
    repository = BacktestRepository(db_session)

    # Create backtests for different strategies
    strategies = ["SMA Crossover", "RSI Mean Reversion", "Momentum"]

    for strategy in strategies:
        for i in range(5):
            run_id = uuid4()

            backtest_run = await repository.create_backtest_run(
                run_id=run_id,
                strategy_name=strategy,
                strategy_type="test",
                instrument_symbol="AAPL",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="Mock",
                execution_status="success",
                execution_duration_seconds=Decimal("10.5"),
                config_snapshot={"version": "1.0", "config": {}},
            )

            await repository.create_performance_metrics(
                backtest_run_id=backtest_run.id,
                total_return=Decimal("0.15"),
                final_balance=Decimal("115000.00"),
                sharpe_ratio=Decimal("1.5"),
                total_trades=10,
                winning_trades=6,
                losing_trades=4,
            )

    await db_session.commit()

    # Test service with strategy filter
    service = BacktestQueryService(repository)
    backtests = await service.list_by_strategy("SMA Crossover")

    # Assertions
    assert len(backtests) == 5
    assert all(bt.strategy_name == "SMA Crossover" for bt in backtests)


@pytest.mark.asyncio
async def test_history_command_custom_limit(db_session):
    """
    Test that history service respects custom limit parameter.

    Given: Database with 30 backtest runs
    When: Service queries with limit=50
    Then: Returns all 30 available backtests
    """
    repository = BacktestRepository(db_session)

    # Create 30 test backtest runs
    for i in range(30):
        run_id = uuid4()

        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name=f"Test Strategy {i}",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_status="success",
            execution_duration_seconds=Decimal("10.5"),
            config_snapshot={"version": "1.0", "config": {}},
        )

        await repository.create_performance_metrics(
            backtest_run_id=backtest_run.id,
            total_return=Decimal("0.15"),
            final_balance=Decimal("115000.00"),
            sharpe_ratio=Decimal("1.5"),
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

    await db_session.commit()

    # Test service with custom limit
    service = BacktestQueryService(repository)
    backtests = await service.list_recent_backtests(limit=50)

    # Assertions - should return all 30 since limit is 50
    assert len(backtests) == 30
    assert all("Test Strategy" in bt.strategy_name for bt in backtests)
