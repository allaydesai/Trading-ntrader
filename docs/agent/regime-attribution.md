# Regime calendar and attribution

The design and plan are the OpenSpec change `openspec/changes/add-regime-attribution/`
(`design.md`, `tasks.md`). This page is the working summary of what is built. Phase 1 (the
calendar) and phase 2 (the daily-view regime profile, `run_regime_attribution`) are built;
phases 3-6 wait on Allay's go-ahead at the phase 2 stop rule.

## Calendar

One **version** per (reference, catalog, label set, definition hash, input revision), numbered
per (reference, catalog, label set). One **label row** per version and reference session
date, mapped from the daily bar's stamp by `src/core/regimes/sessions.py` (see "Session
dates" below).
Storage: `docs/agent/persistence.md`, "Regime Calendar and Reports".

| Label set | Keys | Rule | Code |
|---|---|---|---|
| `ground_truth` (hindsight) | `leg_minor`, `leg_intermediate`, `leg_major` | directional change on closes; a peak (trough) is confirmed when a close falls (rises) 3 / 6 / 12 × ATR20 from it, using the ATR at the extreme's date; ATR20 is a simple mean of true range; days in (trough, peak] are `up`, (peak, trough] `down`, outside the first and last turning point `null` | `src/core/regimes/ground_truth.py` |
| | `ranging` | efficiency ratio over the 21 sessions centred on the day < 0.2 (`ranging_threshold`; v1 used 0.3); `null` within 10 sessions of either end | |
| | `volatility` | centred 21-session realised volatility (annualised ×252) `high` above the version's **frozen** median (`frozen_stats`), else `low` | |
| `detector` (past data only) | `sma200` | `up` when close > SMA200 of closes up to *t*; the first 272 sessions are `null` | `src/core/regimes/detectors.py` |

Definitions (`src/core/regimes/definition.py`) hold parameters only; the default hashes are
pinned in `tests/unit/core/regimes/test_definition.py`. A changed parameter is a new version
(`build_regime_calendar(definition_overrides={"detector": {"sma200": {"window": 150}}})`).

Stored SPY versions (`firstrate-etf`). Without `version`, `get_regime_labels` reads the
default definition's calendar, which is v2 for ground truth since the 0.2 default; a database
holding only v1 answers `no_calendar` until `build_regime_calendar()` is run once. Pass
`version=1` to read v1. A new version freezes the volatility median over the catalog **as
it is when the version is created**, so compare a new version's `frozen_stats` and range with
the version it replaces before comparing labels.

| Label set | Version | Definition | Note |
|---|---|---|---|
| `ground_truth` | 1 | `ranging_threshold` 0.3 (hash `5fc1c016…`) | phase 1; ranging on 70% of sessions. Extend or verify it with `definition_overrides={"ground_truth": {"ranging_threshold": 0.3}}` |
| `ground_truth` | 2 | default, `ranging_threshold` 0.2 (hash `45e4389e…`) | phase 2 on (design.md Q14). Built 2026-11-27 from the unchanged catalog: frozen median (0.13723) and range checked equal to v1's |
| `detector` | 1 | default (`sma200`) | unchanged by the ground-truth v2 |

Stored ES and NQ versions (`firstrate-futures`, tasks.md 1b, built 2026-11-27). Each futures
root is its own reference with its own versions and frozen median; SPY labels are never
reused for them. Both are v1 of the **same default definitions as SPY v2** (ground truth
`45e4389e…`, detector `sma200`), over 2008-01-02 → 2026-05-01 (4,623 sessions):

| Reference | Ground truth v1 frozen median | Detector v1 |
|---|---|---|
| ES | 0.13518 | `sma200`, labels from 2009-01-29 |
| NQ | 0.16927 | `sma200`, labels from 2009-01-29 |

## Rules

- **Append-only.** Detector rows never change. A ground-truth value goes `null` → value once,
  when the future it needs has printed, stamped with the build that set it. Nothing is
  deleted. Enforced by DB triggers, not just the repository.
- **Fingerprints.** Each row stores the hash of its session's OHLC. A build over the stored
  range refuses `reference_bars_changed` if a bar changed, or a session disappeared or
  appeared; `new_revision=true, revision_reason="reference_bars_changed"` relabels as a new
  input revision and leaves the old version alone. A re-cut median is `median_recut`.
- **Snapshots.** `get_regime_labels(as_of_build=n)` hides rows inserted and values set after
  build *n*, and reports build *n*'s `data_end` from `regime_calendar_builds`.
- **Hindsight never trades.** Only the files named in
  `tests/unit/governance/test_regime_ground_truth_boundary.py` may import
  `src.core.regimes.ground_truth` or call `ground_truth_labels`. Ground-truth output carries
  `hindsight: true` and `stamp: "hindsight, not tradeable"`.

## SPY data

`firstrate-etf` SPY daily bars (2000-01-03 → 2026-05-01, 6,622 sessions) are **not adjusted
for dividends** (closes match the raw prints, e.g. 222.95 on 2020-03-23), and SPY has had no
split, so there is no split adjustment to check. Close-to-close returns therefore exclude
dividends; the phase 2 benchmark return does too (design.md section 5.6).

## QQQ data

`firstrate-etf` QQQ daily bars run 2000-02-28 → 2026-05-01 (6,585 sessions, stamped like
SPY's). QQQ ground truth v1 and detector v1 (built 2026-11-27) are the default definitions,
the same hashes as SPY ground truth v2 and detector v1 (ER < 0.2, 3 / 6 / 12 × ATR20), with
QQQ's own frozen volatility median 0.1778 (SPY 0.1372). Detector labels start 2001-03-27.
Legs (up / down, median sessions): minor 98 / 99, 22; intermediate 37 / 38, 76; major 8 / 9,
289. Ranging share 47.9%.

## Futures data (ES, NQ)

`firstrate-futures` holds FirstRate's **back-adjusted continuous** series only (no
per-contract bars). Per the vendor (firstratedata.com FAQ and price-adjustment pages, read
2026-11-27): rolls happen on a fixed date per ticker set from historical volume changeover
(ES/NQ about 7-10 days before expiry); the adjustment is **ratio**, one factor per roll
(incoming ÷ outgoing settle on the last day both settled), compounded backwards so the latest
contract is unadjusted. A daily bar is the CME trading day (18:00 → 17:00 New York) and its
close is the **official settlement**, not the 16:00 or 17:00 last trade (checked: it matches
neither hourly close). Ratio adjustment keeps percent returns, ATR-relative legs and realised
volatility the same across rolls and never goes negative (min close ES 648.97, NQ 1,183.70);
the roll windows show no gaps (median |open ÷ previous close − 1| 4 bp vs 5 bp elsewhere for ES).

On the same 2008 → 2026 window, futures legs are fewer and longer than SPY's at the minor
scale (ES 68 / 69 legs vs SPY 87 / 88): a Globex bar's range includes the overnight session,
so ATR20 is about 2.5× the median daily move against 2.2× for SPY, and k × ATR20 is a wider
threshold in return terms.

## Session dates

`src/core/regimes/sessions.py` maps each daily bar's stamp to its session date. One
convention per series, read on the New York wall clock (so DST moves only the UTC hour):

- 00:00 New York: the session date itself. FirstRate ETF **and futures** daily bars are
  stamped this way.
- 17:00 New York: the importers' session stamp (the end of the previous trading day), so the
  bar belongs to the next calendar day (`D-1 17:00` → `D`; a Sunday stamp → Monday). No
  futures series uses it today. It is not the exchange open: Globex opens ES/NQ at 18:00
  New York, and an 18:00 stamp is refused. A Friday or Saturday 17:00 has no session and is refused; a
  holiday has no bar, so no date. A 00:00 stamp on a Saturday or Sunday is refused too.

FX has no regime calendar: its daily bars are `MID` (the calendar reads `1-DAY-LAST`), and
the FX importer keeps out-of-session weekend bars, which this guard refuses by design. FX
trade days use their own rule, `src/services/fx/sessions.py`.

Any other stamp, or a series mixing both, is refused with `unsupported_session_stamp`,
never keyed by its UTC date.

## Tools

`build_regime_calendar(benchmark_symbol="SPY", catalog="firstrate-etf", ...)` and
`get_regime_labels(..., label_set="detector"|"ground_truth", start, end, version,
as_of_build, limit)`; `src/mcp_server/tools/regimes.py`. Without `version`, reads use the
newest revision of the **default** definition, never an override's version; pass `version`
to read a declared robustness check. `revision_reason="median_recut"` revises ground truth
only. Tool code: `src/mcp_server/tools/regimes.py`, glue in
`src/mcp_server/analysis/regime_calendar.py`.

## Attribution (phase 2): `run_regime_attribution`

Code: core `src/core/regimes/attribution.py` (daily view), `bootstrap.py` (intervals and the
stop-rule outcome), `trade_view.py` (trade view, alignment, correlation); glue
`src/mcp_server/analysis/regime_reports.py`; vault files `regime_export.py`. It computes no
labels: it reads one calendar version as of one build and the reference's closes (benchmark
only).

- **Inputs.** Runs with a stored `mark_to_market` curve (else `curve_missing`), their trades,
  the version's rows as of the build (`as_of_build`, default current; rows inserted and values
  set later are hidden, and that build's `data_end` bounds the as-of lookup).
- **Daily P&L.** `E(d) − E(d−1)`, the first day from initial capital, one point per UTC date.
  Every family's rows plus `unlabelled` sum to the last equity point minus initial capital;
  when a position is open at the end the report says how far that is from `final_balance`.
- **Alignment.** Ground truth: the label dated exactly *d*, lag 0, any `lag_days` refused
  (`lag_not_allowed`). Detector: the label of the *k*-th session strictly before *d*
  (`lag_days`, default 1; 0 = on or before *d*), weekends carry the earlier label, a day past
  `data_end` is `unlabelled`.
- **Families.** Ground truth: `leg_minor`, `leg_intermediate`, `leg_major` (up / down),
  `ranging` (ranging / not_ranging), `volatility` (high / low) and `cross`: the scale with the
  widest |up − down| annualised return × volatility (scale and gaps recorded). Detector:
  `sma200`.
- **Per bucket.** Days, time share (of labelled days), P&L and share, mean daily return,
  annualised return (×252) and volatility (×√252), Sharpe, worst day, 90% interval, exposure
  (position carried in from the previous close, or a round trip inside the day), the
  reference's annualised return on the same days, drift (that return × the side held, +1 /
  −1 / 0, **not scaled by position size**), trades entered, `insufficient` (< `min_days` 60
  days or < `min_trades` 20 trades).
- **Position-scaled drift (phase 2b, opt-in).** `drift_scaling="position"` multiplies each
  day's reference return by the position actually carried in, `Σ sign × quantity ×
  close(d−1) / E(d−1)`, and adds `mean_position_weight` (+0.10 = long 10% of equity, flat
  days 0). `quantity` is the stored trade's, the peak size of a position scaled in or out,
  which overweights its smaller days. Only the drift columns change. A default call stores exactly the phase 2 payload
  (no `drift_scaling` key; a golden digest in `tests/unit/core/regimes/test_attribution.py`
  pins it, and the six stored phase 2 reports rerun byte for byte). The run must trade the
  reference itself, unlevered (`drift_scaling_unavailable` for another symbol, futures,
  where no multiplier is known, or FX, where quantity × close is not USD notional). **Reading it:** for a strategy that trades the reference,
  carried notional × the reference's move *is* its close-to-close P&L, so position-scaled
  drift ≈ the bucket's return and `excess_vs_drift` ≈ 0 (fills and costs only) by
  construction. It shows that a bucket's P&L is the exposure held there; it cannot show
  timing skill inside a bucket. The ±1 drift overstates a 10%-of-equity strategy's drift
  about tenfold.
- **Intervals.** Stationary bootstrap (Politis and Romano) of the run's whole daily timeline:
  1,000 resamples (`bootstrap_samples`), geometric blocks of mean 10 sessions, uniform starts,
  circular wrap, `seed` 0. Blocks are cut before a bucket is picked, so every day has the same
  inclusion probability and no block joins two separate spells except through the wrap. Mean
  10: regime spells and volatility cluster over days to weeks; ten sessions is the spec's block
  size, and geometric lengths keep the resampled series stationary. Each bucket gets a
  percentile interval on its mean daily return (a resample missing the bucket is redrawn and
  counted), and each regime-conditional difference (a two-bucket family's first minus second;
  for `cross`, each leg's high − low and each volatility's up − down) an interval from the
  same resamples.
- **Outcome per family** (the stop rule, design.md 5.4): `strong_dependence` when one
  sufficient bucket's interval is entirely below zero and another's entirely above;
  `no_dependence` with ≥ 2 sufficient buckets otherwise; `insufficient_evidence` with < 2.
  The difference intervals are reported beside it; they do not change the outcome.
- **Descriptive extras** (no verdict reads them in phase 2; the fixed verdicts are phase 4):
  trade view (closed trades by entry-day label, open counted apart), with / counter-trend /
  unclassified split for trend families, and for 2+ runs Pearson correlation on shared days,
  overall and per bucket (`null` + `insufficient` under `min_days`).
- **Stored and exported.** One `regime_reports` row per call, with `calendar_version_ids`
  (version and build), `labels_hash`, every parameter and the non-default ones under
  `overrides` (`label_set` and `as_of_build` are selectors, and `drift_scaling` moves no
  label, bucket or interval, so none of the three is an override).
  `export_results(slug, regime_report=<id>)` writes `<slug>.regime.json` and `.regime.md`;
  ground truth carries `hindsight: true` and the stamp.

The phase 2 baseline profiles and the stop-rule reading are recorded in `tasks.md` (2.12-2.13).
