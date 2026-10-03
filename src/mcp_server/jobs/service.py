"""Job operations behind the tools: submit (validated first), inspect, list, cancel."""

import time
from datetime import datetime
from typing import Any

from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.jobs.store import STATES
from src.mcp_server.request import BacktestSpec
from src.mcp_server.validation import validate


def prepare_backtest(spec: BacktestSpec, default_catalog: str) -> dict[str, Any]:
    """Validate a spec and build the job payload; raises when it cannot run.

    The stored spec has the catalog filled in, so the worker never depends on
    the server's default-catalog setting.
    """
    check = validate(spec, default_catalog=default_catalog)
    if not check["ok"]:
        raise ToolFailure(
            "validation_failed",
            "The request did not pass validation; nothing was queued.",
            fix="Fix each listed error (validate_config shows the same report).",
            details={"errors": check["errors"]},
        )
    resolved = check["resolved"]
    fixed_spec = spec.model_copy(update={"catalog": resolved["catalog"]})
    return {
        "kind": "backtest",
        "spec": fixed_spec.model_dump(mode="json"),
        "resolved": resolved,
        "warnings": check["warnings"],
    }


def _elapsed(status: dict[str, Any]) -> float | None:
    if "elapsed_s" in status:
        return status["elapsed_s"]
    if status.get("state") == "running" and status.get("started_at"):
        started = datetime.fromisoformat(status["started_at"]).timestamp()
        return round(time.time() - started, 1)
    return None


def job_view(runner: JobRunner, job_id: str, log_lines: int) -> dict[str, Any]:
    """Everything known about a job: state, phase, elapsed, result or error, log tail."""
    store = runner.store
    status = store.status(job_id)
    request = store.request(job_id)
    progress = store.progress(job_id) or {}
    view: dict[str, Any] = {
        **status,
        "phase": progress.get("phase"),
        "elapsed_s": _elapsed(status),
        "request": request.get("resolved"),
        "log_tail": store.log_tail(job_id, log_lines),
    }
    result = store.result(job_id)
    if result and result.get("status") == "ok":
        view["result"] = {k: result.get(k) for k in ("run_id", "config_hash", "headline")}
    return view


def list_jobs(runner: JobRunner, limit: int, state: str | None) -> dict[str, Any]:
    """Recent jobs, newest first."""
    if state is not None and state not in STATES:
        raise ToolFailure(
            "invalid_state", f"Unknown state '{state}'.", fix=f"Use one of: {', '.join(STATES)}."
        )
    jobs = runner.store.list(limit=max(1, min(limit, 200)), state=state)
    return {"jobs": jobs, "queue": runner.queue_state()}
