"""Gate checks judge a candidate's evidence: pass, fail or missing, never a silent pass (S6.1)."""

import pytest

from src.mcp_server.studies.checks import Evidence, RunFacts, judge

pytestmark = pytest.mark.unit

G1 = {
    "is_profit_factor": 1.3,
    "is_expectancy_gt": 0,
    "beats_benchmark_on_one": ["sharpe_ratio", "calmar_ratio", "max_drawdown"],
    "positive_sub_periods": {"count": 3, "of": 4},
}


def _run(run_id="is", dirty=False, **metrics) -> RunFacts:
    base = {
        "total_trades": 40,
        "profit_factor": 1.5,
        "expectancy": 12.0,
        "sharpe_ratio": 0.8,
        "calmar_ratio": 0.5,
        "max_drawdown": -0.2,
    }
    return RunFacts(run_id=run_id, metrics={**base, **metrics}, git_dirty=dirty)


def _evidence(**overrides) -> Evidence:
    fields = dict(
        in_sample=_run(),
        in_sample_benchmark=_run("bh", sharpe_ratio=0.6, calmar_ratio=0.2, max_drawdown=-0.5),
        sub_period_returns=[0.1, 0.05, -0.02, 0.2],
        trials_used=12,
    )
    return Evidence(**{**fields, **overrides})


def test_a_strong_in_sample_candidate_passes_g1():
    result = judge("G1", G1, _evidence())
    assert result["status"] == "pass", result
    beats = result["checks"]["beats_benchmark_on_one"]
    assert beats["evidence"] == ["is", "bh"] and "sharpe_ratio" in beats["note"]


def test_one_failing_check_fails_the_gate():
    result = judge("G1", G1, _evidence(sub_period_returns=[0.1, -0.1, -0.2, 0.3]))
    assert result["status"] == "fail"
    assert result["checks"]["positive_sub_periods"]["value"] == {"positive": 2, "of": 4}


def test_missing_evidence_is_missing_not_pass():
    result = judge("G1", G1, _evidence(in_sample_benchmark=None))
    assert result["status"] == "missing"
    assert result["checks"]["beats_benchmark_on_one"]["status"] == "missing"


def test_fail_outranks_missing():
    ev = _evidence(in_sample_benchmark=None, in_sample=_run(profit_factor=1.0))
    assert judge("G1", G1, ev)["status"] == "fail"


def test_g0_flags_a_small_sample_and_a_dirty_tree():
    thresholds = {"min_trades": 30, "clean_tree": True, "benchmark_present": True}
    ev = _evidence(in_sample=_run(total_trades=12, dirty=True))
    checks = judge("G0", thresholds, ev)["checks"]
    assert checks["min_trades"]["status"] == "fail"
    assert "insufficient sample" in checks["min_trades"]["note"]
    assert checks["clean_tree"]["status"] == "fail"
    assert checks["benchmark_present"]["status"] == "pass"


def test_g0_wants_an_out_of_sample_benchmark_once_there_is_an_oos_run():
    ev = _evidence(out_of_sample=_run("oos"))
    check = judge("G0", {"benchmark_present": True}, ev)["checks"]["benchmark_present"]
    assert check["status"] == "fail" and "out_of_sample" in check["note"]


def test_g2_before_the_holdout_run_is_missing():
    result = judge("G2", {"oos_sharpe_vs_is": 0.5, "oos_profit_factor": 1.1}, _evidence())
    assert result["status"] == "missing"
    assert set(result["checks"]) == {"oos_sharpe_vs_is", "oos_profit_factor", "oos_run_once"}


def test_g2_judges_the_holdout_run_once():
    ev = _evidence(out_of_sample=_run("oos", sharpe_ratio=0.5), out_of_sample_attempts=1)
    result = judge("G2", {"oos_sharpe_vs_is": 0.5, "oos_profit_factor": 1.1}, ev)
    assert result["status"] == "pass", result
    assert result["checks"]["oos_sharpe_vs_is"]["value"] == pytest.approx(0.625)


def test_an_overridden_holdout_fails_run_once():
    ev = _evidence(
        out_of_sample=_run("oos"), out_of_sample_attempts=2, out_of_sample_overridden=True
    )
    assert judge("G2", {}, ev)["checks"]["oos_run_once"]["status"] == "fail"


def test_a_non_positive_in_sample_sharpe_fails_the_ratio():
    ev = _evidence(
        in_sample=_run(sharpe_ratio=-0.1), out_of_sample=_run("oos"), out_of_sample_attempts=1
    )
    assert (
        judge("G2", {"oos_sharpe_vs_is": 0.5}, ev)["checks"]["oos_sharpe_vs_is"]["status"] == "fail"
    )


def test_phase_4_checks_are_always_missing():
    thresholds = {"neighbourhood_sharpe": 0.7, "breadth": {"instruments": 3}}
    result = judge("G2", thresholds, _evidence(out_of_sample=_run("oos"), out_of_sample_attempts=1))
    assert result["checks"]["neighbourhood_sharpe"]["status"] == "missing"
    assert "phase 4" in result["checks"]["breadth"]["note"]
    assert judge("G3", {"walk_forward_efficiency": 0.5}, _evidence())["status"] == "missing"


def test_paper_gate_and_unknown_checks_and_absent_thresholds_are_missing():
    assert judge("G4", {"weeks": 8}, _evidence())["status"] == "missing"
    unknown = judge("G1", {"is_sortino": 1.0}, _evidence())["checks"]["is_sortino"]
    assert unknown["status"] == "missing" and "no check named" in unknown["note"]
    assert judge("G1", None, _evidence())["status"] == "missing"


def test_many_trials_warn_but_do_not_fail():
    check = judge("G2", {"trials_warn_above": 50}, _evidence(trials_used=60))["checks"][
        "trials_warn_above"
    ]
    assert check["status"] == "pass" and "stronger" in check["note"]


def test_a_metric_the_run_lacks_is_missing():
    ev = _evidence(in_sample=_run(profit_factor=None))
    assert judge("G1", {"is_profit_factor": 1.3}, ev)["checks"]["is_profit_factor"]["status"] == (
        "missing"
    )
