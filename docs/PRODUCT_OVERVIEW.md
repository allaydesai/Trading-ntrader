# Trading-NTrader: Product Overview & Features

**Version**: 1.0
**Last Updated**: January 2025
**Status**: Active Development

---

## Table of Contents

1. [Product Vision](#product-vision)
2. [Core Features](#core-features)
3. [Technical Architecture](#technical-architecture)
4. [Features Implemented](#features-implemented)
5. [Development Roadmap](#development-roadmap)
6. [User Workflows](#user-workflows)
7. [Performance & Metrics](#performance--metrics)

---

## Product Vision

### Overview

Trading-NTrader is a production-grade algorithmic trading backtesting system built on the Nautilus Trader framework with Interactive Brokers data integration. The system enables quantitative traders and developers to validate trading strategies on historical market data before risking actual capital.

### Problem Statement

Quantitative traders need a reliable, extensible backtesting framework to validate trading strategies on US equities, ETFs, and spot FX using institutional-grade historical data. Current solutions are either too complex, expensive, or lack proper integration with Interactive Brokers' ecosystem.

### Solution

A lightweight, Python-based backtesting application that:
- Ingests historical data from Interactive Brokers (IBKR)
- Supports custom strategy development with minimal code
- Provides actionable performance metrics and visualizations
- Maintains simplicity while supporting incremental extensibility
- Persists backtest results for comparison and reproducibility

### Target Users

- **Primary**: Quantitative developers/traders with Python experience
- **Secondary**: Portfolio managers evaluating systematic strategies
- **Skill Level**: Intermediate to advanced Python developers familiar with trading concepts

---

## Core Features

### 1. Data Management

#### Interactive Brokers Integration
- Direct connection to IBKR TWS/Gateway for historical data retrieval
- Automatic rate limiting (50 requests/second) to respect IBKR constraints
- Support for US equities, ETFs, and major FX pairs (EURUSD, USDJPY)
- Adjusted prices for corporate actions (splits, dividends)
- Multiple timeframe support: 1-minute, 5-minute, 1-hour, daily bars

#### Parquet-Based Data Storage
- **Single source of truth**: All market data stored in Parquet format
- Organized hierarchy: instrument → bar type → daily partitions
- Fast availability checks without loading full datasets
- Automatic data fetching when cache misses occur
- CSV import fallback for external data sources

#### Data Catalog Features
- Catalog metadata for quick availability lookups
- Date range validation before backtest execution
- Gap identification in historical data coverage
- Automatic persistence after IBKR fetch operations
- Concurrent read access during backtests

### 2. Backtesting Engine

#### Strategy Framework
- Built on Nautilus Trader event-driven architecture
- Support for custom strategies via Python classes
- Multiple timeframe access within single strategy
- Three sample strategies included:
  - **SMA Crossover**: Moving average crossover signals
  - **Mean Reversion**: Price reversion to statistical mean
  - **Momentum**: Trend-following based on price momentum

#### Execution Simulation
- Market and limit order support
- Realistic commission models (IBKR structure)
- Slippage simulation (1 basis point default)
- Position sizing with configurable risk per trade (default 1%)
- Short selling support (simplified, no borrowing costs)
- FX conversion for non-USD instruments

#### Portfolio Management
- Initial capital: $100,000 USD (configurable)
- Default date range: 2019-01-01 to 2024-12-31
- Single instrument backtesting (portfolio support planned)
- Real-time portfolio value tracking
- Position management and P&L calculation

### 3. Performance Analytics

#### Key Performance Indicators
- **Return Metrics**: Total return, CAGR, annualized return
- **Risk Metrics**: Sharpe ratio, Sortino ratio, maximum drawdown, volatility
- **Trading Metrics**: Total trades, win rate, winning/losing trade counts
- **Profit Metrics**: Profit factor, average win/loss amounts
- **Benchmark Comparison**: Strategy performance vs SPY benchmark

#### Reporting Formats
- **HTML Reports**: Interactive dashboards with charts and tables
- **CSV Exports**: Trade blotter with timestamp, symbol, side, quantity, price, P&L
- **JSON Output**: Structured metrics for programmatic access
- **Visualizations**: Equity curve, drawdown charts (PNG format)

### 4. Metadata Storage & Persistence

#### Backtest Execution Tracking
- Automatic persistence of every backtest run
- Unique identifier assignment for each execution
- Complete strategy configuration snapshots (immutable)
- Execution metadata: timestamp, duration, status, data source
- Error capture for failed backtests (stack trace, error message)

#### Query & Retrieval System
- List recent backtests (configurable limit: default 20, max 1000)
- Filter by strategy name, type, instrument, date range, status
- Sort by execution date, return, Sharpe ratio, drawdown
- Sub-100ms retrieval by unique identifier
- Configuration parameter search capabilities

#### Comparison & Reproducibility
- Side-by-side comparison of 2-10 backtests
- Configuration difference highlighting
- Best performer identification for specific metrics
- Re-run previous backtests with stored configuration
- Reproduction tracking (links to original run)

### 5. Testing Architecture

#### Three-Tier Testing Strategy
1. **Unit Tests (50% of suite)**
   - Pure Python business logic with no Nautilus dependencies
   - Sub-100ms execution time per test
   - Algorithm validation (position sizing, risk management, trade decisions)

2. **Component Tests (25% of suite)**
   - Test doubles for trading engine interactions
   - Lightweight mocks (TestTradingEngine, TestOrder, TestPosition)
   - Sub-500ms execution time per test
   - Strategy behavior verification

3. **Integration Tests (20% of suite)**
   - Real Nautilus components in isolated environments
   - Process isolation with pytest-forked
   - Parallel execution with pytest-xdist
   - End-to-end validation

#### Test Organization
- Separate directories: `tests/unit/`, `tests/component/`, `tests/integration/`, `tests/e2e/`
- Pytest markers for categorization
- Makefile targets: `test-unit`, `test-component`, `test-integration`, `test-all`
- CI/CD pipeline optimization: fast tests on commit, comprehensive tests on merge

---

## Technical Architecture

### Technology Stack

- **Language**: Python 3.11+
- **Core Framework**: Nautilus Trader (event-driven backtesting)
- **Data Source**: Interactive Brokers TWS/Gateway via nautilus_trader[ib]
- **Data Storage**: Parquet (Apache Arrow columnar format)
- **Database**: PostgreSQL 16+ with TimescaleDB for metadata
- **ORM**: SQLAlchemy 2.0 with async support
- **Migration**: Alembic for database schema versioning
- **Validation**: Pydantic 2.5+ for data models
- **Testing**: pytest 7.4+ with pytest-asyncio, pytest-xdist, pytest-forked
- **Package Manager**: UV (exclusive)
- **Logging**: structlog with correlation IDs
- **API Framework**: FastAPI 0.109+ with async/await patterns

### Project Structure

```
src/
├── api/          # FastAPI routers and endpoints
├── core/         # Core business logic
├── models/       # Pydantic models and schemas
├── services/     # Business services
├── db/           # Database models and migrations
└── utils/        # Shared utilities

tests/            # Test files mirror src structure
├── unit/         # Pure Python unit tests
├── component/    # Test double component tests
├── integration/  # Real Nautilus integration tests
└── e2e/          # End-to-end system tests

scripts/          # CLI tools and automation
docs/             # Documentation and ADRs
docker/           # Docker configurations
```

### Data Flow Architecture

```
User Request → CLI Command → Backtest Engine
                                   ↓
                         Check Parquet Catalog
                                   ↓
                    ┌──────────────┴──────────────┐
                    ↓                             ↓
            Data Available                Data Missing
                    ↓                             ↓
            Load from Parquet         Fetch from IBKR
                    ↓                             ↓
            Run Backtest              Persist to Parquet
                    ↓                             ↓
         Calculate Metrics            Load from Parquet
                    ↓                             ↓
         Save to PostgreSQL           Run Backtest
                    ↓                             ↓
         Generate Reports         Calculate Metrics
                    ↓                             ↓
         Return Results          Save to PostgreSQL
                                                  ↓
                                         Generate Reports
                                                  ↓
                                         Return Results
```

### Database Schema (Metadata)

#### Backtest Run Table
- `id` (UUID, primary key)
- `created_at` (timestamp)
- `execution_duration` (interval)
- `status` (enum: success, failure)
- `error_message` (text, nullable)
- `strategy_name` (string)
- `strategy_type` (string)
- `strategy_config` (JSONB, immutable snapshot)
- `instrument_symbol` (string)
- `date_range_start` (date)
- `date_range_end` (date)
- `initial_capital` (decimal)
- `data_source` (string)
- `original_run_id` (UUID, nullable, foreign key for reproductions)

#### Performance Metrics Table
- `id` (UUID, primary key)
- `backtest_run_id` (UUID, foreign key with cascade delete)
- `total_return` (decimal)
- `cagr` (decimal)
- `sharpe_ratio` (decimal)
- `sortino_ratio` (decimal)
- `max_drawdown` (decimal)
- `volatility` (decimal)
- `total_trades` (integer)
- `winning_trades` (integer)
- `losing_trades` (integer)
- `win_rate` (decimal)
- `profit_factor` (decimal)
- `average_win` (decimal)
- `average_loss` (decimal)

### Parquet Catalog Structure

```
./data/catalog/
├── AAPL.NASDAQ/
│   ├── 1_MIN/
│   │   ├── 2024-01-01.parquet
│   │   ├── 2024-01-02.parquet
│   │   └── ...
│   ├── 5_MIN/
│   └── 1_HOUR/
├── MSFT.NASDAQ/
└── EURUSD.IDEALPRO/
```

---

## Features Implemented

### Milestone 1: Initial Backtesting System (Spec 001)

**Status**: ✅ Completed
**Branch**: `001-docs-prd-md`

#### Features Delivered
- IBKR TWS/Gateway connection and authentication
- Historical data retrieval for US equities and FX pairs
- Nautilus Trader integration for event-driven backtesting
- SMA crossover strategy implementation
- Market and limit order execution
- Commission and slippage modeling
- Performance metrics calculation (CAGR, Sharpe, drawdown, win rate)
- HTML report generation with equity curve visualization
- CSV trade export functionality
- Multi-timeframe support (1m, 5m, 1h, daily)
- CLI interface with progress indicators

#### User Value
- Traders can validate a simple SMA crossover strategy on historical data
- Immediate feedback on strategy performance with key metrics
- Visual confirmation via equity curve and trade history
- Foundation for more complex strategy development

### Milestone 2: Parquet-Only Data Storage (Spec 002)

**Status**: ✅ Completed
**Branch**: `002-migrate-from-dual`

#### Features Delivered
- Parquet catalog as single source of truth for market data
- Automatic data fetching when cache misses occur
- Direct CSV import to Parquet (bypassing PostgreSQL)
- Data availability checks without full dataset loading
- PostgreSQL market data table deprecation
- Migration script for existing PostgreSQL data
- Catalog metadata for fast lookups
- Rate limit handling during IBKR fetch operations
- Clear error messaging with recovery steps

#### User Value
- Faster backtest startup (no database queries for market data)
- Transparent data management (know when fetching vs loading cached data)
- Simplified architecture (one data storage system instead of two)
- Data persistence for future use without manual intervention
- CSV import flexibility for external data sources

### Milestone 3: Unit Testing Architecture (Spec 003)

**Status**: ✅ Completed
**Branch**: `003-rework-unit-testing`

#### Features Delivered
- Pure business logic extraction into non-Nautilus classes
- Test double implementations (TestTradingEngine, TestOrder, TestPosition)
- Process isolation for integration tests (pytest-forked)
- Parallel test execution (pytest-xdist)
- Test organization into unit/component/integration/e2e categories
- Pytest markers for test categorization
- Makefile targets for running test subsets
- Cleanup fixtures for state management
- Async event loop isolation

#### User Value
- Developers get rapid feedback (unit tests in seconds, not minutes)
- Reliable test suite (C extension crashes don't cascade)
- Easier debugging (pure logic tests are simple to troubleshoot)
- Faster development cycles (can run unit tests without full Nautilus setup)
- Higher confidence in code changes (comprehensive test pyramid)

### Milestone 4: PostgreSQL Metadata Storage (Spec 004)

**Status**: ✅ Completed
**Branch**: `004-postgresql-metadata-storage`

#### Features Delivered
- Automatic persistence of backtest execution metadata
- Complete strategy configuration snapshots (immutable)
- Performance metrics storage (returns, risk, trading, profit metrics)
- CLI commands for listing and retrieving backtests
- Filtering by strategy, instrument, date range, status
- Sorting by date, return, Sharpe ratio, drawdown
- Side-by-side comparison of multiple backtests (2-10 runs)
- Re-run capability using stored configurations
- Reproduction tracking (links to original run)
- Sub-100ms retrieval by ID, sub-2s for filtered lists

#### User Value
- Never lose backtest results (automatic saving)
- Review testing history without re-running (fast retrieval)
- Compare different parameter combinations (side-by-side view)
- Reproduce past experiments (exact configuration reload)
- Identify best performers (sorting and filtering)
- Understand what was tested (complete configuration transparency)

---

## Development Roadmap

### Phase 1 - MVP (Weeks 1-4) ✅ COMPLETED

1. ✅ Basic IBKR connection and data retrieval
2. ✅ Nautilus integration with simple buy/hold strategy
3. ✅ SMA crossover strategy + metrics calculation
4. ✅ HTML reporting + equity curve visualization

### Phase 2 - Core Features (Weeks 5-8) ✅ COMPLETED

1. ✅ Parquet-based data storage (single source of truth)
2. ✅ Automatic data fetching and persistence
3. ✅ CSV import functionality
4. ✅ Enhanced testing architecture (unit/component/integration)
5. ✅ PostgreSQL metadata storage and backtest history

### Phase 3 - Advanced Testing & Reliability (Weeks 9-12) 🔄 IN PROGRESS

1. 🔄 Additional sample strategies (mean reversion, momentum)
2. 🔄 Enhanced error handling and recovery
3. 🔄 Performance optimization
4. 📋 Walk-forward analysis
5. 📋 Parameter optimization framework

### Phase 4 - Portfolio & Advanced Analytics (Future)

1. 📋 Multi-strategy portfolio support
2. 📋 Advanced risk metrics (VaR, Monte Carlo)
3. 📋 Machine learning integration
4. 📋 Real-time data updates
5. 📋 Web-based user interface

### Phase 5 - Production Features (Future)

1. 📋 Live trading capability (paper trading first)
2. 📋 Cloud deployment options
3. 📋 Multi-user support with authentication
4. 📋 Options and futures support
5. 📋 Advanced order types (stop-loss, trailing stops)

**Legend**: ✅ Completed | 🔄 In Progress | 📋 Planned

---

## User Workflows

### Workflow 1: First-Time Backtest

```bash
# 1. Start IBKR Gateway (if using live data)
docker compose up ibgateway

# 2. Run backtest with SMA strategy
uv run python scripts/backtest.py \
  --strategy sma_crossover \
  --instrument AAPL \
  --start-date 2019-01-01 \
  --end-date 2024-12-31 \
  --fast-period 20 \
  --slow-period 50

# 3. System automatically:
#    - Checks Parquet catalog for AAPL data
#    - Fetches from IBKR if missing
#    - Persists to Parquet for future use
#    - Runs backtest simulation
#    - Saves results to PostgreSQL
#    - Generates HTML report

# 4. View results
open reports/backtest_<run_id>.html
```

### Workflow 2: Review Past Backtests

```bash
# List recent backtests
uv run python scripts/cli.py history --limit 20

# Filter by strategy
uv run python scripts/cli.py history --strategy sma_crossover

# View specific backtest details
uv run python scripts/cli.py report <run_id>

# Compare multiple runs
uv run python scripts/cli.py compare <run_id_1> <run_id_2> <run_id_3>
```

### Workflow 3: Parameter Optimization

```bash
# Run backtest with different parameters
for fast in 10 20 30; do
  for slow in 40 50 60; do
    uv run python scripts/backtest.py \
      --strategy sma_crossover \
      --instrument AAPL \
      --fast-period $fast \
      --slow-period $slow
  done
done

# Find best performers by Sharpe ratio
uv run python scripts/cli.py history \
  --strategy sma_crossover \
  --instrument AAPL \
  --sort-by sharpe_ratio \
  --limit 10
```

### Workflow 4: Reproduce Previous Run

```bash
# Re-run a specific backtest using stored config
uv run python scripts/cli.py rerun <run_id>

# System automatically:
#    - Loads complete configuration snapshot
#    - Uses same parameters, instrument, date range
#    - Creates new run record (doesn't overwrite original)
#    - Links to original run for tracking
```

### Workflow 5: Import External Data

```bash
# Import CSV data directly to Parquet catalog
uv run python scripts/import_csv.py \
  --file /path/to/AAPL_2023.csv \
  --instrument AAPL \
  --exchange NASDAQ \
  --bar-type 1_MIN

# System validates and writes to catalog
# Future backtests automatically use this data
```

---

## Performance & Metrics

### System Performance

- **Parquet Data Load**: <500ms for 1 year of 1-minute bars
- **Backtest Execution**: ~2-5 minutes for 5-year single instrument test (1-minute data)
- **Metadata Query**: <100ms for individual backtest retrieval by ID
- **History List**: <200ms for 20 most recent backtests
- **Comparison View**: <2 seconds for 10 backtests side-by-side
- **Database Capacity**: 10,000+ backtest runs without performance degradation

### Test Suite Performance

- **Unit Tests**: <5 seconds for full suite (~50% of tests)
- **Component Tests**: <10 seconds for full suite (~25% of tests)
- **Integration Tests**: <2 minutes with 4 parallel workers (~20% of tests)
- **Full Suite**: <3 minutes with parallel execution
- **Coverage**: 80%+ on critical trading logic paths

### Data Management

- **IBKR Rate Limit**: 50 requests/second (automatically enforced)
- **Parquet Compression**: ~70% size reduction vs raw CSV
- **Concurrent Access**: Unlimited read operations during backtests
- **Storage Estimate**: ~50 MB per 10,000 backtest run records (metadata only)

### Trading Metrics Calculated

1. **Return Metrics**
   - Total return (%)
   - CAGR (Compound Annual Growth Rate)
   - Annualized return

2. **Risk Metrics**
   - Sharpe ratio (returns per unit risk)
   - Sortino ratio (downside risk adjusted)
   - Maximum drawdown (peak to trough)
   - Volatility (standard deviation of returns)

3. **Trading Metrics**
   - Total trades executed
   - Winning trades count
   - Losing trades count
   - Win rate (%)

4. **Profit Metrics**
   - Profit factor (gross profit / gross loss)
   - Average winning trade amount
   - Average losing trade amount

5. **Benchmark Comparison**
   - Strategy performance vs SPY (S&P 500 ETF)
   - Relative performance metrics

---

## Development Philosophy

### KISS (Keep It Simple, Stupid)
Simplicity is a key design goal. The system favors straightforward solutions over complex ones:
- Single data storage system (Parquet, not dual storage)
- Clear separation of concerns (data, logic, testing)
- Minimal abstractions (only where needed)

### YAGNI (You Aren't Gonna Need It)
Features are implemented only when needed, not speculatively:
- Started with single instrument before portfolio support
- Added persistence after core backtesting worked
- No premature optimization

### Test-Driven Development (TDD)
All features follow red-green-refactor cycle:
- Write failing test first
- Implement minimal code to pass
- Refactor while keeping tests green
- Maintain 80%+ coverage on critical paths

### Incremental Growth
Architecture supports adding features without breaking existing functionality:
- Modular design with clear interfaces
- Backward compatibility maintained
- Migration paths for data structure changes
- Comprehensive regression testing

---

## Security & Best Practices

### Code Quality Standards

- **Type Safety**: All functions require type hints (PEP 484)
- **Documentation**: Google-style docstrings with examples
- **Linting**: Ruff for formatting and linting
- **Type Checking**: Mypy validation on all code
- **Line Length**: Max 100 characters
- **Function Size**: <50 lines, single responsibility
- **Class Size**: <100 lines, single concept
- **File Size**: <500 lines, split into modules if exceeded

### Security Practices

- Never commit secrets (use environment variables)
- Validate all user input with Pydantic
- Use parameterized queries for database operations
- Implement rate limiting for IBKR API
- Keep dependencies updated with UV
- HTTPS for all external communications

### Error Handling

- Custom exceptions for trading domain
- Structured logging with correlation IDs
- Fail-fast principle (check errors early)
- Comprehensive error messages with recovery steps
- Graceful degradation on connection failures

---

## Getting Started

### Prerequisites

- Python 3.11+
- UV package manager
- PostgreSQL 16+ (for metadata)
- Docker (optional, for IBKR Gateway)
- IBKR account with market data subscriptions (for live data)

### Installation

```bash
# Clone repository
git clone <repository-url>
cd Trading-ntrader

# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv

# Sync dependencies
uv sync

# Setup database
docker compose up -d postgres
alembic upgrade head

# Run tests
uv run pytest
```

### Configuration

```bash
# Copy environment template
cp .env.example .env

# Edit configuration
vim .env

# Required settings:
# - DATABASE_URL=postgresql://user:pass@localhost:5432/ntrader
# - IBKR_HOST=127.0.0.1
# - IBKR_PORT=4002
# - IBKR_CLIENT_ID=1
```

### Running Your First Backtest

```bash
# Start IBKR Gateway (if using live data)
docker compose up -d ibgateway

# Run backtest
uv run python scripts/backtest.py \
  --strategy sma_crossover \
  --instrument AAPL \
  --start-date 2023-01-01 \
  --end-date 2023-12-31

# View results
open reports/backtest_<run_id>.html
```

---

## Support & Contributing

### Documentation

- **CLAUDE.md**: Development guidelines and coding standards
- **README.md**: Quick start and setup instructions
- **PRD.md**: Original product requirements document
- **PRODUCT_OVERVIEW.md**: This document

### Contributing

1. Follow TDD approach (write tests first)
2. Maintain code quality standards (ruff, mypy)
3. Update documentation for new features
4. Ensure all tests pass before commit
5. Use conventional commit messages

### Getting Help

- **Issues**: Report bugs or request features via GitHub issues
- **Documentation**: Check docs/ directory for detailed guides
- **Tests**: Review tests/ directory for usage examples

---

## Appendix

### Glossary

- **Backtest**: Historical simulation of a trading strategy
- **Bar**: OHLCV data point (Open, High, Low, Close, Volume)
- **CAGR**: Compound Annual Growth Rate
- **Drawdown**: Peak-to-trough decline in portfolio value
- **IBKR**: Interactive Brokers
- **Nautilus Trader**: Event-driven backtesting framework
- **Parquet**: Columnar storage format (Apache Arrow)
- **Sharpe Ratio**: Risk-adjusted return metric
- **Slippage**: Difference between expected and actual execution price
- **TDD**: Test-Driven Development

### References

- [Nautilus Trader Documentation](https://nautilustrader.io/)
- [Interactive Brokers API](https://www.interactivebrokers.com/en/trading/ib-api.php)
- [Apache Parquet Format](https://parquet.apache.org/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)

---

**Document Version**: 1.0
**Last Updated**: January 2025
**Maintainer**: Trading-NTrader Development Team
