"""A study's parameter space is checked against the strategy's parameter model (S1.1)."""

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.strategies import resolve_strategy
from src.mcp_server.studies.spec import (
    ParamRange,
    build_study_spec,
    check_param_space,
    outside_space,
)

pytestmark = pytest.mark.unit

SMA = resolve_strategy("sma_crossover")
FIELDS = dict(
    slug="sma-qqq",
    title="SMA on QQQ",
    hypothesis="Trends persist",
    strategy="sma_crossover",
    symbols=["qqq"],
    in_sample_start="2000-01-01",
    in_sample_end="2015-12-31",
    out_of_sample_start="2016-01-01",
    out_of_sample_end="2025-12-31",
)


def test_a_valid_space_passes():
    check_param_space(
        SMA,
        {
            "fast_period": ParamRange(values=[5, 10, 15]),
            "slow_period": ParamRange(min=20, max=60, step=10),
        },
    )


def test_an_unknown_parameter_is_refused():
    with pytest.raises(ToolFailure) as exc:
        check_param_space(SMA, {"fast_perod": ParamRange(values=[5])})
    assert exc.value.code == "invalid_param_space"
    assert "fast_perod" in exc.value.message


def test_a_value_the_model_rejects_is_refused():
    with pytest.raises(ToolFailure) as exc:
        check_param_space(SMA, {"fast_period": ParamRange(min=-5, max=10)})
    assert exc.value.code == "invalid_param_space"
    assert "-5" in exc.value.message


@pytest.mark.parametrize(
    "raw",
    [{}, {"values": [1], "min": 1, "max": 2}, {"min": 5, "max": 1}, {"values": []}],
    ids=["empty", "both", "reversed", "no-values"],
)
def test_a_range_needs_exactly_one_valid_form(raw):
    with pytest.raises(ValueError):
        ParamRange(**raw)


def test_outside_space_warns_only_for_given_params():
    space = {"fast_period": {"values": [5, 10]}, "slow_period": {"min": 20, "max": 60}}
    assert outside_space(space, {"fast_period": 10, "slow_period": 40}) == []
    warnings = outside_space(space, {"fast_period": 7, "slow_period": 80})
    assert len(warnings) == 2 and "still counts as a trial" in warnings[0]


def test_slug_and_budget_are_validated():
    assert build_study_spec(**FIELDS).trial_budget == 40
    for bad in ({"slug": "Bad Slug"}, {"trial_budget": 0}, {"symbols": []}):
        with pytest.raises(ToolFailure) as exc:
            build_study_spec(**{**FIELDS, **bad})
        assert exc.value.code == "invalid_study"
