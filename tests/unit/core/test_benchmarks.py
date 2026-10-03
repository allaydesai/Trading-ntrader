"""Benchmarks are runnable by module path and never selectable by the live path."""

import pytest

from src.core.benchmarks import BENCHMARKS
from src.core.strategy_factory import StrategyFactory
from src.core.strategy_registry import StrategyRegistry

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name", sorted(BENCHMARKS))
def test_benchmark_is_not_in_the_strategy_registry(name):
    """`live create --strategy <name>` only accepts registry names (src/cli/commands/live.py)."""
    StrategyRegistry.discover()
    assert not StrategyRegistry.exists(name)
    registered_paths = {d.strategy_path for d in StrategyRegistry.get_all().values()}
    assert BENCHMARKS[name].strategy_path not in registered_paths


@pytest.mark.parametrize("name", sorted(BENCHMARKS))
def test_benchmark_paths_resolve(name):
    benchmark = BENCHMARKS[name]
    StrategyFactory.create_strategy_class(benchmark.strategy_path)
    StrategyFactory.create_config_class(benchmark.config_path)


def test_buy_and_hold_rejects_allocation_over_100():
    model = BENCHMARKS["buy_and_hold"].param_model
    with pytest.raises(ValueError):
        model(allocation_pct=101)
    assert str(model().allocation_pct) == "100"
