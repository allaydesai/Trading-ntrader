"""FX M4 (task 4.1): IB spot FX commission, the modelled half-spread and the run's FX terms.

Real Nautilus ``Cache``, ``CurrencyPair``s built from the shipped specs, real prices and
quantities; orders are stand-ins carrying only what the fee model reads (side and client
order id). Cross rates come from the M3 rate book, exactly as the feeder writes them in a
run. The model inside a running engine is pinned in
``tests/integration/core/test_fx_margin_engine.py``.

Shipped terms: 0.20 bp of USD trade value, USD 2.00 minimum per order (VERIFIED); typical
spread (UNVERIFIED) EURUSD 0.5, GBPUSD 0.8, USDJPY 0.5, EURGBP 1.0 pips, charged half per side.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.backtest_venue import cost_snapshot
from src.core.fee_models import IBKRCommissionModel
from src.core.futures_costs import FuturesCommissionModel, venue_cost_models
from src.core.fx_costs import FxCommissionModel, fx_snapshot, fx_terms
from src.core.fx_rates import FxRateBook
from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import build_futures_contract
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

TS = 1_741_273_200_000_000_000  # 2025-03-06 15:00 UTC


def _pair(name: str):
    return build_fx_pair(get_pair_spec(name))


EURUSD, GBPUSD, USDJPY, EURGBP = (_pair(n) for n in ("EURUSD", "GBPUSD", "USDJPY", "EURGBP"))
_IDS = iter(range(1, 10_000))


def _qty(units: int) -> Quantity:
    return Quantity.from_int(units)


def _order(side=OrderSide.BUY, client_order_id: str | None = None, quantity: int = 100_000):
    cid = ClientOrderId(client_order_id or f"O-{next(_IDS)}")
    return SimpleNamespace(side=side, client_order_id=cid, quantity=_qty(quantity))


def _cache(*instruments, gbpusd: str | None = None) -> Cache:
    cache = Cache()
    for instrument in {*instruments, GBPUSD}:
        cache.add_instrument(instrument)
    if gbpusd is not None:  # the rate the feeder would have written from GBPUSD's MID bar
        px = GBPUSD.make_price(Decimal(gbpusd))
        bar_type = BarType.from_str(f"{GBPUSD.id}-1-HOUR-MID-EXTERNAL")
        FxRateBook(cache).on_bar(Bar(bar_type, px, px, px, px, Quantity.from_int(1), TS, TS))
    return cache


def _model(*instruments, scale="1", cache=None) -> FxCommissionModel:
    terms = {i.id: fx_terms(i) for i in instruments}
    return FxCommissionModel(terms, cache=cache or _cache(*instruments), scale=Decimal(scale))


def _charge(model, instrument, qty: int, px: str, order=None) -> Money:
    order = order or _order()
    return model.get_commission(order, Quantity.from_int(qty), Price.from_str(px), instrument)


class TestCommission:
    def test_eurusd_100k_pays_bps_of_usd_value_plus_half_the_spread(self):
        # 0.2 bp x 110,000 USD = 2.20; 100,000 x 0.5 pip x 0.0001 / 2 = 2.50
        model = _model(EURUSD)
        assert _charge(model, EURUSD, 100_000, "1.10000") == Money("4.70", USD)
        costs = model.costs[str(EURUSD.id)]
        assert (costs.commission, costs.spread) == (Decimal("2.20"), Decimal("2.50"))

    def test_a_small_order_pays_the_usd_2_minimum(self):
        # 0.2 bp x 11,000 USD = 0.22 < 2.00; spread 10,000 x 0.000025 = 0.25
        model = _model(EURUSD)
        assert _charge(model, EURUSD, 10_000, "1.10000") == Money("2.25", USD)
        assert model.costs[str(EURUSD.id)].commission == Decimal("2.00")

    def test_the_round_trip_matches_the_m4_acceptance_formula(self):
        # 2 x max(0.2 bp x notional, 2) + 100k x 0.5 pip = 2 x 2.20 + 5.00
        model = _model(EURUSD)
        buy = _charge(model, EURUSD, 100_000, "1.10000")
        sell = _charge(model, EURUSD, 100_000, "1.10000", _order(OrderSide.SELL))
        assert buy.as_decimal() + sell.as_decimal() == Decimal("9.40")

    def test_the_minimum_is_charged_once_per_order_across_partial_fills(self):
        model = _model(EURUSD)
        order = _order()
        fills = [_charge(model, EURUSD, 10_000, "1.10000", order) for _ in range(3)]
        # bps 0.22 each, total 0.66: the order pays the 2.00 minimum once, not 3 x 2.00
        commission = [f.as_decimal() - Decimal("0.25") for f in fills]
        assert commission == [Decimal("2.00"), Decimal("0"), Decimal("0")]
        assert model.costs[str(EURUSD.id)].commission == Decimal("2.00")

    def test_partial_fills_above_the_minimum_top_up_to_the_total_bps(self):
        model = _model(EURUSD)
        order = _order()
        fills = [_charge(model, EURUSD, 100_000, "1.10000", order) for _ in range(3)]
        # bps 2.20 a fill: 2.20 (above the minimum), then 2.20, 2.20: total 6.60
        assert [f.as_decimal() - Decimal("2.50") for f in fills] == [Decimal("2.20")] * 3

    def test_separate_orders_each_pay_their_own_minimum(self):
        model = _model(EURUSD)
        first = _charge(model, EURUSD, 10_000, "1.10000")
        second = _charge(model, EURUSD, 10_000, "1.10000")
        assert first == second == Money("2.25", USD)

    def test_usd_base_trade_value_is_the_quantity(self):
        # USDJPY: 100,000 USD of value -> 0.2 bp = 2.00; spread 100,000 x 0.5 x 0.01 / 2 = 250 JPY
        # = 250 / 150 = 1.67 USD at the fill price
        model = _model(USDJPY)
        assert _charge(model, USDJPY, 100_000, "150.000") == Money("3.67", USD)
        assert _charge(model, USDJPY, 1_000_000, "150.000") == Money("36.67", USD)  # 20 + 16.67

    def test_a_cross_converts_through_the_cached_rate(self):
        # EURGBP 1,000,000 at 0.85000 = 850,000 GBP x 1.25 = 1,062,500 USD -> 0.2 bp = 21.25;
        # spread 1,000,000 x 1.0 pip x 0.0001 / 2 = 50 GBP x 1.25 = 62.50
        model = _model(EURGBP, cache=_cache(EURGBP, gbpusd="1.25000"))
        assert _charge(model, EURGBP, 1_000_000, "0.85000") == Money("83.75", USD)

    def test_a_cross_without_a_rate_is_charged_nothing_and_recorded(self):
        # The account misses the same rate, so the M3 guard fails the run on that fill.
        model = _model(EURGBP, cache=_cache(EURGBP))
        assert _charge(model, EURGBP, 100_000, "0.85000") == Money(0, USD)
        assert len(model.misses) == 1 and "GBP/USD" in model.misses[0]

    def test_commission_is_always_usd(self):
        model = _model(USDJPY, EURGBP, cache=_cache(USDJPY, EURGBP, gbpusd="1.25000"))
        assert _charge(model, USDJPY, 100_000, "150.000").currency == USD
        assert _charge(model, EURGBP, 100_000, "0.85000").currency == USD


class TestSpreadAndScale:
    @pytest.mark.parametrize(
        "pair, qty, px, spread",
        [
            (EURUSD, 100_000, "1.10000", "2.50"),  # 0.5 pip / 2
            (GBPUSD, 100_000, "1.25000", "4.00"),  # 0.8 pip / 2
            (USDJPY, 100_000, "125.000", "2.00"),  # 250 JPY / 125
        ],
    )
    def test_each_side_pays_half_the_typical_spread(self, pair, qty, px, spread):
        model = _model(pair)
        _charge(model, pair, qty, px)
        _charge(model, pair, qty, px, _order(OrderSide.SELL))
        assert model.costs[str(pair.id)].spread == 2 * Decimal(spread)  # both sides charged

    def test_the_cost_multiplier_scales_commission_and_spread(self):
        model = _model(EURUSD, scale="2")
        assert _charge(model, EURUSD, 100_000, "1.10000") == Money("9.40", USD)
        assert _charge(model, EURUSD, 10_000, "1.10000") == Money("4.50", USD)  # 2 x (2.00+0.25)

    def test_a_zero_multiplier_is_free(self):
        assert _charge(_model(EURUSD, scale="0"), EURUSD, 100_000, "1.1") == Money(0, USD)

    def test_a_negative_multiplier_is_refused(self):
        with pytest.raises(ValueError, match="scale"):
            _model(EURUSD, scale="-1")

    def test_an_instrument_without_terms_is_refused(self):
        with pytest.raises(ValueError, match="EUR/USD"):
            _charge(_model(GBPUSD), EURUSD, 1, "1.10000")


class TestTerms:
    def test_terms_come_from_the_instrument_info(self):
        terms = fx_terms(USDJPY)
        assert terms is not None
        assert (terms.pip_size, terms.spread_pips) == (Decimal("0.01"), Decimal("0.5"))
        assert (terms.rate_bps, terms.min_per_order_usd) == (Decimal("0.20"), Decimal("2.00"))
        assert terms.margin_fraction == Decimal("0.03")
        assert terms.half_spread == Decimal("0.0025")  # quote units per base unit per side

    def test_non_fx_instruments_have_none(self):
        assert fx_terms(TestInstrumentProvider.equity("AAPL", "XNAS")) is None
        assert fx_terms(TestInstrumentProvider.btcusdt_binance()) is None


class TestVenueCostModels:
    def test_an_fx_venue_gets_the_fx_commission_and_nautilus_percentage_margin(self):
        equity_fee = IBKRCommissionModel()
        fee, margin = venue_cost_models([EURUSD, USDJPY], equity_fee, Decimal("1.5"))
        assert isinstance(fee, FxCommissionModel) and fee.scale == Decimal("1.5")
        assert set(fee.terms) == {EURUSD.id, USDJPY.id}
        assert margin is None  # margin_init on the CurrencyPair, Nautilus' leveraged model

    def test_equity_and_futures_venues_are_unchanged(self):
        equity_fee = IBKRCommissionModel()
        aapl = TestInstrumentProvider.equity("AAPL", "XNAS")
        assert venue_cost_models([aapl], equity_fee) == (equity_fee, None)
        es = build_futures_contract(get_contract_spec("ES"))
        fee, _ = venue_cost_models([es], equity_fee)
        assert isinstance(fee, FuturesCommissionModel)

    def test_crypto_currency_pairs_are_not_fx(self):
        equity_fee = IBKRCommissionModel()
        btc = TestInstrumentProvider.btcusdt_binance()
        assert venue_cost_models([btc], equity_fee) == (equity_fee, None)


class TestSnapshot:
    def test_fx_snapshot_records_terms_and_the_cost_split(self):
        cache = _cache(EURUSD, USDJPY)
        model = _model(EURUSD, USDJPY, cache=cache)
        _charge(model, EURUSD, 100_000, "1.10000")
        snapshot = fx_snapshot([EURUSD, USDJPY], cache)
        assert snapshot["account_currency"] == "USD"
        eurusd = snapshot["fx_pairs"]["EUR/USD.IDEALPRO"]
        assert eurusd["base"] == "EUR" and eurusd["quote"] == "USD"
        assert eurusd["pip_size"] == "0.0001" and eurusd["typical_spread_pips"] == "0.5"
        assert eurusd["margin_pct"] == "3.0" and eurusd["conversion_pair"] is None
        assert eurusd["commission"] == {
            "rate_bps": "0.20",
            "min_per_order_usd": "2.00",
            "as_of": "2026-11-23",
        }
        assert eurusd["costs_usd"] == {"commission": "2.20", "spread": "2.50"}
        assert "margin_pct" in eurusd["unverified"]
        usdjpy = snapshot["fx_pairs"]["USD/JPY.IDEALPRO"]
        assert usdjpy["conversion_pair"] == "USDJPY"
        assert usdjpy["costs_usd"] == {"commission": "0.00", "spread": "0.00"}

    def test_a_run_without_fx_records_nothing(self):
        aapl = TestInstrumentProvider.equity("AAPL", "XNAS")
        assert fx_snapshot([aapl], Cache()) == {}

    def test_the_cost_snapshot_merges_futures_and_fx_keys(self):
        es = build_futures_contract(get_contract_spec("ES"))
        cache = _cache(EURUSD)
        cache.add_instrument(es)
        snapshot = cost_snapshot(cache)
        assert {"futures_contracts", "profit_pct_basis", "account_currency", "fx_pairs"} <= set(
            snapshot
        )
        equity_only = Cache()
        equity_only.add_instrument(TestInstrumentProvider.equity("AAPL", "XNAS"))
        assert cost_snapshot(equity_only) == {}


class TestPartialFillsAndOddLots:
    def test_an_orders_spread_is_rounded_once_not_per_fill(self):
        # USDJPY 3 x 33,334 at 150.000: 83.335 JPY = 0.5556 USD a fill; per-fill cents would be
        # 3 x 0.56 = 1.68, the order's exact 250.005 JPY / 150 = 1.6667 is 1.67.
        model = _model(USDJPY)
        order = _order()
        fills = [_charge(model, USDJPY, 33_334, "150.000", order) for _ in range(3)]
        assert model.costs[str(USDJPY.id)].spread == Decimal("1.67")
        assert sum(f.as_decimal() for f in fills) == Decimal("2.00") + Decimal("1.67")

    def test_the_odd_lot_check_reads_the_order_size_not_the_fill(self):
        model = _model(EURUSD)
        order = SimpleNamespace(
            side=OrderSide.BUY, client_order_id=ClientOrderId("BIG"), quantity=_qty(100_000)
        )
        _charge(model, EURUSD, 10_000, "1.10000", order)  # a 10k fill of a 110k-USD order
        assert model._odd_lots == set()
        small = SimpleNamespace(
            side=OrderSide.BUY, client_order_id=ClientOrderId("SMALL"), quantity=_qty(10_000)
        )
        _charge(model, EURUSD, 10_000, "1.10000", small)
        _charge(model, EURUSD, 10_000, "1.10000", _order(quantity=10_000))
        assert model._odd_lots == {str(EURUSD.id)}  # once per pair
