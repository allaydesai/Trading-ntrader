"""Regime and sub-period splits computed from stored equity, trades and closes (S4.3)."""

import numpy as np
import pandas as pd
import pytest

from src.mcp_server.analysis.regimes import (
    TradePoint,
    breakdown,
    daily_returns,
    trend_labels,
    volatility_labels,
)

pytestmark = pytest.mark.unit


def _days(start: str, end: str) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end, tz="UTC")


def _equity(days: pd.DatetimeIndex, daily: np.ndarray) -> pd.Series:
    return pd.Series(1_000_000 * np.cumprod(1 + daily), index=days)


def _trade(day: str, pnl: float, closed: bool = True) -> TradePoint:
    return TradePoint(entry=pd.Timestamp(day, tz="UTC"), closed=closed, pnl=pnl)


@pytest.fixture
def run():
    """Up 0.1 %/day through 2018, down 0.1 %/day through 2019."""
    days = _days("2018-01-01", "2019-12-31")
    daily = np.where(days.year == 2018, 0.001, -0.001)
    equity = _equity(days, daily)
    closes = pd.Series(
        np.linspace(100, 200, len(_days("2017-01-02", "2019-12-31"))),
        index=_days("2017-01-02", "2019-12-31"),
    )
    trades = [_trade("2018-03-01", 500), _trade("2018-06-01", -100), _trade("2019-05-01", -50)]
    return equity, trades, closes


def test_years_compound_their_own_days_and_flag_losers(run):
    result = breakdown(*run)
    y2018, y2019 = result["by_year"]
    assert y2018["label"] == "2018" and not y2018["losing"]
    assert y2018["trades"] == 2 and y2018["win_rate"] == 0.5 and y2018["pnl"] == 400
    assert y2019["losing"] and y2019["max_drawdown"] < 0
    returns = daily_returns(run[0])
    total = float(np.prod(1 + returns.to_numpy()) - 1)
    combined = (1 + y2018["return"]) * (1 + y2019["return"]) - 1
    assert combined == pytest.approx(total, abs=1e-5)


def test_sub_periods_cover_every_day_and_their_returns_compound_to_the_total(run):
    cells = breakdown(*run, periods=4)["sub_periods"]
    assert len(cells) == 4
    assert sum(c["days"] for c in cells) == len(daily_returns(run[0]))
    assert [c["losing"] for c in cells] == [False, False, True, True]
    assert sum(c["trades"] for c in cells) == 3


def test_a_trade_on_the_first_day_lands_in_the_first_sub_period(run):
    equity, _, closes = run
    first = [_trade("2018-01-01", 10)]
    cells = breakdown(equity, first, closes)["sub_periods"]
    assert cells[0]["trades"] == 1


def test_trend_uses_the_previous_close():
    days = _days("2020-01-01", "2021-12-31")
    closes = pd.Series(100.0, index=days)
    cross = days[300]
    closes[days >= cross] = 150.0  # jumps above its 200-day average on `cross`
    labels = trend_labels(closes)
    assert labels[cross] == "below_sma200"  # known at the prior close: not yet above
    assert labels[days[301]] == "above_sma200"
    assert labels[days[0]] == "unknown"


def test_volatility_terciles_split_the_run_days_in_three():
    rng = np.random.default_rng(0)
    days = _days("2015-01-01", "2019-12-31")
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(days)))), index=days)
    run_days = days[days.year >= 2017]
    labels = volatility_labels(closes, run_days).reindex(run_days)
    counts = labels.value_counts()
    assert set(counts.index) == {"low", "mid", "high"}
    assert counts.min() > len(run_days) / 3 - 5


def test_too_little_history_is_labelled_unknown_with_a_note(run):
    equity, trades, closes = run
    result = breakdown(equity, trades, closes[closes.index >= "2018-01-01"])
    assert result["notes"] and "200-day" in result["notes"][0]
    assert result["trend"][-1]["label"] == "unknown"


def test_open_trades_count_but_have_no_win_rate():
    days = _days("2018-01-01", "2018-03-30")
    equity = _equity(days, np.full(len(days), 0.001))
    closes = pd.Series(100.0, index=_days("2017-01-02", "2018-03-30"))
    cell = breakdown(equity, [_trade("2018-02-01", 0, closed=False)], closes)["by_year"][0]
    assert cell["trades"] == 1 and cell["win_rate"] is None
