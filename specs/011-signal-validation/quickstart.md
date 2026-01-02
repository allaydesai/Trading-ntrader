# Quickstart: Multi-Condition Signal Validation

**Feature**: `011-signal-validation`
**Audience**: Developers implementing or using signal validation

---

## Overview

Multi-condition signal validation enables strategies to:
1. Define multiple entry/exit conditions as reusable components
2. Combine conditions with AND/OR logic
3. Capture a complete audit trail during backtests
4. Analyze which conditions trigger, block, or create "near-miss" situations

---

## Installation

No additional dependencies required. Uses existing project stack.

```bash
# Ensure environment is up to date
uv sync
```

---

## Basic Usage

### 1. Define Signal Components

Create components for each condition in your strategy:

```python
from src.core.signals.components import (
    TrendFilterComponent,
    RSIThresholdComponent,
    VolumeConfirmComponent,
)

# Trend filter: Close > SMA(200)
trend_filter = TrendFilterComponent(
    name="trend_filter",
    sma_period=200,
)

# RSI condition: RSI(2) < 10
rsi_condition = RSIThresholdComponent(
    name="rsi_oversold",
    period=2,
    threshold=10.0,
    direction="below",  # "below" or "above"
)

# Volume confirmation: Volume > 1.5x average
volume_confirm = VolumeConfirmComponent(
    name="volume_surge",
    period=20,
    multiplier=1.5,
)
```

### 2. Create Composite Signal Generator

Combine components with AND/OR logic:

```python
from src.core.signals.composite import CompositeSignalGenerator, CombinationLogic

# AND logic: All conditions must pass
entry_signal = CompositeSignalGenerator(
    name="entry_signal",
    components=[trend_filter, rsi_condition, volume_confirm],
    logic=CombinationLogic.AND,
)
```

### 3. Integrate with Strategy

Use the signal generator in your strategy's `on_bar` method:

```python
from nautilus_trader.trading.strategy import Strategy
from src.core.signals.collector import SignalCollector

class MyStrategy(Strategy):
    def __init__(self, config):
        super().__init__(config)
        self._signal_generator = entry_signal  # From step 2
        self._signal_collector = SignalCollector()

    def on_bar(self, bar):
        # Update indicators (handled automatically if registered)

        # Evaluate signal
        evaluation = self._signal_generator.evaluate(bar, self.cache)

        # Record for audit trail
        self._signal_collector.record(evaluation)

        # Act on signal
        if evaluation.signal:
            self._enter_position(bar)

        # Log details
        if not evaluation.signal:
            self.log.debug(
                f"Signal blocked by {evaluation.blocking_component}, "
                f"strength={evaluation.strength:.0%}"
            )
```

### 4. Export Audit Trail

After backtest completion:

```python
# Export to CSV
self._signal_collector.export_csv("/path/to/output/signals.csv")

# Get statistics
stats = self._signal_collector.get_statistics()
print(f"Signal rate: {stats.signal_rate:.1%}")
print(f"Primary blocker: {stats.primary_blocker}")
print(f"Near misses: {stats.near_miss_count}")
```

---

## Component Types

### TrendFilterComponent

Checks if price is above/below a moving average.

```python
TrendFilterComponent(
    name="trend_filter",
    sma_period=200,          # SMA period
    direction="above",       # "above" or "below"
    price_type="close",      # "close", "open", "high", "low"
)
```

### RSIThresholdComponent

Checks if RSI is above/below a threshold.

```python
RSIThresholdComponent(
    name="rsi_oversold",
    period=2,                # RSI period
    threshold=10.0,          # Threshold value
    direction="below",       # "below" or "above"
)
```

### PriceBreakoutComponent

Checks if price breaks previous high/low.

```python
PriceBreakoutComponent(
    name="breakout_high",
    lookback_bars=1,         # How many bars to look back
    comparison="above",      # "above" (vs prev high) or "below" (vs prev low)
)
```

### VolumeConfirmComponent

Checks if volume exceeds average by multiplier.

```python
VolumeConfirmComponent(
    name="volume_surge",
    period=20,               # Average volume period
    multiplier=1.5,          # Required multiple of average
)
```

### FibonacciLevelComponent

Checks if price is near a Fibonacci retracement level.

```python
FibonacciLevelComponent(
    name="fib_382",
    level=0.382,             # Fib level (0.236, 0.382, 0.5, 0.618, 0.786)
    tolerance=0.02,          # Tolerance percentage
    swing_period=50,         # Period to find swing high/low
)
```

### TimeStopComponent

Checks if position has been held for max bars.

```python
TimeStopComponent(
    name="time_stop",
    max_bars=5,              # Maximum bars to hold
)
```

---

## Custom Components

Implement the `SignalComponent` protocol:

```python
from typing import Protocol
from src.core.signals.evaluation import ComponentResult

class SignalComponent(Protocol):
    name: str

    def evaluate(self, bar: Bar, cache: Cache) -> ComponentResult:
        ...

# Example custom component
class MyCustomComponent:
    def __init__(self, name: str, my_param: float):
        self.name = name
        self._my_param = my_param

    def evaluate(self, bar: Bar, cache: Cache) -> ComponentResult:
        # Your custom logic
        value = calculate_something(bar)
        triggered = value > self._my_param

        return ComponentResult(
            name=self.name,
            value=value,
            triggered=triggered,
            reason=f"Value ({value:.2f}) {'>' if triggered else '<='} {self._my_param}",
        )
```

---

## Analyzing Results

### Via Python API

```python
from src.core.signals.analysis import SignalAnalyzer

analyzer = SignalAnalyzer()
stats = analyzer.analyze(signal_collector.evaluations)

# Trigger rates per component
for name, rate in stats.trigger_rates.items():
    print(f"{name}: {rate:.1%} trigger rate")

# Blocking analysis
for name, rate in stats.blocking_rates.items():
    if rate > 0.1:  # More than 10% blocking rate
        print(f"{name}: {rate:.1%} blocking rate - consider tuning")

# Near misses
print(f"\nNear misses: {stats.near_miss_count}")
print(f"Primary blocker: {stats.primary_blocker}")
```

### Via REST API

```bash
# Get statistics
curl http://localhost:8000/api/v1/backtests/123/signals/statistics

# Get blocking analysis
curl http://localhost:8000/api/v1/backtests/123/signals/blocking-analysis

# Export to CSV
curl -X POST http://localhost:8000/api/v1/backtests/123/signals/export \
  -H "Content-Type: application/json" \
  -d '{"format": "csv"}'
```

---

## Memory Management

For long backtests (100,000+ bars), configure periodic flushing:

```python
signal_collector = SignalCollector(
    flush_threshold=10_000,    # Flush every 10k evaluations
    output_dir=Path("/tmp/signal_chunks"),
)
```

The collector will:
1. Accumulate evaluations in memory
2. Flush to disk when threshold reached
3. Merge chunks on final export

---

## Best Practices

### 1. Start Simple
Begin with 2-3 conditions. Add more only if analysis shows value.

### 2. Monitor Blocking Rates
If one condition blocks >80% of signals, it may be too strict.

```python
if stats.blocking_rates["rsi_oversold"] > 0.8:
    print("Consider raising RSI threshold from 10 to 15")
```

### 3. Use Near-Miss Analysis
Near misses indicate opportunities for parameter tuning.

```python
# Get evaluations where 75%+ conditions passed
near_misses = [e for e in evaluations if e.is_near_miss]
```

### 4. Log Blocking Reasons
Capture why signals didn't fire for debugging:

```python
if not evaluation.signal:
    self.log.info(
        f"Signal blocked: {evaluation.blocking_component}",
        strength=evaluation.strength,
        components={c.name: c.triggered for c in evaluation.components},
    )
```

---

## Common Patterns

### Entry + Exit Signals

```python
entry_signal = CompositeSignalGenerator(
    name="entry",
    components=[trend_filter, rsi_condition],
    logic=CombinationLogic.AND,
)

exit_signal = CompositeSignalGenerator(
    name="exit",
    components=[price_breakout, time_stop],
    logic=CombinationLogic.OR,  # Exit if ANY condition triggers
)
```

### Conditional Evaluation

```python
def on_bar(self, bar):
    # Only evaluate entry if no position
    if not self._has_position():
        entry_eval = self._entry_generator.evaluate(bar, self.cache)
        self._entry_collector.record(entry_eval)
        if entry_eval.signal:
            self._enter()
    else:
        # Evaluate exit conditions
        exit_eval = self._exit_generator.evaluate(bar, self.cache)
        self._exit_collector.record(exit_eval)
        if exit_eval.signal:
            self._exit()
```

---

## Troubleshooting

### "Insufficient data" in ComponentResult

Components return `triggered=False` with reason "Insufficient data" when indicators haven't warmed up.

**Solution**: Check `indicator.initialized` before evaluating, or accept warm-up period in analysis.

### Memory usage growing

Ensure `flush_threshold` is set for long backtests:

```python
collector = SignalCollector(flush_threshold=10_000)
```

### Signal never triggers

Check blocking analysis to find the strictest condition:

```python
stats = collector.get_statistics()
print(f"Primary blocker: {stats.primary_blocker}")
print(f"Blocking rate: {stats.blocking_rates[stats.primary_blocker]:.1%}")
```

---

## Next Steps

1. **Implement components** for your strategy's conditions
2. **Run a backtest** with signal collection enabled
3. **Analyze statistics** to tune parameters
4. **Export audit trail** for detailed investigation
