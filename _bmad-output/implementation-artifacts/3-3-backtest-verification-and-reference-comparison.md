# Story 3.3: Backtest Verification & Reference Comparison

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want an end-to-end backtest against imported FirstRate Stocks data plus a reproducible reference comparison against the existing CSV loader on the AAPL 2018 1-MINUTE dataset,
So that I can prove the FirstRate catalog produces consistent, trustworthy backtest results before adopting it as the default data source for Phase 1 and beyond.

## Scope & Non-Goals

**In scope:**

- An end-to-end backtest run against `e2e-test` catalog (FirstRate-imported AAPL Stocks data) using `sma_crossover` with whole-share equity sizing — completes without errors, persists a `BacktestRun` row with `data_source="catalog:e2e-test"`, and is viewable in the existing web UI run-detail page.
- A **reproducible reference-comparison harness** comparing two backtest runs of `sma_crossover` against the same logical dataset (AAPL 2018-01-01 → 2018-12-31, 1-MINUTE) loaded via two different paths:
  - **Path A (legacy CSV):** `data/AAPL_1min.csv` filtered to 2018 → imported via `ntrader data import --csv … --symbol AAPL --venue NASDAQ --bar-type 1-MINUTE-LAST` into the default `NAUTILUS_PATH` catalog → backtested without `--catalog` flag.
  - **Path B (FirstRate):** AAPL Stocks bundle imported into `e2e-test` via `ntrader import --format firstrate --catalog e2e-test …` → backtested with `--catalog e2e-test`.
- A **comparison report** (text + structured JSON) documenting bar count delta, trade count delta, total PnL delta, win-rate delta, and bar-level price deltas across the two runs, with explicit tolerance thresholds: bar count Δ ≤ 0.5%, trade count exact match, total PnL Δ ≤ 0.1%.
- An **assertion-driven integration test** (forked, gated on `E2E_CATALOG_AVAILABLE=1` + `data/AAPL_1min.csv` presence) that runs both backtests through the same `BacktestOrchestrator` invocation path, captures both `BacktestResult` objects, and fails CI-locally on any tolerance breach.
- A **manually-runnable verification script** (`scripts/verify_aapl_2018_reference.py` or equivalent) that drives both backtests end-to-end via the public CLI and writes its evidence (results table, comparison JSON, deltas) to `/tmp/story-3-3-evidence/`.
- **Asset-appropriate position sizing confirmation** — formal AC + test that `sma_crossover._calculate_position_size` produces `Quantity.from_int(...)` (whole shares) when `instrument.size_precision == 0` (equities) and `Quantity.from_str(f"{q:.{size_precision}f}")` (fractional) when `size_precision > 0` (crypto). No new sizing code; this is a verification-only AC documenting that the architecture already supports per-asset-class sizing through `instrument.size_precision`.
- **Backtest error-surfacing AC** — verify that data-related errors (missing ticker, empty window, unknown catalog from Story 3.1 Tasks 2.2/2.3/AC #6) and engine-related errors (strategy crash, malformed instrument) surface with distinguishable messages in both CLI and web UI flows. No new error types — confirm the existing `DataNotFoundError` + `ValueError` paths from Story 3.1 + existing strategy exceptions reach the right surface unchanged.
- A **retrospective entry** in `_bmad-output/implementation-artifacts/epic-3-retro-2026-04-XX.md` once the story closes, summarising the comparison numbers, tolerance breaches (if any), known-explainable deltas (FirstRate dedupe, CSV decimal→float), and follow-ups for Epic 4 / Phase 2 asset classes.

**Out of scope:**

- **Multi-asset-class sizing implementations** for futures (contract lots), FX (lot-based), crypto (already supported via `size_precision > 0` in existing code), or indices. Phase 1 ships Stocks only — futures/FX/crypto/indices ship in later phases. AC #5 documents the pattern; implementations are deferred per epic FR coverage map and `_bmad-output/planning-artifacts/prd.md` MVP Strategy.
- **Multi-instrument backtests via the comparison harness.** R11 (deferred from 3.1) — `BacktestRequest.symbol` is scalar, no input channel for multi-ticker. Comparison runs single-ticker (AAPL).
- **A new web UI** for viewing the comparison results. The reference comparison is a developer/operator concern; results land as static JSON + console output. Future story may add a "Compare Runs" UI.
- **Tightening tolerances** beyond the agreed thresholds (P1 in `epic-2-retro-2026-04-19.md` line 97). Bar count 0.5%, trade count exact, PnL 0.1% are the agreed values; deviations require a follow-up retro discussion, not a code change in this story.
- **Re-importing or re-validating the `e2e-test` catalog itself.** Story 3.3 trusts the catalog as already-imported; re-import is Epic 1's territory (Stories 1-1 through 1-7, all done).
- **Performance benchmarking** of FirstRate vs CSV-loader runtime / memory. Phase 1 has no performance target for backtests (NFR7 covers import only). Document any wall-clock numbers as informational, not as a pass/fail gate.
- **A `BacktestNode` / `BacktestRunConfig` adoption.** Same disclaimer as 3.1 — direct `BacktestEngine` API stays.
- **Modifying `csv_loader.py`** or any existing CSV-import path. The legacy path must be drivable as-is. If `data/AAPL_1min.csv` semantics drift (e.g., new conflict-mode), that's out-of-scope for 3.3.
- **Backfilling historical AAPL backtests** for prior PRs. Reference comparison is forward-looking — once green, the FirstRate catalog is "trusted" going forward.

## Acceptance Criteria

1. **End-to-end FirstRate backtest succeeds** — **Given** the `e2e-test` catalog with AAPL imported (verified: 1-MINUTE bars exist for the AAPL Stocks bundle, `catalog_instruments.bar_count_minute > 0`), **When** the operator runs `ntrader backtest run --strategy sma_crossover --symbol AAPL --start 2018-01-01 --end 2018-12-31 --timeframe 1-MINUTE --catalog e2e-test` (persistence enabled), **Then** the command completes with exit code 0, prints a `BacktestResult` summary table, persists a `BacktestRun` row with `data_source="catalog:e2e-test"` and `symbol="AAPL"`, **And** the run is viewable at `/backtests/{run_id}` in the web UI with metrics rendering correctly (no template errors, no zero-row metrics rendered as `null`).

2. **Reference comparison harness runs both paths and emits a structured report** — **Given** both `data/AAPL_1min.csv` (filtered to 2018) and the `e2e-test` catalog AAPL data are available, **When** the operator runs the verification script (`uv run python scripts/verify_aapl_2018_reference.py` or the equivalent integration-test entrypoint), **Then** the script (a) imports the legacy CSV path into a temp catalog under `NAUTILUS_PATH` (or uses an existing one if `--skip-import` is passed), (b) runs `sma_crossover` against both data sources with identical strategy params (`fast_ema_period=10`, `slow_ema_period=20`, `position_size_pct=10`), (c) writes a structured comparison JSON to `/tmp/story-3-3-evidence/aapl_2018_comparison.json` containing both runs' totals, deltas, and tolerance verdicts, **And** prints a side-by-side comparison table to stdout.

3. **Tolerance verdicts: bar count Δ ≤ 0.5%** — **Given** the comparison report, **When** the bar counts of both data sources are compared, **Then** the absolute relative difference `abs(legacy_bars - firstrate_bars) / max(legacy_bars, firstrate_bars)` is ≤ 0.005 (0.5%). **Documented explanation:** FirstRate's parser deduplicates bars by timestamp (commit `6efb82b`); the legacy CSV loader does not. Up to 0.5% bar count difference is expected and acceptable.

4. **Tolerance verdicts: trade count exact** — **Given** the comparison report, **When** the trade counts of both backtests are compared, **Then** `legacy_trades == firstrate_trades` exactly (zero tolerance). **Rationale:** `sma_crossover` is deterministic given identical bar inputs; if trade counts diverge, the bar series are not identical at the timestamps where trades fire, which is a signal worth investigating, not a tolerance. **If this AC fails, the comparison fails — do NOT pad the tolerance to make it pass.**

5. **Tolerance verdicts: total PnL Δ ≤ 0.1%** — **Given** the comparison report, **When** the total PnL of both backtests are compared, **Then** the absolute relative difference `abs(legacy_pnl - firstrate_pnl) / max(abs(legacy_pnl), abs(firstrate_pnl), 1.0)` is ≤ 0.001 (0.1%). **Documented explanation:** legacy CSV uses `Decimal → float` conversion; FirstRate preserves source decimal precision. 0.1% PnL drift on multi-thousand-trade equity strategies is expected.

6. **Tolerance breach behavior is loud** — **Given** any one of AC #3 / #4 / #5 fails, **When** the comparison harness completes, **Then** the script exits with a non-zero exit code, the comparison JSON sets `tolerance_passed: false` for the breaching metric and `overall_passed: false`, the stdout output prints a red `❌ TOLERANCE BREACH:` line per failed metric with the actual delta and the threshold, **And** the integration test (`tests/integration/core/test_aapl_2018_reference_comparison.py`) marks the test as `FAIL` with a clear pytest assertion message — the test does NOT silently pass on tolerance breach.

7. **Comparison runs use the same strategy class, the same params, and the same time window** — **Given** the harness invokes both backtests, **When** the strategy configs are compared, **Then** both invocations use exactly: `strategy=sma_crossover`, `fast_ema_period=10`, `slow_ema_period=20`, `position_size_pct=Decimal("10")`, `start=2018-01-01T00:00:00Z`, `end=2018-12-31T23:59:59.999999Z`, `timeframe=1-MINUTE`, `initial_balance=$1,000,000 USD`, `instrument_id="AAPL.NASDAQ"`. The harness asserts (or builds) the `BacktestRequest` for each path so these values are byte-identical strings — diverging configs are a setup bug, not a comparison input.

8. **Asset-appropriate position sizing — equity confirmation** — **Given** an AAPL 1-MINUTE backtest via `sma_crossover` against the FirstRate catalog, **When** `_calculate_position_size` runs on a typical bar (close ~$165), **Then** the resulting `Quantity` is constructed via `Quantity.from_int(shares)` (whole-share path at `src/core/strategies/sma_crossover.py:160-161`), **And** for a $100K notional the share count is `int($100K / $165) = 606` shares (no fractional digits in `str(quantity)`). Verified by inspecting the `BacktestRun.fills` (or `BacktestResult.total_trades` + first-fill quantity log) — at least one fill in the persisted run shows `quantity` is a whole-number string with no decimal point.

9. **Asset-appropriate position sizing — architecture supports future asset classes** — **Given** the same `_calculate_position_size` implementation, **When** the instrument's `size_precision > 0` (e.g., crypto with precision=8), **Then** the existing branch `Quantity.from_str(f"{float(raw_qty):.{size_prec}f}")` produces fractional quantities at the instrument's native precision (path at `src/core/strategies/sma_crossover.py:154-157`). Verified by a unit test on `_calculate_position_size` with a mock instrument exposing `size_precision=8` — assert the returned `Quantity` has 8 fractional digits. **No code change** — this AC is a verification-only confirmation that the architecture is ready for futures / FX / crypto sizing in Phase 2 without structural changes.

10. **Data error vs engine error are distinguishable** — **Given** four explicit failure modes, **When** the operator triggers each via the CLI, **Then** the user-facing error message identifies the category:
    - **Missing ticker** (e.g., `--catalog e2e-test --symbol DOES_NOT_EXIST`) → `"Ticker 'DOES_NOT_EXIST' not found in catalog 'e2e-test'. …"` (Story 3.1 AC #7 reuses verbatim).
    - **Empty window** (e.g., `--catalog e2e-test --symbol AAPL --start 2030-01-01 --end 2030-12-31`) → `DataNotFoundError` message includes both the metadata range and the requested range (Story 3.1 AC #8).
    - **Unknown catalog** (e.g., `--catalog made-up-name`) → `ValueError("Unknown catalog 'made-up-name'. Available: [...]")` re-raised as `click.UsageError` with exit code 2 (Story 3.1 AC #6).
    - **Strategy / engine error** (induced by passing `fast_ema_period=0` or similar invalid config) → message contains `"strategy"` or the underlying exception class name (e.g., `ValueError`, `KeyError`) and is NOT confused for a data error. Exit code 1 (orchestration failure), distinct from the data-error exit code 2.
    AC validated by a parametrised CLI integration test.

11. **Web UI surfaces the same distinction** — **Given** each of the four failure modes from AC #10 triggered via POST `/backtests/run`, **When** the response renders, **Then** the inline error fragment (or run-detail error block) shows the same human-readable message — no stack traces leaked, no generic `"backtest failed"` placeholder. Verified by a component test that overrides `CatalogManager` / `MetadataService` / `BacktestOrchestrator.execute` to raise each exception in turn and asserts the rendered fragment contains the expected substring.

12. **Persisted result is consumable by existing reporting paths** — **Given** the AAPL 2018 1-MINUTE FirstRate-catalog backtest from AC #1 has been persisted, **When** the operator opens `/backtests/{run_id}` in the web UI, **Then** all existing metrics render correctly: total trades, total PnL (absolute + percentage), win rate, max drawdown (if computed), Sharpe ratio (if computed), final balance. **And** the configuration tab shows `data_source: catalog:e2e-test`, `catalog_name: e2e-test`, `symbol: AAPL`, `instrument_id: AAPL.NASDAQ`, `bar_type: AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL`. **And** the "Back to Explorer" link from Story 3.2 reconstructs `/explorer?catalog=e2e-test&ticker=AAPL&tf=1m` (note: `1-MINUTE` reverse-maps to `1m` per `_TIMEFRAME_RUN_FORM_TO_EXPLORER`).

13. **Comparison evidence is committed-or-archived for the retro** — **Given** AC #2 has produced `/tmp/story-3-3-evidence/aapl_2018_comparison.json` + a stdout log, **When** the story is closed, **Then** the contents are summarised inline in the story's `Dev Agent Record → Completion Notes List` (key totals + deltas + tolerance verdicts) AND referenced from the Epic 3 retrospective. Raw evidence files are NOT committed to git (they may contain absolute paths and personal data dir references) — the summary in the story file IS the durable artifact.

14. **Quality gates** — **Given** the full quality suite, **When** the operator runs `make format && make lint && make typecheck && make test-unit && make test-component && make test-integration`, **Then** all pass (matching pre-3.3 baselines: 811 unit / 608 component as of `34cb745`; integration tier may have pre-existing flakiness — new 3.3 tests must pass under `--forked` in isolation). No new ruff / mypy errors. The new integration test `test_aapl_2018_reference_comparison.py` is properly gated on `E2E_CATALOG_AVAILABLE=1` + CSV presence so CI without local data still goes green.

15. **No regressions in 3.1 / 3.2 paths** — All existing tests under `tests/component/api/test_run_backtest_routes.py`, `tests/component/api/test_chart_panel_routes.py`, `tests/component/services/firstrate/test_catalog_backtest_loader.py`, `tests/integration/core/test_backtest_catalog_integration.py`, and `tests/component/test_backtest_commands.py` continue to pass without modification. Story 3.3 adds tests; it does not edit Story 3.1 / 3.2 production code.

## Tasks / Subtasks

- [x] Task 1: Verification script — drive both backtests end-to-end (AC: #1, #2, #7)
  - [x] 1.1 Failing test in `tests/integration/core/test_aapl_2018_reference_comparison.py::test_harness_runs_both_paths` (gated on `E2E_CATALOG_AVAILABLE=1` AND `Path("data/AAPL_1min.csv").exists()` — `pytest.skip` otherwise). Asserts the harness function returns a `ComparisonReport` with both `legacy_run` and `firstrate_run` populated and tolerances evaluated.
  - [x] 1.2 Create `scripts/verify_aapl_2018_reference.py` (NEW) — Click-based CLI script with flags `--legacy-csv` (default `data/AAPL_1min.csv`), `--firstrate-catalog` (default `e2e-test`), `--output-dir` (default `/tmp/story-3-3-evidence/`), `--skip-import` (skip CSV→catalog re-import if the legacy data is already in NAUTILUS_PATH). Document usage in the script's docstring; no `README.md` updates required (CLAUDE.md rule: don't create docs unless asked).
  - [x] 1.3 Implement `_run_legacy_backtest(...)` — invokes the existing `MinimalBacktestRunner` or `BacktestOrchestrator` path against the default catalog (post-import) using `sma_crossover` with the canonical params (AC #7). Returns `(BacktestResult, dict)` where the dict captures bar count, time window, instrument id used.
  - [x] 1.4 Implement `_run_firstrate_backtest(...)` — invokes the same orchestrator path but with `BacktestRequest.catalog_name="e2e-test"`. Returns the same `(BacktestResult, dict)` shape.
  - [x] 1.5 Implement `_filter_csv_to_2018(src_csv: Path, dst_csv: Path)` — reads `data/AAPL_1min.csv`, filters rows where `2018-01-01T00:00:00 <= timestamp <= 2018-12-31T23:59:59`, writes to `dst_csv`. Use pandas (already in `csv_loader.py` deps). Skip if `dst_csv` already exists and `--skip-import` is set.
  - [x] 1.6 Implement `_import_legacy_csv(...)` — calls `CSVLoader(conflict_mode="overwrite").load_file(filtered_csv, "AAPL", "NASDAQ", "1-MINUTE-LAST")`. Uses `overwrite` so the script is idempotent across runs. Logs the bar count written.
  - [x] 1.7 Wire it all up under a `main()` that: (a) filters CSV → temp file under `/tmp/story-3-3-evidence/AAPL_2018.csv`, (b) imports to default catalog (skippable), (c) runs both backtests sequentially (NOT in parallel — both create `BacktestEngine` instances; per CLAUDE.md Gotcha #4 each must complete + dispose before the next starts), (d) builds the `ComparisonReport` (Task 2), (e) writes JSON, (f) prints table, (g) exits 0/1 based on tolerance verdict.

- [x] Task 2: `ComparisonReport` dataclass + tolerance evaluator (AC: #3, #4, #5, #6)
  - [x] 2.1 Failing unit test in `tests/unit/comparison/test_comparison_report.py::test_tolerance_evaluator` (NEW) — parametrised over (a) all-pass case, (b) bar count breach, (c) trade count breach, (d) PnL breach, (e) all breaches. Asserts `overall_passed` is the AND of per-metric `tolerance_passed`, asserts the breach reasons are populated correctly.
  - [x] 2.2 Create `src/models/comparison_report.py` (NEW) — Pydantic model `ComparisonReport` with fields: `legacy_metrics: BacktestResultSummary`, `firstrate_metrics: BacktestResultSummary`, `bar_count_delta: float`, `trade_count_delta: int`, `pnl_delta_pct: float`, `bar_count_passed: bool`, `trade_count_passed: bool`, `pnl_passed: bool`, `overall_passed: bool`, `notes: list[str]`, `generated_at: datetime`, `dataset: str` (e.g., `"AAPL_2018_1-MINUTE"`). Plus a nested `BacktestResultSummary` with `total_trades`, `total_pnl`, `total_pnl_percentage`, `final_balance`, `bar_count`, `instrument_id`, `data_source`. Use `model_dump_json(indent=2)` for the persistence shape.
  - [x] 2.3 Implement `evaluate_tolerance(legacy, firstrate, *, bar_count_tol=0.005, trade_count_tol=0, pnl_tol=0.001) -> ComparisonReport`. Add `notes` strings explaining any breach. Bar-count formula uses `max(...)` denominator (handles zero-bar edge case). PnL formula uses `max(abs(...), 1.0)` denominator (handles near-zero PnL).
  - [x] 2.4 Place tolerance constants as module-level `Final[...]` so they're discoverable for future calibration. Cite `epic-2-retro-2026-04-19.md:97` (P1) in the docstring as the source of the values.

- [x] Task 3: Stdout reporter (AC: #2, #6)
  - [x] 3.1 Failing test `tests/unit/comparison/test_report_renderer.py::test_renderer_red_on_breach` — feeds a tolerance-breach `ComparisonReport`, asserts the rendered stdout contains `❌ TOLERANCE BREACH` and the breach metric name. Asserts a passing report contains `✅` and no `❌`.
  - [x] 3.2 Implement `render_comparison_table(report: ComparisonReport) -> str` using `rich.table.Table` (already a dep — see `_backtest_helpers.display_backtest_results`). Columns: Metric / Legacy CSV / FirstRate / Δ (abs) / Δ (%) / Tolerance / Verdict.
  - [x] 3.3 Wire into the script's `main()` via `console.print(render_comparison_table(report))`.

- [x] Task 4: Integration test wiring (AC: #1, #2, #14, #15)
  - [x] 4.1 Failing test `tests/integration/core/test_aapl_2018_reference_comparison.py::test_full_reference_comparison_within_tolerance` — calls the harness function (extracted from the script's `main()`), asserts `report.overall_passed is True`, asserts each delta is within its threshold. **Gated on `E2E_CATALOG_AVAILABLE=1` + `Path("data/AAPL_1min.csv").exists()` — `pytest.skip` cleanly when missing**. Also asserts `data/AAPL_1min.csv` is readable and contains 2018 timestamps (a fast pre-flight to fail fast on a corrupt fixture).
  - [x] 4.2 Add `@pytest.mark.integration` marker — runs under `make test-integration` (`--forked`).
  - [x] 4.3 Add a sibling test `test_harness_skips_when_evidence_dir_unwritable` — feed an unwritable output dir, assert the harness raises a clear `OSError` (not a silent fail) — this is a script-quality guard, not a tolerance test.
  - [x] 4.4 Confirm `tests/integration/conftest.py::integration_cleanup` (the double `gc.collect()` after engine dispose) is inherited.

- [x] Task 5: Asset-appropriate sizing verification (AC: #8, #9)
  - [x] 5.1 Failing unit test `tests/unit/strategies/test_sma_crossover_position_sizing.py::test_equity_whole_shares` — instantiates `SMACrossover` with a mocked instrument exposing `size_precision=0` and a current bar at close=$165, asserts the `_calculate_position_size()` return type is `Quantity` and `str(qty)` contains no `.` (no decimal point). Use the existing test patterns in `tests/unit/strategies/`.
  - [x] 5.2 Failing unit test `test_crypto_fractional_shares` — same setup but `size_precision=8`, close=$60000, asserts `str(qty)` ends with exactly 8 fractional digits.
  - [x] 5.3 No production code change. The two tests are pure verification of `src/core/strategies/sma_crossover.py:140-161` behavior. They serve as a regression guard against accidental refactor of the sizing branch.
  - [x] 5.4 Add a third test `test_equity_minimum_shares` — verify the `max(int(raw_qty), 1)` floor at line 160 (e.g., a $0.01 portfolio + $200 stock still produces `Quantity.from_int(1)`, not `Quantity.from_int(0)`).

- [x] Task 6: Error message distinguishability — CLI (AC: #10)
  - [x] 6.1 Failing component test `tests/component/test_backtest_commands.py::TestErrorMessageCategorization::test_missing_ticker_message` — uses `CliRunner` with mocked `CatalogManager` returning a valid `ParquetDataCatalog` and mocked `MetadataService.get_instrument_sync` returning `None`. Asserts `result.exit_code == 1` (data error) and the printed output contains both the ticker name and the catalog name verbatim.
  - [x] 6.2 `test_empty_window_message` — mocked `catalog.bars(...)` returns `[]`; asserts the printed output mentions both the requested window and the metadata range.
  - [x] 6.3 `test_unknown_catalog_message` — mocked `CatalogManager.resolve_catalog(name)` raises `FileNotFoundError(f"... Available: [...]")`. Asserts `result.exit_code == 2` (CLI usage error) and the message contains `"Unknown catalog"` and the available-catalogs list.
  - [x] 6.4 `test_strategy_error_message` — patches `BacktestOrchestrator.execute` to raise `ValueError("Invalid strategy config: fast_ema_period must be > 0")`. Asserts `result.exit_code == 1` AND the output does NOT contain the substring `"not found in catalog"` (i.e., not a false-positive data error).
  - [x] 6.5 If any of these patches reveal an existing CLI handler swallows the message into a generic "backtest failed" string, **fix the handler** in `src/cli/commands/backtest.py` or `_backtest_helpers.py` — that's the only allowed Story 3.1/3.2 production-code edit in this story. Document in Review Findings if applied. **Applied:** added `console.print(str(e))` before the generic data-error panel and a new `except ValueError` arm catching the named-catalog "Unknown catalog" string and re-raising as `click.UsageError` (exit 2).

- [x] Task 7: Error message distinguishability — Web UI (AC: #11)
  - [x] 7.1 Failing component test `tests/component/api/test_run_backtest_routes.py::TestErrorSurface::test_missing_ticker_inline_error` — overrides `MetadataService` dep, posts the run form, asserts the rendered HTMX fragment contains the ticker + catalog substrings and HTTP 200 (errors render inline, not as 5xx).
  - [x] 7.2 `test_empty_window_inline_error` — analogous, asserts metadata-range substring is present.
  - [x] 7.3 `test_unknown_catalog_inline_error` — overrides `CatalogManager`, asserts the available-catalogs list appears in the rendered fragment, HTTP 200.
  - [x] 7.4 `test_strategy_error_inline_error` — patches `BacktestOrchestrator.execute` to raise; asserts the fragment contains `"strategy"` or the `ValueError` message, HTTP 200.
  - [x] 7.5 Verify the error handler partial (`templates/partials/htmx_error_handler.html` from Story 2-4) renders these without `| safe` and without leaked stack traces. If any test surfaces a stack trace, **patch the handler** — minimal scope, document in Review Findings. **Applied:** added `DataNotFoundError` to the `(ValueError, RuntimeError)` except clause at `src/api/ui/backtests.py:309` so catalog-data errors render inline (HTTP 200) rather than 5xx. Stack traces are not leaked because `execution_error=str(e)` only surfaces the exception's message.

- [x] Task 8: Persistence + UI smoke (AC: #1, #12)
  - [x] 8.1 Run the AC #1 CLI command end-to-end (persistence enabled, NOT `--no-persist`). Capture stdout to `/tmp/story-3-3-evidence/aapl_2018_persisted_run.txt`. Assert (manually verify): exit 0, summary table printed, `BacktestRun` row created with `data_source="catalog:e2e-test"`. **Result:** run_id `5f7b4ff0-bb36-43ee-acb1-d2f513d71d35`, exit 0, `data_source=catalog:e2e-test`, 9,697 trades, 28.97% win rate, -33.54% return, 32s execution.
  - [x] 8.2 Use `agent-browser` per CLAUDE.md to: (a) navigate to `/backtests/{run_id}` from step 8.1, (b) snapshot — verify all metric cards populated (no `null` or `0` placeholders for fields that should be non-zero on a 12-month 1-MINUTE run with thousands of bars), (c) take a screenshot to `/tmp/story-3-3-evidence/run_detail_aapl_2018.png`, (d) click "Back to Explorer" and confirm it lands on `/explorer?catalog=e2e-test&ticker=AAPL&tf=1m` (AC #12 reverse-mapping). **Result:** All metrics populated (Sharpe -3.91, Sortino -4.43, Max Drawdown -69.96%, Volatility 20.60%, Profit Factor 0.86). Back-to-Explorer URL = `http://127.0.0.1:8000/explorer?catalog=e2e-test&ticker=AAPL&tf=1m` ✅.
  - [x] 8.3 Verify the configuration tab via `agent-browser` — expand the configuration section if collapsed, snapshot, assert the rendered DOM contains `catalog:e2e-test`, `AAPL.NASDAQ`, `AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL`. Screenshot to `/tmp/story-3-3-evidence/run_detail_config.png`. **Result:** `catalog:e2e-test` ✅, `AAPL.NASDAQ` ✅, `e2e-test` ✅. Bar type renders as `1-MINUTE-LAST` (the spec) instead of the full `AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL` Nautilus form — pre-existing Story 3.2 persistence shape stores the spec only. Noted for retro.
  - [x] 8.4 Verify SQL: `SELECT run_id, data_source, symbol, instrument_id FROM backtest_runs WHERE run_id = '<id>';` shows the expected values. Capture to `/tmp/story-3-3-evidence/run_db_row.txt`. **Result:** `data_source=catalog:e2e-test`, `instrument_symbol=AAPL`, `execution_status=success`. Note: actual schema uses `instrument_symbol` (not `instrument_id`) and `bar_type` lives in `config_snapshot` JSON — story AC text predates current schema.

- [x] Task 9: Quality gates + retro stub (AC: #14, #13)
  - [x] 9.1 `make format && make lint && make typecheck` — clean. (verified 2026-05-01)
  - [x] 9.2 `make test-unit` — **835 passed** (was 811, +24 new tests from Tasks 2/3/5).
  - [x] 9.3 `make test-component` — **616 passed**, 16 skipped (was 608, +8 new tests from Tasks 6/7).
  - [x] 9.4 `make test-integration` — new test gated on `E2E_CATALOG_AVAILABLE=1` + CSV presence; skips cleanly without env (3 skip messages). Forked-isolation harness verified end-to-end (legacy 27s + FirstRate 28s, ran without segfault or LogGuard panic).
  - [x] 9.5 Captured comparison deltas inline in `### Completion Notes List` (see below).
  - [x] 9.6 Retro stub deferred — Epic 3 retro will be run via `bmad-retrospective` skill after this story merges. Findings recorded in completion notes.

### Review Findings

_To be populated by code-review after Tasks 1–9 are complete. Inherit the three-layer review pattern from Stories 3.1 / 3.2 (Blind Hunter / Edge Case Hunter / Acceptance Auditor). Categorise findings as **Decision needed** / **Patch** / **Defer** matching prior stories._

## Dev Notes

### Architecture Compliance

- **No new BacktestOrchestrator changes.** Story 3.1 already plumbed `catalog_name` end-to-end through the orchestrator. Story 3.3 USES that plumbing, it does not extend it. The harness builds `BacktestRequest` objects (or invokes the CLI which builds them) — no edits to `src/core/backtest_orchestrator.py`, `src/services/firstrate/backtest_loader.py`, or `src/models/backtest_request.py`. The only allowed prod edits are in CLI / web error-handling paths if Tasks 6/7 surface a swallowed error message — and even those should be minimal one-line fixes.
- **`BacktestEngine` is single-use** (CLAUDE.md Gotcha #4). The harness runs both backtests **sequentially**, not in parallel — each completes + disposes before the next starts. Do NOT attempt `asyncio.gather([_run_legacy(), _run_firstrate()])` even though the script is async-friendly: shared C state corrupts if two engines exist concurrently in the same process. Use `--forked` for the integration test so even pytest-xdist parallelism puts each test in its own process.
- **LogGuard discipline** (CLAUDE.md Gotcha #1). `BacktestEngineConfig(logging=LoggingConfig(bypass_logging=is_logging_initialized()))` — Story 3.1's existing pattern, no change. The harness invokes the orchestrator twice; the second invocation will see `is_logging_initialized() == True` and skip re-init. Confirm by running the harness once and checking for any `LiveLogger initialized twice` panic.
- **`asyncio.to_thread` wrap** (Epic 2 retro B2 — resolved 2026-04-19). The integration test runs through the same async loader path as Story 3.1; the wrap is already in place. No re-introduction of sync-in-async.
- **Strategy-config snapshot** (Story 3.2 review patch). `StrategyConfigSnapshot.bar_type` field exists; the persisted run's bar_type round-trips for the "Back to Explorer" reverse-map (AC #12).
- **Tolerances are config, not code-of-law.** The 0.5% / 0% / 0.1% values are Phase 1 agreement (`epic-2-retro-2026-04-19.md:97`). If a tolerance breach surfaces a real bug, fix the bug — do NOT widen the tolerance to make the test green. If the breach is explainable (new dedup behavior, new precision rounding), document in the comparison report's `notes:` array AND in the Epic 3 retro.

### Critical Implementation Details

**Reference dataset reproducibility:**

```python
# scripts/verify_aapl_2018_reference.py — sketch

REFERENCE_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
REFERENCE_END = datetime(2018, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
STRATEGY_PARAMS = {
    "fast_ema_period": 10,
    "slow_ema_period": 20,
    "position_size_pct": Decimal("10"),
    "trade_size": None,  # let _calculate_position_size derive
}
INITIAL_BALANCE_USD = Decimal("1000000")  # $1M
INSTRUMENT_ID = "AAPL.NASDAQ"
TIMEFRAME = "1-MINUTE"

# AC #7 — both runs MUST use these exact values; the harness asserts equality.
```

**Why two backtests share an instrument id:** both Path A (legacy CSV) and Path B (FirstRate catalog) use `AAPL.NASDAQ` because:
- Path A: `ntrader data import --csv … --venue NASDAQ` writes to `{INSTRUMENT_ID}/...` directories under `NAUTILUS_PATH` keyed on `AAPL.NASDAQ`.
- Path B: `catalog_instruments.nautilus_id` for AAPL was populated from `company_profiles.csv` during the `e2e-test` import — the imported FirstRate Stocks bundle ships AAPL on NASDAQ. Verify pre-flight: `SELECT nautilus_id FROM catalog_instruments WHERE catalog_name='e2e-test' AND ticker='AAPL';` returns `AAPL.NASDAQ`.

**Tolerance evaluator — boundary semantics:**

```python
# src/models/comparison_report.py
def evaluate_tolerance(
    legacy: BacktestResultSummary,
    firstrate: BacktestResultSummary,
    *,
    bar_count_tol: float = 0.005,
    trade_count_tol: int = 0,
    pnl_tol: float = 0.001,
) -> ComparisonReport:
    bar_max = max(legacy.bar_count, firstrate.bar_count, 1)
    bar_count_delta = abs(legacy.bar_count - firstrate.bar_count) / bar_max

    trade_count_delta = abs(legacy.total_trades - firstrate.total_trades)

    pnl_max = max(abs(legacy.total_pnl), abs(firstrate.total_pnl), 1.0)
    pnl_delta_pct = abs(legacy.total_pnl - firstrate.total_pnl) / pnl_max

    bar_count_passed = bar_count_delta <= bar_count_tol
    trade_count_passed = trade_count_delta <= trade_count_tol
    pnl_passed = pnl_delta_pct <= pnl_tol

    notes: list[str] = []
    if not bar_count_passed:
        notes.append(f"bar_count Δ={bar_count_delta:.4%} > {bar_count_tol:.2%}")
    if not trade_count_passed:
        notes.append(f"trade_count Δ={trade_count_delta} > {trade_count_tol}")
    if not pnl_passed:
        notes.append(f"pnl Δ={pnl_delta_pct:.4%} > {pnl_tol:.2%}")

    return ComparisonReport(
        legacy_metrics=legacy,
        firstrate_metrics=firstrate,
        bar_count_delta=bar_count_delta,
        trade_count_delta=trade_count_delta,
        pnl_delta_pct=pnl_delta_pct,
        bar_count_passed=bar_count_passed,
        trade_count_passed=trade_count_passed,
        pnl_passed=pnl_passed,
        overall_passed=bar_count_passed and trade_count_passed and pnl_passed,
        notes=notes,
        generated_at=datetime.now(timezone.utc),
        dataset="AAPL_2018_1-MINUTE",
    )
```

`max(..., 1)` and `max(..., 1.0)` denominators handle the degenerate "both runs returned zero bars / zero PnL" case without `ZeroDivisionError` and without falsely passing (delta 0/1 = 0 still trivially passes).

**Sequential engine runs:**

```python
# scripts/verify_aapl_2018_reference.py — main()

async def main() -> int:
    legacy_csv = filter_csv_to_2018(args.legacy_csv, evidence_dir / "AAPL_2018.csv")
    if not args.skip_import:
        await import_legacy_csv(legacy_csv)  # populates default catalog

    # SEQUENTIAL — one engine at a time per CLAUDE.md Gotcha #4
    legacy_summary = await run_legacy_backtest()
    firstrate_summary = await run_firstrate_backtest()

    report = evaluate_tolerance(legacy_summary, firstrate_summary)
    (evidence_dir / "aapl_2018_comparison.json").write_text(
        report.model_dump_json(indent=2)
    )
    console.print(render_comparison_table(report))

    return 0 if report.overall_passed else 1
```

The harness function (extracted from `main()`) is what the integration test imports and calls — keep `main()` thin, push logic into a sync-friendly `run_comparison_harness(...)` so the test can call it without asyncio shenanigans (or wrap with `asyncio.run()` inside the test).

**CSV filter to 2018:**

```python
def filter_csv_to_2018(src: Path, dst: Path) -> Path:
    df = pd.read_csv(src, parse_dates=["timestamp"])
    mask = (df["timestamp"] >= "2018-01-01") & (df["timestamp"] <= "2018-12-31 23:59:59")
    filtered = df.loc[mask].copy()
    if len(filtered) == 0:
        raise ValueError(f"No 2018 rows in {src} — verify the CSV covers 2018-01-01..2018-12-31")
    dst.parent.mkdir(parents=True, exist_ok=True)
    filtered.to_csv(dst, index=False)
    return dst
```

`data/AAPL_1min.csv` has 1.6M rows starting at 2010-01-04 — filtering to 2018 should leave ~98K rows per `epic-2-retro-2026-04-19.md:97`.

**Error categorisation seam (Tasks 6 & 7):** the existing exception types map cleanly to user messages already (Story 3.1 ACs #6, #7, #8). The Task is to ASSERT the messages reach the surface unchanged — not to design new error UX. If any handler discovered to swallow the message, fix it minimally (a single `except`-clause edit) and document in Review Findings.

### Existing Code to Reuse (DO NOT duplicate)

| What | Where | How to use |
|------|-------|------------|
| `CSVLoader.load_file` | `src/services/csv_loader.py:74-…` | Path A import — use `conflict_mode="overwrite"` so the harness is idempotent |
| `BacktestOrchestrator.execute` | `src/core/backtest_orchestrator.py` | Existing engine wrapper — used for both backtests |
| `BacktestRequest.from_cli_args(catalog_name=...)` | `src/models/backtest_request.py` | Build the FirstRate-path request |
| `BacktestRequest.from_cli_args()` (no catalog_name) | same | Build the legacy-path request — instrument id resolves through `_resolve_instrument_id` against the default `NAUTILUS_PATH` catalog |
| `load_backtest_data` | `src/cli/commands/_backtest_helpers.py:374-461` | Routes both paths — Story 3.1 added the `catalog_name` short-circuit |
| `MinimalBacktestRunner` | `src/core/backtest_runner.py` | Alternative single-instrument runner used by `ntrader run` / `reproduce`. The harness should use `BacktestOrchestrator` directly for parity with the CLI `backtest run` path — do NOT use `MinimalBacktestRunner` here, it has divergent persistence semantics |
| `_calculate_position_size` | `src/core/strategies/sma_crossover.py:120-169` | Verification target for AC #8 / #9 — read it, do not modify |
| `is_logging_initialized` + `set_nautilus_log_guard` | `src/utils/logging.py` | Existing LogGuard — already wired |
| `DataNotFoundError` | `src/services/exceptions.py` | Story 3.1 extended with `context` field — used by error-categorisation tests |
| `templates/partials/htmx_error_handler.html` | (Story 2-4) | Existing inline error renderer — verification target for AC #11 |
| `BacktestResult` | `src/models/backtest_result.py` | The harness consumes the orchestrator's return value to populate `BacktestResultSummary` |
| `display_backtest_results` (rich.Table pattern) | `src/cli/commands/_backtest_helpers.py:658-729` | Reference for `render_comparison_table` — reuse the styling vocab |
| `agent-browser` skill | CLAUDE.md § UI Testing | Task 8 web smoke — same pattern as Story 3.2 Task 7 |
| `MetadataService.get_instrument_sync` | `src/services/firstrate/metadata_service.py` | Pre-flight check that AAPL.NASDAQ exists in `e2e-test` (Task 1.1) |
| `CatalogInstrumentRepository` (sync + async) | `src/db/repositories/` | Reference for the SQL pre-flight in Task 1.1 |
| `tests/integration/conftest.py::integration_cleanup` | existing | Inherited fixture — no new fixture work |

### File Structure

**New files:**

- `scripts/verify_aapl_2018_reference.py` — Click CLI script driving the reference comparison end-to-end. Single entry point per CLAUDE.md scripts pattern. Keep under 200 lines (~150 ideal).
- `src/models/comparison_report.py` — `ComparisonReport` Pydantic model + `BacktestResultSummary` + `evaluate_tolerance(...)` + module-level tolerance constants. Keep under 100 lines.
- `tests/unit/comparison/__init__.py` (empty) + `tests/unit/comparison/test_comparison_report.py` — tolerance evaluator unit tests.
- `tests/unit/comparison/test_report_renderer.py` — stdout renderer unit tests.
- `tests/unit/strategies/test_sma_crossover_position_sizing.py` — sizing AC verification (if a sibling file already exists, ADD the three tests there instead — check first).
- `tests/integration/core/test_aapl_2018_reference_comparison.py` — gated full-comparison integration test.

**Modified files (test-only unless Tasks 6/7 surface a real handler bug):**

- `tests/component/test_backtest_commands.py` — add `TestErrorMessageCategorization` class (Task 6).
- `tests/component/api/test_run_backtest_routes.py` — add `TestErrorSurface` class (Task 7).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `3-3-…` → `review` after dev complete (the workflow handles `→ ready-for-dev`).

**No production code changes expected.** Tasks 6/7 may surface a single one-line handler fix in `src/cli/commands/backtest.py` or `src/api/ui/backtests.py` — document it explicitly in Review Findings if applied, otherwise the diff is tests + harness + new model.

### Project Structure Notes

- `scripts/` directory exists per `scripts/build-css.sh` (CLAUDE.md). Add the verification script there.
- `src/models/` already hosts `backtest_request.py`, `backtest_result.py`, `data_load_result.py` (Story 3.1 review patch) — `comparison_report.py` joins them as a sibling.
- `tests/unit/comparison/` is a NEW subfolder — ensure `__init__.py` is present so pytest discovers the tests.
- `tests/integration/core/` already exists from Story 3.1 — new integration test file lives there next to `test_backtest_catalog_integration.py`.

### Testing Requirements

- **TDD non-negotiable.** Every task starts with a failing test (CLAUDE.md § Foundational Rules).
- **Test pyramid distribution:**
  - **Unit** (`make test-unit`): tolerance evaluator (Task 2), report renderer (Task 3), sizing verification (Task 5) — pure logic / Pydantic / dataframes. ~6-10 new tests.
  - **Component** (`make test-component`): error-message categorisation in CLI + web (Tasks 6, 7) — `TestClient` + `CliRunner` + dependency-overrides. ~8-10 new tests.
  - **Integration** (`make test-integration`, `--forked`): full reference comparison harness (Task 4) — single test, gated on `E2E_CATALOG_AVAILABLE=1` + CSV presence.
  - **E2E / manual** (Task 8): `agent-browser` web UI smoke + CLI smoke + DB query — evidence captured to `/tmp/story-3-3-evidence/`.
- **Markers:** `@pytest.mark.integration` for the gated test. No new markers.
- **Gating:** `pytest.skip(..., reason="…")` when env vars or files are absent — CI without local data still goes green. Same pattern as Story 3.1 Task 7.1's `E2E_CATALOG_AVAILABLE=1` gate.
- **Forked execution:** mandatory for the integration test (Nautilus C extensions corrupt shared state across `fork()` per CLAUDE.md Gotcha #2). `make test-integration` already wires `--forked`.
- **Quality gates:** `make format && make lint && make typecheck && make test-unit && make test-component && make test-integration` — all clean. Baseline: 811 unit + 608 component + N integration; new tests add ~14-20 unit/component tests + 1 integration test.

### Previous Story Intelligence

**From Story 3.1 (catalog integration with BacktestEngine) — directly relevant:**

- **Reference-parity smoke test exists** at `tests/component/services/firstrate/test_catalog_backtest_loader.py::test_aapl_2018_1min_identical_via_both_paths` (gated on `E2E_CATALOG_AVAILABLE=1`). That test asserts the **DATA layer** is byte-identical between the two paths. Story 3.3's reference comparison is the **PnL layer** — different concern, complementary test. Do NOT delete or modify the existing parity test.
- **`BacktestRequest.catalog_name` plumbing is complete** end-to-end — CLI flag, web form, persistence shape, validator. Story 3.3 just consumes it.
- **`DataLoadResult` lives at `src/models/data_load_result.py`** (Story 3.1 review patch) — neutral location, not under `cli/`. The harness imports from there.
- **Catalog-name validator** rejects non-`[A-Za-z0-9_-]` chars — `e2e-test` is valid (hyphen allowed).
- **`make test-integration` flakiness** is pre-existing (17 failed on main, 15 failed on the 3.1 branch) — caused by Nautilus C-extension parallelism with xdist. Acceptable as long as 3.3's new tests pass under `--forked` in isolation. Capture both `make test-integration` summary AND `pytest tests/integration/core/test_aapl_2018_reference_comparison.py --forked` in completion notes.

**From Story 3.2 (explorer-to-backtest bridge) — relevant for AC #12:**

- **`StrategyConfigSnapshot.bar_type` field** exists (Story 3.2 review patch) — the persisted backtest's bar_type round-trips, enabling the "Back to Explorer" reverse-map. AC #12 verifies this works end-to-end for a 1-MINUTE FirstRate run.
- **Reverse timeframe map** `_TIMEFRAME_RUN_FORM_TO_EXPLORER` covers `1-MINUTE → 1m`, `5-MINUTE → 5m`, `1-HOUR → 1H`, `1-DAY → D`. AC #12 specifically asserts `1-MINUTE → 1m`.
- **Open-redirect guard** for `explorer_return` is in place — `agent-browser` test in Task 8.2 should NOT need to test that again.
- **`datetime.max.time()` end-of-day combine** (Story 3.2 review patch) — `end_date=2018-12-31` now maps to `2018-12-31 23:59:59.999999 UTC`, matching the harness's `REFERENCE_END` constant.

**From Story 1-7 (idempotent import) — D11 carryover relevant:**

- **D11: silent reimport on regressed source** is still open (`deferred-work.md`). For Story 3.3, this means: if AAPL's `date_range_end` in `catalog_instruments` has drifted below the actual Parquet end (because of a regressed reimport), the `--end 2018-12-31` window may request beyond the data. The empty-window error from Story 3.1 AC #8 surfaces this — but if the AAPL window is fully present, D11 is invisible to 3.3. **Pre-flight check:** before running the harness, query `SELECT date_range_start, date_range_end, bar_count_minute FROM catalog_instruments WHERE catalog_name='e2e-test' AND ticker='AAPL';` and assert the date range covers 2018-01-01 through 2018-12-31. If not, skip with a clear error.

**From Epic 2 retro (P1, P2, P3) — already resolved:**

- **P1 (reference dataset)** — RESOLVED. AAPL 2018-01-01..2018-12-31 1-MINUTE is the dataset.
- **P2 (sync-in-async)** — RESOLVED via `asyncio.to_thread` wrap. No new exposure.
- **P3 (D8 + D11 triage)** — D8 (dividends/splits noise in dry-run) is unrelated to 3.3. D11 — see above pre-flight.

### Git Intelligence (last 5 commits)

- `34cb745 feat(backtest): explorer-to-backtest bridge (Story 3-2)` — direct predecessor; persistence shape + reverse timeframe map established. AC #12 builds on this.
- `618836e feat(backtest): catalog integration with BacktestEngine (Story 3-1)` — `catalog_name` plumbing + DataNotFoundError context + parity smoke test. AC #1, #10 build on this.
- `10d8ba6 feat(retro): Epic 2 retrospective and Epic 3 blocker fixes` — P1/P2 resolved here; reference dataset confirmed; tolerance values picked.
- `56a7a73 fix(explorer): Story 2-4 code review patches` — XSS guards in HTMX fragments. AC #11 inherits this — verify the error-handler partial doesn't regress the autoescape posture.
- `31b126a feat(explorer): UX polish pass (Story 2-4)` — `templates/partials/htmx_error_handler.html` partial used by AC #11.

### Known Constraints / Carryover Debt

- **D11 (silent reimport)** — `deferred-work.md`. Pre-flight check in the harness mitigates for Story 3.3's specific run; full fix is out of scope.
- **F7 resolved** (Epic 2 retro B2) — `asyncio.to_thread` wraps in place.
- **R11 (multi-instrument input channel)** — `BacktestRequest.symbol` is scalar; multi-ticker comparison is out of scope. Single-ticker (AAPL) is the entire 3.3 surface.
- **`make test-integration` baseline flakiness** — pre-existing; new test must pass in isolation under `--forked`.
- **CSV loader's float→Decimal round-trip** — known source of the up-to-0.1% PnL drift. Documented in AC #5; not a bug to fix.
- **Tolerance values are Phase 1 agreement, not law** — see Architecture Compliance bullet above. Re-discuss in Epic 3 retro if new evidence justifies.

### Anti-patterns to Avoid

- **Pad the tolerances to make the test green.** If 3.3 reveals a real divergence (e.g., 1.5% PnL Δ caused by a parser bug), FIX the bug. Tolerance widening is a retro decision, not a dev decision.
- **Run both backtests concurrently** (`asyncio.gather`, `concurrent.futures`, `pytest-xdist` without `--forked`). CLAUDE.md Gotcha #4 — single-use engine, single instance per process.
- **Use `MinimalBacktestRunner` in the harness.** Diverges from the CLI `backtest run` persistence path; the user-facing test surface is `BacktestOrchestrator`. Use the orchestrator for both backtests.
- **Mock the catalog or strategy in the integration test.** The whole point of AC #4 is to exercise the real engine — mocks belong in unit / component tier (Tasks 2, 3, 5, 6, 7).
- **Modify `_calculate_position_size`.** It already does the right thing for equities + crypto. Tasks 5.1 / 5.2 / 5.4 are verification-only.
- **Add a new `data_source` enum value** for "comparison" or "reference". Stay on the existing `{catalog, ibkr, kraken, mock}` allow-list.
- **Commit `/tmp/story-3-3-evidence/` artifacts to git.** They contain absolute paths and personal data dir references. Summarise in the story file's completion notes (AC #13).
- **Block CI on the integration test.** The gated `pytest.skip` is mandatory — CI lacks the 21M-bar `e2e-test` catalog. Capture the test's expected gating logic in a comment so future maintainers don't "fix" the skip.
- **Run the harness without disposing the engine.** The script's `try/finally orchestrator.dispose()` is mandatory between the two runs.
- **Trust `catalog_instruments.date_range_end` blindly** (D11). Pre-flight check that the metadata range covers the requested window.
- **Re-import the `e2e-test` catalog as part of the harness.** That's Epic 1's territory; the harness assumes the catalog is already populated.
- **Fix unrelated bugs in this story.** If Tasks 6 / 7 surface a swallowed error message, fix it minimally and document. Anything broader belongs in a follow-up.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md#Story 3.3` (lines 654-680)] — canonical acceptance criteria.
- [Source: `_bmad-output/planning-artifacts/epics.md#FR Coverage Map` (lines 124-159)] — FR25, FR26, FR27 mapping to Epic 3.
- [Source: `_bmad-output/planning-artifacts/prd.md` (lines 295-297)] — FR25/FR26/FR27 verbatim.
- [Source: `_bmad-output/planning-artifacts/prd.md` (lines 116-120)] — Phase 1 backtest verification scope (whole-share equity sizing; reference comparison against existing CSV loader).
- [Source: `_bmad-output/planning-artifacts/architecture.md#Backtest Verification (FR25-FR27)` (lines 31, 565, 610)] — uses existing BacktestOrchestrator + CatalogManager, no new files.
- [Source: `_bmad-output/implementation-artifacts/3-1-catalog-integration-with-backtestengine.md`] — `catalog_name` plumbing (full file); AC #12 reference-parity smoke (Task 7).
- [Source: `_bmad-output/implementation-artifacts/3-2-explorer-to-backtest-bridge.md`] — bridge UI; reverse timeframe map; persistence shape with `bar_type`.
- [Source: `_bmad-output/implementation-artifacts/epic-2-retro-2026-04-19.md#5 Epic 3 Preview & Dependencies` (lines 82-106)] — P1 (AAPL 2018 dataset + tolerances), P2 (sync-in-async resolved), P3 (D11 carryover), P6 (BacktestEngine single-use).
- [Source: `_bmad-output/implementation-artifacts/deferred-work.md#D11`] — silent reimport on regressed source; drives the harness pre-flight check.
- [Source: `_bmad-output/implementation-artifacts/sprint-status.yaml`] — `3-3-…: backlog → ready-for-dev`.
- [Source: `docs/agent/nautilus.md#Engine Setup Sequence (Strict Order)`] — venue → instrument → data → strategy → run; multi-engine sequencing rules.
- [Source: `docs/agent/data-pipeline.md#DataCatalogService`] — legacy CSV path imports through this service.
- [Source: `CLAUDE.md#Critical Gotchas` (items 1, 2, 4)] — LogGuard, `--forked`, single-use engine.
- [Source: `CLAUDE.md#UI Testing (agent-browser)`] — Task 8 web smoke pattern.
- [Source: `CLAUDE.md#Foundational Rules`] — TDD non-negotiable; UV only; size limits.
- [Source: `src/core/strategies/sma_crossover.py:120-169`] — `_calculate_position_size` — verification target for AC #8 / #9.
- [Source: `src/services/csv_loader.py:34-…`] — legacy CSV path; harness Path A consumes it as-is.
- [Source: `src/cli/commands/data.py:28-…`] — `ntrader data import` CLI entry point; harness can shell out to it OR call `CSVLoader` directly.
- [Source: `src/core/backtest_orchestrator.py`] — single orchestrator; harness invokes it twice sequentially.
- [Source: `src/services/firstrate/backtest_loader.py`] — Story 3.1 catalog-backed loader; FirstRate path uses it via the orchestrator.
- [Source: `src/services/exceptions.py`] — `DataNotFoundError` with `context` field; AC #10 / #11 verification target.
- [Source: `src/models/backtest_request.py:259-346`] — `from_cli_args` shape; harness builds `BacktestRequest` for both paths.
- [Source: `src/models/backtest_result.py`] — `BacktestResult` dataclass; harness consumes it to populate `BacktestResultSummary`.
- [Source: `templates/partials/htmx_error_handler.html`] — error renderer; AC #11 verification target.
- [Source: `data/AAPL_1min.csv`] — legacy reference CSV; 1.6M rows starting 2010-01-04; filtered to ~98K rows for 2018.

## Dev Agent Record

### Agent Model Used

claude-opus-4-7 (Opus 4.7, 1M context)

### Debug Log References

- Initial harness run hung indefinitely on IBKR auto-fetch — root cause: `_load_catalog_data.covers_range(2018-01-01, 2018-12-31)` returns False because the legacy CSV's first 2018 bar is at 2018-01-02 (NYE is a non-trading day). Helper fell through to `fetch_or_load` → IBKR fetch → connection retries forever. **Fix:** legacy path now bypasses `load_backtest_data` and calls `catalog.bars(...)` + `_build_equity(...)` directly, mirroring the FirstRate path.
- First harness implementation imported the legacy CSV via `CSVLoader()` whose default `DataCatalogService()` resolves to `NAUTILUS_PATH=./data/catalogs/e2e-test` (per `.env`) — same directory as the FirstRate `e2e-test` catalog. With `conflict_mode="overwrite"` this would (and partially did) trample the FirstRate AAPL 1-MINUTE 2018 partition. **Fix:** legacy path now uses an isolated `output_dir/legacy_catalog/` via an explicit `DataCatalogService(catalog_path=...)`. The trampling damage from the first run was reverted (the partial overwrite created a sibling parquet file rather than rewriting the original; partial file removed manually).
- Equity-instrument precision drift: `_load_catalog_data` falls back to `create_test_instrument` (price_precision=2) when no instrument is in the catalog. Legacy CSV bars are precision=4. The engine's strict ``invalid bar.open.precision=N != self.instrument.price_precision=M`` check would fail. Resolved by reusing `_build_equity` from `src/services/firstrate/backtest_loader.py` to synthesise the Equity from `bars[0]`.

### Completion Notes List

**AC #1 — End-to-end FirstRate backtest persisted ✅**
- `run_id`: `5f7b4ff0-bb36-43ee-acb1-d2f513d71d35`
- `data_source`: `catalog:e2e-test`
- `instrument_symbol`: `AAPL`
- `execution_status`: `success`
- 9,697 trades, 28.97% win rate, -33.54% total return, $6,646,162.74 final balance (initial 10M; CLI default), 32.13s execution.
- Web UI run-detail page renders all metric cards (Sharpe -3.91, Sortino -4.43, Max Drawdown -69.96%, Volatility 20.60%, Profit Factor 0.86) — no `null` placeholders.

**AC #2-#6 — Reference comparison harness numbers**

```
{
  "legacy_metrics":  { "total_trades": 9731, "total_pnl": -329617.88, "bar_count": 165981 },
  "firstrate_metrics":{ "total_trades": 9745, "total_pnl": -336170.66, "bar_count": 165964 },
  "bar_count_delta":  0.00010 (0.01%) → ✅ pass (≤ 0.5%),
  "trade_count_delta":14         → ❌ breach (> 0 zero-tolerance),
  "pnl_delta_pct":    0.01949 (1.95%) → ❌ breach (> 0.10%),
  "overall_passed":   false
}
```

**Root cause investigation — these are different data sources, not a parser bug**

Direct catalog comparison at the same UTC nanosecond `1514885400000000000` (2018-01-02 09:30:00 UTC):
- Legacy CSV catalog: open=`40.6858`
- FirstRate `e2e-test` catalog: open=`39.8128`

These are different prices for the same UTC timestamp — `data/AAPL_1min.csv` and the FirstRate AAPL Stocks bundle disagree at the bar level. The CSV's timestamps are interpreted as UTC by `CSVLoader` while FirstRate already converts ET → UTC at parse time, but timezone offset alone doesn't explain the mismatch (no candidate offset reconciles the prices). The two sources appear to be from different data providers / different adjustment policies.

**Implication:** the integration test `test_full_reference_comparison_within_tolerance` will FAIL when run against this developer's local data — which is the AC #6 specified behaviour ("test does NOT silently pass on tolerance breach"). The harness, model, renderer, and tolerance evaluator are all working correctly. The breach is a true signal about the input data.

**Recommended retro action:** decide whether to (a) regenerate `data/AAPL_1min.csv` from the same source as the FirstRate Stocks bundle so the comparison is meaningful, (b) widen tolerances knowingly with a documented reason, or (c) drop the legacy-CSV reference comparison from the verification methodology and replace with a different parity check.

**AC #7 — Strategy params byte-identical between paths ✅**
Both runs use: `strategy=sma_crossover`, `fast_period=10`, `slow_period=20`, `position_size_pct=Decimal("10")`, `start=2018-01-01T00:00:00Z`, `end=2018-12-31T23:59:59.999999Z`, `bar_type_spec=1-MINUTE-LAST`, `instrument_id=AAPL.NASDAQ`. Asserted by the harness via the shared `_build_request(catalog_name=...)` helper.

**AC #8 / #9 — Position sizing verification ✅**
Six unit tests in `tests/unit/strategies/test_sma_crossover_position_sizing.py` exercise the existing `_calculate_position_size` branches against duck-typed mock instruments (`SimpleNamespace` because Nautilus Cython slots block `setattr` on `Strategy.cache`):
- `size_precision=0` (equity) → `Quantity.from_int(606)` for $1M × 10% / $165 close (no decimal point in `str(qty)`).
- `size_precision=8` (crypto) → `Quantity.from_str(...)` with exactly 8 fractional digits.
- `size_precision=6` (Kraken altcoin) → 6 fractional digits.
- Floor `max(int(raw_qty), 1)` verified at line 160.
- `current_bar=None` raises `ValueError("...without current bar...")`.
- No production-code change to `sma_crossover.py`.

**AC #10 — CLI error categorisation (4 cases) ✅**
- Missing ticker → `DataNotFoundError` message "Ticker 'X' not found in catalog 'Y'" surfaces verbatim, exit code 1.
- Empty window → `DataNotFoundError` message includes both metadata range and requested range, exit 1.
- Unknown catalog → `ValueError("Unknown catalog ...")` re-raised as `click.UsageError`, exit 2, available list shown.
- Strategy error → `ValueError` from orchestrator surfaces, exit 1, output does NOT contain `"not found in catalog"`.

**Production-code edit:** added `console.print(str(e))` before the generic `DATA_NOT_FOUND_NO_IBKR` panel so the exception's actual message reaches the user, and added a new `except ValueError as e` arm in the data-load try block at `src/cli/commands/backtest.py:286` re-raising "Unknown catalog ..." as `click.UsageError`. The previous handler swallowed both message classes into a generic IBKR-flavored panel.

**AC #11 — Web UI error surface (4 cases) ✅**
- All 4 cases (missing ticker, empty window, unknown catalog, strategy error) render inline at HTTP 200, no leaked stack traces.

**Production-code edit:** added `DataNotFoundError` to the `(ValueError, RuntimeError)` except clause at `src/api/ui/backtests.py:309`. Previously `DataNotFoundError` (which is `Exception`, not `ValueError`) leaked as a 5xx; now it renders inline like the other categories.

**AC #12 — Persistence + Back-to-Explorer reverse-map ✅ (with one caveat)**
- All UI metrics render correctly.
- "Back to Explorer" link reconstructs `/explorer?catalog=e2e-test&ticker=AAPL&tf=1m` exactly (1-MINUTE → 1m via `_TIMEFRAME_RUN_FORM_TO_EXPLORER` from Story 3.2).
- Caveat: configuration tab shows `bar_type` as `1-MINUTE-LAST` (the spec) instead of the full Nautilus `AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL` form mentioned in the AC text. The persisted shape (Story 3.1/3.2 design) stores the spec only; this is pre-existing behaviour, not introduced by 3.3. AC text predates the current schema.

**AC #13 — Comparison evidence ✅**
JSON: `/tmp/story-3-3-evidence/aapl_2018_comparison.json` (deltas summarised above).
Stdout log: `/tmp/story-3-3-evidence/aapl_2018_persisted_run.txt`.
Screenshots: `/tmp/story-3-3-evidence/run_detail_aapl_2018.png`, `run_detail_config.png`.
DB row dump: `/tmp/story-3-3-evidence/run_db_row.txt`.
Per AC instructions, raw evidence files are NOT committed.

**AC #14 — Quality gates ✅**
- `make format && make lint && make typecheck` — clean.
- `make test-unit` — 835 passed (was 811, +24 new from Tasks 2/3/5).
- `make test-component` — 616 passed, 16 skipped (was 608, +8 new from Tasks 6/7).
- `make test-integration` — new gated test skips cleanly without `E2E_CATALOG_AVAILABLE=1` + CSV. Pre-existing flakiness in other integration tests is carryover from Story 3.1.

**AC #15 — No regressions in 3.1/3.2 ✅**
All existing component tests in `test_run_backtest_routes.py` (31 total), `test_backtest_commands.py` (32 total) continue to pass. Only the new error-categorisation tests touched the existing files; existing assertions unchanged.

### File List

**New files:**
- `src/models/comparison_report.py` — `ComparisonReport`, `BacktestResultSummary`, `evaluate_tolerance` + tolerance constants.
- `src/services/comparison_renderer.py` — `render_comparison_table` (rich.Table renderer with ❌/✅ markers).
- `scripts/__init__.py` — empty marker so `scripts.*` is importable from tests.
- `scripts/verify_aapl_2018_reference.py` — Click CLI harness driving both backtests + ComparisonReport + JSON evidence.
- `tests/unit/comparison/__init__.py` — package marker.
- `tests/unit/comparison/test_comparison_report.py` — 14 tolerance-evaluator unit tests.
- `tests/unit/comparison/test_report_renderer.py` — 4 stdout-renderer unit tests.
- `tests/unit/strategies/__init__.py` — package marker.
- `tests/unit/strategies/test_sma_crossover_position_sizing.py` — 6 sizing verification tests.
- `tests/integration/core/test_aapl_2018_reference_comparison.py` — 3 gated integration tests (forked).

**Modified files:**
- `src/cli/commands/backtest.py` — DataNotFoundError handler now surfaces the exception's specific message; new `except ValueError` arm catches "Unknown catalog ..." in the data-load try block (AC #10 production fix).
- `src/api/ui/backtests.py` — added `DataNotFoundError` to the `(ValueError, RuntimeError)` except clause so catalog-data errors render inline at HTTP 200 (AC #11 production fix).
- `tests/component/test_backtest_commands.py` — added `TestErrorMessageCategorization` (4 tests).
- `tests/component/api/test_run_backtest_routes.py` — added `TestErrorSurface` (4 tests).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `3-3-...: ready-for-dev → in-progress → review`.

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-04-30 | Bob (SM) | Story 3.3 created. Status: backlog → ready-for-dev. |
| 2026-05-01 | Amelia (Dev) | Story 3.3 implemented. ComparisonReport + harness + CLI/web error categorisation + sizing verification + persisted AAPL 2018 backtest. Two minimal production fixes in CLI/web error handlers (AC #10/#11). Reference comparison reveals genuine bar-level data divergence between `data/AAPL_1min.csv` and FirstRate `e2e-test` AAPL — flagged for retro investigation. Status: in-progress → review. |
