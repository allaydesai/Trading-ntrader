"""Run detail and side-by-side comparison (S3.3)."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.db.models.backtest import PerformanceMetrics
from src.mcp_server.errors import ToolFailure
from src.mcp_server.metrics import METRIC_SPECS
from src.mcp_server.runs import compare_view, parse_run_ids, run_view

pytestmark = pytest.mark.unit

UTC = timezone.utc


def _metrics(**values):
    base = {c.name: None for c in PerformanceMetrics.__table__.columns}
    base.update(total_trades=10, winning_trades=6, losing_trades=4)
    base.update(values)
    return SimpleNamespace(**base)


def _run(strategy="sma_crossover", params=None, dirty=False, **metric_values):
    return SimpleNamespace(
        run_id=uuid4(),
        strategy_name=strategy.replace("_", " ").title(),
        strategy_type=strategy,
        instrument_symbol="QQQ",
        start_date=datetime(2000, 1, 1, tzinfo=UTC),
        end_date=datetime(2015, 12, 31, tzinfo=UTC),
        initial_capital=Decimal("1000000"),
        data_source="catalog:firstrate-etf",
        execution_status="success",
        execution_duration_seconds=Decimal("4.2"),
        error_message=None,
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
        run_type="backtest",
        reproduced_from_run_id=None,
        git_commit="a" * 40,
        git_dirty=dirty,
        strategies_commit="b" * 40,
        config_hash="c" * 64,
        config_snapshot={"config": params or {}, "bar_type": "1-DAY-LAST"},
        metrics=_metrics(**metric_values),
    )


def test_every_metric_column_has_a_spec():
    columns = {c.name for c in PerformanceMetrics.__table__.columns}
    assert set(METRIC_SPECS) == columns - {"id", "backtest_run_id", "created_at"}


def test_run_view_has_provenance_params_and_units():
    run = _run(
        params={"fast_period": 10}, total_return=Decimal("0.25"), sharpe_ratio=Decimal("1.1")
    )
    view = run_view(run)
    assert view["run_id"] == str(run.run_id)
    assert view["provenance"]["config_hash"] == "c" * 64
    assert view["params"] == {"fast_period": 10}
    assert view["metrics"]["total_return"] == {"value": 0.25, "unit": "fraction"}
    assert view["metrics"]["sharpe_ratio"]["unit"] == "ratio"
    assert view["kind"] == "strategy"
    assert view["warnings"] == []


def test_dirty_code_is_flagged():
    assert "uncommitted" in run_view(_run(dirty=True))["warnings"][0]


def test_benchmark_runs_are_labelled():
    assert run_view(_run(strategy="buy_and_hold"))["kind"] == "benchmark"


def test_compare_marks_best_per_metric_by_direction():
    a = _run(total_return=Decimal("0.30"), max_drawdown=Decimal("-0.40"), volatility=Decimal("0.2"))
    b = _run(total_return=Decimal("0.10"), max_drawdown=Decimal("-0.10"), volatility=Decimal("0.1"))
    view = compare_view([a, b], requested=[str(a.run_id), str(b.run_id)])
    assert view["best"]["total_return"]["run_ids"] == [str(a.run_id)]
    assert view["best"]["max_drawdown"]["run_ids"] == [str(b.run_id)]  # nearer zero
    assert view["best"]["volatility"]["run_ids"] == [str(b.run_id)]  # lower is better
    assert "total_trades" not in view["best"]  # no direction


def test_compare_lists_differing_params_and_ties():
    a = _run(params={"fast_period": 10, "slow_period": 20}, sharpe_ratio=Decimal("1"))
    b = _run(params={"fast_period": 5, "slow_period": 20}, sharpe_ratio=Decimal("1"))
    view = compare_view([a, b], requested=[str(a.run_id), str(b.run_id)])
    assert view["differing_params"] == ["fast_period"]
    assert view["rows"][0]["params"] == {"fast_period": 10}
    assert len(view["best"]["sharpe_ratio"]["run_ids"]) == 2


def test_compare_keeps_requested_order_and_reports_missing():
    a, b = _run(), _run(strategy="buy_and_hold")
    missing = str(uuid4())
    view = compare_view([b, a], requested=[str(a.run_id), missing, str(b.run_id)])
    assert [r["run_id"] for r in view["rows"]] == [str(a.run_id), str(b.run_id)]
    assert view["missing"] == [missing]
    assert view["rows"][1]["kind"] == "benchmark"


def test_compare_warns_when_windows_differ():
    a, b = _run(), _run()
    b.start_date = datetime(2005, 1, 1, tzinfo=UTC)
    view = compare_view([a, b], requested=[str(a.run_id), str(b.run_id)])
    assert any("window" in w for w in view["warnings"])


def test_parse_run_ids():
    run_id = str(uuid4())
    assert parse_run_ids([run_id, run_id], minimum=1, maximum=20) == [run_id]
    with pytest.raises(ToolFailure) as exc:
        parse_run_ids(["not-a-uuid"], minimum=1, maximum=20)
    assert exc.value.code == "invalid_run_id"
    with pytest.raises(ToolFailure) as exc:
        parse_run_ids([run_id], minimum=2, maximum=20)
    assert exc.value.code == "too_few_runs"
    with pytest.raises(ToolFailure) as exc:
        parse_run_ids([str(uuid4()) for _ in range(3)], minimum=2, maximum=2)
    assert exc.value.code == "too_many_runs"
