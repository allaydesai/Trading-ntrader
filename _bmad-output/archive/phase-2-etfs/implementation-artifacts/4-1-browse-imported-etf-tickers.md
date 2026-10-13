# Story 4.1: Browse Imported ETF Tickers

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story
<!-- Tasks 1–5 complete; see Dev Agent Record. -->

As the operator,
I want imported ETF tickers listed in the catalog browser with their available date ranges and **available timeframes**,
so that I can see which ETFs are in the catalog and what data each has.

## Acceptance Criteria

1. **Given** imported ETFs in the catalog, **When** the operator opens the explorer catalog browser (`GET /explorer/`) or switches to the ETF catalog, **Then** ETF tickers are listed alongside — and filterable from — Stocks (via the existing `asset_class` pills, `AssetClass.ETF == "ETF"`), each row showing its available **date range** and its **available timeframes** as per-timeframe bar counts. [Source: epics.md Story 4.1 AC1; src/api/ui/explorer.py:344 `ticker_list_fragment`; templates/explorer/ticker_list.html]
2. **Given** the browse row's timeframe/bar-count column, **When** an ETF row renders, **Then** the column reflects **all five native ETF timeframes** — Daily, 1-Hour, **30-Minute**, 5-Minute, 1-Minute — so the 30min timeframe added in Story 2.1 is visible in the browse list (it is currently the only native timeframe omitted from the row: `bar_count_30min` exists on `CatalogInstrument` but is dropped from `TickerRow`, the UI/REST row construction, and the template). A timeframe with `bar_count == 0` reads as "no data" (rendered `—`/`0`), never absent from the header. [Source: src/api/models/explorer.py:86-112 `TickerRow` (no `bar_count_30min`); src/db/models/catalog_instrument.py:104 `bar_count_30min`; src/api/models/explorer.py:26-30 `ExplorerTimeframe` (30m is a first-class member); Story 2.1 30-minute convention]
3. **Given** the paginated ticker list across ~5,000 ETFs, **When** a page renders, **Then** the render reads **only** from the DB metadata/catalog store (`catalog_instruments` via `MetadataService.list_instruments_with_search` → `CatalogInstrumentRepository`) with a `LIMIT`/`OFFSET` page of `EXPLORER_PAGE_SIZE` (25) — it never scans Parquet directories and never opens a `ParquetDataCatalog` — so the list render meets NFR2 (< 500ms). The list path must not gain a `DataCatalog`/`ParquetDataCatalog` dependency. [Source: epics.md Story 4.1 AC2 / NFR2; src/services/firstrate/metadata_service.py:121; src/db/repositories/catalog_instrument_repository.py:212-261; src/api/ui/explorer.py:213 `_get_ticker_data`]
4. **Given** the REST mirror `GET /api/explorer/tickers`, **When** it returns a `TickerListResponse`, **Then** each `TickerRow` in the JSON also carries `bar_count_30min` (parity with the UI construction — the two builders at `src/api/ui/explorer.py:229` and `src/api/rest/explorer.py:181` must stay in sync). [Source: src/api/rest/explorer.py:143-200]

## Tasks / Subtasks

- [x] **Task 1: Add `bar_count_30min` to the `TickerRow` presentation model** (AC: #2, #4) — *write/extend the unit test in `tests/unit/api/test_explorer_models.py` FIRST (TDD Red→Green)*
  - [x] In `src/api/models/explorer.py`, add `bar_count_30min: int = 0` to `TickerRow` (place it after `bar_count_hourly` to mirror the timeframe ordering D → 1H → 30m → 5m → 1m). Update the `TickerRow` docstring `Attributes:` block to list `bar_count_30min`. Default `0` keeps every existing constructor call valid. [Source: src/api/models/explorer.py:86-112]
  - [x] Do **not** touch `TickerStatsResponse` (Story 4.5 owns per-timeframe stats) or `coverage_pct`. Keep the model file `< 500` lines. No new imports (int field only, so no F401/F821 risk).

- [x] **Task 2: Populate `bar_count_30min` in BOTH row builders** (AC: #2, #4)
  - [x] `src/api/ui/explorer.py` `_get_ticker_data` (~line 229): add `bar_count_30min=inst.bar_count_30min,` to the `TickerRow(...)` construction.
  - [x] `src/api/rest/explorer.py` (~line 181): add the identical `bar_count_30min=inst.bar_count_30min,` to its `TickerRow(...)` construction. These two builders are duplicated and MUST stay in sync (documented caution in the codebase). [Source: src/api/ui/explorer.py:229-243; src/api/rest/explorer.py:181-195]
  - [x] No new imports — `bar_count_30min` is already a column on the `CatalogInstrument` instances returned by `list_instruments_with_search`.

- [x] **Task 3: Render the 30min column in the browse table** (AC: #1, #2)
  - [x] In `templates/explorer/ticker_list.html`, update the bar-counts column so all five native timeframes show. Header (line ~110): change `D / 1H / 5m / 1m` → `D / 1H / 30m / 5m / 1m`. Body cell (line ~185): insert `{{ format_bar_count(row.bar_count_30min) }}` between the hourly and 5min values → `{{ format_bar_count(row.bar_count_daily) }} / {{ format_bar_count(row.bar_count_hourly) }} / {{ format_bar_count(row.bar_count_30min) }} / {{ format_bar_count(row.bar_count_5min) }} / {{ format_bar_count(row.bar_count_minute) }}`.
  - [x] Keep the sort button target on `bar_count_daily` (sorting on 30min is out of scope; `_SORTABLE_COLUMNS` is unchanged). The column stays `hidden xl:table-cell` (no layout change beyond the extra value). Do not alter the pills, pagination, or empty-state blocks. [Source: templates/explorer/ticker_list.html:103-112,182-187]
  - [x] Invoke the `web-ui-development` skill conventions before editing the template (HTMX/Jinja2 fragment; no new routes, no HTMX attribute changes). This is a display-only column addition to an existing fragment. [Source: CLAUDE.md "UI changes: Always invoke the web-ui-development skill"]

- [x] **Task 4: Tests** (AC: #1, #2, #3, #4) — TDD, tests authored before/with the source edits
  - [x] **Unit (`tests/unit/api/test_explorer_models.py`, `@pytest.mark.unit`):** assert a `TickerRow(...)` accepts and round-trips `bar_count_30min` (e.g. construct with `bar_count_30min=42`, assert the attribute and that `.model_dump()` includes it); assert the default is `0` when omitted (back-compat for existing constructors). [Source: tests/unit/api/test_explorer_models.py]
  - [x] **Component (`tests/component/api/test_explorer_routes.py`, existing `_make_instrument` MagicMock factory + `AsyncMock` MetadataService + `app.dependency_overrides`):** extend the `_make_instrument` fixture to set `bar_count_30min` (mirror the other bar_count fields). For **both** the UI `GET /explorer/ticker-list` fragment and the REST `GET /api/explorer/tickers`: (a) assert an ETF row (`asset_class="ETF"`) renders/returns; (b) UI — assert the rendered HTML contains the 30m column header (`30m`) and the instrument's `bar_count_30min` value in the row; (c) REST — assert the JSON `tickers[0].bar_count_30min` equals the stub value. Add an ETF-filter assertion: request with `asset_class=ETF` and confirm the stubbed `list_instruments_with_search` is called with `asset_class="ETF"` (AC1 filterability). [Source: tests/component/api/test_explorer_routes.py:14 `_make_instrument`, :56-58 overrides]
  - [x] **DB-only / no-Parquet regression (AC3):** in the same component test, assert the ticker-list path does **not** resolve the `DataCatalog`/`ParquetDataCatalog` dependency — i.e. the `get_metadata_service` override is exercised while `get_data_catalog_service` is not required for the list routes (the list routes' signatures take only `Metadata`, not `DataCatalog`). A lightweight guard: the fragment/REST list endpoints render with only `get_metadata_service`, `get_catalog_list`, `get_default_catalog` overridden (no catalog-service override needed), proving no Parquet read. [Source: src/api/ui/explorer.py:344-353 (list route deps); src/api/rest/explorer.py:143 (list route deps)]

- [x] **Task 5: Verify** (AC: all)
  - [x] `uv run ruff check .` clean (mind the F401/F821 import gate — this story adds no new imports, so the risk is only a half-applied edit). → All checks passed.
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: 6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). The new field is a plain annotated `int`. → Success: no issues found in 352 source files.
  - [x] `make test-unit` green (explorer model delta); `make test-component` green (explorer routes UI + REST + ETF filter + no-Parquet guard). No regressions in existing explorer tests. → explorer unit+component 65 passed.
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`.
  - [x] Optional UI smoke via the `e2e-ui-testing` / `agent-browser` skill is deferred to Story 4.6 (Explorer Accuracy Verification) — not required here.

## Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). **Clean on the diff** — all three layers confirmed the field addition is internally consistent (ordering `daily→hourly→30min→5min→minute` identical across model, both builders, and template; header/value 1:1 alignment; DB-only list path preserved; REST/UI parity). All 4 ACs PASS and scope boundaries respected. One Low, pre-existing, out-of-scope item deferred. 0 decision-needed · 0 patch · 1 defer · rest dismissed.

- [x] [Review][Defer] **Stats/detail panel omits the 30min tile, so 30m is visible in browse but not in the ticker detail panel** [src/api/models/explorer.py:163-166; templates/explorer/stats_panel.html:19-49] — deferred, pre-existing. `TickerStatsResponse` has `bar_count_daily/hourly/5min/minute` but no `bar_count_30min`, and `stats_panel.html` renders D / 1H / 5m / 1m tiles only. After this story an ETF's browse row shows a 30m column, but clicking through to the stats panel drops 30m. This is the same Story-2.1 "wire 30min by hand everywhere" debt and is **explicitly owned by Story 4.5 (Per-Timeframe ETF Data Statistics)** per this story's scope boundary ("No per-timeframe statistics … Story 4.5 (`TickerStatsResponse`). Leave it untouched."). Not caused by this diff's edited lines; fixing it here would violate the 4.5 scope. Recorded in `deferred-work.md`.

**Dismissed as noise:** (a) Blind Hunter's "verify the producing layer populates `inst.bar_count_30min`" caveat — Edge Case Hunter confirmed the column is `nullable=False, server_default="0"` (`catalog_instrument.py:104-106`) with a backfilling Alembic migration, so no `None`/`AttributeError` is possible. (b) Acceptance Auditor's "a stricter test could assert `get_data_catalog_service` is never invoked" — the spec explicitly accepts the lightweight no-override guard; the list route signatures take only `Metadata`, so a stricter assertion adds nothing.

## Dev Notes

### The core idea (why this story exists)
Epic 4 extends the **existing** Phase-1 explorer (Jinja2 + HTMX + `NavigationState` + TradingView charts) to ETFs, read-only stance preserved. The explorer already filters by `asset_class` end-to-end (repo `WHERE asset_class`, pills, `ExplorerPageState.asset_class`), so "list ETFs alongside/filterable-from Stocks" (AC1) is largely satisfied by existing machinery pointed at the ETF catalog. **The one real gap** is that Story 2.1 introduced the 30-minute timeframe as a first-class native ETF timeframe, but the browse row never surfaced it: `CatalogInstrument.bar_count_30min` exists and is populated at import, `ExplorerTimeframe.THIRTY_MIN` is a first-class enum member, yet `TickerRow` (and both row builders + the template) still shows only `D / 1H / 5m / 1m`. This story closes that gap so an operator browsing ETFs sees the complete, honest set of available timeframes. [Source: epics.md Epic 4 intro + Story 4.1; Story 2.1 30-minute convention; MEMORY project_phase2_epic2_story21_30min — "ExplorerTimeframe is the central enum, but bridge map / VALID_TIMEFRAMES / presentation models must be wired by hand"]

### Why the gap is exactly `bar_count_30min` (traceable)
- `CatalogInstrument` has all five bar-count columns incl. `bar_count_30min` (src/db/models/catalog_instrument.py:47,104).
- `ExplorerTimeframe` enumerates all five, THIRTY_MIN included, each mapping label → bar_type_spec → `bar_count_field` (src/api/models/explorer.py:26-30). The chart panel already derives available timeframes over `ALL_TIMEFRAMES` (src/api/ui/explorer.py:457) — so 30m already works in the chart; only the **browse row** omits it.
- `TickerRow` declares `bar_count_daily/hourly/5min/minute` — **no `bar_count_30min`** (src/api/models/explorer.py:108-111). Both builders and the template inherited that omission. The whole story is: add the missing field, populate it in both builders, render it.

### The seam (data + control flow — no new I/O)
```
catalog_instruments (DB)  ──MetadataService.list_instruments_with_search──▶  list[CatalogInstrument]
   (bar_count_daily/hourly/30min/5min/minute, date_range_*, asset_class, nautilus_id)
        │                                   │
        │  UI: _get_ticker_data             │  REST: rest/explorer.py list builder
        ▼                                   ▼
   TickerRow(... bar_count_30min ...)   TickerRow(... bar_count_30min ...)   ← ADD the field in BOTH
        │                                   │
        ▼                                   ▼
   ticker_list.html  (D / 1H / 30m / 5m / 1m)     TickerListResponse (JSON incl. bar_count_30min)
```
Read is DB-only (repository `SELECT ... LIMIT/OFFSET`), **no `ParquetDataCatalog`** on the list path — that is exactly what keeps AC3/NFR2 (< 500ms) true, and the story must not regress it. [Source: src/db/repositories/catalog_instrument_repository.py:212-261]

### Performance (AC3 / NFR2) — already satisfied, keep it that way
The paginated list already reads a single indexed `SELECT` (count + `LIMIT 25 OFFSET n`, ordered by a whitelisted column) from `catalog_instruments` and never touches Parquet. Adding one more already-selected integer column (`bar_count_30min`) to the row projection adds nothing measurable. Do **not** introduce any per-row catalog/Parquet read to compute "available timeframes" — the bar-count columns already encode availability (`count > 0`). [Source: src/db/repositories/catalog_instrument_repository.py:212-261; src/api/models/explorer.py:15 `EXPLORER_PAGE_SIZE = 25`]

### Scope boundaries (do NOT do here)
- **No new DB column / migration** — `bar_count_30min` already exists and is populated by the ETF import (Epic 2). This is a read-path/presentation change only.
- **No search/filter work** — typing-to-filter by symbol and the < 300ms NFR3 are **Story 4.2**. Don't touch `#search-input` HTMX debounce or `list_by_catalog_with_search`'s ILIKE beyond what already exists.
- **No chart work** — windowed TradingView chart at 30min is **Story 4.3** (already wired via `ExplorerTimeframe`/`ALL_TIMEFRAMES` in the chart panel). Don't touch `chart_panel_fragment`.
- **No metadata panel / N/A rendering** — name/venue/sector `display_*` `@computed_field` props are **Story 4.4**. This story keeps the existing `row.name` `—` fallback as-is; do not add `instrument_metadata` joins.
- **No per-timeframe statistics** — row-count/date-range/min-max price per timeframe is **Story 4.5** (`TickerStatsResponse`). Leave it untouched.
- **No sort on 30min** — `_SORTABLE_COLUMNS` stays `{ticker, date_range_start, date_range_end, bar_count_daily}`; the header sort button remains on `bar_count_daily`.
- **No backtestability gate on the list** — the list intentionally shows all imported tickers; `nautilus_id`-based gating lives on the chart/stats routes (404), not the browse list. Don't add a backtestable filter here.

### Presentation-model + duplicated-builder convention
`TickerRow`/`TickerListResponse` are the shared presentation models for BOTH the HTMX UI (`src/api/ui/explorer.py`) and the JSON REST API (`src/api/rest/explorer.py`). The two row-construction sites are **intentionally duplicated** and there is a standing caution to keep them in sync when adding fields — this story adds a field, so it MUST edit both (Task 2). The `@computed_field` pattern (as in `coverage_pct`) is the idiom for **derived** display props; `bar_count_30min` is raw DB data, so it's a plain field, not a computed one. [Source: src/api/models/explorer.py:86-146; Explore report §5]

### Testing standards (test pyramid)
- **Unit** for the pure model change (`tests/unit/api/test_explorer_models.py`, `@pytest.mark.unit`) — no Nautilus, parallel-safe.
- **Component** for the routes with test doubles (`tests/component/api/test_explorer_routes.py`) — `MagicMock(spec=CatalogInstrument)` via `_make_instrument`, `AsyncMock` MetadataService, FastAPI `app.dependency_overrides`, `TestClient`. This is the established template for both the UI fragment and the REST endpoint; extend it, don't invent a new harness.
- No integration/`--forked` tier needed — no `BacktestEngine`, no Nautilus C extensions, no real Parquet on the list path. [Source: CLAUDE.md Decision Heuristics "Test tier"; tests/component/api/test_explorer_routes.py]

### Project Structure Notes
- Files touched: `src/api/models/explorer.py` (add field), `src/api/ui/explorer.py` (UI builder), `src/api/rest/explorer.py` (REST builder), `templates/explorer/ticker_list.html` (column). Tests: `tests/unit/api/test_explorer_models.py`, `tests/component/api/test_explorer_routes.py`. All are existing files — prefer editing over creating (CLAUDE.md "New file vs edit").
- No conflicts with the unified structure: models under `src/api/models/`, routes split UI/REST under `src/api/ui`/`src/api/rest`, templates under `templates/explorer/`. Naming (`bar_count_30min`) matches the existing `bar_count_*` convention exactly.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 4.1: Browse Imported ETF Tickers] — user story + AC1 (list w/ date range & timeframes) + AC2 (< 500ms, DB-only, no Parquet scan / NFR2).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 4: Data Explorer — ETF Support] — "Extends the Phase 1 explorer … read-only stance preserved."
- [Source: src/api/models/explorer.py:18-67] — `ExplorerTimeframe` (THIRTY_MIN member, `bar_count_field` mapping).
- [Source: src/api/models/explorer.py:86-128] — `TickerRow` (target of the new field; `coverage_pct` `@computed_field` idiom).
- [Source: src/db/models/catalog_instrument.py:47,104] — `bar_count_30min` column (already exists/populated).
- [Source: src/api/ui/explorer.py:213-248,344-387] — `_get_ticker_data`, `ticker_list_fragment` (UI builder + fragment route, `Metadata`-only deps).
- [Source: src/api/rest/explorer.py:143-200] — `GET /api/explorer/tickers` (REST builder to keep in sync).
- [Source: templates/explorer/ticker_list.html:103-112,182-187] — bar-count header + body cell to extend to five timeframes.
- [Source: src/db/repositories/catalog_instrument_repository.py:212-261] — DB-only paginated query (AC3 / NFR2 evidence; `_SORTABLE_COLUMNS`).
- [Source: tests/component/api/test_explorer_routes.py] — component test template (`_make_instrument`, dependency overrides).
- [Source: MEMORY project_phase2_epic2_story21_30min] — 30min wiring must be done by hand across presentation models; this story is one such hand-wiring point.
- [Source: MEMORY project_explorer_catalog_scoping_bug] — explorer catalog-scoping precedent (this story does not alter catalog resolution; the list path already reads the selected catalog).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/unit/api/test_explorer_models.py tests/component/api/test_explorer_routes.py -q` → 65 passed.
- `uv run ruff check .` → All checks passed.
- `uv run mypy .` → Success: no issues found in 352 source files (no new errors vs baseline).

### Completion Notes List

- Root cause of the gap: `bar_count_30min` (added to `CatalogInstrument` in Story 2.1) was never surfaced in the browse row — `TickerRow` declared only `daily/hourly/5min/minute`, and both duplicated row builders + the template inherited the omission. The 30min timeframe already worked in the chart panel (`ALL_TIMEFRAMES`), so the fix was scoped precisely to the browse list.
- Change is read-path/presentation only: added `bar_count_30min: int = 0` to `TickerRow`, populated it in the UI (`_get_ticker_data`) and REST (`rest/explorer.py`) builders, and rendered it in `ticker_list.html` (header `D / 1H / 30m / 5m / 1m` + body cell). No DB migration (column already exists/populated), no new imports, no new I/O — the list path stays DB-only (AC3/NFR2 preserved; no `ParquetDataCatalog` on the list route signatures).
- `default = 0` keeps every pre-existing `TickerRow(...)` constructor and test valid (back-compat).
- Tests: unit round-trip + default; component asserts REST parity (`bar_count_30min` in JSON), the 5-timeframe header, an ETF row's 30min value rendered, and ETF `asset_class` filter forwarding.
- Out of scope, untouched (per Dev Notes scope boundaries): search/filter debounce (4.2), chart (4.3), metadata N/A panel (4.4), per-timeframe stats/`TickerStatsResponse` (4.5), sort columns (`_SORTABLE_COLUMNS`).

### File List

- `src/api/models/explorer.py` — added `bar_count_30min` field + docstring to `TickerRow`.
- `src/api/ui/explorer.py` — populate `bar_count_30min` in `_get_ticker_data` row builder.
- `src/api/rest/explorer.py` — populate `bar_count_30min` in REST row builder (parity).
- `templates/explorer/ticker_list.html` — 30m in the bar-count column header + body cell.
- `tests/unit/api/test_explorer_models.py` — `bar_count_30min` field + default tests.
- `tests/component/api/test_explorer_routes.py` — REST parity, 30m header, ETF-row value, ETF filter tests.
