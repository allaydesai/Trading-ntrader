-- ============================================================================
-- PostgreSQL Metadata Storage Schema - DDL for backtest persistence
-- ============================================================================
-- Feature: 004-postgresql-metadata-storage
-- Version: 1.0.0
-- PostgreSQL: 16+
-- Purpose: Store backtest execution metadata and performance metrics
-- ============================================================================

-- ============================================================================
-- Table: backtest_runs
-- ============================================================================
-- Purpose: Store backtest execution metadata (configuration, date ranges, instruments)
-- Records: One per backtest execution
-- Relationships: Parent to performance_metrics (1:1)
-- ============================================================================

CREATE TABLE backtest_runs (
    -- ========================================================================
    -- Primary Key
    -- ========================================================================
    id BIGSERIAL PRIMARY KEY,

    -- ========================================================================
    -- Business Identifier
    -- ========================================================================
    run_id UUID NOT NULL UNIQUE
        CONSTRAINT run_id_not_null CHECK (run_id IS NOT NULL),

    -- ========================================================================
    -- Strategy Metadata
    -- ========================================================================
    strategy_name VARCHAR(100) NOT NULL
        CONSTRAINT strategy_name_not_empty CHECK (LENGTH(TRIM(strategy_name)) > 0),

    strategy_type VARCHAR(50) NOT NULL
        CONSTRAINT strategy_type_not_empty CHECK (LENGTH(TRIM(strategy_type)) > 0),

    -- ========================================================================
    -- Instrument and Time Period
    -- ========================================================================
    instrument_symbol VARCHAR(20) NOT NULL
        CONSTRAINT instrument_symbol_not_empty CHECK (LENGTH(TRIM(instrument_symbol)) > 0),

    start_date TIMESTAMPTZ NOT NULL,

    end_date TIMESTAMPTZ NOT NULL,

    -- ========================================================================
    -- Date Range Validation
    -- ========================================================================
    CONSTRAINT valid_date_range CHECK (end_date >= start_date),

    -- ========================================================================
    -- Configuration Snapshot (JSONB)
    -- ========================================================================
    -- Stores complete strategy configuration for reproducibility
    -- Expected structure:
    -- {
    --   "strategy_path": "strategies.sma_crossover.SmaCrossover",
    --   "config_path": "configs/sma_crossover.yaml",
    --   "config": {
    --     "fast_period": 10,
    --     "slow_period": 20,
    --     ...
    --   },
    --   "version": "1.0"
    -- }
    config_snapshot JSONB NOT NULL
        CONSTRAINT config_snapshot_not_null CHECK (config_snapshot IS NOT NULL),

    -- ========================================================================
    -- JSONB Required Fields Validation
    -- ========================================================================
    CONSTRAINT config_has_required_fields CHECK (
        config_snapshot ? 'strategy_path' AND
        config_snapshot ? 'config_path' AND
        config_snapshot ? 'config'
    ),

    -- ========================================================================
    -- Execution Status
    -- ========================================================================
    execution_status VARCHAR(20) NOT NULL DEFAULT 'success'
        CONSTRAINT valid_execution_status CHECK (
            execution_status IN ('success', 'failed', 'partial')
        ),

    error_message TEXT,

    -- ========================================================================
    -- Audit Timestamps
    -- ========================================================================
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP

    -- ========================================================================
    -- Notes:
    -- - run_id: Generated in application (uuid.uuid4()) before insert
    -- - config_snapshot: JSONB for queryability (e.g., find by fast_period=10)
    -- - execution_status: Future-proofing for failed/partial executions
    -- - created_at/updated_at: Audit trail (updated_at for future updates)
    -- ========================================================================
);

-- ============================================================================
-- Table Comments
-- ============================================================================
COMMENT ON TABLE backtest_runs IS 'Backtest execution metadata - one record per execution';
COMMENT ON COLUMN backtest_runs.id IS 'Internal surrogate key for database relations';
COMMENT ON COLUMN backtest_runs.run_id IS 'Business identifier (UUID) for external references';
COMMENT ON COLUMN backtest_runs.strategy_name IS 'Human-readable strategy name (e.g., "SMA Crossover")';
COMMENT ON COLUMN backtest_runs.strategy_type IS 'Strategy classification (e.g., "trend_following")';
COMMENT ON COLUMN backtest_runs.instrument_symbol IS 'Trading symbol (e.g., "AAPL", "EUR/USD")';
COMMENT ON COLUMN backtest_runs.start_date IS 'Backtest period start (inclusive)';
COMMENT ON COLUMN backtest_runs.end_date IS 'Backtest period end (inclusive)';
COMMENT ON COLUMN backtest_runs.config_snapshot IS 'Complete strategy configuration as JSONB for reproducibility';
COMMENT ON COLUMN backtest_runs.execution_status IS 'Execution outcome: success, failed, partial';
COMMENT ON COLUMN backtest_runs.error_message IS 'Error details if execution_status != success';
COMMENT ON COLUMN backtest_runs.created_at IS 'Backtest execution timestamp';
COMMENT ON COLUMN backtest_runs.updated_at IS 'Last modification timestamp';

-- ============================================================================
-- Table: performance_metrics
-- ============================================================================
-- Purpose: Store aggregate performance metrics for each backtest
-- Records: One per backtest (1:1 with backtest_runs)
-- Relationships: Child of backtest_runs (foreign key with CASCADE delete)
-- ============================================================================

CREATE TABLE performance_metrics (
    -- ========================================================================
    -- Primary Key
    -- ========================================================================
    id BIGSERIAL PRIMARY KEY,

    -- ========================================================================
    -- Foreign Key to backtest_runs
    -- ========================================================================
    backtest_run_id BIGINT NOT NULL
        REFERENCES backtest_runs(id) ON DELETE CASCADE,

    -- ========================================================================
    -- Enforce 1:1 Relationship
    -- ========================================================================
    CONSTRAINT unique_metrics_per_run UNIQUE (backtest_run_id),

    -- ========================================================================
    -- Return Metrics
    -- ========================================================================
    total_return NUMERIC(18, 8),

    final_balance NUMERIC(18, 2),

    -- ========================================================================
    -- Risk Metrics
    -- ========================================================================
    sharpe_ratio NUMERIC(10, 4)
        CONSTRAINT valid_sharpe_range CHECK (sharpe_ratio IS NULL OR sharpe_ratio BETWEEN -10 AND 10),

    sortino_ratio NUMERIC(10, 4)
        CONSTRAINT valid_sortino_range CHECK (sortino_ratio IS NULL OR sortino_ratio BETWEEN -10 AND 10),

    max_drawdown NUMERIC(10, 4)
        CONSTRAINT valid_drawdown_range CHECK (max_drawdown IS NULL OR max_drawdown BETWEEN -1 AND 0),

    max_drawdown_date TIMESTAMPTZ,

    calmar_ratio NUMERIC(10, 4)
        CONSTRAINT valid_calmar_range CHECK (calmar_ratio IS NULL OR calmar_ratio BETWEEN -100 AND 100),

    volatility NUMERIC(10, 6)
        CONSTRAINT valid_volatility_range CHECK (volatility IS NULL OR volatility >= 0),

    -- ========================================================================
    -- Trading Statistics
    -- ========================================================================
    total_trades INTEGER NOT NULL DEFAULT 0
        CONSTRAINT valid_total_trades CHECK (total_trades >= 0),

    winning_trades INTEGER NOT NULL DEFAULT 0
        CONSTRAINT valid_winning_trades CHECK (winning_trades >= 0),

    losing_trades INTEGER NOT NULL DEFAULT 0
        CONSTRAINT valid_losing_trades CHECK (losing_trades >= 0),

    -- ========================================================================
    -- Trading Statistics Validation
    -- ========================================================================
    CONSTRAINT winning_losing_sum_equals_total CHECK (
        total_trades = winning_trades + losing_trades
    ),

    win_rate NUMERIC(5, 4)
        CONSTRAINT valid_win_rate_range CHECK (win_rate IS NULL OR win_rate BETWEEN 0 AND 1),

    profit_factor NUMERIC(10, 4)
        CONSTRAINT valid_profit_factor CHECK (profit_factor IS NULL OR profit_factor >= 0),

    expectancy NUMERIC(18, 8),

    avg_win NUMERIC(18, 8)
        CONSTRAINT valid_avg_win CHECK (avg_win IS NULL OR avg_win >= 0),

    avg_loss NUMERIC(18, 8)
        CONSTRAINT valid_avg_loss CHECK (avg_loss IS NULL OR avg_loss <= 0),

    -- ========================================================================
    -- Audit Timestamps
    -- ========================================================================
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP

    -- ========================================================================
    -- Notes:
    -- - All numeric fields allow NULL for graceful handling of calculation failures
    -- - Sharpe/Sortino: Typically -5 to 5, allowing -10 to 10 for outliers
    -- - Max Drawdown: Always negative or zero (0 = no drawdown, -1 = 100% loss)
    -- - Volatility: Always positive (standard deviation of returns)
    -- - Win Rate: Decimal 0-1 (0 = 0%, 1 = 100%)
    -- - Profit Factor: Ratio of gross profit to gross loss (>1 = profitable)
    -- ========================================================================
);

-- ============================================================================
-- Table Comments
-- ============================================================================
COMMENT ON TABLE performance_metrics IS 'Aggregate performance metrics - one record per backtest execution';
COMMENT ON COLUMN performance_metrics.id IS 'Internal surrogate key';
COMMENT ON COLUMN performance_metrics.backtest_run_id IS 'Foreign key to backtest_runs (CASCADE delete)';
COMMENT ON COLUMN performance_metrics.total_return IS 'Total profit/loss as percentage (0.15 = 15%)';
COMMENT ON COLUMN performance_metrics.final_balance IS 'Ending account balance';
COMMENT ON COLUMN performance_metrics.sharpe_ratio IS 'Risk-adjusted return metric (annualized)';
COMMENT ON COLUMN performance_metrics.sortino_ratio IS 'Downside risk-adjusted return metric';
COMMENT ON COLUMN performance_metrics.max_drawdown IS 'Maximum peak-to-trough decline (negative value)';
COMMENT ON COLUMN performance_metrics.max_drawdown_date IS 'Date when max drawdown occurred';
COMMENT ON COLUMN performance_metrics.calmar_ratio IS 'Return divided by max drawdown';
COMMENT ON COLUMN performance_metrics.volatility IS 'Standard deviation of returns (annualized)';
COMMENT ON COLUMN performance_metrics.total_trades IS 'Total number of completed trades';
COMMENT ON COLUMN performance_metrics.winning_trades IS 'Number of profitable trades';
COMMENT ON COLUMN performance_metrics.losing_trades IS 'Number of unprofitable trades';
COMMENT ON COLUMN performance_metrics.win_rate IS 'Percentage of winning trades (0-1)';
COMMENT ON COLUMN performance_metrics.profit_factor IS 'Gross profit / gross loss ratio';
COMMENT ON COLUMN performance_metrics.expectancy IS 'Expected profit per trade';
COMMENT ON COLUMN performance_metrics.avg_win IS 'Average profit per winning trade';
COMMENT ON COLUMN performance_metrics.avg_loss IS 'Average loss per losing trade (negative)';
COMMENT ON COLUMN performance_metrics.created_at IS 'Metrics calculation timestamp';
COMMENT ON COLUMN performance_metrics.updated_at IS 'Last modification timestamp';

-- ============================================================================
-- Schema Version Tracking
-- ============================================================================
-- Optional: Track schema version for future migrations
-- Uncomment if schema versioning is required
--
-- CREATE TABLE schema_version (
--     version INTEGER PRIMARY KEY,
--     description TEXT NOT NULL,
--     applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
-- );
--
-- INSERT INTO schema_version (version, description)
-- VALUES (1, 'Initial schema: backtest_runs and performance_metrics tables');
-- ============================================================================

-- ============================================================================
-- End of Schema Definition
-- ============================================================================
