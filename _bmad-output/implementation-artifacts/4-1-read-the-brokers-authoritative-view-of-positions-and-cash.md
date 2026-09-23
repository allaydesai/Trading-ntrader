# Story 4.1: Read the Broker's Authoritative View of Positions and Cash

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want the system to be able to ask IBKR what it actually holds,
so that every downstream check has an external authority to compare against rather than trusting
itself.

## Why this story is shaped the way it is

Read this before designing anything. The epic text sounds like "call the adapter's position report
and read the account balance". Both of those are wrong, for reasons measured at drafting
(2026-09-22, installed `nautilus-trader 1.220.0`, head `11cba25`). Wheel paths are relative to
`.venv/lib/python3.11/site-packages/nautilus_trader/`. Note that ripgrep skips `.venv`
(it is gitignored), so search it with `grep -n` on explicit paths.

**1. Nothing in Nautilus or its IB adapter can tell "flat" from "failed".** This is the
central fact of the story, and it holds at three levels (F1–F3):

- the adapter's `get_positions` returns `None` for a timeout, a lost connection **and** an
  account with zero positions;
- `generate_position_status_reports` turns all three into `[]`;
- the engine counts a client whose mass-status call raised as *reconciled*.

AC #3 and AC #4 are unmeetable on any existing API. The story builds the missing signal. It
**observes** the adapter's own request future: a list, even an empty one, means IBKR answered
`positionEnd`, and an exception means it did not. It never creates, ends or cancels an adapter
request (D-B).

**2. Nautilus's account balance is not cash, and is sometimes invented.** The adapter builds
`AccountState` like this:

- `total` is `NetLiquidation` (equity, not cash);
- `locked` is `FullMaintMarginReq`;
- when maintenance margin exceeds half of net liquidation it sets `total = 400000` with a
  `# TODO: Bug` comment (F5).

So `cache.account(...)`, `portfolio.account(...)` and `balance_total()` are all unusable as
"the broker's cash". IBKR's cash is the `TotalCashValue` tag. The adapter receives it on its
account-summary subscription and keeps it in two places:

- the exec client's in-memory `_account_summary`;
- a Redis-persisted general-cache key. Across a restart, that key serves the **previous** run's
  values until new ones arrive (F6).

The story reads the in-memory copy (D-A).

**3. There is nowhere to wire it yet, and that is correct.**

- `src/core/live_session_runner.py` is at **499 of a hard 500** executable statements (Story
  4.4's closing measurement).
- `_phase_reconcile` belongs to Story 4.2.
- `ntrader live reconcile` belongs to Story 4.6.

So this story ships a library with no production caller, by design, like
`confirm_state_reestablished`'s "dormant until Epic 4". It also ships an operator surface, an
opt-in `--read-broker-state` flag on the existing `live_node_probe.py`, following the
`--verify-account` / P2 precedent (D-H). The reader takes a *connected node*, not
`IBKRSettings`. It finds its IB client through the node's own exec client, so it works equally
on the session's node (`ibkr_live_client_id`, Story 4.2) and on 4.6's read-only node
(`ibkr_live_client_id + 1`, AR34). The existing `IB_CLIENTS[(host, port,
ibkr_live_client_id)]` lookups hard-wire the session's id.

### Measured facts — cite, don't re-derive

The drafting research read every fact below in the installed wheel. Task 1 re-measures F1, F2,
F4 and F7 in a fresh interpreter before production code is written.

| # | Fact | Where |
|---|---|---|
| F1 | `get_positions(account_id)`:<br>• registers one request named `"OpenPositions"` (`handle=self._eclient.reqPositions`), or **joins** an in-flight request of that name;<br>• awaits `_await_request(request, 30)`;<br>• then `if not all_positions: return None`;<br>• filters `position.account_id == account_id`.<br>`_await_request` returns `None` on `TimeoutError` **and** on `ConnectionError`. `positionEnd` resolves the future with the collected list, `[]` for an empty login. So `None` means *timeout*, *connection lost* or *zero positions*. `[]` means only "other accounts hold positions, this one does not". The type hint says `list[Position]`; the value is `list[IBPosition]`. | `adapters/interactive_brokers/client/account.py:138-179`, `client/client.py:495-534` |
| F2 | `_end_request(req_id, success=True)` does `future.set_result(request.result)`. `success=False` calls `request.cancel()` (the Request's **cancel callable**, a no-op lambda here, not `future.cancel()`), then `future.set_exception(exception)` if one was given. The request is then removed from the registry. A socket disconnect sets `ConnectionError("Socket disconnected.")` on every pending request future (`connection.py:237-239`). An IB error on the request calls `_end_request(req_id, success=False)` with no exception (`error.py:197`), which leaves the future **pending forever**. `reqPositions` carries no reqId on the wire, so an IB error cannot be matched to it anyway. `Requests.get(name=...)` returns a `Request` whose `.future` is the same `asyncio.Future` object each time. | `client/client.py:536-566`, `client/common.py:52-65, 366-483`, `client/error.py:101-109, 197` |
| F3 | `generate_position_status_reports`:<br>• `if not positions: return []`, so failure reads as flat;<br>• ignores its `command` filter;<br>• skips zero quantity;<br>• resolves each contract through `instrument_provider.get_instrument(contract)`, which **raises `ValueError`** for an unresolvable non-BAG contract rather than returning `None`, so its `if instrument is None: continue` branch is dead and one bad row aborts the whole report;<br>• sets `avg_px_open = instrument.make_price(avg_cost)`, not divided by the multiplier.<br>`generate_mass_status` catches any exception and returns `None` (`live/execution_client.py:505-507`). `reconcile_execution_state` logs a warning for a `None` mass status and **does not count it as a failure** (`live/execution_engine.py:915-920`). Its `timeout_secs` is only validated positive, never enforced (`:874`). | `adapters/interactive_brokers/execution.py:454-505`, `providers.py:99-129` |
| F4 | `process_position` appends to an in-flight `"OpenPositions"` request **before** it considers the `"PositionUpdates"` streaming subscription, which the exec client opens at connect (`execution.py:232`). A streaming update that lands while a request is pending is appended to that request's result. The same `conId` can then appear twice; nothing de-duplicates it. Each IB `position` callback carries the full current position, not a delta, so the last row per `conId` is the truth. | `client/account.py:212-241` |
| F5 | `_on_account_summary`:<br>• `total = NetLiquidation`, `locked = FullMaintMarginReq`, `free = total - locked`;<br>• `if total - locked < locked: total = 400000  # TODO: Bug`;<br>• builds `AccountBalance` / `MarginBalance` and calls `generate_account_state`;<br>• stores **every** tag, float-converted when numeric, in `self._account_summary[currency][tag]`;<br>• sets `_account_summary_loaded` on the **first** tag, outside the completeness check, so `_connect` can return before `TotalCashValue` has arrived.<br>Tags with an empty currency (`AccountType`, `Cushion`, …) are stored under key `""`. | `adapters/interactive_brokers/execution.py:173-182, 795-846` |
| F6 | The same handler also does `self._cache.add(f"accountSummary:{account}", json.dumps(self._account_summary).encode())`. That is a general-cache key, and the session cache is Redis-backed (Story 2.4), so it persists and is reloaded at the next process start. | `execution.py:841-844` |
| F7 | `subscribe_account_summary` requests `reqAccountSummary(groupName="All", tags=AccountSummaryTags.AllTags)`, and AllTags includes `TotalCashValue`. `$LEDGER` is **not** requested, so values arrive in the account's **base currency only** (expect one `USD` row). `process_account_summary` routes rows to the `accountSummary-{account}` handler. Only the configured account's rows reach the exec client. | `client/account.py:52-83, 181-196`; `ibapi/account_summary_tags.py:38-70` |
| F8 | Getting the exec client from a node:<br>• `node.kernel.exec_engine._clients` is `cdef readonly dict[ClientId, ExecutionClient]`, readable from Python;<br>• the public `registered_clients` returns ids only;<br>• the IB exec client's id is `ClientId("INTERACTIVE_BROKERS")` (the adapter's `IB` constant, `adapters/interactive_brokers/common.py:29`);<br>• its `._client` is the `InteractiveBrokersClient`, and `account_id.get_id()` is the bare configured account (`execution.py:171, 459-461`);<br>• `instrument_provider` is a property (`:184-186`).<br>Do **not** use `LiveExecutionClient.query_account`: it calls an undefined `_query_account`. | `execution/engine.pxd:51`, `engine.pyx:191-200`, `live/execution_client.py:323-328` |
| F9 | The IB client's two readiness flags, `_is_ib_connected` and `_is_client_ready`, are the only honest connection signal. `exec_engine.check_connected()` stays `True` through a dead socket. Precedent: `src/core/live_connection_probe.py:30-49, 72-130`, fail-closed, with a name canary in `tests/component/core/test_live_connection_probe.py`. | `src/core/live_connection_probe.py` |
| F10 | `reqPositions` returns positions for **every** account on the login (`ibapi/client.py:2359-2360`). The exec client faults at connect if the configured account is not in `managedAccounts` (`execution.py:203-213`). | |
| F11 | Size budgets:<br>• `live_session_runner.py` is at 499/500, **not touched by this story**;<br>• `live_node_probe.py` is a script under `scripts/`, outside `test_size_caps`' `src` walk;<br>• new modules must be under the caps: file < 500, class < 100, function < 50 executable statements.<br>The baseline is **exact**: `test_regenerating_the_baseline_reproduces_it_exactly`. | `tests/unit/governance/test_size_caps.py` |

### Design decisions (disclosed here, not discovered in review)

- **D-A — Read the broker, not a cache.**
  - **Positions:** a fresh `reqPositions` through the adapter's own
    `InteractiveBrokersClient.get_positions(account)` (F1), never through
    `generate_position_status_reports` (F3) and never from `cache.positions_open()`.
    `flatten_position.py:142-153` reads the cache *after* reconciliation. That echoes Nautilus's
    view, and it is exactly what 4.2/4.6 must compare **against**, not with.
  - **Cash:** `TotalCashValue` per currency from the exec client's in-memory
    `_account_summary` (F5). Never from `AccountState` / `balance_total()` (NetLiquidation, or
    the invented 400000). Never from the `accountSummary:` cache key (F6: across a restart it
    serves the previous process's values).
- **D-B — "Empty" and "failed" are told apart by observing the adapter's own request future.**
  1. Start `get_positions` as a task.
  2. Obtain the in-flight `"OpenPositions"` `Request` through `ib_client._requests.get(name=...)`,
     read-only. If one is already in flight (Nautilus's reconciliation, or `_initialize_position_tracking`),
     this is the request `get_positions` joins.
  3. Hold its `.future`.
  4. When the task finishes, classify from the **future**, not from the `None`-conflating return
     value:
     - result is a `list`, even `[]`: IBKR answered. Filter to the configured account.
     - exception `TimeoutError`: the adapter's own 30 s timeout. Failure `POSITIONS_UNANSWERED`.
     - `ConnectionError`: failure `CONNECTION_LOST`.
     - anything else, or the future never observed, cancelled, or still pending: failure
       `ADAPTER_INCOMPATIBLE`.

  The reader never calls `_requests.add`, `_end_request`, `reqPositions`, `cancelPositions` or
  `subscribe_*`. **It reads adapter state; it never writes it.**
- **D-C — One deadline over the whole read, and nothing is ever cancelled.**
  - `DEFAULT_BROKER_STATE_TIMEOUT_SECONDS = 20.0`. It is under NFR5's 30 s (pinned by a test),
    which leaves 4.6 about 10 s of its own NFR5 budget for connect, compare and disconnect.
    It is overridable per call.
  - The deadline covers the connection pre-check, positions, instrument resolution and the cash
    wait.
  - The adapter task is awaited with `asyncio.wait({task}, timeout=remaining)`, **never**
    `wait_for(task)`. `wait_for` would cancel it, and the cancellation propagates into
    `_await_request`'s `wait_for(request.future)`. That **cancels the shared future**, and any
    concurrent joiner then gets `CancelledError`, a `BaseException` that `generate_mass_status`'s
    `except Exception` does not catch.
  - On expiry the task is left to end on the adapter's own timeout. A done-callback consumes its
    result so nothing logs "exception was never retrieved".
  - Instrument resolution uses the same no-cancel wait.
  - Pinned by a test: a concurrent awaiter of the same request still receives its result after
    the reader has timed out.
- **D-D — Success is a value; failure is an exception. There is no third shape.**
  - `read_broker_state` returns a `BrokerState`, whose `positions` may be `()`: flat, which is a
    success. Every failure raises `BrokerStateUnavailableError(reason: BrokerStateFailure,
    detail: str)`.
  - A caller cannot mistake a failure for "flat", because a failure produces no `BrokerState` to
    read. This is the whole of AC #3/#4.
  - Reasons (`StrEnum`): `NOT_CONNECTED`, `TIMEOUT` (our deadline), `POSITIONS_UNANSWERED`
    (adapter timeout), `CONNECTION_LOST`, `CASH_UNAVAILABLE`, `ADAPTER_INCOMPATIBLE`.
  - D3 markers:
    - `BrokerStateUnavailableError`: `exit_outcome = BROKER_UNREACHABLE` (exit 4, AR28's
      "broker connectivity failure").
    - Its subclass `BrokerStateAdapterError`, used for `ADAPTER_INCOMPATIBLE`:
      `exit_outcome = ERROR` (exit 1). An adapter rename is a code problem, and exit 4 would send
      the operator to restart a healthy Gateway. This is the `ACCOUNT_VERIFICATION_ERROR`
      precedent.
    - Both set `operator_safe_message = True`. The text is our own and never embeds broker or
      exception text (NFR26).
- **D-E — Where the types live (AR38).**
  - Value types go in a new stdlib-only **`src/models/broker_state.py`**: `BrokerPosition`,
    `CashBalance` and `BrokerState`, as frozen dataclasses with `Decimal` money and quantities
    and tuple collections. A services-layer consumer (4.6's `reconciliation_service.py`, which
    must never import Nautilus) can import them. `import src.models` loads no Nautilus, measured.
  - The reader, the reason enum and the two exceptions go in a new
    **`src/core/live_broker_state.py`**. It is **duck-typed with no `nautilus_trader` import**
    (the `live_trade_recorder` / `live_session_warmup` discipline), so it is unit-testable with
    stubs. Its exceptions sit under the D3 marker scan's `src/core/live_*.py` glob.
  - The adapter's `"INTERACTIVE_BROKERS"` client id and `"OpenPositions"` request name are module
    constants. A component-tier canary pins each against the real adapter.
- **D-F — An unresolvable instrument is reported, never dropped.**
  - Each row's contract is resolved through the exec client's
    `instrument_provider.get_instrument(contract)`. That is the resolution Nautilus's own
    reconciliation uses, so ids match the cache (`NVDA.NASDAQ`).
  - On any exception (F3: `ValueError`), the row is kept with `instrument_id =
    "IB-CONID-<conId>"`, `instrument_resolved=False`, and the contract's `symbol`, plus one
    WARNING `reconcile.broker_instrument_unresolved` carrying `con_id`, `symbol`, `sec_type` and
    `error_type` only.
  - Dropping the row would be a partial "flat": broker exposure that no downstream check could
    see. Carrying it makes 4.2/4.6 report it as a discrepancy by name, which is the honest outcome.
- **D-G — Row normalisation:**
  - keep only `row.account_id == account` (F10);
  - skip quantity `0` (IB keeps showing closed positions);
  - de-duplicate by `conId`, **last row wins** (F4);
  - `quantity` is a signed `Decimal`: `+` long, `−` short;
  - `average_price = Decimal(str(avg_cost)) / multiplier`, where `multiplier` is the contract's
    `multiplier` parsed as `Decimal`, default `1`;
  - `None` when `avg_cost` is ≤ 0, NaN or infinite;
  - positions sorted by `instrument_id`, cash by `currency`, so output is deterministic.

  ⚠️ IBKR's `avgCost` semantics are **unverified offline**. IB documents stock `avgCost` as
  commission-inclusive, and Nautilus's `avg_px_open` excludes commission, so the two will
  differ by commission ÷ quantity. This story **reports** the price and compares nothing. 4.6's
  comparison must compare quantities exactly and treat price as informational; that is routed
  there. P15 records one live reading.
- **D-H — No runner, CLI, DB or migration change.**
  - The runner is at 499/500 and `_phase_reconcile` is 4.2's. `live reconcile` is 4.6's.
    `SessionRecordPort`, `EXPECTED_CAPABILITIES`, `src/db/**`, `alembic/**` (head
    `85c949ac0374`), `src/services/**`, `src/api/**` and `templates/**` are **zero-diff**.
  - Operator surface: `scripts/diagnostics/live_node_probe.py --read-broker-state`, an opt-in
    flag, so P1's and P2's documented output is byte-for-byte unchanged without it. It prints the
    rendered state (masked) and appends `broker_state=ok positions=N` to the `RESULT:` line.
- **D-I — Records (AR41 `reconcile.*` namespace, NFR26 masking).**
  - `reconcile.broker_state_retrieved`, INFO:
    - `account` (masked via `live_gate.mask_account`);
    - `positions` (count);
    - `instruments` (sorted ids);
    - `cash` (`{currency: str(amount)}`);
    - `elapsed_ms`.
  - `reconcile.broker_state_failed`, ERROR: `reason`, `detail`, `elapsed_ms`, `error_type` when
    an exception caused it.
  - `reconcile.broker_instrument_unresolved`, WARNING (D-F).
  - None of these is an AR41 milestone. `reconcile.ok` / `reconcile.discrepancy` stay 4.2/4.3's.
  - Never log a raw account, a `str(exc)`, or broker text.
- **D-J — Cash freshness is disclosed, not engineered.**
  - Positions are a fresh request. Cash is IBKR's **latest account-summary push received by this
    process**. It is pushed in full at subscribe (the exec client's `_connect`), then as IB
    updates it.
  - At 4.2's `reconcile` phase and on 4.6's freshly connected node, the push is seconds old.
    Mid-session (4.3/4.7) it can lag.
  - The reader waits, bounded by the deadline, for `TotalCashValue` to be present in at least
    one non-empty currency, because `_connect` returns after the *first* tag (F5). If it is
    absent at the deadline: `CASH_UNAVAILABLE`. Never zero, never skipped.
  - Forcing a refresh by re-calling `subscribe_account_summary()` re-sends `reqAccountSummary`
    with the same reqId, which is unmeasured IB behaviour. Not done; routed to 4.3/4.7.
- **D-K — Fail fast when the socket is down.** Before issuing anything, read the IB client's
  `_is_ib_connected` / `_is_client_ready` (F9) through a local fail-closed helper, the
  `live_connection_probe._flag_is_set` shape. That module imports Nautilus at top level and
  hard-wires `ibkr_live_client_id`, so it is not imported. Either flag unset: `NOT_CONNECTED`
  immediately. Otherwise `reqPositions` on a dead socket answers nothing, and the operator waits
  the full deadline for the same verdict.

## Acceptance Criteria

The epic text (`epics.md:1481-1510`) is in **bold**. The clarifications under each are binding.

1. **Given a connected session, When broker state is requested, Then positions (instrument,
   quantity, average price) and cash balances are retrieved for the configured account (FR32).**
   - `await read_broker_state(node)`, on the node's loop, returns a `BrokerState` holding:
     - every non-zero position the broker reports for the exec client's configured account, as
       `(instrument_id, signed quantity, average_price)`;
     - `TotalCashValue` for every non-empty currency the broker pushed.
   - Other accounts' rows are excluded (F10).
   - Proven against the **real adapter code** at component tier: the real `get_positions` /
     `_await_request` / `_end_request` / `Requests` / `process_position` /
     `process_position_end` / `_on_account_summary`, driven by a harness standing in for the
     socket.
   - Live: Procedure P15 (read-only, informational).
2. **Given the retrieved state, When it is represented internally, Then it is converted to domain
   types at the boundary, so nothing downstream depends on Nautilus or IBKR types (AR38).**
   - `src/models/broker_state.py` imports only the standard library. This is proven two ways:
     - an AST scan;
     - a fresh-interpreter `sys.modules` check forbidding `nautilus_trader`, `ibapi`,
       `sqlalchemy`, `src.db` and `src.services`.
   - A recursive walk of a returned `BrokerState` finds only `str`, `int`, `bool`, `Decimal`,
     `datetime`, `None`, `tuple` and the three dataclasses. No `IBPosition`, `IBContract`,
     `Price`, `Quantity`, `InstrumentId` or `Money` survives the boundary.
   - `BrokerState.account` holds the **masked** form only; a raw id is refused at construction
     (NFR26).
3. **Given an account with no positions, When state is retrieved, Then an empty result is returned
   successfully and is distinguishable from a failed retrieval.**
   - Against the real adapter code:
     - A `positionEnd` with zero rows makes `get_positions` return `None`. This is F1's
       conflation, pinned as a **canary** that fails by name if an upgrade fixes it.
     - `read_broker_state` returns `BrokerState(positions=())`, with cash, as a success.
   - The same harness with the request timing out, or with the socket dropping, **raises**.
   - Mutation: classify on `get_positions`' return value instead of the future. The empty-account
     test must go red (record it).
4. **Given retrieval fails or times out, When the error surfaces, Then it is reported explicitly
   and never silently treated as "flat" (NFR20).**
   - Each reason raises `BrokerStateUnavailableError` (or `BrokerStateAdapterError`) with that
     `reason`, logs exactly one `reconcile.broker_state_failed`, and returns no `BrokerState`.
     Each of these has a test:
     - `NOT_CONNECTED`: no request is issued;
     - no IB exec client registered: `NOT_CONNECTED`;
     - our deadline: `TIMEOUT`;
     - adapter `TimeoutError`: `POSITIONS_UNANSWERED`;
     - `ConnectionError`: `CONNECTION_LOST`;
     - a pending-forever future (F2's IB-error path): `TIMEOUT`;
     - no `TotalCashValue` by the deadline: `CASH_UNAVAILABLE`;
     - a missing adapter member: `ADAPTER_INCOMPATIBLE`.
   - The exit codes are asserted on the real classes through `live_check.classify_failure`: 4 and 1.
   - An unresolvable instrument is **not** a failure. It is reported under D-F, and a test proves
     the row survives.
5. **Given the retrieval, When it is timed, Then it completes within 30 seconds (NFR5).**
   - `DEFAULT_BROKER_STATE_TIMEOUT_SECONDS` is pinned `== 20.0` and `< 30`.
   - A broker that never answers makes the read return (raise `TIMEOUT`) within `timeout + 0.5 s`,
     using a small injected timeout. So do a slow instrument resolution and a cash tag that
     never arrives. **One** deadline bounds every await.
   - `elapsed_ms` is on both records.
   - D-C's no-cancel property is pinned: after the reader times out, a concurrent awaiter of the
     same adapter request still receives the adapter's eventual answer.
   - Live: P15 records `elapsed_ms` against the real Gateway.

## Tasks / Subtasks

- [x] **Task 0 — Standing checks (record results in the Debug Log)**
  - [x] 0.1 Head, clean tree. `uv run ruff check .` and `make typecheck` clean before the first
    edit. Try `uv run alembic current` and record it; a denied tool is recorded, not assumed.
  - [x] 0.2 Baselines: `make test-unit` and `make test-component`. Record passed/failed/skipped,
    including the two pre-existing `custom/`-submodule size-cap failures Story 4.4 recorded, if
    they reproduce here.
  - [x] 0.3 Gateway reachable? Record how it was determined. At drafting, the harness denied
    every port-probe command (`nc`, `lsof`), and it was 20:52 ET Tuesday, after RTH. A
    positions/cash read does not need RTH, so P15 can run outside market hours if a Gateway is
    up.

- [x] **Task 1 — Probes (fresh interpreter, `/tmp/p41/`, not committed), BEFORE production code**
  - [x] 1.1 Harness the real `get_positions` / `_await_request` / `_end_request` / `Requests` /
    `process_position` / `process_position_end` on a minimal object: stub `_eclient.reqPositions`,
    `_next_req_id` and `_log`. Confirm:
    - zero rows followed by `positionEnd` makes `get_positions` return `None`, and the held future's
      result is `[]` (F1);
    - on the timeout path, the future carries a `TimeoutError` (use a short timeout by calling
      `_await_request` directly);
    - on the disconnect path, it carries a `ConnectionError`;
    - a joiner shares the future.
  - [x] 1.2 Confirm D-C: `asyncio.wait_for(task)` cancellation does propagate into the shared
    future (the hazard), and `asyncio.wait({task}, timeout=…)` does not.
  - [x] 1.3 Confirm F4: a `process_position` for the same `conId` during a pending request
    appends a second row.
  - [x] 1.4 Confirm F5's `_account_summary` shape by driving the real `_on_account_summary` on a
    harness `self`: float conversion, the `""` currency key, and `TotalCashValue` present after
    the tag arrives.

- [x] **Task 2 — `src/models/broker_state.py` (AC #2) — TDD, unit tier**
  - [x] 2.1 Red (`tests/unit/models/test_broker_state.py`):
    - frozen;
    - `quantity == 0` refused;
    - empty or lowercase currency refused;
    - duplicate `instrument_id` in `positions` refused;
    - duplicate currency refused;
    - a raw (unmasked) `account` refused;
    - `is_flat` true only for `positions == ()`;
    - collections are tuples;
    - purity, both the AST scan and the subprocess check.
  - [x] 2.2 Green: stdlib only (`dataclasses`, `datetime`, `decimal`). Google-style docstrings.

- [x] **Task 3 — `src/core/live_broker_state.py` (AC #1, #3, #4, #5) — TDD, unit tier with duck-typed stubs**
  - [x] 3.1 Red (`tests/unit/core/test_live_broker_state.py`). Stubs: node → kernel → exec_engine
    `_clients`, then exec client (`_client`, `account_id.get_id()`, `instrument_provider`,
    `_account_summary`), then IB client (`_requests.get(name=…)` returning a request with a
    real `asyncio.Future`, `get_positions`, readiness flags). Cases:
    - every AC #4 reason;
    - success with positions and cash;
    - flat success;
    - other-account rows excluded;
    - zero quantity skipped;
    - `conId` duplicates, last wins;
    - signed quantities;
    - `average_price` with a multiplier and with a bad `avg_cost`;
    - an unresolvable instrument kept with the fallback id and a WARNING;
    - NaN/inf/string `TotalCashValue` treated as absent;
    - the `""` currency ignored;
    - output sorted;
    - records emitted with a masked account and no raw id anywhere, checked across every
      captured record's values;
    - the reader never calls any write-shaped adapter member (spy stubs that record calls to
      `_requests.add`, `_end_request`, `subscribe_account_summary`, `cancelPositions`, and
      assert none happened);
    - the deadline bound, via injected `monotonic` and small timeouts;
    - D-C no-cancel;
    - a done-callback consumes a late exception, asserted by no "Task exception was never
      retrieved" through `loop.set_exception_handler`;
    - `render_broker_state(state) -> list[str]`, the probe's lines, masked.
  - [x] 3.2 Green: implement. No `nautilus_trader` import.
    - Constants: `DEFAULT_BROKER_STATE_TIMEOUT_SECONDS`, `IB_EXEC_CLIENT_ID = "INTERACTIVE_BROKERS"`,
      `OPEN_POSITIONS_REQUEST = "OpenPositions"`, `CASH_TAG = "TotalCashValue"`, and the three
      event names.
    - Keep every function under 50 statements and the file well under 500. Measure with
      `measure_module`.
  - [x] 3.3 Exceptions carry the D3 marker pairs (D-D). `test_exit_outcome_markers.py` must pass
    without growing `UNMARKED`.

- [x] **Task 4 — Real-adapter proofs and canaries (AC #1, #3, #5) — component tier**
  (`tests/component/core/test_live_broker_state_adapter.py`)
  - [x] 4.1 A harness object borrowing the **real** adapter methods (class attributes bound from
    `InteractiveBrokersClientAccountMixin` / `InteractiveBrokersClient`), fed synthetic
    `position` / `positionEnd` / `accountSummary` callbacks. Prove:
    - AC #1, the positions and cash round trip;
    - AC #3, flat versus timeout versus disconnect;
    - AC #5 / D-C, a real `generate_position_status_reports`-shaped joiner still resolves after
      the reader timed out.
  - [x] 4.2 Canaries, each failing **by name** on an upgrade:
    - `IB == "INTERACTIVE_BROKERS"`;
    - `get_positions` still conflates empty with `None` (F1);
    - `Requests.get(name=...)` / `Request.future` exist;
    - `_account_summary`, `_is_ib_connected` and `_is_client_ready` exist on real instances;
    - `IBPosition` fields are `(account_id, contract, quantity, avg_cost)`;
    - `provider.get_instrument` raises `ValueError` on a miss.

    Building a real `InteractiveBrokersClient` needs no socket; the
    `test_live_connection_probe.py:297-328` precedent shows how.
  - [x] 4.3 Every component file carries the `_assert_c_logging_state_is_unchanged` autouse
    fixture. Do **not** construct a `LiveExecutionEngine` or a `TradingNode` here.

- [x] **Task 5 — Guard lists and governance (same change as the module that triggers them)**
  - [x] 5.1 Add `src/core/live_broker_state.py` to:
    - `NODE_FACING_MODULES` (`tests/unit/core/test_live_node_never_exits.py`), because it drives the
      node's exec client and Story 4.2 wires it into the runner;
    - `TestImportPurity.MODULES` (`tests/component/core/test_session_runner_phases.py`).

    Give each a reason comment. **Not** `STOP_PATH_MODULES`: it is a startup and on-demand read
    that nothing in the teardown `finally` calls. Add that as a comment there, following the
    `live_session_warmup` precedent.
  - [x] 5.2 `_STDLIB_AND_FIRST_PARTY` is expected to need no edit, because `asyncio`, `time`,
    `math`, `decimal`, `dataclasses`, `datetime`, `enum` and `typing` are already listed. Prove it
    by **running** `tests/integration/core/test_epic1_ac_node.py`, not by inspection. No
    `from __future__ import annotations` (it would trip the list).
  - [x] 5.3 `LIVE_MODULE_GLOBS` picks the module up automatically. Run
    `test_live_dependency_invariance.py` and `test_order_path_has_no_retry.py`.
  - [x] 5.4 Size caps: both new modules under every cap, measured with the guard's own
    `measure_module` and recorded. No baseline entries are expected. If one is unavoidable, add it
    with a one-line reason.

- [x] **Task 6 — Operator surface and docs (D-H)**
  - [x] 6.1 `scripts/diagnostics/live_node_probe.py --read-broker-state`, opt-in. After connect,
    and after `--verify-account` when both are given, run
    `loop.run_until_complete(read_broker_state(node))` and print `render_broker_state`'s lines.
    - Append `broker_state=ok positions=N` to `RESULT:`.
    - On `BrokerStateUnavailableError`, print `RESULT: fail reason=broker_state_unavailable
      failure=<reason>` and exit 1.
    - Without the flag, output is byte-identical.
  - [x] 6.2 `docs/agent/nautilus.md`: a short "Reading broker state" section covering F1/F3/F5
    and the D-A/D-B rules, so the next agent does not reach for `balance_total()` or
    `generate_position_status_reports`.

- [x] **Task 7 — Live verification (informational only, never a gate)**
  - [x] 7.1 Write **Procedure P15** in `docs/qa/phase3-live-verification.md`, continuing after P14.
    Keep the parallel-story renumbering note.
    - It is read-only: `live_node_probe.py --run-seconds 1 --verify-account --read-broker-state`.
      No strategy is added, so nothing can submit. The probe's node has an in-memory cache, so no
      Redis write.
    - Preconditions: no session running on `ibkr_live_client_id`, which the probe shares.
    - Pass criteria:
      1. `RESULT: ok … broker_state=ok`;
      2. the position lines match TWS's Portfolio window instrument for instrument and share
         for share, recorded;
      3. the cash line matches TWS's `TotalCashValue` for the base currency;
      4. `elapsed_ms` is under 30000;
      5. the account appears masked in every `[probe]` line;
      6. `order.submitted` count is 0.
    - Also record one reading of IBKR's average price against the commission-inclusive question
      (D-G).
  - [x] 7.2 If a Gateway is reachable and the worktree has the settings the probe needs, run it
    and record the row. Otherwise record **defined, not run** with the reason. Never
    `--real-money`. Never read or touch `.env`, docker, or the kill/reconnect/connection-loss
    scripts. Never submit, open, close or flatten anything.

- [x] **Task 8 — Routed debt (`deferred-work.md`, new "Deferred from: story-4.1" section)**
  - [x] 8.1 **→ Story 4.2:** Nautilus's native startup reconciliation cannot fail on a failed
    position read. The chain is F1 → F3 → `reconcile_execution_state` counting a `None` mass
    status as success, with `timeout_reconciliation` unenforced. "Execution state reconciled"
    therefore proves nothing about positions, and 4.2's 0-discrepancy check must use this story's
    read, not the framework's log line.
  - [x] 8.2 **→ Story 4.3:** while any `"OpenPositions"` request is in flight, streaming
    position updates are diverted into its result instead of the exec client's
    `_on_position_update` (F4). A mid-session read diverts real updates for its duration. Cash
    freshness mid-session (D-J) is routed here too.
  - [x] 8.3 **→ Story 4.6:** compare quantities exactly and treat average price as
    informational (D-G). Size `timeout_seconds` inside 4.6's own NFR5 budget. The reader takes a
    node, so a `+1` node works unchanged.
  - [x] 8.4 **→ Story 5.1:** the live-host `ResultsExtractor` widening must never read IB
    `AccountState` balances as cash or equity. They are `NetLiquidation`, or an invented 400000
    (F5).
  - [x] 8.5 Story 2.4's "Nothing enforces Redis's disposability yet" entry
    (`deferred-work.md:1218-1223`) gets a note: this story supplies the broker read that 4.2 will
    enforce it with, and the enforcement itself stays 4.2's.

- [x] **Task 9 — Mutation sweep (each applied, target tests run, file restored; record kill/survive)**
  - M1 classify on `get_positions`' return value (None → flat);
  - M2 `wait_for(task)` instead of `asyncio.wait` (cancels the shared future);
  - M3 drop the account filter;
  - M4 first row wins instead of last;
  - M5 drop the zero-quantity skip;
  - M6 drop unresolved rows instead of keeping them;
  - M7 read cash from `NetLiquidation`;
  - M8 return zero cash when `TotalCashValue` is absent;
  - M9 skip the connection pre-check;
  - M10 no deadline on the cash wait;
  - M11 log the raw account;
  - M12 `BrokerStateAdapterError` marked `BROKER_UNREACHABLE`.

- [x] **Task 10 — Gates**
  - [x] 10.1 `uv run ruff format .`, `uv run ruff check .`, `make typecheck`.
  - [x] 10.2 `make test-unit`, `make test-component`. Integration `--forked` for
    `tests/integration/core/` (it includes `test_epic1_ac_node.py`).
  - [x] 10.3 `grep -rn "is_live" src/core/strategies/` gives 0. `git diff --stat` shows the D-H
    zero-diff set untouched.

### Review Findings

Code review, 2026-09-22. Three parallel layers ran: Blind Hunter (diff only), Edge Case Hunter
(diff plus the wheel) and Acceptance Auditor (diff plus spec). They produced 54 raw findings
(21, 23 and 10). After merging duplicates: 0 decisions, 22 patches, 2 deferred, 8 dismissed.

- [x] [Review][Patch] Adapter drift is reported as a broker outage (exit 4). A missing readiness
  flag, or a node with no IB exec client, reads as `NOT_CONNECTED`; both should be
  `BrokerStateAdapterError`, the class whose own docstring forbids exactly this
  [src/core/live_broker_state.py:209-242]
- [x] [Review][Patch] `raise drift from exc` keeps an adapter exception whose text may hold the raw
  account (NFR26). Drift is also impossible to diagnose: there is no location,
  `_observe_request` discards the task's own exception, and the probe prints only the reason
  [src/core/live_broker_state.py:193-196, 261-271; scripts/diagnostics/live_node_probe.py:359-367]
- [x] [Review][Patch] ibapi's unset sentinels pass as data. `UNSET_DOUBLE` (`sys.float_info.max`)
  becomes a ~1.8e308 average price or cash value, and `UNSET_DECIMAL` (`2**127-1`, which the decoder
  returns for an empty field) becomes a real position [src/core/live_broker_state.py:369-403, 423-429]
- [x] [Review][Patch] A position row with `conId` 0 (the decoder's default) would be merged with
  others by last-wins, under-reporting holdings [src/core/live_broker_state.py:316-321]
- [x] [Review][Patch] `BrokerState` accepts `"***DU4076626"` as masked. `MASK_PREFIX` duplicates
  `mask_account`'s literal with no equality pin [src/models/broker_state.py:26, 113-114]
- [x] [Review][Patch] `CashBalance` accepts IBKR's `BASE` aggregate and codes of any length.
  One odd summary key fails the whole read as drift [src/models/broker_state.py:88-90;
  src/core/live_broker_state.py:423-429]
- [x] [Review][Patch] Two contracts that resolve to one instrument id surface as anonymous drift
  from `BrokerState.__post_init__`, naming no instrument [src/core/live_broker_state.py:312-327]
- [x] [Review][Patch] `get_instrument` returning `None`, or an object without `.id`, fails the
  whole read instead of leaving one unresolved row (D-F) [src/core/live_broker_state.py:345-351]
- [x] [Review][Patch] `get_positions` raising after it has registered the request (for example a
  send on a dead socket) costs the full deadline and is reported as `TIMEOUT`
  [src/core/live_broker_state.py:251-258]
- [x] [Review][Patch] Joining an in-flight request can race its answer: it is answered and removed
  before the first observation, giving a spurious "never registered". The "not issued" `TIMEOUT`
  text is false once the task is scheduled [src/core/live_broker_state.py:251-275]
- [x] [Review][Patch] `timeout_seconds` is not validated. Zero, negative or NaN issue a request and
  then time out at once; infinity, or anything above 30, breaks NFR5
  [src/core/live_broker_state.py:147-179]
- [x] [Review][Patch] Nothing checks that the read runs on the node's own loop
  [src/core/live_broker_state.py:147-188]
- [x] [Review][Patch] An injected `monotonic` can drift from the loop's time: a frozen fake makes
  `_read_cash` hang. No caller needs the seam [src/core/live_broker_state.py:152, 178-179]
- [x] [Review][Patch] An exec client whose `_connect` has not finished is not refused, so cash is
  polled for the whole budget [src/core/live_broker_state.py:182-188]
- [x] [Review][Patch] `_emit` receives `log.info` already resolved, so an attribute lookup that
  raises escapes the never-raise helper [src/core/live_broker_state.py:197-199, 457]
- [x] [Review][Patch] Failure records omit `error_type` when an exception on the future caused the
  failure (D-I) [src/core/live_broker_state.py:278-298, 445-457]
- [x] [Review][Patch] A cancelled future is labelled "the adapter's own timeout", although any
  awaiter's cancellation produces one. The module docstring's "never creates … anything"
  overstates: `get_positions` creates the request, and resolution tasks can outlive a failed read
  [src/core/live_broker_state.py:1-60, 281-285]
- [x] [Review][Patch] `retrieved_at` is checked with `tzinfo is None` rather than
  `utcoffset() is None`, and "at one time" overclaims [src/models/broker_state.py:96-126]
- [x] [Review][Patch] Missing tests, and tests that cannot fail:
  - the adapter-`TimeoutError` branch (an AC #4 case);
  - an F2-faithful pending-forever stub;
  - the two `_observe_request` branches;
  - "never writes adapter state", which swallows drift and has no `_eclient` spy;
  - the vacuous probe assertion;
  - the untested probe `RESULT` suffix;
  - the `inf` cash case.

  [tests/unit/core/test_live_broker_state.py;
  tests/component/scripts/test_live_node_probe_broker_state.py]
- [x] [Review][Patch] AC #2's purity proofs check a forbidden list rather than "stdlib-only". The
  subprocess check omits `src.db`/`src.services`, and neither new AST scan has a non-vacuity twin
  [tests/unit/models/test_broker_state.py:168-193; tests/unit/core/test_live_broker_state.py:738-756]
- [x] [Review][Patch] P15 times the read with the probe's own clock, not the reader's `elapsed_ms`
  [docs/qa/phase3-live-verification.md]
- [x] [Review][Patch] Story record: the red-first claim is unrecorded and overstated, because the
  component suites were written after the reader. AC #3's mutation was never seen turning the
  AC #3 tests themselves red; `-x` stopped on an earlier test
  [_bmad-output/implementation-artifacts/4-1-read-the-brokers-authoritative-view-of-positions-and-cash.md]
- [x] [Review][Defer] A cancelled awaiter strands a registered `OpenPositions` request with a
  cancelled future, so every later read fails immediately until restart
  [src/core/live_broker_state.py:281-285] — deferred, pre-existing adapter defect
- [x] [Review][Defer] Multi-currency cash returns as soon as any one currency has `TotalCashValue`
  [src/core/live_broker_state.py:410-420] — deferred, pre-existing: this is only reachable if
  `$LEDGER` is requested (F7)

## Dev Notes

### The current surface — exact extension points

- `LiveSessionRunner.run()` phase calls are at `src/core/live_session_runner.py:382-392`, and
  `_phase_reconcile` at `:576-581`. **Do not edit either** (D-H). Story 4.2 will call
  `read_broker_state(self._node, log=self._log)` from inside that phase. The seam shape it
  expects is a coroutine run with `loop.run_until_complete`, the `_phase_gate_account`
  precedent at `:565-574`.
- Account masking: `src/core/live_gate.py:117` (`mask_account`, a whole-value masker, per token)
  and `src/core/live_account_gate.py:97-106` (`masked_accounts`).
- The fail-closed private-flag read shape is `src/core/live_connection_probe.py:30-69`.
- The D3 marker protocol is `src/core/exit_outcome.py`, scanned by
  `tests/unit/core/test_exit_outcome_markers.py`. `classify_failure` / `failure_message` live in
  `src/core/live_check.py`.
- The Decimal-at-the-boundary precedent is `src/core/live_trade_recorder.py`'s
  `to_price_decimal` (rejects NaN; `Decimal(str(value))`).
- Probe shape: `scripts/diagnostics/live_node_probe.py:156-257` (`_verify_account`, `_run`, and
  the `RESULT` suffix discipline).

### Scope boundaries — do NOT build

- No comparison, no discrepancy logic, and no `reconcile.ok` / `reconcile.discrepancy` (4.2/4.3/4.6).
- No runner edit, no CLI command, no DB/migration/port change (D-H).
- No `reqAccountSummary` re-request, no `$LEDGER`, no multi-currency fan-out (D-J, F7).
- No retry on a failed read. A failure is reported; the caller decides (AR43's spirit).
- No edit to the IB adapter, `live_node_builder.py`, `live_connection_probe.py` or
  `live_account_gate.py`.
- No change to `flatten_position.py`: the harness charter forbids it, and its cache read is a
  diagnostic's business.

### Hazards (each has bitten a previous story, or will)

- **A test that cannot fail.** An empty-account test that asserts only "no exception" passes
  under M1. It must assert the returned value is a `BrokerState` with `positions == ()` **and**
  that the timeout twin raises. Every new AST scan gets a non-vacuity twin (the repo rule since
  Story 2.3).
- **Cancelling the shared request (D-C).** Measured in Task 1.2. Test it with a second awaiter.
- **Masking free text.** `mask_account` is whole-value. Never pass it an exception message; log
  `error_type` only (`live_strategy_guard.redact_accounts` docstring, `:140-160`).
- **Float to Decimal.** `Decimal(float)` gives binary noise. Use `Decimal(str(value))` and check
  `is_finite()`.
- **The component tier must not claim C logging.** Construct no `TradingNode`,
  `LiveExecutionEngine` or `Logger`. The `_assert_c_logging_state_is_unchanged` fixture exists to
  catch it.
- **Guard-list rot.** The two hand lists are edited in the creating change (CLAUDE.md
  Anti-Patterns).
- **Parallel Epic 4 stories** (4.2 especially, which will consume this) may edit
  `test_session_runner_phases.py`, `test_live_node_never_exits.py`, `deferred-work.md` and
  `phase3-live-verification.md` concurrently. Append rather than reflow, and keep the footprint
  in shared files minimal.

### Testing standards summary

- TDD, red first and recorded.
- **Unit tier:** the domain types and the duck-typed reader, with stubs and a real
  `asyncio.Future`.
- **Component tier:** the real adapter methods on a socket-less harness, plus canaries.
- **Integration:** no new file. Run `tests/integration/core/` for the import allowlist.
- No test touches a broker (NFR32). Live evidence is P15 (NFR33), informational.
- Every mutation in Task 9 is run and its result recorded.

### Project Structure Notes

- New:
  - `src/models/broker_state.py`
  - `src/core/live_broker_state.py`
  - `tests/unit/models/test_broker_state.py`
  - `tests/unit/core/test_live_broker_state.py`
  - `tests/component/core/test_live_broker_state_adapter.py`

  The split between a unit and a component suite follows the `test_live_order_rejections.py` +
  `_engine.py` convention.
- Modified:
  - `scripts/diagnostics/live_node_probe.py`
  - the two guard-list test files, plus a comment in `test_live_stop_path_is_inert.py`
  - `docs/agent/nautilus.md`
  - `docs/qa/phase3-live-verification.md`
  - `deferred-work.md`
  - `sprint-status.yaml`: the `4-1-…` key only
- Architecture's delta tree names `src/services/reconciliation_service.py` for "positions/cash vs
  IBKR". That service is the **comparison** (4.6). The broker **read** must sit on the core side,
  because services never import Nautilus (AR38). This story's value types in `src/models/` are
  what that service will consume.

### References

- Epic/story: `_bmad-output/planning-artifacts/epics.md:1481-1510`. Scope extract:
  `_bmad-output/planning-artifacts/prd-epic4-scope.md` (FR32, NFR5, NFR20, NFR26, NFR32/33,
  AR34, AR38, AR41).
- Architecture: `_bmad-output/planning-artifacts/architecture.md:239-246` (D2: Redis is a cache,
  IBKR is truth), `:400-407` (AR38/AR39), `:435-440` (events), `:696-700` (G3: reconcile on `+1`).
- Pre-work: `_bmad-output/implementation-artifacts/epic4-prework-spec.md:331-336` (4.6's future
  `DISCREPANCY` outcome; this story adds no outcome).
- Sibling: `_bmad-output/implementation-artifacts/4-4-warm-indicators-from-history-before-the-live-stream-starts.md`
  covers the guard-list, size-budget and live-procedure conventions this story follows.
- Wheel: the Measured facts table above.

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), in the Epic 4 harness worktree
`harness/s4-1-20260922-205044-1`.

### Debug Log References

- **Task 0.**
  - Head `11cba25`. The tree was clean apart from this story file and the `4-1` key.
  - `uv run ruff check .` and `make typecheck` were clean before the first edit.
  - `uv run alembic current` was **denied by tool permission**. `alembic/**` is zero-diff and no
    migration was written.
  - Baselines:
    - unit: **2721 passed**, 0 failed. The two `custom/` size-cap failures Story 4.4 recorded do not
      reproduce; `6647104` excluded the submodule from the walk.
    - component: **1606 passed / 16 skipped**, measured alone.
  - A first component run **concurrently with the unit suite** showed 12 failures, all
    timing-sensitive runner/warm-up tests starved of CPU. Re-run alone, 0 failed. So the two suites
    are always run sequentially here.
  - Gateway: the sandbox refused every port-probe command (`nc`, `lsof`), so reachability could not
    be measured. It was 20:52 ET Tuesday.
- **Task 1 probes** (fresh interpreters, `/tmp/p41/`, not committed):
  - `probe_positions.py`, the real `get_positions` / `_await_request` / `_end_request` / `Requests` /
    `process_position*` on a harness object:
    - empty account → `get_positions` returns `None` and the held future's result is `[]` (F1
      confirmed);
    - disconnect → the future carries `ConnectionError('Socket disconnected.')`;
    - a second row for the same `conId` during a pending request is appended (F4 confirmed);
    - `asyncio.wait_for(task)` **cancels** the shared future and the joiner raises
      `CancelledError`; `asyncio.wait` leaves it pending and the joiner gets the answer (D-C
      confirmed).
    - **Correction to F2 / D-B:** the adapter's *own* 30 s timeout does **not** leave a
      `TimeoutError` on the future. Its `wait_for(request.future, 30)` **cancels** the shared future,
      so `_end_request(success=False, exception=e)` finds it already done and sets nothing. Every
      joiner of a timed-out request gets `CancelledError`. The reader classifies a **cancelled**
      future as `POSITIONS_UNANSWERED`. It also still maps a `TimeoutError` exception to the same
      reason, for robustness.
  - `probe_summary.py`, the real `_on_account_summary`: `_account_summary_loaded` is set after the
    *first* tag (`AccountType`, currency `""`). With NetLiquidation 100,400 and maintenance margin
    60,000, the generated `AccountBalance.total` is **400000.00 USD** while `TotalCashValue` is
    100000.52 (F5 confirmed in running code).
  - `probe_real_stack.py`: the reader end-to-end against a real `InteractiveBrokersClient`,
    `InteractiveBrokersExecutionClient`, `InteractiveBrokersInstrumentProvider` and
    `ExecutionEngine` (`_clients` keyed by the real `ClientId`). Only `_eclient` was stood in. It read
    positions and cash, returned a flat state, and left `is_logging_initialized()` False → the
    component-tier harness shape.
- **Task 9 mutation sweep** (`/tmp/p41/mutate.py`, each mutation applied, its target tests run,
  the file restored): **16 of 16 KILLED.**
  - M1a / M1b: trusting `get_positions`' return value (`None` → failed / `None` → flat).
  - M2: `wait_for` on the adapter task.
  - M3: no account filter.
  - M4: first row wins.
  - M5: no zero skip.
  - M6: unresolved rows dropped.
  - M7: cash from `NetLiquidation`.
  - M8: zero cash when absent.
  - M9: no pre-check.
  - M10: no cash deadline.
  - M11: raw account in the success record.
  - M12: drift marked exit 4.
  - M13: the probe loses its `broker_state_unavailable` reason.
  - M14: no positions deadline.
  - M15: a cancelled future read as flat.

  M1b, M10 and M14 were killed by a **hang** (the runner's 90 s timeout): with the deadline removed,
  the target tests block rather than fail. Acceptable, because those mutations remove the bound
  itself. M2 was re-run against the no-cancel tests alone, and **both** the unit and the component
  one fail.
- **Guard-list proof by mutation.** Adding `import inspect` to `live_broker_state.py` turned
  `tests/integration/core/test_epic1_ac_node.py` red (`the live path imports an undeclared …
  'inspect'`). So `_STDLIB_AND_FIRST_PARTY` does scan the new module, and it needed no edit.
  Reverted; 8/8 green.

### Completion Notes List

- **What shipped, as designed in D-A..D-K.**
  - `src/models/broker_state.py`: stdlib-only frozen `BrokerPosition` / `CashBalance` /
    `BrokerState`. The invariants make a half-failed read unrepresentable: no cash, a raw account,
    a zero-quantity row, or a float quantity/price.
  - `src/core/live_broker_state.py`: duck-typed, no Nautilus import.
    - `read_broker_state(node)` issues or joins `OpenPositions` through the adapter's own
      `get_positions`, and classifies IBKR's answer from the request **future**, read-only.
    - It resolves each contract through the exec client's instrument provider and keeps an
      unresolvable one as `IB-CONID-<conId>`.
    - It reads `TotalCashValue` from the exec client's in-memory `_account_summary`, with a bounded
      wait.
    - It either returns a `BrokerState` or raises `BrokerStateUnavailableError` (exit 4) /
      `BrokerStateAdapterError` (exit 1), both carrying the D3 markers.
  - There is still no production caller, by design (D-H). Story 4.2 wires `_phase_reconcile` and
    Story 4.6 the CLI. The operator surface is `live_node_probe.py --read-broker-state`.
- **Refinements made while building, each disclosed rather than silent:**
  1. **The future is awaited, not the task.** The reader waits with
     `asyncio.wait({request.future}, timeout=…)`, so an answer that lands at the deadline is still
     classified from the future. The task is detached, and a done-callback consumes its outcome.
  2. **A cancelled future means "unanswered".** This is the Task 1 correction to F2/D-B above.
  3. **The success record's fields changed from D-I.** `reconcile.broker_state_retrieved` carries
     `position_count` and `positions={instrument_id: quantity}` rather than D-I's `positions`
     (count) plus `instruments` (ids), because the quantities are the evidence Story 4.2 and P15
     read.
  4. **An unreadable or missing readiness flag reads as `NOT_CONNECTED`** (D-K), following
     `live_connection_probe`'s fail-closed precedent. A rename is caught by name by the component
     canary, not at runtime.
  5. **Every record goes through a helper that never raises** (`_emit`). This is the Epic 3 retro's
     `log.error`-inside-`except` lesson, applied up front. A raising sink can neither replace the
     typed failure nor turn a good read into an exception, and a test pins it.
  6. **Timing tests use real small timeouts, not a fake clock.** Task 3.1 had said "injected
     `monotonic`". The loop's own waiting cannot be faked cheaply, and each test stays under 0.7 s.
     `monotonic` / `utc_now` remain injectable.
- **AC evidence:**
  - **AC #1:** `TestTheBrokerIsRead` (unit, 17 cases) and
    `test_positions_and_cash_round_trip_for_the_configured_account` (real adapter stack).
  - **AC #2:** `tests/unit/models/test_broker_state.py` (AST + subprocess purity, invariants) and
    `TestNothingPastTheBoundaryIsAnAdapterType` (recursive leaf-type walk, no raw account in any
    record).
  - **AC #3:** `TestFlatIsDistinguishableFromFailed` (unit) and
    `test_a_flat_account_is_a_success_although_get_positions_says_none` (real adapter, with the F1
    canary).
  - **AC #4:** `TestAFailureIsNeverFlat`, 11 parametrized failure shapes, each raising its reason
    with exactly one `reconcile.broker_state_failed`. Exit codes are 4 and 1 through the real
    `classify_failure`. `test_the_reader_never_writes_adapter_state` also sits here. The real-adapter
    twins are connection lost, a joined request that the adapter itself times out, a socket down,
    and cash never pushed.
  - **AC #5:** `TestTheReadIsBounded`:
    - default `20.0 < 30.0`;
    - a silent broker, slow resolution and absent cash each end within 0.7 s at a 0.2 s deadline;
    - no-cancel;
    - an in-flight request is joined;
    - a late outcome is consumed.

    The real-adapter no-cancel proof shows the real `generate_position_status_reports`, joined to
    the same request, still getting IBKR's answer after the reader gave up. Live timing is P15's
    criterion 4.
- **Guard lists (CLAUDE.md Anti-Patterns), all in this change:**
  - `src/core/live_broker_state.py` was added to `NODE_FACING_MODULES` and
    `TestImportPurity.MODULES`, each with a reason.
  - It is deliberately **not** on `STOP_PATH_MODULES`; the comment added there gives the startup /
    on-demand reason.
  - `_STDLIB_AND_FIRST_PARTY` needed no edit, proven by mutation above.
  - `LIVE_MODULE_GLOBS` picked the module up, and the dependency, no-retry and D3 marker scans are
    green. `UNMARKED` did not grow.
- **Sizes, measured with the guard's own `measure_module`:**
  - `live_broker_state.py`: **263** executable statements. The largest class is 7 statements, and
    the largest function is `read_broker_state` at 29.
  - `broker_state.py`: **60**, with `BrokerState` at 28 statements.

  Both are well under every cap, and no baseline entry was added.
- **Red-first, stated exactly (corrected at code review; the first version overstated it).**
  - Red before the code existed, observed in-session, **but not recorded in the Debug Log at the
    time**:
    - `tests/unit/models/test_broker_state.py` failed to collect with `ModuleNotFoundError: No
      module named 'src.models.broker_state'` before Task 2.2;
    - `tests/unit/core/test_live_broker_state.py` failed the same way before Task 3.2.

    A collection error proves the file was written first, not that any single test in it can
    fail.
  - **Not red-first:**
    - `tests/component/core/test_live_broker_state_adapter.py` was written after the reader, as a
      proof against the real adapter;
    - `tests/component/scripts/test_live_node_probe_broker_state.py` was written after the probe
      flag.
  - What does show every test can fail is the mutation sweep:
    - 16/16 at dev time;
    - 37/37 after the review fixes, including R1–R21 for the fixes themselves;
    - AC #3's own tests seen red under M1a/M1b (see "Code review fixes" below).
- **Live verification.** Procedure P15 is written into `docs/qa/phase3-live-verification.md` and
  recorded as **defined, not run**. The probe was attempted, and it stopped at
  `RESULT: fail reason=config_error … TWS_ACCOUNT is not set` before opening any socket. The harness
  worktree has no `.env`, and creating or reading one is off-limits. It is informational only, per
  the charter. Nothing was submitted. `--real-money`, `.env`, docker and the
  kill/reconnect/connection-loss scripts were not touched.
- **Routed, not fixed** (`deferred-work.md`, "Deferred from: story-4.1"):
  - → 4.2: Nautilus's reconciliation cannot fail on a failed position read.
  - → 4.2: the adapter's own timeout poisons every joiner with `CancelledError`.
  - → 4.3: a mid-session read diverts streaming position updates, and cash freshness mid-session is
    owned there.
  - → 4.6: compare quantities exactly, treat price as informational, and size the timeout.
  - → 5.1: never read IB `AccountState` balances as cash or equity.
  - → P15's owner: the probe run itself.
  - Story 2.4's Redis-disposability entry gained a note.
- **Gates at dev time:**
  - `ruff format` / `ruff check` / `make typecheck` clean.
  - unit **2804 passed** (+83).
  - component **1625 passed / 16 skipped** (+19).
  - integration `tests/integration/core/` `--forked` **92 passed / 2 skipped**; the full
    `tests/integration` tier was **318 passed / 2 skipped** (unchanged from Story 4.4's close).
  - `is_live` in `src/core/strategies/` = **0**.
  - `git diff --stat` over the D-H zero-diff set (`live_session_runner.py`, `src/db`, `alembic`,
    `src/services`, `src/api`, `templates`, `src/cli`) is **empty**.
  - README validated: its "`reconcile` is an explicit no-op placeholder until Epic 4" is still true
    until Story 4.2, and the probe is not documented there, so no edit was needed.
- **Code review fixes (2026-09-22). All 22 patches applied under the PO's option 0 (batch-apply).**
  - **Drift versus outage is now exact.**
    - These raise `BrokerStateAdapterError` (exit 1):
      - a *missing* readiness flag;
      - a node with no IB exec client;
      - a read on the wrong event loop;
      - `get_positions` raising something other than `OSError`;
      - an unset quantity;
      - a row without a `conId`;
      - two contracts on one instrument, whose detail names both.
    - A *present but unset* flag, or an exec client whose `_connect` has not finished, is
      `NOT_CONNECTED` (exit 4), refused before anything is requested.
    - This reverses dev-time refinement 4 (an unreadable flag read as `NOT_CONNECTED`): the review
      found it contradicted `BrokerStateAdapterError`'s own rationale.
  - **NFR26 and diagnosis.** Drift is raised `from None`, so the adapter exception, whose text may
    hold the raw account, is dropped. The detail names the exception type and its raise site
    (`file:line function`). The probe prints `[probe] broker state failed: <detail>` before its
    `RESULT` line.
  - **Failure records.** `BrokerStateUnavailableError` carries `error_type`, and
    `reconcile.broker_state_failed` records it for every exception-caused failure, including one
    set on the future (D-I as drafted).
  - **ibapi's sentinels.** `UNSET_DOUBLE` (`sys.float_info.max`) is treated as absent for average
    cost and cash. `UNSET_DECIMAL` (`2**127-1`) is refused as a quantity. Both are pinned to
    `ibapi.const` by a component canary.
  - **Positions path.**
    - Joining the race: an in-flight `OpenPositions` request is captured *before* the task starts
      (mutation R11 proves the race).
    - `get_positions` raising after it registered (a send on a dead socket) fails fast as
      `CONNECTION_LOST` instead of costing the deadline; the wait watches both the future and the
      task.
    - `get_instrument` returning `None` leaves one unresolved row, as D-F intends.
  - **Deadline.**
    - `timeout_seconds` outside `(0, 30]` (including NaN and inf) is a `ValueError`, refused before
      anything is requested.
    - The deadline now runs on the loop's own clock, the clock `asyncio.wait` uses, and the
      injectable `monotonic` seam is gone. Nothing injected it, and a frozen fake could hang the
      cash wait.
  - **Model.**
    - A masked account must be `***` plus at most three characters, and a new test pins the
      duplicated `MASK_PREFIX` to `mask_account`'s real output.
    - Currencies must be three ASCII upper-case letters, via a new `is_currency_code`, so `BASE` and
      malformed keys are skipped by the reader rather than failing it.
    - `retrieved_at` uses `utcoffset() is None`.
  - **Wording.**
    - A cancelled future now reads as "the adapter's own timeout, or another awaiter giving up".
    - The module docstring no longer says the reader "never creates" anything: `get_positions`
      creates the request, and a slow resolution may still load an instrument after a failed read.
    - `_emit` resolves `log.<level>` inside its `try`.
  - **Tests.**
    - New cases:
      - the adapter-`TimeoutError` branch;
      - an F2-faithful stub that removes the request and leaves the future pending;
      - a request never registered;
      - registration later than the deadline;
      - send failures;
      - `inf` and unset cash;
      - wrong loop;
      - a timeout outside NFR5;
      - an unconnected exec client (unit and real adapter);
      - a logger whose attribute lookup raises.
    - "Never writes adapter state" no longer swallows drift, now has an `_eclient` spy, and asserts
      the shared future is untouched.
    - The probe's `RESULT` suffix is tested through the real `_run`, and a vacuous assertion was
      removed.
    - AC #2's purity scans now check *stdlib-only* (`sys.stdlib_module_names`). Both new AST scans
      gained non-vacuity twins, and the subprocess check covers `src.db` and `src.services`.
    - P15 criterion 4 now reads the reader's `elapsed_ms`, and the probe's clock is secondary.
  - **Mutation sweep re-run:** `/tmp/p41/mutate2.py`, the original 15 re-anchored plus R1–R21, one
    per fix. The first run killed **36/37**. R8 (drop the shared-instrument check) **survived**: the
    model's own `ValueError` still produced drift, so the test never checked the fix's point, that
    the instrument is *named*. A new `test_two_contracts_on_one_instrument_are_named_not_anonymous`
    kills it, so the total is **37/37**.
  - **AC #3's mutation, aimed at the AC #3 tests themselves** (auditor finding): under M1a both
    `test_a_flat_account_is_a_successful_empty_state` and the real-adapter
    `test_a_flat_account_is_a_success_although_get_positions_says_none` fail. Under M1b,
    `test_the_same_none_from_a_timeout_raises_instead` and the real-adapter joined-timeout test
    fail.
  - **Sizes after the fixes:**
    - `live_broker_state.py`: **338** executable statements. The largest class is 8 statements, and
      the largest function is `read_broker_state` at 35.
    - `broker_state.py`: **64**.

    Both are under every cap.
- **Gates after the fixes:**
  - `ruff format` / `ruff check` / `make typecheck` clean.
  - unit **2851 passed** (+130 over the 2721 baseline).
  - component **1629 passed / 16 skipped** (+23).
  - full integration `--forked` **318 passed / 2 skipped** (unchanged).
  - `is_live` = **0**.
  - The D-H zero-diff set is still **empty**.

### File List

New:

- `src/models/broker_state.py`
- `src/core/live_broker_state.py`
- `tests/unit/models/test_broker_state.py`
- `tests/unit/core/test_live_broker_state.py`
- `tests/component/core/test_live_broker_state_adapter.py`
- `tests/component/scripts/test_live_node_probe_broker_state.py`
- `_bmad-output/implementation-artifacts/4-1-read-the-brokers-authoritative-view-of-positions-and-cash.md`

Modified:

- `scripts/diagnostics/live_node_probe.py` (`--read-broker-state`, opt-in)
- `docs/agent/nautilus.md` ("Reading Broker State" section)
- `docs/qa/phase3-live-verification.md` (Procedure P15)
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (the `4-1-…` key only)
- `tests/unit/core/test_live_node_never_exits.py` (`NODE_FACING_MODULES`)
- `tests/component/core/test_session_runner_phases.py` (`TestImportPurity.MODULES`)
- `tests/unit/core/test_live_stop_path_is_inert.py` (non-membership comment)

## Change Log

- 2026-09-22 — Story drafted (create-story): ready-for-dev. Designed around F1–F3: no existing
  API can tell a flat account from a failed read.
- 2026-09-22 — Implemented (dev-story): all 11 tasks done.
  - Task 1 corrected F2: the adapter's own timeout *cancels* the shared request future.
  - 16/16 mutations killed.
  - Test counts: unit 2804 (+83), component 1625/16sk (+19), integration core 92/2sk.
  - P15 is defined, not run (no `.env` in the worktree).
  - Status → review.
- 2026-09-22 — Code review (three parallel layers; 54 raw findings → 0 decisions, 22 patches,
  2 deferred, 8 dismissed). The PO chose option 0 (batch-apply), and all 22 patches are applied.
  - Mutation sweep: 37/37 killed after one test was tightened.
  - Test counts: unit 2851, component 1629/16sk, integration 318/2sk.
  - Status → done.
