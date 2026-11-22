"""Futures M1: spec-built continuous contracts run through a real ``BacktestEngine``.

Proves the never-expiring activation/expiration window does not reject orders on
historical data, bars at the contract's precision are accepted, and Nautilus' realised
P&L applies the spec multiplier. Margin/commission/sizing are M3; this runs with zero fees
and Nautilus' default (unset) margins. Run with ``--forked`` (C extensions).
"""

from decimal import Decimal

import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.trading.strategy import Strategy

from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import build_futures_contract

pytestmark = pytest.mark.integration

# root -> (first close, step per bar) chosen to carry the series' native decimals
SERIES = {
    "ES": ("5922.58", "1.25"),
    "NQ": ("21000.25", "4.5"),
    "CL": ("71.234", "0.125"),
    "GC": ("2650.4", "1.1"),
    "ZN": ("110.703125", "0.015625"),
}


class _RoundTripConfig(StrategyConfig, frozen=True):
    bar_type: str


class _RoundTrip(Strategy):
    """Buy one contract on the first bar, sell it on the third."""

    def __init__(self, config: _RoundTripConfig) -> None:
        super().__init__(config)
        self._bars_seen = 0

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_bar(self, bar: Bar) -> None:
        self._bars_seen += 1
        if self._bars_seen in (1, 3):
            side = OrderSide.BUY if self._bars_seen == 1 else OrderSide.SELL
            instrument_id = bar.bar_type.instrument_id
            order = self.order_factory.market(instrument_id, side, Quantity.from_int(1))
            self.submit_order(order)


def _bars(contract, first: str, step: str, count: int = 5) -> list[Bar]:
    bar_type = BarType.from_str(f"{contract.id}-1-HOUR-LAST-EXTERNAL")
    start = pd.Timestamp("2024-03-04 15:00", tz="UTC").value
    bars = []
    for i in range(count):
        px = contract.make_price(Decimal(first) + Decimal(step) * i)
        ts = start + i * 3_600_000_000_000
        bars.append(Bar(bar_type, px, px, px, px, Quantity.from_int(100), ts, ts))
    return bars


@pytest.mark.parametrize("root", sorted(SERIES))
def test_round_trip_realises_multiplier_pnl(root):
    spec = get_contract_spec(root)
    contract = build_futures_contract(spec)
    first, step = SERIES[root]
    bars = _bars(contract, first, step)

    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    try:
        engine.add_venue(
            venue=Venue(spec.venue),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            starting_balances=[Money(1_000_000, USD)],
        )
        engine.add_instrument(contract)
        engine.add_data(bars)
        engine.add_strategy(_RoundTrip(_RoundTripConfig(bar_type=str(bars[0].bar_type))))
        engine.run()

        fills = engine.trader.generate_order_fills_report()
        assert len(fills) == 2, f"{root}: orders were not both filled"
        positions = engine.trader.generate_positions_report()
        assert len(positions) == 1
        entry = Decimal(str(positions.iloc[0]["avg_px_open"]))
        exit_ = Decimal(str(positions.iloc[0]["avg_px_close"]))
        realised = Decimal(str(positions.iloc[0]["realized_pnl"]).split()[0])
        assert exit_ > entry
        assert realised == (exit_ - entry) * spec.multiplier
    finally:
        engine.dispose()
