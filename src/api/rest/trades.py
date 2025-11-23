"""
Trades API endpoint for trade markers and equity curve.

Provides trade entry/exit points for chart overlay and equity curve generation.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.sql import func

from src.api.dependencies import BacktestService, DbSession
from src.api.models.chart_errors import ErrorDetail
from src.api.models.chart_trades import TradeMarker, TradesResponse
from src.db.models.trade import Trade
from src.models.trade import (
    DrawdownMetrics,
    EquityCurveResponse,
    PaginationMetadata,
    SortingMetadata,
    TradeListResponse,
    TradeStatistics,
)
from src.services.trade_analytics import (
    calculate_drawdowns,
    calculate_trade_statistics,
    generate_equity_curve,
)

router = APIRouter()


@router.get(
    "/trades/{run_id}",
    response_model=TradesResponse,
    responses={
        404: {"model": ErrorDetail, "description": "Backtest not found"},
        422: {"description": "Validation error"},
    },
    summary="Get trade markers for backtest",
    description="Returns trade entry/exit points for chart overlay",
)
async def get_trades(
    run_id: UUID,
    service: BacktestService,
) -> TradesResponse:
    """
    Get trade markers for a backtest run.

    Args:
        run_id: Backtest run UUID
        service: BacktestQueryService dependency

    Returns:
        TradesResponse with trade markers array

    Raises:
        HTTPException: 404 if backtest not found
    """
    # Get backtest from database
    backtest = await service.get_backtest_by_id(run_id)

    if not backtest:
        raise HTTPException(
            status_code=404,
            detail=f"Backtest run {run_id} not found",
        )

    # Extract trades from config_snapshot
    config = backtest.config_snapshot or {}
    trades_data = config.get("trades", [])

    # Convert to TradeMarker objects
    trades = []
    for trade in trades_data:
        marker = TradeMarker(
            time=trade.get("time", ""),
            side=trade.get("side", "buy"),
            price=float(trade.get("price", 0)),
            quantity=int(trade.get("quantity", 0)),
            pnl=float(trade.get("pnl", 0)),
        )
        trades.append(marker)

    # Sort trades by timestamp ascending
    trades.sort(key=lambda t: t.time)

    return TradesResponse(
        run_id=run_id,
        trade_count=len(trades),
        trades=trades,
    )


@router.get(
    "/equity-curve/{backtest_id}",
    response_model=EquityCurveResponse,
    responses={
        404: {"model": ErrorDetail, "description": "Backtest not found"},
        422: {"description": "Validation error"},
    },
    summary="Get equity curve for backtest",
    description=(
        "Returns time-series data showing account balance evolution based on trade executions"
    ),
)
async def get_equity_curve(
    backtest_id: int,
    service: BacktestService,
    db: DbSession,
) -> EquityCurveResponse:
    """
    Get equity curve showing cumulative account balance over time.

    The equity curve visualizes how the account balance evolved as trades were
    executed. Each point represents the balance after a trade exit, starting
    from initial capital.

    Args:
        backtest_id: Backtest run database ID
        service: BacktestQueryService dependency
        db: Database session dependency

    Returns:
        EquityCurveResponse with:
        - points: Array of (timestamp, balance, cumulative_return_pct) tuples
        - initial_capital: Starting account balance
        - final_balance: Ending balance after all trades
        - total_return_pct: Overall return percentage

    Raises:
        HTTPException: 404 if backtest not found

    Example:
        GET /api/v1/backtests/123/equity-curve

        Response:
        {
            "points": [
                {"timestamp": "2025-01-01T10:00:00Z", "balance": "100000.00", ...},
                {"timestamp": "2025-01-01T11:00:00Z", "balance": "100995.00", ...},
                ...
            ],
            "initial_capital": "100000.00",
            "final_balance": "101300.00",
            "total_return_pct": "1.30"
        }
    """
    # Get backtest from database using internal ID
    backtest = await service.get_backtest_by_internal_id(backtest_id)

    if not backtest:
        raise HTTPException(
            status_code=404,
            detail=f"Backtest run with ID {backtest_id} not found",
        )

    # Query trades for this backtest
    result = await db.execute(
        select(Trade).where(Trade.backtest_run_id == backtest_id).order_by(Trade.entry_timestamp)
    )
    trades = result.scalars().all()

    # Convert SQLAlchemy models to Pydantic models
    from src.models.trade import Trade as PydanticTrade

    pydantic_trades = [PydanticTrade.model_validate(trade) for trade in trades]

    # Generate equity curve
    equity_curve = generate_equity_curve(
        pydantic_trades,
        backtest.initial_capital,
    )

    return equity_curve


@router.get(
    "/statistics/{backtest_id}",
    response_model=TradeStatistics,
    responses={
        404: {"model": ErrorDetail, "description": "Backtest not found"},
        422: {"description": "Validation error"},
    },
    summary="Get trade statistics for backtest",
    description=(
        "Returns comprehensive trade performance metrics including win rate, "
        "profit factor, consecutive streaks, and holding period statistics"
    ),
)
async def get_trade_statistics(
    backtest_id: int,
    service: BacktestService,
    db: DbSession,
) -> TradeStatistics:
    """
    Get comprehensive trade statistics for a backtest run.

    Calculates detailed performance metrics from trade history including:
    - Trade counts (total, winning, losing, breakeven)
    - Win rate percentage
    - Profit metrics (total profit/loss, average, largest)
    - Risk metrics (profit factor, expectancy)
    - Consecutive win/loss streaks
    - Holding period statistics (average, max, min)

    Args:
        backtest_id: Backtest run database ID
        service: BacktestQueryService dependency
        db: Database session dependency

    Returns:
        TradeStatistics with comprehensive performance metrics

    Raises:
        HTTPException: 404 if backtest not found

    Example:
        GET /api/statistics/123

        Response:
        {
            "total_trades": 15,
            "winning_trades": 10,
            "losing_trades": 5,
            "win_rate": "66.67",
            "total_profit": "5000.00",
            "total_loss": "2000.00",
            "net_profit": "3000.00",
            "profit_factor": "2.50",
            "max_consecutive_wins": 4,
            "max_consecutive_losses": 2,
            ...
        }
    """
    # Get backtest from database using internal ID
    backtest = await service.get_backtest_by_internal_id(backtest_id)

    if not backtest:
        raise HTTPException(
            status_code=404,
            detail=f"Backtest run with ID {backtest_id} not found",
        )

    # Query trades for this backtest
    result = await db.execute(
        select(Trade).where(Trade.backtest_run_id == backtest_id).order_by(Trade.entry_timestamp)
    )
    trades = result.scalars().all()

    # Convert SQLAlchemy models to Pydantic models
    from src.models.trade import Trade as PydanticTrade

    pydantic_trades = [PydanticTrade.model_validate(trade) for trade in trades]

    # Calculate trade statistics
    statistics = calculate_trade_statistics(pydantic_trades)

    return statistics


@router.get(
    "/drawdown/{backtest_id}",
    response_model=DrawdownMetrics,
    responses={
        404: {"model": ErrorDetail, "description": "Backtest not found"},
        422: {"description": "Validation error"},
    },
    summary="Get drawdown metrics for backtest",
    description=(
        "Returns drawdown analysis from equity curve including maximum drawdown, "
        "drawdown periods, recovery times, and current drawdown status"
    ),
)
async def get_drawdown_metrics(
    backtest_id: int,
    service: BacktestService,
    db: DbSession,
) -> DrawdownMetrics:
    """
    Get comprehensive drawdown metrics for a backtest run.

    Analyzes equity curve to identify peak-to-trough drawdown periods, their
    recovery times, and provides comprehensive drawdown statistics including
    the maximum drawdown and top 5 largest drawdown periods.

    Args:
        backtest_id: Backtest run database ID
        service: BacktestQueryService dependency
        db: Database session dependency

    Returns:
        DrawdownMetrics with:
        - max_drawdown: Largest drawdown period by percentage
        - top_drawdowns: Up to 5 largest drawdown periods
        - current_drawdown: Ongoing drawdown if not yet recovered
        - total_drawdown_periods: Count of completed drawdown periods

    Raises:
        HTTPException: 404 if backtest not found

    Example:
        GET /api/drawdown/123

        Response:
        {
            "max_drawdown": {
                "peak_timestamp": "2025-01-01T11:00:00Z",
                "peak_balance": "120000.00",
                "trough_timestamp": "2025-01-01T13:00:00Z",
                "trough_balance": "100000.00",
                "drawdown_amount": "20000.00",
                "drawdown_pct": "16.6667",
                "duration_days": 0,
                "recovery_timestamp": "2025-01-01T14:00:00Z",
                "recovered": true
            },
            "top_drawdowns": [...],
            "current_drawdown": null,
            "total_drawdown_periods": 1
        }
    """
    # Get backtest from database using internal ID
    backtest = await service.get_backtest_by_internal_id(backtest_id)

    if not backtest:
        raise HTTPException(
            status_code=404,
            detail=f"Backtest run with ID {backtest_id} not found",
        )

    # Query trades for this backtest
    result = await db.execute(
        select(Trade).where(Trade.backtest_run_id == backtest_id).order_by(Trade.entry_timestamp)
    )
    trades = result.scalars().all()

    # Convert SQLAlchemy models to Pydantic models
    from src.models.trade import Trade as PydanticTrade

    pydantic_trades = [PydanticTrade.model_validate(trade) for trade in trades]

    # Generate equity curve first
    equity_curve = generate_equity_curve(
        pydantic_trades,
        backtest.initial_capital,
    )

    # Calculate drawdown metrics from equity curve
    drawdown_metrics = calculate_drawdowns(equity_curve.points)

    return drawdown_metrics


@router.get(
    "/backtests/{backtest_id}/trades",
    response_model=TradeListResponse,
    responses={
        404: {"model": ErrorDetail, "description": "Backtest not found"},
        422: {"description": "Validation error"},
    },
    summary="Get paginated trades for backtest",
    description=(
        "Returns paginated list of trades with server-side sorting and pagination "
        "controls for displaying in UI tables"
    ),
)
async def get_backtest_trades(
    backtest_id: int,
    service: BacktestService,
    db: DbSession,
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    sort_by: Literal["entry_timestamp", "exit_timestamp", "profit_loss"] = Query(
        "entry_timestamp", description="Field to sort by"
    ),
    sort_order: Literal["asc", "desc"] = Query(
        "asc", description="Sort order (ascending or descending)"
    ),
) -> TradeListResponse:
    """
    Get paginated trades list for a backtest run.

    Supports server-side pagination and sorting for efficient rendering of
    large trade datasets in UI tables. Useful for displaying trade history
    with configurable page sizes and sorting options.

    Args:
        backtest_id: Backtest run database ID
        service: BacktestQueryService dependency
        db: Database session dependency
        page: Page number (1-indexed, default: 1)
        page_size: Items per page (default: 20, max: 100)
        sort_by: Field to sort by (entry_timestamp, exit_timestamp, profit_loss)
        sort_order: Sort order (asc or desc, default: asc)

    Returns:
        TradeListResponse with:
        - trades: Array of Trade objects for current page
        - pagination: Metadata (total_items, total_pages, current_page, etc.)
        - sorting: Current sort configuration

    Raises:
        HTTPException: 404 if backtest not found

    Example:
        GET /api/backtests/123/trades?page=1&page_size=20&sort_by=entry_timestamp&sort_order=asc

        Response:
        {
            "trades": [...],
            "pagination": {
                "total_items": 500,
                "total_pages": 25,
                "current_page": 1,
                "page_size": 20,
                "has_next": true,
                "has_prev": false
            },
            "sorting": {
                "sort_by": "entry_timestamp",
                "sort_order": "asc"
            }
        }
    """
    # Verify backtest exists
    backtest = await service.get_backtest_by_internal_id(backtest_id)

    if not backtest:
        raise HTTPException(
            status_code=404,
            detail=f"Backtest run with ID {backtest_id} not found",
        )

    # Get total count of trades for this backtest
    count_result = await db.execute(
        select(func.count(Trade.id)).where(Trade.backtest_run_id == backtest_id)
    )
    total_items = count_result.scalar() or 0

    # Calculate pagination metadata
    total_pages = (total_items + page_size - 1) // page_size if total_items > 0 else 0
    has_next = page < total_pages
    has_prev = page > 1

    # Build query with sorting
    query = select(Trade).where(Trade.backtest_run_id == backtest_id)

    # Apply sorting
    sort_column = {
        "entry_timestamp": Trade.entry_timestamp,
        "exit_timestamp": Trade.exit_timestamp,
        "profit_loss": Trade.profit_loss,
    }[sort_by]

    if sort_order == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.limit(page_size).offset(offset)

    # Execute query
    result = await db.execute(query)
    trades = result.scalars().all()

    # Convert SQLAlchemy models to Pydantic models
    from src.models.trade import Trade as PydanticTrade

    pydantic_trades = [PydanticTrade.model_validate(trade) for trade in trades]

    # Build response
    return TradeListResponse(
        trades=pydantic_trades,
        pagination=PaginationMetadata(
            total_items=total_items,
            total_pages=total_pages,
            current_page=page,
            page_size=page_size,
            has_next=has_next,
            has_prev=has_prev,
        ),
        sorting=SortingMetadata(
            sort_by=sort_by,
            sort_order=sort_order,
        ),
    )
