# NTrader Development Guide

## Prerequisites

- **Python** >= 3.11
- **UV** package manager (replaces pip/poetry)
- **PostgreSQL 16** with TimescaleDB extension
- **Docker** and Docker Compose (for database and IB Gateway)
- **Node.js** (for Tailwind CSS build, first time only)

## Quick Start

```bash
# 1. Clone and setup
git clone <repo-url>
cd Trading-ntrader
git submodule update --init --recursive  # Custom strategies

# 2. Install dependencies
uv sync

# 3. Start infrastructure
docker compose up -d postgres redis

# 4. Run database migrations
uv run alembic upgrade head

# 5. Build CSS (first time only)
./scripts/build-css.sh

# 6. Start web UI
uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000

# 7. Or use CLI
uv run python -m src.cli.main --help
```

## Environment Configuration

Copy `.env.example` to `.env` and configure:

```bash
# Database
DATABASE_URL=postgresql://ntrader:ntrader@localhost:5432/ntrader_dev

# Interactive Brokers (optional)
IBKR_HOST=127.0.0.1
IBKR_PORT=4002
IBKR_CLIENT_ID=1
TWS_USERNAME=...
TWS_PASSWORD=...
TWS_ACCOUNT=...

# Kraken (optional)
KRAKEN_API_KEY=...
KRAKEN_API_SECRET=...

# Application
LOG_LEVEL=INFO
NAUTILUS_PATH=./data/catalog
```

Settings classes: `IBKRSettings` and `KrakenSettings` in `src/config.py` (Pydantic Settings, env-var backed).

## Dependency Management

**UV is the only package manager.** Never edit `pyproject.toml` dependencies directly.

```bash
uv add <package>           # Production dependency
uv add --dev <package>     # Dev dependency
uv remove <package>        # Remove
uv sync                    # Sync lockfile to environment
```

## Code Quality

Run before every commit (enforced by `.claude/hooks/`):

```bash
make format      # ruff format .
make lint        # ruff check . (rules: E, F, I)
make typecheck   # mypy src/core src/services
```

Ruff config in `pyproject.toml`: line-length=100, excludes tests_archive, _bmad, alembic, .venv.

## Testing

### Test Pyramid

| Tier | Command | Runner Flags | Speed |
|------|---------|-------------|-------|
| Unit | `make test-unit` | `-n auto` (parallel) | <5s |
| Component | `make test-component` | `-n auto` (parallel) | <10s |
| Integration | `make test-integration` | `--forked -n auto` | <2min |
| E2E | `make test-e2e` | sequential | varies |
| All | `make test-all` | `-n auto` | varies |
| Coverage | `make test-coverage` | covers src/core + src/strategies | varies |

### TDD Workflow (Non-Negotiable)

1. **Red** — Write a failing test
2. **Green** — Minimum code to pass
3. **Refactor** — Improve while green

Every feature starts with a failing test. Minimum 80% coverage on critical paths.

### Why `--forked`

Integration tests use `pytest --forked` because Nautilus C/Rust extensions don't survive `fork()`. Each test runs in a subprocess. Always double `gc.collect()` after engine disposal.

### Key Markers

`unit`, `component`, `integration`, `e2e`, `slow`, `trading`, `db`

Async config: `asyncio_mode = "auto"`, each test gets its own event loop.

## Docker Infrastructure

```bash
docker compose up -d                    # Start all services
docker compose up -d postgres redis     # DB + cache only
docker compose up -d ib-gateway         # IB Gateway (paper trading)
docker compose logs -f postgres         # Watch logs
```

### Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| postgres | timescale/timescaledb:latest-pg16 | 5432 | Database with TimescaleDB |
| redis | redis:7-alpine | 6379 | Caching (appendonly) |
| ib-gateway | ghcr.io/gnzsnz/ib-gateway:stable | 4002 (API), 5900 (VNC) | IBKR paper trading |
| ntrader-app | Local build | 8000 | Application container |

## Database Migrations

```bash
uv run alembic upgrade head     # Apply all migrations
uv run alembic downgrade -1     # Rollback one migration
uv run alembic history          # Show migration history
uv run alembic current          # Show current revision
```

4 migrations: backtest_runs + performance_metrics → additional fields → market_data → trades.

## Common Development Tasks

### Run a Backtest (CLI)

```bash
# From config file
uv run python -m src.cli.main backtest run configs/sma_config_50_200.yaml

# From CLI arguments
uv run python -m src.cli.main backtest run \
    --symbol AAPL --start 2024-01-01 --end 2024-12-31 \
    --strategy sma_crossover --data-source catalog

# Quick demo with mock data
uv run python -m src.cli.main run
```

### Import Data

```bash
uv run python -m src.cli.main data import \
    --csv data/AAPL_1min.csv --symbol AAPL --venue NASDAQ
```

### View Past Backtests

```bash
uv run python -m src.cli.main backtest history --limit 10
uv run python -m src.cli.main backtest show <run-id>
uv run python -m src.cli.main backtest compare <id1> <id2>
```

### Add a New Strategy

1. Write failing test in `tests/unit/strategies/test_my_strat.py`
2. Create `src/core/strategies/my_strat.py` with `@register_strategy` decorator
3. Define a `StrategyConfig` subclass with Pydantic-validated parameters
4. Extract pure logic into a framework-free class for testability

### Update Custom Strategies Submodule

```bash
git submodule update --remote src/core/strategies/custom
```

## Build & Deployment

### Docker Build

```bash
docker build -t ntrader .
docker compose up ntrader-app
```

The container: installs UV, syncs dependencies, runs migrations, exposes port 8000.

### CSS Build

```bash
./scripts/build-css.sh    # Generates static/css/tailwind.css
```

Required after template changes that add new Tailwind classes.

## Code Size Limits

Convention-enforced (not tool-enforced):
- **Files**: <500 lines
- **Functions**: <50 lines
- **Classes**: <100 lines
- **Line length**: 100 chars (ruff-enforced)

## Error Handling

- Domain exceptions: `src/db/exceptions.py` + `src/services/exceptions.py`
- Logging: `structlog.get_logger(__name__)`, key-value pairs only
- Config: console (colored) + file (JSON, 10MB rotation) in `src/utils/logging.py`
- Financial math: **Decimal arithmetic only** (never float)

## Security

- Never commit secrets — use `.env` files
- All broker credentials via environment variables
- Validate input with Pydantic models
- Parameterized queries via SQLAlchemy ORM
