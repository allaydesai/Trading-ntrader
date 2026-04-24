"""Component tests for chart panel REST and UI routes (Story 2-2)."""

from datetime import datetime, timedelta, timezone
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


def _make_bar(ts_ns: int, o: float, h: float, lo: float, c: float, vol: float):
    """Create a mock Nautilus Bar object."""
    bar = MagicMock()
    bar.ts_event = ts_ns
    bar.open.as_double.return_value = o
    bar.high.as_double.return_value = h
    bar.low.as_double.return_value = lo
    bar.close.as_double.return_value = c
    bar.volume.as_double.return_value = vol
    return bar


@pytest.fixture
def mock_metadata_service():
    """Create a mock MetadataService."""
    service = AsyncMock()
    service.get_instrument = AsyncMock(return_value=_make_instrument())
    service.list_instruments_with_search = AsyncMock(return_value=([], 0))
    service.count_asset_classes = AsyncMock(return_value={})
    return service


@pytest.fixture
def mock_catalog_service():
    """Create a mock DataCatalogService."""
    service = MagicMock()
    # Return 2 bars by default
    service.query_bars.return_value = [
        _make_bar(1704067200_000_000_000, 473.25, 475.10, 472.80, 474.50, 45e6),
        _make_bar(1704153600_000_000_000, 474.50, 476.00, 473.00, 475.80, 42e6),
    ]
    return service


@pytest.fixture
def client(mock_metadata_service, mock_catalog_service):
    """Get test client with mocked dependencies."""
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
class TestChartDataRestEndpoint:
    """Tests for GET /api/chart/catalog/{ticker}."""

    def test_returns_200_with_bars(self, client):
        response = client.get("/api/chart/catalog/AAPL?catalog=us_stocks&tf=D")
        assert response.status_code == 200
        data = response.json()
        assert data["instrument_id"] == "AAPL.XNAS"
        assert data["bar_count"] == 2
        assert len(data["bars"]) == 2

    def test_bar_format(self, client):
        response = client.get("/api/chart/catalog/AAPL?catalog=us_stocks&tf=D")
        bar = response.json()["bars"][0]
        assert bar["time"] == 1704067200
        assert bar["open"] == 473.25
        assert bar["high"] == 475.10
        assert bar["low"] == 472.80
        assert bar["close"] == 474.50
        assert bar["volume"] == 45000000

    def test_timeframe_mapping(self, client, mock_catalog_service):
        client.get("/api/chart/catalog/AAPL?catalog=us_stocks&tf=1H")
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        assert call_kwargs["bar_type_spec"] == "1-HOUR-LAST"

    def test_default_timeframe_is_daily(self, client, mock_catalog_service):
        client.get("/api/chart/catalog/AAPL?catalog=us_stocks")
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        assert call_kwargs["bar_type_spec"] == "1-DAY-LAST"

    def test_ticker_resolved_via_metadata(
        self, client, mock_metadata_service, mock_catalog_service
    ):
        client.get("/api/chart/catalog/AAPL?catalog=us_stocks&tf=D")
        mock_metadata_service.get_instrument.assert_called_once_with("us_stocks", "AAPL")
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        assert call_kwargs["instrument_id"] == "AAPL.XNAS"

    def test_empty_bars_returns_200(self, client, mock_catalog_service):
        mock_catalog_service.query_bars.return_value = []
        response = client.get("/api/chart/catalog/AAPL?catalog=us_stocks&tf=D")
        assert response.status_code == 200
        data = response.json()
        assert data["bars"] == []
        assert data["bar_count"] == 0

    def test_unknown_ticker_returns_404(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = None
        response = client.get("/api/chart/catalog/ZZZZ?catalog=us_stocks&tf=D")
        assert response.status_code == 404

    def test_requires_catalog_param(self, client):
        response = client.get("/api/chart/catalog/AAPL")
        assert response.status_code == 422

    def test_start_end_params_forwarded(self, client, mock_catalog_service):
        client.get("/api/chart/catalog/AAPL?catalog=us_stocks&tf=D&start=2024-01-01&end=2024-06-30")
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        assert call_kwargs["start"].year == 2024
        assert call_kwargs["start"].month == 1
        assert call_kwargs["end"].year == 2024
        assert call_kwargs["end"].month == 6

    def test_catalog_error_returns_empty_bars(self, client, mock_catalog_service):
        from datetime import datetime, timezone

        mock_catalog_service.query_bars.side_effect = DataNotFoundError(
            "AAPL.XNAS",
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        response = client.get("/api/chart/catalog/AAPL?catalog=us_stocks&tf=D")
        assert response.status_code == 200
        assert response.json()["bars"] == []


@pytest.mark.component
class TestChartPanelUIRoute:
    """Tests for GET /explorer/chart-panel (HTMX fragment)."""

    def test_returns_200(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert response.status_code == 200

    def test_is_fragment_not_full_page(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert "<html" not in response.text
        assert "<body" not in response.text

    def test_contains_chart_container(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert 'id="chart-container"' in response.text

    def test_contains_timeframe_buttons(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        # D should be active (bg-blue-500)
        assert "bg-blue-500" in response.text
        # All 4 timeframe labels should appear
        for label in ["D", "1H", "5m", "1m"]:
            assert label in response.text

    def test_default_timeframe_is_daily(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL")
        assert response.status_code == 200
        # D button should be active
        assert "bg-blue-500" in response.text

    def test_bars_json_in_script(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        # Should contain bars data as JSON in script
        assert "barsJson" in response.text

    def test_ticker_not_found_returns_404(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = None
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=ZZZZ&tf=D")
        assert response.status_code == 404

    def test_requires_catalog_and_ticker(self, client):
        response = client.get("/explorer/chart-panel")
        assert response.status_code == 422

    def test_oob_stats_panel_wrapper_present(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert 'id="stats-panel"' in response.text
        assert 'hx-swap-oob="innerHTML"' in response.text

    def test_oob_stats_panel_rendered_with_cards(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        text = response.text
        for label in [
            "Date Range",
            "Daily Bars",
            "1-Hour Bars",
            "5-Min Bars",
            "1-Min Bars",
            "Price Range",
            "Nautilus ID",
        ]:
            assert label in text

    def test_unavailable_tf_inline_message(
        self, client, mock_metadata_service, mock_catalog_service
    ):
        """AC #7: when bar_count for the active tf is 0, render tf-aware message."""
        mock_metadata_service.get_instrument.return_value = _make_instrument(bar_count_minute=0)
        mock_catalog_service.query_bars.return_value = []
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=1m")
        text = response.text
        # Server-rendered tf-aware no-data message
        assert "No 1m data available for AAPL" in text
        # Generic string should NOT appear in server-rendered chart container
        assert "No chart data available for this timeframe." not in text
        # 1m button still disabled after render
        assert "disabled" in text

    def test_timeframe_toolbar_wrapper_class_present(self, client):
        """Wrapper class scopes arrow-key JS on the tf toolbar (AC #10)."""
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert "timeframe-toolbar" in response.text

    def test_timeframe_buttons_have_focus_rings(self, client):
        """Every tf button shows focus rings (AC #10)."""
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        text = response.text
        assert "focus:ring-2" in text
        assert "focus:ring-blue-500" in text

    def test_active_timeframe_button_has_aria_pressed_true(self, client):
        """Active tf button carries aria-pressed=true (AC #11)."""
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=1H")
        assert 'aria-pressed="true"' in response.text

    def test_chart_container_has_role_img_and_aria_label(self, client):
        """Chart container wrapped in role=img with ticker-aware aria-label (AC #11)."""
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        text = response.text
        assert 'role="img"' in text
        assert 'aria-label="Price chart for AAPL"' in text

    def test_unavailable_tf_button_has_disabled_attr(self, client, mock_metadata_service):
        """AC #7: tf button for unavailable timeframe stays disabled."""
        mock_metadata_service.get_instrument.return_value = _make_instrument(
            bar_count_hourly=0, bar_count_minute=0, bar_count_5min=0
        )
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert "disabled" in response.text


@pytest.mark.component
class TestRunBacktestButton:
    """Story 3.2 — Run Backtest anchor inside the chart panel fragment."""

    def test_button_rendered_with_correct_url(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = _make_instrument(
            ticker="SPY",
            nautilus_id="SPY.ARCA",
            date_range_start=datetime(2003, 1, 2, tzinfo=timezone.utc),
            date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        response = client.get("/explorer/chart-panel?catalog=firstrate-research&ticker=SPY&tf=D")
        assert response.status_code == 200
        text = response.text
        # Anchor present, not a <button>
        assert 'aria-label="Run backtest for SPY"' in text
        assert "Run Backtest" in text
        # URL carries all four bridge params with the run-form timeframe shape.
        # `&` is HTML-escaped to `&amp;` inside href attributes by Jinja autoescape.
        assert "catalog=firstrate-research" in text
        assert "ticker=SPY" in text
        assert "timeframe=1-DAY" in text
        assert "start=2003-01-02" in text
        assert "end=2024-12-31" in text
        # Primary-action styling.
        assert "bg-blue-500" in text

    def test_button_absent_when_chart_panel_not_loaded(self, client):
        response = client.get("/explorer?catalog=us_stocks")
        assert response.status_code == 200
        assert "Run backtest for" not in response.text

    def test_button_url_encodes_special_ticker(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = _make_instrument(
            ticker="BRK.B",
            nautilus_id="BRK.B.NYSE",
        )
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=BRK.B&tf=D")
        assert response.status_code == 200
        assert "ticker=BRK.B" in response.text

    def test_button_hidden_when_no_bars(self, client, mock_metadata_service, mock_catalog_service):
        """Zero-bar render: anchor is gated on `run_backtest_url`, so it must not appear."""
        mock_metadata_service.get_instrument.return_value = _make_instrument(
            bar_count_daily=0,
            bar_count_hourly=0,
            bar_count_5min=0,
            bar_count_minute=0,
        )
        mock_catalog_service.query_bars.return_value = []
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert response.status_code == 200
        assert "Run backtest for" not in response.text

    def test_button_aria_label_includes_ticker(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = _make_instrument(ticker="MSFT")
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=MSFT&tf=1H")
        assert response.status_code == 200
        assert 'aria-label="Run backtest for MSFT"' in response.text

    def test_button_focus_ring(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = _make_instrument(ticker="AAPL")
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        # Pin the ring assertion to the Run Backtest anchor itself — the Story 2-4
        # focus-ring class appears on multiple elements, so a bare substring check
        # would pass even if the anchor silently lost it.
        html = response.text
        anchor_start = html.find('aria-label="Run backtest for AAPL"')
        assert anchor_start != -1, "Run Backtest anchor missing"
        # Scan a reasonable window around the anchor element for the ring class.
        anchor_tag_open = html.rfind("<a ", 0, anchor_start)
        anchor_tag_close = html.find(">", anchor_start)
        assert anchor_tag_open != -1 and anchor_tag_close != -1
        anchor_markup = html[anchor_tag_open : anchor_tag_close + 1]
        assert "focus:ring-offset-slate-950" in anchor_markup
        assert "focus:ring-2" in anchor_markup

    def test_button_threads_explorer_state(self, client, mock_metadata_service):
        mock_metadata_service.get_instrument.return_value = _make_instrument(ticker="AAPL")
        response = client.get(
            "/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D"
            "&search=A&asset_class=STOCK&sort_by=ticker&page=2"
        )
        assert response.status_code == 200
        # Parse the Run Backtest anchor's href and confirm the encoded
        # explorer_return carries every threaded state field.
        html = response.text
        anchor_start = html.find('aria-label="Run backtest for AAPL"')
        assert anchor_start != -1, "Run Backtest anchor missing"
        tag_open = html.rfind("<a ", 0, anchor_start)
        tag_close = html.find(">", anchor_start)
        anchor_markup = html[tag_open : tag_close + 1]
        assert "explorer_return=" in anchor_markup
        # explorer_return value is URL-encoded once; search/asset_class/sort_by/page
        # must all appear inside the encoded /explorer?... nested URL.
        from urllib.parse import parse_qs, unquote, urlparse

        href_start = anchor_markup.find('href="') + len('href="')
        href_end = anchor_markup.find('"', href_start)
        raw_href = anchor_markup[href_start:href_end].replace("&amp;", "&")
        explorer_return = parse_qs(urlparse(raw_href).query).get("explorer_return", [""])[0]
        decoded = unquote(explorer_return)
        assert "search=A" in decoded
        assert "asset_class=STOCK" in decoded
        assert "sort_by=ticker" in decoded
        assert "page=2" in decoded


@pytest.mark.component
class TestChartPanelWindowing:
    """Tests for windowed chart data loading."""

    def test_daily_loads_windowed_range(self, client, mock_catalog_service):
        """Daily timeframe windows to last 1825 days."""
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        assert call_kwargs["end"] == datetime(2025, 12, 31, tzinfo=timezone.utc)
        expected_start = datetime(2025, 12, 31, tzinfo=timezone.utc) - timedelta(days=1825)
        assert call_kwargs["start"] == expected_start

    def test_5min_loads_windowed_range(self, client, mock_catalog_service):
        """5-min timeframe windows to last 30 days from date_range_end.

        Instrument date_range_end = 2025-12-31, so window starts
        2025-12-01 (30 days before).
        """
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=5m")
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        # End anchored to instrument's date_range_end
        assert call_kwargs["end"] == datetime(2025, 12, 31, tzinfo=timezone.utc)
        # Start is 30 days before date_range_end
        expected_start = datetime(2025, 12, 31, tzinfo=timezone.utc) - timedelta(days=30)
        assert call_kwargs["start"] == expected_start

    def test_hourly_loads_windowed_range(self, client, mock_catalog_service):
        """Hourly timeframe windows to last 180 days."""
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=1H")
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        assert call_kwargs["end"] == datetime(2025, 12, 31, tzinfo=timezone.utc)
        expected_start = datetime(2025, 12, 31, tzinfo=timezone.utc) - timedelta(days=180)
        assert call_kwargs["start"] == expected_start

    def test_1min_loads_windowed_range(self, client, mock_catalog_service):
        """1-min timeframe windows to last 7 days."""
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=1m")
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        expected_start = datetime(2025, 12, 31, tzinfo=timezone.utc) - timedelta(days=7)
        assert call_kwargs["start"] == expected_start

    def test_window_metadata_in_response(self, client):
        """Template receives windowing JS variables for 5-min."""
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=5m")
        assert "windowDays" in response.text
        assert "hasEarlierData" in response.text

    def test_no_date_range_end_still_works(
        self, client, mock_metadata_service, mock_catalog_service
    ):
        """When instrument has no date_range_end, falls back to now."""
        mock_metadata_service.get_instrument.return_value = _make_instrument(date_range_end=None)
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=5m")
        assert response.status_code == 200
        # Should still call query_bars with some bounded range
        call_kwargs = mock_catalog_service.query_bars.call_args_list[0].kwargs
        assert call_kwargs["end"].year >= 2026
        assert call_kwargs["start"] < call_kwargs["end"]

    def test_chart_and_stats_queries_paired(self, client, mock_catalog_service):
        """Chart-panel render must issue both the windowed chart query AND the
        full-range stats query; the tests above use call_args_list[0] for the
        chart call, so this asserts the stats call actually exists."""
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=5m")
        assert mock_catalog_service.query_bars.call_count == 2
        chart_call = mock_catalog_service.query_bars.call_args_list[0].kwargs
        stats_call = mock_catalog_service.query_bars.call_args_list[1].kwargs
        # Chart is windowed; stats scans the instrument's full date range.
        assert stats_call["start"] == datetime(2020, 1, 2, tzinfo=timezone.utc)
        assert stats_call["end"] == datetime(2025, 12, 31, tzinfo=timezone.utc)
        assert chart_call["start"] > stats_call["start"]
