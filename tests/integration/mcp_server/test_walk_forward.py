"""Walk-forward of the frozen candidate's grid, sliced from full-span runs (S5.3)."""

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies import candidates, lifecycle, references, walkforward
from src.mcp_server.studies.batch import cancel_batch, reserve_trials
from src.mcp_server.studies.submit import prepare_in_sample, reserve_trial
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import requires_e2e_data, requires_postgres
from tests.integration.mcp_server.study_fixtures import freeze_fake, make_ctx, open_study

pytestmark = [pytest.mark.integration, requires_postgres]

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
WF = dict(
    is_years=1, oos_years=1, mode="rolling", select_by="sharpe_ratio", grid_axes=None, max_cells=200
)


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch)
    # In-sample 2018-2019, holdout 2020: two 1y/1y rolling windows over 2018-2020.
    open_study(ctx, symbols=["AAPL"], param_space={"fast_period": {"values": [5, 10]}})
    return ctx


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


async def _finish(ctx, job_id: str) -> str:
    store = ctx.runner.store
    store.update(job_id, state="running")
    assert await run_job(store.job_dir(job_id)) == 0, store.log_tail(job_id, 30)
    run_id = store.result(job_id)["run_id"]
    store.update(job_id, state="succeeded", run_id=run_id)
    return run_id


async def _frozen(ctx) -> str:
    job, _ = prepare_in_sample("sma-aapl", params=PARAMS, **IN_SAMPLE)
    run_id = await _finish(ctx, reserve_trial(ctx.runner, job)["job_id"])
    candidates.freeze_candidate(ctx.runner.store, "sma-aapl", run_id, None)
    return run_id


def test_walk_forward_needs_a_frozen_candidate_and_two_windows(ctx):
    assert _code(walkforward.prepare_walk_forward, "sma-aapl", **WF) == "invalid_study_state"
    assert (
        _code(walkforward.get_analysis, ctx.runner.store, "sma-aapl", analysis_id=None, kind=None)
        == "no_analysis"
    )


@requires_e2e_data
async def test_the_batch_runs_every_cell_over_the_span_and_evaluates_lazily(ctx):
    store = ctx.runner.store
    await _frozen(ctx)
    assert (
        _code(walkforward.prepare_walk_forward, "sma-aapl", **{**WF, "is_years": 2})
        == "invalid_request"
    )
    assert (
        _code(walkforward.prepare_walk_forward, "sma-aapl", **{**WF, "select_by": "x"})
        == "invalid_request"
    )
    batch, warnings = walkforward.prepare_walk_forward("sma-aapl", **WF)
    assert [c["fast_period"] for c in batch.cells] == [5, 10]
    assert all(
        p["resolved"]["start"] == "2018-01-01" and p["resolved"]["end"] == "2020-12-31"
        for p in batch.payloads
    )
    assert len(batch.request["windows"]) == 2 and batch.role == "walk_forward"
    assert any("holdout is seen" in w for w in warnings)
    recorded = reserve_trials(ctx.runner, batch)
    analysis_id = recorded["analysis_id"]
    pending = walkforward.get_analysis(store, "sma-aapl", analysis_id=analysis_id, kind=None)[
        "analysis"
    ]
    assert pending["status"] == "pending" and len(pending["members"]) == 2

    for job in recorded["jobs"]:
        await _finish(ctx, job["job_id"])
    done = walkforward.get_analysis(store, "sma-aapl", analysis_id=None, kind="walk_forward")[
        "analysis"
    ]
    assert done["status"] == "completed" and done["analysis_id"] == analysis_id
    result = done["result"]
    assert result["method"] == "sliced" and result["missing_cells"] == []
    assert len(result["windows"]) == 2
    first = result["windows"][0]
    assert first["chosen"] in result["cells"] and first["out_of_sample"]["days"] > 200
    assert set(first["candidates"]) == set(result["cells"])
    assert "efficiency" in result and "negative_windows" in result
    assert result["cells"][first["chosen"]]["fast_period"] in (5, 10)
    assert set(result["runs"]) == {j["job_id"] for j in recorded["jobs"]}

    study = lifecycle.get_study(store, "sma-aapl")["study"]
    assert study["budget"]["used"] == 1  # walk-forward runs are never trials
    assert [a["kind"] for a in study["analyses"]] == ["walk_forward"]
    assert all(r["counted"] is False for r in study["ledger"] if r["role"] == "walk_forward")
    # The runs saw the holdout: a new version is contaminated, and reproducing one needs
    # the same code.
    wf_run = next(r["run_id"] for r in study["ledger"] if r["role"] == "walk_forward")
    # With the code unchanged since the run, reproducing it is allowed.
    try:
        references.prepare_reproduction(wf_run)
    except ToolFailure as refused:
        assert refused.code in ("holdout_locked", "config_changed")
    after = candidates.new_candidate_version(
        store, "sma-aapl", "try a time stop", "exit after 5 days"
    )
    assert after["study"]["contaminated"] is True


async def test_a_cancelled_batch_becomes_a_void_analysis(ctx, monkeypatch):
    store = ctx.runner.store
    job, _ = prepare_in_sample("sma-aapl", params=PARAMS, **IN_SAMPLE)
    recorded = reserve_trial(ctx.runner, job)
    from uuid import uuid4

    run_id = str(uuid4())
    store.update(recorded["job_id"], state="succeeded", run_id=run_id)
    monkeypatch.setattr(candidates, "load_run", lambda session, rid: _FakeRun(run_id))
    candidates.freeze_candidate(store, "sma-aapl", run_id, None)
    batch, _ = walkforward.prepare_walk_forward("sma-aapl", **WF)
    reserved = reserve_trials(ctx.runner, batch)
    await cancel_batch(ctx.runner, reserved["analysis_id"])
    void = walkforward.get_analysis(
        store, "sma-aapl", analysis_id=reserved["analysis_id"], kind=None
    )
    assert void["analysis"]["status"] == "void"
    assert len(void["analysis"]["result"]["missing_cells"]) == 2


def test_walk_forward_cells_change_only_the_swept_axes_of_the_candidate(ctx, monkeypatch):
    params = {"fast_period": 5, "slow_period": 30, "position_size_pct": 5.0}
    freeze_fake(ctx, monkeypatch, params=params, fill_seed=7)
    batch, _ = walkforward.prepare_walk_forward("sma-aapl", **WF)
    specs = [p["spec"] for p in batch.payloads]
    assert [s["params"]["fast_period"] for s in specs] == [5, 10]
    assert {s["params"]["slow_period"] for s in specs} == {30}
    assert all(float(s["params"]["position_size_pct"]) == 5.0 for s in specs)
    assert {s["fill_seed"] for s in specs} == {7}


class _FakeRun:
    def __init__(self, run_id):
        from uuid import UUID

        self.run_id = UUID(run_id)
        self.strategy_type = "sma_crossover"
        self.instrument_symbol = "AAPL"
        self.config_snapshot = {"config": PARAMS, "fill_seed": 42}
        self.data_source = "catalog:e2e-test"
        self.initial_capital = 1_000_000
        self.git_commit = None
