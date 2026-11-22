"""Futures reporting figures (M4): hand-computed ES example, equity passthrough.

ES: multiplier 50, initial margin 27,500 USD, 2.25 USD per contract per side.

- Trade A: long 2 @ 5000.00 -> 5020.00. Points +20; gross 20 x 50 x 2 = 2,000;
  commission 2 x 2 x 2.25 = 9; P&L 1,991. Margin 55,000; notional 500,000.
  Return on margin 1,991 / 55,000 = 3.62%; on the underlying 20 / 5000 = 0.40%.
- Trade B: short 1 @ 5030.00 -> 5040.00. Points -10; gross -500; commission 4.50;
  P&L -504.50. Margin 27,500. Return on margin -1.8345%; underlying -10 / 5030.
- Run: net 1,486.50; peak margin 55,000 (no overlap); return on margin 2.7027%;
  underlying (1.004 x (1 - 10/5030)) - 1 = 0.2004%.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.services.futures.returns import (
    EQUITY,
    FUTURES,
    asset_class,
    report_fields,
    summarise_run,
    terms_from_snapshot,
    trade_fields,
    trade_figures,
)

pytestmark = pytest.mark.unit

T0 = datetime(2024, 3, 1, 14, tzinfo=timezone.utc)
ES_SNAPSHOT = {
    "futures_contracts": {
        "ES.CME": {
            "multiplier": "50",
            "margin_initial_usd": "27500",
            "margin_maintenance_usd": "25000",
            "commission_per_contract_usd": "2.25",
            "margin_as_of": "2026-11-21",
        }
    },
    "profit_pct_basis": "underlying",
}


def _trade(side, qty, entry, exit_, pnl, start_h, end_h, iid="ES.CME"):
    return SimpleNamespace(
        trade_id=f"T-{start_h}",
        instrument_id=iid,
        order_side=side,
        quantity=Decimal(qty),
        entry_price=Decimal(entry),
        exit_price=None if exit_ is None else Decimal(exit_),
        profit_loss=None if pnl is None else Decimal(pnl),
        entry_timestamp=T0 + timedelta(hours=start_h),
        exit_timestamp=None if end_h is None else T0 + timedelta(hours=end_h),
    )


TRADE_A = _trade("BUY", "2", "5000.00", "5020.00", "1991.00", 0, 5)
TRADE_B = _trade("SELL", "1", "5030.00", "5040.00", "-504.50", 6, 9)


class TestTerms:
    def test_equity_snapshot_has_no_terms(self):
        assert terms_from_snapshot({"config": {}}) == {}
        assert terms_from_snapshot(None) == {}
        assert asset_class({"config": {}}) == EQUITY

    def test_futures_terms_are_decimals(self):
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        assert terms.multiplier == Decimal("50")
        assert terms.margin_initial_usd == Decimal("27500")
        assert terms.commission_per_contract_usd == Decimal("2.25")
        assert terms.margin_as_of == "2026-11-21"
        assert asset_class(ES_SNAPSHOT) == FUTURES


class TestTradeFigures:
    def test_long_winner(self):
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        fig = trade_figures(TRADE_A, terms)
        assert fig.contracts == Decimal("2")
        assert fig.multiplier == Decimal("50")
        assert fig.notional_usd == Decimal("500000")
        assert fig.margin_used_usd == Decimal("55000")
        assert fig.points == Decimal("20")
        assert fig.return_on_underlying_pct == Decimal("0.4")
        assert fig.return_on_margin_pct.quantize(Decimal("0.0001")) == Decimal("3.6200")

    def test_short_loser_is_side_aware(self):
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        fig = trade_figures(TRADE_B, terms)
        assert fig.points == Decimal("-10")
        assert fig.return_on_underlying_pct.quantize(Decimal("0.0001")) == Decimal("-0.1988")
        assert fig.return_on_margin_pct.quantize(Decimal("0.0001")) == Decimal("-1.8345")

    def test_winning_short_is_positive(self):
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        short = _trade("SELL", "1", "5040", "5030", "495.50", 0, 1)
        fig = trade_figures(short, terms)
        assert fig.points == Decimal("10")
        assert fig.return_on_underlying_pct > 0

    def test_open_trade_has_exposure_but_no_return(self):
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        fig = trade_figures(_trade("BUY", "1", "5000", None, None, 0, None), terms)
        assert fig.margin_used_usd == Decimal("27500")
        assert fig.points is None
        assert fig.return_on_margin_pct is None
        assert fig.return_on_underlying_pct is None

    def test_dict_rows_work_like_orm_rows(self):
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        assert trade_figures(vars(TRADE_A), terms) == trade_figures(TRADE_A, terms)


class TestRunSummary:
    def test_equity_run_is_none(self):
        assert summarise_run({"config": {}}, [TRADE_A]) is None

    def test_hand_computed_es_run(self):
        s = summarise_run(ES_SNAPSHOT, [TRADE_A, TRADE_B])
        assert s.asset_class == FUTURES
        assert s.closed_trades == 2
        assert s.net_pnl_usd == Decimal("1486.50")
        assert s.peak_margin_usd == Decimal("55000")
        assert s.max_contracts == Decimal("2")
        assert s.return_on_margin_pct.quantize(Decimal("0.0001")) == Decimal("2.7027")
        assert s.return_on_underlying_pct.quantize(Decimal("0.0001")) == Decimal("0.2004")
        assert s.points_per_contract == Decimal("10")

    def test_overlapping_trades_sum_their_margin(self):
        overlap = _trade("BUY", "1", "5010", "5015", "245.50", 2, 4)
        s = summarise_run(ES_SNAPSHOT, [TRADE_A, overlap])
        assert s.peak_margin_usd == Decimal("82500")

    def test_close_and_reverse_on_one_bar_is_not_double_counted(self):
        reverse = _trade("SELL", "2", "5020", "5010", "991", 5, 8)
        s = summarise_run(ES_SNAPSHOT, [TRADE_A, reverse])
        assert s.peak_margin_usd == Decimal("55000")

    def test_open_trade_holds_margin_to_the_end(self):
        still_open = _trade("BUY", "1", "5030", None, None, 3, None)
        s = summarise_run(ES_SNAPSHOT, [TRADE_A, still_open])
        assert s.peak_margin_usd == Decimal("82500")
        assert s.closed_trades == 1
        assert s.net_pnl_usd == Decimal("1991.00")

    def test_no_trades_has_no_return(self):
        s = summarise_run(ES_SNAPSHOT, [])
        assert s.closed_trades == 0
        assert s.return_on_margin_pct is None
        assert s.return_on_underlying_pct is None

    def test_trade_on_an_unlisted_instrument_is_skipped(self):
        stray = _trade("BUY", "100", "10", "11", "100", 0, 1, iid="AAPL.NASDAQ")
        s = summarise_run(ES_SNAPSHOT, [TRADE_A, stray])
        assert s.closed_trades == 1


class TestSerialisation:
    def test_report_fields_are_json_safe_strings(self):
        fields = report_fields(summarise_run(ES_SNAPSHOT, [TRADE_A, TRADE_B]))
        assert fields["asset_class"] == "futures"
        assert fields["return_on_margin_pct"] == "2.7027"
        assert fields["return_on_underlying_pct"] == "0.2004"
        assert fields["peak_margin_usd"] == "55000.00"
        assert fields["net_pnl_usd"] == "1486.50"
        assert fields["contracts"]["ES.CME"]["multiplier"] == "50"
        assert fields["pnl_basis"] == "ratio_adjusted"

    def test_trade_fields(self):
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        row = trade_fields(trade_figures(TRADE_B, terms))
        assert row == {
            "contracts": "1",
            "multiplier": "50",
            "notional_usd": "251500.00",
            "margin_used_usd": "27500.00",
            "points_adjusted": "-10.00",
            "return_on_underlying_pct": "-0.1988",
            "return_on_margin_pct": "-1.8345",
        }

    def test_open_trade_fields_are_blank(self):
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        row = trade_fields(trade_figures(_trade("BUY", "1", "5000", None, None, 0, None), terms))
        assert row["points_adjusted"] is None
        assert row["return_on_margin_pct"] is None


class TestReviewEdgeCases:
    """CodeRabbit round 1 on PR #53."""

    def test_partly_closed_position_without_exit_timestamp_is_still_open(self):
        """Nautilus sets avg_px_close/realized_pnl per closing fill, ts_closed only at flat."""
        partial = _trade("BUY", "2", "5000", "5010", "495.50", 1, None)
        s = summarise_run(ES_SNAPSHOT, [TRADE_A, partial])
        assert s.closed_trades == 1
        assert s.net_pnl_usd == Decimal("1991.00")
        terms = terms_from_snapshot(ES_SNAPSHOT)["ES.CME"]
        assert trade_figures(partial, terms).return_on_margin_pct is None

    def test_same_timestamp_round_trip_keeps_its_margin(self):
        instant = _trade("BUY", "1", "5000", "5004", "195.50", 2, 2)
        s = summarise_run(ES_SNAPSHOT, [instant])
        assert s.peak_margin_usd == Decimal("27500")
        assert s.return_on_margin_pct is not None
