"""Integration test for the AAPL 2018 IBKR-vs-FirstRate parity harness.

Story 3.4 ACs #1, #2, #6, #9, #10 — runs the parity comparison through
the same orchestrator path the CLI uses. Gated on:

- ``IBKR_AVAILABLE=1`` — caller asserts the IBKR Gateway is up and the
  ``IBKR_*`` env vars in ``.env`` are valid (paper-trading, port 4002).
- ``E2E_CATALOG_AVAILABLE=1`` — caller asserts the FirstRate ``e2e-test``
  catalog has AAPL 2018 1-MINUTE bars (Story 1-x import).

When ``IBKR_AVAILABLE`` is unset the test ``pytest.skip``s. When it IS
set but the Gateway is unreachable the test FAILS (not skips) with the
underlying IBKR connection error — the env var is a developer
assertion, not an active healthcheck (AC #9).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

IBKR_GATING_ENV = "IBKR_AVAILABLE"
FIRSTRATE_GATING_ENV = "E2E_CATALOG_AVAILABLE"


def _gating_skip_reason() -> str | None:
    """Return None if both gates are set, else a clear skip reason."""
    if os.environ.get(IBKR_GATING_ENV) != "1":
        return f"{IBKR_GATING_ENV}=1 not set — IBKR Gateway parity gate disabled"
    if os.environ.get(FIRSTRATE_GATING_ENV) != "1":
        return f"{FIRSTRATE_GATING_ENV}=1 not set — local FirstRate e2e-test catalog unavailable"
    return None


@pytest.fixture
def evidence_dir(tmp_path: Path) -> Path:
    """Per-test evidence directory.

    Production users hit ``/tmp/story-3-4-evidence/``; tests use pytest's
    isolated tmp_path so concurrent runs don't collide.
    """
    out = tmp_path / "story-3-4-evidence"
    out.mkdir(parents=True, exist_ok=True)
    return out


@pytest.mark.integration
class TestIbkrVsFirstrateHarness:
    """End-to-end gated tests for the IBKR-vs-FirstRate parity harness."""

    def test_harness_runs_both_paths(self, evidence_dir: Path) -> None:
        """Harness produces a ComparisonReport with both runs populated."""
        skip_reason = _gating_skip_reason()
        if skip_reason is not None:
            pytest.skip(skip_reason)

        from scripts.verify_aapl_2018_reference import run_comparison_harness

        report = run_comparison_harness(
            firstrate_catalog="e2e-test",
            output_dir=evidence_dir,
            skip_fetch=False,
        )

        # Both runs ran end-to-end (legacy_metrics is the IBKR side here)
        assert report.legacy_metrics.bar_count > 0
        assert report.firstrate_metrics.bar_count > 0
        assert report.legacy_metrics.instrument_id == "AAPL.NASDAQ"
        assert report.firstrate_metrics.instrument_id == "AAPL.NASDAQ"
        assert "IBKR" in report.legacy_metrics.data_source
        assert "FirstRate" in report.firstrate_metrics.data_source

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
            firstrate_catalog="e2e-test",
            output_dir=evidence_dir,
            skip_fetch=False,
        )

        assert report.bar_count_passed, (
            f"Bar count diverged: ibkr={report.legacy_metrics.bar_count} "
            f"firstrate={report.firstrate_metrics.bar_count} "
            f"Δ={report.bar_count_delta:.4%}"
        )
        assert report.trade_count_passed, (
            f"Trade count diverged: ibkr={report.legacy_metrics.total_trades} "
            f"firstrate={report.firstrate_metrics.total_trades}"
        )
        assert report.pnl_passed, (
            f"PnL diverged: ibkr={report.legacy_metrics.total_pnl:.2f} "
            f"firstrate={report.firstrate_metrics.total_pnl:.2f} "
            f"Δ={report.pnl_delta_pct:.4%}"
        )
        assert report.overall_passed

    def test_harness_raises_on_unwritable_output(self, tmp_path: Path) -> None:
        """Unwritable output dir raises a clear OSError, not a silent failure.

        This case is independent of the IBKR/FirstRate gates because the
        write-probe pre-flight fires before any fetch or catalog query.
        """
        from scripts.verify_aapl_2018_reference import run_comparison_harness

        unwritable = tmp_path / "readonly"
        unwritable.mkdir()
        unwritable.chmod(0o500)  # r-x — no write permission

        try:
            with pytest.raises((OSError, PermissionError)):
                run_comparison_harness(
                    firstrate_catalog="e2e-test",
                    output_dir=unwritable,
                    skip_fetch=True,
                )
        finally:
            # Restore permissions so pytest can clean up tmp_path
            unwritable.chmod(0o700)
