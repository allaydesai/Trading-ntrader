"""Gate checks: the named checks a vault gate may list, judged on a candidate's evidence (S6.1).

Pure. Each check takes the ``Evidence`` and the threshold the vault gave it and
returns a row with ``pass``, ``fail`` or ``missing``, the number behind it, the
threshold and the runs it was judged on. A check this server cannot compute yet
(phase 3 or 4) is always ``missing``, never ``pass``.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

PASS, FAIL, MISSING = "pass", "fail", "missing"


@dataclass(frozen=True)
class RunFacts:
    """What a check needs of one run."""

    run_id: str
    metrics: dict[str, float | None]
    git_dirty: bool | None


@dataclass
class Evidence:
    """A candidate's evidence, gathered from the study's ledger only."""

    in_sample: RunFacts | None = None
    in_sample_benchmark: RunFacts | None = None
    out_of_sample: RunFacts | None = None
    out_of_sample_benchmark: RunFacts | None = None
    out_of_sample_attempts: int = 0
    out_of_sample_overridden: bool = False
    sub_period_returns: list[float] | None = None
    trials_used: int = 0
    candidate_dirty: bool | None = None
    notes: list[str] = field(default_factory=list)


Row = dict[str, Any]
Check = Callable[[Evidence, Any], Row]


def _row(
    status: str, value: Any, threshold: Any, runs: Sequence[RunFacts | None], note: str = ""
) -> Row:
    row: Row = {
        "status": status,
        "value": value,
        "threshold": threshold,
        "evidence": [r.run_id for r in runs if r is not None],
    }
    if note:
        row["note"] = note
    return row


def _missing(threshold: Any, note: str) -> Row:
    return _row(MISSING, None, threshold, [], note)


def _metric(run: RunFacts | None, name: str) -> float | None:
    return None if run is None else run.metrics.get(name)


def min_trades(ev: Evidence, threshold: Any) -> Row:
    runs = [r for r in (ev.in_sample, ev.out_of_sample) if r is not None]
    if not runs:
        return _missing(threshold, "No in-sample candidate run.")
    counts = {r.run_id: int(r.metrics.get("total_trades") or 0) for r in runs}
    short = [n for n in counts.values() if n < threshold]
    note = "insufficient sample (inconclusive)" if short else ""
    return _row(FAIL if short else PASS, counts, threshold, runs, note)


def clean_tree(ev: Evidence, threshold: Any) -> Row:
    runs = [r for r in (ev.in_sample, ev.out_of_sample) if r is not None]
    dirty = [r.run_id for r in runs if r.git_dirty] + (["candidate"] if ev.candidate_dirty else [])
    if not threshold:
        return _row(PASS, {"dirty": dirty}, threshold, runs, "not required")
    if not runs:
        return _missing(threshold, "No in-sample candidate run.")
    note = "made from uncommitted code: note the diff" if dirty else ""
    return _row(FAIL if dirty else PASS, {"dirty": dirty}, threshold, runs, note)


def benchmark_present(ev: Evidence, threshold: Any) -> Row:
    needed = {"in_sample": ev.in_sample_benchmark}
    if ev.out_of_sample is not None:
        needed["out_of_sample"] = ev.out_of_sample_benchmark
    absent = [k for k, v in needed.items() if v is None]
    note = (
        f"No buy_and_hold benchmark on the same symbol and window for: {', '.join(absent)} "
        "(submit_benchmark)."
        if absent
        else ""
    )
    return _row(
        FAIL if absent else PASS,
        {k: v is not None for k, v in needed.items()},
        threshold,
        list(needed.values()),
        note,
    )


def _at_least(run: RunFacts | None, name: str, threshold: float, *, strict: bool) -> Row:
    value = _metric(run, name)
    if run is None:
        return _missing(threshold, "No run to judge.")
    if value is None:
        return _row(MISSING, None, threshold, [run], f"{name} is not available for this run.")
    ok = value > threshold if strict else value >= threshold
    return _row(PASS if ok else FAIL, value, threshold, [run])


def is_profit_factor(ev: Evidence, threshold: Any) -> Row:
    return _at_least(ev.in_sample, "profit_factor", float(threshold), strict=False)


def is_expectancy_gt(ev: Evidence, threshold: Any) -> Row:
    return _at_least(ev.in_sample, "expectancy", float(threshold), strict=True)


def oos_profit_factor(ev: Evidence, threshold: Any) -> Row:
    if ev.out_of_sample is None:
        return _missing(threshold, "No out-of-sample run yet (run_out_of_sample).")
    return _at_least(ev.out_of_sample, "profit_factor", float(threshold), strict=True)


def beats_benchmark_on_one(ev: Evidence, threshold: Any) -> Row:
    names = list(threshold or [])
    if ev.in_sample is None or ev.in_sample_benchmark is None:
        return _missing(names, "Needs the in-sample run and its buy_and_hold benchmark.")
    beaten, compared = [], {}
    for name in names:  # every metric listed is higher-is-better (drawdown is stored negative)
        mine, theirs = _metric(ev.in_sample, name), _metric(ev.in_sample_benchmark, name)
        compared[name] = {"run": mine, "benchmark": theirs}
        if mine is not None and theirs is not None and mine > theirs:
            beaten.append(name)
    status = PASS if beaten else FAIL
    note = f"beats on: {', '.join(beaten)}; say why that is worth it" if beaten else ""
    return _row(status, compared, names, [ev.in_sample, ev.in_sample_benchmark], note)


def positive_sub_periods(ev: Evidence, threshold: Any) -> Row:
    need = int((threshold or {}).get("count", 3))
    if ev.sub_period_returns is None:
        return _missing(threshold, "No sub-period split of the in-sample run (needs its equity).")
    positive = sum(1 for r in ev.sub_period_returns if r > 0)
    value = {"positive": positive, "of": len(ev.sub_period_returns)}
    return _row(PASS if positive >= need else FAIL, value, threshold, [ev.in_sample])


def oos_sharpe_vs_is(ev: Evidence, threshold: Any) -> Row:
    oos, ins = _metric(ev.out_of_sample, "sharpe_ratio"), _metric(ev.in_sample, "sharpe_ratio")
    runs = [ev.in_sample, ev.out_of_sample]
    if ev.out_of_sample is None:
        return _missing(threshold, "No out-of-sample run yet (run_out_of_sample).")
    if oos is None or ins is None:
        return _row(MISSING, None, threshold, runs, "Sharpe is not available for both runs.")
    if ins <= 0:
        return _row(
            FAIL, {"oos": oos, "is": ins}, threshold, runs, "in-sample Sharpe is not positive"
        )
    ratio = oos / ins
    return _row(PASS if ratio >= float(threshold) else FAIL, round(ratio, 4), threshold, runs)


def oos_run_once(ev: Evidence, threshold: Any) -> Row:
    if ev.out_of_sample_attempts == 0:
        return _missing(threshold, "No out-of-sample run yet (run_out_of_sample).")
    once = ev.out_of_sample_attempts == 1 and not ev.out_of_sample_overridden
    note = "" if once else "the holdout was run more than once (override recorded)"
    return _row(PASS if once else FAIL, ev.out_of_sample_attempts, 1, [ev.out_of_sample], note)


def trials_warn_above(ev: Evidence, threshold: Any) -> Row:
    over = ev.trials_used > int(threshold)
    note = "many trials: demand a stronger out-of-sample result" if over else ""
    return _row(PASS, ev.trials_used, threshold, [], note)


def later_phase(phase: int, what: str) -> Check:
    def check(_ev: Evidence, threshold: Any) -> Row:
        return _missing(threshold, f"{what} arrives in phase {phase}.")

    return check


CHECKS: dict[str, Check] = {
    "min_trades": min_trades,
    "clean_tree": clean_tree,
    "benchmark_present": benchmark_present,
    "is_profit_factor": is_profit_factor,
    "is_expectancy_gt": is_expectancy_gt,
    "beats_benchmark_on_one": beats_benchmark_on_one,
    "positive_sub_periods": positive_sub_periods,
    "oos_sharpe_vs_is": oos_sharpe_vs_is,
    "oos_profit_factor": oos_profit_factor,
    "oos_run_once": oos_run_once,
    "trials_warn_above": trials_warn_above,
    "neighbourhood_sharpe": later_phase(4, "Parameter sensitivity"),
    "cost_stress_multiplier": later_phase(4, "Cost stress"),
    "breadth": later_phase(4, "Breadth on untuned instruments"),
    "walk_forward_efficiency": later_phase(4, "Walk-forward"),
}
#: Checks every gate gets whatever the vault lists: the holdout is run once.
IMPLIED: dict[str, tuple[str, Any]] = {"G2": ("oos_run_once", 1)}
#: Gates the server cannot judge at all yet.
LATER_GATES: dict[str, int] = {"G4": 3}


def judge(gate_id: str, thresholds: dict[str, Any] | None, ev: Evidence) -> dict[str, Any]:
    """Every check of one gate and the gate's roll-up: fail > missing > pass."""
    if gate_id in LATER_GATES:
        rows = {"paper": _missing(None, f"Paper results arrive in phase {LATER_GATES[gate_id]}.")}
    elif thresholds is None:
        rows = {"thresholds": _missing(None, f"{gate_id} has no thresholds in the gates block.")}
    else:
        listed = dict(thresholds)
        if gate_id in IMPLIED:
            name, value = IMPLIED[gate_id]
            listed.setdefault(name, value)
        rows = {}
        for name, threshold in listed.items():
            check = CHECKS.get(name)
            rows[name] = (
                check(ev, threshold)
                if check
                else _missing(threshold, f"The server has no check named '{name}'.")
            )
    statuses = {row["status"] for row in rows.values()}
    status = FAIL if FAIL in statuses else MISSING if MISSING in statuses else PASS
    return {"status": status, "checks": rows}
