"""Venue exclusion register: load ``venue_exclusions.csv`` and merge into the store.

The PRD's venue gate is "0 tickers with an unresolved venue" and forbids guessed
venues (ADR-6). That leaves a real gap: an ETF that is delisted, untradeable, or
that no authoritative source — FMP, IBKR's contract database, the issuer — can
qualify. Such a ticker can never resolve, and inventing a venue for it to make the
gate green would be exactly the false confidence the PRD prohibits.

The register is the honest exit. A ticker may leave ``VENUE_UNRESOLVED`` for
``EXCLUDED`` only via a git-tracked row carrying **both** a written reason and a
piece of evidence. That requirement is the whole point: it is what distinguishes an
adjudicated exclusion from a guess, and it is enforced here — a row missing either
field is refused, never defaulted.

An excluded ticker keeps its bars on disk and is never backtestable. Coverage
reporting counts exclusions as their own line rather than folding them into the
denominator, so the size of the register stays visible.

Mirrors ``venue_overrides.py`` deliberately (same parse contract, same degradation
behaviour, same merge shape) so the two registers behave identically for operators.
"""

import csv
from datetime import datetime
from pathlib import Path

import structlog
from pydantic import BaseModel

from src.db.repositories.instrument_metadata_repository_sync import (
    SyncInstrumentMetadataRepository,
)

logger = structlog.get_logger(__name__)

#: The exact header the exclusion file must carry (cells compared case-insensitively).
_EXPECTED_HEADER = ["ticker", "reason", "evidence"]


class VenueExclusionError(Exception):
    """Raised on a malformed exclusion file (header not exactly the expected three)."""


class ExclusionRecord(BaseModel):
    """One adjudicated exclusion.

    Attributes:
        ticker: Normalized (upper-cased) trading symbol.
        reason: Human-written justification, e.g. "delisted 2019-04, no successor".
        evidence: Where the operator verified it — a URL, filing reference, or
            issuer-page citation. Free text, but never empty.
    """

    ticker: str
    reason: str
    evidence: str


class VenueExclusionMergeResult(BaseModel):
    """Outcome tally for a ``merge_venue_exclusions`` run.

    Attributes:
        applied: Rows flipped to ``EXCLUDED`` by this run.
        unchanged: Rows already ``EXCLUDED`` (idempotent no-write).
        unmatched: Exclusion tickers with no ``instrument_metadata`` row (surfaced,
            never fabricated).
    """

    applied: int = 0
    unchanged: int = 0
    unmatched: list[str] = []

    @property
    def total(self) -> int:
        """Total exclusions processed across all three outcomes."""
        return self.applied + self.unchanged + len(self.unmatched)


def load_venue_exclusions(path: Path) -> dict[str, ExclusionRecord]:
    """Parse ``venue_exclusions.csv`` into a ``{TICKER: ExclusionRecord}`` map (pure).

    A missing, empty, or header-only file returns ``{}`` — the register is optional
    and must never break an import. A present-but-wrong header, or a file that
    cannot be read/decoded, raises ``VenueExclusionError`` so callers degrade to a
    single warning uniformly.

    A row is **refused** (skipped with a warning, not an error) when its ticker,
    reason, or evidence is blank. This is the register's core invariant: an
    unjustified row is not an exclusion, and half-filled worklist lines must not
    silently remove tickers from the gate. Duplicate tickers: last wins.

    Args:
        path: Path to the exclusion CSV.

    Returns:
        Mapping of normalized ticker to its ``ExclusionRecord``.

    Raises:
        VenueExclusionError: If the header is present but wrong, or the file cannot
            be read/decoded/parsed.
    """
    if not path.exists():
        return {}

    exclusions: dict[str, ExclusionRecord] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                return {}  # empty file — a no-op, like an absent/header-only file
            if [c.strip().lower() for c in header] != _EXPECTED_HEADER:
                raise VenueExclusionError(
                    "venue_exclusions header must be exactly "
                    f"'ticker,reason,evidence' (got: {header!r})"
                )
            for row in reader:
                _merge_row(row, exclusions)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise VenueExclusionError(f"venue_exclusions could not be read: {exc}") from exc
    return exclusions


def _merge_row(row: list[str], exclusions: dict[str, ExclusionRecord]) -> None:
    """Normalize and fold one CSV data row into ``exclusions`` (refuse unusable rows)."""
    if len(row) < 3:
        return
    ticker = row[0].strip().upper()
    reason = row[1].strip()
    evidence = row[2].strip()
    if not ticker or not reason or not evidence:
        # Refused, and logged at warning: a half-filled row means someone intended
        # to exclude a ticker and did not finish justifying it. Silence here would
        # leave the ticker failing the gate with no explanation of why.
        logger.warning(
            "venue_exclusion_row_refused",
            ticker=ticker or None,
            has_reason=bool(reason),
            has_evidence=bool(evidence),
        )
        return
    if ticker in exclusions and exclusions[ticker].reason != reason:
        logger.warning(
            "venue_exclusion_shadowed",
            ticker=ticker,
            kept=reason,
            dropped=exclusions[ticker].reason,
        )
    exclusions[ticker] = ExclusionRecord(ticker=ticker, reason=reason, evidence=evidence)


def drop_overridden_exclusions(
    exclusions: dict[str, ExclusionRecord],
    overrides: dict[str, str],
) -> tuple[dict[str, ExclusionRecord], list[str]]:
    """Resolve the override-vs-exclusion conflict: a resolved venue always wins (pure).

    A ticker present in both registers is contradictory — it cannot be both
    qualified and unqualifiable. The override wins because it carries positive
    evidence (an authoritative venue) where the exclusion only asserts absence, and
    because letting an exclusion mask a working ticker would silently shrink the
    backtestable universe.

    Args:
        exclusions: Parsed exclusion register.
        overrides: Parsed ``{TICKER: VENUE}`` override map.

    Returns:
        ``(kept, shadowed)`` — the exclusions to apply, and the sorted tickers
        dropped because an override covers them (for operator reporting).
    """
    shadowed = sorted(t for t in exclusions if t in overrides)
    for ticker in shadowed:
        logger.warning("venue_exclusion_overridden", ticker=ticker, venue=overrides[ticker])
    kept = {t: rec for t, rec in exclusions.items() if t not in overrides}
    return kept, shadowed


def merge_venue_exclusions(
    exclusions: dict[str, ExclusionRecord],
    repository: SyncInstrumentMetadataRepository,
    excluded_at: datetime,
) -> VenueExclusionMergeResult:
    """Merge exclusions into ``instrument_metadata``, tallying outcomes.

    Iterates tickers in sorted order (deterministic) and delegates each to
    ``repository.apply_venue_exclusion``. The clock is threaded in so unchanged
    rows never churn — a re-run is a provable no-op.

    Args:
        exclusions: ``{TICKER: ExclusionRecord}`` from ``load_venue_exclusions``.
        repository: Sync metadata repository backing the store.
        excluded_at: Timestamp recorded on rows the merge changes.

    Returns:
        The applied/unchanged/unmatched tally.
    """
    result = VenueExclusionMergeResult()
    for ticker, record in sorted(exclusions.items()):
        outcome = repository.apply_venue_exclusion(ticker, record.reason, excluded_at)
        if outcome == "applied":
            result.applied += 1
        elif outcome == "unchanged":
            result.unchanged += 1
        else:
            result.unmatched.append(ticker)
    return result
