"""Scenario steps 2-5, 7 (regimes), 8, 9 and 11 over stdio (research MCP phase-2 exit check).

Open a study -> in-sample trial and benchmark -> the holdout refuses a peek ->
in-sample bars to the vault, clamped -> regimes -> freeze -> out-of-sample once
-> scorecard -> study record in the vault. Real server, real workers, real
e2e-test catalog and Postgres; every study and run is deleted afterwards.
"""

import asyncio
import json
from uuid import uuid4

import pytest
from mcp import Client

from tests.e2e.mcp_server.conftest import call, requires_e2e_data

pytestmark = [pytest.mark.e2e, requires_e2e_data]


async def _finish(client: Client, job_id: str, timeout_s: float = 180) -> dict:
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


async def test_a_study_from_open_to_scorecard(server_params, vault, created_runs, created_studies):
    slug = f"e2e-study-{uuid4().hex[:8]}"
    created_studies.append(slug)
    study = {"study": slug}
    async with Client(server_params) as client:
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
                "trial_budget": 2,
            },
        )
        assert opened["ok"], opened
        assert opened["study"]["pass_criteria"] == ["G0", "G1", "G2", "G3"]

        run_id = await _run(client, "submit_backtest", {**study, "params": {"fast_period": 5}})
        await _run(client, "submit_benchmark", study)
        peek = await call(client, "submit_backtest", {**study, "end": "2020-06-30"})
        assert peek["error"]["code"] == "holdout_locked"

        bars = await call(
            client, "export_bars", {**study, "slug": f"{slug}-bars", "end": "2021-01-01"}
        )
        assert bars["ok"] and bars["end"] == "2019-12-31" and bars["clamped"]
        regimes = await call(client, "get_regime_breakdown", {"run_id": run_id})
        assert [c["label"] for c in regimes["by_year"]] == ["2018", "2019"]

        frozen = await call(client, "freeze_candidate", {**study, "run_id": run_id})
        assert frozen["ok"], frozen
        await _run(client, "run_out_of_sample", study)
        again = await call(client, "run_out_of_sample", study)
        assert again["error"]["code"] == "out_of_sample_spent"
        await _run(client, "submit_benchmark", {**study, "window": "out_of_sample"})

        card = await call(client, "get_scorecard", study)
        assert card["ok"], card
        assert set(card["gates"]) == {"G0", "G1", "G2", "G3"}
        assert card["gates"]["G3"]["status"] == "missing"
        assert card["evidence"]["out_of_sample"] and card["evidence"]["in_sample_benchmark"]

        state = (await call(client, "get_study", study))["study"]
        assert state["status"] == "tested" and state["budget"]["used"] == 1
        assert [r["role"] for r in state["ledger"]] == [
            "in_sample",
            "benchmark",
            "out_of_sample",
            "benchmark",
        ]
        created_runs.extend(r["run_id"] for r in state["ledger"] if r["run_id"])

        found = await call(client, "search_runs", study)
        assert found["count"] == 4
        exported = await call(client, "export_results", {**study, "slug": slug})
        assert exported["ok"], exported
    record = json.loads((vault / "Lab" / "results" / f"{slug}.study.json").read_text())
    assert record["scorecard"]["study"] == slug
    assert (vault / "Lab" / "results" / f"{slug}-bars.bars.csv").is_file()
