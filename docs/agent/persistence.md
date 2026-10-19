# Database & Persistence

## Async/Sync Dual Pattern

The codebase has **both async and sync** DB implementations:

| Layer | Async (web UI) | Sync (CLI) |
|-------|---------------|------------|
| Session | `src/db/session.py` | `src/db/session_sync.py` |
| Repository | `backtest_repository.py` | `backtest_repository_sync.py` |
| Driver | `asyncpg` | `psycopg2` |

**When adding DB features, update both repositories.** Same ORM models are shared.

## Database Schema

PostgreSQL 16+ with TimescaleDB. Four Alembic migrations in `alembic/versions/`:

| Migration | Tables |
|-----------|--------|
| `9c7d5c4` | `backtest_runs`, `performance_metrics` |
| `0937d13` | Additional Nautilus Trader fields on above |
| `7d28f3a` | `market_data` |
| `34f3c8e` | `trades` (individual trade tracking) |

Run migrations: `alembic upgrade head`

## ORM Models

All models inherit `Base` + `TimestampMixin`.

**BacktestRun** (`src/db/models/backtest.py`):
- `id` (BigInteger PK) — internal identifier
- `run_id` (UUID, indexed) — external business key
- `config_snapshot` (JSONB) — complete strategy config for reproducibility
- Relationships: one-to-one `PerformanceMetrics`, one-to-many `Trade`

**Trade** (`src/db/models/trade.py`):
- Flattened denormalization of closed Nautilus trades (not open positions)
- Calculated: `profit_loss`, `profit_pct`, `holding_period_seconds`
- Constraints: `positive_quantity`, `positive_entry_price`, `positive_exit_price`
- Composite index: `(backtest_run_id, entry_timestamp)` for range queries
- P&L: `(exit_price - entry_price) * quantity - commission - fees`

## Repository Pattern

Async methods, constructor-injected `AsyncSession`.
Use `selectinload()` on relationships to avoid N+1 queries.

```python
# DI chain: get_db() → repository → service
async def get_backtest_service(session: DbSession) -> BacktestQueryService:
    repo = BacktestRepository(session)
    return BacktestQueryService(repo)
```

## Persistence Service

`src/services/backtest_persistence.py`:
- Validates metrics don't contain NaN or Infinity before storage (`_validate_metrics()`)
- Handles `DuplicateRecordError` if `run_id` already exists
- Starting balance must be passed separately (Nautilus doesn't retain it)
- **No equity curve is stored for a backtest** (corrected by Story 5.2; this line used to say it
  was a JSON array on `PerformanceMetrics`, which has no JSON column at all). The backtest paths
  put `equity_curve` into `config_snapshot`, but `_validate_config_snapshot` runs the snapshot
  through `StrategyConfigSnapshot(...).model_dump()`, which ignores unknown keys — so the curve
  is dropped, and the equity chart (`/equity/{run_id}`) draws its two-point
  `initial_capital → final_balance` fallback. **Since the research MCP (phase 1) the curve is
  stored in `run_equity_curves`** (one value per UTC day, written by the orchestrator), which the
  MCP reads; the web chart still draws its fallback. Beyond its five base keys (`strategy_path`,
  `config_path`, `version`, `config`, `bar_type` — the last dumped as `null` when absent), the
  model keeps `metrics_basis` and `fill_seed` (nullable, always dumped), `session_conditions`
  (a sealed paper session's) and `cost_multiplier` (a cost-stress run's, MCP phase 4), the last
  two omitted from the dump when empty so every ordinary backtest row is byte-identical to before.

## Research Analyses (MCP phase 4)

`research_analyses` (`src/db/models/research.py`, migration `87090e0d5660`): one row per
robustness analysis of a study — `kind` ∈ sweep, sensitivity, monte_carlo, walk_forward,
breadth, cost_stress, and since MCP phase 5a `portfolio` (migration `72a945ca06f5` widened the
CHECK; computed from stored curves and inserted already `completed`, its `result` holding
both combined curves downsampled); `request` (what was asked, plus a `job_id → cell` map for
batches) and `result`, written once (`SyncResearchRepository.complete_analysis`, pending →
completed or void).
A batch's member jobs are `research_trials` rows with `analysis_pk`; `walk_forward`, `breadth`
and `cost_stress` joined the trial roles (never counted, never freezable).

## Results Extraction

`src/core/results_extractor.py` — pulls metrics from Nautilus engine:

**Nautilus-provided**: Sharpe, Sortino, volatility, avg_return, total_pnl, expectancy, avg_win/loss, max_winner/loser.

**profit_factor** is `net_profit_factor(analyzer.realized_pnls(USD))` (`src/core/metrics.py`): dollar wins over dollar losses, net of commission. Nautilus's own `Profit Factor` statistic is price-return based and ignores commission and size (found 2026-10-13; the research gates judge on this column).

**Custom-calculated** (Nautilus doesn't provide):
- Max drawdown (peak-to-trough)
- CAGR (from starting balance, final balance, date range)
- Calmar ratio (CAGR / max_drawdown)
- Max drawdown duration in days

Extract results **before engine goes out of scope** — engine is single-use.

**Live hosts (Story 5.1, FR41).** The same extractor computes a paper session's metrics —
there is no second metrics path. Settle the host first, then pass it as `engine=`:

```python
host = LiveHost.settle(source, starting_balance=Decimal("100000"))  # source: .portfolio + .cache
result = ResultsExtractor(engine=host).extract_results(start, end)
```

`settle` is the live analogue of `BacktestEngine.end()`. It collects the session's legs —
`cache.positions()` + `cache.position_snapshots()`, less reconciliation-owned (`EXTERNAL` /
`INTERNAL-DIFF`) positions, which `trades` never records either — copies them in close order
(open legs last, the order a HEDGING backtest presents), and primes a **copy** of the source
portfolio's analyzer with a **ledger** (starting capital + Σ realized P&L) in place of an
account. The settled host is a snapshot: nothing done to the source afterwards changes it, and
`settle` never writes to the source. Four rules follow:

- **Never an IB balance.** IB's `AccountBalance.total` is `NetLiquidation`, or a literal
  `400000` under margin stress. A live host's final balance and `PnL (total)` come from the
  ledger only. Neither its `cache` nor its `portfolio` has an account accessor, and a raw
  `TradingNode` passed as `engine=` is a `TypeError`. On a finished backtest the ledger equals
  the account's balance to the cent, which is why the two paths agree.
- **NETTING legs count.** A re-entered NETTING position keeps its earlier closed legs in
  `position_snapshots()`; the settled host's `cache.positions_closed()` returns them.
- **Capital is the session's own.** `starting_balance` is a required `Decimal`, positive, in
  whole cents. The extractor never falls back to `settings.default_balance` for a live host, and
  refuses a different one.
- **The source decides completeness.** `settle` reads whatever legs its source holds, and a
  node's cache is **not** a complete record of a NETTING session. A flip in one order replaces
  the closed position with no snapshot, a redelivered closing fill is snapshotted twice, and a
  restart drops every snapshot. A seal builds its source from `trades` rows (one per
  `PositionClosed`, deduplicated by `insert_trade_if_absent`), not from a node.

The dispatch lives in two helpers (`_starting_balance`, `_final_balance`); every metric formula
is shared.

**A sealed session's source: its trade rows (Story 5.2, AR35).** `src/core/session_equity.py`
turns the session's `trades` rows into the legs `settle` reads and runs the unmodified extractor
over them — it computes no metric itself:

```python
sealed = reconstruct_session_results(
    rows, starting_capital=Decimal("100000"), start=first_start, end=sealed_at  # tz-aware
)
sealed.result                     # BacktestResult — the one results vocabulary
sealed.merged_into(snapshot)      # snapshot + session_conditions["equity_series"], beside 5.4's keys
```

- **One leg per closed row**, carrying the row's `profit_loss` and the return Nautilus would have
  computed for that position, `(exit − entry) / entry`, side-adjusted. Open rows (no exit) are
  not part of the closed-trade sequence and are left out. Each of these is refused with a
  `ValueError` that names it:
  - a closed row with no P&L or exit price;
  - a row with an exit price but no exit timestamp;
  - a repeated row id;
  - a non-positive entry price, an unknown side, or a non-USD commission;
  - a naive or non-forward period;
  - closed trades that produced no series.
- **The curve has a point per distinct close instant, not per trade.** Legs closing at the
  identical timestamp are summed into one return, exactly as a backtest's are, and times are
  whole seconds.
- **Read a drawdown with its definition.** A backtest's max drawdown compounds each position's
  *price* return as if all capital were in every trade, so it does not depend on position size
  (measured: −1.59% at 10% and at 2% sizing, where the account fell 0.15% / 0.03%). A sealed
  session's uses the same definition (PO ruling R1b), so the two compare like for like — and
  neither is the account's dollar drawdown. `session_conditions.equity_series` records the basis
  (`per_trade_price_return`), the granularity (`closed_trade`, not bar), the closed-trade count,
  what the series excludes (`open_positions`, `reconciliation_owned_activity`) and the series.
- **Zero closed trades** is an explicit record: 0 trades, an empty series, no drawdown or Calmar.
  A non-finite metric is refused rather than written.

**The seal: one transaction across the async/sync seam (Story 5.3).**
`src/services/session_seal_service.py`'s `seal_session(db: AsyncSession, …)` is the only place a
paper session becomes a `backtest_runs` row. The backtest write (`BacktestPersistenceService`) is
async-only; the session's state machine (`SessionService`) is sync-only and must stay Nautilus-free.
`AsyncSession.run_sync(fn)` runs `fn` with a sync `Session` on the async session's **own
connection**, so both halves share one transaction:

```python
claimed = await db.run_sync(_claim, …)  # SessionService.transition(to=SEALED) — FOR UPDATE, first
holdings = read_open_positions(session_id)  # Redis: view + legs + working orders (Story 5.5)
                                        # held position, no --force → SealRefusedError
await db.run_sync(_record_open_legs, pk, holdings.legs)  # --force: one open trade per leg
trades = await db.run_sync(_trades_of, pk)                  # find_session_trades, DB id order
run = await BacktestPersistenceService(BacktestRepository(db)).save_backtest_results(…)  # verbatim
run.run_type = "paper"; await db.flush()                    # create_backtest_run has no run_type
await db.run_sync(_backfill, pk, run.id, len(trades))       # count must equal the rows read
```

- **The seal never commits.** The caller does: `live seal` commits only when `seal_session`
  returns and rolls back explicitly otherwise, so any raise leaves nothing — no orphan run row a
  list view would show (AR5), no `sealed` session without its record. Tests drive it on the
  rolled-back `db_session` fixture. Once the commit lands the seal is permanent: a failure in the
  cleanup after it is noted beside the report, never reported as a failed seal.
- **Judged before it is written** (code review):
  - the starting capital first, before the lock: 5.1's positive/whole-cent rule plus Nautilus's
    `MONEY_MAX` (`require_sealable_capital`; the CLI applies it as a usage error, exit 2);
  - the seal instant is read by `transition()` itself, after the row lock, and the same instant
    is `end_date`;
  - a metric its `performance_metrics` column cannot hold (`Numeric(15,6)` ratios stay under
    1e9 — a 1-day session at +6% computes a CAGR of ~1.75e9) is refused by name rather than left
    to an unexplained Postgres `DataError`;
  - NaN/Infinity stays the persistence service's own refusal (AR12).
- **Open positions at seal are the operator's decision** (Story 5.5, FR47, AR30; PO rulings
  R1a / R2a / R3b, 2026-10-11):
  - the reader is `read_session_holdings` (`src/core/live_session_view.py`): the same load-only
    read as `read_session_view`, plus one `OpenLeg` per open position on the account
    (strategy- *and* reconciliation-owned; legs netting to zero leave no row and no leg) and every
    `WorkingOrder` the cache holds (`load_order`, the fifth `LOAD_METHODS` member);
  - without `force` a held position refuses, listing each instrument and quantity;
  - with `force` each leg becomes an **open trade with no exit** through `open_trade`
    (`src/services/seal_open_positions.py`, with the dispositions and the refusal text) →
    `insert_trade_if_absent` (`client_order_id` NULL, so it never conflicts), written **before**
    `find_session_trades` so 5.2 leaves it out of the metrics and the backfill links it — its
    count check covers the open rows too. It is the backtest's own open row, column for column
    (`tests/component/services/test_open_trade_parity.py`); a *partly reduced* position is where
    they part: the backtest keeps `peak_qty` and the reduction's price as an exit price with no
    exit time, the seal keeps what remains and no exit;
  - `force` also seals past positions on another account (counted, not recorded) and past
    `NO_ENGINE_STATE` (holdings reported **unknown**, never "none open"); an unreadable cache, an
    unreachable Redis or an unset `TWS_ACCOUNT` still refuses;
  - working orders are **reported** with their disposition, never a refusal, never cancelled.
    The cache cannot see what changed at IBKR while the session was stopped. An order the cache
    cannot rebuild or read is **named** (`unreadable_orders`), never a refusal (code review DN1a);
    an open order with nothing left to fill is not working;
  - every disposition names the session's engine cache as its basis; an unknown holding is
    `holdings_known: false`, never a concrete "none", and the unknown disposition tells the
    operator to confirm `REDIS_HOST`/`REDIS_PORT` (a reachable but wrong Redis reads as empty);
  - P&L already realized on a partly reduced, still-open position lives only on its open row's
    `profit_loss`, so it is outside the record's metrics (5.2 reads closed rows only) — the
    disposition says so; the fix is the epic-level both-sides decision.
- **Status moves through `SessionService.transition()` only** (AR37);
  `SessionService.record_sealed_run` then writes `sealed_run_id` and nothing else — refused unless
  the row is already `sealed`, and write-once.
- **The run row's identity comes from the spec as stored** (one strategy; `strategy_name` is the
  backtest path's `strategy_type.replace("_", " ").title()`; symbols joined with `,`); `start_date`
  is the session's `created_at` (nothing durable records its first start, so CAGR can read low but
  never high; the record says so in `session_conditions.start_date_basis`); `end_date` is the
  instant `sealed_at` was stamped with; `data_source` is
  `ibkr_live`; `initial_capital` is the operator's `--starting-capital`, as given.
- **The record states what it ran under (Story 5.4, FR43/FR45, AR7).**
  `src/services/session_conditions.py`'s `build_session_conditions(...)` writes the flat keys of
  `config_snapshot["session_conditions"]`. They join the base snapshot **before** `merged_into`,
  so 5.2's `equity_series` lands beside them. The keys:

  | Key | Value | Source |
  |---|---|---|
  | `schema_version` | `1` | — |
  | `starting_capital` | `"100000.00"` | `--starting-capital`, at the column's own scale (`1E+5` → `"100000.00"`), never scaled against a backtest |
  | `rth_only` | `true` | `resolve_live_use_rth` refuses anything else on every start |
  | `market_data_tier` | `"REALTIME"` | imported `REQUIRED_LIVE_MARKET_DATA_TYPE_NAME`; a delayed feed stops the session |
  | `price_basis` | `"ibkr_unadjusted_smart"` | structural; a backtest's would be `"firstrate_adjusted"`, but a backtest writes no `session_conditions` at all |
  | `closed_trade_count` | int | `equity_series.closed_trades`, never recounted (pinned equal, and equal to `total_trades`) |
  | `open_positions_at_seal` | `[{instrument_id, quantity}]` or `null` | the session view, every owner netted. `[]` unless `--force` sealed past held positions (Story 5.5); `null` when the holdings are unknown (a forced seal past an empty engine cache), never `[]` |
  | `start_date_basis` | `"session_created_at"` | PO ruling R1a: `start_date` is `created_at`, a disclosed deviation from "first start" |
  | `last_run_flags` | `runtime_flags` as stored, or `null` | PO ruling R2a. Holds the **last process run's** facts only, because the flags are cleared on every start |

  The config-snapshot panel renders the document unchanged (AR7, zero UI change). Nothing here is
  recorded during the run, and no writer exists on the live path. **Reconciliation-owned activity
  is stated structurally only** (PO ruling R3a): by `price_basis` and by
  `equity_series.excludes`. A per-session flag has no durable source (see deferred-work).
- **⚠️ `run_sync` shares the identity map.** A test reading back must `expire_all()` and use fresh
  queries; touching an expired ORM attribute in async code raises `MissingGreenlet`.
- **⚠️ A delete would cascade.** `trades.backtest_run_id` is `ON DELETE CASCADE`, so deleting a
  sealed paper run would delete the session's trades — the system of record. The UI's delete is a
  no-op today; whoever implements it must refuse `run_type = 'paper'` or detach the trades first.
- **The sealed record in the existing views (Story 5.6, FR46, AR8).** The read side has no paper
  code at all. Once `backtest_run_id` is backfilled, every trade query keyed on it in
  `src/api/rest/trades.py` returns the session's rows exactly as a backtest's: markers, the
  paginated list, statistics, drawdown, the equity curve and export. Open rows from a `--force`
  seal come back as a backtest's open row does, with a null exit, one entry marker and no exit
  marker, and outside statistics and the curve. Two dispositions, not fixes: the side-unaware
  `profit_pct` (a profitable SHORT reads negative) is the same on both sides, kept for parity —
  both writers are pinned on one real SHORT by `tests/component/services/test_profit_pct_parity.py`,
  while the views only show the stored value; and
  `trades` has no `strategy_id`, which a sealed record never needs, because `run_identity` refuses
  a spec with more than one strategy. Proven against Postgres by
  `tests/integration/db/test_sealed_record_in_existing_views.py`, each query compared row by row
  (the drawdown by its hand-computed amount); the views themselves are frozen
  (`docs/agent/web-ui.md`, "Sealed Paper Sessions in These Views").

## Exceptions

**DB exceptions** (`src/db/exceptions.py`): `DatabaseConnectionError`, `DuplicateRecordError`, `ValidationError`, `InvalidSessionTransition`, `SealRefusedError` (a seal that cannot proceed as the session stands — operator-safe text, exit 1).

**Service exceptions** (`src/services/exceptions.py`): `DataNotFoundError`, `CatalogCorruptionError`, connection errors, rate limit errors (with `retry_after`).

Exception constructors designed for structured logging — always catch with context.
