# Story 1.6: Detect Connection Loss and Withhold Trading Permission

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want the system to know when the broker connection has dropped and to stop considering itself
permitted to trade,
so that no order can ever be sent into a connection that is not there.

## Acceptance Criteria

1. **Given** an established broker connection, **When** the connection drops, **Then** a
   `connection.lost` event is logged with the session identifier bound (FR6, AR41) **And** the
   trading-permitted state becomes `False`.
2. **Given** the connection is re-established, **When** recovery completes, **Then**
   `connection.restored` is logged and trading permission is restored **only after state is
   re-established** — a reconnected socket alone never restores permission (NFR10).
3. **Given** a transient disconnect, **When** reconnection is measured, **Then** the elapsed
   downtime is observable in the logs, carried on `connection.restored`, and reported against the
   60-second reconnect window (NFR4).
4. **Given** IB Gateway performs its scheduled daily restart, **When** the session observes the
   disconnect, **Then** it is handled on the normal reconnect path — nothing raises, the event is
   logged at warning (not error) level, and the monitor returns to its exact prior state after the
   cycle so an unbounded number of such cycles is normal operation (NFR19).
5. **Given** the broker is unreachable for longer than the reconnect window, **When** the session
   evaluates its state, **Then** it halts trading, reports the halt at error level, and never
   proceeds on stale state — no observation of a *disconnected* source can ever produce
   `trading_permitted is True` (NFR20).
6. **Given** this story's whole diff, **When** it is reviewed, **Then** it introduces **no
   order-submission code path** (Epic 1 has none by design — the flag ships before the capability
   it constrains) **And** no new dependency is added (`pyproject.toml` / `uv.lock` unchanged, AR3)
   **And** the safety gate in `src/core/live_gate.py` is untouched.

## Tasks / Subtasks

- [x] **Task 1: Write the failing unit tests for the connection state machine (TDD Red)** (AC: #1–#5)
  - [x] New file `tests/unit/core/test_live_connection_monitor.py`. **Unit tier** — the module under
        test imports no `nautilus_trader`, exactly like `src/core/live_gate.py`. `pytest.ini:25`
        defines unit as "Pure Python unit tests (no Nautilus)"; honour it.
  - [x] Mark every test `@pytest.mark.unit`. `--strict-markers` is on (`pytest.ini:15`).
  - [x] **Deterministic time.** Never call `time.monotonic()` in a test. Inject a fake clock via the
        monitor's `time_source` parameter — a tiny list-backed or closure-backed counter the test
        advances explicitly. A test that sleeps to cross the reconnect window is a flaky test.
  - [x] **Capture logs with `structlog.testing.capture_logs`.** In-repo precedent:
        `tests/unit/services/test_kraken_settings.py:8,336`. Assert on the `event` key
        (`"connection.lost"`, `"connection.restored"`, `"connection.halted"`), the bound
        `session_id`, and `log_level`.
  - [x] **AC #1 — loss.** From a connected+confirmed monitor, one `observe(disconnected)`:
        `state is ConnectionState.LOST`, `trading_permitted is False`, exactly one
        `connection.lost` event, `session_id` present on it.
  - [x] **AC #1 — the log is emitted once, not per poll.** `observe(disconnected)` ten times in a
        row still yields exactly one `connection.lost`. The monitor is polled on an interval; a
        per-poll log would bury the session's log stream.
  - [x] **AC #2 — the two-step recovery, which is the heart of this story.** Three assertions:
        1. `observe(connected)` after a loss moves to `RECOVERING` and `trading_permitted` is still
           **`False`** — and **no** `connection.restored` has been logged yet.
        2. `confirm_state_reestablished()` then moves to `CONNECTED`, `trading_permitted` is `True`,
           and `connection.restored` is logged exactly once.
        3. `confirm_state_reestablished()` called while the last observation was *disconnected*
           (state `LOST` or `HALTED`) does **not** grant permission — it logs
           `connection.recovery_refused` at warning and leaves the state unchanged.
  - [x] **AC #2 — the permission invariant, asserted exhaustively.** Loop over every member of
        `ConnectionState` and assert `trading_permitted` is `True` for `CONNECTED` and `False` for
        every other member. This is the story's load-bearing safety property; assert it against the
        enum so a future state added without thought fails the test rather than silently permitting
        trading.
  - [x] **AC #3 — elapsed time.** With the fake clock advanced 12.5s between the loss and the
        confirmation, `connection.restored` carries `downtime_seconds == 12.5` and
        `within_reconnect_window is True`. Repeat past the window and assert
        `within_reconnect_window is False` — the event is still emitted, because a slow reconnect
        must be *visible*, not suppressed.
  - [x] **AC #4 — daily restart.** A full disconnect → reconnect → confirm cycle raises nothing,
        emits `connection.lost` at `warning` and `connection.restored` at `info` (assert the levels
        — "not raised as an error condition" is the AC's actual wording), and leaves the monitor in
        a state byte-identical to its pre-cycle state. Run the cycle three times in one test to
        prove it is repeatable rather than one-shot.
  - [x] **AC #5 — halt.** Advance the fake clock past `reconnect_window_seconds` while still
        disconnected, `observe(disconnected)` again → `state is HALTED`, `trading_permitted is
        False`, `connection.halted` logged exactly once at `error`, and further disconnected
        observations neither re-log nor change state.
  - [x] **AC #5 — halt is escapable only through the same two-step path.** From `HALTED`,
        `observe(connected)` → `RECOVERING` (still not permitted), then
        `confirm_state_reestablished()` → `CONNECTED`. A halt is not a dead end; it is a withdrawal
        of permission that only re-established state can reverse.
  - [x] **Startup is not a loss.** A brand-new monitor is in `AWAITING_CONNECTION` with
        `trading_permitted is False`; `observe(disconnected)` on it logs **no** `connection.lost`
        (nothing was lost) and starts no downtime clock. "Gate passed but the broker was never
        reachable" is Story 1.7's exit-code-4 concern, not a lost connection.
  - [x] Run `uv run pytest tests/unit/core/test_live_connection_monitor.py -v` and **record the RED
        output** in Debug Log References before writing Task 2.
- [x] **Task 2: Implement the connection monitor** (AC: #1–#5)
  - [x] New file `src/core/live_connection_monitor.py`. **Imports no `nautilus_trader`, no
        SQLAlchemy, no I/O library** — same purity contract as `live_gate.py`, and the reason this
        is unit-tier. `structlog` is the only third-party import.
  - [x] Module docstring states what it owns (the connection state machine, the trading-permission
        flag, the loss/restore/halt events) and what it does not (polling schedule and the decision
        to *call* it belong to Epic 2's runner, AR38; re-establishing state is Epic 4's
        reconciliation; consuming the flag on an order path is Epic 3).
  - [x] Public surface:
        ```python
        class ConnectionState(str, Enum):
            AWAITING_CONNECTION = "awaiting_connection"
            CONNECTED = "connected"
            RECOVERING = "recovering"
            LOST = "lost"
            HALTED = "halted"

        @dataclass(frozen=True)
        class ConnectionStatus:
            connected: bool
            detail: str          # why — carried into the log line, never into a decision

        DEFAULT_RECONNECT_WINDOW_SECONDS = 60.0   # NFR4

        class ConnectionMonitor:
            def __init__(
                self,
                *,
                session_id: str,
                reconnect_window_seconds: float = DEFAULT_RECONNECT_WINDOW_SECONDS,
                time_source: Callable[[], float] = time.monotonic,
            ) -> None: ...
            @property
            def state(self) -> ConnectionState: ...
            @property
            def trading_permitted(self) -> bool: ...
            @property
            def downtime_seconds(self) -> float | None: ...
            def observe(self, status: ConnectionStatus) -> ConnectionState: ...
            def confirm_state_reestablished(self) -> ConnectionState: ...
        ```
        ⚠️ **Amended by code review 2026-08-09.** The shipped surface additionally takes
        `max_observation_age_seconds` (Decision 2), exposes `observation_is_stale`,
        `observation_age_seconds` and `reconnect_seconds`, validates `session_id` and both
        intervals in `__init__`, and — the breaking change — takes the reading as an argument:
        `confirm_state_reestablished(status: ConnectionStatus) -> ConnectionState` (Decision 3).
  - [x] **`trading_permitted` is derived, never stored:**
        ```python
        @property
        def trading_permitted(self) -> bool:
            return self._state is ConnectionState.CONNECTED
        ```
        A stored boolean can drift out of step with the state machine on a path someone forgets to
        update. A derived one cannot. This single line is the story's safety contract — do not
        replace it with an assignment anywhere.
  - [x] **`session_id` is a required keyword argument with no default**, bound once at construction:
        `self._log = structlog.get_logger(__name__).bind(session_id=session_id)`. Epic 2 owns
        *deriving* session identity (AR10); this only binds one it is handed — the same posture
        `build_trading_node`'s `trader_id` takes.
  - [x] **Transition table.** ⚠️ **Superseded by code review 2026-08-09** — the table below is the
        table as originally specified, kept because Decision 1 is a deviation *from* it and the
        deviation is the point. Two cells were wrong and both were exploitable: `RECOVERING` on a
        disconnected reading restarted the downtime clock (so a flapping gateway never halted), and
        the halt was reachable only from `LOST` (so an unconfirmed recovery stalled forever). The
        shipped table is in the module docstring and in Decision 1 below.
        | From | `observe(connected=False)` | `observe(connected=True)` | `confirm_state_reestablished()` |
        |---|---|---|---|
        | `AWAITING_CONNECTION` | stay (no log, no clock) | `RECOVERING` | refuse + `connection.recovery_refused` |
        | `CONNECTED` | `LOST` + `connection.lost` + start clock | stay | stay (already permitted) |
        | `RECOVERING` | `LOST` + `connection.lost` + start clock | stay | `CONNECTED` (+ `connection.restored` if a clock is running) |
        | `LOST` | `HALTED` once clock > window (+ `connection.halted`), else stay | `RECOVERING` | refuse + `connection.recovery_refused` |
        | `HALTED` | stay | `RECOVERING` | refuse + `connection.recovery_refused` |
  - [x] **Shipped transition table (post-review).** Unavailability is anchored once and cleared only
        by a successful confirmation; the halt deadline is evaluated after *every* observation,
        wherever the state sits; and `confirm_state_reestablished(status)` folds its own reading in
        first, so it is `observe` + grant rather than a separate inspection of cached state.
        | From | `observe(connected=False)` | `observe(connected=True)` |
        |---|---|---|
        | `AWAITING_CONNECTION` | stay (no log, no clock) | `RECOVERING` |
        | `CONNECTED` | `LOST` + `connection.lost` + **anchor** unavailability | stay |
        | `RECOVERING`, never yet connected | back to `AWAITING_CONNECTION` (no log, no clock) | stay |
        | `RECOVERING`, previously connected | `LOST`, anchor **untouched**, no second `connection.lost` | stay |
        | `LOST` | stay | `RECOVERING` (+ record the reconnect instant) |
        | `HALTED` | stay | `RECOVERING` |
        Then, on every observation: if an outage is anchored, unreported, and older than the
        window → `HALTED` + `connection.halted` (latched, so it reports once and a halt stays
        escapable). `confirm_state_reestablished(status)` grants only from `RECOVERING`, and the
        grant clears the anchor, the reconnect instant and the halt latch.
  - [x] **The first connection logs nothing.** `AWAITING_CONNECTION → RECOVERING → CONNECTED` emits
        no `connection.restored`, because nothing was restored — the downtime clock is `None`. The
        startup path's own logging is AR39's phase sequence, owned by Epic 2's runner. Do not emit a
        phase event, a `connection.established`, or anything else from here.
  - [x] **Logging surface — exactly four events, no more:**
        | Event | Level | Fields |
        |---|---|---|
        | `connection.lost` | `warning` | `detail`, `state` |
        | `connection.restored` | `info` | `downtime_seconds`, `within_reconnect_window`, `reconnect_window_seconds` |
        | `connection.halted` | `error` | `downtime_seconds`, `reconnect_window_seconds` |
        | `connection.recovery_refused` | `warning` | `state` |
        `session_id` rides on all four via the bound logger. **`connection.lost` is warning, not
        error** — that is AC #4 (NFR19) expressed in code: the Gateway's scheduled daily restart is
        an expected event on the normal path. The halt, which withdraws permission for an
        abnormally long outage, is the error.
  - [x] **Never log the account, host, or port from this module.** It has no reason to know them,
        and NFR26 masking is `mask_account`'s job on the paths that do. Do not import
        `IBKRSettings` here.
  - [x] **Never raise from `observe()` or `confirm_state_reestablished()`.** A monitor that raises
        into a runner's poll loop turns a survivable disconnect into a dead session — the precise
        opposite of NFR19/NFR20. Both methods return the resulting `ConnectionState`.
  - [x] Green: the Task 1 tests pass. Target ~180 lines; the CLAUDE.md limits (file <500, function
        <50, class <100 lines) are hard.
- [x] **Task 3: Write the failing component tests for the IBKR status reader (TDD Red)** (AC: #1, #5)
  - [x] New file `tests/component/core/test_live_connection_probe.py`, every test
        `@pytest.mark.component`. Component tier because it imports the IB adapter; **verified this
        session** that constructing an `InteractiveBrokersClient` leaves `is_logging_initialized()`
        at `False`, so the non-forked parallel tier is safe (same justification
        `test_live_node_builder.py` documents for config assembly). Add the same autouse
        C-logging-state guard fixture that file uses.
  - [x] New broker double `tests/component/doubles/test_ib_connection.py` exporting
        `TestIBConnection`, re-exported from `tests/component/doubles/__init__.py` alongside
        `TestOrder` / `TestPosition` / `TestTradingEngine`. Architecture requires broker doubles to
        live here (`architecture.md#Structure-Patterns`: "broker doubles extend
        `tests/component/doubles/`"). The double mimics only what the reader touches:
        `_is_ib_connected` and `_is_client_ready`, both `asyncio.Event`-shaped (an object with
        `is_set()` is enough — do not import asyncio machinery into the double if a two-line stub
        does it). Note the repo convention that doubles are `Test*`-named in `test_*.py` files;
        pytest declines to collect them because they define `__init__`, and warnings are disabled.
  - [x] **The reader's contract:** `read_ibkr_connection_status(settings) -> ConnectionStatus`,
        looking the client up in the adapter's module-level cache by the *same* key the node builder
        configured. Tests to write:
        1. **No client registered** → `connected is False`, `detail` names the missing client. This
           is the fail-closed case and the most important one: it is what an unbuilt node, a
           mistyped port, or a Nautilus upgrade that moves the cache all look like.
        2. **Client registered, socket up, client ready** → `connected is True`.
        3. **Socket down** (`_is_ib_connected` clear) → `connected is False`, `detail` says so.
        4. **Socket up but client not ready** (`_is_client_ready` clear — the adapter's own
           post-reconnect handshake window) → `connected is False`.
        5. **Key sensitivity** — a client cached under a *different* `client_id` is not found. Prove
           the reader keys on `ibkr_live_client_id`, not on "any IB client in the process": the
           historical data client (`ibkr_client_id`) shares this cache dict.
        6. **Missing attributes** — an object registered in the cache that has neither flag returns
           `connected is False` rather than raising `AttributeError`. A `getattr`-based read is not
           defensive padding here; it is the difference between "withhold permission" and "kill the
           session" when a Nautilus upgrade renames a private field.
  - [x] **Always restore the cache.** `IB_CLIENTS` is a module-level global
        (`adapters/interactive_brokers/factories.py:42`) that leaks across tests in the shared,
        parallel component tier. Use a fixture that inserts the double and removes it in teardown —
        `monkeypatch.setitem` / `delitem` is the least error-prone form.
  - [x] **Canary test against the real class.** Construct a real `InteractiveBrokersClient` (cheap —
        it needs only a loop, `MessageBus`, `Cache`, `LiveClock`; verified no C logging init) and
        assert it still exposes `_is_ib_connected` and `_is_client_ready`, both with `is_set()`.
        Without this, a Nautilus upgrade that renames either flag turns the reader into a silent
        permanent `connected=False` — safe, but invisible, and it would take the session down with
        no diagnosis. The canary makes it a test failure that names the field.
  - [x] Record the RED output before Task 4.
- [x] **Task 4: Implement the IBKR status reader** (AC: #1, #5)
  - [x] Add `read_ibkr_connection_status(settings: IBKRSettings) -> ConnectionStatus` to
        **`src/core/live_node_builder.py`**, importing `ConnectionStatus` from
        `live_connection_monitor`. It goes there, not in the monitor, for two reasons: the monitor
        must stay Nautilus-free (that is what makes it unit-tier), and the cache key
        `(ibkr_host, ibkr_port, ibkr_live_client_id)` is the very triple this module hands to both
        client configs — co-locating them means the lookup key can never drift from the configured
        one. Extend the module docstring's "Owns" sentence accordingly.
  - [x] Implementation shape:
        ```python
        client = IB_CLIENTS.get((settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id))
        if client is None:
            return ConnectionStatus(connected=False, detail="no ib client registered for ...")
        socket_up = _event_is_set(client, "_is_ib_connected")
        client_ready = _event_is_set(client, "_is_client_ready")
        ...
        ```
        where `_event_is_set` is a small module-private helper doing a `getattr` + `is_set()` inside
        a `try`, returning `False` on anything unexpected. Keep the `detail` strings short and
        specific — they land in `connection.lost`'s log line and are the operator's first clue.
  - [x] **Do not mutate the client, do not call `connect()`/`disconnect()`/`start()`/`stop()`, do
        not `await` anything.** This is a read. The reader is safe to call from a synchronous poll
        and must never influence the connection it is measuring.
  - [x] Green: the Task 3 tests pass.
- [x] **Task 5: Operator verification procedure** (AC: #1, #2)
  - [x] New `scripts/diagnostics/live_connection_probe.py`, following
        `scripts/diagnostics/live_node_probe.py`'s shape exactly — module docstring with a Usage
        block, `argparse`, `load_dotenv` inside `main()` (never at import), an explicit
        `asyncio.new_event_loop()` the script owns end to end, a `finally`-driven shutdown, and one
        parseable `RESULT: ok|fail ...` line on stdout with tracebacks to stderr. **Do not modify
        `live_node_probe.py` or `ibkr_reconnect_probe.py`** — they are other stories' evidence.
  - [x] What it proves, read-only and gate-checked: build a node (the gate runs first, so a
        non-paper configuration is refused before any socket), connect, then
        1. `read_ibkr_connection_status()` reports `connected=True` and `monitor.observe()` →
           `RECOVERING`, `trading_permitted is False` — permission is *not* granted by a live socket;
        2. `confirm_state_reestablished()` → `CONNECTED`, `trading_permitted is True`;
        3. `node.stop()` — a genuine, clean disconnect — then read again: `connected=False`,
           `monitor.observe()` → `LOST`, `trading_permitted is False`, `connection.lost` in the log.
        Step 3 is the real payoff: it is an actual observed disconnect against a real Gateway,
        obtained **without** killing anything.
  - [x] **Submits no orders, subscribes to no data, and never touches `--real-money`,
        `NTRADER_REAL_MONEY_ACCOUNT`, `.env`, or docker.** Epic 1 has no order path; if this script
        grows one, the story has gone wrong.
  - [x] Append **Procedure P2** to `docs/qa/phase3-live-verification.md`, matching P1's structure
        (Introduced by / Verifies / Tool / Preconditions / Command / Expected output / failure-mode
        table / Pass criteria / Result log). Do not restructure P1 or its result log.
  - [x] P2 is **informational evidence, never a blocking gate** — AC #1/#2 are proven by the
        automated unit and component tests, which is what NFR32/NFR34 require. If no Gateway is
        available in this session, say so plainly in Completion Notes and record "not run" in the
        result log. A dry run is not a pass.
- [x] **Task 6: Verify and record** (AC: #6 and the repo gates)
  - [x] `uv run ruff check .`, `make format`, `make typecheck` (covers `src/core`, so **both**
        touched modules are type-checked — annotate every signature).
  - [x] `make test-unit` — baseline **1561 collected**, expect 1561 + N.
  - [x] `make test-component` — baseline **853 collected** (837 passed + 16 skipped), expect 853 + N.
  - [x] `make test-integration` — this story adds no integration test. Expect the *pre-existing*
        failures unchanged; compare against a baseline run rather than assuming zero (Story 1.3
        measured 144 passed / 25 failed / 2 skipped, of which 2 failures are its own tests hitting
        the documented `src/api/web.py` collection-order hazard). Do not "fix" that here.
  - [x] **AC #6, three greps, each of which must be empty or explained:**
        `git diff --stat pyproject.toml uv.lock` (empty); `git diff src/core/live_gate.py` (empty);
        `grep -rn "submit_order\|OrderFactory\|MarketOrder" src/core/live_connection_monitor.py
        scripts/diagnostics/live_connection_probe.py` (no matches).
  - [x] Append any non-blocking findings to `_bmad-output/implementation-artifacts/deferred-work.md`
        under a new `## Deferred from: story-1.6` heading — including, at minimum, the
        `_is_ib_connected` private-attribute dependency recorded in Dev Notes, which Epic 4 will
        want to know about when it wires reconciliation into the recovery path.
  - [x] Fill in Dev Agent Record: Debug Log References (RED evidence for Tasks 1 and 3, plus the
        verification table), Completion Notes List, File List.
  - [x] Update **only** the `1-6-detect-connection-loss-and-withhold-trading-permission` key in
        `sprint-status.yaml`. Never touch `epic-1` or any other story key.

### Review Findings

Code review 2026-08-09. Three adversarial layers (Blind Hunter — diff only, no spec, no project
access; Edge Case Hunter — diff + project + installed wheel, findings verified by execution;
Acceptance Auditor — diff + spec + context docs). 34 raw findings → **3 decisions / 17 patches /
3 deferred / 3 dismissed** after dedup and triage.

**Decisions — the correct fix is ambiguous without a ruling. All three are the same family: how
much of the runtime-safety burden this story carries versus how much it hands to Epic 2's runner.**

**All three ruled by Allay 2026-08-09: option (a) in each case — the fail-closed reading, matching
the posture the rest of Epic 1 takes. Each became a patch and is applied.** The net effect is that
the monitor now defends itself rather than trusting its caller: the halt fires on *unavailability*
rather than on an uninterrupted disconnection, permission expires if nobody is polling, and the
confirmation carries its own reading. Decision 3 was ruled a **deliberate breaking signature
change** — no no-arg fallback was kept, and every caller in this story's scope passes a fresh
status.

- [x] [Review][Decision] **The halt clock is defeated by a flapping connection, and is never
      evaluated at all while `RECOVERING`.** Two verified defects with one root cause — `_lost_at`
      is reset on *every* entry to `LOST`, and the deadline check sits only in the `LOST` branch.
      (a) *Flapping*: `DOWN`(t=0) → `UP`(t=30, → `RECOVERING`, `_lost_at` untouched) →
      `DOWN`(t=31, `_lost_at` reset to 31). Edge Case Hunter drove 200 cycles of "50s down, 1s
      blip up" — 10,200s (~2.8h) of an unusable link — and the monitor ended in `RECOVERING` with
      `downtime_seconds == 51.0`, having **never** emitted `connection.halted`. NFR20's halt never
      fires for the most common real failure mode. (b) *Unconfirmed recovery*: `LOST` →
      `observe(UP)` → `RECOVERING`, and `confirm_state_reestablished()` is never called (Epic 4
      does not exist yet; or reconciliation raised). Clock advanced 24h and polled 50 more times:
      state `RECOVERING`, `downtime_seconds == 86410.0`, no `connection.halted`, no log line at
      all. The class docstring claims polling is what stops an outage that "simply persists" from
      going unnoticed; this is a persisting outage that goes unnoticed. **Why this is a ruling and
      not a patch:** the story's own prescribed transition table (Task 2) specifies exactly this
      behaviour — `RECOVERING` is in the reset set, and the halt is only reachable from `LOST`. Any
      fix deviates from the spec. **Options:** (a) anchor the clock on "unavailable since", cleared
      only by a successful `confirm_state_reestablished()`, and evaluate the halt deadline on every
      `observe()` including in `RECOVERING`; (b) fix only the flapping reset and leave `RECOVERING`
      un-deadlined; (c) accept as specified and defer both to Epic 2's runner, which owns the poll
      loop. [src/core/live_connection_monitor.py:191,204]
- [x] [Review][Decision] **`trading_permitted` has no staleness bound, so a dead poller leaves
      permission granted forever.** Verified: after `observe(UP)` → `confirm_state_reestablished()`
      the monitor is `CONNECTED`, and if the runner's poll task dies (unhandled exception,
      `CancelledError`, loop starvation) nothing can ever withdraw permission — `observe()` is the
      only thing that withdraws it. The monitor stores no `last_observed_at` and the property
      applies no max age. The module's stated safety contract — "no sequence of observations of a
      disconnected source may produce `trading_permitted is True`" — is bypassed entirely by *no*
      observations, which is the one shape none of the 31 unit tests cover. All three review layers
      raised it independently. The class docstring names the hazard and ships no defence; it is
      recorded in `deferred-work.md` as Epic 2's job. **Options:** (a) add
      `max_observation_age_seconds` + `last_observed_at` here and make `trading_permitted` return
      `False` on a stale reading (fail-closed by construction, no caller cooperation needed);
      (b) expose `last_observed_at` read-only and leave enforcement to Epic 2's runner;
      (c) accept as-is — the gap is declared, and AR38 gives the runner the poll loop.
      [src/core/live_connection_monitor.py:117]
- [x] [Review][Decision] **`confirm_state_reestablished()` grants permission from cached state, not
      from a fresh reading.** Verified: `observe(DOWN)` → `observe(UP)` → socket dies again between
      that poll and the caller's confirmation → `confirm_state_reestablished()` still returns
      `CONNECTED` with `trading_permitted is True` on a dead socket, and stays true until the next
      poll tick — a whole poll interval of blind trading. The method inspects only `self._state`
      and takes no argument, so a caller *cannot* confirm atomically against a live reading even if
      it wanted to. This is the seam Epic 4's reconciliation will call, and reconciliation is not
      instantaneous (NFR5 allows 30s), so the window is real rather than theoretical. **Options:**
      (a) change the signature to `confirm_state_reestablished(status: ConnectionStatus)` and
      refuse unless it is connected, making the grant atomic with a reading; (b) add an optional
      `status` parameter, keeping the current call shape working; (c) leave the signature and
      document the precondition that callers must `observe()` immediately before confirming.
      [src/core/live_connection_monitor.py:141]

**Patches (fix is unambiguous):**

- [x] [Review][Patch] `reconnect_window_seconds` is unvalidated — `float("nan")` silently disables
      the halt forever (`downtime > nan` is always `False`; verified over 1e9s of downtime),
      `-5.0` halts on the second poll with `downtime_seconds=0.0`, `0.0` halts on any non-zero
      downtime, `inf` never halts. The sibling module already rejects exactly this class of input
      (`_validate_timeouts`, `live_node_builder.py:102`), and the story documents Epic 2's runner
      as a future caller that may pass its own value. [src/core/live_connection_monitor.py:98]
- [x] [Review][Patch] A backward time step produces negative downtime and suppresses the halt —
      `time_source` is an unconstrained `Callable[[], float]`; stepping the clock back 3600s
      mid-outage yielded `downtime_seconds == -3600.0`, five further disconnected polls, and no
      halt (verified). `connection.restored` would then report a negative downtime with
      `within_reconnect_window=True`. Clamp at zero, so a backward step can only shorten the
      *report*, never defeat the halt. [src/core/live_connection_monitor.py:129]
- [x] [Review][Patch] `downtime_seconds`' docstring is false — "Seconds since the connection was
      lost, **or None if it is not down**", but `_lost_at` is cleared only in
      `confirm_state_reestablished()`, so throughout `RECOVERING` (where the last reading said
      *connected*) it returns a growing number. A consumer using `downtime_seconds is not None` as
      "is down" reads it backwards. [src/core/live_connection_monitor.py:129]
- [x] [Review][Patch] `_flag_is_set`'s fail-closed guarantee has a hole and zero test coverage —
      `getattr` suppresses only `AttributeError`, so a cache entry exposing `_is_ib_connected` as a
      *property that raises* propagates straight out of `read_ibkr_connection_status()` into the
      caller's poll loop, contradicting both "never raises" and "fail-closed on every uncertainty".
      Separately, both tests that claim to exercise the `except` arm (`object()` and
      `TestIBConnectionWithoutFlags`) short-circuit at `if flag is None` and never reach it —
      deleting the `try/except` entirely leaves all 12 tests green. Widen the guard to cover the
      attribute read, and add a double whose flag exists but is a plain `bool` (what a Nautilus
      refactor would look like). [src/core/live_node_builder.py:270]
- [x] [Review][Patch] The reader accepts a stale, disposed client and reports it as connected —
      `IB_CLIENTS` is never purged (grep across the whole wheel: only `get`, `in`, and one
      assignment), and `TradingNode.dispose()` closes the loop without `cancel_all_tasks()`
      (`live/node.py:449-458`), so a pending `_stop_async` may never run and leaves both flags set.
      A later read under the same `(host, port, client_id)` key then returns `connected=True` for a
      node that no longer exists — fail-*open* on the one path that must fail closed. Reject a
      client that reports `is_disposed`, or that is not `is_running`, when those attributes are
      present. [src/core/live_node_builder.py:308]
- [x] [Review][Patch] A first-ever connection that drops before confirmation emits `connection.lost`
      and starts a downtime clock for a session that never connected — the `AWAITING_CONNECTION`
      guard covers only the *first* reading. Verified: `UP` then `DOWN` from a fresh monitor gives
      `state=LOST`, `['connection.lost']`, `downtime_seconds=0.0`, and 60s later a
      `connection.halted` for a broker that was never reached, instead of surfacing as Story 1.7's
      exit-code-4 startup failure. This is precisely the conflation the guard exists to prevent.
      [src/core/live_connection_monitor.py:182]
- [x] [Review][Patch] `session_id` is unvalidated — `ConnectionMonitor(session_id="")` is accepted
      and every `connection.lost` / `restored` / `halted` / `recovery_refused` then carries
      `session_id=""`, which is exactly the correlation FR6/AR41 want the field for. The docstring
      explicitly draws the parallel to `build_trading_node`'s `trader_id`, which *is* validated
      (`_validate_trader_id`, `live_node_builder.py:53`). [src/core/live_connection_monitor.py:98]
- [x] [Review][Patch] The probe builds the node and creates the run task **outside** its
      `try/finally`, so a failure at `node.build()` — the one moment the kernel and its non-daemon
      `ThreadPoolExecutor` exist unprotected — skips `_shutdown` entirely: no `dispose()`, no
      `loop.close()`, client id left reserved on the Gateway, and exactly the interpreter-exit hang
      `_shutdown`'s own docstring says it guards against. [scripts/diagnostics/live_connection_probe.py:186]
- [x] [Review][Patch] The probe hangs forever, with no `RESULT` line, when the Gateway is
      unreachable — `node.build()` → `get_cached_ib_client` → `client.start()` calls `_start()`
      **synchronously**, which (loop not yet running) does `run_until_complete(_start_async())`;
      with `_indefinite_reconnect` defaulted on (`IB_MAX_CONNECTION_ATTEMPTS` unset →
      `_max_connection_attempts == 0`, `client.py:135`) that loop retries forever and never
      consults `IBKR_CONNECTION_TIMEOUT`. Traced end-to-end through the wheel. Bound it.
      [scripts/diagnostics/live_connection_probe.py:195]
- [x] [Review][Patch] The probe's join on the cancelled run task is the only unbounded wait in the
      file — every other wait is deadline-bounded. The author's own comment says it is not safe to
      bet on `run_async()`'s `asyncio.gather` resolving after a stop, then bets on it resolving
      under cancellation instead. A queue task that swallows `CancelledError` hangs the probe
      forever holding the live client id, with no `RESULT:` line.
      [scripts/diagnostics/live_connection_probe.py:140]
- [x] [Review][Patch] The probe can print `RESULT: ok` for a run that never observed a *socket*
      disconnect — `_stop_async()` clears `_is_client_ready` first (`client.py:266`), while
      `_is_ib_connected` clears much later and indirectly via `connectionClosed`
      (`connection.py:241`). `_await_disconnect_observed` returns as soon as **either** flag drops,
      and the assertions never check *which*. So the evidence line certifies "readiness flag
      cleared", not the "genuine disconnect" the docstring claims. The comment at
      `_await_disconnect_observed` ("`_stop_async()` — which is what clears `_is_ib_connected`") is
      also factually wrong about the wheel. [scripts/diagnostics/live_connection_probe.py:108]
- [x] [Review][Patch] The reader's comment overstates what `_is_client_ready` means — "A socket
      without it is a connection whose subscriptions have not been restored" implies the converse,
      which does not hold: `_start_async` sets `_is_client_ready` *before* `_resume_async` runs
      `_resubscribe_all()` (`client.py:204,304`), so both flags are set for the entire
      resubscription window. The monitor's two-step confirm mitigates the permission consequence
      today, but Epic 2 and Epic 4 will build on this flag's documented meaning.
      [src/core/live_node_builder.py:324]
- [x] [Review][Patch] The probe's `KeyboardInterrupt` comment is wrong about its own effect — it
      claims that without the clause an operator's Ctrl-C "leaves the client id reserved on the
      Gateway", but id release is done by `_run`'s `finally` → `_shutdown`, which runs on
      `KeyboardInterrupt` regardless. The clause only prints the `RESULT` line.
      [scripts/diagnostics/live_connection_probe.py:316]
- [x] [Review][Patch] `TestIBConnectionWithoutFlags` is not protected by the anti-collection
      mechanism the story cites — it defines no `__init__`, so pytest's "cannot collect a class
      with `__init__`" rule does not apply. It is harmless today only because it declares no
      `test_*` methods (verified: `--collect-only` → 0 items), i.e. by accident rather than by the
      cited mechanism; any helper named `test_*` added later would silently turn a double into a
      test class. [tests/component/doubles/test_ib_connection.py:71]
- [x] [Review][Patch] Task 6's `sprint-status.yaml` checkbox was checked and the File List claims
      the file was modified, but it was not — `git status` does not list it, and it still reads
      `1-6-...: backlog` while the story file says `Status: review`. The tracker is the system of
      record and currently contradicts the story. (Sequencing artefact: the update is the final
      harness step, after review. The claim must not precede the act.)
      [_bmad-output/implementation-artifacts/sprint-status.yaml:144]
- [x] [Review][Patch] Procedure P2's Expected-output transcript omits the `connection.lost` line
      that its own Pass criterion 3 requires as evidence — the block goes straight from the status
      line to the `observed ->` line. The event *is* emitted (confirmed by running the monitor
      standalone: `[warning ] connection.lost detail='ib socket not connected'
      session_id=probe-connection-0001 state=lost`), so the transcript is simply incomplete, and an
      operator diffing real output has no anchor for the criterion.
      [docs/qa/phase3-live-verification.md]
- [x] [Review][Patch] Two small accuracy defects in this story file: Completion Notes say the
      monitor is "211 lines" (actual: 214), and the new unit-test file puts its `src.*` import
      inside the third-party block, unlike the `test_live_gate.py` precedent it cites and unlike
      the sibling component test written in the same diff (`ruff` cannot catch it — no
      `known-first-party` is configured). [tests/unit/core/test_live_connection_monitor.py:19]

**Deferred (real, but pre-existing or owned by a later story — logged in deferred-work.md):**

- [x] [Review][Defer] IB error code **1101** re-sets `_is_ib_connected` without any resubscription,
      so both flags can read "connected" on a link whose market-data subscriptions IB silently
      dropped — 1100 clears the flag, 1101 sets it again before the 1-second watchdog tick, so
      `_handle_disconnection` never runs, `_degrade()` never fires and `_resubscribe_all()` is
      never called. 1101 ("restored — data lost") and 1102 ("restored — data maintained") are
      treated identically by the adapter. There is no third observable available today
      [src/core/live_node_builder.py:321] — deferred, needs a subscription-state signal Epic 1
      does not have
- [x] [Review][Defer] `live_node_probe.py` (Story 1.3) has the same build-outside-`try` shape as
      the patch above, so the hang-on-unreachable-Gateway exposure is inherited, not invented here
      [scripts/diagnostics/live_node_probe.py:164] — deferred, another story's artifact
- [x] [Review][Defer] AR41's normative event list omits `connection.halted` and
      `connection.recovery_refused`, which this story ships as production events
      [_bmad-output/planning-artifacts/epics.md:241] — deferred, architecture-text amendment at the
      Epic 1 retro (already logged)

**Dismissed (3):**

- **`ibkr_client_id == ibkr_live_client_id` would collapse both clients onto one cache key** —
  false positive, and a good illustration of the Blind Hunter's deliberate blindness.
  `validate_client_ids_distinct` (`src/config.py:109`) rejects *any* intersection of the
  historical rotation range `[base, base+5]` with the reserved pair `{live, live+1}`, in both
  directions. Verified by execution: `IBKRSettings(ibkr_client_id=10, ibkr_live_client_id=10)`
  raises `ValidationError`.
- **"`HALTED` is escapable automatically, so it is a log line, not a halt"** — this is the
  specified behaviour, not a defect. The story's Task 1 states it explicitly: "A halt is not a
  dead end; it is a withdrawal of permission that only re-established state can reverse," and a
  test asserts it. The Blind Hunter had no spec, which is the point of that layer. (The related
  question of *when* the halt fires is genuinely open and is Decision 1.)
- **Test doubles being `Test*`-named inside a pytest-collected module** — the established repo
  convention (`tests/component/doubles/test_engine.py`, `test_order.py`, `test_position.py` all
  do this) and warnings are disabled. Only the narrower `TestIBConnectionWithoutFlags` variant is
  actionable, and it is a patch above.

## Dev Notes

### What this story is, and where its edges are

Two production changes: one new pure module (`src/core/live_connection_monitor.py`) and one new
function appended to `src/core/live_node_builder.py`. Plus one new broker double, two new test
files, one new diagnostic script, and one appended QA procedure.

It delivers **detection and a flag**. It does not submit orders, does not consult the flag on any
order path (there is none — Epic 3 builds it), does not own a poll loop (Epic 2's runner does), and
does not re-establish state after a reconnect (Epic 4's reconciliation does). The epic's own note
says this explicitly: *"the order path that consults this permission flag arrives in Epic 3; this
story delivers the detection and the flag, verified directly against broker doubles."*

If the diff touches `src/core/live_gate.py`, `src/config.py`, any strategy file, `src/api/**`,
`templates/**`, `alembic/`, `.env*`, `docker-compose.yml`, or the two existing diagnostic scripts,
something has gone wrong.

### ⚠️ The finding that decides this story's whole design

**At nautilus-trader 1.220.0, an IBKR connection drop produces no message-bus event and no change to
any public connection property.** All three claims below were verified against the installed wheel
this session, not recalled from documentation:

1. **`is_connected` is a lifecycle flag, not a liveness flag.** `LiveDataClient` / `LiveExecutionClient`
   set it `True` as the `connect()` action and `False` in `disconnect()`
   (`live/data_client.py:229,243`, `live/execution_client.py:240,254`); the IB adapter calls
   `_set_connected(False)` only from `_disconnect()` (`adapters/interactive_brokers/execution.py:246`).
   Nothing on the socket-drop path touches it. Consequently
   `node.kernel.data_engine.check_connected()` and `exec_engine.check_connected()`
   (`data/engine.pyx:273`, `execution/engine.pyx:255`) **keep returning `True` through a dead
   socket**. Story 1.3's probe uses them correctly — to answer "did the node finish connecting?" —
   but using them here would produce a permission flag that is permanently `True`, which is exactly
   the blind trading NFR10 forbids. **Do not use `check_connected()` as the liveness source.**
2. **No `ComponentStateChanged` is published either.** The IB client's connection watchdog calls
   `self._degrade()` directly (`client/client.py:384` ← `_handle_disconnection`), and `_degrade` is
   the Cython `Component`'s *subclass hook*, not its public `degrade()` transition
   (`common/component.pyx:1855` vs `:2034`). Only `degrade()` drives the FSM and publishes the
   event, and **nothing in `adapters/interactive_brokers/` calls it** (verified by grep). Confirmed
   by execution: after `client._degrade()`, `client.is_degraded` is still `False`. So there is no
   event to subscribe to — detection must poll.
3. **The adapter's own flags are the truth, and they are private.** `InteractiveBrokersClient` keeps
   two `asyncio.Event`s (`client/client.py:122-123`):
   - `_is_ib_connected` — set when TWS/Gateway returns `managedAccounts`; cleared by
     `_disconnect()`, by `connectionClosed` (`client/connection.py:241-243`), by the incoming-message
     reader exiting (`client/client.py:596-601`), and by `_handle_disconnection` (`:386-388`).
   - `_is_client_ready` — cleared by `_degrade()` on connection loss, set again by `_start_async()`
     only after the reconnect handshake completes.

   Both were confirmed present and `Event`-typed on a freshly constructed client, and that
   construction left `is_logging_initialized()` at `False`.

**Why this is safe to depend on, and how to keep it safe:** read both flags through `getattr` with a
`False` fallback and a canary test that asserts the real class still has them (Task 3). If a Nautilus
upgrade renames them, the reader degrades to "disconnected" — permission is withheld, which is the
correct failure direction — and the canary test names the field that moved. Record the dependency in
`deferred-work.md` so Epic 4 sees it when it wires reconciliation into recovery.

**Why the reconnect timing is *measured*, not enforced.** NFR4's 60 seconds is the IB adapter's
job: its watchdog sleeps 5s and retries (`client/client.py:380-390`), with `_reconnect_delay`
between attempts. This story does not implement reconnection and must not try to — it measures the
downtime and reports whether it fell inside the window. AC #3's actual words are "the elapsed time
is observable in the logs".

### Why recovery is two steps, and why that is the whole point

AC #2's "restored only after state is re-established, never on reconnect alone" is not a nicety —
it is NFR10, and it is why `observe(connected=True)` lands in `RECOVERING` rather than `CONNECTED`.

The adapter makes the gap concrete. `_handle_reconnect()` is `_reset()` + `_resume()`
(`client/connection.py:111-115`); `_reset()` restarts the client, and `_resume()` waits for
`_is_client_ready` and *then* calls `_resubscribe_all()`. So there is a real window in which the
socket is up and the API handshake is done, but subscriptions have not been restored and nothing has
been checked against the broker's authoritative view. Granting permission there would be trading on
stale state.

The `confirm_state_reestablished()` call is deliberately a **seam left for Epic 4**: reconciliation
is what re-establishes state, and it does not exist yet. In Epic 2's runner it is the `trading`
phase of AR39's sequence; today it is called by the tests and by the P2 probe. Do not implement
reconciliation here, and do not make the monitor call it for itself on a timer — an automatic grant
is the bug this design exists to prevent.

### Judgment calls made while writing this story (flag at the Epic 1 retro)

- **Two log event names not in AR41's enumeration.** AR41 lists `connection.lost` and
  `connection.restored` but has no name for the halt NFR20 requires ("halts trading and reports
  it" — reporting needs an event) or for a refused permission grant. This story adds
  `connection.halted` and `connection.recovery_refused`, both conforming to AR41's stated
  convention (dotted lowercase, past tense, session-scoped). AR41's list should be amended to
  include them rather than treated as closed.
- **The reconnect window is a constructor default (60.0s), not a new setting.** Adding an env var
  would ripple into `.env.example`, `README.md` and `docs/setup/IBKR_SETUP.md` — the exact
  four-file ripple Story 1.2's review had to clean up — for a value NFR4 fixes anyway. Epic 2's
  runner can pass a different window if it ever needs one.
- **The IBKR reader lives in `live_node_builder.py`, not the monitor.** The alternative (a third
  new module) was rejected: the cache key must match what the builder configured, and the monitor's
  freedom from Nautilus is what makes the state machine unit-testable. This is a small, additive
  change to a `done` story's module, not a modification of its existing behaviour.
- **The first connection requires `confirm_state_reestablished()` too.** The AC only speaks about
  restoration, but a monitor that starts out permitted would let a session trade before
  reconciliation — NFR10 again. Uniform treatment is both safer and simpler than a special case.

### Scope boundaries — do NOT do these here

- **No order path, no `submit_order`, no order-path wrapper that consults the flag.** Epic 3 owns
  FR6's consumption side (`epics.md:1105` — "no order is submitted, and the suppression is
  logged"). Epic 1 ships the control before the capability, on purpose. If an AC seems to need an
  order path, stop and escalate.
- **No changes to `src/core/live_gate.py`.** The two-factor gate is Layer 1 and is settled. This
  story is an orthogonal, *runtime* control; it neither weakens nor duplicates the gate.
- **No runner, no poll loop, no `asyncio` task, no signal handling.** `live_session_runner.py` is
  Epic 2, Story 2.5 (AR38). The monitor is a synchronous object someone else polls.
- **No reconciliation.** Epic 4 (AR25). `confirm_state_reestablished()` is the seam, not the
  implementation.
- **No session record, no DB, no heartbeat columns.** AR32's `last_heartbeat_at` / `last_bar_at` and
  the `degraded` health value are Epic 2's migration and status command. This module must not
  import SQLAlchemy or `src.db.*` (AR38).
- **No `market_data_type` / RTH work** (Story 1.5), **no Layer 2 account verification** (Story 1.4),
  **no CLI or exit codes** (Story 1.7). Stories 1.4 and 1.5 are still `backlog`; this story must not
  depend on either, and it does not — the status reader works off the adapter's client cache, which
  Story 1.3 already populates.
- **No new dependency** (AR3). `structlog` is already a direct dependency.
- **No README change.** No new environment variable and no new operator command — the probe is a
  diagnostic documented in `docs/qa/`, exactly as Story 1.3's was. CLAUDE.md's README-sync rule is
  satisfied by leaving it alone.

### Previous story intelligence (Stories 1.1–1.3)

- **The gate is the load-bearing control; `ibkr_read_only` is a declaration of intent** (NFR27,
  AR43). Nothing in this story may be presented as a second safety gate — `trading_permitted` is a
  *runtime* availability flag, and the review anti-pattern list explicitly rejects treating a flag
  as the safety control.
- **`build_trading_node()` returns an unbuilt, unstarted node** and takes an explicit `loop`
  (added by Story 1.3's review after it was found silently binding to an ambient one). The P2 probe
  must pass its own loop, as P1's does.
- **`GateRefusedError` must stay distinguishable** — Story 1.7 maps it to exit code 3 (AR28). The
  new probe should catch it separately, as `live_node_probe.py` does.
- **A malformed `trader_id` panics in Rust and aborts the process** — uncatchable, kills the pytest
  worker rather than failing a test. `_validate_trader_id` now guards it, but any new script must
  still use a well-formed value like `"PAPER-PROBE0001"`.
- **`.env*` is hook-protected** (`.claude/hooks/protect-files.sh`, matched by basename). Stories 1.1
  and 1.2 both had to escalate to Allay for a one-off. This story needs no `.env` change; if you
  think it does, you have added a setting you were told not to add.
- **Component-tier test isolation:** build settings with a `_settings(...)` helper passing
  `_env_file=None` **and** every field the code reads as init kwargs. `_env_file=None` disables the
  dotenv *file* only — `os.environ` remains an active pydantic-settings source, so an unlisted field
  silently becomes the developer's shell. The reader touches `ibkr_host`, `ibkr_port` and
  `ibkr_live_client_id`; `IBKRSettings` construction additionally validates `ibkr_client_id`
  against them (`validate_client_ids_distinct`), so pass that too.
- **Pre-existing integration-tier noise:** 23–25 failures unrelated to Phase 3, plus the
  `src/api/web.py` import-time `init_logging()` hazard that crashes Story 1.3's two integration
  tests when `tests/integration/api/test_trades_api.py` is collected in the same `--forked` run.
  Allay ruled document-and-proceed. This story adds no integration test, so it should not
  encounter it — but do not read those failures as regressions.

### Project Structure Notes

- **NEW** `src/core/live_connection_monitor.py` — the delta tree has no entry for it. The tree
  lists connection concerns implicitly under `live_session_runner.py`
  (`architecture.md#Delta-Project-Tree:507-509`), but AR38 gives the runner *lifecycle* ownership,
  and Epic 2 owns the runner; a state machine the runner will poll is a separate, unit-testable
  concern that must exist before the runner does. Same relationship `live_gate.py` has to the CLI
  that will call it. Record the tree addition in Completion Notes.
- **MOD** `src/core/live_node_builder.py` — additive only: one public function, one private helper,
  one import, one docstring line. No existing behaviour changes.
- **NEW** `tests/unit/core/test_live_connection_monitor.py`,
  **NEW** `tests/component/core/test_live_connection_probe.py`,
  **NEW** `tests/component/doubles/test_ib_connection.py`,
  **MOD** `tests/component/doubles/__init__.py`.
- **NEW** `scripts/diagnostics/live_connection_probe.py`, **MOD** `docs/qa/phase3-live-verification.md`.
- **MOD** `_bmad-output/implementation-artifacts/deferred-work.md`, `sprint-status.yaml`.
- Untouched by construction: `src/api/**`, `templates/**`, `src/db/**`, `src/config.py`,
  `src/core/live_gate.py`, every strategy file, `alembic/**`. [Source: architecture.md AR44]
- Import direction (AR38, the law): `live_connection_monitor` imports **only** the standard library
  and `structlog`. `live_node_builder` may additionally import `nautilus_trader.*`,
  `src.core.live_gate`, `src.core.live_connection_monitor` and `src.config` (types only). Neither
  may import SQLAlchemy, `src.db.*`, or any service.

### Testing standards

- **Unit tier** (`make test-unit`, `-n auto`) for the state machine — no Nautilus, deterministic
  clock, `structlog.testing.capture_logs` for events.
- **Component tier** (`make test-component`, `-n auto`, no fork) for the IBKR status reader —
  verified not to initialise C logging; guard that with the autouse fixture
  `test_live_node_builder.py` already establishes.
- **No integration tier this story** — nothing constructs a `TradingNode` in an automated test.
- **No automated test connects to a broker** (NFR32). Broker-dependent behaviour is operator-verified
  (NFR33) in `docs/qa/phase3-live-verification.md`, and that evidence is informational.
- **TDD is non-negotiable**: Task 1 RED before Task 2, Task 3 RED before Task 4. Record both.
- `pytest.ini` is the effective config (not `pyproject.toml`); `--strict-markers` is on.
- `make test-coverage` measures `src/core`, so both touched modules are in scope for the >80% bar.

### Commit hygiene for this repo

- Structural import gate: an unused (F401) or undefined (F821) import hard-blocks the commit at three
  points (`.githooks/pre-commit`, the Claude bash-guard, CI). Add imports and their usages in the
  same edit.
- Stage and commit in **separate** Bash calls; the gate inspects staged files only.
- Commit format `<type>(<scope>): <subject>`. Never reference AI or Claude in commit messages.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.6] (lines 643–676) — the story statement,
  the five acceptance criteria, and the note deferring the order path to Epic 3
- [Source: _bmad-output/planning-artifacts/epics.md:116,125,137,138] — NFR4 (reconnect < 60s),
  NFR10 (no blind trading), NFR19 (gateway restart tolerance), NFR20 (safe degradation)
- [Source: _bmad-output/planning-artifacts/epics.md:238-244] — AR38 (runner owns the node),
  AR39 (startup phase sequence), AR41 (log event naming), AR43 (anti-patterns), AR44 (untouched)
- [Source: _bmad-output/planning-artifacts/epics.md:1105] — Epic 3, Story 3.2's "no order is
  submitted, and the suppression is logged": the consumer of this story's flag
- [Source: _bmad-output/planning-artifacts/prd.md:799] — FR6
- [Source: _bmad-output/planning-artifacts/architecture.md:394-416,424,439,449] — runner/service
  split, health vocabulary (`degraded`), log event naming, "Broker unreachable → halt trading, log
  `connection.lost`, keep process alive for reconnect window; never proceed on stale state"
- [Source: src/core/live_gate.py:44-118] — `GateFlags`/`GateRefusal`/`GateDecision` shapes and
  `mask_account`; the module-purity precedent this story's monitor copies
- [Source: src/core/live_node_builder.py:116-250] — `build_trading_node_config`,
  `build_trading_node(..., loop=)`, and the `(host, port, client_id)` triple the reader must match
- [Source: src/utils/logging.py:101-115] — structlog configuration
- [Source: tests/unit/core/test_live_gate.py:26-46] — the `_settings(...)` isolation helper
- [Source: tests/unit/services/test_kraken_settings.py:8,336] — `structlog.testing.capture_logs`
  precedent
- [Source: tests/component/core/test_live_node_builder.py] — component-tier conventions, the
  C-logging-state autouse guard, the `_settings(...)` helper
- [Source: tests/component/doubles/__init__.py] — the doubles package and its re-export convention
- [Source: scripts/diagnostics/live_node_probe.py:1-263] — the diagnostic-script shape P2 follows,
  including the owned-event-loop pattern and the `RESULT:` line contract
- [Source: docs/qa/phase3-live-verification.md] — P1 and the result-log format P2 appends to
- [Source: _bmad-output/implementation-artifacts/1-3-assemble-and-start-a-tradingnode-against-ibkr-paper.md]
  — previous story: Nautilus facts, the `trader_id` abort trap, the `src/api/web.py` hazard
- [Source: CLAUDE.md] — commit format, import gate, staging discipline, size limits, typecheck scope
- Installed-wheel verification performed while writing this story (nautilus-trader **1.220.0**):
  `nautilus_trader/adapters/interactive_brokers/client/client.py:122-123,180-206,304-322,373-390,596-601`
  (the two `asyncio.Event` flags, the watchdog, `_degrade`/`_resume`),
  `.../client/connection.py:38-40,96-115,241-243` (what clears `_is_ib_connected`),
  `.../factories.py:42,112-126` (`IB_CLIENTS` and its `(host, port, client_id)` key),
  `.../execution.py:234,246` and `.../data.py:134-155` (where `_set_connected` is and is not called),
  `nautilus_trader/live/data_client.py:229,243`, `live/execution_client.py:240,254`
  (`is_connected` is a lifecycle flag), `nautilus_trader/data/engine.pyx:273-304`,
  `execution/engine.pyx:255-283` (`check_connected` iterates those lifecycle flags),
  `nautilus_trader/common/component.pyx:1855,2034,2147` (`_degrade` hook vs `degrade()` FSM
  transition and its `ComponentStateChanged` publication)

## Dev Agent Record

### Agent Model Used

Claude Opus 5 (claude-opus-5)

### Debug Log References

**Task 1 RED** — `uv run pytest tests/unit/core/test_live_connection_monitor.py -q` before
`src/core/live_connection_monitor.py` existed:
```
ImportError while importing test module '.../tests/unit/core/test_live_connection_monitor.py'
E   ModuleNotFoundError: No module named 'src.core.live_connection_monitor'
```
Collection error (31 tests never ran) rather than individual failures — expected, since the import
is at module scope. GREEN after Task 2: 31/31 passed.

**Task 3 RED** — `uv run pytest tests/component/core/test_live_connection_probe.py -q` before
`read_ibkr_connection_status` existed:
```
ImportError: cannot import name 'read_ibkr_connection_status' from 'src.core.live_node_builder'
```
GREEN after Task 4: 12/12 passed.

**Verification table** (Task 6):

**Post-review re-verification** (2026-08-09, after the 3 decisions and 17 patches were applied):

| Command | Result |
| --- | --- |
| `uv run pytest tests/unit -n auto` | **1617 passed** — baseline 1561 + 56 (31 as delivered, 25 more added by review) |
| `uv run pytest tests/component -n auto` | **855 passed, 16 skipped** (871 collected) — baseline 853 + 18 (12 as delivered, 6 more added by review) |
| `uv run pytest tests/integration/core/test_live_node_lifecycle.py --forked` | 2 passed — Story 1.3's tests unaffected by the reader changes |
| `uv run ruff format .` / `uv run ruff check .` | 399 files unchanged / All checks passed |
| `make typecheck` (`src/core src/services`) | Success: no issues found in 76 source files |
| `uv run python scripts/diagnostics/live_connection_probe.py --hold-seconds 1` (no `.env`, no Gateway) | `RESULT: fail reason=config_error ...`, exit 1, and the `finally` now runs — `[probe] disposing node...` / `loop.is_closed=True` — where it previously would have skipped shutdown entirely |

**As delivered, before review:**

| Command | Result |
| --- | --- |
| `uv run pytest tests/unit -n auto` | **1592 passed** — baseline 1561 collected + 31 new |
| `uv run pytest tests/component -n auto` | **849 passed, 16 skipped** (865 collected) — baseline 853 collected + 12 new |
| `uv run pytest tests/integration -n auto --forked` | 144 passed, 23 failed, 4 skipped — **144 passed identical to Story 1.3's baseline**; all 23 failures pre-date Phase 3 and this story adds no integration test. The 2 `test_live_node_lifecycle.py` tests now **skip** in a full run ("requires a process where Nautilus logging is not yet initialised" — the self-guard Story 1.3's review added), which is why the failure count reads 23/4-skipped rather than 25/2-skipped; both still pass standalone (`--forked`, 2 passed) |
| `uv run pytest tests/integration/core/test_live_node_lifecycle.py --forked` | 2 passed — the touched module's own integration tests are not regressed |
| `uv run ruff format .` | 399 files left unchanged |
| `uv run ruff check .` | All checks passed |
| `make typecheck` (`src/core src/services`) | Success: no issues found in 76 source files |
| `git diff --stat pyproject.toml uv.lock` | empty (AC #6) |
| `git diff src/core/live_gate.py` | empty (AC #6) |
| `grep -rn "submit_order\|OrderFactory\|MarketOrder"` over the new module + probe | no matches (AC #6) |
| `uv run python scripts/diagnostics/live_connection_probe.py --hold-seconds 0` | rejected by argument validation, exit 2 |
| `uv run python scripts/diagnostics/live_connection_probe.py --hold-seconds 1` (no `.env`, no Gateway) | `RESULT: fail reason=config_error msg=Cannot build an IBKR execution client: TWS_ACCOUNT is not set ...`, exit 1, **no socket opened** — fail-closed path, tooling evidence only |

### Completion Notes List

- Delivered `src/core/live_connection_monitor.py` (358 lines after review; 214 as first written —
  an earlier note said 211, which was simply wrong): `ConnectionState`,
  `ConnectionStatus`, `ConnectionMonitor`. Pure — standard library plus `structlog`, asserted by a
  test that AST-parses the module and rejects any `nautilus_trader` / `sqlalchemy` / `src.db` /
  `src.services` / `src.api` import. TDD Red→Green for both halves (Tasks 1–2, then 3–4).
- **The story's decisive finding held up under implementation and is worth restating**: at
  nautilus-trader 1.220.0 an IBKR connection drop publishes **no** message-bus event and changes
  **no** public connection property. Re-verified during development: `client._degrade()` leaves
  `client.is_degraded` at `False` (the adapter calls the `_degrade` *hook* directly, never the
  `degrade()` FSM transition, so no `ComponentStateChanged` is published), and `is_connected` —
  hence `DataEngine.check_connected()` — only moves on the `connect()`/`disconnect()` lifecycle.
  Detection therefore polls the adapter's two private `asyncio.Event` flags, and a canary test
  constructs a real `InteractiveBrokersClient` to fail by name if either flag ever moves.
- `trading_permitted` is a derived property with no setter (`return self._state is
  ConnectionState.CONNECTED`). Three tests defend it: an exhaustive parametrisation over every
  `ConnectionState` member, a meta-test proving the driver helper actually reaches all five (so the
  parametrisation cannot silently stop being exhaustive), and a 40-iteration hostile sequence that
  asserts no disconnected observation — and no confirmation attempt following one — ever grants it.
- **Recovery is two steps**, which is the story's substance. `observe(connected=True)` lands in
  `RECOVERING`, never `CONNECTED`; only `confirm_state_reestablished()` grants permission, and it
  refuses (logging `connection.recovery_refused`) whenever the last reading said disconnected. That
  seam is where Epic 4's reconciliation plugs in; nothing here grants permission on a timer.
- **Two log event names were invented beyond AR41's enumeration** — `connection.halted` (NFR20 says
  "halts trading and reports it"; reporting needs an event) and `connection.recovery_refused`
  (permission is the safety-critical output, so a refused grant must be visible). Both follow
  AR41's stated convention. Flagged in Dev Notes for the Epic 1 retro so AR41's list is amended
  rather than treated as closed.
- The IBKR reader lives in `live_node_builder.py` rather than a third module: the cache key is the
  exact `(ibkr_host, ibkr_port, ibkr_live_client_id)` triple that module hands to both client
  configs, so co-locating them means the lookup key cannot drift from the configured one — and it
  keeps the monitor Nautilus-free, which is what makes the state machine unit-tier. The change to
  that `done` module is purely additive (one public function, one private helper, one import, a
  docstring sentence); its own integration tests still pass.
- **AC #6 clean.** No order path exists anywhere in the diff, `pyproject.toml`/`uv.lock` are
  untouched, and `src/core/live_gate.py` is byte-identical — the two-factor gate was neither
  weakened nor duplicated. `trading_permitted` is a *runtime availability* flag, deliberately not a
  second safety gate (AR43).
- **Procedure P2 was defined but could not be run — no live evidence this session.** Two
  independent blockers, both recorded in `docs/qa/phase3-live-verification.md`'s P2 result log:
  no IB Gateway or TWS was listening (all four IB ports refused a TCP connection when probed), and
  this worktree has no `.env` (gitignored, so it does not follow a worktree; and `.env*` is
  Edit/Write-protected by `.claude/hooks/protect-files.sh`, so creating one was neither attempted
  nor appropriate). The probe *was* exercised as a dry run and behaved correctly on the fail-closed
  path — it reported `RESULT: fail reason=config_error ... TWS_ACCOUNT is not set`, exit 1, before
  opening any socket. Per this file's own policy that is tooling evidence, **not** a pass, and it
  is logged as `⛔ not run`. AC #1–#5 rest on the 31 unit and 12 component tests, which require no
  broker (NFR32/NFR34).
- **Delta-tree addition recorded:** `src/core/live_connection_monitor.py` has no entry in
  `architecture.md#Delta-Project-Tree`. Connection concerns are listed implicitly under
  `live_session_runner.py`, but AR38 gives the runner *lifecycle* ownership and Epic 2 owns the
  runner — a state machine the runner will poll has to exist before the runner does, exactly as
  `live_gate.py` precedes the CLI that calls it.
- No new dependency (AC #6): `structlog` was already a direct dependency.

**Post-review additions (2026-08-09).** Three adversarial layers produced 34 raw findings; all
three decisions were ruled option (a) and every patch applied. The three that changed behaviour
rather than wording are worth naming, because each was a way `trading_permitted` could be `True`
when it should not have been, and none was reachable by the tests as originally written:

- **The halt clock is now anchored, not restarted.** A gateway that came up for one poll every 59
  seconds used to reset the 60-second window on every blip — Edge Case Hunter drove 200 such cycles
  (2.8 hours of an unusable link) and `connection.halted` never fired once. Unavailability is now
  anchored at the loss and cleared only by a successful confirmation, and the deadline is evaluated
  after *every* observation rather than only in the `LOST` branch, so an unconfirmed recovery halts
  instead of stalling silently in `RECOVERING` forever (verified previously to sit there for a
  simulated 24 hours with no log line at all).
- **Permission now expires.** `trading_permitted` requires the last reading to be no older than
  `max_observation_age_seconds` (defaulted *from* the reconnect window rather than invented
  separately). This is the one failure shape no sequence of observations can express — a poll loop
  that dies — and it was the single most consequential fail-open in the original design.
- **The confirmation carries its own reading.** `confirm_state_reestablished(status)` is a breaking
  signature change, ruled deliberate: the old no-arg form inspected only cached state, so a socket
  that died between the caller's last poll and its confirmation bought a whole poll interval of
  blind trading. It now folds the reading in as an observation first, which also means a
  confirmation arriving after the window halts rather than granting.
- One genuinely instructive consequence, pinned by its own test: the halt measures *unavailability*
  while `within_reconnect_window` on `connection.restored` measures NFR4's narrower *reconnect*
  time. A 2-second reconnect followed by 70 seconds of reconciliation therefore halts — correctly,
  since the session was unable to trade for longer than the window — while still reporting the
  reconnect as fast. Conflating the two would have made every `connection.restored` indict the
  broker for however long reconciliation took (NFR5 allows it 30s).
- The reader gained two guards: the whole flag read now sits inside the fail-closed `try` (its
  `getattr` default only ever caught `AttributeError`, so a property that raised escaped into the
  poll loop), and a client that reports `is_disposed` or is not `is_running` is rejected —
  `IB_CLIENTS` is never purged, and `TradingNode.dispose()` can leave a corpse with both flags
  still set.
- The probe was hardened on the same three axes Story 1.3's was: the node build moved inside the
  `try/finally`, `node.build()` runs under a bounded connection-retry budget (the adapter
  reconnects *indefinitely* by default and never consults `IBKR_CONNECTION_TIMEOUT`, so an
  unreachable Gateway hung it forever with no `RESULT:` line), the run-task join is bounded, and it
  now waits on the *socket* flag specifically — waiting on `ConnectionStatus.connected` returned as
  soon as the readiness flag cleared and would have certified that as a "genuine disconnect".
- Two review findings were **dismissed after verification rather than fixed**: the Blind Hunter's
  client-ID-collision report is a false positive (`validate_client_ids_distinct` rejects it —
  confirmed by execution), and "a halt is escapable, so it is not a halt" is the specified
  behaviour, asserted by a test. The Blind Hunter had no project or spec access, which is exactly
  why it raised both.

### File List

- `src/core/live_connection_monitor.py` (new)
- `src/core/live_node_builder.py` (modified — additive: `read_ibkr_connection_status`, `_flag_is_set`,
  the `IB_CLIENTS`/`ConnectionStatus` imports, and a docstring sentence)
- `tests/unit/core/test_live_connection_monitor.py` (new)
- `tests/component/core/test_live_connection_probe.py` (new)
- `tests/component/doubles/test_ib_connection.py` (new)
- `tests/component/doubles/__init__.py` (modified — re-export the two new doubles)
- `scripts/diagnostics/live_connection_probe.py` (new)
- `docs/qa/phase3-live-verification.md` (modified — Procedure P2 appended)
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified — new "Deferred from:
  story-1.6" section)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified — status transitions)
- `_bmad-output/implementation-artifacts/1-6-detect-connection-loss-and-withhold-trading-permission.md`
  (modified — this file: tasks, Dev Agent Record, Change Log, Status)

## Change Log

| Date       | Description                                                                 |
| ---------- | --------------------------------------------------------------------------- |
| 2026-08-07 | Story created — developer context assembled against the installed nautilus-trader 1.220.0 wheel. The decisive finding: an IBKR connection drop at 1.220.0 publishes no message-bus event and does not change `is_connected` or `is_degraded` on any public surface, so detection must poll the adapter's two private `asyncio.Event` flags; `check_connected()` would yield a permanently-`True` permission flag. Status → ready-for-dev. |
| 2026-08-09 | Code-reviewed (3 adversarial layers, 34 raw findings → 3 decisions / 17 patches / 3 deferred / 3 dismissed). All 3 decisions ruled option (a) by Allay and all 17 patches applied. Every decision closed a way `trading_permitted` could be `True` when it should not have been, and none was reachable by the tests as first written: the halt clock is now **anchored** at the loss and cleared only by a successful confirmation, with the deadline evaluated after every observation (a gateway flapping up for one poll every 59s previously reset the window forever — 200 cycles, 2.8h of an unusable link, zero `connection.halted`; and an unconfirmed recovery sat in `RECOVERING` for a simulated 24h with no log line); `trading_permitted` now **expires** if the last reading is older than `max_observation_age_seconds`, closing the one fail-open shape no *observation* can express — a dead poll loop; and `confirm_state_reestablished(status)` is a deliberate **breaking signature change** so the grant is atomic with a fresh reading rather than taken against cached state. Also fixed: `reconnect_seconds` is now reported separately from `downtime_seconds` (NFR4 measures the reconnect, not the reconciliation that follows); constructor validation for `session_id` and both intervals (`nan` silently disabled the halt forever); backward clock steps clamped; a half-up first connection no longer emits `connection.lost` for a broker never reached; the reader's fail-closed guard widened to cover the attribute read and extended to reject a disposed/stopped client left in the never-purged `IB_CLIENTS`; and the probe hardened (build inside the `try/finally`, bounded build and join, and it now waits on the **socket** flag rather than certifying a cleared readiness flag as a genuine disconnect). Final: unit 1617 passed (1561 + 56), component 855 passed / 16 skipped (853 + 18), Story 1.3's 2 integration tests unaffected, lint/format/typecheck clean, no dependency change. P2 remains **not run** — still no Gateway and no `.env` in this worktree. Status → done. |
| 2026-08-07 | Implemented. `src/core/live_connection_monitor.py` delivers the connection state machine and the derived `trading_permitted` flag (AC #1–#5); `read_ibkr_connection_status()` in `live_node_builder.py` is the fail-closed reader that turns the adapter's two private flags into one `ConnectionStatus`. TDD Red→Green across Tasks 1–4: 31 unit tests, 12 component tests, one new broker double. Recovery is deliberately two-step — a live socket alone never grants permission (NFR10) — with `confirm_state_reestablished()` left as the seam Epic 4's reconciliation will call. AC #6 clean: no order path, no dependency change, `live_gate.py` byte-identical. Procedure P2 defined but **not run** — no Gateway was listening and this worktree has no `.env`; the dry run exercised the fail-closed path only and is logged as tooling evidence, not a pass. Status → review. |
