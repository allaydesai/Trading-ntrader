"""Pre-trade futures margin check for backtests (futures M3).

Nautilus 1.220 computes margin on a margin account but never refuses an order over it: the
risk engine returns early for margin accounts (``risk/engine.pyx``: "TODO: Determine risk
controls for margin") and the account clamps free balance at zero instead of failing. Left
alone, a futures backtest has unlimited leverage.

Every order path (strategy ``submit_order``/``submit_order_list``/``modify_order``, emulator
release, exec algorithms) reaches the risk engine through one message-bus endpoint,
``RiskEngine.execute``. :class:`FuturesMarginGate` sits on that endpoint and refuses, through
the risk engine's own ``_deny_order``/``_reject_modify_order`` (so the strategy receives a
normal ``OrderDenied``/``OrderModifyRejected``):

- a submit (or order list) whose new futures exposure needs more initial margin than the
  venue account has free;
- a modify that raises a working futures order's quantity beyond what is free.

Everything else goes straight on to the risk engine unchanged. The gate is installed only
when a run has a futures contract, so equity runs never see it.

"Free" is Nautilus' free balance: realised cash minus the margin it has locked (initial
margin for working orders, maintenance margin for open positions; dollars per contract via
``FuturesMarginModel``), less the initial margin of orders submitted on the same venue but
not yet accepted, which hold no reservation yet. Unrealised P&L is not counted (Nautilus does
not add it to the balance), and there are no margin calls: maintenance margin is locked,
never used to liquidate.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.execution.messages import ModifyOrder, SubmitOrder, SubmitOrderList
from nautilus_trader.model.enums import OrderSide, OrderStatus
from nautilus_trader.model.events import OrderModifyRejected, OrderUpdated
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, PositionId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order

from src.core.futures_costs import FuturesTerms, terms_by_instrument

RISK_ENDPOINT = "RiskEngine.execute"
DENIAL_REASON = "MARGIN_EXCEEDS_FREE_BALANCE"
ZERO = Decimal(0)


class FuturesMarginGate:
    """Refuses futures submits and modifies whose initial margin exceeds free balance."""

    def __init__(self, risk_engine, cache, msgbus, terms: Mapping[InstrumentId, FuturesTerms]):
        """Bind to a kernel's risk engine, cache and bus; :meth:`install` takes the endpoint."""
        self._risk = risk_engine
        self._cache = cache
        self._msgbus = msgbus
        self._terms = dict(terms)
        # client order id -> its gated modifies still unresolved; see _ModifyLedger
        self._ledgers: dict[ClientOrderId, _ModifyLedger] = {}
        self.denied_count = 0

    def install(self) -> None:
        """Route ``RiskEngine.execute`` through this gate."""
        self._msgbus.deregister(RISK_ENDPOINT, self._risk.execute)
        self._msgbus.register(RISK_ENDPOINT, self.execute)

    def execute(self, command) -> None:
        """Refuse an over-margin submit or modify; pass every other command on."""
        if isinstance(command, ModifyOrder):
            order = self._cache.order(command.client_order_id)
            if order is not None and order.instrument_id in self._terms:
                self.handle_modify(order, command.quantity, lambda: self._risk.execute(command))
                return
        if not self._refused(command):
            self._risk.execute(command)

    def handle_modify(self, order: Order, new_quantity, forward) -> None:
        """Check one modify of a futures order, then reject it or record and ``forward`` it."""
        ledger = self._ledgers.setdefault(order.client_order_id, _ModifyLedger(order))
        ledger.sync(order)
        reason = self.modify_denial_reason(order, new_quantity)
        if reason is not None:
            self.denied_count += 1
            self._risk._reject_modify_order(order, reason)
            ledger.skip_to_end(order)  # the gate's own rejection resolves no pending modify
            return
        ledger.forwarded(order, new_quantity)
        forward()
        ledger.after_forward(order)

    def _refused(self, command) -> bool:
        reason = None
        if isinstance(command, SubmitOrder):
            reason = self.denial_reason([command.order], command.position_id)
            if reason is not None:
                self._risk._deny_order(command.order, reason)
        elif isinstance(command, SubmitOrderList):
            reason = self.denial_reason(command.order_list.orders, command.position_id)
            if reason is not None:
                self._risk._deny_order_list(command.order_list, reason)
        if reason is not None:
            self.denied_count += 1
        return reason is not None

    def denial_reason(self, orders: list[Order], position_id: PositionId | None) -> str | None:
        """Why submitting ``orders`` must be denied, or ``None`` when the margin is there.

        Only orders without a parent (entries) count. What they close of the targeted
        position is credited once across the whole list, net of what other working orders on
        that position already claim to close.
        """
        entries = [
            o for o in orders if o.parent_order_id is None and o.instrument_id in self._terms
        ]
        if not entries:
            return None
        position = self._cache.position(position_id) if position_id is not None else None
        credit = _closing_credit(self._cache, position, {o.client_order_id for o in orders})
        required = ZERO
        contracts = ZERO
        for order in entries:
            exposure, credit = _new_exposure(order, position, credit)
            contracts += exposure
            required += exposure * self._terms[order.instrument_id].initial_margin_usd
        if required <= 0:
            return None
        free = self._free_usd(entries[0].instrument_id)
        if required <= free:
            return None
        names = ", ".join(sorted({str(o.instrument_id) for o in entries}))
        return (
            f"{DENIAL_REASON}: {contracts} {names} contract(s) need {required} USD "
            f"initial margin, free={free} USD"
        )

    def modify_denial_reason(self, order: Order, new_quantity) -> str | None:
        """Why raising ``order`` to ``new_quantity`` must be rejected, or ``None``.

        Price-only and quantity-reducing modifies always pass; the order's current quantity
        is already reserved (or counted as in flight), so only the increase is checked. Child
        orders are checked too unless reduce-only. A target no higher than one already held
        for an unresolved modify of the same order adds nothing and passes.
        """
        terms = self._terms.get(order.instrument_id)
        if terms is None or new_quantity is None or order.is_reduce_only:
            return None
        current, new = order.quantity.as_decimal(), new_quantity.as_decimal()
        ledger = self._ledgers.get(order.client_order_id)
        held_top = ledger.max_target() if ledger else None
        if new <= current or (held_top is not None and new <= held_top):
            return None
        required = (new - current) * terms.initial_margin_usd
        # This order's own hold is covered by the new target: check without it.
        free = self._free_usd(order.instrument_id, exclude=order.client_order_id)
        if required <= free:
            return None
        return (
            f"{DENIAL_REASON}: modify adds {new - current} {order.instrument_id} contract(s) "
            f"needing {required} USD initial margin, free={free} USD"
        )

    def _free_usd(self, instrument_id: InstrumentId, exclude=None) -> Decimal:
        """Venue free balance less the margin of its submitted-but-unaccepted orders."""
        venue: Venue = instrument_id.venue
        account = self._cache.account_for_venue(venue)
        instrument = self._cache.instrument(instrument_id)
        if account is None or instrument is None:
            return ZERO
        free_money = account.balance_free(instrument.quote_currency)
        free = free_money.as_decimal() if free_money is not None else ZERO
        for order in self._cache.orders_inflight(venue=venue):
            terms = self._terms.get(order.instrument_id)
            # PENDING_UPDATE/PENDING_CANCEL are in flight too, but already reserved.
            if terms is None or order.status != OrderStatus.SUBMITTED:
                continue
            if not order.is_reduce_only and order.parent_order_id is None:
                free -= order.leaves_qty.as_decimal() * terms.initial_margin_usd
        return free - _held_usd(self._cache, self._ledgers, self._terms, venue, exclude)


def _closing_credit(cache, position, submitting: set) -> Decimal:
    """Contracts of ``position`` still free to close: its size less what working opposite
    orders on it (other than ``submitting``) already claim. Fills and cancels release
    their claim as soon as Nautilus updates the cache."""
    if position is None or not position.is_open:
        return ZERO
    credit = position.quantity.as_decimal()
    closing_side = OrderSide.SELL if position.is_long else OrderSide.BUY
    for order in cache.orders_for_position(position.id):
        if order.client_order_id in submitting or order.side != closing_side:
            continue
        if order.is_open or order.is_inflight:
            credit -= order.leaves_qty.as_decimal()
    return max(credit, ZERO)


class _ModifyLedger:
    """One order's gated modifies, oldest first, matched against the order's own events.

    Each entry is the target of a quantity-raising modify, or ``None`` for any other modify.
    The venue resolves an order's modifies in order, so:

    - an ``OrderModifyRejected`` resolves the oldest entry;
    - an ``OrderUpdated`` that changes the quantity resolves the entry with that target
      (or, for a reducing modify, the oldest ``None``);
    - an ``OrderUpdated`` that leaves the quantity unchanged (a price-only modify, or an
      engine-driven move such as a trailing stop's) never releases a raise: at most it
      resolves a ``None`` at the head.

    Rejections the gate or risk engine raise at once are matched to their own modify where
    they happen, not to the queue's head. The hold is margin for
    ``max(pending targets) - current quantity``, floored at 0.
    """

    def __init__(self, order: Order) -> None:
        """Start watching ``order`` from its current events and quantity."""
        self.pending: list[Decimal | None] = []
        self._cursor = len(order.events)
        self._forwarded_at = self._cursor
        self._quantity = order.quantity.as_decimal()

    def max_target(self) -> Decimal | None:
        """The highest pending raise target, or ``None`` when no raise is pending."""
        targets = [t for t in self.pending if t is not None]
        return max(targets) if targets else None

    def held_contracts(self, order: Order) -> Decimal:
        """Contracts of pending raises beyond the order's current quantity."""
        top = self.max_target()
        return max(top - order.quantity.as_decimal(), ZERO) if top is not None else ZERO

    def forwarded(self, order: Order, new_quantity) -> None:
        """Record a modify the gate is about to forward."""
        raises = new_quantity is not None and new_quantity.as_decimal() > self._quantity
        self.pending.append(new_quantity.as_decimal() if raises else None)
        self._forwarded_at = len(order.events)

    def after_forward(self, order: Order) -> None:
        """A rejection raised while forwarding (risk-engine checks) belongs to this modify."""
        if any(isinstance(e, OrderModifyRejected) for e in order.events[self._forwarded_at :]):
            self.pending.pop()
            self._cursor = len(order.events)

    def skip_to_end(self, order: Order) -> None:
        """Treat every event so far as seen (after the gate's own rejection)."""
        self._cursor = len(order.events)

    def sync(self, order: Order) -> None:
        """Resolve pending entries from the order's events since the last sync."""
        events = order.events
        for event in events[self._cursor :]:
            if isinstance(event, OrderModifyRejected):
                if self.pending:
                    self.pending.pop(0)
            elif isinstance(event, OrderUpdated):
                self._updated(event.quantity.as_decimal())
        self._cursor = len(events)

    def _updated(self, quantity: Decimal) -> None:
        if quantity == self._quantity:
            if self.pending and self.pending[0] is None:
                self.pending.pop(0)
            return
        self._quantity = quantity
        if quantity in self.pending:
            self.pending.remove(quantity)
        elif None in self.pending:
            self.pending.remove(None)


def _held_usd(
    cache,
    ledgers: dict,
    terms: Mapping[InstrumentId, FuturesTerms],
    venue: Venue,
    exclude: ClientOrderId | None = None,
) -> Decimal:
    """Margin held for unresolved quantity raises on ``venue``; prunes closed orders' ledgers."""
    total = ZERO
    for client_order_id, ledger in list(ledgers.items()):
        order = cache.order(client_order_id)
        if order is None or order.is_closed:
            del ledgers[client_order_id]
            continue
        ledger.sync(order)
        # ClientOrderId's __eq__ raises on None, so test identity first.
        mine = exclude is not None and client_order_id == exclude
        if order.instrument_id.venue == venue and not mine:
            contracts = ledger.held_contracts(order)
            total += contracts * terms[order.instrument_id].initial_margin_usd
    return total


def _new_exposure(order: Order, position, credit: Decimal) -> tuple[Decimal, Decimal]:
    """``(new contracts, remaining credit)``: the order less what it closes of ``position``."""
    if order.is_reduce_only:
        return ZERO, credit
    quantity = order.quantity.as_decimal()
    if position is None or not position.is_open or position.instrument_id != order.instrument_id:
        return quantity, credit
    if (order.side == OrderSide.SELL) != position.is_long:
        return quantity, credit
    used = min(quantity, credit)
    return quantity - used, credit - used


def install_futures_margin_gate(
    engine: BacktestEngine, instruments: list[Instrument]
) -> FuturesMarginGate | None:
    """Install the gate when ``instruments`` include a futures contract; else do nothing."""
    terms = terms_by_instrument(instruments)
    if not terms:
        return None
    kernel = engine.kernel
    gate = FuturesMarginGate(kernel.risk_engine, kernel.cache, kernel.msgbus, terms)
    gate.install()
    return gate
