"""Scenario step 15 over stdio (research MCP phase-5a exit check).

A study with an in-sample run, and a held sleeve that is a plain stored run over
a longer span -> how the two move together -> freeze and test the study's
candidate -> its out-of-sample run judged inside the portfolio that holds the
other sleeve -> the analysis readable, listed by the study and exported with
it. Real server, real workers, real e2e-test catalog and Postgres; every study
and run is deleted afterwards.
"""

import asyncio
import json
from uuid import uuid4

import pytest
from mcp import Client

from tests.e2e.mcp_server.conftest import call, requires_e2e_data

pytestmark = [pytest.mark.e2e, requires_e2e_data]

PORTFOLIO_TOOLS = {"get_sleeve_correlation", "run_portfolio_backtest"}
HELD = {
    "strategy": "sma_crossover",
    "symbol": "MSFT",
    "start": "2018-01-01",
    "end": "2021-06-30",
    "params": {"fast_period": 5},
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


def _study(slug: str) -> dict:
    return {
        "slug": slug,
        "title": "SMA crossover on AAPL",
        "hypothesis": "Trends persist on large caps",
        "strategy": "sma_crossover",
        "symbols": ["AAPL"],
        "in_sample_start": "2018-01-01",
        "in_sample_end": "2019-12-31",
        "out_of_sample_start": "2020-01-01",
        "out_of_sample_end": "2021-06-30",
        "param_space": {"fast_period": {"values": [5, 10]}},
        "trial_budget": 3,
    }


async def test_a_candidate_sleeve_is_judged_inside_the_held_portfolio(
    server_params, vault, created_runs, created_studies
):
    slug = f"e2e-port-{uuid4().hex[:8]}"
    created_studies.append(slug)
    study = {"study": slug}
    async with Client(server_params) as client:
        info = await call(client, "server_info")
        assert info["phase"] == 5 and PORTFOLIO_TOOLS <= set(info["capabilities"])
        assert info["limits"]["max_sleeves"] >= 2 and info["limits"]["min_portfolio_days"] <= 252

        opened = await call(client, "create_study", _study(slug))
        assert opened["ok"], opened
        a = await _run(client, "submit_backtest", {**study, "params": {"fast_period": 5}})
        held = await _run(client, "submit_backtest", HELD)  # a plain run: no study behind it
        created_runs += [a, held]

        # Two sleeves side by side, before either is proven.
        together = await call(client, "get_sleeve_correlation", {"run_ids": [a, held]})
        assert together["ok"], together
        assert together["correlation"]["matrix"][a][a] == 1.0
        assert together["window"]["start"] >= "2018-01-02"
        assert together["window"]["end"] == "2019-12-31"  # the in-sample run ends there
        assert [row["label"] for row in together["regimes"]["by_year"]] == ["2018", "2019"]
        warned = {s["run_id"]: s["warnings"][0] for s in together["sleeves"]}
        assert "in_sample run" in warned[a] and "unattributed" in warned[held]

        # The study's candidate, tested honestly, then judged beside the held sleeve.
        frozen = await call(client, "freeze_candidate", {**study, "run_id": a})
        assert frozen["ok"], frozen
        oos = await _run(client, "run_out_of_sample", study)
        created_runs.append(oos)
        sleeves = [{"run_id": held, "weight": 1}]
        judged = await call(client, "run_portfolio_backtest", {**study, "sleeves": sleeves})
        assert judged["ok"], judged
        assert judged["candidate"]["run_id"] == oos
        assert judged["candidate"]["chosen_by"] == "newest_out_of_sample"
        assert judged["weights"]["with"] == {held: 0.5, oos: 0.5}
        assert judged["window"]["start"] >= "2020-01-02" and judged["window"]["days"] >= 252
        assert set(judged["delta"]) >= {"sharpe_ratio", "max_drawdown", "cagr"}
        assert judged["with_candidate"]["metrics"]["days"] == judged["window"]["days"]
        assert [row["label"] for row in judged["regimes"]["by_year"]] == ["2020", "2021"]

        stored = await call(client, "get_analysis", {**study, "kind": "portfolio"})
        assert stored["ok"] and stored["analysis"]["analysis_id"] == judged["analysis_id"]
        state = (await call(client, "get_study", study))["study"]
        assert "portfolio" in [x["kind"] for x in state["analyses"]]
        assert state["budget"]["used"] == 1  # nothing was counted

        twice = await call(
            client, "run_portfolio_backtest", {**study, "sleeves": [{"run_id": oos}]}
        )
        assert twice["error"]["code"] == "duplicate_sleeve"

        exported = await call(client, "export_results", {**study, "slug": slug})
        assert exported["ok"], exported
    record = json.loads((vault / "Lab" / "results" / f"{slug}.study.json").read_text())
    assert record["portfolio"]["analysis_id"] == judged["analysis_id"]
