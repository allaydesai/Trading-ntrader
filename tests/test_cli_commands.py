"""Tests for CLI commands."""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from click.exceptions import ClickException
from click.testing import CliRunner

from src.cli.commands.run import run_simple


def test_run_simple_command_defaults():
    """Test run_simple command with default parameters."""
    runner = CliRunner()

    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_result = Mock()
        mock_result.total_return = 1000.0
        mock_result.total_trades = 10
        mock_result.win_rate = 60.0
        mock_result.winning_trades = 6
        mock_result.losing_trades = 4
        mock_result.largest_win = 500.0
        mock_result.largest_loss = -300.0
        mock_result.final_balance = 101000.0

        mock_runner.run_sma_backtest.return_value = mock_result
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple)

        assert result.exit_code == 0
        assert "Running simple SMA backtest" in result.output
        assert "Backtest completed successfully" in result.output
        assert "Total Return: 1,000.00" in result.output
        assert "Total Trades: 10" in result.output
        assert "Win Rate: 60.0%" in result.output

        # Verify runner was called with default parameters
        mock_runner.run_sma_backtest.assert_called_once_with(
            fast_period=None,
            slow_period=None,
            trade_size=None,
            num_bars=None,
        )
        mock_runner.dispose.assert_called_once()


def test_run_simple_command_with_custom_parameters():
    """Test run_simple command with custom parameters."""
    runner = CliRunner()

    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_result = Mock()
        mock_result.total_return = 500.0
        mock_result.total_trades = 5
        mock_result.win_rate = 80.0
        mock_result.winning_trades = 4
        mock_result.losing_trades = 1
        mock_result.largest_win = 200.0
        mock_result.largest_loss = -50.0
        mock_result.final_balance = 100500.0

        mock_runner.run_sma_backtest.return_value = mock_result
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple, [
            "--fast-period", "5",
            "--slow-period", "15",
            "--trade-size", "500000",
            "--bars", "500"
        ])

        assert result.exit_code == 0
        mock_runner.run_sma_backtest.assert_called_once_with(
            fast_period=5,
            slow_period=15,
            trade_size=Decimal("500000"),
            num_bars=500,
        )


def test_run_simple_command_strategy_choice():
    """Test run_simple command strategy choice validation."""
    runner = CliRunner()

    # Test valid strategy
    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_result = Mock()
        mock_result.total_return = 1000.0
        mock_result.total_trades = 10
        mock_result.win_rate = 60.0
        mock_result.winning_trades = 6
        mock_result.losing_trades = 4
        mock_result.largest_win = 500.0
        mock_result.largest_loss = -300.0
        mock_result.final_balance = 101000.0

        mock_runner.run_sma_backtest.return_value = mock_result
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple, ["--strategy", "sma"])
        assert result.exit_code == 0

    # Test invalid strategy
    result = runner.invoke(run_simple, ["--strategy", "invalid"])
    assert result.exit_code == 2  # Click validation error
    assert "Invalid value for '--strategy'" in result.output


def test_run_simple_command_data_choice():
    """Test run_simple command data choice validation."""
    runner = CliRunner()

    # Test valid data source
    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_result = Mock()
        mock_result.total_return = 1000.0
        mock_result.total_trades = 10
        mock_result.win_rate = 60.0
        mock_result.winning_trades = 6
        mock_result.losing_trades = 4
        mock_result.largest_win = 500.0
        mock_result.largest_loss = -300.0
        mock_result.final_balance = 101000.0

        mock_runner.run_sma_backtest.return_value = mock_result
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple, ["--data", "mock"])
        assert result.exit_code == 0

    # Test invalid data source
    result = runner.invoke(run_simple, ["--data", "invalid"])
    assert result.exit_code == 2  # Click validation error
    assert "Invalid value for '--data'" in result.output


def test_run_simple_command_negative_return():
    """Test run_simple command handles negative returns."""
    runner = CliRunner()

    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_result = Mock()
        mock_result.total_return = -500.0
        mock_result.total_trades = 8
        mock_result.win_rate = 25.0
        mock_result.winning_trades = 2
        mock_result.losing_trades = 6
        mock_result.largest_win = 100.0
        mock_result.largest_loss = -200.0
        mock_result.final_balance = 99500.0

        mock_runner.run_sma_backtest.return_value = mock_result
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple)

        assert result.exit_code == 0
        assert "Strategy shows loss on mock data" in result.output
        # Should show negative return in red formatting
        assert "total_return >= 0" not in result.output  # Negative formatting applied


def test_run_simple_command_zero_trades():
    """Test run_simple command handles zero trades scenario."""
    runner = CliRunner()

    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_result = Mock()
        mock_result.total_return = 0.0
        mock_result.total_trades = 0
        mock_result.win_rate = 0.0
        mock_result.winning_trades = 0
        mock_result.losing_trades = 0
        mock_result.largest_win = 0.0
        mock_result.largest_loss = 0.0
        mock_result.final_balance = 1000000.0

        mock_runner.run_sma_backtest.return_value = mock_result
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple)

        assert result.exit_code == 0
        assert "Total Trades: 0" in result.output
        # Should not display win/loss details for zero trades
        assert "Winning Trades:" not in result.output
        assert "Losing Trades:" not in result.output


def test_run_simple_command_backtest_exception():
    """Test run_simple command handles backtest exceptions."""
    runner = CliRunner()

    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_runner.run_sma_backtest.side_effect = Exception("Backtest failed")
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple)

        assert result.exit_code == 1  # ClickException exit code
        assert "Backtest failed: Backtest failed" in result.output
        # Should still call dispose even on exception
        mock_runner.dispose.assert_called_once()


def test_run_simple_command_invalid_trade_size():
    """Test run_simple command handles invalid trade size format."""
    runner = CliRunner()

    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_runner.run_sma_backtest.side_effect = Exception("Invalid decimal")
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple, ["--trade-size", "invalid"])

        assert result.exit_code == 1
        mock_runner.dispose.assert_called_once()


def test_run_simple_command_help():
    """Test run_simple command help message."""
    runner = CliRunner()
    result = runner.invoke(run_simple, ["--help"])

    assert result.exit_code == 0
    assert "Run a simple backtest with mock data" in result.output
    assert "--strategy" in result.output
    assert "--data" in result.output
    assert "--fast-period" in result.output
    assert "--slow-period" in result.output
    assert "--trade-size" in result.output
    assert "--bars" in result.output


def test_run_simple_rich_console_output():
    """Test that run_simple uses Rich console for formatted output."""
    runner = CliRunner()

    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class, \
         patch("src.cli.commands.run.console") as mock_console:

        mock_runner = Mock()
        mock_result = Mock()
        mock_result.total_return = 1000.0
        mock_result.total_trades = 10
        mock_result.win_rate = 60.0
        mock_result.winning_trades = 6
        mock_result.losing_trades = 4
        mock_result.largest_win = 500.0
        mock_result.largest_loss = -300.0
        mock_result.final_balance = 101000.0

        mock_runner.run_sma_backtest.return_value = mock_result
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple)

        assert result.exit_code == 0
        # Verify console.print was called multiple times
        assert mock_console.print.call_count >= 5


@pytest.mark.parametrize("fast_period,slow_period,expected_calls", [
    (None, None, 1),
    (10, None, 1),
    (None, 20, 1),
    (10, 20, 1),
])
def test_run_simple_parameter_combinations(fast_period, slow_period, expected_calls):
    """Test various parameter combinations for run_simple command."""
    runner = CliRunner()

    args = []
    if fast_period is not None:
        args.extend(["--fast-period", str(fast_period)])
    if slow_period is not None:
        args.extend(["--slow-period", str(slow_period)])

    with patch("src.cli.commands.run.MinimalBacktestRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_result = Mock()
        mock_result.total_return = 1000.0
        mock_result.total_trades = 5
        mock_result.win_rate = 60.0
        mock_result.winning_trades = 3
        mock_result.losing_trades = 2
        mock_result.largest_win = 500.0
        mock_result.largest_loss = -200.0
        mock_result.final_balance = 101000.0

        mock_runner.run_sma_backtest.return_value = mock_result
        mock_runner_class.return_value = mock_runner

        result = runner.invoke(run_simple, args)

        assert result.exit_code == 0
        assert mock_runner.run_sma_backtest.call_count == expected_calls