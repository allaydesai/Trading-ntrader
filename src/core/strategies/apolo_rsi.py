"""Apolo RSI Mean Reversion strategy implementation.

A simple 2-period RSI mean reversion strategy that capitalizes on
reversion back up to the mean after sharp declines. Long only.

Trading Rules:
- Buy: RSI(2) < 10 (oversold after sharp decline)
- Sell: RSI(2) > 50 (mean reversion back up)
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading.strategy import Strategy

from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.models.strategy import ApoloRSIParameters


class ApoloRSIConfig(StrategyConfig):  # type: ignore[misc]
    """
    Configuration for Apolo RSI Mean Reversion strategy.

    Simple 2-period RSI rules:
    - Buy Signal: RSI(2) < buy_threshold
    - Sell Signal: RSI(2) > sell_threshold
    - Long only (no shorting)
    """

    # Required
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal = Decimal("1000.0")  # Default: 1000 shares

    # Parameters
    rsi_period: int = 2
    buy_threshold: float = 10.0
    sell_threshold: float = 50.0


@register_strategy(
    name="apolo_rsi",
    description="Apolo RSI Mean Reversion Strategy (2-period RSI, long only)",
    aliases=["apolo", "apolo_mean_reversion"],
    param_model=ApoloRSIParameters,
)
class ApoloRSI(Strategy):
    """
    Apolo RSI Mean Reversion Strategy.

    A simple mean reversion strategy using 2-period RSI:
    - Enters long when RSI drops below buy threshold (oversold)
    - Exits when RSI rises above sell threshold (mean reversion)
    - Long only, no trend filter

    This is a base strategy that can be extended with additional
    rules based on backtest results.
    """

    def __init__(self, config: ApoloRSIConfig) -> None:
        super().__init__(config)
        self.instrument_id = self.config.instrument_id

        # Initialize RSI indicator using Nautilus built-in
        self.rsi = RelativeStrengthIndex(period=config.rsi_period)

    def on_start(self) -> None:
        """Actions to be performed on strategy start."""
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            self.stop()
            return

        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """Handle incoming bar data."""
        if bar.bar_type != self.config.bar_type:
            return

        # Update RSI indicator
        self.rsi.handle_bar(bar)

        # Wait for indicator to warm up
        if not self.rsi.initialized:
            return

        # Check current position
        long_position = None
        positions = self.cache.positions(instrument_id=self.instrument_id)
        for p in positions:
            if p.is_open and p.is_long:
                long_position = p
                break

        rsi_value = self.rsi.value
        close = float(bar.close)

        # Exit Logic: Sell when RSI > sell_threshold
        if long_position:
            if rsi_value > self.config.sell_threshold:
                self.close_position(long_position)
                self.log.info(
                    f"SELL SIGNAL: RSI={rsi_value:.2f} > {self.config.sell_threshold}, "
                    f"Close={close:.2f}"
                )

        # Entry Logic: Buy when RSI < buy_threshold
        else:
            if rsi_value < self.config.buy_threshold:
                qty = self.instrument.make_qty(self.config.trade_size)
                order = self.order_factory.market(
                    instrument_id=self.instrument_id,
                    order_side=OrderSide.BUY,
                    quantity=qty,
                )
                self.submit_order(order)
                self.log.info(
                    f"BUY SIGNAL: RSI={rsi_value:.2f} < {self.config.buy_threshold}, "
                    f"Close={close:.2f}"
                )


# Register config and param model
StrategyRegistry.set_config("apolo_rsi", ApoloRSIConfig)
StrategyRegistry.set_param_model("apolo_rsi", ApoloRSIParameters)
