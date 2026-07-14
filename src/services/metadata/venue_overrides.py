"""Manual venue corrections: load ``venue_overrides.csv`` and merge with precedence (Story 3.3).

The operator supplies venues the conservative FMP map (ADR-6) could not resolve
in a git-tracked ``venue_overrides.csv`` (header exactly ``ticker,venue``). This
module owns the pure CSV parse (``load_venue_overrides``) and the store-merge
orchestration (``merge_venue_overrides``) that flips matching rows to
``RESOLVED`` with the override venue taking precedence.

The merge is an import-orchestration concern layered over the persisted metadata
rows — it never calls ``FMPClient`` / the provider, and the clock is passed in by
the caller so unchanged rows stay untouched (idempotent re-runs).
"""

import csv
from datetime import datetime
from pathlib import Path

import structlog
from pydantic import BaseModel

from src.db.repositories.instrument_metadata_repository_sync import (
    SyncInstrumentMetadataRepository,
)
from src.models.instrument_metadata import NA_SENTINEL

logger = structlog.get_logger(__name__)

#: The exact header the override file must carry (cells compared case-insensitively).
_EXPECTED_HEADER = ["ticker", "venue"]

#: Max venue length — matches the ``instrument_metadata.venue`` ``String(20)`` column;
#: a longer value is a typo/garbage, skipped at load rather than failing at write time.
_MAX_VENUE_LEN = 20


class VenueOverrideError(Exception):
    """Raised on a malformed override file (header not exactly ``ticker,venue``)."""


class VenueOverrideMergeResult(BaseModel):
    """Outcome tally for a ``merge_venue_overrides`` run.

    Attributes:
        applied: Rows whose venue/status were changed by an override.
        unchanged: Rows already at the override target (idempotent no-write).
        unmatched: Override tickers with no ``instrument_metadata`` row (surfaced,
            never fabricated).
    """

    applied: int = 0
    unchanged: int = 0
    unmatched: list[str] = []

    @property
    def total(self) -> int:
        """Total overrides processed across all three outcomes."""
        return self.applied + self.unchanged + len(self.unmatched)


def load_venue_overrides(path: Path) -> dict[str, str]:
    """Parse ``venue_overrides.csv`` into a ``{TICKER: VENUE}`` map (pure).

    A missing or empty file returns ``{}`` (overrides are optional; an absent,
    empty, or header-only file must never break an import). A present-but-wrong
    header, or a file that cannot be read/decoded (bad bytes, embedded NUL,
    permission error), raises ``VenueOverrideError`` so both callers degrade to a
    single warning uniformly. Data rows are normalized (ticker/venue upper-cased,
    trimmed); a row with a blank ticker, a blank venue, a venue equal to
    ``NA_SENTINEL``, or a venue longer than the ``venue`` column is skipped (an
    unfilled/garbage worklist line is not an error). Duplicate tickers: last wins.

    Args:
        path: Path to the override CSV.

    Returns:
        Mapping of normalized ticker to normalized venue code.

    Raises:
        VenueOverrideError: If the header is present but not exactly
            ``ticker,venue``, or the file cannot be read/decoded/parsed.
    """
    if not path.exists():
        return {}

    overrides: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None:
                return {}  # empty file — treated as a no-op, like an absent/header-only file
            if [c.strip().lower() for c in header] != _EXPECTED_HEADER:
                raise VenueOverrideError(
                    f"venue_overrides header must be exactly 'ticker,venue' (got: {header!r})"
                )
            for row in reader:
                _merge_row(row, overrides)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        # A malformed/unreadable file degrades like a bad header — never a raw traceback.
        raise VenueOverrideError(f"venue_overrides could not be read: {exc}") from exc
    return overrides


def _merge_row(row: list[str], overrides: dict[str, str]) -> None:
    """Normalize and fold one CSV data row into ``overrides`` (skip unusable rows)."""
    if len(row) < 2:
        return
    ticker = row[0].strip().upper()
    venue = row[1].strip().upper()
    if not ticker or not venue or venue == NA_SENTINEL or len(venue) > _MAX_VENUE_LEN:
        logger.debug("venue_override_row_skipped", ticker=ticker or None, venue=venue or None)
        return
    if ticker in overrides and overrides[ticker] != venue:
        logger.warning(
            "venue_override_shadowed", ticker=ticker, kept=venue, dropped=overrides[ticker]
        )
    overrides[ticker] = venue


def merge_venue_overrides(
    overrides: dict[str, str],
    repository: SyncInstrumentMetadataRepository,
    resolved_at: datetime,
) -> VenueOverrideMergeResult:
    """Merge overrides into ``instrument_metadata`` with precedence, tallying outcomes.

    Iterates tickers in sorted order (deterministic) and delegates each to
    ``repository.apply_venue_override`` (which flips a matching row to ``RESOLVED``
    or reports ``unchanged``/``unmatched``). The clock is threaded in via
    ``resolved_at`` — the caller stamps it once so unchanged rows never churn.

    Args:
        overrides: ``{TICKER: VENUE}`` map from ``load_venue_overrides``.
        repository: Sync metadata repository backing the store.
        resolved_at: Timestamp recorded on rows the merge changes.

    Returns:
        The applied/unchanged/unmatched tally.
    """
    result = VenueOverrideMergeResult()
    for ticker, venue in sorted(overrides.items()):
        outcome = repository.apply_venue_override(ticker, venue, resolved_at)
        if outcome == "applied":
            result.applied += 1
        elif outcome == "unchanged":
            result.unchanged += 1
        else:
            result.unmatched.append(ticker)
    return result
