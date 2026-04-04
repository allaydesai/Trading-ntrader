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
- Equity curve stored as JSON array on `PerformanceMetrics`
- Starting balance must be passed separately (Nautilus doesn't retain it)

## Results Extraction

`src/core/results_extractor.py` — pulls metrics from Nautilus engine:

**Nautilus-provided**: Sharpe, Sortino, volatility, profit_factor, avg_return, total_pnl, expectancy, avg_win/loss, max_winner/loser.

**Custom-calculated** (Nautilus doesn't provide):
- Max drawdown (peak-to-trough)
- CAGR (from starting balance, final balance, date range)
- Calmar ratio (CAGR / max_drawdown)
- Max drawdown duration in days

Extract results **before engine goes out of scope** — engine is single-use.

## Exceptions

**DB exceptions** (`src/db/exceptions.py`): `DatabaseConnectionError`, `DuplicateRecordError`, `ValidationError`.

**Service exceptions** (`src/services/exceptions.py`): `DataNotFoundError`, `CatalogCorruptionError`, connection errors, rate limit errors (with `retry_after`).

Exception constructors designed for structured logging — always catch with context.
