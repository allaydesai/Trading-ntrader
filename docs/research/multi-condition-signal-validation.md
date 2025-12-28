# Multi-Condition Trading Signal Validation in Nautilus Trader

## Research Document

**Date:** 2025-12-27
**Branch:** `feature/multi-condition-signal-validation`
**Status:** Research Complete
**Version:** 2.1

---

## 1. Problem Statement

### The Challenge

When building trading strategies with multiple entry/exit conditions, debugging and validation become exponentially harder:

```
ENTRY SIGNAL = Trend Filter AND Price Pattern AND Fibonacci Level AND Volume Confirm
```

**Pain Points:**

1. **Black Box Problem**: Final signal is binary (true/false), but WHY did it fire or not fire?
2. **Condition Interaction**: Individual conditions may work, but combinations fail silently
3. **Temporal Debugging**: Need to know state at each bar, not just at trade points
4. **Backtesting Opacity**: Hard to audit why trades didn't happen at expected points
5. **Parameter Sensitivity**: Which condition is the bottleneck? Which is too loose?

### Real-World Scenario

Consider a strategy with these conditions:
- **Trend Filter**: Close > 200 EMA (uptrend only)
- **Price Action**: Bullish engulfing pattern detected
- **Fibonacci**: Price at 61.8% retracement level (±2% tolerance)
- **Volume**: Above average volume confirmation

On any given bar, you need to answer:
- Which conditions passed? Which failed?
- How close was each condition to triggering?
- What was the "signal strength" (3 of 4 conditions met vs 1 of 4)?
- Over the backtest, which condition blocked the most potential entries?

---

## 2. Nautilus Trader Architecture Analysis

### Available Mechanisms

| Mechanism | Purpose | Best For |
|-----------|---------|----------|
| `self.log` | Basic logging | Simple debugging |
| `publish_signal()` | Lightweight alerts | Broadcasting simple state changes |
| `publish_data()` | Custom data publishing | Structured condition tracking |
| `@customdataclass` | Define custom data types | Serializable audit trails |
| `MessageBus` | Pub/sub communication | Cross-component coordination |
| `Actor` | Standalone component | **Signal collection & analysis** |
| Custom Indicators | Reusable calculations | Encapsulating condition logic |

### Key Architectural Insight: Actor Pattern

Nautilus Trader's `Actor` class provides clean separation of concerns:
- **Strategy** focuses on trading logic
- **Actor** (SignalCollector) focuses on audit/analysis

This separation keeps strategy code clean while enabling comprehensive signal tracking.

---

## 3. Approaches Analyzed

### Approach A: Condition State Tracker

**Concept**: Create a dedicated data structure that captures the state of every condition at each evaluation point.

**Pros:**
- Complete audit trail of every decision
- Actor pattern keeps strategy code clean
- Analyzable after backtest completion
- Supports "near miss" analysis
- Can calculate signal strength metrics

**Cons:**
- Memory overhead for long backtests
- Requires upfront design of condition interface

---

### Approach B: Composite Indicator Pattern

**Concept**: Wrap each condition as a custom indicator, then create a composite indicator that aggregates them.

**Pros:**
- Leverages Nautilus indicator infrastructure
- Auto-updates with bar data
- Each indicator testable in isolation

**Cons:**
- Indicators designed for numeric output, not rich state
- Less flexible for complex conditional logic
- Harder to capture "why" a condition failed

---

### Approach C: Rule Engine / Decision Tree

**Concept**: Model conditions as a tree/graph where each node evaluates and records its decision.

**Pros:**
- Visual representation of decision flow
- Easy to identify blocking conditions
- Supports complex AND/OR/NOT logic

**Cons:**
- More complex implementation
- May be overkill for simpler strategies

---

### Approach D: Event Sourcing Pattern

**Concept**: Emit an event for every condition evaluation, store all events, replay for analysis.

**Pros:**
- Complete history, nothing lost
- Can replay and re-analyze with different criteria

**Cons:**
- High volume of events
- Requires event storage infrastructure

---

## 4. Recommended Architecture

### Signal Component Pattern with Actor-Based Audit Trail

**Selected Approach**: Hybrid of Approach A (Condition State Tracker) with Nautilus Actor pattern for clean separation.

```
┌────────────────────────────────────────────────────────────────────┐
│                         ARCHITECTURE                                │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Signal Components                         │   │
│  │  (Each implements evaluate() with full audit data)          │   │
│  ├─────────────────────────────────────────────────────────────┤   │
│  │  TrendFilter │ FibRetracement │ PricePattern │ VolumeCheck  │   │
│  └──────────────┴────────────────┴──────────────┴──────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              CompositeSignalGenerator                        │   │
│  │  - Evaluates all components                                  │   │
│  │  - Combines with AND/OR logic                               │   │
│  │  - Calculates signal strength                                │   │
│  │  - Returns SignalEvaluation with full audit trail           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│              ┌───────────────┴───────────────┐                     │
│              │                               │                      │
│              ▼                               ▼                      │
│  ┌───────────────────────┐     ┌────────────────────────────┐     │
│  │    Strategy           │     │   SignalCollector (Actor)   │     │
│  │  - Trading logic      │     │   - Stores evaluations      │     │
│  │  - Entry/exit         │     │   - Export to CSV/DataFrame │     │
│  │  - Position mgmt      │     │   - Post-backtest analysis  │     │
│  └───────────────────────┘     └────────────────────────────┘     │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## 5. Core Concepts

### SignalComponent

Each signal component represents a single tradeable condition:

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Unique identifier (e.g., "TrendFilter_200") |
| `value` | float | Actual evaluated value (e.g., RSI = 15.3) |
| `triggered` | bool | Whether condition passed |
| `reason` | str | Human-readable explanation |

The `evaluate(bar, context)` method receives current bar data and shared context (indicators, previous bars) and returns the updated component state.

### SignalEvaluation

Captures the complete state of all conditions at a point in time:

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | int | Bar timestamp (nanoseconds) |
| `bar` | Bar | Current bar data |
| `components` | List[SignalComponent] | All evaluated conditions |
| `final_signal` | bool | Combined result (AND/OR) |
| `signal_strength` | float | Ratio of passed conditions (0.0 - 1.0) |
| `blocking_component` | str | First condition that blocked signal |

### CompositeSignalGenerator

Orchestrates evaluation of multiple components:
- Evaluates each component in sequence
- Combines results using configurable logic (AND/OR)
- Calculates signal strength
- Identifies blocking conditions
- Produces structured logging via `structlog`

### SignalCollector (Actor)

Nautilus Actor that runs alongside the strategy:
- Receives `SignalEvaluation` objects from strategy
- Stores complete audit trail in memory
- Exports to CSV/DataFrame on backtest completion
- Calculates post-hoc statistics (trigger rates, blocking rates)
- Logs near-misses in real-time (e.g., 75%+ conditions met)

---

## 6. Signal Components Library

### Planned Components

| Component | Description | Key Parameters |
|-----------|-------------|----------------|
| **TrendFilter** | Price above/below moving average | `period`, `ma_type`, `direction` |
| **RSIThreshold** | RSI above/below threshold | `period`, `threshold`, `direction` |
| **FibonacciRetracement** | Price near Fibonacci level | `level`, `tolerance_pct` |
| **BullishEngulfingPattern** | Candlestick pattern detection | - |
| **BearishEngulfingPattern** | Candlestick pattern detection | - |
| **VolumeSpike** | Volume above average | `multiplier`, `period` |
| **PriceBreakout** | Price breaks key level | `lookback`, `direction` |

Each component provides:
- Clear pass/fail determination
- Distance to threshold (for near-miss analysis)
- Human-readable reason string

---

## 7. Analysis Capabilities

### Real-Time (During Backtest)
- Structured logging of each signal evaluation
- Near-miss alerts when signal strength ≥ 75%
- Blocking condition identification

### Post-Backtest
- **Component Trigger Rates**: How often each condition passes
- **Blocking Analysis**: Which condition blocks signals most often
- **Near-Miss Report**: Instances where signal almost triggered
- **Signal Strength Distribution**: Histogram of signal strengths

### Visualization
- Signal strength over time chart
- Component trigger rate bar chart
- Blocking component pie chart
- Condition activation heatmap

---

## 8. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Component pattern** | Mutable dataclass with `evaluate()` | Simple, familiar, easy to test |
| **Audit collection** | Nautilus Actor | Clean separation from trading logic |
| **Context passing** | Dictionary | Flexible, supports any indicator/state |
| **Storage format** | CSV + DataFrame | Simple, portable, easy to analyze |
| **Combination logic** | AND/OR configurable | Covers most strategy patterns |
| **Logging** | structlog | Structured, per project constitution |

---

## 9. Resolved Questions

| Question | Resolution |
|----------|------------|
| Memory for long backtests | Actor exports to CSV periodically, clears memory |
| Real-time vs post-hoc analysis | Both supported - logs during, CSV after |
| Condition dependencies | Use evaluation order; context enables dependencies |
| Weighting conditions | Signal strength = simple ratio; weights can be added later |
| Testing approach | Unit test components in isolation; integration test combinations |

---

## 10. Implementation Phases

### Phase 1: Core Infrastructure
- Create `src/core/signals/` module structure
- Implement `SignalComponent` base class
- Implement `SignalEvaluation` data class
- Implement `CompositeSignalGenerator`
- Write unit tests for core classes

### Phase 2: Signal Components
- `TrendFilter` component (EMA/SMA variants)
- `RSIThreshold` component
- `FibonacciRetracement` component
- `BullishEngulfingPattern` component
- Unit tests for each component

### Phase 3: Actor & Collection
- Implement `SignalCollector` Actor
- CSV export functionality
- DataFrame conversion
- Statistics calculation
- Integration tests

### Phase 4: Analysis & Visualization
- Post-backtest analysis script
- Signal strength charts
- Condition heatmaps
- Bottleneck analysis
- Documentation

---

## 11. References

- Nautilus Trader Strategy Documentation
- Nautilus Trader Actor Documentation
- Nautilus Trader Custom Data & Signals
- Project constitution: `.specify/memory/constitution.md`
- Existing strategies: `src/core/strategies/`

---

## 12. Next Steps

1. **Create feature specification** using `/specify` skill
2. **Generate implementation tasks** using `/tasks` skill
3. **Begin TDD implementation** starting with Phase 1

---

*Document Version: 2.1*
*Research consolidated from multiple sources*
