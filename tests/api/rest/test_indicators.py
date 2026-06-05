"""
Tests for GET /api/indicators/{run_id} endpoint.

Tests indicator series retrieval for chart overlay.
The endpoint computes indicators on-demand using DataCatalogService
based on the strategy_path in config_snapshot.
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service, get_data_catalog_service
from src.api.rest.indicators import _load_chart_bars
from src.api.web import app


class TestIndicatorsEndpoint:
    """Tests for GET /api/indicators/{run_id} endpoint."""

    def test_indicators_returns_valid_series(self, client: TestClient):
        """Test that indicators are computed for SMA strategy."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.instrument_symbol = "SPY.ARCA"
        mock_backtest.start_date = date(2024, 1, 1)
        mock_backtest.end_date = date(2024, 1, 31)
        mock_backtest.config_snapshot = {
            "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
            "config": {
                "fast_period": 10,
                "slow_period": 20,
            },
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        # Mock DataCatalogService to return empty bars (indicators will be empty)
        mock_catalog = MagicMock()
        mock_catalog.query_bars = MagicMock(return_value=[])

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service
        app.dependency_overrides[get_data_catalog_service] = lambda: mock_catalog

        try:
            response = client.get(f"/api/indicators/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["run_id"] == str(run_id)
            # With empty bars, indicators will be empty but endpoint succeeds
            assert "indicators" in data
            assert isinstance(data["indicators"], dict)
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)
            app.dependency_overrides.pop(get_data_catalog_service, None)

    def test_indicators_sorts_by_timestamp(self, client: TestClient):
        """Test that indicator points are sorted by timestamp ascending."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.instrument_symbol = "SPY.ARCA"
        mock_backtest.start_date = date(2024, 1, 1)
        mock_backtest.end_date = date(2024, 1, 31)
        mock_backtest.config_snapshot = {
            "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
            "config": {
                "fast_period": 5,
                "slow_period": 10,
            },
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        # Create mock bars that will generate indicator data
        mock_bars = []
        # Need enough bars for the SMA to initialize (slow_period=10)
        for i in range(15):
            mock_bar = MagicMock()
            mock_bar.ts_event = int(datetime(2024, 1, i + 1, tzinfo=timezone.utc).timestamp() * 1e9)
            mock_bar.close = MagicMock()
            mock_bar.close.__float__ = MagicMock(return_value=100.0 + i)
            mock_bars.append(mock_bar)

        mock_catalog = MagicMock()
        mock_catalog.query_bars = MagicMock(return_value=mock_bars)

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service
        app.dependency_overrides[get_data_catalog_service] = lambda: mock_catalog

        try:
            response = client.get(f"/api/indicators/{run_id}")

            assert response.status_code == 200
            data = response.json()

            # Check that indicators are computed and sorted
            if data["indicators"] and "sma_fast" in data["indicators"]:
                sma_fast = data["indicators"]["sma_fast"]
                if len(sma_fast) > 1:
                    # Verify sorted by time
                    times = [p["time"] for p in sma_fast]
                    assert times == sorted(times)
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)
            app.dependency_overrides.pop(get_data_catalog_service, None)


class TestIndicatorsEmptyObject:
    """Tests for empty indicators object response."""

    def test_indicators_returns_empty_when_no_indicators(self, client: TestClient):
        """Test that unknown strategy returns empty indicators object."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.instrument_symbol = "SPY.ARCA"
        mock_backtest.start_date = date(2024, 1, 1)
        mock_backtest.end_date = date(2024, 1, 31)
        mock_backtest.config_snapshot = {
            "strategy_path": "src.core.strategies.unknown:UnknownStrategy",
            "config": {},
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/indicators/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["indicators"] == {}
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_indicators_returns_empty_dict_when_empty(self, client: TestClient):
        """Test that empty config returns empty indicators."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.instrument_symbol = "SPY.ARCA"
        mock_backtest.start_date = date(2024, 1, 1)
        mock_backtest.end_date = date(2024, 1, 31)
        mock_backtest.config_snapshot = {}

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/indicators/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["indicators"] == {}
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)


class TestIndicators404Error:
    """Tests for 404 error when run_id not found."""

    def test_indicators_returns_404_when_not_found(self, client: TestClient):
        """Test that non-existent run_id returns 404."""
        run_id = uuid4()
        mock_service = MagicMock()

        async def mock_get_backtest(rid):
            return None

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/indicators/{run_id}")

            assert response.status_code == 404
            data = response.json()
            assert "detail" in data
            assert str(run_id) in data["detail"]
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_indicators_rejects_invalid_uuid(self, client: TestClient):
        """Test that invalid UUID returns 422."""
        response = client.get("/api/indicators/not-a-uuid")

        assert response.status_code == 422


class TestLoadChartBars:
    """Unit tests for the run-aware chart bar loader (review finding #5)."""

    def _backtest(self, **cfg) -> MagicMock:
        backtest = MagicMock()
        backtest.config_snapshot = cfg
        backtest.instrument_symbol = cfg.pop("_symbol", "AAPL.NASDAQ")
        backtest.data_source = cfg.pop("_data_source", "catalog")
        backtest.start_date = date(2024, 1, 1)
        backtest.end_date = date(2024, 1, 31)
        return backtest

    async def test_uses_config_bar_type_on_default_catalog(self):
        """Default-catalog runs query the actual bar type, not hardcoded 1-DAY-LAST."""
        backtest = self._backtest(bar_type="1-MINUTE-LAST")

        with patch("src.api.rest.indicators.DataCatalogService") as mock_svc:
            mock_svc.return_value.query_bars.return_value = []
            await _load_chart_bars(backtest)

        mock_svc.assert_called_once_with()  # default NAUTILUS_PATH catalog
        kwargs = mock_svc.return_value.query_bars.call_args.kwargs
        assert kwargs["bar_type_spec"] == "1-MINUTE-LAST"
        assert kwargs["instrument_id"] == "AAPL.NASDAQ"

    async def test_named_catalog_resolves_path_and_nautilus_id(self):
        """Named-catalog runs read the named directory using the DB nautilus_id."""
        backtest = self._backtest(bar_type="5-MINUTE-LAST", catalog_name="e2e-test", _symbol="AAPL")

        with (
            patch("src.api.rest.indicators.DataCatalogService") as mock_svc,
            patch(
                "src.api.rest.indicators._resolve_named_nautilus_id",
                return_value="AAPL.NASDAQ",
            ),
            patch("src.api.rest.indicators.get_settings") as mock_settings,
        ):
            mock_settings.return_value.catalog.catalog_base_path = "/tmp/catalogs"
            mock_svc.return_value.query_bars.return_value = []
            await _load_chart_bars(backtest)

        mock_svc.assert_called_once_with(catalog_path="/tmp/catalogs/e2e-test")
        kwargs = mock_svc.return_value.query_bars.call_args.kwargs
        assert kwargs["bar_type_spec"] == "5-MINUTE-LAST"
        assert kwargs["instrument_id"] == "AAPL.NASDAQ"

    async def test_named_catalog_from_data_source_prefix(self):
        """`catalog:<name>` data_source prefix is honoured when config lacks catalog_name."""
        backtest = self._backtest(
            bar_type="1-DAY-LAST", _symbol="AAPL", _data_source="catalog:firstrate-etf"
        )

        with (
            patch("src.api.rest.indicators.DataCatalogService") as mock_svc,
            patch(
                "src.api.rest.indicators._resolve_named_nautilus_id",
                return_value="AAPL.ARCA",
            ),
            patch("src.api.rest.indicators.get_settings") as mock_settings,
        ):
            mock_settings.return_value.catalog.catalog_base_path = "/tmp/catalogs"
            mock_svc.return_value.query_bars.return_value = []
            await _load_chart_bars(backtest)

        mock_svc.assert_called_once_with(catalog_path="/tmp/catalogs/firstrate-etf")
        assert mock_svc.return_value.query_bars.call_args.kwargs["instrument_id"] == "AAPL.ARCA"
