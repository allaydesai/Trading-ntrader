"""Bollinger Band reversal strategy with Weekly MA trend filter."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.indicators import AverageTrueRange, BollingerBands, SimpleMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders.list import OrderList
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.models.strategy import BollingerReversalParameters


class BollingerReversalConfig(StrategyConfig):
    """
    Configuration for Bollinger Reversal strategy.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    portfolio_value: Decimal = Decimal("1000000")
    daily_bb_period: int = 20
    daily_bb_std_dev: float = 2.0
    weekly_ma_period: int = 20
    weekly_ma_tolerance_pct: float = 0.10
    max_risk_pct: Decimal = Decimal("1.0")
    stop_loss_atr_mult: Decimal = Decimal("2.0")
    take_profit_rr: Decimal = Decimal("2.0")
    atr_period: int = 14


@register_strategy(
    name="bollinger_reversal",
    description="Bollinger Band Reversal with Weekly MA Confluence",
    aliases=["bollinger", "bollingerreversal", "bb_reversal"],
)
class BollingerReversalStrategy(Strategy):
    """
    Multi-timeframe strategy combining Daily Bollinger Bands with Weekly SMA support/resistance.

    Logic:
    - Bullish: Daily price closes below Lower Bollinger Band AND
      price is near Weekly SMA support.
    - Bearish: Daily price closes above Upper Bollinger Band AND
      price is near Weekly SMA resistance.
    """

    def __init__(self, config: BollingerReversalConfig) -> None:
        super().__init__(config)

        # Note: self.config is already set by parent class and is read-only
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type

        # Daily Indicators
        self._bollinger = BollingerBands(
            period=config.daily_bb_period,
            k=config.daily_bb_std_dev,
        )
        self._atr = AverageTrueRange(
            period=config.atr_period,
        )

        # Weekly Indicator (Manually updated)
        self._weekly_sma = SimpleMovingAverage(
            period=config.weekly_ma_period,
        )

        # Weekly Aggregation State
        self._current_week_iso: tuple[int, int] | None = None
        self._current_week_close: float | None = None

    def on_start(self) -> None:
        """Subscribe to bars."""
        self.subscribe_bars(self.bar_type)

    def on_stop(self) -> None:
        """Cleanup."""
        self.unsubscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """
        Handle Daily bars.
        1. Update Weekly Aggregation & Indicator.
        2. Update Daily Indicators.
        3. Check Entry/Exit signals.
        """
        # Update Indicators
        self._bollinger.handle_bar(bar)
        self._atr.handle_bar(bar)

        # Handle Weekly Aggregation
        self._update_weekly_aggregation(bar)

        # Ensure all indicators are initialized
        if not (
            self._bollinger.initialized and self._atr.initialized and self._weekly_sma.initialized
        ):
            return

        # Check for Entries (exits handled by bracket orders)
        if self._can_enter():
            self._check_entries(bar)

    def _update_weekly_aggregation(self, bar: Bar) -> None:
        """
        Aggregate Daily bars into Weekly SMA.

        Logic:
        - Detect change in ISO week number.
        - When week changes, push the *last known close* (from previous bar) to Weekly SMA.
        - Note: This approximates Weekly Close using the last Daily Close of the week
          (usually Friday).
        """
        # Convert bar timestamp (ns) to datetime
        bar_dt = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc)
        iso_year, iso_week, _ = bar_dt.isocalendar()
        current_iso = (iso_year, iso_week)

        if self._current_week_iso is None:
            # First bar seen
            self._current_week_iso = current_iso
            self._current_week_close = float(bar.close)
        elif current_iso != self._current_week_iso:
            # Week has changed! Commit the PREVIOUS week's close to the SMA.
            if self._current_week_close is not None:
                self._weekly_sma.update_raw(self._current_week_close)
                # Log only occasionally or on change to avoid spam
                # self.log.info(
                #     f"Weekly SMA Updated: {self._weekly_sma.value:.4f} "
                #     f"(Close: {self._current_week_close})"
                # )

            # Reset for new week
            self._current_week_iso = current_iso
            self._current_week_close = float(bar.close)
        else:
            # Same week, just update the running close
            self._current_week_close = float(bar.close)

    def _check_entries(self, bar: Bar) -> None:
        """
        Check for entry signals based on multi-timeframe confluence.

        Bullish Setup: Price at Weekly MA support + Daily oversold
        - Price is within tolerance range of Weekly MA (at support level)
        - Price is oversold on Daily (below Lower Bollinger Band)
        - Confluence suggests reversal bounce off support

        Bearish Setup: Price at Weekly MA resistance + Daily overbought
        - Price is within tolerance range of Weekly MA (at resistance level)
        - Price is overbought on Daily (above Upper Bollinger Band)
        - Confluence suggests reversal rejection at resistance
        """
        close_price = float(bar.close)
        weekly_ma = self._weekly_sma.value
        tolerance = self.config.weekly_ma_tolerance_pct

        # Calculate Weekly MA tolerance band
        ma_upper_bound = weekly_ma * (1.0 + tolerance)
        ma_lower_bound = weekly_ma * (1.0 - tolerance)

        # Check if price is AT or NEAR the Weekly MA (within tolerance band)
        is_at_weekly_ma = ma_lower_bound <= close_price <= ma_upper_bound

        # --- Bullish Signal ---
        # Price has pulled back to Weekly MA support AND is oversold on daily
        is_oversold = close_price < self._bollinger.lower

        if is_oversold and is_at_weekly_ma:
            self._enter_position(OrderSide.BUY, bar, "Bullish: Oversold at Weekly Support")
            return

        # --- Bearish Signal ---
        # Price has rallied to Weekly MA resistance AND is overbought on daily
        is_overbought = close_price > self._bollinger.upper

        if is_overbought and is_at_weekly_ma:
            self._enter_position(OrderSide.SELL, bar, "Bearish: Overbought at Weekly Resistance")
            return

    def _enter_position(self, side: OrderSide, bar: Bar, reason: str) -> None:
        """Execute entry with bracket orders (SL + TP)."""
        self.log.info(f"_enter_position called: side={side}, reason={reason}")

        # Calculate position size
        qty = self._calculate_position_size(bar)
        self.log.info(f"Position size calculated: {qty}")

        if qty <= 0:
            self.log.warning(f"Position size is {qty}, skipping entry")
            return

        if not self._atr.initialized:
            self.log.warning("ATR not initialized, cannot calculate stops")
            return

        if self._atr.value == 0:
            self.log.warning("ATR value is 0, cannot calculate stops")
            return

        # Calculate stop loss and take profit prices based on bar close
        atr_value = Decimal(str(self._atr.value))
        stop_distance = atr_value * self.config.stop_loss_atr_mult
        tp_distance = stop_distance * self.config.take_profit_rr
        entry_price = Decimal(str(bar.close))

        if side == OrderSide.BUY:
            sl_price = entry_price - stop_distance
            tp_price = entry_price + tp_distance
        else:
            sl_price = entry_price + stop_distance
            tp_price = entry_price - tp_distance

        # Create bracket OrderList (entry + SL + TP as atomic unit)
        order_list: OrderList = self.order_factory.bracket(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=Quantity.from_int(qty),
            entry_order_type=OrderType.MARKET,  # Execute immediately at market
            sl_trigger_price=Price.from_str(f"{float(sl_price):.2f}"),
            tp_price=Price.from_str(f"{float(tp_price):.2f}"),
        )

        # Submit bracket as single OrderList
        self.submit_order_list(order_list)

        self.log.info(
            f"BRACKET ORDER | {side} | Qty: {qty} | Reason: {reason} | "
            f"Entry: ~{bar.close} | SL: ${float(sl_price):.2f} | TP: ${float(tp_price):.2f}"
        )

    def _calculate_position_size(self, bar: Bar) -> int:
        """Calculate size based on risk % and ATR."""
        if not self._atr.initialized or self._atr.value == 0:
            return 0

        account_balance = (
            self.config.portfolio_value
        )  # Or self.cache.account(venue).balance_total()
        risk_amount = account_balance * (self.config.max_risk_pct / Decimal("100"))

        atr_value = Decimal(str(self._atr.value))
        stop_distance = atr_value * self.config.stop_loss_atr_mult

        if stop_distance == 0:
            return 0

        # Value per contract (assuming 1:1 for simple spot/equity)
        # For FX, this might need adjustment (e.g. 100,000 units)
        # Here we assume "qty" is number of units/shares.

        qty = int(risk_amount / stop_distance)
        return max(qty, 1)

    def _can_enter(self) -> bool:
        """Check if we can enter a new position (no existing open positions)."""
        # Check cache for open positions
        positions = self.cache.positions(
            venue=self.instrument_id.venue, instrument_id=self.instrument_id
        )
        return not any(p.is_open for p in positions)


# Register config and parameter model for this strategy
StrategyRegistry.set_config("bollinger_reversal", BollingerReversalConfig)
StrategyRegistry.set_param_model("bollinger_reversal", BollingerReversalParameters)
StrategyRegistry.set_default_config(
    "bollinger_reversal",
    {
        "instrument_id": "EUR/USD.SIM",
        "bar_type": "EUR/USD.SIM-1-DAY-MID-EXTERNAL",
        "portfolio_value": 1000000,
        "daily_bb_period": 20,
        "daily_bb_std_dev": 2.0,
        "weekly_ma_period": 20,
        "weekly_ma_tolerance_pct": 0.05,
        "max_risk_pct": 1.0,
        "stop_loss_atr_mult": 2.0,
        "atr_period": 14,
    },
)
