"""One paper session read against its expectation band: the body of ``get_session``.

Takes an open database session, so the scorecard can judge G4 from the same
report inside its own transaction. Every input is a plain read.
"""

from datetime import datetime, timedelta
from math import ceil
from typing import Any
from uuid import UUID

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from src.db.models.backtest import BacktestRun
from src.db.models.trading_session import TradingSession
from src.mcp_server.paper.band import ClosedTrade, build_band, window_stats
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

NO_LINK_BAND = {
    "status": "unavailable",
    "note": "The session has no compare-to run: create it with --compare-to (paper_commands).",
}


def band_for_run(session: Session, run_id: UUID | None, *, days: int, n: int) -> dict[str, Any]:
    """The band from a run's closed trades over ``days``-long stretches and ``n``-trade runs."""
    if run_id is None:
        return dict(NO_LINK_BAND)
    stmt = select(BacktestRun).options(noload(BacktestRun.trades))
    run = session.scalars(stmt.where(BacktestRun.run_id == run_id)).first()
    if run is None:
        return {"status": "unavailable", "note": f"Run {run_id} no longer exists."}
    trades = closed_trades(session, run_pk=run.id)
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


def horizon(
    row: TradingSession, trades: list[ClosedTrade], now: datetime, weeks: float | None
) -> dict[str, Any]:
    """How long the session has traded, and the stretch its band is built over."""
    first_entry = min((t.entry_at for t in trades), default=None)
    last_start = to_utc(row.last_started_at)
    starts = [d for d in (last_start, first_entry) if d is not None]
    start = min(starts) if starts else to_utc(row.created_at)
    end = _horizon_end(row, now) or start
    elapsed = max((end - start) / timedelta(days=1), 0.0) if start and end else 0.0
    restarted = first_entry is not None and last_start is not None and first_entry < last_start
    return {
        "start": start,
        "end": end,
        "elapsed_days": round(elapsed, 2),
        "weeks": round(elapsed / 7, 2),
        "band_days": max(1, round(weeks * 7)) if weeks else max(1, ceil(elapsed)),
        "source": "weeks argument" if weeks else "elapsed time",
        "includes_stopped_time": restarted,
    }


def _paper_stats(trades: list[ClosedTrade]) -> dict[str, Any]:
    commissions = [t.commission_frac for t in trades if t.commission_frac is not None]
    median = float(np.median(commissions)) if commissions else None
    return {**window_stats(trades), "median_commission_frac": median}


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


def _g4(g4: dict[str, Any], weeks: float, trades: int) -> dict[str, Any]:
    min_weeks, min_trades = g4.get("min_weeks"), g4.get("min_trades")
    reached = (min_weeks is None or weeks >= float(min_weeks)) and (
        min_trades is None or trades >= int(min_trades)
    )
    return {
        "min_weeks": min_weeks,
        "min_trades": min_trades,
        "weeks": weeks,
        "trades": trades,
        "reached": reached and bool(g4),
    }


def _warnings(row: TradingSession, link: Link | None, hz: dict[str, Any]) -> list[str]:
    warnings = []
    if len((row.spec or {}).get("strategies") or []) > 1:
        warnings.append("multi_strategy_trades_not_attributable: trades carry no strategy id.")
    if link and not link.is_latest_out_of_sample:
        warnings.append("The compare-to run is not the candidate's newest out-of-sample run.")
    if link and not link.candidate_is_current:
        warnings.append("The study has moved to a newer candidate version since this one.")
    if hz["includes_stopped_time"]:
        warnings.append("The session was restarted: time it spent stopped counts as elapsed.")
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
    hz = horizon(row, trades, now, weeks)
    paper = _paper_stats(trades)
    n = max(len(trades), MIN_TRADES_TO_JUDGE)
    band = band_for_run(session, row.linked_backtest_run_id, days=hz["band_days"], n=n)
    rows = compare(paper, band)
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
        "g4": _g4(g4, hz["weeks"], len(trades)),
        "recent_trades": trade_rows(session, row.id, recent) if recent != 0 else [],
        "definitions": DEFINITIONS,
        "warnings": _warnings(row, link, hz),
    }
