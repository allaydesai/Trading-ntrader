# Story 4.3: Chart a Selected ETF at All 5 Timeframes (incl. 30min)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to load a windowed TradingView chart for a selected ETF at any of the five native timeframes including 30min,
so that I can visually inspect its bars at the resolution I need.

## Acceptance Criteria

1. **Given** a selected ETF, **When** the operator picks a timeframe, **Then** a windowed TradingView chart loads for **any of 1min, 5min, 30min, 1hour, 1day** — the chart-panel fragment resolves the requested `tf` through the central `ExplorerTimeframe` enum (Story 2.1) and queries the catalog with that member's `bar_type_spec` (so `30m → 30-MINUTE-LAST`), never a hard-coded or conflated bar-type string. [Source: epics.md Story 4.3 AC1; src/api/models/explorer.py:18-30; src/api/ui/explorer.py:445-476]
2. **Given** the chart-panel toolbar for an ETF, **When** it renders, **Then** **all five** timeframe buttons — including **30m** — are present, sourced from the central enum (`ALL_TIMEFRAMES = list(ExplorerTimeframe)`); the 30m button is **enabled** when the ETF has 30min bars (`bar_count_30min > 0`) and **disabled** (never hidden) when it does not, exactly like the other four. [Source: epics.md Story 4.3 AC1 "30min present in the timeframe selector (sourced from the central enum)"; src/api/ui/explorer.py:457-461; templates/explorer/chart_panel.html]
3. **Given** a single ETF/30min chart request, **When** it loads, **Then** it reads a **windowed** Parquet range (30min uses the enum's `initial_window_days = 90`, anchored to the instrument's `date_range_end`) rather than the full history — the same windowing contract the other intraday timeframes already honor — so the chart completes fast (NFR1: < 2s) and scroll/zoom stays smooth (NFR5: 60fps, unchanged from the Phase 1 Lightweight-Charts renderer). [Source: epics.md Story 4.3 AC2; src/api/ui/explorer.py:390-411,462-476]
4. **Given** a venue-qualified ETF (Epic 3 resolved its `nautilus_id`), **When** any of the five timeframes is charted, **Then** bars are queried against the DB-authoritative `nautilus_id` (e.g. `SPY.ARCA`), and an ETF whose venue is **unresolved** (`nautilus_id` NULL) yields a clean 404 from the chart-panel route (unchanged gate) — never a guessed venue. An empty/missing 30min partition degrades to the empty-state panel (no 500), mirroring the existing daily/5m/1m/1H behavior. [Source: src/api/ui/explorer.py:447-482; src/api/rest/explorer.py:93-121]
5. **Given** the public REST/OpenAPI surface for the chart and stats endpoints, **When** the `tf` parameter is documented, **Then** its description enumerates **all five** labels including `30m` (currently the `Query(...)` descriptions and docstrings read "(D, 1H, 5m, 1m)" and silently omit 30m) — so the operator/consumer sees 30m as a first-class option, consistent with AC1/AC2. [Source: src/api/rest/explorer.py:51,70,81; src/api/ui/explorer.py:437]

## Tasks / Subtasks

- [x] **Task 1: Close the 30min chart-panel test gap (TDD Red→Green)** (AC: #1, #2, #3) — *write tests FIRST in `tests/component/api/test_chart_panel_routes.py`*
  - [x] In `TestChartPanelWindowing`, add `test_30min_loads_windowed_range`: `GET /explorer/chart-panel?catalog=...&ticker=...&tf=30m` → the **first** `query_bars` call (the chart call; `call_args_list[0]`) has `end == date_range_end (2025-12-31)` and `start == date_range_end - timedelta(days=90)` and `bar_type_spec == "30-MINUTE-LAST"`. Mirror `test_5min_loads_windowed_range`/`test_hourly_loads_windowed_range`. [Source: tests/component/api/test_chart_panel_routes.py:453-473; src/api/models/explorer.py:28 (`initial_window_days=90`)]
  - [x] In the REST `TestChartData` group, add `test_30min_timeframe_mapping`: `GET /api/chart/catalog/{ticker}?catalog=...&tf=30m` → `query_bars` called with `bar_type_spec == "30-MINUTE-LAST"` (mirror `test_timeframe_mapping` for 1H). [Source: tests/component/api/test_chart_panel_routes.py:142-145]
  - [x] In `TestChartPanelUIRoute`, add `test_timeframe_toolbar_includes_30m` (all five labels present) + `test_30m_button_active_when_selected` (30m selectable → `aria-pressed="true"`), and `test_30m_button_disabled_when_no_30min_bars`: with `_make_instrument(bar_count_30min=0)` (others non-zero → 30m is the lone disabled button) the 30m button carries the `disabled` attribute (present-but-disabled, AC2). [Source: tests/component/api/test_chart_panel_routes.py:215-223,324-335]
  - [x] Confirmed no new production code needed — the enum + fragment already implement the behavior; the tests lock it in (53 pre-existing + new behavior tests passed on first run).

- [x] **Task 2: ETF-flavored chart verification** (AC: #1, #4) — *tests FIRST*
  - [x] Added `TestEtfChartAllTimeframes` in `tests/component/api/test_chart_panel_routes.py`: `_make_instrument(asset_class="ETF", ticker="SPY", nautilus_id="SPY.ARCA", catalog_name="firstrate-etf")` + `test_etf_charts_each_timeframe` parametrized over `["D", "1H", "30m", "5m", "1m"]`: each `GET /explorer/chart-panel?...&tf=<label>` returns 200 and `query_bars` is called with `instrument_id == "SPY.ARCA"` and the label's expected `bar_type_spec`. Proves the venue-qualified ETF path (Epic 3) charts at every resolution. [Source: src/api/ui/explorer.py:447-476; epics.md Story 4.3]
  - [x] Added `test_etf_unresolved_venue_returns_404`: `_make_instrument(nautilus_id=None)` → `GET /explorer/chart-panel?...&tf=30m` → 404 (the `nautilus_id`-NULL gate; a venue-unresolved ETF is never charted under a guessed venue — Epic 3 / ADR-6). [Source: src/api/ui/explorer.py:448-452; tests/component/api/test_chart_panel_routes.py:234-237]

- [x] **Task 3: Enumerate 30m in the public `tf` docs** (AC: #5) — *test FIRST (OpenAPI schema assertion)*
  - [x] Added `TestChartEndpointDocs::test_chart_endpoint_tf_doc_lists_30m` + `test_stats_endpoint_tf_doc_lists_30m`: fetch `GET /openapi.json`, assert the `tf` query-param description (for `/api/chart/catalog/{ticker}` and `/api/explorer/ticker/{ticker}/stats`) contains `"30m"`. Failed Red first (`'30m' in 'Timeframe label (D, 1H, 5m, 1m)'`), passed Green after the edit.
  - [x] Updated the `tf` `Query(..., description=...)` strings in `src/api/rest/explorer.py` (`get_chart_data` **and** `get_ticker_stats`) and both docstring lines, plus the `chart_panel_fragment` docstring in `src/api/ui/explorer.py`, from `(D, 1H, 5m, 1m)` → `(D, 1H, 30m, 5m, 1m)`. Doc-only — no behavior change, no new literal (`30m` is the enum label, not the bar-type spec, so `test_timeframe_single_definition` stays green). [Source: src/api/rest/explorer.py:51,70,81; src/api/ui/explorer.py:437]

- [x] **Task 4: Verify** (AC: all)
  - [x] `uv run ruff check .` → **All checks passed!**
  - [x] `uv run mypy .` → **Success: no issues found in 352 source files** (no new errors; the doc-string edits change no signatures).
  - [x] Chart/explorer suites green: `uv run pytest tests/component/api/test_chart_panel_routes.py tests/unit/api/test_timeframe_single_definition.py tests/unit/api/test_explorer_models.py tests/unit/api/test_chart_data_models.py tests/unit/api/test_explorer_bridge_url.py tests/component/api/test_explorer_routes.py` → **176 passed**.
  - [x] Size limits respected: only test additions + one-word doc-string edits; no function/class/file exceeds the limits.
  - [x] Live smoke skipped (harness blocks the dev server / no local ETF catalog wired here); component tests are the gating evidence. External-reference spot-check is **Story 4.6**, not this story.

### Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). Acceptance Auditor: **full pass** — all five ACs satisfied, no scope-boundary breaches, "verification + doc" framing verified accurate against source. Three test-quality patches (2 Med, 1 Low) — all fixed; one Low pre-existing gap deferred.

- [x] [Review][Patch] **`test_30m_button_active_when_selected` had a vacuous assertion (Blind+Edge, Med)** [tests/component/api/test_chart_panel_routes.py]. Asserting `'aria-pressed="true"' in text` + `"30m" in text` page-wide proved nothing — the toolbar always emits exactly one `aria-pressed="true"` and always renders the `30m` label, so a `tf=30m → Daily` fallback would still pass green. **Fixed:** added a `_toolbar_button_tag(html, label)` helper that extracts the specific button's opening tag by `aria-label`, and now assert `aria-pressed="true"` is on the **30m** button and **not** on the Daily button.
- [x] [Review][Patch] **`test_30m_button_disabled_when_no_30min_bars` `"disabled" in text` was always-true (Blind+Edge, Med)** [tests/component/api/test_chart_panel_routes.py]. The arrow-key script in `templates/explorer/chart_panel.html` literally contains `button:not([disabled])`, so the substring `disabled` is present on every render regardless of button state. **Fixed:** assert `disabled` on the **30m** button's own tag markup, and additionally that the still-populated **5m** button is **not** disabled (proves the disabled state is specific to 30m, not blanket).
- [x] [Review][Patch] **`test_timeframe_toolbar_includes_30m` used over-broad substrings (Blind+Edge, Low)** [tests/component/api/test_chart_panel_routes.py]. `"D" in text` (and 5m/1m/1H) can match incidental content in the OOB stats/supplementary blocks ("Date Range", "Daily Bars", …). **Fixed:** renamed to `test_timeframe_toolbar_includes_all_five_buttons` and match each of the five via `_toolbar_button_tag` (raises if the specific `aria-label` button is missing).
- [x] [Review][Defer] **Stats panel has no 30-Min bar-count card (Edge, Low)** [src/api/models/explorer.py:161-164; src/api/stats_service.py:126-129]. `TickerStatsResponse` / `_build_ticker_stats` carry `bar_count_daily/hourly/5min/minute` but **not** `bar_count_30min`, so the stats panel shows no 30-Min card. The 30m **chart/toolbar** works (it reads `CatalogInstrument.bar_count_30min` directly); only the stats *display* omits it. **Deferred to Story 4.5** (Per-Timeframe ETF Data Statistics) — pre-existing, explicitly out of this story's scope (Scope boundaries: "No stats/metadata-panel work"). Recorded in `deferred-work.md`.

## Dev Notes

### The core idea (why this story exists — and why it's mostly verification)
Epic 4 extends the **existing** Phase 1 explorer (Jinja2 + HTMX + Lightweight-Charts) to ETFs; it does **not** rebuild the chart. Story 2.1 already expanded the timeframe convention to include 30min by adding `ExplorerTimeframe.THIRTY_MIN = ("30m", "30-MINUTE-LAST", "bar_count_30min", 90)` as the single source of truth. The chart-panel route already iterates `ALL_TIMEFRAMES = list(ExplorerTimeframe)` (all five) for the toolbar, computes `available_tfs` from the `CatalogInstrument.bar_count_*` fields (including `bar_count_30min`), windows the read via `_compute_chart_window` using each member's `initial_window_days` (90 for 30m), and queries with `active_tf.bar_type_spec`. So the runtime behavior for AC1–AC4 **already exists**. This story's job is to (a) **lock that behavior in with the missing 30min + ETF tests** (the chart-panel windowing suite covers D/1H/5m/1m but **not 30m**, and there is no ETF-asset-class chart test), and (b) **fix the one honest gap** — the public REST `tf` descriptions/docstrings still say "(D, 1H, 5m, 1m)" and omit 30m (AC5). Be honest in the completion notes: this is a thin, verification-heavy story, not a from-scratch feature. [Source: epics.md Epic 4 intro:670-674; memory: Phase 2 Epic 2 Story 2.1 — "ExplorerTimeframe is the central enum … presentation models must be wired by hand"]

### The seam (data + control flow) — unchanged, exercised by this story
```
ExplorerTimeframe (central enum, Story 2.1)         CatalogInstrument (Epic 3: venue-qualified nautilus_id + bar_count_30min)
  label "30m" · bar_type_spec "30-MINUTE-LAST"           │
  bar_count_field "bar_count_30min" · window 90d          │  get_instrument(catalog, ticker) → nautilus_id, bar_count_*
        │                                                  ▼
        ▼                                    available_tfs = {tf.label for tf if bar_count_field > 0}
  chart_panel_fragment (GET /explorer/chart-panel?tf=30m)
        │  _compute_chart_window(THIRTY_MIN, start, end) → (end-90d, end)  ← windowed read (AC3)
        ▼
  catalog_service.query_bars(instrument_id=nautilus_id, start, end, bar_type_spec="30-MINUTE-LAST")  ← AC1/AC4
        ▼
  Candle[] → bars_json → chart_panel.html (toolbar renders ALL_TIMEFRAMES; 30m enabled iff in available_tfs) ← AC2
```
Read-only over the local Parquet catalog + metadata DB. No network, no write, no engine. [Source: src/api/ui/explorer.py:390-545]

### What already exists (do NOT rebuild)
- **`ExplorerTimeframe.THIRTY_MIN`** — label `30m`, spec `30-MINUTE-LAST`, count field `bar_count_30min`, window 90d. The `30-MINUTE-LAST` literal is defined **once** (guarded by `test_timeframe_single_definition.py`) — reference the enum, never re-inline it. [Source: src/api/models/explorer.py:28; tests/unit/api/test_timeframe_single_definition.py]
- **Toolbar over all five timeframes** — `templates/explorer/chart_panel.html` loops `timeframes` (= `ALL_TIMEFRAMES`) and disables (not hides) a button not in `available_tfs`; arrow-key nav + focus rings + `aria-pressed` already present. [Source: src/api/ui/explorer.py:529; templates/explorer/chart_panel.html]
- **Windowed reads** — `_compute_chart_window` returns a trailing `initial_window_days` window anchored to `date_range_end` (falls back to `now()` when `date_range_end` is None). Daily (`initial_window_days=None`) reads the full 1970–2099 range. [Source: src/api/ui/explorer.py:390-411]
- **Venue gate** — the chart-panel and REST routes 404 when `instrument.nautilus_id` is falsy; corrupt/missing Parquet degrades to the empty-state panel (`DataNotFoundError`/`CatalogCorruptionError` swallowed). This is the Epic-3 "no guessed venue" contract at the read edge. [Source: src/api/ui/explorer.py:448-482]

### Scope boundaries (do NOT do here)
- **No new enum member, no new bar-type literal, no migration, no new DB column.** 30min plumbing landed in Story 2.1; this story consumes it. Do not touch `catalog_instruments` schema.
- **No stats/metadata-panel work** — `TickerStatsResponse` and the stats panel do **not** yet carry `bar_count_30min`; that per-timeframe-statistics gap is **Story 4.5**, and the N/A-aware metadata panel is **Story 4.4**. Do not add 30min to the stats models here (out of scope; would collide with parallel 4.4/4.5 work).
- **No ticker-list / search work** — Stories 4.1/4.2. `TickerRow` lacking `bar_count_30min` is a 4.1/4.5 concern, not a chart concern (the chart reads the `CatalogInstrument`, which has it).
- **No external-reference verification** — clicking through TradingView with `agent-browser` for OHLC parity is **Story 4.6**. Here, a local smoke is optional; component tests are the gate.
- **No renderer changes** — Lightweight-Charts `createChartWithDefaults`, colors, resize observer, 60fps behavior are Phase 1 and unchanged; NFR5 is inherited, not re-implemented.
- **No changes to the run-backtest bridge** — `TIMEFRAME_EXPLORER_TO_RUN_FORM` already maps `"30m" → "30-MINUTE"` (Story 2.1). Leave it.

### Design decision — verification story with a doc fix, tests as the deliverable
The behavior is implemented; the **risk** is silent regression (a future edit dropping 30m from the toolbar loop, or conflating `30-MINUTE-LAST` with `5-MINUTE-LAST`). The chart-panel windowing suite already pins D/1H/5m/1m — this story adds the symmetric 30m pin plus an ETF-path parametrized test, so any regression fails CI. The AC5 doc fix is the only production edit and is purely a description string (OpenAPI-visible), carrying no behavior and no new literal. TDD still applies: write the OpenAPI-description assertion Red first, then edit the strings Green. [Source: CLAUDE.md Decision Heuristics — "Component for Nautilus with test doubles"; development-principles.md TDD]

### Previous-story intelligence
- **Story 2.1 (done)** — established `ExplorerTimeframe` as the central enum and wired `bar_count_30min`, `VALID_TIMEFRAMES`, the bridge map, and presentation models "by hand." Its retro note (memory `project_phase2_epic2_story21_30min`) warns that every consumer of the enum must be wired individually — this story verifies the **chart** consumer specifically. [Source: memory: Phase 2 Epic 2 Story 2.1]
- **Epic 3 (done)** — a chartable ETF must have a **resolved venue** (`nautilus_id` set, e.g. `SPY.ARCA`); a `VENUE_UNRESOLVED` ETF has `nautilus_id = NULL` and is correctly excluded — the chart route 404s it (Task 2's unresolved case). No venue is ever guessed to admit a chart. [Source: memory: Epic 3 done + backtestable signal; src/api/ui/explorer.py:448]
- **Explorer catalog-scoping fix (done)** — `get_data_catalog_service` reads the dropdown-selected catalog, not the default `NAUTILUS_PATH`; the chart-panel route inherits this via the `DataCatalog` dependency, so an ETF in `firstrate-etf` charts from the right catalog. [Source: memory: Explorer catalog-scoping bug (FIXED)]

### Testing standards
- **Component tier** for the chart-panel + REST routes (FastAPI `TestClient`, `MagicMock(spec=CatalogInstrument)` + `AsyncMock` metadata service + mock catalog service returning mock Nautilus bars) — the established pattern in `tests/component/api/test_chart_panel_routes.py`. **Unit tier** already covers the enum single-definition guard. No integration/e2e — no real engine, no C extensions, no live data. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill; tests/component/api/test_chart_panel_routes.py]
- **TDD non-negotiable** — Red first for the 30m windowing/mapping tests, the ETF parametrized test, and the OpenAPI-description assertion. [Source: development-principles.md]

### Project Structure Notes
- **Modified (production, doc-only):** `src/api/rest/explorer.py` (`tf` `Query` descriptions + docstrings on `get_chart_data`/`get_ticker_stats`), `src/api/ui/explorer.py` (`chart_panel_fragment` docstring `tf` line).
- **Modified (tests):** `tests/component/api/test_chart_panel_routes.py` (30m windowing + REST mapping + toolbar-30m present/disabled + ETF parametrized all-five + ETF unresolved-404 + OpenAPI `tf`-doc-lists-30m).
- **New:** none required (no new module, template, or route).
- **Untouched:** `src/api/models/explorer.py` (enum already correct), `templates/explorer/chart_panel.html` (toolbar already loops all five), the Lightweight-Charts JS.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.3 (lines 708-722)] — story statement + AC1 (all five incl. 30m from central enum) + AC2 (NFR1 < 2s windowed, NFR5 60fps)
- [Source: _bmad-output/planning-artifacts/epics.md:670-674] — Epic 4 intro: extends Phase 1 explorer, read-only stance preserved, 30min included
- [Source: src/api/models/explorer.py:18-67] — `ExplorerTimeframe` (THIRTY_MIN at :28; `from_label`; `initial_window_days`)
- [Source: src/api/ui/explorer.py:390-545] — `_compute_chart_window` + `chart_panel_fragment` (toolbar over `ALL_TIMEFRAMES`, windowed query, 404 gate, empty-state degrade)
- [Source: src/api/rest/explorer.py:38-140] — `get_ticker_stats` + `get_chart_data` (`tf` params to enumerate 30m; `bar_type_spec` mapping)
- [Source: templates/explorer/chart_panel.html] — toolbar loop, disabled-not-hidden, aria/focus/arrow-key affordances
- [Source: tests/component/api/test_chart_panel_routes.py:441-511] — `TestChartPanelWindowing` (the D/1H/5m/1m precedent to extend with 30m) + fixtures (`_make_instrument` with `bar_count_30min`)
- [Source: tests/unit/api/test_timeframe_single_definition.py] — the `30-MINUTE-LAST` single-definition guard (do not re-inline the literal)
- [Source: docs/agent/web-ui.md] — HTMX fragment / DI chain / chart conventions

### Open questions (non-blocking — proceed with the documented default)
1. **Live 60fps/NFR5 evidence.** Default: NFR5 is inherited from the unchanged Phase 1 Lightweight-Charts renderer and is not separately re-measured here; the component tests assert the windowed data contract that keeps payloads small. Flag if the team wants an explicit perf capture (that naturally belongs with Story 4.6's evidence record).
2. **30m window size (90 days).** Default: reuse the enum's `initial_window_days = 90` set in Story 2.1 — between 1H (180d) and 5m (30d), which is sensible for ~13 bars/day. Flag only if product wants a different 30m default window.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/component/api/test_chart_panel_routes.py -q` → Red state: 53 passed, 2 failed (`TestChartEndpointDocs` — `'30m' in 'Timeframe label (D, 1H, 5m, 1m)'` as expected; the 30m windowing/mapping/toolbar/ETF behavior already passed, proving the enum wiring from Story 2.1).
- After the doc-string edit: `uv run pytest tests/component/api/test_chart_panel_routes.py::TestChartEndpointDocs ...::TestEtfChartAllTimeframes tests/unit/api/test_timeframe_single_definition.py` → 10 passed.
- `uv run ruff check .` → All checks passed. `uv run mypy .` → Success: no issues found in 352 source files.
- `uv run pytest tests/component/api/test_chart_panel_routes.py tests/unit/api/test_timeframe_single_definition.py tests/unit/api/test_explorer_models.py tests/unit/api/test_chart_data_models.py tests/unit/api/test_explorer_bridge_url.py tests/component/api/test_explorer_routes.py` → 176 passed.

### Completion Notes List

- **AC1** — `tf=30m` resolves through the central `ExplorerTimeframe` enum to `bar_type_spec="30-MINUTE-LAST"`; verified for both the chart-panel fragment (`test_30min_loads_windowed_range`) and the REST endpoint (`test_30min_timeframe_mapping`). No bar-type literal was re-inlined (`test_timeframe_single_definition` still green).
- **AC2** — all five timeframe buttons (incl. 30m) are present in the toolbar, sourced from `ALL_TIMEFRAMES = list(ExplorerTimeframe)`; 30m is enabled/selectable when `bar_count_30min > 0` (`test_timeframe_toolbar_includes_30m`, `test_30m_button_active_when_selected`) and present-but-disabled when it is 0 (`test_30m_button_disabled_when_no_30min_bars`).
- **AC3** — the 30min chart reads a **windowed** Parquet range (`date_range_end − 90 days`, the enum's `initial_window_days` from Story 2.1), keeping payloads small (NFR1). NFR5 (60fps) is inherited from the unchanged Phase 1 Lightweight-Charts renderer.
- **AC4** — a venue-qualified ETF (`SPY.ARCA`) charts at every resolution against its DB-authoritative `nautilus_id` (`TestEtfChartAllTimeframes`); a venue-unresolved ETF (`nautilus_id` NULL) 404s — never charted under a guessed venue (Epic 3 / ADR-6).
- **AC5** — the public OpenAPI `tf` descriptions for the chart and stats endpoints (and the corresponding docstrings) now enumerate all five labels `(D, 1H, 30m, 5m, 1m)`; verified via `/openapi.json` assertions.
- **Scope honesty** — this is a thin, verification-heavy story: the 30min chart runtime already existed (Story 2.1 wired the central enum; the Phase 1 explorer already loops all timeframes and windows reads). The only production change is a doc-only description edit (AC5); the bulk of the deliverable is the missing 30min + ETF-path regression tests that lock the behavior in. No new module, template, route, DB column, or migration. Stats-model `bar_count_30min` (Story 4.5), the N/A metadata panel (Story 4.4), ticker-list (4.1/4.2), and external-reference verification (4.6) were intentionally left out of scope to avoid colliding with parallel Epic-4 work.

### File List

- `src/api/rest/explorer.py` (modified — `tf` `Query` descriptions + docstrings on `get_chart_data`/`get_ticker_stats` now list 30m; doc-only)
- `src/api/ui/explorer.py` (modified — `chart_panel_fragment` docstring `tf` line now lists 30m; doc-only)
- `tests/component/api/test_chart_panel_routes.py` (modified — added 30m REST mapping + 30m windowing + 30m toolbar present/active/disabled tests; new `TestEtfChartAllTimeframes` (parametrized all-five + unresolved-404) and `TestChartEndpointDocs` (OpenAPI 30m doc) classes; review: added `_toolbar_button_tag` helper and tightened the three toolbar assertions to the specific 30m button)
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified — recorded the deferred stats-panel 30-Min card gap for Story 4.5)

### Change Log

| Date | Change |
|---|---|
| 2026-07-16 | Story 4.3 drafted (SM). Status → ready-for-dev. |
| 2026-07-16 | Implemented (TDD): 30m + ETF chart-panel/REST regression tests locking in the Story-2.1 enum behavior; AC5 doc-only edit enumerating 30m in the public `tf` descriptions. Ruff/mypy clean; 176 explorer/chart tests green. Status → review. |
| 2026-07-16 | Code review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). Auditor: full pass on all 5 ACs. Fixed 3 test-quality patches (2 Med vacuous assertions + 1 Low over-broad substring) via a `_toolbar_button_tag` helper pinning assertions to the 30m button; deferred the pre-existing stats-panel 30-Min-card gap to Story 4.5. Gates green (ruff/mypy clean, 55 chart-panel tests pass). Status → done. |
