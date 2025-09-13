"""Tests for the minimal backtest runner."""

from decimal import Decimal

import pytest

from src.core.backtest_runner import MinimalBacktestRunner, BacktestResult


def test_backtest_result_creation():
    """Test BacktestResult creation and properties."""
    result = BacktestResult(
        total_return=1000.0,
        total_trades=10,
        winning_trades=6,
        losing_trades=4
    )

    assert result.total_return == 1000.0
    assert result.total_trades == 10
    assert result.win_rate == 60.0
    assert "BacktestResult" in str(result)


def test_backtest_result_zero_trades():
    """Test BacktestResult with zero trades."""
    result = BacktestResult()

    assert result.win_rate == 0.0
    assert result.total_trades == 0


def test_minimal_backtest_runner_initialization():
    """Test MinimalBacktestRunner initialization."""
    runner = MinimalBacktestRunner()

    assert runner.engine is None
    assert runner._results is None
    assert runner.settings is not None


def test_run_sma_backtest():
    """Test running an SMA backtest with mock data."""
    runner = MinimalBacktestRunner()

    # Run a small backtest
    result = runner.run_sma_backtest(
        fast_period=5,
        slow_period=10,
        trade_size=Decimal("100000"),
        num_bars=100
    )

    # Basic checks
    assert isinstance(result, BacktestResult)
    assert result.total_trades >= 0
    assert result.final_balance > 0

    # Clean up
    runner.dispose()


def test_get_detailed_results():
    """Test getting detailed results."""
    runner = MinimalBacktestRunner()

    # Run backtest first
    runner.run_sma_backtest(num_bars=50)

    # Get detailed results
    detailed = runner.get_detailed_results()

    assert "basic_metrics" in detailed
    assert "account_summary" in detailed
    assert "positions" in detailed
    assert "orders" in detailed

    assert detailed["basic_metrics"]["total_trades"] >= 0
    assert detailed["account_summary"]["currency"] == "USD"

    # Clean up
    runner.dispose()


def test_reset_and_dispose():
    """Test reset and dispose functionality."""
    runner = MinimalBacktestRunner()

    # Run backtest
    runner.run_sma_backtest(num_bars=20)
    assert runner.engine is not None
    assert runner._results is not None

    # Test reset
    runner.reset()
    assert runner._results is None
    # Engine should still exist but be reset

    # Test dispose
    runner.dispose()
    assert runner._results is None