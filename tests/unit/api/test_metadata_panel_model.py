"""Unit tests for the ETF metadata presentation model (Story 4.4).

Covers the shared ``display_*`` ``@computed_field`` props (N/A-aware rendering),
the distinct venue state (never the ``N/A`` sentinel), and ``from_orm_row``.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from src.api.models.metadata_panel import VENUE_UNRESOLVED_LABEL, EtfMetadataPanel
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


@pytest.mark.unit
class TestEtfMetadataPanelDisplay:
    """The shared ``display_*`` props render values or a clean ``N/A``."""

    def test_full_metadata_shows_resolved_values(self):
        panel = EtfMetadataPanel.from_orm_row(_make_orm())
        assert panel.display_company_name == "SPDR S&P 500 ETF Trust"
        assert panel.display_sector == "Financial Services"
        assert panel.display_industry == "Asset Management"
        assert panel.display_country == "US"
        assert panel.display_currency == "USD"
        assert panel.display_asset_type == "ETF"
        assert panel.display_ipo_date == "1993-01-29"

    def test_none_descriptive_field_renders_na(self):
        panel = EtfMetadataPanel.from_orm_row(_make_orm(sector=None))
        assert panel.display_sector == NA_SENTINEL

    def test_na_sentinel_descriptive_field_stays_na(self):
        panel = EtfMetadataPanel.from_orm_row(_make_orm(country=NA_SENTINEL))
        assert panel.display_country == NA_SENTINEL

    def test_empty_string_descriptive_field_renders_na(self):
        panel = EtfMetadataPanel.from_orm_row(_make_orm(company_name=""))
        assert panel.display_company_name == NA_SENTINEL

    def test_missing_ipo_date_renders_na(self):
        panel = EtfMetadataPanel.from_orm_row(_make_orm(ipo_date=None))
        assert panel.display_ipo_date == NA_SENTINEL


@pytest.mark.unit
class TestEtfMetadataPanelVenue:
    """Venue is rendered distinctly — never the descriptive N/A sentinel (AC3)."""

    def test_resolved_venue_shows_code(self):
        panel = EtfMetadataPanel.from_orm_row(_make_orm(venue="ARCA"))
        assert panel.display_venue == "ARCA"
        assert panel.venue_resolved is True

    def test_unresolved_venue_shows_distinct_label(self):
        panel = EtfMetadataPanel.from_orm_row(
            _make_orm(venue=None, resolution_status=ResolutionStatus.VENUE_UNRESOLVED)
        )
        assert panel.display_venue == VENUE_UNRESOLVED_LABEL
        assert panel.venue_resolved is False

    def test_unresolved_venue_never_na_sentinel(self):
        panel = EtfMetadataPanel.from_orm_row(_make_orm(venue=None))
        assert panel.display_venue != NA_SENTINEL

    def test_empty_string_venue_is_unresolved(self):
        """A stored "" venue is normalized to the unresolved state (not a code)."""
        panel = EtfMetadataPanel.from_orm_row(_make_orm(venue=""))
        assert panel.venue is None
        assert panel.venue_resolved is False
        assert panel.display_venue == VENUE_UNRESOLVED_LABEL

    def test_whitespace_venue_is_unresolved(self):
        """A whitespace-only venue is normalized to the unresolved state."""
        panel = EtfMetadataPanel.from_orm_row(_make_orm(venue="   "))
        assert panel.venue is None
        assert panel.venue_resolved is False
        assert panel.display_venue == VENUE_UNRESOLVED_LABEL

    def test_venue_resolved_agrees_with_display_venue(self):
        """venue_resolved and display_venue must never disagree on the boundary."""
        for venue in ("ARCA", "", "   ", None):
            panel = EtfMetadataPanel.from_orm_row(_make_orm(venue=venue))
            is_code = panel.display_venue not in (VENUE_UNRESOLVED_LABEL,)
            assert panel.venue_resolved is is_code


@pytest.mark.unit
class TestFromOrmRow:
    """``from_orm_row`` maps the ORM row field-for-field."""

    def test_maps_all_fields(self):
        panel = EtfMetadataPanel.from_orm_row(_make_orm())
        assert panel.ticker == "SPY"
        assert panel.metadata_provider == "FMP"
        assert panel.venue == "ARCA"
        assert panel.currency == "USD"
        assert panel.asset_type == "ETF"
        assert panel.company_name == "SPDR S&P 500 ETF Trust"
        assert panel.sector == "Financial Services"
        assert panel.industry == "Asset Management"
        assert panel.country == "US"
        assert panel.ipo_date == date(1993, 1, 29)
        assert panel.resolution_status == ResolutionStatus.RESOLVED
