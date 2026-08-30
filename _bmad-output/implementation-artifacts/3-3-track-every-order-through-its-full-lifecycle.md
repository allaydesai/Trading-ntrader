# Story 3.3: Track Every Order Through Its Full Lifecycle

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want each order's whole life recorded — including the states a backtest never produces,
so that the order history is trustworthy evidence rather than an assumption that submission implies
a fill.

## Why this story is shaped the way it is

This story **extends** the order-event observer Story 3.2 built; it does not build a new consumer.
`OrderEventObserver` was designed for exactly this moment — 3.2's scope fence reads *"build it so
3.3 extends the same subscription point"* — and the extension surface is one method:
`handle_order_event` currently drops every event whose class name is not `"OrderSubmitted"`
(`src/core/live_order_path.py:321-322`). [Source: 3-2-submit-a-strategys-orders-to-the-broker.md:338-341]

Three measured facts make this story small and sharply bounded:

1. **Every order event already arrives.** All order events — venue-sourced and denied — publish on
   `events.order.{strategy_id}` (`execution/engine.pyx:911, 1176, 1248` in the installed wheel), and
   the runner's existing `events.order*` subscription (`live_session_runner.py:563`,
   `ORDER_EVENTS_TOPIC` at `live_order_path.py:122`) already delivers all of them to the observer.
   **Zero new subscriptions, zero runner diffs, zero guard-list edits** — evidence contracts, below.
2. **There is no `OrderPartiallyFilled` event.** Grep of `model/events/order.pyx`/`.pxd`/
   `__init__.py` in the installed 1.220.0 wheel: zero hits. A partial fill is an ordinary
   `OrderFilled`; only the *order object's* FSM distinguishes it
   (`model/orders/base.pyx:1116-1120`: `filled_qty + last_qty < quantity` → `PARTIALLY_FILLED`,
   else `FILLED`). AR41 already encodes the consequence: `order.filled` carries `fill_qty` **and**
   `cum_qty`, and the rising `cum_qty` series *is* the partial-fill representation.
3. **`OrderFilled` carries no cumulative quantity** — only `last_qty`/`last_px`, no `cum_qty`, no
   `leaves_qty`, no `.order` accessor (events expose identifiers only). Cumulative state lives on
   the `Order` object (`model/orders/base.pxd:99-101`), which the framework-free observer cannot
   reach. `cum_qty` is therefore **derived in-observer** (design pinned in Dev Notes).

What is measured ABSENT today (the story's whole surface): `OrderAccepted`, `OrderRejected`,
`OrderCanceled`, `OrderExpired`, `OrderFilled`, and `OrderDenied` all reach `handle_order_event`
and are dropped without a record. The rejection drop is pinned by name —
`test_an_order_rejected_is_contained_without_crashing_or_logging` asserts `logs == []`
(`tests/component/core/test_live_order_path.py:430-439`) — and this story **inverts that
assertion** while keeping its containment half. The 3.2 review dismissed
"`OrderDenied`/`OrderRejected` leaving no `order.*` record" as noise *because* "Story 3.3 owns the
lifecycle" — that ownership lands here, `OrderDenied` included.
[Source: 3-2-submit-a-strategys-orders-to-the-broker.md:226]

## Acceptance Criteria

1. **Given** a submitted order
   **When** it progresses
   **Then** accepted, partially filled, filled, rejected, cancelled, and expired are each represented
   and logged as they arrive from the venue (FR24, NFR21).
   *Representation decision, made here so dev does not invent an event Nautilus doesn't have:
   "partially filled" is represented by `order.filled` records whose `cum_qty` is below the order's
   quantity — one record per fill, `cum_qty` rising — exactly mirroring how Nautilus itself
   represents partials (no dedicated event; FSM-side distinction only,
   `model/orders/base.pyx:1116-1120`). A single-fill order emits one `order.filled` with
   `fill_qty == cum_qty`. No `order.partially_filled` event name exists or is wanted — AR41's
   normative list has none, deliberately.*

2. **Given** each order event
   **When** it is logged
   **Then** it uses the dotted past-tense event names — `order.accepted`, `order.filled` with
   `fill_qty` and `cum_qty`, `order.rejected` with `venue_reason` — each carrying `session_id`,
   `client_order_id`, and `instrument_id` (AR41).
   *Names pinned at creation (all conformant by construction under AR41's `order.*` namespace,
   none carrying an AR36 stem): `order.accepted`, `order.rejected`, `order.filled`,
   **`order.canceled`**, **`order.expired`**, **`order.denied`**. `canceled` is single-l,
   matching the Nautilus event class `OrderCanceled`, so a transcript grep for the class name and
   the structured record agree; the epic prose's "cancelled" describes the state, not the event
   name. Field-by-field contract is in Task 2 — including the fields that must **never** appear
   (`account_id` rides on every venue-sourced Nautilus order event; this story pins
   never-logged-at-all, deliberately stricter than NFR26's literal "never logged in full").*

3. **Given** a rejected order
   **When** the rejection arrives
   **Then** the reason reported by IBKR is captured verbatim and logged, never swallowed or replaced
   by a generic message (FR26).
   *Verbatim means `str(event.reason)` with no rewording, truncation, or classification. Known
   wheel behaviour to preserve, not "fix": `OrderRejected` normalises an empty/absent reason to the
   literal string `"None"` in its own constructor (`model/events/order.pyx:1991`) — the observer
   passes through whatever the event carries. Containment survives the inversion: a rejection is
   logged AND never raises (the 3.2 pin's containment half).*

4. **Given** order events
   **When** they are consumed
   **Then** they arrive through Nautilus's own handlers (`on_order_rejected`, `on_position_closed`,
   and peers) — no polling loop and no parallel event bus exists (AR26).
   *Scoping decision, made here so review does not bounce it: the sanctioned consumption path is
   the runner's existing subscription on Nautilus's own message bus — the same bus and the same
   delivery mechanism that dispatches `Strategy.on_order_*` hooks (`handle_event` cascade,
   `trading/strategy.pyx:1615-1700`), and the same wildcard-subscriber shape Nautilus's own
   `Portfolio` (`portfolio/portfolio.pyx:187`, `topic="events.order.*"`) and `RiskEngine`
   (`risk/engine.pyx:188`) use internally. Strategy-level `on_order_*` overrides are NOT wanted:
   strategies stay mode-agnostic (AR40) and this story lands with **zero diffs under
   `src/core/strategies/`** — the same evidence contract as 3.2's AC #1. "No polling loop, no
   parallel event bus" is satisfied structurally: the observer has no thread, timer, task, or
   queue, and its only entry points are the two subscribed handlers.*

5. **Given** any single order
   **When** its history is reconstructed from logs
   **Then** submission, acknowledgement, every fill, and its final state are all traceable end to
   end (NFR21).
   *Operationalised as a component test that publishes a full real-event lifecycle
   (`OrderSubmitted → OrderAccepted → OrderFilled(partial) → OrderFilled(final)`) plus a rejection
   path (`OrderSubmitted → OrderRejected`) through the observer and asserts the captured records
   alone reconstruct each: one shared `client_order_id` across records, the final `order.filled`
   showing `cum_qty == order_qty` (fill-path finality readable from the record alone — Task 3.2's
   quantity harvest exists for this clause; no Nautilus event marks a fill as final), a terminal
   record on the rejection path with `venue_reason`.
   "Acknowledgement" is `order.accepted` carrying `venue_order_id` — measured:
   `OrderSubmitted.venue_order_id` is hardcoded `None` in the wheel ("Pending assignment by
   venue", `model/events/order.pyx:1534-1543`), so acceptance is the first moment a venue-side
   identity exists. NFR21's evidence lives in logs — this story writes **no database rows**
   (order history has no table; trade persistence is Story 3.6's).*

## Tasks / Subtasks

- [x] Task 1: Measure the event surface before writing production code (AC: all)
  - [x] 1.1 Fresh-interpreter probe (the 3.2 Task 1.2 pattern — real `MessageBus`, no C logging):
    publish one of EACH — `OrderAccepted`, `OrderRejected`, `OrderCanceled`, `OrderExpired`,
    `OrderFilled`, `OrderDenied` — on `events.order.{strategy_id}` and confirm a plain
    `events.order*` subscriber receives all six. Build the first five via
    `nautilus_trader.test_kit.stubs.events.TestEventStubs` (`order_accepted` :208,
    `order_rejected` :227, `order_canceled` :362, `order_expired` :379, `order_filled` :302);
    **there is no `order_denied` stub** — construct `OrderDenied` directly (ctor
    `model/events/order.pyx:661-669`: `trader_id, strategy_id, instrument_id, client_order_id,
    reason, event_id, ts_init` — note: no separate `ts_event` parameter).
  - [x] 1.2 Measure the str/Decimal renderings the field builders will use: `str(event.last_qty)`,
    `event.last_qty.as_decimal()` (expected present — `model/objects.pyx:543`), `str(event.last_px)`,
    `str(event.commission)` (expected `"<amount> <CUR>"` shape), `str(event.currency)`,
    `str(event.venue_order_id)`, `str(event.trade_id)`, and `OrderInitialized.quantity` (the
    Task 3.2 harvest depends on this read). Record every rendering in the Dev Agent Record — the
    log-field contract in Task 2 depends on them.
  - [x] 1.3 Confirm `reconciliation` is readable on every one of the six types (property on each;
    `OrderSubmitted` exposes it as a property despite lacking the ctor kwarg) — the
    reconciliation-sourced-event flag matters because a reconciled fill is NOT a live venue
    arrival (the P7 transcript's one `OrderFilled` was reconciliation's inferred fill for a
    residual position, not a session fill — `docs/qa/phase3-live-verification.md:757`).
- [x] Task 2: RED first — per-type dispatch tests in `tests/component/core/test_live_order_path.py` (AC: #1, #2, #3)
  - [x] 2.1 **Invert the named pin**: `test_an_order_rejected_is_contained_without_crashing_or_logging`
    (`:430-439`, `assert logs == []` at `:439`) becomes a test that `OrderRejected` produces
    exactly one `order.rejected` record carrying `venue_reason` — verbatim from a distinctive
    reason. `TestEventStubs.order_rejected` HARDCODES `reason="ORDER_REJECTED"`
    (`test_kit/stubs/events.py:238`, no override parameter), so construct `OrderRejected` directly
    (ctor `model/events/order.pyx:1971-1983`) with a reason no generic literal would match, making
    a swallow-and-reword mutation go red. Also assert `client_order_id`,
    `instrument_id`, `strategy_id`, `due_post_only`, and session_id via the bound logger — AND
    still never raises — AND that NO `order.observer_failed` record appears (a builder that logs,
    then raises into containment, must fail here; this preserves the one property the deleted
    `logs == []` guarded) — AND `log_level == "warning"` (Task 3.3's severity choice, pinned here
    and on `order.denied`'s test). Keep the containment assertion in the same test; rename it to
    say what it now guards. **The sibling pin must survive unchanged**:
    `test_an_order_initialized_on_the_same_topic_is_ignored_silently` (`:417-428`) —
    `OrderInitialized` stays a silent ignore **as a log record**; Task 3.2 harvests its `quantity`
    into observer state, which the pin's `assert logs == []` cannot see and survives (it is
    pre-submission, local, and `order.submitted` already marks lifecycle start).
  - [x] 2.2 New per-type tests, one record each, fields per this contract (every record also
    carries `strategy_id` — the 3.2 review's two-strategies-on-one-instrument lesson — and
    `ts_event` as the venue-side nanos; `reconciliation=True` included only when the event's flag
    is **True** — every event exposes the property (Task 1.3), so "carries it" means the value,
    not the attribute; a False flag emits no key, the absent-latency-field pattern):
    - `order.accepted`: `venue_order_id` (the acknowledgement identity, AC #5).
    - `order.canceled` / `order.expired`: `venue_order_id` when present — both nullable on these
      events (`model/events/order.pyx:2271-2282, 2541-2552`); log what exists, never invent.
    - `order.filled`: `fill_qty` (= `str(event.last_qty)`), `cum_qty` (derived, emitted as a
      string — Task 3.2), `order_qty` when known (the Task 3.2 harvest), `last_px`, `commission`,
      `currency`, `trade_id`, `venue_order_id`, and `position_id` when present. The commission is
      the real broker charge — the value 3.2's AC #7 has been waiting to see.
    - `order.denied`: `reason` — deliberately named `reason`, NOT `venue_reason`: a denial is
      local (risk/exec engine, `risk/engine.pyx:903` / `execution/engine.pyx:898`), no venue was
      involved, and an operator grepping `venue_reason` must find only venue-sourced rejections.
  - [x] 2.3 The NFR26 anti-field test: for EVERY emitted record type (all six new + the existing
    `order.submitted`), assert `"account_id" not in record` — `account_id` rides on every
    venue-sourced order event (`OrderDenied`'s property returns hardcoded `None`,
    `order.pyx:781`) and a naive field dump would leak it. Parametrize the scan from
    `EMITTED_ORDER_EVENTS` (Task 3.3's exported enumeration) and pin that enumeration as an
    **exact set** in its own test — the `ORDER_CREATING_METHODS` membership-pin discipline — so a
    seventh emitted type cannot appear without joining the scan, and a dropped name goes red.
  - [x] 2.4 Partial-fill series test (AC #1's representation): two `OrderFilled` events for one
    `client_order_id` (`TestEventStubs.order_filled` takes `last_qty` — build 30 of 100, then 70)
    produce two `order.filled` records with `fill_qty` `"30"`/`"70"` and `cum_qty` `"30"`/`"100"`
    (string forms — the Task 3.2 emission contract). A different `client_order_id` accumulates
    independently — fixture trap, measured: two `TestExecStubs.market_order(...)` calls yield the
    SAME frozen `client_order_id`; pass an explicit `client_order_id=ClientOrderId(...)` on the
    second order or the "independent" order is the same order.
  - [x] 2.5 Partial-fill-then-cancel (a real venue sequence — remainder canceled):
    `OrderFilled(30) → OrderCanceled` emits the `order.canceled` record and the prior
    `order.filled` record stands; then — the late-fill race, a fill can cross the cancel ack — a
    subsequent `OrderFilled(20)` for the same `client_order_id` logs `cum_qty` `"50"`, not
    `"20"` (Task 3.2's retain-on-fills prune rule, observable from outside the observer).
  - [x] 2.6 Unknown-type silence pin: `OrderUpdated` (stub `:262`) or `OrderPendingCancel` produces
    no record — the dispatch is a closed set, not a default-log; Story 3.3 owns exactly the six
    states plus denied, and no modify/cancel-request path exists in this repo to make the others
    meaningful.
  - [x] 2.7 Containment extension: a malformed `OrderFilled` (e.g. a stub with `last_qty` access
    raising) is contained by the existing `order.observer_failed` path
    (`live_order_path.py:324-330`) — never raises (a raise re-enters `publish_c`, which invokes
    handlers with NO try — `common/component.pyx:2757` — the `os._exit(1)` failure mode).
  - [x] 2.8 Drive-by while in the file: the comment at `test_live_order_path.py:55` claims the
    module imports `TestLiveNode` from `tests.component.doubles` — it does not (that import lives
    in `test_session_runner_order_path.py:24`); correct the stale comment.
- [x] Task 3: GREEN — extend `OrderEventObserver` in `src/core/live_order_path.py` (AC: #1, #2, #3)
  - [x] 3.1 Replace the single-name filter (`:321-322`) with a class-name dispatch map
    (`{"OrderSubmitted": ..., "OrderAccepted": ..., ...}`) keeping the established
    string-comparison pattern — **no Nautilus imports added** (the module has none today; keep it
    framework-free). Unknown names return silently. All dispatch stays inside
    `handle_order_event`'s existing `try` (`:320`).
  - [x] 3.2 The accumulator — derivation, rendering, harvest, and pruning, all decided here:
    - **Derive**: accumulate `event.last_qty.as_decimal()` (measured present,
      `model/objects.pyx:543`, returns `Decimal`) per `str(event.client_order_id)` in a new
      observer dict.
    - **Emit `cum_qty` as `str(cum)`** — consistent with `fill_qty = str(event.last_qty)`. A raw
      `Decimal` reaches the file transcript as the literal `"Decimal('30')"` (the JSON renderer's
      fallback, `src/utils/logging.py:94`) — a defect NO component test can see, because
      `capture_logs()` hands tests raw objects; Tasks 2.4/4.1 assert the string forms so the
      contract is at least pinned where tests can reach it.
    - **Harvest `OrderInitialized.quantity` into the same map** (state only, NO log record — the
      `:417-428` silence pin survives; confirm the property read in Task 1.2) so `order.filled`
      carries `order_qty` when known and fill-path finality (`cum_qty == order_qty`) is readable
      from the transcript — without it, no event carries the order's total and a completed order
      is indistinguishable from one still working (AC #5's "final state" clause). When
      `OrderInitialized` was never seen (a reconciliation-sourced order), `order_qty` is absent —
      log what exists, never invent.
    - **Prune**: on `rejected`/`denied`, always (no fills can exist); on completion
      (`cum_qty >= order_qty`, when known); on `canceled`/`expired`, ONLY an entry with no
      accumulated fills — a fill-bearing entry is RETAINED, because a late fill can cross the
      cancel ack and pruning would then log a `cum_qty` counting the late fill alone (a false
      statement in the NFR21 record — Task 2.5 pins the correct behaviour).
    - **Disclose in the class docstring**: (a) retained fill-bearing entries for canceled orders
      whose late fill never comes, and unknown-`order_qty` fill entries, live until session end —
      bounded by orders-touched-per-run (joins the `_last_bar` unpruned-dict deferral,
      `deferred-work.md:2153-2155`); (b) **cum_qty resets across a process restart** — a fill
      arriving after resume for an order partially filled before the stop logs `cum_qty` counting
      post-restart fills only. Working orders across restarts are Story 3.4/Epic 4's subject;
      disclosed, not solved, here.
  - [x] 3.3 New event-name constants beside `SUBMITTED_EVENT` (`:117`), same comment discipline
    (`:113-115`): `ACCEPTED_EVENT`, `REJECTED_EVENT`, `FILLED_EVENT`, `CANCELED_EVENT`,
    `EXPIRED_EVENT`, `DENIED_EVENT`. Emission via the established shape —
    `self._log.info(<EVENT>, **fields)` (`:361` precedent). Severity: `info` for
    accepted/filled/canceled/expired; `warning` for rejected/denied (operator-actionable, but NOT
    `error` — a rejection is a normal venue answer, and 3.7 owns escalation/visibility). Also
    export **`EMITTED_ORDER_EVENTS`** — an importable tuple (or the dispatch map's keys made
    public) enumerating every event name the observer can emit; Task 2.3 parametrizes from it and
    pins it as an exact set. Extend CLAUDE.md's "Membership-pinned lists" Anti-Patterns entry in
    the same commit (the `ORDER_CREATING_METHODS` precedent).
  - [x] 3.4 Update BOTH stale scope notes: the module docstring's "…or anything about a fill, a
    rejection or a lifecycle beyond ``OrderSubmitted`` (Story 3.3)" at `live_order_path.py:15-16`,
    AND the class docstring's "ignores anything but ``OrderSubmitted``" at `:257-258`.
    `note_bar`/latency behaviour stays untouched — latency is submit-side only; no new record
    carries `bar_close_to_submit_ms`.
- [x] Task 4: AC #5 reconstruction tests (AC: #5)
  - [x] 4.1 Lifecycle-reconstruction test (new class, e.g. `TestLifecycleReconstruction`): publish
    `OrderInitialized → OrderSubmitted → OrderAccepted → OrderFilled(30) → OrderFilled(70)` for
    one order through `handle_order_event` inside one `capture_logs()`; assert exactly four
    records (the `OrderInitialized` harvests state, emits nothing), sharing one
    `client_order_id`, in arrival order, the final `order.filled` showing
    `cum_qty == order_qty == "100"` (string forms — fill-path finality readable from the record
    alone), and `venue_order_id` appearing from `order.accepted` onward. Then the rejection path:
    `OrderSubmitted → OrderRejected` reconstructs submission + terminal state + `venue_reason`.
  - [x] 4.2 Reconciliation-distinguishability pin: an `OrderFilled` built with
    `reconciliation=True` produces an `order.filled` record carrying `reconciliation=True`, and a
    normal fill's record carries NO `reconciliation` key (Task 2.2's emit-only-when-True rule,
    asserted here) — the transcript reader's defence against counting an inferred reconciliation
    fill as a live fill (the exact confusion recorded at
    `docs/qa/phase3-live-verification.md:757`). Fixture fact, measured:
    `TestEventStubs.order_filled` has NO `reconciliation` parameter (`TypeError` if passed) —
    construct the `OrderFilled` directly (ctor `model/events/order.pyx:4540-4560`,
    `reconciliation` kwarg; the test file will need `Money`/`Currency`/`OrderSide`/`OrderType`/
    `LiquiditySide` imports for the 19-parameter build).
- [x] Task 5: Non-change evidence contracts (AC: #4) — verified, not assumed
  - [x] 5.1 `git diff --stat` empty for: `src/core/live_session_runner.py` (the existing
    subscription already delivers every order event — no wiring change), `src/core/strategies/`
    (AC #4's mode-agnostic contract), all four guard-list test files' list contents
    (`live_order_path.py` is already in `NODE_FACING_MODULES`
    (`test_live_node_never_exits.py:59`) and `TestImportPurity.MODULES`
    (`test_session_runner_phases.py:1249`), already excluded with recorded reasons from
    `STOP_PATH_MODULES` (`test_live_stop_path_is_inert.py:48-58`) and `NEW_OR_MODIFIED_FOR_STOP`
    (`:433-443`), and `LIVE_MODULE_GLOBS` globs it automatically). Record each in the Dev Agent
    Record.
  - [x] 5.2 AR36 sweep: the six new event names carry no forbidden stem
    (`pause|halt|kill|close|finalize` — `test_live_stop_path_is_inert.py:470`); the module is not
    in `NEW_OR_MODIFIED_FOR_STOP` so the scan does not run against it, but honour the spirit —
    no forbidden stem in any new operator-facing string. The existing `order.*` AR36 exemption
    comment (`:445-456`) covers `method=close_position` in suppression records only; nothing new
    may lean on it.
  - [x] 5.3 Size budget, decided BEFORE the edit (CLAUDE.md D4): `live_order_path.py` is 361 raw
    lines / 96 executable statements today; the dispatch + field builders + accumulator
    (harvest, rendering, pruning) are budgeted ~+120-170 raw / ~+50 statements → ~480-530 raw /
    ~145 statements. The cap is measured on executable statements, so this fits with wide margin;
    if raw lines cross 500, disclose the figure in the Dev Agent Record — do NOT split the module
    (Anti-Patterns: guard-list rot; this module is on two hand-maintained lists and
    excluded-with-reasons from two more).
- [x] Task 6: Live verification — the lifecycle records in a real transcript (AC: #1, #2, #3, #5) — operator procedure, NFR33 tier
  - [x] 6.1 This story's live evidence and Story 3.2's outstanding AC #7 close on the SAME run:
    once 3.3's automated tiers land, the next `scripts/diagnostics/run_p7_position.sh` re-run
    inside RTH (preconditions verbatim from 3.2 Task 8.1: no competing IBKR login — mobile app and
    client portal included; the bare non-compose Gateway, never the `READ_ONLY_API=yes` compose
    one; Redis up; `sma_crossover`, never `sma_crossover_long_only`) produces a transcript whose
    fill now carries structured `order.accepted` and `order.filled` records with the real
    commission. **Land 3.3 before that run happens** so one RTH window closes both stories' live
    items; if 3.2's run has already happened by dev time, re-run the same procedure (the
    `p7-position-test` session is reusable across stop/start cycles).
  - [x] 6.2 Record in `docs/qa/phase3-live-verification.md` (dated, in P7's result log, alongside
    3.2's entry): the `order.accepted` record with `venue_order_id`; every `order.filled` with
    `fill_qty`/`cum_qty`/`commission`; confirmation that no record carries `account_id`; the
    162/10182/366 transcript grep (detection discipline; response ownership is Epic 4's).
  - [x] 6.3 If the market is closed or the Gateway unavailable when dev completes, the automated
    tiers gate the story into review; this task is then the explicit remaining item and the story
    must NOT be marked done until the records are observed live (the Epic 2 retro's standing
    rule — same sanctioned path as 3.2's Task 8.4).
- [x] Task 7: Mutation sweep from the AC list + closeout (AC: all)
  - [x] 7.1 Minimum one mutation per AC, scripted break→observe-red→revert, naming the killing
    test each time: (M1/AC1) delete the `OrderCanceled` dispatch entry; (M2/AC1) delete the
    `OrderExpired` entry; (M3/AC2) emit `order.cancelled` (double-l) instead of the pinned
    spelling; (M4/AC2) drop `cum_qty` from the `order.filled` fields; (M5/AC3) replace
    `venue_reason`'s value with a generic literal (`"order rejected"`); (M6/AC4) delete the
    runner's `ORDER_EVENTS_TOPIC` subscribe call site — 3.2's M7 pins must go red (proves the
    delivery path 3.3 rides is still guarded, without this story writing new wiring);
    (M7/AC5) make the accumulator reset on every fill (`cum = fill`) — the partial-series and
    reconstruction tests must both go red; (M8/NFR26) add `account_id=str(event.account_id)` to
    one field builder — the Task 2.3 anti-field scan must go red; (M9) remove the
    `except Exception` from `handle_order_event` — the containment tests must go red;
    (M10/AC1) prune the accumulator unconditionally on `OrderCanceled` (delete the
    retain-on-fills rule) — the Task 2.5 late-fill test must go red; (M11/AC5) drop the
    `OrderInitialized.quantity` harvest — Task 4.1's finality assertion must go red. When a
    mutation stays green, inspect the FIXTURE before concluding the guard is missing (the 3.2 M4
    strawman lesson: a fixture whose values cannot distinguish mutant from original kills
    nothing — give stub events distinctive quantities/reasons).
  - [x] 7.2 Clause matrix (the 3.1/3.2 method): decompose each AC into distinct obligations,
    mutate each in isolation, record which named test fires, in this story file.
  - [x] 7.3 Full gates: `make format && make lint && make typecheck`; unit, component,
    integration `--forked`, e2e; Epic 1 acceptance sweep. Baselines at story creation:
    **unit 2419 · component 1353/16 skipped · integration 281/2 skipped · e2e 1 · Epic 1 sweep
    43/43** (post-3.2-review figures; head `7131afb`, tree clean).
  - [x] 7.4 Update `sprint-status.yaml`; `deferred-work.md` only if a new deferral is created
    (none is owed — the silent-suppression owner gap at `deferred-work.md:2195-2205` is
    explicitly NOT this story's; see Scope boundaries).

## Dev Notes

### The partial-fill trap — read before designing anything

The epic AC names six states; Nautilus has events for five of them. There is **no
`OrderPartiallyFilled` event class** in the installed 1.220.0 wheel (grep: zero hits across
`model/events/order.pyx`, `order.pxd`, `model/events/__init__.py`). A partial fill is an ordinary
`OrderFilled`; the *order object* transitions to `PARTIALLY_FILLED` only in its own FSM
(`model/orders/base.pyx:1116-1120`), and the event stream never says which. Do not invent an
`order.partially_filled` event, do not try to read order status (events have no `.order` accessor
and the observer has no cache) — the AR41-mandated `fill_qty`/`cum_qty` pair on `order.filled` is
the representation, by design.

`cum_qty` must be **derived**: `OrderFilled` carries `last_qty` and `last_px` only ("not average
price" — `order.pyx:4513`); cumulative quantity lives on the `Order` object
(`model/orders/base.pxd:99-101`, mutated at `base.pyx:1144-1146`). The observer accumulates
`last_qty` per `client_order_id` (Decimal), the same in-observer-state pattern as `_last_bar`
(`live_order_path.py:301`). Two disclosed residuals (Task 3.2): unbounded-by-construction growth
on the fill side (bounded in practice by orders-filled-per-run), and reset-on-restart (Epic 4's
working-order resume is when that matters; until then no order spans a restart by design).

### The current module — extension points, exact

- Dispatch seam: `handle_order_event` at `live_order_path.py:318`; the filter to replace is
  `:321-322` (`if type(event).__name__ != "OrderSubmitted": return` — string comparison, no
  Nautilus import; keep that property). The containing `try` at `:320` and the
  `order.observer_failed` handler at `:324-330` already give every new branch its containment.
- Field-builder precedent: `_log_submitted` at `:332-361` — builds a dict, emits
  `self._log.info(SUBMITTED_EVENT, **fields)` at `:361`. New builders follow it. Latency logic
  (`:350-360`) is submit-only; new records never carry `bar_close_to_submit_ms`.
- Event-name constants: `SUPPRESSED_EVENT`/`SUBMITTED_EVENT` at `:116-117` under the AR41/AR36
  comment at `:113-115`.
- The logger arrives pre-bound with `session_id` (`__init__` at `:295`; runner hands its bound
  `self._log` at `live_session_runner.py:561`). The observer never binds, never creates a logger
  (docstring contract at `:270-274`) — new records get `session_id` for free, and every new field
  set must therefore add `strategy_id` explicitly (events carry it; the 3.2 review's patch #1
  lesson: with two strategies on one instrument, a record without `strategy_id` is ambiguous).

### Measured Nautilus 1.220.0 facts — cite, don't re-derive

All measured against the installed wheel (`.venv/lib/python3.11/site-packages/nautilus_trader/`);
"Nautilus docs are not evidence" stands (epics.md:784-786).

- **Event classes** (`model/events/order.pyx`): `OrderSubmitted` :1407, `OrderAccepted` :1664,
  `OrderRejected` :1940, `OrderCanceled` :2243, `OrderExpired` :2513, `OrderFilled` :4485,
  `OrderDenied` :635. Also present and deliberately NOT logged: `OrderInitialized` :197 (stays the
  silent-ignore pin), `OrderEmulated`/`OrderReleased`/`OrderTriggered`/`OrderPendingUpdate`/
  `OrderPendingCancel`/`OrderModifyRejected`/`OrderCancelRejected`/`OrderUpdated` (no modify or
  cancel-request path exists in this repo; Task 2.5 pins the closed set).
- **`OrderRejected.reason`**: `str`, property `:2107`; ctor normalises `reason or "None"` at
  `:1991` — an absent reason arrives as the literal `"None"`, never dropped. `due_post_only`:
  `bool`, property `:2131`.
- **`OrderFilled` fields** (`order.pxd:340-359`): `trade_id`, `position_id` (nullable),
  `order_side`, `order_type`, `last_qty`, `last_px` ("not average price" —
  `order.pyx:4513-4514`), `currency`, `commission` (Money), `liquidity_side`, `info` — plus the
  identifier properties. `order_side`/`liquidity_side` are C enums; rendering them readably needs
  Nautilus helper imports the module doesn't have — **omit both** from the field contract (no AC
  needs them) rather than log raw ints or add imports. `Quantity.as_decimal()` exists
  (`model/objects.pyx:543`, returns `Decimal`; also on `Price` `:977` and `Money` `:1338`) —
  Task 1.2's confirmation is expected to pass.
- **`OrderCanceled`/`OrderExpired`**: identical 10-field shape; `venue_order_id` and `account_id`
  both nullable (`:2271-2282`, `:2541-2552`); neither carries a reason.
- **`OrderDenied`**: `reason` property `:784`; generated by the risk engine
  (`risk/engine.pyx:903`, sent to `ExecEngine.process` at `:913`), the exec engine
  (`execution/engine.pyx:898`, published directly at `:910-913`), and
  `Strategy._generate_order_denied` (`trading/strategy.pyx:1720-1730`). It reaches the same topic —
  logging it is this story's, per the 3.2 review's dismissal-with-ownership.
- **Topic**: every order event publishes on `events.order.{strategy_id}`
  (`execution/engine.pyx:911, 1176, 1248`); the runner's `events.order*` subscription
  (`live_session_runner.py:563`) covers all of them. Wildcard-subscriber precedent inside Nautilus
  itself: `portfolio/portfolio.pyx:187`, `risk/engine.pyx:188`.
- **`OrderSubmitted.venue_order_id` is hardcoded `None`** (`order.pyx:1534-1543`) — acknowledgement
  identity first exists on `OrderAccepted` (AC #5's "acknowledgement").
- **Msgbus handlers run with no `try`** (`common/component.pyx:2757`) — a raise from
  `handle_order_event` is the `os._exit(1)`-with-zero-output failure mode. Everything stays inside
  the existing containment.
- **Test stubs** (`test_kit/stubs/events.py`): `order_accepted` :208, `order_rejected` :227,
  `order_filled` :302 (takes `order`, `instrument`, optional `last_qty`/`last_px` — the
  partial-series lever), `order_canceled` :362, `order_expired` :379. **No `order_denied` stub** —
  build the event directly (ctor `order.pyx:661-669`; note it takes `ts_init` only, no `ts_event`).

### Scope boundaries — what this story must NOT build

- **No position events.** `events.position.*` is not subscribed and stays that way —
  `PositionOpened/Changed/Closed` are Stories 3.5/3.6's input (the trade recorder). AC #4's
  mention of `on_position_closed` is the epic illustrating "Nautilus's own handlers", not a scope
  grant.
- **No DB writes, no orders table.** NFR21's evidence is the log stream (AC #5 reconstructs "from
  logs"); the PRD's "represented and **persisted**" (prd.md:436) resolves to trade persistence,
  which is Story 3.6's. Head stays `b7c419e2a3d8`; no migration.
- **No retry, no backoff, no query-on-ambiguity machinery** — Story 3.4/AR24. This story only
  *records* what happens; it never reacts.
- **No rejection-visibility surfacing** — Story 3.7 owns FR49/NFR24 (status/CLI distinguishability
  of a rejected-on-every-order session). This story's `order.rejected` record is 3.7's raw
  material, not its deliverable. No CLI changes, no `live_session_health` changes.
- **No suppression feedback.** The silent-suppression owner gap (`deferred-work.md:2195-2205`)
  explicitly notes 3.3 owns *rejection* semantics, not suppression — do not absorb it here.
- **No accumulator persistence, to any store.** The restart-reset residual (Task 3.2) is
  disclosed, not solved — persisting observer state to Redis, Postgres, or disk is
  Epic 4-adjacent machinery no AC asks for, and "no DB writes" alone would not fence the Redis
  temptation (the project already runs Redis for session cache).
- **No strategy diffs** (`src/core/strategies/` zero-diff — AC #4 evidence contract), **no runner
  diffs** (Task 5.1), **no guard-list edits** (Task 5.1), **no new subscriptions** (the existing
  two are recorded in `self._subscriptions` and cancelled by `unsubscribe_runner_topics` —
  `live_session_node.py:296-328`; nothing new to record).
- **No new exception names**, AR44's untouched list applies (`src/api/**`, templates,
  `backtest_query.py`, comparison views, `BacktestOrchestrator`).

### Hazards (every one bit a previous story)

1. **A raise from a msgbus handler ends the process with zero output** (`publish_c` has no try —
   `common/component.pyx:2757`). Every new dispatch branch lives inside `handle_order_event`'s
   existing `try`.
2. **`account_id` is on every venue-sourced order event** (`OrderDenied`'s property returns
   hardcoded `None` — `order.pyx:781`). NFR26's letter is "never logged in full" (`epics.md:150`);
   this story pins the stricter never-at-all, since no order record needs even a masked account.
   Task 2.3's anti-field scan is the guard; never field-dump an event.
3. **No `freezegun`** (inject time); **`capture_logs()` is banned near `session_id` context** —
   BUT the existing `test_live_order_path.py` uses `capture_logs()` against a locally-bound
   `structlog.get_logger("test").bind(session_id=...)` (`:378`), which is the sanctioned shape —
   the ban is on capturing near the *runner's* contextvars binding. Follow the file's own
   established pattern.
4. **The C-logging guard fixture is order-dependent** (`test_live_order_path.py:73-83`, known
   deferral) — construct events via stubs, which do not touch C logging (measured by 3.2); do not
   build a `TradingNode` or `BacktestEngine` in this file.
5. **The auto-linter**: F401 is unfixable-but-blocking; add each new constant and its use in the
   same edit; the commit gate inspects staged files only.
6. **Size caps** measured on executable statements; budget BEFORE the edit (Task 5.3); do not
   split `live_order_path.py`.
7. **Stub signatures vary, and stub fixtures are indistinct by default** —
   `TestEventStubs.order_filled` requires `instrument`; `order_rejected` HARDCODES
   `reason="ORDER_REJECTED"` (`test_kit/stubs/events.py:238`, no override) — build `OrderRejected`
   (and `OrderDenied`) directly wherever the reason must be distinctive. Measured stub-fill
   quirks: two stub fills for one order share one `trade_id` (derived from the frozen
   `client_order_id`) and the stub computes commission on `order.quantity`, not `last_qty` —
   construct `OrderFilled` directly when `trade_id` must distinguish fills, and never use
   commission as a distinguishing field in component tests. Give every fixture **distinctive**
   reasons/quantities so mutations cannot survive on indistinct values (the 3.2 M4 strawman
   lesson).
8. **`StrategyRegistry` is process-global**; never depend on a `custom/` strategy in tests.
9. **Deferred-work citations into `live_order_path.py` are stale** — items at
   `deferred-work.md:2148-2181` cite pre-review line numbers (`:135/:225/:233/:244` etc.); the
   current file's equivalents are `:177/:301/:311/:325`. Do not "fix" the old citations; they
   date-stamp the review.

### Testing standards summary

- Tier: everything here is **component** (real Nautilus event objects + stubs, no engine, no
  broker — NFR32); the live transcript is the only NFR33-tier evidence (Task 6). No new unit or
  integration tests are expected.
- Markers on every test; TDD Red first with RED evidence recorded (the 3.1/3.2 pattern).
- Extend `tests/component/core/test_live_order_path.py` (598 lines, 23 tests / 24 cases today) —
  do not create a new file for observer behaviour; the reconstruction class (Task 4) lives there
  too. `test_session_runner_order_path.py` (11 tests) should need **zero changes** — if it goes
  red, the non-change contracts are being violated.
- The wiring-pin discipline is inherited, not re-built: the runner→observer delivery path is
  already pinned by 3.2's M7 tests (`test_the_runner_wires_the_order_event_observer_before_trading`
  and the `test_session_runner_phases.py` topic/timeline pins). M6 re-runs that mutation to prove
  the pins still hold rather than writing new wiring tests.
- Known residual to state, not hide: no automated test drives a REAL venue's event sequence — the
  stubs are Nautilus's own, and the live transcript (Task 6) is the only real-venue evidence.
  Same recorded limit as 3.1/3.2.
- Baselines at story creation: unit 2419 · component 1353/16 skipped · integration 281/2 skipped ·
  e2e 1 · Epic 1 sweep 43/43. Head `7131afb`, tree clean.

### Project Structure Notes

- Modified: `src/core/live_order_path.py` (dispatch + field builders + accumulator + docstring),
  `tests/component/core/test_live_order_path.py` (inverted pin + new tests),
  `CLAUDE.md` (membership-pinned-lists entry gains `EMITTED_ORDER_EVENTS` — Task 3.3),
  `docs/qa/phase3-live-verification.md` (Task 6.2, when the live run happens),
  `_bmad-output/implementation-artifacts/sprint-status.yaml`, this story file.
- NOT modified (evidence contracts, Task 5.1): `src/core/live_session_runner.py`,
  `src/core/live_connection_monitor.py`, `src/core/live_strategy_guard.py`,
  `src/core/strategies/**`, guard-list contents in all four guard test files,
  `src/cli/**`, `src/db/**`, `src/services/**`, `alembic/**`, `src/models/**`,
  `tests/component/core/test_session_runner_order_path.py`.
- Naming: six new event names in the sanctioned `order.*` namespace (AR41), all clear of AR36
  stems; `order.canceled` single-l pinned (AC #2); `reason` vs `venue_reason` distinction pinned
  (Task 2.2).
- Commit shape: one `feat(live):` commit carrying src + tests + BMAD artifacts together (the
  3.1/3.2 precedent); subject is an operator-outcome sentence; no AI references.

### References

- Story source: `_bmad-output/planning-artifacts/epics.md:1245-1278`; epic context `:1105-1114`
- Requirements: FR24 `epics.md:66`; FR26 `:68`; FR31 `:73`; NFR13 `:128`; NFR21 `:142`; NFR26
  `:150`; NFR32/33 `:162-163`; AR26 `:214`; AR36 `:236`; AR40 `:240`; AR41 `:241-243`
- Architecture: D7 order-path policy `architecture.md:286-296`; event naming `:435-440`; handler
  consumption `:441-443`; testing split `:413-416, :470-472`
- PRD: execution-correctness domain rules `prd.md:431-450` (lifecycle `:436-438`, rejections
  `:445-447`); traceability `:972-974`
- Previous story: `_bmad-output/implementation-artifacts/3-2-submit-a-strategys-orders-to-the-broker.md`
  (esp. "Review Findings" `:145-226`, the observer design `:113-117` tasks, scope fence `:338-341`)
- Current module: `src/core/live_order_path.py` (`ORDER_EVENTS_TOPIC` `:122`, observer `:247-361`);
  runner wiring `src/core/live_session_runner.py:542-564` (`_subscribe` `:566-577`);
  unsubscribe `src/core/live_session_node.py:296-328`
- Wheel: `model/events/order.pyx` (classes as cited above); `model/orders/base.pyx:1116-1146`;
  `execution/engine.pyx:911, 1176, 1248`; `execution/client.pyx:336-827` (generators);
  `trading/strategy.pyx:1615-1700` (handler cascade); `common/component.pyx:2757`;
  `test_kit/stubs/events.py`
- Guard lists: `tests/unit/core/test_live_node_never_exits.py:42-62`;
  `tests/unit/core/test_live_stop_path_is_inert.py:38-74, :414-506`;
  `tests/component/core/test_session_runner_phases.py:1214-1253`;
  `tests/component/core/test_live_dependency_invariance.py:39-45`
- Live procedure: `scripts/diagnostics/run_p7_position.sh`;
  `docs/qa/phase3-live-verification.md:655-800` (the reconciliation-inferred-fill lesson `:757`);
  3.2's Task 8 preconditions (its story file, Task 8.1)
- Deferred work: `deferred-work.md:2134-2205` (the 3.2 review section; suppression owner gap
  `:2195-2205`; stale-citation warning above)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5

### Debug Log References

Task 1 fresh-interpreter probe (real `MessageBus`, no C logging; script run under `uv run python`,
not pytest): published one of each `OrderAccepted`/`OrderRejected`/`OrderCanceled`/`OrderExpired`/
`OrderFilled`/`OrderDenied` on `events.order.{strategy_id}` — a plain `events.order*` subscriber
received all six (1.1 confirmed). Renderings measured (1.2): `str(event.last_qty)` → `"100"`;
`event.last_qty.as_decimal()` → `Decimal('100')`; `str(event.last_px)` → `"1.00"`;
`str(event.commission)` → `"0.00 USD"`; `str(event.currency)` → `"USD"`;
`str(event.venue_order_id)` → `"1"`; `str(event.trade_id)` → stub-derived
(`"E-...-001-001-1"`); `OrderInitialized.quantity` → `100` (str `"100"`). `TestEventStubs
.order_rejected` confirmed hardcoding `reason="ORDER_REJECTED"` with no override — rejection/denial
tests build events directly, as the story anticipated. `OrderCanceled`/`OrderExpired.venue_order_id`
confirmed `None` on an order with no applied `OrderAccepted` (nullable, as cited).
`reconciliation` read `False` on all six without error (1.3 confirmed; property exists on every
type, including the two — `OrderSubmitted`, `OrderDenied` — with no ctor kwarg for it).
`account_id` present (`"SIM-000"`) on five events, hardcoded `None` only on `OrderDenied` — matches
`order.pyx:781`. Confirmed the fixture trap: two independent `TestExecStubs.market_order(...)`
calls (no explicit `client_order_id`) returned the SAME frozen ID — Task 2.4's
"accumulates independently" test passes an explicit override, as the story warned.

Mutation sweep (Task 7.1), each applied to the working tree, run against
`tests/component/core/test_live_order_path.py` (plus the two named `test_session_runner_phases.py`
tests for M6), observed red, then reverted — `diff` against a pre-sweep copy of both touched
production files confirmed byte-exact after every revert:

| # | AC | Mutation | Killed by |
|---|---|---|---|
| M1 | AC1 | delete the `OrderCanceled` dispatch entry | `test_order_canceled_omits_venue_order_id_when_absent`, `test_order_canceled_logs_venue_order_id_when_present`, `test_the_record_carries_no_account_id_field[order.canceled]` |
| M2 | AC1 | delete the `OrderExpired` dispatch entry | `test_order_expired_omits_venue_order_id_when_absent`, `test_the_record_carries_no_account_id_field[order.expired]` |
| M3 | AC2 | emit `order.cancelled` (double-l) | nothing — **survived** against the originally-planned test set, because every assertion compared a captured record against the imported `CANCELED_EVENT` constant, so the mutated literal and the assertion drifted together. Fixed by adding `TestEventNameLiteralsArePinned.test_the_literal_spellings_are_exact`, which asserts each constant against its literal string; re-run confirmed red |
| M4 | AC2 | drop `cum_qty` from `order.filled`'s fields | `test_order_filled_logs_the_full_field_contract`, `test_a_partial_fill_series_produces_a_rising_cum_qty`, `test_a_different_client_order_id_accumulates_independently`, `test_a_late_fill_across_the_cancel_ack_still_accumulates`, `test_the_fill_path_reconstructs_end_to_end_from_records_alone` |
| M5 | AC3 | replace `venue_reason`'s value with `"order rejected"` | `test_an_order_rejected_is_logged_with_the_venue_reason_and_never_raises`, `test_the_rejection_path_reconstructs_submission_and_terminal_state` — both use a distinctive reason, per the 3.2 M4 strawman lesson |
| M6 | AC4 | delete the runner's `ORDER_EVENTS_TOPIC` subscribe call site | `test_the_runner_wires_the_order_event_observer_before_trading`, `test_subscribe_watches_the_bar_topic_on_the_message_bus`, `test_note_bar_is_subscribed_before_any_strategy_is_added` — 3.2's own wiring pins, unmodified by this story, still guard the path 3.3 rides |
| M7 | AC5 | reset the accumulator on every fill (`cum = fill` not `cum += fill`) | `test_a_partial_fill_series_produces_a_rising_cum_qty`, `test_a_late_fill_across_the_cancel_ack_still_accumulates`, `test_the_fill_path_reconstructs_end_to_end_from_records_alone` |
| M8 | NFR26 | add `account_id=str(event.account_id)` to `_log_accepted`'s fields | `test_the_record_carries_no_account_id_field[order.accepted]` |
| M9 | — | remove `except Exception` from `handle_order_event` | `test_handle_order_event_never_raises_on_a_malformed_submitted_event`, `test_a_malformed_filled_event_is_contained`, `test_a_malformed_rejected_event_is_contained` |
| M10 | AC1 | prune the accumulator unconditionally on `OrderCanceled` | `test_a_late_fill_across_the_cancel_ack_still_accumulates` |
| M11 | AC5 | drop the `OrderInitialized.quantity` harvest | `test_the_fill_path_reconstructs_end_to_end_from_records_alone` (the `order_qty`/finality assertion) |

M3 is the one mutation that did not die on the first attempt — recorded per the Epic 2 retro's
standing instruction ("when a mutation goes green, inspect the fixture before concluding the guard
is missing"): every existing assertion compared a captured record's `event` key against the
imported `CANCELED_EVENT`/`ACCEPTED_EVENT`/etc. constant rather than a literal string, so mutating
the constant's value silently mutated the test's expectation along with it — an AC #2 property
("uses the dotted past-tense event names") had no test that could fail on a wrong spelling.
`TestEventNameLiteralsArePinned` closes this for all six new constants at once, added mid-sweep and
verified red under M3 before being kept.

Task 7.3 full gates (run after the mutation sweep, tree confirmed clean by diff against the
pre-mutation backup): `make format` 491 files unchanged; `make lint` clean; `make typecheck` clean,
104 files; unit 2419 (unchanged — no new unit tests, per Testing Standards); component 1378/16
skipped (was 1353/16 — +25: 24 new dispatch/reconstruction/membership tests plus the M3-driven
literal-pin test); integration `--forked` 281/2 skipped (unchanged); e2e 1 (unchanged); Epic 1
acceptance sweep 40/40 (the story's own baseline note recorded both `40/40` and, later after 3.2's
review, `43/43` for this same sweep — today's measured, complete, all-passing run is `40/40`,
matching 3.2's own dev-story-session figure; the discrepancy predates this story and is not
something this diff touches, so it is reported as measured rather than reconciled).

One integration failure surfaced and was fixed, not anticipated by the story: `test_epic1_ac_node
.py::test_no_new_dependency_was_added_for_the_live_path` scans every live-path module's imports
against a hand-maintained `_STDLIB_AND_FIRST_PARTY` allowlist (deliberately not
`sys.stdlib_module_names`, per that file's own repeated rationale for `socket`/`re`/`json`/etc.).
`live_order_path.py`'s new `from decimal import Decimal` — needed because `OrderFilled` carries no
cumulative quantity, so `cum_qty` is derived by summing `Quantity.as_decimal()` — is stdlib but
undeclared in that list; added `"decimal"` by hand with the same discipline the file already
documents for its other entries.

### Completion Notes List

All 5 ACs satisfied. `OrderEventObserver.handle_order_event` now dispatches by class name across
the order's full lifecycle — `OrderSubmitted` (unchanged), `OrderAccepted`, `OrderRejected`,
`OrderFilled`, `OrderCanceled`, `OrderExpired`, `OrderDenied` are each logged; `OrderInitialized` is
harvested silently (feeds the fill-completion accumulator, emits nothing — the `:417-428` pin
survives, now inverted-and-extended for `OrderRejected`); every other type is ignored, a closed set
per Task 2.6. AC #1's partial-fill representation is the `fill_qty`/`cum_qty` pair on `order.filled`
— no `order.partially_filled` name was added. AC #2's field/naming contract is pinned two ways: by
constant (existing pattern) and, after M3 surfaced the gap, by literal string
(`TestEventNameLiteralsArePinned`). AC #3's rejection reason is `str(event.reason)` verbatim,
containment preserved (the 3.2 pin inverted, not deleted). AC #4 holds on three evidence contracts,
all verified by empty `git diff --stat`, not assumed: `src/core/live_session_runner.py`,
`src/core/strategies/`, and the four guard-list test files' contents. AC #5's fill-path finality
(`cum_qty == order_qty`) and rejection-path terminal state are both proven by
`TestLifecycleReconstruction` reading captured records alone; the reconciliation-vs-live
distinguishability guard closes the exact confusion recorded at
`docs/qa/phase3-live-verification.md:757`.

Size budget (Task 5.3, disclosed per CLAUDE.md D4 rather than hidden): `live_order_path.py` measures
**556 raw lines / 166 executable statements** after this story (was 361 raw / 96 statements; budgeted
~480–530 raw / ~145 statements — raw lines landed above the budgeted range and above the 500-line
figure CLAUDE.md's cap names, executable statements landed close to budget and well under any
class/function-level cap). `OrderEventObserver` itself is 94 executable statements — under the
100-statement class cap with margin. Per CLAUDE.md D4 and the guard-list-rot Anti-Pattern, the module
is **not split**: it is already on two hand-maintained guard lists
(`NODE_FACING_MODULES`, `TestImportPurity.MODULES`) and excluded-with-recorded-reasons from two more
(`STOP_PATH_MODULES`, `NEW_OR_MODIFIED_FOR_STOP`), and a split would silently escape some of them —
the exact Story 2.6 precedent CLAUDE.md's Anti-Patterns entry exists to prevent.

AR36 sweep (Task 5.2): none of the six new event names or any new field name/value carries a
`pause`/`halt`/`kill`/`close`/`finalize` stem; the existing `close_position`/`close_all_positions`
occurrences are all pre-existing method names in docstrings/`_describe`, already covered by the
module's own exemption comment, and nothing new leans on it.

Task 6 (live verification) did not run: today, 2026-08-30, is a Sunday — the same market-closed
precondition Story 3.2's Task 8 hit. Per Task 6.3 and the Epic 2 retro's standing rule (the same
sanctioned path as 3.2's own Task 8.4), this gates the story into `review` without blocking on it.
The story explicitly may **not** move to `done` until a `scripts/diagnostics/run_p7_position.sh`
re-run inside RTH (no competing IBKR login, the bare non-compose Gateway, `sma_crossover`) produces
a transcript with structured `order.accepted`/`order.filled` records — the same run Story 3.2's
still-open AC #7 is waiting on, per Task 6.1's design.

### File List

- Modified: `src/core/live_order_path.py`
- Modified: `tests/component/core/test_live_order_path.py`
- Modified: `tests/integration/core/test_epic1_ac_node.py` (not anticipated by the story —
  `_STDLIB_AND_FIRST_PARTY` needed `"decimal"` added by hand; see Debug Log References)
- Modified: `CLAUDE.md` (Membership-pinned lists entry extended for `EMITTED_ORDER_EVENTS`, per
  Task 3.3)
- Modified: `_bmad-output/implementation-artifacts/sprint-status.yaml`
- Modified: `_bmad-output/implementation-artifacts/3-3-track-every-order-through-its-full-lifecycle.md`
  (this file)
- NOT modified (evidence contracts, Task 5.1, verified by empty `git diff --stat`):
  `src/core/live_session_runner.py`, `src/core/live_connection_monitor.py`,
  `src/core/live_strategy_guard.py`, `src/core/strategies/**`,
  `tests/unit/core/test_live_node_never_exits.py`, `tests/unit/core/test_live_stop_path_is_inert.py`,
  `tests/component/core/test_session_runner_phases.py`,
  `tests/component/core/test_live_dependency_invariance.py`,
  `tests/component/core/test_session_runner_order_path.py`, `src/cli/**`, `src/db/**`,
  `src/services/**`, `src/models/**`, `alembic/**`, `docs/qa/phase3-live-verification.md` (Task 6.2
  did not run — see Completion Notes)

## Change Log

| Date | Change |
|---|---|
| 2026-08-30 | Story 3.3 created from epics.md:1245-1278 (backlog → ready-for-dev). Central pre-resolved decisions, all from wheel measurement: no `OrderPartiallyFilled` event exists (the `fill_qty`/`cum_qty` pair on `order.filled` is the partial-fill representation, per AR41's own field mandate); `cum_qty` is derived in-observer (no event carries it, no cache access exists) and emitted as a string (a raw Decimal reaches the JSON transcript as `"Decimal('30')"` — invisible to `capture_logs` tests); `OrderInitialized.quantity` is harvested into observer state (never logged) so fill-path finality is readable from the transcript; pruning is fully decided including the late-fill-across-cancel retention rule; every order event already arrives on the existing `events.order*` subscription so the story needs zero new subscriptions, zero runner diffs, zero guard-list edits; `OrderDenied` is included (`reason`, not `venue_reason`) per the 3.2 review's dismissal-with-ownership; `order.canceled` single-l spelling pinned to the Nautilus class name; `account_id` rides every venue-sourced event and is fenced by an exact-set anti-field scan parametrized from a new exported `EMITTED_ORDER_EVENTS`. Live evidence rides the same pending P7 re-run as 3.2's open AC #7 — land 3.3 first so one RTH window closes both. Validated by two fresh-context agents (citation fact-check: 118 claims checked, 7 discrepancies corrected — among them `TestEventStubs.order_rejected` hardcoding its reason with no override; dev-simulation: 6 criticals, 6 enhancements, all applied — the sharpest two being the Decimal-rendering defect no component test can see, and the finality gap that made AC #5's "final state" clause unsatisfiable on the fill path as first drafted). |
| 2026-08-30 | Story 3.3 implemented: `ready-for-dev` → `in-progress` → `review`, same session as creation. All 7 tasks / 34 subtasks complete; all 5 ACs satisfied on the automated tiers. `handle_order_event` replaced its single-name filter with a class-name dispatch map covering `OrderInitialized` (silent harvest) plus the six new lifecycle events; a new per-`client_order_id` accumulator (`_orders`) derives `cum_qty`, harvests `order_qty` from `OrderInitialized`, and prunes per Task 3.2's exact rules (always on rejected/denied, on fill-completion, and on canceled/expired only when no fills were accumulated — a late fill can cross the cancel ack). Six new event-name constants plus an exported, membership-pinned `EMITTED_ORDER_EVENTS` tuple; CLAUDE.md's Membership-pinned-lists entry extended in the same commit. Eleven scripted mutations (one++ per AC), all killed; M3 (a wrong `order.canceled` spelling) survived on the first pass because every test compared a captured record against the imported constant rather than a literal string — fixed by adding a dedicated literal-value pin test, re-verified red, kept. One integration test not anticipated by the story needed a one-line fix: `test_epic1_ac_node.py`'s hand-maintained live-path stdlib allowlist needed `"decimal"` added, the same by-hand discipline it already documents for `socket`/`re`/`json`. Size disclosed per CLAUDE.md D4 rather than hidden: `live_order_path.py` is 556 raw lines / 166 executable statements (over the raw-line figure the cap names, within budget on the statement measure the cap is actually keyed to); not split, per the guard-list-rot Anti-Pattern — the module is already on two hand-maintained guard lists and excluded-with-reasons from two more. Gates: format 491 unchanged, lint clean, mypy clean 104 files, unit 2419 unchanged, component 1353 → 1378/16 skipped (+25), integration --forked 281/2 skipped unchanged, e2e 1 unchanged, Epic 1 sweep 40/40, `git diff --stat` empty for `live_session_runner.py`/`src/core/strategies/`/all four guard-list files. **STORY IS NOT DONE**: Task 6 (live verification) did not run — 2026-08-30 is a Sunday, market closed, the same precondition Story 3.2's Task 8 hit. Per Task 6.3 and the Epic 2 retro's standing rule, the story stays in `review` until a `run_p7_position.sh` re-run inside RTH records `order.accepted`/`order.filled` in a real transcript — the same run 3.2's open AC #7 is waiting on. |
