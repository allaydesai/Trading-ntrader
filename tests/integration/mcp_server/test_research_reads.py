"""Trades, equity, regimes, bar export, run search and reproduction on real runs.

Real engine, real e2e-test catalog, real Postgres (throwaway schema); --forked.
"""

import csv
import json
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from src.db.models.research import ResearchTrial
from src.db.repositories.research_repository import SyncResearchRepository
from src.mcp_server.analysis import bar_export, run_reads
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.search import search_runs
from src.mcp_server.studies import lifecycle, references
from src.mcp_server.studies.submit import reserve_trial
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import E2E_CATALOG, requires_e2e_data
from tests.integration.mcp_server.study_fixtures import make_ctx, open_study

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


async def _run(directory: Path, payload: dict) -> dict:
    directory.mkdir()
    (directory / "request.json").write_text(json.dumps(payload))
    assert await run_job(directory) == 0, (directory / "result.json").read_text()
    return json.loads((directory / "result.json").read_text())


@pytest.fixture
async def run_id(isolated_results, tmp_path):
    result = await _run(tmp_path / "first", {"kind": "backtest", "spec": SPEC})
    return result["run_id"]


async def test_trades_page_in_entry_order(run_id):
    first = run_reads.get_trades(run_id, 0, 2, max_limit=500)
    assert first["total"] > 2 and len(first["trades"]) == 2
    assert first["next_offset"] == 2
    entries = [t["entry_timestamp"] for t in first["trades"]]
    assert entries == sorted(entries)
    last = run_reads.get_trades(run_id, first["total"] - 1, 100, max_limit=500)
    assert len(last["trades"]) == 1 and last["next_offset"] is None


async def test_equity_curve_is_downsampled_with_its_drawdown_kept(run_id):
    full = run_reads.get_equity_curve(run_id, 10_000)
    small = run_reads.get_equity_curve(run_id, 20)
    assert full["returned_points"] == full["total_points"] > 20
    assert small["returned_points"] <= 20
    assert small["max_drawdown"] == full["max_drawdown"] < 0
    assert min(p["drawdown"] for p in small["points"]) == pytest.approx(full["max_drawdown"])


async def test_regime_breakdown_splits_by_year_trend_volatility_and_period(run_id):
    result = run_reads.get_regime_breakdown(run_id, None, 4)
    assert [c["label"] for c in result["by_year"]] == ["2018", "2019"]
    assert {c["label"] for c in result["trend"]} <= {"above_sma200", "below_sma200"}
    assert {c["label"] for c in result["volatility"]} == {"low", "mid", "high"}
    assert len(result["sub_periods"]) == 4
    days = sum(c["days"] for c in result["by_year"])
    assert (
        days
        == sum(c["days"] for c in result["trend"])
        == sum(c["days"] for c in result["sub_periods"])
    )
    assert result["notes"] == []  # 300 days of warm-up bars were read


async def test_search_finds_the_run_as_unattributed(run_id, tmp_path):
    found = search_runs(JobStore(tmp_path / "jobs"), strategy="sma", symbol="aapl")["runs"]
    row = next(r for r in found if r["run_id"] == run_id)
    assert row["study"] is None and row["role"] is None
    assert row["headline"]["total_trades"] > 0


async def test_a_reproduction_matches_the_original(run_id, tmp_path):
    payload, job = references.prepare_reproduction(run_id)
    assert job is None  # unattributed: no ledger entry
    result = await _run(tmp_path / "again", payload)
    assert result["reproduction"]["matches"] is True, result["reproduction"]
    assert result["reproduction"]["of"] == run_id
    assert result["config_hash"] == payload["resolved"]["config_hash"]


async def test_reproducing_an_out_of_sample_run_needs_its_own_clean_commit(
    run_id, tmp_path, monkeypatch, isolated_results
):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(ctx)
    with isolated_results.begin() as session:
        repo = SyncResearchRepository(session)
        study = repo.find_study("sma-aapl")
        repo.add_trial(
            ResearchTrial(
                study_pk=study.id,
                version=1,
                role="out_of_sample",
                job_id="oos-job",
                run_id=UUID(run_id),
                state="completed",
                counted=False,
                strategy_type="sma_crossover",
                symbol="AAPL",
                params={},
                config_hash="x",
                start=date(2018, 1, 1),
                end=date(2019, 12, 31),
            )
        )
    monkeypatch.setattr(
        references,
        "git_provenance",
        lambda: type("P", (), {"git_commit": "0" * 40, "git_dirty": False})(),
    )
    with pytest.raises(ToolFailure) as exc:
        references.prepare_reproduction(run_id)
    assert exc.value.code == "holdout_locked"


async def test_a_study_run_is_reproduced_as_an_uncounted_trial(
    run_id, tmp_path, monkeypatch, isolated_results
):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(ctx)
    with isolated_results.begin() as session:
        repo = SyncResearchRepository(session)
        study = repo.find_study("sma-aapl")
        repo.add_trial(
            ResearchTrial(
                study_pk=study.id,
                version=1,
                role="in_sample",
                job_id="is-job",
                run_id=UUID(run_id),
                state="completed",
                counted=True,
                strategy_type="sma_crossover",
                symbol="AAPL",
                params={},
                config_hash="x",
                start=date(2018, 1, 1),
                end=date(2019, 12, 31),
            )
        )
    _, job = references.prepare_reproduction(run_id)
    recorded = reserve_trial(ctx.runner, job)
    assert recorded["role"] == "reproduction"
    assert recorded["budget"]["used"] == 1  # only the original in-sample trial counts
    store = ctx.runner.store
    assert await run_job(store.job_dir(recorded["job_id"])) == 0
    store.update(
        recorded["job_id"], state="succeeded", run_id=store.result(recorded["job_id"])["run_id"]
    )
    # The search settles the just-finished trial itself: no study read needed first.
    roles = [r["role"] for r in search_runs(store, study="sma-aapl")["runs"]]
    assert roles == ["reproduction", "in_sample"]


def test_bars_export_is_clamped_to_in_sample_and_recorded(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(ctx)
    result = bar_export.export_bars(
        ctx.settings,
        "sma-aapl",
        slug="aapl-probe",
        folder="Lab/results",
        start=date(2019, 6, 1),
        end=date(2021, 1, 1),
    )
    assert result["end"] == "2019-12-31" and "locked" in result["clamped"][0]
    with open(result["file"], newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == result["rows"] > 100
    assert rows[-1]["time"] < "2020-01-01"
    assert set(rows[0]) == {"time", "open", "high", "low", "close", "volume"}
    study = lifecycle.get_study(ctx.runner.store, "sma-aapl")["study"]
    assert study["events"][-1]["kind"] == "bars_exported"
    with pytest.raises(ToolFailure) as exc:
        bar_export.export_bars(ctx.settings, "sma-aapl", slug="aapl-probe", folder="Lab/results")
    assert exc.value.code == "file_exists"


def test_benchmarks_are_never_the_study_strategy_and_wait_for_a_freeze(
    tmp_path, monkeypatch, isolated_results
):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(ctx)
    args = dict(params={}, symbol=None, start=None, end=None)
    job, _ = references.prepare_benchmark(
        "sma-aapl", strategy="buy_and_hold", window="in_sample", **args
    )
    recorded = reserve_trial(ctx.runner, job)
    assert recorded["role"] == "benchmark" and recorded["budget"]["used"] == 0
    for strategy, window, code in [
        ("sma", "in_sample", "study_mismatch"),
        ("buy_and_hold", "out_of_sample", "invalid_study_state"),
        ("buy_and_hold", "sideways", "invalid_window"),
    ]:
        with pytest.raises(ToolFailure) as exc:
            references.prepare_benchmark("sma-aapl", strategy=strategy, window=window, **args)
        assert exc.value.code == code
