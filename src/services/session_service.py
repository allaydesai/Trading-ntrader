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
from src.models.session import (  # noqa: F401  # re-export: Story 2.3's tests and callers
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,  # import it from here
    SessionStatus,
)

logger = structlog.get_logger(__name__)

# AR32's write cadence ("~every 30s") now lives in `src/models/session.py`, the
# one session module that imports no framework, because Story 2.5's runner may
# not import SQLAlchemy (AR38) and so cannot reach this module. Re-exported
# above so the callers and tests Story 2.3 wrote are unaffected by the move —
# two constants that can silently drift is the exact failure declaring one
# constant was meant to prevent.

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


def _load_or_raise(
    repository: SyncTradingSessionRepository, session_id: UUID, *, for_update: bool = False
) -> TradingSession:
    """Read one row by its business key, or raise the documented failure.

    ``for_update`` is the caller's decision and not a default anyone should
    change casually. ``transition()`` needs the lock because it *decides*
    across a read-then-write window another process may enter (Story 2.3
    AC #6); ``record_strategy_failure()`` needs it because it read-modify-writes
    a whole document (review fix, 2026-08-23); ``record_activity()`` must not
    take it, because a heartbeat every 30 seconds that held an exclusive row
    lock would block every ``live status`` for the life of the session.
    """
    trading_session = repository.find_by_session_id(session_id, for_update=for_update)
    if trading_session is None:
        raise RecordNotFoundError(f"No trading session found with id {session_id}")
    return trading_session


def _refuse_if_reclaimed(trading_session: TradingSession, *, started_at: datetime) -> None:
    """Refuse when the row's ``last_started_at`` moved past the caller's own.

    There is no fencing token on this table (an owner/epoch column was
    considered and rejected: the phase's single migration is spent), but every
    ``-> running`` transition stamps ``last_started_at``, so a value newer than
    the one the caller's own transition wrote means a *second* process has
    taken this session. Shared by the heartbeat (``_stamp_activity``) and the
    stop path (``_apply_transition``): without the second, a dispossessed
    incumbent's teardown would transition the **successor's** session to
    ``stopped`` (review finding, 2026-08-21).

    Known, accepted limit: the comparison trusts two *application* clocks
    stamped by different processes. Skew between hosts that exceeds the real
    gap between the two claims makes the reclaim undetectable — the staleness
    math above clamps skew in one direction, but nothing can clamp it here
    without the fencing column this phase cannot add. Stated, not solved.

    Raises:
        InvalidSessionTransition: The row was reclaimed by another process.
    """
    last_started_at = trading_session.last_started_at
    if last_started_at is not None and last_started_at > started_at:
        logger.error(
            "session.activity_refused",
            session_id=str(trading_session.session_id),
            name=trading_session.name,
            own_started_at=started_at.isoformat(),
            row_started_at=last_started_at.isoformat(),
        )
        raise InvalidSessionTransition(
            f"Session {trading_session.name!r} was reclaimed by another process: the row "
            f"started at {last_started_at.isoformat()}, after this process's own start at "
            f"{started_at.isoformat()}. This process no longer owns the session."
        )


def _apply_transition(
    trading_session: TradingSession,
    *,
    to: SessionStatus,
    now: datetime,
    stale_after_seconds: float,
    started_at: datetime | None = None,
) -> TradingSession:
    """Validate the requested edge and, only then, move the row (AR37).

    Lives at module scope rather than inside :class:`SessionService` for two
    reasons, in that order. The load-bearing one is that the *whole* of AR37 is
    "one module may assign ``TradingSession.status``" — the guards that enforce
    it (an AST scan and a scoped grep, both in
    ``tests/unit/services/test_session_service.py``) are per-file, so this
    function is exactly as compliant here as it was as a method, and moving it
    changes nothing an operator or a reviewer can observe. The second is
    CLAUDE.md's 100-line class limit: ``SessionService`` was at 97 of it before
    Story 2.5 added ``record_activity``, and this is the same split
    ``_reclaim_or_refuse`` above already models.

    Validates before mutating, so no column changes on any raised exception.
    That is a guarantee about *column values only*: the caller's locked read has
    already taken an exclusive row lock, held until the caller's transaction
    ends either way.

    ``started_at``, when given, is the instant the caller's own ``-> running``
    transition stamped, and arms :func:`_refuse_if_reclaimed` **before** the
    edge is even considered: a caller that has lost the session must hear
    "reclaimed", not "cannot move from stopped to stopped", because the remedy
    is different (walk away versus investigate). The stop path passes it; the
    claim path does not, because a claim is allowed to take a stale session.

    Raises:
        InvalidSessionTransition: The edge is illegal, either status is not a
            valid ``SessionStatus``, ``to`` is ``running`` while the row is
            already ``running`` with a fresh heartbeat, or ``started_at`` is
            given and the row was reclaimed by another process.
    """
    if started_at is not None:
        _refuse_if_reclaimed(trading_session, started_at=started_at)
    to = _as_status(to, "The requested target")
    current = _as_status(
        trading_session.status, f"The stored status of session {trading_session.name!r}"
    )

    if to is SessionStatus.RUNNING and current is SessionStatus.RUNNING:
        _reclaim_or_refuse(trading_session, now, stale_after_seconds)
    elif to not in _LEGAL_TRANSITIONS.get(current, frozenset()):
        raise InvalidSessionTransition(
            f"Session {trading_session.name!r} cannot move from {current.value!r} to {to.value!r}."
        )

    trading_session.status = to
    for column in _TIMESTAMPS_BY_TARGET.get(to, ()):
        setattr(trading_session, column, now)
    if to is SessionStatus.RUNNING:
        # Story 2.7 (AC #4). Every `-> running` edge starts a new process run,
        # and `runtime_flags` records facts about *one* run. Without this clear,
        # `live status` reports a strategy that failed three runs ago against a
        # session that is currently healthy — a stale fact presented as a live
        # one. Deliberately only this edge: a stop must leave the failures
        # readable, or an operator investigating why a session stopped trading
        # loses the evidence at the moment they need it.
        trading_session.runtime_flags = None
    return trading_session


def _stamp_activity(
    trading_session: TradingSession,
    *,
    started_at: datetime,
    at: datetime,
    bar_seen_at: datetime | None,
) -> TradingSession:
    """Write AR32's liveness columns, or refuse — the heartbeat's whole contract.

    Two guards, and both of them exist because getting either wrong puts two
    processes on one broker account, which is the catastrophic failure NFR6
    exists to prevent.

    1. **The row must be ``running``.** A heartbeat for a ``stopped`` or
       ``sealed`` session would refresh the very liveness signal the AR33
       reclaim reads, making an abandoned row permanently un-reclaimable — the
       stuck state NFR11 forbids, manufactured by the mechanism meant to
       prevent it.
    2. **The row's ``last_started_at`` must not have moved past the caller's
       own.** There is no fencing token on this table (an owner/epoch column
       was considered and rejected: the phase's single migration is spent), but
       every ``-> running`` transition stamps ``last_started_at``, so a value
       newer than the one the caller's own transition wrote means a *second*
       process has taken this session. Without this the incumbent would keep
       refreshing a row it no longer owns and, on the way out, transition the
       **successor's** session to ``stopped``.

    ``last_bar_at`` is written **only** when ``bar_seen_at`` is given, and is
    never cleared: a quiet interval is not evidence that the last bar never
    happened. The value is the instant the bar was *observed*, not ``now`` —
    the write is batched onto the heartbeat tick but the timestamp is not.

    Known, accepted limit: guard 2 is a *detection*, not a cure. The window
    between another process reclaiming this session and this process's next
    tick is up to one heartbeat interval of two live processes. Closing it
    needs the fencing column this phase cannot add.

    Args:
        trading_session: The row to stamp, already loaded (unlocked).
        started_at: The instant this caller's own ``-> running`` transition
            stamped. Bound into the adapter at construction, so a runner cannot
            forge its own claim to ownership.
        at: The heartbeat instant to write. Resolved by the caller, so the
            runner's injected clock is what lands in the column.
        bar_seen_at: When a bar was last observed since the previous tick, or
            ``None``.

    Returns:
        The same ``TradingSession``, mutated in place.

    Raises:
        InvalidSessionTransition: Either guard refused.
    """
    current = _as_status(
        trading_session.status, f"The stored status of session {trading_session.name!r}"
    )
    if current is not SessionStatus.RUNNING:
        raise InvalidSessionTransition(
            f"Session {trading_session.name!r} is {current.value!r}, not running, so its "
            "activity cannot be recorded — a heartbeat for a session that is not running "
            "would make the row un-reclaimable."
        )

    _refuse_if_reclaimed(trading_session, started_at=started_at)

    trading_session.last_heartbeat_at = at
    if bar_seen_at is not None:
        trading_session.last_bar_at = bar_seen_at
    return trading_session


#: The ``runtime_flags`` document's schema version. Bumped only when a key's
#: *meaning* changes; Story 2.8 adding ``connection_lost_at`` is an addition,
#: not a bump.
RUNTIME_FLAGS_VERSION = 1


def _record_strategy_failure(
    trading_session: TradingSession,
    *,
    started_at: datetime,
    strategy_id: str,
    spec_strategy_id: str,
    error_type: str,
    handler: str,
    at: datetime,
    detail: str | None = None,
    all_failed: bool = False,
) -> TradingSession:
    """Append one contained strategy failure to ``runtime_flags`` (Story 2.7).

    At module scope for the same two reasons :func:`_apply_transition` is: AR37
    is enforced **per file**, so this is exactly as compliant here as it would
    be as a method, and ``SessionService`` is held to CLAUDE.md's 100-line class
    limit. (It had 17 lines of headroom, measured by AST — enough for a plain
    method — but the module-level body plus a thin delegator is the shape the
    other two writes already use, and matching them beats saving a level of
    indirection.)

    **Never assigns ``status``.** A session whose strategy failed is still
    ``running`` — that is AC #1's letter, and Judgment call #7's named
    trade-off. AR37's AST guard matches only ``t.attr == "status"``, so this
    write is invisible to it, which is correct rather than a loophole.

    Two guards, the same two the heartbeat has and for the same reason: writing
    a failure into a row this process no longer owns puts a stale fact in a
    successor's record, and a row that is not ``running`` has no current run to
    record anything about.

    **The row is loaded ``FOR UPDATE``** (review fix, 2026-08-23), unlike the
    heartbeat's: this is a read-modify-write of the whole ``runtime_flags``
    document, so an unlocked read lets a dispossessed incumbent rebuild the doc
    from a pre-reclaim snapshot — stamping stale failures into the successor's
    entire run and erasing entries the successor already recorded. The lock
    serialises the reclaim guard with the ``-> running`` transition that clears
    the column. The heartbeat's no-lock rationale does not carry over: that
    write is two scalar stamps every 30 seconds; this one is rare, because the
    guard latches per strategy.

    ⚠️ **The document is rebuilt and reassigned, never mutated in place.**
    SQLAlchemy does not track in-place mutation of a plain ``JSONB`` column, so
    ``trading_session.runtime_flags["failed_strategies"].append(...)`` silently
    never persists — the write appears to succeed, the test passes against the
    in-memory object, and the column never changes. Both the outer dict and the
    inner list are new objects.

    Args:
        trading_session: The row to append to, already loaded ``FOR UPDATE`` by
            the caller — see :meth:`SessionService.record_strategy_failure` for
            why this write locks where the heartbeat does not.
        started_at: This process's own ``-> running`` instant, for the ownership
            guard.
        strategy_id: The Nautilus id, or ``""`` for a strategy that never
            started.
        spec_strategy_id: The spec's own id — what the operator wrote.
        error_type: The exception's class name. Never the exception.
        handler: ``"handle_bar"``, ``"handle_event"`` or ``"start"``.
        at: When the failure was contained.
        detail: One **already-redacted** line of the message (NFR26 is applied
            at the catch site, in ``live_strategy_guard``, because that is where
            the raw text exists). No traceback: that belongs in the log sink.
        all_failed: Whether every strategy in the session has now failed. Never
            downgraded — once true it stays true, because a later write must not
            report a dead session as partially alive again.

    Returns:
        The same ``TradingSession``, mutated in place.

    Raises:
        InvalidSessionTransition: Either guard refused.
    """
    current = _as_status(
        trading_session.status, f"The stored status of session {trading_session.name!r}"
    )
    if current is not SessionStatus.RUNNING:
        raise InvalidSessionTransition(
            f"Session {trading_session.name!r} is {current.value!r}, not running, so a strategy "
            "failure cannot be recorded against it — the run it would describe is over."
        )

    _refuse_if_reclaimed(trading_session, started_at=started_at)

    existing = trading_session.runtime_flags or {}
    entries = list(existing.get("failed_strategies", ()))
    entries.append(
        {
            "strategy_id": strategy_id,
            "spec_strategy_id": spec_strategy_id,
            "error_type": error_type,
            "handler": handler,
            "at": at.isoformat(),
            "detail": detail or "",
        }
    )
    # `**existing` first (review fix, 2026-08-23): keys this function does not
    # know about — Story 2.8's `connection_lost_at` is already planned — must
    # survive the rebuild, or "append, never replace" holds only for the keys
    # named below.
    trading_session.runtime_flags = {
        **existing,
        "v": RUNTIME_FLAGS_VERSION,
        "all_failed": bool(existing.get("all_failed", False)) or all_failed,
        "failed_strategies": entries,
    }
    return trading_session


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

    def transition(
        self, session_id: UUID, *, to: SessionStatus, started_at: datetime | None = None
    ) -> TradingSession:
        """Move a session to ``to``, or raise; the rules are in :func:`_apply_transition`."""
        trading_session = _load_or_raise(self._repository, session_id, for_update=True)
        return _apply_transition(
            trading_session,
            to=to,
            now=self._time_source(),
            stale_after_seconds=self._heartbeat_stale_after_seconds,
            started_at=started_at,
        )

    def record_activity(
        self,
        session_id: UUID,
        *,
        started_at: datetime,
        at: datetime | None = None,
        bar_seen_at: datetime | None = None,
    ) -> TradingSession:
        """Stamp AR32's liveness columns; the rules are in :func:`_stamp_activity`."""
        trading_session = _load_or_raise(self._repository, session_id)
        return _stamp_activity(
            trading_session,
            started_at=started_at,
            at=self._time_source() if at is None else at,
            bar_seen_at=bar_seen_at,
        )

    def record_strategy_failure(
        self, session_id: UUID, *, started_at: datetime, **failure: object
    ) -> TradingSession:
        """Append a contained strategy failure; rules in :func:`_record_strategy_failure`.

        Loads **``FOR UPDATE``**, unlike the heartbeat — the why lives with
        the rules, in :func:`_record_strategy_failure`.
        """
        trading_session = _load_or_raise(self._repository, session_id, for_update=True)
        return _record_strategy_failure(
            trading_session,
            started_at=started_at,
            **failure,  # type: ignore[arg-type]
        )
