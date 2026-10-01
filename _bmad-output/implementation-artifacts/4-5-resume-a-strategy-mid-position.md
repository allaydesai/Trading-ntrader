# Story 4.5: Resume a Strategy Mid-Position

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a restarted strategy to know it is already long and to keep hunting the same exit,
so that a multi-day swing trade survives my daily restarts without leaving a trace in the trade
record.

## Why this story is shaped the way it is

Read this before designing anything. The epic text makes it sound as if Stories 4.2 and 4.4
already did the work and 4.5 only has to prove it. That is **false today**, in a way that places
real orders. Measured by Story 4.2 against the installed `nautilus-trader 1.220.0`, re-read at
drafting (2026-09-28, head `a42cfc3`). Wheel paths are relative to
`.venv/lib/python3.11/site-packages/nautilus_trader/`; ripgrep skips `.venv` (gitignored), so use
`grep -n` on explicit paths.

**1. Every mid-position restart leaves the strategy beside two synthetic positions, and both
built-ins act on all three.** The strategy's own position does survive: Redis restores it, and
Story 4.2 proves its quantity against the broker. But the IB adapter *fabricates* a `FILLED` order
report for every broker position (F1). The cache does not know that order, so Nautilus imports it
as `EXTERNAL +10`. Its position pass then sees net 20 against the broker's 10 and adds
`INTERNAL-DIFF −10`. Net is right, so 4.2's check passes (`synthetic_positions=2`).
- `sma_crossover` reads `cache.positions(venue, instrument_id)` with **no strategy filter** and
  `close_position()`s every opposite-side open one (F4). On the next bearish crossover it sells
  its own 10 **and** `EXTERNAL`'s 10, leaving the broker short 10. On a bullish one it "closes"
  `INTERNAL-DIFF −10`, buying 10 more.
- `sma_momentum` reads the portfolio's net, which counts synthetics too.

Either way this is an order the strategy did not mean to place, caused by the restart (NFR14).
Today the resume journey *is* the hazard.

**2. The same fabricated order later aborts the process.** Suppose a later restart finds the broker
position smaller than the fabricated order it imported earlier (sizing moves with price, so an
ordinary exit and re-entry does this). Nautilus calls `_generate_order_updated`, `leaves_qty`
underflows, and a Rust panic kills the process inside `node:connect`, before any phase of ours runs
(F2). Python cannot catch it, and the row stays `running` until its heartbeat goes stale. This is
Story 4.2's HIGH item, routed here.

**3. The framework can drop that fabricated order, but not one already cached.** It ships the
switch for this (F3): `LiveExecEngineConfig.filter_unclaimed_external_orders=True` makes
`_generate_order` return `None` for an unclaimed `EXTERNAL` order, so it is never cached.
`INTERNAL-DIFF` is not "external" (`is_external()` is an identity check against `EXTERNAL`), so
the net correction the position pass makes is untouched.
- With the switch on, a normal restart leaves exactly the strategy's own position, and the
  shrink abort cannot arise for a namespace that never cached a fabricated order (D-A).
- A namespace that already cached one before this story is not helped. Known orders never reach
  `_generate_order`, so it can still abort on a shrink. The only place to stop that is before
  `run_async()` (D-D).

**4. "Hydrated from reconciliation, not reconstructed from local state" has one honest reading
here.** The architecture keeps the strategy's position in the session's Redis engine cache (AR10,
D2) and treats IBKR as authoritative over it (FR35). The resumed position is the cache's own
position, and it is kept **only because** reconciliation proved its side and quantity equal the
broker's:
- Story 4.2 refuses the start when a strategy's own position contradicts the broker.
- The strategy never rebuilds it itself: no strategy-private state, no trades-table lookup (G3 is
  superseded, Story 4.6 D-A).

Ownership cannot come from IBKR. The broker does not know which strategy opened a holding. So a
broker holding the cache cannot attribute (lost Redis, a manual TWS trade, the paper account's old
`AAPL +4`) lands as a synthetic `INTERNAL-DIFF` position. **What a strategy does next to one is a
product decision (D-C, pending the PO).**

**5. The history/live seam was deferred here by the PO (Story 4.4 review, ruling A).** A strategy
joins the bar stream only in its history callback. A bar published between the request and the
callback is missed. A history bar the adapter republishes after the callback is fed to the
indicators twice. Both are measurable from the cache (F8). **What to build is D-F, pending the
PO.**

### Measured facts — cite, don't re-derive

Each row says who measured it. Task 1 re-measures anything marked *to measure* before production
code is written.

| # | Fact | Where | Measured by |
|---|---|---|---|
| F1 | The IB adapter's `generate_order_status_reports` fabricates one `FILLED` `OrderStatusReport` per broker position, with `client_order_id = venue_order_id = instrument.id.value` (e.g. `"NVDA.NASDAQ"`). The cache does not know it, so `_reconcile_order_report` → `_generate_order(report, is_external=True)` → strategy `EXTERNAL`, then an inferred fill. The position pass (`_reconcile_position_report_netting`) sums **all** open positions on the instrument and corrects net through a diff report attributed to `INTERNAL-DIFF`. Result on a restart holding S +10 against a broker at +10: the triple S +10 / `EXTERNAL` +10 / `INTERNAL-DIFF` −10, net 10. | `adapters/interactive_brokers/execution.py:374-433`; `live/execution_engine.py:1128-1266, 1441-1597` | Story 4.2 Task 1.4A. Pinned: `test_a_normal_mid_position_restart_passes` (`synthetic_positions == 2`), `test_f4_a_broker_position_is_imported_as_an_external_order` (`tests/component/core/test_live_startup_reconcile_engine.py`) |
| F2 | Start from a cached fabricated `EXTERNAL "NVDA.NASDAQ"` order filled 10. A restart with the broker at 4 gives `_should_update → True` → `_generate_order_updated(quantity=4)` on an order filled 10 → `leaves_qty` underflow → Rust panic (`raw outside valid range`) → process abort inside `node:connect`. | `live/execution_engine.py:1190-1192` | Story 4.2 Task 1.4S. Pinned in a subprocess: `TestTheShrunkReEntryAbortCanary::test_the_second_restart_aborts_the_process` (default engine config) |
| F3 | `_generate_order`: `strategy_id = self.get_external_order_claim(...)`; `None` → `EXTERNAL` (`is_external=True`) or `INTERNAL-DIFF`. Then `if self.filter_unclaimed_external_orders and strategy_id.is_external(): … return None`, and `_reconcile_order_report` returns `True` ("External order dropped") **before** `cache.add_order`. `StrategyId.is_external()` is `self == EXTERNAL_STRATEGY_ID`, so `INTERNAL-DIFF` is never filtered. An order **already in the cache** never reaches `_generate_order` (`order = self._cache.order(client_order_id)` first). Nautilus's default is `False`; the session sets nothing today. | `live/execution_engine.py:1150-1160, 1708-1728`; `model/identifiers.pyx:818-829`; `src/core/live_node_builder.py:464-471` | Read at drafting. **To measure:** Task 1.1 |
| F4 | `sma_crossover._generate_buy_signal`/`_generate_sell_signal` read `self.cache.positions(venue=…, instrument_id=…)`, which covers every strategy, and `close_position()` every opposite-side open one. `sma_momentum.on_bar` reads `self.portfolio.is_net_long/is_net_short/is_flat(instrument_id)`, which is net over every strategy. | `src/core/strategies/sma_crossover.py:227-229, 271-273`; `sma_momentum.py:125-127` | Story 4.2 F10 |
| F5 | Under NETTING, a fill's position id is `{instrument_id}-{fill.strategy_id}`, so a reconciliation fill never lands in a strategy's own position. A strategy's position id is stable across restarts: `order_id_tag` is resolved from the frozen spec (`src/models/session.py:208-230`), and `StrategyId = f"{class}-{order_id_tag}"` is final only after `Trader.add_strategy` (`live_strategy_guard.py:233`). | `execution/engine.pyx:1225-1300` | Story 4.2 F6; Story 3.2 |
| F6 | The Redis namespace is per-session (`use_trader_prefix=True`, `use_instance_id=False`, `flush_on_start=False`, `src/core/live_cache.py:137-158`). The cache is loaded in `NautilusKernel.__init__` (`exec_engine.load_cache()`), i.e. during our `node:build`. Orders and the client-order-id counter round-trip through the real msgpack adapter. Positions round-trip too (`tests/integration/core/test_live_session_view_redis.py`). **Whether a restored `Position` keeps every fill's commission and `event_count` (so `position_vouches_for` holds on a close in the next process) is to measure.** | `system/kernel.py:455-456` | Stories 2.4, 3.4, 4.6. **To measure:** Task 1.3 |
| F7 | `PositionClosed` is an immutable snapshot carrying `ts_opened`, `avg_px_open`, `opening_order_id`, `peak_qty` and `realized_pnl`. `TradeRecorder` reads commission from the cached `Position` only when it vouches for the leg. `trade_key = f"{position_id}:{closing_order_id}"`. `SqlTradeRecord` writes `session_id = claimed.session_pk`, and the idempotency key is `(session_id, trade_id, client_order_id)`. `live start <name>` re-claims the **same** row on every start, bumping `owner_epoch`. | `src/core/live_trade_recorder.py:250-303, 391-447`; `src/services/trade_record.py:71-130`; `src/cli/commands/live_start.py:48-60` | Stories 3.5, 3.6 |
| F8 | `DataEngine._handle_bar` (live) caches every bar it processes (`self._cache.add_bar(bar)`), and does so before any strategy subscribes, because the runner's bar observer subscribes at `subscribe`. The historical response path `_handle_bars` calls `self._cache.add_bars(bars)`. **To measure:** where `add_bars` puts history relative to live bars already cached (i.e. what `cache.bar(bar_type)` and `cache.bars(bar_type)` return inside the history callback), and whether an instance-level `handle_bar` wrapper installed *before* the strategy's own callback is what `subscribe_bars` binds (the guard's precedent says yes). | `data/engine.pyx:1835-1874, 2102-2103`; `common/actor.pyx:3719-3798` | Read at drafting. **To measure:** Task 1.4 |
| F9 | Story 4.2 D-D: a strategy's own cached position that contradicts the broker refuses the start (`STRATEGY_POSITION_CONTRADICTED`, before any write), a PO ruling. Story 4.3 D-D stops a running session on the same finding. Both stay as they are (D-G). | `src/core/live_startup_reconcile.py:274-275`; `src/core/live_runtime_reconcile.py` | Stories 4.2, 4.3 |
| F10 | Every production backtest path runs **one strategy per instrument** under **HEDGING**: `BacktestOrchestrator` creates one strategy per instrument with a distinct `order_id_tag` (`src/core/backtest_orchestrator.py:218-220`, `:330`, `:368`); `backtest_runner.py` sets `OmsType.HEDGING` at every venue. So an own-book position read equals an instrument-wide read in every backtest. **To prove, not assume:** Story 4.4's byte-identical parity fingerprint (`tests/integration/core/test_warmup_backtest_parity.py`) must still pass unchanged. | | **To measure:** Task 1.5 |
| F11 | Sizes, using the guard's own metric (`tests/unit/governance/test_size_caps.py::measure_module`), as last recorded by Story 4.3 (**re-measure at Task 0**): `live_session_runner.py` **485/500** (not allowlisted); `LiveSessionRunner` baseline **412**; `SMACrossover` baseline **110** (over the class cap of 100); `SMAMomentum.on_bar` baseline **52**; `WarmupWatch` 81. The baseline is exact: every touched baselined subject is updated in the same commit. | `test_size_caps.py:63-65, 149-165` | Story 4.3 Debug Log |

### Design decisions (disclosed here, not discovered in review)

- **D-A — Stop importing the adapter's fabricated order: `filter_unclaimed_external_orders=True`.**
  - New constant `EXEC_ENGINE_FILTER_UNCLAIMED_EXTERNAL_ORDERS = True` in
    `src/core/live_node_builder.py`, passed to `LiveExecEngineConfig` beside the other explicit
    fields. The comment block states F1–F3 and the trade-off below.
    `tests/component/core/test_live_node_builder.py` pins both halves, as it does for every field
    there: we set `True`, and Nautilus's stock default is still `False`.
  - Enforced on the **running** engine, like Story 4.2's D-B: add
    `("filter_unclaimed_external_orders", True)` to `live_startup_reconcile.BROKER_WARD_SETTINGS`,
    with a comment saying why it lives there (it is what keeps the broker's view from being
    imported *twice*).
    - `tests/component/doubles/test_live_node.py::_TestEngine` and the unit tier's engine doubles
      must carry the attribute, or every runner test refuses. Find them with `grep -rn
      "generate_missing_orders" tests/`.
    - Story 4.2's D-B note ("`filter_unclaimed_external_orders` is **not** enforced") is
      superseded. Say so in the module docstring.
  - Effects:
    - A normal restart leaves exactly the strategy's own position (no triple).
    - A holding the cache cannot attribute is imported **once**, as `INTERNAL-DIFF`, priced from
      the broker's `avg_px_open`.
    - F2 cannot arise in a namespace that never cached a fabricated order.
  - Trade-off, disclosed and accepted:
    - A genuinely unknown *working* order at the broker (a manual TWS order) is no longer imported
      into the cache at startup, so `reconcile.ok`'s `open_orders` no longer counts it.
    - Its fills still reach the cache as a net correction (`INTERNAL-DIFF`), through the position
      pass and Story 4.3's runtime cycle.
    - AR25 is unaffected: it is about a strategy's *own* working orders, which Redis restores by
      `client_order_id`.
  - `open_check_interval_secs` stays `None` (Story 4.3's PO ruling 1A). Nothing else in the exec
    config changes.
  - Story 4.2's framework canaries (default config) stay exactly as they are: they pin what the
    framework does, not what the session does. This story adds **twins under the session's own
    config** (`_session_exec_config()`, `tests/component/core/test_live_runtime_reconcile_engine.py:84`).
- **D-B — Built-in strategies act on their own book only.**
  - `sma_crossover`: both signal methods read
    `self.cache.positions_open(instrument_id=self.instrument_id, strategy_id=self.id)`. The set is
    the same as today's `positions(...)` filtered by `is_open`, but restricted to this strategy. The
    `is_open` re-checks in the loops can go.
  - `sma_momentum`: `is_long`/`is_short`/`is_flat` come from the signed net of the strategy's own
    open positions on the instrument (`Decimal`, `signed_decimal_qty()`), not from `portfolio`.
  - Mode-agnostic (AR40, `is_live` count stays 0). No new import from `src.core.live_*`.
  - Behaviour-preserving in every backtest (F10). **Proven**: Story 4.4's byte-identical fill
    fingerprint and its `momentum` reference test pass **unchanged**.
  - Why both D-A and D-B:
    - D-A removes the synthetics a *normal* restart creates.
    - D-B keeps a strategy off every synthetic that can still exist beside it: a pre-4.5
      namespace's triple, a Story 4.3 runtime correction (P18b's manual share), an unattributable
      holding under D-C option A.
    - Either one alone leaves an NFR14 path open.
  - Size: `SMACrossover` is baselined at 110 (cap 100). `positions_open(...)` on one line should
    not grow it (measure). Update the baseline entry to the exact new value, lowered or raised
    with a one-line reason. If `SMAMomentum.on_bar` grows, move the own-net read into a small
    method.
- **D-C — A holding no strategy owns, on a traded instrument.** ✅ **Ruled by the PO (Allay),
  2026-09-28: option B.** Binding conditions of the ruling:
  - Only the strategy on the instrument whose unowned holding conflicts is refused.
  - `strategy.resume_refused` names the instrument and both quantities.
  - The holding is **never** auto-closed, flattened or size-adjusted; the remedy is manual, in TWS.
  - The refusal goes through the existing start-failure/containment path. If no strategy can start,
    the session start fails with exit 1 (fail closed).

  At a strategy's start, "unowned" means the signed net of **synthetic**
  (`EXTERNAL`/`INTERNAL-DIFF`) open positions on its instrument is non-zero. A pre-4.5 triple nets
  to zero, so it is not unowned. The options as presented:
  - **(A) Start it on its own book, loudly.** Log `strategy.resume_unowned_position` at WARNING
    (instrument, synthetic quantity, broker quantity) and start the strategy. With D-B it never
    touches the holding. Cost: if the holding *was* this strategy's (lost Redis), the strategy
    believes it is flat and its next entry doubles the exposure. That is exactly the "treating the
    instrument as flat and opening a fresh entry" AC #1 forbids, and it leaves the old shares
    unmanaged.
  - **(B) Refuse that strategy, contained — *recommended*.** Raise `ResumeRefusedError(reason=
    "unowned_position")` inside `_start_strategy`'s existing `try`, **before** `materialise_strategy`
    (the instrument comes from the spec's bar type). Story 2.7's start-failure path records it
    (`strategy.start_failed`, `live status` shows it), and sibling strategies start. If none start,
    the existing `NoStrategyStartedError` fails the start (exit 1). One
    `strategy.resume_refused reason=unowned_position` ERROR names the instrument, the synthetic
    quantity and the broker quantity. The operator message says the system never trades a holding
    it cannot attribute, and to remove the holding by hand in TWS or run the strategy on another
    instrument. Avoid AR36's forbidden stems in the text.
    - Cost: the paper account's old `AAPL +4`, or a P18b share, blocks that instrument's strategy
      until the operator deals with it at the broker.
  - **(C) Refuse the whole session start** at `reconcile` (exit 1) whenever any traded instrument
    carries an unowned holding. Simplest, and bluntest: it also stops strategies on unaffected
    instruments.
- **D-D — A pre-4.5 namespace that cached a fabricated order is refused before Nautilus can abort
  on it.**
  - New `refuse_imported_position_orders(cache)` in `src/core/live_session_resume.py`. It is called
    in `_phase_node_connect` **before** `capture_local_positions` and `run_async()`, because F2's
    panic happens inside `run_async` and nothing after it can catch it.
  - Predicate (precise to F1's shape): any cached order whose `strategy_id` is `EXTERNAL` **and**
    whose `client_order_id.value == instrument_id.value`. Only the adapter's position import mints
    that id. A manual order's id comes from its `orderRef`, or from a generated id.
  - Raises `ResumeRefusedError(reason="legacy_position_import")` (exit 1, operator-safe): `phase=
    node:connect status=failed`, the row is released by the runner's own `finally`, and nothing
    trades. The message names the instrument(s) and gives Story 4.2's remedy: create a new session,
    or clear this session's disposable engine cache (`docs/agent/nautilus.md`, "Startup
    reconciliation"), which loses the restored client-order-id counter.
  - Why always, and not only on a shrink: the broker's quantity is unknowable before `run_async`,
    and an abort that strands the row `running` is strictly worse than an explicit refusal.
    Affects only sessions restarted mid-position before this story (P7–P13-era paper sessions).
- **D-E — The resume is visible: `strategy.resumed`.**
  - After `add_strategy` (the strategy's id is final there, F5) and before `start_strategy`, a
    strategy holding its own open position(s) on its instrument logs one `strategy.resumed` at
    INFO, carrying:
    - `strategy_id`, `instrument_id`, `position_id`, `side`, `quantity`;
    - `avg_px_open` and `ts_opened` (ISO);
    - `opened_before_this_run` (`ts_opened` < this run's `started_at`);
    - `broker_quantity`, `broker_average_price` (from `StartupReconciliation.broker`);
    - `open_orders` (the strategy's own working orders).
  - A flat strategy logs nothing.
  - It is logged before the strategy's history request, so in the transcript it precedes
    `warmup.completed` and every bar (AC #4).
  - Its side and quantity equal the broker's by construction, because Story 4.2 refuses otherwise.
    It prints both anyway so P19 can read AC #2 off one line. Average price is informational: IB's
    `avgCost` folds in commission (Story 4.1's routing).
  - Never raises, and is contained like every other diagnostic record (AR42).
- **D-F — The history/live seam.** ✅ **Ruled by the PO (Allay), 2026-09-28: option B.** Binding
  conditions of the ruling:
  - Duplicate bars are dropped, and each missed bar gets one `warmup.seam_gap` WARNING.
  - **No replay**: a replay could submit an order on a stale price.
  - If Task 1.4 shows the last history bar cannot be read reliably from the cache, fall back to (C),
    measure only. Record the fallback in the Dev Agent Record and bring it back to the PO for a
    ruling.

  Everything lives in
  `live_session_warmup` (the watch already wraps `request_bars` and the history callback). No
  strategy edit and no runner edit. Options:
  - **(A) Full seam guard.**
    - Inside the history-callback wrapper, before the strategy's own callback, take the watermark
      = `ts_event` of the last history bar (F8, via the cache), and the gap bars = any cached live
      bar of that bar type with `ts_event` > watermark.
    - Install a contained, instance-level `handle_bar` filter (outermost, above the guard's
      wrapper) that drops a bar with `ts_event` ≤ the last bar the strategy consumed. This is the
      dedupe.
    - After the strategy's callback has subscribed, replay the gap bars through that same
      `handle_bar`, in `ts_event` order.
    - Cost: replayed bars can signal, so an order can be placed on a bar that closed seconds
      earlier. That is new behaviour to own and test. And NFR1's latency anchor (bar *arrival*)
      never sees a replayed bar.
  - **(B) Dedupe plus a visible gap — *recommended*.**
    - The same watermark and the same contained `handle_bar` filter (drops a duplicate or older
      bar, and logs `warmup.seam_duplicate_dropped` once per strategy).
    - No replay. A gap is reported, not filled: one `warmup.seam_gap` WARNING naming the missed
      bar(s) `ts_event`.
    - The silent error is removed, and the other becomes a record instead of a guess. Neither is a
      position-correctness error: the SMA is off by at most one bar for at most `slow_period`
      bars.
  - **(C) Measure only.**
    - Emit one `warmup.seam` INFO per warm-up (`clean` / `gap` / `duplicate`, with the two
      `ts_event`s). That makes P14 criterion 7 automatic.
    - Filter nothing, and route the fix to the Epic 4 retrospective with data.
  - If Task 1.4 shows the watermark cannot be read reliably from the cache, (A) and (B) fall back
    to (C), recorded, and the ruling is revisited.
- **D-G — What stays exactly as it is.**
  - Story 4.2's D-D refusal (a contradicted strategy position refuses the start) is **not
    relaxed**. A normal restart no longer produces a contradiction, and a real one still cannot be
    rewritten (F5).
  - Story 4.2's IB-CONID refusal (any unresolvable broker row refuses every start) is kept: an
    unresolved row cannot be tied to, or excluded from, any strategy's instrument.
  - Story 3.6's `reconciliation_owned` persist-skip is kept (Story 4.2 D-L).
  - `GUARDED_HANDLERS` is unchanged; its pin is exact.
- **D-H — Routed debt this story dispositions but does not build** (each with its reason, in
  `deferred-work.md`):
  - (a) *Strategy consults `orders_open` before a fresh signal* (Story 3.4's hand-off). Not built.
    - A stranded `ACCEPTED` order (Story 4.3's routed item) would silence the strategy forever.
    - The framework already denies a same-`client_order_id` duplicate.
    - Both built-ins place market orders.
    - Visibility instead: `strategy.resumed` carries `open_orders`.
  - (b) *The stranded `ACCEPTED` order and the `p7-position-test` stall.* Still conditional on
    P17b/P18 reproducing it live; neither has run. Carried forward.
  - (c) *An order filled while disconnected, corrected as `INTERNAL-DIFF`* (4.3 review). D-C governs
    the next start. Mid-run re-attribution would need cache surgery (Story 4.2's rejected option
    C), so it routes to the Epic 4 retrospective.
  - (d) *Strategy-visible suppression feedback* (Epic 3 retro Action Item 1). Unchanged: the
    built-ins keep no private position state, and D-B makes them read the cache every time.
- **D-I — No database, CLI, service or migration change.**
  - `SessionRecordPort` keeps four methods; `EXPECTED_CAPABILITIES` is unchanged; alembic head
    stays `85c949ac0374`.
  - `src/db/**`, `src/services/**`, `src/api/**` and `templates/**` are zero-diff.
  - FR19 is proven, not built: the claim re-binds the same `session_pk` (F7).
- **D-J — `ResumeRefusedError` carries the D3 markers.** It is defined in a `live_*` module, so
  `tests/unit/core/test_exit_outcome_markers.py` requires it: `exit_outcome =
  LiveCheckOutcome.ERROR`, `operator_safe_message = True`, our own text, and no account id
  (NFR26). `UNMARKED` does not grow.

## Acceptance Criteria

The epic text (`epics.md:1611-1642`) is quoted verbatim in **bold**. The clarification under each
criterion is binding.

1. **Given a session stopped while holding an open position, When the process is restarted, Then
   the strategy resumes aware of that position and seeks exits and stop levels for it, rather than
   treating the instrument as flat and opening a fresh entry (FR38).**
   - "Seeks exits" for the built-ins is the opposite crossover. **Neither built-in has a stop
     level**, so there is no stop logic to resume. That is disclosed, not built. A future stop-loss
     strategy inherits D-B's own-book read.
   - Proof, component tier, for **both** built-ins. Use a real `LiveExecutionEngine` under the
     session's own exec config, a real `RiskEngine`/`Portfolio`/`Trader`, a non-live `DataEngine`,
     Story 4.2's IB-shaped NETTING client, and one `MockCacheDatabase` shared by "process A" and
     "process B".
     - A opens LONG 10 and stops. The stop submits nothing (Story 3.1).
     - B loads the cache, runs the framework's own pass and `reconcile_at_startup` against a broker
       at +10, then starts the strategy.
     - A same-side crossover submits **nothing**.
     - The opposite crossover submits **exactly one** order, which closes the strategy's own
       position id.
   - The same scenario with a pre-4.5 triple seeded (`EXTERNAL +10 / INTERNAL-DIFF −10` beside S)
     still submits exactly one order of 10 (D-B). Mutation: D-B reverted → two orders.
2. **Given the resumed position, When its identity is checked, Then it is the same position the
   broker reports — hydrated from reconciliation, not reconstructed from local state (FR35).**
   - Under the session config, after the framework's pass, the instrument holds **exactly one**
     open position: the strategy's own, with the broker's side and quantity, and
     `synthetic_positions == 0` in `reconcile.ok`. Mutation: D-A reverted → the triple.
   - `strategy.resumed` names the position and the broker's quantity, and they are equal.
   - A contradicted cache is still refused (4.2 D-D, existing tests referenced, not duplicated).
   - A cache that lacks the position gets it imported once as `INTERNAL-DIFF`, and D-C's ruling
     applies.
3. **Given a full stop/restart cycle across an open position, When the trade record is examined
   afterwards, Then it contains no entry or exit generated by the interruption — the eventual round
   trip reads as one trade (NFR14).**
   - Across A → B, the only orders are the strategy's entry and its exit.
   - Exactly one `PositionClosed` reaches the `TradeRecorder`. Its sink is called **once**, with
     `strategy_id` = the strategy, `entry_timestamp` / `entry_price` / `venue_order_id` (opening
     order) from process A, and the exit from process B.
   - There are zero synthetic `trade.aggregated` records in the normal case.
   - Integration twin (`--forked`, real Redis, skipped when Redis is unreachable, the Story 3.4
     precedent): the same round trip through the real `CacheDatabaseAdapter`. The closed leg's
     commission covers **both** fills and `fill_count` is 2, or, if F6's measurement shows the
     restored position cannot vouch, the recorder's `trade.commission_unavailable` path is taken
     and the record says so (never a silent zero).
4. **Given the restart, When the strategy's first live decision is made, Then it happens only after
   reconciliation and warm-up have both completed (NFR9, NFR10).**
   - Runner tier (`TestLiveNode`), resumed-position scenario: the captured order is `reconcile.ok`
     → `strategy.resumed` → `warmup.completed` → the first `on_bar`. No order method is called
     before `warmup.completed`.
   - Story 4.2's `require_reconciled` latch and Story 4.4's subscribe-from-callback are the
     mechanisms. This test chains them for the resume case, it does not duplicate their unit
     proofs.
5. **Given the resumed session, When its identity is checked, Then the trade closed after the
   restart belongs to the same `session_id` as trades from before it (FR19).**
   - DB tier (`tests/integration/db/`, Postgres):
     - one session;
     - a first claim (`owner_epoch` 1) persists trade T1 through `SqlTradeRecord`;
     - the session is stopped and re-claimed (`owner_epoch` 2), and T2 is persisted through a new
       `SqlTradeRecord`;
     - both rows carry the same `session_id`, and a retry of T2 is idempotent (`False`).
   - CLI tier: two `live start` claims of one session name build sinks bound to the same
     `session_pk`.
6. **(Added at drafting: the HIGH debt routed here.)** Given a restart after the broker position
   shrank, When the framework's own pass runs under the session's config, Then the process does
   **not** abort.
   - The subprocess twin of Story 4.2's abort canary, under `_session_exec_config()`, exits 0.
   - A namespace that already cached a fabricated order is refused **before** `run_async()` with
     `ResumeRefusedError(reason="legacy_position_import")` and exit 1. It never reaches the panic
     (D-D).
7. **(Added at drafting: the seam routed here by the PO.)** The history/live seam is handled per
   D-F's ruling.
   - Under (B): a history bar republished after the callback reaches the indicators **once**
     (`count` advances by exactly the number of distinct bars), and a live bar published between
     request and callback produces one `warmup.seam_gap` naming it.
   - Proven with a real non-live `DataEngine` + `MockMarketDataClient` (Story 4.4's component
     harness). Mutation: the filter removed → double count.

## Tasks / Subtasks

- [x] **Task 0 — Standing checks (record the results in the Debug Log)**
  - [x] 0.1 Head and a clean tree. `ruff check` and `make typecheck` clean before the first edit.
    Try `uv run alembic current` (`85c949ac0374`); if the tool is denied, record that and rely on
    the zero `alembic/**` diff.
  - [x] 0.2 Baselines: `make test-unit`, `make test-component` (record counts). Re-measure F11 with
    `measure_module`: the runner file, `LiveSessionRunner`, `SMACrossover`, `SMAMomentum.on_bar`,
    `WarmupWatch`.
  - [x] 0.3 Is a Gateway reachable (ports 4001/4002/7496/7497)? Is Redis (6379) up? Is Postgres
    (5432) up? Record all three. They decide which integration and DB proofs run locally, and
    whether P19a can run.

- [x] **Task 1 — Probes (fresh interpreters, `/tmp/p45/`, not committed), BEFORE production code**
  - [x] 1.1 F3, under the session's exec config, on a real `LiveExecutionEngine` + IB-shaped client:
    - (a) S +10 against broker +10 → exactly S +10, and no `EXTERNAL` order in the cache;
    - (b) an empty cache against broker +10 → `INTERNAL-DIFF +10` at the broker's average price;
    - (c) a first restart, then a shrink (S closed and re-opened at 4) → no abort (a subprocess,
      like the canary);
    - (d) a namespace pre-seeded with a fabricated `EXTERNAL "NVDA.NASDAQ"` order → still aborts on
      a shrink. This proves D-D is needed.
  - [x] 1.2 `positions_open(instrument_id=…, strategy_id=…)` returns the same set as today's
    `positions(venue, instrument_id)` + `is_open` for a single strategy, and excludes synthetics.
  - [x] 1.3 F6: a `Position` with two fills (entry in process A, exit in process B) through a
    **real** Redis `CacheDatabaseAdapter`. Does the reloaded position keep both commissions and
    `event_count`, and does `position_vouches_for` hold at the close? Record which branch AC #3's
    integration test asserts.
  - [x] 1.4 F8, on a real non-live `DataEngine` with live bars already published and cached:
    - where history lands relative to them (`cache.bar`, `cache.bars` inside the callback);
    - whether an outer instance-level `handle_bar` wrapper installed before the callback is what
      `subscribe_bars` binds.

    The result decides D-F's feasibility.
  - [x] 1.5 F10: confirm one strategy per instrument in every production backtest path (`grep -n
    "add_strategy\|OmsType" src/core/backtest_*.py`).

- [x] **Task 2 — D-A: the exec config and its enforcement (AC #2, #6), TDD**
  - [x] 2.1 Red:
    - builder pins: the constant is `True`, Nautilus's default is `False`, the built
      `TradingNodeConfig.exec_engine` carries it;
    - `require_broker_ward_reconciliation` refuses when it is `False` or missing;
    - engine twins of Story 4.2's canaries under `_session_exec_config()`: a normal restart gives
      one position and `synthetic_positions == 0`; an empty cache gives `INTERNAL-DIFF` at the
      broker's price; the shrink subprocess exits 0.
  - [x] 2.2 Green: the constant and the config field; extend `BROKER_WARD_SETTINGS`; update every
    engine double. The existing default-config canaries stay green and untouched.
  - [x] 2.3 Docstrings: `live_startup_reconcile` (the D-B note, and the "how a strategy adopts…
    (Story 4.5)" line), `live_node_builder`'s block comment, the stale runner comment at
    `_phase_subscribe` ("Epic 4, still a no-op today").

- [x] **Task 3 — D-B: the own-book read in both built-ins (AC #1), TDD**
  - [x] 3.1 Red, at component tier:
    - a strategy with S +10 beside `EXTERNAL +10 / INTERNAL-DIFF −10` sells exactly 10 on a
      bearish crossover and buys nothing on a bullish one (both built-ins);
    - a strategy flat beside a synthetic `+10` treats its own book as flat. That is what D-C then
      governs.
  - [x] 3.2 Green: edit both strategies. `grep -rn "is_live" src/core/strategies/` → 0. No
    `src.core.live_*` import.
  - [x] 3.3 Parity: run `tests/integration/core/test_warmup_backtest_parity.py` and
    `tests/integration/test_sma_strategy_nautilus.py` (`--forked`). Both are **unchanged and
    green**. Update the size baselines to the exact values, each with a reason.

- [x] **Task 4 — `src/core/live_session_resume.py`: D-C (per ruling), D-D, D-E, D-J — TDD, unit +
  component**
  - [x] 4.1 Red, unit tier with duck-typed doubles:
    - `refuse_imported_position_orders` matches only F1's shape (an `EXTERNAL` manual order and a
      strategy's own order both pass), names every instrument, and never raises on an empty cache;
    - the unowned check (per ruling) nets synthetics per instrument, so a triple nets 0;
    - `strategy.resumed` carries every field, is silent for a flat strategy, and survives a
      raising logger;
    - `ResumeRefusedError` markers and message (no account id).
  - [x] 4.2 Green:
    - Pure netting helpers go in `src/models/position_reconciliation.py` (stdlib only; it already
      owns `CachedPosition.is_synthetic`). Pin any new name there by its existing purity test.
    - The node-facing functions go in the new module.
    - Imports: stdlib, `structlog`-shaped `log`, `src.core.exit_outcome`, the models module. A
      `nautilus_trader` import only if the instrument parse needs `BarType`/`InstrumentId`
      (node-facing modules may).
    - Module docstring: Why #1–#4, F1–F3, D-C's ruling, and the pre-4.5 namespace consequence.
  - [x] 4.3 Guard lists, **in the same commit** (CLAUDE.md Anti-Patterns):
    - add to `NODE_FACING_MODULES` (`tests/unit/core/test_live_node_never_exits.py`) and
      `TestImportPurity.MODULES` (`tests/component/core/test_session_runner_phases.py`), each with
      its reason;
    - **not** to `STOP_PATH_MODULES` (startup-only; add a comment saying so there);
    - `_STDLIB_AND_FIRST_PARTY` is proven by **running** `tests/integration/core/test_epic1_ac_node.py`;
    - `LIVE_MODULE_GLOBS` scans it for `FORBIDDEN_ORDER_METHODS` automatically. It must call none.

- [x] **Task 5 — Runner wiring (AC #1, #4, #6), component tier, `TestLiveNode`**
  - [x] 5.1 Red:
    - a cache holding a fabricated order refuses at `node:connect`: `phase=node:connect
      status=failed`, `run_async` never called, the row released, exit 1;
    - a resumed strategy logs `strategy.resumed` after `reconcile.ok` and before `warmup.completed`;
    - D-C per ruling: under (B), the unowned strategy is contained (`strategy.start_failed` +
      `strategy.resume_refused`) while a sibling starts, and all-unowned gives
      `NoStrategyStartedError`.
    - `TestLiveNode`'s `_TestCache` gains orders/positions only as the tests need.
  - [x] 5.2 Green: one module import (`from src.core import live_session_resume`, the
    `live_startup_reconcile` idiom, one line) and at most three call statements (`_phase_node_connect`,
    and `_start_strategy` before `materialise_strategy` and after `add_strategy`). **Budget: the
    runner file stays ≤ 500; the target is ≤ +6 statements.** Raise `LiveSessionRunner`'s baseline
    to its exact new value, with a one-line reason.
  - [x] 5.3 Update the prose:
    - the runner module docstring (what it does not own: the resume policy, `live_session_resume`);
    - `_phase_node_connect`'s docstring (the pre-`run_async` refusal);
    - `live_session_phases.py` if it describes `node:connect`'s content;
    - `live.py`'s `live start` help, only if it describes restart behaviour.

- [x] **Task 6 — The chained resume proofs (AC #1, #2, #3), component + integration**
  - [x] 6.1 Component: build a "resume harness" from the 4.2 `_Harness` / 4.3 `_session_exec_config`
    / 3.4 `_harness` precedents, with a shared `MockCacheDatabase` for A → B. Cover AC #1's two
    scenarios per built-in, AC #2's one-position assertion, and AC #3's single sink call with A's
    entry fields.
    - `MockExecutionClient` hardcodes HEDGING; use a NETTING subclass (Story 3.7's finding).
    - Mutations: D-A reverted → AC #2 red; D-B reverted → AC #1 triple-case red.
  - [x] 6.2 Integration (`--forked`, real Redis, `_require_redis` skip): AC #3's round trip through
    the real adapter, asserting F6's measured branch.

- [x] **Task 7 — FR19 (AC #5), DB and CLI tiers**
  - [x] 7.1 `tests/integration/db/`: two claims of one session, two `SqlTradeRecord`s, two trades,
    one `session_id`. Plus an idempotent retry. Reuse the Story 3.6 fixtures (`grep -rln
    SqlTradeRecord tests/integration/db`). If Postgres is down, record it; CI runs this directory.
  - [x] 7.2 CLI: `build_session_ports` for two claims of one name → the same `session_pk`.

- [x] **Task 8 — D-F per ruling (AC #7), `live_session_warmup`, unit + component**
  - [x] 8.1 Red: under (B), with Story 4.4's `test_strategy_warmup_engine.py` harness:
    - a duplicate history bar republished after the callback advances `count` by 0;
    - a gap bar gives one `warmup.seam_gap`;
    - a raising filter is contained and never escapes to the bus.
    Under (C): the `warmup.seam` classification only. Under (A): plus the replay order, and a
    replayed bar that signals submits through the order path.
  - [x] 8.2 Green, keeping `WarmupWatch` ≤ 100 (move helpers to module functions, the 4.4
    precedent). `EMITTED_*` pins: `warmup.*` has no membership pin today. Confirm with `grep -rn
    "warmup\.completed" tests/unit` before assuming.
  - [x] 8.3 `deferred-work.md`: strike the Story 4.4 review seam item with its disposition.

- [x] **Task 9 — Live verification (informational only, never a gate)**
  - [x] 9.1 Write **Procedure P19** in `docs/qa/phase3-live-verification.md`, continuing after P18.
    If a parallel story also claims P19, the integrator renumbers. It has two parts:
    - **P19a, read-only:** `scripts/diagnostics/live_node_probe.py --run-seconds 1 --verify-account
      --reconcile` on an account holding a position. Every broker position is now named
      `resolution="framework"` **as `INTERNAL-DIFF`**, and there is zero `EXTERNAL` order import.
      That observes D-A without a session.
    - **P19b, operator only**, defined and never run by a story session. Create a session on a
      liquid instrument, and let it enter inside RTH. Stop it with Ctrl-C and record TWS. Restart
      it by name, and record:
      - `reconcile.ok synthetic_positions=0`;
      - one `strategy.resumed` with `quantity == broker_quantity` and
        `opened_before_this_run=True`;
      - `warmup.completed` after it;
      - no `order.submitted` until an opposite crossover;
      - on that crossover, exactly one order of the resumed quantity;
      - one `trade.aggregated` / `trade.persisted` with the first run's `ts_opened`;
      - one `trades` row, with the same `session_id` as any earlier row of this session;
      - D6's `162|10182|366` grep first.

      Also P19b's day-two variant (a Gateway restart overnight), and the NFR3 09:25 ET note from
      P14.
  - [x] 9.2 Update the stale text:
    - P17a's "imports every broker position as `EXTERNAL`" becomes `INTERNAL-DIFF`;
    - P18b gets a note: under D-C (B) the P18b share contains that strategy on its next start;
    - the three stale "flatten tool is broken" notes (P11 ~1298, P12 ~1387, P13 ~1493, per
      `deferred-work.md:3043-3052` and `epic4-prework-spec.md:203-210`) are left **as they are**:
      their owner is whoever first closes an Epic 4 position with `--confirm`, and this story never
      touches a position.
  - [x] 9.3 If a Gateway is reachable **and** the probe's settings exist without reading or
    creating `.env`, run P19a and record the row. Otherwise record "defined, not run" with the port
    check and the reason. Never `--real-money`. Never touch `.env`, docker, or the kill /
    reconnect / connection-loss scripts. Never submit, open, close or flatten anything.

- [x] **Task 10 — Docs and routed debt**
  - [x] 10.1 `docs/agent/nautilus.md` "Startup reconciliation": D-A (why the filter is on, the
    trade-off), D-D (pre-4.5 namespaces and the remedy), D-C's ruling, and D-B (built-ins read their
    own book). `README.md`'s startup paragraph, only where it describes restart behaviour.
  - [x] 10.2 `deferred-work.md`, a new "Deferred from: story-4.5" section. Disposition every item
    routed to 4.5:
    - the triple (fixed, D-A);
    - the unfiltered read (fixed, D-B);
    - the shrink abort (fixed for new namespaces by D-A; refused explicitly for pre-4.5 ones by
      D-D);
    - D-D not relaxed (D-G);
    - the IB-CONID refusal kept (D-G);
    - the seam (per D-F);
    - D-H (a)–(d), each with an owner.

- [x] **Task 11 — Mutation sweep** (each mutation applied, its target tests run, the file
  restored; record kill or survive). M1 D-A constant `False`. M2 `BROKER_WARD_SETTINGS` without the
  new entry. M3 `sma_crossover` without `strategy_id=`. M4 `momentum` back to `portfolio`. M5 D-D
  predicate matching nothing. M6 D-D called after `run_async`. M7 the unowned check ignoring
  `INTERNAL-DIFF`. M8 `strategy.resumed` after `start_strategy` (so after `warmup.completed`).
  M9 the D-F filter removed. M10 the unowned net computed over all positions, so a triple would
  refuse.

- [x] **Task 12 — Gates**
  - [x] 12.1 `uv run ruff format .`, `uv run ruff check .`, `make typecheck`.
  - [x] 12.2 `make test-unit`, `make test-component`, then `--forked` integration for
    `tests/integration/core/` and `tests/integration/test_sma_strategy_nautilus.py`, plus
    `tests/integration/db/` if Postgres is up.
  - [x] 12.3 `grep -rn "is_live" src/core/strategies/` → 0. `git diff --stat` shows the D-I set
    untouched.

### Review Findings

Code review 2026-09-28, three parallel layers: Blind Hunter, Edge Case Hunter and Acceptance
Auditor. The Edge Case Hunter and the Auditor first failed on an API rate limit (HTTP 429) and
were re-run once the limit reset. 40 raw findings became 36 after merging duplicates: 2 decision,
19 patch, 3 defer, 12 dismissed.

- [x] [Review][Decision] ✅ **Ruled by the PO (Allay), 2026-09-28: A — accept B as built**, "the
  watermark read from the `handle_bars` wrapper is reliable and the cache is not, so the fallback's
  intent is met". Standing instruction: when a fallback condition is literally met, bring it back
  to the PO rather than deciding it. Recorded in the Dev Agent Record. — D-F's fallback condition,
  read literally, was met — The PO's condition:
  "if probing shows the last history bar can't be read reliably from the cache, fall back to C
  (measure only)… and bring it back for a ruling". Task 1.4 measured that the *cache* cannot hold
  it: `Cache.add_bars` drops history older than a live bar cached in flight. The dev read the
  watermark instead from an instance-level `handle_bars` wrapper. That was measured reliable (it
  saw every history bar before the callback), so the dev built B. The fallback's own reporting
  step, taking it back to the PO, was skipped. Options: (A) accept B as built, with the watermark
  from `handle_bars`; (B) fall back to C, measure only, and remove the dedupe filter.
- [x] [Review][Decision] ✅ **Ruled by the PO (Allay), 2026-09-28: A — startup only.** A broker
  holding that fully covers the strategies' own position on the same side is not a contradiction.
  The unowned excess makes D-C refuse only that strategy (`strategy.resume_refused` names the
  instrument and both quantities), and siblings start. The excess is never auto-closed, flattened
  or size-adjusted; the remedy stays manual in TWS. The runtime rule is unchanged. The
  broker-holds-less, flat and opposite-side cases must still refuse the session, with a test for
  each. — A holding beside a strategy's own position is refused session-wide, not contained —
  Example: the strategy holds +10 NVDA and +5 more was bought by hand, so the broker
  holds +15. Story 4.2's `strategy_contradicted` (10 ≠ 15) refuses the **whole session**
  (`STRATEGY_POSITION_CONTRADICTED`, "create a new session") before D-C ever runs, so sibling
  strategies are refused too. The `strategies hold {owned:+}` branch of D-C's message is then
  unreachable. Options: (A) at startup only, a broker holding that covers the strategies' own
  (same sign, `|broker| > |owned|`) is not a contradiction; the excess is unowned and D-C contains
  that one strategy, while the runtime rule is unchanged; (B) the same relaxation in the shared
  comparison, so the runtime cycle corrects it and continues; (C) keep the session refusal and
  only fix its wording.
- [x] [Review][Patch] **HIGH** — Refusing a strategy before `add_strategy` renumbers every later
  strategy's `order_id_tag`, orphaning their restored positions. `Trader.add_strategy` assigns
  `len(added):03d`, but the spec resolves tags by spec position. Fix: materialise with the
  spec-resolved tag. [src/core/live_session_runner.py:815, src/core/live_session_node.py:237]
- [x] [Review][Patch] The seam disarms at the first completion. It should disarm at settle, record
  every warm-up request's bar type, and report gaps only for the bar type just completed.
  [src/core/live_session_warmup.py:354]
- [x] [Review][Patch] D-D is never run against an order the framework actually imported. Add a
  real-engine test. [tests/component/core/test_live_session_resume_engine.py]
- [x] [Review][Patch] D-A's comment says "at startup", but the filter applies at runtime too.
  Manual TWS orders carry no `orderRef` and never reach the engine either way.
  [src/core/live_node_builder.py:176]
- [x] [Review][Patch] `_report_gap` builds its records and sorts outside its `try`.
  [src/core/live_session_warmup.py:455]
- [x] [Review][Patch] Tests that cannot fail: the `open_positions` "untouched" assert in the runner
  test; `test_it_never_raises` asserts no `strategy.resume_record_failed`.
  [tests/component/core/test_session_runner_resume.py, tests/unit/core/test_live_session_resume.py]
- [x] [Review][Patch] The CLI FR19 test's docstring claims more than it proves: it pins the
  binding, not the re-claim. [tests/unit/cli/commands/test_live_cli.py]
- [x] [Review][Patch] AC #4 is only proven up to `session.started`. Add a chained proof:
  `reconcile.ok` → `strategy.resumed` → `warmup.completed` → the first `on_bar`, and no order
  before `warmup.completed`. [tests/component/core/test_live_session_resume_engine.py]
- [x] [Review][Patch] P19 lacks Task 9.1's day-two (overnight Gateway restart) variant and the
  09:25 ET NFR3 note. [docs/qa/phase3-live-verification.md]
- [x] [Review][Patch] `BROKER_WARD_SETTINGS` has no exact-set pin, and nothing pins its names
  against a real engine (the "double pins" hazard). [tests/component/core/test_live_startup_reconcile_engine.py]
- [x] [Review][Patch] The AC #6 subprocess twin runs with the filter only, not the full
  `_session_exec_config()`. [tests/component/core/test_live_session_resume_engine.py]
- [x] [Review][Patch] AC #3's component test does not assert `entry_timestamp`.
  [tests/component/core/test_live_session_resume_engine.py]
- [x] [Review][Patch] The record claims "D-B reverted → two orders" for AC #1's triple case, but
  momentum's case survives M4 there (measured: M4b SURVIVED); M4 is killed only by
  `test_strategy_own_book.py`. Correct the record.
- [x] [Review][Patch] D-F (B) says `warmup.seam_duplicate_dropped` is logged once per strategy;
  it is logged per bar. [src/core/live_session_warmup.py:_is_seam_duplicate]
- [x] [Review][Patch] A Story 4.2 test's docstring still says the framework "imports it as
  `EXTERNAL`". [tests/component/core/test_live_startup_reconcile_engine.py]
- [x] [Review][Patch] `momentum` exits and flips by `trade_size`, not by its own position, so a
  resumed partial position is over-sold. Size the exit from its own net; backtest parity holds
  because there own == `trade_size`. [src/core/strategies/sma_momentum.py:125-172]
- [x] [Review][Patch] `_report_gap` names bars the strategy *did* receive when it subscribed before
  its history (possible for custom strategies). Exclude bars delivered while armed.
  [src/core/live_session_warmup.py]
- [x] [Review][Patch] D-C and D-E present the broker quantity as current, but it is the
  reconcile-time snapshot. Say so in the message. [src/core/live_session_resume.py]
- [x] [Review][Patch] The D-C remedy never reaches the console: the contained-failure line prints
  only the type, and `NoStrategyStartedError`'s text omits `strategy.resume_refused`.
  [src/cli/commands/live.py:579, src/core/live_session_runner.py:769]
- [x] [Review][Defer] History and live bars are assumed to share one `ts_event` convention; this
  is unverifiable offline [src/core/live_session_warmup.py] — deferred, needs P19's live seam
  reading
- [x] [Review][Defer] The built-ins ignore their own working orders restored from Redis
  [src/core/strategies/] — deferred, disclosed decision D-H (a), already routed
- [x] [Review][Defer] An unowned holding that appears *after* a strategy's start check (Story
  4.3's runtime cycle) is never refused [src/core/live_runtime_reconcile.py] — deferred, runtime
  alignment, Epic 4 retrospective

## Dev Notes

### The current surface: exact extension points

- `src/core/live_session_runner.py`:
  - `_phase_node_connect` `:570-590`: `capture_local_positions` at `:579`, `create_task(run_async)`
    at `:580`. **D-D goes before `:579`.**
  - `_phase_reconcile` `:603-629`: stores `self._reconciliation`, which D-E reads for
    `broker_quantity`.
  - `_phase_trading` `:742-776`.
  - `_start_strategy` `:778-827`: `materialise_strategy` at `:815` (**D-C (B) goes before it**,
    inside the `try`), `add_strategy` at `:821` (**D-E after it**), `start_strategy` at `:822`.
  - `_phase_subscribe`'s stale comment is at `:691-694`.
- `src/core/live_startup_reconcile.py`: `BROKER_WARD_SETTINGS` `:92-96`,
  `require_broker_ward_reconciliation` `:211-231`, `cached_positions` `:179-192` (reuse it, do not
  re-derive), `_REMEDY` `:107-112` (D-D's remedy text follows it).
- `src/models/position_reconciliation.py`: `SYNTHETIC_STRATEGY_IDS` `:45`,
  `CachedPosition.is_synthetic` `:87-90`, `StartupReconciliation.broker` `:194`.
- `src/core/live_node_builder.py`: exec config `:464-471`, the `EXEC_ENGINE_*` block `:132-174`.
- `src/core/live_session_warmup.py`: `WarmupWatch.instrument` and the callback wrapper (D-F).
- Strategies: `sma_crossover.py:224-303`, `sma_momentum.py:100-175`.
- Harnesses to reuse:
  - `tests/component/core/test_live_startup_reconcile_engine.py`: `_IBShapedClient` `:106-184`,
    `_Harness` `:187-273` (`seed`, `native`, `reconcile`), the subprocess abort canary `:701-739`;
  - `tests/component/core/test_live_runtime_reconcile_engine.py`: `_session_exec_config` `:84`,
    `_advance` `:100`;
  - `tests/component/core/test_live_order_recovery.py`: the `_harness` with `MockCacheDatabase`;
  - `tests/integration/core/test_live_order_survives_restart.py`: real Redis A → B;
  - `tests/component/core/test_strategy_warmup_engine.py`: non-live `DataEngine` +
    `MockMarketDataClient`;
  - `tests/component/doubles/test_live_node.py`: `_TestEngine` `:102`, `_TestCache` `:244`,
    `run_seconds_from_first_strategy`.

### Scope boundaries: do NOT build

- No change to AR39's phase order or names. D-D runs inside `node:connect`; D-C and D-E run inside
  `trading`.
- No re-invocation of `reconcile_execution_state`, no purge, and no direct cache write (Story
  4.2's rejected option C). No `external_order_claims` (claims register at `add_strategy`, after
  the framework's pass, F1; a claim would also hand the fabricated order to the strategy and
  *contradict* its own position).
- No `open_check_interval_secs` (PO ruling 1A, Story 4.3).
- No stop-loss logic in the built-ins (AC #1's "stop levels" is disclosed as N/A). No
  `orders_open` gating (D-H a). No `trade_size` default change for `momentum` (routed by 4.4).
- No edit to `custom/` strategies (a submodule). Its `sma_crossover_long_only.on_stop` still
  flattens (`deferred-work.md:2196`, owner: the submodule repo). Mention it in P19b's "know
  before starting".
- No new migration or column (`trades` still has no `strategy_id`; that is Epic 5's).

### Hazards (each bit a previous story, or will)

- **Guard-list rot.** Add the new `live_*` module to two lists in its creating commit. Adding
  `decimal` or anything new to a live module can trip `_STDLIB_AND_FIRST_PARTY`, and that is proven
  by running the test, not by reading it (the Story 3.3 lesson).
- **A test that cannot fail.** Every new scan or pin gets a non-vacuity twin. D-A's twins must go
  red with the constant flipped (M1).
- **Double pins.** `BROKER_WARD_SETTINGS` gained a member. If any test pins it as an exact set,
  update the pin in the same edit. If none does, add one: an exact-set pin against the running
  engine's attribute list.
- **The engine doubles.** Missing the new attribute on `_TestEngine` makes *every* runner test
  refuse at `reconcile`. Update the double first, run the suite, then add the constant.
- **HEDGING in `MockExecutionClient`.** The chained proof needs NETTING (F5), so subclass it.
- **C logging.** No component test may construct a `TradingNode` or a `LiveDataEngine`: keep the
  autouse `_assert_c_logging_state_is_unchanged` fixture. Real-Redis and `LiveDataEngine` probes
  belong at the integration tier, `--forked`.
- **The runner's hard cap.** Budget the ~6 statements before editing (F11). If it does not fit,
  move a helper out first (Story 4.2's D-K precedent) and re-check the guard lists.
- **Parallel Epic 4 stories.** Story 4.7 edits `docs/qa/phase3-live-verification.md`,
  `deferred-work.md` and possibly `live_startup_reconcile.py` concurrently. Append rather than
  reflow, and keep this story's footprint in shared files self-contained.
- **AR36 vocabulary.** No `close`/`kill`/`halt`/`pause`/`finalize` stem in any new event name or
  operator-facing message. "Remove the holding by hand in TWS", not "close it".
- **NFR26.** No account id in any new record or message. `strategy.resumed` carries quantities and
  prices only.

### Testing standards summary

TDD, with the red recorded first.
- Unit: the netting helpers and `live_session_resume` with doubles.
- Component: the real `LiveExecutionEngine` + NETTING IB-shaped client, the strategies, the runner
  with `TestLiveNode`, and the seam with a real non-live `DataEngine`.
- Integration (`--forked`): real Redis for AC #3, the backtest parity files, and
  `test_epic1_ac_node.py`.
- DB tier: FR19.

No test touches a broker (NFR32). Every mutation named in Task 11 is run and its result recorded.

### Project Structure Notes

- New:
  - `src/core/live_session_resume.py` (runner-side resume policy; `live_*` family, globbed into
    `LIVE_MODULE_GLOBS`);
  - `tests/unit/core/test_live_session_resume.py`;
  - `tests/component/core/test_live_session_resume_engine.py` (the chained A → B proofs);
  - `tests/component/core/test_session_runner_resume.py` (runner wiring) — or extend
    `test_session_runner_phases.py` if that is the nearer precedent (dev's choice, recorded);
  - `tests/integration/core/test_resume_round_trip_redis.py`;
  - a DB-tier test file under `tests/integration/db/`.
- Modified:
  - `src/core/live_node_builder.py`, `src/core/live_startup_reconcile.py` (setting and
    docstring), `src/models/position_reconciliation.py` (pure helper), `src/core/live_session_runner.py`,
    `src/core/live_session_warmup.py` (D-F);
  - both built-in strategies;
  - test doubles and guard-list files, `test_size_caps.py`, and the Story 4.2 engine test (twins
    only; its canaries are untouched);
  - `docs/agent/nautilus.md`, `docs/qa/phase3-live-verification.md`, `deferred-work.md`.
- "Prefer editing existing files": the resume policy is a new concept, and the runner cannot absorb
  it under its file cap. `live_startup_reconcile.py` owns the phase's proof, not strategy adoption,
  and its own docstring says so.

### References

- Epic and story: `_bmad-output/planning-artifacts/epics.md:1611-1642`. Scope extract:
  `_bmad-output/planning-artifacts/prd-epic4-scope.md` (FR19, FR35, FR38, NFR9, NFR10, NFR14,
  AR25, AR39–AR41, AR43).
- PRD Journey 2: `_bmad-output/planning-artifacts/prd.md:319-340`.
- Architecture D2 (Redis cache vs IBKR ground truth): `architecture.md:243-246, 595-597`; resume:
  `:49-52`.
- Routed debt: `deferred-work.md:3153-3166` (the seam), `:3347-3372` (triple, unfiltered read,
  shrink abort), `:3394`, `:3399`, `:3449-3456`, `:3492`.
- Prior stories:
  - 4.2 (F1–F10, D-D, Task 1.4A/D/S): `4-2-reconcile-against-the-broker-before-any-strategy-trades.md:68-188, 668-693`;
  - 4.3 (rulings 1A/2A, runtime D-D);
  - 4.4 (warm-up watch, seam ruling A): `4-4-…md:414-427`;
  - 3.4 (restore, `orders_open` hand-off): `3-4-…md:145-156, 540-550`;
  - 3.6 (sink, `session_pk`, `trade_key`).

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), in the Epic 4 harness worktree
`harness/s4-5-20260928-111617-1`.

### Debug Log References

- **Task 0.** Head `a42cfc3`, clean tree. `ruff check` and `make typecheck` clean before the first
  edit.
  - Baselines: unit **3095 passed**; component **1808 passed, 16 skipped**. The two `custom/`
    size-cap failures Story 4.4 recorded are gone (fixed by `6647104`).
  - `alembic current` was not run; `alembic/**` stays zero-diff by `git diff --stat`, and no
    migration was written.
  - Ports at 11:31 ET Monday 2026-09-28: 4001/4002/7496/7497 **closed** (no Gateway); Redis 6379
    and Postgres 5432 open.
  - F11 re-measured, all as recorded: runner file 485/500, `LiveSessionRunner` 412, `SMACrossover`
    110, `SMAMomentum.on_bar` 52, `WarmupWatch` 81.
- **Task 1 probes** (fresh interpreters, `/tmp/p45/`, not committed). Every premise held, except
  one correction.
  - **1.1** (real `LiveExecutionEngine` + Story 4.2's IB-shaped client,
    `filter_unclaimed_external_orders=True`):
    - (a) S +10 against broker +10 leaves exactly S +10; no fabricated order is cached;
      `synthetic_positions=0`.
    - (b) An empty cache against broker +10 at 101.25 leaves `INTERNAL-DIFF +10` at 101.25.
    - (c) A shrink after a first restart does not abort.
    - (d) A namespace that cached the fabricated order *before* the filter still panics (`raw
      outside valid range`), so **D-D is load-bearing**.
  - **1.2** `positions_open(instrument_id=…, strategy_id=…)` is the strategy's own set.
  - **1.3** (real Redis, two adapters, same trader id): the restored position keeps its id,
    strategy id and first fill. The close in the next run vouches (`event_count` 2), and its
    `opening_order_id` is run A's.
  - **1.4 — correction to D-F's sketch.** `Cache.add_bars` keeps only history *newer* than the
    latest cached bar (`cache/cache.pyx:1751-1756`). A live bar cached while the request was in
    flight therefore hides the whole history window from `cache.bar()`. The watermark instead comes
    from an **instance-level `handle_bars` wrapper**, which is honoured (probed: it saw all five
    history bars before the callback; `handle_historical_data` is honoured too). Live bars are
    cached by `DataEngine._handle_bar` whether or not the strategy has subscribed, so the gap *is*
    readable from `cache.bars()` in the callback. An outer instance-level `handle_bar` wrapper
    installed before start is what `subscribe_bars` binds. **This is not the (C) fallback
    condition**: the watermark is read reliably, so ruling B was built as ruled.
  - **1.5** Every production backtest path runs one strategy per instrument under HEDGING
    (`backtest_runner.py` ×6, `backtest_orchestrator.py:219-223`).
- **Harness finding (test-only).** Under `Environment.BACKTEST`, `Trader.add_strategy` gives each
  strategy a *fresh* `TestClock` at 0, so a history request computed `start = 0 − lookback` and
  overflowed `uint64`. The chained harness keeps each strategy's clock on the process clock. A real
  kernel does the same.
- **Mutation sweep (Task 11), 13 of 13 KILLED** (`/tmp/p45/mutate.py`: each applied, targets run,
  file restored — `git status` confirmed restored). Mutations M1–M10 as the task names, plus:
  - M11: the gap is never reported;
  - M12: the runner never calls `refuse_unowned`;
  - M13: the seam watermark keeps moving after warm-up.
- **Story-text corrections, recorded rather than absorbed.**
  1. `ResumeCheck` takes `started_at`/`log` positionally, so the runner's construction stays one
     line (budget).
  2. D-D logs `session.resume_refused` (a session-level refusal: `session.*` is sanctioned,
     dotted, past tense) rather than a `strategy.*` name.
  3. D-F's watermark source is `handle_bars`, not the cache (1.4).
  4. Two of Story 4.2's characterisation tests imported a working order unknown to the cache. D-A's
     disclosed trade-off drops it, so one was re-pointed at Nautilus's default (a pin), one twin was
     added under the session filter, and the `open_orders` count test now counts a strategy's own
     restored order.
  5. `_Harness` (Story 4.2) now defaults to the session's filter; the framework canaries pass
     Nautilus's default explicitly (`NAUTILUS_DEFAULT_FILTER`). The canaries still pin exactly what
     they pinned.
  6. The unit test file for `live_session_resume` was written before the module, but its red was
     the import error, not a separate run. The sweep's M5/M7/M10 kills show those tests can fail.
- **D-F's fallback condition was met, and B was built on a different watermark source (recorded
  at the PO's request, 2026-09-28).**
  - The PO's condition was: "if probing shows the last history bar can't be read reliably from
    the cache, fall back to C and bring it back for a ruling". Task 1.4 showed exactly that: the
    cache cannot hold the watermark.
  - The dev read it instead from an instance-level `handle_bars` wrapper, measured reliable, and
    built B without bringing the met condition back.
  - The code review caught it (Acceptance Auditor). The PO ruled "A — accept B as built; the
    fallback's intent is met", with a standing instruction: **when a fallback condition is
    literally met, bring it back to the PO rather than deciding it.**
- **Correction to AC #1's mutation claim.** The drafted AC said "D-B reverted → two orders" for
  the pre-4.5-triple case. That holds for `sma_crossover` only. For `momentum` the triple nets to
  its own +N, so a portfolio-net read still sells N once. Measured: M4 **survives**
  `test_live_session_resume_engine.py` alone (M4b). M4 is killed by `test_strategy_own_book.py`,
  where momentum sits beside an *unowned* holding.
- **Code review patches (2026-09-28, PO: batch-apply all 19, HIGH first).**
  - **HIGH:** each strategy is now materialised with the `order_id_tag` its spec position resolves
    (`SessionSpec.order_id_tags`; `materialise_strategy(spec_entry, session_spec)`), so a refused
    first strategy no longer renumbers a sibling and orphans its restored position. The regression
    test (momentum refused, `sma_crossover` keeps `SMACrossover-001` and resumes its +22) went red
    first with `SMACrossover-000`.
  - **Decision 2 (PO: A, startup only).**
    - New pure `PositionDiscrepancy.broker_covers_strategy`.
    - `reconcile_at_startup` leaves a covered, net-matching row to D-C (`_to_act_on`). Only a
      contradiction that is not covered refuses the session.
    - The runtime cycle is unchanged, pinned by a new unit test.
    - Tests for the three still-refusing shapes (broker holds less, flat, opposite side) at the
      unit tier and on a real engine. An end-to-end runner test shows D-C containing only that
      strategy, naming `unowned_quantity=5 strategy_quantity=10 broker_quantity=15`.
  - **The seam:**
    - it stays armed until the warm-up settles (`_disarm` in `settle`'s `finally`);
    - gaps are reported only for the bar type whose history just answered;
    - bars the strategy did receive are excluded from the gap;
    - the duplicate record is once per strategy (D-F (B)'s text);
    - `_report_gap` is contained end to end.
  - **Momentum** sizes exits and flips from its own net, not `trade_size`. Backtest fills are
    unchanged: parity files green.
  - **The console** prints a start refusal's own (redacted) detail line, so the D-C remedy reaches
    the operator. `NoStrategyStartedError` names `strategy.resume_refused`.
  - **D-D** is exercised on a real engine's cached fabricated order.
  - **Pins and ordering:**
    - `BROKER_WARD_SETTINGS` is pinned as an exact set, and its names are read off a real
      session-config engine;
    - the AC #6 twin runs under the full `_session_exec_config()`;
    - AC #3 asserts `entry_timestamp`;
    - AC #4 has a chained proof: `reconcile.ok` → `strategy.resumed` → `warmup.completed` →
      `test.on_bar` → `test.order_submitted`.
  - **Wording:**
    - D-A's comment and docs cover the runtime scope;
    - D-C/D-E say the broker figure is the reconcile-time read;
    - the CLI FR19 test's docstring is honest about what it pins;
    - a Story 4.2 docstring no longer says "imports as `EXTERNAL`";
    - P19 gained the day-two, 09:25 ET and covered-excess (P19c) variants.
- **Post-review sweep: 22 of 22 KILLED.** M1–M13 were re-run, with M11 re-pointed at the changed
  call. M14–M22 were added, one per review patch:
  - M14: spec tag dropped;
  - M15: Decision 2 reverted;
  - M16: `same_side` ignored;
  - M17: relaxation applied at runtime;
  - M18: disarm at the first answer;
  - M19: delivered bars in the gap;
  - M20: duplicate logged every time;
  - M21: momentum exit by `trade_size`;
  - M22: console remedy removed.
- **Post-review gates.** `ruff format` / `ruff check` / `make typecheck` clean. Unit **3157
  passed**; component **1875 passed, 16 skipped**; integration (`--forked`, all but
  `tests/integration/db`) **210 passed, 2 skipped**; `tests/integration/db` **115 passed**;
  `is_live` = 0. Sizes: runner file 490/500 and `LiveSessionRunner` 416 (unchanged by the
  patches), `WarmupWatch` 84, `SMAMomentum.on_bar` 51, `SessionSpec` 71.

### Completion Notes List

- **What shipped, per the PO's rulings (D-C: B, D-F: B; D-A, D-B, D-D, D-E approved).**
  - **D-A.** `EXEC_ENGINE_FILTER_UNCLAIMED_EXTERNAL_ORDERS = True` in `live_node_builder`, passed
    to `LiveExecEngineConfig` and enforced on the running engine by adding it to
    `BROKER_WARD_SETTINGS`. A restart holding a position now leaves only the strategy's own
    position; a holding the cache cannot attribute arrives once, as `INTERNAL-DIFF`.
  - **D-B.** Both built-ins read their own book (`positions_open(instrument_id, strategy_id=self.id)`;
    `momentum` nets the same set). Backtest parity is proven unchanged.
  - **New `src/core/live_session_resume.py`:**
    - `refuse_imported_position_orders` (D-D, called in `node:connect` before `run_async()`);
    - `ResumeCheck.refuse_unowned` (D-C, contained in `_start_strategy` before `materialise_strategy`);
    - `ResumeCheck.note_resumed` (D-E, after `add_strategy`, before `start_strategy`);
    - `ResumeRefusedError` (D3 markers, exit 1).
  - A pure `split_by_owner` in `position_reconciliation`.
  - **D-F** in `live_session_warmup`: the seam dedupe (`warmup.seam_duplicate_dropped`) and the gap
    record (`warmup.seam_gap`, WARNING, never replayed), contained throughout.
  - No database, migration, service or port change (D-I).
- **AC evidence.**
  - **AC #1:** `test_live_session_resume_engine.py::TestAStrategyResumesMidPosition` (process A
    enters and stops → process B reloads the same cache database, runs Nautilus's own pass and
    `reconcile_at_startup` under the session config, and restarts a real strategy). For both
    built-ins: a same-side signal submits nothing, and the opposite signal submits exactly one exit
    of its own quantity. The same holds beside a seeded pre-4.5 triple. Also
    `test_strategy_own_book.py`.
  - **AC #2:** the same file (one open position, the broker's quantity, `synthetic_positions == 0`,
    `strategy.resumed` quantity == broker quantity) and `TestTheSessionImportsTheBrokerOnce`.
  - **AC #3:** `TestTheRoundTripReadsAsOneTrade` (one sink call; entry order, price and time from A;
    commission and `fill_count` 2; one `trade.aggregated`), and the real-Redis twin
    `test_resume_round_trip_redis.py` (commission 2.55 across both runs).
  - **AC #4:** `test_session_runner_resume.py::TestAResumedStrategyIsNamedBeforeItsFirstDecision`
    (`reconcile.ok` → `strategy.resumed` → `warmup.completed` → `session.started`; logged before
    `start_strategy`).
  - **AC #5:** `test_trade_record.py::test_story_4_5_a_trade_closed_after_a_restart_joins_the_same_session`
    (real Postgres: two claims, one `session_id`, an idempotent retry) and the CLI binding test.
  - **AC #6:** `TestAShrunkRestartNoLongerAborts` (subprocess, exit 0) and the runner's D-D refusal
    before `run_async`.
  - **AC #7:** `test_warmup_seam.py`, with an unwatched anti-tautology twin, plus the watch's unit
    branches.
- **Guard lists, in this change.** `live_session_resume.py` joins `NODE_FACING_MODULES`,
  `TestImportPurity.MODULES` and the AR36 prose scan, each with its reason. It is deliberately not
  in `STOP_PATH_MODULES` (a comment there says why). `_STDLIB_AND_FIRST_PARTY` needed no edit,
  proven by running `test_epic1_ac_node.py` in the full `tests/integration/core` run.
  `UNMARKED` did not grow.
- **Size budgets, the guard's own metric.** Runner file 485 → **490 of 500**. Baselines, each with
  a one-line reason:
  - `LiveSessionRunner` 412 → **416**;
  - `build_trading_node_config` 129 → **130**;
  - `SMACrossover` 110 → **106** (lowered);
  - `SMAMomentum.on_bar` 52 → **51** (lowered).

  `WarmupWatch` 81 → 82; the new `ResumeCheck` is 61.
- **Gates.** `ruff format` / `ruff check` / `make typecheck` clean.
  - Unit **3133 passed** (+38).
  - Component **1861 passed, 16 skipped** (+53).
  - `tests/integration/core` + `test_sma_strategy_nautilus.py` `--forked`: **112 passed, 2
    skipped**.
  - `tests/integration/db`: **115 passed** (Postgres up).
  - `is_live` in `src/core/strategies/` = **0**.
- **Live verification.** Procedure P19 was written: P19a is read-only, P19b is operator-only. It
  is recorded **defined, not run**: no Gateway was reachable, and the worktree has no `.env`. P17a's
  text was updated (the framework now imports as `INTERNAL-DIFF`), and P18 gained a "know before
  starting" note (its hand-bought share triggers D-C at the next start). Nothing was submitted,
  opened, closed or flattened, and `--real-money`, `.env`, docker and the
  kill/reconnect/connection-loss scripts were not touched.
- **Routed** (`deferred-work.md`, "Deferred from: story-4.5"), each with an owner:
  - the pre-4.5 namespaces that now refuse;
  - the kept IB-CONID refusal;
  - the stranded `ACCEPTED` order, carried forward;
  - the mid-run `INTERNAL-DIFF` re-attribution, to the Epic 4 retro;
  - `orders_open` gating, not built;
  - the seam's residual one-bar lag after a named gap;
  - P19 not run.

### File List

New:

- `src/core/live_session_resume.py`
- `tests/unit/core/test_live_session_resume.py`
- `tests/component/core/test_live_session_resume_engine.py`
- `tests/component/core/test_session_runner_resume.py`
- `tests/component/core/test_strategy_own_book.py`
- `tests/component/core/test_warmup_seam.py`
- `tests/integration/core/test_resume_round_trip_redis.py`
- `_bmad-output/implementation-artifacts/4-5-resume-a-strategy-mid-position.md`

Modified:

- `src/core/live_node_builder.py` (D-A constant and config field)
- `src/core/live_startup_reconcile.py` (`BROKER_WARD_SETTINGS`, docstring)
- `src/core/live_session_runner.py` (D-D, D-C, D-E wiring; docstrings)
- `src/core/live_session_warmup.py` (D-F seam)
- `src/core/live_session_phases.py` (docstring)
- `src/core/live_session_node.py` (`materialise_strategy` takes the session spec — review HIGH)
- `src/core/strategies/sma_crossover.py`, `src/core/strategies/sma_momentum.py` (D-B; momentum
  exit sizing — review)
- `src/models/position_reconciliation.py` (`split_by_owner`, `broker_covers_strategy`)
- `src/models/session.py` (`SessionSpec.order_id_tags` — review HIGH)
- `src/cli/commands/live.py` (`live start` help text)
- `README.md`, `docs/agent/nautilus.md`, `docs/qa/phase3-live-verification.md` (P19; P17a/P18 notes)
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (the `4-5-…` key only)
- `tests/component/core/test_live_node_builder.py`
- `tests/component/core/test_live_startup_reconcile_engine.py`
- `tests/component/core/test_live_runtime_reconcile_engine.py`
- `tests/component/core/test_session_runner_phases.py`
- `tests/component/core/test_session_runner_warmup.py` (`_History` passes the new argument through)
- `tests/component/doubles/test_live_node.py`
- `tests/unit/core/test_live_runtime_reconcile.py` (runtime-unchanged pin)
- `tests/unit/models/test_session_spec.py` (`order_id_tags`)
- `tests/integration/db/test_trade_record.py`
- `tests/unit/cli/commands/test_live_cli.py`
- `tests/unit/core/test_live_node_never_exits.py`
- `tests/unit/core/test_live_session_warmup.py`
- `tests/unit/core/test_live_startup_reconcile.py`
- `tests/unit/core/test_live_stop_path_is_inert.py`
- `tests/unit/governance/test_size_caps.py`
- `tests/unit/models/test_position_reconciliation.py`

## Change Log

- 2026-09-28: Story drafted (create-story), ready-for-dev. D-C and D-F carry recommended options
  pending the PO's ruling.
- 2026-09-28: The PO ruled **D-C: B** and **D-F: B** (conditions recorded under each decision), and
  approved D-A, D-B, D-D and D-E as drafted, with these conditions:
  - D-D's refusal is explicit, logged and fail-closed, names the remedy "create a new session", and
    touches no DB schema (Epic 4 owns no migration).
  - `strategy.resumed` and `strategy.resume_refused` use the sanctioned dotted past-tense naming.
  - Strategies stay mode-agnostic: the `is_live` count stays 0, and backtest parity is proven by a
    test.
  - The restart proof is a broker-double test plus a documented operator procedure (P19, recorded
    as "defined, not run").
- 2026-09-28: Implemented (dev-story). All 12 tasks are done and 13/13 mutations were killed.
  - Tests: unit 3133 (+38); component 1861 passed, 16 skipped (+53); integration core 112 passed,
    2 skipped; DB 115.
  - D-F was built as ruled (B); the (C) fallback was not needed.
  - P19 is defined, not run (no Gateway).
  - Status → review.
- 2026-09-28: Code review (three parallel layers; two re-run after an API rate limit). 40 raw
  findings, 36 after merging: 2 decision, 19 patch, 3 defer, 12 dismissed.
  - The PO ruled Decision 1: A (accept D-F B as built, with a standing instruction to bring back
    literally-met fallbacks) and Decision 2: A (startup-only covered-excess relaxation).
  - The PO chose batch-apply for the patches. All 19 are applied, HIGH (order-id renumbering)
    first, with a red-first regression test.
  - Mutations 22/22 killed. Unit 3157; component 1875 passed, 16 skipped; integration 210
    passed, 2 skipped; DB 115.
  - Status → done.
