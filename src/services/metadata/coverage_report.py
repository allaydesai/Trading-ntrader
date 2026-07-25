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
        excluded: Rows adjudicated unqualifiable via the exclusion register — an
            audited gate exit, kept out of the ratio but always reported so the
            size of the register stays visible.
    """

    resolved: int
    venue_unresolved: int
    unresolved: int
    excluded: int = 0

    @classmethod
    def from_counts(cls, counts: dict[ResolutionStatus, int]) -> "VenueCoverage":
        """Build from a status→count map (absent statuses default to 0)."""
        return cls(
            resolved=counts.get(ResolutionStatus.RESOLVED, 0),
            venue_unresolved=counts.get(ResolutionStatus.VENUE_UNRESOLVED, 0),
            unresolved=counts.get(ResolutionStatus.UNRESOLVED, 0),
            excluded=counts.get(ResolutionStatus.EXCLUDED, 0),
        )

    @property
    def decided(self) -> int:
        """Rows resolution has reached a venue verdict on (coverage denominator)."""
        return self.resolved + self.venue_unresolved

    @property
    def total(self) -> int:
        """All metadata rows across every status."""
        return self.resolved + self.venue_unresolved + self.unresolved + self.excluded

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


@dataclass(frozen=True)
class QualificationCoverage:
    """Is the venue-resolved universe actually loadable? (the gate's second term)

    Venue coverage alone was not enough. The venue verdict lives in
    ``instrument_metadata`` while the identity every consumer loads bars by lives in
    ``catalog_instruments.nautilus_id`` — and for a long time only the import
    pipeline wrote the latter. So ``metadata apply-overrides`` could drive venue
    coverage to 100% while ``backtest_loader`` still refused every ticker for want
    of a ``nautilus_id``. A gate that passes on an unloadable universe is worse than
    no gate, because it ends the investigation.

    Attributes:
        catalog: Catalog these counts were measured over.
        resolved: RESOLVED tickers present in this catalog.
        gap: How many of those still lack a ``nautilus_id``. Counts only RESOLVED
            tickers — EXCLUDED and VENUE_UNRESOLVED rows are *supposed* to have a
            NULL identity, so including them would flag correct state as broken.
    """

    catalog: str
    resolved: int
    gap: int

    @property
    def qualified(self) -> int:
        """RESOLVED tickers that carry a loadable identity."""
        return self.resolved - self.gap

    @property
    def is_complete(self) -> bool:
        """Every resolved ticker is qualified — nothing resolved-but-unloadable."""
        return self.gap == 0
