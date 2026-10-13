"""Sliced walk-forward: windows, per-window selection and efficiency (S5.3)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.core.robustness.walk_forward import (
    Window,
    annualised_return,
    evaluate,
    slice_metrics,
    windows,
)

pytestmark = pytest.mark.unit


def test_rolling_windows_step_by_the_out_of_sample_length():
    found = windows(date(2000, 1, 1), date(2010, 1, 1), is_years=4, oos_years=2, mode="rolling")
    assert [(w.is_start, w.oos_start, w.oos_end) for w in found] == [
        (date(2000, 1, 1), date(2004, 1, 1), date(2005, 12, 31)),
        (date(2002, 1, 1), date(2006, 1, 1), date(2007, 12, 31)),
        (date(2004, 1, 1), date(2008, 1, 1), date(2009, 12, 31)),
    ]
    assert found[0].is_end == date(2003, 12, 31) and found[0].index == 1


def test_anchored_windows_keep_the_first_start():
    found = windows(date(2000, 1, 1), date(2010, 1, 1), is_years=4, oos_years=2, mode="anchored")
    assert {w.is_start for w in found} == {date(2000, 1, 1)}
    assert [w.oos_start for w in found] == [date(2004, 1, 1), date(2006, 1, 1), date(2008, 1, 1)]


def test_fewer_than_two_windows_is_refused():
    with pytest.raises(ValueError):
        windows(date(2000, 1, 1), date(2005, 1, 1), is_years=4, oos_years=2, mode="rolling")
    with pytest.raises(ValueError):
        windows(date(2000, 1, 1), date(2010, 1, 1), is_years=4, oos_years=2, mode="sideways")


def test_annualised_return_compounds_over_trading_days():
    assert annualised_return(100.0, 121.0, days=504) == pytest.approx(0.1)


def _equity(start: date, end: date, daily: float, base: float = 1000.0) -> pd.Series:
    index = pd.bdate_range(start, end, tz="UTC")
    noise = np.random.default_rng(len(index)).normal(0, 0.002, len(index))
    return pd.Series(base * np.cumprod(1 + daily + noise), index=index)


def test_slice_metrics_bases_on_the_last_point_before_the_window():
    equity = _equity(date(2000, 1, 3), date(2001, 12, 31), 0.001)
    sliced = slice_metrics(equity, date(2001, 1, 1), date(2001, 12, 31), initial=1000.0)
    before = float(equity[: pd.Timestamp("2000-12-31", tz="UTC")].iloc[-1])
    assert sliced["base"] == pytest.approx(before)
    assert sliced["days"] == len(pd.bdate_range("2001-01-01", "2001-12-31"))
    assert sliced["annualised_return"] > 0 and sliced["sharpe_ratio"] is not None
    first = slice_metrics(equity, date(2000, 1, 1), date(2000, 6, 30), initial=1000.0)
    assert first["base"] == 1000.0  # nothing before the window: the run's initial capital


def test_evaluate_picks_the_best_in_sample_cell_per_window_and_reports_efficiency():
    span = (date(2000, 1, 3), date(2009, 12, 31))
    early = _equity(span[0], date(2003, 12, 31), 0.0015)
    cells = {
        "steady": _equity(*span, 0.0008),
        "lucky_early": pd.concat(
            [early, _equity(date(2004, 1, 1), span[1], -0.0005, base=float(early.iloc[-1]))]
        ),
    }
    found = windows(span[0], span[1], is_years=4, oos_years=2, mode="rolling")
    trades = {
        "steady": [pd.Timestamp(d, tz="UTC") for d in pd.bdate_range(*span, freq="7B")],
        "lucky_early": [],
    }
    result = evaluate(
        cells,
        found,
        select_by="sharpe_ratio",
        initial={"steady": 1000.0, "lucky_early": 1000.0},
        trades=trades,
    )
    assert [w["chosen"] for w in result["windows"]] == ["lucky_early", "steady"]
    assert result["windows"][0]["out_of_sample"]["annualised_return"] < 0
    assert result["windows"][0]["out_of_sample"]["trades"] == 0
    assert result["windows"][1]["out_of_sample"]["trades"] > 15
    assert result["negative_windows"] == 1
    assert result["efficiency"] is not None and result["windows_used"] == 2
    assert result["method"] == "sliced" and result["select_by"] == "sharpe_ratio"


def test_windows_with_a_non_positive_in_sample_return_are_excluded_from_efficiency():
    span = (date(2000, 1, 3), date(2009, 12, 31))
    cells = {"loser": _equity(*span, -0.0005)}
    found = windows(span[0], span[1], is_years=4, oos_years=2, mode="rolling")
    result = evaluate(cells, found, select_by="sharpe_ratio", initial=1000.0, trades={"loser": []})
    assert result["efficiency"] is None
    assert result["excluded_windows"] == [1, 2]


def test_window_dataclass_carries_its_dates():
    w = Window(1, date(2000, 1, 1), date(2003, 12, 31), date(2004, 1, 1), date(2005, 12, 31))
    assert w.to_dict()["oos_end"] == "2005-12-31"
