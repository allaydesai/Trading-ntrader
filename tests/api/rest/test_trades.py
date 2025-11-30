"""
Tests for GET /api/trades/{run_id} endpoint.

Tests trade marker retrieval for chart overlay.
The endpoint queries trades from the database Trade table.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service, get_db
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
        mock_backtest.id = 1  # Internal DB ID

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        # Mock database session with trades
        mock_trade1 = MagicMock()
        mock_trade1.entry_timestamp = datetime(2024, 1, 15, tzinfo=timezone.utc)
        mock_trade1.order_side = "BUY"
        mock_trade1.entry_price = Decimal("185.50")
        mock_trade1.quantity = Decimal("100")
        mock_trade1.exit_timestamp = datetime(2024, 1, 20, tzinfo=timezone.utc)
        mock_trade1.exit_price = Decimal("190.00")
        mock_trade1.profit_loss = Decimal("450.0")

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=[mock_trade1])
        mock_result.scalars = MagicMock(return_value=mock_scalars)

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def mock_get_db_override():
            yield mock_db

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service
        app.dependency_overrides[get_db] = mock_get_db_override

        try:
            # Act
            response = client.get(f"/api/trades/{run_id}")

            # Assert
            assert response.status_code == 200
            data = response.json()
            assert data["run_id"] == str(run_id)
            # 1 trade with entry and exit = 2 markers
            assert data["trade_count"] == 2
            assert len(data["trades"]) == 2
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)
            app.dependency_overrides.pop(get_db, None)

    def test_trades_returns_sorted_by_timestamp(self, client: TestClient):
        """Test that trades are sorted by timestamp ascending."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.id = 1

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        # Create trades in reverse order to test sorting
        mock_trade1 = MagicMock()
        mock_trade1.entry_timestamp = datetime(2024, 1, 20, tzinfo=timezone.utc)
        mock_trade1.order_side = "BUY"
        mock_trade1.entry_price = Decimal("190.00")
        mock_trade1.quantity = Decimal("100")
        mock_trade1.exit_timestamp = None
        mock_trade1.exit_price = None
        mock_trade1.profit_loss = None

        mock_trade2 = MagicMock()
        mock_trade2.entry_timestamp = datetime(2024, 1, 15, tzinfo=timezone.utc)
        mock_trade2.order_side = "BUY"
        mock_trade2.entry_price = Decimal("185.50")
        mock_trade2.quantity = Decimal("100")
        mock_trade2.exit_timestamp = None
        mock_trade2.exit_price = None
        mock_trade2.profit_loss = None

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        # Return in DB order (reverse chronological to test sorting)
        mock_scalars.all = MagicMock(return_value=[mock_trade1, mock_trade2])
        mock_result.scalars = MagicMock(return_value=mock_scalars)

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def mock_get_db_override():
            yield mock_db

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service
        app.dependency_overrides[get_db] = mock_get_db_override

        try:
            response = client.get(f"/api/trades/{run_id}")

            assert response.status_code == 200
            data = response.json()
            # Should be sorted by time
            assert data["trades"][0]["time"] == "2024-01-15"
            assert data["trades"][1]["time"] == "2024-01-20"
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)
            app.dependency_overrides.pop(get_db, None)


class TestTradesEmptyArray:
    """Tests for empty trades array response."""

    def test_trades_returns_empty_array_when_no_trades(self, client: TestClient):
        """Test that empty trades array returns 200 with empty list."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.id = 1

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        # Mock empty database result
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=[])
        mock_result.scalars = MagicMock(return_value=mock_scalars)

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def mock_get_db_override():
            yield mock_db

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service
        app.dependency_overrides[get_db] = mock_get_db_override

        try:
            response = client.get(f"/api/trades/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["trade_count"] == 0
            assert data["trades"] == []
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)
            app.dependency_overrides.pop(get_db, None)

    def test_trades_returns_empty_when_no_trades_key(self, client: TestClient):
        """Test that backtest with no trades in database returns empty array."""
        run_id = uuid4()
        mock_service = MagicMock()
        mock_backtest = MagicMock()
        mock_backtest.run_id = run_id
        mock_backtest.id = 1

        async def mock_get_backtest(rid):
            return mock_backtest

        mock_service.get_backtest_by_id = mock_get_backtest

        # Mock empty database result
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=[])
        mock_result.scalars = MagicMock(return_value=mock_scalars)

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def mock_get_db_override():
            yield mock_db

        app.dependency_overrides[get_backtest_query_service] = lambda: mock_service
        app.dependency_overrides[get_db] = mock_get_db_override

        try:
            response = client.get(f"/api/trades/{run_id}")

            assert response.status_code == 200
            data = response.json()
            assert data["trade_count"] == 0
            assert data["trades"] == []
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)
            app.dependency_overrides.pop(get_db, None)


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
