"""A run's stored equity curve as a series: drawdown and downsampling (S3.3)."""

from typing import Any

import numpy as np
import pandas as pd


def equity_series(points: list[dict[str, Any]]) -> pd.Series:
    """``[{"time": unix_seconds, "value": equity}]`` → equity indexed by UTC time."""
    if not points:
        return pd.Series(dtype=float)
    index = pd.to_datetime([p["time"] for p in points], unit="s", utc=True)
    series = pd.Series([float(p["value"]) for p in points], index=index, name="equity")
    return series[~series.index.duplicated(keep="last")].sort_index()


def drawdown(equity: pd.Series) -> pd.Series:
    """Fractional drawdown from the running peak (0 at a peak, negative below it)."""
    if equity.empty:
        return equity
    return equity / equity.cummax() - 1.0


def max_drawdown(equity: pd.Series) -> float:
    return float(drawdown(equity).min()) if len(equity) else 0.0


def downsample(equity: pd.Series, max_points: int) -> pd.Series:
    """At most ``max_points`` points: the last of each equal bucket, keeping the first
    and last points and the peak and trough of the deepest drawdown, so the shape
    and the worst drawdown survive exactly."""
    if len(equity) <= max_points:
        return equity
    trough = int(drawdown(equity).to_numpy().argmin())
    peak = int(equity.to_numpy()[: trough + 1].argmax())
    step = len(equity) / (max_points - 4)
    keep = {int(i * step) - 1 for i in range(1, max_points - 3)}
    keep |= {0, len(equity) - 1, peak, trough}
    positions = np.array(sorted(k for k in keep if 0 <= k < len(equity)), dtype=np.int64)
    return equity.iloc[positions]
