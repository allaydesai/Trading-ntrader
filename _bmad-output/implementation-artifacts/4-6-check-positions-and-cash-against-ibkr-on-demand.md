# Story 4.6: Check Positions and Cash Against IBKR on Demand

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to run a reconciliation check whenever I want reassurance,
so that I can confirm the system's picture of reality matches what TWS shows me.

## Why this story is shaped the way it is

Read this before designing anything. The epic says "compare the broker's positions and cash
against the session's local view". Architecture G3 (`architecture.md:696-700`) names that view
"trades-derived". **That view cannot represent an open position, so this story does not use it.**
Everything below was measured at drafting (2026-09-27, installed `nautilus-trader 1.220.0`, head
`32f852f`). Wheel paths are relative to `.venv/lib/python3.11/site-packages/nautilus_trader/`;
ripgrep skips `.venv`, so search it with `grep -n` on explicit paths.

**1. The DB has no open positions and no cash.**
- `TradeRecorder` persists only `PositionClosed`. `PositionOpened` and `PositionChanged` go to
  `_ignore` (`src/core/live_trade_recorder.py:356-369`).
- Reconciliation-stamped positions (`EXTERNAL`, `INTERNAL-DIFF`) are never persisted (`:144-148`,
  `:436-445`).
- Every live `trades` row is a completed round trip that nets to zero. A trades-derived net
  position is therefore **always flat**, which is exactly the "failure reads as flat" shape NFR20
  and Story 4.1 exist to prevent.
- Nothing stores a session's cash or starting capital (`trading_sessions` columns,
  `src/db/models/trading_session.py:70-131`; `runtime_flags` holds only failure and rejection
  snapshots).

**2. The session's own record of what it holds is its durable engine cache in Redis** (Story
2.4). The namespace is `trader-PAPER-<8hex>:` with `derive_trader_id(session_id)`. Architecture
D2 already calls it "a cache, IBKR is truth", and Story 4.1 framed it the same way (D-A: the
post-reconciliation cache is "exactly what 4.2/4.6 must compare **against**"). The p7-fill-0901
phantom (Epic 3 retro, `epic-3-retro-2026-09-21.md:195-196`: Redis held a position the broker did
not) is precisely the disagreement this command exists to name.
- **Positions:** `positions:<position_id>` holds the position's fill events. It is rebuilt with
  `CacheDatabaseAdapter.load_position`, which replays them; the instrument comes from
  `instruments:<id>`.
- **Cash:** `general:accountSummary:<account>` holds the IB exec client's full `_account_summary`
  JSON, including `TotalCashValue` per currency (Story 4.1, F6). This is the last cash the
  session's engine was told, and the same tag the broker reader reads.

**3. Reading that namespace from another process is write-free. Measured, not assumed.**
`/tmp/p46/probe_view.py`: a scratch namespace, a position and an `accountSummary` key written by
one adapter, then read by a second adapter with a fresh `instance_id`, with Redis `MONITOR`
capturing every command.
- `load_positions()` and `load()` issued only `CLIENT SETINFO`, `INFO`, `SCAN`, `LRANGE`, `GET`
  and `MGET`: **zero write verbs**.
- The namespace was byte-identical before and after.
- The read took 2–5 ms.
- `/tmp/p46/probe_logging.py`: constructing the adapter, loading and closing it left
  `is_logging_initialized()` **False** throughout, so it neither needs nor claims Nautilus's C
  logging.

Those probes are not committed. Task 1 re-runs them, and Task 5 pins them at integration tier.

**4. The reconcile node must not be the session's node.** It must also not hold `ibkr_live_client_id`
(FR5/AR34), and must not touch the session's Redis through a node:
- A node built on the session's cache config would run Nautilus's own startup reconciliation
  *into the running session's namespace*. That is an auto-resolution by a second writer, which
  FR35 forbids.
- So the reconcile node gets an **in-memory cache**, **no strategy, controller, actor or bar
  observer**, and client id `ibkr_live_client_id + 1`.
- The session's namespace is read only through the adapter's load methods (point 3).

### Measured facts — cite, don't re-derive

| # | Fact | Where |
|---|---|---|
| F1 | Only closed trades reach `trades`; no open-leg row, no signed quantity, no cash column. The live `open_positions` count is always 0 (`trade_counts_by_session`'s docstring at `:162-164` is stale). | `src/core/live_trade_recorder.py:264-303, 356-369`; `src/db/repositories/trading_session_repository_sync.py:150-196` |
| F2 | `build_cache_config` pins `use_trader_prefix=True`, `use_instance_id=False`, `flush_on_start=False` and `encoding="msgpack"`. A fresh adapter on the same `trader_id` therefore reads the same keys a restarted session would. The serializer is `MsgSpecSerializer(encoding=msgspec.msgpack, timestamps_as_str=True)`, the shape `NautilusKernel` builds. | `src/core/live_cache.py:135-156`; `tests/integration/core/test_live_order_survives_restart.py:76-85` |
| F3 | `CacheDatabaseAdapter.__init__` **blocks forever** against an unreachable Redis; `check_redis_reachable` (a stdlib PING needing `+PONG`) must run first. A failure is `RedisUnreachableError`, exit 1. | `src/core/live_cache.py:21-37, 159-259` |
| F4 | `load_positions()` scans `positions*`, calls `load_position` per id, and **silently drops** any it cannot build: a missing `instruments:<id>` logs an error and returns `None` (`cache/database.pyx:474-500, 709-753`). `load_position` raises `RuntimeError("Corrupt cache with duplicate event …")` on a duplicated event. A dropped position would be a partial "flat", so the reader must enumerate the keys itself and fail loudly. | `cache/database.pyx:474-500, 709-753` |
| F5 | `add_position` **appends** the opening fill to the `positions:<id>` list (`:1025-1045`). A NETTING close-then-reopen, or a flip, reuses the id. `Position.apply` resets all state when the position is FLAT before a fill (`model/position.pyx:470-510`), so replaying the whole list gives the **current** position. `Position.signed_decimal_qty()` exists (`position.pyx:438`). | as cited |
| F6 | Open positions per instrument can be several: one per strategy under NETTING (`<instrument>-<strategy>`), plus the `EXTERNAL`/`INTERNAL-DIFF` ones Nautilus's reconciliation creates. The session's net view of an instrument is the **sum** of signed quantities over its open positions. The broker reports one net row per contract. | `src/core/live_trade_recorder.py:97-103` |
| F7 | `load()` returns every `general:*` key, prefix stripped, as `{name: bytes}` (`:255-288`). The adapter writes `accountSummary:<raw account>`, so the key name **contains the raw account** and must never be logged or rendered (NFR26). The JSON is `{currency: {tag: value}}`; the `""` currency holds currency-less tags. | `adapters/interactive_brokers/execution.py:841-844`; Story 4.1 F5/F6 |
| F8 | The builder has **no client-id parameter** and forces `ibkr_read_only=False` (`live_node_builder.py:346`). `build_trading_node_config` is on the size-cap ratchet at 128 (`tests/unit/governance/test_size_caps.py:136`), so no parameter can be added. `settings.model_copy(update={"ibkr_live_client_id": live + 1})` moves the data client, the exec client, `gateway_reported_accounts`' `IB_CLIENTS` key (`live_account_gate.py:84`), `build_clients`' log line and `endpoint()` together. `validate_client_ids_distinct` has already validated `live + 1` on the original (`src/config.py:119-151`). | `src/core/live_node_builder.py:249-259, 346, 356, 376` |
| F9 | Nothing may branch on `ibkr_read_only` (AR43). One test pins it to exactly one appearance in the builder, and another scans every `src/core/live_*.py` and `src/cli/commands/live*.py` for it inside a condition. **"Read-only" is structural:** no strategy, no controller, no observer, an in-memory cache, and load-only access to the session's namespace. | `tests/component/core/test_live_node_builder.py:448-459`; `tests/integration/core/test_epic1_ac_node.py:231-263` |
| F10 | `live check` is the node-lifecycle precedent. Reuse its helpers rather than its driver: `current_event_loop` / `restore_event_loop`, `build_clients` (retry budget `"1"`), `await_connected`, which polls `check_connected()` and raises `BrokerUnreachableError` (exit 4), and `shutdown`, which is guarded at every step, catches `BaseException` and never masks the outcome. The `_drive` driver itself requires a `LiveBarObserver` and cannot be reused (`find_observer`, `:142-150`). The trader id must contain `-`, or Rust aborts the process (`_validate_trader_id`). | `src/core/live_check_node.py`; `src/core/live_check_driver.py:195-229`; `src/core/live_node_builder.py:180-206` |
| F11 | Gates:<br>• Layer 1 is `preflight_gate(settings, cli_flags)`, which **returns** a refusal report rather than raising (`live_check.py:181-256`). The runner converts it with `GateRefusedError(refusal_from_report(report))` (`live_session_runner.py:489-497`), and the builder re-runs the gate, deliberately.<br>• Layer 2 is `verify_connected_account(node, settings, *, cli_flags=None, reported_accounts=...)`, which raises `GateRefusedError` (exit 3).<br>• No `live` command has `--real-money`. `TestNoRealMoneySurface` AST-scans `live.py` for `GateFlags(` and `cli_flags=`/`real_money=` keywords. | `src/core/live_gate.py:143-174`; `src/core/live_account_gate.py:59-175`; `tests/unit/cli/commands/test_live_cli.py:286-330` |
| F12 | Exit codes: `EXIT_CODES` lives in `src/core/exit_outcome.py:46-56`. `test_every_outcome_has_an_exit_code` and `test_only_ok_maps_to_zero` (`tests/unit/core/test_live_check.py:88-95`) loop the enum, so a new outcome needs a code. The pre-work note (`epic4-prework-spec.md:331-336`): add `DISCREPANCY` to `LiveCheckOutcome` with a new distinct code, document it in README's table, and leave `classify_failure` unedited. | as cited |
| F13 | CLI conventions:<br>• `live.py` may only gain `live.add_command(...)` lines (`live_status.py:3-5`), so a new command goes in its own module.<br>• Output is a Rich `Console` with `markup=False, highlight=False`.<br>• The read-only session-resolution pattern is `live_status._load_report` (`live_status.py:55-82`): `with get_sync_session() as db: SessionService(SyncTradingSessionRepository(db)).resolve(identifier)`, which tries UUID then name and raises `RecordNotFoundError` (exit 1).<br>• The failure exit helper is `_exit_on_failure` (`live_status.py:119-135`). | as cited |
| F14 | Size budgets:<br>• `live.py` is 235 executable statements, with room only for the registration line;<br>• `live_session_runner.py` is at 499/500 and is **not touched**;<br>• new modules must sit under every cap (file < 500, class < 100, function < 50 executable statements);<br>• the size-cap baseline is exact. | `tests/unit/governance/test_size_caps.py` |
| F15 | `read_broker_state(node, *, log, timeout_seconds)` accepts `(0, 30]` and raises `ValueError` outside it, which has no D3 marker and would exit 1 with its message withheld. It defaults to 20 s, and 4.1 left "about 10 s" of NFR5 for 4.6's connect, compare and disconnect (`deferred-work.md:3169-3176`). | `src/core/live_broker_state.py:82-84, 178-247` |

### Design decisions (disclosed here, not discovered in review)

- **D-A — The local view is the session's durable engine cache, read load-only; G3's
  "trades-derived" wording is superseded.**
  - Positions: every open `Position` in the session's namespace, net signed quantity per
    `instrument_id` (F5, F6).
  - Cash: `TotalCashValue` per three-letter currency from
    `general:accountSummary:<configured account>` (F7), normalised exactly the way the broker
    reader normalises cash: `is_currency_code`, finite, not ibapi's `UNSET_DOUBLE`,
    `Decimal(str(v))`.
  - The **whole account** is compared, not only the spec's instruments. IBKR reports per account,
    and hiding a broker row because the session does not trade it would under-report exposure
    (4.1 D-F's principle). Nautilus's startup reconciliation imports every broker position into
    the session's cache as `EXTERNAL` anyway, so an account unchanged since the session last ran
    reads clean.
  - Record the G3 correction in `deferred-work.md` for the Epic 4 retro/architect.
- **D-B — A missing or unreadable local view is a failure, never "flat" and never "clean".**
  `SessionViewUnavailableError(reason, detail)`, D3-marked `exit_outcome = ERROR` (exit 1),
  `operator_safe_message = True`, own text only. Reasons (`StrEnum`):
  - `NO_ENGINE_STATE`: the namespace has no keys at all, so the session never started or its
    Redis was flushed. Nothing is compared, and the operator is told why.
  - `UNREADABLE`: a position key that could not be rebuilt, whether through an instrument
    missing (F4's silent drop, made loud), a `RuntimeError` for a corrupt cache, or a
    deserialisation error.
  - `ADAPTER_INCOMPATIBLE`: a load method moved.

  A namespace that exists but has **no `accountSummary` key for the configured account** is
  *not* a failure. Local cash is **unknown**, and every broker currency then reports as a cash
  discrepancy "local unknown": never zero, never skipped.
- **D-C — Exact comparison, no tolerance, and price is informational.**
  - Quantities are compared with `Decimal` `==`. There is no epsilon, no `isclose`, no rounding
    and no tolerance constant anywhere in the new code, and a scan pins that.
  - A broker row the session lacks, a session position the broker lacks, a size mismatch and a
    sign flip are each one discrepancy line. Each names the instrument, the session's (expected)
    and the broker's (actual) signed quantity, and the difference.
  - An unresolved broker row (`IB-CONID-<n>`, 4.1 D-F) never matches a local id and is reported
    with its `symbol`.
  - Broker `average_price` is printed beside the broker quantity but **never compared**. IBKR's
    is commission-inclusive and Nautilus's `avg_px_open` is not (4.1 D-G, routed here).
  - Cash is compared per currency: `broker − local`. A **non-zero difference is a discrepancy**,
    and so is a currency present on one side only or unknown on the local side, per FR36's "no
    close enough". The report says what local cash *is*: the last `TotalCashValue` the session's
    engine received. So a stopped session whose account moved since (another session's fill,
    interest, a dividend) honestly reads as a cash discrepancy.
- **D-D — Outcome and exit code.**
  - Add `LiveCheckOutcome.DISCREPANCY` and `EXIT_DISCREPANCY = 5` to `src/core/exit_outcome.py`
    and to `EXIT_CODES`, and document it in README's exit table. Codes 0–4 are unchanged (AR28),
    and `classify_failure` gets no edit.
  - A completed check is a **value**, a `ReconciliationReport` whose `outcome` is `OK` or
    `DISCREPANCY`. A check that could not complete **raises** its typed failure:
    - `GateRefusedError` (3);
    - `BrokerUnreachableError` or `BrokerStateUnavailableError` (4);
    - `BrokerStateAdapterError` (1);
    - `RedisUnreachableError` (1);
    - `SessionViewUnavailableError` (1);
    - `RecordNotFoundError` (1).

    There is no third shape, the 4.1 D-D discipline. The pre-work note's "set `exit_outcome` on
    the new exception" is satisfied by the new failure class. A discrepancy is a successful
    check's *finding*, not an exception.
- **D-E — The reconcile node is structurally read-only and never holds the session's id (FR5,
  AR34).**
  - `reconcile_settings(settings)` returns `settings.model_copy(update={"ibkr_live_client_id":
    settings.ibkr_live_client_id + 1})` (F8). The caller's settings object is never mutated.
  - The node is `build_trading_node(reconcile_settings, trader_id=RECONCILE_TRADER_ID,
    cli_flags=None, loop=loop)` with **no** `bar_types`, `bar_observer`, `controller` or `cache`
    (in-memory), so nothing on it can create an order.
  - `RECONCILE_TRADER_ID = "PAPER-RECONCILE"`: it has a hyphen (F10) and keeps the `PAPER` safety
    signal.
  - The builder's own Nautilus startup reconciliation still runs on this node (F8: it cannot be
    switched off without a builder edit). It writes only to the node's in-memory cache, and
    `read_broker_state` joins its `OpenPositions` request by design (4.1 D-B). This is disclosed,
    not changed.
  - `ibkr_read_only` is never read (F9).
- **D-F — Order of work, and one budget (NFR5).**
  1. Resolve the session (DB, read-only; **before** any socket).
  2. `check_redis_reachable` (before any IB socket, so a dead Redis never costs an IB connect).
  3. `gate:static` (`preflight_gate`; a refusal raises `GateRefusedError(refusal_from_report(...))`).
  4. Build the node on `+1`, `build_clients`, run, and `await_connected`.
  5. `gate:account` (`verify_connected_account` with `gateway_reported_accounts(reconcile_settings)`).
  6. `read_broker_state`.
  7. Read the session view, **immediately after** the broker read, to minimise skew against a
     running session. The Redis read is milliseconds.
  8. Compare.
  9. `shutdown` (always, in `finally`).
  10. Print the report and exit.

  One wall-clock budget, `RECONCILE_BUDGET_SECONDS = NFR5_BUDGET_SECONDS` (30.0), measured from
  the driver's start:
  - connect deadline: `CONNECT_TIMEOUT_SECONDS = 10.0`;
  - broker-read timeout: `min(DEFAULT_BROKER_STATE_TIMEOUT_SECONDS, remaining −
    TEARDOWN_RESERVE_SECONDS)`, with `TEARDOWN_RESERVE_SECONDS = 5.0`;
  - if that is ≤ 0, raise `BrokerStateUnavailableError(TIMEOUT)` without calling the reader. It
    must never reach the reader's `ValueError` (F15).

  The budget arithmetic is one pure function, unit-tested. The report carries `elapsed_ms`
  (start → verdict). Teardown is bounded by the existing `shutdown` helpers and disclosed: on a
  healthy Gateway the whole command takes seconds, and P16 records the wall clock.
- **D-G — Records (AR41 `reconcile.*`, NFR26).**
  - A clean check emits one `reconcile.ok` (INFO) with `trigger="on_demand"`, `session_id`,
    `trader_id`, masked `account`, `position_count`, `currencies` and `elapsed_ms`.
  - A discrepancy emits one `reconcile.discrepancy` (WARNING) **per discrepant line**:
    - `trigger="on_demand"`;
    - `kind` (`position` | `cash`);
    - `instrument_id` with `local_quantity`, `broker_quantity` and `difference` (as strings);
    - or `currency` with `local_cash` (`"unknown"` when absent), `broker_cash` and `difference`.
  - `trigger` distinguishes these from Story 4.2's startup and 4.3's runtime records of the same
    AR41 names.
  - The reader adds `reconcile.session_view_read` (INFO: `trader_id`, `open_positions`,
    `currencies`, `elapsed_ms`) and `reconcile.session_view_failed` (ERROR: `reason`, `detail`,
    `error_type`).
  - Never the raw account, never a Redis key name (F7), never `str(exc)`.
- **D-H — Layering (AR38).**
  - `src/models/reconciliation.py` holds the stdlib-only value types.
  - `src/services/reconciliation_service.py` holds the pure comparison and rendering, as the
    architecture tree names it (`architecture.md:546-547, 587`). It imports **no** Nautilus and
    **no** SQLAlchemy; its inputs are `BrokerState` and `SessionView`.
  - `src/core/live_session_view.py` is the Redis read, the only new module importing Nautilus
    (`CacheDatabaseAdapter`, `MsgSpecSerializer`, `TraderId`, `UUID4`, `PositionId`). It converts
    at the boundary (`signed_decimal_qty()` → `Decimal`), so no `Position`, `Quantity` or
    `InstrumentId` crosses it.
  - `src/core/live_reconcile.py` is the driver. It imports neither SQLAlchemy **nor
    `src.services`**, because `TestImportPurity.FORBIDDEN` includes `src.services`
    (`tests/component/core/test_session_runner_phases.py:1266-1278`). The comparison reaches it as
    an **injected `compare` callable**, a port: the Story 3.5/3.6 `sink` precedent, where "the
    runner takes the *port*, not the adapter".
  - `src/cli/commands/live_reconcile.py` is the composition root. It resolves the session through
    `SessionService`, hands the driver a plain target (`name`, `session_id`, `status`), and passes
    `compare=reconciliation_service.compare`.
- **D-I — Zero writes, anywhere.** The command:
  - writes no DB row (it only calls `resolve`: no transition, no record port);
  - writes no Redis key (load methods only; an AST scan forbids the adapter's `add*`, `update*`,
    `delete*`, `flush`, `heartbeat`, `index_*` and `snapshot*` in `live_session_view.py`, with a
    non-vacuity twin; the integration test proves it with `MONITOR`);
  - submits, cancels or modifies no order (no strategy on the node; the new modules call none of
    `FORBIDDEN_ORDER_METHODS`).

  This is FR35's "never auto-resolves by trusting local state", made structural.
- **D-J — Out of scope, stated.**
  - No `--json` (AR29's `--json` is `status`/`list`'s, and the D-G records are the machine form).
  - No spec-scoped filter (D-A).
  - No price comparison (D-C).
  - No `reqAccountSummary` refresh (4.1 D-J).
  - No runner, builder, `_phase_reconcile`, DB, migration, `SessionRecordPort` or
    `EXPECTED_CAPABILITIES` change.
  - No retry on any failure (AR43's spirit).

## Acceptance Criteria

The epic text (`epics.md:1644-1676`) is in **bold**. The clarifications under each are binding.

1. **Given `ntrader live reconcile <session>`, When it is run, Then it connects read-only,
   retrieves broker positions and cash, compares them against the session's local view, prints
   an explicit result, and disconnects (FR36).**
   - The command resolves `<session>` by name or UUID. An unknown session exits 1 and opens no
     socket.
   - It runs D-F's sequence. `read_broker_state` gives the broker side (Story 4.1), and D-A's
     load-only read of the session's engine cache gives the local side.
   - "Read-only" is D-E + D-I, each pinned by a test.
   - `shutdown` runs on every path: clean, discrepancy, each failure, and an interrupt.
   - Live: Procedure P16.
2. **Given the command runs while a session process holds `ibkr_live_client_id`, When it
   connects, Then it uses `ibkr_live_client_id + 1`, so it never evicts the running session
   (FR5, AR34).**
   - The settings handed to the node factory, and to `gateway_reported_accounts`, carry
     `ibkr_live_client_id == original + 1`.
   - The **real** `build_trading_node_config` built from them gives both the IB data client and
     the exec client `client_id == original + 1` (component tier).
   - The caller's settings object is unchanged.
   - Live: P16's concurrent variant shows the running session receiving no eviction or disconnect.
3. **Given a discrepancy, When it is reported, Then the report names the specific instrument,
   the expected and actual quantities, and the cash difference — no averaging, no "close
   enough" (FR36).**
   - Each discrepant position line names the instrument, the session's (expected) and the
     broker's (actual) signed quantity, and the difference. The cases are extra at broker,
     missing at broker, size mismatch, sign flip and unresolved broker row.
   - Each discrepant cash line names the currency, local, broker and `broker − local`, or
     "local unknown".
   - `Decimal` exactness is proven by a `0.0001`-share and a `0.01`-cash difference, each reported.
   - A scan finds no tolerance or rounding construct in the new modules.
4. **Given any discrepancy, When the command completes, Then it reports and exits — it never
   auto-resolves by trusting local state (FR35).**
   - Exit `5` (`EXIT_DISCREPANCY`), after the report is printed.
   - D-I is proven:
     - repository spy: only reads;
     - integration `MONITOR`: no Redis write verb, and the namespace is byte-identical;
     - AST: no order method and no adapter mutator in the new modules;
     - no strategy or controller on the node config.
5. **Given matching state, When the command completes, Then it reports a clean result explicitly
   and exits `0`.**
   - The clean line says, in words, that positions and cash match IBKR exactly. It lists the
     matched instruments and currencies, and a flat-and-flat account reads clean with
     `positions=0`.
   - Only `OK` maps to 0 (`test_only_ok_maps_to_zero` stays green with `DISCREPANCY` added).
6. **Given the check, When it is timed, Then it completes within 30 seconds (NFR5).**
   - D-F's budget constants are pinned: `RECONCILE_BUDGET_SECONDS == 30.0`, `CONNECT_TIMEOUT
     + TEARDOWN_RESERVE < 30`, and the broker-read timeout is clamped.
   - With small injected budgets, a never-connecting node and a never-answering broker each end
     within budget + 0.5 s.
   - A budget already spent never calls the reader (no `ValueError`).
   - `elapsed_ms` is in the report and on `reconcile.ok` / `reconcile.discrepancy`.
   - Live: P16 records the wall clock and `elapsed_ms`.

## Tasks / Subtasks

- [x] **Task 0 — Standing checks (record results in the Debug Log)**
  - [x] 0.1 Head, clean tree. `uv run ruff check .` and `make typecheck` clean before the first
    edit.
  - [x] 0.2 Baselines: `make test-unit`, then `make test-component`, run **sequentially** (4.1
    measured CPU-starved flakes when they ran concurrently). Record passed/failed/skipped.
  - [x] 0.3 Redis reachable? (`check_redis_reachable`; at drafting it was, `127.0.0.1:6379`.)
    Gateway reachable, and settings present? Record how each was determined. Never read `.env`.

- [x] **Task 1 — Probes (fresh interpreter, `/tmp/p46/`, not committed), BEFORE production code**
  - [x] 1.1 Re-run `probe_view.py` (scratch `PAPER-<uuid8>` namespace, `MONITOR` capture,
    self-cleaning). Confirm there are no write verbs, the namespace is byte-identical, and the
    read takes ms.
  - [x] 1.2 Re-run `probe_logging.py`: `is_logging_initialized()` stays False across construct,
    load and close.
  - [x] 1.3 Extend the probe with a NETTING **close-then-reopen** and a **flip** on one position
    id, written through a real `Cache` + `ExecutionEngine` (the
    `test_live_order_survives_restart.py` shape). Confirm the replayed `signed_decimal_qty()`
    equals the live object's (F5).
  - [x] 1.4 Confirm F4: delete a scratch `instruments:<id>` key and observe `load_positions()`
    silently drop the position. That is the behaviour the reader must make loud.

- [x] **Task 2 — Exit outcome (AC #4, #5) — TDD, unit tier**
  - [x] 2.1 Red in `tests/unit/core/test_live_check.py`:
    - `EXIT_DISCREPANCY == 5`;
    - `EXIT_CODES[DISCREPANCY] == 5`;
    - 5 is distinct from every other code;
    - the existing total-table and only-OK-is-zero tests stay green.
  - [x] 2.2 Green: `src/core/exit_outcome.py` only. `classify_failure` is unedited.

- [x] **Task 3 — Value types + comparison service (AC #3, #5) — TDD, unit tier**
  - [x] 3.1 Red in `tests/unit/models/test_reconciliation.py`. The types are frozen, with `Decimal`
    and tuples:
    - `ViewPosition(instrument_id, quantity)`, where quantity is non-zero;
    - `SessionView(trader_id, positions, cash, cash_known)`: one row per instrument and per
      currency; `cash` reuses `CashBalance`;
    - `PositionLine`, `CashLine`, `ReconciliationReport`.

    Purity uses both the AST `sys.stdlib_module_names` scan and the subprocess check, with
    non-vacuity twins.
  - [x] 3.2 Red in `tests/unit/services/test_reconciliation_service.py`, covering
    `compare(broker, local, *, session_name, trader_id, elapsed_ms) -> ReconciliationReport`:
    - match;
    - flat and flat;
    - extra at broker;
    - missing at broker;
    - size mismatch;
    - sign flip;
    - unresolved broker row;
    - several local positions summed per instrument (the NETTING + `EXTERNAL` case);
    - cash equal;
    - cash differing both ways;
    - `0.01` cash exactness;
    - `0.0001` quantity exactness;
    - local cash unknown;
    - a currency on one side only;
    - output sorted;
    - `outcome` is `OK` only when every line matches.

    `render(report) -> list[str]` covers:
    - the clean wording;
    - each discrepancy line naming instrument/expected/actual/difference, or
      currency/local/broker/difference;
    - broker `avg_price` shown but not compared;
    - the account masked;
    - no raw account in any line.

    Add a scan: no `isclose`, `round(`, `quantize(` or tolerance-named constant in the service
    and model, with a non-vacuity twin.
  - [x] 3.3 Green: `src/models/reconciliation.py` (stdlib) and
    `src/services/reconciliation_service.py` (no Nautilus, no SQLAlchemy; pinned by an import
    scan).

- [x] **Task 4 — Session-view reader (AC #1, #4) — TDD, component tier with a fake adapter**
  - [x] 4.1 Red in `tests/component/core/test_live_session_view.py`:
    - `read_session_view(session_id, account, redis_settings, *, adapter_factory=...,
      reachability=...)`;
    - the fake adapter serves real Nautilus `Position` objects, built from `TestEventStubs` fills
      (no C logging; carry the `_assert_c_logging_state_is_unchanged` autouse fixture).

    Cases:
    - open positions summed per instrument;
    - closed positions excluded;
    - flat namespace with state → `positions=()`;
    - empty namespace → `NO_ENGINE_STATE`;
    - an unbuildable position key → `UNREADABLE` (never dropped);
    - a corrupt-cache `RuntimeError` → `UNREADABLE`;
    - a missing load method → `ADAPTER_INCOMPATIBLE`;
    - `accountSummary` for the configured account → cash;
    - another account's key ignored;
    - `""` and `BASE` skipped;
    - NaN, inf, `UNSET_DOUBLE` and string values skipped;
    - no key → `cash_known=False`;
    - Redis unreachable → `RedisUnreachableError` **before** the adapter is constructed;
    - the adapter is `close()`d on every path;
    - the trader id is `derive_trader_id(session_id)`;
    - no raw account or key name in any record or exception.

    An AST scan forbids adapter mutators (D-I), with a non-vacuity twin.
  - [x] 4.2 Green: `src/core/live_session_view.py`.
    - Enumerate `adapter.keys("positions:*")` (or the equivalent measured in Task 1) and call
      `load_position` per id, failing loudly (F4).
    - The serializer is F2's.
    - The exception carries the D3 markers (D-B).
    - `test_exit_outcome_markers.py` must pass without growing `UNMARKED`.

- [x] **Task 5 — Real-Redis proofs (AC #1, #4) — integration tier, `--forked`, skipped when
  Redis is down** (`tests/integration/core/test_live_session_view_redis.py`)
  - [x] 5.1 Write a scratch `PAPER-<uuid8>` namespace through a real `Cache` +
    `CacheDatabaseAdapter`, covering an open position, a closed one, a close-then-reopen and an
    `accountSummary`. Then prove `read_session_view` returns the right net quantities and cash.
  - [x] 5.2 Prove D-I: a stdlib RESP `MONITOR` capture during `read_session_view` holds no write
    verb, and the namespace is byte-identical before and after. The tiny RESP helper lives in the
    test file; it is not a new dependency.
  - [x] 5.3 Clean up only the scratch namespace's own keys; **never** `flush()` (`FLUSHDB`, F3's
    warning in `test_live_cache_namespace.py`).

- [x] **Task 6 — Driver (AC #1, #2, #4, #6) — TDD, component tier**
  (`tests/component/core/test_live_reconcile.py`)
  - [x] 6.1 Red. Seams: `compare` (required, the port, D-H), `node_factory`, `account_verifier`,
    `broker_reader`, `view_reader`, `reachability`, and an injectable budget.
    - The node double needs `kernel.data_engine` / `exec_engine.check_connected()`, `build`,
      `run_async`, `stop`, `dispose` and `is_running`. Reuse or extend `TestLiveNode` from
      `tests/component/doubles/`.

    Cases:
    - D-F's order, recorded by the seams;
    - a gate refusal opens no socket (factory never called) → `GateRefusedError`;
    - Redis unreachable → no node built;
    - the settings reaching the factory and the verifier are `+1`, and the original is unchanged;
    - node factory kwargs have no `bar_types`, `bar_observer`, `controller` or `cache`,
      `cli_flags is None`, and `trader_id == "PAPER-RECONCILE"`;
    - clean → `OK`;
    - discrepancy → `DISCREPANCY`;
    - each failure re-raised with its class;
    - `shutdown` called exactly once on every path, including `KeyboardInterrupt`;
    - the budget clamp, including "spent → no reader call";
    - timing bounds with small budgets;
    - `reconcile.ok` / `reconcile.discrepancy` records per D-G, masked;
    - the loop is restored.
  - [x] 6.2 A component test builds the **real** `build_trading_node_config(reconcile_settings(s),
    trader_id=RECONCILE_TRADER_ID)` and asserts both IB client configs' `client_id == live + 1`,
    with no strategies or actors (AC #2).
  - [x] 6.3 Green: `src/core/live_reconcile.py`. `reconcile_settings`, `reconcile_budget` and
    `run_reconcile` each stay under 50 statements.

- [x] **Task 7 — CLI (AC #1, #4, #5) — TDD, unit tier**
  (`tests/unit/cli/commands/test_live_reconcile_cli.py`)
  - [x] 7.1 Red:
    - `ntrader live reconcile <name>` resolves via `SessionService` (patch `get_sync_session` +
      the repository, the `test_live_status_cli.py` shape);
    - it prints the report with `markup=False`;
    - it exits 0, 5, or the classified code (3, 4 and 1 cases);
    - an unknown session exits 1 before the driver is called;
    - the repository spy sees reads only;
    - there is no `--real-money` option;
    - extend `TestNoRealMoneySurface`'s AST scan to `live_reconcile.py`;
    - `cli_flags` is never passed.
  - [x] 7.2 Green: `src/cli/commands/live_reconcile.py` (`@click.command("reconcile")`), plus
    exactly one `live.add_command(reconcile)` line in `live.py`.

- [x] **Task 8 — Guard lists and governance (same change as the modules that trigger them)**
  - [x] 8.1 `NODE_FACING_MODULES`: add `src/core/live_reconcile.py` (it drives a node) and
    `src/core/live_session_view.py` (reached from the driver), each with a reason comment.
    `EXEMPT_MODULES`: add `src/cli/commands/live_reconcile.py` (the CLI boundary raises
    `SystemExit`).
  - [x] 8.2 `TestImportPurity.MODULES` (`tests/component/core/test_session_runner_phases.py`):
    add both new core modules. They satisfy `FORBIDDEN` (no SQLAlchemy / `src.db` /
    `src.services`) because the comparison is injected (D-H). Give each a reason comment.
  - [x] 8.3 `STOP_PATH_MODULES`: **not** added. It is an on-demand command with no session stop
    path. Leave a comment, following the `live_broker_state` precedent.
  - [x] 8.4 `_STDLIB_AND_FIRST_PARTY`: prove it by **running**
    `tests/integration/core/test_epic1_ac_node.py`. Add any new stdlib import (e.g. `json`) by
    hand with a reason. No `from __future__ import annotations`.
  - [x] 8.5 Run `LIVE_MODULE_GLOBS` consumers `test_live_dependency_invariance.py`,
    `test_order_path_has_no_retry.py`, `test_live_stop_path_is_inert.py` and the
    `ibkr_read_only` scan.
  - [x] 8.6 Size caps: measure every new module with `measure_module` and record the numbers. No
    baseline entry is expected.

- [x] **Task 9 — Docs**
  - [x] 9.1 README:
    - add `live reconcile <name-or-id>` to the Live Trading Commands table;
    - add `5` = "the check completed and found a discrepancy" to the exit-code note;
    - add a short note on what "local view" means (D-A), the `+1` client id, and the Redis
      requirement.

    Validate before modifying (CLAUDE.md).
  - [x] 9.2 `docs/agent/nautilus.md`: a short "Reading a session's engine cache" subsection
    covering F3/F4/F5/F7 and D-I's load-only rule.

- [x] **Task 10 — Live verification (informational only, never a gate)**
  - [x] 10.1 Write **Procedure P16** in `docs/qa/phase3-live-verification.md`, after P15, with the
    parallel-story numbering note.
    - It runs `ntrader live reconcile <session>` against a session that has run at least once.
    - **Variant A:** the session is stopped (a pure read).
    - **Variant B, operator-only:** alongside a running session, to prove there is no eviction
      (grep the session's log for `326`/`1100`/`connection.lost` around the reconcile's
      timestamp). The session itself may trade; this is the P10–P13 precedent, and the reconcile
      never does.
    - Pass criteria:
      1. the result line and exit code;
      2. the position lines match TWS;
      3. cash reads explained;
      4. wall clock and `elapsed_ms` under 30 s;
      5. the account masked in every command-printed line;
      6. `order.submitted` = 0 in the reconcile's output;
      7. `client_id=<live+1>` in the build line.
  - [x] 10.2 If a Gateway is reachable **and** the checkout has the settings, run Variant A and
    record the row. Otherwise record **defined, not run** with the reason. Never `--real-money`,
    never `.env`, docker, or the kill/reconnect/connection-loss scripts. Never submit, open, close
    or flatten anything.

- [x] **Task 11 — Routed debt (`deferred-work.md`, new "Deferred from: story-4.6" section)**
  - [x] 11.1 **→ Architecture / Epic 4 retro:** G3's "trades-derived local view" cannot
    represent an open position (F1), so 4.6 uses the engine cache (D-A). The architecture text
    should be amended.
  - [x] 11.2 **→ Story 4.2 (integrator):** `reconciliation_service.compare` takes two domain
    values. 4.2's startup 0-discrepancy check can reuse it with a `SessionView` built from the
    live node's cache instead of Redis, rather than growing a second comparison.
  - [x] 11.3 **Stale docstring:** `trade_counts_by_session`'s "every session honestly reports zero
    today" (F1). Record it; do not fix it here (zero-diff on `src/db`).
  - [x] 11.4 **→ Story 4.7:** local cash carries no timestamp (the `accountSummary` key has none),
    so a cash discrepancy cannot say *since when*. The last `AccountState` event's `ts_event`
    would give it.

- [x] **Task 12 — Mutation sweep (each applied, target tests run, file restored; record kill/survive)**
  - M1 compare with a tolerance (`abs(diff) < 1`);
  - M2 drop the unresolved broker row;
  - M3 read local cash as zero when unknown;
  - M4 skip closed-position filtering;
  - M5 first local position wins instead of the sum;
  - M6 silently drop an unbuildable position (use `load_positions()`);
  - M7 client id not `+1`;
  - M8 pass `cache=build_cache_config(...)` to the node;
  - M9 skip `shutdown` on the discrepancy path;
  - M10 exit 0 on discrepancy;
  - M11 log the raw account or key name;
  - M12 call the reader with an unclamped timeout;
  - M13 the Redis preflight after the IB connect;
  - M14 `SessionViewUnavailableError` marked `BROKER_UNREACHABLE`.

- [x] **Task 13 — Gates**
  - [x] 13.1 `uv run ruff format .`, `uv run ruff check .`, `make typecheck`.
  - [x] 13.2 `make test-unit`, then `make test-component` (sequential). Integration `--forked` for
    `tests/integration/core/`.
  - [x] 13.3 `grep -rn "is_live" src/core/strategies/` gives 0. `git diff --stat` shows the D-J
    zero-diff set untouched: `live_session_runner.py`, `live_node_builder.py`, `src/db`,
    `alembic`, `src/api`, `templates`, `live_broker_state.py`.

### Review Findings

Code review, 2026-09-27. Three parallel layers ran:
- Blind Hunter (diff only);
- Edge Case Hunter (diff plus repo and wheel);
- Acceptance Auditor (diff plus spec, `prd-epic4-scope.md` and `CLAUDE.md`).

Together with the reviewer's own read they produced 40 raw findings (18, 9, 11 and 2). After
merging duplicates: 0 decisions, 25 patches, 4 deferred, 4 dismissed.

- [x] [Review][Patch] **HIGH:** the session's cash key is looked up under the raw `TWS_ACCOUNT`,
  but the session wrote it under the builder's normalised account (`strip().upper()`,
  `live_node_builder._resolve_account`). A lowercase or padded account therefore reads cash as
  "unknown" and exits 5 on every run [src/core/live_reconcile.py:241; src/core/live_session_view.py:209]
- [x] [Review][Patch] Several failures escape `read_session_view` untyped and unlogged, possibly
  carrying adapter text:
  - `adapter_factory(...)` sits outside the `try`;
  - `PositionId(...)` is built before `_call`;
  - `.get` runs on `load()`'s result;
  - `position.is_open` / `instrument_id` / `signed_decimal_qty()` are read outside `_call`.

  [src/core/live_session_view.py:178, 209, 245-256]
- [x] [Review][Patch] Redis `SCAN` may return a key twice, so a duplicated position key is netted
  twice (Nautilus's own `load_positions` dedupes by id) [src/core/live_session_view.py:207-256]
- [x] [Review][Patch] Session positions held for a different account (`TWS_ACCOUNT` changed since
  the session ran) are compared against the configured account's broker rows. Filter them,
  symmetric with the broker reader's account filter (4.1 F10), and warn visibly
  [src/core/live_session_view.py:255]
- [x] [Review][Patch] NFR5 is not bounded end to end:
  - the view read and teardown run outside the budget;
  - the connect deadline starts after `node_factory`;
  - an over-budget run is invisible (`elapsed_ms` is taken before teardown);
  - README's "completes within 30 s" is unconditional.

  Make an overrun visible (a total including teardown, and a warning record), start the connect
  deadline within the budget, and word README honestly [src/core/live_reconcile.py:201-249; README.md]
- [x] [Review][Patch] The account gate's *returned*-refusal branch
  (`if not decision.permitted: raise …`) has no test [src/core/live_reconcile.py:270;
  tests/component/core/test_live_reconcile.py]
- [x] [Review][Patch] `test_a_silent_broker_ends_within_the_whole_budget` proves only that the
  double honours its own timeout. Pin what the driver owns: the timeout it passes
  [tests/component/core/test_live_reconcile.py]
- [x] [Review][Patch] The Redis preflight runs before `gate:static`, so a non-paper config with
  Redis down exits 1 instead of 3 (FR11). Run Layer 1 first, before any socket at all
  [src/core/live_reconcile.py:202-203]
- [x] [Review][Patch] A never-run session costs a full IB connection (or exits 4 with the Gateway
  down) before it reports `no_engine_state`. Check that the namespace exists before the IB connect;
  the full read stays after the broker read [src/core/live_reconcile.py:241;
  src/core/live_session_view.py:201]
- [x] [Review][Patch] `LOAD_METHODS` is documented as "the only" adapter members, but `close()` is
  called too and the tuple is not pinned. The AST mutator scan cannot see names dispatched through
  `_member` [src/core/live_session_view.py:84, 200, 287]
- [x] [Review][Patch] The `MONITOR` proof checks only commands naming the namespace, so a keyless
  write verb (`FLUSHALL`) or a write outside the namespace is never checked, and hash/zset contents
  are not snapshotted [tests/integration/core/test_live_session_view_redis.py]
- [x] [Review][Patch] The integration setup relies on a fixed `sleep(0.3)` for pipelined writes; it
  should poll, bounded [tests/integration/core/test_live_session_view_redis.py]
- [x] [Review][Patch] The UUID-resolution CLI test cannot fail, because `find_by_name` returns the
  row for any identifier [tests/unit/cli/commands/test_live_reconcile_cli.py]
- [x] [Review][Patch] `deferred-work.md` says `test_live_dependency_invariance.py` was "updated"; it
  was not edited, it passes because `msgspec` is declared
  [_bmad-output/implementation-artifacts/deferred-work.md]
- [x] [Review][Patch] The `STOP_PATH_MODULES` comment "calls no order or position method" is
  literally false (`load_position`, `position.is_open`). Nothing scans the new modules for
  `FORBIDDEN_ORDER_METHODS`, although AC #4 / D-I claims it
  [tests/unit/core/test_live_stop_path_is_inert.py]
- [x] [Review][Patch] Teardown on the **discrepancy** path is untested, and "exactly once" is
  unverified. The sweep ran M9 as "no teardown on success" instead of the spec's "skip shutdown on
  the discrepancy path" [tests/component/core/test_live_reconcile.py; Dev Agent Record]
- [x] [Review][Patch] The tolerance/rounding scan covers 2 of the 5 new modules, `round(` appears in
  both `_elapsed_ms` helpers, and no view-reader test sums a fractional quantity
  [tests/unit/services/test_reconciliation_service.py; src/core/live_session_view.py;
  src/core/live_reconcile.py]
- [x] [Review][Patch] API shapes that differ from the spec are undisclosed:
  - `compare` takes `trader_id` from the view;
  - `render_report`;
  - `outcome_for` rather than `report.outcome`;
  - `ReconcileBudget.read_timeout`;
  - `SessionView.cash_known` is derived.

  [Dev Agent Record]
- [x] [Review][Patch] `reconcile.session_view_read` drops D-G's `currencies`, and the disclosure
  reads it as an addition [src/core/live_session_view.py:186-195]
- [x] [Review][Patch] "Before any socket" in the P16 row, the Debug Log and the P16 prose should
  read "before any IB socket", since the DB resolve and the Redis PING ran first
  [docs/qa/phase3-live-verification.md; story Debug Log]
- [x] [Review][Patch] The new real-money AST scan has no non-vacuity twin
  [tests/unit/cli/commands/test_live_reconcile_cli.py]
- [x] [Review][Patch] `test_no_new_dependency_was_added_for_the_live_path`'s docstring still says
  "pyproject.toml and uv.lock are unchanged" [tests/integration/core/test_epic1_ac_node.py]
- [x] [Review][Patch] `ReconcileBudget` accepts NaN or non-positive fields. A NaN `broker_read`
  reaches the reader's unmarked `ValueError`, and a NaN `total` silently disables the budget
  [src/core/live_reconcile.py:116-127]
- [x] [Review][Patch] Nothing pins the `accountSummary:<account>` key the reader depends on against
  the real adapter (the 4.1 canary precedent) [tests/component/core/test_live_session_view.py]
- [x] [Review][Patch] A running session's cash can lag the broker by IBKR's account-summary push
  interval (about 3 min), so "re-run to confirm" misleads right after a fill. Say so
  [src/cli/commands/live_reconcile.py:75-79]
- [x] [Review][Defer] Partial Redis eviction that keeps other keys but loses `positions:*` would read
  as a valid flat view; only a wholly empty namespace is caught [src/core/live_session_view.py:201]
  — deferred: needs an index cross-check; owner Story 4.2 (Redis-disposability enforcement)
- [x] [Review][Defer] Two concurrent `live reconcile` runs collide on the one `+ 1` client id; the
  second reads as broker-unreachable (exit 4) [src/core/live_reconcile.py:139-147] — deferred:
  AR34 reserves exactly one id by design; documented as one run at a time
- [x] [Review][Defer] Ctrl-C after the node is built never maps to INTERRUPTED, because the kernel
  installs loop signal handlers [src/core/live_reconcile.py:229-241] — deferred, pre-existing:
  `live check` behaves the same
- [x] [Review][Defer] If `IBKR_LIVE_CLIENT_ID` changes while a session runs, the new `+ 1` can equal
  the running session's id; the session does not record the id it used
  [src/core/live_reconcile.py:139-147] — deferred, pre-existing: the session record has no client id

## Dev Notes

### The current surface — exact extension points

- Node lifecycle helpers: `src/core/live_check_node.py` (whole file; F10). The driver shape to
  copy is `live_check_driver._drive` (`:195-229`), minus the observer.
- Gate: `preflight_gate` (`src/core/live_check.py:181-256`), `refusal_from_report`
  (`src/core/live_session_node.py:99-122`), `verify_connected_account` /
  `gateway_reported_accounts` (`src/core/live_account_gate.py:59-175`).
- Broker read: `read_broker_state`, `DEFAULT_BROKER_STATE_TIMEOUT_SECONDS` and
  `NFR5_BUDGET_SECONDS` (`src/core/live_broker_state.py:80-84, 178`). Types:
  `src/models/broker_state.py` (`CashBalance` and `is_currency_code` are reusable).
- Redis: `RedisSettings` (`src/config.py:259-344`, exposed as `get_settings().redis`),
  `build_cache_config` and `check_redis_reachable` (`src/core/live_cache.py`), and
  `derive_trader_id` (`src/core/live_trader_id.py:58-87`).
- Session resolution: `SessionService.resolve` (`src/services/session_service.py:646-672`), with
  the `live_status._load_report` pattern.
- Exit mapping: `classify_failure` / `failure_message` (`src/core/live_check.py:259-322`) and
  `_exit_on_failure` (`src/cli/commands/live_status.py:119-135`).
- Masking: `mask_account` (`src/core/live_gate.py:117`, whole-value).

### Scope boundaries — do NOT build

- No change to `live_session_runner.py` (499/500), `live_node_builder.py` (the ratchet),
  `_phase_reconcile` (4.2's), `live_broker_state.py` (4.1's, done), `src/db/**`, `alembic/**`,
  `src/api/**` or `templates/**`.
- No auto-resolution, no write, no retry, no `--json`, no price comparison, no spec scoping (D-J).
- No new dependency. There is no Python Redis client (AR3); Nautilus's adapter is the reader. The
  test RESP helper is test code.

### Hazards (each has bitten a previous story, or will)

- **A dropped row reads as flat.** `load_positions()` hides an unbuildable position (F4). Test the
  loud path first (M6).
- **The Redis key carries the raw account** (F7). The account-masking scan covers every record
  **and** every exception message.
- **An unreachable Redis hangs forever** (F3). The preflight runs before the adapter, and a test
  proves the adapter factory is never called when it fails.
- **`FLUSHDB`.** Never call `adapter.flush()` in a test; it clears the whole Redis database, not a
  namespace.
- **C logging in the component tier.** Construct no `TradingNode`, `LiveExecutionEngine`, `Logger`
  or real `CacheDatabaseAdapter` there. Real adapters go in the integration tier, `--forked`.
- **The timeout `ValueError`** (F15): clamp, and never pass ≤ 0.
- **Guard-list rot:** the hand lists are edited in the creating change (CLAUDE.md Anti-Patterns).
- **Parallel Epic 4 stories:**
  - 4.2 may add `reconcile.ok` / `reconcile.discrepancy` constants of its own; the `trigger`
    field keeps the records distinguishable.
  - 4.2, 4.3 and 4.7 may also edit `deferred-work.md`, `phase3-live-verification.md`, the guard
    lists and `exit_outcome.py`. Append rather than reflow.
- **A test that cannot fail:**
  - every AST scan gets a non-vacuity twin;
  - the "clean" test must assert both `outcome is OK` **and** that a one-share difference is
    `DISCREPANCY`.

### Testing standards summary

- TDD, red first and recorded in the Debug Log, per test file.
- **Unit:** the models, the service, the CLI and the exit table.
- **Component:** the view reader with a fake adapter and real `Position` objects, the driver with
  node doubles, and the real `build_trading_node_config` client-id pin.
- **Integration (`--forked`):** the real-Redis read, and the `MONITOR` no-write proof.
- No test touches a broker (NFR32). Live evidence is P16 (NFR33), informational.

### Project Structure Notes

- New:
  - `src/models/reconciliation.py`
  - `src/services/reconciliation_service.py`
  - `src/core/live_session_view.py`
  - `src/core/live_reconcile.py`
  - `src/cli/commands/live_reconcile.py`
  - tests: `tests/unit/models/test_reconciliation.py`, `tests/unit/services/test_reconciliation_service.py`,
    `tests/component/core/test_live_session_view.py`, `tests/component/core/test_live_reconcile.py`,
    `tests/integration/core/test_live_session_view_redis.py`,
    `tests/unit/cli/commands/test_live_reconcile_cli.py`
- Modified:
  - `src/core/exit_outcome.py`
  - `src/cli/commands/live.py` (one line)
  - `README.md`
  - `docs/agent/nautilus.md`
  - `docs/qa/phase3-live-verification.md`
  - `deferred-work.md`
  - guard-list test files
  - `tests/unit/core/test_live_check.py`
  - `tests/unit/cli/commands/test_live_cli.py`
  - `sprint-status.yaml`: the `4-6-…` key only
- This follows the architecture's `services/reconciliation_service.py` placement, with one
  variance: the Redis read sits on the core side (it imports Nautilus; AR38).

### References

- Epic/story: `_bmad-output/planning-artifacts/epics.md:1644-1676`; scope extract
  `prd-epic4-scope.md` (FR5, FR35, FR36, NFR5, NFR20, NFR26, NFR32/33, AR34, AR38, AR41, AR43).
- Architecture: `architecture.md:546-547` (the service), `:587`, `:595-598` (the D2 data
  boundaries) and `:696-700` (G3, superseded on "trades-derived").
- Pre-work: `epic4-prework-spec.md:331-336` (the DISCREPANCY outcome).
- Sibling: `4-1-read-the-brokers-authoritative-view-of-positions-and-cash.md` (the reader, D-A..D-K,
  and the guard-list and probe conventions).
- Debt routed here: `deferred-work.md:3169-3176`.

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), in the Epic 4 harness worktree
`harness/s4-6-20260927-110812-2`.

### Debug Log References

- **Task 0.**
  - Head `32f852f`. The tree was clean apart from this story file and the `4-6` key.
  - `uv run ruff check .` and `make typecheck` were clean before the first edit.
  - Baselines, run sequentially: unit **2851 passed**; component **1629 passed / 16 skipped**.
  - Redis `127.0.0.1:6379` was reachable (`check_redis_reachable`).
  - Gateway: at 11:22 ET, Sunday 2026-09-27, a Python `socket.connect` to 4001, 4002, 7496 and
    7497 was refused on all four. No Gateway was running, so P16 cannot run in this session.
- **Task 1 probes** (`/tmp/p46/`, not committed; each ran on a scratch `PAPER-<uuid8>`
  namespace and deleted its own keys):
  - `probe_view.py`: the reading adapter issued only `CLIENT SETINFO`, `INFO`, `SCAN`, `LRANGE`,
    `GET` and `MGET` (**no write verb**). The namespace was byte-identical and the read took
    2.2–4.5 ms.
  - `probe_logging.py`: `is_logging_initialized()` stayed False across construct, load and close.
  - `probe_replay.py`, through a real `Cache` + `ExecutionEngine` (NETTING, because no client is
    registered): BUY 7 → SELL 7 → BUY 3 → SELL 5.
    - The live cache read `7 / 0 (closed) / 3 / -2`.
    - A fresh adapter replayed `AAPL.NASDAQ-S-001` to **-2** (`Decimal`), which matches F5.
    - `adapter.keys('positions*')` returns **full** keys (`trader-PAPER-x:positions:<id>`).
    - With `instruments:AAPL.NASDAQ` deleted, `load_positions()` returned `{}` and
      `load_position` returned `None`. That is F4's silent drop, confirmed.
  - `probe_load_all.py`, measured at Task 8 as a msgspec-free alternative: an adapter built with a
    base `Serializer()` constructs, but `load_all()` (Rust-side decode) returns
    **`positions: {}`** for a namespace holding a Python-written position. That is a silent flat,
    so the path is not usable.
- **Red first, per file** (each observed failing before its module existed):
  - `tests/unit/core/test_live_check.py::test_a_reconcile_discrepancy_exits_five`: `ImportError`
    on `EXIT_DISCREPANCY`.
  - `tests/unit/models/test_reconciliation.py` and
    `tests/unit/services/test_reconciliation_service.py`: collection error, module not found.
  - `tests/component/core/test_live_session_view.py`: collection error.
  - `tests/component/core/test_live_reconcile.py`: collection error.
  - `tests/unit/cli/commands/test_live_reconcile_cli.py`: collection error.
  - `tests/integration/core/test_live_session_view_redis.py` was written after the reader, as a
    real-Redis proof. It is **not** red-first.
- **Green:**
  - models + service: 70 passed;
  - session-view reader: 33 passed;
  - real-Redis integration (`--forked`): 5 passed, including the `MONITOR` no-write proof;
  - driver: 26 passed;
  - CLI: 16 passed.
- **Task 8 BLOCKER (dev-story HALT: "additional dependencies need user approval").**
  - `tests/component/core/test_live_dependency_invariance.py::test_every_third_party_import_is_already_declared`
    fails with `{'live_session_view.py': {'msgspec'}}`.
  - `CacheDatabaseAdapter` requires a `MsgSpecSerializer(encoding=msgspec.msgpack, …)` to decode
    the session's positions.
  - `msgspec 0.19.0` is installed as a **hard** requirement of `nautilus-trader`
    (`msgspec>=0.19.0,<1.0.0`), but it is not declared in `pyproject.toml`.
  - AR3's guard allows only declared distributions plus their declared extras' requirements.
  - **Resolved by the PO, option 1 (2026-09-27): declare it.** `uv add "msgspec>=0.19.0,<1.0.0"`
    reported "Audited 68 packages", so nothing was installed. `pyproject.toml` +1 line; `uv.lock` +2
    metadata lines (the existing package entry, now listed as a direct dependency).
  - `test_live_dependency_invariance.py` now passes honestly, 7/7.
  - `test_epic1_ac_node.py`'s `PERMITTED_THIRD_PARTY` and its declared-distributions loop gained
    `msgspec`, with the reason. Its `_STDLIB_AND_FIRST_PARTY` needed **no** edit, because `json`,
    `collections` and `uuid` were already listed. That was proven by running it: 8/8.
- **Task 10, the P16 attempt.** `live reconcile p13-0922` ran the real CLI and printed:
  - `reconcile: session p13-0922 (status=stopped) against IBKR on client_id=11`;
  - `gate.static … status=ok`;
  - `live reconcile failed: Cannot build an IBKR execution client: TWS_ACCOUNT is not set …`,
    exit `1`, before any **IBKR** socket.

  *Corrected at code review:* the run had already read the session row from PostgreSQL and PINGed
  Redis, so "before any socket" was false. The post-review re-runs are in "Code review fixes"
  below.

  **Session side, read for real** (`/tmp/p46/probe_real_views.py`, load-only):
  - `p7-fill-0901` holds AAPL.NASDAQ +4 and NVDA.NASDAQ +22 (18 keys, with one `accountSummary`
    key);
  - `p12-0921`, `p13-0922` and `rth-day-1` have empty namespaces, and each reads
    `no_engine_state`.
- **Task 12 mutation sweep** (`/tmp/p46/mutate.py`, each mutation applied, the five new unit and
  component suites run, and the file restored): **18/19 killed**.
  - M1 position tolerance, M15 cash tolerance.
  - M2 drop an unresolved broker row.
  - M3a/M3b unknown local cash read as zero (service and view).
  - M5 first position wins over the sum.
  - M6 an unbuildable position dropped.
  - M7 client id not `+ 1`.
  - M8 a Redis cache on the node.
  - M9 no teardown on success.
  - M10 discrepancy exits 0.
  - M11 raw account in the read record.
  - M12 unclamped reader timeout.
  - M13 no Redis preflight before the IB connect.
  - M14 view failure marked exit 4.
  - M16 no `reconcile.discrepancy` record.
  - M17 an empty namespace read as flat.
  - M18 the wrong namespace.

  **M4 (count closed positions) SURVIVED, and it is an equivalent mutant.** A closed Nautilus
  `Position` is FLAT, quantity 0 by definition, so it adds nothing to a sum, and the zero-net filter
  drops the instrument anyway. The `is_open` check states the intent and cannot change the result.
  It was kept, and no unkillable test was invented.
- **Sizes** (executable statements, the guard's own `measure_module`):

  | Module | Statements | Largest function or class |
  |---|---|---|
  | `live_reconcile.py` | 166 | `_check_on_node` 26 |
  | `live_session_view.py` | 156 | `_net_positions` 24 |
  | `reconciliation_service.py` | 98 | `render_report` 26 |
  | `models/reconciliation.py` | 102 | largest class 21 |
  | CLI `live_reconcile.py` | 44 | `reconcile` 19 |
  | `live.py` | 237 | — |

  All are under every cap, and there is no baseline entry.

### Completion Notes List

- **What shipped (D-A..D-J):**
  - `ntrader live reconcile <name-or-id>` (`src/cli/commands/live_reconcile.py`, registered with one
    import line and one `add_command` line in `live.py`).
    - It resolves the session read-only.
    - It runs `src/core/live_reconcile.run_reconcile`:
      1. Redis preflight;
      2. `gate:static`;
      3. a node on `ibkr_live_client_id + 1` (`PAPER-RECONCILE`, no strategy, controller or
         observer, in-memory cache);
      4. connect;
      5. `gate:account`;
      6. `read_broker_state`;
      7. `read_session_view`, immediately after the broker read;
      8. compare (injected port);
      9. teardown in `finally`.
    - It prints `reconciliation_service.render_report` and exits 0 (clean) or 5
      (`LiveCheckOutcome.DISCREPANCY`, new), or through `classify_failure` on a failure.
  - `src/core/live_session_view.py` reads the session's Redis engine cache load-only: `keys` /
    `load_position` / `load`, each position key rebuilt or failing loudly, open positions netted
    per instrument, cash from the configured account's `accountSummary`. It returns
    `SessionView`, or raises `SessionViewUnavailableError` (exit 1).
  - `src/models/reconciliation.py` is stdlib plus `broker_state`. `src/services/reconciliation_service.py`
    is pure, exact, and has no tolerance construct (scanned).
- **Refinements made while building, each disclosed:**
  1. **`msgspec` declared** (PO option 1). The Debug Log has the measurements and the guard edits.
  2. **Private imports from `live_broker_state`.** `_cash_from` and `_emit` are imported, not
     duplicated. Both sides of the comparison are normalised by one function, so normalisation
     cannot manufacture or hide a difference; `live_broker_state.py` stays zero-diff. The same
     sharing reaches `live_reconcile.py` (`_emit`).
  3. **Record fields.** `reconcile.session_view_read` carries `position_count` (the 4.1 record's
     name) rather than D-G's `open_positions`. It also carries `positions={iid: qty}`, `cash` and,
     since the code review, D-G's `currencies`, which the first version had dropped while this note
     read as if it only added fields.
  4. **The CLI prints a header and, for a running session, a note** that a fill landing during the
     check can show as a transient discrepancy (re-run to confirm). These are informational lines,
     not part of the verdict.
  5. **Task 3.2's "several local positions summed per instrument"** is proven in the view reader's
     suite, where the summing happens (`test_several_open_positions_in_one_instrument_are_summed`
     and the real-Redis round trip). `SessionView` refuses duplicate instruments, so the service
     can never receive an unsummed view.
  6. **Task 7.1's "extend `TestNoRealMoneySurface` to `live_reconcile.py`"** is done as a sibling
     class in `test_live_reconcile_cli.py`, the same AST scan against the new module, rather than by
     editing `test_live_cli.py`.
- **AC evidence:**
  - **AC #1:**
    - `TestTheSequence` (driver: D-F order, teardown on every path);
    - `TestTheSessionsPositionsAreRead` / `TestTheSessionsCashIsRead` (view, on real `Position`s);
    - `TestTheRealRoundTrip` (real Redis);
    - `TestACleanCheck` (CLI).
  - **AC #2:** `TestTheNodeIsReadOnlyAndNeverTheSessions`:
    - `+ 1` on the node and on the account gate's `IB_CLIENTS` key;
    - the real `build_trading_node_config` gives both IB clients `ibg_client_id == 11`, with no
      strategies, actors or controller;
    - the caller's settings are unchanged.
  - **AC #3:** `TestPositions` / `TestCash` / `TestRender`, covering `0.0001`-share and `0.01`-cash
    exactness, unresolved rows and unknown cash, plus the `TestNoTolerance` scan and its twin.
  - **AC #4:**
    - exit 5 (`TestADiscrepancy`, `test_a_discrepancy_is_a_finding_and_exits_five`);
    - zero writes: repository spy reads only; `MONITOR` shows no write verb and the namespace is
      byte-identical (`TestTheReadIsLoadOnly`); the AST scan forbids adapter mutators; the node
      carries nothing that could trade.
  - **AC #5:** `test_a_clean_report_says_so_in_words`, `test_a_flat_clean_report_reads_positions_zero`,
    and `test_only_ok_maps_to_zero` still green with `DISCREPANCY` added.
  - **AC #6:** `TestTheCheckIsBounded`:
    - default budget 30/10/5/20;
    - the read clamped;
    - a spent budget never calls the reader;
    - a never-connecting node and a silent broker both end within budget;
    - `elapsed_ms` carried.

    Live: P16, defined, not run *(at story close; run and passed, A and B, in the 2026-09-28 Epic 4
    live run — `docs/qa/phase3-live-verification.md`)*.
- **Guard lists (CLAUDE.md Anti-Patterns), all in this change:**
  - `NODE_FACING_MODULES` gained both core modules and `EXEMPT_MODULES` the CLI module;
    `TestImportPurity.MODULES` gained both core modules. Each has its reason.
  - `STOP_PATH_MODULES`: non-membership comment added.
  - `_STDLIB_AND_FIRST_PARTY`: no edit, proven by running it.
  - `LIVE_MODULE_GLOBS` consumers are green.
  - D3 marker scan green, `UNMARKED` unchanged.
- **Gates:**
  - `ruff format` / `ruff check` / `make typecheck` clean.
  - unit **2943 passed** (+92 over 2851).
  - component **1690 passed / 16 skipped** (+61).
  - integration `tests/integration/core/` `--forked` **97 passed / 2 skipped** (+5); the full
    integration tier **323 passed / 2 skipped** (+5 over 318).
  - `is_live` in `src/core/strategies/` = **0**.
  - The D-J zero-diff set (`live_session_runner.py`, `live_node_builder.py`, `live_broker_state.py`,
    `src/db`, `alembic`, `src/api`, `templates`) is **empty** in `git diff --stat`.
  - README validated and updated: the `live reconcile` row, exit `5`, a local-view note.
    `IBKR_LIVE_CLIENT_ID`'s "reserves `+ 1` for reconcile" is now true.
- **Live verification.** P16 is written and recorded as **defined, not run**: no Gateway, and no
  `TWS_ACCOUNT` in this worktree *(at story close; passed, A and B, in the 2026-09-28 Epic 4 live
  run — see `docs/qa/phase3-live-verification.md`)*. It is informational only. Nothing was submitted; `--real-money`,
  `.env`, docker and the kill/reconnect/connection-loss scripts were not touched.
- **Routed** (`deferred-work.md`, "Deferred from: story-4.6"):
  - G3 amendment → architecture / retro;
  - `compare` reuse → 4.2;
  - live "Open Positions" is always 0 → unowned, named;
  - cash timestamp → 4.7;
  - `msgspec` recorded;
  - empty p12/p13 namespaces → P16 operator / 4.2;
  - the connect budget with the Gateway down → P16 operator;
  - the P16 run itself.

- **Code review fixes (2026-09-27). All 25 patches applied under the PO's option 0
  (batch-apply).**
  - **HIGH: the account key.** `live_session_view.normalised_account` trims and upper-cases
    `TWS_ACCOUNT` exactly as `live_node_builder._resolve_account` does. That builder function
    produced the exec client's `account_id`, which names the summary key and stamps every position.
    A parametrized test pins the two equal, because a duplicated rule needs its own equality pin. A
    lowercase or padded account now finds the session's cash.
  - **Reader robustness.** Every adapter touch now goes through the typed guard (`_call`): opening
    the adapter, building the `PositionId`, `load()`'s shape, and a position's
    `is_open` / `account_id` / `instrument_id` / `signed_decimal_qty`. Each failure is
    `SessionViewUnavailableError(UNREADABLE)` with one `reconcile.session_view_failed` record and
    no adapter text.
    - Position keys are deduped (`sorted(set(...))`), because `SCAN` may repeat a key.
    - Positions held for another account are left out and counted in a WARNING
      `reconcile.session_view_other_account` (`positions_skipped` only). This is symmetric with the
      broker reader's account filter.
    - `LOAD_METHODS` is pinned as an exact, mutator-free set, and a new AST scan covers the
      `_member(adapter, "<name>")` dispatch as well as `adapter.<x>()` calls, with a non-vacuity
      twin.
  - **Order and budget (D-F amended).**
    - `gate:static` now runs **first**, before any socket, so a refusal is exit 3 whatever else is
      down.
    - The Redis preflight became a **session-state precheck** (`require_engine_state`: Redis
      reachable *and* the namespace non-empty). It runs before the IB node, so a never-run session
      costs no Gateway connection. The full view read stays after the broker read.
    - The connect deadline is clamped to what the budget leaves.
    - `ReconcileBudget` validates its fields: finite and positive, `broker_read ≤ 30`, and
      `connect + teardown_reserve < total`.
    - The whole check is timed again after teardown, and an overrun logs
      `reconcile.budget_exceeded`.
    - README now states the bound honestly: a healthy Gateway takes seconds, a down one about 20 s
      before exit 4, and an overrun is logged.
  - **Tests that could not fail, or were missing, now can or exist:**
    - the returned account refusal;
    - teardown exactly once (`stop` and `dispose` counted) on six paths, discrepancy included;
    - "the reader is handed a positive timeout inside the budget" replaced the double-honours-its-own-timeout test;
    - a spent budget reached through a slow account gate;
    - the connect-deadline clamp;
    - invalid budgets;
    - the overrun record;
    - the UUID resolution (the name lookup now finds nothing);
    - the real-money scan's non-vacuity twin;
    - a `FORBIDDEN_ORDER_METHODS` scan over all five new modules (`TestTheReconcileModulesSubmitNothing`);
    - the tolerance scan widened to all five modules (both `_elapsed_ms` helpers lost their
      `round(`);
    - fractional-quantity netting (`0.500001 + 0.249999 = 0.750000`, and a net of `0.000001` is not
      flat);
    - a **real-adapter canary**: the IB exec client's own `_on_account_summary` caches
      `accountSummary:<account>` bytes that `live_session_view._cash` parses to the same cash as
      `_cash_from(_account_summary)`.
  - **The real-Redis proof** now checks every command from the reader's own connections, not only
    namespaced lines: data verbs are limited to read verbs, housekeeping is allowed, and `FLUSH*`
    from any client fails. It also snapshots hash and zset contents, and it waits for the namespace
    to settle (polling, bounded) instead of `sleep(0.3)`. The fixture now stamps IB-shaped account
    ids (`INTERACTIVE_BROKERS-DU4076626`), because the new account filter would otherwise have
    skipped every position. Three consecutive runs: 15/15.
  - **API shapes that differ from the story text, disclosed (the auditor's finding):**
    - `compare(broker, local, *, session_name, elapsed_ms)` takes `trader_id` from
      `local.trader_id` rather than a keyword.
    - Task 3.2's `render(report)` shipped as `render_report`.
    - D-D's `report.outcome` is `live_reconcile.outcome_for(report)` / `exit_code_for(report)`,
      because the stdlib model may not import `src.core.exit_outcome`'s enum.
    - Task 6.3's `reconcile_budget` is the `ReconcileBudget` dataclass and its `read_timeout`.
    - `SessionView.cash_known` is derived (`bool(cash)`) rather than a stored field.
    - The driver's Redis seam `reachability` became `state_check` (the precheck above).
  - **Mutation sweep re-run** (`/tmp/p46/mutate2.py`): **34/35 killed**.
    - The original set was re-anchored, with M9 now as the spec defines it ("no teardown on the
      **discrepancy** path", KILLED) plus M9b "no teardown on any success" (KILLED).
    - One R-mutation per fix: R1, R2a–c, R3, R4, R5a/b, R6, R8, R17, R19, R23a/b and R25, all
      KILLED.
    - The only survivor is M4, the documented equivalent mutant.
  - **Post-review P16 re-runs** (no Gateway, no `TWS_ACCOUNT`):
    - `live reconcile p13-0922` gives `gate.static ok` → `reconcile.session_view_failed
      reason=no_engine_state` in 5.5 ms → exit 1, **before any IBKR socket**;
    - `live reconcile p7-fill-0901` gets past the precheck and stops at the builder
      (`TWS_ACCOUNT is not set`), exit 1, before any IBKR socket.
  - **Gates after the fixes:**
    - `ruff format` / `ruff check` / `make typecheck` clean.
    - unit **2955 passed** (+104 over the 2851 baseline).
    - component **1729 passed / 16 skipped** (+100). One earlier full run showed a single failure
      in the pre-existing, timing-sensitive
      `test_session_runner_phases.py::…::test_trading_registers_and_starts_every_strategy_in_spec_order`.
      It passed alone (103/103) and in a full re-run: the CPU-starvation flake 4.1 recorded, not a
      regression.
    - full integration `--forked` **323 passed / 2 skipped**.
    - `is_live` = 0; the zero-diff set is empty.
  - **Sizes after the fixes:**

    | Module | Statements | Largest function |
    |---|---|---|
    | `live_reconcile.py` | 189 | 28 |
    | `live_session_view.py` | 211 | 28 |
    | `reconciliation_service.py` | 98 | 26 |
    | `models/reconciliation.py` | 102 | — |
    | CLI | 46 | 21 |

    All are under every cap.

### File List

New:

- `src/models/reconciliation.py`
- `src/services/reconciliation_service.py`
- `src/core/live_session_view.py`
- `src/core/live_reconcile.py`
- `src/cli/commands/live_reconcile.py`
- `tests/unit/models/test_reconciliation.py`
- `tests/unit/services/test_reconciliation_service.py`
- `tests/unit/cli/commands/test_live_reconcile_cli.py`
- `tests/component/core/test_live_session_view.py`
- `tests/component/core/test_live_reconcile.py`
- `tests/integration/core/test_live_session_view_redis.py`
- `_bmad-output/implementation-artifacts/4-6-check-positions-and-cash-against-ibkr-on-demand.md`

Modified:

- `src/core/exit_outcome.py` (`DISCREPANCY`, `EXIT_DISCREPANCY = 5`)
- `src/cli/commands/live.py` (one import, one `add_command`)
- `pyproject.toml`, `uv.lock` (`msgspec` declared; PO-approved)
- `README.md`
- `docs/agent/nautilus.md`
- `docs/qa/phase3-live-verification.md` (Procedure P16)
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (the `4-6-…` key only)
- `tests/unit/core/test_live_check.py`
- `tests/unit/core/test_live_node_never_exits.py`
- `tests/unit/core/test_live_stop_path_is_inert.py`
- `tests/component/core/test_session_runner_phases.py`
- `tests/integration/core/test_epic1_ac_node.py`

## Change Log

- 2026-09-27 — Story drafted (create-story): ready-for-dev.
  - The local view is the session's Redis engine cache (D-A), because the DB holds only closed
    trades (F1).
  - Load-only reads were measured write-free with `MONITOR`.
- 2026-09-27 — Implemented (dev-story): all 14 tasks done.
  - One HALT: the `msgspec` dependency, resolved by the PO's option 1 (declare it; nothing
    installed).
  - Mutation sweep 18/19 killed; M4 is equivalent.
  - Test counts: unit 2943 (+92), component 1690/16sk (+61), integration 323/2sk (+5).
  - P16 is defined, not run (no Gateway, no `TWS_ACCOUNT`).
  - Status → review.
- 2026-09-27 — Code review (three parallel layers plus the reviewer's own read; 40 raw findings →
  0 decisions, 25 patches, 4 deferred, 4 dismissed). The PO chose option 0 (batch-apply), and all
  25 patches are applied.
  - One HIGH: the cash key uses the builder's normalised account.
  - Gate-first order and the session-state precheck.
  - A visible budget overrun.
  - A real-adapter canary.
  - Mutation sweep: 34/35 killed; M4 is equivalent.
  - Test counts: unit 2955, component 1729/16sk, integration 323/2sk.
  - Status → done.
