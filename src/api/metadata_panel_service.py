"""Context builder for the explorer ETF metadata panel (Story 4.4).

Mirrors ``src/api/supplementary_service.py``: a single
``_build_metadata_panel_context`` entry point assembles the FMP-resolved
metadata for a ticker so both the ``/explorer/metadata-panel`` fragment route
and the chart-panel OOB swap consume identical logic.

``instrument_metadata`` is keyed by ticker alone (catalog-independent, Story
1.2), so the lookup takes no catalog. The panel is *additive*: a ticker with no
resolved row degrades to a clear empty-state, never a 404.
"""

from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError

from src.api.models.metadata_panel import EtfMetadataPanel
from src.db.repositories.instrument_metadata_repository import InstrumentMetadataRepository
from src.models.instrument_metadata import NA_SENTINEL

logger = structlog.get_logger(__name__)


async def _build_metadata_panel_context(
    metadata_repo: InstrumentMetadataRepository,
    ticker: str,
) -> dict[str, Any]:
    """Assemble the template-ready metadata-panel context for a ticker.

    Args:
        metadata_repo: Async instrument-metadata repository.
        ticker: Trading symbol.

    Returns:
        Dict with ``metadata`` (``EtfMetadataPanel`` or ``None``),
        ``has_metadata`` (bool), and ``na_sentinel`` (for styling a
        descriptive ``N/A`` without hardcoding the literal in the template).

    A DB fault (e.g. the ``instrument_metadata`` table not yet migrated, or the
    connection dropping) degrades to the same additive empty-state — the panel
    is a new dependency of the chart panel, so it must never turn a metadata
    read failure into a 500 that takes down the whole chart.
    """
    try:
        row = await metadata_repo.get_by_ticker(ticker)
    except SQLAlchemyError as exc:
        logger.warning("metadata_panel_db_unavailable", ticker=ticker, error=str(exc))
        return {"metadata": None, "has_metadata": False, "na_sentinel": NA_SENTINEL}

    logger.debug(
        "metadata_panel_context_built",
        ticker=ticker,
        has_metadata=row is not None,
    )

    metadata = EtfMetadataPanel.from_orm_row(row) if row is not None else None
    return {
        "metadata": metadata,
        "has_metadata": row is not None,
        "na_sentinel": NA_SENTINEL,
    }
