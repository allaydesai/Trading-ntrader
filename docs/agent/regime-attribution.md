# Regime calendar and attribution

The design and plan are the OpenSpec change `openspec/changes/add-regime-attribution/`
(`design.md`, `tasks.md`). This page is the working summary of what is built. Phase 1 (the
calendar) is built; attribution (phase 2 on) is not yet.

## Calendar

One **version** per (reference, catalog, label set, definition hash, input revision), numbered
per (reference, catalog, label set). One **label row** per version and reference session
date (the daily bar's UTC date, which is the New York session date for FirstRate bars).
Storage: `docs/agent/persistence.md`, "Regime Calendar".

| Label set | Keys | Rule | Code |
|---|---|---|---|
| `ground_truth` (hindsight) | `leg_minor`, `leg_intermediate`, `leg_major` | directional change on closes; a peak (trough) is confirmed when a close falls (rises) 3 / 6 / 12 × ATR20 from it, using the ATR at the extreme's date; ATR20 is a simple mean of true range; days in (trough, peak] are `up`, (peak, trough] `down`, outside the first and last turning point `null` | `src/core/regimes/ground_truth.py` |
| | `ranging` | efficiency ratio over the 21 sessions centred on the day < 0.3; `null` within 10 sessions of either end | |
| | `volatility` | centred 21-session realised volatility (annualised ×252) `high` above the version's **frozen** median (`frozen_stats`), else `low` | |
| `detector` (past data only) | `sma200` | `up` when close > SMA200 of closes up to *t*; the first 272 sessions are `null` | `src/core/regimes/detectors.py` |

Definitions (`src/core/regimes/definition.py`) hold parameters only; the default hashes are
pinned in `tests/unit/core/regimes/test_definition.py`. A changed parameter is a new version
(`build_regime_calendar(definition_overrides={"detector": {"sma200": {"window": 150}}})`).

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

Only daily bars stamped at 00:00 New York are accepted (their UTC date is the session
date). Futures daily bars open at `D-1 17:00` New York and are refused
(`unsupported_session_stamp`) until the futures mapping lands (tasks.md 1b).

## Tools

`build_regime_calendar(benchmark_symbol="SPY", catalog="firstrate-etf", ...)` and
`get_regime_labels(..., label_set="detector"|"ground_truth", start, end, version,
as_of_build, limit)`; `src/mcp_server/tools/regimes.py`. Without `version`, reads use the
newest revision of the **default** definition, never an override's version; pass `version`
to read a declared robustness check. `revision_reason="median_recut"` revises ground truth
only. Tool code: `src/mcp_server/tools/regimes.py`, glue in
`src/mcp_server/analysis/regime_calendar.py`.
