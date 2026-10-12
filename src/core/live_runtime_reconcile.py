"""Keep a running session's cache aligned with the broker (Story 4.3, FR34, FR35).

Owns: the verified position cycle the steady-state tick drives, the reconnect
re-establishment that alone returns trading permission after a loss (NFR10),
and the startup grant the runner calls at the end of ``reconcile``. Does not own:
the tick's cadence (``live_session_steady_state``), what a connection reading
means (``live_connection_monitor``), the broker read (``live_broker_state``), the
comparison (``src.models.position_reconciliation``) or the startup phase
(``live_startup_reconcile``, whose report builder and record helper this reuses).

**Why a cycle of ours, resolving through the framework** (decision D-A, PO
rulings 1A 2026-09-27). Nautilus 1.220.0's only continuous reconciliation is its
in-flight order sweep — configured for the whole session by the node builder,
native, and left to do its job. Nothing native re-checks *positions*: the IB
adapter's own position-update reports are switched off
(:mod:`src.core.live_exec_position_reports`; they produced a phantom round trip
on every entry and never reported a position going flat), and the open-order
consistency check stays off (it republishes an ``OrderAccepted`` forever for a
locally-cancelled order IB still lists open). So this module compares the cache
with Story 4.1's broker read and hands every correction to the framework's own
public ``exec_engine.reconcile_execution_report`` — the Story 4.2 D-E shape. It
never purges or writes the cache, never calls an order method, never trusts
local state over the broker, and never reads ``trading_permitted``.

**One cycle** (D-C, as amended by PO ruling 2A): snapshot the cache → read the
broker with the reader's own per-read records dropped (they would be ~390 INFO
lines a day) → re-snapshot, and **skip** the cycle if the cache moved (a fill
landed) → compare exactly, **deferring** only instruments with an order still in
flight (``SUBMITTED`` / ``PENDING_*``; an ordinary open order does not defer —
an ``ACCEPTED`` order stranded across a restart stays open forever, measured) →
act only on a row seen **identically** on consecutive completed cycles at least
:data:`DEBOUNCE_SECONDS` apart. That debounce absorbs a broker position that
lags the session's own fill, and the reverse.

**What a confirmed row does.** A row where no strategy's own position is
contradicted is corrected broker-ward and logged ``reconcile.discrepancy
resolution="broker"`` once the re-read proves it took. A row the framework
cannot express (an instrument the cache does not hold, an unresolved broker
row) is logged ``resolution="unresolved"`` once and the session continues — no
strategy can act on an instrument the cache lacks, so such a row never withholds
the reconnect grant. An unresolved broker row under a *held* symbol — one a
cached position is held under, or one an instrument the session's strategies
trade is (code review R1, PO ruling 2026-10-07) — does withhold it (fail
closed); any other is named and left out (Story 5.7, PO ruling D2), inert from
its first sighting rather than once debounced (code review P4) — and while a
matching one stands only the row it could be
masking — the cache's "broker 0" row for the **same symbol** — is held back, and
only for :data:`HOLD_BACK_CYCLES` consecutive cycles; past that the holding is
one the adapter will never resolve (``reconcile.hold_back_expired``, once) and
the held-back row is acted on, so a contradiction underneath it is refused
rather than masked for the life of the session. Every other instrument is
checked and corrected throughout (PO rulings: code review 2026-09-28; PR #35 D1,
2026-09-30). A broker quantity the instrument's size precision cannot express (a
DRIP's ``+22.5``) is judged at whole steps of ``10**-size_precision``, truncated
toward zero, and its remainder named once per standing value per instrument —
the same remainder beside the strategy's own trade is not news (code review P2)
— (``reconcile.inexpressible_remainder``, Story 5.7) and carried by every
``reconcile.ok`` while it stands. A strategy's own position
the broker does not cover — less in its direction, none, or the opposite side
(Story 4.7's coverage rule) — is **refused and the session stopped**, before
anything is written (D-D, PO ruling 2A), naming the likely cause: the
framework cannot rewrite a strategy's position, and a strategy believing it
holds shares the broker does not will close them on its next opposite signal
(NFR14). A strategy position the broker holds *more* of — a forward split, a
stock dividend — is an ordinary row: corrected broker-ward into a synthetic
owner, the strategy's own lot left exactly as it was (Story 4.7, PO ruling A).
A framework refusal, or a row that survives its correction, stops the session
the same way. The stop is the runner's ordinary teardown — positions at IBKR
are untouched.

**Latency, stated.** A change the execution stream did not explain is corrected
one to two cycles after it happens (~60–120 s at AR32's 30 s tick), not on
arrival. After a reconnect, a clean cycle grants on the first tick; a
discrepancy must debounce first, so the grant waits for it and the monitor's own
halt clock may report ``connection.halted`` meanwhile (NFR20).

**Volume** (AC #3): ``reconcile.ok`` on the first clean cycle, on the first
clean cycle after anything else, and otherwise at most hourly, each carrying
``cycles`` — the clean cycles it summarises. A failing read is one
``reconcile.cycle_failed`` WARNING per streak (re-logged at most hourly), never
one per cycle — plus the one ERROR below.

**A failure that persists is acted on** (Story 5.8, PO ruling PR #35 D3:
"withhold after 3, drift stops"). The :data:`WITHHOLD_AFTER_FAILED_CYCLES`-th
consecutive failed cycle withdraws trading permission through the monitor's
``RECOVERING`` (orders withheld, ``order.suppressed``) and logs one
``reconcile.cycle_failed`` at ERROR (``action="trading_withheld"``), past the
hourly throttle; the next clean cycle — a reconnect cycle, every tick — grants
it back through ``confirm_state_reestablished``, exactly as after a reconnect.
The period is kept in :class:`WithheldPeriods`, which the steady state's tick
writes to the session row so a seal can record it. Already ``RECOVERING`` after
a loss, there is nothing to withdraw, so no period and no ERROR (code review
P5): the outage owns that withhold, and the streak's WARNING throttle covers
it. Only the reader's own ``BrokerStateFailure`` reason is logged or persisted
(code review P4). Adapter drift (``BrokerStateAdapterError``) is never a streak: it is
logged once at ERROR (``action="session_stopped"``) and stops the session on
its first occurrence, exit 1, as at startup.

**Stranded orders** (Story 4.8). Each runtime cycle — never a reconnect cycle,
whose grant stays exactly as ruled — also runs
:class:`~src.core.live_stranded_orders.StaleOrderWatch`, skipping every
instrument with a row still standing or deferred: a cached open order IBKR has
stopped listing is reconciled ``CANCELED`` after two conclusive reads a
debounce apart, so a stale order no longer rides into every restart. It cannot
prevent or explain a stall inside ``node:connect``, which happens before any
cycle exists, and a namespace already poisoned heals only once a session runs
past the debounce with this code.
"""

import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from src.core.live_broker_state import FAILED_EVENT as READER_FAILED_EVENT
from src.core.live_broker_state import RETRIEVED_EVENT as READER_RETRIEVED_EVENT
from src.core.live_broker_state import (
    BrokerStateAdapterError,
    BrokerStateFailure,
    find_ib_exec_client,
    read_broker_state,
)
from src.core.live_connection_monitor import (
    ConnectionMonitor,
    ConnectionState,
    ConnectionStatus,
)
from src.core.live_startup_reconcile import (
    OK_EVENT,
    BrokerStateReader,
    ReconciliationFailedError,
    ReconciliationFailure,
    cached_positions,
    expressible_broker,
    inexpressible_remainders,
    log_discrepancy,
    log_remainder,
    position_report,
)
from src.core.live_stranded_orders import (
    OpenOrdersReader,
    StaleOrderWatch,
    broker_open_orders,
)
from src.models.broker_state import BrokerState
from src.models.position_reconciliation import (
    CachedPosition,
    InexpressibleRemainder,
    PositionDiscrepancy,
    compare_positions,
    count_synthetic,
    held_symbols,
    may_mask_a_cached_position,
    symbol_key,
)

SCOPE_RUNTIME = "runtime"
SCOPE_RECONNECT = "reconnect"

#: A runtime cycle every this many heartbeat ticks while ``CONNECTED`` — 60 s
#: at AR32's 30 s tick. A reconnect cycle runs on every tick instead.
RUNTIME_RECONCILE_EVERY_TICKS = 2
#: A disagreement acts only once seen identically this long apart (PO ruling 2A).
DEBOUNCE_SECONDS = 60.0
#: ``reconcile.ok`` (and a persisting ``reconcile.cycle_failed``) at most this often.
RUNTIME_OK_LOG_INTERVAL_SECONDS = 3600.0
#: An unresolved broker row holds back the same-symbol "broker 0" cache row for
#: at most this many consecutive cycles (~3 minutes) before the row is acted on
#: (PO ruling, PR #35 D1, 2026-09-30).
HOLD_BACK_CYCLES = 3
#: Story 5.8 (AC #4; PO ruling PR #35 D3, "withhold after 3"): this many
#: consecutive failed cycles withdraw trading permission through the monitor's
#: ``RECOVERING``; the next clean cycle grants it back, as after a reconnect.
WITHHOLD_AFTER_FAILED_CYCLES = 3

CYCLE_FAILED_EVENT = "reconcile.cycle_failed"
#: ``reconcile.cycle_failed``'s ``action`` on its two ERROR records (Story 5.8):
#: a streak reached the threshold, or adapter drift stopped the session.
ACTION_TRADING_WITHHELD = "trading_withheld"
ACTION_SESSION_STOPPED = "session_stopped"
CONNECTION_READ_FAILED_EVENT = "session.connection_read_failed"
#: D1: an unresolved row outlived the hold-back; its same-symbol cache row is now acted on.
HOLD_BACK_EXPIRED_EVENT = "reconcile.hold_back_expired"

ConnectionReader = Callable[[Any], ConnectionStatus]

_CONTRADICTED = (
    "the broker no longer covers a strategy's own position (it holds fewer shares, none, or the "
    "opposite side) while the session was running. IBKR is "
    "authoritative and reconciliation cannot rewrite a strategy's own position, so nothing was "
    "written to the cache and the session was stopped rather than let a strategy trade on a "
    "position the broker does not cover; positions at IBKR were not touched. Restarting this "
    "session meets the same check at startup: create a new session for the strategy, or see "
    "docs/agent/nautilus.md, 'Startup reconciliation', for clearing its disposable engine cache"
)
_REMAINS = "after the correction, the cache still disagrees with the broker"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Quiet:
    """The broker reader's logger for a cycle (F9): its two per-read records —
    ``reconcile.broker_state_retrieved`` (INFO) and
    ``reconcile.broker_state_failed`` (ERROR), ~390 lines a day at 60 s — are
    dropped. Everything else reaches the session log, such as Story 4.1 D-F's
    ``reconcile.broker_instrument_unresolved`` WARNING, the only record that
    names an unresolved contract's symbol and error type (code review of PR
    #35: the first version dropped every level). Never raises."""

    _DROPPED = frozenset({READER_RETRIEVED_EVENT, READER_FAILED_EVENT})

    def __init__(self, log: Any) -> None:
        self._log = log

    def _forward(self, level: str, event: str, *args: Any, **kwargs: Any) -> None:
        if event in self._DROPPED:
            return None
        try:
            getattr(self._log, level)(event, *args, **kwargs)
        except Exception:  # noqa: BLE001 - diagnostics must not change the outcome
            pass
        return None

    def debug(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._forward("debug", event, *args, **kwargs)

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._forward("info", event, *args, **kwargs)

    def warning(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._forward("warning", event, *args, **kwargs)

    def error(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._forward("error", event, *args, **kwargs)

    def exception(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._forward("exception", event, *args, **kwargs)


@dataclass(frozen=True, slots=True)
class _Observation:
    """One completed read-and-compare: the broker, the rows still to answer
    for, and the instruments deferred for an in-flight order.

    Story 5.7 (D-D): ``broker`` is the exact read — what ``reconcile.ok``
    reports; ``expressible`` is the same read at each instrument's expressible
    precision — what every comparison, correction and re-verify judges
    against; ``remainders`` is what that left out; ``held`` the cached
    positions' symbols, for the same-symbol rule — joined with the traded
    instruments' by :func:`_unresolved_rows` (code review R1)."""

    broker: BrokerState
    expressible: BrokerState
    rows: tuple[PositionDiscrepancy, ...]
    deferred: tuple[str, ...]
    started: float
    remainders: tuple[InexpressibleRemainder, ...]
    held: frozenset[str]


def grant_after_reconciliation(
    monitor: ConnectionMonitor,
    connection_reader: ConnectionReader,
    settings: Any,
    log: Any,
) -> ConnectionState | None:
    """Grant trading permission right after a genuine reconciliation (D-F).

    The runner calls this at the end of the ``reconcile`` phase, once
    ``reconcile_at_startup`` has proved the cache matches the broker (Epic 1
    retro Action Item #7: the only grant follows a real reconciliation).
    ``confirm_state_reestablished`` folds the fresh reading in first, so the
    grant is atomic with a live reading. A refusal is not a phase failure:
    submission stays withheld, and the running session's reconnect cycle grants
    once the reading turns connected.

    Returns:
        The monitor's state after the attempt, or ``None`` when the reading
        itself raised (logged, contained — a probe fault must not fail a start
        that has already cleared both gates and reconciled).
    """
    try:
        status = connection_reader(settings)
    except Exception as exc:  # noqa: BLE001 - AR42: must not abort a reconciled start
        _emit(log, "error", CONNECTION_READ_FAILED_EVENT, error_type=type(exc).__name__)
        return None
    return monitor.confirm_state_reestablished(status)


class RuntimeReconciler:
    """The steady-state tick's reconciliation step. One public coroutine.

    Args:
        node: The running ``TradingNode`` — ``cache`` and
            ``kernel.exec_engine`` are what the cycle reads and corrects through.
        monitor: The session's connection state machine; read for the state,
            and granted through only after a clean reconnect cycle.
        log: The session-bound logger.
        connection_reader: The runner's connection seam, for the grant's own
            fresh reading.
        settings: Passed to ``connection_reader``.
        read_state: The broker read — Story 4.1's ``read_broker_state``.
        clock: Monotonic seconds, for the debounce, the throttle and
            ``elapsed_ms``.
        read_open_orders: The open-orders read behind the stale-order check
            (Story 4.8) — ``broker_open_orders``.
        traded_instruments: The instrument ids the session's strategies trade;
            their symbols count as held for an unresolved row (code review R1).
        wall_clock: Aware UTC time, for the withheld periods' persisted
            stamps only (Story 5.8) — every interval stays on ``clock``.

    Attributes:
        withheld: Story 5.8's :class:`WithheldPeriods` — the periods this
            reconciler withheld trading for, which the steady state's tick
            writes through the record port.
    """

    def __init__(
        self,
        *,
        node: Any,
        monitor: ConnectionMonitor,
        log: Any,
        connection_reader: ConnectionReader,
        settings: Any,
        read_state: BrokerStateReader = read_broker_state,
        clock: Callable[[], float] = time.monotonic,
        read_open_orders: OpenOrdersReader = broker_open_orders,
        traded_instruments: Collection[str] = (),
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._node = node
        self._monitor = monitor
        self._log = log
        self._connection_reader = connection_reader
        self._settings = settings
        self._read_state = read_state
        self._clock = clock
        self._ticks = 0
        self._recovering = False
        self._debounce = _Debounce()
        self._unresolved = _Unresolved()
        self._remainders = _Remainders()
        self._hold_back = _HoldBack()
        self._ok = _OkLog()
        self.withheld = WithheldPeriods(wall_clock)
        self._failures = _FailureStreak(monitor, self.withheld)
        self._stale_orders = StaleOrderWatch(read_open_orders)
        self._traded = held_symbols((), traded_instruments)

    @property
    def unreported_clean_cycles(self) -> int:
        """Clean cycles not yet folded into a ``reconcile.ok`` record."""
        return self._ok.clean

    async def on_tick(self) -> None:
        """One heartbeat tick, called right after the tick observes the connection.

        Raises:
            ReconciliationFailedError: A confirmed strategy-position
                contradiction, a framework refusal, or a correction that did
                not take — the session must stop (D-D).
            BrokerStateAdapterError: Adapter drift, on its first occurrence —
                the session must stop, exit 1, as at startup (Story 5.8).
                Nothing else leaves.
        """
        state = self._monitor.state
        if state is ConnectionState.RECOVERING:
            if not self._recovering:
                # A loss is "anything else": a row seen before it must be seen
                # twice again after it, and the reconnect cycle reports at once.
                # Once per loss — the monitor may pass RECOVERING → HALTED →
                # RECOVERING with the socket up, and that must not restart it.
                self._recovering = True
                self._debounce = _Debounce()
                self._stale_orders.forget()
                self._ok.needed = True
            await self._run(SCOPE_RECONNECT)
        elif state is ConnectionState.CONNECTED:
            self._recovering = False
            self._ticks += 1
            if self._ticks % RUNTIME_RECONCILE_EVERY_TICKS == 0:
                await self._run(SCOPE_RUNTIME)

    async def _run(self, scope: str) -> None:
        try:
            observation = await observe(self._node, self._read_state, self._clock, self._log)
            if observation is None:
                self._ok.needed = True  # the cache moved during the read: no action, no progress
                return
            outstanding = self._act(observation, scope)
            self._failures.count = 0
            self._ok.deferred.update(observation.deferred)
            if scope == SCOPE_RUNTIME:
                # Story 4.8: an order on an instrument with a row still standing
                # waits — an inert row's too (Story 5.7, D-H).
                skip = {row.instrument_id for row in observation.rows} | set(observation.deferred)
                await self._stale_orders.check(
                    self._node, self._log, self._clock(), skip=skip, scope=scope
                )
            if outstanding:
                self._ok.needed = True
                return
            self._ok.clean += 1
            if scope == SCOPE_RECONNECT:
                self._grant(observation)
            elif self._ok.due(self._clock()):
                self._ok.emit(self._log, self._node, observation, scope, self._clock())
        except ReconciliationFailedError:
            raise
        except BrokerStateAdapterError as drift:
            # Story 5.8 (AC #2, D-C): drift stops the session on its first
            # occurrence, as at startup — never a streak, never throttled.
            _log_drift(self._log, scope, drift, self._failures.count + 1)
            raise
        except Exception as exc:  # noqa: BLE001 - AR42: a cycle must never kill a session
            self._ok.needed = True
            self._failures.note(self._log, scope, exc, self._clock())

    def _act(self, observation: _Observation, scope: str) -> list[PositionDiscrepancy]:
        """Act on the confirmed rows; return the rows that still stand between
        this cycle and clean — all but those the framework cannot express and
        the unresolved rows under no held symbol (Story 5.7; R1, P4)."""
        self._remainders.log(self._log, observation.remainders, scope)
        confirmed = self._debounce.confirm(observation.rows, self._clock())
        lookup_misses = [row for row in confirmed if not row.broker_resolved]
        actionable = [row for row in confirmed if row.broker_resolved]
        # An `IB-CONID-*` row may be the holding a cache row reads as "broker
        # 0": hold back only the cache row with the *same symbol*, and only for
        # `HOLD_BACK_CYCLES` consecutive cycles — a lookup miss then neither
        # stops the session nor blinds any other instrument, and a holding the
        # adapter can never resolve stops masking a real contradiction (PO
        # rulings: code review 2026-09-28, PR #35 D1 2026-09-30). Only a row
        # under a held symbol is counted (Story 5.7, D-G; code review R1).
        may_mask, harmless = _unresolved_rows(observation, self._traded)
        masking = self._hold_back.masking(may_mask, self._log, scope)
        if masking:
            actionable = [
                row
                for row in actionable
                if not (row.broker_quantity == 0 and row.symbol_key in masking)
            ]
        contradicted = [row for row in actionable if row.strategy_contradicted]
        if contradicted:
            refuse(
                self._log, ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED, contradicted, scope
            )
        corrected, unresolvable = correct(
            self._node, actionable, observation.expressible, self._log, scope
        )
        self._unresolved.log(self._log, [*lookup_misses, *unresolvable], observation.rows, scope)
        if corrected:
            verify_corrected(self._node, corrected, observation.expressible, self._log, scope)
        # No strategy can act on an instrument the cache does not hold, so such a
        # row never withholds a grant; a lookup miss under a held symbol still
        # does (fails closed), and any other — from its first sighting, never
        # acted on so never debounced (code review P4) — does not (Story 5.7).
        inert = {*unresolvable, *harmless}
        return [row for row in observation.rows if row not in inert]

    def _grant(self, observation: _Observation) -> None:
        """Clean, and nothing in flight: the state check passed — only now grant.

        ``reconcile.ok`` precedes the attempt so it lands before the monitor's
        ``connection.restored``; a refused attempt does not repeat it next tick.
        A grant that took ends a period this reconciler withheld (Story 5.8).
        """
        if observation.deferred:
            return
        if self._ok.needed:
            self._ok.emit(self._log, self._node, observation, SCOPE_RECONNECT, self._clock())
        state = grant_after_reconciliation(
            self._monitor, self._connection_reader, self._settings, self._log
        )
        if state is ConnectionState.CONNECTED:
            self.withheld.end()


async def observe(
    node: Any, read_state: BrokerStateReader, clock: Callable[[], float], log: Any
) -> _Observation | None:
    """Read the broker and compare, or ``None`` when the cache moved meanwhile.

    The reader's two per-read records are dropped (F9, :class:`_Quiet`);
    instruments with an order still in flight are deferred (PO ruling 2A) —
    the in-flight sweep owns them.
    """
    cache = node.cache
    started = clock()
    before = _fingerprint(cached_positions(cache))
    broker = await read_state(node, log=_Quiet(log))
    cached = cached_positions(cache)
    if _fingerprint(cached) != before:
        return None
    judged, remainders = expressible_broker(cache, broker)  # Story 5.7, D-D
    deferred = sorted({str(order.instrument_id) for order in cache.orders_inflight()})
    rows = tuple(
        row for row in compare_positions(cached, judged) if row.instrument_id not in deferred
    )
    return _Observation(
        broker, judged, rows, tuple(deferred), started, remainders, held_symbols(cached)
    )


def _unresolved_rows(
    observation: _Observation, traded: frozenset[str]
) -> tuple[list[PositionDiscrepancy], list[PositionDiscrepancy]]:
    """Every unresolved row this observation saw — raw, not debounced (code
    review P4) — as ``(may_mask, harmless)``.

    ``may_mask``: under a held symbol — a cached position's or a traded
    instrument's (Story 5.7, D-A; code review R1). Only these keep D1's
    hold-back and withhold a grant. ``harmless``: every other, inert from its
    first sighting: it is never acted on, so there is nothing to debounce, and
    judging it from the debounced rows withheld a reconnect grant for a window
    — or, flickering, forever (AC #2).
    """
    held = observation.held | traded
    unresolved = [row for row in observation.rows if not row.broker_resolved]
    may_mask = [row for row in unresolved if may_mask_a_cached_position(row, held)]
    return may_mask, [row for row in unresolved if row not in may_mask]


def correct(
    node: Any,
    rows: Sequence[PositionDiscrepancy],
    broker: BrokerState,
    log: Any,
    scope: str,
) -> tuple[list[PositionDiscrepancy], list[PositionDiscrepancy]]:
    """Hand the framework the broker's truth for each confirmed row.

    Returns:
        ``(corrected, unresolvable)`` — the rows handed over, and the rows the
        framework cannot be told about (an instrument the cache does not hold,
        a quantity or price the instrument cannot represent).

    Raises:
        ReconciliationFailedError: ``RESOLUTION_REFUSED`` — the framework
            returned ``False`` or raised. Rows handed over before it stay
            corrected, broker-ward, the only direction this module writes.
    """
    if not rows:
        return [], []
    cache = node.cache
    held = {position.instrument_id: position for position in broker.positions}
    account_id = find_ib_exec_client(node).account_id
    engine = node.kernel.exec_engine
    corrected: list[PositionDiscrepancy] = []
    unresolvable: list[PositionDiscrepancy] = []
    for row in rows:
        report = position_report(row, held.get(row.instrument_id), cache, account_id)
        if report is None:
            unresolvable.append(row)
            continue
        try:
            accepted = engine.reconcile_execution_report(report) is True
            failure = "" if accepted else "it returned False"
        except Exception as exc:  # noqa: BLE001 - any engine failure is this refusal
            accepted, failure = False, f"it raised {type(exc).__name__}"
        if not accepted:
            # The rows before this one already wrote the cache: record those that took.
            _log_taken(node, corrected, broker, log, scope)
            refuse(
                log,
                ReconciliationFailure.RESOLUTION_REFUSED,
                [row],
                scope,
                f"Nautilus refused to reconcile {row.instrument_id} to the broker's position "
                f"({failure})",
            )
        corrected.append(row)
    return corrected, unresolvable


def verify_corrected(
    node: Any,
    corrected: Sequence[PositionDiscrepancy],
    broker: BrokerState,
    log: Any,
    scope: str,
) -> None:
    """Re-read the cache; log each correction only once it provably took.

    Raises:
        ReconciliationFailedError: ``DISCREPANCY_REMAINS`` — a correction the
            framework accepted without acting on.
    """
    remaining = _log_taken(node, corrected, broker, log, scope)
    if remaining:
        refuse(log, ReconciliationFailure.DISCREPANCY_REMAINS, remaining, scope, _REMAINS)


def _log_taken(
    node: Any,
    corrected: Sequence[PositionDiscrepancy],
    broker: BrokerState,
    log: Any,
    scope: str,
) -> list[PositionDiscrepancy]:
    """Log ``resolution="broker"`` for each correction the re-read proves took —
    even when the cycle then stops — and return the rows that did not."""
    cached = cached_positions(node.cache)
    still = {row.instrument_id: row for row in compare_positions(cached, broker)}
    for row in corrected:
        if row.instrument_id not in still:
            log_discrepancy(log, row, "broker", scope=scope)
    return [still[row.instrument_id] for row in corrected if row.instrument_id in still]


def refuse(
    log: Any,
    reason: ReconciliationFailure,
    rows: Sequence[PositionDiscrepancy],
    scope: str,
    detail: str = _CONTRADICTED,
) -> None:
    """Log every row refused, then stop the session (D-D). Never returns."""
    for row in rows:
        log_discrepancy(log, row, "refused", reason=reason, scope=scope)
    raise ReconciliationFailedError(reason, detail, rows, scope=scope)


class _Debounce:
    """A row acts only when seen identically on the previous completed cycle,
    at least :data:`DEBOUNCE_SECONDS` earlier (PO ruling 2A)."""

    def __init__(self) -> None:
        #: instrument → (row, first seen)
        self.pending: dict[str, tuple[PositionDiscrepancy, float]] = {}

    def confirm(self, rows: Sequence[PositionDiscrepancy], now: float) -> list[PositionDiscrepancy]:
        pending: dict[str, tuple[PositionDiscrepancy, float]] = {}
        confirmed = []
        for row in rows:
            seen = self.pending.get(row.instrument_id)
            first = seen[1] if seen is not None and seen[0] == row else now
            pending[row.instrument_id] = (row, first)
            if now - first >= DEBOUNCE_SECONDS:
                confirmed.append(row)
        self.pending = pending
        return confirmed


class _Unresolved:
    """``resolution="unresolved"`` once per identical row; a row that clears
    and returns is logged again."""

    def __init__(self) -> None:
        self.logged: set[PositionDiscrepancy] = set()

    def log(
        self,
        log: Any,
        rows: Sequence[PositionDiscrepancy],
        observed: Sequence[PositionDiscrepancy],
        scope: str,
    ) -> None:
        for row in rows:
            if row not in self.logged:
                log_discrepancy(log, row, "unresolved", scope=scope)
        # Forget only rows the broker no longer shows, not rows merely still debouncing.
        self.logged = {row for row in self.logged if row in observed} | set(rows)


class _Remainders:
    """``reconcile.inexpressible_remainder`` once per standing value per
    instrument (Story 5.7, D-F): a remainder that changes — another DRIP lands
    — or clears and returns is a new fact and is named again. Keyed on
    ``(instrument, remainder)``, not the whole record (code review P2): a
    strategy buying a share moves the broker 22.5 -> 23.5 and leaves the same
    0.5 standing, which is not news (P22b's Fail criterion). No debounce: it is
    an observation, never acted on."""

    def __init__(self) -> None:
        self.named: set[tuple[str, Any]] = set()

    def log(self, log: Any, remainders: Sequence[InexpressibleRemainder], scope: str) -> None:
        standing = {(r.instrument_id, r.remainder): r for r in remainders}
        for key, remainder in standing.items():
            if key not in self.named:
                log_remainder(log, remainder, scope=scope)
        self.named = set(standing)


class _HoldBack:
    """Which symbols an unresolved broker row may still be masking (D1).

    Counts the consecutive cycles each unresolved symbol has been observed;
    while the count is within :data:`HOLD_BACK_CYCLES` the cache's "broker 0"
    row for that symbol is held back. Past it the row is treated as a holding
    the adapter will never resolve — logged once as ``reconcile.hold_back_expired``
    — and the held-back row is acted on (refused if contradicted, fail closed).
    A symbol absent from one observation starts over."""

    def __init__(self) -> None:
        self.cycles: dict[str, int] = {}

    def masking(self, observed: Sequence[PositionDiscrepancy], log: Any, scope: str) -> set[str]:
        present = {
            symbol_key(row.broker_symbol): row for row in observed if not row.broker_resolved
        }
        cycles = {key: self.cycles.get(key, 0) + 1 for key in present}
        for key, count in cycles.items():
            if count == HOLD_BACK_CYCLES + 1:
                _emit(
                    log,
                    "warning",
                    HOLD_BACK_EXPIRED_EVENT,
                    scope=scope,
                    instrument_id=present[key].instrument_id,
                    symbol=present[key].broker_symbol,
                    cycles=HOLD_BACK_CYCLES,
                )
        self.cycles = cycles
        return {key for key, count in cycles.items() if count <= HOLD_BACK_CYCLES}


class _OkLog:
    """AC #3: ``reconcile.ok`` on the first clean cycle, the first clean cycle
    after anything else, and otherwise at most hourly — each record carrying
    the clean cycles it summarises."""

    def __init__(self) -> None:
        self.clean = 0
        self.needed = True
        self.last_at: float | None = None
        #: every instrument deferred since the last record, so none goes unnamed
        self.deferred: set[str] = set()

    def due(self, now: float) -> bool:
        if self.needed or self.last_at is None:
            return True
        return now - self.last_at >= RUNTIME_OK_LOG_INTERVAL_SECONDS

    def emit(self, log: Any, node: Any, observation: _Observation, scope: str, now: float) -> None:
        cache = node.cache
        broker = observation.broker
        _emit(
            log,
            "info",
            OK_EVENT,
            scope=scope,
            account=broker.account,
            positions=len(broker.positions),
            instruments={p.instrument_id: str(p.quantity) for p in broker.positions},
            cycles=self.clean,
            deferred_instruments=sorted(self.deferred | set(observation.deferred)),
            synthetic_positions=count_synthetic(cached_positions(cache)),
            inexpressible_remainders=inexpressible_remainders(observation.remainders),
            open_orders=len(cache.orders_open()),
            elapsed_ms=round((now - observation.started) * 1000, 3),
        )
        self.clean, self.needed, self.last_at = 0, False, now
        self.deferred = set()


class _FailureStreak:
    """One ``reconcile.cycle_failed`` per streak, re-logged at most hourly —
    and, at :data:`WITHHOLD_AFTER_FAILED_CYCLES`, one at ERROR, past the
    throttle, that withdraws trading permission (Story 5.8, AC #1). Only the
    failure's type and the reader's own reason — never ``str(exc)``, which
    may carry the raw account (NFR26)."""

    def __init__(self, monitor: ConnectionMonitor, withheld: "WithheldPeriods") -> None:
        self.count = 0
        self.logged_at = 0.0
        self._monitor = monitor
        self._withheld = withheld

    def note(self, log: Any, scope: str, exc: Exception, now: float) -> None:
        self.count += 1
        # Code review P4: only the reader's own reason — a third party's
        # `.reason` is free text, and this one is persisted too (NFR26).
        raw_reason = getattr(exc, "reason", None)
        reason = raw_reason.value if isinstance(raw_reason, BrokerStateFailure) else None
        error_type = type(exc).__name__
        fields = {
            "scope": scope,
            "reason": reason,
            "error_type": error_type,
            "consecutive": self.count,
        }
        # Code review P5: the ERROR only when this record withdrew permission;
        # a loss's own withhold (D-J) follows the WARNING throttle below.
        if self.count == WITHHOLD_AFTER_FAILED_CYCLES and self._withhold(reason, error_type):
            self.logged_at = now
            _emit(log, "error", CYCLE_FAILED_EVENT, **fields, action=ACTION_TRADING_WITHHELD)
            return
        if self.count > 1 and now - self.logged_at < RUNTIME_OK_LOG_INTERVAL_SECONDS:
            return
        self.logged_at = now
        _emit(log, "warning", CYCLE_FAILED_EVENT, **fields)

    def _withhold(self, reason: str | None, error_type: str) -> bool:
        """Withdraw a granted session's permission, and open a period for it.

        Withdrawn first, stamped second: the safe half never waits on the
        record. Already withheld — a loss's ``RECOVERING`` — this rule has
        nothing to withdraw and opens no period; the outage owns that
        withhold (D-J). Returns whether this call withdrew permission.
        """
        if self._monitor.state is not ConnectionState.CONNECTED:
            return False
        self._monitor.withhold_for_reconciliation()
        self._withheld.start(reason=reason, error_type=error_type, failed_cycles=self.count)
        return True


@dataclass(frozen=True, slots=True)
class WithheldPeriod:
    """One withdrawal of trading permission for a failing reconciliation
    (Story 5.8, D-E): when it began and why, and — once a clean cycle granted
    permission back — when it ended. Standard-library values only: it crosses
    the record port as they are (AR38)."""

    started_at: datetime
    reason: str | None
    error_type: str
    failed_cycles: int
    ended_at: datetime | None = None

    def as_port_kwargs(self) -> dict[str, Any]:
        """``SessionRecordPort.record_trading_withheld``'s keyword arguments."""
        return {
            "withheld_from": self.started_at,
            "withheld_until": self.ended_at,
            "reason": self.reason,
            "error_type": self.error_type,
            "failed_cycles": self.failed_cycles,
        }


class WithheldPeriods:
    """The withheld periods not yet written (Story 5.8, D-F).

    Opened by the streak's withdrawal, closed by the next grant that took, and
    read and acknowledged by the steady state's tick, which writes each through
    the record port. A dirty flag per period, not a queue: a newer version
    supersedes an unwritten older one — one write then carries both the start
    and the end — and acknowledging a superseded version leaves the newer one
    pending. A period a stop cuts short is closed by the session row's own
    ``-> stopped`` edge, never here (record-keeping only: nothing here grants
    or withholds anything).
    """

    def __init__(self, wall_clock: Callable[[], datetime] = _utc_now) -> None:
        self._wall_clock = wall_clock
        self._open: WithheldPeriod | None = None
        self._dirty: dict[datetime, WithheldPeriod] = {}

    def start(self, *, reason: str | None, error_type: str, failed_cycles: int) -> WithheldPeriod:
        """Open a period, stamped now."""
        period = WithheldPeriod(self._wall_clock(), reason, error_type, failed_cycles)
        self._open = self._dirty[period.started_at] = period
        return period

    def end(self) -> WithheldPeriod | None:
        """Close the open period, stamped now — never before its start, even
        across a wall-clock step back (code review P7); ``None`` when none is open."""
        if self._open is None:
            return None
        closed = replace(self._open, ended_at=max(self._wall_clock(), self._open.started_at))
        self._open = None
        self._dirty[closed.started_at] = closed
        return closed

    def pending(self) -> tuple[WithheldPeriod, ...]:
        """Every period whose newest version is not yet written, oldest first."""
        return tuple(self._dirty.values())

    def mark_written(self, period: WithheldPeriod) -> None:
        """Acknowledge ``period`` — only if it is still the newest version."""
        if self._dirty.get(period.started_at) == period:
            del self._dirty[period.started_at]


def _log_drift(log: Any, scope: str, drift: BrokerStateAdapterError, consecutive: int) -> None:
    """The one record of a drift stop (Story 5.8, AC #2, D-C): ERROR, never
    throttled. ``detail`` is the reader's own operator-safe text: it names
    contracts and raise sites, never the account or a third party's exception
    message (NFR26, pinned on both drift paths)."""
    _emit(
        log,
        "error",
        CYCLE_FAILED_EVENT,
        scope=scope,
        reason=drift.reason.value,
        error_type=drift.error_type or type(drift).__name__,
        consecutive=consecutive,
        detail=drift.detail,
        action=ACTION_SESSION_STOPPED,
    )


def _fingerprint(positions: Sequence[CachedPosition]) -> list[tuple[str, str, Any]]:
    return sorted((p.instrument_id, p.strategy_id, p.quantity) for p in positions)


def _emit(log: Any, level: str, event: str, **fields: Any) -> None:
    """Log without ever raising: a failing sink must not turn a clean cycle
    into a failure, nor replace the typed failure the tick is owed."""
    try:
        getattr(log, level)(event, **fields)
    except Exception:  # noqa: BLE001 - diagnostics must not change the outcome
        pass
