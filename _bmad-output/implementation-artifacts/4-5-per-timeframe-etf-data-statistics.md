# Story 4.5: Per-Timeframe ETF Data Statistics

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want basic data statistics for a selected ETF per timeframe (including the new 30-minute timeframe),
so that I can sanity-check coverage and price ranges before relying on the data for a backtest.

## Acceptance Criteria

1. **Given** a selected ETF, **When** the operator views its statistics, **Then** row (bar) count, date range, and min/max prices are shown **per timeframe including 30min** — the stats panel surfaces a **30-Min Bars** tile alongside the existing Daily / 1-Hour / 5-Min / 1-Min tiles, closing the browse-vs-detail inconsistency Story 4.1 deliberately deferred (30m already renders in the browse ticker row but not in the stats/detail panel). [Source: epics.md Story 4.5 AC1; deferred-work.md; project memory `project_phase2_epic4_story41_browse`]
2. **Given** the statistics request, **When** catalog metadata is read, **Then** it completes in **< 500ms (NFR4)** without loading full price data — the per-timeframe bar counts (incl. `bar_count_30min`) come from the DB-sourced `CatalogInstrument` row, and only the *active* timeframe's price range is computed from Parquet (unchanged from the existing single-tf `query_bars` behavior). Adding the 30min tile MUST NOT add a second Parquet scan. [Source: epics.md Story 4.5 AC2; src/api/stats_service.py:71-108]
3. **Given** the active timeframe is `30m`, **When** the stats panel renders, **Then** the 30-Min Bars tile carries the same active-timeframe highlight (`ring-blue-500/40`) the other tiles use when active, and the Price Range card's subtitle echoes `30m timeframe` — consistent with the existing highlight contract for `D`/`1H`/`5m`/`1m`. [Source: templates/explorer/stats_panel.html:20-61; src/api/models/explorer.py:26-30 (`ExplorerTimeframe.THIRTY_MIN`)]
4. **Given** the JSON REST stats endpoint (`GET /api/explorer/ticker/{ticker}/stats`), **When** it returns `TickerStatsResponse`, **Then** the payload includes `bar_count_30min` (default `0` when absent) so the REST contract matches the UI tile set — no field is UI-only. [Source: src/api/models/explorer.py:149-170; src/api/rest/explorer.py:39-55]

## Tasks / Subtasks

- [x] **Task 1: Add `bar_count_30min` to `TickerStatsResponse`** (AC: #4) — *write/extend the model test in `tests/unit/api/test_explorer_models.py` FIRST (TDD Red→Green)*
  - [x] In `src/api/models/explorer.py`, add `bar_count_30min: int = 0` to `TickerStatsResponse`, placed between `bar_count_hourly` and `bar_count_5min` to mirror the coarse→fine ordering used everywhere else (`D / 1H / 30m / 5m / 1m`). Update the class docstring's attribute list to mention it. [Source: src/api/models/explorer.py:161-164; the same `bar_count_30min` field already exists on `CatalogInstrument` (src/db/models/catalog_instrument.py:104) and on `ExplorerTimeframe.THIRTY_MIN` (explorer.py:28)]
  - [x] Note: `TickerRow` already carries the other four counts but **not** 30min (Story 4.1 left the browse row rendering 30m off `CatalogInstrument.bar_count_30min` directly, not off `TickerRow`); this story only touches `TickerStatsResponse`. Do **not** widen `TickerRow` here.

- [x] **Task 2: Populate `bar_count_30min` in the shared stats builder** (AC: #1, #2, #4) — *extend `tests/component/api/test_stats_panel_routes.py` FIRST*
  - [x] In `src/api/stats_service.py`, in `_build_ticker_stats`, add `bar_count_30min=getattr(instrument, "bar_count_30min", 0) or 0` to the `TickerStatsResponse(...)` construction, next to the existing `bar_count_*` reads. `getattr(..., 0)` mirrors the defensive read already used for the other four counts (back-compat with mocks/rows lacking the attr). [Source: src/api/stats_service.py:121-136]
  - [x] No change to the `query_bars` call — the single active-timeframe Parquet scan is untouched, preserving NFR4 (AC2). The 30min tile is a pure DB-count add.

- [x] **Task 3: Render the 30-Min Bars tile in the stats panel** (AC: #1, #3) — *extend `tests/component/api/test_stats_panel_routes.py` FIRST*
  - [x] In `src/api/ui/explorer.py`, add `"bar_count_30min": stats.bar_count_30min,` to the dict returned by `_stats_template_context` (between `bar_count_hourly` and `bar_count_5min`). [Source: src/api/ui/explorer.py:130-142]
  - [x] In `templates/explorer/stats_panel.html`, insert a **30-Min Bars** card between the **1-Hour Bars** card and the **5-Min Bars** card, matching the existing card markup exactly: active-highlight guard `{% if active_tf_label == '30m' %}ring-1 ring-blue-500/40{% endif %}`, label `30-Min Bars`, value `{{ format_bar_count(bar_count_30min) }}`. This makes the grid 8 cards (Date Range, Daily, 1-Hour, **30-Min**, 5-Min, 1-Min, Price Range, Nautilus ID). [Source: templates/explorer/stats_panel.html:27-49]
  - [x] Update `templates/explorer/stats_panel_skeleton.html`: change `range(7)` → `range(8)` and the leading comment from "seven" → "eight" so the deep-link skeleton card count matches the real grid (the skeleton is swapped by the real grid on load, so a mismatched count causes a brief layout jump). [Source: templates/explorer/stats_panel_skeleton.html:2-7]
  - [x] Update the `_stats_template_context` / `stats_panel_fragment` docstrings that say "7 stat cards" → "8 stat cards". [Source: src/api/ui/explorer.py:123-159]

- [x] **Task 4: Update tests for the new 8-card grid** (AC: #1, #3, #4) — TDD: these assertions should be written to fail first, then pass after Tasks 1-3
  - [x] `tests/component/api/test_stats_panel_routes.py`:
    - REST: in `test_returns_200_with_stats`, assert `data["bar_count_30min"] == 15625` (the fixture `_make_instrument` already sets `bar_count_30min=15625`).
    - UI: rename/extend `test_contains_seven_card_labels` → assert **`"30-Min Bars" in text`** in addition to the existing six labels (now eight cards). Keep it asserting all labels present.
    - UI skeleton: update `test_skeleton_rendered_when_ticker_selected` — `text.count("stats-skeleton-card") == 8` (was 7).
    - UI highlight: add a `test_active_tf_30m_highlighted` — request `tf=30m`, assert `ring-blue-500` present and that the "30-Min Bars" card is the highlighted one (e.g. slice from `30-Min Bars` label to next card and assert the ring class appears before it, or simply assert `ring-blue-500` present with `tf=30m` like the existing `1H` test).
  - [x] `tests/unit/api/test_explorer_models.py`: add/extend a test that `TickerStatsResponse` defaults `bar_count_30min` to `0` and round-trips a provided value.
  - [x] Re-check any test asserting a hard-coded card count of 7 (e.g. comments/docstrings) and bump to 8. Grep `seven` / `count(...) == 7` in the stats test module.

- [x] **Task 5: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (this story adds no new imports, but the auto-linter runs after each edit; re-read files after edits).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: up to 6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests; a recent run showed 0 — do not let this story add any).
  - [x] `uv run pytest tests/component/api/test_stats_panel_routes.py tests/unit/api/test_explorer_models.py -q` (or `make test-unit` + the component stats module) green.
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`. The template + model + one-line context add stay well under.
  - [x] Read-path stays DB-only for bar counts (no extra Parquet scan) → NFR4 (<500ms) preserved.

## Dev Notes

### The core idea (why this story exists)
Epic 4 brings the ETF universe into the Data Explorer. Story 2.1 made **30-minute** a first-class native timeframe (`ExplorerTimeframe.THIRTY_MIN`, `CatalogInstrument.bar_count_30min`). Story 4.1 surfaced 30m in the **browse ticker row** but deliberately left `TickerStatsResponse` / `stats_panel.html` untouched — so today 30m shows in the list but **not** in the per-ticker stats/detail panel. Story 4.5's FR31 mandate is "basic data statistics per timeframe **including 30min**", so its real work is closing exactly that browse-vs-detail gap: add the `bar_count_30min` field, populate it from the DB row, and render the 30-Min Bars tile. This is the recurring "wire 30min by hand everywhere" debt (`project_phase2_epic2_story21_30min`), now paid down in the stats surface. [Source: epics.md Story 4.5; deferred-work.md; project memory `project_phase2_epic4_story41_browse`, `project_phase2_epic2_story21_30min`]

### The seam (what changes, end to end)
```
CatalogInstrument.bar_count_30min  (DB column, already imported by Story 2.1)
        │  getattr(instrument, "bar_count_30min", 0)
        ▼
_build_ticker_stats  (src/api/stats_service.py)  → TickerStatsResponse.bar_count_30min   ← NEW field (Task 1/2)
        │                                                     │
        │  REST: /api/explorer/ticker/{t}/stats returns it   │  UI: _stats_template_context flattens it (Task 3)
        ▼                                                     ▼
   JSON payload (AC4)                              stats_panel.html "30-Min Bars" tile (AC1/AC3)
```
Only the **active** timeframe still triggers a Parquet `query_bars` for the price range — bar counts are all DB reads, so the 30min tile costs nothing extra (AC2 / NFR4). [Source: src/api/stats_service.py:40-136; src/api/rest/explorer.py:39-55; src/api/ui/explorer.py:123-159]

### Timeframe ordering convention
Everywhere in the explorer the timeframes render coarse→fine: **D / 1H / 30m / 5m / 1m** (browse row, `ExplorerTimeframe` enum order, chart tf toggles). Place the new field and the new tile at the **30m** position (between hourly and 5-min) to stay consistent — do not append it last. [Source: src/api/models/explorer.py:26-30; templates/explorer/ticker_list.html (browse row order from Story 4.1)]

### Scope boundaries (do NOT do here)
- **No new DB column / migration** — `catalog_instruments.bar_count_30min` already exists (Story 2.1). This story only threads it through the stats response + template.
- **No second Parquet scan** — bar counts are DB-sourced; only the active-tf price range reads Parquet, exactly as today. Do not add per-tf price-range computation (that would breach NFR4 and is out of scope for "basic statistics").
- **No `TickerRow` change** — the browse row already renders 30m (Story 4.1, off `CatalogInstrument` directly). This story is the *stats/detail* surface only.
- **No supplementary-panel change** — dividends/splits/company-profile (the `has_*` flags) are timeframe-independent and out of scope.
- **No chart / chart-panel change** — the chart already supports 30m (Story 2.1/4.3 scope). Only the stats grid gains the tile. Note `_stats_template_context` is also merged into the chart-panel OOB context, so the 30min key flows there for free — verify the OOB path still renders the grid (existing `test_oob_response_does_not_contain_skeleton` covers the grid presence).
- **No accuracy/TradingView verification** — that is Story 4.6.

### Design decision — one shared builder, two callers
`_build_ticker_stats` is the single source for both the REST JSON endpoint and the HTMX fragment (stats_service.py module docstring). Adding `bar_count_30min` there means REST (AC4) and UI (AC1) both get it from one edit; the UI additionally needs the `_stats_template_context` flatten + template tile (the template can't read the Pydantic model's field without the flatten dict). [Source: src/api/stats_service.py:1-11; src/api/ui/explorer.py:123-142]

### Testing standards summary
- **Component tier** (`tests/component/api/test_stats_panel_routes.py`, `@pytest.mark.component`, FastAPI `TestClient` + dependency-override mocks) is the primary tier here — it exercises both the REST endpoint and the HTMX fragment with a mocked `CatalogInstrument` (fixture already sets `bar_count_30min=15625`) and a mocked catalog service returning fake bars. No Nautilus, no real DB.
- **Unit tier** for the Pydantic model default/round-trip.
- TDD: write the failing assertion (missing field / missing "30-Min Bars" label / card count 7→8) first, then implement. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill; existing test module patterns]

### Previous-story intelligence
- **Story 4.1** (browse ETF tickers, done 2026-07-16) added 30m to `TickerRow` + both row builders + `ticker_list.html`, and its code review explicitly filed the stats-panel 30min tile as a **deferred finding → Story 4.5**. `TickerStatsResponse` was left untouched on purpose. This story is the direct follow-through. [Source: deferred-work.md; project memory `project_phase2_epic4_story41_browse`]
- **Story 2.1** (30-min convention) established that `ExplorerTimeframe` is the central enum but the bridge map / `VALID_TIMEFRAMES` / presentation models must each be wired by hand — hence the recurring debt. For 4.5 the manual touch points are: `TickerStatsResponse` field, `_build_ticker_stats` populate, `_stats_template_context` flatten, `stats_panel.html` tile, skeleton card count. [Source: project memory `project_phase2_epic2_story21_30min`]
- **mypy baseline**: `reference_mypy_baseline_debt` records up to 6 pre-existing errors (ui/explorer, ui/backtests + 2 tests) but a recent run showed 0. Re-run and ensure this story adds none.

### Project Structure Notes
- Touch points (all existing files, no new files): `src/api/models/explorer.py`, `src/api/stats_service.py`, `src/api/ui/explorer.py`, `templates/explorer/stats_panel.html`, `templates/explorer/stats_panel_skeleton.html`, plus the two test modules. All within the established explorer surface — no new concepts, no new routes.
- Aligns with the read-only explorer stance (NFR): no writes, no live data sources, DB + local Parquet catalog only.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 4.5: Per-Timeframe ETF Data Statistics (FR31)]
- [Source: src/api/models/explorer.py:26-30, 149-170]
- [Source: src/api/stats_service.py:40-136]
- [Source: src/api/ui/explorer.py:123-159]
- [Source: templates/explorer/stats_panel.html; templates/explorer/stats_panel_skeleton.html]
- [Source: src/db/models/catalog_instrument.py:104 (`bar_count_30min`)]
- [Source: _bmad-output/implementation-artifacts/deferred-work.md (Story 4.1 deferred finding)]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run ruff check .` → All checks passed!
- `uv run mypy .` → Success: no issues found in 352 source files (0 new errors; baseline clean)
- `uv run pytest tests/component/api/test_stats_panel_routes.py tests/unit/api/test_explorer_models.py` → 45 passed
- `uv run pytest tests/component/api/test_stats_panel_routes.py tests/component/api/test_chart_panel_routes.py tests/unit/api/test_explorer_models.py` → 87 passed
- `uv run pytest tests/component/api/test_explorer_routes.py tests/ui/test_explorer_errors.py` → 42 passed (no regressions)

### Completion Notes List

- Closed the browse-vs-detail 30min gap Story 4.1 deferred: `TickerStatsResponse` now carries `bar_count_30min`, `_build_ticker_stats` populates it from the DB `CatalogInstrument` row (defensive `getattr(..., 0) or 0`, matching the other four counts), and the stats panel renders a **30-Min Bars** tile between 1-Hour and 5-Min (coarse→fine `D / 1H / 30m / 5m / 1m` order).
- AC2 / NFR4 preserved: bar counts are pure DB reads; only the *active* timeframe still triggers a single Parquet `query_bars` for the price range — no second scan added. When active tf is `30m`, the tile carries the `ring-blue-500/40` highlight and the Price Range subtitle echoes `30m timeframe` (AC3).
- AC4: the REST `/api/explorer/ticker/{ticker}/stats` payload now includes `bar_count_30min` for free (single shared builder); grid is now 8 cards, skeleton bumped 7→8 to match.
- No new DB column/migration (`catalog_instruments.bar_count_30min` already exists from Story 2.1); `TickerRow` untouched (browse row already renders 30m); no supplementary/chart changes; no new imports (F401/F821 gate clean).
- Test 30m-highlight assertion locates the outer card via its `bg-slate-900` class (the inner label div does not carry the ring) to avoid a false negative.

### File List

- `src/api/models/explorer.py` (M) — add `bar_count_30min: int = 0` to `TickerStatsResponse`
- `src/api/stats_service.py` (M) — populate `bar_count_30min` in `_build_ticker_stats`
- `src/api/ui/explorer.py` (M) — flatten `bar_count_30min` in `_stats_template_context`; docstring 7→8 cards
- `templates/explorer/stats_panel.html` (M) — new 30-Min Bars tile
- `templates/explorer/stats_panel_skeleton.html` (M) — skeleton card count 7→8
- `tests/unit/api/test_explorer_models.py` (M) — assert `bar_count_30min` default/populated
- `tests/component/api/test_stats_panel_routes.py` (M) — REST payload, 8-card labels, 30m highlight, skeleton count
- `src/api/rest/explorer.py` (M) — stats endpoint `tf` param description now lists `30m` (code-review Low doc fix)

### Senior Developer Review (AI)

**Reviewed:** 2026-07-16 · **Outcome:** Approve · Three adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor) — all clean, no High/Med findings.

- **Blind Hunter:** No real bugs. Field ordering (daily→hourly→30min→5min→minute) consistent across model, builder, context, and template; `getattr(..., 0) or 0` null-safe; `'30m'` highlight condition matches `ExplorerTimeframe.THIRTY_MIN.label`; skeleton count 8 matches the real 8-card grid.
- **Edge Case Hunter:** No unhandled edge cases. Verified `active_tf_label == '30m'` resolves correctly (VALID_TF_LABELS → from_label → .label); no Jinja `UndefinedError` risk on either render path (`stats_panel_fragment` and the `chart_panel.html` OOB include both spread `_stats_template_context`, which now carries `bar_count_30min`); REST endpoint inherits the field via the shared builder.
- **Acceptance Auditor:** Fully compliant — AC1–AC4 all PASS, all scope boundaries respected (no new DB column/migration, no `TickerRow` change, no second Parquet scan, no supplementary/chart-panel behavior change). One Low doc observation actioned:

**Action Items:**
- [x] [Low] REST stats `tf` query-param description omitted `30m` (`src/api/rest/explorer.py:51`) — updated to `(D, 1H, 30m, 5m, 1m)`. (The chart/chart-bars endpoints carry the same pre-existing drift but are outside this story's scope — left for their owning story.)

### Change Log

- 2026-07-16 — Story 4.5 implemented (per-timeframe ETF data statistics: 30min tile in stats panel + REST field). Status → review.
- 2026-07-16 — Code review: 3 adversarial layers clean (Approve). Applied one Low doc fix (REST `tf` param description lists 30m).
