"""SMA Crossover Long-Only strategy implementation using Nautilus Trader."""

from decimal import Decimal

from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.models.strategy import SMAParameters


class SMALongOnlyConfig(StrategyConfig):
    """
    Configuration for SMA crossover long-only strategy.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument to trade.
    bar_type : BarType
        The bar type for strategy execution.
    fast_period : int, default=10
        Period for fast moving average.
    slow_period : int, default=20
        Period for slow moving average.
    portfolio_value : Decimal, default=1000000
        Starting portfolio value in USD for position sizing calculations.
    position_size_pct : Decimal, default=10.0
        Position size as percentage of portfolio (e.g., 10.0 = 10%).
        Example: With $1M portfolio and 10%, each trade uses $100K notional.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    fast_period: int = 10
    slow_period: int = 20
    portfolio_value: Decimal = Decimal("1000000")
    position_size_pct: Decimal = Decimal("10.0")


@register_strategy(
    name="sma_crossover_long_only",
    description="SMA Crossover Long-Only Strategy (No Shorts)",
    aliases=["sma_long", "sma_long_only", "smacrossoverlongonly"],
)
class SMACrossoverLongOnly(Strategy):
    """
    Simple Moving Average Crossover Long-Only Strategy.

    Generates buy signals when fast SMA crosses above slow SMA.
    Closes long positions when fast SMA crosses below slow SMA.
    Does not take short positions.
    """

    def __init__(self, config: SMALongOnlyConfig) -> None:
        """Initialize the SMA crossover long-only strategy."""
        super().__init__(config)

        # Strategy configuration
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        self.portfolio_value = config.portfolio_value
        self.position_size_pct = config.position_size_pct

        # Initialize indicators
        self.fast_sma = SimpleMovingAverage(period=config.fast_period, price_type=PriceType.LAST)
        self.slow_sma = SimpleMovingAverage(period=config.slow_period, price_type=PriceType.LAST)

        # Track previous SMA values for crossover detection
        self._prev_fast_sma: float | None = None
        self._prev_slow_sma: float | None = None

        # Current bar for position sizing calculations
        self._current_bar: Bar | None = None

    def on_start(self) -> None:
        """Actions to be performed on strategy start."""
        self.subscribe_bars(self.bar_type)

    def on_stop(self) -> None:
        """Actions to be performed on strategy stop."""
        self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Handle incoming bar data.

        Parameters
        ----------
        bar : Bar
            The bar to be handled.
        """
        # Store current bar for position sizing
        self._current_bar = bar

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

    def _calculate_position_size(self) -> Quantity:
        """
        Calculate position size based on portfolio percentage and current price.

        Returns
        -------
        Quantity
            Number of shares to trade.

        Notes
        -----
        Position size is calculated as:
        shares = (portfolio_value * position_size_pct / 100) / current_price

        Example:
            Portfolio: $1,000,000
            Position %: 10%
            Current Price: $140
            Shares = ($1M * 10% / 100) / $140 = 714 shares (~$100K notional)
        """
        if self._current_bar is None:
            raise ValueError("Cannot calculate position size without current bar data")

        # Calculate position value in USD
        position_value = self.portfolio_value * (self.position_size_pct / Decimal("100"))

        # Get current price (use close price from bar)
        current_price = Decimal(str(self._current_bar.close))

        # Calculate number of shares
        shares = int(position_value / current_price)

        # Ensure at least 1 share
        shares = max(shares, 1)

        self.log.info(
            f"Position sizing: Portfolio=${self.portfolio_value:,.0f}, "
            f"Size%={self.position_size_pct}%, "
            f"Price=${current_price:.2f}, "
            f"Shares={shares} (~${shares * current_price:,.0f} notional)"
        )

        return Quantity.from_int(shares)

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
        # Only check for crossovers if we have previous values
        if self._prev_fast_sma is not None and self._prev_slow_sma is not None:
            # Detect bullish crossover (fast SMA crosses above slow SMA)
            if self._prev_fast_sma <= self._prev_slow_sma and fast_value > slow_value:
                self._generate_buy_signal()

            # Detect bearish crossover (fast SMA crosses below slow SMA)
            elif self._prev_fast_sma >= self._prev_slow_sma and fast_value < slow_value:
                self._generate_sell_signal()

    def _generate_buy_signal(self) -> None:
        """Generate a buy signal."""
        # Get current positions from cache (always up-to-date)
        positions = self.cache.positions(
            venue=self.instrument_id.venue, instrument_id=self.instrument_id
        )

        # Check for open long positions
        has_long = any(p.is_long and p.is_open for p in positions)

        # Only open new long position if we don't already have one
        if not has_long:
            # Calculate position size based on current price
            quantity = self._calculate_position_size()

            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
            )
            self.submit_order(order)

            self.log.info(
                f"Generated BUY signal - Fast SMA: {self.fast_sma.value:.4f}, "
                f"Slow SMA: {self.slow_sma.value:.4f}"
            )

    def _generate_sell_signal(self) -> None:
        """Generate a sell signal (close long)."""
        # Get current positions from cache
        positions = self.cache.positions(
            venue=self.instrument_id.venue, instrument_id=self.instrument_id
        )

        # Check for open long positions
        has_long = any(p.is_long and p.is_open for p in positions)

        # Close any existing long position
        if has_long:
            for position in positions:
                if position.is_long and position.is_open:
                    self.close_position(position)
                    self.log.info(f"Closed LONG position: {position.id}")

            self.log.info(
                f"Generated SELL signal (Close Long) - Fast SMA: {self.fast_sma.value:.4f}, "
                f"Slow SMA: {self.slow_sma.value:.4f}"
            )

    def on_dispose(self) -> None:
        """Clean up strategy resources."""
        pass  # Nothing additional to clean up


# Register config and parameter model for this strategy
StrategyRegistry.set_config("sma_crossover_long_only", SMALongOnlyConfig)
StrategyRegistry.set_param_model("sma_crossover_long_only", SMAParameters)
StrategyRegistry.set_default_config(
    "sma_crossover_long_only",
    {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
        "fast_period": 10,
        "slow_period": 20,
        "portfolio_value": 1000000,
        "position_size_pct": 10.0,
    },
)
