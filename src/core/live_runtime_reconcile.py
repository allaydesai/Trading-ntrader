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
the reconnect grant. An unresolved broker row does withhold it (fail closed), and
while one stands only the rows it could be masking — the cache's rows reading
"broker 0" — are held back; every other instrument is still checked and
corrected (PO ruling, code review 2026-09-28). A strategy's own position
the broker contradicts is **refused and the session stopped**, before anything
is written (D-D, PO ruling 2A): the framework cannot rewrite a strategy's
position, and a strategy believing it holds shares the broker does not will
close them on its next opposite signal (NFR14). A framework refusal, or a row
that survives its correction, stops the session the same way. The stop is the
runner's ordinary teardown — positions at IBKR are untouched.

**Latency, stated.** A change the execution stream did not explain is corrected
one to two cycles after it happens (~60–120 s at AR32's 30 s tick), not on
arrival. After a reconnect, a clean cycle grants on the first tick; a
discrepancy must debounce first, so the grant waits for it and the monitor's own
halt clock may report ``connection.halted`` meanwhile (NFR20).

**Volume** (AC #3): ``reconcile.ok`` on the first clean cycle, on the first
clean cycle after anything else, and otherwise at most hourly, each carrying
``cycles`` — the clean cycles it summarises. A failing read is one
``reconcile.cycle_failed`` per streak (re-logged at most hourly), never one per
cycle.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from src.core.live_broker_state import find_ib_exec_client, read_broker_state
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
    log_discrepancy,
    position_report,
)
from src.models.broker_state import BrokerState
from src.models.position_reconciliation import (
    CachedPosition,
    PositionDiscrepancy,
    compare_positions,
    count_synthetic,
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

CYCLE_FAILED_EVENT = "reconcile.cycle_failed"
CONNECTION_READ_FAILED_EVENT = "session.connection_read_failed"

ConnectionReader = Callable[[Any], ConnectionStatus]

_CONTRADICTED = (
    "a strategy's own position disagrees with the broker while the session was running. IBKR is "
    "authoritative and reconciliation cannot rewrite a strategy's own position, so nothing was "
    "written to the cache and the session was stopped rather than let a strategy trade on a "
    "position the broker does not hold; positions at IBKR were not touched. Restarting this "
    "session meets the same check at startup: create a new session for the strategy, or see "
    "docs/agent/nautilus.md, 'Startup reconciliation', for clearing its disposable engine cache"
)
_REMAINS = "after the correction, the cache still disagrees with the broker"


class _Quiet:
    """A logger that drops everything: the broker reader's per-read records."""

    def _drop(self, *args: Any, **kwargs: Any) -> None:
        return None

    debug = info = warning = error = exception = _drop


_QUIET = _Quiet()


@dataclass(frozen=True, slots=True)
class _Observation:
    """One completed read-and-compare: the broker, the rows still to answer
    for, and the instruments deferred for an in-flight order."""

    broker: BrokerState
    rows: tuple[PositionDiscrepancy, ...]
    deferred: tuple[str, ...]
    started: float


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
        self._ok = _OkLog()
        self._failures = _FailureStreak()

    @property
    def unreported_clean_cycles(self) -> int:
        """Clean cycles not yet folded into a ``reconcile.ok`` record."""
        return self._ok.clean

    async def on_tick(self) -> None:
        """One heartbeat tick, called right after the tick observes the connection.

        Raises:
            ReconciliationFailedError: A confirmed strategy-position
                contradiction, a framework refusal, or a correction that did
                not take — the session must stop (D-D). Nothing else leaves.
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
                self._ok.needed = True
            await self._run(SCOPE_RECONNECT)
        elif state is ConnectionState.CONNECTED:
            self._recovering = False
            self._ticks += 1
            if self._ticks % RUNTIME_RECONCILE_EVERY_TICKS == 0:
                await self._run(SCOPE_RUNTIME)

    async def _run(self, scope: str) -> None:
        try:
            observation = await observe(self._node, self._read_state, self._clock)
            if observation is None:
                self._ok.needed = True  # the cache moved during the read: no action, no progress
                return
            outstanding = self._act(observation, scope)
            self._failures.count = 0
            self._ok.deferred.update(observation.deferred)
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
        except Exception as exc:  # noqa: BLE001 - AR42: a cycle must never kill a session
            self._ok.needed = True
            self._failures.note(self._log, scope, exc, self._clock())

    def _act(self, observation: _Observation, scope: str) -> list[PositionDiscrepancy]:
        """Act on the confirmed rows; return the rows that still stand between
        this cycle and clean — all but those the framework cannot express."""
        confirmed = self._debounce.confirm(observation.rows, self._clock())
        lookup_misses = [row for row in confirmed if not row.broker_resolved]
        actionable = [row for row in confirmed if row.broker_resolved]
        if any(not row.broker_resolved for row in observation.rows):
            # An `IB-CONID-*` row may be the holding a cache row reads as
            # "broker 0": hold only those back until it resolves, so a lookup
            # miss neither stops the session nor blinds every other instrument
            # (PO ruling, code review 2026-09-28).
            actionable = [row for row in actionable if row.broker_quantity != 0]
        contradicted = [row for row in actionable if row.strategy_contradicted]
        if contradicted:
            refuse(
                self._log, ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED, contradicted, scope
            )
        corrected, unresolvable = correct(
            self._node, actionable, observation.broker, self._log, scope
        )
        self._unresolved.log(self._log, [*lookup_misses, *unresolvable], observation.rows, scope)
        if corrected:
            verify_corrected(self._node, corrected, observation.broker, self._log, scope)
        # No strategy can act on an instrument the cache does not hold, so such a
        # row never withholds a grant; a lookup miss still does (fails closed).
        inert = set(unresolvable)
        return [row for row in observation.rows if row not in inert]

    def _grant(self, observation: _Observation) -> None:
        """Clean, and nothing in flight: the state check passed — only now grant.

        ``reconcile.ok`` precedes the attempt so it lands before the monitor's
        ``connection.restored``; a refused attempt does not repeat it next tick.
        """
        if observation.deferred:
            return
        if self._ok.needed:
            self._ok.emit(self._log, self._node, observation, SCOPE_RECONNECT, self._clock())
        grant_after_reconciliation(
            self._monitor, self._connection_reader, self._settings, self._log
        )


async def observe(
    node: Any, read_state: BrokerStateReader, clock: Callable[[], float]
) -> _Observation | None:
    """Read the broker and compare, or ``None`` when the cache moved meanwhile.

    The reader's own records are dropped (F9); instruments with an order still
    in flight are deferred (PO ruling 2A) — the in-flight sweep owns them.
    """
    cache = node.cache
    started = clock()
    before = _fingerprint(cached_positions(cache))
    broker = await read_state(node, log=_QUIET)
    cached = cached_positions(cache)
    if _fingerprint(cached) != before:
        return None
    deferred = sorted({str(order.instrument_id) for order in cache.orders_inflight()})
    rows = tuple(
        row for row in compare_positions(cached, broker) if row.instrument_id not in deferred
    )
    return _Observation(broker, rows, tuple(deferred), started)


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
            open_orders=len(cache.orders_open()),
            elapsed_ms=round((now - observation.started) * 1000, 3),
        )
        self.clean, self.needed, self.last_at = 0, False, now
        self.deferred = set()


class _FailureStreak:
    """One ``reconcile.cycle_failed`` per streak, re-logged at most hourly.
    Only the failure's type and the reader's own reason — never ``str(exc)``,
    which may carry the raw account (NFR26)."""

    def __init__(self) -> None:
        self.count = 0
        self.logged_at = 0.0

    def note(self, log: Any, scope: str, exc: Exception, now: float) -> None:
        self.count += 1
        if self.count > 1 and now - self.logged_at < RUNTIME_OK_LOG_INTERVAL_SECONDS:
            return
        self.logged_at = now
        reason = getattr(exc, "reason", None)
        _emit(
            log,
            "warning",
            CYCLE_FAILED_EVENT,
            scope=scope,
            reason=None if reason is None else str(reason),
            error_type=type(exc).__name__,
            consecutive=self.count,
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
