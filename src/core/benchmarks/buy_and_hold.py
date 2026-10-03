"""Buy-and-hold benchmark.

The research loop never reads a strategy's result in isolation: it is compared
with buying the same instrument on the same window and holding it. The
benchmark is a Nautilus strategy run through the same orchestrator, engine,
fill model, commission model and metric code as the strategy it is measured
against.

**Backtest-only by construction.** The position is closed in ``on_stop``: at
the end of a backtest the engine stops the trader and then processes the venues
once more, so the exit fills at the last price and the gain lands in the
account balance the metrics read (they count realised P&L only). Closing
everything on stop is exactly what a live strategy must never do, so this class
lives outside ``src/core/strategies/`` and is **not** registered in
``StrategyRegistry`` — ``live create`` cannot select it, and the live-path
guards that scan the strategies folder do not need an exemption. It is run by
module path (see ``src.core.benchmarks.BENCHMARKS``).
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading.strategy import Strategy
from pydantic import BaseModel, Field


class BuyAndHoldConfig(StrategyConfig, kw_only=True, frozen=True):  # type: ignore[misc]
    """Configuration for the buy-and-hold benchmark."""

    instrument_id: InstrumentId
    bar_type: BarType
    allocation_pct: Decimal = Decimal("100")


class BuyAndHoldParameters(BaseModel):
    """User-settable parameters of the buy-and-hold benchmark."""

    allocation_pct: Decimal = Field(
        default=Decimal("100"),
        gt=0,
        le=100,
        description="Percent of starting cash invested on the first bar",
    )


class BuyAndHold(Strategy):
    """Buy whole shares with ``allocation_pct`` of the cash on the first bar; hold to the end."""

    def __init__(self, config: BuyAndHoldConfig) -> None:
        """Initialize the benchmark."""
        super().__init__(config)
        self.instrument_id = self.config.instrument_id
        self.bar_type = self.config.bar_type
        self._bought = False

    def on_start(self) -> None:
        """Look up the instrument and subscribe to bars."""
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            self.stop()
            return
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        """Buy once, on the first bar."""
        if self._bought or bar.bar_type != self.bar_type:
            return
        self._bought = True
        quantity = self._entry_quantity(Decimal(str(bar.close)))
        if quantity <= 0:
            self.log.warning("Allocation buys less than one share; holding cash")
            return
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(quantity),
        )
        self.submit_order(order)

    def on_stop(self) -> None:
        """Close the position so the result is realised in the account balance."""
        self.close_all_positions(self.instrument_id)

    def _entry_quantity(self, price: Decimal) -> Decimal:
        account = self.portfolio.account(self.instrument_id.venue)
        cash = account.balance_total(self.instrument.quote_currency).as_decimal()
        budget = cash * self.config.allocation_pct / Decimal(100)
        return (budget / price).to_integral_value(rounding=ROUND_FLOOR)
