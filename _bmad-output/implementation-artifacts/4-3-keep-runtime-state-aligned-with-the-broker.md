# Story 4.3: Keep Runtime State Aligned with the Broker

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want alignment maintained while the session runs, not just at startup,
so that a multi-week session does not drift away from reality between restarts.

## Why this story is shaped the way it is

Read this before designing anything. The epic text reads like "switch on Nautilus's continuous
reconciliation and log what it does". Each half of that is wrong in a specific way. Measured at
drafting (2026-09-27, installed `nautilus-trader 1.220.0`, head `78d59a1`). Wheel paths are relative
to `.venv/lib/python3.11/site-packages/nautilus_trader/`; ripgrep skips `.venv` (gitignored), so
search it with `grep -n` on explicit paths.

**1. Nautilus 1.220.0 has no continuous *position* reconciliation.** Its one continuous task
(`_continuous_reconciliation_loop`) runs two *order* checks: the in-flight sweep (already on since
Story 3.4) and an optional open-order consistency check (`open_check_interval_secs`, off today). No
native loop ever compares positions again after startup (F1). With the IB adapter, the only runtime
position path is the **adapter's own** `positionUpdate` stream (F4) — and that is the path that
produces the P11/P12 double-count phantom on every strategy entry (F5), and it is blind to a position
going flat (it returns early on quantity 0). So "use the native machinery" cannot mean "turn a
setting on and watch": the order half can be switched on; the position half has to be built as a
verified cycle that resolves *through* the framework's public entry point, exactly the shape Story
4.2 already proved at startup (D-A, D-C).

**2. The framework resolves silently, and the adapter's detector is noisy.** An inferred
reconciliation fill is an `OrderFilled(reconciliation=True)` on a synthetic owner (F6); nothing in
this codebase names it as a discrepancy. If the adapter's detector stays live, every strategy entry
logs two "discrepancies" that are really one race, which trains the operator to ignore
`reconcile.discrepancy` — the one record FR35/AR41 needs them to read. D-B removes the race at its
source; D-C makes every runtime correction ours to log.

**3. "Re-established before trading permission returns" needs the grant to go live — and the order
gate to change.** `confirm_state_reestablished` has had zero production callers since Story 1.6, and
Story 3.2's order gate (`submission_withheld`) deliberately does **not** withhold in `RECOVERING`,
because without a grant `RECOVERING` was every session's permanent healthy state (F7). The moment a
grant exists, `RECOVERING` means "socket back, state not re-established", and the gate must withhold
there (NFR10). Story 4.2's D-J kept the call dormant precisely because a startup-only grant arms the
halt clock that only a *later* confirm clears; this story ships both calls together (D-F).

**4. The runner file is at 495 of a hard 500 executable statements, and `SessionSteadyState` is at
its 120 ratchet ceiling** (F11). The wiring does not fit without a budget split first (D-I, the Story
4.2 D-K precedent: split before the edit, measure after each step).

### Measured facts — cite, don't re-derive

Task 1 re-measures F1, F3, F5 and F6 against a real `LiveExecutionEngine` before production code is
written.

| # | Fact | Where |
|---|---|---|
| F1 | `LiveExecutionEngine._on_start` creates **one** `continuous_reconciliation` task when `inflight_check_interval_ms > 0` **or** `open_check_interval_secs` is set (`get_reconciliation_task()` exposes it). The loop sleeps `min(intervals)` and runs `_check_inflight_orders` (only `cache.orders_inflight()`: `SUBMITTED`/`PENDING_*`; after `inflight_check_retries` it resolves **locally**) and, when configured, `_check_orders_consistency`: calls every client's `generate_order_status_reports(open_only=…)` and reconciles **only** reports whose `report.is_open` differs from `client_order_id in cache.client_order_ids_open()`. A cached-open order the venue no longer lists produces no report, so it is never reconciled here. **No loop re-checks positions.** Cancelled in `_on_stop`. | `live/execution_engine.py:405-423, 449-454, 583-686, 687-755`; `live/config.py:106-131, 173-178` |
| F2 | Today's session config: `reconciliation=True`, in-flight `2000 ms / 5000 ms / 5 retries` (pinned by Story 3.4), `open_check_interval_secs` **unset on purpose**, with the comment routing it to Story 4.3. | `src/core/live_node_builder.py:130-157, 447-453` |
| F3 | IB `generate_order_status_reports` **ignores `open_only`**: it calls `get_positions` and fabricates a `FILLED` `OrderStatusReport` per non-zero position (`client_order_id = venue_order_id = instrument id`), then appends `reqOpenOrders` results parsed by `_parse_ib_order_to_order_status_report`, which builds `ClientOrderId(ib_order.orderRef)`. **An empty `orderRef` raises `ValueError`** (measured at drafting: `ClientOrderId('')` → `'value' string was invalid`), which fails the whole list; `reqOpenOrders` returns only the connecting client id's orders unless that id is `0`, so a manual TWS order reaches it only for client id 0. Under F1's consistency rule a fabricated `FILLED` report is `is_open=False` and its id is never an open id, so it should be a no-op — Task 1.1 proves it. | `adapters/interactive_brokers/execution.py:300-372, 374-445`; `…/client/order.py:100-122` |
| F4 | The IB adapter's runtime position path: `_connect` subscribes `positionUpdate-{account}` to `self._on_position_update`, which does `create_task(self._handle_position_update(p))` — looked up on `self` at call time, so an **instance** attribute shadows it. `_handle_position_update` returns silently on quantity `0` (pops `_known_positions`), skips when `new == _known_positions.get(conId, 0)`, and otherwise logs `External position change detected (likely option exercise)` and sends a `PositionStatusReport` straight to the engine. `_known_positions` is updated from `execDetails` **only if the conId is already tracked**. | `adapters/interactive_brokers/execution.py:194-234, 248-261, 1355-1458` |
| F5 | The double-count phantom (P11 and P12, live, identical): flat ⇒ conId untracked; the broker's position update `0 → 22` arrives before `execDetails` ⇒ report LONG 22 ⇒ engine: cache net 0 ≠ 22 ⇒ `INTERNAL-DIFF +22`; the strategy's real fill ⇒ net 44; `execDetails` now adds 22 to the tracked 22 ⇒ 44; the next update `22 ≠ 44` ⇒ report ⇒ `INTERNAL-DIFF −22`. One spurious round trip per entry, `holding_period_seconds=0`, persist-skipped by D-D of Story 3.6. | F4; `deferred-work.md:2817-2840`; `logs/p11-20260921.log:235-274` |
| F6 | Every inferred reconciliation fill is `OrderFilled(..., reconciliation=True)` on the order's (synthetic) strategy id, priced `report.avg_px` → `report.price` → `make_price(0.0)`. | `live/execution_engine.py:1602-1670` |
| F7 | `ConnectionMonitor`: `trading_permitted = CONNECTED and not stale`; `submission_withheld = (state ∉ {CONNECTED, RECOVERING}) or stale` — `RECOVERING` does **not** withhold, by Story 3.2's design "pending Epic 4's grant"; the order wrapper reads `bool(monitor.submission_withheld)`. The first `_grant_permission` sets `_has_ever_connected`, which arms `connection.lost` → `_unavailable_since` → `connection.halted`; `connection.restored` fires only on a grant after a loss; `_unavailable_since` clears only on a grant. `confirm_state_reestablished` has **zero production callers**, pinned by `test_confirm_state_reestablished_is_never_called_in_this_story`; its only other caller is `scripts/diagnostics/live_connection_loss_probe.py` (do not touch). | `src/core/live_connection_monitor.py:176-216, 268-353`; `src/core/live_order_path.py:253-272`; `tests/component/core/test_session_steady_state.py:501-522` |
| F8 | The steady-state tick, in order: `_write_activity` → `_write_strategy_failures` → `_write_order_rejections` → `_observe_connection` (narrates `connection.state_changed`) → `_warn_if_no_bars`. `SessionReclaimedError` is the only exception allowed out; the heartbeat task's failure reaches `_serve` via `self._heartbeat.result()`. | `src/core/live_session_steady_state.py:279-300, 421-472`; `src/core/live_session_runner.py:859-884` |
| F9 | `read_broker_state` logs `reconcile.broker_state_retrieved` at INFO on **every** success and `reconcile.broker_state_failed` at ERROR on every failure. At a 60 s cadence that is ~390 INFO lines per RTH day — AC #3's "drown the trading log". The reader takes a `log=` argument. | `src/core/live_broker_state.py:102-104, 178-247, 563-581` |
| F10 | Story 4.2's reusable pieces: `cached_positions(cache)`, `compare_positions(...)` (stdlib, exact), `_position_report(row, held, cache, account_id)` (validates before any write; passes the broker's average price — without it the fill prices at 0), `_correct(...)`, `_log_discrepancy(...)`, `_emit(...)`. `ReconciliationFailedError`'s message hard-codes `"Startup reconciliation refused to let this session trade"`. | `src/core/live_startup_reconcile.py:119-158, 161-174, 311-457` |
| F11 | Sizes (`measure_module`): `live_session_runner.py` **file 495/500**, `LiveSessionRunner` 424 (baseline 424), `run` 66 (baseline); `SessionSteadyState` **120** (baseline 120 — no headroom), `_warn_if_no_bars` 15; `ConnectionMonitor` 118 (baseline 118); `OrderEventObserver` 165 (baseline); `build_trading_node_config` 128 (baseline 128). `_flush_order_rejections` 16, `_flush_contained_failures` 25. The baseline is exact on regeneration (`test_regenerating_the_baseline_reproduces_it_exactly`). | `tests/unit/governance/test_size_caps.py` |
| F12 | `OrderTriggered` with the IB adapter: `MAP_ORDER_STATUS` maps no IB status to `TRIGGERED`, and reconciliation emits `OrderTriggered` only for a `TRIGGERED` report or a `CANCELED`/`EXPIRED` report with `ts_triggered > 0`, which the IB parser never sets. **Unreachable through the IB adapter at 1.220.0.** | `adapters/interactive_brokers/parsing/execution.py:91-101`; `live/execution_engine.py:1193-1218` |
| F13 | IB subscription-killing codes (D6): `366`/`10189`/`102` — the adapter cancels **and re-issues** the subscription itself; `10182` — clears `_is_ib_connected`, and the 1 s watchdog degrades, sleeps 5 s, reconnects and `_resume`s (`_resubscribe_all`) **unless** the flag is re-set first; `162` — "Unknown subscription error", logged, nothing done (environmental: a competing login); `1101` ("restored, data lost") — sets `_is_ib_connected` with **no** resubscription (Story 1.6 review). None of these reach any code of ours. | `adapters/interactive_brokers/client/error.py:90-190`; `…/client/client.py:292-391`; `deferred-work.md:678-707, 1975-1991, 2099-2123` |
| F14 | An IB order error outside `ORDER_REJECTION_CODES`/`202` is logged and dropped. A `SUBMITTED` order then reaches the in-flight sweep after `inflight_check_threshold_ms` (5 s): `QueryOrder` → `generate_order_status_report` → not in `reqOpenOrders` → the adapter's **local** `Cancelled "Not found in query"` → `OrderCanceled`. An `ACCEPTED` order is not in flight and is never swept. | `…/client/error.py:218-236`; `execution.py:263-298`; `live/execution_engine.py:645-686` |
| F15 | The node builder already patches the IB exec client after construction and before the node starts (`install_avg_px_serialization_fix` inside `InteractiveBrokersLiveExecClientFactory.create`), with a **load-bearing class name**. That is the precedent and the seam for D-B. | `src/core/live_node_builder.py:623-662`; `src/core/live_exec_avg_px.py` |

### Design decisions (disclosed here, not discovered in review)

> **Three decisions were put to the PO (Allay) at creation and ruled 2026-09-27: 1A 2A 3A** — D-B
> (suppress the adapter's position-update reports), D-D (refuse and stop on a runtime strategy
> contradiction) and D-J's two re-routes (marked ⚖️). The PO's binding conditions:
> - **1A:** apply the adapter patch where the IB client is built, in the avg-px-fix pattern; a test
>   proves the position-update feed is off; every runtime correction is logged as
>   `reconcile.discrepancy` with instrument and quantities; the 1–2 cycle lag is recorded in the Dev
>   Agent Record.
> - **2A:** refuse and stop — nothing written to the cache; the session stops through the normal stop
>   path, which must not close, flatten or size-adjust anything, and `close_all_positions()` must not
>   appear anywhere; exit 1 with instrument and quantities; tested against a broker double. The
>   `reconciliation_owned` persist-skip policy is **not** touched.
> - **3A:** D6 and the takeover gap are dispositioned explicitly in the Dev Agent Record and routed to
>   the Epic 4 retrospective as dedicated stories, with the measurements attached.
> - **General:** AR39's phase order is untouched; restoring permission after a reconnect goes through
>   `confirm_state_reestablished()` only after the state check passes.
>
> **Task 1 gate — two further rulings (PO, 2026-09-27: 1A 2A), which amend D-A and D-C below:**
> - **D-A amended (1A): the open-order consistency check stays OFF.** Measured (Task 1.1b): a
>   locally-cancelled order that IB still lists open is never corrected — the engine republishes an
>   `OrderAccepted` for it on every check, forever (log noise, a Nautilus warning each time, and a
>   Story 3.7 streak reset). The measured reason goes in the node-builder comment. AC #1 is met by
>   the native in-flight sweep running for the whole session, with every runtime correction through
>   `reconcile_execution_report`; a test proves the continuous reconciliation task is configured for
>   the whole session, and the open check is pinned deliberately off (Nautilus's default off). **No
>   workaround of our own compensates for it.** AC #1's `open_check_interval_secs == 60.0` pin is
>   replaced by `open_check_interval_secs is None`.
> - **D-C amended (2A): defer an instrument only while an order on it is in flight** (`SUBMITTED`,
>   `PENDING_UPDATE`, `PENDING_CANCEL` — `cache.orders_inflight()`), not for any open order. Measured
>   (Task 1.5): an `ACCEPTED` order stranded across a restart stays open forever, which would have
>   made its instrument unverifiable. Both safeguards stay (cache-moved skip; identical on two
>   consecutive cycles 60 s apart). Corrections resolve broker-ward only, each logged as
>   `reconcile.discrepancy` with instrument and quantities. The reconnect grant still requires no
>   in-flight order. `live_gate.py` and the client-id ranges are not touched.

- **D-A — What "continuous reconciliation" is here: three layers, all live for the session's life.**
  1. **Native order-state, both checks on.** The in-flight sweep stays exactly as Story 3.4 pinned it.
     The open-order consistency check is switched **on**: a new pinned constant
     `EXEC_ENGINE_OPEN_CHECK_INTERVAL_SECS = 60.0` and `EXEC_ENGINE_OPEN_CHECK_OPEN_ONLY = True`
     passed explicitly in `build_trading_node_config` (the "today's-defaults-deliberately" pin
     pattern: pin that we set it, and that the wheel's default is still `None`/`True`). It catches an
     order the venue lists as open that the cache believes closed or never knew — the inverse of the
     conflation in F14. 60 s rather than the docstring's 5–10 s: each run is two IB requests
     (`reqPositions` + `reqOpenOrders`, F3), swing strategies trade on ≥ 1-minute bars, and NFR15's
     budget is for the data path. The F2 comment block is rewritten to say this.
  2. **The adapter's position stream is taken out of the resolution path** (D-B).
  3. **A verified position cycle of ours** (D-C), resolving broker-ward through the framework's own
     public `reconcile_execution_report` — the Story 4.2 D-E shape. "Native machinery" is the
     framework's reconciliation engine doing every write; the cycle only decides when and logs what.
  - Honest scope, stated in the new module's docstring: detection latency for a position change the
    execution stream did not explain is **one to two cycles** (D-C's debounce), not instant.

- **D-B — ⚖️ Suppress the IB adapter's "external position change" reports. ✅ Ruled 1A (PO,
  2026-09-27).**
  - `src/core/live_exec_position_reports.py` (new, the `live_exec_avg_px.py` shape):
    `install_position_report_suppression(client)` replaces the **instance's**
    `_handle_position_update` (F4) with a coroutine function that sends **no** report. It logs one
    `reconcile.position_update_deferred` at INFO (`con_id`, `known_quantity`, `reported_quantity`;
    no account) and returns; it never raises (contained, `_emit`-style). Called from
    `InteractiveBrokersLiveExecClientFactory.create` next to the avg-px fix. The class name does not
    change.
  - Loud at build time if the shape moved: `AttributeError` when the client has no
    `_handle_position_update` or `_known_positions` (the avg-px precedent: a silent skip would bring
    the phantom back). Canaries pin, against the wheel's source: `_on_position_update` calls
    `self._handle_position_update(...)` through `create_task`; `_handle_position_update` still sends
    via `_send_position_status_report`; `_connect` still subscribes `positionUpdate-{account}`.
  - Why: F5's phantom is one spurious round trip per strategy entry — two `trade.aggregated`, two
    would-be `reconcile.discrepancy` records — and the detector is blind to a position going flat
    anyway (F4), so D-C's cycle is needed regardless. With the reports gone, **every** runtime
    position correction is one D-C made, verified and logged.
  - Cost, disclosed: a genuine external change (a manual TWS trade, an option exercise, a split) is
    corrected within one to two D-C cycles instead of on arrival. Nothing trades on it in between
    unless a strategy's own position is involved, which D-D refuses.
  - Alternatives not recommended: (K) keep the stream and patch its two bugs (track untracked
    conIds from `execDetails`; defer reports while the instrument has in-flight orders) — still blind
    to flat, still a second unlogged resolution path, and a deeper patch of adapter internals;
    (N) keep it as-is — D-C would then log two discrepancies per entry that are really one race.

- **D-C — The runtime cycle (`src/core/live_runtime_reconcile.py`, new).**
  - `RuntimeReconciler`, driven by the steady-state tick **after** `_observe_connection` (F8), so a
    reconnect is handled in the same tick that observed it. Constructed by the runner with the node,
    the monitor, the session logger, the connection reader + settings, and the fifth seam
    `broker_state_reader` (Story 4.2's). Its public surface is one coroutine, `on_tick()`.
  - **Cadence.** `CONNECTED`: one cycle every `RUNTIME_RECONCILE_EVERY_TICKS = 2` ticks (60 s at
    AR32's 30 s tick). `RECOVERING`: a reconnect cycle **every** tick (D-F). `AWAITING_CONNECTION`,
    `LOST`, `HALTED`: nothing (submission is already withheld; there is no broker to ask).
  - **One cycle, in order.**
    1. Snapshot the cache (`cached_positions`) and the instruments with working orders
       (`cache.orders_open()` ∪ `cache.orders_inflight()`, by instrument).
    2. `read_broker_state(node, log=<quiet>)` — a logger that drops the reader's own records (F9);
       the cycle logs its own outcome instead. Never wrapped in `asyncio.wait_for` (Story 4.1 D-C).
    3. Re-snapshot the cache. If it moved during the read (a fill landed), the cycle is **skipped**
       — no action, no debounce progress, nothing logged unless it is a state change.
    4. `compare_positions(cache, broker)` (exact, net + strategy-own, Story 4.2 D-C), then drop rows
       for instruments with working orders ("deferred" this cycle — the execution stream owns them).
    5. **Debounce.** A row acts only when an **identical** row (instrument, local, strategy, broker
       quantities) was also seen on the previous *completed* cycle. A clean cycle clears the
       pending set. This absorbs a broker position that lags our own fill, or the reverse.
    6. Act on confirmed rows by the policy table below, then re-read the cache and re-compare
       against the **same** broker snapshot; `resolution="broker"` is logged only once the re-read
       proves the correction took (the Story 4.2 rule).
  - **Policy table (confirmed rows only):**

    | Row | Action |
    |---|---|
    | `kind == "position"`, reconcilable | Broker-ward through `exec_engine.reconcile_execution_report(<Story 4.2's report>)`; WARNING `reconcile.discrepancy resolution="broker"` after the re-read. |
    | `kind == "position"`, unresolvable (broker row unresolved, instrument unknown to the cache, or a quantity/price the instrument cannot represent) | ERROR `reconcile.discrepancy resolution="unresolved"`, logged **once** per identical row, session continues. No strategy of this session can act on an instrument the cache does not hold. |
    | `kind == "strategy_position"` | **D-D.** |
    | framework returns `False`/raises, or the row remains after the re-read | ERROR `resolution="refused"`, then **D-D's stop** — the net the strategies read is wrong on a traded instrument. |

  - **Failure is survivable (AR42).** A `BrokerStateUnavailableError`/`BrokerStateAdapterError`, or
    any other exception inside a cycle, is contained: WARNING `reconcile.cycle_failed` on the first
    failure after a success (and at most hourly while it persists, carrying `consecutive`), never
    every cycle. The next clean cycle's `reconcile.ok` closes it. Only D-D's typed failure escapes
    the tick, beside `SessionReclaimedError`. Story 4.1's stranded-`OpenPositions` request (a
    cancelled awaiter) makes every read fail at once until restart: under this rule that is one
    WARNING and, after a reconnect, a withheld session that halts and says so (NFR20) — fail-closed.
  - Must run on the node's loop (the reader checks); it does, as part of the heartbeat task.
    Bounded: one cycle ≤ the reader's 20 s deadline + the cache work, so the heartbeat cadence
    stays inside `interval + 20 s` < AR32's 90 s staleness.

- **D-D — ⚖️ A strategy's own position contradicted at runtime stops the session. ✅ Ruled 2A (PO,
  2026-09-27): refuse and stop.**
  The runtime twin of Story 4.2's D-D, for the same measured reason: the framework cannot rewrite a
  strategy's position (Story 4.2 F6), and a strategy that believes it holds shares the broker does
  not will `close_position()` them on its next opposite signal — an order nobody requested (NFR14).
  Runtime sources: a manual TWS close, a lost exit fill (F14's conflation), a corporate action.
  - (A) **Recommended — refuse and stop.** Before writing anything: ERROR `reconcile.discrepancy
    kind="strategy_position" resolution="refused" reason="strategy_position_contradicted"
    scope="runtime"` per row, then raise `ReconciliationFailedError(STRATEGY_POSITION_CONTRADICTED,
    scope="runtime")` out of the tick. It reaches `_serve` like `SessionReclaimedError` does; the
    runner's ordinary teardown stops the node (inert stop path — positions untouched at IBKR), marks
    the row `stopped`, exits 1 with an operator-safe message naming instrument and quantities. The
    next `live start` meets Story 4.2's D-D refusal, whose remedy (a new session, or clearing the
    disposable cache) applies unchanged.
  - (B) Stop only the contradicted strategy (Story 2.7's guard path) and correct the net broker-ward;
    the session keeps running for the others. For today's one-strategy sessions this is (A) with a
    live process doing nothing.
  - (C) Correct the net broker-ward and keep trading, logging ERROR. Leaves the false belief in place
    — the NFR14 hazard.
  - Cost of (A), disclosed: a corporate action on a held position stops the session. Story 4.7
    ("absorbed without manual intervention") and Story 4.5 (strategy adoption) own relaxing it,
    with a named test — the same hand-off Story 4.2's D-D made.

- **D-E — `reconcile.ok` at a volume that does not drown the log (AC #3).**
  - INFO `reconcile.ok scope="runtime"` on: the first clean runtime cycle of the process; the first
    clean cycle after any non-clean one (discrepancy, failure, skip streak); and otherwise **at most
    once per `RUNTIME_OK_LOG_INTERVAL_SECONDS = 3600`**. Each record carries `cycles`, the number of
    clean cycles it summarises, so the accounting is exact (Σ `cycles` == clean cycles).
  - A 6.5 h RTH day at 60 s is ~390 clean cycles and **≤ 8** `reconcile.ok` records. With the reader
    quieted (D-C step 2) that is the whole runtime-reconciliation footprint of a clean day.
  - Fields: `scope`, `account` (masked), `positions`, `instruments` (`{id: str(qty)}`), `cycles`,
    `deferred_instruments` (sorted ids skipped for working orders), `synthetic_positions`,
    `open_orders`, `elapsed_ms`. Never a raw account, `str(exc)` or broker text (NFR26).

- **D-F — The grant goes live; the order gate tightens (AC #4, NFR10).**
  - **Startup grant.** `_phase_reconcile` ends, after `reconcile_at_startup` returns, with one call
    to `grant_after_reconciliation(monitor, reading, log)` in the new module: it folds a fresh
    connection reading and calls `confirm_state_reestablished` — the grant is atomic with a live
    reading and follows a genuine reconciliation (Epic 1 retro Action Item #7). A reader that raises
    is contained (no grant). A refusal is not a phase failure: `submission_withheld` stays `True`
    and D-C's reconnect cycle grants once the reading turns connected.
  - **Reconnect re-confirm.** In `RECOVERING`, every tick runs a reconnect cycle (D-C steps 1–6, the
    same debounce), and grants only when (a) the cycle completed with **zero** remaining rows,
    (b) no instrument was deferred for working orders **and** `cache.orders_inflight()` is empty —
    the in-flight sweep must settle first — and (c) the fresh reading folded by
    `confirm_state_reestablished` is still connected. Then INFO `reconcile.ok scope="reconnect"`; the
    monitor itself logs `connection.restored` (AR41's normative milestone, emitted for the first
    time in this project). Anything else: stay `RECOVERING`, retry next tick; the halt clock runs,
    and `connection.halted` fires after the window — NFR20's "halts and reports".
  - **The gate.** `ConnectionMonitor.submission_withheld` becomes `not self.trading_permitted`.
    `RECOVERING` now withholds: it is no longer a steady state, it is "state not re-established".
    This is a **named, deliberate change** of Story 3.2's predicate; every test pinning the old
    closed form is rewritten, not deleted, and the docstring says why. `ConnectionMonitor` stays at
    or under its 118 baseline (the body gets shorter).
  - Latency, disclosed: detection rides the 30 s tick, so trading resumes within one tick of the
    socket returning when state is clean, and after ≥ 2 ticks when a discrepancy must debounce.
    `downtime_seconds` includes up to one tick of detection on each side. A tick-granularity halt
    followed by a restore is legitimate and escapable (the monitor's two-step path).
  - The steady state's `connection.state_changed` narration stays: it still covers transitions the
    armed monitor does not narrate (e.g. the pre-grant drop). Its docstring's "silent monitor"
    paragraph is rewritten.
  - `test_confirm_state_reestablished_is_never_called_in_this_story` becomes an **exact caller-set**
    pin: `live_runtime_reconcile` is the only production module that calls it.

- **D-G — Records (AR41 `reconcile.*` / `connection.*`; NFR26).**
  - Every `reconcile.ok` / `reconcile.discrepancy` gains `scope` ∈ {`startup`, `runtime`,
    `reconnect`} — **additive**, including Story 4.2's startup records (P17's greps still match).
  - `resolution` gains `unresolved` (runtime only). Runtime discrepancy rows carry the Story 4.2
    fields (`instrument_id`, `kind`, `local_quantity`, `strategy_quantity`, `broker_quantity` as
    `str(Decimal)`; `reason` when refused).
  - New names: `reconcile.cycle_failed` (WARNING; `reason`/`error_type`, `consecutive`),
    `reconcile.position_update_deferred` (INFO, D-B). No `reconcile.broker_state_retrieved` from a
    runtime cycle (F9).

- **D-H — One failure type, no new exit code.** `ReconciliationFailedError` gains an optional
  `scope` (default `"startup"`) that selects the message prefix — `"Startup reconciliation refused
  to let this session trade"` or `"Runtime reconciliation stopped this session"`. Same markers
  (`LiveCheckOutcome.ERROR` → exit 1, `operator_safe_message = True`). `EXIT_CODES` and README's
  exit-code table are untouched.

- **D-I — Budget before the edit (F11).** The first code change, a pure move with every tier green
  before and after:
  - **Runner (495/500):** move the bodies of `_flush_order_rejections` (16) and, if needed,
    `_flush_contained_failures` (25) to module-level functions in `live_session_steady_state.py`
    beside `write_rejection_snapshot` (already on `NODE_FACING_MODULES`, `STOP_PATH_MODULES` **and**
    `TestImportPurity.MODULES` — the move escapes no scan; say so in the Debug Log and prove it by
    running them). The runner keeps two-line methods. Target ≤ 485 before wiring; the wiring
    (import, startup grant, reconciler construction) is ~8 → ~493/500. Measure after each step.
  - **`SessionSteadyState` (120/120):** move `_warn_if_no_bars`'s body to module level (the
    `write_rejection_snapshot` shape); the reconciler parameter, attribute and one awaited call then
    fit under 120 with no baseline edit.
  - `LiveSessionRunner`'s baseline changes to its measured value with a one-line reason
    (`Story 4.3: startup grant + runtime reconciler wiring; flushes moved to the steady-state
    module`). Any other baseline change is disclosed the same way.

- **D-J — Routed debt: dispositions (each recorded in `deferred-work.md`, "Deferred from:
  story-4.3").**

  | Item (prd-epic4-scope.md) | Disposition |
  |---|---|
  | Adapter double-counts its own fresh fill (F5) | **Fixed** by D-B; P18's criterion is "one `trade.aggregated` per entry". |
  | Not-open/cancelled conflation on `generate_order_status_report` (F14) | **Consequence caught**: the position a lost fill leaves behind is a D-C discrepancy — detected, logged, corrected (or refused under D-D if it contradicts a strategy). The *order record* stays "canceled": the adapter's `generate_fill_reports` returns `[]`, so no fill evidence exists to correct it with. Recorded as an upstream limit, **unowned, named**. |
  | IB error code outside `ORDER_REJECTION_CODES` delay (F14) | **Measured and bounded, no code change.** A `SUBMITTED` order resolves as `OrderCanceled` within `inflight_check_threshold_ms` + one interval (~7 s), not minutes; the real reason exists only in the adapter's log line. Mapping unknown codes to rejections is refused: IB sends many informational per-order codes, and treating a live order as dead invites a duplicate order (NFR14). |
  | `OrderTriggered` unhandled (F12) | **Closed as unreachable** through the IB adapter at 1.220.0: `live_order_path.py`'s module docstring states it (docstring-only edit, no statement change), and a canary pins F12 against the wheel so an upgrade that makes it reachable goes red by name. `EMITTED_ORDER_EVENTS` unchanged. |
  | Open orders counted, never compared (4.2 review) | **Covered for the venue-open direction** by D-A's consistency check; the cache-open/venue-gone direction has no native path with IB (F1) and its position consequence is D-C's. Recorded. |
  | Cached order fills between broker read and compare (4.2 review) | **Fixed for runtime** by D-C step 3 (cache moved ⇒ skip) and step 4 (working orders ⇒ defer). |
  | Broker-only position with no average price imported at 0 (4.2 review) | D-C's resolution reuses Story 4.2's report builder, which passes the broker's average whenever known; with none known the framework's `0` fallback remains (rare: IB reports `avgCost` for held positions). Recorded, unchanged. |
  | A mid-session broker read diverts position updates (4.1) | **Moot** under D-B (no report depends on the stream). If D-B is not taken, D-C's debounce absorbs it. |
  | Cancelled awaiter strands `OpenPositions` (4.1 review) | **Fails closed** under D-C (one WARNING; a reconnect cannot re-grant; halt reported). Recovery still needs a restart (writing adapter state stays out of bounds). |
  | `confirm_state_reestablished` sanity check (4.2 D-J) | **Live**, D-F. |
  | Stranded-`ACCEPTED`-order startup stall (4.2) | Startup path, inside `node:connect`, before any phase of ours: **re-measure in Task 1.5**; if it reproduces, route to **Story 4.5** (the restart path) with the measured shape. |
  | ⚖️ **D6** — resubscription after 162/10182/366 (F13) | **Recommended: re-route** to the Epic 4 retrospective for a dedicated story, with F13 as its starting measurement. Reasons: it is market-data subscription state, not order/position alignment; none of the codes reaches our code, so a fix means hooking the shared IB client's error path; and it cannot be exercised without broker-side fault injection. Alternative: build a hook here (1101 → `_resubscribe_all`; 162 → a `connection.market_data_lost` record). |
  | ⚖️ Pre-submit reclaim window `owner_epoch` does not fence (3.6 D-C) | **Recommended: re-route** with the D6 item. It is session ownership, not broker alignment; the fix (an ownership probe — `record.record_activity(...)` — before each submit, suppress + stop on `SessionReclaimedError`) puts a synchronous DB round trip on the order path (Story 3.6 D-A's stall exposure) and needs its own two-process AC. Alternative: build it here. |

- **D-K — Zero-diff set.** Untouched: `live_trade_recorder.py`, `live_broker_state.py`,
  `live_order_rejections.py`, `live_session_health.py`, `live_bar_observer.py`; `live_order_path.py`
  except its module docstring (D-J); every strategy under `src/core/strategies/`; `src/db/**`,
  `alembic/**` (head `85c949ac0374`), `src/services/**`, `src/api/**`, `templates/**`;
  `SessionRecordPort`, `EXPECTED_CAPABILITIES`, `EMITTED_ORDER_EVENTS`, `EMITTED_TRADE_EVENTS`,
  `FORBIDDEN_ORDER_METHODS`, `LIFECYCLE_HOOKS`, `EXIT_CODES`; the real-money gate
  (`live_gate.py`, `live_account_gate.py`); `PHASE_SEQUENCE`; every `scripts/diagnostics/*`
  kill/reconnect/connection-loss script.

## Acceptance Criteria

The epic text (`epics.md:1548-1571`) is in **bold**. The clarifications under each are binding.

1. **Given a running session, When continuous reconciliation is configured, Then it runs throughout
   the session using Nautilus's native machinery (FR34).**
   - Pins on the built `TradingNodeConfig.exec_engine`: `open_check_interval_secs ==
     EXEC_ENGINE_OPEN_CHECK_INTERVAL_SECS` (60.0), `open_check_open_only is True`, and the Story 3.4
     in-flight values unchanged; plus the wheel-default canaries (`None` / `True`).
     *(Superseded at the Task 1 gate, PO ruling 1A, 2026-09-27: the open-order check stays
     **off**. What is pinned is `open_check_interval_secs is None` on the built config
     (`test_live_node_builder.py`) and the measured reason as a named canary
     (`TestTheOpenOrderCheckStaysOffCanary`, `test_live_runtime_reconcile_engine.py`, Task 1.1b).
     No `open_check_open_only is True` pin exists — the setting is moot with the check off.)*
   - Real-engine component test: a `LiveExecutionEngine` built from the session's pinned config has a
     running `continuous_reconciliation` task after start (`get_reconciliation_task()` not `None`,
     not done) and it is cancelled by stop; with an IB-shaped client, a fabricated `FILLED` report
     is a no-op and a venue-open report for an order the cache lacks is imported (Task 1.1 pinned as
     named canaries). *(As shipped: the two Task 1.1 canaries named here were not written under
     those names; the surviving canary is Task 1.1b's above. PR #35 code review, P8.)*
   - Runner component test: across a driven sequence of heartbeat ticks the reconciler runs a
     cycle every `RUNTIME_RECONCILE_EVERY_TICKS` ticks while `CONNECTED`, and every correction it
     makes goes through `reconcile_execution_report` only (call-recording spy + an AST check that
     `live_runtime_reconcile.py`'s only engine mutation is that call).
   - D-B: after `InteractiveBrokersLiveExecClientFactory.create(...)`, the client's
     `_handle_position_update` sends no report (spy on `_send_position_status_report`) and logs
     `reconcile.position_update_deferred`; an adapter without the attribute raises at build time.
2. **Given a discrepancy detected at runtime, When it is resolved, Then it resolves broker-ward and is
   logged as `reconcile.discrepancy` with the specific instrument and quantities (FR35, AR41).**
   - Against the real engine + cache (component tier), with the cycle driven twice (debounce):
     (a) cache flat, broker +5 @ 101.25 → cache net +5, the synthetic fill priced 101.25, one WARNING
     `reconcile.discrepancy scope="runtime" resolution="broker"` naming `instrument_id`,
     `local_quantity="0"`, `broker_quantity="5"`; (b) a stale synthetic +4, broker flat → net 0;
     (c) **never the reverse**: no path submits, cancels or modifies an order (the inert scan via
     `LIVE_MODULE_GLOBS`), and under D-D a contradicted strategy row is refused, never "resolved to
     local".
   - Debounce: a discrepancy seen on **one** cycle only changes nothing and logs no
     `reconcile.discrepancy`; seen on two identical cycles it resolves. A cache that moves during the
     broker read skips the cycle. An instrument with a working order is deferred and named in the
     next `reconcile.ok`.
   - Unresolvable rows log `resolution="unresolved"` once per identical row and the session
     continues; a framework refusal or a remaining row logs `resolution="refused"` and stops (D-D's
     path).
   - D-D (per the PO ruling): the chosen behaviour end to end through `runner.run()` — for (A):
     `(reconcile.discrepancy … resolution="refused")` at ERROR, **no** `reconcile_execution_report`
     call, the node stopped, `mark_stopped` called, `classify_failure` → exit 1, the message names
     instrument and quantities, and no order method was called on the stop path.
3. **Given no discrepancy, When a reconciliation cycle completes, Then `reconcile.ok` is logged at a
   volume that does not drown the trading log.**
   - 390 driven clean cycles (a 6.5 h day at 60 s, injected clock) produce **≤ 8** `reconcile.ok
     scope="runtime"` records, the first on the first clean cycle; Σ `cycles` == 390.
   - A discrepancy/failure followed by a clean cycle emits `reconcile.ok` on that clean cycle
     regardless of the hourly throttle.
   - A runtime cycle emits **no** `reconcile.broker_state_retrieved` (F9), and a persistent read
     failure logs `reconcile.cycle_failed` once, not per cycle.
4. **Given a reconnection after a disconnect, When the connection is restored, Then state is
   re-established before trading permission returns (NFR10).**
   - Startup: after a successful `reconcile` phase the monitor is `CONNECTED` and
     `trading_permitted` is `True` before `trading` starts any strategy; with a reader that reports
     disconnected at that instant, no grant, no phase failure, `submission_withheld` stays `True`.
   - The gate: in `RECOVERING` (after a loss) `submission_withheld is True`; an order a strategy
     submits there is suppressed (`order.suppressed`), never reaches the exec client.
   - Reconnect, component tier through the steady state + reconciler: connected → lost
     (`connection.lost`) → connected (`RECOVERING`, withheld) → the next tick runs a reconnect cycle
     and only then `confirm_state_reestablished` (`reconcile.ok scope="reconnect"` strictly before
     `connection.restored` in the captured records) → `CONNECTED`, permitted.
   - Refusal paths each keep permission withheld: a failed broker read; a remaining/confirmed
     discrepancy not yet resolved; an in-flight order; a reading that drops again at confirm time.
     Past the window, `connection.halted` fires; a later clean reconnect cycle still restores.
   - The exact caller-set pin: `confirm_state_reestablished` is called in production only from
     `live_runtime_reconcile.py`.

## Tasks / Subtasks

- [x] **Task 0 — Standing checks (record in the Debug Log)**
  - [x] 0.1 Head, clean tree; `uv run ruff check .` and `make typecheck` clean before the first edit.
  - [x] 0.2 Baselines: `make test-unit`, `make test-component` (passed/failed/skipped).
  - [x] 0.3 Re-measure F11 with `measure_module`.

- [x] **Task 1 — Measure before building (probes in `/tmp/p43/`, not committed)**
  Real `LiveExecutionEngine` + `Cache` + `Portfolio`, NETTING IB-shaped mock client registered as
  `ClientId("INTERACTIVE_BROKERS")` (Story 3.7: `MockExecutionClient` hardcodes HEDGING; reuse Story
  4.2's `test_live_startup_reconcile_engine.py` harness).
  - [x] 1.1 F1/F3: with `open_check_interval_secs` small, the consistency check calls
    `generate_order_status_reports`; a fabricated `FILLED` report (id = instrument id) → no event; a
    venue-open report for a cached-open order → no event; for an unknown order → imported (which
    strategy id?); for a cached-**closed** order → what happens? Record.
  - [x] 1.2 D-C: a runtime `reconcile_execution_report` with the trade recorder and order observer
    subscribed — which records appear (`order.filled` with the reconciliation flag? a
    `trade.persist_skipped` on close)? Synchronous (Story 4.2 1.3 says yes) — confirm.
  - [x] 1.3 D-B: can a real `InteractiveBrokersExecutionClient` be constructed offline for the
    factory test (the avg-px tests' approach)? If not, the canaries are source-level plus an
    instance-level test on a stand-in with the same attribute names.
  - [x] 1.4 D-F: a real `ConnectionMonitor` driven through startup-grant → lost → recovering →
    confirm; confirm `connection.lost`/`connection.restored`/`connection.halted` fire once each
    as F7 predicts.
  - [x] 1.5 The stranded-`ACCEPTED` startup stall: does a cached `ACCEPTED` order absent from the
    mass status still stall portfolio initialisation? Record → D-J routing.
  - [x] **Gate:** a result contradicting D-A/D-B/D-C's premises (e.g. 1.1's fabricated report is
    *not* a no-op) reopens that decision **before** Task 3. Record, and ask.

- [x] **Task 2 — Budget split (D-I), before any feature code**
  - [x] 2.1 Move `_flush_order_rejections` (and `_flush_contained_failures` if needed) bodies to
    `live_session_steady_state.py`; move `_warn_if_no_bars`'s body to module level. Verbatim, plus
    the minimum parameter plumbing.
  - [x] 2.2 Run unit, component, `tests/integration/core/ --forked`, the three guard-list suites and
    `test_size_caps.py`. Record the new measurements.

- [x] **Task 3 — Node config (AC #1) — TDD**
  - [x] 3.1 Red: pins in `tests/component/core/test_live_node_builder.py` for the two new constants
    and their wheel defaults.
  - [x] 3.2 Green: constants + kwargs in `build_trading_node_config`; rewrite the F2 comment block.
    `build_trading_node_config` is baselined at 128 — two kwarg lines raise it; update the baseline
    with a reason or offset it.

- [x] **Task 4 — `src/core/live_exec_position_reports.py` (D-B) — TDD**
  - [x] 4.1 Red (component): install on a client stand-in → no report sent, one INFO record, never
    raises on a malformed `ib_position`; missing attributes → `AttributeError`; source canaries per
    D-B; the factory calls it (spy) and still carries its load-bearing class name.
  - [x] 4.2 Green; wire into `InteractiveBrokersLiveExecClientFactory.create`.

- [x] **Task 5 — `src/core/live_runtime_reconcile.py` (AC #2, #3, #4) — TDD**
  - [x] 5.1 Red, unit tier with duck-typed stubs (`tests/unit/core/test_live_runtime_reconcile.py`):
    cadence by state; quiet reader logger; skip on cache move; defer instruments with working
    orders; debounce (one cycle → nothing, two identical → act, a changed row restarts); policy
    table rows; D-E throttle and exact `cycles` accounting; `reconcile.cycle_failed` on transitions
    only; D-F grant conditions (a)–(c); D-D per the ruling; NFR26 (no raw account in any captured
    value, every path); `_emit` never raises.
  - [x] 5.2 Green. Reuse Story 4.2's helpers — make `_position_report` (and whatever else is reused)
    public in `live_startup_reconcile.py`, add `scope` to its records (D-G) and to
    `ReconciliationFailedError` (D-H). No behaviour change to the startup phase beyond the additive
    field; its tests stay green unmodified except where they pin the exact field set.
  - [x] 5.3 Component tier, real engine (`tests/component/core/test_live_runtime_reconcile_engine.py`):
    AC #2 (a)–(c); Task 1's shapes as named canaries; `_assert_c_logging_state_is_unchanged`
    autouse; never construct a `TradingNode`.

- [x] **Task 6 — The gate and the grant (AC #4)**
  - [x] 6.1 Red → green: `submission_withheld = not trading_permitted`; rewrite each test pinning the
    old closed form (`test_live_connection_monitor.py`, `test_session_runner_order_path.py`,
    `test_live_order_recovery.py`, `test_live_order_rejections_engine.py` — any harness that observes
    `RECOVERING` and expects orders to pass must now grant). Name the change in each docstring.
  - [x] 6.2 `grant_after_reconciliation` + the runner's `_phase_reconcile` call; the steady state
    calls `reconciler.on_tick()` after `_observe_connection`; the runner builds the reconciler in
    `_build_steady_state`. Measure the runner and the class after each edit.
  - [x] 6.3 The exact caller-set pin replaces `test_confirm_state_reestablished_is_never_called_in_this_story`.
  - [x] 6.4 Doubles (all defaulted, so existing callers pass unmodified): `TestLiveNode`'s cache
    gains `orders_inflight()`; the exec engine double already records `reconcile_execution_report`
    (Story 4.2). A shared `flat_broker_state_reader` exists (Story 4.2).
  - [x] 6.5 AC #2's D-D test and AC #4's startup/reconnect tests through `runner.run()` /
    the steady state, in `test_session_runner_phases.py` or a new
    `test_session_runner_runtime_reconcile.py` if size warrants.

- [x] **Task 7 — Guard lists and governance (same commit as the modules)**
  - [x] 7.1 Both new modules go in `NODE_FACING_MODULES` and `TestImportPurity.MODULES`, each with a
    reason. Neither goes in `STOP_PATH_MODULES` (not reached from teardown) — add the comment there,
    the `live_broker_state` precedent. Re-check all three lists after Task 2's move.
  - [x] 7.2 `_STDLIB_AND_FIRST_PARTY`: **run** `tests/integration/core/test_epic1_ac_node.py`; add any
    new stdlib import by hand.
  - [x] 7.3 `LIVE_MODULE_GLOBS` picks both up: run `test_live_dependency_invariance.py`,
    `test_order_path_has_no_retry.py`, `test_live_stop_path_is_inert.py`.
  - [x] 7.4 `test_exit_outcome_markers.py` passes without growing `UNMARKED`.
  - [x] 7.5 Size caps: new modules under every cap; baselines regenerated only with reasons.

- [x] **Task 8 — Operator surface, docs, live verification**
  - [x] 8.1 `live start --help` and `README.md`: one sentence on runtime reconciliation (cadence,
    what is logged, D-D's stop) and on the reconnect grant. Exit 1 now also covers "runtime
    reconciliation stopped the session" (CliRunner test: message names instrument/quantities).
  - [x] 8.2 Docstrings: `live_connection_monitor.py` (module + `submission_withheld`),
    `live_session_steady_state.py` (module limit 4, `_observe_connection`), the runner, 
    `live_session_phases.py` (reconcile now also grants), `live_order_path.py` (F12 note).
    `docs/agent/nautilus.md`: a "Runtime reconciliation" section (F1, F4/F5, D-B, D-C, D-F).
  - [x] 8.3 **Procedure P18** in `docs/qa/phase3-live-verification.md` after P17 (keep the
    parallel-story renumbering note). It needs a running `live start` session, a manual TWS change
    or a Gateway restart — **not read-only** — so it is **defined, not run** by the harness:
    (a) an entry produces exactly one `trade.aggregated` and no `External position change
    detected`; (b) a manual TWS change on a session-traded instrument no strategy holds →
    `reconcile.discrepancy scope="runtime" resolution="broker"` within two cycles; (c) a Gateway
    restart → `connection.lost` → `reconcile.ok scope="reconnect"` → `connection.restored`, with
    zero `order.submitted` in between; (d) a clean hour → ≤ 2 `reconcile.ok`. Never `--real-money`;
    never touch `.env`, docker, or the kill/reconnect/connection-loss scripts.

- [x] **Task 9 — Routed debt (`deferred-work.md`, new "Deferred from: story-4.3")** — every D-J row,
  with the PO's rulings on the ⚖️ rows.

- [x] **Task 10 — Mutation sweep (apply, run the target tests, restore; record kill/survive)**
  - M1 act without debounce; M2 compare with a tolerance; M3 no cache-moved skip; M4 no
    working-order deferral; M5 `reconcile.ok` every cycle; M6 grant without a reconnect cycle;
    M7 grant with in-flight orders; M8 `submission_withheld` back to the old closed form; M9 D-B
    sends the report; M10 resolve a strategy row (D-D dropped); M11 log the raw account; M12 the
    reader's own records not quieted; M13 contain D-D's error inside the tick (session keeps
    trading).

- [x] **Task 11 — Gates**
  - [x] 11.1 `uv run ruff format .`, `uv run ruff check .`, `make typecheck`.
  - [x] 11.2 `make test-unit`, `make test-component`, integration `--forked` for
    `tests/integration/core/`.
  - [x] 11.3 `grep -rn "is_live" src/core/strategies/` → 0; `git diff --stat` shows D-K untouched.

### Review Findings

Code review 2026-09-28 (bmad-code-review; Blind Hunter, Edge Case Hunter and Acceptance Auditor
run as parallel subagents after the harness's own review turn died on an API `Request timed out`).
1 decision-needed (resolved), 8 patches (applied), 6 deferred, 11 dismissed.

- [x] [Review][Decision] A persistent unresolvable broker row blocked the reconnect grant forever,
  and any `IB-CONID-*` row disabled every runtime check — **ruled by the PO 2026-09-28 ("narrow the
  block")**: a row for an instrument the cache does not hold never withholds the grant (no strategy
  can trade it); an unresolved broker row still withholds it (fails closed) and holds back only the
  cache rows reading "broker 0" (the only rows it can mask) — every other instrument is still
  checked and corrected. Unresolved rows now debounce before their ERROR. [src/core/live_runtime_reconcile.py `_act`]
- [x] [Review][Patch] Corrections that took before a refusal / a remaining row were never logged `resolution="broker"` — now logged before the stop, as Story 4.2's startup path does [src/core/live_runtime_reconcile.py `_log_taken`]
- [x] [Review][Patch] A refused reconnect grant re-logged `reconcile.ok scope=reconnect` every tick — now once per loss, still ahead of `connection.restored` [src/core/live_runtime_reconcile.py `_grant`]
- [x] [Review][Patch] The debounce survived a disconnect: a pre-loss sighting could confirm a post-loss row on one observation — reset once per loss (not on RECOVERING↔HALTED flaps) [src/core/live_runtime_reconcile.py `on_tick`]
- [x] [Review][Patch] `reconcile.ok` emit / the grant ran outside the cycle's containment — an exception there could end the heartbeat task (AR42) [src/core/live_runtime_reconcile.py `_run`]
- [x] [Review][Patch] A skipped (cache-moved) cycle did not force the next `reconcile.ok` (D-E's skip streak) [src/core/live_runtime_reconcile.py `_run`]
- [x] [Review][Patch] An instrument deferred on a throttled cycle was never named — deferrals now accumulate until the next record (AC #2) [src/core/live_runtime_reconcile.py `_OkLog`]
- [x] [Review][Patch] `_Unresolved` overwrote its memory per call, so a persistent row was re-logged at ERROR after any flap — pruned against the whole observation instead [src/core/live_runtime_reconcile.py `_Unresolved`]
- [x] [Review][Patch] D-B's record lacked `known_quantity` [src/core/live_exec_position_reports.py `_note_deferred`]
- [x] [Review][Defer] An `ACCEPTED` order filled while disconnected, `execDetails` never replayed, is corrected as a synthetic `INTERNAL-DIFF` position while the order stays open — a consequence of rulings 1A/2A, not a coding error — deferred, routed to 4.5
- [x] [Review][Defer] A drop and reconnect inside one 30 s tick is never observed as a loss (no connection generation counter) [src/core/live_connection_monitor.py] — deferred, pre-existing (Story 3.2 monitor)
- [x] [Review][Defer] D-B's suppression is proven only on a stand-in, never a real `InteractiveBrokersExecutionClient` instance — deferred, P18 covers it live
- [x] [Review][Defer] The D-D `runner.run()` test sets `DEBOUNCE_SECONDS` to 0; the two-observation rule is proven at unit tier only — deferred
- [x] [Review][Defer] Repeated cache-moved skips are silent — deferred
- [x] [Review][Defer] Story 4.6's `test_the_connect_deadline_never_outlives_the_budget` failed once under full `-n auto` load, 5/5 green alone — timing flake, not 4.3 code — deferred

## Dev Notes

### The current surface — exact extension points

- `src/core/live_node_builder.py`: the exec-engine constants block (`:130-157`), the
  `LiveExecEngineConfig(...)` call (`:447-453`), `InteractiveBrokersLiveExecClientFactory.create`
  (`:659-662`).
- `src/core/live_session_runner.py`: `_phase_reconcile` (`:598-615`) — the startup grant goes after
  `reconcile_at_startup`; `_build_steady_state` (`:886-900`) — the reconciler; `_serve`
  (`:859-884`) — D-D's error surfaces here like `SessionReclaimedError`.
- `src/core/live_session_steady_state.py`: `run()` (`:279-300`) — one awaited call after
  `_observe_connection`.
- `src/core/live_connection_monitor.py`: `submission_withheld` (`:188-216`).
- `src/core/live_startup_reconcile.py`: helpers to make public; `ReconciliationFailedError.__init__`
  (`:137-151`) for `scope`; `_log_discrepancy`/`_log_ok` for the `scope` field.

### Scope boundaries — do NOT build

- Strategy position adoption, `external_order_claims`, or any strategy edit (Story 4.5).
- Corporate-action-specific handling (Story 4.7) — D-C's generic path is what 4.7 builds on.
- Anything in `ntrader live reconcile` / `live_reconcile.py` / `reconciliation_service.py` (4.6).
- A new exit code, DB column, migration, `SessionRecordPort` method, or `runtime_flags` key.
- The ⚖️ D-J rows the PO rules as re-routed.

### Hazards (each has bitten a previous story, or will)

- **Guard lists after a split** (CLAUDE.md): Task 2's destination is on all three lists; re-check
  and run them anyway.
- **A multi-line statement counts every line**: measure, don't estimate.
- **`asyncio.wait_for` cancels shared adapter futures** (Story 4.1 D-C): never wrap the reader.
- **`TestLiveNode` is shared** by `test_live_check_driver.py`, `test_epic1_ac_cli.py`,
  `test_epic1_ac_data.py` — every double addition must be defaulted.
- **`TestLiveNode(run_seconds=…)` races startup** (Story 4.2's intermittent): use the opt-in
  `run_seconds_from_first_strategy` in runner helpers.
- **A test that cannot fail** (Epic 2 retro): every "no call happened" assertion needs a sibling
  proving the spy records calls; every "≤ N records" needs a sibling proving records are captured.
- **The monitor's clock is injected**; drive it, never sleep.
- **`build_trading_node_config` / the exec factory are shared** by `live check`, `live reconcile`
  (4.6, client id `+1`) and `live_node_probe.py`, not just `live start`. D-A's open check and D-B's
  suppression therefore apply to those nodes too: harmless (a `live check` window may issue one
  `reqPositions` + `reqOpenOrders`; the `+1` client lists no orders), but assert it rather than
  assume it — `test_live_check_driver.py` and the 4.6 tests must stay green unmodified.

### Testing standards summary

Unit: the reconciler's policy against duck-typed stubs; the monitor's predicate. Component: the real
`LiveExecutionEngine`/`Cache` (no `TradingNode`, C-logging guard fixture), the runner and steady
state against `TestLiveNode`, the factory patch. Integration `--forked`: only the existing
`test_epic1_ac_node.py` scan. Markers on every test; TDD red first; each mutation recorded.

### Project Structure Notes

- New: `src/core/live_runtime_reconcile.py`, `src/core/live_exec_position_reports.py`,
  `tests/unit/core/test_live_runtime_reconcile.py`,
  `tests/component/core/test_live_runtime_reconcile_engine.py`,
  `tests/component/core/test_live_exec_position_reports.py`, optionally
  `tests/component/core/test_session_runner_runtime_reconcile.py`.
- Live modules live in `src/core/` (Story 3.5's precedent); no new `src/models/` value is needed —
  `PositionDiscrepancy`/`CachedPosition` are reused.

### References

- `_bmad-output/planning-artifacts/prd-epic4-scope.md` (Story 4.3 ACs; debt routed to 4.3)
- `_bmad-output/planning-artifacts/epics.md:1548-1571`
- `_bmad-output/planning-artifacts/architecture.md` (D2 broker-authoritative state; AR39; AR41 log
  naming)
- `_bmad-output/implementation-artifacts/4-2-reconcile-against-the-broker-before-any-strategy-trades.md`
  (D-A..D-N, F1..F14, Review Findings)
- `_bmad-output/implementation-artifacts/deferred-work.md` (`confirm_state_reestablished` `:615-631`;
  1101 `:678-707`; 10182/162 `:1975-1991, 2099-2123`; D6 `:2148`; conflation `:2407-2421`;
  `OrderTriggered` `:2268-2283`; epoch fence `:2695-2701`; phantom `:2817-2840`; unhandled code
  `:2897-2907, 2980-2983`; Story 4.1 `:3191-3198, 3220-3234`; Story 4.2 `:3355-3374`)
- `docs/qa/phase3-live-verification.md` (P11/P12 phantom evidence; P17)

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), BMAD dev-story workflow, 2026-09-27.

### Debug Log References

- **Task 0 (standing checks).** Head `78d59a1`, clean tree. `ruff check` clean; `make typecheck`
  clean (109 files). Baselines: unit **3039 passed**; component **1773 passed, 16 skipped**. F11
  re-measured exactly (runner file 495, `LiveSessionRunner` 424, `SessionSteadyState` 120,
  `ConnectionMonitor` 118).
- **Task 1 (measured; real `LiveExecutionEngine` + `Cache` + `Portfolio`, Story 4.2's IB-shaped
  NETTING harness; probes in `/tmp/p43/`, not committed).**
  - Harness note: the engine's continuous loop reads the engine clock, and the 4.2 harness uses a
    `TestClock` that never advances, so the consistency check **never fires** until the test
    advances it. Any component test of the loop must drive the clock.
  - 1.1 (F1/F3): with `open_check_interval_secs` set the loop calls
    `generate_order_status_reports(open_only=True)`. A fabricated `FILLED` report → no event
    (premise holds). Cached-open + venue-open → no event. Unknown venue-open → imported as
    `EXTERNAL` `ACCEPTED` (`reconciliation=True`). Cached-open + venue-gone → nothing (F1 holds).
  - **1.1b (new, contradicts half of D-A's premise):** a cached **CANCELED** order that the venue
    lists open is **not corrected** — `_reconcile_order_report` generates `OrderAccepted`, the
    order's `apply` fails with `InvalidStateTrigger` (logged, order stays CANCELED), and the engine
    **publishes the event anyway** (`execution/engine.pyx:1165-1178`: publish is unconditional
    after `_apply_event_to_order` returns). So every open-check cycle republishes an
    `OrderAccepted` for that order, forever: an `order.accepted` record per cycle, a Nautilus
    warning per cycle, and a reset of Story 3.7's refusal streak. This is exactly the shape F14's
    local cancel produces when IB later lists the order.
  - 1.2: `reconcile_execution_report` is synchronous; it publishes `OrderAccepted` + `OrderFilled`
    (`reconciliation=True`) + `PositionOpened`/`Closed` on `INTERNAL-DIFF`. The portfolio follows.
  - 1.3 (F4/F5): the **real** `_on_position_update` / `_handle_position_update`, driven on a
    stand-in, reproduce the phantom exactly: untracked `0 → 22` sends a LONG 22 report; the
    execDetails update then makes `_known_positions` 44; the next `22` update sends a second report.
    A `0` update sends nothing (blind to flat). The canary can drive these real functions.
  - 1.4 (F7): startup grant → drop (`connection.lost`) → back (`RECOVERING`, and today
    `submission_withheld` is **False** there) → confirm (`connection.restored downtime=30`); a long
    outage halts even while the socket is back (`RECOVERING → HALTED` inside `observe`), and a later
    confirm from `HALTED` restores. The reconciler must act only on `RECOVERING` (a `HALTED` +
    down reading would log `connection.recovery_refused` every tick).
  - **1.5 (new, affects D-C):** a stranded `ACCEPTED` order with the broker holding the position:
    the native pass returns `True`, imports `EXTERNAL +10`, and the stranded order stays `ACCEPTED`
    **forever** — no stall at component tier (the live stall was a portfolio-initialisation timeout
    a kernel-less harness cannot reproduce). Consequence for D-C as drafted: "defer instruments with
    working (open) orders" would leave that instrument **unverifiable for the rest of the session**.
  - **Gate:** 1.1b and 1.5 contradict premises of D-A and D-C. Recorded; put to the PO before Task 3.
    **Ruled 1A 2A (2026-09-27):** open check stays off (measured reason in the node-builder
    comment, no compensating workaround); defer only for in-flight orders. Recorded above the
    decisions; the remaining tasks follow the amended D-A / D-C.
- **Task 2 (budget split, a pure move, every tier green after it).** The bodies of
  `_flush_contained_failures` and `_flush_order_rejections` moved to module-level
  `flush_contained_failures` / `flush_rejection_snapshot` in `live_session_steady_state.py` (on
  `NODE_FACING_MODULES`, `STOP_PATH_MODULES` **and** `TestImportPurity.MODULES`, so the move escapes
  no scan — all three suites run green); `_warn_if_no_bars`'s test and emit moved to
  `no_bars_overdue` / `warn_no_bars`. Runner file **495 → 470**, `LiveSessionRunner` 424 → 398;
  `SessionSteadyState` 120 → 111. Unit 3039, component 1773/16 skipped, integration core 97/2
  skipped — identical to baseline.
- **Tasks 3–7 (wiring measured after each step).** Final sizes: runner file **485/500**,
  `LiveSessionRunner` **412** (baseline 424 → 412, with a reason), `run` 66 (unchanged);
  `SessionSteadyState` **114** (baseline 120 → 114); `ConnectionMonitor` 118 → **116** (the predicate
  got shorter); `build_trading_node_config` 128 → **129** (the explicit `open_check_interval_secs`
  kwarg). New modules: `live_runtime_reconcile.py` file 253, largest class `RuntimeReconciler` **67**
  (a first draft measured **174** — over the cap — and was split into `_Debounce`, `_Unresolved`,
  `_OkLog`, `_FailureStreak` and module-level `observe` / `correct` / `verify_corrected` / `refuse`
  before going further), largest function 28; `live_exec_position_reports.py` file 31.
- **Engine-loop timing (component tests).** The continuous loop *decides* on the engine clock but
  *sleeps* `min(intervals)` of real time (2 s under the session's config), so the two loop-driven
  tests use a 50 ms interval and step the clock; the session's real values stay pinned by the
  builder tests.
- **Guard lists (Task 7).** Both new modules added to `NODE_FACING_MODULES` and
  `TestImportPurity.MODULES` with reasons; neither to `STOP_PATH_MODULES` (comment added there, the
  `live_broker_state` precedent). `_STDLIB_AND_FIRST_PARTY` needed no edit — proven by **running**
  `tests/integration/core/test_epic1_ac_node.py` (8/8). `LIVE_MODULE_GLOBS` scans both;
  `test_live_dependency_invariance.py`, `test_order_path_has_no_retry.py`,
  `test_live_stop_path_is_inert.py`, `test_exit_outcome_markers.py` (no `UNMARKED` growth) green —
  278 passed across the guard suites.
- **Task 8.3 (live).** `ntrader live check` (read-only, the one surface reaching the patched
  exec-client factory) was attempted: `Cannot build an IBKR execution client: TWS_ACCOUNT is not set`
  in 0.00 s, before any socket — the worktree has no `.env`. Every part of Procedure P18 needs
  `live start` or a manual TWS trade, so it is **defined, not run** (operator only).
- **Task 10 (mutation sweep): 16 of 16 killed**, each by a genuine test failure (pytest summary
  captured per mutation, `-x`), files restored and re-verified: M1 act without debounce; M2 debounce
  keyed on instrument only; M3 no cache-moved skip; M4 no in-flight deferral; M5 `reconcile.ok`
  every cycle; M6 grant before verifying the reconnect; M7 grant with in-flight orders; M8
  `submission_withheld` back to the old closed form; M9 D-B still sends the report; M10 resolve a
  contradicted strategy row; M11 log the raw failure text; M12 the reader's own records not
  quieted; M13 contain D-D's error inside the tick; M14 the tick never calls the reconciler; M15 no
  startup grant; M16 the factory does not install D-B.
- **Gates (Task 11, final).** `ruff format --check` (545 files) and `ruff check` clean;
  `make typecheck` clean (111 files). Unit **3088 passed** (+49); component **1808 passed, 16
  skipped** (+35); `tests/integration/core --forked` **97 passed, 2 skipped**. `grep is_live
  src/core/strategies/` → 0. The D-K zero-diff set shows an empty `git diff --stat`;
  `live_order_path.py` and `live_session_phases.py` changed in docstrings only.

### Completion Notes List

- **What shipped.**
  1. **Native continuous reconciliation, pinned for the whole session (AC #1).** The in-flight
     sweep (Story 3.4's values) runs as Nautilus's `continuous_reconciliation` task from engine start
     to stop — proven against a real `LiveExecutionEngine` built from the session's own config. The
     open-order consistency check is passed **explicitly off**
     (`EXEC_ENGINE_OPEN_CHECK_INTERVAL_SECS = None`), with the measured reason in the node-builder
     comment and a canary pinning the republish (PO ruling 1A at the Task 1 gate).
  2. **The IB adapter's position-update reports are switched off (D-B, PO ruling 1A)** —
     `src/core/live_exec_position_reports.py`, installed by the exec-client factory beside the
     avg-px fix (same pattern, same loud-at-build-time refusal). Tests drive the adapter's **real**
     `_on_position_update` / `_handle_position_update` on a stand-in: without the patch they
     reproduce the P11/P12 phantom exactly (two reports for one 22-share order; blind to flat); with
     it the feed sends nothing and logs `reconcile.position_update_deferred`.
  3. **A verified runtime cycle (`src/core/live_runtime_reconcile.py`, AC #2/#3).** On the heartbeat
     tick, every 2 ticks while `CONNECTED`: snapshot → broker read (its own per-read records dropped)
     → skip if the cache moved → exact compare → defer only instruments with an order **in flight**
     (PO ruling 2A) → act only on a row identical on two checks ≥ 60 s apart. Corrections go
     broker-ward through `reconcile_execution_report` only (AST-pinned) and each is logged
     `reconcile.discrepancy scope=runtime resolution=broker` with the instrument and quantities;
     `reconcile.ok` is logged on the first clean cycle, after any problem, and otherwise at most
     hourly (390 clean cycles → ≤ 8 records, exact `cycles` accounting).
  4. **A runtime strategy contradiction stops the session (D-D, PO ruling 2A).** Refused before
     anything is written; `ReconciliationFailedError(STRATEGY_POSITION_CONTRADICTED,
     scope="runtime")` out of the tick; the runner's ordinary teardown (the inert stop path —
     nothing closes, flattens or resizes; `close_all_positions()` is called nowhere in the core
     modules the stop-path scan covers — *as written, "nowhere in `src/`" was false: the
     strategies submodule's `custom/sma_crossover_long_only.py` calls it in `on_stop`, and the
     registry discovers `custom/`, so a session that named that strategy would flatten on stop.
     Neither built-in does. PR #35 code review, P8*); exit
     1 naming the instrument and both quantities. Proven through `runner.run()` against a broker
     double and against the real engine (the strategy's +22 untouched, no synthetic position).
  5. **Trading permission after a reconnect (AC #4, D-F).** `confirm_state_reestablished` goes live
     at exactly two call sites, both in `live_runtime_reconcile`: the startup grant at the end of
     `reconcile` (after the cache is proven), and the reconnect re-confirm after a clean cycle with
     nothing in flight — only after the state check passes (PO). `submission_withheld` is now
     `not trading_permitted`, so `RECOVERING` withholds every order (a real strategy's crossover
     proven suppressed, and the same crossover proven to trade once granted). The zero-callers pin
     became an exact caller-set pin over every module under `src/`.
- **PO condition 1A — the accepted lag, recorded:** a position change the execution stream did not
  explain (a manual TWS trade, an option exercise, a split) is corrected **one to two runtime cycles
  (~60–120 s) after it happens**, not on arrival, because the adapter's instant (and wrong) report
  path is off and a row must repeat before it acts. After a reconnect, a clean state grants on the
  first tick; a discrepancy must debounce first, so the monitor may report `connection.halted`
  meanwhile and `connection.restored` after.
- **PO condition 2A — "`ACCEPTED` stays open after a restart", recorded (Task 1.5, measured):** with
  the broker holding the position, the framework's startup pass returns `True`, imports `EXTERNAL`,
  and leaves the stranded order `ACCEPTED` for the life of the process — nothing native clears it
  with IB. The runtime cycle therefore defers **only in-flight** orders, so that order never makes its
  instrument unverifiable. The stranded order itself, and the live portfolio-initialisation stall it
  caused in `p7-position-test`, go to **Story 4.5** (the restart path).
- **PO condition 2A — the not-open/cancelled conflation, dispositioned:** the in-flight sweep's
  query answers "not found" with a **local** `Cancelled`, so a lost fill reads as a cancel. Its
  *position* consequence is caught: the runtime cycle detects, logs and corrects it broker-ward — or,
  if it contradicts a strategy's own position, refuses and stops the session. Its *order record*
  cannot be corrected (the adapter's `generate_fill_reports` returns `[]`): recorded as an upstream
  adapter limit, **unowned, named**.
- **PO condition 3A — D6, dispositioned: re-routed to the Epic 4 retrospective as a dedicated story,**
  with the measured per-code mechanism attached: 366/10189/102 — the adapter cancels and re-issues
  the subscription itself; 10182 — clears `_is_ib_connected`, and the 1 s watchdog then degrades,
  waits 5 s, reconnects and resubscribes everything **unless** the flag is re-set first (the P5
  shape; what re-set it there is unmeasured); 162 — logged, nothing done (a competing login; a
  resubscribe fails until it ends); 1101 — sets `_is_ib_connected` with no resubscription. Fix
  shape: an instance-level hook on the shared IB client's error path calling `_resubscribe_all()` on
  1101 and logging `connection.market_data_lost` on 162; verification needs broker-side fault
  injection. None of these codes reaches our code, and none is in 4.3's ACs.
- **PO condition 3A — the takeover gap (pre-submit `owner_epoch` window), dispositioned: re-routed to
  the Epic 4 retrospective as a dedicated story,** with the measurements attached: the fence is
  checked at every trade write and by the heartbeat (every 30 s, detection ≤ 90 s), but the order
  wrapper consults only `submission_withheld`, so a reclaimed incumbent can submit until its next
  heartbeat or trade write. Fix shape: an ownership probe (`record.record_activity(...)`, which
  already raises `SessionReclaimedError` on an epoch mismatch) before each submission, suppressing
  and requesting a stop on a reclaim — one synchronous DB round trip per order (~6 ms measured live,
  P12), which is Story 3.6 D-A's stall exposure and needs its own two-process AC.
- **Every other routed item** is dispositioned in `deferred-work.md` ("Deferred from: story-4.3"),
  with in-place notes on the entries that named 4.3: phantom **fixed**; `OrderTriggered` **closed
  as unreachable** (docstring + canary); unhandled IB error code **measured and bounded** (~7 s, no
  mapping — it would invite a duplicate order); open orders compared only venue-open-ward (check off,
  per 1A); cached-order-fills-mid-read **fixed for runtime**; diverted position updates **moot**;
  stranded `OpenPositions` request **fails closed**; `confirm_state_reestablished` **live**.
- **`reconciliation_owned` persist-skip policy untouched** (PO 2A): `live_trade_recorder.py` is
  zero-diff; runtime corrections land in `INTERNAL-DIFF` and their round trips are persist-skipped
  exactly as before.
- **Story-text corrections (recorded, not silently absorbed).**
  - AC #1's drafted `open_check_interval_secs == 60.0` pin became `is None` (PO ruling 1A, Task 1
    gate); AC #2's "working order" deferral became "in-flight order" (2A).
  - AC #3's "Σ `cycles` == 390" holds as Σ `cycles` + the clean cycles not yet folded into a record
    (`unreported_clean_cycles`) == 390 — an hourly throttle leaves a tail after the last record.
  - `SessionSteadyState` did not end "under 120 with no baseline edit" as D-I hoped: it ends at 114,
    so the exact-ratchet baseline moved 120 → 114 (down), with a reason.
  - The runtime stop message reads `Runtime reconciliation stopped this session (runtime,
    strategy_position_contradicted): …`; the startup message is byte-identical to Story 4.2's.
  - New, found while testing: when `observe` itself crosses the halt deadline (`RECOVERING →
    HALTED`), the reconciler skips that one tick; the next tick's observation returns to
    `RECOVERING` and the reconnect cycle runs. Recorded in `deferred-work.md`, no owner unless seen
    live.

### File List

New:
- `src/core/live_runtime_reconcile.py`
- `src/core/live_exec_position_reports.py`
- `tests/unit/core/test_live_runtime_reconcile.py`
- `tests/component/core/test_live_runtime_reconcile_engine.py`
- `tests/component/core/test_live_exec_position_reports.py`
- `tests/component/core/test_session_runner_runtime_reconcile.py`
- `tests/component/core/test_live_order_path_triggered_canary.py`
- `_bmad-output/implementation-artifacts/4-3-keep-runtime-state-aligned-with-the-broker.md`

Modified:
- `src/core/live_connection_monitor.py` — `submission_withheld` is `not trading_permitted`; docstrings
- `src/core/live_node_builder.py` — open check passed explicitly off (measured reason); the factory installs D-B
- `src/core/live_session_runner.py` — startup grant in `reconcile`; the reconciler built in `_build_steady_state`; flush bodies moved out; docstrings
- `src/core/live_session_steady_state.py` — the tick awaits the reconciler; flush bodies and the no-bars test/emit moved in (D-I); docstrings
- `src/core/live_startup_reconcile.py` — `scope` on records and on `ReconciliationFailedError`; `position_report` / `log_discrepancy` public; `unresolved` at ERROR
- `src/core/live_order_path.py`, `src/core/live_session_phases.py` — docstrings only
- `src/cli/commands/live.py` — `live start` help text only
- `README.md`, `docs/agent/nautilus.md` ("Runtime Reconciliation"), `docs/qa/phase3-live-verification.md` (Procedure P18)
- `_bmad-output/implementation-artifacts/deferred-work.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml` (the 4-3 key only)
- `tests/component/doubles/test_live_node.py` — defaulted `orders_inflight()`
- `tests/component/core/test_live_node_builder.py` — open check deliberately-off pins
- `tests/component/core/test_live_exec_avg_px.py` — the factory stand-in carries D-B's two attributes
- `tests/component/core/test_session_runner_order_path.py`, `test_session_runner_phases.py` — the grant at `reconcile` (named changes); `TestImportPurity.MODULES`
- `tests/component/core/test_session_steady_state.py` — the exact caller-set pin; a stale docstring
- `tests/unit/core/test_live_connection_monitor.py` — `TestSubmissionWithheld` rewritten deliberately
- `tests/unit/core/test_live_node_never_exits.py`, `tests/unit/core/test_live_stop_path_is_inert.py` — guard lists
- `tests/unit/cli/commands/test_live_cli.py` — the runtime stop's CLI message and exit 1
- `tests/unit/governance/test_size_caps.py` — four baselines, each with a reason

## Change Log

| Date | Change |
| ---- | ------ |
| 2026-09-27 | Story created (ready-for-dev). D-B, D-D and D-J's two re-routes put to the PO; ruled 1A 2A 3A with conditions (recorded above the decisions). |
| 2026-09-27 | Task 1 gate: two measured findings contradicted D-A (open check republishes `OrderAccepted` for a locally-cancelled order) and D-C (a stranded `ACCEPTED` order would block verification); put to the PO, ruled 1A 2A (open check off; defer only in-flight). |
| 2026-09-27 | Implemented. All tasks done; 16/16 mutations killed; every tier green (unit 3088, component 1808/16 skipped, integration core 97/2 skipped). P18 defined, not run (operator only; no `.env`). Status → review. |
| 2026-09-28 | Code review (three layers, by hand after the harness review turn hit an API timeout): 1 decision ruled by the PO (narrow the unresolved-row block), 8 patches applied with tests, 6 deferred. Status → done. |
