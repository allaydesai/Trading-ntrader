"""Database repository for market data operations."""

from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from sqlalchemy import text

from src.db.session import get_session


class DatabaseRepository:
    """Repository for fetching market data from database."""

    async def fetch_market_data(
        self, symbol: str, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        """
        Fetch market data from database.

        Args:
            symbol: Trading symbol
            start: Start datetime
            end: End datetime

        Returns:
            List of market data records

        Raises:
            ValueError: If no data found
        """
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

        return data

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
        # Ensure start and end dates are timezone-aware (UTC)
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
                    "reason": f"Start date {start} is before available data "
                    f"start {data_range['start']}",
                    "available_range": data_range,
                }

            if end > data_range["end"]:
                return {
                    "valid": False,
                    "reason": f"End date {end} is after available data "
                    f"end {data_range['end']}",
                    "available_range": data_range,
                }

            return {"valid": True, "available_range": data_range}

        except Exception as e:
            return {
                "valid": False,
                "reason": f"Error validating data availability: {e}",
                "available_symbols": [],
            }
