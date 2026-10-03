"""Scenario steps 4-5 through the real server over stdio (research MCP phase-1 exit check).

validate -> submit strategy and buy-and-hold -> poll -> read runs, against the
real e2e-test catalog and Postgres, with a real worker process per job.
"""

import asyncio

import pytest
from mcp import Client

from tests.e2e.mcp_server.conftest import call, requires_e2e_data

pytestmark = [pytest.mark.e2e, requires_e2e_data]

WINDOW = {"symbol": "AAPL", "start": "2018-01-01", "end": "2019-12-31"}


async def _finish(client: Client, job_id: str, timeout_s: float = 180) -> dict:
    for _ in range(int(timeout_s / 0.5)):
        job = await call(client, "get_job", {"job_id": job_id})
        if job["state"] not in ("queued", "running"):
            return job
        await asyncio.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish: {job}")


async def test_validate_run_and_benchmark(server_params, created_runs):
    async with Client(server_params) as client:
        info = await call(client, "server_info")
        assert info["database"]["ok"] and "e2e-test" in info["catalogs"]["names"]

        check = await call(
            client, "validate_config", {"strategy": "sma_crossover", **WINDOW, "params": {}}
        )
        assert check["ok"], check

        strategy = await call(
            client,
            "submit_backtest",
            {"strategy": "sma_crossover", **WINDOW, "params": {"fast_period": 5}},
        )
        benchmark = await call(client, "submit_backtest", {"strategy": "buy_and_hold", **WINDOW})
        jobs = [await _finish(client, j["job_id"]) for j in (strategy, benchmark)]
        for job in jobs:
            if job.get("result"):
                created_runs.append(job["result"]["run_id"])
        for job in jobs:
            assert job["state"] == "succeeded", job
            assert job["result"]["config_hash"] == job["request"]["config_hash"]
        assert jobs[1]["result"]["headline"]["total_trades"] == 1


async def test_missing_data_fails_fast_without_queueing(server_params):
    async with Client(server_params) as client:
        result = await call(
            client, "submit_backtest", {"strategy": "sma_crossover", **WINDOW, "symbol": "ZZZZQ"}
        )
    assert result["ok"] is False
    assert result["error"]["details"]["errors"][0]["code"] == "symbol_not_in_catalog"
