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
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["bar_type_spec"] == "1-HOUR-LAST"

    def test_default_timeframe_is_daily(self, client, mock_catalog_service):
        client.get("/api/chart/catalog/AAPL?catalog=us_stocks")
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["bar_type_spec"] == "1-DAY-LAST"

    def test_ticker_resolved_via_metadata(
        self, client, mock_metadata_service, mock_catalog_service
    ):
        client.get("/api/chart/catalog/AAPL?catalog=us_stocks&tf=D")
        mock_metadata_service.get_instrument.assert_called_once_with("us_stocks", "AAPL")
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
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
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
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

    def test_oob_stats_panel_placeholder(self, client):
        response = client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        assert 'id="stats-panel"' in response.text
        assert "hx-swap-oob" in response.text


@pytest.mark.component
class TestChartPanelWindowing:
    """Tests for windowed chart data loading."""

    def test_daily_loads_windowed_range(self, client, mock_catalog_service):
        """Daily timeframe windows to last 1825 days."""
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=D")
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["end"] == datetime(2025, 12, 31, tzinfo=timezone.utc)
        expected_start = datetime(2025, 12, 31, tzinfo=timezone.utc) - timedelta(days=1825)
        assert call_kwargs["start"] == expected_start

    def test_5min_loads_windowed_range(self, client, mock_catalog_service):
        """5-min timeframe windows to last 30 days from date_range_end.

        Instrument date_range_end = 2025-12-31, so window starts
        2025-12-01 (30 days before).
        """
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=5m")
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        # End anchored to instrument's date_range_end
        assert call_kwargs["end"] == datetime(2025, 12, 31, tzinfo=timezone.utc)
        # Start is 30 days before date_range_end
        expected_start = datetime(2025, 12, 31, tzinfo=timezone.utc) - timedelta(days=30)
        assert call_kwargs["start"] == expected_start

    def test_hourly_loads_windowed_range(self, client, mock_catalog_service):
        """Hourly timeframe windows to last 180 days."""
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=1H")
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["end"] == datetime(2025, 12, 31, tzinfo=timezone.utc)
        expected_start = datetime(2025, 12, 31, tzinfo=timezone.utc) - timedelta(days=180)
        assert call_kwargs["start"] == expected_start

    def test_1min_loads_windowed_range(self, client, mock_catalog_service):
        """1-min timeframe windows to last 7 days."""
        client.get("/explorer/chart-panel?catalog=us_stocks&ticker=AAPL&tf=1m")
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
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
        call_kwargs = mock_catalog_service.query_bars.call_args.kwargs
        assert call_kwargs["end"].year >= 2026
        assert call_kwargs["start"] < call_kwargs["end"]
