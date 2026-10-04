"""The one place a study's status changes, so every change is recorded with its reason.

Study status (``exploring`` … ``promoted``) is unrelated to a trading session's
status; the AR37 guard in ``tests/unit/services/test_session_service.py``
allow-lists this module for that reason, and only this module.
"""

from datetime import datetime, timezone
from typing import Any

from src.db.models.research import ResearchStudy
from src.db.repositories.research_repository import SyncResearchRepository


def touch(study: ResearchStudy) -> None:
    """Mark the study as changed now."""
    study.updated_at = datetime.now(timezone.utc)


def move(
    repo: SyncResearchRepository,
    study: ResearchStudy,
    to: str,
    *,
    kind: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Set the study's status and append the event recording the change."""
    repo.add_event(study, kind, reason, {"from": study.status, "to": to, **(details or {})})
    study.status = to
    study.status_reason = reason
    touch(study)
