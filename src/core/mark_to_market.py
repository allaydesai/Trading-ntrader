"""Mark-to-market equity and the risk metrics computed from it.

Nautilus' portfolio analyzer builds its returns from *realised* position
returns, so a position held through a deep drawdown and closed at the end shows
no drawdown and no volatility. Here equity is rebuilt from the run's fills and
bar closes instead: cash moves with every fill, holdings are valued at the
latest close, and the result is reduced to one value per UTC day.

Pure pandas, no Nautilus imports: the extractor adapts engine objects to
``Fill`` and ``(ts_ns, close)`` pairs.
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class Fill:
    """One execution. ``signed_qty`` is positive for a buy, negative for a sell."""

    ts_ns: int
    instrument_id: str
    signed_qty: float
    price: float
    commission: float
    multiplier: float = 1.0


@dataclass(frozen=True)
class RiskMetrics:
    """Risk metrics of a daily equity series; None where there is too little data."""

    max_drawdown: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    volatility: float | None = None


def _stepped(values: dict[int, float], index: np.ndarray) -> pd.Series:
    """Running total of ``values`` (keyed by timestamp) at every point of ``index``."""
    if not values:
        return pd.Series(0.0, index=index)
    return pd.Series(values).reindex(index, fill_value=0.0).cumsum()


def daily_equity(
    starting_balance: float,
    fills: list[Fill],
    closes_by_instrument: dict[str, list[tuple[int, float]]],
) -> pd.Series:
    """Account equity at the last bar of each UTC day.

    Raises:
        ValueError: A fill names an instrument that has no bars to value it with.
    """
    unpriced = {f.instrument_id for f in fills} - {k for k, v in closes_by_instrument.items() if v}
    if unpriced:
        raise ValueError(f"Fills for instruments with no bars: {', '.join(sorted(unpriced))}")
    stamps = [ts for closes in closes_by_instrument.values() for ts, _ in closes]
    stamps += [f.ts_ns for f in fills]
    if not stamps:
        return pd.Series(dtype=float)
    index = np.unique(np.array(stamps, dtype=np.int64))

    cash_moves: dict[int, float] = {}
    quantity_moves: dict[str, dict[int, float]] = {}
    multipliers: dict[str, float] = {}
    for f in fills:
        spent = f.signed_qty * f.price * f.multiplier + f.commission
        cash_moves[f.ts_ns] = cash_moves.get(f.ts_ns, 0.0) - spent
        moves = quantity_moves.setdefault(f.instrument_id, {})
        moves[f.ts_ns] = moves.get(f.ts_ns, 0.0) + f.signed_qty
        multipliers[f.instrument_id] = f.multiplier

    equity = starting_balance + _stepped(cash_moves, index)
    for instrument_id, moves in quantity_moves.items():
        prices = pd.Series(dict(closes_by_instrument[instrument_id])).reindex(index).ffill().bfill()
        equity = equity + _stepped(moves, index) * prices * multipliers[instrument_id]

    days = pd.to_datetime(index, unit="ns", utc=True).normalize()
    return equity.groupby(days).last()


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def risk_metrics(equity: pd.Series, starting_balance: float) -> RiskMetrics:
    """Max drawdown, Sharpe, Sortino and volatility of a daily equity series.

    Returns are day-over-day, the first measured from ``starting_balance``, and
    annualised on 252 days with the same formulas as Nautilus' statistics.
    """
    if equity.empty or starting_balance <= 0:
        return RiskMetrics()
    values = np.concatenate(([starting_balance], equity.to_numpy(dtype=float)))
    peaks = np.maximum.accumulate(values)
    max_drawdown = min(float(((values - peaks) / peaks).min()), 0.0)

    returns = values[1:] / values[:-1] - 1
    deviation = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    if not math.isfinite(deviation) or deviation == 0.0:
        return RiskMetrics(max_drawdown=max_drawdown)
    mean = float(returns.mean())
    annual = math.sqrt(TRADING_DAYS)
    downside = math.sqrt(float((returns[returns < 0] ** 2).sum()) / len(returns))
    return RiskMetrics(
        max_drawdown=max_drawdown,
        sharpe_ratio=_finite(mean / deviation * annual),
        sortino_ratio=_finite(mean / downside * annual) if downside else None,
        volatility=_finite(deviation * annual),
    )
