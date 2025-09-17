# NTrader - Nautilus Trader Backtesting System

A production-grade algorithmic trading backtesting system built with the Nautilus Trader framework. This system allows you to backtest trading strategies with historical data and Interactive Brokers integration.

## Features

- 🚀 **SMA Crossover Strategy**: Built-in Simple Moving Average crossover strategy
- 📊 **Mock Data Generation**: Synthetic data with predictable patterns for testing
- 📈 **CSV Data Import**: Import real market data from CSV files with validation
- 🗄️ **Database Storage**: PostgreSQL with optimized time-series storage
- 🖥️ **CLI Interface**: Easy-to-use command line interface with data management
- 📈 **Performance Metrics**: Win rate, total return, trade statistics
- ⚡ **Fast Execution**: Built on Nautilus Trader's high-performance engine
- 🧪 **Test Coverage**: Comprehensive test suite with 69+ tests
- 🔄 **Real Data Backtesting**: Run backtests on imported historical data

## Quick Start

### Prerequisites

- Python 3.11+
- UV package manager

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
  --strategy sma \
  --symbol AAPL \
  --start "2024-01-02 09:30:00" \
  --end "2024-01-02 10:20:00" \
  --fast-period 5 \
  --slow-period 10
```

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

This project uses PostgreSQL with TimescaleDB for time-series data storage. Follow these steps to set up the database:

#### Prerequisites

- Docker and Docker Desktop
- PostgreSQL client tools

#### Setup Steps

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

4. **Install PostgreSQL client** (if not already installed):
```bash
# Ubuntu/WSL2
sudo apt update && sudo apt install -y postgresql-client

# macOS
brew install postgresql
```

5. **Verify database connection**:
```bash
# Test connection
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader -c "SELECT version();"
```

#### Database Configuration

The database connection is configured through environment variables in your `.env` file:

```env
DATABASE_URL=postgresql://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
DATABASE_POOL_TIMEOUT=30
```

#### Container Management

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
- Available trading strategies (currently SMA crossover)
- All symbols with imported data
- Date ranges for each symbol

##### Basic SMA Backtest (Mock Data)

Run a simple SMA crossover backtest with mock data:

```bash
uv run python -m src.cli.main run-simple
```

##### Real Data Backtest

Run backtests on imported historical data:

```bash
# Run backtest with real data (using sample data range)
uv run python -m src.cli.main backtest run \
  --strategy sma \
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

#### Command Options

##### Data Commands
- `data import-csv --file <path> --symbol <symbol>`: Import CSV market data
- `data list --symbol <symbol>`: List available data for a symbol

##### Backtest Commands
- `backtest list`: List available strategies and imported data
- `run-simple`: Run simple backtest with mock data
- `backtest run`: Run backtest with real data
  - `--strategy`: Trading strategy to use (default: `sma`)
  - `--symbol`: Trading symbol (required for real data)
  - `--start`: Start date for backtest (YYYY-MM-DD)
  - `--end`: End date for backtest (YYYY-MM-DD)
  - `--fast-period`: Fast SMA period (default: 10)
  - `--slow-period`: Slow SMA period (default: 20)
  - `--trade-size`: Trade size in base currency (default: 1,000,000)

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

### Journey 2: Testing Different Strategies and Timeframes

**Goal**: Compare different SMA configurations on longer datasets

**Prerequisites**: Journey 1 completed successfully

**Steps**:
1. **Import longer dataset**:
   ```bash
   uv run python -m src.cli.main data import-csv --file data/AAPL_test_2018.csv --symbol AAPL2018
   ```

2. **Test fast SMA strategy** (quick signals):
   ```bash
   uv run python -m src.cli.main backtest run \
     --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08 \
     --fast-period 3 --slow-period 7
   ```

3. **Test slow SMA strategy** (trend following):
   ```bash
   uv run python -m src.cli.main backtest run \
     --symbol AAPL2018 --start 2018-02-05 --end 2018-02-08 \
     --fast-period 15 --slow-period 30
   ```

4. **Compare results**: Note differences in trade frequency, win rate, and total return.

**Learning outcome**: Understanding how SMA periods affect trading behavior and profitability.

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
│   └── commands/       # CLI commands (data, backtest)
├── core/               # Core business logic
│   ├── strategies/     # Trading strategies
│   └── backtest_runner.py  # Backtest engine wrapper
├── models/             # Pydantic data models and schemas
│   └── market_data.py  # Market data models with validation
├── services/           # Business services
│   ├── csv_loader.py   # CSV import service
│   └── data_service.py # Data fetching and conversion
├── db/                 # Database layer
│   ├── models.py       # SQLAlchemy ORM models
│   ├── session.py      # Database session management
│   └── migrations/     # Alembic database migrations
├── utils/              # Utilities (mock data, etc.)
└── config.py           # Configuration management

tests/                  # Test files mirror src structure
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
- [x] CSV data import functionality
- [x] PostgreSQL database integration
- [x] Data validation and deduplication
- [x] CLI data management commands
- [x] Real data backtesting

### In Progress 🚧
- [ ] Complete data list command implementation (currently shows "coming soon")
- [ ] Interactive Brokers data integration
- [ ] Additional trading strategies (Mean Reversion, Momentum)
- [ ] Enhanced performance analytics and reporting

### Planned 📋
- [ ] TimescaleDB optimization for large datasets
- [ ] Web-based dashboard
- [ ] Real-time data streaming
- [ ] Advanced risk management features
