# Story 2.4: ETF Import — Parse, Convert & Write to Isolated Catalog

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to trigger an ETF import for a source directory that parses the bars, resolves instrument metadata, and writes Nautilus-compatible Parquet to the isolated catalog at all 5 timeframes,
so that the ETF bars land in the catalog alongside Stocks with their metadata resolved cache-first.

## Acceptance Criteria

1. **AC1 — Parse the 6-column schema and write Parquet to the isolated catalog.**
   Given the ETF source directory and isolated catalog path configured via `FirstRateSettings` /
   `CatalogSettings`, When the operator runs the ETF import CLI, Then the pipeline extracts each archive
   (Story 2.2 `zip_extractor`), parses the 6-column headerless schema with the reused `FirstRateCsvParser`,
   and writes bars as Parquet under `FIRSTRATE_CATALOG_PATH` following `{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet`
   (Nautilus leaf path `data/bar/{BAR_TYPE}/part-*.parquet`, where `BAR_TYPE` begins with the instrument id).

2. **AC2 — All 5 native timeframes import with no restriction.**
   Given the 5 native timeframes are present in the source, When the ETF import runs with no `--timeframe`
   restriction, Then all five (1min, 5min, 30min, 1hour, 1day) are imported.

3. **AC3 — `--timeframe` selects a subset.**
   Given the `--timeframe` selection option, When the operator specifies one or several timeframes
   (including `30min`), Then only the selected timeframe(s) are imported.

4. **AC4 — Cache-first metadata resolution per ticker.**
   Given each parsed ticker, When it is processed, Then `InstrumentMetadataService.resolve(ticker)` (Epic 1)
   is invoked and the resolved metadata is upserted into `instrument_metadata` (cache-first; no redundant FMP
   call when already `RESOLVED`). A metadata-resolution fault must not fail an otherwise-good bar import, and
   a `skipped` (already-complete) ticker triggers no resolution call.

5. **AC5 — Isolation + fidelity.**
   Given the isolated-catalog rule, When ETF Parquet is written, Then it is written only under the FirstRate
   catalog path (never mixed with IBKR/Kraken data), and decimal precision + UTC-normalized timestamps are
   preserved on round-trip (Phase 1 discipline).

## Tasks / Subtasks

- [x] **Task 1 — Wire `InstrumentMetadataService.resolve()` into the import loop (AC: #4)**
  - [x] Add an optional `metadata_resolver: InstrumentMetadataService | None = None` constructor dependency
        to `ImportService` (default `None` keeps the Phase-1 stocks path and all existing tests valid).
  - [x] In `_import_ticker`, after a successful parse (non-empty bars) and before the catalog write, call a
        new `_resolve_metadata(ticker)` helper that invokes `self._metadata_resolver.resolve(ticker)` only
        when a resolver is injected. Wrap the call in its own `try/except` so a metadata fault is logged and
        isolated — it never flips a good bar import to `failed` (the single `resolve()` does not isolate like
        `resolve_batch`). Placed after the `skipped` short-circuit so already-complete tickers never resolve.

- [x] **Task 2 — CLI timeframe surface: 30min + all-5 ETF default (AC: #2, #3)**
  - [x] Add `"30min": "30-MINUTE-LAST"` to `TIMEFRAME_MAP` in `src/cli/commands/import_data.py`
        (sourced from `ExplorerTimeframe.THIRTY_MIN.bar_type_spec`, the central enum).
  - [x] When the import path receives no `--timeframe` AND the asset class is ETF, default to all five native
        timeframes (1min, 5min, 30min, 1hour, 1day). The stocks path keeps its `daily` default and the
        dry-run path is unchanged.

- [x] **Task 3 — CLI wires the metadata resolver (AC: #4)**
  - [x] In `_run_import`, for the ETF path, construct an `InstrumentMetadataService` from the FMP provider
        (`FMPMetadataProvider` + `FMPClient`) and a `SyncInstrumentMetadataRepository` over the existing sync
        session, and pass it to `ImportService`. If FMP is unconfigured (no API key), warn and proceed with
        `metadata_resolver=None` (graceful offline degradation) — bars still import. Never make a network call
        at construction time.

- [x] **Task 4 — Tests with real temp fixtures + fakes (AC: #1, #2, #3, #4, #5)**
  - [x] Component (`tests/component/services/firstrate/test_import_service.py`): a real `FirstRateCsvParser`
        + real `CatalogManager` over a temp `FIRSTRATE_CATALOG_PATH`; import ≥2 ETF tickers and assert the
        on-disk `data/bar/{BAR_TYPE}/part-*.parquet` leaf layout (BAR_TYPE starts with the instrument id),
        round-trip OHLCV decimal precision + UTC `ts_init` fidelity, and that nothing is written outside the
        FirstRate catalog dir (AC1, AC5).
  - [x] Component: a fake/in-memory `InstrumentMetadataService` (real service + fake provider + in-memory
        store) — assert `resolve()` is called once per parsed ticker; a pre-seeded `RESOLVED` row is a cache
        hit (provider NOT called); an unseeded ticker hits the provider once and upserts; `skipped` tickers
        trigger no resolve; a resolver exception leaves the bar import `success` (AC4).
  - [x] Unit (`tests/unit/cli/commands/test_import_data.py`): `parse_timeframes("30min") == ["30-MINUTE-LAST"]`;
        the ETF no-`--timeframe` default expands to all five specs; the stocks default stays `["1-DAY-LAST"]`
        (AC2, AC3).

- [x] **Task 5 — Gates**
  - [x] `uv run ruff check .` clean; `uv run mypy .` clean (no NEW errors vs the documented baseline);
        `make test-unit` + `make test-component` green for the touched areas.

## Dev Notes

### Build ON the existing pipeline — do NOT rewrite it
The per-ticker import loop already parses → writes Parquet → verifies row count → verifies sample points →
upserts catalog-instrument metadata, with per-ticker error isolation and a Story-1-7 idempotent classifier:
- `src/services/firstrate/import_service.py` — `ImportService.import_directory` / `_import_ticker`. AC1/AC5's
  parse + isolated-catalog write + decimal/UTC fidelity are already implemented here (the parser normalizes ET→UTC
  and parses prices/volume through `Decimal`/`Price`/`Quantity`); this story **adds** the Epic-1 metadata-resolve
  call into that loop and proves the catalog-write ACs with a real round-trip test.
- `src/services/firstrate/parsers/firstrate_csv_parser.py` — the reused 6-column headerless parser (ETF + STOCK).
- `src/services/firstrate/catalog_manager.py` — resolves the named FirstRate catalog to a `ParquetDataCatalog`.
- `src/services/firstrate/zip_extractor.py` — Story 2.2 per-archive extraction (the documented extraction stage
  that yields the `.txt` files the import loop consumes; its handler-seam stays as-is).
- `src/services/metadata/instrument_metadata_service.py` — Epic 1 `resolve(ticker)`: cache-first, only a
  `RESOLVED` row short-circuits the provider; it owns the upsert into `instrument_metadata`.

### The metadata-resolve wiring (AC4)
`InstrumentMetadataService.resolve` is cache-first and self-upserting (`instrument_metadata_service.py:48-62`),
so the import loop only needs to **call** `resolve(ticker)` per parsed ticker — no separate upsert. Inject it
as an optional dependency (mirrors how `catalog_manager` / `metadata_service` / `instrument_mapper` are injected)
so the stocks path and existing tests — which pass no resolver — are unaffected. Isolate the call in its own
`try/except`: a single `resolve()` (unlike `resolve_batch`) does not swallow provider faults, and a metadata
hiccup must never fail a verified bar import. Place it after the `skipped` short-circuit so an already-complete
re-run makes no resolve call (sets up Story 2.6's "no FMP calls when nothing changed").

### Catalog layout & the test tier constraint
Nautilus writes bars to `data/bar/{BAR_TYPE}/part-*.parquet`, where `BAR_TYPE` is
`{INSTRUMENT_ID}-{step}-{agg}-{price}-EXTERNAL` (e.g. `SPY.ARCA-1-MINUTE-LAST-EXTERNAL`) — so the
`{INSTRUMENT_ID}/{BAR_TYPE}` layout in the AC is encoded in the leaf dir name. Real `ParquetDataCatalog`
write/read is exercised in a **component** test over a `tmp_path` catalog (no `BacktestEngine`, so no
`--forked` engine-state hazard); the assertion walks the catalog dir for the `bar` leaf and reads bars back to
check decimal precision + UTC `ts_init`. If a bare catalog write proves unsafe without `--forked` in this repo,
fall back to asserting the production `catalog.write_data(bars)` call carries bars whose `bar_type` stringifies
to the expected `{INSTRUMENT_ID}-{BAR_TYPE}` and keep the on-disk-layout assertion in an `--forked` integration
test — but prefer the real component round-trip per the TEA guidance.

### CLI timeframe surface (AC2/AC3)
`TIMEFRAME_MAP` is missing `30min` (Story 2.1 added the 30-minute convention via `ExplorerTimeframe.THIRTY_MIN`).
Add it. For the ETF import path with no `--timeframe`, default to all five native specs so AC2 holds; keep the
stocks default at `daily` and leave the dry-run path untouched (its STOCK default + `test_dry_run_never_constructs_services`
invariant must hold). FirstRate ships one timeframe per source layout, so the per-timeframe loop in `_run_import`
already drives the selection — this story only widens the default + adds the `30min` token.

### Out of scope (later stories / epics — do NOT implement)
- Verification surfacing in the summary beyond what exists (Story 2.5), date-range idempotency tuning (Story 2.6),
  and `ResolutionSummary` in the summary (Story 2.7).
- Venue resolution / Nautilus-qualified InstrumentId (Epic 3), explorer ETF work (Epic 4), backtests (Epic 5).
- Any DB schema change or Alembic migration — **this story needs none** (`instrument_metadata` exists from Epic 1).
- Any change to financial calculations or previously produced numbers.

### Project Structure Notes
- **Modified:** `src/services/firstrate/import_service.py` (optional resolver dep + `_resolve_metadata`),
  `src/cli/commands/import_data.py` (TIMEFRAME_MAP `30min`, ETF all-5 default, resolver wiring). Stay under size
  limits (files <500 lines, functions <50, classes <100, line <100).
- **Modified tests:** `tests/component/services/firstrate/test_import_service.py`,
  `tests/unit/cli/commands/test_import_data.py`.
- No new dependency, no migration, no Nautilus import added to a pure module.

### Testing standards
- **TDD** (CLAUDE.md / development-principles): failing test first (Red-Green-Refactor).
- **Tiers:** Component for the full import loop with the real parser/catalog + fakes; Unit for the CLI timeframe
  parsing/default math. No engine, no Postgres, no network. Commands: `make test-unit`, `make test-component`.
- **Real fixtures:** temp `.txt` trees in `tmp_path`; a temp FirstRate catalog dir; a fake `MetadataProvider`
  (counts calls, never network) behind a real `InstrumentMetadataService` over an in-memory store.
- **Import gate (F401/F821):** add imports and usages in the same edit.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.4] — parse 6-col schema, write isolated Parquet at
  all 5 timeframes, `--timeframe` subset, cache-first `InstrumentMetadataService.resolve`, isolation + fidelity (lines 479-505)
- [Source: harness-epic2-rest-spec.md#Story-2.4] — scoped ACs + TEA verification guidance (temp catalog layout,
  row fidelity, resolve cache-first with fake store, no network/DB/real import)
- [Source: src/services/firstrate/import_service.py] — existing parse→write→verify→upsert loop + idempotent classifier to extend
- [Source: src/services/metadata/instrument_metadata_service.py:48-62] — cache-first `resolve`: only RESOLVED short-circuits the provider; self-upserts
- [Source: src/services/metadata/providers/base.py] — `MetadataProvider` Protocol the fake provider satisfies structurally
- [Source: src/api/models/explorer.py:18-30] — `ExplorerTimeframe` central enum (30-MINUTE-LAST bar_type_spec)
- [Source: tests/component/api/test_chart_panel_routes.py:294] — catalog leaf path `…-1-DAY-LAST-EXTERNAL/part-0.parquet`
- [Source: _bmad-output/implementation-artifacts/2-2-per-archive-zip-extraction.md] — injected-seam precedent (extractor handler)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

### Completion Notes List

- **AC4 wiring:** `ImportService` gained an optional `metadata_resolver`
  (`InstrumentMetadataService | None`, default `None` → Phase-1 stocks path and
  all existing call sites unchanged). `_resolve_metadata(ticker)` is invoked in
  `_import_ticker` after a successful parse and after the `skipped` short-circuit,
  fault-isolated in its own `try/except` so a metadata hiccup never fails a
  verified bar import. The Epic-1 service owns cache-first + upsert.
- **AC2/AC3:** `30min` added to `TIMEFRAME_MAP` (sourced from
  `ExplorerTimeframe.THIRTY_MIN.bar_type_spec` to honor the Story-2.1 single-literal
  guard); `resolve_timeframes()` defaults the ETF path to all five native
  timeframes and lets an explicit `--timeframe` win. `_build_metadata_resolver()`
  wires the FMP-backed resolver for ETF only, degrading to `None` (with a warning)
  when FMP is unconfigured — no network at construction (lazy httpx client).
- **AC1/AC5:** proven by a real `FirstRateCsvParser` + real `ParquetDataCatalog`
  round-trip in a component test (no engine, so no `--forked` needed): on-disk
  `data/bar/{BAR_TYPE}/*.parquet` leaf layout, decimal precision + ET→UTC fidelity,
  and catalog isolation (only the `firstrate-etf` dir is written).
- **Code-review fixes (parallel Blind Hunter / Edge Case Hunter / Acceptance
  Auditor):**
  - *Timeframe-blind discovery (Edge #1, High):* the new all-5 default would have
    re-parsed every file under all 5 bar types because `_discover_tickers` ignored
    the timeframe. Added an optional timeframe filter that reuses the dry-run's
    `_infer_timeframe`; files with no recognizable timeframe token stay
    timeframe-agnostic (backward-compatible with the Phase-1 per-dir layout and
    untokenized test fixtures). `timeframe=None` disables filtering.
  - *Resolve multiplication (Blind #1 / Edge #3, Medium):* added an instance-level
    `_resolved_tickers` set so resolution runs at-most-once per ticker per run; the
    multi-timeframe ETF default no longer re-hits the provider for not-yet-RESOLVED
    tickers on each pass.
  - *Accepted (out of scope this story):* the resolver upserts via `flush` and
    commits with the whole batch at the end of `_run_import` — a fatal error rolls
    everything back, identical to the existing catalog-instrument upsert path. A
    per-ticker transactional boundary is a larger change deferred to later stories;
    rolled-back tickers are simply re-resolved cache-first on the next run.
- **Gates:** `ruff check .` clean; `mypy .` "Success: no issues found in 338
  source files"; `make test-unit` and `make test-component` green.

### File List

- `src/services/firstrate/import_service.py` (optional `metadata_resolver` +
  `_resolve_metadata` with per-run dedup; timeframe-filtered `_discover_tickers` +
  `_matches_timeframe`)
- `src/cli/commands/import_data.py` (`30min` token via `ExplorerTimeframe`,
  `ETF_DEFAULT_TIMEFRAMES`, `resolve_timeframes`, `_build_metadata_resolver`,
  resolver wired into `_run_import`)
- `tests/unit/services/firstrate/test_import_service.py` (timeframe-filter discovery tests)
- `tests/unit/cli/commands/test_import_data.py` (`30min` token, `TestResolveTimeframes`, `TestBuildMetadataResolver`)
- `tests/component/services/firstrate/test_import_service.py` (`TestEtfCatalogRoundTrip`, `TestMetadataResolveWiring` incl. per-run dedup)
- `_bmad-output/implementation-artifacts/2-4-etf-import-parse-convert-and-write-to-isolated-catalog.md` (this story)
