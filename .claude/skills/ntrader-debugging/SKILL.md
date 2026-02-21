---
name: ntrader-debugging
description: >
  Use when diagnosing segfaults, test failures, data issues, or engine crashes.
  Covers LogGuard panics, C extension state leaks, common error messages with fixes,
  and debugging commands.
---

# NTrader Debugging Guide

## Segfault Diagnosis

If the process crashes with no Python traceback (just a segfault or abort), check in order:

1. **Missing `--forked`** in integration tests — Nautilus C extensions corrupt state across `fork()`
2. **LogGuard double-init** — two Nautilus components initialized logging independently
3. **C extension state leak** — engine reused after `run()` (single-use violation)

## LogGuard Panic

**Symptoms**: Process crashes with "already initialized" message, or silent segfault during `BacktestEngine` creation.

**Root cause**: Nautilus C/Rust logging subsystem was initialized twice. This happens when:
- `HistoricInteractiveBrokersClient` starts first (initializes logging)
- `BacktestEngine` is created later (tries to initialize logging again)

**Fix**:
```python
from src.utils.logging import set_nautilus_log_guard

# After creating first Nautilus component:
log_guard = client._log_guard  # or however the guard is exposed
set_nautilus_log_guard(log_guard)  # Stores at module level, persists for process
```

**Verify**: Check that `src/utils/logging.py:_nautilus_log_guard` is not `None` after first Nautilus component starts.

## Test Failures by Category

### Integration tests fail randomly
**Cause**: Missing `--forked` flag — C extension state leaks between tests.
**Fix**: Always use `make test-integration` (includes `--forked`). Add double `gc.collect()`:
```python
engine.dispose()
gc.collect()
gc.collect()
```

### Import errors in strategies
**Cause**: `StrategyRegistry.discover()` was not called before accessing strategies.
**Fix**: Ensure `StrategyRegistry.discover()` is called before `StrategyRegistry.get()`. The registry auto-discovers on first `get()` call, but explicit discovery is safer.

### "Strategy not found" error
**Cause**: Strategy file missing one or more registration steps.
**Checklist**:
1. `@register_strategy(name="...")` decorator on strategy class?
2. `StrategyRegistry.set_config("...", ConfigClass)` at module bottom?
3. `StrategyRegistry.set_param_model("...", ParamModel)` at module bottom?
4. Strategy file in `src/core/strategies/` (or `custom/`) directory?
5. No import errors in the strategy module? (check with `python -c "import src.core.strategies.<name>"`)

### BacktestEngine errors after run
**Cause**: Engine reused after `run()` — BacktestEngine is single-use.
**Fix**: Create a new `BacktestEngine` instance for each backtest run.

### "Venue not found" or "Instrument not found"
**Cause**: Engine setup sequence violated — venue must be added before instrument.
**Fix**: Follow strict order: config -> engine -> add_venue -> add_instrument -> add_data -> add_strategy -> run

## Data Issues

### Empty backtest results (0 trades)
**Causes** (check in order):
1. Wrong bar_type string format — must be `{SYMBOL}.{VENUE}-{STEP}-{TYPE}-{PRICE}-EXTERNAL`
2. Bar type mismatch between strategy config and loaded data
3. Insufficient data for indicator warmup (need at least `slow_period` bars)
4. Strategy parameters too restrictive (thresholds never triggered)

**Debug**: Add `self.log.info(...)` in the strategy's `on_bar()` to verify bars are being received.

### `catalog.bars()` returns nothing
**Cause**: Using BarType object instead of string list.
```python
# WRONG — returns ALL bar types (ignores filter)
bars = catalog.bars(bar_types=[BarType.from_str("...")])

# CORRECT — must be list of strings
bars = catalog.bars(bar_types=["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"])
```

### IBKR connection fails
**Checklist**:
1. IBKR Gateway running? (`docker compose up ibgateway`)
2. Environment variables set? Check via `IBKRSettings`:
   - `IBKR_HOST` (default: 127.0.0.1)
   - `IBKR_PORT` (default: 7497, typically 4002 for Gateway paper)
   - `IBKR_CLIENT_ID` (default: 10)
3. `.env` file loaded? (`load_dotenv()` called before settings access)
4. Port conflict? Only one client ID per connection.

### Parquet file corruption
**Symptoms**: `ArrowInvalid` or `ParquetException` during catalog read.
**Fix**: The `DataCatalogService` auto-quarantines corrupt files to `.corrupt/` directory. Re-fetch data from IBKR after quarantine.

## Common Error Messages

| Error | Cause | Fix |
|-------|-------|-----|
| "logger already initialized" / segfault | LogGuard double-init | Store guard via `set_nautilus_log_guard()` |
| "Strategy 'X' not found" | Missing registration | Check 4-file pattern (see strategy-development skill) |
| "Strategy 'X' not registered" | `set_config()` before `@register_strategy` | Ensure decorator comes first |
| "No data found for instrument" | Wrong bar_type format | Check `{SYM}.{VENUE}-{N}-{TYPE}-{PRICE}-EXTERNAL` |
| "Cannot reuse BacktestEngine" | Engine reused after run | Create new engine instance |
| Segfault in tests | Missing `--forked` | Use `make test-integration` |
| "Venue 'X' not found" | Setup order wrong | Add venue before instrument |
| NaN in metrics | No trades executed | Check indicator warmup and signal thresholds |

## Debugging Commands

```bash
# Run specific test with verbose output and stop on first failure
uv run pytest tests/unit/test_something.py -x -v

# Run with debugger on failure
uv run pytest tests/unit/test_something.py -x --pdb

# Run integration tests with forked processes (required)
make test-integration

# Check strategy registration
uv run python -c "
from src.core.strategy_registry import StrategyRegistry
StrategyRegistry.discover()
for s in StrategyRegistry.list_strategies():
    print(f'{s[\"name\"]}: {s[\"description\"]}')
"

# Check catalog availability
uv run python -c "
from src.services.data_catalog import DataCatalogService
svc = DataCatalogService()
for k, v in svc.availability_cache.items():
    print(f'{k}: {v.start_date} to {v.end_date} ({v.file_count} files)')
"

# Enable structlog debug level
LOG_LEVEL=DEBUG uv run python -m src.cli.main ...
```

## Key Source Files

- `src/utils/logging.py` — LogGuard implementation
- `src/core/backtest_runner.py` — Engine lifecycle errors
- `src/services/data_catalog.py` — Catalog query issues
- `src/core/strategy_registry.py` — Registration errors
