---
name: nautilus-engine-patterns
description: >
  Use when running backtests, debugging engine crashes, working with BacktestEngine lifecycle,
  or encountering segfaults and LogGuard panics. Covers engine setup sequence, result extraction,
  and C extension isolation.
---

# Nautilus Engine Patterns

## LogGuard Lifecycle (Critical)

Nautilus Trader's C/Rust logging subsystem panics if initialized twice in the same process.
This happens when multiple Nautilus components are created (e.g., IBKR client + BacktestEngine).

**Solution**: Store the LogGuard at module level so it persists for the process lifetime.

```python
# src/utils/logging.py
_nautilus_log_guard: Any = None  # Module-level — never goes out of scope

def set_nautilus_log_guard(log_guard: Any) -> None:
    """Store LogGuard from FIRST Nautilus component. Subsequent calls are no-ops."""
    global _nautilus_log_guard
    if _nautilus_log_guard is None:
        _nautilus_log_guard = log_guard
```

**Symptoms of double-init**: Process crashes with "logger already initialized" panic — no Python traceback, just a segfault or abort.

**Fix**: Ensure `set_nautilus_log_guard()` is called when the first Nautilus component starts, and the guard variable is never reassigned or garbage collected.

## BacktestEngine is Single-Use

After `engine.run()`, the engine cannot be reused. You must create a new `BacktestEngine` instance for each backtest run.

```python
# WRONG — will crash or produce incorrect results
engine.run()
engine.run()  # Second run fails

# CORRECT — new engine per run
engine1 = BacktestEngine(config=config)
# ... setup engine1 ...
engine1.run()

engine2 = BacktestEngine(config=config)
# ... setup engine2 ...
engine2.run()
```

The `MinimalBacktestRunner` handles this by creating a new engine in each `run_*` method.

## Engine Setup Sequence (Strict Order)

The engine must be configured in this exact order. Violations cause cryptic errors:

```python
# 1. Create config
config = BacktestEngineConfig(trader_id=TraderId("BACKTESTER-001"))

# 2. Create engine
engine = BacktestEngine(config=config)

# 3. Add venue FIRST (before instrument)
venue = Venue("SIM")  # or actual venue like "NASDAQ"
engine.add_venue(
    venue=venue,
    oms_type=OmsType.HEDGING,
    account_type=AccountType.MARGIN,
    starting_balances=[Money(1_000_000, USD)],
    fill_model=fill_model,     # Optional: FillModel for realistic execution
    fee_model=fee_model,       # Optional: commission model
)

# 4. Add instrument (venue must exist first)
engine.add_instrument(instrument)

# 5. Add data (bars, ticks, etc.)
engine.add_data(bars)

# 6. Add strategy (last, after all data is loaded)
engine.add_strategy(strategy=strategy)

# 7. Run
engine.run()
```

## Result Extraction

After `engine.run()`, extract results through these APIs:

```python
# Portfolio-level metrics
analyzer = engine.portfolio.analyzer
stats_returns = analyzer.get_performance_stats_returns()  # Sharpe, Sortino, etc.
stats_pnls = analyzer.get_performance_stats_pnls(currency=USD)  # PnL metrics
returns_series = analyzer.returns()  # pandas Series of returns

# Account balance
venue = Venue("SIM")
account = engine.cache.account_for_venue(venue)
final_balance = float(account.balance_total(USD).as_double())

# Trade history
closed_positions = engine.cache.positions_closed()
for pos in closed_positions:
    pnl = pos.realized_pnl.as_double()
    # pos.is_long, pos.is_short, pos.ts_closed (nanoseconds)

# Orders and open positions
all_orders = engine.cache.orders()
open_positions = engine.cache.positions_open()

# Reports (DataFrame)
positions_report = engine.trader.generate_positions_report()
```

## Key Metric Extraction

```python
# From stats_returns dict:
sharpe = stats_returns.get("Sharpe Ratio (252 days)")
sortino = stats_returns.get("Sortino Ratio (252 days)")
volatility = stats_returns.get("Returns Volatility (252 days)")
profit_factor = stats_returns.get("Profit Factor")

# From stats_pnls dict:
total_pnl = stats_pnls.get("PnL (total)")
expectancy = stats_pnls.get("Expectancy")
avg_winner = stats_pnls.get("Avg Winner")
avg_loser = stats_pnls.get("Avg Loser")
```

## C Extension Isolation

Nautilus Trader uses C/Rust extensions that corrupt state across `fork()`. This affects:

- **Integration tests**: Must use `--forked` pytest flag (one process per test)
- **Cleanup**: Always double `gc.collect()` after engine disposal

```python
import gc

# After each integration test
engine.dispose()
gc.collect()
gc.collect()  # Second pass catches weak references
```

Already configured in `make test-integration` via `pytest --forked`.

## Venue Configuration

```python
# For backtests with mock data
venue = Venue("SIM")

# For backtests with real IBKR data — use the actual venue from bars
venue = bars[0].bar_type.instrument_id.venue  # e.g., Venue("NASDAQ")

# Or from instrument
venue = instrument.id.venue
```

The venue must match between instrument, bars, and engine setup.

## Fill and Fee Models

```python
from nautilus_trader.backtest.models import FillModel
from src.core.fee_models import IBKRCommissionModel

fill_model = FillModel(
    prob_fill_on_limit=0.95,
    prob_fill_on_stop=0.95,
    prob_slippage=0.01,
)

fee_model = IBKRCommissionModel(
    commission_per_share=settings.commission_per_share,
    min_per_order=settings.commission_min_per_order,
    max_rate=settings.commission_max_rate,
)
```

## Deeper Reference

See `agent_docs/nautilus.md` for:
- Full IBKR client configuration
- Parquet catalog internals
- Nautilus type system details
- Advanced engine configuration options
