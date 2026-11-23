"""Which modifies the margin gate passed on that the venue has not answered yet, per order.

A modify that raises an order's margin (a new FX limit or trigger) is checked against free
balance when the gate passes it on, but Nautilus reserves nothing for it until the venue
applies it. :class:`PendingModifies` keeps, per client order id, the quantity/price/trigger
each passed modify asked for, so the gate can hold back the still-unreserved increase (see
``MarginGate``) and know whether an order has a modify outstanding.

A request is settled by the order's own events, read lazily in order (the venue answers an
order's modifies in the order sent):

- ``OrderModifyRejected`` answers the oldest outstanding request (Nautilus emits it only in
  reply to a modify);
- ``OrderUpdated`` answers it too, except on order kinds Nautilus also updates unsolicited
  (trailing stops, market-to-limit, reduce-only, contingent, emulated and exec-algorithm
  orders), where it answers only when it carries every value the request set: a trail or an
  OUO sync arriving mid-modify leaves the request outstanding;
- the order closing (filled, cancelled, expired, rejected) settles all of them.

Two events must never settle an earlier request: the gate's own rejection of a later modify,
and the risk engine rejecting the modify being passed on there and then. Both land
synchronously at a known index of the order's events, which is claimed first, so a re-entrant
read (a strategy submitting from ``on_order_modify_rejected``) skips them too.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from nautilus_trader.model.enums import ContingencyType, OrderType, TriggerType
from nautilus_trader.model.events import OrderModifyRejected, OrderUpdated
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import Order

_ANSWERS = (OrderModifyRejected, OrderUpdated)

#: Order types Nautilus 1.220 sends ``OrderUpdated`` to without a modify asking: the matching
#: engine and emulator trail trailing stops; a MARKET_TO_LIMIT's first fill sets its price.
_SELF_UPDATING_TYPES = frozenset(
    {OrderType.TRAILING_STOP_MARKET, OrderType.TRAILING_STOP_LIMIT, OrderType.MARKET_TO_LIMIT}
)


@dataclass(frozen=True)
class Requested:
    """What one passed modify asked for (``None``: left unchanged)."""

    quantity: Quantity | None = None
    price: Price | None = None
    trigger_price: Price | None = None

    def applied_by(self, event: OrderUpdated) -> bool:
        """Whether ``event`` carries every value this request set."""
        return all(
            want is None or got == want
            for want, got in (
                (self.quantity, event.quantity),
                (self.price, event.price),
                (self.trigger_price, event.trigger_price),
            )
        )


@dataclass
class _Ledger:
    requested: deque[Requested] = field(default_factory=deque)
    read: int = 0  # order events already read
    claimed: set[int] = field(default_factory=set)  # indices answering no recorded request


def gets_unsolicited_updates(order: Order) -> bool:
    """Whether ``order`` can receive an ``OrderUpdated`` that answers no modify (1.220).

    Besides the self-updating types: reduce-only (a fill past the position is clipped),
    contingent (the matching engine syncs an OUO sibling's quantity on a linked fill), emulated
    (the emulator updates it locally) and exec-algorithm orders (the algorithm reduces its
    primary). On any other order the only backtest source is the venue applying a modify.
    """
    contingency = getattr(order, "contingency_type", ContingencyType.NO_CONTINGENCY)
    emulation = getattr(order, "emulation_trigger", TriggerType.NO_TRIGGER)
    return (
        getattr(order, "order_type", None) in _SELF_UPDATING_TYPES
        or bool(getattr(order, "is_reduce_only", False))
        or contingency not in (None, ContingencyType.NO_CONTINGENCY)
        or emulation not in (None, TriggerType.NO_TRIGGER)
        or getattr(order, "exec_algorithm_id", None) is not None
    )


class PendingModifies:
    """Per order, the passed modifies the venue has not answered yet (module docstring)."""

    def __init__(self) -> None:
        """Start empty: nothing is pending before the gate passes a modify on."""
        self._ledgers: dict[ClientOrderId, _Ledger] = {}

    def __len__(self) -> int:
        """Orders with a ledger (settled ones are dropped as they are read)."""
        return len(self._ledgers)

    def order_ids(self) -> list[ClientOrderId]:
        """Client order ids that may still have a modify outstanding."""
        return list(self._ledgers)

    def outstanding(self, order: Order) -> tuple[Requested, ...]:
        """``order``'s unanswered requests, oldest first, after reading its new events."""
        ledger = self._ledgers.get(order.client_order_id)
        if ledger is None:
            return ()
        _read(order, ledger)
        if order.is_closed or not ledger.requested:
            # nothing left to settle wrongly: a later modify starts a fresh ledger
            del self._ledgers[order.client_order_id]
            return ()
        return tuple(ledger.requested)

    def rejecting(self, order: Order) -> None:
        """Claim the event the gate's rejection of a modify of ``order`` is about to add."""
        ledger = self._ledgers.get(order.client_order_id)
        if ledger is not None:
            ledger.claimed.add(len(order.events))

    def forward(self, order: Order, command, send: Callable[[object], None]) -> None:
        """``send(command)`` and record what it asks unless it was answered there and then."""
        index = len(order.events)
        ledger = self._ledgers.get(order.client_order_id)
        if ledger is not None:
            ledger.claimed.add(index)
        send(command)
        events = order.events
        answered = len(events) > index and isinstance(events[index], _ANSWERS)
        if ledger is not None and not answered:
            ledger.claimed.discard(index)
        if answered:
            return
        ledger = self._ledgers.setdefault(order.client_order_id, _Ledger(read=len(events)))
        ledger.requested.append(Requested(command.quantity, command.price, command.trigger_price))


def _read(order: Order, ledger: _Ledger) -> None:
    """Settle ``ledger``'s requests with the order events it has not read yet."""
    events = order.events
    unsolicited = gets_unsolicited_updates(order)
    for index in range(ledger.read, len(events)):
        event = events[index]
        if not isinstance(event, _ANSWERS) or not ledger.requested:
            continue
        if index in ledger.claimed:
            continue
        head = ledger.requested[0]
        if isinstance(event, OrderModifyRejected) or not unsolicited or head.applied_by(event):
            ledger.requested.popleft()
    ledger.read = len(events)
    ledger.claimed = {i for i in ledger.claimed if i >= ledger.read}
