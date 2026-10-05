"""What the scorecard takes from the paper loop: the expectation band and G4's facts (S6.1, S6.2).

The band is built from the candidate's newest completed out-of-sample run, at
the horizon G4 asks for, so it exists before the first paper trade. G4 is
judged on the newest *started* session linked to any of the candidate's
out-of-sample runs; the others are listed, never chosen instead.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from src.mcp_server.paper.band import MIN_WINDOWS
from src.mcp_server.paper.drift import MIN_TRADES_TO_JUDGE
from src.mcp_server.paper.reads import oos_runs, sessions_linked_to
from src.mcp_server.paper.report import band_for_run, g4_minimums, session_report
from src.mcp_server.studies.checks import PaperFacts

#: What ``paper_inside_band`` judges; the trade count is judged by ``paper_clean``.
JUDGED = ("win_rate", "avg_trade_return", "trade_drawdown")
COUNT_FLAGS = {"fewer_trades", "extra_trades"}
OWN_HORIZON = (
    "G4 is judged on the session's own horizon (the time it has run and the trades it has "
    "closed; see get_session), not on this one."
)


def expectation_band(
    session: Session,
    oos_run_id: UUID | None,
    g4: dict[str, Any],
    *,
    weeks: float | None = None,
    trades: int | None = None,
) -> dict[str, Any]:
    """The band a paper session of this candidate should land in over G4's horizon."""
    min_weeks, min_trades, defaulted = g4_minimums(g4)
    notes = [OWN_HORIZON]
    if weeks is None:
        weeks = min_weeks
        notes += [defaulted["min_weeks"]] if "min_weeks" in defaulted else []
    if trades is None:
        trades = min_trades
        notes += [defaulted["min_trades"]] if "min_trades" in defaulted else []
    if oos_run_id is None:
        return {
            "status": "missing",
            "note": "Built from the candidate's out-of-sample run: run_out_of_sample first.",
        }
    band = band_for_run(session, oos_run_id, days=max(1, round(float(weeks) * 7)), n=int(trades))
    horizon = {"weeks": float(weeks), "trades": int(trades)}
    return {**band, "horizon": horizon, "notes": notes}


def _thin(band: dict[str, Any], axis: str) -> bool:
    """The band has too few windows on one axis (``time_windows`` or ``trade_windows``)."""
    return int((band.get(axis) or {}).get("count") or 0) < MIN_WINDOWS


def _unjudgeable(report: dict[str, Any]) -> str:
    """Why the band cannot judge this session's results, or nothing when it can."""
    if report["g4"]["trades"] < MIN_TRADES_TO_JUDGE:
        return f"Fewer than {MIN_TRADES_TO_JUDGE} closed trades: too few to judge."
    band = report["band"]
    if not _thin(band, "trade_windows"):
        return ""
    return band.get("note") or f"The expectation band is {band.get('status')}."


def paper_facts(
    session: Session, candidate_pk: int, g4: dict[str, Any], *, now: datetime
) -> PaperFacts | None:
    """G4's facts from the newest *started* session compared against the candidate's holdout runs.

    A session created and never started says nothing yet, so it never displaces
    one that has run. A band too thin on an axis fails nothing on that axis:
    too few trade windows leave the results unjudged, too few time windows keep
    a trade-count flag out of the problems.
    """
    linked = sessions_linked_to(session, oos_runs(session, candidate_pk))
    row = next((r for r in linked if r.last_started_at is not None), None)
    if row is None:
        return None
    report = session_report(session, row, g4=g4, now=now, recent=0)
    progress = report["g4"]
    count_unjudgeable = _thin(report["band"], "time_windows")
    return PaperFacts(
        session=row.name,
        weeks=float(progress["weeks"]),
        trades=int(progress["trades"]),
        reached=bool(progress["reached"]),
        positions={key: report["comparison"][key]["position"] for key in JUDGED},
        problems=[
            f["kind"]
            for f in report["drift"]["flags"]
            if f["cause"] != "strategy_or_regime"
            and not (count_unjudgeable and f["kind"] in COUNT_FLAGS)
        ],
        others=[r.name for r in linked if r is not row],
        unjudgeable=_unjudgeable(report),
        restarted=bool(report["horizon"]["includes_stopped_time"]),
    )
