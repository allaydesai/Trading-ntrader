# Story 2.7: Keep One Failing Strategy from Taking Down the Session

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a strategy that raises to be contained,
so that one bad strategy cannot end a multi-week forward test that other strategies are still
contributing to.

---

## Acceptance Criteria

> **Four criteria come from `epics.md:977-994`. Six more (#5–#10) are added here** — the same move
> Stories 2.3, 2.4, 2.5 and 2.6 each made, and for the same reason: the epic's criteria are
> individually right and jointly leave holes that were **measured**, not imagined, while this story
> was drafted. Every added criterion is traceable to a Pre-verified finding below or to a
> `deferred-work.md` item that names Story 2.7 by number.
>
> ⚠️ **AC #1's phrase "caught at the runner boundary" describes a place that does not exist, and
> this story deliberately re-reads it.** Measured against the installed `nautilus-trader 1.220.0`:
> an exception raised in `Strategy.on_bar` is **re-raised** by `Actor.handle_bar`
> (`common/actor.pyx:3743-3748`), unwinds through `MessageBus.publish_c` — which has no
> `try`/`except` around `sub.handler(msg)` (`common/component.pyx:2754-2757`) — into
> `LiveDataEngine._run_data_queue`, and lands in `_handle_queue_exception`
> (`live/data_engine.py:347-365`), whose default branch is **`os._exit(1)`**. `os._exit` runs no
> `except`, no `finally`, no `atexit`. The runner's boundary is never reached, `mark_stopped()`
> never runs, and the row is stranded at `running`. A `try`/`except` in `LiveSessionRunner` is
> therefore **structurally incapable** of satisfying AC #1. Throughout this story, "the runner
> boundary" means **the boundary the runner installs around each strategy** — a per-strategy guard,
> applied before `add_strategy`, that catches the exception before it can re-enter `publish_c`.
> See Pre-verified findings #1–#4.

**AC #1 — A raising strategy handler is contained, and the session keeps running** *(FR50, NFR12)*

**Given** a running session whose strategy raises inside a Nautilus event handler
(`handle_bar` today; `handle_event` is the Epic 3 path and is covered by the same guard)
**When** the exception is raised
**Then** it is caught by the boundary this story installs around that strategy, **before** it
re-enters `MessageBus.publish_c`, and the process **does not exit** — proven by return code, not by
a log assertion
**And** it is logged as `strategy.failed` at ERROR, carrying the Nautilus `strategy_id`, the spec's
own `strategy_id`, the `error_type`, the handler that raised, and the traceback
**And** the node, the heartbeat, the bar subscription and every other strategy keep running: the
session's `last_heartbeat_at` continues to advance and `run()` does not return.

**AC #2 — Every other strategy keeps receiving bars, in either order** *(FR50, NFR12)*

**Given** a session with **more than one** strategy spec on the **same** bar type
**When** one strategy raises on a bar
**Then** every other strategy receives that same bar
**And** this holds **whichever order** the strategies were registered in — the test parametrises
both, because message-bus dispatch is registration-ordered within a priority
(`common/component.pyx:2795`, `sorted(subs_list, reverse=True)`), so a one-order test passes
vacuously against a design with no containment at all when the raiser happens to be last
**And** the pair used is `sma_crossover` (the raiser) + `momentum` (the survivor) — the only pair
that is both in-repo and free of the `order_id_tag` collision proven in finding #10.

**AC #3 — Nothing in the node exits the process** *(AR38)*

**Given** any failure path inside the node
**When** the code is inspected
**Then** an AST scan over the node-facing modules finds no call to `sys.exit`, `os._exit`,
`os.abort`, `exit`, `quit`, or `raise SystemExit` — matched on **identifiers**, never on source
text, and with planted probes proving each name is detectable
**And** the scan carries exactly two documented exemptions, by name and with the reason inline:
`src/core/live_session_signals.py`'s `os._exit` (Story 2.6's sanctioned second-signal force exit,
which is a signal path and not a failure path) and the CLI boundary's `raise SystemExit(...)` in
`src/cli/commands/live.py` / `live_start.py`
**And** — the clause that makes this AC non-vacuous — the scan is paired with a positive proof that
**Nautilus's own `os._exit(1)` is unreachable for strategy code**: a fresh-interpreter subprocess
running a real `LiveDataEngine` with a raising strategy exits **`0`**, and the same probe with the
guard removed exits **`1`**. Today the AST scan passes green while the process still dies; a scan
alone would ship a false pass.

**AC #4 — A failed strategy is visible from another process** *(FR49, NFR23, AR32)*

**Given** a strategy that has failed and been contained
**When** the session's row is read from a **different** process
**Then** the failure is there to be read: a new nullable `runtime_flags` JSONB column on
`trading_sessions` carries `{"v": 1, "failed_strategies": [{...}]}`, written through a **third**
method on `SessionRecordPort` and `SessionService`, never by the runner touching SQLAlchemy (AR38)
and never by assigning `status` (AR37)
**And** each entry carries `strategy_id`, `spec_strategy_id`, `error_type`, `handler`, `at`
(ISO-8601 UTC) and a redacted one-line `detail`; **no traceback** — that belongs in the log sink
**And** `runtime_flags` is **cleared to NULL on every `-> running` transition**, so a failure from
a previous process run is never reported against the current one
**And** the write is **queued, never performed inline** — see AC #10.

> **Why this story adds the column rather than deferring it to Story 2.8** — decided with Allay,
> 2026-08-23. `deferred-work.md:851-861` decided exactly one thing (*"not a fifth status value"*) and
> closed with *"decide there whether that is sufficient or a separate nullable column is still
> warranted"* — the column question was left open, not refused. Three facts settle it:
> **(1) Containment without persistence is a regression, not a neutral omission.** Today
> `os._exit(1)` leaves the row `running` with a frozen heartbeat, which Story 2.8 renders `stale` —
> crude, but visible across processes. After containment the session heartbeats normally, and
> because the runner's `note_bar` is subscribed at the `subscribe` phase **before** any strategy
> subscribes at `trading` (measured: dispatch order `['observer', 'note_bar', 's1']`), `last_bar_at`
> keeps advancing even when every strategy is dead. Story 2.8 would render `trading` for a session
> that cannot place an order. Without AC #4 this story ships a false green.
> **(2) Story 2.8 needs the column anyway.** AR32 defines `degraded` as *"running + fresh heartbeat
> but `connection.lost` flagged"* and no such column exists, while Story 2.8 AC #1 forbids depending
> on the runner's memory. The marginal cost of landing it here is **zero** migrations, not one.
> **(3) The cost is measured, not asserted.** `trading_sessions` holds 2 rows; a nullable column is
> metadata-only on PG 11+; `grep -rn "trading_session\|TradingSession" src/api/ templates/` returns
> **zero** hits (AR44 untouched); and AR37's AST guard matches only `t.attr == "status"`, so a
> non-status column is invisible to it (AR37 untouched). No repository method changes, so AR9's
> dual-repository rule is not triggered.

**AC #5 — A strategy that fails to start does not stop the others starting** *(FR50, NFR12; finding #6)*

**Given** a session whose strategy raises in `on_start`, or whose `add_strategy` raises
**When** the `trading` phase runs
**Then** that spec is contained and recorded exactly as a runtime failure is, and the loop
**continues to the next spec** — today it does not: `_phase_trading`
(`src/core/live_session_runner.py:502-505`) is a bare `for` loop, `Component.start()` re-raises
(`common/component.pyx:1888-1902`), and one bad `on_start` leaves every later spec at `READY` and
the Trader stuck in `STARTING`
**And** the recovery verb on this path is `fault()`, **not** `degrade()`: `degrade()` is illegal
from `STARTING` and is silently swallowed (measured — `_trigger_fsm` catches `InvalidStateTrigger`,
logs and returns, `common/component.pyx:2130-2134`), and `stop()` would run `on_stop()`, which for
`sma_crossover` still calls `close_all_positions()`
**And** if **no** strategy starts at all, the `trading` phase **fails** and the AR39 sequence stops:
a session with zero live strategies can never trade, and reporting it as started would be the
same false green AC #4 exists to prevent.

**AC #6 — The uncontained path is closed at the engine too** *(AR42, NFR12; findings #3, #12)*

**Given** any exception this story's per-strategy guard does not cover — an actor, the runner's own
`note_bar` handler, or a future Nautilus code path
**When** it reaches a live engine's queue loop
**Then** the node performs a graceful shutdown instead of `os._exit(1)`: `build_trading_node` passes
`LiveDataEngineConfig`, `LiveExecEngineConfig` and `LiveRiskEngineConfig` with
`graceful_shutdown_on_exception=True`, **explicitly**, alongside the `NODE_TIMEOUT_*` constants and
for the same stated reason — a safety property resting on a third-party default is one upgrade from
silently changing
**And** this is what covers the runner's own `note_bar` handler and the `LiveBarObserver` — neither
is wrapped by the per-strategy guard, and neither gets a `try` of its own (see Task 7)
**And** the story states plainly, in the code comment and here, that this flag satisfies **neither**
AC #1 nor AC #2: measured, `graceful_shutdown_on_exception=True` publishes `ShutdownSystem` →
`kernel.stop_async()`, which **ends the session**; and with two co-subscribed strategies it still
left the sibling with `{'A': 3, 'B': 0}` — the `publish_c` dispatch abort is upstream of the flag
and untouched by it. It is defence in depth behind per-strategy containment, never a substitute
**And** a canary test pins `LiveDataEngineConfig().graceful_shutdown_on_exception is False`, so a
Nautilus upgrade that flips the default is a decision rather than a silent behaviour change.

**AC #7 — Containment touches no position and no order** *(NFR14, AR43)*

**Given** a contained strategy failure
**When** the containment runs
**Then** no code owned by this story closes, cancels or modifies a position, and no order is
submitted — enforced structurally by extending the existing AST scan in
`tests/unit/core/test_live_stop_path_is_inert.py` to this story's modules, with its non-vacuity
probes intact
**And** the isolation verb is `degrade()`, chosen **because** it does not run `on_stop()`:
`Trader.stop_strategy()` would reach `sma_crossover.on_stop()`'s `close_all_positions()`
(`src/core/strategies/sma_crossover.py:83-86`), manufacturing an exit the strategy never requested
over an unrelated `on_bar` bug — precisely the AR43 anti-pattern and the NFR14 violation
**And** `Trader.remove_strategy()` is **not** used: measured, it does not unsubscribe the strategy's
handlers (`bar_subs 2 -> 2`) and it deletes the strategy from `Trader.strategy_states()`, destroying
the in-process half of AC #4's visibility.

> ⚠️ **A knowingly-accepted consequence, recorded rather than discovered at review.**
> `Trader.stop_strategy()` and `Trader._stop()` both guard on `is_running`, which means
> `state == RUNNING` **exactly** (`common/component.pyx:1757-1767`), so a `DEGRADED` or `FAULTED`
> strategy is **skipped at session teardown and its `on_stop()` never runs** (measured: after
> degrading A mid-run, `Trader.stop_strategy(a.id)` left `A.state == DEGRADED`). Today that is
> strictly *safer* — `sma_crossover.on_stop()` still flattens — so this story deliberately does
> **not** call `strategy.stop()` on a degraded strategy in the teardown. **Story 3.1 must revisit
> this** once the flatten is removed: after 3.1 a degraded strategy silently skipping `on_stop()`
> becomes a leak (no `unsubscribe_bars`), not a safeguard. Recorded in `deferred-work.md` and
> pinned by a test whose docstring says so.

**AC #8 — The failure record never leaks an account identifier** *(NFR26)*

**Given** an exception whose message embeds an account identifier — the measured shape is IBKR's
own `Error 321: account DU4076626 is not managed`, which arrives through the adapter
**When** it is logged and when its one-line `detail` is written to `runtime_flags`
**Then** account-shaped tokens are **redacted in place** by a new `redact_accounts(text) -> str`
in `src/core/live_strategy_guard.py`, which regex-substitutes each match with `mask_account(match)`
and leaves the rest of the string intact
**And** the test is non-vacuous — it asserts **all three**: `"DU4076626" not in text`,
`"***626" in text`, **and** `"is not managed" in text` (for the traceback: `"sma_crossover.py"` and
`"DivisionByZero"` both survive). A test asserting only the first passes against an implementation
that destroys the whole string
**And** the tension with `live_session_phases.py:88-93` — which deliberately logs `error_type` and
**never** `str(exc)` — is resolved deliberately, not ignored: AC #1 requires a traceback, a
strategy's exception is this codebase's own stack rather than an opaque broker string, so the
traceback **is** logged, and redaction is what makes that safe. Measured: raw `exc_info=True` under
this repo's configuration renders the unmasked account id to both the console and
`logs/ntrader.log`.

> ⚠️ **`mask_account` alone cannot do this job, and the obvious reading of it ships a vacuous test.**
> `src/core/live_gate.py:117-140` is a **whole-value** masker — it returns `"***" + the last three
> characters of its entire input`. EXECUTED:
> `mask_account("Error 321: account DU4076626 is not managed")` → **`'***ged'`**, and a full
> traceback masks to `'***ged'` too. Applying it to free text destroys the payload while the naive
> assertion (*"`DU4076626` does not appear"*) still passes — a test that cannot fail. `mask_account`
> is the right **per-token** primitive and `redact_accounts` calls it per match; it is not a
> redactor by itself.
>
> The repo's existing answer to the same problem is the **allowlist** at `live_check.py:145-160`
> (`_SAFE_MESSAGE_EXCEPTION_NAMES` — exception types whose `str()` this codebase wrote, with this
> exact IBKR `Error 321` case documented inline as the reason). A strategy's own exception is not on
> that list and never will be, because a strategy can raise anything. Redaction is therefore the
> correct mechanism here, and the allowlist is named so a reviewer sees the choice was made rather
> than missed. If the retro prefers the allowlist, this is the call site.

**AC #9 — The side effects latch: once per strategy, not once per bar** *(NFR12, AR42)*

**Given** a strategy that would raise on every bar
**When** it is contained
**Then** exactly **one** `strategy.failed` record and exactly **one** `runtime_flags` write occur
per strategy per process run — a per-bar Postgres write on a 1-minute stream is a self-inflicted
denial of service on the record path the heartbeat shares
**And** the latch is set **before** `degrade()` is attempted, because `degrade()` from a
non-`RUNNING` state is silently swallowed (measured: `READY -> degrade() -> still READY`, no
exception) — a guard that relies on `degrade()` alone to suppress repeats will log one contained
failure per bar for the rest of the session
**And** ⚠️ **the latch suppresses the side effects only — the wrapper still calls through to the
base handler**, inside its `try`. This is load-bearing and the obvious short-circuit is wrong:
`Actor.handle_bar` updates registered indicators *before* the `RUNNING` gate, so a wrapper that
returns early freezes them. MEASURED over 8 bars with a registered `EMA(3)`, latching on bar 1:
short-circuiting gives `ema.count: 1, initialized: False`; calling through gives
`ema.count: 8, initialized: True`. Freezing them would contradict finding #7 and Judgment call #8,
which both rest on a degraded strategy staying **warm** — and worse, `sma_crossover` keeps its own
`_prev_fast_sma`/`_prev_slow_sma`, so a resumed strategy would compare a fresh SMA against
pre-outage values and could fire a crossover order the market never justified (NFR14)
**And** calling through is cheap after `degrade()`: `handle_bar`'s `if state == RUNNING` gate is
then false, so `on_bar` is not re-entered — only the indicator update runs
**And** when the **last** live strategy fails, `session.all_strategies_failed` is logged at ERROR
and `runtime_flags` carries `"all_failed": true` alongside the list; the session still **runs**
(AC #1's letter) rather than stopping — the trade-off is named in Judgment call #7.

**AC #10 — Nothing raises out of a wrapped handler, and nothing blocks in one** *(AR42, NFR12; measured)*

**Given** the guard's own failure path — the record call, the redaction, the traceback formatting,
the log call, `degrade()`
**When** any of them raises
**Then** it is caught by the guard itself, logged as `session.strategy_record_failed`, and the
wrapped handler **returns normally**. **Nothing** propagates out of a `handle_*` wrapper —
**including `SessionReclaimedError`**
**And** that is not a stylistic preference. MEASURED, three modes of a real `LiveDataEngine` in a
fresh interpreter: no guard → `rc=1`, 0 bytes; guard → `rc=0`, `survivor_bars=3`,
`a_state=DEGRADED`; **guard whose `except` block itself raises → `rc=1`, 0 bytes**. A reclaim
raised from inside `handle_bar` re-enters `publish_c` and hits the identical silent `os._exit(1)`
this story exists to close. `record_activity`'s reclaim can be fatal because it is raised on the
steady-state executor and propagates to `run()`; **there is no such boundary inside `handle_bar`**
**And** a reclaim observed on the record path therefore sets a flag the steady-state tick reads and
raises **there**, where a boundary exists — the same `_ownership_lost` route the heartbeat already
uses
**And** the guard performs **no I/O on the calling thread**. `handle_*` runs inline and
synchronously inside `MessageBus.publish_c` on the event-loop thread, and
`SessionSteadyState.note_bar`'s own docstring already states that handlers here *"must be trivial
and must not raise"*. A Postgres round trip there stalls the loop and delays the bar for every
later-subscribed strategy — partially recreating the AC #2 starvation this story exists to fix.
This repo has already measured that class of write blocking the node for **59.81s** (decision D2,
2026-08-23), which is why `SessionSteadyState._write_activity` runs on that object's **own**
`ThreadPoolExecutor` rather than `asyncio.to_thread`
**And** so the guard **appends the `StrategyFailure` to an in-memory queue and returns**;
`SessionSteadyState.run()`'s existing tick drains the queue and writes through that same executor,
alongside `_write_activity`. One bounded, off-thread write per strategy per process run.

---

## Tasks / Subtasks

> **TDD is non-negotiable.** Every implementation subtask below is preceded by a test subtask that
> must be **watched failing first**. The single most-caught review finding across Stories 2.1–2.6,
> in every one of them, is *a test that cannot fail*.

- [x] **Task 1 — Reproduce the defect and record the baseline** (AC: #1, #3)
  - [x] Re-confirm finding #3 with your own eyes: write a throwaway fresh-interpreter probe (a real
        `LiveDataEngine`, two registered+started `Strategy` objects, the first raising in `on_bar`)
        and observe **exit code 1 with zero stdout**. `os._exit` discards buffered stdout, so every
        `print` needs `flush=True` and the **return code is the only reliable signal**.
  - [x] Confirm `LiveDataEngineConfig().graceful_shutdown_on_exception is False` and that
        `build_trading_node` passes no engine config (`grep` for `data_engine=` in
        `src/core/live_node_builder.py` → zero hits).
  - [x] Record the per-tier baseline counts with `pytest <dir> --collect-only -q`. Measured on
        2026-08-23 at `107ee14`: **unit 2126, component 1262, integration 261, e2e 1**. Do **not**
        use `make test-all` — it currently fails collection on a `test_session_service.py` basename
        clash between two `__init__.py`-less directories.
  - [x] Confirm the file sizes in the budget table below with `wc -l` before writing a line.

- [x] **Task 2 — The containment guard, as a framework-free module** (AC: #1, #8, #9)
  - [x] RED: `tests/unit/core/test_live_strategy_guard.py` (unit tier, **zero Nautilus imports** —
        the module is duck-typed against a stub strategy object). Assert: a wrapped handler that
        raises does not propagate; the queued `StrategyFailure` carries the strategy id read **at
        failure time**, not at wrap time; a second raise queues **no** second failure and logs no
        second record (AC #9's latch); **after latching the wrapper still calls through to the base
        handler** (AC #9's indicator clause); `degrade()` is attempted only when `is_running` is
        true; a strategy whose `degrade()` is a no-op is still latched.
  - [x] RED: **the guard guards itself** (AC #10) — a stub whose `degrade()` raises, a logger that
        raises, and a redactor that raises each produce **no** propagation out of the wrapped
        handler. This is the subtask that prevents the measured `rc=1, 0 bytes` regression.
  - [x] RED: `drain_pending()` returns each failure exactly once and empties the queue; the guard
        performs **no** port call on the calling thread (assert the port double records nothing).
  - [x] RED: `redact_accounts` — plant `Error 321: account DU4076626 is not managed` and assert
        **all three**: `"DU4076626" not in out`, `"***626" in out`, `"is not managed" in out`.
        Add the mutation-proof case: `redact_accounts` implemented as `mask_account(text)` returns
        `'***ged'` and must fail the second and third assertions.
  - [x] GREEN: `src/core/live_strategy_guard.py` (**NEW**). Public surface in the design section
        below. Standard library + `structlog` + `src.core.live_gate.mask_account` +
        `src.core.live_session_record` only — **no `nautilus_trader` import**, so the module is
        unit-testable and the `live_dependency_invariance` glob has nothing to complain about
        beyond `traceback`.
  - [x] Wrap `handle_bar` **and** `handle_event`, at the **instance** level, and set them with
        `setattr` defensively: a bare `Strategy` is a cdef class with no `__dict__` and rejects the
        assignment, while **every Python subclass accepts it** (measured, both ways). If the
        assignment fails, raise a named `StrategyGuardError` so an un-wrappable strategy is a clear
        startup failure rather than an `AttributeError` inside `_phase_trading`.
  - [x] ⚠️ Wrap `handle_*`, **not** `on_*`. `Actor.handle_bar` runs `_handle_indicators_for_bar`
        **before** the `RUNNING` check and **outside** the `try` (`common/actor.pyx:3735-3744`), so
        an `on_bar` wrapper leaves a raising registered indicator uncontained. No repo strategy
        registers indicators today (`grep -rn register_indicator src/core/strategies/` → zero), so
        this is latent — but Story 4.4 rewrites `on_start` to register them. Pick `handle_*` for
        that forward reason and say so; do not justify it with a claim about today's strategies.
  - [x] Read `strategy.id` **lazily, at failure time**, never captured at wrap time:
        `Trader.add_strategy` rewrites the id when `order_id_tag` is `None`
        (`trading/trader.py:405-423`, measured `SMACrossover-None → SMACrossover-000`), and the
        guard is applied before that call.

- [x] **Task 3 — Wire the guard into the trading phase** (AC: #1, #2, #7)
  - [x] RED: `tests/component/core/test_session_runner_strategy_failure.py` (component tier). Copy
        the `_assert_c_logging_state_is_unchanged` **and** `_isolate_env` autouse fixtures from
        `tests/component/core/test_session_runner_phases.py:60-90` **verbatim**, plus a registry
        snapshot/restore autouse fixture modelled on
        `tests/component/api/test_run_backtest_routes.py:15-52`. Assert against `TestLiveNode` that
        every materialised strategy is guarded before `add_strategy` is called.
  - [x] RED: the **real dispatch proof**, in the same file and still component tier — a real
        `MessageBus` + `Cache` + `Portfolio` + two registered, started `Strategy` objects on the
        **same** bar type. Measured: this leaves `is_logging_initialized()` **False** at every step,
        so it satisfies the C-logging fixture and does **not** need `--forked`. Publish one bar;
        assert the raiser recorded it and **the sibling recorded that same bar**.
        **Parametrise both registration orders** (AC #2).
  - [x] ⚠️ **Seed the instrument into the Cache before `start_strategy`** —
        `cache.add_instrument(TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ"))`.
        Without it `SMAMomentum.on_start` (`src/core/strategies/sma_momentum.py:63-72`) finds
        `cache.instrument(...)` is `None`, calls `self.stop()` and returns **before**
        `subscribe_bars`. MEASURED: `bar subs: 1`, momentum `STOPPED` — the survivor never
        subscribes and AC #2's assertion becomes untestable. Assert `sibling.is_running` after
        `start()` as a guard on the guard. The same seeding applies to Task 9's probe.
  - [x] ⚠️ **Do not assert `bus.pub_count == 1`.** It is a cumulative bus counter, not per-publish:
        registering and starting two strategies already drives it to **6**, and one guarded bar that
        ends in `degrade()` adds **3** (the bar plus two `ComponentStateChanged` publishes). Capture
        `before = bus.pub_count`, publish, assert `bus.pub_count > before` — `component.pyx:2785` is
        reached only when no subscriber raised. Do **not** pin the delta.
  - [x] RED: the negative control in the same class, **with per-order expectations** — without the
        guard: raiser **first** → sibling records 0 and `publish` raises; raiser **last** → sibling
        records 1 and `publish` still raises. In **both** orders `bus.pub_count` does not advance.
        Pin the control on `pub_count`, not on the sibling's count, so it is order-independent. This
        is what stops the positive test passing vacuously.
  - [x] GREEN: call `self._guard.wrap(strategy, spec_strategy_id=spec.strategy_id)` in
        `LiveSessionRunner._phase_trading`, between
        `materialise_strategy(...)` and `trader.add_strategy(...)`. **Two lines in the runner** —
        see the file-size budget; the policy lives in the guard module, not here.
  - [x] Use the zero-close-bar fixture from finding #9 as the raiser rather than a monkeypatched
        `raise Exception("boom")`: a real `sma_crossover` fed a `0.00` close raises
        `decimal.DivisionByZero` at `sma_crossover.py:150` through the real `Actor.handle_bar`
        re-raise. Nautilus accepts a zero-priced `Bar` (measured).

- [x] **Task 4 — Contain the start path** (AC: #5, #7)
  - [x] RED: in the same component file — a two-spec session where the **first** spec raises in
        `on_start`; assert the second is still added and started, that the phase reaches `ok`, and
        that `session.started` is emitted. Then a session where **every** spec fails to start;
        assert the `trading` phase logs `failed`, the sequence stops, and no later phase runs.
  - [x] GREEN: per-spec `try`/`except Exception` **inside** `with phase(self._log, "trading")`,
        never around it — wrapping the phase would defeat AR39's *"a failure in any phase stops the
        sequence"* for genuine phase failures. On failure: call `guard.record_start_failure(...)`,
        call `fault()` (not `degrade()`, not `stop()` — AC #5), and continue the loop.
  - [x] The same `except` also catches the `order_id_tag` `RuntimeError` from `add_strategy`
        (finding #10) — a latent startup failure for a spec order that validated perfectly at
        create time. Note it in `deferred-work.md`; do not add a validator here.

- [x] **Task 5 — The migration and the column** (AC: #4)
  - [x] ⚠️ **`alembic/versions/` is hook-protected.** The second Phase-3 migration is **sanctioned
        by Allay, 2026-08-23** (see the blockquote under AC #4) — cite that when the hook asks, and
        ask before editing rather than working around the hook.
  - [x] RED: `tests/unit/db/test_migration_runtime_flags.py` — the revision's `down_revision` is
        `d08dfbd393f0`, `upgrade()` adds one nullable JSONB column, `downgrade()` drops it, and
        `alembic heads` still reports a single head.
  - [x] GREEN: the migration, plus `runtime_flags: Mapped[Optional[dict]] = mapped_column(JSONB,
        nullable=True)` on `src/db/models/trading_session.py`. Run `alembic upgrade head` and the
        `downgrade -1 → upgrade` round trip against the live database, as Story 2.2 did.
  - [x] RED then GREEN: `_apply_transition` clears `runtime_flags` to `None` on every
        `-> running` edge, alongside `_TIMESTAMPS_BY_TARGET`'s stamping
        (`src/services/session_service.py:320-322`). Without this, `live status` reports a failure
        from three process runs ago.

- [x] **Task 6 — The third port method, end to end** (AC: #4)
  - [x] RED: `tests/unit/core/test_live_session_record.py` — the port declares **three** methods and
        a hand-written double still satisfies `isinstance`. ⚠️ Only two places are **hard**
        failures: `:51` (asserts the exact method set) and `:61` (`_HandWrittenRecord` isinstance),
        plus `tests/unit/services/test_session_record_adapter.py:86`. The three component
        `SpyRecord`s — `test_session_runner_phases.py:133`, `test_session_runner_stop.py:106`,
        `test_session_steady_state.py:50` — are duck-typed and only need the method if a test in
        that file drives it; `test_session_steady_state.py`'s does, because the tick now drains the
        queue. Budget for all three anyway.
  - [x] RED: `tests/unit/services/test_session_service.py` — a failure appends to `runtime_flags`,
        two failures append two entries, the ownership guard (`_refuse_if_reclaimed`) refuses a
        reclaimed row, and a `stopped` row refuses.
  - [x] GREEN: `SessionRecordPort.record_strategy_failure(...)` (stdlib types only — `str`,
        `datetime`; **no** `StrategyId`, no `Strategy`, no exception object crosses AR38's
        boundary; the runner translates to primitives at the catch site).
  - [x] GREEN: module-level `_record_strategy_failure(...)` in `src/services/session_service.py`
        plus a thin delegating method, following Story 2.5's precedent for keeping the class small.
        ⚠️ **The JSONB list must be reassigned, not mutated in place** — SQLAlchemy does not track
        in-place mutation of a plain `JSONB` column, so `row.runtime_flags["failed_strategies"]
        .append(...)` silently never persists. Build a new dict and assign it.
  - [x] GREEN: `SqlSessionRecord.record_strategy_failure(...)` in `src/services/session_record.py`,
        one short-lived transaction, translating `InvalidSessionTransition` to
        `SessionReclaimedError` exactly as its two siblings do.
  - [x] GREEN: **the call site is `SessionSteadyState`'s tick, not the guard** (AC #10). Drain
        `guard.drain_pending()` on the same tick that writes `_write_activity`, through that
        object's **own** `ThreadPoolExecutor` — never `asyncio.to_thread`, whose default executor
        is the kernel's and which `dispose()` joins with `wait=True` (measured 59.81s block,
        decision D2). `SessionReclaimedError` here follows the existing `_ownership_lost` route;
        everything else logs `session.strategy_record_failed` and continues (AR42).
  - [x] ⚠️ Update `SessionReclaimedError`'s docstring
        (`src/core/live_session_record.py:52-57`). It rejects an owner/epoch fencing column
        *"because this phase's single migration is spent"* — a reason Task 5 has just falsified.
        Either restate the rejection on its own merits or record "the fencing column is re-opened
        by Story 2.7's migration" as a new `## Deferred from: story-2.7` item, referencing NFR6.
  - [x] No repository change: this is a service-level mutation on a loaded ORM object, the same
        shape as `_stamp_activity`. State in the Dev Agent Record that **AR9's dual-repository rule
        is therefore not triggered**, rather than leaving a reviewer to wonder.

- [x] **Task 7 — Engine-level defence in depth** (AC: #6)
  - [x] RED: `tests/component/core/test_live_node_builder.py` — the built `TradingNodeConfig` has
        `graceful_shutdown_on_exception is True` on all three engines. Plus the **canary**:
        `LiveDataEngineConfig().graceful_shutdown_on_exception is False`, so an upgrade that flips
        the Nautilus default fails by name.
  - [x] GREEN: pass the three engine configs in `build_trading_node_config`'s `TradingNodeConfig(...)`
        call, in the same block as the `NODE_TIMEOUT_*` arguments and with the same
        "passed unconditionally, including when they equal today's defaults" rationale.
  - [x] ⚠️ **Do not add a `try` to `SessionSteadyState.note_bar`.** It looks like the same hole and
        it is not worth opening: its body is two statements that cannot raise with the production
        clock, and its docstring (a Story 2.5 review artefact) states that **no test may inject a
        raising clock here** — so the RED test cannot be written without breaking an explicit
        in-code prohibition, and the guard would be unreachable code in a file already at 507 of
        500. The engine flag above is what covers `note_bar`, and that is exactly what AC #6 says
        the flag is for. If a future story does add it, `tests/component/core/test_session_steady_state.py`
        (13 `note_bar` call sites) and that docstring both need updating in the same edit.
  - [x] ⚠️ Add a comment stating in one sentence that this flag fixes **neither** AC #1 nor AC #2:
        measured, `True` publishes `ShutdownSystem` → `kernel.stop_async()` (ends the session), and
        with two co-subscribed strategies the sibling still saw `{'A': 3, 'B': 0}`.

- [x] **Task 8 — The AC #3 AST guard** (AC: #3, #7)
  - [x] RED: `tests/unit/core/test_live_node_never_exits.py` (unit tier, `ast` only). Reuse
        `_called_names` from `tests/unit/core/test_live_stop_path_is_inert.py:82-99` verbatim.
        Forbidden names: `exit`, `_exit`, `abort`, `quit`, `SystemExit`. Scanned modules: the
        node-facing set, **excluding** `live_session_signals.py` and `src/cli/commands/live*.py`,
        each exemption named with its reason inline.
  - [x] RED: plant a probe for **every** forbidden name and assert the scan finds each one, plus a
        prose probe (a docstring naming `os._exit`) asserting the scan does **not** fire on it.
        Story 2.3's AR37 guard examined 4 of 192 files and passed a planted probe; every AST scan
        in this repo carries a non-vacuity assertion for that reason.
  - [x] GREEN: add `src/core/live_strategy_guard.py` to the three **hand-maintained** module lists
        in the same commit — `STOP_PATH_MODULES` and `NEW_OR_MODIFIED_FOR_STOP` in
        `tests/unit/core/test_live_stop_path_is_inert.py:30-37` and `:221-224`, and
        `TestImportPurity.MODULES` in `tests/component/core/test_session_runner_phases.py:1138-1142`.
        That file's own comment records that its coverage already shrank once through exactly this
        omission when Story 2.6 split out `live_start.py`.
  - [x] ⚠️ The AR38 purity scan in `test_session_runner_phases.py` iterates `tree.body` only — it
        sees **top-level imports only**, so a function-local `import sqlalchemy` in a new module
        passes it. The `live_dependency_invariance` test's `_imported_roots` walks the whole tree.
        Know which is which before trusting either.

- [x] **Task 9 — The integration proof: the process survives** (AC: #1, #3)
  - [x] RED: `tests/integration/core/test_live_strategy_failure_survives.py`, marker `integration`,
        run under `--forked`. Embed a fresh-interpreter probe as a module-level string in the idiom
        of `tests/integration/core/test_live_node_lifecycle.py:176-220`, driven by
        `subprocess.run(..., timeout=N)` — **the timeout is the assertion**; there is no
        `pytest-timeout` in this repo.
  - [x] Assert `returncode == 0` and a `SURVIVED` line for the guarded build, **and** ship the
        inverted meta-test (the `test_live_node_lifecycle.py:256-277` pattern): the same probe with
        the guard removed exits **1** with no `SURVIVED` line. That inverted run is the permanent
        mutation proof a `TestLiveNode` double cannot give — the double has no message bus, no
        engine and no kernel.
  - [x] ⚠️ Two harness bugs from Story 2.6 will recur verbatim; the fixes are already written in
        `tests/integration/core/test_live_session_signal_ownership.py`. (a) **Drain the child's
        pipes on a background thread** — `TradingNode` construction's ~100 banner lines fill the
        `subprocess.PIPE` buffer and the child blocks on `write()`. (b) **Match log lines exactly**,
        never by substring: Nautilus logs its own `Cache: READY` lines and a `"READY"` substring
        check fires mid-construction.
  - [x] ⚠️ A test that only asserts a structlog record would pass against a design that still
        crashes. The return code is the assertion.

- [x] **Task 10 — The operator-facing surface** (AC: #1, #4, #8)
  - [x] RED: `tests/unit/cli/commands/test_live_cli.py` — `_print_stop_result` names any contained
        failures, and prints nothing extra when there were none.
  - [x] GREEN: extend `_print_stop_result` (`src/cli/commands/live.py:419`) with the contained
        failures, read from a runner property. Exit code stays **0**: the session ran and stopped;
        AR28's table has no code for "a strategy failed" and Story 1.7 recorded that inventing one
        is worse than a generic failure.
  - [x] ⚠️ AR36 vocabulary: `tests/unit/core/test_live_stop_path_is_inert.py:221-237`
        word-boundary-matches `pause|halt|kill|close|finalize` against operator-facing string
        literals in `live_session_runner.py` and `live_session_signals.py`. Use *contained*,
        *failed*, *degraded*. `strategy.halted` would fail the build.

- [x] **Task 11 — Mutation-test every guard, then close the paperwork** (AC: all)
  - [x] Break each guard, watch the named test go red, record the exact assertion message, revert.
        The mutations, named up front:
        1. Remove the per-strategy guard entirely → the component sibling test **and** the
           integration `rc == 0` probe both go red.
        2. Guard only the **first** strategy in the `_phase_trading` loop → the two-strategy
           component test goes red; a one-strategy test would not, which is why AC #2 requires two.
        3. Catch `Exception` but re-raise after logging → the integration probe goes red with
           `rc == 1`.
        4. Drop `strategy_id` from the failure record (or capture it at wrap time instead of at
           failure time) → AC #1's record assertion goes red.
        5. Swap the explicit redacted `traceback=` field for bare `exc_info=True` → AC #8's
           assertion goes red. Measured: `capture_logs` records only `exc_info: True`, never a
           string.
        6. Implement `redact_accounts` as `mask_account(text)` → AC #8's *"`is not managed` still
           present"* assertion goes red while the naive *"`DU4076626` absent"* one stays green.
           This mutation exists to prove the test is not vacuous.
        7. Remove the AC #9 latch → the once-per-strategy assertion goes red.
        8. Make the latch short-circuit before the base handler → the indicator-warmth assertion
           goes red (`ema.count` 1 instead of 8).
        9. Let the record port's `SessionReclaimedError` escape the guard → the integration probe
           goes red with `rc == 1` and zero output. **This is the most important one.**
        10. Point the AC #3 AST scan at an empty module tuple → the non-vacuity assertion goes red.
        11. Remove the `-> running` clear of `runtime_flags` → the stale-failure test goes red.
  - [x] Write **Procedure P8** in `docs/qa/phase3-live-verification.md` (the file ends at line 754,
        after P7). Follow P7's shape exactly. Skeleton in the design section below.
  - [x] Update `deferred-work.md`: strike/re-point the five items that name Story 2.7 (lines 860,
        1235, 1240, 1309, 1523), append a `## Deferred from: story-2.7 (<date>)` section, and add
        this story's new event names to the **one** existing running AR41 amendment item at
        `:1360` — not a second one.
  - [x] Add `traceback` to `_STDLIB_AND_FIRST_PARTY` in
        `tests/integration/core/test_epic1_ac_node.py:441-503` **with the reason inline**, exactly
        as Story 2.4 did for `socket` and Story 2.6 for `signal`/`sys`. Do **not** switch the
        allowlist to `sys.stdlib_module_names` — Story 2.4's review rejected that explicitly.
  - [x] Re-run all six `tests/integration/core/test_epic1_ac_*.py` files — **40/40 Epic 1 criteria**
        must still pass.
  - [x] `make format`, `make lint`, `make typecheck`. Record the **measured** file and class sizes
        before and after — do **not** claim "every class under 100"; Story 2.6's review patched
        exactly that false claim. State the over-cap files honestly (see the budget table).

### Review Findings

Adversarial review, 2026-08-23 (Blind Hunter / Edge Case Hunter / Acceptance Auditor; 26 raw
findings, deduplicated to 22, 0 dismissed). All 6 decisions resolved with Allay (every recommended
option taken) and applied same-day with the 14 patches; the 2 deferred items are in
`deferred-work.md` under `code review of 2-7…`. Full suites re-run green after the fixes: unit
2266, component 1289/16 skipped, integration survives-file 6 (incl. 2 new pins), Epic 1 sweep
40/40, format/lint/mypy clean.

- [x] [Review][Decision→fixed] Drained failures are dropped forever on a transient DB error — the
      "unbounded state" rationale for not re-queuing is false (the guard latches per strategy, so
      the backlog is bounded by strategy count), and a dropped write can leave
      `all_failed: true` beside an incomplete `failed_strategies` list with no reconciliation
      path. (blind+edge) [src/core/live_session_steady_state.py `_write_strategy_failures`]
- [x] [Review][Decision→fixed] End-of-run persistence window: a failure contained within the last
      tick (~30s) before a stop, or the all-failed `NoStrategyStartedError` start path, never
      reaches `runtime_flags` — and the stopped-row refusal then makes that permanent, while the
      CLI trailer tells the operator the column holds "the same facts". (blind+auditor)
      [src/core/live_session_steady_state.py; src/services/session_service.py:479-483]
- [x] [Review][Decision→fixed] A `BaseException` raised by strategy code — `sys.exit()` in `on_bar`,
      a re-raised `CancelledError` — escapes the guard's `except Exception` and lands on exactly
      the `os._exit(1)` path the story exists to prevent; the module docstring's "Nothing leaves
      a wrapped handler. Nothing." overstates the boundary and no test or disclosed limit records
      it. (blind+edge) [src/core/live_strategy_guard.py]
- [x] [Review][Decision→fixed] Unlocked read-modify-write of `runtime_flags` vs a concurrent reclaim:
      a dispossessed incumbent that passes `_refuse_if_reclaimed` on a stale read can stamp a
      pre-reclaim failure doc that survives the successor's **entire run** (clearing happens only
      on the `-> running` edge, already passed) and clobbers entries the successor recorded.
      (edge) [src/services/session_service.py:479-503]
- [x] [Review][Decision→fixed] `redact_accounts` drops the spec-pinned "plus the configured
      TWS_ACCOUNT when set" clause (AC #8's "this is the contract" surface) — a unilateral
      narrowing recorded in deferred-work.md but never sanctioned; the regex is also
      case-sensitive, so a lowercased identifier passes unredacted. (blind+auditor)
      [src/core/live_strategy_guard.py `_ACCOUNT_TOKEN`]
- [x] [Review][Decision→fixed] The guard wraps only `handle_bar`/`handle_event`; `handle_bars`,
      `handle_historical_data`, tick and data handlers are unwrapped — and `sma_momentum`
      already drives the request path at warm-up — yet disclosed limit #1 names only "a raising
      `LiveClock` timer callback". Extend coverage, or extend the disclosure? (edge)
      [src/core/live_strategy_guard.py `GUARDED_HANDLERS`]
**How the six decisions were resolved** (each the recommended option): (1) failed writes are
re-queued via `StrategyGuard.requeue` and retried next tick — bounded by the per-strategy latch;
(2) the runner's `finally` now flushes the guard's queue synchronously before `mark_stopped`
(`_flush_contained_failures`), closing both the last-tick window and the all-failed start path;
(3) the wrapper catches `BaseException`, re-raising only `KeyboardInterrupt`/`CancelledError`;
(4) `record_strategy_failure` loads `FOR UPDATE` — the pinned no-lock test inverted to pin the
lock; (5) `redact_accounts(text, account=...)` implements the pinned TWS_ACCOUNT clause,
case-insensitively, threaded from `IBKRSettings.tws_account` as a plain string;
(6) `GUARDED_HANDLERS` stays pinned — disclosed limit #1 widened + deferred-work entry.

- [x] [Review][Patch] Every strategy-failure write erases `runtime_flags` keys it does not know
      about — Story 2.8's planned `connection_lost_at` would be silently dropped; carry unknown
      keys forward and pin it. [src/services/session_service.py:499-503]
- [x] [Review][Patch] CLI trailer "the other strategies were unaffected" prints unconditionally
      — including when **all** strategies failed, and for single-strategy sessions where no
      "other strategies" exist. [src/cli/commands/live.py:508-514]
- [x] [Review][Patch] CLI AR36 vocabulary test checks only `("halted", "killed", "paused")` —
      the canonical list is `pause/halt/kill/close/finalize`, word-boundary stem-matched, and
      the structural scan does not reach this module. [tests/unit/cli/commands/test_live_cli.py:1253]
- [x] [Review][Patch] AC #7's teardown-skip pin test does not exist — no test pairs a
      `DEGRADED` strategy with the skipped `on_stop()` at teardown, while deferred-work.md
      claims "Pinned by a test whose docstring says exactly this."
      [tests/component/core/test_session_runner_strategy_failure.py]
- [x] [Review][Patch] Pre-verified finding #12's dispatch-order pin test was never written —
      nothing asserts the measured `['observer', 'note_bar', <strategies>]` bus order that
      AC #4's `last_bar_at` rationale depends on. [tests/component/core/test_session_runner_phases.py]
- [x] [Review][Patch] Contained failures are never printed when `run()` ends via an exception
      (e.g. the reclaim interleaving) — `_print_contained_failures` is reached only on normal
      return, and in exactly that path the write was also refused. [src/cli/commands/live_start.py:409-416]
- [x] [Review][Patch] `_fault_quietly`'s docstring cites the measured behaviour of `fault()`
      from `PRE_INITIALIZED`, but the common failure state on this path is `STARTING` (per this
      story's own component test). [src/core/live_session_runner.py:461-486]
- [x] [Review][Patch] `drain_pending`'s copy-then-clear is unsynchronized; an atomic swap
      (`drained, self._pending = tuple(self._pending), []`) closes the loss window at zero cost.
      [src/core/live_strategy_guard.py `drain_pending`]
- [x] [Review][Patch] Guard-failure log events put the spec id in the `strategy_id` field,
      mixing the two identifier semantics the rest of the diff keeps apart.
      [src/core/live_strategy_guard.py `_report_guard_failure`; src/core/live_session_runner.py `_fault_quietly`]
- [x] [Review][Patch] `test_a_clean_handler_is_untouched_and_its_return_value_survives` asserts
      `_guard().failures == ()` on a **fresh** guard — trivially true; the wrapping guard is
      discarded unnamed. [tests/unit/core/test_live_strategy_guard.py:169]
- [x] [Review][Patch] Migration head-count test uses substring membership against a joined
      string (`revision not in " ".join(down_revisions)`) — any id that is a substring of
      another is wrongly excluded from heads. [tests/unit/db/test_migration_runtime_flags.py:94-96]
- [x] [Review][Patch] The integration probe — "the only test that can prove the story's central
      claim" — synchronizes with bare `sleep(0.3)`/`sleep(0.2)`; poll the observable condition
      with a deadline instead. [tests/integration/core/test_live_strategy_failure_survives.py]
- [x] [Review][Patch] The structural test for `graceful_shutdown_on_exception` collects keyword
      names module-wide and never binds the flag to the three engine-config constructors — the
      argument can be dropped from any one engine undetected.
      [tests/component/core/test_live_node_builder.py]
- [x] [Review][Patch] Disclosed size measurements are internally inconsistent
      (`live_session_steady_state.py` "567 after" vs measured 574; `StrategyGuard` 312 vs 320).
      [deferred-work.md:1571-1572; Dev Agent Record]
- [x] [Review][Defer] Any infrastructure exception inside the per-spec start loop is
      misattributed as a spec failure (N `strategy.start_failed` events + "Every specification
      failed" pointing the operator at their specs for a trader/node-level fault)
      [src/core/live_session_runner.py:562-570] — deferred: distinguishing infra from spec
      faults is a design question for the story that adds error classification.
- [x] [Review][Defer] AC #1's heartbeat-advance clause has no automated proof — the integration
      probe drives a bare `LiveDataEngine` with no runner or steady state; the clause lives in
      P8 criterion 1, which is ⛔ not run [docs/qa/phase3-live-verification.md] — deferred:
      sanctioned by the story's own P8-not-run policy; run P8 before Epic 2 closes.

---

## Dev Notes

### What this story owns, and what it must not touch

**Owns:** `src/core/live_strategy_guard.py` (new), the guard's wiring into `_phase_trading`, the
start-path containment, the `runtime_flags` column and its migration, the third `SessionRecordPort`
method and its service/adapter halves, the three engine `graceful_shutdown_on_exception` settings,
the `note_bar` guard, the AC #3 AST scan, `ntrader live start`'s contained-failure output,
Procedure P8, and their tests.

**Does not own — do not build these here:**

- **No strategy-file edits. None.** `grep -rn "is_live" src/` is zero and must stay zero (AR40).
  `sma_crossover.py:150`'s `DivisionByZero` is this story's **test fixture**, not its bug to fix —
  fixing it would delete the only non-synthetic failure the tests have. `sma_crossover.on_stop()`'s
  `close_all_positions()` is **Story 3.1's** (`epics.md:1046-1075`), and the indicator warm-up in
  `on_start()` is **Story 4.4's** (`epics.md:1380-1410`). Both are named as the *two sanctioned
  strategy edits* in AR40 and neither is this story's.
- **No `ntrader live status` / `list`, no `--json`, no health derivation.** Story 2.8. This story
  **writes** `runtime_flags`; it does not read it back for display. Story 2.8 reads it in the same
  single-row query it already needs, and decides how `degraded` covers both senses AR32 names.
- **No fifth `SessionStatus` value.** `deferred-work.md:855` decided that, and the `session_status`
  PG type has exactly four labels. The column is the answer, not a new enum member.
- **No seal, no `sealed_run_id`.** Epic 5.
- **No reconciliation, no warm-up.** `reconcile` and `warmup` stay no-op placeholders, and
  `ConnectionMonitor.confirm_state_reestablished()` stays uncalled — `deferred-work.md` warns
  verbatim that calling it *"would satisfy the type signature while defeating the design"*.
- **No orders, no trades, no `live_trade_recorder`.** Epic 3. `handle_event` is wrapped now because
  wrapping it later would be too late (see finding #5), but nothing acts on order events here.
- **No `--strategy multiple=True` on `live create`.** The CLI cannot express a two-strategy session
  today (`src/cli/commands/live.py:222-227` and `:290`), and nothing in AC #2 requires it — the test
  builds the `SessionSpec` in-process, exactly as
  `tests/component/core/test_session_runner_phases.py:871-877` already does. Adding it would need a
  `--param` namespacing scheme that does not exist.
- **No retry / backoff anywhere.** AR24 is absolute on the order path and the same instinct is wrong
  here: a strategy that raised once is degraded, not retried.
- **No new dependency.** AR3 is absolute. ⚠️ **You *will* trip Epic 1's dependency guard, and that
  is the guard working.** `_live_module_sources()` globs `src/core/live_*.py` plus
  `src/cli/commands/live*.py`, so `live_strategy_guard.py` is swept automatically, and `traceback`
  is **not** in the hand-curated allowlist. Add it with the reason inline.
- **No runner split.** Decided with Allay, 2026-08-23: `live_session_runner.py` is recorded as a
  **sanctioned over-cap exception**, the way `src/cli/commands/import_data.py` (**732**) already is, rather
  than split. See the budget table.

---

### ⚠️ Blockers and preconditions

**Nothing blocks Tasks 1–4 and 7–10.** Every Nautilus fact below was established with no broker, no
Redis and no Postgres.

**Tasks 5 and 6 need Postgres** (`alembic upgrade head`, currently head `d08dfbd393f0`). This repo
runs PostgreSQL via Homebrew, not Docker.

**`alembic/versions/`, `.env.example` and `pyproject.toml` are hook-protected.** Task 5 needs
`alembic/versions/`. The second Phase-3 migration is **sanctioned by Allay, 2026-08-23** — cite the
blockquote under AC #4 when the hook asks. Do not work around the hook.

**Task 9's integration test constructs a real `LiveDataEngine` in a subprocess** and therefore
claims the Nautilus C logging subsystem. It **must** live under `tests/integration/` and run
`--forked`. Never construct one in the component tier —
`test_session_runner_phases.py`'s autouse `_assert_c_logging_state_is_unchanged` fixture exists to
catch exactly that, and you should copy it into any new component file verbatim.

**Task 3's real-dispatch proof is deliberately component tier, not integration.** Measured: a real
`MessageBus` + `Cache` + `Portfolio` + registered, started `Strategy` objects + `publish` leaves
`is_logging_initialized()` **False** at every step. This is the opposite of the cautious default and
it is correct; putting it in `--forked` would cost minutes per run for nothing.

**Procedure P8 needs IB Gateway/TWS on the paper port, plus Redis and Postgres.** Nothing in Tasks
1–11's automated half requires it.

**Do not write a test that depends on a `custom/` submodule strategy.** `StrategyRegistry.discover()`
swallows `ImportError` into `warnings.warn` (`src/core/strategy_registry.py:271-275`), so CI without
the submodule checked out silently drops 5 of the 7 registrations. Only `sma_crossover` and
`momentum` are in-repo and safe.

---

### Pre-verified findings

Everything below was **executed** against this repo and the installed `nautilus-trader 1.220.0` in
its `.venv` while drafting. Reading and running the wheel rather than reading its docs is the
technique the Epic 1 retro named (Key Insight #5), and it overturned the epic's own AC wording
twice here. Do not re-derive these; do re-confirm anything surprising — Task 1 asks you to.

#### 1 — Nautilus catches the strategy's exception, logs it, and **re-raises** it

`common/actor.pyx:3743-3748`:

```python
if self._fsm.state == ComponentState.RUNNING:
    try:
        self.on_bar(bar)
    except Exception as e:
        self.log.exception(f"Error on handling {repr(bar)}", e)
        raise                                    # <-- re-raised, always
```

The identical shape appears at **17 sibling sites** (`on_instrument`, `on_quote_tick`,
`on_trade_tick`, `on_data`, `on_event`, …) and at `trading/strategy.pyx:1714-1716` for the
order/position dispatch. There is no containment anywhere in `Actor` or `Strategy`. What Nautilus
guarantees is a log line before the raise continues upward — and that line is **Rust-side**, so
`structlog.testing.capture_logs()` cannot see it and it identifies the strategy by
`component_id` (the **class name**), never by `StrategyId`: measured,
`[ERROR] TRADER-000.MyStrat: Error on handling Bar(...)` while the id was `MyStrat-007`.

#### 2 — A raising subscriber aborts the whole publish, starving every later subscriber

`common/component.pyx:2754-2757` is a bare loop with no per-handler guard:

```python
for i in range(len(subs)):
    sub = subs[i]
    sub.handler(msg)          # no try/except
```

EXECUTED, four orderings on a real `MessageBus`:

| subscription order | priorities | handlers that ran | publish |
|---|---|---|---|
| `bad`, `good` | 0, 0 | `['bad']` | raised to publisher |
| `good`, `bad` | 0, 0 | `['good', 'bad']` | raised to publisher |
| `bad`, `good` | 10, 0 | `['bad']` | raised to publisher |
| runner wildcard, `stratA`(raises), `stratB` | 0, 0, 0 | `['runner_wildcard', 'stratA']` | raised |

`pub_count` stayed **0** in every failing case (`component.pyx:2785` is never reached). Ordering is
priority-descending, ties in registration order.

**This is AC #2's actual failure mechanism, and it is per-bar.** A fix at the engine or queue level
is already too late — the sibling has been skipped for that bar. Containment must be inside the
failing strategy's own handler, before the raise re-enters `publish_c`.

#### 3 — The default outcome is `os._exit(1)`, and it is usually silent

`live/data_engine.py:347-365`:

```python
if self.graceful_shutdown_on_exception:
    ...  self.shutdown_system(...)
else:
    self._log.error("System will terminate immediately to prevent operation in degraded state")
    os._exit(1)                                  # Immediate crash
```

Identical code at `live/execution_engine.py:380-398` and `live/risk_engine.py:212-230`. EXECUTED:
`LiveDataEngineConfig`, `LiveExecEngineConfig` and `LiveRiskEngineConfig` all default
`graceful_shutdown_on_exception=False`, and `grep` for `data_engine=`/`graceful_shutdown` in
`src/core/live_node_builder.py` returns **nothing** — this repo inherits all three defaults.

EXECUTED end to end in a fresh interpreter (real `LiveDataEngine`, two real `Strategy` objects):

- uncontained → **`rc=1`, output file 0 bytes**
- with a per-strategy wrapper → **`rc=0`, `SURVIVED boom=1 fine=1`**

The explanatory ERROR line survived **4 of 20** runs: `os._exit` skips `atexit`, every `finally`,
and the async Rust log flush. Do not put a percentage in a test; state that the line is usually
lost.

⚠️ **Nautilus's own docstring for this flag is factually wrong.** `live/config.py:48-50` claims the
behaviour *"does not include user actor/strategy exceptions"*. The measured traceback says
otherwise: `live/data_engine.py:482` → `data/engine.pyx:1655` → `:1874` → `common/component.pyx:2757`
→ `common/actor.pyx:3745` → user code. Do not design against that docstring and do not let a
reviewer cite it.

#### 4 — The queue task survives, so the runner boundary is never reached even when the process does

EXECUTED with `graceful_shutdown_on_exception=True` and two failing bars: `data_queue_task.done()`
= **False**, `engine.is_running` = **True**, `engine.state` = `RUNNING`, `data_count` = 2. The
`while True` loop's per-iteration `except Exception` continues
(`live/data_engine.py:476-487`), so `node.run_async()`'s `asyncio.gather` over the queue tasks
(`live/node.py:351-371`) **never returns**, and `_serve()`'s `task.result()` is never reached.

There is no upward propagation to catch. That is why AC #1 is re-read as "the boundary the runner
installs", and it is not a wording convenience — it is the only thing that can be true.

#### 5 — The working seam is an instance-level override of `handle_*`, applied before `add_strategy`

EXECUTED, both directions:

- A **bare** `Strategy()` is a cdef class with no `__dict__`; `s.handle_bar = fn` raises
  `AttributeError: attribute 'handle_bar' is read-only`.
- **Every Python subclass** accepts assignment of `handle_bar`, `handle_event`, `on_bar`, `on_event`,
  and the `MessageBus` dispatches the instance attribute — `bus.subscriptions(...)` showed the plain
  function after the patch. All seven registered strategies are Python subclasses.

**Ordering is load-bearing.** `Strategy.register()` — called by `Trader.add_strategy` — subscribes
the **bound** `self.handle_event` to `events.order.{id}` / `events.position.{id}`
(`trading/strategy.pyx:314-315`), and `subscribe_bars` binds `self.handle_bar` during `on_start`
(`common/actor.pyx:1804-1806`). Wrapping at materialisation time is **before both**. Wrapping after
`add_strategy` would leave `handle_event` bound to the unwrapped method forever.

EXECUTED end to end on a **real `LiveDataEngine` with the default (`os._exit`) config**: override
`handle_bar` → `try`/`except` → `self.degrade()` gave `EXIT_CODE=0`,
`bars seen: {'A': 1, 'B': 3}`, `contained: ['A-A:RuntimeError']`, `A.state: DEGRADED`,
`B.state: RUNNING`, `data task alive: True`.

#### 6 — The start path fails differently, and the current loop starves every later spec

`Component.start()` logs and re-raises, halting the state transition
(`common/component.pyx:1888-1902`), and `Trader._start()` is a bare `for` loop. EXECUTED with
strategy A raising in `on_start`: `run() raised RuntimeError`, `on_start called: ['A']`,
`strategy states: {S-000: 'STARTING', S-001: 'READY'}`, `trader state: STARTING`.

`src/core/live_session_runner.py:502-505` has exactly that shape today. Recovery verbs from
`STARTING`, measured: `stop()` → `STOPPED` **and it does call `on_stop()`**; `fault()` → `FAULTED`;
`degrade()` is **illegal** and is swallowed as an error log, never raised
(`common/component.pyx:2130-2134` catches `InvalidStateTrigger`, logs, returns).

A raise in `on_stop` is the mirror image: EXECUTED, `strategy states: {S-000: 'STOPPING',
S-001: 'RUNNING'}` — one strategy's `on_stop` error leaves the others running while the node tears
down under them.

#### 7 — `degrade()` is the right isolation verb, and its traps are both measurable

FSM table `common/component.pyx:1571-1598`: `(RUNNING, DEGRADE) → DEGRADING`,
`(DEGRADING, DEGRADE_COMPLETED) → DEGRADED`, and from `DEGRADED` the legal triggers are
`RESUME`, `STOP`, `FAULT`. EXECUTED: a strategy degraded on bar 2 of 8 finished with
`on_bar calls: 2`, `ema.count after 8 bars: 8` (indicators keep updating — it stays warm for a
manual `resume()`), `state: DEGRADED`; `resume()` → `RUNNING`; `stop()` → `STOPPED`.

Two traps, both measured:

1. **`degrade()` from a non-`RUNNING` state is silently swallowed** — `READY → degrade() → still
   READY`, no exception. A guard that relies on it alone logs one contained failure per bar
   forever. Hence AC #9's latch, set **first**.
2. **`Trader.stop_strategy()` and `Trader._stop()` skip a `DEGRADED` strategy**, because both guard
   on `is_running`, which is `state == RUNNING` exactly (`common/component.pyx:1757-1767`).
   Measured: `Trader.stop_strategy(a.id)` left `A.state == DEGRADED`; a direct `a.stop()` did move
   it. So a degraded strategy's `on_stop()` never runs at teardown — see the ⚠️ under AC #7.

`Trader.remove_strategy()` is **not** an option: `trading/trader.py:685-693` pops the strategy and
deregisters its clock but **never unsubscribes** (measured: `removed A: bar_subs 2 → 2`), and it
deletes the strategy from `Trader.strategy_states()`.

There is **no** `on_error` hook, no per-component exception callback, and no asyncio loop exception
handler anywhere in the package — EXECUTED `grep -rnE 'def on_error|def on_exception|
set_exception_handler'` returns zero. The only knobs are the three `graceful_shutdown_on_exception`
flags. `Controller` offers no error surface either.

#### 8 — `graceful_shutdown_on_exception=True` fixes neither AC #1 nor AC #2

EXECUTED with `True`: exactly one
`ShutdownSystem(trader_id=TESTER-000, component_id=DataEngine, reason="Unexpected exception in Data
queue processing: RuntimeError(...)")` published on `commands.system.shutdown`, which
`system/kernel.py:576` subscribes and `:620-623` turns into
`self._loop.create_task(self.stop_async())` — a full graceful stop of the whole node.

EXECUTED with `True` **and two co-subscribed strategies**: `SEEN {'A': 3, 'B': 0}`, both strategies
still `RUNNING`. The sibling received **zero** of three bars. The `publish_c` dispatch abort is
upstream of the flag and completely untouched by it.

It is nevertheless strictly better than `os._exit(1)` — the process survives long enough for the
runner's `finally`, `mark_stopped()` and a flushed log — which is why AC #6 sets it as a backstop
and says out loud that it is one.

#### 9 — A real, non-synthetic failure fixture exists: the zero-close bar

EXECUTED with the repo's own registry and `materialise_strategy`: `sma_crossover` at `fast=2,
slow=3`, closes `['90.00', '95.00', '100.00', '105.00', '0.00']` published on a real `MessageBus`.
Bars 0–3 clean; bar 4 → **`decimal.DivisionByZero`**, frames `sma_crossover.py:88 on_bar` →
`:192 _check_for_signals` → `:250 _generate_sell_signal` → `:150 _calculate_position_size`
(`raw_qty = position_value / current_price`). Nautilus accepts a zero-priced — and even
negative-priced — `Bar`.

Two more real raise paths, ranked: `sma_crossover.py:217/:252`'s
`order_factory.market(quantity=...)` → `ValueError: 'init.quantity' not a positive real, was 0.0`
when `raw_qty` rounds to zero at instrument precision; and `:144`'s `Decimal * float` `TypeError`,
which is **not** reachable through the spec path (round-tripping `to_stored`/`from_stored` returns
`Decimal` on both).

⚠️ Do **not** use `momentum` as the raiser. `sma_momentum.py:65-69`'s `on_start` calls
`self.stop()` and returns when the instrument is unqualified, `(STARTING, STOP)` is a legal
transition, and `actor.pyx:3744` then gates on `state == RUNNING` — so it produces a silently
`STOPPED` strategy rather than an exception. `momentum` is the **survivor** in AC #2's pair.

#### 10 — `add_strategy` auto-assigns `order_id_tag` and can collide, order-dependently

`trading/trader.py:405-423` assigns `order_id_tag = f"{len(existing):03d}"` to any strategy whose
tag is `None`, rewrites the `StrategyId`, then raises if the tag is already taken. EXECUTED against
a real `Trader` using the repo's own `materialise_strategy`:

| spec order | outcome |
|---|---|
| `mean_reversion`, `sma_crossover` | **`RuntimeError`: `order_id_tag` conflict for `'001'`** |
| `momentum`, `sma_crossover`, `sma_crossover_long_only` | **`RuntimeError`: conflict for `'002'`** |
| `sma_crossover`, `mean_reversion` | OK |
| `sma_crossover`, `momentum` | OK — `['SMACrossover-000', 'SMAMomentum-002']` |
| `momentum`, `sma_crossover` | OK — `['SMACrossover-001', 'SMAMomentum-002']` |

Two consequences. **(a)** AC #2's pair must be `sma_crossover` + `momentum` — collision-free in both
orders, both in-repo. **(b)** This is a **latent startup failure in its own right**: a `SessionSpec`
that validated perfectly at create time can raise inside `_phase_trading`'s loop for an operator's
choice of strategy order. Task 4's per-spec `except` contains it; record it in `deferred-work.md`
rather than adding a validator here.

Also measured: `materialise_strategy` yields `SMACrossover-None` and `add_strategy` rewrites it to
`SMACrossover-000`. **Read `strategy.id` at failure time, never at wrap time.** A `SessionSpec`
cannot contain two specs of the same strategy (`_reject_duplicate_strategies`,
`src/models/session.py`), so ids are unique — but the `None → 000` rewrite happens after the guard
is applied.

#### 11 — There is nowhere to put a failure fact today, and the spec column is a trap

`trading_sessions` has exactly 13 columns (verified against the live database via
`information_schema.columns`), and only six are mutable at runtime: `status`, `last_started_at`,
`last_heartbeat_at`, `last_stopped_at`, `sealed_at`, `last_bar_at`. `sealed_run_id` is Story 5.3's.
`SessionStatus` has exactly four labels in the PG enum. `SessionRecordPort` has exactly two methods.
`ntrader live status` does not exist. `alembic heads` → **`d08dfbd393f0 (head)`**, 15 files, single
head.

⚠️ **The `spec` JSONB is not merely conventionally write-once — using it would brick the session.**
EXECUTED: `SessionSpec.from_stored({..., 'failed_strategies': [...]})` →
`ValidationError: Extra inputs are not permitted` (`extra="forbid"` at `src/models/session.py:227`
and `:351`), and `schema_version=2` → `ValueError: ... this build only understands up to
schema_version=1`. `live start` reads the spec to materialise strategies, so a spec-carried failure
fact makes the session permanently **unstartable**.

Redis is also out: EXECUTED `importlib.util.find_spec` for `redis`, `aioredis`, `valkey` → **all
absent**, and AR3 forbids adding one; `live_cache.py`'s socket code is a `PING` preflight, not a
client. AR10 makes Redis disposable anyway.

And the tempting zero-cost answer is a hazard: **deliberately withholding the heartbeat** to signal
death is exactly the condition that makes a `running` row **reclaimable** under AR33
(`session_service.py:159-212`), putting two live processes on one broker account — the NFR6
catastrophe. Named here so it is not re-invented at review.

#### 12 — The runner's own `note_bar` is first in dispatch order, and unguarded

`_phase_subscribe` registers `node.trader.subscribe(BAR_TOPIC, self._steady_state.note_bar)` at
`src/core/live_session_runner.py:492`, and `_phase_trading` adds strategies at `:495-505`. Since all
subscriptions use default priority 0 and `sorted(..., reverse=True)` is stable, `note_bar` runs
**before** every strategy — EXECUTED, order `['observer', 'note_bar', 's1']`.

Two consequences, in opposite directions. **Good:** a raising strategy cannot starve the first-bar
watchdog or `last_bar_at`. **Bad:** that also means `last_bar_at` keeps advancing when every
strategy is dead — which is the false green AC #4 exists to prevent — and `note_bar` itself runs
inline on the loop thread with no `try`, so if *it* raises the same `os._exit(1)` cascade fires.
**AC #6's engine flag is what covers that, not a `try` of its own** — see Task 7 for why adding one
is the wrong move here. Pin the ordering with a test; the mutation is to move the `subscribe` call
into `_phase_trading` after `add_strategy`. ⚠️ The exact measured order is
`['observer', 'note_bar', <strategies in registration order>]`: `_phase_subscribe` calls
`add_actor`/`start_actor` (`:490-491`) **before** `trader.subscribe(BAR_TOPIC, ...)` (`:492`), so
the `LiveBarObserver` is first, not `note_bar`. Assert that exact list.

#### 13 — The design was built and attacked before this story was written

Everything in this finding was executed against a **real `LiveDataEngine`** in a fresh interpreter,
or against a real `MessageBus` with the repo's own `materialise_strategy`. It is the difference
between a design that reads well and one that holds.

**What held:**

| Claim | Measured |
|---|---|
| The wrapper survives `Trader.add_strategy`'s `change_id` | wrapper still installed after the id rewrite |
| `Strategy.register()` subscribes the **wrapped** `handle_event` | `is our wrapper: [True]` |
| `subscribe_bars` in `on_start` binds the **wrapper**, not the base | confirmed |
| Re-entrant `degrade()` from inside `publish_c` | **no deadlock**; the outer publish completes for later subscribers |
| The whole AC #1 + #2 scenario | `rc=0`, `survivor_bars=3`, `a_state=DEGRADED` |

**What broke, and changed the design:**

1. **A raise from inside the guard's own `except` is fatal and silent.** Three modes of the same
   probe: no guard → `rc=1`, 0 bytes; guard → `rc=0`; **guard whose `except` raises → `rc=1`, 0
   bytes**. This is why AC #10 exists and why `SessionReclaimedError` does not propagate here.
2. **A short-circuiting latch freezes registered indicators.** 8 bars, `EMA(3)`, latch on bar 1:
   short-circuit → `ema.count: 1, initialized: False`; call through → `ema.count: 8,
   initialized: True`. This is why AC #9 latches the *side effects* only.
3. **`momentum` never subscribes with an empty Cache.** `on_start` finds `cache.instrument(...)`
   is `None`, calls `self.stop()`, returns before `subscribe_bars`: `bar subs: 1`, momentum
   `STOPPED`. With `cache.add_instrument(TestInstrumentProvider.equity("AAPL", "NASDAQ"))` both run
   and the survivor sees all five closes. This is why Task 3 seeds the Cache.
4. **`bus.pub_count` is cumulative, not per-publish.** It reads **6** before the first bar (two
   strategies registering and starting publish `ComponentStateChanged`), and one guarded bar ending
   in `degrade()` adds **3**. This is why Task 3 asserts a delta and does not pin it.

---

### The design

#### The containment, end to end

```
_phase_trading, per spec:
    strategy = materialise_strategy(spec)                        # SMACrossover-None
    guard.wrap(strategy, spec_strategy_id=spec.strategy_id)      # <-- NEW: handle_bar + handle_event
    trader.add_strategy(strategy)                                # rewrites id -> SMACrossover-000
    trader.start_strategy(strategy.id)                           # on_start -> subscribe_bars binds the WRAPPER
        |
        +-- raises?  -> guard.record_start_failure(...), fault(), next spec   (AC #5)

steady state, per bar:
    MessageBus.publish_c                     # registration order, measured
        -> LiveBarObserver                   # added in _phase_subscribe, first
        -> note_bar                          # subscribed in _phase_subscribe, second
        -> wrapped handle_bar                <-- the boundary
               |
               +-- base handle_bar -> indicators, then on_bar -> raises
                       |
                       +-- caught HERE. Never re-enters publish_c.            (AC #1, #2)
                              1. already latched? -> return, silently         (AC #9)
                              2. latch the strategy
                              3. log strategy.failed  (redacted traceback)    (AC #1, #8)
                              4. ENQUEUE the failure -- no I/O here           (AC #10)
                              5. if is_running: degrade()                     (AC #7)
                             (2-5 are themselves inside a bare except)        (AC #10)
        -> every other strategy              <-- still reached, in either order (AC #2)

steady state, per ~30s tick (SessionSteadyState.run):
    drain guard.pending() -> record.record_strategy_failure(...)  on the tick's OWN executor
        |
        +-- SessionReclaimedError -> the existing _ownership_lost route       (AC #10)
        +-- anything else -> log session.strategy_record_failed, continue     (AR42)
```

#### Public surface — this is the contract; do not invent a second shape

```python
# src/core/live_strategy_guard.py   (NEW — stdlib + structlog + first-party only, NO nautilus_trader)

GUARDED_HANDLERS: tuple[str, ...] = ("handle_bar", "handle_event")
#: `handle_*`, not `on_*`: Actor.handle_bar runs _handle_indicators_for_bar
#: BEFORE the RUNNING check and OUTSIDE the try (actor.pyx:3735-3744), so an
#: on_bar wrapper leaves a raising registered indicator uncontained. No repo
#: strategy registers indicators today; Story 4.4 makes them all do so.

def redact_accounts(text: str) -> str: ...
    #: Substitute each account-shaped token (\b[A-Z]{1,2}\d{6,10}\b, plus the
    #: configured TWS_ACCOUNT when set) with mask_account(match), IN PLACE.
    #: NOT mask_account(text) -- see the warning under AC #8.

class StrategyGuardError(Exception):
    """A strategy object refused to be wrapped. A startup failure, not a silent pass."""

@dataclass(frozen=True)
class StrategyFailure:            # all primitives: this crosses AR38's boundary
    strategy_id: str              # the NAUTILUS id, read at FAILURE time, not at wrap time
    spec_strategy_id: str         # the spec's own id, e.g. "sma_crossover"
    error_type: str               # type(exc).__name__
    handler: str                  # "handle_bar" | "handle_event" | "start"
    at: datetime
    detail: str                   # redacted, one line, capped at 200 chars

class StrategyGuard:
    """Owns the latch, the log, the pending queue and the degrade for ONE session.

    One instance per process run, constructed by LiveSessionRunner.__init__ from
    what the runner already holds. It does NOT hold the record port: the port is
    reached only from the steady-state tick (AC #10), which already has it.
    """
    def __init__(self, *, log: Any, time_source: Callable[[], datetime]) -> None: ...

    def wrap(self, strategy: Any, *, spec_strategy_id: str) -> None:
        """Install the boundary. Raises StrategyGuardError if setattr is refused."""

    def record_start_failure(self, *, spec_strategy_id: str, exc: BaseException) -> None:
        """AC #5's path. No strategy object to read an id from yet."""

    def drain_pending(self) -> tuple[StrategyFailure, ...]:
        """Take and clear the queue. Called by SessionSteadyState's tick."""

    @property
    def failures(self) -> tuple[StrategyFailure, ...]: ...   # everything, for the CLI report
    @property
    def all_failed(self) -> bool: ...                        # set by the runner via expect(n)

    def expect(self, strategy_count: int) -> None:
        """How many specs the session started with, so all_failed can be true."""


# src/core/live_session_record.py   (MODIFIED — a THIRD method on the port)
def record_strategy_failure(
    self, *, strategy_id: str, spec_strategy_id: str, error_type: str,
    handler: str, at: datetime, detail: str | None = None, all_failed: bool = False,
) -> None: ...


# src/core/live_session_runner.py   (MODIFIED)
#   __init__:        self._guard = StrategyGuard(log=self._log, time_source=self._time_source)
#   _phase_trading:  guard.wrap(...) per spec + the per-spec try/except (AC #5)
#   _build_steady_state: pass guard=self._guard
#   contained_failures property -> self._guard.failures      (what the CLI prints)

# src/core/live_session_steady_state.py  (MODIFIED — drains guard.drain_pending() on the tick)
# src/core/live_node_builder.py          (MODIFIED — three engine configs)
```

There is **one** entry point (`StrategyGuard.wrap`) and **no** free `guard_strategy` function; the
word *callback* does not appear in this design. The runner constructs the guard, hands it to the
steady state, and reads `contained_failures` off it — nothing else holds one.

#### The column

```sql
ALTER TABLE trading_sessions ADD COLUMN runtime_flags JSONB NULL;
```

```json
{
  "v": 1,
  "all_failed": false,
  "failed_strategies": [
    {
      "strategy_id": "SMACrossover-000",
      "spec_strategy_id": "sma_crossover",
      "error_type": "DivisionByZero",
      "handler": "handle_bar",
      "at": "2026-08-23T14:03:11.482913+00:00",
      "detail": "[<zero>] division by zero"
    }
  ]
}
```

`NULL` means *nothing to report*, so every existing row and every healthy session costs nothing.
**No traceback in the column** — that belongs in the structlog sink; the column carries `error_type`
plus a **redacted** one-line `detail` (redacted, not `mask_account`-ed — the example above is what a
`DivisionByZero` actually yields, and it contains no account token to redact). `"all_failed"` is
AC #9's last-strategy fact, which needs somewhere to live that is not the per-strategy list. `"v"`
is there so Story 2.8 can add `connection_lost_at` without guessing; this story writes only the keys
it produces.

⚠️ **Three traps, all of which have bitten this repo or a repo like it.**

1. **SQLAlchemy does not track in-place mutation of a plain `JSONB` column.**
   `row.runtime_flags["failed_strategies"].append(...)` silently never persists. Build a new dict
   and **assign** it.
2. **Clear on `-> running`.** Add the clear alongside `_TIMESTAMPS_BY_TARGET`'s stamping in
   `_apply_transition` (`src/services/session_service.py:320-322`). Without it, `live status`
   reports a failure from three process runs ago against a healthy session.
3. **Do not route it through any pydantic model with `extra="forbid"`.** That is exactly what makes
   `spec` unusable (finding #11).

#### Why the port and not the runner

AR38 permits stdlib primitives across the boundary and nothing else. The runner translates at the
catch site — `type(exc).__name__`, `str(strategy.id)`, `redact_accounts(...)` of the first line — so no
`Strategy`, no `StrategyId` and no exception object ever reaches `src/services`. The adapter binds
`session_id` and `started_at` at construction, so the new method inherits the reclaim guard for
free. It never assigns `status`, so AR37's AST scan — which matches only `t.attr == "status"` — does
not see it.

AR42 applies in full: a raising `record_strategy_failure` is logged as
`session.strategy_record_failed` and the session continues, with `SessionReclaimedError` the one
fatal exception, exactly as `record_activity` already treats it.

#### New log events (AR41 amendments)

Dotted, lowercase, past-tense, session-scoped. `strategy.*` is an unused namespace and is analogous
to the enumerated `order.*` / `connection.*` families.

| Event | Level | Carries |
|---|---|---|
| `strategy.failed` | ERROR | `strategy_id`, `spec_strategy_id`, `error_type`, `handler`, redacted `traceback` |
| `strategy.degraded` | WARNING | `strategy_id`, `state` |
| `strategy.start_failed` | ERROR | `spec_strategy_id`, `error_type`, redacted `traceback` |
| `session.all_strategies_failed` | ERROR | `strategies` |
| `session.strategy_record_failed` | ERROR | `strategy_id`, `error_type` (AR42) |

Append all six to the **one** existing running AR41 amendment item at `deferred-work.md:1360`, not a
second one. ⚠️ AR36's vocabulary scan word-matches `pause|halt|kill|close|finalize` in
operator-facing strings for `live_session_runner.py` and `live_session_signals.py` —
`strategy.halted` would fail the build.

#### File-size budget — read this before writing a line

Measured on 2026-08-23 at `107ee14`, `wc -l`:

| File | Now | Cap | Note |
|---|---|---|---|
| `src/core/live_session_runner.py` | **581** | 500 | **Sanctioned exception** (decided 2026-08-23). Gains ~2 lines of wiring |
| `src/core/live_session_steady_state.py` | **507** | 500 | **Over cap and undisclosed until now.** Gains the `note_bar` guard |
| `src/core/live_bar_observer.py` | 498 | 500 | untouched |
| `src/core/live_node_builder.py` | 496 | 500 | gains ~14 lines for the three engine configs → ~510 |
| `src/models/session.py` | 474 | 500 | untouched |
| `src/services/session_service.py` | 479 | 500 | class `SessionService` at **83** of 100 — 17 lines of headroom |
| `src/cli/commands/live.py` | 471 | 500 | gains the contained-failure line |
| `src/core/live_session_node.py` | 336 | 500 | untouched |
| `src/core/live_session_signals.py` | 311 | 500 | untouched |
| `src/core/live_strategy_guard.py` | **NEW** | 500 | the containment lands here |

**The runner is not split.** `deferred-work.md:1523-1527` left the choice to *"Story 2.7 or the
Epic 2 retro"*; decided with Allay on 2026-08-23 to **record it as a sanctioned exception** the way
`src/cli/commands/import_data.py` (**732**) already is, rather than refactor a `run()`/`finally`
whose ordering is load-bearing and hard-won. Two stale numbers to correct in `deferred-work.md`
while you are there: the runner is **581**, not the recorded 576; and `:1526` names the wrong file —
**`catalog.py` is 263 lines**, `import_data.py` is the 732. Also **disclose
`live_session_steady_state.py` at 507**, which nothing records today.

**`SessionService` the class is at 83 of 100** — measured by AST, 17 lines of headroom, so a plain
method fits and the module-level-body split is **not** forced. (The 99 that circulates comes from
`deferred-work.md:1237`, which is `src/cli/commands/live_start.py`'s file length, a different file
entirely.) Follow Story 2.5's module-level-body precedent only if the method plus its docstring
would exceed the headroom; measure and disclose the result either way. **Do not mark a "every class
under 100" subtask done** without measuring — Story 2.6's review patched exactly that false claim,
and `LiveSessionRunner` (458), `SessionSteadyState` (204), `LiveBarObserver` (215) and
`SessionStopSignals` (195) are all over it today.

**`live_node_builder.py` at 496 has four lines of headroom** and needs ~14. Going over is now
sanctioned provided it is **disclosed**; if you would rather stay under, the natural extraction is
`_reconcile_bar_types` + `_actor_configs` (~60 lines) into a new module. Say which you chose and
why. ⚠️ Do **not** put the engine configs in `live_session_node.py` — that module imports
`NODE_TIMEOUT_*` from `live_node_builder`, so the reverse import is circular.

#### `ntrader live start`'s contained-failure output

Honest, short, and it says what the operator can still do:

```
Session stopped: rth-day-1 (SIGINT).
⚠️  1 strategy was contained during this run and stopped trading:
      sma_crossover (SMACrossover-000) — DivisionByZero in handle_bar at 14:03:11Z
    The session kept running; the other strategies were unaffected. See `strategy.failed`
    in the log for the traceback.
```

Exit code stays **0**. The session ran and stopped; AR28's table has no code for "a strategy failed"
and Story 1.7 recorded that a CLI inventing a code outside its own documented table is worse than
one reporting a generic failure.

#### Procedure P8 skeleton — `docs/qa/phase3-live-verification.md`

Append after P7 (the file ends at line 754). Follow P7's shape exactly: title, **Introduced by**,
**Verifies**, **Tool**, Preconditions, *What it does — and does not — do*, Command, Expected output,
Pass criteria, Result log table.

- **Verifies:** AC #1, #2, #4, #5 and #7 against a real gateway.
- **What it does not do:** it does not prove NFR12 for every handler — only `handle_bar` and
  `handle_event` are wrapped — and it cannot prove AC #6's backstop without deliberately breaking
  something the guard does not cover.
- **Pass criteria**, at minimum:
  1. A two-strategy session started with `sma_crossover` (fed a contrived zero-close bar, or with
     the guard temporarily pointed at a probe strategy) and `momentum`: `strategy.failed` appears
     once, the process stays up, `momentum` keeps logging bars, and `last_heartbeat_at` keeps
     advancing.
  2. `SELECT runtime_flags FROM trading_sessions WHERE name = '...'` returns the failure, read from
     a **second terminal** while the session is still running. This is AC #4's whole point.
  3. The IBKR positions page is **identical** before and after the contained failure — nothing was
     closed, cancelled or submitted.
  4. Stop the session, start it again, and confirm `runtime_flags` is back to `NULL`.
  5. A run where a strategy fails in `on_start`: the other strategy still starts and the phase log
     reaches `trading status=ok`.
- **Result log:** `⛔ not run` is an acceptable entry. A dry run is not a pass — that file says so.

```sql
-- the one query P8 leans on
SELECT name, status, last_heartbeat_at, last_bar_at, runtime_flags
  FROM trading_sessions WHERE name = 'contain-test-1';
```

---

### Files

| Path | Change |
|---|---|
| `src/core/live_strategy_guard.py` | **NEW** — `StrategyGuard`, `StrategyFailure`, `redact_accounts`, `StrategyGuardError` |
| `src/core/live_session_runner.py` | **MOD** — guard wiring in `_phase_trading`, per-spec containment, `contained_failures` property |
| `src/core/live_session_steady_state.py` | **MOD** — the tick drains `guard.drain_pending()` and writes through its own executor (AC #10, decision D2) |
| `src/core/live_session_record.py` | **MOD** — third port method `record_strategy_failure`; `SessionReclaimedError`'s "single migration is spent" docstring restated |
| `src/core/live_node_builder.py` | **MOD** — three engine configs with `graceful_shutdown_on_exception=True` |
| `src/services/session_service.py` | **MOD** — `_record_strategy_failure` + delegator; clear `runtime_flags` on `-> running` |
| `src/services/session_record.py` | **MOD** — `SqlSessionRecord.record_strategy_failure` |
| `src/db/models/trading_session.py` | **MOD** — `runtime_flags` column |
| `alembic/versions/<rev>_add_runtime_flags_to_trading_sessions.py` | **NEW** — ⚠️ hook-protected; sanctioned 2026-08-23 |
| `src/cli/commands/live.py` | **MOD** — contained-failure line in `_print_stop_result` |
| `tests/unit/core/test_live_strategy_guard.py` | **NEW** — the guard's policy, unit tier, no Nautilus |
| `tests/unit/core/test_live_node_never_exits.py` | **NEW** — AC #3 AST scan + planted probes |
| `tests/unit/core/test_live_session_record.py` | **MOD** — the port now declares three methods |
| `tests/unit/db/test_migration_runtime_flags.py` | **NEW** — the migration's shape and round trip |
| `tests/unit/services/test_session_service.py` | **MOD** — the failure write and the `-> running` clear |
| `tests/unit/cli/commands/test_live_cli.py` | **MOD** — the contained-failure output |
| `tests/component/core/test_session_runner_strategy_failure.py` | **NEW** — wiring + the real-`MessageBus` dispatch proof, both orders |
| `tests/component/core/test_session_runner_phases.py` | **MOD** — the recording double gains the third method; `TestImportPurity.MODULES` gains the new module |
| `tests/component/core/test_live_node_builder.py` | **MOD** — the engine configs + the Nautilus canary |
| `tests/component/core/test_session_steady_state.py` | **MOD** — the tick drains the guard's queue; its `SpyRecord` gains the third method |
| `tests/component/core/test_session_runner_stop.py` | **MOD** — its `SpyRecord` gains the third method |
| `tests/unit/services/test_session_record_adapter.py` | **MOD** — the adapter's third method |
| `tests/component/doubles/test_live_node.py` | **MOD** *(if needed)* — every addition **must be defaulted**; the file is shared with `test_live_check_driver.py`, `test_epic1_ac_cli.py` and `test_epic1_ac_data.py`, which must keep passing unmodified |
| `tests/integration/core/test_live_strategy_failure_survives.py` | **NEW** — `--forked`, real `LiveDataEngine` in a subprocess, `rc == 0` + the inverted `rc == 1` meta-test |
| `tests/integration/core/test_epic1_ac_node.py` | **MOD** — add `traceback` to `_STDLIB_AND_FIRST_PARTY` **with the reason inline**. Nothing else changes; all 40 criteria must still pass |
| `tests/unit/core/test_live_stop_path_is_inert.py` | **MOD** — `STOP_PATH_MODULES` and `NEW_OR_MODIFIED_FOR_STOP` gain the new module |
| `docs/qa/phase3-live-verification.md` | **MOD** — Procedure P8 |
| `_bmad-output/implementation-artifacts/deferred-work.md` | **MOD** — strike, re-point, add a story-2.7 section |

---

### Testing standards

- **Tier boundaries are decided by what the test imports, and they are not negotiable.**
  - `test_live_strategy_guard.py`, `test_live_node_never_exits.py` → **unit**. Zero Nautilus
    imports; the guard is duck-typed against a stub, the scan uses `ast`.
  - `test_session_runner_strategy_failure.py` → **component**, including the real-`MessageBus`
    dispatch proof. Copy the `_assert_c_logging_state_is_unchanged` and `_isolate_env` autouse
    fixtures from `test_session_runner_phases.py:60-90` **verbatim**.
  - `test_live_strategy_failure_survives.py` → **integration**, `--forked`, because it constructs a
    real `LiveDataEngine`.
- **A `BacktestEngine` test cannot prove this story.** EXECUTED: a `BacktestEngine` with a raising
  `on_bar` gives `engine.run() RAISED RuntimeError`, `EXIT_CODE=0`, process alive — there is no
  `os._exit` on the backtest path at all. Any AC #1/#3 test written against `BacktestEngine` is
  **structurally incapable** of detecting the regression and would pass identically against a design
  that still crashes in live. Sibling starvation and every FSM/degrade fact **do** reproduce in both
  tiers, so those can stay cheap.
- **`TestLiveNode` cannot prove containment.** It constructs no `MessageBus`, no `DataEngine` and no
  kernel; `_TestTrader.add_strategy` appends to a list. Against the double a strategy can only
  "fail" by raising from `add_strategy`/`start_strategy` — the startup shape, not the steady-state
  one. Use it for the wiring assertions and nothing else.
- **Assert the ordered list, never set membership**, and **parametrise both registration orders**
  for AC #2 — dispatch is registration-ordered, so a raiser that happens to be last proves nothing.
- **Every guard must be mutation-proved.** Assume yours is one of the tests that cannot fail until
  you have broken the code and watched it go red. Eight mutations are named in Task 11; run all
  eight and record each observed failure verbatim.
- **Non-vacuity for every AST scan.** Plant a probe for every forbidden name, and a prose probe
  proving the scan does not fire on a docstring that merely names one.
- **Do not use `capture_logs()` for anything involving `session_id`.** It clears the entire
  processor list, which strips `merge_contextvars`; and it records `exc_info: True` rather than a
  rendered traceback, so AC #1's traceback must be passed as an **explicit** field to be assertable.
  If a custom chain is needed, copy the `rendered` fixture at `test_session_runner_phases.py:
  1029-1065` **including** its `monkeypatch.setattr(module, "logger", structlog.get_logger(...))`
  line and its negative control at `:1082-1100` — this repo sets `cache_logger_on_first_use=True`
  and `configure_logging()` runs at import of `src/cli/main.py`, so a cached module logger silently
  observes nothing under `-n auto`.
- **Registration is process-global.** A probe strategy registered through `@register_strategy`
  survives into every later test in the xdist worker. Use an autouse snapshot/restore fixture over
  `_strategies`/`_aliases`/`_discovered`, modelled on
  `tests/component/api/test_run_backtest_routes.py:15-52`. **Never call
  `StrategyRegistry.clear()`.**
- **`os._exit` discards buffered stdout** — measured, 0 bytes on the uncontained run. Every `print`
  in a subprocess probe needs `flush=True`, and the return code is the only reliable signal.
- **No `freezegun`, no `time-machine`, no `pytest-timeout`** — none are installed. Inject the clock
  and the sleeper; `subprocess.run(..., timeout=N)` and the timeout *is* the assertion.
- **Mark everything.** `--strict-markers` is on; `pytest.ini` is the effective config and
  `pyproject.toml`'s marker block is shadowed and dead. Ruff enforces **line-length 100 only** — no
  rule of any kind checks file, function or class size, which is how the drift happened.
- **Do not create a test file whose basename already exists tree-wide.** `tests/unit/services/` and
  `tests/integration/db/` have no `__init__.py`, and `pytest tests` already fails collection on the
  existing `test_session_service.py` clash. Verified free: `test_live_strategy_guard`,
  `test_live_node_never_exits`, `test_session_runner_strategy_failure`,
  `test_live_strategy_failure_survives`, `test_migration_runtime_flags`.
- **CI reality.** `--ignore=tests/integration/db` on both the integration job and `coverage-report`,
  so anything under that directory is **evidence, not a gate** — say so rather than implying
  otherwise. `coverage-report` runs `--cov=src --cov-fail-under=64` **without** `--forked`.
  `make typecheck` is `mypy src/core src/services` and runs on every Claude-issued commit; every new
  module here is inside it.
- **Naming.** Long behavioural sentences, class per concern, docstring naming the AC.

---

### Judgment calls made while writing this story (flag at the Epic 2 retro)

1. **AC #1's "runner boundary" is re-read, not met literally.** The measured path ends in
   `os._exit(1)` before any runner code executes, and the queue task never completes, so
   `_serve()`'s `task.result()` is unreachable. Reading "the runner boundary" as "the boundary the
   runner installs around each strategy" is the only reading under which the criterion can be true
   at all. The alternative — recording AC #1 as knowingly unmet, the Story 2.6 AC #2 precedent —
   was rejected because the *intent* (one strategy's failure does not end the session) **is**
   deliverable, and recording it unmet would leave the actual defect live.

2. **Both moves Stories 2.5 and 2.6 forbade are made here, deliberately.** Those stories wrote:
   *"Do not add a `try/except` around `Strategy.on_bar`, and do not flip
   `graceful_shutdown_on_exception` on either engine — that changes crash semantics repo-wide and
   belongs to the story that reasons about it."* This is that story. The guard is not a
   `try/except` around `on_bar` in a strategy file — it is an instance-level wrapper on `handle_*`
   applied by the runner, which touches no strategy source (AR40) — and the flag is set as a
   documented backstop with its limits stated in the code.

3. **`degrade()`, not `stop()` or `remove_strategy()`.** `stop()` runs `on_stop()`, which for
   `sma_crossover` still calls `close_all_positions()` — flattening a real position because of an
   unrelated `on_bar` bug, an AR43 anti-pattern and an NFR14 violation. `remove_strategy()` leaks
   the subscription (measured) and destroys `Trader.strategy_states()`. The cost is the ⚠️ under
   AC #7: a degraded strategy's `on_stop()` never runs at teardown. Today that is safer; after
   Story 3.1 removes the flatten it becomes a leak, and **that is the call site to revisit**.

4. **`fault()` on the start path, `degrade()` on the bar path — two verbs, not one.** `degrade()` is
   illegal from `STARTING` and is *silently swallowed*, so a single verb would produce a strategy
   that looks contained and is not. The cost is a reader having to hold two verbs; the alternative
   is a guard that does nothing in exactly the case the operator most needs it.

5. **The traceback is logged, overriding `live_session_phases.py`'s no-`str(exc)` rule, and masking
   is what makes that safe.** That rule exists because *broker* error text embeds account
   identifiers. A strategy's exception is this codebase's own stack. AC #1 requires the traceback and
   an `error_type` alone would not let an operator fix the strategy. A new `redact_accounts` —
   **not** `mask_account`, which masks the whole value and would reduce a traceback to `'***ged'` —
   is applied to both the log field and the column's `detail`, and AC #8 proves it with a planted
   account string plus a three-part assertion. The repo's existing answer to the same problem is
   `live_check.py`'s `_SAFE_MESSAGE_EXCEPTION_NAMES` allowlist, which cannot serve here because a
   strategy can raise anything. If the retro prefers `error_type`-only, this is the call site.

6. **The guard imports no `nautilus_trader`, and is duck-typed.** It needs `is_running`, `degrade`,
   `fault`, `id` and two `handle_*` attributes — all reachable without an import. The benefit is
   that the whole containment policy is unit-testable with no C logging and no kernel. The cost is
   that `mypy` sees `Any` at the strategy parameter, so a typo in a method name is a runtime
   failure rather than a type error — mitigated by `StrategyGuardError` on a failed `setattr` and by
   the component-tier proof against real `Strategy` objects.

7. **A session whose strategies have all failed keeps running.** AC #1's letter requires it, and
   stopping would reproduce the "stopped means two different things" defect `deferred-work.md:
   851-854` already logs. The cost is real: such a session holds an IBKR client id and a
   market-data line while being incapable of trading. `session.all_strategies_failed` plus
   `runtime_flags` make it visible; if the retro decides it should stop instead, this is the call
   site.

8. **Degrade on the *first* failure, not after N.** A strategy raising once on a malformed bar and
   recovering is a different animal from one raising on every bar, and counting would tolerate the
   first. Latching on the first is simpler, matches the AC's phrase *"a strategy that has failed"*,
   is what was measured working end to end, and is recoverable — `degrade()` keeps the strategy
   warm and `resume()` is legal. Nothing exposes `resume()` yet; if the retro wants it, it is a
   Story 2.8 CLI question, not a guard change.

9. **The second Phase-3 migration.** Decided with Allay on 2026-08-23; the full reasoning is in the
   blockquote under AC #4. Flagged here because it departs from a `deferred-work.md` action that
   re-pointed the question at Story 2.8 — the departure is deliberate, and the reason is that a
   writer without a column ships a false green.

10. **`SessionReclaimedError` is *not* fatal on the bar path, breaking the symmetry with
    `record_activity`.** Every other caller treats a reclaim as fatal and re-raises. The guard
    cannot: measured, a raise out of a wrapped `handle_bar` re-enters `publish_c` and gives `rc=1`
    with zero output — the exact silent death this story exists to close. The reclaim is flagged and
    raised on the steady-state tick instead, one interval later. The cost is up to ~30 seconds of a
    dispossessed process still running; the alternative is a process that dies invisibly. If the
    retro wants the symmetry restored, it needs a boundary inside `handle_bar` that does not exist.

11. **The record write is deferred to the steady-state tick rather than performed at the catch
    site.** It makes the guard's own module free of the record port and adds one indirection
    (`drain_pending()`), which a reader may find surprising in a module named "guard". The reason is
    measured and this repo has already paid for it once: decision D2 (2026-08-23) moved the
    heartbeat write onto `SessionSteadyState`'s private executor after a **59.81s** teardown block,
    and `handle_*` runs inline on the loop thread inside `publish_c`, where `note_bar`'s own
    docstring says handlers *"must be trivial and must not raise"*. A blocking Postgres round trip
    there would partially recreate the AC #2 starvation the story exists to fix.

---

### Deferred items this story reads

**Closes:**

- **`live_session_runner.py` is over the 500-line cap** (`deferred-work.md:1517-1533`) —
  *"Action for Story 2.7 or the Epic 2 retro: either agree a new split line … or record the runner
  as a sanctioned exception the way `src/cli/commands/catalog.py` (732) already is."* (⚠️ that line
  names the wrong file — `catalog.py` is 263; the 732 is `import_data.py`.) **Answered:
  recorded as a sanctioned exception** (Allay, 2026-08-23). Correct the stale **576** to **581**,
  and add `live_session_steady_state.py` at **507**, which nothing records today.

- **`SessionStatus` has no terminal failure state** (`deferred-work.md:851-861`) — *"Action,
  re-pointed at Story 2.8: Story 2.7 makes a strategy failure visible … decide there whether that is
  sufficient or a separate nullable column is still warranted."* **Answered here instead of at 2.8:
  the nullable column is warranted, and it lands with the story that writes it.** The no-fifth-status
  half of the decision stands unchanged.

**Partially closes — say so explicitly rather than claiming more:**

- **`live_node_builder.py` headroom** (`deferred-work.md:1240`) — *"its own headroom is still Story
  2.7's to budget for."* Budgeted, and spent: the three engine configs take it over. Disclosed, not
  resolved.

- **`SessionService` the class is at the 100-line guideline** (`deferred-work.md:1220-1225`) —
  pointed at Story 2.8; 2.7 reaches the fork first and takes the same answer (module-level body,
  thin delegator, disclose the number). The underlying question — whether the limit is the right
  measure for a class whose bulk is docstring — stays open for the retro.

**Reads and does not close:**

- **`live_session_runner` imports `NodeFactory`/`AccountVerifier` from `live_check_driver`**
  (`deferred-work.md:1309-1314`) — *"a Story 2.6 or 2.7 change to the driver's aliases reaches the
  runner."* This story changes no alias. Left for the Epic 2 retro.

- **A stop requested during a synchronous phase is noticed but not acted on until the phase ends**
  (`deferred-work.md:1540-1556`). This story adds per-strategy wrapping inside `_phase_trading`,
  which is synchronous — it is a handful of `setattr` calls and adds no measurable time, but do not
  add anything slow there.

- **The AR41 event enumeration needs amendments** (`deferred-work.md:1360`). Six more from this
  story. **Append to that item; do not start a second one.**

- **Class-size limit violations across the live path** (`deferred-work.md:1494-1503`). Unchanged,
  and re-confirmed by measurement: `LiveSessionRunner` **458**, `SessionSteadyState` **204**,
  `LiveBarObserver` **215**, `SessionStopSignals` **195**.

**New items to record in a `## Deferred from: story-2.7` section:**

- **The owner/epoch fencing column is re-opened.** `SessionReclaimedError`'s docstring
  (`src/core/live_session_record.py:52-57`) rejects it *"because this phase's single migration is
  spent"* — a reason Task 5 falsifies. The NFR6 hazard it names (up to one heartbeat interval of two
  live processes on one broker account) is unchanged and now has no cost argument against fixing it.
- **`deferred-work.md:1526` names the wrong file.** The 732-line sanctioned exception is
  `src/cli/commands/import_data.py`; `catalog.py` is **263**.
- **`SessionSteadyState.note_bar` and `LiveBarObserver` are covered only by AC #6's engine flag**,
  not by a guard of their own — a deliberate choice (Task 7), not an oversight.

- The `order_id_tag` collision (finding #10) is a latent startup failure for an operator's choice of
  strategy order, contained by Task 4 but not prevented at create time.
- A degraded strategy's `on_stop()` never runs at teardown — safe today, a leak after Story 3.1.
- Timer/`TimeEvent` failures are contained but **invisible**: EXECUTED, a raise inside a `LiveClock`
  timer callback is silently swallowed at the pyo3 boundary — process survives, exit 0, nothing
  printed. No repo strategy uses timers today. Not wrapped by this story.
- `Actor.handle_bar` runs `_handle_indicators_for_bar` outside the `try`; a `handle_*` wrapper
  contains it, an `on_*` wrapper would not. Relevant when Story 4.4 registers indicators.
- `StrategyRegistry.discover()` catches only `ImportError`; any other exception at strategy-module
  import time aborts discovery for every remaining strategy.
- An exception in one strategy's order-event handler **silently drops** that fill's pending
  `PositionOpened`/`Changed`/`Closed` — `execution/engine.pyx:1170-1187` clears
  `_pending_position_events` *before* publishing the order event. Wrapping `handle_event` closes
  this, which is why it is wrapped now rather than in Epic 3; record it so Epic 3's trade recorder
  knows why.

---

### Project Structure Notes

`src/core/live_strategy_guard.py` follows the `live_*` naming every module on this path uses, so
it is swept automatically by `_live_module_sources()`'s glob and by
`test_live_dependency_invariance.py`'s `LIVE_MODULE_GLOBS`. It is **not** swept by the two
hand-maintained lists in `test_live_stop_path_is_inert.py` or by `TestImportPurity.MODULES` — add it
to all three in the same commit (Task 8).

The module sits alongside `live_session_node.py` and `live_session_signals.py` as a policy module
the runner *wires* rather than *contains* — the split rationale Stories 2.5 and 2.6 both used. It
is inside `make typecheck`'s `mypy src/core src/services`.

`runtime_flags` is a `trading_sessions` column, so it is Story 2.2's table and Story 2.8's reader.
Nothing in `src/api/**` or `templates/**` references the table (verified: zero grep hits), so AR44's
zero-change criterion is untouched.

---

### References

Line numbers are as of drafting on 2026-08-23 at commit `107ee14`; re-confirm anything that looks
stale rather than trusting the number.

**Requirements**

- [Source: `_bmad-output/planning-artifacts/epics.md#Story-2.7`] — 970-995, the four epic criteria.
- [Source: `epics.md#Functional-Requirements`] — FR49 (why a session stopped trading), FR50
  (isolate one strategy's failure), FR51.
- [Source: `epics.md#NonFunctional-Requirements`] — NFR12 (failure isolation), NFR14 (no artificial
  trades), NFR23 (status without log archaeology), NFR26 (account ids never logged in full).
- [Source: `epics.md#Additional-Requirements`] — AR32 (health, derived not stored), AR37 (one status
  assigner), AR38 (runner owns the node, service owns the record), AR39 (the phase sequence),
  AR41 (log event naming), AR42 (DB write failures never kill the node), AR43 (anti-patterns),
  AR44 (explicitly untouched).
- [Source: `epics.md#Story-2.8`] — 996-1035, what reads `runtime_flags` next.
- [Source: `epics.md#Story-3.1`] — 1047-1075, who removes `close_all_positions()` from `on_stop`.
- [Source: `epics.md#Story-4.4`] — 1380-1410, who registers indicators in `on_start`.

**Code this story changes or leans on**

- [Source: `src/core/live_session_runner.py`] — 483-493 `_phase_subscribe` (the `note_bar`
  subscription), 495-512 `_phase_trading` (the bare loop), 334-351 the teardown's `finally`.
- [Source: `src/core/live_session_node.py`] — 234-264 `materialise_strategy`, 208-231
  `validate_spec_is_materialisable`.
- [Source: `src/core/live_session_phases.py`] — 88-95 the deliberate no-`str(exc)` policy, 100-112
  `phase()`'s re-raise.
- [Source: `src/core/live_session_record.py`] — 37-57 `SessionReclaimedError`, 60-92 the two-method
  port.
- [Source: `src/services/session_service.py`] — 119-139 the transition tables, 230-265
  `_refuse_if_reclaimed`, 267-323 `_apply_transition`, 326-393 `_stamp_activity`.
- [Source: `src/services/session_record.py`] — 83-153 the adapter's two methods and its
  `InvalidSessionTransition` → `SessionReclaimedError` translation.
- [Source: `src/core/live_node_builder.py`] — 82-96 the `NODE_TIMEOUT_*` block and its
  "passed unconditionally" rationale, 322-342 the `TradingNodeConfig(...)` call.
- [Source: `src/core/live_gate.py`] — 117-140 `mask_account` (a **whole-value** masker; see AC #8).
- [Source: `src/core/live_check.py`] — 145-160 `_SAFE_MESSAGE_EXCEPTION_NAMES`, the repo's existing
  answer to NFR26 in operator-facing text, with the `Error 321: account DU4076626` case inline.
- [Source: `src/core/strategies/sma_crossover.py`] — 88 `on_bar` (114 is the `_check_for_signals` call inside it), 150 the division, 83-86 the
  `on_stop` flatten (Story 3.1's).
- [Source: `src/cli/commands/live.py`] — 222-227 the singular `--strategy`, 290 the one-element
  `SessionSpec`, 419-471 `_print_stop_result`.

**Third-party, read out of the installed `nautilus-trader 1.220.0` wheel**

- [Source: `common/actor.pyx`] — 3735-3748 `handle_bar` (indicators outside the `try`, log, re-raise),
  1804-1806 `subscribe_bars` binds `self.handle_bar`.
- [Source: `common/component.pyx`] — 2739-2784 `MessageBus.publish_c` (no per-handler guard),
  2795 subscription ordering, 1571-1598 the FSM table, 1757-1767 `is_running`, 1877-1931
  `Component.start`/`stop` re-raise, 2099-2119 `shutdown_system`, 2130-2135 invalid triggers are
  swallowed, 2146-2160 `ComponentStateChanged`.
- [Source: `trading/strategy.pyx`] — 145-151 the `component_id`/`StrategyId`/logger relationship,
  314-315 `register()` subscribes `handle_event`, 1714-1716 the order/position dispatch re-raise.
- [Source: `trading/trader.py`] — 250-289 `_start`/`_stop` bare loops, 395-397 the `has_controller`
  guard, 405-423 `order_id_tag` assignment and the conflict `RuntimeError`, 625-632
  `stop_strategy`'s `is_running` guard, 685-693 `remove_strategy` does not unsubscribe.
- [Source: `live/data_engine.py`] — 347-365 `_handle_queue_exception` and `os._exit(1)`, 476-487 the
  queue loop. Same shape at `live/execution_engine.py:380-398` and `live/risk_engine.py:212-230`.
- [Source: `live/config.py`] — 48-55, 66-73 the three `graceful_shutdown_on_exception` defaults and
  the docstring that is wrong about strategy exceptions.
- [Source: `live/node.py`] — 351-371 `run_async`'s `gather` over the queue tasks.
- [Source: `system/kernel.py`] — 576 the shutdown subscription, 603-625 `_on_shutdown_system` →
  `stop_async()`.
- [Source: `execution/engine.pyx`] — 1170-1187 pending position events cleared before the order
  event is published.

**Prior stories and deferred work**

- [Source: `2-5-...-startup-sequence.md`] — 651-654 (Story 2.7 owns FR50; do not flip the flag),
  852-862 (the `on_start` path does reach the run task), the `cache_logger_on_first_use` trap.
- [Source: `2-6-...-touching-positions.md`] — 610-612 (the same prohibition), 1039-1069 the
  file-size budget shape, 1149-1187 the testing standards, 1501-1513 the two subprocess-harness
  bugs.
- [Source: `deferred-work.md`] — 851-861 (no fifth status; the column question left open),
  1220-1225 (`SessionService` class size), 1235-1245 (inherited sizes), 1309-1314 (the driver
  aliases), 1360-1366 (the running AR41 amendment item), 1494-1503 (class-size violations),
  1517-1533 (the runner over cap), 1540-1556 (the synchronous-phase stop delay).
- [Source: `docs/qa/phase3-live-verification.md`] — 627-754, Procedure P7's shape.

---

## Dev Agent Record

### Agent Model Used

Claude Opus 5 (1M context) — `claude-opus-5[1m]`.

### Debug Log References

**Task 1 probe results**

| Probe | Expected | Observed |
|---|---|---|
| uncontained raising strategy, fresh interpreter | `rc=1`, no output | ✅ **`rc=1`, output file 0 bytes** |
| same probe, per-strategy `handle_bar` wrapper | `rc=0`, sibling unaffected | ✅ **`rc=0`**, `seen={'A': 1, 'B': 3}`, `contained=['A:RuntimeError']`, `a_state=11` (DEGRADED), `b_state=3` (RUNNING), `queue_task_done=False` |
| `LiveDataEngineConfig().graceful_shutdown_on_exception` | `False` | ✅ `False` (exec and risk configs too) |
| `grep -n "data_engine=\|graceful_shutdown" src/core/live_node_builder.py` | zero hits | ✅ zero hits |
| `grep -rn "is_live" src/` (AR40) | zero hits | ✅ zero hits |
| `grep -rn register_indicator src/core/strategies/` | zero hits | ✅ zero hits |
| baseline: unit / component / integration / e2e | 2126 / 1262 / 261 / 1 | ✅ **2126 / 1262 / 261 / 1** |
| file-size budget table (`wc -l`) | as tabled | ✅ every row matched exactly (runner 581, steady state 507, builder 496, `import_data.py` 732, `catalog.py` 263) |

**Mutation results**

**All eleven were broken, observed red, and reverted.** The tree was then re-scanned for leftover
markers (`grep -rn "MUTATION [0-9]" src/ tests/` → zero hits).

| # | Mutation | Test that went red | Observed failure |
|---|---|---|---|
| 1 | Remove the per-strategy guard | `test_session_runner_strategy_failure.py::TestTheGuardIsInstalledBeforeAddStrategy` (2 failed) | `assert [False, False] == [True, True]` |
| 2 | Guard only the first strategy in the loop | same class (2 failed) | `assert [True, False] == [True, True]` — the one-strategy version would **not** have caught this, which is why AC #2 requires two |
| 3 | Re-raise after logging | `test_live_strategy_failure_survives.py` (3 failed, `--forked`) | `the process died (rc=1)` — the guarded probe stopped surviving |
| 4 | Capture `strategy_id` at wrap time | `test_live_strategy_guard.py` (3 failed) | `assert 'sma_crossover' == 'SMACrossover-000'` |
| 5 | `exc_info=True` instead of the redacted `traceback=` field | `test_live_strategy_guard.py` (3 failed) | `KeyError: 'traceback'` — `capture_logs` records only `exc_info: True`, never a string |
| 6 | `redact_accounts` implemented as `mask_account(text)` | `test_live_strategy_guard.py` (**11** failed) | `assert '***626' in '***ged'`; `assert 'bad size' in '***ize'`; `assert 'Traceback' in '***oom'`. ⚠️ The naive *"`DU4076626` absent"* assertion stayed **green** throughout — exactly the vacuous test the story predicted |
| 7 | Remove the AC #9 latch | `test_live_strategy_guard.py` (4 failed) | `assert 5 == 1` (one `strategy.failed` per bar) |
| 8 | Latch short-circuits before the base handler | `test_live_strategy_guard.py` (1 failed) | `assert 1 == 8` — the indicator-warmth assertion, and the **only** test that catches it |
| 9 | Let `SessionReclaimedError` escape the guard | `test_live_strategy_failure_survives.py` (3 failed, `--forked`) | `the process died (rc=1)`. **The most important one**: it reproduces the measured `rc=1`/0-bytes silent death |
| 10 | Point the AC #3 scan at an empty module tuple | `test_live_node_never_exits.py` (1 failed) | `assert 0 >= 10` |
| 11 | Remove the `-> running` clear of `runtime_flags` | `test_session_service.py` (3 failed) | `assert {'failed_strategies': [{'a': 1}], 'v': 1} is None` |

**File sizes, before → after**

Measured by `wc -l` and by AST. **Nothing here is claimed to be under the cap that is not** — Story
2.6's review patched exactly that false claim, so every over-cap number is stated plainly and every
one is recorded in `deferred-work.md`.

| File | Before | After | Cap | Status |
|---|---|---|---|---|
| `src/core/live_session_runner.py` | 581 | **667** | 500 | **Over — sanctioned** (decided 2026-08-23) |
| `src/core/live_session_steady_state.py` | 507 | **574** | 500 | **Over — disclosed** (was already over, undisclosed until now) |
| `src/core/live_node_builder.py` | 496 | **536** | 500 | **Over — disclosed.** The story offered an extraction (`_reconcile_bar_types` + `_actor_configs`); declined, reason in `deferred-work.md` |
| `src/core/live_strategy_guard.py` | — | **574** | 500 | **Over — disclosed.** ~180 lines of code; the rest is the measured Nautilus reasoning the story required in-module. One clean split candidate named in `deferred-work.md`, not taken because AC #8 pins `redact_accounts` to this file |
| `src/cli/commands/live.py` | 471 | **514** | 500 | **Over — disclosed** (the contained-failure block) |
| `src/services/session_service.py` | 479 | **600** | 500 | **Over — disclosed** |
| `src/services/session_record.py` | 153 | 212 | 500 | under |
| `src/core/live_session_record.py` | 92 | 142 | 500 | under |
| `src/db/models/trading_session.py` | 105 | 124 | 500 | under |

**Classes, measured by AST — do NOT read this as "every class under 100".** `SessionService` is
**94** of 100 (up from 83, so the module-level-body split *was* the right call and the headroom is
nearly gone). Over the 100-line guideline, all recorded in `deferred-work.md`'s standing item:
`LiveSessionRunner` **538**, `StrategyGuard` **312** (new, this story), `SessionSteadyState` **271**,
`SqlSessionRecord` **155**, `TradingSession` **102**, `SessionRecordPort` **75** (under).

**Re-measured after the 2026-08-23 code-review fixes** (which also caught two inconsistencies in
the numbers above: `deferred-work.md` said steady state was "567 after" where this table's 574 was
the real measurement, and the guard module's own docstring said 320 where this record said 312 —
neither pair was re-measured from the same source). Post-fix, files: runner **734**, steady state
**581**, guard **634**, `live.py` **533**, `session_service.py` **620**. Classes by AST:
`LiveSessionRunner` **605**, `StrategyGuard` **346**, `SessionSteadyState` **278**,
`SessionService` **98** (of 100 — the FOR-UPDATE rationale was moved to the module-level body to
stay under).

### Completion Notes List

**All 11 tasks and every subtask complete. All 10 ACs satisfied.** Test counts, per tier:

| Tier | Baseline (collected) | Final | Δ |
|---|---|---|---|
| unit | 2126 | **2254 passed** | +128 |
| component | 1262 | **1283 passed / 16 skipped** | +37 |
| integration (`tests/integration/core`, `--forked`) | 79 passed / 2 skipped | **84 passed / 2 skipped** | +5 |
| Epic 1 acceptance sweep (all six `test_epic1_ac_*.py`) | 40/40 | **40/40** | unchanged |

`make format` / `make lint` clean; `make typecheck` clean across **102** source files. Zero
regressions. The 11 named mutations were all broken, observed red and reverted — table above.

**1. The story's central claim is proved by return code, not by a log assertion.**
`tests/integration/core/test_live_strategy_failure_survives.py` builds a real `LiveDataEngine` in a
fresh interpreter with a real `sma_crossover` fed a `0.00` close: guarded → `rc=0`, the sibling
receives all five bars, the raiser is `DEGRADED`, the data-queue task is still alive. The **inverted**
meta-test ships alongside it and is the permanent mutation proof: the same probe with
`guard.wrap(...)` replaced exits **`1`**. Task 1 re-confirmed the defect first — uncontained gave
`rc=1` with an output file of **0 bytes**, exactly as the story measured.

⚠️ **The probe deliberately uses `LiveDataEngineConfig()`'s defaults** (`graceful_shutdown_on_exception=False`),
which is load-bearing rather than an oversight: with AC #6's `True`, the *uncontained* run would
publish `ShutdownSystem` into a bus with no kernel subscribed and exit `0` anyway, so the inverted
test would pass vacuously. The proof isolates the guard from its own backstop.

**2. AC #2's negative control is order-dependent, and measuring it that way is what makes the
positive test non-vacuous.** Measured on a real `MessageBus`, unguarded: raiser **first** leaves the
sibling with **4 of 5** bars; raiser **last** leaves it with **5 of 5**. A single-order test would
have passed against a design with no containment at all. Both orders are parametrised, and the
control is pinned on `bus.pub_count` (which does **not** advance when a subscriber raises) rather
than on the sibling's count, because only the counter is order-independent. Story's warning
confirmed: `pub_count` reads **6** before the first bar and a guarded bar adds **3**, so no delta is
pinned.

**3. Task 3's real-dispatch proof is component tier and stays there.** Verified rather than assumed:
a real `MessageBus` + `Cache` + `Portfolio` + two registered, started `Strategy` objects leaves
`is_logging_initialized()` **False** at every step. The autouse `_assert_c_logging_state_is_unchanged`
fixture (copied verbatim) is what keeps that honest.

**4. Two of my own test assertions were wrong, not the code, and one is worth reading.**
`str(ComponentState.DEGRADED)` renders `'11'`, not `'DEGRADED'` — use `.name`. And I initially
asserted that a degraded strategy keeps receiving bars *at `on_bar`*; it does not, because
`handle_bar`'s `if state == RUNNING` gate is false once degraded. That is exactly what the story says
("calling through is cheap after `degrade()`"), and the corrected test is stronger for it: it now
asserts **both** halves — the boundary still calls through to `handle_bar` (`delivered` advances) and
`on_bar` is not re-entered (`seen` does not). Two counters, at two levels, because the two facts are
different and the difference is the design.

**5. `NoStrategyStartedError` is new, and it is a fifth name on an allowlist that Story 2.5 said to
revisit at four.** AC #5 requires the `trading` phase to fail when *no* strategy starts, and nothing
existing fits: `BrokerUnreachableError` would tell an operator to go restart a healthy gateway. The
new exception lives in `live_strategy_guard.py`, reaches AR28's **generic exit 1** through
`classify_failure` walking the MRO (inventing no code — Story 1.7's discipline), and is added to
`_SAFE_MESSAGE_EXCEPTION_NAMES` because its message is this codebase's own and names the failed
specs. Story 2.5's *Judgment call #8* flagged the **fourth** typed failure as the moment to revisit
the marker protocol; this is the fifth. Recorded in `deferred-work.md`, deliberately not solved here.

**6. `session.started` now names only the strategies that actually started.** A small, deliberate
behaviour change that falls out of AC #5: reporting a spec as started when it was contained is the
same false green AC #4 exists to prevent. Single-strategy sessions are unaffected, so no existing
test changed.

**7. AR9's dual-repository rule is NOT triggered**, stated here rather than left for a reviewer to
infer. `record_strategy_failure` is a service-level mutation on an already-loaded ORM object — the
same shape as `_stamp_activity` — so no repository method was added or changed on either twin.

**8. `tests/component/doubles/test_live_node.py` was NOT modified.** The story budgeted for it "if
needed"; it was not. Task 4's failure injection is done by patching the double's bound methods on the
*instance* inside the test file, which leaves the shared double — also used by
`test_live_check_driver.py`, `test_epic1_ac_cli.py` and `test_epic1_ac_data.py` — completely
untouched. Strictly safer than touching it carefully.

**9. Epic 1's dependency guard fired, and it caught one name the story did not predict.** The story
anticipated `traceback`; the guard also rejected **`re`** (needed by `redact_accounts`, because
`mask_account` is a whole-value masker). Both stdlib, both added by hand with the reason inline, per
the `socket`/`signal` precedent — and explicitly **not** by switching to `sys.stdlib_module_names`,
which Story 2.4's review rejected. That is the guard working exactly as intended.

**10. One test-fixture bug of my own, caught immediately and worth recording** because it is the
inverse of the trap the story warns about. My registry snapshot/restore fixture took its snapshot
*before* discovery had run (discovery is lazy via `_ensure_discovered`), so restoring at teardown
wiped the real registrations for every later test in the worker. Fixed by forcing
`StrategyRegistry.discover()` before snapshotting. The story's warning is about registrations
*leaking in*; this was the same mechanism leaking them *out*.

**11. Six files are over the 500-line cap and every one is stated plainly.** See the sizes table
above. `live_strategy_guard.py` ships new at **574**, which I first tried to fix by trimming: I
removed ~17 lines of genuine duplication between the module docstring and the method docstrings, and
stopped there rather than deleting measured findings the story required be written into the module.
The one clean split (the redaction trio → `live_redaction.py`, landing it at ~485) is **not taken**,
because AC #8 names `redact_accounts` as living in `live_strategy_guard.py` — moving it needs an AC
amendment, not a dev-time decision. Recorded in `deferred-work.md` with that reasoning. Likewise
`live_node_builder.py` at **536**: the story offered an extraction and I declined it, because it would
move node-assembly helpers out of the module whose job is node assembly to satisfy a cap nothing
enforces, and each new `live_*` module must be hand-added to three guard lists — the exact rot vector
this repo has already been bitten by once. **`StrategyGuard` the class is 312 lines against the
100-line guideline** and says so in its own module docstring; I have not repeated Story 2.6's "every
class under 100" claim, and `SessionService` is now **94** of 100 (up from 83 — the headroom the
story measured is nearly spent).

**12. What is NOT verified, recorded rather than glossed.** **Procedure P8 is written and NOT RUN.**
It needs IB Gateway/TWS on the paper port, Redis, Postgres **and RTH** — and it is the only thing that
can verify the three broker-facing criteria: that `runtime_flags` is readable from a second terminal
while the session runs (AC #4's whole point), that the IBKR positions/orders page is **identical**
across a contained failure (NFR14/AR43), and that a degraded strategy stays registered and
subscribed. The process-level claim *is* covered automatically and by return code. The result log
records `⛔ not run`, which that file's own policy says is acceptable and which a dry run is not.
Also not exercised: the runtime `session.all_strategies_failed` path against a live gateway (the
start-path half is covered by automated tests), and `handle_event` containment has no order events to
carry yet — Epic 3's.

**13. Housekeeping.** The migration ran for real against the live database:
`d08dfbd393f0 -> b7c419e2a3d8`, then a full `downgrade -1 -> upgrade head` round trip, with both
`trading_sessions` rows preserved and `alembic heads` reporting a **single** head throughout. The
column reads `runtime_flags | jsonb | YES | (no default)`; the table went 13 → 14 columns. No test
data was created and no row was written by this story's work. `alembic/versions/` is hook-protected;
the file was staged and placed only after the sanction recorded under AC #4 was confirmed.

### File List

Paths relative to the repo root.

**New — production**
- `src/core/live_strategy_guard.py`
- `alembic/versions/b7c419e2a3d8_add_runtime_flags_to_trading_sessions.py`

**Modified — production**
- `src/core/live_session_runner.py`
- `src/core/live_session_steady_state.py`
- `src/core/live_session_record.py`
- `src/core/live_node_builder.py`
- `src/core/live_check.py`
- `src/services/session_service.py`
- `src/services/session_record.py`
- `src/db/models/trading_session.py`
- `src/cli/commands/live.py`

**New — tests**
- `tests/unit/core/test_live_strategy_guard.py`
- `tests/unit/core/test_live_node_never_exits.py`
- `tests/unit/db/test_migration_runtime_flags.py`
- `tests/component/core/test_session_runner_strategy_failure.py`
- `tests/integration/core/test_live_strategy_failure_survives.py`

**Modified — tests**
- `tests/unit/core/test_live_session_record.py`
- `tests/unit/core/test_live_stop_path_is_inert.py`
- `tests/unit/services/test_session_service.py`
- `tests/unit/services/test_session_record_adapter.py`
- `tests/unit/cli/commands/test_live_cli.py`
- `tests/component/core/test_session_runner_phases.py`
- `tests/component/core/test_session_runner_stop.py`
- `tests/component/core/test_session_steady_state.py`
- `tests/component/core/test_live_node_builder.py`
- `tests/integration/core/test_epic1_ac_node.py`

**Modified — docs and artifacts**
- `docs/qa/phase3-live-verification.md`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/2-7-keep-one-failing-strategy-from-taking-down-the-session.md`

**Deliberately NOT modified**
- `tests/component/doubles/test_live_node.py` — budgeted "if needed"; not needed (see note 8).
- Any file under `src/core/strategies/` — AR40. `grep -rn "is_live" src/` is still **zero**.

## Change Log

| Date | Version | Description | Author |
|---|---|---|---|
| 2026-08-23 | 0.1 | Story created. Context built by **executing** against the installed `nautilus-trader 1.220.0` rather than reading its docs, which overturned the epic's AC wording twice: an uncontained strategy exception ends in `os._exit(1)` (default `graceful_shutdown_on_exception=False`) before any runner code runs, so AC #1's "caught at the runner boundary" is re-read as the boundary the runner *installs*; and `MessageBus.publish_c` has no per-handler guard, so AC #2 is false under raw Nautilus regardless of where the runner catches. Five ACs added beyond the epic's four. Two decisions taken with Allay: a second Phase-3 migration adds a nullable `runtime_flags` column (because containment without persistence turns today's crude-but-visible `stale` into a false `trading`), and `live_session_runner.py` is recorded as a sanctioned over-cap exception rather than split. | Bob (SM) |
| 2026-08-23 | 1.0 | **Implemented; ready-for-dev → in-progress → review.** All 11 tasks, all 10 ACs. Delivers `src/core/live_strategy_guard.py` (new): an instance-level wrapper on `handle_bar`/`handle_event`, applied by the runner between `materialise_strategy` and `add_strategy`, that latches per strategy, logs a **redacted** traceback, queues the failure and calls `degrade()` — with the whole containment body inside its own `except`, so nothing propagates out of a wrapped handler including `SessionReclaimedError`. Task 1 re-confirmed the defect first: uncontained → `rc=1` with an output file of **0 bytes**; guarded → `rc=0`, sibling gets every bar, raiser `DEGRADED`, queue task alive. Also: per-spec start containment with `fault()` (a session where *no* strategy starts now fails the `trading` phase with a new `NoStrategyStartedError`); the phase's **second** migration `b7c419e2a3d8` adding nullable `runtime_flags` JSONB, round-tripped against the live database with both rows preserved and a single head throughout; a third `SessionRecordPort` method written from the steady-state tick on that object's own executor, never inline on the loop thread; `graceful_shutdown_on_exception=True` on all three live engines with a canary pinning Nautilus's `False` default; and the contained-failure block in `_print_stop_result` (exit code stays 0). **All 11 named mutations broken, observed red, reverted** — including #6, where the naive *"`DU4076626` absent"* assertion stayed green against `mask_account(text)` → `'***ged'` exactly as predicted, and #8, killed by exactly one test. **AC #2's negative control is order-dependent as measured**: unguarded, raiser-first starves the sibling (4 of 5 bars), raiser-last does not (5 of 5) — so both orders are parametrised and the control is pinned on `bus.pub_count`. Epic 1's dependency guard caught **`re`** as well as the predicted `traceback`; both added by hand with the reason. Final: unit 2126 → **2254**, component 1262 → **1283 passed / 16 skipped**, integration/core `--forked` 79 → **84 passed / 2 skipped**, **40/40** Epic 1 criteria, format/lint clean, mypy clean on 102 files. **Six files are over the 500-line cap and every one is disclosed** (guard 574, runner 667, steady state 574, builder 536, `live.py` 514, `session_service.py` 600), with split candidates and the reasons they were declined recorded in `deferred-work.md`; `StrategyGuard` is 312 against the 100-line class guideline and says so in its own docstring — no "every class under 100" claim is made. **Procedure P8 is written and NOT RUN** (needs a gateway, Redis, Postgres and RTH); its three broker-facing criteria are the story's unverified residual. | Amelia (Dev) |
| 2026-08-23 | 0.2 | Adversarial validation pass (three independent lenses, in fresh context, re-executing the story's own claims). The design held under attack — the wrapper survives `change_id`, `register()` binds the wrapped `handle_event`, re-entrant `degrade()` does not deadlock, and the full AC #1+#2 scenario gives `rc=0` — but six defects were found and fixed. **AC #10 added**: a raise from inside the guard's own `except` (which AC #4 previously *mandated* for `SessionReclaimedError`) was measured at `rc=1` with zero output, the exact silent death the story exists to close; nothing now propagates out of a wrapped handler, and the record write is queued and drained on the steady-state tick rather than performed inline on the loop thread (decision D2's 59.81s precedent). **AC #8 rewritten**: `mask_account` is a whole-value masker — `mask_account("Error 321: account DU4076626 is not managed")` returns `'***ged'` — so the specified test passed vacuously against an implementation that destroyed the payload; replaced with a `redact_accounts` substring redactor and a three-part assertion. **AC #9 corrected**: a short-circuiting latch freezes registered indicators (measured `ema.count` 1 vs 8), contradicting the warm-resume property findings #7 and #8 both rest on; the latch now suppresses side effects only. **Task 3 corrected**: `momentum` never subscribes without a seeded Cache (`on_start` calls `self.stop()`), and `bus.pub_count == 1` is impossible (cumulative; 6 before the first bar). **Public surface pinned** to one shape — two contradictory `guard_strategy` signatures, an undefined `StrategyGuard.__init__` and an unsourced `contained_failures` are replaced by a single documented contract. Numbers corrected: `SessionService` is **83** lines not 99, and the 732-line sanctioned exception is `import_data.py` not `catalog.py` (263) — both errors inherited from `deferred-work.md`. | Bob (SM) |
