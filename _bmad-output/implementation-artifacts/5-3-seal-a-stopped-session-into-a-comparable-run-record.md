# Story 5.3: Seal a Stopped Session into a Comparable Run Record

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want an explicit seal that ends the session and produces a results record,
so that a multi-week forward test becomes one comparable unit at a moment I choose.

## Why this story is shaped the way it is

Read this before designing anything. Stories 5.1 and 5.2 built everything a seal *computes*:
`reconstruct_session_results(rows, *, starting_capital, start, end)` turns a session's `trades`
rows into an ordinary `BacktestResult` plus the `equity_series` fragment of `session_conditions`,
and `SessionResults.merged_into(snapshot)` folds that fragment into a config snapshot. This story
builds the act itself: the transaction that turns a `stopped` session into one `backtest_runs` row,
and the `ntrader live seal` command that asks for it.

Drafted 2026-10-09 against head `2ba84d0` (worktree `hx-p3e5-5-3-…`, SQLAlchemy 2.0.43,
`nautilus-trader 1.220.0`). Four facts decide the design:

1. **The two halves live on different sides of an async boundary.**
   - `BacktestPersistenceService` (AR12: reuse it verbatim) is async-only, over the async
     `BacktestRepository`.
   - `SessionService` (AR37: the only place a status may change) is sync-only, and must stay free
     of Nautilus (`TestImportPurity`).

   Two connections would mean two transactions. A crash between them leaves either an orphan
   `backtest_runs` row, which the list views would show (an AR5 violation), or a `sealed` session
   with no record. `AsyncSession.run_sync` runs sync ORM code on the async session's own
   connection, so both halves share one transaction (F2). That was measured, not assumed.
2. **Nothing records a session's starting capital** (5.2 F10), and nothing durable records its
   first start (F4). The run row needs both as `NOT NULL` columns, and the metrics need the
   capital. No migration is allowed. → PO rulings **R1** and **R3**.
3. **The database cannot see an open position.** `trades` holds closed round trips only. The one
   local record of what a stopped session still holds is its Redis engine cache, which Story 4.6's
   load-only `read_session_view` reads (F5). The AC asks the report to name open positions and
   their disposition. Story 5.5 owns `--force`, the null-exit rows and the disposition wording.
   → PO ruling **R2** decides what 5.3 does before 5.5 exists.
4. **`create_backtest_run` takes no `run_type`.** The column's server default is `'backtest'`.
   Setting it on the returned ORM row inside the same transaction persists `'paper'` (F1). The
   persistence service and both backtest repositories therefore stay zero-diff.

### Measured facts — cite, don't re-derive

The probe is `/tmp/p53/probe_tx.py` (not committed). It ran on an isolated schema, `probe_p53`,
which it dropped afterwards.

| # | Fact | Where |
|---|---|---|
| F1 | `BacktestRepository.create_backtest_run` / `SyncBacktestRepository.create_backtest_run` have no `run_type` parameter. `BacktestRun.run_type` is `String(20)`, `server_default="backtest"`. Setting `run.run_type = "paper"` on the row `save_backtest_results` returns, then `flush()`, persists `'paper'` at commit. | `src/db/models/backtest.py:120-125`; probe T3 |
| F2 | One transaction across both halves: `await db.run_sync(fn)` hands `fn` a sync `Session` on the same connection and identity map. Inside it, `SessionService(SyncTradingSessionRepository(sync)).transition(sid, to=SEALED)` locks the row `FOR UPDATE` and stamps `sealed_at`. `BacktestPersistenceService(BacktestRepository(db)).save_backtest_results(...)` then runs on the same `AsyncSession`. **Rollback → 0 `backtest_runs` rows and the session still `stopped`. Commit → `run_type='paper'`, status `sealed`, `sealed_run_id == run_id`.** | probe T1–T5; SQLAlchemy `AsyncSession.run_sync` docstring (context7 not permitted in this session; read from the installed 2.0.43 wheel) |
| F3 | `_LEGAL_TRANSITIONS`: the only edge into `SEALED` is from `STOPPED`, and `SEALED` is terminal. `CREATED`/`RUNNING`/`SEALED → SEALED` raise `InvalidSessionTransition`, which carries `exit_outcome=CONFIG_ERROR` (exit 1) and `operator_safe_message=True`. `_TIMESTAMPS_BY_TARGET[SEALED] = ("sealed_at",)`. **Nothing writes `sealed_run_id` today**; the module docstring routes it to 5.3. | `src/services/session_service.py:3-11, 124-139`; `src/db/exceptions.py` |
| F4 | No durable first start. `last_started_at` is overwritten on every claim. `owner_epoch` counts claims **and** reclaims. `runtime_flags` is cleared on every `→ running`. `trading_sessions.created_at` is the only always-present instant that is never after the first start. | `session_service.py:328-345`; `src/db/models/trading_session.py` |
| F5 | `trades` holds closed legs only (the recorder persists `PositionClosed` alone), so `trade_counts_by_session`'s open count is **0 for every live session** until a 5.5 `--force` seal writes null-exit rows. The session's own view of what it holds is its engine cache: `read_session_view(session_id, account, redis)` (load-only, `LOAD_METHODS` pinned mutator-free). It returns `SessionView.positions` (`ViewPosition(instrument_id, quantity)`) plus `positions_skipped` (positions on another account). It raises `SessionViewUnavailableError` (exit 1, operator-safe) rather than ever reading as flat. `live reconcile` calls it with `settings.ibkr.tws_account`. | `src/core/live_session_view.py:1-60, 217-281`; `src/core/live_reconcile.py:271`; `src/models/reconciliation.py:55-99` |
| F6 | A stopped session cannot gain a trade. `SqlTradeRecord.persist` first runs `stamp_activity_if_owner`, which is qualified on `status='running'` and the epoch, and refuses on rowcount 0. That `UPDATE` touches the session row, so the seal's `FOR UPDATE` lock serialises it. | `src/services/trade_record.py:99-135`; `trading_session_repository_sync.py:198-248` |
| F7 | Neither `TradingSessionRepository` twin can read a session's trade rows or backfill them. Both surfaces are pinned by `EXPECTED_CAPABILITIES` (exact set, both twins, no `update/set_/save/patch/merge/upsert/replace` prefix). Each class measures **76** of its 100-statement cap. | `tests/unit/db/test_trading_session_repository_shape.py`; `measure_module` |
| F8 | How a backtest fills its identity columns (`BacktestRequest.from_cli` + orchestrator `_persist_results`):<br>• `strategy_type` = the registry name (`sma_crossover`)<br>• `strategy_name` = `strategy_type.replace("_", " ").title()`<br>• `instrument_symbol` = bare symbol (`AAPL`)<br>• snapshot `strategy_path`/`config_path` = `StrategyDefinition.strategy_path`/`.config_path`<br>• snapshot `bar_type` = the spec part (`1-MINUTE-LAST`)<br>• `data_source` ∈ `catalog`, `catalog:<name>`, `ibkr`, `kraken`, `mock`<br>`StrategyDefinition.config_path` is `Optional`; `None` fails `StrategyConfigSnapshot` (`min_length=1`). | `src/models/backtest_request.py:376-390, 416`; `backtest_orchestrator.py:521-569`; `strategy_registry.py:48-62` |
| F9 | Measured statement sizes:<br>• `SessionService` class: 56 of 100<br>• `src/services/session_service.py`: 256 of 500<br>• `live.py`: 239 statement lines, but its docstring's discipline is "registration lines only": new commands get their own module (`live_status.py`, `live_reconcile.py`)<br>A new `src/cli/commands/live*.py` is globbed by `LIVE_MODULE_GLOBS` (the forbidden-order-method scan and the undeclared-import scan). It raises `SystemExit`, so it joins `EXEMPT_MODULES` (`test_live_node_never_exits.py`) in the commit that creates it, as `live_status.py` and `live_reconcile.py` did. The exit scan itself walks the hand-listed `NODE_FACING_MODULES`, so **nothing goes red if this is forgotten** — exactly CLAUDE.md's guard-list warning. | `measure_module`; `tests/component/core/test_live_dependency_invariance.py:39`; `tests/unit/core/test_live_node_never_exits.py:146` |
| F10 | AR5 today: `save_backtest_results` / `save_failed_backtest` / `create_backtest_run` are called only from `backtest_orchestrator.py` and `backtest_runner.py`; `BacktestRun(...)` is constructed only inside the two backtest repositories. No `live_*`, session-service, session-record or trade-record module references any of them. | `grep` over `src/`, AST-confirmable |
| F11 | `failure_message` shows a message verbatim only for classes marked `operator_safe_message`. `ValueError` (5.2's row refusals), `src.db.exceptions.ValidationError` (NaN/Infinity) and `DuplicateRecordError` carry no marker, so their text would be withheld behind a type name. | `src/core/live_check.py:302-322` |
| F12 | `DELETE /backtests/{run_id}` is a no-op today. But `trades.backtest_run_id` is `ON DELETE CASCADE`, so a future real delete of a sealed paper run would delete the session's trades, which are the system of record. That hazard comes with AR8's design and is outside this story (AR44). | `src/api/ui/backtests.py:728-763` (read only); `src/db/models/trade.py` |

### PO rulings — RULED 2026-10-09: R1a · R2a · R3a · R4a (all recommended, R4 refined)

PO notes with the ruling, binding on implementation:
- **R1a** — the `--starting-capital` value is stored exactly as given, with no normalization
  arithmetic. It is validated positive and whole-cent.
- **R2a** — the check is read-only, on the Redis engine cache, and **never contacts the broker**.
  It fails closed on any of: a held position, a position on another account, an unreadable cache.
  Nothing in the seal path may flatten, close or modify a position or an order. 5.5 adds
  `--force` later.
- **R3a** — one function owns `start_date`, so 5.4 can swap it. The CAGR-understatement caveat is
  recorded in the Dev Agent Record.
- **R4a, refined** — seal does not require or check the linked backtest *as a gate*. The report
  names the linked run, or `null`. **If a linked backtest is named but failed or has no metrics,
  the seal report prints a warning and still seals.** No refusal, and no create-time check. The
  warning appears in both the human and the `--json` output, with identical fields (AR29). → D-K.
- **Settled items accepted:**
  - `commissions[0]` stays unfixed on both sides; that disposition is recorded in the Dev Agent
    Record.
  - A truncated session seals like a complete one. Recording it is 5.4's, with no fifth status
    and no migration.
- **One-transaction seal approved.** It must reuse `BacktestPersistenceService` validation and
  `DuplicateRecordError` verbatim (AR12). **It must change session status only through
  `SessionService.transition()` (AR37).** → D-B amended: the seal calls `transition(to=SEALED)`
  itself. A separate, guarded `record_sealed_run` writes `sealed_run_id`. There is no
  `SessionService.seal()` that would reach `_apply_transition` around `transition()`.

### PO rulings — as REQUESTED (options presented; kept for the record)

- **R1 — Where the starting capital comes from.** It is needed for `initial_capital` (`NOT NULL`,
  `> 0`) and as `starting_capital` for the metrics. Today nothing holds it.
  - **[a] A required `--starting-capital <amount>` on `live seal` (recommended).** The operator
    states the paper account's starting balance. It is refused unless it is a positive, whole-cent
    `Decimal` (5.1's `_require_capital`, reached through 5.2). It is stored as-is: no default, no
    normalisation (AR7). The PRD's protocol is that the paper account is reset to the backtest's
    balance, so the operator knows the number. A wrong number is the operator's, and it is visible
    in the report.
  - **[b] The linked backtest's `initial_capital`,** refusing when the session has no link. This
    encodes "the accounts were matched" as an assumption the record cannot check.
  - **[c] [a], defaulting to [b] when a link exists.** Two sources for one number.
- **R2 — Open positions in 5.3** (5.5 adds `--force`, null-exit rows and the disposition wording).
  - **[a] Read the session's own engine-cache view and refuse while it holds anything
    (recommended).** The read happens after the state machine has accepted the seal, through
    Story 4.6's load-only `read_session_view` (Redis, no broker). The seal refuses with exit 1 when:
    - any position is open: each instrument and quantity is named, which is AR30's default;
    - `positions_skipped > 0`: positions on another account mean the session is not known to be
      flat;
    - the view cannot be read: it is never read as flat.

    A clean seal reports `open_positions: []` with the disposition "none open at seal". 5.5 then
    adds `--force`.
  - **[b] The trades table only** (`trade_counts_by_session`). This is always 0 for a live session
    (F5), so it reads a failure as flat. Not recommended.
  - **[c] No check until 5.5.** The report would say "not checked", and a seal could orphan a
    position silently in the meantime (FR47, AR30).
- **R3 — The run's `start_date`** (no durable first start; no migration).
  - **[a] `trading_sessions.created_at` (recommended).** Every session has one, and it is never
    after the first start. The period can include the gap between create and first start, so CAGR
    is biased low, never high. One named function owns it, so 5.4 (whose AC is "the session's
    first start") can replace the source.
  - **[b] The exact first start when it can be known** (`last_started_at` when `owner_epoch == 1`),
    else `created_at`. Two sources, and which one applies depends on the session's history.
  - **[c] The earliest trade's `entry_timestamp`, else `created_at`.** CAGR is biased high, and a
    zero-trade session falls back to `created_at` anyway.
- **R4 — The linked backtest** (routed debt: should seal require it to be `success` with metrics?).
  - **[a] Seal neither requires nor validates `linked_backtest_run_id` (recommended).** The report
    names it, or `null`. The link records FR15's intent and was checked to exist at `create`. A
    sealed record stands on its own, and a `stopped` session must always be sealable.
  - **[b] Refuse to seal** while linked to a backtest that is not `success` with a metrics row.

**Dispositions recorded without a ruling** (the PO may overrule either):
- **`commissions[0]`** (story-3.5 debt routed here): **not fixed on either side.**
  - No metric reads a commission column. `profit_loss` is net, and the backtest side's metrics come
    from the engine.
  - Paper rows already carry the recorder's settlement-currency `select_commission`.
  - Changing `save_trades_from_positions` breaks 5.2's zero-diff persistence and AR12's "verbatim".

  The only effect is the displayed commission on a mixed-currency backtest trade. Recorded in
  `deferred-work.md`.
- **A truncated session seals the same as a complete one** (routed debt): **accepted.** A fifth
  status needs a migration. The last run's `runtime_flags` (`failed_strategies`, `all_failed`)
  stay readable on the sealed row. Recording them in `session_conditions` is routed to Story 5.4,
  beside 5.8's withheld period.

### Design decisions (drafted on R1a / R2a / R3a / R4a)

- **D-A — `src/services/session_seal_service.py` owns the seal** (architecture.md:533 names the
  file).
  - Entry point: `async def seal_session(db: AsyncSession, identifier: str, *, starting_capital:
    Decimal, read_open_positions: OpenPositionsReader, clock: Callable[[], datetime] = _utc_now,
    new_run_id: Callable[[], UUID] = uuid4) -> SealReport`.
  - **It never commits or rolls back.** The caller owns the transaction (the `SessionService`
    precedent), so a test can drive it on the rolled-back `db_session` fixture.
  - Order, all in one transaction (F2):
    1. `sealed_at = clock()`, taken once; `run_id = new_run_id()`.
    2. `run_sync`: `SessionService(repo, time_source=lambda: sealed_at).resolve(identifier)`, then
       `.transition(row.session_id, to=SessionStatus.SEALED)`, then
       `.record_sealed_run(row.session_id, sealed_run_id=run_id)` (D-B). The edge is validated
       under `FOR UPDATE` first, so `running`/`sealed`/`created` refuse **before any other work**.
    3. `read_open_positions(session_id)` (R2a).
    4. `run_sync`: `find_session_trades(row.id)` (D-C).
    5. `reconstruct_session_results(trades, starting_capital=…, start=_period_start(row),
       end=sealed_at)`.
    6. Build the base snapshot (D-E), then `results.merged_into(snapshot)`.
    7. `await BacktestPersistenceService(BacktestRepository(db)).save_backtest_results(...)`.
    8. `run.run_type = PAPER_RUN_TYPE`, then `await db.flush()`.
    9. `run_sync`: `backfill_trades_run_id(row.id, run.id)`. Refuse unless the count equals the
       rows read.
    10. Build and return the `SealReport`.
  - Any raise anywhere leaves nothing behind once the caller rolls back.
  - *Why a new module, not a `SessionService` method:* `session_service.py` must not import
    Nautilus (`TestImportPurity`), and `session_equity` does. The direction is services → core,
    the architecture's own arrow (`session_seal_service.py ─▶ core/results_extractor.py`). AR38
    holds: `SessionService` and the repositories still import no Nautilus.
- **D-B (amended by the PO's AR37 note) — status changes through `SessionService.transition()`
  only.**
  - The seal calls the existing `transition(session_id, to=SessionStatus.SEALED)` unchanged. It
    validates `STOPPED → SEALED` under `FOR UPDATE` and stamps `sealed_at`.
  - A new `SessionService.record_sealed_run(session_id, *, sealed_run_id)` (a module-level
    `_record_sealed_run(row, *, sealed_run_id)` plus a thin delegator, the
    `record_strategy_failure` shape) writes **only** `sealed_run_id`. It has two guards, each an
    `InvalidSessionTransition`:
    - the row must already be `sealed` — so it can only follow a `transition()` in the same
      transaction;
    - `sealed_run_id` must still be `None` — write-once, mirroring `spec`.

    It never assigns `status`; AR37's AST guard sees only `status`, which is correct, not a
    loophole. It loads `FOR UPDATE`; in the same transaction the lock is already held, and
    `populate_existing` refreshes the identity-map copy.
  - `session_service.py` stays the one module that assigns `status`, and becomes the one that
    assigns `sealed_run_id`. Its docstring's "Does not own: … `sealed_run_id` (Story 5.3)" is
    updated. `_TIMESTAMPS_BY_TARGET`, `_LEGAL_TRANSITIONS` and `_apply_transition` are unchanged.
  - Never commits (`test_this_module_never_commits_or_rolls_back_the_transaction`).
- **D-C — Both repository twins gain two methods (AR9).** `EXPECTED_CAPABILITIES` is updated
  deliberately, in the same commit, and CLAUDE.md's `EXPECTED_CAPABILITIES` note says so.
  - `find_session_trades(session_pk) -> list[Trade]`: every row with `session_id == session_pk`,
    ordered by `Trade.id`. That is 5.2's deferred (e): read by DB id.
  - `backfill_trades_run_id(session_pk, backtest_run_id) -> int`: `UPDATE trades SET
    backtest_run_id = :run WHERE session_id = :pk AND backtest_run_id IS NULL`, returning the
    rowcount.

  Neither name starts with a forbidden mutator prefix, and neither touches `spec`. That keeps
  each class at about 86 of 100 statements.
- **D-D — `run_type` is stamped after the verbatim write (F1).** `PAPER_RUN_TYPE = "paper"`, a
  named constant. `backtest_persistence.py`, `backtest_repository.py` and
  `backtest_repository_sync.py` are zero-diff.
- **D-E — The run's identity comes from the stored spec, read as stored.** It is not re-validated
  through `SessionSpec.from_stored`: a session must always be sealable, and re-validation-on-read
  stays the unowned debt it is.
  - **One strategy only.** Otherwise the seal refuses: `live create` cannot make more, and trades
    carry no `strategy_id` to split them by.
  - `strategy_type` = the stored `strategy_id`.
  - `strategy_name` = `strategy_type.replace("_", " ").title()` (F8; pinned equal to that
    transform for every registered strategy).
  - `instrument_symbol` = the distinct `BarType.from_str(bt).instrument_id.symbol.value`, in spec
    order, joined with `,`. The seal refuses if that exceeds `String(50)`.
  - `config_snapshot`:
    - `strategy_path` / `config_path` from `StrategyRegistry.get(strategy_id)`. The seal refuses,
      naming the strategy, if it is unregistered or `config_path is None`.
    - `version: "1.0"`.
    - `config` = the stored `parameters`, already JSON-mode.
    - `bar_type` = the common spec part (`1-MINUTE-LAST`) when every bar type shares one, else
      `None`.

    The snapshot then goes through `results.merged_into(...)`.
  - `data_source = PAPER_DATA_SOURCE = "ibkr_live"` (5.4 may refine it).
  - `execution_duration_seconds` = the seal's own monotonic duration, quantised to `0.001` ("the
    time taken to produce this record").
  - `reproduced_from_run_id = None`.
- **D-F — Refusals are one operator-safe exception.** `SealRefusedError(BacktestStorageError)`,
  declared in `src/db/exceptions.py` beside `InvalidSessionTransition`, with
  `exit_outcome=CONFIG_ERROR` (exit 1) and `operator_safe_message=True`. It covers:
  - an open position (each named);
  - skipped positions;
  - a row-level `ValueError` from 5.2 (re-raised `from` it, its trade-naming text kept);
  - multiple strategies, an unregistered strategy, or an over-long symbol;
  - a backfill count mismatch.

  `InvalidSessionTransition`, `RecordNotFoundError` and `SessionViewUnavailableError` already
  carry the markers and propagate unchanged. `ValidationError` (NaN/Infinity) and
  `DuplicateRecordError` **propagate unchanged** from the persistence service (AR12 "verbatim").
  The CLI prints `"{TypeName}: {message}"` for those two: their text is this codebase's own (F11).
- **D-G — `src/cli/commands/live_seal.py`.** `live.py` gains exactly one line,
  `live.add_command(seal)`.
  - Command: `ntrader live seal <session> --starting-capital <amount> [--json]`.
  - Composition root: `asyncio.run(_seal(...))`. It opens `get_session()` (async) and calls
    `seal_session`, injecting `read_open_positions = lambda sid: read_session_view(sid,
    settings.ibkr.tws_account, settings.redis)`. It then `await db.commit()`s on success; a raise
    exits `async with` uncommitted, so nothing persists. A `finally` calls
    `dispose_all_connections()`, so no pooled `asyncpg` connection outlives the loop.
  - `--starting-capital` is parsed by a Click callback into a `Decimal`. A non-number is a usage
    error, exit 2. Sign and cents are refused by 5.1's rule (exit 1, operator-safe through D-F).
  - Exit codes: `0` sealed; `1` refused or database failure (AR28's table via `classify_failure`);
    `2` usage.
  - Under `--json`, a failure line goes to **stderr** (the `live_status` precedent: stdout carries
    nothing but JSON).
  - The module joins `EXEMPT_MODULES`. Its stdlib imports must already be on
    `_STDLIB_AND_FIRST_PARTY` (`asyncio`, `json`, `decimal`, `sys`, `uuid`, `typing` all are).
- **D-H — The report (AR29) has one payload, rendered twice.** `SealReport` is frozen, and
  `seal_json_payload(report) -> dict` has exactly these keys:

  ```text
  session_id, name, run_id, run_type, sealed_at, closed_trade_count, trades_backfilled,
  open_positions, open_positions_disposition, linked_backtest_run_id, linked_backtest_warning,
  execution_conditions
  ```

  - `open_positions` is a list of `{instrument_id, quantity}`, with quantity as a string.
  - `execution_conditions` holds:
    - `initial_capital` (Decimal as a string);
    - `start_date` and `end_date` (ISO-8601 UTC);
    - `data_source`;
    - `session_conditions`: the stored document, with `equity_series.curve` replaced by
      `curve_points: <n>`, so 5.4's keys appear without a change here.

  Values are snake_case, ISO-8601 UTC, and Decimal-as-string for money.
  `render_seal_report(report) -> list[str]` renders **from that payload**: one labelled line per
  top-level key, nested keys indented. So human and `--json` carry identical fields by
  construction, and a test pins `labels == keys`.

  `closed_trade_count` is `session_conditions.equity_series.closed_trades`, read from it rather
  than recomputed: one source.
- **D-I — AR5 is pinned against the code, both ways.** An AST scan of all `src/` collects:
  - every module that **calls** `save_backtest_results`, `save_failed_backtest` or
    `create_backtest_run`;
  - every module that **constructs** `BacktestRun(`.

  Those sets equal, exactly, `{backtest_orchestrator, backtest_runner, session_seal_service}` plus
  the defining persistence service and repositories. A new caller goes red, and so does a removed
  one. It is backed by an integration/db proof: create, then claim, then stop leaves
  `backtest_runs` empty; seal creates exactly one row.
- **D-J — The seal never reaches the broker and never flattens (AR31, NFR14).**
  - `session_seal_service.py` and `live_seal.py` import nothing from `ibapi` or
    `nautilus_trader.adapters`.
  - Neither calls any name in `FORBIDDEN_ORDER_METHODS`. Import the constant from
    `tests/unit/core/test_live_stop_path_is_inert.py`; never re-type it.
  - `live_seal.py` is also covered by `LIVE_MODULE_GLOBS`. The service is not, hence its own pin.
  - The only external read is the load-only session view (R2a).
- **D-K — The linked backtest is reported, never a gate (R4a, refined).** When
  `linked_backtest_run_id` is set, the seal reads it once, in the same transaction, through the
  existing `BacktestRepository.find_by_run_id` (async; `metrics` is `selectin`-loaded).
  - `linked_backtest_warning` is a string when:
    - the run is not `execution_status == "success"`;
    - it has no `metrics` row;
    - defensively, it is not found (the FK makes this unreachable today).

    Otherwise it is `null`, including when there is no link at all.
  - The seal **never refuses on it**, and `live create` is untouched.
  - The warning is part of the one payload (D-H), so the human and `--json` forms carry it alike.
  - A non-null warning is also logged once at WARNING as `session.seal_linked_backtest_warning`,
    naming the run id and the reason.

## Acceptance Criteria

Epic ACs 1–7 are copied verbatim from `_bmad-output/planning-artifacts/prd-epic5-scope.md` (Story
5.3). Each is followed by how this story proves it. ACs 8–11 are this story's own, from the
measured facts and the routed debt. *Proofs are written for R1a/R2a/R3a/R4a; a different ruling
amends them.*

1. **Given** `ntrader live seal <session>`
   **When** it is run on a **stopped** session
   **Then** it computes metrics, writes one `backtest_runs` row plus its `performance_metrics` row,
   backfills `backtest_run_id` on the session's trades, sets `sealed_run_id` and `sealed_at`, and
   transitions the session to `sealed` (FR40, FR42, AR8).
   *Proof (integration/db, test DB):* a `stopped` session with closed trade rows is sealed. Then:
   - exactly one new `backtest_runs` row exists, with `execution_status='success'` and a
     `performance_metrics` row whose `total_trades` equals the closed rows;
   - every session trade has `backtest_run_id == run.id`;
   - the session is `sealed`, with `sealed_run_id == run.run_id` and `sealed_at == run.end_date`;
   - the `live seal` CLI drives the same path end to end, with exit 0.

2. **Given** a session that is `running`
   **When** seal is attempted
   **Then** it is refused — sealing never happens to a live process out from under itself (FR40, AR37).
   *Proof:* `InvalidSessionTransition` is raised (CLI exit 1) before any other step runs:
   - the open-position reader is never called (asserted by a spy);
   - no run row is written, no trade changes, and the row's status and columns are unchanged.

   Proven in unit (the seal service over `SessionService.transition`) and integration/db.

3. **Given** a session that is already `sealed`
   **When** seal is attempted again
   **Then** it is refused, `sealed` being terminal (AR37).
   *Proof:* the same as #2, for `sealed`. `sealed_run_id` and `sealed_at` keep their first values,
   and the run count is still one. `created` refuses the same way: its only legal edge is
   `running`.

4. **Given** the created run record
   **When** its `run_type` is read
   **Then** it is `'paper'`, distinguishing it from a backtest with one column (FR44, AR6).
   *Proof (integration/db):* read back from Postgres, `run_type == "paper"`. A backtest row written
   in the same test still reads `'backtest'`.

5. **Given** the seal path
   **When** it writes
   **Then** it reuses `BacktestPersistenceService` NaN/Infinity validation and `DuplicateRecordError`
   handling verbatim (AR12).
   *Proof:*
   - (unit) the write goes through `BacktestPersistenceService.save_backtest_results` (spied), and
     `backtest_persistence.py` plus both backtest repositories are zero-diff;
   - (integration/db) a result carrying a NaN, stubbed past 5.2's guard, raises the service's own
     `ValidationError` and the transaction rolls back to nothing;
   - (integration/db) an injected `new_run_id` that collides with an existing run raises the
     repository's own `DuplicateRecordError`, and nothing is sealed.

6. **Given** no `backtest_runs` row existed for the session before this moment
   **When** the codebase is inspected
   **Then** no path creates one at session creation or start — the row exists only from seal onward
   (AR5).
   *Proof:* D-I's two-way AST pin, plus the integration/db round trip: create → claim → stop
   leaves zero runs, and seal leaves one.

7. **Given** the seal completes
   **When** the report is printed
   **Then** it names the run ID created, closed-trade count, open positions and their disposition, and
   the execution-conditions summary, in both human and `--json` form with identical fields (AR29).
   *Proof:*
   - (unit) `seal_json_payload` has exactly D-H's key set, and every value is JSON-native;
   - (unit) the human lines' labels equal the payload's keys;
   - (CLI unit) `--json` stdout is one parseable JSON document and nothing else, and failures go
     to stderr;
   - (integration/db CLI) the real JSON's `run_id` is the row's `run_id`.

8. **Given** R2a
   **When** the session's view holds an open position, holds positions for another account, or
   cannot be read
   **Then** the seal refuses (exit 1), naming each open instrument and quantity, and nothing is
   written. A view with no positions seals and reports `open_positions: []` with the disposition
   "none open at seal".
   *Proof:* unit tests (reader double) and integration/db (reader double, real Postgres).

9. **Given** R1a / R3a
   **When** the run row is written
   **Then** `initial_capital` is the operator's `--starting-capital` as given, and `start_date` is
   the session's `created_at`. `end_date` is the seal instant, which equals `sealed_at`. Capital
   that is not a positive whole-cent `Decimal` is refused before anything is written.

10. **Given** the seal path
    **When** its modules are inspected
    **Then** they reach no broker and call no order method, so nothing flattens, closes or modifies
    a position (AR31, NFR14). The only external read is the load-only session view (D-J).

11. **Given** the zero-change constraints
    **When** the diff is reviewed
    **Then** the following are untouched:
    - `src/api/**`, `templates/**` and `src/services/backtest_query.py` (AR44);
    - `src/core/backtest_orchestrator.py` and `src/core/results_extractor.py`;
    - `src/services/backtest_persistence.py` and both backtest repositories (AR12);
    - `src/db/models/**` and `alembic/**`.

    There is no new table, model, column, view or migration (FR42, AR43). The ORM table set and the
    single Alembic head stay pinned by 5.1's test.

12. **Given** R4a (refined) and a session linked to a backtest
    **When** that backtest failed or has no metrics row
    **Then** the seal still completes, and `linked_backtest_warning` names the reason in both the
    human and the `--json` report (identical fields). With a healthy link, or no link, the
    warning is `null`. No refusal is added at seal or at `create`.
    *Proof:* unit tests (repository double: failed, no metrics, healthy, none) and integration/db
    (a real failed linked run seals with the warning).

13. **Given** the PO's AR37 note
    **When** the seal moves the session
    **Then** it does so through `SessionService.transition(to=SEALED)` only, and
    `record_sealed_run` writes `sealed_run_id` and nothing else. It refuses unless the row is
    already `sealed` with no `sealed_run_id`.
    *Proof:* unit tests (a `transition` spy is called exactly once, with `to=SEALED`;
    `record_sealed_run` guards); the AR37 AST guard and the scoped-grep gates are unchanged and
    green.

## Tasks / Subtasks

- [x] **Task 0 — Gate and standing checks** (AC: all)
  - [x] 0.1 **PO rulings R1–R4 recorded in the Dev Agent Record before any edit.** If a ruling is
    not the recommended option, amend D-A / D-E / D-G and the AC proofs first.
  - [x] 0.2 `git status` clean. Record baselines with counts in the Debug Log:
    - `uv run pytest tests/unit -n auto`;
    - `uv run pytest tests/component` (sequential);
    - `uv run pytest tests/integration/db` (test DB up).

    Record any pre-existing failure before editing.
- [x] **Task 1 — Unit tier, red first** (AC: #2, #3, #5, #6, #7, #8, #9, #10)
  - [x] 1.1 `tests/unit/services/test_session_service.py` (additions only),
    `TestRecordSealedRun`:
    - on a `sealed` row with no run id, it writes `sealed_run_id` and nothing else (`status`,
      `sealed_at` untouched);
    - `RUNNING`, `STOPPED` and `CREATED` rows raise `InvalidSessionTransition`, columns unchanged;
    - a second write over an existing `sealed_run_id` raises;
    - the row is loaded `for_update=True`;
    - the existing AR37 AST guard and the scoped-grep gates still pass, unchanged.
  - [x] 1.2 `tests/unit/db/test_trading_session_repository_shape.py`: `EXPECTED_CAPABILITIES`
    gains `find_session_trades` and `backfill_trades_run_id`, edited deliberately.
  - [x] 1.3 New `tests/unit/services/test_session_seal_service.py`. The DB is a stub `AsyncSession`
    whose `run_sync(fn, *a)` calls `fn(sync_stub, *a)`. `reconstruct_session_results` and the
    persistence service are seams/spies, so the unit tier builds **no** `Portfolio`.
    - Ordering: a `running` session raises before the reader, the trades read or the write.
    - R2a: an open position, `positions_skipped > 0`, and a raising reader each refuse, naming
      the instrument and quantity; an empty view proceeds.
    - D-E: the identity mapping; refusals for multi-strategy, unregistered, `config_path is None`
      and an over-long symbol; `bar_type` common versus mixed (`None`); the `strategy_name`
      transform equals F8's for every registered strategy.
    - D-F: a 5.2 `ValueError` becomes `SealRefusedError`, with the trade id still in the message.
      A backfill count mismatch refuses.
    - AC #5 (unit half): the write is `save_backtest_results`, and `run_type` is set to `"paper"`
      after it.
    - AC #13: the session moves through `SessionService.transition(..., to=SEALED)` exactly once,
      then `record_sealed_run`, in that order.
    - AC #12 (D-K): the linked backtest is failed / has no metrics / is healthy / is absent →
      warning / warning / `None` / `None`. The seal completes in every case.
  - [x] 1.4 D-H: the payload key set (exact, including `linked_backtest_warning`); render labels
    equal keys; money as strings; ISO UTC; `curve` replaced by `curve_points`.
  - [x] 1.5 D-I: AR5's two-way AST pin (a non-vacuous sibling asserts the scan finds the
    orchestrator).
  - [x] 1.6 D-J: forbidden-order-method and broker-import pins over both new modules.
    `FORBIDDEN_ORDER_METHODS` is imported, never re-typed.
  - [x] 1.7 New `tests/unit/cli/commands/test_live_seal_cli.py` (the basename must be unique; the
    service is patched at its new-module target):
    - `seal` is registered on `live`;
    - exit 0, 1 and 2;
    - a non-numeric `--starting-capital` gives exit 2;
    - `--json` stdout is pure JSON, and the failure line goes to stderr;
    - refusal text is shown, because it is operator-safe;
    - `ValidationError`/`DuplicateRecordError` print as `TypeName: message`;
    - `get_settings()` is not called at import (the `live_status` probe);
    - AR36 vocabulary: no `close/halt/kill/pause/finalize` stems in the output (the
      `_ar36_violations` helper).
- [x] **Task 2 — Implement** (AC: #1–#10) — only after Task 1 is red for the right reason.
  - [x] 2.1 `SealRefusedError` in `src/db/exceptions.py`.
  - [x] 2.2 `SessionService.record_sealed_run` + `_record_sealed_run` (D-B, amended). Update the
    module docstring.
  - [x] 2.3 Both repository twins: `find_session_trades`, `backfill_trades_run_id` (D-C).
  - [x] 2.4 `src/services/session_seal_service.py` (D-A, D-D, D-E, D-F, D-H).
  - [x] 2.5 `src/cli/commands/live_seal.py` + the one `live.add_command(seal)` line. Add the
    module to `EXEMPT_MODULES` in the same edit (F9).
  - [x] 2.6 Size caps: new code under 500 / 100 / 50. `test_size_caps.py` must pass with **no**
    new baseline entry. If `test_regenerating_the_baseline_reproduces_it_exactly` needs it,
    regenerate exactly (memory: "size-cap baseline must regenerate exactly").
- [x] **Task 3 — Integration/db tier** (AC: #1–#6, #8, #9) — new
  `tests/integration/db/test_session_seal.py`, on the async `db_session` fixture (it rolls back).
  It skips cleanly without Postgres, as the folder's conftest does.
  - [x] 3.1 A seeded `stopped` session plus session-owned trade rows, built through the recorder's
    field mapping as 5.2's parity test does: one row per close, at least 2 winners and at least 2
    losers. Seal it. Assert everything in AC #1, plus AC #4 and AC #9 (read back by fresh
    `select`s, not the identity map: call `db.expire_all()` first).
  - [x] 3.2 Refusals write nothing: `running`, `sealed`, `created`, an open-position view, and a
    raising view. Count the runs and trades, and read the row's status before and after.
  - [x] 3.3 AC #5: NaN past the guard gives `ValidationError`; a colliding `new_run_id` gives
    `DuplicateRecordError`. After `rollback()`, the session is still `stopped` and no run
    exists.
  - [x] 3.4 Zero closed trades (5.2's R3a): the seal succeeds with `total_trades` 0,
    `win_rate`/`max_drawdown` NULL and `equity_series.closed_trades` 0.
  - [x] 3.5 AC #6 round trip: `SessionService.transition` CREATED→RUNNING→STOPPED leaves
    `backtest_runs` empty; seal leaves exactly one.
  - [x] 3.6 CLI end to end:
    - patch `src.cli.commands.live_seal.get_session` with a factory over the test schema
      (`test_db_schema`'s shape);
    - patch the session-view reader with an empty view;
    - run `ntrader live seal <name> --starting-capital 100000.00 --json`;
    - expect exit 0 and the payload's `run_id` equal to the row's.
- [x] **Task 4 — Docs and routed debt**
  - [x] 4.1 `docs/agent/persistence.md`: the seal path (one transaction, `run_sync`, `run_type`
    stamped after the verbatim write), and the F12 cascade hazard as a reader's caution.
  - [x] 4.2 `docs/agent/architecture.md`: `session_seal_service.py` and `live_seal.py` in the
    source tree.
  - [x] 4.3 `README.md`: `ntrader live seal` usage (CLAUDE.md: keep the README in sync; validate
    before editing).
  - [x] 4.4 CLAUDE.md: the `EXPECTED_CAPABILITIES` note gains Story 5.3's two names (both twins).
  - [x] 4.5 `deferred-work.md`, new "## Deferred from: story-5.3 (2026-10-09)":
    - (a) `commissions[0]`, dispositioned not-fixed, with the reason;
    - (b) truncated-session state → **5.4**;
    - (c) first-start source (R3) → **5.4**;
    - (d) F12 cascade hazard → whoever implements run deletion;
    - (e) → **5.5**: `--force`, null-exit rows and the disposition wording plug into
      `read_open_positions` and `open_positions_disposition`;
    - (f) `data_source = "ibkr_live"` → **5.4** may refine it.
  - [x] 4.6 Disposition every routed item in the Dev Agent Record, including 5.2's (e) ("how the
    seal calls this"), which this story **does**.
- [x] **Task 5 — Mutation sweep.** Copy the file to `/tmp/p53/`, mutate, run the target tests, and
  restore by copying back. **Never `git stash`.** Record kill or survive for each:
  - M1 `record_sealed_run`'s "must already be sealed" guard removed (a stopped row takes a run
    id) → 1.1;
  - M2 the open-position read happens before the state machine (running refusal calls the
    reader) → 1.3;
  - M3 `run_type` not stamped → 1.3 / 3.1;
  - M4 backfill drops the `backtest_run_id IS NULL` qualifier, or its count check → 1.3;
  - M5 `positions_skipped` ignored → 1.3 / 3.2;
  - M6 `end_date` taken from a second `clock()` call (≠ `sealed_at`) → 1.3 / 3.1;
  - M7 the payload gains a key the human render lacks → 1.4;
  - M8 `start_date = last_started_at` (R3b under an R3a ruling) → 1.3 / 3.1;
  - M9 the seal service commits → 1.3 (a stub asserts no `commit`/`rollback`);
  - M10 AR5 pin: a probe call to `save_backtest_results` planted in `live_start.py` → 1.5;
  - M11 the linked-backtest warning check inverted, or `metrics is None` ignored → 1.3 / 3.x;
  - M12 a failed link raises instead of warning → 1.3.
- [x] **Task 6 — Gates**
  - [x] 6.1 `uv run ruff format .` · `uv run ruff check .` · `make typecheck`.
  - [x] 6.2 Run each suite and record its counts against Task 0.2:
    - `uv run pytest tests/unit -n auto`;
    - `uv run pytest tests/component` (sequential);
    - `uv run pytest tests/integration/db`;
    - `uv run pytest tests/integration -n auto --forked`.
  - [x] 6.3 `git diff --stat` shows AC #11's zero-diff list untouched.

### Review Findings

Code review 2026-10-09, three parallel layers: Blind Hunter (diff only), Edge Case Hunter (diff +
project, probes in `/tmp/p53_ech/`) and Acceptance Auditor (diff + story + PRD extract + CLAUDE.md).
One finding was my own pass.
- The auditor found **no binding violation** of R1a/R2a/R3a/R4a, AR12, AR37 or the zero-change
  list. It rated AC #9 partial (the capital refusal is unproven and late) and the other twelve met.
- Triage after dedup of 36 raw findings: **2 decision-needed, 15 patch, 6 defer, 9 dismissed.**

Dismissed:
- **Autoflush before `record_sealed_run`'s locked re-read.** Production `get_session` uses the
  default `autoflush=True`, the same as the integration tests that prove it.
- **The account passed unstripped.** `read_session_view` normalises it.
- **The row lock held across the Redis read.** That is by design: AC #2 requires the state machine
  first, and the story's Hazards record it.
- **`sealed_run_id` written before the run row exists.** The column has no FK, by the model's
  design.
- **Backfill count versus `closed_trade_count`.** Session rows are strategy-owned only, and open
  rows are backtest parity.
- **The stdout guard's scope.** The docstring is accurate, and the Edge Case Hunter probed that
  Nautilus writes nothing to fd 1.
- **Bare symbols de-duplicated without venue.** That is the backtest convention.
- **AR29's "Rich tables".** D-H was PO-approved.
- **`live.py` gains two lines, not one.** The import is unavoidable.

**PO rulings on the review (2026-10-09): DN1a · DN2a · patches batch-applied (choice 0).**
- **DN1:** keep 5.3 as ruled. Open orders at seal go to Story 5.5, with the limitation recorded in
  the Dev Agent Record and the seal docs. No order read is added to `LOAD_METHODS`.
- **DN2:** keep the fail-closed reading of R2a. A session with no engine state stays refused as
  unreadable, and 5.5's `--force` is the way out. No exception for "no trade rows" or "never
  started".
- **Patch constraints:**
  - Validate the starting capital once, up front: a CLI usage error (exit 2) plus a refusal at
    seal start. Use 5.1's rule and Nautilus's maximum, tested at both tiers.
  - Store the capital as given, and report it as stored.
  - Out-of-range metrics refuse, naming the metric and its limit.
  - A committed seal is never reported as failed.
  - No AR44 path is touched, and neither is `live_gate`.
  - Database-only: no broker, position or order changes.
  - Split and disclose rather than exceed a cap; never re-baseline.

- [x] [Review][Decision] **RESOLVED DN1a — deferred to Story 5.5.** A resting order at the broker is not checked at seal time. R2a
  refuses on held positions, other-account positions and an unreadable cache, but the session view
  reads positions only. A stopped session can leave a GTC/stop order working at IBKR (the stop path
  sends no order method; Story 4.8's stranded orders). Sealing it is terminal. If the order later
  fills, the position belongs to no session, and the record says "none open at seal".
  Options:
  - (a) keep 5.3 as ruled and route open orders at seal to Story 5.5 (open positions at seal)
    with a recorded limitation;
  - (b) widen 5.3 to read the session's open orders from its engine cache and refuse while any is
    open. This widens `LOAD_METHODS`, an exact-pinned membership list, with `load_order`.
  (blind)
- [x] [Review][Decision] **RESOLVED DN2a — kept fail-closed; 5.5's `--force` is the way out.** A
  session with no engine state can never be sealed until 5.5's `--force`. R2a's literal rule refuses an unreadable cache, and `read_session_view` raises
  `NO_ENGINE_STATE` for an empty namespace. So a session whose every `live start` failed before the
  node was built (gate refusal, Redis or broker down), or whose Redis was flushed, is `stopped`
  with no view and refuses forever. Options:
  - (a) keep the ruling's fail-closed reading, and route this explicitly to 5.5 as `--force`'s
    escape hatch;
  - (b) treat `NO_ENGINE_STATE` as flat when the session has zero trade rows, or never claimed
    `owner_epoch == 0`. A narrower exception to R2a.
  (edge)
- [x] [Review][Patch] **Starting capital is judged late, its sign/cents refusal is untested in
  5.3, and an `ArithmeticError` or Nautilus's own text escapes** — AC #9 partial.
  - The fix: one `require_sealable_capital` applying 5.1's `_require_capital` plus Nautilus's
    `MONEY_MAX` bound. It is used by the CLI callback (usage error, exit 2, before any connection)
    and first thing in `seal_session` (`SealRefusedError`, before the lock).
  - `_reconstruct` also wraps `ArithmeticError`.
  - Tests: `-1`, `0`, `100000.001`, `1e15` at both tiers, and the integration case writes
    nothing.

  [src/services/session_seal_service.py:357; src/cli/commands/live_seal.py:49] (auditor+blind+edge)
- [x] [Review][Patch] **`sealed_at` is read before the row lock** — a stop holding the lock that
  commits `last_stopped_at = T1` after the seal read `T0` leaves `sealed_at < last_stopped_at`.
  Read the seal instant from `transition()`'s own time source after the lock (a read-once clock),
  and use that value for `end_date`. [src/services/session_seal_service.py:357] (blind)
- [x] [Review][Patch] **A metric beyond its column's precision fails as an unexplained
  `DataError`** — a 1-day session at +6% gives CAGR 1.75e9 against `Numeric(15,6)` (< 1e9). The
  operator sees only "DataError was raised". Refuse before the write with a `SealRefusedError`
  naming the field, its value and its column bound, read from `PerformanceMetrics`' own column
  types. [src/services/session_seal_service.py:364] (edge)
- [x] [Review][Patch] **A committed seal can be reported as failed** — after `commit()`, a raising
  `dispose_all_connections()` (or anything later) turns into "live seal failed" with a non-zero
  exit, and the retry is refused as already sealed. Post-commit cleanup must log, never fail the
  command. [src/cli/commands/live_seal.py:116-127] (blind+edge)
- [x] [Review][Patch] **Rollback is implicit** — the CLI relies on `AsyncSession.__aexit__` to
  roll back a failed seal. Roll back explicitly on any failure, and pin it with a test.
  [src/cli/commands/live_seal.py:116] (blind)
- [x] [Review][Patch] **The linked-backtest warning is logged before the write** — a seal that
  then rolls back (duplicate, NaN, backfill mismatch) still leaves
  `session.seal_linked_backtest_warning` in the log. Log it after the commit, from the CLI, with
  the report's warning. [src/services/session_seal_service.py:319] (auditor)
- [x] [Review][Patch] **Rich turns `:name:` into an emoji** — a session named `aapl:100:test`
  prints as `aapl💯test`, so `json.loads(out) != payload`, and long human lines hard-wrap at 200.
  Use `emoji=False` and `soft_wrap=True` on every print. [src/cli/commands/live_seal.py:181-186]
  (blind+edge)
- [x] [Review][Patch] **The report shows the capital as typed, not as stored** — `1e5` reads
  `"1E+5"` and `100000.0000` reads `"100000.0000"`, where `Numeric(20,2)` stores `100000.00`.
  Report the run row's own `initial_capital`. [src/services/session_seal_service.py:424] (edge)
- [x] [Review][Patch] **The open-position refusal recommends a dead end** — flattening at the
  broker leaves the engine cache unchanged (the seal still refuses), and the next start refuses a
  strategy position the broker no longer covers. Name only the path that works: restart so the
  strategy can exit, then stop and seal. [src/services/session_seal_service.py:249-250]
  (auditor+edge)
- [x] [Review][Patch] **The help and README say exit 0/1/2, but a refused Postgres connection exits
  4** — `classify_failure` maps `ConnectionRefusedError` to `BROKER_UNREACHABLE`. Document it as
  `live status --help` does. [src/cli/commands/live_seal.py:164-168; README.md] (auditor+edge+blind)
- [x] [Review][Patch] **`seal_session`'s Raises list leaves out the reader's errors** —
  `SessionViewUnavailableError` and `RedisUnreachableError`.
  [src/services/session_seal_service.py:349] (auditor)
- [x] [Review][Patch] **No integration test seals with a raising view** — Task 3.2 and AC #8's
  proof claim it. Add a raising reader that writes nothing, against Postgres.
  [tests/integration/db/test_session_seal.py] (auditor)
- [x] [Review][Patch] **No integration test seals with a healthy link** — only `failed` is
  proven with a real ORM row. Add a success-plus-metrics link that gives no warning.
  [tests/integration/db/test_session_seal.py] (blind)
- [x] [Review][Patch] **Three tests prove little** — each needs tightening:
  - `match="3"` matches any 3: match the full count phrase.
  - labels == keys is checked across two seals and top-level only: use one report and check
    nested keys too.
  - the `LEGS` fixture's quantity contradicts its P&L (10 × 10 ≠ 1000): set quantity 100.

  [tests/unit/services/test_session_seal_service.py; tests/integration/db/test_session_seal.py]
  (blind)
- [x] [Review][Patch] **The async twin's two new methods run nowhere** — AR9 parity keeps them,
  but nothing drives them. Exercise both against Postgres. [tests/integration/db/test_session_seal.py]
  (blind)
- [x] [Review][Defer] **An open strategy leg netted to zero by an opposite EXTERNAL leg reads as
  flat** [src/services/session_seal_service.py:246] — deferred to Story 5.5. The view is
  net-per-instrument by design (4.6). The broker is flat in that state, so nothing is orphaned
  there. (edge)
- [x] [Review][Defer] **A UUID-shaped session name resolves to another session's id first**
  [src/services/session_service.py `resolve`] — deferred, pre-existing in every `live` command
  (AR36 precedence). It is irreversible only here. Owner: `SessionService.resolve` / `live create`
  name validation. (edge)
- [x] [Review][Defer] **The linked-backtest check eager-loads every trade of the baseline**
  [src/services/session_seal_service.py:307] — deferred. `BacktestRun.trades` is
  `lazy="selectin"`, and both backtest repositories are on AC #11's zero-diff list. Owner:
  whoever adds a metrics-only lookup. (edge)
- [x] [Review][Defer] **"Database not configured" is reported by type only** — deferred,
  pre-existing. `live status` shares the same `failure_message` path. Owner: whoever unifies the
  `live` commands' database error rendering. (self)
- [x] [Review][Defer] **`live status --json` has the same Rich emoji substitution**
  [src/cli/commands/live_status.py:176] — deferred, pre-existing. AR44-adjacent, and not this
  story's module. (edge, by extension)
- [x] [Review][Defer] **Extreme CAGR/Calmar are unrepresentable in `Numeric(15,6)` on both
  sides** — deferred to an epic-level both-sides decision (no migration this phase). 5.3 only
  makes the refusal legible (patch above). (edge)

## Dev Notes

### The current surface — exact extension points

- `src/services/session_service.py:270-345` — `_apply_transition`. **Call it; do not copy it.**
  `_apply_seal` is a sibling at module scope, for the same two reasons the others are: AR37 is
  enforced per file, and the class cap applies.
- `src/core/session_equity.py:282-325` — `reconstruct_session_results` and
  `SessionResults.merged_into`. The ORM `Trade` satisfies `SessionTradeRow` (5.2 Debug Log; mypy
  confirmed).
- `src/services/backtest_persistence.py:72-152` — `save_backtest_results`, called unmodified on
  the async session.
- `src/core/live_session_view.py:217` — `read_session_view(session_id, account, redis)`.
  `src/core/live_reconcile.py:271` is the call shape to copy.
- `src/cli/commands/live_status.py:119-180` — the `--json` / stderr / `_exit_on_failure` idiom to
  mirror.
- `src/db/session.py` — `get_session()` (async; **does not commit**) and
  `dispose_all_connections()`.

### Sketch (shape, not a contract — the tests are the contract)

```python
async def seal_session(db, identifier, *, starting_capital, read_open_positions,
                       clock=_utc_now, new_run_id=uuid4) -> SealReport:
    started, sealed_at, run_id = time.monotonic(), clock(), new_run_id()
    row = await db.run_sync(_claim_for_seal, identifier, sealed_at, run_id)  # D-B: refuses first
    _refuse_if_held(read_open_positions(row.session_id))                      # R2a
    trades = await db.run_sync(_trades_of, row.id)
    results = _reconstruct(trades, starting_capital, _period_start(row), sealed_at)  # D-F wraps
    snapshot = results.merged_into(_base_snapshot(row.spec))                 # D-E
    run = await BacktestPersistenceService(BacktestRepository(db)).save_backtest_results(...)
    run.run_type = PAPER_RUN_TYPE
    await db.flush()
    backfilled = await db.run_sync(_backfill, row.id, run.id, expected=len(trades))
    return SealReport(...)
```

### Scope boundaries — do NOT build

- **No `--force`**, no null-exit rows, no "remain at the broker / no longer owned" wording: those
  are 5.5's. 5.3 refuses any open position (R2a).
- **No flat AR7 `session_conditions` keys** and no `schema_version`. `starting_capital`, `rth_only`,
  `price_basis`, `market_data_tier`, `closed_trade_count` and `open_positions_at_seal` are 5.4's.
  The base snapshot carries **no** `session_conditions` of its own; `merged_into` adds
  `equity_series`, and 5.4 adds its keys beside it.
- No change to `results_extractor.py`, `session_equity.py`'s math, `BacktestResult`,
  `BacktestPersistenceService`, the backtest repositories, any DB model or any migration (FR42,
  AR43, AR44, AR12).
- No broker read, no node, no order. NFR33: sealing is database-only. The session-view read is
  local Redis and already proven against a real Redis by Story 4.6's integration test, so **no
  P-number** is added to `docs/qa/phase3-live-verification.md`. **Never run `live seal` against a
  real session.**

### Hazards

- **Parallel stories.** 5.4 (conditions) and 5.5 (open positions) extend this story's seal in
  other worktrees. Keep the seams explicit and small so their merge is additive:
  - `_base_snapshot` (5.4 adds its keys before `merged_into`);
  - `_period_start` (5.4 may replace R3's source);
  - `read_open_positions` / `open_positions_disposition` (5.5).
- **The identity map.** `run_sync`'s sync `Session` *is* `db.sync_session`. A row loaded there is
  the same object the async side sees. The integration test must `expire_all()` before reading
  back, or it reads its own writes from memory.
- **`get_session()` never commits.** The CLI must `await db.commit()` explicitly. A test asserts
  a successful CLI seal is visible from a **new** session.
- **asyncpg and `asyncio.run`.** The module-global async engine pools connections bound to the
  loop. Dispose it in the CLI's `finally`, or a later `asyncio.run` in the same process inherits
  dead connections (`test_db_schema`'s own `_run_isolated` comment names the same loop hazard).
- **Lock order.** The seal holds the session row `FOR UPDATE` while it computes. A concurrent
  `live start` blocks on `transition()`'s own `FOR UPDATE` until the seal commits, then reads
  `sealed` and refuses. That is the correct outcome. Never take a second lock on that row from
  another connection inside the seal.
- **A test that cannot fail** (restated every story since Epic 2):
  - 1.4 needs its labels-equal-keys sibling;
  - 1.5 its non-vacuous sibling;
  - 3.2 must count rows **before and after**, not just "no exception";
  - 3.1 must read back through fresh queries.
- **AR36's word scan meets 5.2's key.** `_ar36_violations` (`test_live_status_cli.py`) scrubs
  `closed trade` and `closed_trade_count`, but the human render prints
  `equity_series.closed_trades`, and `close\w*` matches it. Extend the scrub to `closed_trades`,
  with the reason: AR36 bans lifecycle synonyms, not trade vocabulary (the Story 1.2 carve-out).
  Never rename 5.2's stored key.
- **Import placement.** Land each import with its first use in the same edit (F401 is
  unfixable). `live_seal.py` imports `asyncio`, `json`, `sys` and `decimal`, all already on
  `_STDLIB_AND_FIRST_PARTY`. Any other stdlib import needs a hand-added entry with its reason.

### Testing standards summary

TDD, with red recorded first.
- **Unit:** state machine, mapping, refusals, report, structural pins, CLI (patched).
- **Integration/db:** the one-transaction write, every refusal writing nothing, AR12's verbatim
  failures, zero trades, the AR5 round trip, and the CLI end to end.
- **Component:** none is needed. 5.2's component suite already proves `reconstruct_session_results`
  over a real `Portfolio`/`Cache`, and the seal adds no Nautilus object of its own.

No broker double is needed. The only external read (the session view) is doubled at its
callable seam.

### Project Structure Notes

- New:
  - `src/services/session_seal_service.py`
  - `src/cli/commands/live_seal.py`
  - `tests/unit/services/test_session_seal_service.py`
  - `tests/unit/cli/commands/test_live_seal_cli.py`
  - `tests/integration/db/test_session_seal.py`
- Modified:
  - `src/services/session_service.py` (`seal` + `_apply_seal`)
  - `src/db/exceptions.py` (`SealRefusedError`)
  - `src/db/repositories/trading_session_repository.py`
  - `src/db/repositories/trading_session_repository_sync.py`
  - `src/cli/commands/live.py` (one registration line)
  - `tests/unit/services/test_session_service.py`, additions only
  - `tests/unit/db/test_trading_session_repository_shape.py`, the deliberate
    `EXPECTED_CAPABILITIES` change
  - `tests/unit/core/test_live_node_never_exits.py`, `EXEMPT_MODULES`
  - `docs/agent/persistence.md`
  - `docs/agent/architecture.md`
  - `README.md`
  - `CLAUDE.md`
  - `_bmad-output/implementation-artifacts/deferred-work.md`
- Architecture alignment: `architecture.md:499, 533, 588` name `live.py … seal` and
  `services/session_seal_service.py ─▶ core/results_extractor.py`. The CLI module is split out
  under `live.py`'s registration-only discipline.

### References

- `_bmad-output/planning-artifacts/prd-epic5-scope.md`: the Story 5.3 ACs; AR5, AR6, AR8, AR12,
  AR29, AR30, AR31, AR37, AR38, AR43 and AR44; the routed-debt line for 5.3.
- `_bmad-output/planning-artifacts/architecture.md`:
  - `:212-237` D1 (runs row at seal only; the trades backfill);
  - `:326-335` D6 (seal-time open positions);
  - `:275-283` D8 (CLI, exit codes);
  - `:426-431` (the seal report).
- `_bmad-output/implementation-artifacts/5-2-reconstruct-a-sealed-sessions-equity-curve-from-its-trades.md`:
  D-A to D-H, F8, F10, and the Review Findings (`merged_into`).
- `_bmad-output/implementation-artifacts/deferred-work.md:2614-2630` (story-3.5's `commissions[0]`),
  `:4071-4155` (story-5.2 and its code review; item (e) is this story's).
- `tests/integration/db/test_session_equity_persistence.py`: async `db_session` persistence
  pattern.
- `tests/integration/db/test_live_status_e2e.py`: the CLI-against-test-DB patching idiom.

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`)

### Debug Log References

**Task 0.1 — PO rulings (2026-10-09): R1a · R2a · R3a · R4a (R4 refined).** These are recorded
verbatim under "PO rulings — RULED" above. Two design amendments came with them, both made in
the story before any code:
- **D-B is amended by the PO's AR37 note.** Status changes only through
  `SessionService.transition()`. The draft's `SessionService.seal()` → `_apply_seal` would have
  reached `_apply_transition` around `transition()`. It became `transition(to=SEALED)` plus a
  guarded, write-once `record_sealed_run` that writes `sealed_run_id` alone.
- **D-K / AC #12 are new (R4's refinement).** A failed or metric-less linked backtest is a
  warning in both report forms, never a refusal.

**Task 0.2 — baselines** (head `2ba84d0`; the tree was clean except the story file and the 5-3
key):
- `pytest tests/unit -n auto`: **3572 passed**. The run also collected 1.1's 8 new red tests,
  which failed with `AttributeError: … no attribute 'record_sealed_run'`.
- `pytest tests/component` (sequential): **1975 passed, 16 skipped**.
- `pytest tests/integration/db`: **119 passed**.
- No pre-existing failure. All three match 5.2's closing counts.

**Red first.**
- 1.1: 8 tests failed on the missing `record_sealed_run`.
- 1.2: the shape pin failed on both twins (the capability set lacked the two new names).
- 1.3–1.7: collection failed on the missing `src.services.session_seal_service` /
  `src.cli.commands.live_seal`.
- One test-side bug was found going green. `test_a_strategy_with_no_config_class_is_refused`
  built its spec *after* patching the registry, so spec validation tripped first. The spec is now
  built before the patch.
- One test was written after its code: the `--json` stdout guard (below). M13 proves it can
  fail.

**Measured during implementation.**
- **The probe's design held in the real code.**
  - `seal_session` takes an `AsyncSession`.
  - The state machine, the trades read and the backfill run in `db.run_sync(...)` on the same
    connection, and the backtest write runs on the async session itself.
  - A refusal rolled back by the test (as the CLI does) leaves the committed seeds untouched,
    asserted as before/after counts of runs, linked trades and status.
- **`run_sync` shares the identity map, and an expired attribute cannot lazy-load in async
  code.** The integration tests first failed with `MissingGreenlet`: they touched
  `row.created_at` after `expire_all()`. That is a test-side bug; the production path reads
  plain values inside `run_sync` (`_Claimed`). The seeds now return plain values, and
  `docs/agent/persistence.md` records the hazard.
- **`--json` would have been broken by the seal's own dependencies.** `configure_logging()`
  sends the console log handler to **stdout**, and `BacktestPersistenceService` and
  `read_session_view` log at INFO. `live status --json` never met this because it logs nothing.
  So `live_seal._stdout_reserved_for_json` does two things for the seal's duration:
  - it points every non-file root stream handler at stderr, restoring it afterwards;
  - it redirects `sys.stdout`, which covers structlog's default `PrintLogger` when logging is
    unconfigured, as in tests.

  This is pinned in both directions (`json`/`human`), and mutant M13 is killed.
- **AR36 vocabulary.** The human report prints 5.2's stored `closed_trades` key and its
  `closed_trade` granularity, and `close\w*` matches both. Both are scrubbed in the CLI test as the
  trade-vocabulary carve-out the helper already makes for "closed trade", never renamed. A guard
  asserts there is something to scrub.
- **context7 was not permitted in this session** (the 5.2 precedent).
  `AsyncSession.run_sync` was read from the installed SQLAlchemy 2.0.43 wheel and proven by the
  probe.
- **The trade rows are seeded through `insert_trade_if_absent`**, the recorder's own insert path,
  with `SqlTradeRecord.persist`'s column mapping by hand. `persist()` opens its own sync
  transaction, which the rolled-back async fixture cannot host. Recorded rather than glossed.
- **Sizes (`measure_module`).** Every one is under its cap, and `test_size_caps.py` passes with
  **no baseline entry**:

  | Module | File (statements) | Largest unit |
  |---|---|---|
  | `session_seal_service.py` | 289 | function `seal_session` 34, class `SealReport` 17 |
  | `live_seal.py` | 95 | `_stdout_reserved_for_json` 18 |
  | `session_service.py` | 275 | class `SessionService` 59 |
  | each `TradingSessionRepository` twin | — | class 88 |

**R3a caveat (recorded at the PO's request).** `start_date` is `trading_sessions.created_at`.
The session is created before its first start, so the record's period can include the
create→first-start gap. CAGR is therefore **understated, never overstated**. One function
(`session_seal_service.period_start`) owns the source so that Story 5.4 can swap it
(deferred-work "Deferred from: story-5.3" (c)).

**Task 5 — mutation sweep.** `/tmp/p53/mutate.py` mutated a copy of each file in memory, ran its
target tests, then restored the original bytes (`restored=True` every time; never `git stash`).
**19/19 killed.**

| # | Mutant | Killed by |
|---|---|---|
| M1 | `record_sealed_run`'s "must be sealed" guard removed | unit (`TestRecordSealedRun`) |
| M2 | open-position read before the state machine | unit |
| M3 | `run_type` not stamped | unit, integration/db |
| M4a | backfill count check removed | unit, integration/db |
| M4b | backfill's `backtest_run_id IS NULL` qualifier dropped | integration/db (a row linked elsewhere) |
| M5 | `positions_skipped` ignored | unit, integration/db |
| M6 | `end_date` from a second clock call | unit |
| M7 | a payload key missing from the human render | unit (labels == keys) |
| M8 | `start_date = last_started_at` (R3b under R3a) | unit, integration/db |
| M9 | the seal commits | unit |
| M10 | a run-row write planted in `live_start.py` | unit (AR5 AST pin) |
| M11 | a metric-less link not warned | unit |
| M12 | a failed link refuses instead of warning | unit, integration/db |
| M13 | `--json` stdout guard removed | CLI unit |
| M14 | the CLI never commits | CLI unit, integration/db (CLI e2e) |
| M15 | a non-finite capital accepted | CLI unit |
| M16 | no `TWS_ACCOUNT` accepted | CLI unit |
| M17 | status set directly, around `transition()` | unit (transition spy), AR37 AST guard |
| M18 | the reconstruction's refusal not wrapped | unit |
| M19 | held positions not refused | unit, integration/db |

M4b had no killer as first written: no test seeded a row already linked elsewhere. A test was
added (`test_a_trade_already_linked_elsewhere_refuses_rather_than_being_re_pointed`) before the
sweep was run.

**Task 6 — gates.**
- `ruff format .`: 576 files unchanged.
- `ruff check .`: all passed.
- `make typecheck`: no issues in 122 files. The new CLI module and both repositories, which are
  outside `make typecheck`'s scope, were also checked with `mypy` directly: no issues.
- Unit (`-n auto`): **3654 passed** (+82). All seven 5.3 epic criteria (5.3a–g) are evidenced.
- Component (sequential): **1975 passed, 16 skipped** (unchanged; the story adds no component
  test, per its testing standards).
- `tests/integration/db`: **134 passed** (+15).
- Full `tests/integration -n auto --forked`: **353 passed, 2 skipped** (the two IBKR-gated
  parity tests). 51/51 criteria are evidenced, including 5.3a–g against Postgres.
- `git status` over AC #11's list shows nothing under `src/api/`, `templates/`,
  `src/services/backtest_query.py`, `src/core/backtest_orchestrator.py`,
  `src/core/results_extractor.py`, `src/core/session_equity.py`,
  `src/services/backtest_persistence.py`, either backtest repository, `src/db/models/` or
  `alembic/`.

**Code review (2026-10-09). Three layers, ruled by the PO: DN1a, DN2a, and the patches
batch-applied (choice 0).**

**DN1a — a limitation, recorded here and in the seal docs.** A resting order at IBKR is not checked
at seal time. R2a's check reads positions (`read_session_view`), not orders, and the stop path
sends no order method. So a GTC or stop order that a stopped session left working is invisible to
the seal. If it fills afterwards, the position belongs to no session.
- `live seal --help`, the README and `docs/agent/persistence.md` all say "cancel it in TWS before
  sealing".
- No order read was added to `LOAD_METHODS`.
- Owner: Story 5.5 (deferred-work, code-review section).

**DN2a — kept fail-closed.** A session with no engine state stays refused as unreadable: one
whose every start failed before the node was built, or whose Redis was flushed. There is no "no
trade rows" or "never started" exception. 5.5's `--force` is the way out.

**Patches — red first.** The new tests failed for the right reasons before the code changed:
- the service file failed to collect, on `require_sealable_capital`;
- 14 CLI tests failed: capital usage errors, the explicit rollback, a committed seal, the masked
  failure, the post-commit warning log, the emoji, the hard wrap, and the help's exit-4 line.

Four tests pinned behaviour that already held, so they passed before any code change:
- the exit-4 mapping itself;
- the unreadable-view, healthy-link and async-twin integration cases.

Mutants M24, M27, M4b and others prove each of these can fail.

**What the patches changed:**
- **Capital — one rule, one place, up front.** `require_sealable_capital` applies 5.1's
  `_require_capital` (imported from its one home, `results_extractor.py`, which stays zero-diff)
  plus Nautilus's `MONEY_MAX` (17014118346046).
  - The CLI callback turns a refusal into a usage error (exit 2) before anything is opened.
  - `seal_session` applies it first, before the lock, the clock or the cache.
  - The CLI's own `is_finite` check became redundant: M15 survived as an equivalent mutant, since
    5.1's rule already refuses NaN and Infinity. It was removed.
  - The capital is stored as given. The report reads `run.initial_capital` back: `1E+5` is stored
    and reported as `100000.00`.
- **The seal instant follows the lock.** `_ReadOnce(clock)` is `transition()`'s own
  `time_source`. `transition()` reads it after its `FOR UPDATE`, and the same instant is
  `end_date`.
- **A metric beyond its column is refused by name.** `_require_storable` reads each `Numeric`
  column's precision and scale from `PerformanceMetrics`. It checks only the `BacktestResult`
  constructor's own fields: `win_rate` is a percentage property on the result but a fraction the
  persistence service computes for the column. The first unit run caught exactly that mismatch
  (66.67 against `Numeric(5,4)`). Non-finite values are skipped, so NaN stays the persistence
  service's own refusal (AR12; M24 is killed).
  - Measured at the integration tier: one day at +6% on 10,000.00 gives `cagr` ≈ 1.75e9, refused
    as `Numeric(15,6)`.
- **A committed seal is never reported as failed.** `_Outcome.report` is set the moment the
  commit lands. After that, a cleanup error is a stderr note beside the report with exit 0,
  whether it is `dispose_all_connections` raising or Ctrl-C during it.
  - A pre-commit failure is never masked by a failing cleanup.
  - A failed seal is rolled back explicitly.
- **The linked-backtest warning is logged after the commit**, from the command, together with
  `session.sealed`. The service only computes it.
- **Printing** goes through `_print`: `emoji=False` (`aapl:100:test` stays as typed) and
  `soft_wrap=True` (a 300-character warning stays on one line).
- **Exit 4** for a refused Postgres connection is documented in the help and the README, the
  `live status` precedent. `seal_session`'s `Raises` list now names the reader's errors.
- **The open-position refusal** names only the remedy that works: restart so the strategy can
  exit, then stop and seal.
- **Tests:**
  - integration cases added for an unreadable view, a healthy link, the async twin's two methods,
    four refused capitals, the column bound, and capital as stored;
  - the `LEGS` quantity fixed to 100;
  - `match` tightened to the full count phrase;
  - labels == keys now checked on one report, at every level.

**Post-review mutation sweep: 32/33 killed, 1 equivalent and since removed** (`restored=True`
every time; never `git stash`). M1–M19 were re-targeted to the patched code. The new mutants:

| # | Mutant | Killed by |
|---|---|---|
| M20 | the seal's up-front capital check removed | unit |
| M21 | Nautilus's largest amount not enforced | unit |
| M22 | the seal instant read before the lock | unit (lock/clock order) |
| M23 | the column-bound check removed | unit, integration/db |
| M24 | the column-bound check shadows NaN | unit, integration/db |
| M25 | no explicit rollback | CLI unit |
| M26 | a committed seal reported as failed | CLI unit |
| M27 | a cleanup failure masks the seal's own failure | CLI unit |
| M28 | the linked warning logged before the commit | CLI unit |
| M29 | Rich emoji substitution back on | CLI unit |
| M30 | the capital reported as typed | unit, integration/db |
| M31 | `win_rate`'s percentage checked against the fraction column | unit |
| M32 | the CLI's up-front capital check removed | CLI unit |

**Post-review gates.**
- `ruff format .`: 576 unchanged.
- `ruff check .`: all passed.
- `make typecheck`: 122 files, no issues; `mypy src/cli/commands/live_seal.py` is also clean.
- Unit (`-n auto`): **3685 passed** (+31 since dev).
- Component (sequential): **1975 passed, 16 skipped** (unchanged).
- `tests/integration/db`: **143 passed** (+9).
- Full `tests/integration -n auto --forked`: **362 passed, 2 skipped**.
- The zero-diff list (AC #11) is still untouched, and `src/core/live_gate.py` is untouched too
  (PO constraint).

**Post-review sizes.** No cap was exceeded, no split was needed, and nothing was re-baselined;
`test_size_caps.py` passes 14/14.

| Module | File (statements) | Largest units |
|---|---|---|
| `session_seal_service.py` | 326 | `seal_session` 35, `run_identity` 30 |
| `live_seal.py` | 131 | `_seal` 19 |

### Completion Notes List

- **AC #1 (5.3a).** `ntrader live seal <session> --starting-capital <amount>` runs
  `seal_session` in one transaction:
  - `SessionService.transition(to=SEALED)` under `FOR UPDATE`;
  - `record_sealed_run`;
  - the session's open-position view;
  - `find_session_trades`;
  - 5.2's `reconstruct_session_results`;
  - `BacktestPersistenceService.save_backtest_results` (one `backtest_runs` row plus its
    `performance_metrics`);
  - `run_type = 'paper'`;
  - `backfill_trades_run_id`.

  The CLI commits only when the seal returns. This is proven against Postgres, including the CLI
  end to end.
- **AC #2 / #3 (5.3b, 5.3c).** A `running`, `sealed` or `created` session is refused by the state
  machine **before any other step**: no reader call, no trades read, no write. Before/after row
  counts show nothing changed, and a sealed session keeps its first `sealed_run_id`.
- **AC #4 (5.3d).** `run_type` reads `'paper'` from Postgres. A backtest written beside it still
  reads `'backtest'`.
- **AC #5 (5.3e).** The write is the unmodified `save_backtest_results`. In the integration tests:
  - a NaN stubbed past 5.2's guard raises the persistence service's own `ValidationError`;
  - a colliding `run_id` raises the repository's own `DuplicateRecordError`.

  Both unwind the whole seal. `backtest_persistence.py` and both backtest repositories are
  zero-diff.
- **AC #6 (5.3f).** A two-way AST pin over all of `src/` checks which modules write a run row:
  - the callers are exactly the orchestrator, the runner, the persistence service and the seal;
  - the constructors are exactly the two backtest repositories.

  A create → start → stop round trip leaves zero runs, and the seal leaves one.
- **AC #7 (5.3g).** `SealReport` is one payload rendered twice (the human lines' labels equal the
  JSON keys). It names:
  - the run id;
  - the closed-trade count (read from the series, one source);
  - `open_positions` and their disposition;
  - the linked backtest and its warning;
  - the execution conditions: initial capital, period, data source, and the stored
    `session_conditions` with the curve summarised as `curve_points`.

  Under `--json`, stdout carries the document alone; logs and failures go to stderr.
- **AC #8 (R2a).** Held positions, positions on another account, an unreadable view and an unset
  `TWS_ACCOUNT` each refuse (exit 1) and write nothing. Every held instrument and quantity is
  named.
- **AC #9 (R1a / R3a).** `initial_capital` is the operator's value, stored as given and reported
  as stored. The capital is judged once, up front, by 5.1's rule plus `MONEY_MAX`: a usage error
  in the CLI before anything opens, and a refusal at seal start before the lock. `start_date` is
  `created_at`. `end_date == sealed_at`, read once by `transition()` after the row lock.
- **AC #10.** Neither new module calls an order method or anything with `flatten` in its name,
  and neither imports `ibapi`, `nautilus_trader.adapters` or `nautilus_trader.live`. The CLI is
  also under `LIVE_MODULE_GLOBS`' scans.
- **AC #11.** Zero diff on every listed path. There is no table, model, column, view or
  migration; `src/db/models/**` and `alembic/**` are untouched.
- **AC #12 (R4a, refined).** A failed, metric-less or missing linked backtest yields
  `linked_backtest_warning` in both forms, plus one `session.seal_linked_backtest_warning` log.
  The seal still completes, and no refusal was added at seal or at `create`.
- **AC #13 (AR37 note).** `transition()` is called exactly once (spied). Bypassing it (M17) is
  killed by the spy and by the AR37 AST guard. `record_sealed_run` refuses a non-sealed row and a
  second write.
- **Dispositions:**
  - **5.2's (e) "how the seal calls this"** — **done**, exactly as routed: rows by DB id, then
    `reconstruct_session_results(rows, starting_capital=…, start=<period start>,
    end=<seal time>)`, then `merged_into`, then `save_backtest_results`.
  - **`commissions[0]`** — **not fixed on either side** (PO-accepted). No metric reads a
    commission column, and changing the backtest side breaks AR12 and 5.2's zero-diff
    persistence. Recorded in deferred-work (a).
  - **Truncated session** — **accepted**. It seals like a complete one, with no fifth status and
    no migration. Recording it is routed to 5.4 (deferred-work (b)).
  - **Whether seal should require a `success` linked backtest** — **decided by R4a, refined**:
    report and warn, never gate.
- **New debt** (`deferred-work.md`, "Deferred from: story-5.3"):
  - (c) the R3a first-start source → 5.4;
  - (d) a future run delete would cascade into a session's trades;
  - (e) where 5.5's `--force` plugs in;
  - (f) `data_source` wording → 5.4;
  - (g) `run_type` stamped after the write.
- **No broker, no order, no P-number.** Sealing is database-only plus a load-only Redis read,
  already proven against a real Redis by Story 4.6. `live seal` was never run against a real
  session.
- **After code review** (DN1a, DN2a, 15 patches; the Debug Log has the detail):
  - **Limitations recorded:**
    - a resting order at IBKR is not checked — cancel it in TWS first (→ 5.5);
    - a session with no engine state stays refused (→ 5.5's `--force`).
  - **Behaviour now:**
    - the capital is judged once, up front;
    - the seal instant comes after the lock;
    - an out-of-range metric is refused by name;
    - a committed seal is never reported as failed, and a failed one is rolled back explicitly;
    - the linked warning is logged only after the commit;
    - output is emoji-free and unwrapped, and the report shows the capital as stored;
    - exit 4 is documented.
  - **Proof:** 32/33 post-review mutants killed; the 33rd was equivalent and its redundant code
    removed.

### File List

- `src/services/session_seal_service.py` — new. Contains:
  - `seal_session`, `SealReport` and `RunIdentity`;
  - `run_identity`, `period_start` and `strategy_display_name`;
  - `seal_json_payload` and `render_seal_report`;
  - the constants `PAPER_RUN_TYPE`, `PAPER_DATA_SOURCE`, `NONE_OPEN_DISPOSITION`,
    `INSTRUMENT_SYMBOL_MAX` and `LINKED_BACKTEST_WARNING_EVENT`.
- `src/cli/commands/live_seal.py` — new: the `live seal` command, `read_open_positions`, and the
  `--json` stdout guard.
- `src/cli/commands/live.py` — modified: the import and the one `live.add_command(seal)` line.
- `src/services/session_service.py` — modified: `record_sealed_run` + `_record_sealed_run`, and
  the docstrings.
- `src/db/exceptions.py` — modified: `SealRefusedError`.
- `src/db/repositories/trading_session_repository_sync.py` — modified: `find_session_trades`,
  `backfill_trades_run_id`.
- `src/db/repositories/trading_session_repository.py` — modified: the same two, async (AR9).
- `tests/unit/services/test_session_seal_service.py` — new.
- `tests/unit/cli/commands/test_live_seal_cli.py` — new.
- `tests/integration/db/test_session_seal.py` — new.
- `tests/unit/services/test_session_service.py` — modified (additions only:
  `TestRecordSealedRun`).
- `tests/unit/db/test_trading_session_repository_shape.py` — modified: `EXPECTED_CAPABILITIES`
  +2, deliberately.
- `tests/unit/core/test_live_node_never_exits.py` — modified: `live_seal.py` joins
  `EXEMPT_MODULES`.
- `docs/agent/persistence.md` — modified: the seal's one transaction, `SealRefusedError`, and
  the cascade hazard.
- `docs/agent/architecture.md` — modified: the source tree.
- `README.md` — modified: the `live seal` rows and paragraph.
- `CLAUDE.md` — modified: the `EXPECTED_CAPABILITIES` note.
- `_bmad-output/implementation-artifacts/deferred-work.md` — modified: "Deferred from:
  story-5.3".
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — modified: the 5-3 key only.
- `_bmad-output/implementation-artifacts/5-3-seal-a-stopped-session-into-a-comparable-run-record.md`
  — new (this story).

## Change Log

- 2026-10-09 — Story drafted (create-story). F1–F12 measured at `2ba84d0`, including a probe of
  one transaction across `AsyncSession.run_sync` and the async persistence service (rollback
  leaves nothing; commit stamps `paper`/`sealed`/`sealed_run_id`). PO rulings R1–R4 requested
  before dev; D-A to D-J drafted on the recommended options.
- 2026-10-09 — PO ruled R1a / R2a / R3a / R4a (R4 refined to a warning in both report forms;
  status only through `transition()`). D-B amended (`record_sealed_run`), D-K / AC #12–#13 added.
  Implemented (dev-story):
  - `session_seal_service.seal_session`, one `run_sync`-bridged transaction;
  - `SessionService.record_sealed_run`;
  - `find_session_trades` / `backfill_trades_run_id` on both twins;
  - `SealRefusedError`;
  - `ntrader live seal` with a `--json` stdout guard.

  19/19 mutants killed; no size-cap overage. Status → review.
- 2026-10-09 — Code review (bmad-code-review, three layers: Blind Hunter / Edge Case Hunter /
  Acceptance Auditor). 36 raw findings triaged to 2 decision-needed, 15 patch, 6 defer and 9
  dismissed. The PO ruled DN1a and DN2a (both routed to 5.5, with the limitations recorded) and
  had all patches batch-applied (choice 0). The headline patches:
  - capital judged up front;
  - the seal instant after the lock;
  - a column-bound refusal;
  - a committed seal never reported as failed, with an explicit rollback;
  - the linked warning logged after the commit;
  - no emoji or wrap in output;
  - capital reported as stored;
  - more integration proofs.

  32/33 mutants killed (1 equivalent, its redundant code removed). Status → done.
