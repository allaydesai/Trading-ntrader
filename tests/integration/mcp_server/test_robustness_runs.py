"""Breadth on untuned symbols and cost stress of the frozen candidate (S4.4, G2 cost stress)."""

from uuid import UUID

import pytest
from sqlalchemy import select

from src.db.models.trade import Trade
from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies import candidates, lifecycle, references, robustness
from src.mcp_server.studies.batch import reserve_trials
from src.mcp_server.studies.submit import prepare_in_sample, reserve_trial
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import requires_e2e_data, requires_postgres
from tests.integration.mcp_server.study_fixtures import make_ctx, open_study

pytestmark = [pytest.mark.integration, requires_postgres]

PARAMS = {"fast_period": 5, "slow_period": 20}
IN_SAMPLE = dict(
    strategy=None,
    symbol=None,
    start=None,
    end=None,
    timeframe=None,
    catalog=None,
    starting_balance=None,
    over_budget_reason=None,
)


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(ctx, symbols=["AAPL"])
    return ctx


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


async def _finish(ctx, job_id: str) -> str:
    store = ctx.runner.store
    store.update(job_id, state="running")
    assert await run_job(store.job_dir(job_id)) == 0, store.log_tail(job_id, 30)
    run_id = store.result(job_id)["run_id"]
    store.update(job_id, state="succeeded", run_id=run_id)
    return run_id


def test_breadth_needs_untuned_symbols_and_parameters(ctx):
    assert (
        _code(
            robustness.prepare_breadth,
            "sma-aapl",
            symbols=["AAPL"],
            window="in_sample",
            params=PARAMS,
        )
        == "study_mismatch"
    )
    assert (
        _code(
            robustness.prepare_breadth,
            "sma-aapl",
            symbols=["MSFT"],
            window="in_sample",
            params=None,
        )
        == "params_required"
    )
    assert (
        _code(
            robustness.prepare_breadth,
            "sma-aapl",
            symbols=["MSFT"],
            window="out_of_sample",
            params=PARAMS,
        )
        == "holdout_locked"
    )
    assert (
        _code(
            robustness.prepare_breadth, "sma-aapl", symbols=[" "], window="in_sample", params=PARAMS
        )
        == "invalid_request"
    )
    batch, warnings = robustness.prepare_breadth(
        "sma-aapl", symbols=["msft", "MSFT", "goog"], window="in_sample", params=PARAMS
    )
    assert batch.cells == [{"symbol": "MSFT"}, {"symbol": "GOOG"}]
    assert batch.role == "breadth" and batch.candidate_pk is None
    assert batch.request["params"]["fast_period"] == 5 and batch.request["version"] is None
    recorded = reserve_trials(ctx.runner, batch)
    assert recorded["count"] == 2 and all(j["trial"] is None for j in recorded["jobs"])
    study = lifecycle.get_study(ctx.runner.store, "sma-aapl")["study"]
    assert study["budget"]["used"] == 0  # breadth is never a trial
    assert [r["symbol"] for r in study["ledger"]] == ["MSFT", "GOOG"]


def test_cost_stress_needs_a_frozen_candidate_and_a_multiplier_above_one(ctx):
    assert (
        _code(robustness.prepare_cost_stress, "sma-aapl", multiplier=None, gate_multiplier=2)
        == "invalid_study_state"
    )
    assert (
        _code(robustness.prepare_cost_stress, "sma-aapl", multiplier=1.0, gate_multiplier=2)
        == "invalid_request"
    )
    assert (
        robustness.cost_multiplier_of(
            type("G", (), {"thresholds": {"G2": {"cost_stress_multiplier": 3}}})()
        )
        == 3.0
    )
    assert robustness.cost_multiplier_of(type("G", (), {"thresholds": {}})()) is None


@requires_e2e_data
async def test_cost_stress_doubles_the_commission_and_reproduces(ctx, isolated_results):
    store = ctx.runner.store
    job, _ = prepare_in_sample("sma-aapl", params=PARAMS, **IN_SAMPLE)
    base_run = await _finish(ctx, reserve_trial(ctx.runner, job)["job_id"])
    candidates.freeze_candidate(store, "sma-aapl", base_run, None)

    batch, _ = robustness.prepare_cost_stress("sma-aapl", multiplier=None, gate_multiplier=None)
    assert batch.request["multiplier"] == 2.0 and batch.candidate_pk is not None
    assert batch.payloads[0]["resolved"]["cost_multiplier"] == 2.0
    recorded = reserve_trials(ctx.runner, batch)
    stressed_run = await _finish(ctx, recorded["jobs"][0]["job_id"])

    with isolated_results() as db:

        def commission(run_id: str) -> float:
            from src.db.models.backtest import BacktestRun

            pk = db.scalar(select(BacktestRun.id).where(BacktestRun.run_id == UUID(run_id)))
            rows = db.scalars(
                select(Trade.commission_amount).where(Trade.backtest_run_id == pk)
            ).all()
            return float(sum(rows))

        base, stressed = commission(base_run), commission(stressed_run)
        from src.db.models.backtest import BacktestRun

        snapshot = db.scalar(
            select(BacktestRun.config_snapshot).where(BacktestRun.run_id == UUID(stressed_run))
        )
    assert base > 0 and stressed == pytest.approx(2 * base, rel=0.01)
    assert snapshot["cost_multiplier"] == 2.0
    study = lifecycle.get_study(store, "sma-aapl")["study"]
    row = next(r for r in study["ledger"] if r["role"] == "cost_stress")
    assert row["run_id"] == stressed_run and row["counted"] is False
    assert study["budget"]["used"] == 1
    # The stored multiplier reproduces to the same hash, so reproduce_run accepts it.
    payload, trial_job = references.prepare_reproduction(stressed_run)
    assert payload["resolved"]["cost_multiplier"] == 2.0 and trial_job is not None
    assert (
        _code(candidates.freeze_candidate, store, "sma-aapl", stressed_run, None)
        == "invalid_study_state"
    )
