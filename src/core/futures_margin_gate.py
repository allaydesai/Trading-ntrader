"""Pre-trade futures margin check for backtests (futures M3).

Nautilus 1.220 computes margin on a margin account but never refuses an order over it: the
risk engine returns early for margin accounts (``risk/engine.pyx``: "TODO: Determine risk
controls for margin") and the account clamps free balance at zero instead of failing. Left
alone, a futures backtest has unlimited leverage.

Every order path (strategy ``submit_order``/``submit_order_list``, emulator release, exec
algorithms) reaches the risk engine through one message-bus endpoint, ``RiskEngine.execute``.
:class:`FuturesMarginGate` sits on that endpoint. It denies, through the risk engine's own
``_deny_order`` (so the strategy receives a normal ``OrderDenied``), any order that would
open futures exposure needing more initial margin than the account has free. Everything else
goes straight on to the risk engine unchanged. The gate is installed only when a run has a
futures contract, so equity runs never see it.

"Free" is Nautilus' free balance: realised cash minus the margin it has locked (initial
margin for working orders, maintenance margin for open positions; dollars per contract via
``FuturesMarginModel``). It is net of orders already submitted but not yet accepted.
Unrealised P&L is not counted (Nautilus does not add it to the balance), and there are no
margin calls: maintenance margin is locked, never used to liquidate.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.execution.messages import SubmitOrder, SubmitOrderList
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId, PositionId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order

from src.core.futures_costs import FuturesTerms, terms_by_instrument

RISK_ENDPOINT = "RiskEngine.execute"
DENIAL_REASON = "MARGIN_EXCEEDS_FREE_BALANCE"


class FuturesMarginGate:
    """Denies futures orders whose initial margin exceeds the account's free balance."""

    def __init__(self, engine: BacktestEngine, terms: Mapping[InstrumentId, FuturesTerms]):
        """Bind to ``engine``'s kernel; call :meth:`install` to take over the endpoint."""
        kernel = engine.kernel
        self._risk = kernel.risk_engine
        self._cache = kernel.cache
        self._msgbus = kernel.msgbus
        self._terms = dict(terms)
        self.denied_count = 0

    def install(self) -> None:
        """Route ``RiskEngine.execute`` through this gate."""
        self._msgbus.deregister(RISK_ENDPOINT, self._risk.execute)
        self._msgbus.register(RISK_ENDPOINT, self.execute)

    def execute(self, command) -> None:
        """Deny an over-margin submit; pass every other command to the risk engine."""
        if isinstance(command, SubmitOrder):
            reason = self.denial_reason([command.order], command.position_id)
            if reason is not None:
                self.denied_count += 1
                self._risk._deny_order(command.order, reason)
                return
        elif isinstance(command, SubmitOrderList):
            reason = self.denial_reason(command.order_list.orders, command.position_id)
            if reason is not None:
                self.denied_count += 1
                self._risk._deny_order_list(command.order_list, reason)
                return
        self._risk.execute(command)

    def denial_reason(self, orders: list[Order], position_id: PositionId | None) -> str | None:
        """Why ``orders`` must be denied, or ``None`` when the margin is there."""
        entries = [o for o in orders if o.parent_order_id is None]
        for instrument_id in {o.instrument_id for o in entries}:
            terms = self._terms.get(instrument_id)
            if terms is None:
                continue
            contracts = sum(
                (
                    self._new_exposure(o, position_id)
                    for o in entries
                    if o.instrument_id == instrument_id
                ),
                Decimal(0),
            )
            if contracts <= 0:
                continue
            required = contracts * terms.initial_margin_usd
            free = self._free_usd(instrument_id)
            if required > free:
                return (
                    f"{DENIAL_REASON}: {contracts} {instrument_id} contract(s) need "
                    f"{required} USD initial margin ({terms.initial_margin_usd} per contract), "
                    f"free={free} USD"
                )
        return None

    def _new_exposure(self, order: Order, position_id: PositionId | None) -> Decimal:
        """Contracts of new exposure: the order less what it closes of its target position."""
        if order.is_reduce_only:
            return Decimal(0)
        quantity = order.quantity.as_decimal()
        position = self._cache.position(position_id) if position_id is not None else None
        if position is None or position.is_closed:
            return quantity
        opposite = (order.side == OrderSide.SELL) == position.is_long
        if not opposite:
            return quantity
        return max(quantity - position.quantity.as_decimal(), Decimal(0))

    def _free_usd(self, instrument_id: InstrumentId) -> Decimal:
        """Free balance less the initial margin of orders submitted but not yet accepted."""
        account = self._cache.account_for_venue(instrument_id.venue)
        instrument = self._cache.instrument(instrument_id)
        if account is None or instrument is None:
            return Decimal(0)
        free_money = account.balance_free(instrument.quote_currency)
        free = free_money.as_decimal() if free_money is not None else Decimal(0)
        terms = self._terms[instrument_id]
        for order in self._cache.orders_inflight(instrument_id=instrument_id):
            if not order.is_reduce_only and order.parent_order_id is None:
                free -= order.leaves_qty.as_decimal() * terms.initial_margin_usd
        return free


def install_futures_margin_gate(
    engine: BacktestEngine, instruments: list[Instrument]
) -> FuturesMarginGate | None:
    """Install the gate when ``instruments`` include a futures contract; else do nothing."""
    terms = terms_by_instrument(instruments)
    if not terms:
        return None
    gate = FuturesMarginGate(engine, terms)
    gate.install()
    return gate
