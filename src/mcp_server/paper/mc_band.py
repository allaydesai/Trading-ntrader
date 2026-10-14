"""The expectation band's trade axis by Monte Carlo (phase 4, S6.2).

The rolling band (``band.py``) measures every run of ``n`` consecutive trades;
a run of 60 trades gives 41 overlapping windows and a run of 25 gives six,
too few to rank. Here each window is instead ``n`` trades drawn with
replacement from the run's closed trades, ``iterations`` times, so the
percentiles come from thousands of windows whatever the run's length. The
statistics per window are the rolling band's own (``window_stats``'
definitions), so a paper session is still measured the same way. The time
axis stays rolling: a count of exits in every stretch of ``days``.

Pure. Reads nothing.
"""

from datetime import datetime
from typing import Any

import numpy as np

from src.mcp_server.paper.band import (
    MIN_WINDOWS,
    WEEK,
    ClosedTrade,
    _overall,
    pct,
    time_window_counts,
)

ITERATIONS = 2000
BASIS = (
    "Trade windows are Monte Carlo: n trades drawn with replacement from the linked backtest "
    "run's closed trades, repeated; time windows are every stretch of the horizon's length "
    "over the run, stepped daily. Statistics are the same as for the paper session."
)


def _gaps(trades: list[ClosedTrade]) -> np.ndarray:
    """Days from each trade's exit to the previous exit (the first from its own entry)."""
    ordered = sorted(trades, key=lambda t: t.exit_at)
    previous = [ordered[0].entry_at, *(t.exit_at for t in ordered[:-1])]
    return np.array([(t.exit_at - p) / WEEK * 7 for t, p in zip(ordered, previous)], dtype=float)


def resampled_windows(
    trades: list[ClosedTrade], n: int, *, iterations: int, seed: int
) -> dict[str, np.ndarray]:
    """Per resampled window of ``n`` trades: win rate, average return, drawdown, weeks."""
    rng = np.random.default_rng(seed)
    ordered = sorted(trades, key=lambda t: t.exit_at)
    ret = np.array([t.ret for t in ordered], dtype=float)
    pnl = np.array([t.pnl for t in ordered], dtype=float)
    gaps = _gaps(ordered)
    idx = rng.integers(0, len(ordered), size=(iterations, n))
    rets = ret[idx]
    path = np.concatenate((np.zeros((iterations, 1)), np.cumsum(rets, axis=1)), axis=1)
    return {
        "win_rate": np.mean(pnl[idx] > 0, axis=1),
        "avg_trade_return": rets.mean(axis=1),
        "trade_drawdown": np.min(path - np.maximum.accumulate(path, axis=1), axis=1),
        "weeks": gaps[idx].sum(axis=1) / 7,
    }


def _trade_axis(trades: list[ClosedTrade], n: int, *, iterations: int, seed: int) -> dict:
    if not trades or n <= 0:
        return {
            "method": "monte_carlo",
            "n": n,
            "count": 0,
            "distinct_trades": len(trades),
            "win_rate": None,
            "avg_trade_return": None,
            "trade_drawdown": None,
            "weeks_to_n_trades": None,
        }
    windows = resampled_windows(trades, n, iterations=iterations, seed=seed)
    return {
        "method": "monte_carlo",
        "n": n,
        "count": iterations,
        "distinct_trades": len(trades),
        "seed": seed,
        **{k: pct(list(windows[k])) for k in ("win_rate", "avg_trade_return", "trade_drawdown")},
        "weeks_to_n_trades": pct(list(windows["weeks"])),
    }


def build_mc_band(
    trades: list[ClosedTrade],
    *,
    span_end: datetime,
    days: int,
    n: int,
    iterations: int = ITERATIONS,
    seed: int = 0,
) -> dict[str, Any]:
    """The band with a Monte Carlo trade axis; same keys as ``band.build_band``.

    Thin when the run has fewer than ``MIN_WINDOWS`` closed trades (resampling
    cannot invent variety) or the time axis has fewer than that many stretches.
    """
    start = min((t.entry_at for t in trades), default=None)
    counts = (
        time_window_counts([t.exit_at for t in trades], start=start, end=span_end, days=days)
        if start is not None and days > 0
        else []
    )
    trade_axis = _trade_axis(trades, n, iterations=iterations, seed=seed)
    if not trades:
        status, note = "unavailable", "The run has no closed trades."
    elif min(len(counts), len(trades)) < MIN_WINDOWS:
        status = "thin"
        note = (
            f"Only {len(counts)} time windows and {len(trades)} closed trades behind the trade "
            f"windows (fewer than {MIN_WINDOWS}): treat a result outside the band as a hint, "
            "not a flag."
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
