## ADDED Requirements

### Requirement: Regime profile joins a run's daily equity to the stored calendar
`run_regime_attribution` SHALL take one or more completed run ids and SHALL build each run's
profile from that run's stored daily mark-to-market equity, its stored trades and the stored
calendar rows. It SHALL compute no labels of its own. It SHALL refuse a run whose stored curve
is not on the `mark_to_market` basis. `lag_days` SHALL default to unset and SHALL resolve per
label set. With `label_set="detector"` (the default), the label in force on run day *d* SHALL
be the one dated `lag_days` (1 when unset) reference sessions before *d*. With
`label_set="ground_truth"`, the label SHALL be the one dated *d*, with no lag, and any
explicitly given `lag_days`, 0 included, SHALL be refused with `lag_not_allowed`.

#### Scenario: ground truth without a lag argument
- **WHEN** `run_regime_attribution` is called with `label_set="ground_truth"` and no
  `lag_days`
- **THEN** it runs with lag 0, and the report records `lag_days: 0`

#### Scenario: ground truth with a lag argument
- **WHEN** `run_regime_attribution` is called with `label_set="ground_truth"` and
  `lag_days=1`
- **THEN** it refuses with `lag_not_allowed`

#### Scenario: realised-basis run
- **WHEN** `run_regime_attribution` is called with a run whose `metrics_basis` is not
  `mark_to_market`
- **THEN** it refuses with `curve_missing`, and the fix names `reproduce_run`

#### Scenario: lag is applied
- **WHEN** the same run is profiled under detector labels with `lag_days=0` and `lag_days=1`
- **THEN** the two daily-view tables differ, and each report records its `lag_days`

### Requirement: Daily view reconciles to the run's total P&L
The daily view SHALL measure each day's P&L as the change in stored equity, with the first day
measured from the run's initial capital. It SHALL put days without a label in an `unlabelled`
row. The P&L of all rows, `unlabelled` included, SHALL sum to the last equity point minus the
initial capital. The days of the labelled rows SHALL sum to the labelled day count. Per
bucket it SHALL report:
- days and time share
- P&L and P&L share
- annualised return (mean daily return × 252) and annualised volatility (× √252)
- Sharpe
- worst day
- a 90% block-bootstrap interval on mean daily return (10-day blocks, `bootstrap_samples`
  resamples, a recorded `seed`)
- exposure
- the reference's annualised return on the same days
- trades entered

#### Scenario: reconciliation on the mean-reversion baseline
- **WHEN** run `c4c9c098` (Bollinger Reversal, SPY, 2015-2025) is profiled
- **THEN** the sum of bucket P&L equals its last equity point minus 10,000,000.00 to the cent,
  and the report notes the open position that separates it from `final_balance`

#### Scenario: reproducible interval
- **WHEN** the same run is profiled twice with the same `seed`
- **THEN** every bucket's interval is identical

### Requirement: Thin buckets are marked insufficient
A bucket with fewer than `min_days` (default 60) days, or fewer than `min_trades` (default 20)
trades entered, SHALL be reported with `insufficient: true`. No verdict SHALL cite an
insufficient bucket.

#### Scenario: few major down-leg days
- **WHEN** a ground-truth major-scale `down` bucket holds 41 days
- **THEN** it is listed with its metrics and `insufficient: true`, and no verdict names it

### Requirement: Ground-truth reports are stamped hindsight
Every tool payload, stored report and vault file built from a ground-truth version SHALL carry
`hindsight: true` and the stamp `"hindsight, not tradeable"` at its top level. The same SHALL
hold for `get_regime_labels` with `label_set="ground_truth"`.

#### Scenario: ground-truth profile
- **WHEN** `run_regime_attribution` runs with `label_set="ground_truth"`
- **THEN** the payload, the stored `regime_reports` row and any exported vault file all carry
  `hindsight: true` and the stamp `"hindsight, not tradeable"`

#### Scenario: detector profile
- **WHEN** `run_regime_attribution` runs with `label_set="detector"`
- **THEN** the payload carries `hindsight: false` and no stamp

### Requirement: Every report records the calendar version and overrides
Every regime report SHALL be stored append-only in `regime_reports` with the calendar version
ids it read (never empty), the label set, every parameter, the non-default parameters listed
as `overrides`, and the code commit. A stored report SHALL never be rewritten. A new reading
SHALL be a new row.

#### Scenario: report names its versions
- **WHEN** a profile is computed against ground-truth version 1 and detector version 2
- **THEN** its stored row and payload list both version ids, and `export_results(slug,
  regime_report=<id>)` writes `<slug>.regime.json` and `<slug>.regime.md` that name them

#### Scenario: override recorded
- **WHEN** a profile is computed with `min_days=40`
- **THEN** the report lists `min_days: 40` under `overrides`

### Requirement: Stop rule after phase 2
The phase 2 profile SHALL report one outcome for each ground-truth label family:
- `strong_dependence`: at least one sufficient bucket's interval is entirely below zero and
  another's is entirely above
- `no_dependence`: at least two buckets are sufficient and that pattern does not hold
- `insufficient_evidence`: fewer than two buckets are sufficient, with the shortfall counts

Only `no_dependence` for every family of both baselines SHALL support "stop after phase 2".
Phases 3-6 SHALL NOT start until Allay has read the phase 2 profiles of the trend and
mean-reversion baselines and given an explicit go-ahead.

#### Scenario: no dependence
- **WHEN** both baselines report `no_dependence` for every ground-truth family
- **THEN** the phase 2 record states the stop-rule outcome "stop after phase 2", and no phase
  3-6 task is started

#### Scenario: too few trades is not "no dependence"
- **WHEN** a low-frequency baseline has fewer than two buckets with 20 or more trades entered
  in a family
- **THEN** that family reports `insufficient_evidence` with the counts, and that family does
  not count towards the stop outcome

### Requirement: Trade view, alignment and verdicts follow the spec's fixed rules
The trade view SHALL bucket closed trades by the label in force on their entry day. Trades
entered on a day with no label SHALL go in an `unlabelled` row. Per bucket it SHALL report
trades, win rate, average win and loss, expectancy and profit factor, using the same metric
definitions as the run's stored metrics, and it SHALL count open trades separately. The
bucketed closed trades, `unlabelled` included, SHALL sum to the run's `total_trades`. Each trade SHALL be tagged with-trend (a `BUY` entered under `up`, or a `SELL`
under `down`) or counter-trend for each trend definition. Verdicts SHALL be the spec's fixed
rules (including the 70% / 30% concentration rule, the three-of-four panel rule, the
direction-gate rule and the drift rule), with thresholds held as constants and not exposed as
parameters.

#### Scenario: trade counts reconcile
- **WHEN** run `c4c9c098` is profiled in the trade view
- **THEN** its bucketed closed trades sum to 117 and it reports 1 open trade

#### Scenario: drift
- **WHEN** a long-only run's return in a bucket is no higher than its exposure times the
  benchmark return in that bucket
- **THEN** the verdicts include `drift_not_edge` for that bucket

### Requirement: Multiple runs report per-bucket correlation and an equal-risk combination
Given two or more runs, `run_regime_attribution` SHALL report the correlation of daily returns
on shared days, both overall and per bucket (`null` with `insufficient: true` for a bucket
under `min_days` shared days). It SHALL also report the profile of an equal-risk combination
weighted by the inverse volatility of each run's daily returns, and it SHALL state that those
weights use the full window.

#### Scenario: two baselines
- **WHEN** the trend and mean-reversion baselines are profiled together
- **THEN** the payload has a full-period correlation matrix, one matrix per bucket, and a
  profile column for the combination

### Requirement: Filter test must beat random removal
`run_regime_filter_test` SHALL accept only detector labels. It SHALL confirm the flagged
bucket on the first half of the run's labelled days. It SHALL apply the half-size (default) or
off filter to the second half. It SHALL compare the filtered Sharpe and max drawdown against
1,000 seeded random removals of the same number of days, in contiguous runs whose length is
the bucket's median spell length in the first half. It SHALL pass only when both metrics beat
the 95th percentile of the random versions. A pass SHALL be reported as a candidate until a
second instrument or period also passes.

#### Scenario: bucket not confirmed in the first half
- **WHEN** the flagged bucket's first-half interval is not entirely below zero
- **THEN** the tool refuses with `bucket_not_confirmed` and runs no baseline

#### Scenario: single pass
- **WHEN** the filter passes on one run only
- **THEN** the stored result says `candidate`, not `rule`

### Requirement: Existing regime tools keep their contract
`get_regime_breakdown` and `get_sleeve_correlation` SHALL keep their inputs and outputs. Their
`definitions` SHALL state that the volatility terciles are cut over the run's (or the shared)
days and are therefore a partial hindsight view.

#### Scenario: breakdown definitions
- **WHEN** `get_regime_breakdown` is called on any run
- **THEN** its cells are unchanged, and `definitions.volatility` says the tercile cut uses the
  run's own days (hindsight)

### Requirement: Regime tools are registered and pinned
The research MCP server SHALL register `build_regime_calendar`, `get_regime_labels`,
`run_regime_attribution`, `get_regime_scorecard` and `run_regime_filter_test`, each in the
phase that builds it. Each SHALL be added to `REGISTERED_TOOLS` and to the test literal
`PINNED_TOOLS` in the same commit. No name SHALL contain a word that the protocol-safety test
forbids.

#### Scenario: phase 1 registration
- **WHEN** phase 1 merges
- **THEN** the live tool list, `REGISTERED_TOOLS` and `PINNED_TOOLS` each include
  `build_regime_calendar` and `get_regime_labels`, and no other regime tool
