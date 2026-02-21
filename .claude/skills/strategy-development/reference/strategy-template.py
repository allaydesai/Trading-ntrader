"""
<STRATEGY_NAME> strategy implementation using Nautilus Trader.

TODO: Replace all <PLACEHOLDERS> with actual values.
"""

from decimal import Decimal

from nautilus_trader.indicators import SimpleMovingAverage  # TODO: Replace with your indicator
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.models.strategy import (
    pass  # TODO: Import your parameter model from src/models/strategy.py
)


# --- Config Class (File 2) ---
class <Name>Config(StrategyConfig):
    """
    Configuration for <STRATEGY_NAME> strategy.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument to trade.
    bar_type : BarType
        The bar type for strategy execution.
    portfolio_value : Decimal
        Starting portfolio value for position sizing.
    position_size_pct : Decimal
        Position size as percentage of portfolio.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    portfolio_value: Decimal = Decimal("1000000")
    position_size_pct: Decimal = Decimal("10.0")
    # TODO: Add strategy-specific parameters here


# --- Strategy Class (File 1) ---
@register_strategy(
    name="<strategy_name>",  # snake_case canonical name
    description="<Human-readable description of what strategy does>",
    aliases=["<alias1>", "<alias2>"],  # short names for CLI/API
)
class <StrategyName>(Strategy):
    """<STRATEGY_NAME> Strategy — <one-line description>."""

    def __init__(self, config: <Name>Config) -> None:
        super().__init__(config)

        # Store config values
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        self.portfolio_value = config.portfolio_value
        self.position_size_pct = config.position_size_pct

        # Initialize indicators
        # TODO: Replace with your indicators
        # self.indicator = SomeIndicator(period=config.period)

        # State tracking for signal detection
        self._current_bar: Bar | None = None
        # self._prev_value: float | None = None

    def on_start(self) -> None:
        """Subscribe to market data on strategy start."""
        self.subscribe_bars(self.bar_type)

    def on_stop(self) -> None:
        """Close all positions and unsubscribe on strategy stop."""
        self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Handle incoming bar data.

        IMPORTANT: Follow this exact order:
        1. Store bar -> 2. Update indicators -> 3. Check initialized
        -> 4. Read values -> 5. Check signals -> 6. Store prev values
        """
        # 1. Store current bar for position sizing
        self._current_bar = bar

        # 2. Update indicators
        # self.indicator.handle_bar(bar)

        # 3. Check initialization
        # if not self.indicator.initialized:
        #     return

        # 4. Read current values
        # current_value = self.indicator.value

        # 5. Check for signals (only with previous values)
        # if self._prev_value is not None:
        #     self._check_signals(current_value)

        # 6. Store for next iteration
        # self._prev_value = current_value

    def _calculate_position_size(self) -> Quantity:
        """Calculate position size: (portfolio * pct / 100) / price."""
        if self._current_bar is None:
            raise ValueError("Cannot calculate position size without current bar")

        position_value = self.portfolio_value * (self.position_size_pct / Decimal("100"))
        current_price = Decimal(str(self._current_bar.close))
        shares = max(int(position_value / current_price), 1)
        return Quantity.from_int(shares)

    def _generate_buy_signal(self) -> None:
        """Generate buy signal — close shorts, open long if none exists."""
        positions = self.cache.positions(
            venue=self.instrument_id.venue, instrument_id=self.instrument_id
        )
        has_short = any(p.is_short and p.is_open for p in positions)
        has_long = any(p.is_long and p.is_open for p in positions)

        if has_short:
            for p in positions:
                if p.is_short and p.is_open:
                    self.close_position(p)

        if not has_long:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=self._calculate_position_size(),
            )
            self.submit_order(order)

    def _generate_sell_signal(self) -> None:
        """Generate sell signal — close longs, open short if none exists."""
        positions = self.cache.positions(
            venue=self.instrument_id.venue, instrument_id=self.instrument_id
        )
        has_long = any(p.is_long and p.is_open for p in positions)
        has_short = any(p.is_short and p.is_open for p in positions)

        if has_long:
            for p in positions:
                if p.is_long and p.is_open:
                    self.close_position(p)

        if not has_short:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self._calculate_position_size(),
            )
            self.submit_order(order)

    def on_dispose(self) -> None:
        """Clean up strategy resources."""
        pass


# --- Registration (MUST be at module bottom, AFTER all class definitions) ---
StrategyRegistry.set_config("<strategy_name>", <Name>Config)
StrategyRegistry.set_param_model("<strategy_name>", <ParameterModel>)  # from models/strategy.py
StrategyRegistry.set_default_config(
    "<strategy_name>",
    {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
        "portfolio_value": 1000000,
        "position_size_pct": 10.0,
        # TODO: Add strategy-specific defaults
    },
)
