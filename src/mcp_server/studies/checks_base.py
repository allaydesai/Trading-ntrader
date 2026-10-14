"""What a gate check judges: the evidence records and the row every check returns.

Shared by the phase-2/3 checks (``checks.py``) and the phase-4 robustness checks
(``checks_robustness.py``). Pure.
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


@dataclass(frozen=True)
class PaperFacts:
    """What G4 needs of the newest paper session linked to the candidate."""

    session: str
    weeks: float
    trades: int
    #: Both the weeks and the trades the gate asks for have been met.
    reached: bool
    #: Win rate, average trade and drawdown against the band: below, inside, above or n/a.
    positions: dict[str, str]
    #: Signal, execution and config flags (the strategy-or-regime flag is not one).
    problems: list[str]
    #: Other sessions linked to the same candidate, never judged instead of this one.
    others: list[str] = field(default_factory=list)
    #: Why the band cannot judge the results (too thin, too few trades); empty when it can.
    unjudgeable: str = ""
    #: The session is in a run that began after a stop.
    restarted: bool = False


@dataclass(frozen=True)
class NeighbourhoodFacts:
    """The candidate's cell in a sweep against its grid neighbours (G2 neighbourhood)."""

    sweep: str
    cell_run_id: str | None
    chosen: float | None
    #: Neighbour run id -> its Sharpe (None when the run lacks one).
    neighbours: dict[str, float | None]
    min_ratio: float | None
    median_ratio: float | None


@dataclass(frozen=True)
class StressFacts:
    """The candidate's cost-stress run and the multiplier it ran at."""

    analysis: str
    run: RunFacts
    multiplier: float


@dataclass(frozen=True)
class BreadthFacts:
    """The candidate's parameters on untuned symbols: symbol -> run."""

    analyses: list[str]
    runs: dict[str, RunFacts]


@dataclass(frozen=True)
class WalkForwardFacts:
    """A completed walk-forward of the candidate's grid."""

    analysis: str
    efficiency: float | None
    windows: int
    windows_used: int
    negative_windows: int
    excluded_windows: list[int]
    run_ids: list[str]


@dataclass(frozen=True)
class MonteCarloFacts:
    """The Monte Carlo of the candidate's source run (its in-sample expectation)."""

    analysis: str
    run_id: str
    method: str
    #: The worst 5 % drawdown of the resampled paths (negative).
    drawdown_p5: float | None
    breach_probability: float | None
    breach: float | None


@dataclass(frozen=True)
class DeflatedSharpeFacts:
    """The candidate's Sharpe deflated by the study's trials."""

    run_id: str
    probability: float
    trials: int
    sharpe_annualised: float | None
    expected_max_sharpe: float
    observations: int


@dataclass
class Evidence:
    """A candidate's evidence, gathered from the study's ledger and analyses only."""

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
    # Phase 4 robustness evidence (each None until its analysis or run exists).
    neighbourhood: NeighbourhoodFacts | None = None
    cost_stress: StressFacts | None = None
    breadth: BreadthFacts | None = None
    walk_forward: WalkForwardFacts | None = None
    monte_carlo: MonteCarloFacts | None = None
    deflated_sharpe: DeflatedSharpeFacts | None = None
    notes: list[str] = field(default_factory=list)


def as_number(value: Any) -> float | None:
    """A threshold as a number, or None for a blank, a flag or text."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
