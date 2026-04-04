# NTrader Source Tree Analysis

**Total Python Files:** ~222 (113 src/, 109 tests/)

## Source Directory (`src/`)

```
src/
├── __init__.py
├── config.py                    # Pydantic Settings (IBKRSettings, KrakenSettings, env vars)
│
├── api/                         # Web API + UI (FastAPI)
│   ├── web.py                   # FastAPI app factory, mount routes, init logging
│   ├── dependencies.py          # DI chain: get_db → repo → service
│   ├── models/                  # 13 Pydantic models (request/response/view)
│   │   ├── backtest_detail.py   #   MetricDisplayItem, MetricsPanel, ConfigurationSnapshot
│   │   ├── backtest_list.py     #   BacktestListItem, BacktestListPage
│   │   ├── chart_equity.py      #   EquityResponse, EquityPoint
│   │   ├── chart_timeseries.py  #   TimeseriesResponse, Candle
│   │   ├── chart_trades.py      #   TradesResponse, TradeMarker
│   │   ├── chart_indicators.py  #   IndicatorsResponse
│   │   ├── chart_errors.py      #   ErrorDetail
│   │   ├── dashboard.py         #   DashboardSummary
│   │   ├── run_backtest.py      #   BacktestRunFormData, StrategyOption
│   │   ├── filter_models.py     #   FilterState, SortColumn, ExecutionStatus
│   │   ├── navigation.py        #   NavigationState, BreadcrumbItem
│   │   └── common.py            #   EmptyStateMessage
│   ├── rest/                    # 4 REST endpoints (chart data)
│   │   ├── equity.py            #   GET /api/equity/{run_id}, /api/equity-curve/{id}
│   │   ├── timeseries.py        #   GET /api/timeseries
│   │   ├── trades.py            #   GET /api/trades/{run_id}, /api/backtests/{id}/trades
│   │   └── indicators.py        #   GET /api/indicators/{run_id}
│   └── ui/                      # 2 UI route modules (server-rendered)
│       ├── dashboard.py         #   GET / (dashboard)
│       └── backtests.py         #   /backtests/* (list, detail, run, delete, rerun)
│
├── cli/                         # Click CLI
│   ├── main.py                  # Click group entry point
│   └── commands/                # 11 command modules
│       ├── backtest.py          #   `ntrader backtest` group
│       ├── run.py               #   `ntrader backtest run` (config + CLI mode)
│       ├── history.py           #   `ntrader backtest history` (list past runs)
│       ├── show.py              #   `ntrader backtest show` (detail view)
│       ├── compare.py           #   `ntrader backtest compare` (side-by-side)
│       ├── reproduce.py         #   `ntrader backtest reproduce` (replay)
│       ├── data.py              #   `ntrader data import` (CSV → Parquet)
│       ├── strategy.py          #   `ntrader strategy list|create`
│       ├── report.py            #   `ntrader report summary|generate`
│       └── _backtest_helpers.py #   Shared: resolve_request, load_data, display_results
│
├── core/                        # Business logic (framework-agnostic)
│   ├── sma_logic.py             # SMATradingLogic — pure Python signal generation
│   ├── position_sizing.py       # PositionSizingLogic
│   ├── risk_management.py       # RiskManagementLogic
│   ├── metrics.py               # Performance metric calculations
│   ├── analytics.py             # Trade analytics
│   ├── backtest_orchestrator.py # BacktestOrchestrator (preferred runner)
│   ├── backtest_runner.py       # MinimalBacktestRunner (legacy)
│   ├── strategy_factory.py      # StrategyFactory — param resolution
│   ├── strategy_registry.py     # @register_strategy decorator + registry
│   ├── results_extractor.py     # ResultsExtractor — Nautilus metrics + custom calcs
│   ├── fee_models.py            # IBKRCommissionModel
│   └── strategies/              # Strategy implementations
│       ├── sma_crossover.py     #   SMA Crossover strategy
│       ├── sma_momentum.py      #   SMA Momentum strategy
│       └── custom/              #   ⚠️ Git submodule (external strategies repo)
│
├── db/                          # Database layer (SQLAlchemy)
│   ├── base.py                  # DeclarativeBase + TimestampMixin
│   ├── session.py               # Async engine/session (asyncpg)
│   ├── session_sync.py          # Sync engine/session (psycopg2)
│   ├── exceptions.py            # DatabaseConnectionError, DuplicateRecordError
│   ├── models/
│   │   ├── backtest.py          #   BacktestRun + PerformanceMetrics ORM
│   │   └── trade.py             #   Trade ORM
│   ├── repositories/
│   │   ├── backtest_repository.py       # Async repo (web UI)
│   │   └── backtest_repository_sync.py  # Sync repo (CLI)
│   └── types/                   # Custom SQLAlchemy types
│
├── models/                      # Pydantic domain models
│   ├── backtest_request.py      # BacktestRequest (unified CLI/web)
│   ├── trade.py                 # TradeBase, TradeCreate, Trade, statistics
│   ├── strategy.py              # SMAParameters, MomentumParameters
│   ├── market_data.py           # MarketDataBase, OHLCV validation
│   ├── catalog_metadata.py      # CatalogAvailability, FetchRequest
│   └── config_snapshot.py       # StrategyConfigSnapshot
│
├── services/                    # Application services
│   ├── backtest_persistence.py  # Saves results to DB (NaN validation)
│   ├── backtest_query.py        # BacktestQueryService (async, DI target)
│   ├── data_service.py          # Data loading orchestration
│   ├── data_fetcher.py          # Auto-fetch from IBKR/Kraken if missing
│   ├── data_catalog.py          # DataCatalogService (Parquet catalog)
│   ├── ibkr_client.py           # IBKRHistoricalClient (rate limit 45 req/s)
│   ├── ibkr_data_provider.py    # IBKR data provider wrapper
│   ├── kraken_client.py         # Kraken API (pair mapping, pagination)
│   ├── csv_loader.py            # CSV → Nautilus bars
│   ├── nautilus_converter.py    # Nautilus ↔ domain model converter
│   ├── portfolio.py             # Portfolio service
│   ├── trade_analytics.py       # Trade statistics, equity curves, drawdowns
│   ├── exceptions.py            # DataNotFoundError, RateLimitExceededError, etc.
│   ├── catalog/                 # Catalog sub-services
│   └── reports/                 # Report generation
│       ├── csv_exporter.py
│       ├── json_exporter.py
│       ├── text_report.py
│       └── validators.py
│
└── utils/                       # Utilities
    ├── logging.py               # structlog config + Nautilus LogGuard
    ├── config_loader.py         # YAML config loader
    └── ...                      # Formatting, helpers
```

## Tests Directory (`tests/`)

```
tests/
├── conftest.py                  # project_root, test_data_dir, autouse cleanup (gc.collect)
├── fixtures/
│   └── scenarios.py             # MarketScenario dataclass (VOLATILE, TRENDING, RANGING)
│
├── unit/                        # Pure Python, parallel (-n auto)
│   ├── api/                     #   API model tests
│   ├── cli/                     #   CLI command tests
│   ├── core/                    #   Core logic tests (SMA, position sizing, risk)
│   ├── models/                  #   Pydantic model tests
│   ├── services/                #   Service tests (mocked dependencies)
│   ├── signals/                 #   Signal generation tests
│   └── utils/                   #   Utility tests
│
├── component/                   # Test doubles, parallel (-n auto)
│   ├── conftest.py              #   test_engine, sma_logic, risk_manager fixtures
│   ├── doubles/                 #   TestTradingEngine, TestOrder, TestPosition
│   ├── api/                     #   API component tests
│   └── services/                #   Service component tests
│
├── integration/                 # Real Nautilus, --forked (subprocess isolation)
│   ├── conftest.py              #   integration_cleanup (double gc.collect)
│   ├── db/                      #   PostgreSQL integration tests
│   └── api/                     #   API integration tests
│
├── e2e/                         # Full workflow, sequential
├── api/                         # API layer tests
│   ├── models/                  #   View model tests
│   └── rest/                    #   REST endpoint tests
├── ui/                          # Web UI tests
├── db/                          # Database-specific tests
├── services/                    # Service layer tests
└── catalog/                     # Data catalog tests
```

## Root Configuration

| File | Purpose |
|------|---------|
| `pyproject.toml` | Dependencies, pytest markers, mypy/ruff config |
| `pytest.ini` | Test discovery, asyncio_mode=auto, markers |
| `Makefile` | Build/test automation (test-unit, lint, format, typecheck) |
| `alembic.ini` | Alembic migration config |
| `docker-compose.yml` | PostgreSQL+TimescaleDB, Redis, IB Gateway |
| `Dockerfile` | Python 3.11-slim + UV |
| `tailwind.config.js` | Tailwind CSS for templates |
| `.gitmodules` | Custom strategies submodule |
| `mypy.ini` | Type checking (Python 3.11 target) |

## Supplementary Directories

| Directory | Files | Purpose |
|-----------|-------|---------|
| `scripts/` | 12 | Dev utilities, validation scripts, DB checks |
| `configs/` | 5 YAML | Strategy configuration examples |
| `templates/` | 14 HTML | Jinja2 templates (base, backtests/, partials/) |
| `static/` | 9 | CSS (Tailwind), JS (charts), vendor (HTMX) |
| `specs/` | 13 dirs, ~136 md | Feature specifications 001-013 |
| `alembic/` | 4 migrations | Database schema versioning |
| `data/` | — | Parquet catalog (gitignored) |
| `logs/` | — | Application logs (gitignored) |

## Entry Points

| Entry | File | Invocation |
|-------|------|------------|
| CLI | `src/cli/main.py` | `uv run python -m src.cli.main` |
| Web | `src/api/web.py` | `uv run uvicorn src.api.web:app --reload` |
| Script | `ntrader.py` | `python ntrader.py` |
