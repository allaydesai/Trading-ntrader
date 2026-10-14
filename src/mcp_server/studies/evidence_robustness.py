"""Gather a candidate's phase-4 evidence from the study's analyses and robustness runs.

Each piece is None until the analysis or run behind it exists, so every
robustness check stays ``missing`` with the tool to call. Only analyses of
the candidate (by candidate, or by its exact parameters) count.
"""

import math
from typing import Any

import numpy as np
from scipy import stats
from sqlalchemy.orm import Session

from src.core.robustness import sensitivity
from src.core.robustness.bootstrap import TRADING_DAYS
from src.core.robustness.deflated_sharpe import deflated_sharpe
from src.db.models.research import ResearchAnalysis, ResearchCandidate, ResearchStudy, ResearchTrial
from src.db.repositories.equity_curve_repository import SyncEquityCurveRepository
from src.db.repositories.research_repository import SyncResearchRepository
from src.mcp_server.analysis.regimes import daily_returns
from src.mcp_server.analysis.series import equity_series
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.studies import ledger
from src.mcp_server.studies.checks_base import (
    BreadthFacts,
    DeflatedSharpeFacts,
    MonteCarloFacts,
    NeighbourhoodFacts,
    RunFacts,
    StressFacts,
    WalkForwardFacts,
)

Facts = Any  # a callable (session, run_id) -> RunFacts | None, scorecard._facts


def _same_params(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return to_jsonable(a) == to_jsonable(b)


def _cell_matches(cell: dict[str, Any], params: dict[str, Any]) -> bool:
    if any(k not in params for k in cell):
        return False
    try:
        return all(float(cell[k]) == float(params[k]) for k in cell)
    except (TypeError, ValueError):
        return all(cell[k] == params[k] for k in cell)


def neighbourhood_facts(
    session: Session, repo: SyncResearchRepository, study: ResearchStudy, candidate, facts: Facts
) -> NeighbourhoodFacts | None:
    """The candidate's cell in the newest sweep that contains it, against its neighbours."""
    for sweep in reversed(repo.analyses(study, kind="sweep")):
        cells_by_job = sweep.request.get("cells") or {}
        axes = list(sweep.request.get("axes") or {})
        if not axes or not cells_by_job:
            continue
        jobs = list(cells_by_job)
        cells = [cells_by_job[j] for j in jobs]
        centre = next((i for i, c in enumerate(cells) if _cell_matches(c, candidate.params)), None)
        if centre is None:
            continue
        trials = {t.job_id: t for t in repo.trials_of_analysis(sweep)}
        values: dict[int, float | None] = {}
        runs: dict[int, str] = {}
        for i, job_id in enumerate(jobs):
            trial = trials.get(job_id)
            run = facts(session, trial.run_id) if trial and trial.state == "completed" else None
            values[i] = None if run is None else run.metrics.get("sharpe_ratio")
            if run is not None:
                runs[i] = run.run_id
        found = sensitivity.neighbourhood(cells, values, axes, centre)
        return NeighbourhoodFacts(
            sweep=str(sweep.analysis_id),
            cell_run_id=runs.get(centre),
            chosen=found["chosen"],
            neighbours={runs[j]: v for j, v in found["neighbours"].items() if j in runs},
            min_ratio=found["min_ratio"],
            median_ratio=found["median_ratio"],
        )
    return None


def _completed(analyses: list[ResearchAnalysis]) -> list[ResearchAnalysis]:
    return [a for a in analyses if a.status == "completed" and a.result]


def stress_facts(
    session: Session, repo: SyncResearchRepository, study: ResearchStudy, candidate, facts: Facts
) -> StressFacts | None:
    for analysis in reversed(_completed(repo.analyses(study, kind="cost_stress"))):
        if analysis.candidate_pk != candidate.id:
            continue
        for run_id in (analysis.result or {}).get("runs", {}).values():
            run = facts(session, run_id)
            if run is not None:
                return StressFacts(
                    analysis=str(analysis.analysis_id),
                    run=run,
                    multiplier=float(analysis.request.get("multiplier") or 1),
                )
    return None


def breadth_facts(
    session: Session, repo: SyncResearchRepository, study: ResearchStudy, candidate, facts: Facts
) -> BreadthFacts | None:
    """Newest finished breadth run per untuned symbol, over batches of the candidate's params."""
    runs: dict[str, RunFacts] = {}
    used: list[str] = []
    for analysis in reversed(_completed(repo.analyses(study, kind="breadth"))):
        if not _same_params(analysis.request.get("params") or {}, candidate.params):
            continue
        members: list[ResearchTrial] = repo.trials_of_analysis(analysis)
        touched = False
        for trial in members:
            if trial.state != "completed" or trial.symbol in runs or trial.symbol in study.symbols:
                continue
            run = facts(session, trial.run_id)
            if run is not None:
                runs[trial.symbol], touched = run, True
        if touched:
            used.append(str(analysis.analysis_id))
    return BreadthFacts(analyses=used, runs=runs) if runs else None


def walk_forward_facts(
    repo: SyncResearchRepository, study: ResearchStudy, candidate
) -> WalkForwardFacts | None:
    for analysis in reversed(_completed(repo.analyses(study, kind="walk_forward"))):
        if analysis.candidate_pk != candidate.id:
            continue
        result = analysis.result or {}
        return WalkForwardFacts(
            analysis=str(analysis.analysis_id),
            efficiency=result.get("efficiency"),
            windows=len(result.get("windows") or []),
            windows_used=int(result.get("windows_used") or 0),
            negative_windows=int(result.get("negative_windows") or 0),
            excluded_windows=list(result.get("excluded_windows") or []),
            run_ids=list((result.get("runs") or {}).values()),
        )
    return None


def monte_carlo_facts(
    repo: SyncResearchRepository, study: ResearchStudy, candidate
) -> MonteCarloFacts | None:
    """The newest Monte Carlo of the candidate's source run (bootstrap first)."""
    for analysis in reversed(_completed(repo.analyses(study, kind="monte_carlo"))):
        if str(analysis.request.get("run_id")) != str(candidate.source_run_id):
            continue
        simulations = (analysis.result or {}).get("simulations") or {}
        method = "bootstrap" if "bootstrap" in simulations else next(iter(simulations), None)
        if method is None:
            continue
        sim = simulations[method]
        return MonteCarloFacts(
            analysis=str(analysis.analysis_id),
            run_id=str(candidate.source_run_id),
            method=method,
            drawdown_p5=(sim.get("max_drawdown") or {}).get("p5"),
            breach_probability=sim.get("breach_probability"),
            breach=sim.get("breach"),
        )
    return None


def deflated_sharpe_facts(
    session: Session, trials: list[ResearchTrial], candidate, in_sample: RunFacts | None
) -> DeflatedSharpeFacts | None:
    """The candidate's daily Sharpe against the best of the study's counted trials by luck."""
    if in_sample is None:
        return None
    counted = [t.run_id for t in trials if t.counted and t.state == "completed" and t.run_id]
    sharpes = [
        h["sharpe_ratio"]
        for h in ledger.headlines(session, counted).values()
        if h.get("sharpe_ratio") is not None
    ]
    points = SyncEquityCurveRepository(session).find_points(candidate.source_run_id)
    if len(sharpes) < 2 or not points:
        return None
    returns = daily_returns(equity_series(points)).to_numpy(dtype=float)
    if len(returns) < 3 or float(np.std(returns, ddof=1)) == 0.0:
        return None
    daily = float(np.mean(returns) / np.std(returns, ddof=1))
    variance = float(np.var(np.array(sharpes, dtype=float) / math.sqrt(TRADING_DAYS), ddof=1))
    result = deflated_sharpe(
        daily,
        observations=len(returns),
        skew=float(stats.skew(returns)),
        kurtosis=float(stats.kurtosis(returns, fisher=False)),
        trials=max(ledger.used(trials), len(sharpes)),
        trial_sharpe_variance=variance,
    )
    return DeflatedSharpeFacts(
        run_id=in_sample.run_id,
        probability=result["probability"],
        trials=result["trials"],
        sharpe_annualised=in_sample.metrics.get("sharpe_ratio"),
        expected_max_sharpe=result["expected_max_sharpe"],
        observations=result["observations"],
    )


def gather(
    session: Session,
    repo: SyncResearchRepository,
    study: ResearchStudy,
    trials: list[ResearchTrial],
    candidate: ResearchCandidate,
    *,
    in_sample: RunFacts | None,
    facts: Facts,
) -> dict[str, Any]:
    """Every phase-4 evidence field of ``Evidence``, from the study's analyses."""
    return {
        "neighbourhood": neighbourhood_facts(session, repo, study, candidate, facts),
        "cost_stress": stress_facts(session, repo, study, candidate, facts),
        "breadth": breadth_facts(session, repo, study, candidate, facts),
        "walk_forward": walk_forward_facts(repo, study, candidate),
        "monte_carlo": monte_carlo_facts(repo, study, candidate),
        "deflated_sharpe": deflated_sharpe_facts(session, trials, candidate, in_sample),
    }
