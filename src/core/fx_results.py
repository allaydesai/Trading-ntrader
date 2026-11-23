"""FX M3: settling a finished FX run in USD (the missing-rate guard and the exit rates).

``FxRateFeeder`` (``src/core/fx_rates.py``) records the rate the account converted every FX
fill at, and a miss for every fill the cache had no rate for. Here a finished engine is read
back: a miss (or FX fills with no feeder at all) fails the run, because Nautilus left the USD
balance unchanged for that fill. Otherwise each position's quote-currency P&L converts at the
rate the account used for each of its reducing fills (for a single exit, the exit's rate).
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


def _effective_rate(position, book: FxRateBook) -> float | None:
    """USD per quote unit that converts ``position``'s whole realised P&L exactly.

    The account converts each reducing fill's P&L at that fill's rate, so a position closed in
    parts is replayed (average cost, as Nautilus' ``Position``) and the summed USD divided by
    the summed quote P&L. A position with no realised P&L yet uses its last fill's rate.
    """
    quantity = average = quote_pnl = usd = 0.0
    last_rate = None
    multiplier = position.multiplier.as_double()
    for fill in position.events:
        rate = book.fill_rate(fill.trade_id)
        if rate is None:
            continue
        last_rate = rate
        signed = fill.last_qty.as_double() * (1.0 if fill.order_side == OrderSide.BUY else -1.0)
        price = fill.last_px.as_double()
        if quantity == 0.0 or (quantity > 0) == (signed > 0):
            average = (quantity * average + signed * price) / (quantity + signed)
            quantity += signed
            continue
        closed = math.copysign(min(abs(signed), abs(quantity)), quantity)
        pnl = closed * (price - average) * multiplier
        quote_pnl += pnl
        usd += pnl * rate
        quantity -= closed
    if abs(quote_pnl) < 1e-9:
        return last_rate
    return usd / quote_pnl


def exit_rates(engine: BacktestEngine) -> dict[str, Decimal]:
    """USD per quote unit converting each converting position's realised P&L, by position id.

    For a single exit this is the exit fill's rate; for a position closed in parts, the
    P&L-weighted rate of its reducing fills, so ``P&L x rate`` equals the account's USD.

    Raises:
        FxRateMissingError: as :func:`require_fx_rates`.
    """
    book = require_fx_rates(engine)
    if book is None:
        return {}
    rates: dict[str, Decimal] = {}
    for position in engine.cache.positions():
        rate = _effective_rate(position, book)
        if rate is not None:
            rates[position.id.value] = Decimal(str(rate))
    return rates


def usd_pnl(position, rates: dict[str, Decimal]) -> float:
    """``position``'s realised P&L in USD: at its exit rate when ``rates`` has one.

    Without a rate (every equity and futures position) this is the raw ``realized_pnl`` as
    before; :func:`settle_fx_results` has already failed any FX fill that had no rate.
    """
    pnl = getattr(position, "realized_pnl", None)
    if not pnl:
        return 0.0
    rate = rates.get(position.id.value) if rates else None
    if rate is None or pnl.currency == ACCOUNT_CURRENCY:
        return pnl.as_double()
    return pnl.as_double() * float(rate)


def settle_fx_results(engine: BacktestEngine) -> dict[str, Decimal]:
    """Fail on a missing rate, then give the analyzer every FX trade's P&L in USD.

    Nautilus' analyzer keys realised P&L by the position's settlement currency, so a USDJPY
    trade would be missing from every USD statistic. Each converted trade is added in USD
    (``add_trade`` replaces by position id, so calling this twice is harmless). Returns
    :func:`exit_rates`; a run with no conversion returns ``{}`` and touches nothing.
    """
    rates = exit_rates(engine)
    analyzer = engine.portfolio.analyzer
    for position in engine.cache.positions_closed():
        if position.id.value in rates:
            usd = Money(usd_pnl(position, rates), ACCOUNT_CURRENCY)
            analyzer.add_trade(position.id, usd)
    return rates
