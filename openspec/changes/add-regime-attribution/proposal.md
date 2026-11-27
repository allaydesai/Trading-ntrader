# Proposal: regime attribution study (phases 1-6)

Source: Allay's spec "Regime Attribution Study — NTrader Spec" (2026-11-10), kept verbatim in
`source-spec.md`. This change is step 0: the investigation (`design.md` section 0) and the plan.
It ships no code.

## Why

The study answers one question: how much of each strategy's P&L depends on the market regime it
traded in? That decides whether a regime filter is worth testing at all, and which regimes the
current strategies leave uncovered, which in turn decides what strategy three should be.

NTrader already has part of the answer, but not in a form the study can trust:

- **`get_regime_breakdown`** (`src/mcp_server/analysis/regimes.py`) splits a run by
  above/below the 200-day average and by a volatility tercile. It does lag its labels one day
  (`_previous_close`, `regimes.py:108-110`). Its tercile cut points, though, are taken over the
  run's own days (`regimes.py:126-129`), so they use future data. It recomputes labels from the
  run's own symbol on every call (`run_reads.py:144`), stores nothing, has no ground truth,
  no confidence intervals, no joint buckets and no minimum-sample rule.
- **`get_sleeve_correlation`** (`src/mcp_server/analysis/correlation.py:141`) correlates stored
  mark-to-market curves over the full shared window only, not per bucket, and reuses the same
  tercile labels (`sleeves.py:180-200`).
- There is **no stored regime calendar**, so two strategies can't be compared on the same
  regime days, and nothing records which label definition a reading used.

The inputs the study needs already exist. Runs store daily mark-to-market equity net of
commission and slippage (`run_equity_curves`). Trades are stored with entry and exit time,
side and P&L net of commission (`trades`). The `firstrate-etf` catalog holds SPY daily bars
from 2000-01-03, which gives 3,773 trading days of warm-up before 2015. Details and evidence
are in `design.md` section 0.

## What changes (by build phase; details in `design.md`, tasks in `tasks.md`)

- **Phase 1, calendar.** A versioned, stored regime calendar for SPY in Postgres (new Alembic
  migration): a `regime_calendar_versions` table (reference, catalog, label set, definition,
  definition hash, data range) and a `regime_labels` table (one row per version and session
  date). Ground-truth labels: wave legs at 3, 6 and 12 × ATR(20), a centered ranging flag and
  a centered volatility label. One detector: close above its 200-day SMA. New pure module
  `src/core/regimes/`, a sync repository, and two MCP tools: `build_regime_calendar` and
  `get_regime_labels`.
- **Phase 2, regime profile (daily view).** `run_regime_attribution` joins a run's stored daily
  equity to the calendar and returns the daily-view table per bucket, with benchmark return,
  exposure and the insufficient flag. Profiles are stored in a new append-only
  `regime_reports` table (second migration) stamped with the calendar version, and filed in
  the vault through a new additive `regime_report` option on `export_results`. Phase 2 also
  runs the two baselines and records the stop/continue decision. **Phases 3-6 are gated on
  Allay's go-ahead (stop rule).**
- **Phase 3, detector panel and scorecard.** The 50-day SMA, 100-day channel, swing-structure
  and trailing-volatility detectors, as a new detector calendar version. `get_regime_scorecard`
  returns agreement, lag at turns and flips per year against each ground-truth scale.
- **Phase 4, trade view, alignment and intervals.** Per-trade metrics by entry label, the
  with-trend / counter-trend split, 90% block-bootstrap intervals, the panel table and the
  fixed verdict rules.
- **Phase 5, multiple runs.** Per-bucket correlation and the equal-risk combination inside
  `run_regime_attribution` when it is given several runs.
- **Phase 6, filter test.** `run_regime_filter_test`: split-half confirmation, then the
  half-size or off filter against 1,000 random block removals. It takes detector labels only.

## Interfaces crossed

- **DB schema / migrations**: two new migrations off the single head `3f56b46c78eb` (phase 1:
  `regime_calendar_versions`, `regime_labels`; phase 2: `regime_reports`). No existing table
  changes. `docs/agent/persistence.md` and the single-head note in `CLAUDE.md` are updated.
- **MCP tool contract**: new tools `build_regime_calendar`, `get_regime_labels` (phase 1),
  `run_regime_attribution` (phase 2), `get_regime_scorecard` (phase 3) and
  `run_regime_filter_test` (phase 6). `export_results` gains an optional `regime_report`
  parameter (additive). `get_regime_breakdown` and `get_sleeve_correlation` keep their inputs
  and outputs; only their `definitions` text gains a hindsight note on the tercile cut
  (phase 2).
- **Pinned constants**: `REGISTERED_TOOLS` (`src/mcp_server/server.py:27`) and the literal
  `PINNED_TOOLS` (`tests/component/mcp_server/test_protocol_safety.py:16`) both gain each new
  tool in the same commit. The new names avoid every word in
  `test_no_tool_can_trade_or_manage_sessions` (`order`, `start`, `stop`, `import`, `fetch`,
  `live`, ...). `server.INSTRUCTIONS` gains one sentence on the regime tools.
- **Spec names vs this change** (matched to the server's conventions; table in `design.md`
  section 8): `regime_labels` becomes `build_regime_calendar` + `get_regime_labels`;
  `regime_attribution` becomes `run_regime_attribution`; `reference` becomes
  `benchmark_symbol` (plus `catalog`); `trend_window`, `vol_window` and `vol_lookback` move from
  the attribution call into the calendar definition, where a changed value is a new version;
  `label_set`, `lag_days`, `min_days`, `min_trades` and `bootstrap_samples` keep their names.
- **New import boundary**: a governance unit test pins that ground-truth code
  (`src/core/regimes/ground_truth.py`) is imported only by the regime calendar build and the
  attribution and scorecard readers. It is never imported by strategies, the backtest engine
  path, sweeps or job submission.

## Guard lists / pinned constants

- No `src/core/live_*.py` or `src/cli/commands/live*.py` module is touched, so no live guard
  list applies. The paper/live detector feed (spec step 8) is out of scope here.
- `FROZEN_VIEWS`: untouched. No `src/api/**` or `templates/**` change.
- `ANALYSIS_KINDS` (`src/db/models/research.py:59`) is **not** extended. Regime reports belong
  to a run, not to a study, and `research_analyses.study_pk` is NOT NULL (`research.py:77-80`),
  so they get their own table.
- Size caps: all new logic lives in new modules (`src/core/regimes/*`,
  `src/mcp_server/analysis/regime_*.py`, `src/mcp_server/tools/regimes.py`). `run_reads.py`,
  `regimes.py`, `correlation.py` and `sleeves.py` must not grow past their baselines.
- `.gitmodules` is not changed. No strategy (submodule) change.

## Out of scope

- Labelling individual stocks, intraday regimes, and hourly labels (spec non-goals).
- Searching for the best trend definition (the vault idea "Trend definition sweep on SPY").
- Trend-scanning labels, the jump-model detector, the breadth label and other reference markets
  (QQQ, IWM, TLT, GLD, ES/NQ). These are later additions behind the same calendar. The schema
  takes a reference per version, so adding one is a new version, not a migration.
- VIX as a volatility source. No VIX series is in any catalog today (open question in
  `design.md`).
- Any change to strategies, order placement, or paper and live sessions, including feeding
  detector labels to a paper session (spec step 8).
- Changing the promotion gates (G0-G4) or the vault's Idea template. Both are Allay's
  decisions and are listed as open questions.
- Web UI pages for regime reports. v1 is MCP and vault only.
- Re-running the engine for the filter test. Phase 6 scales stored daily returns (see
  `design.md` section 7 for the limits of that).
