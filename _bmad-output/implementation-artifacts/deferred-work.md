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
  **RESOLVED in story-1.7** (2026-08-11): `live_check.preflight_gate` emits `gate.refused` at
  **ERROR** with `phase="gate:static"`, the `GateRefusalReason` value and the refusal message, on
  the CLI's own path — before any node exists. ERROR rather than the WARNING suggested below,
  deliberately: Layer 2 already emits that event name at ERROR, and one event at two levels is
  worse than either level. Note the residual: a refusal raised by `build_trading_node_config` on a
  path that does **not** go through `preflight_gate` (a future caller, or the diagnostic probes) is
  still unlogged by `live_gate` itself. Closing that properly means logging inside the builder,
  which Story 1.3's spec deliberately declined.

- **A gate refusal is logged nowhere in the codebase.** `src/core/live_gate.py` contains **zero**
  logging calls (verified by grep), and Story 1.3's spec deliberately capped that module's logging
  surface at one debug event on success — so the single most operationally interesting event the
  safety gate produces leaves no trace beyond whatever a caller chooses to emit from the caught
  exception. **Action for Story 1.7**, which maps `GateRefusedError` → exit code 3 and is the
  natural place to log the refusal at WARNING with its `GateRefusalReason`.

- ~~**`TradingNodeConfig` is built with no `LoggingConfig` and no explicit timeouts.**~~ —
  **RESOLVED in story-2.5, 2026-08-21.** `build_trading_node_config` now takes keyword-only
  `logging=` and `controller=` and passes **all six** inherited timeouts unconditionally from
  named `NODE_TIMEOUT_*` constants, each asserted by name in
  `tests/component/core/test_live_node_builder.py`. The six values are today's Nautilus defaults
  deliberately — a zero-behaviour-change guard against a silent upgrade, with a test that fails
  if Nautilus ever moves one. `ntrader live check` passes `None` for both new arguments and is
  unaffected; the session passes `SESSION_LOGGING` (stdout at INFO, **no** Nautilus file sink,
  because this repo's file logging is structlog's). The original text follows. Log level,
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

- ~~**⚠️ Epic 2 blocker: `NautilusKernel.start_async()` offers no hook between "engines connected"
  and "trader started", so AR39's `gate:account` phase cannot be placed by polling.**~~ —
  **RESOLVED in story-2.5, 2026-08-21.** Closed by the third mechanism this item itself names:
  the node is built with **zero** strategies and no declaratively-attached bar observer, so when
  `gate:account` decides the trader holds nothing but the controller; the observer and the
  strategies are registered afterwards, at `subscribe` and `trading`. The ordering is therefore a
  property of the control flow, not of a poll that can race. It requires a `Controller`
  (`src/core/live_session_controller.py`), because `Trader.add_strategy`/`add_actor` **silently
  return** on a running trader without one — proven against a real `Trader` in
  `tests/integration/core/test_session_controller_unlocks_registration.py`, which is also the
  permanent mutation proof. The original text follows.
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

  **Story 2.5 update (2026-08-21) — STILL OPEN, re-pointed at Story 2.6.** The half this story
  owed is done: the runner tracks `trader_started` as its own boolean, set exactly once at the
  `trading` phase, and infers nothing from any `ComponentState` name — pinned by a test that
  fails if any of five state names (`PRE_INITIALIZED`, `STARTING`, `RUNNING`, `RESETTING`,
  `DEGRADED`) appears as a quoted literal in the runner's source. Deliberately **not** all of
  them: `READY` appears in the runner's own docstring (the prose-trips-grep trap Stories 2.1 and
  2.3 both recorded), so the test pins the names a poll would actually compare against, not the
  vocabulary. (Wording corrected at the 2026-08-21 code review — the original note claimed "a
  state name", overstating the guard.) The **underlying** blind spot in
  `NOT_YET_STARTED_STATES` is unchanged. Its real trigger is reusing one `Trader` across
  sessions in a single process, which Story 2.5 forbids by precondition (one process, one
  session, a fresh node each time); Story 2.6, which owns stop, is where that could first stop
  being true.

  **Story 2.6 update (2026-08-21) — STILL OPEN, precondition re-confirmed, not re-pointed.** A
  stop always ends the process: `SessionStopSignals`'s first-signal callback requests
  `node.stop()`, which ends `_serve()` and falls through to `run()`'s `finally` and then to
  `run()` returning normally — there is no code path in this story that resets a `Trader` and
  starts a second session inside the same process. Re-confirmed rather than assumed: nothing
  added here calls `Trader.reset()`, `Component.reset()` or constructs a second
  `LiveSessionRunner` in one process. The blind spot itself remains untouched, open for whichever
  story first reuses a node.

- **The ordering guard inspects strategies only, not actors or execution algorithms.**
  `Trader._start()` starts **actors first**, then strategies, then exec algorithms
  (`trading/trader.py`). A running actor subscribing to and acting on live data from an unverified
  account is invisible to `_placement_refusal`. Deliberately not widened in this story: AC #6 is
  worded "before any **strategy** is started", Epic 1 configures neither actors nor exec algorithms,
  and widening the guard would compound the Epic 2 placement problem above. **Action for Story 2.5:**
  decide whether the guard should read "before anything is running" and extend it to
  `actor_states()` / `exec_algorithm_states()` if so.

  **Story 2.5 decision (2026-08-21) — DECIDED, deliberately NOT widened.** The guard is left
  exactly as Epic 1 wrote it, because the runner's design makes the question moot rather than
  answering it: nothing but the controller is registered when `gate:account` decides, so there
  is no actor and no exec algorithm for a widened guard to inspect. That is a stronger
  guarantee than the widening would have bought, and it is asserted directly
  (`test_no_strategy_is_registered_when_the_account_gate_decides` reads both `strategies()` and
  `actors()` at the moment the verifier is invoked). ⚠️ **A future story that attaches an actor
  declaratively via `TradingNodeConfig.actors` must revisit this** — the kernel adds config
  actors before `trader.start()`, so the guarantee is a property of *this* runner's choices, not
  of the guard.

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

  **Story 2.5 update (2026-08-21) — PARTIALLY closed, and only the visibility half.** The
  session's heartbeat tick now runs a first-bar watchdog: a started session that has never seen
  a bar logs `session.no_bars_observed` at WARNING **once**, after
  `DEFAULT_NO_BARS_AFTER_SECONDS` (300s). The `subscribe` phase separately logs
  `session.instruments` at WARNING for any requested contract IBKR did not qualify, which is
  the shortfall comparison a *session* previously lacked. Both are visibility only: nothing
  changes state, nothing stops, and neither gives **broker-authoritative subscription state**,
  which is what would actually distinguish a dead feed from a quiet market and remains
  **Epic 4's**. This note is repeated verbatim on all four entries that describe this one gap;
  they close together or not at all.

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
  **RESOLVED in story-1.7** (2026-08-11): the check compares `instrument_ids_for(bar_types)` against
  `node.cache.instruments()`, logs `live_check.instruments` at WARNING when any are missing, and
  names them in the operator summary. Scope of the fix: it makes the shortfall *visible to an
  operator running the check*. A **session** (Epic 2's runner) still has no such comparison, and the
  provider still reports nothing — so the underlying adapter behaviour is unchanged.

  **Story 2.5 update (2026-08-21) — PARTIALLY closed, and only the visibility half.** The
  session's heartbeat tick now runs a first-bar watchdog: a started session that has never seen
  a bar logs `session.no_bars_observed` at WARNING **once**, after
  `DEFAULT_NO_BARS_AFTER_SECONDS` (300s). The `subscribe` phase separately logs
  `session.instruments` at WARNING for any requested contract IBKR did not qualify, which is
  the shortfall comparison a *session* previously lacked. Both are visibility only: nothing
  changes state, nothing stops, and neither gives **broker-authoritative subscription state**,
  which is what would actually distinguish a dead feed from a quiet market and remains
  **Epic 4's**. This note is repeated verbatim on all four entries that describe this one gap;
  they close together or not at all.

- **`ntrader live check` should log the gate refusal.** Carried forward unchanged from Story 1.3's
  review: `src/core/live_gate.py` contains zero logging calls, so the most operationally interesting
  event the safety gate produces leaves no trace beyond what a caller emits. Still true after this
  story. **Action for Story 1.7.**
  **RESOLVED in story-1.7** (2026-08-11) — see the story-1.3 review section above for the detail and
  the residual.

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

- ~~**Nothing polls the monitor yet, and the halt deadline is only evaluated inside `observe()`.**~~
  — **RESOLVED in story-2.5, 2026-08-21**, by exactly the carrier this item recommends: the
  session's ~30s heartbeat tick calls `monitor.observe(read_ibkr_connection_status(settings))`
  every time (`src/core/live_session_steady_state.py`). The maximum-observation-age half was
  already closed by Story 1.6's review (`trading_permitted` expires on a stale reading). Note
  what is **not** closed: nothing in Story 2.5 *acts* on the monitor's verdict —
  `confirm_state_reestablished` is deliberately never called, because `reconcile` is a no-op
  placeholder here and granting permission without a real reconciliation is the failure the
  entry below this one warns about. Acting on it is Epic 4's. The original text follows.
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
  `uv run python scripts/diagnostics/live_connection_loss_probe.py` once a Gateway and a populated
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

  **Story 2.5 update (2026-08-21) — PARTIALLY closed, and only the visibility half.** The
  session's heartbeat tick now runs a first-bar watchdog: a started session that has never seen
  a bar logs `session.no_bars_observed` at WARNING **once**, after
  `DEFAULT_NO_BARS_AFTER_SECONDS` (300s). The `subscribe` phase separately logs
  `session.instruments` at WARNING for any requested contract IBKR did not qualify, which is
  the shortfall comparison a *session* previously lacked. Both are visibility only: nothing
  changes state, nothing stops, and neither gives **broker-authoritative subscription state**,
  which is what would actually distinguish a dead feed from a quiet market and remains
  **Epic 4's**. This note is repeated verbatim on all four entries that describe this one gap;
  they close together or not at all.

- **`scripts/diagnostics/live_node_probe.py` (Story 1.3) builds the node outside its `try/finally`
  and can hang indefinitely against an unreachable Gateway.** Same shape as the defect patched in
  `live_connection_loss_probe.py`: `node.build()` → `get_cached_ib_client` → `client.start()` →
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

## Deferred from: story-1.7 (2026-08-11)

- **`--connect-timeout` cannot bound the part of the connect that happens inside `node.build()`.**
  The check starts its deadline *before* the build precisely so the two budgets are not additive
  (an unreachable gateway took 115s to say so when they were), but `client.start()` drives the
  adapter's connect through `run_until_complete` on a loop that is not yet running, and nothing
  interrupts it. So `--connect-timeout 5` against a gateway whose API handshake hangs still costs
  the adapter's own ~15s `managedAccounts` wait before the deadline is even consulted. The check
  mitigates it with `IB_MAX_CONNECTION_ATTEMPTS=1` (one attempt, not the probes' three).
  **Action:** genuinely bounding it means running `build()` in a thread or reworking the adapter's
  synchronous start path — neither belongs in a story that adds a CLI command. Worth revisiting in
  **Epic 2's runner (AR38)**, which owns lifecycle and will want the same bound for a session start.

  **Story 2.5 update (2026-08-21) — STILL OPEN; the runner now inherits it.**
  `LiveSessionRunner` takes its deadline before the node factory call and installs a bounded
  retry budget (`SESSION_CONNECTION_ATTEMPTS = "3"`, deliberately more than the check's `"1"`,
  because a session should outlast a gateway restart where a check should not) — the same
  mitigation, not a fix. `build()` still runs synchronously and uninterruptibly, so
  `--connect-timeout 5` against a hanging handshake still costs the adapter's own
  `managedAccounts` wait first, now up to three times. The component test asserts the *bound* in
  wall clock rather than only the exception, so the size of the overshoot is at least measured.

- **`live_check.classify_failure` couples exit codes to exception class *names*, not classes.**
  Deliberate — importing `GateRefusedError` or `LiveMarketDataError` would make `live_check.py`
  Nautilus-dependent and collapse the pure/impure split that lets AR28's exit-code table be
  unit-tested with no broker. The coupling is pinned by a component test that asserts the real class
  names, so a rename fails loudly. But it is still a string, and a *new* exception type added to
  `live_node_builder` or `live_market_data` will silently classify as a generic error (exit 1) until
  someone adds it to the map. **Action:** if Epic 2 grows more typed failures on this path, consider
  a shared marker protocol (an `exit_outcome` attribute on the exception) instead of a name map.

- **Zero bars is reported, not failed, and the two causes are indistinguishable.** Outside RTH no
  bar can close, so the check exits 0 with the shortfall named; `--require-bars` makes it fail for a
  script that knows it is inside RTH. What the check still cannot tell apart is "market closed" from
  "connected, subscribed, and the feed is dead" — it has no session calendar. The instrument-load
  comparison narrows it (a contract IBKR never qualified is now named), but a qualified contract on
  a dead feed still looks like a quiet market. **Action for Epic 2's runner**, which owns the poll
  loop a first-bar watchdog would hang off — the same gap "the observer is silent when *no* bar is
  delivered" and the IB-1101 item meet from their own sides.

  **Story 2.5 update (2026-08-21) — PARTIALLY closed, and only the visibility half.** The
  session's heartbeat tick now runs a first-bar watchdog: a started session that has never seen
  a bar logs `session.no_bars_observed` at WARNING **once**, after
  `DEFAULT_NO_BARS_AFTER_SECONDS` (300s). The `subscribe` phase separately logs
  `session.instruments` at WARNING for any requested contract IBKR did not qualify, which is
  the shortfall comparison a *session* previously lacked. Both are visibility only: nothing
  changes state, nothing stops, and neither gives **broker-authoritative subscription state**,
  which is what would actually distinguish a dead feed from a quiet market and remains
  **Epic 4's**. This note is repeated verbatim on all four entries that describe this one gap;
  they close together or not at all.

- **`os.environ.setdefault("IB_MAX_CONNECTION_ATTEMPTS", ...)` mutates process-wide state from a
  library module.** It is the only lever the adapter exposes (the value is read once in
  `InteractiveBrokersClient.__init__`), `setdefault` leaves an operator's own choice alone, and the
  alternative is an unbounded hang — so it ships. It is still a global side effect of calling
  `run_live_check`, which matters if a future caller runs the check in the same process as something
  else that builds an IB client. **Action:** revisit if Nautilus ever accepts the budget as config.

- **The check's `live_check.*` log events are not in AR41's normative list.** `live_check.building`,
  `live_check.connected`, `live_check.observing` and `live_check.instruments` are this command's own
  progress vocabulary, deliberately *not* a claim on AR39's startup-phase sequence (Epic 2's
  contract). `gate.static` is likewise a new event name alongside AR41's `gate.refused`.
  **Action at the Epic 1 retrospective:** decide whether AR41's list should enumerate command-scoped
  events at all, or only session-scoped ones. (Third entry of this shape — Story 1.6 added two.)

## Deferred from: story-2.1 (2026-08-17)

- **`StrategyLoader.build_strategy_params` silently drops an override key that is not a field of the
  strategy's param model.** `src/core/strategy_factory.py:296-380` iterates
  `param_model_cls.model_fields`, so `overrides={"fast_perios": 12}` (a typo) produces a spec built
  from the default `fast_period` with no error anywhere — `StrategySpec.from_overrides` inherits this
  unchanged, since it calls the chain rather than reimplementing it. Pre-existing, and affects the
  backtest path identically; widening it is a change to a function shared well outside this story's
  footprint. **Action for Story 2.2's CLI:** decide whether to validate override keys against the
  param model's field set before calling `from_overrides`, so a typo'd `--param` flag fails loudly
  instead of silently taking the default.

## Deferred from: code review of story-2.1 (2026-08-17)

Three adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor); 53 raw findings → 35
after dedup. Every item below was re-verified by execution before being recorded here.

- **`resolve_live_bar_types` accepts a lowercase instrument id.** `src/core/live_market_data.py:205`
  dedups on `str(bar_type).upper()` but appends the original `BarType`, and `BarType.from_str`
  upper-cases only the aggregation — so `aapl.nasdaq-1-minute-last-external` survives resolution
  intact and `instrument_ids_for` yields `frozenset({'aapl.nasdaq'})`. The module's own comment at
  `:199-204` names the consequence: "a lowercase id IBKR will not resolve — a silently dead
  subscription". Pre-existing, and reachable today with a single strategy. Story 2.1's
  `subscription_bar_types` sits directly on top of it. **Action:** decide where the canonical form is
  established — in `resolve_live_bar_types` (fixes every caller at once) or per-caller.

- **`StrategyRegistry.clear()` empties the registry permanently for the life of the process.**
  `src/core/strategy_registry.py:271-288` — `clear()` resets `_discovered=False`, so `get()` re-runs
  `discover()`, but `importlib.import_module` returns the already-cached modules and no
  `@register_strategy` decorator re-fires. Every subsequent lookup of a perfectly valid strategy then
  fails with the actively misleading `Unknown strategy 'sma_crossover'. Registered strategies: .`
  No current test calls `clear()`, but `make test-unit` runs xdist-parallel with shared workers, so
  one future registry-isolating test would poison every co-located test in that worker.
  **Action:** make `clear()` re-register from a retained snapshot, or drop the `_discovered` reset.

- **A strategy module failing to import with anything other than `ImportError` escapes uncaught.**
  `discover()` catches only `ImportError` (`src/core/strategy_registry.py:271-288`) and
  `_lookup_strategy` catches only `KeyError` (`src/models/session.py:69`), so a `SyntaxError` or
  `RuntimeError` at import time propagates out of a pydantic constructor unconverted. 4 of the 7
  registered param models live in `src/core/strategies/custom/`, a git submodule this repo does not
  control, which makes a broken checkout the realistic trigger. **Action for Story 2.2:** decide
  whether spec construction should surface submodule breakage as a `ValidationError`.

- **Re-validating a persisted spec against today's param model is lossy and brittle.**
  `src/models/session.py:103` re-coerces every stored row through the current `param_model` with
  pydantic's default `extra="ignore"`. Consequences, all verified: a param field removed in a later
  build is silently dropped when a **sealed** session's record is read back; tightening any param
  constraint makes previously-sealed sessions unloadable (`fast_period=250` row vs a later `le=200`);
  and renaming or removing a strategy makes every referencing row permanently unloadable.
  `schema_version` cannot mediate any of this — nothing branches on it. **Action for Story 2.2/2.4:**
  decide whether a sealed session's spec is re-validated on read at all, or read as stored.

- **`json.dumps(spec.model_dump())` raises on `Decimal`.** Verified: `TypeError: Object of type
  Decimal is not JSON serializable`. `model_dump()` is python-mode and preserves `Decimal`, so the
  module's "JSON-losslessly round-trippable" property holds only through the
  `model_dump_json()`/`model_validate_json()` pair. **Action for Story 2.2:** persist via
  `model_dump_json()` or `model_dump(mode="json")` — never bare `model_dump()` into a JSONB column.

- **Validator errors carry an empty `loc`.** `src/models/session.py:160` is a model-level
  `mode="before"` validator, so pydantic attaches its errors to the model root: an unknown
  `strategy_id`, an invalid `parameters` value, and a refused `bar_types` entry are indistinguishable
  by location. A CLI mapping `err["loc"][0]` to the offending flag gets `IndexError` on an empty
  tuple. Note `bar_types=()` *does* produce a properly located `too_short`, so the error shape is
  inconsistent depending on which rule rejected the input. Splitting into field validators is not the
  answer — the Dev Notes rejected it for sound reasons. **Action for Story 2.2's CLI:** map on message
  content, or have the model raise with an explicit `loc`. **Related, from the same review:** the CLI
  must catch `(ValidationError, ValueError)`, not `ValidationError` alone — `StrategySpec.from_overrides`
  is a factory that runs `build_strategy_params` before pydantic, so resolution failures surface as a
  plain `ValueError` by design (decided 2026-08-18).

- **`SessionStatus` has no terminal failure state.** `src/models/session.py:46-50` — a session whose
  node died on a Gateway drop records the same `stopped` as one the operator halted deliberately, so
  any Epic 5 comparison filtering on `stopped` silently mixes complete runs with truncated ones, and
  a truncated run is not comparable to a backtest. AC #6 mandates exactly these four values, so this
  is not a Story 2.1 defect. **Decided by Story 2.3 (2026-08-19), not closed:** not a fifth status
  value — Story 2.1's AC #6 pins the enum at exactly four, the `session_status` PG type is created
  with exactly four labels, and this phase's single migration (Story 2.2) is spent, so a fifth value
  now costs a second migration the epic does not have. The decision does not by itself deliver the
  operator-facing "why did it stop" answer, so the item stays open. **Action, re-pointed at Story
  2.8:** Story 2.7 makes a strategy failure visible and Story 2.8 derives `stale` from the heartbeat
  — decide there whether that is sufficient or a separate nullable column is still warranted.

  **~~Answered by Story 2.7 (2026-08-23), one half closed.~~** The column question is settled and was
  settled *here* rather than at 2.8: `trading_sessions.runtime_flags` (nullable JSONB, migration
  `b7c419e2a3d8`) ships with the story that writes it. The reasoning, decided with Allay, is that
  containment **without** persistence is a regression rather than a neutral omission — today
  `os._exit(1)` leaves the row `running` with a frozen heartbeat, which 2.8 renders `stale`; after
  containment the session heartbeats normally and `note_bar` keeps advancing `last_bar_at`, so 2.8
  would render `trading` for a session where every strategy is dead. The cost argument in the
  paragraph above — *"this phase's single migration is spent"* — is therefore **no longer true**, and
  anywhere else it is cited as a reason (see the `SessionReclaimedError` fencing-token item below) it
  must be re-argued on its own merits. **The no-fifth-status half stands unchanged**: a failed
  *strategy* is not a session lifecycle state. What remains open is only Story 2.8's own question of
  how `degraded` covers both senses AR32 names.

  **~~Answered by Story 2.8 (2026-08-24), item fully closed.~~** One derivation, `_is_degraded` in
  `src/core/live_session_health.py`, covers both senses (Judgment call #1). Sense (a) — contained
  strategies — reads the live writer Story 2.7 shipped: `all_failed` or a non-empty
  `failed_strategies`. Sense (b) — connection lost — is a **dormant** reader on the pre-planned
  `connection_lost_at` key; no writer exists yet and none was added, because writing it means
  consulting `ConnectionMonitor`, which stays Epic 4's (the current reader is known to lie under IB
  error 1101). The dormant branch is pinned by a unit test whose docstring says Epic 4 supplies the
  writer, so the day that writer lands, `degraded` lights up with zero changes to this module. The
  precedence question the derivation also had to answer — `stale` outranks `degraded` (Judgment
  call #8: a stale heartbeat means the process is likely dead, and reporting "running but impaired"
  on the strength of flags from a dead run would be the more misleading answer) — is new and not
  something this item asked, recorded here because it is the same derivation.

- **Unit-tier tests transitively load Nautilus.** Every `StrategySpec` construction in
  `tests/unit/models/test_session_spec.py` triggers the lazy `src.core.live_market_data` import, which
  pulls in `nautilus_trader` — contradicting the documented "unit = parallel, no Nautilus" tier rule,
  in the same file whose `TestImportPurity` exists to assert the module does not do this. The tests
  pass in 0.82s and need no broker, network, or DB, so AC #9 is satisfied. **Action:** a project-level
  call on whether the unit tier's "no Nautilus" rule means "no top-level import" or "no import at
  all"; the answer also settles how `TestImportPurity` should be written.

- **Override `SessionSpec.model_copy` to re-validate.** Verified: `sp.model_copy(update={"strategies":
  ()})` yields a `SessionSpec` with **zero** strategies, violating AC #1's non-empty invariant, and the
  same trick sets an unregistered `strategy_id` — `model_copy(update=)` skips both `frozen=True` and the
  `mode="before"` validator. Deferred by decision on 2026-08-18: the hole is now disclosed in the module
  docstring and pinned by a test, and overriding `model_copy` in this model alone would diverge from
  every other pydantic model in `src/models/`. No consumer derives a modified spec today.
  **Action for Story 2.2:** decide alongside the repository write path, where a real mutation path first
  exists and FR14's "immutable for the session's whole life" becomes enforceable rather than advisory.

- ~~**Add a `schema_version` read gate.**~~ — **RESOLVED in story-2.2** (struck 2026-08-21 by
  story-2.5, which read this item and found the work already done rather than re-implementing
  it). `SessionSpec.from_stored` refuses a payload whose `schema_version` is newer than
  `SPEC_SCHEMA_VERSION` or is not an integer, and `extra="forbid"` makes a newer row's unknown
  fields fail loudly. Credit is Story 2.2's; only the strike is Story 2.5's. The original text
  follows. `src/models/session.py:227` records the field but nothing branches
  on it, there is no upper bound (`schema_version=99` loads clean), and pydantic's default
  `extra="ignore"` means a row written by a newer build has its unknown fields silently dropped — so the
  field cannot do the job its docstring claimed. Deferred by decision on 2026-08-18: the docstring is
  narrowed to "recorded so a future reader can branch on it", because a gate written now would guess at a
  read path Story 2.2 has not defined. Note the same review's `extra="forbid"` patch supplies most of the
  intended behaviour for free — a newer row's unknown fields then fail loudly instead of vanishing.
  **Action for Story 2.2:** define the gate when the read path lands, and decide whether an unrecognised
  version is a hard refusal or a best-effort read.

## Deferred from: story-2.2 (2026-08-19)

- **`src/models/trade.py`'s Pydantic `Trade`/`TradeCreate` still declare `backtest_run_id: int`
  (required).** The ORM (`src/db/models/trade.py`) now allows a NULL `backtest_run_id` for a
  session-owned trade (this story's migration and CHECK constraint), but the domain model was
  deliberately left untouched — nothing writes a session-owned trade until Epic 3, and
  `src/api/rest/trades.py` only ever queries `WHERE backtest_run_id == <id>`, so a NULL row can
  never reach this Pydantic model this phase. Widening it now would be Epic 3's change made blind,
  before the shape a session-owned trade actually needs is known. **Action for Epic 3:** widen
  `backtest_run_id` to `Optional[int]` and add `session_id` alongside it when the order-execution
  path starts writing session-owned trades (Story 3.6).

- **`SessionSpec.model_copy(update=...)` still bypasses `frozen=True` and the `mode="before"`
  validator.** Re-recorded from the Story 2.1 review, which pointed the decision at this story's
  repository write path. That path now exists (`SyncTradingSessionRepository.create` /
  `TradingSessionRepository.create`) and takes a `SessionSpec.to_stored()` dict, immediately
  serialised — so a `model_copy`-derived spec still has no way to reach a stored row without
  passing `SessionSpec`'s own validation again on the way through `from_overrides`/construction.
  The hole stays open and disclosed in the module docstring, pinned by
  `TestKnownLimits::test_model_copy_update_bypasses_validation`. **Action for Story 2.4:** revisit
  once the durable engine cache gives a second code path that reads a `SessionSpec` back into
  memory — that is the first place a `model_copy`-derived spec could plausibly originate from.

- **`resolve_live_bar_types` still returns the un-canonicalised `BarType`.** Re-verified against
  the current `src/core/live_market_data.py:154-208`: the dedup key at line ~198
  (`key = str(bar_type).upper()`) is canonical, but `resolved.append(bar_type)` two lines later
  appends the *original* `BarType` — so a single-strategy lowercase entry (e.g.
  `aapl.nasdaq-1-minute-last-external`) still parses, dedups against itself correctly, and is
  returned lowercase. `StrategySpec._resolve_bar_types` (Story 2.1) papers over this for every spec
  built through pydantic by upper-casing the *string form* after the fact, and this story's CLI
  passes `--bar-type` values through that same path — so `ntrader live create` is not exposed to
  it. The root cause is still open for any future caller that calls
  `resolve_live_bar_types` directly rather than through `StrategySpec`. **Action:** unchanged from
  the Story 2.1 review — decide whether to fix it at the root (`resolve_live_bar_types` itself) so
  every caller is covered at once, or continue relying on each caller's own canonicalisation.

## Deferred from: code review of story-2.2 (2026-08-19)

Three adversarial layers produced 40 raw findings; 10 independent skeptics refuted 24 and confirmed
14. No critical, high or medium finding survived verification. The three items below are real but
were judged not actionable inside this story.

- **Both new repository classes have zero CI-gating test coverage.** Every test of
  `TradingSessionRepository` and `SyncTradingSessionRepository` lives in `tests/integration/db/`,
  which `.github/workflows/ci.yml` `--ignore`s on *both* the integration job (:168) and the coverage
  job (:238); the unit-tier CLI tests replace `SyncTradingSessionRepository` with a `MagicMock`, so
  neither repository body nor the `live.py`→repository call contract is verified on a PR. `live.py`'s
  `create()` itself *is* unit-covered, and the uncovered surface is ~44 executable statements, so the
  effect on the `--cov-fail-under=64` gate is negligible. The root cause — the repo-wide `--ignore` —
  is pre-existing and not this story's doing. The story's own Testing Standards are internally
  inconsistent here: they state "anything that must gate a PR belongs in the unit tier" (which is why
  AC #13 forces the migration guard into `tests/unit/db/`), then route all repository tests to the
  ignored directory. **Action:** the repo already has the precedent — four `tests/component/db/`
  in-memory-SQLite repository test files that the CI component job *does* run. Decide whether to add
  component-tier repository tests for the two new classes, or to stop `--ignore`ing
  `tests/integration/db` and give CI a Postgres service for it. Owner: Epic 2 retro.

- **`--compare-to` accepts a failed or metric-less backtest run.** `SyncBacktestRepository
  .find_by_run_id` (`backtest_repository_sync.py:220-239`) filters on `run_id` only, and
  `src/services/backtest_persistence.py:201-213` does persist runs with `execution_status='failed'`
  and no `PerformanceMetrics` row. So `ntrader live create --compare-to <failed-run-id>` succeeds and
  freezes, for the life of a multi-week forward test, a comparison target that Epic 5's
  distributional comparison cannot compute against. AC #12 asks only for existence ("naming a
  `run_id` that does not exist"), so this is not an unmet AC. **Action:** decide in Epic 5, when the
  comparison actually runs, whether `--compare-to` should additionally require
  `execution_status == 'success'` and the presence of metrics — and whether that check belongs at
  `create` (fail early, but the operator may legitimately link a run they intend to re-run) or at
  seal.

- **ORM↔migration agreement is guarded by no test.** The test fixtures build schemas from
  `Base.metadata.create_all`, never from the migration chain, so the two can disagree silently —
  stated verbatim in `tests/integration/db/test_migration_schema.py`'s own docstring, which exists
  because that drift already happened once. For this story the two *do* agree (verified by hand
  against the live database: index names, uniqueness flags, column set, enum labels and the CHECK all
  match the ORM), but nothing keeps them agreeing. This is the acknowledged repo-wide blind spot the
  story's Dev Notes call out, not a defect introduced here. **Action:** consider a single test that
  diffs `Base.metadata` against a freshly-migrated scratch schema via alembic's `compare_metadata`,
  seeded with an allowlist for the two known pre-existing drifts
  (`idx_trades_backtest_run_id` vs `ix_trades_backtest_run_id`, and the two ORM-only
  `backtest_runs` indexes). Owner: Epic 2 retro.

## Deferred from: story-2.3 (2026-08-19)

Closes none outright — the `SessionStatus` terminal-failure item above is *decided* (not a fifth
status value) but not closed, since the decision does not by itself deliver the operator-facing
answer; it stays open, re-pointed at Story 2.8.

- ~~**`live_check.classify_failure` (deferred-work.md, "Deferred from: story-1.7") will classify
  `InvalidSessionTransition` generically until a CLI story maps it.**~~ — **RESOLVED in
  story-2.5, 2026-08-21**, together with the broader entry in story-2.4's section, which
  supersedes this one. The original text follows. This story's new exception is a
  typed failure on the live path, and the classifier keys on exception class *name*, so a `stopped`
  session refused for `running` currently maps to no exit code at all — nothing on the live path
  calls `transition()` yet. Harmless today. **Action for Story 2.5/2.6:** map
  `InvalidSessionTransition` deliberately, at exit code **1** — AR28 reserves 3 for gate refusal and
  4 for connectivity, and a session-state conflict is neither.

- **Re-validating a persisted spec against today's param model (deferred-work.md, "Deferred from:
  story-2.1") is not triggered by this story, and stays that way.** `transition()` and `resolve()`
  return the ORM row and never call `SessionSpec.from_stored()`. Keeping it that way matters: the
  moment this service starts materialising typed specs, it inherits the brittleness the original item
  describes. Re-pointed at Epic 5 by Story 2.2; unchanged here.

- **`SessionSpec.model_copy(update=...)` still bypasses validation (deferred-work.md, "Deferred
  from: story-2.2").** Untouched — no spec is constructed anywhere in `session_service.py`.
  Re-pointed at Story 2.4 by Story 2.2; unchanged here.

- **Both trading-session repositories still have zero CI-gating coverage (deferred-work.md,
  "Deferred from: code review of story-2.2").** This story adds one unit-tier structural guard
  (`test_find_by_session_id_can_lock_the_row_for_update`) for the new `for_update` read, which
  narrows the gap slightly but does not close it — the behavioural proof of `for_update` under real
  concurrency still lives only in `tests/integration/db/test_session_service.py`, which CI
  `--ignore`s. Owner stays the Epic 2 retro.

- **Judgment calls flagged for the Epic 2 retro (7 total, per the story's Dev Notes):** the 90s
  heartbeat-staleness threshold (derived as 3× AR32's ~30s write cadence, no number fixed anywhere
  else); `SessionService` is sync-only, bridged to Story 2.5's asyncio runner via
  `asyncio.to_thread`; one exception class rather than a `SessionAlreadyRunning` subclass, since
  nothing branches on the distinction; no fifth `SessionStatus` value (see above); `resolve()`
  returns the row, not just the UUID; UUID-first-then-name resolution order; and AC #6 (the
  concurrent-reclaim criterion) was added to the epic's own AC list, since AC #4/#5 are individually
  correct but jointly racy.

## Deferred from: code review of story-2.3 (2026-08-19)

Three adversarial layers produced 42 raw findings, 26 after dedupe. The Critical finding (the
`FOR UPDATE` re-read returning stale identity-mapped attributes, which let two processes both
reclaim one session and let a `sealed` session move back to `running`) was fixed in-story, not
deferred. The five items below were deferred.

- ~~**⚠️ BLOCKING CONSTRAINT ON STORY 2.5 — the reclaim's liveness signal has no producer.**~~ —
  **FIRST HALF RESOLVED in story-2.5, 2026-08-21.** AR32's writer ships:
  `SessionSteadyState.run()` writes `last_heartbeat_at` every
  `DEFAULT_HEARTBEAT_INTERVAL_SECONDS` (30.0) through the record port, and a session is no
  longer reclaimable 90 seconds after it starts.
  ⚠️ **The second half does NOT close and is re-pointed at the Epic 2 retro:** a stale heartbeat
  is still not evidence a process is dead, and there is still no fencing token or ownership
  column — the migration-budget reasoning below is unchanged. Story 2.5 adds a *detection* only:
  the record port is bound to the `started_at` its own transition stamped, and a write is
  refused once the row's `last_started_at` moves past it, at which point the incumbent stops and
  leaves the successor's row alone. The window between the reclaim and the incumbent's next tick
  is up to one interval of two live processes. The original text follows. Nothing
  in `src/` writes `last_heartbeat_at` except `SessionService.transition()`'s single stamp on entry
  to `running` (verified by grep: the only other hits are the ORM column and the staleness helpers).
  Story 2.5 owns AR32's ~30s writer. Until it exists, **every session becomes reclaimable 90 seconds
  after it starts** and AC #5's fresh-heartbeat refusal — the only thing standing between an operator
  typo and two processes on one broker account — is dead for the life of a forward test. *Decision
  2026-08-19 (Allay): accept for Story 2.3, which scoped the writer out deliberately, and record it
  here as a hard constraint.* **Story 2.5 must not ship the runner without the heartbeat writer.**
  Related, and deeper: a stale heartbeat is not evidence a process is dead (DB failover, GC pause,
  throttled container), and there is no fencing token or ownership column, so a reclaimed process
  keeps trading and keeps heartbeating — each side would then refuse the other. The story calls NFR29
  "a policy, not a mechanism", which is accurate; an owner/epoch column was considered and rejected
  for now because the phase's single migration is already spent. Revisit at the Epic 2 retro.

- ~~**`InvalidSessionTransition` inherits `BacktestStorageError` — a live-trading refusal is
  catchable as a backtest-storage error.**~~ — **RESOLVED in story-2.5, 2026-08-21.** Both halves
  of the action are done: `"InvalidSessionTransition"` is mapped explicitly to `CONFIG_ERROR`
  (exit **1**) in `live_check._OUTCOME_BY_EXCEPTION_NAME`, its message is added to
  `_SAFE_MESSAGE_EXCEPTION_NAMES` so the session name and heartbeat age reach the operator, and
  `ntrader live start` contains no retry of any kind — pinned by
  `TestStartNeverRetries` in `tests/unit/cli/commands/test_live_cli.py`. This also supersedes and
  closes the narrower `classify_failure` note in story-2.3's section above. The original text
  follows. Story 2.3's Judgment call #3 chose one exception class deliberately,
  and *Decision 2026-08-19 (Allay): that stands.* Recorded because the risk lands downstream, not
  here: nothing catches `BacktestStorageError` broadly today (verified, 0 hits in `src/`), but
  Story 2.5/2.6 add the first CLI handlers on this path. "Another process appears live on this
  session" must be surfaced to a human and **never retried** — an `except BacktestStorageError:
  retry()` would spin until the incumbent's heartbeat went stale and then reclaim a live session.
  **Action for Story 2.5/2.6:** map `InvalidSessionTransition` explicitly at exit code **1** (AR28
  reserves 3 for gate refusal and 4 for connectivity), and never fold it into a storage-error retry.
  This supersedes the narrower `classify_failure` note carried from story-2.3's own section above.

- ~~**`stopped → running` performs no liveness check at all.**~~ — **RESOLVED in story-2.6,
  2026-08-21.** The first branch this item's own action named was already satisfied and is now
  pinned, not merely argued: `release_record` (`live_session_steady_state.py`) runs from the
  runner's `finally` *after* `shutdown()` has returned — by construction, the ordering the module
  docstring calls its second load-bearing guarantee — so the broker disconnect always completes
  before `stopped` commits. `tests/integration/db/test_session_stop_start_cycles.py` asserts the
  ordered outcome across three real stop/start cycles against Postgres:
  `created → running → stopped → running → stopped → running → stopped` leaves one row, identity
  unchanged throughout. **No heartbeat check was added to `stopped → running`** — the original
  text's second option — because that would refuse a legitimate restart of a session that stopped
  cleanly seconds ago, which is exactly the swing-strategy-across-days use case Story 2.6 exists
  for. The original text follows for context. Only the `running → running` self-edge consults
  `last_heartbeat_at`; `stopped → running` goes straight through `_LEGAL_TRANSITIONS` with no
  heartbeat consultation. A process that has committed `stopped` but is still flattening or
  cancelling working orders can therefore be joined by a second process through a door the guard
  does not watch — the same two-processes-on-one-account outcome NFR6 forbids, reached by a
  different route. **Action for Story 2.6:** the runner must complete the broker disconnect
  *before* committing `stopped`, or this edge needs the same heartbeat check the self-edge has.

- **No `lock_timeout` or `statement_timeout` is configured anywhere in `src/` or `alembic/`.**
  `transition()` takes an exclusive row lock via `SELECT ... FOR UPDATE` and, by design, never
  commits or rolls back — the caller owns the transaction. A caller that catches
  `InvalidSessionTransition`, prints a friendly message and lingers (an interactive prompt, a retry
  loop, a long-lived session scope) holds that lock, and every peer blocks **indefinitely** with no
  output. Confirmed by execution: a second connection with `lock_timeout='800ms'` got Postgres
  `55P03` on the same row. Repo-wide infrastructure concern, broader than this story.

- **Transaction isolation level is neither pinned nor documented.** The whole lock-then-re-read
  mechanism assumes READ COMMITTED. Under REPEATABLE READ or SERIALIZABLE, Postgres aborts the
  loser's `SELECT ... FOR UPDATE` with `could not serialize access due to concurrent update` — a
  `DBAPIError`, not the designed `InvalidSessionTransition` — which the integration race test's
  `except` clause would not catch and which no caller is prepared for. Worth pinning explicitly on
  the engine before Story 2.5 puts this path under real load.

- **`resolve()`'s UUID-before-name precedence can silently shadow.** If row B is *named* row A's
  `session_id` string, `resolve()` returns **A**, with no ambiguity error. This is deliberate and
  documented (Story 2.3 Judgment call #6), and the benign case (a UUID matching no row falling
  through to the name lookup) is tested — but the dangerous case, where the UUID matches A *and*
  the name matches B, is not. In a system where the resolved identifier selects which live session
  gets stopped or sealed, resolving to the wrong session silently is a real hazard. Either test the
  precedence explicitly or reject UUID-shaped `--name` values at creation.

- **Both trading-session repositories still have zero CI-gating coverage.** Carried forward from
  the story-2.2 code review, and now load-bearing: `tests/integration/db/` is `--ignore`d by CI
  (`ci.yml:168`, `:238`), so AC #6's only behavioural evidence — the two-connection reclaim race —
  never gates a PR. Story 2.3 adds one unit-tier structural guard for the `for_update` read, which
  narrows the gap but does not close it. The code review also found that the race test as written
  could not have caught the Critical defect anyway, because its loser uses a fresh `Session` whose
  identity map has never seen the row. Owner stays the Epic 2 retro.

## Deferred from: story-2.4 (2026-08-19)

**Resolves one item pointed at this story.** `deferred-work.md`, "Deferred from: story-2.2" —
**`SessionSpec.model_copy(update=...)` bypasses `frozen=True` and the before-validator**, re-pointed
here with the prediction that *"the durable engine cache gives a second code path that reads a
`SessionSpec` back into memory."* **It does not.** This story reads a `UUID` and `RedisSettings` and
constructs no `SessionSpec` anywhere; the Redis cache stores Nautilus's own engine objects, not the
session spec. The predicted second path is **Story 2.5's runner**, which materialises the stored spec
to build strategies. Re-pointed there, unchanged otherwise.

New items:

- **`REDIS_DB` cannot be honoured, and is refused rather than ignored.** `DatabaseConfig` at
  nautilus-trader 1.220.0 exposes only `type, host, port, username, password, ssl, timeout` — no
  database-index field — and the config is msgpack-encoded straight into the Rust
  `RedisCacheDatabase`, so there is no side channel either. `RedisSettings.validate_database_index_is_reachable`
  therefore raises on any non-zero value. Sessions are still isolated from each other by the
  `trader_id` key namespace, so nothing is lost functionally; what is lost is the ability to run two
  *unrelated* deployments against one Redis on different db indexes. **Action:** if a later Nautilus
  version adds the field, relax the validator and pass it through. Until then the workaround is a
  separate Redis instance on a different `REDIS_PORT`.

- **The 8-hex `trader_id` tag is a truncation, and the collision bound is disclosed rather than
  engineered away.** `derive_trader_id` takes `session_id.hex[:8]` per AR10's `PAPER-<short-session-id>`.
  That is 2**32 values: birthday bound n(n-1)/2N — ~1.2 in 10**6 at 100 sessions, ~1.2 in 10**4 at
  1000. (Stated as 10**7 at the 100-session end until the Story 2.4 review recomputed it; the figure
  matters because it is what the truncation is justified by.) A collision
  would put two sessions in one Redis namespace — the exact contamination this story prevents —
  and nothing detects it, because the derivation is pure and has no view of other sessions.
  `TraderId` accepts the full 32-character hex, so the fix is a one-line change plus a namespace
  migration. **Action for a future high-volume story:** either widen to the full hex, or add a
  uniqueness check at `live create` against existing sessions' derived tags. Not worth either today.

- **The `msgpack` cache encoding is now load-bearing and unversioned.** `build_cache_config` pins
  `encoding="msgpack"`. Changing it later makes every key already written unreadable, with no
  migration path and no version marker in the namespace to detect the mismatch. **Action:** if the
  encoding is ever changed, treat it as a namespace-breaking change — bump something in the
  `trader_id` derivation so old and new state cannot be confused.

- **`REDIS_URL` remains in `.env.example` and `docker-compose.yml`, read by nothing.** It predates
  this phase; a repo-wide grep for `redis` across `src/` and `tests/` found zero consumers before
  this story. `RedisSettings` uses `REDIS_HOST`/`REDIS_PORT` instead, and `docker-compose.yml` now
  sets both. `REDIS_URL` was left in place because removing it from `.env.example` requires the
  one-off approval that hook-protected file needs. **Action:** remove it, or teach `RedisSettings`
  to parse it, next time `.env.example` is legitimately edited. Two spellings of one setting is a
  trap for whoever changes the port.

- **`.env.example` documents no Redis host/port.** Same cause: `.claude/hooks/protect-files.sh:21`
  blocks every `.env*` path. The defaults (`127.0.0.1:6379`) are correct for a local run, so this is
  a documentation gap, not a functional one. **Action:** add `REDIS_HOST` / `REDIS_PORT` next to the
  existing `REDIS_URL` when an approved `.env.example` edit is happening anyway.

- **`read_ibkr_connection_status` moved to `src/core/live_connection_probe.py`.** Not deferred work —
  recorded here because it is a Story 1.6 surface that moved during a Story 2.4 task. Adding the
  cache seam took `live_node_builder.py` to 515 lines against this repo's 500-line limit, and the
  connection reader was the part least about assembling a node. Pure move, no behaviour change; its
  suite (`tests/component/core/test_live_connection_probe.py`) was already named for it and needed
  one import line changed. The `(host, port, client_id)` coupling to `live_node_builder` that
  originally justified their sharing a module is now a comment in both docstrings rather than an
  adjacency. **Action:** none, unless a reviewer disagrees with the split.

- **Nothing enforces Redis's disposability yet (AC #5 is documentation only).** `live_cache.py` and
  `docs/agent/nautilus.md` both state that Redis is a rebuildable cache and IBKR is authoritative on
  conflict, but no code detects or resolves a divergence between cached and broker state. This is by
  design — AC #5 asks only that the design be inspectable, and startup reconciliation is Epic 4
  (FR35, AR25). **Action for Epic 4:** the claim in those two docstrings becomes false-by-omission if
  Epic 4 ships without it; check them when the reconcile path lands.

## Deferred from: code review of story-2.4 (2026-08-19)

- **A Redis-only setting hard-fails every entry point.** `Settings.redis` is a
  `default_factory=RedisSettings` field (`src/config.py:456-457`), so
  `validate_database_index_is_reachable` (`src/config.py:326-333`) runs on every `Settings()`
  construction. Reproduced: `REDIS_DB=1 uv run python -m src.cli.main --help` dies with an uncaught
  `pydantic_core.ValidationError` raised from `src/db/session.py:11`, taking down backtest, catalog,
  data import and the web UI for a value the validator's own message concedes affects nothing but
  the live-session cache. **Deferred as pre-existing:** the cause is the module-scope
  `get_settings()` blast radius already recorded at `deferred-work.md:126-133` (`src/cli/main.py:19`,
  `src/db/session.py:11`), and the IBKR client-id validator already behaves the same way. Story 2.4
  adds one more instance rather than creating the pattern; the message is precise and recovery is a
  one-line env edit. **Action:** fold into whatever fixes the module-scope settings load — not worth
  a scoped workaround on its own.

- **`src/config.py` is at 493 lines against the repo's 500-line file limit.** `RedisSettings` (+94)
  took it within seven lines of the cap, so the next settings block crosses it. Same class of
  overflow that forced `live_connection_probe.py` out of `live_node_builder.py` during this story.
  **Action for whoever adds the next settings class:** split by domain (IBKR / Kraken / Redis /
  database) rather than trimming prose, and do it before the edit rather than during it.

## Deferred from: story-2.5 (2026-08-21)

Story 2.5 shipped `ntrader live start`: the eight-phase startup sequence, the AR32 heartbeat, the
record port and its adapter, the inert `SessionController`, and explicit node configuration. Nine
items were **struck** above (six resolved outright, one resolved-in-half, one credited to Story 2.2,
one superseded) and five were read and left open with updated notes. The items below are new.

- **`SessionService` is at 99 of CLAUDE.md's 100-line class limit, and `transition()` was split to
  get there.** Adding `record_activity` needed ~17 lines against three of headroom, so
  `transition()`'s decision-and-mutation body moved to a module-level `_apply_transition`, and the
  row read to `_load_or_raise` — the same shape `_reclaim_or_refuse` already models in that file.
  Nothing an operator or a guard can observe changed: AR37's enforcement is *per file* (an AST scan
  and a scoped grep over all of `src/`), so `session_service.py` is still the only module that
  assigns `TradingSession.status`, and all 74 of Story 2.3's tests pass unmodified. But the next
  method added to that class does not fit either. **Action for Story 2.8**, which adds the health
  derivation and will want to read from this service: decide then whether the class splits or the
  limit is the wrong measure for a class whose bulk is docstring. Worth noting for the retro that
  the repo's own largest classes are 1746, 1179 and 793 lines, so the limit is plainly not measured
  on raw lines today.

  **~~Resolved by Story 2.8 (2026-08-24) — by not needing to happen (Judgment call #4).~~** The
  question this item asked was whether the class splits or the limit gets re-argued once Story 2.8
  needed to read from it. It never came up: the health derivation lives entirely in the new
  `src/core/live_session_health.py`, over primitives (`SessionStatus`, timestamps, `runtime_flags`
  as a plain dict) that `live_status.py` reads off the ORM row and passes in — `SessionService`
  gained no new method, no new import, no new line. `resolve()` is reused exactly as Story 2.3 left
  it. The class stays at 98/100 (measured this session, one line freed since the 99 above), so
  neither the split nor the limit-is-the-wrong-measure question needed deciding. The broader
  question — whether the 100-line class guideline is enforced by anything, or measured against the
  right thing for orchestration classes — stands as recorded two entries below, still the Epic 2
  retro's to answer.

- ~~**`live_node_builder.py` is at 496 of the 500-line file limit, and `live_session_runner.py` at
  498.**~~ — **Story 2.6 update (2026-08-21).** The runner's half is addressed: before adding any
  stop-signal code, four constants (`DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS`,
  `SESSION_CONNECTION_ATTEMPTS`, `SESSION_LOGGING`, `BAR_TOPIC`) and their comments relocated to
  `live_session_node.py` verbatim, freeing ~29 lines before a single new line was written — the
  budget-before-writing move the original action asked for. Two more free functions
  (`request_node_stop`, `unsubscribe_bar_topic`) followed the same constants there once the stop
  wiring itself needed room, keeping the runner at the *wiring*, not the policy (Judgment call #8).
  Current sizes for Story 2.7 to inherit as a number: `live_session_runner.py` **499** of 500,
  `live_session_node.py` **336**, `live_session_signals.py` (new) **210**,
  `live_session_steady_state.py` **472**, `src/cli/commands/live.py` **444** (after splitting
  `claim_session`/`exit_with`/`release_quietly` into the new `src/cli/commands/live_start.py`,
  **99** lines). `live_node_builder.py` itself is untouched by this story and remains at 496 — its
  own headroom is still Story 2.7's to budget for. **Budgeted and spent (Story 2.7, 2026-08-23):**
  the three engine configs took it to **536**. Going over was the sanctioned option provided it is
  disclosed, and it is disclosed here; the alternative the story offered — extracting
  `_reconcile_bar_types` + `_actor_configs` (~60 lines) into a new module — was **declined**, because
  it would move node-assembly helpers out of the module whose entire job is node assembly purely to
  satisfy a cap nothing enforces, and every new `live_*` module has to be hand-added to three
  separate guard lists (this file already records that coverage shrinking through exactly that
  omission). **Partially closes, not closes.** The original text follows for context. Story 2.4
  already carved `live_connection_probe.py` out of the builder once; Story 2.5 added the six
  timeouts and two arguments and had to compress comments to stay under. The runner needed two
  splits of its own to fit — `live_session_node.py` (the connect wait, strategy materialisation,
  the spec check, the shortfall report) and `live_session_steady_state.py` (the heartbeat tick, the
  watchdog, the bar observation), both of which are cohesive modules rather than arbitrary halves.
  **Action for Story 2.6**, which attaches signal handling to the runner's run loop and its
  `finally`: budget for a further split before writing, not after. The pre-agreed line is *not* the
  phase sequence and *not* the `finally` — Story 2.5's own instructions were explicit that 2.6
  attaches to both.

- **`SessionReclaimedError` is a fourth name in `_SAFE_MESSAGE_EXCEPTION_NAMES` where only three
  went into `_OUTCOME_BY_EXCEPTION_NAME`.** The two collections answer different questions — which
  exit code describes the failure, and whether the message text is ours to show — and a reclaim
  needs the second without needing the first (AR28 has no better code for it than the generic 1).
  Story 2.5's own Judgment call #8 says a fourth typed failure is *"the moment to revisit"* the
  marker-protocol idea (`deferred-work.md`, story-1.7 section: an `exit_outcome` attribute on the
  exception rather than two name-keyed collections). **Action for the Epic 2 retro:** decide whether
  Story 2.6's `stop` — which will add at least one more typed failure — is where that lands.

  **Story 2.6 answer (2026-08-21): no, and recorded as an answer, not left open a second time.**
  This story adds `SessionStopRequested`, but it never reaches `classify_failure` at all — `run()`
  catches it internally (`except SessionStopRequested: pass`) precisely because a stop is a
  success, not a failure with an exit code to classify. The count of exit-code-mapped typed
  failures does not grow, so this story is not the moment the marker-protocol question was waiting
  for. The question itself stays open for the Epic 2 retro on its own merits.

- **The record port widens Story 2.5's literal rule: *every* `InvalidSessionTransition` from
  `record_activity` is treated as loss of ownership, not only the reclaim.** The story names only
  the `last_started_at` case as fatal and says *"every other exception still follows AR42"*. Read
  strictly, a row that had become `stopped` out of band would then log an error every 30 seconds
  forever against a row that will never accept another write. Both refusals mean the same thing to
  a runner — this process no longer owns this session — so `SqlSessionRecord` translates both into
  the port's own `SessionReclaimedError`. **Flagged for the Epic 2 retro** as a deliberate widening,
  not an oversight; it is strictly safer in the direction that matters, because the alternative is a
  session that keeps trading after something else took its row.

- **`session.no_bars_observed`'s 300-second window is invented, and nothing validates it.** No
  number for a first-bar watchdog exists in the PRD, the architecture or the epics. Five minutes is
  defensible (a 1-minute subscription has had several chances; a 5-minute one has had one) and the
  cost of being wrong is low in both directions, because the watchdog is visibility only. But it
  has never been observed against a real market open, where the first bar's arrival is exactly the
  thing being measured. **Action:** record what Procedure P6 actually shows, and adjust or delete
  the constant on evidence rather than on argument.

  **Story 2.6 note (2026-08-21):** untouched by this story — the watchdog and its window are Story
  2.5's, not read or modified here. Procedure P7 (this story's own) is **not** the procedure that
  validates this; P6 remains the owner.

- **Nothing in this story ever reads `ConnectionMonitor.trading_permitted`.** The monitor is
  constructed at `node:connect` and fed on every heartbeat tick, so its state is correct and
  observable — but no code path consults it, and `confirm_state_reestablished` is deliberately never
  called (*Judgment call #6*: `reconcile` is a no-op placeholder, so there is no genuine
  reconciliation for the confirmation to follow). A reader could reasonably assume feeding the
  monitor means acting on it. **Action for Epic 4**, which owns reconciliation and is the only place
  the confirmation can be truthful.

- **The heartbeat's `asyncio.to_thread` runs on the kernel's own thread pool.**
  `TradingNode.__init__` installs the kernel's `ThreadPoolExecutor` as the loop's default
  (`kernel.py:268-270`), and `dispose()` shuts it down with `wait=True, cancel_futures=True`
  (`live/node.py:445-447`). At one write per 30 seconds the exposure is negligible, and the runner's
  `finally` cancels *and awaits* the heartbeat before `shutdown()` precisely so an in-flight write
  cannot block disposal. **Recorded rather than actioned**, because it becomes interesting the
  moment anything else on this path uses `to_thread` at a higher rate — Epic 3's trade persistence
  being the obvious candidate.

- **`live_session_runner` imports `NodeFactory` and `AccountVerifier` from `live_check_driver`.**
  Story 2.5 was instructed to reuse the aliases rather than define a second pair that could drift,
  which is right — but it means a *session* module now imports a *check* module, and a Story 2.6 or
  2.7 change to the driver's aliases reaches the runner. **Action for the Epic 2 retro:** consider
  moving both aliases to a neutral module (`live_session_node` or `live_check`) that neither
  command's driver owns.

- **Transaction isolation is still not pinned, and this story is what puts the path under load.**
  `deferred-work.md` (story-2.4 review) records that nothing sets an isolation level on the sync
  engine, so it is whatever Postgres defaults to (READ COMMITTED). Story 2.5 **deliberately does not
  pin it**: the one place isolation matters here is `transition()`'s `SELECT … FOR UPDATE`, which
  Story 2.3 already proved correct under a genuine two-connection race at the default level, and
  changing an engine-wide setting to protect a path that is already correct would affect every
  backtest write for no measured benefit. The heartbeat writes one column per 30 seconds in its own
  transaction and takes no lock at all. **Recorded as a deliberate refusal**, not an omission.
  Similarly, no `lock_timeout` or `statement_timeout` is set anywhere; one-transaction-per-heartbeat
  is the mitigation, and a genuinely wedged write surfaces as a logged
  `session.heartbeat_write_failed` rather than a stuck session.

- **Re-validating a persisted spec against today's param model is lossy, and the trigger has now
  fired.** `LiveSessionRunner` is the first production caller of `SessionSpec.from_stored()`, which
  re-runs every parameter through the strategy's *current* registered param model. A model whose
  fields changed since the session was created would silently reinterpret a frozen spec — the exact
  failure FR14 exists to prevent, arriving through the read path rather than the write path.
  Nothing in this story can fix it (the spec must be read to be run). **Ownership stays Epic 5's**,
  alongside the comparison that would notice; recorded here only because the trigger condition this
  item was waiting on is now met.

- **`SessionSpec.model_copy(update=…)` bypasses validation — still untriggered.** Story 2.2's review
  re-pointed this at Story 2.4, which found it not triggered and re-pointed it here as *"the second
  code path that reads a `SessionSpec` back into memory"*. It now is that path, and the answer is
  still **not triggered**: the runner reads through `from_stored` and never derives a modified spec
  from an existing one (verified — `model_copy` appears nowhere in `src/core/live_session_*.py`).
  **Re-pointed at Epic 5** alongside the item above, rather than left with a third stale owner.

- **`IBKRSettings.model_dump()` leaks the account and password in clear, and the runner holds one.**
  The trigger condition is met — a long-running process now holds an `IBKRSettings` for hours — but
  nothing in this story dumps it: the runner logs `endpoint(settings)` (host, port, client id) and
  nothing else. **Recorded as a standing constraint on this path**: never log a
  `Settings`/`IBKRSettings` dump from the runner or the steady state.

- **`live start` is another CLI entry point on the module-scope `get_settings()` blast radius.**
  A settings validation error still kills the whole CLI before argument parsing, so
  `ntrader live start --help` fails when, say, `REDIS_DB=1` is exported. Pre-existing and not caused
  here; noted because the number of affected entry points grew again.

- **A live run emits two command-scoped `live_check.*` records.** A session start logs
  `live_check.building` (from `build_clients`) and `gate.static` (from `preflight_gate`), because it
  reuses Story 1.7's plumbing. AR41 deliberately keeps command-scoped vocabulary separate from
  session-scoped vocabulary, so these read oddly in a session's log. **Deliberately not renamed
  here** — both modules are shared with `ntrader live check`, whose tests pin their record names,
  and renaming them is not this story's to do. **Action for the Epic 2 retro:** decide whether the
  shared plumbing should take its event prefix from its caller.

- **~~AR41's event enumeration needs four amendments.~~** — **RESOLVED at the Epic 2 retrospective,
  2026-08-28, by changing the MECHANISM rather than the list.** This item and its three appended
  updates below record the enumeration falling behind five times across two epics; a measurement of
  `src/` at `0602f1f` found **20 session-scoped events plus an entire `strategy.*` namespace** absent
  from it. AR41 now governs the **naming rule and the sanctioned namespaces** (`session.*`,
  `strategy.*`, `gate.*`, `order.*`, `trade.*`, `reconcile.*`, `connection.*`, `warmup.*`), keeps a
  normative list of **lifecycle milestones only** — which cannot be silently renamed or dropped — and
  declares diagnostic and operational events inside a sanctioned namespace conformant by
  construction. This is the namespace-vs-names decision Story 2.7's update below asked the retro to
  make. The `strategy.*` namespace is sanctioned, and the AR36 hazard that update flagged
  (`strategy.halted` would fail the vocabulary scan; *contained*, *failed*, *degraded* are the
  sanctioned words) is recorded inline in AR41 itself. ⚠️ **Flagged for Allay as a change of mechanism,
  not of content** — reversible to an exhaustive list if that is preferred. The original text and all
  three appended updates follow, unaltered, as the record of why the mechanism changed.
  This story ships `session.started` (enumerated
  but previously unowned — now emitted at the `trading` phase) plus three names that are **not** in
  the enumeration: `session.heartbeat_write_failed`, `session.no_bars_observed` and
  `session.reclaimed_by_another_process`. It also emits `session.phase`, `session.connected`,
  `session.instruments`, `session.shutdown_problems`, `session.connection_read_failed`,
  `session.heartbeat_join_failed` and `session.mark_stopped_failed`, none of which are enumerated
  either. **Action for the Epic 2 retro:** amend AR41 in `epics.md`, following the Epic 1 retro's
  own precedent — it amended the list for `connection.halted` / `connection.recovery_refused`
  rather than leaving the question open a third time.

  **Story 2.6 update (2026-08-21) — the list grows again, added here rather than starting a
  second one.** `session.stopped` is enumerated at `epics.md:241` but was previously unowned —
  this story is the first to emit it, from the runner's stop-signal callback, carrying `signal` and
  `trader_started`. Four more are **not** in the enumeration: `session.force_exit` (the second
  signal), `session.heartbeat_join_timeout` (Task 6's bounded join), `session.unsubscribe_failed`
  (a guarded, non-fatal teardown step), and `session.shutdown_problems` was already unenumerated
  per the note above and remains so. Same action, same owner: the Epic 2 retro.

  **Story 2.7 update (2026-08-23) — appended to this same item, still not a second one.** Six more,
  none of them in AR41's enumeration, and the first four open a **new `strategy.*` namespace**
  (unused before this story, and analogous to the enumerated `order.*` / `connection.*` families —
  the retro should decide whether AR41 enumerates the namespace or the individual names):
  `strategy.failed` (ERROR — `strategy_id`, `spec_strategy_id`, `error_type`, `handler`, a **redacted**
  `traceback`), `strategy.degraded` (WARNING), `strategy.start_failed` (ERROR),
  `session.all_strategies_failed` (ERROR) and `session.strategy_record_failed` (ERROR, AR42's
  "the DB write failed and the session continues" event, emitted from both the guard and the
  steady-state tick). ⚠️ Note for whoever amends AR41: AR36's vocabulary scan word-matches
  `pause|halt|kill|close|finalize` against operator-facing strings, so `strategy.halted` — the
  obvious name — would fail the build. *Contained*, *failed*, *degraded* are the sanctioned words.
  Same action, same owner: the Epic 2 retro.

  **Story 2.8 note (2026-08-24) — no growth this time, recorded so the list is not silently
  believed complete.** `status`/`list` emit zero structlog events by design (Judgment call #7):
  they are pure readers whose console output *is* the product, and AR41's command-scoped carve-out
  already treats that vocabulary as separate from the session-lifecycle enumeration above. Nothing
  to amend here — the item is appended to rather than left unmentioned, so a future reader does not
  have to re-derive that this story was considered and found to add nothing.

- **`tests/component/core/test_live_check_node.py` is a NEW file where Story 2.5's Files table says
  MOD.** `live_check_node.py` never had a suite of its own; its behaviour was covered indirectly
  through `test_live_check_driver.py`. The new `max_connection_attempts` parameter exists precisely
  so a session and a check choose different budgets, and that difference deserved a test that names
  it. Recorded as a deviation from the story's own file list, not a scope addition.

- **~~CLAUDE.md still says "14 migrations… single head (`a436f35f525c`)".~~** — **RESOLVED at the
  Epic 2 retrospective, 2026-08-28.** Corrected to **16 migrations, single head `b7c419e2a3d8`**
  (`a436f35f525c` is now two revisions behind; `d08dfbd393f0` from Story 2.2 and `b7c419e2a3d8` from
  Story 2.7 both landed since). The original text follows. Story 2.2 added
  `d08dfbd393f0` and the head moved. Carried forward from Story 2.5's own Project Structure Notes,
  which flagged it as a drive-by observation rather than a task. **Action for the Epic 2 retro.**

## Deferred from: code review of story-2.5 (2026-08-21)

- ~~**Teardown can block indefinitely on an in-flight heartbeat write.**~~ — **HALF RESOLVED in
  story-2.6, 2026-08-21.** `join_heartbeat` (`live_session_steady_state.py`) is now bounded:
  `HEARTBEAT_JOIN_TIMEOUT_SECONDS` (10.0s) via `asyncio.wait({task}, timeout=...)` rather than an
  unbounded `gather`, chosen over `asyncio.wait_for` specifically because the measured failure mode
  — an `asyncio.to_thread` worker stuck in a blocking socket read — does not respond to
  cancellation at all, and `wait` returns on the deadline regardless of whether the task ever
  finishes. On timeout it logs `session.heartbeat_join_timeout` naming what was abandoned and
  continues the teardown. **The other half stays open, re-pointed at the same story-2.3 item this
  entry already names** (no `lock_timeout`/`statement_timeout` anywhere): pinning a
  `statement_timeout` on the sync engine is repo-wide and moves every backtest write, which this
  story's scope (stop) does not license. The original text follows for context. `task.cancel()`
  cannot interrupt an `asyncio.to_thread` call whose worker is already running; the runner's
  `_stop_heartbeat` then waits on it without a bound, `node.dispose()` afterwards joins the same
  executor `wait=True`, and the sync engine (`src/db/session_sync.py:53-60`) sets no
  connect/statement timeout — so a Postgres TCP stall at teardown hangs the process while it still
  holds the IBKR live client id. **Action:** when the statement-timeout item is picked up, pin a
  `statement_timeout` (or `connect_args` timeout) on the sync engine — `_stop_heartbeat`'s join no
  longer needs it.

- ~~**A clean run whose final `→ stopped` write fails still exits 0.**~~ — **RESOLVED in
  story-2.6, 2026-08-21, per its Judgment call #7.** Decided: exit code stays **0**, and a console
  warning names the consequence. `release_record` now returns `True` only for this shape (a write
  failure that is not a reclaim), the runner surfaces it as `record_release_failed`, and
  `ntrader live start`'s clean-stop path prints *"The session stopped cleanly but its record could
  not be marked stopped. The row still reads `running`; the next `live start` will be refused
  until its heartbeat goes stale (90s)."* before returning. The `session.mark_stopped_failed`
  structlog ERROR this item names is unchanged — this adds the operator-visible half rather than
  removing the machine-readable one. Rationale: the broker-side outcome was correct and the row is
  reclaimable in 90 seconds, so a non-zero exit would misreport a successful stop as a failure. The
  original text follows for context. `_finish_record` swallows a `mark_stopped` failure by design
  ("must never replace the primary outcome"), but on the *clean* path there is no primary failure
  to protect: the CLI printed "Session stopped" and exited 0 while the row stayed `running` for the
  full 90-second threshold, with only a `session.mark_stopped_failed` structlog ERROR explaining
  why the next `live start` was refused.

- **Two timing-raced component tests may flake on a saturated worker.**
  `test_an_ordinary_write_failure_does_not_stop_the_session` asserts `> 1` heartbeat writes inside
  a 0.05s node run with each write a real `asyncio.to_thread` round trip, and the connect-wait
  bound tests assert real elapsed time (`< 3.0` / `< 2.0`) in a suite run `-n auto`. Generous, but
  host-load-dependent. **Action:** none until CI flakes; if one does, drive it deterministically
  (inject the sleeper/clock) rather than loosening the bound.

## Deferred from: story-2.6 (2026-08-21)

Story 2.6 shipped signal handling: `SessionStopSignals` (new module), the runner's arm/re-arm/
phase-boundary wiring, subscription cancellation on stop, a bounded heartbeat join, and the
operator-visible failed-release warning. Six items above were **struck** (three resolved outright,
one half-resolved, one answered, one precondition re-confirmed) and four were read and left open
with updated notes. The items below are new.

- **`SessionStopSignals` is at ~109 lines, over CLAUDE.md's 100-line class guideline.** Trimmed
  once already — per-method docstrings were cut and their content folded into the module docstring,
  which does not count against the class — and the remainder is deliberate: this is the one class
  in the live path where nearly every line of prose records a fact measured against the installed
  `nautilus-trader` wheel rather than argued from documentation (the two-mechanism arming, the
  order dependency, the `SIGABRT` handoff), and cutting further would delete the reasoning a future
  reviewer needs to trust a signal handler without re-deriving it from scratch. No repo-wide
  enforcement of this guideline was found (grep of `pyproject.toml`/`ruff.toml` for a class-length
  rule returns nothing) — `session_service.py`'s own "99 of 100" note (story-2.5 section) is itself
  a manually-tracked observation, not evidence of a gate. **Recorded as a disclosed, deliberate
  overage**, not an oversight. **Action:** if the Epic 2 retro decides the guideline should bind
  here, the class splits along its own two concerns — the arm/restore/handle state machine, and the
  default `force_exit` announcement — which the module docstring already treats as separable ideas.

- **AC #2's epic clause is knowingly not met end to end, pinned by a test rather than fixed.**
  `sma_crossover.on_stop()` still calls `close_all_positions()`, and `node.stop()` reaches it
  through real Nautilus machinery (`Trader._stop()` → `Strategy.on_stop()`) this story does not
  touch. `tests/unit/core/test_live_stop_path_is_inert.py::test_the_known_limit_is_pinned_...`
  documents this and instructs Story 3.1 to delete both the call and the test. Not a new item —
  the epic's own FR coverage map (`epics.md:283`) already names Story 3.1 as the fix — recorded
  here only so a reader of this file's index sees it without opening the story. **Owner: Story
  3.1**, unchanged.

- **Procedure P7 (this story's own) has not been run against a live gateway.** No automated test
  in this story's suite requires IB Gateway/TWS or Redis — every signal fact was established with a
  bare `TradingNodeConfig` and no broker (Dev Notes, "Blockers and preconditions"). What P7 alone
  can still show — identity surviving a stop/restart against a *real* IBKR paper connection, and
  the Redis namespace genuinely unchanged — remains unverified. **Action:** run P7 once with a
  position open and once without, per its own preconditions, and record both.

- **`tests/integration/db/test_session_stop_start_cycles.py` is not CI-gated**, for the same
  structural reason every file in that directory is not: no `__init__.py`, `--ignore`d by both the
  integration job and `coverage-report` (`ci.yml:168`, `:238`). It is evidence, not a gate — the
  same posture `test_session_service.py` beside it already documents. Not a new gap; recorded so
  Story 2.6's identity claim (AC #5) is not mistaken for CI-enforced.

- **Story 2.8's `trade_counts_by_session` tests are not CI-gated either, for the same reason
  (fifth entry).** The typed-key join proof (AC #8: closed/open counts, the join-on-UUID mutation
  check) and the `create → status → list` round trip (`test_live_status_e2e.py`) both live in
  `tests/integration/db/`, which `--ignore`s the same way every sibling in this thread does. The
  behavioural gap is narrower than it looks: `tests/unit/cli/commands/test_live_status_cli.py`
  mocks the repository and carries the gated coverage for `status`/`list`'s own wiring (option
  surface, exit codes, `--json` shape, AR36 vocabulary), and
  `tests/unit/db/test_trading_session_repository_shape.py` gates the capability-set and
  deterministic-ordering guards. What stays uncovered on a PR is specifically the SQL itself —
  the `LEFT OUTER JOIN`/`FILTER` phantom-row bug this story's own TDD cycle caught (a session with
  zero trades counted itself as one open position until the aggregate was rewritten to
  `count(Trade.id).filter(...)` instead of `count(case(...))`) is exactly the class of defect this
  gap would let back in silently. **Action, still the Epic 2 retro's to decide:** the two options
  named in the first entry above (component-tier repository tests against in-memory SQLite, or
  stop `--ignore`ing this directory with a CI Postgres service) apply unchanged; five stories
  independently hitting the same `--ignore` is the strongest signal yet that the root cause, not
  another workaround, is what the retro should spend its time on.

---

## Deferred from: code review of story-2.6 (2026-08-22)

Adversarial review, three parallel layers. 53 raw / 31 deduplicated: 5 decisions, 21 patches,
3 deferred (below), 2 dismissed. Two of the decisions invalidate claims in Story 2.6's own
Completion Notes and were reproduced by execution, not argued — they are **not** deferred and are
tracked in that story's `### Review Findings` section, not here.

- **An abandoned heartbeat worker can write after the row is marked `stopped`.** When
  `join_heartbeat` gives up on a wedged write, the `asyncio.to_thread` worker keeps running; if
  Postgres recovers after `release_record` has committed `stopped`, its `record_activity` lands on
  a stopped row — a session that looks freshly heartbeating to `live status`, or an unhandled
  exception on a thread nobody watches. **There is no fencing token on `trading_sessions`**, which
  `release_record`'s own detail string already says in as many words
  (`src/core/live_session_steady_state.py:459-460`). Pre-existing; the correct fix is a fencing
  token or a `last_started_at`-qualified UPDATE on the activity write, not a wider join bound.
  **Action:** decide the fencing-token question when Story 2.8 (`live status`) makes a stale-but-
  fresh-looking row operator-visible.

  **~~Disposition recorded by Story 2.8 (2026-08-24) — deferred again, on its merits, not
  resolved.~~** Judgment call #3. This story owns **no** `SessionRecordPort` writes — it is the
  phase's one pure reader — and the item below (story-2.7's re-opening) is explicit that the
  column "changes the meaning of every write on `SessionRecordPort`, so it belongs to a story that
  owns that port, not to a bystander." Adding it here would be exactly that bystander move. What
  this story *can* do without owning a write, it does: `status` renders `last_started_at` alongside
  the heartbeat's age, the two facts an operator needs to notice a row that is stale-but-recently-
  claimed. The decision moves to the first Epic 3/Epic 4 story that touches `SessionRecordPort`
  write semantics, where NFR6's two-processes hazard becomes order-adjacent to whatever that story
  is already doing. **Ratified by Allay, 2026-08-24.**

- **Class-size limit violations on Story 2.6's touched files.** Measured by AST against the current
  tree: `LiveSessionRunner` = **379** lines, `SessionSteadyState` = **171**, `SessionStopSignals` =
  **109**, against CLAUDE.md's <100-line class guideline. Only the 109 is disclosed anywhere in the
  story. Both larger classes shipped in Story 2.5, so this is pre-existing rather than introduced
  here — but Task 11's "Confirm ... every class under 100" subtask is marked `[x]` while three
  classes violate it, and that false claim *is* patched by this review. The guideline is documented
  in CLAUDE.md and `project-context.md` and enforced by nothing in the repo (no ruff rule, no test),
  which is why it drifted silently across two stories. **Re-measured by Story 2.7 (2026-08-23), all
  larger than when this item was written and one of them new:** `LiveSessionRunner` = **537**,
  `StrategyGuard` = **320** (new, Story 2.7), `LiveBarObserver` = **215**, `SessionSteadyState` =
  **240**, `SessionStopSignals` = **195**. Story 2.7 discloses its own 320 in the module docstring
  rather than repeating Story 2.6's false "every class under 100" claim. **Action:** either enforce it (a unit-tier
  AST guard, the shape this repo already uses for AR37) or amend the guideline to say what is
  actually intended for orchestration classes. Flag for the Epic 2 retro.

- **`unsubscribe_bar_topic` uses `steady_state is not None` as its proxy for "subscribe ran".**
  `_phase_subscribe` assigns `self._steady_state = self._build_steady_state()` one line *before*
  `self._node.trader.subscribe(BAR_TOPIC, ...)` (`src/core/live_session_runner.py:413-414`), so a
  raise between the two leaves the teardown unsubscribing a handler that was never registered. Today
  the two coincide and the surrounding `except Exception` contains the consequence to a spurious
  message-bus warning on an already-failing stop; `test_no_unsubscribe_attempted_when_subscribe_never_ran`
  would keep passing right through a refactor that moved steady-state construction earlier (e.g. to
  `node:connect`, where the `ConnectionMonitor` already lives). **Action:** track the fact of the
  subscription rather than inferring it, whenever that phase is next touched.

- **`src/core/live_session_runner.py` is 576 lines, over CLAUDE.md's 500-line cap.** Accepted by
  Allay at the Story 2.6 review (2026-08-23) rather than resolved. The review's signal-window fixes
  had to land in `run()` and `_phase_node_build`, and the story's own file-size budget section states
  the pre-agreed split line is *"**not** the phase sequence and **not** the `finally`"* — the two
  places the fixes belong. Even with minimal comments the necessary code lands ~545. The file has
  been at the cap for two consecutive stories, so this is structural rather than incidental.
  **Action for Story 2.7 or the Epic 2 retro:** either agree a new split line (the strongest
  candidate is the class's construction surface — `__init__` plus the class docstring's Args block
  is ~80 lines — since it is neither the sequence nor the `finally`), or record the runner as a
  sanctioned exception the way `src/cli/commands/catalog.py` (732) already is. Note the cap is
  documented but enforced by nothing — no ruff rule, no test — which is how it drifted silently.

  ⚠️ **Two numbers above are wrong and are corrected here.** The runner was **581** at `107ee14`, not
  576. And the 732-line sanctioned exception is **`src/cli/commands/import_data.py`**, not
  `catalog.py` — `catalog.py` is **263** lines. Both errors have been carried forward unchallenged.

  **~~Answered by Story 2.7 (2026-08-23): recorded as a sanctioned exception, not split.~~** Decided
  with Allay. The runner's `run()`/`finally` ordering is load-bearing and hard-won across three
  stories, and the pre-agreed split line explicitly excludes the two places it would have to be cut.
  Story 2.7 adds ~85 lines of wiring and per-spec containment, taking it to **666** — and the
  2026-08-23 code-review fixes (the teardown flush, `all_strategies_failed`, the corrected
  `_fault_quietly` measurements) take it to **734, measured**. The `__init__`
  candidate above stays on the table for whoever next needs room, and the underlying observation —
  that the cap is enforced by nothing — is what the class-size item below is for.

  **Two more files to record while here.** `src/core/live_session_steady_state.py` was **507** before
  this story and **574** after the guard-queue drain (the 567 first recorded here was a stale
  pre-format measurement — caught by the 2026-08-23 code review), then **581** after that review's
  retry-requeue fix.
  `src/core/live_strategy_guard.py` shipped **new at 574**, now **634** after the review fixes
  (the `BaseException` boundary, `requeue`, the configured-account redaction):
  roughly 180 lines of code and the rest the
  measured Nautilus findings the story required be written down in the module rather than only in the
  story file. Its one clean split candidate is the redaction trio (`redact_accounts`,
  `_one_redacted_line`, `_redacted_traceback` and their pattern — ~90 lines) into a
  `live_redaction.py`, which would land the guard at ~485. **Not taken**, because Story 2.7's AC #8
  names `redact_accounts` as living *in* `live_strategy_guard.py`; moving it needs an AC amendment,
  not a dev-time decision.

- **The heartbeat write is bounded against a wedged Postgres only up to interpreter exit.**
  Decision D2 (2026-08-23) moved the write onto `SessionSteadyState`'s own one-worker pool, so
  `node.dispose()`'s `executor.shutdown(wait=True)` can no longer join it — the measured teardown
  block went from 59.81s to 0.00s, and the `-> stopped` transition and operator report now complete
  on time. What remains: CPython joins thread-pool workers at interpreter exit, so a worker still
  stuck in a socket read can delay the *process* from exiting after all meaningful work is done.
  **Action:** bound the write at the database with a `statement_timeout`, scoped with `SET LOCAL`
  inside the session-record transaction — **not** on the engine or via `connect_args`, because
  `get_sync_engine()` (`src/db/session_sync.py:52`) is a single global engine shared with backtest
  persistence, where an aggressive timeout would start aborting large trade-batch inserts.

- **A stop requested during a *synchronous* phase is noticed but not acted on until that phase ends.**
  Found by running Procedure P7 against a real gateway on 2026-08-23, after the Story 2.6 review's
  D1 fix. The fix works as far as it claims: with `node:build` made genuinely slow, `session.stopped
  … signal=SIGINT` is now logged 6.0s into the phase, where before the signal was discarded outright.
  But the handler's only action is `loop.call_soon_threadsafe(node.stop)`, and the loop is **not
  running** during `build_clients`' synchronous 3-attempt connect — so the callback sits queued and
  the stop can only take effect at the next `raise_if_requested()` boundary, which is after all three
  attempts (~4 minutes; measured >200s still running). The operator sees Ctrl-C acknowledged in the
  log and nothing happen. **Mitigation that exists today:** a second Ctrl-C force-exits immediately
  (verified in that exact window — exit 1, 0.0s), so there is an escape hatch; before this story
  there was neither notice nor escape. **Action:** make the stop actionable inside the phase rather
  than only at its boundary. The retry loop belongs to Nautilus's IB adapter, so the practical
  options are (a) check `signals.requested` between connection attempts by lowering
  `SESSION_CONNECTION_ATTEMPTS` and looping in our own code, (b) run `build_clients` on a thread the
  runner can abandon, or (c) document the two-Ctrl-C answer in the CLI's own help and P7. Do **not**
  "fix" this by shortening the connect budget — that trades a stop delay for a spurious
  broker-unreachable failure on a slow-but-healthy gateway.

- **P7's "with a position open" half is still unverified.** The 2026-08-23 run was on a **Sunday**
  with the market closed and `use_rth=True`, so no session could open a position of its own. The
  4-share `AAPL.NASDAQ-EXTERNAL` position in the paper account did survive both stops — good evidence
  that the runner touches nothing — but it is `EXTERNAL`, and `sma_crossover.on_stop()`'s
  `close_all_positions()` filters by `strategy_id`, so it was never a candidate for flattening. The
  case that actually matters — a session that opened its **own** position, stopped, and had it
  flattened by `on_stop()` — has still never been observed. **Action:** re-run P7 inside RTH before
  Story 3.1 claims to remove that call, so there is a before/after pair rather than only an after.

## Deferred from: story-2.7 (2026-08-23)

- **The owner/epoch fencing column is re-opened, and its one cost argument is gone.**
  `SessionReclaimedError`'s docstring (`src/core/live_session_record.py`) rejected a fencing token
  *"because this phase's single migration is spent"* — a reason **Story 2.7's own migration
  falsified**. The hazard it names is unchanged and is the worst thing this phase can produce: up to
  one heartbeat interval (~30s) of **two live processes on one broker account**, NFR6's catastrophe,
  detected rather than prevented. The docstring has been restated to say so rather than left citing a
  dead reason. **Action:** decide the fencing column on its merits — it changes the meaning of every
  write on `SessionRecordPort`, so it belongs to a story that owns that port, not to a bystander.
  Story 2.8 (`live status`) is the natural place, since it is what makes a stale-but-fresh-looking
  row operator-visible.

  **~~Re-argued and deferred again by Story 2.8 (2026-08-24) — see the full disposition on the
  code-review-of-2.6 entry above.~~** Not taken here after all: 2.8 turned out to be the phase's one
  pure reader, owning no `SessionRecordPort` writes, which is precisely the "bystander" shape this
  item's own action text warns against. `status` renders `last_started_at` next to the heartbeat's
  age instead, and the column itself moves to the first Epic 3/Epic 4 story that owns a write on
  that port. Ratified by Allay, 2026-08-24.

- **~~`redact_accounts` reads no configuration, so a non-standard account id is not redacted.~~**
  **CLOSED by the 2026-08-23 code review (decision with Allay):** the Acceptance Auditor flagged the
  omission as a unilateral narrowing of AC #8's pinned contract (*"plus the configured `TWS_ACCOUNT`
  when set"* was part of the pinned surface, not a comment). Implemented as the cheap version this
  entry proposed: `redact_accounts(text, *, account=None)` and `StrategyGuard(..., account=...)`
  take the already-loaded **string** — no settings import, no I/O on the event-loop thread — and the
  configured value is redacted case-insensitively, closing the lowercased-identifier leak too. The
  residual is unchanged in kind but smaller: an account id that is neither token-shaped nor the
  configured value is still not redacted (e.g. the second entry of a multi-account
  `TWS_ACCOUNT="DU…,U…"` — the guard receives the raw string and matches it whole).

- **`NoStrategyStartedError` is the FIFTH name in `live_check._SAFE_MESSAGE_EXCEPTION_NAMES` where
  only three go into `_OUTCOME_BY_EXCEPTION_NAME`.** Story 2.5's *Judgment call #8* said a **fourth**
  typed failure was the moment to revisit the marker-protocol idea; `SessionReclaimedError` made four
  and this makes five, so the question is now overdue rather than approaching. The two sets answer
  genuinely different questions (*"is this text ours to show?"* versus *"which exit code describes
  it?"*), which is why the divergence keeps growing. **Action for the Epic 2 retro:** a marker base
  class or a protocol, decided once, rather than a sixth hand-maintained string.

- **The `order_id_tag` collision is a latent startup failure that nothing prevents at create time.**
  Measured against a real `Trader`: `Trader.add_strategy` assigns `order_id_tag = f"{len(existing):03d}"`
  to any strategy whose tag is `None`, rewrites the `StrategyId`, then raises if the tag is taken —
  so `mean_reversion, sma_crossover` raises `RuntimeError: order_id_tag conflict for '001'` while
  `sma_crossover, mean_reversion` is fine. A `SessionSpec` that validated perfectly at create time
  can therefore fail at `trading` for an operator's **choice of strategy order**. Story 2.7's per-spec
  `except` *contains* it (the session starts with the other strategies and the failure is recorded),
  which is strictly better than the crash it used to be, but the operator still learns about it at
  start time rather than at create time. **Action:** a create-time validator, or an explicit
  `order_id_tag` per spec — the latter is probably right, since it also makes client order IDs
  stable across a re-ordered spec, which Epic 3 cares about.

- **A `DEGRADED` strategy's `on_stop()` never runs at session teardown.** `Trader.stop_strategy()`
  and `Trader._stop()` both guard on `is_running`, which means `state == RUNNING` **exactly**
  (`common/component.pyx:1757-1767`); measured, `Trader.stop_strategy(a.id)` left a degraded strategy
  at `DEGRADED`. Today that is strictly **safer**, because `sma_crossover.on_stop()` still calls
  `close_all_positions()` and skipping it is what stops an unrelated `on_bar` bug from manufacturing
  an exit. **After Story 3.1 removes that flatten it inverts into a leak** — no `unsubscribe_bars`,
  no strategy-owned cleanup. **Action for Story 3.1:** revisit whether the runner should explicitly
  `stop()` degraded strategies at teardown once doing so is safe. Pinned by a test whose docstring
  says exactly this: `tests/integration/core/test_live_strategy_failure_survives.py::`
  `TestADegradedStrategyIsSkippedAtTeardown` (written by the 2026-08-23 code review — this entry
  claimed the test existed before it did; the Acceptance Auditor caught the false claim).

- **`SessionSteadyState.note_bar` and `LiveBarObserver` are covered only by AC #6's engine flag.**
  A deliberate choice (Story 2.7, Task 7), not an oversight. Neither is wrapped by the per-strategy
  guard, and `note_bar` deliberately did **not** get a `try` of its own: its body is two statements
  that cannot raise with the production clock, its docstring (a Story 2.5 review artefact) states
  that **no test may inject a raising clock here**, so the RED test could not be written without
  breaking an explicit in-code prohibition, and the guard would be unreachable code in a file already
  over the size cap. What covers them is `graceful_shutdown_on_exception=True` — which is a graceful
  stop of the **whole node**, not containment. **Action:** if a future story does add the guard,
  `tests/component/core/test_session_steady_state.py` (13 `note_bar` call sites) and that docstring
  both need updating in the same edit.

- **Timer/`TimeEvent` failures are contained but invisible.** EXECUTED: a raise inside a `LiveClock`
  timer callback is silently swallowed at the pyo3 boundary — process survives, exit 0, nothing
  printed, no traceback. No repo strategy uses timers today, so this is latent. Story 2.7 does not
  wrap them (there is no `handle_*` seam to wrap). **Action:** whenever a strategy first uses a timer.

- **`Actor.handle_bar` runs `_handle_indicators_for_bar` OUTSIDE its `try`.** A `handle_*` wrapper
  contains a raising registered indicator; an `on_*` wrapper would not (`actor.pyx:3735-3744`). Story
  2.7 chose `handle_*` for exactly this forward reason. **Relevant to Story 4.4**, which rewrites
  `on_start` to register indicators and is the first story where this stops being hypothetical —
  `grep -rn register_indicator src/core/strategies/` returns zero today.

- **`StrategyRegistry.discover()` catches only `ImportError`.** `src/core/strategy_registry.py:271-275`
  swallows it into `warnings.warn`; any *other* exception at strategy-module import time aborts
  discovery for every remaining strategy. Also why no test may depend on a `custom/` submodule
  strategy: CI without the submodule checked out silently drops 5 of the 7 registrations and the
  suite still goes green. **Action for the Epic 2 retro.**

- **An exception in one strategy's order-event handler silently drops that fill's pending position
  events.** `execution/engine.pyx:1170-1187` clears `_pending_position_events` **before** publishing
  the order event, so a raise in `handle_event` loses the `PositionOpened`/`Changed`/`Closed` that
  would have followed. Wrapping `handle_event` (which Story 2.7 does now, rather than in Epic 3)
  closes this — **recorded so Epic 3's trade recorder knows why the wrapper is already there** and
  does not "tidy" it away as premature.

- **A session whose strategies have all failed keeps running.** *Judgment call #7*, and a real cost:
  such a session holds an IBKR client id and a market-data line while being incapable of placing an
  order. AC #1's letter requires it, and stopping instead would reproduce the "stopped means two
  different things" defect this file already logs. `session.all_strategies_failed` plus
  `runtime_flags.all_failed` make it visible. **Action for the Epic 2 retro:** decide whether it
  should instead stop — and if so, what status it stops into, which loops back to the fifth-status
  question above.

- **`SessionReclaimedError` is not fatal on the bar path, breaking the symmetry with
  `record_activity`.** *Judgment call #10.* Every other caller re-raises a reclaim; the guard cannot,
  because a raise out of a wrapped `handle_*` re-enters `MessageBus.publish_c` and dies at
  `os._exit(1)` with **zero bytes of output** — measured, and the exact silent death the story exists
  to close. It is surfaced one steady-state tick later instead. The cost is up to ~30s of a
  dispossessed process still running. **Action:** restoring the symmetry needs a boundary inside
  `handle_bar` that does not exist, so this is really the fencing-token item above wearing a
  different hat.

## Deferred from: code review of 2-7-keep-one-failing-strategy-from-taking-down-the-session (2026-08-23)

- **Any infrastructure exception inside the per-spec start loop is misattributed as a spec
  failure.** `_start_strategy`'s per-spec `except Exception` cannot distinguish "this spec is bad"
  from "the trader/node is broken" (`src/core/live_session_runner.py:562-570`): a trader-level
  fault raised for every spec is recorded as N `strategy.start_failed` events with
  `handler="start"` and surfaced as `NoStrategyStartedError("Every specification failed: ...")` —
  pointing the operator at their specs for a failure no spec caused. Under `python -O` the
  `assert self._node is not None` is stripped, turning a missing node into an `AttributeError`
  attributed the same way. **Reason deferred:** distinguishing infra from spec faults is a design
  question (error classification, or letting non-spec exceptions propagate) for a later story;
  the containment itself is what Story 2.7 sanctioned.

- **AC #1's heartbeat-advance clause has no automated proof.** The integration probe
  (`tests/integration/core/test_live_strategy_failure_survives.py`) drives a bare
  `LiveDataEngine` — no runner, no `SessionSteadyState`, no heartbeat — so "`last_heartbeat_at`
  continues to advance" is proxied, not proven; it lives in P8 criterion 1, which is ⛔ not run
  (`docs/qa/phase3-live-verification.md`). **Reason deferred:** sanctioned by the story's own
  P8-not-run-at-review policy; **action:** run P8 live verification before Epic 2 closes.

- **The guard wraps `handle_bar`/`handle_event` only; the rest of the handler surface is
  uncontained.** `handle_bars` and `handle_historical_data` (the `request_bars` response path —
  `sma_momentum` already drives it at warm-up), `handle_quote_tick`, `handle_trade_tick` and
  `handle_data` all dispatch outside the boundary: a custom-submodule strategy overriding one of
  those and raising gets AC #6's whole-node graceful shutdown, not containment. **Decision with
  Allay (2026-08-23): disclose now, widen later** — `GUARDED_HANDLERS` is a pinned surface
  (`test_live_strategy_guard.py` asserts the exact tuple), and each additional handler needs its
  own dispatch-path measurement before wrapping it is honest. The module docstring's disclosed
  limit #1 now names the full unwrapped surface. **Action:** widen `GUARDED_HANDLERS` in the story
  that first ships a strategy overriding one of these handlers, with a per-handler containment
  test.

## Deferred from: code review of 2-8-see-what-a-session-is-doing-without-reading-logs (2026-08-24)

Two of 24 surviving findings. The other 22 (3 decisions, 19 patches) are tracked in that story's
`### Review Findings` section, not here.

- **Two functions in the new health module exceed CLAUDE.md's 50-line function cap.**
  `derive_health` is 63 lines and `build_status_report` 76 (`src/core/live_session_health.py:94`,
  `:225`), both docstring-dominated — roughly 12 and 20 executable statements respectively. Files
  (376 and 205) and classes (`StatusReport` 41, `SessionHealth` 6) are all inside their caps.
  **Reason deferred:** pre-existing and project-wide — 195 functions under `src/` already exceed 50
  raw lines, and this file already records that the limit "is plainly not measured on raw lines
  today". Story 2.8 disclosed `live.py`'s file-level over-cap (533 → 538) but said nothing about
  the function cap, so the disclosure is incomplete on its face. **Action:** no code change until
  the Epic 2 retro settles whether the cap is measured on raw lines or on executable statements;
  whichever it picks, apply it to this module in the same sweep as the other 195.

- **`_age_seconds`'s clamp is documented with a rationale the code does not need, and its real
  consequence is undocumented.** The docstring justifies clamping a future-dated timestamp to age 0
  as protection against "a negative number some comparison could mistake for stale"
  (`src/core/live_session_health.py:57-68`) — but neither consumer would misread one: a negative
  age already compares fresh under `>` and trading under `<=`. What the clamp actually does is
  suppress a negative age in the human output and, more importantly, mask clock skew between the
  runner host and the querying host for the duration of the skew — a genuinely dead session can
  read fresh. **Reason deferred:** the function deliberately mirrors
  `session_service._heartbeat_age_seconds` (`session_service.py:142-156`), which carries the same
  clamp, the same rationale and the same unstated skew behaviour; correcting one without the other
  would break a symmetry Story 2.8 established on purpose. **Action:** fix both docstrings together
  in the first story that touches heartbeat-age semantics, and decide there whether skew masking
  deserves an operator-visible warning.

## Deferred from: live-verification session of 2026-08-28 (Procedures P1–P9)

Two findings surfaced by running the live procedures against a real paper Gateway inside RTH, rather
than by code review. Both come from the same run of Procedure P5 and are recorded in that procedure's
result log in `docs/qa/phase3-live-verification.md`.

- **A transient IB data-farm drop at subscribe time kills a bar subscription permanently, and nothing
  retries it.** MEASURED, `logs/p5-check-20260828.log`: 324 ms after the data client reported
  `Subscribed AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL bars` for request id 10007, IB sent
  `HMDS data farm connection is broken:ushmds` (code 2105) followed by
  `Failed to request live updates (disconnected). (code: 10182, req_id=10007)`. The farm recovered
  514 ms later (2106) and the market-data farm flapped and recovered too (2103 → 2104), but the
  live-update stream for req 10007 was never re-requested. Zero bars arrived for the remaining 90
  seconds, and teardown closed with `No historical data query found for ticker id:10007` (366). An
  identical run minutes later, with no farm codes, took 5 bars — so this is the blip, not the code.
  **Reason deferred:** it is an adapter-level reconnect concern discovered during verification, not a
  defect in any story's acceptance criteria, and no Epic 2 story owns market-data resubscription.
  **Action:** decide who owns resubscription-after-10182 before Procedure **P6** is treated as
  meaningful. P6 runs unattended for 6.5 hours and its criterion 4 asks that `last_bar_at` stay
  within about a minute of the most recent bar; a session that takes this blip keeps heartbeating and
  keeps its row looking healthy while receiving nothing, so P6 would be measuring a dead stream
  without saying so. `scripts/diagnostics/run_p6_rth_day.sh` greps the transcript for codes
  10182/2103/2105/366 and warns, which is a detector, not a fix.

- **`live check` reports `ok` and exit 0 for a dead subscription, and blames regular trading hours.**
  When the above run received zero bars, the summary read *"no bars closed during the observation
  window. Outside regular trading hours this is expected (use_rth=True means no bar closes)"* — at
  10:14 ET on a Friday, with the market open. The command has no RTH awareness, so its zero-bar
  explanation is asserted rather than determined, and it is asserted most confidently in exactly the
  case where something is actually wrong. `--require-bars` turns the same run into exit 1 and is the
  right flag for scripted use, but the default is misleading. **Reason deferred:** AR28's exit-code
  table has no outcome for "connected and subscribed but received nothing", and Story 1.7 already
  recorded that inventing one is worse than a generic failure — so this needs a decision, not a
  patch. **Action:** either soften the message to name both possibilities without choosing between
  them, or give the command a real RTH calendar so it can tell them apart. Procedure P5's pass
  criterion 3 should not be read as covering this: the procedure passes on the bars it did receive.

- **Procedure P4's probe cannot pass its own final assertion, by construction.**
  `live_connection_loss_probe.py:341-343` requires the string `socket` in `status.detail` before it
  will certify the disconnect, but `read_connection_status` tests `_client_is_unusable` *before* the
  socket flag (`src/core/live_connection_probe.py:114-118`) and the probe produces its disconnect by
  calling `node.stop()` — which makes the client unusable. The earlier branch therefore always wins
  and the detail reads `ib client is stopped or disposed`, never `ib socket not connected`. MEASURED
  2026-08-28 against a real paper Gateway: all three of P4's pass criteria appeared in the transcript
  (`recovering permitted=False` on a live socket, `confirmed … permitted=True`, then `lost
  permitted=False` with a session-bound `connection.lost`), and the run still ended
  `RESULT: fail reason=ProbeError …`, exit 1. The socket drop was independently confirmed in the same
  run — `_await_socket_disconnect` polls `_is_ib_connected` directly and returned normally, and would
  otherwise have raised its own distinct error — so the assertion is redundant as well as unreachable.
  **Reason deferred:** the production reader's branch order is deliberate and fail-closed, and
  changing it to satisfy a diagnostic script would be the wrong direction; this is the script's
  assertion and the procedure's documented expected output that are wrong. **Action:** either relax
  the assertion to accept the stopped/disposed detail (the socket poll already proves the drop), or
  drive the disconnect by stopping the **Gateway** instead of the node so the client stays usable and
  the socket branch can report; then correct the `detail=ib socket not connected` line in P4's
  "Expected output" block, which is unreachable via `node.stop()` on nautilus-trader 1.220.0.

- **🚨 BLOCKER — a live session cannot submit any order: the IB execution client is never given a
  default route.** `InteractiveBrokersExecClientConfig` is constructed without a `routing=`
  argument (`src/core/live_node_builder.py:333-339`), so it takes Nautilus's default
  `RoutingConfig(default=False)` and serves only its own `INTERACTIVE_BROKERS` venue. Every
  instrument this codebase trades carries the *exchange* as its venue — `AAPL.NASDAQ`,
  `NVDA.NASDAQ`, `MSFT.NASDAQ` — so no order can ever be routed to it. MEASURED 2026-08-28 against
  the live paper Gateway, twice, while attempting Procedure P7's criterion 2: a real
  `sma_crossover` produced a real signal and a real `MarketOrder`, and `ExecEngine` refused it with
  `Cannot execute command: no execution client configured for NASDAQ or 'client_id' None,
  SubmitOrder(order=MarketOrder(SELL 22 NVDA.NASDAQ MARKET GTC …))`. The order stops at
  `OrderInitialized`: there is no `OrderSubmitted`, `OrderAccepted`, `OrderFilled`, `OrderDenied`,
  `OrderRejected` or `PositionOpened` anywhere in either transcript. The likely one-line fix is
  `routing=RoutingConfig(default=True)` on that config, which is what the adapter's own examples
  use, but it must be **verified live** rather than assumed — and it deserves a test that fails
  today, because nothing in the suite currently notices.
  **Reason deferred:** it is a live-verification finding, not a code-review one, and it is larger
  than any single story — no Epic 1 or Epic 2 acceptance criterion asserts that an order reaches
  the broker (Epic 1 deliberately has no order path, and Epic 2's stories are about session
  lifecycle), so nothing that has been marked done is actually wrong. It is nonetheless the most
  consequential thing found on 2026-08-28.
  **Action, and what it invalidates:** fix and re-verify before Epic 3, which owns trades and would
  otherwise be built on a path that has never once executed. Two statements already written into
  `docs/qa/phase3-live-verification.md` are false until it is fixed and must be corrected then —
  Procedure P6's "It **does** submit orders if the strategy's logic fires", and Procedure P7's "A
  session that traded will have been flattened by `sma_crossover.on_stop()`". **No session has ever
  traded.** Procedure P7's criterion 2 position-open half is unreachable by any means until this is
  resolved, and the `sma_crossover.on_stop()` flatten warning that P6, P7 and P8 all carry has
  never been exercised against a session-owned position. Story 3.1, which is scheduled to remove
  that flatten, should re-read those warnings in this light.

  **✅ RESOLVED 2026-08-28, same session.** `routing=RoutingConfig(default=True)` now passed
  explicitly on that config. The mechanism, confirmed against the installed 1.220.0 source rather
  than assumed: Nautilus registers a client as the engine's default only when the config asks
  (`live/node_builder.py:252-254`), and its own fallback — adopt the first client registered —
  fires **only** for a client constructed with `venue=None` (`execution/engine.pyx:421-429`). The
  IB *exec* client passes `venue=IB_VENUE` (`execution.py:157`) so it never qualified; the IB
  *data* client passes `venue=None` (`data.py:115`) so it did, which is precisely why market data
  worked and hid this for two epics. Regression coverage added in
  `tests/component/core/test_live_node_builder.py`: `TestExecutionRouting` drives Nautilus's real
  `TradingNodeBuilder.build_exec_clients` and asserts a `SubmitOrder` for `NVDA.NASDAQ` reaches the
  client, with an anti-tautology twin proving the same harness counts **zero** under the stock
  `RoutingConfig` and a control proving an `INTERACTIVE_BROKERS`-venued order routed even before
  the fix. Plus the config/canary/structural guards in `TestExecClientDefaultRouting`.
  Verified live: `ExecClient-INTERACTIVE_BROKERS: Submit MarketOrder(SELL 22 NVDA.NASDAQ …)` —
  a line that had never appeared in any transcript before.

- **🚨 BLOCKER (second, found behind the first) — the execution client's instrument provider is
  never loaded, so a routed order dies inside the adapter.** Found immediately after the routing
  fix above let an order reach the client for the first time; it had been unreachable until then.
  `InteractiveBrokersExecClientConfig` was constructed with no `instrument_provider=`, leaving
  `load_ids=None`, while `_transform_order_to_ib_order` dereferences
  `self.instrument_provider.find(order.instrument_id).is_inverse` with **no `None` check**
  (`adapters/interactive_brokers/execution.py:525`). MEASURED live 2026-08-28
  (`logs/p7-position-20260828-153506.log`): `[ERROR] ExecClient-INTERACTIVE_BROKERS: Error on
  'submit_order: SubmitOrder(order=MarketOrder(SELL 22 NVDA.NASDAQ MARKET GTC …))':
  AttributeError("'NoneType' object has no attribute 'is_inverse'")`. Note the failure shape: it is
  an exception raised *inside the adapter's own* `submit_order`, so the strategy sees no rejection
  and no `OrderDenied` — the order simply stops.
  **✅ RESOLVED 2026-08-28**: the exec client now carries the same
  `InteractiveBrokersInstrumentProviderConfig(load_ids=…)` as the data client — a session can only
  trade what it subscribed to. Covered by
  `TestInstrumentLoading::test_the_exec_client_loads_the_same_instruments_as_the_data_client`.
  **What this invalidates:** Procedure P3's "Known benign log lines" listed
  `InteractiveBrokersInstrumentProvider: No loading configured` as benign, reasoning that "the
  execution client constructs its own instrument provider, which this story deliberately leaves at
  its default — Epic 1 has no order path that would need it". That reasoning was sound for Epic 1
  and expired when Epic 2 started strategies. The entry is struck through in
  `docs/qa/phase3-live-verification.md` with the measurement above.
  **Lesson worth carrying:** two independent defects sat in series on the order path, and the
  first completely masked the second. Neither was visible to the automated suite, and neither was
  visible to any procedure that stopped short of submitting a real order. "Market data arrives" is
  not evidence about orders; only an order reaching the broker is.

- **A third instance of "an IB subscription error permanently kills the stream and nothing retries
  it" — this time IB code 162.** The first entry in this section recorded code 10182 after a
  data-farm blip. On 2026-08-28, while trying to close P7's criterion 2 after the two order-path
  fixes, four consecutive sessions received **zero bars** because IB answered each subscription's
  request id with `Historical Market Data Service error message:Trading TWS session is connected
  from a different IP address (code: 162)`. Ruled out as causes, by experiment: a stale client id
  (reproduced with `IBKR_LIVE_CLIENT_ID=17`), and anything contract-specific (reproduced on
  `MSFT.NASDAQ` and `NVDA.NASDAQ`). It began mid-session at ~15:39 UTC after runs in the same hour
  had taken bars normally, so the trigger is account-side — a competing IBKR login. **Confirmed by
  the operator: they signed into IBKR on their mobile device mid-session.** IBKR permits one active
  session per account; the mobile login evicted the Gateway's market-data entitlement while leaving
  its API socket up, which is why the session connected, passed both gate layers, logged
  `Subscribed … bars` and kept heartbeating while receiving nothing at all. Logs:
  `logs/p7-position-20260828-{153949,154259,154402,154527}.log`.
  **Operational note for every live procedure:** do not use the IBKR mobile app or client portal
  while a session is running. It is silent from the session's side, and it looks exactly like a
  healthy session with a quiet market.
  **Reason deferred:** the trigger is environmental and not ours to fix. The *response* is ours,
  and it is the same gap as 10182: the session keeps heartbeating, keeps its row looking healthy,
  and reports nothing wrong while receiving nothing at all.
  **Action:** whoever owns resubscription-after-10182 should own this too — the two want one
  mechanism, not two special cases. Until then, treat "zero bars inside RTH" as a red flag rather
  than a quiet outcome, and note that this now bites **P6** hardest, whose criterion 4 would
  measure a dead stream and read as a pass. The three known killers to grep a transcript for are
  **162**, **10182** and **366**.

## Dispositions from the Epic 2 retrospective (2026-08-28)

Full writeup: `epic-2-retro-2026-08-28.md`. ~30 items in this file carried an explicit "Action for
the Epic 2 retro". They fall into three buckets, recorded here so no future reader re-derives them.

### Resolved outright at the retrospective (struck in place above)

- **AR41's event enumeration** — resolved by changing the mechanism from enumeration to namespace.
  See the struck item in the story-2.5 section for the reasoning and the flag for Allay.
- **`CLAUDE.md`'s migration count** — corrected to 16 migrations, head `b7c419e2a3d8`.

### Escalated as decisions (D1–D6) — **ALL SIX RULED BY ALLAY, 2026-08-28**

These are **not deferred again**; they are the retrospective's actual output, and five of the six had
already been re-argued between three and five times. Every recommendation was accepted.

| # | Item | Deferrals | **Ruling** | Implementation |
|---|---|---|---|---|
| **D1** | Owner/epoch **fencing column** on `trading_sessions` | **5** | **Story 3.6** — the first story owning a `SessionRecordPort` write. Epic 2 never had a legitimate owner (2.8, the natural candidate, was the phase's one pure reader). | ✅ Written into `epics.md` under Story 3.6, with the hazard, the two folded-in findings (post-`stopped` heartbeat write; `SessionReclaimedError` non-fatal on the bar path) and the retired cost argument |
| **D2** | CI `--ignore=tests/integration/db` | **5** | **Drop the `--ignore`, add a Postgres service.** The SQLite component route cannot exercise the SQL that actually broke — the tables use `JSONB`, `PG_UUID`, `sa.Enum`. | ⏳ `.github/workflows/ci.yml` (both the integration job and `coverage-report`) — **not yet made** |
| **D3** | AR28 exit-code **marker protocol** | **4** | **`exit_outcome` marker attribute, now**, before a sixth hand-maintained string. | ⏳ `src/core/live_check.py` — **not yet built**. Must fix the `sqlalchemy.exc.TimeoutError` → exit 4 collision and change no already-documented exit code |
| **D4** | What the size caps mean | — | **Keep them, measure on executable statements**, enforce with a unit-tier AST guard in the AR37 shape + a sanctioned-exception allowlist. | ◑ `CLAUDE.md` wording updated; the **AST guard is not yet written**. Its allowlist starts with Epic 2's six over-cap files |
| **D5** | Should an all-strategies-failed session stop? | — | **Leave as-is.** `session.all_strategies_failed` + `runtime_flags.all_failed` make it visible and 2.8 renders it `degraded`. | ✅ Closed on its merits — no change. Revisit only if observed in practice |
| **D6** | Resubscription after a subscription-killing IB error | 3 instances | **Epic 4**, with the broker-authoritative reconciliation work. | ✅ Routed. Interim rule stands: "zero bars inside RTH" is a red flag, not a quiet outcome; grep every transcript for **162**, **10182**, **366** |

**Note for readers of the older entries above.** D1's original item text still reads "moves to the
first Epic 3/Epic 4 story that owns a write on that port" and D2's still offers two options. Those are
superseded by this table — the ruling is made and the owner is named. Do not re-open either as an
open question.

### Re-pointed at a named owning story rather than left at a priority label

The retrospective's clearest cross-epic finding is that Epic 1's *directed, numbered* action items
were followed through at a near-perfect rate while its *undirected* "non-blocking, lower priority"
bucket went 6-of-7 unaddressed — and one of those, `load_ids` on the exec client's instrument
provider, turned out to be one of two blockers meaning **no session had ever traded**. The remedy
adopted: **give each item a named owning story, not a priority label.** Items re-pointed accordingly
(all already recorded in their own sections above; listed here as the index):

- `close_all_positions()` in `on_stop()`, and the DEGRADED-strategy teardown leak it inverts into →
  **Story 3.1** (both now written into `epics.md` under that story).
- The two order-path fixes' live verification, and P7 criterion 2's position-open half →
  **Story 3.2** (written into `epics.md`).
- Explicit `order_id_tag` per `StrategySpec`, or a create-time validator → **Story 3.2**, which is
  where client-order-ID stability across a re-ordered spec first matters.
- The `handle_event` wrapper's rationale (`_pending_position_events` cleared before publish) →
  recorded in `epics.md` under Epic 3 so it is not tidied away as premature.
- Widening `GUARDED_HANDLERS` → the first story shipping a strategy that overrides an unwrapped
  handler, with a per-handler containment test.
- `Actor.handle_bar` running `_handle_indicators_for_bar` outside its `try` → **Story 4.4**, the
  first story to register indicators.
- Re-validating a persisted spec on read, and `model_copy(update=…)` → **Epic 5**, unchanged.
- P4's unreachable probe assertion → a diagnostics fix, unowned by any story; see Action Item 15.
- `StrategyRegistry.discover()` swallowing only `ImportError`, the `live_check.*` prefix question,
  the `NodeFactory`/`AccountVerifier` alias coupling, the `_age_seconds` clock-skew masking, and
  `architecture.md`'s Delta Project Tree → still unowned, and explicitly named as such rather than
  labelled low priority.
