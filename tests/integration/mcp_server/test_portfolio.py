"""Sleeve correlation and the combined portfolio over real stored runs (S9.2–S9.5)."""

import pytest

from src.mcp_server.analysis import correlation, sleeves
from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies.submit import prepare_in_sample, reserve_trial
from src.mcp_server.worker import run_job
from tests.integration.mcp_server.conftest import requires_e2e_data
from tests.integration.mcp_server.study_fixtures import make_ctx, open_study

pytestmark = [pytest.mark.integration, requires_e2e_data]

IN_SAMPLE = dict(
    strategy=None,
    start=None,
    end=None,
    timeframe=None,
    catalog=None,
    starting_balance=None,
    over_budget_reason=None,
)
CORRELATION = dict(
    weights=None,
    drawdown_threshold=0.1,
    benchmark_symbol=None,
    catalog=None,
    max_sleeves=10,
    min_days=252,
)


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch)
    open_study(ctx)
    return ctx


async def _in_sample(ctx, symbol: str, fast: int = 5) -> str:
    job, _ = prepare_in_sample(
        "sma-aapl", params={"fast_period": fast, "slow_period": 20}, symbol=symbol, **IN_SAMPLE
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


async def test_sleeve_correlation_over_two_real_runs(ctx, monkeypatch):
    a, b = await _in_sample(ctx, "AAPL"), await _in_sample(ctx, "MSFT")
    found = correlation.get_sleeve_correlation(ctx.runner.store, [a, b], **CORRELATION)

    assert [s["run_id"] for s in found["sleeves"]] == [a, b]
    assert found["sleeves"][0]["symbol"] == "AAPL" and found["sleeves"][1]["symbol"] == "MSFT"
    assert all("in_sample run of 'sma-aapl'" in s["warnings"][0] for s in found["sleeves"])
    assert found["sleeves"][0]["role"] == "in_sample" and found["sleeves"][0]["study"] == "sma-aapl"
    assert found["weights"] == {a: 0.5, b: 0.5}
    assert found["window"]["days"] >= 252 and found["window"]["start"] >= "2018-01-02"
    matrix = found["correlation"]["matrix"]
    assert matrix[a][a] == 1.0 and matrix[b][b] == 1.0
    assert matrix[a][b] is None or -1 <= matrix[a][b] <= 1
    (pair,) = found["correlation"]["pairs"]
    assert pair == {"a": a, "b": b, "correlation": matrix[a][b]}
    (overlap,) = found["drawdown_overlap"]["pairs"]
    assert 0 <= overlap["both_below"] <= 1 and set(overlap["episodes"]) == {
        "a",
        "b",
        "a_with_b",
        "b_with_a",
    }
    assert set(found["per_sleeve"]) == {a, b} and found["total"]["days"] == found["window"]["days"]
    years = found["regimes"]["by_year"]
    assert [row["label"] for row in years] == ["2018", "2019"]
    assert set(years[0]["return"]) == {a, b, "total"}
    assert found["regimes"]["benchmark"] == "AAPL" and found["regimes"]["catalog"] == "e2e-test"
    assert "window" in found["definitions"]

    assert (
        _code(correlation.get_sleeve_correlation, ctx.runner.store, [a], **CORRELATION)
        == "too_few_sleeves"
    )
    assert (
        _code(correlation.get_sleeve_correlation, ctx.runner.store, [a, a], **CORRELATION)
        == "duplicate_sleeve"
    )
    few = {**CORRELATION, "max_sleeves": 1}
    assert (
        _code(correlation.get_sleeve_correlation, ctx.runner.store, [a, b], **few)
        == "too_many_sleeves"
    )
    for weights in ([1.0], [1.0, -1.0]):
        bad = {**CORRELATION, "weights": weights}
        assert (
            _code(correlation.get_sleeve_correlation, ctx.runner.store, [a, b], **bad)
            == "invalid_weights"
        )
    long = {**CORRELATION, "min_days": 10_000}
    assert (
        _code(correlation.get_sleeve_correlation, ctx.runner.store, [a, b], **long)
        == "window_too_short"
    )
    deep = {**CORRELATION, "drawdown_threshold": 1.5}
    assert (
        _code(correlation.get_sleeve_correlation, ctx.runner.store, [a, b], **deep)
        == "invalid_request"
    )
    monkeypatch.setattr(sleeves, "usable_curve", lambda session, run: None)
    assert (
        _code(correlation.get_sleeve_correlation, ctx.runner.store, [a, b], **CORRELATION)
        == "curve_missing"
    )
