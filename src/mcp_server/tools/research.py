"""Research job tools: benchmarks beside a study's runs, and reproductions (S2.3, S2.4)."""

from datetime import date
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.studies import references
from src.mcp_server.studies.submit import queue_study_job
from src.mcp_server.tools import call

TOOL_NAMES = ("submit_benchmark", "reproduce_run")


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the research job tools to ``server``."""

    def runner() -> JobRunner:
        assert ctx.runner is not None, "build_server always creates the runner"
        ctx.runner.start()
        return ctx.runner

    @server.tool()
    async def submit_benchmark(
        study: str,
        strategy: str = "buy_and_hold",
        params: dict[str, Any] | None = None,
        symbol: str | None = None,
        window: str = "in_sample",
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        """Run a benchmark beside a study's runs, on the same window and metric code.

        buy_and_hold by default; an incumbent strategy may be named instead (not the
        study's own strategy: that is a trial). window is in_sample, or
        out_of_sample once a candidate is frozen. Not counted against the budget.
        """

        def prepare() -> dict[str, Any]:
            job, warnings = references.prepare_benchmark(
                study,
                strategy=strategy,
                params=params or {},
                symbol=symbol,
                window=window,
                start=start,
                end=end,
            )
            return {"job": job, "warnings": warnings}

        prepared = await call(prepare)
        if not prepared.pop("ok"):
            return {"ok": False, **prepared}
        return await queue_study_job(runner(), prepared["job"], prepared["warnings"])

    @server.tool()
    async def reproduce_run(run_id: str) -> dict[str, Any]:
        """Re-run a stored run's exact config (same fill seed) as a new run.

        When the job finishes, get_job's result.reproduction says whether every
        metric matched the original to stored precision. Refused when the stored
        config no longer resolves to the same config hash. An out-of-sample run is
        reproduced only from its own commit with a clean tree.
        """

        def prepare() -> dict[str, Any]:
            payload, job = references.prepare_reproduction(run_id)
            return {"payload": payload, "job": job}

        prepared = await call(prepare)
        if not prepared.pop("ok"):
            return {"ok": False, **prepared}
        payload, job = prepared["payload"], prepared["job"]
        if job is not None:
            return await queue_study_job(runner(), job, payload["warnings"])
        try:
            job_id = await runner().submit(payload)
        except ToolFailure as failure:
            return failure.to_dict()
        return {
            "ok": True,
            "job_id": job_id,
            "state": "queued",
            "resolved": payload["resolved"],
            "reproduces": payload["reproduced_from_run_id"],
            "warnings": payload["warnings"],
            "queue": runner().queue_state(),
        }
