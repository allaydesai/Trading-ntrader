"""Data access for research studies (sync: the research MCP is the only user).

Candidates and events are **insert-only**: there is no method that updates or
deletes either, of any name (pinned by ``tests/unit/db/
test_research_repository_shape.py``), and the models refuse an ORM update.
Studies are mutable only in their lifecycle fields, which the MCP changes on the
object it loaded ``for_update``; trials only ever move from ``pending`` to
``completed`` or ``void`` through ``settle_trial``.
"""

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models.research import (
    ResearchCandidate,
    ResearchStudy,
    ResearchStudyEvent,
    ResearchTrial,
)


def _as_uuid(key: str) -> UUID | None:
    try:
        return UUID(key.strip())
    except ValueError:
        return None


class SyncResearchRepository:
    """Studies, their ledger, candidates and events, on a sync session."""

    def __init__(self, session: Session):
        self.session = session

    def add_study(self, study: ResearchStudy) -> ResearchStudy:
        """Insert a new study; flushes so its id and study_id are set."""
        self.session.add(study)
        self.session.flush()
        return study

    def find_study(self, key: str, *, for_update: bool = False) -> ResearchStudy | None:
        """A study by slug or study id; ``for_update`` locks its row to the transaction."""
        study_id = _as_uuid(key)
        column = ResearchStudy.study_id if study_id else ResearchStudy.slug
        stmt = select(ResearchStudy).where(column == (study_id or key.strip()))
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalars(stmt).first()

    def search_studies(
        self,
        *,
        status: str | None = None,
        strategy: str | None = None,
        symbol: str | None = None,
        tag: str | None = None,
        text: str | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        limit: int = 50,
    ) -> list[ResearchStudy]:
        """Studies matching every given filter, newest first."""
        stmt = select(ResearchStudy)
        if status:
            stmt = stmt.where(ResearchStudy.status == status)
        if strategy:
            stmt = stmt.where(ResearchStudy.strategy_type == strategy)
        if symbol:
            stmt = stmt.where(ResearchStudy.symbols.contains([symbol.upper()]))
        if tag:
            stmt = stmt.where(ResearchStudy.tags.contains([tag]))
        if text:
            pattern = f"%{text}%"
            stmt = stmt.where(
                ResearchStudy.slug.ilike(pattern)
                | ResearchStudy.title.ilike(pattern)
                | ResearchStudy.hypothesis.ilike(pattern)
            )
        if created_from:
            stmt = stmt.where(func.date(ResearchStudy.created_at) >= created_from)
        if created_to:
            stmt = stmt.where(func.date(ResearchStudy.created_at) <= created_to)
        stmt = stmt.order_by(ResearchStudy.created_at.desc(), ResearchStudy.id.desc())
        return list(self.session.scalars(stmt.limit(limit)))

    def add_event(
        self,
        study: ResearchStudy,
        kind: str,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ResearchStudyEvent:
        """Append a decision to the study's record."""
        event = ResearchStudyEvent(
            study_pk=study.id, kind=kind, reason=reason, details=details or {}
        )
        self.session.add(event)
        self.session.flush()
        return event

    def events(self, study: ResearchStudy) -> list[ResearchStudyEvent]:
        stmt = select(ResearchStudyEvent).where(ResearchStudyEvent.study_pk == study.id)
        return list(self.session.scalars(stmt.order_by(ResearchStudyEvent.id)))

    def add_trial(self, trial: ResearchTrial) -> ResearchTrial:
        """Record a submitted job in the study's ledger."""
        self.session.add(trial)
        self.session.flush()
        return trial

    def trials(self, study: ResearchStudy) -> list[ResearchTrial]:
        """The ledger, oldest first."""
        stmt = select(ResearchTrial).where(ResearchTrial.study_pk == study.id)
        return list(self.session.scalars(stmt.order_by(ResearchTrial.id)))

    def settle_trial(self, trial: ResearchTrial, *, state: str, run_id: UUID | None) -> None:
        """Move a pending trial to ``completed`` (with its run) or ``void``."""
        if trial.state != "pending":
            raise ValueError(f"trial {trial.job_id} is already {trial.state}")
        trial.state, trial.run_id = state, run_id
        trial.settled_at = datetime.now(timezone.utc)
        self.session.flush()

    def trials_of_runs(self, run_ids: list[UUID]) -> list[ResearchTrial]:
        """Ledger rows for the given runs (a run belongs to at most one trial)."""
        if not run_ids:
            return []
        stmt = select(ResearchTrial).where(ResearchTrial.run_id.in_(run_ids))
        return list(self.session.scalars(stmt))

    def add_candidate(self, candidate: ResearchCandidate) -> ResearchCandidate:
        """Freeze a candidate. There is no way to change it afterwards."""
        self.session.add(candidate)
        self.session.flush()
        return candidate

    def candidates(self, study: ResearchStudy) -> list[ResearchCandidate]:
        """Every frozen candidate of the study, by version."""
        stmt = select(ResearchCandidate).where(ResearchCandidate.study_pk == study.id)
        return list(self.session.scalars(stmt.order_by(ResearchCandidate.version)))
