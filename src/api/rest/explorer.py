"""
REST API endpoint for explorer ticker data.

Provides paginated, searchable ticker list as JSON for the explorer feature,
and chart data endpoint for candlestick rendering.
"""

import asyncio
import math
from datetime import date, datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import DataCatalog, Metadata
from src.api.models.chart_timeseries import Candle
from src.api.models.explorer import (
    EXPLORER_PAGE_SIZE,
    ChartDataResponse,
    ExplorerTimeframe,
    TickerListResponse,
    TickerRow,
    TickerStatsResponse,
)
from src.api.stats_service import _build_ticker_stats
from src.services.exceptions import DataNotFoundError

logger = structlog.get_logger(__name__)

router = APIRouter()

PAGE_SIZE = EXPLORER_PAGE_SIZE

VALID_TF_LABELS = {tf.label for tf in ExplorerTimeframe}


@router.get(
    "/explorer/ticker/{ticker}/stats",
    response_model=TickerStatsResponse,
    summary="Get statistics for a ticker",
    description="Returns date range, per-timeframe bar counts, and price range for a ticker.",
)
async def get_ticker_stats(
    ticker: str,
    service: Metadata,
    catalog_service: DataCatalog,
    catalog: str = Query(..., description="Catalog name"),
    tf: str = Query("D", description="Timeframe label (D, 1H, 5m, 1m)"),
) -> TickerStatsResponse:
    """Get ticker statistics and price range for the selected timeframe."""
    return await _build_ticker_stats(service, catalog_service, catalog, ticker, tf)


@router.get(
    "/chart/catalog/{ticker}",
    response_model=ChartDataResponse,
    summary="Get chart data for a ticker",
    description="Returns OHLCV candlestick data for TradingView chart rendering",
)
async def get_chart_data(
    ticker: str,
    service: Metadata,
    catalog_service: DataCatalog,
    catalog: str = Query(..., description="Catalog name"),
    tf: str = Query("D", description="Timeframe label (D, 1H, 5m, 1m)"),
    start: Optional[date] = Query(None, description="Start date (ISO 8601)"),
    end: Optional[date] = Query(None, description="End date (ISO 8601)"),
) -> ChartDataResponse:
    """Get OHLCV chart data for a ticker from the Parquet catalog.

    Args:
        ticker: Trading symbol (e.g., "AAPL").
        service: MetadataService dependency.
        catalog_service: DataCatalogService dependency.
        catalog: Catalog name to query.
        tf: Timeframe label (D, 1H, 5m, 1m). Defaults to D.
        start: Optional start date for time range.
        end: Optional end date for time range.

    Returns:
        ChartDataResponse with bars array.

    Raises:
        HTTPException: 404 if ticker not found in catalog.
    """
    timeframe = ExplorerTimeframe.from_label(tf if tf in VALID_TF_LABELS else "D")

    instrument = await service.get_instrument(catalog, ticker)
    if instrument is None or not instrument.nautilus_id:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{ticker}' not found in catalog '{catalog}'",
        )
    nautilus_id: str = instrument.nautilus_id

    start_dt = (
        datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        if start
        else datetime(1970, 1, 1, tzinfo=timezone.utc)
    )
    end_dt = (
        datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)
        if end
        else datetime(2099, 12, 31, tzinfo=timezone.utc)
    )

    try:
        bars = await asyncio.to_thread(
            catalog_service.query_bars,
            instrument_id=nautilus_id,
            start=start_dt,
            end=end_dt,
            bar_type_spec=timeframe.bar_type_spec,
        )
    except DataNotFoundError:
        bars = []

    candles = [
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

    return ChartDataResponse(
        bars=candles,
        instrument_id=nautilus_id,
        timeframe=timeframe.bar_type_spec,
        bar_count=len(candles),
    )


@router.get(
    "/explorer/tickers",
    response_model=TickerListResponse,
    summary="Get paginated ticker list",
    description="Returns filtered, paginated ticker list for explorer",
)
async def get_explorer_tickers(
    service: Metadata,
    catalog: str = Query(..., description="Catalog name"),
    search: Optional[str] = Query(None, description="Prefix search on ticker"),
    asset_class: Optional[str] = Query(None, description="Asset class filter"),
    page: int = Query(1, ge=1, description="Page number"),
    sort_by: str = Query("ticker", description="Sort column"),
) -> TickerListResponse:
    """Get paginated ticker list with optional search and filters.

    Args:
        service: MetadataService dependency.
        catalog: Catalog name to query.
        search: Optional prefix search string.
        asset_class: Optional asset class filter.
        page: Page number (1-based).
        sort_by: Sort column name.

    Returns:
        TickerListResponse with paginated results.
    """
    offset = (page - 1) * PAGE_SIZE

    instruments, total_count = await service.list_instruments_with_search(
        catalog_name=catalog,
        search=search,
        asset_class=asset_class,
        sort_by=sort_by,
        limit=PAGE_SIZE,
        offset=offset,
    )

    tickers = [
        TickerRow(
            ticker=inst.ticker,
            name=inst.name,
            asset_class=inst.asset_class,
            date_range_start=inst.date_range_start,
            date_range_end=inst.date_range_end,
            bar_count_daily=inst.bar_count_daily,
            bar_count_hourly=inst.bar_count_hourly,
            bar_count_5min=inst.bar_count_5min,
            bar_count_minute=inst.bar_count_minute,
            nautilus_id=inst.nautilus_id,
        )
        for inst in instruments
    ]

    total_pages = math.ceil(total_count / PAGE_SIZE) if total_count > 0 else 0

    return TickerListResponse(
        tickers=tickers,
        total_count=total_count,
        page=page,
        page_size=PAGE_SIZE,
        total_pages=total_pages,
    )
