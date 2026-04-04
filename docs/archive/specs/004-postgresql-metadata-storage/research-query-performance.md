# PostgreSQL Query Performance Optimization Research

**Feature**: PostgreSQL Metadata Storage for Backtest Results
**Research Date**: 2025-01-25
**Focus**: Index strategies, query patterns, and pagination for backtest metadata retrieval

## Performance Targets

Based on the feature specification:
- **Single record by UUID**: <100ms
- **List of 20 recent backtests**: <200ms
- **Comparison queries (up to 10 records)**: <2s
- **Scale**: Support 10,000+ backtest runs without degradation

## Schema Context

### Proposed Tables

**backtest_runs table**:
- `id` (UUID, primary key)
- `created_at` (TIMESTAMPTZ, execution start time)
- `completed_at` (TIMESTAMPTZ, execution end time)
- `duration_seconds` (INTEGER)
- `status` (VARCHAR, enum: 'success', 'failed', 'interrupted')
- `strategy_name` (VARCHAR)
- `strategy_type` (VARCHAR)
- `instrument_symbol` (VARCHAR)
- `date_range_start` (DATE)
- `date_range_end` (DATE)
- `initial_capital` (NUMERIC)
- `data_source` (VARCHAR)
- `config_snapshot` (JSONB, complete strategy configuration)
- `error_message` (TEXT, nullable)
- `original_run_id` (UUID, nullable, foreign key to backtest_runs.id)

**performance_metrics table**:
- `id` (UUID, primary key)
- `backtest_run_id` (UUID, foreign key to backtest_runs.id)
- `total_return` (NUMERIC)
- `cagr` (NUMERIC)
- `sharpe_ratio` (NUMERIC)
- `sortino_ratio` (NUMERIC)
- `max_drawdown` (NUMERIC)
- `volatility` (NUMERIC)
- `total_trades` (INTEGER)
- `winning_trades` (INTEGER)
- `losing_trades` (INTEGER)
- `win_rate` (NUMERIC)
- `profit_factor` (NUMERIC)
- `avg_win_amount` (NUMERIC)
- `avg_loss_amount` (NUMERIC)

## Query Patterns Analysis

### Common Query Types

1. **Single record by ID**: `SELECT * FROM backtest_runs WHERE id = $1`
2. **Recent backtests list**: `SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT 20`
3. **Filter by strategy**: `SELECT * FROM backtest_runs WHERE strategy_name = $1 ORDER BY created_at DESC`
4. **Filter by date range**: `SELECT * FROM backtest_runs WHERE created_at BETWEEN $1 AND $2`
5. **Sort by metrics**: `SELECT br.*, pm.* FROM backtest_runs br JOIN performance_metrics pm ON br.id = pm.backtest_run_id ORDER BY pm.sharpe_ratio DESC`
6. **Multi-record fetch for comparison**: `SELECT * FROM backtest_runs WHERE id = ANY($1)` (array of UUIDs)
7. **Search by config parameters**: `SELECT * FROM backtest_runs WHERE config_snapshot @> '{"param_name": "value"}'`

## Index Strategy Recommendations

### Primary Indexes (Critical)

#### 1. Primary Key Index (Automatic)
```sql
-- Automatically created with PRIMARY KEY constraint
-- Supports: Single record lookups by UUID
-- Expected: <1ms for UUID lookups
CREATE TABLE backtest_runs (
    id UUID PRIMARY KEY,
    -- other columns...
);
```

#### 2. Composite Index: created_at + id (For Pagination)
```sql
-- Critical for time-ordered queries with cursor pagination
-- Column order: created_at DESC NULLS LAST, id DESC
-- Supports: List recent backtests, date range filters
CREATE INDEX idx_backtest_runs_created_id
ON backtest_runs (created_at DESC, id DESC);
```

**Rationale**:
- B-tree index supports both ordering and range scans
- `created_at` first because it's used in range queries
- `id` second for tie-breaking and cursor pagination
- DESC ordering matches query patterns (newest first)
- Enables efficient cursor pagination without offset scanning

**Query Performance**:
- Recent 20: Uses index directly, ~5-10ms
- Date range filter: Index range scan, ~10-50ms
- Cursor pagination: Index seek, constant time regardless of depth

#### 3. Composite Index: strategy_name + created_at + id
```sql
-- Supports: Filter by strategy, sorted by time
-- Column order follows selectivity principle
CREATE INDEX idx_backtest_runs_strategy_created_id
ON backtest_runs (strategy_name, created_at DESC, id DESC);
```

**Rationale**:
- Equality condition (strategy_name) before range condition (created_at)
- Common query pattern: "Show me recent SMA strategy runs"
- Supports both equality and time-ordered results
- `id` included for covering index benefit

**Query Pattern**:
```sql
SELECT * FROM backtest_runs
WHERE strategy_name = 'SMA Crossover'
ORDER BY created_at DESC
LIMIT 20;
```

#### 4. Composite Index: instrument_symbol + created_at + id
```sql
-- Supports: Filter by instrument, sorted by time
CREATE INDEX idx_backtest_runs_symbol_created_id
ON backtest_runs (instrument_symbol, created_at DESC, id DESC);
```

**Rationale**:
- Same pattern as strategy index
- Common query: "Show me recent AAPL backtests"
- Enables efficient instrument-specific analysis

#### 5. GIN Index: config_snapshot (JSONB)
```sql
-- Supports: JSON containment queries for parameter search
-- Operator class: jsonb_path_ops (optimized for @> operator)
CREATE INDEX idx_backtest_runs_config_gin
ON backtest_runs USING GIN (config_snapshot jsonb_path_ops);
```

**Rationale**:
- `jsonb_path_ops` is smaller and faster than default `jsonb_ops`
- Supports `@>` (contains) operator for parameter matching
- Enables queries like "find all runs with fast_period=10"
- Trade-off: Doesn't support `?` (key exists) operator

**Query Pattern**:
```sql
-- Find backtests with specific parameter value
SELECT * FROM backtest_runs
WHERE config_snapshot @> '{"fast_period": 10}';

-- Find backtests with nested parameter
SELECT * FROM backtest_runs
WHERE config_snapshot @> '{"risk_management": {"max_position_size": 1000}}';
```

**Index Size Considerations**:
- GIN indexes are larger than B-tree (typically 2-3x)
- For 10,000 records with 5KB avg config: ~100-150 MB
- Acceptable for query performance gains

#### 6. Foreign Key Index: performance_metrics.backtest_run_id
```sql
-- Critical for JOIN performance
CREATE INDEX idx_performance_metrics_run_id
ON performance_metrics (backtest_run_id);
```

**Rationale**:
- Not automatically created by PostgreSQL for foreign keys
- Essential for JOIN operations
- Prevents full table scan when joining metrics

### Secondary Indexes (Performance Enhancement)

#### 7. Composite Index: status + created_at + id
```sql
-- Supports: Filter by status (success/failure)
CREATE INDEX idx_backtest_runs_status_created_id
ON backtest_runs (status, created_at DESC, id DESC);
```

**Use Case**:
- "Show me only successful backtests"
- "List all failed runs from last week"

**Alternative - Partial Index** (if mostly successful):
```sql
-- Only index failed runs if failures are rare (<5%)
CREATE INDEX idx_backtest_runs_failed
ON backtest_runs (created_at DESC, id DESC)
WHERE status = 'failed';
```

**Rationale for Partial Index**:
- Smaller index size (only indexes failures)
- Faster maintenance (fewer rows to update)
- Optimal when failure rate is low
- Still supports "list all failures" efficiently

#### 8. Covering Index for List Queries
```sql
-- Include frequently accessed columns to avoid heap lookups
CREATE INDEX idx_backtest_runs_list_covering
ON backtest_runs (created_at DESC, id DESC)
INCLUDE (strategy_name, instrument_symbol, status, duration_seconds);
```

**Rationale**:
- Enables Index-Only Scans for list queries
- Avoids heap page reads (faster query execution)
- Trade-off: Larger index size, slower writes
- Best for read-heavy workloads

**Performance Improvement**:
- Without INCLUDE: 15-20ms (index + heap reads)
- With INCLUDE: 5-10ms (index-only scan)

### Metrics Table Indexes

#### 9. Composite Index: Metrics for Sorting
```sql
-- Support sorting by common metrics
CREATE INDEX idx_performance_metrics_sharpe
ON performance_metrics (sharpe_ratio DESC, backtest_run_id);

CREATE INDEX idx_performance_metrics_return
ON performance_metrics (total_return DESC, backtest_run_id);

CREATE INDEX idx_performance_metrics_drawdown
ON performance_metrics (max_drawdown ASC, backtest_run_id);
```

**Rationale**:
- Enables "top performers" queries
- `backtest_run_id` included for JOIN back to main table
- DESC/ASC order matches query patterns

**Query Pattern**:
```sql
-- Top 10 by Sharpe ratio
SELECT br.*, pm.*
FROM backtest_runs br
JOIN performance_metrics pm ON br.id = pm.backtest_run_id
ORDER BY pm.sharpe_ratio DESC
LIMIT 10;
```

## Index Type Selection Guide

### B-tree vs Hash vs GIN

| Index Type | Use Case | Best For | Avoid For |
|------------|----------|----------|-----------|
| **B-tree** (default) | Ordered data, range queries | `created_at`, `id`, equality + range | High-cardinality text (long strings) |
| **Hash** | Exact equality only | Simple lookups (rare in our case) | Range queries, ordering, NULL handling |
| **GIN** | JSONB, arrays, full-text | `config_snapshot`, complex queries | Simple scalar columns, frequent updates |

**Decision for Backtest Metadata**:
- **B-tree**: All timestamp, UUID, numeric, and varchar columns
- **GIN**: Only `config_snapshot` (JSONB)
- **Hash**: Not used (B-tree handles equality efficiently)

### Column Order in Composite Indexes

**Rule**: Equality conditions before range conditions, high selectivity first

**Example 1: Correct Order**
```sql
-- ✅ Correct: equality (strategy_name) before range (created_at)
CREATE INDEX idx_strategy_time
ON backtest_runs (strategy_name, created_at DESC);

-- Query efficiently uses index
SELECT * FROM backtest_runs
WHERE strategy_name = 'SMA' AND created_at > '2025-01-01';
```

**Example 2: Incorrect Order**
```sql
-- ❌ Wrong: range before equality
CREATE INDEX idx_time_strategy
ON backtest_runs (created_at DESC, strategy_name);

-- Query can only use first column (created_at)
-- Must scan all dates, then filter by strategy_name (slow)
SELECT * FROM backtest_runs
WHERE strategy_name = 'SMA' AND created_at > '2025-01-01';
```

### Partial Indexes for Specific Cases

**Use Case 1: Failed Backtests Only**
```sql
-- Only index failures if they're rare
CREATE INDEX idx_backtest_runs_failed_only
ON backtest_runs (created_at DESC, id DESC)
WHERE status = 'failed';
```

**Benefits**:
- 95% smaller than full index (if 5% failure rate)
- Faster queries for "show all failures"
- Less maintenance overhead on writes

**Use Case 2: Recent Backtests Only**
```sql
-- Only index last 6 months if old data rarely accessed
CREATE INDEX idx_backtest_runs_recent
ON backtest_runs (created_at DESC, id DESC)
WHERE created_at > NOW() - INTERVAL '6 months';
```

**Benefits**:
- Smaller, faster index
- Lower maintenance cost
- Still supports most queries (recent data access is common)

## JSONB Indexing Deep Dive

### GIN Operator Classes: jsonb_ops vs jsonb_path_ops

| Feature | jsonb_ops (default) | jsonb_path_ops (optimized) |
|---------|---------------------|----------------------------|
| Index size | Larger (1x) | Smaller (0.4x) |
| Query speed | Slower | Faster (2-3x) |
| Operators | `@>`, `@?`, `@@`, `?`, `?\|`, `?&` | `@>`, `@?`, `@@` only |
| Use case | Need key existence checks | Only containment queries |

**Recommendation**: Use `jsonb_path_ops` for backtest config because:
- We primarily need containment queries (`@>`)
- Key existence queries (`?`) not critical for our use case
- Smaller index = better cache efficiency
- Faster containment checks = better performance

### Example JSONB Queries

```sql
-- Find backtests with specific parameter
WHERE config_snapshot @> '{"fast_period": 10}'

-- Find backtests with nested parameter
WHERE config_snapshot @> '{"risk_management": {"stop_loss": 0.02}}'

-- Find backtests with multiple parameters (AND logic)
WHERE config_snapshot @> '{"fast_period": 10, "slow_period": 50}'

-- Find backtests with array element
WHERE config_snapshot @> '{"instruments": ["AAPL"]}'
```

### JSONB Index Maintenance

**Write Performance Impact**:
- GIN indexes are slower to update than B-tree
- Each INSERT requires updating posting tree
- For our use case: acceptable (backtests are infrequent)

**Vacuuming**:
- GIN indexes benefit from regular VACUUM
- Consider autovacuum tuning for JSONB columns

```sql
-- Check GIN index bloat
SELECT schemaname, tablename, indexname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE indexname LIKE '%gin%';
```

## Query Patterns for Performance Targets

### Pattern 1: Single Record by ID (<100ms target)

**Query**:
```sql
SELECT br.*, pm.*
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.id = $1;
```

**Execution Plan**:
- Index Scan on `backtest_runs_pkey` (UUID primary key)
- Nested Loop with Index Scan on `idx_performance_metrics_run_id`

**Expected Performance**: 1-5ms (well under 100ms target)

**Optimization**:
- No additional optimization needed
- UUID primary key index is sufficient
- Foreign key index on metrics table ensures fast JOIN

### Pattern 2: List Recent Backtests (<200ms target)

**Query**:
```sql
SELECT br.id, br.created_at, br.strategy_name, br.instrument_symbol,
       br.status, br.duration_seconds,
       pm.total_return, pm.sharpe_ratio, pm.max_drawdown
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
ORDER BY br.created_at DESC
LIMIT 20;
```

**Execution Plan**:
- Index Scan on `idx_backtest_runs_created_id` (forward scan)
- Nested Loop with Index Scan on `idx_performance_metrics_run_id`

**Expected Performance**: 10-20ms

**Optimizations**:
1. Use covering index with INCLUDE clause (5-10ms)
2. Consider materialized view for dashboard queries
3. Cache results for 30-60 seconds if acceptable

### Pattern 3: Filter by Strategy (<200ms target)

**Query**:
```sql
SELECT br.*, pm.*
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.strategy_name = $1
ORDER BY br.created_at DESC
LIMIT 20;
```

**Execution Plan**:
- Index Scan on `idx_backtest_runs_strategy_created_id`
- Nested Loop with Index Scan on `idx_performance_metrics_run_id`

**Expected Performance**: 15-30ms

**Key Factor**: Selectivity of strategy_name
- If 5 strategies, each strategy = 20% of data
- Index still efficient up to ~30% selectivity

### Pattern 4: Sort by Metrics (<2s target for comparison)

**Query**:
```sql
SELECT br.*, pm.*
FROM backtest_runs br
JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.strategy_name = $1
ORDER BY pm.sharpe_ratio DESC
LIMIT 10;
```

**Execution Plan** (without metrics index):
- Index Scan on `idx_backtest_runs_strategy_created_id`
- Hash Join or Nested Loop with `performance_metrics`
- Sort by `sharpe_ratio` (expensive!)

**Optimization**: Add index on metrics table
```sql
CREATE INDEX idx_performance_metrics_sharpe
ON performance_metrics (sharpe_ratio DESC, backtest_run_id);
```

**Improved Plan**:
- Index Scan on `idx_performance_metrics_sharpe`
- Filter by strategy_name on joined rows

**Expected Performance**: 20-50ms

### Pattern 5: Comparison Query (<2s target)

**Query**:
```sql
SELECT br.*, pm.*
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.id = ANY($1::uuid[])  -- Array of 2-10 UUIDs
ORDER BY br.created_at DESC;
```

**Execution Plan**:
- Bitmap Index Scan on `backtest_runs_pkey` (multiple UUIDs)
- Nested Loop with Index Scan on `idx_performance_metrics_run_id`

**Expected Performance**: 5-15ms (well under 2s target)

**Optimization**:
- No additional optimization needed
- Primary key lookups are extremely fast
- Sorting by created_at benefits from index

### Pattern 6: Date Range Query (<200ms target)

**Query**:
```sql
SELECT br.id, br.created_at, br.strategy_name,
       pm.total_return, pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.created_at BETWEEN $1 AND $2
ORDER BY br.created_at DESC
LIMIT 100;
```

**Execution Plan**:
- Index Range Scan on `idx_backtest_runs_created_id`
- Nested Loop with Index Scan on `idx_performance_metrics_run_id`

**Expected Performance**:
- 1 day range: 10-20ms
- 1 month range: 30-60ms
- 1 year range: 100-200ms

**Optimization for Large Ranges**:
- Consider adding WHERE clause filters (status, strategy)
- Use pagination to limit result set
- Cache common date ranges

### Pattern 7: JSONB Parameter Search (<500ms target)

**Query**:
```sql
SELECT br.*, pm.*
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.config_snapshot @> '{"fast_period": 10}'
ORDER BY br.created_at DESC
LIMIT 20;
```

**Execution Plan**:
- Bitmap Index Scan on `idx_backtest_runs_config_gin`
- Recheck Cond for false positives
- Sort by created_at
- Nested Loop with metrics table

**Expected Performance**: 50-200ms (depends on selectivity)

**Optimization**:
- GIN index is critical here
- Consider composite index with created_at if this pattern is common
- Denormalize frequently searched parameters to columns

## Pagination Strategy

### Offset/Limit (Simple but Slow)

**Implementation**:
```sql
SELECT * FROM backtest_runs
ORDER BY created_at DESC
LIMIT 20 OFFSET 0;  -- Page 1

SELECT * FROM backtest_runs
ORDER BY created_at DESC
LIMIT 20 OFFSET 20;  -- Page 2
```

**Performance**:
- Page 1: 10ms
- Page 10: 50ms
- Page 100: 500ms (PostgreSQL scans and discards 2000 rows)

**When to Use**:
- Admin interfaces with page numbers
- Small datasets (<1000 records)
- Random page access required

**Cons**:
- Performance degrades linearly with offset
- Not suitable for infinite scroll
- Inefficient for deep pagination

### Cursor Pagination (Fast and Scalable)

**Implementation**:
```sql
-- First page (no cursor)
SELECT id, created_at, strategy_name, ...
FROM backtest_runs
ORDER BY created_at DESC, id DESC
LIMIT 20;

-- Next page (cursor = last created_at and id from previous page)
SELECT id, created_at, strategy_name, ...
FROM backtest_runs
WHERE (created_at, id) < ($1, $2)  -- Cursor values
ORDER BY created_at DESC, id DESC
LIMIT 20;
```

**Performance**:
- Page 1: 10ms
- Page 10: 10ms (constant time!)
- Page 100: 10ms

**Rationale**:
- Uses index seek instead of offset scan
- 17x faster than offset for deep pages (research finding)
- Constant performance regardless of page depth

**Cursor Format**:
```json
{
  "created_at": "2025-01-25T10:30:00Z",
  "id": "550e8400-e29b-41d4-a716-446655440000"
}
```

**Composite Cursor Encoding**:
```python
import base64
import json

def encode_cursor(created_at: datetime, id: UUID) -> str:
    """Encode cursor values to opaque string."""
    data = {
        "created_at": created_at.isoformat(),
        "id": str(id)
    }
    json_str = json.dumps(data)
    return base64.urlsafe_b64encode(json_str.encode()).decode()

def decode_cursor(cursor: str) -> tuple:
    """Decode cursor string to values."""
    json_str = base64.urlsafe_b64decode(cursor.encode()).decode()
    data = json.loads(json_str)
    return (
        datetime.fromisoformat(data["created_at"]),
        UUID(data["id"])
    )
```

**Index Requirement**:
```sql
-- Critical: Must match ORDER BY columns exactly
CREATE INDEX idx_backtest_runs_created_id
ON backtest_runs (created_at DESC, id DESC);
```

**Pros**:
- Constant-time performance
- No skipped/duplicate records on updates
- Efficient for infinite scroll
- Stateless (cursor is self-contained)

**Cons**:
- Can't jump to arbitrary page numbers
- Slightly more complex implementation
- Cursor must match ORDER BY columns

### Recommendation

**Use Cursor Pagination** for:
- List recent backtests (primary use case)
- API endpoints (if web interface added)
- Infinite scroll UIs
- Performance-critical queries

**Use Offset/Limit** for:
- Admin interfaces requiring page numbers
- Small result sets (<100 records)
- Export/download operations (with LIMIT only)

## Index Maintenance Strategy

### Index Build Order

Create indexes in this order during migration:
1. Primary keys (automatic)
2. Foreign keys
3. Time-based indexes (created_at)
4. Composite strategy/instrument indexes
5. Metrics indexes
6. GIN index (slowest to build)
7. Optional covering indexes

### Monitoring Index Usage

```sql
-- Check index usage statistics
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan AS index_scans,
    idx_tup_read AS tuples_read,
    idx_tup_fetch AS tuples_fetched,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;
```

**Red Flags**:
- `idx_scan = 0` after 1 week: Remove unused index
- Large index with low scan count: Consider dropping
- Index size > table size: Investigate bloat

### Index Bloat Detection

```sql
-- Estimate index bloat
SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
    idx_scan,
    idx_tup_read,
    CASE
        WHEN idx_scan = 0 THEN 'UNUSED'
        WHEN idx_tup_read::float / idx_scan > 100 THEN 'HIGH READ RATIO'
        ELSE 'OK'
    END AS status
FROM pg_stat_user_indexes
WHERE schemaname = 'public';
```

### Maintenance Operations

**Regular Maintenance**:
```sql
-- Analyze tables to update statistics (run weekly)
ANALYZE backtest_runs;
ANALYZE performance_metrics;

-- Reindex if bloat detected (run quarterly)
REINDEX INDEX CONCURRENTLY idx_backtest_runs_config_gin;
```

**Autovacuum Tuning** (for JSONB indexes):
```sql
-- Tune autovacuum for tables with GIN indexes
ALTER TABLE backtest_runs SET (
    autovacuum_vacuum_scale_factor = 0.05,  -- More aggressive
    autovacuum_analyze_scale_factor = 0.02
);
```

## Performance Testing Plan

### Test Scenarios

1. **Baseline Performance** (empty database):
   - Single record by ID: Should be <1ms
   - List 20 recent: Should be <5ms

2. **Loaded Database** (10,000 records):
   - Single record by ID: Should be <5ms
   - List 20 recent: Should be <20ms
   - Filter by strategy (2000 matches): Should be <30ms
   - Sort by Sharpe ratio (top 20): Should be <50ms
   - Comparison query (10 records): Should be <15ms

3. **Stress Test** (100,000 records):
   - All queries should meet performance targets
   - Cursor pagination Page 100: Should be <20ms
   - Offset pagination Page 100: Will be >500ms (expected)

### EXPLAIN ANALYZE Examples

```sql
-- Check if index is used
EXPLAIN (ANALYZE, BUFFERS, VERBOSE)
SELECT * FROM backtest_runs
WHERE strategy_name = 'SMA Crossover'
ORDER BY created_at DESC
LIMIT 20;

-- Expected plan:
-- Index Scan using idx_backtest_runs_strategy_created_id
-- Planning Time: 0.2ms
-- Execution Time: 15ms
```

**Red Flags in EXPLAIN**:
- `Seq Scan` instead of `Index Scan`: Missing or unused index
- `Sort` operation: Index doesn't match ORDER BY
- `Hash Join` for small result sets: Foreign key index missing
- High `Buffers: shared read` count: Cache misses

## Migration Strategy

### Phase 1: Essential Indexes (Deploy immediately)

```sql
-- Primary keys (automatic)
-- Foreign key index
CREATE INDEX idx_performance_metrics_run_id
ON performance_metrics (backtest_run_id);

-- Time-based index for listing
CREATE INDEX idx_backtest_runs_created_id
ON backtest_runs (created_at DESC, id DESC);

-- Strategy filter index
CREATE INDEX idx_backtest_runs_strategy_created_id
ON backtest_runs (strategy_name, created_at DESC, id DESC);
```

**Deployment**:
- Use `CREATE INDEX CONCURRENTLY` to avoid blocking writes
- Monitor during creation (GIN index may take minutes)

### Phase 2: Performance Indexes (Deploy after 1 week)

```sql
-- Instrument filter index
CREATE INDEX idx_backtest_runs_symbol_created_id
ON backtest_runs (instrument_symbol, created_at DESC, id DESC);

-- JSONB parameter search
CREATE INDEX idx_backtest_runs_config_gin
ON backtest_runs USING GIN (config_snapshot jsonb_path_ops);

-- Metrics sorting indexes
CREATE INDEX idx_performance_metrics_sharpe
ON performance_metrics (sharpe_ratio DESC, backtest_run_id);

CREATE INDEX idx_performance_metrics_return
ON performance_metrics (total_return DESC, backtest_run_id);
```

**Rationale**:
- Observe actual query patterns first
- Add indexes based on real usage
- Avoid over-indexing (write performance impact)

### Phase 3: Optional Optimizations (Deploy if needed)

```sql
-- Covering index for list queries (if heap reads are bottleneck)
CREATE INDEX idx_backtest_runs_list_covering
ON backtest_runs (created_at DESC, id DESC)
INCLUDE (strategy_name, instrument_symbol, status, duration_seconds);

-- Partial index for failed runs (if failure rate <10%)
CREATE INDEX idx_backtest_runs_failed
ON backtest_runs (created_at DESC, id DESC)
WHERE status = 'failed';
```

## Example SQL with Complete Indexes

### Schema Creation

```sql
-- Backtest runs table
CREATE TABLE backtest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    status VARCHAR(20) NOT NULL CHECK (status IN ('success', 'failed', 'interrupted')),
    strategy_name VARCHAR(100) NOT NULL,
    strategy_type VARCHAR(50) NOT NULL,
    instrument_symbol VARCHAR(20) NOT NULL,
    date_range_start DATE NOT NULL,
    date_range_end DATE NOT NULL,
    initial_capital NUMERIC(20, 2) NOT NULL,
    data_source VARCHAR(50) NOT NULL,
    config_snapshot JSONB NOT NULL,
    error_message TEXT,
    original_run_id UUID REFERENCES backtest_runs(id) ON DELETE SET NULL
);

-- Performance metrics table
CREATE TABLE performance_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    total_return NUMERIC(10, 4),
    cagr NUMERIC(10, 4),
    sharpe_ratio NUMERIC(8, 4),
    sortino_ratio NUMERIC(8, 4),
    max_drawdown NUMERIC(10, 4),
    volatility NUMERIC(10, 4),
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate NUMERIC(5, 4),
    profit_factor NUMERIC(10, 4),
    avg_win_amount NUMERIC(20, 2),
    avg_loss_amount NUMERIC(20, 2)
);

-- Essential indexes (Phase 1)
CREATE INDEX idx_performance_metrics_run_id
ON performance_metrics (backtest_run_id);

CREATE INDEX idx_backtest_runs_created_id
ON backtest_runs (created_at DESC, id DESC);

CREATE INDEX idx_backtest_runs_strategy_created_id
ON backtest_runs (strategy_name, created_at DESC, id DESC);

-- Performance indexes (Phase 2)
CREATE INDEX idx_backtest_runs_symbol_created_id
ON backtest_runs (instrument_symbol, created_at DESC, id DESC);

CREATE INDEX idx_backtest_runs_config_gin
ON backtest_runs USING GIN (config_snapshot jsonb_path_ops);

CREATE INDEX idx_performance_metrics_sharpe
ON performance_metrics (sharpe_ratio DESC, backtest_run_id);

CREATE INDEX idx_performance_metrics_return
ON performance_metrics (total_return DESC, backtest_run_id);

CREATE INDEX idx_performance_metrics_drawdown
ON performance_metrics (max_drawdown ASC, backtest_run_id);
```

### Example Queries with Expected Plans

#### Query 1: List Recent Backtests
```sql
SELECT
    br.id,
    br.created_at,
    br.strategy_name,
    br.instrument_symbol,
    br.status,
    pm.total_return,
    pm.sharpe_ratio,
    pm.max_drawdown
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- Expected Plan:
-- Nested Loop Left Join
--   -> Index Scan using idx_backtest_runs_created_id on backtest_runs br
--   -> Index Scan using idx_performance_metrics_run_id on performance_metrics pm
-- Execution Time: 10-20ms
```

#### Query 2: Filter by Strategy with Cursor Pagination
```sql
SELECT
    br.id,
    br.created_at,
    br.strategy_name,
    br.instrument_symbol,
    pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.strategy_name = 'SMA Crossover'
  AND (br.created_at, br.id) < ($1, $2)  -- Cursor
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- Expected Plan:
-- Nested Loop Left Join
--   -> Index Scan using idx_backtest_runs_strategy_created_id on backtest_runs br
--        Index Cond: (strategy_name = 'SMA Crossover' AND (created_at, id) < ($1, $2))
--   -> Index Scan using idx_performance_metrics_run_id on performance_metrics pm
-- Execution Time: 15-30ms
```

#### Query 3: Top Performers by Sharpe Ratio
```sql
SELECT
    br.id,
    br.strategy_name,
    br.instrument_symbol,
    br.created_at,
    pm.sharpe_ratio,
    pm.total_return,
    pm.max_drawdown
FROM performance_metrics pm
JOIN backtest_runs br ON pm.backtest_run_id = br.id
WHERE br.status = 'success'
ORDER BY pm.sharpe_ratio DESC
LIMIT 10;

-- Expected Plan:
-- Nested Loop
--   -> Index Scan using idx_performance_metrics_sharpe on performance_metrics pm
--   -> Index Scan using backtest_runs_pkey on backtest_runs br
--        Filter: (status = 'success')
-- Execution Time: 20-50ms
```

#### Query 4: Search by Configuration Parameters
```sql
SELECT
    br.id,
    br.strategy_name,
    br.created_at,
    br.config_snapshot->>'fast_period' as fast_period,
    br.config_snapshot->>'slow_period' as slow_period,
    pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.config_snapshot @> '{"fast_period": 10, "slow_period": 50}'
ORDER BY br.created_at DESC
LIMIT 20;

-- Expected Plan:
-- Sort (if no index on created_at after filter)
--   -> Nested Loop Left Join
--        -> Bitmap Heap Scan on backtest_runs br
--              Recheck Cond: (config_snapshot @> '{"fast_period": 10, "slow_period": 50}')
--              -> Bitmap Index Scan on idx_backtest_runs_config_gin
--        -> Index Scan using idx_performance_metrics_run_id on performance_metrics pm
-- Execution Time: 50-200ms (depends on selectivity)
```

#### Query 5: Comparison (Multiple UUIDs)
```sql
SELECT
    br.id,
    br.strategy_name,
    br.instrument_symbol,
    br.created_at,
    br.config_snapshot,
    pm.*
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.id = ANY($1::uuid[])  -- Array of UUIDs
ORDER BY br.created_at DESC;

-- Expected Plan:
-- Sort
--   -> Nested Loop Left Join
--        -> Bitmap Heap Scan on backtest_runs br
--              Recheck Cond: (id = ANY($1))
--              -> Bitmap Index Scan on backtest_runs_pkey
--        -> Index Scan using idx_performance_metrics_run_id on performance_metrics pm
-- Execution Time: 5-15ms (for 10 UUIDs)
```

## Key Recommendations Summary

### Index Strategy
1. **Create composite indexes** with equality columns before range columns
2. **Use cursor pagination** for time-ordered queries (17x faster than offset)
3. **Choose `jsonb_path_ops`** for JSONB config storage (smaller, faster)
4. **Add foreign key indexes** explicitly (PostgreSQL doesn't auto-create them)
5. **Include frequently accessed columns** in covering indexes for Index-Only Scans
6. **Consider partial indexes** for rare status values (e.g., failures <10%)

### Query Patterns
1. **Always paginate** with LIMIT (never return unbounded results)
2. **Use prepared statements** with parameters (prevents SQL injection, better caching)
3. **Avoid SELECT *** in production (specify columns for better performance)
4. **Batch comparison queries** instead of N+1 queries (use `id = ANY($1)`)
5. **Cache dashboard queries** for 30-60 seconds if real-time not required

### Pagination Approach
1. **Primary recommendation: Cursor pagination**
   - Use `(created_at, id)` composite cursor
   - Encode as base64 JSON for API responses
   - Constant-time performance regardless of page depth
2. **Fallback: Offset/Limit with low maximum**
   - Limit offset to <1000 for acceptable performance
   - Warn users if requesting deep pages

### Monitoring
1. **Track index usage** weekly via `pg_stat_user_indexes`
2. **Run EXPLAIN ANALYZE** on slow queries (>100ms)
3. **Monitor index bloat** quarterly and REINDEX if needed
4. **Analyze tables** weekly to update query planner statistics

## Additional Resources

- PostgreSQL Official Docs: Index Types - https://www.postgresql.org/docs/current/indexes-types.html
- Use The Index, Luke! - https://use-the-index-luke.com/ (excellent B-tree guide)
- Cursor Pagination Guide (2025) - https://bun.uptrace.dev/guide/cursor-pagination.html
- JSONB Performance Tips - https://www.postgresql.org/docs/current/datatype-json.html#JSON-INDEXING

## Next Steps

1. Implement schema with Phase 1 indexes in Alembic migration
2. Write repository methods using cursor pagination
3. Add query performance tests with EXPLAIN ANALYZE assertions
4. Monitor actual query patterns in production for 1 week
5. Add Phase 2 indexes based on observed usage
6. Profile slow queries and optimize as needed
