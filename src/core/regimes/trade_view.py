"""Trade view, alignment and cross-run correlation by regime bucket (design.md 5.7, 6).

Descriptive tables beside the daily view, which stays the reference (a trade entered in one
regime can exit in another). No verdict rule reads them in phase 2; the fixed verdicts are
phase 4.

- **Trade view**: closed trades bucketed by the label in force on their entry day (the same
  lag rule as the daily view); unlabelled entries in their own row; open trades counted apart.
  Win rate and profit factor are the stored metrics' definitions
  (``src.core.robustness.bootstrap``).
- **Alignment**: ``BUY`` under ``up`` or ``SELL`` under ``down`` is with-trend, the reverse is
  counter-trend, and a trade entered with no trend label is unclassified, never counted in
  either group.
- **Correlation**: Pearson of daily returns on the days every run shares, over the full window
  and inside each bucket; ``None`` where a run never moved, and per bucket ``None`` with
  ``insufficient`` under ``min_days`` shared days.
"""

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from src.core.robustness.bootstrap import profit_factor, win_rate

if TYPE_CHECKING:  # attribution imports this module
    from src.core.regimes.attribution import TradeRecord

UNLABELLED = "unlabelled"


def _round(value: float | None, places: int = 4) -> float | None:
    return None if value is None or not np.isfinite(value) else round(float(value), places)


def trade_metrics(pnls: list[float]) -> dict[str, Any]:
    """Trades, win rate, average win and loss, expectancy and profit factor."""
    values = np.asarray(pnls, dtype=float)
    wins, losses = values[values > 0], values[values < 0]
    return {
        "trades": int(len(values)),
        "win_rate": _round(win_rate(values)),
        "avg_win": _round(float(wins.mean()) if len(wins) else None, 2),
        "avg_loss": _round(float(losses.mean()) if len(losses) else None, 2),
        "expectancy": _round(float(values.mean()) if len(values) else None, 2),
        "profit_factor": _round(profit_factor(values)),
        "pnl": round(float(values.sum()), 2),
    }


def trade_view(
    trades: list["TradeRecord"], entry_buckets: list[str | None], order: tuple[str, ...]
) -> dict[str, Any]:
    """Closed trades per bucket of their entry day, plus the unlabelled row and open count."""
    closed = [(t, b) for t, b in zip(trades, entry_buckets) if t.exit is not None]
    rows = [
        {"bucket": bucket, **trade_metrics([t.pnl for t, b in closed if b == bucket])}
        for bucket in order
    ]
    unlabelled = {
        "bucket": UNLABELLED,
        **trade_metrics([t.pnl for t, b in closed if b not in order]),
    }
    return {
        "rows": rows,
        "unlabelled": unlabelled,
        "closed_trades": len(closed),
        "open_trades": len(trades) - len(closed),
    }


def _alignment_group(side: str, direction: str | None) -> str:
    if direction not in ("up", "down"):
        return "unclassified"
    long = side.upper() == "BUY"
    return "with_trend" if long == (direction == "up") else "counter_trend"


def alignment(trades: list["TradeRecord"], entry_directions: list[str | None]) -> list[dict]:
    """Trade-view metrics of closed with-trend, counter-trend and unclassified trades."""
    groups: dict[str, list[float]] = {"with_trend": [], "counter_trend": [], "unclassified": []}
    for trade, direction in zip(trades, entry_directions):
        if trade.exit is not None:
            groups[_alignment_group(trade.side, direction)].append(trade.pnl)
    return [{"group": name, **trade_metrics(pnls)} for name, pnls in groups.items()]


def _matrix(frame: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    names = list(frame.columns)
    values = frame.to_numpy(dtype=float)
    spread = values.std(axis=0) if len(values) > 1 else np.zeros(len(names))
    out: dict[str, dict[str, float | None]] = {n: {} for n in names}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if spread[i] == 0 or spread[j] == 0 or len(values) < 2:
                out[a][b] = None
            else:
                out[a][b] = _round(float(np.corrcoef(values[:, i], values[:, j])[0, 1]))
    return out


def correlation(
    returns: dict[str, pd.Series],
    buckets: dict[date, str | None],
    order: tuple[str, ...],
    min_days: int,
) -> dict[str, Any]:
    """Pearson matrices of daily returns on the shared days: overall and per bucket."""
    frame = pd.concat(returns, axis=1, join="inner").sort_index()
    labels = np.array([buckets.get(d) for d in frame.index], dtype=object)
    rows = []
    for bucket in order:
        inside = frame[labels == bucket]
        enough = len(inside) >= min_days
        rows.append(
            {
                "bucket": bucket,
                "days": int(len(inside)),
                "insufficient": not enough,
                "matrix": _matrix(inside) if enough else None,
            }
        )
    return {"shared_days": int(len(frame)), "overall": _matrix(frame), "buckets": rows}
