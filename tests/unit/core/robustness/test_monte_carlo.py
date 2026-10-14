"""Monte Carlo over a run's closed trades (S4.1)."""

import numpy as np
import pytest

from src.core.robustness.monte_carlo import Trade, _days_under_water, longest_runs, simulate

pytestmark = pytest.mark.unit

TRADES = [Trade(pnl=p, gap_days=g) for p, g in [(100, 5), (-40, 3), (60, 7), (-80, 2), (120, 4)]]


def test_reshuffle_keeps_every_trade_so_the_final_return_and_span_never_change():
    result = simulate(TRADES, starting_balance=1000, iterations=200, method="reshuffle", seed=1)
    final = result["final_return"]
    assert final["p5"] == final["p50"] == final["p95"] == pytest.approx(160 / 1000)
    assert result["span_days"] == 21
    assert result["cagr"]["p5"] == result["cagr"]["p95"]


def test_bootstrap_resamples_with_replacement_so_outcomes_spread():
    result = simulate(TRADES, starting_balance=1000, iterations=500, method="bootstrap", seed=1)
    assert result["final_return"]["p5"] < result["final_return"]["p95"]
    assert result["method"] == "bootstrap" and result["trades"] == 5


def test_same_seed_same_numbers():
    a = simulate(TRADES, starting_balance=1000, iterations=300, method="bootstrap", seed=7)
    b = simulate(TRADES, starting_balance=1000, iterations=300, method="bootstrap", seed=7)
    assert a == b


def test_drawdowns_are_negative_and_the_worst_is_the_fifth_percentile():
    result = simulate(TRADES, starting_balance=1000, iterations=300, method="bootstrap", seed=3)
    dd = result["max_drawdown"]
    assert dd["p5"] <= dd["p50"] <= dd["p95"] <= 0


def test_breach_probability_falls_as_the_named_drawdown_deepens():
    shallow = simulate(
        TRADES, starting_balance=1000, iterations=400, method="bootstrap", seed=3, breach=0.05
    )
    deep = simulate(
        TRADES, starting_balance=1000, iterations=400, method="bootstrap", seed=3, breach=0.5
    )
    assert 0 <= deep["breach_probability"] <= shallow["breach_probability"] <= 1
    assert shallow["breach"] == 0.05


def test_losing_streak_and_time_under_water_of_a_known_order():
    # Reshuffle of a single ordering is itself: streak 2 (the two middle losses), under water
    # from the first loss's exit until the recovery: gaps 3 + 7 = 10 days.
    trades = [Trade(100, 1), Trade(-40, 3), Trade(-30, 7), Trade(120, 4)]
    result = simulate(trades, starting_balance=1000, iterations=5, method="reshuffle", seed=0)
    assert result["longest_losing_streak"]["p50"] <= 2
    assert 0 <= result["time_under_water_days"]["p95"] <= 14


@pytest.mark.parametrize(
    ("under", "days"),
    [
        # Under from the first exit (day 1) until the second exit recovers it (day 31).
        ([True, False, False], 30),
        # Still under at the last exit: the stretch ends with the data (day 33).
        ([False, True, True], 2),
        # Two stretches: the longer one counts.
        ([True, False, True], 30),
    ],
)
def test_time_under_water_runs_from_the_losing_exit_to_the_recovering_one(under, days):
    gaps = np.array([[1.0, 30.0, 2.0]])
    assert _days_under_water(np.array([under]), gaps)[0] == days


def test_longest_runs_is_the_longest_true_stretch_per_row():
    mask = np.array([[1, 1, 0, 1, 1, 1], [0, 0, 0, 0, 0, 0], [1, 0, 1, 0, 1, 0]], dtype=bool)
    assert longest_runs(mask).tolist() == [3, 0, 1]


def test_iterations_below_one_or_no_trades_are_refused():
    with pytest.raises(ValueError):
        simulate(TRADES, starting_balance=1000, iterations=0, method="bootstrap", seed=0)
    with pytest.raises(ValueError):
        simulate([], starting_balance=1000, iterations=10, method="bootstrap", seed=0)
    with pytest.raises(ValueError):
        simulate(TRADES, starting_balance=1000, iterations=10, method="shuffle", seed=0)
