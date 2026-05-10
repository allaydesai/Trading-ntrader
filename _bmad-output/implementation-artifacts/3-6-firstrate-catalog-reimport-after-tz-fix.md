# Story 3.6: FirstRate Catalog Re-import After Timezone Fix

Status: ready-for-dev

<!-- BLOCKING for Epic 4 — Epic 3 retro action item C2. -->

## Story

As a system operator,
I want every existing FirstRate-backed catalog re-imported through the timezone-corrected parser, with any persisted `backtest_runs` rows that referenced pre-fix data flagged as TZ-incorrect,
So that all bar timestamps in storage reflect the actual UTC instant of the bar (not a 4–5h DST-dependent shift) and Epic 4 supplementary data can join correctly to the corrected bar data.

## Background — why this story exists

Story 3.4's parity comparison surfaced a silent bug in `FirstRateCsvParser._parse_timestamp` (and the parallel `source_probe._parse_timestamp`): naive Eastern-time CSV timestamps were stamped as UTC instead of localized to `America/New_York` and converted. Every FirstRate-imported intraday bar was shifted 4–5h DST-dependent; daily bars were shifted 0h but stored as midnight UTC instead of the correct midnight ET (=05:00 UTC). The bug landed in production code on day one of the FirstRate parser (Epic 1, Story 1.2) and was uncaught by every Epic 1 unit test, Epic 2 visual chart verification, and Story 3.3's CSV-vs-FirstRate comparison.

The parser fix landed in Story 3.4. Existing catalogs imported before that fix still hold corrupt timestamps. Per the retro readiness assessment, Epic 4 cannot start until the catalogs are re-imported — Epic 4 dividend/split data joins to bar data, and overlay views in the explorer would surface the TZ drift.

The current inventory is small: one FirstRate-backed catalog (`e2e-test`) holding 5 tickers (AAPL/AMZN/MSFT/NVDA/TSLA) × 4 timeframes (1-MINUTE / 5-MINUTE / 1-HOUR / 1-DAY) = 20 partitions, ~21.25M deduplicated bars. The re-import is operationally light but needs a structured handoff so we don't lose track of which `backtest_runs` rows ran on corrupt data.

## Scope & Non-Goals

**In scope:**

- **Inventory** every existing FirstRate-backed catalog under `CATALOG_BASE_PATH` (`./data/catalogs/`). Confirmed today: `e2e-test` is the only FirstRate-backed catalog; if any user-private catalogs exist on the operator's machine that weren't imported through `ntrader import --format firstrate`, those are out-of-scope.
- **Capture pre-fix metadata snapshot** — for each catalog × ticker × timeframe, record current `bar_count_*` and `date_range_start/end` from `catalog_instruments` BEFORE deleting partitions. Saved as `_bmad-output/implementation-artifacts/3-6-evidence/pre-import-metadata.json` so we can verify the post-import counts are sane (expected ~0% bar count delta; expected `date_range_*` shift of 4–5h on intraday and 5h on daily).
- **Delete the partitioned parquet data** for each affected catalog (`{catalog_path}/data/bar/*` directories that match a FirstRate-imported instrument) and the corresponding `catalog_instruments` rows. Use the existing `ntrader import` clear/overwrite path if available; otherwise delete the partition directories and run `import --format firstrate` with `--clear` semantics (verify the existing `conflict_mode` behavior in `FirstRateCsvParser` / `ImportService`).
- **Re-run import** — `ntrader import --format firstrate --catalog e2e-test <source-path>` for AAPL/AMZN/MSFT/NVDA/TSLA. Use the same source bundle that produced the original `e2e-test` (the FirstRate Stocks bundle path). Capture exit code, summary table, and updated `catalog_instruments` rows.
- **Post-import verification** — for each re-imported instrument:
  - Bar counts within 0.5% of pre-fix counts (the deduplication and source bundle haven't changed; only timestamp interpretation has). A larger delta is a red flag.
  - Date ranges shifted: intraday `date_range_start` should be ~5h LATER (UTC moved from naive-ET-stamped-as-UTC to true-UTC-of-09:30-ET); daily `date_range_start` should shift from midnight UTC to 05:00 UTC (midnight ET).
  - Spot-check one bar per ticker against IBKR / TradingView at a known timestamp (e.g., AAPL 2018-01-02 09:30 ET) — should now match to the cent post-fix.
- **Flag persisted `backtest_runs` rows** — any row in `backtest_runs` with `data_source LIKE 'catalog:%'` and `created_at < <fix commit timestamp>` referenced corrupt bar data. Either:
  - **Option A (preferred):** Add a column `data_quality_flag: str | None` to `backtest_runs` (nullable, default NULL); set it to `'tz_corrupted_pre_3.6'` for affected rows. Surface in the run-detail UI as an inline warning banner.
  - **Option B (light-touch fallback):** Insert a one-line comment row into `backtest_runs.notes` (if such a column exists) or write a separate `_bmad-output/implementation-artifacts/3-6-evidence/affected-runs.md` listing the affected `run_id`s.
  - Pick A unless schema-migration cost makes B clearly cheaper. Document the decision in Dev Notes before starting Task 4.
- **Update memory** — refresh the `project_e2e_catalog_setup.md` user memory entry with the new bar counts and date-range edges (the existing entry says "21.25M deduplicated bars"; verify and update if the count materially changes).
- **Quality gates clean** — `make format && make lint && make typecheck && make test-unit && make test-component`. No new test required for the operational re-import itself, but if the schema migration in Option A is taken, alembic + a unit test on the new column's default + serialization is required.

**Out of scope:**

- Closing the residual 0.31% PnL Δ / 44 trade Δ between IBKR and FirstRate post-fix. That's Story 3.7.
- Upstream-Nautilus issues (segmenter, error 326, useRTH leak). Those are Story 3-X / action items C9, C10.
- Any change to `FirstRateCsvParser._parse_timestamp` or `source_probe._parse_timestamp` — those are already fixed in Story 3.4.
- Multi-asset class / non-RTH timezone edge cases (DST gap / fall-back overlap). FirstRate convention is undocumented; deferred to a future 24/7 asset class story (per Story 3.4 review D10).
- A fresh production import from a NEW source bundle. Re-import means the same source bundle, fixed parser.
- Adding a UI for browsing or curating affected `backtest_runs` rows beyond the inline banner. If Option A's banner is enough for now, that's enough.

## Acceptance Criteria

1. **Pre-import inventory snapshot captured** — **Given** the operator runs the inventory step before any deletion, **When** the snapshot writer runs, **Then** `_bmad-output/implementation-artifacts/3-6-evidence/pre-import-metadata.json` exists with one entry per (catalog, ticker, timeframe) tuple containing `bar_count`, `date_range_start`, `date_range_end`, and the catalog_instruments primary key. Inventory shows `e2e-test` × 5 tickers × 4 timeframes = 20 entries (or fewer if any tickers have only some timeframes populated).

2. **All FirstRate-backed catalog data deleted before re-import** — **Given** the inventory step has completed, **When** the deletion step runs, **Then** every `{catalog_path}/data/bar/{instrument}` directory listed in the inventory is removed AND the corresponding `catalog_instruments` rows are deleted. After this step, `MetadataService.list_instruments(catalog_name='e2e-test')` returns an empty list (or a list excluding only non-FirstRate-imported tickers, if any).

3. **Re-import completes for every inventoried instrument** — **Given** the deletion step has completed, **When** the operator runs `ntrader import --format firstrate --catalog e2e-test <source-path>` against the same FirstRate Stocks source bundle that produced the original `e2e-test`, **Then** the command exits 0, the summary table shows N tickers imported with status NEW (not REIMPORTED — because we just deleted the rows), AND `catalog_instruments` re-populates with one row per (ticker, timeframe) tuple.

4. **Post-import bar counts within 0.5% of pre-fix counts** — **Given** the re-import has completed, **When** the post-import verification step runs, **Then** for each (catalog, ticker, timeframe) tuple the new `bar_count` is within `±0.5%` of the snapshotted pre-fix `bar_count`. **Rationale:** the source bundle and dedup logic haven't changed; only timestamp interpretation has. A larger delta indicates either a different source bundle was used (operational error) or the dedup logic interacts with the TZ fix in a way we haven't anticipated (real bug — pause and investigate).

5. **Post-import date ranges shifted as expected** — **Given** the post-import verification step runs, **When** comparing the new `date_range_start` and `date_range_end` to the pre-fix values, **Then**:
    - Intraday timeframes (1-MINUTE, 5-MINUTE, 1-HOUR): `date_range_start` is **later** by 4–5h (DST-dependent), reflecting that what was stored as `2018-01-02T04:00:00Z` (the bug's interpretation of `04:00 ET stamped as UTC`) becomes `2018-01-02T09:00:00Z` (the correct UTC of `04:00 ET = 09:00 UTC` in EST winter).
    - Daily timeframe: `date_range_start` shifts from midnight UTC to **05:00 UTC** (= midnight ET in EST winter; 04:00 UTC in EDT summer, average 05:00 UTC over a full year).
    - Direction of shift confirms the fix took effect; absolute drift on `date_range_end` follows the same pattern.

6. **Spot-check three known timestamps match IBKR / TradingView post-fix** — **Given** the re-imported `e2e-test` catalog and IBKR Gateway access, **When** the operator queries the catalog for AAPL 2018-01-02 09:30 ET, AAPL 2018-06-15 09:30 ET, and AAPL 2018-12-28 09:30 ET (the three sample points from Story 3.4's investigation), **Then** the returned bar's `open` price matches IBKR's `reqHistoricalData` open for the same UTC timestamp to **the cent** (FirstRate single-venue feed stores 4-decimal precision but rounds correctly to cents). Pre-fix these were off by 5–6%; post-fix they should align.

7. **Affected `backtest_runs` rows flagged** — **Given** the re-import has completed AND the operator has chosen Option A or Option B per Scope, **When** the flagging step runs, **Then**:
    - **(Option A)** A new `data_quality_flag: str | None` column exists on `backtest_runs`, NULL for new runs by default, set to `'tz_corrupted_pre_3.6'` for every row with `data_source LIKE 'catalog:%'` AND `created_at < <fix commit timestamp documented in Dev Notes>`. The run-detail UI surfaces an inline yellow warning banner when this flag is non-null. A new alembic migration handles the column add.
    - **(Option B)** `_bmad-output/implementation-artifacts/3-6-evidence/affected-runs.md` lists every affected `run_id` with its `data_source` and `created_at`. Decision documented in Dev Notes.

8. **Memory entry refreshed** — **Given** the post-import verification has completed, **When** the operator updates user memory, **Then** `~/.claude/projects/-Users-allay-dev-Trading-ntrader/memory/project_e2e_catalog_setup.md` reflects the new bar counts and date-range edges. If the bar count delta is < 0.5% and the count description is approximate ("21.25M"), no update is required.

9. **Pre-existing test corpus passes** — **Given** the re-import + flagging changes, **When** the operator runs `make format && make lint && make typecheck && make test-unit && make test-component`, **Then** all gates clean against Story 3.4's baseline (851 unit + 621 component). If Option A is taken, the new column's default + serialization has at least one unit test asserting the migration round-trips.

10. **Story 3.4's parity test still PASSES on the re-imported catalog** — **Given** the re-imported `e2e-test` catalog and `IBKR_AVAILABLE=1 E2E_CATALOG_AVAILABLE=1`, **When** the operator runs `pytest tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py --forked`, **Then** the 3 tests run with the same outcomes as Story 3.4's final state: `test_harness_runs_both_paths` PASSES, `test_full_reference_comparison_within_tolerance` PASSES (with the widened `bar 0.5% / trade ±50 / pnl 0.5%` tolerances landed in 3.4 P7), and `test_harness_raises_on_unwritable_output` PASSES. Pre-fix `pnl_delta` was 7.32% (failure); 3.4 fix-then-re-import collapsed it to 0.31%. This story re-runs the same harness against a clean re-import and confirms the 0.31% holds.

## Tasks / Subtasks

- [ ] Task 1: Pre-import inventory snapshot (AC: #1)
  - [ ] 1.1 Write `scripts/diagnostics/snapshot_catalog_metadata.py` — reads `catalog_instruments` for every catalog under `CATALOG_BASE_PATH`, filters to FirstRate-backed (heuristic: any with `nautilus_id` matching `*.NASDAQ` or `*.NYSE` and a non-null `bar_count_*`; refine if ambiguity surfaces), writes JSON.
  - [ ] 1.2 Run against `e2e-test`. Verify 20 entries (5 × 4) or document deviation.
  - [ ] 1.3 Save snapshot to `_bmad-output/implementation-artifacts/3-6-evidence/pre-import-metadata.json`. Snapshot is gitignored / not committed (matches Story 3.3's `/tmp/story-*-evidence/` convention) but the path is recorded in Dev Notes.

- [ ] Task 2: Delete partitions + metadata (AC: #2)
  - [ ] 2.1 Confirm all consuming services are quiescent (no active backtest run, web server stopped, no concurrent CLI). The catalog deletion is destructive.
  - [ ] 2.2 For each inventoried (catalog, ticker, timeframe): remove the parquet partition directory `{catalog_path}/data/bar/{nautilus_id}-{bar_type_spec}-EXTERNAL/` AND delete the `catalog_instruments` row. Prefer using existing `ImportService` clear semantics if available; fall back to direct `rm -rf` on the partition directory + a SQL `DELETE FROM catalog_instruments WHERE ...` if not.
  - [ ] 2.3 Verify `MetadataService.list_instruments(catalog_name='e2e-test')` returns an empty list (or the residual non-FirstRate set).

- [ ] Task 3: Re-import via fixed parser (AC: #3)
  - [ ] 3.1 Run `ntrader import --format firstrate --catalog e2e-test <source-path>` against the same source bundle path that produced the original `e2e-test` (operator knows the path; reference `project_firstrate_import_patterns.md` user memory if needed).
  - [ ] 3.2 Capture stdout / summary table to `_bmad-output/implementation-artifacts/3-6-evidence/import-stdout.txt`.
  - [ ] 3.3 Confirm exit code 0; confirm summary shows the expected NEW classifications.

- [ ] Task 4: Decide Option A vs Option B for `backtest_runs` flagging (AC: #7)
  - [ ] 4.1 Read `src/db/models/backtest_run.py` (or the equivalent SQLAlchemy model file). Inspect existing columns; check whether `notes` or similar free-text exists.
  - [ ] 4.2 Estimate alembic migration cost vs writing a static evidence file.
  - [ ] 4.3 Decision lands in Dev Notes BEFORE starting Task 5. Default to Option A unless schema-migration cost is clearly higher than the value of an in-UI banner.

- [ ] Task 5: Implement chosen flagging (AC: #7)
  - [ ] **(Option A)** 5A.1 New alembic migration `alembic/versions/XXXX_add_data_quality_flag.py` adding `data_quality_flag: str | None` (default NULL) to `backtest_runs`. Run `alembic upgrade head` against the local DB.
  - [ ] **(Option A)** 5A.2 Update `BacktestRun` model + repository to expose the column.
  - [ ] **(Option A)** 5A.3 One-shot SQL update: `UPDATE backtest_runs SET data_quality_flag = 'tz_corrupted_pre_3.6' WHERE data_source LIKE 'catalog:%' AND created_at < '<fix-commit-timestamp>';`. Document the timestamp source in Dev Notes (the commit that landed the parser fix in 3.4 — find via `git log --oneline -- src/services/firstrate/parsers/firstrate_csv_parser.py | grep -i 'tz\|timezone\|parse_timestamp'`).
  - [ ] **(Option A)** 5A.4 UI update: `templates/backtests/detail.html` renders a yellow inline banner when `view.data_quality_flag == 'tz_corrupted_pre_3.6'` (banner copy: "This backtest ran against a FirstRate catalog with corrupt timestamps. Re-run after the 3.6 re-import for trustworthy results."). Component test in `tests/component/api/test_backtest_detail_routes.py` covers the banner appearing/disappearing based on flag.
  - [ ] **(Option B)** 5B.1 Run a single SQL select to enumerate affected rows; write the list to `_bmad-output/implementation-artifacts/3-6-evidence/affected-runs.md` with columns `run_id | data_source | created_at | symbol`.
  - [ ] **(Option B)** 5B.2 Add a one-line note to the run-detail-UI README/comment block (if any) pointing to that file. No UI banner.

- [ ] Task 6: Post-import verification (AC: #4, #5, #6)
  - [ ] 6.1 Re-run the inventory script (Task 1.1) post-import; diff against the pre-import snapshot.
  - [ ] 6.2 Assert: bar count Δ within 0.5% per (catalog, ticker, timeframe). If any tuple breaches, halt and investigate.
  - [ ] 6.3 Assert: date-range shifts match the pattern in AC #5 (intraday +4–5h, daily +5h). If a shift goes the wrong direction, the fix didn't take effect.
  - [ ] 6.4 Spot-check AAPL 2018-01-02 09:30 ET / 2018-06-15 09:30 ET / 2018-12-28 09:30 ET against IBKR via `DataCatalogService.fetch_or_load(...)` and / or against the raw FirstRate CSV. Open prices should match IBKR to the cent.
  - [ ] 6.5 Capture verification output to `_bmad-output/implementation-artifacts/3-6-evidence/post-import-verification.txt`.

- [ ] Task 7: Re-run Story 3.4's parity harness (AC: #10)
  - [ ] 7.1 With `IBKR_AVAILABLE=1 E2E_CATALOG_AVAILABLE=1` set, run `pytest tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py --forked`.
  - [ ] 7.2 Confirm 3 tests, same outcomes as 3.4's final state. If `test_full_reference_comparison_within_tolerance` fails, pause — either the re-import drifted further than 0.5% PnL or there's a regression in the IBKR side. Investigate before closing this story.
  - [ ] 7.3 Capture pytest output to `_bmad-output/implementation-artifacts/3-6-evidence/parity-rerun.txt`.

- [ ] Task 8: Update memory + quality gates (AC: #8, #9)
  - [ ] 8.1 If bar counts shifted materially, update `~/.claude/projects/-Users-allay-dev-Trading-ntrader/memory/project_e2e_catalog_setup.md`. If the existing summary is still accurate, leave it.
  - [ ] 8.2 `make format && make lint && make typecheck` clean.
  - [ ] 8.3 `make test-unit && make test-component` zero regressions vs 851u + 621c baseline. If Option A added a column-default test, confirm it passes.

## Dev Notes

### Architecture Compliance

- **Idempotent classifier (Story 1-7).** The `import --format firstrate` pipeline classifies each ticker as `new` / `reimported` / `skipped`. After Task 2's deletion, every ticker should classify as `new`, NOT `reimported`. If `reimported` shows up, the deletion didn't take.
- **No `Instrument` persistence in catalog (Story 1-4 / Story 3-1).** FirstRate import does not write `Instrument` objects to the parquet catalog; instruments are synthesised at read time via `_build_equity` / `TestInstrumentProvider.equity`. The TZ fix only affects `Bar.ts_event` / `Bar.ts_init` values stored in parquet — instrument synthesis is unchanged.
- **Single-use `BacktestEngine` (CLAUDE.md Gotcha #4).** Task 7's parity harness creates two `BacktestEngine` instances sequentially (one per data path). Same pattern as Story 3.4. No regression risk.
- **LogGuard discipline (CLAUDE.md Gotcha #1).** Story 3.4's `_guard_nautilus_logging` monkey-patch protects against double-init. Re-running the parity harness in Task 7 inherits that.

### Critical Implementation Details

**Direction of timestamp shift:**

The fix in `_parse_timestamp` changed from:

```python
# BUG: stamps naive ET as UTC, no shift, wrong instant
naive.replace(tzinfo=ZoneInfo("UTC"))
```

to:

```python
# FIXED: localizes naive to ET, then converts to UTC
naive.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(ZoneInfo("UTC"))
```

Worked example for `2018-01-02 09:30:00` (naive, intended ET):
- **Before fix:** stored as `2018-01-02T09:30:00Z` (UTC) — but the actual market event was at `14:30:00 UTC`. So the catalog's stored UTC instant was **5 hours earlier** than reality.
- **After fix:** stored as `2018-01-02T14:30:00Z` (UTC) — correct.

This means the re-imported catalog's `date_range_start` for AAPL 2018 1-MIN should move from `2018-01-02T04:00:00Z` (the bug's reading of pre-market `04:00 ET`) to `2018-01-02T09:00:00Z` (correct UTC of `04:00 ET` in EST winter). All bars in between shift by the same offset.

**Why daily-bar shift is also notable (D6):**

Pre-fix daily bars stamped as `2018-01-02T00:00:00Z` (naive midnight ET stamped as UTC midnight) become `2018-01-02T05:00:00Z` (correct UTC of midnight ET in EST winter). This is a semantic change — Epic 4 dividend/split data joins on date may be unaffected (date arithmetic is TZ-agnostic), but any client comparing bar timestamps against `<= some_utc_midnight` boundary will see different results than before.

### Existing Code to Reuse

| What | Where | How to use |
|------|-------|------------|
| Inventory walker | `src/services/firstrate/import_service.py` (existing `_discover_*` helpers) | Borrow patterns; the snapshot script is read-only |
| `MetadataService` async + sync repos | `src/services/metadata_service.py` | Inventory + flagging both go through this |
| `CatalogManager.resolve_catalog(name)` | `src/services/catalog_manager.py` | Resolve `e2e-test` to its `ParquetDataCatalog` instance |
| `ntrader import --format firstrate` CLI | `src/cli/commands/import_data.py` (Story 1.5) | Re-run the import; no code change needed |
| `DataCatalogService.fetch_or_load` | `src/services/data_catalog.py` | Spot-check IBKR side in Task 6.4 |
| Story 3.4 parity harness | `scripts/verify_aapl_2018_reference.py` | Task 7 re-runs unchanged against re-imported catalog |
| `BacktestRun` ORM model | `src/db/models/backtest_run.py` (verify exact path) | Task 4/5 schema decision |

### Known Constraints

- **Single FirstRate catalog today.** If the operator has additional FirstRate-backed catalogs not surfaced by the inventory script, those are the operator's responsibility to flag and re-import. Worth a quick `find ./data/catalogs -maxdepth 2 -type d` before Task 1 to confirm.
- **Source bundle path is operator-local.** The CLI invocation `ntrader import --format firstrate --catalog e2e-test <source-path>` requires the operator to know `<source-path>`. That's fine — same operator who originally imported.
- **`backtest_runs` flagging is one-shot.** Once Option A's flag is set on the affected rows, subsequent runs against the re-imported catalog will have NULL flag. This is correct.
- **Pre-fix `data/AAPL_1min.csv` (the legacy CSV from Story 3.3) is NOT a FirstRate catalog** — it's a separate CSV import path. Don't accidentally include it in the inventory.

### Anti-patterns to Avoid

- **Don't re-introduce the bug while re-importing.** `_parse_timestamp` is fixed; verify by reading `src/services/firstrate/parsers/firstrate_csv_parser.py` BEFORE running Task 3. If the file shows `naive.replace(tzinfo=ZoneInfo("UTC"))`, abort — the fix wasn't merged or was reverted.
- **Don't delete `data/AAPL_1min.csv`.** It's a legacy CSV used by no live test (Story 3.3's CSV path was retired in 3.4). Out of scope for 3.6 — let it sit.
- **Don't conflate "re-import after TZ fix" with "fresh production import from a new bundle."** Same source bundle, fixed parser. Mixing in a new bundle would invalidate the bar-count Δ check (AC #4).
- **Don't write to the user's default `NAUTILUS_PATH` mid-task.** The same lesson from Story 3.3 — `e2e-test` IS the default `NAUTILUS_PATH`, so any partial parquet leak (e.g., from running a backtest mid-import) would corrupt the re-import in progress. Quiesce all consumers before Task 2.
- **Don't widen scope to non-FirstRate catalogs.** If the operator has CSV-imported or IBKR-imported catalogs alongside `e2e-test`, those are not affected by the FirstRate parser bug. Touching them is out of scope.

### Testing Requirements

- No new tests for the operational re-import (it's a one-shot procedure).
- If Option A is chosen for Task 5, one unit test on the new `data_quality_flag` column's default + Pydantic model serialization is required. One component test on the run-detail UI banner is required.
- Story 3.4's existing parity harness (Task 7) is the load-bearing post-import test.

### References

- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-09.md` action C2] — retro decision driving this story; this is the BLOCKING gate for Epic 4.
- [Source: `_bmad-output/implementation-artifacts/3-4-ibkr-vs-firstrate-parity-comparison.md` Completion Notes "Status: resolved — root cause was a timezone bug"] — full diagnosis path and fix details.
- [Source: `src/services/firstrate/parsers/firstrate_csv_parser.py:_parse_timestamp`] — the fix location.
- [Source: `src/services/firstrate/source_probe.py:_parse_timestamp`] — parallel fix in the source classifier.
- [Source: `tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py`] — Task 7's load-bearing parity verification.
- [Source: `~/.claude/projects/-Users-allay-dev-Trading-ntrader/memory/project_firstrate_import_patterns.md`] — operator knowledge on the FirstRate import workflow (subset import trick, required env vars, gotchas).
- [Source: `~/.claude/projects/-Users-allay-dev-Trading-ntrader/memory/project_e2e_catalog_setup.md`] — current `e2e-test` inventory; refresh in Task 8.1 if counts move.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-05-09 | Bob (SM) | Story 3.6 created via `bmad-create-story 3-6`. Status: backlog → ready-for-dev. **BLOCKING gate before Epic 4.** Re-imports `e2e-test` (5 tickers × 4 timeframes) through the timezone-corrected parser; flags pre-fix `backtest_runs` rows. |
