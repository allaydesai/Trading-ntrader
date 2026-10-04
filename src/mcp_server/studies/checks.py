"""Gate checks: the named checks a vault gate may list, judged on a candidate's evidence (S6.1).

Pure. Each check takes the ``Evidence`` and the threshold the vault gave it and
returns a row with ``pass``, ``fail`` or ``missing``, the number behind it, the
threshold and the runs it was judged on. A check this server cannot compute yet
(phase 4) is always ``missing``, never ``pass``. G4 is judged on the paper
session linked to the candidate's out-of-sample run: ``missing`` until it has
run long enough, then ``pass`` or ``fail``.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.mcp_server.metrics import INSOLVENT_NOTE, insolvent

PASS, FAIL, MISSING = "pass", "fail", "missing"


@dataclass(frozen=True)
class RunFacts:
    """What a check needs of one run."""

    run_id: str
    metrics: dict[str, float | None]
    git_dirty: bool | None


@dataclass(frozen=True)
class PaperFacts:
    """What G4 needs of the newest paper session linked to the candidate."""

    session: str
    weeks: float
    trades: int
    #: Both the weeks and the trades the gate asks for have been met.
    reached: bool
    #: Win rate and average trade against the band: below, inside, above or n/a.
    positions: dict[str, str]
    #: Signal, execution and config flags (the strategy-or-regime flag is not one).
    problems: list[str]
    #: Older sessions linked to the same candidate, never judged instead of the newest.
    others: list[str] = field(default_factory=list)


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
    benchmark_rationale: str | None = None
    paper: PaperFacts | None = None
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
    runs = [ev.in_sample, ev.in_sample_benchmark]
    if not beaten:
        return _row(FAIL, compared, names, runs)
    rationale = (ev.benchmark_rationale or "").strip()
    if not rationale:
        note = (
            f"beats on: {', '.join(beaten)}, but the frozen candidate records no "
            "benchmark_rationale saying why that is worth it (freeze_candidate)"
        )
        return _row(MISSING, compared, names, runs, note)
    value = {**compared, "rationale": rationale}
    return _row(PASS, value, names, runs, f"beats on: {', '.join(beaten)}")


def equity_stays_positive(ev: Evidence, threshold: Any) -> Row:
    runs = [r for r in (ev.in_sample, ev.out_of_sample) if r is not None]
    if not runs:
        return _missing(threshold, "No in-sample candidate run.")
    drawdowns = {r.run_id: r.metrics.get("max_drawdown") for r in runs}
    if any(v is None for v in drawdowns.values()):
        return _row(MISSING, drawdowns, threshold, runs, "max_drawdown is not available.")
    value = {
        run_id: {"stayed_positive": not insolvent(dd), "max_drawdown": dd}
        for run_id, dd in drawdowns.items()
    }
    broke = any(insolvent(v) for v in drawdowns.values())
    return _row(FAIL if broke else PASS, value, threshold, runs, INSOLVENT_NOTE if broke else "")


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
    # Either a bare ratio, or {ratio, min_is_sharpe}: a ratio over a near-zero in-sample
    # Sharpe is noise, so below the floor it fails rather than passes by a mile.
    limits = threshold if isinstance(threshold, dict) else {"ratio": threshold}
    if limits.get("ratio") is None:
        return _row(MISSING, None, threshold, runs, "The gates block gives no ratio.")
    floor = float(limits.get("min_is_sharpe") or 0)
    if ins <= 0 or ins < floor:
        note = (
            "in-sample Sharpe is not positive"
            if ins <= 0
            else f"in-sample Sharpe {ins:g} is too small for a ratio (min_is_sharpe {floor:g})"
        )
        return _row(FAIL, {"oos": oos, "is": ins}, threshold, runs, note)
    ratio = oos / ins
    passed = ratio >= float(limits["ratio"])
    return _row(PASS if passed else FAIL, round(ratio, 4), threshold, runs)


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


NO_SESSION = (
    "No paper session is linked to the candidate's out-of-sample run: paper_commands gives "
    "the commands to start one."
)
NOT_YET = "Judged once min_weeks and min_trades are both met."


def _paper_row(status: str, value: Any, threshold: Any, paper: PaperFacts, note: str = "") -> Row:
    row = _row(status, value, threshold, [], note)
    row["evidence"] = [f"session:{paper.session}"]
    return row


def _progress(value: float, threshold: Any, paper: PaperFacts, unit: str) -> Row:
    if value >= float(threshold):
        return _paper_row(PASS, value, threshold, paper)
    note = f"in progress: {value:g} of {threshold} {unit}"
    return _paper_row(MISSING, value, threshold, paper, note)


def paper_min_weeks(ev: Evidence, threshold: Any) -> Row:
    if ev.paper is None:
        return _missing(threshold, NO_SESSION)
    return _progress(round(ev.paper.weeks, 1), threshold, ev.paper, "weeks")


def paper_min_trades(ev: Evidence, threshold: Any) -> Row:
    if ev.paper is None:
        return _missing(threshold, NO_SESSION)
    return _progress(ev.paper.trades, threshold, ev.paper, "trades")


def paper_inside_band(ev: Evidence, threshold: Any) -> Row:
    paper = ev.paper
    if paper is None:
        return _missing(threshold, NO_SESSION)
    if not paper.reached:
        return _paper_row(MISSING, paper.positions, threshold, paper, NOT_YET)
    if "n/a" in paper.positions.values():
        note = "The band could not judge every metric (see get_session)."
        return _paper_row(MISSING, paper.positions, threshold, paper, note)
    outside = [f"{k} {v}" for k, v in paper.positions.items() if v != "inside"]
    status = FAIL if outside else PASS
    return _paper_row(status, paper.positions, threshold, paper, ", ".join(outside))


def paper_clean(ev: Evidence, threshold: Any) -> Row:
    paper = ev.paper
    if paper is None:
        return _missing(threshold, NO_SESSION)
    others = (
        f"Other linked sessions, not judged: {', '.join(paper.others)}." if paper.others else ""
    )
    if not paper.reached:
        return _paper_row(MISSING, paper.problems, threshold, paper, f"{NOT_YET} {others}".strip())
    note = (
        "Unexplained behaviour; explanations are recorded in the vault, the server cannot "
        "judge them."
        if paper.problems
        else ""
    )
    status = FAIL if paper.problems else PASS
    return _paper_row(status, paper.problems, threshold, paper, f"{note} {others}".strip())


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
    "equity_stays_positive": equity_stays_positive,
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
#: Checks that mean something else inside one gate: G4's min_trades counts paper trades.
GATE_CHECKS: dict[str, dict[str, Check]] = {
    "G4": {
        "min_weeks": paper_min_weeks,
        "min_trades": paper_min_trades,
        "paper_inside_band": paper_inside_band,
        "paper_clean": paper_clean,
    },
}
#: Checks a gate gets whatever the vault lists: no run whose equity went to zero is
#: evidence, the holdout is run once, and paper results sit in the band, unexplained-free.
IMPLIED: dict[str, tuple[tuple[str, Any], ...]] = {
    "G0": (("equity_stays_positive", True),),
    "G2": (("oos_run_once", 1),),
    "G4": (("paper_inside_band", True), ("paper_clean", True)),
}


def judge(gate_id: str, thresholds: dict[str, Any] | None, ev: Evidence) -> dict[str, Any]:
    """Every check of one gate and the gate's roll-up: fail > missing > pass."""
    if thresholds is None:
        rows = {"thresholds": _missing(None, f"{gate_id} has no thresholds in the gates block.")}
    else:
        listed = dict(thresholds)
        for name, value in IMPLIED.get(gate_id, ()):
            listed.setdefault(name, value)
        own = GATE_CHECKS.get(gate_id, {})
        rows = {}
        for name, threshold in listed.items():
            check = own.get(name) or CHECKS.get(name)
            rows[name] = (
                check(ev, threshold)
                if check
                else _missing(threshold, f"The server has no check named '{name}'.")
            )
    statuses = {row["status"] for row in rows.values()}
    status = FAIL if FAIL in statuses else MISSING if MISSING in statuses else PASS
    return {"status": status, "checks": rows}
