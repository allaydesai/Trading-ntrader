## ADDED Requirements

### Requirement: Regime calendar is stored per reference, label set, version and date
The system SHALL store regime labels in Postgres. It SHALL keep one
`regime_calendar_versions` row per reference market, label set (`ground_truth` or
`detector`) and definition version, holding the full definition, its hash, the catalog, the
data range read and the code commit. It SHALL keep one `regime_labels` row per version and
reference session date. Labels SHALL be looked up by date and SHALL never be computed by the
tools that read them.

#### Scenario: first SPY build
- **WHEN** `build_regime_calendar` runs for `SPY` in `firstrate-etf` with the default
  definition and no stored calendar
- **THEN** it creates ground-truth version 1 and detector version 1, each with one label row
  per SPY session date from 2000-01-03 to the last daily bar, and returns both version
  numbers and the data range

#### Scenario: same definition again
- **WHEN** `build_regime_calendar` runs again for `SPY` with the same definition and no new
  bars
- **THEN** no version is created, no row changes, and it reports the existing versions as
  up to date

### Requirement: Detector labels are append-only
The system SHALL never update or delete a stored detector label. A build over newer bars SHALL
only append rows for session dates after the version's last stored date. The database SHALL
reject any `UPDATE` or `DELETE` of a detector label row.

#### Scenario: new bars arrive
- **WHEN** the catalog gains SPY bars after the detector version's last stored date and
  `build_regime_calendar` runs with the same definition
- **THEN** rows are appended for the new dates and every earlier row is byte-identical to
  before

#### Scenario: direct update attempt
- **WHEN** any SQL statement tries to change or delete a stored detector label row
- **THEN** the database raises an error and the row is unchanged

### Requirement: Ground-truth labels are filled once and never rewritten
A ground-truth label that depends on future data SHALL be stored as `null` until that data
exists. A later build SHALL replace a `null` value with its final value, and SHALL never
change a value that is already set. The database SHALL reject any other change and any
delete.

#### Scenario: centred window completes
- **WHEN** a build runs ten or more sessions after a date whose ranging and volatility labels
  were stored as `null`
- **THEN** those labels are filled in and every label that was already set is unchanged

#### Scenario: attempt to change a set label
- **WHEN** any write tries to change a stored ground-truth value from `up` to `down`
- **THEN** the write is rejected

### Requirement: A changed definition creates a new version
The system SHALL treat any change to a calendar definition parameter (wave multiples, ATR
window, efficiency-ratio window or threshold, volatility windows, detector windows, warm-up)
as a new version with the next version number. It SHALL leave every earlier version and its
labels untouched, so that earlier reports stay reproducible.

#### Scenario: declared robustness check on the trend window
- **WHEN** `build_regime_calendar` is called for `SPY` with a definition override setting the
  long-average detector to 150 days
- **THEN** a new detector version is created whose definition records the override, and the
  default version's rows are unchanged

### Requirement: Ground-truth labels follow the spec's hindsight rules
The system SHALL label each SPY session with:
- a wave-leg direction at three scales, whose reversal thresholds are 3, 6 and 12 times the
  20-day average true range (days between a confirmed trough and the next peak are `up`, and
  days between a peak and the next trough are `down`)
- a ranging flag, true when the efficiency ratio over the 21 sessions centred on the day is
  below 0.3
- a volatility label, `high` when the centred 21-session realised volatility is above the
  version's median

Days before the first confirmed turning point at a scale, and days after the last one, SHALL
carry no direction at that scale.

#### Scenario: leg after the last turning point
- **WHEN** the latest confirmed major turning point is a trough and price has since risen less
  than 12 × ATR20
- **THEN** every day after that trough has a `null` major-scale direction

#### Scenario: rebuild reproduces the stored labels
- **WHEN** a ground-truth version is rebuilt from the same bars and definition
- **THEN** every recomputed label equals the stored label for every date

### Requirement: Detector labels use only past data and carry a warm-up
Every detector label for session *t* SHALL be computed from bars up to and including the
close of *t* only. The volatility detector's median SHALL be trailing (252 sessions). The
first 272 sessions of reference data SHALL carry no detector label.

#### Scenario: perturbing the future
- **WHEN** every bar after a session *t* is altered and the detectors are recomputed
- **THEN** every detector label dated on or before *t* is unchanged

#### Scenario: warm-up
- **WHEN** detector labels are built from SPY bars starting 2000-01-03
- **THEN** the first 272 sessions have `null` for every detector and the 273rd session has a
  label for every detector

### Requirement: Ground-truth labels are never exported to trading paths
The system SHALL NOT pass ground-truth labels to any strategy, strategy config, backtest
submission, sweep, filter or bar export. Only the regime calendar build and the attribution
and scorecard readers SHALL import the ground-truth module or read ground-truth rows. A
governance test SHALL pin that set of importers against the code.

#### Scenario: a strategy module imports ground truth
- **WHEN** any module outside the permitted set imports `src.core.regimes.ground_truth` or
  reads ground-truth rows from the regime repository
- **THEN** the governance unit test fails and names the module

#### Scenario: ground-truth labels in a filter test
- **WHEN** `run_regime_filter_test` is called with `label_set="ground_truth"`
- **THEN** it refuses with an error whose fix says filters take detector labels only

### Requirement: Detector scorecard grades each detector against each ground-truth scale
The system SHALL compute, for each detector against each ground-truth scale:
- agreement: the share of days where both labels exist and the lag-1 detector direction equals
  the ground-truth leg
- lag at turns: the median number of sessions from a ground-truth turning point to the
  detector's flip, with flips that never come counted as missed
- flips per year

The scorecard SHALL be stored with the versions it was computed from, refreshed whenever
either version changes, and returned by `get_regime_scorecard`.

#### Scenario: a slow detector
- **WHEN** the scorecard is computed for SPY
- **THEN** each of the four trend detectors has one row per scale with agreement, median lag,
  missed turns and flips per year, and the payload names the ground-truth and detector
  versions used
