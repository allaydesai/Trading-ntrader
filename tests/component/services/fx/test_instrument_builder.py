"""Spot FX ``CurrencyPair`` construction (FX M1, task 1.3)."""

from decimal import Decimal

import pytest
from nautilus_trader.adapters.interactive_brokers.parsing.instruments import (
    RE_CASH,
    VENUES_CASH,
    instrument_id_to_ib_contract,
)
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from src.services.fx.instrument_builder import build_fx_pair, fx_instrument_id
from src.services.fx.pair_specs import get_commission_spec, get_pair_spec

pytestmark = pytest.mark.component

CASES = {
    # pair: (instrument id, raw symbol, base, quote, precision, increment)
    "EURUSD": ("EUR/USD.IDEALPRO", "EUR.USD", "EUR", "USD", 5, "0.00001"),
    "GBPUSD": ("GBP/USD.IDEALPRO", "GBP.USD", "GBP", "USD", 5, "0.00001"),
    "USDJPY": ("USD/JPY.IDEALPRO", "USD.JPY", "USD", "JPY", 3, "0.001"),
    "EURGBP": ("EUR/GBP.IDEALPRO", "EUR.GBP", "EUR", "GBP", 5, "0.00001"),
}


@pytest.mark.parametrize("pair", sorted(CASES))
def test_builds_valid_currency_pair(pair):
    inst_id, raw, base, quote, precision, increment = CASES[pair]

    instrument = build_fx_pair(get_pair_spec(pair))

    assert isinstance(instrument, CurrencyPair)
    assert str(instrument.id) == inst_id
    assert instrument.raw_symbol.value == raw
    assert instrument.base_currency.code == base
    assert instrument.quote_currency.code == quote
    assert instrument.asset_class == AssetClass.FX
    assert instrument.price_precision == precision
    assert instrument.price_increment == Price.from_str(increment)
    assert instrument.size_precision == 0
    assert instrument.size_increment == Quantity.from_int(1)
    assert instrument.margin_init == Decimal("0.03")
    assert instrument.margin_maint == Decimal("0.03")
    assert instrument.maker_fee == instrument.taker_fee == Decimal(0)


@pytest.mark.parametrize("pair", sorted(CASES))
def test_id_is_ib_simplified_cash_symbology(pair):
    instrument_id = fx_instrument_id(get_pair_spec(pair))
    spec = get_pair_spec(pair)

    match = RE_CASH.match(instrument_id.symbol.value)
    assert match is not None
    assert (match["symbol"], match["currency"]) == (spec.base, spec.quote)
    assert instrument_id.venue.value in VENUES_CASH

    contract = instrument_id_to_ib_contract(instrument_id, exchange="IDEALPRO")
    assert contract.secType == spec.ib.sec_type
    assert contract.exchange == spec.ib.exchange
    assert contract.localSymbol == spec.ib.local_symbol


def test_bar_type_round_trips_with_mid_price():
    instrument = build_fx_pair(get_pair_spec("EURUSD"))
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-MID-EXTERNAL")
    assert bar_type.instrument_id == instrument.id


@pytest.mark.parametrize(
    ("pair", "precision", "increment"), [("EURUSD", 4, "0.0001"), ("USDJPY", 2, "0.01")]
)
def test_precision_override_follows_bars(pair, precision, increment):
    instrument = build_fx_pair(get_pair_spec(pair), price_precision=precision)
    assert instrument.price_precision == precision
    assert instrument.price_increment == Price.from_str(increment)


def test_negative_precision_is_rejected():
    with pytest.raises(ValueError, match="price_precision"):
        build_fx_pair(get_pair_spec("EURUSD"), price_precision=-1)


def test_matching_catalog_id_is_accepted():
    instrument = build_fx_pair(get_pair_spec("USDJPY"), nautilus_id="USD/JPY.IDEALPRO")
    assert instrument.id == InstrumentId.from_str("USD/JPY.IDEALPRO")


@pytest.mark.parametrize("bad_id", ["EURUSD.IDEALPRO", "EUR/USD.SIM", "USD/EUR.IDEALPRO"])
def test_mismatched_catalog_id_is_rejected(bad_id):
    with pytest.raises(ValueError, match="does not match the EURUSD spec"):
        build_fx_pair(get_pair_spec("EURUSD"), nautilus_id=bad_id)


def test_info_records_the_terms():
    spec = get_pair_spec("USDJPY")
    commission = get_commission_spec()

    info = build_fx_pair(spec).info

    assert info["asset_class"] == "FX"
    assert info["pip_size"] == "0.01"
    assert info["min_tick"] == "0.005"
    assert info["min_order_usd"] == "25000"
    assert info["typical_spread_pips"] == "0.5"
    assert info["margin_pct"] == "3.0"
    assert info["maint_margin_pct"] == "3.0"
    assert info["margin_as_of"] == "2026-11-22"
    assert info["commission"] == {
        "rate_bps": str(commission.rate_bps),
        "min_per_order_usd": str(commission.min_per_order_usd),
        "as_of": commission.as_of.isoformat(),
    }
    assert info["ib"] == spec.ib.model_dump()
    assert set(info["unverified"]) == set(spec.unverified_fields())
    assert "margin_pct" in info["unverified"]


def test_make_price_and_qty_follow_the_grid():
    instrument = build_fx_pair(get_pair_spec("EURUSD"))
    assert str(instrument.make_price(1.084314)) == "1.08431"
    assert str(instrument.make_qty(100_000)) == "100000"
