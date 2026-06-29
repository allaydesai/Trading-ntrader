# Story 2.5: Import Verification — OHLC Sanity, Row-Count Parity & Sample-Point

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want per-ticker/timeframe verification during import (OHLC sanity, row-count parity, sample-point accuracy),
so that zero data loss and import accuracy are provable, not assumed — and any verification problem is logged and surfaced in the import summary rather than silently passing.

## Acceptance Criteria

1. **AC1 — OHLC sanity is checked and invalid rows are flagged.**
   Given bars being imported for a ticker/timeframe, When OHLC sanity is checked, Then rows violating
   `high ≥ low` or `volume ≥ 0` are flagged (per ticker/timeframe).

2. **AC2 — Row-count parity (source vs output Parquet).**
   Given a completed ticker/timeframe import, When completeness is verified, Then the source row count is
   compared against the output Parquet row count and any mismatch is reported.

3. **AC3 — Sample-point accuracy (first/last rows).**
   Given a completed ticker/timeframe import, When accuracy is validated, Then sample data points (first and
   last rows) are compared between source and output.

4. **AC4 — Verification problems are logged AND surfaced (never silent).**
   Given any verification failure (row-count mismatch, sample-point mismatch) or OHLC-sanity flag, When it is
   detected, Then it is logged via structured logging AND surfaced in the import summary — it does not silently
   pass.

## Tasks / Subtasks

- [x] **Task 1 — OHLC-sanity verification primitive (AC: #1)**
  - [x] Add `src/services/firstrate/import_verification.py` with a pure `check_ohlc_sanity(bars)` that flags any
        bar with `high < low` or `volume < 0`, returning human-readable violation strings (capped). No I/O, no
        DB, no Nautilus engine — coerces Nautilus `Price`/`Quantity` via `.as_double()` (and plain numbers) so it
        unit-tests with stub bars and works on real bars. `import_service.py` is already over the 500-line size
        limit, so the new logic lives in its own module rather than growing it.

- [x] **Task 2 — Wire OHLC sanity into the import loop (AC: #1, #4)**
  - [x] In `ImportService._import_ticker`, after a successful non-empty parse, call `check_ohlc_sanity(bars)`.
        When violations exist, log a structured `ohlc_sanity_flagged` event (ticker, timeframe, count, sample)
        and thread the violations into the per-ticker `ImportResult.warnings`. OHLC flags are **non-blocking**
        (the bars still import — FirstRate ships occasional glitch rows) but never silent. The existing blocking
        checks — `_verify_row_count` (AC2) and `_verify_sample_points` (AC3) — are unchanged and keep failing the
        ticker on a mismatch.

- [x] **Task 3 — `ImportResult.warnings` field (AC: #1, #4)**
  - [x] Add `warnings: list[str] = Field(default_factory=list)` to `ImportResult` (default empty keeps every
        existing call site valid). Document it as the non-blocking verification-flag channel.

- [x] **Task 4 — Surface verification in the summary (AC: #4)**
  - [x] `import_reporting._bucket_results`: add a `flagged` count (tickers with warnings). `build_summary_text`
        and `_print_summary`: add a `Flagged (OHLC)` metric and a `Verification Warnings` section listing each
        flagged ticker and its issues. `_print_progress_line`: append a `⚠` marker on a successful row that
        carries warnings. Row-count / sample-point failures already surface in the `Failures` table (AC2/AC3
        reporting); OHLC flags do not change the exit code.

- [x] **Task 5 — Tests with fixtures/stubs only (AC: #1, #2, #3, #4)**
  - [x] Unit (`tests/unit/services/firstrate/test_import_verification.py`): `check_ohlc_sanity` flags `high < low`
        and `volume < 0`, returns empty for clean bars, caps a flood of violations, and flags a **real** Nautilus
        `Bar` with `high < low` (proves the `.as_double()` path) (AC1).
  - [x] Component (`tests/component/services/firstrate/test_import_service.py`): importing a ticker whose bars
        include a `high < low` row leaves `status == "success"` (non-blocking) but populates `result.warnings`
        and logs `ohlc_sanity_flagged` (AC1, AC4).
  - [x] Unit (`tests/unit/cli/commands/test_import_reporting.py`): `build_summary_text` reports the
        `Flagged (OHLC)` count and a `Verification Warnings` block; a row-count/sample failure still appears under
        `Failures` (AC4).
  - [x] AC2/AC3 reporting is already proven by the existing `TestVerifyRowCount`,
        `TestVerifySamplePoints`, and `test_verification_failure_skips_metadata_upsert` tests (kept green).

- [x] **Task 6 — Gates**
  - [x] `uv run ruff check .` clean; `uv run mypy .` (no NEW errors vs the documented baseline);
        `make test-unit` + `make test-component` green for the touched areas.

## Dev Notes

### Build ON the existing pipeline — do NOT rewrite it
The per-ticker import loop already verifies row count and sample points and surfaces failures:
- `src/services/firstrate/import_service.py` — `_import_ticker` runs parse → write → `_verify_row_count` (AC2)
  → `_verify_sample_points` (AC3) → upsert, returning `status == "failed"` with an `error` on a mismatch. This
  story **adds** OHLC sanity (AC1) as a non-blocking flag and **surfaces** all verification problems in the
  summary (AC4). It does not move or rewrite the two existing blocking checks.
- `src/services/firstrate/parsers/base.py` — `BaseParser.validate_bars` independently validates raw rows
  (high<low, non-positive prices, volume<0) and logs `bar_validation_failed`; that complementary stream stays.
- `src/cli/commands/import_reporting.py` — owns the progress line + Rich/text summary; the new
  `Flagged (OHLC)` metric and `Verification Warnings` section go here.

### Why bar-level OHLC sanity
Nautilus `Bar` construction does **not** reject out-of-range OHLC (proven: negative prices pass through
`parse_file`), so a `high < low` row survives to the bar list and is the realistic catalog-integrity case AC1
targets ("bars being imported"). The check coerces `Price`/`Quantity` through `.as_double()` so it works on both
real bars and the lightweight stub/mocked bars used across the import tests.

### Blocking vs non-blocking
Row-count parity and sample-point accuracy are **blocking** (a mismatch fails the ticker — existing behavior).
OHLC-sanity is a **flag** ("rows … are flagged", AC1): the bars still import, but the violation is logged
(`ohlc_sanity_flagged`) and surfaced in the summary so it never silently passes (AC4). OHLC flags do not change
the CLI exit code; only `status == "failed"` does (unchanged `determine_exit_code`).

### Out of scope (later stories / epics — do NOT implement)
- Idempotent date-range re-runs (Story 2.6) and the `ResolutionSummary` line in the summary (Story 2.7).
- Venue resolution (Epic 3), explorer (Epic 4), backtests (Epic 5).
- Any DB schema change / Alembic migration (none needed), and any change to financial calculations or
  previously produced numbers.

### Project Structure Notes
- **New:** `src/services/firstrate/import_verification.py` (pure `check_ohlc_sanity`).
- **Modified:** `src/services/firstrate/import_service.py` (call + thread warnings),
  `src/models/catalog.py` (`ImportResult.warnings`), `src/cli/commands/import_reporting.py` (summary surfacing).
- Stay under size limits (files <500, functions <50, classes <100, line <100). `import_service.py` is already
  over 500 — do not grow it materially; the new logic is a separate module.

### Testing standards
- **TDD** (CLAUDE.md / development-principles): failing test first (Red-Green-Refactor).
- **Tiers:** Unit for the pure verification primitive + reporting text; Component for the full loop with the
  existing test doubles. No engine, no Postgres, no network, no real import. Commands: `make test-unit`,
  `make test-component`.
- **Import gate (F401/F821):** add imports and usages in the same edit.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.5] — OHLC sanity (high≥low, volume≥0) flag,
  row-count parity (source vs Parquet), sample-point accuracy, logged + surfaced (lines 507-529); FR13/FR14/FR15
- [Source: harness-epic2-rest-spec.md#Story-2.5] — scoped ACs + TEA verification guidance (fixtures only,
  no real import/network/DB)
- [Source: src/services/firstrate/import_service.py] — existing `_verify_row_count` / `_verify_sample_points`
  (AC2/AC3) and the per-ticker `ImportResult` return path to extend
- [Source: src/services/firstrate/parsers/base.py:126-195] — `validate_bars` raw-row integrity gate (complementary)
- [Source: src/cli/commands/import_reporting.py] — `_bucket_results` / `build_summary_text` / `_print_summary`
  to extend with the OHLC-flag surfacing

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

### Completion Notes List

- **AC1:** new pure `import_verification.check_ohlc_sanity(bars)` flags `high < low` and `volume < 0` per bar,
  capping the violation list. Bar-level because Nautilus does not reject out-of-range OHLC, so the violations
  reach the catalog and are the realistic integrity case.
- **AC1/AC4 wiring:** `_import_ticker` calls it after parse, logs `ohlc_sanity_flagged`, and threads the
  violations into `ImportResult.warnings` (non-blocking — bars still import) on both the success and the
  verification-failure return paths.
- **AC2/AC3:** unchanged blocking `_verify_row_count` / `_verify_sample_points`; their failures already surface
  in the `Failures` table and were re-confirmed green.
- **AC4 surfacing:** `import_reporting` now counts `Flagged (OHLC)`, prints a `Verification Warnings` section in
  both the text and Rich summaries, and marks flagged success rows with `⚠`. Exit code unchanged.
- **Gates:** `ruff check .` clean; `mypy .` no new errors; `make test-unit` + `make test-component` green.

### File List

- `src/services/firstrate/import_verification.py` (new — `check_ohlc_sanity`)
- `src/services/firstrate/import_service.py` (OHLC call + `warnings` threading)
- `src/models/catalog.py` (`ImportResult.warnings`)
- `src/cli/commands/import_reporting.py` (`flagged` bucket, `Verification Warnings` surfacing, `⚠` progress marker)
- `tests/unit/services/firstrate/test_import_verification.py` (new — `check_ohlc_sanity` unit tests)
- `tests/component/services/firstrate/test_import_service.py` (OHLC-flag-surfaced component test)
- `tests/unit/cli/commands/test_import_reporting.py` (summary surfacing tests)
- `_bmad-output/implementation-artifacts/2-5-import-verification-ohlc-sanity-row-count-parity-and-sample-point.md` (this story)
