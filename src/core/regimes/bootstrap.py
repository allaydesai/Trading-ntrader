"""Stationary bootstrap of a run's daily timeline, and the stop-rule outcome (design.md 5.3-5.4).

Politis and Romano (1994), as decided on 2026-11-26 (D14): each resample is a sequence of
blocks whose start day is uniform over all *N* days of the run and whose length is
geometric with mean ``MEAN_BLOCK`` (*p* = 1 / mean), wrapping circularly from the last day
to the first, until *N* days are drawn. Blocks are cut **before** a bucket's days are
picked, so a block never joins two spells of a bucket that lie apart in time except through
the wrap, and the wrap gives every day the same inclusion probability (no end-of-run
underweighting, unlike a non-wrapping moving block bootstrap).

Each resample's statistic is the mean return of the drawn days that fall in the bucket. A
resample that draws no day of the bucket is redrawn, and the redraw count is reported. The
interval is the percentile interval at ``CONFIDENCE``. Every family shares the same draws
for the same seed, so a report reproduces exactly.

Why a mean block of 10: regime spells and return volatility cluster over days to weeks.
Ten sessions (two weeks) is the spec's block size; geometric lengths make the resampled
series stationary, which a fixed block length does not.
"""

from typing import Any

import numpy as np

MEAN_BLOCK = 10
CONFIDENCE = 0.90
BOOTSTRAP_SAMPLES = 1000
#: Redraw budget per bucket, as a multiple of the sample count, before it is given up.
_MAX_REDRAW_FACTOR = 200


def draw_blocks(
    n: int, *, samples: int, mean_block: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """``(indices, starts)``, each ``(samples, n)``: timeline day per drawn position, and
    whether that position starts a new block. Inside a block the next position is the next
    timeline day, wrapping from ``n - 1`` to 0."""
    starts = rng.random((samples, n)) < 1.0 / mean_block
    starts[:, 0] = True
    origins = rng.integers(0, n, size=(samples, n))
    position = np.arange(n)
    block_start = np.maximum.accumulate(np.where(starts, position, 0), axis=1)
    origin = np.take_along_axis(origins, block_start, axis=1)
    return (origin + position - block_start) % n, starts


def _bucket_means(drawn_returns: np.ndarray, in_bucket: np.ndarray) -> np.ndarray:
    counts = in_bucket.sum(axis=1)
    sums = np.where(in_bucket, drawn_returns, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(counts > 0, sums / np.maximum(counts, 1), np.nan)


def _redraw(
    returns: np.ndarray, mask: np.ndarray, rng: np.random.Generator, mean_block: int, limit: int
) -> tuple[float | None, int]:
    """One resample's bucket mean, drawing again until the resample holds a bucket day."""
    for attempt in range(1, limit + 1):
        idx, _ = draw_blocks(len(returns), samples=1, mean_block=mean_block, rng=rng)
        hit = mask[idx[0]]
        if hit.any():
            return float(returns[idx[0]][hit].mean()), attempt
    return None, limit


def _resampled_means(
    returns: np.ndarray,
    labels: np.ndarray,
    buckets: list[str],
    *,
    samples: int,
    seed: int,
    mean_block: int,
) -> dict[str, tuple[np.ndarray | None, int]]:
    """Per bucket: each resample's mean over its drawn bucket days, and the redraw count.

    Every bucket reads the same draws, so the means of two buckets pair row by row (a
    redrawn row is the exception: it is a fresh draw for that bucket alone).
    """
    rng = np.random.default_rng(seed)
    idx, _ = draw_blocks(len(returns), samples=samples, mean_block=mean_block, rng=rng)
    drawn_returns = returns[idx]
    out: dict[str, tuple[np.ndarray | None, int]] = {}
    for bucket in buckets:
        mask = np.asarray(labels == bucket, dtype=bool)
        if not mask.any():
            out[bucket] = (None, 0)
            continue
        means = _bucket_means(drawn_returns, mask[idx])
        redraws = 0
        for row in np.flatnonzero(np.isnan(means)):
            value, used = _redraw(returns, mask, rng, mean_block, samples * _MAX_REDRAW_FACTOR)
            redraws += used
            means[row] = np.nan if value is None else value
        out[bucket] = (means, redraws)
    return out


def _percentiles(values: np.ndarray, confidence: float) -> tuple[float, float] | None:
    kept = values[np.isfinite(values)]
    if not len(kept):
        return None
    tail = (1.0 - confidence) / 2.0
    low, high = np.quantile(kept, [tail, 1.0 - tail])
    return float(low), float(high)


def _difference(
    pair: tuple[str, str],
    means: dict[str, tuple[np.ndarray | None, int]],
    returns: np.ndarray,
    labels: np.ndarray,
    confidence: float,
) -> dict[str, Any]:
    a, b = pair
    row: dict[str, Any] = {"a": a, "b": b, "point": None, "low": None, "high": None}
    row["excludes_zero"] = False
    first, second = means.get(a, (None, 0))[0], means.get(b, (None, 0))[0]
    if first is None or second is None:
        return row
    row["point"] = float(returns[labels == a].mean() - returns[labels == b].mean())
    bounds = _percentiles(first - second, confidence)
    if bounds is not None:
        row["low"], row["high"] = bounds
        row["excludes_zero"] = bounds[0] > 0 or bounds[1] < 0
    return row


def bucket_intervals(
    returns: np.ndarray,
    labels: np.ndarray,
    buckets: list[str],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = 0,
    mean_block: int = MEAN_BLOCK,
    confidence: float = CONFIDENCE,
    pairs: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """``{"buckets": {bucket: {low, high, redraws}}, "differences": [...]}``.

    Each bucket's ``confidence`` interval is on its mean daily return. Each pair
    ``(a, b)`` gets the interval on ``mean(a) - mean(b)`` from the same resamples, with its
    point value and whether it excludes zero. ``returns`` and ``labels`` cover the run's
    **whole** timeline in date order (unlabelled days included), so blocks are cut before
    any bucket is selected.
    """
    returns = np.asarray(returns, dtype=float)
    labels = np.asarray(labels, dtype=object)
    empty = {"low": None, "high": None, "redraws": 0}
    if len(returns) < mean_block:
        means: dict[str, tuple[np.ndarray | None, int]] = {b: (None, 0) for b in buckets}
    else:
        means = _resampled_means(
            returns, labels, buckets, samples=samples, seed=seed, mean_block=mean_block
        )
    out: dict[str, dict[str, Any]] = {}
    for bucket, (values, redraws) in means.items():
        bounds = None if values is None else _percentiles(values, confidence)
        out[bucket] = (
            {**empty, "redraws": redraws}
            if bounds is None
            else {"low": bounds[0], "high": bounds[1], "redraws": redraws}
        )
    differences = [_difference(p, means, returns, labels, confidence) for p in pairs or []]
    return {"buckets": out, "differences": differences}


def dependence_outcome(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The stop-rule reading of one label family's labelled buckets (design.md 5.4).

    ``strong_dependence``: a sufficient bucket's interval lies entirely below zero and
    another's entirely above. ``no_dependence``: at least two sufficient buckets and no such
    pair. ``insufficient_evidence``: fewer than two sufficient buckets. Insufficient buckets
    are never cited.
    """
    sufficient = [r for r in rows if not r["insufficient"] and r["ci_low"] is not None]
    below = [r["bucket"] for r in sufficient if r["ci_high"] < 0]
    above = [r["bucket"] for r in sufficient if r["ci_low"] > 0]
    if len(sufficient) < 2:
        outcome, below, above = "insufficient_evidence", [], []
    elif below and above:
        outcome = "strong_dependence"
    else:
        outcome = "no_dependence"
    return {
        "outcome": outcome,
        "sufficient": len(sufficient),
        "below_zero": below,
        "above_zero": above,
        "insufficient_buckets": [r["bucket"] for r in rows if r["insufficient"]],
    }
