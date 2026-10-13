"""Clear an order the cache holds open after IBKR stopped listing it (Story 4.8, FR34, FR35).

Owns: finding the cache's open orders that IBKR no longer lists, and resolving
each one ``CANCELED`` through the framework's own
``exec_engine.reconcile_execution_report``. Does not own: when it runs (one
call per runtime cycle from :class:`~src.core.live_runtime_reconcile.RuntimeReconciler`),
positions (that module's comparison — the true economic effect of an order
that filled while the session was away lands there, as ``INTERNAL-DIFF``), or
the startup phase (``live_startup_reconcile`` is not touched: a stall inside
``node:connect`` happens before any code of ours runs, so a startup call could
neither prevent nor explain one).

**Why this exists.** An order ``ACCEPTED`` before a restart or a disconnect,
then filled or cancelled at IBKR while the session was away, stays ``ACCEPTED``
in the cache forever. Nothing native resolves it: Nautilus's startup pass and
its open-order consistency check reconcile only the orders a broker *report*
contains, never a cached order missing from one. The consistency check also
stays **off**, and this module does not touch it (PO ruling 1A, Story 4.3: it
republishes an ``OrderAccepted`` forever for a locally-cancelled order IBKR
still lists).

**How the broker is asked** (D-D, amended by PO ruling 2026-10-06). Through
the exec client's own ``get_open_orders`` — the evidence Nautilus's in-flight
sweep already acts on — never ``generate_order_status_reports``. That method
returns ``[]`` on a flat account without asking for open orders at all, and
one manual TWS order (empty ``orderRef``) makes it raise for every order. Even
``get_open_orders`` turns a timeout or a lost connection into ``[]``, so the
verdict is read from the adapter's own ``OpenOrders`` request **future**, the
``live_broker_state`` precedent: a list — even ``[]`` — is IBKR's
``openOrderEnd``; anything else is :class:`Inconclusive`, and an inconclusive
cycle confirms nothing. ``asyncio.wait``, never ``wait_for``: this module adds
no cancellation of its own to a request Nautilus's own sweep may be sharing. The
adapter's internal 30 s ``wait_for`` still ends that request for every caller
when it fires — unavoidable, and true of the sweep's own reads too. The broker
is asked only when there is a candidate — never on a session with no resting
order.

**What a confirmed absence does** (D-C, RULED (A)): the order is reconciled
``CANCELED``, always — whether it filled or was cancelled at IBKR cannot be
told from here, and the record says so. A ``FILLED`` is never fabricated: a
wrong fill price or commission would be worse than an honest cancel. The order
must be absent on two conclusive reads at least
:data:`STALE_ORDER_DEBOUNCE_SECONDS` apart, and an instrument with a position
issue still standing that cycle is skipped. An order the strategy put in flight
meanwhile is left to Nautilus's own sweep. Every failure is contained and logged
once per streak: clearing a stale order is hygiene, never a reason to stop a
session.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import OrderStatusReport
from nautilus_trader.model.enums import OrderStatus, TriggerType

from src.core.live_broker_state import READINESS_FLAGS, find_ib_exec_client

#: An absent order acts only once absent this long apart, on two conclusive reads.
STALE_ORDER_DEBOUNCE_SECONDS = 60.0
#: How long a cycle waits for IBKR's answer before calling the read inconclusive.
OPEN_ORDERS_DEADLINE_SECONDS = 10.0
#: The adapter's request name for ``reqOpenOrders`` (``client/order.py``).
OPEN_ORDERS_REQUEST = "OpenOrders"

CLEARED_EVENT = "reconcile.stale_order_cleared"
CLEAR_FAILED_EVENT = "reconcile.stale_order_clear_failed"
#: A diagnostic, at most once per streak of inconclusive reads.
CHECK_FAILED_EVENT = "reconcile.stale_order_check_failed"
EMITTED_STALE_ORDER_EVENTS = (CLEARED_EVENT, CLEAR_FAILED_EVENT, CHECK_FAILED_EVENT)

UNDETERMINED = (
    "the broker no longer lists this order as open; whether it filled or was cancelled could "
    "not be determined from here"
)


@dataclass(frozen=True, slots=True)
class BrokerOpenOrders:
    """IBKR's own answer: the open orders it lists for the configured account."""

    refs: frozenset[str]
    venue_order_ids: frozenset[str]

    def lists(self, order: Any) -> bool:
        """Listed under its ``orderRef`` (our ``client_order_id``) or its IB order id."""
        return (
            order.client_order_id.value in self.refs
            or order.venue_order_id.value in self.venue_order_ids
        )


@dataclass(frozen=True, slots=True)
class Inconclusive:
    """No answer from IBKR this cycle. ``reason`` is a fixed word or an
    exception type name — never an exception's text (NFR26)."""

    reason: str


OpenOrdersReader = Callable[[Any], Awaitable[BrokerOpenOrders | Inconclusive]]


def stale_accepted_orders(cache: Any) -> tuple[Any, ...]:
    """The cache's open orders the broker has acknowledged and that are not in flight.

    ``ACCEPTED``, ``PARTIALLY_FILLED`` and ``TRIGGERED`` (Task 1.1). In-flight
    orders belong to Nautilus's own sweep; an order with no venue order id
    never reached IBKR, so there is nothing to ask about.
    """
    inflight = {order.client_order_id for order in cache.orders_inflight()}
    return tuple(
        order
        for order in cache.orders_open()
        if order.client_order_id not in inflight and order.venue_order_id is not None
    )


async def broker_open_orders(
    node: Any, *, deadline_seconds: float = OPEN_ORDERS_DEADLINE_SECONDS
) -> BrokerOpenOrders | Inconclusive:
    """Ask IBKR which orders it lists open. Never raises."""
    try:
        exec_client = find_ib_exec_client(node)
        ib_client, account = exec_client._client, exec_client.account_id.get_id()
        if not exec_client.is_connected or not all(
            getattr(ib_client, name).is_set() for name in READINESS_FLAGS
        ):
            return Inconclusive("not_connected")
        # Captured before the task runs: a joined request may be answered and
        # removed before the first look.
        request = ib_client._requests.get(name=OPEN_ORDERS_REQUEST)
        task = asyncio.ensure_future(ib_client.get_open_orders(account))
        task.add_done_callback(_consume_outcome)
        await asyncio.sleep(0)  # `get_open_orders` registers before its first await
        if request is None:
            request = ib_client._requests.get(name=OPEN_ORDERS_REQUEST)
        if request is None:
            return Inconclusive("unanswered")
        await asyncio.wait(
            {request.future, task}, timeout=deadline_seconds, return_when=asyncio.FIRST_COMPLETED
        )
        return _answer(request.future, task, account)
    except Exception as exc:  # noqa: BLE001 - a maintenance read must never raise
        return Inconclusive(type(exc).__name__)


def _answer(
    future: asyncio.Future, task: asyncio.Future, account: str
) -> BrokerOpenOrders | Inconclusive:
    """IBKR's verdict, from the request's future — never the adapter's return value."""
    if not future.done():
        # A task already done here failed to send; otherwise our deadline passed.
        return Inconclusive("unanswered" if task.done() else "timed_out")
    if future.cancelled():
        return Inconclusive("cancelled")
    error = future.exception()
    if error is not None:
        return Inconclusive(type(error).__name__)
    rows = [row for row in future.result() if row.account == account]
    return BrokerOpenOrders(
        frozenset(row.orderRef for row in rows if row.orderRef),
        frozenset(str(row.orderId) for row in rows),
    )


def _consume_outcome(future: asyncio.Future) -> None:
    """Mark a detached task's outcome retrieved; the verdict is the request's."""
    if not future.cancelled():
        future.exception()


def canceled_report(order: Any, *, ts_ns: int) -> OrderStatusReport:
    """``CANCELED``, built from the order's own fields — the one status this
    module ever reports (D-C). Its own price, trigger price and trigger type,
    so the engine sees no amendment to apply first (Task 1.3); only the report's
    own times are ``ts_ns``."""
    return OrderStatusReport(
        account_id=order.account_id,
        instrument_id=order.instrument_id,
        venue_order_id=order.venue_order_id,
        order_side=order.side,
        order_type=order.order_type,
        time_in_force=order.time_in_force,
        order_status=OrderStatus.CANCELED,
        quantity=order.quantity,
        filled_qty=order.filled_qty,
        avg_px=Decimal(str(order.avg_px)),
        report_id=UUID4(),
        ts_accepted=order.ts_accepted,
        ts_last=ts_ns,
        ts_init=ts_ns,
        client_order_id=order.client_order_id,
        price=order.price if order.has_price else None,
        trigger_price=order.trigger_price if order.has_trigger_price else None,
        trigger_type=order.trigger_type if order.has_trigger_price else TriggerType.NO_TRIGGER,
    )


def clear_stale_order(
    node: Any, order: Any, log: Any, scope: str, *, logged: str | None = None
) -> str | None:
    """Reconcile one confirmed order ``CANCELED``; log it only once the re-read
    proves it took (the ``_log_taken`` discipline).

    Returns:
        The failure reason, or ``None`` when it took or there was nothing to
        clear. A failure whose reason is ``logged`` is not logged again.
    """
    current = node.cache.order(order.client_order_id)
    if current is None or not current.is_open or current.is_inflight:
        # Closed meanwhile, or the strategy sent a cancel or modify during the
        # read: IBKR's own answer to it is coming, and the sweep owns it.
        return None
    try:
        report = canceled_report(current, ts_ns=time.time_ns())
        took = node.kernel.exec_engine.reconcile_execution_report(report) is True
        reason = "" if took else "returned_false"
    except Exception as exc:  # noqa: BLE001 - hygiene must never stop a session
        took, reason = False, type(exc).__name__
    after = node.cache.order(order.client_order_id)
    if took and (after is None or after.status != OrderStatus.CANCELED):
        took, reason = False, "not_canceled"
    fields = {
        "scope": scope,
        "instrument_id": str(current.instrument_id),
        "strategy_id": str(current.strategy_id),
        "client_order_id": current.client_order_id.value,
        "side": current.side_string(),
        "quantity": str(current.quantity),
    }
    if took:
        _emit(log, "warning", CLEARED_EVENT, **fields, detail=UNDETERMINED)
        return None
    if reason != logged:
        _emit(log, "warning", CLEAR_FAILED_EVENT, **fields, reason=reason)
    return reason


class StaleOrderWatch:
    """Debounce and act, once per runtime cycle (D-E).

    Args:
        read: The broker read — :func:`broker_open_orders` unless a test says otherwise.
    """

    def __init__(self, read: OpenOrdersReader = broker_open_orders) -> None:
        self._read = read
        #: client order id → when first seen absent on a conclusive read
        self.pending: dict[str, float] = {}
        #: client order id → the reason its clear last failed (logged once)
        self.refused: dict[str, str] = {}
        self.failing = False

    def forget(self) -> None:
        """A connection loss: an absence seen before it must be seen twice again."""
        self.pending = {}

    async def check(
        self, node: Any, log: Any, now: float, *, skip: Collection[str], scope: str
    ) -> None:
        """One cycle. ``skip``: instruments with a position issue still
        standing — their orders wait (AC #5). Never raises."""
        try:
            candidates = [
                order
                for order in stale_accepted_orders(node.cache)
                if str(order.instrument_id) not in skip
            ]
            if not candidates:
                # Nothing left to ask about ends every streak.
                self.pending, self.refused, self.failing = {}, {}, False
                return
            answer = await self._read(node)
            if isinstance(answer, Inconclusive):
                self._failed(log, scope, answer.reason)
                return
            self.failing = False
            self._clear(node, log, scope, self._confirm(candidates, answer, now))
        except Exception as exc:  # noqa: BLE001 - hygiene must never stop a session
            self._failed(log, scope, type(exc).__name__)

    def _failed(self, log: Any, scope: str, reason: str) -> None:
        if not self.failing:
            _emit(log, "warning", CHECK_FAILED_EVENT, scope=scope, reason=reason)
        self.failing = True

    def _clear(self, node: Any, log: Any, scope: str, confirmed: list[Any]) -> None:
        refused = {}
        for order in confirmed:
            key = order.client_order_id.value
            reason = clear_stale_order(node, order, log, scope, logged=self.refused.get(key))
            if reason is not None:
                refused[key] = reason
        self.refused = refused

    def _confirm(self, candidates: list[Any], answer: BrokerOpenOrders, now: float) -> list[Any]:
        pending: dict[str, float] = {}
        confirmed = []
        for order in candidates:
            if answer.lists(order):
                continue
            first = self.pending.get(order.client_order_id.value, now)
            pending[order.client_order_id.value] = first
            if now - first >= STALE_ORDER_DEBOUNCE_SECONDS:
                confirmed.append(order)
        self.pending = pending
        return confirmed


def _emit(log: Any, level: str, event: str, **fields: Any) -> None:
    """Log without ever raising."""
    try:
        getattr(log, level)(event, **fields)
    except Exception:  # noqa: BLE001 - diagnostics must not change the outcome
        pass
