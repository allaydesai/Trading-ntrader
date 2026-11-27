"""Window statistics shared by the ground-truth and detector labels."""

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def window_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Trailing simple mean; ``nan`` until ``window`` values exist.

    Each window is summed on its own (no running sum), so a value never depends on data
    outside its window, and the same window gives the same float on any longer series.
    """
    out = np.full(len(values), np.nan)
    if len(values) >= window:
        out[window - 1 :] = sliding_window_view(values, window).mean(axis=1)
    return out
