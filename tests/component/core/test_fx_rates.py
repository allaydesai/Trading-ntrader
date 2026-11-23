"""FX M3 (task 3.1): the rate book behind ``FxRateFeeder`` and conversion-pair resolution.

Real Nautilus ``Cache``, instruments and bars; fills are stand-ins carrying only the fields
the book reads. The feeder inside a running engine (ordering, the missing-rate failure, no
quotes reaching strategies) is in ``tests/integration/core/test_fx_conversion_engine.py``.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.currencies import GBP, JPY, USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import TradeId, Venue
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.fx_rates import (
    FxConversionPairError,
    FxRateBook,
    conversion_pair,
    conversion_pairs_for,
    fx_quote_rates,
    fx_ticker,
    is_fx,
    needs_conversion,
)
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

IDEALPRO = Venue("IDEALPRO")
TS = 1_741_273_200_000_000_000  # 2025-03-06 15:00 UTC


def _pair(name: str):
    return build_fx_pair(get_pair_spec(name))


EURUSD, GBPUSD, USDJPY, EURGBP = (_pair(n) for n in ("EURUSD", "GBPUSD", "USDJPY", "EURGBP"))


def _bar(instrument, close: str, ts: int = TS) -> Bar:
    px = instrument.make_price(Decimal(close))
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-MID-EXTERNAL")
    return Bar(bar_type, px, px, px, px, Quantity.from_int(1_000), ts, ts)


def _book(*instruments) -> tuple[FxRateBook, Cache]:
    cache = Cache()
    for instrument in instruments:
        cache.add_instrument(instrument)
    return FxRateBook(cache), cache


def _fill(instrument, trade_id: str = "T-1", side=OrderSide.SELL, ts: int = TS):
    return SimpleNamespace(
        instrument_id=instrument.id, order_side=side, trade_id=TradeId(trade_id), ts_event=ts
    )


# -- classification and conversion-pair resolution --------------------------------------------


def test_spot_fx_pairs_are_fx_and_crypto_pairs_are_not():
    assert all(is_fx(i) for i in (EURUSD, GBPUSD, USDJPY, EURGBP))
    crypto = TestInstrumentProvider.btcusdt_binance()  # a CurrencyPair without the FX info tag
    assert not is_fx(crypto)
    assert not needs_conversion(crypto)
    assert not is_fx(TestInstrumentProvider.equity("AAPL", "XNAS"))


def test_fx_ticker_is_base_plus_quote():
    assert [fx_ticker(i) for i in (EURUSD, USDJPY, EURGBP)] == ["EURUSD", "USDJPY", "EURGBP"]


def test_a_usd_quote_needs_no_conversion_pair():
    assert not needs_conversion(EURUSD)
    assert conversion_pair(EURUSD) is None
    assert conversion_pair(GBPUSD) is None


def test_a_usd_base_pair_is_its_own_rate_source():
    assert needs_conversion(USDJPY)
    assert conversion_pair(USDJPY) == "USDJPY"


def test_a_cross_converts_through_its_quote_against_usd():
    assert needs_conversion(EURGBP)
    assert conversion_pair(EURGBP) == "GBPUSD"


def test_a_cross_without_a_usd_pair_for_its_quote_raises_naming_it():
    with pytest.raises(FxConversionPairError, match="GBPUSD or USDGBP") as err:
        conversion_pair(EURGBP, known=frozenset({"EURUSD", "USDJPY"}))
    assert "EUR/GBP.IDEALPRO" in str(err.value)


def test_conversion_pairs_for_lists_only_pairs_the_run_does_not_trade():
    assert conversion_pairs_for([EURGBP, USDJPY, EURUSD]) == ["GBPUSD"]
    assert conversion_pairs_for([EURGBP, GBPUSD]) == []  # GBPUSD is traded: its bars are in
    assert conversion_pairs_for([USDJPY]) == []  # its own bars are the rate source
    assert conversion_pairs_for([EURUSD, GBPUSD]) == []


# -- the rate book ------------------------------------------------------------------------------


def test_a_usd_base_bar_writes_a_cache_quote_a_mark_xrate_and_the_history():
    book, cache = _book(USDJPY)
    book.on_bar(_bar(USDJPY, "150.000"))

    quote = cache.quote_tick(USDJPY.id)
    assert quote is not None
    assert quote.bid_price == quote.ask_price == USDJPY.make_price(150)
    assert cache.get_xrate(IDEALPRO, JPY, USD) == pytest.approx(1 / 150)
    assert cache.get_mark_xrate(USD, JPY) == pytest.approx(150.0)
    assert book.rate_history(JPY) == [(TS, pytest.approx(1 / 150))]


def test_a_usd_quote_bar_feeds_its_base_rate_for_crosses():
    book, cache = _book(GBPUSD, EURGBP)
    book.on_bar(_bar(GBPUSD, "1.25000"))
    assert cache.get_xrate(IDEALPRO, GBP, USD) == pytest.approx(1.25)
    assert book.rate_history(GBP) == [(TS, pytest.approx(1.25))]
    book.on_bar(_bar(GBPUSD, "1.30000", TS + 1))
    assert cache.get_xrate(IDEALPRO, GBP, USD) == pytest.approx(1.30)  # latest bar wins
    assert [ts for ts, _ in book.rate_history(GBP)] == [TS, TS + 1]


def test_a_fill_with_a_rate_records_the_rate_the_account_converted_at():
    book, _ = _book(USDJPY)
    book.on_bar(_bar(USDJPY, "151.000"))
    book.on_fill(_fill(USDJPY, "T-9"))
    assert book.fill_rate(TradeId("T-9")) == pytest.approx(1 / 151)
    assert book.misses == []


def test_a_fill_without_a_rate_is_recorded_as_a_miss():
    book, _ = _book(USDJPY)
    book.on_fill(_fill(USDJPY, "T-1"))
    assert book.fill_rate(TradeId("T-1")) is None
    assert len(book.misses) == 1
    assert "USD/JPY.IDEALPRO" in book.misses[0]
    assert "JPY/USD" in book.misses[0]


def test_a_cross_fill_without_its_conversion_bars_is_a_miss():
    book, _ = _book(EURGBP, GBPUSD)
    book.on_bar(_bar(EURGBP, "0.85000"))  # the traded pair alone gives no GBP/USD rate
    book.on_fill(_fill(EURGBP))
    assert "GBP/USD" in book.misses[0]


def test_a_usd_quote_fill_needs_no_rate_and_records_nothing():
    book, _ = _book(EURUSD)
    book.on_fill(_fill(EURUSD))
    assert book.misses == []
    assert book.fill_rate(TradeId("T-1")) is None


# -- the rates table the sizing helpers take (FX M6) -----------------------------------------


def test_fx_quote_rates_is_closed_form_for_usd_quote_and_usd_base():
    _, cache = _book(EURUSD, USDJPY)
    assert fx_quote_rates(EURUSD, Decimal("1.08"), cache) == {"USD": Decimal(1)}
    assert fx_quote_rates(USDJPY, Decimal("160"), cache) == {"JPY": Decimal("0.00625")}


def test_fx_quote_rates_reads_a_cross_rate_from_the_cache_and_is_none_before_it():
    book, cache = _book(GBPUSD, EURGBP)
    assert fx_quote_rates(EURGBP, Decimal("0.85"), cache) is None
    book.on_bar(_bar(GBPUSD, "1.25000"))
    rates = fx_quote_rates(EURGBP, Decimal("0.85"), cache)
    assert rates is not None and float(rates["GBP"]) == pytest.approx(1.25)
