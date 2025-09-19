"""Momentum strategy implementation using Nautilus Trader."""

import math
from decimal import Decimal
from typing import List

from nautilus_trader.core.message import Event
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.strategy import StrategyConfig


class MomentumConfig(StrategyConfig):
    """Configuration for Momentum strategy."""

    instrument_id: InstrumentId
    bar_type: str
    rsi_period: int = 14
    oversold_threshold: float = 30.0
    overbought_threshold: float = 70.0
    trade_size: Decimal = Decimal("1_000_000")


class MomentumStrategy(Strategy):
    """
    Momentum Strategy using RSI (Relative Strength Index).

    Generates buy signals when RSI crosses below oversold threshold.
    Generates sell signals when RSI crosses above overbought threshold.
    Exits when RSI returns to neutral zone (45-55).
    """

    def __init__(self, config: MomentumConfig) -> None:
        """Initialize the Momentum strategy."""
        super().__init__(config)

        # Strategy configuration
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        self.rsi_period = config.rsi_period
        self.oversold_threshold = config.oversold_threshold
        self.overbought_threshold = config.overbought_threshold
        self.trade_size = Quantity.from_str(str(config.trade_size))

        # Initialize RSI indicator
        self.rsi = RelativeStrengthIndex(period=config.rsi_period)

        # Price history for tracking
        self._price_history: List[float] = []

        # Track current RSI value
        self._current_rsi: float | None = None

        # Current position tracking
        self.position: Position | None = None

        # Track last RSI for signal generation
        self._last_rsi: float | None = None

    def on_start(self) -> None:
        """Actions to be performed on strategy start."""
        bar_type = BarType.from_str(self.bar_type)
        self.subscribe_bars(bar_type)

    def on_stop(self) -> None:
        """Actions to be performed on strategy stop."""
        self.close_all_positions(self.instrument_id)
        bar_type = BarType.from_str(self.bar_type)
        self.unsubscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Handle incoming bar data.

        Parameters
        ----------
        bar : Bar
            The bar to be handled.
        """
        # Update RSI indicator with new bar
        self.rsi.handle_bar(bar)

        # Update price history for tracking
        close_price = float(bar.close)
        self._price_history.append(close_price)

        # Keep only the required number of prices for RSI period
        if len(self._price_history) > self.rsi_period:
            self._price_history.pop(0)

        # Wait for sufficient data to calculate RSI
        if not self.rsi.initialized:
            return

        # Get current RSI value
        self._current_rsi = self.rsi.value

        # Check for momentum signals
        if self._last_rsi is not None:
            self._check_for_signals(self._current_rsi)

        # Store current RSI for next iteration
        self._last_rsi = self._current_rsi

    def on_event(self, event: Event) -> None:
        """Handle events."""
        # Track position changes
        if hasattr(event, "position_id"):
            self.position = self.cache.position(event.position_id)

    def _check_for_signals(self, current_rsi: float) -> None:
        """
        Check for momentum signals and generate orders.

        Parameters
        ----------
        current_rsi : float
            Current RSI value.
        """
        if self._last_rsi is None:
            return

        # Check for oversold condition (RSI crossed below oversold threshold)
        if (
            self._last_rsi >= self.oversold_threshold
            and current_rsi < self.oversold_threshold
        ):
            self._generate_buy_signal()

        # Check for overbought condition (RSI crossed above overbought threshold)
        elif (
            self._last_rsi <= self.overbought_threshold
            and current_rsi > self.overbought_threshold
        ):
            self._generate_sell_signal()

        # Check for exit conditions (RSI returns to neutral zone)
        elif self.position:
            # Define neutral zone (45-55)
            neutral_low = 45.0
            neutral_high = 55.0

            if (
                self.position.is_long
                and current_rsi >= neutral_low
                and current_rsi <= neutral_high
            ):
                self._exit_position("RSI returned to neutral zone (Long exit)")

            elif (
                self.position.is_short
                and current_rsi >= neutral_low
                and current_rsi <= neutral_high
            ):
                self._exit_position("RSI returned to neutral zone (Short exit)")

    def _generate_buy_signal(self) -> None:
        """Generate a buy signal (oversold condition)."""
        # Close any existing short position first
        if self.position and self.position.is_short:
            self.close_position(self.position)

        # Only open new long position if we don't already have one
        if not self.position or not self.position.is_long:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self.trade_size,
            )
            self.submit_order(order)

            self.log.info(
                f"Generated BUY signal (Oversold) - RSI: {self._current_rsi:.2f}, "
                f"Threshold: {self.oversold_threshold:.2f}"
            )

    def _generate_sell_signal(self) -> None:
        """Generate a sell signal (overbought condition)."""
        # Close any existing long position first
        if self.position and self.position.is_long:
            self.close_position(self.position)

        # Only open new short position if we don't already have one
        if not self.position or not self.position.is_short:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.trade_size,
            )
            self.submit_order(order)

            self.log.info(
                f"Generated SELL signal (Overbought) - RSI: {self._current_rsi:.2f}, "
                f"Threshold: {self.overbought_threshold:.2f}"
            )

    def _exit_position(self, reason: str) -> None:
        """Exit current position with reason."""
        if self.position:
            self.close_position(self.position)
            self.log.info(f"Exiting position: {reason}")

    def on_dispose(self) -> None:
        """Clean up strategy resources."""
        pass  # Nothing additional to clean up
