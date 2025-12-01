"""
Integration tests for MinimalBacktestRunner core functionality.

These tests exercise the main backtest execution flows to maximize coverage
of the backtest_runner.py module, particularly focusing on:
- Catalog data backtests
- Strategy type backtests
- Result extraction and metrics calculation
- Database persistence
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from src.core.backtest_runner import MinimalBacktestRunner
from src.models.backtest_result import BacktestResult


@pytest.fixture
def mock_settings():
    """Create mock settings for backtest runner."""
    settings = Mock()
    settings.default_balance = 100000.0
    settings.fast_ema_period = 10
    settings.slow_ema_period = 20
    settings.portfolio_value = Decimal("100000")
    settings.position_size_pct = Decimal("10.0")
    settings.mock_data_bars = 100
    settings.commission_per_share = Decimal("0.005")
    settings.commission_min_per_order = Decimal("1.0")
    settings.commission_max_rate = Decimal("0.5")
    return settings


@pytest.fixture
def mock_bars():
    """Create mock Nautilus Trader bars for testing."""
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.objects import Price, Quantity

    # Create bar type
    InstrumentId.from_str("AAPL.NASDAQ")
    bar_type = BarType.from_str("AAPL.NASDAQ-1-MINUTE-MID-EXTERNAL")

    # Generate sample bars
    bars = []
    base_price = 150.0
    base_time = 1704067200000000000  # 2024-01-01 in nanoseconds

    for i in range(20):
        # Vary prices slightly to create realistic market movement
        price_variation = (i % 5) - 2  # -2, -1, 0, 1, 2 pattern
        price = base_price + price_variation

        bar = Bar(
            bar_type=bar_type,
            open=Price(price, precision=2),
            high=Price(price + 1, precision=2),
            low=Price(price - 1, precision=2),
            close=Price(price + 0.5, precision=2),
            volume=Quantity(1000000, precision=0),
            ts_event=base_time + (i * 60_000_000_000),  # 1 minute intervals
            ts_init=base_time + (i * 60_000_000_000),
        )
        bars.append(bar)

    return bars


@pytest.fixture
def mock_instrument():
    """Create mock Nautilus Trader instrument."""
    from nautilus_trader.test_kit.providers import TestInstrumentProvider

    return TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")


class TestBacktestRunnerCatalogIntegration:
    """Integration tests for catalog-based backtest execution."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_run_backtest_with_catalog_data_sma_strategy(
        self, mock_settings, mock_bars, mock_instrument
    ):
        """Test full backtest execution with catalog data and SMA strategy."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            # Mock database session and persistence
            with patch("src.core.backtest_runner.get_session") as mock_get_session:
                mock_session = AsyncMock()
                mock_session.__aenter__.return_value = mock_session
                mock_session.__aexit__.return_value = None
                mock_get_session.return_value = mock_session

                with patch("src.core.backtest_runner.BacktestRepository"):
                    with patch(
                        "src.core.backtest_runner.BacktestPersistenceService"
                    ) as mock_service_cls:
                        mock_service = AsyncMock()
                        mock_service.save_backtest_results = AsyncMock(
                            return_value=Mock(id=uuid4())
                        )
                        mock_service.save_trades_from_positions = AsyncMock(return_value=5)
                        mock_service_cls.return_value = mock_service

                        # Arrange
                        runner = MinimalBacktestRunner(data_source="catalog")

                        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
                        end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)

                        # Act
                        result, run_id = await runner.run_backtest_with_catalog_data(
                            bars=mock_bars,
                            strategy_type="sma_crossover",
                            symbol="AAPL.NASDAQ",
                            start=start_date,
                            end=end_date,
                            instrument=mock_instrument,
                            fast_period=10,
                            slow_period=20,
                        )

                        # Assert - Verify result structure
                        assert result is not None
                        assert isinstance(result, BacktestResult)
                        assert hasattr(result, "total_return")
                        assert hasattr(result, "total_trades")
                        assert hasattr(result, "final_balance")

                        # Verify result values are valid
                        assert isinstance(result.total_return, (float, int))
                        assert isinstance(result.total_trades, int)
                        assert result.total_trades >= 0
                        assert isinstance(result.final_balance, (float, int))
                        assert result.final_balance > 0

                        # Verify persistence was called
                        assert mock_service.save_backtest_results.called
                        assert run_id is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_run_backtest_with_catalog_data_calculates_metrics(
        self, mock_settings, mock_bars, mock_instrument
    ):
        """Test that catalog backtest calculates all performance metrics."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            # Mock database session
            with patch("src.core.backtest_runner.get_session") as mock_get_session:
                mock_session = AsyncMock()
                mock_session.__aenter__.return_value = mock_session
                mock_session.__aexit__.return_value = None
                mock_get_session.return_value = mock_session

                with patch("src.core.backtest_runner.BacktestRepository"):
                    with patch(
                        "src.core.backtest_runner.BacktestPersistenceService"
                    ) as mock_service_cls:
                        mock_service = AsyncMock()
                        mock_service.save_backtest_results = AsyncMock(
                            return_value=Mock(id=uuid4())
                        )
                        mock_service.save_trades_from_positions = AsyncMock(return_value=0)
                        mock_service_cls.return_value = mock_service

                        # Arrange
                        runner = MinimalBacktestRunner(data_source="catalog")

                        # Act
                        result, _ = await runner.run_backtest_with_catalog_data(
                            bars=mock_bars,
                            strategy_type="sma_crossover",
                            symbol="AAPL.NASDAQ",
                            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
                            instrument=mock_instrument,
                        )

                        # Assert - Check that metrics are calculated
                        # These may be None if no trades were made, but should be present
                        assert hasattr(result, "sharpe_ratio")
                        assert hasattr(result, "max_drawdown")
                        assert hasattr(result, "cagr")
                        assert hasattr(result, "calmar_ratio")
                        assert hasattr(result, "win_rate")

                        # Winning and losing trades should sum to total trades
                        assert result.winning_trades + result.losing_trades == result.total_trades

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_run_backtest_with_catalog_data_with_custom_params(
        self, mock_settings, mock_bars, mock_instrument
    ):
        """Test catalog backtest with custom strategy parameters."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            # Mock database session
            with patch("src.core.backtest_runner.get_session") as mock_get_session:
                mock_session = AsyncMock()
                mock_session.__aenter__.return_value = mock_session
                mock_session.__aexit__.return_value = None
                mock_get_session.return_value = mock_session

                with patch("src.core.backtest_runner.BacktestRepository"):
                    with patch(
                        "src.core.backtest_runner.BacktestPersistenceService"
                    ) as mock_service_cls:
                        mock_service = AsyncMock()
                        mock_service.save_backtest_results = AsyncMock(
                            return_value=Mock(id=uuid4())
                        )
                        mock_service.save_trades_from_positions = AsyncMock(return_value=0)
                        mock_service_cls.return_value = mock_service

                        # Arrange
                        runner = MinimalBacktestRunner(data_source="catalog")

                        # Act - Test with custom parameters
                        result, run_id = await runner.run_backtest_with_catalog_data(
                            bars=mock_bars,
                            strategy_type="sma_crossover",
                            symbol="AAPL.NASDAQ",
                            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                            end=datetime(2024, 1, 31, tzinfo=timezone.utc),
                            instrument=mock_instrument,
                            fast_period=5,
                            slow_period=15,
                        )

                        # Assert
                        assert result is not None
                        assert run_id is not None
                        assert isinstance(result.total_trades, int)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_run_backtest_with_catalog_data_error_handling(
        self, mock_settings, mock_instrument
    ):
        """Test catalog backtest handles errors gracefully."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            # Mock database session for failed backtest persistence
            with patch("src.core.backtest_runner.get_session") as mock_get_session:
                mock_session = AsyncMock()
                mock_session.__aenter__.return_value = mock_session
                mock_session.__aexit__.return_value = None
                mock_get_session.return_value = mock_session

                with patch("src.core.backtest_runner.BacktestRepository"):
                    with patch(
                        "src.core.backtest_runner.BacktestPersistenceService"
                    ) as mock_service_cls:
                        mock_service = AsyncMock()
                        mock_service.save_failed_backtest = AsyncMock(return_value=uuid4())
                        mock_service_cls.return_value = mock_service

                        runner = MinimalBacktestRunner(data_source="catalog")

                        # Test with empty bars list - should raise ValueError
                        with pytest.raises(ValueError, match="No bars provided"):
                            await runner.run_backtest_with_catalog_data(
                                bars=[],
                                strategy_type="sma_crossover",
                                symbol="AAPL.NASDAQ",
                                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                                end=datetime(2024, 1, 31, tzinfo=timezone.utc),
                                instrument=mock_instrument,
                            )

                        # Test with invalid strategy type
                        with pytest.raises(ValueError, match="Unsupported strategy type"):
                            await runner.run_backtest_with_catalog_data(
                                bars=[Mock()],  # Non-empty but will fail before using
                                strategy_type="invalid_strategy",
                                symbol="AAPL.NASDAQ",
                                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                                end=datetime(2024, 1, 31, tzinfo=timezone.utc),
                                instrument=mock_instrument,
                            )


class TestBacktestRunnerMetricsCalculation:
    """Tests for backtest metrics calculation methods."""

    @pytest.mark.integration
    def test_calculate_cagr_with_valid_inputs(self, mock_settings):
        """Test CAGR calculation with valid date range and balances."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            runner = MinimalBacktestRunner(data_source="mock")

            # 2 years, 100k -> 120k = 9.54% CAGR
            start_date = datetime(2022, 1, 1)
            end_date = datetime(2024, 1, 1)

            cagr = runner._calculate_cagr(
                starting_balance=100000.0,
                final_balance=120000.0,
                start_date=start_date,
                end_date=end_date,
            )

            assert cagr is not None
            assert isinstance(cagr, float)
            # CAGR should be approximately 9.54%
            assert 0.09 < cagr < 0.11

    @pytest.mark.integration
    def test_calculate_cagr_with_loss(self, mock_settings):
        """Test CAGR calculation with negative returns."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            runner = MinimalBacktestRunner(data_source="mock")

            start_date = datetime(2022, 1, 1)
            end_date = datetime(2024, 1, 1)

            # Lost 20% over 2 years
            cagr = runner._calculate_cagr(
                starting_balance=100000.0,
                final_balance=80000.0,
                start_date=start_date,
                end_date=end_date,
            )

            assert cagr is not None
            assert cagr < 0  # Should be negative

    @pytest.mark.integration
    def test_calculate_cagr_edge_cases(self, mock_settings):
        """Test CAGR calculation edge cases."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            runner = MinimalBacktestRunner(data_source="mock")

            start_date = datetime(2022, 1, 1)
            end_date = datetime(2024, 1, 1)

            # Zero starting balance
            cagr = runner._calculate_cagr(
                starting_balance=0.0,
                final_balance=100000.0,
                start_date=start_date,
                end_date=end_date,
            )
            assert cagr is None

            # Negative final balance
            cagr = runner._calculate_cagr(
                starting_balance=100000.0,
                final_balance=-10000.0,
                start_date=start_date,
                end_date=end_date,
            )
            assert cagr is None

            # Same start and end date
            cagr = runner._calculate_cagr(
                starting_balance=100000.0,
                final_balance=110000.0,
                start_date=start_date,
                end_date=start_date,
            )
            assert cagr is None

    @pytest.mark.integration
    def test_calculate_calmar_ratio(self, mock_settings):
        """Test Calmar ratio calculation."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            runner = MinimalBacktestRunner(data_source="mock")

            # CAGR of 20%, max drawdown of 10%
            calmar = runner._calculate_calmar_ratio(cagr=0.20, max_drawdown=-0.10)

            assert calmar is not None
            assert isinstance(calmar, float)
            assert calmar == pytest.approx(2.0)  # 0.20 / 0.10 = 2.0

    @pytest.mark.integration
    def test_calculate_calmar_ratio_edge_cases(self, mock_settings):
        """Test Calmar ratio calculation edge cases."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            runner = MinimalBacktestRunner(data_source="mock")

            # None inputs
            assert runner._calculate_calmar_ratio(cagr=None, max_drawdown=-0.10) is None
            assert runner._calculate_calmar_ratio(cagr=0.20, max_drawdown=None) is None

            # Zero drawdown
            assert runner._calculate_calmar_ratio(cagr=0.20, max_drawdown=0.0) is None


class TestBacktestRunnerResetAndDispose:
    """Tests for backtest runner lifecycle methods."""

    @pytest.mark.integration
    def test_reset_clears_state(self, mock_settings):
        """Test that reset clears all backtest state."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            runner = MinimalBacktestRunner(data_source="mock")

            # Run a backtest to populate state
            result = runner.run_sma_backtest(fast_period=10, slow_period=20)
            assert result is not None
            assert runner.engine is not None
            assert runner._results is not None

            # Reset
            runner.reset()

            # Verify state is cleared
            assert runner._results is None
            assert runner._venue is None
            assert runner._backtest_start_date is None
            assert runner._backtest_end_date is None

    @pytest.mark.integration
    def test_dispose_cleans_up_engine(self, mock_settings):
        """Test that dispose cleans up engine resources."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            runner = MinimalBacktestRunner(data_source="mock")

            # Run a backtest
            runner.run_sma_backtest(fast_period=10, slow_period=20)
            assert runner.engine is not None

            # Dispose
            runner.dispose()

            # Verify results are cleared
            assert runner._results is None

    @pytest.mark.integration
    def test_get_detailed_results(self, mock_settings):
        """Test detailed results extraction."""
        with patch("src.core.backtest_runner.get_settings", return_value=mock_settings):
            runner = MinimalBacktestRunner(data_source="mock")

            # Run backtest
            runner.run_sma_backtest(fast_period=10, slow_period=20)

            # Get detailed results
            detailed = runner.get_detailed_results()

            # Verify structure
            assert isinstance(detailed, dict)
            assert "basic_metrics" in detailed
            assert "account_summary" in detailed
            assert "positions" in detailed
            assert "orders" in detailed

            # Verify content
            assert "total_return" in detailed["basic_metrics"]
            assert "total_trades" in detailed["basic_metrics"]
            assert "starting_balance" in detailed["account_summary"]
            assert "final_balance" in detailed["account_summary"]
