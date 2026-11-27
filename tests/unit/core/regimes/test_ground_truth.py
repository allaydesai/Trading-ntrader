"""Ground-truth labels: ATR, wave legs, ranging, volatility, finality (tasks.md 1.2)."""

import numpy as np
import pandas as pd
import pytest

from src.core.regimes.definition import GroundTruthDefinition
from src.core.regimes.ground_truth import (
    TurningPoint,
    atr,
    centred_volatility,
    efficiency_ratio,
    label_ground_truth,
    leg_labels,
    leg_summary,
    true_range,
    turning_points,
    volatility_median,
)

DEFAULT = GroundTruthDefinition()


def frame_of(close, spread: float = 0.5) -> pd.DataFrame:
    close = np.asarray(close, float)
    index = pd.bdate_range("2001-01-01", periods=len(close)).date
    return pd.DataFrame({"high": close + spread, "low": close - spread, "close": close}, index)


def zigzag(start: float, deltas: list[int]) -> np.ndarray:
    path = [start]
    for delta in deltas:
        step = 1 if delta > 0 else -1
        path += [path[-1] + step * (i + 1) for i in range(abs(delta))]
    return np.array(path)


#: Extremes at 30 (peak), 40, 70, 75, 105, 135; the series ends rising at 165.
ZIGZAG = zigzag(100, [30, -10, 30, -5, 30, -30, 30])


def random_walk(n: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    index = pd.bdate_range("2001-01-01", periods=n).date
    return pd.DataFrame({"high": high, "low": low, "close": close}, index)


class TestAtr:
    def test_true_range_uses_the_previous_close(self):
        frame = pd.DataFrame({"high": [10, 12, 11], "low": [9, 11, 8], "close": [9.5, 11.5, 9]})
        assert true_range(frame).tolist() == [1.0, 2.5, 3.5]

    def test_atr_is_the_simple_mean_of_true_range(self):
        frame = random_walk(60)
        tr = true_range(frame)
        got = atr(frame, 20)
        assert np.isnan(got[:19]).all()
        assert got[19] == pytest.approx(tr[:20].mean())
        assert got[45] == pytest.approx(tr[26:46].mean())


class TestTurningPoints:
    @pytest.mark.parametrize(
        ("multiple", "expected"),
        [
            (3, [(19, "trough"), (30, "peak"), (40, "trough"), (70, "peak"), (75, "trough"),
                 (105, "peak"), (135, "trough")]),
            (6, [(19, "trough"), (30, "peak"), (40, "trough"), (105, "peak"), (135, "trough")]),
            (12, [(19, "trough"), (105, "peak"), (135, "trough")]),
        ],
    )  # fmt: skip
    def test_zigzag_turning_points_per_scale(self, multiple, expected):
        frame = frame_of(ZIGZAG)
        points = turning_points(frame["close"].to_numpy(), atr(frame, 20), multiple)
        assert points == [TurningPoint(i, k) for i, k in expected]

    def test_threshold_uses_the_atr_at_the_extremes_date(self):
        close = np.array([0, 1, 2, 3, 4, 5, 4, 3, 2, 1, 0], float)
        atr_ = np.array([1.0] * 6 + [100.0] * 5)  # ATR explodes after the peak
        points = turning_points(close, atr_, 3)
        assert points == [TurningPoint(0, "trough"), TurningPoint(5, "peak")]

    def test_flat_zero_range_series_has_no_points(self):
        frame = frame_of(np.full(40, 100.0), spread=0.0)  # ATR is zero throughout
        assert turning_points(frame["close"].to_numpy(), atr(frame, 20), 3) == []
        labels = label_ground_truth(frame, DEFAULT, frozen_median=0.1)
        assert {row["leg_minor"] for row in labels} == {None}

    def test_no_atr_no_points(self):
        assert turning_points(np.arange(5.0), np.full(5, np.nan), 3) == []


class TestLegLabels:
    def test_legs_are_half_open_and_null_outside_the_points(self):
        points = [TurningPoint(2, "trough"), TurningPoint(5, "peak"), TurningPoint(7, "trough")]
        labels = leg_labels(10, points)
        assert labels == [None, None, None, "up", "up", "up", "down", "down", None, None]

    def test_major_scale_after_last_trough_is_null(self):
        labels = label_ground_truth(frame_of(ZIGZAG), DEFAULT, frozen_median=1.0)
        major = [row["leg_major"] for row in labels]
        assert major[:20] == [None] * 20
        assert set(major[20:106]) == {"up"}
        assert set(major[106:136]) == {"down"}
        assert set(major[136:]) == {None}  # no peak confirmed after the last trough

    def test_leg_summary_counts_legs_and_median_length(self):
        labels = label_ground_truth(frame_of(ZIGZAG), DEFAULT, frozen_median=1.0)
        minor = leg_summary(labels, "minor")
        assert minor == {"up_legs": 3, "down_legs": 3, "median_leg_sessions": 20.5}


class TestRanging:
    def test_trend_is_not_ranging_and_chop_is(self):
        trend = efficiency_ratio(np.arange(100.0), 21)
        chop = efficiency_ratio(np.array([100.0, 101.0] * 50), 21)
        assert trend[10] == pytest.approx(1.0)
        assert chop[50] < 0.1

    def test_er_is_centred_with_null_ends(self):
        close = random_walk(80)["close"].to_numpy()
        er = efficiency_ratio(close, 21)
        assert np.isnan(er[:10]).all() and np.isnan(er[-10:]).all()
        d = 40
        expected = (
            abs(close[d + 10] - close[d - 10]) / np.abs(np.diff(close[d - 10 : d + 11])).sum()
        )
        assert er[d] == pytest.approx(expected)

    def test_ranging_label_uses_threshold(self):
        frame = random_walk(120)
        labels = label_ground_truth(frame, DEFAULT, frozen_median=0.2)
        er = efficiency_ratio(frame["close"].to_numpy(), 21)
        for i, row in enumerate(labels):
            assert row["ranging"] == (None if np.isnan(er[i]) else bool(er[i] < 0.2))

    def test_threshold_changes_only_the_ranging_flag(self):
        frame = random_walk(400, seed=3)
        median = volatility_median(frame, DEFAULT)
        loose = GroundTruthDefinition(ranging_threshold=0.3)
        low = label_ground_truth(frame, DEFAULT, median)
        high = label_ground_truth(frame, loose, median)
        assert [{**r, "ranging": None} for r in low] == [{**r, "ranging": None} for r in high]
        # ER < 0.2 implies ER < 0.3, so the lower threshold flags a subset of the days.
        assert all(h for lo, h in zip(low, high) if lo["ranging"])
        assert sum(bool(r["ranging"]) for r in low) < sum(bool(r["ranging"]) for r in high)


class TestVolatility:
    def test_centred_volatility_is_annualised_sd_of_window_returns(self):
        close = random_walk(80)["close"].to_numpy()
        vol = centred_volatility(close, 21, 252)
        d = 30
        window = close[d - 10 : d + 11]
        returns = window[1:] / window[:-1] - 1
        assert vol[d] == pytest.approx(returns.std(ddof=1) * np.sqrt(252))
        assert np.isnan(vol[:10]).all() and np.isnan(vol[-10:]).all()

    def test_label_compares_to_the_frozen_median_not_the_sample(self):
        frame = random_walk(200)
        vol = centred_volatility(frame["close"].to_numpy(), 21, 252)
        frozen = float(np.nanmax(vol)) + 1  # nothing beats it
        labels = label_ground_truth(frame, DEFAULT, frozen_median=frozen)
        assert {row["volatility"] for row in labels[10:-10]} == {"low"}
        median = volatility_median(frame, DEFAULT)
        labels = label_ground_truth(frame, DEFAULT, frozen_median=median)
        highs = sum(row["volatility"] == "high" for row in labels)
        assert highs == int((vol[np.isfinite(vol)] > median).sum())


class TestFinality:
    def test_appending_bars_never_changes_a_set_label(self):
        full = random_walk(900, seed=11)
        median = volatility_median(full.iloc[:500], DEFAULT)
        short = label_ground_truth(full.iloc[:500], DEFAULT, median)
        long = label_ground_truth(full, DEFAULT, median)
        filled = 0
        for before, after in zip(short, long):
            for key, value in before.items():
                if value is not None:
                    assert after[key] == value, key
                elif after[key] is not None:
                    filled += 1
        assert filled > 0  # the tail's nulls were filled in

    def test_labels_are_reproducible(self):
        frame = random_walk(400)
        median = volatility_median(frame, DEFAULT)
        assert label_ground_truth(frame, DEFAULT, median) == label_ground_truth(
            frame.copy(), DEFAULT, median
        )
