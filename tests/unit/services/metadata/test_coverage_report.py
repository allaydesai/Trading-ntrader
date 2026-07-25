"""Unit tests for the pure venue-coverage aggregate (Story 3.4)."""

import pytest

from src.models.instrument_metadata import ResolutionStatus
from src.services.metadata.coverage_report import QualificationCoverage, VenueCoverage


@pytest.mark.unit
class TestVenueCoverage:
    """`VenueCoverage.from_counts` + coverage/gate math."""

    def test_all_resolved_is_complete_100pct(self):
        cov = VenueCoverage.from_counts({ResolutionStatus.RESOLVED: 10})
        assert cov.resolved == 10
        assert cov.venue_unresolved == 0
        assert cov.decided == 10
        assert cov.total == 10
        assert cov.coverage_pct == 100.0
        assert cov.is_complete is True

    def test_mixed_with_unresolved_venue_fails_gate(self):
        cov = VenueCoverage.from_counts(
            {ResolutionStatus.RESOLVED: 8, ResolutionStatus.VENUE_UNRESOLVED: 2}
        )
        assert cov.venue_unresolved == 2
        assert cov.decided == 10
        assert cov.coverage_pct == 80.0
        assert cov.is_complete is False

    def test_never_attempted_excluded_from_pct_and_gate(self):
        # UNRESOLVED = resolution not yet run: counted in total, out of the ratio,
        # and NOT a gate blocker (only VENUE_UNRESOLVED fails the gate).
        cov = VenueCoverage.from_counts(
            {ResolutionStatus.RESOLVED: 5, ResolutionStatus.UNRESOLVED: 5}
        )
        assert cov.unresolved == 5
        assert cov.total == 10
        assert cov.decided == 5
        assert cov.coverage_pct == 100.0
        assert cov.is_complete is True

    def test_empty_is_vacuously_complete(self):
        cov = VenueCoverage.from_counts({})
        assert cov.total == 0
        assert cov.decided == 0
        assert cov.coverage_pct == 100.0
        assert cov.is_complete is True

    def test_absent_statuses_default_to_zero(self):
        cov = VenueCoverage.from_counts({ResolutionStatus.VENUE_UNRESOLVED: 3})
        assert cov.resolved == 0
        assert cov.unresolved == 0
        assert cov.venue_unresolved == 3

    def test_gate_identity_holds(self):
        # is_complete  ⟺  coverage_pct == 100.0
        complete = VenueCoverage.from_counts({ResolutionStatus.RESOLVED: 4})
        assert complete.is_complete is True and complete.coverage_pct == 100.0

        incomplete = VenueCoverage.from_counts(
            {ResolutionStatus.RESOLVED: 4, ResolutionStatus.VENUE_UNRESOLVED: 1}
        )
        assert incomplete.is_complete is False and incomplete.coverage_pct < 100.0


@pytest.mark.unit
class TestExcludedRows:
    """EXCLUDED is an audited gate exit — counted and surfaced, never hidden."""

    def test_excluded_counted_in_total_but_not_in_the_ratio(self):
        cov = VenueCoverage.from_counts(
            {ResolutionStatus.RESOLVED: 8, ResolutionStatus.EXCLUDED: 2}
        )
        assert cov.excluded == 2
        assert cov.total == 10
        assert cov.decided == 8
        assert cov.coverage_pct == 100.0

    def test_excluded_does_not_block_the_gate(self):
        """The whole point of the register: an adjudicated ticker stops blocking."""
        cov = VenueCoverage.from_counts(
            {ResolutionStatus.RESOLVED: 8, ResolutionStatus.EXCLUDED: 2}
        )
        assert cov.is_complete is True

    def test_excluded_alongside_unresolved_still_fails(self):
        cov = VenueCoverage.from_counts(
            {
                ResolutionStatus.RESOLVED: 8,
                ResolutionStatus.EXCLUDED: 1,
                ResolutionStatus.VENUE_UNRESOLVED: 1,
            }
        )
        assert cov.is_complete is False

    def test_absent_excluded_defaults_to_zero(self):
        assert VenueCoverage.from_counts({ResolutionStatus.RESOLVED: 1}).excluded == 0


@pytest.mark.unit
class TestQualificationCoverage:
    """The second gate term: is the resolved universe actually loadable?"""

    def test_no_gap_is_complete(self):
        qual = QualificationCoverage(catalog="firstrate-etf", resolved=100, gap=0)
        assert qual.qualified == 100
        assert qual.is_complete is True

    def test_any_gap_fails(self):
        """Exactly the pre-fix state: venue coverage green, nothing loadable."""
        qual = QualificationCoverage(catalog="firstrate-etf", resolved=4612, gap=3451)
        assert qual.qualified == 1161
        assert qual.is_complete is False

    def test_empty_catalog_is_vacuously_complete(self):
        assert QualificationCoverage(catalog="empty", resolved=0, gap=0).is_complete is True
