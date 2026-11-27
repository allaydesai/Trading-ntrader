"""Regime profile of one run: daily view against stored calendar rows (design.md 2, 5.1-5.6).

Pure, no DB. Inputs are the run's stored daily mark-to-market equity, its trades, the
calendar rows read for one version (as of one build), and the reference's daily closes.
Nothing here computes a label: labels come from the calendar only.

- **Daily P&L**: ``pnl(d) = E(d) - E(d-1)`` with ``E(-1)`` the initial capital, and
  ``r(d) = pnl(d) / E(d-1)``. Every family's rows, ``unlabelled`` included, sum to
  ``E(last) - initial`` exactly.
- **Alignment**: ground truth takes the label dated exactly *d* (lag 0, no other lag is
  allowed). Detectors take the label of the *k*-th reference session strictly before *d*
  (*k* = 0: the session on or before *d*), so a weekend carries the earlier label. A day after
  the version's ``data_end`` is unlabelled, never filled from an older label.
- **Exposure**: a position carried in from the previous close (entered on an earlier date,
  not exited before *d*), or opened and closed within *d*.
- **Drift**: the reference's return on the bucket's days, times the side the strategy held
  into each day (+1 long, -1 short, 0 flat), annualised. A bucket whose return is no higher is
  market drift, not edge (the spec's drift rule, signed so it also reads short positions).

This module may read ground-truth rows (it is named in
``tests/unit/governance/test_regime_ground_truth_boundary.py``) but imports nothing from
``ground_truth``: the labels arrive as data.
"""

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from src.core.regimes.bootstrap import bucket_intervals, dependence_outcome
from src.core.regimes.definition import SCALES
from src.core.regimes.trade_view import UNLABELLED, alignment, trade_view
from src.core.robustness.bootstrap import TRADING_DAYS, sharpe

MIN_DAYS = 60
MIN_TRADES = 20
_CENT = 0.005
#: Each label family: its key in the label payload, and the bucket order.
GROUND_TRUTH_FAMILIES: dict[str, tuple[str, ...]] = {
    **{f"leg_{scale}": ("up", "down") for scale in SCALES},
    "ranging": ("ranging", "not_ranging"),
    "volatility": ("high", "low"),
}
DETECTOR_FAMILIES: dict[str, tuple[str, ...]] = {"sma200": ("up", "down")}
#: Families whose buckets are trend directions: they get the with/counter-trend split.
TREND_FAMILIES = (*(f"leg_{scale}" for scale in SCALES), "sma200")
CROSS_BUCKETS = ("up/high", "up/low", "down/high", "down/low")


@dataclass(frozen=True)
class TradeRecord:
    """What the profile needs of a stored trade (``order_side`` is the entry side)."""

    entry: datetime
    exit: datetime | None
    side: str
    pnl: float


@dataclass(frozen=True)
class Calendar:
    """One version's rows as read: session dates in order, their payloads, the as-of end."""

    label_set: str
    sessions: list[date]
    labels: list[dict[str, Any]]
    data_end: date


def daily_frame(equity: pd.Series, initial_capital: float) -> pd.DataFrame:
    """One row per UTC date: the last equity point, its P&L and its return."""
    index = pd.DatetimeIndex(equity.index)
    by_day = equity.groupby(index.tz_convert("UTC").date).last().sort_index()
    previous = by_day.shift(1).fillna(initial_capital)
    frame = pd.DataFrame({"equity": by_day.astype(float)})
    frame["pnl"] = frame["equity"] - previous
    frame["ret"] = frame["pnl"] / previous
    frame.index = pd.Index(list(by_day.index))
    return frame


def bucket_name(key: str, value: Any) -> str | None:
    """The bucket a label value falls in (``ranging`` true/false → ranging / not_ranging)."""
    if value is None:
        return None
    if key == "ranging":
        return "ranging" if value else "not_ranging"
    return str(value)


def labels_in_force(days: list[date], calendar: Calendar, key: str, *, lag: int) -> list[Any]:
    """The ``key`` value in force on each day under the lag rule (design.md section 2)."""
    if calendar.label_set == "ground_truth":
        if lag != 0:
            raise ValueError("ground-truth labels take no lag (lag_not_allowed)")
        by_date = {d: row.get(key) for d, row in zip(calendar.sessions, calendar.labels)}
        return [by_date.get(day) for day in days]
    if lag < 0:
        raise ValueError("lag must be zero or positive")
    sessions = calendar.sessions
    out: list[Any] = []
    for day in days:
        if day > calendar.data_end:
            out.append(None)
            continue
        position = bisect_right(sessions, day) - 1 if lag == 0 else bisect_left(sessions, day) - lag
        out.append(calendar.labels[position].get(key) if position >= 0 else None)
    return out


def _utc_date(stamp: datetime) -> date:
    return pd.Timestamp(stamp).tz_convert("UTC").date()


def _positions(
    days: list[date], trades: list[TradeRecord]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(carried, inside, sign)``: day x trade masks of positions carried in from the
    previous close and of round trips opened and closed within the day, and each side."""
    day = np.array(days, dtype="datetime64[D]")[:, None]
    entry = np.array([_utc_date(t.entry) for t in trades], dtype="datetime64[D]")[None, :]
    far = np.datetime64("9999-12-31")
    exits = [far if t.exit is None else np.datetime64(_utc_date(t.exit)) for t in trades]
    exit_ = np.array(exits, dtype="datetime64[D]")[None, :]
    carried = (entry < day) & (exit_ >= day)
    inside = (entry == day) & (exit_ == day)
    sign = np.array([1 if t.side.upper() == "BUY" else -1 for t in trades])[None, :]
    return carried, inside, sign


def carried_sides(days: list[date], trades: list[TradeRecord]) -> np.ndarray:
    """Per day: +1 long, -1 short, 0 flat, from the positions carried in from the previous
    close (the ones that earn the day's close-to-close move). A same-day round trip never
    changes it, so it can never cancel a carried position."""
    if not trades or not days:
        return np.zeros(len(days), dtype=int)
    carried, _, sign = _positions(days, trades)
    return np.sign((carried * sign).sum(axis=1)).astype(int)


def exposed_days(days: list[date], trades: list[TradeRecord]) -> np.ndarray:
    """Per day: whether any position was carried in, or opened and closed within the day."""
    if not trades or not days:
        return np.zeros(len(days), dtype=bool)
    carried, inside, _ = _positions(days, trades)
    return (carried | inside).any(axis=1)


def benchmark_returns(days: list[date], closes: pd.Series) -> np.ndarray:
    """The reference's close-to-close return on each day that is one of its sessions."""
    sessions = closes.sort_index()
    previous = sessions.shift(1)
    change = (sessions / previous - 1.0).to_dict()
    return np.array([change.get(day, np.nan) for day in days], dtype=float)


def _rounded(value: float | None, places: int = 6) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), places)


def _metrics(
    pnl: np.ndarray, ret: np.ndarray, bench: np.ndarray, sides: np.ndarray, exposed: np.ndarray
) -> dict:
    n = len(ret)
    finite_bench = bench[np.isfinite(bench)]
    return {
        "days": int(n),
        "pnl": round(float(pnl.sum()), 2),
        "ann_return": _rounded(float(ret.mean()) * TRADING_DAYS if n else None),
        "ann_volatility": _rounded(
            float(np.std(ret, ddof=1)) * math.sqrt(TRADING_DAYS) if n > 1 else None
        ),
        "sharpe": _rounded(sharpe(ret)),
        "worst_day": _rounded(float(ret.min()) if n else None),
        "mean_daily_return": _rounded(float(ret.mean()) if n else None, 8),
        "exposure": _rounded(float(np.mean(exposed)) if n else None, 4),
        "benchmark_ann_return": _rounded(
            float(finite_bench.mean()) * TRADING_DAYS if len(finite_bench) else None
        ),
        "drift_return": _rounded(
            float(np.mean(np.nan_to_num(bench) * sides)) * TRADING_DAYS if n else None
        ),
    }


def _entry_buckets(trades: list[TradeRecord], calendar: Calendar, key: str, lag: int) -> list:
    entries = [_utc_date(t.entry) for t in trades]
    return [bucket_name(key, v) for v in labels_in_force(entries, calendar, key, lag=lag)]


def _masked(arrays: tuple[np.ndarray, ...], mask: np.ndarray) -> dict[str, Any]:
    return _metrics(*(values[mask] for values in arrays))


def _bucket_row(
    bucket: str,
    mask: np.ndarray,
    arrays: tuple[np.ndarray, ...],
    context: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """One bucket's daily-view metrics, interval, trade count and insufficient flag."""
    row = {"bucket": bucket, **_masked(arrays, mask)}
    trades_in = sum(1 for b in context["entry_buckets"] if b == bucket)
    interval = context["intervals"][bucket]
    labelled, total_pnl = context["labelled"], context["total_pnl"]
    row |= {
        "time_share": _rounded(row["days"] / labelled if labelled else None, 4),
        "pnl_share": _rounded(row["pnl"] / total_pnl if total_pnl else None, 4),
        "ci_low": _rounded(interval["low"], 8),
        "ci_high": _rounded(interval["high"], 8),
        "ci_redraws": interval["redraws"],
        "trades_entered": trades_in,
        "insufficient": row["days"] < params["min_days"] or trades_in < params["min_trades"],
    }
    row["excess_vs_drift"] = _excess(row)
    return row


def _reconciliation(
    rows: list[dict[str, Any]], unlabelled: dict[str, Any], change: float, labelled: int
) -> dict[str, Any]:
    bucket_sum = sum(r["pnl"] for r in rows) + unlabelled["pnl"]
    return {
        "bucket_pnl_sum": round(bucket_sum, 2),
        "equity_change": round(change, 2),
        "reconciles": abs(bucket_sum - change) < _CENT * (len(rows) + 1),
        "labelled_days": labelled,
        "bucket_days_sum": sum(r["days"] for r in rows),
    }


def _family(
    name: str,
    order: tuple[str, ...],
    day_buckets: list[str | None],
    entry_buckets: list[str | None],
    inputs: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """One label family's rows, unlabelled row, total row, reconciliation and outcome."""
    frame = inputs["frame"]
    pnl, ret = frame["pnl"].to_numpy(float), frame["ret"].to_numpy(float)
    arrays = (pnl, ret, inputs["bench"], inputs["sides"], inputs["exposed"])
    names = np.array([b or UNLABELLED for b in day_buckets], dtype=object)
    context: dict[str, Any] = {
        "entry_buckets": entry_buckets,
        "labelled": int((names != UNLABELLED).sum()),
        "total_pnl": float(pnl.sum()),
    }
    resampled = bucket_intervals(
        ret, names, list(order), samples=params["samples"], seed=params["seed"],
        pairs=_pairs(order),
    )  # fmt: skip
    context["intervals"] = resampled["buckets"]
    rows = [_bucket_row(b, names == b, arrays, context, params) for b in order]
    unlabelled = {
        "bucket": UNLABELLED,
        **_masked(arrays, names == UNLABELLED),
        "trades_entered": sum(1 for b in entry_buckets if b is None),
    }
    change = float(frame["equity"].iloc[-1] - inputs["initial"])
    family = {
        "family": name,
        "rows": rows,
        "unlabelled": unlabelled,
        "total": {"bucket": "total", **_masked(arrays, np.ones(len(names), dtype=bool))},
        "reconciliation": _reconciliation(rows, unlabelled, change, context["labelled"]),
        "outcome": dependence_outcome(rows),
        "differences": [_rounded_difference(d) for d in resampled["differences"]],
        "trades": trade_view(inputs["trades"], entry_buckets, order),
    }
    if name in TREND_FAMILIES:
        family["alignment"] = alignment(inputs["trades"], entry_buckets)
    return family


def _pairs(order: tuple[str, ...]) -> list[tuple[str, str]]:
    """The regime-conditional differences reported: the two buckets of a two-bucket
    family, and for the crossed family each leg's high minus low and each volatility's up
    minus down."""
    if len(order) == 2:
        return [(order[0], order[1])]
    return [
        ("up/high", "up/low"), ("down/high", "down/low"),
        ("up/high", "down/high"), ("up/low", "down/low"),
    ]  # fmt: skip


def _rounded_difference(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("point", "low", "high")
    return {**row, **{k: _rounded(row[k], 8) for k in keys}}


def _excess(row: dict[str, Any]) -> float | None:
    if row["ann_return"] is None or row["drift_return"] is None:
        return None
    return _rounded(row["ann_return"] - row["drift_return"])


def families_of(label_set: str) -> dict[str, tuple[str, ...]]:
    """Each label family of a label set, with its bucket order."""
    return GROUND_TRUTH_FAMILIES if label_set == "ground_truth" else DETECTOR_FAMILIES


def _cross_labels(
    days: list[date], calendar: Calendar, scale: str, lag: int, entries: list[date]
) -> tuple[list[str | None], list[str | None]]:
    def joined(on: list[date]) -> list[str | None]:
        legs = labels_in_force(on, calendar, f"leg_{scale}", lag=lag)
        vols = labels_in_force(on, calendar, "volatility", lag=lag)
        return [f"{leg}/{vol}" if leg and vol else None for leg, vol in zip(legs, vols)]

    return joined(days), joined(entries)


def _widest_gap(families: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The scale to cross with volatility: the widest |up - down| annualised return among
    the scales whose two buckets are both sufficient (all scales when none is), so a gap
    resting on a bucket no rule may cite never picks it."""
    gaps: dict[str, float | None] = {}
    eligible = []
    for scale in SCALES:
        up, down = families[f"leg_{scale}"]["rows"]
        both = up["ann_return"] is not None and down["ann_return"] is not None
        gaps[scale] = _rounded(abs(up["ann_return"] - down["ann_return"])) if both else None
        if both and not (up["insufficient"] or down["insufficient"]):
            eligible.append(scale)
    pool = {s: gaps[s] for s in (eligible or SCALES) if gaps[s] is not None}
    scale = max(pool, key=lambda s: pool[s] or 0.0) if pool else SCALES[-1]
    return {"scale": scale, "gaps": gaps, "eligible_scales": eligible}


def profile(
    frame: pd.DataFrame,
    calendar: Calendar,
    trades: list[TradeRecord],
    closes: pd.Series,
    *,
    lag: int,
    min_days: int = MIN_DAYS,
    min_trades: int = MIN_TRADES,
    samples: int = 1000,
    seed: int = 0,
    initial_capital: float | None = None,
) -> dict[str, Any]:
    """Every family's daily-view table for one run under one calendar version."""
    if calendar.label_set == "ground_truth" and lag != 0:
        raise ValueError("ground-truth labels take no lag (lag_not_allowed)")
    days = list(frame.index)
    initial = (
        float(frame["equity"].iloc[0] - frame["pnl"].iloc[0])
        if initial_capital is None
        else float(initial_capital)
    )
    inputs = {
        "frame": frame,
        "initial": initial,
        "bench": benchmark_returns(days, closes),
        "sides": carried_sides(days, trades),
        "exposed": exposed_days(days, trades),
        "trades": trades,
    }
    params = {"min_days": min_days, "min_trades": min_trades, "samples": samples, "seed": seed}
    families: dict[str, dict[str, Any]] = {}
    for name, order in families_of(calendar.label_set).items():
        day_buckets = [bucket_name(name, v) for v in labels_in_force(days, calendar, name, lag=lag)]
        entry_buckets = _entry_buckets(trades, calendar, name, lag)
        families[name] = _family(name, order, day_buckets, entry_buckets, inputs, params)
    if calendar.label_set == "ground_truth":
        choice = _widest_gap(families)
        scale = choice["scale"]
        entries = [_utc_date(t.entry) for t in trades]
        day_buckets, entry_buckets = _cross_labels(days, calendar, scale, lag, entries)
        cross = _family("cross", CROSS_BUCKETS, day_buckets, entry_buckets, inputs, params)
        families["cross"] = {**cross, **choice}
    return {"label_set": calendar.label_set, "lag_days": lag, "families": families}
