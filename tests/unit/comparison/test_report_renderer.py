"""Unit tests for the comparison report stdout renderer."""

import pytest

from src.models.comparison_report import (
    BacktestResultSummary,
    evaluate_tolerance,
)
from src.services.comparison_renderer import render_comparison_table


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
class TestComparisonRenderer:
    """Verify renderer output for both pass and breach scenarios."""

    def test_renderer_pass_shows_check(self) -> None:
        """All-pass renders ✅ markers and no ❌ TOLERANCE BREACH."""
        legacy = _summary()
        firstrate = _summary()
        report = evaluate_tolerance(legacy, firstrate)

        rendered = render_comparison_table(report)

        assert "✅ ALL TOLERANCES PASSED" in rendered
        assert "❌ TOLERANCE BREACH" not in rendered

    def test_renderer_breach_marks_red(self) -> None:
        """A trade-count breach renders the breach line and metric name."""
        legacy = _summary(total_trades=100)
        firstrate = _summary(total_trades=105)
        report = evaluate_tolerance(legacy, firstrate)

        rendered = render_comparison_table(report)

        assert "❌ TOLERANCE BREACH" in rendered
        assert "trade_count" in rendered

    def test_renderer_includes_metrics(self) -> None:
        """Both run totals and the Δ rows appear in the rendered table."""
        legacy = _summary(total_trades=120, total_pnl=12_345.0, bar_count=98_100)
        firstrate = _summary(total_trades=120, total_pnl=12_400.0, bar_count=98_100)
        report = evaluate_tolerance(legacy, firstrate)

        rendered = render_comparison_table(report)

        assert "Bar Count" in rendered
        assert "Trade Count" in rendered
        assert "Total PnL" in rendered
        # Numeric values present (loose match — formatting may add commas)
        assert "12,345" in rendered or "12345" in rendered

    def test_renderer_lists_all_breaches(self) -> None:
        """All three breaches each emit a ❌ TOLERANCE BREACH line."""
        legacy = _summary(total_trades=100, total_pnl=10_000.0, bar_count=100_000)
        firstrate = _summary(total_trades=110, total_pnl=11_000.0, bar_count=98_000)
        report = evaluate_tolerance(legacy, firstrate)

        rendered = render_comparison_table(report)

        # Three metrics each breach — three lines
        assert rendered.count("❌ TOLERANCE BREACH") == 3
