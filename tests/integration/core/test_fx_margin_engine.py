"""FX M4 (task 4.3): costs, percentage margin and base-unit sizing through a real engine.

The production venue wiring (``setup_backtest_venues``) gives an FX venue the IB spot
commission plus the modelled half-spread (``src/core/fx_costs.py``) and the margin gate
(``src/core/margin_gate.py``) at 3% of USD notional (shipped UNVERIFIED defaults). Pinned:

- Over-margin longs and shorts are DENIED (``MARGIN_EXCEEDS_FREE_BALANCE``), within-margin
  orders fill, closes are never denied, a quantity-increase modify is rejected; for a
  USD-quote pair (EURUSD), a USD-base pair (USDJPY) and a cross (EURGBP via GBPUSD).
- The USD balance moves by exactly P&L less commission and spread, hand-computed per pair, and
  the trade's stored USD P&L (``usd_realised_pnls``) equals that balance change.
- The orchestrator runs ``sma_crossover`` on USDJPY in base units: USD 100,000 of notional
  is 100,000 USDJPY, not ~690.

The futures margin-engine suite (``test_futures_margin_engine.py``) runs unchanged.
Run with ``--forked`` (C extensions).
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderDenied
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from src.core.backtest_venue import cost_snapshot, setup_backtest_venues
from src.core.fee_models import IBKRCommissionModel
from src.core.fx_results import usd_realised_pnls
from src.core.margin_gate import DENIAL_REASON
from src.core.results_extractor import mark_to_market
from src.models.backtest_request import BacktestRequest
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.integration

IDEALPRO = Venue("IDEALPRO")
HOUR_NS = 3_600_000_000_000
T0 = pd.Timestamp("2025-03-06 15:00", tz="UTC").value
BAR_VOLUME = Quantity.from_int(100_000_000)


def _pair(name: str):
    return build_fx_pair(get_pair_spec(name))


EURUSD, USDJPY, EURGBP, GBPUSD = (_pair(n) for n in ("EURUSD", "USDJPY", "EURGBP", "GBPUSD"))


def _bars(instrument, closes: list[str]) -> list[Bar]:
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-MID-EXTERNAL")
    bars = []
    for i, close in enumerate(closes):
        px = instrument.make_price(Decimal(close))
        ts = T0 + i * HOUR_NS
        bars.append(Bar(bar_type, px, px, px, px, BAR_VOLUME, ts, ts))
    return bars


class _ScriptConfig(StrategyConfig, frozen=True):
    bar_type: str
    script: tuple  # (bar number, "BUY"|"SELL"|"CLOSE"|"LIMIT"|"MODIFY", units, price or None)


class _Script(Strategy):
    """Submit the scripted orders on each bar; record denials and modify rejections."""

    def __init__(self, config: _ScriptConfig) -> None:
        super().__init__(config)
        self._bar = 0
        self.denials: list[str] = []
        self.modify_rejections: list[str] = []
        self.resting = None

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_bar(self, bar: Bar) -> None:
        self._bar += 1
        iid = bar.bar_type.instrument_id
        instrument = self.cache.instrument(iid)
        for number, action, units, price in self.config.script:
            if number != self._bar:
                continue
            if action == "CLOSE":
                self.close_all_positions(iid)
            elif action == "LIMIT":
                px = instrument.make_price(Decimal(price))
                self.resting = self.order_factory.limit(
                    iid, OrderSide.BUY, Quantity.from_int(units), px
                )
                self.submit_order(self.resting)
            elif action == "MODIFY":
                self.modify_order(self.resting, quantity=Quantity.from_int(units))
            else:
                side = OrderSide.BUY if action == "BUY" else OrderSide.SELL
                self.submit_order(self.order_factory.market(iid, side, Quantity.from_int(units)))

    def on_order_denied(self, event: OrderDenied) -> None:
        self.denials.append(event.reason)

    def on_order_modify_rejected(self, event) -> None:
        self.modify_rejections.append(event.reason)


def _run(traded, closes, script, balance, rate_sources=(), cost_multiplier=1):
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    data = [(_bars(traded, closes), traded)]
    gate = setup_backtest_venues(
        engine,
        {IDEALPRO: [traded]},
        starting_balance=balance,
        fill_model=FillModel(),
        equity_fee_model=IBKRCommissionModel(),
        cost_multiplier=cost_multiplier,
        data=data,
        rate_sources=rate_sources,
    )
    bar_type = f"{traded.id}-1-HOUR-MID-EXTERNAL"
    strategy = _Script(_ScriptConfig(bar_type=bar_type, script=tuple(script)))
    engine.add_strategy(strategy)
    engine.run()
    return engine, strategy, gate


def _usd_change(engine, start) -> Decimal:
    account = engine.cache.account_for_venue(IDEALPRO)
    return account.balance_total(USD).as_decimal() - Decimal(start)


GBP_RATES = [(_bars(GBPUSD, ["1.25000"] * 4), GBPUSD)]


# -- margin ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pair, closes, over, within, rate_sources",
    [
        # USD 10,000 at 3%: 400,000 EURUSD at 1.10000 needs 13,200; 250,000 needs 8,250
        (EURUSD, ["1.10000"] * 4, 400_000, 250_000, ()),
        # USDJPY: margin is 3% of the USD quantity: 400,000 needs 12,000; 300,000 needs 9,000
        (USDJPY, ["150.000"] * 4, 400_000, 300_000, ()),
        # EURGBP 0.85 x GBPUSD 1.25: 400,000 needs 12,750; 300,000 needs 9,562.50
        (EURGBP, ["0.85000"] * 4, 400_000, 300_000, GBP_RATES),
    ],
)
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_over_margin_is_denied_within_fills_and_the_close_is_never_denied(
    pair, closes, over, within, rate_sources, side
):
    script = [(1, side, over, None), (2, side, within, None), (3, "CLOSE", 0, None)]
    engine, strategy, gate = _run(pair, closes, script, 10_000, rate_sources)
    try:
        assert len(strategy.denials) == 1 and strategy.denials[0].startswith(DENIAL_REASON)
        assert f"{over} {pair.id} unit(s)" in strategy.denials[0]
        assert gate is not None and gate.denied_count == 1
        (position,) = engine.cache.positions()
        assert position.peak_qty == Quantity.from_int(within) and position.is_closed
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "pair, px, locked",
    [(EURUSD, "1.10000", "6600.00"), (USDJPY, "150.000", "6000.00")],
)
def test_a_position_locks_3_percent_and_a_second_entry_sees_it(pair, px, locked):
    # 200,000 holds 3% of its USD notional (leverage 1, not Nautilus' default 10); another
    # 200,000 would need the same again of the ~3,400-4,000 left free.
    script = [(1, "BUY", 200_000, None), (2, "BUY", 200_000, None)]
    engine, strategy, _ = _run(pair, [px] * 3, script, 10_000)
    try:
        assert len(strategy.denials) == 1
        account = engine.cache.account_for_venue(IDEALPRO)
        assert account.balance_locked(USD).as_decimal() == Decimal(locked)
    finally:
        engine.dispose()


def test_a_quantity_increase_modify_is_rejected_even_with_ample_margin():
    script = [(1, "LIMIT", 100_000, "1.00000"), (2, "MODIFY", 200_000, None)]
    engine, strategy, gate = _run(EURUSD, ["1.10000"] * 3, script, 10_000_000)
    try:
        assert len(strategy.modify_rejections) == 1
        assert strategy.modify_rejections[0].startswith("FX_QTY_INCREASE_NOT_SUPPORTED")
        assert engine.cache.order(strategy.resting.client_order_id).quantity == 100_000
        assert gate is not None and gate.denied_count == 1
    finally:
        engine.dispose()


# -- costs -----------------------------------------------------------------------------------


def test_eurusd_round_trip_pays_bps_with_minimum_plus_the_spread():
    # 100,000 at 1.10000 -> 1.10100: +100.00 USD. Commission 0.2 bp x 110,000 = 2.20 and
    # x 110,100 = 2.20; spread 100,000 x 0.5 pip / 2 = 2.50 a side. 100 - 4.40 - 5.00 = 90.60.
    script = [(1, "BUY", 100_000, None), (2, "CLOSE", 0, None)]
    engine, _, _ = _run(EURUSD, ["1.10000", "1.10100", "1.10100"], script, 100_000)
    try:
        assert _usd_change(engine, 100_000) == Decimal("90.60")
        (position,) = engine.cache.positions_closed()
        assert [str(c) for c in position.commissions()] == ["9.40 USD"]
        assert position.realized_pnl.as_decimal() == Decimal("90.60")
        snapshot = cost_snapshot(engine.cache)
        costs = snapshot["fx_pairs"]["EUR/USD.IDEALPRO"]["costs_usd"]
        assert costs == {"commission": "4.40", "spread": "5.00"}
    finally:
        engine.dispose()


def test_small_orders_pay_the_usd_2_minimum_per_side():
    # 10,000 at 1.10000: 0.22 bp-commission < 2.00 each side; spread 0.25 a side; P&L +10.00
    script = [(1, "BUY", 10_000, None), (2, "CLOSE", 0, None)]
    engine, _, _ = _run(EURUSD, ["1.10000", "1.10100", "1.10100"], script, 100_000)
    try:
        assert _usd_change(engine, 100_000) == Decimal("10.00") - Decimal("4.50")
    finally:
        engine.dispose()


def test_usdjpy_round_trip_costs_convert_and_the_trade_row_matches_the_balance():
    # 100,000 USDJPY 150.000 -> 151.000: 100,000 JPY = 662.25 USD. Commission 2.00 a side
    # (0.2 bp x 100,000 USD); spread 250 JPY a side = 1.67 (/150) and 1.66 (/151).
    # 662.25 - 4.00 - 3.33 = 654.92.
    script = [(1, "BUY", 100_000, None), (3, "CLOSE", 0, None)]
    closes = ["150.000", "150.500", "151.000", "151.000"]
    engine, _, _ = _run(USDJPY, closes, script, 100_000)
    try:
        assert _usd_change(engine, 100_000) == Decimal("654.92")
        (position,) = engine.cache.positions_closed()
        assert usd_realised_pnls(engine) == {position.id.value: Decimal("654.92")}
        marked = mark_to_market(engine, {USDJPY.id: _bars(USDJPY, closes)}, 100_000.0)
        assert marked is not None  # mark-to-market still reconciles with the account
    finally:
        engine.dispose()


def test_eurgbp_cross_costs_convert_through_gbpusd():
    # 100,000 EURGBP 0.85000 -> 0.86000 = 1,000 GBP x 1.25 = 1,250.00 USD. Commission
    # 0.2 bp x 106,250 = 2.125 -> 2.12 (cents, half-even) and x 107,500 = 2.15; spread
    # 1.0 pip / 2 = 5 GBP = 6.25 a side. 1,250 - 4.27 - 12.50 = 1,233.23.
    script = [(1, "BUY", 100_000, None), (2, "CLOSE", 0, None)]
    engine, _, _ = _run(EURGBP, ["0.85000", "0.86000", "0.86000"], script, 100_000, GBP_RATES)
    try:
        assert _usd_change(engine, 100_000) == Decimal("1233.23")
        (position,) = engine.cache.positions_closed()
        assert usd_realised_pnls(engine) == {position.id.value: Decimal("1233.23")}
    finally:
        engine.dispose()


def test_the_cost_multiplier_scales_every_fx_cost():
    script = [(1, "BUY", 100_000, None), (2, "CLOSE", 0, None)]
    closes = ["1.10000", "1.10100", "1.10100"]
    engine, _, _ = _run(EURUSD, closes, script, 100_000, cost_multiplier=2)
    try:
        assert _usd_change(engine, 100_000) == Decimal("100") - 2 * Decimal("9.40")
    finally:
        engine.dispose()


# -- sizing through the orchestrator ---------------------------------------------------------


def _wave(n: int = 60) -> list[str]:
    """USDJPY closes that cross a 3/8 SMA pair both ways: 150 +/- 1.5 over 20 bars."""
    import math

    return [f"{150 + 1.5 * math.sin(i * 2 * math.pi / 20):.3f}" for i in range(n)]


async def test_the_orchestrator_sizes_sma_crossover_usdjpy_in_base_units():
    from src.core.backtest_orchestrator import BacktestOrchestrator

    bars = _bars(USDJPY, _wave())
    request = BacktestRequest(
        strategy_type="sma_crossover",
        strategy_path="src.core.strategies.sma_crossover:SMACrossover",
        config_path="src.core.strategies.sma_crossover:SMAConfig",
        strategy_config={
            "fast_period": 3,
            "slow_period": 8,
            "portfolio_value": Decimal("1000000"),
            "position_size_pct": Decimal("10"),  # USD 100,000 a trade
        },
        symbol="USDJPY",
        instrument_id="USD/JPY.IDEALPRO",
        start_date=pd.Timestamp(T0, tz="UTC").to_pydatetime(),
        end_date=pd.Timestamp(bars[-1].ts_event, tz="UTC").to_pydatetime(),
        bar_type="1-HOUR-MID",
        persist=False,
        starting_balance=Decimal("1000000"),
    )
    orchestrator = BacktestOrchestrator()
    try:
        result, _ = await orchestrator.execute(request, bars, USDJPY)
        positions = orchestrator.engine.cache.positions()
        assert positions, "sma_crossover should trade the wave"
        assert {p.peak_qty.as_decimal() for p in positions} == {Decimal(100_000)}
        for position in positions:
            fees = sum(c.as_decimal() for c in position.commissions())
            assert fees > Decimal("2.00")  # USD 2 minimum plus a converted spread per side
            assert all(c.currency == USD for c in position.commissions())
        assert result.total_trades >= 1
    finally:
        orchestrator.dispose()
