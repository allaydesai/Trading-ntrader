"""Mean Reversion strategy implementation using Nautilus Trader."""

import math
from decimal import Decimal
from typing import List

from nautilus_trader.core.message import Event
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.strategy import StrategyConfig


class MeanReversionConfig(StrategyConfig):
    """Configuration for Mean Reversion strategy."""

    instrument_id: InstrumentId
    bar_type: str
    lookback_period: int = 20
    num_std_dev: float = 2.0
    trade_size: Decimal = Decimal("1_000_000")


class MeanReversionStrategy(Strategy):
    """
    Mean Reversion Strategy using Bollinger Bands.

    Generates buy signals when price crosses below lower Bollinger Band.
    Generates sell signals when price crosses above upper Bollinger Band.
    Exits when price returns to moving average.
    """

    def __init__(self, config: MeanReversionConfig) -> None:
        """Initialize the Mean Reversion strategy."""
        super().__init__(config)

        # Strategy configuration
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        self.lookback_period = config.lookback_period
        self.num_std_dev = config.num_std_dev
        self.trade_size = Quantity.from_str(str(config.trade_size))

        # Initialize moving average indicator
        self.sma = SimpleMovingAverage(
            period=config.lookback_period, price_type=PriceType.LAST
        )

        # Price history for standard deviation calculation
        self._price_history: List[float] = []

        # Track Bollinger Band values
        self._upper_band: float | None = None
        self._lower_band: float | None = None
        self._middle_band: float | None = None

        # Current position tracking
        self.position: Position | None = None

        # Track last bar for signal generation
        self._last_close: float | None = None

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
        # Update indicators with new bar
        self.sma.handle_bar(bar)

        # Update price history for standard deviation calculation
        close_price = float(bar.close)
        self._price_history.append(close_price)

        # Keep only the required number of prices for lookback period
        if len(self._price_history) > self.lookback_period:
            self._price_history.pop(0)

        # Wait for sufficient data to calculate Bollinger Bands
        if not self.sma.initialized or len(self._price_history) < self.lookback_period:
            return

        # Calculate Bollinger Bands
        self._calculate_bollinger_bands()

        # Check for mean reversion signals
        if self._last_close is not None:
            self._check_for_signals(close_price)

        # Store current close for next iteration
        self._last_close = close_price

    def on_event(self, event: Event) -> None:
        """Handle events."""
        # Track position changes
        if hasattr(event, "position_id"):
            self.position = self.cache.position(event.position_id)

    def _calculate_bollinger_bands(self) -> None:
        """Calculate Bollinger Bands from SMA and price history."""
        if not self.sma.initialized or len(self._price_history) < self.lookback_period:
            return

        # Get the middle band (SMA)
        self._middle_band = self.sma.value

        # Calculate standard deviation
        mean = sum(self._price_history) / len(self._price_history)
        variance = sum((price - mean) ** 2 for price in self._price_history) / len(
            self._price_history
        )
        std_dev = math.sqrt(variance)

        # Calculate upper and lower bands
        self._upper_band = self._middle_band + (self.num_std_dev * std_dev)
        self._lower_band = self._middle_band - (self.num_std_dev * std_dev)

    def _check_for_signals(self, current_close: float) -> None:
        """
        Check for mean reversion signals and generate orders.

        Parameters
        ----------
        current_close : float
            Current bar's closing price.
        """
        if (
            self._upper_band is None
            or self._lower_band is None
            or self._middle_band is None
            or self._last_close is None
        ):
            return

        # Check for oversold condition (price crossed below lower band)
        if self._last_close >= self._lower_band and current_close < self._lower_band:
            self._generate_buy_signal()

        # Check for overbought condition (price crossed above upper band)
        elif self._last_close <= self._upper_band and current_close > self._upper_band:
            self._generate_sell_signal()

        # Check for exit conditions (price returns to middle band)
        elif self.position:
            if (
                self.position.is_long
                and self._last_close < self._middle_band
                and current_close >= self._middle_band
            ):
                self._exit_position("Price returned to middle band (Long exit)")

            elif (
                self.position.is_short
                and self._last_close > self._middle_band
                and current_close <= self._middle_band
            ):
                self._exit_position("Price returned to middle band (Short exit)")

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
                f"Generated BUY signal (Oversold) - Price: {self._last_close:.5f}, "
                f"Lower Band: {self._lower_band:.5f}, "
                f"Middle Band: {self._middle_band:.5f}"
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
                f"Generated SELL signal (Overbought) - Price: {self._last_close:.5f}, "
                f"Upper Band: {self._upper_band:.5f}, "
                f"Middle Band: {self._middle_band:.5f}"
            )

    def _exit_position(self, reason: str) -> None:
        """Exit current position with reason."""
        if self.position:
            self.close_position(self.position)
            self.log.info(f"Exiting position: {reason}")

    def on_dispose(self) -> None:
        """Clean up strategy resources."""
        pass  # Nothing additional to clean up
