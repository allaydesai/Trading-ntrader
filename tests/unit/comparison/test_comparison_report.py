"""Unit tests for the ComparisonReport tolerance evaluator."""

from datetime import datetime, timezone

import pytest

from src.models.comparison_report import (
    BacktestResultSummary,
    ComparisonReport,
    evaluate_tolerance,
)


def _summary(
    *,
    total_trades: int = 100,
    total_pnl: float = 10_000.0,
    bar_count: int = 98_000,
    instrument_id: str = "AAPL.NASDAQ",
    data_source: str = "catalog",
    final_balance: float = 1_010_000.0,
    total_pnl_percentage: float = 1.0,
) -> BacktestResultSummary:
    """Build a BacktestResultSummary fixture with overridable fields."""
    return BacktestResultSummary(
        total_trades=total_trades,
        total_pnl=total_pnl,
        total_pnl_percentage=total_pnl_percentage,
        final_balance=final_balance,
        bar_count=bar_count,
        instrument_id=instrument_id,
        data_source=data_source,
    )


@pytest.mark.unit
class TestToleranceEvaluator:
    """Parametrised tolerance pass/fail scenarios."""

    def test_all_pass(self) -> None:
        """Identical metrics produce all-pass verdict."""
        legacy = _summary(total_trades=120, total_pnl=12_345.0, bar_count=98_100)
        firstrate = _summary(total_trades=120, total_pnl=12_345.0, bar_count=98_100)

        report = evaluate_tolerance(legacy, firstrate)

        assert report.bar_count_passed
        assert report.trade_count_passed
        assert report.pnl_passed
        assert report.overall_passed
        assert report.notes == []

    def test_bar_count_breach(self) -> None:
        """A 1% bar count diff exceeds the 0.5% threshold."""
        legacy = _summary(bar_count=100_000)
        firstrate = _summary(bar_count=98_900)  # 1.1% diff

        report = evaluate_tolerance(legacy, firstrate)

        assert not report.bar_count_passed
        assert not report.overall_passed
        assert report.bar_count_delta > 0.005
        assert any("bar_count" in note for note in report.notes)

    def test_bar_count_at_boundary_passes(self) -> None:
        """Exactly 0.5% diff is on the boundary and passes."""
        legacy = _summary(bar_count=100_000)
        firstrate = _summary(bar_count=99_500)  # exactly 0.5%

        report = evaluate_tolerance(legacy, firstrate)

        assert report.bar_count_passed

    def test_trade_count_breach_zero_tolerance(self) -> None:
        """Any trade count diff fails (zero tolerance)."""
        legacy = _summary(total_trades=120)
        firstrate = _summary(total_trades=121)

        report = evaluate_tolerance(legacy, firstrate)

        assert not report.trade_count_passed
        assert not report.overall_passed
        assert report.trade_count_delta == 1
        assert any("trade_count" in note for note in report.notes)

    def test_pnl_breach(self) -> None:
        """0.5% PnL drift exceeds the 0.1% threshold."""
        legacy = _summary(total_pnl=10_000.0)
        firstrate = _summary(total_pnl=10_050.0)  # 0.5% diff

        report = evaluate_tolerance(legacy, firstrate)

        assert not report.pnl_passed
        assert not report.overall_passed
        assert report.pnl_delta_pct > 0.001
        assert any("pnl" in note.lower() for note in report.notes)

    def test_pnl_within_tolerance(self) -> None:
        """0.05% PnL drift passes the 0.1% threshold."""
        legacy = _summary(total_pnl=10_000.0)
        firstrate = _summary(total_pnl=10_005.0)  # 0.05%

        report = evaluate_tolerance(legacy, firstrate)

        assert report.pnl_passed

    def test_all_breach(self) -> None:
        """All three metrics breach simultaneously — overall fails."""
        legacy = _summary(total_trades=120, total_pnl=10_000.0, bar_count=100_000)
        firstrate = _summary(total_trades=130, total_pnl=11_000.0, bar_count=98_000)

        report = evaluate_tolerance(legacy, firstrate)

        assert not report.bar_count_passed
        assert not report.trade_count_passed
        assert not report.pnl_passed
        assert not report.overall_passed
        assert len(report.notes) == 3

    def test_zero_pnl_does_not_divide_by_zero(self) -> None:
        """Both runs at near-zero PnL must not raise ZeroDivisionError."""
        legacy = _summary(total_pnl=0.0)
        firstrate = _summary(total_pnl=0.5)

        report = evaluate_tolerance(legacy, firstrate)

        # 0.5 / max(abs(0), abs(0.5), 1.0) = 0.5
        assert report.pnl_delta_pct == pytest.approx(0.5)
        assert not report.pnl_passed

    def test_zero_bars_does_not_divide_by_zero(self) -> None:
        """Both runs at zero bars must not raise ZeroDivisionError."""
        legacy = _summary(bar_count=0)
        firstrate = _summary(bar_count=0)

        report = evaluate_tolerance(legacy, firstrate)

        # 0 / max(0, 0, 1) = 0
        assert report.bar_count_delta == 0.0
        assert report.bar_count_passed

    def test_overall_passed_is_and_of_metrics(self) -> None:
        """overall_passed is logical AND of the three per-metric verdicts."""
        legacy = _summary(total_trades=100, total_pnl=10_000.0, bar_count=100_000)
        firstrate = _summary(total_trades=100, total_pnl=10_000.0, bar_count=99_000)
        # Bar count diff = 1% > 0.5% threshold. Others pass.

        report = evaluate_tolerance(legacy, firstrate)

        assert report.trade_count_passed
        assert report.pnl_passed
        assert not report.bar_count_passed
        assert not report.overall_passed

    def test_tolerances_can_be_overridden(self) -> None:
        """Caller can pass custom tolerances (used for tests of edge behavior)."""
        legacy = _summary(total_pnl=10_000.0)
        firstrate = _summary(total_pnl=10_005.0)

        # Override pnl_tol to be tighter than the diff
        report = evaluate_tolerance(legacy, firstrate, pnl_tol=0.0001)

        assert not report.pnl_passed

    def test_report_serialization_round_trips(self) -> None:
        """ComparisonReport JSON dump must round-trip through Pydantic."""
        legacy = _summary()
        firstrate = _summary()

        report = evaluate_tolerance(legacy, firstrate)
        as_json = report.model_dump_json(indent=2)

        rebuilt = ComparisonReport.model_validate_json(as_json)
        assert rebuilt.overall_passed == report.overall_passed
        assert rebuilt.dataset == report.dataset
        assert rebuilt.legacy_metrics.total_trades == legacy.total_trades

    def test_dataset_label_in_report(self) -> None:
        """Dataset label is captured for downstream reporting."""
        legacy = _summary()
        firstrate = _summary()

        report = evaluate_tolerance(legacy, firstrate, dataset="AAPL_2018_1-MINUTE")

        assert report.dataset == "AAPL_2018_1-MINUTE"

    def test_generated_at_is_utc(self) -> None:
        """generated_at is timezone-aware UTC."""
        legacy = _summary()
        firstrate = _summary()

        report = evaluate_tolerance(legacy, firstrate)

        assert report.generated_at.tzinfo is not None
        assert report.generated_at.tzinfo.utcoffset(report.generated_at) == (
            datetime.now(timezone.utc).utcoffset()
        )
