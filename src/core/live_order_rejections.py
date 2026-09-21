"""The refusal tally a running session writes to its own row (Story 3.7,
FR31/FR49/NFR24).

Owns: :class:`RejectionSnapshot` — an O(1), primitives-only summary of every
order this session asked for and did not get — :class:`RejectionTally`, the
**third, independent subscriber** on ``events.order*``
(:data:`~src.core.live_order_path.ORDER_EVENTS_TOPIC`) the runner installs
beside :class:`~src.core.live_order_path.OrderEventObserver` and
:class:`~src.core.live_trade_recorder.TradeRecorder`, and
:func:`render_rejection_summary`, the pure renderer ``live start`` prints when
a run ends.

Does not own: the *logging* of a rejection (Story 3.3's ``order.rejected`` /
``order.denied``, ``src/core/live_order_path.py`` — a **zero-diff** file in
this story, decision D-A), the database write (``SessionSteadyState``'s tick
drains this object and calls the record port; ``src/services/
session_service.py`` owns the document's rules), the health derivation that
reads the column back (``src/core/live_session_health.py``, a different
process), or any retry, resubmit or "react to a rejection" behaviour
whatsoever (AR24/AR43 — this module *records and reports*; the strategy's next
signal is the only path to a new order).

**Why a sibling subscriber rather than an extension of the observer**
(decision D-B). ``OrderEventObserver`` already logs both refusals verbatim, so
the temptation is to hang a counter off it. Three things say no:
``OrderEventObserver`` is at 98 statements against CLAUDE.md's 100-statement
class cap; ``live_order_path.py`` is at 187 statements and already over its
file cap in raw lines; and its ``EMITTED_ORDER_EVENTS`` / ``_dispatch`` pins
are two-directional, so any new emitter there needs both re-argued. The
``TradeRecorder`` precedent is exact — *"the second, independent subscriber on
``events.position*``"* — and this is the third on ``events.order*``. Order of
subscription on the bus does not matter: this class needs nothing the observer
computes.

**Standard library only, and that is the point of the file.** The snapshot
crosses AR38's boundary into ``src/services`` (through the record port), so no
``OrderRejected``, no ``ClientOrderId`` and no Nautilus object may travel with
it: every field is an ``int``, ``str``, ``bool`` or ``datetime``. Dispatch is
on ``type(event).__name__`` over a closed set for the same reason — this
module never imports the classes it reacts to. The one non-stdlib import is
:func:`~src.core.live_strategy_guard.redact_accounts` and its companion cap,
which are themselves framework-free (the same package, Story 2.7's
per-token primitive built for exactly this case).

**What counts, and what resets the streak** (decision D-C, measured facts
below). ``rejected`` counts every ``OrderRejected``: IBKR's own
(``ORDER_REJECTION_CODES = {201, 203, 321, 10289, 10293}`` →
``order_status="Rejected"``, ``adapters/interactive_brokers/client/error.py:40,
:223-226``), the adapter's local one (a ``ValueError`` while translating an
order, ``execution.py:652-665``), and Story 3.4's in-flight sweep closing an
unanswered order as ``OrderRejected(reason="UNKNOWN", reconciliation=True)``
(``live/execution_engine.py:537-571``) — the last carries
``last_reconciliation=True`` in the snapshot so a reader can tell which kind it
was. ``denied`` counts every ``OrderDenied``, the risk engine's local refusal
(``risk/engine.pyx:726-823``). ``consecutive`` counts both since the last
**non-reconciliation** ``OrderAccepted``: an acceptance is the venue saying
"this order is working", which is the opposite of a refusal, while a
``reconciliation=True`` acceptance is a *startup restore* of an order accepted
before a restart, not fresh evidence that orders are getting through today.
Fills are never consulted — every fill was preceded by an acceptance. A
strategy denied locally on every order is the same silent quiet-market failure
as one rejected by the venue on every order, which is why denials extend the
same streak; ``last_kind`` keeps the two distinguishable for the operator.

``OrderTriggered``, ``OrderModifyRejected`` and ``OrderCancelRejected`` stay
outside the closed set (decision D-J): no modify or cancel-request path exists
in this repo, and a triggered stop is not a refusal. Epic 4 owns the
broker-authoritative view that would change that.

**Bounded by construction (NFR2).** A session rejected on every 1-minute
crossover produces a refusal every few minutes for 6.5 hours. A per-rejection
list would grow without bound and be re-serialised whole on every write, so
this class holds counters plus **one** ``last`` record, and the document it
produces replaces its predecessor wholesale. The write is driven by a
dirty-flag (:meth:`pending` / :meth:`mark_written`), not a queue: the tick
writes at most once per interval regardless of refusal rate, and a newer
summary simply supersedes an older one — nothing is queued and nothing is
lost, because the transcript already holds every ``order.rejected`` record
(NFR21's evidence) and losing the last ≤ 30 s of a *summary* to a ``kill -9``
costs nothing an operator cannot reconstruct. (This is deliberately **not**
Story 3.6's inline-write shape: NFR8's "a ``SIGKILL`` must lose nothing
already closed" argues for a trade and not for a refusal summary.)

**Containment, always.** A raise from a msgbus handler re-enters
``MessageBus.publish_c``, which has no ``try`` around ``sub.handler(msg)``
(``common/component.pyx:2757``), and ends at Nautilus's own silent
``os._exit(1)`` with zero output — the Story 2.7 lesson. The whole of
:meth:`handle_order_event` sits in one ``try`` that emits
:data:`TALLY_FAILED_EVENT` and never raises. That record is a **diagnostic**,
deliberately outside ``live_order_path.EMITTED_ORDER_EVENTS``'s
order-lifecycle pin, on the ``order.observer_failed`` precedent (CLAUDE.md,
"Membership-pinned lists"). The counters are committed only *after* the record
is built, so a half-applied increment cannot survive a failed build — the
``_log_filled`` lesson from Story 3.3's review.

**Timestamps come from the injected clock, never from ``event.ts_event``**
(decision D-I's sibling concern). The heartbeat and the strategy-failure
record both stamp the runner's clock, so every instant an operator compares in
``live status`` comes from one clock; and a test drives it without
``freezegun``, which is banned near this code path.

**The column's reason is redacted; the transcript's is not.** NFR26 is
value-level and IB rejection text can embed the account code, so
``last_reason`` is passed through :func:`redact_accounts` (with the configured
``TWS_ACCOUNT`` when set) and capped at :data:`MAX_DETAIL_CHARS` before it
reaches the row that ``live status`` renders. Story 3.3's verbatim
``venue_reason`` in the transcript is *not* reversed here — that open conflict
stays escalated for an epic-level ruling; decision D-I settles the column
only.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from typing import Any

from src.core.live_strategy_guard import MAX_DETAIL_CHARS, redact_accounts

#: AR41's ``order.*`` namespace. A **diagnostic** record — the tally's own
#: machinery broke and the session continues — so it sits outside
#: ``live_order_path.EMITTED_ORDER_EVENTS``, which pins the *order-lifecycle*
#: records only (the ``order.observer_failed`` / ``order.suppressed``
#: precedent). AR36: no ``halt``/``kill``/``pause``/``close``/``finalize``
#: stem.
TALLY_FAILED_EVENT = "order.rejection_tally_failed"

#: What ``last_kind`` says about the most recent refusal. ``rejected`` is a
#: venue (or adapter, or in-flight-sweep) ``OrderRejected``; ``denied`` is the
#: local risk engine's ``OrderDenied``. Both mean the same thing to the
#: operator — the strategy asked and nothing was placed — and the distinction
#: is kept so the operator knows where to look.
KIND_REJECTED = "rejected"
KIND_DENIED = "denied"


def _safe_str(event: Any, name: str) -> str:
    """One attribute, stringified, or ``""`` — and never a raise.

    The only field read that happens *inside* a containment block, so it is
    the one read that must not be able to re-raise. ``getattr(event, name,
    None)`` is not enough: its default covers ``AttributeError`` alone, and a
    ``__getattr__`` raising anything else is exactly the shape that put
    execution in the ``except`` block in the first place. ``str()`` is inside
    the ``try`` too — a ``__str__`` that raises is the same problem one step
    later.
    """
    try:
        return str(getattr(event, name, None))
    except Exception:  # noqa: BLE001 - the diagnostic must never raise
        return ""


@dataclass(frozen=True)
class RejectionSnapshot:
    """One O(1) summary of every refusal since the session started.

    Frozen and primitives-only because this record crosses AR38's boundary:
    built in ``src/core`` on the event-loop thread and handed to
    ``src/services`` by the steady-state tick, so no Nautilus object may
    travel with it.

    Attributes:
        version: A monotonic token, local to this process, that
            :meth:`RejectionTally.mark_written` acknowledges. Not part of the
            column — the document carries no schema of its own for it.
        rejected: Every ``OrderRejected`` since the session started, venue,
            adapter-local and reconciliation alike.
        denied: Every ``OrderDenied`` since the session started.
        consecutive: Refusals of either kind since the last non-reconciliation
            ``OrderAccepted``. This is the number the operator's health
            derivation reads.
        first_at: The first refusal's instant. Never moves.
        last_at: The most recent refusal's instant.
        last_kind: :data:`KIND_REJECTED` or :data:`KIND_DENIED`.
        last_client_order_id: The refused order's own id — the same string
            Story 3.3's ``order.rejected`` record carries, so a transcript line
            and a status line name the same order.
        last_instrument_id: The refused order's instrument.
        last_strategy_id: The Nautilus strategy id that asked.
        last_reason: The venue's (or the risk engine's) reason, **already
            redacted** and capped at :data:`MAX_DETAIL_CHARS`. Never the
            verbatim text — that is the transcript's, not the column's.
        last_reconciliation: Whether the most recent refusal was
            reconciliation-generated (Story 3.4's ``UNKNOWN`` sweep) rather
            than a fresh answer from the venue.
    """

    version: int
    rejected: int
    denied: int
    consecutive: int
    first_at: datetime
    last_at: datetime
    last_kind: str
    last_client_order_id: str
    last_instrument_id: str
    last_strategy_id: str
    last_reason: str
    last_reconciliation: bool

    def as_port_kwargs(self) -> dict[str, Any]:
        """Exactly the keyword set ``SessionRecordPort.record_order_rejections``
        takes — primitives only, and no ``version``.
        """
        return {
            "rejected": self.rejected,
            "denied": self.denied,
            "consecutive": self.consecutive,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "last_kind": self.last_kind,
            "last_client_order_id": self.last_client_order_id,
            "last_instrument_id": self.last_instrument_id,
            "last_strategy_id": self.last_strategy_id,
            "last_reason": self.last_reason,
            "last_reconciliation": self.last_reconciliation,
        }


class RejectionTally:
    """Counts refusals for ONE session and hands out a snapshot to write.

    Args:
        log: A structlog logger already bound to ``session_id``. The only
            channel this object has; contextvars are empty on the executor
            thread the write later runs on, so nothing here relies on them.
        time_source: Returns the current aware ``datetime``. Injected, so
            every instant in the snapshot comes from the runner's own clock
            and a test can drive it without ``freezegun``.
        account: The configured ``TWS_ACCOUNT``, when set — passed through to
            :func:`redact_accounts` so the one identifier the operator
            actually configured is masked case-insensitively even when it is
            not token-shaped. Passed in rather than read here: no settings
            read may happen inside a msgbus handler, and this module stays
            free of I/O.
    """

    def __init__(
        self,
        log: Any,
        *,
        time_source: Callable[[], datetime],
        account: str | None = None,
    ) -> None:
        self._log = log
        self._time_source = time_source
        self._account = account
        self._rejected = 0
        self._denied = 0
        self._consecutive = 0
        self._first_at: datetime | None = None
        self._version = 0
        self._written_version = 0
        self._snapshot: RejectionSnapshot | None = None
        #: Class-name dispatch over a closed set — the ``OrderEventObserver``
        #: and ``TradeRecorder`` precedent. Pinned as an exact set by
        #: ``tests/unit/core/test_live_order_rejections.py`` so a fourth type
        #: cannot join silently (decision D-J).
        self._dispatch: dict[str, Callable[[Any], None]] = {
            "OrderRejected": partial(self._record_refusal, kind=KIND_REJECTED),
            "OrderDenied": partial(self._record_refusal, kind=KIND_DENIED),
            "OrderAccepted": self._note_accepted,
        }

    @property
    def snapshot(self) -> RejectionSnapshot | None:
        """The latest summary, or ``None`` when nothing was ever refused.

        The runner's and the CLI's read (decision D-H). Reading it never
        clears the dirty flag — only :meth:`mark_written` does.
        """
        return self._snapshot

    def handle_order_event(self, event: Any) -> None:
        """Handle one ``events.order*`` delivery.

        The whole body is one ``try``: this runs inline inside
        ``MessageBus.publish_c``, which has no ``try`` of its own, so a raise
        here ends the process at ``os._exit(1)`` with zero output.

        ⚠️ The diagnostic's own field read goes through :func:`_safe_str`, not
        through ``getattr(event, name, None)``. Measured while writing this
        story's containment test: a default-valued ``getattr`` suppresses
        **only** ``AttributeError``, so an event whose ``__getattr__`` raises
        anything else — the very shape that brought execution into this
        ``except`` block — raises straight back out of the handler that exists
        to contain it. The sibling handlers in ``live_order_path.py`` and
        ``live_trade_recorder.py`` use the bare ``getattr`` form and carry the
        same hole; both are zero-diff files in this story (decision D-A), so it
        is recorded in ``deferred-work.md`` rather than fixed here.
        """
        try:
            handler = self._dispatch.get(type(event).__name__)
            if handler is not None:
                handler(event)
        except Exception as exc:  # noqa: BLE001 - a msgbus handler must never raise
            self._log.error(
                TALLY_FAILED_EVENT,
                stage="handle_order_event",
                event_type=type(event).__name__,
                client_order_id=_safe_str(event, "client_order_id"),
                error_type=type(exc).__name__,
                exc_info=True,
            )

    def pending(self) -> RejectionSnapshot | None:
        """The snapshot the tick should write, or ``None`` when nothing changed.

        Dirty-flag, not a queue (NFR2): a clean tally costs zero database
        round trips, and a tally dirtied five times between two ticks costs
        exactly one write of the *latest* summary.
        """
        if self._snapshot is None or self._snapshot.version == self._written_version:
            return None
        return self._snapshot

    def mark_written(self, version: int) -> None:
        """Acknowledge that ``version`` reached the row.

        A **stale** version leaves the tally dirty: a refusal that arrived
        between the tick reading :meth:`pending` and its write landing must
        not be marked clean by the older write's acknowledgement, or that
        refusal never reaches the column.
        """
        if version > self._written_version:
            self._written_version = version

    def _note_accepted(self, event: Any) -> None:
        """Reset the streak — unless this acceptance is a startup restore.

        A ``reconciliation=True`` ``OrderAccepted`` is Story 3.4's replay of an
        order accepted *before* a restart; treating it as fresh evidence that
        orders are getting through today would silently clear a streak the
        operator needs to see (decision D-C).

        **A reset dirties the tally**, so the *next* tick writes the cleared
        streak to the row. Without that, a session that recovered would read
        ``degraded`` from another process for the rest of its run — a stale
        fact presented as a live one, the same trap Story 2.7's
        ``-> running`` clear exists to close. Bounded: at most one extra write
        per recovery, and a streak already at zero writes nothing at all.
        The ``last`` record is carried forward untouched — the most recent
        *refusal* is still the most recent refusal.
        """
        if bool(event.reconciliation) or self._consecutive == 0 or self._snapshot is None:
            return
        self._consecutive = 0
        self._version += 1
        self._snapshot = replace(self._snapshot, version=self._version, consecutive=0)

    def _record_refusal(self, event: Any, *, kind: str) -> None:
        """Read the event, then commit — never the other way round.

        Every attribute read that can raise happens before any counter moves,
        so a malformed event leaves the tally exactly as it found it (the
        ``_log_filled`` lesson, Story 3.3 review). The snapshot is then
        replaced **whole**, never mutated: it is frozen, it is what the tick
        writes, and "replaced whole, no list" is the shape NFR2 asks for.
        """
        client_order_id = str(event.client_order_id)
        instrument_id = str(event.instrument_id)
        strategy_id = str(event.strategy_id)
        reason = redact_accounts(str(event.reason), account=self._account)[:MAX_DETAIL_CHARS]
        reconciliation = bool(event.reconciliation)
        at = self._time_source()

        self._denied += kind == KIND_DENIED
        self._rejected += kind == KIND_REJECTED
        self._consecutive += 1
        self._first_at = self._first_at or at
        self._version += 1
        self._snapshot = RejectionSnapshot(
            version=self._version,
            rejected=self._rejected,
            denied=self._denied,
            consecutive=self._consecutive,
            first_at=self._first_at,
            last_at=at,
            last_kind=kind,
            last_client_order_id=client_order_id,
            last_instrument_id=instrument_id,
            last_strategy_id=strategy_id,
            last_reason=reason,
            last_reconciliation=reconciliation,
        )


def render_rejection_summary(snapshot: RejectionSnapshot | None) -> list[str]:
    """The block ``live start`` prints when a run that saw refusals ends.

    Pure, and in this module rather than in ``src/cli/commands/live.py``
    because that file is already over its size cap and under a
    "registration lines only" discipline (Story 2.8). **Every** line the
    operator sees is built here, header and trailer included: the CLI helper
    is then a `for` loop with no wording in it at all, which is what lets the
    whole block be asserted — AR36 vocabulary, NFR26 redaction, field
    coverage — without a Click runner. A clean run renders nothing at all:
    the empty list, not a "no rejections" line.

    The trailer points at both halves of the evidence, mirroring the
    contained-failure block's: the transcript for each venue reason verbatim
    (Story 3.3's contract — this summary's ``reason`` is the *redacted* copy),
    and the session's row for the same summary from another process.

    AR36-audited: *rejected*, *denied* and *refused* are sanctioned operator
    vocabulary; no ``halt``/``kill``/``pause``/``close``/``finalize`` stem
    appears. ``last_reason`` is rendered verbatim because it was already
    redacted when the snapshot was built (NFR26) — re-masking it with
    ``mask_account`` would destroy the payload.
    """
    if snapshot is None:
        return []
    kind = "rejected by the venue" if snapshot.last_kind == KIND_REJECTED else "denied locally"
    marker = " (from reconciliation)" if snapshot.last_reconciliation else ""
    return [
        "⚠️  Orders were refused during this run:",
        f"    {snapshot.rejected} rejected, {snapshot.denied} denied "
        f"({snapshot.consecutive} in a row with no acceptance between)",
        f"    first refusal at {snapshot.first_at.isoformat()}",
        f"    most recent at {snapshot.last_at.isoformat()} — {snapshot.last_instrument_id} "
        f"{snapshot.last_client_order_id} {kind}{marker}",
        f"    reason: {snapshot.last_reason}",
        "    See `order.rejected` / `order.denied` in the log for each venue reason verbatim, "
        "and `runtime_flags.order_rejections` on the session's row for the same summary from "
        "another process.",
    ]
