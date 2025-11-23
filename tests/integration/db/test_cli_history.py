"""
Integration tests for backtest history functionality.

Tests the complete workflow of querying backtest history,
including database operations and service layer integration.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

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


@pytest.mark.asyncio
async def test_history_command_sorts_by_sharpe_ratio(db_session):
    """
    Test that history service can sort backtests by Sharpe ratio descending.

    Given: Database with backtests having different Sharpe ratios
    When: Service queries for top performers by Sharpe ratio
    Then: Returns backtests sorted by Sharpe ratio in descending order
    """
    repository = BacktestRepository(db_session)

    # Create backtests with varying Sharpe ratios
    sharpe_ratios = [
        Decimal("2.5"),
        Decimal("1.8"),
        Decimal("3.2"),
        Decimal("0.5"),
        Decimal("1.2"),
    ]

    for i, sharpe in enumerate(sharpe_ratios):
        run_id = uuid4()

        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name=f"Strategy {i}",
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
            sharpe_ratio=sharpe,
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

    await db_session.commit()

    # Test service with sorting by Sharpe ratio
    service = BacktestQueryService(repository)
    backtests = await service.find_top_performers(metric="sharpe_ratio", limit=5)

    # Assertions
    assert len(backtests) == 5

    # Verify descending order by Sharpe ratio
    sharpe_values = [bt.metrics.sharpe_ratio for bt in backtests]
    assert sharpe_values == sorted(sharpe_values, reverse=True)

    # Verify highest Sharpe is first
    assert backtests[0].metrics.sharpe_ratio == Decimal("3.2")
    assert backtests[-1].metrics.sharpe_ratio == Decimal("0.5")


@pytest.mark.asyncio
async def test_history_command_sorts_by_total_return(db_session):
    """
    Test that history service can sort backtests by total return descending.

    Given: Database with backtests having different total returns
    When: Service queries for top performers by total return
    Then: Returns backtests sorted by total return in descending order
    """
    repository = BacktestRepository(db_session)

    # Create backtests with varying total returns
    total_returns = [
        Decimal("0.25"),
        Decimal("0.15"),
        Decimal("0.35"),
        Decimal("0.05"),
        Decimal("0.20"),
    ]

    for i, total_return in enumerate(total_returns):
        run_id = uuid4()

        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name=f"Strategy {i}",
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
            total_return=total_return,
            final_balance=Decimal("115000.00"),
            sharpe_ratio=Decimal("1.5"),
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

    await db_session.commit()

    # Test service with sorting by total return
    service = BacktestQueryService(repository)
    backtests = await service.find_top_performers(metric="total_return", limit=5)

    # Assertions
    assert len(backtests) == 5

    # Verify descending order by total return
    return_values = [bt.metrics.total_return for bt in backtests]
    assert return_values == sorted(return_values, reverse=True)

    # Verify highest return is first
    assert backtests[0].metrics.total_return == Decimal("0.35")
    assert backtests[-1].metrics.total_return == Decimal("0.05")


@pytest.mark.asyncio
async def test_history_command_excludes_null_sharpe_ratios(db_session):
    """
    Test that top performers query excludes backtests with NULL Sharpe ratios.

    Given: Database with some backtests having NULL Sharpe ratios
    When: Service queries for top performers by Sharpe ratio
    Then: Returns only backtests with non-NULL Sharpe ratios
    """
    repository = BacktestRepository(db_session)

    # Create backtests - some with NULL Sharpe ratios
    for i in range(5):
        run_id = uuid4()

        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name=f"Strategy {i}",
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

        # Only give 3 backtests a Sharpe ratio
        sharpe_ratio = Decimal(str(2.0 - i * 0.5)) if i < 3 else None

        await repository.create_performance_metrics(
            backtest_run_id=backtest_run.id,
            total_return=Decimal("0.15"),
            final_balance=Decimal("115000.00"),
            sharpe_ratio=sharpe_ratio,
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

    await db_session.commit()

    # Test service - should only return 3 backtests
    service = BacktestQueryService(repository)
    backtests = await service.find_top_performers(metric="sharpe_ratio", limit=10)

    # Assertions
    assert len(backtests) == 3
    assert all(bt.metrics.sharpe_ratio is not None for bt in backtests)


@pytest.mark.asyncio
async def test_strategy_history_chronological_order(db_session):
    """
    Test that strategy history shows backtests in chronological order (most recent first).

    Given: Database with 15+ backtest runs for the same strategy at different times
    When: Service queries for strategy history
    Then: Returns backtests ordered by creation date descending (newest first)
    """
    import asyncio

    repository = BacktestRepository(db_session)

    # Create 18 backtest runs for the same strategy at different times
    strategy_name = "SMA Crossover"
    created_runs = []

    for i in range(18):
        run_id = uuid4()

        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name=strategy_name,
            strategy_type="trend_following",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_status="success",
            execution_duration_seconds=Decimal("10.5"),
            config_snapshot={
                "version": "1.0",
                "config": {
                    "fast_period": 10 + i,  # Vary parameter
                    "slow_period": 20 + i,
                },
            },
        )

        await repository.create_performance_metrics(
            backtest_run_id=backtest_run.id,
            total_return=Decimal(str(0.10 + i * 0.01)),  # Vary return
            final_balance=Decimal("115000.00"),
            sharpe_ratio=Decimal(str(1.5 + i * 0.1)),  # Vary Sharpe
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

        created_runs.append(backtest_run)

        # Small delay to ensure different created_at timestamps
        await asyncio.sleep(0.001)

    await db_session.commit()

    # Test service with strategy filter
    service = BacktestQueryService(repository)
    backtests = await service.list_by_strategy(strategy_name, limit=20)

    # Assertions
    assert len(backtests) == 18
    assert all(bt.strategy_name == strategy_name for bt in backtests)

    # Verify chronological order (newest first)
    created_at_values = [bt.created_at for bt in backtests]
    assert created_at_values == sorted(created_at_values, reverse=True)

    # Verify the most recent backtest is first
    assert backtests[0].config_snapshot["config"]["fast_period"] == 27  # Last created (i=17)
    assert backtests[-1].config_snapshot["config"]["fast_period"] == 10  # First created (i=0)
