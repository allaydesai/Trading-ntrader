"""Unit tests for BacktestRun and PerformanceMetrics models."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.db.models.backtest import BacktestRun, PerformanceMetrics


class TestBacktestRunModel:
    """Test suite for BacktestRun SQLAlchemy model."""

    def test_backtest_run_creation(self):
        """Test BacktestRun model instantiation with valid data."""
        run_id = uuid4()
        start_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2023, 12, 31, tzinfo=timezone.utc)

        backtest_run = BacktestRun(
            run_id=run_id,
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            instrument_symbol="AAPL",
            start_date=start_date,
            end_date=end_date,
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("45.237"),
            config_snapshot={
                "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
                "config_path": "config/strategies/sma_crossover.yaml",
                "version": "1.0",
                "config": {"fast_period": 10, "slow_period": 50},
            },
        )

        assert backtest_run.run_id == run_id
        assert backtest_run.strategy_name == "SMA Crossover"
        assert backtest_run.strategy_type == "trend_following"
        assert backtest_run.instrument_symbol == "AAPL"
        assert backtest_run.start_date == start_date
        assert backtest_run.end_date == end_date
        assert backtest_run.initial_capital == Decimal("100000.00")
        assert backtest_run.data_source == "IBKR"
        assert backtest_run.execution_status == "success"
        assert backtest_run.execution_duration_seconds == Decimal("45.237")
        assert backtest_run.error_message is None
        assert "strategy_path" in backtest_run.config_snapshot

    def test_backtest_run_with_error_message(self):
        """Test BacktestRun model for failed execution."""
        run_id = uuid4()

        backtest_run = BacktestRun(
            run_id=run_id,
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="TEST",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_status="failed",
            execution_duration_seconds=Decimal("5.0"),
            error_message="Test error message",
            config_snapshot={
                "strategy_path": "test",
                "config_path": "test",
                "config": {},
            },
        )

        assert backtest_run.execution_status == "failed"
        assert backtest_run.error_message == "Test error message"

    def test_backtest_run_with_reproduced_from(self):
        """Test BacktestRun model with reproduction reference."""
        original_run_id = uuid4()
        reproduced_run_id = uuid4()

        backtest_run = BacktestRun(
            run_id=reproduced_run_id,
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="TEST",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="Mock",
            execution_status="success",
            execution_duration_seconds=Decimal("5.0"),
            config_snapshot={
                "strategy_path": "test",
                "config_path": "test",
                "config": {},
            },
            reproduced_from_run_id=original_run_id,
        )

        assert backtest_run.reproduced_from_run_id == original_run_id


class TestPerformanceMetricsModel:
    """Test suite for PerformanceMetrics SQLAlchemy model."""

    def test_performance_metrics_creation(self):
        """Test PerformanceMetrics model instantiation with valid data."""
        metrics = PerformanceMetrics(
            backtest_run_id=1,
            total_return=Decimal("0.25"),
            final_balance=Decimal("125000.00"),
            cagr=Decimal("0.22"),
            sharpe_ratio=Decimal("1.85"),
            sortino_ratio=Decimal("2.10"),
            max_drawdown=Decimal("-0.15"),
            max_drawdown_date=datetime(2023, 6, 15, tzinfo=timezone.utc),
            calmar_ratio=Decimal("1.50"),
            volatility=Decimal("0.12"),
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=Decimal("0.60"),
            profit_factor=Decimal("1.75"),
            expectancy=Decimal("250.00"),
            avg_win=Decimal("500.00"),
            avg_loss=Decimal("-285.71"),
        )

        assert metrics.backtest_run_id == 1
        assert metrics.total_return == Decimal("0.25")
        assert metrics.final_balance == Decimal("125000.00")
        assert metrics.sharpe_ratio == Decimal("1.85")
        assert metrics.total_trades == 100
        assert metrics.winning_trades == 60
        assert metrics.losing_trades == 40

    def test_performance_metrics_with_nulls(self):
        """Test PerformanceMetrics model with nullable fields."""
        metrics = PerformanceMetrics(
            backtest_run_id=1,
            total_return=Decimal("0.10"),
            final_balance=Decimal("110000.00"),
            cagr=None,
            sharpe_ratio=None,
            sortino_ratio=None,
            max_drawdown=None,
            max_drawdown_date=None,
            calmar_ratio=None,
            volatility=None,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=None,
            profit_factor=None,
            expectancy=None,
            avg_win=None,
            avg_loss=None,
        )

        assert metrics.total_return == Decimal("0.10")
        assert metrics.sharpe_ratio is None
        assert metrics.total_trades == 0

    def test_performance_metrics_trade_count_consistency(self):
        """Test that total_trades equals winning_trades + losing_trades."""
        metrics = PerformanceMetrics(
            backtest_run_id=1,
            total_return=Decimal("0.15"),
            final_balance=Decimal("115000.00"),
            total_trades=150,
            winning_trades=90,
            losing_trades=60,
        )

        # Verify trade count consistency
        assert metrics.total_trades == (metrics.winning_trades + metrics.losing_trades)
