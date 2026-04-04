# NTrader Data Models

## Overview

Four-layer model architecture:
1. **SQLAlchemy ORM** (`src/db/models/`) — PostgreSQL table definitions
2. **Pydantic Domain** (`src/models/`) — Business logic validation
3. **API View Models** (`src/api/models/`) — Presentation layer
4. **Repositories** (`src/db/repositories/`) — Data access abstraction

---

## Database Schema (PostgreSQL 16 + TimescaleDB)

### Table: `backtest_runs`

| Column | Type | Constraints | Index |
|--------|------|-------------|-------|
| id | BigInteger | PK, autoincrement | Yes |
| run_id | UUID | UNIQUE, NOT NULL | Yes (unique) |
| strategy_name | String(255) | NOT NULL | Composite |
| strategy_type | String(100) | NOT NULL | Composite |
| instrument_symbol | String(50) | NOT NULL | Yes |
| start_date | TIMESTAMP(tz) | NOT NULL | — |
| end_date | TIMESTAMP(tz) | NOT NULL | — |
| initial_capital | Numeric(20,2) | NOT NULL, >0 | — |
| data_source | String(100) | NOT NULL | — |
| execution_status | String(20) | NOT NULL | Yes |
| execution_duration_seconds | Numeric(10,3) | NOT NULL, >=0 | — |
| error_message | Text | NULL | — |
| config_snapshot | JSONB | NOT NULL | — |
| reproduced_from_run_id | UUID | NULL | — |
| created_at | TIMESTAMP(tz) | NOT NULL, server_default | Yes |

**Check Constraints:** `end_date > start_date`, `initial_capital > 0`, `execution_duration_seconds >= 0`

**Composite Indexes:** `(created_at, id)`, `(strategy_name, created_at, id)`

**Relationships:** one-to-one → `PerformanceMetrics`, one-to-many → `Trade` (both CASCADE delete)

**Design Notes:**
- Dual ID: `id` (internal PK) + `run_id` (UUID business key for external use)
- `config_snapshot` (JSONB) stores complete strategy config for reproducibility
- `reproduced_from_run_id` links reproduction runs to originals

---

### Table: `performance_metrics`

| Column | Type | Constraints |
|--------|------|-------------|
| id | BigInteger | PK |
| backtest_run_id | BigInteger | FK(backtest_runs.id), UNIQUE |
| total_return | Numeric(15,6) | NOT NULL |
| final_balance | Numeric(20,2) | NOT NULL |
| cagr | Numeric(15,6) | NULL |
| sharpe_ratio | Numeric(15,6) | NULL |
| sortino_ratio | Numeric(15,6) | NULL |
| max_drawdown | Numeric(15,6) | NULL |
| max_drawdown_date | TIMESTAMP(tz) | NULL |
| calmar_ratio | Numeric(15,6) | NULL |
| volatility | Numeric(15,6) | NULL |
| risk_return_ratio | Numeric(15,6) | NULL |
| avg_return | Numeric(15,6) | NULL |
| avg_win_return | Numeric(15,6) | NULL |
| avg_loss_return | Numeric(15,6) | NULL |
| total_trades | Integer | NOT NULL, default=0 |
| winning_trades | Integer | NOT NULL, default=0 |
| losing_trades | Integer | NOT NULL, default=0 |
| win_rate | Numeric(5,4) | NULL |
| profit_factor | Numeric(15,6) | NULL |
| expectancy | Numeric(20,2) | NULL |
| avg_win | Numeric(20,2) | NULL |
| avg_loss | Numeric(20,2) | NULL |
| total_pnl | Numeric(20,2) | NULL |
| total_pnl_percentage | Numeric(15,6) | NULL |
| max_winner | Numeric(20,2) | NULL |
| max_loser | Numeric(20,2) | NULL |
| min_winner | Numeric(20,2) | NULL |
| min_loser | Numeric(20,2) | NULL |

**Check Constraint:** `total_trades = winning_trades + losing_trades`

---

### Table: `trades`

| Column | Type | Constraints | Index |
|--------|------|-------------|-------|
| id | BigInteger | PK | Yes |
| backtest_run_id | BigInteger | FK, NOT NULL | Yes |
| instrument_id | String(50) | NOT NULL | Yes |
| trade_id | String(100) | NOT NULL | — |
| venue_order_id | String(100) | NOT NULL | — |
| client_order_id | String(100) | NULL | — |
| order_side | String(10) | NOT NULL | — |
| quantity | Numeric(20,8) | NOT NULL, >0 | — |
| entry_price | Numeric(20,8) | NOT NULL, >0 | — |
| exit_price | Numeric(20,8) | NULL, >0 if set | — |
| commission_amount | Numeric(20,8) | NULL | — |
| commission_currency | String(10) | NULL | — |
| fees_amount | Numeric(20,8) | NULL | — |
| profit_loss | Numeric(20,8) | NULL | — |
| profit_pct | Numeric(10,4) | NULL | — |
| holding_period_seconds | Integer | NULL | — |
| entry_timestamp | TIMESTAMP(tz) | NOT NULL | Yes |
| exit_timestamp | TIMESTAMP(tz) | NULL | — |
| created_at | TIMESTAMP(tz) | NOT NULL, server_default | — |

**Check Constraints:** `quantity > 0`, `entry_price > 0`, `exit_price IS NULL OR exit_price > 0`

**Composite Index:** `(backtest_run_id, entry_timestamp DESC)` for detail queries

**P&L Formula:** `(exit_price - entry_price) * quantity - commission - fees`

---

### Migration History (Alembic)

| Revision | Date | Tables |
|----------|------|--------|
| 9c7d5c448387 | 2025-10-26 | backtest_runs, performance_metrics |
| 0937d13... | — | Additional Nautilus fields |
| 7d28f3a... | — | market_data |
| 34f3c8e99016 | 2025-11-22 | trades |

---

## Pydantic Domain Models

### BacktestRequest (`src/models/backtest_request.py`)

Unified request model for CLI and web UI backtest execution.

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| strategy_type | str | — | Required |
| strategy_path | str | — | Module path to strategy class |
| config_path | str | None | Module path to config class |
| strategy_config | dict | {} | Strategy parameters |
| symbol | str | — | Required |
| instrument_id | str | — | Full instrument ID |
| start_date | datetime | — | UTC-aware |
| end_date | datetime | — | UTC-aware |
| bar_type | str | — | e.g., "1-DAY-LAST" |
| persist | bool | True | Save to DB |
| data_source | str | catalog | catalog/ibkr/kraken/mock |
| starting_balance | Decimal | 1000000 | Initial capital |

**Class Methods:** `from_yaml_config()`, `from_yaml_file()`, `from_cli_args()`, `to_config_snapshot()`

---

### Trade Models (`src/models/trade.py`)

**TradeBase** — Base validation (instrument_id, prices, timestamps, costs)

**TradeCreate** — Extends TradeBase with `backtest_run_id`

**Trade** — Full model with calculated fields (profit_loss, profit_pct, holding_period_seconds)

**EquityCurvePoint** — `{timestamp, balance, cumulative_return_pct, trade_number}`

**EquityCurveResponse** — `{points, initial_capital, final_balance, total_return_pct}`

**DrawdownPeriod** — Peak/trough with timestamps, amounts, duration, recovery status

**DrawdownMetrics** — `{max_drawdown, top_drawdowns (5), current_drawdown, total_periods}`

**TradeStatistics** — Aggregate metrics (counts, rates, streaks, holding periods)

---

### CatalogAvailability (`src/models/catalog_metadata.py`)

Metadata for Parquet catalog data availability per instrument.

| Field | Type | Notes |
|-------|------|-------|
| instrument_id | str | e.g., "AAPL.NASDAQ" |
| bar_type_spec | str | e.g., "1-MINUTE-LAST" |
| start_date | datetime | Earliest data (UTC) |
| end_date | datetime | Latest data (UTC) |
| file_count | int | Parquet file count |
| total_rows | int | Approximate bar count |

**Methods:** `covers_range(start, end)`, `overlaps_range(start, end)`

---

### FetchRequest (`src/models/catalog_metadata.py`)

IBKR historical data fetch operation tracker.

States: PENDING → IN_PROGRESS → COMPLETED | FAILED (max 5 retries)

---

### Strategy Models (`src/models/strategy.py`)

**SMAParameters** — `fast_period` (1-200), `slow_period` (1-200), `portfolio_value`, `position_size_pct`. Validator: slow > fast.

**MomentumParameters** — `trade_size`, `fast_period`, `slow_period`, `warmup_days`, `allow_short`

**TradingStrategy** — Full entity with UUID, name, type, parameters, lifecycle status (DRAFT → ACTIVE → ARCHIVED)

---

## API View Models

### BacktestDetailView (`src/api/models/backtest_detail.py`)

**MetricDisplayItem** — `{name, value, format_type, tooltip, is_favorable}` with computed `formatted_value` and `color_class` (Tailwind CSS).

**MetricsPanel** — `{return_metrics, risk_metrics, trading_metrics}` (list of MetricDisplayItem each)

**ConfigurationSnapshot** — Immutable config with computed `date_range_display` and `cli_command`

---

### BacktestListItem (`src/api/models/backtest_list.py`)

Table row with computed `run_id_short` (8 chars), `return_percentage`, `is_positive_return`, `status_color`.

**BacktestListPage** — Paginated response with computed `total_pages`, `has_next`, `has_previous`.

---

### FilterState (`src/api/models/filter_models.py`)

Complete filter/sort/pagination state. Enums: `SortOrder` (asc/desc), `ExecutionStatus` (success/failed), `SortColumn` (7 columns).

Method: `to_query_params()` for URL query string preservation.

---

### BacktestRunFormData (`src/api/models/run_backtest.py`)

Form validation model. Constants: `VALID_DATA_SOURCES`, `VALID_TIMEFRAMES`.

**StrategyParamField** — Dynamic form field descriptor (name, type, default, min, max).

**schema_to_fields()** — Converts Pydantic model → list of form field descriptors.

---

## Session Management

### Async (Web UI — asyncpg)

`src/db/session.py` — `get_async_engine()`, `get_async_session_maker()`, `get_session()` (context manager), `test_connection()`, `dispose_all_connections()`

Pool config: size, max_overflow, timeout, pre_ping, recycle=3600s.

### Sync (CLI — psycopg2)

`src/db/session_sync.py` — `get_sync_engine()`, `get_sync_session_maker()`, `get_sync_session()` (context manager with auto-commit/rollback), `dispose_sync_connections()`

---

## Decimal Precision Conventions

| Use Case | Precision | Scale |
|----------|-----------|-------|
| Currency (capital, balance, P&L) | 20 | 2 |
| Ratios (return, sharpe) | 15 | 6 |
| Prices (entry, exit) | 20 | 8 |
| Quantities | 20 | 8 |
| Win rate | 5 | 4 |

All timestamps: `TIMESTAMP WITH TIME ZONE`, always UTC.
