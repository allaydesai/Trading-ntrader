# Story 4.4: ETF Metadata Panel with N/A-Aware Rendering

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a selected ETF's FMP-resolved metadata (name, venue, sector, country, etc.) displayed with `N/A` rendered cleanly where a descriptive value is unavailable — and the venue state shown distinctly from a descriptive gap,
so that I can trust what I see and tell an honest data gap apart from a bug.

## Acceptance Criteria

1. **Given** a selected ETF with full FMP metadata (a `RESOLVED` `instrument_metadata` row), **When** the metadata panel renders, **Then** name, venue, asset type, sector, industry, country, currency and IPO date display their resolved values correctly.
2. **Given** a selected ETF with descriptive gaps (one or more of the five descriptive fields — `company_name` / `sector` / `industry` / `country` / `currency` — stored as the `NA_SENTINEL` `"N/A"` or `NULL`), **When** the panel renders, **Then** each missing descriptive field renders as a clear `N/A` **via shared `@computed_field` `display_*` props** — never a blank, never ambiguous — and the template never branches on `""`/`None` ad hoc (it reads only `metadata.display_*`).
3. **Given** a ticker whose venue is unresolved (`venue IS NULL`, typically `resolution_status = VENUE_UNRESOLVED`), **When** the panel renders, **Then** the venue is shown **distinctly** from a descriptive `N/A` — a labelled *Unresolved* state (never the `"N/A"` sentinel; `venue` never holds the sentinel per ADR-2), visually differentiated from the muted descriptive `N/A`.
4. **Given** a selected ticker with **no** `instrument_metadata` row at all (never resolved — e.g. a Phase-1 stock catalog, or an ETF whose metadata was never fetched), **When** the panel renders, **Then** it degrades gracefully to a clear empty-state message (no 404, no traceback) — the panel is additive, mirroring the supplementary panel (Story 4.2).

## Tasks / Subtasks

- [x] **Task 1: N/A-aware presentation model** (AC: #1, #2, #3) — *write `tests/unit/api/test_metadata_panel_model.py` FIRST (TDD Red→Green)*
  - [x] Add `src/api/models/metadata_panel.py` — an `EtfMetadataPanel(BaseModel)` presentation model beside `TickerRow`/`TickerStatsResponse` (`src/api/models/explorer.py`), holding the raw FMP fields: `ticker: str`, `metadata_provider: Optional[str]`, `venue: Optional[str]`, `currency`, `asset_type`, `company_name`, `sector`, `industry`, `country` (all `Optional[str] = None`), `ipo_date: Optional[date] = None`, `resolution_status: Optional[ResolutionStatus] = None`. [Source: src/api/models/explorer.py:86-128 (`TickerRow` + `coverage_pct` `@computed_field` precedent); src/models/instrument_metadata.py:61-102 (canonical field set + three-state N/A rule)]
  - [x] Expose the shared N/A logic as `@computed_field` `display_*` props (each returns a non-empty `str`, so templates never branch): `display_company_name`, `display_currency`, `display_asset_type`, `display_sector`, `display_industry`, `display_country` each = the value if truthy else `NA_SENTINEL` (a module `_na(v)` helper collapses both `NULL`→`None` and an empty/`NA_SENTINEL` value to `"N/A"` — `"N/A"` is idempotent under `or NA_SENTINEL`). `display_ipo_date` = `ipo_date.isoformat()` if set else `NA_SENTINEL`. Import `NA_SENTINEL` from `src.models.instrument_metadata` (F401/F821 gate). [Source: src/models/instrument_metadata.py:25 (`NA_SENTINEL`), :155-163 (`_DESCRIPTIVE_FIELDS`)]
  - [x] Venue is rendered distinctly (AC3, never the N/A sentinel): `display_venue` = `venue` if set else the module `VENUE_UNRESOLVED_LABEL = "Unresolved"`; `venue_resolved` (`@computed_field -> bool`) = `venue is not None` — the template uses `venue_resolved` to switch styling (amber *Unresolved* pill vs a resolved venue code), so venue-state is visually separate from the muted descriptive `N/A`. Never emit `NA_SENTINEL` for venue.
  - [x] `@classmethod from_orm_row(cls, row) -> "EtfMetadataPanel"` builds the panel from the SQLAlchemy ORM `InstrumentMetadata` row (asset_type stored as a plain string, `resolution_status` as the enum). Explicit field copy (no `from_attributes` magic) — keeps the ORM→presentation seam obvious and testable. Keep the module `< 200` lines, every function `< 50`, fully return-type-annotated.

- [x] **Task 2: Async metadata repo dependency** (AC: #1, #4) — *no new logic, DI wiring only*
  - [x] In `src/api/dependencies.py` add `get_instrument_metadata_repository(session) -> InstrumentMetadataRepository` and the `InstrumentMetadataRepo = Annotated[InstrumentMetadataRepository, Depends(...)]` type alias, mirroring `get_dividend_repository` / `DividendRepo`. Import `InstrumentMetadataRepository` from `src.db.repositories.instrument_metadata_repository` (mind F401). [Source: src/api/dependencies.py:133-166 (`get_dividend_repository` + alias idiom); src/db/repositories/instrument_metadata_repository.py:22-84 (`get_by_ticker`)]

- [x] **Task 3: Panel context builder** (AC: #1, #2, #4) — *write `tests/unit/api/test_metadata_panel_service.py` FIRST (TDD)*
  - [x] Add `src/api/metadata_panel_service.py` with `async def _build_metadata_panel_context(metadata_repo, ticker) -> dict`, mirroring `src/api/supplementary_service.py`. `instrument_metadata` is keyed by **ticker only** (catalog-independent — Story 1.2), so the lookup takes no catalog. Body: `row = await metadata_repo.get_by_ticker(ticker)`; if `row is None` → `{"metadata": None, "has_metadata": False, "na_sentinel": NA_SENTINEL}` (AC4 additive empty-state); else `{"metadata": EtfMetadataPanel.from_orm_row(row), "has_metadata": True, "na_sentinel": NA_SENTINEL}`. Pass `na_sentinel` so the template can style a descriptive `N/A` distinctly without hardcoding the `"N/A"` literal (styling only — the value is already computed by the model). Structured `logger.debug` like the supplementary builder. [Source: src/api/supplementary_service.py:53-96]

- [x] **Task 4: Metadata-panel fragment route + OOB wiring** (AC: #1, #2, #3, #4) — *write `tests/component/api/test_metadata_panel_routes.py` FIRST (TDD, TestClient)*
  - [x] In `src/api/ui/explorer.py` add `@router.get("/metadata-panel", response_class=HTMLResponse) async def metadata_panel_fragment(request, metadata_repo: InstrumentMetadataRepo, ticker: str = Query(...))` — returns `templates.TemplateResponse("explorer/metadata_panel.html", {"request": request, **context})`. Ticker-only (no `catalog` param — the table is catalog-independent). Mirror the `/supplementary` fragment shape. [Source: src/api/ui/explorer.py:162-182]
  - [x] Inject `metadata_repo: InstrumentMetadataRepo` into `chart_panel_fragment` and merge `**(await _build_metadata_panel_context(metadata_repo, ticker))` into its template context so the metadata panel refreshes via OOB on every ticker/timeframe change (same as stats + supplementary). Add imports: `InstrumentMetadataRepo` (from `src.api.dependencies`), `_build_metadata_panel_context` (mind F401/F821). [Source: src/api/ui/explorer.py:414-545 (`chart_panel_fragment` OOB context assembly)]
  - [x] In `templates/explorer/chart_panel.html` add an OOB block after the supplementary one: `<div id="metadata-panel" hx-swap-oob="innerHTML">{% include "explorer/metadata_panel.html" %}</div>`. [Source: templates/explorer/chart_panel.html:81-84]

- [x] **Task 5: Templates — panel + skeleton + page mount** (AC: #1, #2, #3, #4)
  - [x] Add `templates/explorer/metadata_panel.html` (content-only fragment — the `#metadata-panel` id lives on the page container, like `supplementary_panel.html`). Header "Metadata"; a `<dl>` grid (same tokens as the supplementary Company Profile) with cells reading ONLY `metadata.display_*` (never `x or "—"`): Name, Venue, Asset Type, Sector, Industry, Country, Currency, IPO Date. Venue cell branches on `metadata.venue_resolved` — resolved → a normal chip with `display_venue`; unresolved → a distinct amber *Unresolved* pill (AC3). Descriptive cells whose `display_*` equals `na_sentinel` get a muted style (`text-slate-500 italic`) so an honest gap reads differently from a real value (AC2) — this compares the already-computed display string, not the raw field. When `not has_metadata` → a single clear empty-state line "No resolved metadata for this ticker yet" (AC4). Escape nothing manually — Jinja autoescape is on.
  - [x] Add `templates/explorer/metadata_panel_skeleton.html` (animate-pulse placeholder matching the card, `aria-hidden="true"`), mirroring `stats_panel_skeleton.html` / `supplementary_panel_skeleton.html`. [Source: templates/explorer/supplementary_panel_skeleton.html]
  - [x] In `templates/explorer/explorer.html` add a `<div id="metadata-panel" aria-live="polite" data-ticker=...>` mount with the lazy `hx-get="/explorer/metadata-panel"` on `hx-trigger="load"` (hx-vals: ticker only) + the skeleton include, placed between `#stats-panel` and `#supplementary-panel`. [Source: templates/explorer/explorer.html:128-149 (stats + supplementary lazy-load mounts)]

- [x] **Task 6: Tests** (AC: #1–#4)
  - [x] **Model (`tests/unit/api/test_metadata_panel_model.py`, `@pytest.mark.unit`):** full-metadata panel → every `display_*` equals the resolved value, `display_ipo_date` is ISO, `venue_resolved is True`, `display_venue` == the code. Descriptive-gap panel (`sector=None`, `country=NA_SENTINEL`, `company_name=""`) → those `display_*` == `NA_SENTINEL`; a set field still shows its value. Unresolved-venue panel (`venue=None`) → `display_venue == "Unresolved"`, `venue_resolved is False`, and `display_venue != NA_SENTINEL` (AC3). `from_orm_row` maps an ORM row (via `_make_orm(...)`) field-for-field.
  - [x] **Service (`tests/unit/api/test_metadata_panel_service.py`, `@pytest.mark.unit`):** row present → `has_metadata is True`, `ctx["metadata"]` is an `EtfMetadataPanel` with the row's ticker, `na_sentinel == NA_SENTINEL`. Row absent (`get_by_ticker` → None) → `has_metadata is False`, `ctx["metadata"] is None` (AC4). Use an `AsyncMock` repo (mirror `test_supplementary_service.py`).
  - [x] **Route (`tests/component/api/test_metadata_panel_routes.py`, `@pytest.mark.component`, `TestClient`, `app.dependency_overrides[get_instrument_metadata_repository]`):** 200 + fragment-only (no `<html>`/`<body>`, no `id="metadata-panel"` wrapper). Full metadata → contains the venue code + sector value. Descriptive gap → contains `N/A`. Unresolved venue → contains `Unresolved` and NOT `venue` `N/A` (AC3). Missing ticker query → 422. No row → 200 + empty-state text (AC4).

- [x] **Task 7: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate on the new model/service/route/dependency imports (`NA_SENTINEL`, `InstrumentMetadataRepository`, `InstrumentMetadataRepo`, `_build_metadata_panel_context`).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Fully annotate the new model/computed props/service/route.
  - [x] `make test-unit` green (model + service + no regressions); `make test-component` green (new route + existing chart/stats/supplementary panel routes still pass).
  - [x] `./scripts/build-css.sh` if any new Tailwind utility class was introduced (amber pill / muted N/A). Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`.
  - [x] Smoke via `TestClient` component tests (the harness blocks a live `uvicorn` subprocess); the `/explorer/metadata-panel` path + OOB swap are exercised there and in the chart-panel route test.

## Dev Notes

### The core idea (why this story exists)
Epic 1 resolved FMP metadata into `instrument_metadata` with a deliberate three-state N/A model (ADR-2): a descriptive field is a *value*, the `NA_SENTINEL` `"N/A"`, or `NULL` (never attempted); **`venue` is a real code or `NULL` — never the sentinel**. Story 4.4 surfaces that model in the explorer so the operator can *trust* what they see: a clean `N/A` where a descriptive value is genuinely absent, and a **distinct** venue state (an unresolved venue is a completeness problem, not a descriptive gap — it's what Epic 3's gate drives to zero). The honest-gap-vs-bug distinction is the whole point. [Source: epics.md Story 4.4 AC; src/models/instrument_metadata.py:21-102]

### Design decision — N/A logic lives once, in `@computed_field display_*` props
The AC forbids ad-hoc template branching (`{{ x or "—" }}`, `{% if x %}`). Instead the presentation model `EtfMetadataPanel` owns the single N/A mechanism: each `display_*` computed prop returns a guaranteed non-empty string, so the template only interpolates `{{ metadata.display_sector }}`. This mirrors the existing `TickerRow.coverage_pct` `@computed_field` precedent and keeps the "N/A" contract in one unit-testable place. The **only** template branch is `metadata.venue_resolved` (AC3's required venue-vs-N/A distinction) and a *styling-only* compare of the already-computed `display_*` string against `na_sentinel` for muting a gap — neither re-implements the null logic. [Source: src/api/models/explorer.py:114-128]

### Why a presentation model, not the domain/ORM model
- The ORM `InstrumentMetadata` (`src/db/models/instrument_metadata.py`) is a persistence row; the Pydantic domain `InstrumentMetadata` (`src/models/instrument_metadata.py`) is the resolution-layer model. Neither should carry web-display concerns. `EtfMetadataPanel` lives in `src/api/models/` (the view layer) and is built from the ORM row via `from_orm_row`, exactly as `TickerRow`/`TickerStatsResponse` are view models assembled in the routes. [Source: src/api/models/explorer.py; src/api/stats_service.py]

### The seam (data + control flow)
```
instrument_metadata (ticker PK, catalog-independent — Story 1.2)
        │  InstrumentMetadataRepository.get_by_ticker(ticker)   (async, web)
        ▼
  _build_metadata_panel_context(repo, ticker)
        │  row → EtfMetadataPanel.from_orm_row(row)   (display_* N/A logic)
        │  None → {has_metadata: False}               (AC4 additive empty-state)
        ▼
  GET /explorer/metadata-panel   (lazy load on deep-link)   ─┐
  chart_panel_fragment OOB swap  (refresh on ticker/tf)     ─┴─→ templates/explorer/metadata_panel.html
```
Read-only over the local metadata cache. No network, no provider (FMP) call, no write, no catalog Parquet read. [Source: src/api/ui/explorer.py:162-182, 414-545; src/api/supplementary_service.py]

### Panel wiring mirrors stats/supplementary (Stories 2-3 / 4-2)
Each explorer sub-panel is: a `<div id="…-panel">` mount in `explorer.html` with a lazy `hx-get` on `hx-trigger="load"` (deep-link initial render) + a skeleton include, an OOB `hx-swap-oob` block in `chart_panel.html` (refresh when the chart reloads on ticker/timeframe change), a fragment route in `src/api/ui/explorer.py`, and a context builder beside `stats_service`/`supplementary_service`. Story 4.4 adds one more panel in exactly this shape — the fragment is content-only (id on the container). [Source: templates/explorer/explorer.html:128-149; templates/explorer/chart_panel.html:76-84]

### Venue distinctness (AC3) — the one non-descriptive field
`venue` is architecturally not a descriptive field: it can never be `NA_SENTINEL` (the domain validator rejects it). An unresolved venue means the ticker is *non-backtestable* (Epic 3), a materially different state from "the provider didn't return a sector". So the panel renders it distinctly: `display_venue` = the code or `"Unresolved"`, and `venue_resolved` drives a distinct visual (amber *Unresolved* pill) — never the grey descriptive `N/A`. Do NOT collapse venue into the descriptive `_na` helper. [Source: src/models/instrument_metadata.py:96-102 (`venue_never_na_sentinel`); epics.md Story 4.4 AC3]

### Scope boundaries (do NOT do here)
- **No FMP / network call, no resolution, no venue guessing** — read the resolved `instrument_metadata` cache only. Resolution is Epic 1; venue overrides are Story 3.3.
- **No new column / migration / stored `display_*`** — computed props over the existing row.
- **No change to the supplementary "Company Profile" section** — that renders `catalog_instruments` fields (a separate table). This story adds a *new* FMP-metadata panel; it does not touch or merge the supplementary panel.
- **No CLI / sync path, no REST JSON model change** — this is a UI (async) fragment only.
- **No stats/chart/backtest logic change** — the metadata panel is additive; `nautilus_id`-gated behaviour (chart/stats) is untouched.
- **No catalog param on the lookup** — `instrument_metadata` is keyed by ticker alone; do not thread a catalog through it.

### Project Structure Notes
- **New:** `src/api/models/metadata_panel.py` (`EtfMetadataPanel` + `display_*`/`venue_resolved` + `from_orm_row`), `src/api/metadata_panel_service.py` (`_build_metadata_panel_context`), `templates/explorer/metadata_panel.html`, `templates/explorer/metadata_panel_skeleton.html`.
- **Modified:** `src/api/dependencies.py` (`get_instrument_metadata_repository` + `InstrumentMetadataRepo`), `src/api/ui/explorer.py` (`/metadata-panel` route + metadata context merged into `chart_panel_fragment`), `templates/explorer/chart_panel.html` (OOB block), `templates/explorer/explorer.html` (mount + skeleton).
- **New tests:** `tests/unit/api/test_metadata_panel_model.py`, `tests/unit/api/test_metadata_panel_service.py`, `tests/component/api/test_metadata_panel_routes.py`.

### Testing standards
- **Unit tier** for the pure presentation model (`display_*`/`from_orm_row`) and the async context builder (`AsyncMock` repo). **Component tier** (`TestClient` + `app.dependency_overrides`) for the fragment route. No integration/e2e — no engine, no C extensions, no Parquet read. [Source: CLAUDE.md Decision Heuristics; tests/unit/api/test_supplementary_service.py; tests/component/api/test_supplementary_panel_routes.py]
- **TDD non-negotiable** — Red first for each `display_*` prop, the context builder, and the route. [Source: development-principles.md]

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.4] — story statement + 3 AC (FR30)
- [Source: src/models/instrument_metadata.py:21-102, 155-163] — `NA_SENTINEL`, three-state N/A rule, `venue_never_na_sentinel`, the five descriptive fields
- [Source: src/db/models/instrument_metadata.py:47-71] — ORM row (`from_orm_row` source)
- [Source: src/db/repositories/instrument_metadata_repository.py:73-84] — async `get_by_ticker`
- [Source: src/api/supplementary_service.py:53-96] — `_build_supplementary_context` (builder to mirror)
- [Source: src/api/ui/explorer.py:162-182, 414-545] — `/supplementary` fragment + `chart_panel_fragment` OOB assembly
- [Source: templates/explorer/{explorer,chart_panel,supplementary_panel,supplementary_panel_skeleton}.html] — panel wiring precedents
- [Source: src/api/models/explorer.py:86-128] — `TickerRow.coverage_pct` `@computed_field` precedent

### Open questions (non-blocking — proceed with the documented default)
1. **Panel visibility for non-ETF / never-resolved tickers.** Default: the panel is always mounted for any selected ticker; a ticker with no `instrument_metadata` row shows the AC4 empty-state ("No resolved metadata for this ticker yet"). This makes it forward-compatible (works the moment metadata is resolved) and honest for Phase-1 stock catalogs. Flag if the team wants the panel hidden entirely unless a row exists.
2. **Unresolved-venue label text.** Default: `"Unresolved"` for `venue IS NULL`. Not distinguishing `VENUE_UNRESOLVED` (attempted, failed) from `UNRESOLVED` (never attempted) in the venue chip — both surface as `Unresolved` since from the operator's view the venue is simply not usable. Flag if a finer label is wanted.

## Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). All 4 ACs met and every scope boundary respected; two substantive fixes applied, one test strengthened, two nits documented.

- [x] [Review][Fix] **Empty/whitespace venue desyncs `venue_resolved` from `display_venue` (Blind + Edge + Auditor, Med).** `venue_resolved` used `venue is not None` while `display_venue` used truthiness — a stored `""`/whitespace venue (persistable: the ORM column is a plain nullable `String` and the domain validator only rejects `"N/A"`) rendered the literal word "Unresolved" in the *resolved* (plain mono) branch instead of the amber pill. **Fixed** by normalizing a blank/whitespace venue to `None` in `from_orm_row` (`_normalize_venue`) AND making `venue_resolved = bool(self.venue)`, so the two props can never disagree on the resolved/unresolved boundary. Added model tests for `""`, whitespace, and a boundary-agreement sweep.
- [x] [Review][Fix] **Chart panel could 500 wholesale if `instrument_metadata` is unavailable (Edge Case, Med).** Story 4.4 makes the metadata cache a *new* dependency of `chart_panel_fragment`; `get_by_ticker` is unwrapped, so a missing/un-migrated `instrument_metadata` table (or a dropped connection) would raise a `SQLAlchemyError` out of the metadata build and 500 a chart panel that previously rendered fine. **Fixed** by degrading `_build_metadata_panel_context` to the additive empty-state on `SQLAlchemyError` (single warning log, no traceback) — the metadata read is the last DB op in the request, so a caught fault can't corrupt earlier stats/supplementary reads. Added a service unit test (`OperationalError` → empty-state).
- [x] [Review][Patch] **Route AC3 test only asserted the positive (Auditor, test-completeness).** `test_unresolved_venue_shown_distinctly` asserted `"Unresolved" in text` but not that the venue avoids the `N/A` sentinel end-to-end. **Strengthened** to also assert `NA_SENTINEL not in text` (all descriptive fields resolved in that fixture) and that the distinct `text-amber-400` pill styling is present.
- [x] [Review][Doc] **Muted-gap cell uses `text-slate-500` without the `italic` from the task wording (Auditor, cosmetic).** `.italic` is not present in the committed/purged `static/css/app.css` and `./scripts/build-css.sh` is approval-gated in this environment; adding a dead class would render nothing. AC2's requirement ("an honest gap reads differently from a real value") is met by the `text-slate-500` vs `text-slate-100` colour contrast. Intentional deviation — kept colour-only muting; no rebuild needed (all utilities reused from `supplementary_panel.html` / `ticker_list.html`, verified against the compiled CSS + the `test_compiled_css` gate).
- [x] [Review][Ack] **Deep-link double-fetch of `#metadata-panel` (Blind, Low).** On an initial deep-link with a selected ticker, both the lazy `hx-trigger="load"` mount and the chart-panel OOB swap populate the panel (two `get_by_ticker` calls). This is the **exact** established pattern for the sibling `#stats-panel` / `#supplementary-panel`; matching it is deliberate (consistency over a micro-optimization that would diverge one panel). No change.
- [x] [Review][Ack] **`resolution_status` carried but unused by the template (Edge Case, note).** The panel drives venue state off `venue` alone; `resolution_status` is mapped by `from_orm_row` (and asserted by a test) but not rendered. Kept — it faithfully mirrors the row and is available for future refinement (Open Question #2); not dead/incorrect data.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/unit/api/test_metadata_panel_model.py tests/unit/api/test_metadata_panel_service.py tests/component/api/test_metadata_panel_routes.py tests/component/api/test_chart_panel_routes.py` — 67 pass (model + service + route + chart-panel OOB, incl. post-review venue-normalization / DB-degrade / AC3-negative tests).
- `uv run pytest tests/unit/api tests/component/api` — 331 pass (no regressions across the explorer/backtest UI suites).
- `uv run ruff check .` — All checks passed. `uv run mypy .` — Success: no issues found in 357 source files.

### Completion Notes List

- **AC1** — `EtfMetadataPanel.from_orm_row` maps the `instrument_metadata` row; the panel renders all 8 fields (Name, Venue, Asset Type, Sector, Industry, Country, Currency, IPO Date) exclusively via `metadata.display_*`.
- **AC2** — the shared `_na()` helper + `display_*` `@computed_field` props are the single place a descriptive gap (`None`/`""`/`NA_SENTINEL`) collapses to `"N/A"`; the template reads only `display_*` (the sole template branch is `venue_resolved` for AC3 and a styling-only compare of the already-computed string against `na_sentinel`).
- **AC3** — venue is rendered distinctly: `display_venue` = code or `"Unresolved"` (never the sentinel), `venue_resolved` drives a distinct amber pill. Blank/whitespace venue is normalized to the unresolved state so the two props never disagree.
- **AC4** — no `instrument_metadata` row → additive empty-state ("No resolved metadata for this ticker yet"), 200 not 404; extended (post-review) to also degrade gracefully on a DB fault so the chart panel never 500s on this new dependency.
- **Design** — presentation model in `src/api/models/`, context builder beside `stats_service`/`supplementary_service`, fragment route + lazy mount + OOB swap mirroring the stats/supplementary panels. No FMP/network call, no migration/column, no supplementary-panel change, no CLI/sync path, no catalog param on the ticker-keyed lookup, no chart/stats/backtest logic change. All Tailwind utilities reused from existing templates (no CSS rebuild required).

### Change Log

| Date | Change |
|---|---|
| 2026-07-16 | Story 4.4 drafted (create-story). Status → ready-for-dev. |
| 2026-07-16 | Implemented `EtfMetadataPanel` (N/A-aware `display_*` props), `_build_metadata_panel_context`, `InstrumentMetadataRepo` DI, `/explorer/metadata-panel` route + chart-panel OOB integration, panel/skeleton templates + explorer mount; unit + component tests (TDD). Status → review. |
| 2026-07-16 | Code review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). Fixed venue normalization/`venue_resolved` desync and added chart-panel DB-fault degradation; strengthened AC3 route test; documented the `italic`/double-fetch/`resolution_status` nits. Gates green. Status → done. |

### File List

- `src/api/models/metadata_panel.py` (new — `EtfMetadataPanel` + `display_*`/`venue_resolved` + `from_orm_row` + `_na`/`_normalize_venue`)
- `src/api/metadata_panel_service.py` (new — `_build_metadata_panel_context`, DB-fault-degrading)
- `src/api/dependencies.py` (modified — `get_instrument_metadata_repository` + `InstrumentMetadataRepo` alias)
- `src/api/ui/explorer.py` (modified — `/metadata-panel` route + metadata context merged into `chart_panel_fragment`)
- `templates/explorer/metadata_panel.html` (new — N/A-aware panel fragment)
- `templates/explorer/metadata_panel_skeleton.html` (new — lazy-load skeleton)
- `templates/explorer/chart_panel.html` (modified — OOB `#metadata-panel` block)
- `templates/explorer/explorer.html` (modified — lazy `#metadata-panel` mount + skeleton)
- `tests/unit/api/test_metadata_panel_model.py` (new — `display_*` / venue distinctness / normalization)
- `tests/unit/api/test_metadata_panel_service.py` (new — context builder + DB-fault degrade)
- `tests/component/api/test_metadata_panel_routes.py` (new — fragment route, N/A, AC3, empty-state)
- `tests/component/api/test_chart_panel_routes.py` (modified — metadata-repo mock override + OOB assertion)
