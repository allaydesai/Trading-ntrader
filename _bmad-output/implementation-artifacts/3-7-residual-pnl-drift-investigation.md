# Story 3.7: Residual PnL Drift Investigation — Single-Venue IBKR Contract

Status: ready-for-dev

<!-- Optional polish — Epic 3 retro action item C3. Non-blocking for Epic 4. -->

## Story

As a system operator,
I want to investigate whether requesting AAPL through a single-venue IBKR contract route (e.g., `AAPL.ARCA` or the primary listing exchange) closes the residual 0.31% PnL Δ / 44 trade Δ that remains between IBKR and FirstRate after Story 3.6's re-import,
So that we either (a) tighten the Phase 1 parity tolerance back toward Epic 2 retro's original aspiration with a documented sub-cent agreement, or (b) accept the residual as Phase 1 venue noise and lock the widened tolerance as the final answer.

## Background — why this story exists

Story 3.4's parity verdict, after the FirstRate TZ parser fix and Story 3.6 re-import, sits at:

| Metric | IBKR | FirstRate | Δ | Threshold (3.4 widened) | Verdict |
|---|---:|---:|---:|---|---|
| Bar count | 97,350 | 97,350 | 0.00% | ≤ 0.5% | ✅ pass |
| Trade count | 5,453 | 5,497 | +44 (0.8%) | ±50 | ✅ pass (close) |
| Total PnL | -$189,539.15 | -$190,123.63 | 0.31% | ≤ 0.5% | ✅ pass (close) |

Side-by-side bar inspection across 2018 confirmed the residual is real: IBKR's consolidated TRADES tape vs FirstRate's single-venue (likely NASDAQ-only) feed produces sub-cent per-bar OHLC noise at volume ratios of 8–111×. The drift is structural, not a bug.

Story 3.4 explicitly left this as a follow-up (Story 3.7 candidate): test a single-venue IBKR contract (e.g., `AAPL.ARCA`, or the primary-listing route) to see if it matches FirstRate to sub-cent. If yes, Phase 1 has a sub-cent option. If no, the consolidated-vs-single-venue noise is the floor and the widened tolerance is the final answer.

This story is **non-blocking for Epic 4** — Epic 4 is supplementary data display, not backtesting. The widened tolerances are already locked in `comparison_report.py` per retro decision C6. 3.7 either tightens the documentation or confirms it.

## Scope & Non-Goals

**In scope:**

- **Spike investigation** of IBKR contract-routing options for `AAPL`:
  - The default `IBKRHistoricalClient.fetch_bars` path uses `whatToShow=TRADES` against the SMART-routed contract — IBKR consolidates across listed venues. Document this baseline.
  - Survey the Nautilus IBKR adapter (`nautilus_trader.adapters.interactive_brokers`) for non-SMART contract routing options. Concrete candidates: explicit primary exchange (`primaryExchange="NASDAQ"` on the Contract), explicit ARCA listing route (`exchange="ARCA"`), or the Nautilus `instrument_id` form `AAPL.NASDAQ` resolved to a single-venue Contract instead of SMART.
  - Identify ONE candidate route worth testing end-to-end. Don't chase all three.
- **End-to-end parity re-run** with the candidate single-venue route on AAPL 2018 1-MINUTE:
  - Fetch via the single-venue path into an isolated `output_dir/ibkr_single_venue_catalog/`.
  - Run the existing `verify_aapl_2018_reference.py` harness path with the new catalog as Path A; FirstRate `e2e-test` (post-3.6 re-import) as Path B.
  - Capture the resulting `ComparisonReport` and side-by-side bar inspection at the same three sample points Story 3.4 used (2018-01-02, 2018-06-15, 2018-12-28 each at 09:30 ET).
- **Decision: tighten or accept**:
  - **If the single-venue route matches FirstRate to sub-cent** (PnL Δ < 0.05% AND trade Δ ≤ 5 AND open prices match to the cent at all three sample points): document the choice, add a `data_provider_route: str` field to `BacktestRequest` (or equivalent metadata) noting `consolidated` vs `single-venue`, and propose narrowing `DEFAULT_PNL_TOL` back toward 0.1% in a follow-up story (NOT this one — scope discipline).
  - **If the single-venue route still drifts** (PnL Δ > 0.1% OR trade Δ > 10): accept the residual as Phase 1 venue noise, update the `comparison_report.py` `DEFAULT_*_TOL` docstrings with a dated reference to this investigation, and close the question.
- **Update `comparison_report.py` docstring** with the empirical evidence and dated retro reference, regardless of which decision branch fires. Future readers should not have to re-do this investigation.
- **Update memory** — refresh `project_ibkr_vs_firstrate_parity_drift.md` with the single-venue numbers if the investigation runs.

**Out of scope:**

- Production-code changes to `IBKRHistoricalClient` or `DataCatalogService` to permanently expose a single-venue route. If the spike validates the route, the production wiring is a separate story (Story 3.8 candidate). 3.7 is a spike + a documentation update.
- Tightening tolerances in production code. The decision triggers a separate follow-up story to do the actual narrowing.
- Investigating other tickers / windows / timeframes. AAPL 2018 1-MINUTE is the canonical reference; if the single-venue route works there, generalisation is a future concern.
- Changing the `comparison_report.py` `DEFAULT_*_TOL` values themselves in this story. Only the docstring is updated.
- Asking IBKR support what route their TWS uses by default. The Nautilus / TWS API docs are the authoritative source.
- Working on AAPL.ARCA-vs-FirstRate when FirstRate's actual single-venue source is unconfirmed. If Task 1's investigation reveals FirstRate's NASDAQ-only assumption is wrong, document and reconsider.

## Acceptance Criteria

1. **Single-venue contract route identified and documented** — **Given** the operator surveys the Nautilus IBKR adapter and TWS API docs, **When** Task 1 closes, **Then** the Dev Notes contain (a) the exact Contract construction call (Python code or pseudocode) for the candidate route, (b) the rationale for picking that route over alternatives, and (c) a citation to the relevant Nautilus or TWS API documentation.

2. **Single-venue catalog populated for AAPL 2018 1-MINUTE** — **Given** IBKR Gateway is reachable AND the candidate single-venue route is wired through a one-off harness, **When** the operator runs the fetch, **Then** ~98K bars (matching the consolidated bar count from Story 3.4 within 0.5%) are written to `output_dir/ibkr_single_venue_catalog/`. If the bar count differs by >5% from consolidated, the route is fundamentally different (e.g., extended hours included/excluded) and Task 2 documents the structural difference before any backtest is attempted.

3. **Parity verdict captured for single-venue path** — **Given** both `output_dir/ibkr_single_venue_catalog/` and `e2e-test` (FirstRate, post-3.6 re-import) are populated, **When** the operator runs the existing parity harness with the single-venue catalog as Path A, **Then** a `ComparisonReport` JSON is written to `_bmad-output/implementation-artifacts/3-7-evidence/single_venue_comparison.json` with bar count Δ, trade count Δ, PnL Δ, and tolerance verdicts. Stdout output is captured to `_bmad-output/implementation-artifacts/3-7-evidence/single_venue_run.txt`.

4. **Side-by-side bar inspection at the three sample points** — **Given** the single-venue catalog is populated, **When** the operator queries open prices at 2018-01-02 09:30 ET, 2018-06-15 09:30 ET, and 2018-12-28 09:30 ET, **Then** the prices are recorded in Dev Notes alongside the consolidated IBKR (Story 3.4) and FirstRate values. **Decision rule:** if all three single-venue opens match FirstRate to the cent, the route closes the gap; if any sample drifts ≥ $0.01 from FirstRate, it doesn't.

5. **Decision documented** — **Given** AC #3 and AC #4 evidence, **When** Task 4 runs, **Then** Dev Notes explicitly states "single-venue route closes the gap" OR "single-venue route does not close the gap" with the dated evidence references. No "TBD" or "needs more investigation" language — the decision is binary based on the dated rule in AC #4.

6. **`comparison_report.py` docstring updated** — **Given** the decision is documented, **When** Task 5 runs, **Then** the module-level docstring or the `DEFAULT_*_TOL` constants' inline documentation in `src/models/comparison_report.py` is updated with one of:
    - **(closes the gap)** "AAPL 2018 1-MIN single-venue parity: PnL Δ < 0.05% on `<route>`. Phase 1 default uses consolidated TRADES (widened tolerance) for operational simplicity; single-venue route available as `<knob>` for tighter parity. See Story 3.7."
    - **(doesn't close)** "AAPL 2018 1-MIN single-venue parity: PnL Δ ≈ X.X% on `<route>` (vs 0.31% consolidated). Sub-cent agreement not achievable for Phase 1; widened tolerance `bar 0.5% / trade ±50 / pnl 0.5%` is the empirical noise floor. See Story 3.7."

7. **Memory entry refreshed** — **Given** Task 4's decision, **When** the operator updates user memory, **Then** `~/.claude/projects/-Users-allay-dev-Trading-ntrader/memory/project_ibkr_vs_firstrate_parity_drift.md` includes the single-venue row in its table (alongside the existing 1-MINUTE and 1-HOUR rows from Story 3.4) with the dated decision.

8. **No regressions** — **Given** the spike's harness-level edits and docstring updates, **When** the operator runs `make format && make lint && make typecheck && make test-unit && make test-component`, **Then** all pass against Story 3.6's post-re-import baseline. Story 3.4's gated integration tests continue to pass with the same outcomes (`test_full_reference_comparison_within_tolerance` PASS at the widened tolerances).

## Tasks / Subtasks

- [ ] Task 1: Survey IBKR contract-routing options + pick a candidate (AC: #1)
  - [ ] 1.1 Read `nautilus_trader.adapters.interactive_brokers.client.historical_client` to understand how the existing path constructs the IBKR Contract. Identify the SMART-routing assumption.
  - [ ] 1.2 Read TWS API docs (`interactivebrokers.github.io/tws-api/contracts.html`) for non-SMART Contract construction patterns. Look for `primaryExchange`, `exchange`, `secType`, and `tradingClass` fields and their interaction.
  - [ ] 1.3 Pick ONE candidate route. Document in Dev Notes with code/pseudocode + citation. Reasonable default: `Contract(symbol="AAPL", secType="STK", exchange="NASDAQ", primaryExchange="NASDAQ", currency="USD")` to force NASDAQ-only.
  - [ ] 1.4 If the survey reveals that the existing `whatToShow=TRADES` setting is the binding constraint (not the routing), pivot the spike: try `whatToShow=BID_ASK` or `whatToShow=MIDPOINT` instead of changing the contract. Document the pivot in Dev Notes.

- [ ] Task 2: One-off harness fetching the candidate route (AC: #2)
  - [ ] 2.1 Add `scripts/diagnostics/fetch_aapl_single_venue.py` that builds the candidate Contract (or `whatToShow` variant), calls the IBKR adapter against the live Gateway, and writes bars to `output_dir/ibkr_single_venue_catalog/`. Reuse Story 3.4's `_fetch_ibkr_chunked` + `_iter_chunks` helpers — same chunking workaround, same midnight alignment.
  - [ ] 2.2 Run with `IBKR_AVAILABLE=1` set. Capture bar count + first/last bar timestamps to the evidence file.
  - [ ] 2.3 Confirm bar count vs Story 3.4's consolidated path (`97,350` post-fix). If the delta exceeds 5%, document the structural difference and pause before Task 3.

- [ ] Task 3: Parity re-run with single-venue Path A (AC: #3, #4)
  - [ ] 3.1 Modify the existing `verify_aapl_2018_reference.py` harness ONLY to accept `--ibkr-catalog-override <path>` (so it reads from `output_dir/ibkr_single_venue_catalog/` instead of the harness's default isolated path). Keep the change minimal — don't refactor the harness.
  - [ ] 3.2 Run the harness with the override pointing at the single-venue catalog. Capture stdout + JSON to `_bmad-output/implementation-artifacts/3-7-evidence/single_venue_run.txt` and `single_venue_comparison.json`.
  - [ ] 3.3 Query open prices at the three sample points (2018-01-02, 2018-06-15, 2018-12-28 each at 09:30 ET) from the single-venue catalog. Compare in a table against FirstRate (post-3.6) and Story 3.4's consolidated IBKR values. Save the table to Dev Notes.

- [ ] Task 4: Decision (AC: #5)
  - [ ] 4.1 Apply the AC #4 decision rule. Either single-venue route matches all three sample points to the cent AND PnL Δ < 0.05% AND trade Δ ≤ 5 (closes the gap), or it doesn't.
  - [ ] 4.2 Write the decision into the Dev Agent Record's Completion Notes List with a one-paragraph summary citing the evidence files.

- [ ] Task 5: Update docstring + memory (AC: #6, #7)
  - [ ] 5.1 Edit `src/models/comparison_report.py` per AC #6. Pick the closes-the-gap or doesn't-close branch based on Task 4's decision.
  - [ ] 5.2 Update `~/.claude/projects/-Users-allay-dev-Trading-ntrader/memory/project_ibkr_vs_firstrate_parity_drift.md` to include a `1-MINUTE single-venue` row.
  - [ ] 5.3 Revert `verify_aapl_2018_reference.py`'s `--ibkr-catalog-override` flag if it was added in Task 3.1 — it was a one-off spike knob, not production. The diagnostic script `fetch_aapl_single_venue.py` stays for archival reference.

- [ ] Task 6: Quality gates (AC: #8)
  - [ ] 6.1 `make format && make lint && make typecheck` clean.
  - [ ] 6.2 `make test-unit && make test-component` zero regressions vs Story 3.6's post-re-import baseline.
  - [ ] 6.3 (Optional, gated) `pytest tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py --forked` with `IBKR_AVAILABLE=1 E2E_CATALOG_AVAILABLE=1` — same outcomes as Story 3.4 / 3.6 (one design-mandated FAIL only if it shows up; per Story 3.4 P7 the test now passes). The single-venue path doesn't change the consolidated test's outcome.

## Dev Notes

### Architecture Compliance

- **Spike, not production.** Story 3.7 is intentionally a one-off harness + docstring update. No new production code paths, no new model fields, no new DB columns. If Task 4's decision is "closes the gap", the production wiring (knob to switch consolidated vs single-venue at runtime) is Story 3.8 territory.
- **Reuse Story 3.4 infrastructure.** `_fetch_ibkr_chunked`, `_iter_chunks`, midnight-alignment, `_dedup_by_ts_event` — all stay. Only the Contract / `whatToShow` knob changes.
- **Single-use `BacktestEngine` (CLAUDE.md Gotcha #4).** Task 3's harness re-run inherits Story 3.4's sequential run pattern.
- **`asyncio.to_thread` wrap (Epic 2 retro B2).** Inherited from 3.4; no new sync-in-async paths added.

### Critical Implementation Details

**Why this is a spike:** the question is empirical. We have one paired-trusted-source measurement (consolidated IBKR vs FirstRate at 0.31% PnL Δ); we need a second measurement (single-venue IBKR vs FirstRate) to know if the residual is venue-noise or sub-cent-achievable. The spike answers that question with one number; production decisions follow.

**Decision rule (from AC #4) restated:**
- **Single-venue closes the gap if:** all three sample-point opens match FirstRate to the cent, AND PnL Δ < 0.05%, AND trade Δ ≤ 5.
- **Single-venue doesn't close if:** any one of those fails.

These thresholds are tighter than the Phase 1 widened tolerance because the question is "can we achieve sub-cent agreement" — a yes-answer should be visibly tighter than the consolidated baseline, not just within widened tolerance.

**Why FirstRate's single-venue assumption matters:** Story 3.4's volume-ratio analysis suggested FirstRate is single-venue (likely NASDAQ-only). If Task 1's survey of the FirstRate Stocks bundle docs / source files surfaces evidence that FirstRate is also consolidated, the spike premise is broken — both feeds are consolidated, the residual is something else (rounding, dedup, fill semantics). Pause and reconsider before Task 2.

### Existing Code to Reuse

| What | Where | How to use |
|------|-------|------------|
| `_fetch_ibkr_chunked` + `_iter_chunks` + `_dedup_by_ts_event` | `scripts/verify_aapl_2018_reference.py` | Reuse unchanged for single-venue fetch |
| `IBKRHistoricalClient.connect()` rotation | `src/services/ibkr_client.py` | Reuse — same Gateway, same rotation semantics |
| Story 3.4 parity harness | `scripts/verify_aapl_2018_reference.py` | Add a one-off `--ibkr-catalog-override` flag in Task 3.1; revert in Task 5.3 |
| `ComparisonReport` + `evaluate_tolerance` + renderer | `src/models/comparison_report.py`, `src/services/comparison_renderer.py` | Reuse unchanged — same tolerances, same output shape |
| `DataCatalogService.fetch_or_load` | `src/services/data_catalog.py` | Backing store for the single-venue catalog |

### Known Constraints

- **IBKR Gateway availability.** Story 3.7 requires a live Gateway connection. Fetching a year of AAPL 1-MIN through chunked single-venue requests will take 5–15 minutes (same as Story 3.4 baseline). Plan accordingly.
- **Single-venue contract may not exist for all tickers.** If the spike works, generalising to AMZN/MSFT/NVDA/TSLA needs per-ticker primary-listing knowledge. Out of scope for this story (spike is AAPL-only).
- **TWS API per-bar-size duration limits + Gateway 10.45 useRTH leak still apply.** Inherited from Story 3.4. Don't try to "improve" them in this story.

### Anti-patterns to Avoid

- **Don't try multiple routes in parallel.** Pick ONE candidate route in Task 1, run ONE end-to-end measurement in Tasks 2–3, make ONE decision in Task 4. Multi-route exploration is scope creep.
- **Don't widen the comparison's scope past AAPL 2018 1-MINUTE.** Other tickers, other years, other timeframes don't change the decision.
- **Don't tighten the production tolerances in this story.** Even if the spike closes the gap, production tightening is a follow-up. Doing it here couples the empirical investigation to a code change with regression risk.
- **Don't refactor `verify_aapl_2018_reference.py` beyond the override flag.** That harness is the load-bearing parity entrypoint; minimal touch.
- **Don't build a UI for switching consolidated vs single-venue.** Production wiring is Story 3.8 territory; 3.7 is a spike + a docstring.

### Testing Requirements

- No new automated tests required for the spike. The output is empirical evidence + a docstring update.
- Existing integration test (`test_aapl_2018_ibkr_vs_firstrate.py`) continues to pass at widened tolerances per Story 3.6 / 3.4. Confirm in Task 6.3.

### References

- [Source: `_bmad-output/implementation-artifacts/epic-3-retro-2026-05-09.md` action C3] — retro decision driving this story; explicitly non-blocking for Epic 4.
- [Source: `_bmad-output/implementation-artifacts/3-4-ibkr-vs-firstrate-parity-comparison.md` Completion Notes "Empirical findings"] — three-sample-point side-by-side table from Story 3.4.
- [Source: `scripts/verify_aapl_2018_reference.py` — the parity harness] — Task 3's load-bearing entrypoint.
- [Source: TWS API docs — `interactivebrokers.github.io/tws-api/contracts.html`] — Contract construction for non-SMART routing. Citation lands in Dev Notes after Task 1.
- [Source: Story 3.4 Dev Notes "Adjustment policy assumption"] — IBKR `whatToShow=TRADES` is split-only (no dividend adjustment). Same applies to single-venue.
- [Source: `~/.claude/projects/-Users-allay-dev-Trading-ntrader/memory/project_ibkr_vs_firstrate_parity_drift.md`] — drift table; refresh in Task 5.2.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-05-09 | Bob (SM) | Story 3.7 created via `bmad-create-story 3-7`. Status: backlog → ready-for-dev. **Non-blocking for Epic 4.** Spike + docstring update to lock in the residual 0.31% PnL Δ as either tightenable (single-venue closes the gap) or accepted Phase 1 venue noise. |
