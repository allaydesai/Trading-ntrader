"""The Monte Carlo trade axis of the expectation band (phase 4, S6.2)."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.mcp_server.paper.band import MIN_WINDOWS, ClosedTrade, build_band, window_stats
from src.mcp_server.paper.mc_band import build_mc_band, resampled_windows

pytestmark = pytest.mark.unit

T0 = datetime(2020, 1, 6, 14, 30, tzinfo=timezone.utc)


def _trade(day: int, ret: float, *, held: int = 2) -> ClosedTrade:
    entry = T0 + timedelta(days=day)
    return ClosedTrade(
        entry_at=entry,
        exit_at=entry + timedelta(days=held),
        ret=ret,
        pnl=ret * 10_000,
        commission_frac=0.0005,
    )


def _weekly_trades(weeks: int) -> list[ClosedTrade]:
    return [_trade(7 * w, 0.01 if w % 3 else -0.02) for w in range(weeks)]


def test_resampled_windows_use_the_rolling_bands_statistics():
    trades = _weekly_trades(30)
    windows = resampled_windows(trades, 10, iterations=500, seed=1)
    assert all(len(v) == 500 for v in windows.values())
    assert np.all((windows["win_rate"] >= 0) & (windows["win_rate"] <= 1))
    assert np.all(windows["trade_drawdown"] <= 0)
    # A window drawn n times from the same trade has that trade's stats exactly.
    one = [trades[1]] * 1
    single = resampled_windows(one, 4, iterations=3, seed=0)
    stats = window_stats([trades[1]] * 4)
    assert single["win_rate"].tolist() == [stats["win_rate"]] * 3
    assert single["avg_trade_return"].tolist() == pytest.approx([stats["avg_trade_return"]] * 3)


def test_the_band_keeps_the_rolling_bands_keys_and_time_axis():
    trades = _weekly_trades(60)
    end = trades[-1].exit_at + timedelta(days=1)
    rolling = build_band(trades, span_end=end, days=28, n=10)
    mc = build_mc_band(trades, span_end=end, days=28, n=10, iterations=300, seed=2)
    assert set(mc) == set(rolling)
    assert mc["time_windows"] == rolling["time_windows"]
    assert mc["overall"] == rolling["overall"]
    assert mc["span"] == rolling["span"]
    axis = mc["trade_windows"]
    assert axis["method"] == "monte_carlo" and axis["n"] == 10
    assert axis["count"] == 300 and axis["distinct_trades"] == 60
    assert set(axis["win_rate"]) == {"p5", "p50", "p95"}
    assert 0 < axis["win_rate"]["p5"] <= axis["win_rate"]["p95"] <= 1
    assert axis["trade_drawdown"]["p5"] <= axis["trade_drawdown"]["p95"] <= 0
    assert axis["weeks_to_n_trades"]["p50"] == pytest.approx(10, abs=2)
    assert mc["status"] == "ok" and "Monte Carlo" in mc["basis"]


def test_same_seed_same_band_and_a_window_longer_than_the_run_still_works():
    trades = _weekly_trades(25)
    end = trades[-1].exit_at
    a = build_mc_band(trades, span_end=end, days=28, n=40, iterations=200, seed=5)
    b = build_mc_band(trades, span_end=end, days=28, n=40, iterations=200, seed=5)
    assert a == b
    assert a["trade_windows"]["count"] == 200


def test_thin_is_about_the_runs_closed_trades_not_the_iterations():
    trades = _weekly_trades(MIN_WINDOWS - 1)
    thin = build_mc_band(trades, span_end=trades[-1].exit_at + timedelta(days=60), days=7, n=5)
    assert thin["status"] == "thin" and "closed trades" in thin["note"]
    assert thin["trade_windows"]["count"] == 2000
    empty = build_mc_band([], span_end=T0, days=28, n=10)
    assert empty["status"] == "unavailable"
    assert empty["trade_windows"]["win_rate"] is None and empty["trade_windows"]["count"] == 0
