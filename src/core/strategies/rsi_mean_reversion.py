"""RSI Mean Reversion strategy implementation using Nautilus Trader."""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import Optional

import pandas as pd
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading.strategy import Strategy

from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.models.strategy import MeanReversionParameters


class RSIMeanRevConfig(StrategyConfig):  # type: ignore[misc]
    """Configuration for RSI Mean Reversion strategy."""

    # Required
    instrument_id: InstrumentId  # e.g., InstrumentId.from_str("AAPL.NASDAQ")
    bar_type: BarType  # e.g., BarType.from_str("AAPL.NASDAQ-1-DAY[LAST]-EXTERNAL")
    trade_size: Decimal  # e.g., Decimal("100")
    order_id_tag: str  # unique per strategy instance (used in strategy_id)

    # Params (sane defaults)
    rsi_period: int = 2
    rsi_buy_threshold: float = 10.0  # buy when RSI < threshold
    exit_rsi: float = 50.0  # exit when RSI > exit_rsi
    sma_trend_period: int = 200  # only buy if close > SMA(trend_period)
    warmup_days: int = 400  # historical bars to warm up (>= sma_trend_period)
    cooldown_bars: int = 0  # optional cooldown after exit to reduce churn


@register_strategy(
    name="mean_reversion",
    description="RSI Mean Reversion Strategy with Trend Filter",
    aliases=["rsi", "rsi_mean_reversion", "meanreversion", "rsimeanrev"],
)
class RSIMeanRev(Strategy):
    """
    RSI Mean Reversion Strategy.

    Buys when RSI is oversold in an uptrend (close > SMA).
    Exits when RSI returns to neutral/overbought levels.
    """

    def __init__(self, config: RSIMeanRevConfig) -> None:
        """Initialize the RSI Mean Reversion strategy."""
        super().__init__(config)
        self.instrument_id = self.config.instrument_id
        self.bar_type = self.config.bar_type

        # Internal state
        self._closes: deque[float] = deque(
            maxlen=max(self.config.sma_trend_period, self.config.rsi_period) + 5
        )
        self._cooldown_left = 0

        # Wilder RSI incremental state
        self._rsi_ready = False
        self._prev_close: Optional[float] = None
        self._avg_gain = 0.0
        self._avg_loss = 0.0

        # Running SMA for trend filter (O(1))
        self._sma_sum = 0.0

    # ---- helpers
    def _update_rsi(self, close: float) -> float | None:
        """Update RSI calculation with new close price."""
        p = self.config.rsi_period
        if self._prev_close is None:
            self._prev_close = close
            return None

        change = close - self._prev_close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if not self._rsi_ready:
            self._closes.append(close)
            # bootstrap: use simple averages for first `p` deltas
            if len(self._closes) >= p + 1:
                gains, losses = 0.0, 0.0
                for i in range(1, len(self._closes)):
                    diff = self._closes[i] - self._closes[i - 1]
                    if diff >= 0:
                        gains += diff
                    else:
                        losses += -diff
                self._avg_gain = gains / p
                self._avg_loss = losses / p
                self._rsi_ready = True
        else:
            # Wilder smoothing
            self._avg_gain = ((self._avg_gain * (p - 1)) + gain) / p
            self._avg_loss = ((self._avg_loss * (p - 1)) + loss) / p

        self._prev_close = close

        if not self._rsi_ready:
            return None

        rs = self._avg_gain / self._avg_loss if self._avg_loss > 0 else float("inf")
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    def _update_sma(self, close: float) -> float | None:
        """Update SMA calculation with new close price."""
        n = self.config.sma_trend_period
        self._closes.append(close)
        self._sma_sum += close
        if len(self._closes) > n:
            self._sma_sum -= self._closes[-(n + 1)]
        return (self._sma_sum / n) if len(self._closes) >= n else None

    # ---- lifecycle
    def on_start(self) -> None:
        """Actions to be performed on strategy start."""
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            self.stop()
            return

        # Hydrate warmup history (start is required in recent versions)
        start = self.clock.utc_now() - pd.Timedelta(days=int(self.config.warmup_days))
        self.request_bars(self.bar_type, start=start)  # hydrate our indicator state
        self.subscribe_bars(self.bar_type)  # stream live bars
        # Optional: also subscribe to quotes if you care about slippage/spread
        # self.subscribe_quote_ticks(self.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        """
        Handle incoming bar data.

        Parameters
        ----------
        bar : Bar
            The bar to be handled.
        """
        if bar.bar_type != self.bar_type:
            return

        # Convert bar.close -> float; adjust if your build uses high-precision
        close = float(bar.close)
        rsi = self._update_rsi(close)
        sma = self._update_sma(close)

        if self._cooldown_left > 0:
            self._cooldown_left -= 1

        if rsi is None or sma is None:
            return  # not warmed up

        long_now = self.portfolio.is_net_long(self.instrument_id)
        flat_now = self.portfolio.is_flat(self.instrument_id)

        # Entry: dip in an uptrend
        if flat_now and self._cooldown_left == 0:
            if (rsi < float(self.config.rsi_buy_threshold)) and (close > sma):
                order = self.order_factory.market(
                    instrument_id=self.instrument_id,
                    order_side=OrderSide.BUY,
                    quantity=self.instrument.make_qty(self.config.trade_size),
                )
                self.submit_order(order)
                return

        # Exit: RSI mean reverts
        if long_now and rsi > float(self.config.exit_rsi):
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.instrument.make_qty(self.config.trade_size),
            )
            self.submit_order(order)
            self._cooldown_left = int(self.config.cooldown_bars or 0)


# Register config and parameter model for this strategy
StrategyRegistry.set_config("mean_reversion", RSIMeanRevConfig)
StrategyRegistry.set_param_model("mean_reversion", MeanReversionParameters)
StrategyRegistry.set_default_config(
    "mean_reversion",
    {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
        "trade_size": 1000000,
        "order_id_tag": "001",
        "rsi_period": 2,
        "rsi_buy_threshold": 10.0,
        "exit_rsi": 50.0,
        "sma_trend_period": 200,
        "warmup_days": 400,
        "cooldown_bars": 0,
    },
)
