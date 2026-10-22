"""Two real servers over one jobs directory, as Claude Desktop starts them.

Desktop launches one server per consumer of the same config entry (the chat and a
linked session), each over its own stdio. Whichever is second must still be able
to run research: its submit is queued in the shared directory and run by the
owner, and it can cancel the owner's job.
"""

import asyncio

import pytest
from mcp import Client

from tests.e2e.mcp_server.conftest import call, requires_e2e_data

pytestmark = [pytest.mark.e2e, requires_e2e_data]

REQUEST = {"strategy": "buy_and_hold", "symbol": "AAPL", "start": "2019-01-01", "end": "2019-12-31"}


async def _finish(client: Client, job_id: str, timeout_s: float = 180) -> dict:
    for _ in range(int(timeout_s / 0.5)):
        job = await call(client, "get_job", {"job_id": job_id})
        if job["state"] not in ("reserved", "queued", "running"):
            return job
        await asyncio.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish: {job}")


async def test_the_second_server_runs_research_through_the_first(server_params, created_runs):
    async with Client(server_params) as first:
        assert (await call(first, "server_info"))["jobs"]["owner"] is True
        async with Client(server_params) as second:
            info = (await call(second, "server_info"))["jobs"]
            assert info["owner"] is False and info["owner_process"]["pid"]

            submitted = await call(second, "submit_backtest", REQUEST)
            assert submitted["ok"], submitted
            job = await _finish(second, submitted["job_id"])
            if job.get("result"):
                created_runs.append(job["result"]["run_id"])
            assert job["state"] == "succeeded", job

            doomed = await call(second, "submit_backtest", REQUEST)
            cancelled = await call(second, "cancel_job", {"job_id": doomed["job_id"]})
            assert cancelled["ok"] and cancelled["state"] in ("cancelled", "succeeded")
            if cancelled.get("run_id"):
                created_runs.append(cancelled["run_id"])
