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
