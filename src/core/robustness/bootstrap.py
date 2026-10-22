"""Bootstrap confidence intervals and the sample-size verdict (S4.2).

The point statistics use the stored metrics' own definitions: Sharpe is the
mean over the sample standard deviation of daily returns, annualised on 252
days (``mark_to_market.risk_metrics``); profit factor is gross wins over gross
losses; win rate counts trades whose P&L is positive. Intervals are BCa
bootstraps from ``scipy.stats.bootstrap``; the minimum track record length is
Bailey & López de Prado's, at 95 % confidence against a Sharpe of zero.
"""

import math
from typing import Any

import numpy as np
from scipy import stats

TRADING_DAYS = 252
#: Below this many trades, intervals are too wide to prove anything (S4.2).
MIN_TRADES = 30
CONFIDENCE = 0.95


def sharpe(returns: np.ndarray) -> float | None:
    """Annualised Sharpe of daily returns, or None with no spread."""
    if len(returns) < 2:
        return None
    deviation = float(np.std(returns, ddof=1))
    if not math.isfinite(deviation) or deviation == 0.0:
        return None
    return float(np.mean(returns) / deviation * math.sqrt(TRADING_DAYS))


def profit_factor(pnls: np.ndarray) -> float | None:
    """Gross wins over gross losses; None when nothing was lost."""
    losses = float(-pnls[pnls < 0].sum())
    return float(pnls[pnls > 0].sum()) / losses if losses > 0 else None


def win_rate(pnls: np.ndarray) -> float | None:
    """The share of trades with a positive P&L."""
    return float(np.mean(pnls > 0)) if len(pnls) else None


def _resampled_profit_factor(pnls: np.ndarray) -> float:
    """A resample's profit factor: one that drew no loser but won is the best case (infinite)."""
    value = profit_factor(pnls)
    if value is not None:
        return value
    return math.inf if float(pnls[pnls > 0].sum()) > 0 else math.nan


def _interval(
    data: np.ndarray,
    statistic: Any,
    *,
    iterations: int,
    seed: int,
    resampled: Any = None,
) -> dict[str, Any]:
    """The point and its interval; ``resampled`` scores a resample when it can be infinite.

    With ``resampled``, an infinite upper bound is reported as ``high`` None and
    ``unbounded`` true, so a few losers still give the lower bound that matters.
    """
    point = statistic(data)
    if point is None or len(data) < 2:
        return {"point": point, "low": None, "high": None}

    def stat(sample: np.ndarray) -> float:
        value = statistic(np.asarray(sample))
        return math.nan if value is None else value

    score = stat if resampled is None else (lambda sample: resampled(np.asarray(sample)))
    low, high = _bootstrap(data, score, iterations=iterations, seed=seed, open_top=bool(resampled))
    row: dict[str, Any] = {"point": round(point, 6), "low": low, "high": high}
    if resampled is not None:
        row["unbounded"] = low is not None and high is None
    return row


def _bootstrap(
    data: np.ndarray, stat: Any, *, iterations: int, seed: int, open_top: bool = False
) -> tuple[float | None, float | None]:
    """BCa interval, falling back to percentile when BCa is degenerate (e.g. a constant).

    ``open_top``: the statistic can be infinite, so a finite lower bound under a
    non-finite upper one is kept, with the upper bound None.
    """
    for method in ("BCa", "percentile"):
        try:
            res = stats.bootstrap(
                (data,),
                stat,
                n_resamples=iterations,
                confidence_level=CONFIDENCE,
                method=method,
                random_state=np.random.default_rng(seed),
                vectorized=False,
            )
        except Exception:  # noqa: BLE001 - scipy raises several types on degenerate data
            continue
        low, high = float(res.confidence_interval.low), float(res.confidence_interval.high)
        if math.isfinite(low) and math.isfinite(high):
            return round(low, 6), round(high, 6)
        if open_top and math.isfinite(low):
            return round(low, 6), None
    return None, None


def min_track_record_length(
    *, sharpe_daily: float, skew: float, kurtosis: float, benchmark: float = 0.0
) -> float | None:
    """Daily observations needed to trust that the Sharpe beats ``benchmark`` at 95 %.

    ``kurtosis`` is raw (a normal distribution's is 3). None when the Sharpe is
    not above the benchmark: no track record would ever prove it.
    """
    if sharpe_daily <= benchmark:
        return None
    z = stats.norm.ppf(CONFIDENCE)
    spread = 1 - skew * sharpe_daily + (kurtosis - 1) / 4 * sharpe_daily**2
    if spread <= 0:
        return None
    return round(1 + spread * (z / (sharpe_daily - benchmark)) ** 2, 1)


def confidence_intervals(
    *, daily_returns: np.ndarray, trade_pnls: np.ndarray, iterations: int = 2000, seed: int = 0
) -> dict[str, Any]:
    """Intervals for Sharpe, profit factor and win rate, plus the sample-size verdict."""
    returns, pnls = np.asarray(daily_returns, dtype=float), np.asarray(trade_pnls, dtype=float)
    daily = sharpe(returns)
    skew = float(stats.skew(returns)) if len(returns) > 2 else 0.0
    kurt = float(stats.kurtosis(returns, fisher=False)) if len(returns) > 3 else 3.0
    return {
        "sharpe_ratio": _interval(returns, sharpe, iterations=iterations, seed=seed),
        "profit_factor": _interval(
            pnls,
            profit_factor,
            iterations=iterations,
            seed=seed,
            resampled=_resampled_profit_factor,
        ),
        "win_rate": _interval(pnls, win_rate, iterations=iterations, seed=seed),
        "trades": int(len(pnls)),
        "observations": int(len(returns)),
        "min_trades": MIN_TRADES,
        "insufficient_sample": len(pnls) < MIN_TRADES,
        "min_track_record_days": (
            None
            if daily is None
            else min_track_record_length(
                sharpe_daily=daily / math.sqrt(TRADING_DAYS), skew=skew, kurtosis=kurt
            )
        ),
        "iterations": iterations,
        "confidence": CONFIDENCE,
    }
