"""Unit tests for ``FMPMetadataProvider`` (Story 1.4, Task 2/3).

Mocked input only — no network, no DB. The injected client is the test seam:
a tiny stub returns a profile dict (or ``None``) so ``resolve`` is fully
deterministic. Mirrors the AAA + ``unittest.mock`` idioms in
``test_fmp_client.py``.
"""

from datetime import date

import pytest

from src.models.instrument_metadata import (
    NA_SENTINEL,
    AssetType,
    InstrumentMetadata,
    ResolutionStatus,
)
from src.services.metadata.providers.fmp_provider import FMPMetadataProvider

pytestmark = pytest.mark.unit


class _StubClient:
    """Minimal stand-in for ``FMPClient`` — returns a canned ``fetch_profile``."""

    def __init__(self, result):
        self._result = result

    def fetch_profile(self, ticker):  # noqa: ARG002 — ticker irrelevant to stub
        return self._result


def _provider(result):
    return FMPMetadataProvider(client=_StubClient(result))


# A fully-populated, confidently-venued profile (AC1/AC2/AC3 happy path).
_FULL_PROFILE = {
    "companyName": "Apple Inc.",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "country": "US",
    "currency": "USD",
    "ipoDate": "1980-12-12",
    "isEtf": False,
    "isFund": False,
    "exchange": "NASDAQ",
}

# The canonical SPY shape from the 1.3 live smoke: descriptive-resolved but
# venue-unresolved (FMP labels SPY's exchange AMEX → real venue is ARCA).
_SPY_PROFILE = {
    "companyName": "SPDR S&P 500 ETF Trust",
    "sector": "",
    "industry": "",
    "country": "US",
    "currency": "USD",
    "ipoDate": "1993-01-22",
    "isEtf": True,
    "isFund": False,
    "exchange": "AMEX",
}


class TestPopulatedProfile:
    """AC1 — descriptive fields populate from the JSON."""

    def test_descriptive_fields_populated(self):
        md = _provider(_FULL_PROFILE).resolve("AAPL")
        assert md.company_name == "Apple Inc."
        assert md.sector == "Technology"
        assert md.industry == "Consumer Electronics"
        assert md.country == "US"
        assert md.currency == "USD"

    def test_ipo_date_parsed_to_date(self):
        md = _provider(_FULL_PROFILE).resolve("AAPL")
        assert md.ipo_date == date(1980, 12, 12)


class TestDescriptiveGaps:
    """AC1 — absent/empty descriptive fields become the sentinel."""

    def test_empty_and_missing_fields_become_na_sentinel(self):
        profile = {"companyName": "", "country": "  ", "exchange": "NYSE"}
        md = _provider(profile).resolve("T")
        assert md.company_name == NA_SENTINEL
        assert md.sector == NA_SENTINEL  # missing key
        assert md.industry == NA_SENTINEL
        assert md.country == NA_SENTINEL  # whitespace-only
        assert md.currency == NA_SENTINEL

    def test_blank_or_missing_ipo_date_is_none_not_sentinel(self):
        for profile in [
            {"ipoDate": "", "exchange": "NYSE"},
            {"exchange": "NYSE"},  # missing key
        ]:
            md = _provider(profile).resolve("T")
            assert md.ipo_date is None

    def test_invalid_ipo_date_is_none(self):
        md = _provider({"ipoDate": "not-a-date", "exchange": "NYSE"}).resolve("T")
        assert md.ipo_date is None


class TestAssetType:
    """AC2 — asset type derived from isEtf / isFund."""

    def test_is_etf(self):
        md = _provider({"isEtf": True, "exchange": "NYSE"}).resolve("SPY")
        assert md.asset_type == AssetType.ETF

    def test_is_fund(self):
        md = _provider({"isEtf": False, "isFund": True, "exchange": "NYSE"}).resolve("X")
        assert md.asset_type == AssetType.FUND

    def test_equity_when_neither(self):
        md = _provider({"exchange": "NYSE"}).resolve("AAPL")
        assert md.asset_type == AssetType.EQUITY


class TestVenueResolution:
    """AC3/AC4 — status is venue-driven; no venue is ever fabricated."""

    def test_confident_venue_is_resolved(self):
        md = _provider({"exchange": "NASDAQ"}).resolve("AAPL")
        assert md.venue == "NASDAQ"
        assert md.resolution_status == ResolutionStatus.RESOLVED

    @pytest.mark.parametrize("exchange", ["AMEX", "", "XETRA"])
    def test_ambiguous_blank_or_unmapped_is_venue_unresolved(self, exchange):
        md = _provider({"exchange": exchange}).resolve("T")
        assert md.venue is None
        assert md.resolution_status == ResolutionStatus.VENUE_UNRESOLVED

    def test_missing_exchange_is_venue_unresolved(self):
        md = _provider({"companyName": "X"}).resolve("T")
        assert md.venue is None
        assert md.resolution_status == ResolutionStatus.VENUE_UNRESOLVED


class TestSpyRegressionGuard:
    """The canonical descriptive-resolved / venue-unresolved split."""

    def test_spy_descriptive_resolved_but_venue_unresolved(self):
        md = _provider(_SPY_PROFILE).resolve("SPY")
        assert md.company_name == "SPDR S&P 500 ETF Trust"
        assert md.asset_type == AssetType.ETF
        assert md.ipo_date == date(1993, 1, 22)
        assert md.sector == NA_SENTINEL  # empty in the profile
        assert md.venue is None
        assert md.resolution_status == ResolutionStatus.VENUE_UNRESOLVED


class TestDegraded:
    """Client ``None`` → the unknown-ticker / no-data degraded record."""

    def test_degraded_record(self):
        md = _provider(None).resolve("NOPE")
        assert md.ticker == "NOPE"
        assert md.metadata_provider == "FMP"
        assert md.company_name == NA_SENTINEL
        assert md.sector == NA_SENTINEL
        assert md.industry == NA_SENTINEL
        assert md.country == NA_SENTINEL
        assert md.currency == NA_SENTINEL
        assert md.venue is None
        assert md.asset_type is None
        assert md.ipo_date is None
        assert md.resolution_status == ResolutionStatus.VENUE_UNRESOLVED


class TestTypeContract:
    """No-leak: resolve returns the domain model; provider name is the constant."""

    def test_returns_instrument_metadata(self):
        md = _provider(_FULL_PROFILE).resolve("AAPL")
        assert isinstance(md, InstrumentMetadata)

    def test_provider_name_constant(self):
        assert FMPMetadataProvider.PROVIDER_NAME == "FMP"
        md = _provider(_FULL_PROFILE).resolve("AAPL")
        assert md.metadata_provider == FMPMetadataProvider.PROVIDER_NAME

    def test_default_client_constructed_when_none(self):
        # CI-safe: fmp_api_key defaults to "" so the real client builds fine.
        provider = FMPMetadataProvider()
        assert provider._client is not None
