"""Regime profile of one run against stored calendar rows (tasks.md 2.3; design.md 5).

Pure: a synthetic equity curve, trades and calendar rows. The daily view is the reference
and must reconcile to the run's P&L exactly; labels are aligned by session date with the
lag rule of design.md section 2.
"""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.core.regimes.attribution import (
    Calendar,
    TradeRecord,
    benchmark_returns,
    carried_sides,
    daily_frame,
    labels_in_force,
    profile,
)

pytestmark = pytest.mark.unit

INITIAL = 1_000_000.0


def _sessions(n: int, start: str = "2020-01-02") -> list[date]:
    return list(pd.bdate_range(start, periods=n).date)


def _equity(days: list[date], values) -> pd.Series:
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in days])
    return pd.Series(np.asarray(values, dtype=float), index=index)


def _at(day: date, hour: int = 5) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)


def _detector(sessions: list[date], flip: int, data_end: date | None = None) -> Calendar:
    labels = [{"sma200": "up" if i < flip else "down"} for i in range(len(sessions))]
    return Calendar("detector", sessions, labels, data_end or sessions[-1])


def _closes(sessions: list[date], seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(sessions)))), index=sessions)


# -- daily P&L --------------------------------------------------------------------------


def test_first_day_is_measured_from_initial_capital():
    days = _sessions(3)
    frame = daily_frame(_equity(days, [1_001_000, 999_000, 1_002_000]), INITIAL)
    assert list(frame.index) == days
    assert list(frame["pnl"]) == [1_000.0, -2_000.0, 3_000.0]
    assert frame["ret"].iloc[0] == pytest.approx(0.001)
    assert frame["ret"].iloc[1] == pytest.approx(-2_000 / 1_001_000)


def test_several_points_on_one_utc_day_keep_the_last():
    stamps = pd.DatetimeIndex(
        ["2020-01-02 15:00", "2020-01-02 21:00", "2020-01-03 21:00"], tz="UTC"
    )
    frame = daily_frame(pd.Series([1.0e6 + 5, 1.0e6 + 10, 1.0e6 + 30], index=stamps), INITIAL)
    assert list(frame["pnl"]) == [10.0, 20.0]


# -- label alignment ---------------------------------------------------------------------


def test_lag_zero_and_one_put_the_flip_day_in_different_buckets():
    sessions = _sessions(10)
    calendar = _detector(sessions, flip=5)
    lag0 = labels_in_force(sessions, calendar, "sma200", lag=0)
    lag1 = labels_in_force(sessions, calendar, "sma200", lag=1)
    assert lag0[5] == "down" and lag1[5] == "up"
    assert lag1[0] is None  # no session strictly before the first one
    assert lag1[6] == "down"


def test_weekend_inside_the_range_carries_the_earlier_label():
    sessions = _sessions(10)  # 2020-01-02 (Thu) ...
    calendar = _detector(sessions, flip=2)  # Thu, Fri up; Mon on down
    saturday = date(2020, 1, 4)
    assert labels_in_force([saturday], calendar, "sma200", lag=0) == ["up"]  # Friday's
    assert labels_in_force([saturday], calendar, "sma200", lag=1) == ["up"]  # Friday's
    monday = date(2020, 1, 6)
    assert labels_in_force([monday], calendar, "sma200", lag=1) == ["up"]  # Friday's


def test_days_after_the_versions_data_end_are_unlabelled():
    sessions = _sessions(10)
    calendar = _detector(sessions[:6], flip=10, data_end=sessions[5])
    days = [sessions[5], sessions[5] + timedelta(days=1), sessions[7]]
    assert labels_in_force(days, calendar, "sma200", lag=1) == ["up", None, None]


def test_ground_truth_takes_the_exact_date_and_refuses_a_lag():
    sessions = _sessions(4)
    labels = [
        {"leg_minor": "up", "leg_intermediate": None, "leg_major": "up", "ranging": True,
         "volatility": "low"}
        for _ in sessions
    ]  # fmt: skip
    calendar = Calendar("ground_truth", sessions, labels, sessions[-1])
    saturday = date(2020, 1, 4)
    found = labels_in_force([sessions[0], saturday], calendar, "leg_minor", lag=0)
    assert found == ["up", None]
    assert labels_in_force([sessions[0]], calendar, "leg_intermediate", lag=0) == [None]
    with pytest.raises(ValueError, match="lag"):
        labels_in_force(sessions, calendar, "leg_minor", lag=1)


# -- exposure and benchmark -----------------------------------------------------------------


def test_exposure_counts_positions_carried_in_from_the_previous_close():
    days = _sessions(6)
    trades = [
        TradeRecord(_at(days[1]), _at(days[3]), "BUY", 10.0),  # carried into days 2 and 3
        TradeRecord(_at(days[5], 14), _at(days[5], 19), "SELL", 1.0),  # intraday round trip
        TradeRecord(_at(days[4]), None, "SELL", 0.0),  # opened on day 4's bar, still open
    ]
    sides = carried_sides(days, trades)
    assert list(sides) == [0, 0, 1, 1, 0, -1]


def test_benchmark_return_is_the_references_close_to_close_on_the_day():
    sessions = _sessions(4)
    closes = pd.Series([100.0, 110.0, 99.0, 99.0], index=sessions)
    days = [sessions[0], sessions[1], date(2020, 1, 4), sessions[2]]
    out = benchmark_returns(days, closes)
    assert np.isnan(out[0])  # no earlier close
    assert out[1] == pytest.approx(0.10)
    assert np.isnan(out[2])  # Saturday: no session
    assert out[3] == pytest.approx(-0.10)


# -- the profile ---------------------------------------------------------------------------


def _run(n=300, flip=150, seed=3):
    sessions = _sessions(n + 20)
    days = sessions[20:]
    rng = np.random.default_rng(seed)
    returns = np.where(np.arange(n) < flip - 20, 0.002, -0.002) + rng.normal(0, 0.004, n)
    equity = INITIAL * np.cumprod(1 + returns)
    trades = [
        TradeRecord(_at(days[i]), _at(days[i + 3]), "BUY" if i % 2 else "SELL", float(i - 100))
        for i in range(0, n - 5, 6)
    ]
    trades.append(TradeRecord(_at(days[-2]), None, "BUY", 0.0))
    return days, _equity(days, equity), trades, _detector(sessions, flip=flip), _closes(sessions)


def _profile(lag=1, **kwargs):
    days, equity, trades, calendar, closes = _run()
    frame = daily_frame(equity, INITIAL)
    params = {"min_days": 60, "min_trades": 20, "samples": 200, "seed": 0} | kwargs
    return frame, profile(frame, calendar, trades, closes, lag=lag, **params)


def test_bucket_pnl_reconciles_to_the_runs_total():
    frame, result = _profile()
    family = result["families"]["sma200"]
    rows = family["rows"] + [family["unlabelled"]]
    total = frame["equity"].iloc[-1] - INITIAL
    assert sum(r["pnl"] for r in rows) == pytest.approx(total, abs=0.005)
    assert family["reconciliation"]["reconciles"] is True
    assert sum(r["days"] for r in family["rows"]) == family["reconciliation"]["labelled_days"]
    assert family["total"]["days"] == len(frame)


def test_lag_changes_the_tables():
    _, lag0 = _profile(lag=0)
    _, lag1 = _profile(lag=1)
    assert lag0["families"]["sma200"]["rows"] != lag1["families"]["sma200"]["rows"]
    assert (lag0["lag_days"], lag1["lag_days"]) == (0, 1)


def test_row_metrics_and_the_insufficient_flag():
    _, result = _profile()
    up, down = result["families"]["sma200"]["rows"]
    assert (up["bucket"], down["bucket"]) == ("up", "down")
    assert up["days"] == 131 and up["time_share"] == pytest.approx(131 / 300, abs=1e-4)
    assert up["ann_return"] > 0 > down["ann_return"]
    assert up["ci_low"] > 0 and down["ci_high"] < 0
    assert up["trades_entered"] + down["trades_entered"] == 51
    assert up["insufficient"] is False
    _, strict = _profile(min_days=200)
    assert all(r["insufficient"] for r in strict["families"]["sma200"]["rows"])
    _, few = _profile(min_trades=30)
    assert all(r["insufficient"] for r in few["families"]["sma200"]["rows"])


def test_family_outcome_is_reported():
    _, result = _profile()
    assert result["families"]["sma200"]["outcome"]["outcome"] == "strong_dependence"


def test_benchmark_and_drift_cover_the_buckets_days():
    days, equity, trades, calendar, closes = _run()
    frame = daily_frame(equity, INITIAL)
    result = profile(frame, calendar, trades, closes, lag=1, samples=50, seed=0)
    up = result["families"]["sma200"]["rows"][0]
    labels = labels_in_force(list(frame.index), calendar, "sma200", lag=1)
    bench = benchmark_returns(list(frame.index), closes)
    in_up = np.array([v == "up" for v in labels])
    assert up["benchmark_ann_return"] == pytest.approx(np.nanmean(bench[in_up]) * 252, abs=1e-6)
    sides = carried_sides(list(frame.index), trades)
    assert up["exposure"] == pytest.approx(np.mean(sides[in_up] != 0), abs=1e-4)
    expected_drift = np.nanmean(np.where(np.isnan(bench), 0, bench)[in_up] * sides[in_up]) * 252
    assert up["drift_return"] == pytest.approx(expected_drift, abs=1e-6)


def _gt_calendar(sessions: list[date]) -> Calendar:
    labels = []
    for i in range(len(sessions)):
        labels.append(
            {
                "leg_minor": "up" if i % 20 < 10 else "down",
                "leg_intermediate": "up" if i % 60 < 30 else "down",
                "leg_major": "up" if i < 170 else "down",
                "ranging": i % 3 == 0,
                "volatility": "high" if i % 7 < 3 else "low",
            }
        )
    return Calendar("ground_truth", sessions, labels, sessions[-1])


def test_ground_truth_families_and_the_widest_gap_cross():
    days, equity, trades, _, closes = _run()
    sessions = _sessions(320)
    frame = daily_frame(equity, INITIAL)
    result = profile(frame, _gt_calendar(sessions), trades, closes, lag=0, samples=50, seed=0)
    families = result["families"]
    assert set(families) == {
        "leg_minor", "leg_intermediate", "leg_major", "ranging", "volatility", "cross",
    }  # fmt: skip
    assert [r["bucket"] for r in families["ranging"]["rows"]] == ["ranging", "not_ranging"]
    cross = families["cross"]
    gaps = cross["gaps"]
    assert cross["scale"] == max(gaps, key=lambda s: gaps[s]) == "major"
    assert [r["bucket"] for r in cross["rows"]] == ["up/high", "up/low", "down/high", "down/low"]
    for family in families.values():
        assert family["reconciliation"]["reconciles"] is True


def test_ground_truth_profile_refuses_a_lag():
    days, equity, trades, _, closes = _run()
    frame = daily_frame(equity, INITIAL)
    with pytest.raises(ValueError, match="lag"):
        profile(frame, _gt_calendar(_sessions(320)), trades, closes, lag=1, samples=10)


def test_same_seed_reproduces_the_profile():
    _, one = _profile(seed=9)
    _, two = _profile(seed=9)
    assert one == two


def test_every_family_has_a_trade_view_and_trend_families_an_alignment():
    days, equity, trades, _, closes = _run()
    frame = daily_frame(equity, INITIAL)
    result = profile(frame, _gt_calendar(_sessions(320)), trades, closes, lag=0, samples=20)
    for name, family in result["families"].items():
        view = family["trades"]
        closed = sum(r["trades"] for r in view["rows"]) + view["unlabelled"]["trades"]
        assert closed == view["closed_trades"] == 50 and view["open_trades"] == 1
        assert ("alignment" in family) == name.startswith("leg_")
    detector = _profile()[1]["families"]["sma200"]
    assert {r["group"] for r in detector["alignment"]} == {
        "with_trend", "counter_trend", "unclassified",
    }  # fmt: skip
