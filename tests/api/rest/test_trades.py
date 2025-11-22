"""
Tests for GET /api/trades/{run_id} endpoint.

Tests trade marker retrieval for chart overlay.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service
from src.api.web import app


class TestTradesEndpoint:
    """Tests for GET /api/trades/{run_id} endpoint."""

    def test_trades_returns_valid_markers(self, client: TestClient):
        """Test successful retrieval of trade markers."""
        # Arrange
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {
            "trades": [
                {
                    "time": "2024-01-15",
                    "side": "buy",
                    "price": 185.50,
                    "quantity": 100,
                    "pnl": 0.0,
                },
                {
                    "time": "2024-01-20",
                    "side": "sell",
                    "price": 190.00,
                    "quantity": 100,
                    "pnl": 450.0,
                },
            ]
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            # Act
            response = client.get(f"/api/trades/{run_id}")

            # Assert
            assert response.status_code == 200
            data = response.json()
            assert data["run_id"] == str(run_id)
            assert data["trade_count"] == 2
            assert len(data["trades"]) == 2

            trade1 = data["trades"][0]
            assert trade1["time"] == "2024-01-15"
            assert trade1["side"] == "buy"
            assert trade1["price"] == 185.50
            assert trade1["quantity"] == 100
            assert trade1["pnl"] == 0.0
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_trades_returns_sorted_by_timestamp(self, client: TestClient):
        """Test that trades are sorted by timestamp ascending."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        # Trades in reverse order
        mock_backtest.config_snapshot = {
            "trades": [
                {
                    "time": "2024-01-20",
                    "side": "sell",
                    "price": 190.0,
                    "quantity": 100,
                    "pnl": 450.0,
                },
                {
                    "time": "2024-01-15",
                    "side": "buy",
                    "price": 185.5,
                    "quantity": 100,
                    "pnl": 0.0,
                },
            ]
        }

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/trades/{run_id}")

            assert response.status_code == 200
            data = response.json()
            # Should be sorted by time
            assert data["trades"][0]["time"] == "2024-01-15"
            assert data["trades"][1]["time"] == "2024-01-20"
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)


class TestTradesEmptyArray:
    """Tests for empty trades array response."""

    def test_trades_returns_empty_array_when_no_trades(self, client: TestClient):
        """Test that empty trades array returns 200 with empty list."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {"trades": []}

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/trades/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["trade_count"] == 0
            assert data["trades"] == []
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_trades_returns_empty_when_no_trades_key(self, client: TestClient):
        """Test that missing trades key returns empty array."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.config_snapshot = {"strategy": "sma"}  # No trades key

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/trades/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["trade_count"] == 0
            assert data["trades"] == []
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)


class TestTrades404Error:
    """Tests for 404 error when run_id not found."""

    def test_trades_returns_404_when_not_found(self, client: TestClient):
        """Test that non-existent run_id returns 404."""
        run_id = uuid4()
        mock_service = MagicMock()

        async def mock_get_backtest(rid):
            return None

        mock_service.get_backtest_by_id = mock_get_backtest

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service

        try:
            response = client.get(f"/api/trades/{run_id}")

            assert response.status_code == 404
            data = response.json()
            assert "detail" in data
            assert str(run_id) in data["detail"]
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_trades_rejects_invalid_uuid(self, client: TestClient):
        """Test that invalid UUID returns 422."""
        response = client.get("/api/trades/not-a-uuid")

        assert response.status_code == 422
