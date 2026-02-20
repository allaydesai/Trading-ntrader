# Nautilus Trader Notes

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

## C Extension Isolation

Nautilus uses C/Rust extensions that don't survive `fork()` cleanly. This causes:
- Segfaults in child processes
- Corrupted global state across tests

**Fix**: Integration tests run with `--forked` (pytest-forked) so each test gets a fresh
process. See `make test-integration`.

## BacktestEngine Lifecycle

`BacktestEngine` is **single-use** — it cannot be reset or reused after a run completes.
Create a new engine instance for each backtest. The `backtest_runner.py` handles this.

## Parquet Data Catalog

Market data lives in Parquet files under the Nautilus catalog structure.
`src/services/data_catalog.py` and `data_service.py` handle reading/writing.
CSV data is converted via `csv_loader.py` → `nautilus_converter.py` → Parquet.

## IBKR Client

`src/services/ibkr_client.py` connects to Interactive Brokers TWS/Gateway.

**All connection settings come from environment variables** via `src/config.py:IBKRSettings`:
- `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID`
- `TWS_USERNAME`, `TWS_PASSWORD`, `TWS_ACCOUNT`
- `IBKR_TRADING_MODE` (paper/live), `IBKR_READ_ONLY` (default: true)

Rate limit: 45 req/s (90% of IBKR's 50/s hard limit).
Never hardcode IBKR connection values — always use env vars through `IBKRSettings`.

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
