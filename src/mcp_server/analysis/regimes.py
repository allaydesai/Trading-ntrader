"""Results split by calendar year, trend regime, volatility regime and sub-period (S4.3).

Pure: computed from a run's stored daily equity, its trades and a benchmark's
daily closes. Every split shows, per cell, the days in it, the compounded
return of the run's daily returns on those days, the drawdown of that
compounded path, the trades entered in it, their win rate and their P&L.

Regimes are known before the day they label: the trend (close above or below
its 200-day average) and the volatility (21-day realised, annualised) are taken
from the previous close, so a day's return is attributed to the regime the
strategy could have seen. Volatility terciles are cut over the run's own days.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

SMA_DAYS = 200
VOL_DAYS = 21
TRADING_DAYS = 252
WARMUP_CALENDAR_DAYS = 300  # enough history before a run for a 200-day average
UNKNOWN = "unknown"


@dataclass(frozen=True)
class TradePoint:
    """What the splits need of a trade: when it was entered, whether it closed, its P&L."""

    entry: pd.Timestamp
    closed: bool
    pnl: float


def _days(index: Any) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(list(index), utc=True)).normalize()


def daily_returns(equity: pd.Series) -> pd.Series:
    """Close-to-close returns of the equity curve, one per UTC date."""
    daily = equity.groupby(_days(equity.index)).last()
    daily.index = pd.DatetimeIndex(daily.index)
    return daily.pct_change().dropna()


def _compounded_drawdown(values: np.ndarray) -> float:
    if not len(values):
        return 0.0
    path = np.cumprod(1.0 + values)
    peak = np.maximum.accumulate(np.concatenate(([1.0], path)))[1:]
    return float((path / peak - 1.0).min())


def _cell(label: str, returns: pd.Series, trades: list[TradePoint]) -> dict[str, Any]:
    values = returns.to_numpy(dtype=float)
    closed = [t for t in trades if t.closed]
    wins = sum(1 for t in closed if t.pnl > 0)
    total = float(np.prod(1.0 + values) - 1.0) if len(values) else 0.0
    return {
        "label": label,
        "days": int(len(values)),
        "return": round(total, 6),
        "max_drawdown": round(_compounded_drawdown(values), 6),
        "trades": len(trades),
        "win_rate": round(wins / len(closed), 4) if closed else None,
        "pnl": round(sum(t.pnl for t in closed), 2),
        "losing": total < 0,
    }


def _label_at(labels: pd.Series, days: pd.DatetimeIndex) -> list[str]:
    """The label in force on each day: the latest one dated on or before it."""
    if labels.empty:
        return [UNKNOWN] * len(days)
    found = labels.sort_index().reindex(days, method="ffill")
    return [UNKNOWN if pd.isna(v) else str(v) for v in found]


def _split(
    returns: pd.Series, trades: list[TradePoint], labels: pd.Series, order: list[str]
) -> list[dict[str, Any]]:
    """One cell per label in ``order`` that has days or trades."""
    day_labels = np.array(_label_at(labels, pd.DatetimeIndex(returns.index)), dtype=object)
    entry_labels = _label_at(labels, _days([t.entry for t in trades]))
    cells = []
    for label in order:
        in_cell = [t for t, lab in zip(trades, entry_labels) if lab == label]
        cell_returns = returns[day_labels == label]
        if len(cell_returns) or in_cell:
            cells.append(_cell(label, cell_returns, in_cell))
    return cells


def by_year(returns: pd.Series, trades: list[TradePoint]) -> list[dict[str, Any]]:
    """One cell per calendar year."""
    days = pd.DatetimeIndex(returns.index).union(_days([t.entry for t in trades]))
    labels = pd.Series([str(d.year) for d in days], index=days)
    return _split(returns, trades, labels, sorted(set(labels)))


def _previous_close(values: pd.Series) -> pd.Series:
    """Each date carries the value known at the prior close."""
    return values.shift(1)


def trend_labels(closes: pd.Series) -> pd.Series:
    """``above_sma200`` / ``below_sma200`` per date, from the previous close."""
    sma = closes.rolling(SMA_DAYS).mean()
    raw = np.where(closes.to_numpy() > sma.to_numpy(), "above_sma200", "below_sma200")
    labels = pd.Series(raw, index=closes.index, dtype=object)
    labels[sma.isna().to_numpy()] = UNKNOWN
    return _previous_close(labels).fillna(UNKNOWN)


def volatility_labels(closes: pd.Series, run_days: pd.DatetimeIndex) -> pd.Series:
    """``low`` / ``mid`` / ``high`` realised-volatility tercile per date, cut over the run."""
    log_returns = pd.Series(np.log(closes.to_numpy(dtype=float)), index=closes.index).diff()
    vol = _previous_close(log_returns.rolling(VOL_DAYS).std() * np.sqrt(TRADING_DAYS))
    in_run = vol.sort_index().reindex(run_days, method="ffill").dropna()
    if in_run.empty:
        return pd.Series(UNKNOWN, index=vol.index, dtype=object)
    low, high = (float(q) for q in in_run.quantile([1 / 3, 2 / 3]))
    values = vol.to_numpy(dtype=float)
    raw = np.where(values <= low, "low", np.where(values <= high, "mid", "high"))
    labels = pd.Series(raw, index=vol.index, dtype=object)
    labels[np.isnan(values)] = UNKNOWN
    return labels


def sub_periods(returns: pd.Series, trades: list[TradePoint], count: int) -> list[dict[str, Any]]:
    """``count`` consecutive periods with equal numbers of days."""
    if returns.empty:
        return []
    index = pd.DatetimeIndex(returns.index)
    names: list[str] = []
    starts: list[pd.Timestamp] = []
    for chunk in np.array_split(np.arange(len(index)), count):
        if len(chunk):
            starts.append(index[chunk[0]])
            names.append(f"{index[chunk[0]].date()}..{index[chunk[-1]].date()}")
    entries = _days([t.entry for t in trades])
    if len(entries):  # a trade entered on the first day, before the first return
        starts[0] = min(starts[0], entries.min())
    labels = pd.Series(names, index=pd.DatetimeIndex(starts))
    return _split(returns, trades, labels, names)


def breakdown(
    equity: pd.Series, trades: list[TradePoint], closes: pd.Series, *, periods: int = 4
) -> dict[str, Any]:
    """Every split for one run; ``closes`` should start before the run for warm-up."""
    returns = daily_returns(equity)
    run_days = pd.DatetimeIndex(returns.index)
    trend = trend_labels(closes)
    notes = []
    if UNKNOWN in _label_at(trend, run_days):
        notes.append(
            f"Some days have no {SMA_DAYS}-day average yet (too little history); "
            "they are labelled unknown."
        )
    still_open = sum(1 for t in trades if not t.closed)
    if still_open:
        notes.append(
            f"{still_open} trade(s) still open at the end: counted in each cell's trades, but "
            "not in the run's total_trades, nor in any win_rate or pnl."
        )
    return {
        "trades": {"total": len(trades), "closed": len(trades) - still_open, "open": still_open},
        "by_year": by_year(returns, trades),
        "trend": _split(returns, trades, trend, ["above_sma200", "below_sma200", UNKNOWN]),
        "volatility": _split(
            returns, trades, volatility_labels(closes, run_days), ["low", "mid", "high", UNKNOWN]
        ),
        "sub_periods": sub_periods(returns, trades, periods),
        "notes": notes,
    }
