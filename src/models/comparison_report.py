"""Reference comparison report model and tolerance evaluator.

Used by the AAPL 2018 1-MINUTE reference comparison harness (Story 3.3) to
produce a structured verdict comparing two backtest runs of the same
strategy against the same logical dataset, loaded via two different paths
(legacy CSV vs FirstRate-imported catalog).

Tolerance values are Phase 1 agreement from
`_bmad-output/implementation-artifacts/epic-2-retro-2026-04-19.md:97`. They
are configuration, not law: a real divergence should be fixed at the source,
not papered over by widening these thresholds.
"""

from datetime import datetime, timezone
from typing import Final

from pydantic import BaseModel, Field

DEFAULT_BAR_COUNT_TOL: Final[float] = 0.005  # 0.5% — FirstRate dedupe drift
DEFAULT_TRADE_COUNT_TOL: Final[int] = 0  # exact match required
DEFAULT_PNL_TOL: Final[float] = 0.001  # 0.1% — Decimal vs float rounding


class BacktestResultSummary(BaseModel):
    """Snapshot of the metrics needed for cross-run comparison."""

    total_trades: int
    total_pnl: float
    total_pnl_percentage: float
    final_balance: float
    bar_count: int
    instrument_id: str
    data_source: str


class ComparisonReport(BaseModel):
    """Structured verdict for a two-path reference comparison run."""

    legacy_metrics: BacktestResultSummary
    firstrate_metrics: BacktestResultSummary
    bar_count_delta: float
    trade_count_delta: int
    pnl_delta_pct: float
    bar_count_passed: bool
    trade_count_passed: bool
    pnl_passed: bool
    overall_passed: bool
    notes: list[str] = Field(default_factory=list)
    generated_at: datetime
    dataset: str


def evaluate_tolerance(
    legacy: BacktestResultSummary,
    firstrate: BacktestResultSummary,
    *,
    bar_count_tol: float = DEFAULT_BAR_COUNT_TOL,
    trade_count_tol: int = DEFAULT_TRADE_COUNT_TOL,
    pnl_tol: float = DEFAULT_PNL_TOL,
    dataset: str = "AAPL_2018_1-MINUTE",
) -> ComparisonReport:
    """Compute per-metric deltas + verdicts for two backtest summaries.

    Denominators use ``max(..., 1)`` / ``max(..., 1.0)`` so the degenerate
    "both zero" case yields a 0.0 delta (trivial pass) instead of
    ``ZeroDivisionError``.
    """
    bar_max = max(legacy.bar_count, firstrate.bar_count, 1)
    bar_count_delta = abs(legacy.bar_count - firstrate.bar_count) / bar_max

    trade_count_delta = abs(legacy.total_trades - firstrate.total_trades)

    pnl_max = max(abs(legacy.total_pnl), abs(firstrate.total_pnl), 1.0)
    pnl_delta_pct = abs(legacy.total_pnl - firstrate.total_pnl) / pnl_max

    bar_count_passed = bar_count_delta <= bar_count_tol
    trade_count_passed = trade_count_delta <= trade_count_tol
    pnl_passed = pnl_delta_pct <= pnl_tol

    notes: list[str] = []
    if not bar_count_passed:
        notes.append(f"bar_count Δ={bar_count_delta:.4%} > {bar_count_tol:.2%}")
    if not trade_count_passed:
        notes.append(f"trade_count Δ={trade_count_delta} > {trade_count_tol}")
    if not pnl_passed:
        notes.append(f"pnl Δ={pnl_delta_pct:.4%} > {pnl_tol:.2%}")

    return ComparisonReport(
        legacy_metrics=legacy,
        firstrate_metrics=firstrate,
        bar_count_delta=bar_count_delta,
        trade_count_delta=trade_count_delta,
        pnl_delta_pct=pnl_delta_pct,
        bar_count_passed=bar_count_passed,
        trade_count_passed=trade_count_passed,
        pnl_passed=pnl_passed,
        overall_passed=bar_count_passed and trade_count_passed and pnl_passed,
        notes=notes,
        generated_at=datetime.now(timezone.utc),
        dataset=dataset,
    )
