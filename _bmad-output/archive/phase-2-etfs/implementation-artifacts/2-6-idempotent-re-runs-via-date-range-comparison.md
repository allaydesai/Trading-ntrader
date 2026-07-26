# Story 2.6: Idempotent Re-runs via Date-Range Comparison

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want re-running the import to skip already-complete tickers and re-import only the incomplete ones,
so that recovery after interruption is cheap, repeatable, and free of duplicate data — and a re-run that
touches nothing rewrites no Parquet and makes no FMP calls.

## Acceptance Criteria

1. **AC1 — Date-range comparison re-imports only incomplete tickers (Phase 1 behavior preserved).**
   Given a prior partial import, When the same import command is re-run, Then date-range comparison identifies
   complete vs. incomplete tickers and re-imports only the incomplete ones.

2. **AC2 — Already-resolved tickers hit the FMP cache (near-100% cache hit).**
   Given already-resolved tickers, When the re-run processes them, Then the FMP cache prevents redundant
   metadata re-fetching — the provider (FMP) is not called again for a ticker already `RESOLVED`.

3. **AC3 — A re-run that touches nothing rewrites no Parquet and makes no FMP calls.**
   Given a re-run where all tickers are already complete, When the import runs, Then no Parquet is rewritten
   (no `catalog.write_data`) and no FMP calls are made (no provider `resolve`).

## Tasks / Subtasks

- [x] **Task 1 — Confirm the date-range classifier covers AC1 (build ON Phase 1, do NOT rewrite) (AC: #1)**
  - [x] The idempotent re-run classifier already exists: `ImportService._classify_ticker` decides
        `skipped` / `reimported` / `new` from the `CatalogInstrument` metadata row plus the cheap
        `source_probe.compute_source_last_date` last-date probe (ADR-5: metadata, not the filesystem, is the
        gatekeeper). Day-granularity comparison for `DAY` aggregation; full-datetime for intraday. No change to
        this logic — Story 2.6 preserves Phase 1 behavior for the ETF path and proves it with ETF-context tests.

- [x] **Task 2 — Confirm cache-first metadata resolution covers AC2 (build ON Story 2.4) (AC: #2)**
  - [x] `ImportService._resolve_metadata` delegates to the Epic-1 `InstrumentMetadataService.resolve`, which is
        cache-first (only a `RESOLVED` row short-circuits the provider) and self-upserts. The in-run
        `_resolved_tickers` set dedups across the per-timeframe passes; the DB-backed `RESOLVED` row dedups
        across separate runs. No change — Story 2.6 proves the cross-run cache hit with a counting provider.

- [x] **Task 3 — Confirm the skip short-circuit covers AC3 (AC: #3)**
  - [x] In `_import_ticker`, a `"skipped"` decision returns immediately — before `_resolve_metadata` (no
        provider call) and before `catalog.delete_data_range` / `catalog.write_data` (no Parquet rewrite). An
        all-complete re-run therefore does zero writes and zero FMP calls. No change — Story 2.6 proves the
        combined guarantee in one all-complete re-run with the real cache-first resolver.

- [x] **Task 4 — Tests mapping each AC to a passing test, fixtures/doubles only (AC: #1, #2, #3)**
  - [x] Component (`tests/component/services/firstrate/test_import_service.py`, new
        `TestStory26IdempotentRerun` class), reusing the existing in-memory doubles
        (`stateful_metadata_service`, `_FakeMetadataProvider`, `_FakeMetadataRepo`,
        `_install_parser_and_readback`, `_force_bar_end_to_2025_01_15`) — no real import, no network, no
        Postgres, no DB:
    - [x] **AC1:** run 1 imports two tickers; rewind one metadata row to a partial date-range while leaving the
          other complete; the re-run re-imports only the incomplete ticker (one more `write_data`) and skips the
          complete one.
    - [x] **AC2:** wire the **real** cache-first `InstrumentMetadataService` (counting `_FakeMetadataProvider` +
          in-memory `_FakeMetadataRepo`) across two `ImportService` instances sharing one resolver; force a
          re-import on run 2 (source advanced) so `resolve()` is actually invoked — and assert the provider is
          **not** called again (cache hit) for the already-`RESOLVED` ticker.
    - [x] **AC3:** with the real counting resolver, an all-complete re-run makes **zero** new `write_data` calls
          and **zero** new provider calls (no Parquet rewrite, no FMP).
  - [x] Existing coverage kept green and cross-referenced: `TestIdempotentRerun` (Phase-1 skip/reimport/orphan,
        AC1), `TestMetadataResolveWiring::test_cache_first_resolved_ticker_skips_provider` (AC2),
        `TestMetadataResolveWiring::test_skipped_ticker_does_not_resolve` (AC3), and the
        `TestClassifyTicker` unit suite (AC1 edge cases).

- [x] **Task 5 — Gates**
  - [x] `uv run ruff check .` clean; `uv run mypy .` (no NEW errors vs the documented baseline);
        `make test-unit` + `make test-component` green for the touched areas.

## Dev Notes

### Build ON the existing pipeline — do NOT rewrite it
Story 2.6 is idempotency that **already exists** for the Phase-1 stocks path and was wired through to the ETF
path in Stories 2.4–2.5. This story preserves that behavior for ETFs and proves all three ACs with
ETF-context, fixture-only tests. The three load-bearing pieces (none changed):

- `src/services/firstrate/import_service.py`
  - `_classify_ticker` — date-range comparison (`skipped` / `reimported` / `new`) from the metadata row +
    `compute_source_last_date`. **AC1.**
  - `_resolve_metadata` — cache-first resolution via the injected Epic-1 service, with the in-run
    `_resolved_tickers` dedup; faults isolated. **AC2.**
  - `_import_ticker` — the `"skipped"` decision returns before any `resolve()` / `delete_data_range` /
    `write_data`. **AC3.**
- `src/services/firstrate/source_probe.py` — `compute_source_last_date` (Nautilus-free, streams the file;
  ET→UTC parity with the parser) is the cheap last-date probe the classifier compares against.
- `src/services/metadata/instrument_metadata_service.py` — `resolve()` is cache-first (only a `RESOLVED` row
  short-circuits the provider) and self-upserts; this is the FMP-cache mechanism behind AC2/AC3.

### Why no code change and no migration
The classifier and cache-first resolver satisfy AC1–AC3 as written; `instrument_metadata` already exists
(Epic 1). Adding code purely to re-state existing behavior would violate the harness's "build ON existing,
do not rewrite" guardrail. The deliverable is the per-AC proving tests (verification guidance: every AC maps
to a passing fixture-based unit/component test).

### Out of scope (later stories / epics — do NOT implement)
- Import summary + `ResolutionSummary` line / progress reporting (Story 2.7).
- Venue resolution (Epic 3), explorer (Epic 4), backtests (Epic 5).
- Any DB schema change / Alembic migration (none needed), and any change to financial calculations or
  previously produced numbers.

### Project Structure Notes
- **Modified (tests only):** `tests/component/services/firstrate/test_import_service.py`
  (new `TestStory26IdempotentRerun` class). No production source changes.
- Stay under size limits; the test class reuses the file's existing fixtures and in-memory doubles.

### Testing standards
- **Tiers:** Component for the full `import_directory` re-run with the existing test doubles; the Phase-1
  classifier edge cases are already covered by unit tests. No engine, no Postgres, no network, no real import.
  Commands: `make test-unit`, `make test-component`.
- **Import gate (F401/F821):** add imports and usages in the same edit.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.6] — idempotent re-runs via date-range comparison;
  FMP cache prevents redundant re-fetch; a no-op re-run writes nothing (lines 531-549); FR16, NFR10, NFR14
- [Source: harness-epic2-rest-spec.md#Story-2.6] — scoped ACs + TEA verification guidance (fixtures only,
  no real import/network/DB)
- [Source: src/services/firstrate/import_service.py] — `_classify_ticker` (AC1), `_resolve_metadata` (AC2),
  `_import_ticker` skip short-circuit (AC3)
- [Source: src/services/firstrate/source_probe.py] — `compute_source_last_date` last-date probe (AC1)
- [Source: src/services/metadata/instrument_metadata_service.py:48-62] — cache-first `resolve()` (AC2/AC3)
- [Source: _bmad-output/planning-artifacts/architecture.md ADR-5] — metadata row (not the Parquet file) is the
  import gatekeeper for the classifier

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

### Completion Notes List

- **AC1:** `_classify_ticker` (date-range comparison, Phase-1) preserved for the ETF path; proven by a re-run
  that re-imports only the rewound/incomplete ticker and skips the complete one.
- **AC2:** the Epic-1 cache-first `InstrumentMetadataService.resolve` plus the `_resolved_tickers` in-run dedup;
  proven across two `ImportService` instances sharing one resolver — a forced re-import on run 2 invokes
  `resolve()` but the counting provider is NOT hit again for the already-`RESOLVED` ticker.
- **AC3:** the `"skipped"` short-circuit returns before any `resolve()` / `write_data`; proven by an
  all-complete re-run making zero new `write_data` and zero new provider calls.
- **No production change / no migration:** behavior already present from Phase 1 + Stories 2.4–2.5; this story
  adds the per-AC proving tests only.
- **Gates:** `ruff check .` clean; `mypy .` no new errors; `make test-unit` + `make test-component` green.

### File List

- `tests/component/services/firstrate/test_import_service.py` (new `TestStory26IdempotentRerun` class — AC1/AC2/AC3 proving tests)
- `_bmad-output/implementation-artifacts/2-6-idempotent-re-runs-via-date-range-comparison.md` (this story)
</content>
</invoke>
