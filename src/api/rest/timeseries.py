"""
Timeseries API endpoint for OHLCV candlestick data.

Provides TradingView-compatible JSON data from Parquet catalog.
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import DataCatalog
from src.api.models.chart_errors import ErrorDetail
from src.api.models.chart_timeseries import (
    Candle,
    Timeframe,
    TimeseriesResponse,
    TIMEFRAME_TO_BAR_TYPE,
)
from src.services.exceptions import DataNotFoundError

router = APIRouter()


def symbol_to_instrument_id(symbol: str) -> str:
    """
    Convert user symbol to Nautilus instrument_id format.

    Args:
        symbol: User-provided symbol (e.g., "AAPL" or "AAPL.NASDAQ")

    Returns:
        Nautilus instrument_id (e.g., "AAPL.NASDAQ")

    Example:
        >>> symbol_to_instrument_id("AAPL")
        'AAPL.NASDAQ'
        >>> symbol_to_instrument_id("AAPL.NYSE")
        'AAPL.NYSE'
    """
    if "." in symbol:
        return symbol
    return f"{symbol}.NASDAQ"


@router.get(
    "/timeseries",
    response_model=TimeseriesResponse,
    responses={
        404: {"model": ErrorDetail, "description": "Market data not found"},
        422: {"description": "Validation error"},
    },
    summary="Get OHLCV time series data",
    description="Returns candlestick data for chart rendering from Parquet catalog",
)
def get_timeseries(
    catalog: DataCatalog,
    symbol: str = Query(
        ..., min_length=1, max_length=20, description="Trading symbol (e.g., AAPL)"
    ),
    start: date = Query(..., description="Start date (ISO 8601)"),
    end: date = Query(..., description="End date (ISO 8601)"),
    timeframe: Timeframe = Query(
        default=Timeframe.ONE_MIN, description="Bar timeframe"
    ),
) -> TimeseriesResponse:
    """
    Get OHLCV time series data for chart rendering.

    Args:
        catalog: DataCatalogService dependency
        symbol: Trading symbol (e.g., AAPL)
        start: Start date for data range
        end: End date for data range
        timeframe: Bar timeframe (default: 1_MIN)

    Returns:
        TimeseriesResponse with candles array

    Raises:
        HTTPException: 404 if data not found, 422 if validation fails
    """
    # Validate date range
    if end < start:
        raise HTTPException(
            status_code=422,
            detail="End date must be after start date",
        )

    # Convert to datetime with UTC timezone
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)

    # Convert symbol to Nautilus instrument_id
    instrument_id = symbol_to_instrument_id(symbol)

    # Get bar type spec from timeframe
    bar_type_spec = TIMEFRAME_TO_BAR_TYPE[timeframe]

    try:
        # Query bars from catalog
        bars = catalog.query_bars(
            instrument_id=instrument_id,
            start=start_dt,
            end=end_dt,
            bar_type_spec=bar_type_spec,
        )

        # Convert bars to candles
        candles = []
        for bar in bars:
            # Convert nanosecond timestamp to date string
            ts_seconds = bar.ts_event / 1e9
            dt = datetime.fromtimestamp(ts_seconds, tz=timezone.utc)
            time_str = dt.strftime("%Y-%m-%d")

            candle = Candle(
                time=time_str,
                open=bar.open.as_double(),
                high=bar.high.as_double(),
                low=bar.low.as_double(),
                close=bar.close.as_double(),
                volume=int(bar.volume.as_double()),
            )
            candles.append(candle)

        return TimeseriesResponse(
            symbol=symbol,
            timeframe=timeframe.value,
            candles=candles,
        )

    except DataNotFoundError:
        # Return 404 with CLI suggestion
        raise HTTPException(
            status_code=404,
            detail={
                "detail": f"Market data not found for {symbol} from {start} to {end}",
                "suggestion": (
                    f"Run: ntrader data fetch --symbol {symbol} "
                    f"--start {start} --end {end}"
                ),
            },
        )
