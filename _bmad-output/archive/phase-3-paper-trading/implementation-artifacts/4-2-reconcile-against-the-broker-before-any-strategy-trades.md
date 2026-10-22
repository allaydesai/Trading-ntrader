# Story 4.2: Reconcile Against the Broker Before Any Strategy Trades

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want startup to hydrate from IBKR's answer rather than from local state,
so that a resumed session begins from what is actually true at the broker.

## Why this story is shaped the way it is

Read this before designing anything. The epic text reads like "fill in `_phase_reconcile`, call
Nautilus's reconciliation, check the result". Each part of that is wrong in a specific way.
Measured at drafting (2026-09-27, installed `nautilus-trader 1.220.0`, head `32f852f`). Wheel paths
are relative to `.venv/lib/python3.11/site-packages/nautilus_trader/`. ripgrep skips `.venv`
because it is gitignored, so search it with `grep -n` on explicit paths.

**1. Nautilus's own reconciliation has already run by the time `reconcile` starts, and it cannot
be moved.** `NautilusKernel.start_async` does connect → **reconcile** → emulator → portfolio →
`trader.start()` in one coroutine, and returns early (the trader never starts) if reconciliation
reports failure (F1). Story 2.5's `node:connect` waits for `trader.is_running`, so native
reconciliation physically runs **inside `node:connect`, before `gate:account`**. Turning it off and
re-invoking it from our phase would reorder the kernel's own sequence (portfolio and trader would
start on unreconciled state) and contradict AC #2's "enabled through framework configuration
rather than reimplemented". So the `reconcile` phase does not *run* reconciliation. It
**verifies and completes** the framework's pass, after `gate:account` and before any strategy
exists (D-A).

**2. The framework's pass is net-only, and it is blind to an instrument the broker is flat in.**
This is the `p7-fill-0901` bug, now traced to its line. For every cached open position whose
instrument is absent from the mass status, the engine asks the client for a position report
(F2). The IB adapter's `generate_position_status_reports` **ignores the instrument filter and
skips zero quantities** (F3), so a flat instrument never gets a report. The phantom cached
position is never touched. That is exactly the `p7-fill-0901` transcript: `Reconciling NET
position for AAPL.NASDAQ`, never NVDA, then `Portfolio: NVDA.NASDAQ net_position=22` on a flat
broker. It also means "Execution state reconciled" proves nothing (Story 4.1's routed finding:
a failed position read counts as success). **The only honest 0-discrepancy check compares the
cache against Story 4.1's `read_broker_state`.**

**3. The framework resolves a disagreement without ever rewriting a strategy's own position.**
Every reconciliation fill is attributed to a synthetic strategy: `EXTERNAL` for an order the
broker reports that the cache does not know, and `INTERNAL-DIFF` for a net-quantity correction
(F4, F5). Under NETTING, a fill's position id is derived from its **own** strategy id (F6). So a
correction lands in a separate `…-INTERNAL-DIFF` position and the strategy's position stays as
the cache recorded it. Net is right; the strategy's own belief is not. Both built-in strategies
act on that belief. `sma_crossover` reads `cache.positions(venue, instrument_id)` **unfiltered by
strategy**, and `sma_momentum` reads the portfolio's net (F10). A strategy that believes it holds
22 shares the broker does not hold will, on its next opposite signal, `close_position()` them:
a real SELL 22 against a flat account, a trade nobody requested (NFR14). This is why D-D exists,
and why it is the one decision drafted as **pending a PO ruling**.

**4. `confirm_state_reestablished` cannot go live on its own.** Its first call sets
`_has_ever_connected` (F11), which silently arms the monitor's own `connection.lost` →
`_unavailable_since` → `connection.halted` path. The steady state's narration was written
around the monitor being silent. The halt clock is cleared **only** by a later
`confirm_state_reestablished` call, and a reconnect re-confirm is Story 4.3's. A startup-only
grant would therefore make every later outage log `connection.halted` 60 s after the loss,
even one that reconnected in 5 s. That is the retro's "sanity check", and its answer is: **dormant
until 4.3** (D-J).

**5. The runner file is at 499 of a hard 500 executable statements** (F12). A multi-line
statement counts every line it occupies. There is no room for the wiring without first moving
something out, and CLAUDE.md requires the split to be budgeted **before** the edit (D-K).

### Measured facts — cite, don't re-derive

Task 1 re-measures F4–F8 against a real `LiveExecutionEngine` before production code is written.

| # | Fact | Where |
|---|---|---|
| F1 | `start_async`: `_await_engines_connected` → `if exec_engine.reconciliation: if not await _await_execution_reconciliation(): return` → `_emulator.start()` → `_initialize_portfolio()` → `_await_portfolio_initialization` → `_trader.start()`. A reconciliation failure returns **before** `trader.start()`, which is why the runner's `node:connect` times out. The Redis cache is loaded earlier, in `NautilusKernel.__init__` (`exec_engine.load_cache()` when `config.exec_engine.load_cache`, default `True`), i.e. during our `node:build`. | `system/kernel.py:991-1027`, `:455-456`; `execution/engine.pyx:695-785` |
| F2 | `reconcile_execution_state`: per client, `generate_mass_status` → `_reconcile_execution_mass_status` (orders, then position reports). Then, for each **cached** open position whose instrument is **not** in `mass_status.position_reports`, it awaits `generate_position_status_reports(...)` and reconciles whatever comes back. A `None` mass status is a warning, not a failure (`:915-920`). `timeout_secs` is validated positive and never enforced (`:874`). | `live/execution_engine.py:850-992` |
| F3 | IB `generate_position_status_reports` calls `get_positions(account)` and **ignores `command.instrument_id`**. `if not positions: return []`, and zero quantities are skipped (`continue`). A cached position on an instrument the broker is flat in therefore produces **no report**, and F2's loop reconciles nothing for it. Live reproduction: `p7-fill-0901` (`deferred-work.md` "Deferred from: Procedure P10 live run"). | `adapters/interactive_brokers/execution.py:453-505` |
| F4 | IB `generate_order_status_reports` **fabricates a `FILLED` `OrderStatusReport` per broker position**, with `client_order_id = venue_order_id = instrument.id.value` (e.g. `"NVDA.NASDAQ"`). `_reconcile_order_report` for an id the cache lacks calls `_generate_order(report, is_external=True)`, giving strategy `EXTERNAL` (unless a strategy's `external_order_claims` claims the instrument, which is registered at `add_strategy`, after reconciliation), then an inferred fill for `filled_qty − order.filled_qty`. **If `report.filled_qty < order.filled_qty` it returns `False`**, which fails native reconciliation, so the trader never starts. | `execution.py:374-433`; `live/execution_engine.py:1128-1266, 1672-1762` |
| F5 | `_reconcile_position_report_netting` sums `signed_decimal_qty()` over **all** open positions on the instrument, across strategies. If that differs from the report: `generate_missing_orders=False` returns `False`. Otherwise it builds a `FILLED` diff `OrderStatusReport` and calls `_reconcile_order_report(diff, is_external=False)`, giving strategy `INTERNAL-DIFF`. Price: `calculate_reconciliation_price` needs the report's `avg_px_open`; without it, the fallback is last quote, then current average, then a `MARKET` report with no price, whose inferred fill is priced `make_price(0.0)`. | `live/execution_engine.py:1441-1597`; `live/reconciliation.py:25-90`; `:1602-1670` |
| F6 | `_determine_position_id`: if the cache has no position id for the fill's `client_order_id` and the OMS is NETTING, the id comes from `_determine_netting_position_id(fill)`, which is `{instrument_id}-{fill.strategy_id}`. A reconciliation fill therefore opens or changes `…-EXTERNAL` / `…-INTERNAL-DIFF`, **never** a strategy's own position. Live corroboration: P11/P12's `INTERNAL-DIFF` phantom round trip (`deferred-work.md` P11 addendum). Task 1 re-measures it. | `execution/engine.pyx:1225-1300` |
| F7 | Consequence of F4 on a restart that holds a strategy-owned position S LONG 10 with the broker at 10, first restart. Order pass: `EXTERNAL` LONG 10, net 20. Position pass: `INTERNAL-DIFF` SELL 10, net 10. The cache now holds **three** open positions on the instrument (S +10, EXTERNAL +10, INTERNAL-DIFF −10), net correct. On a later restart after the broker position *shrank*, the cached `"NVDA.NASDAQ"` order's `filled_qty` exceeds the fabricated report's, so reconciliation returns `False` and the trader never starts. This is **not 4.2's to fix** (it is the resume path, Story 4.5). Task 1 pins the shape and Task 9 routes it. | derived from F4–F6 |
| F8 | `LiveExecutionEngine.reconcile_execution_report(report) -> bool` is public (also a msgbus endpoint). For a `PositionStatusReport` it runs F5's netting path. For an instrument excluded by `reconciliation_instrument_ids` it **returns `True` without reconciling**. `PositionStatusReport.create_flat(account_id, instrument_id, size_precision, ts_init)` exists. The constructor takes `(account_id, instrument_id, position_side, quantity, report_id, ts_last, ts_init, venue_position_id=None, avg_px_open=None)`, and `venue_position_id=None` selects netting. | `live/execution_engine.py:994-1037`; `execution/reports.py:704-749` |
| F9 | `LiveExecutionEngine` exposes the broker-ward configuration on the running engine: the `reconciliation` property, and the attributes `generate_missing_orders`, `filter_position_reports`, `reconciliation_instrument_ids` (a list, `[]` when unset) and `filter_unclaimed_external_orders`. | `live/execution_engine.py:160-168, 218-228` |
| F10 | `sma_crossover._generate_buy_signal` and `_generate_sell_signal` read `self.cache.positions(venue=…, instrument_id=…)`, all strategies, and `close_position()` every opposite-side open position. `sma_momentum` reads `self.portfolio.is_net_long/is_flat(instrument_id)`, which is net. | `src/core/strategies/sma_crossover.py:224-300`; `src/core/strategies/sma_momentum.py:125-155` |
| F11 | `ConnectionMonitor`: `_has_ever_connected` is set only in `_grant_permission` (`:295`), reached only via `confirm_state_reestablished` (`:268-287`). The LOST / `_unavailable_since` / halt path requires it (`:331-353`). `_unavailable_since` is cleared only by `_grant_permission` (`:310`). The steady state narrates transitions because the monitor is silent (`live_session_steady_state.py:421-455`). `test_confirm_state_reestablished_is_never_called_in_this_story` scans production modules for any call (`tests/component/core/test_session_steady_state.py:501-519`). Readers of `trading_permitted`: tests and `scripts/diagnostics/live_connection_loss_probe.py` only. The order gate is `submission_withheld`. | `src/core/live_connection_monitor.py` |
| F12 | Size (the guard's own `measure_module`): `live_session_runner.py` **file 499/500**, not allowlisted. `LiveSessionRunner` 414 (baselined), `__init__` 44, `run` 66 (baselined), `_phase_reconcile` 3. `stop_degraded_strategies` is module-level, **18**, and is only called from `run()`'s `finally`. `live_session_node.py` file 107, and it is already on `NODE_FACING_MODULES`, `STOP_PATH_MODULES` **and** `TestImportPurity.MODULES`. `test_regenerating_the_baseline_reproduces_it_exactly` makes the baseline **exact**: any size change to a baselined entry needs an edit. | `tests/unit/governance/test_size_caps.py` |
| F13 | `live_trade_recorder.RECONCILIATION_STRATEGY_IDS = frozenset({"EXTERNAL", "INTERNAL-DIFF"})` (`:148`). A `PositionClosed` carrying either is `trade.persist_skipped reason="reconciliation_owned"` (`:441`; Story 3.6 D-D). | `src/core/live_trade_recorder.py` |
| F14 | Runner seams: `node_factory`, `account_verifier`, `client_builder`, `connection_reader` are the "four broker-facing seams" (`live_check_driver.py:124-125`). Eight test files construct `LiveSessionRunner`; each has its own `_runner`-style helper passing a stub `account_verifier`. `TestLiveNode`'s `_TestCache` has only `instruments()`; its `_TestEngine` (both engines) has `check_connected`/`registered_clients`. | `tests/component/doubles/test_live_node.py` |

### Design decisions (disclosed here, not discovered in review)

- **D-A — The framework reconciles where it always has; `reconcile` verifies and completes it.**
  - `EXEC_ENGINE_RECONCILIATION = True` stays. Nothing turns it off, and nothing re-invokes
    `reconcile_execution_state`.
  - The body of `_phase_reconcile` runs `reconcile_at_startup(...)` on the node's loop, the
    `gate:account` precedent (`loop.run_until_complete`). In order it:
    1. enforces the broker-ward configuration on the running engine (D-B);
    2. reads the broker (`read_broker_state`, Story 4.1);
    3. compares (D-C);
    4. refuses a strategy-position contradiction (D-D);
    5. resolves the rest broker-ward through the framework (D-E);
    6. re-verifies 0 discrepancy;
    7. logs `reconcile.ok`.
  - Honest scope, stated in the phase docstring and in `live_session_phases.py`: the framework's
    pass physically runs inside `node:connect` and is read-only against the broker (it submits
    nothing). A **hard** native failure (F1's early return) stops the sequence at
    `node:connect`, because the trader never starts. The `reconcile` phase's own failures log
    `phase=reconcile status=failed`.
- **D-B — Enforce the configuration on the live engine, not only on the config object.**
  - A new `require_broker_ward_reconciliation(exec_engine)` refuses with
    `FRAMEWORK_RECONCILIATION_DISABLED` unless all of these hold:
    - `reconciliation is True`;
    - `generate_missing_orders is True`;
    - `filter_position_reports is False`;
    - `list(reconciliation_instrument_ids) == []`.

    A missing attribute is drift, and it is refused too.
  - Additionally, a component pin (test only, no production line) that the **built**
    `TradingNodeConfig.exec_engine.load_cache is True`. That flag is what brings a session's own
    working orders back from Redis (AR25). `filter_unclaimed_external_orders` is **not**
    enforced: `True` drops `EXTERNAL` orders, but F5's position pass still corrects net through
    `INTERNAL-DIFF`, and D-C's re-verify is the final guard either way. *(Superseded by Story 4.5,
    D-A, 2026-09-28: the session's engine now runs with `filter_unclaimed_external_orders=True`
    and `require_broker_ward_reconciliation` enforces it as a fourth entry of
    `live_startup_reconcile.py`'s `BROKER_WARD_SETTINGS` — it is what keeps the adapter's
    fabricated per-position order out of the cache.)*
- **D-C — What "internal position state matches the broker exactly" means (NFR9).**
  - For every instrument in (cache open positions ∪ broker positions): the cache's **net**
    signed quantity (`Σ position.signed_decimal_qty()` over all open positions, every strategy)
    `==` the broker's signed `BrokerPosition.quantity`, compared exactly as `Decimal`.
  - Average price is informational (Story 4.1's routing). Cash is logged, not compared:
    Nautilus's `AccountState` is not cash (Story 4.1, F5 there).
  - Working orders are counted into `reconcile.ok` (AR25 visibility), not compared: the broker
    read has no orders, and open-order truth is Story 4.3's.
  - The comparison is a pure, stdlib-only function in `src/models/position_reconciliation.py`,
    so Story 4.6's service can reuse it without importing Nautilus (AR38).
  - It returns one `PositionDiscrepancy` per disagreeing instrument, carrying:
    - `instrument_id`;
    - `local_quantity` (cache net);
    - `strategy_quantity` (the net of non-synthetic strategies only; F13's two ids are
      synthetic);
    - `broker_quantity`;
    - `broker_resolved` (the broker row's `instrument_resolved`).
  - `kind` is `"strategy_position"` when `strategy_quantity != 0 and strategy_quantity !=
    broker_quantity`, else `"position"`. **Scenario D disagrees only here:** after the framework
    pass, net equals the broker but the strategy's own position does not. Rows are sorted by
    instrument.
- **D-D — A strategy's own position that contradicts the broker stops the session.**
  ✅ **Ruled by the PO (Allay), 2026-09-27: option A, refuse.** The alternatives were presented
  at creation and not taken. (B) Correct net through the framework and start anyway: this leaves
  the strategy's false belief in place (F6, F10). (C) Purge and re-hydrate: this is cache surgery
  outside the framework's reconciliation API, against AC #2.
  - Any `kind == "strategy_position"` discrepancy produces:
    - one `reconcile.discrepancy … resolution="refused"` per instrument, at ERROR;
    - `ReconciliationFailedError(STRATEGY_POSITION_CONTRADICTED)`;
    - `phase=reconcile status=failed`;
    - no strategy is ever started.
  - The check runs **before** D-E writes anything, so a refused start leaves no `INTERNAL-DIFF`
    residue of ours in Redis. The framework's own pass has already written its own residue (F7),
    which is unavoidable.
  - Why refuse rather than correct: the framework cannot rewrite a strategy's position (F6), and
    a started strategy acts on it (F10, NFR14). Refusing trusts neither side: local state is not
    acted on, and the broker is not "corrected".
  - The scenario-A restart (strategy-owned equals broker) passes. A synthetic-only phantom (a
    stale `EXTERNAL` position the broker has since closed) is not a strategy belief and is
    resolved by D-E.
  - Operator remedy in the error text: *create a new session for the strategy*. The Redis
    namespace is disposable by architecture (D2, `docs/agent/nautilus.md`), so clearing it is
    documented there as the alternative, with its cost: the restored client-order-id counter is
    lost (Story 3.4).
  - Story 4.5 owns making a strategy adopt the broker's position and may relax this rule, with a
    named test.
- **D-E — Everything else resolves broker-ward through the framework's own public entry point.**
  - Remaining `kind == "position"` rows are handled like this:
    - `broker_resolved is False` (an `IB-CONID-*` row) → refuse (`UNRESOLVABLE_DISCREPANCY`);
    - an instrument the cache does not hold (`cache.instrument(InstrumentId.from_str(id)) is
      None`, or `from_str` raises) → refuse (`UNRESOLVABLE_DISCREPANCY`);
    - otherwise, build a `PositionStatusReport` carrying the broker's truth and call
      `exec_engine.reconcile_execution_report(report)`:
      - FLAT → `create_flat(...)`;
      - otherwise `LONG`/`SHORT`, with `Quantity(abs(broker), instrument.size_precision)` and
        `avg_px_open = instrument.make_price(broker.average_price)` when the broker reported a
        price. Never `None` when a price is known: F5's fallback would price the fill at 0.
      - `account_id` is `find_ib_exec_client(node).account_id` (Story 4.1's finder);
      - the timestamps are `time.time_ns()`.
    - A `False` return → refuse (`RESOLUTION_REFUSED`).
  - **Never** a purge, a direct cache write, or an order method. The module is scanned against
    `FORBIDDEN_ORDER_METHODS` by `LIVE_MODULE_GLOBS` automatically.
  - Then re-read the cache and re-compare. **Any** remaining row → refuse
    (`DISCREPANCY_REMAINS`, rows named). This is the guard that makes F8's silent `True` for a
    filtered instrument harmless.
  - The trade recorder is not subscribed yet (it attaches at `subscribe`), so the correction's
    `PositionOpened`/`Closed` reach no sink.
- **D-F — A pre-reconciliation snapshot, so the framework's fixes are not silent.**
  - `capture_local_positions(node, log)` runs in `_phase_node_connect` **immediately before**
    `create_task(node.run_async())`, the last instant the cache holds only what Redis gave it
    (F1). It returns `tuple[CachedPosition, ...]`.
  - It is contained: any raise logs `reconcile.local_snapshot_failed error_type=…` and returns
    `None`. It is diagnostic only, AR42.
  - In `reconcile`, each instrument that disagreed **before** the framework pass and agrees
    **after** logs one `reconcile.discrepancy … resolution="framework"` at WARNING.
  - This is the "a discrepancy is logged, never silently resolved" guarantee for the half of
    reconciliation that runs inside Nautilus. It is also the "before" quantity Story 4.7 needs.
  - A fresh session on an account holding anything logs it once per instrument. That is the
    truth: the local view did not know.
- **D-G — Records (AR41 `reconcile.*`; NFR26).**
  - `reconcile.discrepancy`, WARNING, or ERROR when refused. Fields:
    - `instrument_id`, `kind`, `resolution` ∈ {`framework`, `broker`, `refused`};
    - `local_quantity`, `strategy_quantity`, `broker_quantity` (as `str(Decimal)`);
    - `reason` when refused.
  - `reconcile.ok`, INFO, exactly once per successful phase. Fields:
    - `account` (already masked in `BrokerState`);
    - `positions` (count) and `instruments` (`{id: str(qty)}`);
    - `open_orders` (count);
    - `discrepancies` (count logged this phase);
    - `synthetic_positions` (count of open `EXTERNAL`/`INTERNAL-DIFF`);
    - `elapsed_ms`.
  - Never a raw account, a `str(exc)`, or broker text. Story 4.1's
    `reconcile.broker_state_*` records are unchanged.
- **D-H — One failure type, no new exit code.** `ReconciliationFailedError(RuntimeError)` in
  `src/core/live_startup_reconcile.py`:
  - it carries `reason: ReconciliationFailure` (a `StrEnum`: `FRAMEWORK_RECONCILIATION_DISABLED`,
    `STRATEGY_POSITION_CONTRADICTED`, `UNRESOLVABLE_DISCREPANCY`, `RESOLUTION_REFUSED`,
    `DISCREPANCY_REMAINS`, `NOT_RECONCILED`), `detail` and `discrepancies`;
  - D3 markers: `exit_outcome = LiveCheckOutcome.ERROR`, which is exit 1, AR28's "configuration,
    state or database failure"; `operator_safe_message = True`;
  - the message is our own text naming instruments and quantities.

  `BrokerStateUnavailableError` (exit 4) and `BrokerStateAdapterError` (exit 1) propagate
  **unchanged** out of the phase. The AR28 table and `EXIT_CODES` are untouched.
- **D-I — Trading refuses without a reconciliation result (NFR18, defence in depth).**
  - The runner stores `reconcile_at_startup`'s `StartupReconciliation` in `self._reconciliation`.
  - `_phase_trading`'s first statement is `require_reconciled(self._reconciliation)`, which
    raises `ReconciliationFailedError(NOT_RECONCILED)` when it is `None`.
  - Ordering already guarantees this. The latch is what turns "a later edit made `reconcile` a
    no-op or swallowed its failure" into a red test rather than a session trading on unverified
    state.
  - With it, AC #4 has a structural proof, since no strategy exists before `trading`, **and** an
    enforced one.
- **D-J — `confirm_state_reestablished` stays dormant; the sanity check is the deliverable.**
  - Findings recorded, F11: the call arms the LOST/halt path, the halt clock is cleared only by a
    later confirm, the steady-state narration assumes a silent monitor, and a scan test pins zero
    callers.
  - No production caller is added. `trading_permitted` has no production reader, and
    `submission_withheld` (the real gate) is unchanged.
  - Routed to 4.3, which owns the reconnect re-confirm that makes the call safe.
  - The dormancy docstrings in the runner and the steady state change from "no-op placeholder"
    to "dormant until Story 4.3's reconnect re-confirm".
- **D-K — Budget the runner before the edit (F12).**
  - The **first** code change of the story, a pure move with every tier green before and after:
    `stop_degraded_strategies` moves verbatim to `src/core/live_session_node.py`.
  - The runner keeps the name by importing it, since `run()`'s `finally` calls it. So `from
    src.core.live_session_runner import stop_degraded_strategies` in two test files keeps
    working unmodified.
  - The `Trader` import moves with it.
  - All three guard lists already hold `live_session_node.py`, so the move escapes no scan. Say
    so in the commit and prove it by running them.
  - Budget: 499 − 18 − 1 + 1 = **481**, then this story's wiring (~13), about **494/500**.
    Measure after each step.
  - `LiveSessionRunner`'s baseline 414 changes to its measured value, with a one-line reason
    (`Story 4.2: reconcile seam, pre-reconciliation snapshot, trading latch; logic lives in
    live_startup_reconcile.py`).
- **D-L — The Story 3.6 D-D persist-skip policy is confirmed, not flipped.** This story routes every
  broker-ward correction into `EXTERNAL`/`INTERNAL-DIFF` positions (D-E, F6) and refuses to
  rewrite a strategy's (D-D). A synthetic position's round trip is therefore never a strategy
  decision and must never join a strategy's comparison sample. `live_trade_recorder.py` is
  **zero-diff**.
  - `SYNTHETIC_STRATEGY_IDS` in the new models module is a duplicated literal of F13's constant.
    Per CLAUDE.md, pin it **equal by import** in a test.
- **D-M — Read-only operator surface.**
  - `scripts/diagnostics/live_node_probe.py --reconcile` (opt-in; the P15 `--read-broker-state`
    precedent) runs `reconcile_at_startup` against the probe's in-memory-cache node:
    - no strategy is added, so nothing can submit;
    - no Redis write occurs;
    - `local_before` is empty, so every broker position is logged `resolution="framework"`.
  - It prints `reconcile=ok positions=N discrepancies=M`. On `ReconciliationFailedError` it prints
    `RESULT: fail reason=reconcile_refused failure=<reason>` and exits 1.
  - Without the flag, output is byte-identical.
- **D-N — Zero-diff set.** These are all untouched:
  - `live_trade_recorder.py`, `live_order_path.py`, `live_connection_monitor.py`,
    `live_session_steady_state.py` (docstring edit only), `live_broker_state.py`;
  - every strategy under `src/core/strategies/`;
  - `src/db/**`, `alembic/**` (head `85c949ac0374`), `src/services/**`, `src/api/**`,
    `templates/**`;
  - `SessionRecordPort` and `EXPECTED_CAPABILITIES`.

## Acceptance Criteria

The epic text (`epics.md:1512-1546`) is in **bold**. The clarifications under each are binding.

1. **Given the startup sequence, When the `reconcile` phase runs, Then it executes after
   `gate:account` and strictly before `warmup`, `subscribe`, and `trading` — no strategy is started
   until reconciliation has completed (FR33, NFR18, AR39).**
   - `PHASE_SEQUENCE` is unchanged. The ordered-pairs test still sees all sixteen records.
   - A spy proves `reconcile_at_startup` is invoked after `gate:account`'s `ok` and before
     `warmup`'s `started`, and that `trader.added_strategies`, `started_strategies` and
     `added_actors` are all empty at that moment.
   - The landmine suite gains `_phase_reconcile` as a failing phase. No later phase runs, and no
     strategy is ever added.
   - D-A's scope statement (the native pass runs inside `node:connect`) is in the phase docstring
     and `live_session_phases.py`.
2. **Given Nautilus's native reconciliation, When the node is configured, Then startup reconciliation
   is enabled through framework configuration rather than reimplemented, and working orders and open
   positions are loaded into the cache (FR33, AR25).**
   - The existing `EXEC_ENGINE_RECONCILIATION` pins stay.
   - New pin: the built config's `exec_engine.load_cache is True`.
   - D-B's runtime enforcement refuses each drifted attribute, one test per attribute.
   - Characterization tests at component tier, with a **real** `LiveExecutionEngine` + `Cache` +
     NETTING mock client:
     - an `ACCEPTED` open-order report and a position report in a mass status end up in
       `cache.orders_open()` / `cache.positions_open()`;
     - a cache pre-loaded with a strategy order survives reconciliation still open.
   - Our code's **only** engine mutation is `reconcile_execution_report` (a call-recording spy
     plus an AST check over `live_startup_reconcile.py`).
3. **Given a disagreement between cached state and broker state, When reconciliation resolves it,
   Then the broker's view wins and the cache is overwritten — never the reverse (FR35).**
   - Against the real engine and cache:
     - (a) a stale `EXTERNAL` LONG 4 with the broker flat: after the phase, cache net is 0, and
       one `reconcile.discrepancy resolution="broker"`;
     - (b) cache flat, broker +5 at 101.25: cache net +5, and the synthetic fill is priced from
       the broker's average, not 0;
     - (c) the framework's own pass correcting a synthetic mismatch: a
       `resolution="framework"` record from D-F;
     - (d) never the reverse: no path submits, cancels or modifies an order at the broker (the
       inert scan), and a strategy contradiction is **refused**, not resolved to local (D-D).
       With the broker flat and strategy +22, the phase raises and the strategy's +22 is still
       in the cache, untouched by us.
4. **Given reconciliation has not completed, When a signal would fire, Then no order is submitted
   (NFR10).**
   - Structural: strategies are materialised only in `trading`. This is proven by the AC #1 spy.
   - Enforced (D-I): `_phase_trading` with `_reconciliation is None` raises `NOT_RECONCILED`
     before `materialise_strategy` is reached (spy). The mutation that drops the latch goes red.
   - A failed reconcile adds no strategy and submits nothing (the double's trader records no
     `add_strategy`).
5. **Given reconciliation completes, When state is compared, Then internal position state matches
   the broker exactly — 0 discrepancy — before trading is permitted (NFR9).**
   - `reconcile.ok` is emitted only when the post-resolution `compare_positions(cache, broker)`
     is `()`. *(Narrowed by Story 4.5, PO ruling 2026-09-28, startup only: a row whose net
     already matches the broker and whose broker holding covers the strategies' own on the same
     side is settled by `_to_act_on` and does not withhold `reconcile.ok`; the excess is refused
     per strategy in `trading` by D-C's resume check. The running session's cycle still stops on
     the same row.)*
   - A resolution the framework reports as successful that leaves a row (F8's filtered-instrument
     shape) → `DISCREPANCY_REMAINS`.
   - `compare_positions` is exact (`Decimal("22") != Decimal("22.0001")`). It spans cache ∪ broker
     instruments, so a broker-only and a cache-only instrument are each caught. It includes the
     scenario-D strategy row where net agrees.
6. **Given reconciliation fails, When the phase reports, Then it logs `phase=reconcile
   status=failed`, the startup sequence stops, and the session does not trade (AR39).**
   - Each `ReconciliationFailure` reason, and a `BrokerStateUnavailableError` from the reader, is
     tested end to end through `runner.run()`. Each case shows:
     - `(reconcile, failed)` is the last phase pair;
     - no later phase runs;
     - the exception propagates unchanged;
     - `classify_failure` gives exit 1 (ours / adapter) or 4 (broker unreachable);
     - the row is released (`mark_stopped`).
   - A stop signal during the broker read is demoted to a stop (the existing
     `session.stop_superseded_failure` path), not reported as a reconcile failure.

## Tasks / Subtasks

- [x] **Task 0 — Standing checks (record in the Debug Log)**
  - [x] 0.1 Head, clean tree. `uv run ruff check .` and `make typecheck` clean before the first edit.
  - [x] 0.2 Baselines: `make test-unit`, `make test-component`. Record passed/failed/skipped.
  - [x] 0.3 Re-measure F12 with `measure_module` (runner file 499, class 414).

- [x] **Task 1 — Measure before building (component-tier probes in `/tmp/p42/`, not committed)**
  Real `LiveExecutionEngine` + `Cache` + `Portfolio`, using the `test_live_order_recovery.py`
  `_harness` shape, with a **NETTING** mock client registered as `ClientId("INTERACTIVE_BROKERS")`.
  Story 3.7 found `MockExecutionClient` hardcodes HEDGING, so subclass or construct with
  `OmsType.NETTING`.
  - [x] 1.1 F6: a `create_flat` report against a cache holding strategy S LONG 22 → which position
    ids are open afterwards, and their signed quantities. Expected: S +22, `…-INTERNAL-DIFF` −22.
  - [x] 1.2 F5/D-E: a flat cache plus a LONG 5 report with `avg_px_open` → the fill price. Then
    without `avg_px_open` and with no quote → the fill price. Expected: the broker average, then 0.
  - [x] 1.3 Is `reconcile_execution_report`'s effect on `cache.positions_open()` **synchronous**
    (no loop tick needed)? If not, record how many ticks, and D-E's re-verify must yield them.
  - [x] 1.4 F4/F7: a mass status shaped like the IB adapter's (fabricated `FILLED` order report
    keyed by instrument id, plus a position report) against (A) S +10 cached, broker +10, and
    (D) S +22 cached, broker +10. Record the open positions after `reconcile_execution_state`.
  - [x] 1.5 Does the `Portfolio`'s `net_position` follow the correction?
  - [x] **Gate:** a result contradicting D-D/D-E's premises (e.g. 1.1 lands in S's own position)
    reopens that decision **before** Task 3. Record, and ask.

- [x] **Task 2 — Budget split (D-K), before any feature code**
  - [x] 2.1 Move `stop_degraded_strategies` (and its `Trader` import) verbatim into
    `live_session_node.py`. Import it in the runner. Update its docstring's "Module level, not a
    method" sentence to say where it lives and why.
  - [x] 2.2 Run the unit, component and `tests/integration/core/` (`--forked`) tiers, plus the three
    guard-list suites, and `test_size_caps.py` after regenerating the baseline. Record
    **481**-ish.

- [x] **Task 3 — `src/models/position_reconciliation.py` (AC #3, #5) — TDD, unit tier**
  - [x] 3.1 Red (`tests/unit/models/test_position_reconciliation.py`):
    - `CachedPosition` is frozen, with a non-zero `Decimal` quantity and `is_synthetic`;
    - `PositionDiscrepancy.kind` covers both kinds;
    - `compare_positions`:
      - net across strategies;
      - union of instruments;
      - exact `Decimal`;
      - scenario A → `()`;
      - scenario C → `strategy_position`;
      - scenario D (net agrees) → `strategy_position`;
      - synthetic-only phantom → `position`;
      - broker-only → `position`;
      - an unresolved broker row → `broker_resolved=False`;
      - sorted output;
    - `StartupReconciliation` holds the facts `reconcile.ok` renders;
    - `SYNTHETIC_STRATEGY_IDS == live_trade_recorder.RECONCILIATION_STRATEGY_IDS`, by import;
    - purity, both an AST scan and a fresh-interpreter `sys.modules` check, forbidding
      `nautilus_trader`, `ibapi`, `sqlalchemy`, `src.core`, `src.db` and `src.services`.
  - [x] 3.2 Green, stdlib only.

- [x] **Task 4 — `src/core/live_startup_reconcile.py` (AC #2, #3, #5, #6) — TDD**
  - [x] 4.1 Red, unit tier, with duck-typed stubs (`tests/unit/core/test_live_startup_reconcile.py`):
    - D-B, each attribute plus a missing attribute;
    - D-D refuses before any `reconcile_execution_report` call (spy);
    - D-E:
      - FLAT versus LONG/SHORT report shape;
      - `avg_px_open` present;
      - unresolved or unknown instrument refused;
      - `False` refused;
      - a still-remaining row refused;
    - D-F:
      - snapshot success;
      - snapshot contained on raise;
      - `framework` records;
      - `None` snapshot → no `framework` records, and not a failure;
    - D-G: record fields and levels, and no raw account in any captured value;
    - D-H: markers, and `classify_failure` → 1;
    - `require_reconciled`;
    - elapsed bound, with an injected reader that sleeps under its deadline.
  - [x] 4.2 Green. Duck-typed where possible. The Nautilus imports are only what building a
    `PositionStatusReport` needs (`PositionStatusReport`, `PositionSide`, `InstrumentId`,
    `Quantity`, `UUID4`).
  - [x] 4.3 Component tier, with the real engine (`tests/component/core/test_live_startup_reconcile_engine.py`):
    - AC #2 characterizations;
    - AC #3 (a)–(d);
    - Task 1's measured shapes pinned as named canaries, which fail by name on an upgrade:
      - F3/F6: a flat report lands in `INTERNAL-DIFF`;
      - F4: the fabricated-order `EXTERNAL`;
      - F8: filtered-instrument `True`;
      - the F9 attribute names exist.

    Every component file carries the `_assert_c_logging_state_is_unchanged` autouse fixture.
    Never construct a `TradingNode` here.

- [x] **Task 5 — Runner wiring (AC #1, #4, #6)**
  - [x] 5.1 Add a fifth broker-facing seam, `broker_state_reader: BrokerStateReader =
    read_broker_state`. Then:
    - `_phase_node_connect` calls `capture_local_positions` just before `run_async`;
    - `_phase_reconcile` runs `reconcile_at_startup` on the loop;
    - `_phase_trading` opens with `require_reconciled`.

    Update the class docstring's seam list. Measure the file after each edit, against the
    ≤ 500 cap.
  - [x] 5.2 Doubles, all defaulted so every existing caller keeps passing:
    - `_TestCache.positions_open()` / `orders_open()` / `instrument()` return configurable
      values;
    - the exec `_TestEngine` gets F9's attributes and a recording `reconcile_execution_report`;
    - a `flat_broker_state_reader` helper in `tests/component/doubles/`.
  - [x] 5.3 Add `broker_state_reader=flat_broker_state_reader` to each runner-building helper
    (F14's eight files), next to their stub `account_verifier`.
  - [x] 5.4 Rewrite `TestTheEpicFourPlaceholders` deliberately. `warmup` still makes no call on
    the node. `reconcile` is no longer a placeholder: its accesses are exactly the read set plus
    no `reconcile_execution_report` on a clean cache. Name the change in the docstring.
  - [x] 5.5 The AC #1, #4 and #6 tests above, in `test_session_runner_phases.py`, or a new
    `test_session_runner_reconcile.py` if that file's size warrants it.

- [x] **Task 6 — Guard lists and governance (same change as the module that triggers them)**
  - [x] 6.1 `live_startup_reconcile.py` goes in `NODE_FACING_MODULES` and `TestImportPurity.MODULES`,
    each with a reason. It does **not** go in `STOP_PATH_MODULES`: add a comment there, following
    the `live_broker_state` precedent. It is a startup read-and-correct that nothing in teardown
    calls.
  - [x] 6.2 `_STDLIB_AND_FIRST_PARTY` is expected to need no edit. Prove it by **running**
    `tests/integration/core/test_epic1_ac_node.py`. No `from __future__ import annotations`.
  - [x] 6.3 `LIVE_MODULE_GLOBS` picks up the module. Run `test_live_dependency_invariance.py`,
    `test_order_path_has_no_retry.py` and `test_live_stop_path_is_inert.py`.
  - [x] 6.4 `test_exit_outcome_markers.py` passes without growing `UNMARKED`.
  - [x] 6.5 Size caps: new modules under every cap. Regenerate the baseline for
    `LiveSessionRunner` with the D-K reason. The file allowlist is unchanged (three entries).

- [x] **Task 7 — Operator surface and docs**
  - [x] 7.1 `live start --help` (`src/cli/commands/live.py:360-361`) and `README.md:219`: replace
    "`reconcile` is a no-op placeholder" with one sentence covering D-A, D-D and D-E. Exit 1 now
    also covers "reconciliation refused to let the session trade". A `CliRunner` test
    (`tests/unit/cli/commands/test_live_cli.py`) proves a `ReconciliationFailedError` out of
    `runner.run()` prints the instrument and quantities, exits 1, and releases the row.
  - [x] 7.2 Docstrings:
    - `live_session_phases.py:24-27` (the "clean phase log is not evidence" limit is now false
      for `reconcile`, so restate it);
    - the runner (`:18`, `:55-58`, `_phase_reconcile`);
    - `live_session_steady_state.py:430-433` (D-J wording);
    - `live_cache.py:12-19`;
    - `docs/agent/nautilus.md:182-187`. "Nothing enforces Redis's disposability" is now false.
      Add a short "Startup reconciliation" section with F2/F3/F6/F7 and D-D's remedy.
  - [x] 7.3 `live_node_probe.py --reconcile` (D-M).

- [x] **Task 8 — Live verification (informational only, never a gate)**
  - [x] 8.1 Write **Procedure P16** in `docs/qa/phase3-live-verification.md`, after P15. Keep the
    parallel-story renumbering note: another Epic 4 story may claim P16 concurrently, and the
    integrator renumbers. *(Integration, 2026-09-27: Story 4.6 did claim P16 and merged first, so
    this story's procedure is **P17**; every reference below uses the new number.)*
    - **P17a** is read-only: `live_node_probe.py --run-seconds 1 --verify-account --reconcile`.
      Pass criteria:
      1. `RESULT: ok … reconcile=ok`;
      2. positions match TWS;
      3. `elapsed_ms` < 30000;
      4. the account is masked;
      5. `order.submitted` count is 0.
    - **P17b** needs a Redis-cached position the broker does not hold, e.g. `p7-fill-0901`
      (`live start`). It starts strategies if reconciliation passes, so it is **defined, not
      run** by the harness.
  - [x] 8.2 If a Gateway is reachable **and** the worktree has the settings the probe needs, run
    P17a and record it. Otherwise record "defined, not run" with the reason. Never `--real-money`.
    Never read or touch `.env`, docker, or the kill/reconnect/connection-loss scripts. Never
    submit, open, close or flatten.

- [x] **Task 9 — Routed debt and dispositions (`deferred-work.md`, new "Deferred from: story-4.2")**
  - [x] 9.1 Dispose of each item routed to 4.2:
    - the stale-cache bug: fixed (D-D refuses the `p7-fill-0901` shape, D-E resolves the
      synthetic shape);
    - D-D persist-skip: confirmed (D-L);
    - `confirm_state_reestablished`: sanity-checked, dormant, routed to 4.3 (D-J);
    - "native reconciliation cannot fail on a failed position read": closed by comparing against
      `read_broker_state`;
    - the joiner-poisoning 30 s timeout and the stranded cancelled request: the phase fails on
      them explicitly (`POSITIONS_UNANSWERED`), recovery needs a restart, routed to 4.3's runtime
      read;
    - Story 2.4's "Nothing enforces Redis's disposability": closed.
  - [x] 9.2 → **Story 4.5**:
    - F7's triple-position shape on every mid-position restart;
    - F10's unfiltered `cache.positions` read, which is an NFR14 hazard whenever a synthetic
      position shares a traded instrument;
    - F4's `filled_qty <` failure on a shrunk re-entry;
    - D-D's refusal, which 4.5 may relax.
  - [x] 9.3 → **Story 4.3**: the stranded-`ACCEPTED`-order startup stall (`p7-position-test`,
    portfolio-init timeout) is open-order reconciliation and is not fixed here.

    Also unowned and named: `node:connect`'s timeout message cannot say *which* of the three
    pre-trader waits failed. The kernel ordering (F1) would allow it. Owner: whoever next touches
    `await_trader_started`.

- [x] **Task 10 — Mutation sweep (each applied, target tests run, file restored; record kill/survive)**
  - M1 compare net over strategy-owned only (drops synthetics);
  - M2 compare with a tolerance / `float`;
  - M3 drop the strategy-position check;
  - M4 resolve before the strategy check (residue written on a refused start);
  - M5 `avg_px_open=None` always;
  - M6 skip the re-verify;
  - M7 drop one D-B attribute check;
  - M8 drop the `require_reconciled` latch;
  - M9 snapshot taken after `run_async` (F-shape: framework fixes invisible);
  - M10 swallow `BrokerStateUnavailableError` as flat;
  - M11 log the raw account;
  - M12 `ReconciliationFailedError` marked `BROKER_UNREACHABLE`.

- [x] **Task 11 — Gates**
  - [x] 11.1 `uv run ruff format .`, `uv run ruff check .`, `make typecheck`.
  - [x] 11.2 `make test-unit`, `make test-component`, integration `--forked` for
    `tests/integration/core/`.
  - [x] 11.3 `grep -rn "is_live" src/core/strategies/` gives 0. `git diff --stat` shows the D-N set
    untouched.

### Review Findings

Code review 2026-09-27 (Blind Hunter + Edge Case Hunter + Acceptance Auditor), run by hand after
the harness's review turn hit `TimeoutExpired` (3600 s) on this story's ~4,200-line diff. Reviewed
**after** integrating Story 4.6 (merged first): this story's module was renamed
`live_reconcile.py` → `live_startup_reconcile.py` (4.6 owns an unrelated `src/core/live_reconcile.py`,
the on-demand check) and its procedure renumbered P16 → P17. 0 decision-needed (D-D and D-E already
rule every policy question raised), 14 patch, 8 defer, 20 dismissed.

- [x] [Review][Patch] Size-cap ratchet red after integration: the rename made `ruff format` split the `capture_local_positions` call over three lines (`LiveSessionRunner` 426 > baseline 424) — import the function directly instead of re-baselining [src/core/live_session_runner.py:574]
- [x] [Review][Patch] A framework refusal mid-loop leaves earlier rows' corrections applied, contradicting `_correct`'s "a refusal leaves nothing of ours behind"; an exception from `reconcile_execution_report` escapes untyped with no `reconcile.discrepancy` record [src/core/live_startup_reconcile.py:300]
- [x] [Review][Patch] `make_qty`/`make_price` run inside the write loop: a broker quantity finer than `size_precision` is silently rounded (then `DISCREPANCY_REMAINS` after a write) or raises an untyped `ValueError` — build and validate every report before the first write [src/core/live_startup_reconcile.py:347]
- [x] [Review][Patch] An unresolved broker row (`IB-CONID-*`) for an instrument a strategy holds is refused as `STRATEGY_POSITION_CONTRADICTED`, with a remedy telling the operator to abandon the session — refuse `UNRESOLVABLE_DISCREPANCY` first (still before any write) [src/core/live_startup_reconcile.py:245]
- [x] [Review][Patch] The unresolvable refusal logs every row `resolution=refused reason=unresolvable_discrepancy`, including correctable ones — log and carry only the unresolvable rows [src/core/live_startup_reconcile.py:313]
- [x] [Review][Patch] Operator message ends "…engine cache.. NVDA.NASDAQ…" (`_REMEDY` ends in a period and `__init__` appends another) [src/core/live_startup_reconcile.py:141]
- [x] [Review][Patch] `reconcile_instrument_ids` set to `None` raises an untyped `TypeError` from `list(None)` instead of passing as "no filter" [src/core/live_startup_reconcile.py:201]
- [x] [Review][Patch] README / `live` CLI docstring say "`reconcile.ok` means 0 discrepancy" while the record's `discrepancies` field counts what was resolved on the way (P17a's own expected output shows `discrepancies=1`) — say "0 remaining" [README.md, src/cli/commands/live.py]
- [x] [Review][Patch] P17a criterion 2 and the probe docstring promise every broker position appears as `resolution="framework"`; a position the framework could not import appears as `resolution="broker"` (or refuses) — word it so a correct run cannot be judged a fail [docs/qa/phase3-live-verification.md, scripts/diagnostics/live_node_probe.py]
- [x] [Review][Patch] The probe accepts `--reconcile` without `--verify-account`, running reconcile without Layer 2 (D-M / AR39 order) [scripts/diagnostics/live_node_probe.py]
- [x] [Review][Patch] AC #2 pin missing: the built config's `exec_engine.load_cache is True` [tests/component/core/test_live_startup_reconcile_engine.py]
- [x] [Review][Patch] AC #2 test missing: a strategy-owned working order pre-loaded in the cache survives reconciliation still open (AR25) [tests/component/core/test_live_startup_reconcile_engine.py]
- [x] [Review][Patch] AC #2 AST check missing: `live_startup_reconcile.py`'s only engine mutation is `reconcile_execution_report` [tests/component/core/test_live_startup_reconcile_engine.py]
- [x] [Review][Patch] AC #6 / Task 4.1 assertion gaps: NOT_RECONCILED and broker-unreadable runner cases don't assert exit code / `mark_stopped`; no `BrokerStateAdapterError` case through `runner.run()`; no elapsed bound with a sleeping reader [tests/component/core/test_session_runner_phases.py, tests/unit/core/test_live_startup_reconcile.py]
- [x] [Review][Defer] A normal mid-position restart passes with `S +10 / EXTERNAL +10 / INTERNAL-DIFF −10` (net matches) although this story's own deferred notes call that state an order hazard for `sma_crossover` [src/models/position_reconciliation.py] — deferred, routed to Story 4.5 (strategy adoption)
- [x] [Review][Defer] Open orders are counted, never compared with the broker's; a stale open order passes [src/core/live_startup_reconcile.py:268] — deferred, routed to Story 4.3 (open-order reconciliation)
- [x] [Review][Defer] A cached working order that fills, or a manual TWS trade, between the broker read and the cache comparison makes the phase correct toward a stale snapshot; `framework_resolved` mislabels such changes [src/core/live_startup_reconcile.py:240] — deferred, runtime alignment is Story 4.3's
- [x] [Review][Defer] A broker-only position with no average price is imported by the framework at a fill price of 0 (F5's fallback) [src/core/live_startup_reconcile.py:361] — deferred, rare (IB reports `avgCost` for every held position); revisit with 4.3
- [x] [Review][Defer] The D-B settings check runs after `node:connect`; with `generate_missing_orders=False` the kernel's own pass fails first and the session exits 4 blaming the broker [src/core/live_startup_reconcile.py:188] — deferred, config-time check belongs in the node builder
- [x] [Review][Defer] Any unresolvable broker position — including a manual holding in an instrument this session never trades — refuses every start of every session (D-E, as specified) [src/core/live_startup_reconcile.py:313] — deferred, policy revisit with Story 4.5
- [x] [Review][Defer] Duplicate broker rows for one instrument id collapse in `held = {…}` [src/models/position_reconciliation.py:154] — deferred, pre-existing in Story 4.1's reader contract
- [x] [Review][Defer] A stop signal that arrives while `reconcile` refuses demotes the refusal to a clean stop (exit 0) [src/core/live_session_runner.py:421] — deferred, pre-existing (Story 3.1 stop semantics; nothing trades either way)

## Dev Notes

### The current surface — exact extension points

- `src/core/live_session_runner.py`:
  - `_phase_reconcile` (`:576-581`) is the body to fill;
  - `_phase_node_connect` (`:549-563`) is the snapshot site, before `create_task(run_async())`;
  - `_phase_trading` (`:694-721`) takes the latch;
  - `__init__` (`:174-248`) takes the seam;
  - `stop_degraded_strategies` (`:1050-1127`) moves out first.
- `src/core/live_broker_state.py`: `read_broker_state(node, *, log, timeout_seconds=20.0)`,
  `find_ib_exec_client(node)`, `BrokerStateUnavailableError`/`BrokerStateAdapterError`. Use them
  as they are, zero-diff.
- `src/models/broker_state.py`: `BrokerState`, `BrokerPosition` (signed `quantity`,
  `average_price`, `instrument_resolved`).
- `src/core/live_session_phases.py`: `phase()` already emits the pair and re-raises. Do not wrap
  it again.
- `src/core/exit_outcome.py` / `live_check.classify_failure`: D3 markers are read from the MRO.

### Scope boundaries — do NOT build

- Continuous or runtime reconciliation, reconnect re-reconciliation, a
  `confirm_state_reestablished` caller, or an open-order reconciliation fix (all Story 4.3).
- Strategy position adoption or external-order claims, or any strategy edit (Story 4.5).
- `ntrader live reconcile` or `reconciliation_service.py` (Story 4.6).
- Corporate-action handling (Story 4.7).
- A new exit code, a DB column, a migration, or a `SessionRecordPort` method.

### Hazards (each has bitten a previous story, or will)

- **Guard lists after a split** (CLAUDE.md): the D-K destination is already on all three lists.
  Re-check them anyway and run them.
- **A multi-line statement counts every line**: a wrapped import or call costs 3–5. Measure
  rather than estimate.
- **`asyncio.wait_for` cancels shared adapter futures** (Story 4.1 D-C). The reader already
  avoids it. Do not wrap `read_broker_state` in `wait_for`.
- **`TestLiveNode` is shared by `test_live_check_driver.py`, `test_epic1_ac_cli.py` and
  `test_epic1_ac_data.py`**, so every double addition must be defaulted.
- **A test that cannot fail** (Epic 2 retro): every "no call happened" assertion needs a sibling
  that proves the spy records calls.

### Testing standards summary

Unit covers the models module and `live_startup_reconcile` against stubs. Component covers the real
`LiveExecutionEngine`/`Cache` and the runner against `TestLiveNode`, with no `TradingNode` and the
C-logging guard fixture. Integration `--forked` is only the existing `test_epic1_ac_node.py` scan
(Task 6.2). Markers go on every test, TDD red first, and each mutation is recorded.

### Project Structure Notes

- New: `src/models/position_reconciliation.py`, `src/core/live_startup_reconcile.py`,
  `tests/unit/models/test_position_reconciliation.py`, `tests/unit/core/test_live_startup_reconcile.py`,
  `tests/component/core/test_live_startup_reconcile_engine.py`, and optionally
  `tests/component/core/test_session_runner_reconcile.py`.
- Every live module lives in `src/core/` (Story 3.5's placement precedent), and domain values in
  `src/models/` (Story 4.1).

### References

- `_bmad-output/planning-artifacts/prd-epic4-scope.md` (Story 4.2 ACs; debt routed to 4.2)
- `_bmad-output/planning-artifacts/epics.md:1512-1546`
- `_bmad-output/planning-artifacts/architecture.md` (D2 `:239-246`; D7 `:291-294`; AR39 `:405`;
  log naming `:434-440`)
- `_bmad-output/implementation-artifacts/deferred-work.md` (`p7-fill-0901` `:2556-2572`;
  Story 3.6 D-D persist-skip `:2682-2688`; Story 4.1 routing `:3139-3203`; monitor dormancy
  `:615-640`, `:1354-1360`)
- `_bmad-output/implementation-artifacts/4-1-read-the-brokers-authoritative-view-of-positions-and-cash.md`
- `docs/qa/phase3-live-verification.md` (P10 result row: the live `p7-fill-0901` observation)

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), BMAD dev-story workflow, 2026-09-27.

### Debug Log References

- **Task 0 (standing checks).** Head `32f852f`, clean tree. `ruff check` and `make typecheck` were
  clean. Baselines: unit **2851 passed**; component **1629 passed, 16 skipped**. `measure_module`:
  runner file **499/500**, `LiveSessionRunner` 414, `__init__` 44, `run` 66.
- **Task 1 (measured, real `LiveExecutionEngine` + `Cache` + `Portfolio`, NETTING IB-shaped
  client).** Probe script in `/tmp/p42/`, not committed. Every premise held:
  - `attrs`: a default `LiveExecEngineConfig()` gives `reconciliation=True`,
    `generate_missing_orders=True`, `filter_position_reports=False`,
    `reconciliation_instrument_ids=[]`.
  - 1.1 (F6): a flat report against S +22 leaves S +22 and adds `INTERNAL-DIFF −22`. Net and
    portfolio net are both 0. **Applied synchronously**, with no loop tick needed (1.3 answered).
    The portfolio follows (1.5 answered).
  - 1.2: a LONG 5 report with `avg_px_open` fills at 101.25. Without it, and with no quote, it
    fills at **0.0**.
  - 1.4A: S +10 against broker +10 leaves the triple S +10, `EXTERNAL` +10, `INTERNAL-DIFF` −10,
    net 10.
  - 1.4D: S +22 against broker +10 leaves S +22, `EXTERNAL` +10, `INTERNAL-DIFF` −22, net 10. Net
    agrees; the strategy does not.
  - 1.4C (`p7-fill-0901`): native returns `ok=True` and the phantom is untouched.
    `position_calls == [None]`: the per-cached-position sweep **never fired**.
    **Correction to F2/F3:** the sweep calls `positions_open(venue)` with the IB client's venue
    `INTERACTIVE_BROKERS`, while positions carry `NASDAQ`, so it matches nothing. The adapter
    ignoring its filter is a second, independent reason.
  - 1.4E: a stale `EXTERNAL` +4 survives native reconciliation. `create_flat` adds
    `INTERNAL-DIFF −4`, net 0.
  - 1.4F: a filtered instrument returns `True` with nothing changed.
  - 1.4S (**new, worse than F7 predicted**): a second restart after the broker shrank 10 → 4
    triggers `_should_update → True`, then `_generate_order_updated`, then a **Rust panic**
    (`Quantity` raw out of range), and the process aborts. It does not return `False`. It is routed
    to 4.5 as HIGH and pinned by a subprocess canary.
- **Task 2 (split).** The runner went 499 → **481** (−18 for the function, −1 for the `Trader`
  import, +1 for the imported name). After the move, the guard-list, stop, size-cap and forked
  integration suites were green.
- **Task 5.** The runner is at **494/500**. `LiveSessionRunner` 424 (baseline 414 → 424, with a
  reason), `__init__` 47, `run` 66 (unchanged). `live_startup_reconcile.py` 229, largest function 34.
  `position_reconciliation.py` 73.
- **Intermittent component failure found and root-caused (Task 11).** About two in seven full
  parallel runs failed an order-path test with `NoStrategyStartedError`. Cause:
  `TestLiveNode(run_seconds=0.01)` counted from `run_async()`'s start, so the 10 ms node raced
  the whole startup. The new `reconcile` phase runs the loop once more after `gate:account`, and
  under load the timer fired before `trading`. The runner then correctly refused to start
  strategies on a node whose run task had ended.

  Fix: a double-only, opt-in `run_seconds_from_first_strategy`, set by the five session-runner
  helpers. `test_live_check_driver.py` keeps the from-start semantics, because a check never
  starts a strategy (the first, non-opt-in version broke it, measured). After the fix: **5/5 clean
  full component runs**.
- **Task 8.** P17a was attempted via `runpy` (the script needs the repo root importable, which is
  a pre-existing property). It returned `RESULT: fail reason=config_error … TWS_ACCOUNT is not
  set` before any socket opened, because this worktree has no `.env`. Recorded as defined, not
  run.
- **Gates (final).** `ruff format --check` and `ruff check` clean; `make typecheck` clean (106
  files). Unit **2927 passed** (+76). Component **1662 passed, 16 skipped** (+33), five
  consecutive clean runs. `tests/integration/core --forked` **92 passed, 2 skipped**.
  `grep is_live src/core/strategies/` → 0. The D-N zero-diff set shows an empty `git diff --stat`.

### Completion Notes List

- **What shipped.** The `reconcile` phase now has a body.
  `src/core/live_startup_reconcile.reconcile_at_startup` does the following, in order:
  1. enforces the broker-ward settings on the **running** exec engine (D-B);
  2. reads the broker through Story 4.1's `read_broker_state`;
  3. compares with the stdlib-only `src/models/position_reconciliation.compare_positions`, on net
     per instrument, exactly, plus a strategy-own-share check (D-C);
  4. refuses a contradicted strategy position before writing anything (D-D, **PO ruling
     2026-09-27: option A**);
  5. corrects everything else broker-ward through the framework's public
     `reconcile_execution_report`, passing the broker's average price (D-E);
  6. re-verifies 0 discrepancy;
  7. logs `reconcile.ok`.

  A `resolution="broker"` record is emitted only once the re-read proves the correction took.
  Supporting pieces:
  - A pre-reconciliation snapshot, taken just before `run_async()`, turns the framework's silent
    fixes into `resolution="framework"` records (D-F).
  - `_phase_trading` opens with `require_reconciled` (D-I).
  - Failures raise `ReconciliationFailedError` (exit 1, operator-safe). Broker-read failures
    propagate unchanged (exit 4/1). No new exit code.
- **`confirm_state_reestablished` stays dormant (D-J).** The sanity check's findings are recorded
  in `deferred-work.md`, and `live_startup_reconcile` joined the zero-callers scan.
- **Story 3.6's D-D persist-skip policy is confirmed, not flipped (D-L).** `SYNTHETIC_STRATEGY_IDS`
  is pinned equal to `RECONCILIATION_STRATEGY_IDS` by import.
- **Budget split first (D-K).** `stop_degraded_strategies` moved to `live_session_node.py`, which
  is on all three guard lists. Its old import path still works.
- **Operator surface.**
  - `live start --help`, README and the docstrings in `live_cache`, the phases module, the runner
    and the steady state are updated;
  - `docs/agent/nautilus.md` gained a "Startup Reconciliation" section with the operator remedy;
  - `live_node_probe.py --reconcile` is read-only: no strategy, in-memory cache;
  - Procedure P17 is written. P17a was defined, not run (no `.env`). P17b is defined for the
    operator only.
- **Mutation sweep: 13 of 13 killed.**
  - M1–M12 as specified, plus M13, the abort canary without the shrink.
  - **M2 survived first**, which exposed a real gap. The exactness test used a strategy-owned
    position, so the strategy check hid a tolerance on net. It was closed by
    `test_the_net_comparison_is_exact_when_no_strategy_owns_the_position`.
  - The raw-account test was hardened to scan every path (refused, corrected, framework-refused,
    remains, hydrated) before M11 was run.
- **Story-text corrections (recorded, not silently absorbed).**
  - F14 said eight runner-building files. Five construct `LiveSessionRunner`. The two
    `test_epic1_ac_*` files drive `run_live_check`: the seam was added there by mistake,
    reverted, and they pass unmodified.
  - Task 7.1's "releases the row": the release is the runner's own `finally`, proven through
    `runner.run()` (`record.calls[-1] == "mark_stopped"`). The CLI test covers the message and
    exit 1.
  - F2/F3's mechanism is stronger than drafted (venue mismatch), and F7's shrunk re-entry
    **aborts the process** rather than returning `False`.
- **Routed** (`deferred-work.md`, "Deferred from: story-4.2"):
  - Story 4.5: the triple-position restart shape; `sma_crossover`'s unfiltered position read (an
    NFR14 hazard); the shrunk-re-entry abort (HIGH).
  - Story 4.3: the stranded-`ACCEPTED`-order stall.
  - Unowned, named: the `node:connect` timeout message's cause attribution.

### File List

New:
- `src/core/live_startup_reconcile.py`
- `src/models/position_reconciliation.py`
- `tests/unit/core/test_live_startup_reconcile.py`
- `tests/unit/models/test_position_reconciliation.py`
- `tests/component/core/test_live_startup_reconcile_engine.py`
- `tests/component/scripts/test_live_node_probe_reconcile.py`
- `_bmad-output/implementation-artifacts/4-2-reconcile-against-the-broker-before-any-strategy-trades.md`

Modified:
- `src/core/live_session_runner.py` — seam, snapshot, phase body, latch, docstrings; `stop_degraded_strategies` moved out
- `src/core/live_session_node.py` — `stop_degraded_strategies` moved in
- `src/core/live_session_phases.py`, `src/core/live_session_steady_state.py`, `src/core/live_cache.py` — docstrings only
- `src/cli/commands/live.py` — `live start` help text only
- `scripts/diagnostics/live_node_probe.py` — `--reconcile`
- `README.md`, `docs/agent/nautilus.md`, `docs/qa/phase3-live-verification.md` (Procedure P17)
- `_bmad-output/implementation-artifacts/deferred-work.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml` (4-2 key only)
- `tests/component/doubles/__init__.py`, `tests/component/doubles/test_live_node.py`
- `tests/component/core/test_session_runner_phases.py`, `test_session_runner_stop.py`,
  `test_session_runner_strategy_failure.py`, `test_session_runner_order_path.py`,
  `test_session_runner_warmup.py`, `test_session_steady_state.py`
- `tests/component/scripts/test_live_node_probe_broker_state.py`
- `tests/unit/cli/commands/test_live_cli.py`
- `tests/unit/core/test_live_node_never_exits.py`, `tests/unit/core/test_live_stop_path_is_inert.py`
- `tests/unit/governance/test_size_caps.py` — `LiveSessionRunner` baseline 414 → 424, with a reason

## Change Log

| Date | Change |
| ---- | ------ |
| 2026-09-27 | Story created (ready-for-dev). D-D put to the PO and ruled option A (refuse). |
| 2026-09-27 | Implemented. All tasks done; 13/13 mutations killed; every tier green. Status → review. P17a defined, not run (no `.env`); P17b is operator-only. |
| 2026-09-27 | Code review (by hand, after the harness review turn timed out). Integrated onto Story 4.6: module renamed `live_startup_reconcile.py`, procedure renumbered P17. 14 patches applied (reports validated before any write, typed mid-loop refusal, unresolved-before-contradiction, probe requires `--verify-account`, missing AC #2/#6 pins), 8 deferred. Every tier green. Status → done. |
