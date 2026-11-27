"""Stationary bootstrap of a run's daily timeline and the stop-rule outcome (tasks.md 2.4).

Blocks are cut from the whole timeline (circular wrap, uniform starts, geometric lengths of
mean 10) before any bucket's days are picked, so a block never joins two spells of a bucket
that lie apart in time, and no day is underweighted at the ends of the run.
"""

import numpy as np
import pytest

from src.core.regimes.bootstrap import (
    CONFIDENCE,
    MEAN_BLOCK,
    bucket_intervals,
    dependence_outcome,
    draw_blocks,
)

pytestmark = pytest.mark.unit


def test_defaults_are_the_decided_ones():
    assert MEAN_BLOCK == 10
    assert CONFIDENCE == 0.90


def test_blocks_are_contiguous_in_the_timeline_with_a_circular_wrap():
    n = 400
    idx, starts = draw_blocks(n, samples=200, mean_block=10, rng=np.random.default_rng(3))
    assert idx.shape == starts.shape == (200, n)
    assert starts[:, 0].all()
    inside = ~starts[:, 1:]
    step = (idx[:, 1:] - idx[:, :-1]) % n
    assert (step[inside] == 1).all()  # inside a block: the next timeline day, wrapping n-1 → 0


def test_block_lengths_are_geometric_with_mean_ten():
    idx, starts = draw_blocks(5000, samples=40, mean_block=10, rng=np.random.default_rng(1))
    lengths = np.diff(np.flatnonzero(starts.ravel()))
    assert 9.5 < lengths.mean() < 10.5


def test_no_block_joins_two_spells_apart_in_time():
    """Two spells of one bucket years apart: a block holding days of both must cross every
    day between them, or the wrap. With mean-10 blocks over a 1,000-day gap, none does."""
    n = 2766
    spell_a, spell_b = np.arange(100, 160), np.arange(1900, 1960)
    idx, starts = draw_blocks(n, samples=300, mean_block=10, rng=np.random.default_rng(7))
    block_id = np.cumsum(starts, axis=1)
    for row in range(idx.shape[0]):
        in_a = np.isin(idx[row], spell_a)
        in_b = np.isin(idx[row], spell_b)
        assert not set(block_id[row][in_a]) & set(block_id[row][in_b])


def test_every_day_has_the_same_expected_inclusion():
    """The circular wrap: the first and last days are drawn as often as the middle ones."""
    n = 60
    idx, _ = draw_blocks(n, samples=20000, mean_block=10, rng=np.random.default_rng(11))
    counts = np.bincount(idx.ravel(), minlength=n) / idx.shape[0]
    assert np.allclose(counts, 1.0, atol=0.05)
    assert abs(counts[[0, -1]].mean() - counts[25:35].mean()) < 0.05


def test_same_seed_same_interval_and_another_seed_differs():
    rng = np.random.default_rng(0)
    returns = rng.normal(0.0005, 0.01, 800)
    labels = np.where(np.arange(800) % 50 < 25, "up", "down").astype(object)
    one = bucket_intervals(returns, labels, ["up", "down"], samples=300, seed=4)
    two = bucket_intervals(returns, labels, ["up", "down"], samples=300, seed=4)
    other = bucket_intervals(returns, labels, ["up", "down"], samples=300, seed=5)
    assert one == two
    assert one["buckets"]["up"]["low"] != other["buckets"]["up"]["low"]


def test_interval_covers_the_true_mean_near_ninety_percent():
    """i.i.d. data: the 90% interval holds the true mean in roughly 90% of seeded trials."""
    rng = np.random.default_rng(2024)
    hits, trials = 0, 120
    for trial in range(trials):
        returns = rng.normal(0.001, 0.01, 400)
        labels = np.full(400, "all", dtype=object)
        row = bucket_intervals(returns, labels, ["all"], samples=400, seed=trial)["buckets"]["all"]
        hits += row["low"] <= 0.001 <= row["high"]
    assert 0.82 <= hits / trials <= 0.97


def test_a_bucket_missing_from_a_resample_is_redrawn_and_counted():
    returns = np.zeros(200)
    returns[5] = 0.01
    labels = np.full(200, "other", dtype=object)
    labels[5] = "rare"  # one day: most resamples miss it
    row = bucket_intervals(returns, labels, ["rare"], samples=50, seed=0)["buckets"]["rare"]
    assert row["redraws"] > 0
    assert row["low"] == row["high"] == pytest.approx(0.01)


def test_short_run_and_empty_bucket_get_no_interval():
    short = bucket_intervals(np.zeros(9), np.full(9, "up", dtype=object), ["up"], samples=10)
    assert short["buckets"]["up"] == {"low": None, "high": None, "redraws": 0}
    labels = np.full(50, "up", dtype=object)
    empty = bucket_intervals(np.zeros(50), labels, ["down"], samples=10)
    assert empty["buckets"]["down"] == {"low": None, "high": None, "redraws": 0}


def _row(bucket, low, high, insufficient=False):
    return {"bucket": bucket, "ci_low": low, "ci_high": high, "insufficient": insufficient}


def test_strong_dependence_needs_one_interval_below_and_another_above_zero():
    out = dependence_outcome([_row("up", 0.001, 0.003), _row("down", -0.004, -0.001)])
    assert out["outcome"] == "strong_dependence"
    assert out["below_zero"] == ["down"] and out["above_zero"] == ["up"]


def test_no_dependence_when_two_buckets_are_sufficient_and_the_pattern_fails():
    out = dependence_outcome([_row("up", 0.001, 0.003), _row("down", -0.002, 0.001)])
    assert out["outcome"] == "no_dependence"
    assert out["sufficient"] == 2


def test_insufficient_buckets_are_never_cited():
    rows = [
        _row("up", 0.001, 0.003),
        _row("down", -0.004, -0.001, insufficient=True),
    ]
    out = dependence_outcome(rows)
    assert out["outcome"] == "insufficient_evidence"
    assert out["sufficient"] == 1
    assert out["below_zero"] == [] and out["insufficient_buckets"] == ["down"]


def test_difference_interval_pairs_the_same_resamples():
    """Up minus down, from the same draws: covers a known gap, and the seed reproduces it."""
    rng = np.random.default_rng(8)
    n = 1500
    labels = np.where((np.arange(n) // 40) % 2 == 0, "up", "down").astype(object)
    returns = np.where(labels == "up", 0.001, -0.0005) + rng.normal(0, 0.004, n)
    one = bucket_intervals(
        returns, labels, ["up", "down"], samples=400, seed=2, pairs=[("up", "down")]
    )
    two = bucket_intervals(
        returns, labels, ["up", "down"], samples=400, seed=2, pairs=[("up", "down")]
    )
    (diff,) = one["differences"]
    assert diff == two["differences"][0]
    assert (diff["a"], diff["b"]) == ("up", "down")
    assert diff["point"] == pytest.approx(
        returns[labels == "up"].mean() - returns[labels == "down"].mean()
    )
    assert 0 < diff["low"] < diff["point"] < diff["high"]
    assert diff["excludes_zero"] is True
    assert set(one) == {"buckets", "differences"}
    assert one["buckets"]["up"]["low"] is not None


def test_difference_with_an_empty_bucket_has_no_interval():
    labels = np.full(100, "up", dtype=object)
    out = bucket_intervals(
        np.zeros(100), labels, ["up", "down"], samples=20, pairs=[("up", "down")]
    )
    assert out["differences"][0]["low"] is None and out["differences"][0]["excludes_zero"] is False


def test_difference_pairs_only_rows_drawn_together():
    """A row redrawn for one bucket alone is not paired with the other bucket's row."""
    returns = np.linspace(-0.01, 0.01, 300)
    labels = np.full(300, "common", dtype=object)
    labels[150] = "rare"
    out = bucket_intervals(
        returns, labels, ["common", "rare"], samples=200, seed=1, pairs=[("rare", "common")]
    )
    (diff,) = out["differences"]
    assert out["buckets"]["rare"]["redraws"] > 0
    assert 0 < diff["paired_rows"] < 200


def test_draws_are_chunked_so_memory_does_not_scale_with_samples(monkeypatch):
    import src.core.regimes.bootstrap as bootstrap

    seen = []
    real = bootstrap.draw_blocks

    def spy(n, *, samples, mean_block, rng):
        seen.append(samples)
        return real(n, samples=samples, mean_block=mean_block, rng=rng)

    monkeypatch.setattr(bootstrap, "draw_blocks", spy)
    labels = np.full(500, "up", dtype=object)
    bucket_intervals(np.zeros(500), labels, ["up"], samples=1000, seed=0)
    assert max(seen) <= bootstrap.CHUNK and sum(seen) == 1000
