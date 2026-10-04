"""OHLCV for one symbol from a named catalog, as a DataFrame (S2.5, S4.3).

Reads the Parquet catalog directly, the way the named-catalog loader does, but
without synthesising an instrument: the analyses only need prices.
``DataCatalogService`` is off-limits to the MCP (it can fetch from IBKR).
"""

from datetime import datetime

import pandas as pd

from src.db.repositories.catalog_instrument_repository import SyncCatalogInstrumentRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.catalogs import catalog_manager, require_catalog, timeframe_spec
from src.mcp_server.errors import ToolFailure
from src.services.firstrate.metadata_service import MetadataService

COLUMNS = ("open", "high", "low", "close", "volume")


def _bar_type(catalog: str, symbol: str, timeframe: str) -> str:
    with get_sync_session() as session:
        service = MetadataService(sync_repo=SyncCatalogInstrumentRepository(session))
        row = service.get_instrument_sync(catalog, symbol)
        nautilus_id = row.nautilus_id if row is not None else None
    if not nautilus_id:
        raise ToolFailure(
            "symbol_not_in_catalog",
            f"'{symbol}' has no backtestable instrument in catalog '{catalog}'.",
            fix="Check catalog_availability for this symbol.",
        )
    return f"{nautilus_id}-{timeframe_spec(timeframe)}-EXTERNAL"


def read_bars(
    catalog: str, symbol: str, timeframe: str, start: datetime, end: datetime
) -> pd.DataFrame:
    """Bars in ``[start, end]``, indexed by UTC bar time; empty when there are none."""
    require_catalog(catalog)
    symbol = symbol.strip().upper()
    bar_type = _bar_type(catalog, symbol, timeframe)
    bars = (
        catalog_manager().resolve_catalog(catalog).bars(bar_types=[bar_type], start=start, end=end)
    )
    frame = pd.DataFrame(
        [
            (
                bar.ts_event,
                bar.open.as_double(),
                bar.high.as_double(),
                bar.low.as_double(),
                bar.close.as_double(),
                bar.volume.as_double(),
            )
            for bar in bars
        ],
        columns=["ts", *COLUMNS],
    )
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("ts"), unit="ns", utc=True))
    frame.index.name = "time"
    return frame


def daily_closes(catalog: str, symbol: str, start: datetime, end: datetime) -> pd.Series:
    """Daily closing prices, one per UTC date."""
    frame = read_bars(catalog, symbol, "1-DAY", start, end)
    closes = frame["close"]
    closes.index = pd.DatetimeIndex(closes.index).normalize()
    return closes[~closes.index.duplicated(keep="last")]
