"""FX M3: settling a finished FX run in USD (the missing-rate guard and the exit rates).

``FxRateFeeder`` (``src/core/fx_rates.py``) records the rate the account converted every FX
fill at, and a miss for every fill the cache had no rate for. Here a finished engine is read
back: a miss (or FX fills with no feeder at all) fails the run, because Nautilus left the USD
balance unchanged for that fill. Otherwise each position's realised P&L is rebuilt in USD,
every reducing fill converted at the rate the account used for it.
"""

from __future__ import annotations

import math
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.objects import Money

from src.core.fx_rates import ACCOUNT_CURRENCY, FxRateBook, FxRateFeeder, needs_conversion


class FxRateMissingError(RuntimeError):
    """An FX fill had no USD conversion rate, so the account's USD balance did not move."""


def _fx_fills(engine: BacktestEngine) -> list[OrderFilled]:
    fills = []
    for order in engine.cache.orders():
        instrument = engine.cache.instrument(order.instrument_id)
        if instrument is not None and needs_conversion(instrument):
            fills += [e for e in order.events if isinstance(e, OrderFilled)]
    return fills


def require_fx_rates(engine: BacktestEngine) -> FxRateBook | None:
    """The run's rate book, or None for a run with no fill needing conversion.

    Raises:
        FxRateMissingError: A fill needing conversion had no rate in the cache, or such
            fills happened with no ``FxRateFeeder`` registered.
    """
    trader = getattr(engine, "trader", None)
    if trader is None:  # a live host's settled results: no backtest feeder, no FX (yet)
        return None
    feeders = [a for a in trader.actors() if isinstance(a, FxRateFeeder)]
    book = feeders[0].book if feeders else None
    if book is None:
        fills = _fx_fills(engine)
        if not fills:
            return None
        raise FxRateMissingError(
            f"{len(fills)} FX fill(s) settle outside USD but the run had no FxRateFeeder, so "
            "the USD balance never moved; set the venue up with setup_backtest_venues(data=...)"
        )
    if book.misses:
        shown = "; ".join(book.misses[:3])
        raise FxRateMissingError(
            f"{len(book.misses)} FX fill(s) had no USD conversion rate, so the USD balance did "
            f"not move for them (Nautilus: 'Cannot calculate account state'): {shown}"
        )
    return book


def _usd_realised(position, book: FxRateBook) -> float:
    """``position``'s realised P&L in USD, each reducing fill converted at its own rate.

    The account converts every reducing fill's quote P&L at that fill's rate, so the position
    is replayed (average cost, as Nautilus' ``Position``) and the USD amounts summed. No single
    rate could stand in: parts closed at 151 and 149 net 0 JPY but not 0 USD.
    """
    quantity = average = usd = 0.0
    multiplier = position.multiplier.as_double()
    for fill in position.events:
        rate = book.fill_rate(fill.trade_id) or 0.0  # a missing rate already failed the run
        signed = fill.last_qty.as_double() * (1.0 if fill.order_side == OrderSide.BUY else -1.0)
        price = fill.last_px.as_double()
        if quantity == 0.0 or (quantity > 0) == (signed > 0):
            average = (quantity * average + signed * price) / (quantity + signed)
            quantity += signed
            continue
        closed = math.copysign(min(abs(signed), abs(quantity)), quantity)
        usd += closed * (price - average) * multiplier * rate
        quantity -= closed
    return usd


def usd_realised_pnls(engine: BacktestEngine) -> dict[str, Decimal]:
    """Each converting position's realised P&L in USD, by position id (as the account booked).

    Raises:
        FxRateMissingError: as :func:`require_fx_rates`.
    """
    book = require_fx_rates(engine)
    if book is None:
        return {}
    return {
        position.id.value: Decimal(str(_usd_realised(position, book)))
        for position in engine.cache.positions()
        if needs_conversion(engine.cache.instrument(position.instrument_id))
    }


def usd_pnl(position, usd_pnls: dict[str, Decimal]) -> float:
    """``position``'s realised P&L in USD: its converted amount when ``usd_pnls`` has one.

    Without one (every equity and futures position) this is the raw ``realized_pnl`` as
    before; :func:`settle_fx_results` has already failed any FX fill that had no rate.
    """
    pnl = getattr(position, "realized_pnl", None)
    usd = usd_pnls.get(position.id.value) if usd_pnls else None
    if usd is not None and (pnl is None or pnl.currency != ACCOUNT_CURRENCY):
        return float(usd)
    return pnl.as_double() if pnl else 0.0


def settle_fx_results(engine: BacktestEngine) -> dict[str, Decimal]:
    """Fail on a missing rate, then give the analyzer every FX trade's P&L in USD.

    Nautilus' analyzer keys realised P&L by the position's settlement currency, so a USDJPY
    trade would be missing from every USD statistic. Each converted trade is added in USD
    (``add_trade`` replaces by position id, so calling this twice is harmless). Returns
    :func:`usd_realised_pnls`; a run with no conversion returns ``{}`` and touches nothing.
    """
    usd_pnls = usd_realised_pnls(engine)
    analyzer = engine.portfolio.analyzer
    for position in engine.cache.positions_closed():
        if position.id.value in usd_pnls:
            usd = Money(usd_pnl(position, usd_pnls), ACCOUNT_CURRENCY)
            analyzer.add_trade(position.id, usd)
    return usd_pnls
