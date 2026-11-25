"""Exact crypto peak exposure from a run's saved fill series (follow-up to crypto M5).

``crypto_pairs[<id>].position_series`` lists, per pair, every fill in engine order as
``[seq, ts_ns, position_id, position_qty, fill_px, commission_usd]``: the signed quantity of
the fill's position after it (the venue is ``HEDGING``), the fill price and the USD charged.
Replaying it gives the cost-basis notional held after each fill and the USD balance (starting
capital plus realised P&L net of costs), so peak notional and peak exposure are exact. Runs
saved without the series keep the M5 upper bound.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.services.crypto import exposure, returns

pytestmark = pytest.mark.unit

BTC, ETH = "BTC/USD.KRAKEN", "ETH/USD.KRAKEN"
T0 = datetime(2024, 3, 1, tzinfo=timezone.utc)
NS = 3_600 * 10**9


def _ns(hour: int) -> int:
    return int(T0.timestamp()) * 10**9 + hour * NS


# Scripted fills, capital USD 100,000 (hand figures beside each: holdings, notional, balance):
#  0 h1 BTC buy 1 @ 20,000, fee 10   BTC 1 @ 20,000            N 20,000   E 99,990
#  1 h1 ETH buy 5 @ 1,000, fee 5     ETH 5 @ 1,000             N 25,000   E 99,985
#  2 h2 BTC buy 1 @ 30,000, fee 15   BTC 2 @ 25,000 (scale in) N 55,000   E 99,970  <- peak
#  3 h3 BTC sell 1 @ 28,000, fee 14  BTC 1, realised +3,000    N 30,000   E 102,956
#  4 h3 ETH buy 5 @ 1,400, fee 7     ETH 10 @ 1,200            N 37,000   E 102,949
#  5 h4 ETH sell 4 @ 1,500, fee 6    ETH 6, realised +1,200    N 32,200   E 104,143
#  6 h5 BTC sell 1 @ 26,000, fee 13  BTC flat, realised +1,000 N 7,200    E 105,130
#  7 h6 ETH sell 6 @ 1,100, fee 7    ETH flat, realised -600   N 0        E 104,523
SERIES = {
    BTC: [
        [0, _ns(1), "P", "1", "20000", "10"],
        [2, _ns(2), "P", "2", "30000", "15"],
        [3, _ns(3), "P", "1", "28000", "14"],
        [6, _ns(5), "P", "0", "26000", "13"],
    ],
    ETH: [
        [1, _ns(1), "P", "5", "1000", "5"],
        [4, _ns(3), "P", "10", "1400", "7"],
        [5, _ns(4), "P", "6", "1500", "6"],
        [7, _ns(6), "P", "0", "1100", "7"],
    ],
}
EXACT_NOTIONAL = Decimal("55000")
EXACT_EXPOSURE = Decimal("55000") / Decimal("99970")


def _pair(base: str, series=None) -> dict:
    pair = {
        "base": base,
        "quote": "USD",
        "fee_tier": 2,
        "maker_pct": "0.30",
        "taker_pct": "0.60",
        "typical_spread_bps": "2",
        "size_precision": 8,
        "price_precision": 4,
        "costs_usd": {"commission": "77.00", "commission_taker": "77.00", "spread": "0"},
    }
    if series is not None:
        pair["position_series"] = series
    return pair


def _snapshot(with_series: bool) -> dict:
    return {
        "account_currency": "USD",
        "fee_tier": 2,
        "periods_per_year": 365,
        "crypto_pairs": {
            BTC: _pair("BTC", SERIES[BTC] if with_series else None),
            ETH: _pair("ETH", SERIES[ETH] if with_series else None),
        },
    }


def _trade(iid, qty, entry, exit_, pnl, start_h, end_h):
    # What the trade rows keep: the position's largest quantity, its average open price
    return {
        "instrument_id": iid,
        "order_side": "BUY",
        "quantity": Decimal(qty),
        "entry_price": Decimal(entry),
        "exit_price": Decimal(exit_),
        "profit_loss": Decimal(pnl),
        "entry_timestamp": T0 + timedelta(hours=start_h),
        "exit_timestamp": T0 + timedelta(hours=end_h),
    }


TRADES = [
    _trade(BTC, "2", "25000", "27000", "3948", 1, 5),
    _trade(ETH, "10", "1200", "1266.6667", "582", 1, 6),
]


class TestReplay:
    def test_peaks_are_exact_over_partial_adds_and_reductions_on_two_pairs(self):
        notional, peak = exposure.peaks(SERIES, Decimal("100000"))
        assert notional == EXACT_NOTIONAL
        assert peak == EXACT_EXPOSURE

    def test_without_capital_the_exposure_is_unknown(self):
        assert exposure.peaks(SERIES, None) == (EXACT_NOTIONAL, None)

    def test_a_run_with_no_fills_has_no_peaks(self):
        assert exposure.peaks({BTC: [], ETH: []}, Decimal("100000")) == (None, None)

    def test_a_round_trip_inside_one_bar_counts_while_it_was_held(self):
        series = {BTC: [[0, _ns(1), "P", "1", "20000", "0"], [1, _ns(1), "P", "0", "21000", "0"]]}
        assert exposure.peaks(series, Decimal("100000")) == (Decimal("20000"), Decimal("0.2"))

    def test_fills_are_replayed_in_engine_order_across_pairs(self):
        # ETH sold (seq 0) before BTC bought (seq 1) at one timestamp: never held together
        series = {
            BTC: [[1, _ns(2), "P", "1", "20000", "0"]],
            ETH: [[0, _ns(2), "P", "0", "1000", "0"]],
        }
        series[ETH].insert(0, [-1, _ns(1), "P", "10", "1000", "0"])
        assert exposure.peaks(series, Decimal("100000"))[0] == Decimal("20000")

    def test_hedged_positions_on_one_pair_keep_their_own_cost(self):
        # Two BTC positions (100 and 200); closing the second at 150 realises -50, and the
        # first is left at its own 100 cost (a pair-wide average would say 150 and 0)
        series = {
            BTC: [
                [0, _ns(1), "A", "1", "100", "0"],
                [1, _ns(2), "B", "1", "200", "0"],
                [2, _ns(3), "B", "0", "150", "0"],
            ]
        }
        last = exposure.replay(series, Decimal("1000"))[-1]
        assert (last.notional, last.balance) == (Decimal("100"), Decimal("950"))
        assert exposure.peaks(series, Decimal("1000"))[0] == Decimal("300")

    def test_replay_tracks_the_balance_after_every_fill(self):
        balances = [s.balance for s in exposure.replay(SERIES, Decimal("100000"))]
        assert balances == [
            Decimal(b)
            for b in ("99990", "99985", "99970", "102956", "102949", "104143", "105130", "104523")
        ]

    def test_each_fills_realised_pnl_is_booked_in_whole_cents(self):
        # 0.12345678 BTC bought at 78,000.0001 and sold at 79,000: P&L 123.456767... -> 123.46
        series = {
            BTC: [
                [0, _ns(1), "P", "0.12345678", "78000.0001", "0"],
                [1, _ns(2), "P", "0", "79000", "0"],
            ]
        }
        assert exposure.replay(series, Decimal("1000"))[-1].balance == Decimal("1123.46")

    def test_has_series_needs_every_pair_to_carry_one(self):
        assert exposure.has_series({BTC: SERIES[BTC], ETH: []})
        assert not exposure.has_series({BTC: SERIES[BTC], ETH: None})
        assert not exposure.has_series({})


class TestSummary:
    def test_a_run_with_the_series_reports_the_exact_figure(self):
        s = returns.summarise_run(_snapshot(True), TRADES, Decimal("100000"))
        assert s.exposure_exact is True
        assert s.peak_notional_usd == EXACT_NOTIONAL
        assert s.peak_exposure == EXACT_EXPOSURE

    def test_the_old_upper_bound_differs_on_the_same_run(self):
        # Trade rows count BTC at 2 x 25,000 and ETH at 10 x 1,200 from hour 1
        old = returns.summarise_run(_snapshot(False), TRADES, Decimal("100000"))
        assert old.exposure_exact is False
        assert old.peak_notional_usd == Decimal("62000")
        assert old.peak_exposure == Decimal("0.62")
        assert old.peak_exposure > EXACT_EXPOSURE

    def test_report_fields_and_note_name_the_basis(self):
        exact = returns.report_fields(returns.summarise_run(_snapshot(True), TRADES, 100000))
        old = returns.report_fields(returns.summarise_run(_snapshot(False), TRADES, 100000))
        assert exact["peak_exposure_basis"] == "exact"
        assert exact["peak_notional_usd"] == "55000.00"
        assert exact["peak_exposure_pct"] == "55.0165"
        assert "upper bound" not in exact["note"]
        assert "exact" in exact["note"]
        assert old["peak_exposure_basis"] == "upper_bound"
        assert old["peak_exposure_pct"] == "62.0000"
        assert "upper bound when a position scales in or round-trips" in old["note"]

    def test_the_note_without_a_series_is_the_m5_note_unchanged(self):
        assert returns.note(2) == returns.note(2, exact=False)
        assert returns.note(2).endswith(
            "an upper bound when a position scales in or round-trips within one bar."
        )
