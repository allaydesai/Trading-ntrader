"""Component tests for explorer REST and UI routes."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_catalog_list, get_default_catalog, get_metadata_service
from src.api.web import app
from src.db.models.catalog_instrument import CatalogInstrument


def _make_instrument(**overrides) -> CatalogInstrument:
    """Create a CatalogInstrument with defaults."""
    defaults = {
        "id": 1,
        "ticker": "AAPL",
        "nautilus_id": "AAPL.XNAS",
        "asset_class": "STOCK",
        "catalog_name": "us_stocks",
        "exchange": "XNAS",
        "name": "Apple Inc.",
        "bar_count_daily": 1250,
        "bar_count_hourly": 8750,
        "bar_count_30min": 15625,
        "bar_count_5min": 93750,
        "bar_count_minute": 468750,
        "date_range_start": datetime(2020, 1, 2, tzinfo=timezone.utc),
        "date_range_end": datetime(2025, 12, 31, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    inst = MagicMock(spec=CatalogInstrument)
    for k, v in defaults.items():
        setattr(inst, k, v)
    return inst


@pytest.fixture
def mock_metadata_service():
    """Create a mock MetadataService."""
    service = AsyncMock()
    service.list_instruments_with_search = AsyncMock(
        return_value=(
            [_make_instrument(), _make_instrument(id=2, ticker="MSFT", name="Microsoft Corp")],
            2,
        )
    )
    service.count_asset_classes = AsyncMock(return_value={"STOCK": 2})
    return service


@pytest.fixture
def client(mock_metadata_service):
    """Get test client with mocked dependencies."""
    app.dependency_overrides[get_metadata_service] = lambda: mock_metadata_service
    app.dependency_overrides[get_catalog_list] = lambda: ["us_stocks", "crypto"]
    app.dependency_overrides[get_default_catalog] = lambda: "us_stocks"

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_metadata_service, None)
        app.dependency_overrides.pop(get_catalog_list, None)
        app.dependency_overrides.pop(get_default_catalog, None)


@pytest.mark.component
class TestExplorerRestEndpoint:
    """Tests for GET /api/explorer/tickers."""

    def test_returns_200_with_tickers(self, client, mock_metadata_service):
        response = client.get("/api/explorer/tickers?catalog=us_stocks")
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 2
        assert len(data["tickers"]) == 2
        assert data["tickers"][0]["ticker"] == "AAPL"

    def test_response_shape(self, client):
        response = client.get("/api/explorer/tickers?catalog=us_stocks")
        data = response.json()
        assert "tickers" in data
        assert "total_count" in data
        assert "page" in data
        assert "page_size" in data
        assert "total_pages" in data

    def test_ticker_row_fields(self, client):
        response = client.get("/api/explorer/tickers?catalog=us_stocks")
        row = response.json()["tickers"][0]
        assert row["ticker"] == "AAPL"
        assert row["asset_class"] == "STOCK"
        assert row["bar_count_daily"] == 1250
        assert row["bar_count_5min"] == 93750
        assert "coverage_pct" in row

    def test_ticker_row_includes_30min(self, client):
        """Story 4.1 AC4: REST rows carry bar_count_30min (parity with the UI row)."""
        response = client.get("/api/explorer/tickers?catalog=us_stocks")
        row = response.json()["tickers"][0]
        assert row["bar_count_30min"] == 15625

    def test_search_param_forwarded(self, client, mock_metadata_service):
        client.get("/api/explorer/tickers?catalog=us_stocks&search=AA")
        call_kwargs = mock_metadata_service.list_instruments_with_search.call_args.kwargs
        assert call_kwargs["search"] == "AA"

    def test_asset_class_param_forwarded(self, client, mock_metadata_service):
        client.get("/api/explorer/tickers?catalog=us_stocks&asset_class=STOCK")
        call_kwargs = mock_metadata_service.list_instruments_with_search.call_args.kwargs
        assert call_kwargs["asset_class"] == "STOCK"

    def test_pagination_params(self, client, mock_metadata_service):
        client.get("/api/explorer/tickers?catalog=us_stocks&page=3")
        call_kwargs = mock_metadata_service.list_instruments_with_search.call_args.kwargs
        assert call_kwargs["offset"] == 50  # (3-1) * 25

    def test_requires_catalog_param(self, client):
        response = client.get("/api/explorer/tickers")
        assert response.status_code == 422

    def test_empty_results(self, client, mock_metadata_service):
        mock_metadata_service.list_instruments_with_search.return_value = ([], 0)
        response = client.get("/api/explorer/tickers?catalog=us_stocks")
        data = response.json()
        assert data["total_count"] == 0
        assert data["tickers"] == []
        assert data["total_pages"] == 0


@pytest.mark.component
class TestExplorerUIPage:
    """Tests for GET /explorer (full page)."""

    def test_returns_200(self, client):
        response = client.get("/explorer")
        assert response.status_code == 200

    def test_contains_explorer_title(self, client):
        response = client.get("/explorer")
        assert "Data Explorer" in response.text

    def test_nav_explorer_active(self, client):
        response = client.get("/explorer")
        # Explorer link should have active styling
        assert 'href="/explorer"' in response.text

    def test_catalog_selector_rendered(self, client):
        response = client.get("/explorer")
        assert "us_stocks" in response.text
        assert "crypto" in response.text

    def test_ticker_rows_rendered(self, client):
        response = client.get("/explorer")
        assert "AAPL" in response.text
        assert "MSFT" in response.text

    def test_asset_class_badge(self, client):
        response = client.get("/explorer")
        assert "Stock" in response.text

    def test_search_input_rendered(self, client):
        response = client.get("/explorer")
        assert 'name="search"' in response.text

    def test_breadcrumbs_rendered(self, client):
        response = client.get("/explorer")
        assert "Explorer" in response.text
        assert "us_stocks" in response.text

    def test_breadcrumb_not_duplicated_on_full_page(self, client):
        # The full page includes ticker_list.html; the OOB breadcrumb block must
        # be suppressed there so the breadcrumb id renders exactly once.
        response = client.get("/explorer")
        assert response.text.count('id="explorer-breadcrumb"') == 1
        assert "hx-swap-oob" not in response.text

    def test_deep_link_catalog_param(self, client, mock_metadata_service):
        client.get("/explorer?catalog=crypto")
        call_kwargs = mock_metadata_service.list_instruments_with_search.call_args.kwargs
        assert call_kwargs["catalog_name"] == "crypto"

    def test_deep_link_search_param(self, client, mock_metadata_service):
        client.get("/explorer?search=MS")
        call_kwargs = mock_metadata_service.list_instruments_with_search.call_args.kwargs
        assert call_kwargs["search"] == "MS"


@pytest.mark.component
class TestExplorerTickerListFragment:
    """Tests for GET /explorer/ticker-list (HTMX fragment)."""

    def test_returns_200(self, client):
        response = client.get("/explorer/ticker-list?catalog=us_stocks")
        assert response.status_code == 200

    def test_is_fragment_not_full_page(self, client):
        response = client.get("/explorer/ticker-list?catalog=us_stocks")
        # Fragment should NOT have html/body tags
        assert "<html" not in response.text
        assert "<body" not in response.text

    def test_contains_ticker_data(self, client):
        response = client.get("/explorer/ticker-list?catalog=us_stocks")
        assert "AAPL" in response.text
        assert "Apple Inc." in response.text

    def test_requires_catalog(self, client):
        response = client.get("/explorer/ticker-list")
        assert response.status_code == 422

    def test_oob_breadcrumb_present(self, client):
        # Fragment must carry an out-of-band breadcrumb so switching catalog via
        # the dropdown keeps the header breadcrumb in sync.
        response = client.get("/explorer/ticker-list?catalog=us_stocks")
        assert 'id="explorer-breadcrumb"' in response.text
        assert 'hx-swap-oob="true"' in response.text

    def test_oob_breadcrumb_reflects_selected_catalog(self, client):
        response = client.get("/explorer/ticker-list?catalog=crypto")
        # The OOB breadcrumb should label the newly-selected catalog.
        assert "crypto" in response.text

    def test_30min_column_header_rendered(self, client):
        """Story 4.1 AC2: the browse table header lists all five native timeframes."""
        response = client.get("/explorer/ticker-list?catalog=us_stocks")
        assert "D / 1H / 30m / 5m / 1m" in response.text

    def test_30min_bar_count_value_rendered(self, client, mock_metadata_service):
        """Story 4.1 AC1/AC2: an ETF row shows its 30min bar count in the row."""
        mock_metadata_service.list_instruments_with_search.return_value = (
            [_make_instrument(ticker="SPY", asset_class="ETF", bar_count_30min=333)],
            1,
        )
        response = client.get("/explorer/ticker-list?catalog=us_stocks&asset_class=ETF")
        assert "SPY" in response.text
        # 333 formats verbatim (< 1000) — proves the 30min column value is emitted.
        assert "333" in response.text

    def test_etf_filter_forwarded_to_service(self, client, mock_metadata_service):
        """Story 4.1 AC1: ETFs are filterable from Stocks via asset_class."""
        client.get("/explorer/ticker-list?catalog=us_stocks&asset_class=ETF")
        call_kwargs = mock_metadata_service.list_instruments_with_search.call_args.kwargs
        assert call_kwargs["asset_class"] == "ETF"

    def test_empty_search_message(self, client, mock_metadata_service):
        mock_metadata_service.list_instruments_with_search.return_value = ([], 0)
        response = client.get("/explorer/ticker-list?catalog=us_stocks&search=ZZZZZ")
        assert "No tickers found" in response.text

    def test_pagination_rendered(self, client, mock_metadata_service):
        # Simulate many results requiring pagination
        mock_metadata_service.list_instruments_with_search.return_value = (
            [_make_instrument()],
            100,
        )
        response = client.get("/explorer/ticker-list?catalog=us_stocks")
        assert "Page 1 of" in response.text
        assert "Next" in response.text

    def test_empty_catalog_message(self, client, mock_metadata_service):
        """AC #6: empty catalog renders AC-worded message + import command example."""
        mock_metadata_service.list_instruments_with_search.return_value = ([], 0)
        response = client.get("/explorer/ticker-list?catalog=us_stocks")
        text = response.text
        assert "No data in catalog 'us_stocks'. Run an import to get started." in text
        assert "ntrader import --format firstrate --catalog us_stocks /path/to/data" in text

    def test_empty_search_preserves_query_in_message(self, client, mock_metadata_service):
        """AC #5: zero-match search preserves the query in the empty-state message."""
        mock_metadata_service.list_instruments_with_search.return_value = ([], 0)
        response = client.get("/explorer/ticker-list?catalog=us_stocks&search=ZZZZZ")
        assert "No tickers found for 'ZZZZZ'" in response.text


@pytest.mark.component
class TestExplorerAccessibility:
    """AC #10, #11: keyboard tab order, focus rings, ARIA attributes."""

    def test_search_input_has_aria_label(self, client):
        response = client.get("/explorer")
        assert 'aria-label="Search tickers"' in response.text

    def test_active_asset_class_pill_has_aria_pressed_true(self, client):
        response = client.get("/explorer?asset_class=STOCK")
        assert 'aria-pressed="true"' in response.text

    def test_inactive_pills_have_aria_pressed_false(self, client):
        response = client.get("/explorer?asset_class=STOCK")
        assert 'aria-pressed="false"' in response.text

    def test_all_pill_has_aria_pressed(self, client):
        """The "All" pill has aria-pressed too (true when no filter, false otherwise)."""
        response = client.get("/explorer")
        # With no filter, All is active
        assert 'aria-pressed="true"' in response.text

    def test_ticker_rows_have_role_button_tabindex(self, client):
        response = client.get("/explorer")
        text = response.text
        assert 'role="button"' in text
        assert 'tabindex="0"' in text

    def test_ticker_row_aria_pressed_reflects_selection(self, client):
        # aria-pressed (not aria-selected): rows carry role="button", where
        # aria-selected is invalid ARIA
        response = client.get("/explorer?ticker=AAPL")
        text = response.text
        # AAPL row selected
        assert 'aria-pressed="true"' in text
        # Other rows not selected
        assert 'aria-pressed="false"' in text

    def test_chart_panel_aria_live_polite(self, client):
        response = client.get("/explorer?ticker=AAPL")
        text = response.text
        # Both chart-panel and stats-panel wrappers carry aria-live
        assert text.count('aria-live="polite"') >= 2

    def test_focus_ring_classes_present(self, client):
        response = client.get("/explorer")
        text = response.text
        # Focus rings on at least one interactive element
        assert "focus:ring-2" in text
        assert "focus:ring-blue-500" in text
        assert "focus:outline-none" in text

    def test_asset_class_pills_wrapper_class(self, client):
        """Wrapper class enables the arrow-key JS to scope its listener."""
        response = client.get("/explorer")
        assert "asset-class-pills" in response.text


@pytest.mark.component
class TestBaseHtmlErrorHandler:
    """AC #8, #9: every page must include the shared HTMX error-handler snippet."""

    def test_base_html_includes_error_handler(self, client):
        response = client.get("/explorer")
        text = response.text
        # Handler identifiable by unique marker comment/id
        assert "ntrader-htmx-error-handler" in text

    def test_error_handler_listens_for_required_events(self, client):
        response = client.get("/explorer")
        text = response.text
        assert "htmx:responseError" in text
        assert "htmx:sendError" in text

    def test_retry_header_marker_present(self, client):
        response = client.get("/explorer")
        assert "X-NTrader-Retry" in response.text


@pytest.mark.component
class TestExplorerEmptyStates:
    """Tests for empty state handling."""

    def test_no_catalogs_message(self):
        """When no catalogs exist, show guidance."""
        app.dependency_overrides[get_metadata_service] = lambda: AsyncMock()
        app.dependency_overrides[get_catalog_list] = lambda: []
        app.dependency_overrides[get_default_catalog] = lambda: ""

        try:
            test_client = TestClient(app)
            response = test_client.get("/explorer")
            assert response.status_code == 200
            assert "No Catalogs Found" in response.text
        finally:
            app.dependency_overrides.pop(get_metadata_service, None)
            app.dependency_overrides.pop(get_catalog_list, None)
            app.dependency_overrides.pop(get_default_catalog, None)
