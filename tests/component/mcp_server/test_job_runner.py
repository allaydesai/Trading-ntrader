"""JobRunner: one worker process at a time, cancel, timeout, restart recovery (S2.1, S2.2)."""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.recovery import worker_alive
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.jobs.store import JobStore

pytestmark = pytest.mark.component

FAKE = Path(__file__).with_name("fake_worker.py")


def _command(job_dir: Path) -> list[str]:
    return [sys.executable, str(FAKE), str(job_dir)]


def _runner(store: JobStore, timeout_s: float = 30, **kwargs) -> JobRunner:
    return JobRunner(store, timeout_s=timeout_s, worker_command=_command, **kwargs)


def _orphan(store: JobStore, behaviour: str) -> tuple[str, subprocess.Popen]:
    """A job a previous server left running: its worker is alive in its own session."""
    job_id = store.create(_payload(behaviour))
    proc = subprocess.Popen(
        _command(store.job_dir(job_id)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    store.update(job_id, state="running", started_at="2026-10-03T00:00:00+00:00", pid=proc.pid)
    return job_id, proc


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


# --- one owning server per jobs directory -----------------------------------------


async def test_second_server_leaves_the_first_ones_jobs_alone(store):
    first = _runner(store)
    running = await first.submit(_payload("sleep"))
    queued = await first.submit(_payload("ok-mine"))
    while store.status(running)["state"] != "running":
        await asyncio.sleep(0.05)

    second = _runner(store)
    second.start()
    await asyncio.sleep(0.3)

    assert store.status(running)["state"] == "running"
    assert store.status(queued)["state"] == "queued"
    assert second.queue_state()["owner"] is False
    with pytest.raises(ToolFailure) as exc:
        await second.submit(_payload("ok-theirs"))
    assert exc.value.code == "another_server_active"
    with pytest.raises(ToolFailure) as exc:
        await second.cancel(running)
    assert exc.value.code == "another_server_active"
    await first.cancel(running)


async def test_a_server_takes_over_once_the_owner_is_gone(store):
    first = _runner(store)
    second = _runner(store)
    assert second.queue_state()["owner"] is False

    first.release()
    status = await _wait(store, await second.submit(_payload("ok-after")))

    assert status["state"] == "succeeded"
    assert second.queue_state()["owner"] is True


# --- recovery of jobs a previous server left running -------------------------------


async def test_live_orphan_worker_is_adopted_and_finishes_before_the_queue(store):
    orphan, proc = _orphan(store, "slow_ok")
    queued = store.create(_payload("ok-next"))
    try:
        runner = _runner(store)
        assert store.status(orphan)["state"] == "running"
        runner.start()

        first, second = await _wait(store, orphan), await _wait(store, queued)

        assert first["state"] == "succeeded"
        assert first["run_id"] == "slow_ok"
        assert first["finished_at"] <= second["started_at"]
    finally:
        proc.kill()
        proc.wait()


async def test_adopted_worker_can_be_cancelled(store):
    orphan, proc = _orphan(store, "sleep")
    try:
        runner = _runner(store)
        runner.start()
        while runner.queue_state()["running"] != orphan:
            await asyncio.sleep(0.05)
        proc_reaper = asyncio.get_running_loop().run_in_executor(None, proc.wait)
        status = await runner.cancel(orphan)
        await proc_reaper
        assert status["state"] == "cancelled"
    finally:
        proc.kill()


async def test_finished_orphan_is_finalised_from_its_result(store):
    job_id = store.create(_payload("ok-done"))
    store.update(job_id, state="running", pid=999999)
    store.write_json(job_id, "result.json", {"status": "ok", "run_id": "saved-by-orphan"})

    _runner(store)

    status = store.status(job_id)
    assert status["state"] == "succeeded"
    assert status["run_id"] == "saved-by-orphan"


async def test_lost_job_whose_run_was_saved_is_reported_succeeded(store):
    job_id = store.create(_payload("ok-x"))
    store.update(job_id, state="running", pid=999999)

    _runner(store, find_run=lambda job: "run-in-db")

    status = store.status(job_id)
    assert status["state"] == "succeeded"
    assert status["run_id"] == "run-in-db"


def test_a_reused_pid_is_not_mistaken_for_the_worker():
    assert worker_alive(os.getpid(), "20261003T000000000000-abcdef") is False


# --- a saved run is never reported cancelled ---------------------------------------


async def test_cancel_after_the_worker_saved_its_run_reports_success(store):
    runner = _runner(store)
    job_id = await runner.submit(_payload("ok_then_hang"))
    while store.result(job_id) is None:
        await asyncio.sleep(0.05)

    status = await runner.cancel(job_id)

    assert status["state"] == "succeeded"
    assert status["run_id"] == "ok_then_hang"
    assert status["cancel_requested"] is True


async def test_cancel_of_a_run_already_in_the_database_reports_success(store):
    """Killed between the commit and result.json: the run exists, so say so."""
    runner = _runner(store, find_run=lambda job: "run-in-db")
    job_id = await runner.submit(_payload("sleep"))
    while store.status(job_id).get("pid") is None:
        await asyncio.sleep(0.05)

    status = await runner.cancel(job_id)

    assert status["state"] == "succeeded"
    assert status["run_id"] == "run-in-db"
    assert status["cancel_requested"] is True


# --- the queue survives a broken job ------------------------------------------------


async def test_deleted_job_directory_does_not_stop_the_queue(store):
    runner = _runner(store)
    blocker = await runner.submit(_payload("sleep"))
    victim = await runner.submit(_payload("ok-victim"))
    survivor = await runner.submit(_payload("ok-survivor"))
    shutil.rmtree(store.root / victim)

    await runner.cancel(blocker)

    assert (await _wait(store, survivor))["state"] == "succeeded"


async def test_spawn_failure_fails_the_job_and_the_queue_carries_on(store):
    def command(job_dir: Path) -> list[str]:
        if "bad" in (job_dir / "request.json").read_text():
            return ["/nonexistent/interpreter"]
        return _command(job_dir)

    runner = JobRunner(store, timeout_s=30, worker_command=command)
    bad = await runner.submit(_payload("bad"))
    good = await runner.submit(_payload("ok-good"))

    assert (await _wait(store, bad))["error"]["code"] == "runner_error"
    assert (await _wait(store, good))["state"] == "succeeded"
    assert (await runner.cancel(bad))["state"] == "failed"
