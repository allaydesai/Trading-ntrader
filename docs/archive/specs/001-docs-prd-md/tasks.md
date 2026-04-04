# Implementation Tasks: Nautilus Trader Backtesting System CLI

**Feature**: Nautilus Trader Backtesting System with IBKR Integration  
**Focus**: CLI Implementation (Phase 1)  
**Branch**: `001-docs-prd-md`  
**Generated**: 2025-01-13

## Overview

This document contains ordered implementation tasks for building the Nautilus Trader backtesting system CLI. Tasks are organized in **incremental milestones** where each milestone delivers working, testable functionality.

**Integration Philosophy**: Each milestone produces a working system that can be run and tested. We integrate continuously rather than building everything separately.

**Parallel Execution**: Tasks marked with [P] can be executed in parallel within the same milestone.

---

## Implementation Guide

### Quick Navigation Map

**Essential Reference Files**:
- `data-model.md` → Core entity definitions (8 entities, lines 9-283)
- `contracts/openapi.yaml` → API schemas and endpoints
- `cli-commands.md` → CLI interface specifications
- `research.md` → Technology decisions and rationale
- `quickstart.md` → Usage examples and validation scenarios

### Entity-to-Task Mapping

| Data Model Entity | Primary Implementation Tasks | Reference Lines |
|------------------|----------------------------|-----------------|
| TradingStrategy | T006, T025, T029 | data-model.md:9-28 |
| Instrument | T057 | data-model.md:30-47 |
| MarketData | T015, T055 | data-model.md:48-69 |
| BacktestConfiguration | T031, T032 | data-model.md:70-89 |
| Trade | T039 | data-model.md:90-116 |
| Portfolio | T040 | data-model.md:117-136 |
| BacktestResult | T033, T034 | data-model.md:137-188 |
| PerformanceReport | T041, T043, T056 | data-model.md:189-204 |

### API-to-Task Mapping

| API Endpoint Group | Implementation Tasks | OpenAPI Lines |
|-------------------|---------------------|---------------|
| /strategies | T006, T025, T330-T331 | 27-87 |
| /backtests | T032, T033, T341 | 89-171 |
| /data | T018, T019, T048, T051-T052 | 275-383 |
| /reports | T041, T042, T056 | 209-273 |

### CLI-to-Task Mapping

| CLI Command Group | Implementation Tasks | CLI-Commands Lines |
|-------------------|---------------------|-------------------|
| strategy commands | T330-T331 | 24-44 |
| backtest commands | T341-T342 | 46-74 |
| data commands | T019, T051-T052 | 76-108 |
| report commands | T042, T044 | 110-130 |

### Key Integration Points

1. **Nautilus Trader Integration** (Tasks T007, T009, T048)
   - Reference: research.md:7-16 (framework decision)
   - Core implementation: T009 (backtest runner)

2. **Database Layer** (Tasks T014-T016, T055)
   - TimescaleDB setup: T055
   - Migration strategy: data-model.md:277-283

3. **IBKR Integration** (Tasks T046-T052)
   - Connection: research.md:22-36
   - Rate limiting: 50 req/sec (research.md:28)

---

## Milestone 1: Basic CLI with Simple Backtest (T001-T012)
**Goal**: Run a basic SMA backtest from CLI with mock data

**Key References for Milestone 1**:
- Strategy model: data-model.md:9-28 (TradingStrategy entity) → T006
- Mock data generation: T008 (no external reference needed)
- CLI structure: cli-commands.md:12-21 (overview) → T004, T010
- Expected output: quickstart.md:98-116 (scenario 1 results) → T011

### T001: Initialize Python Project Structure
**File**: Project root structure  
**Dependencies**: None  
```bash
Create directory structure:
src/
├── __init__.py
├── cli/
│   ├── __init__.py
│   └── main.py
├── core/
│   ├── __init__.py
│   └── strategies/
├── models/
└── utils/
tests/
├── __init__.py
└── conftest.py
```

### T002: Setup UV and Core Dependencies
**File**: `pyproject.toml`, `.python-version`  
**Dependencies**: T001  
```bash
# Create .python-version with "3.11"
uv init
uv add nautilus_trader click rich pandas numpy
uv add pydantic pydantic-settings python-dotenv
uv add --dev pytest pytest-cov
```

### T003: Create Minimal Configuration
**File**: `src/config.py`  
**Dependencies**: T002  
```python
# Basic Pydantic Settings
# Just enough config to run a backtest
# Default values for testing
```

### T004: Create Basic CLI Entry Point
**File**: `src/cli/main.py`, `src/cli/__main__.py`  
**Dependencies**: T003  
```python
# Click application with version
# Single command: ntrader run-simple
# Help text
```

### T005: Write First Integration Test [P]
**File**: `tests/test_simple_backtest.py`  
**Dependencies**: T004  
```python
def test_can_run_simple_sma_backtest():
    """End-to-end test - MUST FAIL INITIALLY"""
    # Run: ntrader run-simple --strategy sma --data mock
    # Verify: Returns exit code 0 and shows results
```

### T006: Create SMA Strategy Model [P]
**File**: `src/models/strategy.py`  
**Dependencies**: T002  
**Reference**: data-model.md:9-28 (TradingStrategy entity)  
**API Contract**: contracts/openapi.yaml:404-443 (Strategy schemas)  
```python
# Pydantic model for SMA strategy (follow data-model.md:11-18 field structure)
# Parameters: fast_period, slow_period (per data-model.md:15)
# Validation rules (per data-model.md:20-23)
```

### T007: Implement Basic SMA Strategy
**File**: `src/core/strategies/sma_crossover.py`  
**Dependencies**: T006, T005  
```python
# Nautilus Trader strategy class
# Minimal implementation
# Works with mock data
# Make test T005 pass
```

### T008: Create Mock Data Generator [P]
**File**: `src/utils/mock_data.py`  
**Dependencies**: T002  
```python
# Generate simple OHLCV data
# Sine wave pattern for predictable results
# Return as pandas DataFrame
```

### T009: Implement Minimal Backtest Engine
**File**: `src/core/backtest_runner.py`  
**Dependencies**: T007, T008  
```python
# Wrap Nautilus BacktestEngine
# Load mock data
# Run SMA strategy
# Return basic metrics (total return, trades count)
```

### T010: Connect CLI to Backtest Engine
**File**: `src/cli/commands/run.py`  
**Dependencies**: T004, T009  
```python
@click.command()
def run_simple():
    """Run a simple backtest with mock data"""
    # Load SMA strategy
    # Generate mock data
    # Run backtest
    # Display results with Rich table
```

### T011: Add Basic Result Display
**File**: `src/cli/formatters.py`  
**Dependencies**: T010  
```python
# Rich table for results
# Format: Total Return, Trades, Win Rate
# Colored output (green for profit, red for loss)
```

### T012: Integration Test - Verify Milestone 1
**File**: `tests/test_milestone_1.py`  
**Dependencies**: T010, T011  
```python
def test_cli_runs_simple_backtest():
    """Verify we can run: ntrader run-simple"""
    # Should execute without errors
    # Should display results table
    # Should show positive return on mock data
```

**MILESTONE 1 COMPLETE**: Can run `ntrader run-simple` to execute basic SMA backtest

---

## Milestone 2: CSV Data Import & Real Backtest (T013-T024)
**Goal**: Import CSV data and run real backtests

**Key References for Milestone 2**:
- MarketData model: data-model.md:48-69 → T015
- Database migrations: data-model.md:277-283 → T016
- CSV import CLI: cli-commands.md:90-95 → T019
- Sample data format: quickstart.md:122-147 (scenario 2) → T023

### T013: Add Database Dependencies
**File**: `pyproject.toml`  
**Dependencies**: Milestone 1  
```bash
uv add sqlalchemy psycopg2-binary alembic
uv add --dev pytest-asyncio
```

### T014: Setup Database Configuration
**File**: `src/config.py`, `.env.example`  
**Dependencies**: T013  
```python
# Add database URL to config
# Support for PostgreSQL
# Connection pooling settings
```

### T015: Create Market Data Model
**File**: `src/models/market_data.py`  
**Dependencies**: T014  
**Reference**: data-model.md:48-69 (MarketData entity specification)  
**API Contract**: contracts/openapi.yaml:275-299 (data endpoints)  
```python
# Pydantic model for OHLCV (follow data-model.md:49-68 field definitions)
# SQLAlchemy model (simple table, no TimescaleDB yet)
# Validation rules (per data-model.md:60-64)
```

### T016: Setup Database Migrations
**File**: `alembic.ini`, `alembic/`  
**Dependencies**: T015  
```bash
alembic init alembic
# Configure for PostgreSQL
# Create migration for market_data table
alembic upgrade head
```

### T017: Write CSV Import Test
**File**: `tests/test_csv_import.py`  
**Dependencies**: T016  
```python
def test_can_import_csv_file():
    """Test CSV import - MUST FAIL INITIALLY"""
    # Run: ntrader data import --file sample.csv
    # Verify: Data stored in database
```

### T018: Implement CSV Data Loader
**File**: `src/services/csv_loader.py`  
**Dependencies**: T017  
```python
# Parse CSV with pandas
# Validate OHLCV format
# Store to database
# Make test T017 pass
```

### T019: Add Data Import Command
**File**: `src/cli/commands/data.py`  
**Dependencies**: T004, T018  
**CLI Reference**: cli-commands.md:76-108 (Data Commands section)  
**API Contract**: contracts/openapi.yaml:300-340 (data/import endpoint)  
```python
@click.group()
def data():
    """Data management commands"""

@data.command()
def import_csv(file):
    """Import CSV market data (per cli-commands.md:90-95)"""
    # Load file
    # Parse and validate
    # Store to database
    # Show progress bar
```

### T020: Create Data Service
**File**: `src/services/data_service.py`  
**Dependencies**: T018  
```python
# Fetch data from database
# Convert to Nautilus format
# Cache for performance
```

### T021: Update Backtest Runner for Real Data
**File**: `src/core/backtest_runner.py`  
**Dependencies**: T020, T009  
```python
# Add option to use database data
# Support date range selection
# Handle real commission/slippage
```

### T022: Add Backtest Command with Real Data
**File**: `src/cli/commands/backtest.py`  
**Dependencies**: T019, T021  
```python
@click.group()
def backtest():
    """Backtest commands"""

@backtest.command()
def run(strategy, symbol, start, end):
    """Run backtest with real data"""
    # Fetch data from database
    # Run backtest
    # Display comprehensive results
```

### T023: Create Sample CSV Data
**File**: `data/sample_data.csv`  
**Dependencies**: None  
```csv
# Create sample AAPL data for testing
# 1 month of daily data
# Realistic price movements
```

### T024: Integration Test - Verify Milestone 2
**File**: `tests/test_milestone_2.py`  
**Dependencies**: T022, T023  
```python
def test_csv_import_and_backtest():
    """Full CSV workflow test"""
    # Import sample CSV
    # Run backtest on imported data
    # Verify realistic results
```

**MILESTONE 2 COMPLETE**: Can import CSV and run real backtests

---

## Milestone 3: Multiple Strategies & Configuration (T025-T036)
**Goal**: Support multiple strategies with configuration files

### T025: Create Strategy Base Model
**File**: `src/models/strategy.py`  
**Dependencies**: Milestone 2  
```python
# Abstract base for all strategies
# Strategy type enum
# Parameter validation
```

### T026: Write Strategy Test Suite
**File**: `tests/test_strategies.py`  
**Dependencies**: T025  
```python
def test_mean_reversion_strategy():
    """Test mean reversion - MUST FAIL INITIALLY"""
def test_momentum_strategy():
    """Test momentum - MUST FAIL INITIALLY"""
```

### T027: Implement Mean Reversion Strategy
**File**: `src/core/strategies/mean_reversion.py`  
**Dependencies**: T026  
```python
# Z-score calculation
# Entry/exit logic
# Make mean reversion test pass
```

### T028: Implement Momentum Strategy
**File**: `src/core/strategies/momentum.py`  
**Dependencies**: T026  
```python
# RSI indicator
# Entry/exit signals
# Make momentum test pass
```

### T029: Create Strategy Factory
**File**: `src/core/strategy_factory.py`  
**Dependencies**: T027, T028  
```python
# Load strategy by name
# Configure parameters
# Validate settings
```

### T030: Add Strategy Management Commands
**File**: `src/cli/commands/strategy.py`  
**Dependencies**: T029  
```python
@strategy.command()
def list():
    """List available strategies"""

@strategy.command()
def create(name, type, params):
    """Create strategy configuration"""
```

### T031: Implement YAML Configuration
**File**: `src/utils/config_loader.py`  
**Dependencies**: T030  
```python
# Load YAML backtest configs
# Validate against schema
# Support multiple formats
```

### T032: Add Config-Based Backtest
**File**: `src/cli/commands/backtest.py`  
**Dependencies**: T031  
```python
@backtest.command()
def run_config(config_file):
    """Run backtest from config file"""
    # Load YAML config
    # Execute backtest
    # Save results
```

### T033: Create BacktestResult Model
**File**: `src/models/backtest_result.py`  
**Dependencies**: T032  
**Reference**: data-model.md:137-188 (BacktestResult entity)  
**API Contract**: contracts/openapi.yaml:529-590 (BacktestResult/PerformanceMetrics schemas)  
```python
# Pydantic model for results (follow data-model.md:140-149 field structure)
# SQLAlchemy model for persistence
# Performance metrics structure (per data-model.md:151-178 JSON format)
```

### T034: Add Results Persistence
**File**: `src/services/results_service.py`  
**Dependencies**: T033  
```python
# Save backtest results to database
# Query historical results
# Compare multiple runs
```

### T035: Create Example Configurations
**File**: `configs/examples/`  
**Dependencies**: T032  
```yaml
# sma_config.yaml
# mean_reversion_config.yaml
# momentum_config.yaml
```

### T036: Integration Test - Verify Milestone 3
**File**: `tests/test_milestone_3.py`  
**Dependencies**: T035  
```python
def test_all_strategies_work():
    """Test all three strategies"""
    # Run each strategy
    # Verify different results
    # Check config loading
```

**MILESTONE 3 COMPLETE**: Three working strategies with config file support

---

## Milestone 4: Performance Metrics & Basic Reports (T037-T045)
**Goal**: Calculate comprehensive metrics and generate simple reports

### T037: Write Metrics Test Suite
**File**: `tests/test_metrics.py`  
**Dependencies**: Milestone 3  
```python
def test_sharpe_ratio_calculation():
    """Test Sharpe - MUST FAIL INITIALLY"""
def test_max_drawdown_calculation():
    """Test drawdown - MUST FAIL INITIALLY"""
```

### T038: Implement Performance Calculator
**File**: `src/services/performance.py`  
**Dependencies**: T037  
```python
# Sharpe ratio
# Sortino ratio
# Max drawdown
# Win rate
# Make tests pass
```

### T039: Add Trade Model
**File**: `src/models/trade.py`  
**Dependencies**: T038  
**Reference**: data-model.md:90-116 (Trade entity)  
**API Contract**: contracts/openapi.yaml:591-623 (Trade schema)  
```python
# Pydantic model for trades (follow data-model.md:92-105 field structure)
# SQLAlchemy model
# PnL calculations (per data-model.md:107-111 validation rules)
```

### T040: Create Portfolio Tracker
**File**: `src/services/portfolio.py`  
**Dependencies**: T039  
```python
# Track positions
# Calculate equity curve
# Daily snapshots
```

### T041: Implement Text Report Generator
**File**: `src/services/reports/text_report.py`  
**Dependencies**: T040  
```python
# Generate formatted text report
# Include all metrics
# Trade summary
```

### T042: Add Report Command
**File**: `src/cli/commands/report.py`  
**Dependencies**: T041  
```python
@click.group()
def report():
    """Report commands"""

@report.command()
def generate(backtest_id, format):
    """Generate report"""
```

### T043: Create CSV Exporter
**File**: `src/services/reports/csv_exporter.py`  
**Dependencies**: T042  
```python
# Export trades to CSV
# Export metrics to CSV
# Portfolio snapshots
```

### T044: Add Summary Command
**File**: `src/cli/commands/report.py`  
**Dependencies**: T043  
```python
@report.command()
def summary(backtest_id):
    """Quick summary to console"""
    # Display key metrics
    # Rich formatted table
```

### T045: Integration Test - Verify Milestone 4
**File**: `tests/test_milestone_4.py`  
**Dependencies**: T044  
```python
def test_metrics_and_reports():
    """Test full metrics calculation"""
    # Run backtest
    # Verify all metrics present
    # Generate text and CSV reports
```

**MILESTONE 4 COMPLETE**: Full metrics calculation with text/CSV reports

---

## Milestone 5: IBKR Integration (T046-T054)
**Goal**: Connect to Interactive Brokers for real market data

**Key References for Milestone 5**:
- IBKR research: research.md:22-36 (integration decision) → T048
- Rate limiting: research.md:28 (50 req/sec limit) → T048
- IBKR CLI commands: cli-commands.md:78-83, cli-commands.md:310-328 → T051, T052
- Docker setup: quickstart.md:215-224 → T053
- Connection API: contracts/openapi.yaml:341-383 → T048

### T046: Add IBKR Dependencies
**File**: `pyproject.toml`  
**Dependencies**: Milestone 4  
```bash
uv add nautilus_trader[ib]
```

### T047: Write IBKR Connection Test
**File**: `tests/test_ibkr.py`  
**Dependencies**: T046  
```python
def test_ibkr_connection():
    """Test IBKR - MUST FAIL INITIALLY"""
    # Mock connection for testing
def test_rate_limiting():
    """Test 50 req/sec limit"""
```

### T048: Implement IBKR Client
**File**: `src/services/ibkr_client.py`  
**Dependencies**: T047  
**Research Reference**: research.md:22-36 (IBKR Integration decision)  
**API Contract**: contracts/openapi.yaml:341-383 (data/ibkr/connect endpoint)  
```python
# Nautilus IBKR adapter (per research.md:24-31 rationale)
# Connection management
# Rate limiting (per research.md:28 - 50 req/sec limit)
# Make tests pass
```

### T049: Add IBKR Config
**File**: `src/config.py`  
**Dependencies**: T048  
```python
# IBKR host/port settings
# Paper vs live mode
# Client ID management
```

### T050: Create Data Fetcher
**File**: `src/services/data_fetcher.py`  
**Dependencies**: T048  
```python
# Fetch historical data from IBKR
# Handle different timeframes
# Store to database
```

### T051: Add Connect Command
**File**: `src/cli/commands/data.py`  
**Dependencies**: T050  
```python
@data.command()
def connect(host, port, paper):
    """Connect to IBKR"""
    # Establish connection
    # Verify account
    # Show status
```

### T052: Add Fetch Command
**File**: `src/cli/commands/data.py`  
**Dependencies**: T051  
```python
@data.command()
def fetch(instruments, start, end):
    """Fetch historical data"""
    # Connect to IBKR
    # Download data
    # Store to database
```

### T053: Create Docker Setup for IB Gateway
**File**: `docker-compose.yml`  
**Dependencies**: T052  
```yaml
# IB Gateway service
# PostgreSQL service
# Network configuration
```

### T054: Integration Test - Verify Milestone 5
**File**: `tests/test_milestone_5.py`  
**Dependencies**: T053  
```python
def test_ibkr_data_fetch():
    """Test IBKR integration"""
    # Connect to IBKR (mock)
    # Fetch data
    # Run backtest
```

**MILESTONE 5 COMPLETE**: IBKR connection working with data fetch

---

## Milestone 6: Advanced Features (T055-T065)
**Goal**: HTML reports, multi-timeframe, interactive mode

### T055: Setup TimescaleDB
**File**: `alembic/versions/`  
**Dependencies**: Milestone 5  
```sql
-- Add TimescaleDB extension
-- Convert market_data to hypertable
-- Add indexes
```

### T056: Implement HTML Report Generator
**File**: `src/services/reports/html_generator.py`  
**Dependencies**: T055  
```python
# Jinja2 templates
# Plotly.js charts
# Equity curve
# Drawdown chart
```

### T057: Add Instrument Model
**File**: `src/models/instrument.py`  
**Dependencies**: T056  
```python
# Full instrument specifications
# Asset class support
# Trading hours
```

### T058: Create Commission Models
**File**: `src/services/commission.py`  
**Dependencies**: T057  
```python
# Fixed commission
# Percentage commission
# Realistic slippage
```

### T059: Implement Multi-Timeframe Support
**File**: `src/core/multi_timeframe.py`  
**Dependencies**: T058  
```python
# Load multiple timeframes
# Synchronize data
# Strategy support
```

### T060: Add Interactive Mode
**File**: `src/cli/interactive.py`  
**Dependencies**: T059  
```python
# REPL interface
# Command history
# Tab completion
```

### T061: Create Comparison Service
**File**: `src/services/reports/comparison.py`  
**Dependencies**: T060  
```python
# Compare multiple backtests
# Statistical significance
# Relative performance
```

### T062: Add Logging Throughout
**File**: Various files  
**Dependencies**: T061  
```python
# structlog setup
# Correlation IDs
# Debug mode
```

### T063: Performance Optimization
**File**: Various files  
**Dependencies**: T062  
```python
# Add caching
# Query optimization
# Memory profiling
```

### T064: Complete Documentation
**File**: `docs/`, `README.md`  
**Dependencies**: T063  
```markdown
# User guide
# API reference
# Examples
```

### T065: Final Integration Test Suite
**File**: `tests/test_integration.py`  
**Dependencies**: T064  
```python
def test_complete_workflow():
    """Test everything works together"""
    # All 4 scenarios from quickstart.md
    # Performance benchmarks
    # Memory usage checks
```

**MILESTONE 6 COMPLETE**: Full feature set with advanced capabilities

---

## Milestone 7: Polish & Production (T066-T070)
**Goal**: Production-ready with monitoring and deployment

### T066: Add Redis Cache
**File**: `src/services/cache.py`  
**Dependencies**: Milestone 6  
```python
# Redis connection
# Cache strategies
# TTL management
```

### T067: Implement Health Checks
**File**: `src/utils/health.py`  
**Dependencies**: T066  
```python
# Database connectivity
# IBKR connection status
# Memory usage
```

### T068: Create Deployment Scripts
**File**: `scripts/deploy.sh`  
**Dependencies**: T067  
```bash
# Database setup
# Migration runner
# Service startup
```

### T069: Add Monitoring
**File**: `src/utils/monitoring.py`  
**Dependencies**: T068  
```python
# Prometheus metrics
# Performance tracking
# Error rates
```

### T070: Final Validation
**File**: `tests/test_production.py`  
**Dependencies**: T069  
**Validation Reference**: quickstart.md:257-325 (Test scenarios 1-3)  
```python
def test_production_ready():
    """Validate production requirements"""
    # 80% test coverage
    # Performance benchmarks (per quickstart.md:363-374)
    # All scenarios pass (quickstart.md:62-96, 118-147, 149-209, 211-255)
```

**MILESTONE 7 COMPLETE**: Production-ready system

---

## Execution Strategy by Milestone

### Milestone Deliverables

1. **Milestone 1** (Day 1-2): Basic working CLI with simple backtest
   - Deliverable: `ntrader run-simple` works with mock data
   
2. **Milestone 2** (Day 3-4): CSV import and real backtests
   - Deliverable: Can import CSV and run backtests on real data
   
3. **Milestone 3** (Day 5-6): Multiple strategies with configs
   - Deliverable: 3 strategies working with YAML configs
   
4. **Milestone 4** (Day 7-8): Metrics and basic reports
   - Deliverable: Full metrics with text/CSV reports
   
5. **Milestone 5** (Day 9-10): IBKR integration
   - Deliverable: Can fetch data from IBKR and run backtests
   
6. **Milestone 6** (Day 11-13): Advanced features
   - Deliverable: HTML reports, multi-timeframe, interactive mode
   
7. **Milestone 7** (Day 14-15): Production polish
   - Deliverable: Production-ready with monitoring

### Testing at Each Milestone

```bash
# Milestone 1 Test
ntrader run-simple
# Expected: Shows results table

# Milestone 2 Test  
ntrader data import --file sample.csv
ntrader backtest run --strategy sma --symbol AAPL --start 2020-01-01 --end 2020-01-31
# Expected: Real backtest results

# Milestone 3 Test
ntrader strategy list
ntrader backtest run-config configs/examples/momentum_config.yaml
# Expected: Multiple strategies working

# Milestone 4 Test
ntrader report summary latest
ntrader report generate --backtest latest --format csv
# Expected: Full metrics and reports

# Milestone 5 Test
ntrader data connect --host localhost --port 7497
ntrader data fetch --instruments AAPL --start 2020-01-01 --end 2020-12-31
# Expected: IBKR data fetched

# Milestone 6 Test
ntrader report generate --backtest latest --format html
ntrader interactive
# Expected: HTML report and interactive mode

# Milestone 7 Test
docker-compose up
ntrader health-check
# Expected: Production deployment working
```

---

## Parallel Execution Within Milestones

### Milestone 1 Parallel Tasks
```bash
# After T004, can parallelize:
- T005: Write integration test
- T006: Create SMA model
- T008: Create mock data generator
```

### Milestone 2 Parallel Tasks
```bash
# After T016, can parallelize:
- T017: Write CSV test
- T023: Create sample data
```

### Milestone 3 Parallel Tasks
```bash
# After T026, can parallelize:
- T027: Mean reversion strategy
- T028: Momentum strategy
- T035: Example configurations
```

### Milestone 4 Parallel Tasks
```bash
# After T038, can parallelize:
- T041: Text report generator
- T043: CSV exporter
```

---

## Success Criteria Per Milestone

### Milestone 1 ✓
- [ ] CLI runs without errors
- [ ] Simple backtest executes
- [ ] Results displayed in table

### Milestone 2 ✓
- [ ] CSV import works
- [ ] Database stores data
- [ ] Real backtest runs

### Milestone 3 ✓
- [ ] Three strategies available
- [ ] Config files work
- [ ] Results persisted

### Milestone 4 ✓
- [ ] All metrics calculated
- [ ] Reports generated
- [ ] CSV export works

### Milestone 5 ✓
- [ ] IBKR connection established
- [ ] Historical data fetched
- [ ] Rate limiting works

### Milestone 6 ✓
- [ ] HTML reports with charts
- [ ] Multi-timeframe support
- [ ] Interactive mode works

### Milestone 7 ✓
- [ ] 80% test coverage
- [ ] Docker deployment
- [ ] Production monitoring

---

## Key Differences from Previous Version

1. **Incremental Integration**: Each milestone produces a working system
2. **Early Testing**: Can test functionality from Milestone 1
3. **Gradual Complexity**: Start simple, add features incrementally
4. **Continuous Validation**: Integration tests at each milestone
5. **User Value Early**: Basic functionality available immediately
6. **Risk Reduction**: Problems detected early, not at the end
7. **Clear Deliverables**: Each milestone has specific working features

---

## Cross-Reference Validation ✓

**All Major Tasks Now Include**:
- ✅ **File references**: Specific implementation file paths
- ✅ **Dependency mapping**: Clear task prerequisites  
- ✅ **Data model references**: Links to entity specifications (data-model.md:line-numbers)
- ✅ **API contract references**: Links to OpenAPI schemas (contracts/openapi.yaml:line-numbers)
- ✅ **CLI command references**: Links to command specifications (cli-commands.md:line-numbers)
- ✅ **Research references**: Links to technology decisions (research.md:line-numbers)

**Implementation Ready**:
- ✅ Each milestone has clear navigation guide
- ✅ Entity-to-task mapping complete
- ✅ API-to-task mapping complete  
- ✅ CLI-to-task mapping complete
- ✅ Key integration points identified
- ✅ Validation scenarios linked to quickstart.md

**Next Step**: Begin implementation with **Milestone 1 (T001-T012)** - all references are in place.

---

*Generated from specifications in `/specs/001-docs-prd-md/`*  
*Cross-references added 2025-01-13*