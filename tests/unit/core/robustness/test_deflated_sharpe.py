"""The deflated Sharpe ratio (S5.4)."""

import math

import pytest

from src.core.robustness.deflated_sharpe import (
    deflated_sharpe,
    expected_max_sharpe,
    probabilistic_sharpe,
)

pytestmark = pytest.mark.unit


def test_probabilistic_sharpe_is_one_half_at_the_benchmark_and_rises_with_evidence():
    at = probabilistic_sharpe(0.1, benchmark=0.1, observations=100, skew=0.0, kurtosis=3.0)
    assert at == pytest.approx(0.5)
    short = probabilistic_sharpe(0.1, benchmark=0.0, observations=50, skew=0.0, kurtosis=3.0)
    long = probabilistic_sharpe(0.1, benchmark=0.0, observations=500, skew=0.0, kurtosis=3.0)
    assert 0.5 < short < long < 1.0


def test_expected_max_sharpe_is_zero_for_one_trial_and_grows_with_trials():
    assert expected_max_sharpe(trials=1, variance=0.01) == 0.0
    assert expected_max_sharpe(trials=10, variance=0.01) < expected_max_sharpe(
        trials=100, variance=0.01
    )


def test_expected_max_sharpe_matches_the_papers_formula():
    # Bailey & López de Prado (2014): sqrt(V) ((1-γ) Z^-1(1-1/N) + γ Z^-1(1-1/(N e))).
    from scipy.stats import norm

    gamma = 0.5772156649
    n, v = 20, 0.04
    wanted = math.sqrt(v) * (
        (1 - gamma) * norm.ppf(1 - 1 / n) + gamma * norm.ppf(1 - 1 / (n * math.e))
    )
    assert expected_max_sharpe(trials=n, variance=v) == pytest.approx(wanted)


def test_deflated_sharpe_equals_the_probabilistic_sharpe_against_zero_for_a_single_trial():
    one = deflated_sharpe(
        0.08, observations=1000, skew=0.0, kurtosis=3.0, trials=1, trial_sharpe_variance=0.0
    )
    assert one["probability"] == pytest.approx(
        probabilistic_sharpe(0.08, benchmark=0.0, observations=1000, skew=0.0, kurtosis=3.0)
    )
    assert one["expected_max_sharpe"] == 0.0


def test_more_trials_deflate_the_same_sharpe():
    few = deflated_sharpe(
        0.08, observations=1000, skew=0.0, kurtosis=3.0, trials=5, trial_sharpe_variance=0.002
    )
    many = deflated_sharpe(
        0.08, observations=1000, skew=0.0, kurtosis=3.0, trials=200, trial_sharpe_variance=0.002
    )
    assert many["probability"] < few["probability"]
    assert many["trials"] == 200


def test_fat_tails_and_negative_skew_lower_the_probability():
    normal = probabilistic_sharpe(0.1, benchmark=0.0, observations=200, skew=0.0, kurtosis=3.0)
    ugly = probabilistic_sharpe(0.1, benchmark=0.0, observations=200, skew=-1.0, kurtosis=8.0)
    assert ugly < normal
