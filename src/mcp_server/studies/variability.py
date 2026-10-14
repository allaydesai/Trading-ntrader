"""``run_monte_carlo``: the range of outcomes a study run's trades could have made (S4.1, S4.2).

Runs in the server (a few thousand resamples take milliseconds) and is stored
as an analysis of the study, so the scorecard and the vault can cite it. The
run must be in the study's ledger; by default it is the current candidate's
source run, else the newest finished in-sample trial.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import numpy as np

from src.core.robustness import bootstrap
from src.core.robustness.monte_carlo import METHODS, Trade, simulate
from src.db.models.research import ResearchAnalysis, ResearchStudy, ResearchTrial
from src.db.repositories.equity_curve_repository import SyncEquityCurveRepository
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.regimes import daily_returns
from src.mcp_server.analysis.run_reads import load_run
from src.mcp_server.analysis.series import equity_series
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.paper.band import ClosedTrade
from src.mcp_server.paper.reads import closed_trades, to_utc
from src.mcp_server.studies import ledger
from src.mcp_server.studies.lifecycle import load_study

MIN_ITERATIONS = 1000
DAY_SECONDS = 86_400.0


def mc_trades(trades: list[ClosedTrade], run_start: datetime) -> list[Trade]:
    """Monte Carlo trades: each P&L with the days since the previous exit (the first since
    the run started, so the gaps sum to the time from the run's start to the last exit)."""
    ordered = sorted(trades, key=lambda t: t.exit_at)
    previous = [run_start, *(t.exit_at for t in ordered[:-1])]
    return [
        Trade(pnl=t.pnl, gap_days=max((t.exit_at - p).total_seconds() / DAY_SECONDS, 0.0))
        for t, p in zip(ordered, previous)
    ]


def _pick_run(
    repo: SyncResearchRepository,
    study: ResearchStudy,
    trials: list[ResearchTrial],
    run_id: str | None,
) -> tuple[UUID, ResearchTrial]:
    """The run to analyse: the one named (a ledger run), the candidate's source, or the newest."""
    done = [t for t in trials if t.state == "completed" and t.run_id is not None]
    if run_id is not None:
        key = run_id.strip().lower()
        for trial in done:
            if str(trial.run_id) == key:
                return trial.run_id, trial  # type: ignore[return-value]
        raise ToolFailure(
            "not_a_trial",
            f"Run {run_id} is not a finished run of study '{study.slug}'.",
            fix="get_study lists the ledger's runs.",
        )
    for candidate in reversed(repo.candidates(study)):
        if candidate.version == study.current_version:
            for trial in done:
                if trial.run_id == candidate.source_run_id:
                    return trial.run_id, trial  # type: ignore[return-value]
    in_sample = [t for t in done if t.role == "in_sample"]
    if not in_sample:
        raise ToolFailure(
            "no_run", f"Study '{study.slug}' has no finished in-sample run.", fix="Run one first."
        )
    return in_sample[-1].run_id, in_sample[-1]  # type: ignore[return-value]


def _compute(
    trades: list[Trade],
    pnls: np.ndarray,
    returns: np.ndarray,
    *,
    starting_balance: float,
    iterations: int,
    methods: tuple[str, ...],
    breach: float,
    seed: int,
) -> dict[str, Any]:
    simulations = {
        m: simulate(
            trades,
            starting_balance=starting_balance,
            iterations=iterations,
            method=m,
            seed=seed,
            breach=breach,
        )
        for m in methods
    }
    intervals = bootstrap.confidence_intervals(
        daily_returns=returns, trade_pnls=pnls, iterations=min(iterations, 5000), seed=seed
    )
    return {"simulations": simulations, "confidence_intervals": intervals}


def run_monte_carlo(
    store: JobStore,
    study_key: str,
    *,
    run_id: str | None,
    iterations: int,
    method: str,
    breach_drawdown: float,
    seed: int,
    max_iterations: int,
) -> dict[str, Any]:
    """Resample a ledger run's trades; store and return the percentiles and intervals."""
    methods = _check_args(
        method=method, iterations=iterations, max_iterations=max_iterations, breach=breach_drawdown
    )
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, study_key, for_update=True)
        ledger.settle(repo, store, study)
        chosen, trial = _pick_run(repo, study, repo.trials(study), run_id)
        run = load_run(session, str(chosen))
        closed = closed_trades(session, run_pk=run.id)
        if not closed:
            raise ToolFailure(
                "no_trades", f"Run {chosen} has no closed trades.", fix="Nothing to resample."
            )
        points = SyncEquityCurveRepository(session).find_points(run.run_id)
        returns = daily_returns(equity_series(points)).to_numpy() if points else np.array([])
        result = _compute(
            mc_trades(closed, to_utc(run.start_date) or run.start_date),
            np.array([t.pnl for t in closed]),
            returns,
            starting_balance=float(run.initial_capital),
            iterations=iterations,
            methods=methods,
            breach=breach_drawdown,
            seed=seed,
        )
        request = {
            "run_id": str(chosen),
            "iterations": iterations,
            "method": method,
            "breach_drawdown": breach_drawdown,
            "seed": seed,
        }
        stored = _store(repo, study, trial, request, {**result, "run_id": str(chosen)})
        return to_jsonable(
            {
                "analysis_id": stored.analysis_id,
                "run_id": chosen,
                "role": trial.role,
                "trades": len(closed),
                "starting_balance": run.initial_capital,
                **request,
                **result,
            }
        )


def _check_args(*, method: str, iterations: int, max_iterations: int, breach: float) -> tuple:
    if method != "both" and method not in METHODS:
        raise ToolFailure(
            "invalid_method", "method must be bootstrap, reshuffle or both.", fix="Pass one."
        )
    if not MIN_ITERATIONS <= iterations <= max_iterations:
        raise ToolFailure(
            "invalid_request",
            f"iterations must be between {MIN_ITERATIONS} and {max_iterations}.",
            fix="Pass a value in that range.",
        )
    if not 0 < breach < 1:
        raise ToolFailure(
            "invalid_request", "breach_drawdown is a fraction in (0, 1).", fix="e.g. 0.2"
        )
    return METHODS if method == "both" else (method,)


def _store(
    repo: SyncResearchRepository,
    study: ResearchStudy,
    trial: ResearchTrial,
    request: dict[str, Any],
    result: dict[str, Any],
) -> ResearchAnalysis:
    return repo.add_analysis(
        ResearchAnalysis(
            study_pk=study.id,
            candidate_pk=trial.candidate_pk,
            version=trial.version,
            kind="monte_carlo",
            status="completed",
            request=request,
            result=to_jsonable({**result, "role": trial.role}),
            completed_at=datetime.now(timezone.utc),
        )
    )
