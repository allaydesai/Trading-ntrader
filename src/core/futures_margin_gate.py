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
- any modify that raises a working futures order's quantity (reason
  ``FUTURES_QTY_INCREASE_NOT_SUPPORTED``): cancel and resubmit instead, so the new size
  gets a fresh margin check;
- any modify that sets a futures order's quantity while an earlier modify of that order is
  still outstanding (reason ``FUTURES_MODIFY_PENDING``). Each check compares against the
  cached quantity, which an outstanding modify has not changed yet, so two queued modifies
  (3 -> 1, then 3 -> 2) would otherwise together raise 1 -> 2 unchecked.

Quantity-reducing and price-only modifies, and everything else, go straight on to the risk
engine unchanged. The gate keeps no pending-modify state: margin checks read only orders'
actual current quantities and open positions. The gate is installed only when a run has a
futures contract, so equity runs never see it.

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
from nautilus_trader.model.events import OrderModifyRejected, OrderPendingUpdate, OrderUpdated
from nautilus_trader.model.identifiers import InstrumentId, PositionId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order

from src.core.futures_costs import FuturesTerms, terms_by_instrument

RISK_ENDPOINT = "RiskEngine.execute"
DENIAL_REASON = "MARGIN_EXCEEDS_FREE_BALANCE"
QTY_INCREASE_REASON = "FUTURES_QTY_INCREASE_NOT_SUPPORTED"
MODIFY_PENDING_REASON = "FUTURES_MODIFY_PENDING"
ZERO = Decimal(0)


class FuturesMarginGate:
    """Refuses futures submits and modifies whose initial margin exceeds free balance."""

    def __init__(self, risk_engine, cache, msgbus, terms: Mapping[InstrumentId, FuturesTerms]):
        """Bind to a kernel's risk engine, cache and bus; :meth:`install` takes the endpoint."""
        self._risk = risk_engine
        self._cache = cache
        self._msgbus = msgbus
        self._terms = dict(terms)
        self.denied_count = 0

    def install(self) -> None:
        """Route ``RiskEngine.execute`` through this gate."""
        self._msgbus.deregister(RISK_ENDPOINT, self._risk.execute)
        self._msgbus.register(RISK_ENDPOINT, self.execute)

    def execute(self, command) -> None:
        """Refuse an over-margin submit or a futures size increase; pass the rest on."""
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
        """Why modifying ``order`` to ``new_quantity`` must be rejected, or ``None``.

        A futures order's quantity may not be raised by a modify, whatever the margin: an
        increase would need margin held for a change the venue has not applied yet. Cancel
        and resubmit instead. Decreases and price-only modifies (``new_quantity`` is
        ``None``) pass; Nautilus releases margin only once a decrease is applied. A modify
        that sets a quantity is also refused while an earlier modify of the order is still
        outstanding; price-only modifies are not (the venue applies them to whatever the
        quantity is by then).
        """
        if order.instrument_id not in self._terms or new_quantity is None:
            return None
        if _earlier_modifies_outstanding(order) > 0:
            return (
                f"{MODIFY_PENDING_REASON}: an earlier modify of {order.client_order_id} is "
                "not applied or rejected yet; wait for it to resolve"
            )
        current, new = order.quantity.as_decimal(), new_quantity.as_decimal()
        if new <= current:
            return None
        return (
            f"{QTY_INCREASE_REASON}: modify would raise {order.instrument_id} from {current} "
            f"to {new} contract(s); cancel and resubmit instead"
        )

    def _free_usd(self, instrument_id: InstrumentId) -> Decimal:
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
        return free


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


def _earlier_modifies_outstanding(order: Order) -> int:
    """Modifies of ``order`` sent before this one that the venue has not resolved yet.

    Read from the order's own events in the cache (no gate state). Nautilus' status alone
    cannot say: ``Strategy.modify_order`` sets ``PENDING_UPDATE`` before sending the command,
    and the first ``OrderUpdated``/``OrderModifyRejected`` clears it even while a second
    modify is queued. So: each ``OrderPendingUpdate`` opens a modify, each
    ``OrderModifyRejected`` (venue, risk engine or this gate) closes one, and an
    ``OrderUpdated`` closes one while any is open. This command's own ``OrderPendingUpdate``
    (the order's latest event when it came through ``Strategy.modify_order``) is not counted.
    """
    events = list(order.events)
    if events and isinstance(events[-1], OrderPendingUpdate):
        events.pop()  # this modify's own pending event
    elif order.status == OrderStatus.PENDING_UPDATE:
        return 1  # pending, but not because of this command: an earlier modify is open
    outstanding = 0
    for event in events:
        if isinstance(event, OrderPendingUpdate):
            outstanding += 1
        elif isinstance(event, (OrderModifyRejected, OrderUpdated)) and outstanding:
            outstanding -= 1
    return outstanding


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
