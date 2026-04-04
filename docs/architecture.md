# NTrader Architecture

## Executive Summary

NTrader is a production-grade algorithmic trading backtesting system built on Nautilus Trader with IBKR and Kraken data integration. It provides both a CLI and web UI for executing, analyzing, and comparing backtests with full results persistence in PostgreSQL.

## Technology Stack

| Category | Technology | Version | Purpose |
|----------|-----------|---------|---------|
| Language | Python | >=3.11 | Primary language |
| Framework | FastAPI | >=0.121 | Web API + server-rendered UI |
| Trading Engine | Nautilus Trader | >=1.190 | Backtesting engine (C/Rust extensions) |
| ORM | SQLAlchemy | >=2.0 | Database access (async + sync) |
| Database | PostgreSQL + TimescaleDB | 16 | Persistence + time-series |
| Validation | Pydantic | >=2.11 | Models and settings |
| CLI | Click | >=8.2 | Command-line interface |
| Templates | Jinja2 + HTMX | — | Server-rendered UI |
| CSS | Tailwind CSS | — | Styling |
| Charts | TradingView Lightweight Charts | v5.0.0 | Price/equity visualization |
| Package Manager | UV | — | Dependency management |
| Logging | structlog | >=25.4 | Structured logging |
| Migrations | Alembic | >=1.16 | Database schema versioning |

## Architecture Pattern

**Layered monolith** with clear separation of concerns:

```
┌─────────────────────────────────────────────────┐
│  Presentation Layer                              │
│  ├── Web UI (FastAPI + Jinja2 + HTMX)           │
│  ├── REST API (FastAPI, chart data endpoints)    │
│  └── CLI (Click commands)                        │
├─────────────────────────────────────────────────┤
│  Service Layer                                   │
│  ├── BacktestQueryService (async, DI-injected)   │
│  ├── BacktestPersistenceService                  │
│  ├── DataCatalogService (Parquet catalog)        │
│  ├── TradeAnalyticsService                       │
│  └── Data Clients (IBKR, Kraken, CSV)           │
├─────────────────────────────────────────────────┤
│  Core Layer (framework-agnostic)                 │
│  ├── BacktestOrchestrator                        │
│  ├── StrategyRegistry + StrategyFactory          │
│  ├── ResultsExtractor                            │
│  ├── Pure logic: SMA, position sizing, risk      │
│  └── Fee models (IBKR commission)                │
├─────────────────────────────────────────────────┤
│  Data Layer                                      │
│  ├── SQLAlchemy ORM (BacktestRun, Trade, Metrics)│
│  ├── Async Repository (asyncpg, web)             │
│  ├── Sync Repository (psycopg2, CLI)             │
│  └── Alembic migrations                          │
├─────────────────────────────────────────────────┤
│  External                                        │
│  ├── Nautilus Trader Engine (C/Rust)             │
│  ├── PostgreSQL + TimescaleDB                    │
│  ├── Interactive Brokers API                     │
│  └── Kraken Exchange API                         │
└─────────────────────────────────────────────────┘
```

## Data Flow

### Backtest Execution

```
Data Source (IBKR/Kraken/CSV/Mock)
  → Parquet Catalog (single source of truth)
    → BacktestOrchestrator
      → Nautilus BacktestEngine (single-use)
        → ResultsExtractor (metrics before engine disposal)
          → BacktestPersistenceService (NaN validation)
            → PostgreSQL (BacktestRun + Metrics + Trades)
```

### Web UI Request

```
Browser → FastAPI Route
  → Dependency Injection: get_db() → Repository → QueryService
    → SQLAlchemy Query (selectinload for relationships)
      → Pydantic View Model (MetricDisplayItem, BacktestListItem)
        → Jinja2 Template Rendering
          → HTMX Fragment (partial page update)
```

### Chart Data

```
Browser (TradingView) → REST API (/api/timeseries, /api/equity, /api/trades)
  → DataCatalogService (Parquet) or BacktestQueryService (DB)
    → JSON Response (Unix timestamps for TradingView)
```

## Key Design Decisions

### Async/Sync Dual Pattern

Web UI uses async (asyncpg) for non-blocking I/O. CLI uses sync (psycopg2) for simplicity. Both share the same ORM models. **When adding DB features, update both repositories.**

### Strategy Registration

Strategies use `@register_strategy` decorator with name and aliases. Parameter resolution: CLI overrides → settings map → Pydantic defaults. Pure logic extracted into framework-free classes (e.g., `SMATradingLogic`) for unit testing without Nautilus.

### Nautilus Engine Constraints

- **Single-use**: BacktestEngine cannot be reused after a run
- **LogGuard**: C logging panics if initialized twice — use `set_nautilus_log_guard()`
- **Fork isolation**: Integration tests use `--forked` to prevent C extension corruption
- **Strict setup order**: venue → instrument → data → strategy → run
- **Extract results before disposal**: Engine is garbage-collected after run

### Configuration Reproducibility

Every backtest stores its complete `config_snapshot` as JSONB. The `reproduce` command rebuilds a `BacktestRequest` from this snapshot and re-executes with identical parameters.

### HTMX Fragment Pattern

Server-rendered UI uses HTMX for dynamic updates. Fragment endpoints return HTML partials (no html/body tags). Filter state preserved in URL query strings. The `_backtest_lock` (asyncio.Lock) prevents concurrent executions.

## Deployment Architecture

```
docker-compose.yml:
  ├── postgres (timescale/timescaledb:latest-pg16)
  │     Port 5432, persistent volume
  ├── redis (redis:7-alpine)
  │     Port 6379, appendonly persistence
  ├── ib-gateway (ghcr.io/gnzsnz/ib-gateway:stable)
  │     Port 4002 (API), 5900 (VNC)
  └── ntrader-app (local Dockerfile)
        Port 8000, depends on all above
        Runs: alembic upgrade head → uvicorn
```

## Cross-References

For implementation details, see:
- **Agent docs** (`agent_docs/`): architecture.md, nautilus.md, data-pipeline.md, web-ui.md, persistence.md, testing.md, conventions.md
- **Source tree**: `docs/source-tree-analysis.md`
- **API contracts**: `docs/api-contracts.md`
- **Data models**: `docs/data-models.md`
- **Development**: `docs/development-guide.md`
