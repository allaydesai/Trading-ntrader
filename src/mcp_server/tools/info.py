"""``server_info`` tool."""

from typing import Any

from mcp.server import MCPServer

from src.mcp_server import info
from src.mcp_server.context import ServerContext
from src.mcp_server.tools import call

TOOL_NAMES = ("server_info",)


def register(server: MCPServer, ctx: ServerContext, *, capabilities: list[str]) -> None:
    """Add ``server_info`` to ``server``."""

    @server.tool()
    async def server_info() -> dict[str, Any]:
        """Versions, git state, database and catalog health, job queue, limits, capabilities."""
        return await call(info.server_info, ctx, capabilities)
