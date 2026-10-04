"""A session's stored spec against the frozen candidate it should trade (silent-drift guard)."""

import pytest

from src.mcp_server.paper.specs import spec_differences

pytestmark = pytest.mark.unit

FROZEN = dict(
    strategy="connors_rsi_mean_rev",
    symbol="QQQ",
    timeframe="1-DAY",
    params={"rsi_period": 2, "trade_size": "1000"},
)


def _numeric(params: dict) -> dict:
    """A stand-in for the strategy's parameter model: coerce numbers alike."""
    return {k: float(v) if isinstance(v, (int, float, str)) else v for k, v in params.items()}


def _spec(**strategy) -> dict:
    base = {
        "strategy_id": "connors_rsi_mean_rev",
        "parameters": {"rsi_period": 2, "trade_size": 1000},
        "bar_types": ["QQQ.NASDAQ-1-DAY-LAST-EXTERNAL"],
    }
    return {"schema_version": 1, "strategies": [{**base, **strategy}]}


def test_a_spec_that_matches_after_normalising_has_no_differences():
    assert spec_differences(_spec(), normalise=_numeric, **FROZEN) == []


def test_every_kind_of_difference_is_named():
    spec = _spec(
        strategy_id="sma_crossover",
        parameters={"rsi_period": 3, "trade_size": 1000, "extra": 1},
        bar_types=["SPY.ARCA-1-MINUTE-LAST-EXTERNAL"],
    )
    found = spec_differences(spec, normalise=lambda p: p, **FROZEN)
    text = " ".join(found)
    assert "strategy" in text and "sma_crossover" in text
    assert "rsi_period" in text and "extra" in text
    assert "bar type" in text


def test_several_strategies_or_a_malformed_spec_are_differences_not_crashes():
    two = {"strategies": [_spec()["strategies"][0]] * 2}
    assert "2 strategies" in spec_differences(two, normalise=_numeric, **FROZEN)[0]
    assert spec_differences({"nonsense": True}, normalise=_numeric, **FROZEN)


def test_params_the_model_refuses_are_a_difference():
    def refuse(params: dict) -> dict:
        raise ValueError("trade_size: not a number")

    found = spec_differences(_spec(), normalise=refuse, **FROZEN)
    assert any("not a number" in f for f in found)
