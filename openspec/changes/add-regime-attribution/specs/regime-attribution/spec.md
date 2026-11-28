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
- **WHEN** a fixture run whose detector label flips from `up` to `down` between two
  consecutive reference sessions inside the run is profiled with `lag_days=0` and
  `lag_days=1`
- **THEN** the flip day falls in `down` under lag 0 and in `up` under lag 1, the two
  daily-view tables differ, and each report records its `lag_days`

#### Scenario: run continues past the calendar
- **WHEN** a run has days after the detector version's last reference session
- **THEN** those days are `unlabelled` and are not filled from an earlier label

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
- a 90% stationary-bootstrap interval on mean daily return (`bootstrap_samples` resamples of
  blocks with geometric lengths of mean 10 days, drawn from the run's whole daily timeline with
  a circular wrap before the bucket's days are selected, so a block never joins separate spells
  except through the wrap and no day is underweighted, and a recorded `seed`)
- exposure
- the reference's annualised return on the same days
- trades entered

Beside the buckets it SHALL report, for each regime-conditional difference (a two-bucket
family's first bucket minus its second; for the crossed family each leg's high minus low and
each volatility level's up minus down), the difference in mean daily return and its 90%
interval from the same resamples. The difference intervals SHALL NOT change the family's
stop-rule outcome.

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
Every regime report SHALL be stored append-only in `regime_reports` with:
- the calendar version ids it read and each version's build number at read time (never empty)
- a hash of the label rows it read
- the label set
- every parameter, with the non-default ones listed as `overrides`
- the code commit

A stored report SHALL never be rewritten, and a new reading SHALL be a new row. Rerunning a
report as of its recorded build numbers SHALL reproduce its inputs and alignment exactly, even
after later builds extend the data or fill in null ground-truth values. It SHALL ignore rows
inserted after the recorded build, and it SHALL use that build's recorded data end as the
as-of boundary.

#### Scenario: rerun after the data is extended
- **WHEN** a detector profile is stored at a build whose data ends on a Friday, leaving the
  run's Saturday unlabelled, and a later build extends the data through Monday
- **THEN** a rerun as of the recorded build still leaves that Saturday unlabelled

#### Scenario: report reproduces after ground truth fills in
- **WHEN** a ground-truth profile is stored at build 3, a later build fills the last ten
  sessions' ranging labels, and the profile is rerun as of build 3
- **THEN** the rerun reads the same label rows (the hash matches) and gives identical buckets

#### Scenario: report names its versions
- **WHEN** a profile is computed against ground-truth version 1 and detector version 2
- **THEN** its stored row and payload list both version ids, and `export_results(slug,
  regime_report=<id>)` writes `<slug>.regime.json` and `<slug>.regime.md` that name them

#### Scenario: override recorded
- **WHEN** a profile is computed with `min_days=40`
- **THEN** the report lists `min_days: 40` under `overrides`

### Requirement: Drift can be scaled by position size, opt-in
`run_regime_attribution` SHALL take `drift_scaling`, `"side"` by default (drift = the
reference's daily return × +1 / −1 / 0 by the side carried in) or `"position"` (× the signed
quantity carried in × the reference's previous close / the previous equity, with each bucket's
`mean_position_weight`). Only the drift columns SHALL differ between the two. A default call
SHALL store exactly the payload phase 2 stored, so earlier reports reproduce. `"position"` SHALL
be recorded in `params` without declaring a robustness check, and SHALL be refused with
`drift_scaling_unavailable` for a run that does not trade the reference itself, or whose
instrument record (not its catalog name) is futures, FX, non-USD or missing, including a run
not read from a catalog.

#### Scenario: a phase 2 report reruns unchanged
- **WHEN** a stored phase 2 report is rerun with its recorded parameters and no `drift_scaling`
- **THEN** the result equals the stored result byte for byte, with no `drift_scaling` key

#### Scenario: position-scaled drift
- **WHEN** a SPY run is profiled with `drift_scaling="position"`
- **THEN** each bucket's drift is the mean of the reference return × the carried position
  weight, ×252, and buckets, P&L, intervals and outcomes equal the default report's

#### Scenario: another instrument
- **WHEN** a QQQ run is profiled against SPY's calendar with `drift_scaling="position"`
- **THEN** it is refused with `drift_scaling_unavailable` and a `fix`

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

#### Scenario: the crossed family is exploratory (Q16)
- **WHEN** a baseline reports `strong_dependence` only in `cross` (scale × volatility)
- **THEN** that is reported as an exploratory lead for phase 4 and does not count as
  dependence for the stop outcome

### Requirement: The stop-rule verdict is computed on request
`run_regime_attribution` SHALL take `report_version`, `1` by default (the phase 2 payload,
unchanged byte for byte, with no `report_version` key) or `2`. Version 2 SHALL add, to each
ground-truth run, a `stop_rule` verdict over the single ground-truth families only
(`dependence` if any is `strong_dependence`, else `insufficient_evidence` if any is, else
`no_dependence`), mark `cross` `exploratory: true`, and change no row, interval or outcome. It
SHALL be recorded in `params` without declaring a robustness check; any other value SHALL be
refused with `invalid_report_version`.

#### Scenario: version 2 leaves the numbers alone
- **WHEN** a run is profiled under ground truth with `report_version=2`
- **THEN** every family equals the default report's apart from `cross`'s `exploratory` flag,
  and the run's `stop_rule` lists the five single families as deciding and `cross` apart

### Requirement: Trade view, alignment and verdicts follow the spec's fixed rules
The trade view SHALL bucket closed trades by the label in force on their entry day. Trades
entered on a day with no label SHALL go in an `unlabelled` row. Per bucket it SHALL report
trades, win rate, average win and loss, expectancy and profit factor, using the same metric
definitions as the run's stored metrics, and it SHALL count open trades separately. The
bucketed closed trades, `unlabelled` included, SHALL sum to the run's `total_trades`. For each
trend definition, each trade SHALL be tagged:
- with-trend: a `BUY` entered under `up`, or a `SELL` under `down`
- counter-trend: a `BUY` under `down`, or a `SELL` under `up`
- unclassified: no trend label in force at entry. These are reported in their own row and
  never counted towards either group or the direction-gate rule

Verdicts SHALL be the spec's fixed
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
weights use the full window. If any run's daily returns have zero volatility over the shared
window, the combination SHALL be reported as `null` with `insufficient: true` and
`reason: "zero_volatility"`, naming those runs, and no weights SHALL be computed.

#### Scenario: a run that never moved
- **WHEN** one of the runs has constant equity over the shared window
- **THEN** the combination is `null` with `reason: "zero_volatility"` naming that run, and
  the correlation matrix reports `null` for that run's pairs

#### Scenario: two baselines
- **WHEN** the trend and mean-reversion baselines are profiled together
- **THEN** the payload has a full-period correlation matrix, one matrix per bucket, and a
  profile column for the combination

### Requirement: Filter test must beat random removal
`run_regime_filter_test` SHALL accept only detector labels. It SHALL confirm the flagged
bucket on the first half of the run's labelled days. It SHALL apply the half-size (default) or
off filter to the second half. It SHALL compare the filtered Sharpe and max drawdown against
1,000 seeded random removals of exactly the same number of days, in non-overlapping contiguous
runs whose length is the bucket's median spell length in the first half, rounded half up to
an integer with a minimum of 1. Only the final run of each removal MAY be shorter, so that the
total matches exactly. It SHALL pass only when
both metrics beat
the 95th percentile of the random versions. A pass SHALL be reported as a candidate until a
second instrument or period also passes.

#### Scenario: bucket not confirmed in the first half
- **WHEN** the flagged bucket's first-half interval is not entirely below zero
- **THEN** the tool refuses with `bucket_not_confirmed` and runs no baseline

#### Scenario: day count not a multiple of the spell length
- **WHEN** the first-half median spell length is 2 days and the filter flags 3 second-half
  days
- **THEN** every random removal takes exactly 3 days, as one 2-day run and one 1-day run

#### Scenario: fractional median spell length
- **WHEN** the bucket's first-half spells last 2 and 3 days (median 2.5) and the filter flags 7
  second-half days
- **THEN** the run length is 3, and every random removal takes exactly 7 days as runs of 3, 3
  and 1

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
