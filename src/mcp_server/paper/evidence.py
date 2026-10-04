"""What the scorecard takes from the paper loop: the expectation band and G4's facts (S6.1, S6.2).

The band is built from the candidate's newest completed out-of-sample run, at
the horizon G4 asks for, so it exists before the first paper trade. G4 is
judged on the newest session linked to any of the candidate's out-of-sample
runs; older ones are listed, never chosen instead.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from src.mcp_server.paper.reads import oos_runs, sessions_linked_to
from src.mcp_server.paper.report import band_for_run, session_report
from src.mcp_server.studies.checks import PaperFacts

DEFAULT_WEEKS, DEFAULT_TRADES = 8, 20
JUDGED = ("win_rate", "avg_trade_return")


def expectation_band(
    session: Session,
    oos_run_id: UUID | None,
    g4: dict[str, Any],
    *,
    weeks: float | None = None,
    trades: int | None = None,
) -> dict[str, Any]:
    """The band a paper session of this candidate should land in over G4's horizon."""
    notes = []
    if weeks is None:
        weeks = g4.get("min_weeks")
        if weeks is None:
            weeks, notes = DEFAULT_WEEKS, [f"G4 sets no min_weeks: {DEFAULT_WEEKS} weeks used."]
    if trades is None:
        trades = g4.get("min_trades")
        if trades is None:
            trades = DEFAULT_TRADES
            notes.append(f"G4 sets no min_trades: {DEFAULT_TRADES} trades used.")
    if oos_run_id is None:
        return {
            "status": "missing",
            "note": "Built from the candidate's out-of-sample run: run_out_of_sample first.",
        }
    band = band_for_run(session, oos_run_id, days=max(1, round(float(weeks) * 7)), n=int(trades))
    horizon = {"weeks": float(weeks), "trades": int(trades)}
    return {**band, "horizon": horizon, **({"notes": notes} if notes else {})}


def paper_facts(
    session: Session, candidate_pk: int, g4: dict[str, Any], *, now: datetime
) -> PaperFacts | None:
    """G4's facts from the newest session compared against one of the candidate's holdout runs."""
    linked = sessions_linked_to(session, oos_runs(session, candidate_pk))
    if not linked:
        return None
    report = session_report(session, linked[0], g4=g4, now=now, recent=0)
    progress = report["g4"]
    return PaperFacts(
        session=linked[0].name,
        weeks=float(progress["weeks"]),
        trades=int(progress["trades"]),
        reached=bool(progress["reached"]),
        positions={key: report["comparison"][key]["position"] for key in JUDGED},
        problems=[
            f["kind"] for f in report["drift"]["flags"] if f["cause"] != "strategy_or_regime"
        ],
        others=[row.name for row in linked[1:]],
    )
