# NTrader API Contracts

**Framework:** FastAPI (Python) | **CLI:** Click | **Database:** PostgreSQL (async SQLAlchemy)

## REST API — Chart Data

### GET /api/timeseries

OHLCV time series data for chart rendering from Parquet catalog.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| symbol | string | yes | — | Trading symbol (e.g., "AAPL") |
| start | date | yes | — | ISO 8601 date |
| end | date | yes | — | ISO 8601 date |
| timeframe | enum | no | 1_MIN | 1_MIN, 5_MIN, 15_MIN, 1_HOUR, 1_DAY |

**Response:** `TimeseriesResponse` — `{symbol, timeframe, candles: [{time, open, high, low, close, volume}]}`

**Dependencies:** DataCatalogService

**Notes:** Symbol → Nautilus instrument_id (e.g., "AAPL" → "AAPL.NASDAQ"). Timestamps converted from nanoseconds to Unix seconds for TradingView.

---

### GET /api/equity/{run_id}

Equity curve and drawdown percentages for a backtest run.

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| run_id | UUID (path) | yes | Business identifier |

**Response:** `EquityResponse` — `{run_id, equity: [{time, value}], drawdown: [{time, value}]}`

**Dependencies:** BacktestQueryService

**Notes:** Drawdown = (current - peak) / peak * 100. Falls back to 2-point curve if no equity data stored.

---

### GET /api/trades/{run_id}

Trade entry/exit markers for chart overlay.

**Response:** `TradesResponse` — `{run_id, trade_count, trades: [{time, side, price, quantity, pnl}]}`

Each closed trade → 2 markers (entry + exit). Exit side flipped (BUY→SELL).

---

### GET /api/indicators/{run_id}

Indicator series for chart overlay (Bollinger Bands, SMA).

**Response:** `IndicatorsResponse` — `{run_id, indicators: {name: [{time, value}]}}`

Strategy-dependent:
- **Bollinger:** upper_band, middle_band, lower_band, weekly_sma
- **SMA Crossover:** sma_fast, sma_slow

Indicators recomputed on-demand using same Nautilus indicator classes.

---

## REST API — Trade Analytics

### GET /api/equity-curve/{backtest_id}

Cumulative account balance evolution. Uses internal DB ID (not UUID).

**Response:** `EquityCurveResponse` — `{points: [{timestamp, balance, cumulative_return_pct}], initial_capital, final_balance, total_return_pct}`

---

### GET /api/statistics/{backtest_id}

Comprehensive trade performance metrics.

**Response:** `TradeStatistics` — win/loss counts, rates, profit metrics, streaks, holding periods.

---

### GET /api/drawdown/{backtest_id}

Drawdown analysis from equity curve.

**Response:** `DrawdownMetrics` — `{max_drawdown, top_drawdowns (up to 5), current_drawdown, total_drawdown_periods}`

Each drawdown: peak/trough timestamps and balances, percentage, duration, recovery status.

---

### GET /api/backtests/{backtest_id}/trades

Paginated trades list with server-side sorting.

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| page | int | 1 | ≥1 |
| page_size | int | 20 | 1-100 |
| sort_by | enum | entry_timestamp | entry_timestamp, exit_timestamp, profit_loss |
| sort_order | enum | asc | asc, desc |

**Response:** `TradeListResponse` — `{trades: [...], pagination: {total_items, total_pages, ...}, sorting: {sort_by, sort_order}}`

---

### GET /api/backtests/{backtest_id}/export

Export trades to CSV or JSON. Query param `format` (csv|json, default csv).

Returns file download with Content-Disposition header. All trades, sorted by entry_timestamp.

---

## UI Routes — Server-Rendered Pages

| Route | Method | Template | Purpose |
|-------|--------|----------|---------|
| `/` | GET | dashboard.html | Dashboard with summary stats and recent backtests |
| `/backtests` | GET | backtests/list.html | Filtered, sorted, paginated backtest list |
| `/backtests/fragment` | GET | backtests/list_fragment.html | HTMX partial for table updates |
| `/backtests/{run_id}` | GET | backtests/detail.html | Backtest detail with charts and metrics |
| `/backtests/run` | GET | backtests/run.html | Backtest execution form |
| `/backtests/run` | POST | — | Submit backtest (HX-Redirect on success) |
| `/backtests/run/strategy-params/{name}` | GET | partials/strategy_params.html | HTMX dynamic strategy parameter fields |
| `/backtests/{run_id}` | DELETE | — | Delete backtest (HX-Redirect) |
| `/backtests/{run_id}/rerun` | POST | — | Rerun backtest (202 Accepted) |
| `/backtests/{backtest_id}/trades-table` | GET | partials/trades_table.html | HTMX paginated trades table |
| `/backtests/{run_id}/export` | GET | — | Export as HTML report download |

### Backtest List Filters

| Parameter | Type | Notes |
|-----------|------|-------|
| strategy | string | Exact match |
| instrument | string | Partial match |
| date_from | date | Min creation date |
| date_to | date | Max creation date |
| status | enum | success, failed |
| sort | enum | created_at, sharpe_ratio, total_return, max_drawdown |
| order | enum | asc, desc |
| page | int | 1-indexed |
| page_size | int | 1-100, default 20 |

### Backtest Run Form Fields

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| strategy | string | — | Required, validated against registry |
| symbol | string | — | Required |
| start_date | date | — | Required |
| end_date | date | — | Required |
| data_source | enum | catalog | catalog, ibkr, kraken, mock |
| timeframe | enum | 1-DAY | 1-MINUTE through 1-WEEK |
| starting_balance | decimal | 1000000 | Positive |
| timeout_seconds | int | 300 | Execution timeout |
| param_* | varies | — | Strategy-specific parameters |

Concurrency: `asyncio.Lock` prevents parallel backtest execution (per-process only).

---

## CLI Commands

### `ntrader backtest run [config.yaml] [OPTIONS]`

Execute backtest from YAML config or CLI arguments.

| Option | Short | Type | Default | Notes |
|--------|-------|------|---------|-------|
| --symbol | -sym | string | — | Required in CLI mode |
| --strategy | -s | string | — | Validated against registry |
| --start | -st | date | — | YYYY-MM-DD |
| --end | -e | date | — | YYYY-MM-DD |
| --data-source | -ds | enum | catalog | catalog, ibkr, kraken, mock |
| --starting-balance | -sb | float | 1000000 | Starting capital |
| --fast-period | -f | int | 10 | Fast SMA period |
| --slow-period | -sl | int | 20 | Slow SMA period |
| --trade-size | -ts | int | 1000000 | Shares per trade |
| --timeframe | -t | enum | 1-DAY | Bar timeframe |
| --persist/--no-persist | — | bool | persist | Save to DB |

### `ntrader run [OPTIONS]`

Quick SMA backtest with mock data for demonstrations.

### `ntrader backtest history [OPTIONS]`

List past backtests with filtering. Options: --limit, --strategy, --instrument, --status, --sort, --strategy-summary, --show-params.

### `ntrader backtest show RUN_ID`

Display detailed backtest results (metadata, config, metrics).

### `ntrader backtest compare RUN_ID1 RUN_ID2 [...]`

Side-by-side comparison (2-10 backtests). Rich-formatted table output.

### `ntrader backtest reproduce RUN_ID`

Re-execute backtest with identical configuration from config_snapshot.

### `ntrader data import --csv FILE --symbol SYM [OPTIONS]`

Import CSV to Parquet catalog. Options: --venue, --bar-type, --conflict-mode.

### `ntrader strategy list`

List available strategies with descriptions.

### `ntrader strategy create --type TYPE --output FILE`

Generate YAML config template for a strategy.

### `ntrader report summary RESULT_ID`

Display key metrics for a backtest run.

### `ntrader report generate --result-id ID [OPTIONS]`

Generate report in text/csv/json format. Option: --output for file output.

---

## Dependency Injection

```
get_db() → AsyncSession
  └→ get_backtest_repository(session) → BacktestRepository
      └→ get_backtest_query_service(repo) → BacktestQueryService

get_data_catalog_service() → DataCatalogService (singleton)
get_templates() → Jinja2Templates (singleton)
```

**Type Aliases** for route signatures:
- `DbSession = Annotated[AsyncSession, Depends(get_db)]`
- `BacktestRepo = Annotated[BacktestRepository, Depends(...)]`
- `BacktestService = Annotated[BacktestQueryService, Depends(...)]`
- `DataCatalog = Annotated[DataCatalogService, Depends(...)]`

---

## Error Handling

| HTTP Code | Condition |
|-----------|-----------|
| 200 | Success |
| 404 | Resource not found |
| 409 | Concurrent backtest execution |
| 422 | Validation error (Pydantic) |
| 500 | Server error / timeout |

**Service Exceptions:**
- `DataNotFoundError` → 404 with fetch suggestion
- `IBKRConnectionError` → Connection diagnostics
- `KrakenConnectionError` → Network check suggestions
- `RateLimitExceededError` → Retry with `retry_after` value
- `CatalogCorruptionError` → Recovery options

**Security:** No authentication. No CORS. No middleware. Single-origin development mode.
