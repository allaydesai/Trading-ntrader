"""Historical data fetcher for IBKR integration."""

import datetime
from pathlib import Path
from typing import List

from nautilus_trader.adapters.interactive_brokers.common import IBContract
from nautilus_trader.model.data import Bar, QuoteTick, TradeTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from .ibkr_client import IBKRHistoricalClient


class HistoricalDataFetcher:
    """
    Fetch historical data from IBKR and store to catalog.

    Handles bars (OHLCV), ticks (trades, quotes), and instrument definitions.
    Based on Nautilus Trader official IBKR adapter documentation.

    Reference: milestone-5-design.md:262-404 (Historical Data Fetching)
    Official docs: https://nautilustrader.io/docs/latest/integrations/ib
    """

    def __init__(
        self,
        client: IBKRHistoricalClient,
        catalog_path: str = "./data/catalog",
    ):
        """
        Initialize data fetcher.

        Args:
            client: Connected IBKR historical client
            catalog_path: Path to Parquet data catalog
        """
        self.client = client
        self.catalog_path = Path(catalog_path)
        self.catalog = ParquetDataCatalog(str(self.catalog_path))

    async def fetch_bars(
        self,
        contracts: List[IBContract],
        bar_specifications: List[str],
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        timezone: str = "America/New_York",
        use_rth: bool = True,
    ) -> List[Bar]:
        """
        Fetch historical bars (OHLCV data).

        Args:
            contracts: List of instruments to fetch
            bar_specifications: Bar types (e.g., "1-MINUTE-LAST", "1-DAY-LAST")
            start_date: Start of historical period
            end_date: End of historical period
            timezone: Timezone for date interpretation
            use_rth: Regular Trading Hours only

        Returns:
            List of Bar objects (Nautilus type)

        Raises:
            RuntimeError: If client not connected

        Example bar_specifications:
            "1-MINUTE-LAST"  - 1-minute bars using last price
            "5-MINUTE-MID"   - 5-minute bars using midpoint
            "1-HOUR-LAST"    - 1-hour bars using last price
            "1-DAY-LAST"     - Daily bars using last price
        """
        if not self.client.is_connected:
            raise RuntimeError("Client not connected. Call connect() first.")

        # Apply rate limiting
        await self.client.rate_limiter.acquire()

        # Request bars using Nautilus client
        bars = await self.client.client.request_bars(
            bar_specifications=bar_specifications,
            start_date_time=start_date,
            end_date_time=end_date,
            tz_name=timezone,
            contracts=contracts,
            use_rth=use_rth,
            timeout=120,  # 2 minutes timeout per request
        )

        # Save to catalog for later database import
        # skip_disjoint_check=True allows writing overlapping data intervals
        if bars:
            self.catalog.write_data(bars, skip_disjoint_check=True)

        return bars

    async def fetch_ticks(
        self,
        contracts: List[IBContract],
        tick_types: List[str],
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        timezone: str = "America/New_York",
        use_rth: bool = True,
    ) -> List[TradeTick | QuoteTick]:
        """
        Fetch historical tick data.

        Args:
            contracts: List of instruments to fetch
            tick_types: Tick types (e.g., "TRADES", "BID_ASK")
            start_date: Start of historical period
            end_date: End of historical period
            timezone: Timezone for date interpretation
            use_rth: Regular Trading Hours only

        Returns:
            List of tick objects (TradeTick or QuoteTick)

        Raises:
            RuntimeError: If client not connected

        Tick types:
            "TRADES"  - Trade ticks with price, size, timestamp
            "BID_ASK" - Quote ticks with bid/ask prices and sizes
        """
        if not self.client.is_connected:
            raise RuntimeError("Client not connected. Call connect() first.")

        # Apply rate limiting
        await self.client.rate_limiter.acquire()

        # Request ticks using Nautilus client
        ticks = await self.client.client.request_ticks(
            tick_types=tick_types,
            start_date_time=start_date,
            end_date_time=end_date,
            tz_name=timezone,
            contracts=contracts,
            use_rth=use_rth,
            timeout=120,
        )

        # Save to catalog
        # skip_disjoint_check=True allows writing overlapping data intervals
        if ticks:
            self.catalog.write_data(ticks, skip_disjoint_check=True)

        return ticks

    async def request_instruments(self, contracts: List[IBContract]) -> List:
        """
        Request instrument definitions.

        Args:
            contracts: List of contracts to fetch instruments for

        Returns:
            List of Instrument objects (Nautilus type)

        Raises:
            RuntimeError: If client not connected
        """
        if not self.client.is_connected:
            raise RuntimeError("Client not connected. Call connect() first.")

        # Apply rate limiting
        await self.client.rate_limiter.acquire()

        # Request instruments using Nautilus client
        instruments = await self.client.client.request_instruments(contracts=contracts)

        # Save instrument definitions
        # skip_disjoint_check=True allows writing overlapping data intervals
        if instruments:
            self.catalog.write_data(instruments, skip_disjoint_check=True)

        return instruments
