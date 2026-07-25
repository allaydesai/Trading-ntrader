"""Converge ``catalog_instruments`` identity onto the metadata venue verdict.

``instrument_metadata`` is the source of truth for whether a ticker has a venue;
``catalog_instruments.nautilus_id`` is the derived identity every consumer actually
loads bars by. Until this module existed, only the import pipeline wrote that
derived value — so ``metadata apply-overrides`` could flip a ticker to RESOLVED,
the coverage gate would report PASS, and ``backtest_loader`` would still refuse it
because ``nautilus_id`` was never repopulated. A green gate over an unloadable
universe is worse than a red one.

The invariant enforced here, in both directions:

    nautilus_id is non-NULL  ⟺  resolution_status is RESOLVED (with a venue)

The reverse direction is not decoration. A ticker that loses RESOLVED — excluded
via the register, or re-resolved to VENUE_UNRESOLVED — must have its stale identity
cleared, or it stays silently loadable under a venue nothing vouches for any more.

Convergent, not forward-only: it reads current state and writes only the difference,
so it heals drift from any cause and a second run is a provable no-op.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Optional, Protocol

import structlog

from src.models.instrument_metadata import ResolutionStatus

logger = structlog.get_logger(__name__)


class _MetaRow(Protocol):
    ticker: str
    venue: Optional[str]
    resolution_status: ResolutionStatus


class _CatalogRow(Protocol):
    ticker: str
    nautilus_id: Optional[str]
    exchange: Optional[str]


class _MetaRepo(Protocol):
    def list_all(self) -> list[_MetaRow]: ...


class _CatalogRepo(Protocol):
    def iter_by_catalog(self, catalog_name: str) -> Iterator[_CatalogRow]: ...


class _Mapper(Protocol):
    def sync_qualification(self, ticker: str, catalog_name: str, venue: Optional[str]): ...


@dataclass(frozen=True)
class QualificationSyncResult:
    """Outcome tally for a ``sync_resolved_qualifications`` run.

    Attributes:
        synced: Rows given a qualified ``nautilus_id`` (new or corrected).
        cleared: Rows whose stale identity was nulled because the ticker is no
            longer RESOLVED — the reverse-direction repair.
        unchanged: Rows already at their target (no write, no ``updated_at`` churn).
        missing_row: Tickers with metadata but no ``catalog_instruments`` row —
            surfaced, never fabricated (usually means profiles were not loaded).
    """

    synced: int = 0
    cleared: int = 0
    unchanged: int = 0
    missing_row: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        """Rows this run actually wrote — 0 means the store was already converged."""
        return self.synced + self.cleared


def _target_venue(meta: Optional[_MetaRow]) -> Optional[str]:
    """The venue a catalog row should carry, or ``None`` when it must be unqualified.

    A row is qualified only when metadata says RESOLVED *and* carries a venue. The
    conjunction is deliberate: RESOLVED-with-no-venue is an incoherent state, and
    the safe reading of incoherent is "unqualified" — never fabricate an identity.
    """
    if meta is None:
        return None
    if meta.resolution_status != ResolutionStatus.RESOLVED:
        return None
    return meta.venue or None


def sync_resolved_qualifications(
    *,
    meta_repo: _MetaRepo,
    catalog_repo: _CatalogRepo,
    mapper: _Mapper,
    catalog_name: str,
    tickers: Optional[Iterable[str]] = None,
) -> QualificationSyncResult:
    """Converge ``catalog_instruments`` identity onto the metadata venue verdict.

    Reads both tables once and writes only rows that differ from their target.
    A catalog row whose ticker has no metadata row is left strictly alone —
    resolution never ran for it, and clearing it would destroy a working identity
    from an older import.

    Args:
        meta_repo: Sync metadata repository (``list_all``).
        catalog_repo: Sync catalog repository (``iter_by_catalog``).
        mapper: ``InstrumentMapper`` — owns the actual write, so the qualified-id
            format lives in exactly one place.
        catalog_name: Catalog whose identity rows are being converged.
        tickers: Optional subset to restrict the sweep to (targeted repair and
            tests); ``None`` sweeps the whole catalog.

    Returns:
        The synced/cleared/unchanged/missing_row tally.
    """
    scope = {t.upper() for t in tickers} if tickers is not None else None
    by_ticker: dict[str, _MetaRow] = {
        row.ticker: row
        for row in meta_repo.list_all()
        if scope is None or row.ticker.upper() in scope
    }

    synced = cleared = unchanged = 0
    seen: set[str] = set()

    for row in catalog_repo.iter_by_catalog(catalog_name):
        if scope is not None and row.ticker.upper() not in scope:
            continue
        meta = by_ticker.get(row.ticker)
        if meta is None:
            continue  # no venue verdict for this ticker — not ours to touch
        seen.add(row.ticker)

        venue = _target_venue(meta)
        target_id = f"{row.ticker}.{venue}" if venue else None
        if row.nautilus_id == target_id and row.exchange == venue:
            unchanged += 1
            continue

        mapper.sync_qualification(row.ticker, catalog_name, venue)
        if venue:
            synced += 1
        else:
            cleared += 1
            logger.info(
                "qualification_identity_cleared",
                ticker=row.ticker,
                previous=row.nautilus_id,
                status=meta.resolution_status.value,
            )

    missing = sorted(
        t for t, meta in by_ticker.items() if t not in seen and _target_venue(meta) is not None
    )
    if missing:
        logger.warning(
            "qualification_sync_missing_catalog_rows",
            catalog=catalog_name,
            count=len(missing),
            sample=missing[:5],
        )

    return QualificationSyncResult(
        synced=synced, cleared=cleared, unchanged=unchanged, missing_row=missing
    )
