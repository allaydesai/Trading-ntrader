# Data Model: PostgreSQL Metadata Storage

**Feature**: PostgreSQL Metadata Storage for Backtest Execution
**Version**: 1.0
**Status**: Implementation-Ready
**Date**: 2025-01-25

## Overview

This document defines the complete data model for persisting backtest execution metadata and performance metrics in PostgreSQL. The design supports automatic persistence, fast retrieval, comparison capabilities, and reproducibility through configuration snapshots.

## Database Schema

### Entity Relationship Diagram

```
┌─────────────────────────────────────┐
│         backtest_runs               │
│─────────────────────────────────────│
│ id (PK)                    BIGSERIAL│
│ run_id (UK)                UUID     │
│ strategy_name              VARCHAR  │
│ strategy_type              VARCHAR  │
│ instrument_symbol          VARCHAR  │
│ start_date                 TIMESTAMP│
│ end_date                   TIMESTAMP│
│ initial_capital            NUMERIC  │
│ data_source                VARCHAR  │
│ execution_status           VARCHAR  │
│ execution_duration_seconds NUMERIC  │
│ error_message              TEXT     │
│ config_snapshot            JSONB    │
│ reproduced_from_run_id     UUID     │
│ created_at                 TIMESTAMP│
└─────────────────────────────────────┘
            │
            │ 1:1
            │
            ▼
┌─────────────────────────────────────┐
│      performance_metrics            │
│─────────────────────────────────────│
│ id (PK)                    BIGSERIAL│
│ backtest_run_id (FK)       BIGINT   │
│ total_return               NUMERIC  │
│ final_balance              NUMERIC  │
│ cagr                       NUMERIC  │
│ sharpe_ratio               NUMERIC  │
│ sortino_ratio              NUMERIC  │
│ max_drawdown               NUMERIC  │
│ max_drawdown_date          TIMESTAMP│
│ calmar_ratio               NUMERIC  │
│ volatility                 NUMERIC  │
│ total_trades               INTEGER  │
│ winning_trades             INTEGER  │
│ losing_trades              INTEGER  │
│ win_rate                   NUMERIC  │
│ profit_factor              NUMERIC  │
│ expectancy                 NUMERIC  │
│ avg_win                    NUMERIC  │
│ avg_loss                   NUMERIC  │
│ created_at                 TIMESTAMP│
└─────────────────────────────────────┘
```

## Entity Definitions

### 1. BacktestRun

**Purpose**: Represents a single execution of a backtesting run with complete metadata and configuration snapshot.

**Lifecycle**: Immutable once created. Never updated, only deleted (with cascade to metrics).

#### Field Specifications

| Field Name | Data Type | Constraints | Default | Description |
|------------|-----------|-------------|---------|-------------|
| `id` | BIGSERIAL | PRIMARY KEY, NOT NULL | Auto-increment | Internal database identifier |
| `run_id` | UUID | UNIQUE, NOT NULL | uuid4() | Business identifier for external references |
| `strategy_name` | VARCHAR(255) | NOT NULL | - | Human-readable strategy name (e.g., "SMA Crossover") |
| `strategy_type` | VARCHAR(100) | NOT NULL | - | Strategy category (e.g., "trend_following") |
| `instrument_symbol` | VARCHAR(50) | NOT NULL | - | Trading symbol (e.g., "AAPL", "ES-FUT") |
| `start_date` | TIMESTAMP WITH TIME ZONE | NOT NULL | - | Backtest period start (inclusive) |
| `end_date` | TIMESTAMP WITH TIME ZONE | NOT NULL | - | Backtest period end (inclusive) |
| `initial_capital` | NUMERIC(20, 2) | NOT NULL, CHECK > 0 | - | Starting account balance in USD |
| `data_source` | VARCHAR(100) | NOT NULL | - | Data provider (e.g., "IBKR", "CSV", "Mock") |
| `execution_status` | VARCHAR(20) | NOT NULL | - | Execution outcome: "success" or "failed" |
| `execution_duration_seconds` | NUMERIC(10, 3) | NOT NULL, CHECK >= 0 | - | Time taken to run backtest (seconds) |
| `error_message` | TEXT | NULL | NULL | Error details if status = "failed" |
| `config_snapshot` | JSONB | NOT NULL | - | Complete strategy configuration (see structure below) |
| `reproduced_from_run_id` | UUID | FOREIGN KEY, NULL | NULL | Reference to original run if this is a reproduction |
| `created_at` | TIMESTAMP WITH TIME ZONE | NOT NULL | NOW() | When record was created |

#### Field Validation Rules

**Business Rules**:
- `end_date` MUST be greater than `start_date`
- `initial_capital` MUST be greater than 0
- `execution_duration_seconds` MUST be non-negative
- `error_message` MUST be NULL when `execution_status` = "success"
- `error_message` MUST be populated when `execution_status` = "failed"
- `config_snapshot` MUST contain required keys: `strategy_path`, `config_path`, `config`

**Database Constraints**:
```sql
-- Date range validation
ALTER TABLE backtest_runs
ADD CONSTRAINT chk_date_range
CHECK (end_date > start_date);

-- Capital validation
ALTER TABLE backtest_runs
ADD CONSTRAINT chk_initial_capital
CHECK (initial_capital > 0);

-- Duration validation
ALTER TABLE backtest_runs
ADD CONSTRAINT chk_execution_duration
CHECK (execution_duration_seconds >= 0);

-- Error message validation
ALTER TABLE backtest_runs
ADD CONSTRAINT chk_error_message
CHECK (
    (execution_status = 'success' AND error_message IS NULL) OR
    (execution_status = 'failed' AND error_message IS NOT NULL)
);

-- Config snapshot structure validation
ALTER TABLE backtest_runs
ADD CONSTRAINT chk_config_structure
CHECK (
    config_snapshot ? 'strategy_path' AND
    config_snapshot ? 'config_path' AND
    config_snapshot ? 'config'
);
```

#### config_snapshot JSONB Structure

**Complete Structure**:
```json
{
  "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
  "config_path": "config/strategies/sma_crossover.yaml",
  "version": "1.0",
  "config": {
    "fast_period": 10,
    "slow_period": 50,
    "risk_percent": 2.0,
    "position_size": 100,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.15
  }
}
```

**Field Descriptions**:
- `strategy_path`: Fully qualified Python path to strategy class
- `config_path`: Relative path to YAML configuration file (for reference)
- `version`: Schema version for future compatibility
- `config`: Strategy-specific parameters as key-value pairs

**Size Estimates**:
- Typical size: 300-400 bytes
- Maximum expected: 2 KB
- TOAST threshold: 2 KB (won't trigger out-of-line storage)

**Example Configurations**:

```json
// SMA Crossover
{
  "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
  "config_path": "config/strategies/sma_crossover.yaml",
  "version": "1.0",
  "config": {
    "fast_period": 10,
    "slow_period": 50,
    "risk_percent": 2.0
  }
}

// RSI Mean Reversion
{
  "strategy_path": "src.strategies.rsi_mean_reversion.RSIMeanReversionConfig",
  "config_path": "config/strategies/rsi_mean_reversion.yaml",
  "version": "1.0",
  "config": {
    "rsi_period": 14,
    "oversold_threshold": 30,
    "overbought_threshold": 70,
    "position_size": 100
  }
}

// Momentum
{
  "strategy_path": "src.strategies.momentum.MomentumStrategyConfig",
  "config_path": "config/strategies/momentum.yaml",
  "version": "1.0",
  "config": {
    "lookback_period": 20,
    "momentum_threshold": 0.05,
    "holding_period": 5
  }
}
```

### 2. PerformanceMetrics

**Purpose**: Stores pre-computed performance metrics for successful backtest executions.

**Lifecycle**: Created immediately after successful backtest. Immutable. Deleted when parent BacktestRun is deleted (cascade).

#### Field Specifications

| Field Name | Data Type | Constraints | Default | Description |
|------------|-----------|-------------|---------|-------------|
| `id` | BIGSERIAL | PRIMARY KEY, NOT NULL | Auto-increment | Internal database identifier |
| `backtest_run_id` | BIGINT | FOREIGN KEY, UNIQUE, NOT NULL | - | Reference to parent backtest_runs.id |
| `total_return` | NUMERIC(10, 4) | NOT NULL, CHECK >= -1.0 | - | Total return as decimal (0.15 = 15%) |
| `final_balance` | NUMERIC(20, 2) | NOT NULL, CHECK >= 0 | - | Account balance at end of backtest |
| `cagr` | NUMERIC(10, 4) | NULL, CHECK >= -1.0 | NULL | Compound annual growth rate |
| `sharpe_ratio` | NUMERIC(10, 4) | NULL, CHECK >= -10 AND <= 10 | NULL | Risk-adjusted return metric |
| `sortino_ratio` | NUMERIC(10, 4) | NULL, CHECK >= -10 AND <= 10 | NULL | Downside risk-adjusted return |
| `max_drawdown` | NUMERIC(10, 4) | NULL, CHECK >= -1.0 AND <= 0 | NULL | Maximum peak-to-trough decline |
| `max_drawdown_date` | TIMESTAMP WITH TIME ZONE | NULL | NULL | When max drawdown occurred |
| `calmar_ratio` | NUMERIC(10, 4) | NULL | NULL | CAGR divided by max drawdown |
| `volatility` | NUMERIC(10, 4) | NULL, CHECK >= 0 | NULL | Annualized standard deviation of returns |
| `total_trades` | INTEGER | NOT NULL, CHECK >= 0 | - | Total number of completed trades |
| `winning_trades` | INTEGER | NOT NULL, CHECK >= 0 | - | Number of profitable trades |
| `losing_trades` | INTEGER | NOT NULL, CHECK >= 0 | - | Number of losing trades |
| `win_rate` | NUMERIC(5, 4) | NULL, CHECK >= 0 AND <= 1 | NULL | Percentage of winning trades (0.6 = 60%) |
| `profit_factor` | NUMERIC(10, 4) | NULL, CHECK >= 0 | NULL | Gross profit / gross loss |
| `expectancy` | NUMERIC(20, 2) | NULL | NULL | Expected profit per trade |
| `avg_win` | NUMERIC(20, 2) | NULL, CHECK >= 0 | NULL | Average winning trade amount |
| `avg_loss` | NUMERIC(20, 2) | NULL, CHECK <= 0 | NULL | Average losing trade amount (negative) |
| `created_at` | TIMESTAMP WITH TIME ZONE | NOT NULL | NOW() | When record was created |

#### Field Validation Rules

**Business Rules**:
- `total_trades` MUST equal `winning_trades` + `losing_trades`
- `win_rate` MUST equal `winning_trades` / `total_trades` (if total_trades > 0)
- `final_balance` MUST be >= 0 (cannot go negative)
- `max_drawdown` MUST be <= 0 (negative percentage)
- `avg_loss` MUST be <= 0 (losses are negative)
- `avg_win` MUST be >= 0 (wins are positive)

**Database Constraints**:
```sql
-- Trade count consistency
ALTER TABLE performance_metrics
ADD CONSTRAINT chk_trade_consistency
CHECK (total_trades = winning_trades + losing_trades);

-- Win rate consistency (when trades > 0)
ALTER TABLE performance_metrics
ADD CONSTRAINT chk_win_rate
CHECK (
    (total_trades = 0 AND win_rate IS NULL) OR
    (total_trades > 0 AND win_rate = (winning_trades::numeric / total_trades))
);

-- Return bounds
ALTER TABLE performance_metrics
ADD CONSTRAINT chk_total_return_range
CHECK (total_return >= -1.0 AND total_return <= 1000.0);

-- Sharpe ratio bounds
ALTER TABLE performance_metrics
ADD CONSTRAINT chk_sharpe_range
CHECK (sharpe_ratio >= -10.0 AND sharpe_ratio <= 10.0);

-- Max drawdown bounds
ALTER TABLE performance_metrics
ADD CONSTRAINT chk_max_drawdown_range
CHECK (max_drawdown >= -1.0 AND max_drawdown <= 0);

-- Average win/loss consistency
ALTER TABLE performance_metrics
ADD CONSTRAINT chk_avg_win_positive
CHECK (avg_win IS NULL OR avg_win >= 0);

ALTER TABLE performance_metrics
ADD CONSTRAINT chk_avg_loss_negative
CHECK (avg_loss IS NULL OR avg_loss <= 0);
```

#### NaN and Infinity Handling

**Application-Level Validation** (before storage):
```python
from decimal import Decimal
import math

def validate_metric(value: float | None, field_name: str) -> Decimal | None:
    """Validate metric before storage."""
    if value is None:
        return None

    if math.isnan(value):
        raise ValueError(f"{field_name} cannot be NaN")

    if math.isinf(value):
        raise ValueError(f"{field_name} cannot be Infinity")

    return Decimal(str(value))
```

**Special Cases**:
- Division by zero → NULL (not stored)
- No trades executed → total_trades = 0, win_rate = NULL
- All winning trades → win_rate = 1.0, avg_loss = NULL
- All losing trades → win_rate = 0.0, avg_win = NULL

## Relationships

### 1. BacktestRun ↔ PerformanceMetrics (One-to-One)

**Relationship Type**: One-to-One (Optional)

**Cardinality**:
- Each `BacktestRun` has AT MOST one `PerformanceMetrics` record
- Each `PerformanceMetrics` belongs to EXACTLY one `BacktestRun`

**Foreign Key**:
```sql
ALTER TABLE performance_metrics
ADD CONSTRAINT fk_metrics_backtest_run
FOREIGN KEY (backtest_run_id)
REFERENCES backtest_runs (id)
ON DELETE CASCADE
ON UPDATE CASCADE;

-- Enforce one-to-one
ALTER TABLE performance_metrics
ADD CONSTRAINT uk_metrics_backtest_run
UNIQUE (backtest_run_id);
```

**Cascade Behavior**:
- **ON DELETE CASCADE**: Deleting a backtest run automatically deletes its metrics
- **ON UPDATE CASCADE**: Updating backtest_run.id updates the foreign key (rare)

**Why Optional**:
- Failed backtests have NO metrics (execution_status = "failed")
- Successful backtests ALWAYS have metrics

**SQLAlchemy Mapping**:
```python
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

class BacktestRun(Base):
    __tablename__ = 'backtest_runs'

    id: Mapped[int] = mapped_column(primary_key=True)

    # One-to-one relationship (uselist=False)
    metrics: Mapped[PerformanceMetrics | None] = relationship(
        back_populates="backtest_run",
        cascade="all, delete-orphan",
        uselist=False
    )

class PerformanceMetrics(Base):
    __tablename__ = 'performance_metrics'

    id: Mapped[int] = mapped_column(primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        unique=True
    )

    # Back reference
    backtest_run: Mapped[BacktestRun] = relationship(back_populates="metrics")
```

### 2. BacktestRun ↔ BacktestRun (Self-Referencing for Reproductions)

**Relationship Type**: Self-Referencing (Optional)

**Cardinality**:
- Each `BacktestRun` MAY reference one original run (if reproduced)
- Each original run MAY be referenced by multiple reproductions

**Foreign Key**:
```sql
ALTER TABLE backtest_runs
ADD CONSTRAINT fk_reproduced_from
FOREIGN KEY (reproduced_from_run_id)
REFERENCES backtest_runs (run_id)
ON DELETE SET NULL
ON UPDATE CASCADE;
```

**Cascade Behavior**:
- **ON DELETE SET NULL**: Deleting original run doesn't delete reproductions
- **ON UPDATE CASCADE**: Update propagates to reproductions

**Use Case**: Track which backtest is a re-run of a previous configuration

**Example**:
```python
# Original run
original = BacktestRun(
    run_id=uuid4(),
    strategy_name="SMA Crossover",
    # ... other fields
)

# Reproduction
reproduction = BacktestRun(
    run_id=uuid4(),
    strategy_name="SMA Crossover",
    reproduced_from_run_id=original.run_id,  # Links to original
    # ... same config_snapshot as original
)
```

## Indexes

### Index Strategy (3-Phase Deployment)

Indexes are deployed in phases to balance initial deployment speed with performance optimization.

#### Phase 1: Essential Indexes (Deploy with Tables)

**Purpose**: Critical for core functionality and foreign key performance

```sql
-- 1. Primary keys (automatic)
-- backtest_runs.id - BTREE (automatic)
-- performance_metrics.id - BTREE (automatic)

-- 2. Unique constraints
CREATE UNIQUE INDEX uk_backtest_runs_run_id
ON backtest_runs (run_id);

CREATE UNIQUE INDEX uk_metrics_backtest_run_id
ON performance_metrics (backtest_run_id);

-- 3. Foreign key index (critical for JOIN performance)
CREATE INDEX idx_metrics_backtest_run_id
ON performance_metrics (backtest_run_id);

-- 4. Cursor pagination (created_at DESC, id DESC)
CREATE INDEX idx_backtest_runs_created_id
ON backtest_runs (created_at DESC, id DESC);

-- 5. Strategy filter + time ordering
CREATE INDEX idx_backtest_runs_strategy_created_id
ON backtest_runs (strategy_name, created_at DESC, id DESC);
```

**Expected Performance**:
- Single retrieval by UUID: 1-5ms
- List 20 recent: 10-20ms
- Filter by strategy: 15-30ms

#### Phase 2: Performance Indexes (Deploy After 1 Week)

**Purpose**: Optimize common query patterns observed in production

```sql
-- 6. Instrument filter + time ordering
CREATE INDEX idx_backtest_runs_symbol_created_id
ON backtest_runs (instrument_symbol, created_at DESC, id DESC);

-- 7. JSONB config queries (jsonb_path_ops for 60% smaller, 3x faster)
CREATE INDEX idx_backtest_runs_config_gin
ON backtest_runs USING GIN (config_snapshot jsonb_path_ops);

-- 8. Metrics sorting (Sharpe ratio)
CREATE INDEX idx_metrics_sharpe_run
ON performance_metrics (sharpe_ratio DESC, backtest_run_id);

-- 9. Metrics sorting (Total return)
CREATE INDEX idx_metrics_return_run
ON performance_metrics (total_return DESC, backtest_run_id);
```

**Query Examples**:
```sql
-- Find backtests with specific config parameter
SELECT * FROM backtest_runs
WHERE config_snapshot @> '{"config": {"fast_period": 10}}';

-- Top 20 by Sharpe ratio
SELECT br.*, pm.*
FROM backtest_runs br
JOIN performance_metrics pm ON br.id = pm.backtest_run_id
ORDER BY pm.sharpe_ratio DESC
LIMIT 20;
```

#### Phase 3: Optional Indexes (Based on Usage)

**Purpose**: Further optimize specific use cases if needed

```sql
-- 10. Covering index for list view (index-only scan)
CREATE INDEX idx_backtest_runs_list_covering
ON backtest_runs (created_at DESC, id DESC)
INCLUDE (run_id, strategy_name, instrument_symbol, execution_status);

-- 11. Partial index for failed runs (if rare)
CREATE INDEX idx_backtest_runs_failed
ON backtest_runs (created_at DESC)
WHERE execution_status = 'failed';

-- 12. Date range queries
CREATE INDEX idx_backtest_runs_date_range
ON backtest_runs (start_date, end_date);
```

### Index Maintenance

**Size Estimates**:
- Phase 1 indexes: ~50 MB for 10,000 runs
- Phase 2 indexes: +30 MB additional
- Phase 3 indexes: +20 MB additional
- Total: ~100 MB for 10,000 runs

**Monitoring**:
```sql
-- Check index usage
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;

-- Find unused indexes
SELECT
    schemaname,
    tablename,
    indexname
FROM pg_stat_user_indexes
WHERE idx_scan = 0
AND indexname NOT LIKE 'pg_%';
```

**Rebuild Strategy** (if needed):
```sql
-- Rebuild specific index
REINDEX INDEX CONCURRENTLY idx_backtest_runs_created_id;

-- Rebuild all indexes for table
REINDEX TABLE CONCURRENTLY backtest_runs;
```

## Data Dictionary

### Enumerated Values

#### execution_status

| Value | Description | Metrics Stored? | Error Message Required? |
|-------|-------------|----------------|------------------------|
| `success` | Backtest completed successfully | Yes | No |
| `failed` | Backtest failed during execution | No | Yes |

#### data_source

| Value | Description | Notes |
|-------|-------------|-------|
| `IBKR` | Interactive Brokers live connection | Requires TWS/Gateway |
| `CSV` | CSV file import | File path stored in config |
| `Mock` | Mock data generator | Used for testing |

### Metric Definitions

| Metric | Formula | Range | Interpretation |
|--------|---------|-------|----------------|
| **total_return** | (final - initial) / initial | [-1.0, ∞) | -0.5 = -50% loss, 1.0 = 100% gain |
| **cagr** | (final/initial)^(1/years) - 1 | [-1.0, ∞) | Annualized return rate |
| **sharpe_ratio** | (return - rf) / volatility | [-10, 10] | >2 excellent, 1-2 good, <1 poor |
| **sortino_ratio** | (return - rf) / downside_dev | [-10, 10] | Like Sharpe but downside risk only |
| **max_drawdown** | min(peak - trough) / peak | [-1.0, 0] | -0.3 = -30% drawdown |
| **calmar_ratio** | cagr / abs(max_drawdown) | [0, ∞) | >3 excellent, 1-3 good |
| **volatility** | stddev(returns) * sqrt(252) | [0, ∞) | Annualized, 0.2 = 20% volatility |
| **win_rate** | winning_trades / total_trades | [0, 1.0] | 0.6 = 60% win rate |
| **profit_factor** | gross_profit / gross_loss | [0, ∞) | >2 excellent, 1-2 good, <1 losing |
| **expectancy** | avg_win * win_rate + avg_loss * (1 - win_rate) | (-∞, ∞) | Expected $ per trade |

### Data Type Reference

| PostgreSQL Type | Python Type | SQLAlchemy Type | Range | Notes |
|----------------|-------------|-----------------|-------|-------|
| BIGSERIAL | int | BigInteger | 1 to 9.2e18 | Auto-increment |
| UUID | uuid.UUID | UUID(as_uuid=True) | 128-bit | Version 4 (random) |
| VARCHAR(n) | str | String(n) | Up to n chars | Variable length |
| TEXT | str | Text | Unlimited | For error messages |
| NUMERIC(p,s) | Decimal | Numeric(p,s) | Exact decimal | No rounding errors |
| INTEGER | int | Integer | -2.1e9 to 2.1e9 | Signed 32-bit |
| TIMESTAMP WITH TIME ZONE | datetime | DateTime(timezone=True) | Full range | UTC normalized |
| JSONB | dict | JSONB | 1 GB max | Binary JSON, indexed |

## Example Data

### Example 1: Successful SMA Crossover Backtest

**backtest_runs**:
```sql
INSERT INTO backtest_runs (
    run_id, strategy_name, strategy_type, instrument_symbol,
    start_date, end_date, initial_capital, data_source,
    execution_status, execution_duration_seconds, error_message,
    config_snapshot, reproduced_from_run_id, created_at
) VALUES (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'SMA Crossover',
    'trend_following',
    'AAPL',
    '2023-01-01 00:00:00+00',
    '2023-12-31 23:59:59+00',
    100000.00,
    'IBKR',
    'success',
    45.237,
    NULL,
    '{
        "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
        "config_path": "config/strategies/sma_crossover.yaml",
        "version": "1.0",
        "config": {
            "fast_period": 10,
            "slow_period": 50,
            "risk_percent": 2.0
        }
    }',
    NULL,
    '2025-01-25 10:30:00+00'
);
```

**performance_metrics**:
```sql
INSERT INTO performance_metrics (
    backtest_run_id, total_return, final_balance, cagr,
    sharpe_ratio, sortino_ratio, max_drawdown, max_drawdown_date,
    calmar_ratio, volatility, total_trades, winning_trades,
    losing_trades, win_rate, profit_factor, expectancy,
    avg_win, avg_loss, created_at
) VALUES (
    1,  -- backtest_run.id
    0.2547,  -- 25.47% return
    125470.00,
    0.2547,
    1.85,
    2.34,
    -0.12,  -- -12% max drawdown
    '2023-06-15 14:30:00+00',
    2.12,
    0.18,
    45,  -- total trades
    28,  -- winning
    17,  -- losing
    0.6222,  -- 62.22% win rate
    2.15,
    145.60,  -- avg profit per trade
    520.35,  -- avg win
    -285.20,  -- avg loss
    '2025-01-25 10:30:15+00'
);
```

### Example 2: Failed Backtest (Data Error)

**backtest_runs**:
```sql
INSERT INTO backtest_runs (
    run_id, strategy_name, strategy_type, instrument_symbol,
    start_date, end_date, initial_capital, data_source,
    execution_status, execution_duration_seconds, error_message,
    config_snapshot, reproduced_from_run_id, created_at
) VALUES (
    'b2c3d4e5-f6a7-8901-bcde-f12345678901',
    'RSI Mean Reversion',
    'mean_reversion',
    'TSLA',
    '2024-01-01 00:00:00+00',
    '2024-12-31 23:59:59+00',
    50000.00,
    'CSV',
    'failed',
    2.145,
    'DataError: Missing price data for 2024-03-15. Data file corrupted or incomplete.',
    '{
        "strategy_path": "src.strategies.rsi_mean_reversion.RSIMeanReversionConfig",
        "config_path": "config/strategies/rsi_mean_reversion.yaml",
        "version": "1.0",
        "config": {
            "rsi_period": 14,
            "oversold_threshold": 30,
            "overbought_threshold": 70
        }
    }',
    NULL,
    '2025-01-25 11:15:30+00'
);
```

**performance_metrics**: (No record - failed backtest)

### Example 3: Reproduced Backtest

**backtest_runs**:
```sql
-- Original run (id=1, run_id='a1b2c3d4...')

-- Reproduction
INSERT INTO backtest_runs (
    run_id, strategy_name, strategy_type, instrument_symbol,
    start_date, end_date, initial_capital, data_source,
    execution_status, execution_duration_seconds, error_message,
    config_snapshot, reproduced_from_run_id, created_at
) VALUES (
    'c3d4e5f6-a7b8-9012-cdef-123456789012',
    'SMA Crossover',
    'trend_following',
    'AAPL',
    '2023-01-01 00:00:00+00',
    '2023-12-31 23:59:59+00',
    100000.00,
    'IBKR',
    'success',
    43.891,
    NULL,
    '{
        "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
        "config_path": "config/strategies/sma_crossover.yaml",
        "version": "1.0",
        "config": {
            "fast_period": 10,
            "slow_period": 50,
            "risk_percent": 2.0
        }
    }',
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',  -- References original
    '2025-01-25 14:20:00+00'
);
```

## Query Patterns

### Common Queries with Performance

#### 1. Retrieve Single Backtest by UUID

```sql
SELECT br.*, pm.*
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.run_id = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';
```

**Index Used**: `uk_backtest_runs_run_id` (unique index)
**Expected Performance**: 1-5ms
**Result Rows**: 1

#### 2. List 20 Most Recent Backtests

```sql
SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.execution_status,
    br.created_at,
    pm.total_return,
    pm.sharpe_ratio,
    pm.max_drawdown
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;
```

**Index Used**: `idx_backtest_runs_created_id`
**Expected Performance**: 10-20ms
**Result Rows**: 20

#### 3. Filter by Strategy (Cursor Pagination)

```sql
-- First page
SELECT br.*, pm.*
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.strategy_name = 'SMA Crossover'
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- Next page (cursor = last row's created_at and id)
SELECT br.*, pm.*
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.strategy_name = 'SMA Crossover'
AND (br.created_at, br.id) < ('2025-01-25 10:00:00+00', 123)
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;
```

**Index Used**: `idx_backtest_runs_strategy_created_id`
**Expected Performance**: 15-30ms
**Result Rows**: 20

#### 4. Top Performers by Sharpe Ratio

```sql
SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    pm.sharpe_ratio,
    pm.total_return,
    pm.max_drawdown
FROM backtest_runs br
INNER JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE pm.sharpe_ratio IS NOT NULL
ORDER BY pm.sharpe_ratio DESC
LIMIT 20;
```

**Index Used**: `idx_metrics_sharpe_run` (Phase 2)
**Expected Performance**: 20-50ms
**Result Rows**: 20

#### 5. Find Backtests with Specific Config

```sql
-- Find all backtests with fast_period = 10
SELECT br.*
FROM backtest_runs br
WHERE br.config_snapshot @> '{"config": {"fast_period": 10}}';
```

**Index Used**: `idx_backtest_runs_config_gin` (Phase 2)
**Expected Performance**: 50-200ms
**Result Rows**: Variable

#### 6. Compare Multiple Backtests

```sql
SELECT
    br.run_id,
    br.strategy_name,
    br.config_snapshot->'config' as config,
    pm.total_return,
    pm.sharpe_ratio,
    pm.max_drawdown,
    pm.win_rate,
    pm.total_trades
FROM backtest_runs br
INNER JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.run_id IN (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'b2c3d4e5-f6a7-8901-bcde-f12345678901',
    'c3d4e5f6-a7b8-9012-cdef-123456789012'
)
ORDER BY pm.sharpe_ratio DESC;
```

**Index Used**: `uk_backtest_runs_run_id`
**Expected Performance**: 5-15ms
**Result Rows**: 3

## Data Lifecycle

### Creation Flow

```
1. Backtest Execution Starts
   ↓
2. Generate UUID (uuid4())
   ↓
3. Execute Strategy
   ↓
4. [SUCCESS] → Extract Metrics
   ├─ Create BacktestRun (status=success)
   └─ Create PerformanceMetrics
   ↓
5. [FAILED] → Capture Error
   └─ Create BacktestRun (status=failed, error_message)
   ↓
6. Commit Transaction
   ↓
7. Display run_id to User
```

### Deletion Flow

```
1. User Deletes Backtest (run_id)
   ↓
2. Database Cascade Trigger
   ├─ Delete PerformanceMetrics (FK CASCADE)
   └─ Delete BacktestRun
   ↓
3. Update Reproductions
   └─ Set reproduced_from_run_id = NULL
```

### Immutability Rules

**NEVER ALLOWED**:
- Update any field in `backtest_runs` after creation
- Update any field in `performance_metrics` after creation
- Modify `config_snapshot` (defeats reproducibility)

**ALLOWED**:
- DELETE backtest_runs (cascades to metrics)
- SELECT queries (read-only)

## Storage Estimates

### Per-Record Size

**backtest_runs**:
- Fixed columns: ~200 bytes
- config_snapshot (JSONB): 300-400 bytes
- Total: ~600 bytes per row

**performance_metrics**:
- All columns: ~150 bytes per row

**Total per backtest**: ~750 bytes

### Scale Projections

| Backtest Count | Disk Space | Index Overhead | Total |
|---------------|------------|----------------|-------|
| 1,000 | 0.75 MB | 5 MB | ~6 MB |
| 10,000 | 7.5 MB | 50 MB | ~58 MB |
| 100,000 | 75 MB | 500 MB | ~575 MB |
| 1,000,000 | 750 MB | 5 GB | ~5.8 GB |

**Recommendation**: No cleanup needed until >100,000 records (~575 MB)

## Implementation Notes

### SQLAlchemy Model Example

```python
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import (
    BigInteger, CheckConstraint, ForeignKey, Index,
    Numeric, String, Text, func
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.db.base import Base

class BacktestRun(Base):
    __tablename__ = 'backtest_runs'

    # Primary key
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Business identifier
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        default=uuid4,
        unique=True,
        nullable=False,
        index=True
    )

    # Metadata
    strategy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(100), nullable=False)
    instrument_symbol: Mapped[str] = mapped_column(String(50), nullable=False)

    # Date range
    start_date: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False
    )
    end_date: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False
    )

    # Execution
    initial_capital: Mapped[Decimal] = mapped_column(
        Numeric(20, 2),
        nullable=False
    )
    data_source: Mapped[str] = mapped_column(String(100), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_duration_seconds: Mapped[Decimal] = mapped_column(
        Numeric(10, 3),
        nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Configuration snapshot
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Reproduction tracking
    reproduced_from_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("backtest_runs.run_id", ondelete="SET NULL"),
        nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    # Relationships
    metrics: Mapped["PerformanceMetrics | None"] = relationship(
        back_populates="backtest_run",
        cascade="all, delete-orphan",
        uselist=False
    )

    # Constraints
    __table_args__ = (
        CheckConstraint('end_date > start_date', name='chk_date_range'),
        CheckConstraint('initial_capital > 0', name='chk_initial_capital'),
        CheckConstraint(
            'execution_duration_seconds >= 0',
            name='chk_execution_duration'
        ),
        CheckConstraint(
            "(execution_status = 'success' AND error_message IS NULL) OR "
            "(execution_status = 'failed' AND error_message IS NOT NULL)",
            name='chk_error_message'
        ),
        Index(
            'idx_backtest_runs_created_id',
            'created_at',
            'id',
            postgresql_using='btree',
            postgresql_ops={'created_at': 'DESC', 'id': 'DESC'}
        ),
        Index(
            'idx_backtest_runs_strategy_created_id',
            'strategy_name',
            'created_at',
            'id',
            postgresql_ops={'created_at': 'DESC', 'id': 'DESC'}
        ),
    )


class PerformanceMetrics(Base):
    __tablename__ = 'performance_metrics'

    # Primary key
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Foreign key (one-to-one)
    backtest_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )

    # Return metrics
    total_return: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    final_balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    cagr: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    # Risk metrics
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    sortino_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    max_drawdown_date: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    calmar_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    volatility: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))

    # Trading metrics
    total_trades: Mapped[int] = mapped_column(nullable=False)
    winning_trades: Mapped[int] = mapped_column(nullable=False)
    losing_trades: Mapped[int] = mapped_column(nullable=False)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))

    # Profit metrics
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    expectancy: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    avg_win: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    avg_loss: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    # Relationships
    backtest_run: Mapped[BacktestRun] = relationship(back_populates="metrics")

    # Constraints
    __table_args__ = (
        CheckConstraint(
            'total_trades = winning_trades + losing_trades',
            name='chk_trade_consistency'
        ),
        CheckConstraint('total_return >= -1.0', name='chk_total_return_min'),
        CheckConstraint('sharpe_ratio >= -10.0', name='chk_sharpe_min'),
        CheckConstraint('sharpe_ratio <= 10.0', name='chk_sharpe_max'),
        CheckConstraint('max_drawdown >= -1.0', name='chk_drawdown_min'),
        CheckConstraint('max_drawdown <= 0', name='chk_drawdown_max'),
        Index('idx_metrics_backtest_run_id', 'backtest_run_id'),
    )
```

### Pydantic Validation Model

```python
from pydantic import BaseModel, Field, field_validator
from typing import Dict, Any

class StrategyConfigSnapshot(BaseModel):
    """Configuration snapshot with validation."""

    strategy_path: str = Field(
        ...,
        min_length=1,
        description="Fully qualified path to strategy class"
    )
    config_path: str = Field(
        ...,
        min_length=1,
        description="Relative path to configuration file"
    )
    version: str = Field(
        default="1.0",
        description="Schema version for compatibility"
    )
    config: Dict[str, Any] = Field(
        ...,
        description="Strategy-specific parameters"
    )

    @field_validator('config')
    @classmethod
    def validate_config_not_empty(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if not v:
            raise ValueError("Config cannot be empty")
        return v

    @field_validator('strategy_path')
    @classmethod
    def validate_strategy_path_format(cls, v: str) -> str:
        if not v.startswith('src.strategies.'):
            raise ValueError("Strategy path must start with 'src.strategies.'")
        return v
```

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 1.0 | 2025-01-25 | Initial data model specification | System |

---

**Status**: ✅ Implementation-Ready
**Next Step**: Generate DDL schema in `contracts/` directory
**Dependencies**: None - standalone data model design
