# NTrader

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-1040%20passing-brightgreen.svg)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Production-grade algorithmic trading backtesting system built with Nautilus Trader.

## Overview

NTrader is a comprehensive backtesting platform designed for traders, quants, and developers who need reliable strategy testing with real market data. Built on the high-performance Nautilus Trader engine, it provides:

- **Extensible strategy framework** with example strategies and custom strategy support
- **Multi-source market data** — Interactive Brokers and Kraken cryptocurrency exchange
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
- **FirstRate bulk import** — multi-ticker, multi-timeframe CSV bundles into named Parquet catalogs, with dry-run preview and supplementary dividend/split data
- Single-file CSV import directly to Parquet catalog
- Interactive Brokers historical data fetching
- Kraken historical crypto data fetching (BTC/USD, ETH/USD, etc.)
- Multi-source support: FirstRate, CSV, IBKR, Kraken
- **Named catalogs** — isolate datasets (e.g. `e2e-test`, `firstrate-stocks`) under one base path
- **Data explorer** — browse imported tickers, charts, statistics, and supplementary data in the web UI
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
- Kraken account with API keys (optional, for crypto data)

### Installation

```bash
# Clone the repository
git clone https://github.com/allaydesai/Trading-ntrader.git
cd Trading-ntrader

# Install UV package manager (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Install the git pre-commit hook (structural import gate, once per clone)
make install-hooks

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

### Import Commands (FirstRate bulk import)

The top-level `import` command ingests a FirstRate data directory (many tickers ×
many timeframes) into a **named** Parquet catalog. It also loads company profiles
and any supplementary dividend/split files it discovers alongside the bars.

| Command | Description |
|---------|-------------|
| `import --format firstrate --catalog <name> --dry-run <dir>` | Preview what would be imported (no writes) — ticker/file counts and estimated size |
| `import --format firstrate --catalog <name> --asset-class stock --timeframe daily <dir>` | Import a FirstRate directory into a named catalog |
| `import ... --timeframe daily,hourly,1min,5min <dir>` | Import multiple timeframes in one pass |
| `import ... --dividends-dir <dir> --splits-dir <dir>` | Override auto-discovery of supplementary data |

Key options:

- `--format firstrate` (required) — only FirstRate is supported today
- `--catalog <name>` (required) — target catalog under `CATALOG_BASE_PATH`
- `--asset-class` — `etf` (import default) · `stock` · `futures` · `fx` · `crypto` · `index` (`--dry-run` defaults to `stock`)
- `--timeframe` — comma-separated: `daily`, `hourly`, `minute`/`1min`, `5min` (default `daily`)
- `--dividends-dir` / `--splits-dir` — explicit supplementary sources (auto-discovered from the source tree when omitted)
- `--dry-run` — scan and report only; writes nothing to the catalog or DB

Exit codes: `0` success · `1` partial (some bar files failed) · `2` fatal (bad path, DB not configured, missing profiles). Supplementary (dividend/split) failures are reported but never change the exit code.

> A `company_profiles.csv` must be loadable for the target catalog. The importer looks for it inside the source directory and its parent. Without it (e.g. the ETF bundle, which ships none) the import aborts with exit code 2.

### Data Commands

| Command | Description |
|---------|-------------|
| `data import --csv <file> --symbol <SYM> --venue <VENUE>` | Import a single CSV file to Parquet catalog |
| `data list` | List all data in the catalog (reads `NAUTILUS_PATH`) |
| `data list --catalog <name>` | List data in a specific named catalog (overrides `NAUTILUS_PATH`) |
| `data check --symbol <SYM>` | Check data availability |
| `data check --symbol <SYM> --catalog <name>` | Check availability in a specific named catalog |
| `data check --symbol <SYM> --start <date> --end <date>` | Detect data gaps |
| `data connect` | Test IBKR connection |
| `data fetch --instruments <SYM> --start <date> --end <date>` | Fetch data from IBKR |

### Backtest Commands

| Command | Description |
|---------|-------------|
| `backtest run --strategy <type> --symbol <SYM> [--data-source <src>] ...` | Run a backtest (sources: catalog, ibkr, kraken, mock) |
| `backtest run --strategy <type> --symbol <SYM> --catalog <name> ...` | Run against a named FirstRate catalog (resolves the ticker via the import DB; overrides the default `NAUTILUS_PATH`) |
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

### Catalog Commands

| Command | Description |
|---------|-------------|
| `catalog restamp-venues --catalog <name> --dry-run` | Preview moving a named catalog's bar partitions onto their IBKR-corrected venues (writes a rollback-manifest plan JSON, moves nothing) |
| `catalog restamp-venues --catalog <name>` | Apply a venue re-stamp plan (partition dirs + parquet footer metadata) |
| `catalog validate-fmp` | Spot-check the default named catalog's daily ETF closes against FMP's independent historical prices for 8 liquid ETFs (SPY, QQQ, IWM, GLD, XLF, TLT, VTI, ARKK), last 12 months |
| `catalog validate-fmp --catalog <name> --tickers SPY,QQQ` | Validate a specific catalog and ticker subset |
| `catalog validate-fmp --months 6 --mean-tol-pct 0.2 --max-tol-pct 2` | Override the lookback window and deviation tolerances |

> `validate-fmp` requires `FMP_API_KEY` (see Configuration below). It prints a per-ticker Rich table plus a JSON evidence file under `logs/fmp-validation/`, and exits non-zero if any ticker's mean/max deviation or matched-day coverage breaches tolerance.

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

### 3. Bulk Import FirstRate Data

FirstRate ships per-asset-class bundles laid out by timeframe (e.g.
`~/Data/Stocks/Stocks_1day`, `Stocks_1hour`, `Stocks_1min`, `Stocks_5min`) with a
`company_profiles.csv` at the bundle root. Import into a **named catalog** so
datasets stay isolated.

```bash
# 0. Configure catalog env vars in .env (see Configuration below)
#    CATALOG_BASE_PATH=./data/catalogs
#    DEFAULT_CATALOG_NAME=firstrate-stocks
#    NAUTILUS_PATH=./data/catalogs/firstrate-stocks

# 1. Dry-run first — preview tickers, file counts, and estimated catalog size
uv run python -m src.cli.main import \
  --format firstrate \
  --catalog firstrate-stocks \
  --asset-class stock \
  --dry-run \
  ~/Data/Stocks/Stocks_1day

# 2. Import the timeframes you need (one or many)
uv run python -m src.cli.main import \
  --format firstrate \
  --catalog firstrate-stocks \
  --asset-class stock \
  --timeframe daily \
  ~/Data/Stocks/Stocks_1day

# 3. Verify — list what's in the named catalog
uv run python -m src.cli.main data list --catalog firstrate-stocks

# 4. Run a backtest against the named catalog
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol AAPL \
  --start 2023-01-01 --end 2023-12-31 \
  --timeframe 1-day \
  --catalog firstrate-stocks

# 5. Explore in the web UI (see "Use the Web Dashboard" below) → http://127.0.0.1:8000/explorer
```

**Importing a subset of tickers.** The importer ingests *every* ticker in the
source directory. To import a few, build a symlink tree with a matching subset
`company_profiles.csv` and point `import` at that tree. Loading the full
`company_profiles.csv` writes thousands of zero-bar rows to the DB — prefer a
subset CSV.

**Supplementary data (dividends & splits).** FirstRate dividend
(`{TICKER}_divs.txt`) and split (`{TICKER}.txt`) files are auto-discovered next to
the bars and persisted per ticker. Override with `--dividends-dir` / `--splits-dir`
if they live elsewhere. Supplementary failures are reported but never fail the import.

> **Tip:** After changing `NAUTILUS_PATH`, restart the web server — `--reload` does
> not pick up env changes.

### 4. Fetch Data from Interactive Brokers

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

For detailed IBKR setup, see [docs/setup/IBKR_SETUP.md](docs/setup/IBKR_SETUP.md).

### 5. Fetch Crypto Data from Kraken

```bash
# Run a backtest with Kraken crypto data
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover \
  --symbol BTC/USD \
  --start 2026-01-01 \
  --end 2026-01-31 \
  --timeframe 1-hour \
  --data-source kraken
```

### 6. Compare Strategy Performance

```bash
# Run multiple strategies on same data
uv run python -m src.cli.main backtest run \
  --strategy sma_crossover --symbol AAPL --start 2024-01-02 --end 2024-01-02

uv run python -m src.cli.main backtest run \
  --strategy momentum --symbol AAPL --start 2024-01-02 --end 2024-01-02

# View history and find best performers
uv run python -m src.cli.main backtest history --sort sharpe

# Compare specific runs
uv run python -m src.cli.main backtest compare <uuid1> <uuid2>
```

### 7. Use the Web Dashboard

```bash
# First run (or after changing styles): build CSS, then serve
make dev

# Subsequent runs (CSS already built): just serve
make web

# (make web is equivalent to: uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000)

# Open in browser:
#   http://127.0.0.1:8000              — dashboard / backtest results
#   http://127.0.0.1:8000/explorer     — data explorer (tickers, charts, stats, dividends/splits)
#   http://127.0.0.1:8000/backtests/run — configure and run a backtest
```

The **data explorer** browses the catalog at `NAUTILUS_PATH`: pick a ticker to see
its price chart (switchable timeframes), bar statistics and date coverage, and any
imported dividend/split data. It pre-selects `DEFAULT_CATALOG_NAME` when set.

**Run a backtest from the UI.** Open `/backtests/run` — the form pre-fills the
**Catalog** field from `DEFAULT_CATALOG_NAME`, so a run against the default catalog
works straight from the nav. To target a different catalog, start from the explorer
(pick a ticker → *Run Backtest*) or pass `?catalog=<name>` in the URL. Selecting a
non-catalog data source (mock / ibkr / kraken) ignores the catalog field.

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
| `IBKR_CLIENT_ID` | Client ID for historical data fetch (rotates `1`–`6` on connect retry) | `1` |
| `IBKR_LIVE_CLIENT_ID` | Client ID for the live trading session; reconcile uses `+ 1`. Must differ from `IBKR_CLIENT_ID` | `10` |
| `TWS_USERNAME` | IBKR username | - |
| `TWS_PASSWORD` | IBKR password | - |
| `TWS_ACCOUNT` | IBKR account ID | - |
| `DEFAULT_BALANCE` | Starting balance for backtests | `1000000` |
| `TRADE_SIZE` | Default trade size | `1000000` |
| `CATALOG_BASE_PATH` | Base dir holding named catalog subdirectories (import target) | - |
| `DEFAULT_CATALOG_NAME` | Catalog the explorer pre-selects | - |
| `NAUTILUS_PATH` | Catalog path read by `data list`, backtests, and the explorer | `./data/catalog` |
| `FIRSTRATE_SOURCE_PATH` | Default FirstRate source directory | - |
| `FIRSTRATE_CATALOG_NAME` | Default catalog name for FirstRate imports | `firstrate-etf` |
| `FMP_API_KEY` | Financial Modeling Prep API key (metadata import + `catalog validate-fmp`) | - |
| `FMP_RATE_LIMIT` | Max FMP requests/min | `300` |
| `KRAKEN_API_KEY` | Kraken API key | - |
| `KRAKEN_API_SECRET` | Kraken API secret (base64) | - |
| `KRAKEN_RATE_LIMIT` | Kraken requests/sec | `10` |
| `KRAKEN_DEFAULT_MAKER_FEE` | Maker fee rate | `0.0016` |
| `KRAKEN_DEFAULT_TAKER_FEE` | Taker fee rate | `0.0026` |

> **Catalog paths.** `import --catalog <name>` writes to `<CATALOG_BASE_PATH>/<name>`.
> Reading tools (`data list`, backtests, the explorer) use `NAUTILUS_PATH`, so set it
> to the same `<CATALOG_BASE_PATH>/<name>` to read back what you imported. Restart the
> web server after changing `NAUTILUS_PATH` (`--reload` ignores env changes).

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
├── services/         # Business services (IBKR, Kraken, analytics)
├── db/               # Database models and migrations
└── utils/            # Utilities

tests/
├── unit/             # Fast unit tests (315 tests)
├── component/        # Component tests (421 tests)
├── integration/      # Integration tests (135 tests)
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
5. See [docs/setup/IBKR_SETUP.md](docs/setup/IBKR_SETUP.md) for detailed troubleshooting

### Kraken Data Issues

1. Verify API keys are set: `KRAKEN_API_KEY` and `KRAKEN_API_SECRET`
2. Check symbol format — use standard pairs like `BTC/USD`, not Kraken-native formats
3. Rate limit errors — reduce `KRAKEN_RATE_LIMIT` (default: 10 req/s)
4. Missing data — Kraken Charts API may have gaps for low-volume pairs

### FirstRate Import Issues

1. **Exit code 2, "Instrument profiles not loaded"** — the importer could not find a
   `company_profiles.csv` in the source directory or its parent. Stock bundles ship
   one; ETF bundles do not. Supply a (subset) profiles CSV alongside the data.
2. **"Database not configured"** — set `DATABASE_URL`; import persists instrument
   metadata and supplementary data to PostgreSQL.
3. **Imported data not showing up** — `data list` and the explorer read `NAUTILUS_PATH`,
   not `--catalog`. Point `NAUTILUS_PATH` at `<CATALOG_BASE_PATH>/<catalog-name>` and
   restart the web server.
4. **Too many zero-bar tickers** — a full `company_profiles.csv` loads every row. Use a
   subset symlink tree with a matching subset profiles CSV to import only what you need.
5. **Always dry-run first** — `--dry-run` reports ticker/file counts, schema mismatches,
   and estimated catalog size without writing anything.

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
