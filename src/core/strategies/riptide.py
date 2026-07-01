"""Riptide — long-only pullback mean-reversion strategy (US equities, daily bars).

Buy a quality uptrending stock on a short-term pullback via a limit order, then
exit on the first sign of strength, a hard ATR stop, or a time stop.

Per-symbol logic (single-instrument scope). The universe ROC-100 ranking and the
10-position portfolio cap are a documented follow-up that requires a
multi-instrument runner; ROC-100 is computed and logged here so the ranking hook
is ready. See docs/plan: Riptide.

Setup (all required):
- Trend:     close > SMA(200)
- Pullback:  close is the lowest close of the last ``pullback_period`` days
- Liquidity: ``adv_period``-day average dollar volume >= ``min_adv_dollars``

Entry:
- Buy LIMIT ``limit_offset_pct`` below the setup-day close, valid for exactly the
  next trading day (cancelled at the next bar if unfilled).

Exits (precedence):
1. Hard stop  — resting STOP-MARKET at entry_price - ``atr_stop_mult`` * ATR(14)
   (fires intrabar on the low).
2. Time stop  — flatten at the open after ``max_hold_days`` full days held.
3. Green exit — flatten at the next open once a day closes above the prior close.
"""

from __future__ import annotations

from collections import deque
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from nautilus_trader.indicators import AverageTrueRange, SimpleMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PriceType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.models.strategy import RiptideParameters


class RiptideConfig(StrategyConfig):
    """Configuration for the Riptide pullback mean-reversion strategy."""

    instrument_id: InstrumentId
    bar_type: BarType
    portfolio_value: Decimal = Decimal("1000000")
    position_size_pct: Decimal = Decimal("10.0")
    sma_trend_period: int = 200
    pullback_period: int = 15
    limit_offset_pct: Decimal = Decimal("2.0")
    atr_period: int = 14
    atr_stop_mult: Decimal = Decimal("2.5")
    max_hold_days: int = 10
    adv_period: int = 20
    min_adv_dollars: Decimal = Decimal("10000000")
    roc_period: int = 100


@register_strategy(
    name="riptide",
    description="Pullback mean-reversion: buy the dip in an uptrend, exit on strength/ATR/time",
    aliases=["riptide", "rip"],
)
class Riptide(Strategy):
    """Long-only pullback mean-reversion strategy (single instrument)."""

    def __init__(self, config: RiptideConfig) -> None:
        super().__init__(config)

        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        # Coerce Decimal-typed params to Decimal: Nautilus StrategyConfig (msgspec)
        # does not convert, so YAML numbers arrive as int/float and would break
        # Decimal arithmetic (e.g. float / Decimal).
        self.portfolio_value = Decimal(str(config.portfolio_value))
        self.position_size_pct = Decimal(str(config.position_size_pct))
        self._limit_offset_pct = Decimal(str(config.limit_offset_pct))
        self._atr_stop_mult = Decimal(str(config.atr_stop_mult))
        self._max_hold_days = config.max_hold_days
        self._min_adv_dollars = Decimal(str(config.min_adv_dollars))
        self._roc_period = config.roc_period

        # Indicators
        self._sma = SimpleMovingAverage(config.sma_trend_period, price_type=PriceType.LAST)
        self._atr = AverageTrueRange(config.atr_period)

        # Rolling windows
        self._closes: deque[float] = deque(maxlen=config.pullback_period)
        self._dollar_vols: deque[float] = deque(maxlen=config.adv_period)
        self._roc_closes: deque[float] = deque(maxlen=config.roc_period + 1)
        self._prev_close: float | None = None

        # Entry / position lifecycle state
        self._entry_order = None
        self._entry_ts: int | None = None
        self._stop_order = None
        self._entry_price: Decimal | None = None
        self._bars_held: int = 0
        self._position_active: bool = False
        self._exit_pending: bool = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def on_start(self) -> None:
        """Subscribe to the configured bar type."""
        self.subscribe_bars(self.bar_type)

    def on_stop(self) -> None:
        """Unsubscribe; leave any open position for results extraction."""
        self.unsubscribe_bars(self.bar_type)

    # ------------------------------------------------------------------ #
    # Bar handling
    # ------------------------------------------------------------------ #
    def on_bar(self, bar: Bar) -> None:
        """Update indicators/windows, then manage exits or look for an entry."""
        prev_close = self._prev_close
        self._update_state(bar)

        if not self._warmed_up():
            self._prev_close = float(bar.close)
            return

        self._log_roc()

        if self._position_active and not self._exit_pending:
            self._manage_exit(bar, prev_close)
        elif not self._position_active and not self._exit_pending:
            self._expire_stale_entry(bar)
            if self._entry_order is None:
                self._maybe_enter(bar)

        self._prev_close = float(bar.close)

    def _update_state(self, bar: Bar) -> None:
        """Feed the new bar into indicators and rolling windows."""
        self._sma.handle_bar(bar)
        self._atr.handle_bar(bar)
        close = float(bar.close)
        self._closes.append(close)
        self._dollar_vols.append(close * float(bar.volume))
        self._roc_closes.append(close)

    def _warmed_up(self) -> bool:
        """True once trend, ATR, and the pullback window all have enough data."""
        return (
            self._sma.initialized
            and self._atr.initialized
            and len(self._closes) == self._closes.maxlen
        )

    # ------------------------------------------------------------------ #
    # Entry
    # ------------------------------------------------------------------ #
    def _maybe_enter(self, bar: Bar) -> None:
        """Place a buy-limit entry when all setup conditions are met."""
        if self.cache.orders_open(venue=self.instrument_id.venue, instrument_id=self.instrument_id):
            return
        if not self._is_setup(bar):
            return

        close = float(bar.close)
        limit_dec = self._compute_limit_price(close, self._limit_offset_pct)
        shares = self._position_shares(limit_dec)

        instrument = self.cache.instrument(self.instrument_id)
        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(shares),
            price=instrument.make_price(limit_dec),
            time_in_force=TimeInForce.GTC,  # one-day validity enforced manually
        )
        self._entry_order = order
        self._entry_ts = bar.ts_event
        self.submit_order(order)
        self.log.info(
            f"RIPTIDE setup | close={close:.2f} SMA200={self._sma.value:.2f} "
            f"-> BUY LIMIT {limit_dec} x{shares} (valid next day)"
        )

    def _expire_stale_entry(self, bar: Bar) -> None:
        """Cancel an unfilled entry once its one allowed trading day has passed.

        Fills are applied before ``on_bar`` for the same bar, so if we are here
        (flat) on a bar later than placement, the order did not fill and is dead.
        """
        if self._entry_order is None or self._entry_ts is None:
            return
        if bar.ts_event <= self._entry_ts:
            return
        if self._entry_order.is_open:
            self.cancel_order(self._entry_order)
            self.log.info("RIPTIDE entry limit expired unfilled -> cancelled")
        self._entry_order = None
        self._entry_ts = None

    def _is_setup(self, bar: Bar) -> bool:
        """Trend + pullback + liquidity gate for a new entry."""
        close = float(bar.close)
        trend = close > self._sma.value
        pullback = self._is_pullback_low(close, self._closes)
        liquid = self._is_liquid(self._dollar_vols, self._min_adv_dollars)
        return trend and pullback and liquid

    # ------------------------------------------------------------------ #
    # Exit management
    # ------------------------------------------------------------------ #
    def _manage_exit(self, bar: Bar, prev_close: float | None) -> None:
        """Apply time-stop then green-candle exit (hard stop rests on the book)."""
        self._bars_held += 1
        if self._should_time_exit(self._bars_held, self._max_hold_days):
            self._flatten("time stop")
            return
        if self._is_green_candle(float(bar.close), prev_close):
            self._flatten("green candle")

    def _flatten(self, reason: str) -> None:
        """Cancel the resting stop and submit a market exit (fills next open)."""
        if self._stop_order is not None and self._stop_order.is_open:
            self.cancel_order(self._stop_order)
        position = self._open_position()
        if position is not None:
            self.close_position(position)
            self._exit_pending = True
            self.log.info(f"RIPTIDE exit ({reason}) | flattening {position.quantity}")

    # ------------------------------------------------------------------ #
    # Order / position events
    # ------------------------------------------------------------------ #
    def on_position_opened(self, event) -> None:
        """Record entry state and rest the ATR hard stop."""
        position = self.cache.position(event.position_id)
        if position is None or not position.is_long:
            return
        self._position_active = True
        self._bars_held = 0
        self._entry_order = None
        self._entry_ts = None
        self._entry_price = Decimal(str(position.avg_px_open))

        stop_dec = self._compute_stop_price(self._entry_price, self._atr.value, self._atr_stop_mult)
        instrument = self.cache.instrument(self.instrument_id)
        stop = self.order_factory.stop_market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=position.quantity,
            trigger_price=instrument.make_price(stop_dec),
            reduce_only=True,
        )
        self._stop_order = stop
        self.submit_order(stop)
        self.log.info(
            f"RIPTIDE entered @ {self._entry_price} | hard stop {stop_dec} "
            f"(2.5*ATR={self._atr.value:.4f})"
        )

    def on_position_closed(self, event) -> None:
        """Reset all per-trade state after a flatten/stop."""
        if self._stop_order is not None and self._stop_order.is_open:
            self.cancel_order(self._stop_order)
        self._position_active = False
        self._exit_pending = False
        self._stop_order = None
        self._entry_price = None
        self._bars_held = 0

    def _open_position(self):
        """Return the current open long position for this instrument, if any."""
        for position in self.cache.positions(
            venue=self.instrument_id.venue, instrument_id=self.instrument_id
        ):
            if position.is_open:
                return position
        return None

    def _log_roc(self) -> None:
        """Compute and log ROC-100 (informational; feeds the future ranking)."""
        roc = self._compute_roc(self._roc_closes, self._roc_period)
        if roc is not None:
            self.log.info(f"RIPTIDE ROC{self._roc_period}={roc:.4f}")

    def _position_shares(self, price: Decimal) -> int:
        """Whole shares for ``position_size_pct`` of portfolio at ``price`` (min 1)."""
        notional = self.portfolio_value * (self.position_size_pct / Decimal("100"))
        shares = int(notional / price)
        return max(shares, 1)

    # ------------------------------------------------------------------ #
    # Pure decision helpers (unit-tested)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _compute_limit_price(close: float, offset_pct: Decimal) -> Decimal:
        """Buy limit ``offset_pct`` below ``close``, rounded to cents."""
        raw = Decimal(str(close)) * (Decimal("1") - offset_pct / Decimal("100"))
        return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _compute_stop_price(entry_price: Decimal, atr_value: float, mult: Decimal) -> Decimal:
        """Hard stop ``mult`` * ATR below the entry price, rounded to cents."""
        raw = entry_price - (Decimal(str(atr_value)) * mult)
        return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _is_pullback_low(close: float, recent_closes: Sequence[float]) -> bool:
        """True when ``close`` is the lowest close in the window."""
        return bool(recent_closes) and close <= min(recent_closes)

    @staticmethod
    def _is_liquid(dollar_volumes: Sequence[float], min_adv: Decimal) -> bool:
        """True when average dollar volume clears the ADV threshold."""
        if not dollar_volumes:
            return False
        avg = sum(dollar_volumes) / len(dollar_volumes)
        return avg >= float(min_adv)

    @staticmethod
    def _is_green_candle(close: float, prev_close: float | None) -> bool:
        """True when today's close exceeds the prior close."""
        return prev_close is not None and close > prev_close

    @staticmethod
    def _should_time_exit(bars_held: int, max_hold: int) -> bool:
        """True once the position has been held the full holding period."""
        return bars_held >= max_hold

    @staticmethod
    def _compute_roc(closes: Sequence[float], period: int) -> float | None:
        """Rate of change over ``period`` bars, or None if history is too short."""
        if len(closes) <= period:
            return None
        ref = closes[-1 - period]
        if ref == 0:
            return None
        return (closes[-1] - ref) / ref


# Register config and parameter model for this strategy
StrategyRegistry.set_config("riptide", RiptideConfig)
StrategyRegistry.set_param_model("riptide", RiptideParameters)
StrategyRegistry.set_default_config(
    "riptide",
    {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL",
        "portfolio_value": 1000000,
        "position_size_pct": 10.0,
        "sma_trend_period": 200,
        "pullback_period": 15,
        "limit_offset_pct": 2.0,
        "atr_period": 14,
        "atr_stop_mult": 2.5,
        "max_hold_days": 10,
        "adv_period": 20,
        "min_adv_dollars": 10000000,
        "roc_period": 100,
    },
)
