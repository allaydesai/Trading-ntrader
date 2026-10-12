"""A session's withheld periods — ``runtime_flags["reconcile_withheld"]`` (Story 5.8).

Owns: the shape of the one session-lifetime document in ``runtime_flags`` — the
periods in which a running session withheld trading because its runtime
reconciliation kept failing (PO ruling PR #35 D3) — and the four ways it
changes hands: a period written by the running session (:func:`with_period`),
closed by the stop edge (:func:`closed_on_stop`), carried across the
``-> running`` edge (:func:`carried_forward`), and read for a sealed record
(:func:`withheld_for_record`). Pure: no database, no clock, no I/O; every value
JSON-native, every result a new object (SQLAlchemy does not track in-place JSONB
mutation, so the caller's reassignment is what persists).

Does not own: when a period opens or ends (``live_runtime_reconcile``), the
write path and its guards (``session_service``), or the rest of the conditions
document (``session_conditions``).

**Why it survives every restart when nothing else in ``runtime_flags`` does**
(PO ruling Q1, 2026-10-11). A session spans many process runs and seals once,
later, in another process; every other key describes *one* run and is cleared
on its ``-> running`` edge (Story 2.7). The withheld history is the one fact a
seal must carry for every run (AC #3), and ``runtime_flags`` is the only
durable, mutable place a session row has without a migration.

**Record-keeping only.** Closing a period on a stop or a restart grants and
withholds nothing: trading permission is returned only by the running session's
clean-cycle ``confirm_state_reestablished``. An end is never invented — a
period a dead process left open ends at its last heartbeat, or unknown
(``null``).
"""

import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

#: The ``runtime_flags`` key, and the ``session_conditions`` key a seal records it under.
RECONCILE_WITHHELD_KEY = "reconcile_withheld"
#: Every period's cause: the running session's reconciliation kept failing.
WITHHELD_CAUSE = "runtime_reconciliation_failing"
#: NFR2: at most this many periods are kept — the most recent; ``count`` is the total.
WITHHELD_PERIODS_KEPT = 50
#: How a period ended: a clean cycle granted trading back,
ENDED_RECONCILED = "reconciled"
#: the session was stopped while it stood,
ENDED_SESSION_STOPPED = "session_stopped"
#: or its process died and the next start found it open;
ENDED_PROCESS_LOST = "process_lost"
#: and, in a sealed record only, still open when the session was sealed.
OPEN_AT_SEAL = "open_at_seal"


def empty_document() -> dict[str, Any]:
    """A session with no withheld period — explicit, never absent."""
    return {"count": 0, "periods": []}


def _iso(stamp: datetime | None) -> str | None:
    """UTC, as every stamp in the record is (AR29): a heartbeat read back through
    psycopg2 arrives in the Postgres server's zone (PR #36 review P5)."""
    if stamp is None:
        return None
    return (stamp.astimezone(UTC) if stamp.tzinfo is not None else stamp).isoformat()


def _read(document: Any) -> tuple[int, list[dict[str, Any]]]:
    """A stored document as ``(count, periods)`` — copies, tolerantly.

    JSONB has no schema: a hand-edited or restored row may hold anything. What
    is understood is kept; nothing raises, because the writer runs on a live
    session's tick and the readers must always be able to seal.
    """
    if not isinstance(document, Mapping):
        return 0, []
    raw = document.get("periods")
    periods = (
        [copy.deepcopy(dict(p)) for p in raw if isinstance(p, Mapping)]
        if isinstance(raw, list)
        else []
    )
    count = document.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        count = 0
    return max(count, len(periods)), periods


def with_period(
    document: Any,
    *,
    withheld_from: datetime,
    withheld_until: datetime | None,
    reason: str | None,
    error_type: str,
    failed_cycles: int,
) -> dict[str, Any]:
    """The document with one period written — keyed by its start.

    A new start is appended and counted; the same start replaces its earlier
    version only while that version is still open, so a retried write and a
    write carrying both halves are the same write, and nothing ever reopens
    or relabels a period already ended — by the grant, the stop or a restart
    (fail closed: what the record says ended stays ended; code review P6).
    Past :data:`WITHHELD_PERIODS_KEPT` the oldest periods drop and ``count``
    keeps the total.
    """
    count, periods = _read(document)
    entry = {
        "start": _iso(withheld_from),
        "end": _iso(withheld_until),
        "cause": WITHHELD_CAUSE,
        "reason": reason,
        "error_type": error_type,
        "failed_cycles": failed_cycles,
        "ended_by": None if withheld_until is None else ENDED_RECONCILED,
    }
    index = next((i for i, p in enumerate(periods) if p.get("start") == entry["start"]), None)
    if index is None:
        periods.append(entry)
        count += 1
    elif _is_open(periods[index]):
        periods[index] = entry
    return {"count": count, "periods": periods[-WITHHELD_PERIODS_KEPT:]}


def _is_open(period: Mapping[str, Any]) -> bool:
    """The one definition of "open": nothing has ended it yet (code review P6).
    A ``process_lost`` period with an unknown (``null``) end is closed."""
    return period.get("ended_by") is None


def _end_for(start: Any, stamp: datetime | None) -> str | None:
    """``stamp`` as the end of the period that began at ``start`` — or unknown
    (``None``) when there is no stamp or it precedes the start (a clock step,
    or a heartbeat from before the withdrawal): an end is never invented."""
    if stamp is None or not _at_or_after(stamp, start):
        return None
    return _iso(stamp)


def _closed(document: Any, stamp: datetime | None, ended_by: str) -> Any:
    """``document`` with every open period closed — each judged against its own
    start — as a deep copy. What the edges cannot read passes through as
    stored, ``count`` included: an edge never normalises a history it cannot
    parse, so the seal can record it as stored (D-I; code review P6)."""
    closed = copy.deepcopy(document)
    periods = closed.get("periods") if isinstance(closed, Mapping) else None
    for period in periods if isinstance(periods, list) else ():
        if isinstance(period, dict) and _is_open(period):
            period["end"], period["ended_by"] = _end_for(period.get("start"), stamp), ended_by
    return closed


def closed_on_stop(flags: Any, stopped_at: datetime) -> Any:
    """``runtime_flags`` with any open period ended by the stop (D-E 2).

    Returned unchanged — the same object — when there is no history, so a stop
    that has nothing to close writes nothing new.
    """
    if not isinstance(flags, Mapping) or RECONCILE_WITHHELD_KEY not in flags:
        return flags
    closed = _closed(flags[RECONCILE_WITHHELD_KEY], stopped_at, ENDED_SESSION_STOPPED)
    return {**flags, RECONCILE_WITHHELD_KEY: closed}


def carried_forward(flags: Any, *, last_heartbeat_at: datetime | None) -> Any:
    """The history the ``-> running`` edge keeps, or ``None`` (D-D, D-E 3).

    A period still open here was left by a process that died while it stood: it
    ends at that process's last heartbeat — the last instant it is known to have
    run — or, with no heartbeat or one from before that period began, at an
    unknown end (``null``), never an invented one. A history the edge cannot
    read is carried as stored.
    """
    if not isinstance(flags, Mapping) or RECONCILE_WITHHELD_KEY not in flags:
        return None
    return _closed(flags[RECONCILE_WITHHELD_KEY], last_heartbeat_at, ENDED_PROCESS_LOST)


def _at_or_after(stamp: datetime, start: Any) -> bool:
    """Whether ``stamp`` is not before a stored ISO start (unparseable: no)."""
    try:
        return stamp >= datetime.fromisoformat(str(start))
    except (TypeError, ValueError):
        return False


def withheld_for_record(flags: Any) -> Any:
    """What a sealed record carries (D-I): the stored document, as stored.

    Never judged — a stopped session must always be sealable, so a malformed
    document is recorded, not refused (the Story 5.4 P4 precedent). Absent, it
    is :func:`empty_document`, so "none" is explicit. A period still open at the
    seal says so: ``ended_by`` is :data:`OPEN_AT_SEAL` (PO condition,
    2026-10-11).
    """
    if not isinstance(flags, Mapping) or RECONCILE_WITHHELD_KEY not in flags:
        return empty_document()
    recorded = copy.deepcopy(flags[RECONCILE_WITHHELD_KEY])
    periods = recorded.get("periods") if isinstance(recorded, Mapping) else None
    for period in periods if isinstance(periods, list) else ():
        if isinstance(period, dict) and _is_open(period):
            period["ended_by"] = OPEN_AT_SEAL
    return recorded
