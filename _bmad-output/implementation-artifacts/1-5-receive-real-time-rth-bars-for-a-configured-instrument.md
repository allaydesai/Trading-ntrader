# Story 1.5: Receive Real-Time RTH Bars for a Configured Instrument

Status: review

Epic: 1 — Live Broker Connection & Real-Money Safety Gate (Phase 3, Paper Trading)
Depends on: 1.1 (gate), 1.2 (client-ID split), 1.3 (node builder) — all `done`.
Consumed by: 1.7 (`ntrader live check` prints the bars this story delivers), Epic 2 (the runner
owns the node lifecycle and will attach the observer), Epic 4 (warm-up `request_bars`).

## Story

As the operator,
I want live bars arriving on real-time data restricted to regular trading hours,
so that what the strategy sees live matches the session basis of the bars it was backtested on.

## Acceptance Criteria

**AC #1 — REALTIME, never a silent downgrade (FR3, AR21)**

**Given** a session-configured node
**When** market data is requested
**Then** the market data type is `REALTIME`, explicitly overriding the `DELAYED_FROZEN` default that
exists for paper data fetching
**And** a session that cannot obtain real-time data fails loudly rather than silently falling back to
delayed.

**AC #2 — RTH only (FR4, AR21)**

**Given** an instrument subscription
**When** bars are delivered
**Then** they are restricted to regular trading hours via `ibkr_use_rth=True`.

**AC #3 — bars arrive and are observable (FR1)**

**Given** a subscribed instrument
**When** a bar closes at the venue
**Then** the bar is delivered to the node and logged with its instrument and timestamp.

**AC #4 — market-data line budget (NFR16, NFR30)**

**Given** a session whose instrument count exceeds the account's concurrent market-data-line budget
**When** the session starts
**Then** it fails at startup with a message naming the limit and the requested count, rather than
silently dropping subscriptions.

**AC #5 — pacing (NFR15)**

**Given** historical or streaming requests are issued
**When** request volume is measured
**Then** the existing 45 req/s pacing discipline governs them.

## Tasks / Subtasks

> TDD is non-negotiable in this repo. Every task below is Red → Green → Refactor: write the failing
> test first, watch it fail for the stated reason, then implement.

- [x] **Task 0 — Baselines (no code change)**
  - [x] Record today's collected counts so later claims are checkable:
        `uv run pytest tests/unit --collect-only -q | tail -1`,
        same for `tests/component` and `tests/integration`.
        State in the Dev Agent Record whether each number is *collected*, *passed*, or
        *passed+skipped* — Story 1.3's review lost a day to conflating them.
  - [x] Confirm `tests/integration` still has its pre-existing 23-test failure baseline
        (unrelated to this story; see "Known test-infra hazard" in Dev Notes).

- [x] **Task 1 — `ibkr_market_data_lines` setting (AC: #4)**
  - [x] RED: add to `tests/unit/test_ibkr_config.py` — the field exists, defaults to `100`, is
        env-overridable (`IBKR_MARKET_DATA_LINES`), and rejects `0` / negative via `ge=1`.
  - [x] GREEN: add to `IBKRSettings` in `src/config.py`, in the existing "Data settings" block:
        ```python
        ibkr_market_data_lines: int = Field(
            default=100, ge=1,
            description=(
                "Concurrent IBKR market-data lines this account allows. Each streaming bar "
                "subscription consumes one. Default 100 is IBKR's standard allocation; raise it "
                "only to a value the account actually has (quote booster packs / commission tier)."
            ),
        )
        ```
  - [x] Do **not** touch `.env` (hook-protected; see "Files you must not touch"). `.env.example`
        is also protected — record any doc wording needed as a follow-up item, do not attempt a
        write.

- [x] **Task 2 — `src/core/live_market_data.py`: market-data policy (AC: #1, #2, #4)**
  - [x] RED first, in `tests/component/core/test_live_market_data.py` (component tier — it imports
        Nautilus; see "Test tier placement").
  - [x] `class LiveMarketDataError(Exception)` — "the live market-data configuration cannot be
        honoured". Sibling of `LiveNodeConfigError`, deliberately **not** a subclass (that would
        need `live_market_data` to import `live_node_builder`, and the dependency runs the other
        way). Every caller that catches one must catch both — the probe script and Story 1.7's CLI
        are the two call sites that exist.
  - [x] `resolve_live_market_data_type(settings) -> MarketDataTypeEnum`:
    - Returns `MarketDataTypeEnum.REALTIME` when `ibkr_market_data_type` was **left at its
      default** — this is AC #1's "explicitly overriding the `DELAYED_FROZEN` default that exists
      for paper data fetching". Log the override once at INFO (`live_market_data.override`) so it
      is never invisible.
    - Returns `REALTIME` when the operator explicitly set `REALTIME` (any casing).
    - **Raises `LiveMarketDataError`** when the operator explicitly set anything else — including
      an unrecognised string. Message must name the configured value and the remedy.
    - "Explicitly set" is `"ibkr_market_data_type" in settings.model_fields_set` — **verified**:
      `False` when unset, `True` for both an init kwarg and an env/`.env` value.
    - ⚠️ Do **not** call `settings.get_market_data_type_enum()` here. It ends in
      `.get(value, MarketDataTypeEnum.DELAYED_FROZEN)` (`src/config.py:105-107`) — an unknown
      string becomes delayed data with no error. That silent coercion **is** the fallback AC #1
      forbids. Validate the raw string.
  - [x] `resolve_live_use_rth(settings) -> bool`: returns `True`; raises `LiveMarketDataError`
        naming FR4 if `ibkr_use_rth` is `False`. (Default is already `True`, so only an explicit
        opt-out fails — no existing configuration breaks.)
  - [x] `resolve_live_bar_types(raw: Sequence[str]) -> tuple[BarType, ...]`:
    - `BarType.from_str` each entry; wrap `ValueError` into `LiveMarketDataError` naming the bad
      string and showing a valid example (`AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL`).
    - Reject `not bar_type.is_externally_aggregated()` — an `INTERNAL` bar type never reaches the
      IB adapter's `_subscribe_bars`; the DataEngine aggregates it from ticks instead, so the
      operator would get silently different bars. Verified: `is_externally_aggregated()` is `True`
      for `...-EXTERNAL`, `False` for `...-INTERNAL`.
    - Reject duplicates (same bar type twice = two lines burned for one stream).
  - [x] `validate_market_data_line_budget(bar_types, *, budget) -> None`:
    - One streaming subscription = one market-data line. Raise `LiveMarketDataError` when
      `len(bar_types) > budget`, naming **the limit, the requested subscription count, and the
      distinct-instrument count** (AC #4 says "instrument count"; the two differ only when a
      session subscribes to several timeframes on one instrument, and the message must not hide
      that).
    - Called at config-assembly time, i.e. before a socket opens — "fails at startup".
  - [x] `instrument_ids_for(bar_types) -> tuple[str, ...]`: distinct `str(instrument_id)`, stable
        order, for the instrument provider's `load_ids`.

- [x] **Task 3 — `LiveBarObserver` actor (AC: #3, #5, and AC #1's runtime half)**
  - [x] `LiveBarObserverConfig(ActorConfig)` — msgspec Struct, so keep fields JSON-encodable:
        `bar_types: tuple[str, ...]`, `requests_per_second: int = 45`,
        `delayed_data_grace_seconds: float = 120.0`.
  - [x] `build_bar_observer_config(settings, bar_types) -> LiveBarObserverConfig` — the single
        wiring point that pulls `requests_per_second` from `settings.ibkr_rate_limit` (default 45,
        the same 45 as `RateLimiter` in `src/services/ibkr_client.py:143`). Callers use this, never
        the raw constructor, so the discipline has one source.
  - [x] `class LiveBarObserver(Actor)`:
    - `on_start()`: paced dispatch (AC #5). Subscribe up to `requests_per_second` bar types
      immediately; if more remain, schedule the rest in 1-second batches with
      `self.clock.set_time_alert_ns(name, alert_ns, callback=self._dispatch_next_batch)`.
      **Verified**: `Clock.set_time_alert_ns(name, alert_time_ns, callback=..., allow_past=True)`
      takes a per-timer callback in 1.220.0, on both `LiveClock` and `TestClock`.
      Log `live_bars.subscribed` per batch with the count and how many remain.
    - `on_stop()`: cancel any outstanding pacing timer, unsubscribe. Must be idempotent.
    - `on_bar(bar)`: log `live_bars.received` at INFO with `instrument_id`, `bar_type`,
      `ts_event` rendered as an ISO-8601 UTC string, and `close`. That is AC #3's
      "logged with its instrument and timestamp". Increment a per-bar-type counter exposed as
      `received_count(bar_type)` / `total_received` so Story 1.7 and the tests can assert on
      delivery without scraping logs.
    - `on_bar(bar)` freshness guard — AC #1's runtime half. `lag_ns = bar.ts_init - bar.ts_event`;
      if `lag_ns` exceeds `bar_interval + delayed_data_grace_seconds`, log
      `live_bars.delayed_data_suspected` at **ERROR** with the measured lag, the threshold and the
      instrument, set `delayed_data_suspected = True`, and call
      `self.shutdown_system(reason)` — fail closed. Guard fires **once** (latch), so a downgrade
      does not produce one shutdown command per bar.
      - Rationale and the exact numbers are in Dev Notes → "Why a freshness guard is the only
        runtime signal available". Read it before tuning the threshold.
      - Guard against `ts_init < ts_event` (never expected; clamp to 0 rather than reporting a
        negative lag).

- [x] **Task 4 — wire it into the node builder (AC: #1, #2, #3, #4)**
  - [x] RED: extend `tests/component/core/test_live_node_builder.py`.
  - [x] `build_trading_node_config(settings, *, trader_id, bar_types=(), cli_flags=None)`:
    - New keyword-only `bar_types: Sequence[str] = ()`. The default keeps every existing call site
      and test working unchanged.
    - Order inside the function is a contract, same as Story 1.3's gate-first rule:
      **gate → trader_id → account → timeouts → market-data resolution → line budget → client
      configs**. Nothing that can fail may run after a client config is constructed.
    - Data client config gains:
      `market_data_type=resolve_live_market_data_type(trading_settings)`,
      `use_regular_trading_hours=resolve_live_use_rth(trading_settings)`,
      and `instrument_provider=InteractiveBrokersInstrumentProviderConfig(load_ids=frozenset(...))`
      when `bar_types` is non-empty.
      ⚠️ The adapter's field is `use_regular_trading_hours`; `ibkr_use_rth` is *our* settings name.
      Do not rename either.
    - The instrument provider must load the contracts, or `_subscribe_bars` logs
      "instrument not found" and returns — a silently dead subscription
      (`adapters/interactive_brokers/data.py:248-254`).
  - [x] `build_trading_node(...)` gains the same `bar_types` pass-through plus
        `bar_observer: LiveBarObserverConfig | None = None`. When given, translate it into an
        `ImportableActorConfig` on `TradingNodeConfig(actors=[...])` — **verified**:
        `TradingNodeConfig` has an `actors` field. Declarative wiring keeps the config
        serialisable and lets the kernel own the actor's lifetime; do not reach into
        `node.trader.add_actor()` from the builder.
  - [x] Update the module docstring: `live_node_builder.py` currently says REALTIME/RTH belongs to
        "Story 1.5". It now owns them — fix the line rather than leaving a stale pointer.

- [x] **Task 5 — repair the existing component-test helper (regression guard)**
  - [x] `_settings()` in `tests/component/core/test_live_node_builder.py` passes every field the
        module reads, explicitly (its docstring says so). Add `market_data_type` and `use_rth`
        parameters so the new reads are equally explicit.
  - [x] ⚠️ Passing `market_data_type="DELAYED_FROZEN"` as an init kwarg marks the field **set**,
        which is now a hard failure. Default the helper's parameter to `None` = "leave unset"
        (the override path), and pass an explicit value only in tests that want the refusal.
        Getting this wrong turns all 38 existing tests red for the wrong reason.

- [x] **Task 6 — diagnostic probe + QA procedure (AC: #1, #2, #3)**
  - [x] `scripts/diagnostics/live_bars_probe.py`, modelled **exactly** on
        `scripts/diagnostics/live_node_probe.py` — same `RESULT: ok|fail ...` line, same exit-code
        table, same explicit-loop discipline (see "The probe loop rule" in Dev Notes; it cost a
        live-run failure in Story 1.3).
    - Args: `--bar-type` (repeatable, default `AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL`),
      `--run-seconds` (default 150 — over two 1-minute bars — minimum 1).
    - Builds the node with `bar_types` + the observer, waits for both engines to connect (reuse
      1.3's poll, do not sleep blindly), runs, then reports the observer's per-bar-type counts.
    - `RESULT: ok` **only** when at least one bar was received. Zero bars is
      `RESULT: fail reason=no_bars ...` — Story 1.3's review established that "nothing raised" is
      not evidence, and the same applies here.
    - Read-only: it subscribes to data. It must never construct an order, and Epic 1 has no order
      path to reach.
  - [x] Append **Procedure P2** to `docs/qa/phase3-live-verification.md`, in P1's exact shape:
        Introduced by / Verifies / Tool / Preconditions (incl. "run during RTH — outside RTH with
        `use_rth=True` no bars will close, which is a precondition failure, not an AC failure) /
        Command / Expected output / failure table / Pass criteria / Result log.
  - [x] Run it against the local paper Gateway and record the outcome honestly in the Result Log —
        including "ran outside RTH, no bars, precondition not met" if that is what happened. A dry
        run is not evidence (that file's own rule).

- [x] **Task 7 — verification sweep**
  - [x] `uv run ruff check .` clean; `make format`; `make typecheck` clean.
  - [x] `make test-unit`, `make test-component` — report counts against Task 0's baselines.
  - [x] `uv run pytest tests/integration/core/test_live_node_lifecycle.py --forked` in isolation
        (see "Known test-infra hazard" before running the whole integration tier).
  - [x] Record every deferred finding in `_bmad-output/implementation-artifacts/deferred-work.md`
        under `## Deferred from: story-1.5 (<date>)`.

## Dev Notes

### What this story is, and where its edges are

This story makes a node **see** market data. It does not make anything **act** on it.

Owns:
- The market-data half of the node's data-client configuration: type, RTH, instrument loading.
- A minimal observing actor that subscribes, counts and logs bars.
- The startup checks that make a mis-provisioned session fail before it opens a socket.

Does **not** own:
- Order submission. Epic 1 has no order path. **If you find yourself needing one, STOP and
  escalate** — that is Epic 3, and it is gated on Epic 2's `trader_id`.
- Node lifecycle (`build`/`run`/`stop`/`dispose`) — Epic 2's `live_session_runner.py`, AR38.
  The probe script drives a lifecycle because it is a diagnostic, not the runner. Do not
  generalise it into one.
- Indicator warm-up from history (`request_bars` before subscribe) — Epic 4, Story 4.4.
- Connection-loss detection and the trading-permission flag — Story 1.6, immediately after this.
- The account-prefix check after connect — Story 1.4. It is `backlog` and may land in parallel;
  do not implement it here and do not assume it exists.
- The `ntrader live check` CLI — Story 1.7. This story delivers what that command will print.

### The two silent fallbacks this story exists to close

Both are real, both are in code you can read today, and AC #1 is aimed at them.

1. **Ours.** `IBKRSettings.ibkr_market_data_type` defaults to `"DELAYED_FROZEN"`
   (`src/config.py:84-86`) — correct for the catalog fetcher it was written for, wrong for a live
   session. Worse, `get_market_data_type_enum()` ends in
   `market_data_map.get(value.upper(), MarketDataTypeEnum.DELAYED_FROZEN)`: a typo like
   `REALTIEM` becomes delayed data with **no error at all**. A live path that called that helper
   would be running the exact silent downgrade the AC forbids.

2. **Nautilus's.** IB error **10167** — "Requested market data is not subscribed. Displaying
   delayed market data." — is in `WARNING_CODES`
   (`adapters/interactive_brokers/client/error.py:36`), so the adapter logs it and continues.
   `process_market_data_type` (`client/market_data.py:652-656`) likewise only downgrades a `debug`
   to a `warning` when TWS reports a non-REALTIME type. Neither is published on the message bus,
   raised, or exposed on any object we can poll. There is **no supported hook** in 1.220.0 that
   turns a broker-side entitlement failure into an exception.

Closing (1) is configuration work and is exact. Closing (2) needs the freshness guard below.

### Why a freshness guard is the only runtime signal available

Since the adapter will not tell us, the observer has to notice. The usable signal is the delivered
bar's own lag, `bar.ts_init - bar.ts_event`. Three facts from the 1.220.0 source make this sound:

1. **Backfill bars never reach the actor**, so the guard cannot false-positive on them.
   `subscribe_historical_bars` requests ~300 bars with `keepUpToDate=True`
   (`client/market_data.py:445-480`), but `_process_bar_data` drops every one of them:
   `if start and bar_ts_init < start: return None` (`:1157-1159`), and `start` is set to
   `self._clock.timestamp_ns()` at subscribe time. Only forward-going bars are delivered.
   *(This is also why you must not pass a `start_ns` param on `subscribe_bars` in this story — it
   would let backfill through and the guard would fire ~300 times.)*
2. **Live bars carry a wall-clock `ts_init`.** On the live path `ts_init = self._clock.timestamp_ns()`
   (`:1161`); the `historical` branch that overwrites it (`:1175-1176`) is unreachable for
   delivered bars, per (1).
3. **Publication follows the close within seconds, with a bounded worst case.** With
   `handle_revised_bars=False` (the adapter default) a completed bar is published as soon as the
   next bar's first update arrives (`:1163-1168`); when nothing arrives, the fallback
   `_schedule_bar_completion_timeout` publishes at `bar_duration + 1s` (`:1048-1073`). So the
   worst-case honest lag is one bar interval plus a second.

Hence the threshold: **`bar_interval + delayed_data_grace_seconds` (default 120 s)**.

**State the limit honestly in the code comment and in the Completion Notes**: IB's delayed feed
runs ~15 minutes (900 s) behind, so this separates real-time from delayed only while
`900 > interval + grace`, i.e. for bar intervals below about 13 minutes. It covers the 1-minute
and 5-minute timeframes a paper session actually runs. For hourly bars the feed delay is smaller
than one bar period and no lag threshold can distinguish them — for those, the configuration
requirement plus the operator's log check (IB code 10167 / "Market DataType is DELAYED…") is the
control. Do not pretend otherwise, and do not shrink the grace to chase hourly coverage; you will
only manufacture false shutdowns.

### `model_fields_set` — the mechanism behind "explicitly overriding the default"

AC #1 says *overriding the default*, not *requiring the operator to set REALTIME*. Those differ,
and the difference matters operationally: `.env` is hook-protected in this repo and you cannot add
a key to it, so a design that demanded `IBKR_MARKET_DATA_TYPE=REALTIME` would break the live
verification you are supposed to run.

So: unset → override to REALTIME and log it. Explicitly set to something else → refuse loudly.
`settings.model_fields_set` distinguishes the two, and this was verified empirically rather than
assumed:

| how the value arrives | `"ibkr_market_data_type" in model_fields_set` |
| --- | --- |
| not set anywhere | `False` |
| init kwarg | `True` |
| environment / `.env` | `True` |

### Trap: the adapter field is `use_regular_trading_hours`, not `ibkr_use_rth`

`InteractiveBrokersDataClientConfig.use_regular_trading_hours` (`config.py:228`) is what reaches
IB's `useRTH`. It governs **both** subscription paths — 5-second real-time bars
(`data.py:256-261`) and every other interval via keep-up-to-date historical bars
(`data.py:262-269`) — so one setting covers AC #2 for every timeframe.

Note also that the adapter's own default for `market_data_type` is already `REALTIME`
(`config.py:229`). Passing it explicitly is still required: AC #1 asks for the value to be
*explicit*, and relying on a third-party default for a data-integrity property is exactly how
a future adapter upgrade turns into a silent behaviour change.

### Trap: `EXTERNAL` aggregation or the subscription goes somewhere else entirely

`AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL` does not reach the IB adapter at all — the DataEngine
aggregates it locally from ticks. The operator gets bars, they just are not the venue's bars, and
nothing warns. Reject non-EXTERNAL bar types at parse time with a message that shows a correct
example. Verified: `BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL").is_externally_aggregated()`
is `True`; the `INTERNAL` form is `False`.

Price type maps to IB's `whatToShow` via `parsing/data.py:49-59`: `LAST → TRADES`,
`BID/ASK/MID → BID/ASK/MIDPOINT`. Use `LAST` for equities.

### Trap: an unloaded instrument is a silently dead subscription

`_subscribe_bars` (`data.py:248-254`) looks the contract up in the instrument provider and, on a
miss, logs `Cannot subscribe to bars for …: instrument not found` and **returns**. No exception,
no retry, no bars — forever. That is why `load_ids` must carry every instrument the observer will
subscribe to. Verified: `InteractiveBrokersInstrumentProviderConfig(load_ids=frozenset(["AAPL.NASDAQ"]))`
accepts plain strings.

### The pacing discipline (AC #5) — what "existing" means here

The 45 req/s rule lives in `RateLimiter` in `src/services/ibkr_client.py:53-102`, hardcoded at
`RateLimiter(requests_per_second=45)` (`:143`), and is typed on `IBKRSettings.ibkr_rate_limit`
(default 45). The Nautilus IB adapter has **none** — grepping the whole adapter package for
`rate_limit|throttl` returns nothing — so live subscribe/request traffic is unpaced unless we pace
it.

Do **not** import `src.services.ibkr_client` from `src/core/` to reuse the class: it drags in the
Nautilus historical client and its import-time weight, and `RateLimiter.acquire()` is `async`
while `Actor.on_start()` is synchronous. Pace with the Actor's own clock instead
(`set_time_alert_ns(..., callback=...)`), taking the rate from `settings.ibkr_rate_limit` through
`build_bar_observer_config` so there is still exactly one number in play.

A default 100-line budget against a 45/s rate means a full session is three batches — this is not
a hypothetical.

### The probe loop rule (learned the expensive way in Story 1.3)

`TradingNode.dispose()` calls `loop.stop()` whenever it finds the loop running
(`nautilus_trader/live/node.py:451-458`). Calling it from inside a coroutine still executing on
that loop — which a bare `asyncio.run(...)` wrapper guarantees — stops the loop mid-flight and
`asyncio.run`'s cleanup then raises `RuntimeError: Event loop stopped before Future completed.`
**after** everything actually worked. The live_node_probe now owns an explicit
`asyncio.new_event_loop()` end to end and calls `node.stop()` → `node.dispose()` only once
`run_until_complete` has returned control. Copy that structure exactly; do not re-derive it.

Also copy: the `finally`-driven shutdown (a node that failed to connect still holds a socket and
the kernel's non-daemon `ThreadPoolExecutor`, which will hang the process at exit), the
`KeyboardInterrupt` handler, and passing the loop explicitly to `build_trading_node()`.

### Known test-infra hazard — read before running the integration tier

Collecting `tests/integration/api/test_trades_api.py` in the same
`pytest -n auto --forked` run as any test that constructs a real `TradingNode` crashes the latter
with `SIGTRAP`. Cause: `src/api/web.py:22-23` runs Nautilus `init_logging()` as a module-import
side effect, imports happen once per xdist worker at collection time, and `--forked` isolates only
the test call — so forked children inherit `is_logging_initialized() == True` without the native
threads that state depends on. Pre-existing, documented in `deferred-work.md`, out of scope here
(`src/api/**` is off-limits for this story).

**Practical consequence:** run `tests/integration/core/test_live_node_lifecycle.py` on its own.
`tests/integration` also carries a pre-existing 23-test failure baseline in
`test_backtest_catalog_integration.py`, `test_backtest_runner_integration.py`,
`test_backtest_runner_yaml.py` and `test_kraken_backtest.py` — unrelated to this story. Do not
report those as regressions, and do not "fix" them here.

### Test tier placement

- **Unit** (`tests/unit/test_ibkr_config.py`) — the new settings field only. The unit tier is
  defined as "no Nautilus" (`pytest.ini:25`); `src/core/live_market_data.py` imports
  `nautilus_trader.model.data`, so nothing else from this story belongs there.
- **Component** (`tests/component/core/test_live_market_data.py`, plus additions to
  `test_live_node_builder.py`) — everything else. Config assembly and `BarType` parsing do not
  touch the Nautilus C logging subsystem, which is what keeps the parallel non-forked tier safe.
  **Copy the `_assert_c_logging_state_is_unchanged` autouse fixture** from
  `test_live_node_builder.py:36-62` into the new file: it asserts the C-logging *delta*, not the
  absolute state, because other files in the tier do initialise it under `-n auto`.
- **Integration** (`tests/integration/core/test_live_node_lifecycle.py`, `--forked`) — anything
  that constructs a `TradingNode`. One test is enough: a node builds with the observer wired
  through `actors=[ImportableActorConfig(...)]` and the actor is present on the trader. No broker
  (NFR32).
- Use `TestClock` to drive the paced-dispatch test; `set_time_alert_ns` accepts a callback on
  `TestClock` too, so the batching is fully deterministic without a live loop.

### Testing the freshness guard without a broker

Construct a `Bar` directly with `ts_event` and `ts_init` you choose, register the observer against
test doubles, call `on_bar` and assert: (a) under threshold → counter increments, no shutdown;
(b) over threshold → `delayed_data_suspected` is `True`, ERROR logged, `shutdown_system` called
**once** even across several offending bars. Patch/spy `shutdown_system` — do not actually publish
a shutdown command into a shared bus.

### Files you must not touch

- `.env`, `.env.example` — Write/Edit-protected by `.claude/hooks/protect-files.sh`. Stories 1.1
  and 1.2 each needed a one-off human approval for these. If wording changes are needed, record
  them as a follow-up rather than attempting a write.
- `docker-compose.yml`'s gateway service, `NTRADER_REAL_MONEY_ACCOUNT`, anything `--real-money`.
- `src/core/live_gate.py` — the two-factor gate is not weakened, extended or re-ordered by this
  story. It only gains an earlier caller.
- `src/api/**` — see the test-infra hazard above.
- The reconnect/kill diagnostic scripts.

### Previous story intelligence (1.3, and 1.2 before it)

- **Ordering is a contract, not an outcome.** Story 1.3 proved gate-first with a monkeypatched
  sentinel that raises if a client config is constructed, plus a meta-test proving the sentinel
  can actually fire. Your new startup checks sit inside that same ordering; test it the same way,
  and keep the meta-test habit — a guard that cannot fail is worse than no guard.
- **A malformed `trader_id` aborts the process in Rust** (exit 134, uncatchable). Already guarded
  in `_validate_trader_id`. Do not remove it, and be alert for the same shape in `BarType` /
  `InstrumentId` parsing — wrap `from_str` calls rather than letting arbitrary strings through.
- **`mask_account()` is the only way an account identifier may appear in output** (NFR26). Your
  new log lines carry instruments, not accounts — keep it that way.
- **`model_copy(update=...)` validates nothing** — a typo'd key is silently accepted. The builder
  already uses it for `ibkr_read_only`; do not add a second one.
- **Both IB clients share `ibkr_live_client_id` deliberately** (`get_cached_ib_client` is keyed on
  host/port/client_id, so it is one socket). Do not "fix" it.
- `ibkr_read_only` is a declaration of intent only — the gate is the control (NFR27/AR43). An AST
  test in `test_live_node_builder.py` asserts it is never branched on. Adding a branch on it will
  turn that test red, correctly.

### Git intelligence

Last five commits are Epic 1 work: `6530b53` (client-id docs), `a67a0ee` (story 1.3 record),
`64691ce` (the node builder itself), `3a3d41b` (CLAUDE.md correction), `52b363b` (commit-gate
hook scoping). Conventions to match: `<type>(<scope>): <subject>`, no AI references, `feat(live)`
or `feat(epic1)` scope for source changes, `docs(bmad)` for artifact updates.

### Project Structure Notes

Matches the architecture's planned tree (`architecture.md:500-508`), which lists REALTIME + RTH
settings under `live_node_builder.py`. `live_market_data.py` is a new sibling and a deliberate
split: the builder is already 251 lines and the file limit is 500, and the observer is an actor
(runtime), not configuration. Both stay under `src/core/`, neither imports SQLAlchemy, neither
imports `src.services.ibkr_client`.

Size limits from CLAUDE.md apply: files < 500 lines, functions < 50, classes < 100.

### References

- Story ACs: `_bmad-output/planning-artifacts/epics.md:611-642`
- FR3/FR4: `_bmad-output/planning-artifacts/prd.md:794-795`
- NFR15/NFR16/NFR30: `_bmad-output/planning-artifacts/epics.md:133-134,157`
- Market-data decisions (D5 bullet): `_bmad-output/planning-artifacts/architecture.md:319-321`
- Source-tree plan: `_bmad-output/planning-artifacts/architecture.md:500-508`
- Existing builder + gate: `src/core/live_node_builder.py`, `src/core/live_gate.py`
- Settings: `src/config.py:20-151`
- Existing pacing: `src/services/ibkr_client.py:53-102,143`
- Adapter config fields: `.venv/.../interactive_brokers/config.py:189-234`
- Adapter subscribe paths: `.venv/.../interactive_brokers/data.py:247-269`
- Bar delivery / filtering / completion timeout:
  `.venv/.../interactive_brokers/client/market_data.py:445-480,881-944,1048-1187`
- Error-code handling (10167 is a warning):
  `.venv/.../interactive_brokers/client/error.py:36,97-110,125-172`
- QA procedure shape: `docs/qa/phase3-live-verification.md` (Procedure P1)
- Deferred items: `_bmad-output/implementation-artifacts/deferred-work.md:135-260`

## Review Findings

Three adversarial layers ran against the change: an **Acceptance Auditor** (spec + context docs), an
**Edge Case Hunter** (project read access, boundary walk), and a **Blind Hunter** (code only, no
spec). Between them they found two defects that automated tests had passed over, both now fixed with
regression tests that fail without the fix.

### Fixed — Critical

1. **The delayed-data guard would have shut down a healthy session every RTH re-open.**
   The IB adapter keeps `_bar_type_to_last_bar` for the life of a subscription and publishes the
   *previous* bar when a new one arrives, stamped with the wall clock at that moment
   (`client/market_data.py:1141, 1160, 1162-1166`). With `use_rth=True` nothing closes overnight, so
   the first bar of each session republishes yesterday's last bar — a ~17.5-hour apparent delay on
   perfectly correct real-time data, against a 180s threshold. A multi-day paper session, which is
   the entire point of this phase, would have killed itself every morning; the latch made it worse,
   not better, because once is enough. Same shape over a weekend, a holiday, or any subscription
   opened before the open.
   **Fixed two ways, at the root and in depth:** `on_bar` now discards a bar whose `ts_event` is not
   newer than the last seen for that bar type (the republish never reaches the guard at all), and the
   guard additionally requires `delayed_data_consecutive_bars` offenders in a row — a real delayed
   feed is late on every bar, a session boundary on exactly one.

2. **Paced subscription dispatch died silently after the second batch on a real clock.**
   `LiveClock` still lists a fired one-shot timer while its callback runs, so re-arming the same
   name raised `KeyError: 'name' … already contained in 'self.timer_names'` — swallowed by Nautilus's
   timer machinery, no exception, no log line. Batches 1 and 2 dispatched; everything after was
   stranded. `TestClock.advance_time()` pops the timer *before* invoking the callback, so all four
   pacing tests were green against a broken implementation — a test-double/production divergence, and
   exactly the "guard that cannot fail" trap this repo's own Dev Notes warn about.
   The default 100-line budget at 45/s is three batches, so this was the normal case, not an edge.
   **Fixed:** `_SubscriptionPacer` arms a fresh timer name per batch. Verified end-to-end against a
   real `LiveClock` (6 subscriptions at 2/s: 6 of 6 dispatched, previously 4).

### Fixed — High

3. **`ts_event` is the bar's OPEN, not its close.** `_ib_bar_to_ts_event`'s own docstring says so
   (`client/market_data.py:1303-1310`). Every "after its close" claim in the module was overstated by
   exactly one interval, and — more seriously — the documented "blind spot above ~13-minute bar
   intervals" was simply false: it came from leaving the interval in the comparison. Taking it out
   makes the test interval-independent, so the guard works on hourly bars too.
   **Fixed:** `evaluate_bar_freshness` subtracts the interval and reports `delay_past_close_ns`; the
   threshold is now just the grace. Docstrings corrected, including the retraction. A test asserts
   the same delay is reported identically for 1-minute and 1-hour bars.

4. **The observer's `bar_types` and the node's `bar_types` were never reconciled**, so a caller
   naming subscriptions only on the observer bypassed the market-data line budget entirely *and* got
   empty `load_ids` — the exact silent-no-bars failure the story exists to prevent.
   **Fixed:** `_reconcile_bar_types` — an observer alone supplies the set, both supplied must agree,
   and a disagreement is refused rather than silently resolved.

5. **The guard's shutdown was only ever asserted against a stubbed `shutdown_system`.** The
   load-bearing safety action of the story was verified against a lambda.
   **Fixed:** a test now lets the real `Component.shutdown_system` run and asserts a `ShutdownSystem`
   command reaches `commands.system.shutdown` carrying this trader's id — the kernel drops it
   otherwise.

### Fixed — Medium / Low

6. **Bars were counted twice.** A completed bar is published by the completion timeout
   (`client/market_data.py:1075-1098`) *and* again as `previous_bar` (`:1162-1166`), with nothing
   upstream de-duplicating — so `bars=N` inflated the evidence AC #3 rests on. Same fix as (1);
   republishes are counted separately and reported by the probe.
7. **Four bar-type shapes were accepted that can never deliver a bar**: non-time aggregations
   (TICK/VOLUME/VALUE — `spec.timedelta` raises), composite `EXTERNAL@` types (Nautilus subscribes to
   the standard form and publishes the composite one, so the topics never meet), case-variant
   duplicates (which also put an unresolvable lowercase id into `load_ids`), and a bare `str` (which
   `Sequence[str]` admits and would iterate by character). All refused with specific messages.
8. **A negative / `inf` / `nan` grace** stopped the session on its first perfectly fresh bar, or
   raised `OverflowError` from inside the kernel's actor construction. Now refused in both the
   factory and the actor.
9. **`requests_per_second <= 0` was silently repaired to 1.** The kernel rebuilds the config from the
   serialised `ImportableActorConfig` dict, bypassing the factory, so a corrupt value became a
   100-second subscription ramp instead of an error. Now refused.
10. **The probe constructed the node outside its own `try/finally`**, so a raise from
    `_find_observer`, `node.build()` or `create_task` skipped shutdown entirely and would hang the
    process on the kernel's non-daemon thread pool — the exact failure `_shutdown` exists to prevent.
    Also: `_shutdown` now guards `BaseException` (a second Ctrl-C skipped `dispose()`), takes an
    optional `run_task`, uses `isinstance` rather than duck-typing to find the observer, and reports a
    suspected downgrade with its own message instead of the generic "stopped running".
11. **`on_reset` left a pacing timer armed** while `on_stop` cancelled it. Now symmetric.
12. **`build_bar_observer_config` validated nothing it was handed**, deferring every bar-type error to
    kernel build time in a module whose thesis is "fail before a socket opens". Now validates.
13. **`on_stop` unsubscribed bar types it had never subscribed to** when stopped mid-pacing. Now
    tracks what was actually dispatched.
14. **Component tests were environment-sensitive.** `_env_file=None` disables the dotenv file but not
    `os.environ`, and the market-data resolution branches on `model_fields_set` — so an exported
    `IBKR_MARKET_DATA_TYPE` failed four tests. Autouse fixtures now clear the relevant keys in both
    casings; verified by running the tier under a hostile environment.
15. **Documentation accuracy:** three off-by-one citations into `client/market_data.py` corrected; the
    "backfill bars never reach an actor" claim narrowed to what the source actually shows; the
    `load_ids` comment no longer implies it removes the silent-no-bars failure mode (a contract that
    will not qualify is skipped without error — `providers.py:243-265`); the P1 result-log row for
    this story's incidental run demoted from ✅ to ℹ️ so P1's own outstanding re-run stays visible;
    and the story's own stale `--run-seconds` default, config field type and module line count fixed.

### Accepted, recorded rather than fixed

- **CLAUDE.md size limits.** The module was split (`live_market_data.py` policy / `live_bar_observer.py`
  actor) and two cohesive units extracted, bringing every file under 500 lines and
  `LiveBarObserver` from 193 to ~150. Four symbols remain over the per-symbol limits, all
  docstring-dominated and two pre-existing. Recorded in `deferred-work.md` with measurements.
- **Zero-delivery silence.** The guard reports a *late* feed, not a *silent* one. A session whose
  subscription is accepted but never delivers runs on with nothing louder than an INFO line. The
  probe catches it; the runner does not. Deferred to Story 1.6, which owns connection-loss detection
  and the trading-permission flag.
- **Cross-thread state.** `LiveClock` fires timer callbacks from a Rust thread, so `_subscribed` is
  touched from two threads and `_SubscriptionPacer.cancel` is a check-then-act. Not reproduced, and
  Epic 2's runner owns the threading model. Recorded.
- **The line-budget check cannot detect IBKR silently dropping subscriptions** — it only checks a
  list the operator typed against a number the operator typed. That is what NFR16/NFR30 ask for, and
  the honest limit is recorded rather than dressed up.

### Dismissed

- *"`test_the_observer_subscribes_to_the_instruments_the_node_loads` cannot fail"* — correct as
  written, and now genuinely tautological because `_reconcile_bar_types` makes divergence impossible
  by construction. The test that earns the name is
  `test_disagreeing_bar_types_and_observer_are_refused`, added alongside it. Kept as a
  characterisation test rather than deleted.
- *"The probe reports `RESULT: ok` on evidence it has not established"* — partly true and now
  narrowed by de-duplication, but the remaining gap (no assertion that a bar's `ts_event` falls
  inside the run window) is the operator's job in Procedure P2, which lists the four log lines to
  check. Recorded, not code.

## Dev Agent Record

### Agent Model Used

claude-opus-5 (Claude Code)

### Debug Log References

**Test-count baselines (Task 0), all *collected* counts unless stated:**

| Tier | Before | After (post-review) | Delta |
| --- | --- | --- | --- |
| `tests/unit` | 1561 collected | 1566 collected, 1566 passed | +5 |
| `tests/component` | 853 collected | 946 collected, 930 passed + 16 skipped | +93 |
| `tests/integration` | 171 collected | 172 collected, 144 passed / 23 failed / 5 skipped | +1 |

The component delta grew from +64 to +93 during code review: the two Critical defects and the
validation gaps each landed with regression tests that fail without their fix.

The 23 integration failures are the documented pre-existing baseline
(`test_backtest_catalog_integration.py`, `test_backtest_runner_integration.py`,
`test_backtest_runner_yaml.py`, `test_kraken_backtest.py`) — unchanged in count and identity by
this story. `uv run ruff check .` clean; `make typecheck` clean (76 source files).

**Live run against the paper Gateway (2026-08-09):** see Procedure P2's Result Log in
`docs/qa/phase3-live-verification.md`. Pass criteria 1 and 2 met live; criterion 3 blocked by a
closed market (Sunday). Full account there.

### Completion Notes List

**What was built**

- `src/config.py` — `IBKRSettings.ibkr_market_data_lines` (default 100, `ge=1`), the account's
  concurrent market-data line allocation.
- `src/core/live_market_data.py` (new, 278 lines) — the market-data **policy** layer, pure and
  I/O-free: `resolve_live_market_data_type`, `resolve_live_use_rth`, `resolve_live_bar_types`,
  `validate_market_data_line_budget`, `instrument_ids_for`, `LiveMarketDataError`.
- `src/core/live_bar_observer.py` (new, 498 lines) — the **actor** half: `LiveBarObserver`,
  `LiveBarObserverConfig`, `build_bar_observer_config`, the `_SubscriptionPacer` that releases
  subscriptions at a bounded rate, and the pure `evaluate_bar_freshness` lag test.
- `src/core/live_node_builder.py` — `bar_types` and `bar_observer` parameters; the data client now
  carries `market_data_type`, `use_regular_trading_hours` and an instrument provider with
  `load_ids`; the observer is attached declaratively via `ImportableActorConfig`.
- `scripts/diagnostics/live_bars_probe.py` (new) — Procedure P2's tool.
- `docs/qa/phase3-live-verification.md` — Procedure P2.

**How each AC is met**

- **AC #1.** Two independent silent fallbacks are closed, because there are two.
  *Configuration:* the live path resolves `REALTIME` and never calls
  `get_market_data_type_enum()`, whose `.get(value, DELAYED_FROZEN)` turns a typo into delayed
  data with no error. An unset `ibkr_market_data_type` is overridden to REALTIME and the override
  is logged; an explicitly configured non-REALTIME value — including an unrecognised string — is
  refused. The two are told apart by `model_fields_set`, verified to be populated by an init
  kwarg and by an env/`.env` value alike and empty when the default applies. Requiring the
  operator to *set* REALTIME was rejected deliberately: `.env` is hook-protected, so that design
  would have made the story's own live verification unrunnable.
  *Runtime:* Nautilus reports a broker-side downgrade only as a log warning — IB code 10167 is in
  `WARNING_CODES` (`client/error.py:36`) and `process_market_data_type` only escalates debug to
  warning (`client/market_data.py:652-656`); nothing is published, raised, or pollable. So
  `LiveBarObserver` measures each delivered bar's own lag (`ts_init - ts_event`) and, past
  `bar_interval + grace`, logs at ERROR and calls `shutdown_system()` — fail closed. The guard
  latches, so one downgrade is one shutdown, not one per bar.
- **AC #2.** `use_regular_trading_hours` is passed explicitly to the data client, which governs
  both subscription paths (5-second real-time bars and keep-up-to-date historical bars). An
  explicit `ibkr_use_rth=False` is refused. Confirmed live: `Subscribed
  AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL bars`.
- **AC #3.** `LiveBarObserver.on_bar` logs `live_bars.received` with the instrument, bar type,
  `ts_event` as ISO-8601 UTC and the close, and keeps per-bar-type counters so delivery is
  assertable without scraping logs. The actor reaches the node through
  `TradingNodeConfig(actors=[...])`; an integration test proves the kernel resolves the dotted
  paths and registers the actor.
- **AC #4.** `validate_market_data_line_budget` runs during config assembly — before any client
  config exists, therefore before a socket — and its message names the limit, the requested
  subscription count *and* the distinct instrument count, because those two diverge exactly when a
  session subscribes to several timeframes on one instrument.
- **AC #5.** Subscriptions are dispatched in batches of `requests_per_second`, one batch per
  second, via `clock.set_time_alert_ns(..., callback=...)`. The rate comes from
  `IBKRSettings.ibkr_rate_limit` through `build_bar_observer_config`, so the existing 45 req/s
  discipline governs live traffic from one typed field. `RateLimiter` was not reused directly: its
  `acquire()` is a coroutine while `on_start` is synchronous, and importing
  `src.services.ibkr_client` into `src/core/` would drag the Nautilus historical client with it.
  The Nautilus IB adapter has no rate limiter of its own — grepping the whole adapter package for
  `rate_limit|throttl` returns nothing — so without this, live subscribe traffic was unpaced.

**Three findings from reading the 1.220.0 source that shaped the design**

1. **Backfill bars never reach an actor**, which is what makes a lag-based guard viable at all.
   `subscribe_historical_bars` requests ~300 bars with `keepUpToDate=True`, but `_process_bar_data`
   drops every bar older than the subscription instant (`client/market_data.py:1157-1159`). A
   naive freshness check would otherwise have fired ~300 times at every startup. This is also why
   the observer must not pass `params={"start_ns": ...}` — doing so lets the backfill through.
2. **An unloaded instrument is a silently dead subscription.** `_subscribe_bars` logs
   `instrument not found` and returns (`data.py:248-254`) — no exception, no retry, no bars, ever.
   Hence `load_ids`, and hence the probe refusing to print `ok` on zero bars.
3. **`INTERNAL` bar types never reach the adapter.** Nautilus aggregates them locally from ticks,
   so the operator would silently get bars that are not the venue's. Refused at parse time.

**Honesty notes**

- Task 2's tests were written Red-first. Task 3's observer shipped in the same module write as
  Task 2's policy functions, so its tests were authored after the code rather than before. Rather
  than claim a Red phase that did not happen, both load-bearing guards were **mutation-tested**:
  removing the delayed-data latch failed `test_the_guard_latches_so_one_downgrade_is_one_shutdown`
  (3 shutdowns instead of 1), and removing the batching failed two pacing tests. Both mutations
  were reverted and the suite re-confirmed green.
- `test_a_clock_skewed_bar_...` was renamed after the mutation pass: it had been titled as if it
  proved the `max(0, ...)` clamp, but a negative lag can never trip the threshold, so the clamp is
  defensive only and the test verifies what it actually verifies.
- The two positive market-data assertions (REALTIME, RTH) would have passed even if the builder
  passed neither kwarg, because the adapter's own defaults happen to agree today. An AST
  assertion now pins that both are passed explicitly, with a companion test proving that guard can
  fail.
- `scripts/diagnostics/live_bars_probe.py` gained `--host/--port/--account` so it can run in a git
  worktree, where `.env` is absent because it is untracked. These are not a way around the gate:
  `evaluate_gate` still runs on the resulting configuration, and neither `--real-money` nor
  `NTRADER_REAL_MONEY_ACCOUNT` is reachable from the script.
- Five items are recorded under "Deferred from: story-1.5" in `deferred-work.md`, including the
  freshness guard's coverage limit above ~13-minute bar intervals and the fact that the three
  integration tests in `test_live_node_lifecycle.py` skip under `-n auto --forked`.

### File List

**Modified**
- `src/config.py`
- `src/core/live_node_builder.py`
- `tests/unit/test_ibkr_config.py`
- `tests/component/core/test_live_node_builder.py`
- `tests/integration/core/test_live_node_lifecycle.py`
- `docs/qa/phase3-live-verification.md`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

**Added**
- `src/core/live_market_data.py`
- `src/core/live_bar_observer.py`
- `tests/component/core/test_live_market_data.py`
- `tests/component/core/test_live_bar_observer.py`
- `scripts/diagnostics/live_bars_probe.py`
- `_bmad-output/implementation-artifacts/1-5-receive-real-time-rth-bars-for-a-configured-instrument.md`

## Change Log

| Date | Change |
| ---- | ------ |
| 2026-08-07 | Story created — ready-for-dev. |
| 2026-08-11 | Code-reviewed: 3 adversarial layers, 2 Critical + 3 High + 10 Medium/Low fixed, 6 deferred, 2 dismissed. Module split into policy/actor; delayed-feed guard rewritten after `ts_event` was found to be the bar's open and the RTH-boundary republish shown to shut down a healthy session. |
| 2026-08-09 | Implemented Tasks 0-7; status → review. 70 new automated tests (5 unit, 64 component, 1 integration). Procedure P2 added and run live (criteria 1-2 met, 3 blocked by a closed market). |
