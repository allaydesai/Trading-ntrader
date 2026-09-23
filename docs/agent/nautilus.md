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

### Warming indicators from history (Story 4.4)

Both built-ins warm the same, mode-agnostic way (AR40 — never `if self.is_live:`):
`on_start()` calls `register_indicator_for_bars(...)` for each indicator, then
`request_bars(bar_type, start=now - warmup_lookback(bar_type, period), callback=...)`;
the callback records the crossover baseline and calls `subscribe_bars` **last**.
`tests/unit/strategies/test_strategy_warmup_shape.py` enforces that shape. Measured
facts (`nautilus-trader 1.220.0`) that bite:

1. **A registered indicator is fed by `Actor.handle_bar`, before `on_bar`.** Feeding
   it again inside `on_bar` counts every bar twice; calling `on_bar` directly in a
   test no longer moves it — drive `handle_bar`.
2. **History reaches `on_historical_data`, never `on_bar`**, so crossover state
   (`_prev_*`) has to be set in the callback or the strategy is warm but deaf for a bar.
3. **In a backtest the request is answered with nothing, synchronously**, inside
   `on_start` (no `DataEngine` catalog is registered) — backtests behave exactly as
   before. A test harness with **no** `DataEngine` behind its bus never answers at
   all, and the strategy never subscribes: give it one.
4. **Live, the IB adapter can finish a request without ever calling the callback**
   (missing contract, empty or timed-out history, a same-second duplicate). The
   runner's `WarmupWatch` (`src/core/live_session_warmup.py`) bounds that wait and
   contains the strategy (`warmup.failed`) instead of leaving it silent.
5. **Ask in whole days for `>= 1 minute` bars.** The adapter sends a sub-day span as
   seconds, and a seconds window ending pre-open returns nothing — see
   `src/core/strategy_warmup.py`.

## Live Session Cache (Redis)

A live paper-trading session's engine state lives in Redis, namespaced per
session. `src/core/live_cache.py` builds the `CacheConfig`;
`src/core/live_trader_id.py` derives the `trader_id` that names the namespace
(`PAPER-<8 hex of session UUID>`). Keys land as
`trader-PAPER-a1b2c3d4:general:<key>`.

**Redis is a disposable cache. IBKR is authoritative.** It holds engine cache
state — orders, positions, accounts, instruments — all of which is rebuildable
from the broker. Flushing it loses no system of record: closed trades are in
PostgreSQL and session identity is in `trading_sessions`. Nothing currently
*detects or resolves* a conflict between cached state and broker state;
startup reconciliation is Epic 4 (FR35, AR25).

Four things that bite:

1. **An unreachable Redis hangs forever, it does not raise.**
   `CacheDatabaseAdapter.__init__` blocks indefinitely and
   `DatabaseConfig(timeout=...)` does not bound it. `NautilusKernel` builds that
   adapter eagerly, so `TradingNode(config=...)` inherits the hang. Always call
   `check_redis_reachable()` first — `build_trading_node()` already does.
2. **Three `CacheConfig` fields decide the namespace, and all three are silent
   defaults**: `use_trader_prefix=True`, `use_instance_id=False`,
   `flush_on_start=False`. `use_instance_id=True` sends every restart to a new
   namespace (it is a fresh UUID4 per process); `flush_on_start=True` wipes it.
   `build_cache_config()` passes all three explicitly — do not "tidy" them away.
3. **Writes are asynchronous.** `add()` then `keys()` on the same open adapter
   returns `[]`. `close()` is what makes a write readable.
4. **`flush()` is `FLUSHDB`** — it clears the whole database, not the trader's
   namespace. Never call it in a test against a developer's Redis.

`DatabaseConfig` at 1.220.0 has no database-index field, so `REDIS_DB` cannot be
honoured; `RedisSettings` refuses any non-zero value rather than ignoring it.

## Reading Broker State (Story 4.1)

To ask IBKR what the account actually holds, call
`await src.core.live_broker_state.read_broker_state(node)` on the node's loop. It
returns a `src.models.broker_state.BrokerState` (positions, and cash as
`TotalCashValue`), or raises `BrokerStateUnavailableError` with a reason. A flat
account is `positions == ()`, which is a success; a failure never returns a state.

Do **not** reach for the obvious Nautilus APIs. Each of these was measured against
1.220.0:

- **`get_positions` / `generate_position_status_reports` cannot tell flat from failed.**
  The adapter's `get_positions` returns `None` for a timeout, a lost connection
  *and* an empty account. `generate_position_status_reports` turns all three into
  `[]`. `reconcile_execution_state` counts a client whose mass status raised as
  reconciled. So "Execution state reconciled" proves nothing about positions. The
  reader classifies IBKR's answer from the adapter's own `OpenPositions` request
  future instead: a list is `positionEnd`, and `ConnectionError` is a dropped
  socket. A *cancelled* future means some awaiter's `wait_for` gave up on the
  request: normally the adapter's own 30 s timeout, though any cancelled awaiter
  has the same effect. Either way IBKR's answer never arrived. A cancelled
  awaiter also leaves the dead request registered, so every later
  `get_positions` fails immediately (`deferred-work.md`, story-4.1 review).
- **`cache.account(...).balance_total()` is not cash.** The IB adapter sets
  `total = NetLiquidation`. When maintenance margin exceeds half of net
  liquidation it substitutes a literal `400000` (`# TODO: Bug`). Cash is the
  `TotalCashValue` tag in the exec client's in-memory `_account_summary`. The
  `accountSummary:<acct>` general-cache key holds the same data, but it is
  Redis-persisted, so after a restart it serves the previous process's values.
- **Never `wait_for` an adapter request you might share.** Cancelling
  `get_positions` cancels the `OpenPositions` future that Nautilus's own
  reconciliation may also be awaiting. Its joiner then gets `CancelledError`, a
  `BaseException` that `generate_mass_status`'s `except Exception` does not
  catch. Wait with `asyncio.wait(..., timeout=...)` and leave the request alone.
- **`provider.get_instrument(contract)` raises `ValueError`** for a contract it
  cannot resolve. The reader keeps such a position as `IB-CONID-<conId>` rather
  than dropping it, and does the same if a provider ever returns no instrument.
- **ibapi's "unset" sentinels arrive as values.** `UNSET_DOUBLE` is
  `sys.float_info.max`, and the decoder returns `UNSET_DECIMAL` (`2**127-1`) for
  an empty field. The reader treats an unset average cost or cash value as
  absent, and refuses an unset quantity.

The operator surface is `scripts/diagnostics/live_node_probe.py
--read-broker-state` (Procedure P15).
