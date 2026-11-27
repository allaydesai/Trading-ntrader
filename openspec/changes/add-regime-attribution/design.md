# Design: regime attribution study

The spec is `source-spec.md` (Allay, 2026-11-10). This document records what NTrader already
has (section 0, investigated 2026-11-26 on the VPS checkout at `660c9f5`), and how each build
phase maps onto it. Allay owns the decisions in section 13. Everything else here is a
recommendation, written down so it can be overruled before the phase that depends on it.

## 0. Investigation (2026-11-26)

DB evidence comes from read-only `SELECT`s, run in a `SET TRANSACTION READ ONLY` session,
against the VPS database the checkout's `.env` points at. Catalog evidence comes from reading
the `ts_event` column of the Parquet files under `data/catalogs/`. Nothing was written.

### (a) Daily mark-to-market equity per run: yes, daily, net of costs

- **Table**: `run_equity_curves` (`src/db/models/backtest.py:186-215`, migration
  `alembic/versions/40ab8c820444_add_run_equity_curves_table.py:24-34`). It holds one row per
  run (`backtest_run_id` unique) and a JSONB `points` list of `{"time": unix_seconds, "value":
  equity}` in time order.
- **Writer**: `BacktestOrchestrator._persist_results` (`src/core/backtest_orchestrator.py:545`,
  `:564`) calls `ResultsExtractor.extract_equity_curve`
  (`src/core/results_extractor.py:449-473`). That function prefers the mark-to-market rebuild
  (`:467-469`, `mark_to_market()` at `:587`) and falls back to
  `(1 + analyzer.returns()).cumprod()` (`:471-473`) only when the rebuild is unavailable. The
  run records which one it used in `config_snapshot.metrics_basis`
  (`backtest_orchestrator.py:546`; field doc `src/models/config_snapshot.py:42-44`).
- **Granularity**: **one point per day**. `daily_equity` keeps the last bar of each UTC day
  (`src/core/mark_to_market.py:179-180`). Spot FX uses 17:00 New York trade days
  (`fx_daily_equity`, `:144`) and spot crypto every calendar day. Example: run `c4c9c098`
  (SPY daily, 2015-2025) has 2,766 points; ES hourly runs over six months have 154.
- **Net of costs**: **yes**. Commission comes off cash at each fill (`spent = qty * price *
  multiplier + commission`, `mark_to_market.py:168`). Slippage is already in the fill price:
  the fill model slips with `prob_slippage=0.01` (`src/core/fill_model.py:34`), and the
  optional `slippage_per_share` is charged as commission (`src/core/fee_models.py:47-58`). The
  rebuild must match the engine's account balance or it is rejected
  (`results_extractor.py:580-584`).
- **Caveat 1, legacy path**: `src/core/backtest_runner.py:135,155` (the older `BacktestRunner`,
  still imported by `src/cli/commands/run.py` and `reproduce.py`) stores
  `(1 + analyzer.returns()).cumprod()` (`:983-1013`). Nautilus' `analyzer.returns()` holds one
  entry per **position close** (`add_return(ts_closed, realized_return)`, Nautilus
  `analysis/analyzer.py:161`). Those curves are realised and per-trade, not daily
  mark-to-market. `get_sleeve_correlation` already refuses them (`usable_curve`,
  `src/mcp_server/analysis/sleeves.py:58-63`). **`get_regime_breakdown` does not**
  (`run_reads.py:80-88` checks only that points exist).
- **Caveat 2, today's DB**: all 87 successful runs on the VPS are `metrics_basis =
  mark_to_market`, and every one has a stored curve.
- **Conclusion**: no daily-equity export is needed. The spec's open question 1 is answered
  "yes", and Phase 1 has no export prerequisite. The study refuses any run whose
  `metrics_basis` is not `mark_to_market`, as `get_sleeve_correlation` does.

### (b) Stored trade list: yes, `trades`, P&L net of commission

- **Table**: `trades` (`src/db/models/trade.py:31-148`). It has `entry_timestamp`,
  `exit_timestamp` (NULL while open), `order_side` (the **entry** side: `BUY` = long, `SELL` =
  short; written from the positions report's `entry` column,
  `src/services/backtest_persistence.py:569`), `quantity`, `entry_price`, `exit_price`,
  `commission_amount` and `profit_loss`.
- **Writer**: `BacktestPersistenceService`, from the Nautilus positions report
  (`backtest_persistence.py:486-590`). One row is written per **position**, open ones included
  (`:489-494`).
- **P&L**: `profit_loss` is the position's Nautilus `realized_pnl`, converted to USD
  (`usd_profit_loss`, `:60-77`, `:537-538`). Nautilus starts a position's realised P&L at minus
  its commission (`src/core/metrics.py:22-30`), and slippage is in the fill prices, so the
  stored P&L is **net of commission and slippage**. An open position stores `0.00`, and its
  unrealised P&L is only in the equity curve.
- **Counts**: `performance_metrics.total_trades` counts closed positions. Run `c4c9c098` has
  118 trade rows and `total_trades = 117`: one position was still open at the end, which is
  also why its last equity point (9,354,378.78) differs from `final_balance` (9,381,240.62).
  The trade-view reconciliation (phase 4) must therefore count closed trades.

### (c) How `get_regime_breakdown` and `get_sleeve_correlation` work

`get_regime_breakdown` (tool `src/mcp_server/tools/analysis.py:40-50`, logic
`src/mcp_server/analysis/run_reads.py:129-172` and `src/mcp_server/analysis/regimes.py`):

- **Bars**: daily closes of `benchmark_symbol`, which defaults to **the run's own symbol**
  (`run_reads.py:144`). They are read from the catalog the run used (`run_catalog`,
  `:45-48`; runs from a non-named source are refused, `:137-143`) through `daily_closes`
  (`src/mcp_server/analysis/bars.py:64-69`, `1-DAY` bars normalised to the UTC date). The
  read starts 300 **calendar** days before the run (`WARMUP_CALENDAR_DAYS`, `regimes.py:23`),
  about 206 trading days. That is enough for a 200-day SMA but not for the spec's 272-day
  warm-up.
- **Lag: yes, one day.** Trend and volatility labels are both `shift(1)`ed
  (`_previous_close`, `regimes.py:108-110`, used at `:119` and `:125`). The label dated *d*
  comes from the close of the previous benchmark bar and is applied to the run's return on
  *d*. That is the spec's "label at *t* applied to the return at *t+1*". A trade is
  bucketed by the label in force on its entry date (`:85`).
- **Volatility: trailing window, full-sample cut points.** The 21-day realised volatility is
  trailing (`:124-125`), but the tercile thresholds come from `in_run.quantile([1/3, 2/3])`
  over **all of the run's days** (`:126-129`). The cut therefore uses volatility from the
  future of each day, and the output is partly a hindsight view, as the spec suspected.
- **Returns**: daily close-to-close returns of the stored equity curve, with
  `pct_change().dropna()` (`regimes.py:40-44`). This **drops the first day's P&L**, so cell
  P&L never reconciles exactly to the run total. Trade P&L counts closed trades only (`:55-69`).
- No minimum sample, no intervals, no joint buckets, nothing stored.

`get_sleeve_correlation` (tool `src/mcp_server/tools/portfolio.py:28-56`, logic
`src/mcp_server/analysis/correlation.py:141-197`):

- Loads 2 to `max_sleeves` runs that have a **mark-to-market** curve (`load_sleeves`,
  `sleeves.py:66-96`), inner-joins their daily returns on shared dates (`align`), and reports
  the Pearson correlation matrix **over the full shared window** (`correlation.py:161`,
  `:173-182`), drawdown overlap, the constant-mix total and per-sleeve summaries.
- Its regime table (`regime_table`, `sleeves.py:180-200`) reuses `trend_labels` and
  `volatility_labels`, so the same lag applies and the same tercile hindsight applies (cut over
  the shared days). It shows per-label return and drawdown side by side, but not per-bucket
  correlation. The benchmark defaults to the first sleeve's symbol (`benchmark_of`,
  `correlation.py:126-138`).

### (d) SPY daily data: yes, from 2000-01-03, warm-up is ample

`data/catalogs/firstrate-etf/data/bar/SPY.ARCA-1-DAY-LAST-EXTERNAL` has 6,622 daily bars, from
**2000-01-03 to 2026-05-01**. Bars are stamped at 00:00 New York of the session date (04:00 or
05:00 UTC), so their UTC date is the session date. Trading days before candidate start dates:

| Start | SPY trading days before | ≥ 272? |
|---|---|---|
| 2015-01-01 / 2015-01-02 | 3,773 | yes |
| 2020-01-01 | 5,031 | yes |
| 2023-01-01 | 5,787 | yes |
| 2024-01-01 | 6,037 | yes |

The same catalog has QQQ (from 2000-02-28), IWM (from 2000-05-30), TLT and GLD daily bars.
`firstrate-futures` has ES and NQ daily bars from 2008-01-02. **No catalog has VIX.** The bars
end at 2026-05-01, so any run past that date has unlabelled tail days. Whether the ETF bars are
split- and dividend-adjusted is not recorded in the catalog. Phase 1 task 1.1 checks it, since
it matters for the drift benchmark (section 5.6).

**Task 1.1 result (2026-11-26): the SPY bars are unadjusted for dividends**, and SPY has had no
split. The catalog's closes are the raw prints: 222.95 on 2020-03-23 and 145.5625 on
2000-01-03 (a dividend-adjusted series is about 10% and 35% lower on those dates).
`catalog_dividends` holds 132 SPY ex-dates (1993-03-19 → 2026-03-20) that the prices do not
fold in, and `catalog_stock_splits` has no SPY row. Close-to-close returns, and so the drift
benchmark (section 5.6), exclude dividends on both sides.

### (e) Baseline candidates and ORB runs

The VPS DB holds 97 runs (87 successful) and **0 research studies**. All runs were made between
2026-11-13 and 2026-11-25 (engine QA for futures, FX and crypto). Allay's research history may
live in another database (the MCP server's instructions describe backtests "run natively on the
Mac", `src/mcp_server/server.py:43-45`). That is an open question in section 13.

Every candidate below has a stored mark-to-market daily curve:

| Run | Strategy | Instrument / bars | Range | Curve pts | Trade rows (closed) | Notes |
|---|---|---|---|---|---|---|
| `c4c9c098` | Bollinger Reversal (mean reversion) | **SPY**, 1-DAY, firstrate-etf | 2015-01-01 → 2025-12-31 | 2,766 | 118 (117) | long and short; clean tree; capital 10,000,000; return -6.2% |
| `7f1bda54` | Riptide (pullback in a 200-day uptrend) | AAPL, 1-DAY, firstrate-stocks | 2015-01-02 → 2024-12-31 | 2,515 | 27 | long-only; clean |
| `cf49cfef` | Riptide | AAPL, 1-DAY | 2020-01-01 → 2025-12-31 | 1,507 | 18 | long-only; clean |
| `31548651` | Riptide | ES, 1-DAY, firstrate-futures | 2020-01-01 → 2025-12-31 | 1,510 | 5 | long-only; clean |
| `be0d982a`, `df12f6a6` / `02be82e5`, `b339bdb8` | Bollinger Reversal | EURUSD / USDJPY, 1-DAY | 2015-01-02 → 2025-12-31 | 2,856 / 2,855 | 93 / 105 | FX: SPY is not their reference |
| 44 runs | Sma Crossover (trend) | AAPL, BTC, ETH, ES, CL, FX | ≤ 2 years, 2023-2024 | yes | yes | **none on SPY**, none longer than 2 years |

Findings:

- **Mean-reversion baseline**: `c4c9c098` is usable as it stands. It is the only SPY run in
  the DB.
- **Trend baseline**: **no usable run exists.** No trend-following run covers SPY or the
  2015-2025 window. Riptide uses a 200-day trend filter but buys pullbacks, so it is not a
  clean trend baseline, and it trades too rarely (5-27 trades) to pass the 20-trade minimum in
  most buckets.
- **Prerequisite (task 2.1)**: make a fresh trend run (the core `sma_crossover` strategy, SPY,
  `1-DAY-LAST`, `firstrate-etf`, 2015-01-02 → 2025-12-31, clean tree). Re-run Bollinger
  Reversal on the identical window and commit, so the two baselines share dates and code.
  Which strategies count as "the existing trend strategy" and "the existing mean-reversion
  strategy" is Allay's call (section 13, Q6).
- **ORB on SPY: none.** No run in this DB has an ORB or opening-range strategy name (failed
  runs included). The strategies submodule is not checked out in this worktree. The spec's ORB
  alignment question waits on ORB runs that phase 4 can read (section 13, Q7).

### (f) Where the calendar lives: Postgres (recommended)

Recommendation: **a new Alembic migration beside the run tables**, not files under
`~/.ntrader`. Reasons:

1. **The MCP server already reads everything the study joins from Postgres.** Runs, equity
   curves and trades all come through `get_sync_session()` (`run_reads.py:133-136`,
   `correlation.py:154-157`), and so do studies and the trial ledger (`research_*` tables,
   `src/db/models/research.py:86-233`). `~/.ntrader/mcp/jobs` holds only transient job files
   (`src/mcp_server/settings.py:27-28`). A calendar in files would be the only research state
   outside the database.
2. **Versioning and the report stamp are relational.** A report must record the calendar
   version it used. A foreign key from `regime_reports` to `regime_calendar_versions` makes
   that stamp impossible to dangle. A path to a file that someone can edit or delete cannot do
   that.
3. **Append-only can be enforced, not just promised.** The unique key `(version, session_date)`
   plus a `BEFORE UPDATE OR DELETE` trigger (section 1) rejects any change to a detector label
   and any change to a ground-truth value once it is set. Files have no equivalent.
4. **Reproducibility.** The version row stores the full definition as JSON, its hash, the
   catalog, the data range and the code commit. "A rebuild reproduces the stored labels
   exactly" (the phase 1 exit check) then becomes a query, not a file diff.
5. **Size is trivial.** About 6,600 SPY dates × a few versions is tens of thousands of rows.

Cost: two migrations (phase 1, phase 2), and the single-head note in `CLAUDE.md` moves.
Labels are tied to the database they were built in. That is acceptable because the runs they
are joined to live in the same database. The spec's phrase "NTrader's database beside the
catalogs" is this option. The catalogs themselves stay files.

## 1. Calendar schema (phase 1 migration; `src/db/models/regime.py`)

`regime_calendar_versions`:

| Column | Type | Notes |
|---|---|---|
| `id` | BigInteger PK | |
| `reference` | String(20) | `SPY` |
| `catalog` | String(100) | `firstrate-etf` |
| `label_set` | String(20) | CHECK in (`ground_truth`, `detector`) |
| `version` | Integer | 1, 2, ... per (`reference`, `catalog`, `label_set`); UNIQUE together. "Latest" lookups always filter on all three, so a second catalog can never silently replace the price series |
| `definition` | JSONB | **parameters only**: scales and ATR multiples, ER window and threshold, vol windows, detector list and windows, warm-up. Nothing derived from data |
| `definition_hash` | String(64) | sha256 of canonical `definition` JSON. The default definition is a constant, so its hash can be pinned by test |
| `input_revision` | Integer | 1, 2, ... per (`reference`, `catalog`, `label_set`, `definition_hash`). UNIQUE with those four. A plain rebuild with the same definition appends to the latest revision and never creates a duplicate. A new revision of the **same** definition is created only on an explicit request (`build_regime_calendar(new_revision=..., reason=...)`) after `reference_bars_changed`, or to re-cut the frozen median (section 3). It gets its own `version` number, and `revision_reason` records why |
| `revision_reason` | Text, nullable | NULL for revision 1. Otherwise `reference_bars_changed` or `median_recut`, plus a free-text note |
| `frozen_stats` | JSONB | values derived from data at version creation and never recomputed: the ground-truth volatility median and the sample window it was cut over (section 3). Not hashed. Set once on insert |
| `build_no` | Integer | starts at 1 and increments with every build that appends rows or fills ground-truth values (section 1, "Report snapshots") |
| `data_start`, `data_end` | Date | the bar range the latest build read |
| `git_commit` | String(40) | code that built it |
| `created_at`, `updated_at` | timestamptz | `updated_at` moves only when a build appends days |

`regime_labels`:

| Column | Type | Notes |
|---|---|---|
| `version_id` | FK → versions, `ON DELETE RESTRICT` | |
| `session_date` | Date | the reference's session date; PK (`version_id`, `session_date`) |
| `labels` | JSONB | see below; validated by a pydantic model on write |
| `inserted_in_build` | Integer | the `build_no` that inserted the row. Set on insert, never changed |
| `set_in_build` | JSONB | for each label key, the `build_no` that set its value (`null` while unset) |
| `bar_hash` | String(32) | truncated sha256 of that session's OHLC as read. Set on insert, never changed |

`regime_calendar_builds` (append-only, same trigger pattern): one row per build, holding
`version_id`, `build_no` (PK together), that build's `data_end`, `git_commit` and
`built_at`. It is the history behind the version row's current `build_no` and `data_end`.

**Bar fingerprint.** Each label row carries the hash of its own session's bar. A build
compares, over the version's stored range (`data_start` to `data_end`):

- the **set of session dates** in the catalog against the stored rows. A session missing
  from the catalog, or a new historical session inserted into it, is a difference.
- each stored date's recomputed `bar_hash`.

Any difference refuses the build (`reference_bars_changed`, naming the first differing date),
for example after a re-import with back-adjusted prices. New dates after `data_end` simply
add rows with their own hash. Extending the data mid-year, or by a single day, therefore
never touches a stored fingerprint. Re-labelling on changed bars needs a new
`input_revision`.

**Report snapshots.** A report reads a version at its current `build_no` and records
`(version_id, build_no)` for each version used, together with a sha256 of the label rows it
read. Reading "as of build *n*" uses that build's state:

- rows whose `inserted_in_build` is greater than *n* are ignored, including rows whose labels
  are all `null`
- values whose `set_in_build` is greater than *n* are treated as `null`
- the alignment boundary is the `data_end` recorded for build *n* in
  `regime_calendar_builds`, not the version's current `data_end` (section 2)

A rerun with the recorded pair therefore recovers the original inputs and alignment exactly,
even after later builds extend the data or fill in null ground-truth values. The stored hash
proves it.

Label payloads:

- ground truth: `{"leg_minor": "up"|"down"|null, "leg_intermediate": ..., "leg_major": ...,
  "ranging": true|false|null, "volatility": "high"|"low"|null}`
- detector (phase 1): `{"sma200": "up"|"down"|null}`. Phase 3 adds a new version with
  `{"sma200", "sma50", "channel100", "swing5", "volatility"}`.

**Append-only rules** (spec "Storage rules"). The repository has no update or delete method,
and a PL/pgSQL trigger `regime_labels_append_only` enforces the same rules in the database:

- A detector row is immutable once inserted. A build only **appends** dates after the last
  stored date.
- A ground-truth row may change a `null` value to a value (the future it needed has arrived),
  recording the current build in `set_in_build`. It may never change a set value, its
  `set_in_build` entry, or `bar_hash`. Any other change raises.
- `DELETE` on either table raises. Versions are never removed.
- A second trigger, `regime_versions_immutable`, rejects any `UPDATE` of a version row except
  one that moves `data_end` forward, increments `build_no`, and touches `updated_at`.
  `definition`, `definition_hash`, `input_revision`, `revision_reason`, `frozen_stats`,
  `reference`, `catalog`, `label_set`, `version` and `data_start` can never change.
- A changed definition gets a new `definition_hash`, so `build_regime_calendar` creates a
  **new version**. It never edits an old one. The same definition on changed bars, or with a
  re-cut median, becomes a new version only through an explicit new `input_revision`.

Why one version per label set: ground truth and detectors change on different schedules (a
detector panel grows in phase 3 while the ground truth stays fixed), and a report names the
versions it used for each.

Repository: `src/db/repositories/regime_repository_sync.py` (sync only). Every reader is the MCP
server, which is sync (`run_reads.py:133`). `docs/agent/persistence.md` says new DB features
update both repositories. Allay approved a **recorded exception** on 2026-11-26 (section 13,
Q13): no async caller exists, because the web UI is out of scope. Task 1.9 adds the exception
to `persistence.md`.

## 2. Dates and alignment

- **Calendar key**: the reference's session date (the UTC date of the daily bar, which is the
  New York session date for FirstRate daily bars, section 0(d)).
- **Run key**: the UTC date of each stored equity point (`daily_equity`,
  `mark_to_market.py:179`). For US equity and ETF runs on daily or intraday bars, this is the
  session date.
  - CME futures runs on hourly bars split their evening session at UTC midnight. That is a
    known approximation for ES/NQ against SPY, and it is noted in the report.
  - FX (17:00 New York days) and crypto (calendar days) do not map onto SPY sessions. The
    tool warns that SPY is not their reference (spec reference map).
- **Detector labels with lag *k*** (default 1): the run's day *d* takes the label of the
  *k*-th reference session strictly before *d* (*k* = 0: the label dated on or before *d*).
  Run days with no reference session of their own, such as weekends or exchange holidays,
  take the most recent earlier label. That is the label "in force". The as-of lookup is
  **bounded by the `data_end` of the build being read** (the current one, or the recorded
  one on a rerun, section 1): a run day after the last reference session is `unlabelled`,
  never filled from an older label.
- **Ground-truth labels** (lag fixed at 0): the run's day *d* takes the label dated exactly
  *d*. A day with no reference session is **unlabelled**.
- **`lag_days` default**: the parameter is `lag_days: int | None = None`, resolved per label
  set (detector → 1, ground truth → 0). An explicit `lag_days` with
  `label_set="ground_truth"` is refused (`lag_not_allowed`), even when it is 0, so a caller
  can never believe a lag was applied to hindsight labels.
- **Unlabelled days** (warm-up, ground truth not yet final, no reference session, bars past
  the catalog end) form their own `unlabelled` row. Their P&L is reported, so the totals
  reconcile. Time share is over labelled days only (spec).

## 3. Ground-truth labels (phase 1, `src/core/regimes/ground_truth.py`)

Pure functions over a DataFrame of daily `high`, `low`, `close` indexed by session date.

- **ATR(20)**: the simple mean of true range over 20 sessions (the spec says "20-day average
  true range", read as simple, not Wilder).
- **Wave legs (one pass per scale, k ∈ {3, 6, 12})**: a directional-change pass on closes.
  - Track the running extreme since the last confirmed turning point.
  - A peak is confirmed when a close falls from the running high by at least
    k × ATR20 *as of the high's date*. A trough is confirmed symmetrically.
  - Days in (trough, peak] are `up` and days in (peak, trough] are `down`. A leg includes its
    end day, because the close-to-close return into the extreme belongs to the move that made
    it.
  - Days before the first confirmed turning point, and days after the last one, are `null`.
- **Ranging**: efficiency ratio over the 21 sessions centred on *d* (closes *d−10* to *d+10*):
  `|C(d+10) − C(d−10)| / Σ|ΔC|` < 0.3. `null` within 10 sessions of either end of the data.
- **Volatility**: annualised standard deviation of close-to-close returns over the same
  centred 21 sessions. It is `high` when above the median. The median is taken over the
  version's **frozen sample window**: the bars available when the version was created. Its
  value and window are stored in the version's `frozen_stats`, not in the hashed
  `definition` (section 1), so the default definition stays a constant and a build over new
  bars appends to the same version. Without the freeze, every
  extension of the data would move the median and silently rewrite old labels, breaking the
  "rebuild reproduces" check and the append-only rule. Re-cutting the median on longer history
  is a new version. (Section 13, Q9: Allay may prefer a re-cut on every extension, each as a
  new version.)
- **Finality**: every one of these labels is final once set. A confirmed turning point never
  moves, because confirmation uses only data up to the confirming close and the ATR at the
  extreme. A centred window is final once its last day has printed. Ground-truth rows are
  therefore filled in once (`null` → value) and never changed.
- **Exit check (phase 1)**: a chart of SPY with the legs shaded per scale, written as an HTML
  file and attached to the PR, plus the leg count per scale. Allay checks it against the
  spec's question "do 3/6/12 × ATR give sensible legs?" before any strategy is profiled.

## 4. Detectors (phase 1: `sma200`; phase 3: the rest; `src/core/regimes/detectors.py`)

- `sma200` / `sma50`: `up` when close > SMA(n) of closes up to and including *t*.
- `channel100`: `up` when close ≥ the midpoint of the 100-session high-low range up to *t*.
- `swing5`: a swing high (low) is a bar whose high (low) is the extreme of the 5 bars on each
  side. It is confirmed at *t* only once those 5 later bars exist, so the label at *t* sees
  swings up to *t − 5*. The label is `up` when the latest confirmed swing high and swing low
  are both above the previous ones, `down` when both are below, and otherwise it keeps the
  previous state. (Mixed structure is not defined in the spec: section 13, Q10.)
- `volatility`: 20-session realised volatility (annualised) above its **trailing** 252-session
  median, computed with data up to *t* only.
- **Warm-up**: the first 272 sessions of reference data carry `null` for every detector, so
  the panel shares its days. For SPY, labels start in 2001.
- **No-lookahead test (spec validation 2)**: for random *t*, perturbing every bar after *t*
  leaves every detector label at ≤ *t* unchanged. This is a unit test in the pure module.

## 5. Regime profile (phase 2 daily view; phase 4 trade view; `src/core/regimes/attribution.py`)

### 5.1 Inputs

- **Equity**: `run_equity_curves` points, refused unless `metrics_basis == "mark_to_market"`
  (as `usable_curve` does).
- **Trades**: `trades` rows (entry and exit time, `order_side`, `profit_loss`).
- **Labels**: rows of the named calendar versions (the latest by default; a report records the
  versions used).

### 5.2 Daily P&L and returns

- `pnl(d) = E(d) − E(d−1)`, with `E(−1) = initial_capital`. The first day is measured from the
  starting capital, unlike `regimes.daily_returns`.
- `r(d) = pnl(d) / E(d−1)`.
- With these definitions, Σ bucket P&L (including the `unlabelled` row) equals
  `E(last) − initial_capital` **exactly**. That is the phase 2 exit check (spec validation 1).
- The report also shows `final_balance − initial_capital`. If a position is open at the end,
  the difference is that position's unrealised P&L, and the report says so.

### 5.3 Daily-view metrics (phase 2)

Per bucket:

- days and time share
- P&L and P&L share
- annualised return (mean × 252) and annualised volatility (sd × √252)
- Sharpe
- worst day
- **90% interval** on the mean daily return
- exposure
- benchmark annualised return
- trades entered and the insufficient flag

The bucket families:

- **Ground truth**: each scale's leg, ranging, and volatility, each on its own. Then the scale
  with the widest gap between up-leg and down-leg annualised return is crossed with
  volatility, and the chosen scale is recorded.
- **Detectors**: `sma200` alone in phase 2. From phase 3, each trend detector × `volatility`
  gives 4 buckets.

The 90% interval is in phase 2, not phase 4 as the spec's phase list has it, because the stop
rule's test ("strong dependence": one bucket's interval entirely below zero and another's
entirely above) cannot be evaluated without it. It uses a **stationary bootstrap** (Politis and
Romano, 1994; decided 2026-11-26, section 13) of the run's **whole daily timeline**: 1,000
resamples, each a sequence of blocks whose start day is drawn uniformly from all *N* days and
whose length is geometric with mean 10 days (*p* = 0.1), wrapping circularly from the last day
to the first, until *N* days are drawn. Each drawn day carries its own bucket label. Because
blocks are cut from the timeline before the bucket filter, a block never joins two spells of a
bucket that are apart in time except through the wrap, and the circular wrap gives every day
the same inclusion probability, so the first and last days of the run are not underweighted
(the boundary effect of a non-wrapping moving block bootstrap). Each resample's statistic is
the mean return of the drawn observations in the bucket, so the wrap's adjacency of the last and
first days never enters it. An explicit `seed` (default 0) makes a report reproduce. A resample
with no observation in the bucket is redrawn, and the redraw count is reported. A run shorter
than 10 days gets no interval and is insufficient anyway.

### 5.4 Insufficient flag

A bucket with fewer than `min_days` (60) days **or** fewer than `min_trades` (20) trades entered
is `insufficient: true`. It is reported, but no verdict rule may cite it.

The phase 2 stop-rule reading has **three** outcomes per ground-truth label family, not two:

- `strong_dependence`: one sufficient bucket's interval is entirely below zero and another's
  is entirely above.
- `no_dependence`: at least two buckets in the family are sufficient and neither pattern
  holds.
- `insufficient_evidence`: fewer than two buckets in the family are sufficient.

Only `no_dependence` at every family and for both baselines supports "stop after phase 2".
`insufficient_evidence` is reported as such, with the counts that fell short. A low-frequency
strategy (for example `c4c9c098`, 117 trades in 11 years) can then never read as "regime does
not matter" just because it trades rarely. Allay decides what to do with it: more history,
more instruments, or a judgement on the daily-only numbers (section 13, Q12).

### 5.5 Exposure

A day *d* counts as exposed when a position was **carried into it**: some trade has
`entry_timestamp` before the session close of the previous run day, and an `exit_timestamp`
that is NULL or after that close. That matches `pnl(d)`, which is measured from the previous
close. A position opened at *d*'s own bar earned none of *d*'s return, so it does not count,
and it does not inflate exposure × benchmark in the drift rule. Positions opened and closed
inside *d* (intraday strategies) count *d* as exposed through a second test: entry and exit
both fall within *d*.

### 5.6 Benchmark return

The benchmark return is the reference's close-to-close return on the bucket's days. It uses
the same price series the strategy traded. If task 1.1 finds the ETF bars unadjusted for
dividends, both sides exclude dividends, so the drift comparison stays like for like. The
report states which.

### 5.7 Trade view and alignment (phase 4)

- **Trade view**: closed trades bucketed by the label in force on the entry day, using the
  same lag rule as section 2. A trade entered on a day with no label (warm-up, ground truth
  not yet final, past the catalog end, no reference session) goes in an `unlabelled` row, as
  in the daily view. Metrics: trades, win rate, average win and loss, expectancy and profit
  factor. Open trades are counted separately.
- **Reuse**: point statistics (`sharpe`, `profit_factor`, `win_rate`) and the seeded i.i.d.
  interval helpers come from `src/core/robustness/bootstrap.py`, so a bucket's numbers use the
  same definitions as the run's stored metrics and `run_monte_carlo`. Only the stationary
  bootstrap (5.3) is new code.
- **Exit check**: Σ bucket closed trades, `unlabelled` included, =
  `performance_metrics.total_trades`, and closed plus open = trade rows.
- **Alignment**: `BUY` entered under `up`, or `SELL` under `down`, is **with-trend**. `BUY`
  under `down`, or `SELL` under `up`, is counter-trend. A trade entered with no trend label
  in force (warm-up, `null`, past the data end) is **unclassified**, reported in its own row,
  and never counted towards either group or the direction-gate rule. It is computed per
  trend definition.
- **Intervals**: the trade-view expectancy interval is an i.i.d. bootstrap over trades, used
  by the "intervals do not overlap" direction-gate rule.

### 5.8 Verdicts (phase 4)

The fixed tables in the spec's "Reading the results" section are encoded as rule codes. Each
code names the bucket and the numbers that triggered it:

- single run: `no_regime_harm`, `reliable_loss_bucket`, `fragile_concentration` (> 70% of
  profit from < 30% of time), `differences_are_noise`
- panel and alignment: `holds_across_definitions` (≥ 3 of 4), `definition_dependent`,
  `direction_gate_candidate`, `drift_not_edge`
- ground truth vs detectors: `regime_irrelevant`, `regime_detectable`, `detected_too_late`
- multiple runs (phase 5): `uncovered_regime`

The thresholds (70%, 30%, 3 of 4) are constants in `src/core/regimes/verdicts.py`, pinned by
unit tests. They are not tool parameters, because they exist to stop the reading from shifting
after the numbers are in.

## 6. Multiple runs (phase 5)

`run_regime_attribution` with 2 or more `run_ids`:

- inner-joins the runs' daily returns on shared days (as `correlation.align` does)
- reports the Pearson matrix overall and **per bucket**
- builds an **equal-risk combination**: constant weights ∝ 1 / sd of each run's daily returns
  over the shared window, daily rebalanced
- profiles the combination as one more column

The weights use the full window, which is hindsight. That is acceptable for a diagnostic and
is stated in the report. Correlation inside a bucket with fewer than `min_days` shared days is
reported as `null` with the insufficient flag. If any run's daily returns have zero standard
deviation over the shared window (it never traded, or its equity never moved), its inverse
volatility is undefined. The combination is then reported as `null` with `insufficient: true`
and `reason: "zero_volatility"`, naming those runs. The correlation matrix still reports the
other pairs, with `null` for that run's, as `pf.correlation` already does.

## 7. Filter test (phase 6, `run_regime_filter_test`)

Inputs are a run, a detector bucket and a mode (`half` by default, or `off`). **Ground-truth
labels are refused.**

1. Split the run's labelled days in half by date. Confirm the bucket in the first half (its
   interval entirely below zero), or refuse with `bucket_not_confirmed`.
2. In the second half, scale the bucket days' returns by 0.5 (or 0). Record Sharpe and max
   drawdown.
3. Baseline: 1,000 versions of the second half, each scaling the same number of days, chosen
   at random in contiguous runs. The run length is the median spell length of that bucket in
   the **first** half, so nothing from the second half shapes the baseline. A fractional
   median (an even number of spells) is rounded half up to an integer, with a minimum of 1:
   spells of 2 and 3 days give a median of 2.5, so runs are 3 days long. The total
   number of days removed is always exactly the filter's day count. Every run is full length
   except possibly the last, which is shortened to make the total exact (for example, a
   2-day median spell and 3 flagged days give one run of 2 and one of 1). Runs never
   overlap. The draws are seeded.
4. Pass only when the filtered Sharpe **and** max drawdown both beat the 95th percentile of
   the random versions.
5. A pass is stored as `candidate`. The tool reports `rule` only once a pass on a second
   instrument or period is stored for the same bucket definition.

Limits, stated in every report: scaling stored daily returns ignores the extra commission of
resizing, intraday path, and how the strategy's entries interact with the gate. A filter that
passes here still needs an engine run with the gate built into the strategy, which counts as
trials (spec step 6) and is outside this change.

## 8. MCP tools (`src/mcp_server/tools/regimes.py`, `TOOL_NAMES`)

How the names were chosen: tools are `verb_noun` snake_case (`get_*` reads; `run_*` computes
and stores a result, like `run_sensitivity` and `run_monte_carlo`; `submit_*` queues an engine
job). The benchmark parameter is `benchmark_symbol` plus `catalog`, as in
`get_regime_breakdown` and `get_sleeve_correlation`. Lists of runs are `run_ids`.

| Spec name | This change | Phase | Why |
|---|---|---|---|
| `regime_labels` (build + read) | `build_regime_calendar` | 1 | Writes a version. Split from the read so a read can never write. `build` is a new verb: the calendar is not a study, a job or an analysis |
| `regime_labels` (read) | `get_regime_labels` | 1 | `get_*` read convention. Ground-truth output carries the hindsight stamp |
| detector scorecard (no name) | `get_regime_scorecard` | 3 | Computed and stored by `build_regime_calendar`, read here. `get_scorecard` is already the gate scorecard |
| `regime_attribution` | `run_regime_attribution` | 2 (4, 5) | `run_*` computes and stores (a `regime_reports` row) |
| filter test (no name) | `run_regime_filter_test` | 6 | same |
| `reference` | `benchmark_symbol` (default `SPY`) + `catalog` (default `firstrate-etf`) | 1 | existing parameter names |
| `trend_window`, `vol_window`, `vol_lookback` | **not attribution parameters**. They live in the calendar `definition` (`build_regime_calendar(definition_overrides=...)`) | 1/3 | The spec says every tool reads labels from the calendar and computes none of its own. A different window is a different calendar version, declared as a robustness check |
| `label_set` | `label_set`: `"detector"` (default) or `"ground_truth"` | 2 | kept |
| `lag_days`, `min_days`, `min_trades`, `bootstrap_samples` | kept. `lag_days: int \| None = None` (detector → 1; refused if given with ground truth, section 2). Others default 60, 20, 1000 | 2 | kept. `seed` (default 0) added for reproducible intervals, as `run_monte_carlo` has |
| (new) `calendar_version` | optional int; latest by default | 2 | a report can pin an older version |
| (vault file) | `export_results(slug, regime_report=<report_id>)` writes `<slug>.regime.json` and `<slug>.regime.md` | 2 | additive option on the existing vault writer, so `NTRADER_MCP_EXPORT_FOLDERS` still governs writes |

**Existing tools stay as they are.** `get_regime_breakdown` and `get_sleeve_correlation` keep
their inputs and outputs, because callers and e2e tests depend on them
(`tests/e2e/mcp_server/test_stdio_study_flow.py`, `test_stdio_portfolio_flow.py`). Phase 2
adds one sentence to each `definitions` block: their volatility terciles are cut over the
run's (or the shared) days, so they are a partial hindsight view. Migrating them onto the
calendar is a later decision, once the new tools have been used.

**Report storage (phase 2 migration)**: `regime_reports`:

| Column | Notes |
|---|---|
| `id`, `report_id` (UUID) | |
| `kind` | CHECK in (`profile`, `scorecard`, `filter_test`) |
| `run_ids` | JSONB list |
| `calendar_version_ids` | JSONB list of `{version_id, build_no}`. Each id is checked to exist on write, because a JSON list can't carry a foreign key. `build_no` is the snapshot read (section 1, "Report snapshots") |
| `labels_hash` | sha256 of the label rows read, to verify a rerun against the snapshot |
| `label_set` | |
| `hindsight` | bool. True whenever a ground-truth version was read |
| `params` | JSONB, including every non-default value as `overrides` (spec: "a run that overrides them records the override") |
| `result` | JSONB |
| `git_commit`, `created_at` | |

The table is append-only (same trigger pattern). A new reading is a new row, as
`research_analyses` already does (`research.py:160-166`).

Each tool's MCP docstring states the output contract. `server.INSTRUCTIONS` gains one sentence
pointing at the regime tools.

## 9. Hard rules and how each is enforced

| Rule (spec) | Enforcement |
|---|---|
| Ground-truth labels are **never** exported to a strategy, config, sweep or filter | (1) Governance unit test `tests/unit/governance/test_regime_ground_truth_boundary.py`: an AST scan of `src/**` (and the strategies submodule when it is checked out) that resolves relative imports (`from . import ground_truth`) as well as absolute ones. The permitted set is an explicit list of **named files**, never a glob: `src/core/regimes/attribution.py`, `src/core/regimes/scorecard.py`, `src/mcp_server/analysis/regime_calendar.py` (build and read) and `src/mcp_server/analysis/regime_reports.py`. Only these may import `src.core.regimes.ground_truth` or call the repository's ground-truth reader. `src/core/regimes/filter_test.py` and any filter-test glue are named as **forbidden**, and the filter-test entry point lives in its own module (`src/mcp_server/analysis/regime_filter.py`) so the test can tell it apart. The test pins the set against the code. (2) `run_regime_filter_test` refuses `label_set="ground_truth"`, and its glue reads detector versions only. (3) `submit_backtest`, `submit_sweep` and `validate_config` are unchanged and gain no regime parameter. (4) `export_bars` does not export labels |
| Every report built from ground-truth labels is stamped "hindsight, not tradeable" | `hindsight: true` and `stamp: "hindsight, not tradeable"` at the top level of every tool payload, stored report and vault file that read a ground-truth version. `get_regime_labels(label_set="ground_truth")` carries the stamp too. A component test asserts it for each tool |
| Detector labels are append-only | repository (no update or delete) + DB trigger (section 1) + integration test against Postgres (`tests/integration/db/`) |
| A definition change creates a new version | uniqueness on (`reference`, `catalog`, `label_set`, `definition_hash`, `input_revision`). `build_regime_calendar` with a changed definition creates version n+1 and leaves version n untouched (integration test) |
| Every report records the calendar version | `regime_reports.calendar_version_ids` (version and `build_no`) NOT NULL and non-empty, echoed in every payload, plus `labels_hash`. A rerun as of the recorded builds reproduces the report |
| Thresholds fixed before results are seen | definition defaults are constants. A non-default is recorded as an override and labelled "declared robustness check" in the report |
| All four detectors count as trials | the panel table always reports all four. The tool has no "pick the best" option. When the profile is filed against a study, the report notes the four definitions for the study record (how they enter the trial ledger is section 13, Q11) |

## 10. Testing and validation (spec "Validation and pitfalls")

| Spec check | Test | Tier | Phase |
|---|---|---|---|
| Reconciliation: bucket P&L sums to total; bucket days sum to labelled days | `tests/unit/core/regimes/test_attribution.py` (synthetic curve) + `tests/integration/mcp_server/test_regime_attribution.py` (stored run) | unit, integration | 2 |
| No lookahead | `tests/unit/core/regimes/test_detectors.py` (perturb after *t*) | unit | 1 (sma200), 3 (rest) |
| Lag matters | lag 0 ≠ lag 1 on a synthetic series, and on the baseline run | unit, integration | 2 |
| Known case: SPY buy and hold has higher volatility in the High buckets | a buy-and-hold SPY run profiled under the phase 3 detectors | integration | 3 |
| Rebuild reproduces stored labels | rebuild the version, compare every row | integration | 1 |
| Trade counts sum to the run's trade count | `test_attribution.py` + the integration test | unit, integration | 4 |

All test runs follow the box's limits: serial or `-n 2`, `pgrep -af pytest` first, no pytest
while the dashboard runs, integration tier `--forked`.

## 11. Module layout and size caps

- `src/core/regimes/`, pure with no DB: `ground_truth.py`, `detectors.py`, `attribution.py`,
  `bootstrap.py`, `scorecard.py`, `verdicts.py`, `filter_test.py`. Covered by `make
  test-coverage` (src/core).
- `src/db/models/regime.py`, `src/db/repositories/regime_repository_sync.py`, two migrations.
- `src/mcp_server/analysis/regime_calendar.py` (build and read glue: catalog bars → core →
  repository), `regime_reports.py` (run inputs → core → stored report) and `regime_filter.py`
  (phase 6; detector versions only, kept separate for the boundary test in section 9).
- `src/mcp_server/tools/regimes.py` (registration).
- Each new file stays under 500 statement lines, and functions under 50. No baseline entry is
  expected.

## 12. Decisions made in this proposal (overrule before the phase that uses them)

| # | Decision | Phase |
|---|---|---|
| D1 | Calendar in Postgres (section 0(f)) | 1 |
| D2 | One version per (reference, label set), with JSONB labels | 1 |
| D3 | Ground-truth volatility median frozen per version | 1 |
| D4 | ATR simple mean. Wave threshold uses the ATR at the extreme's date. A leg includes its end day | 1 |
| D5 | Daily-view 90% interval moved into phase 2, because the stop rule needs it | 2 |
| D6 | Window parameters live in the calendar definition, not in `run_regime_attribution` | 1-2 |
| D7 | Existing tools unchanged apart from a hindsight note in their `definitions` | 2 |
| D8 | Runs must be `mark_to_market`. Legacy realised curves are refused | 2 |
| D9 | A fresh SPY trend run is a prerequisite, because none exists (confirmed, Q6) | 2 |
| D14 | Daily-view interval is a stationary bootstrap with mean block 10 (decided 2026-11-26) | 2 |
| D10 | Phase 6 scales stored returns and does not re-run the engine | 6 |
| D11 | Detector scoring uses the lag-1 label, the one a strategy could act on | 3 |
| D12 | Annualisation is ×252 throughout, the reference's sessions, whatever the traded asset | 2 |
| D13 | No async repository (approved 2026-11-26, Q13) and no web UI in v1 | all |

## 13. Open questions for Allay

Allay answered these on 2026-11-26. Each entry keeps the question and records the decision.

1. **Reference map. RESOLVED (2026-11-26): OK.** The spec's map (US stocks and broad ETFs →
   SPY, tech → QQQ, small caps → IWM, bonds → TLT, gold → GLD) stands. v1 builds SPY only; all
   five ETFs have daily bars from 2000 in `firstrate-etf`. ES and NQ get their own calendars
   (Q3).
2. **VIX vs realised volatility. RESOLVED (2026-11-26): realised volatility, no VIX.** No VIX
   import is planned.
3. **NQ/ES reference. RESOLVED (2026-11-26): their own calendars.** ES and NQ each get a
   calendar built with the same rules on the back-adjusted continuous futures daily series in
   `firstrate-futures`, built right after the SPY phase-1 build (tasks.md section 1b). Which
   session close the futures daily bar uses (RTH close or the Globex settle) is checked and
   recorded there first.
4. **Promotion gates. RESOLVED (2026-11-26): out of repo scope.** A G1 gate note lives in
   Allay's vault, not in NTrader. This change does not touch the gates.
5. **Idea template. RESOLVED (2026-11-26): out of repo scope.** The "Regime expectation" line
   is a vault edit, not NTrader code.
6. **Baselines. RESOLVED (2026-11-26):** a new core `sma_crossover` run on SPY daily,
   2015-01-02 → 2025-12-31 (task 2.1), plus the existing Bollinger Reversal run `c4c9c098`.
   **Riptide is not a baseline.**
7. **ORB runs.** Still open: none exist in the VPS DB. Where are the SPY ORB runs the spec
   mentions, and should their alignment run come first in phase 4? (Needed only by phase 4.)
8. **Wave thresholds.** Still open by design: Allay confirms 3 / 6 / 12 × ATR20 after seeing the
   phase 1 leg chart, before phase 2 profiles any strategy (the spec fixes thresholds before
   results are seen).
9. **Ground-truth volatility median. RESOLVED (2026-11-26): frozen per version (D3).** A re-cut
   on longer history is an explicit new `input_revision` with reason `median_recut`.
10. **Swing detector with mixed structure. RESOLVED (2026-11-26): keep the previous state.** A
    higher high with a lower low, or the reverse, leaves the label unchanged.
11. **Trials. RESOLVED (2026-11-26): the four detectors count as trials.** The panel always
    reports all four, and a profile filed against a study records the four definitions as
    trials. (The exact ledger mechanism is settled in phase 3, where the panel arrives.)
12. **Stop-rule reading. RESOLVED (2026-11-26):** `insufficient_evidence` at the stop rule
    means **extend the history or add an instrument before stopping**. It never reads as "no
    dependence". Phases 3-6 still start only on Allay's explicit go-ahead.
13. **Sync-only repository. RESOLVED (2026-11-26): approved.** The regime tables get a sync
    repository only, recorded as an exception in `docs/agent/persistence.md` (D13). No async
    repository is added.
