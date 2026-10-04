"""Job tools through an in-memory MCP client, with a fake worker process."""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import Client

from src.mcp_server import validation
from src.mcp_server.context import ServerContext
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.server import build_server
from src.mcp_server.settings import McpSettings

pytestmark = pytest.mark.component

FAKE = Path(__file__).with_name("fake_worker.py")
COVERAGE = {
    "symbol": "AAPL",
    "catalog": "e2e-test",
    "nautilus_id": "AAPL.NASDAQ",
    "backtestable": True,
    "timeframes": {
        "1-DAY": {
            "start": "2000-01-03T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
            "bars": 9,
        }
    },
}
REQUEST = {
    "strategy": "sma",
    "symbol": "AAPL",
    "start": "2018-01-01",
    "end": "2019-12-31",
    "catalog": "e2e-test",
}


def _fake_command(job_dir: Path) -> list[str]:
    # The fake reads its behaviour from spec.behaviour; inject it for real specs.
    request = json.loads((job_dir / "request.json").read_text())
    request["spec"].setdefault("behaviour", "ok-run")
    (job_dir / "request.json").write_text(json.dumps(request))
    return [sys.executable, str(FAKE), str(job_dir)]


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "catalog_availability", lambda symbol, catalog: COVERAGE)
    settings = McpSettings(_env_file=None, jobs_dir=tmp_path / "jobs")
    runner = JobRunner(JobStore(settings.jobs_dir), timeout_s=30, worker_command=_fake_command)
    return ServerContext(settings=settings, runner=runner)


async def _call(client, name, args=None):
    result = await client.call_tool(name, args or {})
    assert not result.is_error, result.content
    return result.structured_content


async def test_submit_then_poll_to_success(ctx):
    async with Client(build_server(ctx)) as client:
        submitted = await _call(client, "submit_backtest", REQUEST)
        assert submitted["ok"] is True, submitted
        assert submitted["resolved"]["strategy"] == "sma_crossover"
        job_id = submitted["job_id"]
        for _ in range(200):
            job = await _call(client, "get_job", {"job_id": job_id})
            if job["state"] not in ("queued", "running"):
                break
            await asyncio.sleep(0.05)
        assert job["state"] == "succeeded", job
        assert job["result"]["run_id"] == "ok-run"
        assert job["request"]["strategy"] == "sma_crossover"
        listed = await _call(client, "list_jobs")
        assert [j["job_id"] for j in listed["jobs"]] == [job_id]


async def test_stored_spec_pins_the_catalog(ctx, monkeypatch):
    ctx.settings.default_catalog = "e2e-test"
    async with Client(build_server(ctx)) as client:
        submitted = await _call(client, "submit_backtest", {**REQUEST, "catalog": None})
    request = ctx.runner.store.request(submitted["job_id"])
    assert request["spec"]["catalog"] == "e2e-test"


async def test_invalid_request_queues_nothing(ctx):
    async with Client(build_server(ctx)) as client:
        result = await _call(client, "submit_backtest", {**REQUEST, "strategy": "nope"})
        assert result["ok"] is False
        assert result["error"]["code"] == "validation_failed"
        assert result["error"]["details"]["errors"][0]["code"] == "unknown_strategy"
        assert (await _call(client, "list_jobs"))["jobs"] == []


async def test_unknown_job_ids(ctx):
    async with Client(build_server(ctx)) as client:
        for tool in ("get_job", "cancel_job"):
            result = await _call(client, tool, {"job_id": "../etc"})
            assert result["error"]["code"] == "unknown_job"


async def test_list_jobs_rejects_unknown_state(ctx):
    async with Client(build_server(ctx)) as client:
        result = await _call(client, "list_jobs", {"state": "exploded"})
    assert result["error"]["code"] == "invalid_state"


async def test_a_second_server_submits_and_the_owner_runs_the_job(ctx, tmp_path):
    """Claude Desktop starts one server per consumer: the second must not be read-only."""
    async with Client(build_server(ctx)) as owner:
        other = JobRunner(
            JobStore(ctx.settings.jobs_dir), timeout_s=30, worker_command=_fake_command
        )
        second = ServerContext(settings=ctx.settings, runner=other)
        async with Client(build_server(second)) as client:
            submitted = await _call(client, "submit_backtest", REQUEST)
            assert submitted["ok"] is True, submitted
            assert submitted["queue"]["owner"] is False
            for _ in range(200):
                job = await _call(client, "get_job", {"job_id": submitted["job_id"]})
                if job["state"] not in ("queued", "running"):
                    break
                await asyncio.sleep(0.05)
            assert job["state"] == "succeeded", job
            assert (await _call(owner, "server_info"))["jobs"]["owner"] is True
            assert (await _call(client, "server_info"))["jobs"]["owner"] is False


async def test_an_unattributed_run_needs_its_fields_and_says_it_is_unattributed(ctx):
    async with Client(build_server(ctx)) as client:
        missing = await _call(client, "submit_backtest", {"strategy": "sma", "symbol": "AAPL"})
        assert missing["ok"] is False
        assert missing["error"]["code"] == "invalid_request"
        assert "start, end" in missing["error"]["message"]
        submitted = await _call(client, "submit_backtest", REQUEST)
        assert "Unattributed: not in a study." in submitted["warnings"]


async def test_create_study_rejects_bad_fields_before_touching_the_database(ctx):
    async with Client(build_server(ctx)) as client:
        study = {
            "slug": "Not A Slug",
            "title": "t",
            "hypothesis": "h",
            "strategy": "sma",
            "symbols": ["AAPL"],
            "in_sample_start": "2018-01-01",
            "in_sample_end": "2019-12-31",
            "out_of_sample_start": "2020-01-01",
            "out_of_sample_end": "2020-12-31",
        }
        refused = await _call(client, "create_study", study)
        assert refused["ok"] is False
        assert refused["error"]["code"] == "invalid_study"
        assert refused["error"]["message"].startswith("slug:")


async def test_server_info_reports_phase_3_and_where_the_gates_come_from(tmp_path, ctx):
    vault = tmp_path / "vault"
    (vault / "System").mkdir(parents=True)
    (vault / "System" / "Gates.md").write_text(
        "```yaml ntrader-gates\nG0:\n  min_trades: 30\n```\n"
    )
    ctx.settings = ctx.settings.model_copy(update={"vault_path": vault})
    async with Client(build_server(ctx)) as client:
        info = await _call(client, "server_info")
    assert info["phase"] == 3
    assert info["gates"]["gate_ids"] == ["G0"] and info["gates"]["problem"] is None
    assert {"get_scorecard", "paper_commands", "get_session"} <= set(info["capabilities"])
