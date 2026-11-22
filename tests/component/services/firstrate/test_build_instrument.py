"""``build_instrument`` dispatches on asset class; equities are unchanged (futures M1, FX M1)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import CurrencyPair, Equity, FuturesContract
from nautilus_trader.model.objects import Price, Quantity

from src.services.firstrate.backtest_loader import (
    build_equity,
    build_instrument,
    load_from_catalog,
)
from src.services.futures.contract_specs import FuturesSpecError, get_contract_spec
from src.services.futures.instrument_builder import build_futures_contract
from src.services.fx.pair_specs import FxSpecError

pytestmark = pytest.mark.component


def _bars(nautilus_id: str, price: str, count: int = 2, price_type: str = "LAST") -> list[Bar]:
    bar_type = BarType.from_str(f"{nautilus_id}-1-HOUR-{price_type}-EXTERNAL")
    p = Price.from_str(price)
    ts = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    return [Bar(bar_type, p, p, p, p, Quantity.from_int(10), ts + i, ts + i) for i in range(count)]


@pytest.mark.parametrize("asset_class", ["STOCK", "ETF", "DELISTED", None])
def test_non_futures_is_exactly_build_equity(asset_class):
    bars = _bars("AAPL.NASDAQ", "187.1234")
    got = build_instrument(
        asset_class=asset_class, nautilus_id="AAPL.NASDAQ", ticker="AAPL", bars=bars
    )
    want = build_equity(nautilus_id="AAPL.NASDAQ", ticker="AAPL", bars=bars)
    assert isinstance(got, Equity)
    assert got == want
    assert got.price_precision == want.price_precision == 4
    assert got.price_increment == want.price_increment


def test_non_futures_without_bars_matches_legacy_default():
    got = build_instrument(asset_class="ETF", nautilus_id="SPY.ARCA", ticker="SPY")
    want = build_equity(nautilus_id="SPY.ARCA", ticker="SPY")
    assert got == want
    assert got.price_precision == want.price_precision


@pytest.mark.parametrize("asset_class", ["FUTURES", "futures"])
def test_futures_builds_contract(asset_class):
    got = build_instrument(asset_class=asset_class, nautilus_id="ES.CME", ticker="ES")
    assert isinstance(got, FuturesContract)
    assert str(got.id) == "ES.CME"
    assert got.price_precision == 2


def test_futures_precision_follows_bars():
    bars = _bars("ES.CME", "5922.5")
    got = build_instrument(asset_class="FUTURES", nautilus_id="ES.CME", ticker="ES", bars=bars)
    assert got.price_precision == 1
    assert got.price_increment == Price.from_str("0.1")


def test_futures_unknown_root_raises():
    with pytest.raises(FuturesSpecError, match="Unknown futures root"):
        build_instrument(asset_class="FUTURES", nautilus_id="XX.CME", ticker="XX")


def test_futures_venue_mismatch_raises():
    with pytest.raises(ValueError, match="does not match"):
        build_instrument(asset_class="FUTURES", nautilus_id="ES.NYMEX", ticker="ES")


def test_futures_is_exactly_build_futures_contract():
    bars = _bars("ES.CME", "5922.5")
    got = build_instrument(asset_class="FUTURES", nautilus_id="ES.CME", ticker="ES", bars=bars)
    want = build_futures_contract(get_contract_spec("ES"), nautilus_id="ES.CME", price_precision=1)
    assert isinstance(got, FuturesContract)
    assert got.to_dict(got) == want.to_dict(want)


@pytest.mark.parametrize("asset_class", ["FX", "fx"])
def test_fx_builds_currency_pair(asset_class):
    bars = _bars("EUR/USD.IDEALPRO", "1.08431", price_type="MID")
    got = build_instrument(
        asset_class=asset_class, nautilus_id="EUR/USD.IDEALPRO", ticker="EURUSD", bars=bars
    )
    assert isinstance(got, CurrencyPair)
    assert str(got.id) == "EUR/USD.IDEALPRO"
    assert (got.base_currency.code, got.quote_currency.code) == ("EUR", "USD")
    assert got.price_precision == 5
    assert got.id == bars[0].bar_type.instrument_id


@pytest.mark.parametrize(("price", "precision"), [("150.123", 3), ("150.12", 2)])
def test_fx_precision_follows_bars(price, precision):
    bars = _bars("USD/JPY.IDEALPRO", price, price_type="MID")
    got = build_instrument(
        asset_class="FX", nautilus_id="USD/JPY.IDEALPRO", ticker="USDJPY", bars=bars
    )
    assert got.price_precision == precision


def test_fx_without_bars_uses_spec_precision():
    got = build_instrument(asset_class="FX", nautilus_id="USD/JPY.IDEALPRO", ticker="USDJPY")
    assert isinstance(got, CurrencyPair)
    assert got.price_precision == 3


def test_fx_unknown_pair_raises():
    with pytest.raises(FxSpecError, match="Unknown FX pair"):
        build_instrument(asset_class="FX", nautilus_id="AUD/USD.IDEALPRO", ticker="AUDUSD")


def test_fx_id_mismatch_raises():
    with pytest.raises(ValueError, match="does not match"):
        build_instrument(asset_class="FX", nautilus_id="EURUSD.IDEALPRO", ticker="EURUSD")


@pytest.mark.asyncio
async def test_load_from_catalog_builds_currency_pair_for_fx_row():
    bars = _bars("EUR/USD.IDEALPRO", "1.08431", price_type="MID")
    row = MagicMock()
    row.ticker = "EURUSD"
    row.nautilus_id = "EUR/USD.IDEALPRO"
    row.asset_class = "FX"
    catalog = MagicMock()
    catalog.bars.return_value = bars
    catalog_manager = MagicMock()
    catalog_manager.resolve_catalog.return_value = catalog
    metadata_service = MagicMock()
    metadata_service.get_instrument_sync.return_value = row

    result = await load_from_catalog(
        catalog_name="firstrate-fx",
        ticker="EURUSD",
        bar_type_spec="1-HOUR-MID",
        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 3, tzinfo=timezone.utc),
        catalog_manager=catalog_manager,
        metadata_service=metadata_service,
    )

    catalog.bars.assert_called_once()
    assert catalog.bars.call_args.kwargs["bar_types"] == ["EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL"]
    assert isinstance(result.instrument, CurrencyPair)
    assert result.instrument.id == bars[0].bar_type.instrument_id
    assert result.instrument.price_precision == 5
