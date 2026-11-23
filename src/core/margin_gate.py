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
  then 3 -> 2) would otherwise together raise 1 -> 2 unchecked;
- an FX modify whose new limit or trigger raises an entry's margin by more than is free
  (``MARGIN_EXCEEDS_FREE_BALANCE``);
- an FX entry that would rest (it has a limit or trigger) but cannot be margined yet because
  its quote has no USD rate (``FX_MARGIN_UNPRICEABLE``).

What one unit needs is per instrument (a :class:`MarginRequirement`):

- **futures**: dollars per contract from the spec, at any price (``FuturesRequirement``);
- **FX**: ``units x price x USD per quote unit x margin_pct`` (``FxRequirement``). The price is
  the order's limit (or stop trigger), else the instrument's latest ``MID`` price in the cache;
  a cross converts at the cache rate. A market order the gate cannot price (no bar or no cross
  rate yet) is passed on: it fills at once, without a rate M3's guard fails the run, and with
  no market the venue rejects it. Any other type (limit, stop, trailing, even one with no
  trigger yet) can rest, so it is refused instead (it could fill later, unmargined).

Quantity-reducing and price-only modifies, and everything else, go straight on to the risk
engine unchanged. The gate's only state is :class:`~src.core.pending_modifies.PendingModifies`:
the modifies it passed on that the venue has not answered yet, per client order id. It backs
the one-quantity-modify-at-a-time rule and the unreserved margin below; each request settles
when the venue applies or rejects it or the order closes. The gate is installed only when a
run has a futures contract or an FX pair, so equity runs never see it.

"Free" is Nautilus' free balance in the account's currency (USD for an FX venue's single
currency account, the quote currency otherwise): realised cash minus the margin it has locked
(initial margin for working orders, maintenance for open positions), less the initial margin of
orders submitted on the same venue but not yet accepted, which hold no reservation yet, and
less the increase each pending FX reprice on the venue adds (Nautilus reserves an order at its
current price until the venue applies the modify; an order counts its largest one).
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
from nautilus_trader.model.enums import OrderSide, OrderStatus, OrderType, PriceType
from nautilus_trader.model.identifiers import InstrumentId, PositionId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order

from src.core.futures_costs import FuturesTerms, terms_by_instrument
from src.core.fx_costs import fx_terms_by_instrument
from src.core.fx_rates import usd_per_quote
from src.core.pending_modifies import PendingModifies

RISK_ENDPOINT = "RiskEngine.execute"
DENIAL_REASON = "MARGIN_EXCEEDS_FREE_BALANCE"
UNPRICEABLE = "MARGIN_UNPRICEABLE"
ZERO = Decimal(0)


class MarginRequirement(Protocol):
    """What ``quantity`` of one instrument needs in initial margin (USD), and how to name it."""

    @property
    def prefix(self) -> str:
        """Reason prefix of the modify rules: ``FUTURES``, ``FX``."""

    @property
    def unit(self) -> str:
        """How quantities read in reasons: ``contract(s)``, ``unit(s)``."""

    @property
    def price_sensitive(self) -> bool:
        """Whether the requirement moves with the order's price (FX yes, futures no)."""

    def initial_usd(
        self, quantity: Decimal, order: Order, cache, price: Decimal | None = None
    ) -> Decimal | None:
        """USD initial margin for ``quantity`` of ``order``'s instrument; None: unpriceable.

        ``price`` overrides the order's reference price (a modify's new limit or trigger).
        """


@dataclass(frozen=True)
class FuturesRequirement:
    """Futures: dollars per contract (the spec's initial margin), at any price."""

    terms: FuturesTerms
    prefix: str = "FUTURES"
    unit: str = "contract(s)"
    price_sensitive: bool = False

    def initial_usd(  # noqa: D102
        self, quantity: Decimal, order: Order, cache, price: Decimal | None = None
    ) -> Decimal | None:
        return quantity * self.terms.initial_margin_usd


@dataclass(frozen=True)
class FxRequirement:
    """Spot FX: a fraction of the USD notional (3% is 0.03), at the order's reference price."""

    margin_fraction: Decimal
    prefix: str = "FX"
    unit: str = "unit(s)"
    price_sensitive: bool = True

    def initial_usd(  # noqa: D102
        self, quantity: Decimal, order: Order, cache, price: Decimal | None = None
    ) -> Decimal | None:
        instrument = cache.instrument(order.instrument_id)
        price = price if price is not None else _reference_price(order, cache)
        if instrument is None or price is None:
            return None
        rate = usd_per_quote(instrument, price, cache, order.side)
        if rate is None:
            return None
        required = quantity * price * rate * self.margin_fraction
        return required.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def _new_reference(order: Order, new_price, new_trigger) -> Decimal | None:
    """The reference price a modify would leave (limit first, as ``_reference_price``)."""
    if new_price is None and new_trigger is None:
        return None
    if getattr(order, "price", None) is not None:
        return (new_price or order.price).as_decimal()  # type: ignore[attr-defined]
    if getattr(order, "trigger_price", None) is not None:
        return (new_trigger or order.trigger_price).as_decimal()  # type: ignore[attr-defined]
    return None


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
        self._pending = PendingModifies()
        self.denied_count = 0

    def install(self) -> None:
        """Route ``RiskEngine.execute`` through this gate."""
        self._msgbus.deregister(RISK_ENDPOINT, self._risk.execute)
        self._msgbus.register(RISK_ENDPOINT, self.execute)

    def execute(self, command) -> None:
        """Refuse an over-margin submit or a size increase; pass the rest on."""
        if not isinstance(command, ModifyOrder):
            if not self._refused(command):
                self._risk.execute(command)
            return
        order = self._cache.order(command.client_order_id)
        if order is None or order.instrument_id not in self._terms:
            self._risk.execute(command)
            return
        reason = self.modify_denial_reason(
            order, command.quantity, command.price, command.trigger_price
        )
        if reason is None:
            self._pending.forward(order, command, self._risk.execute)
            return
        self._pending.rejecting(order)
        self._risk._reject_modify_order(order, reason)
        self.denied_count += 1

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
        that position already claim to close. An entry the gate cannot price is refused when
        it would rest (it has a limit or trigger: its fill could come once a rate exists).
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
            requirement = self._terms[order.instrument_id]
            needed = requirement.initial_usd(exposure, order, self._cache)
            if needed is None and _rests(order):
                return _unpriceable_reason(requirement, order, exposure)
            if needed is None:
                continue  # a market order fills now: without a rate the run fails (module)
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

    def modify_denial_reason(
        self, order: Order, new_quantity, new_price=None, new_trigger_price=None
    ) -> str | None:
        """Why modifying ``order`` to ``new_quantity`` must be rejected, or ``None``.

        A gated order's quantity may not be raised by a modify, whatever the margin: an
        increase would need margin held for a change the venue has not applied yet. Cancel
        and resubmit instead. Decreases and price-only modifies (``new_quantity`` is
        ``None``) pass; Nautilus releases margin only once a decrease is applied. A modify
        that sets a quantity is also refused while a modify of the order the gate passed on
        is unanswered; price-only modifies are not (the venue applies them to whatever the
        quantity is by then).

        Where the requirement moves with price (FX), a new limit or trigger that raises the
        order's initial margin by more than the account has free is refused too
        (``MARGIN_EXCEEDS_FREE_BALANCE``); futures margin ignores price, so their price-only
        modifies always pass.
        """
        requirement = self._terms.get(order.instrument_id)
        if requirement is None:
            return None
        if new_quantity is None:
            return self._reprice_denial(order, requirement, (new_price, new_trigger_price))
        if self._pending.outstanding(order):
            return _modify_pending_reason(requirement, order)
        current, new = order.quantity.as_decimal(), new_quantity.as_decimal()
        if new <= current:
            return self._reprice_denial(order, requirement, (new_price, new_trigger_price), new)
        return _qty_increase_reason(requirement, order, current, new)

    def _reprice_denial(self, order, requirement, new_prices: tuple, quantity=None) -> str | None:
        """Why a new limit/trigger raising an entry's margin past free must be refused."""
        extra = _margin_increase(self._cache, requirement, order, new_prices, quantity)
        if extra <= 0:
            return None
        # this order's own pending increase is replaced, not added to (one state applies)
        free = self._free_usd(order.instrument_id, exclude=order.client_order_id)
        return None if extra <= free else _reprice_reason(order, new_prices, extra, free)

    def _free_usd(self, instrument_id: InstrumentId, exclude=None) -> Decimal:
        """Venue free balance less unaccepted orders' margin and unapplied reprice increases."""
        unreserved = _unreserved_usd(
            self._cache, self._terms, self._pending, instrument_id, exclude
        )
        return _free_usd(self._cache, self._terms, instrument_id) - unreserved


def _unreserved_usd(
    cache, terms, pending: PendingModifies, instrument_id: InstrumentId, exclude=None
) -> Decimal:
    """The margin increases of passed, still unanswered modifies on the instrument's venue.

    Nautilus reserves an order's margin at its current price and quantity, so what a
    pending reprice adds is not reserved until the venue applies it. Each order counts its
    largest pending increase (only one requested state is live at a time); ``exclude`` (an
    order being repriced again) counts none, as its new request replaces the old.
    """
    venue: Venue = instrument_id.venue
    total = ZERO
    for client_order_id in pending.order_ids():
        order = cache.order(client_order_id)
        if order is None:
            continue
        requested = pending.outstanding(order)  # read first: settles and prunes the ledger
        excluded = exclude is not None and client_order_id == exclude
        if excluded or order.instrument_id.venue != venue:
            continue
        requirement = terms.get(order.instrument_id)
        increases = [
            _margin_increase(cache, requirement, order, (r.price, r.trigger_price), q)
            for r in requested
            for q in [r.quantity.as_decimal() if r.quantity is not None else None]
        ]
        total += max(increases, default=ZERO)
    return total


def _unpriceable_reason(requirement, order: Order, exposure: Decimal) -> str:
    return (
        f"{requirement.prefix}_{UNPRICEABLE}: no USD rate for {order.instrument_id} yet to "
        f"margin {exposure} {requirement.unit} resting on it; resubmit once its rate source "
        "has a bar"
    )


def _modify_pending_reason(requirement, order: Order) -> str:
    return (
        f"{requirement.prefix}_MODIFY_PENDING: an earlier modify of {order.client_order_id} "
        "is not applied or rejected yet; wait for it to resolve"
    )


def _qty_increase_reason(requirement, order: Order, current: Decimal, new: Decimal) -> str:
    return (
        f"{requirement.prefix}_QTY_INCREASE_NOT_SUPPORTED: modify would raise "
        f"{order.instrument_id} from {current} to {new} {requirement.unit}; "
        "cancel and resubmit instead"
    )


def _reprice_reason(order: Order, new_prices: tuple, extra: Decimal, free: Decimal) -> str:
    return (
        f"{DENIAL_REASON}: modify of {order.client_order_id} to "
        f"{_new_reference(order, *new_prices)} raises {order.instrument_id} initial margin "
        f"by {extra} USD, free={free} USD"
    )


def _margin_increase(
    cache, requirement, order: Order, new_prices: tuple, quantity: Decimal | None = None
) -> Decimal:
    """USD initial margin a modify to ``new_prices`` (and ``quantity``) adds to an entry.

    Zero where the gate margins no reprice: futures, reduce-only orders, contingent
    children (not margined as entries, ``denial_reason``), quantity-only modifies, and orders
    it cannot price. Nautilus 1.220 margins a working order on its full quantity (not
    ``leaves_qty``), so the increase is computed on that too.
    """
    reference = _new_reference(order, *new_prices)
    if requirement is None or not requirement.price_sensitive or reference is None:
        return ZERO
    if order.is_reduce_only or order.parent_order_id is not None:
        return ZERO
    held_qty = order.quantity.as_decimal()
    after = min(held_qty, quantity) if quantity is not None else held_qty
    before = requirement.initial_usd(held_qty, order, cache)
    new = requirement.initial_usd(after, order, cache, reference)
    if before is None or new is None:
        return ZERO
    return max(new - before, ZERO)


#: Order types that fill on arrival (a market-to-limit's first fill is at market); every
#: other type can rest, including a trailing stop submitted without a trigger yet.
_IMMEDIATE_TYPES = frozenset({OrderType.MARKET, OrderType.MARKET_TO_LIMIT})


def _rests(order: Order) -> bool:
    """Whether ``order`` can wait to fill (its type is not market or market-to-limit)."""
    return getattr(order, "order_type", None) not in _IMMEDIATE_TYPES


def _free_usd(cache, terms, instrument_id: InstrumentId) -> Decimal:
    """Venue free balance less the margin of its submitted-but-unaccepted orders."""
    venue: Venue = instrument_id.venue
    account = cache.account_for_venue(venue)
    instrument = cache.instrument(instrument_id)
    if account is None or instrument is None:
        return ZERO
    currency = getattr(account, "base_currency", None) or instrument.quote_currency
    free_money = account.balance_free(currency)
    free = free_money.as_decimal() if free_money is not None else ZERO
    for order in cache.orders_inflight(venue=venue):
        requirement = terms.get(order.instrument_id)
        # PENDING_UPDATE/PENDING_CANCEL are in flight too, but already reserved.
        if requirement is None or order.status != OrderStatus.SUBMITTED:
            continue
        if not order.is_reduce_only and order.parent_order_id is None:
            held = requirement.initial_usd(order.leaves_qty.as_decimal(), order, cache)
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
