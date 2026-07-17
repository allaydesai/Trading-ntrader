"""
Explorer route handlers for web UI.

Provides the data explorer page with ticker browsing, search, and filtering,
plus chart panel fragment for HTMX partial updates.
"""

import asyncio
import json  # noqa: F401 — used in chart_panel_fragment
import math
from datetime import datetime, timedelta, timezone  # noqa: F401
from typing import Any, Final, Optional
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, HTTPException, Query, Request  # noqa: F401
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.api.dependencies import (  # noqa: F401
    CatalogList,
    DataCatalog,
    DefaultCatalog,
    DividendRepo,
    InstrumentMetadataRepo,
    Metadata,
    SplitRepo,
)
from src.api.metadata_panel_service import _build_metadata_panel_context
from src.api.models.chart_timeseries import Candle  # noqa: F401
from src.api.models.explorer import (
    EXPLORER_PAGE_SIZE,
    ExplorerPageState,
    ExplorerTimeframe,
    TickerRow,
)
from src.api.models.navigation import BreadcrumbItem, NavigationState
from src.api.stats_service import _build_ticker_stats
from src.api.supplementary_service import _build_supplementary_context
from src.services.exceptions import CatalogCorruptionError, DataNotFoundError

logger = structlog.get_logger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

PAGE_SIZE = EXPLORER_PAGE_SIZE
VALID_TF_LABELS = {tf.label for tf in ExplorerTimeframe}
ALL_TIMEFRAMES = list(ExplorerTimeframe)

# Single source of truth for explorer ↔ run-form timeframe translation.
# KeyError on unmapped explorer label is intentional (new timeframe must be added explicitly).
TIMEFRAME_EXPLORER_TO_RUN_FORM: Final[dict[str, str]] = {
    "D": "1-DAY",
    "1H": "1-HOUR",
    "30m": "30-MINUTE",
    "5m": "5-MINUTE",
    "1m": "1-MINUTE",
}
TIMEFRAME_RUN_FORM_TO_EXPLORER: Final[dict[str, str]] = {
    v: k for k, v in TIMEFRAME_EXPLORER_TO_RUN_FORM.items()
}


def _build_explorer_return(
    catalog: str,
    ticker: str,
    active_tf: ExplorerTimeframe,
    state: dict[str, Any],
) -> str:
    """Build the `/explorer?...` URL that 'Back to Explorer' will navigate to."""
    inner: list[tuple[str, str]] = [
        ("catalog", catalog),
        ("ticker", ticker),
        ("tf", active_tf.label),
    ]
    for key in ("search", "asset_class", "sort_by"):
        value = state.get(key)
        if value not in (None, ""):
            inner.append((key, str(value)))
    page = state.get("page")
    # Treat page=1 as default to keep the round-trip URL compact.
    if page not in (None, "", 0, "0", 1, "1"):
        inner.append(("page", str(page)))
    return f"/explorer?{urlencode(inner)}"


def _build_run_backtest_url(
    *,
    catalog: str,
    ticker: str,
    active_tf: ExplorerTimeframe,
    date_range_start: Optional[datetime],
    date_range_end: Optional[datetime],
    bar_count: int,
    explorer_state: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Build the `/backtests/run` bridge URL; returns None when button should be hidden.

    Returns None when `bar_count <= 0`. A missing `date_range_start` / `date_range_end`
    is NOT a hide condition — only bars-zero is (tests assert this invariant).
    """
    if bar_count <= 0:
        return None
    run_form_tf = TIMEFRAME_EXPLORER_TO_RUN_FORM[active_tf.label]
    params: list[tuple[str, str]] = [
        ("catalog", catalog),
        ("ticker", ticker),
        ("timeframe", run_form_tf),
    ]
    if date_range_start is not None:
        params.append(("start", date_range_start.strftime("%Y-%m-%d")))
    if date_range_end is not None:
        params.append(("end", date_range_end.strftime("%Y-%m-%d")))
    # Only emit explorer_return when at least one state field is actually set.
    # Truthiness on the dict itself would be True for `{"search": None, ...}` and
    # every anchor would carry a redundant duplicate of catalog+ticker+tf.
    if explorer_state and any(v not in (None, "") for v in explorer_state.values()):
        params.append(
            ("explorer_return", _build_explorer_return(catalog, ticker, active_tf, explorer_state))
        )
    return f"/backtests/run?{urlencode(params)}"


def _stats_template_context(stats) -> dict:
    """Flatten a TickerStatsResponse for the stats_panel.html template.

    Note: uses `active_tf_label` (string) instead of `active_tf` so this dict
    can be merged into the chart_panel.html context without clobbering its
    `active_tf` (ExplorerTimeframe enum) used for `.label` lookups.
    """
    return {
        "date_range_start": stats.date_range_start,
        "date_range_end": stats.date_range_end,
        "bar_count_daily": stats.bar_count_daily,
        "bar_count_hourly": stats.bar_count_hourly,
        "bar_count_5min": stats.bar_count_5min,
        "bar_count_minute": stats.bar_count_minute,
        "price_min": stats.price_min,
        "price_max": stats.price_max,
        "active_tf_label": stats.active_tf,
        "nautilus_id": stats.nautilus_id,
        "format_bar_count": _format_bar_count,
    }


@router.get("/stats-panel", response_class=HTMLResponse)
async def stats_panel_fragment(
    request: Request,
    service: Metadata,
    catalog_service: DataCatalog,
    catalog: str = Query(..., description="Catalog name"),
    ticker: str = Query(..., description="Ticker symbol"),
    tf: str = Query("D", description="Timeframe label"),
) -> HTMLResponse:
    """Return the stats-panel HTMX fragment (7 stat cards)."""
    stats = await _build_ticker_stats(service, catalog_service, catalog, ticker, tf)
    return templates.TemplateResponse(
        "explorer/stats_panel.html",
        {"request": request, **_stats_template_context(stats)},
    )


@router.get("/supplementary", response_class=HTMLResponse)
async def supplementary_panel_fragment(
    request: Request,
    service: Metadata,
    dividend_repo: DividendRepo,
    split_repo: SplitRepo,
    catalog: str = Query(..., description="Catalog name"),
    ticker: str = Query(..., description="Ticker symbol"),
) -> HTMLResponse:
    """Return the supplementary-panel HTMX fragment (3 collapsible sections).

    Supplementary data is timeframe-independent — no ``tf`` param. Additive:
    a missing company profile renders empty sections rather than a 404.
    """
    context = await _build_supplementary_context(
        service, dividend_repo, split_repo, catalog, ticker
    )
    return templates.TemplateResponse(
        "explorer/supplementary_panel.html",
        {"request": request, **context},
    )


@router.get("/metadata-panel", response_class=HTMLResponse)
async def metadata_panel_fragment(
    request: Request,
    metadata_repo: InstrumentMetadataRepo,
    ticker: str = Query(..., description="Ticker symbol"),
) -> HTMLResponse:
    """Return the ETF metadata-panel HTMX fragment (N/A-aware, Story 4-4).

    FMP-resolved metadata is keyed by ticker alone (catalog-independent) — no
    ``catalog`` param. Additive: a ticker with no resolved row renders a clear
    empty-state rather than a 404.
    """
    context = await _build_metadata_panel_context(metadata_repo, ticker)
    return templates.TemplateResponse(
        "explorer/metadata_panel.html",
        {"request": request, **context},
    )


def _format_bar_count(count: int) -> str:
    """Format bar count for compact display (e.g., 8K, 51K, 3.1M)."""
    if count >= 1_000_000:
        val = count / 1_000_000
        return f"{val:.1f}M" if val < 10 else f"{val:.0f}M"
    if count >= 1_000:
        val = count / 1_000
        return f"{val:.1f}K" if val < 10 else f"{val:.0f}K"
    return str(count)


def _build_page_state(
    catalog: str,
    search: Optional[str],
    asset_class: Optional[str],
    page: int,
    sort_by: str,
) -> ExplorerPageState:
    """Build ExplorerPageState from query params."""
    return ExplorerPageState(
        catalog=catalog,
        search=search or "",
        asset_class=asset_class or "",
        page=page,
        sort_by=sort_by,
    )


async def _get_ticker_data(
    service: Metadata,
    state: ExplorerPageState,
) -> tuple:
    """Fetch ticker data and asset class counts for the current state."""
    offset = (state.page - 1) * PAGE_SIZE

    instruments, total_count = await service.list_instruments_with_search(
        catalog_name=state.catalog,
        search=state.search or None,
        asset_class=state.asset_class or None,
        sort_by=state.sort_by,
        limit=PAGE_SIZE,
        offset=offset,
    )

    tickers = [
        TickerRow(
            ticker=inst.ticker,
            name=inst.name,
            asset_class=inst.asset_class,
            date_range_start=inst.date_range_start,
            date_range_end=inst.date_range_end,
            bar_count_daily=inst.bar_count_daily,
            bar_count_hourly=inst.bar_count_hourly,
            bar_count_5min=inst.bar_count_5min,
            bar_count_minute=inst.bar_count_minute,
            nautilus_id=inst.nautilus_id,
        )
        for inst in instruments
    ]

    total_pages = math.ceil(total_count / PAGE_SIZE) if total_count > 0 else 0
    asset_class_counts = await service.count_asset_classes(state.catalog)

    return tickers, total_count, total_pages, asset_class_counts


def _build_explorer_nav_state(selected_catalog: str) -> NavigationState:
    """Build the explorer breadcrumb/nav state for a given catalog.

    Shared by the full-page render and the ticker-list fragment so the
    breadcrumb stays in sync when the catalog is switched via HTMX.
    """
    return NavigationState(
        active_page="explorer",
        breadcrumbs=[
            BreadcrumbItem(label="Explorer", url="/explorer", is_current=False),
            BreadcrumbItem(
                label=selected_catalog or "No Catalog",
                url=None,
                is_current=True,
            ),
        ],
        app_version="0.1.0",
    )


@router.get("/", response_class=HTMLResponse)
async def explorer_page(
    request: Request,
    service: Metadata,
    catalogs: CatalogList,
    default_catalog: DefaultCatalog,
    catalog: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    asset_class: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    sort_by: str = Query("ticker"),
    ticker: Optional[str] = Query(None, description="Selected ticker for chart"),
    tf: str = Query("D", description="Timeframe label for chart"),
) -> HTMLResponse:
    """Render the full explorer page (AC #1, #5, #6).

    Args:
        request: FastAPI request object.
        service: MetadataService dependency.
        catalogs: List of available catalog names.
        default_catalog: Default catalog name from settings.
        catalog: Selected catalog name (query param).
        search: Search query (query param).
        asset_class: Asset class filter (query param).
        page: Page number (query param).
        sort_by: Sort column (query param).
        ticker: Selected ticker for chart loading (query param).

    Returns:
        HTMLResponse with rendered explorer page.
    """
    selected_catalog = catalog or default_catalog or (catalogs[0] if catalogs else "")

    nav_state = _build_explorer_nav_state(selected_catalog)

    if not catalogs or not selected_catalog:
        return templates.TemplateResponse(
            "explorer/explorer.html",
            {
                "request": request,
                "nav_state": nav_state,
                "catalogs": catalogs,
                "state": ExplorerPageState(catalog=selected_catalog or "none"),
                "tickers": [],
                "total_count": 0,
                "total_pages": 0,
                "asset_class_counts": {},
                "selected_ticker": None,
                "selected_tf": "D",
            },
        )

    state = _build_page_state(selected_catalog, search, asset_class, page, sort_by)
    tickers, total_count, total_pages, asset_class_counts = await _get_ticker_data(service, state)

    return templates.TemplateResponse(
        "explorer/explorer.html",
        {
            "request": request,
            "nav_state": nav_state,
            "catalogs": catalogs,
            "state": state,
            "tickers": tickers,
            "total_count": total_count,
            "total_pages": total_pages,
            "asset_class_counts": asset_class_counts,
            "format_bar_count": _format_bar_count,
            "selected_ticker": ticker,
            "selected_tf": tf,
        },
    )


@router.get("/ticker-list", response_class=HTMLResponse)
async def ticker_list_fragment(
    request: Request,
    service: Metadata,
    catalog: str = Query(...),
    search: Optional[str] = Query(None),
    asset_class: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    sort_by: str = Query("ticker"),
) -> HTMLResponse:
    """Return HTMX fragment for ticker table + pagination (AC #3, #4).

    Args:
        request: FastAPI request object.
        service: MetadataService dependency.
        catalog: Selected catalog name.
        search: Search query.
        asset_class: Asset class filter.
        page: Page number.
        sort_by: Sort column.

    Returns:
        HTMLResponse with ticker_list.html fragment.
    """
    state = _build_page_state(catalog, search, asset_class, page, sort_by)
    tickers, total_count, total_pages, asset_class_counts = await _get_ticker_data(service, state)

    return templates.TemplateResponse(
        "explorer/ticker_list.html",
        {
            "request": request,
            "state": state,
            "tickers": tickers,
            "total_count": total_count,
            "total_pages": total_pages,
            "asset_class_counts": asset_class_counts,
            "format_bar_count": _format_bar_count,
            "selected_ticker": None,
            # Re-render the breadcrumb out-of-band so switching catalog via the
            # dropdown (which only swaps #ticker-list) keeps it in sync.
            "nav_state": _build_explorer_nav_state(catalog),
            "oob_breadcrumb": True,
        },
    )


def _compute_chart_window(
    active_tf: ExplorerTimeframe,
    date_range_start: Optional[datetime],
    date_range_end: Optional[datetime],
) -> tuple[datetime, datetime, bool]:
    """Compute windowed start/end for initial chart load.

    Returns full 1970–2099 range when the timeframe has no window
    (daily). Otherwise returns a trailing window anchored to
    date_range_end, sized by initial_window_days.
    """
    window_days = active_tf.initial_window_days
    if window_days is None:
        return (
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(2099, 12, 31, tzinfo=timezone.utc),
            False,
        )
    anchor = date_range_end or datetime.now(timezone.utc)
    window_start = anchor - timedelta(days=window_days)
    has_earlier = date_range_start is not None and date_range_start < window_start
    return window_start, anchor, has_earlier


@router.get("/chart-panel", response_class=HTMLResponse)
async def chart_panel_fragment(
    request: Request,
    service: Metadata,
    catalog_service: DataCatalog,
    dividend_repo: DividendRepo,
    split_repo: SplitRepo,
    metadata_repo: InstrumentMetadataRepo,
    catalog: str = Query(..., description="Catalog name"),
    ticker: str = Query(..., description="Ticker symbol"),
    tf: str = Query("D", description="Timeframe label"),
    search: Optional[str] = Query(None, description="Explorer search query (for round-trip)"),
    asset_class: Optional[str] = Query(None, description="Explorer asset-class filter"),
    sort_by: Optional[str] = Query(None, description="Explorer sort column"),
    page: Optional[int] = Query(None, description="Explorer page number", ge=1),
) -> HTMLResponse:
    """Return HTMX fragment for chart panel with timeframe toolbar.

    Args:
        request: FastAPI request object.
        service: MetadataService dependency.
        catalog_service: DataCatalogService dependency.
        catalog: Catalog name.
        ticker: Ticker symbol.
        tf: Timeframe label (D, 1H, 5m, 1m).

    Returns:
        HTMLResponse with chart_panel.html fragment.

    Raises:
        HTTPException: 404 if ticker not found.
    """
    active_tf = ExplorerTimeframe.from_label(tf if tf in VALID_TF_LABELS else "D")

    instrument = await service.get_instrument(catalog, ticker)
    if instrument is None or not instrument.nautilus_id:
        raise HTTPException(
            status_code=404,
            detail=f"Ticker '{ticker}' not found in catalog '{catalog}'",
        )
    nautilus_id: str = instrument.nautilus_id

    # Determine available timeframes from bar counts
    available_tfs = set()
    for etf in ALL_TIMEFRAMES:
        count = getattr(instrument, etf.bar_count_field, 0) or 0
        if count > 0:
            available_tfs.add(etf.label)

    # Compute windowed date range for chart loading
    start_dt, end_dt, has_earlier_data = _compute_chart_window(
        active_tf,
        instrument.date_range_start,
        instrument.date_range_end,
    )

    try:
        bars = await asyncio.to_thread(
            catalog_service.query_bars,
            instrument_id=nautilus_id,
            start=start_dt,
            end=end_dt,
            bar_type_spec=active_tf.bar_type_spec,
        )
    except (DataNotFoundError, CatalogCorruptionError):
        # Degrade to the empty-state panel for both a missing instrument and a
        # corrupt parquet file — the stats path already swallows
        # CatalogCorruptionError, so a corrupt file must not 500 only the chart.
        bars = []

    candles = [
        Candle(
            time=int(bar.ts_event / 1e9),
            open=bar.open.as_double(),
            high=bar.high.as_double(),
            low=bar.low.as_double(),
            close=bar.close.as_double(),
            volume=int(bar.volume.as_double()),
        )
        for bar in bars
    ]

    bars_json = json.dumps([c.model_dump() for c in candles])
    window_days = active_tf.initial_window_days or 0
    dr_start = instrument.date_range_start
    dr_start_iso = dr_start.strftime("%Y-%m-%d") if dr_start else None
    no_data_message = f"No {active_tf.label} data available for {ticker}"

    stats = await _build_ticker_stats(service, catalog_service, catalog, ticker, tf)
    supplementary = await _build_supplementary_context(
        service, dividend_repo, split_repo, catalog, ticker
    )
    metadata_panel = await _build_metadata_panel_context(metadata_repo, ticker)

    explorer_state: dict[str, Any] = {
        "search": search,
        "asset_class": asset_class,
        "sort_by": sort_by,
        "page": page,
    }
    run_backtest_url = _build_run_backtest_url(
        catalog=catalog,
        ticker=ticker,
        active_tf=active_tf,
        date_range_start=instrument.date_range_start,
        date_range_end=instrument.date_range_end,
        bar_count=len(candles),
        explorer_state=explorer_state,
    )

    return templates.TemplateResponse(
        "explorer/chart_panel.html",
        {
            "request": request,
            "ticker": ticker,
            "catalog": catalog,
            "active_tf": active_tf,
            "timeframes": ALL_TIMEFRAMES,
            "available_tfs": available_tfs,
            "bars_json": bars_json,
            "bar_count": len(candles),
            "has_earlier_data": has_earlier_data,
            "window_days": window_days,
            "date_range_start_iso": dr_start_iso,
            "no_data_message": no_data_message,
            "run_backtest_url": run_backtest_url,
            "explorer_state_search": search,
            "explorer_state_asset_class": asset_class,
            "explorer_state_sort_by": sort_by,
            "explorer_state_page": page,
            **_stats_template_context(stats),
            **supplementary,
            **metadata_panel,
        },
    )
