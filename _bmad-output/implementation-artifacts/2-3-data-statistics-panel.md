# Story 2.3: Data Statistics Panel

Status: done

## Story

As a system operator,
I want to see data statistics for a selected ticker alongside the chart,
so that I can verify date ranges, bar counts, and price ranges match expectations.

## Acceptance Criteria

1. **Given** a ticker is selected in the explorer **When** the stats panel loads (simultaneously with the chart via `hx-swap-oob`) **Then** it displays a grid of stat cards: Date Range, Daily Bars, 1-Hour Bars, 5-Min Bars, 1-Min Bars, Price Range, Nautilus ID **And** the grid uses `grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))]` (or Tailwind equivalent) so columns reflow based on viewport width.

2. **Given** the stats panel is displayed **When** the data is rendered **Then** all numeric values use `font-mono` for alignment **And** stat card labels use `text-xs text-slate-500 uppercase` **And** stat card values use `font-mono text-lg font-semibold` **And** each card has `bg-slate-900 rounded-lg p-4` for consistent card styling.

3. **Given** the stats panel **When** the timeframe is changed via the chart panel toolbar **Then** the stats panel also refreshes via `hx-swap-oob` **And** the Price Range stat reflects min/max prices for the selected timeframe **And** the other bar-count cards highlight the active timeframe (e.g., ring around the matching card).

4. **Given** no ticker is selected (no `?ticker=` URL param and no prior selection) **When** the explorer page is loaded **Then** the stats panel is hidden (empty `<div id="stats-panel">`, no placeholder text).

5. **Given** the REST endpoint `GET /api/explorer/ticker/{ticker}/stats` **When** called with `catalog` and optional `tf` query params **Then** it returns a `TickerStatsResponse` (Pydantic model) containing: `ticker`, `nautilus_id`, `date_range_start`, `date_range_end`, `bar_count_daily`, `bar_count_hourly`, `bar_count_5min`, `bar_count_minute`, `price_min`, `price_max`, and `active_tf` (the timeframe used to compute the price range) **And** returns HTTP 404 if the ticker is not in the catalog.

6. **Given** the UI fragment endpoint `GET /explorer/stats-panel` **When** called with `catalog`, `ticker`, and optional `tf` query params **Then** it returns the `stats_panel.html` HTMX fragment populated with the same data as the REST endpoint **And** the fragment contains only the stats grid (no `#stats-panel` id wrapper on the fragment itself) — it is consumed via `hx-swap="innerHTML"` into the page's existing `<div id="stats-panel">`.

7. **Given** the chart-panel response **When** rendered **Then** its OOB placeholder `<div id="stats-panel" hx-swap-oob="true">` is replaced with an OOB render of `stats_panel.html` containing the populated stat cards, so ticker clicks and timeframe changes update both chart and stats in a single HTMX request.

8. **Given** `GET /explorer` is loaded with `?ticker=` in the URL **When** the page renders **Then** the stats panel auto-loads via `hx-trigger="load"` against `/explorer/stats-panel` with the catalog, ticker, and tf params (same pattern as chart-panel auto-load in `explorer.html`).

9. **Given** the ticker has `bar_count_*` = 0 for the requested timeframe **When** the stats endpoint computes Price Range **Then** it returns `price_min = null` and `price_max = null` **And** the template renders "—" for the Price Range card value rather than throwing or showing `None`.

## Tasks / Subtasks

- [x] Task 1: Add `TickerStatsResponse` Pydantic model (AC: #5)
  - [x] 1.1 Write failing unit test in `tests/unit/api/test_explorer_models.py` covering field types, defaults, and `price_min`/`price_max` nullability.
  - [x] 1.2 Add `TickerStatsResponse` to `src/api/models/explorer.py` with fields: `ticker: str`, `nautilus_id: Optional[str]`, `date_range_start: Optional[datetime]`, `date_range_end: Optional[datetime]`, `bar_count_daily: int = 0`, `bar_count_hourly: int = 0`, `bar_count_5min: int = 0`, `bar_count_minute: int = 0`, `price_min: Optional[float] = None`, `price_max: Optional[float] = None`, `active_tf: str` (the `ExplorerTimeframe.label` used).

- [x] Task 2: Add price-range computation helper (AC: #5, #9)
  - [x] 2.1 Failing unit tests for `_compute_price_range(bars: list[Bar]) -> tuple[float|None, float|None]` in `tests/unit/api/test_explorer_models.py` (or a sibling module): empty list → `(None, None)`; single bar → `(low, high)`; multi-bar → overall min-of-lows / max-of-highs.
  - [x] 2.2 Implement helper as a private function in `src/api/stats_service.py` (new shared module) using `bar.low.as_double()` / `bar.high.as_double()`.
  - [x] 2.3 Guard against the `DataNotFoundError` path used by `catalog.query_bars()` — return `(None, None)` when no bars.

- [x] Task 3: REST stats endpoint (AC: #5, #9)
  - [x] 3.1 Failing component tests in `tests/component/api/test_stats_panel_routes.py` covering: happy path with DB + bars mocks, 404 for unknown ticker, price_min/max = None when `query_bars` raises `DataNotFoundError`, `tf` defaults to `D`, invalid `tf` coerced to `D` via the existing `VALID_TF_LABELS` guard, `active_tf` echoed in response.
  - [x] 3.2 Add `GET /explorer/ticker/{ticker}/stats` in `src/api/rest/explorer.py` (the router is already prefixed with `/api` in `src/api/web.py`, so the final path is `/api/explorer/ticker/{ticker}/stats`).
  - [x] 3.3 Resolve ticker → instrument via `MetadataService.get_instrument(catalog, ticker)` (re-use the pattern from `get_chart_data`; do NOT use `symbol_to_instrument_id`).
  - [x] 3.4 Validate `tf` via `VALID_TF_LABELS` and coerce to `"D"` on invalid input; resolve to `ExplorerTimeframe.from_label(tf)`.
  - [x] 3.5 Query bars over the instrument's full `date_range_start`→`date_range_end` (fall back to `1970-01-01`→`2099-12-31` when nullable) at `active_tf.bar_type_spec`.
  - [x] 3.6 Catch `DataNotFoundError` → treat as empty bars → `price_min = price_max = None`.
  - [x] 3.7 Build and return `TickerStatsResponse`.

- [x] Task 4: UI stats-panel fragment route (AC: #6, #8)
  - [x] 4.1 Failing component tests in `tests/component/api/test_stats_panel_routes.py` (same file) for `GET /explorer/stats-panel`: returns 200, is a fragment (no `<html>`/`<body>`), renders all 7 stat card labels, renders "—" for null price range, highlights the active timeframe bar-count card, 404 for unknown ticker.
  - [x] 4.2 Add `stats_panel_fragment` handler to `src/api/ui/explorer.py` next to `chart_panel_fragment`. Accept `catalog`, `ticker`, `tf` query params (same signatures/defaults as chart-panel).
  - [x] 4.3 Share implementation with Task 3 by extracting a private `_build_ticker_stats(service, catalog_service, catalog, ticker, tf) -> TickerStatsResponse` helper used by both REST and UI paths. Lives in `src/api/stats_service.py`.
  - [x] 4.4 Return `templates.TemplateResponse("explorer/stats_panel.html", {...})` with the stats fields and `active_tf.label`.

- [x] Task 5: `stats_panel.html` template (AC: #1, #2, #3, #9)
  - [x] 5.1 Create `templates/explorer/stats_panel.html` as an HTMX fragment whose top-level element IS the grid itself (no duplicate `id="stats-panel"` wrapper — the container lives in `explorer.html` / the OOB wrapper in `chart_panel.html`).
  - [x] 5.2 Grid uses Tailwind classes `grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-3 mt-6` (compose with existing spacing conventions).
  - [x] 5.3 Seven cards: Date Range, Daily Bars, 1-Hour Bars, 5-Min Bars, 1-Min Bars, Price Range, Nautilus ID. Each card: label in `text-xs text-slate-500 uppercase tracking-wider`, value in `font-mono text-lg font-semibold text-slate-100`, card wrapper `bg-slate-900 rounded-lg p-4 border border-slate-800`.
  - [x] 5.4 Date Range card value: `{{ start.strftime('%Y-%m-%d') }} — {{ end.strftime('%Y-%m-%d') }}`. Null-safe: render `"—"` if either bound is missing.
  - [x] 5.5 Bar-count cards: use the existing `_format_bar_count` helper (import or pass as a template callable, matching the ticker_list.html pattern). Highlight the active tf card with an extra `ring-1 ring-blue-500/40` class when `active_tf_label == "D" | "1H" | "5m" | "1m"` matches the card. (Renamed from `active_tf` to `active_tf_label` to avoid a collision with chart_panel.html's `active_tf` ExplorerTimeframe enum when stats_panel is `{% include %}`-ed from chart_panel for OOB refresh.)
  - [x] 5.6 Price Range card value: `${{ "%.2f"|format(price_min) }} – ${{ "%.2f"|format(price_max) }}` when both are non-null, else `"—"`. Include subtitle `text-xs text-slate-500 mt-1` showing `{{ active_tf_label }} timeframe`.
  - [x] 5.7 Nautilus ID card value: `{{ nautilus_id or "—" }}`.

- [x] Task 6: Wire OOB stats update from chart-panel response (AC: #3, #7)
  - [x] 6.1 In `chart_panel_fragment` (`src/api/ui/explorer.py`), also compute `TickerStatsResponse` via the shared helper from Task 4.3 and pass it into `chart_panel.html` as `stats` context (spread via `_stats_template_context`).
  - [x] 6.2 In `templates/explorer/chart_panel.html`, replace the existing placeholder `<div id="stats-panel" hx-swap-oob="true"></div>` with an include that renders `stats_panel.html` inside an OOB wrapper:
    ```html
    <div id="stats-panel" hx-swap-oob="innerHTML">
      {% include "explorer/stats_panel.html" %}
    </div>
    ```
    so the chart-panel response atomically refreshes the stats panel without a second HTTP round-trip.
  - [x] 6.3 Component test in `tests/component/api/test_chart_panel_routes.py` asserting the chart-panel response contains the seven stat card labels and the `hx-swap-oob` attribute on the stats wrapper.

- [x] Task 7: Auto-load stats on page refresh with `?ticker=` (AC: #8)
  - [x] 7.1 Update `templates/explorer/explorer.html` so the placeholder `<div id="stats-panel"></div>` grows an `hx-get`/`hx-trigger="load"` block mirroring the chart-panel auto-load.
  - [x] 7.2 Component test covering page load with `?catalog=...&ticker=AAPL&tf=1H` asserts the `hx-get`/`hx-vals` attributes render correctly.

- [x] Task 8: Manual verification via agent-browser (AC: all)
  - [x] 8.1 Start dev server (`uv run uvicorn src.api.web:app --host 127.0.0.1 --port 8000`).
  - [x] 8.2 Loaded `/explorer?catalog=e2e-test`, clicked AAPL row — stats cards populated alongside the chart. Screenshot at `/tmp/story-2-3-evidence/stats_panel_tsla_5m.png`.
  - [x] 8.3 Switched to 1H — Price Range card updated ($0.19→$0.17 low) and `1H timeframe` subtitle + ring highlight followed active tf.
  - [x] 8.4 Refreshed `/explorer/?catalog=e2e-test&ticker=TSLA&tf=5m` directly — chart + stats both auto-loaded (Price Range = `$1.00 – $498.83`, `5m timeframe`, TSLA.NASDAQ).
  - [x] 8.5 Stopped dev server.

### Review Findings

_Code review 2026-04-19. Sources: Blind Hunter, Edge Case Hunter, Acceptance Auditor._

- [x] [Review][Patch] Catch `CatalogCorruptionError` in stats helper [src/api/stats_service.py] — fixed 2026-04-19. Now catches `DataNotFoundError` (empty result) and `CatalogCorruptionError` (logs warning, falls back to `bars = []`) so corrupt parquet no longer 500s the stats endpoint or chart-panel OOB render.
- [x] [Review][Patch] Add structlog timing log around stats `query_bars` [src/api/stats_service.py] — fixed 2026-04-19. Added `stats_query_bars_complete` (info), `stats_query_bars_empty` (debug), and `stats_query_bars_corrupt` (warning) events, each with `duration_ms` via `time.perf_counter()`.
- [x] [Review][Patch] `_build_ticker_stats` returns `TickerStatsResponse` only [src/api/stats_service.py, src/api/rest/explorer.py, src/api/ui/explorer.py] — fixed 2026-04-19. Dropped the unused 2-tuple return, updated 3 call sites to drop `stats, _ = ...` destructuring.
- [x] [Review][Patch] Strengthened weak test assertions [tests/component/api/test_stats_panel_routes.py, tests/component/api/test_chart_panel_routes.py] — fixed 2026-04-19. `test_active_tf_echoed` now asserts `bar_type_spec == "1-HOUR-LAST"` on the mocked call; `test_price_range_renders_dash_when_null` isolates the Price Range card via label→subtitle slice and rejects stray `$`; new `test_chart_and_stats_queries_paired` asserts `query_bars.call_count == 2` and distinguishes windowed chart vs full-range stats call by start/end.

- [x] [Review][Defer] Duplicate `query_bars` on every chart-panel render (chart windowed + stats full-range) — deferred; intentional per Task 6.1, spec-acknowledged perf cost.
- [x] [Review][Defer] Unbounded 1m full-range scan (~1.95M bars) — deferred; spec Known Perf Note explicitly out-of-scope for Phase 1.
- [x] [Review][Defer] Sync `query_bars` awaited from async handler blocks event loop [src/api/stats_service.py:~477] — deferred; pre-existing pattern inherited from Story 2-2.
- [x] [Review][Defer] `tf` coercion does not case/whitespace-normalize (`"1h"` / `"1H "` silently → `"D"`) [src/api/stats_service.py:~463] — deferred; spec specifies silent coercion, but case-insensitive matching would reduce user surprise.
- [x] [Review][Defer] `selected_tf` template variable not verified as string vs enum [templates/explorer/explorer.html:~89] — deferred; `(selected_tf or "D") | tojson` renders `"DAILY"` if enum slips through. Pin at route level in a follow-up.
- [x] [Review][Defer] `_compute_price_range` allocates two full lists before `min`/`max` [src/api/stats_service.py:~35] — deferred; single-pass reduction is trivially faster on million-bar scans, pairs with F6.
- [x] [Review][Defer] `VALID_TF_LABELS` computed/stored in 3 modules — deferred; DRY cleanup.
- [x] [Review][Defer] Prices as `float` not `Decimal` in `TickerStatsResponse` [src/api/models/explorer.py] — deferred; architectural, pre-existing across the project.
- [x] [Review][Defer] Datetime TZ ambiguity (naive vs aware) in REST JSON serialization [src/api/stats_service.py:~488] — deferred; pre-existing Pydantic default behavior.
- [x] [Review][Defer] `format_bar_count` passed via per-render template context rather than registered as a Jinja filter — deferred; pattern inherited from Story 2-2.

## Dev Notes

### Architecture Compliance

- **Endpoints (architecture.md#API Endpoint Naming):**
  - REST: `GET /api/explorer/ticker/{ticker}/stats` — JSON `TickerStatsResponse`.
  - UI fragment: `GET /explorer/stats-panel` — HTML fragment, no `<html>`/`<body>` tags, root `<div id="stats-panel">`.
- **Explorer boundary (architecture.md#Explorer Boundary):** UI route → `MetadataService` (DB ticker/bar-count/date-range) + `DataCatalogService.query_bars()` (Parquet for price min/max) → `templates.TemplateResponse`. REST route → same services → Pydantic response → JSON.
- **No new DB tables or columns.** Date range and bar counts come from `catalog_instruments`. Price min/max is computed from Parquet bars at request time (no new columns). If future perf demands it, add `price_min_*`/`price_max_*` columns in a follow-up migration — out of scope for Story 2-3.
- **ADR-7 (Direct Parquet reads for chart data):** stats reuses `DataCatalogService.query_bars()`, no intermediate cache layer.
- **HTMX Fragment Pattern (architecture.md):** root id matches `hx-target`, multi-target updates use `hx-swap-oob="innerHTML"`, no `<html>`/`<body>`.

### Critical Implementation Details

**Reuse existing infrastructure (DO NOT duplicate):**

| What | Where | Why |
|------|-------|-----|
| `ExplorerTimeframe` enum | `src/api/models/explorer.py` | Single source of truth for label ↔ `bar_type_spec` ↔ `bar_count_field` mapping |
| `VALID_TF_LABELS` | `src/api/rest/explorer.py`, `src/api/ui/explorer.py` | tf validation pattern established in Story 2-2 |
| `MetadataService.get_instrument(catalog, ticker)` | `src/services/firstrate/metadata_service.py` | Ticker → `CatalogInstrument` resolution (never hardcode `.NASDAQ`) |
| `DataCatalogService.query_bars(...)` + `DataNotFoundError` | `src/services/data_catalog.py`, `src/services/exceptions.py` | Parquet read + empty-data signal |
| `_format_bar_count` | `src/api/ui/explorer.py` | Compact K/M formatting for bar-count cards |
| `DataCatalog`, `Metadata` type aliases | `src/api/dependencies.py` | DI for REST + UI |
| `Candle`, `ChartDataResponse` models | `src/api/models/explorer.py`, `src/api/models/chart_timeseries.py` | Shape precedent; keep `TickerStatsResponse` consistent |

**Price range computation (the one new piece of logic):**

- Query `catalog_service.query_bars(instrument_id=nautilus_id, start=date_range_start_or_floor, end=date_range_end_or_ceiling, bar_type_spec=active_tf.bar_type_spec)`.
- On `DataNotFoundError` → return `None, None`.
- Otherwise: `price_min = min(b.low.as_double() for b in bars)`, `price_max = max(b.high.as_double() for b in bars)`.
- **Known perf note:** scanning all 1-minute bars for a 5-year ticker (~1.95M rows) can take a few seconds on cold Parquet reads. Out of scope: Parquet row-group min/max statistics pushdown. If the active timeframe in the UI is 1m/5m and the DB bar-count for that timeframe is large (> 500k), still scan — correctness over optimization for Phase 1. Log query timing via structlog so the retrospective can flag it if users complain.
- **Decimal precision:** `Bar.low.as_double()` / `Bar.high.as_double()` is the same conversion used by `get_chart_data` in Story 2-2. Format to 2 decimals in the template; full precision stays on the Parquet side.

**Shared helper (DRY between REST + UI):**

Extract a private helper in `src/api/ui/explorer.py` (or a new `src/api/stats_service.py`):

```python
async def _build_ticker_stats(
    service: MetadataService,
    catalog_service: DataCatalogService,
    catalog: str,
    ticker: str,
    tf: str,
) -> TickerStatsResponse: ...
```

Both the REST endpoint in `rest/explorer.py` and the UI fragment + OOB chart wiring call this helper so tf validation, ticker resolution, date-range clamping, and price-range computation live in one place.

**OOB simultaneous update (chart + stats, AC #3, #7):**

- Current `chart_panel.html:40-41` has a placeholder: `<div id="stats-panel" hx-swap-oob="true"></div>`.
- Replace that placeholder with an OOB wrapper that includes `stats_panel.html`. The wrapper uses `hx-swap-oob="innerHTML"` so the server re-renders the stats grid in the same response as the chart swap. That satisfies UX-DR4's "chart + stats + supplementary update simultaneously" and the "updates when timeframe changes" AC without a second request.
- Include stats context in `chart_panel_fragment` response so the template has all fields.

**URL state + auto-load (AC #8):**

- `explorer.py` UI route already propagates `ticker` and `tf` query params into the page context. Reuse that: `{% if selected_ticker %}` block on `<div id="stats-panel">`.
- Both chart-panel and stats-panel auto-load on page refresh via `hx-trigger="load"` — they fire in parallel, so both panels populate without sequential delay.

**Empty / hidden state (AC #4):**

- When no ticker is selected, explorer.html's `stats-panel` div renders empty — no placeholder text, consistent with UX spec "panel hidden" and with chart-panel's behavior in Story 2-2.
- Do NOT emit `stats_panel.html` on the page-level route (only on fragment/OOB paths).

### Existing Code to Reuse (DO NOT Duplicate)

| What | Where | How to use |
|------|-------|------------|
| Pattern: `ExplorerTimeframe.from_label(tf)` with safe default | `src/api/models/explorer.py:60` | Reuse for `tf` param resolution in both REST and UI stats handlers |
| Pattern: tf allowlist `VALID_TF_LABELS = {tf.label for tf in ExplorerTimeframe}` | `src/api/rest/explorer.py:32` | Reuse same allowlist in stats handler |
| Pattern: ticker → instrument via `MetadataService.get_instrument` with 404 fallback | `src/api/rest/explorer.py:69-74` | Mirror exact pattern; do NOT use `symbol_to_instrument_id` |
| Pattern: `query_bars` wrapped in `try/except DataNotFoundError → bars = []` | `src/api/rest/explorer.py:88-96` | Same pattern for price-range computation |
| Pattern: dependency_overrides + `TestClient` + `AsyncMock(MetadataService)` + `MagicMock(DataCatalogService)` | `tests/component/api/test_chart_panel_routes.py:66-92` | Copy-adapt for stats test fixtures |
| `_format_bar_count` | `src/api/ui/explorer.py:44-52` | Pass via template context the same way chart-panel does |

### File Structure

**New files:**

- `templates/explorer/stats_panel.html` — HTMX fragment with 7-card grid, root `<div id="stats-panel">`.
- `tests/component/api/test_stats_panel_routes.py` — component tests for REST `/api/explorer/ticker/{ticker}/stats` and UI `/explorer/stats-panel`, mirroring `test_chart_panel_routes.py` structure (fixtures, markers, client setup).

**Modified files:**

- `src/api/models/explorer.py` — add `TickerStatsResponse` Pydantic model.
- `src/api/rest/explorer.py` — add `GET /explorer/ticker/{ticker}/stats` route; add `_compute_price_range` helper (or move to shared helper module — see Task 4.3).
- `src/api/ui/explorer.py` — add `stats_panel_fragment` handler; add `_build_ticker_stats` shared helper; extend `chart_panel_fragment` to pass stats context; update docstring for `explorer_page` to mention stats auto-load.
- `templates/explorer/chart_panel.html` — replace line 40-41 OOB placeholder with OOB wrapper that `{% include "explorer/stats_panel.html" %}`.
- `templates/explorer/explorer.html` — add `hx-trigger="load"` block to line-86 `<div id="stats-panel">` guarded by `{% if selected_ticker %}`.
- `tests/unit/api/test_explorer_models.py` — add `TickerStatsResponse` tests.
- `tests/component/api/test_chart_panel_routes.py` — add test asserting the chart-panel response includes the stats OOB wrapper.

**No new routers registered in `src/api/web.py`** — stats REST endpoint added to existing `explorer_api.router`, UI endpoint added to existing `explorer.router`.

### Project Structure Notes

- REST path: router in `src/api/rest/explorer.py` is registered with prefix `/api` in `web.py:43`. Use `@router.get("/explorer/ticker/{ticker}/stats", ...)` so the final path becomes `/api/explorer/ticker/{ticker}/stats` — this matches architecture.md#API Endpoint Naming.
- UI path: router in `src/api/ui/explorer.py` is registered with prefix `/explorer` in `web.py:39`. Use `@router.get("/stats-panel", ...)` so the final path becomes `/explorer/stats-panel`.
- Templates directory convention: `templates/explorer/stats_panel.html` (matches architecture.md#Project Structure line 456 and the chart/ticker_list precedent).
- No alembic migration required. No new service class required — re-use `MetadataService` + `DataCatalogService`.

### Testing Requirements

- **TDD non-negotiable** (CLAUDE.md#Foundational Rules): write failing tests first, then implement.
- **Unit tests** — `TickerStatsResponse` field types + nullability, `_compute_price_range` edge cases (empty, single, multi-bar).
- **Component tests** — REST + UI stats endpoints with mocked `MetadataService` (AsyncMock) and `DataCatalogService` (MagicMock). Use `app.dependency_overrides` with try/finally cleanup (established pattern from Story 2-1/2-2). Mirror fixtures from `tests/component/api/test_chart_panel_routes.py`.
- **Markers required** — `@pytest.mark.unit`, `@pytest.mark.component` on every test class/function. Integration tier is not needed for this story (no Nautilus engine lifecycle).
- **Manual UI verification via `agent-browser`** — Task 8. Capture a screenshot as evidence.
- **Quality gates** — `make format && make lint && make typecheck && make test-unit && make test-component` must all pass before moving to review.

### Previous Story Intelligence (Story 2-2)

**Patterns to reuse verbatim:**

- `ExplorerTimeframe` enum + `from_label` + `VALID_TF_LABELS` allowlist — do not reinvent timeframe parsing.
- Ticker → instrument resolution via `MetadataService.get_instrument` returning nullable — 404 when None or when `nautilus_id` is missing.
- `DataNotFoundError` → empty result with 200 (not 404) for the data-range case.
- Component test fixtures: `_make_instrument(**overrides)`, `_make_bar(ts_ns, o, h, lo, c, vol)` helpers in the chart-panel test file are ideal references — lift them into the new `test_stats_panel_routes.py` (or import from a shared conftest if it makes sense).

**Bear traps from Story 2-2 commits:**

- **Ruff auto-formatter strips "unused" imports** — when adding the `TickerStatsResponse` import in `rest/explorer.py`, bundle the import with its usage in the same edit; otherwise the auto-formatter may delete the import before the route code lands. If the linter keeps stripping, use `# noqa: F401` alongside a comment explaining the forward use (pattern at `src/api/ui/explorer.py:8-10` and `:18-24`).
- **`DataNotFoundError` constructor** takes 3 positional args `(instrument_id, start, end)` — not a single string. Tests that raise it manually need `datetime(..., tzinfo=timezone.utc)` args (see `test_chart_panel_routes.py:161-167`).
- **Edit dependent changes in one shot** — e.g., adding the `stats` context key to `chart_panel_fragment` AND updating `chart_panel.html` to consume it. Splitting these across two edits risks the linter removing the new template variable's context entry as "unused".
- **Windowed chart loads** (commit `a3f86b0`) — Story 2-2 had a bug where 1-minute timeframes loaded the full history at once. The chart fragment now uses `_compute_chart_window`. The **stats panel does the opposite on purpose**: it needs full-history min/max, not a window. Do NOT apply the chart's windowing to the stats query. Be explicit about this in the helper's docstring to prevent confusion.

**Review feedback from Story 2-1 that still applies:**

- Validate any user-controlled template variable with `|tojson` when embedded in `hx-vals`. In the stats-panel `hx-trigger="load"` block (Task 7.1), use `{{ selected_ticker | tojson }}` — never raw string interpolation.
- Sort-column / param allowlist pattern — use `VALID_TF_LABELS` as the allowlist for `tf`, coerce invalid to `"D"` (do NOT raise 422). Consistent with chart-panel behavior.

### Git Intelligence (last 5 commits)

- `6efb82b feat(import): deduplicate bars by timestamp in FirstRate CSV parser` — data quality fix, unrelated to stats panel.
- `a3f86b0 fix(explorer): window chart data loading for high-frequency timeframes` — **relevant**: chart now windows via `_compute_chart_window`; stats intentionally does NOT (see Previous Story Intelligence above).
- `1c48626 feat(explorer): add chart panel with timeframe switching (Story 2-2)` — the direct predecessor; code patterns and test fixtures are the template for this story.
- `80fa712 fix(explorer): Story 2-1 code review patches` — sort-column allowlist, `|tojson`, page clamp. Apply the same rigor to stats inputs.
- `131f172 feat(explorer): add data explorer page with ticker list (Story 2-1)` — established `MetadataService`, `ExplorerPageState`, test conventions. Continue using them.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md#Story 2.3`] — canonical AC.
- [Source: `_bmad-output/planning-artifacts/architecture.md#API Endpoint Naming`] — REST/UI endpoint paths.
- [Source: `_bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries`] — `stats_panel.html` location, module layout.
- [Source: `_bmad-output/planning-artifacts/architecture.md#HTMX Fragment Pattern`] — fragment rules, OOB multi-target pattern.
- [Source: `_bmad-output/planning-artifacts/architecture.md#Explorer Boundary`] — service wiring (MetadataService + CatalogManager).
- [Source: `_bmad-output/planning-artifacts/ux-design-specification.md#6. Data Statistics Panel (lines 686-700)`] — card list, states, typography.
- [Source: `_bmad-output/planning-artifacts/ux-design-specification.md#HTMX swap strategy (lines 706-710)`] — ticker selection swaps chart + stats + supplementary via `hx-swap-oob`.
- [Source: `src/api/rest/explorer.py`] — ticker → instrument + `query_bars` patterns (`get_chart_data`).
- [Source: `src/api/ui/explorer.py`] — fragment + template-response pattern, `_format_bar_count`, `_compute_chart_window` (intentionally not used here).
- [Source: `src/api/models/explorer.py`] — `ExplorerTimeframe`, `ChartDataResponse` precedents; add `TickerStatsResponse` here.
- [Source: `templates/explorer/chart_panel.html:40-41`] — existing OOB placeholder that Task 6 replaces.
- [Source: `templates/explorer/explorer.html:78-87`] — page-level auto-load pattern copied by Task 7.
- [Source: `tests/component/api/test_chart_panel_routes.py`] — fixture and client-setup template for `test_stats_panel_routes.py`.
- [Source: `tests/unit/api/test_chart_data_models.py`] — model-test template for `TickerStatsResponse` tests.
- [Source: `_bmad-output/implementation-artifacts/2-2-chart-panel-with-timeframe-switching.md`] — previous story patterns, review patches, and known bear traps.

## Dev Agent Record

### Agent Model Used

claude-opus-4-7 (1M context)

### Debug Log References

- Ruff auto-formatter repeatedly stripped the new `src.api.stats_service` import when added ahead of its usage — worked around by bundling the import and its call-site into a single `Edit` per the Story 2-2 bear-trap guidance.
- First chart-panel integration pass broke because both `chart_panel.html` and the OOB-included `stats_panel.html` read `active_tf` from context — chart_panel expected an `ExplorerTimeframe` enum (`active_tf.label`), stats_panel expected the string label. Fix: rename the stats template variable to `active_tf_label` so the two contexts no longer collide when `{% include %}`-ed.
- Existing `TestChartPanelWindowing` tests asserted `mock_catalog_service.query_bars.call_args.kwargs` which pointed at the *latest* call. After adding the stats full-range query (second call), those assertions had to switch to `call_args_list[0].kwargs` (the chart's windowed call).

### Completion Notes List

- **REST endpoint:** `GET /api/explorer/ticker/{ticker}/stats` returns `TickerStatsResponse` JSON. Verified end-to-end against the `e2e-test` catalog — AAPL Daily price range `$0.19 – $288.35`, 1H `$0.17 – $288.35`.
- **UI fragment:** `GET /explorer/stats-panel` returns the bare stats grid (no wrapping `#stats-panel` id). Rendered via `hx-swap="innerHTML"` into the page-level container or the chart-panel OOB wrapper.
- **OOB multi-target update:** chart-panel response now carries an OOB `#stats-panel` wrapper that re-renders stats atomically with the chart swap, so ticker clicks and timeframe changes refresh both panels in one request.
- **Auto-load:** `explorer.html` stats-panel div gets an `hx-trigger="load"` block guarded by `{% if selected_ticker %}` so deep links like `/explorer/?catalog=…&ticker=…&tf=…` populate both chart and stats without a second click.
- **Shared helper:** `src/api/stats_service.py` centralises tf-label validation, ticker → instrument resolution, date-range clamping (1970…2099), and full-range price-min/max computation. Both REST and UI call `_build_ticker_stats`; chart-panel also calls it to gather stats for the OOB wrapper.
- **Hidden/empty state:** when no ticker is selected the `#stats-panel` container renders empty (no placeholder text), matching the UX spec.
- **Stats does not window:** deliberately different from chart_panel (`_compute_chart_window`) — the helper docstring calls this out to prevent the Story 2-2 bear trap from resurfacing.
- **Tests:** 17 new model/helper unit tests + 19 new component tests (REST, UI, page auto-load), plus 2 new chart-panel tests for the OOB stats wrapper. Adjusted 4 existing `TestChartPanelWindowing` assertions to use `call_args_list[0]` now that chart-panel makes two `query_bars` calls.
- **Quality gates:** `make format` clean. `make lint` surfaces only two pre-existing errors in `tests/unit/services/firstrate/test_firstrate_csv_parser.py` (unused `captured` / `old_get`). `make typecheck` surfaces only the pre-existing `src/db/repositories/catalog_instrument_repository.py:201` and `src/api/ui/explorer.py` NavigationState `app_version` errors that were present on the parent commit. Full unit + component runs: 1307 passed / 15 skipped / 2 pre-existing env-leak failures in `tests/unit/test_catalog_settings.py` (both pass in isolation and are unrelated).

### File List

**New files:**
- `src/api/stats_service.py`
- `templates/explorer/stats_panel.html`
- `tests/component/api/test_stats_panel_routes.py`

**Modified files:**
- `src/api/models/explorer.py` — added `TickerStatsResponse`.
- `src/api/rest/explorer.py` — added `GET /explorer/ticker/{ticker}/stats` route + import of shared helper.
- `src/api/ui/explorer.py` — added `stats_panel_fragment` route, `_stats_template_context` helper, and merged stats context into `chart_panel_fragment` response.
- `templates/explorer/chart_panel.html` — replaced OOB placeholder with `hx-swap-oob="innerHTML"` wrapper that `{% include %}`s `stats_panel.html`.
- `templates/explorer/explorer.html` — `#stats-panel` div now auto-loads via `hx-trigger="load"` when `?ticker=` is present.
- `tests/unit/api/test_explorer_models.py` — added `TestTickerStatsResponse` and `TestComputePriceRange` suites.
- `tests/component/api/test_chart_panel_routes.py` — added OOB-wrapper / stats-card assertions and updated windowing tests to `call_args_list[0]`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `2-3-data-statistics-panel: ready-for-dev → review`.

## Change Log

| Date       | Change                                                                                   |
|------------|------------------------------------------------------------------------------------------|
| 2026-04-19 | Implemented Story 2-3: data statistics panel (REST + UI + OOB wiring + auto-load). Status → review. |
| 2026-04-19 | Code review pass: 4 patches applied (corrupt-parquet catch, structlog timing, tuple return dropped, test assertions strengthened); 10 deferred. Status → done. |

