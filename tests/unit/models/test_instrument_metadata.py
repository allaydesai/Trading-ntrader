"""Unit tests for instrument metadata domain models."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from src.models.instrument_metadata import (
    NA_SENTINEL,
    AssetType,
    InstrumentMetadata,
    ResolutionStatus,
    ResolutionSummary,
)


@pytest.mark.unit
class TestNASentinel:
    """Tests for the canonical NA_SENTINEL constant."""

    def test_na_sentinel_value(self):
        """NA_SENTINEL is the literal string 'N/A'."""
        assert NA_SENTINEL == "N/A"


@pytest.mark.unit
class TestResolutionStatus:
    """Tests for ResolutionStatus enum."""

    def test_has_exactly_three_members(self):
        """ResolutionStatus has exactly three members."""
        assert len(ResolutionStatus) == 3

    def test_member_values(self):
        """Members have name == value (avoids SQLAlchemy values_callable footgun)."""
        assert ResolutionStatus.UNRESOLVED.value == "UNRESOLVED"
        assert ResolutionStatus.RESOLVED.value == "RESOLVED"
        assert ResolutionStatus.VENUE_UNRESOLVED.value == "VENUE_UNRESOLVED"

    def test_name_equals_value(self):
        """Every member's name equals its value."""
        for member in ResolutionStatus:
            assert member.name == member.value


@pytest.mark.unit
class TestAssetType:
    """Tests for AssetType enum (FMP-derived, distinct from AssetClass)."""

    def test_has_exactly_three_members(self):
        """AssetType has exactly three members."""
        assert len(AssetType) == 3

    def test_member_values(self):
        """AssetType members carry the FMP-derived classification values."""
        assert AssetType.ETF.value == "ETF"
        assert AssetType.EQUITY.value == "EQUITY"
        assert AssetType.FUND.value == "FUND"


@pytest.mark.unit
class TestInstrumentMetadata:
    """Tests for the InstrumentMetadata Pydantic domain model."""

    def test_create_minimal(self):
        """Model can be created with only the required ticker + provider."""
        meta = InstrumentMetadata(ticker="SPY", metadata_provider="FMP")
        assert meta.ticker == "SPY"
        assert meta.metadata_provider == "FMP"

    def test_default_resolution_status_is_unresolved(self):
        """resolution_status defaults to UNRESOLVED."""
        meta = InstrumentMetadata(ticker="SPY", metadata_provider="FMP")
        assert meta.resolution_status == ResolutionStatus.UNRESOLVED

    def test_optional_fields_default_none(self):
        """Unattempted fields default to None (DB NULL)."""
        meta = InstrumentMetadata(ticker="SPY", metadata_provider="FMP")
        assert meta.venue is None
        assert meta.currency is None
        assert meta.asset_type is None
        assert meta.company_name is None
        assert meta.sector is None
        assert meta.industry is None
        assert meta.country is None
        assert meta.ipo_date is None
        assert meta.resolved_at is None

    def test_full_record(self):
        """Model accepts a fully resolved record."""
        meta = InstrumentMetadata(
            ticker="SPY",
            metadata_provider="FMP",
            venue="ARCA",
            currency="USD",
            asset_type=AssetType.ETF,
            company_name="SPDR S&P 500 ETF Trust",
            sector="Financial Services",
            industry="Asset Management",
            country="US",
            ipo_date=date(1993, 1, 22),
            resolution_status=ResolutionStatus.RESOLVED,
            resolved_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        )
        assert meta.venue == "ARCA"
        assert meta.asset_type == AssetType.ETF
        assert meta.resolution_status == ResolutionStatus.RESOLVED

    def test_descriptive_field_accepts_na_sentinel(self):
        """Descriptive fields may hold the NA_SENTINEL string."""
        meta = InstrumentMetadata(
            ticker="SPY",
            metadata_provider="FMP",
            sector=NA_SENTINEL,
            industry=NA_SENTINEL,
        )
        assert meta.sector == NA_SENTINEL
        assert meta.industry == NA_SENTINEL

    def test_venue_accepts_none(self):
        """venue may be None (paired with VENUE_UNRESOLVED)."""
        meta = InstrumentMetadata(
            ticker="SPY",
            metadata_provider="FMP",
            venue=None,
            resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
        )
        assert meta.venue is None

    def test_venue_accepts_real_code(self):
        """venue may be a real Nautilus venue code."""
        meta = InstrumentMetadata(ticker="SPY", metadata_provider="FMP", venue="XNAS")
        assert meta.venue == "XNAS"

    def test_venue_rejects_na_sentinel(self):
        """venue must never hold NA_SENTINEL — it is a real code or None."""
        with pytest.raises(ValidationError):
            InstrumentMetadata(
                ticker="SPY",
                metadata_provider="FMP",
                venue=NA_SENTINEL,
            )


@pytest.mark.unit
class TestResolutionSummary:
    """Tests for the ResolutionSummary aggregate object."""

    def test_default_zeros(self):
        """ResolutionSummary defaults all counters to zero."""
        summary = ResolutionSummary()
        assert summary.resolved == 0
        assert summary.descriptive_gaps == 0
        assert summary.venue_unresolved == 0

    def test_accepts_explicit_counts(self):
        """ResolutionSummary holds explicit counts."""
        summary = ResolutionSummary(resolved=5, descriptive_gaps=2, venue_unresolved=1)
        assert summary.resolved == 5
        assert summary.descriptive_gaps == 2
        assert summary.venue_unresolved == 1
