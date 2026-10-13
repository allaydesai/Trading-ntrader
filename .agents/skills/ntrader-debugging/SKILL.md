---
name: ntrader-debugging
description: >
  Use when diagnosing segfaults, test failures, data issues, or engine crashes.
  Covers LogGuard panics, C extension state leaks, common error messages with fixes,
  and debugging commands.
---

# NTrader Debugging Guide

> See also: `docs/agent/nautilus.md` (LogGuard, C extensions, engine lifecycle)

## Segfault Diagnosis

If the process crashes with no Python traceback, check in order:

1. **Missing `--forked`** in integration tests — use `make test-integration`
2. **LogGuard double-init** — store guard via `set_nautilus_log_guard()` (see `docs/agent/nautilus.md`)
3. **C extension state leak** — engine reused after `run()` (single-use violation)

## Test Failures by Category

### Integration tests fail randomly
**Fix**: Always use `make test-integration` (includes `--forked`). Add double `gc.collect()`.

### "Strategy not found" error
**Checklist**:
1. `@register_strategy(name="...")` decorator on strategy class?
2. `StrategyRegistry.set_config("...", ConfigClass)` at module bottom?
3. `StrategyRegistry.set_param_model("...", ParamModel)` at module bottom?
4. Strategy file in `src/core/strategies/` (or `custom/`) directory?
5. No import errors? (check with `python -c "import src.core.strategies.<name>"`)

### Empty backtest results (0 trades)
Check in order:
1. Wrong bar_type format — must be `{SYMBOL}.{VENUE}-{STEP}-{TYPE}-{PRICE}-EXTERNAL`
2. Bar type mismatch between strategy config and loaded data
3. Insufficient data for indicator warmup (need at least `slow_period` bars)
4. Strategy parameters too restrictive (thresholds never triggered)

### `catalog.bars()` returns nothing
```python
# WRONG — returns ALL bar types (ignores filter)
bars = catalog.bars(bar_types=[BarType.from_str("...")])

# CORRECT — must be list of strings
bars = catalog.bars(bar_types=["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"])
```

## Common Error Messages

| Error | Cause | Fix |
|-------|-------|-----|
| "logger already initialized" / segfault | LogGuard double-init | Store guard via `set_nautilus_log_guard()` |
| "Strategy 'X' not found" | Missing registration | Check 4-file pattern |
| "No data found for instrument" | Wrong bar_type format | Check `{SYM}.{VENUE}-{N}-{TYPE}-{PRICE}-EXTERNAL` |
| "Cannot reuse BacktestEngine" | Engine reused after run | Create new engine instance |
| Segfault in tests | Missing `--forked` | Use `make test-integration` |
| "Venue 'X' not found" | Setup order wrong | Add venue before instrument |
| NaN in metrics | No trades executed | Check indicator warmup and signal thresholds |

## Debugging Commands

```bash
uv run pytest tests/unit/test_something.py -x -v       # Verbose, stop on first failure
uv run pytest tests/unit/test_something.py -x --pdb     # Debugger on failure
make test-integration                                    # Forked processes (required)
LOG_LEVEL=DEBUG uv run python -m src.cli.main ...       # Debug logging
```

## Key Source Files

- `src/utils/logging.py` — LogGuard implementation
- `src/core/backtest_runner.py` — Engine lifecycle errors
- `src/services/data_catalog.py` — Catalog query issues
- `src/core/strategy_registry.py` — Registration errors
