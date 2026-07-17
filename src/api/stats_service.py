"""Shared stats helpers for the explorer REST and UI routes.

Provides a single `_build_ticker_stats` entry point used by both the JSON REST
endpoint (`/api/explorer/ticker/{ticker}/stats`) and the HTMX fragment
(`/explorer/stats-panel`) so tf validation, ticker resolution, date-range
clamping, and price-range computation live in one place.

Note: the stats panel queries the *full* date range of the instrument for the
active timeframe on purpose — chart panel windows, stats does not. Do not copy
`_compute_chart_window` logic here.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import HTTPException

from src.api.models.explorer import ExplorerTimeframe, TickerStatsResponse
from src.services.data_catalog import DataCatalogService
from src.services.exceptions import CatalogCorruptionError, DataNotFoundError
from src.services.firstrate.metadata_service import MetadataService

logger = structlog.get_logger(__name__)

VALID_TF_LABELS = {tf.label for tf in ExplorerTimeframe}


def _compute_price_range(bars) -> tuple[Optional[float], Optional[float]]:
    """Return (min-of-lows, max-of-highs) across the bars, or (None, None) if empty."""
    if not bars:
        return None, None
    lows = [bar.low.as_double() for bar in bars]
    highs = [bar.high.as_double() for bar in bars]
    return min(lows), max(highs)


async def _build_ticker_stats(
    service: MetadataService,
    catalog_service: DataCatalogService,
    catalog: str,
    ticker: str,
    tf: str,
    dividend_repo=None,
    split_repo=None,
) -> TickerStatsResponse:
    """Resolve ticker → instrument, compute per-tf price range, build response.

    When ``dividend_repo``/``split_repo`` are provided, the supplementary
    availability flags (``has_dividends``/``has_splits``/``has_company_profile``)
    are populated; otherwise they stay ``False`` for back-compat callers.

    Raises:
        HTTPException: 404 if the ticker is unknown or has no nautilus_id.
    """
    active_tf = ExplorerTimeframe.from_label(tf if tf in VALID_TF_LABELS else "D")

    instrument = await service.get_instrument(catalog, ticker)
    if instrument is None or not instrument.nautilus_id:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{ticker}' not found in catalog '{catalog}'",
        )
    nautilus_id: str = instrument.nautilus_id

    start_dt = instrument.date_range_start or datetime(1970, 1, 1, tzinfo=timezone.utc)
    end_dt = instrument.date_range_end or datetime(2099, 12, 31, tzinfo=timezone.utc)

    started = time.perf_counter()
    try:
        bars = await asyncio.to_thread(
            catalog_service.query_bars,
            instrument_id=nautilus_id,
            start=start_dt,
            end=end_dt,
            bar_type_spec=active_tf.bar_type_spec,
        )
    except DataNotFoundError:
        bars = []
        logger.debug(
            "stats_query_bars_empty",
            ticker=ticker,
            catalog=catalog,
            tf=active_tf.label,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except CatalogCorruptionError as exc:
        bars = []
        logger.warning(
            "stats_query_bars_corrupt",
            ticker=ticker,
            catalog=catalog,
            tf=active_tf.label,
            error=str(exc),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    else:
        logger.info(
            "stats_query_bars_complete",
            ticker=ticker,
            catalog=catalog,
            tf=active_tf.label,
            bar_count=len(bars),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    price_min, price_max = _compute_price_range(bars)

    has_dividends = (
        await dividend_repo.has_for_ticker(catalog, ticker) if dividend_repo is not None else False
    )
    has_splits = (
        await split_repo.has_for_ticker(catalog, ticker) if split_repo is not None else False
    )
    has_company_profile = (
        dividend_repo is not None or split_repo is not None
    ) and instrument is not None

    return TickerStatsResponse(
        ticker=ticker,
        nautilus_id=nautilus_id,
        date_range_start=instrument.date_range_start,
        date_range_end=instrument.date_range_end,
        bar_count_daily=getattr(instrument, "bar_count_daily", 0) or 0,
        bar_count_hourly=getattr(instrument, "bar_count_hourly", 0) or 0,
        bar_count_30min=getattr(instrument, "bar_count_30min", 0) or 0,
        bar_count_5min=getattr(instrument, "bar_count_5min", 0) or 0,
        bar_count_minute=getattr(instrument, "bar_count_minute", 0) or 0,
        price_min=price_min,
        price_max=price_max,
        active_tf=active_tf.label,
        has_dividends=has_dividends,
        has_splits=has_splits,
        has_company_profile=has_company_profile,
    )
