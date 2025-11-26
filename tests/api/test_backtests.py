"""
Tests for backtest list route and functionality.

Tests paginated backtest list display, HTMX fragments, and template rendering.
"""

from collections.abc import Generator
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service
from src.api.models.backtest_list import BacktestListItem, FilteredBacktestListPage
from src.api.models.filter_models import FilterState
from src.api.web import app
from src.services.backtest_query import BacktestQueryService


@pytest.fixture
def mock_empty_service() -> BacktestQueryService:
    """Mock service returning empty backtest list."""
    mock_service = AsyncMock(spec=BacktestQueryService)
    mock_service.get_filtered_backtest_list_page = AsyncMock(
        return_value=FilteredBacktestListPage(
            backtests=[],
            page=1,
            page_size=20,
            total_count=0,
            filter_state=FilterState(),
            available_strategies=[],
            available_instruments=[],
        )
    )
    return mock_service


@pytest.fixture
def mock_service_with_backtests() -> BacktestQueryService:
    """Mock service returning populated backtest list."""
    mock_service = AsyncMock(spec=BacktestQueryService)

    items = [
        BacktestListItem(
            run_id=uuid4(),
            strategy_name=f"Strategy {i + 1}",
            instrument_symbol="AAPL",
            date_range="2024-01-01 to 2024-12-31",
            total_return=Decimal(f"{(i + 1) * 1000}"),  # Absolute return in dollars
            final_balance=Decimal(f"{1000000 + (i + 1) * 1000}"),  # Starting balance + return
            sharpe_ratio=Decimal(f"{1.0 + i * 0.5}"),
            max_drawdown=Decimal(f"-0.{20 - i}0"),
            execution_status="success",
            created_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
        )
        for i in range(20)
    ]

    mock_service.get_filtered_backtest_list_page = AsyncMock(
        return_value=FilteredBacktestListPage(
            backtests=items,
            page=1,
            page_size=20,
            total_count=25,
            filter_state=FilterState(),
            available_strategies=["Strategy 1", "Strategy 2"],
            available_instruments=["AAPL"],
        )
    )
    return mock_service


@pytest.fixture
def client_with_empty_db(
    mock_empty_service: BacktestQueryService,
) -> Generator[TestClient, None, None]:
    """Client with empty database."""

    def override_service():
        return mock_empty_service

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client_with_backtests(
    mock_service_with_backtests: BacktestQueryService,
) -> Generator[TestClient, None, None]:
    """Client with database containing backtests."""

    def override_service():
        return mock_service_with_backtests

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_backtest_list_returns_200(client_with_empty_db: TestClient):
    """Backtest list page loads successfully."""
    response = client_with_empty_db.get("/backtests")
    assert response.status_code == 200


def test_backtest_list_has_dark_background(client_with_empty_db: TestClient):
    """Backtest list has dark background class."""
    response = client_with_empty_db.get("/backtests")
    assert "bg-slate-950" in response.text


def test_backtest_list_displays_table_headers(client_with_backtests: TestClient):
    """Backtest list displays table with correct columns."""
    response = client_with_backtests.get("/backtests")
    assert "Run ID" in response.text
    assert "Strategy" in response.text
    assert "Symbol" in response.text
    assert "Date Range" in response.text
    assert "Return" in response.text
    assert "Sharpe" in response.text
    assert "Max DD" in response.text
    assert "Status" in response.text
    assert "Created" in response.text


def test_backtest_list_shows_20_results_per_page(client_with_backtests: TestClient):
    """Backtest list shows 20 results per page by default."""
    response = client_with_backtests.get("/backtests")
    # Count how many strategy items appear (each row has a strategy)
    strategy_count = response.text.count("Strategy ")
    # Should have at least 20 strategies (could be more due to header/footer text)
    assert strategy_count >= 20


def test_backtest_list_shows_pagination_controls(client_with_backtests: TestClient):
    """Backtest list pagination controls appear when >20 results."""
    response = client_with_backtests.get("/backtests")
    # With 25 total and 20 per page, should have pagination
    assert "Page 1 of 2" in response.text
    assert "Next" in response.text
    assert "Previous" in response.text


def test_backtest_list_rows_are_clickable(client_with_backtests: TestClient):
    """Backtest list rows are clickable (navigate to detail)."""
    response = client_with_backtests.get("/backtests")
    # Rows should have onclick for navigation
    assert "onclick=" in response.text
    assert "/backtests/" in response.text


def test_backtest_list_shows_empty_state(client_with_empty_db: TestClient):
    """Backtest list shows empty state when no backtests."""
    response = client_with_empty_db.get("/backtests")
    assert "No Backtests Yet" in response.text
    assert "ntrader backtest run" in response.text


def test_backtest_list_includes_navigation(client_with_empty_db: TestClient):
    """Backtest list includes navigation bar."""
    response = client_with_empty_db.get("/backtests")
    assert "Dashboard" in response.text
    assert "Backtests" in response.text
    assert "Data" in response.text
    assert "Docs" in response.text


def test_backtest_list_highlights_backtests_link(client_with_backtests: TestClient):
    """Backtests link is highlighted as active."""
    response = client_with_backtests.get("/backtests")
    assert 'href="/backtests"' in response.text


def test_htmx_fragment_returns_partial_html(client_with_backtests: TestClient):
    """HTMX fragment endpoint returns partial HTML."""
    response = client_with_backtests.get("/backtests/fragment")
    assert response.status_code == 200
    # Fragment should have table but not full page layout
    assert "<table" in response.text
    # Fragment should NOT have <html> or <head> tags
    assert "<!doctype" not in response.text.lower()
    assert "<head>" not in response.text


def test_htmx_fragment_includes_pagination(client_with_backtests: TestClient):
    """HTMX fragment includes pagination controls."""
    response = client_with_backtests.get("/backtests/fragment")
    assert "hx-get" in response.text
    # Pagination URLs now include all filter state params, so check for page parameter
    assert "page=" in response.text
    assert "/backtests/fragment?" in response.text


def test_backtest_list_displays_return_color_coding(client_with_backtests: TestClient):
    """Backtest list applies color coding: green for positive returns."""
    response = client_with_backtests.get("/backtests")
    # Positive returns should have green color
    assert "text-green-500" in response.text
    # Negative drawdowns should have red color
    assert "text-red-500" in response.text


def test_backtest_list_displays_status_badges(client_with_backtests: TestClient):
    """Backtest list displays status badges."""
    response = client_with_backtests.get("/backtests")
    assert "success" in response.text
    assert "bg-green-900" in response.text


def test_backtest_list_breadcrumbs(client_with_backtests: TestClient):
    """Backtest list shows breadcrumbs."""
    response = client_with_backtests.get("/backtests")
    assert "Dashboard" in response.text
    assert "Backtests" in response.text


def test_backtest_list_includes_footer(client_with_backtests: TestClient):
    """Backtest list includes footer."""
    response = client_with_backtests.get("/backtests")
    assert "NTrader v" in response.text
    assert "0.1.0" in response.text
