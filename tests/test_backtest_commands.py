"""Tests for backtest CLI commands."""

from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from decimal import Decimal
from click.testing import CliRunner

from src.cli.commands.backtest import backtest, run_backtest, list_backtests


class MockBacktestResult:
    """Mock backtest result for testing."""

    def __init__(self):
        self.total_return = Decimal("1500.00")
        self.total_trades = 10
        self.winning_trades = 6
        self.losing_trades = 4
        self.win_rate = 60.0
        self.largest_win = Decimal("500.00")
        self.largest_loss = Decimal("-200.00")
        self.final_balance = Decimal("11500.00")


class TestBacktestCommands:
    """Test cases for backtest CLI commands."""

    def test_backtest_group_exists(self):
        """Test that backtest command group exists."""
        runner = CliRunner()
        result = runner.invoke(backtest, ["--help"])
        assert result.exit_code == 0
        assert "Backtest commands for running strategies" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    def test_run_backtest_success(
        self, mock_runner_class, mock_data_service_class, mock_test_connection
    ):
        """Test successful backtest run."""
        # Setup mocks
        mock_test_connection.return_value = True

        # Mock data service validation
        mock_data_service = AsyncMock()
        mock_data_service.validate_data_availability.return_value = {
            "valid": True,
            "available_range": {
                "start": datetime(2024, 1, 1),
                "end": datetime(2024, 12, 31),
            },
        }
        mock_data_service.get_adjusted_date_range.return_value = {
            "start": datetime(2024, 1, 1),
            "end": datetime(2024, 1, 31),
        }
        mock_data_service_class.return_value = mock_data_service

        # Mock backtest runner
        mock_runner = MagicMock()
        mock_runner.run_backtest_with_strategy_type = AsyncMock(
            return_value=MockBacktestResult()
        )
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            [
                "--symbol",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
                "--fast-period",
                "5",
                "--slow-period",
                "10",
                "--trade-size",
                "100000",
            ],
        )

        assert result.exit_code == 0
        assert "Running SMA_CROSSOVER backtest for AAPL" in result.output
        assert "Data validation passed" in result.output
        assert "Backtest Results" in result.output
        assert "$1500.00" in result.output  # Total return
        assert "Strategy was profitable!" in result.output

        # Verify methods were called
        mock_data_service.validate_data_availability.assert_called_once()
        mock_runner.run_backtest_with_strategy_type.assert_called_once()
        mock_runner.dispose.assert_called_once()

    @patch("src.cli.commands.backtest.test_connection")
    def test_run_backtest_database_not_accessible(self, mock_test_connection):
        """Test backtest when database is not accessible."""
        mock_test_connection.return_value = False

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 1
        assert "Database not accessible" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    def test_run_backtest_data_validation_failed(
        self, mock_data_service_class, mock_test_connection
    ):
        """Test backtest when data validation fails."""
        mock_test_connection.return_value = True

        # Mock data service validation failure
        mock_data_service = AsyncMock()
        mock_data_service.validate_data_availability.return_value = {
            "valid": False,
            "reason": "No data available for symbol INVALID",
            "available_symbols": ["AAPL", "GOOGL"],
        }
        mock_data_service_class.return_value = mock_data_service

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "INVALID", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 1
        assert "Data validation failed" in result.output
        assert "No data available for symbol INVALID" in result.output
        assert "Available symbols: AAPL, GOOGL" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    def test_run_backtest_data_validation_no_symbols(
        self, mock_data_service_class, mock_test_connection
    ):
        """Test backtest when no symbols are available."""
        mock_test_connection.return_value = True

        # Mock data service validation failure with no symbols
        mock_data_service = AsyncMock()
        mock_data_service.validate_data_availability.return_value = {
            "valid": False,
            "reason": "No data available for symbol AAPL",
            "available_symbols": [],
        }
        mock_data_service_class.return_value = mock_data_service

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 1
        assert "No data available in database" in result.output
        assert "Try importing some CSV data first" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    def test_run_backtest_data_validation_date_range(
        self, mock_data_service_class, mock_test_connection
    ):
        """Test backtest when requested date range is invalid."""
        mock_test_connection.return_value = True

        # Mock data service validation failure with range info
        mock_data_service = AsyncMock()
        mock_data_service.validate_data_availability.return_value = {
            "valid": False,
            "reason": "Start date 2023-01-01 is before available data start 2024-01-01",
            "available_range": {
                "start": datetime(2024, 1, 1),
                "end": datetime(2024, 12, 31),
            },
        }
        mock_data_service_class.return_value = mock_data_service

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2023-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 1
        assert "before available data start" in result.output
        assert "Available range: 2024-01-01" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    def test_run_backtest_losing_strategy(
        self, mock_runner_class, mock_data_service_class, mock_test_connection
    ):
        """Test backtest with losing strategy."""
        mock_test_connection.return_value = True

        # Mock data service validation
        mock_data_service = AsyncMock()
        mock_data_service.validate_data_availability.return_value = {
            "valid": True,
            "available_range": {
                "start": datetime(2024, 1, 1),
                "end": datetime(2024, 12, 31),
            },
        }
        mock_data_service.get_adjusted_date_range.return_value = {
            "start": datetime(2024, 1, 1),
            "end": datetime(2024, 1, 31),
        }
        mock_data_service_class.return_value = mock_data_service

        # Mock losing backtest result
        mock_result = MockBacktestResult()
        mock_result.total_return = Decimal("-500.00")
        mock_result.final_balance = Decimal("9500.00")

        mock_runner = MagicMock()
        mock_runner.run_backtest_with_strategy_type = AsyncMock(
            return_value=mock_result
        )
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 0
        assert "Strategy lost money" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    def test_run_backtest_break_even_strategy(
        self, mock_runner_class, mock_data_service_class, mock_test_connection
    ):
        """Test backtest with break-even strategy."""
        mock_test_connection.return_value = True

        # Mock data service validation
        mock_data_service = AsyncMock()
        mock_data_service.validate_data_availability.return_value = {
            "valid": True,
            "available_range": {
                "start": datetime(2024, 1, 1),
                "end": datetime(2024, 12, 31),
            },
        }
        mock_data_service.get_adjusted_date_range.return_value = {
            "start": datetime(2024, 1, 1),
            "end": datetime(2024, 1, 31),
        }
        mock_data_service_class.return_value = mock_data_service

        # Mock break-even result
        mock_result = MockBacktestResult()
        mock_result.total_return = Decimal("0.00")

        mock_runner = MagicMock()
        mock_runner.run_backtest_with_strategy_type = AsyncMock(
            return_value=mock_result
        )
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 0
        assert "Strategy broke even" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    def test_run_backtest_value_error(
        self, mock_runner_class, mock_data_service_class, mock_test_connection
    ):
        """Test backtest when ValueError occurs."""
        mock_test_connection.return_value = True

        # Mock data service validation
        mock_data_service = AsyncMock()
        mock_data_service.validate_data_availability.return_value = {
            "valid": True,
            "available_range": {
                "start": datetime(2024, 1, 1),
                "end": datetime(2024, 12, 31),
            },
        }
        mock_data_service.get_adjusted_date_range.return_value = {
            "start": datetime(2024, 1, 1),
            "end": datetime(2024, 1, 31),
        }
        mock_data_service_class.return_value = mock_data_service

        # Mock runner to raise ValueError
        mock_runner = MagicMock()
        mock_runner.run_backtest_with_strategy_type = AsyncMock(
            side_effect=ValueError("Invalid parameters")
        )
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 1
        assert "❌ Backtest failed: Invalid parameters" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    def test_run_backtest_unexpected_error(
        self, mock_runner_class, mock_data_service_class, mock_test_connection
    ):
        """Test backtest when unexpected error occurs."""
        mock_test_connection.return_value = True

        # Mock data service validation
        mock_data_service = AsyncMock()
        mock_data_service.validate_data_availability.return_value = {
            "valid": True,
            "available_range": {
                "start": datetime(2024, 1, 1),
                "end": datetime(2024, 12, 31),
            },
        }
        mock_data_service.get_adjusted_date_range.return_value = {
            "start": datetime(2024, 1, 1),
            "end": datetime(2024, 1, 31),
        }
        mock_data_service_class.return_value = mock_data_service

        # Mock runner to raise unexpected error
        mock_runner = MagicMock()
        mock_runner.run_backtest_with_strategy_type = AsyncMock(
            side_effect=Exception("Database connection lost")
        )
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 1
        assert "❌ Unexpected error: Database connection lost" in result.output

    def test_run_backtest_required_parameters(self):
        """Test that run command requires symbol, start, and end parameters."""
        runner = CliRunner()

        # Test missing symbol
        result = runner.invoke(
            run_backtest, ["--start", "2024-01-01", "--end", "2024-01-31"]
        )
        assert result.exit_code == 2
        assert "Missing option" in result.output

        # Test missing start
        result = runner.invoke(
            run_backtest, ["--symbol", "AAPL", "--end", "2024-01-31"]
        )
        assert result.exit_code == 2
        assert "Missing option" in result.output

        # Test missing end
        result = runner.invoke(
            run_backtest, ["--symbol", "AAPL", "--start", "2024-01-01"]
        )
        assert result.exit_code == 2
        assert "Missing option" in result.output

    def test_run_backtest_default_parameters(self):
        """Test that run command uses default values for optional parameters."""
        with patch("src.cli.commands.backtest.test_connection", return_value=True):
            with patch(
                "src.cli.commands.backtest.DataService"
            ) as mock_data_service_class:
                with patch(
                    "src.cli.commands.backtest.MinimalBacktestRunner"
                ) as mock_runner_class:
                    # Mock data service validation
                    mock_data_service = AsyncMock()
                    mock_data_service.validate_data_availability.return_value = {
                        "valid": True,
                        "available_range": {
                            "start": datetime(2024, 1, 1),
                            "end": datetime(2024, 12, 31),
                        },
                    }
                    mock_data_service.get_adjusted_date_range.return_value = {
                        "start": datetime(2024, 1, 1),
                        "end": datetime(2024, 1, 31),
                    }
                    mock_data_service_class.return_value = mock_data_service

                    # Mock runner
                    mock_runner = MagicMock()
                    mock_runner.run_backtest_with_strategy_type = AsyncMock(
                        return_value=MockBacktestResult()
                    )
                    mock_runner_class.return_value = mock_runner

                    runner = CliRunner()
                    result = runner.invoke(
                        run_backtest,
                        [
                            "--symbol",
                            "AAPL",
                            "--start",
                            "2024-01-01",
                            "--end",
                            "2024-01-31",
                        ],
                    )

                    assert result.exit_code == 0

                    # Check that default values were used
                    call_args = mock_runner.run_backtest_with_strategy_type.call_args
                    assert call_args.kwargs["fast_period"] == 10  # default
                    assert call_args.kwargs["slow_period"] == 20  # default
                    assert call_args.kwargs["trade_size"] == Decimal(
                        "1000000"
                    )  # default

    def test_list_backtests_command_exists(self):
        """Test that list command exists."""
        runner = CliRunner()
        result = runner.invoke(list_backtests, ["--help"])
        assert result.exit_code == 0

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    def test_list_backtests_with_data(
        self, mock_data_service_class, mock_test_connection
    ):
        """Test list backtests with available data."""
        mock_test_connection.return_value = True

        # Mock data service
        mock_data_service = AsyncMock()
        mock_data_service.get_available_symbols.return_value = ["AAPL", "GOOGL", "MSFT"]
        mock_data_service.get_data_range.return_value = {
            "start": datetime(2024, 1, 1),
            "end": datetime(2024, 12, 31),
        }
        mock_data_service_class.return_value = mock_data_service

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert "Available Strategies" in result.output
        assert "sma" in result.output
        assert "Available Data" in result.output
        assert "AAPL, GOOGL, MSFT" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    def test_list_backtests_no_data(
        self, mock_data_service_class, mock_test_connection
    ):
        """Test list backtests with no available data."""
        mock_test_connection.return_value = True

        # Mock data service with no symbols
        mock_data_service = AsyncMock()
        mock_data_service.get_available_symbols.return_value = []
        mock_data_service_class.return_value = mock_data_service

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert "No market data available" in result.output
        assert "Import some data first" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    def test_list_backtests_database_not_accessible(self, mock_test_connection):
        """Test list backtests when database is not accessible."""
        mock_test_connection.return_value = False

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert "Database not accessible" in result.output

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    def test_list_backtests_many_symbols(
        self, mock_data_service_class, mock_test_connection
    ):
        """Test list backtests with many symbols (truncation)."""
        mock_test_connection.return_value = True

        # Mock data service with many symbols
        symbols = [f"SYMBOL{i}" for i in range(15)]  # 15 symbols
        mock_data_service = AsyncMock()
        mock_data_service.get_available_symbols.return_value = symbols
        mock_data_service.get_data_range.return_value = {
            "start": datetime(2024, 1, 1),
            "end": datetime(2024, 12, 31),
        }
        mock_data_service_class.return_value = mock_data_service

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert "and 5 more" in result.output  # Should show truncation message

    @patch("src.cli.commands.backtest.test_connection")
    @patch("src.cli.commands.backtest.DataService")
    def test_list_backtests_exception(
        self, mock_data_service_class, mock_test_connection
    ):
        """Test list backtests when exception occurs."""
        mock_test_connection.return_value = True

        # Mock data service to raise exception
        mock_data_service = AsyncMock()
        mock_data_service.get_available_symbols.side_effect = Exception(
            "Database error"
        )
        mock_data_service_class.return_value = mock_data_service

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert "Could not fetch data info" in result.output
