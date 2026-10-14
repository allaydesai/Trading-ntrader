"""The phase-4 robustness checks: pass, fail or missing with the tool to call."""

import pytest

from src.mcp_server.studies.checks import judge
from src.mcp_server.studies.checks_base import (
    BreadthFacts,
    DeflatedSharpeFacts,
    Evidence,
    MonteCarloFacts,
    NeighbourhoodFacts,
    RunFacts,
    StressFacts,
    WalkForwardFacts,
)

pytestmark = pytest.mark.unit


def _run(run_id="r", **metrics) -> RunFacts:
    base = {"total_return": 0.2, "profit_factor": 1.5, "sharpe_ratio": 0.8, "max_drawdown": -0.2}
    return RunFacts(run_id=run_id, metrics={**base, **metrics}, git_dirty=False)


def _check(gate, name, threshold, **evidence):
    return judge(gate, {name: threshold}, Evidence(**evidence))["checks"][name]


def _neighbourhood(min_ratio, chosen=1.0):
    return NeighbourhoodFacts(
        sweep="s1",
        cell_run_id="c",
        chosen=chosen,
        neighbours={"n1": chosen * min_ratio, "n2": chosen},
        min_ratio=min_ratio,
        median_ratio=(min_ratio + 1) / 2,
    )


def test_neighbourhood_passes_on_a_plateau_and_fails_on_a_spike():
    plateau = _check("G2", "neighbourhood_sharpe", 0.7, neighbourhood=_neighbourhood(0.8))
    assert plateau["status"] == "pass" and plateau["evidence"][0] == "analysis:s1"
    spike = _check("G2", "neighbourhood_sharpe", 0.7, neighbourhood=_neighbourhood(0.3))
    assert spike["status"] == "fail" and "plateau" in spike["note"]
    flat = _check("G2", "neighbourhood_sharpe", 0.7, neighbourhood=_neighbourhood(0.9, chosen=-0.1))
    assert flat["status"] == "fail" and "not positive" in flat["note"]
    none = NeighbourhoodFacts("s1", "c", 1.0, {}, None, None)
    assert _check("G2", "neighbourhood_sharpe", 0.7, neighbourhood=none)["status"] == "missing"


def test_cost_stress_passes_when_still_profitable_at_the_gates_multiplier():
    ok = StressFacts("a1", _run("stress", total_return=0.05), multiplier=2.0)
    assert _check("G2", "cost_stress_multiplier", 2, cost_stress=ok)["status"] == "pass"
    broke = StressFacts("a1", _run("stress", total_return=-0.01), multiplier=2.0)
    row = _check("G2", "cost_stress_multiplier", 2, cost_stress=broke)
    assert row["status"] == "fail" and "2x" in row["note"]
    weak = StressFacts("a1", _run("stress", total_return=0.05), multiplier=1.5)
    row = _check("G2", "cost_stress_multiplier", 2, cost_stress=weak)
    assert row["status"] == "missing" and "multiplier=2" in row["note"]


def test_breadth_counts_untuned_symbols_above_the_bar():
    runs = {
        "SPY": _run("spy", profit_factor=1.4),
        "IWM": _run("iwm", profit_factor=1.2),
        "DIA": _run("dia", profit_factor=0.9),
        "XLK": _run("xlk", profit_factor=1.3),
    }
    facts = BreadthFacts(analyses=["b1"], runs=runs)
    row = _check("G2", "breadth", {"instruments": 3, "profit_factor": 1.1}, breadth=facts)
    assert row["status"] == "pass" and row["value"]["passing"] == ["IWM", "SPY", "XLK"]
    row = _check("G2", "breadth", {"instruments": 4, "profit_factor": 1.1}, breadth=facts)
    assert row["status"] == "fail" and "DIA" in row["note"]
    few = BreadthFacts(analyses=["b1"], runs={"SPY": runs["SPY"]})
    row = _check("G2", "breadth", {"instruments": 3}, breadth=few)
    assert row["status"] == "missing" and "submit_breadth more" in row["note"]
    assert _check("G2", "breadth", 2, breadth=facts)["status"] == "pass"  # a bare count


def test_walk_forward_efficiency_against_the_bar():
    good = WalkForwardFacts("w1", 0.8, 5, 5, 1, [], ["r1", "r2"])
    row = _check("G3", "walk_forward_efficiency", 0.5, walk_forward=good)
    assert row["status"] == "pass" and "1 window(s) lost money" in row["note"]
    bad = WalkForwardFacts("w1", 0.2, 5, 4, 3, [2], ["r1"])
    row = _check("G3", "walk_forward_efficiency", 0.5, walk_forward=bad)
    assert row["status"] == "fail" and "excluded" in row["note"]
    undefined = WalkForwardFacts("w1", None, 5, 0, 5, [1, 2, 3, 4, 5], [])
    assert _check("G3", "walk_forward_efficiency", 0.5, walk_forward=undefined)["status"] == "fail"


def test_deflated_sharpe_probability_against_the_bar():
    strong = DeflatedSharpeFacts("is", 0.99, 31, 1.1, 0.02, 4000)
    row = _check("G2", "deflated_sharpe_prob", 0.95, in_sample=_run("is"), deflated_sharpe=strong)
    assert row["status"] == "pass" and row["evidence"] == ["is"] and row["value"]["trials"] == 31
    lucky = DeflatedSharpeFacts("is", 0.6, 200, 0.5, 0.1, 500)
    row = _check("G2", "deflated_sharpe_prob", 0.95, in_sample=_run("is"), deflated_sharpe=lucky)
    assert row["status"] == "fail" and "200 trials" in row["note"]


def test_oos_drawdown_within_the_monte_carlo_band():
    mc = MonteCarloFacts(
        "m1", "is", "bootstrap", drawdown_p5=-0.3, breach_probability=0.1, breach=0.2
    )
    inside = _check(
        "G2",
        "oos_drawdown_within_mc",
        True,
        monte_carlo=mc,
        out_of_sample=_run("oos", max_drawdown=-0.2),
    )
    assert inside["status"] == "pass" and inside["value"]["drawdowns_are_negative"] is True
    outside = _check(
        "G2",
        "oos_drawdown_within_mc",
        True,
        monte_carlo=mc,
        out_of_sample=_run("oos", max_drawdown=-0.4),
    )
    assert outside["status"] == "fail" and "95 %" in outside["note"]
    no_oos = _check("G2", "oos_drawdown_within_mc", True, monte_carlo=mc)
    assert no_oos["status"] == "missing" and "run_out_of_sample" in no_oos["note"]
