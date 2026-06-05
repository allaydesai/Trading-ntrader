"""Shared bar loader for backtest-run charts.

Resolves the OHLCV bars a chart should render for a specific backtest run,
honoring the run's *own* catalog and bar type. Used by both the candlestick
timeseries route and the indicator-overlay route so the two never diverge on
which data a run was executed against.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.services.data_catalog import DataCatalogService
from src.services.exceptions import CatalogError


def _resolve_named_nautilus_id(catalog_name: str, ticker: str) -> str | None:
    """Resolve a ticker to its DB-authoritative ``nautilus_id`` for a named catalog.

    Returns ``None`` when the database is unconfigured or the ticker is absent,
    letting the caller fall back to the bare symbol. Synchronous — call via
    ``asyncio.to_thread`` from async code.
    """
    from src.db.repositories.catalog_instrument_repository import (
        SyncCatalogInstrumentRepository,
    )
    from src.db.session_sync import get_sync_session_maker

    session_maker = get_sync_session_maker()
    if session_maker is None:
        return None
    session = session_maker()
    try:
        row = SyncCatalogInstrumentRepository(session).get_by_ticker(catalog_name, ticker)
        return row.nautilus_id if row and row.nautilus_id else None
    finally:
        session.close()


def resolve_run_bar_type_spec(backtest: Any) -> str:
    """Return the bar_type_spec a run's chart should query.

    Comes from ``config_snapshot.bar_type`` so intraday runs render their own
    bars instead of the ``1-DAY-LAST`` default.
    """
    cfg = backtest.config_snapshot or {}
    return cfg.get("bar_type") or "1-DAY-LAST"


async def _load_chart_bars(backtest: Any) -> list:
    """Load the OHLCV bars a backtest-run chart should be rendered against.

    Resolves the *run's own* data location instead of assuming the default
    NAUTILUS_PATH catalog with a daily bar type:

    - ``bar_type_spec`` comes from ``config_snapshot.bar_type`` so intraday runs
      no longer silently fetch ``1-DAY-LAST`` bars.
    - For a named-catalog run (``config_snapshot.catalog_name`` or a
      ``catalog:<name>`` ``data_source`` prefix), bars are read from that
      catalog's directory and the instrument is resolved to its DB-authoritative
      ``nautilus_id``. Previously these queried the default catalog with the bare
      symbol and returned nothing, leaving the chart empty on real data.
    """
    cfg = backtest.config_snapshot or {}
    bar_type_spec = resolve_run_bar_type_spec(backtest)

    catalog_name = cfg.get("catalog_name")
    if not catalog_name:
        data_source = cfg.get("data_source") or getattr(backtest, "data_source", "") or ""
        if isinstance(data_source, str) and data_source.startswith("catalog:"):
            catalog_name = data_source.split(":", 1)[1]

    start = datetime.combine(backtest.start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(backtest.end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    if catalog_name:
        base_path = get_settings().catalog.catalog_base_path
        if not base_path:
            raise CatalogError(
                "CATALOG_BASE_PATH is not configured; cannot resolve named catalog "
                f"'{catalog_name}' for chart rendering."
            )
        catalog_service = DataCatalogService(catalog_path=str(Path(base_path) / catalog_name))
        resolved = await asyncio.to_thread(
            _resolve_named_nautilus_id, catalog_name, backtest.instrument_symbol
        )
        instrument_id = resolved or backtest.instrument_symbol
    else:
        catalog_service = DataCatalogService()
        instrument_id = backtest.instrument_symbol

    return await asyncio.to_thread(
        catalog_service.query_bars,
        instrument_id=instrument_id,
        start=start,
        end=end,
        bar_type_spec=bar_type_spec,
    )
