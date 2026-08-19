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

# An upper bound, because ``math.isfinite`` alone is not enough: ``1e300`` is
# finite and positive, and would disable the reclaim just as completely as
# ``nan`` would — the very failure this validation exists to prevent. A day is
# far beyond any plausible cadence and still fails loudly.
MAX_HEARTBEAT_STALE_AFTER_SECONDS = 24 * 60 * 60.0


def _utc_now() -> datetime:
    """The repo's house clock idiom (31 occurrences of ``datetime.now(timezone.utc)``)."""
    return datetime.now(timezone.utc)


def _require_positive_threshold(value: float) -> float:
    """Reject a threshold that would silently disable the reclaim.

    Mirrors ``live_connection_monitor.py``'s ``_require_positive``: a ``nan``
    threshold makes every ``>`` comparison ``False``, which would disable the
    reclaim forever rather than failing loudly. A huge finite value does the
    same thing, so it is bounded too, and a non-numeric value raises the
    documented ``ValueError`` rather than a bare ``TypeError`` from ``math``.
    ``bool`` is excluded explicitly: ``True`` is an ``int`` and would otherwise
    be accepted as a one-second threshold.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"heartbeat_stale_after_seconds must be a finite positive number of seconds, "
            f"got {value!r}."
        )
    if not math.isfinite(value) or not 0 < value <= MAX_HEARTBEAT_STALE_AFTER_SECONDS:
        raise ValueError(
            f"heartbeat_stale_after_seconds must be a finite positive number of seconds "
            f"no greater than {MAX_HEARTBEAT_STALE_AFTER_SECONDS:.0f}, got {value!r}."
        )
    return float(value)


def _as_status(value: object, subject: str) -> SessionStatus:
    """Coerce a status to its enum member, or raise ``InvalidSessionTransition``.

    Two distinct holes close here. ``SessionStatus`` is a ``StrEnum``, so a bare
    ``"running"`` is *equal* to ``SessionStatus.RUNNING`` under ``in`` but is not
    the same object under ``is`` — a caller passing the raw string would slip
    past the reclaim guard's identity check entirely and then fail on ``.value``.
    And a stored status that is no member at all (the ORM's ``default=`` is
    Python-side and applies only at flush, so an unattached row has
    ``status is None``) would raise ``AttributeError`` from the refusal message
    rather than the documented exception.

    Args:
        value: The status to coerce — an enum member, or its ``str`` label.
        subject: What ``value`` is, for the error message.

    Returns:
        The corresponding ``SessionStatus`` member.

    Raises:
        InvalidSessionTransition: If ``value`` is not a valid session status.
    """
    if isinstance(value, SessionStatus):
        return value
    if isinstance(value, str):
        try:
            return SessionStatus(value)
        except ValueError:
            pass
    raise InvalidSessionTransition(f"{subject} is not a valid session status: {value!r}.")


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

    The clamp absorbs clock skew in **one** direction only: a heartbeat that
    reads as being in the future reads as *fresh* (age 0), never as a negative
    number some other comparison could mistake for stale. It does nothing about
    the opposite and more dangerous direction — a reader whose clock runs ahead
    of the writer's inflates the age and can reclaim a genuinely live session.
    Both timestamps come from application clocks on possibly different hosts;
    only a database-side ``now()`` would remove that, which this module
    deliberately does not reach for.
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
    """AC #4/#5: decide the one sanctioned ``running -> running`` path.

    A stale (or absent) heartbeat is allowed through as a start; a fresh one
    refuses, naming the session and the heartbeat's age so that another live
    process is reported rather than silently overlapped (NFR6). This function
    only *decides* and logs — the caller performs the mutation.

    Both log events are emitted at decision time, which is necessarily before
    the caller commits. A reclaim that is later rolled back therefore still
    leaves a ``session.reclaimed`` line; treat these events as attempts that
    passed validation, not as proof of a durable state change.
    """
    last_heartbeat_at = trading_session.last_heartbeat_at
    age = _heartbeat_age_seconds(last_heartbeat_at, now)
    if not _is_heartbeat_stale(last_heartbeat_at, now, threshold):
        # A second process trying to take over a live session is exactly the
        # signal an operator needs, and raising alone drops it entirely if the
        # caller swallows the exception. Hence warning, not info.
        logger.warning(
            "session.reclaim_refused",
            session_id=str(trading_session.session_id),
            name=trading_session.name,
            heartbeat_age_seconds=age,
            stale_after_seconds=threshold,
        )
        raise InvalidSessionTransition(
            f"Session {trading_session.name!r} is already running and its heartbeat is "
            f"{age:.0f}s old (stale after {threshold:.0f}s) — another process appears live."
        )
    logger.info(
        "session.reclaimed",
        session_id=str(trading_session.session_id),
        name=trading_session.name,
        heartbeat_age_seconds=age,
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
        except (ValueError, TypeError, AttributeError):
            # UUID() raises TypeError for None and bytes, and AttributeError for
            # an int or an already-parsed UUID — all of which must fall through
            # to the name lookup and end at the documented RecordNotFoundError.
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

        Validates before mutating, so no column is changed on any raised
        exception. Note this is a guarantee about *column values only*: the
        locked read has already taken an exclusive row lock, which is held
        until the caller ends its transaction either way.

        Args:
            session_id: The session's UUID business key.
            to: The requested target status.

        Returns:
            The same ``TradingSession``, mutated in place.

        Raises:
            RecordNotFoundError: If no session matches ``session_id``.
            InvalidSessionTransition: If the edge is illegal, if either status
                is not a valid ``SessionStatus``, or if ``to`` is ``running``
                while already ``running`` with a fresh heartbeat.
        """
        to = _as_status(to, "The requested target")
        trading_session = self._repository.find_by_session_id(session_id, for_update=True)
        if trading_session is None:
            raise RecordNotFoundError(f"No trading session found with id {session_id}")

        now = self._time_source()
        current = _as_status(
            trading_session.status, f"The stored status of session {trading_session.name!r}"
        )

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
