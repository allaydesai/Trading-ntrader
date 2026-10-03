"""Catalog contents and per-timeframe coverage, read from instrument metadata (S1.4).

Coverage comes from ``catalog_instruments`` (one indexed row per ticker), never
from scanning Parquet files: ``DataCatalogService`` rebuilds a whole-catalog
availability cache on construction, which is far too slow for a chat tool.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from src.api.models.explorer import ExplorerTimeframe
from src.config import CatalogSettings
from src.db.models.catalog_instrument import CatalogInstrument
from src.db.repositories.catalog_instrument_repository import SyncCatalogInstrumentRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.metadata_service import MetadataService
from src.services.metadata.backtestable import non_backtestable_reason

#: Timeframes the MCP accepts: exactly those instrument metadata tracks coverage for.
SUPPORTED_TIMEFRAMES: dict[str, ExplorerTimeframe] = {
    tf.bar_type_spec.removesuffix("-LAST"): tf for tf in ExplorerTimeframe
}


def timeframe_spec(timeframe: str) -> str:
    """``"1-DAY"`` → ``"1-DAY-LAST"``; refuses timeframes without metadata coverage."""
    key = timeframe.strip().upper().removesuffix("-LAST")
    if key not in SUPPORTED_TIMEFRAMES:
        raise ToolFailure(
            "unsupported_timeframe",
            f"Timeframe '{timeframe}' is not supported.",
            fix=f"Use one of: {', '.join(SUPPORTED_TIMEFRAMES)}.",
        )
    return SUPPORTED_TIMEFRAMES[key].bar_type_spec


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def timeframe_coverage(row: Any, timeframe: str) -> dict[str, Any] | None:
    """Start, end and bar count for one timeframe, or None when it has no bars."""
    count_field = SUPPORTED_TIMEFRAMES[timeframe].bar_count_field
    bars = int(getattr(row, count_field) or 0)
    if bars == 0:
        return None
    end_field = count_field.replace("bar_count_", "date_range_end_")
    end = getattr(row, end_field, None) or row.date_range_end
    return {"start": _iso(row.date_range_start), "end": _iso(end), "bars": bars}


def describe_coverage(row: Any) -> dict[str, Any]:
    """Identity, backtestability and per-timeframe coverage of one instrument row."""
    timeframes = {
        tf: cov for tf in SUPPORTED_TIMEFRAMES if (cov := timeframe_coverage(row, tf)) is not None
    }
    return {
        "symbol": row.ticker,
        "catalog": row.catalog_name,
        "nautilus_id": row.nautilus_id,
        "asset_class": row.asset_class,
        "name": row.name,
        "backtestable": bool(row.nautilus_id),
        "timeframes": timeframes,
    }


def catalog_manager() -> CatalogManager:
    """The named-catalog directory configured by ``CATALOG_BASE_PATH``."""
    try:
        return CatalogManager(Path(CatalogSettings().catalog_base_path))
    except ValueError as exc:
        raise ToolFailure(
            "catalogs_not_configured", str(exc), fix="Set CATALOG_BASE_PATH in .env."
        ) from None


def list_catalogs() -> list[dict[str, Any]]:
    """Catalog directories with the number of instruments each has in metadata."""
    names = catalog_manager().list_catalogs()
    with get_sync_session() as session:
        stmt = select(CatalogInstrument.catalog_name, func.count()).group_by(
            CatalogInstrument.catalog_name
        )
        counts = {name: count for name, count in session.execute(stmt).all()}
    return [{"name": n, "instruments": counts.get(n, 0)} for n in names]


def require_catalog(catalog: str) -> None:
    """Refuse an unknown catalog name, listing the available ones."""
    available = catalog_manager().list_catalogs()
    if catalog not in available:
        raise ToolFailure(
            "unknown_catalog",
            f"No catalog named '{catalog}'.",
            fix=f"Use one of: {', '.join(available) or '(none found)'}.",
        )


def catalog_availability(symbol: str, catalog: str) -> dict[str, Any]:
    """Coverage of ``symbol`` in ``catalog``, or a failure saying how to get it there."""
    require_catalog(catalog)
    ticker = symbol.strip().upper()
    with get_sync_session() as session:
        service = MetadataService(sync_repo=SyncCatalogInstrumentRepository(session))
        row = service.get_instrument_sync(catalog, ticker)
        if row is None:
            raise ToolFailure(
                "symbol_not_in_catalog",
                f"'{ticker}' is not in catalog '{catalog}'.",
                fix="Import it with `ntrader data import-csv`, or choose another catalog "
                "(list_catalogs).",
            )
        coverage = describe_coverage(row)
        if not coverage["backtestable"]:
            status = service.get_resolution_status_sync(ticker)
            coverage["reason"] = non_backtestable_reason(status, ticker, catalog)
    return coverage
