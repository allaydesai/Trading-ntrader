"""SMA Crossover strategy implementation using Nautilus Trader."""

from decimal import Decimal

from nautilus_trader.core.message import Event
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.strategy import StrategyConfig


class SMAConfig(StrategyConfig, frozen=True):
    """Configuration for SMA crossover strategy."""

    instrument_id: InstrumentId
    bar_type: str
    fast_period: int = 10
    slow_period: int = 20
    trade_size: Decimal = Decimal("1_000_000")


class SMACrossover(Strategy):
    """
    Simple Moving Average Crossover Strategy.

    Generates buy signals when fast SMA crosses above slow SMA.
    Generates sell signals when fast SMA crosses below slow SMA.
    """

    def __init__(self, config: SMAConfig) -> None:
        """Initialize the SMA crossover strategy."""
        super().__init__(config)

        # Strategy configuration
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        self.trade_size = Quantity.from_str(str(config.trade_size))

        # Initialize indicators
        self.fast_sma = SimpleMovingAverage(
            period=config.fast_period, price_type=PriceType.LAST
        )
        self.slow_sma = SimpleMovingAverage(
            period=config.slow_period, price_type=PriceType.LAST
        )

        # Track previous SMA values for crossover detection
        self._prev_fast_sma = None
        self._prev_slow_sma = None

        # Current position tracking
        self.position: Position | None = None

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
        self.fast_sma.handle_bar(bar)
        self.slow_sma.handle_bar(bar)

        # Wait for both indicators to be initialized
        if not (self.fast_sma.initialized and self.slow_sma.initialized):
            return

        # Get current SMA values
        fast_value = self.fast_sma.value
        slow_value = self.slow_sma.value

        # Check for crossover signals only if we have previous values
        if self._prev_fast_sma is not None and self._prev_slow_sma is not None:
            self._check_for_signals(fast_value, slow_value)

        # Store current values for next iteration
        self._prev_fast_sma = fast_value
        self._prev_slow_sma = slow_value

    def on_event(self, event: Event) -> None:
        """Handle events."""
        # Track position changes
        if hasattr(event, "position_id"):
            self.position = self.cache.position(event.position_id)

    def _check_for_signals(self, fast_value: float, slow_value: float) -> None:
        """
        Check for crossover signals and generate orders.

        Parameters
        ----------
        fast_value : float
            Current fast SMA value.
        slow_value : float
            Current slow SMA value.
        """
        # Detect bullish crossover (fast SMA crosses above slow SMA)
        if self._prev_fast_sma <= self._prev_slow_sma and fast_value > slow_value:
            self._generate_buy_signal()

        # Detect bearish crossover (fast SMA crosses below slow SMA)
        elif self._prev_fast_sma >= self._prev_slow_sma and fast_value < slow_value:
            self._generate_sell_signal()

    def _generate_buy_signal(self) -> None:
        """Generate a buy signal."""
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
                f"Generated BUY signal - Fast SMA: {self.fast_sma.value:.4f}, "
                f"Slow SMA: {self.slow_sma.value:.4f}"
            )

    def _generate_sell_signal(self) -> None:
        """Generate a sell signal."""
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
                f"Generated SELL signal - Fast SMA: {self.fast_sma.value:.4f}, "
                f"Slow SMA: {self.slow_sma.value:.4f}"
            )

    def on_dispose(self) -> None:
        """Clean up strategy resources."""
        pass  # Nothing additional to clean up
