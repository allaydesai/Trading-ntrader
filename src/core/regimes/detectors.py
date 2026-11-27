"""Detector regime labels: past data only (design.md section 4). Phase 1: ``sma200``.

The label for session *t* uses bars up to and including the close of *t*, so a
strategy could have acted on it after that close. The first ``warmup_sessions``
sessions carry no label, so every detector in a panel shares its days.
"""

from typing import Any

import numpy as np
import pandas as pd

from src.core.regimes.definition import DetectorDefinition
from src.core.regimes.rolling import window_mean


def sma_direction(close: np.ndarray, window: int) -> list[str | None]:
    """``up`` when the close is above its trailing SMA, ``down`` otherwise."""
    sma = window_mean(close, window)
    return [
        None if not np.isfinite(avg) else ("up" if c > avg else "down")
        for c, avg in zip(close, sma)
    ]


def label_detectors(frame: pd.DataFrame, definition: DetectorDefinition) -> list[dict[str, Any]]:
    """One detector payload per row of ``frame``, in order."""
    close = frame["close"].to_numpy(float)
    sma200 = sma_direction(close, definition.sma200.window)
    warmup = definition.warmup_sessions
    return [{"sma200": None if i < warmup else sma200[i]} for i in range(len(close))]
