"""Sleeves: usable stored curves and the regime table that lines them up (S9.2, S9.4)."""

from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest

from src.mcp_server.analysis import sleeves
from src.mcp_server.analysis.regimes import UNKNOWN, return_cells
from src.mcp_server.analysis.sleeves import regime_table, usable_curve

pytestmark = pytest.mark.unit


def _days(start: str, end: str) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end, tz="UTC")


class _Points:
    def __init__(self, points):
        self.points = points

    def find_points(self, run_id):
        return self.points


@pytest.fixture
def run():
    return SimpleNamespace(run_id=uuid4(), config_snapshot={"metrics_basis": "mark_to_market"})


def test_usable_curve_needs_points_and_a_mark_to_market_basis(monkeypatch, run):
    points = [{"time": 1_600_000_000, "value": 100.0}, {"time": 1_600_086_400, "value": 101.0}]
    monkeypatch.setattr(sleeves, "SyncEquityCurveRepository", lambda session: _Points(points))
    assert list(usable_curve(object(), run)) == [100.0, 101.0]
    run.config_snapshot = {"metrics_basis": "realised"}
    assert usable_curve(object(), run) is None
    run.config_snapshot = {"metrics_basis": "mark_to_market"}
    monkeypatch.setattr(sleeves, "SyncEquityCurveRepository", lambda session: _Points([]))
    assert usable_curve(object(), run) is None


def test_return_cells_keep_only_the_return_fields():
    days = _days("2018-01-01", "2019-12-31")
    returns = pd.Series(np.where(days.year == 2018, 0.001, -0.001), index=days)
    labels = pd.Series([str(d.year) for d in days], index=days)
    cells = return_cells(returns, labels, ["2018", "2019"])
    assert [c["label"] for c in cells] == ["2018", "2019"]
    assert set(cells[0]) == {"label", "days", "return", "max_drawdown"}
    assert cells[0]["return"] > 0 > cells[1]["return"]


def _frame() -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    days = _days("2018-01-01", "2019-12-31")
    a = pd.Series(np.where(days.year == 2018, 0.001, -0.001), index=days)
    frame = pd.DataFrame({"a": a, "b": -a})
    return frame, {"total": (frame["a"] + frame["b"]) / 2}


def test_regime_table_lines_every_column_up_on_the_same_days():
    frame, totals = _frame()
    warm = _days("2017-01-02", "2019-12-31")
    closes = pd.Series(np.linspace(100, 200, len(warm)), index=warm)
    table = regime_table(frame, totals, closes)
    years = table["by_year"]
    assert [row["label"] for row in years] == ["2018", "2019"]
    assert years[0]["days"] == len(_days("2018-01-01", "2018-12-31"))
    assert set(years[0]["return"]) == {"a", "b", "total"} == set(years[0]["max_drawdown"])
    assert years[0]["return"]["a"] > 0 > years[0]["return"]["b"]
    assert years[0]["return"]["total"] == pytest.approx(0.0, abs=1e-9)
    assert {row["label"] for row in table["trend"]} <= {"above_sma200", "below_sma200", UNKNOWN}
    assert sum(row["days"] for row in table["volatility"]) == len(frame)
    assert table["notes"] == []


def test_regime_table_notes_days_without_a_trend_label():
    frame, totals = _frame()
    closes = pd.Series(np.linspace(100, 200, len(frame)), index=frame.index)
    table = regime_table(frame, totals, closes)
    assert any(row["label"] == UNKNOWN for row in table["trend"])
    assert any("200-day" in note for note in table["notes"])
