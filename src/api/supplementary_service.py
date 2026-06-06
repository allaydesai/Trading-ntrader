"""Shared supplementary-data helpers for the explorer routes (Story 4.2).

Mirrors ``src/api/stats_service.py``: a single ``_build_supplementary_context``
entry point assembles the company-profile / dividend / split data for a ticker
so both the ``/explorer/supplementary`` fragment route and the chart-panel OOB
swap consume identical logic. The ``format_split_ratio`` presenter converts the
stored ``Decimal`` ratio (new shares per old share) into a display string.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

import structlog

from src.db.repositories.catalog_dividend_repository import CatalogDividendRepository
from src.db.repositories.catalog_stock_split_repository import CatalogStockSplitRepository
from src.services.firstrate.metadata_service import MetadataService

logger = structlog.get_logger(__name__)


def format_split_ratio(ratio: Decimal) -> str:
    """Convert a stored split ``Decimal`` into a display ratio string.

    Rules (per Story 4.2 AC #4):
        - ``ratio >= 1`` and integral → ``"{int(ratio)}:1"`` (e.g. ``4`` → ``"4:1"``).
        - ``0 < ratio < 1`` → reverse split ``"1:{round(1/ratio)}"`` (``0.5`` → ``"1:2"``).
        - non-integral / unexpected → the plain ``Decimal`` string (unambiguous fallback).

    Args:
        ratio: New shares per old share, as delivered by FirstRate.

    Returns:
        Human-readable ratio string.
    """
    try:
        if ratio >= 1:
            if ratio == ratio.to_integral_value():
                return f"{int(ratio)}:1"
            return str(ratio)
        if ratio > 0:
            return f"1:{int(round(1 / ratio))}"
        return str(ratio)
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return str(ratio)


def _format_date(value: date) -> str:
    """Format a date as ``%Y-%m-%d`` (matching the explorer's stats panel)."""
    return value.strftime("%Y-%m-%d")


async def _build_supplementary_context(
    metadata_service: MetadataService,
    dividend_repo: CatalogDividendRepository,
    split_repo: CatalogStockSplitRepository,
    catalog: str,
    ticker: str,
) -> dict:
    """Assemble the template-ready supplementary context for a ticker.

    Supplementary data is *additive* — a missing company profile is not an
    error. Dividend/split lists come back newest-first from the repositories.

    Args:
        metadata_service: Async metadata service (company profile lookup).
        dividend_repo: Async dividend repository.
        split_repo: Async stock-split repository.
        catalog: Catalog name.
        ticker: Trading symbol.

    Returns:
        Dict with ``instrument``, ``dividends``, ``splits``,
        ``has_company_profile``, ``format_split_ratio``, ``format_date``.
    """
    instrument = await metadata_service.get_instrument(catalog, ticker)
    dividends = await dividend_repo.list_by_ticker(catalog, ticker)
    splits = await split_repo.list_by_ticker(catalog, ticker)

    logger.debug(
        "supplementary_context_built",
        catalog=catalog,
        ticker=ticker,
        has_profile=instrument is not None,
        dividend_count=len(dividends),
        split_count=len(splits),
    )

    return {
        "instrument": instrument,
        "dividends": dividends,
        "splits": splits,
        "has_company_profile": instrument is not None,
        "format_split_ratio": format_split_ratio,
        "format_date": _format_date,
    }
