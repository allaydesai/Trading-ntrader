"""The SQLAlchemy side of AR32's record port (Story 2.5).

Owns: :class:`SqlSessionRecord` — the adapter that satisfies
``src/core/live_session_record.SessionRecordPort`` by opening a fresh,
short-lived database session per call and routing every write through
``SessionService``.

Does not own: the port's shape (``src/core/live_session_record.py``, standard
library only), the validation the writes go through
(``src/services/session_service.py``, the only module that may assign
``TradingSession.status`` — AR37), or the runner that holds the port
(``src/core/live_session_runner.py``, which never imports this module: the CLI
is the composition root and hands the runner an already-constructed port).

**One transaction per call, never a long-lived one.** This is Story 2.3's
forward constraint, stated there verbatim: *"The runner must let the
``get_sync_session`` block close right after the transition… Keeping it open
for the life of the session would hold the row lock for hours and block every
``live status``."* A 6.5-hour session writes roughly 780 heartbeats; each is
its own transaction and each ends before the call returns.

**``session_id`` and ``started_at`` are bound at construction, not passed per
call.** The runner therefore cannot write to another session's row, and cannot
supply a ``started_at`` other than the one its own ``-> running`` transition
stamped — which is the value ``session_service._stamp_activity`` compares
against to detect being reclaimed mid-run.

Known, accepted limits:

1. This adapter is **synchronous**, and is called from the runner's event loop
   through ``asyncio.to_thread``. That bridge is the runner's decision, not
   this module's; nothing here is safe to ``await``.
2. A write that fails raises. Whether that is survivable is the *caller's*
   policy (AR42 says a database hiccup must not kill a trading session, with
   one named exception), and it is deliberately not decided here.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from uuid import UUID

import structlog

from src.core.live_session_record import SessionReclaimedError
from src.db.exceptions import InvalidSessionTransition
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.db.session_sync import get_sync_session
from src.models.session import SessionStatus
from src.services.session_service import SessionService

logger = structlog.get_logger(__name__)

#: What ``get_sync_session`` is, expressed as a type so it can be injected.
SessionFactory = Callable[[], AbstractContextManager[object]]


class SqlSessionRecord:
    """Write one session's liveness columns and its final ``stopped`` edge.

    Args:
        session_id: The session's UUID business key — ``TradingSession
            .session_id``, not its ``name`` and not its ``id``.
        started_at: The instant this process's own ``-> running`` transition
            stamped into ``last_started_at``. Held for the object's life and
            compared on every write, so a session reclaimed by a second process
            is detected rather than silently double-driven.
        session_factory: The ``get_sync_session`` context manager. Injected so
            the transaction discipline is testable without a database.
    """

    def __init__(
        self,
        session_id: UUID,
        *,
        started_at: datetime,
        session_factory: SessionFactory = get_sync_session,
    ) -> None:
        self._session_id = session_id
        self._started_at = started_at
        self._session_factory = session_factory

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        """Stamp ``last_heartbeat_at`` (and ``last_bar_at`` when given).

        ``at`` is threaded through as the service's ``time_source`` as well as
        its ``at`` argument. That is not redundant: it means the *one* instant
        the caller chose is the only clock reading involved anywhere in the
        write, so a test that advances a fake clock sees exactly its own value
        in the column rather than a wall-clock reading taken here.

        Every ``InvalidSessionTransition`` is translated to the port's own
        :class:`~src.core.live_session_record.SessionReclaimedError`, for two
        reasons. The runner may not import ``src.db`` (AR38), so it could not
        catch the database exception even if it wanted to; and both refusals
        that produce one — the row is not ``running``, or its
        ``last_started_at`` moved past this process's own — mean the same thing
        to a runner, which is that it no longer owns this session and must
        stop. That is a **widening** of Story 2.5's literal wording, which
        names only the reclaim; the "not running" case reaches the same
        conclusion by the same evidence, and treating it as a survivable hiccup
        would log an error every 30 seconds forever against a row that will
        never accept another write. Flagged for the Epic 2 retro.

        Raises:
            RecordNotFoundError: The session's row is gone. Deliberately **not**
                translated: it is not an ownership question, and AR42's
                survive-a-hiccup rule is the caller's to apply.
            SessionReclaimedError: This process no longer owns the session.
        """
        with self._session_factory() as db_session:
            repository = SyncTradingSessionRepository(db_session)  # type: ignore[arg-type]
            service = SessionService(repository, time_source=lambda: at)
            try:
                service.record_activity(
                    self._session_id,
                    started_at=self._started_at,
                    at=at,
                    bar_seen_at=bar_seen_at,
                )
            except InvalidSessionTransition as exc:
                raise SessionReclaimedError(str(exc)) from exc

    def mark_stopped(self) -> None:
        """Move the session to ``stopped`` through ``SessionService.transition``.

        Never assigns ``status`` itself — AR37 admits exactly one assigner and
        it is not this module.

        The bound ``started_at`` is passed through, which arms the service's
        ownership guard on the **stop** path too (review fix, 2026-08-21):
        without it, an incumbent exiting inside the one-interval detection
        window would transition the *successor's* running row to ``stopped``.
        Every ``InvalidSessionTransition`` is translated to
        :class:`~src.core.live_session_record.SessionReclaimedError` for the
        same two reasons ``record_activity`` documents — the runner may not
        import ``src.db``, and a stop refused for *any* reason (reclaimed, or
        the row already moved out of ``running`` out of band) means the same
        thing: the row is no longer this process's to mark.

        Raises:
            RecordNotFoundError: The session's row is gone.
            SessionReclaimedError: The row is no longer this process's to stop.
        """
        with self._session_factory() as db_session:
            repository = SyncTradingSessionRepository(db_session)  # type: ignore[arg-type]
            service = SessionService(repository)
            try:
                service.transition(
                    self._session_id, to=SessionStatus.STOPPED, started_at=self._started_at
                )
            except InvalidSessionTransition as exc:
                raise SessionReclaimedError(str(exc)) from exc

    def record_strategy_failure(
        self,
        *,
        strategy_id: str,
        spec_strategy_id: str,
        error_type: str,
        handler: str,
        at: datetime,
        detail: str | None = None,
        all_failed: bool = False,
    ) -> None:
        """Append a contained strategy failure to ``runtime_flags`` (Story 2.7).

        One short-lived transaction, like its two siblings, and for the same
        reason: a 6.5-hour session must never hold a row lock. This one is
        rarer still — at most one write per strategy per process run, because
        the guard latches.

        Never assigns ``status`` itself — AR37 admits exactly one assigner and
        it is not this module. A strategy failure is not a session lifecycle
        event: the session stays ``running``.

        Every ``InvalidSessionTransition`` is translated to
        :class:`~src.core.live_session_record.SessionReclaimedError` for the two
        reasons ``record_activity`` documents — the runner may not import
        ``src.db`` (AR38), and both refusals that produce one (the row is not
        ``running``, or its ``last_started_at`` moved past this process's own)
        mean the same thing: this session is no longer ours to write to.

        ⚠️ The **caller's** policy differs here, and deliberately. For the
        heartbeat a reclaim is fatal and re-raised; for this write the
        steady-state tick folds it into the same ``_ownership_lost`` route
        rather than letting it escape a bar handler, because a raise out of a
        wrapped ``handle_*`` re-enters ``publish_c`` and dies at ``os._exit(1)``
        with no output (*Judgment call #10*). That policy lives in the caller,
        not here.

        Raises:
            RecordNotFoundError: The session's row is gone.
            SessionReclaimedError: This process no longer owns the session.
        """
        with self._session_factory() as db_session:
            repository = SyncTradingSessionRepository(db_session)  # type: ignore[arg-type]
            service = SessionService(repository, time_source=lambda: at)
            try:
                service.record_strategy_failure(
                    self._session_id,
                    started_at=self._started_at,
                    strategy_id=strategy_id,
                    spec_strategy_id=spec_strategy_id,
                    error_type=error_type,
                    handler=handler,
                    at=at,
                    detail=detail,
                    all_failed=all_failed,
                )
            except InvalidSessionTransition as exc:
                raise SessionReclaimedError(str(exc)) from exc
