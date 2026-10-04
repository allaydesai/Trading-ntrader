"""One directory per job under ``jobs_dir`` (research MCP phase 1: no migration).

``request.json``   what was asked, written once at submit
``status.json``    lifecycle state, owned by the server (atomic rewrite)
``progress.json``  current phase, owned by the worker
``worker.log``     the worker's stdout and stderr
``result.json``    the worker's outcome: run id and headline, or an error with its fix

Files survive a server restart, so ``get_job`` keeps answering for old jobs.
The directory is also the queue: every server may add or cancel jobs, and the
one that owns the queue runs the oldest ``queued`` job. Status changes take a
per-job lock, so two servers never overwrite each other's change.
"""

import fcntl
import json
import os
import re
import secrets
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.mcp_server.errors import ToolFailure

#: ``reserved``: written but not yet released to the queue (its study ledger row is
#: being recorded); the owner never runs it.
STATES = ("reserved", "queued", "running", "succeeded", "failed", "cancelled", "lost")
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "lost"})
_TAIL_BLOCK = 64 * 1024
_TAIL_LIMIT = 4 * 1024 * 1024  # most log_tail will read, however long the lines
_JOB_ID = re.compile(r"^\d{8}T\d{6}\d{6}-[0-9a-f]{6}$")


def utc_now() -> str:
    """Current UTC time as an ISO string."""
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Create, read and update job directories."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def job_dir(self, job_id: str) -> Path:
        """The job's directory; refuses ids that are malformed or unknown."""
        path = self.root / job_id
        if not _JOB_ID.match(job_id) or not path.is_dir():
            raise ToolFailure(
                "unknown_job", f"No job '{job_id}'.", fix="list_jobs shows recent job ids."
            )
        return path

    def create(self, payload: dict[str, Any], *, state: str = "queued") -> str:
        """Write a new job and return its id (sortable by creation time)."""
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        job_id = f"{stamp}-{secrets.token_hex(3)}"
        path = self.root / job_id
        path.mkdir()
        self._write(path / "request.json", payload)
        status = {"job_id": job_id, "kind": payload.get("kind"), "state": state}
        self._write(path / "status.json", {**status, "created_at": utc_now()})
        return job_id

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        """Merge ``fields`` into the job's status and return it."""
        with self._locked(job_id):
            status = {**self.status(job_id), **fields}
            self._write(self.job_dir(job_id) / "status.json", status)
        return status

    def transition(
        self, job_id: str, from_states: Collection[str], **fields: Any
    ) -> dict[str, Any] | None:
        """Merge ``fields`` only if the job is in one of ``from_states``; None otherwise."""
        with self._locked(job_id):
            status = self.status(job_id)
            if status.get("state") not in from_states:
                return None
            status = {**status, **fields}
            self._write(self.job_dir(job_id) / "status.json", status)
        return status

    def names(self) -> list[str]:
        """Every job id, oldest first."""
        if not self.root.is_dir():
            return []
        return sorted(n for n in os.listdir(self.root) if _JOB_ID.match(n))

    def state_of(self, job_id: str) -> str | None:
        """The job's state, or None if it has no status (any more)."""
        return (self._read(self.root / job_id / "status.json") or {}).get("state")

    @contextmanager
    def _locked(self, job_id: str) -> Iterator[None]:
        with (self.job_dir(job_id) / ".status.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield  # closing the handle releases the lock

    def status(self, job_id: str) -> dict[str, Any]:
        return self._read(self.job_dir(job_id) / "status.json") or {}

    def request(self, job_id: str) -> dict[str, Any]:
        return self._read(self.job_dir(job_id) / "request.json") or {}

    def progress(self, job_id: str) -> dict[str, Any] | None:
        return self._read(self.job_dir(job_id) / "progress.json")

    def result(self, job_id: str) -> dict[str, Any] | None:
        return self._read(self.job_dir(job_id) / "result.json")

    def log_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "worker.log"

    def log_tail(self, job_id: str, lines: int) -> list[str]:
        """The last ``lines`` lines of the worker log (empty before the worker starts).

        Reads backwards from the end in blocks, so polling a job with a very
        large log never loads the whole file.
        """
        try:
            handle = self.log_path(job_id).open("rb")
        except FileNotFoundError:
            return []
        with handle:
            position = handle.seek(0, os.SEEK_END)
            tail = b""
            while position > 0 and tail.count(b"\n") <= lines and len(tail) < _TAIL_LIMIT:
                step = min(_TAIL_BLOCK, position)
                position -= step
                handle.seek(position)
                tail = handle.read(step) + tail
        found = tail.decode("utf-8", errors="replace").splitlines()
        if position > 0:
            found = found[1:]  # the first line was cut by the block boundary
        return found[-lines:] if lines > 0 else []

    def write_json(self, job_id: str, name: str, data: dict[str, Any]) -> None:
        self._write(self.job_dir(job_id) / name, data)

    def list(self, *, limit: int = 20, state: str | None = None) -> list[dict[str, Any]]:
        """Most recent jobs first, optionally only those in ``state``."""
        if not self.root.is_dir():
            return []
        found: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir(), reverse=True):
            if not _JOB_ID.match(path.name):
                continue
            status = self._read(path / "status.json")
            if status and (state is None or status.get("state") == state):
                found.append(status)
            if len(found) >= limit:
                break
        return found

    @staticmethod
    def _write(path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None


def first_queued(store: JobStore, floor: str = "") -> tuple[str | None, str]:
    """The oldest ``queued`` job at or after ``floor``, and the next floor.

    The floor is the oldest job that has not finished, so a server polling the
    queue never re-reads the long tail of finished jobs.
    """
    names = [n for n in store.names() if n >= floor]
    next_floor: str | None = None
    for name in names:
        state = store.state_of(name)
        if state in TERMINAL_STATES:
            continue
        next_floor = next_floor or name
        if state == "queued":
            return name, next_floor
    return None, next_floor or (names[-1] if names else floor)


def active(store: JobStore, floor: str = "") -> dict[str, Any]:
    """The running job (if any) and how many are queued, at or after ``floor``."""
    running, queued = None, 0
    for name in (n for n in store.names() if n >= floor):
        state = store.state_of(name)
        running = name if state == "running" else running
        queued += state == "queued"
    return {"running": running, "queued": queued}
