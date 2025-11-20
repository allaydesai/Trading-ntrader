"""
Trades API endpoint for trade markers.

Provides trade entry/exit points for chart overlay.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.api.dependencies import BacktestService
from src.api.models.chart_errors import ErrorDetail
from src.api.models.chart_trades import TradeMarker, TradesResponse

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
