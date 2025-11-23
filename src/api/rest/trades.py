"""
Trades API endpoint for trade markers and equity curve.

Provides trade entry/exit points for chart overlay and equity curve generation.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from src.api.dependencies import BacktestService, DbSession
from src.api.models.chart_errors import ErrorDetail
from src.api.models.chart_trades import TradeMarker, TradesResponse
from src.db.models.trade import Trade
from src.models.trade import EquityCurveResponse, TradeStatistics
from src.services.trade_analytics import calculate_trade_statistics, generate_equity_curve

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
