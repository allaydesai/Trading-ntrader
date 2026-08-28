"""A strategy that raises in `on_start`, for Procedure P8 criterion 5.

Criterion 5 asks that a strategy failing during `on_start` does not stop the
others starting. The procedure suggests reaching that state with "an unqualified
instrument, or a deliberately invalid parameter". Neither works against this
codebase, which is why this module exists:

* `sma_crossover.on_start` only calls `subscribe_bars` (`sma_crossover.py:79-81`).
  Nautilus answers an unloadable contract with a data-client log line
  (`Cannot subscribe to bars …: instrument not found`) rather than an exception,
  so nothing raises in the start path.
* `SMAParameters` bounds `fast_period`/`slow_period` at `ge=1, le=200`
  (`src/models/strategy.py:27-28`), so an out-of-range value is refused when the
  spec is built — long before any session starts.

So the failure is injected the only remaining honest way: a real registered
strategy whose `on_start` raises. It is a probe strategy, and P8's result log
says which was used. Registering it here rather than under `src/` keeps it out
of the production registry for every process that does not import this module.
"""

from __future__ import annotations

from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from pydantic import BaseModel

from src.core.strategy_registry import StrategyRegistry, register_strategy

START_FAILURE_MESSAGE = "deliberate on_start failure — Procedure P8 criterion 5"


class P8StartRaiserConfig(StrategyConfig):
    """Config mirroring what `materialise_strategy` always supplies."""

    instrument_id: InstrumentId
    bar_type: BarType


class P8StartRaiserParameters(BaseModel):
    """No tunables. `StrategySpec.from_overrides` requires a parameter model."""

    _settings_map: dict[str, str] = {}


@register_strategy(
    name="p8_start_raiser",
    description="Diagnostic strategy that raises in on_start (Procedure P8 criterion 5)",
)
class P8StartRaiser(Strategy):
    """Subscribes to nothing and raises the moment the trader starts it."""

    def __init__(self, config: P8StartRaiserConfig) -> None:
        super().__init__(config)
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type

    def on_start(self) -> None:
        raise RuntimeError(START_FAILURE_MESSAGE)


StrategyRegistry.set_config("p8_start_raiser", P8StartRaiserConfig)
StrategyRegistry.set_param_model("p8_start_raiser", P8StartRaiserParameters)
StrategyRegistry.set_default_config("p8_start_raiser", {})
