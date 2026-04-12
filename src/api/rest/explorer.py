"""
REST API endpoint for explorer ticker data.

Provides paginated, searchable ticker list as JSON for the explorer feature.
"""

import math
from typing import Optional

import structlog
from fastapi import APIRouter, Query

from src.api.dependencies import Metadata
from src.api.models.explorer import EXPLORER_PAGE_SIZE, TickerListResponse, TickerRow

logger = structlog.get_logger(__name__)

router = APIRouter()

PAGE_SIZE = EXPLORER_PAGE_SIZE


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
