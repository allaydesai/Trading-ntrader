# Story 3.4: IBKR vs FirstRate Parity Comparison

Status: draft

<!-- Follow-up to Story 3.3. Replaces the legacy-CSV path with an IBKR autofetch
path so both sides of the comparison are independently authoritative. -->

## Story

As a system operator,
I want the AAPL 2018 reference comparison harness to compare the FirstRate-imported catalog against IBKR-fetched bars (instead of the unverified legacy `data/AAPL_1min.csv`),
So that a clean parity result actually validates FirstRate as the default Phase 1 data source — and any divergence becomes a real data-quality signal rather than a setup artifact.

## Background — why this story exists

Story 3.3 shipped the comparison framework (ComparisonReport model, tolerance evaluator, stdout renderer, harness skeleton, gated integration test) but used `data/AAPL_1min.csv` for Path A. That CSV's provenance is unverified, so the 1.95% PnL Δ and 14-trade Δ revealed by the run can't be attributed to either parser — it's just measuring two unrelated datasets. Switching Path A to IBKR (the system's existing live source via `DataCatalogService.fetch_or_load`) puts two trusted sources side-by-side: a tight match validates both; a divergence is a real signal.

## Scope & Non-Goals

**In scope:**

- Replace Path A in `scripts/verify_aapl_2018_reference.py` with an IBKR-fetch path. Bars come from `DataCatalogService(catalog_path=output_dir/ibkr_catalog).fetch_or_load(...)` — the existing autofetch pipeline (Story 1.x), no new fetch code. First run pulls ~98K bars from IBKR (~1–2 min); subsequent runs read from the harness-owned cache.
- Drop `--legacy-csv` and the CSV filter/import helpers (`filter_csv_to_2018`, `_import_legacy_csv`). Keep `--firstrate-catalog`, `--output-dir`, `--skip-import` (renamed `--skip-fetch`).
- New gating: `IBKR_AVAILABLE=1` env var (analogous to `E2E_CATALOG_AVAILABLE=1`) — confirms the IBKR Gateway is up and credentials are configured. Tests `pytest.skip` cleanly otherwise.
- **Pre-flight alignment** for the two known divergence sources (one-line config asserts at the top of the harness):
  - **Adjustment policy.** IBKR returns "as-traded" by default; FirstRate "Stocks" bundles are typically pre-adjusted. Verify both paths use the same policy for AAPL 2018 (no splits, dividends were small — should still align). If FirstRate is adjusted and IBKR is not, document the expected drift in the retro and either pick a no-adjustment-event ticker or apply matching adjustment in code.
  - **Trading-hours flag.** IBKR `useRTH=True` returns regular-hours only; FirstRate intraday usually includes pre/post. Bar counts will diverge if these don't match. Pin `useRTH` consistently with FirstRate's source.
- Reuse 3.3's `ComparisonReport`, `BacktestResultSummary`, `evaluate_tolerance`, `render_comparison_table` unchanged. No model/renderer changes.
- New gated integration test `tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py` covering the same 3 cases as 3.3's test (both-paths, within-tolerance, unwritable-output). Forked.
- **Retro item:** revisit the agreed tolerances (0.5% / 0 / 0.1%) using the actual paired-trusted-source numbers from this run. If the IBKR-vs-FirstRate result is well within bounds, the existing thresholds are validated; if it's tight to the boundary, propose tightening; if it breaches, that's a real bug to investigate before adoption.

**Out of scope:**

- Story 3.3's reusable framework — this story uses it, doesn't change it.
- Removing 3.3's CSV-based test/script wholesale. Leave it in place but mark it deprecated in a one-line docstring; future cleanup story can delete once 3.4 lands.
- Multi-ticker comparisons (R11 still deferred — `BacktestRequest.symbol` is scalar).
- Adjustment-policy normalization code. If AAPL 2018 reveals a real policy mismatch, that's a separate story. This story documents the alignment, doesn't build new normalization logic.
- A new `data_source` enum value for "ibkr-comparison". Both runs use existing `data_source="catalog"` semantics (fetched from IBKR, then read back from local catalog).
- Production-code error-handler changes. Story 3.3 already fixed CLI/web error surfaces.

## Acceptance Criteria

1. **IBKR autofetch path runs end-to-end** — **Given** the IBKR Gateway is reachable and AAPL 2018 1-MINUTE bars are available via the IBKR API, **When** the harness invokes `DataCatalogService.fetch_or_load(...)` against an isolated `output_dir/ibkr_catalog/` for the AAPL 2018-01-01..2018-12-31 1-MINUTE window, **Then** the call returns ~98K bars without `IBKRConnectionError` or `RateLimitExceededError`, **And** subsequent runs (with `--skip-fetch`) read from the cached parquet files in <5s.

2. **Comparison harness emits a structured report** — **Given** both IBKR-cached AAPL bars and the FirstRate `e2e-test` catalog are available, **When** the operator runs `uv run python scripts/verify_aapl_2018_reference.py`, **Then** the script (a) fetches AAPL 2018 from IBKR into the isolated catalog (or skips on `--skip-fetch`), (b) runs `sma_crossover` against both data sources with the canonical params from 3.3 (`fast_period=10`, `slow_period=20`, `position_size_pct=Decimal("10")`), (c) writes `aapl_2018_comparison.json` to `output_dir`, (d) prints the rich.Table comparison.

3. **Tolerance verdicts: bar count Δ ≤ 0.5%** — same threshold as 3.3, same denominator formula. Documented expected drivers: `useRTH` differences (must be aligned in pre-flight), session-boundary handling, half-day Wednesday before Thanksgiving, NYSE holiday calendars.

4. **Tolerance verdicts: trade count exact** — same zero-tolerance as 3.3.

5. **Tolerance verdicts: total PnL Δ ≤ 0.1%** — same threshold as 3.3. **If breached**, the retro investigates whether (a) IBKR and FirstRate disagree on a specific bar (run a bar-level diff over the trade-firing minutes), (b) adjustment policy needs aligning, or (c) the threshold itself needs revisiting given the paired-trusted-source baseline.

6. **Tolerance breach behavior is loud** — same as 3.3 AC #6 (non-zero exit, `tolerance_passed: false`, ❌ TOLERANCE BREACH lines, integration test FAIL).

7. **Comparison runs use byte-identical strategy params + window** — same as 3.3 AC #7. Harness asserts param equality before either backtest runs.

8. **Pre-flight alignment surfaces divergence sources before any backtest runs** — **Given** the harness starts, **When** it builds the IBKR fetch request, **Then** it logs (a) IBKR `useRTH` flag value, (b) the bar count returned, (c) the FirstRate AAPL row's `bar_count_minute` from `catalog_instruments`, (d) a one-line side-by-side: `IBKR bars: N (useRTH=True)` vs `FirstRate metadata: M`. Mismatch beyond the bar-count tolerance halts execution with a clear message before any expensive backtest runs.

9. **Gating: `IBKR_AVAILABLE=1` + Gateway reachable** — integration test `pytest.skip` when env var unset. When env var is set but the Gateway is unreachable, the test fails (not skips) with the IBKR connection error message — same fail-fast semantics as Story 3.1's similar gate.

10. **Quality gates** — same as 3.3 AC #14: `make format && make lint && make typecheck && make test-unit && make test-component && make test-integration` clean. New integration test adds ~1 case. No new unit/component tests required (model + renderer are unchanged from 3.3).

11. **Evidence captured + retro entry** — `output_dir/aapl_2018_comparison.json` + stdout log. Inline summary in `Dev Agent Record → Completion Notes` mirroring 3.3's format. **Retro entry:** answer the question "are FirstRate and IBKR in agreement on AAPL 2018 1-MINUTE within agreed tolerances?" with the actual numbers + verdict + any required follow-up.

## Tasks / Subtasks

- [ ] Task 1: Refactor harness to use IBKR path (AC: #1, #2, #7, #8)
  - [ ] 1.1 Failing integration test in `tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py` — gated on `IBKR_AVAILABLE=1`, asserts harness returns a `ComparisonReport` with both `ibkr_metrics` and `firstrate_metrics` populated.
  - [ ] 1.2 Replace `_run_legacy_backtest` in `scripts/verify_aapl_2018_reference.py` with `_run_ibkr_backtest` — calls `DataCatalogService.fetch_or_load(...)` against an isolated `output_dir/ibkr_catalog/`. Reuses `_build_equity` for instrument synthesis (same precision-from-bars trick as 3.3).
  - [ ] 1.3 Drop `filter_csv_to_2018`, `_import_legacy_csv` helpers. Drop `--legacy-csv` flag. Add `--skip-fetch` flag (replaces `--skip-import`).
  - [ ] 1.4 Update script docstring + module-level comment to reflect the new IBKR path. Note the `data/AAPL_1min.csv` path is no longer used.

- [ ] Task 2: Pre-flight alignment checks (AC: #8)
  - [ ] 2.1 Failing test asserting the harness halts with a clear error if `useRTH` flag isn't set explicitly OR if the FirstRate metadata bar count diverges from the IBKR fetch by more than `bar_count_tol`.
  - [ ] 2.2 Implement `_assert_pre_flight_alignment(ibkr_bar_count, firstrate_metadata_count, useRTH)` — log both values and raise `RuntimeError` with the side-by-side message if mismatch exceeds 0.5%. Keep the function ≤ 30 lines.

- [ ] Task 3: Integration test wiring (AC: #6, #9, #10)
  - [ ] 3.1 `test_harness_runs_both_paths` (gated). Asserts both runs populate.
  - [ ] 3.2 `test_full_reference_comparison_within_tolerance` (gated). Asserts `report.overall_passed is True`.
  - [ ] 3.3 `test_harness_raises_on_unwritable_output` (no IBKR needed — same pre-flight check fires before any fetch).
  - [ ] 3.4 Mark old `tests/integration/core/test_aapl_2018_reference_comparison.py` as deprecated (one-line docstring), do NOT delete (Story 3.5 cleanup once 3.4 stable).

- [ ] Task 4: Quality gates + retro (AC: #10, #11)
  - [ ] 4.1 `make format && make lint && make typecheck` — clean.
  - [ ] 4.2 `make test-integration` with `IBKR_AVAILABLE=1` — new gated test runs forked, captures evidence.
  - [ ] 4.3 Capture comparison-report deltas inline in `Completion Notes`.
  - [ ] 4.4 Retro entry answering the parity question (see AC #11).

## Dev Notes

### Architecture Compliance

- **No new fetch code.** `DataCatalogService.fetch_or_load(...)` already does IBKR autofetch + parquet caching (Story 1.x). The harness just calls it with an isolated `catalog_path`.
- **`BacktestEngine` is single-use** (CLAUDE.md Gotcha #4). Same sequential-runs constraint as 3.3.
- **LogGuard discipline** — already wired by Story 3.1.
- **Adjustment policy assumption.** Default IBKR `reqHistoricalData` returns "TRADES" with no adjustment. FirstRate "Stocks" Daily-1m bundle (the source of `e2e-test`) needs to be confirmed. AAPL 2018 had no splits and small dividends (~$0.73/share total) — the resulting drift should be ≤ 0.05% on close, well inside the PnL tolerance even if both paths disagree on dividend treatment.
- **Trading-hours assumption.** IBKR `useRTH=True` for equities returns 9:30 ET–16:00 ET only. FirstRate AAPL 1-MIN includes pre-market (04:00 ET start) per Story 3.3's evidence (first bar `2018-01-02T04:00:00Z`). To match, the IBKR fetch needs `useRTH=False`. Verify this in pre-flight and pin in the request.

### Existing Code to Reuse

| What | Where | How to use |
|------|-------|------------|
| `ComparisonReport`, `BacktestResultSummary`, `evaluate_tolerance` | `src/models/comparison_report.py` (Story 3.3) | Import unchanged |
| `render_comparison_table` | `src/services/comparison_renderer.py` (Story 3.3) | Import unchanged |
| `DataCatalogService.fetch_or_load` | `src/services/data_catalog.py` | IBKR autofetch path |
| `_build_equity` | `src/services/firstrate/backtest_loader.py` | Synthesise Equity from bars (same precision trick as 3.3) |
| `BacktestRequest.from_cli_args` | `src/models/backtest_request.py` | Build the canonical request — keep params identical to 3.3 |
| Existing harness skeleton | `scripts/verify_aapl_2018_reference.py` (Story 3.3) | Edit in place (don't fork into a new script) |

### Known Constraints

- **IBKR rate limit:** 45 req/sec target. A full year of 1-MINUTE bars across many segment requests will take 1–2 minutes the first run. `_guard_nautilus_logging()` handles double-init protection (Story 1.x).
- **Pre-existing `make test-integration` flakiness** — same carryover as 3.1/3.3. New 3.4 test must pass under `--forked` in isolation.
- **IBKR Gateway must be running** — the test gate `IBKR_AVAILABLE=1` is a developer assertion, not an active healthcheck. Test failure on connection error is intended (signals the dev forgot to start the Gateway).

### Anti-patterns to Avoid

- **Don't skip the pre-flight bar-count check.** A silent `useRTH` mismatch will produce a 50%+ bar count divergence that the harness should refuse to run — not soldier through to a meaningless tolerance breach an hour later.
- **Don't introduce a new tolerance for "expected IBKR drift."** If the agreed Phase 1 tolerances breach against two trusted sources, that's a real bug to investigate. Tolerance widening is a retro decision with documented justification.
- **Don't delete Story 3.3's CSV-based script in this story.** Mark it deprecated, leave for a 3.5 cleanup. Keeps blast radius small.
- **Don't write to the user's default `NAUTILUS_PATH` catalog.** Use `output_dir/ibkr_catalog/` exactly as 3.3 uses `output_dir/legacy_catalog/`. The shared-`NAUTILUS_PATH` lesson from 3.3 still applies.
- **Don't run both backtests concurrently.** Same single-engine constraint as 3.3.

### References

- [Source: Story 3.3 completion notes] — comparison framework + the data-source mismatch finding that motivated this story.
- [Source: `src/services/data_catalog.py` — `fetch_or_load`] — IBKR autofetch path.
- [Source: `src/services/ibkr_historical_client.py`] — `useRTH` flag, rate limiting.
- [Source: Story 1.x retros] — IBKR client patterns.
- [Source: `_bmad-output/implementation-artifacts/epic-2-retro-2026-04-19.md:97`] — origin of the agreed tolerances; revisit decision point.

## Dev Agent Record

### Agent Model Used

_To be filled by Dev._

### Debug Log References

_To be filled by Dev._

### Completion Notes List

_To be filled by Dev — must include the parity verdict (AC #11)._

### File List

_To be filled by Dev._

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-05-01 | Amelia (Dev) | Story 3.4 drafted as a follow-up to 3.3. Status: draft (awaiting SM review + ready-for-dev). |
