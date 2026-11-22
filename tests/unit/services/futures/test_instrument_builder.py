"""Continuous futures instrument construction (futures M1)."""

from decimal import Decimal

import pandas as pd
import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Price, Quantity

from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import (
    CONTINUOUS_ACTIVATION_NS,
    CONTINUOUS_EXPIRATION_NS,
    build_futures_contract,
    continuous_instrument_id,
)

pytestmark = pytest.mark.unit

CASES = {
    # root: (instrument id, nautilus asset class, multiplier, increment)
    "ES": ("ES.CME", AssetClass.INDEX, 50, "0.01"),
    "NQ": ("NQ.CME", AssetClass.INDEX, 20, "0.01"),
    "CL": ("CL.NYMEX", AssetClass.COMMODITY, 1000, "0.001"),
    "GC": ("GC.COMEX", AssetClass.COMMODITY, 100, "0.1"),
    "ZN": ("ZN.CBOT", AssetClass.DEBT, 1000, "0.000001"),
}


@pytest.mark.parametrize("root", sorted(CASES))
def test_builds_valid_continuous_contract(root):
    inst_id, asset_class, mult, increment = CASES[root]
    spec = get_contract_spec(root)

    contract = build_futures_contract(spec)

    assert isinstance(contract, FuturesContract)
    assert str(contract.id) == inst_id
    assert contract.raw_symbol.value == root
    assert contract.asset_class == asset_class
    assert contract.multiplier == Quantity.from_int(mult)
    assert contract.price_increment == Price.from_str(increment)
    assert contract.price_precision == spec.continuous_price_precision
    assert contract.quote_currency.code == "USD"
    assert contract.underlying == spec.underlying
    assert contract.lot_size == Quantity.from_int(1)
    assert contract.size_precision == 0
    assert contract.activation_ns == CONTINUOUS_ACTIVATION_NS
    assert contract.expiration_ns == CONTINUOUS_EXPIRATION_NS


def test_continuous_window_never_expires_on_history():
    assert CONTINUOUS_ACTIVATION_NS == 0
    assert CONTINUOUS_EXPIRATION_NS == pd.Timestamp("2100-01-01", tz="UTC").value


def test_info_records_kind_and_dollar_economics():
    spec = get_contract_spec("ES")
    info = build_futures_contract(spec).info
    assert info["contract_kind"] == "continuous"
    assert info["adjustment"] == "ratio"
    assert info["tick_size"] == "0.25"
    assert info["margin_initial_usd"] == str(spec.margin.initial_usd)
    assert info["margin_maintenance_usd"] == str(spec.margin.maintenance_usd)
    assert info["margin_as_of"] == spec.margin.as_of.isoformat()
    assert info["commission_per_contract_usd"] == str(spec.commission_per_contract_usd)
    assert info["ib"]["sec_type"] == "FUT"
    assert info["ib"]["trading_class"] == "ES"


def test_continuous_instrument_id():
    assert str(continuous_instrument_id(get_contract_spec("GC"))) == "GC.COMEX"


def test_margins_unset_without_reference_price():
    contract = build_futures_contract(get_contract_spec("ES"))
    assert contract.margin_init == Decimal(0)
    assert contract.margin_maint == Decimal(0)


def test_margins_converted_with_reference_price():
    spec = get_contract_spec("ES")
    contract = build_futures_contract(spec, reference_price=Decimal("5000"))
    notional = Decimal("5000") * spec.multiplier
    assert contract.margin_init == pytest.approx(spec.margin.initial_usd / notional)
    assert contract.margin_maint == pytest.approx(spec.margin.maintenance_usd / notional)


def test_non_positive_reference_price_rejected():
    with pytest.raises(ValueError, match="reference_price"):
        build_futures_contract(get_contract_spec("ES"), reference_price=Decimal("0"))


def test_precision_override_sets_increment():
    contract = build_futures_contract(get_contract_spec("ES"), price_precision=4)
    assert contract.price_precision == 4
    assert contract.price_increment == Price.from_str("0.0001")


def test_negative_precision_rejected():
    with pytest.raises(ValueError, match="price_precision"):
        build_futures_contract(get_contract_spec("ES"), price_precision=-1)


def test_explicit_matching_nautilus_id_accepted():
    contract = build_futures_contract(get_contract_spec("CL"), nautilus_id="CL.NYMEX")
    assert str(contract.id) == "CL.NYMEX"


@pytest.mark.parametrize("bad_id", ["CL.CME", "NQ.NYMEX"])
def test_nautilus_id_disagreeing_with_spec_rejected(bad_id):
    with pytest.raises(ValueError, match="does not match"):
        build_futures_contract(get_contract_spec("CL"), nautilus_id=bad_id)


def test_bar_at_contract_precision_is_accepted():
    """A bar priced at the contract's precision matches it (what the engine checks)."""
    contract = build_futures_contract(get_contract_spec("ZN"))
    bar_type = BarType.from_str("ZN.CBOT-1-HOUR-LAST-EXTERNAL")
    px = Price.from_str("82.708329")
    bar = Bar(bar_type, px, px, px, px, Quantity.from_int(837), 0, 0)
    assert bar.open.precision == contract.price_precision
    assert bar.bar_type.instrument_id == contract.id
