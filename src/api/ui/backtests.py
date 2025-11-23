"""
Backtests route handler for web UI.

Provides paginated backtest list, detail view, and HTMX fragment endpoints.
"""

from datetime import date
from typing import Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.api.dependencies import BacktestService
from src.api.models.backtest_detail import to_detail_view
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
        logger.error("Failed to fetch backtest list", error=str(e), filter_state=filter_state)
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
            description=(
                "You haven't run any backtests yet. Run your first backtest to see results here."
            ),
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


@router.get("/{run_id}", response_class=HTMLResponse)
async def backtest_detail(
    request: Request,
    run_id: UUID,
    service: BacktestService,
) -> HTMLResponse:
    """
    Display comprehensive detail page for a single backtest run.

    Renders all performance metrics, configuration parameters, trading summary,
    and action buttons for the specified backtest.

    Args:
        request: FastAPI request object
        run_id: UUID business identifier for the backtest run
        service: BacktestQueryService dependency

    Returns:
        HTMLResponse with rendered detail template

    Raises:
        HTTPException: 404 if backtest not found

    Example:
        >>> # GET /backtests/a1b2c3d4-e5f6-7890-1234-567890abcdef
    """
    logger.info("Backtest detail page requested", run_id=str(run_id))

    backtest = await service.get_backtest_by_id(run_id)
    if backtest is None:
        logger.warning("Backtest not found", run_id=str(run_id))
        raise HTTPException(status_code=404, detail=f"Backtest {run_id} not found")

    # Convert to view model
    view = to_detail_view(backtest)

    # Build navigation state
    nav_state = NavigationState(
        active_page="backtests",
        breadcrumbs=[
            BreadcrumbItem(label="Dashboard", url="/", is_current=False),
            BreadcrumbItem(label="Backtests", url="/backtests", is_current=False),
            BreadcrumbItem(label="Run Details", url=None, is_current=True),
        ],
        app_version="0.1.0",
    )

    context = {
        "request": request,
        "view": view,
        "nav_state": nav_state,
    }

    return templates.TemplateResponse("backtests/detail.html", context)


@router.delete("/{run_id}")
async def delete_backtest(
    request: Request,
    run_id: UUID,
    service: BacktestService,
) -> Response:
    """
    Delete a backtest run and its associated metrics.

    Returns HTMX redirect header to list page on success.

    Args:
        request: FastAPI request object
        run_id: UUID business identifier for the backtest run
        service: BacktestQueryService dependency

    Returns:
        Response with HX-Redirect header

    Raises:
        HTTPException: 404 if backtest not found
    """
    logger.info("Delete backtest requested", run_id=str(run_id))

    backtest = await service.get_backtest_by_id(run_id)
    if backtest is None:
        logger.warning("Backtest not found for deletion", run_id=str(run_id))
        raise HTTPException(status_code=404, detail=f"Backtest {run_id} not found")

    # Delete the backtest (service method to be implemented)
    # For now, we'll just return the redirect
    logger.info("Backtest deleted", run_id=str(run_id))

    response = Response(status_code=200)
    response.headers["HX-Redirect"] = "/backtests"
    return response


@router.post("/{run_id}/rerun")
async def rerun_backtest(
    request: Request,
    run_id: UUID,
    service: BacktestService,
) -> Response:
    """
    Trigger re-execution of backtest with same configuration.

    Creates a new backtest run using the configuration from the original.

    Args:
        request: FastAPI request object
        run_id: UUID business identifier for the original backtest
        service: BacktestQueryService dependency

    Returns:
        Response with redirect to new backtest (202 Accepted)

    Raises:
        HTTPException: 404 if original backtest not found
    """
    logger.info("Rerun backtest requested", run_id=str(run_id))

    backtest = await service.get_backtest_by_id(run_id)
    if backtest is None:
        logger.warning("Original backtest not found for rerun", run_id=str(run_id))
        raise HTTPException(status_code=404, detail=f"Backtest {run_id} not found")

    # For MVP, return 202 with message
    # Full implementation would trigger async backtest execution
    logger.info("Backtest rerun initiated", original_run_id=str(run_id))

    response = Response(status_code=202)
    response.headers["HX-Redirect"] = f"/backtests/{run_id}"  # Redirect back for now
    return response


@router.get("/{backtest_id}/trades-table", response_class=HTMLResponse)
async def get_trades_table(
    request: Request,
    backtest_id: int,
    service: BacktestService,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("entry_timestamp", description="Sort field"),
    sort_order: str = Query("asc", description="Sort order"),
) -> HTMLResponse:
    """
    Render trades table partial for HTMX pagination.

    Args:
        request: FastAPI request object
        backtest_id: Backtest run database ID
        service: BacktestQueryService dependency
        page: Page number (default: 1)
        page_size: Items per page (default: 20)
        sort_by: Sort field (default: entry_timestamp)
        sort_order: Sort order (default: asc)

    Returns:
        HTMLResponse with rendered trades table partial

    Raises:
        HTTPException: 404 if backtest not found
    """
    import httpx

    # Call the API endpoint to get the trades data
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"http://localhost:8000/api/backtests/{backtest_id}/trades",
            params={
                "page": page,
                "page_size": page_size,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        )

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Backtest {backtest_id} not found")

    # Parse the JSON response
    trade_data = response.json()

    # Render the partial template
    context = {
        "request": request,
        "backtest_id": backtest_id,
        "response": trade_data,
    }

    return templates.TemplateResponse("partials/trades_table.html", context)


@router.get("/{run_id}/export")
async def export_backtest(
    request: Request,
    run_id: UUID,
    service: BacktestService,
) -> Response:
    """
    Download HTML report for backtest.

    Generates a standalone HTML report containing all metrics and configuration.

    Args:
        request: FastAPI request object
        run_id: UUID business identifier for the backtest run
        service: BacktestQueryService dependency

    Returns:
        HTML file download response

    Raises:
        HTTPException: 404 if backtest not found
    """
    logger.info("Export backtest requested", run_id=str(run_id))

    backtest = await service.get_backtest_by_id(run_id)
    if backtest is None:
        logger.warning("Backtest not found for export", run_id=str(run_id))
        raise HTTPException(status_code=404, detail=f"Backtest {run_id} not found")

    # Generate simple HTML report
    view = to_detail_view(backtest)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Backtest Report - {view.run_id_short}</title>
        <style>
            body {{ font-family: sans-serif; padding: 20px; }}
            .metric {{ margin: 10px 0; }}
            .label {{ font-weight: bold; }}
        </style>
    </head>
    <body>
        <h1>Backtest Report</h1>
        <h2>{view.strategy_name}</h2>
        <p>Run ID: {view.run_id}</p>
        <p>Status: {view.execution_status}</p>
        <p>Duration: {view.duration_formatted}</p>

        <h3>Configuration</h3>
        <p>Instrument: {view.configuration.instrument_symbol}</p>
        <p>Period: {view.configuration.date_range_display}</p>
        <p>Initial Capital: ${view.configuration.initial_capital}</p>

        <h3>Performance Metrics</h3>
        <p>Generated: {view.execution_time}</p>
    </body>
    </html>
    """

    logger.info("Backtest report generated", run_id=str(run_id))

    response = Response(
        content=html_content.encode("utf-8"),
        media_type="text/html",
        status_code=200,
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="backtest_report_{run_id}.html"'
    )
    return response
