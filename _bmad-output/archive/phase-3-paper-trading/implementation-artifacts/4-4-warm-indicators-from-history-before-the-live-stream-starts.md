# Story 4.4: Warm Indicators from History Before the Live Stream Starts

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want every strategy's indicators primed with historical bars before the first live bar arrives,
so that a restarted session does not trade on cold indicators or sit silent for a week waiting for
them to fill.

## Why this story is shaped the way it is

Read this section before designing anything. The epic's text makes the story sound like a two-line
edit to two `on_start()` methods. It is not, for three reasons measured at drafting (2026-09-22,
against the installed `nautilus-trader 1.220.0` wheel and head `c4c6afa`).

**1. AR39 and AR40 point at different places, and both are binding.** AR39 fixes the runner's
phase order — `… → reconcile → warmup → subscribe → trading` — and forbids reordering. AR40 says
warm-up lives in each strategy's own `on_start()`, through mode-agnostic Nautilus APIs only. But
strategies are *started* in the `trading` phase (`_start_strategy`,
`src/core/live_session_runner.py:699-736`), so the strategy-owned warm-up necessarily runs
**after** the runner's `warmup` phase has already logged `ok`. The only reading that honours both
is: the `warmup` phase **arms** the runner's warm-up instrumentation (D-B); the warming itself
happens inside each strategy's `on_start()` during `trading`, and `trading` does not declare
`session.started` until every started strategy's warm-up has settled. The AR39 sequence and its
eight names are unchanged. Disclose this in the phase's docstring; do not "fix" it by starting
strategies earlier.

**2. The AC's own mandate creates a new silent failure — the exact one the story title forbids.**
AC #1 requires `subscribe_bars` to be invoked from the `request_bars` history callback. Today
`sma_crossover.on_start()` subscribes unconditionally. After this story, **a strategy whose history
callback never fires never subscribes, and sits silent for the whole process run.** Measured in the
wheel, the IB adapter completes a request *without ever sending a `DataResponse`* — so the callback
never fires — on every one of these paths (F2): the contract is not in the data client's provider;
the bar type is not time-aggregated; the history came back empty; the request timed out
(`_await_request(..., default_value=[])`); or an identical `(bar_type, end-second)` request was
already in flight (`"Request already exist"` → `[]`). The last one is reachable by an ordinary
spec: two strategies on the same bar type, started back to back, issue two requests whose `end`
falls in the same second (`SessionSpec` allows this — `src/models/session.py:452-476`). The story is
therefore mostly about **bounding and surfacing** that failure (D-B, D-D), not about the two
`on_start()` edits.

**3. In a backtest, `request_bars` returns nothing, synchronously — so backtests do not warm at
all.** `BacktestDataClient.request_bars` is a no-op (`backtest/data_client.pyx:544-546`), and the
`DataEngine` answers a request with no usable client and no registered catalog with an immediate
empty `DataResponse` (`data/engine.pyx:1441-1442, 1486-1499`). **Probed**: the callback fires
*inside* `request_bars`, before `on_start()` returns, with the indicator still cold; every backtest
bar is then delivered exactly as today. No production backtest registers a catalog with its
`DataEngine` (`grep -rn register_catalog src/` is empty). So AC #3's "difference explained solely
by warm indicators" is, for today's backtests, **no difference at all** — the parity test must
assert *identical* fills, not "explainable" ones (Task 5).

**And one latent defect the rewrite cannot avoid.** `sma_momentum`'s moving average is not a moving
average (F8): `_update_ma` subtracts the evicted value only when `len(q) > maxlen`, which a
`deque(maxlen=…)` can never satisfy, so both "averages" are the cumulative sum of every close
divided by their period. Both deques receive the same closes, so `fast = S/20 > S/50 = slow`
permanently once filled: **`momentum` has never been able to emit a crossover, in any backtest or
session.** AC #1 requires it to register real indicators, which fixes this — and changes its
backtest output, which AC #3's letter forbids. That conflict is **Decision D-G, ruled by the PO**
(see below).

### Measured facts — cite, don't re-derive

Every fact marked *probed* was run in a fresh interpreter at drafting (`/tmp/p44/*.py`, not
committed). Task 1 re-measures the rest before production code is written.

| # | Fact | Where |
|---|---|---|
| F1 | Backtest: `request_bars` → immediate empty `DataResponse` → `_handle_bars_response` → `handle_bars([])` logs `Received <Bar[0]> data for unknown bar type` and returns → `_finish_response` pops the id **then** calls the callback — all inside `request_bars`. *Probed*: log order `callback sma_init=False count=0` → `on_start:after_request pending=False`; all 10 bars reached `on_bar`; first bar saw `count=1`. | `data/engine.pyx:1438-1514`, `common/actor.pyx:3968-3989` |
| F2 | Live IB `_request_bars` never sends a `DataResponse` when: contract missing (`:509-511`, log + return); non-time bars (`:513-517`); empty or timed-out history (`:536-546` — publishes `{"status": "Failed"}` on `requests.{id}` only). `get_historical_bars` dedups on `(bar_type, end_date_time_str)` at **second** resolution and answers a duplicate with `[]` (`client/market_data.py:536-538, 567-569`); a timeout resolves `[]` (`:566`). In every case the actor's callback **never fires**. | `adapters/interactive_brokers/data.py:506-552` |
| F3 | Duration string: `timedelta_to_duration_str` sends `< 1 day` as **seconds** (`max(30, s) S`), `>= 7` days as weeks, `>= 30` as months, `>= 365` as years, each via `:.0f` — which **rounds**, so 74 days becomes `"2 M"` (60 days, *short*). A seconds duration ending pre-open (e.g. `1200 S` at 09:25 ET with `useRTH=1`) covers no RTH bar → empty → F2 → silent. | `adapters/interactive_brokers/parsing/data.py:91-101` |
| F4 | `_check_bounds` trims a response to `[start, end]` by `ts_init`: a trailing bar with `ts_init > end` (the in-progress bar IB includes in a history ending "now") is dropped; leading bars before `start` are dropped **only if** some bar is `>= start` (if none is, nothing is trimmed — quirk). | `data/engine.pyx:2020-2044` |
| F5 | `handle_bars` (history) feeds registered indicators, then `handle_historical_data` → `on_historical_data` — **never `on_bar`**. `handle_bar` (live) feeds registered indicators **before** the `RUNNING` gate and **outside** its `try`, then calls `on_bar`. | `common/actor.pyx:3719-3798` |
| F6 | Any raise on the response path — the strategy's own history callback, or a registered indicator raising on a historical bar — escapes into `LiveDataEngine._run_res_queue` → `_handle_queue_exception("DataResponse")`, which with Story 2.7's `graceful_shutdown_on_exception=True` **shuts the whole node down**. `GUARDED_HANDLERS = ("handle_bar", "handle_event")` does not cover it. | `live/data_engine.py:347-365, 449-471`; `src/core/live_strategy_guard.py:115` |
| F7 | `register_indicator_for_bars` keys on `bar_type.standard()`; registering the same indicator twice logs an **error** (does not raise). `Actor` exposes `registered_indicators`, `indicators_initialized()`, `has_pending_requests()`, `is_pending_request(id)`. *Probed.* | `common/actor.pyx:799-827` |
| F8 | `sma_momentum._update_ma` is defective. *Probed* with `maxlen=3` over closes 1..6: `2.0, 3.33, 5.0, 7.0` against the true `2, 3, 4, 5`. Consequence: `momentum` never crosses. | `src/core/strategies/sma_momentum.py:75-83` |
| F9 | `sma_crossover.on_bar` feeds its own SMAs (`self.fast_sma.handle_bar(bar)`, `:100-101`). Once registered, `handle_bar` feeds them too — leaving those two lines in **double-counts every bar**. Removing them preserves order exactly: F5 updates registered indicators before `on_bar`, as the manual calls do today. | `src/core/strategies/sma_crossover.py:87-117` |
| F10 | History reaches `on_historical_data`, not `on_bar` (F5), so `_prev_fast_sma`/`_prev_slow_sma` stay `None` after warm-up: the strategy is warm but **deaf for one bar**, and on daily bars loses a last-history → first-live crossover outright. | `sma_crossover.py:111-117`, `sma_momentum.py:109-112` |
| F11 | Live requests are asynchronous: `LiveDataEngine.request` enqueues (`live/data_engine.py:292-306`), `LiveMarketDataClient.request_bars` spawns a task (`live/data_client.py:858-864`). The callback runs later on the loop. `_phase_trading` does not run the loop today. | runner `:675-697` |
| F12 | *Probed*: an instance-level `setattr(strategy, "request_bars", wrapper)` made before `add_strategy` **is** honoured when the strategy's Python `on_start` calls `self.request_bars(...)` (the `install_order_path` precedent, `src/core/live_order_path.py:239-287`). In a synchronous engine the (wrapped) callback runs **before** the base call returns its request id — bookkeeping keyed on the returned id would miss it. | `/tmp/p44/probe_wrap.py` |
| F13 | *Probed*: `strategy.fault()` from `RUNNING` → `FAULTED` (legal). `_start_strategy`'s existing `except` → `record_start_failure` + `_fault_quietly` therefore contains a strategy that started and then failed warm-up, with **no guard edit**. | runner `:733-769` |
| F14 | *Probed*: a Python `Indicator` subclass whose `handle_bar` raises is dispatched by `_handle_indicators_for_bar` and the raise propagates out of `Actor.handle_bar` — i.e. the guard's instance-level `handle_bar` wrapper (which encloses the whole base call) **is** what contains it. This is the Story 4.4 debt item (`deferred-work.md:1852-1856`, `:2160`). | `common/actor.pyx:3735-3748, 4003-4007` |
| F15 | `request_bars` requires registration (`Condition.is_true(self.trader_id is not None, ...)`, `actor.pyx:3142`), so any test calling `on_start()` on an unregistered strategy now raises. `DataEngine.register_catalog(catalog, name)` exists (`data/engine.pyx:339-353`) — the way to make a **backtest** warm, for AC #3's equivalence proof only. | |

**Budgets (measured with `tests/unit/governance/test_size_caps.py::measure_module`):**
`live_session_runner.py` **484 of a hard 500** executable statements (not on the file allowlist —
16 statements of headroom, total); `LiveSessionRunner` 400 (baseline); `SMACrossover` 103
(baseline); `SMAMomentum.on_bar` 58 (baseline); `StrategyGuard` 121 (baseline). The baseline is
**exact**, not a ceiling in practice: `test_regenerating_the_baseline_reproduces_it_exactly` asserts
equality, so every touched baselined subject gets its entry updated in the same commit — lowered,
removed if at/under cap, or raised **with a one-line reason** (CLAUDE.md's disclose-and-record step).

**Pre-existing, environmental, not this story's:** the harness worktree has the
`src/core/strategies/custom/` submodule **unpopulated**, so two size-cap tests fail before any edit
(`ApoloRSI.on_bar` and `BollingerReversalStrategy` baseline entries read "renamed or deleted").
Do **not** remove those entries — they are correct in a checkout with the submodule. Record it.

### Design decisions (disclosed here, not discovered in review)

- **D-A — Warm-up is strategy-owned and inline.** Each built-in strategy's `on_start()` calls
  `register_indicator_for_bars` for each indicator, then `request_bars(bar_type, start=…,
  callback=self._on_history_loaded)`; the callback records the crossover baseline (D-F) and then —
  last — calls `subscribe_bars(bar_type)`. The three Nautilus calls stay **visible in each strategy
  file** so AC #2's inspection is an inspection, not a trace through a helper. The only shared code
  is a pure, framework-free lookback function (D-H) in a new `src/core/strategy_warmup.py`
  (deliberately **not** a `live_*` module: a strategy importing a `live_*` name would read as
  live-coupling, and `LIVE_MODULE_GLOBS` would start scanning it).
- **D-B — The runner instruments `request_bars`; it does not warm anything itself.** New module
  `src/core/live_session_warmup.py` holds a `WarmupWatch`, constructed in `_phase_warmup` (the
  phase's new, honest body: arm the watch — the phase still touches **nothing on the node**, so
  `TestTheEpicFourPlaceholders.test_they_make_no_call_on_the_node` keeps passing for `warmup`). In
  `_start_strategy`, **after** `guard.wrap` and `install_order_path` and **before**
  `add_strategy` (the same window, F12), `watch.instrument(strategy, spec_strategy_id=…)` replaces
  the instance's `request_bars` with a wrapper that wraps the `callback` argument. After
  `start_strategy`, the runner runs the loop until that strategy's warm-up settles
  (`loop.run_until_complete(watch.settle(...))`, the `_phase_node_connect` precedent) — **one
  strategy at a time**, so at most one warm-up request is ever in flight (NFR15, and F2's
  same-second dedup cannot occur). The wrapper is strategy-agnostic: it works for any strategy that
  calls `request_bars` in `on_start`, and a strategy that issues none is logged `warmup.skipped` and
  started cold, exactly as today. Requests issued after a strategy's warm-up settled pass through
  the wrapper untouched (a mid-session `request_bars` is not a warm-up).
- **D-C — `warmup.completed` is structurally before the first live bar.** The wrapped callback
  logs `warmup.completed` **synchronously, before calling the strategy's own callback** — and it is
  the strategy's callback that calls `subscribe_bars`. No live bar can be delivered to a strategy
  before it subscribes, so the ordering AC #4 requires is a property of the call stack, not of a
  race. One record per warm-up request, carrying `strategy_id`, `bar_type`, `requested_from`
  (ISO start), `elapsed_ms`, `indicators_initialized` and `not_initialized` (the `repr` of each
  registered indicator still cold). When `indicators_initialized` is false (history came back
  shorter than the indicators need) the same event is emitted at **WARNING** — the strategy still
  subscribes and still refuses to signal until its own `initialized` checks pass, which is today's
  behaviour, now loud.
- **D-D — A warm-up that does not settle is bounded, loud, and contained.** *[RULED by the PO,
  2026-09-22: option A — bounded wait, then contain; not "fail the whole start", not "fall back to
  cold".]* Deadline = `settings.ibkr_request_timeout` (default 60) +
  `WARMUP_DEADLINE_MARGIN_SECONDS = 15`, strictly after the adapter's own timeout so the watch
  never abandons a request the adapter is still waiting on. On expiry: one `warmup.failed` ERROR
  (`strategy_id`, `bar_type`, `waited_seconds`, `reason="no_response"`), then `settle()` raises
  `WarmupFailedError` inside `_start_strategy`'s existing `try`, whose `except` already records the
  failure through `StrategyGuard.record_start_failure` and `fault()`s the strategy (F13) — so the
  strategy never trades this run, `live status` reads it through Story 2.7's contained-failure
  surface, and if **no** strategy survives the existing `NoStrategyStartedError` fails the start
  (exit 1). A history response that arrives after abandonment is logged `warmup.discarded` and the
  strategy's own callback is **not** run (no late subscribe into a faulted strategy).
- **D-E — The strategy's own history callback is contained.** If it raises, the wrapper catches it
  (never re-raises into F6's node-wide shutdown), logs `warmup.failed reason="callback_raised"
  error_type=…`, and marks the warm-up failed so D-D's containment path runs. A registered indicator
  raising on a **historical** bar (inside `handle_bars`, before the callback) is **not** contained —
  it is F6's node-wide graceful shutdown, disclosed in the module docstring and routed to
  `deferred-work.md`; `GUARDED_HANDLERS` stays exactly `("handle_bar", "handle_event")` (its pin is
  exact, and widening it needs its own dispatch measurement — `deferred-work.md:1904-1914`).
- **D-F — The crossover baseline carries over from history.** The history callback sets the
  strategy's previous-value pair from the now-warm indicators (only if both are initialised) and
  never evaluates a signal: a crossover *inside* history never trades. The effect is that a warm
  live strategy behaves exactly as a backtest that had run continuously through the history — which
  is the equivalence Task 5.3 proves. In backtests the callback sees cold indicators (F1) and sets
  nothing, so backtest behaviour is untouched.
- **D-G — `sma_momentum`'s indicators.** *[RULED by the PO, 2026-09-22: option A — convert to
  registered indicators and accept the disclosed AC #3 exception.]* Replace
  the deques and `_update_ma` with two registered `SimpleMovingAverage(period, PriceType.LAST)`
  indicators, which fixes F8. This is the **one deliberate AC #3 exception**: momentum's backtest
  output changes from "never trades" to real crossovers, pinned by a before/after test that
  records the defect (Task 5.2). Consequence to disclose, not fix here: the param model's
  `trade_size` default is `1000000` shares, which never mattered while the strategy could not
  cross; after this story a default-parameter `momentum` session would submit million-share market
  orders (refused by IBKR — Story 3.7's path handles it). Route to `deferred-work.md`.
  `warmup_days` stays in `MomentumParameters` (schema stability) and becomes a **floor** on the
  computed lookback: `max(timedelta(days=warmup_days), warmup_lookback(...))`.
- **D-H — The lookback is computed, in whole IB units.** `warmup_lookback(bar_step: timedelta,
  bars_needed: int) -> timedelta`, pure. For steps `>= 1 minute`: bars per RTH session =
  `max(1, 6h30m // step)` (daily and longer: one bar per session per day of step), sessions needed
  = `ceil(bars_needed / per_session)`, calendar days = `ceil(sessions * 7 / 5) +
  WEEKEND_AND_HOLIDAY_SLACK_DAYS (3)`, then **snapped up** to what F3's rounding sends intact:
  `< 7` days exact; `7-29` → a multiple of 7; `30-364` → a multiple of 30; `>= 365` → a multiple
  of 365. Never less than one day for `>= 1 minute` bars, so a pre-open start never sends F3's empty
  seconds duration. For sub-minute steps (IB's small-bar rules apply) the lookback is seconds:
  `max(30 s, bars_needed * step * 3)` — pre-open this returns no data and D-D contains the
  strategy; disclosed as a known limit and routed. `bars_needed` = the strategy's largest indicator
  period (`max(fast_period, slow_period)`).
- **D-I — No database, CLI, migration or port change.** Warm-up failures reuse the start-failure
  surface Story 2.7 built; `SessionRecordPort` keeps its four methods; `EXPECTED_CAPABILITIES`,
  alembic head `85c949ac0374`, `src/db/**`, `src/services/**`, `src/api/**`, `templates/**` are
  **zero-diff**. The only CLI-file edit is `src/cli/commands/live.py:360-361`'s docstring
  ("`warmup` … no-op placeholder" is no longer true).
- **D-J — `WarmupFailedError` carries the D3 markers.** It is defined in a `live_*` module, so
  `tests/unit/core/test_exit_outcome_markers.py` requires it to be either marked or listed in
  `UNMARKED`. It is contained and never reaches the classifier on its own, but mark it
  (`exit_outcome = LiveCheckOutcome.ERROR`, `operator_safe_message = True`, our own text) rather
  than growing the exemption list.

## Acceptance Criteria

Epic text (`epics.md:1573-1609`) verbatim in **bold**; the clarification under each is binding.

1. **Given `sma_crossover.py` and `sma_momentum.py` — which today only call `subscribe_bars` —
   When `on_start()` is rewritten, Then each registers its indicators via
   `register_indicator_for_bars()` and requests history via `request_bars(...)`, with
   `subscribe_bars` invoked from the history callback so the live stream starts only after history
   has loaded (FR37, AR40).**
   - The premise is half stale: `sma_momentum.on_start()` already calls `request_bars` (no
     callback) then `subscribe_bars` — and its history has never reached its averages, because
     history goes to `on_historical_data` (F5) and momentum keeps deques, not indicators. The
     outcome required is unchanged for both.
   - (a) `subscribe_bars` appears in **no** `on_start` body and in exactly one callback per
     strategy, called **last**; (b) `on_bar` no longer feeds indicators by hand (F9 — pinned by a
     test that one live bar advances each indicator's `count` by exactly 1); (c) the history window
     comes from D-H, so a warm strategy's indicators are initialised at the first live bar for the
     default parameters on `1-MINUTE` and `1-DAY` bars.
2. **Given the rewritten `on_start()`, When it is inspected, Then it uses only mode-agnostic
   Nautilus APIs and contains no branch on execution mode — `is_live` count remains 0 (AR40,
   AR43).**
   - An AST test over `STRATEGY_MODULES` (globbed, `test_live_stop_path_is_inert.py:119-125`):
     zero `is_live` identifiers; the history callback of each built-in calls no
     `FORBIDDEN_ORDER_METHODS` name (it is not in `LIFECYCLE_HOOKS`, so the existing scan does not
     see it — this closes that gap for the new method only); strategies import nothing from
     `src.core.live_*`.
3. **Given the same strategy in a backtest, When it runs before and after this change, Then it
   behaves identically in both engines, with any difference explained solely by indicators being
   warm at the first traded bar (AR40).**
   - `sma_crossover`: the fill list (timestamp, side, quantity, price) of a deterministic backtest
     is **byte-identical** before and after — captured from the pre-change code in Task 1 **before
     the strategy is edited**, then asserted after.
   - Equivalence, the "explained solely by warm indicators" half: a backtest whose `DataEngine` has
     a catalog holding history `H` (F15) and is fed live bars `L` makes **exactly the same signals
     on `L`** as a plain backtest fed `H + L`.
   - `sma_momentum`: per **D-G's ruling**. Under the recommended ruling, the pre-change fill list is
     recorded as `[]` (the F8 defect, pinned as a documented constant) and the post-change fills
     equal an independent pure-Python SMA-crossover reference over the same closes.
4. **Given a started session, When the first live bar arrives, Then every registered indicator
   already reports initialised, and `warmup.completed` was logged before the first bar was
   processed (FR37, AR41).**
   - Proven at component tier with a real `DataEngine` and a `MockMarketDataClient`
     (`nautilus_trader.test_kit.mocks.data`) serving `slow_period` history bars, then one live
     bar: at that bar's `on_bar`, `indicators_initialized()` is true, and the captured log order
     is `warmup.completed` → the strategy's `subscribe_bars` → the bar. With history shorter than
     the period, `warmup.completed` is at WARNING with `indicators_initialized=False` (D-C).
   - D-F: with history ending in a crossover set-up, the **first** live bar can signal (the
     baseline came from history); a crossover *inside* history submits nothing.
5. **Given the warm-up requests, When they are issued, Then they respect IBKR historical pacing
   rules (NFR15).**
   - One request per strategy per process start, serialized (D-B): a component test with two
     strategies on the **same** bar type proves at most one request is ever pending and both
     strategies warm (F2's dedup does not fire). The duration sent is whole days for
     `>= 1 minute` bars (D-H, unit-tested against F3's mapping), which keeps it outside IB's
     small-bar pacing rules; the live procedure (P14) reads the duration string and greps D6's
     `162|10182|366` for a pacing violation.
6. **Given a session started 5 minutes before the market open, When the full sequence runs, Then
   it is subscribed and trading by the open (NFR3).**
   - Broker-double half: a unit test that D-H never produces a seconds duration for `>= 1 minute`
     bars (the pre-open empty-history trap, F3); a test that the worst case with default settings
     — two strategies both hitting D-D's deadline — is `2 × 75 s = 150 s < 300 s`. Operator half:
     P14's 09:25 ET variant, **defined, not run** (no Gateway at drafting).
7. **(Added at drafting — the hazard AC #1 creates.)** Given a strategy whose history request never
   completes, When the deadline passes, Then `warmup.failed` is logged, the strategy is contained
   through the existing start-failure path and never trades this run, the session keeps its other
   strategies (or fails its start with `NoStrategyStartedError` if none remain), and a late
   response is `warmup.discarded` without subscribing. Plus: a raising history callback is
   contained, never shuts the node down (D-E); a stop requested during a warm-up wait ends the wait
   within one poll interval, records no failure, and starts no further strategy.
8. **(Added at drafting — the retro debt routed here, `deferred-work.md:1852-1856`.)** Given a
   registered indicator that raises on a **live** bar, When the bar is handled, Then the guard's
   `handle_bar` boundary contains it (`strategy.failed handler=handle_bar`, strategy `DEGRADED`, a
   sibling strategy keeps receiving bars) — proven with a real `Actor` and a raising `Indicator`
   subclass (F14), not a stub. The history-path counterpart is disclosed as uncontained (D-E).

## Tasks / Subtasks

- [x] **Task 0 — Standing checks (record results in the Debug Log)**
  - [x] 0.1 Head, clean tree, `uv run alembic current` = `85c949ac0374`; `ruff check`, `make
    typecheck` clean before the first edit.
  - [x] 0.2 Baselines: unit **2646 passed + 2 failed** (the two pre-existing size-cap failures —
    unpopulated `custom/` submodule), component **1562 passed / 16 skipped** (measured at drafting;
    re-measure). Integration: run the files this story touches, `--forked`.
  - [x] 0.3 Gateway reachable? (ports 4001/4002/7496/7497 — all closed at drafting, 16:39 ET
    Tuesday, after RTH.) Decides whether P14 can run (Task 11).

- [x] **Task 1 — Probes and the pre-change fingerprints (BEFORE editing any strategy)**
  - [x] 1.1 Re-measure F2/F11 against a real `LiveDataEngine` in a fresh interpreter: a client that
    never answers → callback never fires; `MockMarketDataClient` answering with bars → indicators
    fed before the callback; no client → empty response, callback fires (asynchronously).
  - [x] 1.2 Re-measure F4 (`_check_bounds` trims a `ts_init > end` bar) and F15 (a
    `BacktestEngine` whose `DataEngine` has a registered tmp `ParquetDataCatalog` holding `H` warms a
    strategy's indicators via `request_bars`).
  - [x] 1.3 **Capture the parity fingerprints from the unmodified code**: a deterministic backtest
    (synthetic price path with several crossovers; small periods) of `sma_crossover` and of
    `momentum`, fill lists recorded as test constants (Task 5). Commit nothing else first.

- [x] **Task 2 — `src/core/strategy_warmup.py` (AC #1c, #5, #6) — TDD, unit tier**
  - [x] 2.1 Red: tests for `warmup_lookback` — 1-minute/period 20 → `5 D`-shaped (≥ one session
    plus weekend slack); 1-minute/period 50; 1-hour; 1-day/period 50 (snaps to a multiple of 30,
    and F3's mapping of the result is not shorter than requested — assert through
    `timedelta_to_duration_str` imported **inside the test**, so the module stays framework-free);
    sub-minute; `bars_needed <= 0` refused; never `< 1 day` for `>= 1 minute`.
  - [x] 2.2 Green: implement; stdlib only (`math`, `datetime`); constants named
    (`RTH_SESSION`, `WEEKEND_AND_HOLIDAY_SLACK_DAYS`).

- [x] **Task 3 — Rewrite `sma_crossover` (AC #1, #2, #4 D-F) — TDD**
  - [x] 3.1 Red first: component-tier harness (real `MessageBus`/`Cache`/`DataEngine`, a
    `MockMarketDataClient`, `TestClock`) — history then live bars: indicators initialised at the
    first live bar; one live bar advances `count` by exactly 1 (F9 double-feed pin); subscribe only
    from the callback; baseline from history (first live bar can signal); crossover inside history
    submits nothing.
  - [x] 3.2 Green: `on_start` registers both SMAs, requests `warmup_lookback(bar_type.spec.timedelta,
    max(fast, slow))`, callback `_on_history_loaded(request_id)` sets the baseline then
    `subscribe_bars`; delete the two manual `handle_bar` calls in `on_bar`.
  - [x] 3.3 Keep `SMACrossover` at or under **103** statements (it is baselined): offset the new
    method against the deleted lines; update the baseline entry to the exact new value.

- [x] **Task 4 — Rewrite `sma_momentum` (AC #1, #3 per D-G)** — per the PO's D-G ruling. Under the
  recommended ruling: two registered SMAs replace the deques and `_update_ma`; `on_start` keeps the
  missing-instrument refusal; lookback `max(warmup_days, computed)`; same callback shape as 3.2.
  `SMAMomentum.on_bar` will likely drop to ≤ 50 statements — **remove** its baseline entry then
  (the ratchet fails on a stale one).

- [x] **Task 5 — Backtest parity and equivalence (AC #3) — integration tier, `--forked`**
  - [x] 5.1 `sma_crossover` post-change fill list == Task 1.3's constant.
  - [x] 5.2 `momentum`: per ruling (recommended: before = `[]` pinned with the F8 explanation; after
    == pure-Python reference crossovers).
  - [x] 5.3 Equivalence: catalog-warmed backtest on `L` makes the same signals as a plain backtest
    on `H + L` (F15). Make the test able to fail: a mutation that skips `register_indicator_for_bars`
    must turn it red (record it).

- [x] **Task 6 — `src/core/live_session_warmup.py` (AC #4, #5, #7) — TDD, unit + component**
  - [x] 6.1 Red: `WarmupWatch` tests with stub strategies — wrapper wraps `callback` whether passed
    by keyword or positionally (bind the `request_bars` parameter order: `bar_type, start, end,
    limit, client_id, callback, update_catalog, params`); callback-before-return tolerated (F12);
    `warmup.completed` emitted before the strategy's callback; WARNING when cold; `skipped` when no
    request was issued; deadline → `warmup.failed` + `WarmupFailedError`; late response →
    `warmup.discarded`, strategy callback not run; raising strategy callback → contained,
    `warmup.failed reason=callback_raised`; post-settle requests pass through untouched; stop /
    run-task-done ends the wait within one poll interval with no failure. Injected `clock` and
    `sleeper` (the runner's `sleeper` precedent) so no test waits 75 s.
  - [x] 6.2 Green: implement. Duck-typed, **no `nautilus_trader` import** (the guard/order-path
    discipline), stdlib + `structlog`-shaped `log` + `src.core.exit_outcome` for D-J's markers.
    Module docstring states the AR39/AR40 reading (Why #1), F2, F6/D-E's uncovered history path.

- [x] **Task 7 — Runner wiring (AC #4, #7) — component tier**
  - [x] 7.1 Red: runner tests with `TestLiveNode` — `warmup` phase logs `started`/`ok` and still
    makes **no call on the node**; `_start_strategy` instruments before `add_strategy`; a strategy
    that fails warm-up is contained and the session starts with its sibling; all fail →
    `NoStrategyStartedError`; `session.started` is logged only after every settle; a stop during a
    wait starts no further strategy. The double's `start_strategy` does not call `on_start` — drive
    `on_start` from a double where the test needs a request.
  - [x] 7.2 Green: `_phase_warmup` constructs the watch; `_start_strategy` instruments and settles.
    **Budget: the runner file may grow by at most 16 statements (hard 500 cap)** — everything else
    lives in the new module. Raise `LiveSessionRunner`'s baseline entry to its exact new value with
    a one-line reason in the same commit.
  - [x] 7.3 Update the stale prose: runner module docstring (`:17-18`, `:53-57`), `_phase_warmup`
    docstring, `live_session_phases.py:24-27`, `live.py:360-361`, and
    `TestTheEpicFourPlaceholders` (keep `reconcile`'s half untouched — Story 4.2 owns it and is in
    flight in parallel; edit only what `warmup` changed).

- [x] **Task 8 — The debt item (AC #8) — component tier**
  - [x] 8.1 Real strategy (`Strategy` subclass), real `MessageBus`, guarded with `StrategyGuard`, a
    registered raising `Indicator` subclass (F14): one live bar → `strategy.failed
    handler=handle_bar`, `DEGRADED`; a sibling strategy on the same bar type still receives the
    bar. Mutation: unwrap `handle_bar` → the raise escapes (record it).
  - [x] 8.2 Disposition line in `deferred-work.md` striking the debt item, and the D-E history-path
    residual recorded with an owner ("whoever first ships an indicator that can raise").

- [x] **Task 9 — Existing tests the rewrite breaks (fix, don't delete their intent)**
  - [x] 9.1 `tests/integration/test_sma_strategy_nautilus.py` `:67-82` (`on_start` on an
    unregistered strategy now raises, F15), `:111-169` (`on_bar` no longer feeds indicators, F9) —
    rewrite against a registered strategy, keep each test's intent.
  - [x] 9.2 `tests/integration/core/test_live_strategy_failure_survives.py` — its probe processes
    bars as soon as both strategies are `RUNNING`; subscription now lands asynchronously after the
    empty history response, so the probe must wait for each strategy's subscription (deadline-polled,
    never a bare sleep — the file's own review rule, `:111-120`). Its mutation meta-test must still
    go red.
  - [x] 9.3 `tests/integration/core/test_live_order_survives_restart.py:185-200` — its
    `_ProbeStrategy` calls `super().on_start()`; confirm it still passes (is there a `DataEngine`
    endpoint in `_new_engines`?) and adapt only if it goes red.
  - [x] 9.4 Run every file `grep -rln "SMACrossover\|sma_crossover\|SMAMomentum\|momentum" tests`
    lists, at its tier.

- [x] **Task 10 — Guard lists and governance (same commit as the module that triggers them)**
  - [x] 10.1 `src/core/live_session_warmup.py` → `NODE_FACING_MODULES`
    (`tests/unit/core/test_live_node_never_exits.py:42`) and `TestImportPurity.MODULES`
    (`tests/component/core/test_session_runner_phases.py:1269`), each with its reason. **Not**
    `STOP_PATH_MODULES` — it does not run on the stop path; say so in a comment there.
  - [x] 10.2 `_STDLIB_AND_FIRST_PARTY` — expected untouched (`asyncio`, `math`, `time`,
    `dataclasses`, `typing` are listed) — **proven by running**
    `tests/integration/core/test_epic1_ac_node.py`, not by inspection.
  - [x] 10.3 `test_exit_outcome_markers.py` passes with `WarmupFailedError` marked (D-J).
  - [x] 10.4 Size-cap baseline entries updated to exact values (SMACrossover, SMAMomentum.on_bar,
    LiveSessionRunner), each change with a one-line reason; the two pre-existing `custom/` failures
    unchanged and recorded.

- [x] **Task 11 — Live verification (informational only, never a gate)**
  - [x] 11.1 Write **Procedure P14** in `docs/qa/phase3-live-verification.md` (continue after P13;
    if a parallel Epic 4 story also claims P14, the integrator renumbers — note it in the row):
    a normal `live start` of `sma_crossover` on `NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL`; read the
    `Request … bars` line (duration), `warmup.completed` (`elapsed_ms`, `indicators_initialized`),
    its position before the first `order.*`/bar record, zero `warmup.failed`, D6's grep first; the
    09:25 ET NFR3 variant (subscribed and trading by 09:30). Paper orders may result — the P10–P13
    precedent.
  - [x] 11.2 If a Gateway is reachable **and** a strictly read-only observation is possible, run it
    and record the row; otherwise record `defined, not run` with the port check. Never submit an
    order from this session, never `--real-money`, never touch `.env`/docker/kill/reconnect scripts.

- [x] **Task 12 — Gates**
  - [x] 12.1 `uv run ruff format .`, `uv run ruff check .`, `make typecheck`.
  - [x] 12.2 `make test-unit`, `make test-component`; integration `--forked` for every touched or
    listed file (Task 9.4) plus `tests/integration/core/`.
  - [x] 12.3 `grep -rn "is_live" src/core/strategies/` → 0.

### Review Findings

Code review 2026-09-22 — three parallel layers (Blind Hunter / Edge Case Hunter / Acceptance
Auditor), 35 raw findings → 1 decision, 12 patches, 4 deferred, 7 dismissed.

- [x] [Review][Decision] The history/live seam can lose or double-count one bar — The live IB
  stream is already open from `_phase_subscribe` (the bar observer); a strategy joins the bus only
  in its history callback. A bar that closes after the history request's `end` but is published
  before the callback is never seen (gap); a bar inside the history that the adapter publishes
  after the callback (it publishes bar X on X+1's first update, or `duration+1s` later —
  `client/market_data.py:1068-1073, 1163-1168`) is fed twice, because `Actor.handle_bar` feeds
  registered indicators unconditionally. Probability ≈ publication lag ÷ bar interval per start;
  effect: the SMA is off by one bar for at most `slow_period` bars (never trades on cold
  indicators; may shift one crossover). A fix needs a seam guard outside the strategy (AC #1
  mandates registered indicators, so the strategy cannot filter its own feed) — scope beyond this
  story. Options: (a) defer to Story 4.5 (resume correctness) with a P14 measurement; (b) build a
  seam guard now. **RESOLVED by the PO: A** — deferred to Story 4.5 with an owner in
  `deferred-work.md`; P14 gains an informational criterion 7 that records which reading each
  live run shows; `sma_crossover.on_start`'s docstring no longer claims the seam is whole.
- [x] [Review][Patch] A stop, a dead node or a reclaim during a warm-up wait still counts the
  strategy as started and lets later strategies start [src/core/live_session_runner.py:760-775]
- [x] [Review][Patch] The connection monitor is not re-observed while a later strategy warms, so an
  already-live strategy's orders pass the NFR10 gate on a stale reading
  [src/core/live_session_runner.py:684]
- [x] [Review][Patch] Requests left pending after a failed/abandoned settle, or after `on_start`
  raised, can still subscribe a faulted strategy [src/core/live_session_warmup.py:206-214]
- [x] [Review][Patch] The history callback's bookkeeping and `warmup.completed` emit sit outside its
  containment `try` [src/core/live_session_warmup.py:257-273]
- [x] [Review][Patch] `warmup_lookback` raises on MONTH/YEAR bars (`spec.timedelta` has no fixed
  interval), aborting `on_start` even in a backtest [src/core/strategy_warmup.py:94]
- [x] [Review][Patch] `momentum`'s `warmup_days` floor bypasses D-H's snapping (10 days → "1 W")
  and forces a day-long window onto sub-minute bars [src/core/strategies/sma_momentum.py:85-86]
- [x] [Review][Patch] A sub-minute window of a day or more is sent as a rounded-down `N D`
  [src/core/strategy_warmup.py:97-98]
- [x] [Review][Patch] `_Request` compares by value, so `requests.remove` can drop the wrong entry;
  a strategy with no registered indicators logs `warmup.completed` at WARNING
  [src/core/live_session_warmup.py:104-113]
- [x] [Review][Patch] Extra positional arguments are silently dropped by the hand-copied
  `request_bars` binding, and nothing pins that binding against the real signature
  [src/core/live_session_warmup.py:160]
- [x] [Review][Patch] Tests that cannot fail or do not test what they claim:
  `test_strategies_warm_one_at_a_time` (serialization), `test_the_two_built_ins_warm_from_distinct_
  requests` (vacuous), the instrumentation `__dict__` check, AC #4's chained proof (watch + real
  engine + real strategy + live bar), AC #7's "within one poll interval", and the runner tests'
  1.0 s timing margin [tests/component/core/test_session_runner_warmup.py,
  tests/component/core/test_strategy_warmup_engine.py, tests/unit/core/test_live_session_warmup.py]
- [x] [Review][Patch] Docstrings: the half-days claim is backwards; "every bar the indicators see
  arrives in order" overclaims the seam; `NoStrategyStartedError`'s text ignores interruption
  [src/core/strategy_warmup.py:35-37, src/core/strategies/sma_crossover.py:80-88,
  src/core/live_session_runner.py:698-703]
- [x] [Review][Patch] Story record: Task 3.3's 103 limit was overridden (110, with reason) without
  saying so; D-H's signature changed to a duck-typed `bar_type` plus a non-time branch, undisclosed;
  Task 0's Gateway reading was carried from drafting, not re-measured at dev start
  [_bmad-output/implementation-artifacts/4-4-warm-indicators-from-history-before-the-live-stream-starts.md]
- [x] [Review][Defer] Serialized warm-up scales as N × (timeout + 15 s): four or more strategies
  all failing would exceed NFR3's five minutes [src/core/live_session_runner.py:742] — deferred,
  design consequence of D-B
- [x] [Review][Defer] A strategy that stops itself in `on_start` is logged `warmup.skipped` and
  counted as started [src/core/strategies/sma_momentum.py:77-80] — deferred, pre-existing (Story 2.7)
- [x] [Review][Defer] Warm-up through `request_aggregated_bars`, `request_data`, tick requests or
  `super().request_bars` is not watched [src/core/live_session_warmup.py:155-180] — deferred,
  custom strategies only
- [x] [Review][Defer] IB's small-bar duration caps (e.g. 3,600 s for 5-second bars) are unverified
  offline [src/core/strategy_warmup.py:97-98] — deferred, needs a live measurement

## Dev Notes

### The current surface — exact extension points

- `LiveSessionRunner.run()` phase calls `src/core/live_session_runner.py:382-388` — **unchanged
  order**. `_phase_warmup` `:579-582` (no-op today). `_phase_trading` `:675-697` (the list
  comprehension over `_start_strategy`; `session.started` at `:691-697`). `_start_strategy`
  `:699-736` — ordering materialise → `guard.wrap` → `install_order_path` → `add_strategy` →
  `start_strategy`; its `except Exception` is the containment path D-D reuses.
- `StrategyGuard.record_start_failure` `src/core/live_strategy_guard.py:342-368`; `_fault_quietly`
  runner `:738-769` (F13: `fault()` from `RUNNING` succeeds).
- `install_order_path` `src/core/live_order_path.py:239-287` — the instance-attribute wrapping
  precedent D-B copies (marker attribute for idempotency, "before `add_strategy`" window).
- `SessionStopSignals.requested` / `raise_if_requested` `src/core/live_session_signals.py:152,217`.
- Settings: `ibkr_request_timeout` `src/config.py:75-77` (default 60 s), passed to the IB data
  client as `request_timeout` (`src/core/live_node_builder.py:358`).
- Live bars are EXTERNAL, time-aggregated only (`resolve_live_bar_types`,
  `src/core/live_market_data.py:143-200`) — F2's non-time-bar path is unreachable from a spec, but
  the watch's deadline covers it anyway.

### Scope boundaries — do NOT build

- No mid-position logic (Story 4.5), no reconciliation (4.2/4.3), no `live reconcile` (4.6). Do not
  touch `_phase_reconcile` or its tests.
- No change to `custom/` strategies (submodule; they stay cold and log `warmup.skipped` if they
  issue no request).
- No widening of `GUARDED_HANDLERS`; no `on_historical_data` override in the built-ins (the
  baseline is set in the callback, D-F).
- No retry of a failed warm-up request (IB pacing; AR43's retry-on-timeout anti-pattern in spirit).
- No fail-fast subscription to the adapter's `requests.{id}` `"Failed"` status — it would save up
  to 74 s on an empty history but couples the watch to IB-adapter internals; record as a possible
  refinement in `deferred-work.md`.
- No `trade_size` default change for `momentum` (D-G consequence — routed, not fixed).

### Hazards (each bit a previous story, or will)

- **Bookkeeping keyed on the returned request id misses a synchronous callback (F12).** Key the
  wrapper's state on a per-call closure object.
- **A late history response into a faulted strategy would subscribe it.** D-D's `discarded` branch
  exists for exactly this; test it.
- **Double feeding (F9).** A test that asserts only `initialized` would pass with the manual lines
  left in; assert `count` advances by exactly 1 per live bar.
- **The component tier must not construct a `LiveDataEngine`** — it claims the Nautilus C logging
  subsystem; `test_session_runner_phases.py`'s autouse `_assert_c_logging_state_is_unchanged`
  exists to catch it. Use the non-live `DataEngine` (synchronous) at component tier and put
  `LiveDataEngine` probes at integration tier, `--forked` (Story 2.7's precedent,
  `test_live_strategy_failure_survives.py:1-33`).
- **A cannot-fail test.** Every new AST scan gets a non-vacuity twin (the scanned set is non-empty
  and a planted probe goes red) — the repo-wide rule since Story 2.3.
- **Guard-list rot.** The new `live_*` module is hand-added to two lists in its creating commit
  (CLAUDE.md Anti-Patterns).
- **The runner's hard file cap.** 16 statements of headroom, total. Budget the runner diff before
  writing it.
- **Parallel Epic 4 stories** edit `live_session_runner.py`, `test_session_runner_phases.py`,
  `docs/qa/phase3-live-verification.md` and `deferred-work.md` concurrently. Keep this story's
  footprint in shared files minimal and self-contained (append, don't reflow).

### Testing standards summary

TDD (red first, recorded). Unit for `warmup_lookback` and the watch with stubs; component for the
strategies against a real non-live `DataEngine` + `MockMarketDataClient`, the runner with
`TestLiveNode`, and the debt item with a real `Strategy`; integration `--forked` for
`BacktestEngine` parity/equivalence and any `LiveDataEngine` probe. No test touches a broker
(NFR32). Every mutation named in the tasks is run and its result recorded (kill or survive).

### Project Structure Notes

- New: `src/core/strategy_warmup.py` (pure; imported by strategies), `src/core/live_session_warmup.py`
  (runner-side; `live_*` family, globbed into `LIVE_MODULE_GLOBS`), tests
  `tests/unit/core/test_strategy_warmup.py`, `tests/unit/core/test_live_session_warmup.py`,
  `tests/component/strategies/…` or `tests/component/core/test_strategy_warmup_engine.py` (dev's
  choice, follow the nearest precedent), `tests/integration/core/test_warmup_backtest_parity.py`.
- Modified: both built-in strategies, `live_session_runner.py`, `live_session_phases.py`
  (docstring), `live.py` (docstring), the guard-list test files, `test_size_caps.py` baseline,
  `test_sma_strategy_nautilus.py`, `test_live_strategy_failure_survives.py`, `deferred-work.md`,
  `docs/qa/phase3-live-verification.md`.
- "Prefer editing existing files": both new modules are genuinely new concepts (a history window;
  a runner-side warm-up watch), and the runner cannot absorb either under its file cap.

### References

- Epic/story: `_bmad-output/planning-artifacts/epics.md:1573-1609`; scope extract
  `_bmad-output/planning-artifacts/prd-epic4-scope.md` (FR37, NFR3, NFR15, AR39-AR41, AR43; debt
  routed to 4.4 at `:171`).
- Architecture: `_bmad-output/planning-artifacts/architecture.md:405-412` (AR39/AR40),
  `:434-440` (AR41 events).
- Debt: `_bmad-output/implementation-artifacts/deferred-work.md:1852-1856`, `:1904-1914`, `:2160`.
- Precedents: `src/core/live_order_path.py:239-287` (instance wrapping), `live_session_runner.py:545-559`
  (running the loop inside a phase), Story 2.7 containment (`live_strategy_guard.py`), Story 3.7's
  record for guard-list and size-budget disclosure style.
- Wheel: see the Measured facts table (all paths under
  `.venv/lib/python3.11/site-packages/nautilus_trader/`).

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), in the Epic 4 harness worktree
`harness/s4-4-20260922-162952-2`.

### Debug Log References

- **Task 0.** Head `c4c6afa`, tree clean at start. `uv run ruff check .` and `make typecheck` clean
  before the first edit. Baselines measured: unit **2646 passed + 2 failed**, component **1562
  passed / 16 skipped**. The two unit failures are pre-existing and environmental:
  `tests/unit/governance/test_size_caps.py`'s baseline names two `src/core/strategies/custom/`
  submodule files and the submodule is not populated in this worktree — recorded, not "fixed" by
  deleting entries that are correct in a full checkout. `uv run alembic current` could not be run
  from this session (tool permission denied); `alembic/**` is zero-diff by `git status`, and no
  migration was written. Gateway: ports 4001/4002/7496/7497 all **closed** at 16:39 ET Tuesday
  (after the RTH close); Redis 6379 and Postgres 5432 open.
- **Task 1 probes** (fresh interpreters, `/tmp/p44/`, not committed): `probe_backtest.py` — F1
  (callback inside `request_bars`, cold, all bars delivered); `probe_wrap.py` — F12 (instance
  `request_bars` override honoured; callback runs before the base call returns), F13 (`fault()`
  from `RUNNING` → `FAULTED`), F14 (a Python `Indicator` subclass raising in `handle_bar` escapes
  `Actor.handle_bar`); `probe_live.py` against a real `LiveDataEngine` — no client: the callback
  fires **asynchronously** after `start()` returns, cold; `MockMarketDataClient` with 5 bars:
  indicator `count=5` **before** the callback; a client that never answers: the callback never
  fires and `has_pending_requests()` stays true (F2 in miniature); `probe_catalog.py` — F15 (a
  `BacktestEngine` with a registered `ParquetDataCatalog` warms `count=5` at the callback and the
  live bars continue the same SMA); `probe_component.py` — the synchronous non-live `DataEngine`
  harness, and F4 (a history bar with `ts_init` in the future is trimmed before the indicators).
- **Task 1.3 fingerprints**, captured from the unmodified strategies before either was edited
  (`/tmp/p44/capture_fingerprints.py`): `sma_crossover` fast=3/slow=5 over a 240-bar sine path →
  13 fills; `momentum` over the same path → **zero fills** (F8 confirmed in a real backtest).
- **Mutation sweep** (`/tmp/p44/mutations.py`: each applied, target tests run, file restored):
  **13 of 13 KILLED** — M1 double feed in `on_bar`; M2 `subscribe_bars` in `on_start`; M3 no
  crossover baseline (crossover); M4 indicators never registered (the Task 5.3 equivalence proof
  went red, as required); M5 no baseline (momentum); M6 `warmup.completed` after the strategy's
  callback; M7 late answer not discarded; M8 the callback's raise escapes; M9 runner never
  instruments; M10 runner does not wait; M11 runner starts further strategies after a stop; M12
  lookback not snapped to whole IB units; M13 a seconds lookback for minute bars. The debt item's
  own mutation (unwrap `handle_bar`) is permanent, as the unguarded twin test.

### Completion Notes List

- **What shipped, as designed in D-A..D-J.** Both built-ins now warm the same way:
  `register_indicator_for_bars` ×2, `request_bars(start=now - warmup_lookback(...),
  callback=self._on_history_loaded)`, and a callback that records the crossover baseline (only
  from initialised indicators) and calls `subscribe_bars` **last**. `sma_crossover.on_bar` no
  longer feeds its SMAs by hand (F9). `sma_momentum`'s deque average and `_update_ma` are gone,
  replaced by two registered `SimpleMovingAverage`s (ruling D-G: A); `warmup_days` is kept as a
  floor under the computed window. New pure `src/core/strategy_warmup.py` (`warmup_lookback`,
  whole IB units, never seconds for `>= 1 minute` bars). New `src/core/live_session_warmup.py`
  (`WarmupWatch`, `WarmupFailedError`, `warmup.completed/failed/skipped/discarded`). Runner:
  `_phase_warmup` arms the watch and still touches nothing on the node; `_start_strategy`
  instruments `request_bars` beside the guard and the order path, then settles each strategy's
  warm-up before the next is started; a failed warm-up is raised into the existing `except` and
  contained by the start-failure path (ruling D-D: A) — zero guard edits.
- **AC evidence.** AC #1/#2: `tests/unit/strategies/test_strategy_warmup_shape.py` (AST scan over
  the globbed `STRATEGY_MODULES`, with planted probes) and `test_strategy_warmup_engine.py`. AC #3:
  `tests/integration/core/test_warmup_backtest_parity.py` — `sma_crossover`'s fills byte-identical
  to the pre-change fingerprint; `momentum`'s pre-change `[]` pinned as the F8 record and its new
  fills equal to a pure-Python reference; a catalog-warmed strategy's per-bar decision state equals
  a continuous run's on the live half, for both strategies. AC #4: real `DataEngine` +
  `MockMarketDataClient` — every indicator initialised at the first live bar, one live bar = +1
  count, the first live bar can signal, history never trades; the unit tier pins
  `warmup.completed` before the strategy's callback. AC #5: one request per strategy, serialized
  (`test_strategies_warm_one_at_a_time`); duration never in seconds (unit tests through the real
  adapter's `timedelta_to_duration_str`). AC #6: the worst case `2 × 75 s < 300 s` and the
  no-seconds lookback; the live half is P14, **defined, not run**. AC #7:
  `test_session_runner_warmup.py` (silent strategy contained and faulted, sibling trades; all
  silent → `NoStrategyStartedError`; a stop ends the wait with no failure and no further start)
  plus the watch's unit tests. AC #8: `TestARaisingRegisteredIndicatorIsContained`, with an
  unguarded twin.
- **Tests the rewrite broke, fixed without weakening their intent.** `test_live_order_path.py`
  (7 call sites): bars now go through `handle_bar` — what the bus calls — because `on_bar` alone no
  longer moves registered indicators. `test_session_runner_strategy_failure.py`: `RealDispatch`
  gained a real non-live `DataEngine`; without one the history request is dropped and nothing
  subscribes (F2 in harness form). `test_momentum_strategy.py`: three tests pinned the deleted
  deques and were rewritten; the MA test now slides past the window — the old version stopped
  before the first eviction, which is how F8 survived. `test_sma_strategy_nautilus.py`: three tests
  rewritten against a registered strategy on the stub bar's own bar type (registered indicators
  only see their own bar type). `test_live_strategy_failure_survives.py`: the probe now waits for
  both subscriptions to land; its mutation meta-test still goes red.
- **Guard lists (CLAUDE.md Anti-Patterns), all in this change.** `live_session_warmup.py` →
  `NODE_FACING_MODULES` and `TestImportPurity.MODULES`, each with its reason; deliberately **not**
  `STOP_PATH_MODULES` (startup-only; comment added there). `_STDLIB_AND_FIRST_PARTY` needed no edit
  — proven by the full integration run, which includes `test_epic1_ac_node.py`. The exit-outcome
  marker scan passes with `WarmupFailedError` marked (D-J); `UNMARKED` did not grow.
- **Size budgets, measured with the guard's own metric.** `live_session_runner.py` 484 → **498 of
  a hard 500** (not allowlisted). Only two statements of headroom are left for Stories 4.2/4.3,
  which also edit this file; the integrator should budget before merging. `LiveSessionRunner`
  400 → 413 and `SMACrossover` 103 → 110, both raised in `SIZE_BASELINE` with one-line reasons;
  `SMAMomentum.on_bar` 58 → 52 (shrank but is still over the cap, so the entry was lowered).
  `WarmupWatch` first measured **119**, over the cap for new code, and was split (the callback
  wrapper and records moved to module functions) to **73** rather than disclosed. Every touched
  baseline entry regenerates exactly; the only drift is the two pre-existing `custom/` entries.
- **Gates.** `ruff format`/`ruff check`/`make typecheck` clean. Unit **2702 passed + 2
  pre-existing failures** (+56); component **1599 passed / 16 skipped** (+37); integration
  (`--forked`, full `tests/integration`, Postgres and Redis up) **318 passed / 2 skipped**;
  `is_live` in `src/core/strategies/` = **0**.
- **Live verification.** Procedure P14 written into `docs/qa/phase3-live-verification.md` and
  recorded **defined, not run**: no Gateway was reachable, and it was after the RTH close.
  Informational only, per the harness charter. Nothing was submitted, and `--real-money`, `.env`,
  docker and the kill/reconnect/connection-loss scripts were not touched.
- **Routed, not fixed** (`deferred-work.md`, "Deferred from: story-4.4"): the history-path
  indicator raise (node-wide shutdown); `momentum`'s now-live `trade_size=1000000` default;
  sub-minute pre-open warm-up; the fail-fast `requests.{id}` refinement; `custom/` strategies
  staying cold; the size-cap guard's dependence on the submodule; `elapsed_ms` never read live.

- **Code review (2026-09-22) — 1 decision (ruled A), 12 patches applied, 4 deferred, 7
  dismissed.** Patches, each with a test that goes red without it:
  - *Interrupted warm-ups are no longer counted as started.* `WarmupWatch.settle_blocking`
    returns `settle`'s result, and `_start_strategy` refuses to start another strategy once
    `_stopping()` is true. A strategy whose wait a stop, a reclaim or a finished run task ended
    returns `False`, so `session.started` is never logged for a strategy that never warmed; if none
    started, `NoStrategyStartedError` (whose text now names interruption) is demoted to a stop by
    `run()` exactly as before.
  - *A reclaim during a warm-up wait is honoured* (an Edge Case Hunter / Acceptance Auditor
    finding). `_stopping()` reads `StartupHeartbeat.reclaim`, and `_phase_trading`'s reclaim
    check moved to after the loop. That is not late, because `_start_strategy`'s pre-check has
    already refused every start by then.
  - *The connection is re-observed while a later strategy warms.* `on_poll` is called at most once
    a second (NFR10: an earlier strategy is live and may trade).
  - *Watch hardening.* Every pending request is abandoned when the wait ends; a faulted strategy's
    late answer is discarded; all callback bookkeeping and logging sit inside the containment
    `try`; requests compare by identity (`eq=False`); extra positional arguments pass through to
    the real method; with no registered indicators the record is INFO with
    `indicators_initialized=None`.
  - *Lookback.* MONTH/YEAR bars no longer raise (their `spec.timedelta` has no fixed interval). A
    sub-minute window of a day or more is snapped to whole days. `momentum`'s `warmup_days` is a
    `minimum=` snapped with the rest, and is ignored for sub-minute bars.
  - *Tests that could not fail.* `test_strategies_warm_one_at_a_time` now reads the log inside the
    `add_strategy` spy (`[0, 1]`). The vacuous two-harness test is deleted. The instrumentation
    check reads the watch's marker. AC #4 has one chained proof (the watch, a real `DataEngine`
    and client, a real strategy, a live bar; order `warmup.completed` → `subscribe_bars` → bar,
    plus the WARNING on a short history). The `request_bars` binding is pinned against
    `inspect.signature(Actor.request_bars)` and exercised positionally against the real method.
    AC #7's "within one poll interval" is asserted.
  - *Docstrings and prose.* The half-days claim is corrected.
- **Story-record corrections the Auditor asked for.**
  - **Task 3.3**'s instruction was to keep `SMACrossover` at or under 103. It was **not met**: the
    class is 110, and the baseline entry was raised with a one-line reason (the disclose-and-record
    path), not held at 103.
  - **D-H** as drafted named `warmup_lookback(bar_step: timedelta, …)`. The code takes a
    duck-typed `bar_type`, because `spec.timedelta` raises on non-time bars (and, found in review,
    on MONTH/YEAR). It also has a non-time branch, and a `minimum=` keyword added in review.
  - **Task 0.1**: `alembic current` was **not** run (tool permission); only `alembic/**`'s zero
    diff stands in for it. The Gateway reading at dev start was carried from drafting rather than
    re-measured. It was re-measured at review, 17:43 ET Tuesday: 4001/4002/7496/7497 closed, Redis
    and Postgres open.
- **Post-review measurements.** Mutation sweep re-run in full, **22 of 22 killed**: the original 13
  re-pointed at the changed code, plus 9 for the review fixes (R1–R9). R1 (reclaim not read)
  **survived first** — its test passed because the node's own run end also ended the wait — and
  the test was tightened (a 5 s node, the run must end in under 3 s) before it was killed. Sizes:
  runner file **499 of 500**, `LiveSessionRunner` 414 (baseline raised with reason), `WarmupWatch`
  81, `SMACrossover` 110, `SMAMomentum.on_bar` 52. Gates: `ruff format`/`ruff check`/`make
  typecheck` clean; unit **2717 passed + the 2 pre-existing `custom/` size-cap failures**;
  component **1606 passed / 16 skipped**; integration **318 passed / 2 skipped** (`--forked`, full
  `tests/integration`).

### File List

New:

- `src/core/strategy_warmup.py`
- `src/core/live_session_warmup.py`
- `tests/unit/core/test_strategy_warmup.py`
- `tests/unit/core/test_live_session_warmup.py`
- `tests/unit/strategies/test_strategy_warmup_shape.py`
- `tests/component/core/test_strategy_warmup_engine.py`
- `tests/component/core/test_session_runner_warmup.py`
- `tests/integration/core/test_warmup_backtest_parity.py`
- `_bmad-output/implementation-artifacts/4-4-warm-indicators-from-history-before-the-live-stream-starts.md`

Modified:

- `src/core/strategies/sma_crossover.py`
- `src/core/strategies/sma_momentum.py`
- `src/core/live_session_runner.py`
- `src/core/live_session_phases.py` (docstring)
- `src/cli/commands/live.py` (`live start` help text)
- `README.md` (startup-sequence paragraph)
- `docs/agent/nautilus.md` (warm-up section)
- `docs/qa/phase3-live-verification.md` (Procedure P14)
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (the `4-4-…` key only)
- `tests/component/core/test_live_order_path.py`
- `tests/component/core/test_session_runner_phases.py`
- `tests/component/core/test_session_runner_strategy_failure.py`
- `tests/component/test_momentum_strategy.py`
- `tests/integration/test_sma_strategy_nautilus.py`
- `tests/integration/core/test_live_strategy_failure_survives.py`
- `tests/unit/core/test_live_node_never_exits.py`
- `tests/unit/core/test_live_stop_path_is_inert.py`
- `tests/unit/governance/test_size_caps.py`

## Change Log

- 2026-09-22 — Story drafted (create-story): ready-for-dev. D-D and D-G carried recommended options
  pending the PO's ruling.
- 2026-09-22 — PO ruled D-G: A (convert `momentum` to registered SMAs; disclosed AC #3 exception)
  and D-D: A (bounded wait, then contain through the start-failure path).
- 2026-09-22 — Implemented (dev-story): all 12 tasks done; 13/13 mutations killed; unit 2702
  (+56, 2 pre-existing failures unchanged), component 1599/16sk (+37), integration 318/2sk. P14
  defined, not run (no Gateway, after RTH). Status → review.
- 2026-09-22 — Code review (three parallel layers): 1 decision (PO ruled A — the history/live seam
  deferred to Story 4.5), 12 patches applied, 4 deferred, 7 dismissed; 22/22 mutations killed after
  the fixes. Status → done.
