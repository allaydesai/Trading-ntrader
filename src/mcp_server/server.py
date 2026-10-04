"""Build the research MCP server and its pinned tool set."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.jobs import service
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.tools import analysis, catalogue, info, jobs, research, runs, studies

#: Every tool this server registers. Pinned by test against the live registration
#: and a literal, so adding or dropping a tool is always a deliberate, visible edit.
REGISTERED_TOOLS: frozenset[str] = frozenset(
    (
        *info.TOOL_NAMES,
        *catalogue.TOOL_NAMES,
        *jobs.TOOL_NAMES,
        *runs.TOOL_NAMES,
        *studies.TOOL_NAMES,
        *research.TOOL_NAMES,
        *analysis.TOOL_NAMES,
    )
)

INSTRUCTIONS = """\
NTrader research server. Backtests run natively on the Mac against NTrader's
Parquet catalogs and are persisted in NTrader's database (visible in its web UI).
Typical flow: list_strategies / describe_strategy -> catalog_availability ->
validate_config -> submit_backtest (returns a job id at once) -> get_job until it
finishes -> get_run / compare_runs (always beside a buy_and_hold benchmark) ->
export_results to file the evidence in the vault. Failures come back as
{ok: false, error: {code, message, fix}}. This server never trades, never starts
or stops sessions, and never fetches or imports data.
"""


def build_server(ctx: ServerContext | None = None) -> MCPServer:
    """A server with every tool in ``REGISTERED_TOOLS`` registered."""
    ctx = ctx or ServerContext()
    if ctx.runner is None:
        store = JobStore(ctx.settings.jobs_dir)
        ctx.runner = JobRunner(
            store,
            timeout_s=ctx.settings.job_timeout_s,
            find_run=partial(service.saved_run_of, store),
        )
    runner = ctx.runner

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        # Resumes jobs a previous server left queued and adopts its live worker.
        # No shutdown step: a worker outlives the server, and the next one adopts it.
        runner.start()
        yield

    server = MCPServer("ntrader-research", instructions=INSTRUCTIONS, lifespan=lifespan)
    info.register(server, ctx, capabilities=sorted(REGISTERED_TOOLS))
    catalogue.register(server, ctx)
    jobs.register(server, ctx)
    runs.register(server, ctx)
    studies.register(server, ctx)
    research.register(server, ctx)
    analysis.register(server, ctx)
    return server
