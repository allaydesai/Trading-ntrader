"""``build_instrument`` dispatches on asset class; equities are unchanged (futures M1)."""

from datetime import datetime, timezone

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import Equity, FuturesContract
from nautilus_trader.model.objects import Price, Quantity

from src.services.firstrate.backtest_loader import build_equity, build_instrument
from src.services.futures.contract_specs import FuturesSpecError

pytestmark = pytest.mark.component


def _bars(nautilus_id: str, price: str, count: int = 2) -> list[Bar]:
    bar_type = BarType.from_str(f"{nautilus_id}-1-HOUR-LAST-EXTERNAL")
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
