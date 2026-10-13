# Story 2.3: Dry-Run Scan with FMP-Aware Estimate

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a dry-run that scans the ETF source archives and reports what would be imported without writing data — including per-ticker date ranges and an FMP-aware metadata-resolution estimate,
so that I can validate the source and estimate both disk **and metadata-resolution work** before committing to a full import.

## Acceptance Criteria

1. **AC1 — ETF scan reports counts, schema, timeframes, per-ticker date ranges, no writes.**
   Given a dry-run pointed at the ETF source directory (`--asset-class etf`), When it runs, Then it reports
   distinct ticker count, the detected 6-column schema (flagging mismatches), the timeframes present, and
   **per-ticker date ranges** (earliest / latest observed date per ticker), and writes nothing to the
   catalog or DB (the existing `"No data written — dry run only."` trailer still prints).

2. **AC2 — Estimated disk footprint.**
   Given the scan, When disk usage is estimated, Then an estimated Parquet disk footprint for the full
   import is reported for the ETF source (existing `estimate_parquet_bytes` / `estimated_parquet_bytes`,
   rendered via `format_bytes`).

3. **AC3 — FMP-aware metadata estimate.**
   Given the dry-run output, When it summarizes metadata work, Then it reports how many distinct tickers
   are **already cached/resolved** vs. how many **would require FMP resolution**, computed **offline** from
   locally-cached metadata only (no network / no provider call), via an injected read-only reader seam so
   `dry_run.py` stays pure. A ticker counts as "cached" iff the metadata store already holds a `RESOLVED`
   record for it (`ResolutionStatus.RESOLVED`); everything else counts as "needs resolution".

4. **AC4 — Tests prove the behavior, infra-light.**
   Given unit/component tests, When the new behavior is exercised, Then per-ticker date ranges and the
   cached-vs-needs-resolution split are each verified with **real temporary fixtures** and a **fake**
   metadata-cache reader (no network, no live data, no Postgres), and `ruff check .` + `mypy .` are clean.

## Tasks / Subtasks

- [x] **Task 1 — Extend the domain model (AC: #1, #3)**
  - [x] In `src/models/catalog.py`, add `TickerDateRange` (earliest/latest ISO date strings) and
        `MetadataEstimate` (`total_tickers`, `cached_count`, `needs_resolution_count`) Pydantic models.
  - [x] Add two optional fields to `DryRunReport`: `ticker_date_ranges: dict[str, TickerDateRange]`
        (default empty) and `metadata_estimate: Optional[MetadataEstimate]` (default `None`). Both default
        absent so the existing stocks dry-run and all current call sites/tests stay valid.
  - [x] Export the new models in `__all__`.

- [x] **Task 2 — Per-ticker date-range derivation in the pure module (AC: #1)**
  - [x] In `src/services/firstrate/dry_run.py`, add a cheap date-bound reader: read the **first non-blank
        line** (reuse `_read_first_nonblank_line`) and the **last non-blank line** (new `_read_last_nonblank_line`
        that seeks the file tail — no full parse). Extract the date as the first CSV field's date portion
        (token before any space, ISO `YYYY-MM-DD` sorts lexically).
  - [x] Aggregate per ticker across all its files/timeframes: `earliest = min(first dates)`,
        `latest = max(last dates)`. Return `dict[str, TickerDateRange]`.
  - [x] Keep `scan_firstrate_directory` **stat-only** (do NOT make it open files — the existing
        `test_scanner_never_opens_files` invariant must hold). Date reads happen only in `build_dry_run_report`.

- [x] **Task 3 — Injected read-only cache-reader seam + FMP-aware estimate (AC: #3)**
  - [x] In `dry_run.py`, define a `ResolvedTickerReader` `Protocol` with one read-only method:
        `resolved(tickers: Iterable[str]) -> set[str]` (returns the subset already `RESOLVED`). This is the
        Story-2.2-style injected seam — `dry_run.py` does NOT import nautilus or open a DB session.
  - [x] Extend `build_dry_run_report` with `cache_reader: ResolvedTickerReader | None = None` and
        `include_date_ranges: bool = False`. When `cache_reader` is provided, compute `MetadataEstimate`
        over the distinct scanned tickers (intersect the reader's result with the scanned set to stay safe).
        When `include_date_ranges` is true, attach `ticker_date_ranges`.

- [x] **Task 4 — CLI wires the real metadata-store reader (AC: #1, #2, #3)**
  - [x] In `src/cli/commands/import_data.py`, for the **ETF** dry-run path only, open a **read-only** sync
        DB session and build a real `ResolvedTickerReader` backed by `SyncInstrumentMetadataRepository` /
        the `instrument_metadata` ORM (single `SELECT ticker WHERE ticker IN (...) AND status=RESOLVED`).
        Never write. If the DB is unconfigured/unavailable, warn and proceed with the estimate omitted
        (graceful offline degradation) — the scan + date ranges + disk estimate still print.
  - [x] Surface both additions in `_print_dry_run_report`: a per-ticker date-range table (capped, like the
        schema-mismatch table) and an FMP-aware estimate line (cached vs needs-resolution). Keep the
        existing `"No data written — dry run only."` trailer unconditionally.
  - [x] Keep the stocks dry-run path unchanged (no date ranges, no DB session) so its existing tests and
        the `test_dry_run_never_constructs_services` invariant hold.

- [x] **Task 5 — Tests with real temp fixtures + fake reader (AC: #1, #2, #3, #4)**
  - [x] Unit (`tests/unit/services/firstrate/test_dry_run.py`): build a small temp ETF tree (≥2 tickers, ≥1
        timeframe, 6-col rows with distinct first/last dates) and assert distinct ticker count, timeframe
        grouping, 6-col schema handling, correct per-ticker earliest/latest (AC1); non-zero
        `estimated_parquet_bytes` (AC2); a **fake** `ResolvedTickerReader` marking some tickers RESOLVED and
        the rest unknown → assert `cached_count` / `needs_resolution_count` match and the fake records it was
        never asked to hit a network/provider (AC3). Reuse `test_module_does_not_import_nautilus` coverage.
  - [x] Unit/CLI (`tests/unit/cli/commands/test_import_data.py`): assert the ETF dry-run path passes a
        cache reader + `include_date_ranges=True` into `build_dry_run_report`, that the stocks path does not,
        and that `_print_dry_run_report` renders the date-range + estimate and still prints the trailer.

- [x] **Task 6 — Gates**
  - [x] `uv run ruff check .` clean; `uv run mypy .` clean (no NEW errors vs the documented baseline);
        `make test-unit` + `make test-component` green for the touched areas.

## Dev Notes

### Build ON the existing dry-run — do NOT rewrite it
A pure-Python, read-only dry-run scanner already exists from Story 1.6:
- `src/services/firstrate/dry_run.py` — `scan_firstrate_directory`, `validate_schema_sample`,
  `estimate_parquet_bytes`, `build_dry_run_report`, `format_bytes`, plus the internal `_walk` / `_ScanState`
  / `_Bucket` / `_read_first_nonblank_line` / `_extract_ticker` / `_infer_timeframe` helpers. **It MUST NOT
  import `nautilus_trader` or open a DB session** (module docstring + `test_module_does_not_import_nautilus`).
- `src/models/catalog.py` — `DryRunReport`, `TimeframeSummary`, `SchemaMismatch` already carry per-timeframe
  ticker/file/byte counts, `distinct_ticker_count`, `total_source_bytes`, `estimated_parquet_bytes`, and the
  6-column schema-mismatch detection. AC1's counts/schema/timeframes and AC2's disk estimate are already DONE
  for the stocks path; this story extends the same machinery to the ETF source and adds the two new pieces.
- `src/cli/commands/import_data.py` — `import_firstrate` with `--dry-run/--no-dry-run`, `_run_dry_run`,
  `_print_dry_run_report`. `--asset-class etf` already maps via `ASSET_CLASS_MAP`.

### The injected-seam pattern (mirror Story 2.2)
Story 2.2's `ZipExtractor` kept itself Nautilus-free by accepting an **injected** parser hand-off callback
(`handler`) and an injected resume predicate (`is_complete`); tests passed stubs, the CLI/import wiring
supplies the real ones. Mirror that here: the FMP-aware estimate needs cached metadata state, but `dry_run.py`
is contractually DB-free / nautilus-free, so the cache lookup comes in through an injected read-only
`ResolvedTickerReader` Protocol. `dry_run.py` stays pure; `import_data.py` wires the real metadata-store reader
(read-only); tests pass a fake reader. This is the intended resolution of "the one decision" in the spec —
proceed on it (no escalation needed; no DB session in `dry_run.py`, no schema change, no provider/network call).

### Cached vs needs-resolution semantics (cache-first, RESOLVED-only)
`InstrumentMetadataService.resolve` short-circuits the provider **only** for a `ResolutionStatus.RESOLVED`
row (`instrument_metadata_service.py:48-56`); `UNRESOLVED` / `VENUE_UNRESOLVED` rows are intentionally
re-attempted. So the offline estimate must classify a ticker as "cached" **iff** it has a `RESOLVED` record,
and "needs resolution" otherwise (missing row, `UNRESOLVED`, or `VENUE_UNRESOLVED`). This matches what a real
import would actually skip vs re-fetch.

### Date-range derivation must stay cheap (no full parse, no nautilus, no DB)
FirstRate rows are `Datetime,Open,High,Low,Close,Volume`, ascending by datetime, e.g.
`2024-01-02 09:30:00,100.0,101.0,99.5,100.5,1000` (intraday) or `2024-01-02,...` (daily). Earliest = date of
the first data line; latest = date of the last data line. Read the first non-blank line (reuse the existing
helper) and the last non-blank line via a tail seek (read the final ~64 KiB and take the last non-blank line —
the file ends after a complete line, so the trailing line is never partial). Take the first comma field, then
the token before any space → ISO date string (lexical sort == chronological). No CSV/Nautilus parsing.

### Keep the stocks path & invariants intact
- `scan_firstrate_directory` stays stat-only (`test_scanner_never_opens_files`). New file reads live in
  `build_dry_run_report` only, gated by `include_date_ranges` / `cache_reader` (off for stocks).
- The stocks dry-run path opens **no** DB session (`test_dry_run_never_constructs_services`, STOCK default).
  Only the ETF path opens a read-only session, and only to read.
- New `DryRunReport` fields default absent so every existing `DryRunReport(...)` construction stays valid.

### Out of scope (later stories / epics — do NOT implement)
- The actual import / parse / Parquet conversion / catalog write (Story 2.4).
- Real FMP resolution, venue resolution, any network/provider call (Story 2.5+ / metadata epic).
  Idempotency, verification, explorer work (2.6–2.7).
- Any DB schema change or Alembic migration — **this story needs none**.
- Any change to financial calculations or previously produced numbers.

### Project Structure Notes
- **Modified:** `src/services/firstrate/dry_run.py` (architecture source-tree line 487: *"dry_run.py —
  MODIFIED: _30min_ token, ETF, FMP-aware estimate"*), `src/models/catalog.py`,
  `src/cli/commands/import_data.py`. Stay under size limits (files <500 lines, functions <50, line <100).
- **Modified tests:** `tests/unit/services/firstrate/test_dry_run.py`,
  `tests/unit/cli/commands/test_import_data.py`.
- No new dependency, no migration, no Nautilus import in `dry_run.py`.

### Testing standards
- **TDD** (CLAUDE.md / development-principles): failing test first (Red-Green-Refactor).
- **Tiers:** Unit for pure logic (date-range derivation, estimate math, fake reader); CLI unit for the wiring
  + render. No integration `--forked` (no Nautilus, no engine). Commands: `make test-unit`, `make test-component`.
- **Real fixtures:** build temp `.txt` trees in `tmp_path`; fake `ResolvedTickerReader` is an in-memory stub
  that asserts it never performs network/provider I/O. No committed binary fixtures, no Postgres.
- **Import gate (F401/F821):** add imports and usages in the same edit; keep `dry_run.py` Nautilus-free.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.3] — AC: ticker counts (~5,039), 6-col schema,
  timeframes, per-ticker date ranges, disk estimate, FMP-aware cached-vs-needs-resolution estimate (lines 459-477)
- [Source: harness-story-2.3-spec.md] — scoped ACs, injected read-only reader seam, offline/read-only constraint,
  fake-reader test guidance, the one escalation decision (already resolved via the seam)
- [Source: _bmad-output/planning-artifacts/architecture.md] — `dry_run.py` "MODIFIED: ETF, FMP-aware estimate"
  (line 487); E2 dry-run with FMP-aware estimate (line 37); reliability via date-range skip + cache hit (line 66)
- [Source: src/services/firstrate/dry_run.py] — existing pure scanner/validator/estimator to extend (purity
  invariant in docstring; `_read_first_nonblank_line`, `_extract_ticker`, `_walk`/`_ScanState`)
- [Source: src/services/metadata/instrument_metadata_service.py:48-56] — cache-first resolve: only RESOLVED
  short-circuits the provider (defines "cached" for the estimate)
- [Source: src/models/instrument_metadata.py:36-46] — `ResolutionStatus` enum (RESOLVED / UNRESOLVED /
  VENUE_UNRESOLVED)
- [Source: src/db/repositories/instrument_metadata_repository_sync.py] — sync repo / ORM the real CLI reader
  queries (read-only)
- [Source: _bmad-output/implementation-artifacts/2-2-per-archive-zip-extraction.md] — injected-seam precedent
  (handler / is_complete callbacks; pure stage + CLI wires the real one; fake in tests)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

### Completion Notes List

- Domain models (`TickerDateRange`, `MetadataEstimate`) and the two optional
  `DryRunReport` fields were already present; the pure derivation logic
  (`_read_last_nonblank_line`, `_date_from_line`, `_derive_date_ranges`,
  `_estimate_metadata`) and the injected `ResolvedTickerReader` seam were also
  already implemented in `dry_run.py`.
- This run completed **Task 4 (CLI wiring)** and **Task 5 (tests)**, which were
  missing: the ETF dry-run path now opens a read-only sync session, builds a
  real `_MetadataStoreReader` (single indexed `SELECT ticker WHERE status =
  RESOLVED`, never writes), and passes `cache_reader` + `include_date_ranges=True`
  into `build_dry_run_report`. The stocks path stays DB-free / date-range-free.
- Graceful offline degradation: if the DB is unconfigured (`get_sync_session_maker`
  → None) or unavailable (`OperationalError` / `DatabaseConnectionError` mid-query),
  the scan + date ranges + disk estimate still print and the metadata estimate is
  omitted with a warning; exit code stays 0.
- `_print_dry_run_report` now renders a capped per-ticker date-range table and an
  FMP-aware estimate line, keeping the unconditional `"No data written — dry run
  only."` trailer.
- Gates: `ruff check .` clean, `mypy .` clean (no new errors — full run reports
  "Success"), `make test-unit` (1086) and `make test-component` (684) green.

### File List

- `src/services/firstrate/dry_run.py` (pre-existing Story 2.3 logic; unchanged this run)
- `src/models/catalog.py` (pre-existing Story 2.3 models; unchanged this run)
- `src/cli/commands/import_data.py` (ETF wiring, reader, rendering, degradation)
- `tests/unit/services/firstrate/test_dry_run.py` (date-range + FMP-estimate + fake reader tests)
- `tests/unit/cli/commands/test_import_data.py` (ETF wiring, degradation, render tests)
