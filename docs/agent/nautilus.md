# Nautilus Trader Reference

## LogGuard Pattern

Nautilus Trader's C/Cython logging subsystem **panics if initialized twice** in a process.
When `IBKRHistoricalClient` starts first, it initializes logging. If a `BacktestEngine` is
created later, it would try to re-initialize and crash.

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

**Web context**: `init_logging()` called at module level in `src/api/web.py`.
**IBKR context**: `_guard_nautilus_logging()` context manager in `ibkr_client.py` prevents double-init.

**Symptoms of double-init**: Process crashes with "logger already initialized" panic — no
Python traceback, just a segfault or abort.

## C Extension Isolation

Nautilus uses C/Rust extensions that don't survive `fork()` cleanly. This causes:
- Segfaults in child processes
- Corrupted global state across tests

**Fix**: Integration tests run with `--forked` (pytest-forked) so each test gets a fresh
process. See `make test-integration`. Always double `gc.collect()` after engine disposal
(see `integration_cleanup()` fixture in `tests/integration/conftest.py`).

## BacktestEngine Lifecycle

`BacktestEngine` is **single-use** — it cannot be reset or reused after a run completes.
Create a new engine instance for each backtest.

**Prefer `BacktestOrchestrator`** (takes `BacktestRequest`, handles persistence) over `MinimalBacktestRunner` (legacy, direct params).

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

Extract results **before engine goes out of scope**:

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

# Trade history (closed positions only, not open)
closed_positions = engine.cache.positions_closed()
positions_report = engine.trader.generate_positions_report()
```

**Custom metrics** calculated by `ResultsExtractor` (Nautilus doesn't provide): max drawdown, CAGR, Calmar ratio, max drawdown duration. Starting balance must be passed separately.

### Venue Configuration

```python
venue = Venue("SIM")  # mock data
venue = bars[0].bar_type.instrument_id.venue  # real data (e.g., "NASDAQ")
```

The venue must match between instrument, bars, and engine setup.
`BacktestOrchestrator` tracks `_venue`, `_backtest_start_date`, `_backtest_end_date` since Nautilus doesn't retain them.

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

**Strategy lifecycle**: `on_start()` → `on_bar()` → `on_stop()` → `on_dispose()`.
**Extract pure logic** into framework-free classes (e.g., `SMATradingLogic`) for testability.
**Position sizing**: Must respect instrument precision (fractional crypto, whole equities).

`StrategyFactory.build_strategy_params()` resolves parameters via:
overrides → settings map → Pydantic defaults.

Config and parameter validation happen separately — a strategy can be registered without a config class.

For data sources and exchange clients, see `docs/agent/data-pipeline.md`.
