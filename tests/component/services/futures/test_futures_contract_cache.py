"""Futures M1: spec-built contracts are accepted by a real Nautilus ``Cache``.

The engine's ``add_instrument`` stores the instrument in its cache; this exercises that
seam (and the bar/instrument identity check) without building a ``BacktestEngine``,
which initialises Nautilus logging and belongs in the ``--forked`` integration tier
(``tests/integration/services/test_futures_contract_engine.py``).
"""

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Price, Quantity

from src.services.futures.contract_specs import load_contract_specs
from src.services.futures.instrument_builder import build_futures_contract

pytestmark = pytest.mark.component

ROOTS = sorted(load_contract_specs().roots)


@pytest.mark.parametrize("root", ROOTS)
def test_cache_round_trips_contract(root):
    spec = load_contract_specs().roots[root]
    contract = build_futures_contract(spec)
    cache = Cache()

    cache.add_instrument(contract)

    stored = cache.instrument(contract.id)
    assert isinstance(stored, FuturesContract)
    assert stored == contract
    assert stored.multiplier == contract.multiplier
    assert contract.id in cache.instrument_ids()


def test_cache_holds_all_roots_side_by_side():
    cache = Cache()
    for spec in load_contract_specs().roots.values():
        cache.add_instrument(build_futures_contract(spec))
    assert {str(i) for i in cache.instrument_ids()} == {
        "ES.CME",
        "NQ.CME",
        "CL.NYMEX",
        "GC.COMEX",
        "ZN.CBOT",
    }


def test_contract_prices_bars_and_notional():
    """make_price / notional use the contract's precision and multiplier."""
    contract = build_futures_contract(load_contract_specs().roots["ES"])
    bar_type = BarType.from_str("ES.CME-1-HOUR-LAST-EXTERNAL")
    px = contract.make_price(5922.58)
    bar = Bar(bar_type, px, px, px, px, Quantity.from_int(1), 0, 0)

    assert bar.close == Price.from_str("5922.58")
    notional = contract.notional_value(Quantity.from_int(2), bar.close)
    assert notional.as_decimal() == 2 * 50 * Price.from_str("5922.58").as_decimal()
