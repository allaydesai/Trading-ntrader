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
  `initial_capital → final_balance` fallback. Beyond its five base keys (`strategy_path`,
  `config_path`, `version`, `config`, `bar_type` — the last dumped as `null` when absent), the
  model keeps exactly one more: `session_conditions` (a sealed paper session's), omitted from
  the dump when empty so every backtest row is byte-identical to before.

## Results Extraction

`src/core/results_extractor.py` — pulls metrics from Nautilus engine:

**Nautilus-provided**: Sharpe, Sortino, volatility, profit_factor, avg_return, total_pnl, expectancy, avg_win/loss, max_winner/loser.

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

## Exceptions

**DB exceptions** (`src/db/exceptions.py`): `DatabaseConnectionError`, `DuplicateRecordError`, `ValidationError`.

**Service exceptions** (`src/services/exceptions.py`): `DataNotFoundError`, `CatalogCorruptionError`, connection errors, rate limit errors (with `retry_after`).

Exception constructors designed for structured logging — always catch with context.
