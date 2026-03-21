# Nautilus Trader Reference

## LogGuard Pattern

Nautilus Trader's C/Cython logging subsystem **panics if initialized twice** in a process.
When `HistoricInteractiveBrokersClient` starts first, it initializes logging. If a
`BacktestEngine` is created later, it would try to re-initialize and crash.

**Solution** — `src/utils/logging.py` stores a module-level `_nautilus_log_guard`:

```python
_nautilus_log_guard: Any = None

def set_nautilus_log_guard(log_guard: Any) -> None:
    global _nautilus_log_guard
    if _nautilus_log_guard is None:
        _nautilus_log_guard = log_guard
```

Always call `set_nautilus_log_guard()` when creating a Nautilus component that initializes
logging. Never let the guard go out of scope — it must live for the process lifetime.

**Symptoms of double-init**: Process crashes with "logger already initialized" panic — no
Python traceback, just a segfault or abort.

## C Extension Isolation

Nautilus uses C/Rust extensions that don't survive `fork()` cleanly. This causes:
- Segfaults in child processes
- Corrupted global state across tests

**Fix**: Integration tests run with `--forked` (pytest-forked) so each test gets a fresh
process. See `make test-integration`. Always double `gc.collect()` after engine disposal.

## BacktestEngine Lifecycle

`BacktestEngine` is **single-use** — it cannot be reset or reused after a run completes.
Create a new engine instance for each backtest. The `backtest_runner.py` handles this.

### Engine Setup Sequence (Strict Order)

The engine must be configured in this exact order. Violations cause cryptic errors:

```python
config = BacktestEngineConfig(trader_id=TraderId("BACKTESTER-001"))
engine = BacktestEngine(config=config)

# 1. Add venue FIRST (before instrument)
engine.add_venue(venue=venue, oms_type=OmsType.HEDGING,
    account_type=AccountType.MARGIN, starting_balances=[Money(1_000_000, USD)],
    fill_model=fill_model, fee_model=fee_model)

# 2. Add instrument (venue must exist)
engine.add_instrument(instrument)

# 3. Add data
engine.add_data(bars)

# 4. Add strategy (last, after all data)
engine.add_strategy(strategy=strategy)

# 5. Run
engine.run()
```

### Result Extraction

```python
# Portfolio metrics
analyzer = engine.portfolio.analyzer
stats_returns = analyzer.get_performance_stats_returns()  # Sharpe, Sortino, etc.
stats_pnls = analyzer.get_performance_stats_pnls(currency=USD)  # PnL metrics

# Key metric keys
sharpe = stats_returns.get("Sharpe Ratio (252 days)")
sortino = stats_returns.get("Sortino Ratio (252 days)")
profit_factor = stats_returns.get("Profit Factor")
total_pnl = stats_pnls.get("PnL (total)")

# Account balance
account = engine.cache.account_for_venue(venue)
final_balance = float(account.balance_total(USD).as_double())

# Trade history
closed_positions = engine.cache.positions_closed()
positions_report = engine.trader.generate_positions_report()
```

### Venue Configuration

```python
venue = Venue("SIM")  # mock data
venue = bars[0].bar_type.instrument_id.venue  # real data (e.g., "NASDAQ")
```

The venue must match between instrument, bars, and engine setup.

### Fill and Fee Models

```python
from nautilus_trader.backtest.models import FillModel
from src.core.fee_models import IBKRCommissionModel

fill_model = FillModel(prob_fill_on_limit=0.95, prob_fill_on_stop=0.95, prob_slippage=0.01)
fee_model = IBKRCommissionModel(
    commission_per_share=settings.commission_per_share,
    min_per_order=settings.commission_min_per_order,
    max_rate=settings.commission_max_rate,
)
```

## Parquet Data Catalog

Market data lives in Parquet files under the Nautilus catalog structure.
`src/services/data_catalog.py` and `data_service.py` handle reading/writing.
CSV data is converted via `csv_loader.py` → `nautilus_converter.py` → Parquet.
Kraken data is fetched via `kraken_client.py` and written to the same Parquet catalog structure.

## IBKR Client

`src/services/ibkr_client.py` connects to Interactive Brokers TWS/Gateway.

**All connection settings come from environment variables** via `src/config.py:IBKRSettings`:
- `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`
- `TWS_USERNAME`, `TWS_PASSWORD`, `TWS_ACCOUNT`
- `IBKR_TRADING_MODE` (paper/live), `IBKR_READ_ONLY` (default: true)

Rate limit: 45 req/s (90% of IBKR's 50/s hard limit).
Never hardcode IBKR connection values — always use env vars through `IBKRSettings`.

## Kraken Client

`src/services/kraken_client.py` fetches historical OHLCV data from Kraken.

**Configuration** via `src/config.py:KrakenSettings`:
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET`
- `KRAKEN_RATE_LIMIT` (default: 10 req/s)
- `KRAKEN_DEFAULT_MAKER_FEE` / `KRAKEN_DEFAULT_TAKER_FEE`

**Pair mapping**: Users specify standard pairs (BTC/USD). Internally mapped to:
- REST API format: XXBTZUSD
- Charts API format: XBT/USD
- Nautilus InstrumentId: BTC/USD.KRAKEN

**Data source**: Futures Charts API (`/api/charts/v1/spot/{symbol}/{resolution}`)
supports arbitrary date ranges with pagination (unlike Spot OHLC which is limited to 720 entries).

Rate limit: 10 req/s default (sliding window). Never hardcode credentials.

## Strategy Config Pattern

Each strategy defines a `StrategyConfig` subclass with Pydantic-validated parameters.
The `@register_strategy` decorator links the config class to the strategy:

```python
@register_strategy(
    name="sma_crossover",
    aliases=["sma", "smacrossover"],
)
class SMACrossover(Strategy):
    ...
```

`StrategyLoader.build_strategy_params()` resolves parameters via:
overrides → settings map → Pydantic defaults.
