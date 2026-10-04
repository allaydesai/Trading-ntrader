"""The worker runs a real backtest from a job directory and verifies it was saved.

Real engine, real e2e-test catalog, real Postgres; results land in a throwaway
schema (see conftest). Requires --forked (Nautilus C/Rust extension isolation).
"""

import json

import pytest

from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.mcp_server.request import BacktestSpec, resolve
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import E2E_CATALOG, requires_e2e_data

pytestmark = [pytest.mark.integration, requires_e2e_data]

SPEC = {
    "strategy": "sma_crossover",
    "symbol": "AAPL",
    "start": "2018-01-01",
    "end": "2019-12-31",
    "catalog": E2E_CATALOG,
    "params": {"fast_period": 5, "slow_period": 20},
    "starting_balance": "100000",
}


def _result(job_dir) -> dict:
    return json.loads((job_dir / "result.json").read_text())


def _public_count(run_id: str) -> int:
    from sqlalchemy import create_engine, text

    from src.config import get_settings

    engine = create_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            sql = text("SELECT count(*) FROM public.backtest_runs WHERE run_id = :r")
            return conn.execute(sql, {"r": run_id}).scalar_one()
    finally:
        engine.dispose()


async def test_strategy_run_is_persisted_with_provenance(isolated_results, job_dir):
    path = job_dir(SPEC)
    assert await run_job(path) == 0
    assert _public_count(_result(path)["run_id"]) == 0, "run leaked into the real results table"

    result = _result(path)
    assert result["status"] == "ok", result
    expected_hash = resolve(BacktestSpec(**SPEC), default_catalog="").config_hash
    assert result["config_hash"] == expected_hash
    assert set(result["headline"]) >= {"total_return", "sharpe_ratio", "total_trades"}
    assert json.loads((path / "progress.json").read_text())["phase"] == "done"

    with isolated_results() as session:
        from uuid import UUID

        run = SyncBacktestRepository(session).find_by_run_id(UUID(result["run_id"]))
        assert run is not None
        assert run.data_source == f"catalog:{E2E_CATALOG}"
        assert run.config_hash == expected_hash
        assert run.git_commit and len(run.git_commit) == 40
        assert run.git_dirty is not None


async def test_benchmark_run(isolated_results, job_dir):
    path = job_dir({**SPEC, "strategy": "buy_and_hold", "params": {}})
    assert await run_job(path) == 0
    result = _result(path)
    assert result["headline"]["total_trades"] == 1


async def test_missing_symbol_fails_fast_with_a_fix(isolated_results, job_dir):
    path = job_dir({**SPEC, "symbol": "NOSUCHTICKER"})
    assert await run_job(path) == 1
    error = _result(path)["error"]
    assert error["code"] == "data_not_found"
    assert "NOSUCHTICKER" in error["message"]


async def test_invalid_params_fail_before_loading(isolated_results, job_dir):
    path = job_dir({**SPEC, "params": {"fast_perid": 5}})
    assert await run_job(path) == 2
    assert _result(path)["error"]["code"] == "invalid_params"
    assert json.loads((path / "progress.json").read_text())["phase"] == "resolving"
