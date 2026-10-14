"""Submit a batch of jobs inside a study as one analysis (S3.1, S4.4, S5.3).

Like ``submit.reserve_trial`` for N jobs: every member is prepared (resolved
and validated) before anything is reserved, then, with the study's row
locked, the state and budget are checked once for the whole batch, an
analysis row is written, each job is reserved in the job store and recorded
as a trial of that analysis, and the lot commits before any job is queued. If
anything fails, every reserved job is cancelled and nothing was queued.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import anyio
from sqlalchemy import select

from src.db.models.research import ResearchAnalysis, ResearchStudy
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.runner import JobRunner
from src.mcp_server.jobs.store import TERMINAL_STATES, utc_now
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.studies import ledger
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.submit import LockedCheck, require_status


@dataclass
class Batch:
    """Prepared jobs that make one analysis, and how the ledger records them."""

    study: str
    role: str
    kind: str
    payloads: list[dict[str, Any]]
    cells: list[dict[str, Any]]
    statuses: frozenset[str]
    request: dict[str, Any]
    reason: str | None = None
    candidate_pk: int | None = None
    checks: list[LockedCheck] = field(default_factory=list)


def _record(
    runner: JobRunner,
    repo: SyncResearchRepository,
    study: ResearchStudy,
    batch: Batch,
    analysis: ResearchAnalysis,
    payload: dict[str, Any],
    reserved: list[str],
) -> Any:
    job_id = runner.reserve(payload)
    reserved.append(job_id)
    return repo.add_trial(
        ledger.new_trial(
            study,
            role=batch.role,
            job_id=job_id,
            resolved=payload["resolved"],
            override_reason=batch.reason,
            candidate_pk=batch.candidate_pk,
            analysis_pk=analysis.id,
        )
    )


def reserve_trials(runner: JobRunner, batch: Batch) -> dict[str, Any]:
    """Reserve every job of ``batch`` under one study lock and one budget check."""
    if not batch.payloads:
        raise ToolFailure("empty_batch", "Nothing to run.", fix="Give at least one cell.")
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, batch.study, for_update=True)
        ledger.settle(repo, runner.store, study)
        require_status(study, batch.statuses, f"{batch.kind.replace('_', '-')} runs")
        for check in batch.checks:
            check(repo, study)
        trials = repo.trials(study)
        adding = len(batch.payloads)
        counted = batch.role in ledger.COUNTED_ROLES
        over = counted and ledger.used(trials) + adding > study.trial_budget
        if counted:
            ledger.require_budget(study, trials, adding, batch.reason)
        analysis = _open_analysis(repo, study, batch)
        reserved: list[str] = []
        try:
            rows = [
                _record(runner, repo, study, batch, analysis, p, reserved) for p in batch.payloads
            ]
            analysis.request = to_jsonable(
                {**batch.request, "cells": dict(zip(reserved, batch.cells))}
            )
            if over:
                details = {"analysis_id": str(analysis.analysis_id), "job_ids": reserved}
                repo.add_event(study, "over_budget", batch.reason, details)
            result = _summary(study, batch, analysis, trials, rows)
            session.commit()
        except Exception:
            for job_id in reserved:
                runner.store.update(job_id, state="cancelled", finished_at=utc_now())
            raise
    return result


def _open_analysis(
    repo: SyncResearchRepository, study: ResearchStudy, batch: Batch
) -> ResearchAnalysis:
    return repo.add_analysis(
        ResearchAnalysis(
            study_pk=study.id,
            candidate_pk=batch.candidate_pk,
            version=study.current_version,
            kind=batch.kind,
            status="pending",
            request=to_jsonable(batch.request),
        )
    )


def _summary(
    study: ResearchStudy, batch: Batch, analysis: ResearchAnalysis, trials: list, rows: list
) -> dict[str, Any]:
    numbers = ledger.numbering([*trials, *rows])[-len(rows) :]
    return {
        "analysis_id": str(analysis.analysis_id),
        "kind": batch.kind,
        "study": study.slug,
        "role": batch.role,
        "version": study.current_version,
        "jobs": [
            {"job_id": r.job_id, "entry": n[0], "trial": n[1], "cell": c}
            for r, n, c in zip(rows, numbers, batch.cells)
        ],
        "count": len(rows),
        "budget": ledger.budget(study, [*trials, *rows]),
    }


async def submit_batch(runner: JobRunner, batch: Batch, warnings: list[str]) -> dict[str, Any]:
    """Record a prepared batch in its ledger, queue every job, report it as a tool result."""
    try:
        recorded = await anyio.to_thread.run_sync(reserve_trials, runner, batch)
    except ToolFailure as failure:
        return failure.to_dict()
    for job in recorded["jobs"]:
        runner.enqueue(job["job_id"])
    return {
        "ok": True,
        **recorded,
        "state": "queued",
        "warnings": warnings,
        "queue": runner.queue_state(),
    }


def find_analysis(session: Any, analysis_id: str) -> ResearchAnalysis:
    """An analysis by id, in any study."""
    try:
        key = UUID(analysis_id.strip())
    except ValueError:
        raise ToolFailure(
            "unknown_analysis",
            f"'{analysis_id}' is not an analysis id.",
            fix="get_study lists them.",
        ) from None
    found = session.scalars(select(ResearchAnalysis).where(ResearchAnalysis.analysis_id == key))
    analysis = found.first()
    if analysis is None:
        raise ToolFailure("unknown_analysis", f"No analysis {key}.", fix="get_study lists them.")
    return analysis


def _member_job_ids(analysis_id: str) -> list[str]:
    with get_sync_session() as session:
        analysis = find_analysis(session, analysis_id)
        return [t.job_id for t in SyncResearchRepository(session).trials_of_analysis(analysis)]


async def cancel_batch(runner: JobRunner, analysis_id: str) -> dict[str, Any]:
    """Cancel every member job of a batch analysis that has not finished."""
    job_ids = await anyio.to_thread.run_sync(_member_job_ids, analysis_id)
    cancelled: list[str] = []
    finished: list[str] = []
    for job_id in job_ids:
        try:
            before = runner.store.status(job_id).get("state")
        except ToolFailure:
            continue
        (finished if before in TERMINAL_STATES else cancelled).append(job_id)
        if before not in TERMINAL_STATES:
            await runner.cancel(job_id)
    return {"analysis_id": analysis_id, "cancelled": cancelled, "already_finished": finished}
