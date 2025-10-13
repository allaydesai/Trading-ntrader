# NTrader - Nautilus Trader Backtesting System

A production-grade algorithmic trading backtesting system built with the Nautilus Trader framework. This system allows you to backtest trading strategies with historical data and Interactive Brokers integration.

## Features

- 🚀 **Multiple Trading Strategies**: Three built-in strategies with dynamic configuration
  - **SMA Crossover**: Simple Moving Average crossover strategy
  - **RSI Mean Reversion**: RSI-based mean reversion with trend filter
  - **SMA Momentum**: Moving average momentum strategy (golden/death cross)
- 📊 **Strategy Management**: CLI commands for strategy discovery, configuration, and validation
- 📋 **YAML Configuration**: Flexible strategy configuration via YAML files
- 📊 **Mock Data Generation**: Synthetic data with predictable patterns for testing
- 📈 **CSV Data Import**: Import real market data from CSV files with validation
- 📡 **Interactive Brokers Integration**: ✨ NEW - Fetch real market data from IBKR TWS/Gateway
- 🗄️ **Database Storage**: PostgreSQL with optimized time-series storage
- 🖥️ **CLI Interface**: Easy-to-use command line interface with data management
- 📊 **Performance Analytics**: Comprehensive metrics using Nautilus Trader analytics framework
  - **Risk Metrics**: Sharpe Ratio, Sortino Ratio, Calmar Ratio, Maximum Drawdown
  - **Trade Statistics**: Win rate, profit factor, expectancy, average win/loss
  - **Portfolio Tracking**: Real-time PnL, position monitoring, equity curves
- 📋 **Report Generation**: Multi-format report export with rich visualizations
  - **Text Reports**: Rich-formatted console output with tables and charts
  - **CSV Export**: Precision-preserved data export for spreadsheet analysis
  - **JSON Export**: Structured data for programmatic analysis
- ⚡ **Fast Execution**: Built on Nautilus Trader's high-performance engine
- 🧪 **Test Coverage**: Comprehensive test suite with 144+ tests (Milestones 1-5)
- 🔄 **Real Data Backtesting**: Run backtests on imported historical data with full analytics
- 📡 **Interactive Brokers Integration**: Fetch real market data from IBKR TWS/Gateway

## Current Status & Capabilities

### ✅ What Works (Milestone 5 Complete)
- **Strategy Management**: Discover, create, and validate strategy configurations
- **Multiple Strategy Types**: SMA Crossover, RSI Mean Reversion, SMA Momentum
- **Database Backtesting**: All three strategies work with imported CSV data
- **YAML Configuration**: Create and validate strategy configs via CLI
- **Mock Data Testing**: Test strategies with synthetic data using YAML configs
- **Performance Analytics**: Comprehensive metrics with Nautilus Trader integration
  - Sharpe, Sortino, Calmar ratios
  - Maximum drawdown with recovery tracking
  - Win rate, profit factor, expectancy
  - Portfolio tracking and equity curves
- **Report Generation**: Multi-format exports (text, CSV, JSON)
- **Results Persistence**: Save and retrieve backtest results with full analytics
- **Interactive Brokers Integration**: ✨ NEW in Milestone 5
  - Fetch historical data directly from IBKR TWS/Gateway
  - Support for multiple instruments and timeframes
  - Built-in rate limiting (45 req/sec) to prevent API throttling
  - Data stored in Parquet format via Nautilus catalog
  - Delayed/frozen market data for paper trading accounts

### 🚧 Current Limitations
- **YAML + Database Integration**: `run-config` command supports mock data only
- **Strategy Parameters**: Limited to predefined parameters per strategy type

### 📋 Command Quick Reference
```bash
# Strategy management
uv run python -m src.cli.main strategy list                    # ✅ List available strategies
uv run python -m src.cli.main strategy create --type <type>    # ✅ Create config template
uv run python -m src.cli.main strategy validate <config>       # ✅ Validate config

# IBKR data fetching (New in Milestone 5)
uv run python -m src.cli.main data connect                     # ✅ Test IBKR connection
uv run python -m src.cli.main data fetch --instruments AAPL    # ✅ Fetch historical data

# Database backtesting (recommended)
uv run python -m src.cli.main backtest run --strategy <type>   # ✅ All strategies supported

# Mock data testing
uv run python -m src.cli.main backtest run-config <config>     # ✅ YAML configs supported

# Performance reports (New in Milestone 4)
uv run python -m src.cli report summary <result-id>            # ✅ Quick performance summary
uv run python -m src.cli report generate --result-id <id>      # ✅ Generate text report
uv run python -m src.cli report generate --result-id <id> --format csv  # ✅ Export to CSV
uv run python -m src.cli report list                           # ✅ List saved results
```

## Quick Start

### Prerequisites

- Python 3.11+
- UV package manager
- Docker (for PostgreSQL database)
- **Optional**: Interactive Brokers TWS/Gateway (for live data fetching)
  - See [IBKR Setup Guide](docs/IBKR_SETUP.md) for detailed instructions

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd Trading-ntrader
```

2. Install dependencies using UV:
```bash
# Install UV if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv sync
```

3. Set up environment variables:
```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your specific configuration
```

4. Run database migrations:
```bash
# Initialize the database schema
uv run alembic upgrade head
```

## Quick Start Guide

### Your First Backtest in 3 Steps

Follow this guide to run your first successful backtest using the included sample data:

#### Step 1: Import Sample Data
```bash
# Import the included AAPL sample data (covers Jan 2, 2024, 50 minutes of 1-min bars)
uv run python -m src.cli.main data import-csv --file data/sample_AAPL.csv --symbol AAPL
```

Expected output:
```
✅ CSV Import Successful
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric              ┃ Value                                                 ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ File                │ data/sample_AAPL.csv                                 │
│ Symbol              │ AAPL                                                  │
│ Records Processed   │ 50                                                    │
│ Records Inserted    │ 50                                                    │
│ Duplicates Skipped  │ 0                                                     │
└─────────────────────┴───────────────────────────────────────────────────────┘
```

#### Step 2: Verify Data Import
```bash
# List available strategies and data
uv run python -m src.cli.main backtest list
```

You should see AAPL listed in the available symbols.

#### Step 3: Run Your First Backtest
```bash
# Run SMA crossover strategy on the imported data (with specific times)
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2024-01-02 09:30:00" \
  --end "2024-01-02 10:20:00" \
  --fast-period 5 \
  --slow-period 10
```

Alternative: Use the new YAML configuration approach (mock data only):
```bash
# Create a strategy config template
uv run python -m src.cli.main strategy create --type sma_crossover --output my_strategy.yaml

# Run backtest with config file (uses mock data for testing)
uv run python -m src.cli.main backtest run-config my_strategy.yaml
```

**Note**: YAML-based backtesting currently supports mock data only. For database backtests, use the `backtest run` command with strategy parameters.

Expected output:
```
🚀 Running SMA backtest for AAPL
   Period: 2024-01-02 to 2024-01-02
   Strategy: Fast SMA(5) vs Slow SMA(10)
   Trade Size: 1,000,000

✅ Data validation passed
   Available data range: 2024-01-02 09:30:00+00:00 to 2024-01-02 10:20:00+00:00

🎯 Backtest Results
[Results table with trade statistics]
```

**Note**: If you use just dates (e.g., `--start 2024-01-02`) you'll get a helpful error showing the exact available time range. Use `backtest list` to see all available data ranges.

🎉 **Congratulations!** You've successfully run your first backtest with real market data.

### Database Setup

This project uses PostgreSQL with TimescaleDB for time-series data storage. You can set up the database using either Docker (recommended for beginners) or a local PostgreSQL installation.

#### Option 1: Docker Setup (Recommended)

**Prerequisites:**
- Docker and Docker Desktop
- PostgreSQL client tools

**Setup Steps:**

1. **Pull PostgreSQL Docker image**:
```bash
docker pull postgres:17
```

2. **Create a volume for data persistence**:
```bash
docker volume create pgdata
```

3. **Run PostgreSQL container**:
```bash
docker run -d --name pgdb \
  -e POSTGRES_USER=ntrader \
  -e POSTGRES_PASSWORD=ntrader_dev_2025 \
  -e POSTGRES_DB=trading_ntrader \
  -p 5432:5432 \
  -v pgdata:/var/lib/postgresql/data \
  postgres:17
```

4. **Verify database connection**:
```bash
# Test connection
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader -c "SELECT version();"
```

**Container Management:**

```bash
# Start the database
docker start pgdb

# Stop the database
docker stop pgdb

# View logs
docker logs pgdb

# Remove container (data persists in volume)
docker rm pgdb
```

**Data Storage Location:**
- Docker volume: `pgdata` (managed by Docker)
- Physical location (Linux): `/var/lib/docker/volumes/pgdata/_data`
- Physical location (macOS): `~/Library/Containers/com.docker.docker/Data/vms/0/`
- To inspect volume: `docker volume inspect pgdata`

#### Option 2: Local PostgreSQL Installation

**Prerequisites:**
- PostgreSQL 17+ installed locally

**macOS Setup:**

1. **Install PostgreSQL via Homebrew**:
```bash
brew install postgresql@17
brew services start postgresql@17
```

2. **Create database and user**:
```bash
# Connect as postgres superuser
psql postgres

# In psql prompt:
CREATE USER ntrader WITH PASSWORD 'ntrader_dev_2025';
CREATE DATABASE trading_ntrader OWNER ntrader;
GRANT ALL PRIVILEGES ON DATABASE trading_ntrader TO ntrader;
\q
```

3. **Verify connection**:
```bash
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader -c "SELECT version();"
```

**Data Storage Location (macOS Homebrew):**
- Data directory: `/opt/homebrew/var/postgresql@17/`
- Contains all database files, WAL logs, and configuration
- Check database size: `du -sh /opt/homebrew/var/postgresql@17/`
- Check specific database: `PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader -c "SELECT pg_size_pretty(pg_database_size('trading_ntrader'));"`

**Ubuntu/Debian Setup:**

1. **Install PostgreSQL**:
```bash
sudo apt update
sudo apt install postgresql-17 postgresql-contrib
```

2. **Create database and user**:
```bash
sudo -u postgres psql
CREATE USER ntrader WITH PASSWORD 'ntrader_dev_2025';
CREATE DATABASE trading_ntrader OWNER ntrader;
GRANT ALL PRIVILEGES ON DATABASE trading_ntrader TO ntrader;
\q
```

**Data Storage Location (Ubuntu/Debian):**
- Data directory: `/var/lib/postgresql/17/main/`
- Check size: `sudo du -sh /var/lib/postgresql/17/main/`

#### Database Configuration

The database connection is configured through environment variables in your `.env` file:

```env
DATABASE_URL=postgresql://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
DATABASE_POOL_TIMEOUT=30
```

## Sample Data

The project includes several sample CSV files in the `data/` directory for testing and learning:

### Available Sample Files

#### 1. sample_AAPL.csv
- **Symbol**: AAPL (Apple Inc.)
- **Date Range**: January 2, 2024, 09:30:00 to 10:20:00 (50 minutes)
- **Frequency**: 1-minute bars
- **Records**: 50 data points
- **Best for**: Quick testing and learning the system

```bash
# Import this file with:
uv run python -m src.cli.main data import-csv --file data/sample_AAPL.csv --symbol AAPL

# Then run backtest with:
uv run python -m src.cli.main backtest run --symbol AAPL --start 2024-01-02 --end 2024-01-02
```

#### 2. AAPL_test_2018.csv
- **Symbol**: AAPL (Apple Inc.)
- **Date Range**: February 5-8, 2018 (04:00 to 19:59 each day)
- **Frequency**: 1-minute bars
- **Records**: ~4,800 data points
- **Best for**: Longer backtests and strategy validation

```bash
# Import this file with:
uv run python -m src.cli.main data import-csv --file data/AAPL_test_2018.csv --symbol AAPL2018

# Then run backtest with dates only (system will show exact time range if needed):
uv run python -m src.cli.main backtest run --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08

# Or with specific times:
uv run python -m src.cli.main backtest run --symbol AAPL2018 --start "2018-02-05 04:00:00" --end "2018-02-08 19:59:00"
```

#### 3. AAPL_1min.csv
- **Symbol**: AAPL (Apple Inc.)
- **Date Range**: January 4, 2010 to December 31, 2019 (10 years)
- **Frequency**: 1-minute bars
- **Records**: 1.6+ million data points (94MB+ file)
- **Best for**: Performance testing and comprehensive backtests

```bash
# Import this file with:
uv run python -m src.cli.main data import-csv --file data/AAPL_1min.csv --symbol AAPL_LARGE

# Then run backtest with any year from 2010-2019:
uv run python -m src.cli.main backtest run --symbol AAPL_LARGE --start 2019-01-01 --end 2019-12-31
```

### CSV Format Requirements

All CSV files must follow this exact format:
```csv
timestamp,open,high,low,close,volume
2024-01-02 09:30:00,185.25,186.50,184.75,185.95,2847300
2024-01-02 09:31:00,185.95,186.25,185.50,186.10,1254800
```

**Format Rules:**
- Timestamp format: `YYYY-MM-DD HH:MM:SS`
- OHLC prices: Decimal numbers (high ≥ open,close,low and low ≤ open,close,high)
- Volume: Integer values
- No missing values allowed
- Timestamps must be unique per symbol

### Usage

#### Strategy Management (New in Milestone 3)

The system now supports multiple trading strategies with flexible configuration management:

##### Available Strategies

1. **SMA Crossover** (`sma_crossover`): Classic moving average crossover strategy
2. **RSI Mean Reversion** (`mean_reversion`): RSI-based mean reversion with trend filter
3. **SMA Momentum** (`momentum`): Moving average momentum strategy with golden/death cross

##### Strategy Discovery

List all available strategies:
```bash
uv run python -m src.cli.main strategy list
```

Output shows strategy types, descriptions, and implementations:
```
📊 Available Strategies

                          Supported Trading Strategies
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ Strategy Type  ┃ Description                                ┃ Implementation ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ sma_crossover  │ Simple Moving Average Crossover Strategy   │ SMACrossover   │
│ mean_reversion │ RSI Mean Reversion Strategy with Trend     │ RSIMeanRev     │
│                │ Filter                                     │                │
│ momentum       │ SMA Momentum Strategy (Golden/Death Cross) │ SMAMomentum    │
└────────────────┴────────────────────────────────────────────┴────────────────┘
```

##### Configuration Management

Create configuration templates for any strategy:
```bash
# Create RSI mean reversion config
uv run python -m src.cli.main strategy create --type mean_reversion --output my_mr_config.yaml

# Create SMA momentum config
uv run python -m src.cli.main strategy create --type momentum --output my_momentum_config.yaml

# Create SMA crossover config
uv run python -m src.cli.main strategy create --type sma_crossover --output my_sma_config.yaml
```

Each template includes strategy-specific parameters with sensible defaults.

##### Configuration Validation

Validate your strategy configurations before running backtests:
```bash
uv run python -m src.cli.main strategy validate my_mr_config.yaml
```

Example validation output:
```
🔍 Validating my_mr_config.yaml...
✅ Config valid
   Strategy: RSIMeanRev
   Config class: RSIMeanRevConfig

📋 Configuration Parameters:
   rsi_period: 2
   rsi_buy_threshold: 10.0
   exit_rsi: 50.0
   sma_trend_period: 200
   trade_size: 1000000
   [... other parameters]
```

##### Running Backtests with YAML Configs

Use your validated configurations in backtests (mock data only):
```bash
# Run backtest with YAML config (uses mock data)
uv run python -m src.cli.main backtest run-config my_mr_config.yaml

# For database backtests, use the legacy command:
uv run python -m src.cli.main backtest run \
  --strategy mean_reversion \
  --symbol AAPL \
  --start 2024-01-02 \
  --end 2024-01-02
```

**Current Limitation**: The `run-config` command currently supports mock data only. Database integration with YAML configs is planned for a future release.

##### Strategy Parameter Examples

**RSI Mean Reversion Parameters:**
- `rsi_period`: RSI calculation period (default: 2)
- `rsi_buy_threshold`: Buy when RSI below this level (default: 10.0)
- `exit_rsi`: Exit when RSI above this level (default: 50.0)
- `sma_trend_period`: Trend filter SMA period (default: 200)
- `cooldown_bars`: Optional cooldown after exit (default: 0)

**SMA Momentum Parameters:**
- `fast_period`: Fast moving average period (default: 50)
- `slow_period`: Slow moving average period (default: 200)
- `allow_short`: Enable short selling on death cross (default: false)

**SMA Crossover Parameters:**
- `fast_period`: Fast moving average period (default: 10)
- `slow_period`: Slow moving average period (default: 20)

#### CSV Data Import

Import historical market data from CSV files:

```bash
# Import CSV data for a symbol
uv run python -m src.cli.main data import-csv --file data/sample_AAPL.csv --symbol AAPL

# List available data for a symbol
uv run python -m src.cli.main data list --symbol AAPL
```

##### See Sample Data Section Above

For detailed information about available sample files and CSV format requirements, see the **Sample Data** section above.

#### Backtesting

##### List Available Data and Strategies

Before running backtests, check what data and strategies are available:

```bash
# List all available strategies and imported data
uv run python -m src.cli.main backtest list
```

This shows:
- Available trading strategies (SMA Crossover, RSI Mean Reversion, SMA Momentum)
- All symbols with imported data
- Date ranges for each symbol

##### Basic SMA Backtest (Mock Data)

Run a simple SMA crossover backtest with mock data:

```bash
uv run python -m src.cli.main run-simple
```

##### Real Data Backtest

Run backtests on imported historical data with different strategies:

```bash
# Run SMA crossover backtest
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start 2024-01-02 \
  --end 2024-01-02

# Run RSI mean reversion backtest
uv run python -m src.cli.main backtest run \
  --strategy mean_reversion \
  --symbol AAPL \
  --start 2024-01-02 \
  --end 2024-01-02

# Run SMA momentum backtest
uv run python -m src.cli.main backtest run \
  --strategy momentum \
  --symbol AAPL \
  --start 2024-01-02 \
  --end 2024-01-02
```

**Important**: Always check available data ranges first with `uv run python -m src.cli.main backtest list` to ensure your start/end dates match the imported data.

##### Custom Parameters

Customize the backtest with your own parameters:

```bash
uv run python -m src.cli.main run-simple \
  --fast-period 5 \
  --slow-period 10 \
  --trade-size 100000 \
  --bars 200
```

#### Performance Reports & Analytics (New in Milestone 4)

After running a backtest, you can generate comprehensive performance reports with detailed analytics.

##### List Saved Backtest Results

```bash
# List all saved backtest results
uv run python -m src.cli report list

# Limit results shown
uv run python -m src.cli report list --limit 5
```

##### Quick Performance Summary

Get a quick overview of backtest performance:

```bash
uv run python -m src.cli report summary <result-id>
```

Example output:
```
📊 Performance Summary for result-abc123

┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Metric            ┃ Value      ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Total Return      │ 15.3%      │
│ Sharpe Ratio      │ 1.42       │
│ Sortino Ratio     │ 1.68       │
│ Max Drawdown      │ -8.7%      │
│ Win Rate          │ 58.3%      │
│ Profit Factor     │ 1.85       │
└───────────────────┴────────────┘
```

##### Generate Detailed Reports

Generate comprehensive reports in multiple formats:

```bash
# Text report with rich formatting (default)
uv run python -m src.cli report generate --result-id <id>

# Text report saved to file
uv run python -m src.cli report generate --result-id <id> --format text -o report.txt

# CSV export for spreadsheet analysis
uv run python -m src.cli report generate --result-id <id> --format csv -o results.csv

# JSON export for programmatic analysis
uv run python -m src.cli report generate --result-id <id> --format json -o results.json

# Export all formats at once
uv run python -m src.cli report export-all <result-id> -o reports/
```

**Report Contents:**
- **Summary Panel**: Key performance metrics at a glance
- **Returns Analysis**: Total return, CAGR, annual return, volatility
- **Risk Metrics**: Sharpe, Sortino, Calmar ratios, maximum drawdown
- **Trading Statistics**: Win rate, profit factor, expectancy, avg win/loss
- **Trade Details**: Complete trade history with entry/exit prices and PnL
- **Portfolio Analytics**: Position tracking, equity curve, exposure analysis

#### Command Options

##### Data Commands
- `data import-csv --file <path> --symbol <symbol>`: Import CSV market data
- `data list --symbol <symbol>`: List available data for a symbol
- **IBKR Data Commands** (New in Milestone 5):
  - `data connect`: Test connection to Interactive Brokers TWS/Gateway
    - `--host`: IBKR host address (default: 127.0.0.1)
    - `--port`: IBKR port (default: 7497 for paper trading)
  - `data fetch`: Fetch historical data from IBKR
    - `--instruments`: Comma-separated list of instruments (e.g., AAPL,MSFT)
    - `--start`: Start date (YYYY-MM-DD)
    - `--end`: End date (YYYY-MM-DD)
    - `--timeframe`: Bar timeframe (DAILY, 1-HOUR, 5-MINUTE, 1-MINUTE)

##### Strategy Management Commands
- `strategy list`: List all available trading strategies
- `strategy create --type <strategy> --output <file>`: Create strategy config template
- `strategy validate <config-file>`: Validate strategy configuration file

##### Backtest Commands
- `backtest list`: List available strategies and imported data
- `run-simple`: Run simple backtest with mock data
- `backtest run`: Run backtest with real data (legacy command-line config)
  - `--strategy`: Trading strategy to use (`sma_crossover`, `mean_reversion`, `momentum`)
  - `--symbol`: Trading symbol (required for real data)
  - `--start`: Start date for backtest (YYYY-MM-DD)
  - `--end`: End date for backtest (YYYY-MM-DD)
  - `--fast-period`: Fast SMA period (default: 10)
  - `--slow-period`: Slow SMA period (default: 20)
  - `--trade-size`: Trade size in base currency (default: 1,000,000)
- `backtest run-config <config-file>`: Run backtest with YAML configuration
  - `--symbol`: Trading symbol (required)
  - `--start`: Start date for backtest (YYYY-MM-DD)
  - `--end`: End date for backtest (YYYY-MM-DD)

##### Report Commands (New in Milestone 4)
- `report list`: List all saved backtest results
  - `--limit`: Maximum number of results to show
- `report summary <result-id>`: Display quick performance summary
- `report generate`: Generate comprehensive performance report
  - `--result-id`: ID of the backtest result
  - `--format`: Output format (`text`, `csv`, `json`)
  - `-o, --output`: Output file path
- `report export-all <result-id>`: Export all report formats
  - `-o, --output`: Output directory for exports

### Example Output

#### CSV Import
```
✅ Imported 1000 records
⚠️  Skipped 5 duplicates
```

#### Simple Backtest
```
Running simple SMA backtest...
Initializing backtest engine...

✓ Backtest completed successfully!

Results Summary:
Total Return: 0.00
Total Trades: 0
Win Rate: 0.0%
Final Balance: 1,000,000.00

🎉 Strategy shows profit on mock data!
```

## Complete User Journeys

This section provides detailed, step-by-step guides for common tasks.

### Journey 1: First-Time Setup and Quick Test

**Goal**: Get the system running and execute your first backtest

**Prerequisites**: Python 3.11+, Docker installed

**Steps**:
1. **Clone and setup**:
   ```bash
   git clone <repository-url>
   cd Trading-ntrader
   curl -LsSf https://astral.sh/uv/install.sh | sh  # Install UV if needed
   uv sync
   cp .env.example .env
   ```

2. **Start database**:
   ```bash
   docker pull postgres:17
   docker volume create pgdata
   docker run -d --name pgdb \
     -e POSTGRES_USER=ntrader \
     -e POSTGRES_PASSWORD=ntrader_dev_2025 \
     -e POSTGRES_DB=trading_ntrader \
     -p 5432:5432 \
     -v pgdata:/var/lib/postgresql/data \
     postgres:17
   ```

3. **Initialize database**:
   ```bash
   uv run alembic upgrade head
   ```

4. **Import sample data and run backtest**:
   ```bash
   # Import sample data
   uv run python -m src.cli.main data import-csv --file data/sample_AAPL.csv --symbol AAPL

   # Run your first backtest
   uv run python -m src.cli.main backtest run \
     --symbol AAPL --start 2024-01-02 --end 2024-01-02 \
     --fast-period 5 --slow-period 10
   ```

**Expected result**: Successful backtest with results table showing trade statistics.

### Journey 2: Testing Multiple Strategies with YAML Configuration

**Goal**: Compare different trading strategies using the new configuration management system

**Prerequisites**: Journey 1 completed successfully

**Steps**:
1. **Import longer dataset**:
   ```bash
   uv run python -m src.cli.main data import-csv --file data/AAPL_test_2018.csv --symbol AAPL2018
   ```

2. **Discover available strategies**:
   ```bash
   uv run python -m src.cli.main strategy list
   ```

3. **Create configuration files for different strategies**:
   ```bash
   # Create SMA crossover config (fast signals)
   uv run python -m src.cli.main strategy create --type sma_crossover --output sma_fast.yaml

   # Create RSI mean reversion config
   uv run python -m src.cli.main strategy create --type mean_reversion --output mean_rev.yaml

   # Create SMA momentum config
   uv run python -m src.cli.main strategy create --type momentum --output momentum.yaml
   ```

4. **Validate all configurations**:
   ```bash
   uv run python -m src.cli.main strategy validate sma_fast.yaml
   uv run python -m src.cli.main strategy validate mean_rev.yaml
   uv run python -m src.cli.main strategy validate momentum.yaml
   ```

5. **Run backtests with different strategies**:
   ```bash
   # Test strategies with mock data (YAML configs)
   uv run python -m src.cli.main backtest run-config sma_fast.yaml
   uv run python -m src.cli.main backtest run-config mean_rev.yaml
   uv run python -m src.cli.main backtest run-config momentum.yaml

   # Or test with real database data (legacy command)
   uv run python -m src.cli.main backtest run --strategy sma_crossover \
     --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08
   uv run python -m src.cli.main backtest run --strategy mean_reversion \
     --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08
   uv run python -m src.cli.main backtest run --strategy momentum \
     --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08
   ```

6. **Compare results**: Note differences in:
   - Trade frequency and timing
   - Win rate and average trade size
   - Total return and drawdown patterns
   - Strategy-specific behavior (mean reversion vs momentum)

**Learning outcome**: Understanding how different trading approaches (crossover, mean reversion, momentum) perform on the same dataset and market conditions.

### Journey 3: Custom Data Import and Analysis

**Goal**: Import your own CSV data and run comprehensive analysis

**Prerequisites**: Your own historical data in CSV format

**Steps**:
1. **Prepare your CSV file**:
   - Ensure format: `timestamp,open,high,low,close,volume`
   - Timestamp format: `YYYY-MM-DD HH:MM:SS`
   - Verify OHLC relationships are valid
   - Example:
     ```csv
     timestamp,open,high,low,close,volume
     2024-03-01 09:30:00,175.50,176.25,175.00,176.00,1500000
     2024-03-01 09:31:00,176.00,176.50,175.75,176.25,1200000
     ```

2. **Import your data**:
   ```bash
   uv run python -m src.cli.main data import-csv --file /path/to/your/data.csv --symbol YOUR_SYMBOL
   ```

3. **Verify import**:
   ```bash
   uv run python -m src.cli.main backtest list
   ```

4. **Run analysis with different configurations**:
   ```bash
   # Conservative strategy
   uv run python -m src.cli.main backtest run \
     --symbol YOUR_SYMBOL --start START_DATE --end END_DATE \
     --fast-period 20 --slow-period 50

   # Aggressive strategy
   uv run python -m src.cli.main backtest run \
     --symbol YOUR_SYMBOL --start START_DATE --end END_DATE \
     --fast-period 5 --slow-period 12
   ```

**Expected result**: Your own data successfully imported and analyzed with different strategy parameters.

### Journey 4: Multi-Symbol Portfolio Analysis

**Goal**: Import and analyze multiple symbols for portfolio diversification

**Prerequisites**: Multiple CSV files for different symbols

**Steps**:
1. **Import multiple symbols**:
   ```bash
   uv run python -m src.cli.main data import-csv --file data/AAPL_data.csv --symbol AAPL
   uv run python -m src.cli.main data import-csv --file data/MSFT_data.csv --symbol MSFT
   uv run python -m src.cli.main data import-csv --file data/GOOGL_data.csv --symbol GOOGL
   ```

2. **List all available data**:
   ```bash
   uv run python -m src.cli.main backtest list
   ```

3. **Run identical strategy on all symbols**:
   ```bash
   # Run same strategy on each symbol (adjust dates based on available data)
   for symbol in AAPL MSFT GOOGL; do
     echo "Testing $symbol..."
     # Use 'backtest list' to check each symbol's available date range first
     uv run python -m src.cli.main backtest run \
       --symbol $symbol --start 2024-01-02 --end 2024-01-02 \
       --fast-period 10 --slow-period 20
   done
   ```

   **Note**: Each symbol may have different available date ranges. Check with `backtest list` and adjust dates accordingly.

4. **Compare results**: Analyze which symbols performed best with your strategy.

**Learning outcome**: Understanding how the same strategy performs across different assets.

### Journey 5: Performance Testing with Large Datasets

**Goal**: Test system performance with large datasets

**Prerequisites**: Large CSV file (like AAPL_1min.csv)

**Steps**:
1. **Import large dataset** (monitor performance):
   ```bash
   time uv run python -m src.cli.main data import-csv --file data/AAPL_1min.csv --symbol AAPL_LARGE
   ```

2. **Run performance backtest**:
   ```bash
   time uv run python -m src.cli.main backtest run \
     --symbol AAPL_LARGE --start 2019-01-01 --end 2019-12-31 \
     --fast-period 20 --slow-period 50
   ```

3. **Monitor system resources** during execution:
   ```bash
   # In another terminal, monitor memory usage
   watch -n 1 'ps aux | grep python | head -5'
   ```

**Expected result**: Understanding system performance characteristics and resource usage.

### Journey 6: Comprehensive Performance Analysis and Reporting (New in Milestone 4)

**Goal**: Run backtests with full performance analytics and generate detailed reports

**Prerequisites**: Journey 1 completed successfully, sample data imported

**Steps**:
1. **Run backtest with results persistence**:
   ```bash
   # Run backtest - results are automatically saved
   uv run python -m src.cli.main backtest run \
     --strategy mean_reversion \
     --symbol AAPL2018 \
     --start 2018-02-05 \
     --end 2018-02-08

   # Note the result ID from the output
   ```

2. **List saved results**:
   ```bash
   # View all saved backtest results
   uv run python -m src.cli report list

   # View recent 5 results
   uv run python -m src.cli report list --limit 5
   ```

3. **Get quick performance summary**:
   ```bash
   # Replace <result-id> with actual ID from step 1
   uv run python -m src.cli report summary <result-id>
   ```

   This displays:
   - Total return and CAGR
   - Risk-adjusted metrics (Sharpe, Sortino, Calmar ratios)
   - Maximum drawdown
   - Win rate and profit factor

4. **Generate detailed text report**:
   ```bash
   # View comprehensive report in terminal
   uv run python -m src.cli report generate --result-id <result-id> --format text

   # Save to file for later review
   uv run python -m src.cli report generate --result-id <result-id> --format text -o my_report.txt
   ```

5. **Export data for further analysis**:
   ```bash
   # Export to CSV for spreadsheet analysis
   uv run python -m src.cli report generate --result-id <result-id> --format csv -o results.csv

   # Export to JSON for programmatic analysis
   uv run python -m src.cli report generate --result-id <result-id> --format json -o results.json

   # Export all formats at once
   mkdir reports
   uv run python -m src.cli report export-all <result-id> -o reports/
   ```

6. **Compare multiple strategies**:
   ```bash
   # Run different strategies on same data
   uv run python -m src.cli.main backtest run --strategy sma_crossover \
     --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08

   uv run python -m src.cli.main backtest run --strategy mean_reversion \
     --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08

   uv run python -m src.cli.main backtest run --strategy momentum \
     --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08

   # List all results
   uv run python -m src.cli report list

   # Compare summaries
   uv run python -m src.cli report summary <result-id-1>
   uv run python -m src.cli report summary <result-id-2>
   uv run python -m src.cli report summary <result-id-3>
   ```

7. **Analyze performance metrics**:
   - Review Sharpe Ratio (risk-adjusted returns) - higher is better
   - Check Maximum Drawdown - lower absolute value is better
   - Evaluate Win Rate vs Profit Factor
   - Examine trade distribution (wins vs losses)
   - Study equity curve patterns

**Learning outcome**:
- Understanding comprehensive performance metrics
- Comparing strategies using standardized analytics
- Identifying optimal strategy parameters
- Exporting data for deeper analysis in spreadsheets or custom tools

**Expected results**:
- Rich-formatted performance reports with detailed analytics
- Multi-format exports for various use cases
- Clear comparison of strategy performance
- Data-driven insights for strategy optimization

### Journey 7: Interactive Brokers Data Integration (New in Milestone 5)

**Goal**: Fetch real market data from Interactive Brokers TWS/Gateway and use it for backtesting

**Prerequisites**:
- TWS or IB Gateway installed and running
- Paper trading account (DU prefix) recommended
- Valid IBKR credentials in `.env` file

**Steps**:
1. **Set up IBKR connection** (first-time setup):
   ```bash
   # Follow the comprehensive setup guide
   cat docs/IBKR_SETUP.md

   # Quick setup checklist:
   # 1. Enable API in TWS: File → Global Configuration → API → Settings
   # 2. Check "Enable ActiveX and Socket Clients"
   # 3. Set Socket port to 7497 (paper trading)
   # 4. Restart TWS completely
   ```

2. **Configure environment variables**:
   ```bash
   # Verify your .env file has IBKR settings
   cat .env | grep IBKR

   # Required settings:
   # IBKR_HOST=127.0.0.1
   # IBKR_PORT=7497
   # IBKR_CLIENT_ID=10
   # TWS_USERNAME=your_username
   # TWS_PASSWORD=your_password
   # TWS_ACCOUNT=DU1234567
   ```

3. **Test IBKR connection**:
   ```bash
   # Verify connection to TWS
   uv run python -m src.cli.main data connect
   ```

   Expected output:
   ```
   ✅ Successfully connected to Interactive Brokers

   Connection Details
   ┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
   ┃ Property       ┃ Value                   ┃
   ┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
   │ Host           │ 127.0.0.1               │
   │ Port           │ 7497                    │
   │ Account ID     │ DU1234567               │
   │ Server Version │ 176                     │
   └────────────────┴─────────────────────────┘
   ```

4. **Fetch historical data for single instrument**:
   ```bash
   # Fetch AAPL daily data for January 2024
   uv run python -m src.cli.main data fetch \
     --instruments AAPL \
     --start 2024-01-01 \
     --end 2024-01-31 \
     --timeframe DAILY
   ```

   Expected output:
   ```
   ✅ AAPL: 21 bars fetched

   ✅ Successfully fetched 21 bars for 1 instruments

   Fetch Summary
   ┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
   ┃ Property     ┃ Value                       ┃
   ┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
   │ Instruments  │ AAPL                        │
   │ Start Date   │ 2024-01-01                  │
   │ End Date     │ 2024-01-31                  │
   │ Timeframe    │ DAILY                       │
   │ Total Bars   │ 21                          │
   │ Data Location│ ./data/catalog              │
   └──────────────┴─────────────────────────────┘
   ```

5. **Fetch data for multiple instruments**:
   ```bash
   # Fetch data for portfolio of stocks
   uv run python -m src.cli.main data fetch \
     --instruments AAPL,MSFT,GOOGL \
     --start 2024-02-01 \
     --end 2024-02-29 \
     --timeframe DAILY
   ```

6. **Try different timeframes**:
   ```bash
   # Intraday 1-minute bars
   uv run python -m src.cli.main data fetch \
     --instruments AAPL \
     --start 2024-01-02 \
     --end 2024-01-02 \
     --timeframe 1-MINUTE

   # Hourly bars
   uv run python -m src.cli.main data fetch \
     --instruments MSFT \
     --start 2024-01-01 \
     --end 2024-01-05 \
     --timeframe 1-HOUR
   ```

7. **Verify data storage**:
   ```bash
   # Check Parquet files created
   ls -lh ./data/catalog/data/bar/

   # Files are named: {instrument}-{timeframe}-{venue}.parquet
   # Example: AAPL-1-DAY-IDEALPRO.parquet
   ```

8. **Use IBKR data for backtesting** (future integration):
   ```bash
   # Note: Integration with backtest engine coming in future milestone
   # For now, data is stored in Nautilus catalog format
   # Can be loaded with Nautilus BacktestEngine directly
   ```

**Key Features**:
- **Rate Limiting**: Automatic throttling at 45 req/sec (90% of IBKR limit)
- **Multiple Instruments**: Fetch multiple symbols in one command
- **Timeframe Support**: DAILY, 1-HOUR, 5-MINUTE, 1-MINUTE
- **Parquet Storage**: Efficient columnar storage via Nautilus catalog
- **Market Data Types**: DELAYED_FROZEN for paper accounts, REAL_TIME with subscription

**Troubleshooting**:
- **Connection refused**: Check TWS is running and API is enabled
- **Client ID conflict**: Change `IBKR_CLIENT_ID` in `.env` to different number
- **No data returned**: Paper accounts get delayed/frozen data by default
- **Rate limiting errors**: System auto-throttles, but reduce concurrent requests

**Learning outcome**:
- Understanding IBKR API integration and connection management
- Fetching real market data for backtesting
- Working with Nautilus Trader data catalog
- Managing rate limits and API restrictions

**Expected results**:
- Successfully connected to IBKR TWS/Gateway
- Historical data fetched and stored in Parquet format
- Ready to integrate with backtest engine (future milestone)
- Understanding of IBKR data types and limitations

### Troubleshooting Your Journey

If any journey step fails:

1. **Check the error message** carefully - it usually indicates the specific issue
2. **Verify data availability**: Use `uv run python -m src.cli.main backtest list`
3. **Check date ranges**: Ensure your start/end dates are within available data
4. **Database issues**: Restart the database with `docker restart pgdb`
5. **Refer to Troubleshooting section** above for specific error solutions

## Development

### Project Structure

```
src/
├── cli/                 # Command line interface
│   ├── main.py         # Main CLI entry point
│   └── commands/       # CLI commands (data, backtest, report)
├── core/               # Core business logic
│   ├── strategies/     # Trading strategies
│   └── backtest_runner.py  # Backtest engine wrapper
├── models/             # Pydantic data models and schemas
│   └── market_data.py  # Market data models with validation
├── services/           # Business services
│   ├── csv_loader.py   # CSV import service
│   ├── data_service.py # Data fetching and conversion
│   ├── ibkr_client.py  # IBKR client wrapper (Milestone 5)
│   ├── data_fetcher.py # Historical data fetcher (Milestone 5)
│   └── analytics/      # Performance analytics services
├── db/                 # Database layer
│   ├── models.py       # SQLAlchemy ORM models
│   ├── session.py      # Database session management
│   └── migrations/     # Alembic database migrations
├── utils/              # Utilities (mock data, etc.)
└── config.py           # Configuration management

tests/                  # Test files mirror src structure
docs/                   # Documentation (IBKR_SETUP.md, etc.)
data/
├── catalog/            # Nautilus Parquet data catalog (IBKR data)
└── *.csv              # Sample CSV files
```

### Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=src --cov-report=html

# Run specific test file
uv run pytest tests/test_simple_backtest.py -v
```

### Code Quality

```bash
# Format code
uv run ruff format .

# Lint code
uv run ruff check .

# Type checking (if mypy is installed)
uv run mypy .
```

## Configuration

The system uses configuration defaults that can be overridden via environment variables or command line parameters. Default settings include:

- **Starting Balance**: $1,000,000 USD
- **Fast SMA Period**: 20 periods
- **Slow SMA Period**: 50 periods
- **Trade Size**: $1,000,000
- **Mock Data Bars**: 1,000 bars

## Architecture

### Key Components

1. **CLI Interface**: Built with Click for user-friendly command line interaction
2. **Strategy Engine**: SMA crossover strategy using Nautilus Trader indicators
3. **Mock Data Generator**: Creates synthetic OHLCV data with sine wave patterns
4. **Backtest Runner**: Wraps Nautilus Trader's BacktestEngine for simplified usage
5. **Result Display**: Rich console output with formatted metrics

### Technology Stack

- **Core Framework**: Nautilus Trader 1.220.0
- **Database**: PostgreSQL with SQLAlchemy async ORM
- **Data Processing**: pandas for CSV import, Pydantic v2 for validation
- **CLI**: Click with Rich formatting and progress bars
- **Migrations**: Alembic for database schema management
- **Testing**: pytest with comprehensive coverage (69+ tests)
- **Package Management**: UV (exclusive)
- **Code Quality**: Ruff formatter and linter

## Contributing

1. Follow TDD principles - write tests before implementation
2. Use UV for all package management (`uv add`, `uv remove`)
3. Maintain test coverage above 80%
4. Follow the existing code style and patterns
5. Run tests and linting before submitting changes

## Troubleshooting

### Common Issues and Solutions

#### 1. Database Connection Errors

**Error**: `Database not accessible` or connection timeout

**Solutions**:
```bash
# Check if PostgreSQL container is running
docker ps | grep pgdb

# Start the database if stopped
docker start pgdb

# Verify connection with correct credentials
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader -c "SELECT version();"

# Check your .env file has correct DATABASE_URL
cat .env | grep DATABASE_URL
```

#### 2. Timezone Comparison Errors

**Error**: `can't compare offset-naive and offset-aware datetimes`

**Cause**: Mixing timezone-aware and timezone-naive datetime objects

**Solution**: Ensure your CSV timestamps don't include timezone info, or use consistent timezone handling throughout.

#### 3. Data Validation Failures

**Error**: `No data available for symbol AAPL`

**Solutions**:
```bash
# List what symbols are actually in the database
uv run python -m src.cli.main backtest list

# Check if your symbol name matches exactly (case sensitive)
# If you imported as AAPL2018, use that exact name:
uv run python -m src.cli.main backtest run --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08
```

#### 4. Date Range Mismatches

**Error**: `Start date is before available data start` or `End date is after available data end`

**Solutions**:
```bash
# Check what date range is available for your symbol
uv run python -m src.cli.main backtest list

# Use dates within the available range
# Example: If data shows 2018-02-05 to 2018-02-08, use those dates
uv run python -m src.cli.main backtest run --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08
```

#### 5. CSV Import Issues

**Error**: `Invalid CSV format` or import failures

**Solutions**:
```bash
# Check your CSV format matches exactly:
head -3 data/sample_AAPL.csv

# Required format:
# timestamp,open,high,low,close,volume
# 2024-01-02 09:30:00,185.25,186.50,184.75,185.95,2847300

# Verify OHLC relationships: high >= open,close,low and low <= open,close,high
# Check for missing values or extra commas
```

#### 6. Migration Errors

**Error**: Alembic migration failures

**Solutions**:
```bash
# Check current migration status
uv run alembic current

# Apply migrations
uv run alembic upgrade head

# If database is corrupted, recreate it:
docker stop pgdb
docker rm pgdb
# Then follow database setup steps again
```

#### 7. Data List Command Shows "Coming Soon"

**Current Status**: The `data list` command is not yet fully implemented.

**Workaround**: Use `backtest list` to see available data and symbols.

#### 8. Performance Issues with Large CSV Files

**Issue**: Slow import or out of memory errors with large files

**Solutions**:
```bash
# For very large files, consider splitting them:
split -l 10000 large_file.csv smaller_chunk_

# Monitor import progress and memory usage
# The system is optimized for files up to ~100MB
```

#### 9. Date Range Issues and Tips

**Common Problems**: Date range mismatches, timezone confusion, wrong date formats

**How to avoid date range errors**:

1. **Always check available data first**:
   ```bash
   uv run python -m src.cli.main backtest list
   ```

2. **Understand date-only vs datetime inputs**:
   - `--start 2024-01-02` = `2024-01-02 00:00:00+00:00` (midnight UTC)
   - `--start "2024-01-02 09:30:00"` = `2024-01-02 09:30:00+00:00`

3. **Match your date range to available data**:
   ```bash
   # Good - matches sample_AAPL.csv range
   --start 2024-01-02 --end 2024-01-02

   # Good - with specific times for intraday data
   --start "2024-01-02 09:30:00" --end "2024-01-02 10:20:00"

   # Bad - outside available data range
   --start 2024-01-01 --end 2024-12-31
   ```

4. **Sample data ranges quick reference**:
   - **AAPL** (sample_AAPL.csv): 2024-01-02 (09:30 to 10:20)
   - **AAPL2018** (AAPL_test_2018.csv): 2018-02-05 to 2018-02-08
   - **AAPL_LARGE** (AAPL_1min.csv): 2010-01-04 to 2019-12-31

5. **For your own data**: Always import first, then check `backtest list` to see the actual date range before running backtests.

#### 10. IBKR Connection Issues (New in Milestone 5)

**Error**: Connection refused or timeout when connecting to TWS/Gateway

**Solutions**:
```bash
# 1. Verify TWS is running
ps aux | grep -i tws

# 2. Check TWS API settings
# Go to: File → Global Configuration → API → Settings
# Ensure "Enable ActiveX and Socket Clients" is checked
# Verify port is 7497 (paper trading) or 7496 (live)

# 3. Test basic socket connection
uv run python scripts/test_tws_connection.py

# 4. Restart TWS completely after changing settings
# Settings don't take effect until restart

# 5. Check firewall isn't blocking localhost connections
```

**Error**: Client ID already in use (Error 326)

**Solutions**:
```bash
# Change client ID in .env file
# Edit IBKR_CLIENT_ID to a different number (1-999)
# Then retry connection
```

**Error**: No market data permissions

**Solutions**:
- Paper accounts get DELAYED_FROZEN data by default
- For real-time data, subscribe in IBKR Account Management
- Verify `.env` has correct market data type setting

**For detailed IBKR troubleshooting**, see [docs/IBKR_SETUP.md](docs/IBKR_SETUP.md)

### Getting Help

If you encounter issues not covered here:

1. **Check logs**: Look for detailed error messages in the console output
2. **Verify setup**: Ensure all prerequisites are installed and database is running
3. **Test with sample data**: Try the Quick Start Guide first to verify your setup
4. **Check database**: Use `backtest list` to see what data is actually available

## License

[Add your license information here]

## Roadmap

### Completed ✅
- [x] **Milestone 1**: Basic SMA crossover strategy and mock data testing
- [x] **Milestone 2**: CSV data import functionality and database integration
  - [x] PostgreSQL database integration
  - [x] Data validation and deduplication
  - [x] CLI data management commands
  - [x] Real data backtesting
- [x] **Milestone 3**: Multi-strategy system with configuration management
  - [x] RSI Mean Reversion strategy implementation
  - [x] SMA Momentum strategy implementation
  - [x] Strategy factory for dynamic loading
  - [x] YAML configuration support
  - [x] Strategy management CLI commands
  - [x] Strategy discovery and validation
- [x] **Milestone 4**: Performance Metrics & Basic Reports
  - [x] Performance metrics engine with Nautilus Trader analytics
  - [x] Custom statistics (MaxDrawdown, CalmarRatio, WinRate, Expectancy)
  - [x] Enhanced trade tracking with Nautilus Position integration
  - [x] Portfolio analytics service with real-time metrics
  - [x] Text report generation with Rich formatting
  - [x] CSV/JSON export system with precision preservation
  - [x] CLI report commands (summary, generate, list, export-all)
  - [x] Results persistence and retrieval system
  - [x] Comprehensive test suite (106 tests passing)
- [x] **Milestone 5**: Interactive Brokers Integration
  - [x] IBKR client wrapper with Nautilus HistoricInteractiveBrokersClient
  - [x] Connection management and health checks
  - [x] Rate limiting (45 req/sec with sliding window algorithm)
  - [x] Historical data fetching (bars, instruments, ticks)
  - [x] Multi-instrument support with progress tracking
  - [x] CLI commands (data connect, data fetch)
  - [x] Parquet catalog integration for data storage
  - [x] Comprehensive IBKR setup documentation
  - [x] Full test suite (38 IBKR-specific tests passing)

### In Progress 🚧
- [ ] Complete data list command implementation (currently shows "coming soon")
- [ ] IBKR data integration with backtest engine
- [ ] Advanced visualization and charting

### Planned 📋
- [ ] TimescaleDB optimization for large datasets
- [ ] Web-based dashboard
- [ ] Real-time data streaming
- [ ] Advanced risk management features
- [ ] Portfolio optimization tools
- [ ] Walk-forward analysis
- [ ] Monte Carlo simulation
