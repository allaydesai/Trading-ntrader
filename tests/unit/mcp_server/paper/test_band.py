"""The expectation band: what a run's own trades say a paper session should look like (S6.2)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.mcp_server.paper.band import (
    ClosedTrade,
    build_band,
    closed_trade,
    pct,
    time_window_counts,
    trade_drawdown,
    trade_window_stats,
    window_size,
    window_stats,
)

pytestmark = pytest.mark.unit

T0 = datetime(2020, 1, 6, 14, 30, tzinfo=timezone.utc)


def _trade(day: int, ret: float, *, held: int = 2, pnl: float | None = None) -> ClosedTrade:
    entry = T0 + timedelta(days=day)
    return ClosedTrade(
        entry_at=entry,
        exit_at=entry + timedelta(days=held),
        ret=ret,
        pnl=ret * 10_000 if pnl is None else pnl,
        commission_frac=0.0005,
    )


def test_a_trade_return_is_net_pnl_over_entry_notional_whatever_the_side():
    long = closed_trade(
        entry_at=T0,
        exit_at=T0 + timedelta(days=1),
        quantity=Decimal("100"),
        entry_price=Decimal("50"),
        profit_loss=Decimal("-25"),
        commission=Decimal("2"),
    )
    assert long is not None
    assert long.ret == pytest.approx(-0.005)
    assert long.commission_frac == pytest.approx(0.0004)
    # A winning short: stored P&L is positive, so the return is too.
    short = closed_trade(
        entry_at=T0,
        exit_at=T0 + timedelta(days=1),
        quantity=Decimal("10"),
        entry_price=Decimal("100"),
        profit_loss=Decimal("30"),
        commission=None,
    )
    assert short is not None and short.ret == pytest.approx(0.03)
    assert short.commission_frac is None


def test_an_open_or_unpriced_position_is_not_a_closed_trade():
    common = dict(entry_at=T0, quantity=Decimal("1"), entry_price=Decimal("10"), commission=None)
    assert closed_trade(exit_at=None, profit_loss=Decimal("1"), **common) is None
    assert closed_trade(exit_at=T0, profit_loss=None, **common) is None


def test_trade_drawdown_is_the_worst_fall_of_the_summed_returns_from_their_peak():
    assert trade_drawdown([0.02, -0.01, -0.03, 0.05, -0.02]) == pytest.approx(-0.04)
    assert trade_drawdown([-0.01, -0.01]) == pytest.approx(-0.02)
    assert trade_drawdown([0.01, 0.02]) == 0.0
    assert trade_drawdown([]) is None


def test_window_stats_counts_a_zero_pnl_trade_as_not_a_win():
    stats = window_stats([_trade(0, 0.02), _trade(3, -0.01), _trade(6, 0.0)])
    assert stats["trades"] == 3
    assert stats["win_rate"] == pytest.approx(1 / 3)
    assert stats["avg_trade_return"] == pytest.approx(0.01 / 3)
    assert stats["trade_drawdown"] == pytest.approx(-0.01)


def test_window_stats_of_nothing_has_a_count_and_no_rates():
    assert window_stats([]) == {
        "trades": 0,
        "win_rate": None,
        "avg_trade_return": None,
        "avg_trade_pnl": None,
        "trade_drawdown": None,
    }


def test_time_windows_step_daily_and_count_exits_inside_each():
    exits = [T0 + timedelta(days=d) for d in (1, 2, 8)]
    counts = time_window_counts(exits, start=T0, end=T0 + timedelta(days=10), days=7)
    # Windows [s, s+7) start on days 0..3 and must end by day 10: [2,9) holds 2 and 8.
    assert counts == [2, 2, 2, 1]
    assert time_window_counts(exits, start=T0, end=T0 + timedelta(days=5), days=7) == []


def test_trade_windows_slide_one_trade_at_a_time_and_time_each_window():
    trades = [_trade(0, 0.01), _trade(7, -0.02), _trade(14, 0.03)]
    windows = trade_window_stats(trades, 2)
    assert [w["trades"] for w in windows] == [2, 2]
    assert windows[0]["win_rate"] == 0.5
    # First entry day 0 to second exit day 9: 9/7 weeks.
    assert windows[0]["weeks"] == pytest.approx(9 / 7)
    assert trade_window_stats(trades, 4) == []


def test_percentiles_are_5_50_95_and_none_without_values():
    band = pct(list(range(101)))
    assert band == {"p5": 5.0, "p50": 50.0, "p95": 95.0}
    assert pct([None, None]) is None


def _weekly_trades(weeks: int) -> list[ClosedTrade]:
    return [_trade(7 * w, 0.01 if w % 3 else -0.02) for w in range(weeks)]


def test_the_band_starts_at_the_first_trade_not_at_the_run_start():
    # A long warm-up before the first trade must not dilute the trade counts.
    trades = _weekly_trades(60)
    end = trades[-1].exit_at + timedelta(days=1)
    band = build_band(trades, span_end=end, days=28, n=10)
    assert band["status"] == "ok"
    assert band["span"]["start"] == trades[0].entry_at
    assert band["time_windows"]["trades"]["p50"] == pytest.approx(4, abs=1)
    assert band["time_windows"]["trades"]["p5"] >= 3
    trade_axis = band["trade_windows"]
    assert trade_axis["n"] == 10 and trade_axis["count"] == 51
    assert 0 < trade_axis["win_rate"]["p5"] <= trade_axis["win_rate"]["p95"] < 1
    assert band["overall"]["trades"] == 60
    assert band["overall"]["trades_per_week"] == pytest.approx(1.0, rel=0.05)


def test_too_few_windows_make_a_thin_band_and_no_trades_none():
    trades = _weekly_trades(12)
    thin = build_band(trades, span_end=trades[-1].exit_at, days=28, n=10)
    assert thin["status"] == "thin"
    assert "windows" in thin["note"]
    empty = build_band([], span_end=T0, days=28, n=10)
    assert empty["status"] == "unavailable"
    assert empty["time_windows"]["trades"] is None


def test_the_trade_window_is_capped_so_the_band_keeps_enough_windows():
    # 100 run trades leave 20 windows of at most 81 trades.
    assert window_size(30, run_trades=100, floor=5) == 30
    assert window_size(95, run_trades=100, floor=5) == 81
    assert window_size(2, run_trades=100, floor=5) == 5


def test_a_run_too_small_for_any_cap_keeps_the_wanted_window():
    # 20 run trades cannot give 20 windows of 5: the band stays thin and says so.
    assert window_size(12, run_trades=20, floor=5) == 12
