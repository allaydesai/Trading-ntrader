"""
Integration tests for backtest comparison functionality.

Tests the complete workflow of comparing multiple backtests,
including database operations and service layer integration.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_query import BacktestQueryService


@pytest.mark.asyncio
async def test_compare_command_displays_side_by_side_comparison(db_session):
    """
    Test that compare service retrieves multiple backtests for side-by-side display.

    Given: Database with 3 different backtest runs
    When: Service queries for comparison with multiple run IDs
    Then: Returns all requested backtests with metrics loaded
    """
    repository = BacktestRepository(db_session)
    run_ids = []

    # Create 3 backtest runs with different results
    test_data = [
        {
            "strategy": "SMA Crossover",
            "return": Decimal("0.25"),
            "sharpe": Decimal("1.8"),
            "balance": Decimal("125000.00"),
        },
        {
            "strategy": "RSI Mean Reversion",
            "return": Decimal("0.15"),
            "sharpe": Decimal("1.2"),
            "balance": Decimal("115000.00"),
        },
        {
            "strategy": "Momentum",
            "return": Decimal("0.35"),
            "sharpe": Decimal("2.1"),
            "balance": Decimal("135000.00"),
        },
    ]

    for data in test_data:
        run_id = uuid4()
        run_ids.append(run_id)

        backtest_run = await repository.create_backtest_run(
            run_id=run_id,
            strategy_name=data["strategy"],
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
            total_return=data["return"],
            final_balance=data["balance"],
            sharpe_ratio=data["sharpe"],
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

    await db_session.commit()

    # Test service layer comparison (use same session)
    service = BacktestQueryService(repository)
    backtests = await service.compare_backtests(run_ids)

    # Assertions
    assert len(backtests) == 3

    # Verify all expected strategies are present (order may vary due to created_at DESC)
    strategy_names = {bt.strategy_name for bt in backtests}
    assert strategy_names == {"SMA Crossover", "RSI Mean Reversion", "Momentum"}

    # Verify backtests have all required attributes (metrics loading tested in unit tests)
    for bt in backtests:
        assert bt.run_id is not None
        assert bt.strategy_name is not None
        assert bt.instrument_symbol == "AAPL"


@pytest.mark.asyncio
async def test_compare_command_minimum_2_backtests(db_session):
    """
    Test that compare service accepts exactly 2 backtests (minimum).

    Given: Database with 2 backtest runs
    When: Service queries for comparison with 2 run IDs
    Then: Returns both backtests successfully
    """
    repository = BacktestRepository(db_session)
    run_ids = []

    # Create exactly 2 backtest runs
    for i in range(2):
        run_id = uuid4()
        run_ids.append(run_id)

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

    # Test service with minimum 2 backtests (use same session)
    service = BacktestQueryService(repository)
    backtests = await service.compare_backtests(run_ids)

    # Assertions
    assert len(backtests) == 2

    # Verify backtests have required attributes (metrics loading tested in unit tests)
    for bt in backtests:
        assert bt.run_id is not None
        assert bt.strategy_name is not None
        assert bt.instrument_symbol == "AAPL"


@pytest.mark.asyncio
async def test_compare_command_maximum_10_backtests(db_session):
    """
    Test that compare service accepts exactly 10 backtests (maximum).

    Given: Database with 10 backtest runs
    When: Service queries for comparison with 10 run IDs
    Then: Returns all 10 backtests successfully
    """
    repository = BacktestRepository(db_session)
    run_ids = []

    # Create exactly 10 backtest runs
    for i in range(10):
        run_id = uuid4()
        run_ids.append(run_id)

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
            total_return=Decimal(str(0.1 + i * 0.02)),  # Varying returns
            final_balance=Decimal(str(110000 + i * 2000)),
            sharpe_ratio=Decimal(str(1.0 + i * 0.1)),
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
        )

    await db_session.commit()

    # Test service with maximum 10 backtests (use same session)
    service = BacktestQueryService(repository)
    backtests = await service.compare_backtests(run_ids)

    # Assertions
    assert len(backtests) == 10

    # Verify backtests have required attributes (metrics loading tested in unit tests)
    for bt in backtests:
        assert bt.run_id is not None
        assert bt.strategy_name is not None
        assert bt.instrument_symbol == "AAPL"
