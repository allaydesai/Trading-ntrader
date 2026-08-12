# Deferred Work — Phase 3 (Paper Trading)

Non-blocking findings surfaced during code review. Each entry names the source review and the reason
it was not actioned at the time. Archived with the phase; a fresh file opens with the next phase.

## Deferred from: code review of story-1.1 (2026-08-03)

- ~~**Real-money crossing permits a `DU`/`DF` paper account**~~ — **RESOLVED in story-1.4, 2026-08-07.**
  `_evaluate_real_money_crossing` still receives only the two declarations, and deliberately so — ANDing
  the paper conditions into that branch would make the crossing unreachable. The check moved to Layer 2
  instead, exactly as this item's action line specified: `_evaluate_reported_real_money`
  (`src/core/live_gate.py`) refuses `REPORTED_ACCOUNT_IS_PAPER` when the authorized account carries a
  `DU`/`DF` prefix, and refuses `REPORTED_ACCOUNT_NOT_AUTHORIZED` when the connected gateway does not
  name that account at all. Both directions are now tested
  (`tests/unit/core/test_live_gate.py::TestAccountGateRealMoneyCrossing`), and the enforcement seam
  stops the node on either (`tests/component/core/test_live_account_gate.py`).
  `test_real_money_crossing_does_not_evaluate_paper_conditions` is unchanged and still locks in the
  Layer 1 omission it was written for.

- **Invalid `IBKR_TRADING_MODE` dies with a traceback instead of a gate refusal** —
  `ibkr_trading_mode` is `Literal["paper", "live"]` (`src/config.py:23-25`), so `"Paper"`, `"PAPER"`,
  `" paper"`, `"LIVE"`, and `""` are all rejected by pydantic *before* `evaluate_gate` runs. Fail-closed,
  but the operator gets a stack trace rather than the gate's operator-facing refusal message, and the
  whole CLI dies on a capitalization typo. Story 1.1's new `.env.example` and `IBKR_SETUP.md` lines
  actively invite hand-editing this variable. Consider a `BeforeValidator` that normalizes case and
  whitespace, or routing the invalid value into the gate as a refusal. Pre-existing: the `Literal` was
  not introduced by this story.

- **`ENV=dev/qa/prod` env-file selection never reaches nested `IBKRSettings`** —
  `Settings.ibkr` is `Field(default_factory=IBKRSettings)` (`src/config.py:268-270`), and the `_env_file`
  passed to `Settings(...)` (`src/config.py:305-325`) is not propagated into the nested construction, which
  falls back to its own `model_config["env_file"] = ".env"`. Verified empirically: `Settings(_env_file=alt)`
  picks up `log_level` from the alternate file but reads all four gate-relevant IBKR fields from `.env`.
  The gate's entire input surface therefore comes from a different config file than the rest of the app.
  Fix is either threading the selected env file into `IBKRSettings` or documenting that the gate fields
  are `.env`-only. Pre-existing: `src/config.py:268-270` is untouched by this story.

- **`model_dump()` still returns raw account and password in clear** —
  `repr=False` on `tws_password` / `tws_account` / `ntrader_real_money_account` (`src/config.py:32-42`) is
  a display-only guard affecting `__repr__` / `__str__`. Any `model_dump()`, `model_dump_json()`, or
  settings snapshot serialized into a log or session record leaks the value. AC #5 scopes the masking
  requirement to `GateDecision`, which is fully satisfied, so this is outside the story's contract —
  but it becomes live the moment Epic 2 persists session configuration.

- **`(str, Enum)` renders `str(GateMode.PAPER)` as `"GateMode.PAPER"`, not `"paper"`** —
  `src/core/live_gate.py:132-147`. The `str` mixin makes `== "paper"` comparisons work, which is what
  makes the formatting behaviour surprising. The lowercase snake_case values are clearly shaped for
  logging and serialization, and that is exactly where the mixin misbehaves; `StrEnum` (Python 3.11+)
  would give the intended rendering. Deferred rather than patched because Task 4 explicitly specifies
  `(str, Enum)` to match `src/core/sma_logic.py:7` house style — changing it is a project-wide
  convention call, not a story-1.1 fix. Worth settling before Story 1.7 logs a `GateDecision`.

- **Multi-account `TWS_ACCOUNT` is validated on its first element only** —
  `account.upper().startswith(PAPER_ACCOUNT_PREFIXES)` (`src/core/live_gate.py:191`) treats the field as a
  single opaque identifier. `TWS_ACCOUNT="DU1234567,U7654321"` — a shape IBKR advisor/multi-account setups
  produce — passes the prefix check while the configuration also names a real-money account. No delimiter
  rejection, no length bound, no test covering a non-atomic value. Speculative: depends on whether any
  downstream consumer accepts a list, which no current code does.

## Deferred from: story-1.2 (2026-08-04)

- **`DataCatalogService.ibkr_client` reads client settings from `os.environ` directly, not from typed
  settings** — `src/services/data_catalog.py:135-141` builds host/port/client_id out of
  `os.environ.get(...)` with hardcoded string fallbacks and its own inline-comment stripping, bypassing
  `IBKRSettings` entirely. Two consequences: CLAUDE.md's "never hardcode IBKR connection details" rule is
  violated in the one place that actually opens a historical connection, and the new
  `validate_client_ids_distinct` model validator (`src/config.py`) cannot see this path at all — it
  compares settings fields, and this code never constructs settings. A stale `IBKR_CLIENT_ID=10` exported
  in a shell would still put the catalog fetcher on the live session's reserved ID with no error raised.
  Story 1.2 changed the one fallback literal `"10"` → `"1"` to align the unset-env case (AC #5) and
  stopped there: routing this through `settings.ibkr.ibkr_client_id` changes the catalog import path's
  configuration source, which is outside FR5's footprint. The port fallback (`7497`) has the same shape
  and is equally stale — `.env` and `docker-compose.yml` both use `4002` for Gateway. Fix both together.

- ~~**The equality validator cannot see the historical client's rotation range**~~ — **RESOLVED in
  code review, 2026-08-04.** Allay ruled to widen rather than document around it.
  `validate_client_ids_distinct` now rejects any intersection between the historical rotation range
  `[base, base + HISTORICAL_CLIENT_ID_ROTATION_SPAN]` and the reserved pair
  `{ibkr_live_client_id, ibkr_live_client_id + 1}`, in both directions, and `ge=1` was added to both
  fields (client ID `0` is IBKR's master client, which binds manually-entered TWS orders). Verified:
  with the default live ID of `10`, bases `5`–`11` are now rejected and `1`–`4` accepted. Note the
  predicate originally recorded here was wrong — `ibkr_client_id <= ibkr_live_client_id + 1 <=
  ibkr_client_id + 5` tests only whether *reconcile* lands in the window and would have let
  `IBKR_CLIENT_ID=5` through while its range `5–10` swallowed the live ID. `le=999` was deliberately
  **not** added: it was repo convention with no basis in IBKR's limits, and enforces nothing real.
  One coupling to watch — `HISTORICAL_CLIENT_ID_ROTATION_SPAN` in `src/config.py` duplicates
  `IBKRHistoricalClient.connect(max_id_rotations=5)`, because `ibkr_client.py` imports `config.py`
  and the dependency cannot be inverted. If that default ever changes, change both.

## Deferred from: code review of story-1.2 (2026-08-04)

- **`ENV=dev|qa|prod` does not propagate into nested `IBKRSettings`, and `.env.dev` / `.env.qa` still
  set `IBKR_CLIENT_ID=10`** — `Settings(_env_file=".env.dev")` (`src/config.py:353`) does not reach
  `ibkr: IBKRSettings = Field(default_factory=IBKRSettings)` (`src/config.py:302-304`); the factory
  runs with no arguments and falls back to the class's own `env_file: ".env"`. Verified: with
  `ENV=dev`, `get_settings().ibkr.ibkr_client_id` returns `1`, not the `10` in `.env.dev:15`. The
  root cause was already recorded from Story 1.1 and is unchanged; what is new is that `.env.dev:15`
  and `.env.qa:15` now hold a value that *would* be rejected by `validate_client_ids_distinct` if it
  were ever read. Today the collision is invisible because the file is silently ignored — so fixing
  the propagation gap without first correcting these two files will break dev and qa at startup.
  Both files are untracked and local to this machine, so this cannot be fixed in the repo; it is an
  operator action. Docker compounds it: compose interpolation reads only `./.env`, so `ENV=qa` on
  bare metal and `docker compose up` resolve different client IDs from the same checkout.

- **`--client-id 0` is silently swallowed, and two call sites disagree on the idiom** —
  `src/cli/commands/data.py:463` uses `client_id or settings.ibkr.ibkr_client_id`, so an explicit
  `--client-id 0` is falsy, falls back to the configured value, and the confirmation table at
  `data.py:498` then prints the fallback rather than what the operator asked for. No error, no
  warning. `scripts/venue/resolve_venues_ibkr.py:143` uses `is not None` for the same option, so the
  two paths behave differently for the same input. Pre-existing and untouched by Story 1.2. Note
  that adding `ge=1` to the settings fields does **not** fix this: the CLI passes its override
  straight to `IBKRHistoricalClient(client_id=...)` without routing it through `IBKRSettings`, so
  field bounds never see it. The fix is `client_id if client_id is not None else ...` at the call
  site, matching the `resolve_venues_ibkr.py` idiom.

- **`IBKR_CLIENT_ID=` (present but empty) crashes the catalog path with an unattributed
  `ValueError`** — `src/services/data_catalog.py:139-142` calls `os.environ.get("IBKR_CLIENT_ID",
  "1")`, whose default applies only when the variable is *unset*; a set-but-empty value returns `""`
  and `int("")` raises `ValueError: invalid literal for int() with base 10: ''` from inside the
  `ibkr_client` property, naming no variable. The docstring's "default: 1" does not hold for this
  case. `tests/unit/services/test_data_catalog.py:338-355` covers unset (`patch.dict(os.environ, {},
  clear=True)`) but not set-empty. Same shape applies to the `IBKR_HOST` and `IBKR_PORT` fallbacks
  on the adjacent lines. Fold into the `DataCatalogService` typed-settings refactor above rather
  than patching the three literals individually.

- **Configuration errors reach the operator as raw pydantic tracebacks** — `src/cli/main.py:19` and
  `src/db/session.py:11` both call `get_settings()` at module scope, so any settings validation
  failure terminates the process with an uncaught `ValidationError` before argument parsing. Verified
  that even `python -m src.cli.main --help` is unreachable when a client-ID collision is configured.
  Pre-existing structure, newly reachable now that `IBKRSettings` has a raising validator. A single
  try/except at each entry point that prints the validator's message without the traceback would
  cover it — and would also contain the secret-leak exposure noted in the Story 1.2 review, since
  the leak is in pydantic's `input_value=` rendering rather than in the message itself.

## Deferred from: story-1.3 (2026-08-05)

- **`docker-compose.yml:51` hardcodes `READ_ONLY_API: "yes"` on the `ib-gateway` service** —
  harmless today (Epic 1 has no order path), but Epic 3's first submitted order will be refused by
  the Gateway itself if an operator is running the compose-managed gateway rather than a bare
  TWS/Gateway install, regardless of anything `live_node_builder.py` or the safety gate do. Flagged
  in the story's Dev Notes ("The `ibkr_read_only` truth") as a trap worth a day if undiscovered.
  **Action for Epic 3:** either make the compose value configurable per environment or document that
  the compose gateway cannot be used once order submission ships.

- ~~**Collecting `tests/integration/api/test_trades_api.py` alongside any test that builds a real
  `TradingNode` crashes the latter with `SIGTRAP`, in the same `pytest -n auto --forked` run**~~ —
  **RESOLVED 2026-08-11.** Fixed exactly as this entry's action item prescribed: the module-level
  `init_logging()` call moved out of import time into a FastAPI `lifespan` hook
  (`src/api/web.py`), guarded by `is_logging_initialized()` so it neither panics on re-entry nor
  overwrites a guard a legitimate earlier caller already stored. Importing `src.api.web` is now
  inert; logging is claimed only in the process that actually serves.

  **Two corrections to the analysis below, both established by measurement while fixing it:**

  1. **The blast radius was much larger than "any test that builds a real `TradingNode`."** The
     claim that "a `BacktestEngine` built afterwards is unaffected — a backtest is synchronous and
     never touches the broken channel" is **wrong**. Every one of the 23 failures was a
     `BacktestEngine` test dying with the same `SIGTRAP` (signal 5), in
     `test_backtest_catalog_integration.py`, `test_backtest_runner_integration.py`,
     `test_backtest_runner_yaml.py` and `test_kraken_backtest.py`.
  2. **The "pre-existing 23-test failure baseline … is unrelated to this mechanism" is therefore
     also wrong** — it *was* this mechanism, and it is gone. `make test-integration` now reports
     **169 passed, 2 skipped**, identical across four consecutive runs (was: 144 passed / 23
     failed, with a failing set that *changed between runs* because it depended on how xdist
     happened to distribute modules across workers — that nondeterminism was the tell).

  The skip count also dropped 4 → 2: the two `test_live_node_lifecycle.py` tests that used to
  skip with "requires a process where Nautilus logging is not yet initialised" now genuinely run.

  Original analysis, retained because the mechanism description is accurate and worth keeping:
  `test_trades_api.py` does `from src.api.web import app`, and `src/api/web.py:22-23`
  runs Nautilus `init_logging()` as a **module-import-time side effect**. Under `pytest-xdist` +
  `pytest-forked`, module imports happen once in the persistent worker process during collection,
  before any per-test fork; `--forked` only isolates the *test call*, not collection. Every later
  forked child in that worker therefore inherits a process image where
  `is_logging_initialized()` is already `True`, but the native background threads Nautilus's
  logging subsystem depends on do not survive `fork()` (only the calling thread does). A
  `BacktestEngine` built afterwards is unaffected — a backtest is synchronous and never touches
  the broken channel. A `TradingNode` is not: constructing one performs live-environment
  logging/async setup that depends on that channel, and crashes.
  **Verified by bisection** (repeated full runs at `-n 1`, `-n 4`, `-n auto`, and by
  file-subset elimination): `tests/component/core/test_live_node_builder.py` (11 tests, config
  assembly only, never touches `TradingNode`) and `tests/integration/core/test_live_node_lifecycle.py`
  (2 tests) both pass **every** time in isolation and in every file combination that excludes
  `test_trades_api.py`; combined with it, the two integration tests crash deterministically,
  100% reproducible across 7+ runs. ~~The pre-existing 23-test failure baseline in
  `tests/integration` (present before this story, in `test_backtest_catalog_integration.py`,
  `test_backtest_runner_integration.py`, `test_backtest_runner_yaml.py`,
  `test_kraken_backtest.py`) is unrelated to this mechanism and unaffected by it either way.~~
  — struck: see correction 2 above, it was the same mechanism.
  ~~Not fixed here: the story's Dev Notes explicitly forbid touching `src/api/**`.~~ — fixed
  2026-08-11, outside that story's scope bar.

  **Regression guard:** `tests/component/api/test_web_app_logging.py` asserts in a fresh
  subprocess that importing `src.api.web` leaves `is_logging_initialized()` `False`, plus a
  meta-test proving the probe still detects a real `init_logging()` call so it cannot pass
  vacuously. Anyone reintroducing an import-time initialization gets a named failure instead of
  23 unrelated `SIGTRAP` crashes.

  **Lesson worth keeping:** a *changing* failure set across identical runs is the signature of
  collection-order/process-state contamination, not of 23 independent broken tests. The original
  triage read the instability as a stable "pre-existing baseline" and set it aside; re-running
  the tier twice and diffing the failure lists would have surfaced the shared cause immediately.

- ~~**`make test-component`'s documented baseline of 815 and `make test-integration`'s documented
  169 do not match the actual collected counts**~~ — **RETRACTED 2026-08-05 by code review; this
  finding was itself wrong and no action should be taken on it.** It compared *passed* counts
  against *collected* baselines. Task 6 explicitly labelled 815 "today's **collected** baseline",
  and re-measured independently during the review: `pytest tests/component --collect-only` = **826
  collected**, minus this story's 11 new = **815**. `pytest tests/integration --collect-only` =
  **171 collected**, minus this story's 2 new = **169**. Both documented figures were correct all
  along. 799 is the component tier's *passed* count, and 799 passed + 16 skipped = 815 collected —
  the story's own verification table already showed this ("810 passed, 16 skipped" = 826). The
  original entry's action item ("re-baseline rather than propagate the stale figures") would have
  replaced correct documentation with wrong figures, so it is withdrawn rather than left standing
  with a correction appended. Lesson worth keeping: when comparing test counts, state whether the
  number is collected, passed, or passed+skipped — the three diverge by exactly the skip count.

- ~~**Procedure P1 in `docs/qa/phase3-live-verification.md` was not run against a live
  gateway**~~ — **RESOLVED same session, 2026-08-05.** A Gateway became available mid-session;
  see that file's Result Log for the full account. Running it live surfaced a real bug the dry
  run could not have caught: the probe's original `asyncio.run(...)`-wrapped implementation
  called the synchronous `node.dispose()` while still inside a coroutine executing on that same
  running loop, and `TradingNode.dispose()` (`nautilus_trader/live/node.py:451-458`) calls
  `loop.stop()` whenever the loop is running — fatal in that position, `RuntimeError: Event loop
  stopped before Future completed.` — even though the node had already connected, found the
  account, reconciled, and shut every engine down cleanly per the logs. Fixed by having the probe
  own an explicit `asyncio.new_event_loop()` end to end and only call `node.stop()` →
  `node.dispose()` after `run_until_complete` had returned control — the fix lives entirely in
  `scripts/diagnostics/live_node_probe.py`, not in `src/core/live_node_builder.py`, which the bug
  never touched. Re-run twice after the fix: both exit 0, both ending
  `loop.is_running=False`/`loop.is_closed=True`, the second run being AC #6's "no single-use
  carryover" evidence (a fresh process reused `client_id=10` immediately). AC #6 is now fully
  verified, not just tooled-and-ready.

## Deferred from: code review of story-1.3 (2026-08-05)

Non-blocking findings from the three-layer adversarial code review of Story 1.3. Blocking items
(3 decisions, 23 patches) live in that story's `### Review Findings` section instead.

- **`set_nautilus_log_guard` is an unsynchronised check-then-act.** `src/utils/logging.py:127-130`
  does an unlocked `global` read-then-write (`if _nautilus_log_guard is None: _nautilus_log_guard =
  log_guard`), so two threads can each observe `None` and the first guard is dropped. More
  seriously, `NautilusKernel.__init__` performs its own unlocked `if not is_logging_initialized():
  init_logging(...)` — two concurrent constructions can both pass the check and double-initialise
  the C subsystem, which is CLAUDE.md Gotcha #1's process-killing panic. Pre-existing; the new
  module inherits rather than introduces it. **Action:** serialise the construct-and-register
  sequence with a module-level lock, or document the single-threaded precondition explicitly.

- **A `TradingNode` construction that fails after the kernel claims C logging leaves the guard
  unregistered and the subsystem torn down.** `NautilusKernel.__init__` calls `init_logging()`
  (kernel.py:178-252) and only later reaches `_setup_loop()` → `signal.signal(...)` (kernel.py:277,
  :557), which raises `ValueError: signal only works in main thread of the main interpreter` off
  the main thread. Reproduced: the Nautilus banner is emitted, then the exception propagates out of
  `TradingNode(config=config)` before `set_nautilus_log_guard` is ever reached, and the orphaned
  `LogGuard` is dropped on GC — leaving `is_logging_initialized()` back at `False`. Same shape for
  any other post-logging-init failure in the kernel constructor. **Action:** capture and register
  the guard even on partial construction failure, or document that a failed construction disturbs
  process logging state.

- **PARTIALLY RESOLVED in story-1.4** (2026-08-09): Layer 2 refusals *are* now logged, at ERROR,
  with AR41's `gate.refused` event name and the `GateRefusalReason` value
  (`src/core/live_account_gate.py`). The item below still stands for **Layer 1** refusals raised by
  `live_node_builder.build_trading_node_config`, which remain unlogged; Story 1.7 is still the
  natural place to log those as it maps `GateRefusedError` → exit code 3.

- **A gate refusal is logged nowhere in the codebase.** `src/core/live_gate.py` contains **zero**
  logging calls (verified by grep), and Story 1.3's spec deliberately capped that module's logging
  surface at one debug event on success — so the single most operationally interesting event the
  safety gate produces leaves no trace beyond whatever a caller chooses to emit from the caught
  exception. **Action for Story 1.7**, which maps `GateRefusedError` → exit code 3 and is the
  natural place to log the refusal at WARNING with its `GateRefusalReason`.

- **`TradingNodeConfig` is built with no `LoggingConfig` and no explicit timeouts.** Log level,
  file output and stdout behaviour for a live trading session are left entirely to Nautilus
  defaults and are invisible at the call site — in a module whose headline concern is which
  component claims the C logging subsystem. `timeout_connection` (default **60.0s**),
  `timeout_disconnection` and `timeout_post_stop` govern exactly the shutdown behaviour AC #6
  cares about and are unstated. The module docstring lists what it does not own but does not say
  whether these are a decision or an omission. **Action for Epic 2's runner (AR38)**, which owns
  lifecycle and is the right place to set them from typed settings.

- **AC #4's literal wording describes an ordering the Nautilus API does not expose.** The AC asks
  for the log guard to be registered "before any other Nautilus component initialises in-process",
  but `NautilusKernel.__init__` initialises logging at kernel.py:190-226 and then constructs
  `MessageBus` (:337), `Cache` (:348), `Portfolio` (:353), the data/risk/exec engines (:370-448)
  and `Trader` (:469) — all before returning, so every one of them predates any registration the
  caller can perform. The implementation registers at the earliest reachable point, which is what
  Task 4 actually prescribes and what CLAUDE.md Gotcha #1 needs. No code change is warranted.
  **Action:** amend the AC text when Epic 1 is retro'd, so a future reader does not "fix" correct
  code to satisfy an impossible clause.

- **`model_copy(update=...)` validates nothing.** Pydantic v2 writes straight into `__dict__` with
  neither field-existence nor type checking, so `update={"ibkr_read_onlyy": False}` is silently
  accepted (verified) — producing a settings object with a bogus attribute and an unchanged flag,
  with no error and with the AST guard still passing. Inert today because the derived value is
  never read back. Relatedly: `validate_client_ids_distinct` is the only field-interdependency
  validator on `IBKRSettings`, and a caller that builds settings via `model_construct`/`model_copy`
  bypasses it — so overlapping historical/live client IDs (the FR5 isolation Story 1.2 established)
  could reach both live clients unchallenged, since this module never re-checks.

- **Task 1's NFR26 masking sub-assertion is vacuously satisfied.** The subtask required asserting
  "that the message contains only the masked form of any account it echoes", but the only scenario
  tested is `tws_account=""`, whose error message echoes no account at all — so the requirement is
  met by construction rather than by assertion. Not a leak; the checkbox simply claims more than
  the test demonstrates.

## Deferred from: code review of story-1.4 (2026-08-09)

- **Procedure P2 was never run against a live gateway.** `docs/qa/phase3-live-verification.md`
  defines P2 in full but its Result log records `⏳ not yet run`: the story was implemented in a
  detached git worktree with no `.env` (gitignored and hook-protected), and the session's command
  sandbox declined the invocations that would have supplied the connection settings another way.
  Layer 2's logic is covered by 28 unit cases and 31 component tests, but **nothing in the suite
  proves that a real IBKR gateway's `managedAccounts` message reaches
  `InteractiveBrokersClient.accounts()` where `gateway_reported_accounts` reads it** — the whole
  chain was verified by reading the installed 1.220.0 wheel, not by executing it. Same shape as the
  Story 1.3 P1 entry above. **Action:** run P2 from a checkout that has `.env` the next time a paper
  Gateway is available, and record the result. Non-blocking for the story by its own AC wording
  ("when its position is **inspected**"), but it is the only end-to-end evidence that exists.

- **⚠️ Epic 2 blocker: `NautilusKernel.start_async()` offers no hook between "engines connected" and
  "trader started", so AR39's `gate:account` phase cannot be placed by polling.**
  `system/kernel.py` awaits engines-connected → reconciliation → portfolio init → `self._trader.start()`
  inside **one coroutine**. A runner that polls `exec_engine.check_connected()` (the way
  `scripts/diagnostics/live_node_probe.py` does) observes `True` partway through that coroutine and
  then races its continuation. With zero strategies configured — Epic 1's only situation — the race
  is invisible, because `strategy_states()` is `{}` and the ordering guard is vacuously satisfied
  *even when `_trader.start()` has already run*. **The moment Epic 2's runner configures a strategy,
  `STRATEGY_STARTED_BEFORE_ACCOUNT_GATE` will fire nondeterministically** depending on how long
  reconciliation takes. **Action for Story 2.5:** do not call `verify_connected_account` from a
  poll loop. Drive the phase from a real hook — an `on_start`-style kernel callback, a custom
  `Controller`, or by starting the node with no strategies and adding them after the gate returns.
  This is a real finding, not a hypothetical: the guard is correct and fail-closed; the *placement
  mechanism* Epic 1 could offer is not sufficient for a node that has strategies.

- **`READY` does not mean "never started" after a reset, so the ordering guard has a blind spot.**
  `NOT_YET_STARTED_STATES` admits `READY`, but the component FSM allows
  `STOPPED --RESET--> RESETTING --RESET_COMPLETED--> READY` (`common/component.pyx`), and
  `Trader._reset()` resets every strategy. A runner that stops and resets a trader between sessions
  in one process — exactly the "session ≠ process run" model Phase 3 is built on — could therefore
  re-run the gate against strategies that have already traded on the previous, unverified
  connection. Not fixable from the state name alone; it needs the runner to track whether a start
  has occurred. **Action for Story 2.5**, alongside the item above.

- **The ordering guard inspects strategies only, not actors or execution algorithms.**
  `Trader._start()` starts **actors first**, then strategies, then exec algorithms
  (`trading/trader.py`). A running actor subscribing to and acting on live data from an unverified
  account is invisible to `_placement_refusal`. Deliberately not widened in this story: AC #6 is
  worded "before any **strategy** is started", Epic 1 configures neither actors nor exec algorithms,
  and widening the guard would compound the Epic 2 placement problem above. **Action for Story 2.5:**
  decide whether the guard should read "before anything is running" and extend it to
  `actor_states()` / `exec_algorithm_states()` if so.

- **Nautilus prints the raw account identifier to stdout, outside NFR26's reach.**
  `adapters/interactive_brokers/execution.py` logs ``Account `DU…` found in the connected
  TWS/Gateway`` and `client/account.py` logs `Managed accounts set: {…}`, both through the Nautilus
  C logger on every successful connection. This codebase masks everything it renders itself, but a
  full session transcript still contains the account in clear — P1's own 2026-08-05 result-log entry
  quotes it. Pre-existing and third-party. **Action:** a later story should decide whether to route
  Nautilus logging to a file, raise its level, or accept the exposure; P2's pass criterion 3 is
  scoped to `[probe]` lines in the meantime so it is achievable.

- **The paper path does not check that `TWS_ACCOUNT` is among the reported accounts.**
  `_evaluate_reported_paper` verifies every reported account carries a paper prefix but never that
  the *configured* account is one of them; the real-money path does require that membership. The
  gap is closed in practice by the IB execution client, which faults and raises when its configured
  `account_id` is not in `client.accounts()` (`execution.py`) — verified by reading the wheel, but
  not pinned by any test here, so the gate's completeness rests on unverified third-party behaviour.
  Deliberately not added: the two sides normalise differently (Layer 1 permits a lowercase `du…`
  while `live_node_builder._resolve_account` upper-cases), so a naive membership check would refuse
  configurations Layer 1 permits. **Action:** settle the normalisation question first, then decide.

- **`tests/unit/core/test_live_gate.py` is 833 lines, over CLAUDE.md's 500-line file limit.**
  It was already 508 lines before this story. The limit has no stated test-file exemption, but the
  repo has clear precedent against applying it to tests (`tests/component/test_ibkr_client.py` is
  712 lines, `tests/component/core/test_live_node_builder.py` 512). Splitting the Layer 2 decision
  table into its own file was explicitly rejected when the story was written — the Layer 1 and
  Layer 2 truth tables must stay readable side by side, since Layer 2's first act is to run Layer 1.
  **Action:** settle whether the size limit applies to test files at all, project-wide, rather than
  per story.

- **The probe's `if problems: raise ProbeError(...)` is unreachable when an exception propagates.**
  `scripts/diagnostics/live_node_probe.py` computes `problems` inside a `finally` but checks it
  after the `try/finally`, so on any failure path — including the new Layer 2 refusal — a genuine
  unclean shutdown is reported only as a `[probe] shutdown problems: …` line on **stderr**, never in
  the parseable `RESULT:` line. Pre-existing from Story 1.3; the information is not lost, only
  demoted. A Layer 2 refusal also stops the node twice (once in the gate, once in the probe's
  `finally`), which is harmless but produces `InvalidStateTrigger` noise from already-stopped
  components. **Action:** fold the shutdown-problem check into the `RESULT:` line whenever the probe
  is next touched.

## Deferred from: story-1.5 (2026-08-09)

- **The execution client's instrument provider carries no `load_ids`.** The IB adapter builds a
  *separate* `InteractiveBrokersInstrumentProvider` per client, and this story wired `load_ids`
  onto the data client's only — deliberately, since Epic 1 has no order path that needs a
  contract resolved for execution. Observed live on 2026-08-09: `[WARN]
  InteractiveBrokersInstrumentProvider: No loading configured: ensure either 'load_all=True' or
  there are 'load_ids'` is emitted once, alongside the data provider's successful
  `Loaded 1 instruments`. Harmless today and documented in Procedure P3's "Known benign log
  lines" (this story drafted that procedure as "P2"; it was renumbered to P3 on merge, because
  Story 1.4's account-gate procedure landed on P2 first). **Action for Epic 3**, whose first submitted order is the point at which the exec
  client's provider stops being decorative: give it the same `load_ids`, or confirm the adapter
  resolves the contract on demand and drop the warning from the benign list.

- **The delayed-data freshness guard cannot separate real-time from delayed for bar intervals
  above roughly 13 minutes.** The guard trips when a delivered bar's `ts_init - ts_event` exceeds
  `bar_interval + delayed_data_grace_seconds` (default 120s). IBKR's delayed feed runs ~15 minutes
  (900s) behind, so detection holds only while `900 > interval + grace`. For hourly bars the feed
  delay is smaller than one bar period and no lag threshold can distinguish them. Stated in the
  `LiveBarObserver` docstring rather than hidden, and the configuration-level REALTIME requirement
  plus the operator's log check for IB code 10167 remains the control for those timeframes.
  **Action if a session ever runs on hourly bars:** find a different signal (Nautilus exposes none
  today — `process_market_data_type` and error 10167 are both log-only, verified against 1.220.0),
  or accept the configuration check alone and say so explicitly.

- **`IBKR_MARKET_DATA_LINES` is undocumented in `.env.example`.** The new setting defaults to 100
  (IBKR's standard allocation) and is only discoverable from the field description in
  `src/config.py`. `.env` and `.env.example` are Write/Edit-protected by
  `.claude/hooks/protect-files.sh`, and Stories 1.1 and 1.2 each needed a one-off human approval
  to touch them, so no write was attempted here. **Action:** add the key with its default and a
  one-line note that each streaming bar subscription consumes one line, next time those files are
  opened under approval.

- **Procedure P3's third pass criterion is not closed.** (Drafted as "P2" by this story; renumbered
  to P3 on merge.) The procedure ran against the live paper
  Gateway on 2026-08-09 and met pass criteria 1 and 2 (REALTIME requested and confirmed in the
  gateway log, contract qualified and loaded, subscription accepted, no 10167). Criterion 3 — a
  bar actually delivered and logged — could not be met because 2026-08-09 is a Sunday and the
  contract's own `tradingHours` returned `20260809:CLOSED`; with `use_rth=True` no bar can close.
  The probe reported this correctly rather than printing `ok`. **Action:** re-run
  `scripts/diagnostics/live_bars_probe.py` during regular trading hours and record the result in
  that file's P3 Result Log. Not a blocker for the story's automated coverage, which exercises the
  delivery and logging path against Nautilus test doubles.

- **The three tests in `tests/integration/core/test_live_node_lifecycle.py` SKIP under
  `make test-integration`.** Each guards on `if is_logging_initialized(): pytest.skip(...)` — a
  deliberate Story 1.3 decision so that running the file unforked does not fail the second test on
  a bare assertion — but under `-n auto --forked` the xdist worker has usually already claimed C
  logging by the time they run, so all three skip in the full tier. They pass when the file is run
  on its own (verified this session: 3 passed). The practical effect is that this story's new
  observer-wiring test, like the two before it, contributes no signal to a full-tier run.
  **Action:** either give this file a dedicated make target / xdist group so it lands first in a
  clean worker, or replace the skip with a fixture that forces its own process. Not attempted here
  — it changes Story 1.3's tests and its own risk profile deserves a decision, not a drive-by.

## Deferred from: code review of story-1.5 (2026-08-11)

Non-blocking findings from the three-layer adversarial review. Blocking items (2 Critical, 3 High,
10 Medium/Low — all fixed) live in that story's `### Review Findings` section instead.

- **Four symbols exceed CLAUDE.md's per-symbol size limits.** Files are all now under 500 lines —
  the module was split into `live_market_data.py` (policy, 278) and `live_bar_observer.py` (actor,
  498), and `_SubscriptionPacer` / `evaluate_bar_freshness` were extracted as cohesive units, taking
  `LiveBarObserver` from 193 to ~150 lines of body. What remains, measured by AST span:
  `LiveBarObserver` 215 (>100), `build_trading_node_config` 126 (>50, pre-existing at 87 and
  worsened by this story's checks), `build_trading_node` 58 (>50), `resolve_live_bar_types` 82
  (>50), the probe's `_run` 95 and `main` 69, and the pre-existing `IBKRSettings` 142 (>100). All
  are docstring-and-comment dominated rather than dense logic. **Action:** decide as a codebase
  whether the limits count docstrings; if they do, the honest fix for `LiveBarObserver` is to move
  the lifecycle methods behind a thinner facade rather than to delete the explanations, and
  `build_trading_node_config` wants its validation block extracted wholesale.

- **The observer is silent when *no* bar is delivered at all.** The freshness guard reports a *late*
  feed, not an absent one: a session whose subscription is accepted but never delivers (market
  closed, no entitlement, a contract that would not qualify) runs indefinitely with nothing louder
  than an INFO `live_bars.subscribed`. `scripts/diagnostics/live_bars_probe.py` catches it and exits
  non-zero, but the *runner* — what Epic 2 and Story 1.7 will consume — does not. **Action for Story
  1.6**, which owns connection-loss detection and the trading-permission flag and is the natural
  home for a first-bar / staleness watchdog.
  **Merge update (2026-08-11):** Story 1.6 has landed and did *not* close this. It detects a dead
  *socket*, not an absent *bar* — a healthy socket delivering nothing still reads
  `connected=True`, which is the same blind spot from the other side (see the IB-1101 entry under
  "code review of story-1.6" below). **Action now falls to Epic 2's runner**, which owns the poll
  loop both watchdogs would hang off.

- **`LiveClock` fires timer callbacks from a Rust thread, so the observer touches state from two.**
  Verified: a time-alert callback executes on a Rust timer thread, not the main thread. So pacing
  batches 2+ call `subscribe_bars()` and append to `_subscribed` from that thread while `on_stop()`
  drains the same list from the kernel's thread, and `_SubscriptionPacer.cancel` is a check-then-act
  across the same boundary. Not reproduced — it is a timing race, and `ThrottledEnqueuer.enqueue`
  uses `call_soon_threadsafe` for the common path — but the ordering is real. **Action for Epic 2's
  runner (AR38)**, which owns the threading model: either confine dispatch to the loop thread or
  state the single-threaded precondition explicitly.

- **The market-data line budget cannot detect the failure it describes.** It checks a list the
  operator typed against a number the operator typed; IBKR silently dropping subscriptions past the
  account's real allocation is not observable from our side. That is exactly what NFR16/NFR30 ask
  for, so this is recorded as a known limit rather than a defect — but nobody should read the check
  as protection against the broker's behaviour, only against the operator's arithmetic.

- **`load_ids` narrows the silent-no-bars hole rather than closing it.**
  `InteractiveBrokersInstrumentProvider.load_with_return_async` returns `None` on failure and
  `load_ids_with_return_async` skips it (`providers.py:243-265`) — nothing raises and nothing reports
  a partial load, so an id IBKR cannot qualify still yields a connected session with zero bars for
  that subscription. The docstrings now say so. **Action for Story 1.7**, whose `ntrader live check`
  is the natural place to compare loaded instruments against requested ones and report the shortfall.

- **`ntrader live check` should log the gate refusal.** Carried forward unchanged from Story 1.3's
  review: `src/core/live_gate.py` contains zero logging calls, so the most operationally interesting
  event the safety gate produces leaves no trace beyond what a caller emits. Still true after this
  story. **Action for Story 1.7.**

## Deferred from: story-1.6 (2026-08-07)

> **Numbering note.** Story 1.6 drafted its live-verification procedure as "P2"; it was renumbered
> to **Procedure P4** on merge into the Epic 1 integration branch, since Story 1.4's account-gate
> procedure holds P2 and Story 1.5's bars procedure holds P3. Every "P2" below that refers to the
> connection probe means P4 in `docs/qa/phase3-live-verification.md`.

- **Connection-loss detection depends on two *private* attributes of a third-party class.**
  `read_ibkr_connection_status()` (`src/core/live_node_builder.py`) reads
  `InteractiveBrokersClient._is_ib_connected` and `._is_client_ready`. This is not a shortcut — at
  nautilus-trader 1.220.0 there is no public alternative: a socket drop publishes no message-bus
  event (the adapter's watchdog calls the `_degrade` *hook* directly rather than the `degrade()`
  FSM transition, so `is_degraded` stays `False` and no `ComponentStateChanged` is emitted —
  verified by execution), and `is_connected` / `DataEngine.check_connected()` only move on the
  `connect()`/`disconnect()` lifecycle, so they read `True` straight through a dead socket. The
  dependency is mitigated three ways: the read is `getattr`-based and fail-closed, a canary test
  constructs a real client and fails *by name* if either flag moves, and the failure direction is
  "withhold trading permission". **Action:** re-check on every nautilus-trader upgrade, and if the
  adapter ever gains a public connection-status surface or publishes a state-change event, migrate
  to it and delete the canary. Epic 4 should know this before it wires reconciliation into the
  recovery path.

- **`confirm_state_reestablished()` has no caller in production yet.** It is the seam by which
  trading permission is granted, and the whole NFR10 guarantee ("never on reconnect alone") rests
  on the runner calling it only *after* state is genuinely re-established. Today it is called by
  tests and by the P4 probe. **Action for Epic 2's runner (AR38/AR39)** — it belongs at the
  `trading` phase of the startup sequence — **and for Epic 4**, which owns the reconciliation that
  makes the confirmation truthful after a reconnect. A runner that calls it straight after
  observing a live socket would satisfy the type signature while defeating the design.

- **Nothing polls the monitor yet, and the halt deadline is only evaluated inside `observe()`.**
  A monitor that is polled only on state *change* can never notice an outage that simply persists,
  so NFR20's halt would never fire. This is documented in the class docstring rather than enforced.
  **Action for Epic 2's runner:** poll on a fixed interval (the same heartbeat AR32's
  `last_heartbeat_at` uses is the natural carrier), and consider asserting a maximum age on the
  last observation before `trading_permitted` is honoured.

- **Two log event names are not in AR41's enumeration.** `connection.halted` and
  `connection.recovery_refused` were added because NFR20 requires the halt to be *reported* and a
  refused permission grant must be visible; both follow AR41's stated convention (dotted lowercase,
  past tense, session-scoped). **Action at the Epic 1 retrospective:** amend AR41's list rather
  than leaving two production events undocumented in the architecture.

- **`src/core/live_connection_monitor.py` is absent from the architecture's Delta Project Tree.**
  The tree folds connection concerns into `live_session_runner.py`, but AR38 gives the runner
  lifecycle ownership and Epic 2 owns the runner, so the state machine had to precede it — the same
  relationship `live_gate.py` has to the CLI that calls it. **Action:** add the file when
  `architecture.md` is next revised (the post-implementation `docs/agent/` refresh is the natural
  moment).

- **Procedure P4 has no live result.** `docs/qa/phase3-live-verification.md` records it as
  `⛔ not run`: no IB Gateway was listening on any of the four IB ports, and the implementation
  worktree has no `.env` (gitignored, and `.env*` is hook-protected). **Action:** run
  `uv run python scripts/diagnostics/live_connection_probe.py` once a Gateway and a populated
  `.env` are both available and record the result. Non-blocking — AC #1–#5 are proven by 31 unit
  and 12 component tests that require no broker (NFR32/NFR34). Note this sits alongside P1's own
  outstanding `⚠️ re-run required` row from Story 1.3's review, so one Gateway session can clear
  both.

- **The reconnect window is not observable from outside the monitor.** `reconnect_window_seconds`
  is a constructor default (60.0, NFR4) with no accessor; it appears in log lines but a caller
  cannot read it back to, say, size its own poll interval or render it in `ntrader live status`.
  Deliberate — the story declined to add a setting for it — but worth revisiting when Epic 2 builds
  the `health` derivation (AR32), which needs to distinguish `degraded` from `stale`.

## Deferred from: code review of story-1.6 (2026-08-09)

- **IB error code 1101 re-sets `_is_ib_connected` without any resubscription, so
  `read_ibkr_connection_status()` can report a healthy connection on a link whose market data is
  dead.** TWS emits 1100 ("connectivity lost") → `_process_error` clears `_is_ib_connected`
  (`adapters/interactive_brokers/client/error.py:110-116`). The socket to TWS itself stays up, so
  `_eclient.isConnected()` remains `True`. TWS then emits **1101** ("restored — *data lost*")
  before the watchdog's next 1-second tick (`client/client.py:370-375`) and `_process_error` sets
  the flag again — so `_handle_disconnection` never runs, `_degrade()` never clears
  `_is_client_ready`, and `_resubscribe_all()` (reachable only via `_handle_reconnect`) is never
  called. The adapter treats 1101 and 1102 identically even though only 1102 means "data
  maintained". Result: both flags set, reader says `connected=True, "ib socket connected, client
  ready"`, and every subscription has been silently dropped by IB. **Action:** this needs a third
  observable — subscription state — that Epic 1 does not have and does not own. Natural home is
  **Story 1.5** (which owns subscriptions) or **Epic 4** (broker-authoritative state). Until then,
  the two-flag reading is the best available signal and its limit is documented here.
  **Merge update (2026-08-11):** Story 1.5 has landed and does not carry this — its
  `LiveBarObserver` measures bar *lateness*, not resubscription state, and is silent when nothing
  arrives at all (see "The observer is silent when *no* bar is delivered" above, which this item
  meets from the connection side). So the home is **Epic 4**, or Epic 2's runner if it wants an
  interim first-bar watchdog.

- **`scripts/diagnostics/live_node_probe.py` (Story 1.3) builds the node outside its `try/finally`
  and can hang indefinitely against an unreachable Gateway.** Same shape as the defect patched in
  `live_connection_probe.py`: `node.build()` → `get_cached_ib_client` → `client.start()` →
  `Component.start()` calls `_start()` synchronously, which (loop not yet running) runs
  `run_until_complete(_start_async())`; with `_indefinite_reconnect` on by default
  (`IB_MAX_CONNECTION_ATTEMPTS` unset → `_max_connection_attempts == 0`) that loop retries forever
  and never consults `IBKR_CONNECTION_TIMEOUT`. A Ctrl-C out of it skips `_shutdown` entirely.
  **Action:** apply the same fix to `live_node_probe.py` — not done here because it is another
  story's artifact and Procedure P1's evidence is recorded against its current form.

- **AR41's normative event list omits two events this story ships.** `connection.halted` and
  `connection.recovery_refused` are emitted by `src/core/live_connection_monitor.py` and are not in
  `epics.md:241` / `architecture.md#Communication-Patterns`. Both follow AR41's stated convention.
  **Action at the Epic 1 retrospective:** amend the list. (Recorded twice deliberately — once as a
  story judgment call, once as a review finding — because it is the architecture document, not the
  code, that needs the edit.)
