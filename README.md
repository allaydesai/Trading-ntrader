# NTrader

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-715%20passing-brightgreen.svg)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Production-grade algorithmic trading backtesting system built with Nautilus Trader.

## Overview

NTrader is a comprehensive backtesting platform designed for traders, quants, and developers who need reliable strategy testing with real market data. Built on the high-performance Nautilus Trader engine, it provides:

- **Extensible strategy framework** with example strategies and custom strategy support
- **Interactive Brokers integration** for fetching real market data
- **Parquet-based data catalog** for fast, efficient storage without database overhead for market data
- **PostgreSQL metadata storage** for tracking backtest history and performance metrics
- **Web dashboard** for visualizing results
- **Comprehensive CLI** for all operations

## Key Features

### Trading Strategies
- **Built-in examples:**
  - SMA Crossover (classic moving average crossover)
  - SMA Momentum (golden/death cross detection)
- **Custom strategy support** via `src/core/strategies/custom/` directory
- Auto-discovery of strategies using `@register_strategy` decorator

### Data Management
- CSV import directly to Parquet catalog
- Interactive Brokers historical data fetching
- Auto-fetch missing data when IBKR is connected
- Data inspection and gap detection commands

### Performance Analytics
- Risk metrics: Sharpe, Sortino, Calmar ratios
- Trade statistics: Win rate, profit factor, expectancy
- Maximum drawdown tracking
- Multi-format reports (text, CSV, JSON)

### Web Dashboard
- Overview with key metrics
- Paginated backtest list with filtering
- Dark theme with responsive design
- HTMX-powered dynamic updates

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 16+ (for metadata storage)
- Interactive Brokers TWS/Gateway (optional, for live data)

### Installation

```bash
# Clone the repository
git clone https://github.com/allaydesai/Trading-ntrader.git
cd Trading-ntrader

# Install UV package manager (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your database credentials
```

### Database Setup

```bash
# Start PostgreSQL (using Docker)
docker run -d --name pgdb \
  -e POSTGRES_USER=ntrader \
  -e POSTGRES_PASSWORD=ntrader_dev_2025 \
  -e POSTGRES_DB=trading_ntrader \
  -p 5432:5432 \
  postgres:17

# Run database migrations
uv run alembic upgrade head
```

### Run Your First Backtest

```bash
# Import sample data
uv run python -m src.cli.main data import \
  --csv data/sample_AAPL.csv \
  --symbol AAPL \
  --venue NASDAQ

# Run a backtest
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start "2024-01-02 09:30:00" \
  --end "2024-01-02 10:20:00" \
  --fast-period 5 \
  --slow-period 10
```

## CLI Commands Reference

### Strategy Commands

| Command | Description |
|---------|-------------|
| `strategy list` | List all available trading strategies |
| `strategy create --type <type> --output <file>` | Create a strategy config template |
| `strategy validate <config.yaml>` | Validate a strategy configuration |

### Data Commands

| Command | Description |
|---------|-------------|
| `data import --csv <file> --symbol <SYM> --venue <VENUE>` | Import CSV to Parquet catalog |
| `data list` | List all data in the catalog |
| `data check --symbol <SYM>` | Check data availability |
| `data check --symbol <SYM> --start <date> --end <date>` | Detect data gaps |
| `data connect` | Test IBKR connection |
| `data fetch --instruments <SYM> --start <date> --end <date>` | Fetch data from IBKR |

### Backtest Commands

| Command | Description |
|---------|-------------|
| `backtest run --strategy <type> --symbol <SYM> ...` | Run a backtest |
| `backtest run <config.yaml>` | Run backtest with YAML config |
| `backtest history` | View recent backtest executions |
| `backtest history --sort sharpe` | Sort by Sharpe ratio |
| `backtest show <run-id>` | View complete backtest details |
| `backtest compare <id1> <id2>` | Compare backtests side-by-side |
| `backtest reproduce <run-id>` | Re-run a previous backtest |

### Report Commands

| Command | Description |
|---------|-------------|
| `report list` | List all saved results |
| `report summary <result-id>` | Quick performance summary |
| `report generate --result-id <id> --format <fmt>` | Generate detailed report |

## Common Workflows

### 1. Quick Test with Sample Data

```bash
# Import and test with included sample data
uv run python -m src.cli.main data import \
  --csv data/sample_AAPL.csv --symbol AAPL --venue NASDAQ

uv run python -m src.cli.main backtest run \
  --strategy sma_crossover --symbol AAPL \
  --start 2024-01-02 --end 2024-01-02
```

### 2. Import Your Own CSV Data

```bash
# CSV format: timestamp,open,high,low,close,volume
uv run python -m src.cli.main data import \
  --csv /path/to/your/data.csv \
  --symbol YOUR_SYMBOL \
  --venue YOUR_VENUE

# Verify import
uv run python -m src.cli.main data list
```

### 3. Fetch Data from Interactive Brokers

```bash
# Test connection first
uv run python -m src.cli.main data connect

# Fetch historical data
uv run python -m src.cli.main data fetch \
  --instruments AAPL,MSFT \
  --start 2024-01-01 \
  --end 2024-01-31 \
  --timeframe DAILY
```

For detailed IBKR setup, see [docs/IBKR_SETUP.md](docs/IBKR_SETUP.md).

### 4. Compare Strategy Performance

```bash
# Run multiple strategies on same data
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover --symbol AAPL --start 2024-01-02 --end 2024-01-02

uv run python -m src.cli.main backtest run \
  --strategy mean_reversion --symbol AAPL --start 2024-01-02 --end 2024-01-02

# View history and find best performers
uv run python -m src.cli.main backtest history --sort sharpe

# Compare specific runs
uv run python -m src.cli.main backtest compare <uuid1> <uuid2>
```

### 5. Use the Web Dashboard

```bash
# Build CSS (first time only)
./scripts/build-css.sh

# Start the web server
uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000

# Open in browser: http://127.0.0.1:8000
```

## Available Strategies

### Built-in Example Strategies

| Strategy | Type | Key Parameters | Description |
|----------|------|----------------|-------------|
| `sma_crossover` | Trend Following | fast_period, slow_period | Classic moving average crossover |
| `momentum` | Momentum | fast_period, slow_period | Golden/death cross detection |

### Adding Custom Strategies

Custom strategies can be placed in `src/core/strategies/custom/` directory.
They will be auto-discovered if they use the `@register_strategy` decorator.

#### Using Git Submodule (Recommended for Private Strategies)

```bash
git submodule add git@github.com:username/private-strategies.git src/core/strategies/custom
git submodule update --init --recursive
```

#### Creating a Custom Strategy

```python
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from src.core.strategy_registry import register_strategy, StrategyRegistry

class MyStrategyConfig(StrategyConfig):
    instrument_id: InstrumentId
    bar_type: BarType
    my_param: int = 10

@register_strategy(
    name="my_strategy",
    description="My custom trading strategy",
    aliases=["mystrat"],
)
class MyStrategy(Strategy):
    def __init__(self, config: MyStrategyConfig):
        super().__init__(config)
        # ... strategy implementation

# Register config and param model
StrategyRegistry.set_config("my_strategy", MyStrategyConfig)
```

After placing the file in `src/core/strategies/custom/`, the strategy will be auto-discovered and available for use.

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `IBKR_HOST` | IBKR TWS/Gateway host | `127.0.0.1` |
| `IBKR_PORT` | IBKR port (7497=paper, 7496=live) | `7497` |
| `IBKR_CLIENT_ID` | Client ID for IBKR connection | `1` |
| `TWS_USERNAME` | IBKR username | - |
| `TWS_PASSWORD` | IBKR password | - |
| `TWS_ACCOUNT` | IBKR account ID | - |
| `DEFAULT_BALANCE` | Starting balance for backtests | `1000000` |
| `TRADE_SIZE` | Default trade size | `1000000` |

### YAML Strategy Configuration

Create strategy configs with `strategy create`:

```yaml
# my_strategy.yaml
strategy_type: sma_crossover
instrument_id: AAPL.NASDAQ
start_date: "2024-01-01"
end_date: "2024-12-31"
parameters:
  fast_period: 10
  slow_period: 20
  trade_size: 100000
```

## Development

### Project Structure

```
src/
├── api/              # FastAPI web application
├── cli/              # Command line interface
├── core/             # Core business logic
│   └── strategies/   # Trading strategy implementations
├── models/           # Pydantic data models
├── services/         # Business services (IBKR, analytics)
├── db/               # Database models and migrations
└── utils/            # Utilities

tests/
├── unit/             # Fast unit tests (141 tests)
├── component/        # Component tests (456 tests)
├── integration/      # Integration tests (112 tests)
└── e2e/              # End-to-end tests
```

### Running Tests

```bash
# Fast unit tests (run constantly during development)
make test-unit

# Component tests (run before commits)
make test-component

# Integration tests (run in CI/CD)
make test-integration

# All tests
make test-all

# With coverage
make test-coverage
```

### Code Quality

```bash
# Format code
uv run ruff format .

# Lint code
uv run ruff check .

# Type checking
uv run mypy .
```

## Troubleshooting

### Database Connection Errors

```bash
# Check if PostgreSQL is running
docker ps | grep pgdb

# Start if stopped
docker start pgdb

# Verify connection
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader -c "SELECT 1;"
```

### Data Not Found

```bash
# Check available data
uv run python -m src.cli.main data list

# Verify symbol and date range match your imported data
```

### IBKR Connection Issues

1. Ensure TWS/Gateway is running
2. Enable API: File → Global Configuration → API → Settings
3. Check "Enable ActiveX and Socket Clients"
4. Verify port matches your `.env` configuration
5. See [docs/IBKR_SETUP.md](docs/IBKR_SETUP.md) for detailed troubleshooting

### Date Range Errors

Always check available data ranges before running backtests:

```bash
uv run python -m src.cli.main data list
```

Use dates within the available range shown for each symbol.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

Built with [Nautilus Trader](https://nautilustrader.io/) | [Documentation](docs/) | [Issues](https://github.com/allaydesai/Trading-ntrader/issues)
