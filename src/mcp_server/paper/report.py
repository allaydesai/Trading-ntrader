"""One paper session read against its expectation band: the body of ``get_session``.

Takes an open database session, so the scorecard can judge G4 from the same
report inside its own transaction. Every input is a plain read.
"""

from datetime import datetime, timedelta
from math import ceil, floor
from typing import Any
from uuid import UUID

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from src.db.models.backtest import BacktestRun
from src.db.models.trading_session import TradingSession
from src.mcp_server.errors import ToolFailure
from src.mcp_server.paper.band import ClosedTrade, build_band, window_size, window_stats
from src.mcp_server.paper.drift import (
    MIN_TRADES_TO_JUDGE,
    SessionHealth,
    bar_seconds,
    classify,
    commission_flag,
    compare,
    health_flags,
)
from src.mcp_server.paper.reads import Link, closed_trades, link_of, to_utc, trade_rows
from src.mcp_server.paper.specs import model_normaliser, spec_differences
from src.mcp_server.paper.views import DEFINITIONS, link_view, session_view
from src.mcp_server.studies.checks import as_number

#: G4's horizon when the vault's gate sets none.
DEFAULT_WEEKS, DEFAULT_TRADES = 8, 20
MAX_WEEKS, MAX_TRADES = 520, 100_000
RESTARTED = (
    "The session was restarted: time it spent stopped counts as elapsed, and its runtime "
    "flags cover only the run since its last start."
)
NO_LINK_BAND = {
    "status": "unavailable",
    "note": "The session has no compare-to run: create it with --compare-to (paper_commands).",
}


def check_horizon(*, weeks: float | None, trades: int | None) -> None:
    """Refuse a band horizon no run could be windowed over."""
    if weeks is not None and not 0 < weeks <= MAX_WEEKS:
        raise ToolFailure(
            "invalid_request",
            f"weeks must be above 0 and at most {MAX_WEEKS}.",
            fix="Omit it, or pass a value in that range.",
        )
    if trades is not None and not 1 <= trades <= MAX_TRADES:
        raise ToolFailure(
            "invalid_request",
            f"trades must be between 1 and {MAX_TRADES}.",
            fix="Omit it, or pass a value in that range.",
        )


def band_for_run(
    session: Session, run_id: UUID | None, *, days: int, n: int, fit: bool = False
) -> dict[str, Any]:
    """The band from a run's closed trades over ``days``-long stretches and ``n``-trade runs.

    With ``fit``, ``n`` is capped so the run still gives enough windows to judge;
    the length used is the band's ``trade_windows.n``.
    """
    if run_id is None:
        return dict(NO_LINK_BAND)
    stmt = select(BacktestRun).options(noload(BacktestRun.trades))
    run = session.scalars(stmt.where(BacktestRun.run_id == run_id)).first()
    if run is None:
        return {"status": "unavailable", "note": f"Run {run_id} no longer exists."}
    trades = closed_trades(session, run_pk=run.id)
    if fit:
        n = window_size(n, run_trades=len(trades), floor=MIN_TRADES_TO_JUDGE)
    band = build_band(trades, span_end=to_utc(run.end_date) or run.end_date, days=days, n=n)
    return {"source_run": str(run_id), **band}


def _horizon_end(row: TradingSession, now: datetime) -> datetime | None:
    status = str(row.status)
    if status == "running":
        return now
    if status == "sealed":
        return to_utc(row.sealed_at or row.last_stopped_at)
    if status == "stopped":
        return to_utc(row.last_stopped_at)
    return None


def horizon(row: TradingSession, now: datetime, weeks: float | None) -> dict[str, Any]:
    """How long the session has existed as a started session, and the stretch its band covers.

    The clock runs from ``created_at``: the row keeps only its *last* start, which
    a restart overwrites, and ``live create`` is followed at once by ``live
    start``. Time spent stopped therefore counts; a session never started has
    run for no time. ``weeks`` narrows the stretch, never past the time elapsed.
    """
    last_start, last_stop = to_utc(row.last_started_at), to_utc(row.last_stopped_at)
    start = to_utc(row.created_at)
    end = (_horizon_end(row, now) if last_start else None) or start
    elapsed = max((end - start) / timedelta(days=1), 0.0) if start and end else 0.0
    days = max(1, ceil(elapsed))
    return {
        "start": start,
        "end": end,
        "elapsed_days": round(elapsed, 2),
        # Floored, so a minimum is never met early by rounding.
        "weeks": floor(elapsed / 7 * 100 + 1e-9) / 100,
        "band_days": max(1, min(round(weeks * 7), days)) if weeks else days,
        "source": "weeks argument" if weeks else "elapsed time",
        "includes_stopped_time": bool(last_start and last_stop and last_start > last_stop),
    }


def _paper_stats(trades: list[ClosedTrade], n: int) -> dict[str, Any]:
    """Every closed trade counted; rates and drawdown over the newest ``n``, as the band's are."""
    judged = trades[-n:] if 0 < n < len(trades) else trades
    commissions = [t.commission_frac for t in trades if t.commission_frac is not None]
    return {
        **window_stats(judged),
        "trades": len(trades),
        "judged_trades": len(judged),
        "median_commission_frac": float(np.median(commissions)) if commissions else None,
    }


def _spec_check(row: TradingSession, link: Link | None) -> dict[str, Any] | None:
    if link is None:
        return None
    c = link.candidate
    differences = spec_differences(
        row.spec,
        strategy=c.strategy_type,
        symbol=c.symbol,
        timeframe=c.timeframe,
        params=c.params,
        normalise=model_normaliser(c.strategy_type),
    )
    return {"matches": not differences, "differences": differences}


def session_health(row: TradingSession) -> SessionHealth:
    """What the session row says about whether it is running as it should."""
    strategies = (row.spec or {}).get("strategies") or [{}]
    first = (strategies[0] or {}).get("bar_types") or [""]
    return SessionHealth(
        status=str(row.status),
        last_started_at=to_utc(row.last_started_at),
        last_bar_at=to_utc(row.last_bar_at),
        last_heartbeat_at=to_utc(row.last_heartbeat_at),
        runtime_flags=row.runtime_flags,
        bar_seconds=bar_seconds(str(first[0])),
    )


def _flags(row, paper, band, spec_check, now) -> list[dict[str, str]]:
    flags = health_flags(session_health(row), now)
    if spec_check and not spec_check["matches"]:
        detail = "; ".join(spec_check["differences"])
        flags.append({"kind": "spec_mismatch", "cause": "config", "detail": detail})
    overall = band.get("overall") or {}
    cost = commission_flag(paper["median_commission_frac"], overall.get("median_commission_frac"))
    return flags + ([cost] if cost else [])


def g4_minimums(g4: dict[str, Any]) -> tuple[Any, Any, dict[str, str]]:
    """G4's ``min_weeks`` and ``min_trades``, a default standing in for one unset or unusable."""
    found, notes = {}, {}
    for key, default, unit in (
        ("min_weeks", DEFAULT_WEEKS, "weeks"),
        ("min_trades", DEFAULT_TRADES, "trades"),
    ):
        raw = g4.get(key)
        if as_number(raw) is not None:
            found[key] = raw
            continue
        found[key] = default
        why = f"G4's {key} {raw!r} is not a number" if key in g4 else f"G4 sets no {key}"
        notes[key] = f"{why}: {default} {unit} used."
    return found["min_weeks"], found["min_trades"], notes


def g4_progress(g4: dict[str, Any], weeks: float, trades: int) -> dict[str, Any]:
    """Where the session stands against G4's horizon: reached only when both minimums are met."""
    min_weeks, min_trades, notes = g4_minimums(g4)
    progress = {
        "min_weeks": min_weeks,
        "min_trades": min_trades,
        "weeks": weeks,
        "trades": trades,
        "reached": weeks >= float(min_weeks) and trades >= float(min_trades),
    }
    return {**progress, "notes": list(notes.values())} if notes else progress


def _warnings(row: TradingSession, link: Link | None, hz: dict[str, Any]) -> list[str]:
    warnings = []
    if len((row.spec or {}).get("strategies") or []) > 1:
        warnings.append("multi_strategy_trades_not_attributable: trades carry no strategy id.")
    if link and not link.is_latest_out_of_sample:
        warnings.append("The compare-to run is not the candidate's newest out-of-sample run.")
    if link and not link.candidate_is_current:
        warnings.append("The study has moved to a newer candidate version since this one.")
    if hz["includes_stopped_time"]:
        warnings.append(RESTARTED)
    return warnings


def session_report(
    session: Session,
    row: TradingSession,
    *,
    g4: dict[str, Any],
    now: datetime,
    weeks: float | None = None,
    recent: int | None = 20,
) -> dict[str, Any]:
    """The session, its band, the comparison, the drift flags and its G4 progress."""
    trades = closed_trades(session, session_pk=row.id)
    link = link_of(session, row.linked_backtest_run_id)
    hz = horizon(row, now, weeks)
    wanted = max(len(trades), MIN_TRADES_TO_JUDGE)
    band = band_for_run(
        session, row.linked_backtest_run_id, days=hz["band_days"], n=wanted, fit=True
    )
    paper = _paper_stats(trades, (band.get("trade_windows") or {}).get("n") or len(trades))
    # The trade count is compared over the same stretch the band's count covers.
    since = hz["end"] - timedelta(days=hz["band_days"])
    counted = sum(1 for t in trades if t.exit_at > since)
    if counted != len(trades):
        paper["trades_in_window"] = counted
    rows = compare({**paper, "trades": counted}, band)
    spec_check = _spec_check(row, link)
    drift = classify(rows, _flags(row, paper, band, spec_check, now), trades=len(trades))
    return {
        "session": session_view(row),
        "link": link_view(row.linked_backtest_run_id, link),
        "spec_check": spec_check,
        "horizon": hz,
        "paper": paper,
        "band": band,
        "comparison": rows,
        "drift": drift,
        "g4": g4_progress(g4, hz["weeks"], len(trades)),
        "recent_trades": trade_rows(session, row.id, recent) if recent != 0 else [],
        "definitions": DEFINITIONS,
        "warnings": _warnings(row, link, hz),
    }
