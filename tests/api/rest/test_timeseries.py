"""
Tests for GET /api/timeseries endpoint.

Tests OHLCV candlestick data retrieval from Parquet catalog.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service, get_data_catalog_service
from src.api.web import app
from src.services.exceptions import DataNotFoundError


class TestTimeseriesEndpoint:
    """Tests for GET /api/timeseries endpoint."""

    def test_timeseries_returns_valid_candles(
        self, client: TestClient, mock_data_catalog_service: MagicMock
    ):
        """Test successful retrieval of OHLCV candlestick data."""
        # Arrange: Create mock bar data
        mock_bar = MagicMock()
        mock_bar.ts_event = 1705276800000000000  # 2024-01-15 00:00:00 UTC in ns
        mock_bar.open.as_double.return_value = 185.50
        mock_bar.high.as_double.return_value = 186.00
        mock_bar.low.as_double.return_value = 185.00
        mock_bar.close.as_double.return_value = 185.75
        mock_bar.volume.as_double.return_value = 1000000

        mock_data_catalog_service.query_bars.return_value = [mock_bar]

        app.dependency_overrides[get_data_catalog_service] = lambda: mock_data_catalog_service

        try:
            # Act
            response = client.get(
                "/api/timeseries",
                params={
                    "symbol": "AAPL",
                    "start": "2024-01-01",
                    "end": "2024-01-31",
                    "timeframe": "1_MIN",
                },
            )

            # Assert
            assert response.status_code == 200
            data = response.json()
            assert data["symbol"] == "AAPL"
            assert data["timeframe"] == "1_MIN"
            assert len(data["candles"]) == 1
            candle = data["candles"][0]
            assert candle["time"] == 1705276800  # Unix timestamp for 2024-01-15
            assert candle["open"] == 185.50
            assert candle["high"] == 186.00
            assert candle["low"] == 185.00
            assert candle["close"] == 185.75
            assert candle["volume"] == 1000000
        finally:
            app.dependency_overrides.pop(get_data_catalog_service, None)

    def test_timeseries_maps_timeframe_correctly(
        self, client: TestClient, mock_data_catalog_service: MagicMock
    ):
        """Test that timeframe parameter is correctly mapped to Nautilus format."""
        mock_data_catalog_service.query_bars.return_value = []

        app.dependency_overrides[get_data_catalog_service] = lambda: mock_data_catalog_service

        try:
            # Test 1_DAY timeframe
            client.get(
                "/api/timeseries",
                params={
                    "symbol": "AAPL",
                    "start": "2024-01-01",
                    "end": "2024-01-31",
                    "timeframe": "1_DAY",
                },
            )

            # Should call with 1-DAY-LAST bar type spec
            call_args = mock_data_catalog_service.query_bars.call_args
            assert "1-DAY-LAST" in str(call_args)
        finally:
            app.dependency_overrides.pop(get_data_catalog_service, None)

    def test_timeseries_converts_symbol_to_instrument_id(
        self, client: TestClient, mock_data_catalog_service: MagicMock
    ):
        """Test that symbol is converted to Nautilus instrument_id format."""
        mock_data_catalog_service.query_bars.return_value = []

        app.dependency_overrides[get_data_catalog_service] = lambda: mock_data_catalog_service

        try:
            client.get(
                "/api/timeseries",
                params={
                    "symbol": "AAPL",
                    "start": "2024-01-01",
                    "end": "2024-01-31",
                },
            )

            # Should convert AAPL to AAPL.NASDAQ
            call_args = mock_data_catalog_service.query_bars.call_args
            assert "AAPL.NASDAQ" in str(call_args)
        finally:
            app.dependency_overrides.pop(get_data_catalog_service, None)


class TestTimeseriesValidation:
    """Tests for timeseries parameter validation."""

    def test_timeseries_rejects_missing_symbol(self, client: TestClient):
        """Test that missing symbol parameter returns 422."""
        response = client.get(
            "/api/timeseries",
            params={
                "start": "2024-01-01",
                "end": "2024-01-31",
            },
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    def test_timeseries_rejects_missing_start(self, client: TestClient):
        """Test that missing start parameter returns 422."""
        response = client.get(
            "/api/timeseries",
            params={
                "symbol": "AAPL",
                "end": "2024-01-31",
            },
        )

        assert response.status_code == 422

    def test_timeseries_rejects_missing_end(self, client: TestClient):
        """Test that missing end parameter returns 422."""
        response = client.get(
            "/api/timeseries",
            params={
                "symbol": "AAPL",
                "start": "2024-01-01",
            },
        )

        assert response.status_code == 422

    def test_timeseries_rejects_invalid_date_range(
        self, client: TestClient, mock_data_catalog_service: MagicMock
    ):
        """Test that end date before start date returns 422."""
        app.dependency_overrides[get_data_catalog_service] = lambda: mock_data_catalog_service

        try:
            response = client.get(
                "/api/timeseries",
                params={
                    "symbol": "AAPL",
                    "start": "2024-01-31",
                    "end": "2024-01-01",  # Before start
                },
            )

            assert response.status_code == 422
            data = response.json()
            assert "detail" in data
        finally:
            app.dependency_overrides.pop(get_data_catalog_service, None)

    def test_timeseries_rejects_invalid_timeframe(self, client: TestClient):
        """Test that invalid timeframe parameter returns 422."""
        response = client.get(
            "/api/timeseries",
            params={
                "symbol": "AAPL",
                "start": "2024-01-01",
                "end": "2024-01-31",
                "timeframe": "INVALID",
            },
        )

        assert response.status_code == 422


class TestTimeseries404Error:
    """Tests for 404 error responses with CLI suggestions."""

    def test_timeseries_returns_404_with_cli_suggestion(
        self, client: TestClient, mock_data_catalog_service: MagicMock
    ):
        """Test that 404 response includes CLI fetch suggestion."""
        # Arrange: Mock service to raise DataNotFoundError
        mock_data_catalog_service.query_bars.side_effect = DataNotFoundError(
            "AAPL.NASDAQ",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        app.dependency_overrides[get_data_catalog_service] = lambda: mock_data_catalog_service

        try:
            # Act
            response = client.get(
                "/api/timeseries",
                params={
                    "symbol": "AAPL",
                    "start": "2024-01-01",
                    "end": "2024-01-31",
                },
            )

            # Assert
            assert response.status_code == 404
            data = response.json()
            assert "detail" in data
            # HTTPException wraps our dict in the detail field
            detail = data["detail"]
            assert "AAPL" in detail["detail"]
            assert "suggestion" in detail
            assert "ntrader data fetch" in detail["suggestion"]
            assert "--symbol AAPL" in detail["suggestion"]
        finally:
            app.dependency_overrides.pop(get_data_catalog_service, None)

    def test_timeseries_404_includes_date_range_in_suggestion(
        self, client: TestClient, mock_data_catalog_service: MagicMock
    ):
        """Test that CLI suggestion includes the requested date range."""
        mock_data_catalog_service.query_bars.side_effect = DataNotFoundError(
            "AAPL.NASDAQ",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc),
        )

        app.dependency_overrides[get_data_catalog_service] = lambda: mock_data_catalog_service

        try:
            response = client.get(
                "/api/timeseries",
                params={
                    "symbol": "AAPL",
                    "start": "2024-01-01",
                    "end": "2024-01-31",
                },
            )

            data = response.json()
            detail = data["detail"]
            assert "--start 2024-01-01" in detail["suggestion"]
            assert "--end 2024-01-31" in detail["suggestion"]
        finally:
            app.dependency_overrides.pop(get_data_catalog_service, None)

    def test_timeseries_empty_result_returns_empty_array(
        self, client: TestClient, mock_data_catalog_service: MagicMock
    ):
        """Test that empty results return 200 with empty candles array."""
        mock_data_catalog_service.query_bars.return_value = []

        app.dependency_overrides[get_data_catalog_service] = lambda: mock_data_catalog_service

        try:
            response = client.get(
                "/api/timeseries",
                params={
                    "symbol": "AAPL",
                    "start": "2024-01-01",
                    "end": "2024-01-31",
                },
            )

            # Empty result should return 200 with empty array
            assert response.status_code == 200
            data = response.json()
            assert data["candles"] == []
        finally:
            app.dependency_overrides.pop(get_data_catalog_service, None)


class TestRunTimeseriesEndpoint:
    """Tests for GET /api/timeseries/run/{run_id} (review finding #5).

    The run-scoped route renders candles from the backtest's own catalog and
    bar type, so named-catalog / intraday runs no longer chart the default
    catalog at a hardcoded daily timeframe.
    """

    @staticmethod
    def _mock_backtest(**cfg) -> MagicMock:
        backtest = MagicMock()
        backtest.instrument_symbol = "AAPL.NASDAQ"
        backtest.config_snapshot = cfg
        return backtest

    @staticmethod
    def _override_service(backtest) -> MagicMock:
        service = MagicMock()
        service.get_backtest_by_id = AsyncMock(return_value=backtest)
        app.dependency_overrides[get_backtest_query_service] = lambda: service
        return service

    def test_run_timeseries_uses_run_catalog_and_bar_type(self, client: TestClient):
        """Candles come from the loader; response labels the run's own bar type."""
        backtest = self._mock_backtest(bar_type="1-MINUTE-LAST", catalog_name="e2e-test")
        self._override_service(backtest)

        mock_bar = MagicMock()
        mock_bar.ts_event = 1705276800000000000
        mock_bar.open.as_double.return_value = 185.50
        mock_bar.high.as_double.return_value = 186.00
        mock_bar.low.as_double.return_value = 185.00
        mock_bar.close.as_double.return_value = 185.75
        mock_bar.volume.as_double.return_value = 1000000

        try:
            with patch(
                "src.api.rest.timeseries._load_chart_bars",
                new=AsyncMock(return_value=[mock_bar]),
            ) as mock_loader:
                response = client.get(f"/api/timeseries/run/{uuid4()}")

            assert response.status_code == 200
            data = response.json()
            assert data["symbol"] == "AAPL.NASDAQ"
            assert data["timeframe"] == "1-MINUTE-LAST"
            assert len(data["candles"]) == 1
            assert data["candles"][0]["close"] == 185.75
            # Loader received the run object (resolves its catalog + bar type).
            assert mock_loader.await_args.args[0] is backtest
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_run_timeseries_returns_404_when_run_missing(self, client: TestClient):
        """An unknown run id yields 404, not an empty chart."""
        self._override_service(None)
        try:
            response = client.get(f"/api/timeseries/run/{uuid4()}")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_run_timeseries_degrades_to_empty_on_load_error(self, client: TestClient):
        """A corrupt/missing catalog degrades to an empty candle list (HTTP 200)."""
        backtest = self._mock_backtest(bar_type="1-DAY-LAST")
        self._override_service(backtest)
        try:
            with patch(
                "src.api.rest.timeseries._load_chart_bars",
                new=AsyncMock(side_effect=RuntimeError("corrupt parquet")),
            ):
                response = client.get(f"/api/timeseries/run/{uuid4()}")

            assert response.status_code == 200
            data = response.json()
            assert data["candles"] == []
            assert data["timeframe"] == "1-DAY-LAST"
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)
