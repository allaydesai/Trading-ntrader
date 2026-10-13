# Story 3.2: Submit a Strategy's Orders to the Broker

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a crossover signal to become a real order at IBKR through the same strategy code the backtest ran,
so that any difference I later measure cannot be blamed on divergent implementations.

## Why this story is shaped the way it is

This story **proves** the order path; it does not build it. The two defects that kept every order from
reaching the broker for two epics — no `routing=` on `InteractiveBrokersExecClientConfig`, and an
exec-side instrument provider that was never loaded — were found on 2026-08-28 by attempting
Procedure P7, fixed in `src/core/live_node_builder.py` (commit `90337eb`), and are covered by
regression tests that drive Nautilus's real `TradingNodeBuilder`. What has been observed live is
exactly one line: `ExecClient-INTERACTIVE_BROKERS: Submit MarketOrder(SELL 22 NVDA.NASDAQ …)`.
**No `OrderFilled`, no `PositionOpened`, and no real commission value has ever been observed.**
This story's live tier closes that gap and inherits P7 criterion 2's position-open half, deferred
out of Epic 2 on Allay's call. [Source: epics.md:1215-1243; docs/qa/phase3-live-verification.md:756-757]

The lesson this story exists downstream of: *"Market data arrives" is not evidence about orders;
only an order reaching the broker is.* The IB **data** client is built `venue=None` so Nautilus's
default-client fallback adopted it; the **exec** client is built `venue=IB_VENUE` so it never
qualified. Working market data actively concealed a dead execution path for two epics.
[Source: epics.md:1238-1243]

What this story builds new (all three are measured ABSENT today):

1. **The first consumer of the trading-permission machinery** — nothing in `src/` reads
   `ConnectionMonitor.trading_permitted`, nothing gates order submission on connection state, and
   nothing logs a suppression (AC #4).
2. **The first order-event observer** — `grep -rn "events.order" src/` finds exactly one prose
   comment (`live_strategy_guard.py:106`); the `order.*` log namespace is sanctioned but empty (AC #6).
3. **The first latency measurement** — no bar-close→submission timing exists anywhere (AC #5).

## Acceptance Criteria

1. **Given** a strategy calling `order_factory` and `submit_order` — unchanged from the backtest path
   **When** a signal fires in a live session
   **Then** the order reaches IBKR through the live execution client, with no live-specific strategy
   variant and no second execution path (FR23, AR43).
   *Evidence contract: this story lands with **zero diffs under `src/core/strategies/`** — the
   cleanest possible proof that the submitting code is the code the backtest ran.*

2. **Given** an order is submitted
   **When** its client order ID is inspected
   **Then** it comes from Nautilus's deterministic generator operating under the session-stable
   `trader_id` from Story 2.4 — not from a locally invented scheme (FR27, AR23).
   *Expected live format: `O-{YYYYMMDD-HHMMSS}-{8-hex trader tag}-{order_id_tag}-{count}`, e.g. the
   real observed `O-20260828-193805-621bb88c-000-4`.*

3. **Given** an equity or ETF instrument
   **When** position size is computed
   **Then** the existing whole-share sizing discipline applies unchanged (FR30)
   **And** a size the broker rejects for buying power is handled as a rejection, not as an arithmetic
   guarantee — no pre-trade buying-power check is added, and a rejection does not corrupt or kill
   anything (full rejection handling is Stories 3.3/3.7).

4. **Given** trading permission is false because the execution client is disconnected
   **When** a signal fires
   **Then** no order is submitted, and the suppression is logged (FR6, NFR10).
   *Scoping decision, made here so dev does not rediscover it (see Dev Notes "The AC #4 trap"):
   the suppression trigger this story owns is the **connection-loss half** of NFR10. The
   reconciliation-incomplete half is Epic 4's, because `reconcile` is a declared no-op placeholder
   until Story 4.2 and the grant path (`confirm_state_reestablished`) is deliberately never called
   in production until then. Gating on `trading_permitted` itself would suppress every order for
   the life of every session, including this story's own target fill.*

5. **Given** a bar closes and a signal fires
   **When** submission latency is measured
   **Then** the bar-close→submission interval is logged, and is under 1 second (NFR1).
   *The <1s bound is NFR33 operator-procedure evidence (read from the live P7 transcript), not a CI
   assertion; CI pins that the interval is computed from the right anchors and logged.*

6. **Given** an order is submitted
   **When** the event is logged
   **Then** `order.submitted` carries `session_id`, `client_order_id`, and `instrument_id` (AR41).

7. **Given** the landed groundwork (`90337eb`), a live IBKR paper Gateway inside RTH, and **no other
   IBKR login active anywhere** (mobile app and client portal included)
   **When** `scripts/diagnostics/run_p7_position.sh` is re-run
   **Then** a **fill** is recorded, not a submission — the transcript shows
   `OrderSubmitted → OrderAccepted → OrderFilled → PositionOpened` with a real commission value
   **And** the position survives the Ctrl-C stop untouched (P7 criterion 2's position-open half,
   deferred into this story out of Epic 2)
   **And** the transcript is grepped for IB error codes **162, 10182, 366** before any result is
   recorded (three instances of a silent subscription-killer are on record; response ownership is
   Epic 4's, detection discipline is now).
   *(AC added at story creation from the binding 2026-08-28 retro block in epics.md:1215-1243 —
   "re-run `scripts/diagnostics/run_p7_position.sh` inside RTH and record a **fill**, not a
   submission." It is the story's phase-gate contribution: the first half of the phase's ≥1 round
   trip.)*

## Tasks / Subtasks

- [x] Task 1: Measure the installed wheel before writing production code (AC: #4, #5, #6)
  - [x] 1.1 Fresh-interpreter probe (pattern: `tests/integration/core/test_live_strategy_failure_survives.py:197` `_run()` — `subprocess.run`, never `Popen`+`PIPE`): on a Python `Strategy` subclass instance, measure whether instance-level attribute assignment shadows `submit_order` and `close_position` for the strategy's **own Python calls** (`self.submit_order(...)` from `on_bar`). Story 2.7's guard proves the mechanism for `handle_bar`/`handle_event` (`LiveStrategyGuard.wrap` at `live_strategy_guard.py:296`, inner closure `:437` — the `GUARDED_HANDLERS` constant at `:102-118` is rationale, not implementation); confirm it for these two names. Also measure: does Nautilus-internal C-level dispatch bypass the instance attribute (expected: yes, and acceptable — the strategy's own Python call sites are the interception target; document the residual).
  - [x] 1.2 Measure the order-event topic shape at 1.220.0: `Strategy.register()` subscribes `events.order.{strategy_id}` (`trading/strategy.pyx:314-315`). Confirm `trader.subscribe("events.order*", handler)` (the `BAR_TOPIC` precedent, `live_session_runner.py:547`) delivers `OrderSubmitted` to a plain handler, and record which fields the event carries (`client_order_id`, `instrument_id`, `ts_init`).
  - [x] 1.3 Record both measurements in the Dev Agent Record with the probe source. If 1.1 fails (wrapping unreliable), fall back to the alternative in Dev Notes "AC #4 mechanism" — do not improvise a third design mid-implementation.
- [x] Task 2: Suppression predicate on `ConnectionMonitor` (AC: #4) — unit tier, TDD
  - [x] 2.1 RED: extend `tests/unit/core/test_live_connection_monitor.py` with tests for a new derived property (suggested name: `submission_withheld`; avoid AR36 stems `pause|halt|kill|close|finalize` and avoid `*permitted` — `trading_permitted` already means the Epic-4 grant): True when state is `LOST` or `HALTED`, or the observation is stale, or no observation has arrived yet (`AWAITING_CONNECTION`); False for `CONNECTED` and for `RECOVERING` with a fresh observation (today's permanent healthy steady state — see Dev Notes). Closed form — adopt it verbatim so no cell is arguable: `withheld = (state not in {CONNECTED, RECOVERING}) or observation_is_stale`.
  - [x] 2.2 GREEN: implement in `src/core/live_connection_monitor.py` (framework-free, derived-never-stored, same shape as `trading_permitted` at `:176-186`). Docstring must state the scoping decision from AC #4 and cite `live_session_steady_state.py:345-348` (the grant deferral) so the two properties can never be confused.
  - [x] 2.3 Cover the first-tick window: the monitor starts `AWAITING_CONNECTION` and the first `observe()` arrives on a heartbeat tick (~30s cadence); a signal cannot mathematically fire before ~3 bars (~3 min at 1-min bars), but do not rely on that — have the **runner** perform one synchronous `observe()` at steady-state start so `AWAITING_CONNECTION` never overlaps the trading phase (the runner owns the monitor — created at `live_session_runner.py:513`, and it already holds `self._connection_reader` at `:681`; `live_session_steady_state.py` stays untouched). Pin it in `tests/component/core/test_session_runner_order_path.py`.
- [x] Task 3: New module `src/core/live_order_path.py` — pre-submit suppression (AC: #4) — component tier
  - [x] 3.1 RED first (new file `tests/component/core/test_live_order_path.py`): with a stub monitor reporting withheld, a strategy whose `on_bar` fires a signal submits **nothing** and one `order.suppressed` record is logged with `session_id`, `strategy_id`, `instrument_id`, and `client_order_id` when the order object already exists (a wrapped `submit_order` has it; a wrapped `close_position` does not — log what exists, never invent). With the monitor healthy, the same signal reaches the exec layer unchanged. Anti-tautology twin: the same harness **without** the wrap installed records the order — proving the test can fail (the `TestExecutionRouting` / `TestTheProbeCanActuallyFail` pattern).
  - [x] 3.2 GREEN: implement `install_order_path(strategy, monitor, log)` wrapping the order-**creating** strategy methods only — `submit_order`, `submit_order_list`, `close_position`, `close_all_positions` — as a module-level frozenset constant. Cancels are deliberately not suppressed (cancelling while disconnected creates no exposure and cannot "blindly trade"). Membership-pin the constant in its own test AND assert it is a subset of the six-name forbidden set mirrored from `tests/unit/core/test_live_stop_path_is_inert.py:53-62` (production cannot import from tests — duplicate the literal and let the test assert the relationship; record the new pinned list in CLAUDE.md's Anti-Patterns "Membership-pinned lists" entry, same commit).
  - [x] 3.3 The public API of this module must not force forbidden identifiers on its callers: the runner and CLI are in `STOP_PATH_MODULES`, whose scan collects **call sites module-wide** — no call whose callee name is a forbidden order method may appear anywhere in those files (`_called_names` at `test_live_stop_path_is_inert.py:123-141`, applied at `:194-197`; it is call-callee-only, so a bare attribute read or string literal would technically slip it — honor the spirit, not the letter). `install_order_path(...)` satisfies this; keep it that way.
  - [x] 3.4 Wrapper containment: consult the predicate per call; on suppression log and return None; never raise out of the wrapper (a raise here re-enters strategy code paths the guard exists to protect). `except Exception` inside, log `order.suppression_failed`-style diagnostics rather than propagate.
- [x] Task 4: Order-event observer + latency in the same module (AC: #5, #6) — component tier
  - [x] 4.1 RED: publishing a Nautilus `OrderSubmitted` for an instrument whose bar was observed at a known `ts_event` produces exactly one `order.submitted` log record carrying `session_id` (bound), `client_order_id`, `instrument_id`, and `bar_close_to_submit_ms` computed as `(event.ts_init - bar.ts_event)` in ns→ms. An `OrderSubmitted` for an instrument with **no** observed bar logs `order.submitted` with the latency field absent — never a fabricated value. Any other order event type is ignored silently (Story 3.3 owns the rest of the lifecycle) — and this same ignore-silently pin doubles as AC #3's rejection-tolerance guard: an `OrderRejected` delivered today is contained without degradation or crash (full rejection semantics are Stories 3.3/3.7's).
  - [x] 4.2 GREEN: an observer object in `live_order_path.py` that (a) records `instrument_id → last bar ts_event` from the bar stream, (b) handles `events.order*` deliveries, (c) contains **all** exceptions internally — a raise from a msgbus handler re-enters `publish_c` and can end the process with zero output (the Story 2.7 lesson; `os._exit(1)` territory). Wire both subscriptions from the runner in `_phase_subscribe`, directly after the existing `BAR_TOPIC` subscribe at `live_session_runner.py:547`.
  - [x] 4.3 Latency anchor discipline (document in the module docstring): the anchor is the **venue bar close** (`bar.ts_event`), not handler entry — NFR1's purpose is the live-vs-backtest divergence window, which includes delivery lag. Known measurement hazards to disclose where the value is logged: `live_bars.received` appears twice per bar (Nautilus C logger + structlog), and the first bar after subscribe can be a backfill bar (P3 record) — live-transcript readers must read latency from steady-state bars.
  - [x] 4.4 The `session_id` on these records comes from the same bound-structlog pattern every live module uses (`structlog.get_logger(__name__).bind(session_id=...)` handed in at construction — the `ConnectionMonitor`/guard precedent). Contextvars bound in `run()` reach asyncio tasks created after the bind but are **empty on ThreadPoolExecutor threads** — do not depend on ambient context; bind explicitly.
- [x] Task 5: Runner wiring + wiring pins (AC: #4, #6) — the Story 3.1 headline lesson
  - [x] 5.1 Wire `install_order_path` at strategy materialisation, adjacent to `guard.wrap` (`live_session_runner.py:582-598`, **before** `add_strategy` — `Strategy.register()` subscribes bound methods, so a wrapper installed after is never reached). Wire the observer subscriptions in `_phase_subscribe`. Before wiring, sweep `test_session_runner_phases.py`'s existing subscribe/timeline pins (`:868-885` wraps `trader.subscribe`/`add_strategy` in an interleaved-timeline assertion) — the new calls may trip them and those tests must be extended, not weakened.
  - [x] 5.2 Component pins that construct a **`LiveSessionRunner`** (not the helpers directly) and prove the runner reaches both new call sites — Story 3.1's review found the AC #6 wiring pinned by nothing: deleting the call site left 904 tests green because every test called the helper directly. Precedent: `tests/component/core/test_session_runner_stop.py:639` `test_the_runner_reaches_the_helper_on_the_stop_path`. New file: `tests/component/core/test_session_runner_order_path.py`.
  - [x] 5.3 Disclose the line budget: `live_session_runner.py` is a sanctioned over-cap file measuring **833 raw lines at story creation** (Story 3.1's own File List recorded 795 — an undercount; the cap is measured on executable statements per CLAUDE.md, so restate the executable-statement figure when disclosing); this wiring adds ~10-20 raw lines. Record the delta in the Dev Agent Record. Do NOT split the module (Anti-Patterns: guard-list rot).
- [x] Task 6: Client-order-ID determinism pins + the `order_id_tag` deferred item (AC: #2)
  - [x] 6.1 Component pin (new file `tests/component/core/test_client_order_id_determinism.py` — these pins are about Nautilus's generator under our `trader_id`, not about `live_order_path`, so they get their own suite): a strategy registered under `TraderId("PAPER-0e8f1c2a")` produces client order IDs matching `^O-\d{8}-\d{6}-0e8f1c2a-\d{3}-\d+$` — the format is trader-tag + strategy-tag + counter from `common/generators.pyx:144-151`; the trader tag is `TraderId.get_tag()` = text after the **last** hyphen, which is why `derive_trader_id` uses `session_id.hex[:8]` (`src/core/live_trader_id.py:21-26`).
  - [x] 6.2 Canary pin (same file): `use_uuid_client_order_ids` / `use_hyphens_in_client_order_ids` are **per-StrategyConfig** options (`trading/config.py:70-72`), both at their defaults everywhere in `src/` — assert no occurrence of either name in `src/` (AST or grep-shaped test), so a future flip to UUID ids (non-deterministic, NFR6-hostile) goes red.
  - [x] 6.3 Determinism-across-restarts pin (same file): auto-assigned `order_id_tag` is registration-order-derived (`trading/trader.py:406-412`: `f"{len(order_id_tags):03d}"`; duplicate raise at `:415-419`), registration order is spec order, and the spec is immutable (FR14) — so tags are session-stable. Construction that works: two **independently materialised** `sma_crossover` `StrategySpec`s added to one real `Trader` in order → tags `000`/`001` by position. NOT one two-entry `SessionSpec` (`SessionSpec._reject_duplicate_strategies`, `src/models/session.py:360-384`, refuses same-strategy duplicates at create) and NOT crossover+momentum (momentum's registry default pins `"002"` — `sma_momentum.py:179`).
  - [x] 6.4 Close the deferred-work item re-pointed at this story (`deferred-work.md:1748-1757` — "explicit `order_id_tag` per `StrategySpec`, or a create-time validator → Story 3.2"). **Facts first, they overturn the naive design:** same-strategy duplicates are ALREADY refused at create (`SessionSpec._reject_duplicate_strategies`, `src/models/session.py:360-384`); only `MomentumParameters` declares `order_id_tag` (`src/models/strategy.py:72`); and `_normalise_parameters` materialises param-model defaults via `model_dump()` (`session.py:148`), so explicit-vs-defaulted is indistinguishable at the spec layer — an "explicit duplicates only" validator could never fire against today's built-ins. The measured start-time failure is the **explicit-vs-auto** collision (`mean_reversion` + `sma_crossover`, order-dependent `RuntimeError: order_id_tag conflict for '001'`). Therefore the validator must **simulate Nautilus's resolution**: walk the spec's entries in order, resolve each tag as `parameters.get("order_id_tag")` if present else the positional auto-assign `f"{len(taken):03d}"` (replicating `trader.py:406-419`; keep a drift-pin comment naming those lines — Task 6.3's pin goes red first if Nautilus changes the shape), and refuse any duplicate **resolved** tag at create. RED test constructible with today's built-ins: `[sma_crossover (no tag → auto "000"), sma_momentum (explicit order_id_tag="000")]` collides. Tests extend the existing `SessionSpec` unit suite under `tests/unit/models/` (locate it first; create a file only if genuinely absent). Record the disposition in `deferred-work.md`: create-time refusal via simulated resolution closes the measured case; a mandatory explicit-tag field is deliberately not imposed while `live create --strategy` is singular.
- [x] Task 7: Guard-list registration + scan compliance (AC: all) — same commit as the module
  - [x] 7.1 Add `src/core/live_order_path.py` to `NODE_FACING_MODULES` (`tests/unit/core/test_live_node_never_exits.py:42-58`) — it must never call/raise an exit.
  - [x] 7.2 Add `src.core.live_order_path` to `TestImportPurity.MODULES` (`tests/component/core/test_session_runner_phases.py:1211-1227`) — no `sqlalchemy`/`src.db`/`src.services` imports (polarity note at `:1200-1206`: `nautilus_trader` is permitted there).
  - [x] 7.3 Do **NOT** add it to `STOP_PATH_MODULES` — it legitimately names `submit_order`; record the reason inline where the lists live (the `live_status.py`/`sealed_at` precedent).
  - [x] 7.4 `LIVE_MODULE_GLOBS` covers it automatically (`test_live_dependency_invariance.py:39`) — so it may import **only** already-declared dependencies (AR3: zero new).
  - [x] 7.5 Event-name vocabulary: `order.submitted` and `order.suppressed` are conformant by construction (AR41 `order.*` namespace, dotted lowercase past tense). Never name anything with `close`/`halt`/`kill`/`pause`/`finalize` stems — `order.closed` would fail AR36. NFR26: no account identifiers in any new log record.
  - [x] 7.6 AR36 module-list decision, made here so review does not bounce it: `live_order_path.py` is **NOT** added to `NEW_OR_MODIFIED_FOR_STOP` (`test_live_stop_path_is_inert.py:412-421`) — it is not a stop-path module; record the reason inline where the list lives (the Task 7.3 pattern). Hygiene regardless: keep operator-facing prose in the module free of the five stems — note the scan's `\b{word}\w*\b` regex matches the literal string `"close_position"` (underscore is `\w`), so a raw method-name literal inside a log call would trip the scan if the module ever joined the list.
- [x] Task 8: Live verification — the fill (AC: #1, #2, #5, #7) — operator procedure, NFR33 tier
      **DONE 2026-09-01.** AC #7 is closed: a real fill with the real broker commission was
      recorded inside RTH. `order.filled` carried `fill_qty=22 cum_qty=22 order_qty=22
      last_px=217.83 commission='1.00 USD' trade_id=00025b45.6a9b76bb.01.01 venue_order_id=103
      position_id=NVDA.NASDAQ-SMACrossover-000`, followed by `PositionOpened` for a
      **strategy-owned** position. Session `p7-fill-0901`; full transcript analysis in
      `docs/qa/phase3-live-verification.md`, P7 result log, 2026-09-01 row (phase ⓷).
      Two defects were found and fixed on the way to it — an upstream `avg_px` serialization
      crash that killed the node on the *first* fill attempt (`src/core/live_exec_avg_px.py`),
      and a self-inflicted factory class-name regression that stopped four sessions starting.
      Both are written up in that same row. ⚠️ `bar_close_to_submit_ms` read **65682.202** against
      NFR1's `<1000`: the metric's anchor is the bar's *open* for this adapter, so the figure is
      not a real latency (true interval ~1ms) and NFR1's definition needs an epic-level ruling —
      recorded in `deferred-work.md`, deliberately NOT silently re-anchored here.
      *(Ruled 2026-09-10, option (c): the field is now anchored on the close and a second field,
      `bar_arrival_to_submit_ms`, carries the interval NFR1's bound is judged on — see the
      deferred-work item's disposition. The 65682 figure above is the pre-ruling field's.)*
  - [x] 8.1 Preconditions checklist, all hard-learned: (a) inside RTH; (b) **no other IBKR login** — no mobile app, no client portal (error 162 evicted market data on four consecutive runs while everything looked healthy); (c) the Gateway must NOT be the compose-managed one — `docker-compose.yml:51` hardcodes `READ_ONLY_API: "yes"` and the Gateway itself will refuse the first order; use the bare Gateway install; (d) Redis up (the kernel blocks forever on unreachable Redis); (e) strategy is `sma_crossover` — **never** `sma_crossover_long_only`/`sma_long`, whose `on_stop()` still flattens (unversioned `custom/` submodule) and would manufacture the exact fake exit Story 3.1 removed.
  - [x] 8.2 Session recipe (proven to signal within ~2.5 min on 2026-08-28): `sma_crossover` on `NVDA.NASDAQ`, `fast_period=2`, `slow_period=3`, `position_size_pct=0.5`. The script does **not** create the session (`run_p7_position.sh:36` goes straight to `live start`): reuse the existing `p7-position-test` session — created 2026-08-28 with exactly this spec, immutable, reusable across stop/start cycles — or create a fresh one via `ntrader live create` (singular `--strategy`; the session shape is recorded at `phase3-live-verification.md:756-757`). Then run `./scripts/diagnostics/run_p7_position.sh` (waits for `PositionOpened(`, snapshots broker state, SIGINTs the real runner PID — `uv run` does not forward signals, the script already handles this).
  - [x] 8.3 Record in `docs/qa/phase3-live-verification.md` (dated, in P7's result log): the full `OrderSubmitted → OrderAccepted → OrderFilled → PositionOpened` sequence; the real commission value; the client order ID and its format; the `order.submitted` record with its fields and `bar_close_to_submit_ms` (< 1000 — NFR1's live evidence); broker positions before/after the stop **identical** (criterion 2 closed); the 162/10182/366 grep result. Also close FR18's live half note (`:669-687`) — this is the first stop over a session-owned position.
  - [x] 8.4 If the market is closed or the Gateway is unavailable when dev completes, the automated tiers still gate the story into review; AC #7 is then the explicit remaining item and the story must NOT be marked done until it runs (the Epic 2 retro's standing rule: run each story's live procedure before the story closes, not on the epic's last day).
- [x] Task 9: Mutation sweep from the AC list + closeout (AC: all)
  - [x] 9.1 Select mutations **from the AC list, not from what feels fragile** (Epic 2 retro action item), minimum one per AC, scripted with break→observe-red→revert, naming the killing test each time: (M1/AC4) invert or bypass the predicate consult in the wrapper; (M2/AC4) delete the runner's `install_order_path` call site — the Task 5.2 pin must go red, nothing else may be relied on; (M3/AC6) drop `client_order_id` from the `order.submitted` record; (M4/AC5) compute latency from handler-entry time instead of `bar.ts_event`; (M5/AC2) flip `use_uuid_client_order_ids=True` on a strategy config; (M6/AC2) make `derive_trader_id` return `str(uuid)` (hyphens — the tag silently becomes the UUID tail); (M7/AC6) delete the observer subscription call site; (M8/AC1) plant an `is_live` branch or a lifecycle-hook order call under `src/core/strategies/` → must be killed by the existing scans (`test_live_stop_path_is_inert.py:266-303`), proving the zero-diff contract is checkable; (M9/AC3) alter `_calculate_position_size`'s whole-share branch (`sma_crossover.py:158-160`) → name the killing test, and if nothing goes red, FIRST add the whole-share unit pin — AC #3's "unchanged" is then guarded rather than asserted. AC #7 is exempt from mutation by design: it is NFR33 operator-procedure evidence, recorded as such. When a mutation stays green, inspect the FIXTURE before concluding the guard is missing (2.8's mutation #7 lesson).
  - [x] 9.2 Run the clause matrix (Story 3.1's method, recorded in its file under "AC #6 clause-by-clause verification matrix"): decompose each AC into distinct obligations, mutate each in isolation, run the whole plausible guard surface, record which named test fires. One test per AC is a coverage claim, not a proof.
  - [x] 9.3 Full gates: `make format && make lint && make typecheck`; unit, component, integration `--forked`, e2e; Epic 1 acceptance sweep 40/40; record all counts (baselines: unit 2397, component 1305/16 skipped, integration 281/2 skipped, e2e 1).
  - [x] 9.4 Update `deferred-work.md` (order_id_tag disposition per Task 6.4 — done; the "strike when AC #7 records the fill" clause deliberately NOT actioned yet — AC #7 has not run, see Task 8) and `sprint-status.yaml`.

### Review Findings

Code review 2026-08-30, three adversarial layers (Blind Hunter — diff only, no spec or project
access; Edge Case Hunter — diff plus project and installed-wheel read access; Acceptance Auditor —
diff, spec and context docs), plus orchestrator verification of every finding before triage. 56 raw
findings → 4 decisions / 13 patches / 12 deferred / 8 dismissed. Every finding below was confirmed
against the tree; refuted claims were dropped rather than passed through.

**Independent of the layers, one claim in the story record is wrong and is recorded here rather
than silently corrected:** Task 9.1's mutation table is sound for M1–M3 and M5–M9, but M4's guard is
weaker than claimed — see `[Review][Patch]` item 3.

- [x] [Review][Decision] The predicate's `LOST`/`HALTED` arms are unreachable in production, and the disconnect path that *is* reachable is silent and untested — `_has_ever_connected` is set only in `_grant_permission`, reached only via `confirm_state_reestablished`, which has zero production callers (`grep` confirms: definition and docstrings only). So `_observe_disconnected` always takes the `RECOVERING and not _has_ever_connected` branch (`live_connection_monitor.py:331-337`) and routes a real mid-session disconnect to `AWAITING_CONNECTION`, not `LOST`. Consequences: (a) `submission_withheld` still returns `True`, so AC #4's *outcome* holds; (b) but no `connection.lost` fires, so a genuine broker drop produces zero output in the `connection.*` namespace; (c) `_unavailable_since` is never set, so `_check_halt_deadline` always returns early and `HALTED` — hence NFR4/NFR20's halt — cannot occur in a real session; (d) every new `TestSubmissionWithheld` case for `LOST`/`HALTED` reaches those states through `_drive_to`'s `_connected()` helper, i.e. through the one call site production never makes, while the production-reachable transition has no test at all. `_drive_to`'s docstring asserts the opposite: *"a state the public API cannot reach is a state that cannot occur in production."* Related: from `HALTED`, one connected reading returns the monitor to `RECOVERING` and `submission_withheld` to `False`, with `_halt_reported` latched so the halt never re-fires — a halted session would silently resume submitting once Epic 4 makes `HALTED` reachable. Decide: is `AWAITING_CONNECTION` the intended production disconnect path (and should it log), and should `HALTED` be terminal for submission?
- [x] [Review][Decision] NFR1's latency anchor is keyed per *instrument*, not per *bar type* — `OrderEventObserver.note_bar` stores `self._last_bar_ts_event[str(bar.bar_type.instrument_id)]` (`live_order_path.py:230-231`) while subscribed to the wildcard `BAR_TOPIC` (`data.bars.*`). `StrategySpec.bar_types` is a tuple and `subscription_bar_types` flattens across strategies, so one instrument can carry two aggregations; whichever bar arrives last overwrites the other and `bar_close_to_submit_ms` is then measured from a bar the signal did not use. `OrderSubmitted` carries only `instrument_id`, so a per-bar-type key cannot be resolved from the order event alone — the fix is a design choice (key on bar type and map order→strategy→bar type; or keep the instrument key and log which `bar_type` the anchor came from; or restrict `note_bar` to the traded aggregation). Found independently by two layers.
- [x] [Review][Decision] A suppressed order is indistinguishable from a submitted one to the strategy — the wrapper returns `None` (`live_order_path.py:141-142`), which is exactly what the real `cpdef void` methods return on success. No exception, no sentinel, no callback. A strategy that tracks entry state internally (`self._in_position = True` after `submit_order`) is left believing it holds a position that was never opened, and no later reconnection reverses that. `sma_crossover` re-derives from the portfolio so it is unaffected today, which is also why no test can see this class of failure. Stories 3.3/3.7 own *rejection* semantics, but nothing downstream is scoped to own *suppression* feedback. Decide: accept silent suppression as the contract, or give the strategy a signal.
- [x] [Review][Decision] `order.suppressed` routes an AR36-forbidden stem into an operator-facing record — at runtime a suppressed close emits `method=close_position` / `method=close_all_positions`, and `close` is one of AR36's five stems. The AR36 scan structurally cannot see it (`_operator_facing_strings` walks only string constants inside log/print calls; the name arrives through a variable), and the new comment at `test_live_stop_path_is_inert.py:433-441` documents that blind spot accurately — but offers the documentation in place of compliance. Decide: AR36 tolerates the stem inside the `order.*` namespace (say so in AR36), or the field carries a neutral token.

- [x] [Review][Patch] `order.suppressed` omits `strategy_id`, which Task 3.1 mandates, and the guarding test was written to the code rather than the spec [src/core/live_order_path.py:157] — Task 3.1 requires `session_id`, `strategy_id`, `instrument_id`, `client_order_id`; `_describe` returns at most two of those and the module's only `strategy_id` occurrence is a docstring reference at `:98`. Not a "log what exists" case: `Order.strategy_id` and `Position.strategy_id` are on the objects already in hand, one line from the `instrument_id` read at `:181-190`. `test_live_order_path.py:144` asserts `session_id`, `method`, `instrument_id` and `client_order_id` presence — adding or omitting `strategy_id` goes red in neither direction. With two strategies on one instrument (which `SessionSpec` explicitly permits) an operator cannot tell which was withheld.
- [x] [Review][Patch] The two new message-bus subscriptions are never cancelled on stop [src/core/live_session_runner.py:420-422] — `_phase_subscribe` now makes three `trader.subscribe` calls but `_unsubscribe` still delegates to `unsubscribe_bar_topic`, which cancels only `steady_state.note_bar`. `live_session_node.py:295-313`'s docstring ("what nothing cancels today is the runner's **own** subscription") is now two-thirds false, and the existing stop pin cannot notice because it hardcodes `len(node.trader.unsubscriptions) == 1` and index `[0]` (`test_session_runner_stop.py:325-336`). Effect: bars and order events keep reaching `OrderEventObserver` through teardown, so `order.submitted` records can land after `session.stopped` — in the very transcript Task 8.3 asks the operator to read.
- [x] [Review][Patch] Mutation M4 over-claims: the bar fixture makes the two plausible anchors identical [tests/component/core/test_live_order_path.py:84-95] — `make_bar` sets `ts_event=index * 60_000_000_000, ts_init=index * 60_000_000_000`. Mutating `bar.ts_event` → `bar.ts_init` at `live_order_path.py:231` — the realistic wrong anchor, and the one that *excludes* the delivery lag NFR1 exists to measure — leaves every test green. M4 as scripted only caught `time.time_ns()`, which is off by ~1.8e12. Give the fixture distinct `ts_init`, then re-run M4 against `bar.ts_init`.
- [x] [Review][Patch] The "before `add_strategy`" ordering is asserted by the code, the docstring and the test's own name, and pinned by none of them [tests/component/core/test_session_runner_order_path.py:234-256] — `assert strategy in node.trader.added_strategies` is a membership check. Moving `install_order_path(...)` after `add_strategy`, or after `start_strategy` (which would leave `on_start()` order calls unwrapped), leaves the test green. The correct pattern is two files over: `test_session_runner_phases.py::test_note_bar_is_subscribed_before_any_strategy_is_added` builds an interleaved `timeline` and asserts index order. Story 3.1's lesson applied one level too shallowly — deletion is guarded, reordering is not.
- [x] [Review][Patch] No happy-path pin that the wiring actually permits submission, and no component test wires a real `ConnectionMonitor` [tests/component/core/test_session_runner_order_path.py] — `TestConnectionObservedBeforeTrading` asserts `submission_withheld is True` on both failure paths (`:211`, `:223`) and `observed_state_at_trading == [RECOVERING]` on the happy path, but never `submission_withheld is False` at trading time. A change to the staleness window or to `observe()`'s timestamping would suppress every order with this suite green. Compounding it, every `test_live_order_path.py` case uses `_StubMonitor`, whose `submission_withheld` is a plain attribute — were the real property to become a method, `bool(bound_method)` is `True` and every order would be withheld forever with those tests still passing.
- [x] [Review][Patch] The non-vacuity probe for the UUID/hyphen scan never calls the scanner [tests/component/core/test_client_order_id_determinism.py:207-210] — `test_the_scan_would_catch_a_planted_reference` re-implements the AST walk inline and asserts that CPython's `ast` records keyword names. It passes if `_all_identifiers` is deleted, returns `set()`, or is inverted. The one test that exists to prove the scan is non-vacuous is the one test that does not touch it. Related: `test_neither_flag_is_referenced_anywhere_in_src` passes on an empty `offenders` list with no assertion that any file was scanned.
- [x] [Review][Patch] A test whose name promises message assertions makes none [tests/unit/models/test_session_spec.py:157-171] — `test_the_collision_message_names_the_tag_and_the_colliding_strategy` asserts only `any(error["type"] == "value_error")`. It passes if the message is empty, names the wrong strategy, or the whole operator-facing explanation block (`session.py:437-445`) is deleted. The assertions its name promises already exist at `:120-122` in the test above it, so this one contributes a green tick and a false coverage claim. Assert the content or delete it as a duplicate.
- [x] [Review][Patch] The `FORBIDDEN_ORDER_METHODS` copy has no drift guard, and the stated reason for copying does not apply [tests/component/core/test_live_order_path.py:48-62] — `ORDER_CREATING_METHODS <= _STOP_PATH_FORBIDDEN_ORDER_METHODS` (`:232`) is asserted against a hand-copied literal that nothing ties to the real set. If the real set ever shrinks — exactly the failure mode CLAUDE.md's "Membership-pinned lists" entry was added for — the copy keeps the removed name and the subset assertion keeps passing against a stale relationship. The justification given ("production code cannot import from `tests/`") is a non-sequitur here: both are test files, `tests/__init__.py` exists, and `test_session_runner_order_path.py:24` already does `from tests.component.doubles import TestLiveNode`. The CLAUDE.md paragraph this commit adds records the relationship as checked when only one side's copy is.
- [x] [Review][Patch] The module docstring's containment premise is false for a subset of call sites [src/core/live_order_path.py:56-59] — it justifies leaving the pass-through path un-`try`'d because Story 2.7's boundary "already wraps every strategy call this module's calls happen inside". `GUARDED_HANDLERS = ("handle_bar", "handle_event")` (`live_strategy_guard.py:114`) — `on_start`, `on_stop`, `on_reset` and timer callbacks are outside it, and that file's own note #1 records widening the tuple as deferred work. `custom/sma_crossover_long_only.py:86` still flattens in `on_stop`, so the new wrapper code (a predicate read and a log call) does run uncontained there. The decision may stand; the stated reason needs correcting.
- [x] [Review][Patch] Latency is emitted with no plausibility band [src/core/live_order_path.py:258-260] — the guard is `is not None`, not a range check. A bar carrying `ts_event == 0` yields `ts_init / 1e6` ≈ 1.7e12 ms (~55 years) logged as the NFR1 measurement; an out-of-order or backfill bar yields a negative one. Neither is tested (the suite covers `+500ms` and the absent-anchor case only). Separately, the subtraction crosses clock domains — `bar.ts_event` is the venue's instant, `event.ts_init` the local host's — so host/venue skew lands in the number whole, and the docstring's hazard list mentions delivery lag but not skew. Logging both anchors alongside the delta would let a transcript reader detect skew instead of inheriting it.
- [x] [Review][Patch] Failure diagnostics carry an exception class name and nothing else [src/core/live_order_path.py:135-139, 232-235, 243-246] — `order.suppression_failed` logs `method` + `error_type`; `order.observer_failed` logs `stage` + `error_type`. No message, no `exc_info`, no arguments. If the predicate read ever starts raising, *every order in the session is dropped* and the operator's entire evidence base is a stream of `error_type=AttributeError` with no repro path.
- [x] [Review][Patch] Double installation nests wrappers silently [src/core/live_order_path.py:121-123] — `base = getattr(strategy, method_name)` returns the already-wrapped function on a second call, giving two predicate consults and two `order.suppressed` records per call, N-deep on N installs. The runner calls it once per strategy today, but the function is public and framework-free; a sentinel check costs one line.
- [x] [Review][Patch] Three checkable counts in the story record contradict the tree — Completion Notes claim `test_session_runner_order_path.py` has 11 tests (actual: 7 `def test_`, none parametrised); the clause matrix claims `TestSubmissionWithheld` has 9 (actual: 8 functions / 14 collected cases — neither figure is 9); `sprint-status.yaml:49` says `live_order_path.py` is 256 lines (actual: 261, which is also what `git show --stat` reports). By contrast the Task 5.3 line-budget disclosure was verified exact, digit for digit.

- [x] [Review][Defer] `_elapsed_since`'s clamp makes a backward clock step read as *maximally fresh*, which is the outcome its own docstring says must never happen [src/core/live_connection_monitor.py:381-389] — deferred, pre-existing (Epic 1); default `time_source` is `time.monotonic`, but `submission_withheld` now gates orders on it.
- [x] [Review][Defer] `assert` used as a production guard on the order path [src/core/live_session_runner.py:575, 633] — deferred, pre-existing convention in this module; under `python -O` the asserts vanish, `install_order_path(strategy, None, log)` succeeds, and every order is silently suppressed for the session's life.
- [x] [Review][Defer] The `log.error` inside each `except` is itself the operation most likely to be broken [src/core/live_order_path.py:135, 233, 244] — deferred, universal pattern in this codebase; a structlog sink failure in the `try` recurs in the handler and escapes.
- [x] [Review][Defer] `_last_bar_ts_event` is never pruned [src/core/live_order_path.py:225] — deferred; bounded by the session's subscribed instruments today, unbounded by construction on a wildcard topic.
- [x] [Review][Defer] The C-logging guard fixture is order-dependent [tests/component/core/test_client_order_id_determinism.py:46-57] — deferred, copied pattern; once an earlier test in the process initialises C logging, `before` is `True` and the claim it "machine-enforces" no longer holds.
- [x] [Review][Defer] `test_matches_the_pinned_closed_form_for_every_state` is the implementation re-typed, not an independent check [tests/unit/core/test_live_connection_monitor.py] — deferred; its docstring claims it "cannot pass by tautology", but it recomputes the same expression over the same inputs and can only detect divergence between two copies of one formula. A hand-written `(state, stale) -> expected` truth table is the honest version.
- [x] [Review][Defer] `submit_order_list` suppression names only the first order's instrument [src/core/live_order_path.py:166-170] — deferred; the rest of a multi-instrument bracket is invisible in the record.
- [x] [Review][Defer] `ORDER_CREATING_METHODS` is never checked against the real Nautilus `Strategy` surface [src/core/live_order_path.py:87-89] — deferred; both lists are hand-maintained, so a framework upgrade exposing a new order-creating entry point leaves it unwrapped with every test green. A `dir(Strategy)` test asserting no *unlisted* public `submit*`/`close*` name exists would catch it.
- [x] [Review][Defer] The new validator is a read-path gate, not only a create-time one [src/models/session.py:411-448] — deferred; as a `model_validator(mode="after")` it also runs on `SessionSpec.from_stored` at `src/cli/commands/live.py:393`, so a spec persisted before this commit that collides would fail `live start` as a `ValidationError`. Unreachable today, but FR14 treats a stored spec as immutable and the docstring describes the validator strictly as create-time.
- [x] [Review][Defer] The cancel-exclusion rationale is argued on the wrong axis [src/core/live_order_path.py:43-46] — deferred; a cancel issued on a known-lost connection does not cancel anything, leaving a live working order the strategy believes is gone. The exposure is not *new*, but the believed-vs-actual divergence is the failure class this boundary exists to prevent. The exclusion is likely right; the reason needs a sentence.
- [x] [Review][Defer] Strategy containment shifts Nautilus's positional tags away from the create-time simulation [src/models/session.py:435-448] — deferred; a strategy contained by `_start_strategy` never reaches `add_strategy`, so runtime `len(order_id_tags)` diverges from spec position. Direction is safe (create-time is stricter), but it can refuse a spec that would have run.
- [x] [Review][Defer] An empty or whitespace-only `order_id_tag` is treated as explicit [src/models/session.py:227-230] — deferred; matches Nautilus's own `in (None, str(None))` test, so the simulation is faithful, but both then produce a degenerate `StrategyId`.

#### Review fixes applied 2026-08-30

All 4 decisions were ruled by Allay and all 17 patches applied, TDD Red→Green with the RED
observed before each fix. Gates after: format 491 unchanged · lint clean · mypy clean (104 files) ·
**unit 2419** (was 2417) · **component 1353/16 skipped** (was 1334/16) · integration `--forked`
281/2 skipped (unchanged) · e2e 1 (unchanged) · Epic 1 sweep **43/43** · `git diff --stat
src/core/strategies/` still empty.

Decisions, as ruled:

1. *Unreachable `LOST`/`HALTED` arms* → **log the reachable transition only**; the halt-clock gap
   stays Epic 4's. Implemented in `SessionSteadyState._observe_connection`, **not** in the monitor:
   the monitor's silence on that branch is a deliberate Epic 1 decision pinned by
   `test_a_half_up_first_connection_that_drops_is_still_not_a_loss`, and emitting `connection.lost`
   there would start a halt clock for a broker that was never reached. The poller is the only
   component holding both the previous state and the next, so it now emits
   `connection.state_changed` once per transition. `_drive_to`'s false docstring claim is corrected
   and `TestSubmissionWithheld` gained the two production-path cases it never had.
2. *Per-instrument latency anchor* → **restrict + self-describe**. The runner passes each spec
   entry's `bar_types[0]`; `note_bar` ignores every other aggregation; every emitted latency carries
   `latency_anchor_bar_type`.
3. *Silent suppression* → **accept as the contract, assign an owner**. Documented in the module
   docstring; the un-owned gap recorded in `deferred-work.md`.
4. *AR36 stem in `method=close_position`* → **exemption granted and recorded** inline where the list
   lives, so the next review does not re-open it.

Two things worth carrying into the retro:

- **M4 was a strawman and is now real.** `make_bar` set `ts_event == ts_init`, so the plausible wrong
  anchor (`bar.ts_init`, which excludes the delivery lag NFR1 exists to measure) survived; only the
  absurd `time.time_ns()` variant died. With distinct timestamps the mutation was re-run and kills
  **5** tests. The lesson generalises: a mutation is only as strong as the fixture's ability to tell
  the mutant from the original.
- **Four counts in this story's own record were wrong** (`11`→7 tests, `9`→8 tests, `256`→261 lines,
  `40/40`→43/43), every one of them checkable in seconds. None changed a conclusion, but a record
  that reports unverified numbers alongside verified ones spends the credibility of both.

**Dismissed as noise (8, recorded so they are not re-raised):** torn read of `state` + staleness (GIL-level window against a 30 s poll); no suppressed-order counter on the session record (the story's scope fence forbids DB writes); `OrderDenied`/`OrderRejected` leaving no `order.*` record (Story 3.3 owns the lifecycle, and the observer's type filter is the sanctioned containment); the `close_position` outer/inner double-consult half-run (requires the predicate to flip mid-call); catching `BaseException` in the msgbus handlers (would swallow the `KeyboardInterrupt` the runner's stop path depends on); heartbeat cadence vs staleness window causing periodic self-suppression (**refuted by measurement** — 30 s tick against a 60 s `DEFAULT_MAX_OBSERVATION_AGE_SECONDS`, no overlap; nothing *enforces* the relationship, which is the only residue); "no test asserts a resolved `order_id_tag` value" (**refuted** — `test_session_spec.py:120-122` asserts both `"000"` and `"momentum"` in the refusal message); and the AR36 scan tripping on the `ORDER_CREATING_METHODS` frozenset literal (**refuted** — `_operator_facing_strings` walks only constants inside log/print calls, so the exclusion comment is correct as scoped).

## Dev Notes

### The AC #4 trap — read before designing anything

`ConnectionMonitor.trading_permitted` (`src/core/live_connection_monitor.py:176-186`) is **`False`
for the entire life of every session today**, and that is by design: the only grant path,
`confirm_state_reestablished()`, is deliberately never called in production —
`src/core/live_session_steady_state.py:345-348` says so explicitly ("Granting permission stays
Epic 4's"), because Epic 1's retro requires the grant to follow *genuine* reconciliation and
`reconcile` is a no-op placeholder until Story 4.2. On a healthy session the monitor sits
permanently in `RECOVERING` with fresh observations (states: `AWAITING_CONNECTION | CONNECTED |
RECOVERING | LOST | HALTED`; observations arrive from the heartbeat tick via
`live_session_steady_state._observe_connection()` → `live_connection_probe.read_ibkr_connection_status`,
Story 1.6's fail-closed read of the adapter's private flags — at 1.220.0 a socket drop publishes no
message-bus event, so polling those flags is the only truth).

Therefore: **gate on known loss, not on absent grant.** The new predicate (Task 2) says "withhold"
when the connection is believed lost or unobserved; it says "do not withhold" in the healthy
`RECOVERING`-with-fresh-observation state. NFR10's other half (no orders while reconciliation is
incomplete) is Epic 4's by phase design — every order this phase submits, including AC #7's fill,
happens with reconciliation incomplete, and the epics sequence sanctions exactly that (Epic 3 before
Epic 4; "the order path that consults this permission flag arrives in Epic 3" — epics.md:720).
Write this scoping into the predicate's docstring so review does not re-litigate it.

### AC #4 mechanism — decided, with the fallback named

**Primary (build this):** instance-level wrapping of the order-creating strategy methods, installed
by the runner between `materialise_strategy` and `add_strategy` — the exact mechanism Story 2.7's
guard already uses for `handle_bar`/`handle_event` (`LiveStrategyGuard.wrap`,
`live_strategy_guard.py:296`, inner closure `:437`; runner wiring `live_session_runner.py:582-598`;
ordering is load-bearing because `Strategy.register()` subscribes **bound** methods). Strategies are Python subclasses of the Cython `Strategy`, so instances carry a
`__dict__` and attribute assignment shadows the class method for the strategy's own Python-level
calls (`self.submit_order(...)` in `_generate_buy_signal` etc.). Task 1.1 measures this before any
production code. Known residual to document: Nautilus-internal C-level dispatch (e.g.
`close_position`'s internal submit) bypasses an instance attribute — which is why `close_position`
itself is in the wrap set: intercepting the strategy's Python call to it suppresses the whole
sub-chain.

**Fallback (only if Task 1.1 measures the wrap unreliable):** Nautilus's `RiskEngine` trading-state
(`set_trading_state`, states ACTIVE/REDUCING/HALTED at 1.220.0) driven from the steady-state tick —
framework-native and catches every path, but it is **unmeasured in this repo**, produces
`OrderDenied` events whose semantics belong to 3.3/3.7, and its restore-on-reconnect direction must
still honor the same scoping decision. Measure before choosing it; do not invent a third design.

**Why a new module:** `live_strategy_guard.py` and `live_session_runner.py` are both in
`STOP_PATH_MODULES` (`test_live_stop_path_is_inert.py:38-46`), whose scan forbids any **call** to a
forbidden order method anywhere in the module (call-callee collection via `_called_names`,
`:123-141`, applied at `:194-197`) — and a wrapper module traffics in those names and may
legitimately call the wrapped originals. Hence `src/core/live_order_path.py`, kept OUT of
`STOP_PATH_MODULES` deliberately (Task 7.3).

### Measured Nautilus 1.220.0 facts — cite, don't re-derive

All measured against the installed wheel (`.venv/.../nautilus_trader`); "Nautilus docs are not
evidence" is a standing rule (`live/config.py:48-50`'s docstring is measurably wrong; epics.md:784-786).

- **Client order ID**: `ClientOrderIdGenerator.generate()` (`common/generators.pyx:117-153`),
  default flags → `O-{YYYYMMDD-HHMMSS}-{trader_tag}-{strategy_tag}-{count}`. Trader/strategy tags are
  `get_tag()` = substring after the **last** hyphen (`model/identifiers.pyx:727-736, 807-816`).
- **Counter restore** (what makes a restart not a duplicate): `Strategy._start()` counts cached
  client order IDs for its `strategy_id` and calls `set_client_order_id_count`
  (`trading/strategy.pyx:353-376`) — works only if the Redis namespace is rejoined, which requires
  the same `trader_id`, which `derive_trader_id(session_id)` guarantees (`src/core/live_trader_id.py:58-87`,
  format `PAPER-{hex[:8]}`).
- **`order_id_tag` auto-assignment**: `Trader.add_strategy` rewrites `None` tags to
  `f"{len(order_id_tags):03d}"` in registration order (`trading/trader.py:406-412`) and **raises on
  duplicates** (`:415-419`). `SMAConfig` has no `order_id_tag` (→ `000` first); `SMAMomentumConfig`
  *requires* the field with no default (`sma_momentum.py:26`) — the `"002"` default lives in the
  registry default config (`sma_momentum.py:179`). Known start-time collision:
  `mean_reversion, sma_crossover` ordering raises while the reverse is fine
  (`deferred-work.md:1748-1757`).
- **UUID/hyphen flags are per-StrategyConfig** (`trading/config.py:70-72`), not per-node; unset
  anywhere in `src/` → deterministic format holds.
- **Order-event subscription**: `Strategy.register()` subscribes `events.order.{strategy_id}` /
  `events.position.{strategy_id}` (`trading/strategy.pyx:314-315`).
- **The containment wrapper is Epic 3's input path — do not touch it**: `execution/engine.pyx:1170-1187`
  clears `_pending_position_events` **before** publishing the order event, so a raise in
  `handle_event` silently loses the `PositionOpened/Changed/Closed` that follow. Story 2.7 wrapped
  `handle_event` ahead of need for exactly this story (`live_strategy_guard.py:104-118`). Never
  "tidy it away", and never let a new msgbus handler raise (a raise re-enters `publish_c` → the
  `os._exit(1)` failure mode with zero output).
- **The submit path on stop**: `TradingNode.stop()` runs `trader.stop()` while the exec client is
  still connected (`system/kernel.py:1075-1076`) — an order submitted from a stopping strategy
  WOULD reach the broker post-`90337eb`. Story 3.1 removed the built-in flatten;
  `custom/sma_crossover_long_only.py:86` still has one.

### Existing order-path state (inherited, regression-covered — do not re-fix, do not re-implement)

- `routing=RoutingConfig(default=True)` — `src/core/live_node_builder.py:379` (mechanism comment
  `:359-378`). Exec-side `load_ids` equal to the data side — `:356-358` (rationale `:340-355`:
  the adapter dereferences `instrument_provider.find(...).is_inverse` with no None check at
  `adapters/interactive_brokers/execution.py:525`).
- Pinned by `tests/component/core/test_live_node_builder.py`: `TestInstrumentLoading` (`:652`,
  exec `load_ids` asserted **equal** to data's), `TestExecClientDefaultRouting` (`:1413`, incl. the
  1.220.0 canary that the stock default is still `False`), `TestExecutionRouting` (`:1614`, drives
  the real `TradingNodeBuilder.build_exec_clients`; anti-tautology twin proves the harness counts
  zero under stock `RoutingConfig`).
- Strategy submit sites: `sma_crossover.py` `_generate_buy_signal`/`_generate_sell_signal`
  (`order_factory.market` + `submit_order` at `:216-221`, `:251-256`; `close_position` for opposite
  positions at `:208`, `:243`); `sma_momentum.py` submit at `:137`, `:156`, `:165`. Whole-share
  sizing: `sma_crossover.py:158-160` (`max(int(raw_qty), 1)` when `size_precision == 0`).
  **`_calculate_position_size`'s division shape (`position_value / current_price` at `:149`) must
  not change** — `scripts/diagnostics/live_contain_probe.py` (P8's harness) depends on feeding a
  `0.00` close to raise `decimal.DivisionByZero` through it.
- `trader_id` feed: `derive_trader_id` in runner `__init__` (`live_session_runner.py:199`) →
  node factory `:475` → `TradingNodeConfig(trader_id=...)` (`live_node_builder.py:291, :391`).
  Redis namespace comes from `trader_id`, not `CacheConfig` (`:395-398`).

### Scope boundaries — what this story must NOT build

- **No `order.accepted`/`order.filled`/`order.rejected` structured events** — Story 3.3 owns the
  lifecycle. The observer ignores everything but `OrderSubmitted`; build it so 3.3 extends the same
  subscription point. AC #7's fill evidence comes from Nautilus's own transcript lines, not from
  structured events this story doesn't own.
- **No retry, no backoff, no query-don't-retry machinery** — AR24/Story 3.4. A retry decorator
  anywhere near the order path is an auto-reject in review (AR43).
- **No trade recorder, no DB writes, no fencing column** — Stories 3.5/3.6 (D1 is ruled to 3.6; do
  not absorb it). This story touches no repository and no migration; head stays `b7c419e2a3d8`.
- **No rejection-visibility surfacing** — Story 3.7 (FR49/NFR24).
- **No sizing refactor** — AC #3 says *unchanged*. The measured duplication (sizing logic inline
  per strategy; `PositionSizingLogic`/`SMATradingLogic` exist but have zero strategy consumers) is
  real and stays: consolidating it would put a diff under `src/core/strategies/` and break AC #1's
  zero-diff evidence contract.
- **No grant path, no reconciliation** — Epic 4 (Story 4.2). `confirm_state_reestablished` keeps
  zero production callers.
- **No new exception names** (D3's `exit_outcome` marker is decided but unbuilt — adding names now
  worsens the collision it fixes); no CLI changes (the structured log IS the operator surface;
  `live.py` is in `STOP_PATH_MODULES` and cannot name order methods anyway); AR44's untouched list
  applies (`src/api/**`, templates, `backtest_query.py`, comparison views, `BacktestOrchestrator`).

### Hazards (every one bit a previous story)

1. **Epoch-zero bar timestamps SIGABRT** any Equity `BacktestEngine.run()` descended from pytest —
   use realistic timestamps; `_make_daily_bars`' fixed recent anchor is the template
   (`tests/integration/test_sma_strategy_nautilus.py:295`). This killed two whole test files once.
2. **`conftest.py::setup_backtest_venue` (NETTING/CASH) crashes with Equity** — use HEDGING/MARGIN +
   `IBKRCommissionModel` (the `backtest_orchestrator.py` production shape) if a real engine is needed.
3. **`BacktestEngine` is single-use**; extract results before disposal; two engines in one test drop
   the LogGuard unless `LoggingConfig(bypass_logging=True)`.
4. **`trader.strategies()` is a METHOD** (`trading/trader.py:160`) — an uncalled attribute iterates
   as a `TypeError` that teardown containment silently swallows. Stub doubles must expose it as a
   callable; every stub strategy must set flags explicitly (bare MagicMock attributes are truthy).
5. **StrategyRegistry is process-global** — force `discover()` before snapshotting; never depend on
   a `custom/` strategy in tests (CI without the submodule silently drops 5 of 7 registrations).
6. **Fresh-interpreter probes**: `subprocess.run` (never `Popen`+`PIPE` — the ~100-line
   `TradingNode` banner deadlocks it); the `timeout` argument is itself the assertion.
7. **No `freezegun`** (inject time); **`capture_logs()` is banned** near `session_id` context.
8. **The auto-linter**: F401 is unfixable-but-blocking; make dependent changes in a single edit;
   the commit gate inspects staged files only.
9. **Import guards**: any new import in a `live_*` module must clear
   `test_live_dependency_invariance.py` (declared deps only) and
   `test_epic1_ac_node.py`'s `_STDLIB_AND_FIRST_PARTY` (add by hand with reason — every prior live
   story hit this once).
10. **Size caps** measured on executable statements; budget the split BEFORE the edit; disclose
    overages in the Dev Agent Record (`live_session_runner.py` at 833 raw lines is sanctioned; the
    new module must stay under the cap).

### Testing standards summary

- Tiers: predicate logic → unit (`live_connection_monitor` is framework-free); wrap/observer with
  stub monitor + real Nautilus events → component; anything constructing a real
  `Trader`/`BacktestEngine` → integration `--forked`; the fill → operator procedure (NFR33).
- Markers on every test; TDD Red first with the RED evidence recorded (3.1's pattern).
- The wiring-pin discipline is non-negotiable (Task 5.2): every "the runner does X" claim needs a
  test that constructs a `LiveSessionRunner` and goes red when the call site is deleted.
- Known residual to state, not hide: no test composes the runner with a REAL `TradingNode` (3.1's
  recorded limit — component drives the `TestLiveNode` double; the seam is verified by reading
  installed source). AC #7's live run is the only evidence at that seam; say so in the story record.
- Baselines at story creation: unit 2397 · component 1305/16 skipped · integration 281/2 skipped
  (DB tier's 85 inside it) · e2e 1 · Epic 1 sweep 40/40.

### Project Structure Notes

- New: `src/core/live_order_path.py` (suppression wrap + order-event observer + latency; one module
  keeps guard-list rot surface to one entry per hand-maintained list),
  `tests/component/core/test_live_order_path.py`, `tests/component/core/test_session_runner_order_path.py`,
  `tests/component/core/test_client_order_id_determinism.py` (Task 6 pins).
- Modified: `src/core/live_connection_monitor.py` (new derived predicate),
  `src/core/live_session_runner.py` (wiring only), `src/models/session.py` (Task 6.4 validator),
  the existing `SessionSpec` unit suite under `tests/unit/models/` (Task 6.4 tests),
  `tests/unit/core/test_live_connection_monitor.py`, guard-list test files (Tasks 7.1/7.2/7.6),
  `tests/component/core/test_session_runner_phases.py` (Task 5.1 timeline pins, extended not weakened),
  CLAUDE.md (membership-pinned list entry), `docs/qa/phase3-live-verification.md`,
  `deferred-work.md`, `sprint-status.yaml`.
- NOT modified: anything under `src/core/strategies/` (AC #1's evidence contract),
  `src/core/live_session_steady_state.py` (Task 2.3's runner-only decision), `src/cli/**`
  (no CLI surface change), `src/db/**`, `src/services/**`, `alembic/**`, AR44's untouched list.
- Naming: module/event names must clear AR36 (`gate` is reserved for the safety gate — hence
  "order path", not "order gate"; `guard` is 2.7's strategy guard). Event names live in the
  sanctioned `order.*` namespace.
- Commit shape: one `feat(live):` commit carrying src + all test tiers + BMAD artifacts + docs/qa
  together (the `90337eb`/3.1 precedent); subject is an operator-outcome sentence; no AI references.

### References

- Story + retro block: `_bmad-output/planning-artifacts/epics.md:1178-1243`; epic context `:385-439`
- Requirements: FR23/FR27/FR30 `epics.md:65-72`; FR6 `:39`; NFR1 `:113`; NFR6 `:121`; NFR10 `:125`;
  NFR32/33 `:162-163`; AR23-AR26 `:211-214`; AR38 `:238`; AR40 `:240`; AR41 `:241-243`; AR43 `:245`
- Architecture: D7 order-path policy `architecture.md:286-296`; one-engine constraint `:93-97`;
  live data flow `:606-612`; event naming `:432-440`; testing split `:413-416, :470-472`;
  boundaries `:567-582`
- PRD: same-code-path rationale `prd.md:99-112, :188-195, :654-678`; execution-correctness domain
  rules `:431-450`; NFR1 origin `:923-925`; machinery-complete gate `:170-178, :228-229`
- Groundwork: commit `90337eb`; `src/core/live_node_builder.py:334-380`;
  `tests/component/core/test_live_node_builder.py:652, :1413, :1614`
- Previous story: `_bmad-output/implementation-artifacts/3-1-stop-trading-without-manufacturing-an-exit.md`
  (esp. the AC #6 clause matrix `:355-387` and review findings `:323-416`)
- Trading permission: `src/core/live_connection_monitor.py:176-186`;
  `src/core/live_session_steady_state.py:345-348`; `src/core/live_connection_probe.py:73-85`;
  Story 1.6 contract `epics.md:688-721`
- trader_id: `src/core/live_trader_id.py`; generator `nautilus_trader/common/generators.pyx:117-153`;
  auto-tag `nautilus_trader/trading/trader.py:406-419`; counter restore `trading/strategy.pyx:353-376`
- Guard lists: `tests/unit/core/test_live_node_never_exits.py:42-82`;
  `tests/unit/core/test_live_stop_path_is_inert.py:38-116, :190-370`;
  `tests/component/core/test_session_runner_phases.py:1197-1245`;
  `tests/component/core/test_live_dependency_invariance.py:39-58`
- Live procedure: `scripts/diagnostics/run_p7_position.sh`;
  `docs/qa/phase3-live-verification.md:655-800`; compose hazard `docker-compose.yml:51`;
  deferred-work items `deferred-work.md:1963-2106`

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

**Task 1.1 — instance-level wrap reliability (measured against installed nautilus-trader 1.220.0).**
Fresh-interpreter probes (`subprocess`-free, run directly via `PYTHONPATH=. uv run python`,
scratch files not committed):

- Measurement A: wrapped `strategy.submit_order`/`strategy.close_position` at the instance level
  on a real materialised `SMACrossover`. Drove a real bullish-then-bearish crossover sequence
  through `on_bar`. Result: `submit_order` wrapper fired twice (open long, open short),
  `close_position` wrapper fired once (closing the long ahead of the short) — the strategy's own
  Python call sites (`self.submit_order(...)` / `self.close_position(...)` in
  `_generate_buy_signal`/`_generate_sell_signal`) are reliably intercepted by instance-level
  attribute assignment, matching `LiveStrategyGuard.wrap`'s existing mechanism for
  `handle_bar`/`handle_event`.
- Measurement B: the residual named in Dev Notes ("Nautilus-internal C-level dispatch bypasses an
  instance attribute") does **not** hold for `close_position → submit_order` specifically. Wrapped
  **only** `submit_order` (left `close_position` unwrapped), built a genuinely open `Position` by
  hand (real `Order` lifecycle: `INITIALIZED → SUBMITTED → ACCEPTED → FILLED`, then
  `Position(instrument, fill)`), and called Nautilus's real `close_position(position)` directly
  (unwrapped by us). `close_position`'s own body (`strategy.pyx:1303`,
  `self.submit_order(order, position_id=position.id, ...)`) **did** reach our wrapped
  `submit_order` — Cython's `self.method()` self-call for a `cpdef` method on a Python subclass
  instance honours a Python-level instance `__dict__` override rather than bypassing it via a
  direct vtable/C dispatch. Correction to the Dev Notes' pre-measurement hypothesis, recorded here
  rather than silently. This does **not** change the mechanism (Task 3.2 still wraps
  `close_position` as its own named entry — required regardless, both for defence in depth and
  because suppression must stop the *outer* call, not merely hope the inner one aliases into an
  already-wrapped sibling) and does not change the Primary/fallback decision: the wrap is reliable,
  so the RiskEngine fallback is **not** needed.
- Non-vacuity: both `strategy.submit_order is wrapped_submit` and
  `strategy.close_position is wrapped_close` were asserted `True` immediately after assignment in
  every run (the same defensive check `StrategyGuard.wrap` already performs at
  `live_strategy_guard.py:333-337`).

**Task 1.2 — order-event topic shape (measured against installed nautilus-trader 1.220.0).**
`Strategy.register()` subscribes `self.handle_event` to `events.order.{strategy_id}` and
`events.position.{strategy_id}` (`trading/strategy.pyx:314-315`, confirmed by direct read). A real
`MessageBus.subscribe(topic="events.order*", handler=...)` (construction alone does not touch C
logging, per the existing measured precedent in `test_session_runner_phases.py`) followed by
publishing a real `OrderSubmitted` (built the same way `ExecutionClient.generate_order_submitted`
does at `execution/client.pyx:359-368`) on `events.order.{strategy_id}` delivered exactly one event
to a plain handler. Fields carried: `client_order_id`, `instrument_id`, `strategy_id`,
`account_id`, `event_id`, `ts_event`, `ts_init` — confirming `client_order_id`/`instrument_id`/
`ts_init` are all present for the latency and log-field work in Tasks 3-4. `OrderSubmitted` is
generated by the **execution client** (the adapter), not by `Strategy.submit_order` itself, which
only publishes the order's `init_event_c()` (`OrderInitialized`) synchronously
(`strategy.pyx:799-803`) before routing to the risk engine — so the observer built in Task 4 must
filter on event *type*, not merely subscribe to the topic, since `OrderInitialized` arrives on the
same topic first.

**Decision from Task 1**: build the Primary mechanism (instance-level wrapping, installed by the
runner between `materialise_strategy` and `add_strategy`) exactly as Dev Notes specifies. No
fallback needed.

**Task 5.3 — line budget disclosure.** `live_session_runner.py`: 833 → 871 raw lines (+38), 284 →
298 executable statements measured by `ast.stmt` node count (+14, within the "~10-20 raw lines"
estimate once docstring/comment lines are excluded). The delta is: one new import line, one new
`self._order_observer` field declaration, `_observe_connection_once()` (a new ~15-line method,
mostly docstring), three new statements in `_phase_subscribe` (observer construction + two
subscribes + the synchronous observe call already counted under Task 2), and two new statements in
`_start_strategy` (`assert self._monitor is not None` + `install_order_path(...)`). Not split —
Anti-Patterns' guard-list-rot warning applies, and this module is already on `NODE_FACING_MODULES`,
`STOP_PATH_MODULES`, `TestImportPurity.MODULES` and `LIVE_MODULE_GLOBS`; a split would silently
re-open the exact hazard Story 2.6 caused.

**Task 6 — corrected measurement.** The Dev Notes' "UUID/hyphen flags are per-StrategyConfig, unset
anywhere in src/" is right about non-use but wrong to imply both flags default `False`: measured
directly against `nautilus_trader.trading.config.StrategyConfig()`,
`use_uuid_client_order_ids` defaults `False` (the NFR6-hostile one — correct to stay off) but
`use_hyphens_in_client_order_ids` defaults **`True`** — which is *wanted*, since the real observed
live client order ID (`O-20260828-193805-621bb88c-000-4`) already has hyphens. The canary test
(`TestUuidAndHyphenFlagsAreUnused.test_the_defaults_this_repo_relies_on_have_not_drifted`) asserts
the true measured values, not the pre-measurement assumption.

**Task 8 — live verification checked, not run.** At the point Tasks 1-7 completed, the wall-clock
date is Sunday 2026-08-30, 11:30 EDT — a weekend, so Task 8.1(a)'s "inside RTH" precondition cannot
be satisfied today regardless of Gateway or Redis state (checked and both were reachable on
`127.0.0.1:4002`/`127.0.0.1:6379`, and `docker ps` failed to reach a daemon — consistent with the
listening Gateway being the required bare, non-compose one rather than the forbidden
`READ_ONLY_API=yes` compose one, though this was not independently confirmed since RTH already
gates the attempt). Per Task 8.4 and the Epic 2 retro's standing rule, this does **not** block the
automated tiers from gating the story into review: AC #7 is the explicit remaining item, and the
story must not be marked **done** until a fill is recorded inside a real RTH session. Re-run Task 8
on the next trading day with no competing IBKR login (mobile app or client portal).

**Task 9.1 — mutation sweep, one per AC minimum, break→observe-red→revert.** Every mutation was
applied to the real source, the predicted test(s) confirmed RED, then reverted (`git diff --stat`
confirmed byte-identical after each revert).

| # | AC | Mutation | Killed by |
|---|----|----------|-----------|
| M1 | #4 | Inverted the suppression predicate in `_wrap` (`if not withheld: return None`) | 3 tests in `test_live_order_path.py::TestSuppressionWhenWithheld` |
| M2 | #4 | Deleted the runner's `install_order_path(...)` call site | `test_session_runner_order_path.py::TestTheRunnerReachesTheOrderPathCallSites::test_the_runner_calls_install_order_path_before_add_strategy` (only that one — nothing else relied on) |
| M3 | #6 | Dropped `client_order_id` from the `order.submitted` fields | `test_live_order_path.py::TestOrderEventObserver::test_order_submitted_with_a_known_bar_logs_the_latency` |
| M4 | #5 | Anchored latency on handler-entry `time.time_ns()` instead of `bar.ts_event` | Same test — latency assertion off by ~1.8 trillion ms |
| M5 | #2 | Added `use_uuid_client_order_ids: bool = True` to `SMAConfig` | `test_client_order_id_determinism.py::TestUuidAndHyphenFlagsAreUnused::test_neither_flag_is_referenced_anywhere_in_src` |
| M6 | #2 | `derive_trader_id` returns `f"{PREFIX}-{session_id}"` (full `str(uuid)`, hyphens included) instead of `.hex[:8]` | 5 tests in the **pre-existing** `tests/unit/core/test_live_trader_id.py` (determinism, format, no-hyphen, lowercase-hex) — proves Story 2.4's own guard still catches this, not just this story's new tests |
| M7 | #6 | Deleted the runner's `ORDER_EVENTS_TOPIC` subscribe call site | 3 tests: the Task 5.2 wiring pin, plus 2 in `test_session_runner_phases.py` (topics list, subscribe-count-before-add_strategy) |
| M8 | #1 | Planted `self.close_all_positions(self.instrument_id)` in `sma_crossover.on_stop()` | `test_live_stop_path_is_inert.py::TestStrategyLifecycleHooksAreInert` (Story 3.1's existing scan — proves AC #1's zero-diff contract is independently checkable, not just asserted) |
| M9 | #3 | `shares = max(int(raw_qty) + 1, 1)` in `_calculate_position_size`'s whole-share branch | 6 tests, all pre-existing, in `tests/unit/strategies/test_sma_crossover_position_sizing.py` — no new pin needed, one already existed |

AC #7 exempt by design (NFR33 operator-procedure evidence, not a CI assertion).

**Task 9.2 — clause matrix**, decomposing each AC into distinct obligations and naming the test that
guards each (Story 3.1's method):

- **AC #1** (a) zero diffs under `src/core/strategies/` — verified directly: `git diff --stat
  src/core/strategies/` is empty at closeout. (b) unchanged behaviour when healthy —
  `test_live_order_path.py::test_a_signal_reaches_the_exec_layer_unchanged_when_healthy`. (c) no
  `is_live` branch or forbidden lifecycle-hook call — `TestStrategyLifecycleHooksAreInert` (M8).
- **AC #2** (a) format — `TestClientOrderIdFormat`. (b) positional tag assignment + stability across
  a simulated restart — `TestOrderIdTagPositionalAssignment` (both tests). (c) UUID/hyphen flags
  unused — `TestUuidAndHyphenFlagsAreUnused` (M5). (d) `derive_trader_id` itself hyphen-free —
  `test_live_trader_id.py` (M6). (e) create-time collision refusal — `TestOrderIdTagCollision`.
- **AC #3** (a) whole-share sizing unchanged — `test_sma_crossover_position_sizing.py` (M9). (b)
  rejection tolerance — `TestOrderEventObserver::test_an_order_rejected_is_contained_without_crashing_or_logging`.
- **AC #4** (a) predicate correctness — `TestSubmissionWithheld` (unit, **8** tests at
  implementation — the "9" recorded here was wrong, corrected by the 2026-08-30 review, which then
  added 2 more for the production-reachable disconnect path, so 10 today). (b) wrapper actually suppresses — `TestSuppressionWhenWithheld` (M1). (c) runner
  wires the wrap before `add_strategy` — `TestTheRunnerReachesTheOrderPathCallSites` (M2). (d) the
  first-tick `AWAITING_CONNECTION` window is closed — `TestConnectionObservedBeforeTrading` (4 tests,
  incl. the failed-read and disconnected-read fail-closed cases).
- **AC #5** (a) latency computed from `bar.ts_event` when known — `test_order_submitted_with_a_known_bar_logs_the_latency`
  (M4). (b) absent, never fabricated, when unknown — `test_order_submitted_with_no_known_bar_omits_the_latency_field`.
  (c) the <1s bound itself — AC #7's live evidence (NFR33, not CI).
- **AC #6** (a) `client_order_id` present — M3. (b) `instrument_id`/`session_id` present — same test
  class's un-mutated assertions. (c) runner wires the observer subscription — M7.
- **AC #7** — exempt, NFR33 operator-procedure evidence; outstanding (Task 8, above).

One test per AC is a coverage claim, not a proof — every AC above has 2+ independently-failing
guards, and three (AC #1, #2, #6) are guarded partly by suites this story did not write
(`test_live_stop_path_is_inert.py`, `test_live_trader_id.py`), which is what M6 and M8 exist to
demonstrate: this story's new behaviour is caught by the repo's *existing* discipline, not only by
tests invented alongside the code they test.

**Task 9.3 — full gates.**

```
make format   -> 491 files left unchanged
make lint     -> All checks passed
make typecheck -> Success: no issues found in 104 source files
unit           -> 2417 passed (baseline 2397, +20)
component       -> 1334 passed, 16 skipped (baseline 1305/16, +29)
integration --forked -> 281 passed, 2 skipped (baseline 281/2, unchanged)
e2e             -> 1 passed (unchanged)
Epic 1 acceptance sweep -> 40/40 PASS  # WRONG: measured 43/43 on 2026-08-30 by the review,
                                       # against an untouched tests/integration/core/test_epic1_ac_*.py
```

Zero regressions across every tier. `git diff --stat src/core/strategies/` empty (AC #1's evidence
contract, re-verified after the full sweep).

### Completion Notes List

- Task 1: measured (not assumed) that instance-level wrapping is a reliable mechanism for
  `submit_order`/`close_position`, including the surprising case that Cython's `cpdef` self-calls
  inside `close_position` DO honour a Python instance override — corrected the Dev Notes'
  pre-measurement hypothesis. Confirmed the order-event topic shape and that `OrderInitialized`
  arrives on the same topic before `OrderSubmitted`.
- Task 2: added `ConnectionMonitor.submission_withheld` (unit, TDD, 9 new tests) and wired one
  synchronous `observe()` call in the runner's `_phase_subscribe` so `AWAITING_CONNECTION` never
  overlaps the `trading` phase.
- Tasks 3-4: new `src/core/live_order_path.py` — `install_order_path()` (suppression wrap over the
  four order-creating strategy methods) and `OrderEventObserver` (latency-anchored `order.submitted`
  logging, silent on every event type but `OrderSubmitted`). 14 component tests, including the
  anti-tautology twin and full exception-containment coverage for both handlers.
- Task 5: wired both into `LiveSessionRunner` — the wrap at strategy materialisation (before
  `add_strategy`), the observer's two subscriptions in `_phase_subscribe`. New wiring-pin file
  (`test_session_runner_order_path.py`, **7** tests at implementation — the "11" recorded here was
  wrong, corrected by the 2026-08-30 review, which then added 4 more, so 11 today) constructs a real
  `LiveSessionRunner` throughout, per Story 3.1's review lesson. Extended two existing
  `test_session_runner_phases.py` tests (subscription topics/order, timeline) rather than leaving
  them false. `live_session_runner.py`: 833 → 871 raw lines / 284 → 298 executable statements.
- Task 6: new `test_client_order_id_determinism.py` (7 tests, real `Trader` + engines, measured not
  to touch C logging) plus a new `SessionSpec._reject_order_id_tag_collision` validator (4 new unit
  tests) closing a 2026-08-23 deferred-work.md item. Corrected a second pre-measurement assumption:
  `use_hyphens_in_client_order_ids` defaults `True`, not `False`.
- Task 7: `live_order_path.py` added to `NODE_FACING_MODULES` and `TestImportPurity.MODULES`;
  deliberately kept out of `STOP_PATH_MODULES` and `NEW_OR_MODIFIED_FOR_STOP`, both reasons recorded
  inline; `LIVE_MODULE_GLOBS` already covered it. CLAUDE.md's membership-pinned-lists entry extended.
- Task 8: preconditions checked, not run — Sunday, market closed. AC #7 is the one open item; the
  story is not done until it runs on the next trading day (Task 8.4's sanctioned path).
- Task 9: nine mutations, one per AC minimum, all killed on the first attempt (table above) — three
  (M6, M8, M9) killed by suites this story did not write, demonstrating the new behaviour is caught
  by the repo's existing discipline too. Full gates clean, zero regressions, `src/core/strategies/`
  diff empty.

### File List

**New:**
- `src/core/live_order_path.py`
- `tests/component/core/test_live_order_path.py`
- `tests/component/core/test_session_runner_order_path.py`
- `tests/component/core/test_client_order_id_determinism.py`

**Modified:**
- `src/core/live_connection_monitor.py` — `submission_withheld` property
- `src/core/live_session_runner.py` — order-path wiring (Tasks 2.3, 5)
- `src/models/session.py` — `_reject_order_id_tag_collision` validator + `_resolve_order_id_tag`
- `tests/unit/core/test_live_connection_monitor.py` — `TestSubmissionWithheld`
- `tests/unit/core/test_live_node_never_exits.py` — `NODE_FACING_MODULES` entry
- `tests/unit/core/test_live_stop_path_is_inert.py` — `STOP_PATH_MODULES`/`NEW_OR_MODIFIED_FOR_STOP` exclusion comments
- `tests/unit/models/test_session_spec.py` — `TestOrderIdTagCollision`
- `tests/component/core/test_session_runner_phases.py` — extended subscription/timeline pins, `TestImportPurity.MODULES` entry
- `CLAUDE.md` — membership-pinned-lists entry for `ORDER_CREATING_METHODS`
- `_bmad-output/implementation-artifacts/deferred-work.md` — order_id_tag disposition (resolved)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status
- `_bmad-output/implementation-artifacts/3-2-submit-a-strategys-orders-to-the-broker.md` — this story file

## Change Log

| Date | Change |
|---|---|
| 2026-08-30 | Story 3.2 implemented: `ready-for-dev` → `in-progress` → `review`, same session as creation. Tasks 1-7 and 9 complete; Task 8 (the live fill, AC #7) blocked on the calendar — a Sunday, market closed, checked and disclosed rather than skipped silently. New `src/core/live_order_path.py` (`install_order_path` suppression wrap + `OrderEventObserver`), a new `ConnectionMonitor.submission_withheld` predicate, full runner wiring, a new create-time `order_id_tag` collision validator closing a standing deferred-work.md item, and two corrected pre-measurement assumptions (Cython `cpdef` self-calls DO honour a Python instance override; `use_hyphens_in_client_order_ids` defaults `True`, not `False`). Nine mutations (one per AC minimum), all killed on the first attempt — three by suites this story did not write. Unit 2397→2417, component 1305→1334/16 skipped, integration 281/2 skipped unchanged, e2e 1 unchanged, Epic 1 sweep 40/40, format/lint/typecheck clean, `src/core/strategies/` diff empty. Story is **not** marked done: AC #7 must record a real fill before it can be. |
