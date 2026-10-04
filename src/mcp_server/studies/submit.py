"""Submit jobs inside a study: holdout lock, budget and ledger in one transaction (F1).

A job is prepared (resolved and validated) first, then, with the study's row
locked, the study's state and budget are checked again, the job is reserved in
the job store and its trial is written; the caller enqueues it only after that
commits. A job reserved but not recorded is cancelled at once, so no job ever
runs outside the ledger.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import anyio

from src.db.models.research import ResearchStudy
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs import service
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.jobs.store import utc_now
from src.mcp_server.strategies import resolve_strategy
from src.mcp_server.studies import ledger
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.spec import outside_space
from src.mcp_server.studies.split import Split, require_in_sample
from src.mcp_server.tools import build_spec

#: Called with the locked study and its repository before a job is reserved.
LockedCheck = Callable[[SyncResearchRepository, ResearchStudy], None]


@dataclass
class StudyJob:
    """A prepared job and how the ledger should record it."""

    study: str
    role: str
    payload: dict[str, Any]
    statuses: frozenset[str]
    reason: str | None = None
    candidate_pk: int | None = None
    checks: list[LockedCheck] = field(default_factory=list)


def require_status(study: ResearchStudy, statuses: frozenset[str], action: str) -> None:
    """Refuse an action the study's status does not allow, saying what would."""
    if study.status in statuses:
        return
    if study.status in ("frozen", "tested") and "exploring" in statuses:
        fix = "Version is frozen: call new_candidate_version to explore a changed candidate."
    elif study.status in ("rejected", "parked", "promoted"):
        fix = "A parked study resumes with update_study(status='active', reason=...)."
    else:
        fix = "freeze_candidate first."
    raise ToolFailure(
        "invalid_study_state",
        f"Study '{study.slug}' is {study.status}; {action} needs it "
        f"{' or '.join(sorted(statuses))}.",
        fix=fix,
    )


def reserve_trial(runner: JobRunner, job: StudyJob) -> dict[str, Any]:
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, job.study, for_update=True)
        ledger.settle(repo, runner.store, study)
        require_status(study, job.statuses, job.role.replace("_", "-") + " runs")
        for check in job.checks:
            check(repo, study)
        trials = repo.trials(study)
        counted = job.role in ledger.COUNTED_ROLES
        over = counted and ledger.used(trials) + 1 > study.trial_budget
        if counted:
            ledger.require_budget(study, trials, 1, job.reason)
        job_id = runner.reserve(job.payload)
        try:
            trial = repo.add_trial(
                ledger.new_trial(
                    study,
                    role=job.role,
                    job_id=job_id,
                    resolved=job.payload["resolved"],
                    override_reason=job.reason,
                    candidate_pk=job.candidate_pk,
                )
            )
            if over:
                repo.add_event(study, "over_budget", job.reason, {"job_id": job_id})
            result = {
                "job_id": job_id,
                "study": study.slug,
                "trial": len(trials) + 1,
                "role": job.role,
                "version": trial.version,
                "budget": ledger.budget(study, [*trials, trial]),
            }
            session.commit()
        except Exception:
            runner.store.update(job_id, state="cancelled", finished_at=utc_now())
            raise
    return result


async def submit_in_study(runner: JobRunner, job: StudyJob) -> dict[str, Any]:
    """Record the job in the study's ledger, then queue it."""
    recorded = await anyio.to_thread.run_sync(reserve_trial, runner, job)
    runner.enqueue(recorded["job_id"])
    return recorded


async def queue_study_job(runner: JobRunner, job: StudyJob, warnings: list[str]) -> dict:
    """Record a prepared study job in its ledger, queue it, and report it as a tool result."""
    try:
        recorded = await submit_in_study(runner, job)
    except ToolFailure as failure:
        return failure.to_dict()
    return {
        "ok": True,
        **recorded,
        "state": "queued",
        "resolved": job.payload["resolved"],
        "warnings": warnings,
        "queue": runner.queue_state(),
    }


def _same(given: str | None, expected: str, what: str, study: ResearchStudy) -> None:
    if given is not None and given.strip().upper() != expected.upper():
        raise ToolFailure(
            "study_mismatch",
            f"Study '{study.slug}' uses {what} {expected}, not {given}.",
            fix=f"Omit {what}, or open a new study for it.",
        )


def prepare_in_sample(
    study_key: str,
    *,
    strategy: str | None,
    symbol: str | None,
    start: date | None,
    end: date | None,
    timeframe: str | None,
    catalog: str | None,
    params: dict[str, Any],
    starting_balance: Decimal | None,
    over_budget_reason: str | None,
) -> tuple[StudyJob, list[str]]:
    """A study's in-sample backtest, resolved and validated; the study fills the gaps."""
    with get_sync_session() as session:
        study = load_study(SyncResearchRepository(session), study_key)
        require_status(study, frozenset({"exploring"}), "in-sample runs")
        if strategy is not None and resolve_strategy(strategy).name != study.strategy_type:
            raise ToolFailure(
                "study_mismatch",
                f"Study '{study.slug}' researches {study.strategy_type}, not {strategy}.",
                fix="Run an incumbent or another reference with submit_benchmark(strategy=...).",
            )
        symbol = (symbol or study.symbols[0]).strip().upper()
        if symbol not in study.symbols:
            raise ToolFailure(
                "study_mismatch",
                f"{symbol} is not one of the study's symbols ({', '.join(study.symbols)}).",
                fix="Use a study symbol; breadth on other symbols arrives in phase 4.",
            )
        bare_timeframe = timeframe and timeframe.upper().removesuffix("-LAST")
        _same(bare_timeframe, study.timeframe, "timeframe", study)
        _same(catalog, study.catalog, "catalog", study)
        split = Split.of(study)
        start, end = start or split.is_start, end or split.is_end
        require_in_sample(split, start, end)
        spec = build_spec(
            strategy=study.strategy_type,
            symbol=symbol,
            start=start,
            end=end,
            timeframe=study.timeframe,
            catalog=study.catalog,
            params=params,
            starting_balance=starting_balance or study.starting_balance,
        )
        warnings = outside_space(study.param_space, params)
        slug = study.slug
    payload = service.prepare_backtest(spec, default_catalog=spec.catalog or "")
    payload["study"] = {"slug": slug, "role": "in_sample"}
    job = StudyJob(
        study=slug,
        role="in_sample",
        payload=payload,
        statuses=frozenset({"exploring"}),
        reason=over_budget_reason,
    )
    return job, warnings + payload["warnings"]
