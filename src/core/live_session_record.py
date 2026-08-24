"""AR32's record port: the only way a running session touches its own row.

Owns: :class:`SessionRecordPort`, the two-method Protocol
``LiveSessionRunner`` writes its liveness timestamps and its final ``stopped``
transition through.

Does not own: the implementation (``src/services/session_record.py``, which
holds the SQLAlchemy), the validation those writes go through
(``src/services/session_service.py``), or the runner that calls it
(``src/core/live_session_runner.py``).

**Standard library only.** That is the point of the file rather than a
side-effect of it: AR38 forbids ``LiveSessionRunner`` from importing SQLAlchemy,
and AR32 nevertheless requires the runner to write ``last_heartbeat_at`` and
``last_bar_at`` *"through the same record port it uses for transitions"*. A
Protocol is what lets both be true at once — the runner depends on this shape,
the CLI (the composition root) supplies something that satisfies it, and the
database stays on the other side of the boundary.

**The port deliberately takes no ``session_id`` and no ``started_at``.** Both
are bound into the adapter at construction, so a runner physically cannot write
to the wrong row and cannot forge its own claim to ownership — which matters,
because ``started_at`` is precisely what the mid-run reclaim guard compares
against (see ``session_service._stamp_activity``).

Known, accepted limit: a port is a shape, not a guarantee. Nothing here
promises the write reached a database, only that the caller asked for it. The
runner treats a raising ``record_activity`` as survivable (AR42) — with one
named exception, the reclaim refusal — and that policy lives in the runner, not
here.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable


class SessionReclaimedError(Exception):
    """This process no longer owns the session it is running.

    Raised by an adapter when the record layer refuses a write because the row
    is no longer this process's to write: another process reclaimed it (the
    row's ``last_started_at`` moved past this one's), or the row is no longer
    ``running`` at all. Both mean the same thing to a runner, and both are
    **fatal** — this is the one exception the heartbeat loop must not swallow
    under AR42. Everything else there is a database hiccup and survivable.

    Declared here, in the port's own module, so the runner can catch it without
    importing ``src.db.exceptions`` — which AR38 forbids it. The adapter
    translates; see ``src/services/session_record.py``.

    Known, accepted limit: this is a **detection**, not a cure. The window
    between another process reclaiming the session and this process's next
    heartbeat is up to one interval of two live processes on one broker
    account — the NFR6 catastrophe, narrowed rather than closed. Closing it
    needs a fencing token the schema does not have.

    ⚠️ **The reason that column was rejected no longer holds** (Story 2.7,
    2026-08-23). It was rejected *"because this phase's single migration is
    spent"*; Story 2.7's ``runtime_flags`` migration falsified that, so the
    cost argument against an owner/epoch column is gone while the hazard is
    unchanged. Re-opened in ``deferred-work.md`` under ``story-2.7`` rather than
    taken here: a fencing token changes the meaning of every write on this port
    and is not a bystander to a containment story.
    """


@runtime_checkable
class SessionRecordPort(Protocol):
    """What a running session needs from its own database row, and no more.

    ``@runtime_checkable`` so a hand-written test double can be *asserted* to
    satisfy the port rather than merely duck-typing at the call site. Note what
    that check does and does not do: ``isinstance`` against a runtime-checkable
    Protocol verifies the method *names* exist, never their signatures.
    """

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        """Stamp this session's liveness columns (AR32).

        Args:
            at: The heartbeat instant. Passed rather than read from a clock
                inside the adapter so the runner's own injected clock is what
                lands in ``last_heartbeat_at`` — without which the runner's
                ``time_source`` seam is decorative and its cadence cannot be
                driven deterministically in a test.
            bar_seen_at: When a bar was last observed since the previous call,
                or ``None`` for an interval in which none arrived. ``None``
                leaves ``last_bar_at`` **unchanged**; it never clears it.
        """
        ...

    def mark_stopped(self) -> None:
        """Move this session to ``stopped`` through the one validated path.

        Called from the runner's ``finally``, **after** the node has been torn
        down — committing ``stopped`` while the broker link is still up opens a
        door the AR33 reclaim guard does not watch.
        """
        ...

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
        """Record that a strategy was contained, so another process can see it
        (Story 2.7, FR49, NFR23).

        Called from ``SessionSteadyState``'s tick, **never** from the wrapped
        handler that caught the failure: ``handle_*`` runs inline on the
        event-loop thread inside ``MessageBus.publish_c``, and a Postgres round
        trip there stalls the loop and delays the bar for every later-subscribed
        strategy. The guard queues; the tick drains and writes on its own
        executor.

        Every argument is a standard-library primitive. AR38 permits nothing
        else across this boundary, so the runner translates at the catch site —
        ``type(exc).__name__``, ``str(strategy.id)``, a redacted first line — and
        no ``Strategy``, no ``StrategyId`` and no exception object travels here.

        Args:
            strategy_id: The Nautilus id at failure time, or ``""`` when the
                strategy never started.
            spec_strategy_id: The spec's own id — what the operator wrote.
            error_type: The exception's class name.
            handler: ``"handle_bar"``, ``"handle_event"`` or ``"start"``.
            at: When the failure was contained, from the runner's clock.
            detail: One line of the message, **already redacted** (NFR26). The
                redaction happens at the catch site because that is the only
                place the raw text exists. No traceback: that belongs in the log
                sink, and a column carrying one would be the least redacted
                place in the system.
            all_failed: Whether every strategy in this session has now failed.
                Never downgraded by a later call.
        """
        ...
