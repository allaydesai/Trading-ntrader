---
name: strategy-development
description: >
  Use when creating a new trading strategy, modifying strategy logic, adding indicators,
  or working on position sizing. Covers the 4-file registration pattern, on_bar() flow,
  and Nautilus indicator gotchas.
---

# Strategy Development Guide

## The 4-File Registration Pattern

Every strategy requires exactly 4 components registered in a specific order:

### File 1: Strategy Class (`src/core/strategies/<name>.py`)

```python
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.models.strategy import MyParameters  # File 3

class MyConfig(StrategyConfig):
    """Config class — all strategy parameters live here."""
    instrument_id: InstrumentId
    bar_type: BarType
    # ... strategy-specific params with defaults

@register_strategy(
    name="my_strategy",
    description="What this strategy does",
    aliases=["mystrat", "ms"],
)
class MyStrategy(Strategy):
    def __init__(self, config: MyConfig) -> None:
        super().__init__(config)
        # Store config values as instance attrs
        # Initialize indicators
        # Initialize state tracking variables

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)

    def on_stop(self) -> None:
        self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        # See on_bar() flow below
        ...

    def on_dispose(self) -> None:
        pass
```

### File 2: Config class (same file as Strategy, defined above the class)

The `StrategyConfig` subclass holds all parameters. Nautilus requires `instrument_id` and `bar_type` at minimum.

### File 3: Parameter model (`src/models/strategy.py`)

Pydantic model for API/UI validation:

```python
class MyParameters(BaseModel):
    param_a: int = Field(default=10, ge=1, le=200, description="...")
    param_b: Decimal = Field(default=Decimal("10.0"), ge=0.1, le=100.0)

    _settings_map = {
        "param_a": "settings_field_name",
    }
```

### File 4: YAML config (`configs/<name>.yaml`)

```yaml
strategy_path: "src.core.strategies.<module>:<StrategyClass>"
config_path: "src.core.strategies.<module>:<ConfigClass>"
config:
  instrument_id: "AAPL.NASDAQ"
  bar_type: "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"
  # strategy-specific params...

backtest:
  start_date: "2020-01-01"
  end_date: "2025-12-31"
  initial_capital: 100000
```

### Registration (bottom of strategy file, AFTER all class definitions)

This order is mandatory — all 3 calls must appear at module level:

```python
# 1. Config class
StrategyRegistry.set_config("my_strategy", MyConfig)
# 2. Parameter model
StrategyRegistry.set_param_model("my_strategy", MyParameters)
# 3. Default config dict
StrategyRegistry.set_default_config(
    "my_strategy",
    {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
        "param_a": 10,
    },
)
```

## on_bar() Flow (Strict Order)

Every `on_bar` method must follow this exact sequence:

```python
def on_bar(self, bar: Bar) -> None:
    # 1. Store current bar (needed for position sizing)
    self._current_bar = bar

    # 2. Update ALL indicators with new bar
    self.indicator.handle_bar(bar)

    # 3. Check if indicators are initialized (have enough data)
    if not self.indicator.initialized:
        return

    # 4. Read indicator values
    value = self.indicator.value

    # 5. Check for signals (only if previous values exist for crossover detection)
    if self._prev_value is not None:
        self._check_signals(value)

    # 6. Store current values for next iteration
    self._prev_value = value
```

## Position Sizing Pattern

```python
def _calculate_position_size(self) -> Quantity:
    position_value = self.portfolio_value * (self.position_size_pct / Decimal("100"))
    current_price = Decimal(str(self._current_bar.close))
    shares = max(int(position_value / current_price), 1)
    return Quantity.from_int(shares)
```

Formula: `shares = (portfolio_value * position_size_pct / 100) / current_price`

## Critical Gotchas

1. **Nautilus RSI returns 0-1 range, NOT 0-100** — buy threshold of 10 in traditional terms = `0.10` in Nautilus
2. **`@register_strategy` decorator** must appear directly above the class, BEFORE the 3 `StrategyRegistry.set_*()` calls at module bottom
3. **`instrument_id` and `bar_type` are REQUIRED** in every StrategyConfig — the engine won't start without them
4. **Bar type format**: `{SYMBOL}.{VENUE}-{STEP}-{STEP_TYPE}-{PRICE_TYPE}-EXTERNAL` for catalog data, `-INTERNAL` for mock data
5. **Use `self.cache.positions()`** to check open positions — always up-to-date from the engine cache
6. **Close positions before opening opposite** — check `has_long`/`has_short` before submitting new orders

## Order Submission Pattern

```python
# Market order
order = self.order_factory.market(
    instrument_id=self.instrument_id,
    order_side=OrderSide.BUY,  # or OrderSide.SELL
    quantity=self._calculate_position_size(),
)
self.submit_order(order)

# Close existing position
self.close_position(position)
```

## Strategy Creation Checklist

1. [ ] Create strategy file in `src/core/strategies/<name>.py`
2. [ ] Define `<Name>Config(StrategyConfig)` with all parameters
3. [ ] Apply `@register_strategy()` decorator with name, description, aliases
4. [ ] Implement `__init__`, `on_start`, `on_stop`, `on_bar`, `on_dispose`
5. [ ] Follow on_bar() flow order strictly
6. [ ] Add parameter model to `src/models/strategy.py`
7. [ ] Add 3 `StrategyRegistry.set_*()` calls at bottom of strategy file
8. [ ] Create YAML config in `configs/`
9. [ ] Write tests (unit for logic, integration with `--forked` for full engine)
10. [ ] Run `make test-unit` and `make test-integration`

## Reference Files

- `reference/strategy-template.py` — Copy-paste scaffold for new strategies
- `reference/yaml-config-template.yaml` — YAML config template

## Key Source Files

- `src/core/strategies/sma_crossover.py` — Canonical strategy implementation
- `src/core/strategy_registry.py` — Registration system
- `src/core/strategy_factory.py` — Factory and StrategyLoader
- `src/models/strategy.py` — Parameter models
