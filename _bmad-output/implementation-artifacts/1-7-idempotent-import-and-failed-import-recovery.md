# Story 1.7: Idempotent Import & Failed Import Recovery

Status: review

## Story

As a system operator,
I want the import to skip complete tickers and re-import only incomplete ones,
so that I can recover from interrupted imports by re-running the same command.

## Acceptance Criteria

1. **AC-1: Complete Ticker Is Skipped** — Given a ticker that was fully imported in a prior run (a `catalog_instruments` row exists with a non-NULL `date_range_end` whose UTC date equals the max-date found in the source CSV, AND `bar_count_<timeframe> > 0` for the timeframe being imported), when the import command is re-run for the same catalog and source directory, then the ticker is skipped (no CSV parsing beyond the quick last-date probe, no `catalog.write_data`, no metadata upsert), and a structured `ticker_import_skipped` log line is emitted with ticker, catalog, timeframe, and `reason="already complete"`.

2. **AC-2: Incomplete Ticker Is Re-Imported From Scratch** — Given a ticker whose metadata row has `date_range_end < source_last_date` (partial prior import; source has newer data), when the import is re-run, then the ticker is re-imported end-to-end (parse all source rows → `catalog.write_data` overwrites the existing Parquet file per the ADR-5 write-data semantics → verify row count → verify sample points → upsert metadata with the refreshed date range and bar count). The resulting `ImportResult.outcome` is `"reimported"`.

3. **AC-3: Orphaned Parquet (No Metadata) Is Treated As New** — Given a ticker whose `catalog_instruments` row has `date_range_end IS NULL` and `bar_count_<timeframe> == 0` (orphaned from a crash after `catalog.write_data` but before the metadata upsert — or simply never imported for this timeframe after `load_company_profiles`), when the import is re-run, then the ticker is fully imported (parse → `catalog.write_data` overwrites any orphaned Parquet, per ADR-5 — → verify → upsert). The resulting `ImportResult.outcome` is `"new"`.

4. **AC-4: Summary Distinguishes New / Re-Imported / Skipped** — Given a mixed-state import run, when `_print_summary` is generated, then the summary table shows four distinct counts: `New`, `Re-imported`, `Skipped`, and `Failed`; AND the `Total processed` line equals `new + reimported` only (skipped and failed tickers are NOT counted as "processed"); AND the `Total tickers` line equals `new + reimported + skipped + failed` (all discovered tickers); AND the progress line per skipped ticker shows a distinct glyph (e.g., `⟳` for skipped) so operators can visually scan a long run.

5. **AC-5: Exit Code Unchanged By Skipping** — Given a re-run where every ticker is skipped (zero new, zero re-imported, zero failed), when the CLI exits, then the exit code is `0`. Skipping is a success outcome. Exit code `1` is still reserved for "some tickers failed" and exit code `2` for fatal errors, matching `_run_import`'s existing contract (`src/cli/commands/import_data.py:85-96`).

6. **AC-6: Last-Date Probe Is Cheap and Nautilus-Free** — Given the pre-classification step reads the source CSV to compute the max source timestamp, when the probe runs on a ticker that will be skipped, then it must NOT construct `Bar`/`Price`/`Quantity` objects and must NOT import any `nautilus_trader` module; it reads the file text, splits each non-blank line on comma, takes column 0, parses as `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`, and returns the max as a UTC `datetime`. Reuse must be done by *logic parity* with `FirstRateCsvParser._read_lines` / `_parse_timestamp`, NOT by import (the parser module pulls in Nautilus C extensions per CLAUDE.md's LogGuard gotcha).

## Tasks / Subtasks

- [x] **Task 1: Extend `ImportResult` with an `outcome` field** (AC: 1, 2, 3, 4)
  - [x] 1.1 Write failing unit test in `tests/unit/models/test_catalog.py` that `ImportResult` accepts `outcome: Literal["new", "reimported", "skipped"] | None` with default `None`, and that unknown values raise `ValidationError`.
  - [x] 1.2 Write failing unit test asserting `ImportResult(status="skipped", ...)` is valid (status domain: `"success" | "failed" | "skipped"`).
  - [x] 1.3 Add `outcome: Optional[Literal["new", "reimported", "skipped"]] = None` to `ImportResult` in `src/models/catalog.py`. Do NOT tighten `status` to a Literal in this story — existing callers pass strings; document the new allowed value in the docstring only.
  - [x] 1.4 Update `__all__` in `src/models/catalog.py` only if you introduce a new symbol (you won't; `ImportResult` is already exported).

- [x] **Task 2: Add a pure-Python source-CSV last-date probe** (AC: 1, 6)
  - [x] 2.1 Write failing unit tests for `compute_source_last_date(file_path: Path) -> datetime | None` in `tests/unit/services/firstrate/test_source_probe.py`:
    - Daily format (`YYYY-MM-DD` column 0): returns a UTC datetime at 00:00:00 on the max date.
    - Intraday format (`YYYY-MM-DD HH:MM:SS` column 0): returns a UTC datetime at the max second.
    - File with unsorted rows: still returns the true max (not the last line's date).
    - Empty file: returns `None`.
    - File with blank lines and CRLF endings: matches `FirstRateCsvParser._read_lines` tolerance.
    - File with fewer than 6 columns on every line: returns `None` (nothing parseable).
    - Unreadable file (`PermissionError`): returns `None` (log a structured warning, don't raise).
  - [x] 2.2 Implement `compute_source_last_date` in a new helper module `src/services/firstrate/source_probe.py` — pure Python, zero `nautilus_trader` imports (enforced by a subprocess/import-isolation test as in Story 1-6). Use `file_path.read_text(encoding="utf-8", errors="replace")` and iterate lines; split on comma; require `len(parts) == 6` before attempting to parse column 0; parse with `datetime.strptime` using both `"%Y-%m-%d %H:%M:%S"` (intraday) and `"%Y-%m-%d"` (daily) branches, mirroring `FirstRateCsvParser._parse_timestamp`. Return the max as `datetime(tz=timezone.utc)` or `None` if no row parsed.
  - [x] 2.3 Write failing unit test asserting `src.services.firstrate.source_probe` does not import `nautilus_trader` (subprocess approach — see 1-6's `test_module_does_not_import_nautilus`).
  - [x] 2.4 Size target: helper module ≤ 80 lines; `compute_source_last_date` ≤ 30 lines (CLAUDE.md size limits).

- [x] **Task 3: Add a classifier that decides skip / reimport / new per ticker** (AC: 1, 2, 3)
  - [x] 3.1 Write failing unit tests for `ImportService._classify_ticker(ticker, file_path, catalog_name, timeframe) -> Literal["skipped", "reimported", "new"]` covering every row in the Dev Notes edge-case table.
  - [x] 3.2 Implement `_classify_ticker` in `src/services/firstrate/import_service.py`. Metadata lookup via `_metadata_service.get_instrument_sync`, source probe via `compute_source_last_date`, day-granularity comparison.
  - [x] 3.3 Added module-level helper `_bar_count_field_for_timeframe` that wraps the existing `_TIMEFRAME_FIELD_MAP` lookup and keeps `_classify_ticker` ≤ 50 lines.
  - [x] 3.4 Log one structured line per classification decision via `_log_classification` helper (`ticker_classification`, `ticker`, `decision`, `metadata_date_end`, `source_last_date`, `timeframe`).

- [x] **Task 4: Wire the classifier into `_import_ticker` and `import_directory`** (AC: 1, 2, 3, 4, 5)
  - [x] 4.1 Unit tests added covering skipped/reimported/new outcomes plus classifier-exception-becomes-failed and the new `import_directory_complete` log line.
  - [x] 4.2 `_import_ticker` now calls `_classify_ticker` first and short-circuits on `"skipped"`; otherwise tags `result.outcome` with the classifier's decision.
  - [x] 4.3 `import_directory` now tallies `skipped`, `reimported`, `new` counts into the `import_directory_complete` log payload.
  - [x] 4.4 Kept the `InstrumentMapper.is_loaded` profile-gate at the top of `import_directory` untouched — skipped tickers still depend on a loaded mapper for metadata lookups.

- [x] **Task 5: CLI summary + progress line updates** (AC: 4, 5)
  - [x] 5.1 Failing unit tests added in `tests/unit/cli/commands/test_import_data.py` for progress glyph, summary buckets, and all exit-code branches (all-skipped, mixed, any-failed).
  - [x] 5.2 `_print_progress_line` now emits the `⟳` glyph + `skipped (already complete)` reason for `status == "skipped"`.
  - [x] 5.3 `_print_summary` and `build_summary_text` share a `_bucket_results` helper that groups by `r.outcome`; they render New / Re-imported / Skipped / Failed / Total rows / Total processed.
  - [x] 5.4 `determine_exit_code` docstring now explicitly calls out skipped as a success outcome.

- [x] **Task 6: Component tests — re-run behavior end-to-end** (AC: 1, 2, 3, 4, 5)
  - [x] 6.1 `TestIdempotentRerun::test_second_run_skips_complete_tickers` — back-to-back `import_directory` calls over a two-ticker fixture; second run shows `outcome="skipped"` + `row_count=0` for both, and `mock_catalog.write_data.call_count` is unchanged.
  - [x] 6.2 `TestIdempotentRerun::test_partial_metadata_triggers_reimport` — first run imports; test rewinds AAPL's `date_range_end` to 2024-12-31; second run returns `outcome="reimported"` and AAPL's metadata row is refreshed past 2024-12-31.
  - [x] 6.3 `TestIdempotentRerun::test_orphan_metadata_triggers_new_import` — first run imports; test zeros AAPL's `date_range_end` and `bar_count_daily`; second run returns `outcome="new"` and bumps the Parquet write count.
  - [x] 6.4 `TestStory17DoubleRun::test_second_run_shows_skip_glyph_and_summary` — `CliRunner` invokes the CLI twice; first run prints `✓` for each ticker, second run prints `⟳` + `skipped (already complete)` + `Skipped` summary row, both exit 0.
  - [x] 6.5 All new tests are marked `@pytest.mark.component` and do NOT require `--forked`.

- [x] **Task 7: Documentation + deferred-work hygiene** (AC: 4)
  - [x] 7.1 `ImportResult` class docstring in `src/models/catalog.py` now documents the `outcome` field and its allowed values.
  - [x] 7.2 Added an ADR-5 block comment above `_classify_ticker` in `import_service.py` pointing to `architecture.md:212-216`.
  - [x] 7.3 Verified `_bmad-output/implementation-artifacts/deferred-work.md` is unchanged for this story.

## Dev Notes

### The Cardinal Rule: The Metadata Row Is The Gatekeeper

Architecture ADR-5 (`_bmad-output/planning-artifacts/architecture.md:212-216`) is the source of truth for this story:

> Import writes Parquet via `catalog.write_data()`, then upserts metadata row only after successful write + row count verification. No temp files, no staging catalog. Interrupted imports leave orphaned Parquet files (invisible to explorer/backtest since no metadata row exists). Re-run detects incomplete tickers and re-imports.

Consequence for this story: the `catalog_instruments` row IS the ground truth for "what was successfully imported". The Parquet file on disk is NOT the ground truth — it may be stale, partial, or orphaned. The classifier must decide solely from metadata + the cheap source-CSV probe. **Do not** try to enumerate Parquet files under `catalog_base_path / <catalog> / data / bar / ...` to detect orphans — that's fragile to Nautilus internal layout changes and was explicitly rejected by ADR-5.

An "orphan" (Parquet written, metadata upsert failed) is detected by checking `date_range_end IS NULL AND bar_count_<tf> == 0` on the existing row. The right recovery is: re-run the full import path; `catalog.write_data` overwrites per the ADR-5 "idempotent by design but partial writes during interruption could corrupt data" note — and re-writing the full bar set heals any partial-write corruption.

### Source-CSV Last-Date Probe — Pure Python, Zero Nautilus

`FirstRateCsvParser` imports `nautilus_trader.model.*` (see `src/services/firstrate/parsers/firstrate_csv_parser.py:17-20`). Importing the parser module pulls the C/Rust extension into process state and risks the LogGuard double-init panic (`CLAUDE.md#Critical Gotchas`). The classifier runs **before** the parser does, and for skipped tickers it must never invoke the parser at all — so the source-date probe MUST be in a separate pure-Python module.

Create `src/services/firstrate/source_probe.py`. The probe:

1. Reads the source file as text with `file_path.read_text(encoding="utf-8", errors="replace")`.
2. Iterates lines with the same CRLF + blank-line handling as `FirstRateCsvParser._read_lines` (`src/services/firstrate/parsers/firstrate_csv_parser.py:88-97`) — copy the 4-line loop inline; do NOT import it.
3. Splits each line on `,`; skips lines where `len(parts) != 6`.
4. Attempts to parse `parts[0].strip()` as `datetime.strptime(..., "%Y-%m-%d %H:%M:%S")` first, then `"%Y-%m-%d"` on failure (mirrors `FirstRateCsvParser._parse_timestamp`).
5. Tracks the running max; returns `max_dt.replace(tzinfo=timezone.utc)` or `None` if nothing parsed.

FirstRate files are not guaranteed sorted by date (the parser explicitly sorts at `firstrate_csv_parser.py:69`). Therefore reading only the *last* line is unsafe. You must scan the whole file. This is cheap because each file is typically a few MB for daily timeframes (≤ 100K rows).

### Day-Granularity Comparison Prevents Drift

When comparing source last date vs. metadata `date_range_end`, compare at **day granularity** via `.date()` on both sides. Rationale:

- Daily bars have `ts_init` at 00:00:00 UTC of the trade date (FirstRate convention — see `_parse_timestamp` default branch).
- `ImportService._upsert_metadata` (`src/services/firstrate/import_service.py:370-383`) stores `bars[-1].ts_init` as the metadata `date_range_end`.
- If a prior run imported daily data ending 2025-01-15 and the source still ends 2025-01-15, both sides resolve to `date(2025, 1, 15)`. Strict datetime equality works too, but day-level equality is robust against any future change in how we store timestamps (e.g., intraday + daily mix).

Use `datetime.date()` conversion, not string formatting.

### Classifier Edge Cases (Be Explicit)

| Metadata row | `date_range_end` | `bar_count_<tf>` | Source last date | Decision |
|---|---|---|---|---|
| Exists | Present | > 0 | == metadata (day) | **skipped** |
| Exists | Present | > 0 | > metadata | **reimported** |
| Exists | Present | > 0 | < metadata | **reimported** + warn (stale/regressed source) |
| Exists | Present | > 0 | `None` (empty/malformed source) | **new** (let import path fail loudly) |
| Exists | `None` | 0 | any | **new** (orphan / never imported this tf) |
| Exists | Present | 0 | any | **new** (never imported *this* timeframe, only a different one) |
| Missing | — | — | — | **new** (shouldn't happen post-profile-load; safe default) |

AC-3's orphan case maps to the `date_range_end IS NULL AND bar_count == 0` row. Do not try to detect orphans via filesystem checks.

### Progress Line & Summary — Three-Way Bucket

The existing `_print_progress_line` in `src/cli/commands/import_data.py:133-145` is binary (`✓`/`✗`). Extend it to a three-way split: `✓` (success/new/reimported), `⟳` (skipped), `✗` (failed). Keep the same `<ticker> — <timeframe> — <reason>` shape so the output remains parseable by anything that already consumes it.

The existing `_print_summary` Rich table shows four rows: Total tickers, Successful, Failed, Total rows. Refactor to seven rows:

- Total tickers (= all discovered)
- New
- Re-imported
- Skipped
- Failed
- Total rows (only new + reimported rows)
- Total processed (= new + reimported — AC-4 explicit requirement)

Use `r.outcome` (new field) to bucket; do NOT bucket on `r.status` alone (success could be either new or reimported).

### Exit Code Discipline — Preserve Existing Contract

`determine_exit_code` (`src/cli/commands/import_data.py:85-96`) currently returns 1 iff any `status == "failed"`. This already handles skipped-only runs correctly (returns 0). Just add an explicit test for the all-skipped case and a comment clarifying that skipped is a success outcome. Exit code 2 remains reserved for fatal errors in `_run_import` (DB unreachable, source path invalid, etc.).

### Nautilus C Extension Isolation — Same Rules As Story 1-6

- ❌ Do NOT import `nautilus_trader` in `src/services/firstrate/source_probe.py` at all.
- ❌ Do NOT import from `src/services/firstrate/parsers/firstrate_csv_parser.py` in `source_probe.py` — that module transitively imports Nautilus.
- ❌ Do NOT run the import-isolation assertion via AST walking alone; run it as a fresh subprocess that imports `src.services.firstrate.source_probe` and asserts `"nautilus_trader" not in sys.modules` (see Story 1-6 review patch for the pattern).
- ✅ DO use `datetime.strptime` and manual comma-splitting inline — 15 lines of logic, no import chain.

### Session Lifecycle Notes — Leave It Alone

Story 1-5 fix `85bafaa` established that `_run_import` commits at the end and rolls back on exception (`src/cli/commands/import_data.py:267-288`). **This story does not touch that lifecycle.** Skipping a ticker does not upsert metadata and therefore does not dirty the session; the existing commit-at-end pattern handles all three outcomes (new / reimported / skipped) identically. Do not refactor session management here.

### Testing Strategy

**Unit tests (`@pytest.mark.unit`):**

- `compute_source_last_date` — sorted/unsorted/empty/CRLF/intraday/daily/malformed/permission-error variants.
- Import-isolation subprocess assertion for `source_probe.py`.
- `ImportResult.outcome` Pydantic validation — allowed values, default `None`, rejection of unknown.
- `ImportService._classify_ticker` — every row in the edge-case table above.
- `ImportService._import_ticker` — all three outcomes lead to the right `ImportResult` shape and the right downstream calls (parser / `catalog.write_data` / `metadata_service.upsert_instrument_sync` are called or not called appropriately).
- `_print_progress_line`, `_print_summary`, `determine_exit_code`, `build_summary_text` — all four buckets visible, exit-code zero on skipped-only.

**Component tests (`@pytest.mark.component`):**

- Back-to-back `import_directory` calls (new → skipped).
- Partial metadata simulation (rewind `date_range_end` → reimported).
- Orphan simulation (`date_range_end = None` + `bar_count = 0` → new).
- CLI-level `CliRunner` double-run: progress glyphs + summary counts + exit code.

**Not in this story:**

- Real Parquet file overwrite verification (Nautilus-internal; rely on existing verify-row-count + verify-sample-points gates in `_import_ticker`).
- Multi-timeframe mix (e.g., daily skipped, hourly new) — the classifier handles it correctly by checking `bar_count_<tf>` per-timeframe, but dedicated multi-tf component coverage is deferred to Epic 2 where the explorer will make these mixed states visible.
- `--force` flag to override skip decisions — out of scope; re-document as follow-up if operators request it.
- `--dry-run` + idempotency interaction — dry-run already reports "what would be imported" irrespective of current state; keeping them orthogonal is intentional.

### Project Structure Notes

- `src/services/firstrate/import_service.py` — **MODIFY** (add `_classify_ticker`, wire it into `_import_ticker`, add skipped/reimported/new counting to `import_directory` log line).
- `src/services/firstrate/source_probe.py` — **NEW** (pure-Python `compute_source_last_date`; no Nautilus imports).
- `src/models/catalog.py` — **MODIFY** (add `outcome` field to `ImportResult`; update docstring).
- `src/cli/commands/import_data.py` — **MODIFY** (`_print_progress_line`, `_print_summary`, `build_summary_text`, `determine_exit_code` comment).
- `tests/unit/services/firstrate/test_source_probe.py` — **NEW** (probe unit tests + import-isolation subprocess test).
- `tests/unit/services/firstrate/test_import_service.py` — **MODIFY** (add `TestClassifyTicker`, `TestImportTickerOutcomes`).
- `tests/unit/cli/commands/test_import_data.py` — **MODIFY** (progress/summary/exit-code branches for skipped).
- `tests/unit/models/test_catalog.py` — **MODIFY** (extend `TestImportResult` with `outcome` cases).
- `tests/component/services/firstrate/test_import_service.py` — **MODIFY** (add re-run, partial, orphan scenarios).
- `tests/component/cli/commands/test_import_data.py` — **MODIFY** (add CLI double-run test).
- `alembic/versions/` — **NO CHANGE** (all DB fields already exist in `catalog_instruments`).
- `pyproject.toml` — **NO CHANGE** (no new dependencies).

### Previous Story Intelligence

**From Story 1-6 (`_bmad-output/implementation-artifacts/1-6-pre-import-dry-run-validation.md`):**

- Pure-Python helper modules under `src/services/firstrate/` are the established pattern for Nautilus-isolation — `dry_run.py` is your template for `source_probe.py` (same file layout, same docstring style, same subprocess-based isolation test).
- The subprocess-based `test_module_does_not_import_nautilus` pattern (review patch, 1-6 Review Findings) is the correct way to enforce "no Nautilus imports"; AST walking is insufficient.
- Review finding in 1-6 removed in-line `_format_bytes` from `import_data.py` and put it in `dry_run.py` — follow the same principle: `compute_source_last_date` lives in `source_probe.py`, not in `import_data.py` or `import_service.py`.
- Case-insensitive file handling and `errors="replace"` on text reads came out of 1-6 edge-case review; apply both to the probe.

**From Story 1-5 (`_bmad-output/implementation-artifacts/1-5-cli-import-command-with-progress-and-summary.md`):**

- `_print_progress_line` and `_print_summary` are the right hooks for AC-4 — do not invent new rendering helpers. Extend the existing functions.
- Exit code discipline: 0 / 1 / 2 semantics are locked. Do not invent exit code 3 for "all skipped".
- Commit `85bafaa` moved `session.commit()` to the end of `_run_import`; the skipped path does not dirty the session, so that commit still works.
- Review D3 (broad `except Exception` with generic message) — reuse the same pattern in `_classify_ticker`'s caller; classification failures become `ImportResult(status="failed")` via the existing except block in `_import_ticker`.

**From Story 1-4 (`_bmad-output/implementation-artifacts/1-4-import-pipeline-core.md` + deferred D1/D2):**

- `_discover_tickers` `subdir.iterdir()` still has no exception handling (deferred D1). Not in scope for this story, but be aware that permission errors during ticker discovery crash the whole run — if your re-run encounters a newly-unreadable directory, you'll hit that edge case before classification runs. Do not try to fix D1 here; add a one-line note to `deferred-work.md` only if the Task-6 component tests happen to exercise it accidentally.
- `_upsert_metadata` mutates the ORM object in place (deferred D2). The skipped path never mutates the ORM object, so D2 is irrelevant to the skipped code path but still applies to the reimported/new paths you're leaving unchanged.

**From Story 1-3 (`InstrumentMapper` / profile loading):**

- `InstrumentMapper.is_loaded(catalog_name)` checks for ≥ 1 row in `catalog_instruments` for this catalog. Skipped tickers still need profiles loaded for `_metadata_service.get_instrument_sync` to return a row. Don't try to optimize this check away.

### Git Intelligence

Recent commits on the branch show small, focused per-story diffs (~200-400 lines including tests). Expect this story to add ~80 lines to `import_service.py`, ~60-line new `source_probe.py`, ~20 lines to `import_data.py` (progress + summary tweaks), ~10 lines to `catalog.py`, and ~400 lines of tests across unit + component suites. No migrations. No new dependencies.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.7 — Idempotent Import & Failed Import Recovery]
- [Source: _bmad-output/planning-artifacts/architecture.md:212-216 — ADR-5 Idempotent Import with Metadata Gatekeeper]
- [Source: _bmad-output/planning-artifacts/architecture.md:130-137 — ParquetDataCatalog `write_data` overwrite semantics]
- [Source: _bmad-output/planning-artifacts/architecture.md:365-374 — Import Pipeline Error Pattern (per-ticker isolation)]
- [Source: _bmad-output/planning-artifacts/prd.md#FR9 — Idempotent re-runs via date-range comparison]
- [Source: src/services/firstrate/import_service.py:114-226 — `_import_ticker` current success path — extend before parsing step]
- [Source: src/services/firstrate/import_service.py:343-390 — `_upsert_metadata` metadata-gatekeeper semantics]
- [Source: src/services/firstrate/import_service.py:84-112 — `import_directory` structured logging — extend with skip/reimport/new counts]
- [Source: src/services/firstrate/parsers/firstrate_csv_parser.py:88-150 — `_read_lines` / `_parse_timestamp` — logic to mirror (NOT import) in `source_probe.py`]
- [Source: src/services/firstrate/dry_run.py — file-layout template for pure-Python helper module + isolation test pattern]
- [Source: src/services/firstrate/metadata_service.py:114-135 — `get_instrument_sync` / `upsert_instrument_sync` — classifier's only DB touchpoint]
- [Source: src/db/models/catalog_instrument.py:55-70 — `date_range_end`, `bar_count_daily/hourly/minute` fields the classifier reads]
- [Source: src/cli/commands/import_data.py:85-96 — `determine_exit_code` — extend comment; keep semantics]
- [Source: src/cli/commands/import_data.py:133-178 — `_print_progress_line` and `_print_summary` — extend for three-way bucket]
- [Source: src/models/catalog.py:66-82 — `ImportResult` model — add `outcome` field]
- [Source: _bmad-output/implementation-artifacts/1-6-pre-import-dry-run-validation.md#Review Findings — subprocess import-isolation test pattern to reuse]
- [Source: _bmad-output/implementation-artifacts/deferred-work.md#D1 — known crash on unreadable subdirs; NOT in this story's scope]
- [Source: CLAUDE.md#Critical Gotchas — LogGuard + C extension isolation rules]
- [Source: CLAUDE.md#Anti-Patterns — never reuse `BacktestEngine`; file/function size limits]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context) — `claude-opus-4-6[1m]`

### Debug Log References

- Ruff auto-formatter stripped `Literal` / `compute_source_last_date` imports on two occasions when the consuming code was added in a follow-up edit. Worked around by bundling the import line with its first usage in a single Edit call, per CLAUDE.md's ruff-auto-linter guidance.
- Existing `TestImportTicker` and `TestErrorIsolation` suites briefly regressed after wiring the classifier because their `mock_metadata_service.get_instrument_sync.return_value = MagicMock()` fixture created auto-attrs whose `bar_count_daily <= 0` raised `TypeError`. Fixed by introducing `_fresh_instrument_row()` helpers in both the unit and component test modules that set `date_range_end=None` and explicit `bar_count_*=0` on the returned mock, which yields a deterministic `"new"` classification.
- Component tests needed to align `bars[-1].ts_init` with the source CSV's last date (the default `_make_bars` fixture used 2020-dated bars, but the `idempotent_source_dir` CSVs end on 2025-01-15). Extracted `_force_bar_end_to_2025_01_15` so the three re-run scenarios share one alignment step.

### Completion Notes List

- Implemented the Story 1-7 metadata-gated idempotent classifier end-to-end: `compute_source_last_date` (pure-Python, zero Nautilus), `ImportService._classify_ticker` (day-granularity comparison, full edge-case coverage), and the `_import_ticker` short-circuit that skips parser + `catalog.write_data` + metadata upsert for complete tickers.
- Added an `outcome` field to the `ImportResult` domain model with `Literal["new", "reimported", "skipped"]` validation; legacy call sites that never set it keep working (`Optional`, defaults to `None`).
- Extended the CLI progress/summary rendering to a three-way bucket (`✓` / `⟳` / `✗`) and surfaced the seven-row summary table (Total tickers / New / Re-imported / Skipped / Failed / Total rows / Total processed). `determine_exit_code` now has explicit tests for all-skipped and mixed-success-and-skipped runs returning exit 0.
- `import_directory_complete` structured log line now includes `skipped`, `reimported`, and `new` counts alongside the pre-existing `total`/`success`/`failed`.
- Added subprocess-based import-isolation test for `src.services.firstrate.source_probe` mirroring the Story 1-6 pattern.
- Full unit + component suite (`pytest tests/unit tests/component`) passes: **1183 passed, 15 skipped** (the skipped tests are pre-existing `test_report_commands.py` TODOs, unrelated to this story).
- `ruff check` / `ruff format` / `mypy` on all touched files: clean.

### File List

- `src/models/catalog.py` — **MODIFIED** (added `ImportOutcome` Literal alias + `outcome` field on `ImportResult`, docstring update).
- `src/services/firstrate/source_probe.py` — **NEW** (pure-Python `compute_source_last_date` + `_parse_timestamp`; no Nautilus imports).
- `src/services/firstrate/import_service.py` — **MODIFIED** (new `_bar_count_field_for_timeframe` helper, `_classify_ticker`, `_log_classification`, wiring into `_import_ticker` and `import_directory`).
- `src/cli/commands/import_data.py` — **MODIFIED** (`determine_exit_code` docstring, new `_bucket_results` helper, updated `build_summary_text`, `_print_progress_line`, `_print_summary`).
- `tests/unit/models/test_catalog.py` — **MODIFIED** (added `TestImportResult` cases for `outcome` Literal + skipped-status validation).
- `tests/unit/services/firstrate/test_source_probe.py` — **NEW** (10 tests: probe edge cases + subprocess import-isolation).
- `tests/unit/services/firstrate/test_import_service.py` — **MODIFIED** (new `_fresh_instrument_row` helper + fixture defaults; added `TestBarCountFieldForTimeframe`, `TestClassifyTicker`, `TestImportTickerOutcomes`, `TestImportDirectoryCompleteLogLine`).
- `tests/unit/cli/commands/test_import_data.py` — **MODIFIED** (rewrote `test_summary_totals`; added `TestStory17ProgressAndSummary` with progress-glyph, summary-bucket, and exit-code cases).
- `tests/component/services/firstrate/test_import_service.py` — **MODIFIED** (fixture now returns fresh instrument rows; added `TestIdempotentRerun` with skip / reimport / orphan scenarios, plus `_force_bar_end_to_2025_01_15` helper).
- `tests/component/cli/commands/test_import_data.py` — **MODIFIED** (rewrote `test_summary_report_contains_metrics`; added `TestStory17DoubleRun::test_second_run_shows_skip_glyph_and_summary`).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — **MODIFIED** (1-7 in-progress → review).

## Change Log

| Date       | Version | Description                                                            |
| ---------- | ------- | ---------------------------------------------------------------------- |
| 2026-04-11 | 0.1     | Story drafted (ready-for-dev).                                         |
| 2026-04-11 | 0.2     | Implementation complete — classifier, CLI bucket rendering, tests, docs. Story ready for review. |
