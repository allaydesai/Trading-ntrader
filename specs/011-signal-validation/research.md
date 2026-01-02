# Research: Multi-Condition Signal Validation

**Feature**: `011-signal-validation`
**Date**: 2025-12-27
**Status**: Complete

## Research Summary

This document consolidates research findings for implementing multi-condition signal validation in the Nautilus Trader backtesting system.

---

## 1. Integration with Nautilus Trader Event Model

### Decision
Use composition pattern where signal evaluation is called within `on_bar()` handler, with results collected by a `SignalCollector` passed to the strategy.

### Rationale
- Nautilus Trader's `on_bar()` is the natural hook point for per-bar signal evaluation
- Indicators are automatically updated before `on_bar()` is called when registered
- The `on_signal()` callback exists in Nautilus but is for custom data types, not our use case
- Composition keeps signal evaluation decoupled from order execution

### Alternatives Considered
1. **Custom Data Events**: Create signal evaluations as Nautilus `Data` objects routed via `on_data()`
   - Rejected: Adds unnecessary complexity; evaluations are internal state, not market data
2. **Indicator Subclass**: Wrap signals as custom indicators
   - Rejected: Indicators are stateless per-bar; signals require aggregate tracking
3. **Post-Processing Approach**: Extract signals after backtest from order events
   - Rejected: Loses granularity of bars where no signal triggered

---

## 2. SignalComponent Pattern

### Decision
Use Protocol-based abstract component with concrete implementations for each condition type.

### Rationale
- Protocol (`typing.Protocol`) allows structural subtyping without inheritance overhead
- Each component evaluates independently and returns `ComponentResult(value, triggered, reason)`
- Matches existing indicator pattern: components can be registered and auto-updated

### Design Pattern
```python
from typing import Protocol
from dataclasses import dataclass

@dataclass(frozen=True)
class ComponentResult:
    """Immutable result of a single condition evaluation."""
    name: str
    value: float  # The calculated value (e.g., RSI=8.5)
    triggered: bool  # Did condition pass?
    reason: str  # Human-readable explanation

class SignalComponent(Protocol):
    """Protocol for signal condition components."""
    name: str

    def evaluate(self, bar: Bar, cache: Cache) -> ComponentResult:
        """Evaluate condition against current bar state."""
        ...
```

### Alternatives Considered
1. **ABC (Abstract Base Class)**: Traditional inheritance-based approach
   - Rejected: Python Protocols are more Pythonic and flexible
2. **Callable Pattern**: Simple functions returning tuples
   - Rejected: Loses type safety and doesn't support stateful components (e.g., trend filters)
3. **Dictionary-based Conditions**: JSON/dict config defining conditions
   - Rejected: Too dynamic; loses compile-time type checking; harder to unit test

---

## 3. Composite Signal Logic (AND/OR)

### Decision
Implement `CompositeSignalGenerator` with configurable `CombinationLogic` enum.

### Rationale
- AND logic: All conditions must pass (default for conservative entry)
- OR logic: At least one condition must pass (for exit signals)
- Short-circuit evaluation for performance (AND stops on first fail)
- First-fail tracking identifies blocking condition

### Design Pattern
```python
from enum import Enum

class CombinationLogic(Enum):
    AND = "and"  # All must pass
    OR = "or"    # At least one must pass

class CompositeSignalGenerator:
    def __init__(
        self,
        components: list[SignalComponent],
        logic: CombinationLogic = CombinationLogic.AND
    ):
        self.components = components
        self.logic = logic

    def evaluate(self, bar: Bar, cache: Cache) -> SignalEvaluation:
        results = []
        blocking_component = None

        for component in self.components:
            result = component.evaluate(bar, cache)
            results.append(result)

            if self.logic == CombinationLogic.AND and not result.triggered:
                if blocking_component is None:
                    blocking_component = result.name
                # Continue to evaluate all for analysis purposes

        passed = sum(1 for r in results if r.triggered)
        total = len(results)

        if self.logic == CombinationLogic.AND:
            final_signal = passed == total
        else:  # OR
            final_signal = passed >= 1

        return SignalEvaluation(
            timestamp=bar.ts_event,
            components=results,
            signal=final_signal,
            strength=passed / total if total > 0 else 0.0,
            blocking_component=blocking_component if not final_signal else None
        )
```

### Alternatives Considered
1. **Complex Boolean Expressions**: Support `(A AND B) OR (C AND D)`
   - Rejected: Out of scope for v1 per spec; adds parsing complexity
2. **Weighted Voting**: Each condition has a weight
   - Rejected: Out of scope for v1 per spec; simple pass/fail is sufficient

---

## 4. Memory Management for Long Backtests

### Decision
In-memory collection with configurable periodic flush to CSV.

### Rationale
- Memory-bound design prevents OOM on million-bar backtests
- Periodic flush every N evaluations (configurable, default: 10,000)
- CSV files append-friendly; final export merges chunks
- PostgreSQL storage is optional post-processing step

### Design Pattern
```python
class SignalCollector:
    def __init__(
        self,
        flush_threshold: int = 10_000,
        output_dir: Path | None = None
    ):
        self._evaluations: list[SignalEvaluation] = []
        self._flush_threshold = flush_threshold
        self._output_dir = output_dir
        self._chunk_count = 0

    def record(self, evaluation: SignalEvaluation) -> None:
        self._evaluations.append(evaluation)
        if len(self._evaluations) >= self._flush_threshold:
            self._flush_to_disk()

    def _flush_to_disk(self) -> None:
        if self._output_dir is None:
            return
        chunk_file = self._output_dir / f"signals_{self._chunk_count:04d}.csv"
        self._write_csv(self._evaluations, chunk_file)
        self._evaluations.clear()
        self._chunk_count += 1
```

### Performance Target
- 100,000 bars × 10 conditions = 1M component evaluations
- Estimated memory: ~50 bytes per ComponentResult × 10 × 100,000 = ~50MB (acceptable)
- Flush ensures bounded memory regardless of backtest length

### Alternatives Considered
1. **Streaming to Database**: Write directly to PostgreSQL
   - Rejected: Adds latency; complicates testing; better as post-process
2. **No Memory Bound**: Keep all in memory
   - Rejected: OOM risk on large backtests (edge case: spec mentions millions of bars)
3. **Parquet Files**: Binary format for efficiency
   - Rejected: CSV is simpler and spec explicitly requests CSV export

---

## 5. Post-Backtest Statistics

### Decision
Calculate statistics in `SignalAnalyzer` class after backtest completion.

### Rationale
- Trigger rate = count(passed) / total_evaluations per component
- Blocking rate = count(was_blocker) / count(failed_signals) per component
- Near-miss = evaluations where strength >= 0.75 but signal = False

### Implementation Pattern
```python
@dataclass
class SignalStatistics:
    """Post-backtest analysis results."""
    trigger_rates: dict[str, float]  # component_name -> rate (0.0-1.0)
    blocking_rates: dict[str, float]  # component_name -> rate (0.0-1.0)
    near_miss_count: int
    total_evaluations: int
    total_triggered: int

class SignalAnalyzer:
    def analyze(self, evaluations: list[SignalEvaluation]) -> SignalStatistics:
        # Count per-component triggers and blocks
        ...
```

---

## 6. Existing Strategy Integration

### Decision
Create wrapper pattern that strategies opt-into for signal validation.

### Rationale
- Existing strategies work without modification (backwards compatible)
- New strategies can use `SignalEnabledStrategy` mixin or wrapper
- Gradual migration path for existing strategies

### Integration Options

**Option A: Mixin Pattern** (Recommended for new strategies)
```python
class SignalEnabledMixin:
    """Mixin that adds signal validation to a strategy."""

    def __init__(self, *args, signal_generator: CompositeSignalGenerator, **kwargs):
        super().__init__(*args, **kwargs)
        self._signal_generator = signal_generator
        self._signal_collector = SignalCollector()

    def evaluate_signal(self, bar: Bar) -> SignalEvaluation:
        evaluation = self._signal_generator.evaluate(bar, self.cache)
        self._signal_collector.record(evaluation)
        return evaluation
```

**Option B: Wrapper Pattern** (For retrofitting existing strategies)
```python
class SignalValidatedStrategy:
    """Wrapper that adds signal validation around an existing strategy."""

    def __init__(self, strategy: Strategy, signal_generator: CompositeSignalGenerator):
        self._inner = strategy
        self._generator = signal_generator
        self._collector = SignalCollector()
```

### Alternatives Considered
1. **Modify Strategy Base Class**: Add signal validation to all strategies
   - Rejected: Too invasive; breaks existing code
2. **Decorator Pattern**: `@signal_validated` decorator
   - Rejected: Decorators don't compose well with Nautilus's class-based system

---

## 7. CSV Export Format

### Decision
Standard tabular CSV with one row per evaluation, flattened component columns.

### Rationale
- Compatible with pandas, Excel, and common analysis tools
- Flattened format (one column per component) enables easy filtering
- Timestamp as ISO-8601 for readability

### Schema
```csv
timestamp,signal,strength,blocking_component,trend_filter_value,trend_filter_triggered,trend_filter_reason,rsi_value,rsi_triggered,rsi_reason,...
2024-01-01T10:00:00Z,False,0.75,trend_filter,99.5,False,"Close < SMA",8.2,True,"RSI < 10",...
```

### Alternatives Considered
1. **JSON Lines**: One JSON object per row
   - Rejected: Less tool-compatible; CSV specified in feature spec
2. **Nested CSV**: Component results in JSON column
   - Rejected: Harder to filter; loses benefits of flat format

---

## 8. Concrete SignalComponent Implementations

### Decision
Provide built-in components for common conditions; allow custom via Protocol.

### Built-in Components

| Component | Purpose | Parameters |
|-----------|---------|------------|
| `TrendFilterComponent` | Close > SMA(period) | period, price_type |
| `RSIThresholdComponent` | RSI < threshold | period, threshold, direction |
| `PriceBreakoutComponent` | Close > prev_high | lookback_bars |
| `VolumeConfirmComponent` | Volume > avg_volume(period) | period, multiplier |
| `FibonacciLevelComponent` | Price near Fib level | level (0.382, 0.5, 0.618), tolerance |
| `TimeStopComponent` | Bars held > max_bars | max_bars |

### Rationale
- Covers all examples from feature spec (trend, pattern, Fibonacci, volume)
- Each is independently testable
- Custom components just implement the Protocol

---

## 9. Technology Choices

### No New Dependencies Required

All requirements met by existing stack:
- **Pydantic 2.5+**: Signal models with validation
- **SQLAlchemy 2.0 async**: Optional database persistence
- **structlog**: Signal evaluation logging
- **pytest**: TDD-compliant testing
- **Nautilus Trader**: Indicator infrastructure

---

## 10. Risk Assessment

| Risk | Mitigation |
|------|------------|
| Performance degradation on large backtests | Benchmark with 100k+ bars; optimize hot paths |
| Memory exhaustion | Periodic flush to CSV; configurable threshold |
| Breaking existing strategies | Opt-in pattern; no changes to base Strategy class |
| Complex condition logic requests | Document v1 scope (AND/OR only); plan v2 for complex |

---

## Conclusion

The research confirms that multi-condition signal validation can be implemented cleanly within the existing Nautilus Trader architecture using:

1. **Protocol-based SignalComponent** for extensible condition definitions
2. **CompositeSignalGenerator** for AND/OR logic with blocking detection
3. **SignalCollector** with memory-bounded operation for audit trail
4. **Opt-in integration** via mixin/wrapper pattern for backward compatibility

No new dependencies are required. The design adheres to all constitution principles (KISS, TDD, type safety, modular design).

**Next Steps**: Proceed to Phase 1 - Data Model and Contracts generation.
