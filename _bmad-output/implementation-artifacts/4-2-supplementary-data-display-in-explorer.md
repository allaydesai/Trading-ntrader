# Story 4.2: Supplementary Data Display in Explorer

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want to see company profile, dividend history, and stock split history in the explorer when viewing a ticker,
so that I have fundamental context alongside price data for research and verification.

## Acceptance Criteria

1. **Three collapsible sections, collapsed by default.** Given a ticker is selected in the explorer, when the supplementary panel loads below the stats panel, then it renders three native `<details>` sections in order — **Company Profile**, **Dividend History**, **Stock Split History** — and all three are collapsed by default (no `open` attribute).

2. **Company Profile content.** Given the Company Profile section is expanded, when the content renders, then it displays: `ticker`, `name`, `exchange`, `sector`, `industry`, `ipo_date`, **`country`**, and **`state`**, sourced from the `catalog_instruments` table. Missing/null fields render as `—` (em-dash), never blank or `None`. (`country`/`state` require the schema + import changes in Tasks 1–3 below.)

3. **Dividend History content.** Given the Dividend History section is expanded, when the content renders, then it displays a date-ordered list of dividend records (`ex_date` + `amount`) for the selected ticker; dates use the explorer's consistent `%Y-%m-%d` format and amounts use `font-mono`. Order is newest-first (the repository's `ex_date DESC` default).

4. **Stock Split History content.** Given the Stock Split History section is expanded, when the content renders, then it displays a date-ordered list of split records (`effective_date` + a derived ratio display string) for the selected ticker, newest-first. The stored `Decimal` ratio is converted to a display string at presentation time (e.g. `4` → `"4:1"`, `7` → `"7:1"`, `0.5` → `"1:2"`); the raw `Decimal` is also available via `font-mono` for unambiguous reverse splits.

5. **Empty-state messages.** Given a ticker with no dividend and/or no split data, when the supplementary panel renders, then the Dividend History section shows **"No dividend history available"** and/or the Stock Split History section shows **"No split history available"**, **and** the Company Profile section still displays if company data exists.

6. **HTMX fragment endpoint + OOB on ticker selection.** Given a ticker is selected, when the explorer fetches it, then the supplementary panel is delivered via `GET /explorer/supplementary?catalog=...&ticker=...` (HTML fragment, no `<html>`/`<body>`, no `id="supplementary-panel"` wrapper) **and** it is refreshed as part of the existing `hx-swap-oob` response from `GET /explorer/chart-panel` when a ticker is clicked (same mechanism the stats panel already uses).

7. **Availability flags on the REST stats endpoint.** Given `GET /api/explorer/ticker/{ticker}/stats`, when it responds, then `TickerStatsResponse` includes boolean availability flags — `has_dividends`, `has_splits`, `has_company_profile` — reflecting whether supplementary data exists for that `(catalog, ticker)`.

8. **Placeholder removed; zero regression.** Given Story 2-3's Epic-4 placeholder note in `stats_panel.html` (`"Dividends and stock splits available in Epic 4"`), when this story lands, then that note is removed, and the existing stats panel, chart panel, and ticker list continue to behave exactly as before (deep-link `?ticker=` load and ticker-click both render the new panel).

## Tasks / Subtasks

- [x] **Task 1 — Add `country` + `state` columns to `CatalogInstrument` (AC: 2)**
  - [x] In `src/db/models/catalog_instrument.py`: add `country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)` and `state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)`. Place them next to `industry`/`ipo_date`; update the class docstring `Attributes:` list to match.
  - [x] Unit model test (extend `tests/unit/models/test_catalog_instrument_model.py`): assert both columns exist, are `String`, and are nullable.

- [x] **Task 2 — Alembic migration for the two new columns (AC: 2)**
  - [x] `uv run alembic revision --autogenerate -m "add country and state to catalog_instruments"`. → `dbec2c1f25a6`
  - [x] **Verify `down_revision = "b3accfdb64bd"`** (current head — Story 4-1's `catalog_dividends`/`catalog_stock_splits` migration). Confirm with `uv run alembic heads` first; do **not** trust CLAUDE.md's "4 migrations" (stale — there are 8). Chain tail: `… → 79f6e07bee8b → b3accfdb64bd`. → confirmed
  - [x] Review generated `upgrade()`/`downgrade()` — both `add_column` / `drop_column` present, nullable.
  - [x] `uv run alembic upgrade head` → `downgrade -1` → `upgrade head` again; confirm reversible. → reversible, new head `dbec2c1f25a6`

- [x] **Task 3 — Persist `country`/`state` in `InstrumentMapper` + backfill (AC: 2)**
  - [x] In `src/services/firstrate/instrument_mapper.py`: add `_COL_COUNTRY = 2`, `_COL_STATE = 3` (the CSV header is `Ticker,Company Name,Country,State,Exchange,Sector,Industry,Ipo Date` — verified on disk). In `load_company_profiles`, set `country=row[_COL_COUNTRY].strip() or None`, `state=row[_COL_STATE].strip() or None` on the `CatalogInstrument(...)` construction.
  - [x] Confirm the upsert path (`SyncCatalogInstrumentRepository.upsert`) updates these columns on an existing row (re-import overwrites, not just inserts) — added `country`/`state` to BOTH async + sync upsert update sets.
  - [x] Extend the instrument-mapper tests to assert `country`/`state` are parsed and persisted; assert blank → `None`. Also added component test `test_upsert_updates_country_state_on_existing` + fixed two hand-rolled SQLite DDLs (added country/state cols).
  - [x] **Backfill (manual, AC-2):** import skips profiles when already loaded (`import_service.py:114` `is_loaded`), so backfilled via direct `load_company_profiles` against the sync session. Verified: AAPL=US/CA, AMZN=US/WA, MSFT=US/WA, NVDA=US/CA, TSLA=US/TX.

- [x] **Task 4 — Async availability methods on the supplementary repos (AC: 7)**
  - [x] Add `async def has_for_ticker(self, catalog_name: str, ticker: str) -> bool` to **both** `CatalogDividendRepository` and `CatalogStockSplitRepository`. Implemented as `select(literal(1)) … LIMIT 1` → `result.first() is not None` (no full-row load).
  - [x] Component tests (extend both repo tests): True when rows exist, False when none, catalog-scoped. 6 new tests, all green.

- [x] **Task 5 — Supplementary presentation service + ratio formatter (AC: 3, 4, 5)**
  - [x] Create `src/api/supplementary_service.py` mirroring `src/api/stats_service.py`. `async def _build_supplementary_context(...)` resolves instrument, lists dividends + splits, returns `{instrument, dividends, splits, has_company_profile, format_split_ratio, format_date}`. No 404 — additive.
  - [x] `format_split_ratio(ratio: Decimal) -> str`: integral ≥1 → `"{n}:1"`; `0<ratio<1` → `"1:{round(1/ratio)}"`; non-integral / unexpected → plain `Decimal` string. Pure function.
  - [x] Unit tests `tests/unit/api/test_supplementary_service.py`: formatter (4→"4:1", 7→"7:1", 0.5→"1:2", 1→"1:1", 1.5 fallback, 0.333→"1:3") + builder doubles (present / all-empty / missing-profile). 9 tests green.

- [x] **Task 6 — Async DI for the two supplementary repos (AC: 6, 7)**
  - [x] In `src/api/dependencies.py`: added `get_dividend_repository` + `get_stock_split_repository` (mirror `get_backtest_repository`) + `DividendRepo`/`SplitRepo` aliases + async repo imports.

- [x] **Task 7 — Supplementary fragment route + templates (AC: 1, 2, 3, 4, 5, 6)**
  - [x] In `src/api/ui/explorer.py`: added `GET /explorer/supplementary` → `supplementary_panel_fragment(...)` calling `_build_supplementary_context(...)`; renders `explorer/supplementary_panel.html`. No `tf` param.
  - [x] Created `templates/explorer/supplementary_panel.html` — content only (no id wrapper). Three collapsed `<details>`: Company Profile (8-field dl, `—` for null incl. country/state), Dividend History (`ex_date` + font-mono `amount`, empty-state), Stock Split History (`effective_date` + `format_split_ratio` + font-mono raw `ratio`, empty-state).
  - [x] Created `templates/explorer/supplementary_panel_skeleton.html` — animate-pulse placeholders.

- [x] **Task 8 — Wire the container + OOB swap into the page (AC: 1, 6, 8)**
  - [x] `templates/explorer/explorer.html`: added `#supplementary-panel` container directly below `#stats-panel` — deep-link self-load via `hx-get="/explorer/supplementary"` (catalog+ticker, no tf) + skeleton include.
  - [x] `templates/explorer/chart_panel.html`: added `#supplementary-panel` OOB block after the stats OOB; injected `DividendRepo`+`SplitRepo` into `chart_panel_fragment` and merged `**_build_supplementary_context(...)` (same helper, no duplicated queries). Updated chart-panel test `client` fixture to override the two new repo deps — 40/40 still green.
  - [x] `templates/explorer/stats_panel.html`: removed the Epic-4 placeholder `<p>… available in Epic 4</p>` block.

- [x] **Task 9 — Availability flags on `TickerStatsResponse` (AC: 7)**
  - [x] In `src/api/models/explorer.py`: added `has_dividends`/`has_splits`/`has_company_profile` (bool, default False).
  - [x] In `src/api/stats_service.py`: added optional `dividend_repo=None`/`split_repo=None`; flags set via `has_for_ticker` when provided, else stay False. (`has_company_profile` = repos-provided AND instrument exists.)
  - [x] In `src/api/rest/explorer.py` `get_ticker_stats`: inject `DividendRepo`+`SplitRepo`, pass to builder. UI `stats_panel_fragment` keeps passing nothing (defaults None). Updated stats test fixture w/ repo overrides; 2 new flag tests + inverted the obsolete Epic-4 placeholder guard.

- [x] **Task 10 — Tests (component + UI) (AC: 1–8)**
  - [x] `tests/component/api/test_supplementary_panel_routes.py` — 13 tests: 200; fragment (no html/body); no id wrapper; three headings; collapsed-by-default; country/state shown; dividend rows; split display strings; div/split empty-states; Company Profile shows when div/split empty; missing-profile renders gracefully (no 404).
  - [x] Extended `tests/component/api/test_chart_panel_routes.py`: `test_oob_supplementary_panel_wrapper_present` asserts the `#supplementary-panel` OOB block in the chart-panel response.
  - [x] Extended `tests/component/api/test_stats_panel_routes.py`: 2 flag tests (`has_dividends`/`has_splits`/`has_company_profile` reflect mocked repos).
  - [x] Browser verification: AAPL in `e2e-test` → 3 `<details>`, all collapsed by default; expanded → Company Profile shows country US / state CA, Stock Split History shows 4:1 / 7:1 / 2:1 / 2:1 (+ raw Decimal mono), Dividend History lists 52 rows newest-first; AMZN shows "No dividend history available" while profile (US/WA) + 20:1 split still render. Screenshots captured via Playwright MCP (the `agent-browser` CLI screenshot subcommand could not write files in this sandbox; Playwright MCP worked).

## Dev Notes

### What Story 4-1 already built (reuse, do NOT rebuild)

Story 4-1 (status: done) created the **parse + persist** half. The tables, models, and **async read repositories already exist** — this story is the **display** half plus the country/state schema gap.

- **Tables/models:** `catalog_dividends` (`CatalogDividend`: `id, catalog_name, ticker, ex_date:Date, amount:Numeric(20,8)`), `catalog_stock_splits` (`CatalogStockSplit`: `id, catalog_name, ticker, effective_date:Date, ratio:Numeric(20,8)`). [Source: src/db/models/catalog_dividend.py, catalog_stock_split.py]
- **Async repos (USE THESE):** `CatalogDividendRepository(session).list_by_ticker(catalog_name, ticker)` → `list[CatalogDividend]` ordered `ex_date DESC`; `CatalogStockSplitRepository(session).list_by_ticker(catalog_name, ticker)` → `effective_date DESC`. [Source: src/db/repositories/catalog_dividend_repository.py:74-95, catalog_stock_split_repository.py:73-94]
- **Company profile:** `MetadataService.get_instrument(catalog_name, ticker) -> Optional[CatalogInstrument]` is the existing async read used by the stats/chart routes. [Source: src/services/firstrate/metadata_service.py:51; used at src/api/rest/explorer.py:89, src/api/stats_service.py:54]

### Discrepancy (resolved): split ratio is a `Decimal`, not a `"2:1"` string

The epic's 4-1 AC said splits store `"2:1"`. **They don't** — Story 4-1 stored a single `Decimal` (new shares per old share: `4`, `7`; reverse splits `< 1` e.g. `0.5`). The `"4:1"` / `"1:2"` **display** string is this story's job — that's `format_split_ratio` in Task 5. Show the raw `Decimal` alongside (font-mono) so reverse splits are unambiguous. [Source: 4-1 story Dev Notes §"Discrepancy: split ratio format"]

### Discrepancy (resolved this story): country/state were never persisted

The epic's Company Profile AC lists `country` and `state`. The source `company_profiles.csv` **has** them (`Ticker,Company Name,Country,State,Exchange,Sector,Industry,Ipo Date` — cols 2 & 3, verified on disk), **but** `CatalogInstrument` has no such columns and `InstrumentMapper` only reads cols 0,1,4,5,6,7 (`_COL_*` constants skip 2,3). **Product decision (Allay, 2026-06-03): include them** — add the columns, migration, mapper persistence, and re-import to backfill (Tasks 1–3). This is why a "display" story carries a DB migration. [Source: src/db/models/catalog_instrument.py:44-92, src/services/firstrate/instrument_mapper.py:23-104, ~/Data/e2e-subset/company_profiles.csv header]

### Explorer HTMX architecture (the panel-injection contract)

- **Two ways the panel loads, both must work** (mirror the **stats panel** exactly):
  1. **Deep-link** (`/explorer?ticker=AAPL`): the `#stats-panel` container in `explorer.html:89-99` self-loads via `hx-trigger="load"` → `GET /explorer/stats-panel`, showing a skeleton first. Add an identical `#supplementary-panel` container below it → `GET /explorer/supplementary`.
  2. **Ticker click:** the row triggers `GET /explorer/chart-panel`, whose response carries an **`hx-swap-oob="innerHTML"`** block for `#stats-panel` (`chart_panel.html:77-79`) that refreshes stats without a separate request. Add a parallel OOB block for `#supplementary-panel`. [Source: templates/explorer/explorer.html:89-99, chart_panel.html:77-79]
- **Fragment contract:** the fragment template renders **content only** — it must NOT re-declare `id="stats-panel"`/`id="supplementary-panel"` (the page container owns the id; OOB `innerHTML` swaps inner content and preserves the container's own `hx-*` attributes). Tests assert the wrapper id is absent. [Source: templates/explorer/stats_panel.html:1-6, the Explore-confirmed `test_fragment_has_no_stats_panel_id_wrapper` test]
- **Shared builder pattern:** stats logic lives in one place (`src/api/stats_service.py:_build_ticker_stats`) and is consumed by the REST route, the UI fragment route, **and** the chart-panel OOB (via `_stats_template_context`). Replicate this: put supplementary logic in `src/api/supplementary_service.py:_build_supplementary_context` and call it from the `/explorer/supplementary` route **and** `chart_panel_fragment`. Do not inline the queries in the route handlers. [Source: src/api/stats_service.py, src/api/ui/explorer.py:119-155]
- **DI:** async repos are provided FastAPI-style — see `get_backtest_repository`/`DbSession` in `src/api/dependencies.py:88-133`. Add `get_dividend_repository`/`get_stock_split_repository` + `DividendRepo`/`SplitRepo` aliases the same way. The async session comes from `get_db()` (`get_session` context manager). [Source: src/api/dependencies.py:24-40,88-133]

### Templates & styling

- Match the existing dark theme: cards `bg-slate-900 rounded-lg p-4 border border-slate-800`, labels `text-xs text-slate-500 uppercase tracking-wider`, values `font-mono` for amounts/ratios/dates. Native `<details>`/`<summary>` for collapsible sections (no JS needed; collapsed = omit `open`). [Source: templates/explorer/stats_panel.html:6-68]
- Skeleton mirrors `stats_panel_skeleton.html` (`animate-pulse`), shown only on deep-link first paint. [Source: explorer.html:98]
- Date format `%Y-%m-%d` everywhere, matching `stats_panel.html:12`.

### Project structure (new / edited files)

```
NEW:
  src/api/supplementary_service.py                       # _build_supplementary_context + format_split_ratio
  templates/explorer/supplementary_panel.html            # 3 <details> fragment (no id wrapper)
  templates/explorer/supplementary_panel_skeleton.html   # animate-pulse placeholder
  alembic/versions/<rev>_add_country_and_state_to_catalog_instruments.py
  tests/unit/api/test_supplementary_service.py
  tests/component/api/test_supplementary_panel_routes.py
EDIT:
  src/db/models/catalog_instrument.py                    # + country, state columns
  src/services/firstrate/instrument_mapper.py            # + _COL_COUNTRY/_COL_STATE, persist
  src/db/repositories/catalog_dividend_repository.py     # + async has_for_ticker
  src/db/repositories/catalog_stock_split_repository.py  # + async has_for_ticker
  src/db/repositories/catalog_instrument_repository.py   # ensure upsert updates country/state
  src/api/dependencies.py                                # + dividend/split repo DI + aliases
  src/api/models/explorer.py                             # + has_dividends/has_splits/has_company_profile
  src/api/stats_service.py                               # optional repos → set flags
  src/api/rest/explorer.py                               # inject repos into get_ticker_stats
  src/api/ui/explorer.py                                 # + /explorer/supplementary route; chart_panel OOB wiring
  templates/explorer/explorer.html                       # + #supplementary-panel container
  templates/explorer/chart_panel.html                    # + supplementary OOB block
  templates/explorer/stats_panel.html                    # − Epic-4 placeholder note
```
Respect size limits: files <500 lines, functions <50, classes <100, line length 100. `src/api/ui/explorer.py` is already large — keep new code minimal and lean on `supplementary_service.py`. [Source: CLAUDE.md Foundational Rules]

### Migration head (verify, don't assume)

Current head is **`b3accfdb64bd`** (Story 4-1's two-table migration). New `down_revision` must be `b3accfdb64bd`. Confirm with `uv run alembic heads`. CLAUDE.md's "4 migrations" is stale (now 8). [Source: 4-1 story Completion Notes "New migration head = b3accfdb64bd"]

### Testing standards

- **Tiers:** unit for `supplementary_service` (pure formatter + context builder with doubles, no Nautilus); component for repo `has_for_ticker` (real test DB) and for the fragment/REST routes (FastAPI `TestClient` + `app.dependency_overrides`); browser/UI via `e2e-ui-testing`/`agent-browser`. **No `--forked`** — no Nautilus C extensions touched. [Source: docs/agent/testing.md, CLAUDE.md test tiers]
- **TDD non-negotiable** — failing test first per task (Red-Green-Refactor). [Source: CLAUDE.md]
- Invoke the **`web-ui-development`** skill before editing templates/routes/HTMX (project rule). [Source: CLAUDE.md "UI changes"]
- Gates before done: `make test-unit`, `make test-component`, `make lint`, `make typecheck`. [Source: CLAUDE.md Commands]

### Out of scope

- No changes to the import CLI flags or the supplementary **write** path (4-1 owns parsing/persisting divs/splits). This story only **reads** them — except the `country`/`state` mapper change, which extends the *existing* company-profile load (no new flag).
- No new chart overlays (dividend/split markers on the price chart) — display is the `<details>` panel only.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.2] — original ACs
- [Source: _bmad-output/implementation-artifacts/4-1-dividend-and-stock-split-data-parsing.md] — tables/models/async repos this story consumes; split-ratio discrepancy
- [Source: src/api/stats_service.py:40-117] — `_build_ticker_stats` shared-builder pattern to mirror
- [Source: src/api/ui/explorer.py:119-155,373+] — `_stats_template_context`, `stats_panel_fragment`, `chart_panel_fragment`
- [Source: src/api/rest/explorer.py:38-52] — `get_ticker_stats` (add flags here)
- [Source: src/api/dependencies.py:24-40,88-133] — async session + repo DI pattern
- [Source: src/api/models/explorer.py:148-166] — `TickerStatsResponse` (add flags)
- [Source: templates/explorer/explorer.html:89-99, chart_panel.html:77-79, stats_panel.html:6-75] — container + OOB + placeholder-to-remove
- [Source: src/db/models/catalog_instrument.py:44-92] — model to extend with country/state
- [Source: src/services/firstrate/instrument_mapper.py:23-113] — `_COL_*`, `load_company_profiles` to extend
- [Source: ~/Data/e2e-subset/company_profiles.csv] — CSV header confirming Country=col2, State=col3
- [Source: _bmad-output/project-context.md] — dual DB (async for web), Decimal-never-float, structured logging

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m]

### Debug Log References

- Migration head pre-impl: `b3accfdb64bd`; post-impl head: `dbec2c1f25a6` (reversible: `upgrade head → downgrade -1 → upgrade head` all clean).
- Backfill: `import_service.py:114` skips company-profile load when `is_loaded()` → backfilled `country`/`state` for `e2e-test` via a direct `InstrumentMapper.load_company_profiles` against `get_sync_session_maker()`. Verified AAPL=US/CA, AMZN=US/WA, MSFT=US/WA, NVDA=US/CA, TSLA=US/TX.
- Regression after wiring `chart_panel_fragment`/`get_ticker_stats` to require the new repos: existing chart-panel + stats-panel component tests hit the real DB (event-loop-closed/TCP errors) until their `client` fixtures were given `get_dividend_repository`/`get_stock_split_repository` overrides.

### Completion Notes List

Implemented the **display** half of Epic 4 — three collapsible `<details>` (Company Profile / Dividend History / Stock Split History) below the stats panel — reusing Story 4-1's async read repos. Plus the planned `country`/`state` schema gap.

- **Schema + backfill (Tasks 1–3):** added nullable `country`/`state` `String(100)` to `CatalogInstrument` + reversible migration `dbec2c1f25a6`; `InstrumentMapper` now parses CSV cols 2/3 (`_COL_COUNTRY`/`_COL_STATE`); added both columns to the async **and** sync `upsert` update-sets so re-import overwrites; backfilled `e2e-test`.
- **Repos (Task 4):** `has_for_ticker` existence probe (`SELECT 1 … LIMIT 1`) on both async supplementary repos.
- **Service (Task 5):** `src/api/supplementary_service.py` with `_build_supplementary_context` (mirrors `stats_service`, additive — no 404) + pure `format_split_ratio` (`4`→"4:1", `0.5`→"1:2", non-integral → plain Decimal).
- **DI + routes (Tasks 6–8):** `get_dividend_repository`/`get_stock_split_repository` + `DividendRepo`/`SplitRepo` aliases; `GET /explorer/supplementary` fragment route; `#supplementary-panel` container in `explorer.html` (deep-link self-load) + OOB block in `chart_panel.html` (chart_panel_fragment merges the **same** helper's context); removed the Epic-4 placeholder note from `stats_panel.html`. Rebuilt Tailwind CSS for the new classes.
- **Flags (Task 9):** `has_dividends`/`has_splits`/`has_company_profile` on `TickerStatsResponse`, populated by `_build_ticker_stats` only when repos are injected (REST endpoint); back-compat UI caller leaves them False.
- **Tests (Task 10):** 49 new/changed test cases — model (3), mapper unit (2), instrument-repo component (1) + two SQLite DDLs patched, supplementary repos (6), service unit (9), supplementary route (13), chart-panel OOB (1), stats flags (2), Epic-4 placeholder guard inverted. Browser-verified live in `e2e-test`: 3 collapsed `<details>`, AAPL 4:1/7:1/2:1 + US/CA, AMZN/TSLA "No dividend history available".
- **Quality gates:** 924 unit + 657 component green; `ruff format`/`ruff check`/`mypy src/core src/services` all clean.
- **UI evidence:** screenshots captured via Playwright MCP — collapsed default state, AAPL Company Profile (US/CA) + splits 4:1/7:1/2:1, and AMZN empty-dividend state. (The `agent-browser` CLI screenshot subcommand failed to write files in this sandbox; Playwright MCP worked.)

### File List

**New:**
- `alembic/versions/dbec2c1f25a6_add_country_and_state_to_catalog_.py`
- `src/api/supplementary_service.py`
- `templates/explorer/supplementary_panel.html`
- `templates/explorer/supplementary_panel_skeleton.html`
- `tests/unit/api/test_supplementary_service.py`
- `tests/component/api/test_supplementary_panel_routes.py`

**Modified:**
- `src/db/models/catalog_instrument.py` (+ country, state)
- `src/services/firstrate/instrument_mapper.py` (+ _COL_COUNTRY/_COL_STATE, persist)
- `src/db/repositories/catalog_instrument_repository.py` (upsert updates country/state — async + sync)
- `src/db/repositories/catalog_dividend_repository.py` (+ has_for_ticker)
- `src/db/repositories/catalog_stock_split_repository.py` (+ has_for_ticker)
- `src/api/dependencies.py` (+ dividend/split repo DI + aliases)
- `src/api/models/explorer.py` (+ has_dividends/has_splits/has_company_profile)
- `src/api/stats_service.py` (optional repos → flags)
- `src/api/rest/explorer.py` (inject repos into get_ticker_stats)
- `src/api/ui/explorer.py` (+ /explorer/supplementary route; chart_panel OOB wiring)
- `templates/explorer/explorer.html` (+ #supplementary-panel container)
- `templates/explorer/chart_panel.html` (+ supplementary OOB block)
- `templates/explorer/stats_panel.html` (− Epic-4 placeholder note)
- `static/css/app.css` (rebuilt Tailwind)
- `tests/unit/models/test_catalog_instrument_model.py`
- `tests/unit/services/firstrate/test_instrument_mapper.py`
- `tests/component/db/test_catalog_instrument_repository.py` (+ DDL country/state, upsert test)
- `tests/component/db/test_catalog_dividend_repository.py` (+ has_for_ticker tests)
- `tests/component/db/test_catalog_stock_split_repository.py` (+ has_for_ticker tests)
- `tests/component/services/firstrate/test_instrument_mapper.py` (+ DDL country/state)
- `tests/component/api/test_chart_panel_routes.py` (+ repo overrides, OOB test)
- `tests/component/api/test_stats_panel_routes.py` (+ repo overrides, flag tests, inverted placeholder guard)

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-06-03 | Amelia (Dev) | Story 4-2 implemented via `bmad-dev-story`. All 10 tasks complete. country/state schema + migration `dbec2c1f25a6` + backfill; supplementary repos `has_for_ticker`; `supplementary_service` + `format_split_ratio`; `/explorer/supplementary` fragment route + templates; `#supplementary-panel` container + chart-panel OOB; availability flags on `TickerStatsResponse`; Epic-4 placeholder removed. 49 new/changed tests; 924 unit + 657 component green; lint/format/typecheck clean. Status → review. |
