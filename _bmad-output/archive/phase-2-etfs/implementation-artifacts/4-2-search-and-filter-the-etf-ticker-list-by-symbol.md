# Story 4.2: Search & Filter the ETF Ticker List by Symbol

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to search/filter the ETF ticker list by symbol **without losing the active asset-class (ETF) filter**,
so that I can quickly find a specific ETF among thousands and stay within the ETF universe while I do it.

## Acceptance Criteria

1. **Given** the ETF ticker list (the ETF asset-class pill is active), **When** the operator types a symbol fragment into the search box, **Then** the list narrows to matching **ETF** tickers via the existing HTMX filter-state pattern — the search request carries the active `asset_class` alongside `search`, `sort_by`, and `catalog`, so searching *within* the ETF filter stays within ETFs (it does not silently reset to All asset classes). [Source: epics.md Story 4.2 AC1; templates/explorer/explorer.html:72-78 `#search-input` `hx-include`; src/db/repositories/catalog_instrument_repository.py:234-239 (`asset_class` AND `search` are combined server-side)]
2. **Given** a keystroke in the filter, **When** results update, **Then** the search responds within NFR3 (< 300ms) because the list path is the same DB-only, indexed, no-Parquet read as the browse list (Story 4.1 / NFR2): a single catalog-scoped `SELECT` with an ILIKE prefix predicate and `LIMIT/OFFSET`, never a `ParquetDataCatalog` open. The 300ms client-side `keyup` debounce is a UX affordance and is orthogonal to the server response budget. [Source: epics.md Story 4.2 AC2 / NFR3; src/db/repositories/catalog_instrument_repository.py:212-261; templates/explorer/explorer.html:73 `hx-trigger="keyup changed delay:300ms"`]
3. **Given** the search input renders on the full page and after any HTMX swap, **When** the operator types, **Then** the request goes to `GET /explorer/ticker-list` and the fragment re-renders `#ticker-list` with the correct asset-class pill still highlighted (state round-trips: `state.asset_class` is preserved end-to-end and the "ETF" pill remains `aria-pressed="true"`). [Source: templates/explorer/ticker_list.html:9-10 (hidden `asset_class` input), :26-38 (pill highlight from `state.asset_class`)]
4. **Given** a zero-match search (with or without an active asset-class filter), **When** the fragment renders, **Then** the existing empty-state message is preserved (`No tickers found for '<query>'`) — this story does not change the empty-state copy or the debounce. [Source: templates/explorer/ticker_list.html:225-229]

## Tasks / Subtasks

- [x] **Task 1: Preserve the active asset-class filter when searching** (AC: #1, #3) — *write the failing component test FIRST (TDD Red→Green)*
  - [x] In `templates/explorer/explorer.html`, the `#search-input` `hx-include` currently reads `"#catalog-select, [name='sort_by']"` (line ~75) — it is the **only** ticker-list control that omits `[name='asset_class']`. Every sibling control includes it: the asset-class pills (`ticker_list.html:20,32`), the sort buttons (`ticker_list.html:84,96,107`), and pagination (`ticker_list.html:202,213`). Add `[name='asset_class']` to the search input's `hx-include` → `"#catalog-select, [name='sort_by'], [name='asset_class']"` so a keystroke carries the active filter and the search narrows *within* the ETF list instead of resetting to All. [Source: templates/explorer/explorer.html:72-78]
  - [x] Do **not** change `hx-trigger` (keep `keyup changed delay:300ms`), `hx-target`, `hx-vals` (`{"page": "1"}` — a new search resets to page 1, intended), `hx-push-url`, or the input's a11y attributes. This is a one-attribute filter-state fix, not a debounce or UX change.
  - [x] Invoke the `web-ui-development` skill conventions before editing the template (HTMX/Jinja2 fragment; no new routes, no new HTMX attributes — only widening an existing `hx-include`). [Source: CLAUDE.md "UI changes: Always invoke the web-ui-development skill"]

- [x] **Task 2: Tests** (AC: #1, #2, #3, #4) — TDD, the hx-include assertion authored before the template edit
  - [x] **Component (`tests/component/api/test_explorer_routes.py`, `@pytest.mark.component`):**
    - [x] `test_search_input_preserves_asset_class_filter` (the Red test): render `GET /explorer`, slice out the `#search-input` element, assert its `hx-include` contains **both** `[name='sort_by']` **and** `[name='asset_class']`. This fails before the fix (asset_class absent) and passes after. [Source: templates/explorer/explorer.html:72-78]
    - [x] `test_search_within_etf_filter_forwarded` (AC1 round-trip through the route): `GET /explorer/ticker-list?catalog=us_stocks&asset_class=ETF&search=SP` and assert the stubbed `list_instruments_with_search` was called with **both** `asset_class="ETF"` and `search="SP"` — proving the combined query narrows within ETFs server-side. [Source: src/api/ui/explorer.py:242-249]
    - [x] `test_etf_pill_stays_active_during_search` (AC3): `GET /explorer/ticker-list?catalog=us_stocks&asset_class=ETF&search=SP` and assert the ETF pill renders `aria-pressed="true"` (state round-trips so the highlight is preserved). [Source: templates/explorer/ticker_list.html:26-38]
  - [x] Keep the existing `test_search_param_forwarded`, `test_empty_search_preserves_query_in_message`, `test_search_input_rendered`, and `test_search_input_has_aria_label` green (no regressions to search plumbing, empty-state copy, or a11y). No unit-model change is needed (no new fields).

- [x] **Task 3: Verify** (AC: all)
  - [x] `uv run ruff check .` clean (no new imports — template-only source edit + test edit; F401/F821 risk is nil). → All checks passed.
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: 6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). No typed source signatures change. → no new errors.
  - [x] `make test-unit` green; `make test-component` green for `tests/component/api/test_explorer_routes.py` (new + existing search/filter tests). No regressions in the explorer suite.
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`. Template edit is a single-attribute widen.
  - [x] Optional browser smoke (`agent-browser` / `e2e-ui-testing`) deferred to Story 4.6 (Explorer Accuracy Verification) — not required here.

## Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). **Clean on the diff.** The fix is a single-attribute widen of the `#search-input` `hx-include` to add `[name='asset_class']`, bringing the search control into parity with every other ticker-list control (pills, sort headers, pagination) that already carry the asset-class filter. All 4 ACs PASS; scope boundaries respected (no debounce change, no empty-state copy change, no catalog-select change, no server-route change). **0 decision-needed · 0 patch · 1 defer · 3 dismissed.**

- [x] [Review][Defer] **Catalog dropdown drops the active asset-class filter on switch** [templates/explorer/explorer.html:38] — deferred, pre-existing. The catalog `<select>` uses `hx-include="#search-input, [name='asset_class']:checked"`; the only `name="asset_class"` element is the `type="hidden"` input (`ticker_list.html:10`), which can never be `:checked` (pills are `<button>`s), so switching catalog forwards no `asset_class` and the list resets to All (the "All" pill becomes `aria-pressed="true"`). Asymmetric with the dropdown's `search` preservation, but **not caused by this diff** and **explicitly scoped out** by this story (search-preservation only; catalog-switch reset to All is a defensible fresh-catalog view — a new catalog may lack ETFs). Recorded in `deferred-work.md`.

**Dismissed as noise:** (a) Blind Hunter's "fragile tag-boundary parse" in `test_search_input_preserves_asset_class_filter` — Edge Case Hunter confirmed `.html` templates are Jinja2-autoescaped, so any `>` in an attribute value renders as `&gt;` and the `hx-include` literal is unconditional; the slice is robust. (b) Edge Case Hunter's "selector fragile to a future second `name='asset_class']` element" — informational only; exactly one matching element exists today and nothing on the roadmap adds another. (c) Acceptance Auditor's note that the pill test asserts a bare `aria-pressed="true"` — unambiguous here (the "All" pill renders `false` under `asset_class=ETF`, sort headers use `aria-sort`), so exactly one true pill exists; the companion `"ETF (1)"` assertion ties it to the ETF context.

- Confirmed the server route already ANDs `asset_class` + `search` (`list_by_catalog_with_search`), so the ONLY gap was the client-side `hx-include` dropping the filter — the minimal, correct fix is the template attribute.

## Dev Notes

### The core idea (why this story exists)
Epic 4 extends the **existing** Phase-1 explorer (Jinja2 + HTMX + `NavigationState`) to ETFs, read-only stance preserved. The search machinery is already fully built and tested from Phase 1: a debounced `#search-input`, the `list_by_catalog_with_search` repository method with an ILIKE **prefix** predicate, the empty-state, and combined `asset_class` + `search` filtering server-side. The Story-4.1 dev notes explicitly deferred "typing-to-filter by symbol and the < 300ms NFR3" to **this** story. [Source: 4-1 story Dev Notes "Scope boundaries"; epics.md Story 4.2]

**The one real gap** is a filter-state leak in the HTMX wiring: the `#search-input` is the only ticker-list control whose `hx-include` omits `[name='asset_class']`. So when an operator selects the **ETF** pill and then types a symbol fragment, the search request drops the asset-class filter and the list silently resets to **All** asset classes — the exact opposite of AC1 ("Given the ETF ticker list … narrows to matching tickers via the existing HTMX filter-state pattern"). This story closes that leak so searching *within* the ETF universe stays within ETFs.

### Why the gap is exactly the search input's `hx-include` (traceable)
- **Pills** include the filter chain: `hx-include="#catalog-select, #search-input, [name='sort_by']"` (`ticker_list.html:20,32`) and set `asset_class` via `hx-vals`.
- **Sort headers** include it: `hx-include="#catalog-select, #search-input, [name='asset_class']"` (`ticker_list.html:84,96,107`).
- **Pagination** includes it: `hx-include="#catalog-select, #search-input, [name='asset_class'], [name='sort_by']"` (`ticker_list.html:202,213`).
- **Search input** — the outlier — `hx-include="#catalog-select, [name='sort_by']"` (`explorer.html:75`): **no `[name='asset_class']`**. The hidden `<input name="asset_class" value="{{ state.asset_class }}">` (`ticker_list.html:10`) is present in the DOM and correctly picked up by the *other* controls; the search input just never asked for it.

Server-side, `list_by_catalog_with_search` already ANDs both predicates (`catalog_instrument_repository.py:234-239`), so no repository/service/route change is required — the fix is purely the missing include on the client.

### The seam (control flow — no new I/O)
```
#search-input (keyup, debounced 300ms)
   hx-get /explorer/ticker-list
   hx-include: #catalog-select, [name='sort_by'], [name='asset_class']   ← ADD asset_class
        │  (catalog, sort_by, asset_class, search, page=1)
        ▼
   ticker_list_fragment → _get_ticker_data → MetadataService.list_instruments_with_search
        │  WHERE catalog_name = ? AND asset_class = ? AND ticker ILIKE 'SP%'   (both predicates)
        ▼
   #ticker-list re-render (ETF pill stays aria-pressed=true; results = ETFs matching 'SP')
```
Read is DB-only (indexed `SELECT ... LIMIT/OFFSET`), **no `ParquetDataCatalog`** — that is what keeps NFR3 (< 300ms) true, exactly as Story 4.1's browse list keeps NFR2. [Source: src/api/ui/explorer.py:235-271,367-410]

### Performance (AC2 / NFR3) — already satisfied, keep it that way
The list is a single catalog-scoped `SELECT` (count + `LIMIT 25 OFFSET n`) with an ILIKE **prefix** predicate on `ticker`, filtered first by the indexed `catalog_name`/`asset_class` (`ix_catalog_instruments_catalog_asset`). For a single catalog's ~5,000 ETFs this is comfortably < 300ms and never opens Parquet. Adding one more `hx-include` selector changes nothing about the query — it only forwards a WHERE value that already existed on the page. Do **not** widen the ILIKE to a substring match (`%frag%`) — that would defeat prefix indexability and is not what "search by symbol" means; prefix is the established, tested behavior (`test_list_by_catalog_with_search_prefix_only`). [Source: src/db/repositories/catalog_instrument_repository.py:212-261; src/db/models/catalog_instrument.py:113-125]

### Scope boundaries (do NOT do here)
- **No debounce / trigger change** — keep `keyup changed delay:300ms`. NFR3 is about server response, not the client debounce; don't "optimize" the debounce.
- **No repository/service/route change** — `asset_class` + `search` are already ANDed server-side and both params are already accepted by `ticker_list_fragment` / the REST list. This is a template-only client-side fix.
- **No search-semantics change** — keep ILIKE **prefix** (`ticker ILIKE 'frag%'`), not substring. Symbol search = prefix; changing it is out of scope and breaks `test_list_by_catalog_with_search_prefix_only`.
- **No empty-state copy change** — `No tickers found for '<query>'` stays as-is (Story 4.1 / Phase 1 owns it).
- **No catalog-select change** — the catalog dropdown's `[name='asset_class']:checked` selector never matches the hidden input, so switching catalog resets the asset-class filter to All (a defensible fresh-catalog behavior). This story is about **search** preserving the filter, not catalog-switch; leave the dropdown alone.
- **No new sortable column / no chart / stats / metadata work** — those are 4.1 (sort), 4.3 (chart), 4.4 (metadata N/A), 4.5 (stats). Untouched.

### Presentation-model + duplicated-builder convention
No presentation-model change here — `TickerRow`/`TickerListResponse` are untouched (this is a wiring fix, not a data-shape change). The UI and REST list builders stay identical. The REST `GET /api/explorer/tickers` already accepts and forwards `asset_class` + `search` (`test_search_param_forwarded`, `test_asset_class_param_forwarded`), so REST parity is preserved with no edit. [Source: src/api/rest/explorer.py]

### Testing standards (test pyramid)
- **Component** for the route + rendered fragment/page with test doubles (`tests/component/api/test_explorer_routes.py`) — `MagicMock(spec=CatalogInstrument)` via `_make_instrument`, `AsyncMock` MetadataService, FastAPI `app.dependency_overrides`, `TestClient`. This is the established template; extend it. The rendered-`hx-include` assertion is the guard that actually pins the fix; the combined-forwarding assertion documents AC1 through the route.
- **No unit-model test** — no new model field. **No integration/`--forked`** — no `BacktestEngine`, no Nautilus C extensions, no real Parquet on the list path. [Source: CLAUDE.md Decision Heuristics "Test tier"; tests/component/api/test_explorer_routes.py]

### Project Structure Notes
- Files touched: `templates/explorer/explorer.html` (widen the search-input `hx-include`). Tests: `tests/component/api/test_explorer_routes.py` (3 new component tests). Both existing files — prefer editing (CLAUDE.md "New file vs edit").
- No conflicts with the unified structure: templates under `templates/explorer/`, component tests under `tests/component/api/`.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 4.2: Search & Filter the ETF Ticker List by Symbol] — user story + AC1 (narrow via HTMX filter-state) + AC2 (< 300ms / NFR3).
- [Source: _bmad-output/planning-artifacts/epics.md#Epic 4: Data Explorer — ETF Support] — "Extends the Phase 1 explorer … read-only stance preserved."
- [Source: templates/explorer/explorer.html:58-83] — the search + filter bar; `#search-input` (the `hx-include` to widen), `#loading-bar`.
- [Source: templates/explorer/ticker_list.html:9-10,20,32,84,96,107,202,213] — hidden `asset_class` input + the sibling controls that already include `[name='asset_class']` (parity target).
- [Source: src/api/ui/explorer.py:235-271,367-410] — `_get_ticker_data`, `ticker_list_fragment` (route already accepts `search` + `asset_class`, both forwarded to the service).
- [Source: src/db/repositories/catalog_instrument_repository.py:212-261] — `list_by_catalog_with_search` (combines `asset_class` AND `search`; ILIKE prefix; DB-only; NFR3 evidence; `_SORTABLE_COLUMNS`).
- [Source: src/db/models/catalog_instrument.py:113-125] — `ix_catalog_instruments_catalog_asset` / `ix_catalog_instruments_ticker` (indexed catalog-scoped read).
- [Source: tests/component/api/test_explorer_routes.py:104-107,270-274,281-283] — existing search plumbing/empty-state/a11y tests to keep green.
- [Source: 4-1-browse-imported-etf-tickers.md Dev Notes "Scope boundaries"] — 4.1 explicitly deferred search/filter + NFR3 to this story.
- [Source: MEMORY project_phase2_epic4_story41_browse] — Epic 4 explorer wiring context (30min surfaced in browse; per-hand wiring theme).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/component/api/test_explorer_routes.py tests/unit/api/test_explorer_models.py -q` → all passed (new + existing).
- `uv run ruff check .` → All checks passed.
- `uv run mypy .` → no new errors vs baseline.

### Completion Notes List

- Root cause of the gap: the `#search-input` was the sole ticker-list control whose `hx-include` omitted `[name='asset_class']`, so typing a symbol while the ETF pill was active dropped the asset-class filter and reset the list to All. Server-side both predicates were already ANDed, so the fix was a one-attribute client-side widen.
- Change is template-only wiring: `hx-include="#catalog-select, [name='sort_by']"` → `"#catalog-select, [name='sort_by'], [name='asset_class']"`. No debounce, empty-state, route, repository, or presentation-model change. Prefix ILIKE search semantics preserved (not substring). NFR3 preserved — same DB-only, indexed, no-Parquet list read.
- Tests: rendered-`hx-include` guard (asserts both `sort_by` and `asset_class` present on `#search-input`), combined `asset_class=ETF` + `search=SP` forwarding through the route, and ETF-pill-stays-active (`aria-pressed="true"`) during search.
- Out of scope, untouched (per scope boundaries): debounce/trigger, empty-state copy, catalog-select `:checked` reset behavior, search semantics (prefix), sort columns, chart (4.3), metadata (4.4), stats (4.5).

### File List

- `templates/explorer/explorer.html` — widen `#search-input` `hx-include` to include `[name='asset_class']` (preserve active filter when searching).
- `tests/component/api/test_explorer_routes.py` — 3 new component tests (hx-include guard, combined ETF+search forwarding, ETF pill stays active).
</content>
</invoke>
