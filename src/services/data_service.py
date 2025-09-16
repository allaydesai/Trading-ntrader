"""Data service for fetching and converting market data."""

from datetime import datetime
from typing import List, Optional, Dict, Any

import pandas as pd
from sqlalchemy import text

from src.db.session import get_session
from src.config import get_settings


class DataService:
    """Service for fetching and converting market data."""

    def __init__(self):
        """Initialize data service with caching."""
        self._cache: Dict[str, Any] = {}
        self.settings = get_settings()

    async def get_market_data(
        self, symbol: str, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch market data from database for the given parameters.

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
        cache_key = f"{symbol}_{start}_{end}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Query database
        async with get_session() as session:
            query = text("""
                SELECT symbol, timestamp, open, high, low, close, volume
                FROM market_data
                WHERE symbol = :symbol
                AND timestamp >= :start
                AND timestamp <= :end
                ORDER BY timestamp ASC
            """)

            result = await session.execute(
                query, {"symbol": symbol.upper(), "start": start, "end": end}
            )
            rows = result.fetchall()

        if not rows:
            raise ValueError(
                f"No market data found for {symbol} between {start} and {end}"
            )

        # Convert to list of dictionaries
        data = []
        for row in rows:
            data.append(
                {
                    "symbol": row.symbol,
                    "timestamp": row.timestamp,
                    "open": float(row.open),
                    "high": float(row.high),
                    "low": float(row.low),
                    "close": float(row.close),
                    "volume": int(row.volume),
                }
            )

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
        nautilus_data = []

        for record in data:
            # Convert to Nautilus Bar format (simplified)
            nautilus_record = {
                "instrument_id": f"{record['symbol']}.SIM",
                "bar_type": f"{record['symbol']}.SIM-1-MINUTE-MID-EXTERNAL",
                "ts_event": int(record["timestamp"].timestamp() * 1_000_000_000),
                "ts_init": int(record["timestamp"].timestamp() * 1_000_000_000),
                "open": int(record["open"] * 100000),  # Nautilus uses price precision
                "high": int(record["high"] * 100000),
                "low": int(record["low"] * 100000),
                "close": int(record["close"] * 100000),
                "volume": int(record["volume"]),
            }
            nautilus_data.append(nautilus_record)

        return nautilus_data

    async def get_available_symbols(self) -> List[str]:
        """
        Get list of available symbols in the database.

        Returns:
            List of unique symbols
        """
        async with get_session() as session:
            query = text("SELECT DISTINCT symbol FROM market_data ORDER BY symbol")
            result = await session.execute(query)
            rows = result.fetchall()

        return [row.symbol for row in rows]

    async def get_data_range(self, symbol: str) -> Optional[Dict[str, datetime]]:
        """
        Get the date range of available data for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Dictionary with 'start' and 'end' datetime, or None if no data
        """
        async with get_session() as session:
            query = text("""
                SELECT MIN(timestamp) as start_date, MAX(timestamp) as end_date
                FROM market_data
                WHERE symbol = :symbol
            """)
            result = await session.execute(query, {"symbol": symbol.upper()})
            row = result.fetchone()

        if row and row.start_date and row.end_date:
            return {"start": row.start_date, "end": row.end_date}

        return None

    def clear_cache(self) -> None:
        """Clear the data cache."""
        self._cache.clear()

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
        try:
            data_range = await self.get_data_range(symbol)

            if not data_range:
                return {
                    "valid": False,
                    "reason": f"No data available for symbol {symbol}",
                    "available_symbols": await self.get_available_symbols(),
                }

            if start < data_range["start"]:
                return {
                    "valid": False,
                    "reason": f"Start date {start} is before available data start {data_range['start']}",
                    "available_range": data_range,
                }

            if end > data_range["end"]:
                return {
                    "valid": False,
                    "reason": f"End date {end} is after available data end {data_range['end']}",
                    "available_range": data_range,
                }

            return {"valid": True, "available_range": data_range}

        except Exception as e:
            return {
                "valid": False,
                "reason": f"Error validating data availability: {e}",
                "available_symbols": [],
            }
