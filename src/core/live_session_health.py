"""Derives a session's operator-facing health from primitives (Story 2.8, AR32).

Owns: the five-value :class:`SessionHealth` vocabulary, the precedence that
decides between them, the :class:`StatusReport` shape ``live status``/``live
list`` render, and the pure builders/renderers for both the human and
``--json`` output — the ``render_report`` split ``src/core/live_check.py``
already models (renderer in core, printing in the CLI).

Does not own: reading the database (``src/cli/commands/live_status.py``), the
``status`` column itself (``SessionService.transition()``, AR37 — this module
never assigns it), or a writer for ``runtime_flags`` (Story 2.7's
``SessionService._record_strategy_failure``; this module only reads it).

Deliberately framework-free: no SQLAlchemy, no Nautilus, no ``src.services``
import — every argument is a primitive, so the whole module survives
``TestImportPurity`` and needs no database to test (AC #9). The one non-stdlib
import is :class:`~src.models.session.SessionStatus`, which is itself
framework-free by design.

Health is always **derived at query time and never stored**: no new column,
no migration, and this module contains no ``status =`` assignment anywhere —
the same read-only posture ``SessionService.resolve()`` already has.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from src.models.session import SessionStatus


#: AC #2's five outcomes. ``StrEnum`` for the same reason ``SessionStatus``
#: is one: ``str()``, f-strings and JSON encoding all render the bare value
#: (``"degraded"``), which is what a ``--json`` payload needs with no
#: translation step.
class SessionHealth(StrEnum):
    STOPPED = "stopped"
    STALE = "stale"
    DEGRADED = "degraded"
    TRADING = "trading"
    IDLE = "idle"


#: AC #2 step 4/5's ``trading``/``idle`` boundary. Judgment call #2: no number
#: for this split exists anywhere in the PRD, architecture or epics. Pinned
#: equal to the no-bars watchdog's threshold
#: (``live_session_steady_state.DEFAULT_NO_BARS_AFTER_SECONDS``) by a drift
#: test in ``tests/unit/core/test_live_session_health.py`` rather than an
#: import — this module must not reach into a node-facing module — because
#: both answer the same question ("how long without a bar is worth
#: remarking on"), and a future correction to one should fix both.
DEFAULT_BAR_FRESH_AFTER_SECONDS = 300.0


def _as_utc(at: datetime | None) -> datetime | None:
    """Normalise a timestamp to tz-aware UTC, or pass ``None`` through.

    Every column this module reads is ``TIMESTAMP(timezone=True)``, so the
    driver already returns aware datetimes — but nothing in the type system
    says so, and two failure modes follow from assuming it. A naive value
    makes ``now - at`` raise ``TypeError`` and ``max()`` raise across a mixed
    list; an aware-but-not-UTC value makes ``isoformat()`` emit ``-04:00``,
    breaking AR29's "ISO-8601 UTC timestamps" contract silently.

    Naive input is read as UTC rather than rejected: this is a read-only
    reporting path, and refusing to report on a row is worse than reporting
    it under the same assumption the writer made.
    """
    if at is None:
        return None
    if at.tzinfo is None:
        return at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc)


def _age_seconds(at: datetime | None, *, now: datetime) -> float | None:
    """Seconds since ``at``, clamped to zero, or ``None`` if ``at`` is unset.

    Mirrors ``session_service._heartbeat_age_seconds`` exactly (same clamp,
    same reasoning), duplicated rather than imported: this module may not
    import ``src.services`` (AC #9, AR38's discipline extended to the reader).
    A future-dated timestamp reads as fresh (age 0), never as a negative
    number some comparison could mistake for stale.
    """
    if at is None:
        return None
    return max(0.0, (now - at).total_seconds())


def _flags_mapping(runtime_flags: object) -> Mapping[str, object]:
    """The ``runtime_flags`` document, or an empty mapping if it is not one.

    ``runtime_flags`` is unconstrained ``JSONB`` with no server-side schema:
    a hand-edited row, a restored backup or a future writer can put a list,
    a string or a number where the single current writer puts an object.
    Tolerance here is what keeps the one command whose job is to *explain* a
    session from being the one command that cannot report on it.
    """
    return runtime_flags if isinstance(runtime_flags, Mapping) else {}


def _failed_strategy_entries(runtime_flags: object) -> tuple[Mapping[str, object], ...]:
    """``failed_strategies``, keeping only the entries that are objects.

    The writer always produces a list of 6-key dicts, so anything else is
    malformed input rather than a shape this codebase creates. Dropping the
    non-objects renders what is understood and ignores the rest — the same
    rule :func:`_flags_mapping` applies one level up — instead of raising
    ``AttributeError`` out of the renderer, which runs outside the CLI's
    exit-code guard.
    """
    raw = _flags_mapping(runtime_flags).get("failed_strategies")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(entry for entry in raw if isinstance(entry, Mapping))


def _is_degraded(runtime_flags: Mapping[str, object] | None) -> bool:
    """AC #4: both AR32 senses of ``degraded``, from one derivation.

    Sense (a) — contained strategies — has a live writer today
    (``failed_strategies``/``all_failed``, Story 2.7). Sense (b) — connection
    lost — is a **dormant** reader on the pre-planned ``connection_lost_at``
    key: no writer exists yet, and none may be added here (Epic 4 owns
    ``ConnectionMonitor``, which stays unread by this story).

    Tolerant by construction: only the three known keys are ever read, so an
    unknown key, an unexpected ``v``, or a document that is not an object at
    all never raises — the document is rendered for what is understood, per
    Story 2.7's own versioning rule that ``v`` bumps only on a *meaning*
    change, not on an addition.
    """
    flags = _flags_mapping(runtime_flags)
    if not flags:
        return False
    if flags.get("all_failed"):
        return True
    if flags.get("failed_strategies"):
        return True
    return flags.get("connection_lost_at") is not None


def derive_health(
    status: SessionStatus,
    last_heartbeat_at: datetime | None,
    last_bar_at: datetime | None,
    runtime_flags: Mapping[str, object] | None,
    *,
    now: datetime,
    heartbeat_stale_after_seconds: float,
    bar_fresh_after_seconds: float = DEFAULT_BAR_FRESH_AFTER_SECONDS,
) -> SessionHealth:
    """AC #2: five values, a fixed precedence, evaluated fresh every call.

    Precedence, in order — each step's condition is authoritative only when
    every step above it did not already return:

    1. ``stopped`` — ``status`` is not ``running`` (covers ``created``,
       ``stopped`` and ``sealed`` alike).
    2. ``stale`` — the heartbeat is ``None`` or its age exceeds
       ``heartbeat_stale_after_seconds`` **strictly** (``>``, not ``>=`` —
       exactly-at-threshold reads fresh, matching
       ``session_service._is_heartbeat_stale``).
    3. ``degraded`` — ``runtime_flags`` records either AR32 impairment (see
       :func:`_is_degraded`). This outranks bar recency deliberately
       (Judgment call #8): a session whose every strategy died can still
       show a fresh heartbeat and a recent bar, because the runner's own
       ``note_bar`` subscribes before any strategy — the false-green trap
       Story 2.7 documented.
    4. ``trading`` — ``last_bar_at`` is non-``None`` and its age is at most
       ``bar_fresh_after_seconds`` (``<=``: exactly-at-threshold still
       counts).
    5. ``idle`` — none of the above; a healthy session that has not traded.

    Args:
        status: The row's lifecycle state.
        last_heartbeat_at: Most recent liveness heartbeat, or ``None``.
        last_bar_at: Most recent bar the session observed, or ``None``.
        runtime_flags: Story 2.7's versioned impairment document, or ``None``.
        now: The instant to derive health as of. Injected — no wall clock is
            read here, matching this repo's ``time_source`` idiom.
        heartbeat_stale_after_seconds: The staleness threshold. Callers pass
            ``session_service.DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS`` — this
            module declares no threshold literal of its own (Dev Notes, "the
            import-direction problem").
        bar_fresh_after_seconds: The ``trading``/``idle`` boundary.

    Returns:
        Exactly one :class:`SessionHealth` member.
    """
    # ``!=``, not ``is not``: ``SessionStatus`` is a ``StrEnum``, so a raw
    # ``"running"`` string compares equal to the member but is a different
    # object. An identity test would report ``health: stopped`` beside
    # ``state: running`` — the same hazard ``session_service._as_status``
    # exists to close.
    if status != SessionStatus.RUNNING:
        return SessionHealth.STOPPED

    heartbeat_age = _age_seconds(last_heartbeat_at, now=now)
    if heartbeat_age is None or heartbeat_age > heartbeat_stale_after_seconds:
        return SessionHealth.STALE

    if _is_degraded(runtime_flags):
        return SessionHealth.DEGRADED

    bar_age = _age_seconds(last_bar_at, now=now)
    if bar_age is not None and bar_age <= bar_fresh_after_seconds:
        return SessionHealth.TRADING

    return SessionHealth.IDLE


def _last_activity_at(
    *,
    last_heartbeat_at: datetime | None,
    last_bar_at: datetime | None,
    last_started_at: datetime | None,
    last_stopped_at: datetime | None,
    sealed_at: datetime | None,
) -> datetime | None:
    """Judgment call #5: the greatest non-``None`` of the five activity columns.

    The AC names "last activity" without defining it; this is the definition,
    answerable from the single row this story already reads, and it degrades
    to ``None`` for a session that has never started.
    """
    timestamps = [
        value
        for value in (last_heartbeat_at, last_bar_at, last_started_at, last_stopped_at, sealed_at)
        if value is not None
    ]
    return max(timestamps) if timestamps else None


@dataclass(frozen=True)
class StatusReport:
    """Everything ``live status``/``live list`` render — primitives only (AC #1).

    Every collection field is a ``tuple``, not a ``list``: the dataclass is
    frozen, and a mutable member would make that a lie.

    Attributes:
        session_id: The business-key UUID, as a string.
        name: The operator-chosen handle.
        status: The bare ``SessionStatus`` value (``"running"``, not the
            derived health).
        health: The bare :class:`SessionHealth` value.
        closed_trade_count: Rows with a non-``None`` ``exit_timestamp``.
        open_positions: Rows with a ``None`` ``exit_timestamp``.
        last_activity_at: The greatest non-``None`` activity timestamp, or
            ``None`` for a never-started session.
        heartbeat_age_seconds: Seconds since the last heartbeat, for the
            human-readable "Xs ago" line — not part of the ``--json`` payload.
        last_started_at: Carried for display alongside the heartbeat age, so
            an operator can notice a suspiciously old start on an otherwise
            fresh-looking row (Judgment call #3 — the fencing-column stand-in
            this story can offer without owning a ``SessionRecordPort`` write).
        trader_id: Rendered in human output only (AR29's ``--json`` key set
            is exact and does not include it).
        failed_strategies: Story 2.7's ``runtime_flags["failed_strategies"]``,
            carried verbatim for the renderer.
        all_failed: Story 2.7's ``runtime_flags["all_failed"]``.
        connection_lost_at: AR32's connection-lost sense, read from the
            pre-planned ``runtime_flags`` key. **Dormant** — no writer exists
            (Epic 4 owns ``ConnectionMonitor``); carried so that a degraded
            session always renders a cause, whichever sense caused it.
    """

    session_id: str
    name: str
    status: str
    health: str
    closed_trade_count: int
    open_positions: int
    last_activity_at: datetime | None
    heartbeat_age_seconds: float | None = None
    last_started_at: datetime | None = None
    trader_id: str | None = None
    failed_strategies: tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    all_failed: bool = False
    connection_lost_at: str | None = None


def build_status_report(
    *,
    session_id: object,
    name: str,
    status: SessionStatus,
    last_heartbeat_at: datetime | None,
    last_bar_at: datetime | None,
    last_started_at: datetime | None,
    last_stopped_at: datetime | None,
    sealed_at: datetime | None,
    runtime_flags: Mapping[str, object] | None,
    closed_trade_count: int,
    open_positions: int,
    now: datetime,
    heartbeat_stale_after_seconds: float,
    bar_fresh_after_seconds: float = DEFAULT_BAR_FRESH_AFTER_SECONDS,
    trader_id: str | None = None,
) -> StatusReport:
    """Assemble a :class:`StatusReport` from the single row this story reads.

    Args:
        session_id: The row's business-key UUID (or any ``str``-able value).
        name: The operator-chosen handle.
        status: The row's ``SessionStatus``.
        last_heartbeat_at: Most recent liveness heartbeat, or ``None``.
        last_bar_at: Most recent bar observed, or ``None``.
        last_started_at: When the row last started, or ``None``.
        last_stopped_at: When the row last stopped, or ``None``.
        sealed_at: When the row was sealed, or ``None``.
        runtime_flags: Story 2.7's impairment document, or ``None``.
        closed_trade_count: AC #8's closed-trade count.
        open_positions: AC #8's open-position count.
        now: The instant to derive health and ages as of.
        heartbeat_stale_after_seconds: The staleness threshold (the caller's
            job to import — see :func:`derive_health`).
        bar_fresh_after_seconds: The ``trading``/``idle`` boundary.
        trader_id: The session's framework-free trader id, or ``None``.

    Returns:
        The assembled report, ready for :func:`render_status` or
        :func:`status_json_payload`.
    """
    # Normalise once, at the boundary, so neither the arithmetic below nor
    # ``isoformat()`` further down can be handed a naive or non-UTC value.
    last_heartbeat_at = _as_utc(last_heartbeat_at)
    last_bar_at = _as_utc(last_bar_at)
    last_started_at = _as_utc(last_started_at)
    last_stopped_at = _as_utc(last_stopped_at)
    sealed_at = _as_utc(sealed_at)

    health = derive_health(
        status,
        last_heartbeat_at,
        last_bar_at,
        runtime_flags,
        now=now,
        heartbeat_stale_after_seconds=heartbeat_stale_after_seconds,
        bar_fresh_after_seconds=bar_fresh_after_seconds,
    )
    flags = _flags_mapping(runtime_flags)
    connection_lost_at = flags.get("connection_lost_at")
    return StatusReport(
        session_id=str(session_id),
        name=name,
        status=SessionStatus(status).value,
        health=health.value,
        closed_trade_count=closed_trade_count,
        open_positions=open_positions,
        last_activity_at=_last_activity_at(
            last_heartbeat_at=last_heartbeat_at,
            last_bar_at=last_bar_at,
            last_started_at=last_started_at,
            last_stopped_at=last_stopped_at,
            sealed_at=sealed_at,
        ),
        heartbeat_age_seconds=_age_seconds(last_heartbeat_at, now=now),
        last_started_at=last_started_at,
        trader_id=trader_id,
        failed_strategies=_failed_strategy_entries(runtime_flags),
        all_failed=bool(flags.get("all_failed", False)),
        connection_lost_at=str(connection_lost_at) if connection_lost_at is not None else None,
    )


def status_json_payload(report: StatusReport) -> dict[str, object]:
    """AC #7: exactly the seven pinned keys, snake_case, ISO-8601 UTC timestamps.

    ``trader_id``, ``failed_strategies`` and ``all_failed`` are deliberately
    excluded — AR29's key set for ``status --json`` is exact, pinned by a
    set-equality test, and ``trader_id`` is human-output only.
    """
    return {
        "session_id": report.session_id,
        "name": report.name,
        "status": report.status,
        "closed_trade_count": report.closed_trade_count,
        "open_positions": report.open_positions,
        "last_activity_at": (
            report.last_activity_at.isoformat() if report.last_activity_at is not None else None
        ),
        "health": report.health,
    }


def _render_failed_strategies(failed_strategies: Sequence[Mapping[str, object]]) -> list[str]:
    """AC #5, #7: name every contained failure, using AR36-sanctioned vocabulary.

    ``detail`` is rendered verbatim — it was already redacted at the catch
    site (NFR26); re-masking with ``mask_account`` would destroy the payload.
    """
    lines: list[str] = []
    plural = "strategy was" if len(failed_strategies) == 1 else "strategies were"
    lines.append(f"  {len(failed_strategies)} {plural} contained and stopped trading:")
    for failure in failed_strategies:
        strategy_id = failure.get("strategy_id") or ""
        identity = f" ({strategy_id})" if strategy_id else ""
        detail = failure.get("detail") or ""
        suffix = f" — {detail}" if detail else ""
        lines.append(
            f"    {failure.get('spec_strategy_id', '')}{identity} — "
            f"{failure.get('error_type', '')} in {failure.get('handler', '')} "
            f"at {failure.get('at', '')}{suffix}"
        )
    return lines


def _render_degradation(report: StatusReport) -> list[str]:
    """AC #4, #5: whenever health is ``degraded``, name the cause.

    :func:`_is_degraded` treats its three senses as independent, so the
    renderer must too. Gating every explanation behind a non-empty
    ``failed_strategies`` — as this function's first version did — left
    ``all_failed`` unexplained on its own and AR32's connection-lost sense
    unexplained entirely, rendering a bare ``health: degraded`` with nothing
    an operator could act on.

    The closing fallback exists so that stays true by construction: if a
    future ``runtime_flags`` sense makes :func:`_is_degraded` return true
    without matching any branch here, the operator is told the session is
    impaired and that this reader could not name why — never nothing.
    """
    lines: list[str] = []
    if report.failed_strategies:
        lines.extend(_render_failed_strategies(report.failed_strategies))
    if report.all_failed:
        lines.append("  Every strategy in this session was contained. It can no longer trade.")
    if report.connection_lost_at is not None:
        lines.append(f"  Broker connection was lost at {report.connection_lost_at}.")
    if report.health == SessionHealth.DEGRADED and not lines:
        lines.append("  Impaired, but this session's runtime flags name no cause.")
    return lines


def render_status(report: StatusReport) -> str:
    """The operator-facing summary block for ``live status`` (AC #1, #5).

    Contains no unmasked account: nothing this module reads ever carries one
    — ``failed_strategies[*].detail`` was already redacted at the catch site
    (NFR26), and no other field originates from a third party.
    """
    lines = [
        f"session: {report.name} ({report.session_id})",
        f"  state: {report.status}",
        f"  health: {report.health}",
        f"  closed trades: {report.closed_trade_count}, open positions: {report.open_positions}",
    ]
    if report.heartbeat_age_seconds is not None:
        lines.append(f"  heartbeat: {report.heartbeat_age_seconds:.0f}s ago")
    else:
        lines.append("  heartbeat: never")
    if report.last_started_at is not None:
        lines.append(f"  last started: {report.last_started_at.isoformat()}")
    if report.trader_id:
        lines.append(f"  trader_id: {report.trader_id}")
    if report.last_activity_at is not None:
        lines.append(f"  last activity: {report.last_activity_at.isoformat()}")
    else:
        lines.append("  last activity: never")
    lines.extend(_render_degradation(report))
    return "\n".join(lines)
