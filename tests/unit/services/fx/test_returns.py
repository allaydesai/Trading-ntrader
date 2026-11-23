"""FX reporting figures (M5): a hand-computed EURUSD long win and USDJPY short loss.

Terms: EURUSD and USDJPY at 3% margin, pip 0.0001 / 0.01. Account USD 1,000,000.

- Trade A, EURUSD long 100,000 @ 1.08000 -> 1.08500 (h0 to h5). Pips +50.0; lots 1.00;
  notional 100,000 x 1.08 = USD 108,000; margin 3,240; underlying 0.005 / 1.08 = +0.4630%;
  stored P&L 491.50 (500 gross less costs).
- Trade B, USDJPY short 200,000 @ 150.000 -> 150.500 (h3 to h8). Pips -50.0; lots 2.00;
  notional USD 200,000 (USD base); margin 6,000; underlying -0.5 / 150 = -0.3333%;
  stored P&L -672.40.
- Run: net -180.90; trades overlap h3 to h5, so the peak notional is 308,000 USD on
  USD 1,000,000 of equity (nothing closed yet): peak leverage 0.308x; peak margin 9,240.
  After A closes (equity 1,000,491.50) B alone is 200,000 / 1,000,491.50.
  Costs: commission 8.00 + 4.00, spread 2.50 + 4.60 from ``costs_usd``.
- The spec's scenario: a 100,000 USDJPY short 151.000 -> 150.500 is +50.0 pips, 1.00 lots,
  USD notional 100,000.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.services.fx.returns import (
    FINANCING_NOTE,
    FX,
    TRADE_COLUMNS,
    asset_class,
    report_fields,
    summarise_run,
    terms_from_snapshot,
    trade_fields,
    trade_figures,
    trade_rows,
)

pytestmark = pytest.mark.unit

T0 = datetime(2024, 3, 1, 14, tzinfo=timezone.utc)


def _pair(base, quote, pip, commission, spread, conversion):
    return {
        "base": base,
        "quote": quote,
        "pip_size": pip,
        "typical_spread_pips": "0.5",
        "margin_pct": "3.0",
        "maint_margin_pct": "3.0",
        "margin_as_of": "2026-11-22",
        "min_order_usd": "25000",
        "commission": {"rate_bps": "0.20", "min_per_order_usd": "2.00", "as_of": "2026-11-22"},
        "unverified": ["margin_pct"],
        "conversion_pair": conversion,
        "costs_usd": {"commission": commission, "spread": spread},
    }


SNAPSHOT = {
    "account_currency": "USD",
    "fx_pairs": {
        "EUR/USD.IDEALPRO": _pair("EUR", "USD", "0.0001", "8.00", "2.50", None),
        "USD/JPY.IDEALPRO": _pair("USD", "JPY", "0.01", "4.00", "4.60", "USDJPY"),
    },
}


def _trade(iid, side, qty, entry, exit_, pnl, start_h, end_h):
    return SimpleNamespace(
        trade_id=f"T-{iid[:3]}-{start_h}",
        instrument_id=iid,
        order_side=side,
        quantity=Decimal(qty),
        entry_price=Decimal(entry),
        exit_price=None if exit_ is None else Decimal(exit_),
        profit_loss=None if pnl is None else Decimal(pnl),
        entry_timestamp=T0 + timedelta(hours=start_h),
        exit_timestamp=None if end_h is None else T0 + timedelta(hours=end_h),
    )


TRADE_A = _trade("EUR/USD.IDEALPRO", "BUY", "100000", "1.08000", "1.08500", "491.50", 0, 5)
TRADE_B = _trade("USD/JPY.IDEALPRO", "SELL", "200000", "150.000", "150.500", "-672.40", 3, 8)


def _terms(iid):
    return terms_from_snapshot(SNAPSHOT)[iid]


class TestTerms:
    def test_equity_and_futures_snapshots_are_not_fx(self):
        assert terms_from_snapshot({"config": {}}) == {}
        assert terms_from_snapshot(None) == {}
        assert asset_class({"futures_contracts": {"ES.CME": {}}}) is None
        assert summarise_run({"config": {}}, [TRADE_A], Decimal("1000000")) is None

    def test_fx_terms_and_costs(self):
        terms = _terms("USD/JPY.IDEALPRO")
        assert (terms.base, terms.quote) == ("USD", "JPY")
        assert terms.pip_size == Decimal("0.01")
        assert terms.margin_pct == Decimal("3.0")
        assert terms.commission_usd == Decimal("4.00")
        assert terms.spread_usd == Decimal("4.60")
        assert terms.ticker == "USDJPY"
        assert asset_class(SNAPSHOT) == FX

    def test_missing_costs_read_as_zero(self):
        snapshot = {
            "fx_pairs": {"EUR/USD.IDEALPRO": {**_pair("EUR", "USD", "0.0001", "0", "0", None)}}
        }
        del snapshot["fx_pairs"]["EUR/USD.IDEALPRO"]["costs_usd"]
        terms = terms_from_snapshot(snapshot)["EUR/USD.IDEALPRO"]
        assert terms.commission_usd == terms.spread_usd == Decimal(0)


class TestTradeFigures:
    def test_eurusd_long_win(self):
        fig = trade_figures(TRADE_A, _terms("EUR/USD.IDEALPRO"))
        assert fig.units == Decimal("100000")
        assert fig.lots == Decimal("1")
        assert fig.notional_usd == Decimal("108000")
        assert fig.margin_used_usd == Decimal("3240")
        assert fig.pips == Decimal("50")
        assert round(fig.return_on_underlying_pct, 4) == Decimal("0.4630")
        assert fig.pnl_usd == Decimal("491.50")

    def test_usdjpy_short_loss(self):
        fig = trade_figures(TRADE_B, _terms("USD/JPY.IDEALPRO"))
        assert fig.lots == Decimal("2")
        assert fig.notional_usd == Decimal("200000")
        assert fig.margin_used_usd == Decimal("6000")
        assert fig.pips == Decimal("-50")
        assert round(fig.return_on_underlying_pct, 4) == Decimal("-0.3333")

    def test_spec_scenario_usdjpy_short_win(self):
        trade = _trade("USD/JPY.IDEALPRO", "SELL", "100000", "151.000", "150.500", "330", 0, 1)
        fig = trade_figures(trade, _terms("USD/JPY.IDEALPRO"))
        assert fig.pips == Decimal("50")
        assert fig.lots == Decimal("1")
        assert fig.notional_usd == Decimal("100000")

    def test_open_trade_has_no_pips_or_returns(self):
        trade = _trade("EUR/USD.IDEALPRO", "BUY", "100000", "1.08", "1.09", "1000", 0, None)
        fig = trade_figures(trade, _terms("EUR/USD.IDEALPRO"))
        assert fig.pips is None and fig.return_on_underlying_pct is None
        assert fig.notional_usd == Decimal("108000")

    def test_cross_has_no_usd_notional(self):
        terms = terms_from_snapshot(
            {"fx_pairs": {"EUR/GBP.IDEALPRO": _pair("EUR", "GBP", "0.0001", "0", "0", "GBPUSD")}}
        )["EUR/GBP.IDEALPRO"]
        trade = _trade("EUR/GBP.IDEALPRO", "BUY", "100000", "0.85000", "0.85100", "125", 0, 1)
        fig = trade_figures(trade, terms)
        assert fig.notional_usd is None and fig.margin_used_usd is None
        assert fig.pips == Decimal("10")


class TestRunSummary:
    def test_run_figures(self):
        s = summarise_run(SNAPSHOT, [TRADE_A, TRADE_B], Decimal("1000000"))
        assert s.asset_class == FX
        assert s.closed_trades == 2
        assert s.net_pnl_usd == Decimal("-180.90")
        assert s.max_units == Decimal("200000")
        assert s.peak_notional_usd == Decimal("308000")
        assert s.peak_margin_usd == Decimal("9240")
        assert s.peak_leverage == Decimal("0.308")
        assert s.commission_usd == Decimal("12.00")
        assert s.spread_usd == Decimal("7.10")
        assert s.pips == Decimal("0")
        expected = (Decimal("1") + Decimal("0.005") / Decimal("1.08")) * (
            Decimal("1") - Decimal("0.5") / Decimal("150")
        ) - 1
        assert round(s.return_on_underlying_pct, 6) == round(expected * 100, 6)

    def test_leverage_uses_equity_after_closed_trades(self):
        later_b = _trade("USD/JPY.IDEALPRO", "SELL", "200000", "150.000", "150.500", "-1", 6, 8)
        s = summarise_run(SNAPSHOT, [TRADE_A, later_b], Decimal("1000000"))
        assert s.peak_leverage == Decimal("200000") / Decimal("1000491.50")

    def test_no_capital_means_no_leverage(self):
        assert summarise_run(SNAPSHOT, [TRADE_A], None).peak_leverage is None

    def test_unlisted_instrument_is_skipped(self):
        other = _trade("AAPL.NASDAQ", "BUY", "10", "100", "110", "100", 0, 1)
        assert summarise_run(SNAPSHOT, [other], Decimal("1000")).closed_trades == 0


class TestFields:
    def test_report_fields_are_strings(self):
        f = report_fields(summarise_run(SNAPSHOT, [TRADE_A, TRADE_B], Decimal("1000000")))
        assert f["asset_class"] == "fx"
        assert f["account_currency"] == "USD"
        assert f["net_pnl_usd"] == "-180.90"
        assert f["peak_leverage"] == "0.31"
        assert f["commission_usd"] == "12.00"
        assert f["spread_usd"] == "7.10"
        assert f["costs_usd"] == "19.10"
        assert f["financing_modelled"] is False
        assert f["note"] == FINANCING_NOTE
        assert f["pairs"]["USD/JPY.IDEALPRO"]["costs_usd"] == {
            "commission": "4.00",
            "spread": "4.60",
        }

    def test_trade_fields(self):
        row = trade_fields(trade_figures(TRADE_B, _terms("USD/JPY.IDEALPRO")))
        assert list(row) == list(TRADE_COLUMNS)
        assert row == {
            "units": "200000",
            "lots": "2.00",
            "notional_usd": "200000.00",
            "margin_used_usd": "6000.00",
            "pips": "-50.0",
            "return_on_underlying_pct": "-0.3333",
            "pnl_usd": "-672.40",
        }

    def test_trade_rows_blank_for_unlisted(self):
        other = _trade("AAPL.NASDAQ", "BUY", "10", "100", "110", "100", 0, 1)
        rows = trade_rows(SNAPSHOT, [TRADE_A, other])
        assert rows[0]["pips"] == "50.0" and rows[1] == {}


class TestReviewEdgeCases:
    """CodeRabbit round 1 on PR #64."""

    def test_a_cross_leaves_the_portfolio_peaks_unknown_not_zero(self):
        cross = {
            "fx_pairs": {"EUR/GBP.IDEALPRO": _pair("EUR", "GBP", "0.0001", "0", "0", "GBPUSD")}
        }
        trade = _trade("EUR/GBP.IDEALPRO", "BUY", "100000", "0.85", None, None, 0, None)
        s = summarise_run(cross, [trade], Decimal("1000000"))
        assert s.peak_notional_usd is None and s.peak_margin_usd is None
        assert s.peak_leverage is None
        f = report_fields(s)
        assert f["peak_notional_usd"] is None and f["peak_leverage"] is None

    def test_leverage_is_sampled_after_every_exit_at_a_timestamp(self):
        # Both close at h5. Applying only the loser's exit would show 100,000 / 40,000 = 2.5x,
        # a state that never existed; after both exits nothing is open.
        loser = _trade("EUR/USD.IDEALPRO", "BUY", "100000", "1.00000", "0.40000", "-60000", 1, 5)
        winner = _trade("EUR/USD.IDEALPRO", "BUY", "100000", "1.00000", "1.60000", "60000", 0, 5)
        s = summarise_run(SNAPSHOT, [loser, winner], Decimal("100000"))
        assert s.peak_leverage == Decimal(2)  # 200,000 / 100,000 while both are open

    def test_breakeven_trades_report_zero_underlying_return(self):
        flat = _trade("EUR/USD.IDEALPRO", "BUY", "100000", "1.08000", "1.08000", "-4.50", 0, 1)
        assert summarise_run(SNAPSHOT, [flat], Decimal("1000000")).return_on_underlying_pct == 0
