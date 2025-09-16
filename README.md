# NTrader - Nautilus Trader Backtesting System

A production-grade algorithmic trading backtesting system built with the Nautilus Trader framework. This system allows you to backtest trading strategies with historical data and Interactive Brokers integration.

## Features

- 🚀 **SMA Crossover Strategy**: Built-in Simple Moving Average crossover strategy
- 📊 **Mock Data Generation**: Synthetic data with predictable patterns for testing
- 🖥️ **CLI Interface**: Easy-to-use command line interface
- 📈 **Performance Metrics**: Win rate, total return, trade statistics
- ⚡ **Fast Execution**: Built on Nautilus Trader's high-performance engine
- 🧪 **Test Coverage**: Comprehensive test suite with 16+ tests

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

### Usage

#### Basic SMA Backtest

Run a simple SMA crossover backtest with default parameters:

```bash
uv run python -m src.cli.main run-simple
```

#### Custom Parameters

Customize the backtest with your own parameters:

```bash
uv run python -m src.cli.main run-simple \
  --fast-period 5 \
  --slow-period 10 \
  --trade-size 100000 \
  --bars 200
```

#### Command Options

- `--strategy`: Trading strategy to use (default: `sma`)
- `--data`: Data source to use (default: `mock`)
- `--fast-period`: Fast SMA period (default: from config)
- `--slow-period`: Slow SMA period (default: from config)
- `--trade-size`: Trade size in base currency (default: from config)
- `--bars`: Number of mock data bars to generate (default: from config)

### Example Output

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

## Development

### Project Structure

```
src/
├── cli/                 # Command line interface
│   ├── main.py         # Main CLI entry point
│   └── commands/       # CLI commands
├── core/               # Core business logic
│   ├── strategies/     # Trading strategies
│   └── backtest_runner.py  # Backtest engine wrapper
├── models/             # Data models and schemas
├── utils/              # Utilities (mock data, etc.)
└── config.py           # Configuration management

tests/                  # Test files
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
- **CLI**: Click with Rich formatting
- **Data Validation**: Pydantic v2
- **Testing**: pytest with comprehensive coverage
- **Package Management**: UV (exclusive)
- **Code Quality**: Ruff formatter and linter

## Contributing

1. Follow TDD principles - write tests before implementation
2. Use UV for all package management (`uv add`, `uv remove`)
3. Maintain test coverage above 80%
4. Follow the existing code style and patterns
5. Run tests and linting before submitting changes

## License

[Add your license information here]

## Roadmap

- [ ] Interactive Brokers data integration
- [ ] Additional trading strategies (Mean Reversion, Momentum)
- [ ] Performance analytics and reporting
- [ ] CSV data import functionality
- [ ] Web-based dashboard
