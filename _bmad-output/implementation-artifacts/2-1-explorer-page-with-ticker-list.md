# Story 2.1: Explorer Page with Ticker List

Status: review

## Story

As a system operator,
I want a web-based explorer page where I can browse, search, and filter all imported tickers with inline metadata,
So that I can quickly answer "what data do I have?" across my catalogs.

## Acceptance Criteria

1. **Page load:** `GET /explorer` renders a full page extending `base.html` with catalog selector dropdown, auto-focused search input, asset class filter pills with ticker counts, and a paginated ticker list. Navigation bar shows "Explorer" as active item.

2. **Ticker row display:** Each row shows: ticker symbol (font-mono bold), name, asset class badge (colored per type), date range (font-mono), coverage bar, and bar counts "D / 1H / 5m / 1m" (font-mono compact, all 4 timeframes). Paginated at 25 rows/page with sort headers (symbol, date range, bar count).

3. **Search:** Typing in search input filters the ticker list in real-time via HTMX partial swap (debounced 300ms, prefix match, case insensitive). Pagination resets to page 1. Search text preserved in URL param `?search=SP`.

4. **Asset class filter:** "All" pill is default active. Clicking an asset class pill filters via HTMX partial swap. Active pill shows `bg-blue-500`. Filter preserved in URL `?asset_class=stock`. Clicking "All" clears the filter. Search and filter combine.

5. **Catalog selector:** Switching catalogs refreshes full explorer content (ticker list, stats cleared). Selection preserved in URL `?catalog=name`.

6. **Deep linking:** URL params (catalog, search, asset_class, page, sort_by) restore exact state on page load/refresh.

7. **Breadcrumbs:** Explorer > {catalog name}. Each segment clickable, using existing `breadcrumbs.html` partial.

8. **REST endpoint:** `GET /api/explorer/tickers?catalog=...&search=...&asset_class=...&page=...&sort_by=...` returns paginated `TickerListResponse` (Pydantic). Search uses SQL ILIKE prefix match on indexed ticker column. Response <500ms paginated, <300ms search.

## Tasks / Subtasks

- [x] Task 1: Add "explorer" to `NavigationState.active_page` pattern (AC: #1)
  - [x] Update regex in `src/api/models/navigation.py:58` to include `explorer`
  - [x] Update nav.html to add Explorer link (between Dashboard and Backtests, href="/explorer")
- [x] Task 2: Create explorer UI router and DI dependencies (AC: #1, #8)
  - [x] Create `src/api/ui/explorer.py` with `APIRouter()`
  - [x] Register in `src/api/web.py` with `prefix="/explorer"`
  - [x] Add DI functions for `MetadataService` and `CatalogManager` in `src/api/dependencies.py`
  - [x] Create type aliases: `Metadata = Annotated[MetadataService, Depends(...)]` and `CatalogMgr = Annotated[CatalogManager, Depends(...)]`
- [x] Task 3: Create Pydantic response/presentation models (AC: #2, #8)
  - [x] Create `src/api/models/explorer.py` with: `TickerRow`, `TickerListResponse`, `ExplorerPageState` (catalog, search, asset_class, page, sort_by, total_count, total_pages)
  - [x] `TickerRow` fields: ticker, name, asset_class, date_range_start, date_range_end, bar_count_daily, bar_count_hourly, bar_count_minute, bar_count_5min, nautilus_id, coverage_pct (computed)
- [x] Task 4: Implement REST endpoint `GET /api/explorer/tickers` (AC: #8)
  - [x] Create `src/api/rest/explorer.py` with router
  - [x] Register in `src/api/web.py` with `prefix="/api"` 
  - [x] Implement paginated query: catalog filter + optional asset_class + optional search (ILIKE prefix) + sort + limit/offset
  - [x] Add `count()` query to `CatalogInstrumentRepository` for total count (needed for pagination)
  - [x] Add `list_by_catalog_with_search()` to repository: combines catalog filter + ILIKE search + asset_class filter + sort + pagination
  - [x] Add `count_by_catalog()` and `count_asset_classes()` to repository for pill counts
- [x] Task 5: Create explorer templates (AC: #1, #2, #3, #4, #5, #6, #7)
  - [x] Create `templates/explorer/explorer.html` — full page extending base.html
  - [x] Create `templates/explorer/ticker_list.html` — HTMX fragment with table rows + pagination
  - [x] Include HTMX attributes: `hx-get="/explorer/ticker-list"`, `hx-trigger="keyup changed delay:300ms"` on search, `hx-target="#ticker-list"`
  - [x] Asset class pills as `<button>` with `hx-get` targeting ticker list fragment
  - [x] Catalog selector as `<select>` with `hx-get` on change, targeting full content area
  - [x] Coverage bar: 4px tall, `bg-slate-800` track, `bg-green-500` fill proportional to date range
  - [x] Asset class badges: Stock=blue, ETF=slate, Futures=amber, FX=emerald, Crypto=purple, Index=cyan, Delisted=gray
  - [x] Bar counts: compact format "8K / 51K / 200K / 3.1M" (D / 1H / 5m / 1m)
- [x] Task 6: Implement UI route handlers (AC: #1, #3, #4, #5, #6, #7)
  - [x] `GET /explorer` — full page with initial ticker list, resolves catalog list from `CatalogManager.list_catalogs()`
  - [x] `GET /explorer/ticker-list` — HTMX fragment returning only the ticker table + pagination
  - [x] Both endpoints accept query params: catalog, search, asset_class, page, sort_by
  - [x] Build `NavigationState` with `active_page="explorer"` and breadcrumbs
- [x] Task 7: Write tests (all ACs)
  - [x] Unit tests for Pydantic models (TickerRow, TickerListResponse, ExplorerPageState)
  - [x] Unit tests for repository count/search methods
  - [x] Component tests for REST endpoint (mock DB, verify response shape, pagination, search)
  - [x] Component tests for UI routes (mock service, verify template rendering, HTMX fragments)

## Dev Notes

### Architecture Compliance

**Data boundary (from architecture.md):**
```
UI route (explorer.py)
  → MetadataService (ticker list, search, filter from DB)
  → templates.TemplateResponse() (HTML)

REST route (explorer.py)
  → CatalogInstrumentRepository (paginated query)
  → TickerListResponse (Pydantic → JSON)
```

- UI routes return HTML (full pages + HTMX fragments)
- REST routes return JSON (for potential future API consumers)
- Both use async DB access via existing DI chain
- Ticker metadata comes from `catalog_instruments` table — NOT from Parquet catalog reads
- Catalog list comes from `CatalogManager.list_catalogs()` (filesystem discovery)

**Existing services to reuse — DO NOT recreate:**
- `CatalogInstrumentRepository` (`src/db/repositories/catalog_instrument_repository.py`) — already has `list_by_catalog()`, `search()`, `get_by_ticker()`. Extend with count/combined-search methods.
- `MetadataService` (`src/services/firstrate/metadata_service.py`) — async/sync facade over repository. Add new async methods that delegate to new repository methods.
- `CatalogManager` (`src/services/firstrate/catalog_manager.py`) — `list_catalogs()` returns catalog names from filesystem. **WARNING:** Importing `CatalogManager` pulls in `nautilus_trader.persistence.catalog.parquet` at module level. For Story 2-1, only `list_catalogs()` is needed (pure filesystem, no Nautilus). Preferred approach: write a lightweight `list_catalog_dirs(base_path: Path) -> list[str]` utility function that avoids the Nautilus import, OR accept the Nautilus import overhead if LogGuard is already initialized in `web.py`.
- `CatalogSettings` (`src/config.py`) — `catalog_base_path` and `default_catalog_name` for defaults.

**Existing `search()` uses substring match — DO NOT reuse for AC #3:**
The existing `CatalogInstrumentRepository.search()` method uses `%{query}%` (substring ILIKE). Story 2-1 requires **prefix match** (`{query}%`) per AC #3 and the epics spec. Create a new `list_by_catalog_with_search()` method with prefix matching instead of reusing `search()`.

**`bar_count_5min` gap in existing `upsert()`:**
The `CatalogInstrumentRepository.upsert()` method updates `bar_count_daily`, `bar_count_hourly`, `bar_count_minute` but does NOT update `bar_count_5min` (added later in migration `67772db31d8d`). This means 5min counts may show 0 for instruments upserted before the fix. This is a pre-existing issue, not for this story to fix — just display whatever value is in the DB.
- `NavigationState` + `BreadcrumbItem` (`src/api/models/navigation.py`) — existing nav pattern.
- `get_db()` dependency (`src/api/dependencies.py`) — async session provider.

**Key model: `CatalogInstrument` columns available (from `src/db/models/catalog_instrument.py`):**
- `ticker` (String 20, indexed), `nautilus_id`, `asset_class` (String 20), `catalog_name` (String 50)
- `exchange`, `name` (String 200), `sector`, `industry`, `ipo_date`
- `date_range_start`, `date_range_end` (TIMESTAMP TZ)
- `bar_count_daily`, `bar_count_hourly`, `bar_count_minute`, `bar_count_5min` (Integer)
- Indexes: `ix_catalog_instruments_ticker`, `ix_catalog_instruments_catalog_asset`, `uq_catalog_instruments_catalog_ticker`

### NavigationState Update Required

The `active_page` field in `src/api/models/navigation.py` (the `NavigationState` class) has a Pydantic regex pattern:
```python
pattern=r"^(dashboard|backtests|run_backtest|data|docs)$"
```
**Must add `explorer` to this pattern** or `NavigationState(active_page="explorer")` will raise `ValidationError`. Note: existing `nav.html` already has "Data" and "Docs" links at `/data` and `/docs` but no route handlers exist for them — Explorer replaces or coexists with "Data".

### DI Wiring for New Dependencies

The existing `src/api/dependencies.py` has `get_data_catalog_service()` but Story 2-1 needs:
1. **`MetadataService`** — requires `CatalogInstrumentRepository(session)`. Wire as: `get_db → session → CatalogInstrumentRepository(session) → MetadataService(async_repo=repo)`.
2. **`CatalogManager`** — requires `CatalogSettings.catalog_base_path`. Wire as: `CatalogSettings() → Path(catalog_base_path) → CatalogManager(base_path)`.

Follow the existing pattern: create `get_metadata_service()` and `get_catalog_manager()` functions, then type aliases.

### HTMX Fragment Pattern (from architecture.md)

- Fragments have no `<html>`/`<body>` tags — just the target container with its ID
- Container ID matches `hx-target` attribute on the triggering element
- Search uses `hx-trigger="keyup changed delay:300ms"` for debounce
- Loading indicators via `hx-indicator` class (thin progress bar, not spinner overlay)
- **All fragment endpoints include `catalog` param — no implicit catalog state**
- Use `hx-push-url="true"` to preserve state in browser URL

### Template File Structure (from architecture.md)

```
templates/explorer/
├── explorer.html           # Full page extending base.html
├── ticker_list.html        # HTMX fragment: filtered/paginated ticker table
```
Chart panel, stats panel, supplementary templates are Story 2-2, 2-3, 2-4 — do NOT create them now. But plan the explorer.html layout with `<div id="chart-panel">`, `<div id="stats-panel">` placeholder containers for future HTMX targets.

### Tailwind CSS Classes (from UX spec)

- Page background: `bg-slate-950 text-slate-100` (already in base.html body)
- Inputs: `bg-slate-800 border-slate-700 text-slate-100 placeholder-slate-500`
- Table rows: `border-b border-slate-800`, hover `bg-slate-800`
- Selected row: `bg-slate-800` or `bg-blue-900/50`
- Active pill: `bg-blue-500 border-blue-500 text-white`
- Inactive pill: `border-slate-700 text-slate-400`
- Badge colors: Stock=`bg-blue-500/20 text-blue-400`, ETF=`bg-slate-500/20 text-slate-400`
- Coverage bar: track `bg-slate-800` h-1, fill `bg-green-500`
- Stat labels: `text-xs text-slate-500 uppercase`
- Font-mono for: ticker symbol, date ranges, bar counts

### Empty State Handling

- **No catalogs found:** Show message "No catalogs configured. Run `ntrader import` to create one." Use existing `EmptyStateMessage` pattern from `src/api/models/common.py`.
- **Search returns 0 results:** Show "No tickers found for '{query}'" in the ticker list area.
- **Catalog has no instruments:** Show "No imported tickers in this catalog."
- **No asset class matches:** Same as search empty state with filter context.

### Performance Targets

- Initial page load: <1s (NFR4 from PRD)
- Paginated results: <500ms
- Search filtering: <300ms
- The `ix_catalog_instruments_ticker` index supports ILIKE prefix queries
- The `ix_catalog_instruments_catalog_asset` index supports catalog + asset_class filtering
- Pagination via SQL LIMIT/OFFSET is acceptable for the expected dataset size (~7,800 Stocks tickers)

### Stocks Pivot Context

Phase 1 pivoted from ETFs to Stocks (commit `3028473`, 2026-04-11). In Phase 1:
- Primary asset class is "STOCK" (not "ETF")
- Asset class pills will show "Stock" as the dominant filter
- The `company_profiles.csv` ships with the Stocks bundle, so `name`, `exchange`, `sector`, `industry` fields are populated
- ETF data may exist in some catalogs but without metadata (no company_profiles.csv) — gracefully handle empty name/exchange

### Anti-Patterns to Avoid

- **Do NOT instantiate `DataCatalogService` for ticker list** — it creates a Nautilus `ParquetDataCatalog` and tries to connect to IBKR. The ticker list reads from PostgreSQL via `CatalogInstrumentRepository`.
- **Do NOT use `get_data_catalog_service()`** from dependencies.py for this story — that dependency creates `DataCatalogService` which initializes Nautilus logging and IBKR/Kraken clients. Use `MetadataService` + `CatalogManager` instead.
- **Do NOT hardcode catalog names** — discover from `CatalogManager.list_catalogs()` and `CatalogSettings.default_catalog_name`.
- **Do NOT create new DB tables or migrations** — `catalog_instruments` already has everything needed.
- **Do NOT import from `src.core.strategies.custom.*`** — custom/ is a git submodule.
- **Ruff auto-linter footgun:** Make dependent changes in a single edit (e.g., adding imports + their usage together). Re-read file after each edit.

### Project Structure Notes

- New files follow existing patterns: `src/api/ui/explorer.py` (matches `dashboard.py`, `backtests.py`), `src/api/rest/explorer.py` (matches `equity.py`, `timeseries.py`), `src/api/models/explorer.py` (matches `dashboard.py`, `backtest_list.py`)
- Templates in `templates/explorer/` (matches `templates/backtests/`)
- No new directories needed beyond `templates/explorer/`

### Previous Story Intelligence (Epic 1 Learnings)

From Epic 1 retrospective:
1. **TDD held across all 7 stories** — maintain the same discipline for UI code.
2. **Ruff auto-linter strips imports** — appeared in Stories 1-2 and 1-7. Bundle imports with first usage in single edits.
3. **SQLAlchemy sync-session quirks** — Story 2-1 uses async repo, which is cleaner. But watch for detached instances if doing complex queries.
4. **Story intelligence forwarding works** — concrete learnings from previous stories prevent repeat mistakes.
5. **Code review was substantive** — expect review to catch real issues; write tests that cover edge cases.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 2, Story 2.1]
- [Source: _bmad-output/planning-artifacts/architecture.md#Explorer Boundary, API Endpoint Naming, HTMX Fragment Pattern, Template Structure]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Layout Wireframe, Component Specifications, Navigation Patterns]
- [Source: _bmad-output/planning-artifacts/prd.md#FR15-FR21, MVP Strategy, Data Verification Journey]
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-04-11.md#Challenges, Epic 2 Preview]
- [Source: src/api/models/navigation.py — NavigationState active_page pattern]
- [Source: src/db/repositories/catalog_instrument_repository.py — existing repo methods]
- [Source: src/services/firstrate/metadata_service.py — MetadataService async facade]
- [Source: src/services/firstrate/catalog_manager.py — CatalogManager.list_catalogs()]
- [Source: src/api/dependencies.py — existing DI pattern]
- [Source: src/db/models/catalog_instrument.py — CatalogInstrument ORM model]

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6 (1M context)

### Debug Log References
- Ruff auto-linter repeatedly stripped unused imports — resolved by bundling imports + usage in single edits
- Avoided CatalogManager import (Nautilus dependency) by creating lightweight `list_catalog_dirs()` utility

### Completion Notes List
- Task 1: Added "explorer" to NavigationState regex pattern; added Explorer nav link between Dashboard and Backtests in both desktop and mobile menus
- Task 2: Created explorer UI router, registered in web.py; added DI functions for MetadataService, catalog list, and default catalog; used `list_catalog_dirs()` to avoid Nautilus import
- Task 3: Created TickerRow (with computed coverage_pct), TickerListResponse, and ExplorerPageState Pydantic models
- Task 4: Added `count_by_catalog()`, `count_asset_classes()`, and `list_by_catalog_with_search()` to CatalogInstrumentRepository; added corresponding MetadataService async methods; created REST endpoint at GET /api/explorer/tickers
- Task 5: Created explorer.html full page (catalog selector, search input, asset class pills, placeholder panels for future stories) and ticker_list.html HTMX fragment (table with sort headers, pagination, all empty states)
- Task 6: Implemented GET /explorer (full page) and GET /explorer/ticker-list (HTMX fragment) with deep linking via URL params
- Task 7: 4 unit tests (navigation), 9 unit tests (explorer models), 6 component tests (repository), 25 component tests (REST + UI routes) = 44 new tests, all passing

### Change Log
- 2026-04-12: Story 2-1 implementation complete — explorer page with ticker list, search, filter, pagination, HTMX fragments

### File List
- src/api/models/navigation.py (modified — added "explorer" to active_page pattern)
- src/api/models/explorer.py (new — TickerRow, TickerListResponse, ExplorerPageState)
- src/api/dependencies.py (modified — added MetadataService, CatalogList, DefaultCatalog DI)
- src/api/web.py (modified — registered explorer UI and REST routers)
- src/api/ui/explorer.py (new — UI route handlers for /explorer)
- src/api/rest/explorer.py (new — REST endpoint for /api/explorer/tickers)
- src/db/repositories/catalog_instrument_repository.py (modified — added count_by_catalog, count_asset_classes, list_by_catalog_with_search)
- src/services/firstrate/metadata_service.py (modified — added list_instruments_with_search, count_asset_classes)
- templates/partials/nav.html (modified — added Explorer link)
- templates/explorer/explorer.html (new — full page template)
- templates/explorer/ticker_list.html (new — HTMX fragment template)
- tests/unit/api/test_navigation_models.py (new — 4 tests)
- tests/unit/api/test_explorer_models.py (new — 9 tests)
- tests/component/db/test_catalog_instrument_repository.py (modified — 6 new tests)
- tests/component/api/test_explorer_routes.py (new — 25 tests)
