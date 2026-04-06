---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
lastStep: 8
status: 'complete'
completedAt: '2026-04-05'
inputDocuments:
  - 'prd.md'
  - 'product-brief-Trading-ntrader.md'
  - 'ux-design-specification.md'
  - 'project-context.md'
  - 'docs/agent/architecture.md'
  - 'docs/governance/development-principles.md'
workflowType: 'architecture'
project_name: 'Trading-ntrader'
user_name: 'Allay'
date: '2026-04-05'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements (34 total):**
- **Data Import Pipeline (FR1-FR14):** CLI-driven batch import with dry-run validation, 6 CSV schema parsers, OHLC sanity checks, row count verification, sample point validation, idempotent re-runs via date-range comparison, import summary reporting, progress output, isolated catalog storage, sharded directory handling, zip extraction
- **Data Explorer (FR15-FR21):** Paginated/searchable ticker list with metadata, timeframe selection, windowed chart view with pan/zoom, data statistics display, supplementary data display (company profiles, dividends, splits)
- **Catalog Integration (FR22-FR24):** FirstRate catalog as BacktestEngine data source, routing based on configuration, metadata reads without loading full price data
- **Backtest Verification (FR25-FR27):** End-to-end backtest against imported data, asset-appropriate position sizing per asset class, reference comparison against existing CSV loader
- **Supplementary Data (FR28-FR34):** Company profiles, dividend history, stock split parsing and association, import configuration (source dir, asset class, timeframes)

**Non-Functional Requirements:**

| Category | Requirement | Architectural Impact |
|---|---|---|
| Performance | Chart data load < 2s | Windowed Parquet reads, time-range API slicing |
| Performance | Ticker search < 300ms (20K+ tickers) | Metadata index or cache required |
| Performance | Ticker list page < 500ms | Paginated queries with pre-computed metadata |
| Performance | Catalog metadata read < 500ms | Cannot scan Parquet directories on every request |
| Reliability | Individual ticker failure doesn't block import | Per-ticker error isolation, summary reporting |
| Reliability | Interrupted imports leave consistent state | Atomic writes — no partial Parquet files |
| Reliability | Explorer reflects newly imported data on refresh | No stale metadata caching without invalidation |
| Integration | Parquet directly consumable by BacktestEngine | Must produce `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet` |
| Integration | Existing web UI patterns (HTMX, DI chain) | New routes follow established patterns |
| Integration | Existing CLI patterns (Click) | New commands in `src/cli/commands/` |
| Security | Env var configuration only | Pydantic settings, no credentials in code |

**Scale & Complexity:**

- Primary domain: Data pipeline + web application (full-stack)
- Complexity level: High
- Estimated architectural components: ~12 (6 CSV parsers, instrument mapper, catalog writer, catalog metadata service, explorer API endpoints, explorer UI templates, CLI import commands)

### Technical Constraints & Dependencies

**Nautilus Trader Constraints:**
- BacktestEngine is single-use — new instance per run
- LogGuard C extension must not be initialized twice — import pipeline must respect this
- Engine setup order is strict: venue → instrument → data → strategy → run
- Parquet catalog must follow existing `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet` directory convention
- Catalog v2 schema changes (GitHub Issue #991) may require catalog writing layer updates

**Existing Codebase Constraints:**
- Must use `BacktestOrchestrator` pattern for backtest integration
- Must extend or parallel `DataCatalogService` with lazy-init + availability-caching pattern
- CLI via Click in `src/cli/commands/`, web routes via FastAPI in `src/api/`
- Dual DB pattern: async (web) + sync (CLI) — new DB features need both
- Ruff auto-formatter runs after edits — dependent changes must be atomic
- TDD mandatory — every feature starts with a failing test

**Data Constraints:**
- 6 distinct CSV schemas with different column counts, date formats, and fields
- 387GB+ total dataset — parsers must process one ticker at a time (bounded memory)
- Decimal precision must be maintained through CSV → Parquet conversion
- Catalog isolation enforced — FirstRate data never mixed with IBKR/Kraken data
- Adjustment methodology: exclusively split+dividend adjusted

**UX Constraints:**
- Server-rendered HTML with HTMX partial updates (no SPA framework)
- TradingView Lightweight Charts v5.0 for charting (CDN-loaded, already in codebase)
- Dark theme (slate-950), existing Tailwind design system
- Desktop-only (Chrome/Safari on macOS)
- Progressive chart loading via time-range API slicing

### Cross-Cutting Concerns

1. **Instrument ID Correctness** — Affects import pipeline (mapping), catalog storage (directory names), explorer display, and backtest execution. A wrong instrument ID cascades silently through every downstream component
2. **Parquet Catalog Format** — The canonical interface between import (write) and consumption (read by BacktestEngine + explorer). Format must satisfy Nautilus requirements, explorer query needs, and potential Catalog v2 migration
3. **Catalog Metadata** — Ticker lists, date ranges, timeframe availability, and row counts are needed by both the explorer (search/filter/display) and CLI (idempotent import checks). This metadata must be fast to query (<300ms) across 20K+ tickers
4. **Error Handling & Reporting** — Import pipeline (CLI log output + summary report) and explorer (inline error messages in HTMX fragments) both need structured error handling, but with different presentation patterns
5. **Data Validation** — OHLC sanity, row count verification, sample point comparison needed at import time; data quality indicators (gaps, coverage) needed at explorer display time. Validation results must be accessible to both surfaces
6. **Named Catalog Abstraction** — The UX spec introduces catalogs as named entities with metadata. This concept spans configuration (where catalogs are defined), import (target catalog), explorer (catalog selector), and backtest (data source selection)

### Open Architectural Questions

1. **Multi-instrument backtest support** — Single multi-instrument engine run vs batch of single-instrument runs? Needs Nautilus research
2. **Catalog metadata storage** — Parquet directory scanning vs metadata DB table vs in-memory index? Trade-off: freshness vs query speed
3. **Named catalog configuration** — Pydantic settings vs DB vs config file? Affects how catalogs are created, discovered, and selected
4. **Catalog v2 abstraction depth** — How much indirection around Parquet writing to mitigate Nautilus catalog format changes?
5. **15-min resampling scope** — Product brief includes it, PRD does not. Architecture should account for it or explicitly exclude it

## Technical Foundation (Brownfield)

### Existing Technology Stack

This is a brownfield extension of an established backtesting system with 13+ shipped feature specs. No starter template selection is needed — the technology stack, project structure, and development patterns are fully established.

**Core Stack:**
- **Runtime:** Python 3.11+
- **Trading Engine:** nautilus-trader >=1.190.0 (C/Rust extensions with strict isolation rules)
- **Web:** FastAPI >=0.121.2 + Jinja2 + HTMX + Tailwind CSS + TradingView Lightweight Charts v5.0
- **Database:** PostgreSQL 16 + TimescaleDB, SQLAlchemy >=2.0.43 (async + sync dual pattern)
- **CLI:** Click >=8.2.1
- **Validation:** Pydantic >=2.11.9 + pydantic-settings
- **Package Manager:** UV (never pip/poetry)
- **Quality:** ruff (format + lint), mypy, pytest (forked, xdist, asyncio)

### Nautilus Trader Capabilities Research

Research conducted via official Nautilus Trader documentation to resolve open architectural questions:

**Multi-Instrument Backtest Support: CONFIRMED**
- `BacktestDataConfig` natively accepts a list of `instrument_ids`
- `BacktestRunConfig` accepts multiple `BacktestDataConfig` objects
- A single engine run can process multiple instruments simultaneously
- **Architectural implication:** No need for a batch-of-single-instrument-runs pattern. The existing `BacktestOrchestrator` can be extended to pass multiple instrument IDs from a single catalog

**ParquetDataCatalog API:**
- `write_data(bars)` — auto-categorizes by data type + instrument ID
- `bars(bar_types=[...], start=..., end=...)` — windowed reads with time-range filtering
- `instruments()` — enumerate available instruments in catalog
- Bar type naming: `{INSTRUMENT_ID}-{STEP}-{AGGREGATION}-{PRICE_TYPE}-EXTERNAL`
- Path-based initialization: `ParquetDataCatalog(path="./catalog", fs_protocol="file")`
- **WARNING:** `write_data` overwrites existing files for same instrument/type — idempotent by design but partial writes during interruption could corrupt data
- Multiple named catalogs = multiple `ParquetDataCatalog` instances with different paths

**Catalog Directory Structure:**
Data is organized by data type and instrument ID. The catalog handles file organization internally — the import pipeline produces Nautilus data objects (e.g., `Bar`) and calls `catalog.write_data()`, letting the catalog manage storage layout.

### Architectural Decisions Already Established

These decisions are inherited from the existing codebase and are not open for re-evaluation:

| Decision | Established Pattern | Impact on New Feature |
|---|---|---|
| Web framework | FastAPI + Jinja2 + HTMX | Explorer UI follows existing template/route patterns |
| CLI framework | Click | Import commands follow existing CLI structure |
| Data storage | Parquet via Nautilus `ParquetDataCatalog` | Import pipeline uses `write_data()` API |
| Database | PostgreSQL + SQLAlchemy (async/sync dual) | Catalog metadata may use existing DB infrastructure |
| Testing | pytest with TDD, forked integration tests | All new code requires test-first development |
| DI pattern | `get_db()` → repository → service | New services plug into existing DI chain |
| Config | Pydantic settings from env vars | New settings (catalog paths, names) follow existing pattern |
| Logging | structlog + Nautilus LogGuard | Import pipeline respects LogGuard lifecycle |
| Deployment | Docker + docker-compose | No new infrastructure decisions |

### New Technical Capabilities Required

The FirstRate Data import + explorer feature requires these new capabilities not present in the current codebase:

1. **CSV schema parser framework** — 6 distinct parsers producing Nautilus `Bar` objects from headerless CSV files
2. **Instrument ID mapping service** — Ticker symbol → Nautilus-qualified ID (e.g., SPY → SPY.ARCA) using reference data
3. **Named catalog management** — Multiple `ParquetDataCatalog` instances with named configuration
4. **Catalog metadata index** — Fast (<300ms) searchable index of available tickers, date ranges, and bar counts across 20K+ instruments
5. **Windowed chart data API** — Time-range sliced Parquet reads for progressive chart loading
6. **HTMX explorer UI** — New page with search, filter pills, chart panel, and stats panel using existing component patterns

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**
1. Catalog metadata storage → PostgreSQL
2. Named catalog configuration → Pydantic settings
3. CSV parser architecture → Strategy pattern with registry
4. Import atomicity → Idempotent re-run with metadata gatekeeper
5. Instrument ID mapping → PostgreSQL table populated from reference files
6. Chart data API → Direct Parquet reads via Nautilus catalog API
7. Explorer search → SQL ILIKE with index on PostgreSQL

**Deferred Decisions (Not Blocking):**
- 15-min resampling: Scope decision, not architectural. Can be added to any phase without changes
- Catalog v2 migration: No abstraction layer needed — `catalog.write_data()` already abstracts file layout

### Data Architecture

**ADR-1: Catalog Metadata in PostgreSQL**
- **Decision:** Store catalog metadata (tickers, date ranges, bar counts, timeframe availability) in a PostgreSQL table
- **Rationale:** Existing DB infrastructure, fast paginated search with sorting, supports both async (web) and sync (CLI) access via existing dual repository pattern
- **Trade-off accepted:** Dual-write required (Parquet + DB) — but import pipeline is the only writer, so drift risk is minimal
- **Recovery:** A CLI "rebuild metadata" command can rescan Parquet and repopulate if ever needed

**ADR-2: Named Catalogs via Pydantic Settings**
- **Decision:** Define named catalogs (name, path, format) in Pydantic settings, consistent with existing `IBKRSettings`/`KrakenSettings` pattern
- **Rationale:** Small number of catalogs, rarely changed, no dynamic creation needed. `--catalog` CLI flag maps to a named settings entry
- **Trade-off accepted:** Adding a catalog requires config change + restart — acceptable for a personal tool with a handful of catalogs

**ADR-3: Single `catalog_instruments` Table**
- **Decision:** One table combining instrument identity (ticker, Nautilus ID, exchange, asset class, name, sector, industry, IPO date) with import metadata (catalog name, date range, bar counts per timeframe). Populated from company_profiles.csv during first import, updated by import pipeline per-ticker
- **Rationale:** Keeps Phase 1 simple — one table serves both import pipeline (instrument ID lookup, idempotent detection) and explorer (search, filter, display, statistics). Future phases may normalize into separate tables when fundamentals/supplementary data justify it
- **Serves:** FR5 (instrument mapping), FR9 (idempotent detection), FR15-FR17 (ticker list/search/filter), FR20 (statistics), FR24 (catalog metadata reads), FR28 (company profiles)

### Import Pipeline Architecture

**ADR-4: Strategy Pattern with Parser Registry**
- **Decision:** Each CSV schema implemented as a parser class with a common interface, registered by asset class. All parsers produce `list[Bar]` and support shared validation hooks (OHLC sanity, row counting)
- **Rationale:** 6 genuinely distinct schemas with different parsing logic (FX date handling, futures open interest, index no-volume). Common interface ensures consistency; shared logic in base class. Mirrors `@register_strategy` pattern
- **Interface contract:** `parse(file_path) -> list[Bar]`, `map_instrument_id(ticker) -> InstrumentId`, `validate(bars) -> ValidationResult`
- **Extensibility:** New asset class = new parser class, no existing code touched

**ADR-5: Idempotent Import with Metadata Gatekeeper**
- **Decision:** Import writes Parquet via `catalog.write_data()`, then upserts metadata row only after successful write + row count verification. No temp files, no staging catalog. Interrupted imports leave orphaned Parquet files (invisible to explorer/backtest since no metadata row exists). Re-run detects incomplete tickers and re-imports
- **Rationale:** Simplest approach that guarantees correctness. Metadata DB row is the gatekeeper — only tickers with verified metadata are visible to the system
- **Edge case accepted:** A ticker fully written to Parquet but crashed before metadata upsert gets re-imported on next run — wasteful but correct
- **Per-ticker loop:** Parse CSV → validate OHLC → `catalog.write_data(bars)` → verify row count → upsert metadata row → log success

**ADR-6: Instrument Mapping via PostgreSQL**
- **Decision:** Instrument ID mapping stored in `catalog_instruments` table (same table as ADR-3). First import loads company_profiles.csv into DB. Subsequent imports look up instrument IDs from DB. Each parser implements asset-class-specific mapping logic to populate the table
- **Rationale:** Leverages existing DB infrastructure, makes mapping data available to both import and explorer, supports manual overrides via DB update + re-import
- **Phase 1:** company_profiles.csv for ETF exchange mapping (ticker → exchange → Nautilus ID)
- **Future phases:** Each parser adds its own mapping logic (futures → ES.CME, FX → EUR/USD.SIM, etc.)

### API & Communication Patterns

**ADR-7: Direct Parquet Reads for Chart Data**
- **Decision:** Chart API endpoint reads directly from Parquet via `catalog.bars(bar_types=[...], start=..., end=...)`. No intermediate storage or pre-aggregation
- **Rationale:** Nautilus API already supports windowed time-range reads. Parquet columnar format enables efficient filtering. Avoids duplicating 387GB in PostgreSQL
- **API contract:** `GET /api/chart/{ticker}?catalog=...&tf=...&start=...&end=...` → resolves Nautilus instrument ID from DB → builds bar type string → catalog query → JSON OHLCV array
- **Performance target:** <2s for single ticker/timeframe. If 1-min data at large ranges is slow, narrow the default window rather than change the architecture

**ADR-8: SQL Search for Explorer**
- **Decision:** Ticker search via SQL `ILIKE` prefix matching with B-tree index on `catalog_instruments.ticker`. Pagination, sorting, and asset class filtering via standard SQL clauses
- **Rationale:** 20K rows is trivial for PostgreSQL. Prefix ILIKE on indexed column returns in single-digit milliseconds. No extra infrastructure (full-text search, in-memory cache) needed
- **Serves:** FR15 (paginated list), FR16 (search/filter), FR24 (metadata reads)

### Frontend Architecture

**No new decisions required.** Explorer follows existing patterns:
- Jinja2 templates extending `base.html` with HTMX fragment swaps
- `NavigationState` for page context, `FilterState` for URL-preserved search/filter state
- TradingView Lightweight Charts for chart rendering (already in codebase)
- New components (catalog selector, search input, filter pills, ticker table, timeframe toolbar, stats panel) all server-rendered with Tailwind CSS

### Infrastructure & Deployment

**No new decisions required.** Existing Docker + docker-compose infrastructure is sufficient:
- PostgreSQL already available for metadata storage
- No new services or containers needed
- Import pipeline runs as CLI command inside existing container
- Explorer is new routes in existing FastAPI app

### Decision Impact Analysis

**Implementation Sequence:**
1. Database schema: `catalog_instruments` table + Alembic migration
2. Pydantic settings: Named catalog configuration
3. Parser framework: Base class + ETF parser (Phase 1)
4. Instrument mapping: company_profiles.csv loader → DB population
5. Import pipeline: CLI command orchestrating parse → validate → write → metadata upsert
6. Chart API: Parquet read endpoint with time-range filtering
7. Explorer UI: Templates + HTMX routes using metadata from DB

**Cross-Component Dependencies:**
- Parser framework (ADR-4) produces `Bar` objects consumed by `catalog.write_data()` (ADR-7) and metadata upserts (ADR-1)
- `catalog_instruments` table (ADR-3) is written by import pipeline (ADR-5/6) and read by explorer API (ADR-8) and chart API (ADR-7)
- Named catalog settings (ADR-2) are consumed by import CLI (`--catalog` flag), explorer UI (catalog selector), and chart API (catalog path resolution)
- Instrument ID mapping (ADR-6) bridges the import pipeline (CSV ticker → Nautilus ID) and chart API (ticker → bar type string)

## Implementation Patterns & Consistency Rules

### Pattern Scope

The existing `project-context.md` defines 88 implementation rules for AI agents (naming, testing, framework patterns, etc.). This section defines **additional patterns specific to the FirstRate Data import + explorer feature** that prevent conflicts between agents implementing different components.

### Parser Registry Pattern

All FirstRate CSV parsers must implement a common abstract interface:

**Base class:** `src/services/firstrate/parsers/base.py`
- `parse_file(file_path: Path, ticker: str) -> list[Bar]` — parse CSV into Nautilus Bar objects
- `map_instrument_id(ticker: str, db_session: Session) -> InstrumentId` — resolve Nautilus-qualified ID
- `validate_bars(bars: list[Bar]) -> ValidationResult` — shared OHLC sanity + row count (base class provides default)
- Registration via decorator: `@register_parser(asset_class=AssetClass.ETF)`

**Rules:**
- Each parser is one file, one class, registered for one asset class
- Parsers never write to Parquet or DB — they only parse and validate. The import service orchestrates writes
- Shared validation logic lives in the base class. Asset-specific validation overrides `validate_bars()`
- All parsers produce `list[Bar]` sorted by `ts_init` ascending — the import service depends on this ordering

### File Organization

New FirstRate-specific code lives under `src/services/firstrate/`:

```
src/services/firstrate/
├── __init__.py
├── parsers/                     # CSV schema parsers (one per asset class)
│   ├── __init__.py
│   ├── base.py                  # ABC + shared validation + parser registry
│   └── etf_parser.py            # Phase 1 (additional parsers added per phase)
├── import_service.py            # Orchestrates: parse → validate → write → metadata upsert
├── instrument_mapper.py         # company_profiles.csv loader → DB population
├── catalog_manager.py           # Named catalog resolution (Pydantic settings → ParquetDataCatalog)
└── metadata_service.py          # catalog_instruments CRUD (async + sync)
```

**Rules:**
- No FirstRate-specific code in `src/core/` — core remains framework-agnostic
- Explorer UI routes in `src/api/ui/explorer.py`, REST endpoints in `src/api/rest/explorer.py`
- Explorer templates in `templates/explorer/` (page + fragments)
- CLI import command in `src/cli/commands/import_data.py`
- Tests mirror source: `tests/unit/services/firstrate/`, `tests/component/`, etc.

### Database Naming

**Table:** `catalog_instruments` (snake_case plural, matches existing `backtest_runs`, `backtest_trades`)

**Columns (all snake_case):**
- `id` (BigInteger PK), `ticker`, `nautilus_id`, `asset_class`, `catalog_name`
- `exchange`, `name`, `sector`, `industry`, `ipo_date`
- `date_range_start`, `date_range_end`
- `bar_count_daily`, `bar_count_hourly`, `bar_count_minute`
- `created_at`, `updated_at` (via `TimestampMixin`)

**Indexes:** B-tree on `ticker`, composite on `(catalog_name, asset_class)`, unique on `(catalog_name, ticker)`

**Model:** Inherits `Base` + `TimestampMixin` (existing pattern). Dual repository: async for web, sync for CLI.

### API Endpoint Naming

**REST endpoints (JSON):**
- `GET /api/explorer/tickers` — paginated ticker list with search/filter/sort params
- `GET /api/chart/catalog/{ticker}` — windowed OHLCV data for chart rendering
- `GET /api/explorer/ticker/{ticker}/stats` — ticker statistics and supplementary data

**UI endpoints (HTML):**
- `GET /explorer` — full explorer page
- `GET /explorer/ticker-list` — HTMX fragment: filtered/paginated ticker table
- `GET /explorer/chart-panel` — HTMX fragment: chart + timeframe toolbar
- `GET /explorer/stats-panel` — HTMX fragment: ticker statistics
- `GET /explorer/supplementary` — HTMX fragment: collapsible supplementary data

**Rules:**
- All query params use `snake_case`: `asset_class`, `catalog`, `page`, `sort_by`
- Catalog is always a required query param on explorer endpoints (scopes all content)
- REST returns Pydantic response models. UI returns `templates.TemplateResponse()`

### CLI Command Pattern

```bash
ntrader import --format firstrate --catalog <name> <source-path> [--asset-class <type>] [--dry-run] [--timeframe <list>]
```

**Rules:**
- Follows existing Click command group pattern in `src/cli/commands/`
- `--format` flag enables future data providers without changing command structure
- `--catalog` maps to named Pydantic settings entry
- `--dry-run` validates without writing (dry-run output to stdout, not DB)
- Progress via structlog: one log line per ticker with status
- Exit codes: 0 (all success), 1 (some failures), 2 (fatal error)

### Import Pipeline Error Pattern

Per-ticker isolation with summary reporting:

**Rules:**
- Each ticker processes independently — one failure never blocks others
- Success: parse → validate → `catalog.write_data()` → verify row count → upsert metadata → log success
- Failure: catch exception → log error with ticker + reason → add to failure list → continue to next ticker
- Summary report at end: total tickers, successes, failures with reasons
- structlog fields: `ticker`, `asset_class`, `timeframe`, `row_count`, `status`, `error` (if failed)

### Chart API Response Format

```json
{
  "bars": [
    {"time": 1704067200, "open": 473.25, "high": 475.10, "low": 472.80, "close": 474.50, "volume": 45000000}
  ],
  "instrument_id": "SPY.ARCA",
  "timeframe": "1-DAY",
  "bar_count": 5523
}
```

**Rules:**
- Timestamps as UNIX seconds (int) — TradingView Lightweight Charts format
- Prices as floats in JSON (acceptable for charting display; decimal precision maintained in Parquet storage)
- Response wrapped in Pydantic model with `instrument_id`, `timeframe`, `bar_count` metadata
- Empty result (no data for range): return `{"bars": [], ...}` with 200 status, not 404

### HTMX Fragment Pattern

**Rules:**
- Fragments have no `<html>`/`<body>` tags — just the target container with its ID
- Container ID matches `hx-target` attribute on the triggering element
- Multi-target updates use `hx-swap-oob="true"` (ticker selection updates chart + stats + supplementary in one request)
- Search uses `hx-trigger="keyup changed delay:300ms"` for debounce
- Loading indicators via `hx-indicator` class (thin progress bar, not spinner overlay on chart)
- All fragment endpoints include `catalog` param — no implicit catalog state

### Enforcement

These patterns are enforced by:
- Existing ruff/mypy/pytest quality gates (code style, types, tests)
- Alembic migration review (DB naming)
- PR review checklist: parser implements full interface, tests cover happy + failure paths, CLI `--help` is complete
- project-context.md rules remain authoritative for general patterns (imports, logging, testing, etc.)

## Project Structure & Boundaries

### New Files & Directories (Feature Addition)

The existing project structure is documented in `docs/agent/architecture.md`. Below are the **new** files and directories added by the FirstRate Data import + explorer feature, shown within the existing tree:

```
src/
├── config.py                              # MODIFIED: add CatalogSettings, FirstRateSettings
├── api/
│   ├── models/
│   │   └── explorer.py                    # NEW: Pydantic response models (TickerListResponse, ChartDataResponse, TickerStatsResponse)
│   ├── rest/
│   │   └── explorer.py                    # NEW: REST endpoints (tickers, chart data, stats)
│   └── ui/
│       └── explorer.py                    # NEW: HTML routes (explorer page, HTMX fragments)
├── cli/
│   └── commands/
│       └── import_data.py                 # NEW: Click import command group (import, dry-run)
├── db/
│   ├── models/
│   │   └── catalog_instrument.py          # NEW: CatalogInstrument SQLAlchemy model
│   └── repositories/
│       ├── catalog_instrument_repo.py     # NEW: async repository (web)
│       └── catalog_instrument_repo_sync.py # NEW: sync repository (CLI)
├── models/
│   └── catalog.py                         # NEW: domain models (CatalogConfig, ImportResult, ValidationResult, AssetClass enum)
├── services/
│   └── firstrate/                         # NEW: entire directory
│       ├── __init__.py
│       ├── parsers/
│       │   ├── __init__.py
│       │   ├── base.py                    # ABC + shared validation + parser registry
│       │   └── etf_parser.py              # Phase 1 ETF parser
│       ├── import_service.py              # Import orchestration
│       ├── instrument_mapper.py           # company_profiles.csv → DB
│       ├── catalog_manager.py             # Named catalog → ParquetDataCatalog
│       └── metadata_service.py            # catalog_instruments CRUD
templates/
│   └── explorer/                          # NEW: entire directory
│       ├── explorer.html                  # Full page extending base.html
│       ├── ticker_list.html               # HTMX fragment: paginated ticker table
│       ├── chart_panel.html               # HTMX fragment: chart + timeframe toolbar
│       ├── stats_panel.html               # HTMX fragment: ticker statistics
│       └── supplementary.html             # HTMX fragment: collapsible company/dividend/split
alembic/
│   └── versions/
│       └── 005_add_catalog_instruments.py # NEW: migration for catalog_instruments table
tests/
├── unit/
│   ├── services/
│   │   └── firstrate/                     # NEW: parser unit tests, validation tests
│   │       ├── test_etf_parser.py
│   │       ├── test_instrument_mapper.py
│   │       └── test_import_service.py
│   └── api/
│       └── test_explorer_models.py        # NEW: response model tests
├── component/
│   └── services/
│       └── firstrate/
│           └── test_catalog_manager.py    # NEW: catalog manager with test doubles
├── integration/
│   └── services/
│       └── firstrate/
│           └── test_import_pipeline.py    # NEW: end-to-end import with real Parquet (--forked)
├── api/
│   └── test_explorer_endpoints.py         # NEW: REST + UI endpoint tests
└── ui/
    └── test_explorer_ui.py                # NEW: agent-browser UI tests
```

### Existing Files Modified

| File | Modification |
|---|---|
| `src/config.py` | Add `CatalogSettings` (named catalogs) and `FirstRateSettings` (source paths, defaults) |
| `src/api/web.py` | Register explorer REST + UI routers |
| `src/cli/main.py` | Register `import` command group |
| `templates/partials/nav.html` | Add "Explorer" nav link |
| `templates/base.html` | No change needed (TradingView Charts already loaded) |

### Architectural Boundaries

**Import Pipeline Boundary (CLI → Services → Parquet + DB):**
```
CLI command (import_data.py)
  → ImportService (orchestration)
    → Parser (CSV → list[Bar])
    → CatalogManager (settings → ParquetDataCatalog)
    → ParquetDataCatalog.write_data(bars)
    → MetadataService (upsert catalog_instruments row)
    → InstrumentMapper (company_profiles → DB)
```
- CLI layer handles argument parsing, progress output, exit codes
- ImportService handles the per-ticker loop and error isolation
- Parsers are stateless — given a file path, produce bars
- No direct DB access from CLI command — always through services

**Explorer Boundary (Web → Services → DB + Parquet):**
```
UI route (explorer.py)
  → MetadataService (ticker list, search, filter, stats from DB)
  → templates.TemplateResponse() (HTML)

REST route (explorer.py)
  → MetadataService (resolve ticker → Nautilus ID)
  → CatalogManager (get ParquetDataCatalog instance)
  → catalog.bars() (windowed Parquet read)
  → ChartDataResponse (Pydantic model → JSON)
```
- UI routes return HTML (full pages + HTMX fragments)
- REST routes return JSON (chart data, ticker metadata)
- Both use async DB access via existing DI chain
- Chart data reads Parquet directly — never goes through DB

**Data Boundary:**
```
         ┌─────────────────┐
         │   PostgreSQL     │
         │ catalog_instruments │ ← MetadataService (async + sync)
         └────────┬────────┘
                  │ ticker/date range/bar counts
                  │
    ┌─────────────┼─────────────┐
    │             │             │
    ▼             ▼             ▼
 Explorer     Import CLI    Backtest
 (search,     (idempotent   (instrument
  filter,      check,        ID lookup)
  stats)       upsert)

         ┌─────────────────┐
         │  Parquet Catalog │
         │ (per named catalog) │ ← CatalogManager
         └────────┬────────┘
                  │ bar data (OHLCV)
                  │
    ┌─────────────┼─────────────┐
    │             │             │
    ▼             ▼             ▼
 Chart API    Import CLI    BacktestEngine
 (windowed    (write_data)  (BacktestDataConfig)
  reads)
```

### Requirements to Structure Mapping

| FR Category | Files |
|---|---|
| **Import Pipeline (FR1-FR14)** | `cli/commands/import_data.py`, `services/firstrate/import_service.py`, `services/firstrate/parsers/*.py`, `services/firstrate/catalog_manager.py` |
| **Data Explorer (FR15-FR21)** | `api/ui/explorer.py`, `api/rest/explorer.py`, `templates/explorer/*.html`, `api/models/explorer.py` |
| **Catalog Integration (FR22-FR24)** | `services/firstrate/catalog_manager.py`, `services/firstrate/metadata_service.py`, `config.py` (CatalogSettings) |
| **Backtest Verification (FR25-FR27)** | No new files — uses existing `BacktestOrchestrator` with catalog from `CatalogManager` |
| **Supplementary Data (FR28-FR31)** | `services/firstrate/instrument_mapper.py`, `db/models/catalog_instrument.py` (company profile fields) |
| **Import Config (FR32-FR34)** | `cli/commands/import_data.py` (CLI flags), `config.py` (FirstRateSettings) |

### Cross-Cutting Integration Points

**CatalogManager** is the central integration point — consumed by:
- Import pipeline (get catalog for writing)
- Chart API (get catalog for reading)
- Explorer UI (list available catalogs for selector dropdown)
- Backtest integration (provide catalog path for BacktestDataConfig)

**MetadataService** bridges import and explorer:
- Import writes metadata after successful Parquet write
- Explorer reads metadata for search, filter, stats display
- Async repository for web, sync repository for CLI

**CatalogInstrument DB model** is the shared data contract:
- Written by import pipeline (via sync repo)
- Read by explorer (via async repo)
- Queried by chart API (ticker → Nautilus ID resolution)

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility:**
All 8 ADRs compose without conflict. PostgreSQL handles metadata queries; Parquet handles bar data. No technology conflicts — all decisions use the existing stack. Pydantic settings → CatalogManager → ParquetDataCatalog is a clean resolution chain. Parser registry → import service → dual-write (Parquet + DB) flows naturally.

**Pattern Consistency:**
All naming, file organization, DB conventions, API patterns, and HTMX fragment patterns align with established codebase conventions. No new patterns contradict existing project-context.md rules.

**Structure Alignment:**
Project structure supports all decisions — every ADR maps to specific files in the structure. Boundaries are clear: parsers don't write to DB, CLI doesn't access DB directly, chart API reads Parquet not DB, explorer queries DB not Parquet.

### Requirements Coverage Validation ✅

**Functional Requirements:** All 34 FRs mapped to specific architectural components and files. No gaps.

**Non-Functional Requirements:** All performance targets (<2s chart, <300ms search, <500ms page render), reliability requirements (fault isolation, atomic writes), and integration requirements (Parquet compatibility, existing patterns) are architecturally addressed.

**Coverage Matrix:**
- FR1-FR14 (Import Pipeline): Covered by ADR-4 (parsers), ADR-5 (idempotent import), ADR-6 (mapping), CLI command
- FR15-FR21 (Explorer): Covered by ADR-1 (metadata), ADR-7 (chart reads), ADR-8 (search), HTMX templates
- FR22-FR24 (Catalog Integration): Covered by ADR-2 (named catalogs), CatalogManager
- FR25-FR27 (Backtest Verification): Covered by existing BacktestOrchestrator + CatalogManager
- FR28-FR34 (Supplementary + Config): Covered by ADR-3 (single table), ADR-6 (mapper), CLI flags

### Implementation Readiness Validation ✅

**Decision Completeness:** All critical decisions documented with rationale, trade-offs, and implementation implications. Technology versions inherited from existing project-context.md (verified and current).

**Structure Completeness:** Every new file defined with purpose. Existing files to modify identified. Test locations specified per tier (unit, component, integration, API, UI).

**Pattern Completeness:** Parser interface contract defined. Error handling pattern specified. API response format documented with examples. HTMX fragment rules established. CLI command structure with flags and exit codes specified.

### Gap Analysis Results

**Critical Gaps:** None.

**Minor Observations (Non-Blocking):**

1. **Supplementary data tables:** Dividend/split history not defined as separate tables. Acceptable for Phase 1 (display-only in explorer). Future phases add `catalog_dividends` and `catalog_splits` tables when needed
2. **Metadata rebuild command:** Recovery mechanism mentioned in ADR-1 but not explicitly in project structure. Simple CLI subcommand added when needed
3. **Multi-ticker backtest from explorer:** Architecture supports it (BacktestDataConfig accepts list of instrument_ids), but explorer-to-backtest bridge carries single ticker. Multi-ticker selection is a future UX enhancement

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] Project context thoroughly analyzed (88 existing rules + PRD + UX spec)
- [x] Scale and complexity assessed (high — 6 schemas, 387GB, 20K+ tickers)
- [x] Technical constraints identified (Nautilus C extensions, LogGuard, engine lifecycle)
- [x] Cross-cutting concerns mapped (instrument IDs, catalog format, metadata, validation)

**✅ Technical Foundation**
- [x] Nautilus multi-instrument backtest support confirmed via documentation research
- [x] ParquetDataCatalog API verified (write_data, bars with time-range, instruments)
- [x] Existing codebase patterns catalogued (DI chain, dual DB, CLI, HTMX)

**✅ Architectural Decisions**
- [x] 8 ADRs documented with rationale and trade-offs
- [x] All decisions use existing technology stack (no new dependencies)
- [x] Integration patterns defined (CatalogManager, MetadataService)
- [x] Performance approach specified for all NFR targets

**✅ Implementation Patterns**
- [x] Parser registry pattern with interface contract
- [x] File organization rules (src/services/firstrate/)
- [x] Database naming and schema defined
- [x] API endpoint naming and response formats
- [x] CLI command structure with flags and exit codes
- [x] Import error handling pattern
- [x] HTMX fragment rules

**✅ Project Structure**
- [x] All new files and directories defined with purpose
- [x] Existing files to modify identified
- [x] Architectural boundaries documented (import, explorer, data)
- [x] FR-to-file mapping complete
- [x] Cross-cutting integration points specified (CatalogManager, MetadataService)

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** High — brownfield extension with proven patterns, all decisions leverage existing infrastructure, Nautilus API capabilities confirmed via documentation

**Key Strengths:**
- No new frameworks or infrastructure — every decision extends established patterns
- Clean separation: Parquet for bar data, PostgreSQL for metadata, each queried by appropriate consumers
- Phased approach: architecture supports all 6 phases but Phase 1 (ETFs) is a self-contained vertical slice
- Single table simplicity for Phase 1 with clear evolution path for supplementary data

**Areas for Future Enhancement:**
- Normalized supplementary data tables when dividends/splits grow beyond display metadata
- Multi-ticker selection in explorer for batch backtest launch
- Metadata rebuild CLI command as a recovery tool
- 15-min resampling if product scope confirms it

### Implementation Handoff

**AI Agent Guidelines:**
- Follow all 8 ADRs exactly as documented — they are decisions, not suggestions
- Use implementation patterns from this document AND project-context.md (88 rules)
- Respect boundaries: parsers parse, import service orchestrates, metadata service persists
- TDD mandatory — write failing test before implementing each component
- Test tiers: unit for parsers/models, component for services with test doubles, integration for real Parquet + DB (--forked), API for endpoints, UI for explorer via agent-browser

**First Implementation Priority:**
1. Alembic migration: `catalog_instruments` table
2. Pydantic settings: `CatalogSettings` in `config.py`
3. Domain models: `AssetClass` enum, `CatalogConfig`, `ValidationResult` in `models/catalog.py`
4. ETF parser with TDD (base class + ETF implementation)
5. Import service + CLI command
6. Explorer API + UI
