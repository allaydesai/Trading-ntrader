"""Run tools: get_run, compare_runs, export_results (S3.3, S8.1)."""

from typing import Any

from mcp.server import MCPServer

from src.mcp_server import export, runs
from src.mcp_server.context import ServerContext
from src.mcp_server.studies import record
from src.mcp_server.tools import call

TOOL_NAMES = ("get_run", "compare_runs", "export_results")


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the run tools to ``server``."""

    @server.tool()
    async def get_run(run_id: str) -> dict[str, Any]:
        """A persisted run: window, provenance (commit, dirty, config hash), params, metrics.

        Metrics carry units: fraction (0.25 = 25 %), ratio, currency, count, percent.
        """
        return await call(runs.get_run, run_id)

    @server.tool()
    async def compare_runs(run_ids: list[str]) -> dict[str, Any]:
        """Compare 2-20 runs side by side: differing params, best run per metric.

        Include a buy_and_hold run on the same window so no result is read in isolation.
        A metric that any of the runs lacks has no best.
        """
        return await call(runs.compare_runs, run_ids, maximum=ctx.settings.max_compare)

    @server.tool()
    async def export_results(
        slug: str,
        run_ids: list[str] | None = None,
        study: str | None = None,
        folder: str = "Lab/results",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Write runs to the vault as <slug>.json and <slug>.trades.csv (runner format).

        With study, also writes <slug>.study.json (study, ledger, candidates,
        decisions and scorecard); run_ids then default to the study's completed
        runs. Only folders in NTRADER_MCP_EXPORT_FOLDERS are writable; an existing
        slug needs overwrite=true.
        """
        if study is not None:
            assert ctx.runner is not None, "build_server always creates the runner"
            return await call(
                record.export_study,
                ctx.settings,
                ctx.runner.store,
                study,
                run_ids=run_ids,
                slug=slug,
                folder=folder,
                overwrite=overwrite,
            )
        return await call(
            export.export_results, ctx.settings, run_ids or [], slug, folder, overwrite
        )
