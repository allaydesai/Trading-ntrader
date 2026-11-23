"""Mark-to-market equity and the risk metrics computed from it.

Nautilus' portfolio analyzer builds its returns from *realised* position
returns, so a position held through a deep drawdown and closed at the end shows
no drawdown and no volatility. Here equity is rebuilt from the run's fills and
bar closes instead: cash moves with every fill, holdings are valued at the
latest close, and the result is reduced to one value per UTC day.

Spot FX (M3) has its own rebuild, :func:`fx_daily_equity`: the account is USD but P&L
accrues in the quote currency, so each fill's realised P&L is booked at the rate the account
converted that fill at, and open positions are marked at the latest rate. Its days end at the
17:00 New York cutoff (``src/services/fx/sessions.py``), not at UTC midnight.

Pure pandas, no Nautilus imports: the extractor adapts engine objects to
``Fill`` and ``(ts_ns, close)`` pairs.
"""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.services.fx.sessions import fx_trade_days

TRADING_DAYS = 252


@dataclass(frozen=True)
class Fill:
    """One execution. ``signed_qty`` is positive for a buy, negative for a sell.

    FX only: ``quote_rate`` is the USD per quote-currency unit the account converted the fill
    at (1.0 for a USD quote) and ``position_id`` the position it belongs to (HEDGING venues
    hold several per pair). The equity rebuild ignores both.
    """

    ts_ns: int
    instrument_id: str
    signed_qty: float
    price: float
    commission: float
    multiplier: float = 1.0
    quote_rate: float = 1.0
    position_id: str = ""


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


def _unpriced(fills: list[Fill], closes_by_instrument: dict[str, list[tuple[int, float]]]) -> None:
    unpriced = {f.instrument_id for f in fills} - {k for k, v in closes_by_instrument.items() if v}
    if unpriced:
        raise ValueError(f"Fills for instruments with no bars: {', '.join(sorted(unpriced))}")


def _fx_moves(fills: list[Fill]) -> tuple[dict[int, float], dict[str, dict], dict[str, dict]]:
    """USD cash moves, and per instrument the quantity and open-cost moves, by timestamp.

    Average cost per position, as Nautilus' ``Position``: a reducing fill realises
    ``closed x (price - average) x multiplier`` in the quote currency, booked in USD at the
    fill's ``quote_rate``; a fill past flat opens the remainder at its price.
    """
    usd: dict[int, float] = {}
    qty_moves: dict[str, dict[int, float]] = {}
    cost_moves: dict[str, dict[int, float]] = {}
    held: dict[str, tuple[float, float]] = {}  # position -> (signed quantity, average price)
    for f in sorted(fills, key=lambda fill: fill.ts_ns):
        key = f.position_id or f.instrument_id
        quantity, average = held.get(key, (0.0, 0.0))
        cost = 0.0
        realised = 0.0
        if quantity == 0.0 or (quantity > 0) == (f.signed_qty > 0):
            new_quantity = quantity + f.signed_qty
            average = (quantity * average + f.signed_qty * f.price) / new_quantity
            cost = f.signed_qty * f.price
        else:
            closed = math.copysign(min(abs(f.signed_qty), abs(quantity)), quantity)
            realised = closed * (f.price - average) * f.multiplier
            cost = -closed * average
            new_quantity = quantity - closed
            rest = f.signed_qty + closed
            if abs(rest) > 1e-9:
                new_quantity, average = rest, f.price
                cost += rest * f.price
        held[key] = (new_quantity, average if abs(new_quantity) > 1e-9 else 0.0)
        usd[f.ts_ns] = usd.get(f.ts_ns, 0.0) + realised * f.quote_rate - f.commission
        moves = qty_moves.setdefault(f.instrument_id, {})
        moves[f.ts_ns] = moves.get(f.ts_ns, 0.0) + f.signed_qty
        costs = cost_moves.setdefault(f.instrument_id, {})
        costs[f.ts_ns] = costs.get(f.ts_ns, 0.0) + cost * f.multiplier
    return usd, qty_moves, cost_moves


def fx_daily_equity(
    starting_balance: float,
    fills: list[Fill],
    closes_by_instrument: dict[str, list[tuple[int, float]]],
    rates_by_instrument: dict[str, list[tuple[int, float]]],
) -> pd.Series:
    """USD account equity of a spot FX run at the last bar of each FX trade date.

    ``rates_by_instrument`` gives, per pair settling outside USD, ``(ts_ns, USD per quote
    unit)``; a pair without an entry is USD-quoted (rate 1). Open positions are worth their
    unrealised quote P&L at the latest rate; realised P&L stays at its fill's rate.

    Raises:
        ValueError: A fill names an instrument that has no bars to value it with.
    """
    _unpriced(fills, closes_by_instrument)
    stamps = [ts for closes in closes_by_instrument.values() for ts, _ in closes]
    stamps += [f.ts_ns for f in fills]
    if not stamps:
        return pd.Series(dtype=float)
    index = np.unique(np.array(stamps, dtype=np.int64))
    usd, qty_moves, cost_moves = _fx_moves(fills)
    multipliers = {f.instrument_id: f.multiplier for f in fills}
    equity = starting_balance + _stepped(usd, index)
    for instrument_id, moves in qty_moves.items():
        prices = pd.Series(dict(closes_by_instrument[instrument_id])).reindex(index).ffill().bfill()
        rates = pd.Series(1.0, index=index)
        if rates_by_instrument.get(instrument_id):
            # forward-fill over every rate observation, then sample: never a future rate.
            # Before the first rate nothing can be held (a fill needs one), so 0 is inert.
            points = pd.Series(dict(rates_by_instrument[instrument_id])).sort_index()
            union = points.index.union(pd.Index(index))
            rates = points.reindex(union).ffill().reindex(index).fillna(0.0)
        held = _stepped(moves, index) * prices * multipliers[instrument_id]
        equity = equity + (held - _stepped(cost_moves[instrument_id], index)) * rates
    return equity.groupby(fx_trade_days(index)).last()


def daily_equity(
    starting_balance: float,
    fills: list[Fill],
    closes_by_instrument: dict[str, list[tuple[int, float]]],
) -> pd.Series:
    """Account equity at the last bar of each UTC day.

    Raises:
        ValueError: A fill names an instrument that has no bars to value it with.
    """
    _unpriced(fills, closes_by_instrument)
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
