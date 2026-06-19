"""Unit tests for ``InstrumentMetadataService`` (Story 1.5).

Pure orchestration tests — no network, no DB, no Nautilus. Both dependencies
are mocked: a stub/``MagicMock`` provider returns canned domain metadata (or
raises), and a ``MagicMock(spec=SyncInstrumentMetadataRepository)`` scripts
``get_by_ticker`` / ``upsert``. Mirrors the AAA + ``unittest.mock`` idioms in
``test_fmp_provider.py`` / ``test_fmp_client.py``.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

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
from src.services.metadata import instrument_metadata_service as service_module
from src.services.metadata.instrument_metadata_service import InstrumentMetadataService

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _domain(
    ticker: str = "SPY",
    *,
    provider: str = "FMP",
    venue: str | None = "ARCA",
    asset_type: AssetType | None = AssetType.ETF,
    status: ResolutionStatus = ResolutionStatus.RESOLVED,
) -> InstrumentMetadata:
    """A populated domain ``InstrumentMetadata`` (what a provider returns)."""
    return InstrumentMetadata(
        ticker=ticker,
        metadata_provider=provider,
        venue=venue,
        currency="USD",
        asset_type=asset_type,
        company_name="SPDR S&P 500 ETF Trust",
        sector="N/A",
        industry="N/A",
        country="US",
        ipo_date=date(1993, 1, 29),
        resolution_status=status,
    )


def _orm(
    ticker: str = "SPY",
    *,
    provider: str = "FMP",
    venue: str | None = "ARCA",
    asset_type: str | None = "ETF",
    status: ResolutionStatus = ResolutionStatus.RESOLVED,
) -> OrmInstrumentMetadata:
    """A populated ORM row (what the repository returns). ``asset_type`` is str."""
    return OrmInstrumentMetadata(
        ticker=ticker,
        metadata_provider=provider,
        venue=venue,
        currency="USD",
        asset_type=asset_type,
        company_name="SPDR S&P 500 ETF Trust",
        sector="N/A",
        industry="N/A",
        country="US",
        ipo_date=date(1993, 1, 29),
        resolution_status=status,
    )


def _service(provider=None, repository=None):
    """Build a service with ``MagicMock`` seams (overridable)."""
    provider = provider or MagicMock()
    repository = repository or MagicMock(spec=SyncInstrumentMetadataRepository)
    return InstrumentMetadataService(provider=provider, repository=repository), provider, repository


# --------------------------------------------------------------------------- #
# AC #2 — cache hit (RESOLVED short-circuits the provider)
# --------------------------------------------------------------------------- #
def test_cache_hit_skips_provider_and_upsert() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = _orm(status=ResolutionStatus.RESOLVED)
    service, provider, _ = _service(repository=repo)

    result = service.resolve("SPY")

    provider.resolve.assert_not_called()
    repo.upsert.assert_not_called()
    assert isinstance(result, InstrumentMetadata)
    assert result.venue == "ARCA"
    assert result.asset_type == AssetType.ETF  # str → enum round-trip


# --------------------------------------------------------------------------- #
# AC #3 — cache miss (None row → provider invoked + upserted)
# --------------------------------------------------------------------------- #
def test_cache_miss_invokes_provider_and_upserts() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = None
    repo.upsert.side_effect = lambda orm: orm  # echo persisted row back
    provider = MagicMock()
    provider.resolve.return_value = _domain()
    service, _, _ = _service(provider=provider, repository=repo)

    result = service.resolve("SPY")

    provider.resolve.assert_called_once_with("SPY")
    repo.upsert.assert_called_once()
    assert result.resolved_at is not None
    assert isinstance(result, InstrumentMetadata)


# --------------------------------------------------------------------------- #
# Re-attempt: non-RESOLVED cached row is a MISS
# --------------------------------------------------------------------------- #
def test_venue_unresolved_row_is_not_a_cache_hit() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = _orm(status=ResolutionStatus.VENUE_UNRESOLVED)
    repo.upsert.side_effect = lambda orm: orm
    provider = MagicMock()
    provider.resolve.return_value = _domain()
    service, _, _ = _service(provider=provider, repository=repo)

    service.resolve("SPY")

    provider.resolve.assert_called_once_with("SPY")
    repo.upsert.assert_called_once()


def test_unresolved_row_is_not_a_cache_hit() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = _orm(status=ResolutionStatus.UNRESOLVED)
    repo.upsert.side_effect = lambda orm: orm
    provider = MagicMock()
    provider.resolve.return_value = _domain()
    service, _, _ = _service(provider=provider, repository=repo)

    service.resolve("SPY")

    provider.resolve.assert_called_once_with("SPY")


# --------------------------------------------------------------------------- #
# AC #1 — no leak: domain type out, never the ORM type
# --------------------------------------------------------------------------- #
def test_resolve_returns_domain_model_not_orm() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = _orm()
    service, _, _ = _service(repository=repo)

    result = service.resolve("SPY")

    assert isinstance(result, InstrumentMetadata)
    assert not isinstance(result, OrmInstrumentMetadata)


# --------------------------------------------------------------------------- #
# ORM↔domain asset_type enum bridge (both directions)
# --------------------------------------------------------------------------- #
def test_to_domain_maps_asset_type_str_to_enum() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = _orm(asset_type="ETF")
    service, _, _ = _service(repository=repo)

    result = service.resolve("SPY")

    assert result.asset_type == AssetType.ETF


def test_to_orm_maps_asset_type_enum_to_str() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = None
    repo.upsert.side_effect = lambda orm: orm
    provider = MagicMock()
    provider.resolve.return_value = _domain(asset_type=AssetType.ETF)
    service, _, _ = _service(provider=provider, repository=repo)

    service.resolve("SPY")

    persisted = repo.upsert.call_args.args[0]
    assert isinstance(persisted, OrmInstrumentMetadata)
    assert persisted.asset_type == "ETF"  # enum → str
    assert persisted.resolved_at is not None  # service stamps the clock


def test_to_orm_maps_none_asset_type_to_none() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = None
    repo.upsert.side_effect = lambda orm: orm
    provider = MagicMock()
    provider.resolve.return_value = _domain(asset_type=None, venue=None)
    service, _, _ = _service(provider=provider, repository=repo)

    service.resolve("SPY")

    persisted = repo.upsert.call_args.args[0]
    assert persisted.asset_type is None


# --------------------------------------------------------------------------- #
# AC #4 — fault isolation: provider raises mid-batch
# --------------------------------------------------------------------------- #
def test_resolve_batch_isolates_provider_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = None
    repo.upsert.side_effect = lambda orm: orm
    provider = MagicMock()
    provider.PROVIDER_NAME = "FMP"
    provider.resolve.side_effect = [
        _domain("A"),
        RuntimeError("boom"),
        _domain("C"),
    ]
    fake_logger = MagicMock()
    monkeypatch.setattr(service_module, "logger", fake_logger)
    service, _, _ = _service(provider=provider, repository=repo)

    results = service.resolve_batch(["A", "B", "C"])

    assert len(results) == 3
    assert results[0].ticker == "A"
    # middle ticker degraded, not aborted
    assert results[1].ticker == "B"
    assert results[1].resolution_status == ResolutionStatus.VENUE_UNRESOLVED
    assert results[1].metadata_provider == "FMP"
    assert results[1].venue is None
    assert results[1].company_name == NA_SENTINEL
    # loop continued to "C"
    assert results[2].ticker == "C"
    assert provider.resolve.call_count == 3
    fake_logger.error.assert_called_once_with(
        "metadata_resolution_failed",
        ticker="B",
        provider="FMP",
        error="boom",
    )


def test_resolve_batch_degraded_record_uses_na_sentinel_fields() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = None
    provider = MagicMock()
    provider.resolve.side_effect = RuntimeError("db exploded")
    service, _, _ = _service(provider=provider, repository=repo)

    results = service.resolve_batch(["X"])

    degraded = results[0]
    assert degraded.ticker == "X"
    assert degraded.venue is None
    assert degraded.asset_type is None
    assert degraded.ipo_date is None
    assert degraded.currency == NA_SENTINEL
    assert degraded.sector == NA_SENTINEL
    assert degraded.industry == NA_SENTINEL
    assert degraded.country == NA_SENTINEL
    assert degraded.resolution_status == ResolutionStatus.VENUE_UNRESOLVED


# --------------------------------------------------------------------------- #
# AC #4 — fault isolation: provider degrades cleanly (no raise)
# --------------------------------------------------------------------------- #
def test_resolve_batch_records_clean_degraded_without_exception() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = None
    repo.upsert.side_effect = lambda orm: orm
    provider = MagicMock()
    provider.resolve.side_effect = [
        _domain("A"),
        _domain("B", venue=None, asset_type=None, status=ResolutionStatus.VENUE_UNRESOLVED),
    ]
    service, _, _ = _service(provider=provider, repository=repo)

    results = service.resolve_batch(["A", "B"])

    assert len(results) == 2
    assert results[1].resolution_status == ResolutionStatus.VENUE_UNRESOLVED


# --------------------------------------------------------------------------- #
# AC #5 — provider swap: a second provider works with zero service change
# --------------------------------------------------------------------------- #
def test_alternate_provider_swaps_in_without_service_change() -> None:
    repo = MagicMock(spec=SyncInstrumentMetadataRepository)
    repo.get_by_ticker.return_value = None
    repo.upsert.side_effect = lambda orm: orm

    class _OtherProvider:
        """A structurally-compatible provider with a different name."""

        def resolve(self, ticker: str) -> InstrumentMetadata:
            return _domain(ticker, provider="OTHER")

    service = InstrumentMetadataService(provider=_OtherProvider(), repository=repo)

    result = service.resolve("SPY")

    assert result.metadata_provider == "OTHER"
    assert isinstance(result, InstrumentMetadata)
