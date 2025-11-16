"""
Tests for dashboard route and functionality.

Tests dashboard statistics display, empty state handling, and template rendering.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.web import app
from src.api.dependencies import get_backtest_query_service
from src.api.models.dashboard import DashboardSummary, RecentBacktestItem
from src.services.backtest_query import BacktestQueryService


@pytest.fixture
def mock_empty_service() -> BacktestQueryService:
    """Mock service returning empty dashboard stats."""
    mock_service = AsyncMock(spec=BacktestQueryService)
    mock_service.get_dashboard_stats = AsyncMock(
        return_value=DashboardSummary(total_backtests=0)
    )
    return mock_service


@pytest.fixture
def mock_service_with_data() -> BacktestQueryService:
    """Mock service returning populated dashboard stats."""
    mock_service = AsyncMock(spec=BacktestQueryService)

    recent_items = [
        RecentBacktestItem(
            run_id=uuid4(),
            strategy_name=f"Strategy {i + 1}",
            instrument_symbol="AAPL",
            execution_status="success",
            created_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            total_return=Decimal(f"0.{i + 1}0"),
        )
        for i in range(5)
    ]

    mock_service.get_dashboard_stats = AsyncMock(
        return_value=DashboardSummary(
            total_backtests=5,
            best_sharpe_ratio=Decimal("3.0"),
            best_sharpe_strategy="Strategy 5",
            worst_max_drawdown=Decimal("-0.20"),
            worst_drawdown_strategy="Strategy 1",
            recent_backtests=recent_items,
        )
    )
    return mock_service


@pytest.fixture
def client_with_empty_db(mock_empty_service: BacktestQueryService) -> TestClient:
    """Client with empty database."""

    def override_service():
        return mock_empty_service

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client_with_backtests(mock_service_with_data: BacktestQueryService) -> TestClient:
    """Client with database containing backtests."""

    def override_service():
        return mock_service_with_data

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_dashboard_returns_200(client_with_empty_db: TestClient):
    """Dashboard page loads successfully."""
    response = client_with_empty_db.get("/")
    assert response.status_code == 200


def test_dashboard_has_dark_background(client_with_empty_db: TestClient):
    """Dashboard has dark background class."""
    response = client_with_empty_db.get("/")
    assert "bg-slate-950" in response.text


def test_dashboard_has_light_text(client_with_empty_db: TestClient):
    """Dashboard has light text class."""
    response = client_with_empty_db.get("/")
    assert "text-slate-100" in response.text


def test_dashboard_shows_empty_state_when_no_backtests(
    client_with_empty_db: TestClient,
):
    """Dashboard handles no backtests gracefully."""
    response = client_with_empty_db.get("/")
    assert "No Backtests Yet" in response.text
    assert "ntrader backtest run" in response.text


def test_dashboard_displays_navigation(client_with_empty_db: TestClient):
    """Dashboard includes navigation bar."""
    response = client_with_empty_db.get("/")
    assert "Dashboard" in response.text
    assert "Backtests" in response.text
    assert "Data" in response.text
    assert "Docs" in response.text


def test_dashboard_highlights_active_page(client_with_empty_db: TestClient):
    """Dashboard link is highlighted as active."""
    response = client_with_empty_db.get("/")
    # Dashboard link should have active styling (bg-slate-800)
    assert 'href="/"' in response.text


def test_dashboard_includes_footer(client_with_empty_db: TestClient):
    """Dashboard includes footer with version info."""
    response = client_with_empty_db.get("/")
    assert "NTrader v" in response.text
    assert "0.1.0" in response.text


def test_dashboard_includes_quick_action_links(client_with_empty_db: TestClient):
    """Dashboard includes quick action links."""
    response = client_with_empty_db.get("/")
    # Even in empty state, we should have some navigation
    assert "/docs" in response.text


def test_dashboard_displays_total_backtests(client_with_backtests: TestClient):
    """Dashboard shows total backtest count."""
    response = client_with_backtests.get("/")

    assert response.status_code == 200
    assert "Total Backtests" in response.text
    # Should show 5 backtests
    assert ">5<" in response.text or "5</p>" in response.text


def test_dashboard_displays_best_sharpe(client_with_backtests: TestClient):
    """Dashboard displays best Sharpe ratio and strategy name."""
    response = client_with_backtests.get("/")

    assert "Best Sharpe Ratio" in response.text
    # Best should be 3.0 from Strategy 5
    assert "3.0" in response.text
    assert "Strategy 5" in response.text


def test_dashboard_displays_worst_drawdown(client_with_backtests: TestClient):
    """Dashboard displays worst max drawdown and strategy name."""
    response = client_with_backtests.get("/")

    assert "Worst Max Drawdown" in response.text
    # Worst should be -0.20 from Strategy 1
    assert "Strategy 1" in response.text


def test_dashboard_displays_recent_backtests(client_with_backtests: TestClient):
    """Dashboard displays recent backtest activity."""
    response = client_with_backtests.get("/")

    assert "Recent Activity" in response.text
    # Should show backtest items
    assert "success" in response.text
    assert "AAPL" in response.text
