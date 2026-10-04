"""Paper tools: the commands for a paper session and its reading against the band (S6.2, S7.1-S7.3).

The server never starts, stops, creates or seals a session: paper_commands
returns text for Allay to run, and the session tools only read.
"""

from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.paper import handoff, sessions
from src.mcp_server.tools import call

TOOL_NAMES = ("paper_commands", "list_sessions", "get_session")


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the paper tools to ``server``."""

    @server.tool()
    async def paper_commands(
        study: str, version: int | None = None, name: str | None = None
    ) -> dict[str, Any]:
        """The exact commands to start a paper session of a study's frozen candidate.

        Text only: Allay runs them. Every frozen parameter is passed explicitly and the
        session is linked (--compare-to) to the candidate's newest out-of-sample run,
        which must exist. name defaults to <study>-v<version>-paper. Failing or missing
        gates, contamination, code changed since freezing and .env settings that would
        change a parameter come back as warnings, with the expectation band.
        """
        assert ctx.runner is not None, "build_server always creates the runner"
        return await call(
            handoff.paper_commands, ctx.settings, ctx.runner.store, study, version, name
        )

    @server.tool()
    async def list_sessions(
        status: str | None = None, study: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """Paper sessions, newest first: status, whether a running one is alive, closed
        trades, weeks elapsed, and the study and candidate version whose out-of-sample run
        each is compared against. status: created, running, stopped or sealed.
        """
        return await call(sessions.list_sessions, status, study, limit)

    @server.tool()
    async def get_session(
        session: str, weeks: float | None = None, recent_trades: int = 20
    ) -> dict[str, Any]:
        """A paper session (name or id) beside its expectation band, for the weekly check.

        The band comes from the compare-to run's own trades: trade count over stretches as
        long as the session has run (or `weeks`), win rate, average trade and drawdown over
        runs of as many trades as it has closed; each is inside, below or above. Drift
        flags name a likely cause: signals (missed or extra), execution (rejections,
        costs), config (spec differs from the frozen candidate) or strategy_or_regime.
        Slippage is not measurable. Includes G4 progress and the newest trades.
        """
        return await call(
            sessions.get_session, ctx.settings, session, weeks=weeks, recent=recent_trades
        )
