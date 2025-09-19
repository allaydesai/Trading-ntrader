"""Data service for fetching and converting market data."""

from datetime import datetime, timezone
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
        if not data:
            return []

        try:
            # Import the data wrangler
            from src.utils.data_wrangler import MarketDataWrangler

            # If instrument is not provided, create a basic one
            if instrument is None:
                from src.utils.mock_data import create_test_instrument

                # Extract symbol from instrument_id
                symbol = str(instrument_id).split(".")[0]
                instrument, _ = create_test_instrument(symbol)

            # Create wrangler and process data
            wrangler = MarketDataWrangler(instrument)
            bars = wrangler.process(data)

            if not bars:
                raise ValueError("No bars were created from the provided data")

            # Successfully converted data to bars
            return bars

        except ImportError as e:
            print(f"Failed to import data wrangler: {e}")
            # Fallback to original implementation
            return self._convert_to_nautilus_bars_fallback(data, instrument_id)

        except Exception as e:
            print(f"Error converting data to Nautilus bars: {e}")
            # Fallback to original implementation
            return self._convert_to_nautilus_bars_fallback(data, instrument_id)

    def _convert_to_nautilus_bars_fallback(
        self, data: List[Dict[str, Any]], instrument_id
    ) -> List:
        """
        Fallback method for converting market data to Nautilus Trader Bar objects.

        Args:
            data: List of market data dictionaries
            instrument_id: Nautilus InstrumentId object

        Returns:
            List of Nautilus Bar objects
        """
        from nautilus_trader.model.data import Bar, BarType, BarSpecification
        from nautilus_trader.model.enums import (
            BarAggregation,
            PriceType,
            AggregationSource,
        )
        from nautilus_trader.model.objects import Price, Quantity

        bars = []

        # Create bar type specification for 1-minute bars
        bar_spec = BarSpecification(
            step=1,
            aggregation=BarAggregation.MINUTE,
            price_type=PriceType.MID,
        )
        bar_type = BarType(
            instrument_id=instrument_id,
            bar_spec=bar_spec,
            aggregation_source=AggregationSource.EXTERNAL,
        )

        for record in data:
            try:
                # Convert timestamp to nanoseconds since Unix epoch
                ts_event = int(record["timestamp"].timestamp() * 1_000_000_000)
                ts_init = ts_event

                # Create price objects (Nautilus uses 5 decimal places precision for most instruments)
                open_price = Price.from_str(f"{record['open']:.5f}")
                high_price = Price.from_str(f"{record['high']:.5f}")
                low_price = Price.from_str(f"{record['low']:.5f}")
                close_price = Price.from_str(f"{record['close']:.5f}")

                # Create volume quantity
                volume = Quantity.from_int(int(record["volume"]))

                # Create Bar object
                bar = Bar(
                    bar_type=bar_type,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                    ts_event=ts_event,
                    ts_init=ts_init,
                )

                bars.append(bar)

            except Exception as e:
                print(f"Failed to create bar for record {record}: {e}")
                continue

        # Return the created bars
        return bars

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
        # Ensure dates are timezone-aware (UTC)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        # Get the actual data range for this symbol
        data_range = await self.get_data_range(symbol)
        if not data_range:
            return None

        # Check if input times appear to be date-only (midnight)
        start_is_midnight = start.time().hour == 0 and start.time().minute == 0
        end_is_midnight = end.time().hour == 0 and end.time().minute == 0

        adjusted_start = start
        adjusted_end = end

        # If start is midnight, find the first available data point on or after that date
        if start_is_midnight:
            # Query for the earliest timestamp on the start date
            async with get_session() as session:
                query = text("""
                    SELECT MIN(timestamp) as first_timestamp
                    FROM market_data
                    WHERE symbol = :symbol
                    AND DATE(timestamp) = DATE(:target_date)
                """)
                result = await session.execute(
                    query, {"symbol": symbol.upper(), "target_date": start}
                )
                row = result.fetchone()

                if row and row.first_timestamp:
                    adjusted_start = row.first_timestamp
                else:
                    # No data on exact date, use overall data start if it's later
                    if data_range["start"] > start:
                        adjusted_start = data_range["start"]

        # If end is midnight, find the last available data point on or before that date
        if end_is_midnight:
            # For midnight end times, we want to include the entire day
            # So we look for the last data point on that date
            async with get_session() as session:
                query = text("""
                    SELECT MAX(timestamp) as last_timestamp
                    FROM market_data
                    WHERE symbol = :symbol
                    AND DATE(timestamp) = DATE(:target_date)
                """)
                result = await session.execute(
                    query, {"symbol": symbol.upper(), "target_date": end}
                )
                row = result.fetchone()

                if row and row.last_timestamp:
                    adjusted_end = row.last_timestamp
                else:
                    # No data on exact date
                    # If requested end is before available data, use adjusted start
                    if end < data_range["start"]:
                        adjusted_end = adjusted_start
                    # Otherwise use overall data end if it's earlier
                    elif data_range["end"] < end:
                        adjusted_end = data_range["end"]

        return {"start": adjusted_start, "end": adjusted_end}

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
        # Ensure start and end dates are timezone-aware (UTC) for consistent comparison
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

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
