"""FX symbol forms and bar price types for run requests (M5, design D1/D2).

``EURUSD``, ``EUR/USD``, ``eur/usd`` and ``EUR.USD`` all name the catalog ticker ``EURUSD``.
A catalog row whose ``asset_class`` is ``FX`` has ``MID`` bars (``1-HOUR-MID``), so a
``--timeframe 1-HOUR`` request loads those instead of the ``-LAST`` default. Only a symbol that
names a shipped pair spec is looked up, so equity and futures requests never touch the DB here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.services.fx.pair_specs import pair_names

FX_ASSET_CLASS = "FX"
FX_PRICE_TYPE = "MID"
DEFAULT_PRICE_TYPE = "LAST"


def fx_ticker(symbol: str) -> str | None:
    """The pair-spec ticker (``EURUSD``) a symbol names, or ``None`` if it names no pair."""
    ticker = symbol.strip().upper().replace("/", "").replace(".", "")
    return ticker if ticker in pair_names() else None


def price_type_for(asset_class: str | None) -> str:
    """``MID`` for FX catalog rows, ``LAST`` for everything else."""
    return FX_PRICE_TYPE if (asset_class or "").upper() == FX_ASSET_CLASS else DEFAULT_PRICE_TYPE


def with_price_type(bar_type_spec: str, price_type: str) -> str:
    """``1-HOUR-LAST`` (or ``1-HOUR``) with ``MID`` -> ``1-HOUR-MID``."""
    step, aggregation = bar_type_spec.upper().split("-")[:2]
    return f"{step}-{aggregation}-{price_type}"


def _catalog_row(catalog_name: str, ticker: str) -> Any:
    """The ``catalog_instruments`` row (its ``asset_class`` read inside the session)."""
    from src.db.repositories.catalog_instrument_repository import (
        SyncCatalogInstrumentRepository,
    )
    from src.db.session_sync import get_sync_session

    with get_sync_session() as session:
        row = SyncCatalogInstrumentRepository(session).get_by_ticker(catalog_name, ticker)
        return None if row is None else SimpleNamespace(asset_class=row.asset_class)


def resolve_fx_symbol(
    catalog_name: str | None, symbol: str, bar_type_spec: str
) -> tuple[str, str] | None:
    """``(ticker, MID bar spec)`` when ``symbol`` is an FX row of ``catalog_name``, else None."""
    ticker = fx_ticker(symbol)
    if not catalog_name or ticker is None:
        return None
    row = _catalog_row(catalog_name, ticker)
    if row is None or price_type_for(row.asset_class) != FX_PRICE_TYPE:
        return None
    return ticker, with_price_type(bar_type_spec, FX_PRICE_TYPE)
