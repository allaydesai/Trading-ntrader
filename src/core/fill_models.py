"""Custom fill models for realistic backtest execution.

``GapAwareFillModel`` corrects two artifacts of the Nautilus ``SimulatedExchange``
when matching against L1 daily-bar data (verified against nautilus_trader 1.220):

1. **Stop-market gap fills** — the engine fills a triggered stop-market order at
   its trigger price even when the bar *opens* beyond it (an overnight gap),
   overstating stop protection and understating tail losses. This model fills
   such stops at the bar's open; stops reached intrabar still fill at the
   trigger (the standard modeling assumption).
2. **Market-on-open exits** — ``TimeInForce.AT_THE_OPEN`` market orders are not
   supported by the simulated exchange, and a market order submitted from
   ``on_bar(T)`` fills at T's close. A market order tagged ``MOO_EXIT_TAG`` is
   instead filled at the *next* bar's open. Note the fill event still carries
   T's timestamp — only the price is repriced — so intraday equity marks lag by
   one bar on those exits while trade P&L is correct.

The model needs two registrations before the run (both provided by
``BacktestOrchestrator``): the engine clock (to know which bar is being matched)
and the bar series (to look up bar opens). Without them it degrades to the
default engine fill logic. All other order types pass through untouched.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import Sequence

from nautilus_trader.backtest.models import FillModel
from nautilus_trader.common.component import Clock
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import Bar, BookOrder
from nautilus_trader.model.enums import BookType, OrderSide, OrderType
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import Order

# Strategies tag exit market orders with this to request market-on-open fills.
MOO_EXIT_TAG = "MOO_EXIT"

# Effectively infinite book depth so the simulated level always fills in full.
_UNLIMITED_SIZE = 1_000_000_000


class GapAwareFillModel(FillModel):
    """Fill model with realistic overnight-gap handling for daily-bar backtests.

    Accepts the same probabilistic parameters as ``FillModel``; they apply to
    the order types this model does not intercept.
    """

    def __init__(
        self,
        prob_fill_on_limit: float = 1.0,
        prob_fill_on_stop: float = 1.0,
        prob_slippage: float = 0.0,
        random_seed: int | None = None,
    ) -> None:
        super().__init__(
            prob_fill_on_limit=prob_fill_on_limit,
            prob_fill_on_stop=prob_fill_on_stop,
            prob_slippage=prob_slippage,
            random_seed=random_seed,
        )
        self._clock: Clock | None = None
        self._bar_ts: dict[str, list[int]] = {}
        self._bar_opens: dict[str, list[float]] = {}

    def register_clock(self, clock: Clock) -> None:
        """Register the engine clock (``engine.kernel.clock``) used for bar lookup."""
        self._clock = clock

    def register_bars(self, bars: Sequence[Bar]) -> None:
        """Register the bar series so bar opens can be looked up at fill time."""
        opens_by_ts: dict[str, dict[int, float]] = defaultdict(dict)
        for key, ts_list in self._bar_ts.items():
            opens_by_ts[key] = dict(zip(ts_list, self._bar_opens[key]))
        for bar in bars:
            key = str(bar.bar_type.instrument_id)
            opens_by_ts[key][bar.ts_event] = bar.open.as_double()
        for key, by_ts in opens_by_ts.items():
            ordered = sorted(by_ts.items())
            self._bar_ts[key] = [ts for ts, _ in ordered]
            self._bar_opens[key] = [open_ for _, open_ in ordered]

    def get_orderbook_for_fill_simulation(
        self,
        instrument: Instrument,
        order: Order,
        best_bid: Price,
        best_ask: Price,
    ) -> OrderBook | None:
        """Return a one-level book at the corrected price, or None for default logic."""
        fill_price = self._fill_price_for(order)
        if fill_price is None:
            return None
        return self._single_level_book(instrument, order.side, fill_price)

    def _fill_price_for(self, order: Order) -> float | None:
        """Corrected fill price for the order, or None to use default engine logic."""
        if self._clock is None:
            return None
        ts_list = self._bar_ts.get(str(order.instrument_id))
        if not ts_list:
            return None
        opens = self._bar_opens[str(order.instrument_id)]
        now = self._clock.timestamp_ns()

        if order.order_type == OrderType.STOP_MARKET:
            bar_open = self._open_at(ts_list, opens, now)
            if bar_open is None:
                return None
            return self._stop_fill_price(order.side, order.trigger_price.as_double(), bar_open)

        if order.order_type == OrderType.MARKET and order.tags and MOO_EXIT_TAG in order.tags:
            # Last bar has no successor: fall back to the default (close) fill.
            return self._open_after(ts_list, opens, now)

        return None

    @staticmethod
    def _stop_fill_price(side: OrderSide, trigger: float, bar_open: float) -> float:
        """Trigger price intrabar; the bar's open when price gapped through it."""
        if side == OrderSide.SELL:
            return min(trigger, bar_open)
        return max(trigger, bar_open)

    @staticmethod
    def _open_at(ts_list: Sequence[int], opens: Sequence[float], ts: int) -> float | None:
        """Open of the bar at exactly ``ts``, or None if unknown."""
        i = bisect_left(ts_list, ts)
        if i < len(ts_list) and ts_list[i] == ts:
            return opens[i]
        return None

    @staticmethod
    def _open_after(ts_list: Sequence[int], opens: Sequence[float], ts: int) -> float | None:
        """Open of the first bar strictly after ``ts``, or None at series end."""
        i = bisect_right(ts_list, ts)
        if i < len(ts_list):
            return opens[i]
        return None

    @staticmethod
    def _single_level_book(instrument: Instrument, side: OrderSide, price: float) -> OrderBook:
        """One opposing level of effectively infinite size at ``price``."""
        book = OrderBook(instrument_id=instrument.id, book_type=BookType.L2_MBP)
        level = BookOrder(
            side=OrderSide.BUY if side == OrderSide.SELL else OrderSide.SELL,
            price=Price(price, instrument.price_precision),
            size=Quantity(_UNLIMITED_SIZE, instrument.size_precision),
            order_id=1,
        )
        book.add(level, 0, 0)
        return book
