"""FMP adapter: FMP profile JSON → ``InstrumentMetadata`` domain model (ADR-5).

Contains ALL FMP-specific mapping so no FMP types or raw JSON cross out of this
boundary. ``resolve(ticker) -> InstrumentMetadata`` is the stable seam consumed
by Story 1.5's provider-agnostic service; the mapper is pure (no I/O, no clock),
delegating the one network call to the injected ``FMPClient``.
"""

from datetime import date
from typing import Any, Optional

import structlog

from src.models.instrument_metadata import (
    NA_SENTINEL,
    AssetType,
    InstrumentMetadata,
    ResolutionStatus,
)
from src.services.metadata.fmp_client import FMPClient
from src.services.metadata.venue_map import normalize_venue

logger = structlog.get_logger(__name__)


def _descriptive(value: Any) -> str:
    """Map a descriptive FMP value to itself (stripped) or ``NA_SENTINEL``."""
    if value is None:
        return NA_SENTINEL
    text = str(value).strip()
    return text or NA_SENTINEL


def _parse_ipo_date(value: Any) -> Optional[date]:
    """Parse an ISO ``ipoDate`` to a ``date``; blank/invalid → ``None``.

    ``ipo_date`` is typed and can NEVER hold ``NA_SENTINEL`` — an absent or
    unparseable value becomes ``None``. Mirrors the firstrate ``_parse_ipo_date``
    shape (kept local to avoid coupling ``metadata`` → ``firstrate``).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        logger.warning("invalid_ipo_date", value=text, provider=FMPMetadataProvider.PROVIDER_NAME)
        return None


def _asset_type(profile: dict[str, Any]) -> AssetType:
    """Derive the asset classification from FMP ``isEtf`` / ``isFund`` flags."""
    if profile.get("isEtf"):
        return AssetType.ETF
    if profile.get("isFund"):
        return AssetType.FUND
    return AssetType.EQUITY


class FMPMetadataProvider:
    """Maps FMP ticker profiles to the ``InstrumentMetadata`` domain model."""

    PROVIDER_NAME = "FMP"

    def __init__(self, client: FMPClient | None = None) -> None:
        self._client = client or FMPClient()

    def resolve(self, ticker: str) -> InstrumentMetadata:
        """Resolve a ticker to domain metadata; unknown/degraded → degraded record."""
        profile = self._client.fetch_profile(ticker)
        if profile is None:
            return self._degraded(ticker)
        return self._to_domain(ticker, profile)

    def _to_domain(self, ticker: str, profile: dict[str, Any]) -> InstrumentMetadata:
        """Pure FMP-dict → domain mapping (no I/O, no clock)."""
        venue = normalize_venue(profile.get("exchange"))
        status = (
            ResolutionStatus.RESOLVED if venue is not None else ResolutionStatus.VENUE_UNRESOLVED
        )
        return InstrumentMetadata(
            ticker=ticker,
            metadata_provider=self.PROVIDER_NAME,
            venue=venue,
            currency=_descriptive(profile.get("currency")),
            asset_type=_asset_type(profile),
            company_name=_descriptive(profile.get("companyName")),
            sector=_descriptive(profile.get("sector")),
            industry=_descriptive(profile.get("industry")),
            country=_descriptive(profile.get("country")),
            ipo_date=_parse_ipo_date(profile.get("ipoDate")),
            resolution_status=status,
        )

    def _degraded(self, ticker: str) -> InstrumentMetadata:
        """Build the unknown-ticker / no-data record (all descriptive → sentinel)."""
        status = ResolutionStatus.VENUE_UNRESOLVED
        logger.debug(
            "fmp_metadata_degraded",
            ticker=ticker,
            provider=self.PROVIDER_NAME,
            resolution_status=status.value,
        )
        return InstrumentMetadata(
            ticker=ticker,
            metadata_provider=self.PROVIDER_NAME,
            venue=None,
            currency=NA_SENTINEL,
            asset_type=None,
            company_name=NA_SENTINEL,
            sector=NA_SENTINEL,
            industry=NA_SENTINEL,
            country=NA_SENTINEL,
            ipo_date=None,
            resolution_status=status,
        )
