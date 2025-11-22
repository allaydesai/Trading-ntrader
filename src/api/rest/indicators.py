"""
Indicators API endpoint for indicator series.

Provides indicator values for chart overlay.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException

from src.api.dependencies import BacktestService
from src.api.models.chart_errors import ErrorDetail
from src.api.models.chart_indicators import IndicatorPoint, IndicatorsResponse

router = APIRouter()


@router.get(
    "/indicators/{run_id}",
    response_model=IndicatorsResponse,
    responses={
        404: {"model": ErrorDetail, "description": "Backtest not found"},
        422: {"description": "Validation error"},
    },
    summary="Get indicator series",
    description="Returns indicator values for chart overlay",
)
async def get_indicators(
    run_id: UUID,
    service: BacktestService,
) -> IndicatorsResponse:
    """
    Get indicator series for a backtest run.

    Args:
        run_id: Backtest run UUID
        service: BacktestQueryService dependency

    Returns:
        IndicatorsResponse with indicators dictionary

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

    # Extract indicators from config_snapshot
    config = backtest.config_snapshot or {}
    indicators_data = config.get("indicators", {})

    # Convert to IndicatorPoint objects
    indicators: dict[str, list[IndicatorPoint]] = {}

    for name, points in indicators_data.items():
        indicator_points = []
        for point in points:
            indicator_point = IndicatorPoint(
                time=point.get("time", ""),
                value=float(point.get("value", 0)),
            )
            indicator_points.append(indicator_point)

        # Sort by timestamp ascending
        indicator_points.sort(key=lambda p: p.time)
        indicators[name] = indicator_points

    return IndicatorsResponse(
        run_id=run_id,
        indicators=indicators,
    )
