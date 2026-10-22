"""Monte Carlo over a run's closed trades: the range of paths its trades could have made (S4.1).

Each iteration re-orders (``reshuffle``) or resamples with replacement
(``bootstrap``) the run's trades and walks the equity path ``start + Σ pnl``.
A trade carries the days since the previous exit (``gap_days``), so every path
has a time base: a reshuffle keeps the run's span exactly, a bootstrap keeps it
on average, and CAGR and time under water are measured in days, not trades.
Drawdowns are negative fractions of the running peak, so the worst is the 5th
percentile. Serial correlation between trades is ignored (trade-level only).
"""

from dataclasses import dataclass
from typing import Any

import numpy as np

METHODS = ("bootstrap", "reshuffle")
PERCENTILES = (5, 50, 95)
CHUNK = 1000
DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class Trade:
    """One closed trade: its net P&L and the days since the previous exit (or the run start)."""

    pnl: float
    gap_days: float


def longest_runs(mask: np.ndarray) -> np.ndarray:
    """The longest stretch of consecutive True values in every row of ``mask``."""
    rows, _ = mask.shape
    padded = np.zeros((rows, mask.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    edges = np.diff(padded, axis=1)
    starts, ends = np.argwhere(edges == 1), np.argwhere(edges == -1)
    longest = np.zeros(rows, dtype=np.int64)
    np.maximum.at(longest, starts[:, 0], ends[:, 1] - starts[:, 1])
    return longest


def _days_under_water(under: np.ndarray, gaps: np.ndarray) -> np.ndarray:
    """The longest stretch of days spent below the running peak, per row.

    Trade ``k`` exits at ``elapsed[k + 1]``. A stretch under water starts at the
    exit of its first losing trade and ends at the exit of the trade that
    recovers the peak, or at the last exit when nothing does.
    """
    rows, n = under.shape
    padded = np.zeros((rows, n + 2), dtype=np.int8)
    padded[:, 1:-1] = under
    edges = np.diff(padded, axis=1)
    starts, ends = np.argwhere(edges == 1), np.argwhere(edges == -1)
    elapsed = np.concatenate((np.zeros((rows, 1)), np.cumsum(gaps, axis=1)), axis=1)
    recovered = np.minimum(ends[:, 1] + 1, n)
    days = elapsed[ends[:, 0], recovered] - elapsed[starts[:, 0], starts[:, 1] + 1]
    longest = np.zeros(rows)
    np.maximum.at(longest, starts[:, 0], days)
    return longest


def _indexes(rng: np.random.Generator, method: str, rows: int, n: int) -> np.ndarray:
    if method == "bootstrap":
        return rng.integers(0, n, size=(rows, n))
    return rng.permuted(np.tile(np.arange(n), (rows, 1)), axis=1)


def _statistics(pnls: np.ndarray, gaps: np.ndarray, start: float) -> dict[str, np.ndarray]:
    """Per-path statistics of ``rows`` resampled trade sequences."""
    equity = start + np.cumsum(pnls, axis=1)
    path = np.concatenate((np.full((pnls.shape[0], 1), float(start)), equity), axis=1)
    peaks = np.maximum.accumulate(path, axis=1)
    drawdown = np.min((path - peaks) / peaks, axis=1)
    final = path[:, -1]
    span = gaps.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        growth = np.where(final > 0, final / start, np.nan)
        cagr = np.where(final > 0, growth ** (DAYS_PER_YEAR / np.maximum(span, 1)) - 1, -1.0)
    return {
        "final_return": final / start - 1,
        "cagr": cagr,
        "max_drawdown": np.minimum(drawdown, 0.0),
        "longest_losing_streak": longest_runs(pnls <= 0).astype(float),
        "time_under_water_days": _days_under_water(equity < peaks[:, 1:], gaps),
    }


def _percentiles(values: np.ndarray) -> dict[str, float]:
    p5, p50, p95 = np.percentile(values, PERCENTILES)
    return {"p5": round(float(p5), 6), "p50": round(float(p50), 6), "p95": round(float(p95), 6)}


def simulate(
    trades: list[Trade],
    *,
    starting_balance: float,
    iterations: int,
    method: str,
    seed: int,
    breach: float | None = None,
) -> dict[str, Any]:
    """Percentiles of CAGR, drawdown, losing streak, time under water and final return.

    ``breach`` names a drawdown (a positive fraction, e.g. 0.2): the result's
    ``breach_probability`` is the share of paths whose max drawdown reached it.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, not {method!r}")
    if iterations < 1 or not trades or starting_balance <= 0:
        raise ValueError("needs at least one iteration, one trade and a positive balance")
    pnl = np.array([t.pnl for t in trades], dtype=float)
    gap = np.array([t.gap_days for t in trades], dtype=float)
    rng = np.random.default_rng(seed)
    collected: dict[str, list[np.ndarray]] = {}
    for offset in range(0, iterations, CHUNK):
        idx = _indexes(rng, method, min(CHUNK, iterations - offset), len(trades))
        for name, values in _statistics(pnl[idx], gap[idx], float(starting_balance)).items():
            collected.setdefault(name, []).append(values)
    stats = {name: np.concatenate(parts) for name, parts in collected.items()}
    result: dict[str, Any] = {
        "method": method,
        "iterations": iterations,
        "seed": seed,
        "trades": len(trades),
        "span_days": round(float(gap.sum()), 4),
        **{name: _percentiles(values) for name, values in stats.items()},
    }
    if breach is not None:
        result["breach"] = breach
        result["breach_probability"] = round(float(np.mean(stats["max_drawdown"] <= -breach)), 4)
    return result
