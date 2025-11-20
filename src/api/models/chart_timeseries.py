"""
Pydantic models for Timeseries API.

Defines request/response models for OHLCV candlestick data
in TradingView-compatible format.
"""

from enum import Enum

from pydantic import BaseModel, Field


class Timeframe(str, Enum):
    """Supported bar timeframes for chart data."""

    ONE_MIN = "1_MIN"
    FIVE_MIN = "5_MIN"
    FIFTEEN_MIN = "15_MIN"
    ONE_HOUR = "1_HOUR"
    ONE_DAY = "1_DAY"


# Mapping from API timeframe to Nautilus bar_type_spec
TIMEFRAME_TO_BAR_TYPE = {
    Timeframe.ONE_MIN: "1-MINUTE-LAST",
    Timeframe.FIVE_MIN: "5-MINUTE-LAST",
    Timeframe.FIFTEEN_MIN: "15-MINUTE-LAST",
    Timeframe.ONE_HOUR: "1-HOUR-LAST",
    Timeframe.ONE_DAY: "1-DAY-LAST",
}


class Candle(BaseModel):
    """
    Single OHLCV candle for TradingView chart.

    Attributes:
        time: ISO 8601 date format (e.g., "2023-01-15")
        open: Opening price
        high: High price
        low: Low price
        close: Closing price
        volume: Trading volume

    Example:
        >>> candle = Candle(
        ...     time="2024-01-15",
        ...     open=185.50,
        ...     high=186.00,
        ...     low=185.00,
        ...     close=185.75,
        ...     volume=1000000
        ... )
    """

    time: str = Field(..., description="ISO 8601 date format")
    open: float = Field(..., description="Opening price")
    high: float = Field(..., description="High price")
    low: float = Field(..., description="Low price")
    close: float = Field(..., description="Closing price")
    volume: int = Field(..., ge=0, description="Trading volume")


class TimeseriesResponse(BaseModel):
    """
    OHLCV time series response.

    Attributes:
        symbol: Trading symbol (e.g., "AAPL")
        timeframe: Bar timeframe used
        candles: List of OHLCV candles sorted by time ascending

    Example:
        >>> response = TimeseriesResponse(
        ...     symbol="AAPL",
        ...     timeframe="1_MIN",
        ...     candles=[candle1, candle2]
        ... )
    """

    symbol: str = Field(..., description="Trading symbol")
    timeframe: str = Field(..., description="Bar timeframe")
    candles: list[Candle] = Field(default_factory=list, description="OHLCV candles")
