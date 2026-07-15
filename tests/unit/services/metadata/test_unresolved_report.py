"""Unit tests for the Story 3.2 unresolved-venue reason derivation."""

import pytest

from src.db.models.instrument_metadata import InstrumentMetadata
from src.models.instrument_metadata import NA_SENTINEL, AssetType, ResolutionStatus
from src.services.metadata.unresolved_report import unresolved_reason


def _degraded_row(ticker: str = "ZZZ", provider: str = "FMP") -> InstrumentMetadata:
    """An ORM row shaped like the FMP provider's `_degraded` output (unknown ticker)."""
    return InstrumentMetadata(
        ticker=ticker,
        metadata_provider=provider,
        venue=None,
        currency=NA_SENTINEL,
        asset_type=None,
        company_name=NA_SENTINEL,
        sector=NA_SENTINEL,
        industry=NA_SENTINEL,
        country=NA_SENTINEL,
        resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
    )


def _found_unmapped_row(ticker: str = "SPY", provider: str = "FMP") -> InstrumentMetadata:
    """An ORM row shaped like `_to_domain` with a blank/ambiguous exchange label."""
    return InstrumentMetadata(
        ticker=ticker,
        metadata_provider=provider,
        venue=None,
        currency="USD",
        asset_type=AssetType.ETF.value,
        company_name="SPDR S&P 500 ETF Trust",
        sector=NA_SENTINEL,
        industry=NA_SENTINEL,
        country="US",
        resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
    )


@pytest.mark.unit
class TestUnresolvedReason:
    """Derive a human-readable reason for a VENUE_UNRESOLVED row."""

    def test_degraded_row_is_unknown_to_provider(self):
        reason = unresolved_reason(_degraded_row())
        assert "unknown to FMP" in reason
        assert "no profile" in reason.lower()

    def test_found_but_unmapped_row_is_label_unusable(self):
        reason = unresolved_reason(_found_unmapped_row())
        assert "unknown to" not in reason.lower()
        assert "ambiguous" in reason.lower()
        assert "unmapped" in reason.lower()

    def test_reason_is_provider_agnostic_degraded_branch(self):
        reason = unresolved_reason(_degraded_row(provider="OTHER"))
        assert "OTHER" in reason
        assert "FMP" not in reason

    def test_reason_is_provider_agnostic_found_branch(self):
        reason = unresolved_reason(_found_unmapped_row(provider="OTHER"))
        assert "OTHER" in reason
        assert "FMP" not in reason
