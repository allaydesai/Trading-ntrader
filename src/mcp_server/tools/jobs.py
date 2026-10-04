"""Job tools: submit_backtest, get_job, list_jobs, cancel_job (S2.1, S2.2, S2.3)."""

from datetime import date
from decimal import Decimal
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs import service
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.studies.submit import prepare_in_sample, submit_in_study
from src.mcp_server.tools import build_spec, call

TOOL_NAMES = ("submit_backtest", "get_job", "list_jobs", "cancel_job")


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the job tools to ``server``."""

    def runner() -> JobRunner:
        assert ctx.runner is not None, "build_server always creates the runner"
        ctx.runner.start()
        return ctx.runner

    @server.tool()
    async def submit_backtest(
        strategy: str | None = None,
        symbol: str | None = None,
        start: date | None = None,
        end: date | None = None,
        timeframe: str | None = None,
        catalog: str | None = None,
        params: dict[str, Any] | None = None,
        starting_balance: Decimal | None = None,
        study: str | None = None,
        over_budget_reason: str | None = None,
    ) -> dict[str, Any]:
        """Validate and queue one backtest; returns a job id at once. Poll get_job.

        With study (slug or id) it is an in-sample trial: strategy, symbol, window,
        timeframe and catalog default from the study, the window must end on or
        before the in-sample end (the out-of-sample window is locked), and it counts
        against the trial budget (past it, give over_budget_reason). Without study,
        strategy, symbol, start and end are required and the run is unattributed:
        never evidence on a scorecard. Runs are persisted with git commit, dirty
        state and config hash and appear in NTrader's history. One job at a time.
        """
        fields = dict(
            strategy=strategy,
            symbol=symbol,
            start=start,
            end=end,
            timeframe=timeframe,
            catalog=catalog,
            params=params or {},
            starting_balance=starting_balance,
        )
        if study is not None:
            return await _submit_in_study(runner(), study, fields, over_budget_reason)
        return await _submit_unattributed(runner(), ctx.settings.resolved_default_catalog(), fields)

    @server.tool()
    async def get_job(job_id: str) -> dict[str, Any]:
        """State, phase, elapsed time, result (run id and headline) or error, and log tail."""
        return await call(service.job_view, runner(), job_id, ctx.settings.log_tail_lines)

    @server.tool()
    async def list_jobs(limit: int = 20, state: str | None = None) -> dict[str, Any]:
        """Recent jobs, newest first; state filters (queued, running, succeeded, failed...)."""
        return await call(service.list_jobs, runner(), limit, state)

    @server.tool()
    async def cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel a queued or running job; a running worker is stopped within 5 seconds."""
        try:
            status = await runner().cancel(job_id)
        except ToolFailure as failure:
            return failure.to_dict()
        return {"ok": True, **status}


def _unattributed(default_catalog: str, fields: dict[str, Any]) -> dict[str, Any]:
    missing = [n for n in ("strategy", "symbol", "start", "end") if fields[n] is None]
    if missing:
        raise ToolFailure(
            "invalid_request",
            f"Missing {', '.join(missing)}.",
            fix="Give them, or pass study to take them from a study.",
        )
    spec = build_spec(
        **{
            **fields,
            "timeframe": fields["timeframe"] or "1-DAY",
            "starting_balance": fields["starting_balance"] or Decimal("1000000"),
        }
    )
    return service.prepare_backtest(spec, default_catalog)


async def _submit_unattributed(
    runner: JobRunner, default_catalog: str, fields: dict[str, Any]
) -> dict[str, Any]:
    prepared = await call(_unattributed, default_catalog, fields)
    if not prepared.pop("ok"):
        return {"ok": False, **prepared}
    try:
        job_id = await runner.submit(prepared)
    except ToolFailure as failure:  # another server owns the job queue
        return failure.to_dict()
    return {
        "ok": True,
        "job_id": job_id,
        "state": "queued",
        "resolved": prepared["resolved"],
        "warnings": prepared["warnings"] + ["Unattributed: not in a study."],
        "queue": runner.queue_state(),
    }


def _in_study(study: str, fields: dict[str, Any], reason: str | None) -> dict[str, Any]:
    job, warnings = prepare_in_sample(study, **fields, over_budget_reason=reason)
    return {"job": job, "warnings": warnings}


async def _submit_in_study(
    runner: JobRunner, study: str, fields: dict[str, Any], reason: str | None
) -> dict[str, Any]:
    prepared = await call(_in_study, study, fields, reason)
    if not prepared.pop("ok"):
        return {"ok": False, **prepared}
    return await queue_study_job(runner, prepared["job"], prepared["warnings"])


async def queue_study_job(runner: JobRunner, job: Any, warnings: list[str]) -> dict[str, Any]:
    """Record a prepared study job in its ledger, queue it, and report it."""
    try:
        recorded = await submit_in_study(runner, job)
    except ToolFailure as failure:
        return failure.to_dict()
    return {
        "ok": True,
        **recorded,
        "state": "queued",
        "resolved": job.payload["resolved"],
        "warnings": warnings,
        "queue": runner.queue_state(),
    }
