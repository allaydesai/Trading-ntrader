"""
Backtests route handler for web UI.

Provides paginated backtest list and HTMX fragment endpoints.
"""

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.api.dependencies import BacktestService
from src.api.models.common import EmptyStateMessage
from src.api.models.navigation import BreadcrumbItem, NavigationState

logger = structlog.get_logger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def backtest_list(
    request: Request,
    service: BacktestService,
    page: int = 1,
) -> HTMLResponse:
    """
    Render the backtest list page with pagination.

    Displays a paginated table of all backtest runs with key metrics.

    Args:
        request: FastAPI request object
        service: BacktestQueryService dependency
        page: Page number (1-indexed, default 1)

    Returns:
        HTMLResponse with rendered backtest list template

    Example:
        >>> # GET /backtests
        >>> # GET /backtests?page=2
    """
    logger.info("Backtest list page requested", page=page)

    # Get paginated backtest list with error handling
    try:
        list_page = await service.get_backtest_list_page(page=page, page_size=20)
    except Exception as e:
        logger.error("Failed to fetch backtest list", error=str(e), page=page)
        # Return empty list on database error
        from src.api.models.backtest_list import BacktestListPage

        list_page = BacktestListPage(
            backtests=[], page=page, page_size=20, total_count=0
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

    # Prepare empty state message if no backtests
    empty_state = None
    if list_page.total_count == 0:
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
    }

    return templates.TemplateResponse("backtests/list.html", context)


@router.get("/fragment", response_class=HTMLResponse)
async def backtest_list_fragment(
    request: Request,
    service: BacktestService,
    page: int = 1,
) -> HTMLResponse:
    """
    Render HTMX fragment for backtest table (partial page update).

    Returns only the table content for HTMX partial updates without
    the full page layout.

    Args:
        request: FastAPI request object
        service: BacktestQueryService dependency
        page: Page number (1-indexed, default 1)

    Returns:
        HTMLResponse with table fragment only

    Example:
        >>> # GET /backtests/fragment?page=2
        >>> # Used by HTMX hx-get for partial updates
    """
    logger.info("Backtest list fragment requested", page=page)

    # Get paginated backtest list with error handling
    try:
        list_page = await service.get_backtest_list_page(page=page, page_size=20)
    except Exception as e:
        logger.error("Failed to fetch backtest list fragment", error=str(e), page=page)
        # Return empty list on database error
        from src.api.models.backtest_list import BacktestListPage

        list_page = BacktestListPage(
            backtests=[], page=page, page_size=20, total_count=0
        )

    context = {
        "request": request,
        "list_page": list_page,
    }

    return templates.TemplateResponse("backtests/list_fragment.html", context)
