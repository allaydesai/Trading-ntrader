"""Robustness tools: sweeps, sensitivity, Monte Carlo, breadth, cost stress, walk-forward.

Stories S3.1, S3.2, S4.1, S4.2, S4.4, S5.3 (phase 4).
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.studies import robustness, sweeps, variability, walkforward
from src.mcp_server.studies.batch import submit_batch
from src.mcp_server.studies.gates import load_gates
from src.mcp_server.tools import call

TOOL_NAMES = (
    "submit_sweep",
    "run_sensitivity",
    "run_monte_carlo",
    "submit_breadth",
    "submit_cost_stress",
    "submit_walk_forward",
    "get_analysis",
)


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

    @server.tool()
    async def run_monte_carlo(
        study: str,
        run_id: str | None = None,
        iterations: int = 5000,
        method: str = "both",
        breach_drawdown: float = 0.2,
        seed: int = 0,
    ) -> dict[str, Any]:
        """Resample a study run's closed trades (bootstrap, reshuffle or both) into the range
        of paths they could have made: 5th / 50th / 95th percentiles of CAGR, max drawdown
        (negative; the worst is p5), longest losing streak, time under water and final
        return, the probability of breaching breach_drawdown, and bootstrap confidence
        intervals for Sharpe, profit factor and win rate with the sample-size verdict
        (insufficient below 30 trades) and minimum track record length. run_id defaults
        to the current candidate's source run, else the newest in-sample run. Trade-level:
        serial correlation between trades is ignored. Stored as an analysis of the study."""
        return await call(
            variability.run_monte_carlo,
            runner().store,
            study,
            run_id=run_id,
            iterations=iterations,
            method=method,
            breach_drawdown=breach_drawdown,
            seed=seed,
            max_iterations=ctx.settings.max_monte_carlo_iterations,
        )

    @server.tool()
    async def get_analysis(
        study: str, analysis_id: str | None = None, kind: str | None = None
    ) -> dict[str, Any]:
        """A stored analysis of the study (the newest, or the newest of kind: sweep,
        sensitivity, monte_carlo, walk_forward, breadth, cost_stress) with its request,
        status, result and member jobs. A pending walk-forward whose runs have all finished
        is evaluated here first. get_study lists every analysis id."""
        return await call(
            walkforward.get_analysis, runner().store, study, analysis_id=analysis_id, kind=kind
        )

    _register_jobs(server, ctx, runner)


def _register_jobs(server: MCPServer, ctx: ServerContext, runner: Callable[[], JobRunner]) -> None:
    """Robustness runs of a candidate: breadth and cost stress (walk-forward beside them)."""

    @server.tool()
    async def submit_breadth(
        study: str,
        symbols: list[str],
        window: str = "in_sample",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the same rules on symbols the study did not tune on, one job per symbol
        (poll get_job). The frozen candidate's parameters once there is one, else params
        is required. window is in_sample, or out_of_sample after a freeze. Counted as
        breadth, never as trials; G2's breadth check reads the finished runs' profit
        factors. Missing data for a symbol refuses the whole batch before anything runs."""

        def prepare() -> dict[str, Any]:
            batch, warnings = robustness.prepare_breadth(
                study, symbols=symbols, window=window, params=params
            )
            return {"batch": batch, "warnings": warnings}

        prepared = await call(prepare)
        if not prepared.pop("ok"):
            return {"ok": False, **prepared}
        return await submit_batch(runner(), prepared["batch"], prepared["warnings"])

    @server.tool()
    async def submit_cost_stress(study: str, multiplier: float | None = None) -> dict[str, Any]:
        """Run the frozen candidate on the in-sample window with every commission term
        (per-share rate, minimum, cap) scaled by multiplier: G2's cost_stress_multiplier
        from the vault by default, else 2. The fill model is untouched, so this stresses
        commission, not slippage. Not counted; the scorecard asks whether the run is still
        profitable."""

        def prepare() -> dict[str, Any]:
            gates = load_gates(ctx.settings)
            batch, warnings = robustness.prepare_cost_stress(
                study, multiplier=multiplier, gate_multiplier=robustness.cost_multiplier_of(gates)
            )
            return {"batch": batch, "warnings": warnings}

        prepared = await call(prepare)
        if not prepared.pop("ok"):
            return {"ok": False, **prepared}
        return await submit_batch(runner(), prepared["batch"], prepared["warnings"])

    @server.tool()
    async def submit_walk_forward(
        study: str,
        is_years: int = 6,
        oos_years: int = 2,
        mode: str = "rolling",
        select_by: str = "sharpe_ratio",
        grid: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Walk-forward of the frozen candidate's grid (the study's declared space by
        default), sliced: every cell runs once over the whole span (in-sample through
        out-of-sample), then each rolling (or anchored) window picks the best in-sample
        cell by select_by (sharpe_ratio, annualised_return, max_drawdown) and reads its
        out-of-sample slice from the stored daily equity. The result appears in
        get_analysis once every run has finished: per-window table, walk-forward
        efficiency (mean out-of-sample over in-sample annualised return), negative
        windows. Needs a frozen candidate and at least two windows; sees the holdout, so a
        later candidate version is contaminated. Runs are never trials."""

        def prepare() -> dict[str, Any]:
            batch, warnings = walkforward.prepare_walk_forward(
                study,
                is_years=is_years,
                oos_years=oos_years,
                mode=mode,
                select_by=select_by,
                grid_axes=grid,
                max_cells=ctx.settings.max_sweep_cells,
            )
            return {"batch": batch, "warnings": warnings}

        prepared = await call(prepare)
        if not prepared.pop("ok"):
            return {"ok": False, **prepared}
        return await submit_batch(runner(), prepared["batch"], prepared["warnings"])
