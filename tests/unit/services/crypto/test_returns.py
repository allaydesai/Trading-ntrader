"""Crypto M5 (task 5.1): reporting figures from a run's snapshot and stored trades.

``crypto_pairs`` is the snapshot M3 writes (terms at the run's tier and the USD charged); the
trades are stored rows (quantity in coins, ``profit_loss`` in USD). Design section 10.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.services.crypto import returns

pytestmark = pytest.mark.unit

T0 = datetime(2024, 3, 1, tzinfo=timezone.utc)


def _pair(base="BTC", tier=2, maker="0.30", taker="0.60", costs=None, dropped=None):
    pair = {
        "base": base,
        "quote": "USD",
        "fee_tier": tier,
        "maker_pct": maker,
        "taker_pct": taker,
        "discounted_pair": False,
        "typical_spread_bps": "2",
        "size_precision": 8,
        "price_precision": 4,
        "fee_schedule": {"venue": "KRAKEN", "as_of": "2026-11-24", "default_tier": 2},
        "unverified": ["typical_spread_bps"],
        "costs_usd": costs
        or {
            "commission": "120.00",
            "commission_maker": "20.00",
            "commission_taker": "100.00",
            "spread": "4.00",
        },
    }
    if dropped is not None:
        pair["orders_below_minimum"] = dropped
    return pair


SNAPSHOT = {
    "account_currency": "USD",
    "fee_tier": 2,
    "periods_per_year": 365,
    "crypto_pairs": {"BTC/USD.KRAKEN": _pair(dropped=3)},
}


def _trade(qty, entry, exit_=None, pnl=None, start_h=0, end_h=None, side="BUY", iid=None):
    return {
        "instrument_id": iid or "BTC/USD.KRAKEN",
        "order_side": side,
        "quantity": Decimal(qty),
        "entry_price": Decimal(entry),
        "exit_price": None if exit_ is None else Decimal(exit_),
        "profit_loss": None if pnl is None else Decimal(pnl),
        "entry_timestamp": T0 + timedelta(hours=start_h),
        "exit_timestamp": None if end_h is None else T0 + timedelta(hours=end_h),
    }


TRADES = [
    _trade("0.12820512", "78000", "80000", "196.41", 0, 5),
    _trade("0.25", "80000", "79000", "-310.00", 6, 9),
]


class TestAssetClass:
    def test_a_run_with_crypto_pairs_is_crypto(self):
        assert returns.asset_class(SNAPSHOT) == "crypto"

    @pytest.mark.parametrize("snapshot", [None, {}, {"fx_pairs": {"EUR/USD.IDEALPRO": {}}}])
    def test_anything_else_is_not(self, snapshot):
        assert returns.asset_class(snapshot) is None
        assert returns.summarise_run(snapshot, TRADES, 100000) is None


class TestTradeFigures:
    def test_quantity_notional_underlying_and_pnl(self):
        terms = returns.terms_from_snapshot(SNAPSHOT)["BTC/USD.KRAKEN"]
        fig = returns.trade_figures(TRADES[0], terms)
        assert fig.quantity == Decimal("0.12820512")
        assert fig.notional_usd == Decimal("0.12820512") * 78000
        assert fig.return_on_underlying_pct.quantize(Decimal("0.0001")) == Decimal("2.5641")
        assert fig.pnl_usd == Decimal("196.41")

    def test_an_open_trade_has_no_underlying_return(self):
        terms = returns.terms_from_snapshot(SNAPSHOT)["BTC/USD.KRAKEN"]
        fig = returns.trade_figures(_trade("0.1", "78000"), terms)
        assert fig.return_on_underlying_pct is None and fig.pnl_usd is None

    def test_a_partly_closed_trade_keeps_its_realised_pnl(self):
        terms = returns.terms_from_snapshot(SNAPSHOT)["BTC/USD.KRAKEN"]
        fig = returns.trade_figures(_trade("0.1", "78000", pnl="400.00"), terms)
        assert fig.return_on_underlying_pct is None and fig.pnl_usd == Decimal("400.00")
        s = returns.summarise_run(SNAPSHOT, [_trade("0.1", "78000", pnl="400.00")], 100000)
        assert s.net_pnl_usd == 0 and s.closed_trades == 0  # closed trades only


class TestSummary:
    def test_run_figures(self):
        s = returns.summarise_run(SNAPSHOT, TRADES, Decimal("100000"))
        assert s.asset_class == "crypto" and s.venue == "KRAKEN" and s.fee_tier == 2
        assert s.periods_per_year == 365 and s.account_currency == "USD"
        assert s.closed_trades == 2
        assert s.net_pnl_usd == Decimal("-113.59")
        assert s.max_quantity == Decimal("0.25")
        assert s.peak_notional_usd == Decimal("20000.00")
        # 20,000 of BTC on 100,000 + 196.41 equity: under 1, spot is unlevered
        assert s.peak_exposure.quantize(Decimal("0.0001")) == Decimal("0.1996")
        assert s.commission_maker_usd == Decimal("20.00")
        assert s.commission_taker_usd == Decimal("100.00")
        assert s.commission_usd == Decimal("120.00") and s.spread_usd == Decimal("4.00")
        assert s.costs_usd == Decimal("124.00")
        assert s.orders_below_minimum == 3

    def test_runs_before_m5_have_no_recorded_drop_count(self):
        snapshot = {**SNAPSHOT, "crypto_pairs": {"BTC/USD.KRAKEN": _pair()}}
        assert returns.summarise_run(snapshot, TRADES, 100000).orders_below_minimum is None

    def test_drop_counts_sum_over_pairs(self):
        pairs = {
            "BTC/USD.KRAKEN": _pair(dropped=2),
            "ETH/USD.KRAKEN": _pair(base="ETH", dropped=1),
        }
        s = returns.summarise_run({**SNAPSHOT, "crypto_pairs": pairs}, TRADES, 100000)
        assert s.orders_below_minimum == 3

    def test_mixed_tiers_have_no_run_tier(self):
        pairs = {"BTC/USD.KRAKEN": _pair(), "ETH/USD.KRAKEN": _pair(base="ETH", tier=4)}
        s = returns.summarise_run({**SNAPSHOT, "fee_tier": None, "crypto_pairs": pairs}, [], 1)
        assert s.fee_tier is None and s.closed_trades == 0 and s.max_quantity == 0


class TestQuantityText:
    @pytest.mark.parametrize(
        ("qty", "text"),
        [
            ("0.12820512", "0.12820512"),
            ("0.25000000", "0.25"),
            ("1.00000000", "1"),
            ("1234.5", "1,234.5"),
            ("0.00005", "0.00005"),
        ],
    )
    def test_full_precision_trailing_zeros_trimmed(self, qty, text):
        assert returns.quantity_text(Decimal(qty), 8) == text

    def test_never_shows_more_than_the_size_precision(self):
        assert returns.quantity_text(Decimal("0.123456789"), 8) == "0.12345679"


class TestReportFields:
    def test_json_safe_fields(self):
        fields = returns.report_fields(returns.summarise_run(SNAPSHOT, TRADES, 100000))
        assert fields["asset_class"] == "crypto"
        assert fields["venue"] == "KRAKEN" and fields["fee_tier"] == 2
        assert fields["periods_per_year"] == 365 and fields["account_currency"] == "USD"
        assert fields["closed_trade_pnl_usd"] == "-113.59"
        assert fields["max_quantity"] == "0.25"
        assert fields["commission_maker_usd"] == "20.00"
        assert fields["commission_taker_usd"] == "100.00"
        assert fields["spread_usd"] == "4.00" and fields["costs_usd"] == "124.00"
        assert fields["orders_below_minimum"] == 3
        assert fields["spot"] is True and fields["leveraged"] is False
        assert fields["long_only"] is True
        assert "spot, unlevered, long-only" in fields["note"].lower()
        assert "fee tier 2" in fields["note"]
        assert "upper bound when one scales in" in fields["note"]  # review: peak_qty from open
        pair = fields["pairs"]["BTC/USD.KRAKEN"]
        assert pair["maker_pct"] == "0.30" and pair["taker_pct"] == "0.60"
        assert pair["orders_below_minimum"] == 3
        assert pair["costs_usd"]["commission_taker"] == "100.00"

    def test_trade_rows_keep_full_precision(self):
        rows = returns.trade_rows(SNAPSHOT, [*TRADES, _trade("1", "10", iid="AAPL.NASDAQ")])
        assert rows[0]["coin_quantity"] == "0.12820512"
        assert rows[0]["notional_usd"] == "10000.00"
        assert rows[0]["return_on_underlying_pct"] == "2.5641"
        assert rows[1]["coin_quantity"] == "0.25" and rows[1]["pnl_usd"] == "-310.00"
        assert rows[2] == {}
        assert tuple(rows[0]) == returns.TRADE_COLUMNS
