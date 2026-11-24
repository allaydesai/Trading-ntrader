"""Crypto M3 (task 3.5): the production venue wiring runs spot crypto on account model A.

``setup_backtest_venues`` (``src/core/backtest_venue.py``) gives a crypto venue the account the
M1 spike chose (decision C8: ``MARGIN``, ``base_currency=USD``, leverage 1, ``margin_init`` 1),
the Kraken Pro fee model at the run's tier and the spot gate. In a real ``BacktestEngine``:

- a 0.128 BTC round trip fills fractionally and settles commission and P&L in USD equal to a
  hand calculation at tier 1 (taker and maker) and at tier 4;
- a resting limit fills as maker, market and stop orders as taker;
- the over-cash buy and the naked sell of the spike's bracket scenario are denied with the
  pinned reasons, and the bracket's take-profit still closes the long;
- the equity venue's ``add_venue`` call is unchanged, and a venue mixing crypto with FX or
  equities is refused.

Hand figures use the default 2 bp BTC spread (half per side, ``qty x price x 1 bp``) on top of
commission. Run with ``--forked`` (C extensions).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, LiquiditySide, OmsType, OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

from src.core.backtest_venue import add_backtest_venue, cost_snapshot, setup_backtest_venues
from src.core.crypto_costs import CryptoCommissionModel
from src.core.fee_models import IBKRCommissionModel
from src.core.margin_gate import MarginGate
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.integration

KRAKEN = Venue("KRAKEN")
START_USD = Decimal("10000")
MINUTE_NS = 60_000_000_000
T0 = pd.Timestamp("2025-03-06 15:00", tz="UTC").value


def _flat(px: str) -> tuple[str, str, str, str]:
    return (px,) * 4


# --- strategy ----------------------------------------------------------------------------


class _ScriptConfig(StrategyConfig, frozen=True):
    bar_type: str
    script: str


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

    def _limit(self, instrument, side, qty: str, px: str, position_id=None) -> None:
        price = instrument.make_price(Decimal(px))
        order = self.order_factory.limit(instrument.id, side, self._qty(instrument, qty), price)
        self.submit_order(order, position_id=position_id)

    def _market(self, instrument, side, qty: str) -> None:
        self.submit_order(
            self.order_factory.market(instrument.id, side, self._qty(instrument, qty))
        )

    def _taker_round_trip(self, instrument) -> None:
        """Bar 1: market buy 0.128 (fills at 78,000); bar 3: close it (fills at 79,000)."""
        if self.bar == 1:
            self._market(instrument, OrderSide.BUY, "0.128")
        elif self.bar == 3:
            self.close_all_positions(instrument.id)

    def _maker_round_trip(self, instrument) -> None:
        """Resting limits both ways: buy 0.128 at 77,000 (bar 2), sell at 79,000 (bar 4)."""
        if self.bar == 1:
            self._limit(instrument, OrderSide.BUY, "0.128", "77000")
        elif self.bar == 3:
            (position,) = self.cache.positions_open(instrument_id=instrument.id)
            self._limit(instrument, OrderSide.SELL, "0.128", "79000", position.id)

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


TAKER_BARS = [_flat("78000"), _flat("78000"), _flat("79000"), _flat("79000")]
MAKER_BARS = [
    _flat("78000"),
    ("78000", "78000", "76900", "77500"),  # trades through the 77,000 buy
    _flat("77500"),
    ("78000", "79500", "77900", "79400"),  # trades through the 79,000 sell
    _flat("79400"),
]
BRACKET_BARS = [
    _flat("78000"),
    _flat("78000"),
    ("79000", "80500", "78900", "80400"),  # trades through the 80,000 take-profit
    _flat("80400"),
]


# --- harness -----------------------------------------------------------------------------


def _bars(instrument, ohlcs) -> list[Bar]:
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = []
    for i, prices in enumerate(ohlcs):
        o, h, low, c = (instrument.make_price(Decimal(p)) for p in prices)
        ts = T0 + i * MINUTE_NS
        bars.append(Bar(bar_type, o, h, low, c, instrument.make_qty(Decimal(100)), ts, ts))
    return bars


@dataclass
class _Run:
    engine: BacktestEngine
    strategy: _Script
    gate: MarginGate | None

    def fills(self) -> list[tuple[str, str, str, LiquiditySide, Money]]:
        """(side, qty, price, liquidity side, commission) of every fill, in order."""
        events = [e for o in self.engine.cache.orders() for e in o.events]
        fills = sorted(
            (e for e in events if type(e).__name__ == "OrderFilled"), key=lambda e: e.ts_event
        )
        return [
            (e.order_side.name, str(e.last_qty), str(e.last_px), e.liquidity_side, e.commission)
            for e in fills
        ]

    def balance(self) -> Decimal:
        account = self.engine.cache.account_for_venue(KRAKEN)
        return account.balance_total(USD).as_decimal()


def _run(script: str, ohlcs, *, tier: int | None = None) -> _Run:
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    instrument = build_crypto_pair(get_coin_spec("BTC"), fee_tier=tier)
    bars = _bars(instrument, ohlcs)
    gate = setup_backtest_venues(
        engine,
        {KRAKEN: [instrument]},
        starting_balance=START_USD,
        fill_model=FillModel(),
        equity_fee_model=IBKRCommissionModel(),
        data=[(bars, instrument)],
    )
    strategy = _Script(_ScriptConfig(bar_type=str(bars[0].bar_type), script=script))
    engine.add_strategy(strategy)
    engine.run()
    return _Run(engine, strategy, gate)


@pytest.fixture
def runs():
    made: list[_Run] = []
    yield made
    for run in made:
        run.engine.dispose()


def _usd(amount: str) -> Money:
    return Money(Decimal(amount), USD)


# --- account model A ---------------------------------------------------------------------


def test_a_crypto_venue_gets_the_unlevered_usd_margin_account_and_the_gate(runs):
    run = _run("taker_round_trip", TAKER_BARS)
    runs.append(run)
    account = run.engine.cache.account_for_venue(KRAKEN)
    assert account.type == AccountType.MARGIN
    assert account.base_currency == USD
    assert account.leverage(run.engine.cache.instruments()[0].id) == Decimal(1)
    assert isinstance(run.gate, MarginGate)


# --- commission and P&L against hand calculations -----------------------------------------


@pytest.mark.parametrize(
    ("tier", "buy_fee", "sell_fee", "balance"),
    [
        # 9,984 x 0.80% = 79.872 + 1.00 spread; 10,112 x 0.80% = 80.896 + 1.01 spread
        (1, "80.87", "81.91", "9965.22"),
        # tier 2 (default): 9,984 x 0.60% = 59.904; 10,112 x 0.60% = 60.672
        (None, "60.90", "61.68", "10005.42"),
        # tier 4: 9,984 x 0.35% = 34.944; 10,112 x 0.35% = 35.392
        (4, "35.94", "36.40", "10055.66"),
    ],
)
def test_a_taker_round_trip_matches_the_hand_calculation(runs, tier, buy_fee, sell_fee, balance):
    run = _run("taker_round_trip", TAKER_BARS, tier=tier)
    runs.append(run)
    taker = LiquiditySide.TAKER
    assert run.fills() == [
        ("BUY", "0.12800000", "78000.0000", taker, _usd(buy_fee)),
        ("SELL", "0.12800000", "79000.0000", taker, _usd(sell_fee)),
    ]
    # P&L 0.128 x 1,000 = 128.00, less both fees
    assert run.balance() == Decimal(balance)
    assert run.balance() == START_USD + 128 - Decimal(buy_fee) - Decimal(sell_fee)


def test_a_maker_round_trip_matches_the_hand_calculation_at_tier_1(runs):
    run = _run("maker_round_trip", MAKER_BARS, tier=1)
    runs.append(run)
    maker = LiquiditySide.MAKER
    # 9,856 x 0.40% = 39.424 + 0.9856 spread; 10,112 x 0.40% = 40.448 + 1.0112 spread
    assert run.fills() == [
        ("BUY", "0.12800000", "77000.0000", maker, _usd("40.41")),
        ("SELL", "0.12800000", "79000.0000", maker, _usd("41.46")),
    ]
    # P&L 0.128 x 2,000 = 256.00
    assert run.balance() == START_USD + 256 - Decimal("40.41") - Decimal("41.46")
    costs = cost_snapshot(run.engine.cache)["crypto_pairs"]["BTC/USD.KRAKEN"]["costs_usd"]
    assert costs == {
        "commission": "79.87",
        "commission_maker": "79.87",
        "commission_taker": "0.00",
        "spread": "2.00",
    }


# --- the spot gate in the real engine ------------------------------------------------------


def test_the_bracket_runs_and_the_overspend_and_short_are_denied(runs):
    run = _run("bracket", BRACKET_BARS)
    runs.append(run)
    # free: 10,000 - 7,800.00 locked by the open 0.1 BTC - its 47.58 entry fee
    assert run.strategy.denied == [
        "MARGIN_EXCEEDS_FREE_BALANCE: 2 BTC/USD.KRAKEN coin(s) need 156000.00 USD initial "
        "margin, free=2152.42 USD",
        "CRYPTO_SPOT_NO_SHORT: SELL 0.10000000 BTC/USD.KRAKEN would go short; a spot sell "
        "must close an open long by position id (free to sell: 0)",
    ]
    assert run.gate.denied_count == 2
    # entry taker: 7,800 x 0.60% = 46.80 + 0.78; take-profit maker: 8,000 x 0.30% = 24.00 + 0.80
    assert run.fills() == [
        ("BUY", "0.10000000", "78000.0000", LiquiditySide.TAKER, _usd("47.58")),
        ("SELL", "0.10000000", "80000.0000", LiquiditySide.MAKER, _usd("24.80")),
    ]
    (position,) = run.engine.cache.positions()
    assert position.is_closed
    assert run.balance() == START_USD + 200 - Decimal("47.58") - Decimal("24.80")


# --- venue wiring --------------------------------------------------------------------------


def _record_venue(instruments) -> dict:
    calls: list[dict] = []
    engine = SimpleNamespace(cache=None, add_venue=lambda **kw: calls.append(kw))
    add_backtest_venue(
        engine,
        KRAKEN,
        instruments,
        starting_balance=START_USD,
        fill_model=FillModel(),
        equity_fee_model=IBKRCommissionModel(),
    )
    (call,) = calls
    return call


def test_the_equity_venue_call_is_unchanged():
    call = _record_venue([TestInstrumentProvider.equity("AAPL", "XNAS")])
    assert set(call) == {
        "venue",
        "oms_type",
        "account_type",
        "starting_balances",
        "fill_model",
        "fee_model",
    }
    assert isinstance(call["fee_model"], IBKRCommissionModel)


def test_the_crypto_venue_call():
    call = _record_venue([build_crypto_pair(get_coin_spec("BTC"))])
    assert call["oms_type"] == OmsType.HEDGING and call["account_type"] == AccountType.MARGIN
    assert call["base_currency"] == USD and call["default_leverage"] == Decimal(1)
    assert isinstance(call["fee_model"], CryptoCommissionModel)
    assert call["starting_balances"] == [Money(START_USD, USD)]


@pytest.mark.parametrize(
    "other",
    [build_fx_pair(get_pair_spec("EURUSD")), TestInstrumentProvider.equity("AAPL", "XNAS")],
    ids=["fx", "equity"],
)
def test_a_venue_mixing_crypto_with_other_instruments_is_refused(other):
    with pytest.raises(ValueError, match="pairs together with other instruments"):
        _record_venue([build_crypto_pair(get_coin_spec("BTC")), other])
