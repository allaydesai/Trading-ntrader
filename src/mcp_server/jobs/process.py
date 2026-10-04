"""One worker process, as the runner sees it: wait for it, or stop it."""

import asyncio
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from src.mcp_server.jobs.recovery import worker_alive

_POLL_S = 0.2  # how often an adopted worker (not our child) is checked


@dataclass(frozen=True)
class Worker:
    """A job's worker. ``proc`` is None for one adopted from a previous server."""

    job_id: str
    pid: int
    proc: asyncio.subprocess.Process | None = None

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode if self.proc is not None else None

    async def exited(self, timeout_s: float) -> bool:
        """True once the worker has ended; False if it outlives ``timeout_s``."""
        if self.proc is not None:
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=timeout_s)
            except asyncio.TimeoutError:
                return False
            return True
        deadline = time.monotonic() + timeout_s
        while worker_alive(self.pid, self.job_id):
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(_POLL_S)
        return True

    async def terminate(self, grace_s: float) -> None:
        """SIGTERM the worker's process group; SIGKILL it if it outlives ``grace_s``."""
        if self.proc is not None and self.proc.returncode is not None:
            return
        if self.proc is None and not worker_alive(self.pid, self.job_id):
            return  # never signal a pid that is no longer this job's worker
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            if await self.exited(grace_s):
                return


async def spawn(job_id: str, command: list[str], log_path: Path, cwd: Path) -> Worker:
    """Start a worker in its own session, with stdout and stderr in the job's log."""
    with open(log_path, "ab") as log:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
    return Worker(job_id, proc.pid, proc)
