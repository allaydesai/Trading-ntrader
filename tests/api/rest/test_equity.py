"""
Tests for GET /api/equity/{run_id} endpoint.

Tests equity curve and drawdown data retrieval.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service
from src.api.web import app


class TestEquityEndpoint:
    """Tests for GET /api/equity/{run_id} endpoint."""

    def test_equity_returns_valid_curve(self, client: TestClient):
        """Test successful retrieval of equity curve data."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {
            "equity_curve": [
                {"time": "2024-01-01", "value": 100000.0},
                {"time": "2024-01-15", "value": 100000.0},
                {"time": "2024-01-20", "value": 100450.0},
                {"time": "2024-01-31", "value": 100450.0},
            ]
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/equity/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["run_id"] == str(run_id)
            assert len(data["equity"]) == 4
            assert data["equity"][0]["time"] == "2024-01-01"
            assert data["equity"][0]["value"] == 100000.0
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_equity_calculates_drawdown_correctly(self, client: TestClient):
        """Test that drawdown is calculated correctly from equity curve."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        # Equity: 100000 -> 105000 (peak) -> 100000 (5% drawdown) -> 110000 (new peak)
        mock_backtest.config_snapshot = {
            "equity_curve": [
                {"time": "2024-01-01", "value": 100000.0},
                {"time": "2024-01-10", "value": 105000.0},
                {"time": "2024-01-15", "value": 100000.0},
                {"time": "2024-01-20", "value": 110000.0},
            ]
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/equity/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert len(data["drawdown"]) == 4
            # First point: 0% drawdown
            assert data["drawdown"][0]["value"] == 0.0
            # Second point: 0% (new peak)
            assert data["drawdown"][1]["value"] == 0.0
            # Third point: ~4.76% drawdown ((100000-105000)/105000*100)
            assert abs(data["drawdown"][2]["value"] - (-4.76)) < 0.1
            # Fourth point: 0% (new peak)
            assert data["drawdown"][3]["value"] == 0.0
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_equity_and_drawdown_same_length(self, client: TestClient):
        """Test that equity and drawdown arrays have same length."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {
            "equity_curve": [
                {"time": "2024-01-01", "value": 100000.0},
                {"time": "2024-01-31", "value": 100450.0},
            ]
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/equity/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert len(data["equity"]) == len(data["drawdown"])
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)


class TestEquity404Error:
    """Tests for 404 error when run_id not found."""

    def test_equity_returns_404_when_not_found(self, client: TestClient):
        """Test that non-existent run_id returns 404."""
        run_id = uuid4()
        mock_service = MagicMock()

        async def mock_get_backtest(rid):
            return None

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/equity/{run_id}")

            assert response.status_code == 404
            data = response.json()
            assert "detail" in data
            assert str(run_id) in data["detail"]
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_equity_rejects_invalid_uuid(self, client: TestClient):
        """Test that invalid UUID returns 422."""
        response = client.get("/api/equity/not-a-uuid")

        assert response.status_code == 422


class TestEquityEmptyData:
    """Tests for empty equity curve response."""

    def test_equity_returns_empty_when_no_data(self, client: TestClient):
        """Test that missing equity_curve returns empty arrays."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {}  # No equity_curve key

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/equity/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["equity"] == []
            assert data["drawdown"] == []
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)
