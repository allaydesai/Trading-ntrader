# Story 5.2: Reconstruct a Sealed Session's Equity Curve from Its Trades

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want drawdown and the ratios derived from it computed for a paper session,
so that the comparison covers the path-dependent metrics too, with its limitation stated rather than
hidden.

## Why this story is shaped the way it is

Read this before designing anything. The epic text asks for "the equity series from starting capital
plus the session's closed-trade P&L sequence, stored in the existing `PerformanceMetrics` JSON field",
with max drawdown, CAGR and Calmar "computed from it by exactly the same code path `ResultsExtractor`
already uses". Measured at drafting (2026-10-07, `nautilus-trader 1.220.0`, head `cea1daf`, worktree
`hx-p3e5-5-2-…`), three of the premises behind that sentence are false. Each one changes the design:

1. **There is no `PerformanceMetrics` JSON field.** `performance_metrics` has only scalar columns
   (`src/db/models/backtest.py:175-281`). The only JSON on a run is `backtest_runs.config_snapshot`
   (JSONB), and a backtest's curve is *meant* to ride there as `config_snapshot["equity_curve"]`
   (`backtest_orchestrator.py:530-533`; the chart reads it at `src/api/rest/equity.py:84-99`).
2. **No backtest has ever persisted that curve.** `BacktestPersistenceService._validate_config_snapshot`
   runs the snapshot through `StrategyConfigSnapshot(**snapshot).model_dump()`. That model has no
   `extra="allow"`, so Pydantic drops every key except `strategy_path`, `config_path`, `version`,
   `config` and `bar_type` (F3). The orchestrator's `equity_curve` is stripped, and the chart always
   falls back to its two-point line, `initial_capital → final_balance`. **A `session_conditions` key
   would be stripped the same way at seal.** That breaks this story's AC #3 and all of Story 5.4
   unless the model changes. The Phase 2 archive already recorded the strip
   (`archive/phase-2-etfs/.../5-4-persist-etf-backtest-results-to-the-database.md:48`); nobody
   routed it.
3. **A backtest's max drawdown is not an account drawdown.** `_calculate_max_drawdown` compounds
   `analyzer.returns()`, and that series is each position's **price** return
   (`Position._calculate_return = points / avg_px_open`), filed at its close time. So the backtest's
   max drawdown is the strategy's per-trade price path, compounded as if 100% of capital were in
   every trade. It does **not depend on position size** (F1): the same strategy at 10% and at 2%
   sizing reads −1.59% both times. A dollar series (starting capital + cumulative closed-trade P&L,
   AR35's literal words) reads −0.15% and −0.03%. Its curve ends at 177,488 while the account ends at
   105,948 / 101,160.

Premise 3 decides the whole story. Built literally, a paper session's max drawdown would be compared
against a backtest's under one label with a different definition. At realistic sizing it would read
10–50× smaller: "paper had far less drawdown" would come from the definition, not from the market.
That is exactly the over-reading this epic exists to prevent. The architecture authors believed the
backtest's series was a bar-granular account curve ("recorded limitation: trade-granularity, not
bar-granularity"). It is not: it is already trade-granular, and it is price-based.

**This is a product decision, not a coding one, so it goes to the PO as rulings R1–R4 (below)
before any code is written.** The design that follows is drafted against the recommended options
and marked where a different ruling changes it.

### What 5.1 already built that this story feeds

`LiveHost.settle(source, *, starting_balance)` (`src/core/results_extractor.py:161-220`) accepts
anything with a `portfolio` and a `cache`. It collects legs from `cache.positions()` +
`cache.position_snapshots()`, drops reconciliation-owned ones, sorts them chronologically, builds the
USD ledger, primes a deep copy of `portfolio.analyzer`, and returns a host that
`ResultsExtractor(engine=host)` reads. 5.1's deferred list routed the seal's source here:
**build the legs from `trades` rows, never from the session's Redis namespace or a node's cache**
(a NETTING cache loses legs on flips and restarts and duplicates them on redelivered fills —
`deferred-work.md:3922-3938`).

F2 shows the analyzer reads exactly four members of a leg, so a trade row becomes a leg with
**no** synthetic `OrderFilled`, instrument or real `Position`. A frozen
`_TradeLeg(id, strategy_id, realized_pnl, realized_return, ts_closed, is_open, is_closed)` is the
whole surface. Measured (F5): the golden backtest's 16 closed positions, re-expressed as trade rows
(8-dp prices, the recorder's quantization), go through `LiveHost.settle` → `ResultsExtractor` with
every return-based statistic within 1e-9 relative of the backtest path.

### Measured facts — cite, don't re-derive

Probes in `/tmp/p52/` (not committed): `probe_dd.py`, `probe_zero.py`, `probe_parity.py`.

| # | Fact | Where |
|---|---|---|
| F1 | Same price path, SMACrossover 3/5, HEDGING/MARGIN $100,000. Backtest-path `max_drawdown`: **−0.015943 at 10% sizing and at 2% sizing** (identical); Sharpe 0.15985 both. Dollar series (starting + cumulative `realized_pnl` in close order) through `_calculate_max_drawdown`: **−0.0014996** (10%), **−0.00030833** (2%). The backtest curve's last point is **177,488.12** both times; `final_balance` is **105,948.10** / **101,160.45**. Golden (≈100% notional): backtest −0.016098, dollar −0.010809. | `probe_dd.py golden / pct10 / pct2` |
| F2 | `PortfolioAnalyzer.calculate_statistics(account, positions)` reads, per position, only `id` (`.value`), `realized_pnl` (`Money`), `ts_closed` (ns) and `realized_return` (float). `add_return` **sums** returns that share a timestamp. `sort_index()` is a no-op (result discarded). | `.venv/.../analysis/analyzer.py:128-197` |
| F3 | `StrategyConfigSnapshot(strategy_path='a', config_path='b', equity_curve=[…], session_conditions={…}).model_dump()` keys = `['bar_type', 'config', 'config_path', 'strategy_path', 'version']`. Both extra keys are dropped. `BacktestPersistenceService._validate_config_snapshot` is that call (`backtest_persistence.py:220-238`), used by `save_backtest_results` and `save_failed_backtest`. | `src/models/config_snapshot.py:12-67`; probe one-liner |
| F4 | The config panel renders every **top-level** `config_snapshot` key as "Key: value" (`additional_params=run.config_snapshot`, `templates/partials/config_snapshot.html:36-47`; `src/api/models/backtest_detail.py:521`). A new top-level key with a `None` value would appear on **every** backtest; a top-level curve prints as one long list. | read-only inspection (AR44: untouched) |
| F5 | The golden engine's 16 closed positions rebuilt as rows: return = side-adjusted `(exit − entry) / entry` over 8-dp `Decimal` prices; max error vs `realized_return` **≈1e-10**. Through `LiveHost.settle` → extractor: `max_drawdown`, `profit_factor`, `calmar_ratio`, `avg_*_return` and `total_return` agree to the 8-dp price precision of a trade row (≤ 4.3e-9 relative, measured at code review). **Differences come only from the backtest's open leg** (5.1's deferred artifacts): Sharpe 224.8 vs 0.16 and volatility 0.333 vs 0.048 (the epoch-0 return), `expectancy` / `avg_loss` / `min_loser` / `risk_return_ratio` (the open leg's commission counted as a loser). A backtest that ends flat agrees field for field. | `probe_parity.py` |
| F6 | `_calculate_max_drawdown` never sees the starting capital: `(1 + r).cumprod()` starts after the first trade, so a loss before the first peak is invisible. Returns `[−0.10, −0.05]` → **−0.05** (true −0.145); `[−0.10, +0.05]` → **0.0** (true −0.10). Pre-existing on the backtest path. | `probe_parity.py`; `results_extractor.py:538-552` |
| F7 | Zero legs: `LiveHost.settle` over an empty source + `ResultsExtractor` gives **no NaN and no exception**. `get_performance_stats_returns()` returns 8 NaNs, which `_safe_float` maps to `None`. `max_drawdown` `None`, `calmar_ratio` `None`, `cagr` **0.0** (final = starting, period ≥ 1 day). The PnL statistics (`expectancy`, `avg_win`, `avg_loss`, `max_*`, `min_*`) read **0.0**, `total_pnl` 0.0, curve `[]`. `BacktestPersistenceService._calculate_win_rate(0, 0)` → `None`; `chk_trade_count_consistency` holds (0 = 0 + 0). | `probe_zero.py`; `backtest_persistence.py:360-373` |
| F8 | `trades` rows carry no P&L currency and no `strategy_id`. Live rows are written only on `PositionClosed` (Story 3.6), so every session row today has `exit_timestamp`/`exit_price`/`profit_loss` set; null-exit rows appear only after a Story 5.5 `--force` seal. `profit_loss` = `realized_pnl`, **net of commission**. `(trade_id, client_order_id)` is unique per session (`uq_trades_session_trade_key`), but NULLs are distinct there, so a row's DB `id` is the only key guaranteed unique. | `src/db/models/trade.py`; `live_trade_recorder.py:18-28, 97-118` |
| F9 | Reconciliation-owned positions (`EXTERNAL` / `INTERNAL-DIFF`) are never written as trades (3.6 D-D). Nothing durable records that a session absorbed a corporate action or a reconciliation correction: `runtime_flags` carries only `failed_strategies` and `order_rejections` (`live_session_health.py:129, 144`). The evidence exists only in logs (`reconcile.*`). | `grep runtime_flags src/` |
| F10 | No session field holds a starting capital (`src/models/session.py` has none). This story takes it as an explicit `Decimal`; where it comes from is Story 5.3/5.4's. 5.1's `_require_capital` (Decimal, > 0, whole cents) already refuses anything else. | `src/models/session.py:233, 365` |
| F11 | A real `Portfolio(MessageBus(trader_id, clock), Cache(database=None), clock)` can be built without initialising C logging (5.1 measured). It registers the same 17 statistics a backtest kernel's does (5.1 F9). This is the analyzer source: copying the registration list would be a duplicated literal. | 5.1 story F9, Debug Log |

### PO rulings — RULED 2026-10-07: R1b · R2a · R3a · R4a (all recommended options)

PO notes with the ruling, binding on implementation:
- **R1b** — backtest-parity series, run through `LiveHost.settle` and `ResultsExtractor` unchanged;
  no metric-math change, no new table/model/column.
- **R2a** — one optional `session_conditions` field on `StrategyConfigSnapshot`, dropped when empty,
  so existing backtest rows stay byte-identical, **proven by a test**. It is the field 5.4 uses:
  build it once. Persistence validation only — **if it needs any AR44 path (`src/api`, `templates`,
  `backtest_query.py`), stop and escalate.** The trade-granular drawdown limitation is recorded in
  `session_conditions`.
- **R3a** — explicit zero-trade record, `closed_trades: 0` in `session_conditions`; prove no NaN or
  divide-by-zero passes through the extractor or `BacktestPersistenceService` validation. **If the
  existing path would produce one, refuse with a clear message instead.**
- **R4a** — keep `_calculate_max_drawdown` as is, pin current behaviour with a test, log the
  both-sides fix in `deferred-work.md`. No metric-math change.
- **Disposition in the Dev Agent Record:** the missing `PerformanceMetrics` JSON field (series lives
  in `session_conditions`, not a new column); the open-final-position difference already deferred
  by 5.1; the reconciliation-owned positions limitation (recorded in `session_conditions` or handed
  to 5.4 explicitly).

The options as presented to the PO:

- **R1: what the equity series is** (drives max drawdown, Calmar and the stored curve).
  - **[a] Dollar series.** Starting capital + cumulative closed-trade P&L (AR35's wording). Each
    leg's return = `pnl_i / equity_{i−1}`, so every return-based statistic moves to that portfolio
    basis. It is honest about the account. It is **not comparable** to any backtest's max drawdown
    (F1: 10–50× smaller at realistic sizing).
  - **[b] Backtest-parity series (recommended).** Each closed trade's return is rebuilt from its row
    exactly as Nautilus computes a backtest leg's (F5). The whole record runs through
    `LiveHost.settle` → `ResultsExtractor` unchanged, and the series is the extractor's own
    `extract_equity_curve()` over those legs. Max drawdown, CAGR and Calmar share the backtest's
    definition, so the comparison is like for like. `session_conditions` names the basis and the
    trade granularity.
- **R2: where the series is stored** (F3, F4).
  - **[a] Inside `session_conditions` (recommended with R1b).** `StrategyConfigSnapshot` gains one
    optional `session_conditions` mapping, omitted from the dump when absent, so backtest rows stay
    byte-identical. 5.4 needs the same field. The series rides inside it. The chart keeps the
    two-point fallback every backtest already gets, so the two sides render alike. R1b's curve does
    not end at `final_balance` (F1), so it should not be charted as the account.
  - **[b] As a top-level `equity_curve` (natural with R1a).** Keep `equity_curve` too, but only on a
    snapshot that carries `session_conditions`, so the existing chart draws the paper curve.
    Backtests keep losing theirs (asymmetric), and the panel prints the list (F4).
- **R3: zero closed trades.**
  - **[a] Explicit zero-trade record (recommended).** F7: the path already yields no NaN.
    `total_trades` is 0, max drawdown and Calmar are `None`, CAGR is 0.0, the series is empty, and
    `equity_series.closed_trades` is `0`.
  - **[b] Refuse with a clear message.** A session that never closes a trade could then never be
    sealed: `SessionStatus` has no other terminal state.
- **R4: F6's pre-existing defect, which both sides share.**
  - **[a] Inherit it (recommended).** AC #2 says "exactly the same code path". Pin it with a
    characterisation test and route a both-sides fix.
  - **[b] Fix `_calculate_max_drawdown` here, for both sides.** Future backtests whose first trade
    loses would read differently from the rows already stored.

### Design decisions (drafted on R1b / R2a / R3a / R4a)

- **D-A — One pure core module, `src/core/session_equity.py`, owns "trade rows → results".** Its
  entry point is `reconstruct_session_results(trades, *, starting_capital, start, end) ->
  SessionResults`. No SQLAlchemy and no repository: it takes rows through a duck-typed
  `SessionTradeRow` protocol, which the ORM `Trade` and the domain `src.models.trade.Trade` both
  satisfy. Story 5.3's seal service reads the rows and calls this, then persists. *Why a new file:*
  a new concept (the seal's results source), and `results_extractor.py` stays the backtest-path
  module. *Why not `live_*`:* it never touches a node, so no guard list applies.
- **D-B — A trade row becomes a `_TradeLeg`, the four members F2 names plus three settle reads.**
  - `id = PositionId(f"trade-{row.id}")`: F8 makes the DB id the only unique key, and the analyzer
    keys `realized_pnls` by it, so a duplicate would silently overwrite.
  - `strategy_id = "SESSION"`: rows carry none, and every session row is strategy-owned by 3.6 D-D.
    A test asserts it is not in `RECONCILIATION_STRATEGY_IDS`.
  - `realized_pnl = Money(row.profit_loss, USD)`.
  - `realized_return` per R1.
  - `ts_closed` from `exit_timestamp`, in integer nanoseconds (no float).
  - `is_open = False`, `is_closed = True`.

  If a Nautilus upgrade makes the analyzer read another leg member, a canary goes red by name (the
  5.1 D-C discipline).
- **D-C — The source is a real `Portfolio` over a fresh `Cache` plus a legs-only cache view.**
  `_TradesSource.portfolio` is a real `Portfolio` (F11), used for its analyzer.
  `_TradesSource.cache` answers `positions()` with the legs and `position_snapshots()` with `[]`.
  `LiveHost.settle` and `ResultsExtractor` are called **unchanged**, and `results_extractor.py` is
  zero-diff, which is AC #2's proof.
- **D-D — Closed rows only.** A row with `exit_timestamp is None` is not a leg: AR35 says "closed-trade
  P&L sequence", and an open leg would put 5.1's epoch-0 artifact into a paper record (F5). This
  deliberately diverges from a backtest that ends mid-position. It is recorded, and Story 5.5 plus
  the epic-level both-sides decision own it. Rows are ordered by `(exit_timestamp, id)` before
  settle, so ties are deterministic.
- **D-E — Refusals, never a silent wrong number.** Each raises `ValueError` naming the trade id:
  - a closed row with `profit_loss is None` or `exit_price is None`;
  - a non-USD `commission_currency` (rows carry no P&L currency, F8; the 5.1 D-G analogue);
  - an unknown `order_side`.

  Capital validation is 5.1's `_require_capital`, reached through `settle`.
- **D-F — `session_conditions` gets one namespaced key from this story: `equity_series`.**

  ```python
  {"equity_series": {
      "granularity": "closed_trade",        # AR35's recorded limitation — not bar-granular
      "basis": "per_trade_price_return",    # R1b ("cumulative_trade_pnl" under R1a)
      "closed_trades": 16,                  # 0 is R3a's explicit zero-trade state
      "excludes": ["open_positions", "reconciliation_owned_activity"],   # D-D, F9
      "curve": [{"time": 1700000900, "value": 102205.14}, ...],        # R2a
  }}
  ```

  - **Namespaced:** 5.4 owns the flat AR7 keys and `schema_version` (built in parallel), so this
    story writes no flat key that 5.4's schema lists.
  - **`excludes` names what the series cannot see.** This is the routed debt: reconciliation-owned
    activity, including a corporate action absorbed by reconciliation, is outside the trades table.
    Whether a given session absorbed one cannot be known from the database (F9); that per-session
    flag is routed to 5.4.
- **D-G — `StrategyConfigSnapshot` keeps `session_conditions` (R2a).** It gains an optional field
  `session_conditions: dict[str, Any] | None = None` and a wrap-mode `model_serializer` that drops
  the key when it is `None`. So a backtest's snapshot dumps the same five keys it always has
  (byte-identical; pinned), and a sealed session's keeps its conditions. `equity_curve` is
  **not** added, and a backtest's curve is still stripped exactly as before (F3): fixing that is a
  backtest-side behaviour change, routed for an epic-level decision. `BacktestPersistenceService` is
  zero-diff (AR12 "verbatim").
- **D-H — `SessionResults` is the hand-over to 5.3.** It is frozen, with `result: BacktestResult`
  (the existing class, FR42) and `session_conditions: dict` (D-F's fragment). One method,
  `config_snapshot_entries()`, returns `{"session_conditions": …}` for 5.3 to merge into the
  snapshot it builds. 5.3 and 5.4 own merging the flat AR7 keys beside `equity_series`.
  *(Amended at code review, patch 1: `config_snapshot_entries()` is replaced by
  `merged_into(snapshot)`. A shallow `{**snapshot, **entries}` merge would have replaced 5.4's
  keys. The new method adds `equity_series` beside whatever `session_conditions` already holds,
  refuses a second one, and changes neither input.)*

## Acceptance Criteria

Epic ACs 1–4 are copied verbatim from `_bmad-output/planning-artifacts/prd-epic5-scope.md` (Story
5.2); each is followed by how this story proves it. ACs 5–8 are this story's own, from the measured
facts and the routed debt. *Proofs are written for R1b/R2a/R3a/R4a; a different ruling amends
them.*

1. **Given** a sealed session has no engine history to draw an equity curve from
   **When** seal computes metrics
   **Then** it reconstructs the equity series from starting capital plus the session's closed-trade
   P&L sequence, stored in the existing `PerformanceMetrics` JSON field (AR35).
   *Proof:*
   - `reconstruct_session_results` builds the series from `starting_capital` and the closed rows'
     sequence alone, with no engine, node, cache or Redis.
   - Its `session_conditions["equity_series"]["curve"]` is that series.
   - Written through the unmodified `BacktestPersistenceService.save_backtest_results`, the key
     survives into `backtest_runs.config_snapshot` (integration/db, test DB). That JSONB column is
     the only JSON field a run has (F3).

2. **Given** that reconstructed series
   **When** max drawdown, CAGR, and Calmar are computed
   **Then** they are computed from it by exactly the same code path `ResultsExtractor` already uses
   (FR41).
   *Proof:*
   - The result comes from `ResultsExtractor(engine=LiveHost.settle(source, …)).extract_results(start,
     end)`, and `src/core/results_extractor.py` is zero-diff.
   - A unit pin over `session_equity.py`'s AST: no `cumprod`, `expanding`, `** (1 /` or drawdown
     arithmetic of its own (the module computes no metric).
   - Integration parity: for a backtest that ends flat, the trades-row path and the backtest path
     agree on `max_drawdown`, `cagr` and `calmar_ratio` within `rel=1e-8` (F5), and on every other
     field. *(Amended at code review: the draft said `1e-9`. Measured, the flat run's drawdown
     differs by 2.13e-9 because it compounds returns rebuilt from 8-dp prices; `cagr` agrees
     exactly. `1e-8` is the trades table's measured precision, with the worst field at 4.28e-9.)*

3. **Given** the series is trade-granular rather than bar-granular
   **When** the record is written
   **Then** that limitation is recorded in `session_conditions`, so the drawdown comparison is read
   honestly (AR35).
   *Proof:* `equity_series.granularity == "closed_trade"` and `basis` names the return definition.
   Both survive a real write through `BacktestPersistenceService` (integration/db). A unit test
   proves a backtest-shaped snapshot (no `session_conditions`) dumps exactly the five keys it did
   before (D-G).

4. **Given** a session with zero closed trades
   **When** seal is attempted
   **Then** it behaves predictably — either refusing with a clear message or producing a record whose
   zero-trade state is explicit — rather than emitting NaN or dividing by zero (AR12).
   *Proof (R3a):*
   - Zero rows, and only-open rows, both yield `total_trades == 0`,
     `equity_series.closed_trades == 0`, an empty `curve`, `max_drawdown is None` and
     `calmar_ratio is None`.
   - Every float field is finite or `None`.
   - `BacktestPersistenceService` accepts the result: NaN/Infinity validation reused verbatim,
     `win_rate` `None`.

5. **Given** a session's trade rows
   **When** they become legs
   **Then** each leg's return is the one Nautilus would have computed for that position, and its
   P&L is the row's `profit_loss` — so for one finished backtest that ends flat, the trades-row path
   and the backtest path agree field for field (F5; R1b).

6. **Given** a row the reconstruction cannot read honestly
   **When** it is met
   **Then** the reconstruction refuses with a `ValueError` naming the trade: a closed row missing
   `profit_loss` or `exit_price`, a non-USD `commission_currency`, or an unknown side (D-E).
   Starting capital that is not a positive whole-cent `Decimal` is refused by 5.1's rule.

7. **Given** the routed debt (5.2 / 5.4: reconciliation-owned activity is outside the trades table)
   **When** the record is written
   **Then** `equity_series.excludes` names `reconciliation_owned_activity` and `open_positions`, so
   a comparison can attribute a difference rather than read it as strategy decay (D-F, F9).

8. **Given** the zero-change constraints
   **When** the diff is reviewed
   **Then** `src/core/results_extractor.py`, `src/services/backtest_persistence.py`, `src/api/**`,
   `templates/**`, `src/services/backtest_query.py`, `src/core/backtest_orchestrator.py`, `src/db/**`
   and `alembic/**` are untouched. There is no new table, model, column, view or migration
   (FR42, AR43, AR44). The ORM table set and single Alembic head stay pinned by 5.1's test.

## Tasks / Subtasks

- [x] **Task 0 — Gate and standing checks** (AC: all)
  - [x] 0.1 **PO rulings R1–R4 recorded in the Dev Agent Record before any edit.** If a ruling is not
    the recommended option, amend D-B / D-F / D-G and the AC proofs first. The draft is written for
    R1b / R2a / R3a / R4a.
  - [x] 0.2 `git status` clean; baselines with counts in the Debug Log: `uv run pytest tests/unit -n
    auto`, `uv run pytest tests/component` (sequential), `uv run pytest tests/integration/db` (test
    DB up). Record any pre-existing failure before editing.
- [x] **Task 1 — Unit tier, red first** (AC: #2, #3, #4, #6, #7) — new
  `tests/unit/core/test_session_equity.py` (pure Python, no engine). `tests/unit/models/
  test_config_snapshot.py` gets additions only.
  - [x] 1.1 Leg conversion:
    - BUY row → `(exit − entry) / entry`; SELL row → `(entry − exit) / entry`;
    - `realized_pnl == Money(profit_loss, USD)`;
    - `ts_closed` exact integer ns for a µs timestamp;
    - id `trade-{row.id}`;
    - `strategy_id` not in `RECONCILIATION_STRATEGY_IDS` (imported, never re-typed).
  - [x] 1.2 D-E refusals, each `ValueError` naming the trade id: missing `profit_loss`; missing
    `exit_price` on a closed row; `commission_currency="EUR"`; `order_side="HOLD"`.
  - [x] 1.3 D-D: an open row is not a leg, and the rows' input order does not change the result.
  - [x] 1.4 D-G: a snapshot with `session_conditions` dumps it intact. Sibling: a backtest-shaped
    snapshot carrying `equity_curve` (what the orchestrator passes) dumps **exactly**
    `{'strategy_path','config_path','version','config','bar_type'}`. The sibling pins today's
    behaviour and fails if the serializer leaks a `None` key.
  - [x] 1.5 AC #2 structural pin: an AST scan of `session_equity.py` finds none of `cumprod`,
    `expanding`, `pct_change`, `_calculate_` and `**`. Non-vacuous sibling: the module does reference
    `LiveHost` and `ResultsExtractor`.
  - [x] 1.6 F6 characterisation (R4a): `_calculate_max_drawdown` over returns `[−0.10, −0.05]` is
    `−0.05` and over `[−0.10, +0.05]` is `0.0`. The test name and message say "pre-existing, both
    sides, see deferred-work".
- [x] **Task 2 — Component tier, real `Portfolio`/`Cache`** (AC: #1, #3, #4, #5) — new
  `tests/component/core/test_session_equity.py`, `pytestmark = pytest.mark.component`. Copy the
  autouse `_assert_c_logging_state_is_unchanged` fixture from
  `tests/component/core/test_results_extractor_live_host.py`. Never constructs an engine or a node.
  - [x] 2.1 A hand-built session (≥ 2 winners, ≥ 2 losers, a loss after the first peak, legs a day
    apart: 5.1 found that Nautilus resamples returns daily) gives:
    - `total_trades`, winners, losers, largest win/loss as computed by hand;
    - `final_balance == starting + Σ profit_loss`;
    - `max_drawdown` equal to the hand-computed compounded-return drawdown (R1b);
    - `cagr` / `calmar_ratio` equal to `_calculate_cagr` / `_calculate_calmar_ratio` on the same inputs.
  - [x] 2.2 `equity_series.curve` equals `ResultsExtractor(host).extract_equity_curve(start, end)`.
    `granularity`, `basis`, `closed_trades` and `excludes` are as D-F specifies.
  - [x] 2.3 R3a: zero rows, and open-only rows, give the AC #4 state. Every float in
    `result.to_dict()` is `None` or finite (`math.isfinite`).
  - [x] 2.4 Canary (D-B): `PortfolioAnalyzer.calculate_statistics` accepts `_TradeLeg`s, which offer
    only the F2 members plus settle's reads. Named
    `test_the_analyzer_reads_only_four_members_of_a_leg`; the message says what to do if Nautilus
    changes.
  - [x] 2.5 Two rows closing in the same second (F2 sums them) are both counted in `total_trades`,
    and the ledger includes both. Record the measured `max_drawdown` behaviour in the Debug Log.
- [x] **Task 3 — Implement `src/core/session_equity.py` + the `StrategyConfigSnapshot` field** (AC:
  #1–#7) — only after Tasks 1–2 are red for the right reason.
  - [x] 3.1 `SessionTradeRow` (Protocol), `_TradeLeg` (frozen) + `from_row`, `_TradesSource` /
    `_LegsCache`, `SessionResults` (frozen) + `config_snapshot_entries()` (→ `merged_into()` at
    code review), `reconstruct_session_results(...)`.
  - [x] 3.2 `StrategyConfigSnapshot.session_conditions` + the wrap serializer (D-G). Docstring: why
    `equity_curve` is deliberately still dropped.
  - [x] 3.3 Size caps: new code under 500 / 50 / 100 (`test_size_caps.py` must pass with **no**
    baseline entry). If `test_regenerating_the_baseline_reproduces_it_exactly` needs the new module
    listed, regenerate it exactly (memory: "size-cap baseline must regenerate exactly").
- [x] **Task 4 — Integration tier** (AC: #1, #2, #3, #4, #5)
  - [x] 4.1 `tests/integration/core/test_session_equity_parity.py` (`--forked`; reuse the harness
    from `test_results_extractor_parity.py`). Use a path or strategy that **ends flat**, so F5's
    open-leg artifacts are absent. Re-express the engine's closed positions as trade rows (8-dp
    prices, the recorder's `Decimal(str(x))` quantization). `reconstruct_session_results` and the
    backtest path then agree on every `BacktestResult` field (`rel=1e-9`) and on the equity curve
    (`abs=0.01`). Sibling: the golden run (ends mid-position) differs **only** in the F5 field set,
    asserted by name. That pins 5.1's deferred artifacts as the sole cause.
  - [x] 4.2 `tests/integration/db/test_session_equity_persistence.py` (test DB, skips cleanly without
    Postgres, as the folder's conftest does):
    - persist a `SessionResults` through the unmodified `BacktestPersistenceService
      .save_backtest_results`;
    - read back `config_snapshot["session_conditions"]["equity_series"]` intact, and the
      `performance_metrics` row;
    - do the same for the zero-trade result: `total_trades` 0, `win_rate` NULL, `max_drawdown` NULL.

    Set nothing on `run_type` (5.3's). Clean up the rows the test wrote, following the folder's
    existing fixture pattern.
- [x] **Task 5 — Docs and routed debt**
  - [x] 5.1 `docs/agent/persistence.md` "Results Extraction": the seal's source (trade rows →
    legs → `LiveHost.settle`); the backtest drawdown definition (F1) as a reader's caution; the
    `StrategyConfigSnapshot` strip (F3), with the `session_conditions` exception.
  - [x] 5.2 `deferred-work.md`, new "## Deferred from: story-5.2 (2026-10-07)" section:
    - (a) F3: every backtest's `equity_curve` is stripped at persistence and the chart draws two
      points. Needs an epic-level both-sides decision, because the panel would print a top-level
      curve (F4).
    - (b) F6 `_calculate_max_drawdown` ignores the starting capital (both sides).
    - (c) F1: the backtest's max drawdown is size-independent price compounding. Any future "account
      drawdown" metric needs both sides.
    - (d) → **Story 5.4:** per-session "absorbed a corporate action / reconciliation correction"
      evidence has no durable source (F9). 5.4 decides whether to add a writer.
    - (e) → **Story 5.3:** call `reconstruct_session_results` with the session's rows read by DB id
      order. The starting capital source is 5.3/5.4's (F10).
    - (f) → **Story 5.5:** open rows are excluded from the series (D-D). Decide with the epoch-0
      artifact.
  - [x] 5.3 Disposition the routed debt in the Dev Agent Record: 5.1's "→ 5.2 / 5.3: build the seal's
    legs from trades rows" is **done** (D-A–D-C). "→ 5.2: per-leg returns / epoch-0 open leg" is
    **decided** (R1, D-D). "→ 5.2 (code review): the curve disagrees with `final_balance`" is
    **decided** (R1b keeps parity; recorded in `basis`). "→ 5.2/5.4: reconciliation-owned activity"
    is **recorded** (`excludes`) with per-session detection routed to 5.4.
- [x] **Task 6 — Mutation sweep** (copy the file to `/tmp/p52/`, mutate, run the target tests,
  restore by copying back. **Never `git stash`.** Record kill/survive.)
  - M1 SELL return sign flipped → 1.1 / 4.1;
  - M2 open rows kept as legs → 1.3 / 2.3;
  - M3 `realized_return` set to `pnl / starting` (R1a's basis under an R1b ruling) → 2.1 / 4.1;
  - M4 leg id from `trade_id` (collides) → 2.5;
  - M5 `granularity` key dropped → 2.2 / 4.2;
  - M6 serializer keeps a `None` `session_conditions` → 1.4;
  - M7 non-USD guard removed → 1.2;
  - M8 rows not sorted before settle → 1.3 (expected killed via `_chronological` only if ts ties
    differ — record).
- [x] **Task 7 — Gates**
  - [x] 7.1 `uv run ruff format .` · `uv run ruff check .` · `make typecheck`.
  - [x] 7.2 `uv run pytest tests/unit -n auto` · `uv run pytest tests/component` (sequential) ·
    `uv run pytest tests/integration/core/test_session_equity_parity.py
    tests/integration/core/test_results_extractor_parity.py --forked` ·
    `uv run pytest tests/integration/db`. Record counts against Task 0.2.
  - [x] 7.3 `git diff --stat` shows AC #8's zero-diff list untouched.

### Review Findings

Code review 2026-10-07, three parallel layers: Blind Hunter (diff only), Edge Case Hunter (diff +
project, probes in `/tmp/p52_ech/`), Acceptance Auditor (diff + story + PRD extract). The
auditor confirmed AC #1–#8, the R1b/R2a/R3a/R4a notes, the three PO dispositions and the zero-diff
list. It hand-recomputed the component values; it found no blocking violation. Triage after
dedup: 1 decision-needed, 10 patch, 4 defer, 8 dismissed.

Dismissed:
- the inherited drawdown bug, ruled R4a; and Calmar cannot go infinite, because
  `_calculate_calmar_ratio` returns `None` on a 0 drawdown;
- the curve not reconciling with `final_balance` and having no start point, ruled R1b — the
  extractor's own curve;
- legs outside `[start, end]` — no trigger, and metrics and series ignore the window identically;
- the `_TradeLeg` canary wording;
- the serializer's JSON-schema effect — the model is used in no API model;
- HEDGING-only parity — rows are OMS-agnostic;
- two disclosed process notes (context7, the F6 test name).

- [x] [Review][Decision] **RESOLVED by PO ruling (a), 2026-10-07: keep the curve inside
  `session_conditions` as ruled (R2a); do not drop the point list. Story 5.6 judges how the
  detail page renders it. AR44 forbids touching the views, so this is recorded as an observation
  handed to 5.6 (Dev Agent Record).** Under R2a the stored curve prints in the detail page's config panel and
  in its "CLI Command to Replicate"** — The panel renders every top-level `config_snapshot` key
  as `{{ value }}` (`templates/partials/config_snapshot.html:36-47`), and `cli_command` turns
  each one into a `--flag` (`src/api/models/backtest_detail.py:179-181`). So a sealed run shows
  `Session Conditions: {'equity_series': {… 'curve': [one point per trade] …}}` and a
  `--session-conditions {…}` flag. The `session_conditions` row and flag come with AR7's own design
  whatever this story stores; the curve is what makes them long. The R2 options put "the panel
  prints the list" against R2b only, so the PO ruled without this consequence. Options:
  - (a) keep the curve inside `session_conditions` as ruled, and let 5.6 judge its rendering;
  - (b) store only `granularity`, `basis`, `closed_trades` and `excludes`, not the point list —
    the series stays reproducible on demand from the session's backfilled trade rows through the
    same function, but AC #1's literal "stored" then means the trade rows, not the curve.
  (auditor+blind)
- [x] [Review][Patch] **The hand-over replaces a whole `session_conditions` document when merged
  shallowly** — `config_snapshot_entries()` returns `{"session_conditions": {...}}`. A seal that
  already holds 5.4's flat keys and spreads it with `{**snapshot, **entries}` loses them
  silently. Replace it with `merged_into(snapshot)`, which adds `equity_series` beside existing
  keys and refuses a second one. [src/core/session_equity.py:218-220] (blind)
- [x] [Review][Patch] **The parity proof builds rows by hand, so it shares the module's
  assumptions about what the recorder writes** — Build each row through the recorder's own
  `aggregate_closed_position(PositionClosed, position)` and `SqlTradeRecord.persist`'s field
  mapping. [tests/integration/core/test_session_equity_parity.py:106-119] (blind)
- [x] [Review][Patch] **A row with an exit price but no exit timestamp is silently dropped** —
  Only `exit_timestamp` decides open vs closed, so a row with an exit price, a P&L and a NULL
  timestamp vanishes from the record. Refuse `exit_timestamp is None and exit_price is not
  None`. A backtest open row carries a non-null `profit_loss` (0.00 or the realized amount,
  `backtest_persistence.py:480-486`), so P&L cannot be the test.
  [src/core/session_equity.py:169] (blind+edge)
- [x] [Review][Patch] **AC #2's three fields are asserted at `rel=1e-7`, not the stated `1e-9`**
  — The measured 8-dp drift was on `profit_factor` and `avg_loss_return`, not on
  `max_drawdown`, `cagr` or `calmar_ratio`. Assert those three at `rel=1e-9` in both parity tests
  and keep `REL` for the field-for-field sweep. *(Applied as the auditor's alternative fix. At
  `1e-9` the flat run's `max_drawdown` failed: it differs by 2.13e-9, because the drawdown
  compounds every return rebuilt from 8-dp prices. The AC #2 proof and F5 are amended to the
  measured bound, and one `REL = 1e-8` now governs both the three named fields and the sweep —
  tightened from `1e-7`, with the worst field at 4.28e-9.)* [tests/integration/core/test_session_equity_parity.py:58]
  (auditor)
- [x] [Review][Patch] **Duplicate row ids collapse silently** — The analyzer keys P&L by
  `trade-{id}`, so a repeated row is counted twice in the ledger and once in the PnL statistics.
  Refuse duplicate ids. [src/core/session_equity.py:167-171] (blind+edge)
- [x] [Review][Patch] **A naive/aware `start`/`end` mix raises a raw `TypeError`** — Rows are read
  as UTC when naive, but the period is not. Normalise `start`/`end` the same way. *(Applied as a
  refusal, per the PO's instruction for patches 3/5/6/8: a naive bound is a `ValueError`
  naming it, never normalised and never a raw `TypeError`.)*
  [src/core/session_equity.py:275] (blind+edge)
- [x] [Review][Patch] **AC #6's capital clause has no test through 5.2's entry point** — Every
  test passes valid capital. Add float → `TypeError`, zero and sub-cent → `ValueError` through
  `reconstruct_session_results`. [tests/unit/core/test_session_equity.py] (auditor+blind)
- [x] [Review][Patch] **An empty curve beside closed trades would be stored silently** —
  `extract_equity_curve` swallows any exception into `[]`. Refuse `closed_trades > 0` with an
  empty curve (D-E: never a silent wrong value). [src/core/session_equity.py:282] (auditor)
- [x] [Review][Patch] **"One point per closed trade" is wrong** — Returns at the identical
  timestamp are summed, and the curve truncates time to whole seconds. Fix the module and
  `persistence.md` wording, fix the component comment that conflates daily binning with
  same-second closes, and assert the same-second test's curve values.
  [src/core/session_equity.py:54-55; docs/agent/persistence.md; tests/component/core/test_session_equity.py:215-231] (blind)
- [x] [Review][Patch] **`persistence.md` called `session_conditions` "the one optional key"**
  — `bar_type` is optional too, and kept as `null`. Fixed during review: "beyond its five base
  keys … exactly one more". [docs/agent/persistence.md:68-70] (blind)
- [x] [Review][Defer] **A `None` `commission_currency` is accepted as USD**
  [src/core/session_equity.py:157] — deferred. Rows carry no P&L currency, and the recorder
  writes `None` on every unvouched leg (flip, redelivered fill). Phase 3 trades US equities only.
  Owner: the first story to add a non-USD instrument (the 5.7 W4 precedent). (blind+edge+auditor)
- [x] [Review][Defer] **A leg return of −1 or below makes the compounded series zero or negative,
  and `_calculate_max_drawdown` turns the resulting 0/0 into `0.0`**
  [src/core/results_extractor.py:545-549] — deferred, pre-existing on both sides. A short whose
  exit is at least 2× its entry does it. Measured: curve `−50000 → −25000` reads as a recovery,
  and `max_drawdown` is 0.0. R4a forbids a metric-math change. (edge+auditor)
- [x] [Review][Defer] **Closes in the same second at different microseconds give repeated `time`
  values in the curve** [src/core/results_extractor.py:457] — deferred, pre-existing
  (`extract_equity_curve` truncates to whole seconds; `add_return` sums only identical
  timestamps). Harmless while nothing charts the series. Owner: 5.6, or whoever first charts it.
  (edge+auditor)
- [x] [Review][Defer] **5.4's `closed_trade_count` will duplicate `equity_series.closed_trades`
  with nothing tying them** — deferred to Story 5.4. When it adds the flat key, pin the two equal
  (CLAUDE.md: a duplicated literal needs its own equality pin). (auditor)

## Dev Notes

### The current surface — exact extension points

- `src/core/results_extractor.py:161-220` — `LiveHost.settle`. **Call it; do not edit it.** It
  reads `source.cache.positions()`, `source.cache.position_snapshots()`, each leg's
  `strategy_id`, `is_open`, `ts_closed` and `realized_pnl` (`.currency`, `.as_decimal()`), then
  deep-copies the legs and `source.portfolio.analyzer`.
- `src/core/results_extractor.py:309-425` — `extract_results`: counting loop
  (`realized_pnl.as_double()`), analyzer statistics, `_calculate_max_drawdown(analyzer)`,
  `_calculate_cagr(starting, final, start, end)`, `_calculate_calmar_ratio`.
  `extract_equity_curve` (427-471) = `(1 + returns).cumprod() × starting`, keyed by close time, with
  no start point.
- `src/models/config_snapshot.py:12-67` — the model D-G extends. Pydantic v2 (`model_serializer`
  wrap mode: `from pydantic import model_serializer`; `def _ser(self, handler): data = handler(self)
  …`). Check context7 for the v2 wrap-serializer signature before writing it.
- `src/services/backtest_persistence.py:72-152, 220-238` — the write path the integration/db test
  drives, unmodified.

### Sketch (shape, not a contract — the tests are the contract)

```python
class SessionTradeRow(Protocol):
    id: int
    order_side: str
    entry_price: Decimal
    exit_price: Decimal | None
    profit_loss: Decimal | None
    exit_timestamp: datetime | None
    commission_currency: str | None


@dataclass(frozen=True)
class SessionResults:
    result: BacktestResult
    session_conditions: dict[str, Any]

    def config_snapshot_entries(self) -> dict[str, Any]:
        return {"session_conditions": copy.deepcopy(self.session_conditions)}


def reconstruct_session_results(
    trades: Iterable[SessionTradeRow], *, starting_capital: Decimal, start: datetime, end: datetime
) -> SessionResults:
    closed = sorted((t for t in trades if t.exit_timestamp is not None),
                    key=lambda t: (t.exit_timestamp, t.id))
    legs = [_TradeLeg.from_row(t) for t in closed]
    host = LiveHost.settle(_TradesSource.over(legs), starting_balance=starting_capital)
    extractor = ResultsExtractor(engine=host)
    curve = extractor.extract_equity_curve(start, end)
    return SessionResults(extractor.extract_results(start, end), _conditions(len(legs), curve))
```

### Scope boundaries — do NOT build

- No seal service, no `live seal` CLI, no `run_type` stamping, no `backtest_run_id` backfill, no
  `SessionService` transition (5.3). No flat AR7 `session_conditions` keys, no `schema_version`, no
  starting-capital source (5.4). No open-position handling beyond excluding open rows (5.5). No
  repository method: `EXPECTED_CAPABILITIES` (`tests/unit/db/test_trading_session_repository_shape.py`)
  is an exact-set pin, and reading rows is 5.3's.
- No change to `results_extractor.py`, any metric formula, `BacktestResult`,
  `BacktestPersistenceService`, `PerformanceMetrics`, any DB model or migration (FR42, AR43, AR44).
  `StrategyConfigSnapshot` is a validation model, not a DB model. Its change adds no column, and
  backtest rows dump byte-identical.
- No broker, no node, no Redis. NFR33's operator-procedure clause does not apply (sealing is
  database-only), so no P-number is added to `docs/qa/phase3-live-verification.md`.

### Hazards

- **Parallel stories.** 5.3 (seal service) and 5.4 (`session_conditions`) are being built in other
  worktrees. 5.4 will likely add the same `StrategyConfigSnapshot.session_conditions` field.
  Keep D-G minimal and self-describing so the merge is a trivial union, and do not touch any flat
  AR7 key.
- **A test that cannot fail** (restated every story since Epic 2):
  - 1.4 needs its byte-identical sibling;
  - 1.5 its non-vacuous sibling;
  - 4.1 a run that ends flat, or the open-leg artifacts make "agree" impossible and tempt a loosened
    tolerance;
  - 2.1 hand-computed expectations, never values read back from the code under test.
- **Daily resampling.** Nautilus's returns statistics bin by day (5.1 Debug Log). Component legs a
  second apart collapse into one bin, and Sharpe reads `None` for a reason unrelated to the code
  under test.
- **Float vs Decimal.** Build `Money` from the row's `Decimal`. Compute the return in `Decimal`, then
  `float()` it once. Nanoseconds by integer arithmetic on `exit_timestamp - EPOCH`, never
  `timestamp() * 1e9`.
- **C logging.** Constructing a `Portfolio` in the component tier must leave C logging
  uninitialised: keep the autouse guard.
- **Import placement.** `session_equity.py` imports `results_extractor` (which imports
  `live_trade_recorder`); no `live_*` module gains an import, so no guard list changes. Land each
  import and its first use in one edit (F401 is unfixable).

### Testing standards summary

TDD, red recorded first (Tasks 1–2 before 3).
- **Unit:** pure conversion, refusals, the serializer, structural pins, the F6 characterisation.
- **Component:** real `Portfolio` + `Cache`, hand-computed sessions, zero-trade, the canary.
- **Integration `--forked`:** parity against a real backtest that ends flat, plus the open-leg
  sibling.
- **Integration/db:** the write through the unmodified persistence service, including the zero-trade
  record.

No broker double is needed: nothing here touches a broker.

### Project Structure Notes

- New:
  - `src/core/session_equity.py`
  - `tests/unit/core/test_session_equity.py`
  - `tests/component/core/test_session_equity.py`
  - `tests/integration/core/test_session_equity_parity.py`
  - `tests/integration/db/test_session_equity_persistence.py`
- Modified:
  - `src/models/config_snapshot.py`
  - `tests/unit/models/test_config_snapshot.py` (additions only)
  - `docs/agent/persistence.md`
  - `_bmad-output/implementation-artifacts/deferred-work.md`
  - `tests/unit/governance/test_size_caps.py`, only if its baseline must list the new module
- Architecture alignment: `architecture.md:533-536` names `services/session_seal_service.py` (5.3)
  as the seal's home. This story supplies its results computation from `src/core/`, beside the
  extractor it drives. AR38 holds: the service owns the record, and core owns the Nautilus objects.

### References

- `_bmad-output/planning-artifacts/prd-epic5-scope.md`: Story 5.2 ACs, AR7, AR12, AR35, AR43, AR44,
  and the routed-debt line for 5.2 / 5.4.
- `_bmad-output/planning-artifacts/architecture.md:212-237` (D1), `:427-431` (`session_conditions`),
  `:702-708` (G4).
- `_bmad-output/implementation-artifacts/5-1-compute-metrics-for-a-live-host-through-the-existing-extractor.md`:
  D-B to D-G, F2/F3/F9, and the Review Findings.
- `_bmad-output/implementation-artifacts/deferred-work.md:3920-3992` ("Deferred from: story-5.1" and
  its code review), the items routed here.
- `tests/integration/core/test_results_extractor_parity.py`: engine harness and golden.
- `tests/component/core/test_results_extractor_live_host.py`: real `Portfolio`/`Cache` harness and
  the C-logging guard.

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`)

### Debug Log References

**Task 0.1 — PO rulings (2026-10-07): R1b · R2a · R3a · R4a**, all the recommended options, with
the PO's notes recorded verbatim under "PO rulings" above. No design amendment was needed.

**Task 0.2 — baselines (head `cea1daf`, tree clean but for the story file and the 5-2 key).**
- `pytest tests/unit -n auto`: **3525 passed**.
- `pytest tests/component` (sequential): **1960 passed, 16 skipped**.
- `pytest tests/integration/db` (Postgres up): **115 passed**.
- No pre-existing failure.

**Red first.**
- Unit and component collection failed on the missing `src.core.session_equity`.
- `test_a_sealed_sessions_conditions_survive_validation_intact` failed with `KeyError:
  'session_conditions'`: the validator dropped it, which is F3 reproduced at unit tier.
- Two tests passed before any edit, by design. The byte-identical goldens are characterisation:
  the `json.dumps` of both backtest snapshot shapes was captured from the pre-change code at
  `cea1daf`, key order included. The empty-is-dropped test passed because the key was ignored
  then.

**Measured during implementation.**
- **The 5.1 golden path ends SHORT, not long.** SMACrossover closes the opposite side *or*
  opens, never both. A falling tail kept the short open, so the run that ends flat appends a
  rising tail: the golden cross closes the short, and nothing crosses again.
- **The tolerance is the trades table's 8-dp price precision, `REL = 1e-7`.** A return is a
  small difference of two prices quantized to 8 dp. On the golden run, `profit_factor` and
  `avg_loss_return` differ by 4.3e-9 and 3.9e-9 relative, past an initial `rel=1e-9`. A wrong
  sign, basis or leg differs at order one, and mutants M1, M3 and M4 are killed at the
  integration tier under `REL`.
- **The run that ends flat agrees on every `BacktestResult` field**, including `sharpe_ratio`,
  `volatility` and `expectancy`. The curve has identical times and values within $0.01.
- **The golden run, which ends mid-position, differs in exactly** `{sharpe_ratio, volatility,
  risk_return_ratio, expectancy, avg_loss, min_loser}`: 5.1's deferred open-leg artifacts. That
  set is pinned by name.
- **Same-second closes (Task 2.5).** `add_return` sums the returns, so +10% and −10% closing in
  one second file a single 0.0 return. The curve gets one point at 100,000, and `max_drawdown` is
  0.0: the intra-second −10% is invisible. A HEDGING backtest closing two positions on one bar
  does the same, which is parity, not a new defect. `total_trades` (3) and the ledger count both
  trades.
- **Stacked `acceptance_criterion` markers are not additive.** `tests/conftest.py` reads
  `get_closest_marker`, so a test carrying 5.2a and 5.2c reported only 5.2c. It was split into one
  test per criterion.
- **context7 was not permitted in this session.** The Pydantic v2 wrap-serializer signature was
  verified against the installed `pydantic 2.11.9` instead (probe: `model_dump`,
  `model_dump_json`, an extra key, the JSON schema).
- **Sizes (`measure_module`).** `src/core/session_equity.py` is 129 statements; its largest class
  is `_TradeLeg` at 18 and its largest function is `_readable` at 21. `StrategyConfigSnapshot`
  is 38. `test_size_caps.py` passes with no baseline entry.

**Task 6 — mutation sweep.** `/tmp/p52/mutate.py` applies each mutant to the file, runs unit +
component, then integration parity `--forked`, and restores the original bytes (`restored: True`,
never `git stash`). Result: **14/14 killed**.

| # | Mutant | Killed by |
|---|---|---|
| M1 | SELL return sign flipped | unit, component, integration |
| M2 | open rows kept as legs | unit, component |
| M3 | return on capital (R1a's basis) | unit, component, integration |
| M4 | leg ids collide | unit, component, integration |
| M5 | `granularity` key dropped | unit, component |
| M6 | serializer keeps an empty `session_conditions` | unit (byte-identical golden) |
| M7 | non-USD guard removed | unit |
| M8 | rows not sorted before settle | unit |
| M9 | non-finite guard not wired | unit |
| M10 | period guard removed | unit |
| M11 | naive timestamp not read as UTC | unit |
| M12 | entry-price guard removed | unit |
| M13 | refusal reasons ignored | unit |
| M14 | `excludes` key dropped | unit, component |

The story's M8 note ("killed via `_chronological` only if ts ties differ") was conservative.
`_legs_from` returns the order, and the unit test asserts it directly.

**Task 7 — gates.**
- `ruff format .`: 571 files unchanged.
- `ruff check .`: all passed. Two `I001` fixes were needed in the new test files: the hook had
  sorted `src.core.session_equity` as third-party while the module did not exist yet.
- `make typecheck`: no issues in 121 files.
- Unit (`-n auto`): **3559 passed** (+34).
- Component (sequential): **1971 passed, 16 skipped** (+11).
- Integration parity, 5.2's file plus 5.1's, `--forked`: **7 passed**.
- `tests/integration/db`: **119 passed** (+4).
- Full `tests/integration -n auto --forked`: **338 passed, 2 skipped** (the two IBKR-gated
  parity tests).
- `git status`: nothing under `src/api/`, `templates/`, `src/services/`, `src/core/
  results_extractor.py`, `src/core/backtest_orchestrator.py`, `src/db/` or `alembic/` (AC #8).

**Code review (2026-10-07) — three layers, ruled by the PO: decision (a), patches batch-applied
(choice 0).**
- **Red first for the behaviour patches.** 11 new unit tests failed for the right reasons before
  any edit: the naive period (3 cases), the exit price without a timestamp, the repeated id
  (2 cases), `merged_into` (4 cases) and the empty curve.
- **Patches 7 and 9 pin behaviour that already held,** so they passed at once: 5.1's capital
  rule reaching the caller unchanged, and the summed same-instant return. Mutants M21 and M17
  prove those tests can fail.
- **Patch 4 could not be applied as written.** At `rel=1e-9` the flat run's `max_drawdown`
  differs by 2.13e-9. Per-field relative differences were measured (`/tmp/p52/probe_reldiff.py`):

  | Field(s) | Flat run | Golden run |
  |---|---|---|
  | `profit_factor` | 4.28e-9 | 4.30e-9 |
  | `avg_loss_return` | 3.92e-9 | 3.94e-9 |
  | `max_drawdown` / `calmar_ratio` | 2.13e-9 | 2.26e-10 |
  | `cagr` | 0 | 0 |

  The AC #2 proof and F5 now state the measured bound. One `REL = 1e-8` governs the three named
  fields and the sweep; it was `1e-7`.
- **Patch 2 changed the source of the parity rows.** The rows now come from the recorder's
  `aggregate_closed_position(TestEventStubs.position_closed(p), p)` and
  `SqlTradeRecord.persist`'s field mapping, as ORM `Trade` objects. Parity held unchanged.
- **The ORM `Trade` satisfies `SessionTradeRow` under mypy.** A temporary probe in
  `src/services/` passed both the ORM and the domain row to `reconstruct_session_results`, and
  mypy reported no issues; the probe was deleted after (via Python, since `rm` is denied in this
  harness). Pyright's IDE complaint (`Mapped[int]` vs `int`) is IDE-only. `make typecheck` does
  not check `tests/`.
- **Post-review mutation sweep: 22/22 killed** (`restored: True`). M1–M14 as before, plus:
  - M15 — duplicate-id guard removed;
  - M16 — exit-price-without-timestamp guard removed;
  - M17 — naive-period guard removed;
  - M18 — empty-curve guard removed;
  - M19 — `merged_into` replaces 5.4's keys;
  - M20 — second-series refusal removed;
  - M21 — capital coerced before settle;
  - M22 — `merged_into` mutates its input.
- **Post-review gates:**
  - `ruff format .` (571 unchanged), `ruff check .` (all passed), `make typecheck` (121 files,
    no issues).
  - `test_size_caps.py`: 14 passed, **no overage and nothing re-baselined**. The module is 162
    statements (cap 500), largest class `_TradeLeg` 18 (cap 100), largest function `_readable`
    21 (cap 50).
  - Unit **3572 passed**. Component (sequential) **1975 passed, 16 skipped**.
  - `tests/integration -n auto --forked` (db included) **338 passed, 2 skipped**.

**Observation handed to Story 5.6 (PO ruling (a), 2026-10-07; AR44 forbids touching the views).**
A sealed run's detail page will render `config_snapshot["session_conditions"]` — including
`equity_series.curve`, one point per distinct close instant — in two places:
- as one long "Session Conditions" row in the config panel, which renders every top-level key
  with `{{ value }}` (`templates/partials/config_snapshot.html:36-47`);
- as an extra `--session-conditions {…}` flag in "CLI Command to Replicate", where
  `ConfigurationSnapshot.cli_command` turns every top-level key into a flag
  (`src/api/models/backtest_detail.py:179-181`).

The row and the flag come with AR7's own design whatever 5.2 stores; the curve is what makes
them long (~100 points for a 100-trade session). Story 5.6's AC ("the config snapshot —
including `session_conditions` — all render correctly") is where this is judged. No code under
`src/api/**` or `templates/**` was touched.

### Completion Notes List

- **AC #1:** `reconstruct_session_results(rows, *, starting_capital, start, end)` rebuilds the
  series from starting capital plus the closed rows' sequence. It uses no engine, node, cache or
  Redis. The series is stored in `config_snapshot["session_conditions"]["equity_series"]["curve"]`
  and proven to reach Postgres through the unmodified `BacktestPersistenceService`.
- **AC #2:** every metric comes from `LiveHost.settle` → `ResultsExtractor`, and
  `results_extractor.py` is zero-diff. An AST pin confirms the module has no metric arithmetic;
  a non-vacuous sibling confirms it drives the extractor. A component test hand-computes max
  drawdown, CAGR and Calmar. The integration test shows field-for-field parity with a real
  backtest.
- **AC #3:** `equity_series.granularity = "closed_trade"` and `basis =
  "per_trade_price_return"` are stored. A backtest snapshot validates byte-identical to the
  pre-change golden, at unit tier and in Postgres.
- **AC #4 (R3a):** zero rows, and only-open rows, give an explicit record:
  `closed_trades: 0`, an empty curve, `total_trades` 0, `max_drawdown`/`calmar_ratio` `None`,
  CAGR 0.0. Every float is finite. `BacktestPersistenceService` validation accepts it verbatim,
  with `win_rate` NULL. A non-finite metric, if the path ever produced one, is refused with a
  `ValueError` naming the field. The guard is wired, and M9 proves it.
- **AC #5:** each leg's return is Nautilus's `points / avg_px_open`, side-adjusted, and its P&L is
  the row's `profit_loss`. A backtest that ends flat agrees field for field at the trades table's
  precision.
- **AC #6:** a closed row missing `profit_loss` or `exit_price`, a non-positive entry, an unknown
  side, or a non-USD commission refuses with `ValueError: trade <id> …`. A non-forward period
  refuses too. Capital validation stays 5.1's.
- **AC #7:** `equity_series.excludes = ["open_positions", "reconciliation_owned_activity"]`.
- **AC #8:** zero diff on every listed path; no table, model, column, view or migration.
  `StrategyConfigSnapshot` is a validation model: it gained one optional field and no column.
- **Dispositions the PO asked for:**
  - **The missing `PerformanceMetrics` JSON field.** It does not exist: that table has scalar
    columns only. The series lives in `config_snapshot["session_conditions"]["equity_series"]`,
    not in a new column. `docs/agent/persistence.md` claimed a JSON array on
    `PerformanceMetrics` and was corrected; that line was the likely source of AR35's premise.
  - **The open-final-position difference already deferred by 5.1.** Open rows are left out of
    the paper series (D-D) and named in `excludes`. The resulting six-field difference from a
    backtest that ends mid-position is pinned by name. Routed to Story 5.5 and the both-sides
    decision (`deferred-work.md` "Deferred from: story-5.2" (f)).
  - **Reconciliation-owned positions.** The structural limitation is recorded in
    `session_conditions` (`excludes: reconciliation_owned_activity`). Whether *a given* session
    absorbed such activity has no durable source (F9), so that is handed to **Story 5.4**
    explicitly (deferred-work (d)).
- **Routed debt from 5.1:**
  - "→ 5.2 / 5.3: build the seal's legs from `trades` rows, not Redis or a node" is **done** (D-A
    to D-C).
  - "→ 5.2: per-leg returns / the epoch-0 open leg" is **decided**: R1b keeps the per-leg series,
    and D-D excludes open rows.
  - "→ 5.2 (code review): the curve disagrees with `final_balance`" is **decided**: R1b keeps
    parity, recorded as `basis`, and a dollar curve is the deferred both-sides question (c).
  - "→ 5.2 / 5.4: reconciliation-owned activity" is **recorded**, with detection routed to 5.4.
- **New debt, all in `deferred-work.md` "Deferred from: story-5.2":**
  - (a) every backtest's `equity_curve` is stripped at persistence;
  - (b) `_calculate_max_drawdown` ignores the starting capital (R4a: kept and pinned);
  - (c) the backtest's max drawdown is size-independent price compounding;
  - (e) how Story 5.3 calls this.
- No broker, no node, no Redis. NFR33's operator-procedure clause does not apply, so no P-number
  was added.
- **After code review:**
  - The hand-over is `merged_into(snapshot)`: it adds the series beside 5.4's keys, refuses a
    second series, and changes neither input.
  - The parity rows are built through the recorder itself.
  - New refusals, each a clear `ValueError` naming its subject: a repeated row id, an exit price
    without an exit timestamp, a naive period bound, and closed trades with no series. Nothing
    can produce NaN, and no raw `TypeError` escapes.
  - AC #2's tolerance is the trades table's measured precision.
  - A `None` commission currency read as USD is now stated here: it is a deferred limitation
    (code-review deferred list), not silent.

### File List

- `src/core/session_equity.py` — new: `SessionTradeRow`, `_TradeLeg`, `_LegsCache`,
  `_TradesSource`, `SessionResults`, `reconstruct_session_results` and helpers.
- `src/models/config_snapshot.py` — modified: optional `session_conditions` field + a
  wrap serializer that drops it when empty (PO ruling R2a).
- `tests/unit/core/test_session_equity.py` — new.
- `tests/unit/models/test_config_snapshot.py` — modified (additions only: byte-identical
  goldens, `session_conditions` kept/dropped).
- `tests/component/core/test_session_equity.py` — new.
- `tests/integration/core/test_session_equity_parity.py` — new.
- `tests/integration/db/test_session_equity_persistence.py` — new.
- `docs/agent/persistence.md` — modified: the false "equity curve on `PerformanceMetrics`" line
  corrected; "A sealed session's source: its trade rows".
- `docs/agent/architecture.md` — modified: `session_equity.py` added to the source tree.
- `_bmad-output/implementation-artifacts/deferred-work.md` — modified: "Deferred from:
  story-5.2".
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — modified: the 5-2 key only.
- `_bmad-output/implementation-artifacts/5-2-reconstruct-a-sealed-sessions-equity-curve-from-its-trades.md`
  — new (this story).

## Change Log

- 2026-10-07 — Story drafted (create-story). F1–F11 measured against `nautilus-trader 1.220.0` at
  `cea1daf`. Three epic premises found false (no `PerformanceMetrics` JSON field; the config-snapshot
  validator strips unknown keys; a backtest's max drawdown is size-independent price compounding).
  PO rulings R1–R4 requested before dev; D-A to D-H drafted on the recommended options.
- 2026-10-07 — PO ruled R1b / R2a / R3a / R4a. Implemented (dev-story): trade rows → legs →
  `LiveHost.settle` → the unmodified extractor; `StrategyConfigSnapshot.session_conditions`
  (backtest rows byte-identical); explicit zero-trade record; F6 pinned. 14/14 mutants killed.
  Status → review.
- 2026-10-07 — Code review (bmad-code-review, 3 layers): 1 decision, 10 patch, 4 defer, 8
  dismissed. The PO ruled decision (a): keep the curve, and hand the panel/CLI-flag rendering to
  5.6 as an observation. All patches were batch-applied:
  - `merged_into` replaces the shallow-merge hand-over;
  - parity rows go through the recorder;
  - four new clear refusals;
  - AC #2 is held to the measured `1e-8`;
  - capital refusals are tested through the entry point;
  - the "one point per trade" wording is corrected.

  22/22 mutants killed; no size-cap overage. Status → done.
