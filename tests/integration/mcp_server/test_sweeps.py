"""Sweeps as counted batches, and the sensitivity map over them (S3.1, S3.2).

Reservation, budget, cancellation and the stranded-job release need Postgres only;
the map over real runs needs the e2e-test catalog (--forked).
"""

from uuid import uuid4

import pytest

from src.db import session_sync
from src.db.repositories.research_repository import SyncResearchRepository
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import TERMINAL_STATES
from src.mcp_server.studies import candidates, evidence_robustness, ledger, lifecycle, sweeps
from src.mcp_server.studies.batch import cancel_batch, reserve_trials
from src.mcp_server.studies.checks_base import RunFacts
from src.mcp_server.studies.submit import prepare_in_sample
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import requires_e2e_data, requires_postgres
from tests.integration.mcp_server.study_fixtures import freeze_fake, make_ctx, open_study

pytestmark = [pytest.mark.integration, requires_postgres]

SWEEP = dict(grid_axes=None, around=None, around_pct=0.2, over_budget_reason=None, max_cells=200)


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(
        ctx,
        symbols=["AAPL"],
        trial_budget=6,
        param_space={
            "fast_period": {"values": [5, 10]},
            "slow_period": {"min": 20, "max": 30, "step": 10},
        },
    )
    return ctx


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


def test_a_sweep_expands_the_declared_space_into_counted_in_sample_trials(ctx):
    batch, warnings = sweeps.prepare_sweep("sma-aapl", **SWEEP)
    assert [c for c in batch.cells] == [
        {"fast_period": 5, "slow_period": 20},
        {"fast_period": 5, "slow_period": 30},
        {"fast_period": 10, "slow_period": 20},
        {"fast_period": 10, "slow_period": 30},
    ]
    assert batch.role == "in_sample" and batch.kind == "sweep"
    assert all(p["study"]["role"] == "in_sample" for p in batch.payloads)
    assert all(p["resolved"]["start"] == "2018-01-01" for p in batch.payloads)
    recorded = reserve_trials(ctx.runner, batch)
    assert recorded["count"] == 4 and [j["trial"] for j in recorded["jobs"]] == [1, 2, 3, 4]
    assert recorded["budget"] == {"used": 4, "budget": 6, "remaining": 2}
    store = ctx.runner.store
    assert all(store.status(j["job_id"])["state"] == "reserved" for j in recorded["jobs"])
    study = lifecycle.get_study(store, "sma-aapl")["study"]
    assert [r["params"]["slow_period"] for r in study["ledger"]] == [20, 30, 20, 30]
    assert study["budget"]["used"] == 4


def test_the_whole_batch_is_refused_before_anything_is_reserved_past_the_budget(ctx):
    batch, _ = sweeps.prepare_sweep("sma-aapl", **SWEEP)
    reserve_trials(ctx.runner, batch)
    again, _ = sweeps.prepare_sweep("sma-aapl", **SWEEP)
    assert _code(reserve_trials, ctx.runner, again) == "over_budget"
    assert len(ctx.runner.store.names()) == 4  # nothing new was reserved
    with_reason, _ = sweeps.prepare_sweep("sma-aapl", **{**SWEEP, "over_budget_reason": "recorded"})
    recorded = reserve_trials(ctx.runner, with_reason)
    assert recorded["budget"]["used"] == 8
    study = lifecycle.get_study(ctx.runner.store, "sma-aapl")["study"]
    over = [e for e in study["events"] if e["kind"] == "over_budget"]
    assert len(over) == 1 and len(over[0]["details"]["job_ids"]) == 4


def test_around_probes_the_neighbourhood_and_drops_duplicate_cells(ctx):
    batch, warnings = sweeps.prepare_sweep(
        "sma-aapl", **{**SWEEP, "around": {"fast_period": 5, "slow_period": 20}}
    )
    # fast 4/5/6 x slow 16/20/24 = 9 cells, all distinct
    assert len(batch.cells) == 9 and batch.request["around"] == {
        "fast_period": 5,
        "slow_period": 20,
    }
    assert any("outside the study's declared space" in w for w in warnings)
    dup, warnings = sweeps.prepare_sweep(
        "sma-aapl", **{**SWEEP, "grid_axes": {"fast_period": {"values": [5, 5, 10]}}}
    )
    assert len(dup.cells) == 2
    assert any("same run" in w for w in warnings)


def test_a_sweep_too_large_or_an_invalid_grid_is_refused(ctx):
    assert _code(sweeps.prepare_sweep, "sma-aapl", **{**SWEEP, "max_cells": 3}) == "sweep_too_large"
    bad = {"fast_period": {"min": 5}}
    assert _code(sweeps.prepare_sweep, "sma-aapl", **{**SWEEP, "grid_axes": bad}) == "invalid_grid"
    assert _code(sweeps.prepare_sweep, "sma-aapl", **{**SWEEP, "around": {"fast_period": 5}}) == (
        "invalid_grid"
    )
    unknown = {"nope": {"values": [1]}}
    assert _code(sweeps.prepare_sweep, "sma-aapl", **{**SWEEP, "grid_axes": unknown}) == (
        "validation_failed"
    )


async def test_cancel_batch_stops_every_unfinished_member(ctx):
    batch, _ = sweeps.prepare_sweep("sma-aapl", **SWEEP)
    recorded = reserve_trials(ctx.runner, batch)
    store = ctx.runner.store
    store.update(recorded["jobs"][0]["job_id"], state="succeeded", run_id=str(uuid4()))
    result = await cancel_batch(ctx.runner, recorded["analysis_id"])
    assert result["already_finished"] == [recorded["jobs"][0]["job_id"]]
    assert len(result["cancelled"]) == 3
    assert all(store.status(j)["state"] in TERMINAL_STATES for j in result["cancelled"])
    study = lifecycle.get_study(store, "sma-aapl")["study"]
    assert study["budget"]["used"] == 1  # voided members refund the budget


def test_a_job_left_reserved_is_queued_at_the_next_settle(ctx):
    batch, _ = sweeps.prepare_sweep("sma-aapl", **SWEEP)
    recorded = reserve_trials(ctx.runner, batch)  # committed, never enqueued: a crash
    store = ctx.runner.store
    lifecycle.get_study(store, "sma-aapl")
    assert all(store.status(j["job_id"])["state"] == "queued" for j in recorded["jobs"])


def test_a_sweep_is_allowed_after_a_freeze_but_a_single_trial_is_not(ctx, monkeypatch):
    batch, _ = sweeps.prepare_sweep(
        "sma-aapl", **{**SWEEP, "grid_axes": {"fast_period": {"values": [5]}}}
    )
    recorded = reserve_trials(ctx.runner, batch)
    store = ctx.runner.store
    job_id = recorded["jobs"][0]["job_id"]
    # Pretend the run finished and freeze it: a frozen study still accepts sweeps.
    run_id = str(uuid4())
    store.update(job_id, state="succeeded", run_id=run_id)
    monkeypatch.setattr(candidates, "load_run", lambda session, rid: _FakeRun(run_id))
    candidates.freeze_candidate(store, "sma-aapl", run_id, None)
    again, _ = sweeps.prepare_sweep("sma-aapl", **SWEEP)
    assert again.statuses == sweeps.SWEEP_STATUSES
    assert (
        _code(
            prepare_in_sample,
            "sma-aapl",
            strategy=None,
            symbol=None,
            start=None,
            end=None,
            timeframe=None,
            catalog=None,
            params={},
            starting_balance=None,
            over_budget_reason=None,
        )
        == "invalid_study_state"
    )


CENTRE = {"fast_period": 5, "slow_period": 20}
OTHER = {**CENTRE, "position_size_pct": 5.0}  # a parameter the study does not declare


def test_around_carries_the_parameters_it_does_not_sweep(ctx):
    batch, _ = sweeps.prepare_sweep("sma-aapl", **{**SWEEP, "around": OTHER})
    assert len(batch.payloads) == 9
    assert all(float(p["spec"]["params"]["position_size_pct"]) == 5.0 for p in batch.payloads)


def test_a_sweep_after_a_freeze_changes_only_the_swept_axes_of_the_candidate(ctx, monkeypatch):
    """The candidate's symbol, fill seed and unswept parameters, not the study's defaults."""
    freeze_fake(ctx, monkeypatch, params=OTHER, symbol="MSFT", fill_seed=7)
    for overrides in ({"around": CENTRE}, {"grid_axes": {"fast_period": {"values": [5, 10]}}}):
        batch, _ = sweeps.prepare_sweep("sma-aapl", **{**SWEEP, **overrides})
        specs = [p["spec"] for p in batch.payloads]
        assert {s["symbol"] for s in specs} == {"MSFT"}, overrides
        assert {s["fill_seed"] for s in specs} == {7}, overrides
        assert all(float(s["params"]["position_size_pct"]) == 5.0 for s in specs), overrides
        assert batch.request["symbol"] == "MSFT"
    assert {s["params"]["slow_period"] for s in specs} == {20}


def _neighbourhood(ctx):
    with session_sync.get_sync_session() as session:  # patched by isolated_results
        repo = SyncResearchRepository(session)
        study = lifecycle.load_study(repo, "sma-aapl")
        ledger.settle(repo, ctx.runner.store, study)
        candidate = candidates.current_candidate(repo, study)
        return evidence_robustness.neighbourhood_facts(
            session,
            repo,
            study,
            candidate,
            lambda s, run_id: RunFacts(str(run_id), {"sharpe_ratio": 1.0}, git_dirty=False),
        )


def _succeed(ctx, recorded, *, cancel=()) -> None:
    for job in recorded["jobs"]:
        state = "cancelled" if job["cell"] in cancel else "succeeded"
        run_id = None if state == "cancelled" else str(uuid4())
        ctx.runner.store.update(job["job_id"], state=state, run_id=run_id)


def test_the_neighbourhood_is_judged_only_on_runs_of_the_exact_candidate(ctx, monkeypatch):
    """A sweep on another seed is not the candidate's neighbourhood, and a gap is counted."""
    early, _ = sweeps.prepare_sweep(
        "sma-aapl", **{**SWEEP, "around": CENTRE, "over_budget_reason": "test"}
    )
    _succeed(ctx, reserve_trials(ctx.runner, early))
    freeze_fake(ctx, monkeypatch, params=CENTRE, fill_seed=7)
    assert _neighbourhood(ctx) is None  # every early cell ran on the default seed
    late, _ = sweeps.prepare_sweep(
        "sma-aapl", **{**SWEEP, "around": CENTRE, "over_budget_reason": "test"}
    )
    recorded = reserve_trials(ctx.runner, late)
    _succeed(ctx, recorded, cancel=[{"fast_period": 4, "slow_period": 20}])
    facts = _neighbourhood(ctx)
    assert facts is not None and facts.sweep == recorded["analysis_id"]
    assert facts.missing_neighbours == 1 and len(facts.neighbours) == 3


class _FakeRun:
    def __init__(self, run_id):
        from uuid import UUID

        self.run_id = UUID(run_id)
        self.strategy_type = "sma_crossover"
        self.instrument_symbol = "AAPL"
        self.config_snapshot = {"config": {"fast_period": 5, "slow_period": 20}, "fill_seed": 42}
        self.data_source = "catalog:e2e-test"
        self.initial_capital = 1_000_000
        self.git_commit = None


def test_sensitivity_without_a_sweep_or_with_a_bad_metric_is_refused(ctx):
    store = ctx.runner.store
    assert _code(sweeps.run_sensitivity, store, "sma-aapl", metric="sharpe_ratio", sweep=None) == (
        "no_sweep"
    )
    assert _code(
        sweeps.run_sensitivity, store, "sma-aapl", metric="max_drawdown_date", sweep=None
    ) == ("invalid_metric")


@requires_e2e_data
async def test_the_sensitivity_map_over_real_runs(ctx):
    """Two cells run for real; the map scores them and is stored as an analysis."""
    batch, _ = sweeps.prepare_sweep(
        "sma-aapl", **{**SWEEP, "grid_axes": {"fast_period": {"values": [5, 10]}}}
    )
    recorded = reserve_trials(ctx.runner, batch)
    store = ctx.runner.store
    for job in recorded["jobs"]:
        store.update(job["job_id"], state="running")
        assert await run_job(store.job_dir(job["job_id"])) == 0, store.log_tail(job["job_id"], 30)
        store.update(job["job_id"], state="succeeded", run_id=store.result(job["job_id"])["run_id"])
    result = sweeps.run_sensitivity(store, "sma-aapl", metric="sharpe_ratio", sweep=None)
    assert result["sweep"] == recorded["analysis_id"]
    assert len(result["cells"]) == 2 and result["missing"] == []
    assert {c["params"]["fast_period"] for c in result["cells"]} == {5, 10}
    assert all(c["run_id"] and c["stability"] is not None for c in result["cells"])
    assert result["recommended"] is not None and result["raw_best"] is not None
    study = lifecycle.get_study(store, "sma-aapl")["study"]
    assert study["budget"]["used"] == 2
    partial = sweeps.run_sensitivity(
        store, "sma-aapl", metric="total_return", sweep=recorded["analysis_id"]
    )
    assert partial["metric"] == "total_return"
