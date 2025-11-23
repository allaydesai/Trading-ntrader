"""
Tests for interactive chart integration in backtest detail view.

Tests cover chart container elements, data attributes for API calls,
and correct display conditions for successful/failed backtests.
"""

from collections.abc import Generator
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service
from src.api.web import app
from src.db.models.backtest import BacktestRun, PerformanceMetrics


@pytest.fixture
def mock_service():
    """Create a mock BacktestQueryService."""
    service = AsyncMock()
    return service


@pytest.fixture
def client_with_mock(
    mock_service,
) -> Generator[tuple[TestClient, AsyncMock], None, None]:
    """Get test client with mocked service."""

    def override_service():
        return mock_service

    app.dependency_overrides[get_backtest_query_service] = override_service
    yield TestClient(app), mock_service
    app.dependency_overrides.clear()


@pytest.fixture
def sample_successful_backtest() -> BacktestRun:
    """Create a sample successful BacktestRun with metrics."""
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
    metrics.created_at = datetime.now(timezone.utc)

    run.metrics = metrics
    return run


@pytest.fixture
def sample_failed_backtest() -> BacktestRun:
    """Create a sample failed BacktestRun without metrics."""
    run = BacktestRun(
        id=2,
        run_id=uuid4(),
        strategy_name="Test Strategy",
        strategy_type="test",
        instrument_symbol="TSLA",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="TEST",
        execution_status="failure",
        execution_duration_seconds=Decimal("5.0"),
        config_snapshot={},
        error_message="Insufficient data for backtest",
    )
    run.created_at = datetime.now(timezone.utc)
    run.metrics = None
    return run


# ============================================================================
# Price Chart Container Tests
# ============================================================================


class TestPriceChartContainer:
    """Tests for price chart container element."""

    def test_price_chart_container_present_for_successful_backtest(
        self, client_with_mock, sample_successful_backtest
    ):
        """Price chart container is rendered for successful backtests."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert 'id="run-price-chart"' in html
        assert 'data-chart="run-price"' in html

    def test_price_chart_has_correct_run_id_attribute(
        self, client_with_mock, sample_successful_backtest
    ):
        """Price chart container has correct run_id data attribute."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert f'data-run-id="{sample_successful_backtest.run_id}"' in html

    def test_price_chart_has_correct_symbol_attribute(
        self, client_with_mock, sample_successful_backtest
    ):
        """Price chart container has correct symbol data attribute."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert 'data-symbol="AAPL"' in html

    def test_price_chart_has_correct_date_range_attributes(
        self, client_with_mock, sample_successful_backtest
    ):
        """Price chart container has correct start and end date attributes."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert 'data-start="2024-01-01"' in html
        assert 'data-end="2024-12-31"' in html

    def test_price_chart_not_present_for_failed_backtest(
        self, client_with_mock, sample_failed_backtest
    ):
        """Price chart container is NOT rendered for failed backtests."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_failed_backtest

        response = client.get(f"/backtests/{sample_failed_backtest.run_id}")
        html = response.text

        assert 'id="run-price-chart"' not in html

    def test_price_chart_has_loading_spinner(self, client_with_mock, sample_successful_backtest):
        """Price chart container includes loading spinner element."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert "chart-loading" in html
        assert "Loading price data" in html


# ============================================================================
# Equity Chart Container Tests
# ============================================================================


class TestEquityChartContainer:
    """Tests for equity chart container element."""

    def test_equity_chart_container_present_for_successful_backtest(
        self, client_with_mock, sample_successful_backtest
    ):
        """Equity chart container is rendered for successful backtests."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert 'id="run-equity-chart"' in html
        assert 'data-chart="run-equity"' in html

    def test_equity_chart_has_correct_run_id_attribute(
        self, client_with_mock, sample_successful_backtest
    ):
        """Equity chart container has correct run_id data attribute."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        # Check equity chart has run_id
        assert 'data-chart="run-equity"' in html
        # The run_id appears in the equity chart container
        run_id_str = str(sample_successful_backtest.run_id)
        assert run_id_str in html

    def test_equity_chart_not_present_for_failed_backtest(
        self, client_with_mock, sample_failed_backtest
    ):
        """Equity chart container is NOT rendered for failed backtests."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_failed_backtest

        response = client.get(f"/backtests/{sample_failed_backtest.run_id}")
        html = response.text

        assert 'id="run-equity-chart"' not in html

    def test_equity_chart_has_loading_spinner(self, client_with_mock, sample_successful_backtest):
        """Equity chart container includes loading spinner element."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert "Loading equity data" in html


# ============================================================================
# Chart Section Layout Tests
# ============================================================================


class TestChartSectionLayout:
    """Tests for chart section layout and positioning."""

    def test_charts_section_has_price_chart_heading(
        self, client_with_mock, sample_successful_backtest
    ):
        """Charts section includes Price Chart heading."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert "Price Chart" in html

    def test_charts_section_has_equity_curve_heading(
        self, client_with_mock, sample_successful_backtest
    ):
        """Charts section includes Equity Curve heading."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert "Equity Curve" in html

    def test_charts_rendered_with_correct_height_classes(
        self, client_with_mock, sample_successful_backtest
    ):
        """Chart containers have appropriate height classes."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        # Price chart should be taller
        assert "h-96" in html
        # Equity chart can be shorter
        assert "h-64" in html

    def test_charts_in_dark_theme_containers(self, client_with_mock, sample_successful_backtest):
        """Chart containers use dark theme styling."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        # Charts should be in slate-900 containers
        assert "bg-slate-900" in html
        assert "border-slate-700" in html


# ============================================================================
# Base Template Tests
# ============================================================================


class TestBaseTemplateScripts:
    """Tests for required scripts in base template."""

    def test_lightweight_charts_script_included(self, client_with_mock, sample_successful_backtest):
        """Base template includes TradingView Lightweight Charts script."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert "lightweight-charts" in html

    def test_charts_js_script_included(self, client_with_mock, sample_successful_backtest):
        """Base template includes charts.js initialization script."""
        client, mock_service = client_with_mock
        mock_service.get_backtest_by_id.return_value = sample_successful_backtest

        response = client.get(f"/backtests/{sample_successful_backtest.run_id}")
        html = response.text

        assert "charts.js" in html
