"""Backtest-only benchmarks, run beside a strategy on the same window.

Benchmarks are deliberately kept out of ``StrategyRegistry`` so the live path
can never select one (see ``buy_and_hold`` for why that matters). They are run
by module path through the same orchestrator as any strategy.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from src.core.benchmarks.buy_and_hold import BuyAndHoldParameters


@dataclass(frozen=True)
class BenchmarkDefinition:
    """How to run a benchmark by module path."""

    name: str
    description: str
    strategy_path: str
    config_path: str
    param_model: type[BaseModel]


BENCHMARKS: dict[str, BenchmarkDefinition] = {
    "buy_and_hold": BenchmarkDefinition(
        name="buy_and_hold",
        description="Buy-and-hold benchmark: buy on the first bar, sell at the end of the window",
        strategy_path="src.core.benchmarks.buy_and_hold:BuyAndHold",
        config_path="src.core.benchmarks.buy_and_hold:BuyAndHoldConfig",
        param_model=BuyAndHoldParameters,
    ),
}
