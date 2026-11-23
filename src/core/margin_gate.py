"""Pre-trade margin check for backtests: futures (M3) and spot FX (FX M4).

Nautilus 1.220 computes margin on a margin account but never refuses an order over it: the
risk engine returns early for margin accounts (``risk/engine.pyx``: "TODO: Determine risk
controls for margin") and the account clamps free balance at zero instead of failing. Left
alone, a futures or FX backtest has unlimited leverage.

Every order path (strategy ``submit_order``/``submit_order_list``/``modify_order``, emulator
release, exec algorithms) reaches the risk engine through one message-bus endpoint,
``RiskEngine.execute``. :class:`MarginGate` sits on that endpoint and refuses, through the
risk engine's own ``_deny_order``/``_reject_modify_order`` (so the strategy receives a normal
``OrderDenied``/``OrderModifyRejected``):

- a submit (or order list) whose new exposure needs more initial margin than the venue
  account has free (reason ``MARGIN_EXCEEDS_FREE_BALANCE``);
- any modify that raises a working order's quantity (``FUTURES_``/``FX_``
  ``QTY_INCREASE_NOT_SUPPORTED``): cancel and resubmit instead, so the new size gets a fresh
  margin check;
- any modify that sets an order's quantity while an earlier modify of that order is still
  outstanding (``FUTURES_``/``FX_`` ``MODIFY_PENDING``). Each check compares against the cached
  quantity, which an outstanding modify has not changed yet, so two queued modifies (3 -> 1,
  then 3 -> 2) would otherwise together raise 1 -> 2 unchecked.

What one unit needs is per instrument (a :class:`MarginRequirement`):

- **futures**: dollars per contract from the spec, at any price (``FuturesRequirement``);
- **FX**: ``units x price x USD per quote unit x margin_pct`` (``FxRequirement``). The price is
  the order's limit (or stop trigger), else the instrument's latest ``MID`` price in the cache;
  a cross converts at the cache rate. An order the gate cannot price (no bar or no cross rate
  yet) is passed on: its fill would have no rate either, and M3's guard fails such a run.

Quantity-reducing and price-only modifies, and everything else, go straight on to the risk
engine unchanged. The gate keeps no pending-modify state: margin checks read only orders'
actual current quantities and open positions. The gate is installed only when a run has a
futures contract or an FX pair, so equity runs never see it.

"Free" is Nautilus' free balance in the account's currency (USD for an FX venue's single
currency account, the quote currency otherwise): realised cash minus the margin it has locked
(initial margin for working orders, maintenance for open positions), less the initial margin of
orders submitted on the same venue but not yet accepted, which hold no reservation yet.
Unrealised P&L is not counted (Nautilus does not add it to the balance), and there are no
margin calls: maintenance margin is locked, never used to liquidate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Protocol

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.execution.messages import ModifyOrder, SubmitOrder, SubmitOrderList
from nautilus_trader.model.enums import OrderSide, OrderStatus, PriceType
from nautilus_trader.model.events import OrderModifyRejected, OrderPendingUpdate, OrderUpdated
from nautilus_trader.model.identifiers import InstrumentId, PositionId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order

from src.core.futures_costs import FuturesTerms, terms_by_instrument
from src.core.fx_costs import fx_terms_by_instrument
from src.core.fx_rates import usd_per_quote

RISK_ENDPOINT = "RiskEngine.execute"
DENIAL_REASON = "MARGIN_EXCEEDS_FREE_BALANCE"
ZERO = Decimal(0)


class MarginRequirement(Protocol):
    """What ``quantity`` of one instrument needs in initial margin (USD), and how to name it."""

    @property
    def prefix(self) -> str:
        """Reason prefix of the modify rules: ``FUTURES``, ``FX``."""

    @property
    def unit(self) -> str:
        """How quantities read in reasons: ``contract(s)``, ``unit(s)``."""

    def initial_usd(self, quantity: Decimal, order: Order, cache) -> Decimal | None:
        """USD initial margin for ``quantity`` of ``order``'s instrument; None: unpriceable."""


@dataclass(frozen=True)
class FuturesRequirement:
    """Futures: dollars per contract (the spec's initial margin), at any price."""

    terms: FuturesTerms
    prefix: str = "FUTURES"
    unit: str = "contract(s)"

    def initial_usd(self, quantity: Decimal, order: Order, cache) -> Decimal | None:  # noqa: D102
        return quantity * self.terms.initial_margin_usd


@dataclass(frozen=True)
class FxRequirement:
    """Spot FX: a fraction of the USD notional (3% is 0.03), at the order's reference price."""

    margin_fraction: Decimal
    prefix: str = "FX"
    unit: str = "unit(s)"

    def initial_usd(self, quantity: Decimal, order: Order, cache) -> Decimal | None:  # noqa: D102
        instrument = cache.instrument(order.instrument_id)
        price = _reference_price(order, cache)
        if instrument is None or price is None:
            return None
        rate = usd_per_quote(instrument, price, cache, order.side)
        if rate is None:
            return None
        required = quantity * price * rate * self.margin_fraction
        return required.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def _reference_price(order: Order, cache) -> Decimal | None:
    """The limit price, else the stop trigger, else the latest ``MID`` price in the cache."""
    for name in ("price", "trigger_price"):
        price = getattr(order, name, None)
        if price is not None:
            return price.as_decimal()
    latest = cache.price(order.instrument_id, PriceType.MID)
    return latest.as_decimal() if latest is not None else None


class MarginGate:
    """Refuses submits and modifies whose initial margin exceeds the account's free balance."""

    def __init__(self, risk_engine, cache, msgbus, terms: Mapping[InstrumentId, MarginRequirement]):
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
        """Refuse an over-margin submit or a size increase; pass the rest on."""
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
        units = ZERO
        for order in entries:
            exposure, credit = _new_exposure(order, position, credit)
            if exposure <= 0:
                continue
            needed = self._terms[order.instrument_id].initial_usd(exposure, order, self._cache)
            if needed is None:
                return None  # unpriceable: its fill would have no rate either (see module)
            units += exposure
            required += needed
        if required <= 0:
            return None
        free = self._free_usd(entries[0].instrument_id)
        if required <= free:
            return None
        names = ", ".join(sorted({str(o.instrument_id) for o in entries}))
        unit = self._terms[entries[0].instrument_id].unit
        return (
            f"{DENIAL_REASON}: {units} {names} {unit} need {required} USD "
            f"initial margin, free={free} USD"
        )

    def modify_denial_reason(self, order: Order, new_quantity) -> str | None:
        """Why modifying ``order`` to ``new_quantity`` must be rejected, or ``None``.

        A gated order's quantity may not be raised by a modify, whatever the margin: an
        increase would need margin held for a change the venue has not applied yet. Cancel
        and resubmit instead. Decreases and price-only modifies (``new_quantity`` is
        ``None``) pass; Nautilus releases margin only once a decrease is applied. A modify
        that sets a quantity is also refused while an earlier modify of the order is still
        outstanding; price-only modifies are not (the venue applies them to whatever the
        quantity is by then).
        """
        requirement = self._terms.get(order.instrument_id)
        if requirement is None or new_quantity is None:
            return None
        if _earlier_modifies_outstanding(order) > 0:
            return (
                f"{requirement.prefix}_MODIFY_PENDING: an earlier modify of "
                f"{order.client_order_id} is not applied or rejected yet; wait for it to resolve"
            )
        current, new = order.quantity.as_decimal(), new_quantity.as_decimal()
        if new <= current:
            return None
        return (
            f"{requirement.prefix}_QTY_INCREASE_NOT_SUPPORTED: modify would raise "
            f"{order.instrument_id} from {current} to {new} {requirement.unit}; "
            "cancel and resubmit instead"
        )

    def _free_usd(self, instrument_id: InstrumentId) -> Decimal:
        """Venue free balance less the margin of its submitted-but-unaccepted orders."""
        venue: Venue = instrument_id.venue
        account = self._cache.account_for_venue(venue)
        instrument = self._cache.instrument(instrument_id)
        if account is None or instrument is None:
            return ZERO
        currency = getattr(account, "base_currency", None) or instrument.quote_currency
        free_money = account.balance_free(currency)
        free = free_money.as_decimal() if free_money is not None else ZERO
        for order in self._cache.orders_inflight(venue=venue):
            requirement = self._terms.get(order.instrument_id)
            # PENDING_UPDATE/PENDING_CANCEL are in flight too, but already reserved.
            if requirement is None or order.status != OrderStatus.SUBMITTED:
                continue
            if not order.is_reduce_only and order.parent_order_id is None:
                held = requirement.initial_usd(order.leaves_qty.as_decimal(), order, self._cache)
                free -= held or ZERO
        return free


def _closing_credit(cache, position, submitting: set) -> Decimal:
    """Units of ``position`` still free to close: its size less what working opposite
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
    """``(new units, remaining credit)``: the order less what it closes of ``position``."""
    if order.is_reduce_only:
        return ZERO, credit
    quantity = order.quantity.as_decimal()
    if position is None or not position.is_open or position.instrument_id != order.instrument_id:
        return quantity, credit
    if (order.side == OrderSide.SELL) != position.is_long:
        return quantity, credit
    used = min(quantity, credit)
    return quantity - used, credit - used


def margin_requirements(instruments: list[Instrument]) -> dict[InstrumentId, MarginRequirement]:
    """Per instrument, what it needs in margin: futures contracts and spot FX pairs only."""
    requirements: dict[InstrumentId, MarginRequirement] = {
        iid: FuturesRequirement(t) for iid, t in terms_by_instrument(instruments).items()
    }
    for iid, fx in fx_terms_by_instrument(instruments).items():
        requirements[iid] = FxRequirement(fx.margin_fraction)
    return requirements


def install_margin_gate(engine: BacktestEngine, instruments: list[Instrument]) -> MarginGate | None:
    """Install the gate when ``instruments`` include a future or an FX pair; else do nothing."""
    requirements = margin_requirements(instruments)
    if not requirements:
        return None
    kernel = engine.kernel
    gate = MarginGate(kernel.risk_engine, kernel.cache, kernel.msgbus, requirements)
    gate.install()
    return gate
