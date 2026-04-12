"""
Explorer route handlers for web UI.

Provides the data explorer page with ticker browsing, search, and filtering.
"""

import math
from typing import Optional

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.api.dependencies import CatalogList, DefaultCatalog, Metadata
from src.api.models.explorer import EXPLORER_PAGE_SIZE, ExplorerPageState, TickerRow
from src.api.models.navigation import BreadcrumbItem, NavigationState

logger = structlog.get_logger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

PAGE_SIZE = EXPLORER_PAGE_SIZE


def _format_bar_count(count: int) -> str:
    """Format bar count for compact display (e.g., 8K, 51K, 3.1M)."""
    if count >= 1_000_000:
        val = count / 1_000_000
        return f"{val:.1f}M" if val < 10 else f"{val:.0f}M"
    if count >= 1_000:
        val = count / 1_000
        return f"{val:.1f}K" if val < 10 else f"{val:.0f}K"
    return str(count)


def _build_page_state(
    catalog: str,
    search: Optional[str],
    asset_class: Optional[str],
    page: int,
    sort_by: str,
) -> ExplorerPageState:
    """Build ExplorerPageState from query params."""
    return ExplorerPageState(
        catalog=catalog,
        search=search or "",
        asset_class=asset_class or "",
        page=page,
        sort_by=sort_by,
    )


async def _get_ticker_data(
    service: Metadata,
    state: ExplorerPageState,
) -> tuple:
    """Fetch ticker data and asset class counts for the current state."""
    offset = (state.page - 1) * PAGE_SIZE

    instruments, total_count = await service.list_instruments_with_search(
        catalog_name=state.catalog,
        search=state.search or None,
        asset_class=state.asset_class or None,
        sort_by=state.sort_by,
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
    asset_class_counts = await service.count_asset_classes(state.catalog)

    return tickers, total_count, total_pages, asset_class_counts


@router.get("/", response_class=HTMLResponse)
async def explorer_page(
    request: Request,
    service: Metadata,
    catalogs: CatalogList,
    default_catalog: DefaultCatalog,
    catalog: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    asset_class: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    sort_by: str = Query("ticker"),
) -> HTMLResponse:
    """Render the full explorer page (AC #1, #5, #6).

    Args:
        request: FastAPI request object.
        service: MetadataService dependency.
        catalogs: List of available catalog names.
        default_catalog: Default catalog name from settings.
        catalog: Selected catalog name (query param).
        search: Search query (query param).
        asset_class: Asset class filter (query param).
        page: Page number (query param).
        sort_by: Sort column (query param).

    Returns:
        HTMLResponse with rendered explorer page.
    """
    selected_catalog = catalog or default_catalog or (catalogs[0] if catalogs else "")

    nav_state = NavigationState(
        active_page="explorer",
        breadcrumbs=[
            BreadcrumbItem(label="Explorer", url="/explorer", is_current=False),
            BreadcrumbItem(
                label=selected_catalog or "No Catalog",
                url=None,
                is_current=True,
            ),
        ],
    )

    if not catalogs or not selected_catalog:
        return templates.TemplateResponse(
            "explorer/explorer.html",
            {
                "request": request,
                "nav_state": nav_state,
                "catalogs": catalogs,
                "state": ExplorerPageState(catalog=selected_catalog or "none"),
                "tickers": [],
                "total_count": 0,
                "total_pages": 0,
                "asset_class_counts": {},
            },
        )

    state = _build_page_state(selected_catalog, search, asset_class, page, sort_by)
    tickers, total_count, total_pages, asset_class_counts = await _get_ticker_data(service, state)

    return templates.TemplateResponse(
        "explorer/explorer.html",
        {
            "request": request,
            "nav_state": nav_state,
            "catalogs": catalogs,
            "state": state,
            "tickers": tickers,
            "total_count": total_count,
            "total_pages": total_pages,
            "asset_class_counts": asset_class_counts,
            "format_bar_count": _format_bar_count,
        },
    )


@router.get("/ticker-list", response_class=HTMLResponse)
async def ticker_list_fragment(
    request: Request,
    service: Metadata,
    catalog: str = Query(...),
    search: Optional[str] = Query(None),
    asset_class: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    sort_by: str = Query("ticker"),
) -> HTMLResponse:
    """Return HTMX fragment for ticker table + pagination (AC #3, #4).

    Args:
        request: FastAPI request object.
        service: MetadataService dependency.
        catalog: Selected catalog name.
        search: Search query.
        asset_class: Asset class filter.
        page: Page number.
        sort_by: Sort column.

    Returns:
        HTMLResponse with ticker_list.html fragment.
    """
    state = _build_page_state(catalog, search, asset_class, page, sort_by)
    tickers, total_count, total_pages, asset_class_counts = await _get_ticker_data(service, state)

    return templates.TemplateResponse(
        "explorer/ticker_list.html",
        {
            "request": request,
            "state": state,
            "tickers": tickers,
            "total_count": total_count,
            "total_pages": total_pages,
            "asset_class_counts": asset_class_counts,
            "format_bar_count": _format_bar_count,
        },
    )
