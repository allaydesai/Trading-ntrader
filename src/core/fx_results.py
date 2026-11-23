"""FX M3: settling a finished FX run in USD (the missing-rate guard and the exit rates).

``FxRateFeeder`` (``src/core/fx_rates.py``) records the rate the account converted every FX
fill at, and a miss for every fill the cache had no rate for. Here a finished engine is read
back: a miss (or FX fills with no feeder at all) fails the run, because Nautilus left the USD
balance unchanged for that fill. Otherwise each position's quote-currency P&L converts at the
rate of its last fill (its exit for a closed position), which is the rate the account itself
used for a single-exit position.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
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


def exit_rates(engine: BacktestEngine) -> dict[str, Decimal]:
    """USD per quote unit at each converting position's last fill, by position id.

    Raises:
        FxRateMissingError: as :func:`require_fx_rates`.
    """
    book = require_fx_rates(engine)
    if book is None:
        return {}
    rates: dict[str, Decimal] = {}
    for position in engine.cache.positions():
        rate = book.fill_rate(position.last_trade_id)
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
