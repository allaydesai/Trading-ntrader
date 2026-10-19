"""Portfolio tools over stored runs: sleeve correlation and the combined portfolio.

Stories S9.2–S9.5 (phase 5a). Both tools read stored daily curves; neither runs
the engine, queues a job, counts a trial or judges a gate.
"""

from typing import Any

from mcp.server import MCPServer

from src.mcp_server.analysis import correlation
from src.mcp_server.context import ServerContext
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.studies import portfolio
from src.mcp_server.tools import call

TOOL_NAMES = ("get_sleeve_correlation", "run_portfolio_backtest")


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the portfolio tools to ``server``."""

    def runner() -> JobRunner:
        assert ctx.runner is not None, "build_server always creates the runner"
        ctx.runner.start()
        return ctx.runner

    @server.tool()
    async def get_sleeve_correlation(
        run_ids: list[str],
        weights: list[float] | None = None,
        drawdown_threshold: float = 0.1,
        benchmark_symbol: str | None = None,
        catalog: str | None = None,
    ) -> dict[str, Any]:
        """How stored runs (sleeves) move together, on the days their daily curves share:
        Pearson correlation of daily returns, shared drawdown days and episodes per pair
        (deeper than drawdown_threshold), each sleeve's and the constant-mix total's
        return, CAGR, Sharpe, drawdown and time under water, and a regime table by year,
        trend and volatility tercile side by side (benchmark_symbol defaults to the first
        run's symbol, catalog to its catalog). weights are one positive number per run
        (equal by default) and apply to returns, not dollars. Any run with a stored
        mark-to-market curve qualifies; a run that is not a frozen candidate's
        out-of-sample run is warned about, not refused. Reads only."""
        return await call(
            correlation.get_sleeve_correlation,
            runner().store,
            run_ids,
            weights=weights,
            drawdown_threshold=drawdown_threshold,
            benchmark_symbol=benchmark_symbol,
            catalog=catalog,
            max_sleeves=ctx.settings.max_sleeves,
            min_days=ctx.settings.min_portfolio_days,
        )

    @server.tool()
    async def run_portfolio_backtest(
        study: str,
        sleeves: list[dict[str, Any]],
        candidate_run_id: str | None = None,
        candidate_weight: float | None = None,
        rebalance: str = "daily",
        benchmark_symbol: str | None = None,
        drawdown_threshold: float = 0.1,
    ) -> dict[str, Any]:
        """Judge the study's candidate sleeve inside the held portfolio: the held sleeves
        (sleeves=[{run_id, weight}], any stored runs) combined as a daily constant-mix of
        their stored curves with and without the candidate, whose weight is
        candidate_weight (default an equal share; the held weights scale down to make
        room). Returns both portfolios' metrics and curves, each sleeve's contribution to
        return and to the worst drawdown, the delta per metric, the candidate's
        correlation and drawdown overlap with the held portfolio, and a regime table for
        all of them. candidate_run_id defaults to the current candidate's newest
        out-of-sample run, else its source run, else the newest in-sample run; before a
        freeze only this study's in-sample runs may take part. Computed at once from
        stored curves (no engine run, no job, no trial counted, no gate judged) and stored
        as a 'portfolio' analysis of the study (get_analysis(kind='portfolio'); exported
        with the study). Weights apply to returns, not dollars; there is no shared
        capital, exposure limit or instrument-overlap effect."""
        return await call(
            portfolio.run_portfolio_backtest,
            runner().store,
            study,
            sleeves=sleeves,
            candidate_run_id=candidate_run_id,
            candidate_weight=candidate_weight,
            rebalance=rebalance,
            benchmark_symbol=benchmark_symbol,
            drawdown_threshold=drawdown_threshold,
            max_sleeves=ctx.settings.max_sleeves,
            min_days=ctx.settings.min_portfolio_days,
        )
