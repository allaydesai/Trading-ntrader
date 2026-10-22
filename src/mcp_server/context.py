"""What the tools share: settings and, once started, the job runner."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.mcp_server.settings import McpSettings

if TYPE_CHECKING:
    from src.mcp_server.jobs.runner import JobRunner


@dataclass
class ServerContext:
    """Dependencies handed to every tool module at registration."""

    settings: McpSettings = field(default_factory=McpSettings)
    runner: "JobRunner | None" = None
