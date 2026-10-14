"""Monte Carlo over a study run's real trades, stored as an analysis (S4.1, S4.2)."""

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies import variability
from src.mcp_server.studies.submit import prepare_in_sample, reserve_trial
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import requires_e2e_data
from tests.integration.mcp_server.study_fixtures import make_ctx, open_study

pytestmark = [pytest.mark.integration, requires_e2e_data]

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
ARGS = dict(
    run_id=None, iterations=1000, method="both", breach_drawdown=0.1, seed=3, max_iterations=50_000
)


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(ctx, symbols=["AAPL"])
    return ctx


async def _in_sample(ctx) -> str:
    job, _ = prepare_in_sample(
        "sma-aapl", params={"fast_period": 5, "slow_period": 20}, **IN_SAMPLE
    )
    recorded = reserve_trial(ctx.runner, job)
    store = ctx.runner.store
    store.update(recorded["job_id"], state="running")
    assert await run_job(store.job_dir(recorded["job_id"])) == 0
    run_id = store.result(recorded["job_id"])["run_id"]
    store.update(recorded["job_id"], state="succeeded", run_id=run_id)
    return run_id


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


async def test_monte_carlo_on_the_newest_in_sample_run(ctx):
    store = ctx.runner.store
    assert _code(variability.run_monte_carlo, store, "sma-aapl", **ARGS) == "no_run"
    run_id = await _in_sample(ctx)
    result = variability.run_monte_carlo(store, "sma-aapl", **ARGS)
    assert result["run_id"] == run_id and result["role"] == "in_sample"
    assert result["trades"] > 0 and result["iterations"] == 1000
    for method in ("bootstrap", "reshuffle"):
        sim = result["simulations"][method]
        assert sim["trades"] == result["trades"] and sim["iterations"] == 1000
        assert sim["max_drawdown"]["p5"] <= sim["max_drawdown"]["p95"] <= 0
        assert 0 <= sim["breach_probability"] <= 1 and sim["breach"] == 0.1
        assert set(sim["cagr"]) == {"p5", "p50", "p95"}
    reshuffle = result["simulations"]["reshuffle"]["final_return"]
    assert reshuffle["p5"] == reshuffle["p95"]  # every trade kept: one final return
    ci = result["confidence_intervals"]
    assert ci["trades"] == result["trades"] and "insufficient_sample" in ci
    assert ci["win_rate"]["point"] is not None
    assert ci["sharpe_ratio"]["point"] is not None  # the run's stored daily equity

    named = variability.run_monte_carlo(store, "sma-aapl", **{**ARGS, "run_id": run_id})
    assert named["analysis_id"] != result["analysis_id"]
    assert _code(variability.run_monte_carlo, store, "sma-aapl", **{**ARGS, "run_id": "nope"}) == (
        "not_a_trial"
    )
    assert _code(variability.run_monte_carlo, store, "sma-aapl", **{**ARGS, "method": "x"}) == (
        "invalid_method"
    )
    assert _code(variability.run_monte_carlo, store, "sma-aapl", **{**ARGS, "iterations": 10}) == (
        "invalid_request"
    )
