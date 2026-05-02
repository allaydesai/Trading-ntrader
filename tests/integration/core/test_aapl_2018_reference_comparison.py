"""Integration test for the AAPL 2018 reference comparison harness.

Story 3.3 AC #1, #2, #14, #15 — runs the full reference comparison through
the same orchestrator path the CLI uses. Gated on the local FirstRate
``e2e-test`` catalog AND the legacy CSV being present, so CI without local
data still goes green.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REQUIRED_LEGACY_CSV = Path("data/AAPL_1min.csv")
GATING_ENV = "E2E_CATALOG_AVAILABLE"


def _gating_skip_reason() -> str | None:
    """Return None if the test can run, else a clear skip reason."""
    if os.environ.get(GATING_ENV) != "1":
        return f"{GATING_ENV}=1 not set — local FirstRate e2e-test catalog unavailable"
    if not REQUIRED_LEGACY_CSV.exists():
        return f"{REQUIRED_LEGACY_CSV} missing — legacy CSV reference unavailable"
    return None


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    """Per-test evidence directory.

    Production users hit ``/tmp/story-3-3-evidence/``; tests use pytest's
    isolated tmp_path so concurrent runs don't collide.
    """
    out = tmp_path / "story-3-3-evidence"
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.mark.integration
class TestReferenceComparisonHarness:
    """End-to-end gated tests for the reference-comparison harness."""

    def test_harness_runs_both_paths(self, evidence_dir: Path) -> None:
        """Harness produces a ComparisonReport with both runs populated."""
        skip_reason = _gating_skip_reason()
        if skip_reason is not None:
            pytest.skip(skip_reason)

        from scripts.verify_aapl_2018_reference import run_comparison_harness

        report = run_comparison_harness(
            legacy_csv=REQUIRED_LEGACY_CSV,
            firstrate_catalog="e2e-test",
            output_dir=evidence_dir,
            skip_import=False,
        )

        # Both runs ran end-to-end
        assert report.legacy_metrics.bar_count > 0
        assert report.firstrate_metrics.bar_count > 0
        assert report.legacy_metrics.instrument_id == "AAPL.NASDAQ"
        assert report.firstrate_metrics.instrument_id == "AAPL.NASDAQ"

        # Tolerance fields populated
        assert isinstance(report.bar_count_passed, bool)
        assert isinstance(report.trade_count_passed, bool)
        assert isinstance(report.pnl_passed, bool)

        # Evidence JSON written
        json_path = evidence_dir / "aapl_2018_comparison.json"
        assert json_path.exists()
        assert json_path.stat().st_size > 0

    def test_full_reference_comparison_within_tolerance(self, evidence_dir: Path) -> None:
        """All three tolerances pass on the AAPL 2018 1-MINUTE comparison."""
        skip_reason = _gating_skip_reason()
        if skip_reason is not None:
            pytest.skip(skip_reason)

        from scripts.verify_aapl_2018_reference import run_comparison_harness

        report = run_comparison_harness(
            legacy_csv=REQUIRED_LEGACY_CSV,
            firstrate_catalog="e2e-test",
            output_dir=evidence_dir,
            skip_import=False,
        )

        assert report.bar_count_passed, (
            f"Bar count diverged: legacy={report.legacy_metrics.bar_count} "
            f"firstrate={report.firstrate_metrics.bar_count} "
            f"Δ={report.bar_count_delta:.4%}"
        )
        assert report.trade_count_passed, (
            f"Trade count diverged: legacy={report.legacy_metrics.total_trades} "
            f"firstrate={report.firstrate_metrics.total_trades}"
        )
        assert report.pnl_passed, (
            f"PnL diverged: legacy={report.legacy_metrics.total_pnl:.2f} "
            f"firstrate={report.firstrate_metrics.total_pnl:.2f} "
            f"Δ={report.pnl_delta_pct:.4%}"
        )
        assert report.overall_passed

    def test_harness_raises_on_unwritable_output(self, tmp_path: Path) -> None:
        """Unwritable output dir raises a clear OSError, not a silent failure."""
        skip_reason = _gating_skip_reason()
        if skip_reason is not None:
            pytest.skip(skip_reason)

        from scripts.verify_aapl_2018_reference import run_comparison_harness

        unwritable = tmp_path / "readonly"
        unwritable.mkdir()
        unwritable.chmod(0o500)  # r-x — no write permission

        try:
            with pytest.raises((OSError, PermissionError)):
                run_comparison_harness(
                    legacy_csv=REQUIRED_LEGACY_CSV,
                    firstrate_catalog="e2e-test",
                    output_dir=unwritable,
                    skip_import=True,
                )
        finally:
            # Restore permissions so pytest can clean up tmp_path
            unwritable.chmod(0o700)
