"""IBKR data provider for fetching historical market data."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from nautilus_trader.adapters.interactive_brokers.common import IBContract

from src.config import get_settings
from src.services.data_fetcher import HistoricalDataFetcher
from src.services.ibkr_client import IBKRHistoricalClient


class IBKRDataProvider:
    """Provider for fetching data from Interactive Brokers."""

    def __init__(self):
        """Initialize IBKR data provider with settings."""
        self.settings = get_settings()

    async def fetch_historical_data(
        self, symbol: str, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch data from IBKR and return as database-compatible records.

        Args:
            symbol: Trading symbol
            start: Start datetime
            end: End datetime

        Returns:
            List of market data dictionaries compatible with database schema

        Raises:
            Exception: If IBKR connection or data fetch fails
        """
        # Connect to IBKR
        client = IBKRHistoricalClient(
            host=self.settings.ibkr.ibkr_host,
            port=self.settings.ibkr.ibkr_port,
            client_id=self.settings.ibkr.ibkr_client_id,
            market_data_type=self.settings.ibkr.get_market_data_type_enum(),
        )

        try:
            await client.connect(timeout=self.settings.ibkr.ibkr_connection_timeout)

            # Create fetcher
            fetcher = HistoricalDataFetcher(client)

            # Create contract for the symbol
            contract = IBContract(
                secType="STK",
                symbol=symbol,
                exchange="SMART",
                primaryExchange="NASDAQ",  # Will be determined by IB
            )

            # Fetch bars
            bars = await fetcher.fetch_bars(
                contracts=[contract],
                bar_specifications=["1-DAY-LAST"],
                start_date=start,
                end_date=end,
            )

            # Convert bars to database records
            records = []
            for bar in bars:
                record = self._bar_to_db_record(bar, symbol)
                records.append(record)

            return records

        finally:
            await client.disconnect()

    def _bar_to_db_record(self, bar, symbol: str) -> Dict[str, Any]:
        """
        Convert Nautilus Bar to database record format.

        Args:
            bar: Nautilus Bar object
            symbol: Trading symbol

        Returns:
            Dictionary with database schema fields
        """
        # Convert nanosecond timestamp to datetime
        timestamp = datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=timezone.utc)

        return {
            "symbol": symbol,
            "timestamp": timestamp,
            "open": Decimal(str(bar.open.as_double())),
            "high": Decimal(str(bar.high.as_double())),
            "low": Decimal(str(bar.low.as_double())),
            "close": Decimal(str(bar.close.as_double())),
            "volume": int(bar.volume.as_double()),
        }
