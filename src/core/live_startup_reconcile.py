"""The ``reconcile`` phase's body: verify and complete startup reconciliation (Story 4.2).

Owns: proving that Nautilus's own startup reconciliation was allowed to let the
broker's view win, comparing the cache against Story 4.1's independent broker
read, correcting what the framework left behind — broker-ward, through the
framework's own public entry point — refusing what cannot be corrected, and the
``reconcile.ok`` / ``reconcile.discrepancy`` records (AR41). Does not own: the
phase's position in AR39's sequence (the runner), the broker read
(``live_broker_state``), the comparison itself (``src.models.position_reconciliation``,
stdlib-only so Story 4.6 can reuse it), runtime alignment (Story 4.3), or what a
strategy may start beside once the cache is proven (Story 4.5,
``live_session_resume``).

**The framework imports the broker once, not twice** (Story 4.5, D-A). The
session's engine runs with ``filter_unclaimed_external_orders=True``, enforced
below with the other broker-ward settings: the IB adapter's fabricated
per-position ``FILLED`` order is dropped rather than imported as ``EXTERNAL``,
so a restart holding a position leaves only the strategy's own position, and a
holding the cache cannot attribute arrives once, as ``INTERNAL-DIFF``.

**Why the phase verifies rather than runs reconciliation** (decision D-A).
``NautilusKernel.start_async`` reconciles *inside* ``node:connect`` —
``engines connected → reconcile → emulator → portfolio → trader.start()``, one
coroutine, returning early (the trader never starts) on a reported failure
(``system/kernel.py:1008-1027``). That pass is read-only against the broker
(it submits nothing) and cannot be moved without reordering the kernel's own
sequence. So a *hard* native failure stops the session at ``node:connect``; this
module runs after ``gate:account``, before any strategy exists, and every
failure it raises is logged ``phase=reconcile status=failed``.

**Why the framework's "reconciled" is not trusted** (measured against 1.220.0,
Story 4.2 Task 1). The pass is net-only and blind to an instrument the broker is
flat in: its sweep of cached positions filters by the IB client's venue
(``INTERACTIVE_BROKERS``) while positions carry the exchange (``NASDAQ``), and
the IB adapter's position report ignores its instrument filter and skips zero
quantities — so the ``p7-fill-0901`` phantom (``LONG 22 NVDA`` cached, the
broker flat) is never touched and the pass still reports success. Hence
:func:`reconcile_at_startup` compares against ``read_broker_state``.

**Why a strategy's contradicted position is refused, not corrected** (decision
D-D, ruled by the PO 2026-09-27). Every correction the framework makes is a fill
attributed to a synthetic owner (``EXTERNAL`` / ``INTERNAL-DIFF``); under
NETTING that fill lands in the synthetic owner's position and **never** the
strategy's (measured: a flat report against a strategy's ``+22`` leaves the
``+22`` and adds ``INTERNAL-DIFF −22``). A strategy started on that cache still
believes its ``+22``, and the built-in ``sma_crossover`` would ``close_position``
it on its next opposite signal — a real order against a flat account (NFR14).
So a strategy whose own position the broker does not **cover** — it holds
less in the strategy's direction, none, or the opposite side — stops the
session, before anything here writes to the cache; the refusal names the
likely cause (a reverse split, a cash merger or symbol change, a trade outside
the session). Everything else — a synthetic position the broker no longer
holds, a broker position the cache lacks, and a strategy's position the broker
now holds *more* of (a forward split or stock dividend, Story 4.7's coverage
rule) — is corrected through ``exec_engine.reconcile_execution_report``, the
same netting path the framework's own pass runs, and then re-verified: the
phase reports ``ok`` only when the cache matches the broker exactly (NFR9).
Absorbing a split never adjusts, closes or flattens the strategy's own lot:
the extra shares land in a synthetic owner's position, which the trade
recorder never persists (Story 3.6 D-D).

**A covering broker holding is not a contradiction here** (Story 4.5, PO ruling
2026-09-28, startup only). When the broker holds everything the strategies own
on the same side *and more* (+10 owned, IBKR +15 — a share bought by hand, or
a forward split), the strategies' position is real and the excess belongs to
no strategy: this phase lets the framework's ``INTERNAL-DIFF`` carry the excess
(or corrects the net into it, as above) and leaves it to the per-strategy
resume check (``live_session_resume``), which refuses only the strategies on
that instrument. Since Story 4.7's coverage rule this exemption decides
something only for strategies holding both sides of one instrument — any other
covering row is not contradicted at all. IBKR holding less, nothing or the
opposite side still refuses the whole session, and the running session's cycle
still stops on all of them.

**Holdings the adapter cannot express are named, not refused** (Story 5.7, PO
ruling D2, "same-symbol rule everywhere, name the rest"). An unresolved
``IB-CONID-*`` broker row refuses the start only when its symbol is *held* —
one a cached position is held under (the row that could be masking that
position's "broker 0"), or one an instrument the session's strategies trade is
(code review R1, PO ruling 2026-10-07: a flat strategy must not buy on top of a
holding it cannot see); any other is logged ``resolution="unresolved"`` once
and left out of the judgement. A broker quantity the instrument's size
precision cannot express (a DRIP's ``+22.5``) is judged truncated toward zero
to whole steps of ``10**-size_precision`` — what ``make_qty`` rounds to, never
the possibly coarser ``size_increment`` (code review R2) — and its remainder
named once (``reconcile.inexpressible_remainder``) — never rounded, since
``make_qty`` rounds half-even and would read ``23.5`` as ``24``. Nautilus's own
pass imports a whole extra share for that half
(``INTERNAL-DIFF +1``, measured 2026-10-07), which this phase corrects down like
any other net disagreement. Every record that *reports* the broker keeps its
exact read; ``live reconcile`` stays exact (FR36).

What this module never does: purge or write the cache directly, call an order
method, re-invoke ``reconcile_execution_state``, or read ``trading_permitted``.
Granting trading permission after the phase proves the cache — the first
``confirm_state_reestablished`` call — is Story 4.3's
(``live_runtime_reconcile.grant_after_reconciliation``, called by the runner),
which also shares :func:`position_report` and :func:`log_discrepancy` with the
running session's cycle.
"""

import json
import time
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar

from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import PositionStatusReport
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import InstrumentId

from src.core.exit_outcome import LiveCheckOutcome

# ``_cash_from`` is private by name, shared on purpose: the before and the
# after of ``reconcile.cash_changed`` must be normalised by one function
# (Story 4.6's ``live_session_view`` precedent).
from src.core.live_broker_state import (
    ACCOUNT_SUMMARY_KEY_PREFIX,
    _cash_from,
    cash_recorded_at,
    find_ib_exec_client,
    read_broker_state,
)
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from src.models.position_reconciliation import (
    CachedPosition,
    InexpressibleRemainder,
    LocalSnapshot,
    PositionDiscrepancy,
    StartupReconciliation,
    at_expressible_precision,
    cash_changes,
    compare_positions,
    count_synthetic,
    held_symbols,
    may_mask_a_cached_position,
    symbol_key,
)

#: ``read_broker_state``'s shape — the runner's fifth broker-facing seam.
BrokerStateReader = Callable[..., Awaitable[BrokerState]]

OK_EVENT = "reconcile.ok"
DISCREPANCY_EVENT = "reconcile.discrepancy"
#: Story 4.7, D-B (PO ruling A): cash that differs from the last value the
#: previous run recorded (``recorded_at``) — a dividend, interest or a fee while
#: the session was stopped, *or* the previous run's own last fills and
#: commissions, since IBKR pushes the summary only every few minutes (code
#: review). Informational only: never a refusal, never a phase failure, never a
#: trading-permission input. A start that fails after connecting has already
#: let the first push overwrite the stored value, so the next start names
#: nothing — ``live reconcile`` before starting is the reliable view.
CASH_CHANGED_EVENT = "reconcile.cash_changed"
#: Story 5.7 (D-E): a broker quantity finer than its instrument's size
#: precision, named once (WARNING) with both quantities. Not a discrepancy: the
#: row is clean at the expressible precision, and the record says what that
#: left out. Shared with the running session's cycle.
INEXPRESSIBLE_REMAINDER_EVENT = "reconcile.inexpressible_remainder"
#: A **diagnostic** — the snapshot's own read broke — so it sits outside
#: :data:`EMITTED_RECONCILE_EVENTS`, the ``order.observer_failed`` precedent.
SNAPSHOT_FAILED_EVENT = "reconcile.local_snapshot_failed"
#: Every record :func:`reconcile_at_startup` emits — membership-pinned against
#: the code, both directions (``TestEveryEmittedReconcileRecordIsPinned``: an
#: AST scan of every ``_emit`` in this module, and the phase driven through
#: every path), so a new record name cannot join silently (CLAUDE.md,
#: "Membership-pinned lists"; PO ruling, Story 4.7). Widened 3 → 4 at Story 5.7.
EMITTED_RECONCILE_EVENTS: tuple[str, ...] = (
    OK_EVENT,
    DISCREPANCY_EVENT,
    CASH_CHANGED_EVENT,
    INEXPRESSIBLE_REMAINDER_EVENT,
)
#: Story 4.3 (D-G): every ``reconcile.ok`` / ``reconcile.discrepancy`` says
#: which reconciliation wrote it — this module's startup phase, or the running
#: session's cycle (``live_runtime_reconcile``: ``runtime`` / ``reconnect``).
SCOPE_STARTUP = "startup"

#: D-B. The live engine's settings under which the broker's view can win: the
#: framework's pass runs, a discrepancy generates the correcting order rather
#: than being skipped (``live/execution_engine.py:1469-1474``), and position
#: reports are not filtered out. Compared by identity — ``1`` is not ``True``.
#: Story 4.5 (D-A) added ``filter_unclaimed_external_orders``: it is what keeps
#: the broker's view from being imported *twice* — once as the IB adapter's
#: fabricated ``EXTERNAL`` order, once as the ``INTERNAL-DIFF`` that undoes it —
#: beside a strategy's own restored position (``live_node_builder``'s comment).
BROKER_WARD_SETTINGS: tuple[tuple[str, bool], ...] = (
    ("reconciliation", True),
    ("generate_missing_orders", True),
    ("filter_position_reports", False),
    ("filter_unclaimed_external_orders", True),
)
#: Non-empty, it excludes every other instrument from reconciliation — and
#: ``reconcile_execution_report`` then returns ``True`` having done nothing.
INSTRUMENT_FILTER_SETTING = "reconciliation_instrument_ids"

_MISSING = object()
#: Code review P5: each refusal of an unresolved row says what is true of it —
#: a matching symbol (R1: a cached position's or a traded instrument's), or no
#: symbol at all (D-B). The instrument-not-in-cache refusal is ``_correct``'s own.
_UNRESOLVED_SAME_SYMBOL = (
    "the broker holds a position its adapter could not resolve to an instrument, under the same "
    "symbol as a position this session's cache holds or an instrument its strategies trade, so "
    "it may be that holding"
)
_UNRESOLVED_NO_SYMBOL = (
    "the broker holds a position its adapter could not resolve to an instrument and that "
    "carries no symbol, so it cannot be told apart from anything this session holds or trades"
)
_UNRESOLVED_REMEDY = (
    "nothing can be compared or reconciled against it; retry the start, and if it persists "
    "check the instrument at the broker"
)
_REMEDY = (
    "IBKR is authoritative, and reconciliation cannot rewrite a strategy's own position, so no "
    "strategy was started. Create a new session for this strategy, or see "
    "docs/agent/nautilus.md, 'Startup reconciliation', for clearing this session's disposable "
    "engine cache."
)


class ReconciliationFailure(StrEnum):
    """Why the ``reconcile`` phase refused to let the session trade."""

    FRAMEWORK_RECONCILIATION_DISABLED = "framework_reconciliation_disabled"
    STRATEGY_POSITION_CONTRADICTED = "strategy_position_contradicted"
    UNRESOLVABLE_DISCREPANCY = "unresolvable_discrepancy"
    RESOLUTION_REFUSED = "resolution_refused"
    DISCREPANCY_REMAINS = "discrepancy_remains"
    NOT_RECONCILED = "not_reconciled"


class ReconciliationFailedError(RuntimeError):
    """The cache could not be proven to match the broker; the session must not trade.

    Exit 1 (AR28's "configuration, state or database failure" — a state
    failure). The message is this module's own text, naming instruments and
    quantities only — never an account id or broker text (NFR26). A failure to
    *read* the broker is not this class: ``BrokerStateUnavailableError``
    propagates unchanged (exit 4, or 1 for adapter drift).

    Story 4.3 (D-H) raises the same class from the running session's cycle, with
    ``scope`` other than :data:`SCOPE_STARTUP`: same markers, same exit code, a
    message that says the session was *stopped* rather than refused a start.

    Attributes:
        reason: The :class:`ReconciliationFailure`.
        detail: What happened, in this module's words.
        discrepancies: The disagreeing instruments, when there are any.
        scope: Which reconciliation refused — ``startup``, ``runtime`` or
            ``reconnect``.
    """

    exit_outcome: ClassVar[LiveCheckOutcome] = LiveCheckOutcome.ERROR
    operator_safe_message: ClassVar[bool] = True

    def __init__(
        self,
        reason: ReconciliationFailure,
        detail: str,
        discrepancies: Sequence[PositionDiscrepancy] = (),
        *,
        scope: str = SCOPE_STARTUP,
    ) -> None:
        rows = "; ".join(_describe(row) for row in discrepancies)
        suffix = f" {rows}." if rows else ""
        prefix = (
            f"Startup reconciliation refused to let this session trade ({reason.value})"
            if scope == SCOPE_STARTUP
            else f"Runtime reconciliation stopped this session ({scope}, {reason.value})"
        )
        super().__init__(f"{prefix}: {detail.rstrip('.')}.{suffix}")
        self.reason = reason
        self.detail = detail
        self.discrepancies = tuple(discrepancies)
        self.scope = scope


def _describe(row: PositionDiscrepancy) -> str:
    cause = row.likely_cause
    return (
        f"{row.instrument_id}: this session's record {row.local_quantity:+}, its strategies "
        f"{row.strategy_quantity:+}, broker {row.broker_quantity:+}"
        + ("" if cause is None else f" — {cause}")
    )


def cached_positions(cache: Any) -> tuple[CachedPosition, ...]:
    """The cache's open positions as domain values (AR38's boundary)."""
    positions = []
    for position in cache.positions_open():
        quantity = position.signed_decimal_qty()
        if quantity != 0:
            positions.append(
                CachedPosition(
                    instrument_id=str(position.instrument_id),
                    strategy_id=str(position.strategy_id),
                    quantity=quantity,
                )
            )
    return tuple(positions)


def capture_local_state(node: Any, log: Any) -> LocalSnapshot | None:
    """What the cache held before Nautilus's own pass touched it (decision D-F).

    Called by the runner immediately before ``node.run_async()``: the cache was
    loaded from Redis in ``NautilusKernel.__init__`` (``system/kernel.py:455-456``)
    and nothing has reconciled it yet. Diagnostic only — it lets the phase name
    what the framework corrected — so a failure is contained (AR42) and returns
    ``None``, which the phase treats as "not known", never as a failure.

    Story 4.7 (D-B) adds the previous run's last-recorded cash — the restored
    ``accountSummary`` key the exec client's first push is about to overwrite —
    and when the broker reported it. Existing Redis state only, read only
    (``positions_open``, ``get``, ``account``). A cash read that fails is
    contained on its own: the positions are still captured, and cash is
    unknown, so nothing is compared.
    """
    try:
        positions = cached_positions(node.cache)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not change the outcome
        _emit(
            log, "warning", SNAPSHOT_FAILED_EVENT, part="positions", error_type=type(exc).__name__
        )
        return None
    try:
        cash, recorded_at = _recorded_cash(node)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not change the outcome
        _emit(log, "warning", SNAPSHOT_FAILED_EVENT, part="cash", error_type=type(exc).__name__)
        cash, recorded_at = (), None
    return LocalSnapshot(positions=positions, cash=cash, cash_recorded_at=recorded_at)


def _recorded_cash(node: Any) -> tuple[tuple[CashBalance, ...], datetime | None]:
    """The restored summary's ``TotalCashValue`` per currency, normalised by the
    broker reader's own function, and when the broker last reported it."""
    account_id = find_ib_exec_client(node).account_id
    raw = node.cache.get(f"{ACCOUNT_SUMMARY_KEY_PREFIX}{account_id.get_id()}")
    if raw is None:
        return (), None
    summary = json.loads(raw)
    if not isinstance(summary, Mapping):
        raise TypeError("the restored account summary is not a mapping")
    return _cash_from(summary), cash_recorded_at(node.cache.account(account_id))


def require_broker_ward_reconciliation(exec_engine: Any) -> None:
    """Refuse unless the running engine lets the broker's view win (decision D-B).

    Raises:
        ReconciliationFailedError: ``FRAMEWORK_RECONCILIATION_DISABLED``, naming
            every drifted or missing setting.
    """
    drifted = [
        name
        for name, expected in BROKER_WARD_SETTINGS
        if getattr(exec_engine, name, _MISSING) is not expected
    ]
    instrument_filter: Any = getattr(exec_engine, INSTRUMENT_FILTER_SETTING, _MISSING)
    if instrument_filter is _MISSING or (instrument_filter is not None and list(instrument_filter)):
        drifted.append(INSTRUMENT_FILTER_SETTING)
    if drifted:
        raise ReconciliationFailedError(
            ReconciliationFailure.FRAMEWORK_RECONCILIATION_DISABLED,
            "the node's execution engine is not configured to let the broker's view win "
            f"({', '.join(drifted)}); this is a code or configuration defect, not a broker fault",
        )


async def reconcile_at_startup(
    node: Any,
    *,
    log: Any,
    local_before: LocalSnapshot | None,
    read_state: BrokerStateReader = read_broker_state,
    clock: Callable[[], float] = time.monotonic,
    traded_instruments: Collection[str] = (),
) -> StartupReconciliation:
    """Prove the cache matches the broker exactly, correcting it broker-ward first.

    Must run on the node's own loop (``read_broker_state`` checks). Nothing is
    written to the cache until every disagreement is known to be correctable.
    Cash that differs from what the previous run last recorded is named right
    after the read (``reconcile.cash_changed``, Story 4.7 D-B) —
    informational: it cannot refuse, fail or delay the phase.

    Args:
        node: The running, connected, account-verified ``TradingNode``.
        log: The session-bound logger.
        local_before: :func:`capture_local_state`'s snapshot, or ``None``.
        read_state: The broker read — ``read_broker_state`` in production.
        clock: Monotonic seconds, for ``elapsed_ms``.
        traded_instruments: The instrument ids the session's strategies trade;
            their symbols count as held for an unresolved row (code review R1).

    Returns:
        The :class:`StartupReconciliation` ``_phase_trading`` requires.

    Raises:
        ReconciliationFailedError: For any :class:`ReconciliationFailure`.
        BrokerStateUnavailableError: The broker could not be read — unchanged.
    """
    started = clock()
    require_broker_ward_reconciliation(node.kernel.exec_engine)
    broker = await read_state(node, log=log)
    if local_before is not None:
        _log_cash_changes(log, local_before, broker)
    # Story 5.7 (D-D): judged at each instrument's expressible precision,
    # reported exact — every comparison below is against `judged`.
    judged, inexpressible = expressible_broker(node.cache, broker)
    for remainder in inexpressible:
        log_remainder(log, remainder)
    cached = cached_positions(node.cache)
    observed = compare_positions(cached, judged)
    rows = _to_act_on(observed)
    framework = _name_settled(log, observed, rows, local_before, judged)
    rows = _set_aside_unresolved(log, rows, held_symbols(cached, traded_instruments))
    contradicted = [
        row for row in rows if row.strategy_contradicted and not row.broker_covers_strategy
    ]
    if contradicted:
        # Only the cause is refused, as the runtime twin does; every other
        # disagreeing row is named as left untouched, so a refused start still
        # shows the account as found (PR #35 code review, P4).
        for row in rows:
            if row not in contradicted:
                log_discrepancy(log, row, "untouched")
        _refuse(log, ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED, contradicted, _REMEDY)
    corrected = _correct(node, rows, judged, log)
    cached = cached_positions(node.cache)
    remaining = _still_to_answer(cached, judged, traded_instruments)
    # A correction is recorded as the broker's only once the re-read proves it
    # took: a report the framework accepted without acting on (an instrument
    # filtered out of reconciliation returns `True`) must not read "resolved".
    still = {row.instrument_id for row in remaining}
    for row in corrected:
        if row.instrument_id not in still:
            log_discrepancy(log, row, "broker")
    if remaining:
        _refuse(
            log,
            ReconciliationFailure.DISCREPANCY_REMAINS,
            remaining,
            "after reconciling, the cache still disagrees with the broker",
        )
    result = StartupReconciliation(
        broker=broker,
        framework_resolved=framework,
        reconcile_resolved=corrected,
        open_orders=len(node.cache.orders_open()),
        synthetic_positions=count_synthetic(cached),
        elapsed_ms=round((clock() - started) * 1000, 3),
        inexpressible=inexpressible,
    )
    _log_ok(log, result)
    return result


def _name_settled(
    log: Any,
    observed: Sequence[PositionDiscrepancy],
    rows: Sequence[PositionDiscrepancy],
    local_before: LocalSnapshot | None,
    judged: BrokerState,
) -> tuple[PositionDiscrepancy, ...]:
    """Name what this phase will not act on, and return the framework's share.

    A row the covered-excess rule settles is named from this phase, not only by
    the resume check that refuses the strategies on it later (PR #35 code
    review, D5b ruling); a disagreement Nautilus's own pass already fixed is
    named ``framework``. Runs while ``rows`` still holds every unresolved row,
    so none reads as framework-resolved.
    """
    for row in observed:
        if row not in rows:
            log_discrepancy(log, row, "covered")
    framework = _framework_resolved(local_before, judged, rows)
    for row in framework:
        log_discrepancy(log, row, "framework")
    return framework


def _set_aside_unresolved(
    log: Any, rows: Sequence[PositionDiscrepancy], held: Collection[str]
) -> tuple[PositionDiscrepancy, ...]:
    """The rows left to judge once unresolved broker rows are answered for (Story 5.7).

    An unresolved row is keyed ``IB-CONID-*``, so the cache's row for the same
    holding reads "broker 0" and would pass for a contradiction — whose remedy
    (abandon the session) is wrong for a transient lookup miss. Only a row
    under a held symbol — a cached position's or a traded instrument's (code
    review R1) — still refuses the start; any other is named once and left out
    (PO ruling D2).

    Raises:
        ReconciliationFailedError: ``UNRESOLVABLE_DISCREPANCY``, naming the
            masking rows only, with the reason true of them (code review P5).
    """
    unresolved = [row for row in rows if not row.broker_resolved]
    masking = [row for row in unresolved if may_mask_a_cached_position(row, held)]
    for row in unresolved:
        if row not in masking:
            log_discrepancy(log, row, "unresolved")
    if masking:
        _refuse(log, ReconciliationFailure.UNRESOLVABLE_DISCREPANCY, masking, _unresolved(masking))
    return tuple(row for row in rows if row.broker_resolved)


def _unresolved(masking: Sequence[PositionDiscrepancy]) -> str:
    """The refusal's reason for these rows, and only what is true of them (P5)."""
    named = [bool(symbol_key(row.broker_symbol)) for row in masking]
    reasons = [_UNRESOLVED_SAME_SYMBOL] if any(named) else []
    if not all(named):
        reasons.append(_UNRESOLVED_NO_SYMBOL)
    return f"{'; and '.join(reasons)}: {_UNRESOLVED_REMEDY}"


def _still_to_answer(
    cached: Sequence[CachedPosition], judged: BrokerState, traded: Collection[str]
) -> tuple[PositionDiscrepancy, ...]:
    """The re-verify's rows: what the phase acts on, less the unresolved rows
    that mask nothing — judged against the corrected cache (and the traded
    instruments, code review R1), so a correction that put a same-symbol
    position there makes its row count again (fail closed, Story 5.7)."""
    held = held_symbols(cached, traded)
    return tuple(
        row
        for row in _to_act_on(compare_positions(cached, judged))
        if row.broker_resolved or may_mask_a_cached_position(row, held)
    )


def size_steps(cache: Any, broker: BrokerState) -> dict[str, Decimal]:
    """Each resolved broker instrument's size step — ``10**-size_precision`` of
    the instrument the cache holds (Story 5.7, D-C as ruled at code review R2).

    The step is the precision, not ``size_increment``: ``make_qty`` rounds to
    ``size_precision``, and an increment can be coarser (FX, futures, lots), so
    truncating to it would cut an exactly representable quantity (``150`` with
    an increment of ``100`` read as ``100``) and correct the cache below the
    broker. The broker reader's ``get_instrument`` loads every resolved
    instrument into the cache, so it is normally there. A lookup that fails —
    raising, or an instrument without a usable ``size_precision`` (code review
    P3: ``AttributeError`` included) — or an instrument the cache lacks leaves
    that row exact, judged as before this story.
    """
    steps: dict[str, Decimal] = {}
    for position in broker.positions:
        if not position.instrument_resolved:
            continue
        try:
            instrument = cache.instrument(InstrumentId.from_str(position.instrument_id))
            precision = None if instrument is None else instrument.size_precision
        except (AttributeError, ValueError, TypeError, OverflowError):
            continue
        if isinstance(precision, int) and not isinstance(precision, bool) and precision >= 0:
            steps[position.instrument_id] = Decimal(1).scaleb(-precision)
    return steps


def expressible_broker(
    cache: Any, broker: BrokerState
) -> tuple[BrokerState, tuple[InexpressibleRemainder, ...]]:
    """The broker's state as both phases judge it, and what that left out
    (Story 5.7, D-D). Shared with the running session's cycle; never used by
    ``live reconcile``, which stays exact (FR36)."""
    return at_expressible_precision(broker, size_steps(cache, broker))


def _to_act_on(rows: Sequence[PositionDiscrepancy]) -> tuple[PositionDiscrepancy, ...]:
    """The rows this phase must correct or refuse (Story 4.5, PO ruling 2026-09-28).

    A row whose net already matches the broker and whose broker holding
    covers the strategies' own on the same side is settled here: the
    strategies' position is real, and the excess — imported by the framework
    as ``INTERNAL-DIFF`` — belongs to no strategy, which the per-strategy
    resume check refuses in ``trading`` (``live_session_resume``). Startup
    only: the running session's cycle still stops on the same row.
    """
    return tuple(
        row
        for row in rows
        if not (row.broker_covers_strategy and row.local_quantity == row.broker_quantity)
    )


def require_reconciled(result: StartupReconciliation | None) -> StartupReconciliation:
    """The latch ``_phase_trading`` opens with (decision D-I, NFR18)."""
    if result is None:
        raise ReconciliationFailedError(
            ReconciliationFailure.NOT_RECONCILED,
            "trading was reached without a completed reconciliation against the broker",
        )
    return result


def _framework_resolved(
    local_before: LocalSnapshot | None,
    broker: BrokerState,
    remaining: Sequence[PositionDiscrepancy],
) -> tuple[PositionDiscrepancy, ...]:
    """Disagreements before the framework's pass that no longer disagree."""
    if local_before is None:
        return ()
    still = {row.instrument_id for row in remaining}
    return tuple(
        row
        for row in compare_positions(local_before.positions, broker)
        if row.instrument_id not in still
    )


def _log_cash_changes(log: Any, before: LocalSnapshot, broker: BrokerState) -> None:
    """One ``reconcile.cash_changed`` per currency that moved since the previous
    run recorded it (D-B). Contained end to end: a failure here names nothing,
    and never reaches the phase (PO ruling — never blocks a phase)."""
    try:
        changes = cash_changes(before, broker)
    except Exception:  # noqa: BLE001 - an observation must not change the outcome
        return
    recorded_at = before.cash_recorded_at
    for change in changes:
        difference = change.difference
        _emit(
            log,
            "info",
            CASH_CHANGED_EVENT,
            scope=SCOPE_STARTUP,
            currency=change.currency,
            before=None if change.before is None else str(change.before),
            after=None if change.after is None else str(change.after),
            difference=None if difference is None else str(difference),
            recorded_at=None if recorded_at is None else recorded_at.isoformat(),
        )


def _correct(
    node: Any, rows: Sequence[PositionDiscrepancy], broker: BrokerState, log: Any
) -> tuple[PositionDiscrepancy, ...]:
    """Hand the framework the broker's truth for each row.

    Every report is built and validated before the first write, so a row that
    cannot be expressed exactly (unknown instrument, a quantity finer than the
    instrument's size precision) refuses with nothing of ours written. The
    framework itself can still refuse a row once writing has begun; the rows
    handed over before it stay corrected — broker-ward, the only direction this
    module writes — and the refusal names them.
    """
    if not rows:
        return ()
    held = {position.instrument_id: position for position in broker.positions}
    account_id = find_ib_exec_client(node).account_id
    reports = [
        position_report(row, held.get(row.instrument_id), node.cache, account_id) for row in rows
    ]
    unresolvable = [row for row, report in zip(rows, reports, strict=True) if report is None]
    if unresolvable:
        _refuse(
            log,
            ReconciliationFailure.UNRESOLVABLE_DISCREPANCY,
            unresolvable,
            "the broker and the cache disagree on an instrument that cannot be reconciled "
            "(unknown to the cache, or a broker quantity or price the instrument cannot "
            f"represent exactly): {', '.join(row.instrument_id for row in unresolvable)}",
        )
    engine = node.kernel.exec_engine
    for index, (row, report) in enumerate(zip(rows, reports, strict=True)):
        try:
            accepted = engine.reconcile_execution_report(report) is True
            failure = "" if accepted else "it returned False"
        except Exception as exc:  # noqa: BLE001 - any engine failure is this refusal
            accepted, failure = False, f"it raised {type(exc).__name__}"
        if not accepted:
            applied = [done.instrument_id for done in rows[:index]]
            # D-F: a row the framework took before refusing this one was a
            # discrepancy that got resolved — logged so, as the running
            # session's cycle does, never only named in the message below
            # (code review of PR #35).
            _log_taken(node, rows[:index], broker, log)
            already = (
                f"; already corrected broker-ward before it: {', '.join(applied)}"
                if applied
                else ""
            )
            _refuse(
                log,
                ReconciliationFailure.RESOLUTION_REFUSED,
                rows[index:],
                f"Nautilus refused to reconcile {row.instrument_id} to the broker's position "
                f"({failure}){already}",
            )
    return tuple(rows)


def _log_taken(
    node: Any, corrected: Sequence[PositionDiscrepancy], broker: BrokerState, log: Any
) -> None:
    """Log ``resolution="broker"`` for each correction the re-read proves took —
    on the way to a refusal (``_correct``), the same rule the success path
    applies inline after every row: a report the framework accepted without
    acting on must not read "resolved"."""
    still = {row.instrument_id for row in compare_positions(cached_positions(node.cache), broker)}
    for row in corrected:
        if row.instrument_id not in still:
            log_discrepancy(log, row, "broker")


def position_report(
    row: PositionDiscrepancy,
    held: BrokerPosition | None,
    cache: Any,
    account_id: Any,
) -> PositionStatusReport | None:
    """The broker's position for ``row``, as the netting path reads it, or
    ``None`` when it cannot be expressed exactly. The broker's average price is
    passed whenever it is known: without it the framework prices the correcting
    fill at ``0`` (Story 4.2 Task 1.2)."""
    if not row.broker_resolved:
        return None
    try:
        instrument = cache.instrument(InstrumentId.from_str(row.instrument_id))
        if instrument is None:
            return None
        now = time.time_ns()
        if row.broker_quantity == 0:
            return PositionStatusReport.create_flat(
                account_id, instrument.id, instrument.size_precision, now
            )
        quantity = instrument.make_qty(abs(row.broker_quantity))
        if quantity.as_decimal() != abs(row.broker_quantity):
            return None  # rounded: the correction would miss the broker
        price = None if held is None else held.average_price
        return PositionStatusReport(
            account_id=account_id,
            instrument_id=instrument.id,
            position_side=PositionSide.LONG if row.broker_quantity > 0 else PositionSide.SHORT,
            quantity=quantity,
            report_id=UUID4(),
            ts_last=now,
            ts_init=now,
            avg_px_open=None if price is None else instrument.make_price(price),
        )
    except (ValueError, TypeError, OverflowError):
        return None


def _refuse(
    log: Any,
    reason: ReconciliationFailure,
    rows: Iterable[PositionDiscrepancy],
    detail: str,
) -> None:
    rows = tuple(rows)
    for row in rows:
        log_discrepancy(log, row, "refused", reason=reason)
    raise ReconciliationFailedError(reason, detail, rows)


def log_discrepancy(
    log: Any,
    row: PositionDiscrepancy,
    resolution: str,
    *,
    reason: ReconciliationFailure | None = None,
    scope: str = SCOPE_STARTUP,
) -> None:
    """One ``reconcile.discrepancy`` naming the instrument and every quantity
    (AR41); ERROR when refused or unresolved, WARNING otherwise (``framework``,
    ``broker``, or ``untouched`` — a row a refused start left as found). Shared
    with Story 4.3's runtime cycle, which passes its own ``scope``. A refused
    strategy row also carries its ``likely_cause`` (Story 4.7, PO ruling)."""
    fields: dict[str, Any] = {
        "scope": scope,
        "instrument_id": row.instrument_id,
        "kind": row.kind,
        "resolution": resolution,
        "local_quantity": str(row.local_quantity),
        "strategy_quantity": str(row.strategy_quantity),
        "broker_quantity": str(row.broker_quantity),
    }
    if reason is not None:
        fields["reason"] = reason.value
    if resolution == "refused" and row.likely_cause is not None:
        fields["likely_cause"] = row.likely_cause
    level = "error" if resolution in ("refused", "unresolved") else "warning"
    _emit(log, level, DISCREPANCY_EVENT, **fields)


def log_remainder(
    log: Any, remainder: InexpressibleRemainder, *, scope: str = SCOPE_STARTUP
) -> None:
    """One ``reconcile.inexpressible_remainder`` (WARNING) naming both quantities
    (Story 5.7, AC #4). Shared with Story 4.3's runtime cycle, which passes its
    own ``scope``."""
    _emit(
        log,
        "warning",
        INEXPRESSIBLE_REMAINDER_EVENT,
        scope=scope,
        instrument_id=remainder.instrument_id,
        broker_quantity=str(remainder.broker_quantity),
        expressible_quantity=str(remainder.expressible_quantity),
        remainder=str(remainder.remainder),
        size_step=str(remainder.size_step),
    )


def _log_ok(log: Any, result: StartupReconciliation) -> None:
    broker = result.broker
    _emit(
        log,
        "info",
        OK_EVENT,
        scope=SCOPE_STARTUP,
        account=broker.account,
        positions=len(broker.positions),
        instruments={p.instrument_id: str(p.quantity) for p in broker.positions},
        open_orders=result.open_orders,
        discrepancies=result.discrepancy_count,
        synthetic_positions=result.synthetic_positions,
        inexpressible_remainders=inexpressible_remainders(result.inexpressible),
        elapsed_ms=result.elapsed_ms,
    )


def inexpressible_remainders(remainders: Iterable[InexpressibleRemainder]) -> dict[str, str]:
    """``reconcile.ok``'s view of what stands below the expressible precision —
    ``{}`` when nothing does — so no ``reconcile.ok`` is silent over a gap
    (Story 5.7, AC #4). Shared with the runtime cycle's record."""
    return {remainder.instrument_id: str(remainder.remainder) for remainder in remainders}


def _emit(log: Any, level: str, event: str, **fields: Any) -> None:
    """Log without ever raising: a failing sink must not replace the typed
    failure the caller is owed, nor turn a good reconciliation into one."""
    try:
        getattr(log, level)(event, **fields)
    except Exception:  # noqa: BLE001 - diagnostics must not change the outcome
        pass
