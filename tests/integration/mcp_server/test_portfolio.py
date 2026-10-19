"""Sleeve correlation and the combined portfolio over real stored runs (S9.2–S9.5)."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from src.core.robustness.portfolio import METRIC_KEYS
from src.mcp_server.analysis import correlation, sleeves
from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies import lifecycle, portfolio, record, walkforward
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


PORTFOLIO = dict(
    candidate_run_id=None,
    candidate_weight=None,
    rebalance="daily",
    benchmark_symbol=None,
    drawdown_threshold=0.1,
    max_sleeves=10,
    min_days=252,
)


async def test_portfolio_backtest_stores_a_completed_analysis_of_the_study(ctx):
    store = ctx.runner.store
    held, candidate = await _in_sample(ctx, "MSFT"), await _in_sample(ctx, "AAPL", fast=10)
    found = portfolio.run_portfolio_backtest(
        store, "sma-aapl", sleeves=[{"run_id": held, "weight": 1}], **PORTFOLIO
    )

    assert found["kind"] == "portfolio" and found["status"] == "completed"
    assert found["candidate"]["run_id"] == candidate
    assert found["candidate"]["chosen_by"] == "newest_in_sample"
    assert found["candidate"]["role"] == "in_sample" and found["candidate"]["weight"] == 0.5
    assert [s["run_id"] for s in found["sleeves"]] == [held]
    assert found["weights"] == {"with": {held: 0.5, candidate: 0.5}, "without": {held: 1.0}}
    with_, without = found["with_candidate"], found["without_candidate"]
    assert with_["metrics"]["days"] == without["metrics"]["days"] == found["window"]["days"]
    assert set(METRIC_KEYS) <= set(found["delta"]) and found["delta"]["days"]["delta"] == 0
    assert set(with_["contribution"]["return"]) == {held, candidate}
    assert len(with_["curve"]) <= 200 and {"time", "equity", "drawdown"} == set(with_["curve"][0])
    pair = found["candidate_vs_held"]
    assert pair["correlation"] is None or -1 <= pair["correlation"] <= 1
    assert pair["drawdown_overlap"]["a"] == "candidate"
    years = found["regimes"]["by_year"]
    assert set(years[0]["return"]) == {held, candidate, "with_candidate", "without_candidate"}
    assert found["regimes"]["benchmark"] == "AAPL"

    stored = walkforward.get_analysis(store, "sma-aapl", analysis_id=None, kind="portfolio")
    assert stored["analysis"]["analysis_id"] == found["analysis_id"]
    assert stored["analysis"]["result"]["delta"] == found["delta"]
    assert stored["analysis"]["request"]["sleeves"] == [{"run_id": held, "weight": 1.0}]
    study = lifecycle.get_study(store, "sma-aapl")["study"]
    assert [a["kind"] for a in study["analyses"]] == ["portfolio"]
    assert study["status"] == "exploring" and study["budget"]["used"] == 2

    exported = record.export_study(
        ctx.settings,
        store,
        "sma-aapl",
        run_ids=None,
        slug="port",
        folder="Lab/results",
        overwrite=False,
    )
    written = json.loads(Path(exported["files"]["study"]).read_text())
    assert written["portfolio"]["analysis_id"] == found["analysis_id"]

    dup = dict(PORTFOLIO, candidate_run_id=held)
    args = (store, "sma-aapl")
    kw = dict(sleeves=[{"run_id": held, "weight": 1}])
    assert _code(portfolio.run_portfolio_backtest, *args, **kw, **dup) == "duplicate_sleeve"
    assert (
        _code(portfolio.run_portfolio_backtest, *args, sleeves=[], **PORTFOLIO) == "too_few_sleeves"
    )
    monthly = dict(PORTFOLIO, rebalance="monthly")
    assert _code(portfolio.run_portfolio_backtest, *args, **kw, **monthly) == "invalid_request"
    unknown = dict(PORTFOLIO, candidate_run_id=str(uuid4()))
    assert _code(portfolio.run_portfolio_backtest, *args, **kw, **unknown) == "not_a_trial"
    heavy = dict(PORTFOLIO, candidate_weight=1.5)
    assert _code(portfolio.run_portfolio_backtest, *args, **kw, **heavy) == "invalid_weights"
