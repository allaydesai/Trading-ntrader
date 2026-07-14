"""Pure venue-coverage aggregate for the completeness gate (Story 3.4).

Domain-facing math over the per-status counts produced by
``SyncInstrumentMetadataRepository.count_by_status`` — no I/O, no clock, no Click.
The CLI (``metadata coverage``) renders this and owns the process exit code; this
module owns only the coverage percentage and the pass/fail verdict.
"""

from dataclasses import dataclass

from src.models.instrument_metadata import ResolutionStatus


@dataclass(frozen=True)
class VenueCoverage:
    """Venue-coverage snapshot + completeness verdict.

    ``coverage_pct`` is measured over ``decided`` (RESOLVED + VENUE_UNRESOLVED),
    NOT ``total`` — never-attempted ``UNRESOLVED`` rows are surfaced separately
    but kept out of the ratio, so the gate identity holds exactly:
    ``is_complete`` ⟺ ``coverage_pct == 100.0``.

    Attributes:
        resolved: Rows with a known venue (backtestable).
        venue_unresolved: Rows resolution ran on but could not assign a venue —
            the gate blocker.
        unresolved: Rows resolution has not yet been attempted for (transient).
    """

    resolved: int
    venue_unresolved: int
    unresolved: int

    @classmethod
    def from_counts(cls, counts: dict[ResolutionStatus, int]) -> "VenueCoverage":
        """Build from a status→count map (absent statuses default to 0)."""
        return cls(
            resolved=counts.get(ResolutionStatus.RESOLVED, 0),
            venue_unresolved=counts.get(ResolutionStatus.VENUE_UNRESOLVED, 0),
            unresolved=counts.get(ResolutionStatus.UNRESOLVED, 0),
        )

    @property
    def decided(self) -> int:
        """Rows resolution has reached a venue verdict on (coverage denominator)."""
        return self.resolved + self.venue_unresolved

    @property
    def total(self) -> int:
        """All metadata rows across every status."""
        return self.resolved + self.venue_unresolved + self.unresolved

    @property
    def coverage_pct(self) -> float:
        """Percent of decided rows with a venue; 100.0 when nothing is decided."""
        if self.decided == 0:
            return 100.0
        return self.resolved / self.decided * 100

    @property
    def is_complete(self) -> bool:
        """The completeness gate: no ticker left VENUE_UNRESOLVED (no venue guessed)."""
        return self.venue_unresolved == 0
