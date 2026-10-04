"""Freeze a candidate, test it out-of-sample once, and version it (S5.1, S5.2, S6.3).

A candidate is frozen from a completed in-sample trial of the study's current
version: its parameters, the code commit and a window-independent candidate
hash are recorded and never change. The out-of-sample run is built from that
record alone, so what is tested is exactly what was explored; a second
out-of-sample run of the same candidate needs a reason and marks the study
contaminated. A new version reopens exploration in the same study; if the
holdout has already been seen, the study is marked contaminated.
"""

from typing import Any

from src.db.models.research import ResearchCandidate, ResearchStudy, ResearchTrial
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.run_reads import load_run, run_catalog
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs import service
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.request import resolve
from src.mcp_server.studies import ledger
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.split import Split
from src.mcp_server.studies.status import move
from src.mcp_server.studies.submit import StudyJob, require_status
from src.mcp_server.studies.views import candidate_view, study_view
from src.mcp_server.tools import build_spec
from src.models.backtest_request import DEFAULT_FILL_SEED
from src.services.provenance import compute_candidate_hash, git_provenance

TESTABLE = frozenset({"frozen", "tested"})


def current_candidate(repo: SyncResearchRepository, study: ResearchStudy) -> ResearchCandidate:
    """The frozen candidate of the study's current version, or a failure saying to freeze."""
    for candidate in repo.candidates(study):
        if candidate.version == study.current_version:
            return candidate
    raise ToolFailure(
        "no_candidate",
        f"Version {study.current_version} of '{study.slug}' has no frozen candidate.",
        fix="freeze_candidate(study, run_id) with an in-sample run of this version.",
    )


def _candidate_spec(candidate: ResearchCandidate, start, end):
    return build_spec(
        strategy=candidate.strategy_type,
        symbol=candidate.symbol,
        start=start,
        end=end,
        timeframe=candidate.timeframe,
        catalog=candidate.catalog,
        params=candidate.params,
        starting_balance=candidate.starting_balance,
        fill_seed=candidate.fill_seed,
    )


def _source_trial(repo: SyncResearchRepository, study: ResearchStudy, run_id: str) -> ResearchTrial:
    for trial in repo.trials(study):
        if str(trial.run_id) == run_id and trial.role == "in_sample":
            if trial.version != study.current_version:
                break
            return trial
    raise ToolFailure(
        "not_a_trial",
        f"Run {run_id} is not a completed in-sample trial of version "
        f"{study.current_version} of '{study.slug}'.",
        fix="get_study lists the ledger; freeze a run whose role is in_sample.",
    )


def _new_candidate(study: ResearchStudy, run: Any) -> tuple[ResearchCandidate, list[str]]:
    snapshot = run.config_snapshot or {}
    split = Split.of(study)
    candidate = ResearchCandidate(
        study_pk=study.id,
        version=study.current_version,
        source_run_id=run.run_id,
        strategy_type=run.strategy_type,
        symbol=run.instrument_symbol,
        timeframe=(snapshot.get("bar_type") or "1-DAY-LAST").removesuffix("-LAST"),
        catalog=run_catalog(run) or study.catalog,
        params=snapshot.get("config") or {},
        starting_balance=run.initial_capital,
        fill_seed=snapshot.get("fill_seed") or DEFAULT_FILL_SEED,
    )
    resolved = resolve(_candidate_spec(candidate, split.is_start, split.is_end), default_catalog="")
    candidate.candidate_hash = compute_candidate_hash(resolved.request)
    now = git_provenance()
    candidate.git_commit, candidate.git_dirty = now.git_commit, now.git_dirty
    candidate.strategies_commit = now.strategies_commit
    warnings = []
    if now.git_dirty:
        warnings.append("Frozen from a dirty working tree: commit before testing out-of-sample.")
    if run.git_commit and now.git_commit != run.git_commit:
        warnings.append(
            f"The run was made at {run.git_commit[:12]}, HEAD is now "
            f"{(now.git_commit or '?')[:12]}: the frozen code may differ from what was explored."
        )
    return candidate, warnings


def freeze_candidate(store: JobStore, key: str, run_id: str, note: str | None) -> dict[str, Any]:
    """Freeze the current version's candidate from one of its in-sample runs."""
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, key, for_update=True)
        ledger.settle(repo, store, study)
        require_status(study, frozenset({"exploring"}), "freezing")
        trial = _source_trial(repo, study, run_id.strip().lower())
        if trial.state != "completed":
            raise ToolFailure(
                "not_finished", f"Run of job {trial.job_id} has not finished.", fix="Wait for it."
            )
        candidate, warnings = _new_candidate(study, load_run(session, str(trial.run_id)))
        repo.add_candidate(candidate)
        details = {"version": candidate.version, "run_id": str(candidate.source_run_id)}
        move(repo, study, "frozen", kind="frozen", reason=note, details=details)
        session.flush()
        return {"candidate": candidate_view(candidate), "warnings": warnings}


def new_candidate_version(store: JobStore, key: str, reason: str, change: str) -> dict[str, Any]:
    """Reopen exploration as the next version; contaminated if the holdout was already seen."""
    if not reason.strip() or not change.strip():
        raise ToolFailure(
            "reason_required", "Say what changes and why.", fix="Pass reason and change."
        )
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, key, for_update=True)
        ledger.settle(repo, store, study)
        require_status(study, TESTABLE, "a new candidate version")
        seen = any(t.role == "out_of_sample" and t.state != "void" for t in repo.trials(study))
        before = study.current_version
        study.current_version = before + 1
        details = {"version": before + 1, "change": change}
        move(repo, study, "exploring", kind="new_version", reason=reason, details=details)
        if seen and not study.contaminated:
            study.contaminated = True
            study.contamination_reason = (
                f"Version {before + 1} was created after the out-of-sample window was seen."
            )
            repo.add_event(study, "contaminated", study.contamination_reason)
        session.flush()
        return {"study": study_view(session, repo, study)}


def _once(candidate_pk: int, reason: str | None):
    """The locked check: one out-of-sample run per candidate, unless overridden."""

    def check(repo: SyncResearchRepository, study: ResearchStudy) -> None:
        spent = [
            t
            for t in repo.trials(study)
            if t.role == "out_of_sample" and t.candidate_pk == candidate_pk and t.state != "void"
        ]
        if not spent:
            return
        if not (reason and reason.strip()):
            raise ToolFailure(
                "out_of_sample_spent",
                f"Version {study.current_version}'s candidate already has an out-of-sample run "
                f"(job {spent[0].job_id}).",
                fix="Pass override_reason to run it again: the study is then marked "
                "contaminated. Or call new_candidate_version.",
            )
        study.contaminated = True
        study.contamination_reason = f"The out-of-sample window was run again: {reason}"
        repo.add_event(study, "out_of_sample_override", reason, {"previous_job": spent[0].job_id})

    return check


def prepare_out_of_sample(key: str, override_reason: str | None) -> tuple[StudyJob, list[str]]:
    """The frozen candidate on the out-of-sample window, built from its record alone."""
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, key)
        require_status(study, TESTABLE, "the out-of-sample run")
        candidate = current_candidate(repo, study)
        split = Split.of(study)
        spec = _candidate_spec(candidate, split.oos_start, split.oos_end)
        if compute_candidate_hash(resolve(spec, default_catalog="").request) != (
            candidate.candidate_hash
        ):
            raise ToolFailure(
                "candidate_drift",
                "The frozen parameters no longer resolve to the frozen candidate (a parameter "
                "model or default changed since freezing).",
                fix="Freeze a new version with new_candidate_version.",
            )
        warnings = []
        now = git_provenance()
        if now.git_commit != candidate.git_commit or now.git_dirty:
            warnings.append(
                f"Code differs from the frozen candidate ({(candidate.git_commit or '?')[:12]}"
                f"{', dirty' if now.git_dirty else ''}): the scorecard will show it."
            )
        slug, candidate_pk, version = study.slug, candidate.id, candidate.version
    payload = service.prepare_backtest(spec, default_catalog=spec.catalog or "")
    payload["study"] = {"slug": slug, "role": "out_of_sample", "version": version}
    job = StudyJob(
        study=slug,
        role="out_of_sample",
        payload=payload,
        statuses=TESTABLE,
        reason=override_reason,
        candidate_pk=candidate_pk,
        checks=[_once(candidate_pk, override_reason)],
    )
    return job, warnings + payload["warnings"]
