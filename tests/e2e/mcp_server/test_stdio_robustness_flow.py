"""Scenario steps 6, 7, 9, 10 and 11 over stdio (research MCP phase-4 exit check).

Open a study -> a sweep of counted cells and its sensitivity map -> Monte Carlo on
the chosen run -> freeze -> cost stress, breadth on an untuned symbol, the
out-of-sample run -> walk-forward over the span -> every analysis readable ->
a scorecard whose G2 and G3 rows are judged, not missing. Real server, real
workers, real e2e-test catalog and Postgres; every study and run is deleted
afterwards.
"""

import asyncio
from uuid import uuid4

import pytest
from mcp import Client

from tests.e2e.mcp_server.conftest import call, requires_e2e_data

pytestmark = [pytest.mark.e2e, requires_e2e_data]

ROBUSTNESS_TOOLS = {
    "submit_sweep",
    "run_sensitivity",
    "run_monte_carlo",
    "submit_breadth",
    "submit_cost_stress",
    "submit_walk_forward",
    "get_analysis",
}


async def _finish(client: Client, job_id: str, timeout_s: float = 240) -> dict:
    for _ in range(int(timeout_s / 0.5)):
        job = await call(client, "get_job", {"job_id": job_id})
        if job["state"] not in ("queued", "running"):
            assert job["state"] == "succeeded", job
            return job
        await asyncio.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish")


async def _run(client: Client, tool: str, args: dict) -> str:
    submitted = await call(client, tool, args)
    assert submitted["ok"], submitted
    return (await _finish(client, submitted["job_id"]))["result"]["run_id"]


async def _batch(client: Client, tool: str, args: dict, created_runs: list) -> dict:
    submitted = await call(client, tool, args)
    assert submitted["ok"], submitted
    for job in submitted["jobs"]:
        created_runs.append((await _finish(client, job["job_id"]))["result"]["run_id"])
    return submitted


async def test_robustness_from_sweep_to_a_judged_scorecard(
    server_params, vault, created_runs, created_studies
):
    slug = f"e2e-robust-{uuid4().hex[:8]}"
    created_studies.append(slug)
    study = {"study": slug}
    async with Client(server_params) as client:
        info = await call(client, "server_info")
        assert info["phase"] == 5 and ROBUSTNESS_TOOLS <= set(info["capabilities"])
        assert info["limits"]["max_sweep_cells"] >= 2

        opened = await call(
            client,
            "create_study",
            {
                "slug": slug,
                "title": "SMA crossover on AAPL",
                "hypothesis": "Trends persist on large caps",
                "strategy": "sma_crossover",
                "symbols": ["AAPL"],
                "in_sample_start": "2018-01-01",
                "in_sample_end": "2019-12-31",
                "out_of_sample_start": "2020-01-01",
                "out_of_sample_end": "2020-12-31",
                "param_space": {"fast_period": {"values": [5, 10]}},
                "trial_budget": 3,
            },
        )
        assert opened["ok"], opened

        # Step 6: the grid as counted trials, then the plateau map.
        sweep = await _batch(client, "submit_sweep", study, created_runs)
        assert sweep["count"] == 2 and sweep["budget"]["used"] == 2
        too_many = await call(client, "submit_sweep", study)
        assert too_many["error"]["code"] == "over_budget"
        sensitivity = await call(client, "run_sensitivity", study)
        assert sensitivity["ok"] and sensitivity["recommended"]["params"]["fast_period"] in (5, 10)
        run_id = sensitivity["recommended"]["run_id"]
        created_runs.append(run_id)

        # Step 7: ranges of outcomes on the chosen run.
        # Before a freeze the default is the newest in-sample run; name the chosen one.
        mc = await call(client, "run_monte_carlo", {**study, "run_id": run_id, "iterations": 1000})
        assert mc["ok"] and mc["run_id"] == run_id and "bootstrap" in mc["simulations"]
        assert mc["confidence_intervals"]["win_rate"]["point"] is not None

        frozen = await call(client, "freeze_candidate", {**study, "run_id": run_id})
        assert frozen["ok"], frozen
        stress = await _batch(client, "submit_cost_stress", study, created_runs)
        assert stress["jobs"][0]["cell"] == {"cost_multiplier": 2.0}
        breadth = await _batch(
            client, "submit_breadth", {**study, "symbols": ["MSFT"]}, created_runs
        )
        assert breadth["role"] == "breadth"
        created_runs.append(await _run(client, "run_out_of_sample", study))

        # Step 10: walk-forward over the span, evaluated on the first read after the runs.
        wf = await _batch(
            client, "submit_walk_forward", {**study, "is_years": 1, "oos_years": 1}, created_runs
        )
        analysis = await call(client, "get_analysis", {**study, "analysis_id": wf["analysis_id"]})
        assert analysis["ok"] and analysis["analysis"]["status"] == "completed"
        result = analysis["analysis"]["result"]
        assert result["method"] == "sliced" and len(result["windows"]) == 2

        # Step 11: every robustness row judged.
        card = await call(client, "get_scorecard", study)
        assert card["ok"], card
        g2, g3 = card["gates"]["G2"]["checks"], card["gates"]["G3"]["checks"]
        for name in ("neighbourhood_sharpe", "deflated_sharpe_prob", "oos_drawdown_within_mc"):
            assert g2[name]["status"] in ("pass", "fail"), (name, g2[name])
        assert g3["walk_forward_efficiency"]["status"] in ("pass", "fail"), g3
        assert card["evidence"]["analyses"]["walk_forward"] == wf["analysis_id"]
        state = (await call(client, "get_study", study))["study"]
        assert state["budget"]["used"] == 2
        assert sorted(a["kind"] for a in state["analyses"]) == [
            "breadth",
            "cost_stress",
            "monte_carlo",
            "sensitivity",
            "sweep",
            "walk_forward",
        ]
        cancelled = await call(client, "cancel_job", {"analysis": wf["analysis_id"]})
        assert cancelled["ok"] and cancelled["cancelled"] == []
