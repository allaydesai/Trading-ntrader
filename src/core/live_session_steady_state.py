"""What a started session does for the next 6.5 hours (Story 2.5, AR32).

Owns: the ~30s heartbeat loop and everything that rides on its tick — the
``last_heartbeat_at`` / ``last_bar_at`` write through the record port, the
connection-status observation the ``ConnectionMonitor`` needs, and the
first-bar watchdog — plus ``note_bar``, the message-bus handler that observes
bars without touching the database; :class:`StartupHeartbeat`, the writer that
covers the phases before the loop exists; and the two teardown policies the
runner's ``finally`` calls by name, :func:`join_heartbeat` and
:func:`release_record`.

Does not own: the startup sequence or the node's lifetime
(``src/core/live_session_runner.py``, which creates one of these and awaits
:meth:`SessionSteadyState.run` as a task), what a connection status *means*
(``src/core/live_connection_monitor.py``), or how the write reaches Postgres
(``src/services/session_record.py``, behind the port).

Split out of the runner because the runner would otherwise exceed this repo's
500-line file limit — the split line Story 2.5 pre-agreed, precisely so the
phase sequence and the teardown ``finally`` stay in one file for Story 2.6 to
attach to.

**Why an asyncio task and not a ``LiveClock`` timer.** Measured against
nautilus-trader 1.220.0, all four independently disqualifying:

- the callback runs on a foreign Rust thread (``Dummy-N``) where
  ``asyncio._get_running_loop()`` is ``None``, so loop-affine calls are unsafe;
- Nautilus documents no guarantee it is the *same* thread across firings, so
  thread-affine resources such as a DB connection are unsafe too;
- an exception raised in the callback is **silently swallowed** — measured: 5
  calls, process alive, no traceback — so a writer that starts failing fails
  invisibly;
- a blocked callback delays other timers and they fire as a catch-up burst, so
  the cadence is not what it looks like.

Plus ``structlog.contextvars`` are **empty** on that thread, so a record
emitted there would carry no ``session_id``. An ``asyncio.Task`` created after
``bind_contextvars`` inherits it — measured both ways.

**AR42's discipline, generalised.** A database hiccup must never kill a trading
session, so every step of a tick that touches the outside world (the record
write, the connection read) is guarded and the loop survives. There is exactly
one exception, and it is the opposite of a hiccup: an
``InvalidSessionTransition`` from ``record_activity`` means this process no
longer owns the session. That is fatal by design — see
:class:`SessionReclaimedError` in ``src/core/live_session_record.py``. The
injected seams (``time_source``, ``sleeper``, the logger) are deliberately not
guarded: they are this repo's own code or a test's, and wrapping them would
hide a defect rather than survive an outage.

Known, accepted limits, stated rather than implied:

1. A heartbeat proves a **process is writing**, not that it is trading. G1's
   ``degraded`` health is defined as *"running + heartbeat fresh but
   ``connection.lost`` flagged"*, which is only reachable because this loop
   keeps beating while disconnected. Do not gate it on connectivity.
2. ``last_bar_at`` is observed per bar and **persisted on the tick**, so its
   write lags by up to one interval. The recorded *value* is bar-accurate; only
   the write is batched. A per-bar round trip on the event-loop thread is the
   accumulating backlog NFR2 forbids.
3. The bar handler sees republished bars, which ``LiveBarObserver``'s own
   filter drops. For a liveness timestamp that is acceptable — a republished
   bar still means the feed is alive — but ``last_bar_at`` is therefore not
   "the time of a *new* bar".
4. The watchdog is **visibility only**. It logs once and changes nothing.
   Broker-authoritative subscription state is Epic 4's.
"""

import asyncio
import functools
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from src.config import IBKRSettings
from src.core.live_connection_monitor import ConnectionMonitor, ConnectionStatus
from src.core.live_connection_probe import read_ibkr_connection_status
from src.core.live_session_record import SessionReclaimedError, SessionRecordPort
from src.models.session import DEFAULT_HEARTBEAT_INTERVAL_SECONDS

#: How long a started session may go without a single bar before the watchdog
#: says so. No number for this exists anywhere in the PRD, the architecture or
#: the epics, so: five minutes is long enough that a 1-minute subscription has
#: had several chances and a 5-minute one has had one, and short enough that an
#: operator watching a start-up notices within a coffee. It is **visibility
#: only** — nothing changes state on it — so being wrong is cheap in both
#: directions, which is why a defensible number beats an agonised one.
DEFAULT_NO_BARS_AFTER_SECONDS = 300.0

#: Emitted once, at WARNING, when the watchdog window passes with no bar. Not
#: in AR41's enumeration; recorded for the Epic 2 retro along with
#: ``session.heartbeat_write_failed`` and ``session.reclaimed_by_another_process``.
NO_BARS_EVENT = "session.no_bars_observed"

ConnectionReader = Callable[[IBKRSettings], ConnectionStatus]
Sleeper = Callable[[float], Awaitable[None]]


class SessionSteadyState:
    """The heartbeat tick and the bar observation that feeds it.

    Args:
        record: AR32's record port. Bound to one session and one start instant
            at construction, so this object cannot write to the wrong row.
        settings: Loaded IBKR settings, for the connection reader's cache key.
        monitor: The connection state machine to feed. Constructed by the
            runner at ``node:connect`` so it exists before the first tick.
        log: A structlog logger already bound to ``session_id``.
        time_source: Returns the current aware ``datetime``. Injected, so a
            test drives both the stamped values and the watchdog window
            exactly.
        sleeper: How to wait one interval. Injected so a test can drive 780
            ticks without waiting 6.5 hours.
        interval_seconds: AR32's cadence. Defaults to the one shared constant;
            the runner's source contains no literal ``30``.
        no_bars_after_seconds: The watchdog window.
        connection_reader: Injected only so the component tier can drive the
            monitor without an adapter in ``IB_CLIENTS``.
        guard: Story 2.7's ``StrategyGuard``, whose queue this tick drains.
            Optional and defaulting to ``None`` so Story 2.5's and 2.6's
            construction sites keep working unmodified; a session without one
            simply has nothing to drain. Duck-typed to two members
            (``drain_pending()`` and ``all_failed``) rather than imported for a
            type, which keeps this module's dependency direction unchanged.
    """

    def __init__(
        self,
        *,
        record: SessionRecordPort,
        settings: IBKRSettings,
        monitor: ConnectionMonitor,
        log: Any,
        time_source: Callable[[], datetime],
        sleeper: Sleeper = asyncio.sleep,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        no_bars_after_seconds: float = DEFAULT_NO_BARS_AFTER_SECONDS,
        connection_reader: ConnectionReader = read_ibkr_connection_status,
        guard: Any = None,
    ) -> None:
        self._guard = guard
        self._record = record
        self._settings = settings
        self._monitor = monitor
        self._log = log
        self._time_source = time_source
        self._sleeper = sleeper
        self._interval_seconds = interval_seconds
        self._no_bars_after_seconds = no_bars_after_seconds
        self._read_connection = connection_reader
        self._bar_seen_at: datetime | None = None
        self._bars_seen = 0
        self._silence_reported = False
        self._started_at: datetime | None = None
        self.ticks = 0
        #: This loop's **own** thread pool for the record write — deliberately
        #: not the loop's default one, which `TradingNode` replaces with the
        #: kernel's and `dispose()` joins with `wait=True`. One worker: the
        #: writes are serial by construction, one per interval. See
        #: :meth:`_write_activity` (decision D2).
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="session-heartbeat-write"
        )

    @property
    def bars_seen(self) -> int:
        """How many bars the message bus has delivered. Never persisted."""
        return self._bars_seen

    def release_executor(self) -> None:
        """Let the write pool go **without waiting for it** (decision D2).

        ``wait=False`` is the whole point: a worker wedged in a socket read
        against a hung Postgres must not hold the teardown open, which is the
        failure this executor exists to escape. ``cancel_futures=True`` drops
        anything queued behind it; there is never more than one.

        Known residual, stated rather than implied: CPython joins thread-pool
        workers at interpreter exit, so a genuinely wedged write can still delay
        the *process* from exiting. What it can no longer delay is any of the
        work that matters — the node teardown, the ``-> stopped`` transition and
        the operator's report all complete first, where previously all three sat
        behind it. Closing that last gap needs the write bounded at the database
        (a ``statement_timeout``), which stays in ``deferred-work.md``.
        """
        self._executor.shutdown(wait=False, cancel_futures=True)

    def note_bar(self, message: object) -> None:
        """Message-bus handler for ``data.bars.*``. Does exactly one thing.

        Handlers run **inline and synchronously** inside
        ``MessageBus.publish_c`` on the event-loop thread, with no ``try``
        around ``sub.handler(msg)`` (``common/component.pyx:2739-2757``). So
        this must be trivial and must not raise: an exception here would
        propagate into the data engine's own dispatch. The two statements
        below cannot raise with the production clock (``datetime.now``); an
        *injected* ``time_source`` that raises would — that risk is the
        injector's, and no test may inject a raising clock here.
        """
        self._bar_seen_at = self._time_source()
        self._bars_seen += 1

    def take_bar_seen(self) -> datetime | None:
        """Return the bar instant observed since the last call, and clear it.

        Cleared rather than accumulated, so the object's memory is O(1) no
        matter how many bars arrive — which is what makes NFR2's "no
        accumulating backlog" a property of the shape rather than a hope.
        """
        seen, self._bar_seen_at = self._bar_seen_at, None
        return seen

    async def run(self) -> None:
        """Tick forever: sleep one interval, then do the tick's four jobs.

        Sleeps **first**. The ``-> running`` transition has already stamped
        ``last_heartbeat_at``, so writing again immediately would be a
        redundant round trip before the session has done anything.

        Raises:
            SessionReclaimedError: Another process owns this session now. The
                only exception the tick's guarded steps deliberately let out —
                see the module docstring for what is and is not guarded.
        """
        self._started_at = self._time_source()
        while True:
            await self._sleeper(self._interval_seconds)
            now = self._time_source()
            self.ticks += 1
            await self._write_activity(now)
            await self._write_strategy_failures()
            self._observe_connection()
            self._warn_if_no_bars(now)

    async def _write_strategy_failures(self) -> None:
        """Drain the strategy guard's queue and persist it (Story 2.7, AC #10).

        This is the *only* place a contained strategy failure reaches Postgres.
        The guard itself queues and returns, because ``handle_*`` runs inline on
        the event-loop thread inside ``MessageBus.publish_c`` — a round trip
        there stalls the loop and delays the bar for every later-subscribed
        strategy, which is a slower version of the starvation the guard exists
        to fix.

        Runs on the **same private executor** as :meth:`_write_activity` and for
        the same measured reason (decision D2): ``asyncio.to_thread`` resolves to
        the loop's default executor, which ``TradingNode.__init__`` replaces with
        the kernel's and ``dispose()`` joins with ``wait=True`` — 59.81s of
        blocked teardown on a wedged write. One worker, so this write and the
        heartbeat's are serial rather than racing for the same row.

        Bounded by construction: the guard latches, so this is at most one write
        per strategy per process run, not one per bar.

        Raises:
            SessionReclaimedError: Another process owns this session now — the
                one failure AR42 does not survive, surfaced *here* because there
                is a boundary here and none inside a wrapped ``handle_*``
                (*Judgment call #10*).
        """
        if self._guard is None:
            return
        pending = self._guard.drain_pending()
        if not pending:
            return
        all_failed = bool(self._guard.all_failed)
        loop = asyncio.get_running_loop()
        unwritten = []
        for failure in pending:
            write = functools.partial(
                self._record.record_strategy_failure,
                strategy_id=failure.strategy_id,
                spec_strategy_id=failure.spec_strategy_id,
                error_type=failure.error_type,
                handler=failure.handler,
                at=failure.at,
                detail=failure.detail,
                all_failed=all_failed,
            )
            try:
                await loop.run_in_executor(self._executor, write)
            except SessionReclaimedError:
                raise
            except Exception as exc:  # noqa: BLE001 - AR42: a DB hiccup must not kill a session
                # Re-queued for the next tick (review fix, 2026-08-23): the
                # guard latches per strategy, so the retry backlog is bounded
                # by the session's strategy count — not the unbounded state
                # NFR2 forbids — and dropping the record would blind AC #4's
                # cross-process visibility for the rest of a multi-week run
                # over a one-tick Postgres hiccup.
                unwritten.append(failure)
                self._log.error(
                    "session.strategy_record_failed",
                    spec_strategy_id=failure.spec_strategy_id,
                    error_type=type(exc).__name__,
                )
        if unwritten:
            self._guard.requeue(unwritten)

    async def _write_activity(self, now: datetime) -> None:
        """Persist the tick, surviving anything but a loss of ownership.

        ⚠️ **Runs on this object's own executor, never ``asyncio.to_thread``**
        (review fix, 2026-08-23, decision D2). ``to_thread`` resolves to the
        loop's **default** executor, and ``TradingNode.__init__`` installs the
        kernel's own ``ThreadPoolExecutor`` as that default
        (``kernel.py:268-270``), which ``dispose()`` then joins with
        ``wait=True, cancel_futures=True`` (``live/node.py:445-447``).

        That made AC #9's bounded teardown inert, measured end to end: cancelling
        the heartbeat task completes it *immediately* (``CancelledError`` is not
        caught below), so ``join_heartbeat`` returned in 0.00s and its timeout
        branch never ran — and the teardown then blocked **59.8s** inside
        ``dispose()`` on the very write the bound existed to escape. A private
        pool takes the write off the kernel's, so the node teardown, the
        ``-> stopped`` transition and the CLI's report all complete on time.
        """
        bar_seen_at = self.take_bar_seen()
        write = functools.partial(self._record.record_activity, at=now, bar_seen_at=bar_seen_at)
        try:
            await asyncio.get_running_loop().run_in_executor(self._executor, write)
        except SessionReclaimedError:
            raise
        except Exception as exc:  # noqa: BLE001 - AR42: a DB hiccup must not kill a session
            # The observed bar instant is deliberately *not* put back: a lost
            # heartbeat is survivable, and re-queuing the value across ticks
            # would grow unbounded state on the one path NFR2 measures.
            self._log.error(
                "session.heartbeat_write_failed",
                error_type=type(exc).__name__,
                bar_seen=bar_seen_at is not None,
            )

    def _observe_connection(self) -> None:
        """Feed the connection state machine. Closes ``deferred-work.md:550-555``.

        That item says verbatim that *"the same heartbeat AR32's
        ``last_heartbeat_at`` uses is the natural carrier"*. Both calls are
        synchronous and neither raises by contract — the reader is fail-closed
        by construction — but the guard is here anyway, because "by contract"
        is not "by test" for a third-party adapter's private flags.

        ⚠️ ``confirm_state_reestablished()`` is deliberately **never** called
        in this story. Epic 1 retro Action Item #7 requires its only production
        call site to run *after genuine reconciliation*, and ``reconcile`` is a
        no-op placeholder here. Granting permission stays Epic 4's.
        """
        try:
            self._monitor.observe(self._read_connection(self._settings))
        except Exception as exc:  # noqa: BLE001 - AR42
            self._log.error("session.connection_read_failed", error_type=type(exc).__name__)

    def _warn_if_no_bars(self, now: datetime) -> None:
        """Say so, **once**, when a started session has never seen a bar.

        The interim visibility measure Epic 1 retro Action Item #9 asked for,
        and the point at which four fragmented ``deferred-work.md`` entries
        converge. It changes nothing: no state, no stop. A session that
        receives no bars at all is still indistinguishable from a quiet market
        as far as the *broker* is concerned; only Epic 4's
        broker-authoritative subscription state can close that.
        """
        if self._silence_reported or self._bars_seen or self._started_at is None:
            return
        if (now - self._started_at).total_seconds() < self._no_bars_after_seconds:
            return
        self._silence_reported = True
        self._log.warning(
            NO_BARS_EVENT,
            no_bars_after_seconds=self._no_bars_after_seconds,
            remedy=(
                "the market may be closed (use_rth=True means no bar closes outside RTH), the "
                "account may lack a market-data subscription, or IBKR may never have qualified "
                "the contract — see the instrument shortfall logged at the subscribe phase"
            ),
        )


class StartupHeartbeat:
    """Keep ``last_heartbeat_at`` fresh while the startup phases run.

    Review fix (2026-08-21): the ``-> running`` claim stamps the heartbeat
    once, but the first steady-state write lands only after all eight phases —
    the connect budget alone defaults to 120s — plus one leading interval,
    which is past the 90s staleness threshold. In that window a parallel
    ``live start`` could *legitimately* reclaim a healthy, still-starting
    session (NFR6's two-processes shape), and even a failed duplicate moves
    ``last_started_at`` so the incumbent would kill itself at its first tick.
    This writer closes the window by writing every interval from the moment
    ``run()`` begins.

    A **thread**, not an asyncio task, out of necessity rather than taste: the
    long startup phases — ``node:build``'s synchronous connect attempt above
    all — run while the runner's loop is *not* running, so a task would not
    tick through exactly the window that matters. Story 2.5's objections to
    ``LiveClock`` timers do not carry over: this thread is our own, its
    exceptions are caught and logged rather than swallowed, every write opens
    its own short-lived DB session through the port, and its log records go
    through a logger already bound to ``session_id`` rather than relying on
    contextvars. Deviates from the story's "the heartbeat is created after the
    ``trading`` phase" placement — flagged for the Epic 2 retro alongside the
    review finding that forced it.

    Args:
        record: AR32's record port, bound to one session and start instant.
        log: A structlog logger already bound to ``session_id``.
        time_source: Returns the current aware ``datetime``.
        interval_seconds: AR32's cadence — the same constant the steady loop
            uses, so the two writers are indistinguishable in the column.
    """

    def __init__(
        self,
        *,
        record: SessionRecordPort,
        log: Any,
        time_source: Callable[[], datetime],
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._record = record
        self._log = log
        self._time_source = time_source
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="session-startup-heartbeat", daemon=True
        )
        #: The reclaim the writer observed, if any. The runner raises it before
        #: any strategy starts, so a session taken during startup never trades.
        self.reclaim: SessionReclaimedError | None = None

    def start(self) -> None:
        """Begin writing. Call once, before the first startup phase."""
        self._thread.start()

    @property
    def running(self) -> bool:
        """Whether the writer thread is alive."""
        return self._thread.is_alive()

    def stop(self, *, join_timeout_seconds: float = 5.0) -> None:
        """Signal the writer to end and wait for it, boundedly. Idempotent.

        The join is bounded because an in-flight write against a wedged
        database must not hold up the node teardown behind it; the thread is a
        daemon, so an abandoned one cannot hang interpreter exit.
        """
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=join_timeout_seconds)

    def _run(self) -> None:
        """Sleep one interval, write, repeat — until stopped or dispossessed.

        Sleeps first for the same reason the steady loop does: the claim
        already stamped ``last_heartbeat_at``, so an immediate write would be
        a redundant round trip. AR42 applies: an ordinary write failure is
        logged and the writer keeps going; only a loss of ownership ends it.
        """
        while not self._stop.wait(self._interval_seconds):
            try:
                self._record.record_activity(at=self._time_source())
            except SessionReclaimedError as exc:
                self.reclaim = exc
                self._log.error(
                    "session.reclaimed_by_another_process",
                    detail="detected during startup; no strategy will be started",
                )
                return
            except Exception as exc:  # noqa: BLE001 - AR42: a DB hiccup must not kill a start
                self._log.error(
                    "session.heartbeat_write_failed",
                    error_type=type(exc).__name__,
                    bar_seen=False,
                )


#: The bound on `join_heartbeat`'s wait, so an in-flight heartbeat write
#: against a wedged Postgres cannot hold the teardown open indefinitely
#: (Story 2.6, AC #9; `deferred-work.md`, story-2.5 review). No smaller than
#: `TradingNode`'s own `timeout_disconnection` (10.0s,
#: `live_node_builder.NODE_TIMEOUT_DISCONNECTION`) — this join runs *before*
#: that disconnect wait, and the two budgets are meant to feel the same order
#: of magnitude to an operator watching a stop, not race each other.
HEARTBEAT_JOIN_TIMEOUT_SECONDS: float = 10.0


def join_heartbeat(
    task: asyncio.Task | None,
    loop: asyncio.AbstractEventLoop,
    log: Any,
    *,
    timeout: float = HEARTBEAT_JOIN_TIMEOUT_SECONDS,
) -> bool:
    """Cancel the steady heartbeat task and wait for it, boundedly.

    Called from the runner's ``finally`` **before** ``shutdown``. The write
    itself no longer runs on the loop's default executor — see
    :meth:`SessionSteadyState._write_activity` — so ``dispose()`` can no longer
    be held open by an in-flight heartbeat; this join is what stops the *task*.
    Guarded throughout: anything raised here would skip the teardown behind it.

    Bounded with :func:`asyncio.wait` rather than :func:`asyncio.wait_for`:
    the task this cancels may be stuck inside an ``asyncio.to_thread`` worker
    already in a blocking socket read, which does not respond to
    ``Task.cancel()`` at all — ``wait`` simply returns once ``timeout``
    elapses regardless of whether the task ever finishes, where
    ``wait_for``'s own cancel-and-wait dance has no such guarantee.

    Returns:
        ``True`` when the task's outcome was a
        :class:`~src.core.live_session_record.SessionReclaimedError` (review
        fix, 2026-08-21) — which a bare ``gather(return_exceptions=True))``
        would otherwise let a caller discard. The caller folds this into its
        ownership flag so the final release leaves the successor's row alone.
        ``False`` on a timeout: the task's true outcome is now unknowable, and
        treating "abandoned" as "reclaimed" would wrongly skip the final
        release on a session nobody actually took.
    """
    if task is None or loop.is_closed():
        return False
    task.cancel()
    try:
        _done, pending = loop.run_until_complete(asyncio.wait({task}, timeout=timeout))
    except BaseException as exc:  # noqa: BLE001 - must never pre-empt shutdown
        log.error("session.heartbeat_join_failed", error_type=type(exc).__name__)
        return False
    if pending:
        log.error(
            "session.heartbeat_join_timeout",
            timeout_seconds=timeout,
            detail=(
                "the heartbeat task did not finish within the bound — likely blocked inside a "
                "socket read that cancellation cannot interrupt; abandoning the join and "
                "continuing the teardown rather than blocking it forever"
            ),
        )
        return False
    if task.cancelled():
        return False
    return isinstance(task.exception(), SessionReclaimedError)


def release_record(
    record: SessionRecordPort, log: Any, *, trader_id: str, ownership_lost: bool
) -> bool:
    """Mark the session ``stopped`` — unless it is no longer ours to mark.

    Runs **after** ``shutdown()`` returned, never before: committing
    ``stopped`` while the broker link is up opens a door the AR33 reclaim
    guard does not watch. Guarded, so a failure here cannot replace the
    outcome already in flight. Three shapes:

    - ``ownership_lost`` already known: skip the write entirely and say so.
    - The write itself refuses with the stop-path ownership guard (review fix,
      2026-08-21): the reclaim happened inside the detection window and this
      is the first this process hears of it. Leave the row to its new owner.
    - Anything else: log ``session.mark_stopped_failed`` and move on.

    Returns:
        ``True`` only for the third shape — a write that failed for a reason
        other than a reclaim (AC #9). The caller uses this to print an
        operator-visible warning on an otherwise-clean stop; a reclaim is not
        a failure of *this* process's stop, so it does not count.
    """
    detail = "the row belongs to another process now and is left untouched; up to one heartbeat "
    detail += "interval of overlap is possible — there is no fencing token on trading_sessions."
    if ownership_lost:
        log.error("session.reclaimed_by_another_process", trader_id=trader_id, detail=detail)
        return False
    try:
        record.mark_stopped()
    except SessionReclaimedError:
        log.error("session.reclaimed_by_another_process", trader_id=trader_id, detail=detail)
        return False
    except Exception as exc:  # noqa: BLE001 - must never replace the primary outcome
        log.error("session.mark_stopped_failed", error_type=type(exc).__name__)
        return True
    return False
