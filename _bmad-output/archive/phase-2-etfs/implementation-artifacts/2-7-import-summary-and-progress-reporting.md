# Story 2.7: Import Summary & Progress Reporting

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want progress reported during the import and a summary report at the end that also folds in the Epic 1
resolution counts,
so that I can monitor a long ETF batch as it runs and review exactly what happened — tickers processed, rows
imported, failures with reasons, and how metadata resolution went.

## Acceptance Criteria

1. **AC1 — Progress during the import via structured logging.**
   Given an import in progress, When it runs, Then progress is reported (current ticker, running counts,
   errors) via structured logging.

2. **AC2 — End-of-run summary incorporates the Epic 1 `ResolutionSummary`.**
   Given a completed import, When the summary is produced, Then it reports tickers processed, rows imported,
   and failures with reasons, AND it incorporates the Epic 1 `ResolutionSummary`
   (resolved / descriptive-gaps / venue-unresolved counts).

## Tasks / Subtasks

- [x] **Task 1 — Emit per-ticker progress with running counts (AC: #1)**
  - [x] In `ImportService.import_directory`, after each ticker is imported, emit a structured `import_progress`
        event carrying `ticker`, `timeframe`, `index`, `total`, the running `success` / `failed` / `skipped`
        counts, and the `error` string when that ticker failed. Builds ON the existing
        `import_directory_start` / `import_directory_complete` lines (those stay) — this adds the per-ticker
        running-count line the AC asks for. No change to the per-ticker import logic.

- [x] **Task 2 — Collect resolved metadata during the run (AC: #2)**
  - [x] `ImportService._resolve_metadata` already calls the Epic-1 cache-first
        `InstrumentMetadataService.resolve(ticker)` (Story 2.4) but discards the returned domain
        `InstrumentMetadata`. Capture it into a per-run, per-ticker dict (`_resolved_metadata`) so each ticker
        is recorded at most once (mirrors the `_resolved_tickers` dedup). Expose a read-only
        `resolved_metadata` property returning the collected domain models. Faults stay isolated — a metadata
        hiccup records nothing and never fails the bar import.

- [x] **Task 3 — Fold `ResolutionSummary` into the end-of-run summary (AC: #2)**
  - [x] In `import_data._run_import`, after `_print_summary`, build
        `ResolutionSummary.from_results(import_service.resolved_metadata)` and render it via the existing
        Story-1.6 `_print_resolution_summary`. Print it only when metadata was actually resolved this run
        (the ETF path with FMP configured) so the stocks/no-resolver path stays unchanged. The summary section
        (tickers processed, rows imported, failures with reasons) is the existing `_print_summary` output —
        unchanged.

- [x] **Task 4 — Tests mapping each AC to a passing test, fixtures/doubles only (AC: #1, #2)**
  - [x] Unit (`tests/unit/services/firstrate/test_import_service.py`): `_resolve_metadata` collects the
        resolved domain model and dedups per ticker; the `resolved_metadata` property exposes it; a resolver
        fault records nothing. `import_directory` emits one `import_progress` line per ticker with running
        counts (logger monkeypatched).
  - [x] Component (`tests/component/services/firstrate/test_import_service.py`, new
        `TestStory27ResolutionSummaryCollection` class) reusing the existing in-memory doubles
        (`_FakeMetadataProvider`, `_FakeMetadataRepo`, `_install_parser_and_readback`): the real cache-first
        `InstrumentMetadataService` resolves three tickers across a full `import_directory` run, and
        `ResolutionSummary.from_results(service.resolved_metadata)` reports the expected resolved count — no
        real import, no network, no Postgres.
  - [x] Unit (`tests/unit/cli/commands/test_import_reporting.py`): the existing
        `build_resolution_summary_text` / `_print_resolution_summary` helpers (Story 1.6) already cover the
        render; cross-referenced — no duplication.

- [x] **Task 5 — Gates**
  - [x] `uv run ruff check .` clean; `uv run mypy .` (no NEW errors vs the documented baseline);
        `make test-unit` + `make test-component` green for the touched areas.

### Review Findings

- [x] [Review][Defer] `ResolutionSummary` has no bucket for a non-throwing `UNRESOLVED` record
  [src/models/instrument_metadata.py:142-147] — deferred, pre-existing. `from_results` (Story 1.6) counts a
  record toward `resolved` or `venue_unresolved` by status; a clean-degrade `UNRESOLVED` (not `VENUE_UNRESOLVED`)
  increments neither, so `resolved + venue_unresolved` can under-count. Pre-existing Epic-1 aggregation
  semantics — Story 2.7 only reads `from_results`, it does not change it. Whether the FMP provider can return a
  non-throwing `UNRESOLVED` is an Epic-1/Epic-3 venue-worklist concern.
- Dismissed (false positives / by design): progress line keys on `status` while the summary keys skipped on
  `outcome` — no reachable divergence (`_import_ticker` sets both together; `ImportResult` carries both fields);
  per-timeframe running-count reset is intentional (`import_directory` is a per-timeframe operation and the CLI
  loops timeframes); all-faulted ETF run prints no Resolution Summary — intended per spec ("printed only when
  resolution actually ran"); no dedicated CLI-level test for the 3-line fold-in — covered at the service
  (`resolved_metadata`) + model (`from_results`) level, and a CLI test would need the full DB session path
  (violates the infra-light guardrail).

## Dev Notes

### Build ON the existing pipeline — do NOT rewrite it
Most of the reporting surface already exists; Story 2.7 wires the two missing pieces:

- **Progress (AC1):** `import_directory` already logs `import_directory_start` / `import_directory_complete`
  and the CLI prints a per-ticker `_print_progress_line`. The AC additionally asks for **running counts** in
  the structured log stream — added as a per-ticker `import_progress` event inside the loop.
- **Resolution summary (AC2):** the `ResolutionSummary` model (`from_results`), `build_resolution_summary_text`,
  and `_print_resolution_summary` already exist from Story 1.6 but were never wired into the ETF import path.
  The import path resolves metadata per ticker (Story 2.4) but threw the results away. Story 2.7 collects them
  and renders the summary at end-of-run.

### Load-bearing pieces
- `src/services/firstrate/import_service.py`
  - `import_directory` — add the `import_progress` running-count line (AC1); collect via `_resolve_metadata`.
  - `_resolve_metadata` — capture the domain `InstrumentMetadata` returned by the Epic-1 resolver into
    `_resolved_metadata` (deduped per ticker); faults isolated (record nothing). New `resolved_metadata`
    property (AC2).
- `src/cli/commands/import_data.py` — after `_print_summary`, fold in
  `ResolutionSummary.from_results(import_service.resolved_metadata)` via `_print_resolution_summary` when
  resolution ran (AC2).
- `src/cli/commands/import_reporting.py` — `_print_resolution_summary` / `build_resolution_summary_text`
  (Story 1.6) reused as-is. `_print_summary` (tickers processed, rows, failures) reused as-is.
- `src/models/instrument_metadata.py` — `ResolutionSummary.from_results` aggregates the three counters.

### Why no migration
`instrument_metadata` already exists (Epic 1); resolution writes through the existing cache-first service. This
story only reads back the in-run domain results and renders counts. No schema change, no Alembic migration.

### Out of scope (later stories / epics — do NOT implement)
- Venue resolution / unresolved-venue report (Epic 3), explorer (Epic 4), backtests (Epic 5).
- Any DB schema change / Alembic migration (none needed), and any change to financial calculations or
  previously produced numbers.

### Project Structure Notes
- **Modified:** `src/services/firstrate/import_service.py` (progress line + resolved-metadata collection),
  `src/cli/commands/import_data.py` (fold in `ResolutionSummary`).
- **Tests:** `tests/unit/services/firstrate/test_import_service.py`,
  `tests/component/services/firstrate/test_import_service.py`.
- Stay under size limits; reuse existing fixtures and in-memory doubles.

### Testing standards
- **Tiers:** Unit for the collection/dedup/progress-logging logic with mocked deps; Component for the
  full `import_directory` run with the real cache-first resolver over in-memory doubles. No engine, no
  Postgres, no network, no real import. Commands: `make test-unit`, `make test-component`.
- **Import gate (F401/F821):** add imports and usages in the same edit.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.7] — progress (current ticker, running counts,
  errors) via structured logging; end-of-run summary (processed, rows, failures w/ reasons) incorporating the
  Epic 1 `ResolutionSummary` (lines 551-566); FR18, FR19
- [Source: harness-epic2-rest-spec.md#Story-2.7] — scoped ACs + TEA verification guidance (fixtures only,
  no real import/network/DB)
- [Source: src/cli/commands/import_reporting.py:245-283] — `build_resolution_summary_text` /
  `_print_resolution_summary` (Story 1.6), reused
- [Source: src/models/instrument_metadata.py:105-152] — `ResolutionSummary.from_results`
- [Source: src/services/firstrate/import_service.py:650-676] — `_resolve_metadata` (Story 2.4 cache-first)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

### Completion Notes List

- **AC1:** `import_directory` now emits a per-ticker `import_progress` structured event with `ticker`,
  `timeframe`, `index`, `total`, running `success`/`failed`/`skipped` counts, and the `error` on failure.
- **AC2:** `_resolve_metadata` collects the Epic-1 cache-first resolver's domain `InstrumentMetadata` into a
  per-run, per-ticker dict; `resolved_metadata` exposes it; the CLI folds
  `ResolutionSummary.from_results(...)` into the end-of-run summary via the Story-1.6
  `_print_resolution_summary` (printed only when resolution ran).
- **No migration:** reads back in-run resolution results; `instrument_metadata` already exists.
- **Gates:** `ruff check .` clean; `mypy .` no new errors; `make test-unit` + `make test-component` green.

### File List

- `src/services/firstrate/import_service.py` (progress line + resolved-metadata collection + property)
- `src/cli/commands/import_data.py` (fold `ResolutionSummary` into the end-of-run summary)
- `tests/unit/services/firstrate/test_import_service.py` (collection/dedup/fault + progress-logging tests)
- `tests/component/services/firstrate/test_import_service.py` (new `TestStory27ResolutionSummaryCollection`)
- `_bmad-output/implementation-artifacts/2-7-import-summary-and-progress-reporting.md` (this story)
