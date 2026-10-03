"""get_run, compare_runs and export_results against runs the real worker persisted.

Results live in a throwaway schema (see conftest). Requires --forked.
"""

import csv
import json

import pytest

from src.mcp_server import runs
from src.mcp_server.export import export_results
from src.mcp_server.settings import McpSettings
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import E2E_CATALOG, requires_e2e_data

pytestmark = [pytest.mark.integration, requires_e2e_data]

WINDOW = {"symbol": "AAPL", "start": "2018-01-01", "end": "2019-12-31", "catalog": E2E_CATALOG}


async def _run(job_dir, name: str, spec: dict) -> str:
    path = job_dir(spec, name)
    assert await run_job(path) == 0, (path / "result.json").read_text()
    return json.loads((path / "result.json").read_text())["run_id"]


async def test_read_compare_and_export(isolated_results, job_dir, tmp_path):
    strategy = await _run(job_dir, "strategy", {**WINDOW, "strategy": "sma_crossover"})
    benchmark = await _run(job_dir, "benchmark", {**WINDOW, "strategy": "buy_and_hold"})

    detail = runs.get_run(strategy)
    assert detail["strategy"] == "sma_crossover"
    assert detail["provenance"]["config_hash"]
    assert detail["metrics"]["total_return"]["unit"] == "fraction"

    comparison = runs.compare_runs([strategy, benchmark], maximum=20)
    assert [r["kind"] for r in comparison["rows"]] == ["strategy", "benchmark"]
    assert comparison["best"]["total_return"]["run_ids"]
    assert comparison["missing"] == []

    vault = tmp_path / "vault"
    (vault / "Lab" / "results").mkdir(parents=True)
    settings = McpSettings(_env_file=None, vault_path=vault)
    out = export_results(settings, [strategy, benchmark], "it-export", "Lab/results", False)
    assert out["runs"] == 2 and out["missing"] == []
    payload = json.loads((vault / "Lab" / "results" / "it-export.json").read_text())
    assert [r["run_id"] for r in payload["runs"]] == [strategy, benchmark]
    assert payload["runs"][1]["trade_stats"]["closed_trades"] == 1
    assert payload["runs"][0]["metrics"]["total_return"] is not None
    assert payload["git"]["commit"]
    trades = list(csv.DictReader((vault / "Lab" / "results" / "it-export.trades.csv").open()))
    assert {t["run_id"] for t in trades} >= {benchmark}
