"""Detector labels: sma200, the 272-session warm-up and no lookahead (tasks.md 1.4)."""

import numpy as np
import pandas as pd
import pytest

from src.core.regimes.definition import DetectorDefinition, build_definition
from src.core.regimes.detectors import label_detectors

DEFAULT = DetectorDefinition()


def random_walk(n: int, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    index = pd.bdate_range("2000-01-03", periods=n).date
    return pd.DataFrame({"high": close * 1.004, "low": close * 0.996, "close": close}, index)


def test_sma200_is_up_when_close_above_its_trailing_average():
    frame = random_walk(700)
    labels = label_detectors(frame, DEFAULT)
    close = frame["close"].to_numpy()
    for t in (272, 300, 450, 699):
        sma = close[t - 199 : t + 1].mean()
        assert labels[t]["sma200"] == ("up" if close[t] > sma else "down")
    assert {row["sma200"] for row in labels[272:]} == {"up", "down"}


def test_first_272_sessions_are_unlabelled_and_the_273rd_is_labelled():
    labels = label_detectors(random_walk(400), DEFAULT)
    assert all(row["sma200"] is None for row in labels[:272])
    assert labels[272]["sma200"] in ("up", "down")


@pytest.mark.parametrize("t", [272, 350, 401, 598])
def test_no_lookahead_perturbing_the_future_leaves_the_past(t):
    frame = random_walk(600)
    before = label_detectors(frame, DEFAULT)
    rng = np.random.default_rng(t)
    altered = frame.copy()
    shock = rng.uniform(0.3, 3.0, len(frame) - t - 1)
    for column in ("high", "low", "close"):
        altered.iloc[t + 1 :, altered.columns.get_loc(column)] *= shock
    after = label_detectors(altered, DEFAULT)
    assert after[: t + 1] == before[: t + 1]
    assert label_detectors(frame.iloc[: t + 1], DEFAULT) == before[: t + 1]


def test_window_override_changes_the_rule():
    frame = random_walk(700)
    short = build_definition("detector", {"sma200": {"window": 20}})
    assert label_detectors(frame, short) != label_detectors(frame, DEFAULT)
