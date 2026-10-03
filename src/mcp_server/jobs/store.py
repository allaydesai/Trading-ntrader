"""One directory per job under ``jobs_dir`` (research MCP phase 1: no migration).

``request.json``   what was asked, written once at submit
``status.json``    lifecycle state, owned by the server (atomic rewrite)
``progress.json``  current phase, owned by the worker
``worker.log``     the worker's stdout and stderr
``result.json``    the worker's outcome: run id and headline, or an error with its fix

Files survive a server restart, so ``get_job`` keeps answering for old jobs.
"""

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.mcp_server.errors import ToolFailure

STATES = ("queued", "running", "succeeded", "failed", "cancelled", "lost")
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "lost"})
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

    def create(self, payload: dict[str, Any]) -> str:
        """Write a new queued job and return its id (sortable by creation time)."""
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        job_id = f"{stamp}-{secrets.token_hex(3)}"
        path = self.root / job_id
        path.mkdir()
        self._write(path / "request.json", payload)
        status = {"job_id": job_id, "kind": payload.get("kind"), "state": "queued"}
        self._write(path / "status.json", {**status, "created_at": utc_now()})
        return job_id

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        """Merge ``fields`` into the job's status and return it."""
        status = {**self.status(job_id), **fields}
        self._write(self.job_dir(job_id) / "status.json", status)
        return status

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
        """The last ``lines`` lines of the worker log (empty before the worker starts)."""
        path = self.log_path(job_id)
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]

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
