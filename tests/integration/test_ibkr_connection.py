"""Tests for IBKR connection management."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.integration
class TestIBKRConnection:
    """Test suite for IBKR connection management."""

    @pytest.mark.asyncio
    async def test_connection_success_with_mock_gateway(self):
        """INTEGRATION: Connection succeeds with mock IB Gateway."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497, client_id=1)

            # Mock the underlying Nautilus client
            with patch.object(
                client.client, "connect", new_callable=AsyncMock
            ) as mock_connect:
                mock_connect.return_value = None
                client.client.account_id = "DU123456"
                client.client.server_version = 176
                client.client.host = "127.0.0.1"
                client.client.port = 7497

                result = await client.connect(timeout=30)

                assert result["connected"] is True
                assert "account_id" in result
                assert "server_version" in result
                assert "connection_time" in result

    @pytest.mark.asyncio
    async def test_connection_timeout_handling(self):
        """INTEGRATION: Connection timeout is handled gracefully."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)

            with patch.object(
                client.client, "connect", side_effect=asyncio.TimeoutError
            ):
                with pytest.raises(ConnectionError) as exc_info:
                    await client.connect(timeout=5)

                assert "Failed to connect to IBKR" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connection_refused_when_gateway_down(self):
        """INTEGRATION: Connection failure doesn't crash the application."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)

            with patch.object(
                client.client, "connect", side_effect=ConnectionRefusedError
            ):
                with pytest.raises(ConnectionError) as exc_info:
                    await client.connect()

                assert "Failed to connect to IBKR" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_disconnect_gracefully(self):
        """INTEGRATION: Disconnection is handled gracefully."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)
            client._connected = True

            await client.disconnect()

            # Verify connection flag is set to False
            assert client.is_connected is False

    def test_is_connected_property(self):
        """Test connection status property."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)

            assert client.is_connected is False

            client._connected = True
            assert client.is_connected is True


@pytest.mark.integration
class TestRateLimiting:
    """Test rate limiting compliance."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_requests_within_limit(self):
        """INTEGRATION: Rate limiter allows requests within limit."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=10)

        # Should allow 10 requests immediately
        for _ in range(10):
            await limiter.acquire()

        # 11th request should be delayed
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        # Should have waited ~1 second
        assert elapsed > 0.9

    @pytest.mark.asyncio
    async def test_rate_limiter_sliding_window(self):
        """INTEGRATION: Rate limiter implements sliding window correctly."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=5)

        # Make 5 requests
        for _ in range(5):
            await limiter.acquire()

        # Wait 0.5 seconds
        await asyncio.sleep(0.5)

        # Should still block (window not expired)
        start = asyncio.get_event_loop().time()
        await limiter.acquire()
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed > 0.4  # Should wait ~0.5 more seconds

    @pytest.mark.asyncio
    async def test_rate_limiter_prevents_throttling(self):
        """INTEGRATION: Rate limiter enforces IBKR request limits."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=10)

        # Make 15 requests
        start = time.time()
        for _ in range(15):
            await limiter.acquire()
        elapsed = time.time() - start

        # Should take at least 1 second for 15 requests at 10/sec
        assert elapsed >= 1.0
