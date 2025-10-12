"""IBKR client wrapper for historical data fetching."""

import asyncio
from collections import deque
from datetime import datetime, timedelta
from typing import Dict

from ibapi.common import MarketDataTypeEnum  # type: ignore
from nautilus_trader.adapters.interactive_brokers.historical.client import (
    HistoricInteractiveBrokersClient,
)


class RateLimiter:
    """
    Rate limiting for IBKR API calls.

    IBKR enforces 50 requests/second. This implementation uses a conservative
    limit of 45 req/sec (90% of limit) for safety.

    Reference: milestone-5-design.md:406-458 (Rate Limiting Strategy)
    """

    def __init__(self, requests_per_second: int = 45):
        """
        Initialize rate limiter.

        Args:
            requests_per_second: Maximum requests allowed per second
        """
        self.requests_per_second = requests_per_second
        self.window = timedelta(seconds=1)
        self.requests: deque[datetime] = deque()

    async def acquire(self):
        """
        Wait until a request slot is available.

        Implements sliding window rate limiting.
        """
        now = datetime.now()

        # Remove expired requests outside the current window
        while self.requests and self.requests[0] < now - self.window:
            self.requests.popleft()

        # If at limit, wait until oldest request expires
        if len(self.requests) >= self.requests_per_second:
            sleep_time = (self.requests[0] + self.window - now).total_seconds()
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        # Record this request
        self.requests.append(datetime.now())


class IBKRHistoricalClient:
    """
    Wrapper around Nautilus HistoricInteractiveBrokersClient.

    Provides simplified interface for backtesting data retrieval with
    built-in rate limiting and connection management.

    Reference: milestone-5-design.md:72-155 (Historical Data Client Setup)
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        market_data_type: MarketDataTypeEnum = MarketDataTypeEnum.DELAYED_FROZEN,
    ):
        """
        Initialize IBKR historical data client.

        Args:
            host: IB Gateway/TWS host address
            port: Connection port (7497=TWS paper, 7496=TWS live,
                  4002=Gateway paper, 4001=Gateway live)
            client_id: Unique client identifier
            market_data_type: Data type (DELAYED_FROZEN for paper trading)
        """
        self.client = HistoricInteractiveBrokersClient(
            host=host,
            port=port,
            client_id=client_id,
            market_data_type=market_data_type,
            log_level="INFO",
        )
        self._connected = False
        self.rate_limiter = RateLimiter(requests_per_second=45)

    async def connect(self, timeout: int = 30) -> Dict:
        """
        Establish connection to IBKR Gateway.

        Args:
            timeout: Connection timeout in seconds

        Returns:
            Connection info dict with account_id and server_version

        Raises:
            ConnectionError: If connection fails within timeout
        """
        try:
            await self.client.connect()
            await asyncio.sleep(2)  # Allow connection to stabilize

            self._connected = True

            # Get available connection info
            info = {
                "connected": True,
                "account_id": getattr(self.client, "account_id", "N/A"),
                "server_version": getattr(self.client, "server_version", "N/A"),
                "connection_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

            return info
        except Exception as e:
            raise ConnectionError(f"Failed to connect to IBKR: {e}")

    async def disconnect(self):
        """Gracefully disconnect from IBKR."""
        if self._connected:
            # HistoricInteractiveBrokersClient doesn't have a disconnect method
            # Connection is managed by the context/lifecycle
            self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected
