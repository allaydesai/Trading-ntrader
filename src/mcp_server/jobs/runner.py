"""Run jobs one at a time, each in a fresh worker process (S2.1, S2.2).

A backtest never runs in the server process: ``BacktestEngine`` is single-use,
Nautilus' log guard panics if initialised twice, and its output would corrupt
the protocol on stdout. Each job gets a child process in its own session (so a
cancel can signal the whole group), with stdout and stderr in the job's log.

The jobs directory is the queue, shared by every server over it: Claude Desktop
starts one server per consumer of the same config entry, so a second server is
normal. Any server queues and cancels jobs; exactly one, the holder of
``recovery.ServerLock``, runs them, oldest first. The others keep trying the lock,
so when the owner exits the queue moves on without waiting for a submit. A worker
outlives the server that started it, so a new owner adopts a worker that is
still alive and waits for it before running anything else
(``recovery.recover_jobs``).

Cancelling a running job sends SIGTERM to its process group and SIGKILL after
``kill_grace_s``; a server that does not own the job asks the owner by setting
``cancel_requested`` on it. A run is written to the database in one transaction at
the end of the engine run, so a worker killed before that commit leaves no run
behind; one killed after it is reported as succeeded, never as cancelled.
"""

import asyncio
import sys
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import structlog

from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.process import Worker, spawn
from src.mcp_server.jobs.recovery import FindRun, ServerLock, job_outcome, recover_jobs, saved_run
from src.mcp_server.jobs.store import TERMINAL_STATES, JobStore, active, first_queued, utc_now
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
    """Serves the shared queue: runs its jobs one at a time while this server owns it."""

    def __init__(
        self,
        store: JobStore,
        *,
        timeout_s: float,
        worker_command: Callable[[Path], list[str]] = default_worker_command,
        cwd: Path = REPO_ROOT,
        kill_grace_s: float = 3.0,
        find_run: FindRun | None = None,
        poll_s: float = 0.5,
    ) -> None:
        self.store = store
        self._timeout_s = timeout_s
        self._worker_command = worker_command
        self._cwd = cwd
        self._kill_grace_s = kill_grace_s
        self._find_run = find_run
        self._poll_s = poll_s
        self._lock = ServerLock(store.root)
        self._adopted: list[tuple[str, int]] = []
        self._floor = ""  # every job older than this has finished
        self._wake: asyncio.Event | None = None
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
        self._adopted, self._floor = recover_jobs(self.store, self._find_run).adopted, ""
        return True

    def release(self) -> None:
        """Stop serving and give up the jobs directory so another server can own it."""
        if self._task is not None:
            self._task.cancel()
        self._lock.release()

    def start(self) -> None:
        """Serve the queue in the background (needs a running event loop)."""
        if self._task is not None and not self._task.done():
            return
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._serve())

    async def submit(self, payload: dict[str, Any]) -> str:
        """Queue a job and return its id at once."""
        return self.enqueue(self.reserve(payload))

    def reserve(self, payload: dict[str, Any]) -> str:
        """Write a job held back from the queue (callable from a worker thread).

        A study records the job in its ledger in the same transaction that checks
        its budget, then calls ``enqueue``; a reserved job that is never enqueued
        must be cancelled. Until then no server runs it.
        """
        return self.store.create(payload, state="reserved")

    def enqueue(self, job_id: str) -> str:
        """Release a reserved job to the queue; whichever server owns it runs it."""
        self.store.transition(job_id, {"reserved"}, state="queued")
        self.start()
        if self._wake is not None:
            self._wake.set()
        return job_id

    def queue_state(self) -> dict[str, Any]:
        """The running job, how many are waiting, and which server owns the queue."""
        return {**active(self.store, self._floor), **self._lock.ownership()}

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a job; a running one is stopped within five seconds, whoever runs it."""
        status = self.store.status(job_id)
        if status["state"] in TERMINAL_STATES:
            return status
        unstarted = {"reserved", "queued"}
        if stopped := self.store.transition(
            job_id, unstarted, state="cancelled", finished_at=utc_now()
        ):
            return stopped
        if job_id == self._running:
            return await stop_own(self, job_id)
        self.store.update(job_id, cancel_requested=True)  # another server's worker: ask it
        return await wait_until_finished(self.store, job_id, self._kill_grace_s + 2)

    async def _serve(self) -> None:
        while True:
            try:
                ran = self._claim() and await self._step()
            except Exception:  # the queue must outlive any one bad read
                logger.exception("mcp_job_queue_error")
                ran = False
            if not ran:
                await nap(self._wake, self._poll_s)

    async def _step(self) -> bool:
        """Run what is waiting, adopted workers first; False when there was nothing."""
        adopted, self._adopted = self._adopted, []
        for orphan, pid in adopted:
            await self._guarded(orphan, self._follow(orphan, pid))
        job_id, self._floor = first_queued(self.store, self._floor)
        if job_id is not None:
            await self._guarded(job_id, self._run(job_id))
        return bool(adopted) or job_id is not None

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
        self._running = job_id
        if not self.store.transition(job_id, {"queued"}, state="running", started_at=utc_now()):
            return  # cancelled since it was found
        started = time.monotonic()
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


async def nap(wake: asyncio.Event | None, timeout_s: float) -> None:
    """Sleep until woken (a job was queued here) or ``timeout_s`` passes."""
    assert wake is not None, "start() creates the event before serving"
    try:
        await asyncio.wait_for(wake.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        pass
    wake.clear()


async def stop_own(runner: JobRunner, job_id: str) -> dict[str, Any]:
    """Stop the job this server is running and report how it ended."""
    runner._cancelled.add(job_id)
    finished = runner._finished.setdefault(job_id, asyncio.Event())
    if runner._worker is not None:
        await runner._worker.terminate(runner._kill_grace_s)
    try:
        await asyncio.wait_for(finished.wait(), timeout=runner._kill_grace_s + 1)
    except asyncio.TimeoutError:
        pass  # report the job as it stands rather than fail the cancel
    return runner.store.status(job_id)


def _cancel_requested(store: JobStore, job_id: str) -> bool:
    try:
        return bool(store.status(job_id).get("cancel_requested"))
    except ToolFailure:  # its directory is gone: nobody can ask any more
        return False


async def wait_until_finished(store: JobStore, job_id: str, timeout_s: float) -> dict[str, Any]:
    """The job's status once it has finished, or as it stands after ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    while (status := store.status(job_id))["state"] not in TERMINAL_STATES:
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.1)
    return status


async def watch(runner: JobRunner, worker: Worker) -> bool:
    """Wait for the worker to end, stopping it if another server asks; False on timeout."""
    deadline = time.monotonic() + runner._timeout_s
    while (left := deadline - time.monotonic()) > 0:
        if await worker.exited(min(runner._poll_s, left)):
            return True
        if worker.job_id not in runner._cancelled and _cancel_requested(
            runner.store, worker.job_id
        ):
            runner._cancelled.add(worker.job_id)
            await worker.terminate(runner._kill_grace_s)
    return False


async def conclude(runner: JobRunner, worker: Worker, started: float | None) -> None:
    """Wait for the worker to end (or time out) and write the job's final status."""
    job_id = worker.job_id
    timed_out = not await watch(runner, worker)
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
