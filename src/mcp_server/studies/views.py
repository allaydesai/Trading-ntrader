"""Studies as plain JSON for tool results."""

from typing import Any

from sqlalchemy.orm import Session

from src.db.models.research import ResearchCandidate, ResearchStudy, ResearchStudyEvent
from src.db.repositories.research_repository import SyncResearchRepository
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.studies import ledger
from src.mcp_server.studies.split import Split


def candidate_view(candidate: ResearchCandidate) -> dict[str, Any]:
    return to_jsonable(
        {
            "candidate_id": candidate.candidate_id,
            "version": candidate.version,
            "source_run_id": candidate.source_run_id,
            "strategy": candidate.strategy_type,
            "symbol": candidate.symbol,
            "timeframe": candidate.timeframe,
            "catalog": candidate.catalog,
            "params": candidate.params,
            "starting_balance": candidate.starting_balance,
            "fill_seed": candidate.fill_seed,
            "git_commit": candidate.git_commit,
            "git_dirty": candidate.git_dirty,
            "strategies_commit": candidate.strategies_commit,
            "candidate_hash": candidate.candidate_hash,
            "frozen_at": candidate.frozen_at,
        }
    )


def event_view(event: ResearchStudyEvent) -> dict[str, Any]:
    return to_jsonable(
        {
            "kind": event.kind,
            "reason": event.reason,
            "details": event.details,
            "at": event.created_at,
        }
    )


def summary_view(study: ResearchStudy, spent: dict[str, int]) -> dict[str, Any]:
    """The study's identity, split and state, without its ledger."""
    return to_jsonable(
        {
            "study_id": study.study_id,
            "slug": study.slug,
            "title": study.title,
            "status": study.status,
            "status_reason": study.status_reason,
            "strategy": study.strategy_type,
            "symbols": study.symbols,
            "timeframe": study.timeframe,
            "catalog": study.catalog,
            "current_version": study.current_version,
            "contaminated": study.contaminated,
            "contamination_reason": study.contamination_reason,
            "budget": spent,
            "split": Split.of(study).to_dict(),
            "tags": study.tags,
            "created_at": study.created_at,
            "updated_at": study.updated_at,
        }
    )


def study_view(session: Session, repo: SyncResearchRepository, study: ResearchStudy) -> dict:
    """Everything about a study: summary, hypothesis, space, ledger, candidates, events."""
    trials = repo.trials(study)
    view = summary_view(study, ledger.budget(study, trials))
    view.update(
        to_jsonable(
            {
                "hypothesis": study.hypothesis,
                "param_space": study.param_space,
                "pass_criteria": study.pass_criteria,
                "starting_balance": study.starting_balance,
            }
        )
    )
    view["ledger"] = ledger.ledger_view(session, trials)
    view["candidates"] = [candidate_view(c) for c in repo.candidates(study)]
    view["events"] = [event_view(e) for e in repo.events(study)]
    if study.contaminated:
        view["warnings"] = [
            f"Contaminated: {study.contamination_reason} Out-of-sample results are no longer "
            "clean evidence; later versions must prove themselves by walk-forward and paper."
        ]
    return view
