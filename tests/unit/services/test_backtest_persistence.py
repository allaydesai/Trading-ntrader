"""Unit tests for BacktestPersistenceService."""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock, AsyncMock

from src.services.backtest_persistence import BacktestPersistenceService
from src.core.backtest_runner import BacktestResult
from src.db.exceptions import (
    ValidationError,
    DuplicateRecordError,
)


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    repo = Mock()
    repo.create_backtest_run = AsyncMock()
    repo.create_performance_metrics = AsyncMock()
    return repo


@pytest.fixture
def persistence_service(mock_repository):
    """Create persistence service with mock repository."""
    return BacktestPersistenceService(mock_repository)


@pytest.fixture
def sample_backtest_result():
    """Create sample backtest result."""
    return BacktestResult(
        total_return=25000.00,
        total_trades=100,
        winning_trades=60,
        losing_trades=40,
        largest_win=1500.00,
        largest_loss=-800.00,
        final_balance=125000.00,
    )


@pytest.fixture
def sample_config_snapshot():
    """Create sample configuration snapshot."""
    return {
        "strategy_path": "src.core.strategies.sma_crossover.SMACrossover",
        "config_path": "config/strategies/sma_crossover.yaml",
        "version": "1.0",
        "config": {
            "fast_period": 10,
            "slow_period": 50,
            "trade_size": 1000000,
        },
    }


class TestBacktestPersistenceServiceSaveSuccess:
    """Test suite for successful backtest persistence."""

    @pytest.mark.asyncio
    async def test_save_backtest_results_creates_run_and_metrics(
        self,
        persistence_service,
        mock_repository,
        sample_backtest_result,
        sample_config_snapshot,
    ):
        """Test successful save of backtest results."""
        # Arrange
        run_id = uuid4()
        mock_run = Mock()
        mock_run.id = 1
        mock_run.run_id = run_id
        mock_repository.create_backtest_run.return_value = mock_run

        mock_metrics = Mock()
        mock_metrics.id = 1
        mock_repository.create_performance_metrics.return_value = mock_metrics

        # Act
        await persistence_service.save_backtest_results(
            run_id=run_id,
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_duration_seconds=Decimal("45.237"),
            config_snapshot=sample_config_snapshot,
            backtest_result=sample_backtest_result,
        )

        # Assert
        mock_repository.create_backtest_run.assert_called_once()
        mock_repository.create_performance_metrics.assert_called_once()

        # Verify backtest run was created with correct status
        call_kwargs = mock_repository.create_backtest_run.call_args.kwargs
        assert call_kwargs["run_id"] == run_id
        assert call_kwargs["strategy_name"] == "SMA Crossover"
        assert call_kwargs["execution_status"] == "success"
        assert call_kwargs["error_message"] is None

    @pytest.mark.asyncio
    async def test_save_failed_backtest_with_error_message(
        self, persistence_service, mock_repository, sample_config_snapshot
    ):
        """Test saving failed backtest with error message."""
        # Arrange
        run_id = uuid4()
        error_message = "Division by zero in strategy calculation"
        mock_run = Mock()
        mock_run.id = 1
        mock_repository.create_backtest_run.return_value = mock_run

        # Act
        result = await persistence_service.save_failed_backtest(
            run_id=run_id,
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_duration_seconds=Decimal("10.5"),
            config_snapshot=sample_config_snapshot,
            error_message=error_message,
        )

        # Assert
        assert result == mock_run
        mock_repository.create_backtest_run.assert_called_once()
        mock_repository.create_performance_metrics.assert_not_called()

        # Verify failed status and error message
        call_kwargs = mock_repository.create_backtest_run.call_args.kwargs
        assert call_kwargs["execution_status"] == "failed"
        assert call_kwargs["error_message"] == error_message

    @pytest.mark.asyncio
    async def test_save_results_with_reproduced_from_run_id(
        self,
        persistence_service,
        mock_repository,
        sample_backtest_result,
        sample_config_snapshot,
    ):
        """Test saving backtest that reproduces another run."""
        # Arrange
        run_id = uuid4()
        original_run_id = uuid4()
        mock_run = Mock()
        mock_run.id = 1
        mock_repository.create_backtest_run.return_value = mock_run
        mock_repository.create_performance_metrics.return_value = Mock()

        # Act
        await persistence_service.save_backtest_results(
            run_id=run_id,
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            instrument_symbol="AAPL",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_duration_seconds=Decimal("45.0"),
            config_snapshot=sample_config_snapshot,
            backtest_result=sample_backtest_result,
            reproduced_from_run_id=original_run_id,
        )

        # Assert
        call_kwargs = mock_repository.create_backtest_run.call_args.kwargs
        assert call_kwargs["reproduced_from_run_id"] == original_run_id


class TestBacktestPersistenceServiceValidation:
    """Test suite for metric validation."""

    @pytest.mark.asyncio
    async def test_rejects_nan_metrics(
        self, persistence_service, sample_config_snapshot
    ):
        """Test that NaN metrics are rejected with validation error."""
        # Arrange
        result_with_nan = BacktestResult(
            total_return=float("nan"),  # Invalid
            total_trades=10,
            winning_trades=5,
            losing_trades=5,
            largest_win=100.0,
            largest_loss=-50.0,
            final_balance=100000.0,
        )

        # Act & Assert
        with pytest.raises(ValidationError, match="Invalid metric value"):
            await persistence_service.save_backtest_results(
                run_id=uuid4(),
                strategy_name="Test",
                strategy_type="test",
                instrument_symbol="TEST",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="Mock",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot=sample_config_snapshot,
                backtest_result=result_with_nan,
            )

    @pytest.mark.asyncio
    async def test_rejects_infinite_metrics(
        self, persistence_service, sample_config_snapshot
    ):
        """Test that infinite metrics are rejected with validation error."""
        # Arrange
        result_with_inf = BacktestResult(
            total_return=float("inf"),  # Invalid
            total_trades=10,
            winning_trades=5,
            losing_trades=5,
            largest_win=100.0,
            largest_loss=-50.0,
            final_balance=100000.0,
        )

        # Act & Assert
        with pytest.raises(ValidationError, match="Invalid metric value"):
            await persistence_service.save_backtest_results(
                run_id=uuid4(),
                strategy_name="Test",
                strategy_type="test",
                instrument_symbol="TEST",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="Mock",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot=sample_config_snapshot,
                backtest_result=result_with_inf,
            )

    @pytest.mark.asyncio
    async def test_validates_config_snapshot_structure(
        self, persistence_service, sample_backtest_result
    ):
        """Test that invalid config snapshot structure is rejected."""
        # Arrange
        invalid_config = {
            "strategy_path": "",  # Invalid: empty string
            "config_path": "test.yaml",
            "version": "1.0",
        }

        # Act & Assert
        with pytest.raises(ValidationError, match="strategy_path"):
            await persistence_service.save_backtest_results(
                run_id=uuid4(),
                strategy_name="Test",
                strategy_type="test",
                instrument_symbol="TEST",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="Mock",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot=invalid_config,
                backtest_result=sample_backtest_result,
            )


class TestBacktestPersistenceServiceErrorHandling:
    """Test suite for error handling."""

    @pytest.mark.asyncio
    async def test_propagates_duplicate_record_error(
        self,
        persistence_service,
        mock_repository,
        sample_backtest_result,
        sample_config_snapshot,
    ):
        """Test that duplicate record errors are propagated."""
        # Arrange
        run_id = uuid4()
        mock_repository.create_backtest_run.side_effect = DuplicateRecordError(
            f"Run ID {run_id} already exists"
        )

        # Act & Assert
        with pytest.raises(DuplicateRecordError, match="already exists"):
            await persistence_service.save_backtest_results(
                run_id=run_id,
                strategy_name="Test",
                strategy_type="test",
                instrument_symbol="TEST",
                start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
                initial_capital=Decimal("100000.00"),
                data_source="Mock",
                execution_duration_seconds=Decimal("10.0"),
                config_snapshot=sample_config_snapshot,
                backtest_result=sample_backtest_result,
            )


class TestBacktestPersistenceServiceMetricCalculation:
    """Test suite for metric calculations."""

    @pytest.mark.asyncio
    async def test_calculates_win_rate_correctly(
        self, persistence_service, mock_repository, sample_config_snapshot
    ):
        """Test win rate calculation from trades."""
        # Arrange
        result = BacktestResult(
            total_return=10000.0,
            total_trades=100,
            winning_trades=65,
            losing_trades=35,
            largest_win=500.0,
            largest_loss=-200.0,
            final_balance=110000.0,
        )
        mock_run = Mock()
        mock_run.id = 1
        mock_repository.create_backtest_run.return_value = mock_run
        mock_repository.create_performance_metrics.return_value = Mock()

        # Act
        await persistence_service.save_backtest_results(
            run_id=uuid4(),
            strategy_name="Test",
            strategy_type="test",
            instrument_symbol="TEST",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot=sample_config_snapshot,
            backtest_result=result,
        )

        # Assert
        metrics_call_kwargs = (
            mock_repository.create_performance_metrics.call_args.kwargs
        )
        expected_win_rate = Decimal("0.6500")  # 65/100
        assert metrics_call_kwargs["win_rate"] == expected_win_rate

    @pytest.mark.asyncio
    async def test_handles_zero_trades_gracefully(
        self, persistence_service, mock_repository, sample_config_snapshot
    ):
        """Test handling of zero trades scenario."""
        # Arrange
        result = BacktestResult(
            total_return=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            largest_win=0.0,
            largest_loss=0.0,
            final_balance=100000.0,
        )
        mock_run = Mock()
        mock_run.id = 1
        mock_repository.create_backtest_run.return_value = mock_run
        mock_repository.create_performance_metrics.return_value = Mock()

        # Act
        await persistence_service.save_backtest_results(
            run_id=uuid4(),
            strategy_name="Test",
            strategy_type="test",
            instrument_symbol="TEST",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot=sample_config_snapshot,
            backtest_result=result,
        )

        # Assert
        metrics_call_kwargs = (
            mock_repository.create_performance_metrics.call_args.kwargs
        )
        assert metrics_call_kwargs["win_rate"] is None  # Cannot calculate with 0 trades
        assert metrics_call_kwargs["total_trades"] == 0
