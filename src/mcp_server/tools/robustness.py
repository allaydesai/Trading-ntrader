"""Robustness tools: sweeps, sensitivity, Monte Carlo, breadth, cost stress, walk-forward.

Stories S3.1, S3.2, S4.1, S4.2, S4.4, S5.3 (phase 4).
"""

from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.studies import sweeps
from src.mcp_server.studies.batch import submit_batch
from src.mcp_server.tools import call

TOOL_NAMES = ("submit_sweep", "run_sensitivity")


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the robustness tools to ``server``."""

    def runner() -> JobRunner:
        assert ctx.runner is not None, "build_server always creates the runner"
        ctx.runner.start()
        return ctx.runner

    @server.tool()
    async def submit_sweep(
        study: str,
        grid: dict[str, Any] | None = None,
        around: dict[str, Any] | None = None,
        around_pct: float = 0.2,
        over_budget_reason: str | None = None,
    ) -> dict[str, Any]:
        """Run a parameter grid in-sample as one batch of counted trials (poll get_job per job).

        grid is {name: {values: [...]}} or {name: {min, max, step}}; by default the study's
        declared space. around={full parameter set} probes each declared parameter at
        -around_pct, itself and +around_pct instead (the G2 neighbourhood; get_study shows a
        run's params). The whole batch is counted against the budget before anything runs
        and refused past it unless over_budget_reason is given. Allowed while exploring and
        after a freeze. Cells resolving to the same run are submitted once. Then
        run_sensitivity for the plateau map; cancel_job(analysis=...) stops the batch.
        """

        def prepare() -> dict[str, Any]:
            batch, warnings = sweeps.prepare_sweep(
                study,
                grid_axes=grid,
                around=around,
                around_pct=around_pct,
                over_budget_reason=over_budget_reason,
                max_cells=ctx.settings.max_sweep_cells,
            )
            return {"batch": batch, "warnings": warnings}

        prepared = await call(prepare)
        if not prepared.pop("ok"):
            return {"ok": False, **prepared}
        return await submit_batch(runner(), prepared["batch"], prepared["warnings"])

    @server.tool()
    async def run_sensitivity(
        study: str, metric: str = "sharpe_ratio", sweep: str | None = None
    ) -> dict[str, Any]:
        """The sensitivity map of a sweep (the newest by default): per cell the metric, its
        neighbours and stability (median neighbour / own); the recommended cell (best
        plateau centre), the raw best cell and the gap between them. Cells still running or
        failed are listed as missing. Stored as an analysis of the study."""
        return await call(sweeps.run_sensitivity, runner().store, study, metric=metric, sweep=sweep)
