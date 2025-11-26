"""IBKR client wrapper for historical data fetching."""

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Dict

from ibapi.common import MarketDataTypeEnum  # type: ignore
from nautilus_trader.adapters.interactive_brokers.historical.client import (
    HistoricInteractiveBrokersClient,
)
from nautilus_trader.model.identifiers import InstrumentId


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
        self._lock = asyncio.Lock()

    async def acquire(self) -> datetime:
        """
        Wait until a request slot is available.

        Implements sliding window rate limiting with thread-safe operations.
        Uses asyncio.Lock to prevent race conditions in concurrent scenarios.

        Returns:
            The timestamp when this request was recorded
        """
        async with self._lock:
            while True:
                now = datetime.now(timezone.utc)

                # Remove expired requests outside the current window
                while self.requests and self.requests[0] < now - self.window:
                    self.requests.popleft()

                # If we have capacity, record this request and return
                if len(self.requests) < self.requests_per_second:
                    self.requests.append(now)
                    return now

                # At limit, wait until oldest request expires
                sleep_time = (self.requests[0] + self.window - now).total_seconds()
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                # Loop back to re-check after sleeping


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
                "connection_time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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

    async def fetch_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        bar_type_spec: str = "1-MINUTE-LAST",
    ):
        """
        Fetch historical bars and instrument from IBKR.

        Args:
            instrument_id: Instrument ID (e.g., "AAPL.NASDAQ")
            start: Start datetime (UTC)
            end: End datetime (UTC)
            bar_type_spec: Bar type specification (e.g., "1-MINUTE-LAST")

        Returns:
            Tuple of (bars, instrument) where bars is a list of Bar objects
            and instrument is the Instrument object

        Raises:
            Exception: If fetch fails
        """
        # Reason: Apply rate limiting before request
        await self.rate_limiter.acquire()

        # Reason: Parse instrument_id to get symbol and venue
        # Expected format: "SYMBOL.VENUE" (e.g., "AAPL.NASDAQ")
        parts = instrument_id.split(".")
        if len(parts) != 2:
            raise ValueError(f"Invalid instrument_id format: {instrument_id}")

        symbol, venue = parts

        # Reason: Validate bar type spec format
        # Expected format: "{period}-{aggregation}-{price_type}"
        # Example: "1-MINUTE-LAST"
        bar_parts = bar_type_spec.split("-")
        if len(bar_parts) < 2:
            raise ValueError(f"Invalid bar_type_spec format: {bar_type_spec}")

        # Reason: Strip timezone info since we're specifying tz_name parameter
        # Nautilus expects naive datetimes when tz_name is provided
        start_naive = start.replace(tzinfo=None) if start.tzinfo else start
        end_naive = end.replace(tzinfo=None) if end.tzinfo else end

        # Reason: Request bars from IBKR via Nautilus client
        # bar_specifications should be simple format strings like "1-MINUTE-LAST"
        # Use instrument_ids instead of contracts to avoid parsing issues
        bars = await self.client.request_bars(
            bar_specifications=[bar_type_spec],  # Just "1-MINUTE-LAST"
            end_date_time=end_naive,  # Required parameter (comes before start!)
            tz_name="UTC",
            start_date_time=start_naive,  # Optional start time
            instrument_ids=[instrument_id],  # Use instrument_ids instead of contracts
            use_rth=True,  # Regular Trading Hours only
            timeout=120,  # 2 minute timeout
        )

        # Reason: Also fetch instrument definition for persistence
        # This allows us to save the instrument to the catalog
        # Convert string to InstrumentId object for request_instruments API
        nautilus_instrument_id = InstrumentId.from_str(instrument_id)
        instruments = await self.client.request_instruments(
            instrument_ids=[nautilus_instrument_id],
        )

        # Reason: Return both bars and instrument (first from the list)
        instrument = instruments[0] if instruments else None

        return bars, instrument

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected
