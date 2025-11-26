"""
Pytest fixtures for backtest detail view UI testing.

Provides test data and configuration for testing detail view routes,
templates, and model transformations.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.db.models.backtest import BacktestRun, PerformanceMetrics


@pytest.fixture
def sample_backtest_run() -> BacktestRun:
    """
    Create a sample BacktestRun for testing.

    Returns:
        BacktestRun instance with typical values

    Example:
        >>> def test_display(sample_backtest_run):
        ...     assert sample_backtest_run.strategy_name == "SMA Crossover"
    """
    return BacktestRun(
        id=1,
        run_id=uuid4(),
        strategy_name="SMA Crossover",
        strategy_type="trend_following",
        instrument_symbol="AAPL",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="IBKR",
        execution_status="success",
        execution_duration_seconds=Decimal("45.5"),
        config_snapshot={"fast_period": 10, "slow_period": 20},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_metrics() -> PerformanceMetrics:
    """
    Create sample PerformanceMetrics for testing.

    Returns:
        PerformanceMetrics instance with typical values

    Example:
        >>> def test_metrics(sample_metrics):
        ...     assert sample_metrics.sharpe_ratio == Decimal("1.85")
    """
    return PerformanceMetrics(
        id=1,
        backtest_run_id=1,
        total_return=Decimal("0.25"),
        final_balance=Decimal("125000.00"),
        cagr=Decimal("0.28"),
        sharpe_ratio=Decimal("1.85"),
        sortino_ratio=Decimal("2.10"),
        max_drawdown=Decimal("-0.15"),
        max_drawdown_date=datetime(2024, 6, 15, tzinfo=timezone.utc),
        calmar_ratio=Decimal("1.87"),
        volatility=Decimal("0.18"),
        total_trades=100,
        winning_trades=60,
        losing_trades=40,
        win_rate=Decimal("0.60"),
        profit_factor=Decimal("2.5"),
        expectancy=Decimal("250.00"),
        avg_win=Decimal("500.00"),
        avg_loss=Decimal("-250.00"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_backtest_with_metrics(
    sample_backtest_run: BacktestRun, sample_metrics: PerformanceMetrics
) -> BacktestRun:
    """
    Create a sample BacktestRun with associated metrics.

    Args:
        sample_backtest_run: BacktestRun fixture
        sample_metrics: PerformanceMetrics fixture

    Returns:
        BacktestRun with metrics relationship populated

    Example:
        >>> def test_with_metrics(sample_backtest_with_metrics):
        ...     assert sample_backtest_with_metrics.metrics.sharpe_ratio == Decimal("1.85")
    """
    sample_backtest_run.metrics = sample_metrics
    return sample_backtest_run


@pytest.fixture
def failed_backtest_run() -> BacktestRun:
    """
    Create a failed BacktestRun for testing error cases.

    Returns:
        BacktestRun instance with failed status and error message
    """
    return BacktestRun(
        id=2,
        run_id=uuid4(),
        strategy_name="Broken Strategy",
        strategy_type="experimental",
        instrument_symbol="TEST",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="CSV",
        execution_status="failed",
        execution_duration_seconds=Decimal("2.5"),
        config_snapshot={},
        error_message="Strategy execution failed: Invalid signal generation",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
