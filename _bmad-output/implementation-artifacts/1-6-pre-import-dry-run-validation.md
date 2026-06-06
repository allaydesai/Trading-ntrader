# Story 1.6: Pre-Import Dry-Run Validation

Status: done

## Story

As a system operator,
I want to run a dry-run that reports what would be imported without writing any data,
so that I can verify the source directory structure and estimate disk usage before committing.

## Acceptance Criteria

1. **AC-1: Dry-Run Flag Triggers Scan-Only Mode** — Given a source directory with FirstRate Stocks data (the Phase 1 target), when the user runs `ntrader import --format firstrate --catalog <name> <source-path> --dry-run`, then the system scans the directory structure without writing any data to the Parquet catalog or the `catalog_instruments` database table.

2. **AC-2: Dry-Run Summary Report** — Given a dry-run scan completes, when the results are reported, then the output includes all of: detected asset classes, ticker count per asset class, available timeframes per asset class, total file count, and estimated disk usage for the resulting Parquet output.

3. **AC-3: Schema Mismatch Detection** — Given a source directory containing `.txt` files that do not match the expected FirstRate 6-column headerless CSV schema, when the dry-run scans those files, then the mismatches are reported with file name and detected vs. expected column counts (expected = 6).

4. **AC-4: Zero Side Effects and Success Exit Code** — Given a dry-run scan, when it completes (with or without schema mismatches), then no Parquet files are written, no `catalog_instruments` rows are created or modified, and the CLI exits with code 0.

## Tasks / Subtasks

- [x] **Task 1: Add `--dry-run` flag to `import` command** (AC: 1, 4)
  - [x] 1.1 Write failing unit test asserting `--dry-run` is accepted by `import_firstrate` and routes to a new dry-run code path (not `_run_import`).
  - [x] 1.2 Add `--dry-run/--no-dry-run` Click option to `import_firstrate` in `src/cli/commands/import_data.py` (default False).
  - [x] 1.3 Branch in `import_firstrate`: if `dry_run` is True call `_run_dry_run(...)` and exit with its return code; otherwise call existing `_run_import(...)`.
  - [x] 1.4 Unit test: invoking with `--dry-run` never constructs `ImportService` / `CatalogManager` / DB session (patch both and assert not called).

- [x] **Task 2: Implement directory scanner** (AC: 1, 2)
  - [x] 2.1 Write failing unit tests for `scan_firstrate_directory(source_path, asset_class)` helper:
    - Fixture with alphabetical subdirs (`A/`, `S/`) containing `SPY_full_1day_adjsplitdiv.txt`, `AAPL_full_1day_adjsplitdiv.txt` returns one timeframe group (`1-DAY-LAST`) with ticker count 2 and file count 2.
    - Fixture with mixed `*_1day_*.txt` and `*_1hour_*.txt` files returns two timeframe groups.
    - Fixture pointing one level up at `Stocks/` with subdirs `Stocks_1day/A/...` and `Stocks_1hour/A/...` walks recursively and groups correctly.
    - Empty directory returns an empty result (no error).
    - Directory entries that are not directories or not `.txt` files are skipped (no crash).
  - [x] 2.2 Implement `scan_firstrate_directory` in new module `src/services/firstrate/dry_run.py`. Walk `source_path` recursively to find every `.txt` file; for each file extract ticker via the same filename pattern as `ImportService._extract_ticker` and infer timeframe from the filename suffix (`_1day_` → `1-DAY-LAST`, `_1hour_` → `1-HOUR-LAST`, `_1min_` → `1-MINUTE-LAST`, `_5min_` → `5-MINUTE-LAST`). Unknown suffixes are bucketed into an `unknown` group and flagged.
  - [x] 2.3 Return a Pydantic `DryRunReport` (see Task 4) with: `asset_class`, mapping `timeframe_spec → {ticker_count, file_count, total_source_bytes, files: list[Path]}`, plus `total_file_count`.
  - [x] 2.4 Unit test: scanner never opens files for reading and never touches the DB (only `Path.iterdir`, `stat`, and name parsing).

- [x] **Task 3: Implement schema validator** (AC: 3)
  - [x] 3.1 Write failing unit tests for `validate_schema_sample(files, sample_size=5)`:
    - Files with exactly 6 comma-separated columns on first non-empty line → no mismatches.
    - Files with 5 or 7 columns → mismatch reported with file name and detected column count (6 expected).
    - Empty file → mismatch reported as `detected=0`.
    - Blank lines and CRLF endings before the first data row are tolerated (matches `FirstRateCsvParser._read_lines` behavior).
    - Only the first `sample_size` files per timeframe group are inspected (full-scan is too expensive on 387 GB).
  - [x] 3.2 Implement `validate_schema_sample` in `src/services/firstrate/dry_run.py`. Read only the first non-empty line of each sampled file using the same CRLF/blank-line handling as `FirstRateCsvParser._read_lines`; do not construct Bar objects and do not import Nautilus at all.
  - [x] 3.3 Return a list of `SchemaMismatch` records: `file_path: Path`, `expected_columns: int = 6`, `detected_columns: int`.
  - [x] 3.4 Unit test: validator runs without any `nautilus_trader` import (use `importlib.util` check or patch).

- [x] **Task 4: Add domain models for dry-run output** (AC: 2, 3)
  - [x] 4.1 Write failing unit test that `SchemaMismatch`, `TimeframeSummary`, and `DryRunReport` Pydantic models are importable from `src/models/catalog.py` with the expected fields and validation rules.
  - [x] 4.2 Add `SchemaMismatch`, `TimeframeSummary`, and `DryRunReport` Pydantic models to `src/models/catalog.py` next to `ValidationResult` and `ImportResult`. Follow the same `BaseModel`/`Field` style used for existing models.
  - [x] 4.3 `DryRunReport` fields: `asset_class: AssetClass`, `source_path: str`, `timeframes: dict[str, TimeframeSummary]`, `total_file_count: int`, `total_source_bytes: int`, `estimated_parquet_bytes: int`, `schema_mismatches: list[SchemaMismatch]`.
  - [x] 4.4 `TimeframeSummary` fields: `ticker_count: int`, `file_count: int`, `source_bytes: int`.

- [x] **Task 5: Disk usage estimator** (AC: 2)
  - [x] 5.1 Write failing unit test for `estimate_parquet_bytes(source_bytes)`: asserts the returned estimate is a deterministic fraction of source (document the ratio as `~0.35` in the function docstring; tune with one real FirstRate Stocks sample if available in the dev fixture).
  - [x] 5.2 Implement `estimate_parquet_bytes` in `src/services/firstrate/dry_run.py` as `int(source_bytes * PARQUET_COMPRESSION_RATIO)` with `PARQUET_COMPRESSION_RATIO = 0.35` module constant. Include a comment referencing the existing `~128 bytes/row` estimate in `src/services/data_catalog.py:313-315` for context.
  - [x] 5.3 Unit test: the report's `estimated_parquet_bytes` equals `PARQUET_COMPRESSION_RATIO * total_source_bytes` within int-rounding tolerance.

- [x] **Task 6: CLI dry-run orchestration + Rich output** (AC: 1, 2, 3, 4)
  - [x] 6.1 Write failing unit test for `_run_dry_run(format_name, catalog, source_path, asset_class)`:
    - Returns exit code 0 on happy path.
    - Returns exit code 0 even when `schema_mismatches` is non-empty (mismatches are reported, not fatal — AC-4).
    - Returns exit code 2 if `source_path` does not exist (Click's `exists=True` normally catches this first, but guard anyway for programmatic callers).
    - Never instantiates `ImportService`, `CatalogManager`, `MetadataService`, `InstrumentMapper`, or DB sessions (patch each and assert `not_called`).
  - [x] 6.2 Implement `_run_dry_run` in `src/cli/commands/import_data.py`:
    1. Resolve `AssetClass` using the same `ASSET_CLASS_MAP` + default logic as `_run_import` (default `STOCK` for Phase 1, not ETF — align with the 2026-04-11 Phase 1 pivot documented in `_bmad-output/planning-artifacts/prd.md`).
    2. Call `scan_firstrate_directory`, then `validate_schema_sample` on each timeframe's file list, then `estimate_parquet_bytes`.
    3. Assemble `DryRunReport` and pass to `_print_dry_run_report`.
  - [x] 6.3 Implement `_print_dry_run_report(report)` using `rich.table.Table` (consistent with `_print_summary`):
    - Header `Dry-Run Report — {asset_class} @ {source_path}`.
    - Main table with columns: Timeframe | Tickers | Files | Source Size | Est. Parquet Size. One row per timeframe plus a TOTAL row.
    - If `schema_mismatches` is non-empty, a second `Schema Mismatches` table with columns File | Expected | Detected; limit to first 20 rows and print `... and N more` if truncated.
    - Trailing `click.echo("No data written — dry run only.")` for explicit confirmation.
  - [x] 6.4 Wire branch in `import_firstrate`: when `dry_run=True`, call `_run_dry_run` and use `click.get_current_context().exit(code)` only if `code != 0` (same pattern as `_run_import`).

- [x] **Task 7: Component tests** (AC: 1, 2, 3, 4)
  - [x] 7.1 Component test: full CLI invocation via `CliRunner` with `--dry-run` on a `tmp_path` fixture that creates `A/AAPL_full_1day_adjsplitdiv.txt` and `S/SPY_full_1day_adjsplitdiv.txt` (6 valid columns each). Assert exit code 0, assert output contains `Dry-Run Report`, `STOCK`, `1-DAY-LAST`, `Tickers 2`, `Files 2`, `No data written`.
  - [x] 7.2 Component test: fixture with one file that has 5 columns. Assert exit code 0, assert `Schema Mismatches` section appears and lists the bad file with `detected=5 expected=6`.
  - [x] 7.3 Component test: patch `CatalogManager`, `ImportService`, `MetadataService`, `InstrumentMapper`, and `get_sync_session_maker`; assert none are constructed during a `--dry-run` invocation (proves zero side effects per AC-4).
  - [x] 7.4 Component test: fixture with both `_1day_` and `_1hour_` files. Assert report lists two timeframe rows plus a TOTAL row with aggregated file count.

## Dev Notes

### CLI Placement: Extend Existing `import` Command

The `--dry-run` flag is a mode of the existing top-level `import` command registered in `src/cli/main.py`. Do **not** create a new `ntrader dry-run` subcommand and do **not** create a new file — extend `src/cli/commands/import_data.py` in place. The architecture spec (`_bmad-output/planning-artifacts/architecture.md:354`) explicitly documents the flag on the existing command:

```bash
ntrader import --format firstrate --catalog <name> <source-path> [--asset-class <type>] [--dry-run] [--timeframe <list>]
```

Story 1-5's deferred-work entry D4 confirms `--dry-run` is the planned follow-up for this story (`_bmad-output/implementation-artifacts/deferred-work.md:24`).

### Zero Side Effects — The Cardinal Rule

AC-4 is the most load-bearing requirement. The dry-run code path must NOT:

- Construct `ImportService`, `CatalogManager`, `MetadataService`, or `InstrumentMapper`.
- Open a DB session via `get_sync_session_maker()`.
- Touch `ParquetDataCatalog` or call `catalog.write_data(...)`.
- Import any `nautilus_trader` module (defer-import rule — see "Nautilus C Extension Isolation" below).
- Call `InstrumentMapper.load_company_profiles(...)` (that writes rows to `catalog_instruments`).

Task 6.1 and 7.3 enforce this via patching assertions. A dry-run should not even require a working database connection.

### Nautilus C Extension Isolation

`FirstRateCsvParser` and `ImportService` both import from `nautilus_trader.model.*`. Importing them pulls the C/Rust extension into process state and risks the LogGuard double-init gotcha documented in `CLAUDE.md`. The dry-run modules (`src/services/firstrate/dry_run.py`) MUST be pure Python — no Nautilus imports at all.

Consequence: schema validation in Task 3 cannot reuse `FirstRateCsvParser._parse_line` directly (that method is a staticmethod on a class whose module imports `nautilus_trader`). Reimplement the minimal "split on comma, expect 6 columns" check inline in `dry_run.py`. Match the blank-line / CRLF behavior of `FirstRateCsvParser._read_lines` (`src/services/firstrate/parsers/firstrate_csv_parser.py:88-97`) so mismatches are consistent with what the real import would see.

### Directory Scanning Strategy

FirstRate Stocks layout on disk (from `_find_profiles_csv` docstring and existing tests):

```
/path/to/Stocks/
├── company_profiles.csv
├── Stocks_1day/
│   ├── A/ AAPL_full_1day_adjsplitdiv.txt
│   └── S/ SPY_full_1day_adjsplitdiv.txt
└── Stocks_1hour/
    └── A/ AAPL_full_1hour_adjsplitdiv.txt
```

The `import` command's `source_path` today is a single timeframe directory (e.g., `Stocks_1day`), as shown in `tests/unit/cli/commands/test_import_data.py:44,58`. The dry-run scanner must handle **both**:

- `source_path = Stocks_1day` (alphabetical subdirs directly inside) → one timeframe group.
- `source_path = Stocks` (timeframe subdirs inside, alphabetical inside those) → multiple timeframe groups.

Simplest correct approach: recursive `Path.rglob("*.txt")`, then group each file by its inferred timeframe suffix. This handles both layouts in one pass and is robust to additional nesting.

**Timeframe inference from filename (authoritative — do not infer from directory name):**

```python
# Map filename suffix → Nautilus timeframe spec (reuse TIMEFRAME_MAP spec format)
_FILENAME_TIMEFRAME_MAP = {
    "_1day_": "1-DAY-LAST",
    "_1hour_": "1-HOUR-LAST",
    "_1min_": "1-MINUTE-LAST",
    "_5min_": "5-MINUTE-LAST",
}
```

Ticker extraction: reuse the exact pattern from `ImportService._extract_ticker` in `src/services/firstrate/import_service.py:246-262` — split on `_full_`, take the prefix. Do NOT duplicate this as a copy-paste; import it as a staticmethod call if cheap, or (preferred, since `import_service` pulls in Nautilus) re-derive it in `dry_run.py` with an identical docstring reference so future maintainers see the link.

### Asset Class Handling — Default to STOCK (Phase 1 Pivot)

Phase 1 target pivoted from ETF → STOCK on 2026-04-11 (see `_bmad-output/planning-artifacts/prd.md` MVP Strategy section and commit `3028473`). The existing `_run_import` still defaults `--asset-class` to `etf` on line 206 of `src/cli/commands/import_data.py`. **Do not change that default in this story** — the non-dry-run path is already exercised by running tests + committed usage. For `_run_dry_run`, default to `STOCK` instead, and document the divergence in the function docstring with a pointer to Phase 1 follow-up cleanup (or optionally add a note to `deferred-work.md`).

Rationale: the dry-run is a greenfield code path. Defaulting it to STOCK aligns with the current Phase 1 target and avoids a confusing user experience where `--dry-run` reports "ETF" by default on a Stocks directory.

### Disk Usage Estimation

Use a **compression-ratio multiplier on source CSV bytes**, not row counting. Rationale:

1. Reading every source file to count rows defeats the "fast dry-run" purpose on 387 GB.
2. `Path.stat().st_size` is free (stat-only; no I/O past metadata).
3. Nautilus Parquet writes OHLCV + timestamp columns with snappy/zstd compression. Empirical ratio from test imports is roughly 0.30–0.40 of source CSV size. Use `PARQUET_COMPRESSION_RATIO = 0.35` as the initial constant; document it as "tune against one FirstRate Stocks sample" in the docstring.

The existing `src/services/data_catalog.py:313-315` uses `file_size // 128` (~128 bytes/row) for an **inverse** estimate (parquet→rows). That's a useful sanity check but not directly applicable here.

### Rich Output Format

Follow the existing `_print_summary` pattern in `src/cli/commands/import_data.py:142-171`:

- Use module-level `console = Console()`.
- `rich.table.Table` with cyan header column and green value column.
- Render a byte-count helper (e.g., `humanize.naturalsize(n, binary=True)` if `humanize` is already a dependency, else format as `f"{n / 1024**3:.2f} GiB"`).
- Truncate `Schema Mismatches` table at 20 rows with `... and N more` footer to avoid terminal spam on large failures.

Verify whether `humanize` is in `pyproject.toml` before using it; if not, write a 4-line `_format_bytes(n)` helper in `dry_run.py` rather than adding a dependency.

### Schema Check — Keep It Cheap

Only sample the **first 5 files per timeframe group** (Task 3, `sample_size=5`). Reading the first non-empty line of 5 files per group is essentially free; reading all files on a 387 GB dataset is not. Users who want exhaustive schema validation run the real import and rely on the parser's row-level skipping (`FirstRateCsvParser._parse_line` logs `skipping_malformed_row`).

If sampled files pass but real import still fails on a non-sampled file, that's acceptable — the dry-run is a smoke test, not a formal verifier. State this explicitly in the function docstring.

### Exit Code Discipline

Per AC-4, the dry-run exits 0 even when schema mismatches are reported. Mismatches are **informational**, not fatal. Reserve exit code 2 for truly fatal errors (`source_path` not a directory, catastrophic OS errors). Never use exit code 1 from a dry-run — 1 is reserved for "real import had some ticker failures", and semantically that cannot happen when nothing is imported.

Use `click.get_current_context().exit(code)` only for `code != 0`, matching the `_run_import` pattern at `src/cli/commands/import_data.py:331-333`.

### Anti-Patterns to Avoid

- ❌ Do NOT import `nautilus_trader` anywhere in `src/services/firstrate/dry_run.py` or in the dry-run code path of `import_data.py`. Use deferred imports only on the real-import path.
- ❌ Do NOT reuse `ImportService._discover_tickers` — it lives on a class whose module imports Nautilus. Reimplement discovery in `dry_run.py`.
- ❌ Do NOT call `_find_profiles_csv` and do NOT auto-load profiles in the dry-run path. Profiles loading writes to the DB; dry-run must be read-only on the filesystem and touch nothing else.
- ❌ Do NOT open a DB session. No `get_sync_session_maker`, no `SyncCatalogInstrumentRepository`, no `MetadataService`.
- ❌ Do NOT emit structlog warnings for schema mismatches. They go into the Rich report only; structlog is for the real import path.
- ❌ Do NOT use exit code 1 for any dry-run outcome. AC-4 mandates 0; reserve 2 for fatal path errors.
- ❌ Do NOT read full file contents for the schema sample — `_read_first_nonblank_line` only (open + `readline()` loop until non-blank or EOF).
- ❌ Do NOT add a new top-level Click command like `ntrader dry-run`. The architecture says `--dry-run` is a flag on `import`.

### Previous Story Intelligence

**From Story 1-5 (`_bmad-output/implementation-artifacts/1-5-cli-import-command-with-progress-and-summary.md`):**

- `import_firstrate` command lives in `src/cli/commands/import_data.py`, registered in `src/cli/main.py`. Extend this file — do not create a new one.
- `_run_import` uses **deferred imports** at function-body scope (line 193-201) to avoid Nautilus C extension init at module load. Follow the same pattern: any Nautilus-touching imports in `_run_dry_run` (there should be zero) must be deferred.
- CLI output uses `click.echo` for streaming and `rich.table.Table` for tabular output. Reuse the same `console = Console()` module-level instance.
- Exit codes follow: 0 (happy), 1 (some ticker failures — import path only), 2 (fatal). `click.get_current_context().exit(code)` only on non-zero.
- Session commit was a late fix (commit `85bafaa`) — the dry-run path never opens a session, so this concern doesn't apply, but it underscores how touchy the session lifecycle is. Another reason to stay far away from sessions in the dry-run path.
- Review finding D3 (broad `except Exception` with generic message) applies here too — reuse the same pattern for consistency.

**From Story 1-4 (`_bmad-output/implementation-artifacts/1-4-import-pipeline-core.md`, via deferred-work D1):**

- `ImportService._discover_tickers` has unguarded `subdir.iterdir()` at line 238 — permission errors crash the batch. The dry-run scanner should wrap `iterdir` in try/except and log (but not fail) on `PermissionError` / `OSError`. Track unreadable directories in the report as a new `unreadable_paths: list[Path]` field if convenient, or skip silently with a structured log line (prefer silent skip — AC-2 doesn't require it).

**From Story 1-2 (`FirstRateCsvParser`):**

- Files may have leading blank lines, CRLF endings, and empty files must be tolerated (`_read_lines` at `src/services/firstrate/parsers/firstrate_csv_parser.py:88-97`).
- 6-column headerless schema: `Datetime, Open, High, Low, Close, Volume`. This is the spec Task 3 validates against.

### Git Intelligence

Recent commits are low-churn and focused (`feat(<scope>): <subject> (Story X-Y)`). Story 1-5's commit `88950d1` added ~320 lines to `import_data.py` and ~500 lines of tests across unit + component suites. Expect this story to add ~200 lines to `import_data.py`, a new ~150-line `src/services/firstrate/dry_run.py`, and ~300 lines of tests. No migrations, no new dependencies (unless `humanize` is elected — prefer not).

### Project Structure Notes

- `src/cli/commands/import_data.py` — **MODIFY** (add `--dry-run` flag, `_run_dry_run`, `_print_dry_run_report`).
- `src/services/firstrate/dry_run.py` — **NEW** (scanner, validator, estimator; pure Python, no Nautilus imports).
- `src/models/catalog.py` — **MODIFY** (add `SchemaMismatch`, `TimeframeSummary`, `DryRunReport` models next to existing `ValidationResult` / `ImportResult`).
- `tests/unit/cli/commands/test_import_data.py` — **MODIFY** (add `TestDryRunFlag`, `TestRunDryRun` test classes).
- `tests/unit/services/firstrate/test_dry_run.py` — **NEW** (scanner, validator, estimator unit tests).
- `tests/component/cli/commands/test_import_data.py` — **MODIFY** (add `TestDryRunInvocation` test class covering AC-1 through AC-4).
- `src/cli/main.py` — **NO CHANGE** (command is already registered).
- `alembic/versions/` — **NO CHANGE** (no schema changes).
- `pyproject.toml` — **NO CHANGE** unless `humanize` is elected (prefer not; write a 4-line formatter instead).

### Testing Strategy

**Unit tests (`@pytest.mark.unit`):**

- `scan_firstrate_directory` — directory fixture permutations (empty, single timeframe, multi-timeframe, nested).
- `validate_schema_sample` — 6-column ok, 5/7 column mismatch, empty file, blank leading lines, CRLF.
- `estimate_parquet_bytes` — deterministic ratio math.
- `DryRunReport` / `TimeframeSummary` / `SchemaMismatch` Pydantic models — field presence and validation.
- `_run_dry_run` — happy path, mismatch path, missing-directory path, zero-side-effects (patch every service class and assert `not_called`).
- `--dry-run` flag routing — asserts `_run_dry_run` called and `_run_import` not called (and vice versa for `--no-dry-run`).

**Component tests (`@pytest.mark.component`):**

- Full CLI invocation via `CliRunner` on `tmp_path` fixtures:
  1. Happy path: 2 tickers, 1 timeframe, valid schema → exit 0, report contents.
  2. Schema mismatch: 1 bad file among good → exit 0, mismatch table present.
  3. Zero side effects: patch all service classes, assert none instantiated.
  4. Multi-timeframe: 1day + 1hour files → two rows + TOTAL.

**Not in this story:**

- Real Parquet I/O (deferred to future integration story).
- Actual 387 GB FirstRate benchmark (deferred; tune `PARQUET_COMPRESSION_RATIO` empirically in a later cleanup).
- `--dry-run` interaction with `--timeframe` filter (see Task 2.2 note — scanner reports all timeframes found; `--timeframe` filter is a usability concern, not a correctness one, and can be added in a follow-up if useful).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.6 — Pre-Import Dry-Run Validation]
- [Source: _bmad-output/planning-artifacts/prd.md#FR2 — dry-run validates file counts, schemas, date ranges, disk estimates]
- [Source: _bmad-output/planning-artifacts/prd.md — Risk table row on 387GB dataset mitigation]
- [Source: _bmad-output/planning-artifacts/architecture.md:351-363 — CLI Command Pattern with --dry-run flag]
- [Source: _bmad-output/planning-artifacts/architecture.md:431 — import_data.py documented as the command location]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md:101 — dry-run CLI usage example]
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md:438-463 — Journey 1 dry-run flow and "Dry-run builds confidence before committing disk space" UX principle]
- [Source: _bmad-output/implementation-artifacts/1-5-cli-import-command-with-progress-and-summary.md — CLI patterns, deferred imports, exit code discipline]
- [Source: _bmad-output/implementation-artifacts/deferred-work.md#D4 — --dry-run deferred from Story 1-5]
- [Source: src/cli/commands/import_data.py — existing `import` command, deferred-import pattern, Rich output style]
- [Source: src/services/firstrate/import_service.py:228-262 — `_discover_tickers` and `_extract_ticker` patterns to mirror (without inheriting Nautilus imports)]
- [Source: src/services/firstrate/parsers/firstrate_csv_parser.py:88-113 — `_read_lines` blank/CRLF handling and 6-column split rule]
- [Source: src/services/data_catalog.py:313-315 — existing ~128 bytes/row estimation precedent]
- [Source: src/models/catalog.py — home for new `SchemaMismatch` / `TimeframeSummary` / `DryRunReport` models]
- [Source: CLAUDE.md — LogGuard gotcha, C extension isolation, TDD non-negotiable]

## Dev Agent Record

### Agent Model Used

claude-opus-4-6 (bmad-dev-story workflow)

### Debug Log References

- `uv run pytest tests/unit/models/test_catalog.py` → 20 passed
- `uv run pytest tests/unit/services/firstrate/test_dry_run.py` → 22 passed
- `uv run pytest tests/unit/cli/commands/test_import_data.py` → 34 passed
- `uv run pytest tests/component/cli/commands/test_import_data.py` → 14 passed
- `make test-unit` → 663 passed
- `make test-component` → 464 passed, 15 skipped (pre-existing)
- `make lint` → clean
- `make typecheck` → clean

### Completion Notes List

- Implemented the dry-run scanner, validator, and estimator in a pure-Python
  module `src/services/firstrate/dry_run.py` with **zero** `nautilus_trader`
  imports. Enforced via an AST-based test (`test_module_does_not_import_nautilus`).
- The scanner uses `Path.rglob("*.txt")` so both FirstRate layouts
  (`Stocks_1day/` or parent `Stocks/`) work in one pass with no directory-name
  heuristics. Timeframe is inferred purely from filename suffix.
- Schema validation samples only the first 5 files per timeframe group
  (`sample_size=5`) and reads only the first non-blank line per file — reuses
  the same CRLF / blank-line tolerance as `FirstRateCsvParser._read_lines`
  without importing it.
- Disk estimate uses `PARQUET_COMPRESSION_RATIO = 0.35` as a module constant
  with a docstring note to tune against one real FirstRate Stocks sample in a
  follow-up cleanup story.
- CLI path: `--dry-run/--no-dry-run` flag on the existing `import` command.
  Branches in `import_firstrate` to `_run_dry_run`, which is unit-tested to
  never construct `ImportService`, `CatalogManager`, `InstrumentMapper`,
  `MetadataService`, or `get_sync_session_maker`.
- Dry-run asset class **defaults to STOCK** (Phase 1 pivot 2026-04-11). The
  existing `_run_import` path still defaults to ETF — intentionally unchanged
  to avoid churning committed behavior.
- Exit code discipline: AC-4 requires exit 0 even with schema mismatches;
  only `source_path` not a directory / fatal OS errors return 2. Exit code 1
  is never emitted from the dry-run path.
- Rich output: four-column table (Timeframe | Tickers | Files | Source Size |
  Est. Parquet Size) plus a TOTAL row; separate `Schema Mismatches` table
  capped at 20 rows with `... and N more` truncation. Trailing
  `"No data written — dry run only."` line for explicit confirmation.
- No new dependencies (`humanize` was not elected; wrote a 4-line
  `_format_bytes` helper in `import_data.py` instead).

### File List

- `src/models/catalog.py` — **MODIFIED** — added `SchemaMismatch`,
  `TimeframeSummary`, and `DryRunReport` Pydantic models plus `__all__` export.
- `src/services/firstrate/dry_run.py` — **NEW** — pure-Python scanner,
  validator, estimator, and `build_dry_run_report` orchestration helper.
- `src/cli/commands/import_data.py` — **MODIFIED** — added `--dry-run/--no-dry-run`
  Click option, `_run_dry_run`, `_print_dry_run_report`, and `_format_bytes`;
  branches in `import_firstrate` to dispatch dry-run vs. real import.
- `tests/unit/models/test_catalog.py` — **MODIFIED** — added
  `TestSchemaMismatch`, `TestTimeframeSummary`, and `TestDryRunReport`.
- `tests/unit/services/firstrate/test_dry_run.py` — **NEW** — 22 unit tests
  for scanner, validator, estimator, and `build_dry_run_report`.
- `tests/unit/cli/commands/test_import_data.py` — **MODIFIED** — added
  `TestDryRunFlag` and `TestRunDryRun` classes.
- `tests/component/cli/commands/test_import_data.py` — **MODIFIED** — added
  `TestDryRunInvocation` class covering AC-1..AC-4 via `CliRunner`.

### Review Findings

Review performed 2026-04-11 via bmad-code-review (Blind Hunter + Edge Case Hunter + Acceptance Auditor). All four ACs verified implemented and tested. No blockers. Items below are improvements and test-quality issues; triage resolved 45 raw findings into 3 decisions, 16 patches, 2 defers, 14 dismissed.

**Decision-needed (resolved 2026-04-11, all became patches):**

- [x] [Review][Decision] **TOTAL row "Tickers" column double-counts across timeframes** → option (b): scanner carries a top-level `distinct_tickers` set, exposed as new `DryRunReport.distinct_ticker_count`; TOTAL row now shows the true distinct count. [src/services/firstrate/dry_run.py — `_walk`, `_state_to_report`; src/cli/commands/import_data.py — `_print_dry_run_report`]
- [x] [Review][Decision] **Fallback ticker extraction for filenames without `_full_` marker** → option (a): `_extract_ticker` now returns `None` when the marker is absent; `_walk` excludes such files from ticker/file counts and appends a `SchemaMismatch` with `reason="unrecognized filename pattern"`. [src/services/firstrate/dry_run.py — `_extract_ticker`, `_walk`]
- [x] [Review][Decision] **`_run_dry_run` accepts `--format` and `--catalog` but deletes them** → option (a): `_run_dry_run` validates `format_name == "firstrate"` (exit 2 otherwise) and threads `catalog` through `build_dry_run_report` into `DryRunReport.catalog`; `_print_dry_run_report` renders it in the header. [src/cli/commands/import_data.py — `_run_dry_run`, `_print_dry_run_report`]

**Patch (applied 2026-04-11):**

- [x] [Review][Patch] Hardcoded `0.35` in `_print_dry_run_report` duplicates `PARQUET_COMPRESSION_RATIO` — per-row rows drifted from TOTAL. Fixed by calling `estimate_parquet_bytes(summary.source_bytes)` per row. [src/cli/commands/import_data.py]
- [x] [Review][Patch] `test_scanner_never_opens_files` patches only `builtins.open`. Fixed by patching both `pathlib.Path.open` and `builtins.open` with a raising `side_effect`. [tests/unit/services/firstrate/test_dry_run.py]
- [x] [Review][Patch] `test_module_does_not_import_nautilus` walked AST only. Fixed by running a fresh subprocess that imports `src.services.firstrate.dry_run` and asserts `"nautilus_trader" not in sys.modules`. [tests/unit/services/firstrate/test_dry_run.py]
- [x] [Review][Patch] `test_dry_run_never_constructs_services` used a throwing generator lambda that never fired. Fixed by replacing with a plain function `_db_must_not_be_touched` that raises `AssertionError`. [tests/unit/cli/commands/test_import_data.py]
- [x] [Review][Patch] `build_dry_run_report` walked the source tree twice. Fixed by introducing a single `_walk(source_path) -> _ScanState` helper that both `scan_firstrate_directory` and `build_dry_run_report` reuse; `_group_files_by_timeframe` deleted. [src/services/firstrate/dry_run.py]
- [x] [Review][Patch] Scanner silently `continue`d on `file.stat()` errors. Fixed by incrementing `state.unreadable_count` on stat errors and on `os.walk` `onerror` callbacks; exposed via new `DryRunReport.unreadable_count` and rendered as a yellow warning line. [src/services/firstrate/dry_run.py — `_walk`; src/cli/commands/import_data.py — `_print_dry_run_report`]
- [x] [Review][Patch] `_read_first_nonblank_line` swallowed `UnicodeDecodeError` as "empty file". Fixed by adding a `reason: str | None` field to `SchemaMismatch` and returning `(None, "decode error")` / `(None, "empty file")` / `(None, "unreadable")` so the validator surfaces a classified reason. [src/models/catalog.py; src/services/firstrate/dry_run.py]
- [x] [Review][Patch] `_infer_timeframe` substring check was case-sensitive. Fixed by lowercasing `filename` before the suffix match; unit test `test_uppercase_timeframe_suffix` added. [src/services/firstrate/dry_run.py]
- [x] [Review][Patch] `rglob("*.txt")` was case-sensitive on Linux. Fixed by switching to `os.walk` and filtering with `name.lower().endswith(".txt")`; unit test `test_uppercase_extension_included` added. [src/services/firstrate/dry_run.py]
- [x] [Review][Patch] `Path.rglob` followed symlinks and could hang on loops. Fixed by switching to `os.walk(source_path, followlinks=False, onerror=_on_walk_error)`. [src/services/firstrate/dry_run.py]
- [x] [Review][Patch] Per-directory `PermissionError` during lazy iteration could abort the entire scan. Fixed: `os.walk` `onerror` callback counts per-directory failures into `unreadable_count` and continues the walk. [src/services/firstrate/dry_run.py]
- [x] [Review][Patch] Empty or all-unknown report printed a blank Rich table. Fixed: renderer now emits a yellow "No importable files found" warning when total_file_count is 0, and a "All discovered files fell into the 'unknown' timeframe bucket" warning when only the `unknown` bucket is populated. [src/cli/commands/import_data.py]
- [x] [Review][Patch] Component test missing `Tickers 2`/`Files 2` assertions. Fixed with a regex `r"1-DAY-LAST\D+2\D+2"` anchoring on the 1-DAY-LAST row, plus a catalog-name assertion for D3. [tests/component/cli/commands/test_import_data.py]
- [x] [Review][Patch] `_format_bytes` had an unreachable trailing return and B-scale float truncation. Fixed: loop cleaned up, integer-formatted only for B scale, trailing return removed. [src/services/firstrate/dry_run.py — `format_bytes`]
- [x] [Review][Patch] Rich `Table(title="Dry-Run Report")` duplicated the preamble line. Fixed by dropping the `title=` kwarg and keeping the richer preamble (which also now shows the catalog name). [src/cli/commands/import_data.py]
- [x] [Review][Patch] `_format_bytes` lived in `import_data.py` instead of `dry_run.py` as the spec dictates. Fixed: moved to `src/services/firstrate/dry_run.py` as public `format_bytes`; CLI imports it from there. [src/services/firstrate/dry_run.py, src/cli/commands/import_data.py]

**Deferred (out of scope per spec):**

- [x] [Review][Defer] `--dry-run` + `--timeframe` filter interaction is silently ignored — deferred, explicitly listed under "Not in this story" in the spec's Testing Strategy section.
- [x] [Review][Defer] `PARQUET_COMPRESSION_RATIO = 0.35` ships as an untuned placeholder — deferred, spec explicitly says "tune against one FirstRate Stocks sample in a later cleanup story".

**Dismissed as noise / spec-allowed** (not listed; see review transcript): Click `exists=True` dead-code guard (spec Task 6.1 requires it), 5-file sample limit (spec explicitly accepts), `source_path: str` vs `Path` asymmetry (Pydantic coerces), mismatch dedup with multiple suffixes (unrealistic), `test_happy_path_returns_0` empty-dir name (coverage elsewhere), whitespace-tolerant blank line handling (matches parser), exit-code-2 overlap with Click usage errors (POSIX-ish), `sorted(rglob)` memory (test-determinism need), header-row-passes-6-col-check (spec: "minimal split on comma"), CRLF subtle deviation (functionally equivalent), symlinked file double-count (not FirstRate layout), `test_zero_side_effects` patch timing (CLI-level is the contract), `source_bytes=0 file_count>0` silent pass (empty files already flagged by validator).

## Change Log

- 2026-04-11 — Story 1.6 implementation complete. Added `--dry-run` flag to
  the `import` CLI command; introduced pure-Python dry-run scanner/validator/
  estimator with zero Nautilus imports; added `SchemaMismatch`,
  `TimeframeSummary`, and `DryRunReport` models; 36 new unit tests and 4 new
  component tests. Story status → review.
- 2026-04-11 — Code review complete (bmad-code-review). 3 decisions resolved,
  16 patches applied: single-walk scanner refactor (os.walk, followlinks=False);
  distinct-ticker set surfaced as `DryRunReport.distinct_ticker_count`;
  filename-pattern mismatches and encoding errors classified via new
  `SchemaMismatch.reason` field; case-insensitive `.txt`/timeframe matching;
  `unreadable_count` tracked and surfaced; `_format_bytes` moved to
  `dry_run.py` as public `format_bytes`; `_run_dry_run` now validates
  `--format` and threads `--catalog` into the report header; three brittle
  tests (scanner-opens-files, nautilus-import, DB-must-not-be-touched)
  replaced with effective assertions; component happy-path now asserts
  ticker/file counts. Unit tests 663 → 674, component tests unchanged (464),
  all lint/typecheck clean. 2 items deferred to
  `deferred-work.md#code-review-of-1-6` (`--timeframe` interaction,
  PARQUET_COMPRESSION_RATIO tuning). Story status → done.
