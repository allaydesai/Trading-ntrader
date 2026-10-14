"""The scorecard's G2 and G3 rows are judged, not missing, once the robustness analyses exist.

The full phase-4 path on real runs: explore, neighbourhood sweep, Monte Carlo,
freeze, cost stress, breadth, out-of-sample, walk-forward, scorecard (steps 6,
7, 9, 10, 11 of the spec's scenario). e2e-test catalog, --forked.
"""

import pytest

from src.mcp_server.studies import (
    candidates,
    robustness,
    scorecard,
    sweeps,
    variability,
    walkforward,
)
from src.mcp_server.studies.batch import reserve_trials
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
ROBUST_GATES = {
    "G2": {
        "neighbourhood_sharpe": 0.1,
        "cost_stress_multiplier": 2,
        "breadth": {"instruments": 1, "profit_factor": 0.1},
        "deflated_sharpe_prob": 0.0,
        "oos_drawdown_within_mc": True,
        "oos_sharpe_vs_is": -10,
        "oos_profit_factor": 0.0,
    },
    "G3": {"walk_forward_efficiency": -10},
}


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(
        ctx,
        symbols=["AAPL"],
        trial_budget=40,
        param_space={"fast_period": {"values": [5, 10]}, "slow_period": {"values": [20, 30]}},
    )
    return ctx


async def _finish(ctx, job_id: str) -> str:
    store = ctx.runner.store
    store.update(job_id, state="running")
    assert await run_job(store.job_dir(job_id)) == 0, store.log_tail(job_id, 30)
    run_id = store.result(job_id)["run_id"]
    store.update(job_id, state="succeeded", run_id=run_id)
    return run_id


async def _batch(ctx, batch) -> dict:
    recorded = reserve_trials(ctx.runner, batch)
    for job in recorded["jobs"]:
        await _finish(ctx, job["job_id"])
    return recorded


async def test_every_robustness_row_is_judged_on_real_runs(ctx, monkeypatch):
    store = ctx.runner.store
    job, _ = prepare_in_sample("sma-aapl", params=PARAMS, **IN_SAMPLE)
    source = await _finish(ctx, reserve_trial(ctx.runner, job)["job_id"])
    # A neighbourhood around the chosen parameters (ints: 4/5/6 x 16/20/24, 9 cells).
    batch, _ = sweeps.prepare_sweep(
        "sma-aapl",
        grid_axes=None,
        around=PARAMS,
        around_pct=0.2,
        over_budget_reason=None,
        max_cells=200,
    )
    await _batch(ctx, batch)
    sensitivity = sweeps.run_sensitivity(store, "sma-aapl", metric="sharpe_ratio", sweep=None)
    assert sensitivity["missing"] == [] and sensitivity["recommended"] is not None
    candidates.freeze_candidate(store, "sma-aapl", source, None, benchmark_rationale="drawdown")
    mc = variability.run_monte_carlo(
        store,
        "sma-aapl",
        run_id=None,
        iterations=1000,
        method="both",
        breach_drawdown=0.2,
        seed=0,
        max_iterations=50_000,
    )
    assert mc["run_id"] == source
    stress, _ = robustness.prepare_cost_stress("sma-aapl", multiplier=None, gate_multiplier=2)
    await _batch(ctx, stress)
    breadth, _ = robustness.prepare_breadth(
        "sma-aapl", symbols=["MSFT"], window="in_sample", params=None
    )
    await _batch(ctx, breadth)
    oos, _ = candidates.prepare_out_of_sample("sma-aapl", None)
    await _finish(ctx, reserve_trial(ctx.runner, oos)["job_id"])
    wf, _ = walkforward.prepare_walk_forward(
        "sma-aapl",
        is_years=1,
        oos_years=1,
        mode="rolling",
        select_by="sharpe_ratio",
        grid_axes={"fast_period": {"values": [5, 10]}},
        max_cells=200,
    )
    await _batch(ctx, wf)

    from src.mcp_server.studies import gates as gates_module

    monkeypatch.setattr(
        gates_module, "load_gates", lambda settings: gates_module.Gates(ROBUST_GATES, "test", None)
    )
    monkeypatch.setattr(scorecard, "load_gates", gates_module.load_gates)
    card = scorecard.get_scorecard(ctx.settings, store, "sma-aapl", None)
    g2, g3 = card["gates"]["G2"]["checks"], card["gates"]["G3"]["checks"]
    for name in ROBUST_GATES["G2"]:
        assert g2[name]["status"] in ("pass", "fail"), (name, g2[name])
    assert g3["walk_forward_efficiency"]["status"] in ("pass", "fail"), g3
    assert g2["neighbourhood_sharpe"]["value"]["neighbours"] == 4  # one step on one axis
    assert g2["cost_stress_multiplier"]["value"]["multiplier"] == 2.0
    assert list(g2["breadth"]["value"]["profit_factor"]) == ["MSFT"]
    assert g2["deflated_sharpe_prob"]["value"]["trials"] >= 10
    assert g2["oos_drawdown_within_mc"]["value"]["mc_drawdown_p5"] <= 0
    used = card["evidence"]["analyses"]
    assert used["sweep"] and used["cost_stress"] and used["breadth"] and used["walk_forward"]
    assert used["monte_carlo"] == mc["analysis_id"]
    assert "G2" not in card["summary"]["missing"] and "G3" not in card["summary"]["missing"]
