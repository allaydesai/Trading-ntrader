"""Helpers for ``ntrader live start``, split out of ``live.py`` (Story 2.6).

Owns: claiming a session (the atomic ``-> running`` transition), the AR28
exit-code mapping for a failure the runner never saw, and the quiet release of
a claimed-but-not-yet-running session back to ``stopped``.

Split out purely for CLAUDE.md's 500-line file limit — ``live.py`` was at 497
of 500 before this story's stop-line and release-failure-warning additions.
No behaviour changes; every symbol here moved verbatim from ``live.py``.
"""

from datetime import datetime
from typing import NoReturn
from uuid import UUID

import structlog
from rich.console import Console

from src.core.live_check import EXIT_CODES, classify_failure, failure_message
from src.core.live_session_record import SessionReclaimedError
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.db.session_sync import get_sync_session
from src.models.session import SessionStatus
from src.services.session_record import SqlSessionRecord
from src.services.session_service import SessionService

console = Console()
logger = structlog.get_logger(__name__)


def claim_session(identifier: str) -> tuple[UUID, dict, datetime]:
    """Resolve the identifier and move the session to ``running``, atomically.

    Everything happens inside **one** short-lived ``get_sync_session()`` block
    that closes before the runner exists. Story 2.3's forward constraint is
    explicit about why: the ``-> running`` transition takes an exclusive row
    lock (it is the reclaim-or-refuse decision), and holding that lock for the
    life of a 6.5-hour session would block every ``live status``.

    Returns:
        The session's UUID, its stored spec payload, and the instant this
        transition stamped into ``last_started_at`` — the value the runner's
        record port is bound to, and the one the mid-run reclaim guard reads.

    Raises:
        RecordNotFoundError: No session matches ``identifier``.
        InvalidSessionTransition: The session is already running with a fresh
            heartbeat, or is sealed.
    """
    with get_sync_session() as db_session:
        service = SessionService(SyncTradingSessionRepository(db_session))
        trading_session = service.resolve(identifier)
        started = service.transition(trading_session.session_id, to=SessionStatus.RUNNING)
        return started.session_id, started.spec, started.last_started_at


def exit_with(exc: BaseException) -> NoReturn:
    """Render the failure and exit on AR28's code for it.

    Reuses ``live_check``'s table rather than inventing a second one: Story 1.7
    recorded that *"a CLI that invents an exit code outside its own documented
    table is worse than one that reports a generic failure"*. ``markup=False``
    because parts of this text can arrive from third-party exception strings,
    and a stray ``[...]`` would otherwise be eaten as Rich markup — silently
    dropping the operator's most important line.

    **Never retries.** ``InvalidSessionTransition`` inherits
    ``BacktestStorageError``, and an ``except BacktestStorageError: retry()``
    would spin until the incumbent's heartbeat went stale and then reclaim a
    live session — two processes on one broker account, which is the
    catastrophic failure NFR6 exists to prevent.
    """
    console.print(f"live start failed: {failure_message(exc)}", markup=False, highlight=False)
    raise SystemExit(EXIT_CODES[classify_failure(exc)])


def release_quietly(record: SqlSessionRecord) -> None:
    """Put the row back to ``stopped`` after a failure the runner never saw.

    The runner's own ``finally`` does this for anything raised inside
    ``run()``. This covers the window between the ``-> running`` transition
    and the runner existing — where a raise would otherwise leave the row
    ``running`` and the session unstartable for the full 90-second staleness
    threshold.

    Guarded, because the failure already in flight is the one the operator
    needs; a second failure here must not replace it.
    """
    try:
        record.mark_stopped()
    except SessionReclaimedError:
        # The stop-path ownership guard refused (review fix, 2026-08-21):
        # another process took the session; its row is not ours to release.
        logger.error(
            "session.reclaimed_by_another_process",
            detail="release skipped; the row belongs to another process now",
        )
    except Exception as exc:  # noqa: BLE001 - must never replace the primary failure
        logger.error("session.release_failed", error_type=type(exc).__name__)
