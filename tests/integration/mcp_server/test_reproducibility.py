"""The same spec run twice gives the same result (MCP spec S2.3).

The fill model draws random numbers for slippage; unseeded, two runs of one
config drifted apart. Results live in a throwaway schema (see conftest).
Requires --forked.
"""

import json

import pytest

from src.mcp_server import runs
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import E2E_CATALOG, requires_e2e_data

pytestmark = [pytest.mark.integration, requires_e2e_data]

# Hourly bars over several years: enough fills for an unseeded run to drift.
SPEC = {
    "strategy": "sma_crossover",
    "symbol": "AAPL",
    "start": "2015-01-01",
    "end": "2019-12-31",
    "timeframe": "1-HOUR",
    "catalog": E2E_CATALOG,
    "params": {"fast_period": 5, "slow_period": 20},
}


async def _run(job_dir, name: str) -> dict:
    path = job_dir(SPEC, name)
    assert await run_job(path) == 0, (path / "result.json").read_text()
    return runs.get_run(json.loads((path / "result.json").read_text())["run_id"])


async def test_the_same_spec_gives_the_same_metrics(isolated_results, job_dir):
    first = await _run(job_dir, "first")
    second = await _run(job_dir, "second")

    assert first["metrics"]["total_trades"]["value"] > 100, "too few fills to prove anything"
    assert first["provenance"]["config_hash"] == second["provenance"]["config_hash"]
    assert first["metrics"] == second["metrics"]
