"""Data service for fetching and converting market data."""

from datetime import datetime
from typing import List, Optional, Dict, Any, Literal

import pandas as pd

from src.config import get_settings
from src.services.database_repository import DatabaseRepository
from src.services.nautilus_converter import NautilusConverter
from src.services.ibkr_data_provider import IBKRDataProvider


class DataService:
    """Service for fetching and converting market data."""

    def __init__(self, source: Literal["database", "csv", "ibkr"] = "database"):
        """
        Initialize data service with caching.

        Args:
            source: Data source type ("database", "csv", "ibkr")
                   - "database": Use existing database (default, backward compatible)
                   - "csv": Alias for "database" (M2 CSV import workflow)
                   - "ibkr": Fetch data from Interactive Brokers
        """
        self._cache: Dict[str, Any] = {}
        self.settings = get_settings()
        self.source = source

        # Initialize dependencies
        self.db_repo = DatabaseRepository()
        self.converter = NautilusConverter()
        self.ibkr_provider = IBKRDataProvider()

    async def get_market_data(
        self, symbol: str, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch market data from configured source.

        Args:
            symbol: Trading symbol (e.g., AAPL)
            start: Start datetime
            end: End datetime

        Returns:
            List of market data records as dictionaries

        Raises:
            ConnectionError: If database is not accessible
            ValueError: If no data found for parameters
        """
        # Check cache first
        cache_key = f"{self.source}_{symbol}_{start}_{end}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Route to appropriate data source
        if self.source == "ibkr":
            data = await self.ibkr_provider.fetch_historical_data(symbol, start, end)
        else:
            # Use database (default, "csv" alias, backward compatible)
            data = await self.db_repo.fetch_market_data(symbol, start, end)

        # Cache results
        self._cache[cache_key] = data

        return data

    async def get_data_as_dataframe(
        self, symbol: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """
        Get market data as pandas DataFrame.

        Args:
            symbol: Trading symbol
            start: Start datetime
            end: End datetime

        Returns:
            DataFrame with OHLCV data
        """
        data = await self.get_market_data(symbol, start, end)

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)

        return df

    def convert_to_nautilus_bars(
        self, data: List[Dict[str, Any]], instrument_id, instrument=None
    ) -> List:
        """
        Convert market data to Nautilus Trader Bar objects.

        Args:
            data: List of market data dictionaries
            instrument_id: Nautilus InstrumentId object
            instrument: Optional Nautilus Instrument object for proper conversion

        Returns:
            List of Nautilus Bar objects

        Raises:
            ValueError: If data conversion fails
            ImportError: If required modules are not available
        """
        return self.converter.convert_to_nautilus_bars(data, instrument_id, instrument)

    def convert_to_nautilus_format(
        self, data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert market data to format suitable for Nautilus Trader.

        Args:
            data: List of market data dictionaries

        Returns:
            List of data in Nautilus-compatible format

        Note:
            This is a placeholder implementation. Full Nautilus integration
            will be implemented when we have the backtest runner integration.
        """
        return self.converter.convert_to_nautilus_format(data)

    async def get_available_symbols(self) -> List[str]:
        """
        Get list of available symbols in the database.

        Returns:
            List of unique symbols
        """
        return await self.db_repo.get_available_symbols()

    async def get_data_range(self, symbol: str) -> Optional[Dict[str, datetime]]:
        """
        Get the date range of available data for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Dictionary with 'start' and 'end' datetime, or None if no data
        """
        return await self.db_repo.get_data_range(symbol)

    def clear_cache(self) -> None:
        """Clear the data cache."""
        self._cache.clear()

    async def get_adjusted_date_range(
        self, symbol: str, start: datetime, end: datetime
    ) -> Optional[Dict[str, datetime]]:
        """
        Get adjusted date range that fits within available data.

        If the input dates are date-only (time is midnight), adjust them to
        the actual available data boundaries for those dates.

        Args:
            symbol: Trading symbol
            start: Start datetime (may be adjusted)
            end: End datetime (may be adjusted)

        Returns:
            Dictionary with adjusted 'start' and 'end' datetimes, or None if no data
        """
        return await self.db_repo.get_adjusted_date_range(symbol, start, end)

    async def validate_data_availability(
        self, symbol: str, start: datetime, end: datetime
    ) -> Dict[str, Any]:
        """
        Validate if data is available for the given parameters.

        Args:
            symbol: Trading symbol
            start: Start datetime
            end: End datetime

        Returns:
            Dictionary with validation results
        """
        return await self.db_repo.validate_data_availability(symbol, start, end)
