"""Strategy catalogue for the MCP: registered strategies plus backtest-only benchmarks."""

from decimal import Decimal

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.strategies import (
    describe_strategy,
    list_strategies,
    resolve_params,
    resolve_strategy,
)

pytestmark = pytest.mark.unit


def test_lists_registered_strategies_and_benchmarks():
    entries = {e["name"]: e for e in list_strategies()}
    assert entries["sma_crossover"]["kind"] == "strategy"
    assert entries["buy_and_hold"]["kind"] == "benchmark"
    assert "sma" in entries["sma_crossover"]["aliases"]


def test_resolves_aliases_to_canonical_name():
    assert resolve_strategy("sma").name == "sma_crossover"
    assert resolve_strategy("buy_and_hold").kind == "benchmark"


def test_unknown_strategy_lists_the_available_ones():
    with pytest.raises(ToolFailure) as exc:
        resolve_strategy("no_such_strategy")
    assert exc.value.code == "unknown_strategy"
    assert "sma_crossover" in exc.value.fix
    assert "buy_and_hold" in exc.value.fix


def test_describe_returns_schema_defaults_and_example():
    info = describe_strategy("sma_crossover")
    assert info["name"] == "sma_crossover"
    assert "fast_period" in info["param_schema"]["properties"]
    assert info["defaults"]["fast_period"] == 10
    assert info["example_request"]["strategy"] == "sma_crossover"


def test_describe_benchmark():
    info = describe_strategy("buy_and_hold")
    assert info["kind"] == "benchmark"
    assert info["defaults"] == {"allocation_pct": 100.0}


def test_resolve_params_fills_defaults_and_coerces():
    params = resolve_params(resolve_strategy("sma_crossover"), {"fast_period": "5"})
    assert params["fast_period"] == 5
    assert "slow_period" in params


def test_resolve_params_rejects_unknown_names():
    with pytest.raises(ToolFailure) as exc:
        resolve_params(resolve_strategy("sma_crossover"), {"fast_perid": 5})
    assert exc.value.code == "invalid_params"
    assert "fast_perid" in exc.value.message
    assert "fast_period" in exc.value.fix


def test_resolve_params_reports_validation_errors_per_field():
    with pytest.raises(ToolFailure) as exc:
        resolve_params(resolve_strategy("buy_and_hold"), {"allocation_pct": 150})
    assert exc.value.code == "invalid_params"
    assert exc.value.details["fields"][0]["field"] == "allocation_pct"


def test_benchmark_params_resolve():
    assert resolve_params(resolve_strategy("buy_and_hold"), {}) == {
        "allocation_pct": Decimal("100")
    }


def test_describe_says_where_each_default_comes_from():
    sources = describe_strategy("sma_crossover")["default_sources"]
    assert sources["fast_period"] == "setting FAST_EMA_PERIOD"
    assert sources["position_size_pct"] == "setting POSITION_SIZE_PCT"
    assert describe_strategy("buy_and_hold")["default_sources"] == {"allocation_pct": "model"}


def test_describe_warns_when_a_setting_overrides_the_schema_default(monkeypatch):
    from src.config import Settings

    monkeypatch.setattr(
        "src.mcp_server.strategies.get_settings", lambda: Settings(fast_ema_period=7)
    )
    info = describe_strategy("sma_crossover")
    assert info["defaults"]["fast_period"] == 7
    note = next(w for w in info["warnings"] if "fast_period" in w)
    assert "FAST_EMA_PERIOD" in note and "7" in note and "10" in note
