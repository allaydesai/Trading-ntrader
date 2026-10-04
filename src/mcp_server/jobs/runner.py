"""Run jobs one at a time, each in a fresh worker process (S2.1, S2.2).

A backtest never runs in the server process: ``BacktestEngine`` is single-use,
Nautilus' log guard panics if initialised twice, and its output would corrupt
the protocol on stdout. Each job gets a child process in its own session (so a
cancel can signal the whole group), with stdout and stderr in the job's log.

One server owns the jobs directory at a time (``recovery.ServerLock``); a
second server reads jobs but refuses to submit or cancel. A worker outlives the
server that started it, so a new owner adopts a worker that is still alive and
waits for it before running anything else (``recovery.recover_jobs``).

Cancelling a running job sends SIGTERM to its process group and SIGKILL after
``kill_grace_s``. A run is written to the database in one transaction at the end
of the engine run, so a worker killed before that commit leaves no run behind;
one killed after it is reported as succeeded, never as cancelled.
"""

import asyncio
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import structlog

from src.mcp_server.jobs.process import Worker, spawn
from src.mcp_server.jobs.recovery import (
    FindRun,
    ServerLock,
    job_outcome,
    not_owner,
    recover_jobs,
    saved_run,
)
from src.mcp_server.jobs.store import TERMINAL_STATES, JobStore, utc_now
from src.services.provenance import REPO_ROOT

logger = structlog.get_logger(__name__)


def default_worker_command(job_dir: Path) -> list[str]:
    """The real worker: same interpreter, same code, a fresh process."""
    return [sys.executable, "-m", "src.mcp_server.worker", str(job_dir)]


def record_failure(store: JobStore, job_id: str, exc: Exception) -> None:
    """Mark a job failed by the runner itself; never raises."""
    error = {"code": "runner_error", "message": str(exc), "fix": "See server log."}
    try:
        store.update(job_id, state="failed", error=error, finished_at=utc_now())
    except Exception:  # e.g. the job's directory is gone: nothing left to record
        logger.exception("mcp_job_failure_not_recorded", job_id=job_id)


class JobRunner:
    """A FIFO queue of jobs drained by a single background task."""

    def __init__(
        self,
        store: JobStore,
        *,
        timeout_s: float,
        worker_command: Callable[[Path], list[str]] = default_worker_command,
        cwd: Path = REPO_ROOT,
        kill_grace_s: float = 3.0,
        find_run: FindRun | None = None,
    ) -> None:
        self.store = store
        self._timeout_s = timeout_s
        self._worker_command = worker_command
        self._cwd = cwd
        self._kill_grace_s = kill_grace_s
        self._find_run = find_run
        self._lock = ServerLock(store.root)
        self._pending: list[str] = []
        self._adopted: list[tuple[str, int]] = []
        self._queue: asyncio.Queue[str] | None = None
        self._task: asyncio.Task[None] | None = None
        self._running: str | None = None
        self._worker: Worker | None = None
        self._cancelled: set[str] = set()
        self._finished: dict[str, asyncio.Event] = {}
        self._claim()

    def _claim(self) -> bool:
        """Own the jobs directory, recovering its jobs on first taking the lock."""
        if self._lock.held:
            return True
        if not self._lock.acquire():
            return False
        recovery = recover_jobs(self.store, self._find_run)
        self._pending, self._adopted = recovery.queued, recovery.adopted
        return True

    def release(self) -> None:
        """Give up the jobs directory so another server can own it."""
        if self._task is not None:
            self._task.cancel()
        self._lock.release()

    def start(self) -> None:
        """Start the drain task if this server owns the queue (needs a running event loop)."""
        if not self._claim() or (self._task is not None and not self._task.done()):
            return
        # Kept across a restarted task, so waiting jobs are not dropped.
        self._queue = self._queue or asyncio.Queue()
        for job_id in self._pending:
            self._queue.put_nowait(job_id)
        self._pending = []
        self._task = asyncio.create_task(self._drain())

    async def submit(self, payload: dict[str, Any]) -> str:
        """Queue a job and return its id at once."""
        return self.enqueue(self.reserve(payload))

    def reserve(self, payload: dict[str, Any]) -> str:
        """Write a queued job without starting it (callable from a worker thread).

        A study records the job in its ledger in the same transaction that checks
        its budget, then calls ``enqueue``; a reserved job that is never enqueued
        must be cancelled, or the next server to own the directory would run it.
        """
        if not self._claim():
            raise not_owner(self._lock.holder())
        return self.store.create(payload)

    def enqueue(self, job_id: str) -> str:
        """Hand a reserved job to the drain task (needs the running event loop)."""
        self.start()
        assert self._queue is not None
        self._queue.put_nowait(job_id)
        return job_id

    def queue_state(self) -> dict[str, Any]:
        """The running job, how many are waiting, and whether this server owns the queue."""
        waiting = (self._queue.qsize() if self._queue else 0) + len(self._pending)
        return {"running": self._running, "queued": waiting, **self._lock.ownership()}

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a queued or running job; a finished job is returned unchanged."""
        status = self.store.status(job_id)
        if status["state"] in TERMINAL_STATES:
            return status
        if not self._claim():
            raise not_owner(self._lock.holder())
        if job_id != self._running:
            return self.store.update(job_id, state="cancelled", finished_at=utc_now())
        self._cancelled.add(job_id)
        finished = self._finished.setdefault(job_id, asyncio.Event())
        if self._worker is not None:
            await self._worker.terminate(self._kill_grace_s)
        try:
            await asyncio.wait_for(finished.wait(), timeout=self._kill_grace_s + 1)
        except asyncio.TimeoutError:
            pass  # report the job as it stands rather than fail the cancel
        return self.store.status(job_id)

    async def _drain(self) -> None:
        assert self._queue is not None
        adopted, self._adopted = self._adopted, []
        for job_id, pid in adopted:
            await self._guarded(job_id, self._follow(job_id, pid))
        while True:
            job_id = await self._queue.get()
            await self._guarded(job_id, self._run(job_id))

    async def _guarded(self, job_id: str, work: Coroutine[Any, Any, None]) -> None:
        """Run one job's work; whatever happens, the queue carries on and waiters wake."""
        try:
            await work
        except Exception as exc:  # one broken job must not stop the queue
            logger.exception("mcp_job_runner_error", job_id=job_id)
            record_failure(self.store, job_id, exc)
        finally:
            self._worker, self._running = None, None
            self._cancelled.discard(job_id)
            if (finished := self._finished.pop(job_id, None)) is not None:
                finished.set()

    async def _run(self, job_id: str) -> None:
        if self.store.status(job_id).get("state") != "queued":
            return
        self._running = job_id
        started = time.monotonic()
        self.store.update(job_id, state="running", started_at=utc_now())
        command = self._worker_command(self.store.job_dir(job_id))
        self._worker = await spawn(job_id, command, self.store.log_path(job_id), self._cwd)
        self.store.update(job_id, pid=self._worker.pid)
        if job_id in self._cancelled:  # cancelled while the process was spawning
            await self._worker.terminate(self._kill_grace_s)
        await conclude(self, self._worker, started)

    async def _follow(self, job_id: str, pid: int) -> None:
        """Wait for a worker a previous server started, then finalise its job."""
        self._running, self._worker = job_id, Worker(job_id, pid)
        await conclude(self, self._worker, None)


async def conclude(runner: JobRunner, worker: Worker, started: float | None) -> None:
    """Wait for the worker to end (or time out) and write the job's final status."""
    job_id = worker.job_id
    timed_out = not await worker.exited(runner._timeout_s)
    if timed_out:
        await worker.terminate(runner._kill_grace_s)
    outcome = job_outcome(
        runner.store.result(job_id),
        worker.returncode,
        cancelled=job_id in runner._cancelled,
        timed_out=timed_out,
        timeout_s=runner._timeout_s,
    )
    if outcome["state"] == "cancelled":
        # Killed between the database commit and result.json: the run exists.
        run_id = await asyncio.to_thread(saved_run, runner._find_run, job_id)
        if run_id:
            outcome = {"state": "succeeded", "run_id": run_id, "cancel_requested": True}
    if started is not None:
        outcome["elapsed_s"] = round(time.monotonic() - started, 3)
    runner.store.update(job_id, **outcome, finished_at=utc_now())
