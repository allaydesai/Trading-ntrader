# Data Model: Multi-Condition Signal Validation

**Feature**: `011-signal-validation`
**Date**: 2025-12-27
**Version**: 1.0

---

## Overview

This document defines the data model for multi-condition signal validation, including runtime objects, database entities, and Pydantic schemas.

---

## 1. Core Entities

### 1.1 ComponentResult

Represents the evaluation result of a single signal condition.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `str` | Yes | Component identifier (e.g., "trend_filter", "rsi_threshold") |
| `value` | `float` | Yes | Calculated value (e.g., RSI=8.5, Close=152.30) |
| `triggered` | `bool` | Yes | Did the condition pass? |
| `reason` | `str` | Yes | Human-readable explanation of result |

**Validation Rules**:
- `name` must be non-empty alphanumeric with underscores
- `value` can be any float (including NaN for insufficient data)
- `reason` should be concise (< 200 characters)

**Immutability**: Frozen dataclass (cannot be modified after creation)

---

### 1.2 SignalEvaluation

Captures the complete state of all conditions at a single point in time.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `timestamp` | `int` | Yes | Bar timestamp in nanoseconds (Nautilus format) |
| `bar_type` | `str` | Yes | Bar type identifier (e.g., "AAPL.XNAS-1-DAY-LAST") |
| `components` | `list[ComponentResult]` | Yes | Ordered list of component evaluations |
| `signal` | `bool` | Yes | Final composite signal result |
| `strength` | `float` | Yes | Ratio of passed/total conditions (0.0-1.0) |
| `blocking_component` | `str \| None` | No | Name of first failing component (if signal=False) |

**Validation Rules**:
- `timestamp` must be positive
- `components` must have at least 1 item
- `strength` must be between 0.0 and 1.0
- `blocking_component` should be None when `signal=True`

**Derived Properties**:
- `passed_count`: Number of components where `triggered=True`
- `total_count`: Length of `components` list
- `is_near_miss`: `strength >= 0.75 and not signal`

---

### 1.3 CompositeSignalConfig

Configuration for a composite signal generator.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | `str` | Yes | - | Unique identifier for this signal |
| `logic` | `CombinationLogic` | No | `AND` | How to combine conditions |
| `components` | `list[ComponentConfig]` | Yes | - | Component configurations |

**CombinationLogic Enum**:
- `AND`: All conditions must pass
- `OR`: At least one condition must pass

---

### 1.4 ComponentConfig

Configuration for a signal component.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | `ComponentType` | Yes | - | Type of component |
| `name` | `str` | No | Auto-generated | Custom name override |
| `params` | `dict[str, Any]` | No | `{}` | Component-specific parameters |

**ComponentType Enum**:
- `TREND_FILTER`: Close > SMA(period)
- `RSI_THRESHOLD`: RSI comparison to threshold
- `PRICE_BREAKOUT`: Price vs previous high/low
- `VOLUME_CONFIRM`: Volume vs average
- `FIBONACCI_LEVEL`: Price near Fibonacci level
- `TIME_STOP`: Bars held limit
- `CUSTOM`: User-defined evaluation

---

### 1.5 SignalStatistics

Post-backtest analysis results.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `total_evaluations` | `int` | Yes | Total number of bar evaluations |
| `total_triggered` | `int` | Yes | Count where signal=True |
| `trigger_rates` | `dict[str, float]` | Yes | Per-component trigger rate (0.0-1.0) |
| `blocking_rates` | `dict[str, float]` | Yes | Per-component blocking rate (0.0-1.0) |
| `near_miss_count` | `int` | Yes | Evaluations with strength >= 0.75 but signal=False |
| `near_miss_threshold` | `float` | Yes | Threshold used (default 0.75) |

**Derived Properties**:
- `signal_rate`: `total_triggered / total_evaluations`
- `primary_blocker`: Component with highest blocking rate

---

## 2. Entity Relationships

```
CompositeSignalConfig
    └── has many: ComponentConfig[]
           │
           ▼ (runtime instantiation)
CompositeSignalGenerator
    └── has many: SignalComponent[] (Protocol implementations)
           │
           ▼ (per-bar evaluation)
SignalEvaluation
    └── has many: ComponentResult[]
           │
           ▼ (collected by)
SignalCollector
    └── produces: list[SignalEvaluation]
           │
           ▼ (analyzed by)
SignalAnalyzer
    └── produces: SignalStatistics
```

---

## 3. Database Schema (Optional Persistence)

### 3.1 Table: `signal_evaluations`

For optional post-backtest persistence to PostgreSQL.

```sql
CREATE TABLE signal_evaluations (
    id BIGSERIAL PRIMARY KEY,
    backtest_run_id INTEGER NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    timestamp_ns BIGINT NOT NULL,
    bar_type VARCHAR(100) NOT NULL,
    signal BOOLEAN NOT NULL,
    strength DECIMAL(4, 3) NOT NULL,
    blocking_component VARCHAR(100),
    components JSONB NOT NULL,  -- Array of ComponentResult
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_strength_range CHECK (strength >= 0 AND strength <= 1)
);

-- Index for time-series queries
CREATE INDEX idx_signal_evaluations_backtest_time
    ON signal_evaluations(backtest_run_id, timestamp_ns);

-- Index for blocking analysis
CREATE INDEX idx_signal_evaluations_blocking
    ON signal_evaluations(backtest_run_id, blocking_component)
    WHERE blocking_component IS NOT NULL;

-- Index for near-miss queries
CREATE INDEX idx_signal_evaluations_near_miss
    ON signal_evaluations(backtest_run_id, strength)
    WHERE signal = FALSE AND strength >= 0.75;
```

**Design Notes**:
- Uses JSONB for `components` to avoid many-to-many complexity
- Foreign key to `backtest_runs` for data ownership
- Indexes optimized for common query patterns

---

## 4. Pydantic Models

### 4.1 Response Models (API)

```python
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum

class CombinationLogic(str, Enum):
    AND = "and"
    OR = "or"

class ComponentType(str, Enum):
    TREND_FILTER = "trend_filter"
    RSI_THRESHOLD = "rsi_threshold"
    PRICE_BREAKOUT = "price_breakout"
    VOLUME_CONFIRM = "volume_confirm"
    FIBONACCI_LEVEL = "fibonacci_level"
    TIME_STOP = "time_stop"
    CUSTOM = "custom"

class ComponentResultResponse(BaseModel):
    """API response for a single component evaluation."""
    name: str
    value: float
    triggered: bool
    reason: str

    model_config = {"from_attributes": True}

class SignalEvaluationResponse(BaseModel):
    """API response for a signal evaluation."""
    timestamp: datetime
    bar_type: str
    components: list[ComponentResultResponse]
    signal: bool
    strength: float = Field(ge=0.0, le=1.0)
    blocking_component: str | None = None

    model_config = {"from_attributes": True}

class SignalStatisticsResponse(BaseModel):
    """API response for post-backtest signal statistics."""
    total_evaluations: int = Field(ge=0)
    total_triggered: int = Field(ge=0)
    signal_rate: float = Field(ge=0.0, le=1.0)
    trigger_rates: dict[str, float]
    blocking_rates: dict[str, float]
    near_miss_count: int = Field(ge=0)
    near_miss_threshold: float = Field(ge=0.0, le=1.0)
    primary_blocker: str | None = None

    model_config = {"from_attributes": True}
```

### 4.2 Request Models (API)

```python
class ComponentConfigRequest(BaseModel):
    """API request for component configuration."""
    type: ComponentType
    name: str | None = None
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)

class CompositeSignalConfigRequest(BaseModel):
    """API request for composite signal configuration."""
    name: str = Field(min_length=1, max_length=50)
    logic: CombinationLogic = CombinationLogic.AND
    components: list[ComponentConfigRequest] = Field(min_length=1, max_length=10)

class SignalExportRequest(BaseModel):
    """API request for exporting signal evaluations."""
    backtest_run_id: int
    format: str = Field(default="csv", pattern="^(csv|json)$")
    include_passed_only: bool = False
    min_strength: float | None = Field(default=None, ge=0.0, le=1.0)
```

---

## 5. State Transitions

### SignalEvaluation Lifecycle

```
Bar Received
    │
    ▼
[Indicators Update] ──► [Components Evaluate] ──► [Results Collected]
    │                         │                         │
    ▼                         ▼                         ▼
    initialized?          ComponentResult[]        SignalEvaluation
    │                         │                         │
    └─ No ─► Skip            └──────────────────────────┘
                                        │
                                        ▼
                               [Collector Records]
                                        │
                                        ▼
                               [Flush if threshold]
                                        │
                                        ▼
                                   [In-memory] ──► [CSV chunk]
```

### SignalCollector States

| State | Description | Transitions |
|-------|-------------|-------------|
| `IDLE` | No evaluations recorded | → `COLLECTING` on first record |
| `COLLECTING` | Actively recording evaluations | → `FLUSHING` on threshold |
| `FLUSHING` | Writing chunk to disk | → `COLLECTING` on complete |
| `FINALIZED` | Backtest complete, all data available | Terminal state |

---

## 6. Example Data

### ComponentResult Example

```json
{
  "name": "trend_filter",
  "value": 152.30,
  "triggered": true,
  "reason": "Close (152.30) > SMA(200) (148.50)"
}
```

### SignalEvaluation Example

```json
{
  "timestamp": 1704067200000000000,
  "bar_type": "AAPL.XNAS-1-DAY-LAST",
  "components": [
    {"name": "trend_filter", "value": 152.30, "triggered": true, "reason": "Close > SMA(200)"},
    {"name": "rsi_threshold", "value": 8.5, "triggered": true, "reason": "RSI(2) < 10"},
    {"name": "volume_confirm", "value": 1.2, "triggered": true, "reason": "Volume 20% above average"},
    {"name": "fib_level", "value": 0.62, "triggered": false, "reason": "Price not near Fib 0.618 (tolerance 2%)"}
  ],
  "signal": false,
  "strength": 0.75,
  "blocking_component": "fib_level"
}
```

### SignalStatistics Example

```json
{
  "total_evaluations": 1000,
  "total_triggered": 45,
  "signal_rate": 0.045,
  "trigger_rates": {
    "trend_filter": 0.72,
    "rsi_threshold": 0.08,
    "volume_confirm": 0.45,
    "fib_level": 0.12
  },
  "blocking_rates": {
    "trend_filter": 0.15,
    "rsi_threshold": 0.65,
    "volume_confirm": 0.12,
    "fib_level": 0.08
  },
  "near_miss_count": 23,
  "near_miss_threshold": 0.75,
  "primary_blocker": "rsi_threshold"
}
```

---

## 7. CSV Export Schema

Flattened format for analysis tools.

### Header Row

```
timestamp,bar_type,signal,strength,blocking_component,{component1}_value,{component1}_triggered,{component1}_reason,...
```

### Example Rows

```csv
timestamp,bar_type,signal,strength,blocking_component,trend_filter_value,trend_filter_triggered,trend_filter_reason,rsi_threshold_value,rsi_threshold_triggered,rsi_threshold_reason
2024-01-01T10:00:00Z,AAPL.XNAS-1-DAY-LAST,False,0.75,fib_level,152.30,True,"Close > SMA(200)",8.5,True,"RSI(2) < 10"
2024-01-02T10:00:00Z,AAPL.XNAS-1-DAY-LAST,True,1.00,,155.00,True,"Close > SMA(200)",5.2,True,"RSI(2) < 10"
```

---

## 8. Validation Invariants

1. **ComponentResult**: Immutable after creation
2. **SignalEvaluation**: `len(components) >= 1`
3. **SignalEvaluation**: `strength == sum(c.triggered) / len(components)`
4. **SignalEvaluation**: If `signal=True`, then `blocking_component=None`
5. **SignalStatistics**: All rates between 0.0 and 1.0
6. **CompositeSignalConfig**: `len(components) <= 10` (per spec)
7. **Export**: All evaluations for a backtest exported together
