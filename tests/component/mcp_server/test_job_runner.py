"""JobRunner: one worker process at a time, cancel, timeout, restart recovery (S2.1, S2.2)."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.jobs.store import JobStore

pytestmark = pytest.mark.component

FAKE = Path(__file__).with_name("fake_worker.py")


def _runner(store: JobStore, timeout_s: float = 30) -> JobRunner:
    return JobRunner(
        store,
        timeout_s=timeout_s,
        worker_command=lambda job_dir: [sys.executable, str(FAKE), str(job_dir)],
    )


def _payload(behaviour: str) -> dict:
    return {"kind": "backtest", "spec": {"behaviour": behaviour}}


async def _wait(store: JobStore, job_id: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = store.status(job_id)
        if status["state"] not in ("queued", "running"):
            return status
        await asyncio.sleep(0.05)
    raise AssertionError(f"job {job_id} still {store.status(job_id)['state']}")


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")


async def test_submit_returns_immediately_and_job_succeeds(store):
    runner = _runner(store)
    started = time.monotonic()
    job_id = await runner.submit(_payload("ok-1"))
    assert time.monotonic() - started < 2
    status = await _wait(store, job_id)
    assert status["state"] == "succeeded"
    assert status["run_id"] == "ok-1"
    assert status["elapsed_s"] >= 0
    assert "fake worker ok-1" in store.log_tail(job_id, 10)


async def test_jobs_run_one_at_a_time_in_order(store):
    runner = _runner(store)
    first = await runner.submit(_payload("ok-a"))
    second = await runner.submit(_payload("ok-b"))
    assert store.status(second)["state"] == "queued"
    assert runner.queue_state()["queued"] >= 1
    a, b = await _wait(store, first), await _wait(store, second)
    assert a["finished_at"] <= b["started_at"]


async def test_worker_failure_carries_its_error(store):
    status = await _wait(store, await _runner(store).submit(_payload("fail")))
    assert status["state"] == "failed"
    assert status["error"]["code"] == "data_not_found"


async def test_crash_without_result_is_reported(store):
    status = await _wait(store, await _runner(store).submit(_payload("crash")))
    assert status["state"] == "failed"
    assert status["error"]["code"] == "worker_crashed"
    assert "3" in status["error"]["message"]


async def test_cancel_running_job_stops_it_within_five_seconds(store):
    runner = _runner(store)
    job_id = await runner.submit(_payload("ignore_term"))
    while store.status(job_id)["state"] != "running":
        await asyncio.sleep(0.05)
    started = time.monotonic()
    status = await runner.cancel(job_id)
    assert time.monotonic() - started < 5
    assert status["state"] == "cancelled"
    assert store.result(job_id) is None


async def test_cancel_queued_job_never_runs_it(store):
    runner = _runner(store)
    blocker = await runner.submit(_payload("sleep"))
    queued = await runner.submit(_payload("ok-never"))
    status = await runner.cancel(queued)
    assert status["state"] == "cancelled"
    await runner.cancel(blocker)
    await asyncio.sleep(0.5)
    assert store.status(queued)["state"] == "cancelled"
    assert store.result(queued) is None


async def test_cancel_finished_job_is_a_no_op(store):
    runner = _runner(store)
    job_id = await runner.submit(_payload("ok-done"))
    await _wait(store, job_id)
    assert (await runner.cancel(job_id))["state"] == "succeeded"


async def test_timeout_kills_the_worker(store):
    status = await _wait(store, await _runner(store, timeout_s=1).submit(_payload("sleep")))
    assert status["state"] == "failed"
    assert status["error"]["code"] == "timeout"


async def test_restart_marks_running_lost_and_requeues_queued(store):
    running = store.create(_payload("ok-x"))
    store.update(running, state="running", pid=999999)
    queued = store.create(_payload("ok-requeued"))

    runner = _runner(store)
    assert store.status(running)["state"] == "lost"
    runner.start()
    status = await _wait(store, queued)
    assert status["state"] == "succeeded"


async def test_cancel_while_the_worker_is_spawning_still_stops_it(store):
    runner = _runner(store)
    job_id = await runner.submit(_payload("ignore_term"))
    while runner.queue_state()["running"] != job_id:
        await asyncio.sleep(0)
    started = time.monotonic()
    status = await runner.cancel(job_id)
    assert time.monotonic() - started < 5
    assert status["state"] == "cancelled"
