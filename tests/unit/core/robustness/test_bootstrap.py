"""Bootstrap confidence intervals and the sample-size verdict (S4.2)."""

import numpy as np
import pytest

from src.core.robustness.bootstrap import (
    MIN_TRADES,
    confidence_intervals,
    min_track_record_length,
    profit_factor,
    sharpe,
    win_rate,
)

pytestmark = pytest.mark.unit


def test_point_statistics_match_the_stored_metric_definitions():
    pnls = np.array([100.0, -50.0, 30.0, -20.0, 0.0])
    assert profit_factor(pnls) == pytest.approx(130 / 70)
    assert win_rate(pnls) == pytest.approx(2 / 5)  # breakeven counts as not winning
    returns = np.array([0.01, -0.005, 0.02, 0.0, 0.003])
    expected = returns.mean() / returns.std(ddof=1) * np.sqrt(252)
    assert sharpe(returns) == pytest.approx(expected)


def test_profit_factor_without_losses_is_none():
    assert profit_factor(np.array([1.0, 2.0])) is None


def test_a_few_losers_still_give_a_profit_factor_interval():
    """Some resamples draw no loser; that is the best case, not a reason to drop the interval."""
    pnls = np.array([50.0 + i for i in range(27)] + [-100.0, -120.0, -80.0])
    returns = np.random.default_rng(0).normal(0.001, 0.01, 100)
    row = confidence_intervals(daily_returns=returns, trade_pnls=pnls, iterations=2000, seed=1)[
        "profit_factor"
    ]
    assert row["low"] is not None and row["low"] <= row["point"]
    assert row["high"] is None or row["high"] >= row["point"]
    assert row["unbounded"] is (row["high"] is None)


def test_with_one_loser_the_upper_bound_is_unbounded():
    """Over a third of resamples miss the single loser: the upper bound is infinite."""
    pnls = np.array([10.0] * 9 + [-30.0])
    returns = np.random.default_rng(0).normal(0.001, 0.01, 100)
    row = confidence_intervals(daily_returns=returns, trade_pnls=pnls, iterations=2000, seed=1)[
        "profit_factor"
    ]
    assert row["point"] == pytest.approx(3.0)
    assert row["low"] is not None and row["high"] is None and row["unbounded"] is True


def test_intervals_contain_the_point_estimates_and_flag_a_small_sample():
    rng = np.random.default_rng(0)
    returns = rng.normal(0.001, 0.01, 300)
    pnls = rng.normal(20, 100, 25)
    result = confidence_intervals(daily_returns=returns, trade_pnls=pnls, iterations=500, seed=1)
    for name in ("sharpe_ratio", "profit_factor", "win_rate"):
        row = result[name]
        assert row["low"] <= row["point"] <= row["high"], name
    assert result["trades"] == 25
    assert result["insufficient_sample"] is True
    assert result["min_trades"] == MIN_TRADES == 30


def test_a_sample_of_thirty_trades_is_not_flagged():
    rng = np.random.default_rng(2)
    result = confidence_intervals(
        daily_returns=rng.normal(0.001, 0.01, 100),
        trade_pnls=rng.normal(5, 50, 30),
        iterations=200,
        seed=1,
    )
    assert result["insufficient_sample"] is False


def test_min_track_record_length_grows_as_the_sharpe_shrinks():
    long = min_track_record_length(sharpe_daily=0.05, skew=0.0, kurtosis=3.0)
    short = min_track_record_length(sharpe_daily=0.2, skew=0.0, kurtosis=3.0)
    assert long is not None and short is not None and long > short
    assert min_track_record_length(sharpe_daily=-0.1, skew=0.0, kurtosis=3.0) is None
