"""Crypto M3 (task 3.3): the margin gate's spot requirement and the no-short rule.

Real ``CurrencyPair``s from the shipped coin specs and real quantities and prices; orders,
positions, the account and the risk engine are stand-ins so each case is exact. Engine-level
behaviour (a real bracket, a real over-cash buy and a naked sell):
``tests/integration/core/test_crypto_venue_engine.py``.

Spot is unlevered and long-only (decision C8): an entry needs its full USD notional free
(``qty x price``), and under ``HEDGING`` a parentless ``SELL`` passes only when it closes an open
long by position id without overselling it (net of the other working sells on that long).
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OrderSide, OrderStatus, OrderType
from nautilus_trader.model.identifiers import ClientOrderId, PositionId
from nautilus_trader.model.objects import Price

from src.core.margin_gate import (
    DENIAL_REASON,
    NO_SHORT_REASON,
    CryptoSpotRequirement,
    FuturesRequirement,
    FxRequirement,
    MarginGate,
    margin_requirements,
)
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair
from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import build_futures_contract
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

BTC = build_crypto_pair(get_coin_spec("BTC"))
ETH = build_crypto_pair(get_coin_spec("ETH"))
EURUSD = build_fx_pair(get_pair_spec("EURUSD"))
INSTRUMENTS = {i.id: i for i in (BTC, ETH, EURUSD)}
LAST = "78000.1234"
LONG = PositionId("BTC-LONG-1")
_IDS = iter(range(1, 10_000))


def _order(
    qty: str,
    side: OrderSide = OrderSide.BUY,
    price: str | None = None,
    *,
    instrument=BTC,
    order_type: OrderType | None = None,
    parent: ClientOrderId | None = None,
    reduce_only: bool = False,
    status: OrderStatus = OrderStatus.INITIALIZED,
    trigger: str | None = None,
):
    quantity = instrument.make_qty(Decimal(qty))
    kind = order_type or (OrderType.LIMIT if price else OrderType.MARKET)
    return SimpleNamespace(
        instrument_id=instrument.id,
        side=side,
        quantity=quantity,
        leaves_qty=quantity,
        price=Price.from_str(price) if price else None,
        trigger_price=Price.from_str(trigger) if trigger else None,
        order_type=kind,
        status=status,
        events=[],
        is_reduce_only=reduce_only,
        parent_order_id=parent,
        client_order_id=ClientOrderId(f"O-{next(_IDS)}"),
        is_open=status == OrderStatus.ACCEPTED,
        is_inflight=False,
    )


def _long(qty: str = "0.1", *, instrument=BTC, open_: bool = True):
    return SimpleNamespace(
        id=LONG,
        instrument_id=instrument.id,
        quantity=instrument.make_qty(Decimal(qty)),
        is_open=open_,
        is_long=True,
    )


class _Cache:
    """One venue account with ``free`` USD, the BTC/ETH pairs, a last price, one long."""

    def __init__(self, free: str, position=None, working=(), last: str | None = LAST) -> None:
        self.free = Decimal(free)
        self._position = position
        self._working = list(working)
        self._last = last

    def instrument(self, instrument_id):
        return INSTRUMENTS[instrument_id]

    def price(self, instrument_id, price_type):
        return Price.from_str(self._last) if self._last else None

    def account_for_venue(self, venue):
        return SimpleNamespace(
            base_currency=USD,
            balance_free=lambda currency: SimpleNamespace(as_decimal=lambda: self.free),
        )

    def orders_inflight(self, venue=None):
        return []

    def position(self, position_id):
        return self._position if position_id == LONG else None

    def orders_for_position(self, position_id):
        return self._working

    def order(self, client_order_id):
        return None


class _Risk:
    def __init__(self) -> None:
        self.forwarded: list = []
        self.denied: list[str] = []

    def execute(self, command):
        self.forwarded.append(command)

    def _deny_order(self, order, reason):
        self.denied.append(reason)


def _gate(free: str = "10000", **cache) -> MarginGate:
    terms = {i.id: CryptoSpotRequirement() for i in (BTC, ETH)}
    return MarginGate(_Risk(), _Cache(free, **cache), msgbus=None, terms=terms)


class TestRequirement:
    def test_the_requirement_is_the_full_notional_at_the_limit_price(self):
        order = _order("0.128", price="78000.0000")
        assert CryptoSpotRequirement().initial_usd(Decimal("0.128"), order, _Cache("0")) == (
            Decimal("9984.00")
        )

    def test_a_market_order_is_priced_at_the_last_price_and_a_stop_at_its_trigger(self):
        requirement = CryptoSpotRequirement()
        market = _order("2")
        assert requirement.initial_usd(Decimal(2), market, _Cache("0")) == Decimal("156000.25")
        stop = _order("1", order_type=OrderType.STOP_MARKET, trigger="80000.0000")
        assert requirement.initial_usd(Decimal(1), stop, _Cache("0")) == Decimal("80000.00")

    def test_a_given_price_overrides_and_no_price_is_unpriceable(self):
        requirement = CryptoSpotRequirement()
        order = _order("1")
        assert requirement.initial_usd(Decimal(1), order, _Cache("0"), Decimal("100")) == 100
        assert requirement.initial_usd(Decimal(1), order, _Cache("0", last=None)) is None

    def test_names_used_in_reasons(self):
        requirement = CryptoSpotRequirement()
        assert (requirement.prefix, requirement.unit, requirement.price_sensitive) == (
            "CRYPTO",
            "coin(s)",
            True,
        )


class TestCash:
    def test_a_buy_within_the_cash_passes(self):
        assert _gate("10000").denial_reason([_order("0.128")], None) is None  # 9,984.02

    def test_over_allocation_is_denied(self):
        reason = _gate("10000").denial_reason([_order("2")], None)
        assert reason == (
            f"{DENIAL_REASON}: 2 BTC/USD.KRAKEN coin(s) need 156000.25 USD initial margin, "
            "free=10000 USD"
        )

    def test_the_spec_scenario_0_2_btc_at_78000_on_10000_is_denied(self):
        reason = _gate("10000").denial_reason([_order("0.2", price="78000.0000")], None)
        assert reason == (
            f"{DENIAL_REASON}: 0.2 BTC/USD.KRAKEN coin(s) need 15600.00 USD initial margin, "
            "free=10000 USD"
        )

    def test_a_resting_limit_over_the_cash_is_denied_too(self):
        reason = _gate("10000").denial_reason([_order("2", price="77000.0000")], None)
        assert reason is not None and reason.startswith(DENIAL_REASON)


class TestNoShort:
    def test_a_sell_with_no_position_is_denied(self):
        reason = _gate().denial_reason([_order("0.1", OrderSide.SELL)], None)
        assert reason == (
            f"{NO_SHORT_REASON}: SELL 0.10000000 BTC/USD.KRAKEN would go short; a spot sell must "
            "close an open long by position id (free to sell: 0)"
        )

    def test_a_sell_closing_the_long_passes(self):
        gate = _gate(position=_long("0.1"))
        assert gate.denial_reason([_order("0.1", OrderSide.SELL)], LONG) is None
        assert gate.denial_reason([_order("0.05", OrderSide.SELL)], LONG) is None

    def test_a_sell_larger_than_the_long_is_denied(self):
        reason = _gate(position=_long("0.1")).denial_reason([_order("0.2", OrderSide.SELL)], LONG)
        assert reason is not None and reason.startswith(NO_SHORT_REASON)
        assert "(free to sell: 0.1)" in reason

    def test_other_working_sells_on_the_long_count(self):
        target = _order("0.1", OrderSide.SELL, "80000.0000", status=OrderStatus.ACCEPTED)
        gate = _gate(position=_long("0.1"), working=[target])
        reason = gate.denial_reason([_order("0.1", OrderSide.SELL)], LONG)
        assert reason is not None and reason.startswith(NO_SHORT_REASON)

    def test_sells_in_one_list_share_the_long(self):
        sells = [_order("0.1", OrderSide.SELL), _order("0.1", OrderSide.SELL)]
        reason = _gate(position=_long("0.15")).denial_reason(sells, LONG)
        assert reason is not None and reason.startswith(NO_SHORT_REASON)

    def test_a_closed_position_or_another_pairs_long_is_not_a_long(self):
        closed = _gate(position=_long("0.1", open_=False))
        assert closed.denial_reason([_order("0.1", OrderSide.SELL)], LONG) is not None
        eth = _gate(position=_long("1", instrument=ETH))
        assert eth.denial_reason([_order("0.1", OrderSide.SELL)], LONG) is not None

    def test_bracket_exits_are_allowed(self):
        entry = _order("0.1")
        stop = _order("0.1", OrderSide.SELL, order_type=OrderType.STOP_MARKET, trigger="70000")
        stop.parent_order_id = entry.client_order_id
        take = _order("0.1", OrderSide.SELL, "80000.0000", parent=entry.client_order_id)
        assert _gate().denial_reason([entry, stop, take], None) is None

    def test_a_short_bracket_is_denied(self):
        entry = _order("0.1", OrderSide.SELL)
        take = _order("0.1", OrderSide.BUY, "70000.0000", parent=entry.client_order_id)
        reason = _gate().denial_reason([entry, take], None)
        assert reason is not None and reason.startswith(NO_SHORT_REASON)

    def test_a_reduce_only_exit_of_the_long_is_allowed(self):
        sell = _order("0.1", OrderSide.SELL, reduce_only=True)
        assert _gate(position=_long("0.1")).denial_reason([sell], LONG) is None

    def test_a_reduce_only_exit_cannot_strand_a_resting_sell(self):
        # the exit would flatten the long and leave the 0.1 limit sell to fill as a short later
        resting = _order("0.1", OrderSide.SELL, "80000.0000", status=OrderStatus.ACCEPTED)
        gate = _gate(position=_long("0.1"), working=[resting])
        reason = gate.denial_reason([_order("0.1", OrderSide.SELL, reduce_only=True)], LONG)
        assert reason is not None and reason.startswith(NO_SHORT_REASON)

    def test_a_reduce_only_sell_with_no_position_is_denied(self):
        sell = _order("0.1", OrderSide.SELL, reduce_only=True)
        assert _gate().denial_reason([sell], None).startswith(NO_SHORT_REASON)

    def test_bracket_siblings_claim_the_long_once(self):
        # an OCO stop and take-profit both close the same 0.1: together they claim 0.1, not 0.2
        parent = ClientOrderId("O-ENTRY")
        stop = _order("0.1", OrderSide.SELL, trigger="70000.0000", status=OrderStatus.ACCEPTED)
        stop.order_type, stop.parent_order_id = OrderType.STOP_MARKET, parent
        take = _order(
            "0.1", OrderSide.SELL, "80000.0000", parent=parent, status=OrderStatus.ACCEPTED
        )
        # no free USD: a sell that closes needs no margin
        gate = _gate("0", position=_long("0.2"), working=[stop, take])
        assert gate.denial_reason([_order("0.1", OrderSide.SELL)], LONG) is None
        reason = gate.denial_reason([_order("0.11", OrderSide.SELL)], LONG)
        assert reason is not None and reason.startswith(NO_SHORT_REASON)

    def test_a_buy_is_never_a_short(self):
        assert _gate(position=_long("0.1")).denial_reason([_order("0.01")], LONG) is None

    def test_the_short_rule_is_crypto_only(self):
        terms = {EURUSD.id: FxRequirement(Decimal("0.03"))}
        gate = MarginGate(_Risk(), _Cache("10000"), msgbus=None, terms=terms)
        sell = _order("1000", OrderSide.SELL, "1.10000", instrument=EURUSD)
        assert NO_SHORT_REASON not in (gate.denial_reason([sell], None) or "")


class TestModify:
    def test_repricing_a_closing_sell_needs_no_margin(self):
        # raising a 0.1 BTC closing stop from 70,000 to 75,000 adds no exposure, even with
        # no free USD (the FX rule would price the order's full quantity: USD 500 more)
        stop = _order("0.1", OrderSide.SELL, trigger="70000.0000", status=OrderStatus.ACCEPTED)
        stop.order_type = OrderType.STOP_MARKET
        gate = _gate("0", position=_long("0.1"), working=[stop])
        assert gate.modify_denial_reason(stop, None, None, Price.from_str("75000.0000")) is None

    def test_repricing_an_entry_buy_above_the_cash_is_refused(self):
        buy = _order("0.1", price="70000.0000", status=OrderStatus.ACCEPTED)
        reason = _gate("0").modify_denial_reason(buy, None, Price.from_str("75000.0000"))
        assert reason is not None and reason.startswith(DENIAL_REASON)

    def test_a_quantity_increase_is_refused_with_the_crypto_prefix(self):
        order = _order("0.1", price="78000.0000", status=OrderStatus.ACCEPTED)
        reason = _gate().modify_denial_reason(order, BTC.make_qty(Decimal("0.2")))
        assert reason is not None and reason.startswith("CRYPTO_QTY_INCREASE_NOT_SUPPORTED")


class TestRequirements:
    def test_crypto_pairs_get_the_spot_requirement_and_others_are_unchanged(self):
        es = build_futures_contract(get_contract_spec("ES"))
        requirements = margin_requirements([BTC, es, EURUSD])
        assert isinstance(requirements[BTC.id], CryptoSpotRequirement)
        assert isinstance(requirements[es.id], FuturesRequirement)
        assert isinstance(requirements[EURUSD.id], FxRequirement)
