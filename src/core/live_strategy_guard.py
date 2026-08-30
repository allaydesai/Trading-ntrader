"""The boundary the runner installs around each strategy (Story 2.7, FR50, NFR12).

Owns: :class:`StrategyGuard` — the latch, the log, the pending queue and the
``degrade()`` for one session's strategies — plus :class:`StrategyFailure` (the
primitive-only record that crosses AR38's boundary), :func:`redact_accounts`
(NFR26) and :class:`StrategyGuardError`.

Does not own: the wiring (``src/core/live_session_runner.py`` calls
:meth:`StrategyGuard.wrap` between ``materialise_strategy`` and
``add_strategy``), the database write (``src/core/live_session_steady_state.py``
drains :meth:`StrategyGuard.drain_pending` on its tick, through that object's
own executor), or what the operator is shown (``src/cli/commands/live.py``).

**Why a wrapper, and not a ``try`` in the runner.** Measured against the
installed ``nautilus-trader 1.220.0``: an exception raised in ``Strategy.on_bar``
is *re-raised* by ``Actor.handle_bar`` (``common/actor.pyx:3743-3748``), unwinds
through ``MessageBus.publish_c`` — which has no ``try`` around
``sub.handler(msg)`` (``common/component.pyx:2754-2757``) — into
``LiveDataEngine._run_data_queue``, and lands in ``_handle_queue_exception``
(``live/data_engine.py:347-365``), whose default branch is **``os._exit(1)``**.
That runs no ``except``, no ``finally`` and no ``atexit``, so the runner's own
boundary is never reached, ``mark_stopped()`` never runs, and the row is
stranded at ``running``. Measured end to end in a fresh interpreter: uncontained
gives ``rc=1`` with **zero bytes** of output; with this wrapper, ``rc=0``, the
raiser ``DEGRADED``, the sibling receiving every bar. A ``try``/``except`` in
``LiveSessionRunner`` is *structurally incapable* of containing that — the
containment has to be inside the failing strategy's own handler, before the
raise re-enters ``publish_c``.

**Nothing leaves a wrapped handler except cancellation.** Including
:class:`~src.core.live_session_record.SessionReclaimedError`, which every other
caller in this codebase re-raises, and including ``BaseException`` subclasses —
a strategy that calls ``sys.exit()`` inside ``on_bar`` is contained like any
other failure, because ``SystemExit`` unwinds through ``publish_c`` into the
same ``os._exit(1)`` as a ``RuntimeError`` does (review fix, 2026-08-23). The
two exceptions that DO leave are ``asyncio.CancelledError`` and
``KeyboardInterrupt``: both belong to the event loop and the signal machinery,
not to the strategy, and swallowing either would break cancellation for the
task the handler runs under. Measured, three modes of the same probe: no
guard -> ``rc=1``/0 bytes; guard -> ``rc=0``; **guard whose ``except`` block
itself raises -> ``rc=1``/0 bytes**. ``record_activity``'s reclaim can be fatal
because it is raised on the steady-state executor and propagates to ``run()``;
there is no such boundary inside ``handle_bar``, so a reclaim seen here is
raised one tick later, where one exists (*Judgment call #10*).

**No I/O on the calling thread.** ``handle_*`` runs inline inside ``publish_c``
on the event-loop thread, where ``note_bar``'s own docstring already says
handlers *"must be trivial and must not raise"*. A Postgres round trip here
stalls the loop and delays the bar for every later-subscribed strategy —
partially recreating the starvation this module exists to fix, and this repo has
measured that class of write blocking the node for **59.81s** (decision D2). So
the guard queues and returns; the steady-state tick drains it.

**Framework-free on purpose.** Standard library, ``structlog`` and one
first-party helper — no ``nautilus_trader`` import — so the whole policy is
unit-testable with no C logging and no kernel. The cost, stated rather than
discovered: ``mypy`` sees ``Any`` at the strategy parameter, so a typo in a
method name is a runtime failure rather than a type error. Mitigated by
:class:`StrategyGuardError` on a refused ``setattr`` and by the component-tier
proof against real ``Strategy`` objects.

Known, accepted limits:

1. Only ``handle_bar`` and ``handle_event`` are wrapped. The unwrapped surface
   is wider than timers (review fix, 2026-08-23): ``handle_bars`` and
   ``handle_historical_data`` (the ``request_bars`` response path, which
   ``sma_momentum`` already drives at warm-up), ``handle_quote_tick``,
   ``handle_trade_tick`` and ``handle_data`` all dispatch outside this
   boundary — a custom-submodule strategy overriding one of those and raising
   gets AC #6's whole-node graceful shutdown, not containment. A ``LiveClock``
   timer callback that raises is contained by Nautilus itself but **invisibly**
   (measured: swallowed at the pyo3 boundary, exit 0, nothing printed); no repo
   strategy uses timers today. Widening ``GUARDED_HANDLERS`` is in
   ``deferred-work.md``.
2. The runner's own ``note_bar`` and the ``LiveBarObserver`` are **not** wrapped
   here. They are covered by the three engines'
   ``graceful_shutdown_on_exception=True`` (AC #6) — a graceful stop of the whole
   node rather than containment, so defence in depth, never a substitute.
3. A ``DEGRADED`` strategy is skipped by ``Trader._stop()``, which guards on
   ``is_running`` (``component.pyx:1757-1767``), so its ``on_stop()`` never
   runs there. Story 3.1 compensates rather than relying on Nautilus for it:
   the runner's teardown explicitly stops each ``DEGRADED`` strategy via
   ``live_session_runner.stop_degraded_strategies``, so its own ``on_stop()``
   still runs.
4. This class is **346 lines against CLAUDE.md's 100-line guideline** (measured
   2026-08-23, after the review fixes), joining ``LiveSessionRunner`` (605),
   ``SessionSteadyState`` (278) and ``LiveBarObserver`` (215) in
   ``deferred-work.md``'s standing item. Disclosed rather than resolved by a
   split the story's pinned public surface forbids.
"""

import asyncio
import re
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.core.live_gate import mask_account

#: The two Nautilus entry points this guard wraps, at the **instance** level.
#:
#: ``handle_event`` is wrapped now rather than in Epic 3, when orders first
#: exist, because wrapping it later would be too late: ``Strategy.register()``
#: subscribes the **bound** ``self.handle_event`` to ``events.order.{id}`` and
#: ``events.position.{id}`` (``trading/strategy.pyx:314-315``) during
#: ``Trader.add_strategy``, so a wrapper installed after that call would never
#: be reached. It also closes a real Epic 3 hazard: an exception in one
#: strategy's order-event handler silently drops that fill's pending
#: ``PositionOpened``/``Changed``/``Closed``, because
#: ``execution/engine.pyx:1170-1187`` clears ``_pending_position_events``
#: *before* publishing the order event.
GUARDED_HANDLERS: tuple[str, ...] = ("handle_bar", "handle_event")

#: How much of an exception message reaches the database column. The log sink
#: gets the whole traceback; ``runtime_flags`` gets one redacted line.
MAX_DETAIL_CHARS = 200

#: Account-shaped tokens: one or two leading uppercase letters and six to ten
#: digits, word-bounded. Covers every IBKR account form this repo has seen —
#: ``DU4076626`` (paper), ``U1234567`` (live) — and is narrow enough that
#: ordinary traceback text (``line 150, in _calculate_position_size``,
#: ``AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL``) does not match it.
_ACCOUNT_TOKEN = re.compile(r"\b[A-Z]{1,2}\d{6,10}\b")

#: AR41 event names. ``strategy.*`` is an unused namespace, analogous to the
#: enumerated ``order.*`` / ``connection.*`` families. ⚠️ AR36's vocabulary scan
#: word-matches ``pause|halt|kill|close|finalize`` against operator-facing
#: strings on the stop path — ``strategy.halted`` would fail the build.
FAILED_EVENT = "strategy.failed"
DEGRADED_EVENT = "strategy.degraded"
START_FAILED_EVENT = "strategy.start_failed"
ALL_FAILED_EVENT = "session.all_strategies_failed"
#: AR42's event: the guard's own machinery broke, and the session continues.
GUARD_FAILED_EVENT = "session.strategy_record_failed"


def redact_accounts(text: str, *, account: str | None = None) -> str:
    """Replace each account-shaped token with its mask, leaving the rest intact.

    ⚠️ **This is not ``mask_account(text)``, and the difference is the whole
    point.** ``live_gate.mask_account`` is a **whole-value** masker — ``"***"``
    plus the last three characters of its *entire* input. EXECUTED:
    ``mask_account("Error 321: account DU4076626 is not managed")`` returns
    ``'***ged'``, and a full traceback masks to ``'***ged'`` too. Applying it to
    free text destroys the payload while the naive assertion (*"``DU4076626``
    does not appear"*) still passes — a test that cannot fail. It is the right
    **per-token** primitive, and this function calls it per match.

    Redaction rather than the repo's existing answer because
    ``live_check._SAFE_MESSAGE_EXCEPTION_NAMES`` is an *allowlist* of exception
    types whose ``str()`` this codebase wrote, and a strategy can raise anything
    — while AC #1 needs the traceback an ``error_type``-only policy would
    withhold. Measured: raw ``exc_info=True`` under this repo's logging
    configuration renders an unmasked account id to the console and to
    ``logs/ntrader.log``.

    The ``account`` clause (review fix, 2026-08-23, restoring AC #8's pinned
    contract: *"plus the configured TWS_ACCOUNT when set"*): the one identifier
    the operator actually configured is redacted **case-insensitively**, so a
    strategy that lowercases its message before raising (``du4076626``) no
    longer leaks the very value the shape-based token misses. No settings read
    happens here — the caller passes the already-loaded string, which keeps
    this module framework-free and this function free of I/O on the event-loop
    thread.

    Known, accepted limit: an account identifier that is neither token-shaped
    nor the configured one is not redacted. In ``deferred-work.md``.

    Args:
        text: Any free text — an exception message or a rendered traceback.
        account: The configured ``TWS_ACCOUNT``, when set. ``None`` or ``""``
            adds nothing to the token pass.

    Returns:
        The same text with every account-shaped token replaced in place.
    """
    redacted = _ACCOUNT_TOKEN.sub(lambda match: mask_account(match.group(0)), text)
    if account:
        pattern = re.compile(rf"\b{re.escape(account)}\b", re.IGNORECASE)
        redacted = pattern.sub(lambda match: mask_account(match.group(0)), redacted)
    return redacted


class StrategyGuardError(Exception):
    """A strategy object refused to be wrapped.

    Raised by :meth:`StrategyGuard.wrap` so an un-wrappable strategy is a clear
    startup failure rather than an ``AttributeError`` surfacing later from
    inside the ``trading`` phase. Measured (finding #5): a **bare** ``Strategy``
    is a cdef class with no ``__dict__`` and rejects the assignment, while
    **every** Python subclass accepts it — and all seven registered strategies
    are Python subclasses. So this is a tripwire for a shape that does not exist
    in the repo today, not a routine outcome.
    """


class NoStrategyStartedError(Exception):
    """Every strategy in the session failed to start, so it cannot trade.

    AC #5's last clause. The per-spec containment above deliberately lets the
    ``trading`` phase succeed when *some* strategy started — that is the whole
    point of containing a start failure — but a session with **zero** live
    strategies holds an IBKR client id and a market-data line while being
    incapable of placing an order, and reporting it as started would be exactly
    the false green ``runtime_flags`` exists to prevent.

    Raised by ``LiveSessionRunner._phase_trading``, so AR39's *"a failure in any
    phase stops the sequence"* does the rest. The exit code is AR28's generic
    **1** (*"a configuration, state or database failure"*), reached by
    ``live_check.classify_failure`` walking the MRO and finding nothing more
    specific — which is correct here and invents no code, the discipline Story
    1.7 recorded. Its message is this codebase's own and names the specs that
    failed, so it is on ``live_check._SAFE_MESSAGE_EXCEPTION_NAMES``.
    """


@dataclass(frozen=True)
class StrategyFailure:
    """One contained strategy failure, in primitives only.

    Every field is a ``str`` or a ``datetime`` because this record crosses
    AR38's boundary: built at the catch site in ``src/core`` and handed to
    ``src/services``, so no ``Strategy``, no ``StrategyId`` and no exception
    object may travel with it.

    Attributes:
        strategy_id: The **Nautilus** id, read at *failure* time, not at wrap
            time — ``Trader.add_strategy`` rewrites it when ``order_id_tag`` is
            ``None`` (measured: ``SMACrossover-None -> SMACrossover-000``) and
            the guard is applied before that call, so a wrap-time capture
            records an id that never existed on the bus. Empty for a start
            failure, and for the pathological case where reading it raised.
        spec_strategy_id: The spec's own id, e.g. ``"sma_crossover"`` — what the
            operator wrote, and what identifies the failure in the CLI.
        error_type: ``type(exc).__name__``. Never the exception object.
        handler: ``"handle_bar"``, ``"handle_event"`` or ``"start"``.
        at: When the failure was contained, from the runner's injected clock.
        detail: One **redacted** line of the message, capped at
            :data:`MAX_DETAIL_CHARS`. No traceback — that belongs in the log
            sink, not in a database column.
    """

    strategy_id: str
    spec_strategy_id: str
    error_type: str
    handler: str
    at: datetime
    detail: str


class StrategyGuard:
    """Owns the latch, the log, the pending queue and the degrade for ONE session.

    One instance per process run, constructed by ``LiveSessionRunner.__init__``
    from what the runner already holds. It deliberately does **not** hold the
    record port: the port is reached only from the steady-state tick (AC #10),
    which already has one.

    **Threading.** Every mutating entry point runs on the event-loop thread —
    ``wrap`` and :meth:`record_start_failure` from the synchronous ``trading``
    phase, the wrapped handlers from inside ``MessageBus.publish_c``, and
    :meth:`drain_pending` from ``SessionSteadyState.run``'s tick before it hands
    the write to an executor. So the queue needs no lock, and adding one would
    imply a concurrency that does not exist.

    Args:
        log: A structlog logger already bound to ``session_id``.
        time_source: Aware ``datetime`` clock, injected so a test drives the
            recorded instants exactly. Deliberately the *runner's* clock, so a
            failure's ``at`` and the heartbeat's ``last_heartbeat_at`` come from
            the same source.
        account: The configured ``TWS_ACCOUNT``, when set — threaded into every
            :func:`redact_accounts` call this guard makes (AC #8's pinned
            contract). The *string*, never the settings object: this module
            stays framework-free.
    """

    def __init__(
        self, *, log: Any, time_source: Callable[[], datetime], account: str | None = None
    ) -> None:
        self._log = log
        self._time_source = time_source
        self._account = account or None
        self._latched: set[str] = set()
        self._failures: list[StrategyFailure] = []
        self._pending: list[StrategyFailure] = []
        self._expected = 0
        self._all_failed_reported = False

    # ------------------------------------------------------------------
    # What the runner calls.
    # ------------------------------------------------------------------

    def wrap(self, strategy: Any, *, spec_strategy_id: str) -> None:
        """Install the boundary on both handlers of one strategy.

        Must be called **before** ``Trader.add_strategy``, and the ordering is
        load-bearing in both directions: ``Strategy.register()`` subscribes the
        bound ``self.handle_event`` during ``add_strategy``
        (``trading/strategy.pyx:314-315``) and ``subscribe_bars`` binds
        ``self.handle_bar`` during ``on_start`` (``common/actor.pyx:1804-1806``),
        so wrapping at materialisation time is before both while wrapping after
        would leave ``handle_event`` bound to the unwrapped method forever.
        Measured: the wrapper survives ``add_strategy``'s ``change_id``, and
        ``register()`` does subscribe the wrapped handler.

        Args:
            strategy: The materialised strategy. Duck-typed — this module never
                imports ``nautilus_trader``.
            spec_strategy_id: The spec's own id, used as the latch key. Unique
                within a session by construction (``SessionSpec`` rejects
                duplicate strategies). Used rather than the Nautilus id because
                that id is rewritten *after* this call and reading it can raise.

        Raises:
            StrategyGuardError: The strategy refused the assignment.
        """
        for handler_name in GUARDED_HANDLERS:
            base = getattr(strategy, handler_name)
            wrapped = self._wrapper(strategy, spec_strategy_id, handler_name, base)
            try:
                setattr(strategy, handler_name, wrapped)
            except (AttributeError, TypeError) as exc:
                raise StrategyGuardError(
                    f"Strategy {spec_strategy_id!r} refused the containment boundary on "
                    f"{handler_name!r}: {type(exc).__name__}. A session cannot start a strategy "
                    "whose failures could take the process down."
                ) from exc
            # Defensive: a silent no-op assignment would leave the boundary
            # absent while every startup log line said it was installed.
            if getattr(strategy, handler_name) is not wrapped:
                raise StrategyGuardError(
                    f"Strategy {spec_strategy_id!r} accepted the assignment to {handler_name!r} "
                    "but did not keep it, so the containment boundary is not installed."
                )

    def record_start_failure(self, *, spec_strategy_id: str, exc: BaseException) -> None:
        """Contain a spec that raised in ``on_start`` or in ``add_strategy``.

        AC #5's path. There is no strategy object to read a Nautilus id from —
        ``Component.start()`` re-raises and halts the state transition
        (``common/component.pyx:1888-1902``), so the strategy never reached a
        state in which its id was meaningful on the bus.

        Guarded like the bar path (AC #10): the ``trading`` phase's per-spec
        ``except`` calls this and then continues to the next spec, so a failure
        *here* must not become the thing that stops the loop.

        Args:
            spec_strategy_id: The spec's own id.
            exc: What was raised. Never stored — only its type, its first line
                and its traceback, all redacted.
        """
        try:
            self._contain(
                strategy=None,
                spec_strategy_id=spec_strategy_id,
                handler="start",
                exc=exc,
                event=START_FAILED_EVENT,
            )
        except Exception as guard_exc:  # noqa: BLE001 - AR42: nothing here may stop a start
            self._report_guard_failure(spec_strategy_id, guard_exc)

    def expect(self, strategy_count: int) -> None:
        """Tell the guard how many specs this session started with.

        Without it :attr:`all_failed` can never be true, because "every strategy
        has failed" is a statement about a denominator the guard cannot observe.
        """
        self._expected = strategy_count

    def drain_pending(self) -> tuple[StrategyFailure, ...]:
        """Take and clear the queue. Called by ``SessionSteadyState``'s tick.

        Returns:
            Every failure not yet handed out, oldest first. Each is returned
            exactly once; :attr:`failures` keeps the full history separately, so
            draining never costs the CLI its report.
        """
        # One atomic swap, not copy-then-clear: a failure appended between a
        # copy and a clear would be discarded undrained (review fix, 2026-08-23).
        drained, self._pending = tuple(self._pending), []
        return drained

    def requeue(self, failures: Iterable[StrategyFailure]) -> None:
        """Put drained failures back at the front of the queue (AC #4).

        Called by the steady-state tick when the database write failed for a
        reason other than a reclaim, so the record is retried on the next tick
        rather than lost for the rest of a multi-week run (review fix,
        2026-08-23). At the *front*, so oldest-first survives the retry.
        Bounded by construction: the guard latches per strategy, so the queue
        can never hold more than one failure per strategy per process run —
        this is not the unbounded backlog NFR2 forbids.
        """
        self._pending[:0] = failures

    @property
    def failures(self) -> tuple[StrategyFailure, ...]:
        """Every failure contained during this process run, for the CLI's report."""
        return tuple(self._failures)

    @property
    def all_failed(self) -> bool:
        """Whether every strategy the session started with has now failed.

        ``False`` until :meth:`expect` has been told the denominator, and
        ``False`` for a session with no strategies at all — reporting "all
        failed" for a session that never had any would be a different and more
        confusing lie than saying nothing.
        """
        return self._expected > 0 and len(self._latched) >= self._expected

    # ------------------------------------------------------------------
    # The boundary itself.
    # ------------------------------------------------------------------

    def _wrapper(
        self,
        strategy: Any,
        spec_strategy_id: str,
        handler_name: str,
        base: Callable[..., Any],
    ) -> Callable[..., Any]:
        """Build the instance-level replacement for one ``handle_*`` method.

        ``*args``/``**kwargs`` rather than a pinned signature: ``handle_bar``
        and ``handle_event`` differ, and the message bus calls whatever it was
        handed. The base handler is always called, and always inside the
        ``try`` — see :meth:`_contain` for why the latch does not short-circuit
        it.
        """

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return base(*args, **kwargs)
            except (KeyboardInterrupt, asyncio.CancelledError):
                # These belong to the loop and the signal machinery, not to the
                # strategy; swallowing either breaks cancellation for the task
                # this handler runs under.
                raise
            except BaseException as exc:  # noqa: BLE001 - this IS the boundary
                # `BaseException`, not `Exception` (review fix, 2026-08-23): a
                # strategy calling `sys.exit()` in a handler raises SystemExit,
                # which unwinds through `publish_c` into the same silent
                # `os._exit(1)` as any other escape.
                self._contain(
                    strategy=strategy,
                    spec_strategy_id=spec_strategy_id,
                    handler=handler_name,
                    exc=exc,
                    event=FAILED_EVENT,
                )
                return None

        return wrapped

    def _contain(
        self,
        *,
        strategy: Any,
        spec_strategy_id: str,
        handler: str,
        exc: BaseException,
        event: str,
    ) -> None:
        """Latch, log, queue and degrade — with nothing able to escape.

        The whole body sits inside one ``except`` because a raise from here
        re-enters ``publish_c`` and hits the same silent ``os._exit(1)`` the
        wrapper exists to avoid (measured: ``rc=1``, zero bytes).

        **The latch is set before ``degrade()``.** ``degrade()`` from a
        non-``RUNNING`` state is *silently swallowed* — ``_trigger_fsm`` catches
        ``InvalidStateTrigger``, logs and returns (``component.pyx:2130-2134``;
        measured ``READY -> degrade() -> still READY``) — so relying on it to
        suppress repeats would log one contained failure per bar forever.

        **It suppresses the side effects only.** The caller has already called
        through to the base handler by the time this runs, deliberately:
        ``Actor.handle_bar`` updates registered indicators *before* the
        ``RUNNING`` gate, so returning early once latched freezes them —
        measured over 8 bars with an ``EMA(3)``, ``ema.count`` 1 rather than 8.
        Calling through is cheap after ``degrade()``, because ``handle_bar``'s
        own ``if state == RUNNING`` gate is then false.
        """
        try:
            if spec_strategy_id in self._latched:
                return
            self._latched.add(spec_strategy_id)
            failure = self._build_failure(strategy, spec_strategy_id, handler, exc)
            self._log_failure(event, failure, exc)
            self._failures.append(failure)
            self._pending.append(failure)
            self._degrade(strategy, failure)
            self._report_all_failed_once()
        except Exception as guard_exc:  # noqa: BLE001 - AC #10: nothing propagates
            self._report_guard_failure(spec_strategy_id, guard_exc)

    def _build_failure(
        self, strategy: Any, spec_strategy_id: str, handler: str, exc: BaseException
    ) -> StrategyFailure:
        """Translate the exception into primitives, redacting as it goes."""
        return StrategyFailure(
            strategy_id=_read_strategy_id(strategy),
            spec_strategy_id=spec_strategy_id,
            error_type=type(exc).__name__,
            handler=handler,
            at=self._time_source(),
            detail=_one_redacted_line(exc, account=self._account),
        )

    def _log_failure(self, event: str, failure: StrategyFailure, exc: BaseException) -> None:
        """Emit the ERROR record, with the traceback as an explicit field.

        Explicit rather than ``exc_info=True`` for two reasons, both measured:
        ``structlog.testing.capture_logs`` records only ``exc_info: True`` and
        never a rendered string, so a test could not assert on it at all; and
        the raw form renders an unmasked account identifier to the console and
        to ``logs/ntrader.log``, which NFR26 forbids.
        """
        self._log.error(
            event,
            strategy_id=failure.strategy_id,
            spec_strategy_id=failure.spec_strategy_id,
            error_type=failure.error_type,
            handler=failure.handler,
            traceback=_redacted_traceback(exc, account=self._account),
        )

    def _degrade(self, strategy: Any, failure: StrategyFailure) -> None:
        """Isolate the strategy without touching a position (AC #7, NFR14).

        Not ``stop()``: ``Trader.stop_strategy()`` would run the strategy's
        own ``on_stop()`` mid-containment, over an unrelated ``on_bar`` bug —
        the wrong time for any strategy-owned side effect to run, regardless
        of what that hook does today (Story 3.1 removed ``sma_crossover``'s
        flatten, but the AR43 anti-pattern this avoids is about the verb, not
        one strategy's current body). Not ``remove_strategy()``: measured, it
        does not unsubscribe the handlers (``bar_subs 2 -> 2``) and it deletes
        the strategy from
        ``Trader.strategy_states()``, destroying the in-process half of AC #4's
        visibility. ``degrade()`` also keeps the strategy **warm** — indicators
        keep updating, ``resume()`` is legal — which is what makes latching on
        the first failure recoverable rather than terminal.
        """
        if strategy is None or not strategy.is_running:
            return
        strategy.degrade()
        self._log.warning(
            DEGRADED_EVENT,
            strategy_id=failure.strategy_id,
            spec_strategy_id=failure.spec_strategy_id,
            state="degraded",
        )

    def _report_all_failed_once(self) -> None:
        """Say so when the last live strategy goes, and keep running.

        AC #1's letter and *Judgment call #7*'s named trade-off: such a session
        holds an IBKR client id and a market-data line while being incapable of
        trading. Stopping instead would reproduce the "stopped means two
        different things" defect ``deferred-work.md`` already logs. This record
        plus ``runtime_flags`` make the state visible rather than silent.
        """
        if self._all_failed_reported or not self.all_failed:
            return
        self._all_failed_reported = True
        self._log.error(
            ALL_FAILED_EVENT,
            strategies=[failure.spec_strategy_id for failure in self._failures],
            detail=(
                "every strategy in this session has been contained; the session is still "
                "running and still holds its broker connection, but it can no longer trade"
            ),
        )

    def _report_guard_failure(self, spec_strategy_id: str, exc: BaseException) -> None:
        """AR42's last resort: the containment machinery itself broke.

        The inner ``except`` is not defensive theatre. If the logger is what is
        broken — the third failure mode AC #10 names — then reporting the
        report would raise too, and that raise would leave a wrapped handler.
        There is nowhere left to report to, so this returns.
        """
        try:
            # `spec_strategy_id`, not `strategy_id`: everywhere else in this
            # module `strategy_id` is the Nautilus id, and a log consumer
            # correlating on it must not get mixed semantics here (review fix,
            # 2026-08-23).
            self._log.error(
                GUARD_FAILED_EVENT,
                spec_strategy_id=spec_strategy_id,
                error_type=type(exc).__name__,
            )
        except Exception:  # noqa: BLE001 - the log sink itself is the failure
            return


def _read_strategy_id(strategy: Any) -> str:
    """The Nautilus id at failure time, or ``""`` if there isn't one to read.

    Reading it can raise — it is a property on a cdef class — and this is
    called from inside the boundary, so it fails soft. An empty id costs the
    operator a field; a raise here costs them the process.
    """
    if strategy is None:
        return ""
    try:
        return str(strategy.id)
    except Exception:  # noqa: BLE001 - AC #10
        return ""


def _one_redacted_line(exc: BaseException, *, account: str | None = None) -> str:
    """The exception's first message line, redacted, capped for the column.

    Redacted **before** capping: capping first could cut an account token short
    of the six digits the pattern needs and leave a partial identifier visible.
    """
    try:
        message = str(exc)
    except Exception:  # noqa: BLE001 - a __str__ that raises must not escape
        message = ""
    first_line = message.splitlines()[0] if message else ""
    return redact_accounts(first_line, account=account)[:MAX_DETAIL_CHARS]


def _redacted_traceback(exc: BaseException, *, account: str | None = None) -> str:
    """The rendered traceback with every account token masked in place."""
    try:
        rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:  # noqa: BLE001 - AC #10
        rendered = f"{type(exc).__name__}: <traceback unavailable>"
    return redact_accounts(rendered, account=account)
