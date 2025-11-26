"""Unit tests for BacktestResult data class."""

import pytest

from src.core.backtest_runner import BacktestResult


@pytest.mark.unit
def test_backtest_result_creation():
    """Test BacktestResult creation and properties."""
    result = BacktestResult(total_return=1000.0, total_trades=10, winning_trades=6, losing_trades=4)

    assert result.total_return == 1000.0
    assert result.total_trades == 10
    assert result.win_rate == 60.0
    assert "BacktestResult" in str(result)


@pytest.mark.unit
def test_backtest_result_zero_trades():
    """Test BacktestResult with zero trades."""
    result = BacktestResult()

    assert result.win_rate == 0.0
    assert result.total_trades == 0


@pytest.mark.unit
def test_backtest_result_default_values():
    """Test BacktestResult with default initialization."""
    result = BacktestResult()

    assert result.total_return == 0.0
    assert result.total_trades == 0
    assert result.winning_trades == 0
    assert result.losing_trades == 0
    assert result.largest_win == 0.0
    assert result.largest_loss == 0.0
    assert result.final_balance == 0.0


@pytest.mark.unit
def test_backtest_result_win_rate_calculation():
    """Test win rate calculation with various scenarios."""
    # 100% win rate
    result_all_wins = BacktestResult(total_trades=5, winning_trades=5, losing_trades=0)
    assert result_all_wins.win_rate == 100.0

    # 0% win rate
    result_all_losses = BacktestResult(total_trades=5, winning_trades=0, losing_trades=5)
    assert result_all_losses.win_rate == 0.0

    # 50% win rate
    result_mixed = BacktestResult(total_trades=10, winning_trades=5, losing_trades=5)
    assert result_mixed.win_rate == 50.0


@pytest.mark.unit
def test_backtest_result_string_representation():
    """Test that string representation contains key information."""
    result = BacktestResult(
        total_return=500.0,
        total_trades=8,
        winning_trades=5,
        losing_trades=3,
        final_balance=10500.0,
    )

    result_str = str(result)
    assert "BacktestResult" in result_str
