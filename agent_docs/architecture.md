# Architecture

## Source Layout

```
src/
├── config.py              # Settings + IBKRSettings + KrakenSettings (Pydantic, from env vars)
├── api/
│   ├── web.py             # FastAPI app entry point
│   ├── dependencies.py    # DI providers
│   ├── models/            # Pydantic response models (backtest, chart, filter, etc.)
│   ├── rest/              # JSON API: equity, indicators, timeseries, trades
│   └── ui/                # HTML routes: backtests, dashboard (Jinja2 + HTMX)
├── cli/
│   ├── main.py            # Click CLI entry point
│   └── commands/          # backtest, data, history, report, reproduce, run, show, strategy
├── core/
│   ├── strategy_registry.py   # @register_strategy decorator + StrategyRegistry
│   ├── strategy_factory.py    # StrategyFactory + StrategyLoader (dynamic import)
│   ├── backtest_runner.py     # Single backtest execution
│   ├── backtest_orchestrator.py
│   ├── analytics.py / metrics.py / results_extractor.py
│   ├── fee_models.py / position_sizing.py / risk_management.py
│   └── strategies/
│       ├── sma_crossover.py   # Built-in: SMACrossover
│       ├── sma_momentum.py    # Built-in: SMAMomentum
│       └── custom/            # GIT SUBMODULE (private strategies)
├── db/
│   ├── session.py / session_sync.py   # Async + sync SQLAlchemy sessions
│   ├── models/                        # ORM: backtest.py, trade.py
│   └── repositories/                  # Async + sync backtest repos
├── models/                # Domain Pydantic models (request, result, trade, etc.)
├── services/
│   ├── ibkr_client.py / ibkr_data_provider.py  # IBKR connectivity
│   ├── kraken_client.py                         # Kraken crypto data (pair mapping, rate limiting)
│   ├── data_catalog.py / data_service.py        # Parquet catalog (IBKR + Kraken sources)
│   ├── exceptions.py                            # Service exceptions (IBKR, Kraken)
│   ├── csv_loader.py / nautilus_converter.py    # Data import
│   ├── backtest_persistence.py / backtest_query.py
│   ├── trade_analytics.py / portfolio.py
│   └── reports/           # CSV, JSON, text exporters
└── utils/
    ├── logging.py         # structlog config + Nautilus LogGuard
    ├── config_loader.py   # YAML strategy config loading
    └── bar_type_utils.py / data_wrangler.py / error_formatter.py
```

## Data Flow

1. **Data ingestion**: CSV files, IBKR TWS, or Kraken API → `nautilus_converter` / `kraken_client` → Parquet catalog
2. **Backtest execution**: Parquet catalog → Nautilus `BacktestEngine` → fills/positions
3. **Results storage**: `results_extractor` → PostgreSQL (backtest_runs, trades, metrics)
4. **Presentation**: PostgreSQL → FastAPI REST/UI → Jinja2 + HTMX + Tailwind

## Database

PostgreSQL 16+ with TimescaleDB. Four Alembic migrations in `alembic/versions/`:

| Migration | Tables |
|-----------|--------|
| `9c7d5c4` | `backtest_runs`, `performance_metrics` |
| `0937d13` | Additional Nautilus Trader fields on above |
| `7d28f3a` | `market_data` |
| `34f3c8e` | `trades` (individual trade tracking) |

Run migrations: `alembic upgrade head`

## Strategy Registry

Strategies register via decorator at import time:

```python
@register_strategy(name="sma_crossover", aliases=["sma"])
class SMACrossover(Strategy): ...
```

`StrategyRegistry.discover()` scans `src/core/strategies/` and `custom/` (submodule).
The `custom/` dir is a git submodule — update with `git submodule update --remote`.

## Docker Services

`docker-compose.yml` runs: `postgres` (timescaledb:pg16), `redis` (7-alpine), `ib-gateway` (paper trading).

## Web Stack

- **Backend**: FastAPI + Jinja2 templates (`templates/`)
- **Frontend**: HTMX (`static/vendor/htmx.min.js`) + Tailwind CSS
- **Static assets**: `static/js/charts*.js`, `static/css/`
- **CSS build**: `./scripts/build-css.sh` (required before first run)
- **Start dev**: `uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000`

## Feature Specs

`specs/001-012/` contain feature specifications (spec.md, plan.md, tasks.md, contracts/).
