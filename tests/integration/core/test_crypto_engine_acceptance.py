"""Crypto M1 gate (task 1.9): which account model carries unlevered, long-only spot crypto.

Before M2-M7 build on it, this proves in a real ``BacktestEngine`` that:

1. A ``KRAKEN`` venue with ``base_currency=USD`` accepts ``BTC/USD.KRAKEN`` with 4-decimal
   ``LAST`` bars, and a 0.128 BTC market order fills at exactly 0.12800000 with USD P&L.
2. Account model (decision C8), on the design section 1 scenario (buy 0.1 BTC with a resting
   stop-loss and take-profit ``SELL``; then a buy that exceeds the cash; then a naked ``SELL``):

   - **A**: ``MARGIN`` + ``base_currency=USD`` + ``default_leverage=1`` + ``margin_init=1``
     with a prototype gate (the shared :class:`MarginGate` with a spot requirement of
     ``qty x price`` and a no-short rule). The bracket runs, the over-cash buy and the short
     are denied, P&L and every balance are USD. Without the gate the same venue lets the buy
     drive the balance negative (the engine stops) and opens the short: the gate is required.
   - **B**: Nautilus ``CASH``. A single-currency (USD) cash venue refuses the instrument
     outright; a multi-currency one checks neither market orders (no quote or trade tick in a
     bar-only run) nor sells of a coin it does not hold, so it shorts, and it books the coin
     as a second balance.

3. Fill size against a 1-minute bar with volume 2 and 0 (the 2026 whole-coin volumes): the
   order always fills in full; volume only sets how much fills at the bar price, the rest one
   price increment worse. Bar volume must carry the instrument's size precision (8).

The gate here is a prototype; M3 ships the real requirement and rule in ``margin_gate.py``.
See ``openspec/changes/add-crypto-backtesting/design.md`` sections 1 and 3 (decision C8).

Run with ``--forked`` (C extensions).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.common.config import InvalidConfiguration
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import (
    AccountType,
    OmsType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PriceType,
)
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.trading.strategy import Strategy

from src.core.margin_gate import MarginGate, _closing_credit
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair

pytestmark = pytest.mark.integration

KRAKEN = Venue("KRAKEN")
START_USD = Decimal("10000")
MINUTE_NS = 60_000_000_000
T0 = pd.Timestamp("2025-03-06 15:00", tz="UTC").value
NO_SHORT = "CRYPTO_SPOT_NO_SHORT"

FLAT = ("78000.1234",) * 4
# bar 4 trades through the 80,000 take-profit; the 70,000 stop is never touched
BRACKET_BARS = [FLAT, FLAT, FLAT, ("79000", "80500", "78900", "80400"), ("80400",) * 4]


# --- prototype gate (model A) -------------------------------------------------------------


@dataclass(frozen=True)
class _PrototypeSpotRequirement:
    """Spot: 100% of notional (``qty x price``, USD quote), at the order's reference price."""

    prefix: str = "CRYPTO"
    unit: str = "coin(s)"
    price_sensitive: bool = True

    def initial_usd(self, quantity, order, cache, price=None) -> Decimal | None:  # noqa: D102
        for name in ("price", "trigger_price"):
            if price is None and getattr(order, name, None) is not None:
                price = getattr(order, name).as_decimal()
        if price is None:
            last = cache.price(order.instrument_id, PriceType.LAST)  # falls back to the bar
            price = last.as_decimal() if last is not None else None
        return None if price is None else quantity * price


class _PrototypeSpotGate(MarginGate):
    """The shared gate plus a no-short rule for parentless ``SELL`` orders.

    Under ``HEDGING`` a ``SELL`` without a position id opens a new short whatever else is
    held, so a sell passes only when it targets an open long it does not oversell (net of
    other working sells on it). Bracket legs (they have a parent) only ever close.
    """

    def denial_reason(self, orders, position_id):  # noqa: D102
        position = self._cache.position(position_id) if position_id is not None else None
        submitting = {o.client_order_id for o in orders}
        credit = _closing_credit(self._cache, position, submitting)
        for order in orders:
            if order.parent_order_id is not None or order.side != OrderSide.SELL:
                continue
            if position is None or not position.is_long or order.quantity.as_decimal() > credit:
                return f"{NO_SHORT}: SELL {order.quantity} {order.instrument_id} would go short"
            credit -= order.quantity.as_decimal()
        return super().denial_reason(orders, position_id)


# --- strategy ----------------------------------------------------------------------------


class _ScriptConfig(StrategyConfig, frozen=True):
    bar_type: str
    script: str
    qty: str = "0.1"


class _Script(Strategy):
    """Runs one named order script, one step per bar; records denial reasons."""

    def __init__(self, config: _ScriptConfig) -> None:
        super().__init__(config)
        self.bar = 0
        self.denied: list[str] = []

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_order_denied(self, event) -> None:
        self.denied.append(event.reason)

    def on_bar(self, bar: Bar) -> None:
        self.bar += 1
        instrument = self.cache.instrument(bar.bar_type.instrument_id)
        getattr(self, f"_{self.config.script}")(instrument)

    def _qty(self, instrument, qty: str):
        return instrument.make_qty(Decimal(qty))

    def _market(self, instrument, side: OrderSide, qty: str) -> None:
        self.submit_order(
            self.order_factory.market(instrument.id, side, self._qty(instrument, qty))
        )

    def _bracket(self, instrument) -> None:
        """Bar 1: bracket buy 0.1. Bar 2: over-cash buy, then a naked short."""
        if self.bar == 1:
            orders = self.order_factory.bracket(
                instrument.id,
                OrderSide.BUY,
                self._qty(instrument, "0.1"),
                sl_trigger_price=instrument.make_price(Decimal("70000")),
                tp_price=instrument.make_price(Decimal("80000")),
            )
            self.submit_order_list(orders)
        elif self.bar == 2:
            self._market(instrument, OrderSide.BUY, "2")  # USD 156,000 on a USD 10,000 account
            self._market(instrument, OrderSide.SELL, "0.1")  # no position id: a new short

    def _round_trip(self, instrument) -> None:
        """Bar 1: buy ``qty``; bar 3: close it."""
        if self.bar == 1:
            self._market(instrument, OrderSide.BUY, self.config.qty)
        elif self.bar == 3:
            self.close_all_positions(instrument.id)

    def _buy_once(self, instrument) -> None:
        if self.bar == 1:
            self._market(instrument, OrderSide.BUY, self.config.qty)

    def _short_once(self, instrument) -> None:
        if self.bar == 1:
            self._market(instrument, OrderSide.SELL, "0.1")

    def _over_cash(self, instrument) -> None:
        """Bar 1: a market and a limit buy, each beyond the cash; a naked limit sell."""
        if self.bar == 1:
            self._market(instrument, OrderSide.BUY, "2")
            limit = self.order_factory.limit(
                instrument.id,
                OrderSide.BUY,
                self._qty(instrument, "2"),
                instrument.make_price(Decimal("77000")),
            )
            self.submit_order(limit)
        elif self.bar == 2:
            self._market(instrument, OrderSide.SELL, "0.1")


# --- harness -----------------------------------------------------------------------------


def _btc():
    return build_crypto_pair(get_coin_spec("BTC"))


def _bars(instrument, ohlcs, volume: str = "100", bar_spec: str = "1-MINUTE") -> list[Bar]:
    bar_type = BarType.from_str(f"{instrument.id}-{bar_spec}-LAST-EXTERNAL")
    bars = []
    for i, prices in enumerate(ohlcs):
        o, h, low, c = (instrument.make_price(Decimal(p)) for p in prices)
        ts = T0 + i * MINUTE_NS
        bars.append(Bar(bar_type, o, h, low, c, instrument.make_qty(Decimal(volume)), ts, ts))
    return bars


MODELS = {
    "A": dict(account_type=AccountType.MARGIN, base_currency=USD, default_leverage=Decimal(1)),
    "B_single": dict(account_type=AccountType.CASH, base_currency=USD),
    "B_multi": dict(account_type=AccountType.CASH, base_currency=None),
}


def _engine(model: str, start_usd: Decimal = START_USD) -> BacktestEngine:
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=KRAKEN,
        oms_type=OmsType.HEDGING,
        starting_balances=[Money(start_usd, USD)],
        fill_model=FillModel(),
        **MODELS[model],
    )
    return engine


@dataclass
class _Run:
    engine: BacktestEngine
    strategy: _Script
    gate: MarginGate | None

    def orders(self) -> list[tuple[str, str, str, str]]:
        """(side, type, status, filled qty) per order, in submission order."""
        orders = sorted(self.engine.cache.orders(), key=lambda o: o.ts_init)
        return [
            (o.side_string(), o.type_string(), o.status_string(), str(o.filled_qty)) for o in orders
        ]

    def fills(self) -> list[tuple[str, str]]:
        """(qty, price) of every fill event, in order."""
        events = [e for o in self.engine.cache.orders() for e in o.events]
        return [
            (str(e.last_qty), str(e.last_px)) for e in events if type(e).__name__ == "OrderFilled"
        ]

    def balances(self) -> dict[str, Decimal]:
        account = self.engine.cache.account_for_venue(KRAKEN)
        return {str(c): m.as_decimal() for c, m in account.balances_total().items()}


def _run(
    model: str,
    script: str,
    ohlcs=BRACKET_BARS,
    *,
    gated: bool = False,
    qty: str = "0.1",
    volume: str = "100",
    start_usd: Decimal = START_USD,
) -> _Run:
    engine = _engine(model, start_usd)
    instrument = _btc()
    engine.add_instrument(instrument)
    bars = _bars(instrument, ohlcs, volume)
    engine.add_data(bars)
    strategy = _Script(_ScriptConfig(bar_type=str(bars[0].bar_type), script=script, qty=qty))
    engine.add_strategy(strategy)
    gate = None
    if gated:
        kernel = engine.kernel
        terms = {instrument.id: _PrototypeSpotRequirement()}
        gate = _PrototypeSpotGate(kernel.risk_engine, kernel.cache, kernel.msgbus, terms)
        gate.install()
    engine.run()
    return _Run(engine, strategy, gate)


@pytest.fixture
def runs():
    made: list[_Run] = []
    yield made
    for run in made:
        run.engine.dispose()


# --- 1. the venue accepts the pair; fractional fills; USD P&L -----------------------------


def test_usd_margin_venue_accepts_the_pair_and_4_decimal_last_bars(runs):
    run = _run("A", "buy_once", [FLAT, FLAT], qty="0.128")
    runs.append(run)
    instrument = _btc()
    assert [str(i.id) for i in run.engine.cache.instruments(venue=KRAKEN)] == ["BTC/USD.KRAKEN"]
    bar_type = BarType.from_str("BTC/USD.KRAKEN-1-MINUTE-LAST-EXTERNAL")
    assert run.engine.cache.bar_count(bar_type) == 2
    assert run.engine.cache.bar(bar_type).close.precision == instrument.price_precision == 4


def test_fractional_market_order_fills_exactly_and_settles_in_usd(runs):
    closes = [FLAT, FLAT, ("79000.5",) * 4, ("79000.5",) * 4]
    run = _run("A", "round_trip", closes, qty="0.128")
    runs.append(run)

    assert run.orders() == [
        ("BUY", "MARKET", "FILLED", "0.12800000"),
        ("SELL", "MARKET", "FILLED", "0.12800000"),
    ]
    (position,) = run.engine.cache.positions_closed()
    assert position.peak_qty == Quantity.from_str("0.12800000")
    # 0.128 x (79000.5000 - 78000.1234) = 128.048..., no fees on the instrument (M3 charges)
    assert position.realized_pnl == Money(Decimal("128.05"), USD)
    assert run.balances() == {"USD": START_USD + Decimal("128.05")}


def test_bar_volume_must_carry_the_size_precision():
    """M2 must store volume at 8 decimals: an integer-precision volume stops the run."""
    engine = _engine("A")
    try:
        instrument = _btc()
        engine.add_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
        px = instrument.make_price(Decimal("78000.1234"))
        engine.add_data([Bar(bar_type, px, px, px, px, Quantity.from_int(2), T0, T0)])
        with pytest.raises(RuntimeError, match="bar.volume.precision=0 did not match"):
            engine.run()
    finally:
        engine.dispose()


# --- 2. account model A: MARGIN, leverage 1, margin 1, prototype gate ----------------------


def test_model_a_gated_runs_the_bracket_and_denies_overspend_and_short(runs):
    run = _run("A", "bracket", gated=True)
    runs.append(run)

    # 2 x 78000.1234 against 10,000 less the 0.1 BTC (7,800.01) the open long locks
    assert run.strategy.denied == [
        "MARGIN_EXCEEDS_FREE_BALANCE: 2 BTC/USD.KRAKEN coin(s) need 156000.2468 USD "
        "initial margin, free=2199.99 USD",
        f"{NO_SHORT}: SELL 0.10000000 BTC/USD.KRAKEN would go short",
    ]
    assert run.gate.denied_count == 2
    assert run.orders() == [
        ("BUY", "MARKET", "FILLED", "0.10000000"),  # bracket entry
        ("SELL", "STOP_MARKET", "CANCELED", "0.00000000"),  # stop-loss, OCO-cancelled
        ("SELL", "LIMIT", "FILLED", "0.10000000"),  # take-profit at 80,000 (bar 4)
        ("BUY", "MARKET", "DENIED", "0.00000000"),
        ("SELL", "MARKET", "DENIED", "0.00000000"),
    ]
    (position,) = run.engine.cache.positions()
    assert position.is_closed
    # 0.1 x (80000.0000 - 78000.1234) = 199.98766 -> 199.99 USD
    assert position.realized_pnl == Money(Decimal("199.99"), USD)
    assert run.balances() == {"USD": START_USD + Decimal("199.99")}


def test_model_a_gated_denies_a_short_with_no_position(runs):
    run = _run("A", "over_cash", [FLAT, FLAT], gated=True, start_usd=Decimal("1000000"))
    runs.append(run)
    # USD 1M covers both 2-BTC buys (156,000 each); the naked sell is the only denial
    assert run.strategy.denied == [
        f"{NO_SHORT}: SELL 0.10000000 BTC/USD.KRAKEN would go short",
    ]
    assert all(p.side == PositionSide.LONG for p in run.engine.cache.positions())


def test_model_a_without_the_gate_overspends_and_shorts(runs):
    """Why the gate is mandatory: Nautilus skips every risk check for a margin account."""
    run = _run("A", "bracket")
    runs.append(run)
    assert run.strategy.denied == []
    assert run.orders()[3:] == [
        ("BUY", "MARKET", "FILLED", "2.00000000"),  # USD 156,000 on a USD 10,000 account
        ("SELL", "MARKET", "FILLED", "0.10000000"),  # a short
    ]
    shorts = [p for p in run.engine.cache.positions() if p.side == PositionSide.SHORT]
    assert [str(p.quantity) for p in shorts] == ["0.10000000"]
    account = run.engine.cache.account_for_venue(KRAKEN)
    # margin is clamped, not enforced: everything is locked and nothing ever refuses
    assert account.balance_free(USD) == Money(0, USD)


# --- 2. account model B: Nautilus CASH ----------------------------------------------------


def test_model_b_single_currency_cash_refuses_the_pair():
    engine = _engine("B_single")
    try:
        with pytest.raises(InvalidConfiguration, match="single-currency CASH account"):
            engine.add_instrument(_btc())
    finally:
        engine.dispose()


def test_model_b_multi_currency_cash_does_not_price_market_buys(runs, capfd):
    run = _run("B_multi", "over_cash", [FLAT, FLAT, FLAT])
    runs.append(run)

    # only the resting limit buy is priced (no quote or trade tick in a bar-only run)
    assert run.strategy.denied == [
        "NOTIONAL_EXCEEDS_FREE_BALANCE: free=10000.00 USD, balance_impact=-154000.00 USD"
    ]
    assert run.orders() == [
        ("BUY", "MARKET", "FILLED", "2.00000000"),  # USD 156,000 bought on USD 10,000
        ("BUY", "LIMIT", "DENIED", "0.00000000"),
    ]  # and the bar-2 sell never comes: the negative balance stops the whole backtest
    # the books are left inconsistent: the coin is credited, the USD debit never applied
    assert run.balances() == {"USD": START_USD, "BTC": Decimal("2")}
    captured = capfd.readouterr()
    assert "Stopping backtest from AccountBalanceNegative" in captured.out + captured.err


def test_model_b_multi_currency_cash_sells_a_coin_it_does_not_hold(runs):
    run = _run("B_multi", "short_once", [FLAT, FLAT])
    runs.append(run)
    assert run.strategy.denied == []
    assert run.orders() == [("SELL", "MARKET", "FILLED", "0.10000000")]
    (position,) = run.engine.cache.positions()
    assert position.side == PositionSide.SHORT


# --- 3. fill size against thin bars (decision C15) ---------------------------------------


@pytest.mark.parametrize(
    ("qty", "volume", "fills"),
    [
        ("0.128", "2", [("0.12800000", "78000.1234")]),
        ("0.128", "0", [("0.00000001", "78000.1234"), ("0.12799999", "78000.1235")]),
        ("5", "2", [("0.50000000", "78000.1234"), ("4.50000000", "78000.1235")]),
        ("5", "0", [("0.00000001", "78000.1234"), ("4.99999999", "78000.1235")]),
    ],
)
def test_bar_volume_never_caps_the_fill_size(runs, qty, volume, fills):
    run = _run("A", "buy_once", [FLAT, FLAT], qty=qty, volume=volume, start_usd=Decimal("1e6"))
    runs.append(run)
    assert run.orders() == [("BUY", "MARKET", "FILLED", str(_btc().make_qty(Decimal(qty))))]
    assert run.fills() == fills


def test_types_are_what_the_pins_name():
    """Guard the string pins above against enum renames."""
    assert OrderStatus.DENIED.name == "DENIED"
    assert OrderType.STOP_MARKET.name == "STOP_MARKET"
    assert BTC.code == "BTC"
