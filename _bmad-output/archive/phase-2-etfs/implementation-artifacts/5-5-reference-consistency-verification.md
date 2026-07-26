# Story 5.5: Reference-Consistency Verification

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to compare an ETF backtest result against a reference path using the **existing** comparison tooling,
so that I can confirm the ETF catalog path produces results consistent with the reference — closing the Phase 2 verification loop — and that any deviation beyond the expected tolerance is reported rather than silently blessed.

## Acceptance Criteria

1. **Given** an ETF backtest result (an `IVV.ARCA` run through the **existing** `load_from_catalog` → `BacktestOrchestrator.execute` path, summarised into a `BacktestResultSummary`) and a **reference path** — a Stock run of the **identical** strategy/window/bar-series through the **identical** loader/orchestrator path — **When** the operator runs the comparison via the **existing** comparison tooling (`src/models/comparison_report.py::evaluate_tolerance`), **Then** the two results are compared and the per-metric deltas (`bar_count_delta`, `trade_count_delta`, `pnl_delta_pct`) plus verdicts are produced, and **any deviation beyond the agreed tolerance is reported** — `overall_passed is False` with populated `notes`, and `render_comparison_table` emits an `❌ TOLERANCE BREACH` line — never a silent pass. [Source: epics.md Story 5.5 AC1; comparison_report.py `evaluate_tolerance` lines 56-113; comparison_renderer.py `render_comparison_table` lines 83-99]
2. **Given** the ETF-path result and the Stock reference-path result are driven by a **byte-identical** input (same synthetic zigzag bar series, same `sma_crossover` params, same window) — the ETF and the Stock differ only in instrument identity (`IVV.ARCA` vs `REF.NASDAQ`) and asset class — **When** `evaluate_tolerance` runs, **Then** the comparison **passes**: `overall_passed is True`, `bar_count_delta == 0.0`, `trade_count_delta == 0`, and `pnl_passed is True` (PnL within the agreed `DEFAULT_PNL_TOL`). This **confirms the ETF catalog path is consistent with the reference**, closing the Phase 2 verification loop — the ETF path is asset-class-blind, so it reproduces the Stock reference within tolerance. A future asset-class branch in the loader/sizing/orchestrator that perturbed ETF numbers would break this test. [Source: epics.md Story 5.5 AC2; comparison_report.py `evaluate_tolerance`; Epic 5 whole-share/no-adapter thesis; Story 5.2/5.4 asset-class-blindness precedent]
3. **Given** this story verifies consistency via the **existing** comparison tooling and the **existing** catalog/orchestrator path, **When** its changes land, **Then** prior backtest numbers are unchanged, **no** production code is modified (tests + one additive synthetic fixture instrument only), **no** new comparison model / tolerance change is introduced (reuse `evaluate_tolerance`/`BacktestResultSummary`/`ComparisonReport` unchanged), **no** Alembic migration and **no** results-DB schema change are introduced, and **no live data source** is touched (bars generated in-process on a `tmp_path` catalog; no IBKR/Kraken/network). If a schema or tolerance change appears necessary, **STOP and surface it** rather than guessing. [Source: CLAUDE.md harness constraints; architecture.md line 565 "No new files"; comparison_report.py tolerance constants lines 22-24 are Phase 1 agreement — not to be re-tuned here]

## Tasks / Subtasks

- [x] **Task 1: Integration — ETF result is consistent with the Stock reference path (round-trip, passes)** (AC: #2, #3) — *extend `tests/integration/core/test_backtest_catalog_integration.py` (`--forked`, real `BacktestEngine`). TDD: write the `evaluate_tolerance(...).overall_passed` assertion first.*
  - [x] Add one **additive** synthetic instrument to the shared `synthetic_catalog` fixture: `cat.write_data(_make_zigzag_bars("REF.NASDAQ"))` — a reference **Stock** carrying the **identical** zigzag price series as the `IVV.ARCA` ETF (same generator, so byte-identical OHLCV; only the instrument id/venue differs). Additive only — do not alter the existing `AAPL`/`MSFT`/`SPY`/`IVV`/`TQQQ` writes (existing tests query those by ticker).
  - [x] Add a small `_summarise(result, bars, instrument)` helper that builds a `BacktestResultSummary` from an orchestrator result exactly as the existing harness does (`scripts/verify_aapl_2018_reference.py::_execute_and_summarise` lines 403-413): `total_trades=int(result.total_trades)`, `total_pnl=float(result.total_pnl or 0.0)`, `total_pnl_percentage=float(result.total_pnl_percentage or 0.0)`, `final_balance=float(result.final_balance)`, `bar_count=len(bars)`, `instrument_id=str(instrument.id)`, `data_source=<label>`. Keep it local to the test module (< 50 lines, no production surface).
  - [x] Add `test_etf_run_matches_stock_reference_within_tolerance`: over the window `2018-01-01 → 2018-03-01`, run **both** through the existing path — the **reference** Stock (`REF.NASDAQ`, `asset_class="STOCK"`) and the **candidate** ETF (`IVV.ARCA`, `asset_class="ETF"`) — each via `load_from_catalog` → `BacktestOrchestrator.execute(persist=False)`, disposing each orchestrator in a `finally` before the next (CLAUDE.md Gotcha #4 — never two engines live at once). Summarise each, assert **both** `total_trades > 0` (so the comparison is not vacuous), then `report = evaluate_tolerance(stock_summary, etf_summary, dataset="ETF_IVV_vs_Stock_REF")` and assert: `report.overall_passed is True`, `report.bar_count_delta == 0.0`, `report.trade_count_delta == 0`, `report.pnl_passed is True`. This is the "closes the loop" consistency proof (AC2).
  - [x] MUST use `persist=False` (no DB write — this story is result-comparison, not persistence), MUST generate bars in-process on the `tmp_path` catalog, and MUST NOT touch live data. `--forked` runner (`make test-integration`) for Nautilus C/Rust `fork()` isolation.

- [x] **Task 2: Integration — a deviation beyond tolerance IS reported (not silently blessed)** (AC: #1, #3) — *same module / fixtures*
  - [x] Add `test_reference_comparison_reports_deviation_beyond_tolerance`: build a real ETF `BacktestResultSummary` (reuse the Task 1 ETF run, or a compact fixed summary with `total_trades > 0`), then a **perturbed** candidate summary that breaches every tolerance — e.g. `bar_count` off by > `DEFAULT_BAR_COUNT_TOL` (0.5%), `total_trades` off by > `DEFAULT_TRADE_COUNT_TOL` (±50), and `total_pnl` off by > `DEFAULT_PNL_TOL` (0.5%). Assert `evaluate_tolerance(reference, perturbed)` returns `overall_passed is False`, each of `bar_count_passed`/`trade_count_passed`/`pnl_passed` is `False`, and `report.notes` is non-empty. Then assert `render_comparison_table(report)` contains `"❌ TOLERANCE BREACH"` and does **not** contain `"✅ ALL TOLERANCES PASSED"` — proving the operator/CI is told about the deviation (AC1's "any deviation beyond expected tolerance is reported").
  - [x] Keep this at the comparison-tooling tier (the existing `evaluate_tolerance` + `render_comparison_table`, unchanged) — do **not** invent a new tolerance path or renderer. Reuse the exact functions Story 3.3/3.4 shipped.

- [x] **Task 3: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new imports `evaluate_tolerance`, `BacktestResultSummary`, `render_comparison_table` must be used in the same edit; re-read files after edits).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: ≤6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Do not add any.
  - [x] `uv run pytest tests/integration/core/test_backtest_catalog_integration.py --forked -q` green (the existing 12 + the 2 new reference-consistency tests).
  - [x] Size limits: new functions `< 50` lines, line length `≤ 100`. All changes are additive test code + one fixture instrument. NOTE: the shared integration test **module** already exceeded the 500-line file guideline before this story (it entered at 801 lines from Stories 3.1/5.1–5.4) — this is pre-existing test-module debt, not introduced here; the new `_summarise`/`_run_and_summarise` helpers and test methods each obey the function limit, and the `TestEtfReferenceConsistency` test-grouping class follows the same >100-line precedent as 5.4's `TestNamedCatalogEtfPersistence` (the class-size guideline targets production domain classes, not pytest test-grouping classes).
  - [x] Confirm **no production change**, **no** Alembic migration, **no** `src/db/models/**` change, **no** comparison-tooling/tolerance change (AC3). `git diff --stat` should show only the one integration test file (+ this story file + sprint-status).

## Review Findings

Adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor). All three ACs confirmed covered by executing assertions; the diff is verifiably test-only (`git diff --stat` shows one integration test file — no `src/`, `alembic/`, `scripts/`, or `src/db/models/**` change — AC3 holds). Findings triaged:

- [x] [Review][Dismiss] "The consistency test (`ETF ≡ Stock`) is a tautology — identical bars must yield identical results" [Blind Hunter] — verified non-issue: the ONLY input that differs between the two `load_from_catalog` calls is the metadata row's `asset_class` (`"ETF"` vs `"STOCK"`); the test explicitly asserts the two `instrument_id`s are distinct (`IVV.ARCA` vs `REF.NASDAQ`) so it is not an accidental same-instrument comparison. The run therefore verifies the loader/whole-share-sizing/orchestrator path is **asset-class-blind** — an asset-class branch that perturbed ETF numbers would make `trade_count_delta != 0` and break the test. That is exactly the property Epic 5 exists to guarantee (non-tautological).
- [x] [Review][Dismiss] "Strict `bar_count_delta == 0.0` / `trade_count_delta == 0` could be flaky if the venue (ARCA vs NASDAQ) affected fills" [Edge Case Hunter] — verified deterministic: bar count is purely data-driven (both instruments carry the same 40-bar zigzag series in the same window), and SMA-crossover decisions are computed on close prices with identical whole-share sizing — venue does not enter the decision or sizing path. PnL is asserted via the tolerance-based `pnl_passed` (not strict equality) to absorb any commission/venue micro-drift, matching the AC's "within expected tolerance" language. Confirmed by the passing `--forked` run (Δbars=0, Δtrades=0, PnL within tol).
- [x] [Review][Dismiss] "The deviation-reporting test perturbs a summary rather than comparing two real runs — does it prove AC1?" [Acceptance Auditor] — verified sufficient: AC1's requirement is that *any deviation beyond tolerance is reported*. Producing a genuinely-deviating ETF run would require a production regression, so a synthetically-perturbed candidate is the correct way to exercise the reporting path (`overall_passed False` + populated `notes` + `render_comparison_table` breach line). The consistency test (AC2) supplies the two-real-run comparison; together they cover AC1+AC2. The perturbation breaches every metric (bars ≥ doubled, trades +75 > ±50, PnL floored at +10k over the ≥5% relative bump) so the breach is unambiguous regardless of the reference run's exact PnL sign/magnitude.
- [x] [Review][Dismiss] "`evaluate_tolerance` might vacuously pass on empty runs" [Edge Case Hunter] — verified guarded: the consistency test asserts **both** summaries have `total_trades > 0` before comparing, and `evaluate_tolerance` itself fails loudly on both-zero-trades / both-zero-bars / NaN PnL (comparison_report.py lines 73-78). The zigzag window produces repeated crossovers → real fills on both sides.
- [x] [Review][Defer → surfaced] **Pre-existing test-module size debt.** The shared integration module entered this story at 801 lines (already over the 500-line file guideline from Stories 3.1/5.1–5.4); adding the cohesive `TestEtfReferenceConsistency` class (bound to the module's `synthetic_catalog` fixture + bar generators) grows it to 961. Splitting would need a shared conftest or cross-module fixture import (ruff F401 gate friction) — broadening the diff beyond a lock-in story's scope. DEFERRED as pre-existing hygiene; the new helpers/methods each obey the function-size limit, and the test-grouping class follows 5.4's `TestNamedCatalogEtfPersistence` precedent.

## Dev Notes

### The core idea (why this story is verification/lock-in)
The reference-comparison tooling **already exists** and is asset-class-agnostic: `src/models/comparison_report.py` (`BacktestResultSummary`, `ComparisonReport`, `evaluate_tolerance`) + `src/services/comparison_renderer.py` (`render_comparison_table`) were built for the Story 3.3/3.4 AAPL 2018 reference comparison. They compare two `BacktestResultSummary`s (bar count / trade count / PnL) against agreed tolerances and produce a structured verdict + a breach-annotated table. Story 5.5 confirms the **ETF catalog path** produces results consistent with a **reference path** by feeding an ETF run and a Stock reference run — byte-identical inputs through the identical loader/orchestrator — into that **unchanged** tooling. Because the ETF is erased to a plain whole-share `Equity` at load time (Story 5.1/5.2), the ETF path reproduces the Stock reference within tolerance; the comparison **passes**, closing the Phase 2 verification loop. So Story 5.5 ships **no production code** — it *locks the contract in* with integration coverage: an ETF run is consistent with a Stock reference, and a real deviation is *reported* (not silently blessed). [Source: comparison_report.py; comparison_renderer.py; scripts/verify_aapl_2018_reference.py `_execute_and_summarise`]

### Why a Stock reference (and not IBKR live data)
Story 3.4's reference path was **IBKR live data** (`whatToShow=TRADES`) vs FirstRate — gated on `IBKR_AVAILABLE=1`. **This story's harness constraint forbids live data** (local catalog/mock only). The faithful, constraint-compatible reference for an ETF is therefore the **Stock** it must behave identically to: run the identical zigzag bar series through the identical `load_from_catalog` → `BacktestOrchestrator.execute` path under a Stock identity (`REF.NASDAQ`) and under an ETF identity (`IVV.ARCA`). The ETF/Stock differ **only** in instrument identity and (erased-at-load) asset class, so a consistent path yields `trade_count_delta == 0`, `bar_count_delta == 0.0`, and PnL within `DEFAULT_PNL_TOL`. This is **not** a tautology: if a future change slipped an asset-class branch into the loader, whole-share sizing, or the orchestrator, the ETF run would diverge from the Stock reference and `evaluate_tolerance` would report the breach (Task 2 proves the tooling *does* report breaches). [Source: epics.md Epic 5 "whole-share sizing … consistent with Stocks"; scripts/verify_aapl_2018_reference.py header (IBKR path); Story 5.2 leveraged/inverse-ETF-as-ordinary-shares]

### The seam (what is exercised, end to end)
```
REF.NASDAQ (Stock, reference)          IVV.ARCA (ETF, candidate)
   │ load_from_catalog → build_equity     │ load_from_catalog → build_equity
   │   → Equity (whole-share, USD)         │   → Equity (whole-share, USD)   ← identical synthesis
   ▼ BacktestOrchestrator.execute          ▼ BacktestOrchestrator.execute    ← identical path (dispose between)
   │   (same sma_crossover params,          │   (same params, same zigzag bars)
   │    same 2018-01-01→2018-03-01 window)  │
   ▼ _summarise → BacktestResultSummary     ▼ _summarise → BacktestResultSummary
        │                                        │
        └──────────► evaluate_tolerance(stock, etf) ◄──────────┘   ← EXISTING tooling, unchanged
                          │
                          ▼
              ComparisonReport: overall_passed=True, Δbars=0, Δtrades=0, pnl within tol   (AC2 — closes loop)
              render_comparison_table → "✅ ALL TOLERANCES PASSED"

              (Task 2) evaluate_tolerance(reference, PERTURBED) → overall_passed=False,
                       notes populated, render → "❌ TOLERANCE BREACH"                     (AC1 — deviation reported)
```
[Source: comparison_report.py lines 56-113; comparison_renderer.py lines 42-99; backtest_loader.py `load_from_catalog`/`build_equity`; backtest_orchestrator.py `execute`]

### Scope boundaries (do NOT do here)
- **No production edits** unless a test proves a real defect (then STOP + surface). This is a lock-in story; the comparison tooling and the ETF catalog path already exist.
- **No new comparison model, no tolerance re-tuning** — reuse `evaluate_tolerance`/`BacktestResultSummary`/`ComparisonReport`/`render_comparison_table` exactly. The `DEFAULT_*_TOL` constants are Phase 1 agreement (comparison_report.py lines 22-24); do not change them.
- **No Alembic migration, no `src/db/models/**` change, no results-DB schema change** — this story compares in-memory results (`persist=False`); nothing is written to the DB. **STOP and surface** if a schema change appears necessary (hard harness constraint).
- **No live data** — no IBKR/Kraken/network; the Stock reference is a synthetic in-catalog instrument, not an IBKR fetch. (Contrast Story 3.4, which used IBKR live data behind `IBKR_AVAILABLE`.)
- **No new CLI/web surface, no new harness script** — the story is verified by an integration test that exercises the existing `evaluate_tolerance`/renderer directly. A standalone ETF harness script (analogous to `verify_aapl_2018_reference.py`) is **not** in scope; it would add production surface for a lock-in story.
- **No asset-class branch** anywhere in the compared path — introducing one would violate NFR16/ADR-10 and make the ETF diverge from the Stock reference (this story's own AC2).

### Testing standards summary
- Tier: **`--forked` integration** — a real `BacktestEngine.run()` (Nautilus C/Rust `fork()` isolation) is required for both the ETF and the Stock reference run; the comparison itself is pure in-memory (`evaluate_tolerance`). No Postgres needed (this story is `persist=False`, unlike 5.4). [Source: CLAUDE.md test tiers; docs/agent/testing.md; nautilus-engine-patterns]
- TDD Red→Green: write the `evaluate_tolerance(...).overall_passed is True` consistency assertion (AC2) first — it fails if the ETF path diverges from the Stock reference — and the `overall_passed is False` + breach-line assertion (AC1) which proves the tooling reports deviations. The consistency assertion LOCKS IN the ETF≡Stock contract; it may pass immediately — expected for a verification story (mirror Story 5.1 Task 2 / 5.4).
- Reuse existing helpers: `synthetic_catalog` (extend additively with `REF.NASDAQ`), `_make_zigzag_bars`, `_make_instrument_row`, `_build_sma_request`. Extend, don't rewrite.

### Project Structure Notes
- Touch points (tests only): `tests/integration/core/test_backtest_catalog_integration.py` (one additive fixture write + one `_summarise` helper + two tests in a new `TestEtfReferenceConsistency` class). No production files (architecture.md line 565 "No new files").
- Keep the new class local to the module (mirror how Story 5.4 added `TestNamedCatalogEtfPersistence`); a shared conftest/harness is not warranted for two tests and would broaden the diff.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.5 (lines 855-869); Epic 5 line 150 "Results persist via the existing DB path"; whole-share/no-adapter thesis]
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-10; line 565 (E5 "No new files")]
- [Source: src/models/comparison_report.py (`evaluate_tolerance` lines 56-113, `BacktestResultSummary` lines 27-37, tolerance constants lines 22-24)]
- [Source: src/services/comparison_renderer.py (`render_comparison_table` lines 17-101)]
- [Source: scripts/verify_aapl_2018_reference.py (`_execute_and_summarise` lines 393-413 — the summary-extraction pattern reused by `_summarise`)]
- [Source: src/services/firstrate/backtest_loader.py (`load_from_catalog`, `build_equity`); src/core/backtest_orchestrator.py (`execute` lines 87-164)]
- [Source: tests/integration/core/test_backtest_catalog_integration.py (`synthetic_catalog`, `_make_zigzag_bars`, `_build_sma_request`); tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py (reference-harness precedent)]
- [Source: 5-2-run-a-single-etf-backtest-with-whole-share-equity-sizing.md, 5-4-persist-etf-backtest-results-to-the-database.md (Epic 5 asset-class-blindness / verification-lock-in precedent)]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/integration/core/test_backtest_catalog_integration.py --forked -q` → 15 passed (13 existing + the 2 new reference-consistency tests).
- `uv run ruff check .` → All checks passed. `uv run mypy .` → Success: no issues found in 360 source files (no new errors vs baseline).
- `git diff --stat` / `git status --short` → only the one integration test file (M) + this story (A) + sprint-status (M). No `src/`, `alembic/`, `scripts/`, or `src/db/models/**` change (AC3 holds).

### Completion Notes List

- **Zero production change — verification/lock-in story.** The reference-comparison tooling already exists and is asset-class-agnostic: `src/models/comparison_report.py` (`evaluate_tolerance`/`BacktestResultSummary`/`ComparisonReport`) + `src/services/comparison_renderer.py` (`render_comparison_table`), shipped by Story 3.3/3.4 for the AAPL 2018 reference comparison. Story 5.5 feeds an ETF run and a Stock reference run through that **unchanged** tooling; no production code, no new tolerance path, no schema change. `git diff` shows only the one test file + this story + sprint-status.
- **Reference path = a Stock, not IBKR live data (harness constraint).** Story 3.4's reference was IBKR live data behind `IBKR_AVAILABLE`; this story's harness forbids live data (local catalog/mock only). The faithful ETF reference is therefore the Stock it must behave identically to: `REF.NASDAQ` (Stock) carries the byte-identical zigzag series as `IVV.ARCA` (ETF), added as one additive synthetic-fixture instrument. Both run through the identical `load_from_catalog` → `BacktestOrchestrator.execute` path with identical `sma_crossover` params/window.
- **Consistency PASSES, closing the loop (AC2).** `evaluate_tolerance(stock_summary, etf_summary)` → `overall_passed is True`, `bar_count_delta == 0.0`, `trade_count_delta == 0`, `pnl_passed`. In practice the ETF reproduced the Stock reference **exactly** (Δtrades=0, Δbars=0, PnL within `DEFAULT_PNL_TOL`) — the ETF path is asset-class-blind (erased to a plain whole-share `Equity` at load). A future asset-class branch in the loader/sizing/orchestrator that perturbed ETF numbers would break this test.
- **Deviation IS reported, not silently blessed (AC1).** A perturbed candidate summary breaching every tolerance (bars > 0.5%, trades > ±50, PnL > 0.5%) → `evaluate_tolerance` returns `overall_passed is False` with populated `notes`, and `render_comparison_table` emits `❌ TOLERANCE BREACH` (and never `✅ ALL TOLERANCES PASSED`). This proves the same tooling that green-lights the consistent comparison would flag a real divergence — guarding against a vacuous pass.
- **No DB, no live data.** This story compares in-memory orchestrator results (`persist=False`) — no Postgres round-trip (unlike 5.4), no DB write, no migration, no schema change. Bars are generated in-process on a `tmp_path` `ParquetDataCatalog`; no IBKR/Kraken/network. `--forked` runner for Nautilus C/Rust `fork()` isolation; engines disposed between the two sequential runs (Gotcha #4).
- **Pre-existing test-module size debt (surfaced, not introduced).** The shared integration test module entered this story at 801 lines (already over the 500-line guideline from Stories 3.1/5.1–5.4). Adding the cohesive `TestEtfReferenceConsistency` class (which depends on the module's `synthetic_catalog` fixture + bar generators) grows it to 961 lines. A split would require a shared conftest or cross-module fixture import (ruff F401 gate friction), broadening the diff beyond a lock-in story's scope — deferred as pre-existing test hygiene. New helpers/methods each obey the function-size limit.

### File List

- `tests/integration/core/test_backtest_catalog_integration.py` (M) — one additive `synthetic_catalog` write (`REF.NASDAQ` reference Stock, identical zigzag series to `IVV.ARCA`); new `TestEtfReferenceConsistency` class with `_summarise`/`_run_and_summarise` helpers + `test_etf_run_matches_stock_reference_within_tolerance` (AC2 consistency) + `test_reference_comparison_reports_deviation_beyond_tolerance` (AC1 deviation reporting).
- `_bmad-output/implementation-artifacts/5-5-reference-consistency-verification.md` (A) — this story.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (M) — 5-5 → review (epic-5 already in-progress).
</content>
</invoke>
