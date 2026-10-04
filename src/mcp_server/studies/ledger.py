"""The trial ledger: every study job, counted from the moment it is submitted (S1.3).

A trial row is written in the same transaction that checks the budget, keyed by
its job id, so queued and running trials already count. It is settled lazily,
at the start of every study operation, from the job store: a succeeded job's
trial becomes ``completed`` with its run id; a failed, cancelled or lost job's
becomes ``void`` and stops counting, since no result was seen.
"""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models.backtest import BacktestRun, PerformanceMetrics
from src.db.models.research import ResearchStudy, ResearchTrial
from src.db.repositories.research_repository import SyncResearchRepository
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import TERMINAL_STATES, JobStore
from src.mcp_server.jsonable import to_jsonable

#: Roles that count against the trial budget.
COUNTED_ROLES = frozenset({"in_sample"})
HEADLINE = ("total_return", "sharpe_ratio", "max_drawdown", "profit_factor", "total_trades")


def settle(repo: SyncResearchRepository, store: JobStore, study: ResearchStudy) -> None:
    """Settle every pending trial whose job has ended."""
    for trial in repo.trials(study):
        if trial.state != "pending":
            continue
        try:
            status = store.status(trial.job_id)
        except ToolFailure:  # the job lives in another jobs directory: leave it counted
            continue
        state = status.get("state")
        if state not in TERMINAL_STATES:
            continue
        run_id = status.get("run_id") if state == "succeeded" else None
        if run_id:
            repo.settle_trial(trial, state="completed", run_id=UUID(run_id))
        else:
            repo.settle_trial(trial, state="void", run_id=None)


def used(trials: list[ResearchTrial]) -> int:
    """Trials that count against the budget: counted roles, not voided."""
    return sum(1 for t in trials if t.counted and t.state != "void")


def budget(study: ResearchStudy, trials: list[ResearchTrial]) -> dict[str, int]:
    spent = used(trials)
    return {"used": spent, "budget": study.trial_budget, "remaining": study.trial_budget - spent}


def require_budget(
    study: ResearchStudy, trials: list[ResearchTrial], adding: int, reason: str | None
) -> None:
    """Refuse trials past the budget unless a reason is given (it is stored on each trial)."""
    spent = used(trials)
    if spent + adding <= study.trial_budget or (reason and reason.strip()):
        return
    raise ToolFailure(
        "over_budget",
        f"Study '{study.slug}' has used {spent} of {study.trial_budget} trials; "
        f"{adding} more would exceed the budget.",
        fix="Pass over_budget_reason to run anyway (it is recorded), or extend the budget "
        "with update_study(extend_budget_by=..., reason=...).",
    )


def new_trial(
    study: ResearchStudy,
    *,
    role: str,
    job_id: str,
    resolved: dict[str, Any],
    override_reason: str | None = None,
    candidate_pk: int | None = None,
) -> ResearchTrial:
    """A ledger row for a job just reserved from the resolved request summary."""
    return ResearchTrial(
        study_pk=study.id,
        candidate_pk=candidate_pk,
        version=study.current_version,
        role=role,
        job_id=job_id,
        counted=role in COUNTED_ROLES,
        strategy_type=resolved["strategy"],
        symbol=resolved["symbol"],
        params=resolved["params"],
        config_hash=resolved["config_hash"],
        start=date.fromisoformat(resolved["start"]),
        end=date.fromisoformat(resolved["end"]),
        override_reason=override_reason,
    )


def headlines(session: Session, run_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    """Headline metrics and dirty flag per run, in one query (no trades loaded)."""
    if not run_ids:
        return {}
    columns = [getattr(PerformanceMetrics, name) for name in HEADLINE]
    stmt = (
        select(BacktestRun.run_id, BacktestRun.git_dirty, *columns)
        .join(PerformanceMetrics, PerformanceMetrics.backtest_run_id == BacktestRun.id)
        .where(BacktestRun.run_id.in_(run_ids))
    )
    found = {}
    for row in session.execute(stmt).all():
        values = dict(zip(HEADLINE, row[2:]))
        found[row[0]] = to_jsonable({**values, "git_dirty": row[1]})
    return found


def trial_view(number: int, trial: ResearchTrial, headline: dict[str, Any] | None) -> dict:
    """One ledger row as JSON."""
    return to_jsonable(
        {
            "trial": number,
            "job_id": trial.job_id,
            "role": trial.role,
            "version": trial.version,
            "state": trial.state,
            "counted": trial.counted and trial.state != "void",
            "run_id": trial.run_id,
            "strategy": trial.strategy_type,
            "symbol": trial.symbol,
            "start": trial.start,
            "end": trial.end,
            "params": trial.params,
            "override_reason": trial.override_reason,
            "headline": headline,
            "submitted_at": trial.created_at,
        }
    )


def ledger_view(session: Session, trials: list[ResearchTrial]) -> list[dict[str, Any]]:
    """The whole ledger, numbered in submission order, with each run's headline."""
    found = headlines(session, [t.run_id for t in trials if t.run_id])
    return [
        trial_view(n, t, found.get(t.run_id) if t.run_id else None)
        for n, t in enumerate(trials, start=1)
    ]
