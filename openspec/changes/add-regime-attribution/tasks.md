# Tasks

Each build phase is one PR against `main`. Every task starts with the failing test that proves
it and names its tier. Test runs follow the box's limits:

- `pgrep -af pytest` first
- serial or `-n 2`, never `-n auto`
- no pytest while the dashboard runs
- `GLIBC_TUNABLES=glibc.rtld.execstack=2` exported
- integration tier `--forked`

Nothing in phase 1 starts until Allay has answered `design.md` section 13 Q1 (reference map)
and Q6 (baselines), or accepted the proposed defaults. This change touches no live/paper
module, so no live-verification task applies.

## 0. Step 0: investigation and proposal (this PR)

- [x] 0.1 Investigate stored equity, trades, the existing regime tools, SPY coverage, baseline
      runs and calendar storage (`design.md` section 0).
- [x] 0.2 Write `proposal.md`, `design.md`, `tasks.md` and the spec deltas. Keep Allay's spec
      verbatim in `source-spec.md`.

## 1. Phase 1: calendar (ground truth + long-average detector, SPY)

Acceptance: `build_regime_calendar` stores ground-truth version 1 and detector version 1
(`sma200`) for SPY. A rebuild reproduces every stored label exactly. Detector rows cannot be
updated or deleted. The wave-leg chart looks right to Allay.

- [ ] 1.1 Data check (no code): find out whether `firstrate-etf` SPY daily bars are split- and
      dividend-adjusted (compare against `catalog_dividends` ex-dates and a known split-free
      price). Record the answer in `design.md` section 0(d). The drift benchmark depends on it.
- [ ] 1.2 Unit (failing first): `tests/unit/core/regimes/test_ground_truth.py`:
  - ATR20 is a simple mean.
  - A synthetic zigzag gives the expected peaks and troughs at k = 3, 6 and 12, using the ATR
    at the extreme's date.
  - Leg days are (trough, peak] and (peak, trough].
  - Days before the first and after the last turning point are `null`.
  - Ranging uses the centred 21-session efficiency ratio < 0.3, `null` within 10 sessions of
    either end.
  - Volatility is centred 21-session volatility against a frozen median.
  - Finality: appending bars never changes a label that is already set.
- [ ] 1.3 Implement `src/core/regimes/ground_truth.py` (pure).
- [ ] 1.4 Unit (failing first): `tests/unit/core/regimes/test_detectors.py`:
  - `sma200` is `up` when close > SMA200 up to *t*.
  - The first 272 sessions are `null`.
  - **No lookahead**: perturbing every bar after *t* leaves labels ≤ *t* unchanged.
- [ ] 1.5 Implement `src/core/regimes/detectors.py` (`sma200` only).
- [ ] 1.6 Unit (failing first): `tests/unit/core/regimes/test_definition.py`: the default
      definition's canonical JSON and sha256 hash are pinned. Any changed parameter changes
      the hash. Unknown keys are refused. The definition holds no data-derived value: the
      frozen volatility median lives in `frozen_stats`, so building on more bars leaves the
      hash unchanged. The per-session `bar_hash` is stable for the same OHLC and changes when
      any one of its prices changes.
- [ ] 1.7 Implement `src/core/regimes/definition.py` (pydantic definitions, label payload models,
      hashing).
- [ ] 1.8 Integration (failing first, Postgres): `tests/integration/db/test_regime_repository.py`:
  - Insert a version and its labels; read by date range.
  - Uniqueness on (`reference`, `catalog`, `label_set`, `definition_hash`, `input_revision`)
    holds. The same definition at `input_revision` 2 is accepted only with a
    `revision_reason`.
  - The trigger rejects `UPDATE` and `DELETE` of a detector row.
  - On a ground-truth row the trigger allows `null` → value (with `set_in_build` set to the
    current build), and rejects value → other value, any change to `set_in_build` entries or
    `bar_hash`, and `DELETE`.
  - Deleting a version is rejected.
  - `regime_versions_immutable` rejects changes to `definition`, `definition_hash`,
    `input_revision`, `revision_reason`, `frozen_stats`, `reference`, `catalog`,
    `label_set`, `version` and `data_start`. It allows `data_end` to move forward and
    `build_no` to increment.
  - Version numbering and the "latest" lookup are scoped to (`reference`, `catalog`,
    `label_set`).
  - Reading as of build *n* hides values set later and rows inserted later.
- [ ] 1.9 Migration (off `3f56b46c78eb`): `regime_calendar_versions`, `regime_labels`, and the
      `regime_labels_append_only` and `regime_versions_immutable` triggers. Add
      `src/db/models/regime.py` and `src/db/repositories/regime_repository_sync.py` (insert
      and append only, no update or delete method). If Allay does not approve the sync-only
      exception (`design.md` Q13), also add the async `regime_repository.py` with the same
      surface and tests. Update the single-head note in `CLAUDE.md` and
      `docs/agent/persistence.md` (the new tables, plus the exception if approved).
- [ ] 1.10 Unit (failing first): `tests/unit/governance/test_regime_ground_truth_boundary.py`
      scans the AST of `src/**`, plus `src/core/strategies/custom/**` when it is present,
      resolving relative imports. Only the **named** permitted files (`design.md` section 9; no
      globs) may import `src.core.regimes.ground_truth` or call the repository's ground-truth
      reader. `src/core/regimes/filter_test.py` and `src/mcp_server/analysis/regime_filter.py`
      are explicitly forbidden. The test starts red against planted fixture modules, one
      absolute import and one relative.
- [ ] 1.11 Component (failing first): `tests/component/mcp_server/test_regime_calendar_tools.py`:
  - `build_regime_calendar` on a fixture catalog creates both versions.
  - Running it again with the same definition is a no-op.
  - New bars append rows.
  - A changed past bar is refused with `reference_bars_changed`, and a new-revision request
    with that reason then creates `input_revision` 2 of the same definition.
  - A mid-year extension appends rows and changes no stored `bar_hash`.
  - A changed definition creates version 2 and leaves version 1 untouched.
  - `get_regime_labels` returns dated labels for a range.
  - `label_set="ground_truth"` output carries `hindsight: true` and
    `"hindsight, not tradeable"`.
  - Refusals carry `fix`.
- [ ] 1.12 Implement `src/mcp_server/analysis/regime_calendar.py` and
      `src/mcp_server/tools/regimes.py` (`build_regime_calendar`, `get_regime_labels`).
      Register them in `server.py`. Add both names to `REGISTERED_TOOLS` and to `PINNED_TOOLS`
      (`tests/component/mcp_server/test_protocol_safety.py`) in the same commit. Add one
      sentence to `INSTRUCTIONS`.
- [ ] 1.13 Integration (failing first): `tests/integration/mcp_server/test_regime_calendar.py`:
      build SPY from the real `firstrate-etf` catalog, rebuild into a scratch schema, and check
      that every label is equal (**exit check: rebuild reproduces**).
- [ ] 1.14 Exit check: render SPY 2000-2026 with the legs shaded per scale (an HTML file
      attached to the PR), plus the leg count per scale and the median leg length. Allay
      confirms the 3 / 6 / 12 × ATR thresholds (`design.md` Q8) **before** phase 2 profiles
      any strategy.
- [ ] 1.15 Docs: `docs/agent/regime-attribution.md` (calendar, versions, rules), indexed in
      `docs/agent/architecture.md`. Update the README's MCP tool list.

## 2. Phase 2: regime profile, daily view (the stop-or-continue point)

Acceptance: `run_regime_attribution` profiles one run under ground truth and under `sma200`.
Bucket P&L sums to the run's total. Reports are stored, stamped and exportable. Both baselines
are profiled and the stop-rule reading is recorded.

- [ ] 2.1 Prerequisite runs (no code; `design.md` section 0(e), Q6), made from a clean tree:
  - a trend baseline: core `sma_crossover`, SPY, `1-DAY-LAST`, `firstrate-etf`,
    2015-01-02 → 2025-12-31
  - Bollinger Reversal on the same window and commit (or keep `c4c9c098` if Allay prefers)

      Both must have `metrics_basis = mark_to_market`. Record the run ids here.
- [ ] 2.2 Before any new code, run the existing `get_regime_breakdown` and
      `get_sleeve_correlation` on both baselines (spec: "they give a rough answer today") and
      keep the output for comparison.
- [ ] 2.3 Unit (failing first): `tests/unit/core/regimes/test_attribution.py`:
  - Daily P&L is the change in equity, with the first day measured from initial capital.
  - **Reconciliation**: Σ bucket P&L (with `unlabelled`) = last equity − initial, and bucket
    days sum to labelled days.
  - Ground truth uses lag 0 by exact date. Detectors use lag *k* by reference session, with
    as-of fill for non-session days.
  - **Lag matters**: on a fixture with a known `up` → `down` flip inside the run, lag 0
    puts the flip day in `down` and lag 1 puts it in `up`, so the tables differ.
  - The detector as-of lookup is bounded by the version's `data_end`: run days after it are
    `unlabelled`, while weekends inside the range carry the earlier label.
  - Exposure follows `design.md` section 5.5: carried in from the previous close. A
    position opened at *d*'s own bar does not count; an intraday round trip inside *d* does.
  - Benchmark return covers the bucket's days.
  - Insufficient flag below 60 days or 20 trades.
  - Crossing chooses the widest-gap scale and records it.
- [ ] 2.4 Unit (failing first): `tests/unit/core/regimes/test_bootstrap.py`: a circular block
      bootstrap with 10-day blocks drawn from the whole run timeline before the bucket mask is
      applied. On a fixture with two bucket spells years apart, no resampled block contains
      days from both spells. The same seed gives the same interval. On i.i.d. data it
      covers the true mean near 90% (seeded Monte Carlo). The three-way family outcome is
      tested: `strong_dependence`, `no_dependence`, and `insufficient_evidence` when fewer
      than two buckets are sufficient (`design.md` section 5.4).
- [ ] 2.5 Implement `src/core/regimes/attribution.py` (daily view) and the circular block
      bootstrap in `src/core/regimes/bootstrap.py`. Take `sharpe` and the seeded interval
      helpers from `src/core/robustness/bootstrap.py` rather than reimplementing them.
- [ ] 2.6 Integration (failing first, Postgres): the `regime_reports` repository, append-only
      (the trigger rejects `UPDATE` and `DELETE`), with a non-empty `calendar_version_ids`
      (version and `build_no`) and `labels_hash`, and unknown ids refused.
- [ ] 2.7 Migration: `regime_reports` (`design.md` section 8). Update persistence docs and the
      single-head note.
- [ ] 2.8 Component (failing first): `tests/component/mcp_server/test_regime_attribution_tool.py`:
  - A realised-basis run is refused with `curve_missing`.
  - `lag_days` defaults to unset: ground truth without it runs at lag 0, and ground truth
    with any explicit `lag_days` (0 included) is refused with `lag_not_allowed`. Detectors
    without it run at lag 1.
  - The payload records versions with their `build_no`, `labels_hash`, params and
    `overrides`.
  - **Snapshot**: a profile stored at build *n*, rerun as of build *n* after a later build
    filled ground-truth values, reads identical label rows (hash match) and gives identical
    buckets.
  - Ground truth gets `hindsight: true` and the stamp; detectors get `hindsight: false`.
  - A stored report is written.
  - `export_results(slug, regime_report=...)` writes `<slug>.regime.json` and
    `<slug>.regime.md` only inside `NTRADER_MCP_EXPORT_FOLDERS`, and both carry the stamp
    when ground truth was used.
- [ ] 2.9 Implement `src/mcp_server/analysis/regime_reports.py` and the `run_regime_attribution`
      tool. Add the `regime_report` option to `export_results`. Pin the new tool in
      `REGISTERED_TOOLS` and `PINNED_TOOLS`.
- [ ] 2.10 Component (failing first): the `definitions` text of `get_regime_breakdown` and
      `get_sleeve_correlation` states that the tercile cut is hindsight. Their cells are
      unchanged (the existing e2e tests stay green). Then make the text change.
- [ ] 2.11 Integration (failing first): `tests/integration/mcp_server/test_regime_attribution.py`
      profiles a stored SPY run: reconciliation to the cent, lag 0 ≠ lag 1, and the report
      round-trips.
- [ ] 2.12 **Exit check**: profile both baselines under ground truth (every scale, ranging,
      volatility, and the widest-gap scale × volatility) and under `sma200`. Bucket P&L sums to
      each run's total. File both profiles in the vault with `export_results`.

> ### ⛔ PHASE-2 STOP RULE
>
> Stop after 2.12 and report the ground-truth profiles of the trend and mean-reversion
> baselines to Allay, with each family's outcome (`strong_dependence`, `no_dependence` or
> `insufficient_evidence`).
>
> **If neither baseline shows dependence under the ground-truth labels, the build stops
> here.** Phases 3-6 would not pay for themselves. A family reported as
> `insufficient_evidence` is not "no dependence". Report it as such and let Allay decide
> (`design.md` Q12).
>
> **Phases 3-6 are gated on Allay's explicit go-ahead**, whatever the result. Record the
> reading and Allay's decision (date, outcome) in this file before ticking 2.13.

- [ ] 2.13 Stop-rule decision recorded: _outcome_, _date_, _Allay's go/stop_.

## 3. Phase 3: detector panel and scorecard (gated on 2.13 = go)

Acceptance: detector version 2 holds `sma200`, `sma50`, `channel100`, `swing5` and
`volatility`. `get_regime_scorecard` grades every trend detector against every ground-truth
scale. A buy-and-hold SPY run shows higher volatility in both High buckets than in both Low
buckets.

- [ ] 3.1 Unit (failing first): extend `test_detectors.py`:
  - `sma50`.
  - `channel100` midpoint rule.
  - `swing5`: a swing counts only after 5 later bars; the higher-high and higher-low rule;
    mixed structure as decided in Q10.
  - `volatility` against its trailing 252-session median (never full-sample).
  - The shared 272-session warm-up.
  - No-lookahead perturbation for every detector.
- [ ] 3.2 Implement the four detectors. Build detector version 2 (the definition changes, so it
      is a new version, and version 1 is untouched).
- [ ] 3.3 Unit (failing first): `tests/unit/core/regimes/test_scorecard.py`:
  - Agreement over days where both labels exist, using the lag-1 detector.
  - Lag at turns as a median, with missed flips counted.
  - Flips per year.
  - The volatility detector scored against ground-truth volatility (agreement only).
- [ ] 3.4 Implement `src/core/regimes/scorecard.py`. `build_regime_calendar` computes and
      stores the scorecard (`regime_reports`, `kind = scorecard`) whenever either version
      changes.
- [ ] 3.5 Component (failing first): `get_regime_scorecard` returns the stored card with the
      versions it used, and refuses with a `fix` when no card exists. Then implement it and pin
      it in `REGISTERED_TOOLS` and `PINNED_TOOLS`.
- [ ] 3.6 `run_regime_attribution` detector buckets become each trend detector × `volatility`
      (4 buckets). Unit-test the bucket construction first.
- [ ] 3.7 **Exit check (spec "known case")**: integration, profile a buy-and-hold SPY run under
      every detector. Annualised volatility in `*/high` > `*/low` for every detector.
      Otherwise the dates are misaligned and the phase does not merge.

## 4. Phase 4: trade view, alignment, intervals and verdicts (gated on 2.13 = go)

Acceptance: trade counts sum to the run's trade count. The alignment table, the panel table
and the fixed verdicts are reported.

- [ ] 4.1 Unit (failing first): extend `test_attribution.py`:
  - Closed trades are bucketed by the label in force on the entry day (same lag rule).
    Trades entered on unlabelled days (warm-up, ground truth not yet final, past the catalog
    end) go in an `unlabelled` row. Open trades are counted apart.
  - Win rate, average win and loss, expectancy, profit factor.
  - **Reconciliation**: Σ closed trades, `unlabelled` included, = `total_trades`, and closed
    + open = trade rows.
  - With-trend / counter-trend tags for `BUY` and `SELL`. A trade with no trend label at
    entry is `unclassified`, in its own row, and excluded from the direction-gate rule.
  - The trade-view expectancy interval.
- [ ] 4.2 Implement the trade view and alignment in `attribution.py`. Take `profit_factor`,
      `win_rate` and the i.i.d. interval helpers from `src/core/robustness/bootstrap.py`; do
      not redefine them.
- [ ] 4.3 Unit (failing first): `tests/unit/core/regimes/test_verdicts.py`. Each rule in the
      spec's "Reading the results" tables gets a positive and a negative case:
  - `no_regime_harm`, `reliable_loss_bucket`
  - `fragile_concentration` (> 70% of profit from < 30% of time)
  - `differences_are_noise`
  - `holds_across_definitions` (≥ 3 of 4), `definition_dependent`
  - `direction_gate_candidate`, `drift_not_edge`
  - ground truth vs detectors: `regime_irrelevant`, `regime_detectable`, `detected_too_late`

      Insufficient buckets are never cited. The thresholds are pinned constants.
- [ ] 4.4 Implement `src/core/regimes/verdicts.py` and the panel table (one row per trend
      definition).
- [ ] 4.5 Integration: profile both baselines and, if Allay has pointed at them (Q7), the SPY ORB
      runs. **Exit check**: trade counts sum to each run's `total_trades`. File the profiles.

## 5. Phase 5: multiple runs (gated on 2.13 = go)

Acceptance: several runs give a full-period and per-bucket correlation and an equal-risk
combination profile.

- [ ] 5.1 Unit (failing first): `test_attribution.py`:
  - Inner join on shared days.
  - Per-bucket Pearson, `null` + insufficient under `min_days` shared days.
  - Inverse-volatility weights. A zero-volatility run makes the combination `null` with
    `reason: "zero_volatility"`, naming the run.
  - The combination profiled as a column.
  - `uncovered_regime` when two runs lose reliably in the same bucket.
- [ ] 5.2 Implement it in `attribution.py`, reusing `src/core/robustness/portfolio.py`
      (`correlation`, `portfolio_returns`) rather than duplicating it.
- [ ] 5.3 Component: a multi-run payload shape, and the full-window-weights note.
- [ ] 5.4 Integration exit check: profile both baselines together. File the profile.

## 6. Phase 6: filter test (gated on 2.13 = go, and only for a flagged strategy and bucket)

Acceptance: `run_regime_filter_test` runs the spec's split-half test against 1,000 random block
removals, on detector labels only.

- [ ] 6.1 Unit (failing first): `tests/unit/core/regimes/test_filter_test.py`:
  - Split by date.
  - First-half confirmation, else `bucket_not_confirmed`.
  - Half and off scaling.
  - Random removals of exactly the same day count, in non-overlapping runs of the
    first-half median spell length, seeded. Only the last run may be shorter (a 2-day spell
    with 3 flagged days gives runs of 2 and 1).
  - Pass only when both Sharpe and max drawdown beat the 95th percentile.
  - `candidate` until a second instrument or period passes.
- [ ] 6.2 Implement `src/core/regimes/filter_test.py`. It must not import `ground_truth`; the
      boundary test from 1.10 already forbids it.
- [ ] 6.3 Component (failing first): `label_set="ground_truth"` is refused (hard rule). The
      result is stored as `kind = filter_test` with the limits text from `design.md`
      section 7. Then implement the glue in `src/mcp_server/analysis/regime_filter.py`
      (detector versions only) and the tool, and pin it in `REGISTERED_TOOLS` and
      `PINNED_TOOLS`.
- [ ] 6.4 Integration: run the test on the flagged bucket from phase 4. Repeat on a second
      instrument or period (spec step 6). File the result.

## 7. Archive

- [ ] 7.1 After the last phase that merges (phase 2 if the stop rule fires), run
      `/openspec-archive-change add-regime-attribution` so `openspec/specs/regime-calendar` and
      `openspec/specs/regime-attribution` match the code. If the build stops at phase 2, first
      trim the phase 3-6 requirements from the deltas (detector panel, scorecard, trade view
      and verdicts, multiple runs, filter test) and record why.
