"""
Timeseries API endpoint for OHLCV candlestick data.

Provides TradingView-compatible JSON data from Parquet catalog.
"""

from datetime import date, datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query

from src.api.chart_bars import _load_chart_bars, resolve_run_bar_type_spec
from src.api.dependencies import BacktestService, DataCatalog
from src.api.models.chart_errors import ErrorDetail
from src.api.models.chart_timeseries import (
    TIMEFRAME_TO_BAR_TYPE,
    Candle,
    Timeframe,
    TimeseriesResponse,
)
from src.services.exceptions import DataNotFoundError

router = APIRouter()
logger = structlog.get_logger(__name__)


def _bars_to_candles(bars: list) -> list[Candle]:
    """Convert Nautilus bars to TradingView-compatible candles (seconds epoch)."""
    return [
        Candle(
            time=int(bar.ts_event / 1e9),
            open=bar.open.as_double(),
            high=bar.high.as_double(),
            low=bar.low.as_double(),
            close=bar.close.as_double(),
            volume=int(bar.volume.as_double()),
        )
        for bar in bars
    ]


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
    timeframe: Timeframe = Query(default=Timeframe.ONE_MIN, description="Bar timeframe"),
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

        return TimeseriesResponse(
            symbol=symbol,
            timeframe=timeframe.value,
            candles=_bars_to_candles(bars),
        )

    except DataNotFoundError:
        # Return 404 with CLI suggestion
        raise HTTPException(
            status_code=404,
            detail={
                "detail": f"Market data not found for {symbol} from {start} to {end}",
                "suggestion": (
                    f"Run: ntrader data fetch --symbol {symbol} --start {start} --end {end}"
                ),
            },
        )


@router.get(
    "/timeseries/run/{run_id}",
    response_model=TimeseriesResponse,
    responses={
        404: {"model": ErrorDetail, "description": "Backtest not found"},
        422: {"description": "Validation error"},
    },
    summary="Get OHLCV time series for a backtest run",
    description=(
        "Returns candlestick data rendered against the run's own catalog and "
        "bar type, so named-catalog and intraday runs chart their real data "
        "instead of the default catalog at a hardcoded daily timeframe."
    ),
)
async def get_run_timeseries(
    run_id: UUID,
    service: BacktestService,
) -> TimeseriesResponse:
    """Get OHLCV candles for a backtest run's own data.

    Unlike ``/timeseries`` (symbol + timeframe query), this resolves the run's
    ``config_snapshot`` to read the catalog the backtest actually executed on
    and the matching bar type — fixing empty/mismatched price charts for
    named-catalog and intraday runs (review finding #5). On a missing or corrupt
    catalog it degrades to an empty candle list rather than failing the chart.
    """
    backtest = await service.get_backtest_by_id(run_id)
    if not backtest:
        raise HTTPException(
            status_code=404,
            detail=f"Backtest run {run_id} not found",
        )

    try:
        bars = await _load_chart_bars(backtest)
    except Exception as e:
        logger.warning(
            "run_timeseries_load_failed",
            run_id=str(run_id),
            error=str(e),
        )
        bars = []

    return TimeseriesResponse(
        symbol=backtest.instrument_symbol,
        timeframe=resolve_run_bar_type_spec(backtest),
        candles=_bars_to_candles(bars),
    )
