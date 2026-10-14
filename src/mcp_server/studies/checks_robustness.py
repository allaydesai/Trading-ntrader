"""The phase-4 gate checks: sensitivity, cost stress, breadth, walk-forward, deflated Sharpe,
Monte Carlo drawdown (S3.2, S4.1, S4.4, S5.3, S5.4).

Pure, like ``checks.py``. Each is ``missing`` with the tool to call until its
analysis or run exists; a run that exists but misses the gate's bar fails.
"""

from typing import Any

from src.mcp_server.studies.checks_base import (
    FAIL,
    MISSING,
    PASS,
    Evidence,
    Row,
    RunFacts,
    _metric,
    _missing,
    _row,
    as_number,
)


def _analysis_row(status: str, value: Any, threshold: Any, ids: list[str], note: str = "") -> Row:
    row = _row(status, value, threshold, [], note)
    row["evidence"] = ids
    return row


def neighbourhood_sharpe(ev: Evidence, threshold: Any) -> Row:
    """Every grid neighbour of the candidate's cell keeps at least ``threshold`` of its Sharpe."""
    facts = ev.neighbourhood
    if facts is None:
        return _missing(
            threshold,
            "No sweep contains the candidate's cell: submit_sweep(around=<its params>) "
            "and rerun the scorecard once it has finished.",
        )
    ids = [f"analysis:{facts.sweep}", *(r for r in facts.neighbours if r)]
    value = {
        "chosen_sharpe": facts.chosen,
        "neighbours": len(facts.neighbours),
        "min_ratio": facts.min_ratio,
        "median_ratio": facts.median_ratio,
    }
    wanted = as_number(threshold)
    if wanted is None:
        return _analysis_row(MISSING, value, threshold, ids, "The threshold is not a number.")
    if facts.chosen is None or facts.chosen <= 0:
        return _analysis_row(FAIL, value, threshold, ids, "the candidate's Sharpe is not positive")
    if not facts.neighbours or facts.min_ratio is None:
        return _analysis_row(MISSING, value, threshold, ids, "No finished neighbour run yet.")
    note = "" if facts.min_ratio >= wanted else "a neighbour falls below the plateau bar"
    return _analysis_row(PASS if facts.min_ratio >= wanted else FAIL, value, threshold, ids, note)


def cost_stress_multiplier(ev: Evidence, threshold: Any) -> Row:
    """The frozen candidate is still profitable with costs scaled by ``threshold``."""
    facts = ev.cost_stress
    wanted = as_number(threshold) or 2.0
    if facts is None:
        return _missing(
            threshold, f"No cost-stress run: submit_cost_stress(multiplier={wanted:g})."
        )
    ids = [f"analysis:{facts.analysis}", facts.run.run_id]
    total = _metric(facts.run, "total_return")
    value = {
        "multiplier": facts.multiplier,
        "total_return": total,
        "profit_factor": _metric(facts.run, "profit_factor"),
    }
    if facts.multiplier < wanted:
        note = f"stressed at {facts.multiplier:g}x; submit_cost_stress(multiplier={wanted:g})"
        return _analysis_row(MISSING, value, threshold, ids, note)
    if total is None:
        return _analysis_row(MISSING, value, threshold, ids, "total_return is not available.")
    note = "" if total > 0 else f"loses money at {facts.multiplier:g}x costs"
    return _analysis_row(PASS if total > 0 else FAIL, value, threshold, ids, note)


def breadth(ev: Evidence, threshold: Any) -> Row:
    """At least ``instruments`` untuned symbols keep a profit factor above ``profit_factor``."""
    limits = threshold if isinstance(threshold, dict) else {"instruments": threshold}
    needed = as_number(limits.get("instruments"))
    bar = as_number(limits.get("profit_factor")) or 1.1
    facts = ev.breadth
    if facts is None or not facts.runs:
        return _missing(
            threshold, "No breadth run: submit_breadth(symbols=[...]) on untuned symbols."
        )
    ids = [f"analysis:{a}" for a in facts.analyses] + [r.run_id for r in facts.runs.values()]
    table = {symbol: _metric(run, "profit_factor") for symbol, run in facts.runs.items()}
    if needed is None:
        return _analysis_row(MISSING, table, threshold, ids, "The gate names no instruments count.")
    passing = sorted(s for s, pf in table.items() if pf is not None and pf > bar)
    value = {"profit_factor": table, "passing": passing, "needed": int(needed), "bar": bar}
    if len(passing) >= needed:
        return _analysis_row(PASS, value, threshold, ids)
    failing = sorted(s for s in table if s not in passing)
    note = f"only {len(passing)} of {int(needed)} needed; below the bar: {', '.join(failing)}"
    status = FAIL if len(table) >= needed else MISSING
    if status == MISSING:
        note = f"{len(table)} symbol(s) tested, {int(needed)} needed: submit_breadth more"
    return _analysis_row(status, value, threshold, ids, note)


def walk_forward_efficiency(ev: Evidence, threshold: Any) -> Row:
    """Out-of-sample over in-sample annualised return, averaged over the windows."""
    facts = ev.walk_forward
    if facts is None:
        return _missing(threshold, "No walk-forward: submit_walk_forward on the frozen candidate.")
    ids = [f"analysis:{facts.analysis}", *facts.run_ids]
    value = {
        "efficiency": facts.efficiency,
        "windows": facts.windows,
        "windows_used": facts.windows_used,
        "negative_windows": facts.negative_windows,
        "excluded_windows": facts.excluded_windows,
    }
    wanted = as_number(threshold)
    if wanted is None:
        return _analysis_row(MISSING, value, threshold, ids, "The threshold is not a number.")
    if facts.efficiency is None:
        note = "no window had a positive in-sample return, so efficiency is undefined"
        return _analysis_row(FAIL, value, threshold, ids, note)
    notes = []
    if facts.negative_windows:
        notes.append(f"{facts.negative_windows} window(s) lost money out-of-sample")
    if facts.excluded_windows:
        notes.append(f"windows {facts.excluded_windows} excluded (in-sample return not positive)")
    status = PASS if facts.efficiency >= wanted else FAIL
    return _analysis_row(status, value, threshold, ids, "; ".join(notes))


def deflated_sharpe_prob(ev: Evidence, threshold: Any) -> Row:
    """P(true Sharpe > the best of N trials by luck) is at least ``threshold``."""
    facts = ev.deflated_sharpe
    if facts is None:
        return _missing(
            threshold,
            "Needs the candidate's source run equity and at least two finished counted trials.",
        )
    value = {
        "probability": facts.probability,
        "trials": facts.trials,
        "sharpe_ratio": facts.sharpe_annualised,
        "expected_max_sharpe_daily": facts.expected_max_sharpe,
        "observations": facts.observations,
    }
    wanted = as_number(threshold)
    runs: list[RunFacts | None] = [ev.in_sample]
    if wanted is None:
        return _row(MISSING, value, threshold, runs, "The threshold is not a number.")
    status = PASS if facts.probability >= wanted else FAIL
    note = (
        ""
        if status == PASS
        else f"after {facts.trials} trials the Sharpe is not distinguishable from luck"
    )
    return _row(status, value, threshold, runs, note)


def oos_drawdown_within_mc(ev: Evidence, threshold: Any) -> Row:
    """The out-of-sample drawdown is no worse than the in-sample Monte Carlo's worst 5 %."""
    facts = ev.monte_carlo
    if facts is None:
        return _missing(threshold, "No Monte Carlo of the candidate's source run: run_monte_carlo.")
    if ev.out_of_sample is None:
        return _missing(threshold, "No out-of-sample run yet (run_out_of_sample).")
    ids = [f"analysis:{facts.analysis}", facts.run_id, ev.out_of_sample.run_id]
    oos = _metric(ev.out_of_sample, "max_drawdown")
    value = {
        "oos_max_drawdown": oos,
        "mc_drawdown_p5": facts.drawdown_p5,
        "method": facts.method,
        "drawdowns_are_negative": True,
    }
    if oos is None or facts.drawdown_p5 is None:
        return _analysis_row(MISSING, value, threshold, ids, "A drawdown is not available.")
    inside = oos >= facts.drawdown_p5
    note = "" if inside else "the holdout fell further than 95 % of the in-sample resamples"
    return _analysis_row(PASS if inside else FAIL, value, threshold, ids, note)


ROBUSTNESS_CHECKS = {
    "neighbourhood_sharpe": neighbourhood_sharpe,
    "cost_stress_multiplier": cost_stress_multiplier,
    "breadth": breadth,
    "walk_forward_efficiency": walk_forward_efficiency,
    "deflated_sharpe_prob": deflated_sharpe_prob,
    "oos_drawdown_within_mc": oos_drawdown_within_mc,
}
