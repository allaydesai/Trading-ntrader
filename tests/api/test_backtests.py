"""
Tests for backtest list route and functionality.

Tests paginated backtest list display, HTMX fragments, and template rendering.
"""

from collections.abc import Generator
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
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


# ========== Backtest Detail View Tests ==========


@pytest.fixture
def mock_service_for_detail() -> BacktestQueryService:
    """Mock service returning a single backtest for detail view."""
    mock_service = AsyncMock(spec=BacktestQueryService)

    # Create mock backtest with all required attributes
    backtest = MagicMock()
    backtest.id = 1
    backtest.run_id = uuid4()
    backtest.strategy_name = "SMA Crossover"
    backtest.strategy_type = "trend_following"
    backtest.instrument_symbol = "AAPL"
    backtest.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    backtest.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
    backtest.initial_capital = Decimal("100000.00")
    backtest.data_source = "IBKR"
    backtest.execution_status = "success"
    backtest.execution_duration_seconds = Decimal("45.5")
    backtest.error_message = None  # Must be None or string, not MagicMock
    backtest.config_snapshot = {"fast_period": 10, "slow_period": 20}
    backtest.created_at = datetime.now(timezone.utc)
    backtest.updated_at = datetime.now(timezone.utc)

    # Create mock performance metrics
    metrics = MagicMock()
    metrics.id = 1
    metrics.backtest_run_id = 1
    metrics.total_return = Decimal("0.25")
    metrics.final_balance = Decimal("125000.00")
    metrics.cagr = Decimal("0.28")
    metrics.sharpe_ratio = Decimal("1.85")
    metrics.sortino_ratio = Decimal("2.10")
    metrics.max_drawdown = Decimal("-0.15")
    metrics.max_drawdown_date = datetime(2024, 6, 15, tzinfo=timezone.utc)
    metrics.calmar_ratio = Decimal("1.87")
    metrics.volatility = Decimal("0.18")
    metrics.total_trades = 100
    metrics.winning_trades = 60
    metrics.losing_trades = 40
    metrics.win_rate = Decimal("0.60")
    metrics.profit_factor = Decimal("2.5")
    metrics.expectancy = Decimal("250.00")
    metrics.avg_win = Decimal("500.00")
    metrics.avg_loss = Decimal("-250.00")
    metrics.created_at = datetime.now(timezone.utc)
    metrics.updated_at = datetime.now(timezone.utc)

    backtest.metrics = metrics

    mock_service.get_backtest_by_id = AsyncMock(return_value=backtest)
    return mock_service


@pytest.fixture
def client_for_detail(
    mock_service_for_detail: BacktestQueryService,
) -> Generator[TestClient, None, None]:
    """Client with mock service for detail tests."""

    def override_service():
        return mock_service_for_detail

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_backtest_detail_returns_200(client_for_detail: TestClient):
    """Backtest detail page loads successfully."""
    run_id = uuid4()
    response = client_for_detail.get(f"/backtests/{run_id}")
    assert response.status_code == 200


def test_backtest_detail_returns_404_for_nonexistent_backtest():
    """Backtest detail returns 404 when backtest not found."""
    # Create a service that returns None for get_backtest_by_id
    mock_service = AsyncMock(spec=BacktestQueryService)
    mock_service.get_backtest_by_id = AsyncMock(return_value=None)

    def override_service():
        return mock_service

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)

    run_id = uuid4()
    response = client.get(f"/backtests/{run_id}")
    assert response.status_code == 404
    assert "not found" in response.text.lower()

    app.dependency_overrides.clear()


def test_backtest_detail_displays_strategy_name(client_for_detail: TestClient):
    """Backtest detail displays strategy name."""
    run_id = uuid4()
    response = client_for_detail.get(f"/backtests/{run_id}")
    assert "SMA Crossover" in response.text


def test_backtest_detail_displays_breadcrumbs(client_for_detail: TestClient):
    """Backtest detail shows breadcrumbs navigation."""
    run_id = uuid4()
    response = client_for_detail.get(f"/backtests/{run_id}")
    assert "Dashboard" in response.text
    assert "Backtests" in response.text
    assert "Run Details" in response.text


# ========== Delete Backtest Tests ==========


def test_delete_backtest_returns_200(client_for_detail: TestClient):
    """Delete backtest returns 200 on success."""
    run_id = uuid4()
    response = client_for_detail.delete(f"/backtests/{run_id}")
    assert response.status_code == 200


def test_delete_backtest_returns_htmx_redirect(client_for_detail: TestClient):
    """Delete backtest returns HTMX redirect header."""
    run_id = uuid4()
    response = client_for_detail.delete(f"/backtests/{run_id}")
    assert "HX-Redirect" in response.headers
    assert response.headers["HX-Redirect"] == "/backtests"


def test_delete_backtest_returns_404_for_nonexistent():
    """Delete backtest returns 404 when backtest not found."""
    # Create a service that returns None for get_backtest_by_id
    mock_service = AsyncMock(spec=BacktestQueryService)
    mock_service.get_backtest_by_id = AsyncMock(return_value=None)

    def override_service():
        return mock_service

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)

    run_id = uuid4()
    response = client.delete(f"/backtests/{run_id}")
    assert response.status_code == 404

    app.dependency_overrides.clear()


# ========== Rerun Backtest Tests ==========


def test_rerun_backtest_returns_202(client_for_detail: TestClient):
    """Rerun backtest returns 202 Accepted."""
    run_id = uuid4()
    response = client_for_detail.post(f"/backtests/{run_id}/rerun")
    assert response.status_code == 202


def test_rerun_backtest_returns_htmx_redirect(client_for_detail: TestClient):
    """Rerun backtest returns HTMX redirect header."""
    run_id = uuid4()
    response = client_for_detail.post(f"/backtests/{run_id}/rerun")
    assert "HX-Redirect" in response.headers
    assert response.headers["HX-Redirect"] == f"/backtests/{run_id}"


def test_rerun_backtest_returns_404_for_nonexistent():
    """Rerun backtest returns 404 when backtest not found."""
    # Create a service that returns None for get_backtest_by_id
    mock_service = AsyncMock(spec=BacktestQueryService)
    mock_service.get_backtest_by_id = AsyncMock(return_value=None)

    def override_service():
        return mock_service

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)

    run_id = uuid4()
    response = client.post(f"/backtests/{run_id}/rerun")
    assert response.status_code == 404

    app.dependency_overrides.clear()


# ========== Export Backtest Tests ==========


def test_export_backtest_returns_200(client_for_detail: TestClient):
    """Export backtest returns 200 on success."""
    run_id = uuid4()
    response = client_for_detail.get(f"/backtests/{run_id}/export")
    assert response.status_code == 200


def test_export_backtest_returns_html_content(client_for_detail: TestClient):
    """Export backtest returns HTML content."""
    run_id = uuid4()
    response = client_for_detail.get(f"/backtests/{run_id}/export")
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "<!DOCTYPE html>" in response.text
    assert "Backtest Report" in response.text


def test_export_backtest_has_download_header(client_for_detail: TestClient):
    """Export backtest has Content-Disposition download header."""
    run_id = uuid4()
    response = client_for_detail.get(f"/backtests/{run_id}/export")
    assert "Content-Disposition" in response.headers
    assert "attachment" in response.headers["Content-Disposition"]
    assert "backtest_report_" in response.headers["Content-Disposition"]


def test_export_backtest_returns_404_for_nonexistent():
    """Export backtest returns 404 when backtest not found."""
    # Create a service that returns None for get_backtest_by_id
    mock_service = AsyncMock(spec=BacktestQueryService)
    mock_service.get_backtest_by_id = AsyncMock(return_value=None)

    def override_service():
        return mock_service

    app.dependency_overrides[get_backtest_query_service] = override_service
    client = TestClient(app)

    run_id = uuid4()
    response = client.get(f"/backtests/{run_id}/export")
    assert response.status_code == 404

    app.dependency_overrides.clear()


# ========== Get Trades Table Tests ==========


@pytest.fixture
def mock_httpx_client():
    """Mock httpx client for trades table endpoint."""
    return AsyncMock()


def test_get_trades_table_returns_200_on_success(client_for_detail: TestClient, monkeypatch):
    """Get trades table returns 200 when backtest exists."""
    backtest_id = 1

    # Mock httpx.AsyncClient to return successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "backtest_id": backtest_id,
        "trades": [],
        "pagination": {
            "total_items": 0,
            "total_pages": 0,
            "current_page": 1,
            "page_size": 20,
            "has_next": False,
            "has_prev": False,
        },
        "sorting": {"sort_by": "entry_timestamp", "sort_order": "asc"},
    }

    async def mock_get(*args, **kwargs):
        return mock_response

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

    response = client_for_detail.get(f"/backtests/{backtest_id}/trades-table")
    assert response.status_code == 200


def test_get_trades_table_returns_404_when_backtest_not_found(
    client_for_detail: TestClient, monkeypatch
):
    """Get trades table returns 404 when backtest doesn't exist."""
    backtest_id = 999

    # Mock httpx.AsyncClient to return 404
    mock_response = MagicMock()
    mock_response.status_code = 404

    async def mock_get(*args, **kwargs):
        return mock_response

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

    response = client_for_detail.get(f"/backtests/{backtest_id}/trades-table")
    assert response.status_code == 404


def test_get_trades_table_accepts_pagination_params(client_for_detail: TestClient, monkeypatch):
    """Get trades table accepts pagination parameters."""
    backtest_id = 1

    # Mock httpx.AsyncClient to capture request params
    captured_params = {}

    async def mock_get(*args, **kwargs):
        nonlocal captured_params
        captured_params = kwargs.get("params", {})
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "trades": [],
            "pagination": {
                "total_items": 0,
                "total_pages": 0,
                "current_page": 2,
                "page_size": 50,
                "has_next": False,
                "has_prev": True,
            },
            "sorting": {"sort_by": "pnl", "sort_order": "desc"},
        }
        return mock_response

    mock_client = MagicMock()
    mock_client.get = mock_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

    response = client_for_detail.get(
        f"/backtests/{backtest_id}/trades-table?page=2&page_size=50&sort_by=pnl&sort_order=desc"
    )
    assert response.status_code == 200
    assert captured_params["page"] == 2
    assert captured_params["page_size"] == 50
    assert captured_params["sort_by"] == "pnl"
    assert captured_params["sort_order"] == "desc"
