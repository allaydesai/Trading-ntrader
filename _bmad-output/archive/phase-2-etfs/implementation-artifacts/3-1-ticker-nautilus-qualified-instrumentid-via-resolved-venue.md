# Story 3.1: Ticker → Nautilus-Qualified InstrumentId via Resolved Venue

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want to map an ETF ticker to a Nautilus-qualified `InstrumentId` using its resolved venue, syncing the qualification into `catalog_instruments`,
so that tickers with a known venue become fully-qualified, backtestable instruments — and no venue is ever guessed.

## Acceptance Criteria

1. **Given** a ticker with a resolved `venue` in `instrument_metadata`, **When** `instrument_mapper` computes its identity, **Then** it produces a Nautilus-qualified `nautilus_id` (e.g. `SPY.ARCA`) sourced from the resolved metadata venue (`InstrumentMetadataService`), **not** from a hardcoded/guessed venue (e.g. the FirstRate `company_profiles.csv` exchange column).
2. **Given** the qualification sync (ADR-3), **When** import qualification runs, **Then** `catalog_instruments` reads the venue from `instrument_metadata` to compute `nautilus_id` (and mirrors the venue into `catalog_instruments.exchange`), with `instrument_metadata` remaining the single source of truth for resolved metadata and `catalog_instruments` the source of truth for bar counts/date ranges — single writer = the import pipeline.
3. **Given** a ticker whose venue is unresolved (`resolution_status == VENUE_UNRESOLVED`, i.e. `venue is None`), **When** qualification runs, **Then** no `nautilus_id` is fabricated for it — `catalog_instruments.nautilus_id` is left `None` (unqualified). Full exclude/flag/keep-bars handling is deferred to Story 3.5.
4. **Given** the non-resolver path (Phase-1 stocks import, or an ETF import with FMP unconfigured so `metadata_resolver is None`), **When** a ticker is imported, **Then** identity resolution is unchanged from today (the existing `resolve_instrument_id` DB lookup) — no regression.

## Tasks / Subtasks

- [x] **Task 1: `InstrumentMapper` — compute qualified identity from a resolved venue + ADR-3 sync** (AC: #1, #2, #3) — *write the tests in `tests/unit/services/firstrate/test_instrument_mapper.py` FIRST (TDD Red→Green)*
  - [x] Add a pure staticmethod `qualified_instrument_id(ticker: str, venue: Optional[str]) -> Optional[InstrumentId]` to `src/services/firstrate/instrument_mapper.py`: returns `InstrumentId.from_str(f"{ticker}.{venue}")` when `venue` is truthy, else `None`. `venue` is already guaranteed to be a real code or `None` (never `NA_SENTINEL`) by the domain model's `venue_never_na_sentinel` validator — do **not** re-check the sentinel here, and do **not** import `NA_SENTINEL`. [Source: src/models/instrument_metadata.py:96-102; epics.md#Story-3.1 AC1]
  - [x] Add `sync_qualification(self, ticker: str, catalog_name: str, venue: Optional[str]) -> Optional[InstrumentId]` — the ADR-3 qualification sync:
    - Fetch the existing row via `self._repo.get_by_ticker(catalog_name, ticker)`. If `None`, log a warning (`qualification_sync_skipped`, reason "no catalog_instruments row") and return `None` — the row is created by `load_company_profiles` before any import, so a miss means profiles were not loaded (defensive; mirrors `_upsert_metadata`'s missing-row handling).
    - Compute `qid = self.qualified_instrument_id(ticker, venue)`.
    - Set `instrument.nautilus_id = str(qid) if qid is not None else None` and `instrument.exchange = venue` (mirror the authoritative venue onto the identity row; `None` when unresolved).
    - `self._repo.upsert(instrument)` (the sync upsert re-fetches the same row and copies the mutated fields — idempotent). Return `qid`.
    - Add a structured `logger.debug("qualification_synced", ticker=..., venue=..., nautilus_id=...)` line. [Source: src/services/firstrate/instrument_mapper.py:120-140; src/db/repositories/catalog_instrument_repository.py:276-323; architecture.md:262-268 (ADR-3)]
  - [x] **Do NOT** change `load_company_profiles` (it still seeds the row from the CSV, including the CSV `exchange` as a provisional value); do NOT change `resolve_instrument_id` or `is_loaded`. The qualification sync is additive and runs later in the pipeline, over-writing the provisional CSV exchange with the authoritative resolved venue when one exists. [Source: src/services/firstrate/instrument_mapper.py:69-118,120-152]
  - [x] Keep functions `< 50` lines and the file `< 500` lines (currently 153). Fully annotate return types (`Optional[InstrumentId]`) for the F821 import gate. [Source: CLAUDE.md Foundational Rules]

- [x] **Task 2: `ImportService` — qualify from resolved metadata before writing bars** (AC: #1, #2, #3, #4) — *tests FIRST (TDD)*
  - [x] **Reorder** the per-ticker loop in `_import_ticker` so metadata resolution + qualification happen **before** the instrument-id/BarType is needed (the parser writes bars under that id, so the venue must be known before parsing). New order after the `_classify_ticker` skip short-circuit:
    1. `self._resolve_metadata(ticker)` — *moved up* from its current step 3b position (after parse). It is cache-first, dedup-guarded, and fault-isolated already; moving it earlier is safe and is required so the qualified venue is known before parse. [Source: src/services/firstrate/import_service.py:344-352,718-747]
    2. `instrument_id = self._qualify_ticker(ticker, catalog_name)` — *replaces* the old `resolve_instrument_id` call at step 1. [Source: import_service.py:297-298]
    3. If `instrument_id is None` → the ticker's venue is unresolved: log `ticker_unqualified` (fields `ticker`, `catalog`, `timeframe`, reason "venue unresolved — deferred to Story 3.5") and return an `ImportResult(status="skipped", outcome="skipped", row_count=0, ...)`. No parse, no `write_data`, no bar-count upsert. (AC3 — no `nautilus_id` fabricated; bars deferred to 3.5.)
    4. Otherwise continue exactly as today: build `BarType`, parse, OHLC sanity, `write_data`, verify row count + sample points, `_upsert_metadata` (bar counts / date ranges). **Remove** the now-duplicated `_resolve_metadata(ticker)` call from its old step-3b position. [Source: import_service.py:297-405]
  - [x] Add `_qualify_ticker(self, ticker: str, catalog_name: str) -> Optional[InstrumentId]`:
    - `md = self._resolved_metadata.get(ticker)`.
    - **If `self._metadata_resolver is None` OR `md is None`** (no resolver — stocks/unconfigured path; or resolution faulted so nothing was captured): return `self._instrument_mapper.resolve_instrument_id(ticker, catalog_name)` — the existing behavior, unchanged (AC4). A resolver fault must fall back to the existing DB identity, never silently drop the ticker.
    - **Else** (resolver ran and captured domain metadata): return `self._instrument_mapper.sync_qualification(ticker, catalog_name, md.venue)` — writes `catalog_instruments.nautilus_id`/`exchange` from the resolved venue and returns the qualified id (or `None` when `md.venue is None`, i.e. `VENUE_UNRESOLVED`). (AC1, AC2, AC3.) [Source: import_service.py:123-151,718-747; src/models/instrument_metadata.py:83-93]
  - [x] Do NOT change `_resolve_metadata`, `_classify_ticker`, `_upsert_metadata`, the `resolved_metadata` property, or the dedup sets — only their call ordering in `_import_ticker`. Keep `_import_ticker` `< 50` lines by extracting the unqualified-return into `_qualify_ticker` + the reorder (extract a small helper if the function grows past the limit). [Source: import_service.py:245-438; CLAUDE.md size limits]

- [x] **Task 3: Unit tests — pure mapper + orchestration seam** (AC: #1, #2, #3, #4)
  - [x] **Mapper (`tests/unit/services/firstrate/test_instrument_mapper.py`)** — extend with a `TestQualification` class (`@pytest.mark.unit`, mirror the `MagicMock` repo style already in the file):
    - `qualified_instrument_id("SPY", "ARCA")` → `InstrumentId.from_str("SPY.ARCA")`.
    - `qualified_instrument_id("SPY", None)` → `None`. `qualified_instrument_id("SPY", "")` → `None`.
    - `sync_qualification` with a resolved venue → fetches the row, sets `nautilus_id="SPY.ARCA"` and `exchange="ARCA"`, calls `repo.upsert`, returns the `InstrumentId`. (Use a real `CatalogInstrument` as the `get_by_ticker` return so the mutation is observable — mirror `test_resolve_existing_ticker`.)
    - `sync_qualification` with `venue=None` → sets `nautilus_id=None`, calls upsert, returns `None` (AC3 — proves no fabrication).
    - `sync_qualification` when `get_by_ticker` returns `None` → returns `None`, does **not** call `upsert` (defensive missing-row path). [Source: tests/unit/services/firstrate/test_instrument_mapper.py:201-234]
  - [x] **ImportService (`tests/unit/services/firstrate/test_import_service.py`)** — add a `TestVenueQualification` class using the resolver-injecting helper pattern from `TestResolvedMetadataCollection` (`_service_with_resolver`, `_domain_metadata`):
    - **Resolved venue** — resolver returns `_domain_metadata("SPY")` (venue `"ARCA"`, `RESOLVED`); assert `_qualify_ticker("SPY", CATALOG)` calls `mapper.sync_qualification("SPY", CATALOG, "ARCA")` and returns its value; assert `resolve_instrument_id` is **not** used on this path.
    - **Unresolved venue** — resolver returns `_domain_metadata("ZZZ", ResolutionStatus.VENUE_UNRESOLVED)` with `venue=None`; stub `mapper.sync_qualification.return_value = None`; assert `_qualify_ticker` returns `None`.
    - **No resolver (AC4)** — the plain `service` fixture: assert `_qualify_ticker` delegates to `mapper.resolve_instrument_id(ticker, catalog)` and never calls `sync_qualification`.
    - **Resolver fault fallback** — resolver `.resolve` raises (so `_resolved_metadata` stays empty); assert `_qualify_ticker` still returns `resolve_instrument_id(...)` (fault-tolerant AC4).
    - **End-to-end unqualified skip** — a full `import_directory` run with a resolver whose ticker resolves `VENUE_UNRESOLVED` (venue `None`) and `mapper.sync_qualification.return_value = None`: assert the `ImportResult.status == "skipped"`, `catalog.write_data` is **not** called for it, and a `ticker_unqualified` log line is emitted. (Follow the `configured_service`/`patch(get_parser)` idiom, but inject a resolver.) [Source: tests/unit/services/firstrate/test_import_service.py:1209-1288,1295-1335]
  - [x] **Regression guard** — confirm the existing `configured_service` (no resolver) success-path tests still pass unchanged: `_qualify_ticker` → `resolve_instrument_id` returns the mocked `SPY.ARCA`, bars written as before.

- [x] **Task 4: Component test — real sync repos over SQLite/Postgres double** (AC: #2)
  - [x] Add a component test in `tests/component/services/firstrate/test_instrument_mapper.py` (real `SyncCatalogInstrumentRepository` against the existing component DB fixture) proving the ADR-3 sync round-trips: seed a `catalog_instruments` row (via `load_company_profiles` or a direct upsert with a provisional CSV exchange like `"NYSE"`), call `sync_qualification(ticker, catalog, "ARCA")`, re-`get_by_ticker`, and assert the persisted `nautilus_id == "SPY.ARCA"` and `exchange == "ARCA"` (authoritative venue over-wrote the provisional CSV exchange). Add a second assertion: `sync_qualification(ticker, catalog, None)` persists `nautilus_id is None`. Mirror the fixtures/markers already in `tests/component/services/firstrate/test_instrument_mapper.py`. [Source: tests/component/services/firstrate/test_instrument_mapper.py; tests/component/db/test_catalog_instrument_repository.py]

- [x] **Task 5: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new `Optional` usage / `InstrumentId` already imported in the mapper).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: 6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Fully annotate the new methods. [Source: reference_mypy_baseline_debt memory]
  - [x] `make test-unit` green (mapper + import_service unit deltas, no regressions). `make test-component` green for the new mapper component test.
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`.

### Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). Acceptance Auditor: all 4 ACs satisfied, scope respected. Fixes applied and re-verified (1848 unit+component pass, ruff+mypy clean).

- [x] [Review][Patch] Missing-profile row mislabeled as venue-unresolved — `_qualify_ticker` treated `sync_qualification`'s `None` (which also means "no `catalog_instruments` row") as unresolved and silently skipped a ticker whose venue actually resolved. Now falls back to `resolve_instrument_id` (loud `InstrumentMappingError`) when the venue is resolved but the sync returns `None`. [src/services/firstrate/import_service.py:_qualify_ticker]
- [x] [Review][Patch] Unqualified skip printed "skipped (already complete)" — a dropped venue-unresolved ticker was cosmetically indistinguishable from an idempotent skip. The `ImportResult` now carries a venue-unresolved reason in `error` and `_print_progress_line` renders it (status stays `"skipped"`, so summary/exit-code are unaffected). [src/services/firstrate/import_service.py; src/cli/commands/import_reporting.py:_print_progress_line]
- [x] [Review][Patch] Qualification re-synced on every timeframe pass — added a per-run `_qualified_ids` dedup (mirrors `_resolved_tickers`) so the catalog_instruments read+write happens at most once per ticker, not 5× for the ETF default. [src/services/firstrate/import_service.py:_qualify_ticker]
- [x] [Review][Defer] Cross-run orphan: a ticker with pre-existing bars that later resolves `VENUE_UNRESOLVED` gets `nautilus_id` nulled while its bars remain on disk (unreachable). Nulling the provisional id is required by AC3 for the fresh case; the keep-bars/flag reconciliation for already-imported unresolved tickers is Story 3.5's explicit deliverable ("Exclude & Flag Non-Backtestable Tickers"). Deferred to Story 3.5. [src/services/firstrate/instrument_mapper.py:sync_qualification]
- Dismissed as noise: `exchange` set to `None` on unresolved (intended per AC2, tested); metadata resolved before parse (the spec-mandated Task 2 reorder, confirmed desirable by the auditor); `InstrumentId.from_str` raising on a malformed venue (a loud failure is correct — the conservative venue map emits clean codes).

## Dev Notes

### The core idea (why this story exists)
Venue correctness is the Phase-2 signature domain risk (architecture.md:118-120): a wrong/guessed venue silently corrupts backtest results, so the architecture **forbids inference** and gates completion on 100% coverage (Story 3.4). Today the ETF import fabricates `nautilus_id` as `{ticker}.{CSV-exchange}` inside `load_company_profiles` — the FirstRate `company_profiles.csv` exchange column is exactly the "guessed" venue this story replaces. Story 3.1 makes the **FMP-resolved venue** (persisted in `instrument_metadata` by Epic 1, overridable in Story 3.3) the authoritative source of the qualified identity, and syncs it onto `catalog_instruments` (ADR-3) so the explorer/backtest readers see the correct venue.

### The seam (data + control flow)
```
company_profiles.csv → load_company_profiles → catalog_instruments row seeded
                                                 (nautilus_id = {ticker}.{CSV exchange}, provisional)
per-ticker import loop (ImportService._import_ticker), resolver present:
  _resolve_metadata(ticker)        → InstrumentMetadataService.resolve() → upsert instrument_metadata
                                     (single source of truth for resolved venue)  [Epic 1, done]
  _qualify_ticker(ticker)          → mapper.sync_qualification(ticker, catalog, md.venue)   [THIS STORY]
                                       venue resolved → catalog_instruments.nautilus_id = {ticker}.{venue}
                                       venue None     → catalog_instruments.nautilus_id = None (unqualified)
  BarType from instrument_id       → parse → write_data → verify → _upsert_metadata (bar counts)
```
`instrument_metadata` = source of truth for resolved metadata; `catalog_instruments` = source of truth for bar counts/date ranges. **Single writer per table = the import pipeline** → no drift (ADR-1/ADR-3). [Source: architecture.md:262-268,533-540,551-555]

### Why the reorder is mandatory (not cosmetic)
`parser.parse_file(file_path, instrument_id, bar_type)` stamps every `Bar` with the instrument id, and `catalog.write_data(bars)` stores them under `{INSTRUMENT_ID}/{BAR_TYPE}/`. If the bars are written under the CSV venue but `catalog_instruments.nautilus_id` says the FMP venue, the Epic-5 backtest loader (which reads `nautilus_id`) will look under the wrong path and find nothing. So the authoritative venue **must** be known before parsing → metadata resolution + qualification move ahead of `resolve_instrument_id`/`BarType` construction. Since `resolve_instrument_id` reads `catalog_instruments.nautilus_id`, syncing the qualified id **before** that call means the existing `resolve_instrument_id` lookup transparently returns the qualified id — no change to `resolve_instrument_id` itself. [Source: import_service.py:297-364; architecture.md:104-105 "venue-qualified InstrumentId is the precondition for instrument qualification"]

### Scope boundaries (do NOT do here)
- **No unresolved-venue *report*** (`metadata unresolved` CLI) — that is Story 3.2.
- **No `venue_overrides.csv`** load/merge — that is Story 3.3. This story consumes whatever venue Epic 1 already persisted; overrides simply flip more rows to `RESOLVED` later, and the sync picks them up unchanged.
- **No coverage report / completeness gate** — Story 3.4.
- **No exclude/flag/keep-bars for non-backtestable tickers** — Story 3.5. For 3.1 an unresolved ticker is left unqualified and its bars are **skipped** this run (a clean `skipped` `ImportResult`, no `write_data`); 3.5 will convert that into "keep the bars, flag non-backtestable". This staged handling is exactly what AC3 ("handled by Story 3.5") intends. Do not build 3.5's behavior early.
- **No new DB columns / migrations / settings** — everything needed exists (`catalog_instruments.nautilus_id`/`exchange`; `instrument_metadata.venue`/`resolution_status`).
- **No changes to Epic-1 code** (`InstrumentMetadataService`, `FMPMetadataProvider`, repositories, the domain/ORM models) — all `done`. This story only *reads* `md.venue` from the already-captured resolution result.

### Design decision — qualification lives in the import pipeline, not inside the mapper's own resolver call
The architecture source-tree note (architecture.md:486) sketches `instrument_mapper.py` as "venue from InstrumentMetadataService → nautilus_id". Two faithful implementations exist:
- **(A)** Inject `InstrumentMetadataService` into `InstrumentMapper` and have it call `.resolve(ticker)` itself.
- **(B, chosen)** `ImportService` (which already owns the cache-first resolution via `_resolve_metadata`, deduped once per ticker per run) passes the already-resolved `md.venue` into `mapper.sync_qualification(ticker, catalog, venue)`.
**(B) is chosen** because (A) would re-invoke `.resolve()` — re-hitting the FMP provider for every not-yet-`RESOLVED` ticker a second time (resolution is cache-first: only a `RESOLVED` row short-circuits) — duplicating Epic-1 work and provider quota. (B) also honors ADR-3's explicit "single writer = import pipeline" and keeps the mapper a pure, unit-testable identity+persistence primitive (venue in → id out + row synced). The mapper needs no new constructor dependency. Flag at review if the team prefers (A)'s literal reading. [Source: architecture.md:262-268,486; instrument_metadata_service.py:48-62 (cache-first: only RESOLVED short-circuits)]

### Previous-story intelligence
- **`_resolve_metadata` (Story 2.4/2.7, done)** already resolves cache-first, dedups per ticker per run (`_resolved_tickers`), captures the domain `InstrumentMetadata` into `_resolved_metadata`, and swallows/logs provider faults so a metadata hiccup never fails a bar import. Story 3.1 reuses this verbatim — only its call position moves. [Source: import_service.py:128-151,718-747]
- **`md.venue` semantics (Epic 1, done):** a real Nautilus venue code or `None`; **never** `NA_SENTINEL` (validator-enforced). `resolution_status == VENUE_UNRESOLVED` ⇔ `venue is None` for the degraded/unknown records the provider emits. So `venue is None` is the reliable "unqualified" signal — no need to branch on `resolution_status` in the mapper. [Source: src/models/instrument_metadata.py:83-102; instrument_metadata_service.py:88-102; fmp_provider `_degraded`]
- **Non-resolver path must stay byte-for-byte:** the `configured_service`/`service` fixtures inject no resolver, so `_qualify_ticker` routes to `resolve_instrument_id` — every existing full-import test passes untouched (AC4). [Source: tests/unit/services/firstrate/test_import_service.py:68-75,285-304]

### Testing standards
- **Unit tier** for the pure mapper primitives + the `_qualify_ticker` seam (mocked repo/resolver, no DB, no Nautilus engine). **Component tier** for the real-repo ADR-3 round-trip. No integration/e2e needed — no `BacktestEngine`, no C extensions. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — Red first for `qualified_instrument_id`, `sync_qualification`, and `_qualify_ticker`. [Source: development-principles.md]
- Mirror the AAA + `unittest.mock` idioms already in `test_instrument_mapper.py` / `test_import_service.py`; keep `@pytest.mark.unit` / component markers consistent with siblings.

### Git intelligence
Scope precedent: Epic-2 stories committed as `feat(...)`. A reasonable subject here: `feat(epic3): story 3-1 — ticker → nautilus-qualified instrumentid via resolved venue` (per the harness commit-message instruction). No AI/claude references in the message. [Source: CLAUDE.md Commit Format; task instruction]

### Project Structure Notes
- **Modified:** `src/services/firstrate/instrument_mapper.py` (+ `qualified_instrument_id`, `sync_qualification`), `src/services/firstrate/import_service.py` (`_import_ticker` reorder + `_qualify_ticker`).
- **Modified tests:** `tests/unit/services/firstrate/test_instrument_mapper.py`, `tests/unit/services/firstrate/test_import_service.py`, `tests/component/services/firstrate/test_instrument_mapper.py`.
- Placement matches architecture.md:485-486 (both files flagged MODIFIED for the qualification sync).

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.1] — story statement + 3 acceptance criteria; FR20
- [Source: _bmad-output/planning-artifacts/architecture.md:262-268] — ADR-3 qualification sync decision (single writer = import pipeline)
- [Source: _bmad-output/planning-artifacts/architecture.md:104-105,118-120,533-555] — venue-qualified id precondition; venue-correctness risk; two-table data boundary + import flow
- [Source: src/services/firstrate/instrument_mapper.py:55-152] — mapper today (CSV-derived nautilus_id, resolve/is_loaded)
- [Source: src/services/firstrate/import_service.py:245-438,718-747] — `_import_ticker` loop + `_resolve_metadata`
- [Source: src/models/instrument_metadata.py:61-102] — domain model; `venue` semantics + never-sentinel validator
- [Source: src/db/repositories/catalog_instrument_repository.py:264-343] — sync `get_by_ticker`/`upsert` used by the sync
- [Source: tests/unit/services/firstrate/test_instrument_mapper.py:201-265] — resolve/is_loaded test idioms to mirror
- [Source: tests/unit/services/firstrate/test_import_service.py:1209-1335] — resolver-injecting fixtures + `_domain_metadata`

### Open questions (non-blocking — proceed with the documented default)
1. **Mapper injection style.** Default: **(B)** — import pipeline passes `md.venue` to `sync_qualification`; mapper takes no new dependency (avoids double FMP resolution). Alternative **(A)** — inject `InstrumentMetadataService` into the mapper. Flag if the team wants the literal architecture-note reading.
2. **Unresolved-ticker outcome this run.** Default: `ImportResult(status="skipped", outcome="skipped")` with a `ticker_unqualified` log; bars not written until Story 3.5 grants "keep bars, non-backtestable". Alternative: import bars under a provisional path now — rejected as pre-empting 3.5 and risking the exact bars-under-wrong-venue mismatch this story exists to prevent.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/unit/services/firstrate/test_instrument_mapper.py tests/unit/services/firstrate/test_import_service.py` — 95 passed (6 new mapper qualification tests + 5 new `TestVenueQualification` import-service tests).
- `uv run pytest tests/component/services/firstrate/test_instrument_mapper.py` — 8 passed (2 new `TestQualificationSync` + 6 pre-existing, after repairing stale in-memory DDL).
- `uv run pytest tests/unit` — 1141 passed. `uv run pytest tests/component` — 703 passed, 16 skipped (pre-existing report-command skips).
- `uv run ruff check .` — All checks passed. `uv run mypy .` — Success: no issues found in 342 source files.

### Completion Notes List

- **AC1/AC2** — `InstrumentMapper.qualified_instrument_id(ticker, venue)` (pure) + `sync_qualification(ticker, catalog, venue)` (ADR-3 sync) added. The sync reads the `catalog_instruments` row, overwrites `nautilus_id`/`exchange` from the authoritative resolved venue, and upserts (single writer = import pipeline). `instrument_metadata` stays source of truth for resolved venue; `catalog_instruments` for bar counts/date ranges.
- **AC3** — a `None`/blank venue (`VENUE_UNRESOLVED`) yields no `InstrumentId`; `sync_qualification` persists `nautilus_id = None` (unqualified, never fabricated). In `_import_ticker` an unqualified ticker returns a clean `skipped` result (no parse/write) with a `ticker_unqualified` log — exclude/flag/keep-bars deferred to Story 3.5.
- **AC4** — `ImportService._qualify_ticker` falls back to the existing `resolve_instrument_id` DB lookup whenever no resolver is injected (stocks / FMP-unconfigured) or a provider fault captured nothing. The `_import_ticker` loop was reordered so `_resolve_metadata` + qualification run before `BarType` construction (bars must be written under the authoritative venue); `resolve_instrument_id` transparently returns the freshly-synced qualified id, so it needed no change.
- **Design** — chose approach (B): the import pipeline passes the already-resolved `md.venue` to the mapper (no double FMP resolution, honors ADR-3 "single writer"); mapper gains no new constructor dependency. Documented in Dev Notes.
- **Pre-existing fixes made while in-area (not caused by this story, proven via `git diff`/`git show`):**
  1. `tests/component/services/firstrate/test_instrument_mapper.py` — the inline `_CREATE_TABLE_SQL` was missing the five `date_range_end_*` columns the ORM now selects; every test in the file failed at HEAD. Added the columns.
  2. `tests/component/services/firstrate/test_import_service.py` — three idempotent-rerun tests (`test_partial_metadata_triggers_reimport`, `test_ac1_…`, `test_ac2_…`) rewound the shared `date_range_end`, but the per-timeframe classifier (commit d75a409) compares `date_range_end_daily`; that commit updated the unit tests but not these component tests. Rewound `date_range_end_daily` too so the tests express their intent. These tests are decided at `_classify_ticker` (step 0), which this story does not modify.

### Change Log

| Date | Change |
|---|---|
| 2026-07-13 | Story 3.1 drafted (SM). Status → ready-for-dev. |
| 2026-07-13 | Implemented mapper qualification + ADR-3 sync; `_import_ticker` reorder + `_qualify_ticker`; unit + component tests; repaired 2 pre-existing component-test drifts. Gates green. Status → review. |
| 2026-07-13 | Code review (3 adversarial layers). Applied 3 patches (missing-row loud-fail, honest unqualified progress line, per-run qualification dedup); deferred 1 cross-run orphan finding to Story 3.5. 1848 unit+component pass, ruff+mypy clean. Status → done. |

### File List

- `src/services/firstrate/instrument_mapper.py` (modified — `qualified_instrument_id`, `sync_qualification`)
- `src/services/firstrate/import_service.py` (modified — `_import_ticker` reorder + unqualified-skip; `_qualify_ticker`; `InstrumentId` TYPE_CHECKING import)
- `tests/unit/services/firstrate/test_instrument_mapper.py` (modified — `TestQualification`)
- `tests/unit/services/firstrate/test_import_service.py` (modified — `TestVenueQualification`, `_unresolved_metadata`)
- `tests/component/services/firstrate/test_instrument_mapper.py` (modified — `TestQualificationSync`; stale-DDL repair)
- `tests/component/services/firstrate/test_import_service.py` (modified — per-timeframe rewind repair in 3 pre-existing idempotency tests)
- `src/cli/commands/import_reporting.py` (modified — review patch: honest skipped-line reason)
- `tests/unit/cli/commands/test_import_reporting.py` (modified — review patch: progress-line reason tests)
