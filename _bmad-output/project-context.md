---
project_name: 'Trading-ntrader'
user_name: 'Allay'
date: '2026-04-04'
sections_completed: ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'quality_rules', 'workflow_rules', 'anti_patterns']
status: 'complete'
rule_count: 88
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

- **Runtime**: Python 3.11+
- **Trading Engine**: nautilus-trader >=1.190.0 (C/Rust extensions — special isolation rules apply)
- **Web**: FastAPI >=0.121.2, Jinja2 >=3.1.6, HTMX, Tailwind CSS, Uvicorn >=0.38.0
- **ORM/DB**: SQLAlchemy >=2.0.43 (async), asyncpg >=0.30.0, PostgreSQL 16 + TimescaleDB
- **Validation**: Pydantic >=2.11.9, pydantic-settings >=2.10.1
- **Exchanges**: python-kraken-sdk >=3.2.7, nautilus-trader[ib] (IBKR)
- **Logging**: structlog >=25.4.0 (console=colored, file=JSON)
- **Migrations**: Alembic >=1.16.5 (4 migration versions)
- **CLI**: Click >=8.2.1
- **Package Manager**: UV only (never pip/poetry)
- **Linter/Formatter**: ruff >=0.13.0 (rules: E, F, I; line-length: 100)
- **Type Checker**: mypy >=1.18.1 (Python 3.11 target, lax untyped defs)
- **Testing**: pytest >=8.4.2, pytest-forked, pytest-xdist, pytest-asyncio (auto mode)
- **Containers**: Docker (python:3.11-slim), docker-compose (postgres, redis, ib-gateway, app)

## Core Principles

- **Simplicity First (KISS/YAGNI)** — start with the simplest working solution; build features only when needed, never on speculation
- **Obvious over clever** — write code any developer can understand immediately
- **Fail Fast & Observable** — validate inputs at system boundaries, raise exceptions early, include correlation IDs for request tracing

## Critical Implementation Rules

### Language-Specific Rules (Python)

- **Imports**: stdlib → third-party → blank line → `src.*` (ruff isort enforced)
- **Circular imports**: Use `TYPE_CHECKING` guard for cross-model references
- **All config via Pydantic BaseSettings** — env vars, never hardcoded values
- **Nested settings**: `Settings.ibkr`, `Settings.kraken` — each with own validators
- **Custom domain exceptions** in `src/db/exceptions.py` — repos wrap SQLAlchemy errors
- **API error handling**: `HTTPException` with proper status codes, never bare `raise`
- **Dual DB pattern**: Web uses async (`AsyncSession`/asyncpg), CLI uses sync (`Session`/psycopg2). **When adding DB features, update both repositories.**
- **Logging**: `structlog.get_logger(__name__)` — DEBUG for calls, INFO/ERROR for issues
- **Decimal arithmetic** for financial calculations (never float)
- **Type hints required** (PEP 484) — all functions must have type annotations; mypy validates before commit
- **Google-style docstrings** on public APIs with Args, Returns, Raises sections
- **Never use bare `except:`** — always catch specific exceptions
- **Structured logging with correlation IDs** — include request context for tracing
- **Ruff auto-formatter runs on save** — can silently revert "unused" imports; make dependent changes in a single edit

### Framework-Specific Rules

#### Nautilus Trader
- **Strategy registration**: `@register_strategy` decorator + `StrategyRegistry.set_config/set_param_model/set_default_config` at module bottom
- **Strategy lifecycle**: `on_start()` → `on_bar()` → `on_stop()` → `on_dispose()`
- **Extract pure logic** into framework-free classes (e.g., `SMATradingLogic`) — testable without Nautilus
- **Position sizing**: Must respect instrument precision (fractional crypto, whole equities)
- **`self.cache.instrument()`** for instrument details, **`self.order_factory.market()`** for orders
- **Engine setup order is strict**: venue → instrument → data → strategy → run. Violations cause cryptic errors
- **Prefer `BacktestOrchestrator`** (takes `BacktestRequest`, handles persistence) over `MinimalBacktestRunner` (legacy, direct params)
- **Double `gc.collect()`** after engine disposal in integration tests (see `integration_cleanup` fixture)

#### FastAPI + HTMX
- **DI chain**: `get_db()` → repository → service; use type aliases (`BacktestService`) in route signatures
- **REST routes**: `src/api/rest/` — return Pydantic response models
- **UI routes**: `src/api/ui/` — return `HTMLResponse` via `templates.TemplateResponse()`
- **Always include `request`** in template context dict
- **`NavigationState`** context object for all page templates
- **`@computed_field`** for derived display properties in response models
- **Fragment endpoints** return HTML partials (no html/body tags) — container ID must match `hx-target`
- **Filter state** preserved in URL query strings via `FilterState.to_query_params()`
- **`_backtest_lock`** (asyncio.Lock) prevents concurrent backtest execution — per-process only, not cross-worker
- **Charts JS load order is strict**: core → price → equity → statistics → charts (modular dependency chain)

#### SQLAlchemy
- **All models**: inherit `Base` + `TimestampMixin`
- **Dual ID pattern**: `id` (BigInteger PK, internal) + `run_id` (UUID, external business key)
- **Repository pattern**: async methods, constructor-injected `AsyncSession`
- **Eager loading**: `selectinload` on relationships to avoid N+1 queries

### Testing Rules

- **TDD is mandatory** — every feature starts with a failing test (Red-Green-Refactor)
- **Test pyramid**: unit (parallel) → component (parallel) → integration (`--forked`) → e2e (sequential) → ui (agent-browser)
- **Integration tests MUST use `--forked`** — Nautilus C/Rust extensions corrupt state across `fork()`
- **Markers required**: `@pytest.mark.unit`, `.component`, `.integration`, `.e2e` on every test
- **API tests**: use FastAPI `dependency_overrides` with `MagicMock()` — always clean up in `finally` block
- **DB test fixtures**: in-memory SQLite (`sqlite+aiosqlite:///:memory:`) for speed
- **Auto-cleanup**: root `conftest.py` runs `gc.collect()` after every test (autouse)
- **Extract pure logic** for unit testing — Nautilus-dependent code goes in component/integration tiers
- **Composable fixtures**: build complex test data by composing simpler fixtures
- **asyncio_mode = "auto"** — async test functions just work, no decorator needed

### Code Quality & Style Rules

- **Size limits**: files <500 lines, functions <50 lines, classes <100 lines, line length 100 chars
- **ruff rules**: `E`, `F`, `I` (errors, pyflakes, isort) — auto-formatted on save
- **File naming**: `snake_case.py` everywhere
- **Class naming**: `PascalCase` with domain suffixes — `*Service`, `*Repository`, `*Config`, `*Parameters`
- **Function naming**: `snake_case` with verb prefixes — `get_`, `create_`, `calculate_`, `build_`, `validate_`
- **Private methods**: single `_` prefix
- **Layer separation**: `api/` → `services/` → `db/repositories/` → `db/models/`
- **Domain models** (`src/models/`) vs **DB models** (`src/db/models/`) — never mix
- **No direct DB access in route handlers** — always go through service → repository chain
- **Use vectorized operations** over loops for data processing (Pandas/NumPy)
- **Cache expensive computations** with `functools.lru_cache`; use Redis for shared caching

### Development Workflow Rules

- **Commit format**: `<type>(<scope>): <subject>` — types: feat, fix, docs, style, refactor, test, chore
- **Never include AI/claude references** in commit messages
- **UV only** for dependencies — `uv add`, `uv remove`, `uv sync` (never pip/poetry)
- **`uv.lock` is committed** — run `uv sync` after pulling
- **Alembic migrations**: run `alembic upgrade head` before first use; 4 existing versions
- **Tailwind CSS**: run `./scripts/build-css.sh` before first UI use
- **Git submodule**: `src/core/strategies/custom/` — update via `git submodule update --remote`
- **Never import from `custom/`** in core code — it's an external submodule
- **Feature branches**: named by spec number (e.g., `012-kraken-crypto-support`)
- **Hooks**: `.claude/hooks/` auto-enforce formatting, file protection, and pre-commit checks

### Critical Don't-Miss Rules

#### Nautilus LogGuard (MOST CRITICAL)
- **NEVER** instantiate `LiveLogger` or `Logger` directly — C logging panics if initialized twice
- Store LogGuard via `set_nautilus_log_guard()` in `src/utils/logging.py`
- LogGuard must remain in scope for process lifetime — if GC'd, C logging subsystem crashes
- Check `get_nautilus_log_guard()` before creating new Nautilus components

#### BacktestEngine
- **Single-use only** — cannot be reused after a run; create a new instance each time
- Always extract results before engine goes out of scope

#### Ruff Auto-Formatter Gotcha
- Runs after every edit — can silently revert "unused" imports
- **Make dependent changes in a single edit** (e.g., add import + its usage together)
- When removing a function parameter: update call sites first, then remove the parameter
- Re-read the file after each edit if you suspect the linter modified it

#### Security
- Never commit `.env` files — they contain real API keys
- Never change `TRADING_MODE` to `live` without explicit user confirmation
- Kraken key/secret must always be set as a pair (model validator enforces this)

#### Data Pipeline
- IBKR/Kraken/CSV → Parquet catalog → BacktestEngine → Results DB → Web UI/Reports
- Parquet catalog is the single source of truth for market data
- Results DB (PostgreSQL/TimescaleDB) stores backtest metadata and trades
- **DataCatalogService** uses lazy client initialization + availability caching
- **Symbol resolution fallback**: `{symbol}` → `{symbol}.NASDAQ` (see `_resolve_instrument_id`)
- **IBKR rate limit**: 45 requests/second — `_guard_nautilus_logging()` context manager prevents double-init
- **Kraken pair mapping**: `BTC/USD` → `XXBTZUSD` / `XBT/USD` / `BTC/USD.KRAKEN` (Futures Charts API with pagination)
- **Strategy alias resolution**: direct name → case-insensitive alias → fuzzy match

---

## Usage Guidelines

**For AI Agents:**

- Read this file before implementing any code
- Follow ALL rules exactly as documented
- When in doubt, prefer the more restrictive option
- Update this file if new patterns emerge

**For Humans:**

- Keep this file lean and focused on agent needs
- Update when technology stack changes
- Review quarterly for outdated rules
- Remove rules that become obvious over time

Last Updated: 2026-04-04
