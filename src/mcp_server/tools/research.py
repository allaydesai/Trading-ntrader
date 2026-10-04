"""Research tools: benchmarks, reproductions, the out-of-sample run, the scorecard.

Stories S2.3, S2.4, S5.2 and S6.1.
"""

from collections.abc import Callable
from datetime import date
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.studies import candidates, references, scorecard
from src.mcp_server.studies.submit import queue_study_job
from src.mcp_server.tools import call

TOOL_NAMES = ("submit_benchmark", "reproduce_run", "run_out_of_sample", "get_scorecard")


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

    _register_holdout(server, ctx, runner)


def _register_holdout(
    server: MCPServer, ctx: ServerContext, runner: Callable[[], JobRunner]
) -> None:
    """The one out-of-sample run per candidate, and the scorecard that judges it."""

    @server.tool()
    async def run_out_of_sample(study: str, override_reason: str | None = None) -> dict[str, Any]:
        """Run the frozen candidate once on the study's locked out-of-sample window.

        Built from the frozen record alone. A second run of the same candidate is
        refused unless override_reason is given, which marks the study contaminated.
        Run submit_benchmark(window="out_of_sample") beside it.
        """

        def prepare() -> dict[str, Any]:
            job, warnings = candidates.prepare_out_of_sample(study, override_reason)
            return {"job": job, "warnings": warnings}

        prepared = await call(prepare)
        if not prepared.pop("ok"):
            return {"ok": False, **prepared}
        return await queue_study_job(runner(), prepared["job"], prepared["warnings"])

    @server.tool()
    async def get_scorecard(study: str, version: int | None = None) -> dict[str, Any]:
        """A candidate against every gate in the study's pass criteria: pass, fail or
        missing, the number behind each check, its threshold (from the vault's
        System/Gates.md) and the run ids it was judged on. Checks that need phase 3
        or 4 (paper, sensitivity, walk-forward, breadth) are always missing.
        """
        return await call(scorecard.get_scorecard, ctx.settings, runner().store, study, version)
