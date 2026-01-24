"""Tests for backtest CLI commands."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands.backtest import (
    backtest,
    list_backtests,
    run_backtest,
    run_config_backtest,
)


class MockBacktestResult:
    """Mock backtest result for testing."""

    def __init__(self):
        # total_return is stored as percentage (0.15 = 15%), not dollar amount
        self.total_return = Decimal("0.15")  # 15% return
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

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @pytest.mark.component
    def test_run_backtest_success(self, mock_orchestrator_class, mock_catalog_service_class):
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
        mock_catalog_service.load_instrument.return_value = MagicMock()  # Mock instrument
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest orchestrator
        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return MockBacktestResult(), None  # Returns (result, run_id)

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

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
        assert "Data available in catalog" in result.output or "Loaded" in result.output
        assert "Backtest Results" in result.output
        assert "15.00%" in result.output  # Total return (15% as percentage)
        assert "Strategy was profitable!" in result.output

        # Verify catalog service was called
        mock_catalog_service.get_availability.assert_called_once()
        mock_orchestrator.dispose.assert_called_once()

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @pytest.mark.component
    def test_run_backtest_data_not_found(self, mock_catalog_service_class):
        """Test backtest when data is not found."""
        from src.services.exceptions import DataNotFoundError

        # Mock catalog service to raise DataNotFoundError
        mock_catalog_service = MagicMock()
        mock_catalog_service.get_availability.return_value = None

        start_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_dt = datetime(2024, 1, 31, tzinfo=timezone.utc)

        async def mock_fetch_or_load(*args, **kwargs):
            raise DataNotFoundError("INVALID.NASDAQ", start_dt, end_dt)

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "INVALID", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert "No data" in result.output or "not found" in result.output.lower()

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
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

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @pytest.mark.component
    def test_run_backtest_losing_strategy(
        self, mock_orchestrator_class, mock_catalog_service_class
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

        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return (mock_result, None)

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 0
        assert "Strategy lost money" in result.output

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @pytest.mark.component
    def test_run_backtest_break_even_strategy(
        self, mock_orchestrator_class, mock_catalog_service_class
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

        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return (mock_result, None)

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code == 0
        assert "Strategy broke even" in result.output

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @pytest.mark.component
    def test_run_backtest_value_error(self, mock_orchestrator_class, mock_catalog_service_class):
        """Test backtest when ValueError occurs."""
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

        # Mock orchestrator to raise ValueError
        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            raise ValueError("Invalid parameters")

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert "Backtest failed" in result.output or "Invalid parameters" in result.output

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @pytest.mark.component
    def test_run_backtest_unexpected_error(
        self, mock_orchestrator_class, mock_catalog_service_class
    ):
        """Test backtest when unexpected error occurs."""
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

        # Mock orchestrator to raise unexpected error
        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            raise Exception("Unexpected failure")

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert "Unexpected error" in result.output or "failed" in result.output.lower()

    @pytest.mark.component
    def test_run_backtest_required_parameters(self):
        """Test that run command requires symbol, start, and end parameters in CLI mode."""
        runner = CliRunner()

        # Test missing symbol
        result = runner.invoke(run_backtest, ["--start", "2024-01-01", "--end", "2024-01-31"])
        assert result.exit_code == 2
        assert "Missing required option" in result.output or "--symbol" in result.output

        # Test missing start
        result = runner.invoke(run_backtest, ["--symbol", "AAPL", "--end", "2024-01-31"])
        assert result.exit_code == 2
        assert "Missing required option" in result.output or "--start" in result.output

        # Test missing end
        result = runner.invoke(run_backtest, ["--symbol", "AAPL", "--start", "2024-01-01"])
        assert result.exit_code == 2
        assert "Missing required option" in result.output or "--end" in result.output

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @patch("src.cli.commands._backtest_helpers.BacktestRequest")
    @pytest.mark.component
    def test_run_backtest_default_parameters(
        self, mock_request_class, mock_orchestrator_class, mock_catalog_service_class
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

        # Mock BacktestRequest to capture from_cli_args call
        mock_request = MagicMock()
        mock_request.symbol = "AAPL"
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_request.bar_type = "1-DAY-LAST"
        mock_request.starting_balance = Decimal("10000000")
        mock_request.strategy_type = "sma_crossover"
        mock_request.strategy_config = {"fast_period": 10, "slow_period": 20}
        mock_request_class.from_cli_args.return_value = mock_request

        # Mock orchestrator
        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return (MockBacktestResult(), None)

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

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

        # Check that from_cli_args was called with default values
        call_kwargs = mock_request_class.from_cli_args.call_args.kwargs
        assert call_kwargs["fast_period"] == 10  # default
        assert call_kwargs["slow_period"] == 20  # default
        # SMA uses portfolio_value and position_size_pct, not trade_size
        assert call_kwargs["portfolio_value"] == Decimal("10000000")  # trade_size * 10
        assert call_kwargs["position_size_pct"] == Decimal("10.0")

    @pytest.mark.component
    def test_list_backtests_command_exists(self):
        """Test that list command exists."""
        runner = CliRunner()
        result = runner.invoke(list_backtests, ["--help"])
        assert result.exit_code == 0

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
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

        mock_catalog_service.availability_cache = {"AAPL.NASDAQ-1-MINUTE-LAST": mock_availability}
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
        assert "Could not fetch data info" in result.output or "error" in result.output.lower()


class TestRunConfigBacktest:
    """Test cases for run-config backtest command."""

    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @patch("src.cli.commands._backtest_helpers.generate_mock_data_from_yaml")
    @pytest.mark.component
    def test_run_config_success_mock_data(self, mock_generate_data, mock_orchestrator_class):
        """Test successful run-config with mock data source."""
        from datetime import datetime, timezone

        # Create a temporary config file
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create config file with structure expected by BacktestRequest.from_yaml_config
            with open("test_config.yaml", "w") as f:
                f.write("""
strategy_path: "src.core.strategies.sma_crossover:SMACrossover"
config_path: "src.core.strategies.sma_crossover:SMACrossoverConfig"
config:
  instrument_id: "AAPL.NASDAQ"
  bar_type: "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"
  fast_period: 5
  slow_period: 10
backtest:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  initial_capital: 100000
""")

            # Mock generate_mock_data_from_yaml
            mock_bars = MagicMock()
            mock_instrument = MagicMock()
            start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
            mock_generate_data.return_value = (
                [mock_bars] * 100,  # 100 mock bars
                mock_instrument,
                start_date,
                end_date,
            )

            # Mock orchestrator
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=(MockBacktestResult(), None))
            mock_orchestrator.dispose = MagicMock()
            mock_orchestrator_class.return_value = mock_orchestrator

            result = runner.invoke(
                run_config_backtest,
                ["test_config.yaml", "--data-source", "mock", "--no-persist"],
            )

            assert result.exit_code == 0, f"Exit code was {result.exit_code}: {result.output}"
            assert "Running backtest from config: test_config.yaml" in result.output
            assert "Using mock data for testing" in result.output
            assert "Generated 100 mock bars" in result.output
            assert "Backtest Results" in result.output
            assert "15.00%" in result.output  # Total return (15% as percentage)
            assert "Strategy was profitable!" in result.output

            # Verify orchestrator was used
            mock_generate_data.assert_called_once()
            mock_orchestrator.execute.assert_called_once()
            mock_orchestrator.dispose.assert_called_once()

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
        assert "does not exist" in result.output.lower() or "not found" in result.output.lower()

    @patch("src.core.backtest_runner.MinimalBacktestRunner")
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
            mock_runner.run_from_config_file.side_effect = ValueError("Invalid configuration")
            mock_runner.dispose = MagicMock()
            mock_runner_class.return_value = mock_runner

            result = runner.invoke(
                run_config_backtest,
                ["invalid_config.yaml", "--data-source", "mock"],
            )

            # Should fail with configuration error
            assert result.exit_code != 0
            assert "error" in result.output.lower() or "failed" in result.output.lower()


class TestPersistFlag:
    """Test cases for --persist/--no-persist flag behavior."""

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @patch("src.cli.commands._backtest_helpers.BacktestRequest")
    @pytest.mark.component
    def test_run_backtest_with_persist_flag(
        self, mock_request_class, mock_orchestrator_class, mock_catalog_service_class
    ):
        """Test that --persist flag is passed to BacktestRequest."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000
        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock request
        mock_request = MagicMock()
        mock_request.symbol = "AAPL"
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_request.bar_type = "1-DAY-LAST"
        mock_request.starting_balance = Decimal("10000000")
        mock_request.strategy_type = "sma_crossover"
        mock_request.strategy_config = {"fast_period": 10, "slow_period": 20}
        mock_request_class.from_cli_args.return_value = mock_request

        # Mock orchestrator
        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return (MockBacktestResult(), "test-run-id-123")

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

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
                "--persist",
            ],
        )

        assert result.exit_code == 0
        # Verify persist=True was passed
        call_kwargs = mock_request_class.from_cli_args.call_args.kwargs
        assert call_kwargs["persist"] is True
        # Verify "Persisted" appears in output
        assert "Persisted" in result.output

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @patch("src.cli.commands._backtest_helpers.BacktestRequest")
    @pytest.mark.component
    def test_run_backtest_with_no_persist_flag(
        self, mock_request_class, mock_orchestrator_class, mock_catalog_service_class
    ):
        """Test that --no-persist flag prevents database persistence."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000
        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock request
        mock_request = MagicMock()
        mock_request.symbol = "AAPL"
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_request.bar_type = "1-DAY-LAST"
        mock_request.starting_balance = Decimal("10000000")
        mock_request.strategy_type = "sma_crossover"
        mock_request.strategy_config = {"fast_period": 10, "slow_period": 20}
        mock_request_class.from_cli_args.return_value = mock_request

        # Mock orchestrator
        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return (MockBacktestResult(), None)  # None run_id when not persisted

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

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
                "--no-persist",
            ],
        )

        assert result.exit_code == 0
        # Verify persist=False was passed
        call_kwargs = mock_request_class.from_cli_args.call_args.kwargs
        assert call_kwargs["persist"] is False
        # Verify output shows not persisted
        assert "--no-persist" in result.output

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @patch("src.cli.commands._backtest_helpers.BacktestRequest")
    @pytest.mark.component
    def test_run_backtest_persist_default_true(
        self, mock_request_class, mock_orchestrator_class, mock_catalog_service_class
    ):
        """Test that persist defaults to True when flag is not specified."""
        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000
        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock request
        mock_request = MagicMock()
        mock_request.symbol = "AAPL"
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_request.bar_type = "1-DAY-LAST"
        mock_request.starting_balance = Decimal("10000000")
        mock_request.strategy_type = "sma_crossover"
        mock_request.strategy_config = {"fast_period": 10, "slow_period": 20}
        mock_request_class.from_cli_args.return_value = mock_request

        # Mock orchestrator
        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return (MockBacktestResult(), "test-run-id")

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

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
                # No persist flag - should default to True
            ],
        )

        assert result.exit_code == 0
        # Verify persist defaults to True
        call_kwargs = mock_request_class.from_cli_args.call_args.kwargs
        assert call_kwargs["persist"] is True


class TestRunBacktestConfigMode:
    """Test cases for the unified run_backtest command with config file support."""

    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @patch("src.cli.commands._backtest_helpers.generate_mock_data_from_yaml")
    @pytest.mark.component
    def test_run_with_yaml_config(self, mock_generate_data, mock_orchestrator_class):
        """Test running backtest with YAML config file (config mode)."""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create config file
            with open("test_config.yaml", "w") as f:
                f.write("""
strategy_path: "src.core.strategies.sma_crossover:SMACrossover"
config_path: "src.core.strategies.sma_crossover:SMACrossoverConfig"
config:
  instrument_id: "AAPL.NASDAQ"
  bar_type: "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"
  fast_period: 5
  slow_period: 10
backtest:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  initial_capital: 100000
""")

            # Mock generate_mock_data_from_yaml
            mock_bars = MagicMock()
            mock_instrument = MagicMock()
            start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
            mock_generate_data.return_value = (
                [mock_bars] * 100,
                mock_instrument,
                start_date,
                end_date,
            )

            # Mock orchestrator
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=(MockBacktestResult(), None))
            mock_orchestrator.dispose = MagicMock()
            mock_orchestrator_class.return_value = mock_orchestrator

            result = runner.invoke(
                run_backtest,
                ["test_config.yaml", "--data-source", "mock", "--no-persist"],
            )

            assert result.exit_code == 0, f"Exit code was {result.exit_code}: {result.output}"
            assert "Running backtest from config" in result.output
            assert "test_config.yaml" in result.output
            assert "Backtest Results" in result.output
            assert "Strategy was profitable!" in result.output

    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @patch("src.cli.commands._backtest_helpers.generate_mock_data_from_yaml")
    @pytest.mark.component
    def test_run_with_yaml_and_date_override(self, mock_generate_data, mock_orchestrator_class):
        """Test running backtest with YAML config and date override."""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create config file with dates
            with open("test_config.yaml", "w") as f:
                f.write("""
strategy_path: "src.core.strategies.apolo_rsi:ApoloRSI"
config_path: "src.core.strategies.apolo_rsi:ApoloRSIConfig"
config:
  instrument_id: "AMD.NASDAQ"
  bar_type: "AMD.NASDAQ-1-DAY-LAST-EXTERNAL"
  trade_size: 100
backtest:
  start_date: "2024-01-01"
  end_date: "2024-12-31"
  initial_capital: 100000
""")

            # Mock generate_mock_data_from_yaml
            mock_bars = MagicMock()
            mock_instrument = MagicMock()
            start_date = datetime(2024, 6, 1, tzinfo=timezone.utc)
            end_date = datetime(2024, 6, 30, tzinfo=timezone.utc)
            mock_generate_data.return_value = (
                [mock_bars] * 30,
                mock_instrument,
                start_date,
                end_date,
            )

            # Mock orchestrator
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=(MockBacktestResult(), None))
            mock_orchestrator.dispose = MagicMock()
            mock_orchestrator_class.return_value = mock_orchestrator

            # Run with date override
            result = runner.invoke(
                run_backtest,
                [
                    "test_config.yaml",
                    "--start",
                    "2024-06-01",
                    "--end",
                    "2024-06-30",
                    "--data-source",
                    "mock",
                    "--no-persist",
                ],
            )

            assert result.exit_code == 0, f"Exit code was {result.exit_code}: {result.output}"
            # Verify the overridden dates are shown
            assert "2024-06-01" in result.output
            assert "2024-06-30" in result.output

    @pytest.mark.component
    def test_run_cli_mode_requires_symbol(self):
        """Test that CLI mode requires --symbol option."""
        runner = CliRunner()

        result = runner.invoke(
            run_backtest,
            ["--start", "2024-01-01", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert "Missing required option" in result.output or "--symbol" in result.output

    @pytest.mark.component
    def test_run_cli_mode_requires_start(self):
        """Test that CLI mode requires --start option."""
        runner = CliRunner()

        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--end", "2024-01-31"],
        )

        assert result.exit_code != 0
        assert "Missing required option" in result.output or "--start" in result.output

    @pytest.mark.component
    def test_run_cli_mode_requires_end(self):
        """Test that CLI mode requires --end option."""
        runner = CliRunner()

        result = runner.invoke(
            run_backtest,
            ["--symbol", "AAPL", "--start", "2024-01-01"],
        )

        assert result.exit_code != 0
        assert "Missing required option" in result.output or "--end" in result.output

    @patch("src.cli.commands._backtest_helpers.DataCatalogService")
    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @pytest.mark.component
    def test_run_backward_compatibility_cli_mode(
        self, mock_orchestrator_class, mock_catalog_service_class
    ):
        """Test that existing CLI mode behavior is preserved (backward compatibility)."""
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

        # Mock backtest orchestrator
        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return MockBacktestResult(), None

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()
        mock_orchestrator_class.return_value = mock_orchestrator

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
        assert "SMA_CROSSOVER" in result.output.upper() or "AAPL" in result.output
        assert "Backtest Results" in result.output
        assert "Strategy was profitable!" in result.output

    @pytest.mark.component
    def test_run_mock_source_requires_config_file(self):
        """Test that mock data source requires a config file."""
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
                "--data-source",
                "mock",
            ],
        )

        assert result.exit_code != 0
        assert "Mock data source requires a YAML config" in result.output

    @pytest.mark.component
    def test_run_config_file_not_found(self):
        """Test error when config file does not exist."""
        runner = CliRunner()

        result = runner.invoke(
            run_backtest,
            ["nonexistent_config.yaml"],
        )

        assert result.exit_code != 0
        assert "does not exist" in result.output.lower() or "not found" in result.output.lower()

    @patch("src.cli.commands._backtest_helpers.BacktestOrchestrator")
    @patch("src.cli.commands._backtest_helpers.generate_mock_data_from_yaml")
    @pytest.mark.component
    def test_run_with_yaml_and_starting_balance_override(
        self, mock_generate_data, mock_orchestrator_class
    ):
        """Test running backtest with YAML config and starting balance override."""
        runner = CliRunner()

        with runner.isolated_filesystem():
            # Create config file
            with open("test_config.yaml", "w") as f:
                f.write("""
strategy_path: "src.core.strategies.apolo_rsi:ApoloRSI"
config_path: "src.core.strategies.apolo_rsi:ApoloRSIConfig"
config:
  instrument_id: "AMD.NASDAQ"
  bar_type: "AMD.NASDAQ-1-DAY-LAST-EXTERNAL"
backtest:
  start_date: "2024-01-01"
  end_date: "2024-06-30"
  initial_capital: 100000
""")

            # Mock generate_mock_data_from_yaml
            mock_bars = MagicMock()
            mock_instrument = MagicMock()
            start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end_date = datetime(2024, 6, 30, tzinfo=timezone.utc)
            mock_generate_data.return_value = (
                [mock_bars] * 100,
                mock_instrument,
                start_date,
                end_date,
            )

            # Mock orchestrator
            mock_orchestrator = MagicMock()
            mock_orchestrator.execute = AsyncMock(return_value=(MockBacktestResult(), None))
            mock_orchestrator.dispose = MagicMock()
            mock_orchestrator_class.return_value = mock_orchestrator

            result = runner.invoke(
                run_backtest,
                [
                    "test_config.yaml",
                    "--starting-balance",
                    "500000",
                    "--data-source",
                    "mock",
                    "--no-persist",
                ],
            )

            assert result.exit_code == 0, f"Exit code was {result.exit_code}: {result.output}"
            # Verify the overridden balance is shown
            assert "$500,000" in result.output
