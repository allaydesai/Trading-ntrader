"""Entry point: ``uv run python -m src.mcp_server`` (stdio transport).

stdout carries the MCP protocol and nothing else. Logging is pointed at stderr
before anything that might log is imported, and every backtest runs in a child
process whose output goes to its job log, never to this process's stdout.
"""

import sys


def main() -> None:
    """Configure stderr logging, then serve over stdio until the client disconnects."""
    from src.utils.logging import configure_logging

    configure_logging(stream=sys.stderr)

    from src.mcp_server.server import build_server

    build_server().run("stdio")


if __name__ == "__main__":
    main()
