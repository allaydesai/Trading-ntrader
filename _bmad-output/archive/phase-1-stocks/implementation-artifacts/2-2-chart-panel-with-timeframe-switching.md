# Story 2.2: Chart Panel with Timeframe Switching

Status: review

## Story

As a system operator,
I want to load an interactive candlestick chart for any ticker at any stored timeframe,
so that I can visually verify imported data against TradingView for trust in data quality.

## Acceptance Criteria

1. **Given** the ticker list is displayed **When** the user clicks a ticker row **Then** a chart panel loads below the ticker list with the daily timeframe as default **And** the chart, stats, and supplementary panels update simultaneously via `hx-swap-oob` **And** the selected ticker row is highlighted (`bg-slate-800`/`blue-900`) **And** the URL updates with `?ticker={symbol}&tf=D`

2. **Given** a chart is displayed for a ticker **When** a timeframe toolbar is shown above the chart with buttons (1m, 5m, 1H, D) **Then** the active timeframe button is highlighted (`bg-blue-500 text-white`) **And** timeframe buttons for unavailable data are disabled (`opacity-50 cursor-not-allowed`)

3. **Given** a chart is displayed **When** the user clicks a different timeframe button (e.g., 1H) **Then** the chart panel swaps via `hx-get` with the new timeframe data **And** the URL updates with `?tf=1H` **And** the active timeframe button updates

4. **Given** a chart is displayed **When** the user scrolls or zooms the chart **Then** TradingView Lightweight Charts handles pan/zoom natively at 60fps **And** progressive loading fetches additional data via time-range API slicing as the user pans to earlier dates

5. **Given** the REST endpoint `GET /api/chart/catalog/{ticker}` **When** called with query params (catalog, tf, start, end) **Then** it resolves the ticker to a Nautilus instrument ID from the database **And** reads windowed bar data via `catalog.bars()` with time-range filtering **And** returns a `ChartDataResponse` (Pydantic model) with bars as JSON array of `{time, open, high, low, close, volume}`, `instrument_id`, `timeframe`, and `bar_count` **And** response time is under 2 seconds for any single ticker/timeframe combination

6. **Given** the chart panel template **When** rendered **Then** the chart is full-width, maximizing viewport for visual verification **And** a thin 2px animated loading bar appears at the top during initial chart load (no spinner overlay)

## Tasks / Subtasks

- [x] Task 1: Pydantic response models (AC: #5)
  - [x] 1.1 Add `ChartDataResponse` model to `src/api/models/explorer.py` with fields: bars (list of Candle), instrument_id (str), timeframe (str), bar_count (int)
  - [x] 1.2 Add `ExplorerTimeframe` enum mapping display labels (D, 1H, 5m, 1m) to Nautilus bar type specs
  - [x] 1.3 Unit tests for new models
- [x] Task 2: REST chart data endpoint (AC: #5)
  - [x] 2.1 Create `GET /api/chart/catalog/{ticker}` in `src/api/rest/explorer.py`
  - [x] 2.2 Resolve ticker → `nautilus_id` via `MetadataService.get_instrument(catalog, ticker)`
  - [x] 2.3 Read bars from Parquet via `DataCatalogService.query_bars(instrument_id, start, end, bar_type_spec)`
  - [x] 2.4 Convert Nautilus Bar objects to Candle format (nanosecond `ts_event` → Unix seconds)
  - [x] 2.5 Return 200 with empty bars array (not 404) when no data for range
  - [x] 2.6 Component tests with mocked dependencies
- [x] Task 3: Chart panel HTMX fragment template (AC: #1, #2, #6)
  - [x] 3.1 Create `templates/explorer/chart_panel.html` — HTMX fragment (no html/body tags), container `<div id="chart-panel">`
  - [x] 3.2 Timeframe toolbar: button group (1m, 5m, 1H, D) with active/disabled states
  - [x] 3.3 Chart container div for TradingView Lightweight Charts initialization
  - [x] 3.4 Loading bar: 2px animated bar at top of chart panel (CSS animation, not spinner overlay)
  - [x] 3.5 Inline `<script>` to initialize TradingView chart with candlestick series from server-provided JSON
- [x] Task 4: UI route for chart panel fragment (AC: #1, #3)
  - [x] 4.1 Add `GET /explorer/chart-panel` route to `src/api/ui/explorer.py`
  - [x] 4.2 Accept params: catalog, ticker, tf (default "D")
  - [x] 4.3 Resolve ticker → instrument, fetch bar data, determine available timeframes from DB bar counts
  - [x] 4.4 Return `TemplateResponse("explorer/chart_panel.html", ...)` with bars JSON, active tf, available tfs
  - [x] 4.5 Component tests for route with mocked dependencies
- [x] Task 5: Ticker row click → chart load wiring (AC: #1)
  - [x] 5.1 Add `hx-get="/explorer/chart-panel"` to ticker rows in `ticker_list.html`
  - [x] 5.2 Use `hx-target="#chart-panel"` and `hx-swap="innerHTML"` and `hx-push-url="true"`
  - [x] 5.3 Include catalog param via `hx-vals` with tojson filter
  - [x] 5.4 Add `hx-vals` with ticker symbol
  - [x] 5.5 Selected row highlight: toggle `bg-slate-800` + `ring-1 ring-blue-900` via server-rendered `selected_ticker`
- [x] Task 6: Timeframe button switching (AC: #2, #3)
  - [x] 6.1 Timeframe buttons in chart_panel.html use `hx-get="/explorer/chart-panel"` with `tf` param
  - [x] 6.2 Active button server-rendered based on current tf param
  - [x] 6.3 Disabled buttons for timeframes where `bar_count_*` is 0 or null
- [x] Task 7: Progressive chart loading (AC: #4)
  - [x] 7.1 Chart JS: detect pan to visible range boundary → fetch more data via `/api/chart/catalog/{ticker}` with narrowed time range
  - [x] 7.2 Append fetched data to existing candlestick series (deduplicate by timestamp)
  - [x] 7.3 Use TradingView `subscribeVisibleTimeRangeChange` for boundary detection
- [x] Task 8: URL state management (AC: #1, #3)
  - [x] 8.1 Ticker selection and timeframe changes push `?ticker=...&tf=...` to URL via `hx-push-url`
  - [x] 8.2 On page load with ticker/tf params, auto-load chart panel (server-side in explorer page route via `hx-trigger="load"`)

## Dev Notes

### Architecture Compliance

- **Data flow**: UI route (`explorer.py`) → MetadataService (ticker → nautilus_id from DB) → DataCatalogService (Parquet read) → chart_panel.html template
- **REST flow**: REST route (`explorer.py`) → MetadataService → DataCatalogService → `ChartDataResponse` JSON
- **No new DB tables** — uses existing `catalog_instruments` table for ticker resolution and bar count availability
- **Reuse existing patterns**: Follow `src/api/rest/timeseries.py` for Bar → Candle conversion (nanosecond `bar.ts_event / 1e9` → Unix seconds, `bar.open.as_double()`, etc.)
- **Reuse existing Candle model**: Import `Candle` from `src/api/models/chart_timeseries.py` — do NOT create a duplicate

### Critical Implementation Details

**Ticker → Nautilus ID Resolution (DB-based, not hardcoded)**:
- Use `MetadataService.get_instrument(catalog_name, ticker)` which returns `CatalogInstrument` with `.nautilus_id`
- Do NOT use `symbol_to_instrument_id()` from timeseries.py (hardcodes `.NASDAQ` suffix)
- The DB has the correct nautilus_id per ticker per catalog

**Bar Type Spec Mapping**:
- Reuse `TIMEFRAME_TO_BAR_TYPE` from `src/api/models/chart_timeseries.py`
- Map explorer timeframe labels: D → `1-DAY-LAST`, 1H → `1-HOUR-LAST`, 5m → `5-MINUTE-LAST`, 1m → `1-MINUTE-LAST`

**Available Timeframe Detection**:
- `CatalogInstrument` has `bar_count_daily`, `bar_count_hourly`, `bar_count_5min`, `bar_count_minute`
- A timeframe is available if its bar_count > 0 (or is not None/0)
- Disabled timeframe buttons must be rendered server-side based on these counts

**Chart Data Response Format** (per architecture ADR-7):
```json
{
  "bars": [{"time": 1704067200, "open": 473.25, "high": 475.10, "low": 472.80, "close": 474.50, "volume": 45000000}],
  "instrument_id": "SPY.ARCA",
  "timeframe": "1-DAY",
  "bar_count": 5523
}
```
- Timestamps as UNIX seconds (int) — TradingView Lightweight Charts format
- Prices as floats
- Empty result: return `{"bars": [], ...}` with 200 status, NOT 404

**TradingView Lightweight Charts Integration**:
- Already CDN-loaded in `base.html` (v5.0)
- Use `createChartWithDefaults()` from `static/js/charts-core.js` for consistent dark theme
- Use `CHART_COLORS` for candlestick colors (bullish: green-500, bearish: red-500)
- Chart initialization goes in inline `<script>` within `chart_panel.html` fragment (HTMX swaps don't execute external script tags reliably)
- For progressive loading: use `chart.timeScale().subscribeVisibleTimeRangeChange()` to detect pan boundaries

**HTMX Fragment Pattern** (established in Story 2-1):
- Fragment has NO `<html>`/`<body>` tags — just `<div id="chart-panel">` wrapper
- `hx-target="#chart-panel"` on triggering elements
- Always include `catalog` param (no implicit state)
- Use `hx-push-url="true"` to preserve URL state
- Loading indicator: `hx-indicator="#chart-loading-bar"` (thin 2px bar, not spinner)

**Multi-panel Update on Ticker Click**:
- Architecture specifies `hx-swap-oob` for simultaneous chart + stats + supplementary update
- For Story 2-2, chart panel is the primary target; stats panel (`#stats-panel`) can be updated via `hx-swap-oob="true"` in the chart-panel response if ready, or left empty for Story 2-3
- The chart-panel fragment response can include an `<div id="stats-panel" hx-swap-oob="true">` placeholder

### Existing Code to Reuse (DO NOT Duplicate)

| What | Where | Why |
|------|-------|-----|
| `Candle` model | `src/api/models/chart_timeseries.py` | Same OHLCV format for TradingView |
| `TIMEFRAME_TO_BAR_TYPE` | `src/api/models/chart_timeseries.py` | Timeframe → Nautilus bar type mapping |
| `Timeframe` enum | `src/api/models/chart_timeseries.py` | Validated timeframe values |
| `createChartWithDefaults()` | `static/js/charts-core.js` | Dark theme chart creation |
| `CHART_COLORS` | `static/js/charts-core.js` | Consistent color palette |
| `DataCatalog` dependency | `src/api/dependencies.py` | Parquet catalog access |
| `Metadata` dependency | `src/api/dependencies.py` | MetadataService for ticker resolution |
| Bar → Candle conversion | `src/api/rest/timeseries.py:107-120` | Pattern: `bar.ts_event / 1e9`, `bar.open.as_double()` |
| `DataNotFoundError` | `src/services/exceptions.py` | Error handling for missing data |

### File Structure

**New files:**
- `templates/explorer/chart_panel.html` — HTMX fragment: timeframe toolbar + chart container + inline JS

**Modified files:**
- `src/api/models/explorer.py` — Add `ChartDataResponse` model
- `src/api/rest/explorer.py` — Add `GET /api/chart/catalog/{ticker}` endpoint
- `src/api/ui/explorer.py` — Add `GET /explorer/chart-panel` route
- `templates/explorer/ticker_list.html` — Add `hx-get` to ticker rows for chart loading
- `templates/explorer/explorer.html` — Wire `#chart-panel` div with initial state handling (load from URL params)

**New test files:**
- `tests/unit/api/test_chart_data_models.py` — ChartDataResponse model validation
- `tests/component/api/test_chart_panel_routes.py` — Chart REST + UI route tests with mocked deps

### Project Structure Notes

- All new files align with existing structure: REST routes in `src/api/rest/`, UI routes in `src/api/ui/`, templates in `templates/explorer/`
- No new routers need registration in `web.py` — chart REST endpoint added to existing `explorer_api.router`, chart UI endpoint added to existing `explorer.router`
- No new dependencies needed — `DataCatalog` and `Metadata` type aliases already exist in `dependencies.py`

### Testing Requirements

- **Unit tests**: ChartDataResponse model validation, ExplorerTimeframe mapping
- **Component tests**: REST chart endpoint with mocked MetadataService + DataCatalogService; UI chart-panel route with mocked dependencies
- **Use `dependency_overrides`** with `MagicMock()` for API tests — always clean up in `finally` block (established pattern from Story 2-1)
- **Markers required**: `@pytest.mark.unit`, `@pytest.mark.component` on every test
- **TDD**: Write failing tests first, then implement

### Previous Story Intelligence (Story 2-1)

**Key learnings to apply:**
- Asset class pills were moved INTO the ticker_list.html fragment to fix stale counts — similarly, ensure chart panel state is fully self-contained in its fragment
- Sort_by had an arbitrary ORM attribute access vulnerability → fixed with allowlist. Apply same thinking to `tf` param validation
- XSS vector in `hx-vals` via asset_class → use Jinja `|tojson` filter for any user-controlled values in `hx-vals`
- Ruff auto-formatter strips "unused" imports — bundle import + usage in same edit
- `EXPLORER_PAGE_SIZE` extracted to models to avoid duplication — similarly, put chart-related constants in models

**Patterns established:**
- `ExplorerPageState` manages URL params — extend it or create similar for chart state
- Ticker rows use `<tr>` elements with `hx-get` for partial updates
- Empty state handling: show message in target div, not 404
- All fragment endpoints include `catalog` param explicitly

**Review patches applied in 2-1:**
- P1: Allowlist for sort_by → validate `tf` param similarly
- P3: `|tojson` for hx-vals → apply to ticker param in hx-vals
- P4: Page clamp guard → ensure tf defaults gracefully if invalid

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 2.2] — Full AC and epic context
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-7] — Direct Parquet reads for chart data
- [Source: _bmad-output/planning-artifacts/architecture.md#API Endpoint Naming] — REST and UI endpoint contracts
- [Source: _bmad-output/planning-artifacts/architecture.md#HTMX Fragment Pattern] — Fragment rules
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Chart Panel] — Timeframe toolbar states, loading patterns
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Wireframe] — Stacked layout, full-width chart
- [Source: src/api/rest/timeseries.py] — Bar → Candle conversion pattern
- [Source: src/api/models/chart_timeseries.py] — Reusable Candle, Timeframe, TIMEFRAME_TO_BAR_TYPE
- [Source: static/js/charts-core.js] — createChartWithDefaults(), CHART_COLORS
- [Source: _bmad-output/implementation-artifacts/2-1-explorer-page-with-ticker-list.md] — Previous story patterns and learnings

## Dev Agent Record

### Agent Model Used
Claude Opus 4.6 (1M context)

### Debug Log References
- Ruff auto-formatter stripped unused imports multiple times; resolved with `noqa: F401` comments and bundling import+usage in single edits
- `DataNotFoundError` requires 3 positional args (instrument_id, start, end) — fixed test constructor

### Completion Notes List
- Task 1: Added `ExplorerTimeframe` enum with label/bar_type_spec/bar_count_field properties and `from_label()` classmethod with safe default; added `ChartDataResponse` Pydantic model reusing existing `Candle`. 16 unit tests.
- Task 2: Added `GET /api/chart/catalog/{ticker}` REST endpoint with DB-based ticker resolution (not hardcoded), timeframe validation via allowlist, DataNotFoundError → empty bars (200, not 404). 10 component tests.
- Task 3: Created `chart_panel.html` HTMX fragment with timeframe toolbar (active/disabled states), 2px loading bar, inline TradingView chart initialization using `createChartWithDefaults()` and `CHART_COLORS`.
- Task 4: Added `GET /explorer/chart-panel` UI route resolving ticker→instrument, fetching bars, determining available timeframes from DB bar counts, serializing to JSON for template. 9 component tests.
- Task 5: Wired ticker rows with `hx-get`, `hx-target="#chart-panel"`, `hx-vals` with `tojson` filter (XSS safe), `hx-push-url`. Added `selected_ticker` for server-rendered row highlight.
- Task 6: Timeframe buttons implemented in chart_panel.html with `hx-get` switching, active state via `bg-blue-500`, disabled state for unavailable timeframes.
- Task 7: Progressive loading via `subscribeVisibleTimeRangeChange`, fetches more data from REST API when user pans to boundary, deduplicates with `deduplicateTimeseriesData()`, 2s debounce.
- Task 8: URL state via `hx-push-url` on ticker click and timeframe change. Explorer page auto-loads chart via `hx-trigger="load"` when `ticker` param present.

### Change Log
- 2026-04-12: Story 2-2 implementation complete — chart panel with timeframe switching, progressive loading, URL state management

### File List

**New files:**
- `templates/explorer/chart_panel.html` — HTMX fragment: timeframe toolbar + TradingView chart + progressive loading JS
- `tests/unit/api/test_chart_data_models.py` — 16 unit tests for ChartDataResponse and ExplorerTimeframe
- `tests/component/api/test_chart_panel_routes.py` — 19 component tests for REST + UI chart routes

**Modified files:**
- `src/api/models/explorer.py` — Added `ExplorerTimeframe` enum, `ChartDataResponse` model
- `src/api/rest/explorer.py` — Added `GET /api/chart/catalog/{ticker}` endpoint
- `src/api/ui/explorer.py` — Added `GET /explorer/chart-panel` route, `ticker`/`tf` params to explorer page
- `templates/explorer/ticker_list.html` — Added `hx-get` to ticker rows for chart loading, selected row highlight
- `templates/explorer/explorer.html` — Wired `#chart-panel` div with auto-load from URL params
