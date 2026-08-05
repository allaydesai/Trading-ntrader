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
