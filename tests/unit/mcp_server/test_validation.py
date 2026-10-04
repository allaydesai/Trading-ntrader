"""validate_config: resolve the request and check data coverage without running (S1.4)."""

from datetime import date

import pytest

from src.mcp_server import validation
from src.mcp_server.errors import ToolFailure
from src.mcp_server.request import BacktestSpec

pytestmark = pytest.mark.unit

COVERAGE = {
    "symbol": "QQQ",
    "catalog": "firstrate-etf",
    "nautilus_id": "QQQ.NASDAQ",
    "backtestable": True,
    "timeframes": {
        "1-DAY": {
            "start": "2000-01-03T09:30:00-05:00",
            "end": "2026-05-01T00:00:00-04:00",
            "bars": 1,
        }
    },
}


@pytest.fixture
def availability(monkeypatch):
    state = {"result": COVERAGE}

    def fake(symbol, catalog):
        if isinstance(state["result"], ToolFailure):
            raise state["result"]
        return state["result"]

    monkeypatch.setattr(validation, "catalog_availability", fake)
    return state


def _spec(**overrides):
    fields = dict(
        strategy="sma_crossover",
        symbol="QQQ",
        start=date(2000, 1, 3),
        end=date(2015, 12, 31),
        catalog="firstrate-etf",
    )
    fields.update(overrides)
    return BacktestSpec(**fields)


def test_valid_request(availability):
    result = validation.validate(_spec(), default_catalog="")
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["resolved"]["strategy"] == "sma_crossover"
    assert len(result["resolved"]["config_hash"]) == 64
    assert result["coverage"]["timeframes"]["1-DAY"]["bars"] == 1


def test_reports_strategy_and_data_problems_together(availability):
    availability["result"] = ToolFailure("symbol_not_in_catalog", "nope", fix="import it")
    result = validation.validate(_spec(strategy="nope"), default_catalog="")
    codes = [e["code"] for e in result["errors"]]
    assert result["ok"] is False
    assert codes == ["unknown_strategy", "symbol_not_in_catalog"]


def test_timeframe_without_bars(availability):
    result = validation.validate(_spec(timeframe="1-HOUR"), default_catalog="")
    assert [e["code"] for e in result["errors"]] == ["no_bars_for_timeframe"]
    assert "1-DAY" in result["errors"][0]["fix"]


def test_window_entirely_outside_coverage(availability):
    result = validation.validate(
        _spec(start=date(1990, 1, 1), end=date(1995, 1, 1)), default_catalog=""
    )
    assert [e["code"] for e in result["errors"]] == ["window_outside_coverage"]


def test_window_partly_outside_coverage_is_a_warning(availability):
    result = validation.validate(_spec(start=date(1995, 1, 1)), default_catalog="")
    assert result["ok"] is True
    assert "1995-01-01" in result["warnings"][0]


def test_not_backtestable(availability):
    availability["result"] = {**COVERAGE, "backtestable": False, "reason": "venue unresolved"}
    result = validation.validate(_spec(), default_catalog="")
    assert result["errors"][0]["code"] == "not_backtestable"
    assert result["errors"][0]["message"] == "venue unresolved"


def test_an_inexact_start_warns_that_bars_may_begin_elsewhere(availability):
    span = {"start": "2000-01-03T00:00:00+00:00", "end": "2026-05-01T00:00:00+00:00", "bars": 9}
    availability["result"] = {
        **COVERAGE,
        "timeframes": {
            "1-DAY": {**span, "start_is_exact": True},
            "1-MINUTE": {**span, "start_is_exact": False},
        },
    }
    intraday = validation.validate(_spec(timeframe="1-MINUTE"), default_catalog="")
    daily = validation.validate(_spec(), default_catalog="")
    assert intraday["ok"] is True
    assert any("may start earlier or later" in w for w in intraday["warnings"])
    assert daily["warnings"] == []


def test_an_exact_intraday_start_needs_no_warning(availability):
    span = {"start": "2000-01-03T00:00:00+00:00", "end": "2026-05-01T00:00:00+00:00", "bars": 9}
    availability["result"] = {
        **COVERAGE,
        "timeframes": {"1-MINUTE": {**span, "start_is_exact": True}},
    }
    result = validation.validate(_spec(timeframe="1-MINUTE"), default_catalog="")
    assert result["warnings"] == []


def test_unknown_start_does_not_crash(availability):
    span = {"start": None, "end": "2026-05-01T00:00:00+00:00", "bars": 9}
    availability["result"] = {**COVERAGE, "timeframes": {"1-DAY": span}}
    result = validation.validate(_spec(), default_catalog="")
    assert result["ok"] is True
    late = validation.validate(
        _spec(start=date(2027, 1, 1), end=date(2027, 2, 1)), default_catalog=""
    )
    assert late["errors"][0]["code"] == "window_outside_coverage"
