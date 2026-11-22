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
   ``on_bar(T)`` is matched in the same bar, at T's close. A fill model can only
   reprice that fill, not move it to a later bar (an empty book rejects the order).

   So a gap-aware venue also gets **next-bar execution** (``gap_aware_venue_options``):
   a 1 ns ``LatencyModel`` keeps every command sent while bar T is handled in flight,
   and ``NextBarExecutionModule`` releases them at the start of bar T+1, before the
   matching engine sees T+1's prices. A market order tagged ``MOO_EXIT_TAG`` then
   fills at the open of the bar it executes on (T+1), and the fill, the position
   close and the realised P&L all carry T+1's timestamp.

   Limit, stop and cancel commands behave as before: when they are released the book
   still holds T's close, and nothing has traded since. An *untagged* market order on
   a gap-aware venue fills at T's close but is stamped on T+1. An MOO exit signalled
   on the final bar has no next bar and does not fill. Nautilus processes every venue
   after each data point, so every instrument in a gap-aware run must have bars at the
   same timestamps (``bind_fill_model`` checks).

``bind_fill_model`` registers what the model needs before the run (the orchestrator
calls it): the engine clock (to know which bar is being matched) and the bar series
(to look up bar opens). Without them it degrades to the default engine fill logic.
All other order types pass through untouched.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from typing import Any, Sequence

from nautilus_trader.backtest.config import SimulationModuleConfig
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, LatencyModel
from nautilus_trader.backtest.modules import SimulationModule
from nautilus_trader.common.component import Clock, Logger
from nautilus_trader.core.data import Data
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
            # Keyed by ts_init: the engine sets its clock to each bar's ts_init.
            opens_by_ts[key][bar.ts_init] = bar.open.as_double()
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
            # Next-bar execution releases the order as bar T+1 starts, so the clock reads
            # T+1: fill at its open. No bar at the clock: the default fill.
            return self._open_at(ts_list, opens, now)

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


class NextBarExecutionModule(SimulationModule):
    """Release the venue's in-flight commands at the start of each bar.

    Paired with a 1 ns ``LatencyModel`` (see ``gap_aware_venue_options``): commands sent
    while bar T is handled stay in flight past T, and ``pre_process`` (called by the
    exchange before it matches bar T+1) processes them while the book still holds T's
    close, stamped with T+1's time.
    """

    def __init__(self) -> None:
        super().__init__(SimulationModuleConfig())

    def pre_process(self, data: Data) -> None:
        """Process every command due by this bar before its prices are matched."""
        self.exchange.process(data.ts_init)

    def process(self, ts_now: int) -> None:
        """Nothing to do after commands are processed."""

    def log_diagnostics(self, logger: Logger) -> None:
        """No diagnostics."""

    def reset(self) -> None:
        """Stateless."""


def gap_aware_venue_options(fill_model: FillModel) -> dict[str, Any]:
    """Extra ``add_venue`` arguments for ``fill_model``: next-bar execution if gap-aware.

    Any other fill model gets none, so its venue is set up exactly as before.
    """
    if not isinstance(fill_model, GapAwareFillModel):
        return {}
    return {
        "latency_model": LatencyModel(base_latency_nanos=1),
        "modules": [NextBarExecutionModule()],
    }


def bind_fill_model(fill_model: FillModel, engine: BacktestEngine, bars: Sequence[Bar]) -> None:
    """Register the engine clock and ``bars`` with a gap-aware model; no-op for others.

    Raises:
        ValueError: Two instruments in the run have bars at different timestamps. Nautilus
            processes every venue after each data point, so any bar releases in-flight
            commands; an MOO exit would be released on another instrument's bar (even on
            another venue) and filled at a stale price.
    """
    if isinstance(fill_model, GapAwareFillModel):
        _check_alignment(bars)
        fill_model.register_clock(engine.kernel.clock)
        fill_model.register_bars(bars)


def _check_alignment(bars: Sequence[Bar]) -> None:
    """Every instrument in the run must have bars at the same ``ts_init`` timestamps."""
    series: dict[str, set[int]] = defaultdict(set)
    for bar in bars:
        series[str(bar.bar_type.instrument_id)].add(bar.ts_init)
    if len({frozenset(ts) for ts in series.values()}) > 1:
        raise ValueError(
            "fill_model gap_aware needs every instrument in the run to have bars at the same "
            f"timestamps ({', '.join(sorted(series))} differ); use the default fill model"
        )
