"""Unit tests for the ETF metadata panel context builder (Story 4.4)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from src.api.metadata_panel_service import _build_metadata_panel_context
from src.api.models.metadata_panel import EtfMetadataPanel
from src.db.models.instrument_metadata import InstrumentMetadata as OrmInstrumentMetadata
from src.models.instrument_metadata import NA_SENTINEL, ResolutionStatus


def _make_orm(**overrides) -> OrmInstrumentMetadata:
    defaults = {
        "ticker": "SPY",
        "metadata_provider": "FMP",
        "venue": "ARCA",
        "currency": "USD",
        "asset_type": "ETF",
        "company_name": "SPDR S&P 500 ETF Trust",
        "sector": "Financial Services",
        "industry": "Asset Management",
        "country": "US",
        "ipo_date": date(1993, 1, 29),
        "resolution_status": ResolutionStatus.RESOLVED,
    }
    defaults.update(overrides)
    row = MagicMock(spec=OrmInstrumentMetadata)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _make_repo(row):
    repo = AsyncMock()
    repo.get_by_ticker.return_value = row
    return repo


@pytest.mark.unit
class TestBuildMetadataPanelContext:
    """Builder returns a template-ready context for a ticker."""

    async def test_row_present_builds_panel(self):
        ctx = await _build_metadata_panel_context(_make_repo(_make_orm()), "SPY")
        assert ctx["has_metadata"] is True
        assert isinstance(ctx["metadata"], EtfMetadataPanel)
        assert ctx["metadata"].ticker == "SPY"
        assert ctx["na_sentinel"] == NA_SENTINEL

    async def test_row_absent_empty_state(self):
        repo = _make_repo(None)
        ctx = await _build_metadata_panel_context(repo, "ZZZ")
        assert ctx["has_metadata"] is False
        assert ctx["metadata"] is None
        assert ctx["na_sentinel"] == NA_SENTINEL

    async def test_lookup_is_ticker_only(self):
        repo = _make_repo(_make_orm())
        await _build_metadata_panel_context(repo, "SPY")
        repo.get_by_ticker.assert_awaited_once_with("SPY")

    async def test_db_error_degrades_to_empty_state(self):
        """A DB fault (e.g. table not migrated) degrades to empty-state, no raise."""
        repo = AsyncMock()
        repo.get_by_ticker.side_effect = OperationalError("stmt", {}, Exception("no table"))
        ctx = await _build_metadata_panel_context(repo, "SPY")
        assert ctx["has_metadata"] is False
        assert ctx["metadata"] is None
        assert ctx["na_sentinel"] == NA_SENTINEL
