---
name: nautilus-engine-patterns
description: >
  Use when running backtests, debugging engine crashes, working with BacktestEngine lifecycle,
  or encountering segfaults and LogGuard panics. Covers engine setup sequence, result extraction,
  and C extension isolation.
---

# Nautilus Engine Patterns

> Full reference: `agent_docs/nautilus.md`

## Quick Reference

- **LogGuard**: Store via `set_nautilus_log_guard()` in `src/utils/logging.py`. Never let it go out of scope. See `agent_docs/nautilus.md` for details.
- **BacktestEngine is single-use**: Cannot reuse after `run()` — create a new instance each time.
- **C extensions**: Integration tests need `--forked`. Always double `gc.collect()` after engine disposal.

## Engine Setup Sequence (Strict Order)

Violations cause cryptic errors. Must follow this exact order:

```python
config = BacktestEngineConfig(trader_id=TraderId("BACKTESTER-001"))
engine = BacktestEngine(config=config)

# 1. Add venue FIRST
engine.add_venue(venue=venue, oms_type=OmsType.HEDGING,
    account_type=AccountType.MARGIN, starting_balances=[Money(1_000_000, USD)],
    fill_model=fill_model, fee_model=fee_model)

# 2. Add instrument (venue must exist)
engine.add_instrument(instrument)

# 3. Add data
engine.add_data(bars)

# 4. Add strategy (last)
engine.add_strategy(strategy=strategy)

# 5. Run
engine.run()
```

## Result Extraction

```python
analyzer = engine.portfolio.analyzer
stats_returns = analyzer.get_performance_stats_returns()
stats_pnls = analyzer.get_performance_stats_pnls(currency=USD)

# Key metrics
sharpe = stats_returns.get("Sharpe Ratio (252 days)")
sortino = stats_returns.get("Sortino Ratio (252 days)")
profit_factor = stats_returns.get("Profit Factor")
total_pnl = stats_pnls.get("PnL (total)")

# Account and trades
account = engine.cache.account_for_venue(venue)
final_balance = float(account.balance_total(USD).as_double())
closed_positions = engine.cache.positions_closed()
positions_report = engine.trader.generate_positions_report()
```

## Venue Configuration

```python
venue = Venue("SIM")  # mock data
venue = bars[0].bar_type.instrument_id.venue  # real data (e.g., "NASDAQ")
```

The venue must match between instrument, bars, and engine setup.
