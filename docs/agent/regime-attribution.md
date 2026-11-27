# Regime calendar and attribution

The design and plan are the OpenSpec change `openspec/changes/add-regime-attribution/`
(`design.md`, `tasks.md`). This page is the working summary of what is built. Phase 1 (the
calendar) is built; attribution (phase 2 on) is not yet.

## Calendar

One **version** per (reference, catalog, label set, definition hash, input revision), numbered
per (reference, catalog, label set). One **label row** per version and reference session
date, mapped from the daily bar's stamp by `src/core/regimes/sessions.py` (see "Session
dates" below).
Storage: `docs/agent/persistence.md`, "Regime Calendar".

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
