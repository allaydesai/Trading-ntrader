"""
Backtests route handler for web UI.

Provides paginated backtest list and HTMX fragment endpoints with filtering.
"""

import structlog
from datetime import date
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from src.api.dependencies import BacktestService
from src.api.models.common import EmptyStateMessage
from src.api.models.filter_models import (
    ExecutionStatus,
    FilterState,
    SortColumn,
    SortOrder,
)
from src.api.models.navigation import BreadcrumbItem, NavigationState

logger = structlog.get_logger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def backtest_list(
    request: Request,
    service: BacktestService,
    strategy: Optional[str] = Query(None, max_length=255),
    instrument: Optional[str] = Query(None, max_length=50),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    status: Optional[ExecutionStatus] = Query(None),
    sort: SortColumn = Query(SortColumn.CREATED_AT),
    order: SortOrder = Query(SortOrder.DESC),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> HTMLResponse:
    """
    Render the backtest list page with filtering, sorting, and pagination.

    Displays a filtered table of backtest runs with key metrics and filter controls.

    Args:
        request: FastAPI request object
        service: BacktestQueryService dependency
        strategy: Filter by strategy name (exact match)
        instrument: Filter by instrument symbol (partial match)
        status: Filter by execution status
        sort: Column to sort by (default: created_at)
        order: Sort order (default: desc)
        page: Page number (1-indexed, default 1)
        page_size: Results per page (default 20, max 100)

    Returns:
        HTMLResponse with rendered backtest list template

    Example:
        >>> # GET /backtests
        >>> # GET /backtests?strategy=SMA%20Crossover&page=2
        >>> # GET /backtests?sort=sharpe_ratio&order=desc
    """
    logger.info(
        "Backtest list page requested",
        strategy=strategy,
        instrument=instrument,
        date_from=date_from.isoformat() if date_from else None,
        date_to=date_to.isoformat() if date_to else None,
        status=status.value if status else None,
        sort=sort.value,
        order=order.value,
        page=page,
        page_size=page_size,
    )

    # Build filter state from query parameters
    filter_state = FilterState(
        strategy=strategy,
        instrument=instrument,
        date_from=date_from,
        date_to=date_to,
        status=status,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )

    # Get filtered backtest list with error handling
    try:
        list_page = await service.get_filtered_backtest_list_page(filter_state)
    except Exception as e:
        logger.error(
            "Failed to fetch backtest list", error=str(e), filter_state=filter_state
        )
        # Return empty list on database error
        from src.api.models.backtest_list import FilteredBacktestListPage

        list_page = FilteredBacktestListPage(
            backtests=[],
            page=page,
            page_size=page_size,
            total_count=0,
            filter_state=filter_state,
            available_strategies=[],
            available_instruments=[],
        )

    # Build navigation state
    nav_state = NavigationState(
        active_page="backtests",
        breadcrumbs=[
            BreadcrumbItem(label="Dashboard", url="/", is_current=False),
            BreadcrumbItem(label="Backtests", url=None, is_current=True),
        ],
        app_version="0.1.0",
    )

    # Prepare empty state message if no backtests and no filters applied
    empty_state = None
    has_filters = any([strategy, instrument, date_from, date_to, status])
    if list_page.total_count == 0 and not has_filters:
        empty_state = EmptyStateMessage(
            title="No Backtests Yet",
            description="You haven't run any backtests yet. Run your first backtest to see results here.",
            action_text="Run your first backtest",
            action_command="ntrader backtest run --strategy sma_crossover --symbol AAPL",
        )

    context = {
        "request": request,
        "list_page": list_page,
        "nav_state": nav_state,
        "empty_state": empty_state,
        "has_filters": has_filters,
    }

    return templates.TemplateResponse("backtests/list.html", context)


@router.get("/fragment", response_class=HTMLResponse)
async def backtest_list_fragment(
    request: Request,
    service: BacktestService,
    strategy: Optional[str] = Query(None, max_length=255),
    instrument: Optional[str] = Query(None, max_length=50),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    sort: SortColumn = Query(SortColumn.CREATED_AT),
    order: SortOrder = Query(SortOrder.DESC),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> HTMLResponse:
    """
    Render HTMX fragment for backtest table (partial page update).

    Returns only the table content for HTMX partial updates without
    the full page layout. Supports all filtering and sorting parameters.

    Args:
        request: FastAPI request object
        service: BacktestQueryService dependency
        strategy: Filter by strategy name (exact match)
        instrument: Filter by instrument symbol (partial match)
        status: Filter by execution status
        sort: Column to sort by (default: created_at)
        order: Sort order (default: desc)
        page: Page number (1-indexed, default 1)
        page_size: Results per page (default 20, max 100)

    Returns:
        HTMLResponse with table fragment only

    Example:
        >>> # GET /backtests/fragment?page=2
        >>> # GET /backtests/fragment?strategy=SMA%20Crossover
        >>> # Used by HTMX hx-get for partial updates
    """
    # Convert empty strings to None for optional parameters
    strategy = strategy if strategy else None
    instrument = instrument if instrument else None

    # Parse date strings to date objects
    parsed_date_from = None
    if date_from:
        try:
            parsed_date_from = date.fromisoformat(date_from)
        except ValueError:
            pass

    parsed_date_to = None
    if date_to:
        try:
            parsed_date_to = date.fromisoformat(date_to)
        except ValueError:
            pass

    # Parse status enum
    parsed_status = None
    if status:
        try:
            parsed_status = ExecutionStatus(status)
        except ValueError:
            pass

    logger.info(
        "Backtest list fragment requested",
        strategy=strategy,
        instrument=instrument,
        date_from=parsed_date_from.isoformat() if parsed_date_from else None,
        date_to=parsed_date_to.isoformat() if parsed_date_to else None,
        status=parsed_status.value if parsed_status else None,
        sort=sort.value,
        order=order.value,
        page=page,
    )

    # Build filter state from query parameters
    filter_state = FilterState(
        strategy=strategy,
        instrument=instrument,
        date_from=parsed_date_from,
        date_to=parsed_date_to,
        status=parsed_status,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )

    # Get filtered backtest list with error handling
    try:
        list_page = await service.get_filtered_backtest_list_page(filter_state)
    except Exception as e:
        logger.error("Failed to fetch backtest list fragment", error=str(e))
        # Return empty list on database error
        from src.api.models.backtest_list import FilteredBacktestListPage

        list_page = FilteredBacktestListPage(
            backtests=[],
            page=page,
            page_size=page_size,
            total_count=0,
            filter_state=filter_state,
            available_strategies=[],
            available_instruments=[],
        )

    context = {
        "request": request,
        "list_page": list_page,
    }

    return templates.TemplateResponse("backtests/list_fragment.html", context)
