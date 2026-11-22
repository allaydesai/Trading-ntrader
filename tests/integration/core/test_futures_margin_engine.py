"""Futures M3: margin, commission and sizing through a real ``BacktestEngine``.

- An order needing more initial margin (dollars per contract, from the spec) than the
  account has free is DENIED; one within it fills; closing is never blocked.
- Realised P&L is points x multiplier, and commission is the spec's per-contract rate per
  side, so the balance moves by exactly ``points x 50 - commissions`` on ES.
- An equity venue gets no gate and its old cost model: the same over-sized order fills.
- The orchestrator runs ``sma_crossover`` on the trimmed real ES 1-hour fixture in whole
  contracts with per-contract fees.

Run with ``--forked`` (C extensions).
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderDenied
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

from src.core.backtest_venue import setup_backtest_venues
from src.core.fee_models import IBKRCommissionModel
from src.core.fill_model import make_fill_model
from src.core.futures_margin_gate import DENIAL_REASON
from src.models.backtest_request import BacktestRequest
from src.services.firstrate.parsers.firstrate_futures_parser import FirstRateFuturesParser
from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import build_futures_contract

pytestmark = pytest.mark.integration

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "firstrate_futures"
    / "ES_full_1hour_continuous_ratio_adjusted.txt"
)
ES = get_contract_spec("ES")


class _ScriptConfig(StrategyConfig, frozen=True):
    bar_type: str
    script: tuple  # per bar number: ("BUY"|"SELL", contracts) or ("CLOSE", 0)


class _Script(Strategy):
    """Submit the scripted market order on each bar; record denials."""

    def __init__(self, config: _ScriptConfig) -> None:
        super().__init__(config)
        self._bar = 0
        self.denials: list[str] = []

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_bar(self, bar: Bar) -> None:
        self._bar += 1
        for number, action, qty in self.config.script:
            if number != self._bar:
                continue
            instrument_id = bar.bar_type.instrument_id
            if action == "CLOSE":
                self.close_all_positions(instrument_id)
                continue
            side = OrderSide.BUY if action == "BUY" else OrderSide.SELL
            order = self.order_factory.market(instrument_id, side, Quantity.from_int(qty))
            self.submit_order(order)

    def on_order_denied(self, event: OrderDenied) -> None:
        self.denials.append(event.reason)


def _bars(instrument, closes: list[str]) -> list[Bar]:
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-LAST-EXTERNAL")
    start = pd.Timestamp("2025-03-06 15:00", tz="UTC").value
    bars = []
    for i, close in enumerate(closes):
        px = instrument.make_price(Decimal(close))
        ts = start + i * 3_600_000_000_000
        bars.append(Bar(bar_type, px, px, px, px, Quantity.from_int(1000), ts, ts))
    return bars


def _run(instrument, closes, script, balance: float):
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    gate = setup_backtest_venues(
        engine,
        {instrument.id.venue: [instrument]},
        starting_balance=balance,
        fill_model=make_fill_model(42),
        equity_fee_model=IBKRCommissionModel(),
    )
    engine.add_instrument(instrument)
    bars = _bars(instrument, closes)
    engine.add_data(bars)
    strategy = _Script(_ScriptConfig(bar_type=str(bars[0].bar_type), script=tuple(script)))
    engine.add_strategy(strategy)
    engine.run()
    return engine, strategy, gate


def test_order_over_free_margin_is_denied_and_within_it_fills():
    es = build_futures_contract(ES)
    # $50,000: 2 ES contracts need $55,000 initial margin; 1 needs $27,500.
    script = [
        (1, "BUY", 2),  # denied: 55,000 > 50,000
        (2, "BUY", 1),  # fills
        (3, "BUY", 1),  # denied: free = 50,000 - 25,000 maint - fees < 27,500
        (4, "CLOSE", 0),  # closing never needs margin
    ]
    closes = ["6000.00", "6000.00", "6005.00", "6010.25", "6010.25"]
    engine, strategy, gate = _run(es, closes, script, balance=50_000)
    try:
        assert gate is not None and gate.denied_count == 2
        assert len(strategy.denials) == 2
        assert all(reason.startswith(DENIAL_REASON) for reason in strategy.denials)
        assert "55000" in strategy.denials[0]

        fills = engine.trader.generate_order_fills_report()
        assert len(fills) == 2  # the 1-lot entry and its close

        position = engine.cache.positions()[0]
        points = Decimal(str(position.avg_px_close)) - Decimal(str(position.avg_px_open))
        commissions = 2 * ES.commission_per_contract_usd
        realised = position.realized_pnl.as_decimal()  # Nautilus nets commissions in
        assert realised == points * ES.multiplier - commissions

        account = engine.cache.account_for_venue(es.id.venue)
        final = account.balance_total(USD).as_decimal()
        assert final == Decimal("50000") + points * ES.multiplier - commissions
    finally:
        engine.dispose()


def test_short_entries_are_margined_too():
    cl = build_futures_contract(get_contract_spec("CL"))
    margin = get_contract_spec("CL").margin.initial_usd
    contracts = 3  # 3 x 6,600 = 19,800 > 15,000
    script = [(1, "SELL", contracts), (2, "SELL", 2), (3, "CLOSE", 0)]
    engine, strategy, _ = _run(cl, ["70.000"] * 4, script, balance=15_000)
    try:
        assert len(strategy.denials) == 1
        assert str(contracts * margin) in strategy.denials[0]
        position = engine.cache.positions()[0]
        assert position.peak_qty.as_decimal() == 2
    finally:
        engine.dispose()


def test_equity_venue_has_no_gate_and_keeps_the_per_share_fee():
    aapl = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
    # 5,000 shares at $150 = $750K notional on a $50K account: unchanged behaviour fills it.
    script = [(1, "BUY", 5000), (3, "CLOSE", 0)]
    engine, strategy, gate = _run(aapl, ["150.00", "150.00", "151.00", "151.00"], script, 50_000)
    try:
        assert gate is None
        assert strategy.denials == []
        fills = engine.trader.generate_order_fills_report()
        assert len(fills) == 2
        # IBKR per-share: 5,000 x $0.005 = $25 per side.
        commissions = sorted(str(c) for c in engine.cache.positions()[0].commissions())
        assert commissions == ["50.00 USD"]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_orchestrator_runs_sma_on_real_es_bars_in_whole_contracts():
    from src.core.backtest_orchestrator import BacktestOrchestrator

    es = build_futures_contract(ES)
    bar_type = BarType.from_str("ES.CME-1-HOUR-LAST-EXTERNAL")
    bars = FirstRateFuturesParser().parse_file(FIXTURE, es.id, bar_type)
    request = BacktestRequest(
        strategy_type="sma_crossover",
        strategy_path="src.core.strategies.sma_crossover:SMACrossover",
        config_path="src.core.strategies.sma_crossover:SMAConfig",
        strategy_config={
            "fast_period": 3,
            "slow_period": 8,
            "portfolio_value": Decimal("1000000"),
            "position_size_pct": Decimal("100"),
        },
        symbol="ES",
        instrument_id="ES.CME",
        start_date=datetime(2025, 3, 6, tzinfo=timezone.utc),
        end_date=datetime(2025, 3, 12, tzinfo=timezone.utc),
        bar_type="1-HOUR-LAST",
        persist=False,
        starting_balance=Decimal("1000000"),
        catalog_name="firstrate-futures",
    )
    orchestrator = BacktestOrchestrator()
    try:
        result, _ = await orchestrator.execute(request, bars, es)
        engine = orchestrator.engine
        assert engine is not None
        positions = engine.cache.positions()
        assert positions, "sma_crossover should trade the fixture week"
        rate = ES.commission_per_contract_usd
        for position in positions:
            qty = position.peak_qty.as_decimal()
            # ES ~6,000 x 50 = ~$300K a contract: $1M floors to 3 (not ~166 'shares')
            assert qty == 3
            fees = sum(c.as_decimal() for c in position.commissions())
            sides = 2 if position.is_closed else 1
            assert fees == qty * rate * sides
            if position.is_closed:
                points = Decimal(str(position.avg_px_close)) - Decimal(str(position.avg_px_open))
                sign = 1 if position.entry == OrderSide.BUY else -1
                gross = (sign * points * ES.multiplier * qty).quantize(Decimal("0.01"))
                assert position.realized_pnl.as_decimal() == gross - fees
        assert result.total_trades >= 1
    finally:
        orchestrator.dispose()


class _ModifyConfig(StrategyConfig, frozen=True):
    bar_type: str
    limit_price: str
    raise_to: int


class _RaiseWorkingOrder(Strategy):
    """Rest a 1-lot buy limit below the market on bar 1; try to raise it on bar 2."""

    def __init__(self, config: _ModifyConfig) -> None:
        super().__init__(config)
        self._bar = 0
        self.order = None
        self.modify_rejections: list[str] = []

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_bar(self, bar: Bar) -> None:
        self._bar += 1
        instrument = self.cache.instrument(bar.bar_type.instrument_id)
        if self._bar == 1:
            self.order = self.order_factory.limit(
                instrument.id,
                OrderSide.BUY,
                Quantity.from_int(1),
                instrument.make_price(Decimal(self.config.limit_price)),
            )
            self.submit_order(self.order)
        elif self._bar == 2:
            self.modify_order(self.order, quantity=Quantity.from_int(self.config.raise_to))

    def on_order_modify_rejected(self, event) -> None:
        self.modify_rejections.append(event.reason)


@pytest.mark.parametrize("raise_to,rejected", [(3, True), (1, False)])
def test_raising_a_working_order_past_free_margin_is_rejected(raise_to, rejected):
    es = build_futures_contract(ES)
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    try:
        setup_backtest_venues(
            engine,
            {es.id.venue: [es]},
            starting_balance=50_000,
            fill_model=make_fill_model(42),
            equity_fee_model=IBKRCommissionModel(),
        )
        engine.add_instrument(es)
        bars = _bars(es, ["6000.00", "6000.00", "6000.00"])
        engine.add_data(bars)
        config = _ModifyConfig(
            bar_type=str(bars[0].bar_type), limit_price="5000.00", raise_to=raise_to
        )
        strategy = _RaiseWorkingOrder(config)
        engine.add_strategy(strategy)
        engine.run()

        assert bool(strategy.modify_rejections) is rejected
        if rejected:
            # 1 working lot reserves 27,500 of 50,000; 2 more need 55,000.
            assert strategy.modify_rejections[0].startswith(DENIAL_REASON)
            assert engine.cache.order(strategy.order.client_order_id).quantity == 1
    finally:
        engine.dispose()


class _TripleCloseConfig(StrategyConfig, frozen=True):
    bar_type: str


class _TripleClose(Strategy):
    """Buy 1 on bar 1; on bar 2 send three separate plain 1-lot sells at that position."""

    def __init__(self, config: _TripleCloseConfig) -> None:
        super().__init__(config)
        self._bar = 0
        self.denials: list[str] = []

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_bar(self, bar: Bar) -> None:
        self._bar += 1
        instrument_id = bar.bar_type.instrument_id
        if self._bar == 1:
            buy = self.order_factory.market(instrument_id, OrderSide.BUY, Quantity.from_int(1))
            self.submit_order(buy)
        elif self._bar == 2:
            position = self.cache.positions_open(instrument_id=instrument_id)[0]
            for _ in range(3):
                sell = self.order_factory.market(
                    instrument_id, OrderSide.SELL, Quantity.from_int(1)
                )
                self.submit_order(sell, position_id=position.id)

    def on_order_denied(self, event: OrderDenied) -> None:
        self.denials.append(event.reason)


def test_separate_closing_submits_cannot_reopen_short_unmargined():
    """Only one of three 1-lot sells closes the 1-lot long; the others would open a short."""
    es = build_futures_contract(ES)
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    try:
        setup_backtest_venues(
            engine,
            {es.id.venue: [es]},
            starting_balance=50_000,
            fill_model=make_fill_model(42),
            equity_fee_model=IBKRCommissionModel(),
        )
        engine.add_instrument(es)
        bars = _bars(es, ["6000.00"] * 4)
        engine.add_data(bars)
        strategy = _TripleClose(_TripleCloseConfig(bar_type=str(bars[0].bar_type)))
        engine.add_strategy(strategy)
        engine.run()

        assert len(strategy.denials) == 2
        assert all(reason.startswith(DENIAL_REASON) for reason in strategy.denials)
        assert engine.cache.positions_open() == []  # closed, never flipped short
    finally:
        engine.dispose()
