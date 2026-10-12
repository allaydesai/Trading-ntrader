# Story 5.9: Restore the Bar Feed After a Reconnect, and Never Trade on a Catch-Up Bar

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a session that rides out a disconnect to get its bars back and to say so,
so that a session never sits connected, reconciled and permitted to trade while it can no longer
hear a price, and never trades on prices that went stale during the outage.

*(Added 2026-10-12 from the Epic 5 live verification, finding L1 in `deferred-work.md`. PO rulings,
Allay 2026-10-12: "fix it ourselves" (a local shim in the `live_exec_avg_px` pattern, with an
upstream canary), and "ignore the missed prices for trading" (catch-up bars still reach the
indicators, but no order acts on them).)*

## Why this story is shaped the way it is

Measured at drafting (2026-10-12, head `4df9cdf` on `015-paper-trading`, main checkout, installed
`nautilus-trader` 1.220.0). Adapter paths are relative to
`.venv/lib/python3.11/site-packages/nautilus_trader/adapters/interactive_brokers/`.

### Measured facts — cite, don't re-derive

- **F1 — the live failure.** In `logs/p23-1012.log` the adapter reconnected at 18:56:09Z.
  `_resubscribe_all` then logged `Failed to resubscribe to Subscription(req_id=10006,
  name='NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL' ...)` with `TypeError(... subscribe_historical_bars()
  got an unexpected keyword argument 'start')`. The last `live_bars.received` was 18:48:11Z, and
  none arrived in the 4½ minutes before the stop. `connection.restored` (18:56:23Z) and
  `reconcile.ok scope=reconnect` both still fired. `live status` read `health: idle`.
- **F2 — the stored handle is malformed in two ways.** `_subscribe` (`client/market_data.py:147-152`)
  stores the historical-bars handle as `functools.partial(subscription_method, *args, **kwargs)`.
  The kwargs come from `subscribe_historical_bars` (`:457-465`): `bar_type=, contract=, use_rth=,
  handle_revised_bars=, start=start`. The method's signature (`:416-423`) is `(self, bar_type,
  contract, use_rth, handle_revised_bars, params)`. So the stored call passes a `start` that the
  method does not accept **and** omits a `params` that it requires. Tolerating `start` alone would
  still raise.
- **F3 — the shape the adapter depends on.** The handle must stay a `functools.partial` with those
  keywords. `process_historical_data` reads `subscription.handle.keywords.get("start")`
  (`:897-898`). `process_historical_data_update` raises `TypeError` unless the handle is a partial
  (`:933-934`) and reads `keywords.get("handle_revised_bars")` (`:939`).
- **F4 — method identity.** `_subscribe` compares `subscription_method ==
  self.subscribe_historical_bars` (`:147`, `!=` at `:169`), and both sides are looked up at call time.
  If the replacement is an **instance attribute** on the client, `self.subscribe_historical_bars`
  returns the same wrapper object on both sides, so the equality holds. The stored partial then wraps
  the wrapper, which is exactly what this story needs.
- **F5 — the initial call.** `data.py:263-269` calls `self._client.subscribe_historical_bars(bar_type=,
  contract=, use_rth=, handle_revised_bars=, params=command.params)`. The data engine sets
  `command.params["start_ns"]` (`data/engine.pyx:1136-1138`), which is `None` in a live node with no
  catalog. With no `start_ns` the method requests about 300 bars with `keepUpToDate=True` (`:443-455`).
  `live_bar_observer.py:383-390` deliberately passes no `params` and explains why.
- **F6 — resubscribe reuses the subscription.** On resubscribe `_subscribe` finds the entry by name
  (`:141`, "Reusing existing Subscription instance", `:178`) and re-issues `reqHistoricalData` under
  the **old `req_id`**. The stored `start` keyword is unchanged (it is the subscription instant).
  `_resubscribe_all` (`client/client.py:325-342`) catches every exception and only logs it, so a
  failed bar resubscribe is invisible to the session.
- **F7 — the catch-up burst.** `_bar_type_to_last_bar` is never cleared on degrade, reset or
  resubscribe (written only at `market_data.py:1153`). `_process_bar_data` (`:1142-1151`) drops a
  bar older than the last one seen as "Out of sync", and the history batch's `start` filter
  (`:1157-1159`) drops bars before the subscription instant. **Every bar that closed during the
  outage passes both checks and is published in one burst** once the resubscribe works.
- **F8 — catch-up bars are not "late" by their own timestamps.** For a history-batch bar,
  `ts_init` is set to the bar's own period end (`if historical: ts_init = await
  self._ib_bar_to_ts_init(bar, bar_type)`, `:1175-1176`; `_ib_bar_to_ts_init` docstring at
  `:1343-1349`), not its arrival time. Two consequences:
  - `LiveBarObserver._check_freshness` (`src/core/live_bar_observer.py:443-491`, grace 120 s,
    `live_market_data.py:57`, 3 in a row → `shutdown_system`) reads catch-up bars as on time.
    **Confirm this in Task 1.** If it does not hold, the burst would shut the session down.
  - A catch-up bar can only be recognised against the **wall clock at the moment it is handled**,
    never by its `ts_init`.
- **F9 — the existing withhold usually covers the burst, but not by construction.** The adapter
  resubscribes inside its own reconnect (`connection.py:115-116` → `_reset` → `_resume` →
  `_resubscribe_all`). Our monitor is still `RECOVERING` until the next 30 s tick's clean
  `reconcile.ok scope=reconnect` → `grant_after_reconciliation` → `confirm_state_reestablished`
  (`live_runtime_reconcile.py:264-290, 464-479`). So a burst that lands before that grant is already
  suppressed (`order.suppressed`). A burst that lands after it is not. Nothing orders the two: the
  history reply's latency against the tick phase is a race. The PO's ruling has to hold by design,
  not by timing.
- **F10 — how a bar reaches the strategy.** Message-bus subscribers at equal priority run in
  subscription order (component.pyx `:2787-2795`). The runner's `data.bars.*` handlers
  (`steady_state.note_bar` `:681`, `OrderEventObserver.note_bar` `:692`) and the `LiveBarObserver`
  subscribe during the `subscribe` phase, before strategies subscribe in `on_start`. So they see each
  bar **before** the strategy's `handle_bar`, in the same synchronous dispatch. `Actor.handle_bar`
  updates registered indicators and then calls `on_bar`, so indicators see every bar.
- **F11 — the order path's hook.** `install_order_path(strategy, monitor, log)`
  (`live_order_path.py:252-275`, called at `live_session_runner.py:835` with `self._monitor`) wraps
  `ORDER_CREATING_METHODS`. It only reads `bool(monitor.submission_withheld)` (`:278-300`), so the
  monitor is duck-typed. `order.suppressed` carries `method`, `instrument_id`,
  `client_order_id` (submit_order) and `strategy_id`, and has no reason field.
- **F12 — one shared IB client.** `get_cached_ib_client` (`factories.py:112-127`) keys on
  `(host, port, client_id)`. Data and exec use the same `ibkr_live_client_id`
  (`live_node_builder.py:389-391`, "they share ONE socket"), so the exec client's `_client` **is**
  the data client's `InteractiveBrokersClient`. Exec shims are installed in
  `InteractiveBrokersLiveExecClientFactory.create` (`live_node_builder.py:702-707`). The data factory
  is the stock one (`:660`), and two integration tests pin it:
  `tests/integration/core/test_epic1_ac_node.py:198,207` and
  `tests/integration/core/test_live_node_lifecycle.py:93,101` assert
  `builder._data_factories[IB] is InteractiveBrokersLiveDataClientFactory`.
- **F13 — size budget (statement metric, `tests/unit/governance/test_size_caps.py`).** Zero headroom
  on every class this story might be tempted to touch:

  | Class | Ceiling | Current |
  |---|---|---|
  | `ConnectionMonitor` | 134 | 134 |
  | `OrderEventObserver` | 165 | 165 |
  | `LiveBarObserver` | 120 | 120 |
  | `SessionSteadyState` | 120 | 120 |
  | `LiveSessionRunner` | 417 | 417 |
  | `LiveSessionRunner.run` | 66 | 66 |

  Two near-limits: `RuntimeReconciler` is 99/100 (unlisted), and the `live_session_runner.py` file
  is **493/500**. `build_trading_node_config` has a baseline of 130. New logic therefore goes in
  **new modules**.
- **F14 — tests that encode today's behaviour and must change deliberately:**
  - the guard lists (Task 6);
  - `TestInstall`'s wiring pins in `tests/component/core/test_live_exec_avg_px.py:255-331`, if
    `create` grows a call (read before editing; they pin the exec factory's identity, and must not
    break);
  - the `install_order_path` call site, if the gate is passed in place of `self._monitor`.

### Design decisions (disclosed here, not discovered in review)

- **D-A — install the shim on the shared client from the exec factory's `create`.** Call
  `install_bar_resubscribe_fix(client._client, ...)` beside the two existing shims
  (`live_node_builder.py:702-707`). It must run inside `node.build()`, before any `subscribe_bars`
  (strategies subscribe in `on_start`), and it does (F12). A data-factory subclass would break the
  two integration pins in F12 for no gain. Disclose the indirection in the module docstring:
  "installed via the exec factory because the client is shared".
- **D-B — the shim's shape.** It sets an instance attribute `subscribe_historical_bars` on the
  `InteractiveBrokersClient`, keeping F4's identity equality. The wrapper is `async def(bar_type,
  contract, use_rth, handle_revised_bars, params=None, start=None)`:
  - **A call with `params`** is the adapter's initial path (F5). Delegate unchanged and log nothing.
  - **A call without `params`** is the adapter's resubscribe (F2). Delegate with
    `params={"start_ns": None}`, which keeps the 300-bar window (F5). Do not set `start_ns`: the
    stored `start` keyword already filters the batch (F6, F7), and `live_bar_observer.py:383-390`
    explains why an explicit start is wrong.
  - On success, emit `bars.resubscribed` (`bar_type`). On any exception, emit
    `bars.resubscribe_failed` (`bar_type`, `error_type`; never `str(exc)`, per 5.8 P4), notify the
    failure sink (D-D), then **re-raise** so the adapter's own log line is unchanged.
  - Idempotent, as `install_avg_px_serialization_fix` is. Loud at build time: if the client lacks
    `subscribe_historical_bars`, raise `AttributeError`.
- **D-C — a catch-up bar is one handled more than the delayed-data grace after its period
  closed.** Measured on the wall clock at handling (F8), via the session's `LiveClock`:
  `now_ns - (ts_event + interval_ns) > grace`.
  - **Proposed grace:** reuse `DEFAULT_DELAYED_DATA_GRACE_SECONDS` (120 s,
    `live_market_data.py:57`), so "late" means the same thing to the freshness guard and the gate.
    A live IB minute bar is handled about 5 s after close (P7's 2026-09-01 row:
    `bar_close_to_submit_ms≈5450`).
    - **Why not tighter:** at startup, the adapter's first published bar is the last completed
      history bar of the initial batch (memory: "`live_bars.received` also counts the last history
      bar"). That bar can be up to about one interval past its close. A 60 s grace would mark it
      catch-up at every start. Task 1.3 measures it.
  - **This is the operational form of the ruling's "closed before `connection.restored`".** Any bar
    that closed more than 120 s before it is handled after the reconnect is withheld. The last bar
    or two of a burst, within 120 s of close, are as fresh as the guard already accepts. The test
    needs no restore timestamp (the monitor stores none, F11, and is at its cap, F13), and it holds
    whichever side of the grant the burst lands on (F9).
  - **Ruled R1 (2026-10-12): accepted as stated.** The `note_restored()` fallback (a hook called
    from `grant_after_reconciliation`) is not built.
- **D-D — a failed resubscribe stops the session (exit `1`).** This follows adapter drift (Story
  5.8 AC #2). A session that cannot hear a price cannot recover by withholding, and P23's evidence
  shows a deaf session otherwise sits "restored" for its whole life.
  - The shim notifies a small failure sink installed at build. The runner's steady-state tick (or
    the guard path that drift uses, whichever Task 0's budget admits) stops the session with a named
    reason.
  - **If Task 0 shows no in-cap path, stop and bring it back.** Do not raise a ceiling silently.
- **D-E — the gate wraps the monitor; the order path is not edited.** A new `CatchUpGate(monitor,
  clock)` exposes `submission_withheld = monitor.submission_withheld or <the bar now being handled
  is catch-up>`. The runner passes it to `install_order_path` in place of `self._monitor`.
  - The gate learns "the bar now being handled" from its own `note_bar`, subscribed on
    `data.bars.*` beside `OrderEventObserver.note_bar` during `subscribe` (F10), so it always runs
    before the strategy's `handle_bar` for the same bar.
  - It only notes **traded** bar types (each spec's `bar_types[0]`, as `OrderEventObserver.note_bar`
    does: runner `:690-692`).
  - An order raised outside any bar handler (a fill event, a timer) is judged against the last noted
    bar. Disclose this. It is conservative: a session still catching up does not trade on an event
    either.
- **D-F — records.** A new pinned tuple, `EMITTED_BAR_FEED_EVENTS`:
  - `bars.resubscribed` and `bars.resubscribe_failed` (the shim);
  - `bars.catch_up`, emitted **once per burst** when the first non-catch-up bar follows one or more
    catch-up bars, with `bar_type`, `count`, `first_ts_event` and `last_ts_event`;
  - `bars.catch_up_order_withheld`, emitted when the gate (not the monitor) is the reason an order
    was withheld, with `bar_type` and `ts_event`. The order path's own `order.suppressed` follows it
    unchanged.

  Pin the tuple both ways against the code: an AST scan of every `_emit`/`logger.*` name in the
  two new modules, plus every path driven. Parametrize the NFR26 scan from it (the
  `EMITTED_RECONCILE_EVENTS` precedent, CLAUDE.md). Update CLAUDE.md's Anti-Patterns with one
  bullet naming the new pinned constant.
- **D-G — out of scope, disclosed:** a reconnect that outlasts the 300-bar window (more than 5 h of
  1-minute bars) leaves a gap in the indicators. The adapter does not backfill past its window, and
  this story does not add a history request. Epic 4's warm-up `request_bars` is the tool if this is
  ever needed.

### PO rulings (2026-10-12, before dev)

- **R1 — D-C accepted.** "Handled more than 120 s (the delayed-data grace) past its close" is the
  accepted operational form of "closed before `connection.restored`". There is no restore-instant
  wiring; the `note_restored()` fallback is not built.
- **R2 — D-D accepted.** A failed bar resubscribe stops the session with exit `1` and a named
  record, as adapter drift does. It never stays up blocked.

## Acceptance Criteria

Copied from epics.md (Story 5.9) and numbered for traceability.

1. **Given** the IB adapter's reconnect path (`_resubscribe_all`) re-issues a session's bar
   subscription, **When** it does, **Then** the subscription is restored. One `bars.resubscribed`
   record names the bar type, and live bars resume.
2. **Given** the bar resubscribe fails anyway (any exception), **When** it is noted, **Then** one
   `bars.resubscribe_failed` record names the bar type and error type, and the session does not
   report a healthy, trading state while deaf (D-D: it stops with exit `1`).
3. **Given** bars that closed before `connection.restored` and arrive after the resubscribe (the
   catch-up burst, F7), **When** they reach the strategy, **Then** the indicators see them, no order
   acts on them (any order such a bar triggers is withheld and named), and the number skipped is
   recorded once per reconnect (D-C, D-E, D-F).
4. **Given** the upstream defect, **When** Nautilus is upgraded, **Then** a canary pinned against
   the adapter's own source goes red if the stored handle's shape or `subscribe_historical_bars`'s
   signature changes, so the shim becomes deletable by name.
5. **Given** live procedure P24 (P23a's freeze, re-run), **When** the Gateway is restored, **Then**
   `live_bars.received` resumes after `connection.restored`, and no order fills on a bar that
   closed during the outage.

**Traceability gate note (NFR33):** AC #1–#4 are proven against a real `InteractiveBrokersClient`
(no socket) and duck-typed doubles. AC #5 is live procedure P24: defined in this story, run by the
operator.

## Tasks / Subtasks

- [x] **Task 0 — Budget first** (F13)
  - [x] 0.1 Measure `live_session_runner.py` (file and class), `live_node_builder.py`
    (`InteractiveBrokersLiveExecClientFactory`) and `live_session_steady_state.py` with
    `measure_module` **before** editing. Write the numbers into Debug Log References.
  - [x] 0.2 Choose D-D's stop path (steady-state tick vs the drift path) by which one fits under
    every ceiling. If none fits, stop and ask. Never raise a ceiling without a one-line reason in
    the same commit (CLAUDE.md).
- [x] **Task 1 — Measurement gate** (F8, F9; AC #3)
  - [x] 1.1 On a real `InteractiveBrokersClient` (the `test_live_connection_probe.py:304-317`
    construction: own loop, `MessageBus`, `Cache`, `LiveClock`, no socket), feed `_process_bar_data`
    a previous bar, then an outage's worth of history bars (`historical=True`). Assert the published
    bars carry `ts_init == period end` (F8). If not, D-C changes and so does the freshness hazard:
    stop and re-plan.
  - [x] 1.2 Assert that `_resubscribe_all` on the stock client raises nothing and logs `Failed to
    resubscribe` for a historical-bars subscription. This reproduces F1 without a broker, and is the
    red test Task 2 turns green.
  - [x] 1.3 Measure, from the live transcripts already on disk, how far past its close the
    **first** bar of a session is handled:
    - `logs/p23-1012.log`: session start 18:45:13Z, first `live_bars.received`;
    - `logs/p11-20260921.log` and `logs/p12-20260921.log`, if present.

    It must sit inside D-C's grace with margin. If any is within 15 s of the grace, stop and bring
    D-C back.
- [x] **Task 2 — The shim** (AC #1, #2, #4) — `src/core/live_bar_resubscribe.py`
  - [x] 2.1 Failing tests first, in `tests/component/core/test_live_bar_resubscribe.py`:
    - `_resubscribe_all` on a real client with the shim re-issues `reqHistoricalData` under the
      same `req_id` (spy the `_eclient`);
    - the stored handle is still a `functools.partial` with `start` and `handle_revised_bars`
      keywords (F3);
    - the initial call is unchanged;
    - `bars.resubscribed` is emitted once, with no account in it;
    - a raising delegate emits `bars.resubscribe_failed` with `error_type`, notifies the sink, and
      re-raises;
    - installing twice is harmless; a client without the method raises `AttributeError`.
  - [x] 2.2 Canary class `TestTheAdapterDefect`, reading the adapter's source with
    `inspect.getfile` (the `test_live_exec_avg_px.py:100-163` pattern):
    - `inspect.signature(InteractiveBrokersClient.subscribe_historical_bars)` has `params` and no
      `start`;
    - the source still contains `start=start` inside `subscribe_historical_bars`'s `_subscribe`
      call;
    - `_subscribe` still compares `subscription_method == self.subscribe_historical_bars`;
    - `_resubscribe_all` still catches and logs.

    Each assertion message names this module as deletable when it goes red.
  - [x] 2.3 Implement D-B and wire D-A in `live_node_builder.py`, beside the two existing
    `install_*` calls. Extend `TestInstall`'s driven-`create` test to assert the new call (F14).
- [x] **Task 3 — The catch-up gate** (AC #3) — `src/core/live_catch_up_gate.py`
  - [x] 3.1 Failing unit tests (`tests/unit/core/test_live_catch_up_gate.py`), with a fake clock and
    real `Bar` objects:
    - a bar handled within the grace does not withhold;
    - a bar handled past it withholds;
    - the monitor's own withhold still withholds;
    - `bars.catch_up` is emitted once per burst with the right `count` and first/last `ts_event`;
    - `bars.catch_up_order_withheld` is emitted only when the gate, not the monitor, is the cause;
    - non-traded bar types are ignored;
    - every record passes the `RAW_ACCOUNT` absence check.

    Each "nothing happened" test has a sibling where the spy records (5.8 lesson).
  - [x] 3.2 A component test through the real order-path wrapper: `install_order_path(strategy,
    gate, log)` on a `SimpleNamespace` strategy with a spy `submit_order` (5.8's pattern). Show
    `order.suppressed` for a catch-up bar, a submit for a live bar, and that the indicator registered
    on a real strategy is updated by the catch-up bar (AC #3's "indicators see them").
  - [x] 3.3 Wire the gate in the runner: construct it, subscribe its `note_bar` beside
    `OrderEventObserver.note_bar`, and pass it to `install_order_path`. All of this must fit Task 0's
    budget.
- [x] **Task 4 — The stop on a failed resubscribe** (AC #2) — D-D's path from Task 0.2. Tests in
  the runner tier (`tests/component/core/test_session_runner_*.py`): a failed resubscribe ends the
  session with exit `1`, a named reason, and nothing sent to IBKR.
- [x] **Task 5 — Records and pins** (D-F) — `EMITTED_BAR_FEED_EVENTS`, its two-direction pin, the
  NFR26 parametrized scan, and a CLAUDE.md Anti-Patterns bullet.
- [x] **Task 6 — Guard lists, in the creating commit** (CLAUDE.md):
  - add both new modules to `NODE_FACING_MODULES` (`tests/unit/core/test_live_node_never_exits.py`)
    and `TestImportPurity.MODULES` (`tests/component/core/test_session_runner_phases.py`);
  - list both in `STOP_PATH_MODULES`'s non-member comments with a reason (build-time shim; order-path
    gate, not on the teardown path; `live_exec_avg_px` and `live_order_path` are the precedents);
  - add any new stdlib import to `_STDLIB_AND_FIRST_PARTY` (`tests/integration/core/test_epic1_ac_node.py`)
    with a reason.
- [x] **Task 7 — Docs and procedure P24** (AC #5)
  - [x] 7.1 `docs/qa/phase3-live-verification.md`: add `## Procedure P24: bars resume after a
    reconnect, and no order acts on a catch-up bar`. Base it on P23a's freeze. Strip ANSI before
    every `until grep` (L3). Pass criteria:
    - `bars.resubscribed` after the reconnect;
    - `live_bars.received` after `connection.restored`;
    - one `bars.catch_up` with `count` ≈ the outage's minutes;
    - zero `order.submitted` on a bar whose `ts_event` is inside the outage;
    - D6, and NFR26 (our records only).

    Add a phase-gate index row and a "Defined, not run" Result-log row.
  - [x] 7.2 `deferred-work.md` L1: mark it routed to Story 5.9. In P22's and P23's Result-log
    findings, note that P24 supersedes "orders flow again after it".
  - [x] 7.3 `docs/agent/nautilus.md`: one entry under the IB adapter's upstream defects, naming the
    shim and its canary.
- [x] **Task 8 — Gates**
  - [x] 8.1 `ruff format`, `ruff check --no-cache` (I001 hides behind a warm cache), `make
    typecheck`.
  - [x] 8.2 `make test-unit`, `make test-component`, `uv run pytest tests/integration/core -n auto
    --forked`. Never run it beside `tests/integration/db`.
  - [x] 8.3 A mutation sweep, with copies in the scratchpad and never `git stash`:
    - drop the `params` default;
    - map `start` to `start_ns`;
    - flip the grace comparison;
    - emit `bars.catch_up` per bar;
    - pass `self._monitor` instead of the gate;
    - swallow instead of re-raise.

    Each mutation must go red.

## Dev Notes

### The current surface — exact extension points

- Shim install: `src/core/live_node_builder.py:702-707` (`InteractiveBrokersLiveExecClientFactory.create`).
  The exec client is `client`; the shared IB client is `client._client` (F12).
- Shim precedents:
  - `src/core/live_exec_avg_px.py` (instance-attribute replacement, idempotent, loud at build, "How
    this module should die" docstring);
  - `src/core/live_exec_position_reports.py` (shadows a method on the instance; canary class
    `TestTheAdapterDefect`).
- Order path: `src/core/live_order_path.py:252-300`, consumed unchanged (D-E). Runner call site:
  `src/core/live_session_runner.py:835`.
- Bar handlers subscribed in `subscribe`: runner `:681` (`steady_state.note_bar`) and `:692`
  (`order_observer.note_bar`). Traded bar types are at `:690-692`.
- Freshness: `src/core/live_bar_observer.py:443-491`, grace `src/core/live_market_data.py:57`.
- Reconnect grant: `src/core/live_runtime_reconcile.py:264-290` (`grant_after_reconciliation`), and
  `:464-479` (`_grant`).

### Scope boundaries — do NOT build

- No edit to `ConnectionMonitor`, `OrderEventObserver`, `LiveBarObserver` or `SessionSteadyState`
  beyond what Task 0 proves fits. All are at their ceilings (F13).
- No data-factory subclass (F12, D-A).
- No history backfill past the adapter's window (D-G).
- No change to `live status` health derivation. `idle` stays the indirect signal, and the stop
  (D-D) is the direct one.
- Do not touch `FROZEN_VIEWS` surfaces (`src/api/**`, `templates/**`). Nothing here needs them.

### Hazards

- **The freshness guard and the burst (F8).** If Task 1.1 finds catch-up bars stamped with arrival
  time, three of them stop the session through `live_bars.delayed_data_suspected`. That is a new
  failure this story would introduce. Re-plan before going on.
- **The RTH-open republish.** The adapter re-delivers the previous session's last bar after every
  RTH gap (`live_bar_observer.py`, "Delayed-feed detection", fact (2)). It is hours past its close,
  so the gate judges it catch-up. Withhold orders on it, which is correct, but **do not count it
  into a burst or emit `bars.catch_up` for it**: identify it as the observer does, `ts_event <=`
  the last noted `ts_event` for that bar type. Test both halves: withheld, and no record. Otherwise
  every multi-day session logs a phantom catch-up at each open.
- **Strategy state after a suppressed order.** A strategy that signalled on a catch-up bar and was
  suppressed believes nothing about the order. That is the existing withhold semantics (Story 1.6,
  `order.suppressed`). Do not change it here; name it in the P24 run if it shows.
- **ANSI in transcripts.** Strip `\x1b\[[0-9;]*m` before grepping key=value (L3).
- **Parallel Postgres schemas.** Integration `db` and forked integration must not run side by side
  (Story 5.4 memory).

### Testing standards summary

TDD, red first. The tiers:
- **Unit:** pure gate logic.
- **Component:** a real `InteractiveBrokersClient` without a socket, a real order-path wrapper, and
  a real strategy for the indicator assertion. Use the C-logging guard fixture.
- **Runner tier:** the stop path.
- **Integration:** `--forked`, unchanged pins.

Markers are required on every test. NFR26: a `RAW_ACCOUNT` absence assertion on every new record,
on every path.

### Project Structure Notes

Two new modules, `src/core/live_bar_resubscribe.py` and `src/core/live_catch_up_gate.py`, each under
every cap. Both `live_*.py` modules are found automatically by `LIVE_MODULE_GLOBS` and the
`_STDLIB_AND_FIRST_PARTY` scan. Everything else goes in the hand-maintained lists of Task 6.

### References

- `docs/qa/phase3-live-verification.md`: P23's 2026-10-12 Result-log row (findings ⓵–⓷).
- `_bmad-output/implementation-artifacts/deferred-work.md`: "Deferred from: Epic 5 live
  verification, P23 + live seal (2026-10-12)", L1–L3.
- `_bmad-output/planning-artifacts/epics.md`: Story 5.9.
- `_bmad-output/implementation-artifacts/5-8-act-on-a-persistent-runtime-reconciliation-failure.md`
  (format, the drift stop, NFR26 lessons P2/P4).
- `src/core/live_exec_avg_px.py` and `tests/component/core/test_live_exec_avg_px.py` (the shim and
  canary pattern).
- CLAUDE.md: Anti-Patterns (guard lists, membership-pinned lists, two-direction pins).

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (claude-opus-5-5), dev-story workflow, 2026-10-12.

### Debug Log References

**Task 0 — size budget, measured before editing** (`measure_module`, statement **lines**):

| Key | Before | After | Ceiling |
|---|---|---|---|
| `live_session_runner.py` file | 493 | 494 | 500 |
| `LiveSessionRunner` | 417 | 417 | 417 |
| `InteractiveBrokersLiveExecClientFactory` | 6 | 7 | 100 |
| `SessionSteadyState` | 120 | 120 (untouched) | 120 |
| `RuntimeReconciler` | 99 | 99 (untouched) | 100 |

No ceiling was raised. The guard counts every physical line a statement spans, comments
inside a multi-line call included. A first wiring that wrapped the reconciler at the call site
measured 422, then 419 with two comment lines inside the call. Hence `watched_reconciler`: a
one-name swap, with the 5.9 note folded into the existing two-line comment.

**Task 1 — measurement gate, on a real `InteractiveBrokersClient` with a spy socket:**
- The stock resubscribe re-issues no history request. F1 reproduces with no broker.
- A wrapper installed **after** the first subscribe has no effect, because the stored partial
  keeps the stock bound method. Installed **before**, the resubscribe re-issues
  `reqHistoricalData` under the same `reqId` with `durationStr='18000 S'` (300 bars). This is
  why D-A installs at build time.
- A replayed bar's `ts_init` is its period end (start + 60 s). F8 holds, so the freshness
  guard cannot trip on a burst.
- First live bar of a session, handled past its close: p23-1012 12.87 s, p11 19.12 s, p12
  25.74 s, p13 18.59 s. Steady bars are about 5.4 s. All far inside the 120 s grace (Task 1.3).

**Guard lists (Task 6), in this commit:**
- `NODE_FACING_MODULES` and `TestImportPurity.MODULES` gained both modules.
- `STOP_PATH_MODULES` non-member comment added for both, with reasons.
- `_STDLIB_AND_FIRST_PARTY` unchanged: there is no new stdlib name, and the forked
  integration/core run (110 passed) scanned both new modules through `LIVE_MODULE_GLOBS`.

**Mutation sweep (Task 8.3), 10/10 killed.** Copies were kept in the scratchpad; `git stash`
was never used.
- M1 drop the `params` default
- M2 map `start` to `start_ns`
- M3 flip the grace comparison
- M4 `>` → `>=` at the grace
- M5 emit `bars.catch_up` per bar
- M6 hand `_wrap` the monitor instead of the gate
- M7 swallow instead of re-raise
- M8 count a republish
- M9 the watch ignores failures
- M10 the watch runs after the reconciler

M7 and M10 survived the first sweep. They were killed by two added tests:
`test_a_failed_resubscribe_reaches_the_adapter_unchanged` (the adapter swallows either way, so
it calls the stored handle directly) and `TestTheWatch` (the reconciler reads only every second
tick, so the runner tier could not see the ordering).

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created.
- **AC #1, #4:** `src/core/live_bar_resubscribe.py`.
  - An instance-attribute shim on the shared IB client, installed from the exec factory's
    `create` (D-A).
  - A call without `params` (the stored handle) is delegated with `{"start_ns": None}`;
    `bars.resubscribed` is emitted per restored subscription.
  - Canaries (`TestTheAdapterDefect`) pin the handle's keywords, the signature, the identity
    compare, the swallowing resubscribe and the replayed `ts_init`.
- **AC #2:** a failed resubscribe is logged as `bars.resubscribe_failed` (bar and error type,
  never the text), noted on the client and re-raised.
  - `BarFeedWatch` (built by `watched_reconciler`) raises `BarFeedLostError` (exit `1`,
    operator-safe message) at the next tick, **before** the reconciler can re-grant.
  - The stop goes through the runner's ordinary teardown, positions untouched (PO ruling R2).
- **AC #3:** `src/core/live_catch_up_gate.py`. A bar handled more than 120 s past its close
  (R1) withholds every order-creating call raised on it.
  - When the gate is the cause, it emits `bars.catch_up_order_withheld`; `order.suppressed`
    follows unchanged.
  - One `bars.catch_up` per burst. RTH-open republishes are withheld but never counted.
  - The indicators see every bar: proven on a real `sma_crossover`, whose two SMAs count all
    six catch-up bars while no order is placed. The sibling with live bars places one.
- **D-E amended at dev (disclosed):** the gate is installed by `install_order_path` on the
  strategy's own `handle_bar`, not subscribed on `data.bars.*` by the runner. Task 3.3's
  "wire the gate in the runner" is met by that call. Two reasons:
  - It judges the exact bar each order reacts to, per strategy, with no reliance on bus order
    and no traded-bar-type filter.
  - The runner gains no line.

  Warm-up history reaches strategies through `handle_bars`, never `handle_bar`, so it is
  never judged.
- **F14, as predicted:** tests that drive a real strategy on a `LiveClock` with epoch-dated
  bars now see those bars as 56 years stale. They moved to a new `make_live_bar` helper, since
  the bars are the harness's problem, not the gate's. The two exec-factory wiring doubles
  gained a `_client`. The `traded_instruments` spy now patches `live_bar_resubscribe`.
- **AC #5:** P24 is defined (index row, procedure, "Defined, not run" Result-log row) and not
  run: operator only. P23's index row points to it. L1 is routed in `deferred-work.md`.
- **Gates:**
  - ruff format, `ruff check --no-cache`, mypy: clean.
  - unit 4011 passed; component 2053 passed, 16 skipped.
  - integration/core `--forked` 110 passed, 2 skipped; whole integration tier `--forked` 405
    passed, 2 skipped (matching PR #36).

### File List

- `src/core/live_bar_resubscribe.py` (new)
- `src/core/live_catch_up_gate.py` (new)
- `src/core/live_order_path.py`
- `src/core/live_node_builder.py`
- `src/core/live_session_runner.py`
- `tests/component/core/test_live_bar_resubscribe.py` (new)
- `tests/unit/core/test_live_catch_up_gate.py` (new)
- `tests/component/core/test_live_order_path.py`
- `tests/component/core/test_live_exec_avg_px.py`
- `tests/component/core/test_live_exec_position_reports.py`
- `tests/component/core/test_session_runner_phases.py`
- `tests/component/core/test_session_runner_runtime_reconcile.py`
- `tests/unit/core/test_live_node_never_exits.py`
- `tests/unit/core/test_live_stop_path_is_inert.py`
- `CLAUDE.md`
- `docs/agent/nautilus.md`
- `docs/qa/phase3-live-verification.md`
- `_bmad-output/planning-artifacts/epics.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/5-9-restore-the-bar-feed-after-a-reconnect.md`

## Change Log

- 2026-10-12: Story drafted, ready-for-dev. PO rulings: local shim; no trading on catch-up bars.
- 2026-10-12: Implemented (dev-story): bar-resubscribe shim and watch, catch-up gate,
  `EMITTED_BAR_FEED_EVENTS` and its pins, guard lists, P24 defined. D-E amended (gate on the
  strategy's `handle_bar`). Status → review.
