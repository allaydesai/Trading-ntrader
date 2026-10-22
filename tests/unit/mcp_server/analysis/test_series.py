"""Equity curve helpers: drawdown and a downsample that keeps the worst moment."""

import numpy as np
import pandas as pd
import pytest

from src.mcp_server.analysis.series import downsample, drawdown, equity_series, max_drawdown

pytestmark = pytest.mark.unit


def test_points_become_a_utc_series_without_duplicate_times():
    series = equity_series(
        [{"time": 0, "value": 1.0}, {"time": 86400, "value": 2.0}, {"time": 86400, "value": 3.0}]
    )
    assert list(series) == [1.0, 3.0]
    assert str(series.index.tz) == "UTC"
    assert equity_series([]).empty


def test_drawdown_is_from_the_running_peak():
    equity = pd.Series([100.0, 120.0, 90.0, 130.0])
    assert list(drawdown(equity)) == pytest.approx([0, 0, -0.25, 0])
    assert max_drawdown(equity) == pytest.approx(-0.25)


def test_downsample_keeps_first_last_and_the_trough():
    values = np.concatenate([np.linspace(100, 200, 5000), [50.0], np.linspace(60, 300, 4999)])
    equity = pd.Series(values, index=pd.date_range("2000-01-01", periods=len(values), tz="UTC"))
    small = downsample(equity, 100)
    assert len(small) <= 100
    assert small.index[0] == equity.index[0] and small.index[-1] == equity.index[-1]
    assert small.min() == 50.0
    assert max_drawdown(small) == pytest.approx(max_drawdown(equity))


def test_a_short_curve_is_returned_whole():
    equity = pd.Series([1.0, 2.0])
    assert downsample(equity, 10) is equity
