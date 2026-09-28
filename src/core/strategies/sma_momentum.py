"""SMA Momentum strategy implementation using Nautilus Trader."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.trading.strategy import Strategy

from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.core.strategy_warmup import warmup_lookback
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

    Both averages are registered Nautilus ``SimpleMovingAverage`` indicators
    (Story 4.4). They replaced a hand-rolled deque average that summed every
    close it had ever seen instead of a window — its ``len(q) > maxlen`` evict
    branch could never fire on a ``deque(maxlen=...)`` — so ``fast`` sat above
    ``slow`` permanently and the strategy could never cross.
    """

    def __init__(self, config: SMAMomentumConfig) -> None:
        """Initialize the SMA Momentum strategy."""
        super().__init__(config)
        self.instrument_id = self.config.instrument_id
        self.bar_type = self.config.bar_type

        self.fast_sma = SimpleMovingAverage(self.config.fast_period, price_type=PriceType.LAST)
        self.slow_sma = SimpleMovingAverage(self.config.slow_period, price_type=PriceType.LAST)
        self._prev_fast: Optional[float] = None
        self._prev_slow: Optional[float] = None

    def on_start(self) -> None:
        """Register the averages and ask for enough history to warm them (Story 4.4).

        ``subscribe_bars`` runs last in :meth:`_on_history_loaded`, never here,
        so the live stream starts only once the history has loaded (AR40).
        ``warmup_days`` is kept as a floor under the computed window, snapped
        with it so the adapter's rounding cannot cut it short.
        """
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            self.stop()
            return

        self.register_indicator_for_bars(self.bar_type, self.fast_sma)
        self.register_indicator_for_bars(self.bar_type, self.slow_sma)
        period = max(self.config.fast_period, self.config.slow_period)
        floor = timedelta(days=int(self.config.warmup_days))
        start = self.clock.utc_now() - warmup_lookback(self.bar_type, period, minimum=floor)
        self.request_bars(self.bar_type, start=start, callback=self._on_history_loaded)

    def _on_history_loaded(self, request_id: UUID4) -> None:
        """Carry the crossover baseline over from history, then go live.

        Only the baseline is taken — no signal is evaluated on a historical
        bar. Cold averages (a backtest's empty history) record nothing.
        """
        if self.fast_sma.initialized and self.slow_sma.initialized:
            self._prev_fast, self._prev_slow = self.fast_sma.value, self.slow_sma.value
        self.subscribe_bars(self.bar_type)

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

        # `Actor.handle_bar` has already fed both registered averages this bar.
        if not (self.fast_sma.initialized and self.slow_sma.initialized):
            return
        fast_val, slow_val = self.fast_sma.value, self.slow_sma.value

        # Cross detection needs previous values
        if self._prev_fast is None or self._prev_slow is None:
            self._prev_fast, self._prev_slow = fast_val, slow_val
            return

        crossed_up = self._prev_fast <= self._prev_slow and fast_val > slow_val
        crossed_dn = self._prev_fast >= self._prev_slow and fast_val < slow_val

        own = self._own_net_quantity()
        is_long, is_short, is_flat = own > 0, own < 0, own == 0

        # Long-only default: buy on golden cross, exit on death cross. Exits and
        # flips are sized from its own position, not `trade_size` (Story 4.5):
        # a resumed position can differ from it (an entry part-filled before a
        # restart). In backtests the two are always equal, so fills are unchanged.
        if crossed_up:
            if is_short:
                # flip to long: cover its own short, then open `trade_size`
                qty = self.instrument.make_qty(abs(own) + self.config.trade_size)
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
                    qty = self.instrument.make_qty(abs(own) + self.config.trade_size)
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
                        quantity=self.instrument.make_qty(abs(own)),
                    )
                    self.submit_order(order)

        self._prev_fast, self._prev_slow = fast_val, slow_val

    def _own_net_quantity(self) -> Decimal:
        """This strategy's signed net on its instrument (Story 4.5).

        Never the portfolio's: that nets every owner's positions, including
        the ones reconciliation holds (``EXTERNAL``/``INTERNAL-DIFF``), so a
        holding this strategy never opened would read as its own long and be
        sold on the next death cross. Backtests run one strategy per
        instrument, so there the two are equal.
        """
        positions = self.cache.positions_open(instrument_id=self.instrument_id, strategy_id=self.id)
        return sum((p.signed_decimal_qty() for p in positions), Decimal(0))


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
