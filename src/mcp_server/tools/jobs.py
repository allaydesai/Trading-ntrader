"""Job tools: submit_backtest, get_job, list_jobs, cancel_job (S2.1, S2.2, S2.3)."""

from datetime import date
from decimal import Decimal
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs import service
from src.mcp_server.jobs.runner import JobRunner
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
        strategy: str,
        symbol: str,
        start: date,
        end: date,
        timeframe: str = "1-DAY",
        catalog: str | None = None,
        params: dict[str, Any] | None = None,
        starting_balance: Decimal = Decimal("1000000"),
    ) -> dict[str, Any]:
        """Validate and queue one backtest; returns a job id at once. Poll get_job.

        The run is persisted with its git commit, dirty state and config hash and
        appears in NTrader's backtest history and web UI. One job runs at a time.
        """

        def prepare() -> dict[str, Any]:
            spec = build_spec(
                strategy=strategy,
                symbol=symbol,
                start=start,
                end=end,
                timeframe=timeframe,
                catalog=catalog,
                params=params or {},
                starting_balance=starting_balance,
            )
            return service.prepare_backtest(spec, ctx.settings.resolved_default_catalog())

        prepared = await call(prepare)
        if not prepared.pop("ok"):
            return {"ok": False, **prepared}
        try:
            job_id = await runner().submit(prepared)
        except ToolFailure as failure:  # another server owns the job queue
            return failure.to_dict()
        return {
            "ok": True,
            "job_id": job_id,
            "state": "queued",
            "resolved": prepared["resolved"],
            "warnings": prepared["warnings"],
            "queue": runner().queue_state(),
        }

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
