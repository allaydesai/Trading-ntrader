"""
Tests for backtest detail view routes and templates.

Tests cover all user stories: metrics display, trading summary,
configuration snapshot, and action buttons (export, delete, re-run).
"""

import pytest
from collections.abc import Generator
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from src.api.web import app
from src.api.dependencies import get_backtest_query_service
from src.db.models.backtest import BacktestRun, PerformanceMetrics


@pytest.fixture
def mock_service():
    """Create a mock BacktestQueryService.

    Note: Using AsyncMock without spec to allow testing methods that
    don't exist yet in the service (TDD approach).
    """
    service = AsyncMock()
    return service


@pytest.fixture
def client_with_mock(
    mock_service,
) -> Generator[tuple[TestClient, AsyncMock], None, None]:
    """Get test client with mocked service, returns both client and service."""

    def override_service():
        return mock_service

    app.dependency_overrides[get_backtest_query_service] = override_service
    yield TestClient(app), mock_service
    app.dependency_overrides.clear()


@pytest.fixture
def sample_backtest_with_metrics() -> BacktestRun:
    """Create a sample BacktestRun with associated metrics."""
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

    metrics = PerformanceMetrics(
        id=1,
        backtest_run_id=1,
        total_return=Decimal("0.25"),
        final_balance=Decimal("125000.00"),
        cagr=Decimal("0.28"),
        sharpe_ratio=Decimal("1.85"),
        sortino_ratio=Decimal("2.10"),
        max_drawdown=Decimal("-0.15"),
        max_drawdown_date=datetime(2024, 6, 15, tzinfo=timezone.utc),
        calmar_ratio=Decimal("1.87"),
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

    run.metrics = metrics
    return run


# ============================================================================
# User Story 1: View Complete Backtest Results
# ============================================================================


class TestDetailPageRouting:
    """Tests for GET /backtests/{run_id} route."""

    def test_detail_page_returns_200_for_valid_run_id(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """GET /backtests/{run_id} returns 200 with valid UUID."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        assert response.status_code == 200
        assert "Run Details" in response.text

    def test_detail_page_returns_404_for_nonexistent_run_id(self, client_with_mock):
        """GET /backtests/{run_id} returns 404 for non-existent UUID."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = None

        fake_id = uuid4()
        response = client.get(f"/backtests/{fake_id}")
        assert response.status_code == 404


class TestMetricsDisplay:
    """Tests for metrics display in detail page."""

    def test_displays_all_return_metrics(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Detail page displays Total Return, CAGR, and Final Balance."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Total Return" in html
        assert "CAGR" in html
        assert "Final Balance" in html
        # Check actual values
        assert "25.00%" in html  # Total return formatted
        assert "$125,000.00" in html  # Final balance formatted

    def test_displays_all_risk_metrics(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Detail page displays Sharpe, Sortino, Max Drawdown, and Volatility."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Sharpe Ratio" in html
        assert "Sortino Ratio" in html
        assert "Max Drawdown" in html
        assert "Volatility" in html

    def test_displays_all_trading_metrics(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Detail page displays Total Trades, Win Rate, and Profit Factor."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Total Trades" in html
        assert "Win Rate" in html
        assert "Profit Factor" in html

    def test_positive_metrics_highlighted_green(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Positive metrics are highlighted with green CSS class."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        # Check for green color class for positive values
        assert "text-green-400" in html

    def test_negative_metrics_highlighted_red(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Negative metrics are highlighted with red CSS class."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        # Max drawdown is negative, should have red class
        assert "text-red-400" in html

    def test_breadcrumb_navigation_shows_correct_path(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Breadcrumb shows Dashboard > Backtests > Run Details."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Dashboard" in html
        assert "Backtests" in html
        assert "Run Details" in html


# ============================================================================
# User Story 2: Review Trade History
# ============================================================================


class TestTradingSummary:
    """Tests for trading summary panel."""

    def test_displays_total_trades_count(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Trading summary displays total trades count."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Total Trades" in html
        assert "100" in html  # Total trades value

    def test_displays_winning_losing_trade_counts(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Trading summary displays winning and losing trade counts."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Winning Trades" in html
        assert "Losing Trades" in html
        assert "60" in html  # Winning trades
        assert "40" in html  # Losing trades

    def test_displays_win_rate_percentage(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Trading summary displays win rate as percentage."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        # 0.60 should be displayed as 60%
        assert "60" in html

    def test_displays_average_win_loss_amounts(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Trading summary displays average win and loss amounts."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Average Win" in html
        assert "Average Loss" in html
        assert "500.00" in html  # Avg win value
        assert "250.00" in html  # Avg loss value (abs)

    def test_handles_zero_trades_gracefully(self, client_with_mock):
        """Trading summary handles zero trades with informative message."""
        client, mock_service = client_with_mock
        # Create backtest with zero trades
        run = BacktestRun(
            id=1,
            run_id=uuid4(),
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="TEST",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="TEST",
            execution_status="success",
            execution_duration_seconds=Decimal("10.0"),
            config_snapshot={},
        )
        # Set timestamps via attribute assignment (mixin handles these)
        run.created_at = datetime.now(timezone.utc)
        run.updated_at = datetime.now(timezone.utc)

        metrics = PerformanceMetrics(
            id=1,
            backtest_run_id=1,
            total_return=Decimal("0.0"),
            final_balance=Decimal("100000.00"),
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
        )
        # Set timestamps via attribute assignment (mixin handles these)
        metrics.created_at = datetime.now(timezone.utc)
        metrics.updated_at = datetime.now(timezone.utc)

        run.metrics = metrics

        mock_service.get_backtest_by_id.return_value = run

        response = client.get(f"/backtests/{run.run_id}")
        html = response.text

        assert "No trades were executed" in html


# ============================================================================
# User Story 3: View Backtest Configuration
# ============================================================================


class TestConfigurationDisplay:
    """Tests for configuration snapshot display."""

    def test_displays_instrument_symbol(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Configuration section displays instrument symbol."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Instrument" in html
        assert "AAPL" in html

    def test_displays_date_range(self, client_with_mock, sample_backtest_with_metrics):
        """Configuration section displays start and end dates."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Date Range" in html
        assert "2024-01-01" in html
        assert "2024-12-31" in html

    def test_displays_initial_capital(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Configuration section displays initial capital."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "Initial Capital" in html
        assert "100,000" in html

    def test_displays_strategy_specific_parameters(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Configuration section displays strategy-specific parameters."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        # fast_period and slow_period from config_snapshot
        assert "Fast Period" in html or "fast_period" in html.lower()
        assert "Slow Period" in html or "slow_period" in html.lower()
        assert "10" in html
        assert "20" in html

    def test_cli_command_includes_all_parameters(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """CLI command generation includes all configuration parameters."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        assert "ntrader backtest run" in html
        assert "--strategy" in html
        assert "--instrument" in html
        assert "SMA Crossover" in html
        assert "AAPL" in html

    def test_configuration_section_is_collapsible(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Configuration section is rendered as collapsible details element."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(f"/backtests/{sample_backtest_with_metrics.run_id}")
        html = response.text

        # Check for details element with open attribute (expanded by default)
        assert "<details open" in html
        assert "Configuration Parameters" in html


# ============================================================================
# User Story 4: Perform Actions on Backtest
# ============================================================================


class TestActionEndpoints:
    """Tests for action button endpoints."""

    def test_export_returns_html_file_download(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """GET /backtests/{run_id}/export returns HTML file with download header."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(
            f"/backtests/{sample_backtest_with_metrics.run_id}/export"
        )

        assert response.status_code == 200
        assert "attachment" in response.headers.get("content-disposition", "")

    def test_delete_removes_record_and_redirects(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """DELETE /backtests/{run_id} removes record and returns redirect header."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics
        mock_service.delete_backtest.return_value = True

        response = client.delete(
            f"/backtests/{sample_backtest_with_metrics.run_id}",
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        assert "HX-Redirect" in response.headers

    def test_delete_returns_404_for_nonexistent_backtest(self, client_with_mock):
        """DELETE /backtests/{run_id} returns 404 for non-existent backtest."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = None

        fake_id = uuid4()
        response = client.delete(
            f"/backtests/{fake_id}", headers={"HX-Request": "true"}
        )

        assert response.status_code == 404

    def test_rerun_creates_new_backtest_run(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """POST /backtests/{run_id}/rerun creates new backtest run."""
        client, mock_service = client_with_mock
        new_run_id = uuid4()
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics
        mock_service.rerun_backtest.return_value = new_run_id

        response = client.post(
            f"/backtests/{sample_backtest_with_metrics.run_id}/rerun",
            headers={"HX-Request": "true"},
        )

        # Should redirect to new run
        assert response.status_code == 202 or response.status_code == 200

    def test_rerun_returns_404_for_nonexistent_backtest(self, client_with_mock):
        """POST /backtests/{run_id}/rerun returns 404 for non-existent backtest."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = None

        fake_id = uuid4()
        response = client.post(
            f"/backtests/{fake_id}/rerun", headers={"HX-Request": "true"}
        )

        assert response.status_code == 404

    def test_export_includes_correct_content_disposition_header(
        self, client_with_mock, sample_backtest_with_metrics
    ):
        """Export includes correct Content-Disposition header for file download."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_backtest_with_metrics

        response = client.get(
            f"/backtests/{sample_backtest_with_metrics.run_id}/export"
        )

        content_disposition = response.headers.get("content-disposition", "")
        assert "attachment" in content_disposition
        assert "filename" in content_disposition
        assert str(sample_backtest_with_metrics.run_id) in content_disposition
