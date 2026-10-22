"""Mark-to-market equity and the risk metrics computed from it.

Risk metrics used to come from realised position returns, so a position held
through an 83 % drawdown and closed at the end reported a drawdown of zero.
"""

import math

import pandas as pd
import pytest

from src.core.mark_to_market import Fill, daily_equity, risk_metrics

pytestmark = pytest.mark.unit

DAY_NS = 86_400 * 1_000_000_000
START = 1_600_000_000 * 1_000_000_000 // DAY_NS * DAY_NS  # a UTC midnight


def _closes(*prices: float, step_ns: int = DAY_NS) -> list[tuple[int, float]]:
    return [(START + i * step_ns, p) for i, p in enumerate(prices)]


def test_held_position_is_marked_at_each_close():
    closes = {"X": _closes(100, 50, 200)}
    fills = [
        Fill(START, "X", 10, 100.0, 0.0),
        Fill(START + 2 * DAY_NS, "X", -10, 200.0, 0.0),
    ]

    equity = daily_equity(1000.0, fills, closes)

    assert list(equity) == [1000.0, 500.0, 2000.0]


def test_drawdown_of_a_held_position_is_the_price_drawdown():
    closes = {"X": _closes(100, 50, 200)}
    fills = [Fill(START, "X", 10, 100.0, 0.0)]

    metrics = risk_metrics(daily_equity(1000.0, fills, closes), 1000.0)

    assert metrics.max_drawdown == pytest.approx(-0.5)


def test_position_still_open_at_the_end_counts_its_unrealised_gain():
    closes = {"X": _closes(100, 110)}
    fills = [Fill(START, "X", 10, 100.0, 0.0)]

    equity = daily_equity(1000.0, fills, closes)

    assert equity.iloc[-1] == pytest.approx(1100.0)


def test_commission_reduces_equity_when_it_is_paid():
    closes = {"X": _closes(100, 100)}
    fills = [Fill(START, "X", 10, 100.0, 5.0)]

    equity = daily_equity(1000.0, fills, closes)

    assert list(equity) == [995.0, 995.0]


def test_multiplier_scales_the_holding():
    closes = {"X": _closes(100, 110)}
    fills = [Fill(START, "X", 1, 100.0, 0.0, multiplier=50.0)]

    equity = daily_equity(10_000.0, fills, closes)

    assert equity.iloc[-1] == pytest.approx(10_500.0)


def test_no_trades_is_flat_at_the_starting_balance():
    equity = daily_equity(1000.0, [], {"X": _closes(100, 50, 200)})
    metrics = risk_metrics(equity, 1000.0)

    assert list(equity) == [1000.0, 1000.0, 1000.0]
    assert metrics.max_drawdown == 0.0
    assert metrics.sharpe_ratio is None
    assert metrics.volatility is None


def test_intraday_bars_reduce_to_one_point_per_day():
    hour = DAY_NS // 24
    closes = {"X": _closes(100, 90, 120, 80, step_ns=12 * hour)}
    fills = [Fill(START, "X", 1, 100.0, 0.0)]

    equity = daily_equity(1000.0, fills, closes)

    assert len(equity) == 2
    assert list(equity) == [990.0, 980.0]  # last value of each day


def test_two_instruments_are_summed():
    closes = {"X": _closes(100, 110), "Y": _closes(10, 5)}
    fills = [Fill(START, "X", 1, 100.0, 0.0), Fill(START, "Y", 10, 10.0, 0.0)]

    equity = daily_equity(1000.0, fills, closes)

    assert equity.iloc[-1] == pytest.approx(1000.0 + 10.0 - 50.0)


def test_a_fill_for_an_instrument_without_bars_is_refused():
    with pytest.raises(ValueError, match="no bars"):
        daily_equity(1000.0, [Fill(START, "Z", 1, 1.0, 0.0)], {"X": _closes(100)})


def test_first_day_return_is_measured_from_the_starting_balance():
    equity = pd.Series([900.0, 900.0], index=pd.to_datetime([START, START + DAY_NS], utc=True))

    metrics = risk_metrics(equity, 1000.0)

    assert metrics.max_drawdown == pytest.approx(-0.1)


def test_sharpe_sortino_and_volatility_are_annualised_on_252_days():
    index = pd.to_datetime([START + i * DAY_NS for i in range(4)], utc=True)
    equity = pd.Series([1010.0, 999.9, 1019.898, 1019.898], index=index)
    returns = pd.Series([0.01, -0.01, 0.02, 0.0])
    downside = math.sqrt((returns[returns < 0] ** 2).sum() / len(returns))

    metrics = risk_metrics(equity, 1000.0)

    assert metrics.volatility == pytest.approx(returns.std() * math.sqrt(252))
    assert metrics.sharpe_ratio == pytest.approx(returns.mean() / returns.std() * math.sqrt(252))
    assert metrics.sortino_ratio == pytest.approx(returns.mean() / downside * math.sqrt(252))


def test_empty_equity_has_no_metrics():
    metrics = risk_metrics(pd.Series(dtype=float), 1000.0)

    assert metrics.max_drawdown is None
    assert metrics.sharpe_ratio is None
