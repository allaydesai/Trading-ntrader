"""Larry Connors RSI Mean Reversion strategy implementation."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import RelativeStrengthIndex, SimpleMovingAverage
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading.strategy import Strategy

from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.models.strategy import ConnorsRSIParameters


class ConnorsRSIMeanRevConfig(StrategyConfig):  # type: ignore[misc]
    """
    Configuration for Larry Connors RSI Mean Reversion strategy.

    Rules:
    - Trend Filter: Close > 200-day SMA
    - Buy Signal: RSI(2) < 10 at close
    - Sell Signal: Close > Yesterday's High
    - Time Stop: Exit after 5 days if sell signal not triggered
    """

    # Required
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal = Decimal("1000.0")  # Default: 1000 shares

    # Parameters
    rsi_period: int = 2
    buy_threshold: float = 10.0
    sma_trend_period: int = 200
    max_holding_days: int = 5


@register_strategy(
    name="connors_rsi_mean_rev",
    description="Larry Connors 2-period RSI Mean Reversion Strategy",
    aliases=["connors_rsi", "crsi"],
    param_model=ConnorsRSIParameters,
)
class LarryConnorsRSIMeanRev(Strategy):
    """
    Larry Connors RSI Mean Reversion Strategy.

    Trading Rules:
    1. Trend Filter: Close > 200-day SMA (long only when in uptrend)
    2. Buy Signal: RSI(2) < 10 at close
    3. Sell Signal: Close > Yesterday's High
    4. Time Stop: Exit after 5 days if sell signal not triggered
    """

    def __init__(self, config: ConnorsRSIMeanRevConfig) -> None:
        super().__init__(config)
        self.instrument_id = self.config.instrument_id

        # Indicators
        self.rsi = RelativeStrengthIndex(period=config.rsi_period)
        self.sma = SimpleMovingAverage(period=config.sma_trend_period)

        # State
        self._prev_high: Optional[float] = None
        self._bars_held: int = 0

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

        # 1. Update Indicators
        self.rsi.handle_bar(bar)
        self.sma.handle_bar(bar)

        # 2. Check Logic
        if not (self.rsi.initialized and self.sma.initialized):
            self._prev_high = float(bar.high)
            return

        # Portfolio check
        long_position = None
        positions = self.cache.positions(instrument_id=self.instrument_id)
        for p in positions:
            if p.is_open and p.is_long:
                long_position = p
                break

        close = float(bar.close)

        # Exit Logic
        if long_position:
            should_exit = False
            exit_reason = ""

            # Sell Signal: Close > Yesterday's High
            if self._prev_high is not None and close > self._prev_high:
                should_exit = True
                exit_reason = "Price > Prev High"

            # Time Stop: Exit after 5 days (check BEFORE incrementing)
            elif self._bars_held >= self.config.max_holding_days:
                should_exit = True
                exit_reason = f"Time Stop ({self._bars_held} days)"

            if should_exit:
                self.close_position(long_position)
                self.log.info(
                    f"SELL SIGNAL [{exit_reason}]: Close={close:.2f}, PrevHigh={self._prev_high}"
                )
                self._bars_held = 0  # Reset
            else:
                # Increment counter for next bar only if still holding
                self._bars_held += 1

        # Entry Logic
        else:
            self._bars_held = 0

            # Trend Filter: Close > SMA
            if close > self.sma.value:
                # Buy Signal: RSI < 10
                if self.rsi.value < self.config.buy_threshold:
                    qty = self.instrument.make_qty(self.config.trade_size)
                    order = self.order_factory.market(
                        instrument_id=self.instrument_id,
                        order_side=OrderSide.BUY,
                        quantity=qty,
                    )
                    self.submit_order(order)
                    self._bars_held = 1  # Initialize counter on entry
                    self.log.info(
                        f"BUY SIGNAL: RSI={self.rsi.value:.2f}, "
                        f"Close={close:.2f} > SMA={self.sma.value:.2f}"
                    )

        # Update state for next bar
        self._prev_high = float(bar.high)


# Register config
StrategyRegistry.set_config("connors_rsi_mean_rev", ConnorsRSIMeanRevConfig)
StrategyRegistry.set_param_model("connors_rsi_mean_rev", ConnorsRSIParameters)
