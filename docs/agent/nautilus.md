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
6. **The history/live seam** (Story 4.5, PO ruling B). A live bar the history
   already held (IB republishes bar X on X+1's first update) is dropped by the
   watch — `warmup.seam_duplicate_dropped` — and a bar published while the
   request was in flight, which the strategy never saw, is named, one
   `warmup.seam_gap` WARNING each, **never replayed**. The watermark comes from
   an instance-level `handle_bars` wrapper, not `cache.bar()`: `Cache.add_bars`
   keeps only history newer than a live bar already cached.

## Live Session Cache (Redis)

A live paper-trading session's engine state lives in Redis, namespaced per
session. `src/core/live_cache.py` builds the `CacheConfig`;
`src/core/live_trader_id.py` derives the `trader_id` that names the namespace
(`PAPER-<8 hex of session UUID>`). Keys land as
`trader-PAPER-a1b2c3d4:general:<key>`.

**Redis is a disposable cache. IBKR is authoritative.** It holds engine cache
state — orders, positions, accounts, instruments — all of which is rebuildable
from the broker. Flushing it loses no system of record: closed trades are in
PostgreSQL and session identity is in `trading_sessions`. A conflict between
cached state and broker state is detected and resolved at every session start
by the `reconcile` phase — see "Startup Reconciliation" below (Story 4.2, FR35,
AR25).

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

### Reading a session's engine cache (Story 4.6)

To read what a session *believes* it holds, from outside any node, call
`src.core.live_session_view.read_session_view(session_id, account, redis)`. It
returns a `src.models.reconciliation.SessionView` (net open quantity per
instrument, and the last `TotalCashValue` the session's engine received), or
raises `SessionViewUnavailableError`. This is the local side of `ntrader live
reconcile`. The `trades` table cannot be that side: it holds closed round trips
only, so a trades-derived position is always flat.

Each point below was measured against 1.220.0:

- **Load-only reads are write-free.** A fresh `CacheDatabaseAdapter` on the
  session's `trader_id`, calling only `keys` / `load_position` / `load` (plus
  `close()`; the set is pinned by a test), issues
  `SCAN`, `LRANGE`, `GET`, `MGET`, `INFO` and `CLIENT SETINFO`. The namespace
  stays byte-identical, and constructing the adapter does not initialise C
  logging. **Never** call an adapter `add*` / `update*` / `delete*` /
  `heartbeat` / `snapshot*` / `index_*` on a session's namespace. `flush()` is
  `FLUSHDB` on the *whole* database.
- **An unreachable Redis hangs the adapter's constructor forever.** Run
  `check_redis_reachable` first.
- **`load_positions()` silently drops a position whose instrument key is
  missing.** `load_position` returns `None`, and the dict just lacks it. The
  reader enumerates `keys("positions:*")` itself and fails loudly instead.
- **Replay is correct across reuse of a position id.** A NETTING close then
  reopen, or a flip, appends to one fill list. `Position.apply` resets at FLAT,
  so the replay ends on the current leg.
- **Nautilus's Rust-side `load_all()` returns no positions** for data the Python
  adapter wrote. That is a silent flat, so do not use it.
- **The serializer needs `msgspec`.** The reader builds `MsgSpecSerializer`
  exactly as `NautilusKernel` does. `msgspec` is declared in `pyproject.toml`
  for this reason; it was already a hard requirement of `nautilus-trader`.
- **The summary key embeds the raw account** (`general:accountSummary:<acct>`).
  Never log a key name. The account there, and on every position, is the
  builder's normalised form (`TWS_ACCOUNT.strip().upper()`), so look it up the
  same way.
- **`SCAN` may return a key twice.** Dedupe position keys before netting;
  Nautilus's own `load_positions` dedupes by id.
## Startup Reconciliation (Story 4.2)

Nautilus's own startup reconciliation stays switched on
(`EXEC_ENGINE_RECONCILIATION = True`) and runs **inside `node:connect`**:
`start_async` does engines-connected → reconcile → emulator → portfolio →
`trader.start()` in one coroutine (`system/kernel.py:1008-1027`), and returns
early, leaving the trader unstarted, if it reports failure. The `reconcile` phase
(`src/core/live_startup_reconcile.py`) therefore **verifies and completes** that pass,
after `gate:account` and before any strategy exists. It never re-runs it.

Four measured facts (1.220.0, Story 4.2 Task 1) that shape it:

1. **The framework's "Execution state reconciled" proves nothing about a flat
   instrument.** Its sweep of cached positions filters by the IB client's venue
   (`INTERACTIVE_BROKERS`), while positions carry the exchange (`NASDAQ`), and
   the IB adapter's position report ignores its instrument filter and skips zero
   quantities. A position cached on an instrument the broker is flat in is never
   touched, and the pass still reports success. That is the `p7-fill-0901`
   phantom. So the phase compares the cache against `read_broker_state`, never
   against the framework's log line.
2. **Every framework correction is attributed to a synthetic owner.** An order
   the broker reports that the cache lacks becomes `EXTERNAL`, and a net
   correction becomes `INTERNAL-DIFF`. Under NETTING the correcting fill lands
   in that owner's position (`{instrument}-{strategy}`), **never a strategy's**.
   Under Nautilus's defaults a normal mid-position restart therefore leaves three
   open positions (strategy +10, `EXTERNAL` +10, `INTERNAL-DIFF` −10), because
   the IB adapter fabricates a `FILLED` order report per broker position. Since
   Story 4.5 the session's engine runs with `filter_unclaimed_external_orders=True`
   (enforced by the phase), so the fabricated order is dropped and the restart
   leaves only the strategy's own position — see "Resuming Mid-Position" below.
3. **Without the broker's average price, a correcting fill is priced 0.** The
   phase always passes `avg_px_open` when IBKR reported one.
4. **`reconcile_execution_report` returns `True` for an instrument excluded by
   `reconciliation_instrument_ids`**, having done nothing. That is why the phase
   re-reads and re-compares after correcting.

The phase's rules:

- **Internal position state** is the cache's net signed quantity per
  instrument, over every open position, compared exactly with the broker's.
- **A strategy's own position that the broker does not cover refuses the
  start.** This is the PO's 2026-09-27 ruling, narrowed by Story 4.7's
  coverage rule (PO, 2026-09-28): the broker holds fewer shares in the
  strategy's direction, none, or the opposite side. The framework cannot
  rewrite it (fact 2), and a started strategy would act on it: `sma_crossover`
  closes it, which is a real order against the broker. The refusal names the
  likely cause. A strategy position the broker holds *more* of is not refused
  — see "Corporate actions" below — but the excess is unowned, and Story 4.5's
  per-strategy resume check (PO ruling 2026-09-28) refuses the strategy on that
  instrument; see "Resuming Mid-Position". That check's startup exemption also
  lets a covered row through when strategies hold both sides of one instrument.
- **Anything else is corrected broker-ward** through
  `exec_engine.reconcile_execution_report(PositionStatusReport)`, and must then
  re-compare clean.

The records are `reconcile.discrepancy` (`resolution` is `framework`, `broker`
or `refused`) and `reconcile.ok`. A refusal is `ReconciliationFailedError`,
exit 1.

**Operator remedy for a refused start** (`strategy_position_contradicted`):
create a new session for the strategy. The alternative is clearing the
session's engine cache. That cache is disposable by architecture (D2): delete the
session's `trader-PAPER-<id>:*` keys with the process stopped. Doing so also
loses the restored client-order-id counter (Story 3.4), so prefer a new session.
Never use `flush()`, which is `FLUSHDB`.

## Runtime Reconciliation (Story 4.3)

Nautilus 1.220.0 has **no continuous position reconciliation**. Its one
continuous task (`_continuous_reconciliation_loop`, created in
`LiveExecutionEngine._on_start`) runs the **in-flight sweep** — on for the whole
session since Story 3.4 — and, only if `open_check_interval_secs` is set, an
open-order consistency check. Neither ever compares positions again after
startup. So runtime alignment is split three ways:

1. **Orders — native.** The in-flight sweep queries the venue for a
   `SUBMITTED`/`PENDING_*` order gone quiet and resolves it locally; it never
   resubmits. The **open-order check stays off** (PO ruling, 2026-09-27): with
   it on, a cached order the sweep already cancelled locally, which IB still
   lists open, is never corrected — the engine republishes an `OrderAccepted`
   for it on every check, forever (`execution/engine.pyx:1165-1178` publishes
   even after `apply` fails). The measured reason lives in
   `live_node_builder.py`'s comment; a canary in
   `test_live_runtime_reconcile_engine.py` pins it.
2. **The IB adapter's own position-update reports — switched off**
   (`src/core/live_exec_position_reports.py`, installed by the exec-client
   factory beside the avg-px fix). The adapter reports any broker position that
   differs from its own `_known_positions` straight to the engine. A flat
   instrument is untracked, so the broker's `0 → 22` update arriving before the
   strategy's own `execDetails` is "external"; the `execDetails` then makes the
   tracked quantity 44 and the next `22` is reported again. That is the P11/P12
   phantom `INTERNAL-DIFF` round trip on **every** entry. It also returns early
   on quantity 0, so it never reports a position going flat.
3. **Positions — a verified cycle of ours, resolving through the framework**
   (`src/core/live_runtime_reconcile.py`, driven by the heartbeat tick). Every
   2 ticks (60 s) while `CONNECTED`: snapshot the cache, read the broker (Story
   4.1's reader, its own per-read records dropped), **skip** the cycle if the
   cache moved during the read, compare exactly, **defer** instruments with an
   order still in flight, and act only on a disagreement seen **identically on
   two checks at least 60 s apart**. A net disagreement is corrected broker-ward
   through `reconcile_execution_report` (the Story 4.2 path) and logged
   `reconcile.discrepancy scope=runtime resolution=broker`. An instrument the
   cache cannot express is logged `resolution=unresolved` once. A broker row
   the adapter could not resolve (`IB-CONID-*`) holds back only the cache row
   with the **same symbol** — the one holding it could be — and only for
   `HOLD_BACK_CYCLES` (3) consecutive cycles; past that it is a holding the
   adapter will never resolve (`reconcile.hold_back_expired`, once) and the
   held-back row is acted on, so a contradiction underneath it is refused rather
   than masked for the life of the session (PO ruling, PR #35 D1). A strategy's
   own position the broker no longer covers **stops the session** before
   anything is written (PO ruling 2A, narrowed by Story 4.7's coverage rule) —
   `ReconciliationFailedError` out of the tick, the ordinary teardown,
   positions untouched, exit 1, the likely cause named. The same happens when
   the framework refuses a correction or one does not take.

**Latency, accepted:** a change the execution stream did not explain (a manual
TWS trade, a split) is corrected one to two cycles after it happens, not on
arrival. `reconcile.ok scope=runtime` is logged on the first clean cycle, after
any problem, and otherwise at most hourly (`cycles` says how many it covers).

**Trading permission** (`ConnectionMonitor`): granted at the end of the
startup `reconcile` phase, and after a loss **only** by a clean reconnect cycle
(`scope=reconnect`, every tick while `RECOVERING`, no order in flight) followed
by `confirm_state_reestablished` — the one production caller is
`live_runtime_reconcile`. `submission_withheld` is `not trading_permitted`, so
`RECOVERING` withholds every order. A reconnect whose state cannot be verified
stays withheld; past the 60 s window the monitor logs `connection.halted`, and a
later clean cycle still restores (`connection.restored`).

## Resuming Mid-Position (Story 4.5)

A session stopped while holding a position leaves it at IBKR (Story 3.1). On the
next `live start` the strategy's own position comes back from the session's Redis
namespace — same position id, same fills, same commissions — and the `reconcile`
phase proves its side and quantity equal the broker's, or refuses the start. The
strategy then acts on it: the opposite crossover exits it, a same-side one enters
nothing, and the round trip closes as **one** trade in the same `session_id`.
Four rules make that true:

1. **The broker is imported once** (D-A). `EXEC_ENGINE_FILTER_UNCLAIMED_EXTERNAL_ORDERS
   = True` (`live_node_builder.py`) drops the IB adapter's fabricated per-position
   `FILLED` order, so no `EXTERNAL` copy appears beside the strategy's position.
   A holding the cache cannot attribute (a manual trade, a lost cache) is imported
   once, as `INTERNAL-DIFF`, at the broker's average price. Trade-off: the switch
   holds for the engine's whole life, so a working order the cache never knew is
   not imported at startup or later (a manual TWS order has no `orderRef` and never
   reached the engine anyway); a strategy's own working orders still come back from
   Redis (AR25).
2. **Built-in strategies act on their own book only** (D-B). `sma_crossover` reads
   `cache.positions_open(instrument_id=..., strategy_id=self.id)`; `momentum` nets
   the same set instead of reading the portfolio, and sizes its exits and flips
   from that net, not `trade_size`. Backtests run one strategy per instrument, so
   their fills are unchanged (the Story 4.4 parity fingerprint). **A strategy's id
   is a pure function of the frozen spec:** the runner materialises each one with
   the `order_id_tag` its spec position resolves (`SessionSpec.order_id_tags`),
   never the one `Trader.add_strategy` would count from the strategies actually
   added — so a refused or failed earlier entry cannot renumber a sibling and
   orphan its restored position.
3. **A holding no strategy owns refuses only that instrument's strategy** (D-C,
   PO ruling B). `strategy.resume_refused` names the instrument, the unowned
   quantity, the strategies' own quantity and the broker's quantity; the strategy
   is contained through the start-failure path, its siblings start, and a session
   where none can fails closed (exit 1). The holding is **never** traded, flattened
   or resized — the remedy is manual, in TWS. Two amendments from PR #35's code
   review (PO rulings, 2026-09-30):
   - **Covered growth resumes beside the excess (D5a).** When the strategy's own
     position is non-zero and the unowned part is on the *same side* (+10 owned,
     IBKR +15 — a manual add, or a forward split while stopped), the strategy is
     started and resumes managing its own lot; the excess stays `INTERNAL-DIFF`,
     never traded, and one `strategy.resumed_beside_excess` WARNING names both
     quantities. That is the shape the running session already absorbs with the
     strategy left running (Story 4.7's coverage rule, below), so a stop no
     longer makes it stricter. A flat strategy beside an unowned holding, one on
     the opposite side, and strategies on **both** sides of the instrument are
     still refused — those are the FR38 and NFR14 shapes.
   - **A stale own-id is unowned (D4).** A position under a strategy id this
     session's spec does not resolve to (one a pre-fix run left under an id an
     earlier refusal renumbered) belongs to no strategy that will start; it is
     judged as above, so the entry that resolves to the new id cannot read its
     own book as flat and enter beside it.
   At `reconcile`, IBKR holding less, nothing, or the opposite side still refuses
   the whole session, and the running session's cycle still stops on any of them.
4. **A pre-4.5 namespace is refused before the framework runs** (D-D). One that
   cached the fabricated order would abort the process inside `node:connect`
   after a shrink (a Rust panic, uncatchable). `session.resume_refused
   reason=legacy_position_import`, exit 1: create a new session.

A strategy that restarts holding its own position logs `strategy.resumed`
(position id, quantity, `broker_quantity`, `ts_opened`, `opened_before_this_run`,
its own `open_orders`) after `reconcile.ok` and before its `warmup.completed`, so
its first live decision follows both. The operator procedure is P19
(`docs/qa/phase3-live-verification.md`).

## Corporate actions (Story 4.7)

IBKR's account is the truth for positions and cash (FR35). A split or a
dividend over a multi-week session changes that truth **underneath** the
session: IBKR reports no execution for it, and Nautilus 1.220.0 has no
corporate-action or position-adjustment event. Reconciliation absorbs the change
and names it, and never silently corrects it.

**Positions: the coverage rule** (PO ruling, 2026-09-28). A strategy's own
position is refused only when the broker does not *cover* it:

| Broker vs the strategy's own net | Typical cause | What happens |
|---|---|---|
| more, same side (+10 → +20) | forward split, stock dividend, a manual add in TWS | **absorbed**: corrected broker-ward, `reconcile.discrepancy` names the before and after quantities; at a start, the strategy resumes beside the excess (`strategy.resumed_beside_excess`, PR #35 D5a) |
| fewer, same side (+10 → +5) | reverse split, a partial sale outside the session, a lost fill | **refused**: the start is refused, or a running session is stopped |
| none (+10 → 0) | cash merger, symbol change, a close outside the session | **refused** |
| the opposite side (+10 → −5) | a trade outside the session | **refused** |
| strategies on **both** sides of one instrument | — | the pre-4.7 rule: refused unless the strategies' net equals the broker's exactly. At a start a covered row passes `reconcile` (logged `resolution=covered`, PR #35 D5b) and D-C then refuses each strategy on the instrument; a running session stops on it |

- **Why covered growth is safe, and where that stops.** When a strategy closes
  **its own lot**, the broker is left at `broker − strategy`, on the broker's
  own side, so *that* close can never carry the account through zero into a
  position nobody asked for (NFR14). That hazard is the whole reason an
  uncovered strategy is refused.
  - With strategies on both sides of one instrument, their net says nothing
    about each lot, so the equality rule stays (the last table row).
  - **At startup, the strategy resumes beside the split's shares** (PR #35 D5a,
    PO ruling 2026-09-30; from the 4.5/4.7 integration merge until then it was
    refused). A split absorbed at startup leaves strategy +10 beside
    `INTERNAL-DIFF` +10 — the engine no longer imports the adapter's `EXTERNAL`
    order — and the +10 belongs to no strategy. It is same-side growth beside
    the strategy's own lot, so the per-strategy resume check starts the
    strategy and names the excess once (`strategy.resumed_beside_excess`; see
    "Resuming Mid-Position"); the strategy manages its own 10 and the split's
    10 stay at IBKR, unowned and never traded, until removed by hand in TWS.
    (Before 4.5 this was the triple strategy +10, `EXTERNAL` +20,
    `INTERNAL-DIFF` −10, on which `sma_crossover`'s next SELL closed 30 against
    a broker at 20.)
  - A split absorbed *mid-session* leaves the same shape — strategy +10 beside
    `INTERNAL-DIFF` +10 — and the strategy keeps running. Since Story 4.5 the
    built-ins act on their own book only, so `sma_crossover` sells its own 10
    and the split's 10 stay at IBKR, unowned; the next start resumes beside
    them exactly as above.
- **How it is absorbed.** Nautilus's reconciliation writes the difference as a
  fill on a synthetic owner (`EXTERNAL` / `INTERNAL-DIFF`), exactly as for any
  net correction. The strategy's own lot is **never** adjusted, closed or
  flattened, and no zero-price adjustment fill is ever written for it.
  - At startup, Nautilus's own pass inside `node:connect` usually imports the
    split. The `reconcile` phase then names it from the pre-run snapshot:
    `resolution=framework`, with `local_quantity` = before and
    `broker_quantity` = after.
  - While the session runs, the minute cycle corrects it after its 60 s
    debounce: `resolution=broker`, `scope=runtime`.
  - The synthetic fill is priced so that the **combined** average equals the
    broker's: `(target·avg − current·avg) / difference`
    (`live/reconciliation.py`).
    - IBKR's average includes commission, so a long split solves to a small
      positive price (about 0.10).
    - An exact split solves to `0`, and a commission-inclusive short solves
      negative. `calculate_reconciliation_price` rejects both, and the fill
      falls back to the current average price (measured, both sides).
    - That fill price only ever lands on the synthetic position, and the
      combined cost basis still equals IBKR's.
- **What a refusal says.** `reconcile.discrepancy resolution=refused` carries
  `likely_cause`, and the operator message names it: "the broker holds fewer
  shares than the strategy believes — a reverse split, …". When the net already
  matches the broker, it also names this session's own orders against a
  reconciliation-owned position. That case arises when a strategy that reads
  the net sells a split's extra shares: the refusal is then not an outside
  event. Neither built-in does since Story 4.5 (`sma_momentum` read the net
  before it). The remedy is unchanged: create a new session (see
  "Startup Reconciliation" above). Only the contradicted rows are `refused`
  (ERROR, and alone in the exit-1 message); every other row a refused start
  found disagreeing is logged `resolution=untouched` at WARNING — named, not
  corrected, because the phase stopped before correcting anything.

**Costs of absorbing, accepted by the PO:**
- **A lot held across a split records its round trip at unadjusted prices.**
  Entry is before the split and exit after it, so a 2:1 split reads as a ~50 %
  loss on the strategy's lot.
- **The split's extra shares sit in a reconciliation-owned position.** The trade
  recorder never persists those (Story 3.6 D-D), so their gain is never recorded
  as a trade.
  - A strategy that exits only its own lot leaves them at IBKR, visible in
    `reconcile.ok` (`instruments`, `synthetic_positions`) and in
    `live reconcile`. Since Story 4.5 both built-ins exit only their own lot.
  - While they are held, the next start does not start the strategy on that
    instrument (Story 4.5's per-strategy resume check, above) until they are
    removed by hand in TWS.
- **Not corporate-action specific.** A manual same-direction add in TWS is
  absorbed the same way. The record names it either way.
- **The neighbouring hazards were Story 4.5's**, and it has closed them:
  - the three-position state a mid-position restart left (strategy +10,
    `EXTERNAL`, `INTERNAL-DIFF`) — the engine no longer imports the `EXTERNAL`
    order (D-A);
  - `sma_crossover` reading every strategy's positions on its instrument and
    closing synthetic ones too — both built-ins read their own book (D-B);
  - a restart after the broker position **shrank**, which could abort the
    process inside Nautilus's own pass (a reverse split can reach it) — new
    namespaces never cache the order behind it, and one that did is refused
    before the framework runs (D-D).

  See "Resuming Mid-Position" above and `deferred-work.md`.

**Cash: absorbed natively, named at startup.** The session's cash *is* IBKR's
account-summary push, so no local copy can drift while the process runs. A
dividend or interest credited while the session was stopped moves the cash
between runs.
- The pre-run snapshot captures the previous run's last `TotalCashValue` and
  when IBKR last reported it. It reads existing Redis state only: the restored
  `accountSummary:<acct>` general key, and the account's last *reported*
  `AccountState`. There is no new column and no migration.
- The `reconcile` phase then logs one INFO `reconcile.cash_changed` per currency
  that moved, with `before`, `after`, `difference` and `recorded_at`.
- It is informational: it never refuses a start, never blocks the phase, and
  never touches trading permission.
- **What "before" is, exactly.** It is the last summary IBKR *pushed* to the
  previous run, at `recorded_at`, and IBKR pushes only every few minutes.
  - The previous run's own last fills and commissions can therefore show up in
    the difference, as well as a dividend, interest, a fee or another client's
    trade. Compare `recorded_at` with the run's last fill before reading it as a
    corporate action.
  - A start that fails *after* connecting (a `gate:account` refusal, a failed
    broker read) has already let the first push overwrite the stored summary,
    so the next start names nothing.
  - `live reconcile` on the stopped session, before starting it, is the reliable
    view.
- `live reconcile` says the same thing on demand: "session cash as of <time>".
  An account whose time cannot be read is logged
  `reconcile.session_view_account_unreadable` and the time reads as unknown.
  The check is not failed.

**Price basis: an expected structural property of the comparison, not a defect
to chase.** Backtest data is
FirstRate: split- *and* dividend-adjusted, consolidated. Live data is IBKR:
**unadjusted**, SMART-routed. The backtest never sees a corporate action as an
event, because it is pre-baked into adjusted prices. A live session sees:
- a price cliff in its bars on the ex-date (a split halves the price; an SMA
  crossing it may fire);
- the position-size change above;
- the unadjusted P&L of a lot held across it.

So a live session and its backtest diverge by construction around every
corporate action. That divergence is **an expected structural property of the
comparison, not a defect to chase**: expected and attributable. Epic 5 records
it on every sealed session as
`session_conditions.price_basis = "ibkr_unadjusted_smart"`, against a backtest's
`"firstrate_adjusted"` (Story 5.4, architecture AR7). Read a live-vs-backtest
difference near a corporate action against that property first.
