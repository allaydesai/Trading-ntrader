# Story 3.7: Keep Trading After a Rejection

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a rejected order to be loud and non-fatal,
so that a strategy silently rejected for margin on every order never looks to me like a quiet
market.

## Why this story is shaped the way it is

This is the last story of Epic 3, and **half of it already shipped under Story 3.3**. Read that
sentence twice before designing anything, because the commonest way to fail this story is to
rebuild rejection logging that exists, and the second commonest is to assume that because logging
exists, nothing is left.

**What exists today (do not rebuild):**

- `OrderEventObserver._log_rejected` (`src/core/live_order_path.py:597-626`) already emits
  `order.rejected` at `warning` with `venue_reason=str(event.reason)` **verbatim**, plus
  `client_order_id`, `instrument_id`, `strategy_id`, `ts_event`, `due_post_only` and
  `reconciliation`. `_log_denied` (`:727-741`) does the same for the local `OrderDenied` under
  `reason`. Both sit inside `handle_order_event`'s single `try` (`:483-508`), so a raise in either
  becomes `order.observer_failed`, never a re-entry into `MessageBus.publish_c`. Story 3.3 chose
  `warning` rather than `error` deliberately — *"a rejection is a normal venue answer, and 3.7 owns
  escalation/visibility"* (`3-3-…md:245`) — and scoped itself out of exactly this story's
  deliverable: *"No rejection-visibility surfacing — Story 3.7 owns FR49/NFR24 … This story's
  `order.rejected` record is 3.7's raw material, not its deliverable. No CLI changes, no
  `live_session_health` changes."* (`3-3-…md:497-499`).
- Nautilus's own default handlers are no-ops. `Strategy.on_order_rejected` (`trading/strategy.pyx:
  507-521`) and `on_order_denied` (`:443`) are `# Optionally override in subclass` with an empty
  body; `handle_event` (`:1615`) dispatches `OrderRejected → on_order_rejected → on_order_event`
  inside its own `try`. None of the built-in strategies overrides either
  (`grep -rn on_order_rejected src/core/strategies/` is empty). So today a rejection reaches a
  strategy as a silent `pass`, and the strategy's next signal submits a fresh order — *"session
  remains eligible to trade"* is already the mechanical truth. This story **pins** it; it does not
  create it.
- Story 2.7's `StrategyGuard` wraps `handle_event` as well as `handle_bar` — deliberately ahead of
  need, and the Epic 2 retro told Epic 3 not to tidy it away (`epics.md:427-431`). A strategy that
  *did* override `on_order_rejected` and raised would be contained as `strategy.failed
  handler=handle_event`, not become an `os._exit(1)`. That is AC #2's "never terminates the node",
  and it is already true; this story pins it with a raising subclass so a future change to
  `GUARDED_HANDLERS` goes red here too.

**What does not exist — the actual deliverable (FR49, NFR24, AC #3):** nothing off the event-loop
thread can tell a session rejected on every order from a session seeing no signals. Both have a
fresh heartbeat and fresh bars, so `derive_health` (`src/core/live_session_health.py:144-211`)
reads `trading` for both, `live list`'s table shows the same word for both, and `status --json`
carries nothing else. The Epic 2 retro recorded this as the one open half of `status`: *"Story
3.7 AC #3 (repeated rejections visible from `status`) … wait[s] on Epic 3 rows"*
(`epic-2-retro-2026-08-28.md:361-362`). The PRD's own words for the failure: *"A strategy that
silently stops trading because every order is being rejected for margin is the worst possible
failure — it looks like a quiet market"* (`prd.md:445-447`; the story narrative at `:383-395`).

**Facts measured at drafting (2026-09-21) that decide the design** — each re-measured in Task 0
before production code is written:

1. **The only cross-process channel a running session has for a runtime fact is
   `trading_sessions.runtime_flags`**, and it already has a versioned, additive shape: `{"v": 1,
   "all_failed": …, "failed_strategies": [...]}` written by `_record_strategy_failure`
   (`src/services/session_service.py:392-499`), merged with `**existing` first so unknown keys
   survive (`:487-497`), cleared on every `-> running` edge (`:330-339`), read tolerantly by
   `_flags_mapping`/`_is_degraded` (`live_session_health.py:91-141`). `RUNTIME_FLAGS_VERSION` is
   bumped only on a *meaning* change; an added key is not a bump (`:386-389`; Story 2.7's rule).
   Story 2.8 pre-planned exactly this kind of addition (`connection_lost_at`, its Judgment call
   #1). A second column or a migration would be a design regression: Epic 3's head is
   `85c949ac0374` and stays there.
2. **The runner may not perform a database round trip on the event-loop thread for something
   that is not a trade.** Story 2.7's rule and Story 3.6's D-A both stand: the strategy-failure
   record is *queued* by the guard and *drained* by the steady-state tick on its own executor
   (`live_session_steady_state.py:237-300`, `_write_strategy_failures`); the trade write is the
   one sanctioned inline exception, argued from NFR8 (a `SIGKILL` must lose nothing already
   closed). A rejection is not a trade — NFR8 does not apply, the transcript already holds every
   `order.rejected` record (NFR21's evidence), and losing the last ≤ 30 s of a *summary* to a
   `kill -9` costs nothing an operator cannot reconstruct. So this story's write is **queued and
   tick-drained**, on the 2.7 precedent, not inline on the 3.6 one.
3. **The write must be bounded by construction (NFR2).** A session rejected on every 1-minute
   crossover produces a rejection every few minutes for 6.5 hours. A per-rejection append list in
   `runtime_flags` would grow without bound and be re-serialised whole on every write. The column
   therefore holds a **snapshot** — counters plus the most recent refusal — replaced whole on each
   write, and the tick writes **at most once per interval** regardless of rejection rate
   (dirty-flag drain, not a queue of events).
4. **Rejections come from three sources, and all three arrive as the same event type on the same
   topic.** (a) IBKR proper: `_handle_order_error` maps `ORDER_REJECTION_CODES = {201, 203, 321,
   10289, 10293}` to `order_status="Rejected", reason=error_string` (`adapters/interactive_brokers/
   client/error.py:40, :223-226`) → `_on_order_status` → `OrderStatus.REJECTED`
   (`execution.py:999-1000`) → `generate_order_rejected(reason=…)` (`:889-897`). The venue reason
   is IB's message text **without the code** — the code appears only in the adapter's own
   `[ERROR]`/`[WARNING]` line. (b) The adapter itself: `_submit_order` turns a `ValueError` from
   `_transform_order_to_ib_order` into a local `REJECTED` with `reason=str(e)` (`:652-665`) —
   never reached IB, still an `OrderRejected`. (c) Story 3.4's in-flight sweep: an order the venue
   never acknowledges is closed locally as `OrderRejected(reason="UNKNOWN", reconciliation=True)`
   (`3-4-…md:30-38, :112-120`; `live/execution_engine.py:537-571`). And a fourth refusal that is
   *not* an `OrderRejected` at all: the risk engine's `OrderDenied` (`risk/engine.pyx:726-823`,
   reasons like `NOTIONAL_EXCEEDS_FREE_BALANCE`). Every one of them means the same thing to the
   operator — *the strategy asked and nothing was placed* — and the design tallies all of them,
   while the column records which kind the latest one was.
5. **An IB error code outside `ORDER_REJECTION_CODES` produces no event at all.** `_handle_order_
   error` logs `Unhandled order warning or error code` and returns (`error.py:231-236`); an
   `orderStatus` of `Inactive` is a warning-and-return too (`execution.py:1003-1007`). The order
   then sits `SUBMITTED` until 3.4's sweep resolves it as `UNKNOWN`. So a live rejection can
   surface either immediately with IB's text, or minutes later as `venue_reason="UNKNOWN"
   reconciliation=True`. The tally treats both as a refusal; P13 must read the adapter's own
   error line to learn the real reason in the second case.
6. **The live paper account is a MARGIN account, and Nautilus's risk engine skips every notional
   and balance check for margin accounts** — `_check_orders_risk` returns `True` at `if
   account.is_margin_account: return True  # TODO: Determine risk controls for margin`
   (`risk/engine.pyx:~653`, measured against the installed wheel), before any `NOTIONAL_EXCEEDS_*`
   branch is reached. `logs/p12-20260921.log` (18:06:25Z) shows `account_type=MARGIN`,
   `free=32_542.54 USD`, `BuyingPower: 108475.14`. Consequence for P13: an oversized market order
   of **either side** reaches IBKR and is rejected there (expected code 201, the margin
   rejection), rather than being denied locally — which is exactly the rejection AC #1's letter
   ("rejected by IBKR") needs. `OrderDenied` remains reachable only through the earlier checks
   (unknown instrument, quantity precision/min/max, expired GTD, `HALTED`, submit-rate).
7. **`SessionRecordPort` is `@runtime_checkable`, pinned to exactly three methods**
   (`tests/unit/core/test_live_session_record.py:61`), and has six hand-written doubles in the
   test tree (`grep -rn "def record_strategy_failure" tests/`). Adding a fourth method is a
   deliberate design change every double must follow in the same commit, or `isinstance(double,
   SessionRecordPort)` and the exact-set pin go red. Story 3.6's D-F refused to widen the port for
   the trade sink precisely because *"a trade is not a session-row fact"*. A rejection summary
   **is** a session-row fact — the same class of thing as a contained strategy — so the port is
   the right place, and the widening is argued, not inherited.
8. **`status --json` is pinned to exactly seven keys by set equality** (AR29, `epics.md:220`;
   `test_live_session_health.py:346-360`), and Story 2.8's Judgment call #9 — ratified by Allay
   2026-08-24 — ruled that the human path carries the cause and the JSON contract does not grow a
   key per failure mode. This story does not reopen it.
9. **NFR26 is value-level and `mask_account` is a whole-value masker.** `redact_accounts`
   (`live_strategy_guard.py:139-183`) is the per-token primitive Story 2.7 built for exactly the
   case of free text that may embed an account id, and `_one_redacted_line` caps at
   `MAX_DETAIL_CHARS = 200` (`:118`, `:617-628`). IB rejection text can embed the account code
   (3.3's open conflict, `live_order_path.py:601-613`). The **transcript** stays verbatim — 3.3's
   AC #3 is not this story's to reverse — but the **column**, which `live status` renders, is a
   message this system renders and is redacted at the catch site.

**Decisions made at drafting, disclosed rather than left for review:**

- **D-A — What is new vs. pinned.** AC #1's logging and AC #2's containment are Story 3.3's and
  Nautilus's; this story adds *tests that prove the consequence* — the next order after a
  rejection is submitted, the node is still running, the guard did not latch — and does not touch
  `_log_rejected`, `_log_denied` or `handle_order_event`. `src/core/live_order_path.py` is a
  **zero-diff** file in this story (Task 9's evidence contract), for three reasons: its
  `OrderEventObserver` class is at 98 statements against the 100-statement class cap, the module
  is at 187 statements against a 500-*line* file cap it already exceeds in raw lines, and its
  `EMITTED_ORDER_EVENTS`/`_dispatch` pins are two-directional and would need re-arguing for any
  new emitter.
- **D-B — The tally is a third, independent subscriber on `events.order*`, in a new stdlib-only
  module `src/core/live_order_rejections.py`.** Precedent: `TradeRecorder` is *"the second,
  independent subscriber on `events.position*` the runner installs beside
  `OrderEventObserver`"* (`live_trade_recorder.py:7-9`). `RejectionTally.handle_order_event`
  dispatches on `type(event).__name__` over a closed set — `OrderRejected`, `OrderDenied`,
  `OrderAccepted` — with the whole body in one `try` that emits `order.rejection_tally_failed`
  (diagnostic, the `order.observer_failed` precedent; outside `EMITTED_ORDER_EVENTS` by the same
  rule) and never raises. It holds O(1) state: `rejected`, `denied`, `consecutive`, `first_at`,
  `last` (one frozen record), and a monotonic `version`. It never imports `nautilus_trader`,
  never touches the `ConnectionMonitor`, and never calls an order method — so it needs neither
  `STOP_PATH_MODULES` membership nor a retry scan exemption (it joins `NODE_FACING_MODULES` and
  `TestImportPurity.MODULES`, Task 8).
- **D-C — What counts, and what resets the streak.** `rejected` counts every `OrderRejected`
  (venue, adapter-local, and 3.4's `UNKNOWN`/`reconciliation=True` alike — the last carries
  `reconciliation=True` in the snapshot so a reader can tell). `denied` counts every
  `OrderDenied`. `consecutive` counts both since the last **non-reconciliation** `OrderAccepted`:
  an acceptance is the venue saying "this order is working", which is the opposite of a
  rejection, and a `reconciliation=True` acceptance is a *startup restore* of an order accepted
  before a restart (Story 3.4), not fresh evidence that orders are getting through today. Fills
  are not consulted — every fill was preceded by an acceptance. The rationale for counting
  denials in the streak: a strategy denied locally on every order is the same silent quiet-market
  failure the story exists to make loud; the snapshot's `last.kind` (`"rejected"` /
  `"denied"`) keeps the two distinguishable for the operator, and `live status` prints both
  counters.
- **D-D — The `runtime_flags` shape is one sub-document, replaced whole, no list.**
  ```json
  "order_rejections": {
    "rejected": 3, "denied": 0, "consecutive": 3,
    "first_at": "2026-09-22T14:03:11.482913+00:00",
    "last": {
      "at": "2026-09-22T14:09:05.101220+00:00", "kind": "rejected",
      "client_order_id": "O-20260922-140905-0a1b2c3d-000-3", "instrument_id": "NVDA.NASDAQ",
      "strategy_id": "SMACrossover-000", "reconciliation": false,
      "reason": "Order rejected - reason: … (redacted, ≤ 200 chars)"
    }
  }
  ```
  `v` stays `1`. Written by a new module-level `_record_order_rejections` in
  `session_service.py` beside `_record_strategy_failure`, with the **same two guards** (row must
  be `running`; `_refuse_unless_owner` on `owner_epoch`), the same `FOR UPDATE` load (it is a
  read-modify-write of the whole document), the same `**existing`-first rebuild-and-reassign
  (SQLAlchemy does not track in-place `JSONB` mutation — the 2.7 trap), and the same
  `InvalidSessionTransition → SessionReclaimedError` translation in the adapter. No repository
  method is added: `EXPECTED_CAPABILITIES` (`tests/unit/db/test_trading_session_repository_shape.
  py:31-41`) is **unchanged** — the write goes through the ORM row the service already loads,
  exactly as the strategy-failure write does.
- **D-E — The write happens on the steady-state tick and at teardown, never in the handler.**
  `SessionSteadyState.run()` gains one step after `_write_strategy_failures()`:
  `_write_order_rejections()` asks the tally for a pending snapshot (`None` when nothing changed
  since the last successful write), and if there is one runs `record.record_order_rejections(…)`
  on the **same private executor** (never `asyncio.to_thread` — decision D2's 59.81 s lesson,
  asserted by thread name in the existing harness), then marks that version written. A failed
  write is logged as `session.rejection_record_failed` and simply left dirty — the *next* tick
  writes the *latest* snapshot, so nothing is queued and nothing is lost (a newer summary
  supersedes an older one). `SessionReclaimedError` is re-raised exactly as the sibling does
  (Judgment call #10 of 2.7). The runner's `finally` gains `_flush_order_rejections()` in the
  same slot as `_flush_contained_failures()` (`live_session_runner.py:797-841`: after
  `_stop_heartbeat`, before `_finish_record`, skipped when ownership is lost) so a rejection in
  the last interval before a stop still lands while the row is `running`. Cost: at most one
  extra short transaction per 30 s tick *only while rejections are arriving*, and one at
  teardown — zero for a session with no rejections.
- **D-F — Health gains a third `degraded` sense: the consecutive-refusal streak has reached
  `DEFAULT_REJECTIONS_DEGRADED_AFTER`.** Why health, and not only a rendered line: `live list`
  shows health alone, and `--json` carries health alone (fact 8) — a body line in `status` would
  leave both reading `trading` for a session that cannot get an order placed, which is the exact
  false-green AC #3 exists to close, and the same argument Story 2.8 used for its `all_failed`
  sense (*"the false-green trap"*, `test_live_session_health.py:180-192`). Why a threshold and
  not the first rejection: 3.3 ruled a single rejection *"a normal venue answer"*, and one
  rejected order followed by an accepted one is a session that is trading. **The number is a
  judgment call, flagged for Allay: `DEFAULT_REJECTIONS_DEGRADED_AFTER = 2`** — the second refusal
  with no acceptance between is the point at which "rejected on every order" is a pattern rather
  than an incident, and it is provable live inside one RTH session with the fast-crossover
  tuning (two crossovers). A single rejection still renders its line in the `status` body (AC #3's
  "visible without reading logs" holds from the first one); only the one-word health waits for the
  second. Precedence is unchanged (`stopped` > `stale` > `degraded` > `trading` > `idle`); the
  new sense is one more `or` inside `_is_degraded`, read tolerantly (a malformed sub-document
  never raises — `TestMalformedRuntimeFlagsAreToleratedNotFatal`'s contract).
- **D-G — `status --json` keeps its seven keys; `list` is unchanged; the cause is human-output
  only.** Fact 8, Story 2.8 Judgment call #9. `health: degraded` is what a monitoring script
  sees; `live status <name>` is where it reads why. Re-flagged for the Epic 3 retro as the same
  open question it was at Epic 2's (whether AR29 should carry a `degraded_because` list).
- **D-H — The in-process half: `live start` reports the tally when the run ends.** Story 2.7
  established both halves for a contained failure — `runtime_flags` for another process, and a
  console block from `_print_contained_failures` (`live.py:515-568`) for the operator watching
  the stop. This story adds `LiveSessionRunner.order_rejections` (the final snapshot, or `None`)
  and a ≤ 6-statement `_print_order_rejections` in `live.py` that prints nothing on a clean run
  and otherwise one short block rendered by a pure `render_rejection_summary(snapshot)` in the
  new core module, so `live.py` (already over its file cap and under a "registration lines only"
  discipline since 2.8) gains almost nothing.
- **D-I — `last.reason` is redacted at the tally with `redact_accounts(reason,
  account=settings.tws_account)` and capped at `MAX_DETAIL_CHARS`, both imported from
  `live_strategy_guard`.** Fact 9. The runner already reads `settings.tws_account` for the guard
  (`live_session_runner.py:214-216`) and passes the same string to the tally, so no settings read
  happens inside a handler. This settles NFR26 for the *column* only; the transcript conflict 3.3
  escalated stays escalated and is not widened here.
- **D-J — `OrderTriggered`, `OrderModifyRejected`, `OrderCancelRejected` stay outside the tally's
  closed set** — the same reasoning 3.3 recorded (`deferred-work.md:2209-2225`): no modify or
  cancel-request path exists in this repo, and `OrderTriggered` is Epic 4's broker-authoritative
  scope. A triggered stop is not a refusal.

## Acceptance Criteria

1. **Given** an order is rejected by IBKR
   **When** the rejection is handled
   **Then** it is logged with the venue's reason and the session continues operating and remains
   eligible to trade (FR31, NFR13).
   *Operationalised: (a) the log half is Story 3.3's `order.rejected` record with `venue_reason`
   verbatim — already pinned in `tests/component/core/test_live_order_path.py::
   TestOrderEventObserver`; this story adds **no** assertion there and **no** diff to
   `live_order_path.py` (D-A). (b) "Continues operating and remains eligible" is pinned by a new
   component test against a **real `ExecutionEngine` on a real bus** (`test_live_trade_recorder.
   py:1289`'s `_engine_stack` shape, with a recording exec client): submit order A → the engine
   processes `OrderRejected(A, reason="Order rejected - reason: insufficient margin")` → submit
   order B → the client's `submit_order` count is **exactly 2**, B is `SUBMITTED`, and the
   `RejectionTally` snapshot reads `rejected=1, consecutive=1`; then `OrderAccepted(B)` →
   `consecutive=0`, `rejected=1`. (c) The suppression predicate is untouched by a rejection: with
   the observer, the wrapper (`install_order_path`) and the tally all installed on one strategy, an
   `OrderRejected` leaves `monitor.submission_withheld` `False` and the next wrapped
   `submit_order` calls straight through to the original (the `_StubMonitor` harness at
   `test_live_order_path.py:103`). (d) Live, Procedure P13 (Task 10): a real IBKR rejection with
   a real venue reason, followed by the session's next crossover submitting a fresh order.*

2. **Given** a rejection
   **When** it propagates
   **Then** it never raises into strategy silence and never terminates the node (FR31, AR26).
   *Operationalised, four pins: (a) `RejectionTally.handle_order_event` contains any exception
   from any branch — an event whose `reason`/`client_order_id`/`reconciliation` read raises
   produces exactly one `order.rejection_tally_failed` record (`stage`, `event_type`,
   `client_order_id` via `getattr`, `error_type`, `exc_info=True`) and no raise, parametrized
   over all three dispatched types (the `TestContainmentExtendsToEveryNewDispatchBranch`
   precedent, `test_live_order_path.py:975`). (b) Nautilus's default `Strategy.on_order_rejected`
   and `on_order_denied` are no-ops: a real `Strategy` subclass that overrides neither receives
   `OrderRejected` and `OrderDenied` through `handle_event` and then still handles a bar and
   submits on it (the `RealDispatch` harness, `test_session_runner_strategy_failure.py:260`).
   (c) A subclass whose `on_order_rejected` **raises**, wrapped by `StrategyGuard`, receives an
   `OrderRejected` through `handle_event` and the outcome is one `strategy.failed
   handler=handle_event` record with the guard latched — never a propagated exception — the
   Epic 2 retro's *"do not tidy it away"* wrapper proven against this story's own event
   (`tests/component/core/test_live_order_rejections_engine.py::
   TestAStrategyThatRaisesOnARejectionIsContained` — the component tier, because the proof drives
   a real `Strategy.handle_event`; the draft named `test_live_strategy_guard.py`, corrected at the
   2026-09-21 review). (d) `src/core/live_order_rejections.py` is on
   `NODE_FACING_MODULES`, so the never-exits AST scan covers it from the commit that creates it.*

3. **Given** repeated rejections
   **When** the operator queries session status
   **Then** the condition is visible without reading logs — a session being rejected on every
   order is distinguishable from one seeing no signals (FR49, NFR24).
   *Operationalised: (a) **Write path** — after two `OrderRejected` events and one tick, the
   record port receives `record_order_rejections(rejected=2, denied=0, consecutive=2, first_at=…,
   last_at=…, last_kind="rejected", last_client_order_id=…, last_instrument_id=…,
   last_strategy_id=…, last_reason=<redacted>, last_reconciliation=False)` **once** (not twice —
   dirty-flag semantics), on the `session-heartbeat-write` thread; three more ticks with no new
   event cost **zero** further writes; a third rejection then costs exactly one. (b) **Column
   shape** — `SessionService.record_order_rejections` produces the D-D document with `v == 1`,
   replaces a previous `order_rejections` sub-document wholesale (no list growth), preserves
   `failed_strategies`/`all_failed`/`connection_lost_at` when present, refuses a non-`running`
   row and a foreign `owner_epoch` with `InvalidSessionTransition`, and is invisible to the AR37
   `status =` AST guard (it never assigns `status`). (c) **Read path, the distinguishability pin
   itself** — two `StatusReport`s built from rows identical in every column except
   `runtime_flags` (`None` vs. `{"order_rejections": {… "consecutive": 2 …}}`), both with a fresh
   heartbeat and a bar 30 s old: the first derives `trading`, the second `degraded`; a third with
   `consecutive: 1` derives `trading` **and** still renders the rejection line in the body — so
   the two readings differ by health when the streak is ≥ `DEFAULT_REJECTIONS_DEGRADED_AFTER`
   and by body text from the very first rejection. `render_status` names the cause on the
   degraded path (`_render_degradation`'s "never a bare `degraded`" contract at
   `live_session_health.py:411-435` extends to the new sense), prints both counters, the streak,
   `first_at`, and the last refusal's `at`/`kind`/`instrument_id`/`client_order_id`/`reason`;
   `status_json_payload` still emits **exactly** the seven AR29 keys (D-G). (d) **CLI, from
   another process** — `tests/unit/cli/commands/test_live_status_cli.py` invokes `live status`
   against a row carrying the document and asserts every named field is in the output; and
   `tests/integration/db/test_live_status_e2e.py` (real Postgres, local-only until D2 lands)
   writes the document through `SessionService.record_order_rejections` on a `running` row and
   reads it back through `live status` — `health: degraded` and the reason text. (e) **Live** —
   P13's criteria 4–5.*

4. **Given** a rejected order
   **When** the trade record is inspected
   **Then** no trade row is created for it — a rejection is not a trade (FR24).
   *Operationalised: (a) against the real `ExecutionEngine` + bus of AC #1(b), with a
   `TradeRecorder` subscribed on `events.position*` and a recording sink: a submitted order that
   is rejected publishes **no** `PositionOpened`/`PositionChanged`/`PositionClosed`, the sink is
   never called, and no `trade.aggregated`/`trade.persisted` record is emitted — asserted beside
   a control in the same class where an accepted-and-filled round trip **does** call the sink
   once, so the assertion cannot pass vacuously. (b) A structural pin: `TradeRecorder._dispatch`'s
   keys are a subset of `{"PositionOpened", "PositionChanged", "PositionClosed"}` and
   `POSITION_EVENTS_TOPIC` does not match `events.order*` — an order event can never reach the
   recorder's dispatch by topic or by type. (c) `closed_trade_count` is
   `trade_counts_by_session` over `trades` rows (Story 2.8); nothing in this story writes a
   `trades` row, and the D-D document lives in `trading_sessions.runtime_flags`, so the counter
   is unaffected by construction — cited, not re-tested. (d) Live, P13 criterion 6: `psql` shows
   zero `trades` rows for the session after a run whose every order was rejected.*

## Tasks / Subtasks

- [x] **Task 0: Preflight and re-measure the drafting facts** (AC: all)
  - [x] 0.1 Record the baseline gate counts in the Dev Agent Record before any edit: `make
    test-unit`, `make test-component`, `make test-integration` (local Postgres up; note if
    `tests/integration/db/` ran), `make format lint typecheck`. Confirm `uv run alembic current`
    reads `85c949ac0374` and stays there for the whole story (no migration).
  - [x] 0.2 Re-measure fact 6 against the installed wheel: `grep -n "is_margin_account" .venv/lib/
    python3.11/site-packages/nautilus_trader/risk/engine.pyx` and read the surrounding lines of
    `_check_orders_risk`; record the line number. If the early return is gone in the installed
    version, P13's recipe (Task 10) changes — a BUY would then be denied locally
    (`NOTIONAL_EXCEEDS_FREE_BALANCE`, since `MarginAccount.balance_impact` is `-notional` for a
    BUY and `+notional` for a SELL, `accounting/accounts/margin.pyx`) and only a SELL would reach
    IBKR. Write the finding into P13's preconditions either way.
  - [x] 0.3 Re-measure fact 4/5: `ORDER_REJECTION_CODES`, `WARNING_CODES` and `_handle_order_
    error`'s three branches in `adapters/interactive_brokers/client/error.py`; `_on_order_status`'s
    `"Inactive"` branch in `execution.py`. Record the line numbers.
  - [x] 0.4 Re-measure fact 7: `grep -rn "def record_strategy_failure" tests/` — list every file
    (six at drafting: `tests/component/core/test_session_runner_order_path.py`,
    `test_session_runner_phases.py`, `test_session_runner_stop.py`,
    `test_session_runner_strategy_failure.py`, `test_session_steady_state.py`,
    `tests/unit/core/test_live_session_record.py`). Each gains `record_order_rejections` in
    Task 2.4.
  - [x] 0.5 Measure and record the statement counts (AST `ast.stmt` walk, the CLAUDE.md D4
    measure) of every module in the Project Structure Notes' budget table **before** editing.
    Drafting figures: `live_session_health.py` 129, `live_session_steady_state.py` 166
    (`SessionSteadyState` 89), `live_session_runner.py` 338 (`LiveSessionRunner` 290 — already
    over, disclosed precedent, do not split), `session_service.py` 136 (`SessionService` 37),
    `session_record.py` 44, `live_session_record.py` 16, `live.py` 147, `live_order_path.py` 187
    (`OrderEventObserver` 98).

- [x] **Task 1: `RejectionTally` — the third order-event subscriber** (AC: #1b, #1c, #2a, #3a)
  - [x] 1.1 RED first, in a new `tests/unit/core/test_live_order_rejections.py` (unit tier: the
    module is stdlib-only, so hand-built duck-typed events suffice — objects with
    `client_order_id`, `instrument_id`, `strategy_id`, `reason`, `reconciliation`, `ts_event`
    attributes; no Nautilus import): (i) an `OrderRejected` → `pending()` returns a
    `RejectionSnapshot` with `rejected=1, denied=0, consecutive=1`, `first_at == last.at ==
    <clock>`, `last.kind == "rejected"`, `last.reason` **redacted** (feed a reason embedding
    `DU4076626` and assert `***626` appears and the raw id does not — and, per 2.7's AC #8, a
    configured `account=` redacted case-insensitively) and **capped** at `MAX_DETAIL_CHARS`;
    (ii) an `OrderDenied` → `denied=1, consecutive=1, last.kind == "denied"`; (iii) rejected,
    rejected, accepted → `consecutive=0, rejected=2`; rejected, accepted(`reconciliation=True`),
    rejected → `consecutive=2` (D-C — the reconciliation acceptance does not reset); (iv)
    `first_at` is the first refusal's instant and never moves; (v) `pending()` returns `None`
    before any event, a snapshot after one, `None` again after `mark_written(snapshot.version)`,
    and a **new** snapshot after a further event (dirty-flag semantics); a `mark_written` for a
    stale version leaves it dirty; (vi) every other event type (`OrderSubmitted`, `OrderFilled`,
    `OrderInitialized`, `OrderTriggered`, `PositionClosed`, a plain `object()`) is ignored
    silently — pin the dispatch keys as the exact set `{"OrderRejected", "OrderDenied",
    "OrderAccepted"}` (D-J); (vii) `snapshot.as_port_kwargs()` (or equivalent) yields exactly
    the keyword set `record_order_rejections` takes — primitives only (`int`, `str`, `bool`,
    `datetime`), asserted by type, so nothing Nautilus-shaped can cross AR38's line.
  - [x] 1.2 RED, containment (AC #2a): an event object whose attribute access raises
    (`__getattr__` raising `RuntimeError`) for each of the three dispatched class names produces
    exactly one `order.rejection_tally_failed` record with `stage="handle_order_event"`,
    `event_type`, `client_order_id` read via `getattr(..., None)`, `error_type` and
    `exc_info=True` — and **no raise**, and the tally's counters are unchanged (a half-applied
    increment must not survive a failed build — commit counters only after the record is built,
    the 3.3 `_log_filled` lesson). Use `structlog.testing.capture_logs()` against a locally bound
    logger (the sanctioned shape, 3.3 Hazard 3); never `freezegun` — inject `time_source`.
  - [x] 1.3 GREEN: create `src/core/live_order_rejections.py` — module docstring in the repo's
    Owns/Does-not-own voice, naming D-B..D-D and D-I and citing the measured facts by wheel path
    and line; `RejectionSnapshot` (`@dataclass(frozen=True)`: `version: int`, `rejected: int`,
    `denied: int`, `consecutive: int`, `first_at: datetime`, `last_at: datetime`, `last_kind:
    str`, `last_client_order_id: str`, `last_instrument_id: str`, `last_strategy_id: str`,
    `last_reason: str`, `last_reconciliation: bool`); `RejectionTally(log, *, time_source,
    account: str | None = None)` with `handle_order_event`, `pending() -> RejectionSnapshot |
    None`, `mark_written(version: int)`, `snapshot` (read-only, for the runner/CLI; `None` when
    nothing was ever refused); constants `TALLY_FAILED_EVENT = "order.rejection_tally_failed"`,
    `KIND_REJECTED = "rejected"`, `KIND_DENIED = "denied"`. Import `redact_accounts` and
    `MAX_DETAIL_CHARS` from `src.core.live_strategy_guard` (framework-free, same package) — do
    not duplicate the regex. `first_at`/`last_at` come from the injected `time_source`, **not**
    from `event.ts_event`: the heartbeat and the strategy-failure record both stamp the runner's
    clock, so every timestamp an operator compares in `live status` is from one clock, and a
    test drives it without `freezegun`. Budget: ≤ 95 statements, `RejectionTally` ≤ 55.
  - [x] 1.4 `render_rejection_summary(snapshot) -> list[str]` in the same module: the pure
    renderer both `live start` (D-H) and, via its own wording, `live_session_health` do **not**
    share — keep the health module's renderer separate (it reads the column's dict, not the
    dataclass; the two live in different processes). AR36 audit of every string: *rejected*,
    *denied*, *refused* are fine; no *halt*/*kill*/*pause*/*close*/*finalize* stem anywhere.

- [x] **Task 2: The fourth port method, its adapter, and its service write** (AC: #3a, #3b)
  - [x] 2.1 RED, `tests/unit/services/test_session_service.py` — new `TestRecordOrderRejections`
    beside `TestRecordStrategyFailure` (`:1107`): (i) first write creates
    `runtime_flags["order_rejections"]` with the D-D keys and `v == 1`; (ii) a second write
    **replaces** the sub-document (assert the old `last.client_order_id` is gone and the
    document has no list-typed value anywhere); (iii) an existing `failed_strategies`,
    `all_failed` and an unknown `connection_lost_at` survive the rebuild (`**existing` first);
    (iv) a `stopped`/`created`/`sealed` row raises `InvalidSessionTransition`; (v) a foreign
    `owner_epoch` raises `InvalidSessionTransition` via `_refuse_unless_owner`; (vi) the row is
    loaded `FOR UPDATE` (`TestTheLockedReadIsActuallyRequested`'s shape, `:404`); (vii)
    `status` is never assigned — extend `TestTheAR37StatusAssignmentGuard` (`:567`) if it
    enumerates writers; (viii) `RUNTIME_FLAGS_VERSION` is still `1` (an addition is not a bump).
  - [x] 2.2 RED, `tests/unit/services/test_session_record_adapter.py` — new
    `TestRecordOrderRejections` beside `:364`: one transaction per call (`_RecordingFactory`
    entered/exited `(1, 1)`); the document reaches `row.runtime_flags`; a reclaimed row raises
    the port's own `SessionReclaimedError`; `isinstance(SqlSessionRecord(...),
    SessionRecordPort)` still holds (`:88-91`).
  - [x] 2.3 RED, `tests/unit/core/test_live_session_record.py` — **deliberately** change
    `test_the_port_declares_exactly_three_methods` (`:61`) to four, renaming it and rewriting its
    docstring to say Story 3.7 added `record_order_rejections` and why a rejection summary is a
    session-row fact (fact 7, D-D); add a keyword-only/signature pin for the new method mirroring
    the existing `record_activity` one; extend `_HandWrittenRecord`.
  - [x] 2.4 GREEN: (a) `SessionRecordPort.record_order_rejections(self, *, rejected: int, denied:
    int, consecutive: int, first_at: datetime, last_at: datetime, last_kind: str,
    last_client_order_id: str, last_instrument_id: str, last_strategy_id: str, last_reason: str,
    last_reconciliation: bool) -> None` in `src/core/live_session_record.py`, docstring in the
    sibling's voice (called from the tick, never the handler; primitives only; `last_reason`
    already redacted). (b) `_record_order_rejections(...)` module-level in `session_service.py`
    beside `_record_strategy_failure`, plus a thin `SessionService.record_order_rejections(self,
    session_id, *, owner_epoch, **fields)` delegator loading `for_update=True`. (c)
    `SqlSessionRecord.record_order_rejections` in `session_record.py`, translating
    `InvalidSessionTransition → SessionReclaimedError`. (d) Add the method to **every** double
    from Task 0.4 in the same commit — each records the call the way its siblings do (e.g.
    `self.calls.append("record_order_rejections")` and the kwargs), and `SpyRecord` in
    `test_session_steady_state.py` additionally records `threading.current_thread().name` as its
    `record_strategy_failure` does, for Task 3.1(ii).

- [x] **Task 3: Tick drain, teardown flush, and runner wiring** (AC: #3a, #1b)
  - [x] 3.1 RED, `tests/component/core/test_session_steady_state.py` — new
    `TestTheTickDrainsTheRejectionTally` beside `:1017`, with a `_FakeTally` double (members:
    `pending()`, `mark_written(version)`, plus test-side `refuse(...)` to dirty it): (i) a dirty
    tally is written on the tick with the snapshot's fields, and `mark_written` is then called
    with that version; (ii) the write runs on the `session-heartbeat-write` thread (assert by
    thread name — a mutation back to `to_thread` must fail); (iii) a clean tally costs **zero**
    writes across three ticks; (iv) a write that raises an ordinary `Exception` logs
    `session.rejection_record_failed` (`error_type`), does **not** call `mark_written`, and the
    next tick retries with the **then-current** snapshot (dirty it again between ticks and assert
    the second write carries the newer counters — nothing queued, newest wins); (v)
    `SessionReclaimedError` propagates out of `run()` exactly as the strategy-failure sibling's
    does (`TestAr42SurvivalAndItsOneException`'s shape, `:313`); (vi) a steady state constructed
    with `tally=None` (the default) drains nothing — the Story 2.5/2.6 construction sites keep
    working unmodified.
  - [x] 3.2 RED, `tests/component/core/test_session_runner_stop.py` (or the strategy-failure
    file's `TestTheTeardownFlushesTheQueue` shape, `:600`): a tally dirtied after the last tick
    is flushed through `record.record_order_rejections` during teardown **after**
    `_stop_heartbeat` and **before** `mark_stopped` (interleaved-timeline assertion on the spy's
    `calls`), and is **skipped** when `ownership_lost` is already `True`; a raising flush is
    contained and logged, never replacing the run's outcome (AR42; mirror
    `_flush_contained_failures`'s `except Exception` branch, `:830-841`).
  - [x] 3.3 RED, `tests/component/core/test_session_runner_order_path.py` — new
    `TestTheRunnerReachesTheRejectionTallyCallSite` beside `:416`: a real `LiveSessionRunner`
    against `TestLiveNode` subscribes a `RejectionTally.handle_order_event` on
    `ORDER_EVENTS_TOPIC` (assert via `node.trader.subscriptions` or the spy the file already
    uses for `TradeRecorder`), records it in `self._subscriptions` (so `_unsubscribe` cancels
    it), constructs it with `account=settings.tws_account` and the runner's `time_source`, and
    hands the same instance to `SessionSteadyState` (`_build_steady_state` passes `tally=`).
    Pin the ordering: the tally subscribe happens inside `_phase_subscribe` after the observer's
    two subscribes and before `report_instrument_shortfall`. Also pin that
    `runner.order_rejections` is `None` after a run in which nothing was refused.
  - [x] 3.4 GREEN: `SessionSteadyState.__init__` gains `tally: Any = None` (duck-typed to
    `pending()`/`mark_written()`, the `guard` precedent); `run()` gains `await
    self._write_order_rejections()` after `_write_strategy_failures()`; the new method ≤ 11
    statements — if `SessionSteadyState` measures > 100 statements after the edit, move the body
    to a module-level `write_rejection_snapshot(record, tally, executor, log)` and keep a
    2-statement method (the 2.7 `_record_strategy_failure` module-level precedent; disclose
    which shape landed). Runner: `self._rejection_tally: RejectionTally | None = None` in
    `__init__`; construct + subscribe in `_phase_subscribe`; `tally=self._rejection_tally` in
    `_build_steady_state`; `_flush_order_rejections()` in the `finally` beside
    `_flush_contained_failures()`; `order_rejections` property returning
    `self._rejection_tally.snapshot if self._rejection_tally else None`. Runner delta budget:
    ≤ 18 statements. Record `session.rejection_record_failed` on the failure path only — no
    success record (the `_write_strategy_failures` precedent; AR41's `session.*` namespace is
    conformant by construction, no amendment).

- [x] **Task 4: Health derivation and `status` rendering** (AC: #3c, #3d)
  - [x] 4.1 RED, `tests/unit/core/test_live_session_health.py`: (i) new
    `TestDegradedCoversTheRejectionSense` — the two-rows-identical-except-flags distinguishability
    pin from AC #3(c), the `consecutive: 1` → `trading`-with-body-line case, `consecutive: 2` →
    `degraded`, and the boundary at exactly `DEFAULT_REJECTIONS_DEGRADED_AFTER` (`>=`, at-threshold
    counts — say so in the docstring); (ii) extend `TestMalformedRuntimeFlagsAreToleratedNotFatal`
    (`:582`) — `order_rejections` as a string, a list, an int, an object missing `consecutive`,
    `consecutive` as a string, and a `last` that is not a mapping all render without raising and
    never read `degraded` on the strength of garbage; (iii) extend
    `TestEveryDegradationNamesItsCause` (`:497`) — a report degraded **only** by rejections
    renders a cause line and not the generic fallback; (iv) `TestStatusJsonPayload` (`:346`)
    still passes **unchanged** — its `EXPECTED_KEYS` set equality is D-G's pin, and a new
    `StatusReport` field must not leak into it; (v) `TestRenderStatus` (`:415`): with one
    rejection the body carries `rejected`, the instrument, the `client_order_id`, the redacted
    reason, `first_at`/`last_at` in ISO-8601 UTC, and both counters; with none, none of those
    words appear; the AR36 helper `_ar36_violations` (`:38`) passes over every new line; (vi)
    `TestModulePurity` (`:728`) unchanged — the health module imports nothing new
    (`DEFAULT_REJECTIONS_DEGRADED_AFTER` is declared **here**, the reader's module, exactly as
    `DEFAULT_BAR_FRESH_AFTER_SECONDS` is; the writer never needs it).
  - [x] 4.2 RED, `tests/unit/cli/commands/test_live_status_cli.py` — new
    `TestRejectionVisibility` beside `TestDegradedStrategyVisibility` (`:619`): `live status`
    against a `_row(runtime_flags={"order_rejections": {...}})` shows every named field; a
    stopped session's rejections still render (flags are cleared only on `-> running`); `live
    status --json` output parses and has exactly the seven keys; `live list` shows `degraded`
    for the streak ≥ threshold row and `trading` for the streak-1 row, and no reason text (the
    table is unchanged — pin that no new column was added).
  - [x] 4.3 RED, `tests/integration/db/test_live_status_e2e.py` (real Postgres, local-only):
    create → transition to `running` through `SessionService` → `record_order_rejections` twice
    with the second streak at 2 → `live status <name>` prints `health: degraded` and the reason;
    `live status <name> --json` parses to the seven keys with `"health": "degraded"`; then
    `transition(to=STOPPED)` and `status` still shows the block. Runs in `make test-integration`
    (`--forked`); record in the Dev Agent Record that it ran against local Postgres, per 3.6
    Hazard 7.
  - [x] 4.4 GREEN in `src/core/live_session_health.py`: `DEFAULT_REJECTIONS_DEGRADED_AFTER = 2`
    with a docstring naming it Judgment call D-F; `_order_rejections(runtime_flags) ->
    Mapping[str, object]` (tolerant, the `_failed_strategy_entries` shape); `_is_degraded` gains
    the streak sense; `StatusReport` gains `order_rejections: Mapping[str, object] | None = None`
    (frozen dataclass: the value is the column's mapping, carried verbatim like
    `failed_strategies`); `build_status_report` fills it; `_render_rejections(report) ->
    list[str]` renders the block (counters, streak, first/last, last refusal's kind/instrument/
    `client_order_id`/reason) and `_render_degradation` calls it so the degraded cause is named;
    `render_status` calls `_render_rejections` on the non-degraded path too (a single rejection
    is visible). `status_json_payload` **unchanged**. `live_status.py` **unchanged** — it already
    passes `runtime_flags` through (`:55-80`). Budget: ≤ +31 statements (129 → ≤ 160).

- [x] **Task 5: The in-process half — `live start` reports the tally** (D-H)
  - [x] 5.1 RED, `tests/unit/cli/commands/test_live_cli.py`: after a run whose runner exposes an
    `order_rejections` snapshot, the console shows the counters, the streak and the last
    refusal's redacted reason, and on a clean run (`None`) prints nothing extra; the block is
    printed on **both** the clean-stop path and the raised path (the `_print_contained_failures`
    call sites at `live.py:429` and `:504` — a reclaim that raised may be the run whose last
    rejections never reached the row, the 2.7 review's reasoning); AR36 vocabulary asserted
    directly, as `test_live_cli.py` already does for the failure block.
  - [x] 5.2 GREEN: `_print_order_rejections(runner)` in `live.py` (≤ 6 statements, wording from
    `render_rejection_summary` in the core module), called beside `_print_contained_failures`
    on both paths. The trailer sentence points to `order.rejected` in the log and
    `runtime_flags.order_rejections` on the row, mirroring the failure block's.

- [x] **Task 6: AC #1/#2 proofs against a real engine and a real strategy** (AC: #1b, #1c, #2b, #2c)
  - [x] 6.1 RED, new `tests/component/core/test_live_order_rejections_engine.py` (component
    tier; may load Nautilus, no `TradingNode`, no `BacktestEngine` — the C-logging guard fixture
    from `test_live_order_path.py:91` copied or imported): build the `_engine_stack` shape from
    `test_live_trade_recorder.py:1289` with a recording exec client whose `submit_order` counts
    calls and whose rejection is driven by the test (`engine.process(OrderRejected(...))` built
    directly — the stub hardcodes `reason="ORDER_REJECTED"`, 3.3 Hazard 7 — with a distinctive
    reason embedding an account-shaped token). Subscribe `OrderEventObserver`, `RejectionTally`
    and `TradeRecorder` (recording sink) on the bus exactly as the runner does. Then the AC
    #1(b) sequence: submit A → reject A → submit B → assert `submit_order` count `== 2` (exact
    integer), B `SUBMITTED`, tally `rejected=1, consecutive=1`, one `order.rejected` record with
    the **verbatim** reason (3.3's contract) and a tally snapshot whose `last_reason` is the
    **redacted** one (D-I — assert both in one test so the two policies are visibly distinct);
    accept B → `consecutive=0`. AC #4(a) in the same class: after the reject, no position event
    was published (subscribe a recording handler on `events.position*`), the trade sink was never
    called, no `trade.aggregated`; control test: accept-and-fill a round trip → sink called once.
  - [x] 6.2 RED, the same file: AC #1(c) — a real `Strategy` subclass instance from
    `test_live_order_path.py:149`'s `_new_strategy` shape with `install_order_path` (`_StubMonitor`
    connected) and the tally; deliver an `OrderRejected` through `strategy.handle_event`; assert
    `monitor.submission_withheld` is still `False`, the wrapped `submit_order` calls the base
    (spy) exactly once on the next signal, and no `order.suppressed` record was emitted.
  - [x] 6.3 RED, `tests/component/core/test_session_runner_strategy_failure.py` or the same new
    file, using `RealDispatch` (`:260`): AC #2(b) — a `Strategy` subclass overriding **nothing**
    receives `OrderRejected` then `OrderDenied` via `handle_event`, then `handle_bar` on a real
    bar still reaches `on_bar` (spy) — the no-op default, pinned against the installed wheel;
    cite `trading/strategy.pyx:507-521` and `:443` in the docstring.
  - [x] 6.4 RED, in the new component file (the real `Strategy` is needed — the guard wraps the
    instance's `handle_event`, and only Nautilus's own `handle_event` dispatches to
    `on_order_rejected`; use the `RealDispatch` harness of `test_session_runner_strategy_failure.
    py:260`): AC #2(c) — a `Strategy` subclass whose `on_order_rejected` raises
    `RuntimeError("boom DU4076626")`, wrapped by `StrategyGuard`, receives a real `OrderRejected`
    through `strategy.handle_event(event)`: exactly one `strategy.failed handler=handle_event`
    record, `detail` redacted (`***626`), the latch set, **no raise** out of `handle_event`. Cite
    `epics.md:427-431` (retro note 3) in the docstring: this is the wrapper Epic 3 was told not to
    tidy away, exercised by the event it was kept for.

- [x] **Task 7: AC #4 structural pins** (AC: #4b)
  - [x] 7.1 `tests/component/core/test_live_trade_recorder.py`: pin `set(recorder._dispatch) <=
    {"PositionOpened", "PositionChanged", "PositionClosed"}` and that no name in it starts with
    `Order`; pin `fnmatch("events.order.SMACrossover-000", POSITION_EVENTS_TOPIC)` is `False`.
    Docstring: FR24 — a rejection is not a trade, and the recorder cannot be reached by one.
  - [x] 7.2 In the Dev Agent Record, cite (do not re-test) `trade_counts_by_session` as the
    source of `closed_trade_count` and the absence of any `trades` write in this story's diff
    (`git diff --stat -- src/db src/services/trade_record.py` empty).

- [x] **Task 8: Guard lists and membership pins — in the same commit as the module** (AC: #2d)
  - [x] 8.1 `tests/unit/core/test_live_node_never_exits.py` `NODE_FACING_MODULES` +=
    `"src/core/live_order_rejections.py"` with an inline comment (runs inline inside
    `MessageBus.publish_c`, the `live_order_path.py`/`live_trade_recorder.py` shape).
  - [x] 8.2 `tests/component/core/test_session_runner_phases.py` `TestImportPurity.MODULES` +=
    `"src.core.live_order_rejections"` with the comment convention the list uses (reached from
    the runner, builds the record that crosses into `src/services`, framework-free).
  - [x] 8.3 `tests/unit/services/test_session_service.py::TestImportPurity` (`:642`) — if it
    parametrizes over `session_record.py`, nothing to add; confirm and say so.
  - [x] 8.4 Run and confirm **clean, not assumed**: `STOP_PATH_MODULES` scan (the new module is
    deliberately **not** added — it calls no order method and is not on the stop path; the
    runner's `_flush_order_rejections` calls only the port — record the decision);
    `_STDLIB_AND_FIRST_PARTY` (`tests/integration/core/test_epic1_ac_node.py:449`) — the new
    module imports `dataclasses`/`datetime`/`typing` only, all already listed; if any new stdlib
    name is needed, add it by hand with a comment (Story 3.3's `decimal` lesson); the AR24
    retry scan (`test_order_path_has_no_retry.py`); the AR37 `status =` AST guard;
    `LIVE_MODULE_GLOBS` (globbed — automatic, confirm it picked the file up by running
    `test_live_dependency_invariance.py`); `EXPECTED_CAPABILITIES` (unchanged — confirm);
    `EMITTED_ORDER_EVENTS`/`TestEveryDispatchedRecordNameIsPinned` (unchanged — the tally's
    `order.rejection_tally_failed` is a diagnostic record outside the lifecycle pin on the
    `order.observer_failed` precedent; say so in the tally's docstring and in CLAUDE.md's
    membership-pinned-lists entry).
  - [x] 8.5 Update `CLAUDE.md`: the Anti-Patterns guard-list paragraph gains the new module's
    memberships (NODE_FACING + TestImportPurity), and the Membership-pinned lists entry notes the
    port's exact-method pin moved from three to four at Story 3.7.

- [x] **Task 9: Non-change evidence and size budgets** (D-A)
  - [x] 9.1 Run `git diff --stat` and paste the result in the Dev Agent Record proving **zero
    diff** to: `src/core/live_order_path.py`, `src/core/live_strategy_guard.py`,
    `src/core/live_trade_recorder.py` (Task 7.1 is test-only), `src/core/live_connection_monitor.
    py`, `src/core/live_node_builder.py`, `src/core/strategies/**`, `src/db/**`,
    `src/services/trade_record.py`, `src/cli/commands/live_status.py`, `alembic/**`, `src/api/**`,
    `templates/**` (AR44), `tests/component/core/test_live_order_path.py`,
    `tests/component/core/test_live_order_recovery.py`.
  - [x] 9.2 Re-measure every budgeted module after the edits (Task 0.5's script) and record the
    numbers against the table in Project Structure Notes; disclose any overage with the CLAUDE.md
    D4 wording rather than splitting a guard-listed module.
  - [x] 9.3 The observer's NFR26 anti-field scan (`test_live_order_path.py:1174`) is unchanged;
    add the equivalent scan for the tally's one diagnostic record and for every
    `render_*` string in this story (no `account_id`/`account` key, no unmasked `DU\d{6,}`-shaped
    token) — `TestNoRecordEverCarriesAnAccountId`'s shape.

- [x] **Task 10: Procedure P13 — provoke a real IBKR rejection and read it from `status`**
  (AC: #1d, #3e, #4d)
  - [x] 10.1 Append `## Procedure P13: a session rejected on every order stays up and says so`
    to `docs/qa/phase3-live-verification.md` in P11/P12's format (Introduced by / Verifies /
    What it does — and does not — do / Preconditions / Command / Expected output / Pass criteria
    / Result log). **Preconditions**: P12's, unchanged (inside RTH; no other IBKR login — error
    162; the bare non-compose Gateway; Redis + Postgres up; `alembic current` at `85c949ac0374`;
    fresh session name; `flatten_position.py --confirm` still broken), plus this story's
    measured fact 6 restated with the line number Task 0.2 recorded, plus the account's
    `BuyingPower` read from the transcript's first `ExecClient-INTERACTIVE_BROKERS: {… 'USD':
    {…}}` line **before** the first crossover fires — the sizing below must be far above it, or
    the order fills and the operator owns a real ~$500k paper position with no working tool to
    close it (P11's warning, restated). **Recipe** (the deliberate margin rejection — the very
    failure the story's user statement names): `ntrader live create --name p13-<date> --strategy
    sma_crossover --bar-type NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL --param fast_period=2 --param
    slow_period=3 --param portfolio_value=1000000 --param position_size_pct=50` → ≈ $500,000
    notional (≈ 2,200 NVDA at $227) against `BuyingPower ≈ 108,475` measured on 2026-09-21;
    then `live start p13-<date> > logs/p13-<date>.log 2>&1 &` and wait for **two** crossovers
    (minutes, with this tuning). If Task 0.2 found the margin early-return gone, note that a BUY
    crossover will surface as `order.denied` (local) and only a SELL as `order.rejected` (venue),
    and that at least one SELL crossover is then required for criterion 1.
  - [x] 10.2 **Pass criteria** (write them so each names the record it is read from): (1) at
    least one `order.rejected` record with a non-empty `venue_reason` that is **not** `UNKNOWN`
    and `reconciliation` absent/false, preceded by the adapter's own error line naming the code
    (expected 201; record whichever appears) — AC #1's letter; if only `UNKNOWN reconciliation=
    True` appears, fact 5 fired (a code outside `ORDER_REJECTION_CODES`) — record the adapter's
    `Unhandled order warning or error code` line and its code, note that the tally still
    counted it, and count the criterion as **met with the fact-5 caveat**; (2) `session.started`
    … the next crossover's `order.submitted` **after** the first `order.rejected`, with the
    session still heartbeating (`live status` from a second terminal reads `state: running`
    with a fresh heartbeat throughout) — AC #1's "remains eligible"; (3) zero
    `order.rejection_tally_failed`, zero `order.observer_failed`, zero `strategy.failed`, zero
    `session.rejection_record_failed`; the process is alive until the operator's `SIGINT` —
    AC #2; (4) after the second rejection and at least one heartbeat tick, from a **second
    terminal**: `live status p13-<date>` reads `health: degraded`, the rejection block with
    `rejected: 2` (or more), streak ≥ 2, `first_at`, and the last refusal's instrument,
    `client_order_id` and redacted reason; `live list` shows `degraded` in the Health column;
    `live status p13-<date> --json | jq keys` is exactly the seven AR29 keys with `"health":
    "degraded"` — AC #3; (5) `psql`: `SELECT runtime_flags->'order_rejections' FROM
    trading_sessions WHERE name='p13-<date>'` matches the transcript's counters, and after a
    clean `SIGINT` stop the block still renders (flags survive a stop; cleared only on the next
    `-> running`) — AC #3, cross-process; (6) `SELECT count(*) FROM trades WHERE session_id =
    <pk>` is **0** and `live status` reads `closed trades: 0, open positions: 0` — AC #4; (7)
    `grep -c -E "162|10182|366"` inspected **before** anything else is recorded (D6's standing
    rule), and `net_position=0` at the end with nothing open at the broker (a rejected order
    opens nothing — but confirm, because a fill here is the one bad outcome).
  - [x] 10.3 Run P13 if a Gateway + RTH are available in the same session; otherwise leave the
    result row `⏳ not yet run` and send the story to `review`, not `done` (the standing Epic 2
    retro rule; 3.2–3.6 precedent). Either way, record the outcome in the Dev Agent Record and
    `sprint-status.yaml`.

- [x] **Task 11: Mutation evidence — every test must be able to fail** (AC: all)
  - [x] 11.1 Run at least these mutations **for real** and record the killing test for each:
    M1 delete the `OrderAccepted` reset in the tally → 1.1(iii) red; M2 make `mark_written`
    ignore the version → 1.1(v) red; M3 remove the `reconciliation` check on acceptance → 1.1(iii)
    red; M4 drop the redaction call → 1.1(i)/6.1 red; M5 remove the `try` in
    `handle_order_event` → 1.2 red; M6 swap the tick write to `asyncio.to_thread` → 3.1(ii) red;
    M7 make the tick write unconditionally → 3.1(iii) red; M8 drop `_refuse_unless_owner` from
    `_record_order_rejections` → 2.1(v) red; M9 mutate the document in place instead of
    reassigning → 2.1(i) red against a real row (integration tier, 4.3) — note whether the unit
    tier's MagicMock row also catches it; M10 change `>=` to `>` on the threshold → 4.1(i)
    boundary red; M11 add an eighth key to `status_json_payload` → 4.1(iv) red; M12 add
    `"OrderFilled"` to the tally's dispatch → 1.1(vi) red; M13 delete the runner's
    `_flush_order_rejections` call → 3.2 red; M14 delete the runner's subscribe → 3.3 red.
  - [x] 11.2 Any mutation from the list not run is **named as not run** in the Dev Agent Record
    (3.6's precedent), never silently claimed.

- [x] **Task 12: Artifacts, docs and close-out**
  - [x] 12.1 `_bmad-output/implementation-artifacts/deferred-work.md`: new `## Deferred from:
    story-3.7 (<date>)` section for anything routed away — at minimum: the AR29 JSON-cause
    question re-flagged (D-G, owner: Epic 3 retro); the NFR26 transcript-vs-column asymmetry
    (D-I, owner: the epic-level ruling 3.3 requested); `OrderTriggered` still unhandled (D-J,
    owner: Epic 4, already recorded — cross-reference, do not duplicate); fact 5 (IB codes outside
    `ORDER_REJECTION_CODES` surface only as `UNKNOWN` minutes later — owner: Story 4.3 or the
    Epic 3 retro, whichever Allay rules); the silent-suppression owner gap
    (`deferred-work.md:2195-2207`) — state explicitly that this story does **not** take it
    (a suppressed order is not a rejection and never reaches the tally), so it is not left
    implicit between 3.3 and 3.7 as that entry feared.
  - [x] 12.2 `docs/agent/*.md` and `README.md`: grep for `live status` output samples or a
    `runtime_flags` key list; if any reproduce the human block or enumerate the keys, add the
    rejection lines/key. (`grep -rln "runtime_flags\|live status" docs/agent/ README.md` found
    nothing at drafting — confirm, and say so in the Dev Agent Record.)
  - [x] 12.3 `sprint-status.yaml`: `3-7-keep-trading-after-a-rejection` → `in-progress` at dev
    start, `review` on completion (or `done` only if P13 ran and passed); **prepend** a dated
    paragraph to `last_updated` — it is a cumulative folded scalar, newest first, never
    replaced. `epic-3` stays `in-progress` until the retro rules it done.
  - [x] 12.4 One `feat(live):` commit carrying src + every test tier + BMAD artifacts + docs/qa
    (the 3.1–3.6 shape); subject an operator-outcome sentence, e.g. `feat(live): keep trading
    after a rejection and show repeated rejections from live status`; no AI references.

### Review Findings

Code review 2026-09-21 (three parallel adversarial layers — Blind Hunter, Edge Case Hunter,
Acceptance Auditor — plus the reviewer's own read; 37 raw findings, 8 dismissed as noise or
already decided by the story's own D-C/D-J).

- [x] [Review][Decision] Streak reset relies on an `OrderAccepted` the IB adapter does not always emit — `RejectionTally._note_accepted` is the only reset, and the module docstring asserts "every fill was preceded by an acceptance" as fact. The IB adapter generates `OrderAccepted` only from an `openOrder` callback in `PreSubmitted`/`Submitted` (`adapters/interactive_brokers/execution.py:929-985`); its `_on_order_status("Filled")` branch is a debug log (`:873-876`), and fills come from `execDetails`. An order IB reports straight as `Filled` therefore emits `OrderFilled` with no `OrderAccepted`, and a session that fills repeatedly after two early rejections keeps `consecutive >= 2` and reads `degraded` / "It is not a quiet market" for the rest of the run — the opposite false signal from the one the story exists to close. Every live run so far (P7, P11, P12) did show `order.accepted` before each fill, so this is unproven live but unguarded. **Ruled by Allay 2026-09-21: option (1).** Patched — `OrderFilled` (non-reconciliation) now resets the streak; the D-J set is four names, re-pinned; M12 re-run as a fifth-type mutation and killed.
- [x] [Review][Decision] The streak is session-global, not per strategy — `RejectionTally` holds one `_consecutive`, and any strategy's non-reconciliation acceptance zeroes it. In a two-strategy session, one strategy accepted on every order masks another rejected on every order forever: `last_strategy_id` is carried but never consulted by `_is_degraded`, so the "rejected on every order" pattern the PRD names is invisible exactly in the multi-strategy case. The story never discusses multi-strategy sessions, and `failed_strategies` is per strategy. **Ruled by Allay 2026-09-21: option (1).** Documented in the module docstring and recorded in `deferred-work.md` as an Epic 4 item; no code change.
- [x] [Review][Patch] AC #1(b)'s "submit_order count is exactly 2 / B is SUBMITTED" pin is tautological — `_RecordingExecClient` is never registered with the `ExecutionEngine`, and `_submit()` calls `client.submit(order)` directly, adds the order to the cache by hand and applies `order_submitted` itself; both asserted values are ones the helper wrote. Wire a real exec-client double (`register_client`) and submit through `strategy.submit_order` so the count is measured through the engine [tests/component/core/test_live_order_rejections_engine.py:118-175, :263-278]
- [x] [Review][Patch] `_safe_str` returns the string `"None"` for a missing attribute, not `""` as its docstring says, so the `order.rejection_tally_failed` diagnostic carries a literal `None` client id; `test_the_client_order_id_is_read_through_getattr` only asserts the key exists [src/core/live_order_rejections.py:181-192]
- [x] [Review][Patch] Both renderers say "rejected by the venue" for anything that is not literally `"denied"`, so a reconciliation sweep renders as "rejected by the venue (from reconciliation)" — a contradiction, since the venue never answered — and an unknown `kind` from JSONB is reported as a venue rejection [src/core/live_order_rejections.py:412-413, src/core/live_session_health.py:510-511]
- [x] [Review][Patch] Recovered sessions print "0 in a row with no acceptance between" after `_note_accepted` clears the streak; a zero streak should read as recovered, not as a zero-length run [src/core/live_order_rejections.py:416, src/core/live_session_health.py:500-502]
- [x] [Review][Patch] The `live start` trailer unconditionally points at `runtime_flags.order_rejections` "for the same summary", including on the exception path where the teardown flush was refused (reclaim) or failed; the operator is sent to a column that is stale or absent [src/core/live_order_rejections.py:424-426, src/cli/commands/live.py:431]
- [x] [Review][Patch] `_flush_order_rejections` swallows a failed final write with `error_type` only — no `exc_info`, and nothing appended to `shutdown_problems` — whereas Story 3.6's `_flush_pending_trades` records `flush_pending_trades: <ErrorType>` there; a lost final summary is invisible to whatever renders shutdown problems [src/core/live_session_runner.py:905-908]
- [x] [Review][Patch] Three assertions cannot fail: `assert "1" in text` (ISO timestamp contains it), `assert "2" in text`, `assert "4" in text and "5" in text` against output that contains timestamps; pin the rendered phrases (`"1 rejected, 0 denied"`) instead [tests/unit/core/test_live_order_rejections.py:482, tests/unit/core/test_live_session_health.py:934, :1071]
- [x] [Review][Patch] `test_three_handlers_now_sit_on_the_order_topic` asserts `len(on_order) == 2`; the docstring explains why two is right (`note_bar` is on the bar topic), so the name is wrong [tests/component/core/test_session_runner_order_path.py:601-614]
- [x] [Review][Patch] `test_no_built_in_strategy_overrides_either_hook` uses a CWD-relative `pathlib.Path("src/core/strategies")`, so from any other directory it scans nothing and passes vacuously; it also walks the `custom/` submodule the AC scopes out. Anchor on the package's `__file__` and exclude `custom/` [tests/component/core/test_live_order_rejections_engine.py:522]
- [x] [Review][Patch] `test_no_record_name_is_emitted_but_the_one_diagnostic` only asserts no string-literal first argument to `log.*`; a second `self._log.error(OTHER_CONSTANT, ...)` passes. Collect the `ast.Name` first arguments too and assert they equal `{"TALLY_FAILED_EVENT"}` [tests/unit/core/test_live_order_rejections.py:531-549]
- [x] [Review][Patch] `_render_rejections` and `_render_degradation` each wrap the sub-document back into `{"order_rejections": document}` to reuse `_refusal_streak`; a `_streak_of(document)` helper removes the re-wrap and the second `or {}` guard [src/core/live_session_health.py:500, :545]
- [x] [Review][Patch] Stale test docstring: `TestEveryDegradationNamesItsCause` still says "`_is_degraded` has three independent senses"; the story is also inconsistent on "third" (D-F, `_is_degraded`) vs "fourth" (`_render_degradation`, Completion Notes) [tests/unit/core/test_live_session_health.py:499]
- [x] [Review][Patch] Dev Agent Record claims "18 mutations, 18 killed" but the table has 17 rows (M1–M17); either name the 18th or correct the count (M9's two tiers is one mutation) [3-7-keep-trading-after-a-rejection.md:1162, :1335]
- [x] [Review][Patch] `LiveSessionRunner.run()` went 54 → 55 statements (already over the 50-statement function cap) with the added `_flush_order_rejections()` call and the size table does not disclose it; record the overage per CLAUDE.md [src/core/live_session_runner.py:442; story size table]
- [x] [Review][Patch] AC #2(c) and Project Structure Notes cite `tests/unit/core/test_live_strategy_guard.py` as modified; the commit does not touch it — the pin lives in `test_live_order_rejections_engine.py::TestAStrategyThatRaisesOnARejectionIsContained`. Fix the story text and File List [3-7-keep-trading-after-a-rejection.md AC #2(c), Project Structure Notes]
- [x] [Review][Patch] Task 8.3 ("confirm and say so" for `TestImportPurity` in `test_session_service.py`) is not recorded in the Dev Agent Record; the class scans only `session_service` so nothing was needed — say so [3-7-keep-trading-after-a-rejection.md Dev Agent Record, Task 8]
- [x] [Review][Defer] A tick write still in flight on the executor when `release_executor(wait=False)` runs can land after the synchronous teardown flush, leaving the older snapshot on the row permanently once `mark_stopped` follows; the flush docstring asserts "cannot race" without a join [src/core/live_session_runner.py:876-908, src/core/live_session_steady_state.py:236-250] — deferred, pre-existing (decision D2's `wait=False` shape, shared exactly with `_flush_contained_failures`; window is one Postgres round trip)
- [x] [Review][Defer] `self._log.error` raising inside `handle_order_event`'s `except` escapes the handler and reaches Nautilus's `os._exit(1)` [src/core/live_order_rejections.py:297-305] — deferred, pre-existing (the same shape as `OrderEventObserver.handle_order_event` and `TradeRecorder`; a structlog failure would already take the sibling handlers down first)
- [x] [Review][Defer] `exc_info=True` on the diagnostic can carry the account id inside an exception message or traceback (NFR26 value-level) [src/core/live_order_rejections.py:304] — deferred, pre-existing (identical to `order.observer_failed` / `order.suppression_failed`; NFR26 measured live as zero account fields)
- [x] [Review][Defer] A venue reason containing newlines or control characters breaks the console block and the status body line layout [src/core/live_order_rejections.py:365, :423; src/core/live_session_health.py:516] — deferred, pre-existing (`failed_strategies[*].detail` has the same exposure through `_render_failed_strategies`)
- [x] [Review][Defer] `existing = trading_session.runtime_flags or {}` then `**existing` raises `TypeError` if the JSONB column holds a non-mapping (list/str/number), so the summary is never persisted [src/services/session_service.py:597-598] — deferred, pre-existing (byte-identical to `_record_strategy_failure` at `:476`; the column has a single writer)
- [x] [Review][Defer] Task 3.2's interleaved-timeline assertion pins "before `mark_stopped`" but not "after `_stop_heartbeat`", so a mutation moving the flush ahead of the heartbeat join would not go red [tests/component/core/test_session_runner_stop.py:460-478] — deferred, pre-existing (the Story 2.7 contained-failure test has the same limitation)

## Dev Notes

### The trap — read before designing anything

**The observer already logs it, so the temptation is to "extend the observer".** Do not. Three
separate pins would need re-arguing and one class cap would break (D-A). The tally is a sibling
subscriber, the way the trade recorder is — same topic, own dispatch, own containment, own
docstring. The runner subscribes three handlers on `events.order*` after this story
(`note_bar`-style precedent: the runner already has two on `data.bars*`). Order of subscription
on the bus does not matter to the tally; it needs nothing the observer computes.

**The second trap is the inline write.** Story 3.6 wrote a trade *inside* the handler, and the
arguments for that (NFR8, rarity of closes) do not transfer to a rejection: a rejection can happen
every minute for a whole session, and a lost summary costs nothing the transcript does not
already hold. Queue-and-drain on the private executor is the rule; the tick is the *only* place
`record_order_rejections` is called during a run, and the runner's teardown flush is the only
other caller (fact 2, D-E).

**The third trap is a growing document.** If a reviewer sees a list under `order_rejections`,
the design has drifted (fact 3, D-D). Counters and one `last` record, replaced whole.

### The current surface — extension points, exact

- `src/core/live_order_path.py`: `ORDER_EVENTS_TOPIC = "events.order*"` (`:175`) — reuse the
  constant; `OrderEventObserver` (`:346-741`) — **untouched**; its `_log_rejected` fields
  (`:615-626`) are the vocabulary the tally's snapshot mirrors (`client_order_id`,
  `instrument_id`, `strategy_id`, `reconciliation`), so a transcript line and a status line agree
  on names.
- `src/core/live_session_runner.py`: `_phase_subscribe` (`:567-600`) — insert the tally after
  the observer's two `_subscribe` calls (`:587-588`) and before the recorder (`:593`), through
  `self._subscribe(...)` so `_unsubscribe` (`:446`) cancels it; `_build_steady_state`
  (`:767-780`) — add `tally=`; `run()`'s `finally` — `_flush_contained_failures` (`:797-841`) is
  the template for `_flush_order_rejections`, same slot, same `ownership_lost` short-circuit,
  same `except Exception` containment; `contained_failures` (`:299-309`) is the template for the
  `order_rejections` property; `__init__` (`:169-243`) — `settings.tws_account` is already read
  for the guard (`:214-216`).
- `src/core/live_session_steady_state.py`: `run()` (`:215-235`) — the four-job tick gains a
  fifth; `_write_strategy_failures` (`:237-300`) is the template — `functools.partial` +
  `loop.run_in_executor(self._executor, write)`, `SessionReclaimedError` re-raised, everything
  else logged; `__init__`'s `guard: Any = None` (`:129-166`) is the template for `tally`.
- `src/core/live_session_record.py`: `record_strategy_failure` (the third method) — its
  docstring's "called from the tick, never from the handler" paragraph is the template.
- `src/services/session_record.py`: `SqlSessionRecord.record_strategy_failure` (the last
  method) — copy its shape verbatim, translation included.
- `src/services/session_service.py`: `_record_strategy_failure` (`:392-499`) and
  `SessionService.record_strategy_failure` (`:608-621`) — the module-level-body + thin-delegator
  pair, `for_update=True`, both guards, `**existing` first; `_apply_transition`'s clear
  (`:330-339`) already covers the new key (it clears the whole column).
- `src/core/live_session_health.py`: `_flags_mapping` (`:91-100`), `_failed_strategy_entries`
  (`:103-116`), `_is_degraded` (`:119-141`), `StatusReport` (`:236-282`), `build_status_report`
  (`:285-366`), `_render_degradation` (`:411-435`), `render_status` (`:438-464`);
  `DEFAULT_BAR_FRESH_AFTER_SECONDS` (`:53`) is the precedent for declaring the threshold in the
  reader's module with a drift-pinned docstring.
- `src/cli/commands/live.py`: `_print_contained_failures` (`:515-568`), call sites `:429`,
  `:504`. `src/cli/commands/live_status.py`: nothing — `_load_report` already passes
  `runtime_flags` (`:55-80`).
- `src/core/live_strategy_guard.py`: `redact_accounts` (`:139-183`), `MAX_DETAIL_CHARS` (`:118`)
  — import, do not copy; `StrategyFailure` (`:220-250`) is the precedent for a frozen,
  primitives-only record that crosses AR38.

### Measured facts — cite, don't re-derive

All against the installed 1.220.0 wheel (`.venv/lib/python3.11/site-packages/nautilus_trader/`)
and the 2026-09-21 live transcripts; "Nautilus docs are not evidence" stands.

- **`OrderRejected` attributes**: `account_id`, `client_order_id`, `due_post_only`,
  `instrument_id`, `reason`, `reconciliation`, `strategy_id`, `trader_id`, `ts_event`,
  `ts_init`, `venue_order_id`. **`OrderDenied`**: the same minus `due_post_only`; its
  `account_id` is hardcoded `None` (3.3 Hazard 2). `reason` is normalised `reason or "None"` at
  construction (3.3's measured fact) — an absent reason arrives as the literal string `"None"`.
- **Strategy hooks are no-ops**: `trading/strategy.pyx:507-521` (`on_order_rejected`), `:443`
  (`on_order_denied`); `handle_event` `:1615`, dispatch inside its `try` with
  `OrderRejected → on_order_rejected → on_order_event`. **No built-in strategy overrides either.**
- **IB → `OrderRejected` path**: `client/error.py:40` (`ORDER_REJECTION_CODES = {201, 203, 321,
  10289, 10293}`), `:36` (`WARNING_CODES` — 110, 165, 202, 399, 404, 434, 492, 10167 and
  2100–2199 are warnings, not rejections), `:97-99` (newlines flattened to spaces in the reason),
  `:199-236` (`_handle_order_error`: rejection codes → `"Rejected"` with `reason=error_string`;
  202 → `"Cancelled"`; anything else → a warning and **no event**); `execution.py:986-1012`
  (`_on_order_status`: `"Rejected"` → `REJECTED`; `"Inactive"` → warning, return);
  `:848-902` (`_handle_order_event`, `REJECTED` branch at `:889-897`, guarded by `order.status !=
  REJECTED`); `:652-665` (`_submit_order`: adapter-side `ValueError` → local `REJECTED`,
  `reason=str(e)`).
- **Risk engine**: `risk/engine.pyx:627` (`_check_orders_risk`), early `return True` for
  `is_margin_account` (measured at drafting ≈ `:653`; Task 0.2 pins the number), deny reasons
  `:726-823`, `is_bypassed` `:132` (this repo does not set `bypass`; `LiveRiskEngineConfig` in
  `live_node_builder.py:446-448` passes only `graceful_shutdown_on_exception`).
  `accounting/accounts/margin.pyx` `balance_impact`: BUY → `-notional/leverage`, SELL →
  `+notional/leverage`.
- **In-flight resolution** (Story 3.4): `live/execution_engine.py:537-571` → `OrderRejected(
  reason="UNKNOWN", reconciliation=True)` after `inflight_check_retries` unanswered queries.
- **Live account** (`logs/p12-20260921.log`, 18:06:25Z): `account_type=MARGIN`, `free=32_542.54
  USD`, `AvailableFunds 32542.54`, `BuyingPower 108475.14`, `NetLiquidation 32542.54`. A 22-share
  NVDA order (≈ $5k, `position_size_pct=0.5` on `portfolio_value=1000000`) filled on both P11
  and P12.
- **`runtime_flags` writer/reader contract**: `session_service.py:386-389` (`v` rule),
  `:476-497` (append/rebuild), `:330-339` (cleared on `-> running`); `live_session_health.py:
  91-141` (tolerant reads); Story 2.7's document shape at `2-7-…md:1146-1175`; Story 2.8's
  Judgment calls #1 and #9 at `2-8-…md:608-660`.
- **Port pin**: `tests/unit/core/test_live_session_record.py:61` (exactly three methods —
  changes to four here, deliberately).
- **Msgbus handlers run with no `try`** (`common/component.pyx:2757`; Story 2.7) — the reason
  every handler in this story contains everything.

### Scope boundaries — what this story must NOT build

- **No change to what is logged or how** — `order.rejected`/`order.denied` keep 3.3's fields,
  severity (`warning`) and verbatim `venue_reason`. No new `order.*` lifecycle record; no
  `EMITTED_ORDER_EVENTS` edit.
- **No retry, no resubmit, no "react to a rejection"** anywhere (AR24, AR43, 3.4's temptation
  note at `3-4-…md:416-426`). This story *records and reports*; the strategy's next signal is
  the only path to a new order.
- **No migration, no new column, no new table** — the summary lives in `runtime_flags`; head
  stays `85c949ac0374`.
- **No repository method** — `EXPECTED_CAPABILITIES` unchanged (both twins).
- **No `--json` key, no `list` column** (D-G).
- **No sixth health value** — `degraded` gains a sense; AR32's five-word vocabulary stands.
- **No suppression feedback** and no touching `ConnectionMonitor` — a suppressed call never
  becomes an event and never reaches the tally; the owner gap at `deferred-work.md:2195-2207`
  stays open and is named in Task 12.1.
- **No handling of `OrderTriggered`/`OrderModifyRejected`/`OrderCancelRejected`** (D-J).
- **No strategy diffs** (`src/core/strategies/**` zero-diff), **no `live_order_path.py` diff**,
  **no `live_trade_recorder.py` diff** (test-only pin in Task 7.1).
- **No transcript redaction** — 3.3's escalated NFR26 conflict is not resolved here (D-I covers
  the column only).
- **Nothing in `src/api/**`, `templates/**`, `backtest_query.py`, comparison views** (AR44).

### Hazards (every one bit a previous story, or will)

1. **A raise from a msgbus handler ends the process with zero output.** The tally's whole
   handler body sits in one `try`; the counters are committed only after the record is built
   (the `_log_filled` lesson, 3.3 review).
2. **The port is `@runtime_checkable` and exact-set pinned** — six doubles plus
   `_HandWrittenRecord` must gain the method in the same commit, or `isinstance` checks and the
   pin go red in files that otherwise have nothing to do with this story. `grep` before
   claiming "all doubles updated"; 3.6 found five `SpyRecord`s where its text estimated two.
3. **SQLAlchemy does not track in-place `JSONB` mutation** — rebuild and reassign the whole
   `runtime_flags` dict; a test against a MagicMock row will pass a mutation that never persists
   (M9 is run against real Postgres for that reason).
4. **`**existing` first, or the rebuild drops keys** — a rejection write must not erase
   `failed_strategies`, and a strategy-failure write (which already spreads `**existing`) must
   not erase `order_rejections`. Test both directions.
5. **Never `asyncio.to_thread` for the write** — the loop's default executor is the kernel's,
   joined by `dispose()` with `wait=True`; decision D2's 59.81 s. Thread-name assertion.
6. **Contextvars are empty on executor threads** — the tick logs only through the `log` it was
   handed; the tally the same.
7. **`capture_logs()` is banned near the runner's contextvars binding** — use a locally bound
   `structlog.get_logger("test").bind(session_id=...)` (the shape every class in
   `test_live_order_path.py` already uses); assert `exc_info is True`, never a rendered
   traceback.
8. **`TestEventStubs.order_rejected` hardcodes `reason="ORDER_REJECTED"`** — build
   `OrderRejected`/`OrderDenied` directly wherever the reason must be distinctive or must embed
   an account-shaped token; there is no `order_denied` stub at all (3.3 Hazard 7).
9. **The health module must stay framework-free** (`TestModulePurity`) — it reads the column's
   mapping, never the tally's dataclass; the threshold constant lives there, not in the writer.
10. **The AR36 prose scan matches `closed`** (`\bclose\w*\b`) and `test_live_cli.py` asserts the
    vocabulary directly — audit every new operator-facing string (*rejected*, *denied*,
    *refused* are fine; *halted*, *killed*, *paused*, *closed*, *finalized* are not).
11. **`live.py` is over its file cap and under a "registration lines only" discipline** — the
    rendering belongs in the core module; the CLI helper is ≤ 6 statements.
12. **`SessionSteadyState` is at 89 statements against a 100 cap** — budget the drain method
    before writing it (Task 3.4's fallback shape).
13. **Fact 5 will bite P13 if the venue answers with a code outside the set** — the operator
    sees nothing for `inflight_check_threshold_ms × retries`, then `UNKNOWN`. The procedure says
    what to grep for so the run is still evidence.
14. **A rejection recipe that fills is the one dangerous outcome** — read `BuyingPower` from
    the transcript before the first crossover; `flatten_position.py --confirm` is still broken.
15. **ruff F401 is unfixable-but-blocking** — add each import and its use in one edit; the
    commit gate inspects staged files only.
16. **`sprint-status.yaml`'s `last_updated` is a folded scalar** — prepend a paragraph; never
    replace it.

### Testing standards summary

- Tiers: **unit** — the tally's arithmetic, redaction and dirty-flag semantics
  (`test_live_order_rejections.py`), the service document rules (`test_session_service.py`), the
  adapter's transaction discipline (`test_session_record_adapter.py`), the port pin
  (`test_live_session_record.py`), health/render/JSON (`test_live_session_health.py`), the two
  CLIs (`test_live_status_cli.py`, `test_live_cli.py`), the guard's `handle_event` containment
  (`test_live_strategy_guard.py`); **component** — real engine + real bus + real strategy
  (`test_live_order_rejections_engine.py`, new), tick drain (`test_session_steady_state.py`),
  runner wiring and teardown (`test_session_runner_order_path.py`, `test_session_runner_stop.py`,
  `test_session_runner_strategy_failure.py`), recorder pin (`test_live_trade_recorder.py`);
  **integration** — real Postgres status round trip (`test_live_status_e2e.py`, `--forked`,
  local-only until D2) plus the globbed scans; **live** — P13, observational.
- Markers on every test; TDD RED first with the RED output captured in the Dev Agent Record as
  each test is written; a pin GREEN on first run is recorded as "GREEN on first run: pins
  measured behaviour" and its non-vacuity carried by a Task 11 mutation.
- Anti-tautology twins everywhere a claim could pass vacuously: the reject-no-sink test beside
  the fill-sink-once control; the streak-1 `trading` beside the streak-2 `degraded`; the
  clean-tally zero-writes beside the dirty-tally one-write; the redacted column value beside the
  verbatim transcript value in the same test.
- Existing files that must need **zero changes**: `test_live_order_path.py`,
  `test_live_order_recovery.py`, `test_client_order_id_determinism.py`,
  `test_trading_session_repository_shape.py`, `test_trade_record_adapter.py`,
  `tests/integration/db/test_trade_record.py`.

### Project Structure Notes

- **New**: `src/core/live_order_rejections.py`; `tests/unit/core/test_live_order_rejections.py`;
  `tests/component/core/test_live_order_rejections_engine.py`; Procedure P13 in
  `docs/qa/phase3-live-verification.md`; a `story-3.7` section in `deferred-work.md`.
- **Modified — production** (pre-declared statement budgets; measure before and after):

  | Module | Drafting | Budget after | Note |
  | --- | --- | --- | --- |
  | `src/core/live_order_rejections.py` | — | ≤ 95 (`RejectionTally` ≤ 55) | new |
  | `src/core/live_session_health.py` | 129 | ≤ 160 | threshold, sense, field, renderer |
  | `src/core/live_session_steady_state.py` | 166 (`SessionSteadyState` 89) | ≤ 182 (class ≤ 100) | Task 3.4 fallback if over |
  | `src/core/live_session_runner.py` | 338 (`LiveSessionRunner` 290) | ≤ 356 | over-cap precedent; do not split (guard-list rot) |
  | `src/core/live_session_record.py` | 16 | ≤ 18 | one Protocol method |
  | `src/services/session_record.py` | 44 | ≤ 54 | one adapter method |
  | `src/services/session_service.py` | 136 (`SessionService` 37) | ≤ 152 (class ≤ 42) | module-level body + delegator |
  | `src/cli/commands/live.py` | 147 | ≤ 153 | ≤ 6, rendering lives in core |
  | `src/cli/commands/live_status.py` | 71 | 71 | unchanged |
  | `src/core/live_order_path.py` | 187 (`OrderEventObserver` 98) | 187 | **zero diff** |

- **Modified — tests**: the six record doubles (Task 0.4), `test_live_session_record.py`,
  `test_session_service.py`, `test_session_record_adapter.py`, `test_live_session_health.py`,
  `test_live_status_cli.py`, `test_live_cli.py`, `test_session_steady_state.py`,
  `test_session_runner_order_path.py`, `test_session_runner_stop.py`,
  `test_session_runner_strategy_failure.py`,
  `test_live_trade_recorder.py`, `test_live_node_never_exits.py`, `test_session_runner_phases.py`,
  `tests/integration/db/test_live_status_e2e.py`. (`test_live_strategy_guard.py` was listed here
  by the draft and is **not** touched — the AC #2(c) pin lives in the new
  `test_live_order_rejections_engine.py`; corrected at the 2026-09-21 review.)
- **Modified — docs/artifacts**: `CLAUDE.md` (Task 8.5), `docs/qa/phase3-live-verification.md`,
  `deferred-work.md`, `sprint-status.yaml`, this file.
- **Naming**: module `live_order_rejections`; classes `RejectionTally`, `RejectionSnapshot`;
  records `order.rejection_tally_failed` (error, diagnostic), `session.rejection_record_failed`
  (error, tick write failure); port/service/adapter method `record_order_rejections`;
  `runtime_flags` key `order_rejections` with sub-keys exactly as D-D; constant
  `DEFAULT_REJECTIONS_DEGRADED_AFTER` (health module); runner property `order_rejections`; CLI
  helper `_print_order_rejections`; renderers `render_rejection_summary` (core module, CLI use)
  and `_render_rejections` (health module, status use). AR36 audited.
- **Commit shape**: one `feat(live):` commit (Task 12.4).

### References

- Story definition: `_bmad-output/planning-artifacts/epics.md:1444-1471`; Epic 3 header and
  retro addenda `:385-439` (note 3 — the `handle_event` wrapper; note 5 — the D6 grep);
  `**Covers:**` line `:1109-1113` (NFR24 cited by 3.7).
- Requirements (`epics.md`): FR24 `:66`, FR26 `:68`, FR31 `:73`, FR49 `:100`; NFR2 `:114`,
  NFR6 `:121`, NFR8 `:123`, NFR13 `:128`, NFR21 `:142`, NFR23 `:144`, NFR24 `:145`, NFR26 `:150`,
  NFR32 `:162`; AR23 `:211`, AR24 `:212`, AR26 `:214`, AR28 `:219`, AR29 `:220`, AR32 `:229`,
  AR36 `:236`, AR37 `:237`, AR38 `:238`, AR41 `:241`, AR42 `:244`, AR43 `:245`, AR44 `:246`.
- PRD: `prd.md:383-395` (the narrative this story closes), `:445-447` (rejections never
  swallowed), `:448-450` (buying power is real), `:776` (risk table row).
- Architecture: `architecture.md:295-296` (order events with venue reasons), `:423-424` (health
  vocabulary), `:441` (framework handlers), `:558-559` (rejection flow is 3.7's), `:681-686`
  (G1 — `status`/`list` health).
- Prior stories: `3-3-…md:245` (severity decision), `:443-486` (measured event facts),
  `:497-499` (scope handed to 3.7), `:513-550` (hazards); `3-4-…md:30-38, :112-120, :244-252,
  :416-426` (the `UNKNOWN` rejection and the retry temptation); `3-6-…md:80-148` (decision
  format; D-A's inline-write argument that does **not** transfer; D-F's port argument that is
  inverted here), `:1026-1068` (hazards carried), `:1178-1237` (completion notes — harness
  bugs worth knowing); `2-7-…md:1146-1175` (the `runtime_flags` shape and its three traps);
  `2-8-…md:608-660` (Judgment calls #1, #8, #9); `epic-2-retro-2026-08-28.md:355-362` (the
  `status` half that waits on this story).
- Deferred work: `deferred-work.md:2195-2207` (silent suppression — not this story's),
  `:2209-2225` (`OrderTriggered`), `:2727-2771` (P11/P12 findings — the adapter double-count is
  Story 4.3's; read before interpreting any P13 transcript).
- Code (current lines): `src/core/live_order_path.py:175, :346-508, :597-626, :727-741`;
  `src/core/live_session_runner.py:169-243, :299-309, :567-613, :767-780, :797-841, :881-927`;
  `src/core/live_session_steady_state.py:101-166, :215-300`; `src/core/live_session_record.py`
  (whole file); `src/services/session_record.py` (whole file);
  `src/services/session_service.py:330-339, :386-499, :608-621`;
  `src/core/live_session_health.py:53, :91-141, :144-211, :236-282, :285-366, :369-386,
  :411-464`; `src/cli/commands/live.py:429, :504, :515-568`;
  `src/cli/commands/live_status.py:55-80`; `src/core/live_strategy_guard.py:118, :139-183,
  :220-250, :617-628`; `src/core/live_trade_recorder.py:123, :356-359`.
- Tests (current lines): `tests/unit/core/test_live_session_record.py:61`;
  `tests/unit/core/test_live_session_health.py:38, :168, :346, :415, :497, :582, :728`;
  `tests/unit/cli/commands/test_live_status_cli.py:619`;
  `tests/unit/services/test_session_service.py:404, :567, :642, :1107`;
  `tests/unit/services/test_session_record_adapter.py:88, :364`;
  `tests/component/core/test_session_steady_state.py:50, :313, :981, :1017`;
  `tests/component/core/test_session_runner_order_path.py:99, :294, :416`;
  `tests/component/core/test_session_runner_strategy_failure.py:179, :260, :600`;
  `tests/component/core/test_live_order_path.py:91, :103, :149, :401, :420, :975, :1174, :1321`;
  `tests/component/core/test_live_trade_recorder.py:1289, :1314, :1348`;
  `tests/unit/core/test_live_node_never_exits.py:42-70`;
  `tests/component/core/test_session_runner_phases.py:133, :1222-1270`;
  `tests/unit/core/test_live_stop_path_is_inert.py:38-47`;
  `tests/integration/core/test_epic1_ac_node.py:449`;
  `tests/component/core/test_live_dependency_invariance.py:39`;
  `tests/unit/db/test_trading_session_repository_shape.py:31-41`.
- Nautilus 1.220.0 (installed wheel): `trading/strategy.pyx:443, :507-521, :1615`;
  `adapters/interactive_brokers/client/error.py:36, :40, :72-123, :199-236`;
  `adapters/interactive_brokers/execution.py:652-665, :848-902, :986-1012`;
  `risk/engine.pyx:132, :627-823`; `accounting/accounts/margin.pyx` (`balance_impact`);
  `live/execution_engine.py:537-571`; `common/component.pyx:2757`.
- Live evidence: `logs/p11-20260921.log`, `logs/p12-20260921.log` (account line at
  18:06:25Z), `docs/qa/phase3-live-verification.md` P11/P12 preconditions and result rows.

## Dev Agent Record

### Agent Model Used

Claude Opus 5 (1M context), `claude-opus-5[1m]`, via the BMAD `dev-story` workflow.

### Debug Log References

**Task 0 baselines**, all recorded before any edit, all green:

| Gate | Baseline | Final | Delta |
| --- | --- | --- | --- |
| `make test-unit` | 2501 passed | 2623 passed | +122 |
| `make test-component` | 1517 passed, 16 skipped | 1563 passed, 16 skipped | +46 |
| `make test-integration` | 307 passed, 2 skipped | 313 passed, 2 skipped | +6 |
| `make test-e2e` | — | 1 passed | — |
| `make format` / `lint` / `typecheck` | clean (506 files, 107 source) | clean (509 files, 108 source) | — |

`tests/integration/db/` **did** run in both integration passes (216 matching lines in the
baseline log) — local Postgres was up throughout, so Task 4.3's real-Postgres proof and mutation
M9 are genuine evidence rather than skipped. `uv run alembic current` read `85c949ac0374 (head)`
before **and** after; this story adds no migration.

**Task 0.2 — fact 6 re-measured.** `risk/engine.pyx:652` (drafting said ≈653):
`if account.is_margin_account: return True  # TODO: Determine risk controls for margin`, inside
`_check_orders_risk` at `:626`, ahead of every `NOTIONAL_EXCEEDS_*` branch. **Unchanged from
drafting**, so P13's recipe stands as written — an oversized order of either side reaches IBKR
and is rejected there; no SELL-only variant is needed. Line number recorded in P13's
preconditions.

**Task 0.3 — facts 4/5 re-measured**, all unchanged:
`ORDER_REJECTION_CODES = {201, 203, 321, 10289, 10293}` at `client/error.py:40`;
`WARNING_CODES = {1101, 1102, 110, 165, 202, 399, 404, 434, 492, 10167}` at `:36` with
`2100 <= code < 2200` folded in at `:97`; `_handle_order_error`'s three branches at `:199-236`
(rejection codes → `"Rejected"` with `reason=error_string`; `202` → `"Cancelled"`; anything else
→ a warning and **no event**, `:231-236`); `_on_order_status`'s `"Inactive"` warning-and-return
at `execution.py:1003-1007`.

**Task 0.4 — fact 7 re-measured.** Exactly the six doubles drafting named, no more and no fewer:
`tests/component/core/test_session_runner_order_path.py:112`, `test_session_runner_phases.py:156`,
`test_session_runner_stop.py:120`, `test_session_runner_strategy_failure.py:192`,
`test_session_steady_state.py:70`, `tests/unit/core/test_live_session_record.py:39`. All six grew
`record_order_rejections` in this commit. A cross-check on `def record_activity` found three
*further* inline doubles (`test_session_runner_phases.py:1335`,
`test_session_steady_state.py:767` and `:794`) that implement only `record_activity`; they are
duck-typed at their call sites, never `isinstance`-checked against the port, and the full suite
confirms they need nothing.

**Task 0.5 — statement counts measured before and after** (AST walk over all `ast.stmt` nodes —
the measure that reproduces drafting's figures exactly, docstrings included). See *Size budgets*
below.

**Three measured facts that corrected the Dev Notes**, each found by a test failing for the right
reason:

1. **`getattr(event, name, None)` is not containment.** Its default suppresses only
   `AttributeError`, so an event whose `__getattr__` raises anything else re-raises out of the
   `except` block that exists to contain it — into `MessageBus.publish_c`, which has no `try`,
   and on to `os._exit(1)`. Task 1.2's parametrized containment test caught it on the first run.
   The tally uses a `_safe_str` helper with its own `try`; mutation M15 pins it. The two sibling
   handlers (`OrderEventObserver.handle_order_event`,
   `TradeRecorder.handle_position_event`) carry the same hole and are zero-diff files by decision
   D-A — recorded in `deferred-work.md`, with an honest note that a real Nautilus event cannot
   trigger it, so it is defence-in-depth rather than a live defect.
2. **`OrderDenied.__init__` takes `ts_init` only** (`model/events/order.pyx:661-680`), and derives
   `ts_event` from it — unlike every other order event. The Dev Notes' "the same as `OrderRejected`
   minus `due_post_only`" is true of the *attributes* and false of the *constructor*.
3. **`Strategy.handle_event` returns early unless the component FSM reads `RUNNING`**
   (`trading/strategy.pyx:1645`). A registered-but-unstarted strategy swallows every event before
   reaching `on_order_rejected` — which would have made the whole of AC #2(c) pass for entirely
   the wrong reason. Every strategy in `test_live_order_rejections_engine.py` is `start()`ed, with
   the reason inline.

Two smaller ones worth recording: `mask_account` collapses to `"***"` for any value of 6
characters or fewer (`live_gate.py:138`), so a short configured account has no visible tail; and
`str(order.status)` renders the underlying integer, so `status_string()` is the only correct
comparison.

### Completion Notes List

**What shipped.** The missing half of FR49/NFR24: nothing off the event-loop thread could tell a
session rejected on every order from a session seeing no signals — both had a fresh heartbeat and
fresh bars, so `derive_health` read `trading` for both. It now cannot.

- **`src/core/live_order_rejections.py` (new, 93 statements)** — `RejectionTally`, the third
  independent subscriber on `events.order*` beside `OrderEventObserver` and `TradeRecorder`
  (decision D-B, the `TradeRecorder`-on-`events.position*` precedent). Stdlib-only, closed
  three-name dispatch, one `try` around the whole handler, O(1) state, and a
  primitives-only frozen `RejectionSnapshot` that crosses AR38's line. Plus
  `render_rejection_summary`, the pure renderer that holds **every** operator-facing line of the
  `live start` block — header and trailer included — so the CLI helper contains no wording at all
  and the whole block is assertable without a Click runner.
- **A fourth `SessionRecordPort` method**, `record_order_rejections`, with its service body
  (`_record_order_rejections` at module scope beside `_record_strategy_failure`, both guards, the
  `FOR UPDATE` load, `**existing`-first rebuild-and-reassign) and its SQL adapter. **No
  migration, no new column, no repository method** — `EXPECTED_CAPABILITIES` is unchanged and
  `alembic current` stayed at `85c949ac0374`.
- **Queue-free drain on the steady-state tick** (`write_rejection_snapshot`, module-level so
  `SessionSteadyState` stays inside its 100-statement cap) plus a teardown flush in the runner's
  `finally`, in the same slot as `_flush_contained_failures`. A clean session costs **zero**
  database round trips; a session refused on every crossover costs at most one short transaction
  per 30-second tick regardless of refusal rate.
- **A fourth `degraded` sense** at `DEFAULT_REJECTIONS_DEGRADED_AFTER = 2` (decision D-F, the
  judgment call flagged for Allay), plus the `status` body block that renders from the *first*
  refusal. `--json` keeps its seven AR29 keys and `list` grew no column (decision D-G).

**One design decision changed during implementation, disclosed rather than buried.** Decision D-C
said an `OrderAccepted` resets the streak; it did not say what the *column* then reads. Left as
drafted, a session that recovered would have gone on reporting `degraded` to every other process
for the rest of its run — a stale fact presented as a live one, the exact trap Story 2.7's
`-> running` clear exists to close. So **a streak reset dirties the tally**, and the next tick
writes the cleared streak. Bounded: at most one extra write per recovery, and a streak already at
zero writes nothing. `test_a_streak_reset_to_zero_reads_trading_again` pins the consequence.

**Where the story's own text was wrong, and how it was handled:**

- Task 4.2's CLI tests and Task 4.4's `live_status.py` "unchanged" claim were both correct —
  `live_status.py` is byte-identical, and the CLI tests were **GREEN on first run**, pinning
  behaviour the pass-through already had. Their non-vacuity is carried by mutations M11 and M16,
  both of which turn them red.
- Task 11's M1, M2 and M7 as specified were **no-op mutations** — M1 removed a field assignment a
  sibling line already covered, M2's replacement was self-cancelling, and M7 reached for an
  attribute the test double does not have. All three were rewritten as real mutations and re-run.
- **M2, rewritten, then survived — a genuine test gap.** `mark_written` assigning the version
  unconditionally instead of only when it advances was invisible to
  `test_marking_a_stale_version_written_leaves_it_dirty`, which looks like it covers it and does
  not (there the newer snapshot is dirty either way). `TestMarkWrittenIsMonotonic` was added for
  the direction that actually needs the guard — two acknowledgements arriving out of order must
  leave the tally *clean* — and kills it.

**Task 11 — mutation evidence, every one run for real.** 17 mutations, **17 killed**, 0 survived,
0 not run (first recorded as "18", which double-counted M9's two tiers — corrected at the
2026-09-21 code review, which also re-ran M12 against the widened dispatch set, see the row):

| # | Mutation | Killed by |
| --- | --- | --- |
| M1 | make the `OrderAccepted` branch inert | `test_an_acceptance_resets_the_streak_but_not_the_totals` |
| M2 | `mark_written` ignores version ordering | `TestMarkWrittenIsMonotonic::test_an_out_of_order_acknowledgement_does_not_re_dirty_the_tally` **(added for it)** |
| M3 | drop the `reconciliation` check on acceptance | `test_a_reconciliation_acceptance_does_not_reset_the_streak` |
| M4 | drop the redaction call | `test_the_last_reason_is_redacted_by_token_shape` |
| M5 | remove the `try` in `handle_order_event` | `test_a_raising_event_is_contained_and_recorded_once` |
| M6 | swap the tick write to `asyncio.to_thread` | `test_the_write_runs_on_the_objects_own_executor_never_the_loop` (by thread name) |
| M7 | `pending()` ignores the written version | `test_pending_is_none_again_after_mark_written` |
| M8 | drop `_refuse_unless_owner` | `test_a_reclaimed_row_refuses_the_write` |
| M9 | mutate the document in place instead of reassigning | **both tiers** — unit `test_the_document_is_reassigned_not_mutated_in_place` *and* real-Postgres `test_the_second_write_actually_reached_the_column` |
| M10 | `>=` → `>` on the threshold | `test_a_streak_at_the_threshold_is_degraded` |
| M11 | add an eighth key to `status_json_payload` | `TestStatusJsonPayload`'s set equality |
| M12 | add a fifth type (`OrderSubmitted`) to the tally's dispatch — originally "add `OrderFilled`", until the 2026-09-21 review made `OrderFilled` a deliberate member (streak reset on fill) | `test_the_dispatch_keys_are_exactly_the_four_order_outcome_types` (re-run after the review patch: 1 failed / 52 passed) |
| M13 | delete the runner's teardown flush | `test_a_refusal_after_the_last_tick_reaches_the_record_before_mark_stopped` |
| M14 | delete the runner's tally subscribe | `test_the_runner_subscribes_a_rejection_tally_on_the_order_topic` |
| M15 | revert `_safe_str` to a bare `getattr` | `test_a_raising_event_is_contained_and_recorded_once` |
| M16 | delete the rejection block from `render_status` | `test_a_single_rejection_differs_by_body_text_even_while_health_agrees` |
| M17 | drop the reconciliation marker from the CLI summary | `test_a_reconciliation_refusal_says_so` |

**M9 is the one the story singled out**, and the answer to its open question is: the unit tier
*does* catch it, via the object-identity assertion (`row.runtime_flags is not first_document`),
and the real-Postgres tier catches it independently. Both were run with the mutation applied.

**Task 9.1 — zero-diff evidence.** `git diff --stat` over the fourteen paths decision D-A names
returns **nothing**: `src/core/live_order_path.py`, `live_strategy_guard.py`,
`live_trade_recorder.py` (Task 7.1 is test-only), `live_connection_monitor.py`,
`live_node_builder.py`, `src/core/strategies/**`, `src/db/**`, `src/services/trade_record.py`,
`src/cli/commands/live_status.py`, `alembic/**`, `src/api/**`, `templates/**`,
`tests/component/core/test_live_order_path.py`, `test_live_order_recovery.py`.

**Task 7.2 — AC #4's cited (not re-tested) half.** `closed_trade_count` comes from
`trade_counts_by_session` over `trades` rows (Story 2.8). Nothing in this story writes a `trades`
row — `git diff --stat -- src/db src/services/trade_record.py` is empty — and the D-D document
lives in `trading_sessions.runtime_flags`, so the counter is unaffected by construction.

**Task 8 — guard lists, run and confirmed rather than assumed.**
`src/core/live_order_rejections.py` added by hand to `NODE_FACING_MODULES`
(`test_live_node_never_exits.py`) and `TestImportPurity.MODULES`
(`test_session_runner_phases.py`), each with the reason inline, in the commit that creates the
module. Deliberately **not** added to `STOP_PATH_MODULES` — it calls no order method and is not
on the stop path; the runner's `_flush_order_rejections` calls only the port. Confirmed clean by
running: `LIVE_MODULE_GLOBS` picked the file up automatically (verified by resolving the globs and
asserting the path is among them), the AR24 retry scan (20 passed), the AR37 `status =` AST guard,
`test_live_dependency_invariance.py` (7 passed), `EXPECTED_CAPABILITIES` (68 passed, unchanged),
`EMITTED_ORDER_EVENTS`/`TestEveryDispatchedRecordNameIsPinned` (unchanged — the tally's
`order.rejection_tally_failed` is a diagnostic outside the lifecycle pin, on the
`order.observer_failed` precedent, and the module docstring says so). Task 8.3, confirmed and
said so (added at the 2026-09-21 review, which found it unrecorded):
`test_session_service.py::TestImportPurity` scans `session_service` only, not
`session_record.py`, so nothing was added there. `_STDLIB_AND_FIRST_PARTY`
needed **no** hand edit — the new module's imports (`collections.abc`, `dataclasses`, `datetime`,
`functools`, `typing`) are all already listed — confirmed by the integration tier passing, not by
inspection.

**Task 10 — Procedure P13 written, not run.** Appended to
`docs/qa/phase3-live-verification.md` in P11/P12's format. **Gateway check, recorded rather than
assumed:** at 15:56 ET on 2026-09-21 (Monday, still inside RTH) ports 4001, 4002, 7496 and 7497
were all closed — no Gateway or TWS running — with ~4 minutes of RTH left, so the two crossovers
the recipe needs could not have completed even had one been started. **The story therefore goes
to `review`, not `done`** (the standing Epic 2 retro rule, 3.2–3.6 precedent). The result row
carries the check and the re-measured `risk/engine.pyx:652` finding.

**Size budgets — five small overages, disclosed per CLAUDE.md rather than silently exceeded.**
The measure is an AST walk over all `ast.stmt` nodes (the one that reproduces drafting's figures).

| Module | Drafting | Budget | After | Verdict |
| --- | --- | --- | --- | --- |
| `src/core/live_order_rejections.py` | — | ≤ 95 (class ≤ 55) | **93** (`RejectionTally` 54) | ✅ |
| `src/core/live_session_health.py` | 129 | ≤ 160 | **166** → **181** after the 2026-09-21 review (two shared wording helpers, `_streak_of`) | ⚠️ +21 |
| `src/core/live_session_steady_state.py` | 166 (class 89) | ≤ 182 (class ≤ 100) | **187** (class **97**) | ⚠️ +5 (class ✅) |
| `src/core/live_session_runner.py` | 338 (class 290) | ≤ 356 | **360** (class 311) → **361** (class 312) after the review | ⚠️ +5 |
| `LiveSessionRunner.run()` (function) | 54 | 50 (CLAUDE.md function cap) | **55** | ⚠️ already over before this story; +1 for `_flush_order_rejections()` — **undisclosed until the 2026-09-21 review**, recorded here rather than split (the split moves the module out of the guard lists) |
| `src/core/live_session_record.py` | 16 | ≤ 18 | **19** | ⚠️ +1 |
| `src/services/session_record.py` | 44 | ≤ 54 | **52** | ✅ |
| `src/services/session_service.py` | 136 (class 37) | ≤ 152 (class ≤ 42) | **150** (class **41**) | ✅ |
| `src/cli/commands/live.py` | 147 | ≤ 153 | **154** | ⚠️ +1 |
| `src/cli/commands/live_status.py` | 71 | 71 | **71** | ✅ zero diff |
| `src/core/live_order_path.py` | 187 (class 98) | 187 | **187** | ✅ zero diff |

Every overage but the health module's is 1–6 statements and is docstring-dominated — the case CLAUDE.md's D4 note
explicitly carves out. Against the **real** caps rather than this story's self-imposed budgets,
all ten are compliant except `LiveSessionRunner`'s class count, which was already over before this
story and is a disclosed precedent (splitting it would move it out of two hand-maintained guard
lists — the exact rot CLAUDE.md's Anti-Patterns warn about). The health module's +6 is the one
that is a real design choice rather than prose: it gained **two** tolerant readers
(`_order_rejections` and `_refusal_streak`) where the budget assumed one, because the streak read
must reject `bool` as well as non-`int` — `{"consecutive": True}` would otherwise read as a
streak of one.

**Two numbers worth flagging forward.** `SessionSteadyState` is now at **97 of its 100-statement
cap** — the next story that adds a tick job must budget the split before the edit, not during it.
And `live.py` is at 154 statements against a file already over its line cap; the
"registration lines only" discipline is now the only thing keeping it manageable, which is why
every line of this story's console block lives in the core module.

**Anti-tautology twins, as promised:** the reject-no-sink test beside the fill-sink-once control
(`TestARejectionIsNotATrade`); streak-1 `trading` beside streak-2 `degraded`
(`test_the_boundary_is_at_least_not_greater_than`); clean-tally zero-writes beside dirty-tally
one-write; the verbatim transcript reason beside the redacted column value **in one test**
(`test_the_transcript_keeps_the_reason_verbatim_and_the_column_redacts_it`); the withheld-monitor
control beside the not-withheld case; and the quiet-market `status` beside the rejected one.

**Files the story said must need zero changes, and did:**
`test_live_order_path.py`, `test_live_order_recovery.py`, `test_client_order_id_determinism.py`,
`test_trading_session_repository_shape.py`, `test_trade_record_adapter.py`,
`tests/integration/db/test_trade_record.py`.

**Task 12.2 — docs confirmed, not assumed.** `grep -rln "runtime_flags\|live status" docs/agent/
README.md` returns **nothing**, exactly as drafting predicted, so no sample output or key list
needed updating.

### File List

**New — production**

- `src/core/live_order_rejections.py`

**New — tests and docs**

- `tests/unit/core/test_live_order_rejections.py`
- `tests/component/core/test_live_order_rejections_engine.py`
- Procedure P13 in `docs/qa/phase3-live-verification.md`
- `## Deferred from: story-3.7 (2026-09-21)` in
  `_bmad-output/implementation-artifacts/deferred-work.md`

**Modified — production**

- `src/core/live_session_record.py` (the fourth port method)
- `src/core/live_session_health.py` (threshold, fourth `degraded` sense, `StatusReport` field,
  two tolerant readers, `_render_rejections`)
- `src/core/live_session_steady_state.py` (`tally=` parameter, the fifth tick job,
  `write_rejection_snapshot`, `REJECTION_RECORD_FAILED_EVENT`)
- `src/core/live_session_runner.py` (tally construction + subscribe in `_phase_subscribe`,
  `tally=` into `_build_steady_state`, `_flush_order_rejections`, the `order_rejections` property)
- `src/services/session_service.py` (`ORDER_REJECTIONS_KEY`, `_record_order_rejections`,
  `SessionService.record_order_rejections`)
- `src/services/session_record.py` (`SqlSessionRecord.record_order_rejections`)
- `src/cli/commands/live.py` (`_print_order_rejections`, called on both paths)

**Modified — tests**

- `tests/unit/core/test_live_session_record.py` (the exact-method pin, three → four, deliberately)
- `tests/unit/core/test_live_session_health.py`
- `tests/unit/core/test_live_node_never_exits.py` (`NODE_FACING_MODULES`)
- `tests/unit/cli/commands/test_live_status_cli.py`
- `tests/unit/cli/commands/test_live_cli.py`
- `tests/unit/services/test_session_service.py`
- `tests/unit/services/test_session_record_adapter.py`
- `tests/component/core/test_session_steady_state.py` (double + tick-drain class)
- `tests/component/core/test_session_runner_order_path.py` (double + wiring class)
- `tests/component/core/test_session_runner_stop.py` (double + teardown-flush class)
- `tests/component/core/test_session_runner_strategy_failure.py` (double)
- `tests/component/core/test_session_runner_phases.py` (double, `TestImportPurity.MODULES`,
  the two subscription-list pins)
- `tests/component/core/test_live_trade_recorder.py` (AC #4's structural pins, test-only)
- `tests/integration/db/test_live_status_e2e.py`

**Modified — docs and artifacts**

- `CLAUDE.md` (the new module's guard-list memberships; the port's exact-method pin, three → four)
- `docs/qa/phase3-live-verification.md`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- this file

## Change Log

- 2026-09-21 — Story drafted (ready-for-dev). Ultimate context engine analysis completed —
  comprehensive developer guide created. Design decisions D-A..D-J disclosed for review; the
  threshold in D-F is flagged as a judgment call for Allay.
- 2026-09-21 — Implemented (ready-for-dev → review). All twelve tasks complete; 174 new tests
  (unit +122, component +46, integration +6), every tier green, `format`/`lint`/`typecheck`
  clean, `alembic current` unchanged at `85c949ac0374`. Task 11's mutation sweep run in full:
  17 mutations, 17 killed, none skipped (recorded as 18 until the 2026-09-21 review; M9's two
  tiers had been counted twice) — three of the story's own specified mutations (M1, M2,
  M7) were no-ops as written and were rewritten; **M2 then survived, exposing a real gap in the
  dirty-flag tests**, closed by the new `TestMarkWrittenIsMonotonic`. One design decision changed
  and disclosed: a streak reset now *dirties* the tally so the cleared streak reaches the row —
  without it a recovered session would have reported `degraded` to every other process for the
  rest of its run. Three wheel facts corrected the Dev Notes: `getattr(x, n, None)` is not
  containment (it suppresses only `AttributeError` — a real defence-in-depth hole, present in two
  zero-diff sibling handlers, recorded in `deferred-work.md`); `OrderDenied.__init__` takes
  `ts_init` only; and `Strategy.handle_event` returns early unless the component FSM reads
  `RUNNING`, which would have made AC #2(c) pass for the wrong reason. Five 1–6 statement budget
  overages disclosed rather than silently exceeded. **Status is `review`, not `done`: Procedure
  P13 is written but not run** — no Gateway was reachable (all four ports closed at 15:56 ET with
  four minutes of RTH left).
- 2026-09-21 — Code review (three parallel adversarial layers + reviewer read; 37 raw findings →
  2 decisions, 16 patches, 6 deferred, 8 dismissed; all in the Review Findings section). Both
  decisions ruled by Allay and applied: **a non-reconciliation `OrderFilled` now resets the
  streak** (the IB adapter emits `OrderAccepted` only from a `PreSubmitted`/`Submitted`
  `openOrder`, so a fill with no acceptance is possible and a streak a fill could not clear would
  read `degraded` for the rest of the run — D-C amended, D-J widened to four names, M12 re-run as
  a fifth-type mutation and killed); the **session-wide streak is accepted for Epic 3** and
  recorded in `deferred-work.md` for Epic 4. Patches: AC #1(b)'s harness now submits through the
  real `Strategy.submit_order` → `RiskEngine` → `ExecutionEngine` → registered exec-client double
  (the first version called the double directly, so the "count == 2" it asserted was its own);
  `_safe_str` returns `""` not `"None"`; shared `describe_streak`/`describe_refusal` in the
  health module (a reconciliation sweep is no longer "rejected by the venue", an unknown JSONB
  `kind` is named, a cleared streak no longer renders "0 in a row"); the `live start` trailer no
  longer promises the row holds "the same summary"; a failed teardown flush now logs its
  traceback and lands in `shutdown_problems`; three assertions that could not fail, one
  mis-named subscriber-count test, one CWD-relative scan and one literal-only record-name scan
  tightened; stale "three senses" docstring, the 18-vs-17 mutation count, the `run()` 55-statement
  overage, AC #2(c)'s test location and Task 8.3's confirmation recorded. Gates after the
  patches: unit 1362 (touched files), component 388 (touched files), integration
  `test_live_status_e2e.py` 12/12 on local Postgres, `format`/`lint`/`typecheck` clean.
  **Status stays `review`: Procedure P13 is still not run.**
