"""Turn backfill results into the artefacts an operator and the pipeline consume.

Four outputs, each for a different reader:

- ``results.csv`` — the full record, for the evidence directory.
- ``rejects.csv`` — the human worklist: everything that needs adjudication.
- ``overrides-proposal.csv`` — machine input, in the exact ``ticker,venue`` shape
  ``load_venue_overrides`` already parses.
- ``disagreements.csv`` — where IBKR contradicts FMP, so the operator sees the
  scale of the discrepancy before any of it is applied.

Results flow into ``venue_overrides.csv`` rather than straight into the database.
That reuses the tested override path, and leaves every venue decision as a
git-tracked line someone can review — which is what the PRD means by resolved
manually rather than inferred. An IBKR contract-database lookup is an authoritative
source, not a guess, but it still gets recorded as a decision.
"""

import csv
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog

from src.services.metadata.providers.ibkr_venue_provider import (
    VenueOutcome,
    VenueQualification,
)
from src.services.metadata.venue_overrides import load_venue_overrides

logger = structlog.get_logger(__name__)

_RESULT_HEADER = [
    "ticker",
    "outcome",
    "venue",
    "primary_exchange",
    "con_id",
    "candidates",
    "detail",
    "attempts",
    "elapsed_s",
    "unexpected_venue",
]


@dataclass(frozen=True)
class MergeReport:
    """Outcome of folding a proposal into an existing override file.

    Attributes:
        added: Tickers the proposal contributed.
        unchanged: Tickers already present with the same venue.
        conflicts: ``{ticker: (existing, proposed)}`` where the file disagreed with
            the proposal. The existing value is kept.
        total: Row count written.
        backup: Where the pre-merge file was copied, if one existed.
    """

    added: int
    unchanged: int
    conflicts: dict[str, tuple[str, str]]
    total: int
    backup: Optional[Path] = None


def _rows(quals: Iterable[VenueQualification]) -> list[VenueQualification]:
    """Sorted by ticker so every artefact is diff-stable across runs."""
    return sorted(quals, key=lambda q: q.ticker)


def write_results_csv(path: Path, quals: Iterable[VenueQualification]) -> int:
    """Write the complete per-ticker record. Returns the row count."""
    rows = _rows(quals)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_RESULT_HEADER)
        for q in rows:
            writer.writerow(
                [
                    q.ticker,
                    q.outcome.value,
                    q.venue or "",
                    q.primary_exchange or "",
                    q.con_id or "",
                    "|".join(q.candidates),
                    q.detail,
                    q.attempts,
                    f"{q.elapsed_s:.3f}",
                    "yes" if q.unexpected_venue else "no",
                ]
            )
    return len(rows)


def needs_review(qual: VenueQualification) -> bool:
    """True when a human has to look at this ticker before it can be applied.

    Includes RESOLVED-but-unexpected-venue: IBKR is authoritative, so the venue is
    kept as-is, but a code outside the reviewed set is worth one pair of eyes
    before it propagates into instrument ids and parquet paths.
    """
    return qual.outcome is not VenueOutcome.RESOLVED or qual.unexpected_venue


def write_rejects_csv(path: Path, quals: Iterable[VenueQualification]) -> int:
    """Write only the tickers needing adjudication. Returns the row count."""
    return write_results_csv(path, [q for q in quals if needs_review(q)])


def write_overrides_proposal(path: Path, quals: Iterable[VenueQualification]) -> int:
    """Write resolved venues in the exact shape ``load_venue_overrides`` parses.

    Emits every resolved ticker, including ones where IBKR agrees with FMP. That
    makes ``venue_overrides.csv`` the single stable venue authority — immune to a
    later FMP re-resolution flipping an answer underneath the catalog. Agreeing
    rows merge as ``unchanged``, so there is no churn.
    """
    rows = [q for q in _rows(quals) if q.outcome is VenueOutcome.RESOLVED and q.venue]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "venue"])
        for q in rows:
            writer.writerow([q.ticker, q.venue])
    return len(rows)


def write_disagreements_csv(
    path: Path,
    quals: Iterable[VenueQualification],
    fmp_venues: Mapping[str, Optional[str]],
) -> int:
    """Write tickers where IBKR's venue contradicts the one already on record.

    Expected to be non-empty: FMP reports a *listing label* while IBKR reports the
    *primary exchange*, and NYSE-listed ETFs are very often primary-ARCA. The point
    is to put the scale of that in front of the operator before anything is applied.
    """
    rows = [
        (q.ticker, fmp_venues.get(q.ticker) or "", q.venue or "")
        for q in _rows(quals)
        if q.outcome is VenueOutcome.RESOLVED
        and fmp_venues.get(q.ticker)
        and fmp_venues.get(q.ticker) != q.venue
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "current_venue", "ibkr_venue"])
        writer.writerows(rows)
    return len(rows)


def merge_into_overrides(target: Path, proposal: Mapping[str, str]) -> MergeReport:
    """Fold a proposal into ``venue_overrides.csv``, keeping existing rows on conflict.

    Existing wins because the file is hand-curated and git-tracked: a row already
    there may encode a decision someone made deliberately (SPY→ARCA predates this
    tool). Conflicts are returned rather than resolved silently, so the operator can
    adjudicate. A backup is written first — this rewrites a tracked file.

    Args:
        target: The override CSV to update (created if absent).
        proposal: ``{TICKER: VENUE}`` to fold in.

    Returns:
        A ``MergeReport`` describing what changed.
    """
    existing = load_venue_overrides(target)
    backup: Optional[Path] = None
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        backup.write_bytes(target.read_bytes())

    conflicts: dict[str, tuple[str, str]] = {}
    added = unchanged = 0
    merged = dict(existing)
    for ticker, venue in proposal.items():
        ticker, venue = ticker.strip().upper(), venue.strip().upper()
        if ticker not in existing:
            merged[ticker] = venue
            added += 1
        elif existing[ticker] == venue:
            unchanged += 1
        else:
            conflicts[ticker] = (existing[ticker], venue)
            logger.warning(
                "venue_override_conflict_kept_existing",
                ticker=ticker,
                existing=existing[ticker],
                proposed=venue,
            )

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "venue"])
        for ticker in sorted(merged):
            writer.writerow([ticker, merged[ticker]])
    # Atomic swap: a crash mid-write must not leave a tracked file truncated.
    os.replace(tmp, target)

    return MergeReport(
        added=added,
        unchanged=unchanged,
        conflicts=conflicts,
        total=len(merged),
        backup=backup,
    )
