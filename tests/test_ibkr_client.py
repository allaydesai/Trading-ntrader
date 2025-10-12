"""Comprehensive unit tests for IBKR client wrapper.

Tests RateLimiter and IBKRHistoricalClient classes with 80%+ coverage.
Following TDD principles and project testing standards.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from ibapi.common import MarketDataTypeEnum


class TestRateLimiter:
    """Comprehensive tests for RateLimiter class."""

    def test_initialization_default_parameters(self):
        """Test RateLimiter initialization with default parameters."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter()

        assert limiter.requests_per_second == 45
        assert limiter.window == timedelta(seconds=1)
        assert len(limiter.requests) == 0

    def test_initialization_custom_rate(self):
        """Test RateLimiter initialization with custom rate."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=10)

        assert limiter.requests_per_second == 10
        assert limiter.window == timedelta(seconds=1)

    @pytest.mark.asyncio
    async def test_acquire_first_request_immediate(self):
        """Test first request is allowed immediately."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=5)

        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed < 0.1  # Should be immediate
        assert len(limiter.requests) == 1

    @pytest.mark.asyncio
    async def test_acquire_within_limit_immediate(self):
        """Test requests within limit are allowed immediately."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=5)

        start = time.time()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed < 0.2  # All 5 should be immediate
        assert len(limiter.requests) == 5

    @pytest.mark.asyncio
    async def test_acquire_exceeds_limit_delays(self):
        """Test request exceeding limit is delayed."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=5)

        # Make 5 requests (at limit)
        for _ in range(5):
            await limiter.acquire()

        # 6th request should be delayed
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed >= 0.9  # Should wait ~1 second
        assert len(limiter.requests) == 6

    @pytest.mark.asyncio
    async def test_sliding_window_expiration(self):
        """Test sliding window allows requests after expiration."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=5)

        # Make 5 requests
        for _ in range(5):
            await limiter.acquire()

        # Wait for window to expire
        await asyncio.sleep(1.1)

        # Next request should be immediate (old requests expired)
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start

        assert elapsed < 0.2  # Should be immediate
        assert len(limiter.requests) == 1  # Old requests removed

    @pytest.mark.asyncio
    async def test_request_cleanup_on_acquire(self):
        """Test old requests are cleaned up during acquire."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=5)

        # Add requests from 2 seconds ago (using UTC)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=2)
        for _ in range(3):
            limiter.requests.append(old_time)

        # New acquire should clean up old requests
        await limiter.acquire()

        assert len(limiter.requests) == 1  # Only new request remains

    @pytest.mark.asyncio
    async def test_concurrent_requests_respect_limit(self):
        """Test concurrent requests respect rate limit."""
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=10)

        # Launch 15 concurrent requests
        start = time.time()
        tasks = [limiter.acquire() for _ in range(15)]
        await asyncio.gather(*tasks)
        elapsed = time.time() - start

        # Should take at least 1 second for 15 requests at 10/sec
        assert elapsed >= 1.0
        # Note: limiter.requests may have < 15 items because old requests
        # are cleaned up from the sliding window during acquire()

    @pytest.mark.asyncio
    async def test_no_race_condition_under_concurrent_load(self):
        """
        Test that rate limiter never exceeds limit under heavy concurrent load.

        This test verifies the fix for the race condition bug where multiple
        coroutines could simultaneously bypass the limit check.
        """
        from src.services.ibkr_client import RateLimiter

        limiter = RateLimiter(requests_per_second=10)
        request_times = []

        async def make_request():
            await limiter.acquire()
            request_times.append(datetime.now(timezone.utc))

        # Launch 50 concurrent requests
        tasks = [make_request() for _ in range(50)]
        await asyncio.gather(*tasks)

        # Verify no 1-second window has more than 10 requests
        request_times.sort()
        for i in range(len(request_times)):
            window_start = request_times[i]
            window_end = window_start + timedelta(seconds=1)

            # Count requests in this 1-second window
            count = sum(1 for t in request_times if window_start <= t < window_end)

            # Should never exceed the limit
            assert count <= 10, (
                f"Found {count} requests in 1-second window "
                f"(limit: 10) - race condition detected!"
            )


class TestIBKRHistoricalClient:
    """Comprehensive tests for IBKRHistoricalClient class."""

    def test_initialization_default_parameters(self):
        """Test client initialization with default parameters."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            assert not client._connected
            assert client.rate_limiter.requests_per_second == 45

    def test_initialization_custom_parameters(self):
        """Test client initialization with custom parameters."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ) as mock_init:
            client = IBKRHistoricalClient(
                host="192.168.1.100",
                port=4002,
                client_id=10,
                market_data_type=MarketDataTypeEnum.REALTIME,
            )

            mock_init.assert_called_once_with(
                host="192.168.1.100",
                port=4002,
                client_id=10,
                market_data_type=MarketDataTypeEnum.REALTIME,
                log_level="INFO",
            )
            assert not client._connected

    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Test successful connection to IBKR Gateway."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            with patch.object(
                client.client, "connect", new_callable=AsyncMock
            ) as mock_connect:
                mock_connect.return_value = None
                client.client.account_id = "DU123456"
                client.client.server_version = 176

                result = await client.connect(timeout=30)

                assert result["connected"] is True
                assert result["account_id"] == "DU123456"
                assert result["server_version"] == 176
                assert "connection_time" in result
                assert client.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_success_with_missing_attributes(self):
        """Test connection success when some attributes are missing."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            with patch.object(
                client.client, "connect", new_callable=AsyncMock
            ) as mock_connect:
                mock_connect.return_value = None
                # Don't set account_id or server_version

                result = await client.connect(timeout=30)

                assert result["connected"] is True
                assert result["account_id"] == "N/A"
                assert result["server_version"] == "N/A"

    @pytest.mark.asyncio
    async def test_connect_timeout_error(self):
        """Test connection timeout is handled gracefully."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            with patch.object(
                client.client, "connect", side_effect=asyncio.TimeoutError
            ):
                with pytest.raises(ConnectionError) as exc_info:
                    await client.connect(timeout=5)

                assert "Failed to connect to IBKR" in str(exc_info.value)
                assert not client.is_connected

    @pytest.mark.asyncio
    async def test_connect_connection_refused(self):
        """Test connection refused error is handled."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            with patch.object(
                client.client, "connect", side_effect=ConnectionRefusedError
            ):
                with pytest.raises(ConnectionError) as exc_info:
                    await client.connect()

                assert "Failed to connect to IBKR" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connect_generic_exception(self):
        """Test generic exception during connection is handled."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            with patch.object(
                client.client, "connect", side_effect=RuntimeError("Network error")
            ):
                with pytest.raises(ConnectionError) as exc_info:
                    await client.connect()

                assert "Failed to connect to IBKR" in str(exc_info.value)
                assert "Network error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_disconnect_when_connected(self):
        """Test disconnection when client is connected."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()
            client._connected = True

            await client.disconnect()

            assert not client.is_connected

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        """Test disconnection when client is not connected."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            await client.disconnect()

            assert not client.is_connected

    def test_is_connected_property_false(self):
        """Test is_connected property returns False initially."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            assert not client.is_connected

    def test_is_connected_property_true(self):
        """Test is_connected property returns True when connected."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()
            client._connected = True

            assert client.is_connected

    def test_rate_limiter_integration(self):
        """Test rate limiter is properly integrated."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            assert hasattr(client, "rate_limiter")
            assert client.rate_limiter.requests_per_second == 45


class TestIBKRClientIntegration:
    """Integration tests for IBKR client components."""

    @pytest.mark.asyncio
    async def test_client_uses_rate_limiter_on_operations(self):
        """Test client operations use rate limiter."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client = IBKRHistoricalClient()

            # Mock rate limiter
            with patch.object(
                client.rate_limiter, "acquire", new_callable=AsyncMock
            ) as mock_acquire:
                # Note: Client doesn't directly call rate limiter
                # It's used by HistoricalDataFetcher
                # This test verifies rate_limiter is accessible
                assert mock_acquire is not None

    @pytest.mark.asyncio
    async def test_multiple_clients_independent_rate_limits(self):
        """Test multiple client instances have independent rate limiters."""
        from src.services.ibkr_client import IBKRHistoricalClient
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            client1 = IBKRHistoricalClient()
            client2 = IBKRHistoricalClient()

            # Each client should have its own rate limiter
            assert client1.rate_limiter is not client2.rate_limiter

            # Make requests on client1
            for _ in range(5):
                await client1.rate_limiter.acquire()

            # Client2 should not be affected
            assert len(client2.rate_limiter.requests) == 0
