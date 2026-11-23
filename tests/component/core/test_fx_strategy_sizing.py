"""FX M4 (task 4.6): ``sma_crossover`` and ``buy_and_hold`` size spot FX in base units.

Component tier: importing the strategies loads Nautilus. Sizing methods are called unbound on
a duck-typed ``self`` (as ``test_sma_crossover_futures_sizing.py`` does) with real
``CurrencyPair``s and a real ``Cache``; a cross's GBP rate is written by the M3 rate book, as
the feeder does in a run. The orchestrator end to end is in
``tests/integration/core/test_fx_margin_engine.py``.

Before M4 a USDJPY budget of USD 100,000 sized ~690 units (``100,000 / 144.657``, the equity
divide); it is 100,000 USD of base currency.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Money, Quantity

from src.cli.commands._strategy_params import _cli_strategy_params
from src.core.benchmarks.buy_and_hold import BuyAndHold
from src.core.fx_rates import FxRateBook
from src.core.strategies.sma_crossover import SMACrossover
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

TS = 1_741_273_200_000_000_000


def _pair(name: str):
    return build_fx_pair(get_pair_spec(name))


EURUSD, GBPUSD, USDJPY, EURGBP = (_pair(n) for n in ("EURUSD", "GBPUSD", "USDJPY", "EURGBP"))


def _cache(gbpusd: str | None = None) -> Cache:
    cache = Cache()
    for instrument in (EURUSD, GBPUSD, USDJPY, EURGBP):
        cache.add_instrument(instrument)
    if gbpusd is not None:
        px = GBPUSD.make_price(Decimal(gbpusd))
        bar_type = BarType.from_str(f"{GBPUSD.id}-1-HOUR-MID-EXTERNAL")
        FxRateBook(cache).on_bar(Bar(bar_type, px, px, px, px, Quantity.from_int(1), TS, TS))
    return cache


def _sma_self(instrument, close: float, cache: Cache, pct: str = "10", contracts=None):
    return SimpleNamespace(
        _current_bar=SimpleNamespace(close=close),
        portfolio_value=Decimal("1000000"),
        position_size_pct=Decimal(pct),
        instrument_id=instrument.id,
        cache=cache,
        log=MagicMock(),
        config=SimpleNamespace(contracts=contracts),
    )


class TestSmaCrossover:
    def test_usdjpy_usd_100k_is_100k_units_not_the_equity_divide(self):
        sizing = _sma_self(USDJPY, 144.657, _cache())
        assert SMACrossover._calculate_position_size(sizing) == Quantity.from_int(100_000)
        logged = sizing.log.info.call_args.args[0]
        assert "~$100,000 notional" in logged  # USD exposure, not 14.5M (JPY)

    def test_eurusd_floors_to_whole_base_units(self):
        sizing = _sma_self(EURUSD, 1.08, _cache())
        assert SMACrossover._calculate_position_size(sizing) == Quantity.from_int(92_592)

    def test_a_cross_sizes_through_the_cached_gbp_rate(self):
        # one EUR = 0.85 GBP x 1.25 = 1.0625 USD: USD 100,000 -> 94,117 EUR
        sizing = _sma_self(EURGBP, 0.85, _cache(gbpusd="1.25000"))
        assert SMACrossover._calculate_position_size(sizing) == Quantity.from_int(94_117)

    def test_a_cross_before_any_rate_is_an_error_not_a_guess(self):
        with pytest.raises(ValueError, match="GBP/USD"):
            SMACrossover._calculate_position_size(_sma_self(EURGBP, 0.85, _cache()))

    def test_a_budget_below_one_unit_places_no_order(self):
        sizing = _sma_self(EURUSD, 1.08, _cache(), pct="0.0000001")  # USD 0.001
        assert SMACrossover._calculate_position_size(sizing) is None
        sizing.log.warning.assert_called_once()

    def test_contracts_on_an_fx_pair_is_refused(self):
        with pytest.raises(ValueError, match="futures"):
            SMACrossover._calculate_position_size(_sma_self(EURUSD, 1.08, _cache(), contracts=2))


def _buy_and_hold_self(instrument, cash: str, cache: Cache, pct: str = "100"):
    account = SimpleNamespace(
        base_currency=USD,
        balance_total=lambda currency: Money(cash, currency) if currency == USD else None,
    )
    return SimpleNamespace(
        instrument=instrument,
        instrument_id=instrument.id,
        cache=cache,
        portfolio=SimpleNamespace(account=lambda venue: account),
        config=SimpleNamespace(allocation_pct=Decimal(pct)),
    )


class TestBuyAndHold:
    def test_usdjpy_reads_the_usd_cash_and_buys_its_usd_amount(self):
        # the account holds no JPY balance (single-currency USD), so the old JPY read was None
        sizing = _buy_and_hold_self(USDJPY, "1000000", _cache())
        assert BuyAndHold._entry_quantity(sizing, Decimal("150.000")) == Decimal(1_000_000)

    def test_eurusd_buys_whole_euros_worth_the_allocation(self):
        sizing = _buy_and_hold_self(EURUSD, "100000", _cache(), pct="50")
        assert BuyAndHold._entry_quantity(sizing, Decimal("1.10000")) == Decimal(45_454)

    def test_an_equity_account_without_a_base_currency_is_unchanged(self):
        from nautilus_trader.test_kit.providers import TestInstrumentProvider

        aapl = TestInstrumentProvider.equity("AAPL", "XNAS")
        account = SimpleNamespace(
            base_currency=None, balance_total=lambda currency: Money("10000", currency)
        )
        sizing = SimpleNamespace(
            instrument=aapl,
            instrument_id=aapl.id,
            cache=Cache(),
            portfolio=SimpleNamespace(account=lambda venue: account),
            config=SimpleNamespace(allocation_pct=Decimal("100")),
        )
        assert BuyAndHold._entry_quantity(sizing, Decimal("150")) == Decimal(66)


def test_cli_mode_params_size_usdjpy_at_10pct_of_the_balance_in_base_units():
    """``backtest run`` CLI mode: portfolio_value is the starting balance, 10% a trade."""
    params = _cli_strategy_params("sma_crossover", Decimal("1000000"), 100, 10, 20)
    sizing = _sma_self(USDJPY, 157.0, _cache(), pct=str(params["position_size_pct"]))
    sizing.portfolio_value = params["portfolio_value"]
    assert SMACrossover._calculate_position_size(sizing) == Quantity.from_int(100_000)
