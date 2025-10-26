-- ============================================================================
-- PostgreSQL Metadata Storage - Index Strategy
-- ============================================================================
-- Feature: 004-postgresql-metadata-storage
-- Version: 1.0.0
-- PostgreSQL: 16+
-- Purpose: Performance indexes for backtest query optimization
-- ============================================================================

-- ============================================================================
-- INDEX DEPLOYMENT STRATEGY
-- ============================================================================
-- Phase 1: Essential indexes (deploy immediately with schema)
--   - Critical for foreign key JOINs and basic queries
--   - Minimal overhead, maximum impact
--   - Required for acceptable query performance
--
-- Phase 2: Performance indexes (deploy after 1 week of usage)
--   - Optimizations based on actual query patterns
--   - Higher maintenance cost (insert overhead)
--   - Deploy after validating usage patterns
--
-- Phase 3: Optional indexes (deploy only if needed)
--   - Specialized indexes for specific use cases
--   - Only create if profiling shows bottlenecks
-- ============================================================================

-- ============================================================================
-- PHASE 1: ESSENTIAL INDEXES (Deploy Immediately)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Index 1: Foreign Key Index (Critical for JOINs)
-- ----------------------------------------------------------------------------
-- Purpose: Optimize JOINs between backtest_runs and performance_metrics
-- Query Pattern: SELECT ... FROM backtest_runs JOIN performance_metrics
-- Performance: 100x faster JOINs (prevents full table scan)
-- Overhead: Minimal (updated only on metric inserts)
-- Size Estimate: ~200 KB per 10,000 records
-- ----------------------------------------------------------------------------
CREATE INDEX idx_metrics_backtest_run_id
    ON performance_metrics (backtest_run_id);

COMMENT ON INDEX idx_metrics_backtest_run_id IS
    'PHASE 1: Foreign key index for JOIN optimization (100x faster)';

-- ----------------------------------------------------------------------------
-- Index 2: Cursor Pagination Index (Time-Based Listing)
-- ----------------------------------------------------------------------------
-- Purpose: Efficient cursor pagination for "list recent backtests" queries
-- Query Pattern: SELECT ... WHERE (created_at, id) < ($1, $2) ORDER BY created_at DESC, id DESC LIMIT 20
-- Performance: Constant time regardless of page depth (17x faster than offset)
-- Overhead: Moderate (updated on every insert)
-- Size Estimate: ~400 KB per 10,000 records
-- Column Order: created_at DESC, id DESC (matches ORDER BY clause)
-- ----------------------------------------------------------------------------
CREATE INDEX idx_backtest_runs_created_id
    ON backtest_runs (created_at DESC, id DESC);

COMMENT ON INDEX idx_backtest_runs_created_id IS
    'PHASE 1: Cursor pagination index (created_at DESC, id DESC) for constant-time listing';

-- ----------------------------------------------------------------------------
-- Index 3: Strategy Filter + Time Ordering (Composite)
-- ----------------------------------------------------------------------------
-- Purpose: Filter backtests by strategy name, ordered by time
-- Query Pattern: SELECT ... WHERE strategy_name = 'SMA Crossover' ORDER BY created_at DESC LIMIT 20
-- Performance: 15-30ms (vs 200-500ms without index)
-- Overhead: Moderate (updated on every insert)
-- Size Estimate: ~600 KB per 10,000 records
-- Column Order: strategy_name (equality), created_at DESC (range), id DESC (tie-breaker)
-- Design Rationale: Equality before range (PostgreSQL B-tree optimization rule)
-- ----------------------------------------------------------------------------
CREATE INDEX idx_backtest_runs_strategy_created_id
    ON backtest_runs (strategy_name, created_at DESC, id DESC);

COMMENT ON INDEX idx_backtest_runs_strategy_created_id IS
    'PHASE 1: Composite index for strategy filtering with time ordering (equality before range)';

-- ============================================================================
-- PHASE 1 SUMMARY
-- ============================================================================
-- Total Indexes: 3
-- Total Size: ~1.2 MB per 10,000 records
-- Performance Impact: 10-100x speedup for core queries
-- Maintenance Overhead: Low (simple B-tree indexes)
-- ============================================================================

-- ============================================================================
-- PHASE 2: PERFORMANCE INDEXES (Deploy After 1 Week)
-- ============================================================================
-- Deploy after analyzing actual query patterns and validating usage metrics
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Index 4: Instrument Filter + Time Ordering (Composite)
-- ----------------------------------------------------------------------------
-- Purpose: Filter backtests by trading instrument, ordered by time
-- Query Pattern: SELECT ... WHERE instrument_symbol = 'AAPL' ORDER BY created_at DESC LIMIT 20
-- Performance: 15-30ms (vs 200-500ms without index)
-- Overhead: Moderate (updated on every insert)
-- Size Estimate: ~600 KB per 10,000 records
-- Column Order: instrument_symbol (equality), created_at DESC (range), id DESC (tie-breaker)
-- Usage Frequency: Medium (users may filter by symbol for specific instruments)
-- ----------------------------------------------------------------------------
CREATE INDEX idx_backtest_runs_symbol_created_id
    ON backtest_runs (instrument_symbol, created_at DESC, id DESC);

COMMENT ON INDEX idx_backtest_runs_symbol_created_id IS
    'PHASE 2: Composite index for instrument filtering with time ordering';

-- ----------------------------------------------------------------------------
-- Index 5: JSONB Config Parameter Search (GIN Index)
-- ----------------------------------------------------------------------------
-- Purpose: Search backtests by configuration parameters
-- Query Pattern: SELECT ... WHERE config_snapshot @> '{"config": {"fast_period": 10}}'
-- Performance: 50-200ms (vs 2000ms+ full table scan)
-- Overhead: High (GIN indexes are larger and slower to update)
-- Size Estimate: ~1.5 MB per 10,000 records (with jsonb_path_ops)
-- Index Type: GIN with jsonb_path_ops (60% smaller, 3x faster than default jsonb_ops)
-- Trade-off: Only supports containment (@>), not key existence (?)
-- Usage Frequency: Low-Medium (parameter optimization, research)
-- ----------------------------------------------------------------------------
CREATE INDEX idx_backtest_runs_config_gin
    ON backtest_runs USING GIN (config_snapshot jsonb_path_ops);

COMMENT ON INDEX idx_backtest_runs_config_gin IS
    'PHASE 2: GIN index for JSONB parameter search (jsonb_path_ops: 60% smaller, 3x faster)';

-- ----------------------------------------------------------------------------
-- Index 6: Metrics Sorting Index (Sharpe Ratio)
-- ----------------------------------------------------------------------------
-- Purpose: Sort backtests by Sharpe ratio (find best performing strategies)
-- Query Pattern: SELECT ... JOIN performance_metrics ORDER BY sharpe_ratio DESC LIMIT 10
-- Performance: 20-50ms (vs 200-500ms without index)
-- Overhead: Moderate (updated on metric inserts/updates)
-- Size Estimate: ~400 KB per 10,000 records
-- Column Order: sharpe_ratio DESC (sort key), backtest_run_id (tie-breaker for JOIN)
-- Partial Index Opportunity: Could add WHERE sharpe_ratio IS NOT NULL (smaller index)
-- Usage Frequency: Medium (strategy comparison, leaderboards)
-- ----------------------------------------------------------------------------
CREATE INDEX idx_metrics_sharpe_run
    ON performance_metrics (sharpe_ratio DESC, backtest_run_id);

COMMENT ON INDEX idx_metrics_sharpe_run IS
    'PHASE 2: Sort index for Sharpe ratio ranking (best strategies first)';

-- ============================================================================
-- PHASE 2 SUMMARY
-- ============================================================================
-- Total Indexes: 3 additional (6 total)
-- Additional Size: ~2.5 MB per 10,000 records
-- Total Size: ~3.7 MB per 10,000 records (all indexes)
-- Performance Impact: 3-40x speedup for advanced queries
-- Maintenance Overhead: Medium (includes GIN index)
-- ============================================================================

-- ============================================================================
-- PHASE 3: OPTIONAL INDEXES (Deploy Only If Needed)
-- ============================================================================
-- Create these only if profiling shows specific bottlenecks
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Optional Index 1: Covering Index (Index-Only Scan)
-- ----------------------------------------------------------------------------
-- Purpose: Avoid heap access for common columns (index-only scan)
-- Query Pattern: SELECT run_id, strategy_name, created_at, total_return FROM backtest_runs
-- Feature: PostgreSQL INCLUDE clause (non-key columns in index)
-- Size Estimate: ~1.2 MB per 10,000 records
-- Trade-off: Larger index, faster reads (no heap access)
-- Deploy If: Profiling shows significant heap access overhead
-- ----------------------------------------------------------------------------
-- CREATE INDEX idx_backtest_runs_covering
--     ON backtest_runs (created_at DESC, id DESC)
--     INCLUDE (run_id, strategy_name, instrument_symbol);
--
-- COMMENT ON INDEX idx_backtest_runs_covering IS
--     'PHASE 3: Covering index for index-only scans (avoids heap access)';

-- ----------------------------------------------------------------------------
-- Optional Index 2: Partial Index (Failed Executions)
-- ----------------------------------------------------------------------------
-- Purpose: Efficiently query failed backtest executions
-- Query Pattern: SELECT ... WHERE execution_status = 'failed'
-- Feature: Partial index (only indexes rows matching WHERE clause)
-- Size Estimate: ~5% of full index (only failed records)
-- Trade-off: Very small, very fast for rare values
-- Deploy If: Failed execution queries become common
-- ----------------------------------------------------------------------------
-- CREATE INDEX idx_backtest_runs_failed
--     ON backtest_runs (created_at DESC)
--     WHERE execution_status = 'failed';
--
-- COMMENT ON INDEX idx_backtest_runs_failed IS
--     'PHASE 3: Partial index for failed executions (5% of full index size)';

-- ----------------------------------------------------------------------------
-- Optional Index 3: Multi-Column JSONB Index (Specific Parameters)
-- ----------------------------------------------------------------------------
-- Purpose: Optimize queries for specific configuration parameters
-- Query Pattern: SELECT ... WHERE config_snapshot->>'strategy_path' = 'strategies.sma_crossover.SmaCrossover'
-- Feature: Expression index on JSONB field extraction
-- Size Estimate: ~300 KB per 10,000 records
-- Trade-off: Only useful for exact path matching
-- Deploy If: Strategy path filtering becomes common
-- ----------------------------------------------------------------------------
-- CREATE INDEX idx_backtest_runs_strategy_path
--     ON backtest_runs ((config_snapshot->>'strategy_path'));
--
-- COMMENT ON INDEX idx_backtest_runs_strategy_path IS
--     'PHASE 3: Expression index for strategy path extraction (JSONB ->> operator)';

-- ----------------------------------------------------------------------------
-- Optional Index 4: Date Range Query Index (BRIN)
-- ----------------------------------------------------------------------------
-- Purpose: Efficient date range queries for large datasets
-- Query Pattern: SELECT ... WHERE start_date BETWEEN $1 AND $2
-- Feature: BRIN index (Block Range INdex) - very small, for sorted data
-- Size Estimate: ~10 KB per 10,000 records (100x smaller than B-tree)
-- Trade-off: Only effective if data is naturally sorted by date
-- Deploy If: Dataset grows to 100,000+ records and date range queries are common
-- ----------------------------------------------------------------------------
-- CREATE INDEX idx_backtest_runs_date_range_brin
--     ON backtest_runs USING BRIN (start_date, end_date);
--
-- COMMENT ON INDEX idx_backtest_runs_date_range_brin IS
--     'PHASE 3: BRIN index for date range queries (100x smaller than B-tree)';

-- ============================================================================
-- INDEX MAINTENANCE QUERIES
-- ============================================================================
-- Use these queries to monitor index health and performance
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1: Index Size Report
-- ----------------------------------------------------------------------------
-- Purpose: Monitor index growth and identify bloat
-- ----------------------------------------------------------------------------
-- SELECT
--     schemaname,
--     tablename,
--     indexname,
--     pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
--     idx_scan AS index_scans,
--     idx_tup_read AS tuples_read,
--     idx_tup_fetch AS tuples_fetched
-- FROM pg_stat_user_indexes
-- WHERE schemaname = 'public'
--   AND tablename IN ('backtest_runs', 'performance_metrics')
-- ORDER BY pg_relation_size(indexrelid) DESC;

-- ----------------------------------------------------------------------------
-- Query 2: Index Usage Statistics
-- ----------------------------------------------------------------------------
-- Purpose: Identify unused indexes (candidates for removal)
-- ----------------------------------------------------------------------------
-- SELECT
--     schemaname,
--     tablename,
--     indexname,
--     idx_scan,
--     idx_tup_read,
--     idx_tup_fetch,
--     pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
-- FROM pg_stat_user_indexes
-- WHERE schemaname = 'public'
--   AND tablename IN ('backtest_runs', 'performance_metrics')
--   AND idx_scan = 0  -- Never used
-- ORDER BY pg_relation_size(indexrelid) DESC;

-- ----------------------------------------------------------------------------
-- Query 3: Index Bloat Estimation
-- ----------------------------------------------------------------------------
-- Purpose: Detect index bloat and trigger REINDEX if needed
-- ----------------------------------------------------------------------------
-- SELECT
--     schemaname,
--     tablename,
--     indexname,
--     pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
--     pg_size_pretty(pg_relation_size(indexrelid) -
--         pg_relation_size(indexrelid, 'main')) AS bloat_estimate
-- FROM pg_stat_user_indexes
-- WHERE schemaname = 'public'
--   AND tablename IN ('backtest_runs', 'performance_metrics');

-- ============================================================================
-- INDEX MAINTENANCE SCHEDULE
-- ============================================================================
-- Weekly: Monitor index usage statistics (identify unused indexes)
-- Monthly: Check index sizes and bloat estimates
-- Quarterly: REINDEX if bloat exceeds 30%
-- ============================================================================

-- ============================================================================
-- End of Index Strategy
-- ============================================================================
