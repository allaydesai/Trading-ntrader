"""Unit tests for the pure venue-coverage aggregate (Story 3.4)."""

import pytest

from src.models.instrument_metadata import ResolutionStatus
from src.services.metadata.coverage_report import VenueCoverage


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
