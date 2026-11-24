"""Crypto M4 (task 4.3): ``sma_crossover`` and ``buy_and_hold`` size spot crypto fractionally.

Component tier: importing the strategies loads Nautilus. Sizing methods are called unbound on
a duck-typed ``self`` (as ``test_fx_strategy_sizing.py`` does) with real spec-built
``CurrencyPair``s. Before M4 a USD 10,000 BTC budget at 78,000 was sized through a float
format (rounded, not floored) and buy-and-hold floored to whole BTC; both now floor to the
coin's 1e-8 increment and drop an order under Kraken's minimum.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.benchmarks.buy_and_hold import BuyAndHold
from src.core.strategies.sma_crossover import SMACrossover
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair

pytestmark = pytest.mark.component

BTC = build_crypto_pair(get_coin_spec("BTC"), price_precision=4)
ETH = build_crypto_pair(get_coin_spec("ETH"), price_precision=4)
AAPL = TestInstrumentProvider.equity("AAPL", "XNAS")


def _cache() -> Cache:
    cache = Cache()
    for instrument in (BTC, ETH, AAPL):
        cache.add_instrument(instrument)
    return cache


def _sma_self(instrument, close: float, pct: str = "10", value: str = "100000", units=None):
    return SimpleNamespace(
        _current_bar=SimpleNamespace(close=close),
        portfolio_value=Decimal(value),
        position_size_pct=Decimal(pct),
        instrument_id=instrument.id,
        cache=_cache(),
        log=MagicMock(),
        config=SimpleNamespace(contracts=None, units=units),
    )


class TestSmaCrossover:
    def test_usd_10k_of_btc_at_78k_is_0_12820512_not_1(self):
        sizing = _sma_self(BTC, 78000.0)
        quantity = SMACrossover._calculate_position_size(sizing)
        assert quantity == Quantity.from_str("0.12820512")
        assert quantity.precision == 8
        assert "~$10,000 notional" in sizing.log.info.call_args.args[0]

    def test_never_exceeds_the_budget(self):
        quantity = SMACrossover._calculate_position_size(_sma_self(BTC, 91234.5678))
        assert quantity.as_decimal() * Decimal("91234.5678") <= Decimal("10000")

    def test_a_budget_under_the_kraken_minimum_places_no_order_and_warns_once(self):
        sizing = _sma_self(BTC, 78000.0, pct="0.003")  # USD 3: 0.00003846 BTC < 0.00005
        assert SMACrossover._calculate_position_size(sizing) is None
        assert SMACrossover._calculate_position_size(sizing) is None
        sizing.log.warning.assert_called_once()
        assert "minimum" in sizing.log.warning.call_args.args[0]
        assert sizing._unsized_orders == 2

    def test_fixed_decimal_units_replace_the_budget(self):
        """``--units 0.25``: exactly 0.25 BTC per entry, through ``make_qty`` (8 dp)."""
        sizing = _sma_self(BTC, 78000.0, units=Decimal("0.25"))
        quantity = SMACrossover._calculate_position_size(sizing)
        assert quantity == Quantity.from_str("0.25000000") and quantity.precision == 8
        assert "~$19,500 notional" in sizing.log.info.call_args.args[0]

    def test_fixed_units_from_a_config_file_float(self):
        sizing = _sma_self(BTC, 78000.0, units=0.1)
        assert SMACrossover._calculate_position_size(sizing) == Quantity.from_str("0.1")

    def test_fixed_units_off_the_increment_are_refused_not_rounded(self):
        with pytest.raises(ValueError, match="multiple of 0.00000001"):
            SMACrossover._calculate_position_size(
                _sma_self(BTC, 78000.0, units=Decimal("0.000000015"))
            )

    def test_the_drop_count_is_reported_once_on_stop(self):
        from src.core.strategies.sma_crossover import _report_unsized

        quiet = SimpleNamespace(log=MagicMock())
        _report_unsized(quiet)
        quiet.log.warning.assert_not_called()
        dropped = SimpleNamespace(log=MagicMock(), _unsized_orders=3)
        _report_unsized(dropped)
        assert "3 order(s) dropped" in dropped.log.warning.call_args.args[0]

    def test_equity_sizing_is_unchanged(self):
        sizing = _sma_self(AAPL, 140.0, value="1000000")
        assert SMACrossover._calculate_position_size(sizing) == Quantity.from_int(714)

    def test_units_on_an_equity_is_still_refused(self):
        with pytest.raises(ValueError, match="units applies to FX"):
            SMACrossover._calculate_position_size(_sma_self(AAPL, 140.0, units=10))


def _buy_and_hold_self(instrument, cash: str, pct: str = "100"):
    account = SimpleNamespace(
        base_currency=USD,
        balance_total=lambda currency: Money(cash, currency) if currency == USD else None,
    )
    return SimpleNamespace(
        instrument=instrument,
        instrument_id=instrument.id,
        cache=_cache(),
        portfolio=SimpleNamespace(account=lambda venue: account),
        config=SimpleNamespace(allocation_pct=Decimal(pct)),
    )


class TestBuyAndHold:
    def test_all_cash_buys_fractional_btc(self):
        sizing = _buy_and_hold_self(BTC, "100000")
        quantity = BuyAndHold._entry_quantity(sizing, Decimal("78000"))
        assert quantity == Decimal("1.28205128")
        assert BTC.make_qty(quantity) == Quantity.from_str("1.28205128")
        assert quantity * Decimal("78000") <= Decimal("100000")

    def test_under_the_minimum_holds_cash(self):
        sizing = _buy_and_hold_self(BTC, "3")
        assert BuyAndHold._entry_quantity(sizing, Decimal("78000")) == 0

    def test_eth_half_allocation(self):
        sizing = _buy_and_hold_self(ETH, "10000", pct="50")
        assert BuyAndHold._entry_quantity(sizing, Decimal("3000")) == Decimal("1.66666666")
