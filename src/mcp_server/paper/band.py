"""The expectation band: the range a run's own trades put on a stretch of paper trading (S6.2).

Pure. Built from closed trades only, because a paper session stores nothing
else. Every statistic is computed by the same function for the backtest's
windows and for the paper session, so the two are always measured alike:

- a trade's return is its net P&L over its entry notional, so a session that
  trades a different size from the backtest is still comparable;
- time windows (every H-day stretch, stepped daily) give the trade count;
- trade windows (every N consecutive trades) give win rate, average trade and
  drawdown, whose spread depends on how many trades there are, not on time.

Trade drawdown is the worst fall of the running *sum* of trade returns from its
peak: the strategies trade a fixed size, so returns add rather than compound.
"""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np

#: Fewer windows than this make a band too thin to call a result outside it.
MIN_WINDOWS = 20
BASIS = (
    "Rolling windows over the linked backtest run's own closed trades (one run, "
    "overlapping windows); Monte Carlo ranges arrive in phase 4."
)
WEEK = timedelta(days=7)


@dataclass(frozen=True)
class ClosedTrade:
    """One closed position, reduced to what the band and the paper comparison use."""

    entry_at: datetime
    exit_at: datetime
    ret: float
    pnl: float
    commission_frac: float | None


def closed_trade(
    *,
    entry_at: datetime,
    exit_at: datetime | None,
    quantity: Decimal,
    entry_price: Decimal,
    profit_loss: Decimal | None,
    commission: Decimal | None,
) -> ClosedTrade | None:
    """A stored trade row as a ``ClosedTrade``, or None while it is open or unpriced."""
    if exit_at is None or profit_loss is None:
        return None
    notional = float(quantity) * float(entry_price)
    if notional <= 0:
        return None
    return ClosedTrade(
        entry_at=entry_at,
        exit_at=exit_at,
        ret=float(profit_loss) / notional,
        pnl=float(profit_loss),
        commission_frac=None if commission is None else float(commission) / notional,
    )


def trade_drawdown(returns: list[float]) -> float | None:
    """The worst fall of the summed returns from their running peak (zero or negative)."""
    if not returns:
        return None
    path = np.concatenate(([0.0], np.cumsum(returns)))
    return float(np.min(path - np.maximum.accumulate(path)))


def window_stats(trades: list[ClosedTrade]) -> dict[str, Any]:
    """Trades, win rate, average return and P&L, and drawdown of a run of trades."""
    if not trades:
        return {
            "trades": 0,
            "win_rate": None,
            "avg_trade_return": None,
            "avg_trade_pnl": None,
            "trade_drawdown": None,
        }
    returns = [t.ret for t in trades]
    return {
        "trades": len(trades),
        "win_rate": sum(1 for t in trades if t.pnl > 0) / len(trades),
        "avg_trade_return": float(np.mean(returns)),
        "avg_trade_pnl": float(np.mean([t.pnl for t in trades])),
        "trade_drawdown": trade_drawdown(returns),
    }


def time_window_counts(
    exits: list[datetime], *, start: datetime, end: datetime, days: int
) -> list[int]:
    """Exits inside every ``[s, s + days)`` that fits in ``[start, end]``, s stepped daily."""
    ordered = sorted(exits)
    length = timedelta(days=days)
    counts, s = [], start
    while s + length <= end:
        counts.append(bisect_left(ordered, s + length) - bisect_left(ordered, s))
        s += timedelta(days=1)
    return counts


def trade_window_stats(trades: list[ClosedTrade], n: int) -> list[dict[str, Any]]:
    """``window_stats`` of every run of ``n`` consecutive trades, with the weeks it took."""
    ordered = sorted(trades, key=lambda t: t.exit_at)
    windows = []
    for i in range(len(ordered) - n + 1):
        chunk = ordered[i : i + n]
        weeks = (chunk[-1].exit_at - min(t.entry_at for t in chunk)) / WEEK
        windows.append({**window_stats(chunk), "weeks": weeks})
    return windows


def pct(values: list[float | None]) -> dict[str, float] | None:
    """The 5th, 50th and 95th percentiles, or None with nothing to rank."""
    present = [float(v) for v in values if v is not None]
    if not present:
        return None
    p5, p50, p95 = np.percentile(present, [5, 50, 95])
    return {"p5": round(float(p5), 6), "p50": round(float(p50), 6), "p95": round(float(p95), 6)}


def _trade_axis(trades: list[ClosedTrade], n: int) -> dict[str, Any]:
    windows = trade_window_stats(trades, n) if n > 0 else []
    return {
        "n": n,
        "count": len(windows),
        **{
            key: pct([w[key] for w in windows])
            for key in ("win_rate", "avg_trade_return", "trade_drawdown")
        },
        "weeks_to_n_trades": pct([w["weeks"] for w in windows]),
    }


def _overall(trades: list[ClosedTrade], start: datetime | None, end: datetime) -> dict[str, Any]:
    stats = window_stats(trades)
    weeks = (end - start) / WEEK if start is not None and end > start else None
    commissions = [t.commission_frac for t in trades if t.commission_frac is not None]
    return {
        **stats,
        "trades_per_week": round(len(trades) / weeks, 4) if weeks else None,
        "median_commission_frac": float(np.median(commissions)) if commissions else None,
    }


def build_band(
    trades: list[ClosedTrade], *, span_end: datetime, days: int, n: int
) -> dict[str, Any]:
    """The band over ``days``-long stretches and ``n``-trade runs of a run's closed trades.

    The span starts at the first trade's entry, not at the run's start: a
    strategy that warms up for months would otherwise look like it trades less.
    """
    start = min((t.entry_at for t in trades), default=None)
    counts = (
        time_window_counts([t.exit_at for t in trades], start=start, end=span_end, days=days)
        if start is not None and days > 0
        else []
    )
    trade_axis = _trade_axis(trades, n)
    if not trades:
        status, note = "unavailable", "The run has no closed trades."
    elif min(len(counts), trade_axis["count"]) < MIN_WINDOWS:
        status = "thin"
        note = (
            f"Only {len(counts)} time windows and {trade_axis['count']} trade windows "
            f"(fewer than {MIN_WINDOWS}): treat a result outside the band as a hint, not a flag."
        )
    else:
        status, note = "ok", ""
    band = {
        "status": status,
        "basis": BASIS,
        "span": {"start": start, "end": span_end},
        "time_windows": {"days": days, "count": len(counts), "trades": pct(list(counts))},
        "trade_windows": trade_axis,
        "overall": _overall(trades, start, span_end),
    }
    return {**band, "note": note} if note else band
