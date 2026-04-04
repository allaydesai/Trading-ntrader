# Architecture Overview

High-level map. For details, read the topic-specific doc for your area of work.

## Source Layout

```
src/
├── config.py              # Settings + IBKRSettings + KrakenSettings (Pydantic, from env vars)
├── api/                   # → see docs/agent/web-ui.md
│   ├── web.py             # FastAPI app entry point
│   ├── dependencies.py    # DI providers
│   ├── models/            # Pydantic response/presentation models
│   ├── rest/              # JSON API: equity, indicators, timeseries, trades, chart data
│   └── ui/                # HTML routes: backtests, dashboard (Jinja2 + HTMX)
├── cli/
│   ├── main.py            # Click CLI entry point
│   └── commands/          # backtest, compare, data, history, report, reproduce, run, show, strategy
├── core/                  # → see docs/agent/nautilus.md for engine details
│   ├── strategy_registry.py   # @register_strategy decorator + StrategyRegistry
│   ├── strategy_factory.py    # StrategyFactory + StrategyLoader (dynamic import)
│   ├── backtest_orchestrator.py  # Preferred: takes BacktestRequest, handles persistence
│   ├── backtest_runner.py     # Legacy: MinimalBacktestRunner, direct params
│   ├── results_extractor.py  # Nautilus metrics + custom calcs (drawdown, CAGR, Calmar)
│   ├── analytics.py / metrics.py / fee_models.py / position_sizing.py / risk_management.py
│   └── strategies/
│       ├── sma_crossover.py / sma_momentum.py  # Built-in strategies
│       └── custom/            # GIT SUBMODULE (private strategies)
├── db/                    # → see docs/agent/persistence.md
│   ├── session.py / session_sync.py   # Async (web) + sync (CLI) sessions
│   ├── exceptions.py / models/ / repositories/
├── models/                # Domain Pydantic models (request, result, trade, strategy)
├── services/              # → see docs/agent/data-pipeline.md
│   ├── data_catalog.py / data_service.py / data_fetcher.py
│   ├── ibkr_client.py / kraken_client.py
│   ├── exceptions.py / csv_loader.py / nautilus_converter.py
│   ├── backtest_persistence.py / backtest_query.py
│   └── reports/           # CSV, JSON, text exporters
└── utils/
    ├── logging.py         # structlog config + Nautilus LogGuard
    ├── config_loader.py   # YAML strategy config loading
    └── bar_type_utils.py / data_wrangler.py / error_formatter.py
```

## Data Flow

```
IBKR/Kraken/CSV → Parquet catalog → BacktestEngine → ResultsExtractor → PostgreSQL → Web UI
```

1. **Ingestion** — CSV / IBKR TWS / Kraken API → `nautilus_converter` / `kraken_client` → Parquet
2. **Routing** — `DataCatalogService` selects source (`catalog` → `ibkr` → `kraken` → `mock`)
3. **Execution** — Parquet → Nautilus `BacktestEngine` → fills/positions
4. **Extraction** — `ResultsExtractor`: Nautilus metrics + custom calcs (drawdown, CAGR, Calmar)
5. **Storage** — `BacktestPersistenceService` validates (no NaN/Infinity) → PostgreSQL
6. **Presentation** — PostgreSQL → FastAPI REST/UI → Jinja2 + HTMX + Tailwind

## Strategy System

Strategies register via decorator at import time:

```python
@register_strategy(name="sma_crossover", aliases=["sma", "smacrossover"])
class SMACrossover(Strategy): ...
```

**Alias resolution**: direct name → alias (case-insensitive) → fuzzy match (strips underscores/dashes).
**Discovery**: `StrategyRegistry.discover()` scans `src/core/strategies/` + `custom/` submodule.
**Config resolution**: CLI overrides → settings map → Pydantic defaults.
**Prefer** `BacktestOrchestrator` over `MinimalBacktestRunner` for new code.

## Docker & Infrastructure

`docker-compose.yml`: `postgres` (timescaledb:pg16), `redis` (7-alpine), `ib-gateway` (paper trading).
Feature specs: archived in `docs/Archive/specs/` (legacy speckit artifacts).

## Progressive Disclosure

Read the topic doc for your area of work:

| Working on... | Read |
|---------------|------|
| Nautilus engine, LogGuard, strategies | `docs/agent/nautilus.md` |
| Data loading, IBKR/Kraken, Parquet | `docs/agent/data-pipeline.md` |
| Web UI, HTMX, templates, charts | `docs/agent/web-ui.md` |
| Database, repositories, persistence | `docs/agent/persistence.md` |
| Tests, fixtures, TDD | `docs/agent/testing.md` |
| Git, style, dependencies | `docs/agent/conventions.md` |
