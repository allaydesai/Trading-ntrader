"""
Unit tests for backtest detail view Pydantic models.

Tests cover model validation, computed fields, formatting, and
mapping functions from database entities to view models.
"""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.api.models.backtest_detail import (
    BacktestDetailView,
    ConfigurationSnapshot,
    MetricDisplayItem,
    TradingSummary,
    build_configuration,
    build_metrics_panel,
    build_trading_summary,
    to_detail_view,
)
from src.db.models.backtest import BacktestRun, PerformanceMetrics


class TestMetricDisplayItem:
    """Tests for MetricDisplayItem model and computed fields."""

    def test_formatted_value_percentage(self):
        """Percentage format multiplies by 100 and adds % suffix."""
        metric = MetricDisplayItem(
            name="Total Return",
            value=Decimal("0.25"),
            format_type="percentage",
            tooltip="Test tooltip",
            is_favorable=True,
        )
        assert metric.formatted_value == "25.00%"

    def test_formatted_value_decimal(self):
        """Decimal format shows 4 decimal places."""
        metric = MetricDisplayItem(
            name="Sharpe Ratio",
            value=Decimal("1.8567"),
            format_type="decimal",
            tooltip="Test tooltip",
            is_favorable=True,
        )
        assert metric.formatted_value == "1.8567"

    def test_formatted_value_currency(self):
        """Currency format adds $ prefix and thousand separators."""
        metric = MetricDisplayItem(
            name="Final Balance",
            value=Decimal("125000.50"),
            format_type="currency",
            tooltip="Test tooltip",
            is_favorable=True,
        )
        assert metric.formatted_value == "$125,000.50"

    def test_formatted_value_integer(self):
        """Integer format adds thousand separators."""
        metric = MetricDisplayItem(
            name="Total Trades",
            value=Decimal("1500"),
            format_type="integer",
            tooltip="Test tooltip",
            is_favorable=True,
        )
        assert metric.formatted_value == "1,500"

    def test_formatted_value_none(self):
        """None values display as N/A."""
        metric = MetricDisplayItem(
            name="Missing Metric",
            value=None,
            format_type="decimal",
            tooltip="Test tooltip",
            is_favorable=True,
        )
        assert metric.formatted_value == "N/A"

    def test_color_class_positive_favorable(self):
        """Positive values with is_favorable=True are green."""
        metric = MetricDisplayItem(
            name="Sharpe Ratio",
            value=Decimal("1.5"),
            format_type="decimal",
            tooltip="Test",
            is_favorable=True,
        )
        assert metric.color_class == "text-green-400"

    def test_color_class_negative_favorable(self):
        """Negative values with is_favorable=True are red."""
        metric = MetricDisplayItem(
            name="Total Return",
            value=Decimal("-0.10"),
            format_type="percentage",
            tooltip="Test",
            is_favorable=True,
        )
        assert metric.color_class == "text-red-400"

    def test_color_class_zero_value(self):
        """Zero values display as slate (neutral)."""
        metric = MetricDisplayItem(
            name="Return",
            value=Decimal("0"),
            format_type="percentage",
            tooltip="Test",
            is_favorable=True,
        )
        assert metric.color_class == "text-slate-300"

    def test_color_class_negative_unfavorable(self):
        """Negative values with is_favorable=False are red (e.g., drawdown)."""
        metric = MetricDisplayItem(
            name="Max Drawdown",
            value=Decimal("-0.15"),
            format_type="percentage",
            tooltip="Test",
            is_favorable=False,
        )
        assert metric.color_class == "text-red-400"

    def test_color_class_none_value(self):
        """None values display as slate-400."""
        metric = MetricDisplayItem(
            name="Missing",
            value=None,
            format_type="decimal",
            tooltip="Test",
            is_favorable=True,
        )
        assert metric.color_class == "text-slate-400"


class TestConfigurationSnapshot:
    """Tests for ConfigurationSnapshot model."""

    @pytest.fixture
    def sample_config(self) -> ConfigurationSnapshot:
        """Create sample configuration snapshot."""
        return ConfigurationSnapshot(
            instrument_symbol="AAPL",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            data_source="IBKR",
            additional_params={"fast_period": 10, "slow_period": 50},
        )

    def test_date_range_display(self, sample_config):
        """Date range display formats dates correctly."""
        assert sample_config.date_range_display == "2024-01-01 to 2024-12-31"

    def test_cli_command_includes_base_parameters(self, sample_config):
        """CLI command includes all base configuration parameters."""
        cmd = sample_config.cli_command
        assert "ntrader backtest run" in cmd
        assert "--strategy SMA Crossover" in cmd
        assert "--instrument AAPL" in cmd
        assert "--start 2024-01-01" in cmd
        assert "--end 2024-12-31" in cmd
        assert "--capital 100000.00" in cmd

    def test_cli_command_includes_additional_params(self, sample_config):
        """CLI command includes strategy-specific parameters."""
        cmd = sample_config.cli_command
        assert "--fast-period 10" in cmd
        assert "--slow-period 50" in cmd

    def test_cli_command_replaces_underscores_with_hyphens(self, sample_config):
        """Parameter names with underscores are converted to hyphens."""
        cmd = sample_config.cli_command
        # Should be --fast-period, not --fast_period
        assert "--fast-period" in cmd
        assert "--fast_period" not in cmd


class TestTradingSummary:
    """Tests for TradingSummary model."""

    def test_has_trades_true(self):
        """has_trades returns True when total_trades > 0."""
        summary = TradingSummary(
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=Decimal("0.60"),
            avg_win=Decimal("500.00"),
            avg_loss=Decimal("-250.00"),
            profit_factor=Decimal("2.5"),
            expectancy=Decimal("250.00"),
        )
        assert summary.has_trades is True

    def test_has_trades_false(self):
        """has_trades returns False when total_trades = 0."""
        summary = TradingSummary(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
        )
        assert summary.has_trades is False


class TestBacktestDetailView:
    """Tests for BacktestDetailView model computed fields."""

    @pytest.fixture
    def sample_view(self) -> BacktestDetailView:
        """Create sample detail view."""
        config = ConfigurationSnapshot(
            instrument_symbol="AAPL",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            data_source="IBKR",
            additional_params={},
        )
        return BacktestDetailView(
            run_id=uuid4(),
            strategy_name="SMA Crossover",
            execution_status="success",
            execution_time=datetime.now(timezone.utc),
            execution_duration=Decimal("45.5"),
            configuration=config,
            breadcrumbs=[
                {"label": "Dashboard", "url": "/"},
                {"label": "Backtests", "url": "/backtests"},
                {"label": "Run Details", "url": None},
            ],
        )

    def test_run_id_short(self, sample_view):
        """run_id_short returns first 8 characters of UUID."""
        full_id = str(sample_view.run_id)
        assert sample_view.run_id_short == full_id[:8]

    def test_is_successful_true(self, sample_view):
        """is_successful returns True for success status."""
        assert sample_view.is_successful is True

    def test_is_successful_false(self, sample_view):
        """is_successful returns False for failed status."""
        sample_view.execution_status = "failed"
        assert sample_view.is_successful is False

    def test_duration_formatted_seconds(self, sample_view):
        """Duration under 60 seconds shows seconds."""
        sample_view.execution_duration = Decimal("45.5")
        assert sample_view.duration_formatted == "45.5s"

    def test_duration_formatted_minutes(self, sample_view):
        """Duration over 60 seconds shows minutes."""
        sample_view.execution_duration = Decimal("120.0")
        assert sample_view.duration_formatted == "2.0m"

    def test_breadcrumbs_structure(self, sample_view):
        """Breadcrumbs contain expected navigation path."""
        crumbs = sample_view.breadcrumbs
        assert len(crumbs) == 3
        assert crumbs[0]["label"] == "Dashboard"
        assert crumbs[0]["url"] == "/"
        assert crumbs[1]["label"] == "Backtests"
        assert crumbs[1]["url"] == "/backtests"
        assert crumbs[2]["label"] == "Run Details"
        assert crumbs[2]["url"] is None


class TestMappingFunctions:
    """Tests for mapping functions from database models to view models."""

    @pytest.fixture
    def sample_metrics(self) -> PerformanceMetrics:
        """Create sample PerformanceMetrics."""
        metrics = PerformanceMetrics(
            id=1,
            backtest_run_id=1,
            total_return=Decimal("0.25"),
            final_balance=Decimal("125000.00"),
            cagr=Decimal("0.28"),
            sharpe_ratio=Decimal("1.85"),
            sortino_ratio=Decimal("2.10"),
            max_drawdown=Decimal("-0.15"),
            volatility=Decimal("0.18"),
            total_trades=100,
            winning_trades=60,
            losing_trades=40,
            win_rate=Decimal("0.60"),
            profit_factor=Decimal("2.5"),
            expectancy=Decimal("250.00"),
            avg_win=Decimal("500.00"),
            avg_loss=Decimal("-250.00"),
        )
        # Set timestamp via attribute assignment (mixin handles this)
        metrics.created_at = datetime.now(timezone.utc)
        return metrics

    @pytest.fixture
    def sample_run(self, sample_metrics) -> BacktestRun:
        """Create sample BacktestRun with metrics."""
        run = BacktestRun(
            id=1,
            run_id=uuid4(),
            strategy_name="SMA Crossover",
            strategy_type="trend_following",
            instrument_symbol="AAPL",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="IBKR",
            execution_status="success",
            execution_duration_seconds=Decimal("45.5"),
            config_snapshot={"fast_period": 10, "slow_period": 20},
        )
        # Set timestamp via attribute assignment (mixin handles this)
        run.created_at = datetime.now(timezone.utc)
        run.metrics = sample_metrics
        return run

    def test_build_metrics_panel_returns_organized_metrics(self, sample_metrics):
        """build_metrics_panel returns MetricsPanel with categorized metrics."""
        panel = build_metrics_panel(sample_metrics)

        assert panel is not None
        assert len(panel.return_metrics) == 3
        assert len(panel.risk_metrics) == 4
        assert len(panel.trading_metrics) == 3

        # Check specific metrics
        assert panel.return_metrics[0].name == "Total Return"
        assert panel.return_metrics[0].value == Decimal("0.25")

        assert panel.risk_metrics[0].name == "Sharpe Ratio"
        assert panel.risk_metrics[0].value == Decimal("1.85")

        assert panel.trading_metrics[0].name == "Total Trades"

    def test_build_metrics_panel_returns_none_for_none_input(self):
        """build_metrics_panel returns None when metrics is None."""
        result = build_metrics_panel(None)
        assert result is None

    def test_build_metrics_panel_raises_error_for_missing_fields(self):
        """build_metrics_panel raises ValueError for invalid metrics object."""

        class IncompleteMetrics:
            """Metrics object missing required fields."""

            total_return = Decimal("0.25")
            # Missing: cagr, final_balance, sharpe_ratio, etc.

        with pytest.raises(ValueError) as exc_info:
            build_metrics_panel(IncompleteMetrics())

        assert "missing required fields" in str(exc_info.value).lower()
        assert "cagr" in str(exc_info.value)

    def test_build_configuration_extracts_all_fields(self, sample_run):
        """build_configuration extracts all fields from BacktestRun."""
        config = build_configuration(sample_run)

        assert config.instrument_symbol == "AAPL"
        assert config.strategy_name == "SMA Crossover"
        assert config.strategy_type == "trend_following"
        assert config.initial_capital == Decimal("100000.00")
        assert config.data_source == "IBKR"
        assert config.additional_params == {"fast_period": 10, "slow_period": 20}

    def test_build_trading_summary_extracts_trade_stats(self, sample_metrics):
        """build_trading_summary extracts trading statistics."""
        summary = build_trading_summary(sample_metrics)

        assert summary is not None
        assert summary.total_trades == 100
        assert summary.winning_trades == 60
        assert summary.losing_trades == 40
        assert summary.win_rate == Decimal("0.60")
        assert summary.profit_factor == Decimal("2.5")
        assert summary.expectancy == Decimal("250.00")

    def test_build_trading_summary_returns_none_for_none_input(self):
        """build_trading_summary returns None when metrics is None."""
        result = build_trading_summary(None)
        assert result is None

    def test_to_detail_view_creates_complete_view_model(self, sample_run):
        """to_detail_view creates BacktestDetailView with all components."""
        view = to_detail_view(sample_run)

        assert view.run_id == sample_run.run_id
        assert view.strategy_name == "SMA Crossover"
        assert view.execution_status == "success"
        assert view.execution_duration == Decimal("45.5")
        assert view.error_message is None

        # Check nested models are created
        assert view.metrics_panel is not None
        assert view.configuration is not None
        assert view.trading_summary is not None

        # Verify computed fields work
        assert view.is_successful is True
        assert view.duration_formatted == "45.5s"

    def test_to_detail_view_handles_failed_backtest(self):
        """to_detail_view handles failed backtests without metrics."""
        run = BacktestRun(
            id=2,
            run_id=uuid4(),
            strategy_name="Broken Strategy",
            strategy_type="experimental",
            instrument_symbol="TEST",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="CSV",
            execution_status="failed",
            execution_duration_seconds=Decimal("2.5"),
            config_snapshot={},
            error_message="Strategy failed",
        )
        # Set timestamps via attribute assignment (mixin handles these)
        run.created_at = datetime.now(timezone.utc)
        run.updated_at = datetime.now(timezone.utc)
        run.metrics = None  # Failed backtest has no metrics

        view = to_detail_view(run)

        assert view.is_successful is False
        assert view.error_message == "Strategy failed"
        assert view.metrics_panel is None
        assert view.trading_summary is None
        # Configuration should still be present
        assert view.configuration is not None
