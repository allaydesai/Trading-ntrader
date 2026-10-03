"""Run jobs one at a time, each in a fresh worker process (S2.1, S2.2).

A backtest never runs in the server process: ``BacktestEngine`` is single-use,
Nautilus' log guard panics if initialised twice, and its output would corrupt
the protocol on stdout. Each job gets a child process in its own session (so a
cancel can signal the whole group), with stdout and stderr in the job's log.

Cancelling a running job sends SIGTERM to its process group and SIGKILL after
``kill_grace_s``. A run is written to the database in one transaction at the end
of the engine run, so a worker killed before that commit leaves no run behind.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import TERMINAL_STATES, JobStore, utc_now
from src.services.provenance import REPO_ROOT

logger = structlog.get_logger(__name__)


def default_worker_command(job_dir: Path) -> list[str]:
    """The real worker: same interpreter, same code, a fresh process."""
    return [sys.executable, "-m", "src.mcp_server.worker", str(job_dir)]


def recover_jobs(store: JobStore) -> list[str]:
    """Jobs left by a previous server: running ones are lost, queued ones run again.

    A lost job's worker may still be alive (it runs in its own session) and may
    yet save its run; the recorded pid lets the operator check.
    """
    jobs = store.list(limit=10_000)
    lost = {
        "code": "server_restarted",
        "message": "The server stopped while this job was running.",
        "fix": "Resubmit it. Check backtest history first: the worker (pid in this "
        "status) may have finished on its own.",
    }
    for status in jobs:
        if status["state"] == "running":
            store.update(status["job_id"], state="lost", finished_at=utc_now(), error=lost)
    return [s["job_id"] for s in reversed(jobs) if s["state"] == "queued"]


def job_outcome(
    result: dict[str, Any] | None,
    returncode: int | None,
    *,
    cancelled: bool,
    timed_out: bool,
    timeout_s: float,
) -> dict[str, Any]:
    """Final status fields from how the worker ended and what it wrote."""
    if cancelled:
        return {"state": "cancelled"}
    if timed_out:
        fix = "Shorten the window or raise NTRADER_MCP_JOB_TIMEOUT_S."
        failure = ToolFailure("timeout", f"Worker exceeded {timeout_s:g}s.", fix=fix)
        return {"state": "failed", "error": failure.to_dict()["error"]}
    result = result or {}
    if returncode == 0 and result.get("status") == "ok":
        return {"state": "succeeded", "run_id": result.get("run_id")}
    error = result.get("error") or {
        "code": "worker_crashed",
        "message": f"Worker exited with code {returncode} without a result.",
        "fix": "Read log_tail in get_job for the cause.",
    }
    return {"state": "failed", "error": error}


async def terminate(proc: asyncio.subprocess.Process | None, grace_s: float) -> None:
    """SIGTERM a worker's process group; SIGKILL it if it outlives ``grace_s``."""
    if proc is None or proc.returncode is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_s)
            return
        except asyncio.TimeoutError:
            continue


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
    ) -> None:
        self.store = store
        self._timeout_s = timeout_s
        self._worker_command = worker_command
        self._cwd = cwd
        self._kill_grace_s = kill_grace_s
        self._pending = recover_jobs(store)
        self._queue: asyncio.Queue[str] | None = None
        self._task: asyncio.Task[None] | None = None
        self._running: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._cancelled: set[str] = set()
        self._finished: dict[str, asyncio.Event] = {}

    def start(self) -> None:
        """Start the drain task if it is not running (needs a running event loop)."""
        if self._task is not None and not self._task.done():
            return
        self._queue = asyncio.Queue()
        for job_id in self._pending:
            self._queue.put_nowait(job_id)
        self._pending = []
        self._task = asyncio.create_task(self._drain())

    async def submit(self, payload: dict[str, Any]) -> str:
        """Queue a job and return its id at once."""
        job_id = self.store.create(payload)
        self.start()
        assert self._queue is not None
        self._queue.put_nowait(job_id)
        return job_id

    def queue_state(self) -> dict[str, Any]:
        """The running job and how many are waiting."""
        return {"running": self._running, "queued": self._queue.qsize() if self._queue else 0}

    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a queued or running job; a finished job is returned unchanged."""
        status = self.store.status(job_id)
        if status["state"] in TERMINAL_STATES:
            return status
        self._cancelled.add(job_id)
        if job_id != self._running:
            return self.store.update(job_id, state="cancelled", finished_at=utc_now())
        finished = self._finished.setdefault(job_id, asyncio.Event())
        await self._terminate()
        await asyncio.wait_for(finished.wait(), timeout=self._kill_grace_s + 1)
        return self.store.status(job_id)

    async def _drain(self) -> None:
        assert self._queue is not None
        while True:
            job_id = await self._queue.get()
            try:
                if self.store.status(job_id).get("state") == "queued":
                    await self._run(job_id)
            except Exception as exc:  # one broken job must not stop the queue
                logger.exception("mcp_job_runner_error", job_id=job_id)
                error = {"code": "runner_error", "message": str(exc), "fix": "See server log."}
                self.store.update(job_id, state="failed", error=error, finished_at=utc_now())

    async def _run(self, job_id: str) -> None:
        self._running = job_id
        started = time.monotonic()
        self.store.update(job_id, state="running", started_at=utc_now())
        timed_out = False
        try:
            with open(self.store.log_path(job_id), "ab") as log:
                self._proc = await asyncio.create_subprocess_exec(
                    *self._worker_command(self.store.job_dir(job_id)),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    cwd=self._cwd,
                    start_new_session=True,
                )
            self.store.update(job_id, pid=self._proc.pid)
            if job_id in self._cancelled:  # cancelled while the process was spawning
                await self._terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=self._timeout_s)
            except asyncio.TimeoutError:
                timed_out = True
                await self._terminate()
            outcome = job_outcome(
                self.store.result(job_id),
                self._proc.returncode,
                cancelled=job_id in self._cancelled,
                timed_out=timed_out,
                timeout_s=self._timeout_s,
            )
        finally:
            self._proc, self._running = None, None
        elapsed = round(time.monotonic() - started, 3)
        self.store.update(job_id, **outcome, finished_at=utc_now(), elapsed_s=elapsed)
        self._finished.setdefault(job_id, asyncio.Event()).set()

    async def _terminate(self) -> None:
        await terminate(self._proc, self._kill_grace_s)
