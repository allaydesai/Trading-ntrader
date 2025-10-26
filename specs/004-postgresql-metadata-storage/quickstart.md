# Quickstart Guide: PostgreSQL Metadata Storage

**Feature**: Automatic Backtest Metadata and Results Persistence
**Version**: 1.0
**Target Audience**: Developers setting up the feature for the first time

## Overview

This guide walks you through setting up automatic PostgreSQL storage for all your backtesting results. Once configured, every backtest execution will automatically save complete metadata (strategy configuration, date ranges, instruments) and performance metrics (returns, Sharpe ratio, drawdown, trading statistics) to the database. You'll be able to list, filter, compare, and reproduce past backtests without re-running them.

## Prerequisites

Before starting, ensure you have:

- **PostgreSQL 16+** installed and running
- **Python 3.11+** with UV package manager
- **Project dependencies** installed via `uv sync`
- **Database access** (credentials for creating databases)
- **Basic familiarity** with command-line tools and SQL

### Required Python Packages (Already Installed)

The following dependencies are already included in `pyproject.toml`:
- `sqlalchemy>=2.0.43` - Async ORM
- `alembic>=1.16.5` - Database migrations
- `asyncpg>=0.30.0` - PostgreSQL async driver
- `pydantic>=2.11.9` - Data validation

## Step 1: Database Setup

### 1.1 Create Database

Connect to PostgreSQL and create a dedicated database for backtest storage:

```bash
# Connect to PostgreSQL (adjust credentials as needed)
psql -U postgres

# Create database
CREATE DATABASE trading_ntrader;

# Create user (optional - if not using existing user)
CREATE USER ntrader_user WITH PASSWORD 'secure_password_here';

# Grant privileges
GRANT ALL PRIVILEGES ON DATABASE trading_ntrader TO ntrader_user;

# Exit psql
\q
```

### 1.2 Verify Database Connection

Test that you can connect to the new database:

```bash
psql -U ntrader_user -d trading_ntrader -h localhost

# Should see prompt: trading_ntrader=>
# Exit with \q
```

## Step 2: Environment Configuration

### 2.1 Configure Database URL

Add your database connection string to `.env` file (create if it doesn't exist):

```bash
# Create .env file in project root
cd /Users/allay/dev/Trading-ntrader

# Add database configuration
cat >> .env << 'EOF'
# PostgreSQL Database Configuration
DATABASE_URL=postgresql+asyncpg://ntrader_user:secure_password_here@localhost:5432/trading_ntrader

# Connection Pool Settings (optional - these are defaults)
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
DB_POOL_RECYCLE=3600
DB_POOL_PRE_PING=true
EOF
```

**Database URL Format**:
```
postgresql+asyncpg://<username>:<password>@<host>:<port>/<database_name>
```

**Component Breakdown**:
- `postgresql+asyncpg://` - Dialect + async driver
- `ntrader_user` - Database username
- `secure_password_here` - Database password
- `localhost` - Database host
- `5432` - PostgreSQL port (default)
- `trading_ntrader` - Database name

### 2.2 Verify Environment Variables

Check that the configuration is loaded correctly:

```bash
# Verify .env file exists
cat .env | grep DATABASE_URL

# Test loading in Python
uv run python -c "
from src.config.settings import get_settings
settings = get_settings()
print(f'Database URL: {settings.database_url}')
print('Configuration loaded successfully!')
"
```

Expected output:
```
Database URL: postgresql+asyncpg://ntrader_user:***@localhost:5432/trading_ntrader
Configuration loaded successfully!
```

## Step 3: Run Database Migrations

### 3.1 Check Migration Status

View current database schema version:

```bash
uv run alembic current
```

Expected output (before migrations):
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
```

### 3.2 Apply Migrations

Run all pending migrations to create the backtest storage tables:

```bash
uv run alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade -> 001_create_backtest_tables
INFO  [alembic.runtime.migration] Running upgrade 001_create_backtest_tables -> head
```

### 3.3 Verify Schema Creation

Check that tables were created successfully:

```bash
psql -U ntrader_user -d trading_ntrader -c "\dt"
```

Expected output:
```
              List of relations
 Schema |         Name          | Type  |     Owner
--------+-----------------------+-------+---------------
 public | alembic_version       | table | ntrader_user
 public | backtest_runs         | table | ntrader_user
 public | performance_metrics   | table | ntrader_user
(3 rows)
```

View table structure:

```bash
# View backtest_runs schema
psql -U ntrader_user -d trading_ntrader -c "\d backtest_runs"

# View performance_metrics schema
psql -U ntrader_user -d trading_ntrader -c "\d performance_metrics"
```

## Step 4: Verify Installation

### 4.1 Test Database Connection

Run the verification script to ensure everything is working:

```bash
# Test database connectivity
uv run python -c "
import asyncio
from src.db.session import get_session

async def test_connection():
    async with get_session() as session:
        result = await session.execute('SELECT version()')
        version = result.scalar()
        print(f'PostgreSQL Version: {version}')
        print('Database connection successful!')

asyncio.run(test_connection())
"
```

Expected output:
```
PostgreSQL Version: PostgreSQL 16.x on ...
Database connection successful!
```

### 4.2 Query Empty Tables

Verify tables are accessible and empty:

```bash
psql -U ntrader_user -d trading_ntrader << 'EOF'
SELECT COUNT(*) as backtest_count FROM backtest_runs;
SELECT COUNT(*) as metrics_count FROM performance_metrics;
EOF
```

Expected output:
```
 backtest_count
----------------
              0
(1 row)

 metrics_count
---------------
             0
(1 row)
```

## Step 5: Usage Examples

### 5.1 Running Your First Backtest (Auto-Saves to Database)

All backtests now automatically save to the database. Just run your backtest as usual:

```bash
# Example: Run SMA Crossover backtest
uv run python -m src.cli.main run-backtest \
  --strategy sma_crossover \
  --config config/strategies/sma_crossover.yaml \
  --symbol AAPL \
  --start-date 2023-01-01 \
  --end-date 2023-12-31 \
  --initial-capital 100000
```

Expected output includes:
```
Starting backtest for SMA Crossover on AAPL...
Backtest completed in 45.2s

Performance Summary:
Total Return: 25.47%
Sharpe Ratio: 1.85
Max Drawdown: -12.0%
Total Trades: 45
Win Rate: 62.22%

Results saved to database!
Run ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Note**: The `Run ID` is your unique identifier for retrieving this backtest later.

### 5.2 Listing Backtest History

View your recent backtest executions:

```bash
# List 20 most recent backtests
uv run python -m src.cli.main history list

# List with custom limit
uv run python -m src.cli.main history list --limit 50
```

Expected output:
```
Backtest History (20 most recent)

Run ID          Date/Time           Strategy          Symbol  Return    Sharpe  Max DD    Status
──────────────  ──────────────────  ────────────────  ──────  ────────  ──────  ────────  ────────
a1b2c3d4...     2025-01-25 10:30    SMA Crossover     AAPL    +25.47%   1.85    -12.0%    Success
b2c3d4e5...     2025-01-25 09:15    Mean Reversion    TSLA    -8.20%    0.45    -18.5%    Success
c3d4e5f6...     2025-01-24 16:45    Momentum          SPY     +15.30%   2.12    -8.2%     Success
...

Total: 20 backtests displayed
```

### 5.3 Filtering and Sorting

Filter backtests by various criteria:

```bash
# Filter by strategy name
uv run python -m src.cli.main history list --strategy "SMA Crossover"

# Filter by instrument
uv run python -m src.cli.main history list --symbol AAPL

# Filter by date range
uv run python -m src.cli.main history list \
  --after 2025-01-01 \
  --before 2025-01-31

# Sort by Sharpe ratio (descending)
uv run python -m src.cli.main history list --sort sharpe_ratio

# Sort by total return
uv run python -m src.cli.main history list --sort total_return

# Combine filters
uv run python -m src.cli.main history list \
  --strategy "SMA Crossover" \
  --symbol AAPL \
  --sort sharpe_ratio \
  --limit 10
```

### 5.4 Viewing Complete Backtest Details

Retrieve all details for a specific backtest using its Run ID:

```bash
# Get full details
uv run python -m src.cli.main history show a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

Expected output:
```
Backtest Details
════════════════════════════════════════════════════════════════

Run ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
Created: 2025-01-25 10:30:00 UTC
Status: Success
Duration: 45.237 seconds

Strategy Configuration:
────────────────────────────────────────────────────────────────
Strategy: SMA Crossover (trend_following)
Config Path: config/strategies/sma_crossover.yaml
Parameters:
  - fast_period: 10
  - slow_period: 50
  - risk_percent: 2.0

Execution Context:
────────────────────────────────────────────────────────────────
Symbol: AAPL
Period: 2023-01-01 to 2023-12-31
Initial Capital: $100,000.00
Data Source: IBKR

Performance Metrics:
────────────────────────────────────────────────────────────────
Returns:
  Total Return: 25.47%
  Final Balance: $125,470.00
  CAGR: 25.47%

Risk Metrics:
  Sharpe Ratio: 1.85
  Sortino Ratio: 2.34
  Max Drawdown: -12.0% (2023-06-15)
  Calmar Ratio: 2.12
  Volatility: 18.0%

Trading Statistics:
  Total Trades: 45
  Winning Trades: 28 (62.22%)
  Losing Trades: 17 (37.78%)
  Profit Factor: 2.15
  Expectancy: $145.60
  Average Win: $520.35
  Average Loss: -$285.20
```

### 5.5 Comparing Multiple Backtests

Compare performance across different parameter combinations:

```bash
# Compare 3 backtests side-by-side
uv run python -m src.cli.main compare \
  a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  b2c3d4e5-f6a7-8901-bcde-f12345678901 \
  c3d4e5f6-a7b8-9012-cdef-123456789012
```

Expected output:
```
Backtest Comparison

                              Run 1           Run 2           Run 3
────────────────────────────  ──────────────  ──────────────  ──────────────
Run ID                        a1b2c3d4...     b2c3d4e5...     c3d4e5f6...
Strategy                      SMA Crossover   SMA Crossover   SMA Crossover
Symbol                        AAPL            AAPL            AAPL
Period                        2023            2023            2023

Configuration Differences:
  fast_period                 10              10              20
  slow_period                 50              30              50
  risk_percent                2.0             2.0             2.0

Performance:
  Total Return                25.47%          18.20%          22.10%
  Sharpe Ratio                1.85            1.45            1.92
  Max Drawdown                -12.0%          -15.3%          -10.5%
  Win Rate                    62.22%          55.00%          58.33%
  Total Trades                45              60              38

Best Performer:
  Highest Return: Run 1 (25.47%)
  Best Sharpe: Run 3 (1.92)
  Lowest Drawdown: Run 3 (-10.5%)
```

### 5.6 Reproducing a Previous Backtest

Re-run a backtest with its exact same configuration:

```bash
# Reproduce from stored configuration
uv run python -m src.cli.main reproduce a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

Expected output:
```
Loading configuration from backtest a1b2c3d4-e5f6-7890-abcd-ef1234567890...

Configuration loaded:
  Strategy: SMA Crossover
  Symbol: AAPL
  Period: 2023-01-01 to 2023-12-31
  Parameters: fast_period=10, slow_period=50, risk_percent=2.0

Starting reproduction...
Backtest completed in 43.9s

New Run ID: d4e5f6a7-b8c9-0123-def4-234567890123
Reproduced from: a1b2c3d4-e5f6-7890-abcd-ef1234567890

Performance Summary:
Total Return: 25.51%  (original: 25.47%)
Sharpe Ratio: 1.87    (original: 1.85)
Max Drawdown: -11.8%  (original: -12.0%)

Note: Small differences may occur due to data updates or calculation precision.
```

## Step 6: Configuration Options

### 6.1 Database URL Formats

The feature supports various PostgreSQL connection configurations:

```bash
# Local connection (default)
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbname

# Remote connection
DATABASE_URL=postgresql+asyncpg://user:pass@192.168.1.100:5432/dbname

# With SSL (production)
DATABASE_URL=postgresql+asyncpg://user:pass@prod-server:5432/dbname?ssl=require

# Unix socket connection
DATABASE_URL=postgresql+asyncpg://user:pass@/dbname?host=/var/run/postgresql
```

### 6.2 Connection Pool Settings

Adjust connection pool settings in `.env` for different workloads:

```bash
# Default settings (good for most cases)
DB_POOL_SIZE=20              # Permanent connections in pool
DB_MAX_OVERFLOW=10           # Additional temporary connections
DB_POOL_RECYCLE=3600         # Recycle connections after 1 hour
DB_POOL_PRE_PING=true        # Verify connection health before use

# High-concurrency settings (many parallel backtests)
DB_POOL_SIZE=50
DB_MAX_OVERFLOW=20

# Low-resource settings (limited memory)
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
```

**Pool Size Guidelines**:
- Small projects: 5-10 connections
- Medium projects: 20-30 connections
- High concurrency: 50+ connections
- Total max = POOL_SIZE + MAX_OVERFLOW

### 6.3 Query Performance Settings

Environment variables for query optimization:

```bash
# Enable query logging (development only)
DB_ECHO=true                 # Log all SQL queries

# Statement timeout (prevent hung queries)
DB_STATEMENT_TIMEOUT=30000   # 30 seconds in milliseconds

# Query result caching
DB_QUERY_CACHE_SIZE=500      # Cache 500 most recent queries
```

## Step 7: Troubleshooting

### Issue 1: Connection Refused

**Symptoms**:
```
sqlalchemy.exc.OperationalError: (psycopg2.OperationalError) could not connect to server: Connection refused
```

**Solutions**:
1. Verify PostgreSQL is running:
   ```bash
   # macOS
   brew services list | grep postgresql

   # Linux
   systemctl status postgresql
   ```

2. Check port is accessible:
   ```bash
   nc -zv localhost 5432
   ```

3. Verify credentials:
   ```bash
   psql -U ntrader_user -d trading_ntrader -h localhost
   ```

### Issue 2: Authentication Failed

**Symptoms**:
```
FATAL: password authentication failed for user "ntrader_user"
```

**Solutions**:
1. Verify password in `.env` matches database:
   ```bash
   cat .env | grep DATABASE_URL
   ```

2. Reset database password:
   ```sql
   ALTER USER ntrader_user WITH PASSWORD 'new_password';
   ```

3. Check `pg_hba.conf` allows password authentication:
   ```bash
   # Edit pg_hba.conf (location varies by OS)
   # Change 'peer' or 'ident' to 'md5' for TCP connections
   local   all   all   md5
   host    all   all   127.0.0.1/32   md5
   ```

### Issue 3: Migration Fails

**Symptoms**:
```
alembic.util.exc.CommandError: Can't locate revision identified by 'head'
```

**Solutions**:
1. Check migration files exist:
   ```bash
   ls -la alembic/versions/
   ```

2. Reset alembic_version table:
   ```bash
   psql -U ntrader_user -d trading_ntrader << 'EOF'
   DROP TABLE IF EXISTS alembic_version CASCADE;
   EOF

   # Re-run migrations
   uv run alembic upgrade head
   ```

3. Verify alembic.ini configuration:
   ```bash
   cat alembic.ini | grep sqlalchemy.url
   ```

### Issue 4: Table Already Exists

**Symptoms**:
```
sqlalchemy.exc.ProgrammingError: (psycopg2.errors.DuplicateTable) relation "backtest_runs" already exists
```

**Solutions**:
1. Rollback and re-apply:
   ```bash
   uv run alembic downgrade base
   uv run alembic upgrade head
   ```

2. Drop tables manually (CAUTION: loses data):
   ```bash
   psql -U ntrader_user -d trading_ntrader << 'EOF'
   DROP TABLE IF EXISTS performance_metrics CASCADE;
   DROP TABLE IF EXISTS backtest_runs CASCADE;
   DROP TABLE IF EXISTS alembic_version CASCADE;
   EOF

   # Re-run migrations
   uv run alembic upgrade head
   ```

### Issue 5: Slow Query Performance

**Symptoms**:
- List queries take >2 seconds
- Comparison takes >5 seconds

**Solutions**:
1. Check indexes were created:
   ```sql
   SELECT schemaname, tablename, indexname
   FROM pg_indexes
   WHERE tablename IN ('backtest_runs', 'performance_metrics');
   ```

2. Analyze tables:
   ```sql
   ANALYZE backtest_runs;
   ANALYZE performance_metrics;
   ```

3. Check table statistics:
   ```sql
   SELECT relname, n_live_tup, n_dead_tup
   FROM pg_stat_user_tables
   WHERE relname IN ('backtest_runs', 'performance_metrics');
   ```

4. Rebuild indexes if needed:
   ```sql
   REINDEX TABLE CONCURRENTLY backtest_runs;
   REINDEX TABLE CONCURRENTLY performance_metrics;
   ```

### Issue 6: Out of Memory

**Symptoms**:
```
psycopg2.errors.OutOfMemory: out of memory
```

**Solutions**:
1. Reduce pool size:
   ```bash
   # In .env
   DB_POOL_SIZE=5
   DB_MAX_OVERFLOW=5
   ```

2. Limit query results:
   ```bash
   # Use smaller page sizes
   uv run python -m src.cli.main history list --limit 10
   ```

3. Check PostgreSQL memory settings:
   ```sql
   SHOW shared_buffers;
   SHOW work_mem;
   ```

### Issue 7: Backtest Not Saving

**Symptoms**:
- Backtest completes but no record in database
- No "Results saved to database!" message

**Solutions**:
1. Check for errors in backtest output:
   ```bash
   uv run python -m src.cli.main run-backtest ... 2>&1 | tee backtest.log
   cat backtest.log | grep -i error
   ```

2. Verify persistence is enabled:
   ```bash
   # Check settings
   uv run python -c "
   from src.config.settings import get_settings
   settings = get_settings()
   print(f'Enable persistence: {settings.enable_backtest_persistence}')
   "
   ```

3. Test database connection:
   ```bash
   uv run python -c "
   import asyncio
   from src.db.session import get_session

   async def test():
       async with get_session() as session:
           print('Connection successful')

   asyncio.run(test())
   "
   ```

## Step 8: Next Steps

### Explore Advanced Features

1. **Parameter Sweep Analysis**
   - Run multiple backtests with different parameters
   - Compare to find optimal configurations
   - Document in `/specs/004-postgresql-metadata-storage/research.md`

2. **Performance Monitoring**
   - Track query performance over time
   - Identify slow queries with `DB_ECHO=true`
   - Optimize indexes based on usage patterns

3. **Data Export**
   - Export results to CSV for analysis:
     ```bash
     psql -U ntrader_user -d trading_ntrader << 'EOF' > results.csv
     COPY (
       SELECT br.run_id, br.strategy_name, br.instrument_symbol,
              pm.total_return, pm.sharpe_ratio, pm.max_drawdown
       FROM backtest_runs br
       JOIN performance_metrics pm ON br.id = pm.backtest_run_id
       WHERE br.execution_status = 'success'
       ORDER BY pm.sharpe_ratio DESC
     ) TO STDOUT WITH CSV HEADER;
     EOF
     ```

4. **Backup and Restore**
   - Regular database backups:
     ```bash
     pg_dump -U ntrader_user -d trading_ntrader > backup_$(date +%Y%m%d).sql
     ```
   - Restore from backup:
     ```bash
     psql -U ntrader_user -d trading_ntrader < backup_20250125.sql
     ```

### Related Documentation

- **Feature Specification**: `/specs/004-postgresql-metadata-storage/spec.md`
- **Data Model**: `/specs/004-postgresql-metadata-storage/data-model.md`
- **Implementation Plan**: `/specs/004-postgresql-metadata-storage/plan.md`
- **Research Findings**: `/specs/004-postgresql-metadata-storage/research.md`

### Getting Help

- **GitHub Issues**: Report bugs or request features
- **Project README**: `/Users/allay/dev/Trading-ntrader/README.md`
- **Database Schema**: `/specs/004-postgresql-metadata-storage/contracts/schema.sql`

## Summary

You've successfully set up PostgreSQL metadata storage for your backtesting system. Key capabilities now enabled:

- Automatic persistence of all backtest executions
- Fast retrieval of historical results (<100ms)
- Side-by-side comparison of multiple backtests
- Exact reproduction of past configurations
- Filtering and sorting by performance metrics
- Complete audit trail of all testing activities

All backtests are now automatically saved to the database with zero additional effort. Simply run your backtests as usual and access the results anytime using the CLI commands.

Happy backtesting!
