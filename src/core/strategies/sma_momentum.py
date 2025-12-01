"""SMA Momentum strategy implementation using Nautilus Trader."""

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
from src.models.strategy import MomentumParameters


class SMAMomentumConfig(StrategyConfig):  # type: ignore[misc]
    """Configuration for SMA Momentum strategy."""

    # Required
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    order_id_tag: str

    # Params (optimized for minute data)
    fast_period: int = 20
    slow_period: int = 50
    warmup_days: int = 1  # Minimal warmup for limited data
    allow_short: bool = False  # set True if you want symmetrical short entries


@register_strategy(
    name="momentum",
    description="SMA Momentum Strategy (Golden/Death Cross)",
    aliases=["sma_momentum", "smamomentum", "momentumstrategy"],
)
class SMAMomentum(Strategy):
    """
    SMA Momentum Strategy.

    Trades on moving average crossovers (golden cross/death cross).
    Buys when fast MA crosses above slow MA.
    Sells when fast MA crosses below slow MA.
    Optional short selling support.
    """

    def __init__(self, config: SMAMomentumConfig) -> None:
        """Initialize the SMA Momentum strategy."""
        super().__init__(config)
        self.instrument_id = self.config.instrument_id
        self.bar_type = self.config.bar_type

        self._fast: deque[float] = deque(maxlen=self.config.fast_period)
        self._slow: deque[float] = deque(maxlen=self.config.slow_period)
        self._fast_sum = 0.0
        self._slow_sum = 0.0
        self._prev_fast: Optional[float] = None
        self._prev_slow: Optional[float] = None

    def on_start(self) -> None:
        """Actions to be performed on strategy start."""
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            self.stop()
            return

        start = self.clock.utc_now() - pd.Timedelta(days=int(self.config.warmup_days))
        self.request_bars(self.bar_type, start=start)
        self.subscribe_bars(self.bar_type)

    def _update_ma(
        self, q: deque, total: float, x: float, maxlen: int
    ) -> tuple[float | None, float]:
        """Update moving average calculation."""
        q.append(x)
        total += x
        if len(q) > maxlen:
            total -= q[-(maxlen + 1)]
        return (total / maxlen if len(q) >= maxlen else None, total)

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

        close = float(bar.close)

        fast_val, self._fast_sum = self._update_ma(
            self._fast, self._fast_sum, close, self.config.fast_period
        )
        slow_val, self._slow_sum = self._update_ma(
            self._slow, self._slow_sum, close, self.config.slow_period
        )

        if fast_val is None or slow_val is None:
            return

        # Cross detection needs previous values
        if self._prev_fast is None or self._prev_slow is None:
            self._prev_fast, self._prev_slow = fast_val, slow_val
            return

        crossed_up = self._prev_fast <= self._prev_slow and fast_val > slow_val
        crossed_dn = self._prev_fast >= self._prev_slow and fast_val < slow_val

        is_long = self.portfolio.is_net_long(self.instrument_id)
        is_short = self.portfolio.is_net_short(self.instrument_id)
        is_flat = self.portfolio.is_flat(self.instrument_id)

        # Long-only default: buy on golden cross, exit on death cross
        if crossed_up:
            if is_short:
                # flip to long (cover + go long) by buying `trade_size * 2`
                qty = self.instrument.make_qty(self.config.trade_size * 2)
            elif is_flat:
                qty = self.instrument.make_qty(self.config.trade_size)
            else:
                qty = None

            if qty is not None:
                order = self.order_factory.market(
                    instrument_id=self.instrument_id,
                    order_side=OrderSide.BUY,
                    quantity=qty,
                )
                self.submit_order(order)

        elif crossed_dn:
            if self.config.allow_short:
                if is_long:
                    qty = self.instrument.make_qty(self.config.trade_size * 2)
                    side = OrderSide.SELL
                elif is_flat:
                    qty = self.instrument.make_qty(self.config.trade_size)
                    side = OrderSide.SELL
                else:
                    qty = None
                    side = None
                if qty is not None:
                    order = self.order_factory.market(
                        instrument_id=self.instrument_id,
                        order_side=side,
                        quantity=qty,
                    )
                    self.submit_order(order)
            else:
                # long-only: just exit if long
                if is_long:
                    order = self.order_factory.market(
                        instrument_id=self.instrument_id,
                        order_side=OrderSide.SELL,
                        quantity=self.instrument.make_qty(self.config.trade_size),
                    )
                    self.submit_order(order)

        self._prev_fast, self._prev_slow = fast_val, slow_val


# Register config and parameter model for this strategy
StrategyRegistry.set_config("momentum", SMAMomentumConfig)
StrategyRegistry.set_param_model("momentum", MomentumParameters)
StrategyRegistry.set_default_config(
    "momentum",
    {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
        "trade_size": 1000000,
        "order_id_tag": "002",
        "fast_period": 20,
        "slow_period": 50,
        "warmup_days": 1,
        "allow_short": False,
    },
)
