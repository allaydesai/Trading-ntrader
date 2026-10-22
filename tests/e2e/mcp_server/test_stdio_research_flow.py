"""Scenario steps 4-5 through the real server over stdio (research MCP phase-1 exit check).

validate -> submit strategy and buy-and-hold -> poll -> read and compare runs ->
file them in a (temporary) vault, against the real e2e-test catalog and Postgres,
with a real worker process per job.
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


async def test_validate_run_compare_and_file(server_params, created_runs, vault):
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

        run_ids = [job["result"]["run_id"] for job in jobs]
        run = await call(client, "get_run", {"run_id": run_ids[0]})
        assert run["provenance"]["git_commit"]
        comparison = await call(client, "compare_runs", {"run_ids": run_ids})
        assert [r["kind"] for r in comparison["rows"]] == ["strategy", "benchmark"]

        exported = await call(client, "export_results", {"run_ids": run_ids, "slug": "e2e-phase1"})
        assert exported["ok"], exported
        assert (vault / "Lab" / "results" / "e2e-phase1.json").is_file()
        refused = await call(
            client,
            "export_results",
            {"run_ids": run_ids, "slug": "x", "folder": "Strategies"},
        )
        assert refused["error"]["code"] == "folder_not_allowed"


async def test_missing_data_fails_fast_without_queueing(server_params):
    async with Client(server_params) as client:
        result = await call(
            client, "submit_backtest", {"strategy": "sma_crossover", **WINDOW, "symbol": "ZZZZQ"}
        )
    assert result["ok"] is False
    assert result["error"]["details"]["errors"][0]["code"] == "symbol_not_in_catalog"


def test_stdout_stays_clean_while_a_worker_runs(server_params, created_runs):
    """The engine, its logger and Rich all write to stdout: none of it may reach the protocol."""
    import time

    from tests.component.mcp_server.raw_stdio import RawStdioServer, non_protocol_lines

    server = RawStdioServer(server_params.env)
    try:
        server.initialize()
        submitted = server.call_tool("submit_backtest", {"strategy": "sma_crossover", **WINDOW})
        assert submitted["ok"], submitted
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = server.call_tool("get_job", {"job_id": submitted["job_id"]})
            if job["state"] not in ("queued", "running"):
                break
            time.sleep(0.25)
        if job.get("result"):
            created_runs.append(job["result"]["run_id"])
        assert job["state"] == "succeeded", job
        assert job["log_tail"], "worker output belongs in the job log"
    finally:
        lines = server.close()
    assert non_protocol_lines(lines) == []
