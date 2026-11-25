"""Crypto M3 (task 3.1): Kraken Pro spot commission, the modelled half-spread, the run's terms.

Real Nautilus ``Cache`` and ``CurrencyPair``s built from the shipped coin specs; orders are
stand-ins carrying only what the fee model reads (side, client order id and the liquidity side
Nautilus sets on the order before it asks the fee model). Which order types fill as maker or
taker inside a running engine is pinned in ``tests/integration/core/test_crypto_venue_engine.py``.

Shipped schedule (VERIFIED, Kraken Pro CA page, 2026-11-24): tier 1 0.40% maker / 0.80% taker,
default tier 2 0.30% / 0.60%, tier 4 0.20% / 0.35%; no minimum fee. Spread (UNVERIFIED): BTC
2 bp full spread, charged half per side. Hand figures: USD 9,984 is 0.128 BTC at 78,000.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import LiquiditySide, OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    PositionId,
    TradeId,
)
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

from src.core.backtest_venue import cost_snapshot
from src.core.crypto_costs import (
    CRYPTO_PERIODS_PER_YEAR,
    CryptoCommissionModel,
    count_order_below_minimum,
    crypto_snapshot,
    crypto_terms,
    track_orders_below_minimum,
)
from src.core.fee_models import IBKRCommissionModel
from src.core.futures_costs import venue_cost_models
from src.services.crypto.coin_specs import CryptoSpecError, get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

MAKER, TAKER = LiquiditySide.MAKER, LiquiditySide.TAKER
_IDS = iter(range(1, 10_000))


def _pair(coin: str = "BTC", *, tier: int | None = None, spread: str | None = None, **kraken):
    spec = get_coin_spec(coin)
    if spread is not None:
        spec = spec.model_copy(update={"typical_spread_bps": Decimal(spread)})
    if kraken:
        spec = spec.model_copy(update={"kraken": spec.kraken.model_copy(update=kraken)})
    return build_crypto_pair(spec, fee_tier=tier)


BTC = _pair()


def _order(side=OrderSide.BUY, liquidity=TAKER):
    cid = ClientOrderId(f"O-{next(_IDS)}")
    return SimpleNamespace(side=side, client_order_id=cid, liquidity_side=liquidity)


def _model(*instruments, scale="1", cache=None) -> CryptoCommissionModel:
    terms = {i.id: crypto_terms(i) for i in instruments}
    return CryptoCommissionModel(terms, cache=cache, scale=Decimal(scale))


def _charge(model, instrument, qty: str, px: str, order=None) -> Decimal:
    order = order or _order()
    money = model.get_commission(order, Quantity.from_str(qty), Price.from_str(px), instrument)
    assert money.currency == USD
    return money.as_decimal()


class TestCommission:
    @pytest.mark.parametrize(
        ("tier", "liquidity", "expected"),
        [
            (1, TAKER, "79.87"),  # 9,984 x 0.80% = 79.872
            (1, MAKER, "39.94"),  # 9,984 x 0.40% = 39.936
            (2, TAKER, "59.90"),  # 9,984 x 0.60% = 59.904
            (2, MAKER, "29.95"),  # 9,984 x 0.30% = 29.952
            (4, TAKER, "34.94"),  # 9,984 x 0.35% = 34.944
        ],
    )
    def test_usd_9984_of_btc_at_a_tier(self, tier, liquidity, expected):
        btc = _pair(tier=tier, spread="0")
        model = _model(btc)
        assert _charge(model, btc, "0.128", "78000.0000", _order(liquidity=liquidity)) == Decimal(
            expected
        )

    def test_the_default_tier_is_the_schedules_tier_2(self):
        assert crypto_terms(BTC).fee_tier == 2
        btc = _pair(spread="0")
        assert _charge(_model(btc), btc, "0.128", "78000.0000") == Decimal("59.90")

    def test_maker_only_for_a_maker_fill(self):
        btc = _pair(tier=1, spread="0")
        model = _model(btc)
        taker = _charge(model, btc, "0.128", "78000.0000", _order(liquidity=TAKER))
        maker = _charge(model, btc, "0.128", "78000.0000", _order(liquidity=MAKER))
        assert (taker, maker) == (Decimal("79.87"), Decimal("39.94"))
        costs = model.costs[str(btc.id)]
        assert (costs.taker, costs.maker) == (Decimal("79.87"), Decimal("39.94"))

    def test_no_minimum_fee(self):
        btc = _pair(tier=1, spread="0")
        # USD 10 at tier 1 taker: 0.08, not a minimum
        assert _charge(_model(btc), btc, "0.0001", "100000.0000") == Decimal("0.08")

    def test_split_fills_sum_to_the_single_fill_charge(self):
        btc = _pair(tier=1)
        single = _charge(_model(btc), btc, "0.128", "78000.0000")
        model = _model(btc)
        order = _order()
        split = [_charge(model, btc, q, "78000.0000", order) for q in ("0.05", "0.05", "0.028")]
        assert abs(sum(split) - single) <= Decimal("0.01")
        assert sum(split) == single  # per-order running totals: exact, not just within a cent

    def test_the_half_spread_is_a_separate_component(self):
        # BTC 2 bp full spread: 9,984 x 1 bp = 0.9984 -> 1.00 per side, on top of 59.90
        model = _model(BTC)
        assert _charge(model, BTC, "0.128", "78000.0000") == Decimal("60.90")
        costs = model.costs[str(BTC.id)]
        assert (costs.taker, costs.maker, costs.spread) == (
            Decimal("59.90"),
            Decimal("0"),
            Decimal("1.00"),
        )

    def test_cost_stress_scales_both_components(self):
        model = _model(BTC, scale="2")
        # scaled before cent rounding (the FX rule): 59.904 x 2 = 119.808 and 0.9984 x 2 = 1.9968
        assert _charge(model, BTC, "0.128", "78000.0000") == Decimal("121.81")  # 119.81 + 2.00

    def test_a_discounted_pair_pays_2_bp_less_floored_at_zero(self):
        btc = _pair(tier=2, spread="0", discounted_pair=True)
        # tier 2 taker 0.60% - 0.02% = 0.58%: 9,984 x 0.0058 = 57.9072
        assert _charge(_model(btc), btc, "0.128", "78000.0000") == Decimal("57.91")
        free = _pair(tier=12, spread="0", discounted_pair=True)  # maker 0.00% stays 0
        assert _charge(_model(free), free, "0.128", "78000.0000", _order(liquidity=MAKER)) == 0

    def test_totals_per_instrument_and_liquidity_side(self):
        eth = _pair("ETH", tier=1, spread="0")
        btc = _pair(tier=1, spread="0")
        model = _model(btc, eth)
        _charge(model, btc, "0.128", "78000.0000", _order(liquidity=TAKER))
        _charge(model, btc, "0.128", "78000.0000", _order(OrderSide.SELL, MAKER))
        _charge(model, eth, "1", "1000.0000", _order(liquidity=TAKER))
        assert model.costs[str(btc.id)].as_snapshot() == {
            "commission": "119.81",
            "commission_maker": "39.94",
            "commission_taker": "79.87",
            "spread": "0.00",
        }
        assert model.costs[str(eth.id)].as_snapshot()["commission_taker"] == "8.00"

    def test_an_instrument_without_terms_is_refused(self):
        with pytest.raises(ValueError, match="No crypto commission terms"):
            _charge(_model(BTC), _pair("ETH"), "1", "1000.0000")

    def test_a_negative_scale_is_refused(self):
        with pytest.raises(ValueError, match="negative"):
            _model(BTC, scale="-1")


class TestTerms:
    def test_terms_come_from_the_spec_and_the_runs_tier(self):
        terms = crypto_terms(_pair(tier=4))
        assert (terms.base, terms.quote, terms.fee_tier) == ("BTC", "USD", 4)
        assert (terms.maker_pct, terms.taker_pct) == (Decimal("0.20"), Decimal("0.35"))
        assert terms.spread_bps == Decimal("2")
        assert terms.rate(TAKER) == Decimal("0.0035") and terms.rate(MAKER) == Decimal("0.002")

    def test_an_unknown_tier_is_refused_when_the_pair_is_built(self):
        with pytest.raises(CryptoSpecError, match="Unknown Kraken fee tier 18"):
            _pair(tier=18)

    def test_non_crypto_instruments_have_none(self):
        assert crypto_terms(TestInstrumentProvider.equity("AAPL", "XNAS")) is None
        assert crypto_terms(build_fx_pair(get_pair_spec("EURUSD"))) is None
        # the Kraken fetch path's fallback pair has no spec info until M7
        assert crypto_terms(TestInstrumentProvider.btcusdt_binance()) is None


class TestVenueCostModels:
    def test_a_crypto_venue_gets_the_kraken_model_and_nautilus_margin(self):
        fee, margin = venue_cost_models([BTC], IBKRCommissionModel(), Decimal("1.5"))
        assert isinstance(fee, CryptoCommissionModel) and fee.scale == Decimal("1.5")
        assert set(fee.terms) == {BTC.id}
        assert margin is None  # margin_init 1 on the pair, Nautilus' leveraged model at 1x


class TestSnapshot:
    def test_the_snapshot_records_tier_terms_annualisation_and_the_cost_split(self):
        btc = _pair(tier=1)
        cache = Cache()
        cache.add_instrument(btc)
        model = _model(btc, cache=cache)
        _charge(model, btc, "0.128", "78000.0000", _order(liquidity=TAKER))
        snapshot = crypto_snapshot([btc], cache)
        assert snapshot["account_currency"] == "USD"
        assert snapshot["fee_tier"] == 1
        assert snapshot["periods_per_year"] == CRYPTO_PERIODS_PER_YEAR == 365
        entry = snapshot["crypto_pairs"]["BTC/USD.KRAKEN"]
        assert entry["base"] == "BTC" and entry["quote"] == "USD"
        assert entry["fee_tier"] == 1
        assert (entry["maker_pct"], entry["taker_pct"]) == ("0.40", "0.80")
        assert entry["typical_spread_bps"] == "2"
        assert entry["discounted_pair"] is False
        assert entry["size_precision"] == 8 and entry["price_precision"] == 4
        assert entry["fee_schedule"]["as_of"] == "2026-11-24"
        assert "typical_spread_bps" in entry["unverified"]
        assert entry["costs_usd"] == {
            "commission": "79.87",
            "commission_maker": "0.00",
            "commission_taker": "79.87",
            "spread": "1.00",
        }
        # M5: no strategy counted drops here, so the count is "not recorded", not a false 0
        assert "orders_below_minimum" not in entry

    def test_a_tracked_pair_with_no_drops_records_zero(self):
        cache = Cache()
        cache.add_instrument(BTC)
        track_orders_below_minimum(cache, BTC.id)
        track_orders_below_minimum(cache, BTC.id)  # a second strategy: still 0
        assert (
            crypto_snapshot([BTC], cache)["crypto_pairs"]["BTC/USD.KRAKEN"]["orders_below_minimum"]
            == 0
        )

    def test_the_snapshot_records_the_orders_dropped_under_the_minimum_per_pair(self):
        """Crypto M5: the strategies' drop count reaches the saved run, not only the log."""
        eth = _pair("ETH")
        cache = Cache()
        for instrument in (BTC, eth):
            cache.add_instrument(instrument)
        assert count_order_below_minimum(cache, BTC.id) == 1
        assert count_order_below_minimum(cache, BTC.id) == 2
        count_order_below_minimum(cache, eth.id)
        pairs = crypto_snapshot([BTC, eth], cache)["crypto_pairs"]
        assert pairs["BTC/USD.KRAKEN"]["orders_below_minimum"] == 2
        assert pairs["ETH/USD.KRAKEN"]["orders_below_minimum"] == 1

    def test_a_run_without_crypto_records_nothing(self):
        assert crypto_snapshot([TestInstrumentProvider.equity("AAPL", "XNAS")], Cache()) == {}

    def test_the_cost_snapshot_includes_the_crypto_keys(self):
        cache = Cache()
        cache.add_instrument(BTC)
        snapshot = cost_snapshot(cache)
        assert {"account_currency", "fee_tier", "periods_per_year", "crypto_pairs"} <= set(snapshot)
        assert snapshot["crypto_pairs"]["BTC/USD.KRAKEN"]["costs_usd"]["commission"] == "0.00"


class TestPositionSeries:
    """Exact peak exposure: the snapshot saves each pair's fills in engine order.

    The fee model numbers the fills it charges; the snapshot joins those numbers with the
    orders' fill events (real Nautilus orders and ``OrderFilled`` events in a real ``Cache``).
    """

    @staticmethod
    def _filled(cache, model, order, instrument, qty: str, px: str, pid: str | None, ts: int):
        last_qty, last_px = instrument.make_qty(Decimal(qty)), instrument.make_price(Decimal(px))
        commission = model.get_commission(order, last_qty, last_px, instrument)
        fill = OrderFilled(
            trader_id=order.trader_id,
            strategy_id=order.strategy_id,
            instrument_id=instrument.id,
            client_order_id=order.client_order_id,
            venue_order_id=order.venue_order_id,
            account_id=AccountId("KRAKEN-001"),
            trade_id=TradeId(f"T-{next(_IDS)}"),
            position_id=None if pid is None else PositionId(pid),
            order_side=order.side,
            order_type=order.order_type,
            last_qty=last_qty,
            last_px=last_px,
            currency=USD,
            commission=commission,
            liquidity_side=TAKER,
            event_id=UUID4(),
            ts_event=ts,
            ts_init=ts,
        )
        order.apply(fill)
        return commission.as_decimal()

    def _ready(self, cache, instrument, side, qty: str):
        order = TestExecStubs.market_order(
            instrument,
            side,
            instrument.make_qty(Decimal(qty)),
            client_order_id=ClientOrderId(f"O-{next(_IDS)}"),
        )
        order.apply(TestEventStubs.order_submitted(order))
        order.apply(TestEventStubs.order_accepted(order))
        cache.add_order(order)
        return order

    def test_each_pair_gets_its_fills_in_engine_order_with_signed_position_quantity(self):
        eth = _pair("ETH")
        cache = Cache()
        for instrument in (BTC, eth):
            cache.add_instrument(instrument)
        model = _model(BTC, eth, cache=cache)
        buy = self._ready(cache, BTC, OrderSide.BUY, "0.2")
        eth_buy = self._ready(cache, eth, OrderSide.BUY, "1")
        sell = self._ready(cache, BTC, OrderSide.SELL, "0.15")
        # the BTC buy fills in two parts around the ETH buy; the sell is partial (0.15 of 0.2)
        fee0 = self._filled(cache, model, buy, BTC, "0.05", "78000.0000", "P-BTC", 1)
        fee1 = self._filled(cache, model, eth_buy, eth, "1.00000000", "3000.0000", "P-ETH", 1)
        fee2 = self._filled(cache, model, buy, BTC, "0.15", "78100.0000", "P-BTC", 2)
        fee3 = self._filled(cache, model, sell, BTC, "0.15", "79000.0000", "P-BTC", 3)
        pairs = crypto_snapshot([BTC, eth], cache)["crypto_pairs"]
        assert pairs["BTC/USD.KRAKEN"]["position_series"] == [
            [0, 1, "P-BTC", "0.05000000", "78000.0000", f"{fee0:f}"],
            [2, 2, "P-BTC", "0.20000000", "78100.0000", f"{fee2:f}"],
            [3, 3, "P-BTC", "0.05000000", "79000.0000", f"{fee3:f}"],
        ]
        assert pairs["ETH/USD.KRAKEN"]["position_series"] == [
            [1, 1, "P-ETH", "1.00000000", "3000.0000", f"{fee1:f}"]
        ]

    def test_a_pair_with_no_fills_records_an_empty_series(self):
        cache = Cache()
        cache.add_instrument(BTC)
        assert (
            crypto_snapshot([BTC], cache)["crypto_pairs"]["BTC/USD.KRAKEN"]["position_series"] == []
        )

    def test_fills_the_cache_cannot_match_leave_the_series_unrecorded(self):
        # A charge with no fill event behind it: report the M5 upper bound, not a wrong figure
        cache = Cache()
        cache.add_instrument(BTC)
        _charge(_model(BTC, cache=cache), BTC, "0.1", "78000.0000")
        assert (
            "position_series" not in crypto_snapshot([BTC], cache)["crypto_pairs"]["BTC/USD.KRAKEN"]
        )

    def test_without_a_cache_there_is_no_series(self):
        assert "position_series" not in crypto_snapshot([BTC])["crypto_pairs"]["BTC/USD.KRAKEN"]

    def test_fee_models_of_two_venues_share_one_fill_numbering(self):
        eth = _pair("ETH")
        cache = Cache()
        for instrument in (BTC, eth):
            cache.add_instrument(instrument)
        btc_model, eth_model = _model(BTC, cache=cache), _model(eth, cache=cache)
        buy = self._ready(cache, BTC, OrderSide.BUY, "0.1")
        eth_buy = self._ready(cache, eth, OrderSide.BUY, "1")
        self._filled(cache, btc_model, buy, BTC, "0.1", "78000", "P-BTC", 1)
        self._filled(cache, eth_model, eth_buy, eth, "1", "3000", "P-ETH", 2)
        pairs = crypto_snapshot([BTC, eth], cache)["crypto_pairs"]
        assert [e[0] for e in pairs["BTC/USD.KRAKEN"]["position_series"]] == [0]
        assert [e[0] for e in pairs["ETH/USD.KRAKEN"]["position_series"]] == [1]

    def test_a_fill_without_a_position_leaves_the_series_unrecorded(self):
        cache = Cache()
        cache.add_instrument(BTC)
        buy = self._ready(cache, BTC, OrderSide.BUY, "0.1")
        self._filled(cache, _model(BTC, cache=cache), buy, BTC, "0.1", "78000", None, 1)
        assert (
            "position_series" not in crypto_snapshot([BTC], cache)["crypto_pairs"]["BTC/USD.KRAKEN"]
        )

    @pytest.mark.parametrize(
        ("final", "kept"),
        [
            ("9999.94", True),  # 10,000 less the 0.06 charged: the replay's balance
            ("9999.93", True),  # a cent apart on one fill: float rounding, kept
            ("9999.80", False),  # the books disagree by more than a cent a fill
        ],
    )
    def test_the_series_is_kept_only_when_it_replays_to_the_account(self, final, kept):
        cache = Cache()
        cache.add_instrument(BTC)
        buy = self._ready(cache, BTC, OrderSide.BUY, "0.1")
        fee = self._filled(cache, _model(BTC, cache=cache), buy, BTC, "0.1", "100", "P", 1)
        assert fee == Decimal("0.06")  # 10 x 0.60% = 0.06 plus a 0.001 spread, in cents
        account = SimpleNamespace(
            id="KRAKEN-001",
            starting_balances=lambda: {USD: _usd_money("10000.00")},
            balance_total=lambda currency: _usd_money(final),
        )
        books = _CacheWithAccount(cache, account)
        entry = crypto_snapshot([BTC], books)["crypto_pairs"]["BTC/USD.KRAKEN"]
        assert ("position_series" in entry) is kept


class _CacheWithAccount:
    """A real ``Cache`` seen through a stand-in venue account (the balance check reads it)."""

    def __init__(self, cache, account) -> None:
        self._cache, self._account = cache, account

    def __getattr__(self, name):
        return getattr(self._cache, name)

    def account_for_venue(self, venue):
        return self._account


def _usd_money(amount: str) -> Money:
    return Money(Decimal(amount), USD)
