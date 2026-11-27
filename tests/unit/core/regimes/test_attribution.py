"""Regime profile of one run against stored calendar rows (tasks.md 2.3; design.md 5).

Pure: a synthetic equity curve, trades and calendar rows. The daily view is the reference
and must reconcile to the run's P&L exactly; labels are aligned by session date with the
lag rule of design.md section 2.
"""

import hashlib
import json
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
    exposed_days,
    labels_in_force,
    position_weights,
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
    assert list(exposed_days(days, trades)) == [False, False, True, True, False, True]


def test_a_same_day_round_trip_never_cancels_a_carried_position():
    """A long carried in and a short opened and closed the same day: still exposed, and the
    drift side is the carried long, not the net of the two."""
    days = _sessions(4)
    trades = [
        TradeRecord(_at(days[0]), _at(days[2], 15), "BUY", 5.0),
        TradeRecord(_at(days[2], 16), _at(days[2], 19), "SELL", 1.0),
        TradeRecord(_at(days[3], 14), _at(days[3], 19), "BUY", 1.0),  # round trip only
    ]
    assert list(carried_sides(days, trades)) == [0, 1, 1, 0]
    assert list(exposed_days(days, trades)) == [False, True, True, True]


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
    family = result["families"]["sma200"]
    assert family["outcome"]["outcome"] == "strong_dependence"
    (diff,) = family["differences"]
    assert (diff["a"], diff["b"]) == ("up", "down")
    assert diff["low"] > 0 and diff["excludes_zero"] is True


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
    exposed = exposed_days(list(frame.index), trades)
    assert up["exposure"] == pytest.approx(np.mean(exposed[in_up]), abs=1e-4)
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
    assert [(d["a"], d["b"]) for d in cross["differences"]] == [
        ("up/high", "up/low"), ("down/high", "down/low"),
        ("up/high", "down/high"), ("up/low", "down/low"),
    ]  # fmt: skip
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


def test_the_cross_scale_is_chosen_among_scales_whose_buckets_are_sufficient():
    """A scale whose widest gap rests on an insufficient bucket does not pick the cross."""
    days, equity, trades, _, closes = _run()
    sessions = _sessions(320)
    calendar = _gt_calendar(sessions)
    for i, row in enumerate(calendar.labels):  # minor: a 5-day down bucket on the worst days
        row["leg_minor"] = "down" if 165 <= i < 170 else "up"
        row["leg_major"] = "up" if (i // 2) % 2 else "down"  # small gap, both sufficient
    frame = daily_frame(equity, INITIAL)
    result = profile(frame, calendar, trades, closes, lag=0, samples=20, seed=0)
    cross = result["families"]["cross"]
    minor_down = result["families"]["leg_minor"]["rows"][1]
    assert minor_down["insufficient"] is True
    gaps = cross["gaps"]
    assert gaps["minor"] > max(gaps["intermediate"], gaps["major"])
    assert cross["eligible_scales"] == ["intermediate", "major"]
    expected = max(("intermediate", "major"), key=lambda s: gaps[s])
    assert cross["scale"] == expected


# -- position-scaled drift (phase 2b; opt-in, the default output stays as it was) ---------

#: sha256 of the canonical JSON of the default profiles below, pinned from the code before
#: position-scaled drift existed (b6581df). A change here means stored phase 2 reports no
#: longer reproduce.
GOLDEN_SIDE_PROFILES = {
    "detector": "73a81b775e58dbb04be997868fe9eec2d2669d78aca5e5858bd5efd637aafbc1",
    "ground_truth": "35f40dab22e7c6b22fde93facb4a64a6c93874f178e77c5641c1444739c418f7",
}


def _digest(result: dict) -> str:
    text = json.dumps(result, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _default_profiles(**kwargs) -> dict[str, dict]:
    days, equity, trades, calendar, closes = _run()
    frame = daily_frame(equity, INITIAL)
    gt = _gt_calendar(_sessions(320))
    return {
        "detector": profile(frame, calendar, trades, closes, lag=1, samples=200, seed=0, **kwargs),
        "ground_truth": profile(frame, gt, trades, closes, lag=0, samples=200, seed=0, **kwargs),
    }


def test_the_default_profile_is_unchanged_from_phase_2():
    """The old version's output, byte for byte: default and explicit ``side`` scaling."""
    for kwargs in ({}, {"drift_scaling": "side"}):
        digests = {k: _digest(v) for k, v in _default_profiles(**kwargs).items()}
        assert digests == GOLDEN_SIDE_PROFILES


def test_position_weight_is_the_carried_notional_over_the_previous_equity():
    days = _sessions(6)
    closes = pd.Series([50.0, 50.0, 40.0, 40.0, 60.0, 60.0], index=days)
    previous = np.array([10_000.0, 10_000.0, 20_000.0, 8_000.0, 10_000.0, 10_000.0])
    trades = [
        TradeRecord(_at(days[1]), _at(days[3]), "BUY", 0.0, quantity=100.0),  # in on 2, 3
        TradeRecord(_at(days[2]), _at(days[4]), "SELL", 0.0, quantity=50.0),  # in on 3, 4
        TradeRecord(_at(days[5], 14), _at(days[5], 19), "BUY", 0.0, quantity=999.0),  # intraday
    ]
    weights = position_weights(days, trades, closes, previous)
    # day 2: 100 x 50 / 20,000; day 3: (100 - 50) x 40 / 8,000; day 4: -50 x 40 / 10,000
    assert list(weights) == pytest.approx([0.0, 0.0, 0.25, 0.25, -0.2, 0.0])


def test_position_weight_on_a_weekend_uses_the_last_session_close():
    sessions = _sessions(3)  # Thu 2, Fri 3, Mon 6 January 2020
    days = [sessions[0], sessions[1], date(2020, 1, 4), sessions[2]]
    closes = pd.Series([10.0, 20.0, 30.0], index=sessions)
    trades = [TradeRecord(_at(sessions[0]), None, "BUY", 0.0, quantity=10.0)]
    weights = position_weights(days, trades, closes, np.full(4, 1_000.0))
    assert list(weights) == pytest.approx([0.0, 0.1, 0.2, 0.2])


def _scaled_run():
    days, equity, trades, calendar, closes = _run()
    sized = [TradeRecord(t.entry, t.exit, t.side, t.pnl, quantity=10.0 * (i % 4 + 1))
             for i, t in enumerate(trades)]  # fmt: skip
    return daily_frame(equity, INITIAL), sized, calendar, closes


def test_position_scaled_drift_weights_each_days_benchmark_return_by_exposure():
    frame, trades, calendar, closes = _scaled_run()
    side = profile(frame, calendar, trades, closes, lag=1, samples=50, seed=0)
    scaled = profile(
        frame, calendar, trades, closes, lag=1, samples=50, seed=0, drift_scaling="position"
    )
    assert scaled["drift_scaling"] == "position" and "drift_scaling" not in side
    days = list(frame.index)
    previous = frame["equity"].shift(1).fillna(INITIAL).to_numpy()
    weights = position_weights(days, trades, closes, previous)
    bench = np.nan_to_num(benchmark_returns(days, closes))
    labels = np.array(labels_in_force(days, calendar, "sma200", lag=1), dtype=object)
    drift_keys = ("drift_return", "excess_vs_drift", "mean_position_weight")
    for row, plain in zip(scaled["families"]["sma200"]["rows"], side["families"]["sma200"]["rows"]):
        mask = labels == row["bucket"]
        expected = np.mean(bench[mask] * weights[mask]) * 252  # rows round to 6 places
        assert row["drift_return"] == pytest.approx(expected, abs=1e-6)
        assert row["mean_position_weight"] == pytest.approx(np.mean(weights[mask]), abs=1e-6)
        excess = row["ann_return"] - row["drift_return"]
        assert row["excess_vs_drift"] == pytest.approx(excess, abs=1e-6)
        assert row["drift_return"] != plain["drift_return"]
        # nothing but the drift columns moves: buckets, P&L, intervals, outcome
        assert {k: v for k, v in row.items() if k not in drift_keys} == {
            k: v for k, v in plain.items() if k not in drift_keys
        }
    assert scaled["families"]["sma200"]["outcome"] == side["families"]["sma200"]["outcome"]


def test_an_unknown_drift_scaling_is_refused():
    frame, trades, calendar, closes = _scaled_run()
    with pytest.raises(ValueError, match="drift_scaling"):
        profile(frame, calendar, trades, closes, lag=1, samples=10, drift_scaling="notional")
