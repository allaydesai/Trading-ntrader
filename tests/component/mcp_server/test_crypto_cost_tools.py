"""``fee_tier``/``spread_bps`` through ``submit_backtest`` and ``validate_config``."""

import pytest
from mcp import Client

from src.mcp_server.server import build_server
from tests.component.mcp_server.test_job_tools import REQUEST, _call, ctx  # noqa: F401  # fixture

pytestmark = pytest.mark.component

BTC = {**REQUEST, "symbol": "BTC", "catalog": "firstrate-crypto"}
TOOLS = ("submit_backtest", "validate_config")


async def test_both_tools_advertise_the_fields(ctx):  # noqa: F811
    async with Client(build_server(ctx)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in TOOLS:
        properties = tools[name].input_schema["properties"]
        assert {"fee_tier", "spread_bps"} <= properties.keys(), name
        assert "fee_tier" in tools[name].description and "spread_bps" in tools[name].description


async def test_a_crypto_run_stores_and_resolves_them(ctx):  # noqa: F811
    async with Client(build_server(ctx)) as client:
        checked = await _call(client, "validate_config", {**BTC, "fee_tier": 4, "spread_bps": 1})
        submitted = await _call(client, "submit_backtest", {**BTC, "fee_tier": 4, "spread_bps": 1})
    assert checked["ok"] is True, checked
    assert submitted["ok"] is True, submitted
    assert submitted["resolved"]["fee_tier"] == 4
    assert submitted["resolved"]["config_hash"] == checked["resolved"]["config_hash"]
    spec = ctx.runner.store.request(submitted["job_id"])["spec"]
    assert (spec["fee_tier"], spec["spread_bps"]) == (4, 1)


async def test_without_them_the_resolved_run_is_unchanged(ctx):  # noqa: F811
    async with Client(build_server(ctx)) as client:
        plain = await _call(client, "validate_config", BTC)
        tiered = await _call(client, "validate_config", {**BTC, "fee_tier": 4})
    assert "fee_tier" not in plain["resolved"] and "spread_bps" not in plain["resolved"]
    assert plain["resolved"]["config_hash"] != tiered["resolved"]["config_hash"]


@pytest.mark.parametrize("field", ["fee_tier", "spread_bps"])
async def test_a_non_crypto_request_queues_nothing(ctx, field):  # noqa: F811
    async with Client(build_server(ctx)) as client:
        result = await _call(client, "submit_backtest", {**REQUEST, field: 2})
        assert result["ok"] is False
        [error] = result["error"]["details"]["errors"]
        assert error["code"] == "invalid_params"
        assert f"{field} is for spot crypto only" in error["message"]
        checked = await _call(client, "validate_config", {**REQUEST, field: 2})
        assert checked["ok"] is False and checked["errors"] == [error]
        assert (await _call(client, "list_jobs"))["jobs"] == []


async def test_an_out_of_schedule_tier_queues_nothing(ctx):  # noqa: F811
    async with Client(build_server(ctx)) as client:
        result = await _call(client, "submit_backtest", {**BTC, "fee_tier": 18})
        assert result["ok"] is False
        [error] = result["error"]["details"]["errors"]
        assert "fee_tier must be a Kraken tier 1..17" in error["message"]
        assert (await _call(client, "list_jobs"))["jobs"] == []


async def test_a_study_run_refuses_them_before_touching_the_study(ctx):  # noqa: F811
    async with Client(build_server(ctx)) as client:
        result = await _call(client, "submit_backtest", {"study": "no-such", "fee_tier": 4})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_request"
        assert "fee_tier cannot be set on a study run" in result["error"]["message"]
        assert (await _call(client, "list_jobs"))["jobs"] == []
