-- ============================================================================
-- PostgreSQL Metadata Storage - Sample Queries
-- ============================================================================
-- Feature: 004-postgresql-metadata-storage
-- Version: 1.0.0
-- PostgreSQL: 16+
-- Purpose: Example queries demonstrating common usage patterns
-- ============================================================================

-- ============================================================================
-- SECTION 1: INSERT OPERATIONS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 1.1: Insert Backtest Run with Metrics (Transaction)
-- ----------------------------------------------------------------------------
-- Purpose: Complete backtest save operation in a single transaction
-- Use Case: Automatic persistence after backtest execution
-- Performance: <10ms (with Phase 1 indexes)
-- ----------------------------------------------------------------------------

BEGIN;

-- Insert backtest run metadata
INSERT INTO backtest_runs (
    run_id,
    strategy_name,
    strategy_type,
    instrument_symbol,
    start_date,
    end_date,
    config_snapshot,
    execution_status,
    created_at
)
VALUES (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890'::uuid,
    'SMA Crossover',
    'trend_following',
    'AAPL',
    '2023-01-01 00:00:00+00',
    '2023-12-31 23:59:59+00',
    jsonb_build_object(
        'strategy_path', 'strategies.sma_crossover.SmaCrossover',
        'config_path', 'configs/sma_crossover.yaml',
        'config', jsonb_build_object(
            'fast_period', 10,
            'slow_period', 20,
            'position_size', 100
        ),
        'version', '1.0'
    ),
    'success',
    NOW()
)
RETURNING id, run_id, created_at;

-- Insert performance metrics (using RETURNING id from above)
INSERT INTO performance_metrics (
    backtest_run_id,
    total_return,
    final_balance,
    sharpe_ratio,
    sortino_ratio,
    max_drawdown,
    max_drawdown_date,
    calmar_ratio,
    volatility,
    total_trades,
    winning_trades,
    losing_trades,
    win_rate,
    profit_factor,
    expectancy,
    avg_win,
    avg_loss,
    created_at
)
VALUES (
    (SELECT id FROM backtest_runs WHERE run_id = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'::uuid),
    0.1542,          -- 15.42% return
    115420.00,       -- $115,420 final balance
    1.85,            -- Sharpe ratio
    2.12,            -- Sortino ratio
    -0.0823,         -- -8.23% max drawdown
    '2023-06-15 14:30:00+00',
    1.87,            -- Calmar ratio
    0.1234,          -- 12.34% volatility
    127,             -- Total trades
    78,              -- Winning trades
    49,              -- Losing trades
    0.6142,          -- 61.42% win rate
    2.34,            -- Profit factor
    121.42,          -- $121.42 expectancy per trade
    450.32,          -- $450.32 avg win
    -280.15,         -- -$280.15 avg loss
    NOW()
)
RETURNING id, backtest_run_id, sharpe_ratio;

COMMIT;

-- ============================================================================
-- SECTION 2: LIST QUERIES (Pagination)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 2.1: List 20 Most Recent Backtests (First Page)
-- ----------------------------------------------------------------------------
-- Purpose: Default "history" view - show latest backtests
-- Performance: 10-20ms (uses idx_backtest_runs_created_id)
-- CLI Command: ntrader backtest history
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.start_date,
    br.end_date,
    br.execution_status,
    br.created_at,
    pm.total_return,
    pm.sharpe_ratio,
    pm.max_drawdown,
    pm.total_trades
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- ----------------------------------------------------------------------------
-- Query 2.2: Cursor Pagination (Next Page)
-- ----------------------------------------------------------------------------
-- Purpose: Navigate to subsequent pages using cursor
-- Performance: Constant time (10-20ms) regardless of page depth
-- Cursor Format: (last_created_at, last_id) from previous page
-- ----------------------------------------------------------------------------

-- Example: Next page after cursor (created_at='2023-12-15 10:30:00', id=12345)
SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.start_date,
    br.end_date,
    br.created_at,
    pm.total_return,
    pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE (br.created_at, br.id) < ('2023-12-15 10:30:00+00'::timestamptz, 12345)
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- ============================================================================
-- SECTION 3: FILTER QUERIES
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 3.1: Filter by Strategy Name
-- ----------------------------------------------------------------------------
-- Purpose: Show all backtests for a specific strategy
-- Performance: 15-30ms (uses idx_backtest_runs_strategy_created_id)
-- CLI Command: ntrader backtest history --strategy "SMA Crossover"
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.created_at,
    pm.total_return,
    pm.sharpe_ratio,
    pm.total_trades
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.strategy_name = 'SMA Crossover'
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- ----------------------------------------------------------------------------
-- Query 3.2: Filter by Instrument Symbol
-- ----------------------------------------------------------------------------
-- Purpose: Show all backtests for a specific trading instrument
-- Performance: 15-30ms (uses idx_backtest_runs_symbol_created_id - Phase 2)
-- CLI Command: ntrader backtest history --symbol AAPL
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.created_at,
    pm.total_return,
    pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.instrument_symbol = 'AAPL'
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- ----------------------------------------------------------------------------
-- Query 3.3: Filter by Date Range
-- ----------------------------------------------------------------------------
-- Purpose: Show backtests executed within a specific time period
-- Performance: 20-50ms (seq scan or BRIN index if Phase 3 deployed)
-- CLI Command: ntrader backtest history --from 2023-01-01 --to 2023-12-31
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.created_at,
    pm.total_return,
    pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.created_at BETWEEN '2023-01-01'::timestamptz AND '2023-12-31'::timestamptz
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- ----------------------------------------------------------------------------
-- Query 3.4: Filter by Execution Status
-- ----------------------------------------------------------------------------
-- Purpose: Show only successful/failed backtests
-- Performance: 10-30ms (seq scan or partial index if Phase 3 deployed)
-- CLI Command: ntrader backtest history --status failed
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.execution_status,
    br.error_message,
    br.created_at
FROM backtest_runs br
WHERE br.execution_status = 'failed'
ORDER BY br.created_at DESC, br.id DESC
LIMIT 20;

-- ============================================================================
-- SECTION 4: JSONB PARAMETER SEARCH
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 4.1: Find Backtests by Specific Parameter Value
-- ----------------------------------------------------------------------------
-- Purpose: Search for backtests with specific configuration parameters
-- Performance: 50-200ms (uses idx_backtest_runs_config_gin - Phase 2)
-- CLI Command: ntrader backtest history --param fast_period=10
-- Operator: @> (containment - JSONB contains path)
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.config_snapshot->'config' AS config_params,
    br.created_at,
    pm.total_return,
    pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.config_snapshot @> '{"config": {"fast_period": 10}}'::jsonb
ORDER BY br.created_at DESC
LIMIT 20;

-- ----------------------------------------------------------------------------
-- Query 4.2: Find Backtests by Multiple Parameters
-- ----------------------------------------------------------------------------
-- Purpose: Search for backtests with multiple configuration parameters
-- Performance: 50-200ms (uses idx_backtest_runs_config_gin - Phase 2)
-- Operator: @> (all specified parameters must match)
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.config_snapshot->'config' AS config_params,
    pm.total_return,
    pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.config_snapshot @> jsonb_build_object(
    'config', jsonb_build_object(
        'fast_period', 10,
        'slow_period', 20
    )
)
ORDER BY br.created_at DESC
LIMIT 20;

-- ----------------------------------------------------------------------------
-- Query 4.3: Extract Specific Parameter Value
-- ----------------------------------------------------------------------------
-- Purpose: Retrieve specific parameter value from JSONB config
-- Performance: 10-20ms (with index on extracted value - Phase 3 optional)
-- Operator: -> (JSONB object access), ->> (text extraction)
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.config_snapshot->>'strategy_path' AS strategy_path,
    br.config_snapshot->'config'->>'fast_period' AS fast_period,
    br.config_snapshot->'config'->>'slow_period' AS slow_period,
    pm.total_return,
    pm.sharpe_ratio
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.config_snapshot->>'strategy_path' = 'strategies.sma_crossover.SmaCrossover'
ORDER BY br.created_at DESC
LIMIT 20;

-- ============================================================================
-- SECTION 5: COMPARISON QUERIES
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 5.1: Compare 10 Backtests (Specific IDs)
-- ----------------------------------------------------------------------------
-- Purpose: Side-by-side comparison of selected backtests
-- Performance: 5-15ms (primary key lookups)
-- CLI Command: ntrader backtest compare <id1> <id2> ... <id10>
-- Output: Comparison table with key metrics
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.start_date,
    br.end_date,
    br.config_snapshot->'config' AS config_params,
    pm.total_return,
    pm.sharpe_ratio,
    pm.sortino_ratio,
    pm.max_drawdown,
    pm.calmar_ratio,
    pm.volatility,
    pm.total_trades,
    pm.win_rate,
    pm.profit_factor,
    pm.expectancy
FROM backtest_runs br
INNER JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.run_id IN (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890'::uuid,
    'b2c3d4e5-f6a7-8901-bcde-f12345678901'::uuid,
    'c3d4e5f6-a7b8-9012-cdef-123456789012'::uuid
    -- Add up to 10 UUIDs
)
ORDER BY pm.sharpe_ratio DESC;

-- ----------------------------------------------------------------------------
-- Query 5.2: Top 10 Backtests by Sharpe Ratio
-- ----------------------------------------------------------------------------
-- Purpose: Leaderboard of best performing strategies
-- Performance: 20-50ms (uses idx_metrics_sharpe_run - Phase 2)
-- CLI Command: ntrader backtest top --metric sharpe_ratio --limit 10
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.instrument_symbol,
    br.created_at,
    pm.sharpe_ratio,
    pm.total_return,
    pm.max_drawdown,
    pm.total_trades
FROM performance_metrics pm
INNER JOIN backtest_runs br ON pm.backtest_run_id = br.id
WHERE pm.sharpe_ratio IS NOT NULL
ORDER BY pm.sharpe_ratio DESC, pm.backtest_run_id
LIMIT 10;

-- ----------------------------------------------------------------------------
-- Query 5.3: Compare Average Metrics by Strategy
-- ----------------------------------------------------------------------------
-- Purpose: Aggregate comparison across all backtests per strategy
-- Performance: 50-200ms (depends on data size)
-- CLI Command: ntrader backtest stats --group-by strategy
-- Output: Strategy-level statistics
-- ----------------------------------------------------------------------------

SELECT
    br.strategy_name,
    COUNT(*) AS total_runs,
    AVG(pm.total_return) AS avg_return,
    AVG(pm.sharpe_ratio) AS avg_sharpe,
    AVG(pm.max_drawdown) AS avg_drawdown,
    AVG(pm.win_rate) AS avg_win_rate,
    AVG(pm.total_trades) AS avg_trades,
    MIN(pm.sharpe_ratio) AS min_sharpe,
    MAX(pm.sharpe_ratio) AS max_sharpe
FROM backtest_runs br
INNER JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.execution_status = 'success'
  AND pm.sharpe_ratio IS NOT NULL
GROUP BY br.strategy_name
ORDER BY avg_sharpe DESC;

-- ============================================================================
-- SECTION 6: REPRODUCE BACKTEST QUERY
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 6.1: Retrieve Complete Configuration for Reproduction
-- ----------------------------------------------------------------------------
-- Purpose: Get all data needed to reproduce a specific backtest
-- Performance: 1-5ms (primary key lookup)
-- CLI Command: ntrader backtest reproduce <run_id>
-- Output: Complete configuration snapshot for re-execution
-- ----------------------------------------------------------------------------

SELECT
    br.run_id,
    br.strategy_name,
    br.strategy_type,
    br.instrument_symbol,
    br.start_date,
    br.end_date,
    br.config_snapshot,
    br.execution_status,
    br.created_at,
    -- Metrics for comparison after re-run
    pm.total_return AS original_return,
    pm.sharpe_ratio AS original_sharpe,
    pm.max_drawdown AS original_drawdown,
    pm.total_trades AS original_trades
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.run_id = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'::uuid;

-- ============================================================================
-- SECTION 7: ANALYTICAL QUERIES
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 7.1: Parameter Sensitivity Analysis
-- ----------------------------------------------------------------------------
-- Purpose: Analyze how a specific parameter affects performance
-- Performance: 100-500ms (depends on data size and JSONB index)
-- Use Case: Optimization research - find optimal parameter values
-- ----------------------------------------------------------------------------

SELECT
    br.config_snapshot->'config'->>'fast_period' AS fast_period,
    COUNT(*) AS num_runs,
    AVG(pm.sharpe_ratio) AS avg_sharpe,
    AVG(pm.total_return) AS avg_return,
    AVG(pm.max_drawdown) AS avg_drawdown,
    STDDEV(pm.sharpe_ratio) AS sharpe_stddev
FROM backtest_runs br
INNER JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.strategy_name = 'SMA Crossover'
  AND pm.sharpe_ratio IS NOT NULL
GROUP BY br.config_snapshot->'config'->>'fast_period'
ORDER BY avg_sharpe DESC;

-- ----------------------------------------------------------------------------
-- Query 7.2: Time-Based Performance Trends
-- ----------------------------------------------------------------------------
-- Purpose: Analyze if strategy performance changes over time
-- Performance: 50-200ms
-- Use Case: Detect strategy degradation or improvement
-- ----------------------------------------------------------------------------

SELECT
    DATE_TRUNC('month', br.created_at) AS month,
    br.strategy_name,
    COUNT(*) AS num_runs,
    AVG(pm.sharpe_ratio) AS avg_sharpe,
    AVG(pm.total_return) AS avg_return,
    AVG(pm.win_rate) AS avg_win_rate
FROM backtest_runs br
INNER JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.execution_status = 'success'
  AND pm.sharpe_ratio IS NOT NULL
GROUP BY DATE_TRUNC('month', br.created_at), br.strategy_name
ORDER BY month DESC, avg_sharpe DESC;

-- ----------------------------------------------------------------------------
-- Query 7.3: Best Parameter Combinations
-- ----------------------------------------------------------------------------
-- Purpose: Find optimal parameter combinations for a strategy
-- Performance: 200-500ms (complex JSONB extraction)
-- Use Case: Parameter optimization results
-- ----------------------------------------------------------------------------

SELECT
    br.config_snapshot->'config'->>'fast_period' AS fast_period,
    br.config_snapshot->'config'->>'slow_period' AS slow_period,
    br.config_snapshot->'config'->>'position_size' AS position_size,
    COUNT(*) AS num_runs,
    AVG(pm.sharpe_ratio) AS avg_sharpe,
    AVG(pm.total_return) AS avg_return,
    MAX(pm.sharpe_ratio) AS best_sharpe
FROM backtest_runs br
INNER JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.strategy_name = 'SMA Crossover'
  AND br.instrument_symbol = 'AAPL'
  AND pm.sharpe_ratio IS NOT NULL
GROUP BY
    br.config_snapshot->'config'->>'fast_period',
    br.config_snapshot->'config'->>'slow_period',
    br.config_snapshot->'config'->>'position_size'
HAVING COUNT(*) >= 3  -- Minimum 3 runs per combination
ORDER BY avg_sharpe DESC
LIMIT 20;

-- ============================================================================
-- SECTION 8: MAINTENANCE QUERIES
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 8.1: Database Statistics
-- ----------------------------------------------------------------------------
-- Purpose: Monitor database growth and health
-- Performance: <10ms
-- ----------------------------------------------------------------------------

SELECT
    'backtest_runs' AS table_name,
    COUNT(*) AS row_count,
    pg_size_pretty(pg_total_relation_size('backtest_runs')) AS total_size,
    pg_size_pretty(pg_relation_size('backtest_runs')) AS table_size,
    pg_size_pretty(pg_indexes_size('backtest_runs')) AS indexes_size
FROM backtest_runs
UNION ALL
SELECT
    'performance_metrics' AS table_name,
    COUNT(*) AS row_count,
    pg_size_pretty(pg_total_relation_size('performance_metrics')) AS total_size,
    pg_size_pretty(pg_relation_size('performance_metrics')) AS table_size,
    pg_size_pretty(pg_indexes_size('performance_metrics')) AS indexes_size
FROM performance_metrics;

-- ----------------------------------------------------------------------------
-- Query 8.2: Delete Old Backtests (Cleanup)
-- ----------------------------------------------------------------------------
-- Purpose: Remove backtest records older than retention period
-- Performance: Depends on volume (use batching for large deletes)
-- Cascade: Automatically deletes related performance_metrics
-- ----------------------------------------------------------------------------

-- Example: Delete backtests older than 1 year
-- WARNING: Test in staging first!
-- DELETE FROM backtest_runs
-- WHERE created_at < NOW() - INTERVAL '1 year'
--   AND execution_status = 'success';  -- Keep failed runs for debugging

-- Safer approach: Delete in batches of 1000
-- DO $$
-- DECLARE
--     deleted_count INTEGER;
-- BEGIN
--     LOOP
--         DELETE FROM backtest_runs
--         WHERE id IN (
--             SELECT id FROM backtest_runs
--             WHERE created_at < NOW() - INTERVAL '1 year'
--             LIMIT 1000
--         );
--
--         GET DIAGNOSTICS deleted_count = ROW_COUNT;
--         EXIT WHEN deleted_count = 0;
--
--         RAISE NOTICE 'Deleted % rows', deleted_count;
--         PERFORM pg_sleep(1);  -- Throttle to avoid lock contention
--     END LOOP;
-- END $$;

-- ============================================================================
-- SECTION 9: VALIDATION QUERIES
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Query 9.1: Data Integrity Checks
-- ----------------------------------------------------------------------------
-- Purpose: Verify referential integrity and data quality
-- Performance: <50ms
-- ----------------------------------------------------------------------------

-- Check for orphaned metrics (should be 0 due to foreign key constraint)
SELECT COUNT(*) AS orphaned_metrics
FROM performance_metrics pm
WHERE NOT EXISTS (
    SELECT 1 FROM backtest_runs br WHERE br.id = pm.backtest_run_id
);

-- Check for runs without metrics
SELECT COUNT(*) AS runs_without_metrics
FROM backtest_runs br
WHERE NOT EXISTS (
    SELECT 1 FROM performance_metrics pm WHERE pm.backtest_run_id = br.id
);

-- Check for invalid JSONB configs (missing required fields)
SELECT COUNT(*) AS invalid_configs
FROM backtest_runs
WHERE NOT (
    config_snapshot ? 'strategy_path' AND
    config_snapshot ? 'config_path' AND
    config_snapshot ? 'config'
);

-- ============================================================================
-- End of Sample Queries
-- ============================================================================
