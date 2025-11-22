"""
Tests for GET /api/indicators/{run_id} endpoint.

Tests indicator series retrieval for chart overlay.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service
from src.api.web import app


class TestIndicatorsEndpoint:
    """Tests for GET /api/indicators/{run_id} endpoint."""

    def test_indicators_returns_valid_series(self, client: TestClient):
        """Test successful retrieval of indicator series."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {
            "indicators": {
                "sma_fast": [
                    {"time": "2024-01-15", "value": 184.0},
                    {"time": "2024-01-20", "value": 186.5},
                ],
                "sma_slow": [
                    {"time": "2024-01-15", "value": 182.0},
                    {"time": "2024-01-20", "value": 183.0},
                ],
            }
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/indicators/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["run_id"] == str(run_id)
            assert "sma_fast" in data["indicators"]
            assert "sma_slow" in data["indicators"]
            assert len(data["indicators"]["sma_fast"]) == 2
            assert data["indicators"]["sma_fast"][0]["time"] == "2024-01-15"
            assert data["indicators"]["sma_fast"][0]["value"] == 184.0
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_indicators_sorts_by_timestamp(self, client: TestClient):
        """Test that indicator points are sorted by timestamp ascending."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        # Points in reverse order
        mock_backtest.config_snapshot = {
            "indicators": {
                "sma": [
                    {"time": "2024-01-20", "value": 186.5},
                    {"time": "2024-01-15", "value": 184.0},
                ]
            }
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/indicators/{run_id}")

            assert response.status_code == 200
            data = response.json()
            # Should be sorted by time
            assert data["indicators"]["sma"][0]["time"] == "2024-01-15"
            assert data["indicators"]["sma"][1]["time"] == "2024-01-20"
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)


class TestIndicatorsEmptyObject:
    """Tests for empty indicators object response."""

    def test_indicators_returns_empty_when_no_indicators(self, client: TestClient):
        """Test that missing indicators returns empty object."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {}  # No indicators key

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
        """Test that empty indicators object returns 200."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {"indicators": {}}

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
