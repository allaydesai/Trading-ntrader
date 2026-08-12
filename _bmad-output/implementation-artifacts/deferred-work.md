# Deferred Work — Phase 3 (Paper Trading)

Non-blocking findings surfaced during code review. Each entry names the source review and the reason
it was not actioned at the time. Archived with the phase; a fresh file opens with the next phase.

## Deferred from: code review of story-1.1 (2026-08-03)

- **Real-money crossing permits a `DU`/`DF` paper account** — *deferred by decision; Layer 2 catches it
  post-connection.* `_evaluate_real_money_crossing` (`src/core/live_gate.py:160-169`) receives only the two
  declarations, so `--real-money` + `NTRADER_REAL_MONEY_ACCOUNT=DU1234567` + a matching `TWS_ACCOUNT`
  returns `permitted=True, mode=REAL_MONEY` against a demo account on a paper port. The spec correctly
  forbids ANDing the paper conditions into this branch — that would make the crossing unreachable — but a
  paper-prefix check *on the authorized account* is a distinct check the spec never considered. Failure
  mode: the operator believes real orders are going out; they are silently landing on paper.
  **Action for Story 1.4:** the post-connection account check must compare the account the gateway
  actually reports against the authorized account AND reject a paper prefix on a `REAL_MONEY` decision.
  `test_real_money_crossing_does_not_evaluate_paper_conditions` deliberately locks the mode/port omission
  in; the paper-prefix case is untested in either direction.

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

- **Collecting `tests/integration/api/test_trades_api.py` alongside any test that builds a real
  `TradingNode` crashes the latter with `SIGTRAP`, in the same `pytest -n auto --forked` run** —
  root cause: `test_trades_api.py` does `from src.api.web import app`, and `src/api/web.py:22-23`
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
  100% reproducible across 7+ runs. The pre-existing 23-test failure baseline in
  `tests/integration` (present before this story, in `test_backtest_catalog_integration.py`,
  `test_backtest_runner_integration.py`, `test_backtest_runner_yaml.py`,
  `test_kraken_backtest.py`) is unrelated to this mechanism and unaffected by it either way.
  Not fixed here: the story's Dev Notes explicitly forbid touching `src/api/**`. **Action for
  whoever next touches `src/api/web.py` or adds another `TradingNode`-building integration test:**
  move the module-level `init_logging()` call out of import-time (e.g. into an app factory or
  FastAPI lifespan hook) so importing `src.api.web` for its FastAPI `app` object stops being a
  process-global side effect.

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

## Deferred from: story-1.5 (2026-08-09)

- **The execution client's instrument provider carries no `load_ids`.** The IB adapter builds a
  *separate* `InteractiveBrokersInstrumentProvider` per client, and this story wired `load_ids`
  onto the data client's only — deliberately, since Epic 1 has no order path that needs a
  contract resolved for execution. Observed live on 2026-08-09: `[WARN]
  InteractiveBrokersInstrumentProvider: No loading configured: ensure either 'load_all=True' or
  there are 'load_ids'` is emitted once, alongside the data provider's successful
  `Loaded 1 instruments`. Harmless today and documented in Procedure P2's "Known benign log
  lines". **Action for Epic 3**, whose first submitted order is the point at which the exec
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

- **Procedure P2's third pass criterion is not closed.** The procedure ran against the live paper
  Gateway on 2026-08-09 and met pass criteria 1 and 2 (REALTIME requested and confirmed in the
  gateway log, contract qualified and loaded, subscription accepted, no 10167). Criterion 3 — a
  bar actually delivered and logged — could not be met because 2026-08-09 is a Sunday and the
  contract's own `tradingHours` returned `20260809:CLOSED`; with `use_rth=True` no bar can close.
  The probe reported this correctly rather than printing `ok`. **Action:** re-run
  `scripts/diagnostics/live_bars_probe.py` during regular trading hours and record the result in
  that file's P2 Result Log. Not a blocker for the story's automated coverage, which exercises the
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
