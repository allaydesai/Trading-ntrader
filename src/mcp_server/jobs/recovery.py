"""Who runs a jobs directory's queue, and what to do with jobs a previous server left running.

Two servers over one directory (Claude Desktop starts one per consumer of a
config entry, and a restart can leave a worker alive) used to mark each other's
running jobs lost and re-run each other's queue. So exactly one server runs the
queue, by an exclusive lock (every server may add to it), and a job left
``running`` is settled by what actually happened to its worker:

- it wrote ``result.json``: finalise the job from it;
- it is still alive: adopt it, and let it finish before the queue moves on;
- it is gone and its run is in the database: the job succeeded;
- otherwise the job is lost.
"""

import fcntl
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import structlog

from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore, utc_now

logger = structlog.get_logger(__name__)

#: Finds the persisted run of a job whose worker left no result, or None.
FindRun = Callable[[str], str | None]

LOST = {
    "code": "server_restarted",
    "message": "The server stopped while this job was running and its worker left no result.",
    "fix": "Resubmit it. Check backtest history first if in doubt.",
}


class ServerLock:
    """An exclusive, non-blocking lock on ``<jobs_dir>/.server.lock``.

    ``flock`` is released by the kernel when the process exits, however it
    exits, so a crashed owner never leaves the directory locked.
    """

    def __init__(self, root: Path) -> None:
        self._path = root / ".server.lock"
        self._handle: IO[str] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """Take the lock if it is free; True when this process holds it."""
        if self._handle is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.truncate(0)
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()  # closing the descriptor drops the flock
            self._handle = None

    def ownership(self) -> dict[str, Any]:
        """``owner``, and when another server runs the queue, which process it is.

        ``owner_process`` is None when the lock is free: a serving server takes it within a poll.
        """
        if self._handle is not None:
            return {"owner": True}
        return {"owner": False, "owner_process": self.holder()}

    def holder(self) -> dict[str, Any] | None:
        """The process holding the lock when another one does; None when it is free or ours.

        Probes with a second open file description: ``flock`` conflicts across
        descriptions even within one process, and the probe is dropped at once.
        """
        if self._handle is not None or not self._path.exists():
            return None
        with self._path.open("r") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                pid_text = probe.read().strip()
            else:
                fcntl.flock(probe, fcntl.LOCK_UN)
                return None
        return describe_process(int(pid_text)) if pid_text.isdigit() else {"pid": None}


def _ps(pid: int) -> tuple[int | None, str] | None:
    try:
        proc = subprocess.run(
            ["ps", "-o", "ppid=,command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    ppid, _, command = proc.stdout.strip().partition(" ")
    if proc.returncode != 0 or not ppid.isdigit():
        return None
    return int(ppid), command.strip()


def describe_process(pid: int) -> dict[str, Any]:
    """A server's pid, command line and the client that launched it (its parent)."""
    found = _ps(pid)
    if found is None:
        return {"pid": pid}
    ppid, command = found
    parent = _ps(ppid) if ppid else None
    return {"pid": pid, "command": command[:200], "client": parent[1][:200] if parent else None}


def worker_alive(pid: int, job_id: str) -> bool:
    """True when ``pid`` is running and is the worker of ``job_id``.

    The command line is checked, not just the pid: a pid recorded before a
    restart may since have been reused by an unrelated process.
    """
    try:
        proc = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and job_id in proc.stdout


def saved_run(find_run: FindRun | None, job_id: str) -> str | None:
    """``find_run(job_id)``, treating any failure of the lookup as "not found"."""
    if find_run is None:
        return None
    try:
        return find_run(job_id)
    except Exception:
        logger.exception("mcp_saved_run_lookup_failed", job_id=job_id)
        return None


def job_outcome(
    result: dict[str, Any] | None,
    returncode: int | None,
    *,
    cancelled: bool,
    timed_out: bool,
    timeout_s: float,
) -> dict[str, Any]:
    """Final status fields from how the worker ended and what it wrote.

    A worker writes an ``ok`` result only after reading its run back from the
    database, so that result wins over a cancel or a timeout that raced it: a
    saved run is never reported as cancelled.
    """
    result = result or {}
    if result.get("status") == "ok":
        outcome: dict[str, Any] = {"state": "succeeded", "run_id": result.get("run_id")}
        if cancelled:
            outcome["cancel_requested"] = True
        return outcome
    if cancelled:
        return {"state": "cancelled"}
    if timed_out:
        fix = "Shorten the window or raise NTRADER_MCP_JOB_TIMEOUT_S."
        failure = ToolFailure("timeout", f"Worker exceeded {timeout_s:g}s.", fix=fix)
        return {"state": "failed", "error": failure.to_dict()["error"]}
    exit_text = "exited" if returncode is None else f"exited with code {returncode}"
    error = result.get("error") or {
        "code": "worker_crashed",
        "message": f"Worker {exit_text} without a result.",
        "fix": "Read log_tail in get_job for the cause.",
    }
    return {"state": "failed", "error": error}


@dataclass
class Recovery:
    """What a new owner found: live workers to wait for (queued jobs stay in the queue)."""

    adopted: list[tuple[str, int]] = field(default_factory=list)


def _settle(store: JobStore, status: dict[str, Any], find_run: FindRun | None) -> int | None:
    """Finalise one job left ``running``; returns its pid when the worker is still alive."""
    job_id, pid = status["job_id"], status.get("pid")
    result = store.result(job_id)
    if result is not None:
        outcome = job_outcome(result, None, cancelled=False, timed_out=False, timeout_s=0)
    elif pid and worker_alive(pid, job_id):
        return pid
    elif run_id := saved_run(find_run, job_id):
        outcome = {"state": "succeeded", "run_id": run_id, "recovered": True}
    else:
        outcome = {"state": "lost", "error": LOST}
    store.update(job_id, **outcome, finished_at=utc_now())
    return None


def recover_jobs(store: JobStore, find_run: FindRun | None = None) -> Recovery:
    """Settle jobs a previous server left behind. Call only while holding the lock."""
    recovery = Recovery()
    for status in reversed(store.list(limit=10_000)):  # oldest first
        if status.get("state") == "running":
            pid = _settle(store, status, find_run)
            if pid is not None:
                recovery.adopted.append((status["job_id"], pid))
    return recovery
