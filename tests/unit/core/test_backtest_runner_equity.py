"""Unit tests for BacktestRunner equity curve extraction."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pandas as pd
import pytest

from src.core.backtest_runner import MinimalBacktestRunner


class TestExtractEquityCurve:
    """Test suite for equity curve extraction."""

    @pytest.fixture
    def backtest_runner(self):
        """Create MinimalBacktestRunner instance."""
        with patch("src.core.backtest_runner.get_settings") as mock_settings:
            mock_settings.return_value = Mock(
                default_balance=100000.0,
                catalog_path="./data/catalog",
                default_venue="SIM",
                ibkr_host="127.0.0.1",
                ibkr_port=4002,
                ibkr_client_id=1,
            )
            runner = MinimalBacktestRunner()
            return runner

    @pytest.fixture
    def mock_analyzer_with_returns(self):
        """Create mock analyzer with sample returns data."""
        analyzer = Mock()
        # Create sample returns: 1%, 2%, -0.5%, 1.5%
        dates = pd.date_range("2024-01-01", periods=4, freq="D")
        returns = pd.Series([0.01, 0.02, -0.005, 0.015], index=dates)
        analyzer.returns.return_value = returns
        return analyzer

    def test_extract_equity_curve_returns_correct_format(
        self, backtest_runner, mock_analyzer_with_returns
    ):
        """Equity curve returns list of dicts with time and value keys."""
        # Act
        result = backtest_runner._extract_equity_curve(
            mock_analyzer_with_returns, starting_balance=100000.0
        )

        # Assert
        assert isinstance(result, list)
        assert len(result) == 4
        for point in result:
            assert "time" in point
            assert "value" in point
            assert isinstance(point["time"], int)  # Unix timestamp
            assert isinstance(point["value"], float)

    def test_extract_equity_curve_calculates_correct_values(
        self, backtest_runner, mock_analyzer_with_returns
    ):
        """Equity curve calculates correct cumulative values."""
        # Arrange
        starting_balance = 100000.0

        # Expected cumulative returns:
        # Day 1: 1.01
        # Day 2: 1.01 * 1.02 = 1.0302
        # Day 3: 1.0302 * 0.995 = 1.025049
        # Day 4: 1.025049 * 1.015 = 1.04042...

        # Act
        result = backtest_runner._extract_equity_curve(
            mock_analyzer_with_returns, starting_balance=starting_balance
        )

        # Assert
        assert result[0]["value"] == pytest.approx(101000.0, rel=0.01)
        assert result[1]["value"] == pytest.approx(103020.0, rel=0.01)
        assert result[2]["value"] == pytest.approx(102504.9, rel=0.01)
        assert result[3]["value"] == pytest.approx(104042.47, rel=0.01)

    def test_extract_equity_curve_formats_dates_correctly(
        self, backtest_runner, mock_analyzer_with_returns
    ):
        """Equity curve formats dates as Unix timestamps."""
        # Act
        result = backtest_runner._extract_equity_curve(
            mock_analyzer_with_returns, starting_balance=100000.0
        )

        # Assert - Unix timestamps for 2024-01-01 to 2024-01-04 (midnight UTC)
        assert result[0]["time"] == 1704067200  # 2024-01-01
        assert result[1]["time"] == 1704153600  # 2024-01-02
        assert result[2]["time"] == 1704240000  # 2024-01-03
        assert result[3]["time"] == 1704326400  # 2024-01-04

    def test_extract_equity_curve_returns_empty_for_no_returns(self, backtest_runner):
        """Equity curve returns empty list when no returns data."""
        # Arrange
        analyzer = Mock()
        analyzer.returns.return_value = None

        # Act
        result = backtest_runner._extract_equity_curve(analyzer, starting_balance=100000.0)

        # Assert
        assert result == []

    def test_extract_equity_curve_returns_empty_for_empty_returns(self, backtest_runner):
        """Equity curve returns empty list for empty returns series."""
        # Arrange
        analyzer = Mock()
        analyzer.returns.return_value = pd.Series([])

        # Act
        result = backtest_runner._extract_equity_curve(analyzer, starting_balance=100000.0)

        # Assert
        assert result == []

    def test_extract_equity_curve_handles_exception_gracefully(self, backtest_runner):
        """Equity curve returns empty list on exception."""
        # Arrange
        analyzer = Mock()
        analyzer.returns.side_effect = Exception("Test error")

        # Act
        result = backtest_runner._extract_equity_curve(analyzer, starting_balance=100000.0)

        # Assert
        assert result == []

    def test_extract_equity_curve_rounds_values_to_two_decimals(self, backtest_runner):
        """Equity curve values are rounded to 2 decimal places."""
        # Arrange
        analyzer = Mock()
        dates = pd.date_range("2024-01-01", periods=1, freq="D")
        returns = pd.Series([0.123456789], index=dates)
        analyzer.returns.return_value = returns

        # Act
        result = backtest_runner._extract_equity_curve(analyzer, starting_balance=100000.0)

        # Assert
        # 100000 * 1.123456789 = 112345.6789, rounded to 112345.68
        assert result[0]["value"] == 112345.68


class TestPersistBacktestResultsEquityCurve:
    """Test suite for equity curve storage in persist method."""

    @pytest.fixture
    def backtest_runner_with_engine(self):
        """Create MinimalBacktestRunner with mocked engine."""
        with patch("src.core.backtest_runner.get_settings") as mock_settings:
            mock_settings.return_value = Mock(
                default_balance=100000.0,
                catalog_path="./data/catalog",
                default_venue="SIM",
                ibkr_host="127.0.0.1",
                ibkr_port=4002,
                ibkr_client_id=1,
            )
            runner = MinimalBacktestRunner()

            # Set up mock engine with analyzer
            mock_engine = Mock()
            mock_analyzer = Mock()
            dates = pd.date_range("2024-01-01", periods=3, freq="D")
            returns = pd.Series([0.01, 0.02, -0.005], index=dates)
            mock_analyzer.returns.return_value = returns
            mock_engine.portfolio.analyzer = mock_analyzer

            runner.engine = mock_engine
            return runner

    @pytest.mark.asyncio
    async def test_persist_backtest_results_includes_equity_curve(
        self, backtest_runner_with_engine
    ):
        """Persist method adds equity curve to config_snapshot."""
        # Arrange
        from src.core.backtest_runner import BacktestResult

        result = BacktestResult(
            total_return=0.025,
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
            final_balance=102500.0,
        )

        # Mock the database session and repository
        with patch("src.core.backtest_runner.get_session") as mock_get_session:
            mock_session = MagicMock()
            mock_session.__aenter__.return_value = mock_session
            mock_session.__aexit__.return_value = None
            mock_get_session.return_value = mock_session

            with patch("src.core.backtest_runner.BacktestRepository") as mock_repo_cls:
                mock_repo = Mock()
                mock_repo_cls.return_value = mock_repo

                with patch(
                    "src.core.backtest_runner.BacktestPersistenceService"
                ) as mock_service_cls:
                    mock_service = Mock()
                    mock_service.save_backtest_results = MagicMock()
                    mock_service_cls.return_value = mock_service

                    # Act
                    await backtest_runner_with_engine._persist_backtest_results(
                        result=result,
                        strategy_name="Test Strategy",
                        strategy_type="test",
                        instrument_symbol="AAPL",
                        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                        end_date=datetime(2024, 1, 3, tzinfo=timezone.utc),
                        execution_duration_seconds=10.0,
                        strategy_config={"fast_period": 10},
                    )

                    # Assert
                    call_kwargs = mock_service.save_backtest_results.call_args[1]
                    config_snapshot = call_kwargs["config_snapshot"]

                    assert "equity_curve" in config_snapshot
                    assert len(config_snapshot["equity_curve"]) == 3
                    assert (
                        config_snapshot["equity_curve"][0]["time"] == 1704067200
                    )  # 2024-01-01 Unix timestamp
