"""Ground-truth (hindsight) regime labels: wave legs, ranging, volatility (design.md section 3).

**Hindsight, not tradeable.** These labels look at data after the day they describe.
They are never exported to a strategy, config, sweep, filter or bar export; only the
files named in ``tests/unit/governance/test_regime_ground_truth_boundary.py`` may
import this module.

Every function is pure, over a DataFrame of daily ``high``, ``low``, ``close`` in
session order. Every label is **final once set**: a turning point is confirmed with
data up to the confirming close and the ATR at the extreme, and a centred window is
final once its last day has printed. So appending bars never changes a label that is
already set; it only fills labels that were ``None``.
"""

from typing import Any, NamedTuple

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from src.core.regimes.definition import SCALES, GroundTruthDefinition
from src.core.regimes.rolling import window_mean


class TurningPoint(NamedTuple):
    """A confirmed wave extreme: its session index and kind."""

    at: int
    kind: str  # "peak" | "trough"


def true_range(frame: pd.DataFrame) -> np.ndarray:
    """Daily true range; the first session (no previous close) is high − low."""
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    prev = np.concatenate(([np.nan], frame["close"].to_numpy(float)[:-1]))
    spans = np.vstack((high - low, np.abs(high - prev), np.abs(low - prev)))
    return np.nanmax(spans, axis=0)


def atr(frame: pd.DataFrame, window: int) -> np.ndarray:
    """ATR as the simple mean of true range over ``window`` sessions (not Wilder)."""
    return window_mean(true_range(frame), window)


class _Wave:
    """Directional-change state machine for one scale."""

    def __init__(self, close: np.ndarray, atr_: np.ndarray, multiple: float, start: int):
        self.close, self.atr, self.k = close, atr_, multiple
        self.mode: str | None = None  # None: undetermined; "up": seeking a peak; "down"
        self.hi = self.lo = start
        self.points: list[TurningPoint] = []

    # A move must be positive as well as reach the threshold: with a zero ATR (a flat,
    # zero-range stretch) a zero move would otherwise "confirm" an extreme.
    def _fell(self, i: int) -> bool:
        move = self.close[self.hi] - self.close[i]
        return move > 0 and move >= self.k * self.atr[self.hi]

    def _rose(self, i: int) -> bool:
        move = self.close[i] - self.close[self.lo]
        return move > 0 and move >= self.k * self.atr[self.lo]

    def _confirm(self, kind: str, i: int) -> None:
        extreme = self.hi if kind == "peak" else self.lo
        self.points.append(TurningPoint(extreme, kind))
        after = self.close[extreme + 1 : i + 1]
        if kind == "peak":
            self.mode, self.lo = "down", extreme + 1 + int(np.argmin(after))
        else:
            self.mode, self.hi = "up", extreme + 1 + int(np.argmax(after))

    def step(self, i: int) -> None:
        c = self.close[i]
        if self.mode in (None, "up") and c > self.close[self.hi]:
            self.hi = i
        if self.mode in (None, "down") and c < self.close[self.lo]:
            self.lo = i
        for _ in range(2):  # one day can confirm an extreme and then its opposite
            if self.mode is None:
                fell, rose = self._fell(i), self._rose(i)
                if fell and (not rose or self.hi < self.lo):
                    self._confirm("peak", i)
                elif rose:
                    self._confirm("trough", i)
                else:
                    return
            elif self.mode == "up" and self._fell(i):
                self._confirm("peak", i)
            elif self.mode == "down" and self._rose(i):
                self._confirm("trough", i)
            else:
                return


def turning_points(close: np.ndarray, atr_: np.ndarray, multiple: float) -> list[TurningPoint]:
    """Confirmed peaks and troughs, alternating, in time order.

    A peak is confirmed when a close falls from the running high by at least
    ``multiple`` × the ATR *as of the high's date*; a trough symmetrically.
    """
    finite = np.flatnonzero(np.isfinite(atr_))
    if len(finite) == 0:
        return []
    wave = _Wave(close, atr_, multiple, int(finite[0]))
    for i in range(int(finite[0]) + 1, len(close)):
        wave.step(i)
    return wave.points


def leg_labels(n: int, points: list[TurningPoint]) -> list[str | None]:
    """``up`` on (trough, peak], ``down`` on (peak, trough]; ``None`` outside the points."""
    labels: list[str | None] = [None] * n
    for a, b in zip(points, points[1:]):
        direction = "up" if a.kind == "trough" else "down"
        labels[a.at + 1 : b.at + 1] = [direction] * (b.at - a.at)
    return labels


def _centred(values: np.ndarray, n: int, half: int) -> np.ndarray:
    out = np.full(n, np.nan)
    out[half : n - half] = values
    return out


def efficiency_ratio(close: np.ndarray, window: int) -> np.ndarray:
    """``|C(d+h) − C(d−h)| / Σ|ΔC|`` over the ``window`` sessions centred on *d*."""
    n, half = len(close), window // 2
    if n < window:
        return np.full(n, np.nan)
    net = np.abs(close[2 * half :] - close[: n - 2 * half])
    path = sliding_window_view(np.abs(np.diff(close)), 2 * half).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(path > 0, net / np.where(path > 0, path, 1.0), 0.0)
    return _centred(ratio, n, half)


def centred_volatility(close: np.ndarray, window: int, annualisation: int) -> np.ndarray:
    """Annualised sd of the close-to-close returns inside the centred window."""
    n, half = len(close), window // 2
    if n < window:
        return np.full(n, np.nan)
    returns = close[1:] / close[:-1] - 1.0
    sd = sliding_window_view(returns, 2 * half).std(axis=1, ddof=1)
    return _centred(sd * np.sqrt(annualisation), n, half)


def volatility_median(frame: pd.DataFrame, definition: GroundTruthDefinition) -> float:
    """Median centred volatility over every session of ``frame``: the value a version
    freezes in ``frozen_stats`` when it is created."""
    vol = centred_volatility(
        frame["close"].to_numpy(float), definition.volatility_window, definition.annualisation
    )
    finite = vol[np.isfinite(vol)]
    if len(finite) == 0:
        raise ValueError("too few sessions for a volatility median")
    return float(np.median(finite))


def label_ground_truth(
    frame: pd.DataFrame, definition: GroundTruthDefinition, frozen_median: float
) -> list[dict[str, Any]]:
    """One ground-truth payload per row of ``frame``, in order."""
    close = frame["close"].to_numpy(float)
    n = len(close)
    atr_ = atr(frame, definition.atr_window)
    legs = {
        scale: leg_labels(n, turning_points(close, atr_, getattr(definition.scales, scale)))
        for scale in SCALES
    }
    er = efficiency_ratio(close, definition.efficiency_window)
    vol = centred_volatility(close, definition.volatility_window, definition.annualisation)
    rows = []
    for i in range(n):
        row: dict[str, Any] = {f"leg_{s}": legs[s][i] for s in SCALES}
        row["ranging"] = bool(er[i] < definition.ranging_threshold) if np.isfinite(er[i]) else None
        row["volatility"] = (
            ("high" if vol[i] > frozen_median else "low") if np.isfinite(vol[i]) else None
        )
        rows.append(row)
    return rows


def leg_summary(labels: list[dict[str, Any]], scale: str) -> dict[str, Any]:
    """Count of up and down legs and the median leg length (sessions) at one scale."""
    key = f"leg_{scale}"
    lengths: dict[str, list[int]] = {"up": [], "down": []}
    current, run = None, 0
    for row in [*labels, {key: None}]:
        value = row[key]
        if value == current:
            run += 1
            continue
        if current is not None:
            lengths[current].append(run)
        current, run = value, 1
    every = lengths["up"] + lengths["down"]
    return {
        "up_legs": len(lengths["up"]),
        "down_legs": len(lengths["down"]),
        "median_leg_sessions": float(np.median(every)) if every else None,
    }
