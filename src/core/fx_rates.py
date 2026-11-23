"""FX M3: the exchange rates a USD single-currency FX venue converts P&L with (design D4).

Nautilus' accounts manager converts a fill's quote-currency P&L into the account's USD with
``Cache.get_xrate``, which reads only cached quote ticks (or BID+ASK bar pairs), never ``MID``
bars. ``FxRateFeeder`` closes that gap: on every ``MID`` bar of a rate-source pair it writes a
``QuoteTick(bid=ask=close)`` straight into the cache (no message-bus publish, so strategies
and the matching engine never see it) and sets the portfolio's mark xrate. It also checks every
FX fill against the cache, recording the rate the account converted at, or a miss when there
was none (Nautilus then logs "Cannot calculate account state" and leaves the USD balance
unchanged, so a miss must fail the run: ``src/core/fx_results.py``).

Ordering (pinned by ``tests/integration/core/test_fx_conversion_engine.py``):

- The feeder is added before any strategy, so it handles each bar before a strategy acts on it.
- A cross's rate-source bars (GBPUSD for EURGBP) are added before the traded bars; at equal
  timestamps the engine dispatches data in the order it was added.

A fill converts at the latest rate-source close the feeder has handled when the fill happens.
See ``docs/agent/fx-backtesting.md`` for what that means per order type.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CurrencyPair, Instrument
from nautilus_trader.model.objects import Currency, Quantity

from src.core.position_sizing import fx_units_for_budget, quote_to_account_rate
from src.services.fx.pair_specs import pair_names

ACCOUNT_CURRENCY = USD
_ONE = Quantity.from_int(1)

BarsAndInstrument = tuple[list[Bar], Instrument]


class FxConversionPairError(ValueError):
    """A traded pair's quote currency has no pair (spec or bars) to convert it to USD."""


def is_fx(instrument: Instrument) -> bool:
    """A spot FX pair built from ``pair_specs.yaml`` (not a Kraken crypto ``CurrencyPair``)."""
    if not isinstance(instrument, CurrencyPair):
        return False
    return (instrument.info or {}).get("asset_class") == "FX"


def needs_conversion(instrument: Instrument) -> bool:
    """An FX pair whose P&L settles in a currency other than the USD account's."""
    return is_fx(instrument) and instrument.quote_currency != ACCOUNT_CURRENCY


def fx_ticker(instrument: Instrument) -> str:
    """``EURUSD`` for ``EUR/USD.IDEALPRO``: the pair-spec and catalog ticker."""
    return f"{instrument.base_currency.code}{instrument.quote_currency.code}"


def conversion_pair(instrument: Instrument, known: frozenset[str] | None = None) -> str | None:
    """The pair whose bars convert ``instrument``'s quote currency to USD, or None.

    A USD quote needs none; a USD base (USDJPY) is its own rate source; a cross (EURGBP)
    needs ``{quote}USD`` or ``USD{quote}``, whichever has a spec.

    Raises:
        FxConversionPairError: The cross's quote currency has no USD pair in ``known``
            (default: the shipped pair specs).
    """
    if not needs_conversion(instrument):
        return None
    if instrument.base_currency == ACCOUNT_CURRENCY:
        return fx_ticker(instrument)
    quote = instrument.quote_currency.code
    known = pair_names() if known is None else known
    for candidate in (f"{quote}USD", f"USD{quote}"):
        if candidate in known:
            return candidate
    raise FxConversionPairError(
        f"{instrument.id} settles in {quote}, but there is no {quote}USD or USD{quote} pair "
        "to convert it to the USD account; add one to pair_specs.yaml and the catalog"
    )


def conversion_pairs_for(instruments: Iterable[Instrument]) -> list[str]:
    """Rate-source pairs a run needs that it does not already trade (sorted tickers)."""
    instruments = list(instruments)
    traded = {fx_ticker(i) for i in instruments if is_fx(i)}
    needed = {conversion_pair(i) for i in instruments}
    return sorted(pair for pair in needed if pair is not None and pair not in traded)


def usd_per_quote(
    instrument: Instrument, price: Decimal, cache, side: OrderSide | None = None
) -> Decimal | None:
    """USD per unit of ``instrument``'s quote currency at ``price``; None for a missing rate.

    Closed-form for a USD quote (1) and a USD base (``1 / price``); a cross reads the cache's
    rate table, the accounts manager's own lookup (``BID`` for a sell, ``ASK`` for a buy,
    ``MID`` without a side). M4's fee model, margin gate and FX sizing all convert with it.
    """
    if ACCOUNT_CURRENCY in (instrument.quote_currency, instrument.base_currency):
        return quote_to_account_rate(instrument, price)
    price_type = PriceType.MID if side is None else _side_price_type(side)
    rate = cache.get_xrate(
        instrument.id.venue, instrument.quote_currency, ACCOUNT_CURRENCY, price_type
    )
    return None if rate is None else Decimal(str(rate))


def _side_price_type(side: OrderSide) -> PriceType:
    return PriceType.BID if side == OrderSide.SELL else PriceType.ASK


def fx_units(notional_usd: Decimal, price: Decimal, instrument: Instrument, cache) -> Decimal:
    """Whole base units of FX ``instrument`` worth ``notional_usd`` (0: no order).

    Raises:
        ValueError: A cross whose quote currency has no USD rate in the cache yet.
    """
    rate = usd_per_quote(instrument, price, cache)
    rates = {instrument.quote_currency.code: rate} if rate is not None else None
    return fx_units_for_budget(notional_usd, price, instrument, rates)


class FxRateBook:
    """Cache writes and records behind ``FxRateFeeder``; holds only the cache (no bus)."""

    def __init__(self, cache) -> None:
        self._cache = cache
        self._history: dict[str, list[tuple[int, float]]] = {}
        self._fill_rates: dict[str, float] = {}
        self.misses: list[str] = []

    def on_bar(self, bar: Bar) -> None:
        """Make ``bar``'s close the pair's cached quote, mark xrate and USD rate history."""
        instrument = self._cache.instrument(bar.bar_type.instrument_id)
        tick = QuoteTick(instrument.id, bar.close, bar.close, _ONE, _ONE, bar.ts_event, bar.ts_init)
        self._cache.add_quote_tick(tick)
        base, quote, close = instrument.base_currency, instrument.quote_currency, bar.close
        self._cache.set_mark_xrate(base, quote, close.as_double())
        if quote == ACCOUNT_CURRENCY:
            self._record(base, bar.ts_event, close.as_double())
        elif base == ACCOUNT_CURRENCY:
            self._record(quote, bar.ts_event, 1.0 / close.as_double())

    def _record(self, currency: Currency, ts: int, rate: float) -> None:
        self._history.setdefault(currency.code, []).append((ts, rate))

    def on_fill(self, fill: OrderFilled) -> None:
        """Record the USD rate the account converted ``fill`` at, or a miss without one."""
        instrument = self._cache.instrument(fill.instrument_id)
        if instrument is None or not needs_conversion(instrument):
            return
        quote = instrument.quote_currency
        rate = self._cache.get_xrate(  # the accounts manager's exact lookup
            fill.instrument_id.venue,
            quote,
            ACCOUNT_CURRENCY,
            PriceType.BID if fill.order_side == OrderSide.SELL else PriceType.ASK,
        )
        if rate is None:
            self.misses.append(
                f"{fill.instrument_id} fill {fill.trade_id} at ts {fill.ts_event}: "
                f"no {quote.code}/{ACCOUNT_CURRENCY.code} rate"
            )
            return
        self._fill_rates[str(fill.trade_id)] = rate

    def fill_rate(self, trade_id: TradeId) -> float | None:
        """The USD per quote unit the account converted the fill ``trade_id`` at."""
        return self._fill_rates.get(str(trade_id))

    def rate_history(self, currency: Currency) -> list[tuple[int, float]]:
        """``(ts_event, USD per unit of currency)`` for every rate bar handled, in order."""
        return list(self._history.get(currency.code, []))


class FxRateFeederConfig(ActorConfig, frozen=True):
    """The ``MID`` bar types of the run's rate-source pairs."""

    bar_types: tuple[str, ...]


class FxRateFeeder(Actor):
    """Feed rate-source ``MID`` bars into the cache's exchange rates (see the module doc)."""

    def __init__(self, config: FxRateFeederConfig) -> None:
        super().__init__(config)
        self.book: FxRateBook | None = None

    def on_start(self) -> None:
        self.book = FxRateBook(self.cache)
        for bar_type in self.config.bar_types:
            self.subscribe_bars(BarType.from_str(bar_type))
        self.msgbus.subscribe(topic="events.order.*", handler=self._on_order_event)

    def on_bar(self, bar: Bar) -> None:
        assert self.book is not None
        self.book.on_bar(bar)

    def _on_order_event(self, event) -> None:
        if isinstance(event, OrderFilled) and self.book is not None:
            self.book.on_fill(event)


def add_fx_data(
    engine: BacktestEngine,
    data: Sequence[BarsAndInstrument],
    rate_sources: Sequence[BarsAndInstrument] = (),
) -> FxRateFeeder | None:
    """Add every instrument and its bars to ``engine``, plus the rate feeder FX needs.

    Rate-source bars go in first (conversion-only pairs from ``rate_sources``, then any traded
    rate source), the other traded bars after, so at equal timestamps a cross's rate is in the
    cache before its own bar is matched. Call this before adding strategies: the feeder must
    be registered ahead of them. Runs with no pair needing conversion are added unchanged, in
    the given order, and get no feeder.

    Raises:
        FxConversionPairError: A needed rate-source pair is neither traded nor in
            ``rate_sources``.
    """
    instruments = [instrument for _, instrument in data]
    needed = {pair for i in instruments if (pair := conversion_pair(i)) is not None}
    available = {fx_ticker(i): bars for bars, i in [*rate_sources, *data] if is_fx(i)}
    missing = sorted(needed - available.keys())
    if missing:
        raise FxConversionPairError(
            f"FX conversion pair {', '.join(missing)} is not loaded for this run; "
            "load its bars from the same catalog and timeframe"
        )
    traded = {fx_ticker(i) for i in instruments if is_fx(i)}
    conversion_only = [d for d in rate_sources if fx_ticker(d[1]) in needed - traded]
    traded_sources = [d for d in data if is_fx(d[1]) and fx_ticker(d[1]) in needed]
    rest = [d for d in data if not (is_fx(d[1]) and fx_ticker(d[1]) in needed)]
    for bars, instrument in [*conversion_only, *traded_sources, *rest]:
        engine.add_instrument(instrument)
        engine.add_data(bars)
    if not needed:
        return None
    bar_types = tuple(str(available[pair][0].bar_type) for pair in sorted(needed))
    feeder = FxRateFeeder(FxRateFeederConfig(bar_types=bar_types))
    engine.add_actor(feeder)
    return feeder
