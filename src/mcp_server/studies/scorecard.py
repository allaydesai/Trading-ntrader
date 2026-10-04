"""``get_scorecard``: a candidate's evidence against every gate it must pass (S6.1).

Evidence comes only from the study's ledger: the candidate's source in-sample
run, its out-of-sample run, and buy-and-hold benchmarks on the same symbol and
window. Unattributed runs never count. Thresholds come only from the vault's
gates block (``gates.py``); whatever cannot be judged yet is ``missing``.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from src.db.models.backtest import BacktestRun
from src.db.models.research import ResearchCandidate, ResearchStudy, ResearchTrial
from src.db.repositories.equity_curve_repository import SyncEquityCurveRepository
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.regimes import daily_returns, sub_periods
from src.mcp_server.analysis.series import equity_series
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.metrics import METRIC_SPECS
from src.mcp_server.settings import McpSettings
from src.mcp_server.studies import ledger
from src.mcp_server.studies.candidates import current_candidate
from src.mcp_server.studies.checks import FAIL, MISSING, PASS, Evidence, RunFacts, judge
from src.mcp_server.studies.gates import load_gates
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.views import candidate_view

BENCHMARK = "buy_and_hold"


def _facts(session: Session, run_id: UUID | None) -> RunFacts | None:
    if run_id is None:
        return None
    stmt = (
        select(BacktestRun).options(noload(BacktestRun.trades)).where(BacktestRun.run_id == run_id)
    )
    run = session.scalars(stmt).first()
    if run is None or run.metrics is None:
        return None
    metrics = {
        name: None if (v := getattr(run.metrics, name)) is None else float(v)
        for name, spec in METRIC_SPECS.items()
        if spec.unit != "datetime"
    }
    return RunFacts(run_id=str(run.run_id), metrics=metrics, git_dirty=run.git_dirty)


def _benchmark_beside(trials: list[ResearchTrial], run: ResearchTrial | None) -> UUID | None:
    """The newest completed buy-and-hold benchmark on the run's symbol and window."""
    if run is None:
        return None
    for trial in reversed(trials):
        if (
            trial.role == "benchmark"
            and trial.state == "completed"
            and trial.strategy_type == BENCHMARK
            and (trial.symbol, trial.start, trial.end) == (run.symbol, run.start, run.end)
        ):
            return trial.run_id
    return None


def _sub_period_returns(session: Session, run_id: UUID, count: int) -> list[float] | None:
    points = SyncEquityCurveRepository(session).find_points(run_id)
    if not points:
        return None
    cells = sub_periods(daily_returns(equity_series(points)), [], count)
    return [c["return"] for c in cells]


def gather(
    session: Session,
    trials: list[ResearchTrial],
    candidate: ResearchCandidate,
    *,
    periods: int = 4,
) -> Evidence:
    """Everything the checks judge, from the ledger's completed runs."""
    source = next((t for t in trials if t.run_id == candidate.source_run_id), None)
    attempts = [
        t
        for t in trials
        if t.role == "out_of_sample" and t.candidate_pk == candidate.id and t.state != "void"
    ]
    done = [t for t in attempts if t.state == "completed"]
    oos = done[-1] if done else None
    return Evidence(
        in_sample=_facts(session, candidate.source_run_id),
        in_sample_benchmark=_facts(session, _benchmark_beside(trials, source)),
        out_of_sample=_facts(session, oos.run_id if oos else None),
        out_of_sample_benchmark=_facts(session, _benchmark_beside(trials, oos)),
        out_of_sample_attempts=len(attempts),
        out_of_sample_overridden=any(t.override_reason for t in attempts),
        sub_period_returns=_sub_period_returns(session, candidate.source_run_id, periods),
        trials_used=ledger.used(trials),
        candidate_dirty=candidate.git_dirty,
        benchmark_rationale=candidate.benchmark_rationale,
    )


def _candidate_of(repo: SyncResearchRepository, study: ResearchStudy, version: int | None):
    if version is None:
        return current_candidate(repo, study)
    for candidate in repo.candidates(study):
        if candidate.version == version:
            return candidate
    raise ToolFailure(
        "no_candidate", f"No frozen candidate for version {version}.", fix="get_study lists them."
    )


def _evidence_view(evidence: Evidence) -> dict[str, Any]:
    """The run ids each part of the evidence came from."""

    def run_id(facts: RunFacts | None) -> str | None:
        return facts.run_id if facts else None

    return {
        "in_sample": run_id(evidence.in_sample),
        "in_sample_benchmark": run_id(evidence.in_sample_benchmark),
        "out_of_sample": run_id(evidence.out_of_sample),
        "out_of_sample_benchmark": run_id(evidence.out_of_sample_benchmark),
        "trials_used": evidence.trials_used,
    }


def get_scorecard(
    settings: McpSettings, store: JobStore, key: str, version: int | None
) -> dict[str, Any]:
    """Every gate in the study's pass criteria: pass, fail or missing, with its evidence."""
    gates = load_gates(settings)
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, key, for_update=True)
        ledger.settle(repo, store, study)
        candidate = _candidate_of(repo, study, version)
        periods = int(
            (gates.thresholds.get("G1", {}).get("positive_sub_periods") or {}).get("of", 4)
        )
        evidence = gather(session, repo.trials(study), candidate, periods=periods)
        judged = {
            gate: judge(gate, gates.thresholds.get(gate) if gates.thresholds else None, evidence)
            for gate in study.pass_criteria
        }
        warnings = [] if gates.problem is None else [f"Gates: {gates.problem}"]
        if study.contaminated:
            warnings.insert(0, f"CONTAMINATED: {study.contamination_reason}")
        view = {
            "study": study.slug,
            "status": study.status,
            "version": candidate.version,
            "candidate": candidate_view(candidate),
            "contaminated": study.contaminated,
        }
    summary = {s: [g for g, r in judged.items() if r["status"] == s] for s in (PASS, FAIL, MISSING)}
    return to_jsonable(
        {
            **view,
            "gates": judged,
            "summary": summary,
            "evidence": _evidence_view(evidence),
            "expectation_band": {"status": MISSING, "note": "Arrives in phase 3 (paper loop)."},
            "gates_source": gates.source,
            "warnings": warnings,
        }
    )
