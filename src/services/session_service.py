"""The single validated path through which a trading session changes state.

Owns: ``SessionService.transition()`` — the only place in this codebase that
may assign ``TradingSession.status`` (AR37) — and ``SessionService.resolve()``,
the name-or-UUID lookup every future command shares (AR36).

Does not own: ``SessionSpec`` construction (Story 2.1), the initial ``CREATED``
row (``live create``, Story 2.2 — creation is not a transition), the heartbeat
*writer* cadence (Story 2.5, AR32 — this module only reads the heartbeat and
stamps it once, on entering ``running``), any CLI command, and
``sealed_run_id`` (Story 5.3).

Deliberately framework-free: standard library, this repo's own repository/
exception/model types, and ``structlog`` — no live-trading engine import at
all (AC #7). That purity is also why this service is sync-only and never
ends or unwinds the transaction itself: the caller's ``get_sync_session()``
context manager owns that, and the row lock AC #6's reclaim needs depends on
the transaction staying open until the caller's own clean exit.
"""

import math
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from uuid import UUID

import structlog

from src.db.exceptions import InvalidSessionTransition, RecordNotFoundError
from src.db.models.trading_session import TradingSession
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.session import SessionStatus

logger = structlog.get_logger(__name__)

# AR32's write cadence ("~every 30s"), declared here so Story 2.5's runner and
# Story 2.8's `stale` health derivation import one constant instead of each
# inventing their own literal.
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0

# Three missed heartbeats. The costs of getting this wrong are asymmetric:
# too short and a live session gets reclaimed, putting two processes on one
# broker account (NFR6, catastrophic); too long and an operator waits longer
# after a SIGKILL (merely annoying) — so the number is generous by design.
DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS = 3 * DEFAULT_HEARTBEAT_INTERVAL_SECONDS


def _utc_now() -> datetime:
    """The repo's house clock idiom (31 occurrences of ``datetime.now(timezone.utc)``)."""
    return datetime.now(timezone.utc)


def _require_positive_threshold(value: float) -> float:
    """Reject a non-finite or non-positive threshold at construction time.

    Mirrors ``live_connection_monitor.py``'s ``_require_positive``: a ``nan``
    threshold makes every ``>`` comparison ``False``, which would silently
    disable the reclaim forever rather than failing loudly.
    """
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"heartbeat_stale_after_seconds must be a finite positive number of seconds, "
            f"got {value!r}."
        )
    return value


#: AC #2's four legal edges. ``RUNNING -> RUNNING`` is deliberately absent:
#: the reclaim is a named branch in ``transition()``, checked before this
#: table, so the table stays a literal transcription of AC #2 and the one
#: sanctioned self-edge is a single greppable block rather than a table entry
#: that quietly legalises every self-edge.
_LEGAL_TRANSITIONS: Mapping[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.CREATED: frozenset({SessionStatus.RUNNING}),
    SessionStatus.RUNNING: frozenset({SessionStatus.STOPPED}),
    SessionStatus.STOPPED: frozenset({SessionStatus.RUNNING, SessionStatus.SEALED}),
    SessionStatus.SEALED: frozenset(),  # terminal, AC #3
}

#: AC #2's "the corresponding timestamp column" — the epic never maps edges to
#: columns, so this is that map. ``last_bar_at`` is never touched here; it
#: belongs to Story 2.5's bar path. ``sealed_run_id`` is Story 5.3's, not this
#: story's, to write.
_TIMESTAMPS_BY_TARGET: Mapping[SessionStatus, tuple[str, ...]] = {
    SessionStatus.RUNNING: ("last_started_at", "last_heartbeat_at"),
    SessionStatus.STOPPED: ("last_stopped_at",),
    SessionStatus.SEALED: ("sealed_at",),
}


def _heartbeat_age_seconds(last_heartbeat_at: datetime | None, now: datetime) -> float | None:
    """Seconds since the last heartbeat, clamped at zero, or None if never set.

    Clamping absorbs clock skew between the process that wrote the heartbeat
    and this one: a heartbeat that reads as being in the future must read as
    *fresh* (age 0), never as a negative number that some other comparison
    could mistake for stale.
    """
    if last_heartbeat_at is None:
        return None
    return max(0.0, (now - last_heartbeat_at).total_seconds())


def _is_heartbeat_stale(
    last_heartbeat_at: datetime | None, now: datetime, threshold: float
) -> bool:
    """AC #4: a ``NULL`` heartbeat counts as stale, not as fresh.

    Matches ``ConnectionMonitor.observation_is_stale``'s fail-safe direction
    (an absent reading is stale) — the opposite reading would make a
    ``running`` row with no heartbeat un-reclaimable forever, exactly the
    stuck state NFR11 forbids. Strictly ``>``, not ``>=``: "exactly at the
    threshold" reads as fresh, matching ``live_connection_monitor.py``.
    """
    age = _heartbeat_age_seconds(last_heartbeat_at, now)
    return age is None or age > threshold


def _reclaim_or_refuse(trading_session: TradingSession, now: datetime, threshold: float) -> None:
    """AC #4/#5: the one sanctioned ``running -> running`` path.

    A stale (or absent) heartbeat reclaims the session as a start; a fresh
    one refuses, naming the session and the heartbeat's age so another
    process appears live rather than silently overlapping it (NFR6).
    """
    last_heartbeat_at = trading_session.last_heartbeat_at
    if not _is_heartbeat_stale(last_heartbeat_at, now, threshold):
        age = _heartbeat_age_seconds(last_heartbeat_at, now)
        raise InvalidSessionTransition(
            f"Session {trading_session.name!r} is already running and its heartbeat is "
            f"{age:.0f}s old (stale after {threshold:.0f}s) — another process appears live."
        )
    logger.info(
        "session.reclaimed",
        session_id=str(trading_session.session_id),
        name=trading_session.name,
        heartbeat_age_seconds=_heartbeat_age_seconds(last_heartbeat_at, now),
    )


class SessionService:
    """The single validated path through which a session's status changes.

    Args:
        repository: The sync trading-session repository this service reads
            and mutates rows through.
        heartbeat_stale_after_seconds: How old ``last_heartbeat_at`` may be
            before a ``running -> running`` request is treated as a reclaim
            of an abandoned session rather than a refusal (AR33, NFR11).
        time_source: Returns the current aware ``datetime``. Injected so
            tests control it exactly.
    """

    def __init__(
        self,
        repository: SyncTradingSessionRepository,
        *,
        heartbeat_stale_after_seconds: float = DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS,
        time_source: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._heartbeat_stale_after_seconds = _require_positive_threshold(
            heartbeat_stale_after_seconds
        )
        self._repository = repository
        self._time_source = time_source

    def resolve(self, identifier: str) -> TradingSession:
        """Turn a name or a UUID string into its row (AR36).

        Args:
            identifier: Either a session's ``name`` or its ``session_id``.

        Returns:
            The matching ``TradingSession``.

        Raises:
            RecordNotFoundError: If neither form matches any row.
        """
        try:
            session_id = UUID(identifier)
        except ValueError:
            session_id = None
        if session_id is not None:
            found = self._repository.find_by_session_id(session_id)
            if found is not None:
                return found
        found = self._repository.find_by_name(identifier)
        if found is None:
            raise RecordNotFoundError(f"No trading session matches {identifier!r}")
        return found

    def transition(self, session_id: UUID, *, to: SessionStatus) -> TradingSession:
        """Move a session to ``to``, or raise — the only path that may (AR37).

        Validates before mutating, so a refusal never depends on the caller
        rolling back: the row is unchanged on any raised exception.

        Args:
            session_id: The session's UUID business key.
            to: The requested target status.

        Returns:
            The same ``TradingSession``, mutated in place.

        Raises:
            RecordNotFoundError: If no session matches ``session_id``.
            InvalidSessionTransition: If the edge is illegal, or ``to`` is
                ``running`` while already ``running`` with a fresh heartbeat.
        """
        trading_session = self._repository.find_by_session_id(session_id, for_update=True)
        if trading_session is None:
            raise RecordNotFoundError(f"No trading session found with id {session_id}")

        now = self._time_source()
        current = trading_session.status

        if to is SessionStatus.RUNNING and current is SessionStatus.RUNNING:
            _reclaim_or_refuse(trading_session, now, self._heartbeat_stale_after_seconds)
        elif to not in _LEGAL_TRANSITIONS.get(current, frozenset()):
            raise InvalidSessionTransition(
                f"Session {trading_session.name!r} cannot move from {current.value!r} to "
                f"{to.value!r}."
            )

        trading_session.status = to
        for column in _TIMESTAMPS_BY_TARGET.get(to, ()):
            setattr(trading_session, column, now)
        return trading_session
