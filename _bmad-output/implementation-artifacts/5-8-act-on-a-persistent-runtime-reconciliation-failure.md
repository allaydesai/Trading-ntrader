# Story 5.8: Act on a Persistent Runtime Reconciliation Failure

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a session whose runtime reconciliation keeps failing to stop sending orders, and one whose
adapter has drifted to stop outright,
so that a session never trades for its whole life with no alignment against IBKR just because the
broker read keeps failing quietly.

*(Added 2026-09-30 from PR #35's code review, decision D3 — PO ruling: "withhold after 3, drift
stops". Belongs beside Story 5.4's `session_conditions`, which is where a withheld period should be
recorded.)*

## Why this story is shaped the way it is

Read this before designing anything. Everything below was measured at drafting (2026-10-11, head
`a550646`, worktree `hx-p3e5-5-8-…`, installed `nautilus-trader 1.220.0`).

**Today a persistent cycle failure is an hourly WARNING and nothing else**
(`deferred-work.md`, PR #35 D3). `RuntimeReconciler._run` (`src/core/live_runtime_reconcile.py:342-370`)
turns every non-`ReconciliationFailedError` into `_FailureStreak.note(...)`: one
`reconcile.cycle_failed` WARNING per streak, re-logged at most hourly. `_FailureStreak.count` exists,
nothing acts on it. With the adapter's own position reports off (Story 4.3, ruling 1A), a session
whose broker read keeps failing trades for its whole life with no runtime alignment. Two cases:

1. **A failure that may clear** (a positions request IBKR does not answer, a timeout, cash not yet
   reported): withdraw trading permission after three consecutive failed cycles; the next clean
   cycle re-grants it, exactly as a reconnect does.
2. **Adapter drift** (`BrokerStateAdapterError`, e.g. two IB contracts resolving to one instrument
   id): stop the session on the first occurrence, exit `1`, as startup already does.

A withheld period must then reach the sealed record (AC #3). A session spans many process runs, and
sealing happens in a different process later. So the period has to be **durable and survive the
`-> running` edge**. That is the design problem this story solves; the first two ACs are small.

### Measured facts — cite, don't re-derive

- **F1 — the streak.** `_FailureStreak` (`live_runtime_reconcile.py:707-730`): `count` increments on
  every caught non-`ReconciliationFailedError`, in **both** scopes (`runtime` every 2 ticks while
  `CONNECTED`, `reconnect` every tick while `RECOVERING`); `_run` resets it to `0` after any completed
  `_act`; a cycle skipped because the cache moved (`observe` → `None`) neither counts nor resets.
  `note()` logs WARNING at `count == 1` and then at most every `RUNTIME_OK_LOG_INTERVAL_SECONDS`. It
  logs only `reason` (`getattr(exc, "reason")`) and `error_type`, never `str(exc)` (NFR26).
- **F2 — the monitor cannot be moved to `RECOVERING` from `CONNECTED` today.**
  `ConnectionMonitor._observe_connected` returns early in `CONNECTED`/`RECOVERING`
  (`live_connection_monitor.py:312-319`). The only way out of `CONNECTED` is a disconnected reading.
  A new method is needed. `submission_withheld` is `not trading_permitted`, and `trading_permitted`
  is `state is CONNECTED and not stale` (`:189-214`), so `RECOVERING` withholds every order and
  the order path logs `order.suppressed` (`live_order_path.py:283-298`). Nothing else needs to change.
- **F3 — the re-grant path already exists and is the only one.** In `RECOVERING`, `on_tick` runs a
  `reconnect` cycle **every tick**, resets the debounce, forgets stale-order sightings and sets
  `_ok.needed` once per entry (`:324-335`). A clean cycle with nothing in flight calls `_grant` →
  `grant_after_reconciliation` → `confirm_state_reestablished(status)` → `_grant_permission`, which
  logs `connection.restored` **only if** `_unavailable_since` is set (`:287-310`). The caller set of
  `confirm_state_reestablished` is pinned
  (`tests/component/core/test_session_steady_state.py:502`); this story adds no caller.
- **F4 — the halt clock.** `_check_halt_deadline` runs on every `observe`. Once
  `_unavailable_since` is older than the 60 s window it moves to `HALTED` and logs
  `connection.halted` once. The next connected reading moves `HALTED → RECOVERING` (one tick with
  no cycle), so a halt is a log record, not a dead end. The reconciler's docstring already accepts
  this for a reconnect whose discrepancy debounces past the window, and
  `test_past_the_window_the_halt_fires_and_a_clean_cycle_still_restores` pins it.
- **F5 — `BrokerStateAdapterError`.** It subclasses `BrokerStateUnavailableError`, with
  `exit_outcome = ERROR` (exit `1`) and `operator_safe_message = True`
  (`live_broker_state.py:149-162`). `read_broker_state` raises it for drift and **wraps every
  unexpected exception inside the read** as drift (`:234-242`).
  `find_ib_exec_client` raises it too, and `correct()` calls that inside `_act`.
  At startup it propagates unchanged out of `reconcile_at_startup` and exits `1`
  (`live_startup_reconcile.py:402-408`). The runtime cycle's `_Quiet` logger drops the reader's own
  `reconcile.broker_state_failed` record, so the cycle's record is the only one.
- **F6 — the stop path already works for anything `on_tick` lets out.** `SessionSteadyState.run`
  awaits `on_tick()` unguarded (`live_session_steady_state.py:417-419`). `_serve` retrieves the
  heartbeat's result (`live_session_runner.py:909-917`). `run()` re-raises any exception after the
  teardown `finally`. `live start` maps it with `classify_failure` → `EXIT_CODES` (`live.py:449-460`,
  `live_start.py:99-116`). Re-raising `BrokerStateAdapterError` from `_run` is therefore enough for
  "stops with exit 1". The teardown is the ordinary one, so positions at IBKR are untouched.
- **F7 — `runtime_flags` is cleared on every `-> running` edge.** `_apply_transition` sets
  `runtime_flags = None` (`session_service.py:336-344`, Story 2.7 AC #4). The seal copies the row's
  `runtime_flags` as stored into `session_conditions.last_run_flags` (PO ruling R2a, Story 5.4), so
  today a seal sees **the last process run only**. `deferred-work.md` (story-5.4 (c)) routes exactly
  this gap to this story: *"Owner: Story 5.8 (its withheld period has the same shape), or whoever adds
  a session-lifetime flag history"*. Story-5.4 (a) names the shape: *"a durable writer on the reconcile
  path (fenced by `owner_epoch`, surviving the `→ running` clear)"*.
- **F8 — `trading_sessions` has no other mutable JSONB.** `spec` is write-once (FR14), the
  `backtest_runs` row exists only from seal (AR5), and Redis is the disposable engine cache.
  `runtime_flags` is the only durable, mutable place, and no migration is allowed.
- **F9 — the port.** `SessionRecordPort` (`src/core/live_session_record.py`) is `@runtime_checkable`.
  Its exact method set (4) is pinned in `tests/unit/core/test_live_session_record.py`
  (`test_the_port_declares_exactly_four_methods`). `grep -rn "def record_strategy_failure" tests/`
  lists the hand-written doubles that must all grow a fifth method (7 hits at drafting: one unit and
  six component files). The dirty-flag snapshot write on the steady state's **private executor** is
  Story 3.7's `write_rejection_snapshot` (`live_session_steady_state.py:114-164`).
- **F10 — size budgets (statement metric, `tests/unit/governance/test_size_caps.py`).**
  - `RuntimeReconciler` (class): **94 / 100**, not in the baseline. Keep it ≤ 100: put the
    policy in `_FailureStreak` and in module-level helpers, not in the class.
  - `ConnectionMonitor`: **116**, already at its baseline ceiling (116). A new method raises the
    ceiling. Record it with a one-line reason in the same commit (the "Story 4.3, 118 -> 116"
    precedent).
  - `SessionSteadyState`: **114**, at its ceiling (114). The tick's new call needs room: raise the
    ceiling with a reason, or move a method body to module scope first (the D-I precedent).
    Budget it **before** the edit.
  - `LiveSessionRunner`: class **417** (ceiling 417), file **493 / 500**. **This story does not
    touch the runner** (D-F).
  - Files `live_runtime_reconcile.py` 362, `session_service.py` 275, `session_record.py` 77,
    `session_conditions.py` 79 — all well under 500.
- **F11 — tests that encode today's behaviour and must change deliberately.** Say so in the test's
  docstring:
  - `tests/unit/core/test_live_runtime_reconcile.py::TestAFailedCycleIsContainedAndNotRepeated::test_a_persistent_read_failure_is_logged_once_then_closed_by_the_next_ok`:
    10 failed cycles now give a WARNING at 1 and an ERROR at 3, plus a withhold.
  - `tests/unit/core/test_live_session_record.py::…::test_the_port_declares_exactly_four_methods`
    → five.
  - `tests/unit/services/test_session_conditions.py`'s `RULED_EXTRAS` pin gains the new key.
  - `tests/unit/services/test_session_service.py:1054+`'s `-> running` clear tests stay green: no
    withheld key means `runtime_flags is None` as before. Add the carry-forward sibling.
  - Pins that stay green unchanged: `test_an_unexpected_exception_never_leaves_the_tick` (2 cycles
    < 3), `test_past_the_window_the_halt_fires_and_a_clean_cycle_still_restores` (it is already
    `RECOVERING`, D-J), and the `confirm_state_reestablished` caller pin.

### Design decisions (disclosed here, not discovered in review)

- **D-A — the withdrawal is a new monitor method, and it anchors unavailability.**
  `ConnectionMonitor.withhold_for_reconciliation() -> ConnectionState`. From `CONNECTED` only, it:
  - moves to `RECOVERING`;
  - sets `_unavailable_since = now` if unset;
  - sets `_reconnected_at = _unavailable_since`, because the socket never left, so
    `reconnect_seconds` reads `0.0`.

  From any other state it is a no-op that returns the state: permission is already withheld or not
  yet granted. It never raises (the class's contract) and logs nothing (the reconciler's ERROR
  record is the narration). It is framework-free, like the rest of the module.
  Anchoring is what makes the re-grant behave "exactly as a reconnect does". `downtime_seconds`
  measures the withheld time (its doc already says *unavailability*), and `_grant_permission` logs
  `connection.restored` with it. **Disclosed consequence:** past 60 s withheld, the halt clock logs
  `connection.halted` once and costs one tick without a cycle (F4). That is the same accepted
  behaviour as a reconnect whose discrepancy debounces past the window. Pin the new method's caller
  set the way `confirm_state_reestablished`'s is pinned: one production caller,
  `live_runtime_reconcile`.
- **D-B — the threshold.** `WITHHOLD_AFTER_FAILED_CYCLES = 3` (named, pinned, AC #4), counted on
  `_FailureStreak.count` exactly as F1 describes, in both scopes. At `count == 3`, `note()`:
  - logs **one** `reconcile.cycle_failed` at **ERROR**, regardless of the hourly throttle, with
    `consecutive=3` and `action="trading_withheld"`;
  - restarts the throttle from that record;
  - calls `monitor.withhold_for_reconciliation()`.

  Count 1 stays one WARNING. Counts 2 and 4+ follow today's hourly WARNING throttle. The streak
  resets on the next completed cycle, so a new streak withholds again at its own third failure.
- **D-C — drift stops on the first occurrence, in either scope.** A `BrokerStateAdapterError`
  caught in `_run` (from the read, or from `find_ib_exec_client` inside `correct`) is handled before
  the generic branch:
  - it logs `reconcile.cycle_failed` at **ERROR**, never throttled, with `scope`,
    `reason="adapter_incompatible"`, `error_type` (the drift's own `error_type`, else its type name),
    `consecutive` (this failure included), `detail` (the reader's own operator-safe text, never
    `str(exc)` of a third-party error) and `action="session_stopped"`;
  - then it **re-raises unchanged**: exit `1` via its own `exit_outcome`, the same exception
    startup lets out.

  **Literal reading, disclosed:** `read_broker_state` wraps *any* unexpected exception inside the
  read as drift (F5), so such an exception now stops the session too — exactly as it does at startup.
  Keep the handling in a module-level helper (F10).
- **D-D — a withheld period lives in `runtime_flags["reconcile_withheld"]` and survives the
  `-> running` clear.** This is the only durable, mutable, migration-free place (F7, F8). Every
  *other* key still clears per run, so Story 2.7 AC #4 holds for them unchanged. The
  `-> running` edge rebuilds the document as `{"v": 1, "reconcile_withheld": <carried>}` when a
  history exists, and `None` otherwise. **⚠️ PO ruling requested (Q1, below)**: this is the
  semantic change the 5.4 deferral named as needing a story owner. **Ruled (a), 2026-10-11.**
- **D-E — how a period ends.** A period is always closed by one of these, and an end is never
  invented:
  1. **The re-grant.** `ended_by: "reconciled"`, stamped with the reconciler's wall clock at the
     successful grant.
  2. **The `-> stopped` edge.** `ended_by: "session_stopped"`, with the stop instant. It covers a
     Ctrl-C, a drift stop, a contradiction stop and any teardown. It is durable because
     `mark_stopped` already runs through `transition()`.
  3. **The next `-> running` edge**, for a period a dead process left open (`kill -9`, then a
     reclaim). `ended_by: "process_lost"`, and `end` is the row's `last_heartbeat_at`, read
     **before** the edge overwrites it: the last instant that process is known to have run. If
     there is no heartbeat, `end` stays `null`.
- **D-F — who writes, and when.**
  - **Where it is held.** The ledger, `WithheldPeriods`, lives on the `RuntimeReconciler` and is
    exposed as a public attribute `withheld`. It is stamped with an injected wall clock (`wall_clock`,
    default `datetime.now(UTC)`). `datetime` is already in `_STDLIB_AND_FIRST_PARTY`.
  - **When it is written.** `SessionSteadyState.run` writes the ledger's pending periods **right
    after `on_tick()`, in the same tick**, through the port, on its private executor. It reads the
    ledger as `getattr(self._reconciler, "withheld", None)`, so existing reconciler doubles keep
    working.
  - **How the write behaves** (3.7's dirty-flag shape, as a module-level
    `write_withheld_periods(...)`):
    - a `SessionReclaimedError` re-raises;
    - any other failure logs `session.withheld_record_failed` (ERROR, `error_type` only) and leaves
      the period dirty for the next tick.
  - **No teardown flush, and zero runner changes** (F10: file 493/500, class at its ceiling). The
    stop edge closes the period durably instead (D-E 2).
  - **Disclosed residual:** a period whose *start* never reached Postgres before the process stopped
    is not in the record: the DB was down from the withdrawal to the stop, or a stop cancelled that
    one in-flight write. The transcript's ERROR record still names it.
  - **Amended by code review DN1 (PO ruling 1a):** there *is* now a teardown flush.
    `flush_run_snapshots` runs in the runner's existing slot, renamed `_flush_run_snapshots`, before
    `-> stopped`. The runner's size is unchanged. Only a `kill -9` can still lose a start or
    overstate an end.
- **D-G — the port widens 4 → 5.** The new method is
  `record_trading_withheld(*, started_at: datetime, ended_at: datetime | None, reason: str | None,
  error_type: str, failed_cycles: int) -> None`, and it is idempotent: the service upserts by
  `started_at`. Argued, not inherited, on Story 3.7's terms: a withheld period is a session-row fact,
  in the same `runtime_flags` document, written on the same tick by the same executor through the
  same two guards (`running`, `owner_epoch`). Story 3.6 D-F's refusal for trades does not apply.
  `SqlSessionRecord` implements it like its siblings: one short transaction, `FOR UPDATE`, and
  `InvalidSessionTransition` → `SessionReclaimedError`. Update CLAUDE.md's `SessionRecordPort` entry
  (four → five) in the same commit.
- **D-H — the document, and its bound (NFR2).**

  ```
  runtime_flags["reconcile_withheld"] = {
    "count": <every period this session has had>,
    "periods": [<at most WITHHELD_PERIODS_KEPT (50), most recent>]
  }
  period = {"start": iso, "end": iso | null, "cause": "runtime_reconciliation_failing",
            "reason": str | null, "error_type": str, "failed_cycles": 3,
            "ended_by": "reconciled" | "session_stopped" | "process_lost" | null}
  ```

  - **Cause.** `cause` is fixed; `reason`/`error_type` are the **withdrawing** failure's (the third),
    which is enough to say why. No list ever grows without bound: past the cap the oldest period
    drops and `count` still says how many there were.
  - **Where it lives.** Pure, JSON-native helpers in a **new**
    `src/services/reconcile_withheld.py`, shared by the writer (`session_service`) and the reader
    (`session_conditions`). It is not a `live_*` module, so no guard list is touched. The helpers are
    `upsert_period`, `close_open_period(doc, end, ended_by)`, `carried_forward(flags, last_heartbeat_at)`
    and `withheld_for_record(flags)`.
  - **Rebuilt, never mutated in place.** Rebuild and reassign the document; SQLAlchemy does not track
    in-place JSONB mutation (`session_service.py:439-444`).
- **D-I — at seal.** `build_session_conditions` derives
  `session_conditions["reconcile_withheld"]` from the `last_run_flags` it is already handed: the
  stored sub-document, deep-copied and never judged (the 5.4 P4 precedent: a stopped session must
  always be sealable). It is `{"count": 0, "periods": []}` when absent, so absence reads as "none",
  explicitly. The key sits outside AR7's seven, beside `start_date_basis` and `last_run_flags`, and
  `schema_version` stays `1` (an addition is not a meaning change). `session_seal_service.py` needs
  no change. `last_run_flags` keeps recording the row as stored (R2a), so the history appears there
  too (see Q1).
- **D-J — only a withdrawal this story makes is a recorded period.** If the third failure arrives
  while the monitor is already `RECOVERING` (a disconnect whose reconnect cycles keep failing), the
  ERROR is still logged, but there is nothing to withdraw and no period opens. That withhold belongs
  to the connection loss, narrated by `connection.lost`/`connection.restored`. The cause of a period
  is never re-labelled mid-period: a real disconnect during a reconciliation withhold leaves the
  period open until the re-grant. **Amended by code review:**
  - **P5.** When nothing is withdrawn, no ERROR is logged either; the streak's WARNING throttle
    covers it.
  - **P1.** A real disconnect during a reconciliation withhold is narrated as its own outage
    (`connection.lost`, re-anchored, the halt re-armed).
- **D-K — `live status` is unchanged** (Q2 ruled (a)). A withheld session still reads by its existing
  health senses; `_is_degraded` reads only its known keys and ignores the new one.

### PO rulings (2026-10-11, before dev)

- **Q1 = (a).** Carry `reconcile_withheld` across the `-> running` clear. Every other runtime flag
  still clears per run, and `last_run_flags` stays "as stored" (R2a literal). (c) was refused: AC #3
  requires every withheld period to reach `session_conditions`.
- **Q2 = (a).** No `degraded` sense in `live status` / `live list` in this story. Recorded in
  `deferred-work.md`.
- **Conditions on the design (PO):**
  - **The fifth `SessionRecordPort` method is sanctioned.** In the same commit: widen the
    exact-method pin 4 → 5, grow every hand-written double, and re-check the guard lists
    (`NODE_FACING_MODULES`, `TestImportPurity.MODULES`, `STOP_PATH_MODULES`,
    `_STDLIB_AND_FIRST_PARTY`) for every touched module and new import.
  - **No write path to `spec`.** No forbidden mutator prefix on a repository capability unless
    `EXPECTED_CAPABILITIES` is updated deliberately with a reason. This design adds **no**
    repository method: the service mutates the locked row, as its siblings do.
  - **Closing an open period on restart or stop is record-keeping only.** It never grants trading
    permission; only the clean-cycle `confirm_state_reestablished` path does. A stop-time close
    calls no order method.
  - **A `BrokerStateAdapterError` stops first, with exit 1.** A failure to persist the withheld
    period must never mask that exit.
  - **Every period carries `start`, `end` and `cause`, and an open-at-seal period is recorded
    explicitly.** Its `end` is `null` and its `ended_by` is `"open_at_seal"` in the record.
  - **Tests prove that two failed cycles do not withhold and that three do.**
  - Fail closed throughout. AR44 files untouched; no migration.
  - The operator procedure is the next P-number, "defined, not run".

## Acceptance Criteria

Copied verbatim from `_bmad-output/planning-artifacts/epics.md` (Story 5.8) /
`prd-epic5-scope.md`; numbered for traceability.

1. **Given** `RuntimeReconciler._run` catches a non-`ReconciliationFailedError` on three consecutive
   cycles (`_FailureStreak.count` reaches 3)
   **When** the third failure is noted
   **Then** trading permission is withdrawn through the connection monitor's existing `RECOVERING`
   state (orders withheld, `order.suppressed`), one `reconcile.cycle_failed` is logged at ERROR naming
   the streak, and the next clean cycle re-grants through `confirm_state_reestablished` exactly as a
   reconnect does.

2. **Given** the failure is a `BrokerStateAdapterError` (adapter drift — e.g. two IB contracts
   resolving to one instrument id)
   **When** it is first noted
   **Then** the session stops with exit `1` on the first occurrence, as startup treats adapter drift;
   the hourly throttle does not apply.

3. **Given** a withheld period
   **When** the session is later sealed (Story 5.3)
   **Then** the period is recorded in `session_conditions` (Story 5.4) with its start, end and cause.

4. **Given** the three-cycle threshold
   **When** it is pinned
   **Then** it is a named constant with a test that fails if a streak of two withholds or a streak of
   three does not.

**Traceability gate note (NFR33):** a real multi-cycle runtime reconciliation failure cannot exist
in CI. ACs #1, #2 and #4 are proven against broker doubles (unit, plus the component tier for the
tick's write). AC #3's sealing is database-only and is proven by automated tests against the test
database (integration/db). Procedure P23 in `docs/qa/phase3-live-verification.md` is the operator
half, recorded "defined, not run".

## Tasks / Subtasks

- [x] **Task 0 — Budget first** (F10)
  - [x] 0.1 Measure `RuntimeReconciler`, `ConnectionMonitor`, `SessionSteadyState` with
    `measure_module`. Decide where each addition goes **before** editing. Record any ceiling raise
    in `SIZE_BASELINE` with a one-line reason in the same commit.

- [x] **Task 1 — The monitor's withdrawal** (AC: #1) — `src/core/live_connection_monitor.py`
  - [x] 1.1 Failing tests first in `tests/unit/core/test_live_connection_monitor.py`:
    - `withhold_for_reconciliation()` from `CONNECTED` → `RECOVERING`, `trading_permitted` False,
      `submission_withheld` True, `downtime_seconds` measured, `reconnect_seconds == 0.0`.
    - A no-op (state returned, nothing changed) from `RECOVERING`, `LOST`, `HALTED` and
      `AWAITING_CONNECTION`.
    - `confirm_state_reestablished(UP)` afterwards grants and logs `connection.restored`.
    - A disconnected reading while withheld → `LOST`, keeping the anchor.
    - Past the window the halt fires once and the next connected reading returns to `RECOVERING`.
    - It never raises.
  - [x] 1.2 Implement per D-A. Update the module docstring (a fifth thing it owns: the reconciler's
    withdrawal, the one production caller).
  - [x] 1.3 Caller-set pin beside `test_confirm_state_reestablished_has_exactly_one_production_caller`
    (`tests/component/core/test_session_steady_state.py:502`): `withhold_for_reconciliation` has
    exactly one production caller module, `live_runtime_reconcile`.

- [x] **Task 2 — Withhold after three, drift stops** (AC: #1, #2, #4) —
  `src/core/live_runtime_reconcile.py`
  - [x] 2.1 Failing tests first in `tests/unit/core/test_live_runtime_reconcile.py`, using the
    existing `_World` and a **real** `ConnectionMonitor`:
    - **AC #4 pin:** `WITHHOLD_AFTER_FAILED_CYCLES == 3`. A streak of **two** failed cycles leaves
      the monitor `CONNECTED`; a streak of **three** leaves it `RECOVERING`. The test must fail if
      the constant or the comparison drifts either way, so assert both sides, each against the
      constant **and** the literal.
    - **ERROR:** exactly one `reconcile.cycle_failed` at `error` with `consecutive == 3` and
      `action == "trading_withheld"`. Count 1 stays one `warning`. No other record in a 10-cycle
      streak: re-log hourly, as today.
    - **Orders withheld:** `install_order_path` on a `SimpleNamespace` strategy with a spy
      `submit_order`, the reconciler's real monitor, 3 failures, then call `submit_order`. The base
      is not called and `order.suppressed` is logged. The sibling with 2 failures calls the base.
    - **Re-grant "exactly as a reconnect does":**
      - the next tick runs a `reconnect` cycle;
      - a clean one logs `reconcile.ok scope=reconnect` then `connection.restored`;
      - the grant goes through `confirm_state_reestablished` (spy, `calls == [UP]`);
      - an in-flight order still withholds the grant;
      - a new streak withholds again at its own third failure.
    - **D-J:** three reconnect-cycle failures after a real disconnect log the ERROR but open no
      period.
    - **AC #2:** a `BrokerStateAdapterError` on the **first** failed cycle (and, separately, after
      two ordinary failures, inside the hourly throttle window):
      - it raises out of `on_tick` unchanged;
      - `classify_failure(exc)` → `EXIT_ERROR` (exit 1);
      - exactly one ERROR `reconcile.cycle_failed`, with `reason == "adapter_incompatible"` and
        `action == "session_stopped"`;
      - the monitor was never withheld by it.

      Repeat in the `reconnect` scope. Sibling: a plain `BrokerStateUnavailableError` does **not**
      stop.
    - **NFR26:** `RAW_ACCOUNT` appears in no record on either path.
  - [x] 2.2 Rework `test_a_persistent_read_failure_is_logged_once_then_closed_by_the_next_ok` (F11)
    deliberately: WARNING at 1, ERROR at 3, then `reconcile.ok` plus a re-grant on recovery.
  - [x] 2.3 Implement D-B in `_FailureStreak` (it takes the monitor and the ledger) and D-C as a
    module-level helper called from a new `except BrokerStateAdapterError` branch in `_run`, before
    `except Exception`. Keep `RuntimeReconciler` ≤ 100 statements.
  - [x] 2.4 Update the module docstring's **Volume** paragraph (a failing read is no longer only
    hourly) and add the withdrawal and the drift stop to "What a confirmed row does"/the stop list.

- [x] **Task 3 — The withheld-period ledger** (AC: #1, #3) — `src/core/live_runtime_reconcile.py`
  - [x] 3.1 Failing tests first (unit):
    - a withdrawal opens one period (`started_at` from the injected `wall_clock`, `reason`,
      `error_type`, `failed_cycles == 3`, `ended_at is None`);
    - the successful re-grant closes it (`ended_at` set);
    - a refused grant (a reading that drops at confirm time) does **not** close it;
    - `pending()` returns dirty periods in order; `mark_written(p)` clears only that version, so a
      period re-dirtied by its end after a written start is pending again;
    - two consecutive periods are both pending if neither was written.
  - [x] 3.2 Implement `WithheldPeriod` (frozen dataclass, `as_port_kwargs()`, stdlib values only —
    AR38) and `WithheldPeriods` (public, duck-typed by the steady state). Expose it as
    `RuntimeReconciler.withheld`. Add the `wall_clock` constructor parameter with a UTC default, so
    the runner does not change.

- [x] **Task 4 — The port and its write** (AC: #3) — `src/core/live_session_record.py`,
  `src/core/live_session_steady_state.py`, `src/services/session_record.py`
  - [x] 4.1 Widen `SessionRecordPort` 4 → 5 (D-G). Update the exact-method pin (F11) and **every**
    hand-written double (`grep -rn "def record_strategy_failure" tests/`) in the same commit. Update
    CLAUDE.md's `SessionRecordPort` entry.
  - [x] 4.2 Failing component tests in `tests/component/core/test_session_steady_state.py`, using the
    existing `SpyRecord` and a stub reconciler whose `withheld` holds a pending period:
    - the tick writes it **after** `on_tick()`, in the same tick, with the period's own kwargs;
    - a clean tick writes nothing (zero round trips);
    - a failed write logs `session.withheld_record_failed` and is retried next tick;
    - a `SessionReclaimedError` from the write leaves `run()`;
    - a reconciler double with no `withheld` attribute still runs.
  - [x] 4.3 Implement module-level `write_withheld_periods(record, ledger, *, executor, loop, log)`
    (the `write_rejection_snapshot` shape) and the call in `run()`. Update the module and class
    docstrings: `on_tick` now also lets out a `BrokerStateAdapterError`.
  - [x] 4.4 `SqlSessionRecord.record_trading_withheld` → `SessionService.record_trading_withheld`.
    Unit-test the translation of `InvalidSessionTransition` → `SessionReclaimedError` beside the
    existing sibling tests.

- [x] **Task 5 — The durable document** (AC: #3) — new `src/services/reconcile_withheld.py`,
  `src/services/session_service.py`
  - [x] 5.1 Failing unit tests for the pure helpers (D-H):
    - upsert appends a new `start`, replaces the same `start`, and increments `count` only on
      append;
    - the cap keeps the most recent `WITHHELD_PERIODS_KEPT` and `count` stays the true total;
    - `close_open_period` closes only an open period and never re-closes a closed one;
    - `carried_forward` returns `None` when there is no history and closes an open period with the
      given `last_heartbeat_at` and `"process_lost"` (or `end: null` when there is no heartbeat);
    - malformed input (not a mapping, `periods` not a list) is tolerated, never raised;
    - every result is a new object (no aliasing of the input).
  - [x] 5.2 Failing unit tests in `tests/unit/services/test_session_service.py`:
    - `record_trading_withheld` refuses a non-`running` row and a foreign `owner_epoch`
      (`InvalidSessionTransition`);
    - it keeps every other `runtime_flags` key (`**existing`);
    - `-> stopped` closes an open period at the stop instant (`"session_stopped"`);
    - `-> running` (claim **and** reclaim) carries `reconcile_withheld` and clears every other key
      (`failed_strategies`, `order_rejections`, `all_failed`);
    - a reclaim closes a dead process's open period at the old `last_heartbeat_at`;
    - the existing "clears to `None`" tests stay green when there is no history.
  - [x] 5.3 Implement. Keep `_apply_transition` readable by calling the helpers. AR37 still holds:
    only `_apply_transition` assigns `status`.

- [x] **Task 6 — At seal** (AC: #3) — `src/services/session_conditions.py`
  - [x] 6.1 Failing unit tests in `tests/unit/services/test_session_conditions.py`:
    - `reconcile_withheld` is present on every record, `{"count": 0, "periods": []}` when the flags
      are `None` or lack it;
    - it is the stored sub-document as stored (deep copy, never judged), malformed included;
    - update `RULED_EXTRAS` (F11) deliberately;
    - `schema_version` is still `1`.
  - [x] 6.2 Implement D-I.
  - [x] 6.3 **Integration/db proof** (`tests/integration/db/`, the real test database, never beside a
    `--forked` integration run — memory):
    1. claim a session;
    2. write a withheld period's start through `SqlSessionRecord`;
    3. `mark_stopped` closes it;
    4. claim again (the history is carried, the other flags cleared);
    5. write a second period's start and its end;
    6. stop;
    7. `seal_session(...)` (the existing seal harness in `test_session_seal.py`).

    Assert `config_snapshot["session_conditions"]["reconcile_withheld"]` holds **both** periods with
    `start`, `end`, `cause`, `reason`, `error_type` and `ended_by`, and that the seal report's
    `--json` payload carries them.

- [x] **Task 7 — Docs and the operator procedure** (AC: all; NFR33)
  - [x] 7.1 `docs/qa/phase3-live-verification.md`: add **Procedure P23** (if a parallel story took
    P23, the integrator renumbers), "defined, not run", plus its row in the phase-gate evidence index.
    - **P23a — withhold and re-grant.** Run a session against the paper Gateway inside RTH. The
      operator **suspends the Gateway process** (`kill -STOP <java pid>`) for ~3 minutes, so the
      socket stays up while reads time out. Expect: three `reconcile.cycle_failed` cycles, one ERROR
      with `action=trading_withheld`, `order.suppressed` for any signal meanwhile, and possibly
      `connection.halted`. After `kill -CONT`: `reconcile.ok scope=reconnect`, then
      `connection.restored`. If the Gateway drops the API connection instead, that is P4/P18d's path:
      record "not arranged".
    - **P23b — drift.** Not arrangeable on demand. Observational: if ever seen, exit `1` with one
      ERROR `action=session_stopped`.
    - **P23c — the seal record.** Optional, throwaway session only, since sealing is permanent:
      after P23a, stop, then `live seal --json`, and read `session_conditions.reconcile_withheld`.

    List the automated proofs, as P22 does. No story session runs any of it.
  - [x] 7.2 `docs/agent/nautilus.md` (runtime reconciliation section) and README, if either
    describes failure handling: one paragraph on the withhold and the drift stop. Update CLAUDE.md
    (port 4 → 5; the carry-forward exception to the `runtime_flags` clear).
  - [x] 7.3 Disposition the routed debt in the Dev Agent Record: `deferred-work.md` story-5.4 (c)
    (closed for this key; the other keys stay last-run-only), PR #35 D3 (landed), and 5.7 W1 (a
    same-symbol unresolved row withholds the reconnect grant: not a cycle *failure*, so this story's
    rule does not touch it; say so).

- [x] **Task 8 — Gates** (all)
  - [x] 8.1 `uv run ruff check .` (and `--no-cache` before the commit: I001 hides behind a warm
    cache), `uv run ruff format --check .`, `make typecheck`.
  - [x] 8.2 `make test-unit`, `make test-component`, the size-cap guard, and the integration/db tests
    you touched (local DB). Run `make test-integration` core only if a live-path import changed.
  - [x] 8.3 A mutation sweep of the critical lines, at minimum:
    - `>= 3` → `>= 2` and `> 3`;
    - drop the drift branch;
    - drop the carry-forward;
    - drop the stop-edge close.

    Each must turn a test red. Copy files to the scratchpad; **never** `git stash` (memory).

### Review Findings

Code review 2026-10-11: three parallel layers (Blind Hunter, Edge Case Hunter, Acceptance
Auditor), with the auditor finding no blocking AC violation. After triage: 2 decision-needed, 9
patch, 2 defer, 5 dismissed. The PO ruled DN1 = (a) (now P10) and DN2 = (c) (deferred), and
had all patches applied. All 10 patches landed test-first, and a mutation sweep of them killed
10 of 10.

- [x] [Review][Decision] DN1 — A period's write that has not landed when the session stops is
  lost or overstated. There is no teardown flush (D-F).
  - **Lost start.** Its start write fails, or the stop cancels it, and the period is never
    recorded. This case is disclosed.
  - **Overstated end** (not disclosed). A clean cycle re-grants, the end write fails, and the
    `-> stopped` edge then closes the period at the stop instant (`session_stopped`). After a
    `kill -9` the reclaim closes it at the last heartbeat instead. Either way the sealed record
    says trading was withheld until the stop, though it resumed earlier, which breaks "an end is
    never invented".
  - **Options:** (a) add a synchronous teardown flush of the ledger, beside the 3.7
    `flush_rejection_snapshot`. It runs in the runner's `finally`, before `_finish_record`, and
    reopens D-F: the runner gets about 4 statements (file 493 → ≤ 497) and its class ceiling rises
    417 → ~421 with a reason, plus a `withheld` accessor on the steady state. (b) Keep D-F and
    disclose the overstated end beside the lost start.
  - Sources: blind, edge and auditor.
  - **PO ruling 2026-10-11: (a)**, landed as P10 below. The runner's size turned out unchanged
    (file 493, class 417): the existing teardown slot became `_flush_run_snapshots`, flushing both
    run snapshots through one module-level function. Only a `kill -9` can still lose a start or
    overstate an end (`deferred-work.md`).
- [x] [Review][Decision] DN2 — A standing same-symbol unresolved row turns a reconciliation
  withhold into one that lasts until the session stops.
  - **Today.** While `CONNECTED`, a runtime cycle with an outstanding `IB-CONID-*` row under a
    held or traded symbol does not withdraw permission, so trading continues.
  - **After a withhold.** Once three failed reads withhold, every reconnect cycle sees that row as
    outstanding and never grants. Story 5.7 W1 named the same trap for a real disconnect, and the
    PO owns it, "beside Story 5.8". This story makes it reachable through three failed reads.
  - **Options:** (a) keep it fail closed and log one ERROR naming the row or rows blocking the
    re-grant; (b) leave out rows already standing before the withdrawal when deciding this
    withhold's re-grant; (c) leave it as it is and record it beside 5.7 W1.
  - Source: edge (`live_runtime_reconcile.py` `_run`, `_act`).
  - **PO ruling 2026-10-11: (c)**, deferred beside 5.7 W1 (`deferred-work.md`, story-5.8
    section). Reason: same root cause, fails closed, decided together.
- [x] [Review][Patch] P1 — A real disconnect during a reconciliation withhold logs no
  `connection.lost`, the halt latch stays set, and `reconnect_seconds` includes the withheld time.
  Re-anchor at the real loss: log `connection.lost` and re-arm the halt.
  [src/core/live_connection_monitor.py `withhold_for_reconciliation`, `_observe_disconnected`]
- [x] [Review][Patch] P2 — The drift record has no NFR26 test (Task 2.1 said "either path"), and
  `_log_drift`'s docstring overclaims what it logs. [src/core/live_runtime_reconcile.py `_log_drift`]
- [x] [Review][Patch] P3 — AC #2's "stops with exit 1" has no runner-tier proof: add the drift
  sibling of `TestARuntimeStrategyContradictionStopsTheSession`, where `runner.run()` raises
  `BrokerStateAdapterError`, exits 1 and runs the ordinary teardown (`mark_stopped`).
  [tests/component/core/test_session_runner_runtime_reconcile.py]
- [x] [Review][Patch] P4 — An arbitrary exception's `.reason` (e.g. `URLError`'s free text) is
  now persisted to `runtime_flags` and the sealed record. Persist and log only the reader's own
  `BrokerStateFailure` values. [src/core/live_runtime_reconcile.py `_FailureStreak.note`]
- [x] [Review][Patch] P5 — The ERROR says `action=trading_withheld` when nothing was withdrawn
  (D-J), and it can repeat within one withheld episode past the throttle. ERROR only when this
  record withdrew; otherwise follow the WARNING throttle. [src/core/live_runtime_reconcile.py
  `_FailureStreak.note`]
- [x] [Review][Patch] P6 — The document helpers:
  - "open" is defined two ways (`not ended_by` against `is None`);
  - one late open start nulls every open period's end;
  - an end-bearing write can overwrite an edge's closure;
  - a malformed stored history is normalised at the stop and restart edges, not recorded as
    stored;
  - an edge end before the start is not treated as unknown.

  [src/services/reconcile_withheld.py]
- [x] [Review][Patch] P7 — A backward wall-clock step can stamp a ledger end before its start.
  Clamp it to the start. [src/core/live_runtime_reconcile.py `WithheldPeriods.end`]
- [x] [Review][Patch] P8 — Test hygiene:
  - `_HandWrittenRecord` writes into `rejections`;
  - the seal-edge test compares an object with itself;
  - the AC #4 tests assert only against literals, not also against the constant;
  - the integration stop runs through `SessionService` rather than
    `SqlSessionRecord.mark_stopped`.

  [tests/…]
- [x] [Review][Patch] P9 — Docs:
  - the port docstring's "every period of every run" ignores the cap of 50;
  - the adapter's "at most twice" ignores retries;
  - `session_conditions`' R2a line is stale under Q1;
  - the Dev Agent Record does not disclose the renamed names (`withheld_from`/`withheld_until`,
    `with_period`/`closed_on_stop`, the positional writer) or D-E 3's heartbeat-before-start →
    `null`.

  [src/core/live_session_record.py, src/services/session_record.py,
  src/services/session_conditions.py, this file]
- [x] [Review][Patch] P10 (from DN1 (a)) — The teardown writes any withheld period the tick could
  not, before `-> stopped` closes what it finds open.
  - `flush_run_snapshots` in the steady state flushes the refusal summary as before, then the
    ledger.
  - The runner's slot `_flush_order_rejections` was renamed `_flush_run_snapshots`; its size is
    unchanged.
  - New: a `SessionSteadyState.withheld` accessor (class ceiling 118 → 120, with a reason).
  - Proven at the runner tier: a tick write fails, then drift stops the session, and the period
    lands before `mark_stopped`.

  [src/core/live_session_steady_state.py, src/core/live_session_runner.py]
- [x] [Review][Defer] W1 — A `RECOVERING → LOST → RECOVERING` flap does not reset the reconnect
  debounce, so a row first seen before the loss can be acted on after it.
  [src/core/live_runtime_reconcile.py `on_tick`] — deferred, pre-existing: Story 4.3's
  behaviour for any reconnect flap, and `RuntimeReconciler` is at 99/100.
- [x] [Review][Defer] W2 — A sustained database outage logs one
  `session.withheld_record_failed` ERROR per tick. [src/core/live_session_steady_state.py
  `write_withheld_periods`] — deferred, pre-existing: the 3.7 `session.rejection_record_failed`
  precedent behaves identically.

Dismissed (5):
- **The stop edge invents an end for a dead process's period: unreachable.** The only production
  `-> stopped` caller is the owning process's epoch-fenced `SqlSessionRecord.mark_stopped`, and a
  dead process's open period is closed first by the reclaim edge (`process_lost`).
- **The drift log's attribute access could replace the drift.** `BrokerStateAdapterError` always
  carries those attributes, and any raise still stops the session with exit 1.
- **Ledger `start()` while a period is open:** unreachable.
- **`consecutive` on drift raised from `correct()`:** the cycle did fail, so the count holds.
- **A third failure with reason `connection_lost` while the monitor still reads `CONNECTED`:** the
  withdrawal fails closed, the reason is recorded, and P1 narrates the real loss.

## Dev Notes

### The current surface — exact extension points

- `src/core/live_runtime_reconcile.py`:
  - `_run` (`:342-370`): add the drift branch between `except ReconciliationFailedError` and
    `except Exception`;
  - `_grant` (`:413-425`): close the period when the grant leaves the monitor `CONNECTED`
    (`grant_after_reconciliation` returns the state, or `None` if the reading raised);
  - `_FailureStreak` (`:707-730`): the threshold policy;
  - `__init__` (`:280-309`): the ledger and `wall_clock`.
- `src/core/live_connection_monitor.py`: a method beside `confirm_state_reestablished` (`:266-285`).
- `src/core/live_session_steady_state.py`: `run()` (`:393-420`) — the call after `on_tick()`; the
  writer beside `write_rejection_snapshot` (`:114-164`).
- `src/services/session_service.py`:
  - `_apply_transition` (`:275-350`): the `-> running` clear at `:336-344`; read
    `last_heartbeat_at` **before** the `_TIMESTAMPS_BY_TARGET` loop at `:334` overwrites it;
  - `_record_order_rejections` (`:511-620`): the writer's shape to copy (guards, `FOR UPDATE`,
    rebuild).
- `src/services/session_conditions.py`: `build_session_conditions` (`:157-198`).

### Scope boundaries — do NOT build

- No migration, no new column/table/model/view (Epic 5 owns no migration; FR42/AR43).
- No change to `src/api/**`, `templates/**`, `src/services/backtest_query.py` (AR44).
- No change to `src/core/live_session_runner.py` (D-F).
- No new `live_*` module, so no guard-list edits are needed. If you find you need one, re-check all
  five lists in CLAUDE.md.
- No retry around the broker read and no order method anywhere (AR43). The withhold suppresses;
  it never cancels or flattens (NFR14).
- No `live status` change unless Q2 is ruled (b).
- Never run `live start`/`live seal` against a real session. P23 is written, not run.

### Hazards

- **In-place JSONB mutation silently never persists.** Rebuild and reassign, outer dict and
  sub-document both.
- **`last_heartbeat_at` is overwritten by the `-> running` edge** (`_TIMESTAMPS_BY_TARGET`). Read it
  first, or a dead process's period closes "now".
- **The ERROR at 3 must bypass the hourly throttle** (`count > 1 and within the hour → return`
  today). A test with the clock held still across all three failures catches this.
- **A `BrokerStateAdapterError` is a `BrokerStateUnavailableError`.** Order the `except` clauses
  narrow-to-wide, or drift is swallowed into the streak.
- **The monitor's `_recovering` reset.** After the withdrawal, the *next* tick sees `RECOVERING` and
  resets the debounce and the stale-order sightings. That is intended ("exactly as a reconnect").
  Do not suppress it.
- **Port doubles are `isinstance`-checked.** A double missing the fifth method fails the
  `runtime_checkable` assertion, not a call.
- **The wall clock is for persisted instants only.** The debounce, the throttle and `elapsed_ms`
  stay on the monotonic `clock`.

### Testing standards summary

- **Unit:** the monitor, the reconciler (duck-typed node/cache doubles from
  `test_live_startup_reconcile.py`, a real monitor, an injected clock), the pure document helpers,
  the service with its repository doubles, and the conditions.
- **Component:** the steady state's tick write.
- **Integration/db:** the claim → withhold → stop → claim → seal round trip.
- Every "nothing happened" assertion gets a sibling proving the same spy records when something
  does (Epic 2 retro).
- TDD: red first, per task.

### Project Structure Notes

- New file: `src/services/reconcile_withheld.py` (pure, stdlib only, JSON-native), and
  `tests/unit/services/test_reconcile_withheld.py`.
- Everything else edits existing files. `src/services/` already holds `session_conditions.py`'s
  pure vocabulary, so a shared pure helper belongs there, not under `src/models/`: it is a
  persistence document's shape, not a domain model.

### References

- `_bmad-output/planning-artifacts/prd-epic5-scope.md` — Story 5.8 ACs; NFR32/NFR33/NFR35; AR37,
  AR38, AR43, AR44.
- `_bmad-output/implementation-artifacts/deferred-work.md` — PR #35 D3 (problem statement and
  ruling), story-5.4 (a)/(c) (the session-lifetime flag history routed here), story-5.7 W1.
- `src/core/live_runtime_reconcile.py`, `src/core/live_connection_monitor.py`,
  `src/core/live_broker_state.py:113-163`, `src/core/live_session_steady_state.py`,
  `src/core/live_session_record.py`, `src/services/session_service.py`,
  `src/services/session_record.py`, `src/services/session_conditions.py`,
  `src/services/session_seal_service.py` (unchanged; the seal reads `runtime_flags` at `:363`).
- Story 3.7 (`3-7-keep-trading-after-a-rejection.md`) — the port-widening argument and the dirty-flag
  snapshot write.
- Story 5.4 / 5.5 — `session_conditions` rulings R1a/R2a/R3a, the P4 "recorded, not refused" rule.
- Story 5.7 — the most recent change to `RuntimeReconciler`; F7's size note ("keep ≤ 95, 5.8 extends
  the same class").

## Dev Agent Record

### Agent Model Used

claude-opus-5-5 (Claude Code, BMAD dev-story), 2026-10-11.

### Debug Log References

- **Red, then green, per task.** Each new test file or class was run red first (an import error or
  a missing attribute), then green. Exception: the adapter's `TestRecordTradingWithheld`, whose
  tests went in beside the implementation.
- **Size caps, measured with `measure_module`:**

  | Subject | Before | After | Note |
  |---|---|---|---|
  | `RuntimeReconciler` | 94 | **99** | ≤ 100, no baseline entry |
  | `ConnectionMonitor` | 116 | 123 | ceiling raised, with reason |
  | `SessionSteadyState` | 114 | 118 | ceiling raised, with reason |
  | `live_runtime_reconcile.py` (file) | 362 | 444 | |
  | `session_service.py` (file) | 275 | 322 | |
  | `LiveSessionRunner` | 417 | 417 | untouched |
  | runner file | 493 | 493 | untouched |

- **Guard lists re-checked (PO condition).** No new `live_*` module. The new imports in
  `live_runtime_reconcile.py` are `datetime` and `dataclasses.replace`, both already in
  `_STDLIB_AND_FIRST_PARTY`. `test_no_new_dependency_was_added_for_the_live_path` passes
  (`--forked`). Membership is unchanged:
  - `live_runtime_reconcile.py` is already in `NODE_FACING_MODULES` and `TestImportPurity.MODULES`,
    and deliberately not in `STOP_PATH_MODULES`: its drift stop raises, the
    `ReconciliationFailedError` precedent.
  - `live_session_steady_state.py` is already in `STOP_PATH_MODULES`; its forbidden-order-method
    scan passes.
  - `EXPECTED_CAPABILITIES` is unchanged: no repository method was added; the service mutates the
    locked row. Nothing writes `spec`.
- **Mutation sweep** (copies in `/tmp`, never `git stash`): 17 of 17 killed.
  - Threshold: `== 2`, `== 4`, constant → 4.
  - The drift branch dropped; the ERROR held behind the throttle; the withdrawal dropped; the
    grant never ending the period; the monitor not anchoring.
  - The tick never writing; the write moved before `on_tick`.
  - The carry-forward dropped; the stop-edge close dropped; the heartbeat read after the overwrite.
  - A start write reopening a closed period; no `open_at_seal`; the conditions dropping the
    history; no cap.

  The first M9 mutant was malformed (a truthy lambda left the `await` running) and was replaced
  by two valid ones, both killed.

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created.
- **PO rulings before dev (2026-10-11):** Q1 = (a), carry `reconcile_withheld` across
  `-> running`. Q2 = (a), no `live status` change. The design conditions are recorded under "PO
  rulings", and every one is met:
  - the fifth port method, with the pin, the doubles and the guard lists in the same change;
  - no `spec` write path and no new repository capability;
  - stop and restart closures are record-keeping only, and never grant;
  - a drift exit is never masked by a failed period write (component-tested);
  - every period carries start, end and cause, with `open_at_seal` explicit;
  - two failed cycles do not withhold and three do;
  - no AR44 file touched and no migration;
  - P23 defined, not run.
- **AC #1.** `ConnectionMonitor.withhold_for_reconciliation()` moves the session
  `CONNECTED → RECOVERING`, anchors unavailability, and reads `reconnect_seconds` as `0.0`. It has
  one production caller, pinned. The third consecutive failed cycle (`_FailureStreak`) withdraws
  permission and logs one `reconcile.cycle_failed` at ERROR (`action=trading_withheld`,
  `withdrawn`), past the hourly throttle. Orders are suppressed through the real order-path
  wrapper (unit-proven). The next clean reconnect cycle re-grants through
  `confirm_state_reestablished`, as for a reconnect: `reconcile.ok scope=reconnect`, then
  `connection.restored`.
- **AC #2.** `except BrokerStateAdapterError` in `_run`, ahead of the generic branch: one ERROR
  (`action=session_stopped reason=adapter_incompatible`, the reader's own `detail`), then a
  re-raise. Exit 1 comes via its own `exit_outcome`. This holds in both scopes and from
  `correct()`'s `find_ib_exec_client`, inside the throttle window or not.
- **AC #3.**
  - **The ledger.** `WithheldPeriods`/`WithheldPeriod` live on the reconciler, stamped by an
    injected UTC wall clock.
  - **The write.** `SessionSteadyState` writes pending periods right after `on_tick`, on its private
    executor, through the new port method `record_trading_withheld(withheld_from, withheld_until,
    reason, error_type, failed_cycles)` (`SqlSessionRecord` → `SessionService`).
  - **The document.** It lives in `runtime_flags["reconcile_withheld"]`
    (`src/services/reconcile_withheld.py`): an upsert by start, never reopened, capped at 50 with a
    true `count`.
  - **The edges.** The `-> stopped` edge closes an open period (`session_stopped`). The
    `-> running` edge carries the history (every other key still clears) and closes a dead
    process's open period at its last heartbeat (`process_lost`, or `null` when unknown). The
    seal records it as `session_conditions.reconcile_withheld`: as stored, an explicit empty
    document when absent, an open period marked `open_at_seal`.
  - **Proven against Postgres:** two runs, two periods, one sealed record; the carry and clear; a
    `kill -9` reclaim; a late write refused on a stopped row.
- **AC #4.** `WITHHOLD_AFTER_FAILED_CYCLES = 3`, pinned. Literal streak-of-2 and streak-of-3
  tests; mutants at 2 and 4 are both killed.
- **The runner is unchanged** (D-F). The stop edge closes periods durably instead of a teardown
  flush.
- **Routed debt, dispositioned** (`deferred-work.md`, "Deferred from: story-5.8"):
  - PR #35 D3: landed.
  - story-5.4 (c): closed for this key; the other keys stay last-run-only.
  - story-5.7 W1: not a cycle failure, so not touched; the owner stays the PO.
  - Recorded with owners: the Q2 deferral; the lost-start-write residual; the drift catch-all
    breadth; D-J outage withholds unrecorded; P23 not run.
- **Gates (before review):** ruff, format and mypy clean; unit 3950; component 2009 passed, 16
  skipped; integration/db 160; integration/core epic-1 AC 18 (`--forked`).
- **Code review (2026-10-11).** Three layers; the PO ruled DN1 = (a) and DN2 = (c) and had every
  patch applied. All ten landed test-first (see Review Findings):
  - **P1:** the monitor narrates and re-anchors a real loss during a withhold.
  - **P2:** an NFR26 pin on both drift paths.
  - **P3:** a runner-tier drift stop, exit 1.
  - **P4:** only `BrokerStateFailure` reasons are logged or persisted.
  - **P5:** an ERROR only on a real withdrawal.
  - **P6:** the document helpers — one "open", a per-period end, edge closures final, malformed
    passed through, an end before the start → `null`.
  - **P7:** the ledger end clamped to its start.
  - **P8:** test hygiene, the literal and constant AC #4 tests, and an integration stop via
    `SqlSessionRecord.mark_stopped`.
  - **P9:** docs.
  - **P10:** the teardown flush.
- **Departures from the drafted Dev Notes, disclosed:**
  - **Names.** The port's keyword arguments are `withheld_from`/`withheld_until`, not
    `started_at`/`ended_at`, so they cannot be read as the claim's `started_at` that the port
    deliberately never takes. The document helpers are `with_period`/`closed_on_stop` (plus private
    `_closed`), not `upsert_period`/`close_open_period`. The tick writer is
    `write_withheld_periods(record, ledger, executor, log)`; it reads the loop itself.
  - **D-E 3, extended.** An edge's end before a period's start — a heartbeat from before the
    withdrawal, or a clock step — is recorded as unknown (`null`), judged per period. It is never
    an end before the start.
- **Size after review:**

  | Subject | Size | Ceiling |
  |---|---|---|
  | `ConnectionMonitor` | 128 | raised 123 → 128, with a reason |
  | `SessionSteadyState` | 120 | raised 118 → 120, with a reason |
  | `RuntimeReconciler` | 99 | no entry |
  | `LiveSessionRunner` | 417 | unchanged; runner file 493 |
  | `live_runtime_reconcile.py` (file) | 437 | |

- **Mutation sweeps:** the original 17 and the 10 review mutants, all killed. M1/M2/M5 were re-run
  after P5 reworded their line.
- **Gates (after review):**
  - `ruff check --no-cache .`, `ruff format --check .` and `make typecheck` all clean.
  - `make test-unit`: 3967 passed.
  - `make test-component`: 2017 passed, 16 skipped.
  - `tests/integration/db`: 161 passed.
  - integration/core epic-1 AC: 18 passed (`--forked`).
  - The AC summary reports 5.8a–d all PASS.

### File List

- `_bmad-output/implementation-artifacts/5-8-act-on-a-persistent-runtime-reconciliation-failure.md` (new)
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `CLAUDE.md`
- `README.md`
- `docs/agent/nautilus.md`
- `docs/qa/phase3-live-verification.md`
- `src/core/live_connection_monitor.py`
- `src/core/live_runtime_reconcile.py`
- `src/core/live_session_record.py`
- `src/core/live_session_runner.py` (code review P10: the teardown slot `_flush_run_snapshots`, size unchanged)
- `src/core/live_session_steady_state.py`
- `src/db/models/trading_session.py` (comments only)
- `src/services/reconcile_withheld.py` (new)
- `src/services/session_conditions.py`
- `src/services/session_record.py`
- `src/services/session_service.py`
- `tests/component/core/test_session_runner_order_path.py`
- `tests/component/core/test_session_runner_phases.py`
- `tests/component/core/test_session_runner_runtime_reconcile.py`
- `tests/component/core/test_session_runner_stop.py`
- `tests/component/core/test_session_runner_strategy_failure.py`
- `tests/component/core/test_session_runner_warmup.py`
- `tests/component/core/test_session_steady_state.py`
- `tests/integration/db/test_session_seal.py`
- `tests/unit/core/test_live_connection_monitor.py`
- `tests/unit/core/test_live_runtime_reconcile.py`
- `tests/unit/core/test_live_session_record.py`
- `tests/unit/governance/test_size_caps.py`
- `tests/unit/services/test_reconcile_withheld.py` (new)
- `tests/unit/services/test_session_conditions.py`
- `tests/unit/services/test_session_record_adapter.py`
- `tests/unit/services/test_session_seal_service.py`
- `tests/unit/services/test_session_service.py`

## Change Log

- 2026-10-11: Story drafted (create-story); PO rulings Q1 (a) and Q2 (a) recorded with their design
  conditions; implemented (dev-story): withhold after 3 failed cycles, drift stops, withheld periods
  persisted across runs and recorded at seal; P23 defined, not run. Status → review.
- 2026-10-11: Code review (Blind Hunter / Edge Case Hunter / Acceptance Auditor): 2 decisions
  ruled by the PO (DN1 = a, the teardown flush; DN2 = c, deferred beside 5.7 W1); 10 patches
  applied test-first (P1–P10); 2 deferred (W1, W2); 5 dismissed. Status → done.
