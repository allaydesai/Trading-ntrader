"""Tests for the minimal backtest runner."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.backtest_runner import MinimalBacktestRunner, BacktestResult


def test_backtest_result_creation():
    """Test BacktestResult creation and properties."""
    result = BacktestResult(
        total_return=1000.0, total_trades=10, winning_trades=6, losing_trades=4
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


@pytest.mark.trading
@pytest.mark.slow
def test_run_sma_backtest():
    """Test running an SMA backtest with mock data."""
    runner = MinimalBacktestRunner()

    # Run a small backtest
    result = runner.run_sma_backtest(
        fast_period=5, slow_period=10, trade_size=Decimal("100000"), num_bars=100
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


class TestMinimalBacktestRunnerDatabase:
    """Test cases for MinimalBacktestRunner with database integration."""

    def test_init_with_database_source(self):
        """Test initialization with database data source."""
        runner = MinimalBacktestRunner(data_source="database")

        assert runner.data_source == "database"
        assert runner.data_service is not None
        assert runner.engine is None
        assert runner._results is None

    def test_init_with_mock_source(self):
        """Test initialization with mock data source."""
        runner = MinimalBacktestRunner(data_source="mock")

        assert runner.data_source == "mock"
        assert runner.data_service is None

    @pytest.mark.asyncio
    @patch("src.core.backtest_runner.DataService")
    async def test_run_backtest_with_database_success(self, mock_data_service_class):
        """Test run_backtest_with_database with successful execution."""
        from datetime import datetime
        from decimal import Decimal

        # Mock data service
        mock_data_service = AsyncMock()
        mock_data_service.get_market_data.return_value = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            },
            {
                "timestamp": datetime(2024, 1, 1, 9, 31),
                "open": 100.75,
                "high": 101.25,
                "low": 100.50,
                "close": 101.00,
                "volume": 8500,
            },
        ]
        # convert_to_nautilus_bars is not async, so use a regular Mock
        mock_data_service.convert_to_nautilus_bars = MagicMock(
            return_value=[
                "mock_bar1",
                "mock_bar2",
            ]
        )
        mock_data_service_class.return_value = mock_data_service

        runner = MinimalBacktestRunner(data_source="database")
        runner.data_service = mock_data_service

        # Mock the backtest engine and strategy
        with patch("src.core.backtest_runner.BacktestEngine") as mock_engine_class:
            with patch("src.core.backtest_runner.SMACrossover") as mock_strategy_class:
                with patch(
                    "src.core.backtest_runner.create_test_instrument"
                ) as mock_create_instrument:
                    # Setup mocks
                    mock_engine = MagicMock()
                    mock_engine_class.return_value = mock_engine

                    mock_strategy = MagicMock()
                    mock_strategy_class.return_value = mock_strategy

                    mock_instrument = MagicMock()
                    mock_instrument_id = MagicMock()
                    mock_create_instrument.return_value = (
                        mock_instrument,
                        mock_instrument_id,
                    )

                    # Mock account summary
                    mock_account = MagicMock()
                    mock_balance = MagicMock()
                    mock_balance.as_double.return_value = 11000.0
                    mock_account.balance_total.return_value = mock_balance
                    mock_engine.cache.account_for_venue.return_value = mock_account

                    # Mock position and order summary
                    mock_engine.cache.positions_closed.return_value = []

                    start = datetime(2024, 1, 1)
                    end = datetime(2024, 1, 2)

                    result = await runner.run_backtest_with_database(
                        symbol="AAPL",
                        start=start,
                        end=end,
                        fast_period=5,
                        slow_period=10,
                        trade_size=Decimal("100000"),
                    )

                    assert isinstance(result, BacktestResult)
                    assert result.final_balance == 11000.0

                    # Verify data service was called
                    mock_data_service.get_market_data.assert_called_once_with(
                        "AAPL", start, end
                    )
                    mock_data_service.convert_to_nautilus_bars.assert_called_once()

                    # Verify engine was configured and run
                    mock_engine.add_venue.assert_called_once()
                    mock_engine.add_instrument.assert_called_once()
                    mock_engine.add_strategy.assert_called_once()
                    mock_engine.add_data.assert_called_once()
                    mock_engine.run.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.core.backtest_runner.DataService")
    async def test_run_backtest_with_database_no_data(self, mock_data_service_class):
        """Test run_backtest_with_database when no data is available."""
        from datetime import datetime
        from decimal import Decimal

        # Mock data service returning no data
        mock_data_service = AsyncMock()
        mock_data_service.get_market_data.side_effect = ValueError(
            "No market data found"
        )
        mock_data_service_class.return_value = mock_data_service

        runner = MinimalBacktestRunner(data_source="database")
        runner.data_service = mock_data_service

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        with pytest.raises(ValueError, match="No market data found"):
            await runner.run_backtest_with_database(
                symbol="AAPL",
                start=start,
                end=end,
                fast_period=5,
                slow_period=10,
                trade_size=Decimal("100000"),
            )

    @pytest.mark.asyncio
    @patch("src.core.backtest_runner.DataService")
    async def test_run_backtest_with_database_bar_conversion_failure(
        self, mock_data_service_class
    ):
        """Test run_backtest_with_database when bar conversion fails."""
        from datetime import datetime
        from decimal import Decimal

        # Mock data service
        mock_data_service = AsyncMock()
        mock_data_service.get_market_data.return_value = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]
        mock_data_service.convert_to_nautilus_bars = MagicMock(
            side_effect=ValueError("Bar conversion failed")
        )
        mock_data_service_class.return_value = mock_data_service

        runner = MinimalBacktestRunner(data_source="database")
        runner.data_service = mock_data_service

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        with pytest.raises(ValueError, match="Bar conversion failed"):
            await runner.run_backtest_with_database(
                symbol="AAPL",
                start=start,
                end=end,
                fast_period=5,
                slow_period=10,
                trade_size=Decimal("100000"),
            )

    @pytest.mark.asyncio
    @patch("src.core.backtest_runner.DataService")
    async def test_run_backtest_with_database_no_bars_created(
        self, mock_data_service_class
    ):
        """Test run_backtest_with_database when no bars are created."""
        from datetime import datetime
        from decimal import Decimal

        # Mock data service
        mock_data_service = AsyncMock()
        mock_data_service.get_market_data.return_value = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]
        mock_data_service.convert_to_nautilus_bars = MagicMock(
            return_value=[]
        )  # No bars created
        mock_data_service_class.return_value = mock_data_service

        runner = MinimalBacktestRunner(data_source="database")
        runner.data_service = mock_data_service

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        with pytest.raises(ValueError, match="No bars were created from the data"):
            await runner.run_backtest_with_database(
                symbol="AAPL",
                start=start,
                end=end,
                fast_period=5,
                slow_period=10,
                trade_size=Decimal("100000"),
            )

    @pytest.mark.asyncio
    async def test_run_backtest_with_database_without_data_service(self):
        """Test run_backtest_with_database when data service is not available."""
        from datetime import datetime
        from decimal import Decimal

        runner = MinimalBacktestRunner(data_source="mock")  # No data service

        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        with pytest.raises(
            ValueError, match="Data source must be 'database' for this method"
        ):
            await runner.run_backtest_with_database(
                symbol="AAPL",
                start=start,
                end=end,
                fast_period=5,
                slow_period=10,
                trade_size=Decimal("100000"),
            )

    @pytest.mark.asyncio
    @patch("src.core.backtest_runner.DataService")
    async def test_run_backtest_with_database_with_positions(
        self, mock_data_service_class
    ):
        """Test run_backtest_with_database that generates positions and orders."""
        from datetime import datetime
        from decimal import Decimal

        # Mock data service
        mock_data_service = AsyncMock()
        mock_data_service.get_market_data.return_value = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]
        mock_data_service.convert_to_nautilus_bars = MagicMock(
            return_value=["mock_bar"]
        )
        mock_data_service_class.return_value = mock_data_service

        runner = MinimalBacktestRunner(data_source="database")
        runner.data_service = mock_data_service

        # Mock the backtest engine with positions and orders
        with patch("src.core.backtest_runner.BacktestEngine") as mock_engine_class:
            with patch("src.core.backtest_runner.SMACrossover"):
                with patch("src.core.backtest_runner.create_test_instrument"):
                    mock_engine = MagicMock()
                    mock_engine_class.return_value = mock_engine

                    # Mock account with profit
                    mock_account = MagicMock()
                    mock_balance = MagicMock()
                    mock_balance.as_double.return_value = 12000.0
                    mock_account.balance_total.return_value = mock_balance
                    mock_engine.cache.account_for_venue.return_value = mock_account

                    # Mock positions and orders
                    mock_position = MagicMock()
                    mock_position.realized_pnl.as_double.return_value = 500.0

                    mock_engine.cache.positions_closed.return_value = [mock_position]

                    start = datetime(2024, 1, 1)
                    end = datetime(2024, 1, 2)

                    result = await runner.run_backtest_with_database(
                        symbol="AAPL",
                        start=start,
                        end=end,
                        fast_period=5,
                        slow_period=10,
                        trade_size=Decimal("100000"),
                    )

                    assert isinstance(result, BacktestResult)
                    assert result.final_balance == 12000.0
                    assert (
                        result.total_return == -988000.0
                    )  # 12000 - 1000000 starting balance
                    assert result.total_trades == 1
                    assert result.winning_trades == 1  # Positive PnL

    def test_run_sma_backtest_with_database_source(self):
        """Test run_sma_backtest falls back to mock when using database source."""
        runner = MinimalBacktestRunner(data_source="database")

        # Should fall back to mock data since run_sma_backtest doesn't use database
        result = runner.run_sma_backtest(num_bars=50)

        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 0

        # Clean up
        runner.dispose()
