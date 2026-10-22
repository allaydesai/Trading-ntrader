"""The deflated Sharpe ratio: a best-of-many Sharpe judged against what luck would give (S5.4).

Bailey & López de Prado, *The Deflated Sharpe Ratio* (2014). Sharpe ratios
here are per observation (daily, not annualised). The probabilistic Sharpe
is the chance the true Sharpe exceeds a benchmark, given the sample length,
skew and (raw) kurtosis of the returns; the deflated Sharpe sets that
benchmark to the Sharpe the best of N independent trials would reach by
chance, from the variance of the trials' Sharpes.
"""

import math
from typing import Any

from scipy.stats import norm

EULER_GAMMA = 0.5772156649015329


def probabilistic_sharpe(
    sharpe: float, *, benchmark: float, observations: int, skew: float, kurtosis: float
) -> float:
    """P(true Sharpe > ``benchmark``), from ``observations`` returns with these moments."""
    if observations < 2:
        return 0.5
    spread = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe**2
    if spread <= 0:
        return 0.5
    z = (sharpe - benchmark) * math.sqrt(observations - 1) / math.sqrt(spread)
    return float(norm.cdf(z))


def expected_max_sharpe(*, trials: int, variance: float) -> float:
    """The Sharpe the best of ``trials`` zero-skill strategies would show by chance."""
    if trials < 2 or variance <= 0:
        return 0.0
    high = norm.ppf(1 - 1 / trials)
    typical = norm.ppf(1 - 1 / (trials * math.e))
    return float(math.sqrt(variance) * ((1 - EULER_GAMMA) * high + EULER_GAMMA * typical))


def deflated_sharpe(
    sharpe: float,
    *,
    observations: int,
    skew: float,
    kurtosis: float,
    trials: int,
    trial_sharpe_variance: float,
) -> dict[str, Any]:
    """The deflated Sharpe: the probability the observed Sharpe beats the luckiest of N trials."""
    benchmark = expected_max_sharpe(trials=trials, variance=trial_sharpe_variance)
    probability = probabilistic_sharpe(
        sharpe, benchmark=benchmark, observations=observations, skew=skew, kurtosis=kurtosis
    )
    return {
        "sharpe": round(sharpe, 6),
        "expected_max_sharpe": round(benchmark, 6),
        "probability": round(probability, 6),
        "trials": trials,
        "trial_sharpe_std": round(math.sqrt(max(trial_sharpe_variance, 0.0)), 6),
        "observations": observations,
        "skew": round(skew, 6),
        "kurtosis": round(kurtosis, 6),
    }
