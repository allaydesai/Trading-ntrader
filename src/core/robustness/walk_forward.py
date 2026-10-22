"""Sliced walk-forward: does the way parameters are chosen work on data it did not see? (S5.3)

Every grid cell was run once over the whole span; each window's in-sample and
out-of-sample results are sliced from that cell's stored daily equity. In
each window the cell with the best in-sample ``select_by`` is chosen and its
out-of-sample slice is what it would have earned. Efficiency is the mean
over windows of out-of-sample over in-sample annualised return (the vault's
G3 rule), over windows whose in-sample return is positive.

A slice starts from the last equity point *before* the window (the run's
initial capital before the first), so the first day's return is real, not a
spurious zero. Because the runs are continuous, indicators are warm and open
positions carry across window edges: slightly optimistic versus a true re-run.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

from src.core.mark_to_market import TRADING_DAYS, risk_metrics

MODES = ("rolling", "anchored")
SELECTORS = ("sharpe_ratio", "annualised_return", "max_drawdown")
MIN_WINDOWS = 2
#: Fewer out-of-sample trades than this make a window's result a weak reading (spec default).
MIN_TRADES_PER_WINDOW = 20
BASIS = (
    "sliced from one full-span run per cell: in-sample and out-of-sample results are the "
    "stored daily equity cut at the window edges, so indicators are warm and positions carry "
    "across edges (slightly optimistic versus a true re-run)"
)


@dataclass(frozen=True)
class Window:
    """One in-sample stretch and the out-of-sample stretch that follows it."""

    index: int
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date

    def to_dict(self) -> dict[str, Any]:
        return {k: v.isoformat() if isinstance(v, date) else v for k, v in self.__dict__.items()}


def _years_on(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def windows(start: date, end: date, *, is_years: int, oos_years: int, mode: str) -> list[Window]:
    """Rolling (step = the out-of-sample length) or anchored windows inside ``[start, end]``."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
    if is_years < 1 or oos_years < 1:
        raise ValueError("is_years and oos_years must be at least 1")
    found: list[Window] = []
    while True:
        step = len(found) * oos_years
        is_start = _years_on(start, step) if mode == "rolling" else start
        oos_start = _years_on(start, is_years + step)
        oos_end = _years_on(oos_start, oos_years) - timedelta(days=1)
        if oos_end > end:
            break
        is_end = oos_start - timedelta(days=1)
        found.append(Window(len(found) + 1, is_start, is_end, oos_start, oos_end))
    if len(found) < MIN_WINDOWS:
        raise ValueError(
            f"{len(found)} window(s) fit between {start} and {end} with {is_years}y in-sample "
            f"and {oos_years}y out-of-sample; at least {MIN_WINDOWS} are needed"
        )
    return found


def annualised_return(base: float, end_value: float, *, days: int) -> float | None:
    """The return compounded over ``days`` trading days to a year of 252."""
    if base <= 0 or end_value <= 0 or days < 1:
        return None
    return float((end_value / base) ** (TRADING_DAYS / days) - 1)


def _stamp(day: date) -> pd.Timestamp:
    return pd.Timestamp(day, tz="UTC")


def _after(day: date) -> pd.Timestamp:
    """Midnight UTC after ``day``: a window runs to before it, so its last day's close counts."""
    return _stamp(day) + timedelta(days=1)


def slice_metrics(equity: pd.Series, start: date, end: date, *, initial: float) -> dict[str, Any]:
    """A window's metrics from the equity curve, based on the last point before it."""
    before = equity[equity.index < _stamp(start)]
    base = float(before.iloc[-1]) if len(before) else float(initial)
    window = equity[(equity.index >= _stamp(start)) & (equity.index < _after(end))]
    if window.empty:
        return {"base": base, "end_value": None, "days": 0, "annualised_return": None}
    risk = risk_metrics(window, base)
    end_value = float(window.iloc[-1])
    return {
        "base": base,
        "end_value": end_value,
        "days": int(len(window)),
        "return": round(end_value / base - 1, 6) if base > 0 else None,
        "annualised_return": annualised_return(base, end_value, days=len(window)),
        "sharpe_ratio": risk.sharpe_ratio,
        "max_drawdown": risk.max_drawdown,
    }


def _score(metrics: dict[str, Any], select_by: str) -> float:
    value = metrics.get(select_by)
    return float("-inf") if value is None else float(value)


def _trades_in(stamps: list[pd.Timestamp], start: date, end: date) -> int:
    return sum(1 for t in stamps if _stamp(start) <= t < _after(end))


def _window_row(
    window: Window,
    cells: dict[str, pd.Series],
    *,
    select_by: str,
    initial: Mapping[str, float],
    trades: Mapping[str, list[pd.Timestamp]],
) -> dict[str, Any]:
    in_sample = {
        name: slice_metrics(curve, window.is_start, window.is_end, initial=initial[name])
        for name, curve in cells.items()
    }
    chosen = max(in_sample, key=lambda n: _score(in_sample[n], select_by))
    oos = slice_metrics(cells[chosen], window.oos_start, window.oos_end, initial=initial[chosen])
    oos["trades"] = _trades_in(trades.get(chosen, []), window.oos_start, window.oos_end)
    row = {
        **window.to_dict(),
        "chosen": chosen,
        "in_sample": in_sample[chosen],
        "out_of_sample": oos,
        "candidates": {n: m.get(select_by) for n, m in in_sample.items()},
    }
    if oos["trades"] < MIN_TRADES_PER_WINDOW:
        row["note"] = (
            f"only {oos['trades']} out-of-sample trades (fewer than {MIN_TRADES_PER_WINDOW})"
        )
    return row


def evaluate(
    cells: dict[str, pd.Series],
    found: list[Window],
    *,
    select_by: str,
    initial: float | Mapping[str, float],
    trades: Mapping[str, list[pd.Timestamp]],
) -> dict[str, Any]:
    """Per-window selection and results, walk-forward efficiency and the negative windows."""
    if select_by not in SELECTORS:
        raise ValueError(f"select_by must be one of {SELECTORS}, not {select_by!r}")
    if not cells:
        raise ValueError("no cells to evaluate")
    bases = initial if isinstance(initial, Mapping) else {name: initial for name in cells}
    rows = [_window_row(w, cells, select_by=select_by, initial=bases, trades=trades) for w in found]
    ratios, excluded = [], []
    for row in rows:
        is_ar, oos_ar = (
            row["in_sample"]["annualised_return"],
            row["out_of_sample"]["annualised_return"],
        )
        if is_ar is None or oos_ar is None or is_ar <= 0:
            excluded.append(row["index"])
        else:
            ratios.append(oos_ar / is_ar)
    negative = sum(1 for r in rows if (r["out_of_sample"]["annualised_return"] or 0) < 0)
    return {
        "method": "sliced",
        "basis": BASIS,
        "select_by": select_by,
        "cells": list(cells),
        "windows": rows,
        "efficiency": round(sum(ratios) / len(ratios), 6) if ratios else None,
        "windows_used": len(ratios),
        "excluded_windows": excluded,
        "negative_windows": negative,
    }
