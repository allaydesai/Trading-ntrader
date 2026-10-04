"""Paper sessions as plain JSON for tool results."""

from typing import Any
from uuid import UUID

from src.db.models.trading_session import TradingSession
from src.mcp_server.paper.reads import Link, to_utc

DEFINITIONS = {
    "trade_return": "Net P&L over entry notional (price x quantity): independent of trade size.",
    "win_rate": "Share of closed trades with P&L above zero (a fraction).",
    "trade_drawdown": "Worst fall of the running sum of trade returns from its peak.",
    "band": "5th / 50th / 95th percentiles over the compare-to run's rolling windows: trade "
    "count over stretches as long as the session has run, the rest over runs of as many "
    "trades as it has closed.",
    "runtime_flags": "Rejections, failures and lost connections cover the session's current "
    "run only: NTrader clears them on every start.",
}


def link_view(run_id: UUID | None, link: Link | None) -> dict[str, Any]:
    """The compare-to run and, when it was a study's holdout test, that study and candidate."""
    if link is None:
        return {"compare_to": run_id, "study": None}
    return {
        "compare_to": run_id,
        "study": link.study.slug,
        "study_status": link.study.status,
        "contaminated": link.study.contaminated,
        "version": link.candidate.version,
        "candidate_id": link.candidate.candidate_id,
        "is_latest_out_of_sample": link.is_latest_out_of_sample,
        "candidate_is_current": link.candidate_is_current,
    }


def session_view(row: TradingSession) -> dict[str, Any]:
    """The session row, without its internal key or fencing token."""
    return {
        "name": row.name,
        "session_id": row.session_id,
        "status": str(row.status),
        "strategies": (row.spec or {}).get("strategies") or [],
        "created_at": to_utc(row.created_at),
        "last_started_at": to_utc(row.last_started_at),
        "last_stopped_at": to_utc(row.last_stopped_at),
        "sealed_at": to_utc(row.sealed_at),
        "sealed_run_id": row.sealed_run_id,
        "last_heartbeat_at": to_utc(row.last_heartbeat_at),
        "last_bar_at": to_utc(row.last_bar_at),
        "runtime_flags": row.runtime_flags,
    }


def summary_view(
    row: TradingSession, link: Link | None, closed: int, weeks: float, alive: bool | None
) -> dict[str, Any]:
    """One line of ``list_sessions``: identity, state, progress and link, no band."""
    strategies = (row.spec or {}).get("strategies") or [{}]
    first = strategies[0] or {}
    return {
        "name": row.name,
        "session_id": row.session_id,
        "status": str(row.status),
        "alive": alive,
        "strategy": first.get("strategy_id"),
        "bar_types": first.get("bar_types"),
        "created_at": to_utc(row.created_at),
        "last_started_at": to_utc(row.last_started_at),
        "last_bar_at": to_utc(row.last_bar_at),
        "closed_trades": closed,
        "weeks_elapsed": weeks,
        "compare_to": row.linked_backtest_run_id,
        "study": link.study.slug if link else None,
        "version": link.candidate.version if link else None,
    }
