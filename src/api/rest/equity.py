"""
Equity API endpoint for equity curve and drawdown data.

Provides portfolio value and drawdown time series.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.api.dependencies import BacktestService
from src.api.models.chart_errors import ErrorDetail
from src.api.models.chart_equity import DrawdownPoint, EquityPoint, EquityResponse

router = APIRouter()


def calculate_drawdown(equity_values: list[float]) -> list[float]:
    """
    Calculate drawdown percentages from equity curve.

    Args:
        equity_values: List of portfolio values

    Returns:
        List of drawdown percentages (negative numbers)

    Example:
        >>> calculate_drawdown([100000, 105000, 100000, 110000])
        [0.0, 0.0, -4.76, 0.0]
    """
    if not equity_values:
        return []

    drawdowns = []
    peak = equity_values[0]

    for value in equity_values:
        if value > peak:
            peak = value
        drawdown = ((value - peak) / peak) * 100 if peak > 0 else 0.0
        drawdowns.append(round(drawdown, 2))

    return drawdowns


@router.get(
    "/equity/{run_id}",
    response_model=EquityResponse,
    responses={
        404: {"model": ErrorDetail, "description": "Backtest not found"},
        422: {"description": "Validation error"},
    },
    summary="Get equity curve and drawdown",
    description="Returns portfolio value and drawdown time series",
)
async def get_equity(
    run_id: UUID,
    service: BacktestService,
) -> EquityResponse:
    """
    Get equity curve and drawdown data for a backtest run.

    Args:
        run_id: Backtest run UUID
        service: BacktestQueryService dependency

    Returns:
        EquityResponse with equity and drawdown arrays

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

    # Extract equity curve from config_snapshot
    config = backtest.config_snapshot or {}
    equity_data = config.get("equity_curve", [])

    # If no equity curve in config, generate a simple 2-point curve as fallback
    if not equity_data and backtest.metrics:
        # Create start and end points using initial capital and final balance
        start_timestamp = int(backtest.start_date.timestamp())
        end_timestamp = int(backtest.end_date.timestamp())
        initial_capital = float(backtest.initial_capital)
        final_balance = float(backtest.metrics.final_balance)

        equity_data = [
            {"time": start_timestamp, "value": initial_capital},
            {"time": end_timestamp, "value": final_balance},
        ]

    # Convert to EquityPoint objects
    equity_points = []
    equity_values = []

    for point in equity_data:
        # Handle both timestamp and date string formats
        time_value = point.get("time", "")
        if isinstance(time_value, str):
            # Convert date string to Unix timestamp
            try:
                dt = datetime.fromisoformat(time_value.replace("Z", "+00:00"))
                time_value = int(dt.timestamp())
            except (ValueError, AttributeError):
                # If parsing fails, skip this point
                continue
        elif isinstance(time_value, (int, float)):
            time_value = int(time_value)
        else:
            continue

        equity_point = EquityPoint(
            time=time_value,
            value=float(point.get("value", 0)),
        )
        equity_points.append(equity_point)
        equity_values.append(equity_point.value)

    # Calculate drawdown from equity values
    drawdown_values = calculate_drawdown(equity_values)

    # Create DrawdownPoint objects
    drawdown_points = []
    for i, equity_point in enumerate(equity_points):
        drawdown_point = DrawdownPoint(
            time=equity_point.time,
            value=drawdown_values[i] if i < len(drawdown_values) else 0.0,
        )
        drawdown_points.append(drawdown_point)

    return EquityResponse(
        run_id=run_id,
        equity=equity_points,
        drawdown=drawdown_points,
    )
