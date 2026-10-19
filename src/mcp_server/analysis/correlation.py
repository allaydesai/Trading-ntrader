"""Correlation, drawdown overlap and regime offsets across stored runs (S9.2, S9.4).

Reads only: each run's stored mark-to-market daily curve, joined on the dates
all of them share, plus one benchmark's closes for the regime labels. The
"total" column is the equal- or given-weight constant mix of the sleeves.
"""

from typing import Any

import pandas as pd

from src.core.robustness import portfolio as pf
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.run_reads import run_catalog
from src.mcp_server.analysis.sleeves import (
    Sleeve,
    attribute,
    benchmark_closes,
    load_sleeves,
    regime_table,
    sleeve_view,
)
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.studies import ledger

DEFINITIONS = {
    "window": "the dates every sleeve's stored daily curve shares (inner join); returns, "
    "not dollars, are weighted, so starting balances need not match",
    "correlation": "Pearson correlation of simple daily returns on the shared days; null for "
    "a sleeve whose returns never vary",
    "both_below": "share of shared days on which both sleeves were in a drawdown deeper than "
    "the threshold (drawdowns measured from the window's start)",
    "jaccard": "shared drawdown days over days on which either sleeve was in one",
    "episodes": "maximal runs of days below the threshold; a_with_b counts a's episodes that "
    "share at least one day with one of b's",
    "total": "daily constant-mix of the sleeves at the given weights (daily rebalancing)",
    "cagr": "calendar days / 365.25, as stored runs compute it",
    "time_under_water_days": "longest stretch in calendar days from a running peak to the day "
    "equity is back at it (or the window's end)",
    "trend": "benchmark close above/below its 200-day average, as of the prior close",
    "volatility": "benchmark 21-day realised volatility (annualised), terciles cut once over "
    "the shared days, as of the prior close",
}


def check_threshold(threshold: float) -> None:
    if not 0 < threshold < 1:
        raise ToolFailure(
            "invalid_request", "drawdown_threshold is a fraction in (0, 1).", fix="e.g. 0.1"
        )


def assign_weights(sleeves: list[Sleeve], weights: list[float] | None) -> dict[str, float]:
    """Normalised weights by run id (equal when none are given), written onto the sleeves."""
    if weights is None:
        weights = [1.0] * len(sleeves)
    if len(weights) != len(sleeves):
        raise ToolFailure(
            "invalid_weights",
            f"{len(weights)} weights for {len(sleeves)} sleeves.",
            fix="Give one positive weight per run, in the same order.",
        )
    try:
        normalised = pf.check_weights(
            {s.run_id: w for s, w in zip(sleeves, weights)}, [s.run_id for s in sleeves]
        )
    except (ValueError, TypeError) as exc:
        raise ToolFailure(
            "invalid_weights", str(exc), fix="Weights are positive numbers, normalised to one."
        ) from None
    for sleeve in sleeves:
        sleeve.weight = normalised[sleeve.run_id]
    return normalised


def align(
    sleeves: list[Sleeve], *, min_days: int
) -> tuple[pd.DataFrame, dict[str, int], dict[str, Any]]:
    """The sleeves' returns on their shared days, the days each lost, and the window."""
    frame, dropped = pf.aligned_returns({s.run_id: s.equity for s in sleeves})
    if len(frame) < min_days:
        raise ToolFailure(
            "window_too_short",
            f"The sleeves share only {len(frame)} days (fewer than {min_days}).",
            fix="Use sleeves whose stored curves overlap for longer.",
            details={
                "days": len(frame),
                "min_days": min_days,
                "per_sleeve": {s.run_id: sleeve_view(s, days=0, dropped=0) for s in sleeves},
            },
        )
    window = {
        "start": frame.index[0].date().isoformat(),
        "end": frame.index[-1].date().isoformat(),
        "days": int(len(frame)),
        "min_days": min_days,
    }
    return frame, dropped, window


def benchmark_of(
    sleeves: list[Sleeve], benchmark_symbol: str | None, catalog: str | None
) -> tuple[str, str]:
    """The catalog and symbol the regime labels come from: the first sleeve's by default."""
    first = sleeves[0].run
    found = catalog or run_catalog(first)
    if found is None:
        raise ToolFailure(
            "no_catalog",
            f"Run {sleeves[0].run_id} did not read a named catalog ({first.data_source}).",
            fix="Pass catalog (and benchmark_symbol) for the regime labels.",
        )
    return found, (benchmark_symbol or first.instrument_symbol).strip().upper()


def get_sleeve_correlation(
    store: JobStore,
    run_ids: list[str],
    *,
    weights: list[float] | None,
    drawdown_threshold: float,
    benchmark_symbol: str | None,
    catalog: str | None,
    max_sleeves: int,
    min_days: int,
) -> dict[str, Any]:
    """How the named runs move together, and how they split by regime side by side."""
    check_threshold(drawdown_threshold)
    with get_sync_session() as session:
        ledger.settle_pending(SyncResearchRepository(session), store)  # fresh runs attributed
        sleeves = load_sleeves(session, run_ids, minimum=2, maximum=max_sleeves)
        warnings = attribute(session, sleeves)
    normalised = assign_weights(sleeves, weights)
    frame, dropped, window = align(sleeves, min_days=min_days)
    total = pf.portfolio_returns(frame, normalised)
    matrix = pf.correlation(frame)
    found_catalog, symbol = benchmark_of(sleeves, benchmark_symbol, catalog)
    closes = benchmark_closes(found_catalog, symbol, frame.index[0], frame.index[-1])
    regimes = regime_table(frame, {"total": total}, closes)
    warnings += [f"regimes: {note}" for note in regimes["notes"]]
    return to_jsonable(
        {
            "sleeves": [
                sleeve_view(s, days=len(frame), dropped=dropped[s.run_id]) for s in sleeves
            ],
            "window": window,
            "weights": normalised,
            "correlation": {
                "method": "pearson",
                "basis": "simple daily returns on the shared days",
                "matrix": matrix,
                "pairs": [
                    {"a": a, "b": b, "correlation": matrix[a][b]}
                    for i, a in enumerate(frame.columns)
                    for b in list(frame.columns)[i + 1 :]
                ],
            },
            "drawdown_overlap": {
                "threshold": drawdown_threshold,
                "pairs": pf.drawdown_overlap(frame, threshold=drawdown_threshold),
            },
            "total": pf.summary(pf.combine(frame, normalised, initial=1.0), 1.0),
            "per_sleeve": {
                name: pf.summary(pf.combine(frame[[name]], {name: 1.0}, initial=1.0), 1.0)
                for name in frame.columns
            },
            "regimes": {"benchmark": symbol, "catalog": found_catalog, **regimes},
            "definitions": DEFINITIONS,
            "warnings": warnings,
        }
    )
