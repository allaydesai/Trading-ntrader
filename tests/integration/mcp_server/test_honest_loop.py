"""The honest research loop on real runs (F2, F3): explore, benchmark, freeze, test the
holdout once, score against the gates, iterate.

Each job is reserved through the study (ledger and budget, as the tools do), run by
the real worker in-process, and marked finished in the job store the way the runner
would, so trials settle exactly as in production. e2e-test catalog, --forked.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies import candidates, lifecycle, references, scorecard
from src.mcp_server.studies.record import export_study
from src.mcp_server.studies.submit import prepare_in_sample, reserve_trial
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import requires_e2e_data
from tests.integration.mcp_server.study_fixtures import make_ctx, open_study

pytestmark = [pytest.mark.integration, requires_e2e_data]

PARAMS = {"fast_period": 5, "slow_period": 20}
IN_SAMPLE = dict(
    strategy=None,
    symbol=None,
    start=None,
    end=None,
    timeframe=None,
    catalog=None,
    starting_balance=None,
    over_budget_reason=None,
)


async def _finish(ctx, recorded: dict) -> str:
    """Run the reserved job with the real worker and record its end as the runner does."""
    store = ctx.runner.store
    job_id = recorded["job_id"]
    store.update(job_id, state="running")
    assert await run_job(store.job_dir(job_id)) == 0, store.log_tail(job_id, 30)
    run_id = store.result(job_id)["run_id"]
    store.update(job_id, state="succeeded", run_id=run_id)
    return run_id


async def _in_sample(ctx, params=PARAMS) -> str:
    job, _ = prepare_in_sample("sma-aapl", params=params, **IN_SAMPLE)
    return await _finish(ctx, reserve_trial(ctx.runner, job))


async def _benchmark(ctx, window: str) -> str:
    job, _ = references.prepare_benchmark(
        "sma-aapl",
        strategy="buy_and_hold",
        params={},
        symbol=None,
        window=window,
        start=None,
        end=None,
    )
    return await _finish(ctx, reserve_trial(ctx.runner, job))


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(ctx, symbols=["AAPL"], out_of_sample_end=date(2020, 12, 31))
    return ctx


async def test_explore_freeze_test_once_score_and_iterate(ctx, tmp_path):
    store = ctx.runner.store
    run_id = await _in_sample(ctx)
    benchmark_id = await _benchmark(ctx, "in_sample")
    assert (
        _code(
            references.prepare_benchmark,
            "sma-aapl",
            strategy="buy_and_hold",
            params={},
            symbol=None,
            window="out_of_sample",
            start=None,
            end=None,
        )
        == "invalid_study_state"
    )
    assert _code(candidates.freeze_candidate, store, "sma-aapl", benchmark_id, None) == (
        "not_a_trial"
    )

    why = "a shallower drawdown than holding is the point of this sleeve"
    frozen = candidates.freeze_candidate(
        store, "sma-aapl", run_id, "first look", benchmark_rationale=why
    )
    candidate = frozen["candidate"]
    assert candidate["version"] == 1 and candidate["params"]["fast_period"] == 5
    assert candidate["benchmark_rationale"] == why
    assert not any("benchmark_rationale" in w for w in frozen["warnings"])
    assert len(candidate["candidate_hash"]) == 64
    assert _code(prepare_in_sample, "sma-aapl", params=PARAMS, **IN_SAMPLE) == (
        "invalid_study_state"
    )
    assert _code(candidates.freeze_candidate, store, "sma-aapl", run_id, None) == (
        "invalid_study_state"
    )

    before = scorecard.get_scorecard(ctx.settings, store, "sma-aapl", None)
    assert before["gates"]["G2"]["status"] == "missing"
    assert before["evidence"]["in_sample_benchmark"] == benchmark_id
    beats = before["gates"]["G1"]["checks"]["beats_benchmark_on_one"]
    assert beats["status"] == "fail" or beats["value"]["rationale"] == why

    job, _ = candidates.prepare_out_of_sample("sma-aapl", None)
    oos_id = await _finish(ctx, reserve_trial(ctx.runner, job))
    await _benchmark(ctx, "out_of_sample")
    study = lifecycle.get_study(store, "sma-aapl")["study"]
    assert study["status"] == "tested"
    oos_row = next(r for r in study["ledger"] if r["role"] == "out_of_sample")
    assert (oos_row["start"], oos_row["end"]) == ("2020-01-01", "2020-12-31")
    assert oos_row["params"] == study["ledger"][0]["params"]
    assert study["budget"]["used"] == 1  # benchmarks and the holdout run are not trials

    card = scorecard.get_scorecard(ctx.settings, store, "sma-aapl", None)
    assert card["evidence"]["out_of_sample"] == oos_id
    assert card["gates"]["G2"]["checks"]["oos_run_once"]["status"] == "pass"
    assert card["gates"]["G2"]["checks"]["neighbourhood_sharpe"]["status"] == "missing"
    assert card["gates"]["G3"]["status"] == "missing"
    assert card["gates"]["G0"]["checks"]["benchmark_present"]["status"] == "pass"
    assert card["expectation_band"]["source_run"] == oos_id
    # Five out-of-sample trades cannot give 20 windows of 20.
    assert card["expectation_band"]["status"] == "thin"
    for gate in card["gates"].values():
        for check in gate["checks"].values():
            assert check["status"] in ("pass", "fail", "missing")

    again, _ = candidates.prepare_out_of_sample("sma-aapl", None)
    assert _code(reserve_trial, ctx.runner, again) == "out_of_sample_spent"
    overridden, _ = candidates.prepare_out_of_sample("sma-aapl", "data vendor fixed 2020 bars")
    reserve_trial(ctx.runner, overridden)
    study = lifecycle.get_study(store, "sma-aapl")["study"]
    assert study["contaminated"] and "run again" in study["contamination_reason"]

    versioned = candidates.new_candidate_version(
        store, "sma-aapl", "drawdown beyond the band", "add a time stop"
    )["study"]
    assert versioned["current_version"] == 2 and versioned["status"] == "exploring"
    assert versioned["contaminated"]
    assert _code(candidates.prepare_out_of_sample, "sma-aapl", None) == "invalid_study_state"
    assert await _in_sample(ctx, {"fast_period": 10, "slow_period": 20})
    assert lifecycle.get_study(store, "sma-aapl")["study"]["budget"]["used"] == 2

    out = export_study(
        ctx.settings,
        store,
        "sma-aapl",
        run_ids=None,
        slug="sma-aapl-v1",
        folder="Lab/results",
        overwrite=False,
    )
    record = json.loads(Path(out["files"]["study"]).read_text())
    assert record["study"]["slug"] == "sma-aapl"
    assert record["scorecard"]["status"] == "missing"  # version 2 has no candidate yet
    # The scorecard the decision was made on is kept: one per frozen version.
    assert [c["version"] for c in record["scorecards"]] == [1]
    assert record["scorecards"][0]["gates"]["G2"]["checks"]["oos_run_once"]["status"] == "fail"
    assert out["runs"] >= 5 and Path(out["files"]["json"]).exists()
    old = scorecard.get_scorecard(ctx.settings, store, "sma-aapl", 1)
    assert old["warnings"][0].startswith("CONTAMINATED")
    # The overridden second look counts from the moment it was submitted.
    assert old["gates"]["G2"]["checks"]["oos_run_once"]["status"] == "fail"


async def test_a_new_version_before_the_holdout_is_seen_stays_clean(ctx):
    store = ctx.runner.store
    run_id = await _in_sample(ctx)
    frozen = candidates.freeze_candidate(store, "sma-aapl", run_id, None)
    assert any("benchmark_rationale" in w for w in frozen["warnings"])
    study = candidates.new_candidate_version(store, "sma-aapl", "wider grid", "slow 30")["study"]
    assert not study["contaminated"] and study["current_version"] == 2
    assert _code(candidates.freeze_candidate, store, "sma-aapl", run_id, None) == "not_a_trial"


def test_new_version_needs_a_frozen_candidate_and_a_reason(ctx):
    store = ctx.runner.store
    assert _code(candidates.new_candidate_version, store, "sma-aapl", "x", "y") == (
        "invalid_study_state"
    )
    assert _code(candidates.new_candidate_version, store, "sma-aapl", " ", "y") == (
        "reason_required"
    )
    assert _code(scorecard.get_scorecard, ctx.settings, store, "sma-aapl", None) == "no_candidate"
