"""
Indicators API endpoint for indicator series.

Provides indicator values for chart overlay using Nautilus Trader indicator classes
to ensure identical calculations to the strategy.
"""

from datetime import datetime, timezone
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException

from src.api.dependencies import BacktestService
from src.api.models.chart_errors import ErrorDetail
from src.api.models.chart_indicators import IndicatorPoint, IndicatorsResponse
from src.services.data_catalog import DataCatalogService

router = APIRouter()
logger = structlog.get_logger(__name__)


def _compute_bollinger_indicators(
    bars: list,
    strategy_config: dict,
) -> dict[str, list[IndicatorPoint]]:
    """
    Compute Bollinger Bands and Weekly SMA using Nautilus Trader indicators.

    Uses the exact same indicator classes and parameters as the strategy
    to ensure identical values.

    Args:
        bars: List of Nautilus Bar objects
        strategy_config: Strategy configuration with indicator parameters

    Returns:
        Dictionary of indicator name to list of IndicatorPoint
    """
    from nautilus_trader.indicators import BollingerBands, SimpleMovingAverage

    indicators: dict[str, list[IndicatorPoint]] = {}

    # Extract parameters from strategy config (same defaults as strategy)
    bb_period = int(strategy_config.get("daily_bb_period", 20))
    bb_std = float(strategy_config.get("daily_bb_std_dev", 2.0))
    weekly_ma_period = int(strategy_config.get("weekly_ma_period", 20))

    logger.info(
        "computing_bollinger_indicators",
        bb_period=bb_period,
        bb_std=bb_std,
        weekly_ma_period=weekly_ma_period,
        bar_count=len(bars),
    )

    # Initialize Nautilus Trader indicators (same as strategy)
    bollinger = BollingerBands(period=bb_period, k=bb_std)
    weekly_sma = SimpleMovingAverage(period=weekly_ma_period)

    # Weekly aggregation state (same logic as strategy)
    current_week_iso: tuple[int, int] | None = None
    current_week_close: float | None = None

    upper_band: list[IndicatorPoint] = []
    middle_band: list[IndicatorPoint] = []
    lower_band: list[IndicatorPoint] = []
    weekly_sma_values: list[IndicatorPoint] = []

    for bar in bars:
        # Update Bollinger Bands (same as strategy)
        bollinger.handle_bar(bar)

        # Handle Weekly SMA aggregation (same logic as strategy on_bar)
        bar_dt = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc)
        iso_year, iso_week, _ = bar_dt.isocalendar()
        current_iso = (iso_year, iso_week)

        if current_week_iso is None:
            # First bar seen
            current_week_iso = current_iso
            current_week_close = float(bar.close)
        elif current_iso != current_week_iso:
            # Week has changed - commit previous week's close to SMA
            if current_week_close is not None:
                weekly_sma.update_raw(current_week_close)
            # Reset for new week
            current_week_iso = current_iso
            current_week_close = float(bar.close)
        else:
            # Same week, update running close
            current_week_close = float(bar.close)

        # Capture values after processing
        time_str = bar_dt.strftime("%Y-%m-%d")

        if bollinger.initialized:
            upper_band.append(IndicatorPoint(time=time_str, value=float(bollinger.upper)))
            middle_band.append(IndicatorPoint(time=time_str, value=float(bollinger.middle)))
            lower_band.append(IndicatorPoint(time=time_str, value=float(bollinger.lower)))

        if weekly_sma.initialized:
            weekly_sma_values.append(IndicatorPoint(time=time_str, value=float(weekly_sma.value)))

    indicators["upper_band"] = upper_band
    indicators["middle_band"] = middle_band
    indicators["lower_band"] = lower_band
    indicators["weekly_sma"] = weekly_sma_values

    logger.info(
        "bollinger_indicators_computed",
        upper_band_count=len(upper_band),
        middle_band_count=len(middle_band),
        lower_band_count=len(lower_band),
        weekly_sma_count=len(weekly_sma_values),
    )

    return indicators


def _compute_sma_indicators(
    bars: list,
    strategy_config: dict,
) -> dict[str, list[IndicatorPoint]]:
    """
    Compute SMA indicators using Nautilus Trader indicators.

    Uses the exact same indicator classes and parameters as the strategy
    to ensure identical values.

    Args:
        bars: List of Nautilus Bar objects
        strategy_config: Strategy configuration with indicator parameters

    Returns:
        Dictionary of indicator name to list of IndicatorPoint
    """
    from nautilus_trader.indicators import SimpleMovingAverage

    indicators: dict[str, list[IndicatorPoint]] = {}

    # Extract parameters from strategy config (same defaults as SMA Crossover strategy)
    fast_period = int(strategy_config.get("fast_period", 10))
    slow_period = int(strategy_config.get("slow_period", 20))

    logger.info(
        "computing_sma_indicators",
        fast_period=fast_period,
        slow_period=slow_period,
        bar_count=len(bars),
    )

    # Initialize Nautilus Trader indicators (same as strategy)
    fast_sma = SimpleMovingAverage(period=fast_period)
    slow_sma = SimpleMovingAverage(period=slow_period)

    fast_sma_values: list[IndicatorPoint] = []
    slow_sma_values: list[IndicatorPoint] = []

    for bar in bars:
        # Update SMAs (same as strategy)
        fast_sma.handle_bar(bar)
        slow_sma.handle_bar(bar)

        # Capture values after processing
        bar_dt = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc)
        time_str = bar_dt.strftime("%Y-%m-%d")

        if fast_sma.initialized:
            fast_sma_values.append(IndicatorPoint(time=time_str, value=float(fast_sma.value)))

        if slow_sma.initialized:
            slow_sma_values.append(IndicatorPoint(time=time_str, value=float(slow_sma.value)))

    indicators["sma_fast"] = fast_sma_values
    indicators["sma_slow"] = slow_sma_values

    logger.info(
        "sma_indicators_computed",
        sma_fast_count=len(fast_sma_values),
        sma_slow_count=len(slow_sma_values),
    )

    return indicators


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

    Computes indicators on-demand using the same Nautilus Trader indicator
    classes and parameters that the strategy used during backtest execution.
    This ensures indicator values exactly match what the strategy saw.

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

    config_snapshot = backtest.config_snapshot or {}
    strategy_config = config_snapshot.get("config", {})
    strategy_path = config_snapshot.get("strategy_path", "")

    logger.info(
        "computing_indicators_for_backtest",
        run_id=str(run_id),
        strategy_path=strategy_path,
        instrument=backtest.instrument_symbol,
        start_date=str(backtest.start_date),
        end_date=str(backtest.end_date),
    )

    indicators: dict[str, list[IndicatorPoint]] = {}

    # Determine strategy type and compute appropriate indicators
    if "bollinger" in strategy_path.lower():
        try:
            # Load OHLCV bars from catalog
            catalog = DataCatalogService()

            # Query bars for the backtest period
            bars = catalog.query_bars(
                instrument_id=backtest.instrument_symbol,
                start=datetime.combine(backtest.start_date, datetime.min.time()).replace(
                    tzinfo=timezone.utc
                ),
                end=datetime.combine(backtest.end_date, datetime.max.time()).replace(
                    tzinfo=timezone.utc
                ),
                bar_type_spec="1-DAY-LAST",
            )

            indicators = _compute_bollinger_indicators(bars, strategy_config)

        except Exception as e:
            logger.error(
                "failed_to_compute_indicators",
                run_id=str(run_id),
                error=str(e),
            )
            # Return empty indicators on error rather than failing the request
            indicators = {}

    elif "sma" in strategy_path.lower() or "crossover" in strategy_path.lower():
        try:
            # Load OHLCV bars from catalog
            catalog = DataCatalogService()

            # Query bars for the backtest period
            bars = catalog.query_bars(
                instrument_id=backtest.instrument_symbol,
                start=datetime.combine(backtest.start_date, datetime.min.time()).replace(
                    tzinfo=timezone.utc
                ),
                end=datetime.combine(backtest.end_date, datetime.max.time()).replace(
                    tzinfo=timezone.utc
                ),
                bar_type_spec="1-DAY-LAST",
            )

            indicators = _compute_sma_indicators(bars, strategy_config)

        except Exception as e:
            logger.error(
                "failed_to_compute_indicators",
                run_id=str(run_id),
                error=str(e),
            )
            # Return empty indicators on error rather than failing the request
            indicators = {}

    else:
        logger.info(
            "no_indicator_computation_for_strategy",
            strategy_path=strategy_path,
        )

    return IndicatorsResponse(
        run_id=run_id,
        indicators=indicators,
    )
