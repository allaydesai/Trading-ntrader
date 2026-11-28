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

from dataclasses import dataclass
from typing import Any

import numpy as np

MEAN_BLOCK = 10
CONFIDENCE = 0.90
BOOTSTRAP_SAMPLES = 1000
#: Resamples drawn at once: memory is ``CHUNK x n`` per array, whatever the sample count.
CHUNK = 200
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


@dataclass(frozen=True)
class _Means:
    """One bucket's resampled means; ``original`` marks rows drawn with every bucket."""

    values: np.ndarray
    redraws: int
    original: np.ndarray


def _resampled_means(
    returns: np.ndarray,
    labels: np.ndarray,
    buckets: list[str],
    *,
    samples: int,
    seed: int,
    mean_block: int,
) -> dict[str, _Means | None]:
    """Per bucket: each resample's mean over its drawn bucket days, and the redraw count.

    Resamples are drawn ``CHUNK`` at a time, so memory stays at ``CHUNK x n`` whatever the
    sample count. Every bucket reads the same draws, so two buckets' means pair row by row,
    except a row redrawn for one bucket alone: ``original`` marks the rows that pair.
    """
    rng = np.random.default_rng(seed)
    masks = {b: np.asarray(labels == b, dtype=bool) for b in buckets}
    chunks: dict[str, list[np.ndarray]] = {b: [] for b in buckets}
    for start in range(0, samples, CHUNK):
        size = min(CHUNK, samples - start)
        idx, _ = draw_blocks(len(returns), samples=size, mean_block=mean_block, rng=rng)
        drawn_returns = returns[idx]
        for bucket, mask in masks.items():
            if mask.any():
                chunks[bucket].append(_bucket_means(drawn_returns, mask[idx]))
    out: dict[str, _Means | None] = {}
    for bucket, mask in masks.items():
        if not mask.any():
            out[bucket] = None
            continue
        means = np.concatenate(chunks[bucket])
        original = np.isfinite(means)
        redraws = 0
        for row in np.flatnonzero(~original):
            value, used = _redraw(returns, mask, rng, mean_block, samples * _MAX_REDRAW_FACTOR)
            redraws += used
            means[row] = np.nan if value is None else value
        out[bucket] = _Means(means, redraws, original)
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
    means: dict[str, _Means | None],
    returns: np.ndarray,
    labels: np.ndarray,
    confidence: float,
) -> dict[str, Any]:
    """``mean(a) - mean(b)`` over the rows both buckets drew together (redraws excluded)."""
    a, b = pair
    row: dict[str, Any] = {"a": a, "b": b, "point": None, "low": None, "high": None}
    row |= {"excludes_zero": False, "paired_rows": 0}
    first, second = means.get(a), means.get(b)
    if first is None or second is None:
        return row
    row["point"] = float(returns[labels == a].mean() - returns[labels == b].mean())
    paired = first.original & second.original
    row["paired_rows"] = int(paired.sum())
    bounds = _percentiles(first.values[paired] - second.values[paired], confidence)
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
        means: dict[str, _Means | None] = {b: None for b in buckets}
    else:
        means = _resampled_means(
            returns, labels, buckets, samples=samples, seed=seed, mean_block=mean_block
        )
    out: dict[str, dict[str, Any]] = {}
    for bucket, found in means.items():
        bounds = None if found is None else _percentiles(found.values, confidence)
        redraws = 0 if found is None else found.redraws
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


#: Families read as leads, never as stop-rule evidence (design.md Q16, Allay 2026-11-27):
#: the cross's scale is picked from the data, so its outcome is reported, not counted.
EXPLORATORY_FAMILIES = ("cross",)


def stop_rule_reading(families: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One run's stop-rule verdict over its ground-truth families (design.md 5.4, Q16).

    ``dependence`` when a single family has ``strong_dependence``; otherwise
    ``insufficient_evidence`` when any single family lacks evidence (Q12: never read as no
    dependence); otherwise ``no_dependence``. The exploratory families are listed apart and
    move nothing.
    """
    deciding = {
        name: family["outcome"]["outcome"]
        for name, family in families.items()
        if name not in EXPLORATORY_FAMILIES
    }
    dependent = [n for n, o in deciding.items() if o == "strong_dependence"]
    insufficient = [n for n, o in deciding.items() if o == "insufficient_evidence"]
    if dependent:
        verdict = "dependence"
    elif insufficient:
        verdict = "insufficient_evidence"
    else:
        verdict = "no_dependence"
    exploratory = {
        name: {"outcome": family["outcome"]["outcome"], "scale": family.get("scale")}
        for name, family in families.items()
        if name in EXPLORATORY_FAMILIES
    }
    return {
        "verdict": verdict,
        "deciding": deciding,
        "dependent_families": dependent,
        "insufficient_families": insufficient,
        "exploratory": exploratory,
    }
