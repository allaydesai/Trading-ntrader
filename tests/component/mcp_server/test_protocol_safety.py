"""Pinned tool set and a clean protocol stream (spec: phase exit check safety tests)."""

import os

import pytest
from mcp import Client

from src.mcp_server.server import REGISTERED_TOOLS, build_server
from tests.component.mcp_server.raw_stdio import RawStdioServer, non_protocol_lines

pytestmark = pytest.mark.component

#: The tool set, written out by hand. Adding or removing a tool means editing
#: this literal and REGISTERED_TOOLS together, deliberately.
PINNED_TOOLS = frozenset(
    {
        "server_info",
        "list_strategies",
        "describe_strategy",
        "list_catalogs",
        "catalog_availability",
        "validate_config",
        "submit_backtest",
        "get_job",
        "list_jobs",
        "cancel_job",
        "get_run",
        "compare_runs",
        "export_results",
        # phase 2
        "create_study",
        "get_study",
        "list_studies",
        "update_study",
        "submit_benchmark",
        "reproduce_run",
        "get_trades",
        "get_equity_curve",
        "get_regime_breakdown",
        "export_bars",
        "search_runs",
    }
)


async def test_registered_tools_are_exactly_the_pinned_set(tmp_path, monkeypatch):
    monkeypatch.setenv("NTRADER_MCP_JOBS_DIR", str(tmp_path))
    async with Client(build_server()) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
    # Pinned against the live registration, not only against another literal,
    # so a tool registered without being declared (or declared, not registered)
    # goes red in both directions.
    assert listed == REGISTERED_TOOLS
    assert REGISTERED_TOOLS == PINNED_TOOLS


def test_no_tool_can_trade_or_manage_sessions():
    """Spec: "Not provided, on purpose" — no orders, sessions, reconcile, import or fetch."""
    words = ("order", "session", "reconcile", "import", "fetch", "live", "flatten")
    assert [t for t in REGISTERED_TOOLS if any(w in t for w in words)] == []


def test_stdout_carries_only_protocol_messages(tmp_path):
    env = {**os.environ, "NTRADER_MCP_JOBS_DIR": str(tmp_path / "jobs")}
    server = RawStdioServer(env)
    try:
        server.initialize()
        assert server.request("tools/list")["result"]["tools"]
        assert server.call_tool("list_strategies")["ok"] is True
        server.call_tool("server_info")  # touches git, DB and catalogs: all of it logs
        assert server.call_tool("describe_strategy", {"name": "nope"})["ok"] is False
    finally:
        lines = server.close()
    assert lines, "the server wrote nothing at all"
    assert non_protocol_lines(lines) == []


def test_probe_the_stdout_check_flags_stray_output():
    lines = ['{"jsonrpc": "2.0", "id": 1, "result": {}}\n', "logging_configured\n", "{}\n"]
    assert non_protocol_lines(lines) == ["logging_configured\n", "{}\n"]
