"""Tests for backtest CLI commands."""

import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.cli.commands.backtest import (
    backtest,
    run_backtest,
    list_backtests,
    run_config_backtest,
)


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

    @pytest.mark.component
    def test_backtest_group_exists(self):
        """Test that backtest command group exists."""
        runner = CliRunner()
        result = runner.invoke(backtest, ["--help"])
        assert result.exit_code == 0
        assert "Backtest commands for running strategies" in result.output

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.component
    def test_run_backtest_success(self, mock_runner_class, mock_catalog_service_class):
        """Test successful backtest run with catalog data."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        # Mock fetch_or_load to return bars
        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]  # Mock bar objects

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = (
            MagicMock()
        )  # Mock instrument
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest runner
        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            return MockBacktestResult(), None  # Returns (result, run_id)

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
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
        assert (
            "Data available in catalog" in result.output or "Fetched" in result.output
        )
        assert "Backtest Results" in result.output
        assert "$1500.00" in result.output  # Total return
        assert "Strategy was profitable!" in result.output

        # Verify catalog service was called
        mock_catalog_service.get_availability.assert_called_once()
        mock_runner.dispose.assert_called_once()

    @patch("src.cli.commands.backtest.DataCatalogService")
    @pytest.mark.component
    def test_run_backtest_data_not_found(self, mock_catalog_service_class):
        """Test backtest when data is not found."""
        from src.services.exceptions import DataNotFoundError

        # Mock catalog service to raise DataNotFoundError
        mock_catalog_service = MagicMock()
        mock_catalog_service.get_availability.return_value = None

        async def mock_fetch_or_load(*args, **kwargs):
            raise DataNotFoundError("No data found")

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "INVALID", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert "No data" in result.output or "not found" in result.output.lower()

    @patch("src.cli.commands.backtest.DataCatalogService")
    @pytest.mark.component
    def test_run_backtest_ibkr_connection_error(self, mock_catalog_service_class):
        """Test backtest when IBKR connection fails."""
        from src.services.exceptions import IBKRConnectionError

        # Mock catalog service to raise IBKRConnectionError
        mock_catalog_service = MagicMock()
        mock_catalog_service.get_availability.return_value = None

        async def mock_fetch_or_load(*args, **kwargs):
            raise IBKRConnectionError("Connection failed")

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert "IBKR" in result.output or "connection" in result.output.lower()

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.component
    def test_run_backtest_losing_strategy(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """Test backtest with losing strategy."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock losing backtest result
        mock_result = MockBacktestResult()
        mock_result.total_return = Decimal("-500.00")
        mock_result.final_balance = Decimal("9500.00")

        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            return mock_result

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 0
        assert "Strategy lost money" in result.output

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.component
    def test_run_backtest_break_even_strategy(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """Test backtest with break-even strategy."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock break-even result
        mock_result = MockBacktestResult()
        mock_result.total_return = Decimal("0.00")

        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            return mock_result

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 0
        assert "Strategy broke even" in result.output

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.component
    def test_run_backtest_value_error(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """Test backtest when ValueError occurs."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock runner to raise ValueError
        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            raise ValueError("Invalid parameters")

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert (
            "Backtest failed" in result.output or "Invalid parameters" in result.output
        )

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.component
    def test_run_backtest_unexpected_error(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """Test backtest when unexpected error occurs."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock runner to raise unexpected error
        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            raise Exception("Unexpected failure")

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert "Unexpected error" in result.output or "failed" in result.output.lower()

    @pytest.mark.component
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

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.component
    def test_run_backtest_default_parameters(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """Test that run command uses default values for optional parameters."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock runner
        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            # Capture kwargs to verify defaults
            mock_run_backtest.last_kwargs = kwargs
            return MockBacktestResult()

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
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
        assert mock_run_backtest.last_kwargs["fast_period"] == 10  # default
        assert mock_run_backtest.last_kwargs["slow_period"] == 20  # default
        assert mock_run_backtest.last_kwargs["trade_size"] == 1000000  # default

    @pytest.mark.component
    def test_list_backtests_command_exists(self):
        """Test that list command exists."""
        runner = CliRunner()
        result = runner.invoke(list_backtests, ["--help"])
        assert result.exit_code == 0

    @patch("src.cli.commands.backtest.DataCatalogService")
    @pytest.mark.component
    def test_list_backtests_with_data(self, mock_catalog_service_class):
        """Test list backtests with available data."""
        # Mock catalog service with data
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.instrument_id = "AAPL.NASDAQ"
        mock_availability.bar_type_spec = "1-MINUTE-LAST"
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)

        mock_catalog_service.availability_cache = {
            "AAPL.NASDAQ-1-MINUTE-LAST": mock_availability
        }
        mock_catalog_service.catalog_path = "/tmp/catalog"
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert "Available Strategies" in result.output
        assert "sma" in result.output.lower()
        assert "Available Data" in result.output or "catalog" in result.output.lower()

    @patch("src.cli.commands.backtest.DataCatalogService")
    @pytest.mark.component
    def test_list_backtests_no_data(self, mock_catalog_service_class):
        """Test list backtests with no available data."""
        # Mock catalog service with no data
        mock_catalog_service = MagicMock()
        mock_catalog_service.availability_cache = {}
        mock_catalog_service.catalog_path = "/tmp/catalog"
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert "No market data" in result.output or "catalog" in result.output.lower()

    @patch("src.cli.commands.backtest.DataCatalogService")
    @pytest.mark.component
    def test_list_backtests_many_symbols(self, mock_catalog_service_class):
        """Test list backtests with many symbols (truncation)."""
        # Mock catalog service with many symbols
        mock_catalog_service = MagicMock()
        availability_cache = {}
        for i in range(15):  # Create 15 instruments
            mock_avail = MagicMock()
            mock_avail.instrument_id = f"SYMBOL{i}.NASDAQ"
            mock_avail.bar_type_spec = "1-MINUTE-LAST"
            mock_avail.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
            mock_avail.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
            availability_cache[f"SYMBOL{i}.NASDAQ-1-MINUTE-LAST"] = mock_avail

        mock_catalog_service.availability_cache = availability_cache
        mock_catalog_service.catalog_path = "/tmp/catalog"
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert "and 10 more" in result.output  # Should show truncation message

    @patch("src.cli.commands.backtest.DataCatalogService")
    @pytest.mark.component
    def test_list_backtests_exception(self, mock_catalog_service_class):
        """Test list backtests when exception occurs."""
        # Mock catalog service to raise exception
        mock_catalog_service_class.side_effect = Exception("Catalog error")

        runner = CliRunner()
        result = runner.invoke(list_backtests)

        assert result.exit_code == 0
        assert (
            "Could not fetch data info" in result.output
            or "error" in result.output.lower()
        )


class TestRunConfigBacktest:
    """Test cases for run-config backtest command."""

    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.component
    def test_run_config_success_mock_data(self, mock_runner_class):
        """Test successful run-config with mock data source."""
        # Create a temporary config file
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create a simple config file
            with open("test_config.yaml", "w") as f:
                f.write("""
strategy:
  type: sma_crossover
  fast_period: 5
  slow_period: 10
trading:
  trade_size: 100000
""")

            # Mock backtest runner
            mock_runner = MagicMock()
            mock_runner.run_from_config_file.return_value = MockBacktestResult()
            mock_runner.dispose = MagicMock()
            mock_runner_class.return_value = mock_runner

            result = runner.invoke(
                run_config_backtest,
                ["test_config.yaml", "--data-source", "mock"],
            )

            assert result.exit_code == 0
            assert "Running backtest from config: test_config.yaml" in result.output
            assert "Using mock data for testing" in result.output
            assert "Backtest Results" in result.output
            assert "$1500.00" in result.output  # Total return from MockBacktestResult
            assert "Strategy was profitable!" in result.output

            # Verify runner was initialized and disposed
            mock_runner_class.assert_called_once_with(data_source="mock")
            mock_runner.run_from_config_file.assert_called_once_with("test_config.yaml")
            mock_runner.dispose.assert_called_once()

    @pytest.mark.component
    def test_run_config_database_source_deprecated(self):
        """Test run-config with database source shows deprecation warning."""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create a simple config file
            with open("test_config.yaml", "w") as f:
                f.write("""
strategy:
  type: sma_crossover
  fast_period: 5
  slow_period: 10
""")

            result = runner.invoke(
                run_config_backtest,
                [
                    "test_config.yaml",
                    "--data-source",
                    "database",
                    "--symbol",
                    "AAPL",
                    "--start",
                    "2024-01-01",
                    "--end",
                    "2024-01-31",
                ],
            )

            # Should show deprecation warning
            assert (
                "Database data source deprecated" in result.output
                or "not yet implemented" in result.output
            )

    @pytest.mark.component
    def test_run_config_file_not_found(self):
        """Test run-config with non-existent config file."""
        runner = CliRunner()

        result = runner.invoke(
            run_config_backtest,
            ["nonexistent_config.yaml", "--data-source", "mock"],
        )

        # Should fail with file not found error
        assert result.exit_code != 0
        assert (
            "does not exist" in result.output.lower()
            or "not found" in result.output.lower()
        )

    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.component
    def test_run_config_validation_error(self, mock_runner_class):
        """Test run-config with invalid YAML configuration."""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create an invalid config file
            with open("invalid_config.yaml", "w") as f:
                f.write("""
invalid: yaml: structure:
  - this is wrong
""")

            # Mock runner to raise ValueError
            mock_runner = MagicMock()
            mock_runner.run_from_config_file.side_effect = ValueError(
                "Invalid configuration"
            )
            mock_runner.dispose = MagicMock()
            mock_runner_class.return_value = mock_runner

            result = runner.invoke(
                run_config_backtest,
                ["invalid_config.yaml", "--data-source", "mock"],
            )

            # Should fail with configuration error
            assert result.exit_code != 0
            assert "error" in result.output.lower() or "failed" in result.output.lower()
