"""Sleeves: stored runs as components of a portfolio (S9.2–S9.5, research MCP phase 5a).

A sleeve is any stored run with a mark-to-market daily curve. Loading refuses
what cannot be combined (no curve, another metrics basis); attribution only
*warns* when a sleeve is not a frozen candidate's out-of-sample run, because
nothing has been admitted to the portfolio yet and the comparison is still
worth having. The regime table lines several return series up on one benchmark
so the same days carry the same label in every column.
"""

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from src.db.models.research import ResearchStudy
from src.db.repositories.equity_curve_repository import SyncEquityCurveRepository
from src.db.repositories.research_repository import SyncResearchRepository
from src.mcp_server.analysis.bars import daily_closes
from src.mcp_server.analysis.regimes import (
    SMA_DAYS,
    UNKNOWN,
    WARMUP_CALENDAR_DAYS,
    return_cells,
    trend_labels,
    volatility_labels,
)
from src.mcp_server.analysis.run_reads import load_run
from src.mcp_server.analysis.series import equity_series
from src.mcp_server.errors import ToolFailure
from src.mcp_server.runs import parse_run_ids

MARK_TO_MARKET = "mark_to_market"
OUT_OF_SAMPLE = "out_of_sample"


@dataclass
class Sleeve:
    """One stored run as a portfolio component: its curve, weight and ledger attribution."""

    run_id: str
    run: Any
    equity: pd.Series
    weight: float = 0.0
    study: str | None = None
    role: str | None = None
    version: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def base(self) -> float:
        return float(self.run.initial_capital)


def usable_curve(session: Session, run: Any) -> pd.Series | None:
    """The run's stored daily equity when it is mark-to-market; otherwise nothing."""
    points = SyncEquityCurveRepository(session).find_points(run.run_id)
    if not points or (run.config_snapshot or {}).get("metrics_basis") != MARK_TO_MARKET:
        return None
    return equity_series(points)


def load_sleeves(
    session: Session, run_ids: list[str], *, minimum: int, maximum: int
) -> list[Sleeve]:
    """Each named run with its usable curve, in the order given; refusals name the fix."""
    canonical = parse_run_ids(run_ids, minimum=0, maximum=1_000_000)
    if len(canonical) < len(run_ids):
        raise ToolFailure("duplicate_sleeve", "A run is listed twice.", fix="List each run once.")
    if len(canonical) < minimum:
        raise ToolFailure(
            "too_few_sleeves", f"Name at least {minimum} runs.", fix="Add run ids (search_runs)."
        )
    if len(canonical) > maximum:
        raise ToolFailure(
            "too_many_sleeves",
            f"At most {maximum} sleeves at once (NTRADER_MCP_MAX_SLEEVES).",
            fix="Combine fewer runs.",
        )
    sleeves = []
    for run_id in canonical:
        run = load_run(session, run_id)
        equity = usable_curve(session, run)
        if equity is None:
            basis = (run.config_snapshot or {}).get("metrics_basis")
            raise ToolFailure(
                "curve_missing",
                f"Run {run_id} has no stored mark-to-market daily curve.",
                fix="reproduce_run makes a new run of the same config with its curve stored.",
                details={"run_id": run_id, "metrics_basis": basis},
            )
        sleeves.append(Sleeve(run_id=run_id, run=run, equity=equity))
    return sleeves


def attribute(session: Session, sleeves: list[Sleeve]) -> list[str]:
    """Name each sleeve's study and ledger role, warn about weak evidence; shared warnings back."""
    repo = SyncResearchRepository(session)
    trials = {str(t.run_id): t for t in repo.trials_of_runs([UUID(s.run_id) for s in sleeves])}
    for sleeve in sleeves:
        trial = trials.get(sleeve.run_id)
        if trial is None:
            sleeve.warnings.append(f"{sleeve.run_id}: unattributed run, not out-of-sample evidence")
        else:
            study = session.get(ResearchStudy, trial.study_pk)
            sleeve.study = study.slug if study is not None else None
            sleeve.role, sleeve.version = trial.role, trial.version
            if trial.role != OUT_OF_SAMPLE:
                sleeve.warnings.append(
                    f"{sleeve.run_id}: {trial.role} run of '{sleeve.study}', not a frozen "
                    "candidate's out-of-sample run"
                )
        if sleeve.run.git_dirty:
            sleeve.warnings.append(f"{sleeve.run_id}: ran from a dirty working tree")
    balances = sorted({s.base for s in sleeves})
    if len(balances) > 1:
        shown = ", ".join(f"{b:,.0f}" for b in balances)
        return [f"Starting balances differ ({shown}): weights apply to returns, not dollars."]
    return []


def sleeve_view(sleeve: Sleeve, *, days: int, dropped: int) -> dict[str, Any]:
    run = sleeve.run
    return {
        "run_id": sleeve.run_id,
        "strategy": run.strategy_name,
        "symbol": run.instrument_symbol,
        "study": sleeve.study,
        "role": sleeve.role,
        "version": sleeve.version,
        "start": sleeve.equity.index[0].date().isoformat(),
        "end": sleeve.equity.index[-1].date().isoformat(),
        "days": days,
        "days_dropped": dropped,
        "starting_balance": sleeve.base,
        "weight": round(sleeve.weight, 6),
        "warnings": list(sleeve.warnings),
    }


def benchmark_closes(
    catalog: str, symbol: str, start: pd.Timestamp, end: pd.Timestamp
) -> pd.Series:
    """The benchmark's daily closes with warm-up for the 200-day average."""
    closes = daily_closes(catalog, symbol, start - timedelta(days=WARMUP_CALENDAR_DAYS), end)
    if closes.empty:
        raise ToolFailure(
            "no_benchmark_bars",
            f"No daily bars for {symbol} in '{catalog}' for this window.",
            fix="Pass benchmark_symbol with daily bars (catalog_availability).",
        )
    return closes


def _rows(frame: pd.DataFrame, labels: pd.Series, order: list[str]) -> list[dict[str, Any]]:
    cells = {
        col: {c["label"]: c for c in return_cells(frame[col], labels, order)}
        for col in frame.columns
    }
    rows = []
    for label in order:
        found = {col: cells[col].get(label) for col in frame.columns}
        present = [c for c in found.values() if c is not None]
        if not present:
            continue
        rows.append(
            {
                "label": label,
                "days": present[0]["days"],
                "return": {col: c["return"] if c else None for col, c in found.items()},
                "max_drawdown": {col: c["max_drawdown"] if c else None for col, c in found.items()},
            }
        )
    return rows


def regime_table(
    returns: pd.DataFrame, totals: dict[str, pd.Series], closes: pd.Series
) -> dict[str, Any]:
    """By year, trend and volatility tercile: one row per label, one value per column.

    The tercile cut is made once over the shared days, so every column is judged on
    the same regime labels.
    """
    frame = returns.assign(**totals)
    days = pd.DatetimeIndex(frame.index)
    years = pd.Series([str(d.year) for d in days], index=days)
    splits = {
        "by_year": (years, sorted(set(years))),
        "trend": (trend_labels(closes), ["above_sma200", "below_sma200", UNKNOWN]),
        "volatility": (volatility_labels(closes, days), ["low", "mid", "high", UNKNOWN]),
    }
    table: dict[str, Any] = {
        name: _rows(frame, labels, order) for name, (labels, order) in splits.items()
    }
    table["notes"] = []
    if any(row["label"] == UNKNOWN for row in table["trend"]):
        table["notes"].append(
            f"Some days have no {SMA_DAYS}-day average yet (too little history); "
            "they are labelled unknown."
        )
    return table
