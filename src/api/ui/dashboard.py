"""
Dashboard route handler for web UI.

Provides the main dashboard page with summary statistics and recent activity.
"""

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.api.dependencies import BacktestService
from src.api.models.navigation import BreadcrumbItem, NavigationState
from src.api.models.common import EmptyStateMessage

logger = structlog.get_logger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    service: BacktestService,
) -> HTMLResponse:
    """
    Render the main dashboard page.

    Displays summary statistics including total backtests, best Sharpe ratio,
    worst max drawdown, and recent backtest activity.

    Args:
        request: FastAPI request object
        service: BacktestQueryService dependency

    Returns:
        HTMLResponse with rendered dashboard template

    Example:
        >>> # GET /
        >>> # Returns dashboard with:
        >>> # - Total backtest count
        >>> # - Best Sharpe ratio and strategy name
        >>> # - Worst max drawdown and strategy name
        >>> # - 5 most recent backtests
    """
    logger.info("Dashboard page requested")

    # Get dashboard statistics with error handling
    try:
        stats = await service.get_dashboard_stats()
    except Exception as e:
        logger.error("Failed to fetch dashboard statistics", error=str(e))
        # Return empty stats on database error
        from src.api.models.dashboard import DashboardSummary

        stats = DashboardSummary(total_backtests=0)

    # Build navigation state
    nav_state = NavigationState(
        active_page="dashboard",
        breadcrumbs=[
            BreadcrumbItem(label="Dashboard", url=None, is_current=True),
        ],
        app_version="0.1.0",
    )

    # Prepare empty state message if no backtests
    empty_state = None
    if stats.total_backtests == 0:
        empty_state = EmptyStateMessage(
            title="No Backtests Yet",
            description="You haven't run any backtests yet. Run your first backtest to see statistics here.",
            action_text="Run your first backtest",
            action_command="ntrader backtest run --strategy sma_crossover --symbol AAPL",
        )

    context = {
        "request": request,
        "stats": stats,
        "nav_state": nav_state,
        "empty_state": empty_state,
    }

    return templates.TemplateResponse("dashboard.html", context)
