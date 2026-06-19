"""Provider-agnostic instrument metadata resolution service (Story 1.5, ADR-5).

Resolves a ticker cache-first through the ``MetadataProvider`` Protocol and owns
the ORM↔domain bridge (the repositories defer it here by design). No FMP types,
raw JSON, or SQLAlchemy ORM instances cross out of this boundary — consumers
receive only the Pydantic domain ``InstrumentMetadata``. Per-ticker failures are
isolated so a single bad ticker never aborts the batch.

The ORM and domain classes share the name ``InstrumentMetadata``; the ORM is
imported aliased as ``OrmInstrumentMetadata`` and ``InstrumentMetadata`` stays
bound to the Pydantic domain model.
"""

from datetime import datetime, timezone
from typing import Iterable

import structlog

from src.db.models.instrument_metadata import InstrumentMetadata as OrmInstrumentMetadata
from src.db.repositories.instrument_metadata_repository_sync import (
    SyncInstrumentMetadataRepository,
)
from src.models.instrument_metadata import (
    NA_SENTINEL,
    AssetType,
    InstrumentMetadata,
    ResolutionStatus,
)
from src.services.metadata.providers.base import MetadataProvider

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    """Single deterministic clock site for ``resolved_at`` (mirrors the ORM onupdate)."""
    return datetime.now(timezone.utc)


class InstrumentMetadataService:
    """Cache-first ticker resolution over a provider-agnostic seam."""

    def __init__(
        self, provider: MetadataProvider, repository: SyncInstrumentMetadataRepository
    ) -> None:
        self._provider = provider
        self._repository = repository

    def resolve(self, ticker: str) -> InstrumentMetadata:
        """Resolve one ticker cache-first; only a RESOLVED row short-circuits the provider."""
        existing = self._repository.get_by_ticker(ticker)
        if existing is not None and existing.resolution_status == ResolutionStatus.RESOLVED:
            # Cache hit (AC #2): return the persisted metadata, skipping the API call.
            # Only RESOLVED hits — UNRESOLVED / VENUE_UNRESOLVED rows are the venue
            # worklist and are intentionally re-attempted each run (do not "optimize").
            logger.debug("metadata_cache_hit", ticker=ticker, provider="store", cache_hit=True)
            return self._to_domain(existing)

        # Cache miss / re-attempt (AC #3): fetch via the provider and persist.
        domain = self._provider.resolve(ticker)
        domain.resolved_at = _utcnow()
        persisted = self._repository.upsert(self._to_orm(domain))
        return self._to_domain(persisted)

    def resolve_batch(self, tickers: Iterable[str]) -> list[InstrumentMetadata]:
        """Resolve many tickers, isolating per-ticker faults (AC #4) — never abort the batch."""
        results: list[InstrumentMetadata] = []
        for ticker in tickers:
            try:
                results.append(self.resolve(ticker))
            except Exception as exc:  # noqa: BLE001 — last-resort per-ticker fault isolation
                provider_name = self._provider_name()
                logger.error(
                    "metadata_resolution_failed",
                    ticker=ticker,
                    provider=provider_name,
                    error=str(exc),
                )
                results.append(self._error_record(ticker, provider_name))
        return results

    def _provider_name(self) -> str:
        """Return a discoverable provider identifier, or ``UNKNOWN`` for opaque providers."""
        provider_name = getattr(self._provider, "PROVIDER_NAME", None)
        if isinstance(provider_name, str) and provider_name:
            return provider_name
        return "UNKNOWN"

    def _error_record(self, ticker: str, provider_name: str) -> InstrumentMetadata:
        """Last-resort degraded record when the provider itself raised (not a clean degrade)."""
        return InstrumentMetadata(
            ticker=ticker,
            metadata_provider=provider_name,
            venue=None,
            currency=NA_SENTINEL,
            asset_type=None,
            company_name=NA_SENTINEL,
            sector=NA_SENTINEL,
            industry=NA_SENTINEL,
            country=NA_SENTINEL,
            ipo_date=None,
            resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
        )

    def _to_orm(self, domain: InstrumentMetadata) -> OrmInstrumentMetadata:
        """Domain → ORM row. ``asset_type`` enum→str; ``created_at``/``updated_at`` DB-managed."""
        return OrmInstrumentMetadata(
            ticker=domain.ticker,
            metadata_provider=domain.metadata_provider,
            venue=domain.venue,
            currency=domain.currency,
            asset_type=domain.asset_type.value if domain.asset_type is not None else None,
            company_name=domain.company_name,
            sector=domain.sector,
            industry=domain.industry,
            country=domain.country,
            ipo_date=domain.ipo_date,
            resolution_status=domain.resolution_status,
            resolved_at=domain.resolved_at,
        )

    def _to_domain(self, orm: OrmInstrumentMetadata) -> InstrumentMetadata:
        """ORM row → domain model. ``asset_type`` str→enum; no ORM type leaks out (AC #1)."""
        return InstrumentMetadata(
            ticker=orm.ticker,
            metadata_provider=orm.metadata_provider,
            venue=orm.venue,
            currency=orm.currency,
            asset_type=AssetType(orm.asset_type) if orm.asset_type is not None else None,
            company_name=orm.company_name,
            sector=orm.sector,
            industry=orm.industry,
            country=orm.country,
            ipo_date=orm.ipo_date,
            resolution_status=orm.resolution_status,
            resolved_at=orm.resolved_at,
        )
