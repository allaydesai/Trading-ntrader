# Story 3.4: Never Resubmit an Order That Is Already Working

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want ambiguity about an order's fate to be resolved by asking the broker, never by sending it
again,
so that the one genuinely irreversible failure mode in this system cannot occur.

## Why this story is shaped the way it is

This story **proves and pins** the duplicate-prevention invariant; it builds almost nothing. Every
mechanism NFR6 rests on already exists in the installed wheel and is already active in our node —
by *default*, which is the problem: nothing in this repository names any of it, tests any of it, or
would go red if a future edit switched it off. The 3.2 and 3.3 scope fences both point here
("**No retry, no backoff, no query-don't-retry machinery** — AR24/Story 3.4",
3.2:358; "Story 3.4/AR24 … This story only *records* what happens; it never reacts", 3.3:495).
[Source: 3-2-submit-a-strategys-orders-to-the-broker.md:358-359;
3-3-track-every-order-through-its-full-lifecycle.md:495-496]

Four measured facts, all read from the installed 1.220.0 wheel, make the story small and sharply
bounded (full citations in Dev Notes):

1. **The execution layer already queries the venue on ambiguity, and never resubmits.**
   `LiveExecutionEngine._check_inflight_orders` (`live/execution_engine.py:645-685`) sweeps
   `cache.orders_inflight()` — `SUBMITTED | PENDING_UPDATE | PENDING_CANCEL`
   (`model/orders/base.pyx:430-435`) — and for any order older than
   `inflight_check_threshold_ms` sends a `QueryOrder`, which reaches
   `LiveExecutionClient._query_order` → `generate_order_status_report` (`live/execution_client.py:509-527`),
   i.e. a real venue query; in the IB adapter that is `reqOpenOrders`
   (`adapters/interactive_brokers/execution.py:263-298`, `client/order.py:100-137`). When the retry
   budget is spent the order is resolved **locally** — `OrderRejected(reason="UNKNOWN",
   reconciliation=True)` for `SUBMITTED`, `OrderCanceled` for `PENDING_*`
   (`live/execution_engine.py:537-571`). "Retry" in this engine means *retrying the status query*.
   `grep -n -i "submit_order\|resubmit\|retry" live/execution_engine.py` finds no `submit_order`
   call at all; the IB adapter's `_submit_order` is a single `place_order` with no retry
   (`execution.py:652-665`); a disconnect **cancels and drops** unsent submit tasks
   (`live/execution_client.py:245-269`), it never replays them.
2. **A restarted process sees its working orders before any strategy starts — by construction.**
   `NautilusKernel.__init__` calls `exec_engine.load_cache()` (`system/kernel.py:455-456`, gated by
   `ExecEngineConfig.load_cache`, default `True`), which runs `cache_orders → cache_positions →
   build_index` (`execution/engine.pyx:695-775`); `build_index` puts every order whose
   deserialised FSM state `is_open_c()` into `_index_orders_open` (`cache/cache.pyx:1186-1187`).
   `start_async` then connects, reconciles (`reconciliation=True` by default), initialises the
   portfolio, and only *then* calls `self._trader.start()` (`system/kernel.py:991-1027`). So
   `cache.orders_open(strategy_id=…)` already answers correctly inside `on_start`. Live evidence
   that this restore is real, not theoretical: the poisoned `p7-position-test` namespace
   re-loaded an `ACCEPTED` order on every `live start` for three consecutive runs
   (`deferred-work.md:2309-2325`).
3. **A resumed strategy cannot reissue a client order ID it has already used.** `Strategy._start`
   sets the generator's count to the number of this strategy's orders in the (Redis-restored)
   cache (`trading/strategy.pyx:363-368`, logging `Set ClientOrderIdGenerator client_order_id
   count to N`), and `Strategy.submit_order` denies any order whose `client_order_id` already
   exists (`trading/strategy.pyx:804-808`) — an `OrderDenied` our observer already logs as
   `order.denied`. Story 3.2 pinned the ID format and the tag stability
   (`tests/component/core/test_client_order_id_determinism.py`).
4. **Nothing in this repository retries, but nothing forbids it either.** The order path disclaims
   retry in prose only (`src/core/live_order_path.py:16-17`); `tenacity`/`backoff`/`retrying`/
   `stamina` are absent from `uv.lock`; and no scan forbids any of them. AR24 says
   "grep-enforceable"; today it is grep-*able* and enforced by nothing.

What is measured ABSENT today (the story's whole surface):

- `src/core/live_node_builder.py:411-413` builds `LiveExecEngineConfig(graceful_shutdown_on_exception=…)`
  and nothing else — `reconciliation`, `inflight_check_interval_ms`, `inflight_check_threshold_ms`
  and `inflight_check_retries` are defaults by *omission*. A tidy-up that passed
  `inflight_check_interval_ms=0` or `reconciliation=False` would delete fact 1 with no test going
  red (AC #1).
- No test constructs a `LiveExecutionEngine` and counts `submit_order` calls at the client across
  a timeout, a reconnect, or a restart. `test_live_order_path.py`'s wrapper tests count calls on the
  *strategy* double; nothing counts them at the *client* (AC #4).
- No AST scan forbids a retry decorator, a retry library, or a loop around `submit_order` (AC #2).
- No test drives `Cache(database=…)` through a restore and asserts what a strategy sees in
  `on_start` (AC #3). `test_live_cache_namespace.py` proves the *namespace* survives a restart
  (keys), never that an *order* does.

**The one flagged dependency is resolved here, at drafting, as the epic asked** (`epics.md:1310-1321`,
retro `epic-2-retro-2026-08-28.md:337-400` item 4): **option (a).** AC #3 is scoped to what the
Redis cache restores without a reconciliation pass of ours — and fact 2 shows that is *more* than
the retro assumed: the restore happens in the kernel constructor, and Nautilus's own startup
reconciliation additionally runs before `_trader.start()` with our current configuration. What
Story 4.2 owns is untouched: our `reconcile` runner phase stays a no-op placeholder
(`live_session_runner.py:530-535`), `reconcile.ok`/`reconcile.discrepancy` are not emitted,
`confirm_state_reestablished` keeps zero production callers, NFR10's "no orders while
reconciliation is incomplete" is not enforced, and **what reconciliation does with a
disagreement** — including the stale-open-order startup stall measured 2026-09-01 — remains 4.2's.
The dependency-structure claim "strictly linear" (`epics.md:480-494`) therefore stands.

**The other flagged item is not this story's.** The owner/epoch fencing column on
`trading_sessions` (the two-processes-on-one-account hazard, `epics.md:1323-1327`) was **ruled to
Story 3.6** at the Epic 2 retrospective (D1, `deferred-work.md:2083-2100`: "**Story 3.6** — the
first story owning a `SessionRecordPort` write … Do not re-open"). This story owns the
reconnect/restart/resume *order-path* half of NFR6 only; it writes no DB rows and adds no columns.

## Acceptance Criteria

1. **Given** a submission that times out or whose connection drops mid-submit
   **When** the execution layer recovers
   **Then** it queries the order's state at the venue, and only submits if the venue confirms the
   order is not working (FR27, AR24).
   *Operationalised, because the wheel already does this and the story's job is to make it a
   visible decision rather than an accident of defaults: (i) `live_node_builder.py` passes the
   in-flight-check and reconciliation fields to `LiveExecEngineConfig` **explicitly**, with the
   wheel's own default values, and a component pin reads them back off the built
   `TradingNodeConfig` (the `TestExecutionRouting` shape); a canary asserts the wheel defaults still
   equal our explicit values so an upstream change becomes a decision, not a silent drift. (ii) A
   component test drives a real `LiveExecutionEngine` with a `MockLiveExecutionClient`: an order
   left `SUBMITTED` past the threshold produces a `generate_order_status_report` call at the client
   and **exactly one** `submit_order` call, ever. Two resolutions are both pinned: the venue reports
   the order `ACCEPTED` → the cached order becomes `ACCEPTED` and stays in `orders_open`; the venue
   answers nothing for `inflight_check_retries` sweeps → the order is closed locally as
   `OrderRejected(reason="UNKNOWN", reconciliation=True)` and our observer logs it as
   `order.rejected` with `venue_reason="UNKNOWN"` and `reconciliation=True`. "Only submits if the
   venue confirms the order is not working" is satisfied in the strongest form: the execution layer
   never submits on its own initiative at all; a new order is only ever a new strategy signal.*

2. **Given** the entire order path
   **When** it is grepped
   **Then** no retry decorator, backoff wrapper, or retry loop exists around `submit_order` (AR24,
   AR43).
   *Operationalised as a unit-tier AST scan over `LIVE_MODULE_GLOBS` plus `src/core/strategies/*.py`
   (never `custom/`): no import of a retry library (`tenacity`, `backoff`, `retrying`, `stamina`),
   no decorator whose dotted name contains `retry` or `backoff`, and no `submit_order` /
   `submit_order_list` call inside a `for`/`while` body or inside an `except` handler. The scanner
   is proven non-vacuous by planting each forbidden shape in a temp module (the
   `TestTheProbeCanActuallyFail` twin). Deliberately **not** in the loop rule: `close_position`
   inside `for position in positions:` (`sma_crossover.py:206, 241`) is one close per position, not
   a retry, and the connection-attempt budget around `node.build()` (`live_check_node.py`,
   `SESSION_CONNECTION_ATTEMPTS`) is not on the order path.*

3. **Given** a session that is stopped and restarted while an order is working at the broker
   **When** it resumes
   **Then** the working order is loaded into the cache before strategies start, so the resumed
   strategy sees its own open order rather than placing a second one (FR27, AR25).
   *Scoped at drafting to option (a) — see "Why this story is shaped". Operationalised twice: a
   component test with `Cache(database=MockCacheDatabase())` — process A registers a real
   `sma_crossover` strategy, submits, applies `OrderSubmitted`+`OrderAccepted`; process B builds a
   fresh `Cache` on the **same** database, runs the kernel's real restore path
   (`ExecutionEngine.load_cache()`), registers a probe strategy with the same `strategy_id`, and
   asserts from **inside `on_start`** that `self.cache.orders_open(strategy_id=self.id)` holds the
   order and that the generator counter was restored (the next client order ID's counter is
   `N+1`, and re-submitting the restored order object is denied as a duplicate with zero client
   calls). An anti-tautology twin on a *different* database sees nothing. Then an integration test
   (`--forked`, real Redis, `pytest.skip` when unreachable — the `test_live_cache_namespace.py`
   `_require_redis` pattern) repeats the restore through the real `CacheDatabaseAdapter` and
   msgpack serializer, because the one live fill this project has had died in exactly that
   serializer (`src/core/live_exec_avg_px.py`). "Rather than placing a second one" is proven at the
   framework boundary (duplicate `client_order_id` → denied; counter restored); whether a
   *strategy* consults `orders_open` before a fresh signal is strategy logic, stays mode-agnostic
   (AR40, zero diffs under `src/core/strategies/`), and is Story 4.5's resume-mid-position
   concern.*

4. **Given** any reconnect, restart, or resume scenario exercised against broker doubles
   **When** submitted orders are counted
   **Then** the count of duplicate orders is **exactly 0** (NFR6)
   **And** this is covered by explicit tests for each of the three paths, not left to careful
   coding.
   *The count is taken at the exec-client double (`MockLiveExecutionClient.calls.count("submit_order")`
   / `commands`), never at the strategy. Three named paths, each its own test class: **timeout**
   (AC #1's in-flight sweep with no answer → local rejection, one submit); **reconnect** (a
   `SUBMITTED` order whose ack was lost across a disconnect → on the first sweep after
   reconnection the venue reports `ACCEPTED` → one submit; **and** our wrapper's suppressed call
   is never replayed — `submission_withheld` flips True→False and the base method's call count
   stays 0, so a queue-and-replay mutation goes red); **restart/resume** (AC #3's restore → the
   same order is not resubmitted, and a re-submission attempt is denied, one submit across both
   "processes"). Every test asserts the count as an exact integer, and the clause matrix (Task 9)
   mutates each path in isolation.*

## Tasks / Subtasks

- [x] Task 1: Measure the installed wheel before writing production code (AC: all)
  - [x] 1.1 Fresh-interpreter probe (the 3.2/3.3 pattern — `subprocess.run`, never `Popen`+`PIPE`;
    the `timeout` argument is itself the assertion): construct `LiveExecutionEngine(loop, msgbus,
    cache, clock, config=LiveExecEngineConfig())` with a real `MessageBus`, `Cache(database=None)`,
    `LiveClock`, and `MockLiveExecutionClient` (`test_kit/mocks/exec_clients.py:146`), register
    the client, and confirm `is_logging_initialized()` is unchanged before/after — the component
    file must stay under the same autouse C-logging guard `test_live_order_path.py:91-100` uses.
    If the live engine touches C logging, Tasks 3/6 move to the integration tier (`--forked`);
    do not improvise a third design.
  - [x] 1.2 Measure the in-flight sweep's query shape: `_check_inflight_orders`
    (`live/execution_engine.py:645-685`) builds `QueryOrder(... venue_order_id=order.venue_order_id)`
    and a `SUBMITTED` order's `venue_order_id` is `None` (`OrderSubmitted.venue_order_id` is
    hardcoded `None`, `model/events/order.pyx:1534-1543`). `MockLiveExecutionClient
    .generate_order_status_report` keys its reports by `venue_order_id`
    (`exec_clients.py:285-293`) — so the stock mock answers `None` for exactly the order under
    test. Record this. Task 3's "venue says ACCEPTED" case therefore subclasses the mock to look up
    by `client_order_id`, mirroring what the IB adapter actually does (`orderRef ==
    client_order_id.value`, `adapters/interactive_brokers/execution.py:274-284`). Measure the
    threshold/retry plumbing: `self.inflight_check_max_retries` (`:171`), the retry `Counter`
    (`:131`), the "delayed" test `ts_now > order.last_event.ts_event + threshold_ns` (`:655`),
    and the resolution branch (`:537-571`).
  - [x] 1.3 Measure the restore round trip with `MockCacheDatabase` (`test_kit/mocks/cache_database.py:37`):
    `Cache(database=db).add_order(order)` → `db.add_order`; `cache.update_order(order)` after an
    `OrderAccepted` → `db.update_order`; fresh `Cache(database=db)` + a fresh
    `ExecutionEngine`'s `load_cache()` (`execution/engine.pyx:695-775`) → `cache.orders_open()`
    contains the order (`build_index` at `cache/cache.pyx:1186-1187`). Confirm the mock persists the
    *object* (so an `ACCEPTED` order reloads as `ACCEPTED`) and confirm `Strategy._start` logs
    `Set ClientOrderIdGenerator client_order_id count to 1` on the restored cache
    (`trading/strategy.pyx:363-368`).
  - [x] 1.4 Record the wheel defaults the builder will name: `reconciliation=True`,
    `inflight_check_interval_ms=2_000`, `inflight_check_threshold_ms=5_000`,
    `inflight_check_retries=5`, `open_check_interval_secs=None`, `load_cache=True`
    (`live/config.py:166-187`; `execution/config.py:65-71`). Record every measurement in the Dev
    Agent Record with the probe source.
- [x] Task 2: RED first — the AR24 scan, unit tier (AC: #2)
  - [x] 2.1 New `tests/unit/core/test_order_path_has_no_retry.py`, `@pytest.mark.unit`. Module
    set: `LIVE_MODULE_GLOBS` **imported** from
    `tests/component/core/test_live_dependency_invariance.py:39` (`tests/__init__.py` exists — a
    test may import a test; do not duplicate the globs) plus `src/core/strategies/*.py` excluding
    `__init__.py` and `custom/` (the `STRATEGY_MODULES` shape,
    `test_live_stop_path_is_inert.py:109-115`). Non-vacuity: assert the set has ≥ 8 modules and
    contains `live_order_path.py` and `sma_crossover.py`.
  - [x] 2.2 Three rules, each its own test, each with RED output captured **at the moment it is
    written** (the 3.3 review's unobtainable-evidence lesson, 3.3:399-400 — post-hoc mutation is
    not test-first evidence): (a) no `Import`/`ImportFrom` whose root is in
    `FORBIDDEN_RETRY_LIBRARIES = frozenset({"tenacity", "backoff", "retrying", "stamina"})`;
    (b) no decorator (`FunctionDef`/`AsyncFunctionDef`/`ClassDef.decorator_list`) whose dotted
    source text contains `retry` or `backoff`, case-insensitive; (c) no `ast.Call` whose callee's
    attribute/name is `submit_order` or `submit_order_list` with an ancestor `For`/`While`
    (walking the ancestor chain, not merely the same function) or sitting inside an
    `ExceptHandler` body. Pin `FORBIDDEN_RETRY_LIBRARIES` and the two-name submit set as exact
    sets (CLAUDE.md membership-pinned-lists discipline).
  - [x] 2.3 The scanner must actually fail: plant each of the three shapes in a `tmp_path` module
    and run the same scanner over it — three red cases, one per rule (the
    `TestTheProbeCanActuallyFail` / `test_the_check_would_catch_an_undeclared_import` twin,
    `test_live_dependency_invariance.py:198`). A loop rule that also flags `close_position` inside
    `for position in positions` is wrong — add the sma_crossover shape as a *passing* case.
- [x] Task 3: RED — the in-flight canaries, component tier (AC: #1, #4 timeout + reconnect)
  - [x] 3.1 New `tests/component/core/test_live_order_recovery.py`, `pytestmark = pytest.mark.component`,
    with the autouse C-logging guard copied from `test_live_order_path.py:91-100` (never build a
    `TradingNode` here). Harness: `asyncio` loop, `LiveClock` **or** `TestClock` if the live engine
    accepts it (measure — the "delayed" test reads `self._clock.timestamp_ns()`), real
    `MessageBus`, `Cache(database=None)` with `AAPL_EQUITY` added, `Portfolio`, `LiveRiskEngine`
    if the engine's command path requires one (the `test_client_order_id_determinism.py:60-83`
    harness is the starting point — extend it, do not fork it), a real `sma_crossover` via
    `materialise_strategy` (the `_new_strategy()` shape, `test_live_order_path.py:149-163`),
    `MockLiveExecutionClient` registered with `routing` default so `AAPL.NASDAQ` routes.
  - [x] 3.2 `TestTheTimeoutPathQueriesAndNeverResubmits`: strategy submits one market order →
    `client.calls.count("submit_order") == 1`; deliver **only** `OrderSubmitted` (the ack is
    "lost"); advance the clock past `inflight_check_threshold_ms`; `await
    engine._check_inflight_orders()` once → `"generate_order_status_report" in client.calls`, the
    order is still `SUBMITTED`, and `submit_order` count is still 1. Sweep `inflight_check_retries`
    more times → the order is `REJECTED`, its last event is an `OrderRejected` with
    `reason == "UNKNOWN"` and `reconciliation is True`, and — routed through our
    `OrderEventObserver.handle_order_event` on `events.order*` — exactly one `order.rejected`
    record with `venue_reason == "UNKNOWN"` and `reconciliation is True`. **Submit count: 1, as
    an exact integer.** Assert `"submit_order" not in client.calls[after_first:]`, not merely a
    total, so a resubmit-then-cancel mutation cannot hide.
  - [x] 3.3 `TestTheReconnectPathQueriesAndNeverResubmits`: same start; the venue-answering
    subclass (Task 1.2) returns an `OrderStatusReport(order_status=ACCEPTED, …)` for the order's
    `client_order_id`; one sweep → the cached order is `ACCEPTED`, is in `cache.orders_open()`,
    the retry counter for it is cleared (`live/execution_engine.py:1147-1148`), and the submit
    count is 1. Then a second strategy signal (drive bars through `strategy.handle_bar` as
    `test_live_order_path.py` does) may submit a **new** order with a **different**
    `client_order_id` — that is not a duplicate; assert the two IDs differ and neither equals the
    other's.
  - [x] 3.4 `TestASuppressedCallIsNeverReplayed`: `install_order_path` with a `_StubMonitor`
    (`test_live_order_path.py:103-107`) reading `withheld=True`; call the wrapped `submit_order`;
    flip the stub to `withheld=False`; drive nothing else; assert the base method's call count is
    still 0 and one `order.suppressed` record exists — the wrapper has no queue, no timer, no
    replay. Pin structurally too: `install_order_path`'s wrapper closure has no attribute that is
    a `list`/`deque`/`Queue` (a cheap AST or `inspect` check on `_wrap`), so a "remember and resend
    on reconnect" mutation is caught by name.
- [x] Task 4: RED — the restart/resume path (AC: #3, #4 restart)
  - [x] 4.1 In the same component file, `TestARestartedProcessSeesItsWorkingOrderBeforeOnStart`:
    "process A" = harness with `Cache(database=MockCacheDatabase())`, real `sma_crossover`
    registered under `TraderId("PAPER-0e8f1c2a")` (the determinism test's ID) and an explicit
    `order_id_tag="000"`, submits once, apply `OrderSubmitted` then `OrderAccepted`
    (`TestEventStubs.order_accepted`, `venue_order_id` set) through `exec_engine.process(...)` so
    `cache.update_order` writes through. "Process B" = a **new** `MessageBus`/`Cache(database=same_db)`
    /engines/`Trader`; call the engine's `load_cache()` (the kernel path, not `cache.cache_orders()`
    by hand); register a `_ProbeStrategy(SMACrossover)` whose `on_start` records
    `tuple(self.cache.orders_open(strategy_id=self.id))` and `self.order_factory` state into
    instance attributes and then calls `super().on_start()`; `trader.add_strategy`; `strategy.start()`.
    Assert from the recorded attributes: the restored order is present with `status ==
    ACCEPTED`, the next generated `client_order_id` ends in `-000-2` (counter restored to 1, then
    incremented), and `strategy.submit_order(restored_order)` produces an `OrderDenied` whose
    reason contains `duplicate` with **zero** `submit_order` calls at process B's client. The
    probe subclass lives in the test file only — `src/core/strategies/` has zero diffs.
  - [x] 4.2 Anti-tautology twin: process B on a **fresh** `MockCacheDatabase()` → `orders_open` is
    empty inside `on_start` and the next ID ends in `-000-1`. This is the same shape as
    `test_turning_on_use_instance_id_breaks_the_rejoin` (`test_live_cache_namespace.py:185-240`):
    the guarantee is load-bearing only if its absence is observable.
  - [x] 4.3 New `tests/integration/core/test_live_order_survives_restart.py`,
    `pytestmark = pytest.mark.integration`, module-scoped autouse `_require_redis` copied from
    `test_live_cache_namespace.py:44-57` (`check_redis_reachable`, `pytest.skip`), **never**
    `flush()` (the FLUSHDB warning at `:11-14`), a fresh `TraderId` per test so namespaces never
    collide. Same two "processes" as 4.1 but with the real `CacheDatabaseAdapter` +
    `MsgSpecSerializer(msgpack)` (`:60-72`); writes land on `close()` (`:120-127`) — close A's
    adapter before opening B's. Assert the reloaded order's `status`, `client_order_id`,
    `venue_order_id` and `quantity` are byte-equal to A's, and that it is in `orders_open()`
    after `load_cache()`. One extra assertion the mock cannot make: the reloaded order is a
    *different Python object* (`is not`) with equal state — the serializer round trip actually
    happened.
- [x] Task 5: GREEN — make the in-flight/reconciliation configuration explicit (AC: #1)
  - [x] 5.1 `src/core/live_node_builder.py:411-413`: pass `reconciliation=True`,
    `inflight_check_interval_ms=…`, `inflight_check_threshold_ms=…`, `inflight_check_retries=…`
    from four new module constants beside `NODE_TIMEOUT_*` (`:89-103`), values equal to the wheel
    defaults measured in Task 1.4. Leave `open_check_interval_secs` unset and say why in the
    constants' comment: continuous open-order checking is Story 4.3's, and with this adapter it
    is not free — `generate_order_status_reports` ignores `open_only`, calls `get_positions` and
    fabricates a `FILLED` report per position on every tick (`execution.py:374-445`), which is a
    4.3 design decision, not a default to flip here. Docstring block, in the `:79-87` "today's
    Nautilus defaults, deliberately" register: what the sweep does, what it resolves to, and that
    it **never resubmits** (fact 1) — so the next reader does not "add a retry" to fix a
    `venue_reason="UNKNOWN"`.
  - [x] 5.2 Pin in `tests/component/core/test_live_node_builder.py` (the `TestExecutionRouting`
    class drives the real builder — add `TestInFlightCheckIsExplicit` beside it): read
    `config.exec_engine.reconciliation`, `.inflight_check_interval_ms`,
    `.inflight_check_threshold_ms`, `.inflight_check_retries` off the built `TradingNodeConfig`
    and assert the four values; assert `open_check_interval_secs is None`. Anti-tautology twin:
    the *stock* `LiveExecEngineConfig()` equals the same four values (the wheel-default canary) —
    if an upgrade changes a default, this twin goes red and the constants become a decision.
  - [x] 5.3 Size budget, decided BEFORE the edit (CLAUDE.md D4): `live_node_builder.py` is 622
    raw lines today (measure executable statements before editing and record both); budgeted
    ~+30-40 raw (four constants, four kwargs, one docstring block) / ~+8 statements. It is on
    `NODE_FACING_MODULES` and `TestImportPurity` is not its concern; do NOT split it
    (Anti-Patterns: guard-list rot). Record the delta in the Dev Agent Record.
- [x] Task 6: GREEN for Tasks 3/4/6 — expected to be **no production change** (AC: #1, #3, #4)
  - [x] 6.1 The canaries in Tasks 3 and 4 are RED only if the wheel or our wiring contradicts
    the measured facts. If any goes GREEN on first run, that is the expected outcome for a
    pin — record it as such ("GREEN on first run: pins measured behaviour") and prove
    non-vacuity by the mutation in Task 9, not by contriving a RED. If one is genuinely RED, stop
    and record what the wheel actually does before touching production code — the design in
    this file is built on the measurements in Dev Notes, and a contradiction is a finding, not a
    bug to code around.
  - [x] 6.2 If Task 1.1 routed the live-engine tests to the integration tier, they go in
    `tests/integration/core/test_live_order_recovery.py` with the same class names, and the
    component file keeps Tasks 3.4 and 4.1/4.2 (no live engine needed there).
- [x] Task 7: Non-change evidence contracts — verified, not assumed (AC: #2, #3)
  - [x] 7.1 `git diff --stat` empty for: `src/core/live_order_path.py`,
    `src/core/live_session_runner.py`, `src/core/live_connection_monitor.py`,
    `src/core/live_strategy_guard.py`, `src/core/strategies/**`, `src/db/**`, `alembic/**`,
    `src/services/**`, `src/models/**`, `src/cli/**`, and the guard-list *contents* in
    `test_live_node_never_exits.py`, `test_live_stop_path_is_inert.py`,
    `test_session_runner_phases.py::TestImportPurity`, `test_epic1_ac_node.py::_STDLIB_AND_FIRST_PARTY`.
    Record each in the Dev Agent Record. The only production diff this story expects is Task 5's.
  - [x] 7.2 Guard-list check, explicit: no new module under `src/core/live_*.py` → no
    `NODE_FACING_MODULES`/`STOP_PATH_MODULES`/`TestImportPurity.MODULES` entry. No new stdlib
    import on any live-path module (Task 5 adds constants, not imports) → `_STDLIB_AND_FIRST_PARTY`
    untouched. State "zero guard-list edits" only after `git diff --stat` on those four files is
    actually empty — 3.3 claimed it and was wrong (3.3:394).
- [x] Task 8: Live observation on the next RTH transcript — read, do not stage (AC: #3, #4)
  - [x] 8.1 Preconditions are 3.2 Task 8.1's, unchanged: inside RTH; **no other IBKR login**
    (mobile app and client portal included — error 162); the bare non-compose Gateway
    (`READ_ONLY_API: "yes"` on the compose one); Redis up; strategy `sma_crossover` only. **Do not
    use `p7-position-test`** — its namespace holds a stranded `ACCEPTED` order and startup stalls
    (`deferred-work.md:2309-2325`). Use `p7-fill-0901` (its namespace holds a `FILLED` order and,
    unless the operator has closed it, a real `LONG 22 NVDA` position) or a fresh session that
    has traded once.
  - [x] 8.2 ✅ **run 2026-09-11, all four sub-clauses pass** — against a fresh session
    (`p10-fresh-0911`, not `p7-fill-0901`; see the new deferred-work.md finding on why). (i) the
    restore — `Cached 4 orders from database` and `Set ClientOrderIdGenerator client_order_id
    count to 1` both appear before `session.started` on the restart leg; (ii) the counter — the
    two post-restart `order.submitted` records carry `client_order_id` suffixes `-000-2` and
    `-000-3`, both `> 1`, and no `client_order_id` repeats anywhere in the transcript; (iii) both
    NFR1 fields appear on every `order.submitted` record, pre- and post-restart (e.g.
    `bar_close_to_submit_ms=5412.626 bar_arrival_to_submit_ms=1.999`), discharging the 2026-09-10
    ruling's live re-measurement; (iv) `grep -c` for `162`/`10182`/`366` came back non-zero on
    first pass but every hit was a false positive (digits inside a nanosecond timestamp, or a
    benign `Historical Market Data Service ... query cancelled` at teardown) — no
    competing-login evidence. Full detail in Procedure P10's result log. **A live, previously
    undocumented defect was found during this run** — see deferred-work.md's new "story-3.4"
    addendum — and is unrelated to this story's own AC #1-#4, which all held.
  - [x] 8.3 What this run is **not** evidence for, stated in the result row: no live disconnect
    mid-submit and no live working-order-across-restart is staged — the built-in strategy submits
    market orders that fill in milliseconds inside RTH, so a working order across a restart is not
    producible on demand with it, and a Gateway kill mid-submit is P4's territory, not a thing to
    do on purpose against an order. The in-flight and reconnect paths are broker-double evidence
    (NFR32) by design.
  - [x] 8.4 Record as **Procedure P10** in `docs/qa/phase3-live-verification.md` (there is no
    P10 today; P1–P9 end at `:1070`), following the house shape exactly: intro → `### Preconditions`
    → `### What it does — and does not — do` → `### Command` → `### Expected output` (with the
    `| Output | Meaning | Exit |` table) → `### Pass criteria` → `### Result log`
    (`| Date | Operator | Result | Notes |`, newest first, `Allay (Claude Code session)`). If the
    market is closed when the automated tiers land, the story goes to `review` with 8.2 marked
    `⏳ not yet run` (the standing Epic 2 retro rule; 3.2's Task 8.4 / 3.3's Task 6.3 precedent) —
    not `done`.
- [x] Task 9: Mutation sweep, clause matrix, gates, closeout (AC: all)
  - [x] 9.1 Mutations selected **from the AC list, not from what feels fragile**, minimum one per
    AC, scripted break→observe-red→revert naming the killing test each time: (M1/AC1) set
    `inflight_check_interval_ms=0` in the builder → `TestInFlightCheckIsExplicit` red; (M2/AC1)
    set `reconciliation=False` → same; (M3/AC1) monkeypatch the engine's `_resolve_inflight_order`
    to call `client.submit_order` → 3.2's exact-count assertion red; (M4/AC2) plant `@retry` on
    `_wrap` in a temp copy of `live_order_path.py` fed to the scanner → rule (b) red; (M5/AC2)
    wrap `base(*args, **kwargs)` in `for _ in range(3): try: … except Exception: continue` → rules
    (c) red; (M6/AC3) make process B use a fresh database → 4.1 red (and 4.2's twin green — the
    pair is the proof); (M7/AC3) skip `load_cache()` in process B → 4.1 red; (M8/AC4) give the
    wrapper a `deque` and replay it when `withheld` flips False → 3.4 red by count and by the
    structural pin; (M9/AC4) in 3.3, return a `CANCELED` report → assert the order leaves
    `orders_open` and the count is still 1 (a resubmit-after-cancel mutation red). When a
    mutation stays green, inspect the FIXTURE before concluding the guard is missing (2.8's
    mutation #7; 3.3's `trade_id` fixture lesson, 3.3:860-867).
  - [x] 9.2 Clause matrix (3.1's method): decompose each AC into its distinct obligations (AC #1
    has four: explicit config, query happens, ACCEPTED resolution, UNKNOWN resolution + observer
    record; AC #4 has three paths × two clauses), mutate each in isolation, run the plausible guard
    surface (`test_live_order_recovery.py`, `test_order_path_has_no_retry.py`,
    `test_live_node_builder.py`, `test_live_order_path.py`, the integration restart file), record
    which named test fires. Write it **as the tests are written**, not at closeout — it is the
    artifact whose absence let two 3.3 findings through (3.3:391).
  - [x] 9.3 Full gates: `make format && make lint && make typecheck`; unit, component, integration
    `--forked`, e2e; Epic 1 acceptance sweep reported in **both units** (acceptance criteria and
    test functions — 3.3:887-889). Baselines at creation: unit 2421 · component 1441/16 skipped ·
    integration 281/2 skipped · e2e 1. Record every count and delta.
  - [x] 9.4 Update `deferred-work.md` (new section "Deferred from: story-3.4") with, at minimum,
    the IB not-found conflation hazard (Dev Notes, Hazards #1) routed to **Story 4.3**, and
    `sprint-status.yaml`. Commit shape per Project Structure Notes.

## Dev Notes

### The trap — read before designing anything

**"Recover" does not mean "resend", and the framework already agrees.** Every previous story
recorded a hazard that bit it; this one's is a *temptation*: seeing `OrderRejected(reason="UNKNOWN",
reconciliation=True)` in a transcript and reaching for a retry. That rejection is the engine
telling you it asked the venue `inflight_check_retries` times and got no answer
(`live/execution_engine.py:537-555`); the order may still be working at IB. Resending it is the one
irreversible failure mode (PRD `prd.md:441-444`). The correct response to `UNKNOWN` is a *venue
query* — which the engine has already done — and then Epic 4's reconciliation. Nothing in this
story, and nothing in this phase, may add a code path that calls `submit_order` on the execution
layer's own initiative. AR43 makes it an auto-reject in review.

**Do not "fix" the 2026-09-01 stall here.** A stale `ACCEPTED` order in Redis whose broker-side
truth is `FILLED` stalls startup at portfolio initialisation (`deferred-work.md:2309-2325`). It is
the literal AC #3 scenario going wrong in the field, and it is **Story 4.2's**: the fix is what
reconciliation *does* with a disagreement (broker wins, cache overwritten — FR35), and the IB
adapter cannot supply fills (`generate_fill_reports` → `[]`, `execution.py:447-452`). This story
proves the restore and the visibility; it does not touch what happens next.

### The current surface — extension points, exact

- `src/core/live_node_builder.py:411-413` — the only production edit:
  `exec_engine=LiveExecEngineConfig(graceful_shutdown_on_exception=ENGINE_GRACEFUL_SHUTDOWN_ON_EXCEPTION)`.
  Constants live at `:89-103` with the "today's Nautilus defaults, deliberately" register at
  `:79-87`; the `TradingNodeConfig` assembly is `:393-425`. The exec client is built `:337-383`
  (`routing=RoutingConfig(default=True)` `:382`; instrument provider `:359-361`). The factory
  class name is load-bearing (`:583-622`) — do not touch it.
- `src/core/live_order_path.py` — **not modified**. `install_order_path` `:239`, `_wrap`
  `:265-287` (the closure Task 3.4 pins structurally), `ORDER_CREATING_METHODS` `:125-127`,
  `OrderEventObserver` `:346`, `handle_order_event` `:483`, `_log_rejected` `:597-626` (emits
  `reconciliation=True` only when the flag is True — Task 3.2's assertion relies on it),
  `ORDER_EVENTS_TOPIC = "events.order*"` `:175`. Its module docstring already disclaims retry at
  `:16-17`.
- `src/core/live_session_runner.py` — **not modified**. Startup order `:326-350`;
  `_phase_node_connect` launches `node.run_async()` `:507`; `_phase_reconcile` no-op `:530-535`;
  `_phase_subscribe` `:542-564` (observer subscribed `:563`); `install_order_path` call `:657`
  before `add_strategy` `:659`.
- `src/core/live_cache.py:110-151` — `use_trader_prefix=True`, `use_instance_id=False`,
  `flush_on_start=False`, `encoding="msgpack"`; the namespace is keyed by
  `TradingNodeConfig.trader_id` (`:112-116`), derived at `live_trader_id.py:58-87`.
- Tests to extend / imitate: `tests/component/core/test_live_node_builder.py`
  (`TestExecutionRouting` drives the real builder); `tests/component/core/test_client_order_id_determinism.py:60-83`
  (real `Trader` + engines harness, `Environment.BACKTEST`, measured not to touch C logging);
  `tests/component/core/test_live_order_path.py:91-107, 149-163` (C-logging guard, `_StubMonitor`,
  `_new_strategy`); `tests/integration/core/test_live_cache_namespace.py:44-72, 185-240`
  (`_require_redis`, adapter construction, the load-bearing inversion pattern).

### Measured Nautilus 1.220.0 facts — cite, don't re-derive

**In-flight check (AC #1).**
- `LiveExecEngineConfig` (`live/config.py:76`, defaults `:166-187`): `reconciliation=True`,
  `inflight_check_interval_ms=2_000`, `inflight_check_threshold_ms=5_000`,
  `inflight_check_retries=5`, `open_check_interval_secs=None`, `open_check_open_only=True`,
  `generate_missing_orders=True`, `filter_position_reports=False`. Class docstring `:80-82`: the
  in-flight check exists because "events emitted from the venue may have been lost at some point
  — leaving an order in an intermediate state, the check can recover these events via status
  reports." Inherited `load_cache=True` (`execution/config.py:65-71`).
- The continuous task is created in `_on_start` (`live/execution_engine.py:414-422`) when
  `inflight_check_interval_ms > 0 or open_check_interval_secs`; loop `:583-643`; it starts at
  `exec_engine.start()` — **before** clients connect and before startup reconciliation
  (`system/kernel.py:1244-1250` → `execution/engine.pyx:640-656`).
- `_check_inflight_orders` `:645-685`: `cache.orders_inflight()` `:652`; delayed when
  `ts_now > order.last_event.ts_event + threshold_ns` `:655`; `QueryOrder` → `_execute_command`
  `:674-685`; retries exhausted → `_resolve_inflight_order` `:669-672`.
- `_resolve_inflight_order` `:537-571`: `SUBMITTED` → `OrderRejected(reason="UNKNOWN",
  reconciliation=True)` `:540-555`; `PENDING_UPDATE`/`PENDING_CANCEL` → `OrderCanceled(reconciliation=True)`
  `:556-568`; anything else raises `RuntimeError` `:570`.
- Query chain: `execution/engine.pyx:1087-1088` → `live/execution_client.py:330-335` →
  `_query_order` `:509-527` → `generate_order_status_report` → `_send_order_status_report`
  `:527` → `reconcile_execution_report` `:994-1037` → `_reconcile_order_report` `:1128-1266`. A
  `None` report is a warning only (`:523-525`); any report clears the retry counter
  (`:1147-1148`). Report `ACCEPTED` on a cached `SUBMITTED` order → `OrderAccepted` generated
  (`:1187`); report `ACCEPTED` on an already-`ACCEPTED` order → nothing, `True` (`:1180-1184`).
- **Nothing resubmits**: no `submit_order` call in `live/execution_engine.py`; the base engine's
  single forward path is `client.submit_order(command)` at `execution/engine.pyx:1027`;
  `LiveExecutionClient.submit_order` is a one-shot `create_task` (`live/execution_client.py:271-276`);
  `disconnect` → `cancel_pending_tasks` (`:245-269`) cancels unsent tasks; no `resubmit`/`replay`/
  `pending_commands` anywhere in `adapters/interactive_brokers/`. The IB reconnect path re-issues
  **subscriptions** only (`client/client.py:325-343`).
- **The IB adapter's status query is `reqOpenOrders`** (`client/order.py:100-137`, 30 s wait),
  matched on `orderRef == client_order_id.value` (`execution.py:274-284`). **Not found → the
  adapter generates a local `OrderCanceled` itself** (`"Order … not found, canceling"`,
  `execution.py:286-296`) and returns `None`. `generate_fill_reports` is `return []` with a
  warning (`:447-452`). A submit on a dead socket: `ibapi` `placeOrder` errors with code 504
  (`ibapi/client.py:1192-1194`), which the adapter logs as "Unhandled order warning or error
  code" and does **not** turn into a rejection (`client/error.py:200-236`; 504 ∉
  `ORDER_REJECTION_CODES` `:21`) — the order stays `SUBMITTED` until the in-flight sweep resolves
  it. This is the reconnect path AC #4 names, and it ends in a query, never a resend.

**Restart/resume (AC #3).**
- `NautilusKernel.__init__`: `Cache(database=cache_db)` `:348-351`; `exec_engine.load_cache()`
  `:455-456`. `ExecutionEngine.load_cache` `execution/engine.pyx:695-775`: `cache_orders` `:736`,
  `cache_positions` `:748`, `build_index` `:757`, `check_integrity` `:763`. `Cache.cache_orders`
  `cache/cache.pyx:373-396` logs `Cached N order(s) from database`; `_build_indexes_from_orders`
  `:1133-1209`, open index `:1186-1187`, inflight index `:1201-1202`. `orders_open` `:4217-4245`.
- `start_async` `system/kernel.py:991-1027`: engines start → clients connect → `reconcile_execution_state`
  (if `reconciliation`) → `_initialize_portfolio` → `_trader.start()` `:1027` → `Strategy._start`
  `trading/strategy.pyx:348-395` → `set_client_order_id_count(len(cache.client_order_ids(strategy_id=self.id)))`
  `:363-367`, log line `:368`, then `on_start()` `:395`. `flush_on_start` wipes **after**
  `load_cache` already ran (`kernel.py:1245-1246`) — ours is `False`.
- FSM: `is_open_c` = `ACCEPTED | TRIGGERED | PENDING_CANCEL | PENDING_UPDATE | PARTIALLY_FILLED`
  (`base.pyx:407-416`); `is_inflight_c` = `SUBMITTED | PENDING_*` (`:430-435`). A `SUBMITTED`
  order restored from cache is therefore *inflight*, not *open*, and the first sweep after
  restart queries it — the timeout path and the restart path compose.
- `Strategy.submit_order` `trading/strategy.pyx:804-808`: `if self.cache.order_exists(order.client_order_id):
  self._deny_order(order, f"duplicate {repr(order.client_order_id)}"); return`. The **engine**
  does not reject duplicates (`execution/engine.pyx:996-1027` skips caching and forwards) — the
  strategy boundary is the guard, which is why Task 4.1 re-submits via `strategy.submit_order`
  and not via a command.
- Client order ID: `O-{yyyymmdd}-{hhmmss}-{trader_tag}-{strategy_tag}-{count}`
  (`common/generators.pyx:145-151`); pinned by `test_client_order_id_determinism.py:40-43`.
  Startup reconciliation-generated orders carry `StrategyId("EXTERNAL")` or `INTERNAL-DIFF`
  (`live/execution_engine.py:1709-1721, 1715-1717`) and so are **not** counted into a real
  strategy's counter.

**Test doubles (both).**
- `MockLiveExecutionClient` `test_kit/mocks/exec_clients.py:146`: `calls: list[str]` `:203`,
  `commands`, `submit_order` `:241`, `query_order` `:277`, `add_order_status_report(report)`
  `:67` keyed by `venue_order_id` `:54,68`, `generate_order_status_report` `:285-293` looks up
  `command.venue_order_id` — subclass for the client-order-id lookup (Task 1.2).
- `MockCacheDatabase` `test_kit/mocks/cache_database.py:37`: `load_orders` `:97`, `add_order`
  `:145`, `update_order` `:164`, `load_index_order_client` `:121` — an in-memory
  `CacheDatabaseFacade`, constructor takes no arguments.

### Scope boundaries — what this story must NOT build

- **No retry, no backoff, no resend of any kind** — the story exists to forbid it (AR24, AR43).
  Not on the order path, not in a diagnostic script, not "just for the canary".
- **No `reconcile` phase work, no `reconcile.*` records, no grant path, no NFR10 gating** —
  Story 4.2. `confirm_state_reestablished` keeps zero production callers; our `reconcile` phase
  stays `pass`.
- **No `open_check_interval_secs`** — Story 4.3 (continuous reconciliation), and see Task 5.1 for
  why it is not free with this adapter.
- **No strategy edit** — AR40's zero-diff contract (3.2 AC #1, 3.3 AC #4). The probe subclass
  is test-only. Strategy-side awareness of a working order on a fresh signal is Story 4.5's.
- **No fencing column, no DB write, no migration** — D1 is ruled to Story 3.6 and closed as a
  question (`deferred-work.md:2097-2100`).
- **No accumulator persistence** for `OrderEventObserver` (3.3:502-505) — `cum_qty`/`trade_ids`
  still reset across a restart; disclosed there, still not this story's.
- **No new exception names** — D3's `exit_outcome` marker is decided but unbuilt; adding a sixth
  hand-maintained string worsens the collision it fixes (3.2:369-370).
- **No `OrderEventObserver` change.** Task 3.2 routes engine events through the existing
  observer to assert the `order.rejected` record; it needs nothing new. The deferred one-liner
  (`order.submitted` lacks `strategy_id`, "whichever story next touches `_log_submitted`",
  `deferred-work.md:2231-2241`) is **not** claimed here because this story does not touch that
  builder; leave it for a story that does.

### Hazards (every one bit a previous story, or will)

1. **The IB adapter conflates "not open" with "cancelled."** `generate_order_status_report` asks
   `reqOpenOrders`; an order that *filled* while the ack was lost is not open either, and the
   adapter answers by generating a local `OrderCanceled` (`execution.py:286-296`). The cache then
   believes flat while the account holds a position, and the next signal opens exposure the
   strategy did not intend. Not a duplicate *submission* — nothing resends — but a duplicate in
   effect, and our `order.canceled` record cannot distinguish it (no reason field on
   `OrderCanceled`; the adapter's own warning line is the only trace). Owner: **Story 4.3**
   (runtime alignment via position reports; `generate_position_status_reports` *is* implemented,
   `:454-505`). Record in `deferred-work.md` at closeout with this citation; do not solve here.
2. **The stock mock answers `None` to the very query under test** (Task 1.2). A test that
   asserts "the venue was asked" against the stock mock passes for the wrong reason on the
   ACCEPTED path; the subclass must key by `client_order_id`.
3. **IB status reports carry `filled_qty=0, avg_px=0` regardless** (`execution.py:347-348`), so a
   `PARTIALLY_FILLED` report against an order with cached fills hits the
   `report.filled_qty < order.filled_qty` error branch (`live/execution_engine.py:1235-1241`).
   Do not model a partial fill in the in-flight tests; that is 3.5's aggregation plus 4.2's
   reconciliation, and the mock would pass a case the real adapter fails.
4. **`reconcile_execution_state`'s `timeout_secs` is not enforced** in this version — no
   `wait_for` anywhere in `:850-992`; a client returning `None` from `generate_mass_status` is a
   warning and `continue` (`:916-921`). `NODE_TIMEOUT_RECONCILIATION` is therefore not what
   bounded the 2026-09-01 stall (that was `timeout_portfolio`, fail-quiet at `kernel.py:1024`).
   Do not cite the reconciliation timeout as protection.
5. **`config.load_state` defaults `False` while its docstring says `True`** (`system/config.py:77`
   vs `:122`). Irrelevant to the order cache (that is `load_cache`), but a reader will confuse
   them — cite `load_cache`, never `load_state`.
6. **Real `Trader` harness fixture traps** (3.2:374-399, 3.3:513-548): `trader.strategies()` is a
   method; two `TestExecStubs.market_order(...)` yield the same frozen `client_order_id` — pass
   explicit IDs; `TestEventStubs.order_filled` derives `trade_id` from the order and hardcodes
   nothing distinctive; give every fixture **distinctive** values so a mutation cannot survive on
   indistinct ones; `capture_logs()` only against a locally bound logger (`test_live_order_path.py:186`
   shape), never near the runner's contextvars; the C-logging autouse guard is order-dependent —
   never construct a `TradingNode` or `BacktestEngine` in the component file.
7. **`ExecutionEngine._handle_submit_order` forwards a duplicate** (`execution/engine.pyx:996-1027`).
   A test that pushes a `SubmitOrder` *command* to prove denial proves the opposite; the denial
   lives in `Strategy.submit_order`.
8. **A submit task cancelled on disconnect is silently dropped** (`live/execution_client.py:259-269`).
   Correct for NFR6 (it is the opposite of a replay) and worth a sentence in the builder
   docstring, but do not "fix" it — the order never left the process, and the strategy's next
   signal is the only legitimate resend.
9. **Import guards.** New test files import from `nautilus_trader.test_kit.mocks` — fine
   (`PERMITTED_THIRD_PARTY` governs `src/` live modules, not tests). Task 5 adds constants, not
   imports, to `live_node_builder.py`; if you find yourself adding a stdlib import there,
   `_STDLIB_AND_FIRST_PARTY` must gain it by hand in the same commit, and Task 7.2's claim changes.
10. **Size caps on executable statements** (D4): budget before the edit (Task 5.3); `live_node_builder.py`
    is already over the raw-line figure by design (docstring-dominated) — disclose, do not split.
11. **RED evidence is a moment, not a reconstruction** (3.3:399-400). Capture each test's first
    failing run as it is written. For the canaries that are GREEN on first run (Task 6.1), say so
    and let the Task 9 mutation carry non-vacuity — do not fabricate a RED.

### Testing standards summary

- Tiers: **unit** for the AR24 AST scan (`tests/unit/core/test_order_path_has_no_retry.py`);
  **component** for the live-engine canaries, the wrapper no-replay pin, the mock-database restore
  (`tests/component/core/test_live_order_recovery.py`) and the builder pin
  (`tests/component/core/test_live_node_builder.py`); **integration `--forked`** for the
  real-Redis restore (`tests/integration/core/test_live_order_survives_restart.py`, skip when Redis
  is unreachable, never flush). No real broker anywhere (NFR32). Live tier is observational only
  (Task 8) — recorded, not staged.
- Markers on every test; TDD Red first with **RED evidence captured as each test is written**
  (3.3's closeout could not produce it; this story must).
- Every "the count is exactly N" assertion is an exact integer against the client double's
  `calls`/`commands`, never `>= 1`, never at the strategy.
- Anti-tautology twins are mandatory for the scanner (Task 2.3), the restore (Task 4.2), and the
  builder pin (Task 5.2) — a guarantee whose absence is not observable is not a guarantee.
- Existing files that must need **zero changes**: `test_live_order_path.py`,
  `test_session_runner_order_path.py`, `test_session_runner_phases.py`,
  `test_client_order_id_determinism.py`. If any goes red, a non-change contract is being violated.
- Known residual to state, not hide: the reconnect and timeout paths are proven against
  `MockLiveExecutionClient`, not against the IB adapter's `reqOpenOrders` — the adapter's own
  not-found behaviour (Hazards #1) is cited from source, not exercised.
- Baselines at story creation (measured 2026-09-10): unit 2421 · component 1441/16 skipped ·
  integration 281/2 skipped · e2e 1. Head `35c1e1b`, tree clean, submodule at `06c00cb`.

### Project Structure Notes

- New: `tests/unit/core/test_order_path_has_no_retry.py`,
  `tests/component/core/test_live_order_recovery.py`,
  `tests/integration/core/test_live_order_survives_restart.py`, Procedure P10 in
  `docs/qa/phase3-live-verification.md` (Task 8.4).
- Modified: `src/core/live_node_builder.py` (four constants + four explicit kwargs + docstring),
  `tests/component/core/test_live_node_builder.py` (`TestInFlightCheckIsExplicit` + wheel-default
  canary), `_bmad-output/implementation-artifacts/deferred-work.md` (new section),
  `sprint-status.yaml`, this story file.
- NOT modified (evidence contracts, Task 7): `src/core/live_order_path.py`,
  `src/core/live_session_runner.py`, `src/core/live_connection_monitor.py`,
  `src/core/live_strategy_guard.py`, `src/core/strategies/**`, `src/db/**`, `alembic/**`,
  `src/services/**`, `src/models/**`, `src/cli/**`, guard-list contents in all four guard test
  files, `CLAUDE.md` (no new membership-pinned list lands in `src/`; the two pinned sets in Task
  2.2 live in a test file and are documented there).
- Naming: no new log events. New constants in `live_node_builder.py` follow the `NODE_TIMEOUT_*`
  register (`EXEC_ENGINE_RECONCILIATION`, `EXEC_ENGINE_INFLIGHT_CHECK_INTERVAL_MS`,
  `EXEC_ENGINE_INFLIGHT_CHECK_THRESHOLD_MS`, `EXEC_ENGINE_INFLIGHT_CHECK_RETRIES` — or the
  shorter `INFLIGHT_*` form; pick one and say why). No AR36 stem in any new identifier or docstring
  sentence an operator might see.
- Commit shape: one `feat(live):` commit carrying src + all test tiers + BMAD artifacts + docs/qa
  together (the 3.1/3.2/3.3 precedent); subject is an operator-outcome sentence (e.g. `feat(live):
  prove a working order is queried, never resent`); no AI references.

### References

- Story source: `_bmad-output/planning-artifacts/epics.md:1280-1327` (ACs `:1288-1307`, the
  flagged dependency `:1310-1321`, the D1 cross-reference `:1323-1327`); epic context
  `:1105-1114`; the retro block's item 2 `:420-424`; dependency structure `:480-494`
- Requirements (`epics.md`): FR27 `:69`; NFR6 `:121`; NFR10 `:125`; NFR14 `:129`; NFR21 `:142`;
  NFR32 `:162`; AR10 `:189`; AR23 `:211`; AR24 `:212`; AR25 `:213`; AR40 `:240`; AR43 `:245`
- Architecture: invariants `architecture.md:65-67`; cross-cutting concern 2 `:121-122`; D7
  order-path policy `:286-296` ("No retry-on-timeout for order placement, anywhere" `:290`,
  AR25 text `:293-294`); testing split `:413-416`
- PRD: the irreversible failure mode `prd.md:441-444`; risk table row `:767`; FR27 `:842-843`;
  reliability invariant `:935-936`
- Retro: `epic-2-retro-2026-08-28.md:337-400` (Epic 3 preview, item 4 is this story's
  dependency); D1 ruling `:465-470` and table `deferred-work.md:2083-2100`
- Previous stories: `3-3-track-every-order-through-its-full-lifecycle.md` (scope fence `:486-513`,
  hazards `:513-548`, the RED-evidence finding `:399-400`, review-patch record `:814-902`);
  `3-2-submit-a-strategys-orders-to-the-broker.md` (the AC #4 trap `:246-266`, counter restore
  `:303-307`, live preconditions Task 8.1 `:151`, scope fence `:352-372`)
- Deferred work: `deferred-work.md:2243-2349` (the 2026-09-01 section — NFR1 ruling `:2245-2307`,
  stranded `orders_open` `:2309-2325`, the dry run that traded `:2327-2342`, startup message
  `:2344-2349`); 3.2 review section `:2139-2211` (cancel-exclusion rationale `:2186-2191`,
  suppression owner gap `:2200-2210`); 3.3 review section `:2212-2242`
- Current code: `src/core/live_node_builder.py:79-103, 337-383, 393-425, 583-622`;
  `src/core/live_order_path.py:16-17, 125-127, 175, 239-287, 346-508, 597-626`;
  `src/core/live_session_runner.py:326-350, 503-517, 530-535, 542-564, 628-665`;
  `src/core/live_cache.py:110-151`; `src/core/live_trader_id.py:58-87`;
  `src/core/live_connection_monitor.py:188-216`
- Wheel (all under `.venv/lib/python3.11/site-packages/nautilus_trader/`): `live/config.py:76-187`;
  `live/execution_engine.py:130-131, 171, 405-447, 537-571, 583-685, 850-992, 994-1037,
  1128-1266`; `live/execution_client.py:245-276, 330-335, 434-527`; `execution/engine.pyx:640-656,
  695-775, 996-1027, 1087-1088`; `execution/config.py:65-71`; `system/kernel.py:299-351, 455-456,
  991-1027, 1244-1250`; `cache/cache.pyx:373-396, 455-466, 1133-1209, 4217-4245`;
  `cache/config.py:58-68`; `model/orders/base.pyx:389-435`; `trading/strategy.pyx:348-395,
  804-809, 1395-1430`; `common/generators.pyx:117-153`; `adapters/interactive_brokers/execution.py:263-298,
  300-372, 374-445, 447-452, 454-505, 652-665, 908-984`; `adapters/interactive_brokers/client/order.py:42-58,
  100-137`; `adapters/interactive_brokers/client/error.py:18-21, 100-108, 200-236`;
  `test_kit/mocks/exec_clients.py:146-330`; `test_kit/mocks/cache_database.py:37-191`
- Guard lists: `tests/unit/core/test_live_node_never_exits.py:42-67, 86-91`;
  `tests/unit/core/test_live_stop_path_is_inert.py:38-80, 109-134`;
  `tests/component/core/test_session_runner_phases.py:1214-1254`;
  `tests/component/core/test_live_dependency_invariance.py:39, 164-232`;
  `tests/integration/core/test_epic1_ac_node.py:404-445, 449-545`
- Test harnesses to reuse: `tests/component/core/test_client_order_id_determinism.py:40-43, 60-83`;
  `tests/component/core/test_live_order_path.py:91-107, 149-163, 345-385`;
  `tests/integration/core/test_live_cache_namespace.py:44-72, 185-240`
- Live: `docs/qa/phase3-live-verification.md` (P7 `:655-812` incl. the 2026-09-01 row `:758`;
  procedure conventions — outcome tables and result-log columns as in P1 `:68-90`);
  `scripts/diagnostics/run_p7_position.sh`; 3.2 Task 8.1 preconditions

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5), via Claude Code.

### Debug Log References

Task 1 fresh-interpreter probes (three, each `PYTHONPATH=. uv run python <scratchpad script>`,
never `Popen`+`PIPE`; the process's own exit is the assertion):

1. **`probe_live_engine.py`** — measured the in-flight sweep end to end against a real
   `LiveExecutionEngine` + `MockLiveExecutionClient`: submit → 1 `submit_order` call; deliver only
   `OrderSubmitted`; advance the clock past `inflight_check_threshold_ms`; one sweep →
   `generate_order_status_report` is **not** in `client.calls` (see finding 1 below); six sweeps →
   `REJECTED`, `reason="UNKNOWN"`, `reconciliation=True`, submit count still 1. Also drove the
   restore round trip with `MockCacheDatabase` and hit the `ValueError` in finding 2 below.
2. **`probe_counter.py`** — isolated finding 1 below by comparing a probe subclass registered
   under its own class name vs. an explicit `strategy_id="SMACrossover"` override, and confirmed
   the corrected duplicate-resubmission shape (a fresh `INITIALIZED` `MarketOrder` carrying the
   restored order's `client_order_id`) produces `OrderDenied(reason="duplicate ...")` with zero
   client calls.
3. **`probe_reconnect.py`** — confirmed the fix for finding 1: a client subclass overriding
   `query_order` to call `self.create_task(self._query_order(command))` (the base class's real
   body, never reached by the stock mock) and `generate_order_status_report` keyed by
   `client_order_id` correctly drives one sweep to `ACCEPTED`, in `orders_open()`, retry counter
   cleared, submit count still 1.

Two measured findings corrected the story's drafted Task 3/4 design before any test was written
(both cited from source in the module docstring of `tests/component/core/test_live_order_recovery.py`
and recorded in `deferred-work.md`'s new "story-3.4" section):

1. `MockLiveExecutionClient.query_order` (`test_kit/mocks/exec_clients.py:277`) is a pure
   call-recorder — it overrides the base `LiveExecutionClient.query_order`
   (`live/execution_client.py:330-335`) entirely, so `generate_order_status_report` is never
   reached by the stock mock. The timeout path's guard became `"query_order" in client.calls`; the
   reconnect path needed the `_AnsweringClient` subclass above.
2. `strategy.submit_order(restored_order)` on the literal restored object raises `ValueError` at
   Nautilus's own `Condition.is_true(order.status_c() == OrderStatus.INITIALIZED, ...)`
   precondition (`trading/strategy.pyx:794-796`), which runs before the duplicate-`client_order_id`
   check (`:806-808`) — an ACCEPTED restored order can never reach `submit_order` again by
   definition. `_duplicate_of()` builds a fresh `INITIALIZED` order with the same `client_order_id`
   instead, which is the shape that actually reaches the duplicate check.

A third finding surfaced during Task 9's mutation sweep (M5), not Task 1: the AR24 AST scan's
loop rule (c) cannot see a retry loop wrapped around `_wrap`'s own generic `base(*args, **kwargs)`
pass-through, because the callee there is the dynamic `base` parameter, never the literal name
`submit_order`/`submit_order_list` the scan matches on. Closed with a second structural pin
(`TestTheWrapperClosureHasNoQueueAttribute.test_the_base_forwarding_call_is_not_inside_a_loop_or_try`)
that walks `_wrap`'s AST for a loop/`try` ancestor of the `base(...)` call specifically.

A fourth finding, also from the mutation sweep (M8): the first version of
`TestASuppressedCallIsNeverReplayed` flipped `withheld` back to `False` and asserted nothing was
submitted, but drove no call afterward — a "queue and replay on the next call" mutation left it
green. Fixed by adding `test_a_later_call_does_not_also_resend_the_earlier_suppressed_one`, which
submits a fresh signal after the flip and asserts only the fresh order reaches the exec layer.

### Completion Notes List

- **All 9 tasks and their subtasks complete except 8.2** (the live IBKR transcript — no Gateway
  available in this environment). Per the story's own Task 8.4 / the Epic 2 retro's standing rule
  (3.2 Task 8.1 / 3.3 Task 6.3 precedent), the story goes to `review` with that one item
  `⏳ not yet run`, not `done`.
- **Production diff is exactly what Task 5 scoped**: four new constants
  (`EXEC_ENGINE_RECONCILIATION`, `EXEC_ENGINE_INFLIGHT_CHECK_INTERVAL_MS`,
  `EXEC_ENGINE_INFLIGHT_CHECK_THRESHOLD_MS`, `EXEC_ENGINE_INFLIGHT_CHECK_RETRIES`, all equal to the
  measured wheel defaults) plus four explicit kwargs on the builder's `LiveExecEngineConfig(...)`
  call, `src/core/live_node_builder.py` only. Every file in Task 7's zero-diff list (`live_order_path.py`,
  `live_session_runner.py`, `live_connection_monitor.py`, `live_strategy_guard.py`,
  `src/core/strategies/**`, `src/db/**`, `alembic/**`, `src/services/**`, `src/models/**`,
  `src/cli/**`, and the guard-list contents in all four guard test files) verified empty via
  `git diff --stat` — confirmed, not assumed, per the 3.3 lesson this story cites.
- **AC #2's scanner has two disclosed scope boundaries**, both closed by a second guard rather than
  widening the first: rule (c) matches only the literal callee names `submit_order`/
  `submit_order_list`, so a retry loop around `_wrap`'s generic `base(...)` pass-through is outside
  its reach by construction — covered instead by a dedicated structural AST pin on `_wrap` itself
  (see Debug Log finding 3). Both are exercised by their own `TestTheProbeCanActuallyFail`-style
  non-vacuity tests.
- **Nine mutations run, all named, all confirmed red then reverted** (Task 9.1): M1
  (`inflight_check_interval_ms=0`) and M2 (`reconciliation=False`) — real edits to
  `live_node_builder.py`, reverted from a `/tmp` backup, both red on
  `TestInFlightCheckIsExplicit`. M3 (engine resubmits inside `_resolve_inflight_order`) — an
  in-process monkeypatch of `LiveExecutionEngine._resolve_inflight_order` (no file touched),
  submit count moved from 1 to 2, confirming the exact-count assertion is sensitive to exactly
  this regression. M4 (planted `@retry` on `_wrap`) and M5 (planted a bare `for/try/except` retry
  loop around `base(...)`) — fed the real `live_order_path.py` source (read into memory, never
  written to disk) through the unit-tier scanner and the new structural pin respectively; both
  caught, M5 only by the new pin (see Debug Log finding 3). M6 (process B uses a fresh database)
  and M7 (process B skips `load_cache()`) — real one-line edits to
  `test_live_order_recovery.py`, reverted from a `/tmp` backup, both red (a hard `AttributeError`
  on the restored-order lookup, an even stronger signal than a soft assertion failure). M8 (give
  the wrapper a `deque` and replay on unmute) — a real edit to `live_order_path.py`, reverted from
  a `/tmp` backup; caught by both structural pins immediately, and — after Debug Log finding 4's
  fix — by the strengthened behavioral test too. M9 (a `CANCELED` report instead of `ACCEPTED`) —
  turned into permanent coverage
  (`TestTheReconnectPathQueriesAndNeverResubmits.test_a_canceled_report_resolves_without_a_resubmit`)
  rather than a disposable mutation, since it is a legitimate third venue answer the design must
  handle, not only a hypothetical break.
- **Clause matrix** (Task 9.2), AC → obligation → killing test:

  | AC | Obligation | Killing test |
  |---|---|---|
  | #1 | reconciliation/in-flight fields explicit | `TestInFlightCheckIsExplicit.test_the_builder_sets_the_four_values_explicitly` (M1, M2) |
  | #1 | wheel-default canary | `TestInFlightCheckIsExplicit.test_the_nautilus_stock_defaults_still_equal_our_explicit_values` |
  | #1 | query happens on timeout | `TestTheTimeoutPathQueriesAndNeverResubmits` (`"query_order" in client.calls`) |
  | #1 | ACCEPTED resolution, one submit | `TestTheReconnectPathQueriesAndNeverResubmits.test_one_sweep_resolves_to_accepted_with_one_submit_total` |
  | #1 | UNKNOWN resolution + observer record | `TestTheTimeoutPathQueriesAndNeverResubmits.test_the_observer_logs_exactly_one_order_rejected_with_the_venue_reason` |
  | #2 | no retry library import | `TestNoRetryLibraryIsImported` (M4-shape via `TestTheProbeCanActuallyFail`) |
  | #2 | no retry/backoff decorator | `TestNoRetryOrBackoffDecorator` (M4) |
  | #2 | no submit in loop/except (literal names) | `TestNoSubmitOrderInsideALoopOrExceptHandler` |
  | #2 | no loop/try around `_wrap`'s generic pass-through | `TestTheWrapperClosureHasNoQueueAttribute.test_the_base_forwarding_call_is_not_inside_a_loop_or_try` (M5) |
  | #3 | restore visible before `on_start` | `TestARestartedProcessSeesItsWorkingOrderBeforeOnStart` (M6, M7) |
  | #3 | anti-tautology twin | `TestTheRestoreGuaranteeIsLoadBearing` |
  | #3 | real-Redis byte-equal round trip | `tests/integration/core/test_live_order_survives_restart.py` |
  | #4 timeout | exactly 1 submit across the timeout path | `TestTheTimeoutPathQueriesAndNeverResubmits` (M3 via monkeypatch) |
  | #4 reconnect | exactly 1 submit; a later distinct signal is not a duplicate | `TestTheReconnectPathQueriesAndNeverResubmits.test_a_fresh_signal_may_submit_a_distinct_new_order` |
  | #4 reconnect | CANCELED resolves without a resubmit | `TestTheReconnectPathQueriesAndNeverResubmits.test_a_canceled_report_resolves_without_a_resubmit` (M9) |
  | #4 restart | restored order not resubmitted; lookalike denied, zero client calls | `TestARestartedProcessSeesItsWorkingOrderBeforeOnStart` |
  | #4 suppression | no replay, immediately or on a later call | `TestASuppressedCallIsNeverReplayed` (both tests, M8) |
  | #4 suppression | structural: no queue-like state, no loop around the forwarding call | `TestTheWrapperClosureHasNoQueueAttribute` (both tests, M8, M5) |

- **Gates, both units** (Task 9.3): format 499 files unchanged; lint clean; mypy clean, 105 source
  files (pre-existing `annotation-unchecked` notes on `backtest_orchestrator.py`, untouched by this
  story). Unit 2421 → **2441** (+20, `test_order_path_has_no_retry.py`). Component 1441/16 sk →
  **1457/16 sk** (+16: 13 in `test_live_order_recovery.py`, 3 `TestInFlightCheckIsExplicit` in
  `test_live_node_builder.py`). Integration `--forked` 281/2 sk → **282/2 sk** (+1,
  `test_live_order_survives_restart.py`, run against a real local Redis). e2e 1 → **1** (unchanged).
  Epic 1 acceptance sweep still 40/40 (unaffected by this story).
- **Live verification**: run 2026-09-11, ✅ pass — see Procedure P10 in
  `docs/qa/phase3-live-verification.md`. AC #1/#2/#3/#4's substance is proven exhaustively against
  broker doubles (NFR32-compliant); P10 is the observational NFR1/restore-transcript evidence, and
  it now confirms the same properties live: restore-before-`session.started`, a strictly
  incrementing `client_order_id` counter with zero repeats, and both NFR1 fields on every
  `order.submitted` record. Run against a fresh session (`p10-fresh-0911`) rather than the
  story's suggested `p7-fill-0901`, because that session's own cache reported a stale
  `net_position=22` for NVDA that the broker did not hold (reconciliation on restart only
  reconciled `AAPL.NASDAQ`, not `NVDA.NASDAQ`) — routed to Story 4.2, see deferred-work.md.
  **Unrelated to this story's own mechanism, the same live run exposed a real bug in
  `sma_crossover.py`'s reversal handling**, fixed the same day: `_generate_buy_signal`/
  `_generate_sell_signal` ran "close the opposite side" and "open a new position" as two
  independent `if`s rather than a mutually-exclusive choice, so a reversal fired both and
  submitted two orders. Live-observed: one SELL crossover produced two real fills and left the
  broker short when the strategy's own bookkeeping reported flat. The resulting position was
  corrected manually post-hoc (broker re-verified flat), and the strategy itself was fixed —
  `if`/`elif`, two new integration tests (`TestReversalSubmitsExactlyOneOrder`,
  `tests/integration/test_sma_strategy_nautilus.py`) proving RED then GREEN, full suite re-run
  with zero regressions (unit 2441, component 1457/16sk, integration 282→284/2sk, e2e 1). Full
  writeup in deferred-work.md. Not a BMAD story — no epic-3 story owns strategy-level position
  logic, and this was fixed directly on operator instruction rather than through the story
  pipeline.

### File List

**New:**
- `tests/unit/core/test_order_path_has_no_retry.py`
- `tests/component/core/test_live_order_recovery.py`
- `tests/integration/core/test_live_order_survives_restart.py`

**Modified:**
- `src/core/live_node_builder.py` (four constants + four explicit `LiveExecEngineConfig` kwargs +
  docstring block)
- `tests/component/core/test_live_node_builder.py` (`TestInFlightCheckIsExplicit` class)
- `docs/qa/phase3-live-verification.md` (new Procedure P10)
- `_bmad-output/implementation-artifacts/deferred-work.md` (new "Deferred from: story-3.4" section)
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/3-4-never-resubmit-an-order-that-is-already-working.md` (this file)

## Change Log

| Date | Change |
|---|---|
| 2026-09-10 | Story created (backlog → ready-for-dev) from `epics.md:1280-1327` against head `35c1e1b` (tree clean). Drafted after three parallel measurements: the installed 1.220.0 wheel (in-flight check, reconciliation, cache restore, IB adapter, test-kit mocks — 44 source reads), the 3.2/3.3 story records and the 2026-09-01 live findings, and the live wiring / guard lists / test harnesses. **The story proves and pins; it builds one config edit.** Central drafting decisions, all recorded in "Why this story is shaped": (1) the flagged Epic 4 backwards dependency (epics.md:1310-1321) resolves to **option (a)** — the wheel restores `orders_open` in the kernel constructor and starts strategies only after its own reconciliation, so AC #3 is provable with doubles today; what reconciliation *does* with a disagreement, including the 2026-09-01 stale-order stall, stays Story 4.2's; (2) the fencing column is **not** absorbed — D1 is ruled to Story 3.6; (3) AC #1's "queries the venue" is made an explicit builder decision with a wheel-default canary rather than an accident of omitted kwargs; (4) AC #2 becomes a unit-tier AST scan with a non-vacuity twin; (5) one new hazard found in the wheel and routed rather than solved: the IB adapter answers "not found" with a local `OrderCanceled`, conflating filled-while-disconnected with cancelled — Story 4.3. Baselines measured: unit 2421 · component 1441/16 sk · integration 281/2 sk · e2e 1. |
| 2026-09-11 | Story implemented: `ready-for-dev` → `in-progress` → `review`. All 9 tasks / 33 subtasks complete except 8.2 (the live transcript, `⏳ not yet run` — no IBKR Gateway available in this environment; not blocking per the Epic 2 retro's standing rule, 3.2/3.3 precedent). Task 1's fresh-interpreter probes surfaced two corrections to the story's drafted Task 3/4 design before any test was written — `MockLiveExecutionClient.query_order` never reaches `generate_order_status_report` (it overrides the base method's async chain entirely), and resubmitting a restored order literally raises `ValueError` on Nautilus's own INITIALIZED precondition rather than producing `OrderDenied` — both recorded in the new component test file's module docstring and in `deferred-work.md`. The Task 9 mutation sweep found two more: the AR24 loop scan (rule c) cannot see a retry loop around `_wrap`'s generic `base(...)` forwarding call (closed with a second structural AST pin), and the original suppression-replay test never drove a call *after* unmuting so a queue-and-replay-on-next-call mutation left it green (closed by adding that call). All nine scripted mutations (M1–M9) confirmed red then reverted or turned into permanent coverage; clause matrix recorded above. Production diff is exactly Task 5's scope — four new constants and four explicit kwargs on `live_node_builder.py`'s `LiveExecEngineConfig(...)` call, at the measured wheel defaults, with a component pin and wheel-default canary. Zero diffs verified on every file Task 7 named, zero guard-list edits. Gates: format/lint/mypy clean; unit 2421→2441 (+20); component 1441/16sk→1457/16sk (+16); integration `--forked` 281/2sk→282/2sk (+1, real local Redis); e2e 1→1. New Procedure P10 recorded in `docs/qa/phase3-live-verification.md`, result `⏳ not yet run`. New `deferred-work.md` section routes the IB adapter's not-found/cancelled conflation to Story 4.3 and records the four measured corrections above. |
| 2026-09-11 | Story closed: `review` → `done`. Task 8.2 run against the paper Gateway inside RTH: restore-before-`session.started`, a strictly incrementing `client_order_id` counter with zero repeats, and both NFR1 fields on every `order.submitted` — all four P10 pass criteria met, this story's own AC #1-#4 all hold live. Run against a fresh session (`p10-fresh-0911`) instead of the story's suggested `p7-fill-0901`, whose cache turned out to hold a stale `net_position=22` for NVDA that the broker did not have (routed to Story 4.2). **The same run found a live, previously-undisclosed defect outside this story's scope**: `sma_crossover.py`'s reversal handlers re-use a `has_long`/`has_short` value computed before `close_position()` runs, so a reversal signal submits two orders instead of one — observed live as two real fills that left the broker short while the strategy's own bookkeeping reported flat. Not a Story 3.4 regression (the exec-engine recovery path this story owns produced zero duplicate submissions in the same transcript); logged in deferred-work.md with a recommendation to fix before further live use of `sma_crossover`. Also found: `flatten_position.py`'s `--confirm` path (added by the 2026-09-01 fix, commit `29e9130`) now fails every time with `Cannot start strategy, ... not found` — Nautilus 1.220 refuses `add_strategy()` on an already-RUNNING trader, so the fix that closed the "dry run traded" hole also closed off the tool's only real use. Fails safe (no order reaches the broker), but the tool cannot currently close a position on purpose; the resulting SHORT 22 NVDA was closed with a throwaway one-off script instead, broker re-verified flat afterward. |
| 2026-09-11 | Follow-up, same session: the `sma_crossover.py` reversal defect found above was fixed, on operator instruction, outside the story pipeline (no epic-3 story owns strategy-level position logic, so this is not a Story 3.4 change and not a BMAD story). Correction to how the defect was first described: `has_long`/`has_short` are not actually stale in the sense of flipping truth value (closing one side doesn't affect the other side's flag) — the real defect is structural: `_generate_buy_signal`/`_generate_sell_signal` run "close the opposite side" and "open a new position" as two independent `if`s instead of a mutually-exclusive choice, so a genuine reversal fires both. Fixed with `if`/`elif` in both methods. TDD: two new integration tests in `tests/integration/test_sma_strategy_nautilus.py` (`TestReversalSubmitsExactlyOneOrder`, SELL-side and a BUY-side mirror), each driving a real `BacktestEngine` over an engineered 6-bar reversal with `fast_period=2, slow_period=3` — both confirmed RED (2 orders each, mismatched quantities) before the fix, GREEN after. Full suite re-run clean: unit 2441 (unchanged), component 1457/16sk (unchanged), integration 282→284/2sk (+2), e2e 1 (unchanged); format/lint/mypy clean. The three backtest-runner integration files exercising `sma_crossover` end-to-end also re-run clean (23/23). Full writeup, including that every historical backtest reversal of this strategy paid the same double commission (not re-audited here), in `deferred-work.md`'s "story-3.4" addendum. |
