"""Component tests for stats panel REST and UI routes (Story 2-3)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import (
    get_catalog_list,
    get_data_catalog_service,
    get_default_catalog,
    get_metadata_service,
)
from src.api.web import app
from src.db.models.catalog_instrument import CatalogInstrument
from src.services.exceptions import DataNotFoundError


def _make_instrument(**overrides) -> CatalogInstrument:
    """Create a CatalogInstrument mock with defaults."""
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


def _make_bar(low: float, high: float):
    """Create a mock Nautilus Bar object exposing low/high .as_double()."""
    bar = MagicMock()
    bar.low.as_double.return_value = low
    bar.high.as_double.return_value = high
    return bar


@pytest.fixture
def mock_metadata_service():
    service = AsyncMock()
    service.get_instrument = AsyncMock(return_value=_make_instrument())
    service.list_instruments_with_search = AsyncMock(return_value=([], 0))
    service.count_asset_classes = AsyncMock(return_value={})
    return service


@pytest.fixture
def mock_catalog_service():
    service = MagicMock()
    service.query_bars.return_value = [
        _make_bar(low=99.5, high=101.25),
        _make_bar(low=97.10, high=103.75),
        _make_bar(low=98.0, high=102.0),
    ]
    return service


@pytest.fixture
def client(mock_metadata_service, mock_catalog_service):
    app.dependency_overrides[get_metadata_service] = lambda: mock_metadata_service
    app.dependency_overrides[get_data_catalog_service] = lambda: mock_catalog_service
    app.dependency_overrides[get_catalog_list] = lambda: ["us_stocks"]
    app.dependency_overrides[get_default_catalog] = lambda: "us_stocks"
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_metadata_service, None)
        app.dependency_overrides.pop(get_data_catalog_service, None)
        app.dependency_overrides.pop(get_catalog_list, None)
        app.dependency_overrides.pop(get_default_catalog, None)


@pytest.mark.component
class TestStatsRestEndpoint:
    """Tests for GET /api/explorer/ticker/{ticker}/stats."""

    def test_returns_200_with_stats(self, client):
        response = client.get("/api/explorer/ticker/AAPL/stats?catalog=us_stocks&tf=D")
        assert response.status_code == 200
        data = response.json()
        assert data["ticker"] == "AAPL"
        assert data["nautilus_id"] == "AAPL.XNAS"
        assert data["bar_count_daily"] == 1250
        assert data["bar_count_hourly"] == 8750
        assert data["bar_count_5min"] == 93750
        assert data["bar_count_minute"] == 468750
        assert data["price_min"] == 97.10
        assert data["price_max"] == 103.75
        assert data["active_tf"] == "D"
        assert data["date_range_start"].startswith("2020-01-02")
        assert data["date_range_end"].startswith("2025-12-31")

    def test_default_timeframe_is_daily(self, client, mock_catalog_service):
        client.get("/api/explorer/ticker/AAPL/stats?catalog=us_stocks")
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["bar_type_spec"] == "1-DAY-LAST"

    def test_invalid_tf_coerced_to_daily(self, client, mock_catalog_service):
        response = client.get("/api/explorer/ticker/AAPL/stats?catalog=us_stocks&tf=garbage")
        assert response.status_code == 200
        assert response.json()["active_tf"] == "D"
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["bar_type_spec"] == "1-DAY-LAST"

    def test_active_tf_echoed(self, client, mock_catalog_service):
        response = client.get("/api/explorer/ticker/AAPL/stats?catalog=us_stocks&tf=1H")
        assert response.status_code == 200
        assert response.json()["active_tf"] == "1H"
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["bar_type_spec"] == "1-HOUR-LAST"

    def test_unknown_ticker_returns_404(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = None
        response = client.get("/api/explorer/ticker/ZZZZ/stats?catalog=us_stocks&tf=D")
        assert response.status_code == 404

    def test_price_range_null_when_data_not_found(self, client, mock_catalog_service):
        mock_catalog_service.query_bars.side_effect = DataNotFoundError(
            "AAPL.XNAS",
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        response = client.get("/api/explorer/ticker/AAPL/stats?catalog=us_stocks&tf=1m")
        assert response.status_code == 200
        data = response.json()
        assert data["price_min"] is None
        assert data["price_max"] is None

    def test_uses_full_date_range(self, client, mock_catalog_service):
        """Stats intentionally does NOT window — scans the full date range."""
        client.get("/api/explorer/ticker/AAPL/stats?catalog=us_stocks&tf=D")
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["start"] == datetime(2020, 1, 2, tzinfo=timezone.utc)
        assert call_kwargs["end"] == datetime(2025, 12, 31, tzinfo=timezone.utc)

    def test_requires_catalog_param(self, client):
        response = client.get("/api/explorer/ticker/AAPL/stats")
        assert response.status_code == 422

    def test_ticker_resolved_via_metadata(self, client, mock_metadata_service):
        client.get("/api/explorer/ticker/AAPL/stats?catalog=us_stocks&tf=D")
        mock_metadata_service.get_instrument.assert_called_once_with("us_stocks", "AAPL")


@pytest.mark.component
class TestStatsPanelUIRoute:
    """Tests for GET /explorer/stats-panel (HTMX fragment)."""

    def test_returns_200(self, client):
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert response.status_code == 200

    def test_is_fragment_not_full_page(self, client):
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert "<html" not in response.text
        assert "<body" not in response.text

    def test_fragment_has_no_stats_panel_id_wrapper(self, client):
        """Fragment must not re-declare #stats-panel — the container owns that id."""
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert 'id="stats-panel"' not in response.text

    def test_contains_seven_card_labels(self, client):
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        text = response.text
        assert "Date Range" in text
        assert "Daily Bars" in text
        assert "1-Hour Bars" in text
        assert "5-Min Bars" in text
        assert "1-Min Bars" in text
        assert "Price Range" in text
        assert "Nautilus ID" in text

    def test_price_range_renders_dash_when_null(self, client, mock_catalog_service):
        mock_catalog_service.query_bars.side_effect = DataNotFoundError(
            "AAPL.XNAS",
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=1m")
        # Isolate the Price Range card (label → `timeframe` subtitle is unique to this card)
        text = response.text
        start = text.index("Price Range")
        end = text.index("timeframe", start)
        price_range_section = text[start:end]
        assert "—" in price_range_section
        assert "$" not in price_range_section

    def test_active_tf_highlighted(self, client):
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=1H")
        # Active tf highlight uses ring utility
        assert "ring-blue-500" in response.text

    def test_ticker_not_found_returns_404(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = None
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=ZZZZ&tf=D")
        assert response.status_code == 404

    def test_requires_catalog_and_ticker(self, client):
        response = client.get("/explorer/stats-panel")
        assert response.status_code == 422


@pytest.mark.component
class TestExplorerPageStatsAutoload:
    """Tests for #stats-panel auto-load on explorer page refresh with ?ticker=."""

    def test_stats_panel_has_hx_trigger_when_ticker_selected(self, client):
        response = client.get("/explorer/?catalog=us_stocks&ticker=AAPL&tf=1H")
        text = response.text
        assert 'hx-get="/explorer/stats-panel"' in text
        assert 'hx-trigger="load"' in text
        # hx-vals must be JSON-safe (|tojson on selected_ticker, etc.)
        assert '"ticker": "AAPL"' in text
        assert '"tf": "1H"' in text
        assert '"catalog": "us_stocks"' in text

    def test_stats_panel_has_no_hx_trigger_when_no_ticker(self, client):
        response = client.get("/explorer/?catalog=us_stocks")
        text = response.text
        # The #stats-panel div exists but without hx-get (guarded by selected_ticker)
        assert 'id="stats-panel"' in text
        assert 'hx-get="/explorer/stats-panel"' not in text


@pytest.mark.component
class TestStatsPanelSkeleton:
    """Tests for the 7-card skeleton rendered on deep-link auto-load (AC #3)."""

    def test_skeleton_rendered_when_ticker_selected(self, client):
        """Deep-link with ?ticker= must render seven animate-pulse cards inside #stats-panel."""
        response = client.get("/explorer/?catalog=us_stocks&ticker=AAPL&tf=D")
        text = response.text
        # Seven skeleton cards
        assert text.count("stats-skeleton-card") == 7
        # Animate-pulse classes present inside the cards
        assert "animate-pulse bg-slate-800" in text

    def test_skeleton_absent_when_no_ticker(self, client):
        """No auto-load, no skeleton."""
        response = client.get("/explorer/?catalog=us_stocks")
        text = response.text
        assert "stats-skeleton-card" not in text

    def test_oob_response_does_not_contain_skeleton(self, client):
        """OOB chart-panel response renders the real stats grid; skeleton must NOT appear there."""
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        text = response.text
        # OOB response carries the real stats grid (Date Range/Daily Bars/etc.)
        assert "Date Range" in text
        # But must NOT carry skeleton placeholders
        assert "stats-skeleton-card" not in text

    def test_standalone_stats_fragment_does_not_contain_skeleton(self, client):
        """Real stats fragment replaces skeleton; it must not contain skeleton markup."""
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert "stats-skeleton-card" not in response.text


@pytest.mark.component
class TestStatsPanelEpic4Placeholder:
    """Tests for the Epic 4 supplementary placeholder note (AC #12)."""

    def test_epic4_note_present_in_stats_fragment(self, client):
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert "Dividends and stock splits available in" in response.text
        assert "Epic 4" in response.text

    def test_no_details_tag_in_stats_fragment(self, client):
        response = client.get("/explorer/stats-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert "<details" not in response.text
