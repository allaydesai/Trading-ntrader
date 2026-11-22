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
        # client order id -> (approved quantity, its modify's ticket); see _unapplied_holds_usd
        self._holds: dict[ClientOrderId, tuple[Decimal, int]] = {}
        self._modifies_seen: dict[ClientOrderId, int] = {}
        self.denied_count = 0

    def install(self) -> None:
        """Route ``RiskEngine.execute`` through this gate."""
        self._msgbus.deregister(RISK_ENDPOINT, self._risk.execute)
        self._msgbus.register(RISK_ENDPOINT, self.execute)

    def execute(self, command) -> None:
        """Refuse an over-margin submit or modify; pass every other command on."""
        if not self._refused(command):
            self._risk.execute(command)

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
        elif isinstance(command, ModifyOrder):
            order = self._cache.order(command.client_order_id)
            reason = self.modify_denial_reason(order, command.quantity) if order else None
            if reason is not None:
                self._risk._reject_modify_order(order, reason)
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
        orders are checked too unless reduce-only. An approved increase is held against free
        balance until the exchange resolves that modify; a denied one leaves an earlier hold.
        """
        ticket = self._modifies_seen.get(order.client_order_id, 0) + 1
        self._modifies_seen[order.client_order_id] = ticket
        terms = self._terms.get(order.instrument_id)
        if terms is None or new_quantity is None or order.is_reduce_only:
            return None
        added = new_quantity.as_decimal() - order.quantity.as_decimal()
        if added <= 0:
            return None
        required = added * terms.initial_margin_usd
        # This order's own earlier hold overlaps the new target: check without it.
        free = self._free_usd(order.instrument_id, exclude=order.client_order_id)
        if required <= free:
            self._holds[order.client_order_id] = (new_quantity.as_decimal(), ticket)
            return None
        return (
            f"{DENIAL_REASON}: modify adds {added} {order.instrument_id} contract(s) needing "
            f"{required} USD initial margin, free={free} USD"
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
        return free - _unapplied_holds_usd(self._cache, self._holds, self._terms, venue, exclude)


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


def _unapplied_holds_usd(
    cache,
    holds: dict,
    terms: Mapping[InstrumentId, FuturesTerms],
    venue: Venue,
    exclude: ClientOrderId | None = None,
) -> Decimal:
    """Margin of approved quantity increases the exchange has not resolved yet.

    Every ``ModifyOrder`` passes through the gate and the venue resolves an order's
    modifies in order, each with one ``OrderUpdated`` or ``OrderModifyRejected``. A hold
    whose ticket is the n-th modify of its order is therefore released once that order
    has n resolutions (applied or rejected), or when it closes. ``holds`` is pruned.
    """
    total = ZERO
    for client_order_id, (target, ticket) in list(holds.items()):
        order = cache.order(client_order_id)
        if order is None or order.is_closed or _modify_resolutions(order) >= ticket:
            del holds[client_order_id]
        elif order.instrument_id.venue == venue and client_order_id != exclude:
            added = max(target - order.quantity.as_decimal(), ZERO)
            total += added * terms[order.instrument_id].initial_margin_usd
    return total


def _modify_resolutions(order: Order) -> int:
    return sum(isinstance(e, (OrderUpdated, OrderModifyRejected)) for e in order.events)


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
