"""Catalogue tools through an in-memory MCP client (no DB: catalog reads are stubbed)."""

import pytest
from mcp import Client

from src.mcp_server import catalogs, validation
from src.mcp_server.context import ServerContext
from src.mcp_server.errors import ToolFailure
from src.mcp_server.server import build_server
from src.mcp_server.settings import McpSettings

pytestmark = pytest.mark.component

COVERAGE = {
    "symbol": "QQQ",
    "catalog": "e2e-test",
    "nautilus_id": "QQQ.NASDAQ",
    "backtestable": True,
    "timeframes": {
        "1-DAY": {
            "start": "2000-01-03T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
            "bars": 9,
        }
    },
}


@pytest.fixture
def server(tmp_path):
    return build_server(ServerContext(settings=McpSettings(_env_file=None, jobs_dir=tmp_path)))


async def _call(server, name, args=None):
    async with Client(server) as client:
        result = await client.call_tool(name, args or {})
    assert not result.is_error, result.content
    return result.structured_content


async def test_list_strategies_includes_benchmark(server):
    data = await _call(server, "list_strategies")
    assert data["ok"] is True
    names = {s["name"] for s in data["strategies"]}
    assert {"sma_crossover", "buy_and_hold"} <= names


async def test_describe_unknown_strategy_is_data_not_a_crash(server):
    data = await _call(server, "describe_strategy", {"name": "nope"})
    assert data["ok"] is False
    assert data["error"]["code"] == "unknown_strategy"
    assert "sma_crossover" in data["error"]["fix"]


async def test_catalog_availability_failure_carries_fix(server, monkeypatch):
    def missing(symbol, catalog):
        raise ToolFailure("symbol_not_in_catalog", "not there", fix="import it")

    monkeypatch.setattr(catalogs, "catalog_availability", missing)
    data = await _call(server, "catalog_availability", {"symbol": "ZZZ", "catalog": "e2e-test"})
    assert data == {
        "ok": False,
        "error": {"code": "symbol_not_in_catalog", "message": "not there", "fix": "import it"},
    }


async def test_validate_config_resolves_and_reports_coverage(server, monkeypatch):
    monkeypatch.setattr(validation, "catalog_availability", lambda symbol, catalog: COVERAGE)
    data = await _call(
        server,
        "validate_config",
        {
            "strategy": "sma",
            "symbol": "qqq",
            "start": "2010-01-01",
            "end": "2015-12-31",
            "catalog": "e2e-test",
            "params": {"fast_period": 5},
        },
    )
    assert data["ok"] is True, data
    assert data["resolved"]["strategy"] == "sma_crossover"
    assert data["resolved"]["params"]["fast_period"] == 5
    assert len(data["resolved"]["config_hash"]) == 64


async def test_validate_config_rejects_bad_balance_with_fix(server):
    data = await _call(
        server,
        "validate_config",
        {
            "strategy": "sma",
            "symbol": "QQQ",
            "start": "2010-01-01",
            "end": "2015-12-31",
            "catalog": "e2e-test",
            "starting_balance": -5,
        },
    )
    assert data["ok"] is False
    assert data["error"]["code"] == "invalid_request"
