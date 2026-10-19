"""Constant-mix portfolio over stored daily equity curves (S9.2–S9.5, research MCP phase 5a).

Each sleeve is a stored run's daily mark-to-market equity. Sleeves are joined on
the dates they share, weighted in *returns* (so dollar sizes need not match) and
held at constant weights, which in returns space is daily rebalancing:
``R_t = Σ w_i r_{i,t}`` and ``equity_t = initial · Π (1 + R_s)``. No shared
capital, no exposure limits, no instrument overlap: that is the engine's job in
a later phase. Everything here is pure pandas; non-finite values leave as None.
"""

import math
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from src.core.mark_to_market import risk_metrics

DAYS_PER_YEAR = 365.25  # calendar-day CAGR, as stored runs compute it
METRIC_KEYS = (
    "total_return",
    "cagr",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "volatility",
    "max_drawdown",
    "time_under_water_days",
    "days",
)
BASIS = (
    "daily constant-mix of the sleeves' stored mark-to-market curves on the dates they share: "
    "weights apply to returns, not dollars, and are held constant (daily rebalancing); no "
    "shared capital or exposure limits"
)


def _finite(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def daily_returns(equity: pd.Series) -> pd.Series:
    """Close-to-close returns, one per UTC date (the MCP's ``regimes.daily_returns`` twin)."""
    days = pd.DatetimeIndex(pd.to_datetime(list(equity.index), utc=True)).normalize()
    daily = equity.groupby(days).last()
    daily.index = pd.DatetimeIndex(daily.index)
    return daily.pct_change().dropna()


def aligned_returns(curves: Mapping[str, pd.Series]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Every sleeve's daily returns on the dates all of them share, and the days each lost."""
    if len(curves) < 2:
        raise ValueError("a portfolio needs at least two sleeves")
    returns = {name: daily_returns(curve) for name, curve in curves.items()}
    frame = pd.concat(returns, axis=1, join="inner").dropna()
    frame.columns = list(returns)
    dropped = {name: int(len(r) - len(frame)) for name, r in returns.items()}
    return frame, dropped


def check_weights(weights: Mapping[str, float], columns: Iterable[str]) -> dict[str, float]:
    """Positive, finite weights for exactly ``columns``, normalised to sum to one."""
    names = list(columns)
    if set(weights) != set(names):
        raise ValueError("weights must name every sleeve once")
    values = {name: float(weights[name]) for name in names}
    if any(not math.isfinite(w) or w <= 0 for w in values.values()):
        raise ValueError("weights must be positive numbers")
    total = sum(values.values())
    return {name: w / total for name, w in values.items()}


def portfolio_returns(returns: pd.DataFrame, weights: Mapping[str, float]) -> pd.Series:
    """``R_t = Σ w_i r_{i,t}`` with the weights as given (normalise first)."""
    weighted = sum(returns[name] * float(w) for name, w in weights.items())
    return pd.Series(weighted, index=returns.index, name="returns")


def combine(returns: pd.DataFrame, weights: Mapping[str, float], *, initial: float) -> pd.Series:
    """The constant-mix equity curve from ``initial`` over the shared dates."""
    daily = portfolio_returns(returns, check_weights(weights, returns.columns))
    path = np.cumprod(1.0 + daily.to_numpy(dtype=float))
    return pd.Series(float(initial) * path, index=returns.index, name="equity")


def correlation(returns: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    """Pearson correlation of simple daily returns; a flat sleeve correlates with nothing."""
    matrix = returns.corr()
    return {a: {b: _finite(matrix.loc[a, b]) for b in returns.columns} for a in returns.columns}


def _episodes(below: np.ndarray) -> list[tuple[int, int]]:
    """``[start, end)`` of every maximal run of True."""
    padded = np.concatenate(([0], below.astype(np.int8), [0]))
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _below(returns: pd.Series, threshold: float) -> np.ndarray:
    path = np.cumprod(1.0 + returns.to_numpy(dtype=float))
    peak = np.maximum.accumulate(np.concatenate(([1.0], path)))[1:]
    return path / peak - 1.0 <= -threshold


def drawdown_overlap(returns: pd.DataFrame, *, threshold: float) -> list[dict[str, Any]]:
    """Per pair: shared drawdown days (deeper than ``threshold``) and overlapping episodes."""
    below = {name: _below(returns[name], threshold) for name in returns.columns}
    pairs = []
    names = list(returns.columns)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            both, either = below[a] & below[b], below[a] | below[b]
            runs_a, runs_b = _episodes(below[a]), _episodes(below[b])
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "both_below": round(float(both.mean()), 6),
                    "jaccard": round(float(both.sum() / either.sum()), 6) if either.any() else None,
                    "episodes": {
                        "a": len(runs_a),
                        "b": len(runs_b),
                        "a_with_b": sum(1 for s, e in runs_a if below[b][s:e].any()),
                        "b_with_a": sum(1 for s, e in runs_b if below[a][s:e].any()),
                    },
                }
            )
    return pairs


def _shares(weighted: Mapping[str, float]) -> dict[str, dict[str, float | None]]:
    total = sum(weighted.values())
    return {
        name: {"weighted_return": round(v, 6), "share": _finite(v / total) if total else None}
        for name, v in weighted.items()
    }


def contribution(
    returns: pd.DataFrame, weights: Mapping[str, float], equity: pd.Series
) -> dict[str, Any]:
    """Each sleeve's arithmetic share of the portfolio's return and of its worst drawdown."""
    weighted = returns.mul(pd.Series(weights), axis=1)
    values = equity.to_numpy(dtype=float)
    initial = values[0] / (1.0 + float(portfolio_returns(returns, weights).iloc[0]))
    path = np.concatenate(([initial], values))
    drawdown = path / np.maximum.accumulate(path) - 1.0
    trough = int(drawdown.argmin())
    peak = int(path[: trough + 1].argmax())
    window = weighted.iloc[peak:trough]  # returns k ↔ path k + 1
    return {
        "return": _shares(weighted.sum().to_dict()),
        "drawdown": {
            "peak": equity.index[peak - 1].date().isoformat() if peak else None,
            "trough": equity.index[trough - 1].date().isoformat() if trough else None,
            "portfolio_drawdown": round(float(drawdown[trough]), 6),
            "by_sleeve": _shares(window.sum().to_dict()) if trough else {},
        },
    }


def _time_under_water_days(equity: pd.Series, initial: float) -> int:
    path = np.concatenate(([initial], equity.to_numpy(dtype=float)))
    under = path[1:] < np.maximum.accumulate(path)[1:]
    longest = 0
    for start, end in _episodes(under):
        since = equity.index[start - 1] if start else equity.index[0]
        until = equity.index[end] if end < len(equity) else equity.index[-1]
        longest = max(longest, int((until - since).days))
    return longest


def summary(equity: pd.Series, initial: float) -> dict[str, Any]:
    """Return, CAGR (calendar days / 365.25, like stored runs), risk ratios and drawdown."""
    risk = risk_metrics(equity, initial)
    last = float(equity.iloc[-1])
    days = int((equity.index[-1] - equity.index[0]).days)
    cagr = (last / initial) ** (DAYS_PER_YEAR / days) - 1 if last > 0 and days > 0 else None
    drawdown = risk.max_drawdown
    trough = int((equity / equity.cummax() - 1.0).to_numpy().argmin())
    return {
        "total_return": _finite(last / initial - 1),
        "cagr": _finite(cagr) if cagr is not None else (0.0 if last == initial else None),
        "sharpe_ratio": risk.sharpe_ratio,
        "sortino_ratio": risk.sortino_ratio,
        "calmar_ratio": _finite(cagr / abs(drawdown)) if cagr and drawdown else None,
        "volatility": risk.volatility,
        "max_drawdown": drawdown,
        "max_drawdown_at": equity.index[trough].date().isoformat() if drawdown else None,
        "time_under_water_days": _time_under_water_days(equity, initial),
        "days": int(len(equity)),
    }


def delta(without: Mapping[str, Any], with_: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per metric: the value without and with the candidate, and the difference."""
    found = {}
    for key in METRIC_KEYS:
        before, after = without.get(key), with_.get(key)
        change = after - before if before is not None and after is not None else None
        found[key] = {"without": before, "with": after, "delta": change}
    return found


def evaluate(
    returns: pd.DataFrame, weights: Mapping[str, float], *, initial: float
) -> dict[str, Any]:
    """One portfolio: its normalised weights, equity curve, metrics and contributions."""
    normalised = check_weights(weights, returns.columns)
    equity = combine(returns, normalised, initial=initial)
    return {
        "weights": normalised,
        "equity": equity,
        "metrics": summary(equity, initial),
        "contribution": contribution(returns, normalised, equity),
    }
