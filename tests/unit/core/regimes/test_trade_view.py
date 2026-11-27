"""Trade view, alignment and cross-run correlation by regime bucket (design.md 5.7, 6).

Descriptive tables only: no verdict rule reads them in phase 2. Closed trades are bucketed by
the label in force on their entry day; open trades are counted apart.
"""

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from src.core.regimes.attribution import TradeRecord
from src.core.regimes.trade_view import alignment, correlation, trade_view

pytestmark = pytest.mark.unit


def _trade(pnl: float, side: str = "BUY", closed: bool = True) -> TradeRecord:
    at = datetime(2020, 1, 2, 5, tzinfo=timezone.utc)
    return TradeRecord(at, at if closed else None, side, pnl)


def test_closed_trades_by_entry_bucket_reconcile_and_open_ones_are_apart():
    trades = [_trade(10), _trade(-5), _trade(20), _trade(-1), _trade(0, closed=False), _trade(3)]
    buckets = ["up", "up", "down", None, "up", "down"]
    view = trade_view(trades, buckets, ("up", "down"))
    up, down = view["rows"]
    assert (up["bucket"], up["trades"], down["trades"]) == ("up", 2, 2)
    assert view["unlabelled"]["trades"] == 1
    assert view["open_trades"] == 1
    assert view["closed_trades"] == 5 == up["trades"] + down["trades"] + 1
    assert up["win_rate"] == 0.5
    assert (up["avg_win"], up["avg_loss"], up["expectancy"]) == (10.0, -5.0, 2.5)
    assert up["profit_factor"] == 2.0
    assert down["profit_factor"] is None  # nothing lost


def test_alignment_tags_with_and_counter_trend_and_keeps_unclassified_apart():
    trades = [
        _trade(10, "BUY"),  # BUY under up: with
        _trade(-4, "SELL"),  # SELL under up: counter
        _trade(6, "SELL"),  # SELL under down: with
        _trade(-2, "BUY"),  # BUY under down: counter
        _trade(1, "BUY"),  # no label: unclassified
        _trade(0, "BUY", closed=False),  # open: not in any row
    ]
    rows = alignment(trades, ["up", "up", "down", "down", None, "up"])
    by_name = {r["group"]: r for r in rows}
    assert by_name["with_trend"]["trades"] == 2 and by_name["with_trend"]["expectancy"] == 8.0
    assert by_name["counter_trend"]["trades"] == 2
    assert by_name["counter_trend"]["expectancy"] == -3.0
    assert by_name["unclassified"]["trades"] == 1


def _series(values, days):
    return pd.Series(values, index=days, dtype=float)


def test_correlation_overall_and_per_bucket_on_shared_days():
    days = list(pd.bdate_range("2020-01-02", periods=200).date)
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.01, 200)
    b = np.where(np.arange(200) < 100, a, -a) + rng.normal(0, 0.001, 200)
    buckets = {d: ("up" if i < 100 else "down") for i, d in enumerate(days)}
    out = correlation(
        {"a": _series(a, days), "b": _series(b[:150], days[:150])}, buckets, ("up", "down"), 30
    )
    assert out["shared_days"] == 150
    assert out["overall"]["a"]["b"] == out["overall"]["b"]["a"]
    up, down = out["buckets"]
    assert up["bucket"] == "up" and up["days"] == 100 and up["matrix"]["a"]["b"] > 0.95
    assert down["days"] == 50 and down["matrix"]["a"]["b"] < -0.95
    thin = correlation(
        {"a": _series(a, days), "b": _series(b[:150], days[:150])}, buckets, ("up", "down"), 60
    )
    assert thin["buckets"][1]["matrix"] is None and thin["buckets"][1]["insufficient"] is True


def test_correlation_of_a_flat_run_is_null():
    days = [date(2020, 1, d) for d in range(2, 30)]
    flat = _series(np.zeros(len(days)), days)
    moving = _series(np.linspace(-0.01, 0.01, len(days)), days)
    out = correlation({"flat": flat, "moving": moving}, {d: "up" for d in days}, ("up",), 5)
    assert out["overall"]["flat"]["moving"] is None
