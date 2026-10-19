"""Constant-mix portfolio over stored daily curves: alignment, weights, overlap (S9.2–S9.5)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.core.robustness.portfolio import (
    METRIC_KEYS,
    aligned_returns,
    check_weights,
    combine,
    contribution,
    correlation,
    delta,
    drawdown_overlap,
    evaluate,
    portfolio_returns,
    quiet,
    summary,
)

pytestmark = pytest.mark.unit


def _equity(start: date, end: date, daily: float, base: float = 1000.0, seed: int = 0) -> pd.Series:
    index = pd.bdate_range(start, end, tz="UTC")
    noise = np.random.default_rng(seed or len(index)).normal(0, 0.002, len(index))
    return pd.Series(base * np.cumprod(1 + daily + noise), index=index, name="equity")


def _frame(*columns: list[float], start: str = "2020-01-01") -> pd.DataFrame:
    index = pd.bdate_range(start, periods=len(columns[0]), tz="UTC")
    return pd.DataFrame({chr(97 + i): col for i, col in enumerate(columns)}, index=index)


def test_aligned_returns_inner_joins_on_dates_and_reports_dropped_days():
    a = _equity(date(2020, 1, 1), date(2021, 12, 31), 0.001)
    b = _equity(date(2021, 1, 1), date(2022, 12, 31), 0.0005, base=100.0)
    frame, dropped = aligned_returns({"a": a, "b": b})
    assert list(frame.columns) == ["a", "b"]
    assert frame.index[0] == b.index[1] and not frame.isna().any().any()
    assert frame.index[-1] == pd.Timestamp("2021-12-31", tz="UTC")
    assert dropped["a"] == len(a) - 1 - len(frame) and dropped["b"] == len(b) - 1 - len(frame)


def test_aligned_returns_needs_two_curves():
    with pytest.raises(ValueError):
        aligned_returns({"a": _equity(date(2020, 1, 1), date(2020, 12, 31), 0.001)})


def test_weights_are_normalised_and_bad_ones_refused():
    assert check_weights({"a": 1, "b": 3}, ["a", "b"]) == {"a": 0.25, "b": 0.75}
    for bad in ({"a": 0, "b": 1}, {"a": -1, "b": 2}, {"a": float("nan"), "b": 1}, {"a": 1}):
        with pytest.raises(ValueError):
            check_weights(bad, ["a", "b"])


def test_combine_with_one_sleeve_is_that_sleeves_path_and_fifty_fifty_is_the_mean():
    frame = _frame([0.01, -0.02, 0.03], [0.0, 0.01, -0.01])
    alone = combine(frame, {"a": 1.0, "b": 1e-12}, initial=100.0)
    assert alone.to_numpy() == pytest.approx(100 * np.cumprod(1 + frame["a"]), rel=1e-9)
    assert list(portfolio_returns(frame, {"a": 0.5, "b": 0.5})) == pytest.approx(
        list((frame["a"] + frame["b"]) / 2)
    )
    assert len(combine(frame, {"a": 0.5, "b": 0.5}, initial=1.0)) == 3


def test_correlation_has_a_unit_diagonal_and_null_for_a_flat_sleeve():
    frame = _frame([0.01, -0.02, 0.03, 0.0], [-0.01, 0.02, -0.03, 0.0], [0.0, 0.0, 0.0, 0.0])
    matrix = correlation(frame)
    assert matrix["a"]["a"] == 1.0 and matrix["a"]["b"] == pytest.approx(-1.0)
    assert matrix["a"]["c"] is None and matrix["c"]["c"] is None


def test_drawdown_overlap_counts_shared_days_and_episodes():
    a = [0.0] * 5 + [-0.2] + [0.0] * 4 + [0.25] + [0.0] * 9
    b = [0.0] * 8 + [-0.2] + [0.0] * 3 + [0.25] + [0.0] * 7
    (pair,) = drawdown_overlap(_frame(a, b), threshold=0.1)
    assert pair["a"] == "a" and pair["b"] == "b"
    assert pair["both_below"] == pytest.approx(2 / 20) and pair["jaccard"] == pytest.approx(2 / 7)
    assert pair["episodes"] == {"a": 1, "b": 1, "a_with_b": 1, "b_with_a": 1}
    (never,) = drawdown_overlap(_frame(a, b), threshold=0.5)
    assert never["both_below"] == 0.0 and never["jaccard"] is None
    assert never["episodes"] == {"a": 0, "b": 0, "a_with_b": 0, "b_with_a": 0}


def test_contribution_shares_sum_to_one_and_name_the_worst_drawdown():
    frame = _frame([0.01, 0.02, -0.05, -0.05, 0.01], [0.01, 0.0, -0.01, -0.01, 0.02])
    weights = {"a": 0.5, "b": 0.5}
    equity = combine(frame, weights, initial=1000.0)
    found = contribution(frame, weights, equity)
    shares = [v["share"] for v in found["return"].values()]
    assert sum(shares) == pytest.approx(1.0)
    assert found["return"]["a"]["weighted_return"] == pytest.approx(0.5 * sum(frame["a"]))
    worst = found["drawdown"]
    assert worst["peak"] == frame.index[1].date().isoformat()
    assert worst["trough"] == frame.index[3].date().isoformat()
    assert worst["portfolio_drawdown"] == pytest.approx(0.97 * 0.97 - 1)
    assert worst["by_sleeve"]["a"]["weighted_return"] == pytest.approx(-0.05)
    assert worst["by_sleeve"]["a"]["share"] == pytest.approx(0.05 / 0.06)


def test_summary_uses_calendar_day_cagr_and_measures_time_under_water():
    index = pd.bdate_range("2020-01-01", periods=100, tz="UTC")
    values = np.concatenate(
        [1000 + np.arange(11), 1010 - np.arange(1, 21), 990 + np.arange(1, 70)]
    ).astype(float)
    values[50] = 1010.0  # back at the day-10 peak on day 50
    equity = pd.Series(values, index=index)
    found = summary(equity, 1000.0)
    days = (index[-1] - index[0]).days
    assert found["cagr"] == pytest.approx((values[-1] / 1000) ** (365.25 / days) - 1)
    assert found["total_return"] == pytest.approx(values[-1] / 1000 - 1)
    assert found["time_under_water_days"] == (index[50] - index[10]).days
    assert found["max_drawdown"] == pytest.approx(990 / 1010 - 1)
    assert found["max_drawdown_at"] == index[30].date().isoformat()
    assert found["calmar_ratio"] == pytest.approx(found["cagr"] / abs(found["max_drawdown"]))
    assert found["days"] == 100 and set(METRIC_KEYS) <= set(found)


def test_a_flat_curve_has_no_ratios_and_no_drawdown():
    index = pd.bdate_range("2020-01-01", periods=10, tz="UTC")
    found = summary(pd.Series(1000.0, index=index), 1000.0)
    assert found["sharpe_ratio"] is None and found["volatility"] is None
    assert found["max_drawdown"] == 0.0 and found["time_under_water_days"] == 0
    assert found["cagr"] == 0.0 and found["calmar_ratio"] is None


def test_delta_subtracts_per_metric_and_propagates_nulls():
    found = delta(
        {"sharpe_ratio": 0.8, "cagr": None, "days": 10},
        {"sharpe_ratio": 1.0, "cagr": 0.1, "days": 10},
    )
    assert found["sharpe_ratio"] == {"without": 0.8, "with": 1.0, "delta": pytest.approx(0.2)}
    assert found["cagr"] == {"without": None, "with": 0.1, "delta": None}
    assert found["days"]["delta"] == 0


def test_evaluate_returns_the_normalised_weights_curve_metrics_and_contribution():
    frame = _frame([0.01, -0.02, 0.03], [0.0, 0.01, -0.01])
    found = evaluate(frame, {"a": 2, "b": 2}, initial=100.0)
    assert found["weights"] == {"a": 0.5, "b": 0.5}
    assert set(found) == {"weights", "equity", "metrics", "contribution"}
    assert len(found["equity"]) == 3 and found["metrics"]["days"] == 3


def test_a_sleeve_that_barely_moves_is_quiet():
    loud = list(np.random.default_rng(1).normal(0, 0.01, 60))
    frame = _frame(loud, [v / 100 for v in loud], [v / 5 for v in loud])
    assert quiet(frame) == ["b"]
    assert quiet(frame, ratio=0.3) == ["b", "c"]
    assert quiet(_frame([0.0] * 5, [0.0] * 5)) == []
