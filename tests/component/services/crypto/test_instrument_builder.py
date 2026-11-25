"""Spot crypto ``CurrencyPair`` construction (crypto M1, task 1.5)."""

from decimal import Decimal

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from src.core.fx_rates import is_fx, needs_conversion
from src.core.position_sizing import is_crypto, is_fx_pair
from src.services.crypto.coin_specs import get_coin_spec, get_fee_schedule
from src.services.crypto.instrument_builder import build_crypto_pair, crypto_instrument_id
from src.services.kraken_client import KrakenPairMapper

pytestmark = pytest.mark.component

CASES = {
    # coin: (instrument id, raw symbol)
    "BTC": ("BTC/USD.KRAKEN", "XBTUSD"),
    "ETH": ("ETH/USD.KRAKEN", "ETHUSD"),
    "SOL": ("SOL/USD.KRAKEN", "SOLUSD"),
}


@pytest.mark.parametrize("coin", sorted(CASES))
def test_builds_valid_currency_pair(coin):
    inst_id, raw = CASES[coin]

    instrument = build_crypto_pair(get_coin_spec(coin))

    assert isinstance(instrument, CurrencyPair)
    assert str(instrument.id) == inst_id
    assert instrument.raw_symbol.value == raw
    assert instrument.base_currency.code == coin
    assert instrument.quote_currency.code == "USD"
    assert instrument.asset_class == AssetClass.CRYPTOCURRENCY
    assert instrument.price_precision == 4
    assert instrument.price_increment == Price.from_str("0.0001")
    assert instrument.size_precision == 8
    assert instrument.size_increment == Quantity.from_str("0.00000001")
    assert instrument.margin_init == Decimal(1)
    assert instrument.margin_maint == Decimal(1)
    assert instrument.maker_fee == instrument.taker_fee == Decimal(0)


@pytest.mark.parametrize("coin", sorted(CASES))
def test_id_is_the_kraken_mapper_id(coin):
    spec = get_coin_spec(coin)
    expected = KrakenPairMapper.to_nautilus_id(spec.kraken.pair)
    assert str(crypto_instrument_id(spec)) == expected
    assert str(build_crypto_pair(spec).id) == expected
    assert spec.kraken.rest == KrakenPairMapper.to_kraken_rest(spec.kraken.pair)


def test_predicates_see_crypto_not_fx():
    instrument = build_crypto_pair(get_coin_spec("BTC"))
    assert is_crypto(instrument)
    assert not is_fx(instrument)
    assert not is_fx_pair(instrument)
    assert not needs_conversion(instrument)


def test_bar_type_round_trips_with_last_price():
    instrument = build_crypto_pair(get_coin_spec("BTC"))
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-LAST-EXTERNAL")
    assert bar_type.instrument_id == instrument.id
    assert str(bar_type) == "BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL"


@pytest.mark.parametrize(("precision", "increment"), [(2, "0.01"), (5, "0.00001"), (0, "1")])
def test_precision_override_follows_bars(precision, increment):
    instrument = build_crypto_pair(get_coin_spec("ETH"), price_precision=precision)
    assert instrument.price_precision == precision
    assert instrument.price_increment == Price.from_str(increment)


def test_negative_precision_is_rejected():
    with pytest.raises(ValueError, match="price_precision"):
        build_crypto_pair(get_coin_spec("BTC"), price_precision=-1)


def test_matching_catalog_id_is_accepted():
    instrument = build_crypto_pair(get_coin_spec("SOL"), nautilus_id="SOL/USD.KRAKEN")
    assert instrument.id == InstrumentId.from_str("SOL/USD.KRAKEN")


@pytest.mark.parametrize(
    "bad_id", ["BTCUSD.KRAKEN", "BTC/USD.PAXOS", "XBT/USD.KRAKEN", "BTC/USDT.KRAKEN"]
)
def test_mismatched_catalog_id_is_rejected(bad_id):
    with pytest.raises(ValueError, match="does not match the BTC spec"):
        build_crypto_pair(get_coin_spec("BTC"), nautilus_id=bad_id)


def test_info_records_the_terms():
    spec = get_coin_spec("BTC")
    schedule = get_fee_schedule()

    info = build_crypto_pair(spec).info

    assert info["asset_class"] == "CRYPTO"
    assert info["typical_spread_bps"] == "2"
    assert info["kraken"] == {
        "pair": "BTC/USD",
        "rest": "XXBTZUSD",
        "altname": "XBTUSD",
        "min_order_base": "0.00006",
        "min_order_usd": "0.5",
        "tick_size": "0.1",
        "discounted_pair": False,
    }
    assert info["fee_schedule"] == {
        "venue": "KRAKEN",
        "source": schedule.source,
        "as_of": "2026-11-24",
        "default_tier": 2,
    }
    assert set(info["unverified"]) == set(spec.unverified_fields())
    assert info["unverified"] == ["typical_spread_bps", "kraken_discounted_pair"]
    assert "tier" not in info  # the tier is a run parameter, not an instrument property


def test_info_is_json_serialisable():
    import json

    json.dumps(build_crypto_pair(get_coin_spec("SOL")).info)


def test_make_price_and_qty_follow_the_grid():
    instrument = build_crypto_pair(get_coin_spec("BTC"))
    assert str(instrument.make_price(78123.45678)) == "78123.4568"
    assert str(instrument.make_qty(Decimal("0.128"))) == "0.12800000"
    assert str(instrument.make_qty(Decimal("0.123456789"))) == "0.12345679"
