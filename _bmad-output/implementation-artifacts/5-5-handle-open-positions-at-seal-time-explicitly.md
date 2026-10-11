# Story 5.5: Handle Open Positions at Seal Time Explicitly

Status: done

<!-- Drafted 2026-10-11 by bmad-create-story against head d57262c (clean), branch
harness/s5-5-20261011-093755-2. Three PO rulings (R1–R3) are drafted with recommendations and
must be ruled before Task 1 — they are recorded in Task 0. -->

## Story

As the operator,
I want sealing with open positions to be a decision I make, never something that happens quietly,
so that I am never surprised by orphaned positions or by exits my strategy did not request.

## Acceptance Criteria

Verbatim from `_bmad-output/planning-artifacts/prd-epic5-scope.md` (Story 5.5). Test markers use
`@pytest.mark.acceptance_criterion("5.5a" … "5.5e", …, SOURCE)`, the 5.3 convention.

1. **(5.5a)** **Given** a stopped session with open positions **When** `live seal` is run without
   `--force` **Then** it refuses, listing each open position with its instrument and quantity
   (FR47, AR30).
2. **(5.5b)** **Given** the same session **When** `live seal --force` is run **Then** it seals, and
   each open position is persisted as an **open trade with a null exit** — the same shape backtests
   already use for positions open at the end of data (FR47, AR30).
3. **(5.5c)** **Given** a `--force` seal **When** the report is printed **Then** it states the
   disposition explicitly: the positions remain at the broker and are no longer owned by any
   session (FR47, AR30).
4. **(5.5d)** **Given** any seal path **When** the code is inspected **Then** nothing flattens,
   closes, or modifies a position — sealing never manufactures an exit (AR31, NFR14, AR43).
5. **(5.5e)** **Given** a stopped session with no open positions **When** `live seal` is run
   **Then** it seals without requiring `--force`.

## What already exists — read before writing anything

Story 5.3 (commit 73127d7) built the seal. 5.5 **extends it**; it does not build a second path.

| # | Fact | Where |
|---|---|---|
| F1 | `seal_session(db, identifier, *, starting_capital, read_open_positions, clock, new_run_id)` is one transaction: capital check → `_claim` (lock, `transition(→SEALED)`, `record_sealed_run`) → `read_open_positions(session_id)` → `_refuse_unless_flat` → `find_session_trades` → `_reconstruct` → linked-backtest warning → `_write_run` (verbatim `save_backtest_results`, then `run_type='paper'`) → `_backfill` (count must equal the rows read). It never commits; the CLI commits only on return. | `src/services/session_seal_service.py:411-477` |
| F2 | `_refuse_unless_flat` is 5.3's R2a: any `view.positions` → `SealRefusedError` naming `instrument_id quantity` for each; `positions_skipped > 0` → refusal ("another account"). **This is the function 5.5's `--force` replaces** (deferred-work, story-5.3 (e)). | `session_seal_service.py:324-337` |
| F3 | `SealReport.open_positions: tuple[ViewPosition, ...]` and `open_positions_disposition: str = NONE_OPEN_DISPOSITION` ("none open at seal") already reach both report forms. The human form is rendered **from** the JSON payload (`render_seal_report` → `_render(seal_json_payload(...))`), so a new payload key is in both forms automatically (AR29). | `session_seal_service.py:76-114, 493-543` |
| F4 | The reader the CLI injects: `read_open_positions(session_id) -> SessionView`, which calls `read_session_view(session_id, settings.ibkr.tws_account, settings.redis)` and raises `SealRefusedError` itself when `TWS_ACCOUNT` is unset. | `src/cli/commands/live_seal.py:70-83` |
| F5 | `read_session_view` (Story 4.6) is load-only: it enumerates `positions:*` keys, rebuilds each with `load_position` (a real Nautilus `Position`), keeps open ones on the configured account, **nets them per instrument** and drops a zero net (`_net_positions`). It raises `SessionViewUnavailableError(reason=NO_ENGINE_STATE \| UNREADABLE \| ADAPTER_INCOMPATIBLE)` — never "flat" — and `RedisUnreachableError` before opening the adapter. `LOAD_METHODS = ("keys", "load_position", "load", "load_account")` is exact-pinned, mutator-free (`tests/component/core/test_live_session_view.py:860-869`) and proven load-only against a real Redis under `MONITOR` (`tests/integration/core/test_live_session_view_redis.py`). | `src/core/live_session_view.py:104-109, 217-281, 394-427` |
| F6 | **The net loses what a null-exit row needs.** A row needs entry side, entry price, entry time, the opening order id and commission per *position*; `ViewPosition` carries only `(instrument_id, net quantity)`. The rebuilt `Position` has every field: `id`, `instrument_id`, `strategy_id`, `account_id`, `entry`, `quantity`, `peak_qty`, `signed_decimal_qty()`, `avg_px_open`, `ts_opened`, `opening_order_id`, `realized_pnl`, `commissions()`, `settlement_currency` (checked on nautilus_trader 1.220.0). | `live_session_view.py:383-391`; measured |
| F7 | **The backtest's open-row shape** (`save_trades_from_positions`, an open position is `ts_closed` NaN): `trade_id = str(position_id)`, `venue_order_id = opening_order_id`, `client_order_id = closing_order_id` → **`None`**, `order_side = entry` (`BUY`/`SELL`), `quantity = peak_qty`, `entry_price = avg_px_open` (8 dp), **`exit_price = None`, `exit_timestamp = None`, `profit_pct = None`**, `profit_loss = realized_pnl` (an open leg's is minus its commission), `commission = commissions[0]`, `fees_amount = 0.00`, `entry_timestamp = ts_opened`. Validated through `TradeCreate` first. | `src/services/backtest_persistence.py:431-547` |
| F8 | The live recorder reproduces that mapping for closed legs with three pure helpers to reuse, not copy: `to_price_decimal` (Decimal(str(x)), 8 dp, NaN refused), `select_commission(position.commissions(), position.settlement_currency.code)` (settlement-currency pick), `unix_nanos_to_utc`. `RECONCILIATION_STRATEGY_IDS = {"EXTERNAL", "INTERNAL-DIFF"}`: Story 3.6 D-D never persists a reconciliation-owned *closed* leg, because rows carry no `strategy_id` and it would join the strategy's comparison sample. | `src/core/live_trade_recorder.py:144-213, 250-303` |
| F9 | **An open row joins no metric.** 5.2's `_legs_from` keeps only rows with an `exit_timestamp` (an open row must also have no `exit_price`, else it refuses) — and `equity_series.excludes` already names `open_positions`. `trade_analytics` filters `exit_timestamp IS NOT NULL` too. | `src/core/session_equity.py:169-191`; `src/services/trade_analytics.py:56, 79, 160` |
| F10 | The write is already a capability: `insert_trade_if_absent(trade)` on both `TradingSessionRepository` twins (`EXPECTED_CAPABILITIES` exact pin — **no new repository method is needed or allowed**). Its conflict key `(session_id, trade_id, client_order_id)` never conflicts on a `NULL` `client_order_id` (Postgres NULLs are distinct), so an open row always inserts. `trade_counts_by_session` already counts `exit_timestamp IS NULL` rows as `open_positions` — 0 for every session until this story. | `src/db/repositories/trading_session_repository_sync.py:150-196, 250-279` |
| F11 | `trades` CHECKs: `quantity > 0`, `entry_price > 0`, `exit_price IS NULL OR exit_price > 0`, `backtest_run_id IS NOT NULL OR session_id IS NOT NULL`. | `src/db/models/trade.py:147-168` |
| F12 | Operator-facing text bans the AR36 stems `close`/`kill`/`halt`/`pause`/`finalize` (`_ar36_violations`, carve-out for `closed_trade`), and the existing refusal test pins that the refusal never says "broker" or "flatten" (`test_the_refusal_names_only_the_remedy_that_works`). Disposition wording must say "no exit", never "closed". | `tests/unit/cli/commands/test_live_seal_cli.py:377-387`; `tests/unit/services/test_session_seal_service.py:416-430` |
| F13 | The AR31 scan already exists: `TestTheSealReachesNoBroker` walks the seal service and CLI for `FORBIDDEN_ORDER_METHODS` (`close_all_positions`, `close_position`, `cancel_all_orders`, `cancel_order`, `submit_order`, `submit_order_list`) and any identifier containing `flatten`. `src/core/live_session_view.py` is covered by the globbed `LIVE_MODULE_GLOBS` scans. | `tests/unit/services/test_session_seal_service.py:873-895`; `tests/unit/core/test_live_stop_path_is_inert.py:138-160` |
| F14 | Statement budgets (measured, `measure_module`): `session_seal_service.py` 326/500, `seal_session` 35/50, `SealReport` 17/100; `live_seal.py` 131/500; `live_session_view.py` 238/500, `read_session_view` 34/50, `_net_positions` 28/50; `src/models/reconciliation.py` 116/500. | `tests/unit/governance/test_size_caps.py` |
| F15 | `CacheDatabaseAdapter` has `load_order(client_order_id)` (read-only); `Order` has `is_open`, `is_inflight`, `side`, `leaves_qty`, `order_type`, `instrument_id`, `status`, `client_order_id`. P21's live run saw a resting order persist in the session's Redis (`index:orders_open`) across a Gateway restart. | measured; `docs/qa/phase3-live-verification.md:2653` |

### Debt routed here — each must be dispositioned in the Dev Agent Record

From `deferred-work.md` (story-5.1 code review; story-5.2 (f); story-5.3 (e), its code review, DN1a,
DN2a):

1. **story-5.3 (e)** — where `--force` plugs in: replace the refusal in `_refuse_unless_flat`,
   persist the positions as null-exit trades, set the disposition. → **this story's core.**
2. **DN2a** — a session with no engine state can never be sealed; "5.5's `--force` is the way
   out". → **R2.**
3. **DN1a** — a resting order at IBKR is not checked at seal time. Owner "Story 5.5 (open
   positions — and orders — at seal)". → **R3.**
4. **5.3 code review** — an open strategy leg netted to zero by an opposite `EXTERNAL` leg reads as
   flat; owner 5.5 "if it reads per-position legs". → **D-C** (kept: nothing remains at the broker).
5. **story-5.1 (two items) + story-5.2 (f)** — open legs and the metrics: a backtest that ends
   mid-position keeps its open leg in Sharpe/volatility/Sortino (filed at the epoch) and scores its
   entry commission as a loser; a paper record leaves open rows out. Owner "5.5 to surface it, then
   an epic-level both-sides decision". → **D-E** (surfaced in the report; no metric change).

## PO rulings — RULED 2026-10-11: R1a · R2a · R3b (all recommended)

Each is a decision that shapes the record. Recommendations are marked; the proofs in the tasks are
written for the recommended option, and a different ruling changes the named tasks only.

### R1 — Which open positions become null-exit rows, and at what quantity

The view nets per instrument (F5/F6); a row is per position. Under NETTING a session's cache can
hold, per instrument, the strategy's own position **and** a reconciliation-owned one (`EXTERNAL` /
`INTERNAL-DIFF` — e.g. a split's extra shares absorbed at startup, Story 4.7/4.5).

- **[a] (recommended)** One row per open Nautilus position on the configured account, for every
  instrument the view lists (net ≠ 0) — strategy-owned **and** reconciliation-owned alike. The rows
  of an instrument sum (signed) exactly to the quantity the report lists, so the record says what
  remains at the broker. `trade_id` is the position id, which names its owner
  (`…-EXTERNAL`). Quantity is the position's **current** open quantity, not `peak_qty`: they are
  equal unless the strategy partially reduced the position, and only the current one is what
  remains. D-D's reason for never persisting reconciliation-owned *closed* legs (they would join
  the strategy's comparison sample) does not apply: no metric reads an open row (F9). This also
  lets 5.4's `open_positions_at_seal` count the split's extra shares from the rows.
- **[b]** Strategy-owned positions only; a reconciliation-owned open position is listed in the
  report but not persisted (D-D extended to open rows). The rows then do not sum to what the
  report says remains.
- **[c]** As [a] but with `peak_qty` (the backtest's literal column), accepting that a partially
  reduced position's row overstates what remains.

### R2 — What `--force` overrides (DN2a)

5.3 refuses on held positions, on positions held for another account, and on any view failure.

- **[a] (recommended)** `--force` overrides:
  - held positions (the AC);
  - positions held for **another account** (`positions_skipped`) — reported by count, not
    persisted (rows carry no account, and the record describes the configured one);
  - **`NO_ENGINE_STATE`** (DN2a's case: the namespace is empty — the session never started, or its
    Redis was flushed): it seals with **no** open rows and the disposition says the holdings are
    **unknown**, never "none open".

  Still refused under `--force`: an unreachable Redis, an `UNREADABLE` or `ADAPTER_INCOMPATIBLE`
  cache, an unset `TWS_ACCOUNT`. Each is fixable, and a retry reads holdings a forced seal would
  otherwise drop unrecorded.
- **[b]** `--force` overrides every view failure; the disposition is "unknown" for all of them.
- **[c]** `--force` overrides held positions only; DN2a stays open (contradicts DN2a's ruling).

### R3 — Resting orders at seal (DN1a)

AC #5 (5.5e) says a session with **no open positions** seals **without** `--force`. A refusal on
a resting order would contradict it literally.

- **[a]** Keep 5.3's documented limitation ("cancel it in TWS before sealing"); no order read.
- **[b] (recommended)** Read the session's **own** orders from its engine cache, load-only
  (`load_order` joins `LOAD_METHODS` deliberately — the constant, its exact pin, the mutator check
  and the Redis `MONITOR` proof together, the Story 4.7 precedent). Every order the cache holds
  `is_open or is_inflight` is **reported** — instrument, side, remaining quantity, type, client
  order id — with its disposition ("left working at IBKR, owned by no session; cancel it in TWS").
  It **never refuses** (AC #5 holds literally) and **never cancels** (AR31). Stated limitation: the
  cache cannot see what changed at IBKR while the session was stopped, and an order the cache lost
  is invisible.
- **[c]** As [b] but refuse without `--force` (amends AC #5 to "no open positions and no working
  orders").

## Tasks / Subtasks

TDD throughout: each subtask's test is written and seen **red** before its code.

- [x] **Task 0 — Record the PO rulings R1–R3** in this file and the Dev Agent Record before code.
- [x] **Task 1 — The per-position read (R1a)** (AC: 5.5b)
  - [x] 1.1 `src/models/reconciliation.py`: a frozen `OpenLeg` (Nautilus-free, beside
        `ViewPosition`): `position_id`, `instrument_id`, `strategy_id`, `side` (`BUY`/`SELL`),
        `quantity` (signed, non-zero; sign must agree with `side`), `avg_px_open` (> 0, finite),
        `opened_at` (tz-aware), `opening_order_id`, `realized_pnl: Decimal | None`,
        `commission_amount: Decimal | None`, `commission_currency: str | None`. And a frozen
        `SessionHoldings(view: SessionView, legs: tuple[OpenLeg, ...], working_orders: tuple[...]
        = ())` whose `__post_init__` pins **legs ⇔ view**: the leg instruments equal the view's
        instruments, and each instrument's legs sum to its `ViewPosition.quantity`.
  - [x] 1.2 `src/core/live_session_view.py`: `read_session_holdings(session_id, account, redis,
        …same seams…) -> SessionHoldings`, sharing `_read` with `read_session_view` (which then
        returns `.view`, behaviour and records unchanged). `_net_positions` becomes "collect legs,
        then net"; legs of a zero-net instrument are dropped with the row (D-C). The `Position` →
        `OpenLeg` conversion reuses `to_price_decimal` / `select_commission` / `unix_nanos_to_utc`
        from `live_trade_recorder` (F8) — no second formula. A leg that cannot be converted (NaN,
        non-positive price) is `UNREADABLE`, never dropped.
  - [x] 1.3 Component tests (`tests/component/core/test_live_session_view.py`'s fake adapter):
        legs per position; strategy + `EXTERNAL` legs of one instrument both present and summing
        to the net; a zero-net pair yields no row and no leg; other-account positions are neither
        legs nor rows; `read_session_view`'s output and records are byte-identical to before.
- [x] **Task 2 — Working orders (R3b)** (DN1a)
  - [x] 2.1 `WorkingOrder` (frozen, Nautilus-free): `client_order_id`, `instrument_id`, `side`,
        `quantity` (remaining, > 0), `order_type`, `status`.
  - [x] 2.2 `read_session_holdings` enumerates `orders:*` and rebuilds each with `load_order`;
        keeps `is_open or is_inflight`; an order that cannot be rebuilt is `UNREADABLE` (fail
        closed, F4's precedent for positions).
  - [x] 2.3 `LOAD_METHODS` widens 4 → 5 (`load_order`) **in the constant, its exact pin and the
        mutator check together** (`tests/component/core/test_live_session_view.py:860-869`), and
        the Redis `MONITOR` proof (`tests/integration/core/test_live_session_view_redis.py`)
        asserts the order read stays inside the read-only command set and the namespace is
        byte-identical afterwards. Update CLAUDE.md's `LOAD_METHODS` note (three → four → five).
- [x] **Task 3 — `--force` in the seal service** (AC: 5.5a, 5.5b, 5.5c, 5.5e; R2a)
  - [x] 3.1 `seal_session(..., force: bool = False)`; `OpenPositionsReader = Callable[[UUID],
        SessionHoldings]`.
  - [x] 3.2 Without `force`, 5.3's refusals stand, and the held-positions refusal lists every
        instrument and quantity **and** names `--force` as the other way out (no "broker", no
        "flatten", no AR36 stem — F12).
  - [x] 3.3 With `force`: held and other-account positions do not refuse; `NO_ENGINE_STATE` seals
        with unknown holdings; every other reader failure still raises (R2a).
  - [x] 3.4 With `force` and legs: one `TradeCreate`-validated `Trade` per leg, inserted with
        `insert_trade_if_absent` **before** `find_session_trades` — so the rows are read, left out
        of the metrics by 5.2 (F9) and linked by the existing backfill, whose count check then
        covers them. An insert that returns `False` refuses (`SealRefusedError`).
  - [x] 3.5 The row is the backtest's open shape (F7) with D-B's paper differences only.
  - [x] 3.6 `SealReport` gains `forced: bool`, `open_trades_recorded: int`,
        `positions_skipped: int`, `working_orders`; the disposition comes from one function (D-D).
        Keep `seal_session` ≤ 50 statements: split helpers rather than grow it.
- [x] **Task 4 — `live seal --force`** (AC: 5.5a, 5.5c, 5.5e)
  - [x] 4.1 `--force` flag (default off) passed through; the reader becomes
        `read_session_holdings`. `--help` documents what `--force` does and does not override,
        the disposition, the working-order report and that nothing is ever exited or cancelled;
        the "An order still resting at IBKR is not checked" sentence is replaced (R3b).
  - [x] 4.2 `session.sealed` (logged after the commit only) gains `forced` and
        `open_trades_recorded`.
- [x] **Task 5 — AR31 / NFR14 pins** (AC: 5.5d)
  - [x] 5.1 Extend `TestTheSealReachesNoBroker` to the seal modules **and**
        `src/core/live_session_view.py`, and add `Position`/`Order` mutators (`apply`,
        and any identifier containing `flatten`) to what the seal path may never call; a
        not-vacuous sibling proves the scan sees calls in those files.
  - [x] 5.2 Pin that a forced seal calls only `insert_trade_if_absent`, `find_session_trades` and
        `backfill_trades_run_id` on the repository (the spec'd `MagicMock`'s `method_calls`).
- [x] **Task 6 — Integration (Postgres)** `tests/integration/db/test_session_seal.py` (AC: 5.5a,
      5.5b, 5.5e)
  - [x] 6.1 Forced seal of a session with closed trades + two open legs (one `EXTERNAL`): two
        null-exit rows with `session_id` set, linked to the run; `trade_counts_by_session` =
        `(closed, 2)`; the run's metrics equal those of an unforced seal of the same closed rows
        (open rows change no metric).
  - [x] 6.2 Unforced seal with legs refuses and writes nothing (no run, no rows, still `stopped`).
  - [x] 6.3 `NO_ENGINE_STATE` + `--force` seals with no open rows and the unknown disposition.
  - [x] 6.4 The CLI end to end with `--force --json`, read back from a new session.
- [x] **Task 7 — Parity pin (component)**: one real open Nautilus `Position` → (a)
      `ReportProvider.generate_positions_report([position])` → `save_trades_from_positions`
      (repository stubbed to capture rows) and (b) `OpenLeg` → the seal's row. Every column equal
      except the owner columns (`backtest_run_id`/`session_id`). Partially reduce a second
      position and pin D-B's one difference (`quantity`: current vs `peak_qty`).
- [x] **Task 8 — Docs, guard lists, debt**
  - [x] 8.1 README `live seal` rows + paragraph, `docs/agent/persistence.md` (the "what the check
        cannot see" bullets), `docs/agent/architecture.md` if a module's role line changes.
  - [x] 8.2 No new `live_*` module is planned; if one is created, CLAUDE.md's guard lists are
        re-checked in the same commit. A new stdlib import in a live-path module must be added to
        `_STDLIB_AND_FIRST_PARTY` (`tests/integration/core/test_epic1_ac_node.py`).
  - [x] 8.3 deferred-work: close items 1–5 above with their disposition; log anything new.
  - [x] 8.4 No P-number: the seal is database + local Redis only (NFR33); the 4.6 `MONITOR` proof
        covers the read.
- [x] **Task 9 — Gates**: `uv run ruff check .`, `uv run ruff format --check .`, `make typecheck`,
      unit + component, `tests/integration/db/test_session_seal.py`,
      `tests/integration/core/test_live_session_view_redis.py`, the size-cap guard.

### Review Findings

Code review 2026-10-11, three parallel layers (Blind Hunter / Edge Case Hunter / Acceptance
Auditor), none failed. 33 raw findings → 22 after dedupe: 3 decision-needed, 10 patch, 3 defer,
6 dismissed.

**PO rulings on the review (Allay, 2026-10-11): DN1a · DN2a · DN3a · patches batch-applied
(choice 0), test-first, no schema change, no check loosened.**
- **DN1a**: an unreadable order never blocks a seal. It is named in the report, and the seal
  proceeds with or without `--force`.
- **DN2a**: R2a stands as ruled. The report and `--help` tell the operator to confirm that
  `REDIS_HOST`/`REDIS_PORT` point at the session's own Redis before forcing.
- **DN3a**: the zero-entry-price case is deferred to deferred-work.

- [x] [Review][Decision] DN1 — RESOLVED DN1a (→ patch). An order the cache cannot rebuild blocks every seal, `--force` or
  not — `_working_orders` rebuilds every `orders:*` key, long-closed ones included, and any
  `load_order` failure or `None` is `UNREADABLE`. Under `force` only `NO_ENGINE_STATE` is sealed
  past (R2a). So a *flat* session with one corrupt historical order, which 5.3 could seal, is now
  refused. That contradicts R3b ("never refuses") and AC 5.5e literally. Task 2.2 ("fail closed")
  and R3b disagree, and the code followed Task 2.2. [blind+auditor+edge;
  `src/core/live_session_view.py` `_working_orders`]
- [x] [Review][Decision] DN2 — RESOLVED DN2a (→ patch: wording only). A reachable but wrong Redis reads as `NO_ENGINE_STATE`, and
  `--force` then seals it for good with "unknown" holdings — e.g. `REDIS_HOST`/`PORT` pointing
  at a dev instance. R2a refuses an *unreachable* Redis under `--force` because a retry would
  read the holdings, but a *wrong* reachable one is indistinguishable from a flushed cache.
  [blind+edge; `src/services/session_seal_service.py` `_holdings_at_seal`]
- [x] [Review][Decision] DN3 — RESOLVED DN3a (→ deferred to deferred-work). A position with a zero entry price makes every forced seal
  `UNREADABLE` — Nautilus reconciliation's MARKET fallback fill can be priced 0
  (`live/execution_engine.py:1631-1633`, IB `avgCost` 0, no quote). `OpenLeg` refuses
  `avg_px_open <= 0`, and `trades.entry_price > 0` is a CHECK. Such a session can never be
  sealed, even with `--force`; `live reconcile` reads the same cache fine. [edge;
  `src/models/reconciliation.py` `OpenLeg`]
- [x] [Review][Patch] P1 — Unknown holdings are reported as concrete zeros
  (`open_positions: []`, `positions_on_another_account: 0`, `working_orders: []`); add
  `holdings_known`. `session.sealed` should also carry it, plus the working-order and
  other-account counts, and log a warning when the holdings are unknown, so the ERROR
  `reconcile.session_view_failed` before a successful forced seal is explained
  [`src/services/session_seal_service.py` `_holdings_report`; `src/cli/commands/live_seal.py`
  `_log_sealed`]
- [x] [Review][Patch] P2 — The dispositions state broker state as fact from a cache that may be
  stale. Name the engine cache as their basis, and state R3b's limitation in `--help` and the
  README. deferred-work's "the report's wording says 'the session's engine cache'" is false today
  [`src/services/session_seal_service.py` disposition constants]
- [x] [Review][Patch] P3 — Realized P&L on a partly reduced, still-open position silently leaves
  the forced record's metrics: it lives only on the open row's `profit_loss`. Disclose it in the
  disposition, `persistence.md` and deferred-work (the metric fix is the both-sides decision)
  [`src/services/session_seal_service.py` `RECORDED_OPEN_DISPOSITION`]
- [x] [Review][Patch] P4 — The 5.5a refusal lists the net per instrument, not each open position
  (AC 5.5a literally; R1a's position is one Nautilus position). List each leg with its
  instrument, quantity and owner [`src/services/session_seal_service.py` `_refuse_unless_flat`]
- [x] [Review][Patch] P5 — `_read` binds its members from a hand-written literal instead of the
  pinned `LOAD_METHODS`, so the static member scan no longer sees them. Bind from the pinned
  constant and pin the slice [`src/core/live_session_view.py` `_read`]
- [x] [Review][Patch] P6 — The routed debt entries still read "Owner: Story 5.5" in place. Mark
  each one dispositioned, with a pointer to the story-5.5 section
  [`_bmad-output/implementation-artifacts/deferred-work.md`]
- [x] [Review][Patch] P7 — The other-account refusal recommends `--force` for what is most likely
  a wrong `TWS_ACCOUNT`; say to check `TWS_ACCOUNT` first
  [`src/services/session_seal_service.py` `_refuse_unless_flat`]
- [x] [Review][Patch] P8 — A position id or opening order id longer than `trades`' `String(100)`
  passes `TradeCreate` and fails as a raw Postgres error; refuse it by name
  [`src/services/session_seal_service.py` `open_trade`]
- [x] [Review][Patch] P9 — An open order with zero `leaves_qty` (`PARTIALLY_FILLED` after an
  `OrderUpdated`) makes `WorkingOrder` raise, so the read is `UNREADABLE`. It has nothing left to
  fill and is not working [`src/core/live_session_view.py` `_working`]
- [x] [Review][Patch] P10 — Two disposition tests take an unused `harness` and only check a
  constant; check the rendered report instead
  [`tests/unit/services/test_session_seal_service.py`]
- [x] [Review][Defer] W1 — An instrument with long and short legs records the legs gross (e.g.
  +10/−10/+3 → three rows for a net of 3); the +10/−10 pair alone records nothing (D-C).
  Deferred: a consequence of R1a + D-C as ruled; the rows still sum to the net
  [`src/core/live_session_view.py` `_read_holdings`]
- [x] [Review][Defer] W2 — A mixed-currency commission's diagnostics are dropped without a
  record. Deferred: USD-only phase (the story-5.2 code-review precedent)
  [`src/core/live_session_view.py` `_leg`]
- [x] [Review][Defer] W3 — Working orders are not filtered by account, unlike positions. Deferred:
  report-only, and the wording names the engine cache as the basis
  [`src/core/live_session_view.py` `_working`]

## Dev Notes

### Design decisions (drafted on R1a / R2a / R3b)

- **D-A — `SessionHoldings` beside `SessionView`, not a widened `SessionView`.** `live reconcile`
  builds and tests `SessionView`s with positions and no legs; an optional `legs` field would need an
  "unread" state the invariant could not tell from "flat". `read_session_view` keeps its signature
  and records; `read_session_holdings` returns the view plus what the seal needs. One read, one
  adapter session: legs and net come from the same `load_position` calls, so they cannot disagree.
  `OpenLeg`/`SessionHoldings`/`WorkingOrder` are read-side boundary values (AR38: Nautilus objects
  converted at the boundary), **not** a results structure — the result is the existing `trades`
  row (FR42/AR43).
- **D-B — the paper open row differs from the backtest's in exactly:** `quantity` (current, R1a),
  `holding_period_seconds = None` (undefined without an exit, as `profit_pct` already is), and
  `commission` via `select_commission` (the paper recorder's settlement-currency pick, already
  dispositioned in story-5.3 (a)). `fees_amount = 0.00` and `profit_loss = realized_pnl` mirror the
  backtest. The parity pin (Task 7) is the contract.
- **D-C — a zero-net instrument stays unrecorded** (5.3 code review item). The broker is flat on
  it, so nothing "remains at the broker"; the strategy's phantom leg is not recorded, as 5.3
  accepted. Recorded, not changed.
- **D-D — one disposition function, four outcomes**, AR36-clean (no "closed"):
  - none held → `none open at seal` (5.3's constant, unchanged);
  - forced with legs → recorded as open trades with no exit, outside the record's metrics; they
    remain at the broker and are no longer owned by any session;
  - forced, other-account positions → named by count, not recorded, no longer owned by any
    session;
  - forced, no engine state → unknown: the engine cache held nothing; whatever IBKR holds for it
    is no longer owned by any session.
  Pin the AC's two phrases ("remain at the broker", "no longer owned by any session") by test.
- **D-E — open legs and the metrics (5.1/5.2 debt): surfaced, not changed.** The forced
  disposition says the open trades are outside the record's metrics; `equity_series.excludes`
  already says `open_positions`. A backtest that ends mid-position still includes its open leg —
  the both-sides decision stays epic-level (AR44 forbids touching the views; FR41 the math).
- **D-F — `--force` with nothing held** seals exactly as without it; `forced: true` is reported
  so the record of the operator's choice is honest.
- **D-G — rows are written inside the seal's transaction**, before the trades are read, through
  the existing capability. A seal that later raises rolls them back with everything else.

### Scope boundaries — do NOT build

- No `session_conditions` keys: `open_positions_at_seal`, `closed_trade_count` and the rest are
  **Story 5.4's** (parallel worktree). 5.5 leaves `SealReport.open_positions` as the seam 5.4 reads.
- No migration, no new column/table/model for results, no change to `results_extractor.py`,
  `session_equity.py`, `BacktestPersistenceService`, the backtest repositories, the trading-session
  repositories' surface (`EXPECTED_CAPABILITIES` unchanged), `src/api/**`, `templates/**`,
  `src/services/backtest_query.py` (AR44).
- No broker read, no order method, no position or order mutation, ever (AR31, NFR14). Never run
  `live seal` or `live start` against a real session.

### Hazards

- **Parallel 5.4.** Both stories edit `session_seal_service.py`. Keep 5.5's change in
  `_refuse_unless_flat` → its replacement, a new `_record_open_legs` helper, the report fields and
  the reader type; do not touch `_base_snapshot`/`run_identity`/`period_start`.
- **The identity map** (5.3): integration read-backs `expire_all()` first.
- **The reader's return type changes** (`SessionView` → `SessionHoldings`). 5.3's unit `Harness`
  (`harness.view`), its integration `_flat(...)` reader and the CLI test
  `test_the_open_position_reader_is_the_sessions_own_view` inject the old type: adapt them to the
  new seam in the same change, keeping every assertion they make — no 5.3 behaviour changes when
  `force` is off.
- **The backfill count now includes open rows** — insert before `find_session_trades`, or the
  count check refuses every forced seal.
- **`_legs_from` refuses a row with an exit price but no exit timestamp** — an open row must have
  both `None`.
- **A test that cannot fail**: every exact-set or "nothing calls X" scan needs a not-vacuous
  sibling; every new disposition string needs a test that fails if it changes; a mutation check
  per AC (copy the file to a scratchpad, never `git stash` — memory).
- **Guard lists**: `LOAD_METHODS` is membership-pinned; widen it in the constant and the pin in one
  commit (CLAUDE.md, Story 4.7 precedent).

### Project Structure Notes

- Touch: `src/models/reconciliation.py`, `src/core/live_session_view.py`,
  `src/services/session_seal_service.py`, `src/cli/commands/live_seal.py`, CLAUDE.md
  (`LOAD_METHODS` note), README.md, `docs/agent/persistence.md`.
- Tests: `tests/unit/services/test_session_seal_service.py`,
  `tests/unit/cli/commands/test_live_seal_cli.py`, `tests/unit/models/` (OpenLeg/SessionHoldings),
  `tests/component/core/test_live_session_view.py`, a parity test under `tests/component/`,
  `tests/integration/db/test_session_seal.py`,
  `tests/integration/core/test_live_session_view_redis.py`.

### References

- [Source: _bmad-output/planning-artifacts/prd-epic5-scope.md#Story 5.5, AR30, AR31, AR43, NFR14]
- [Source: _bmad-output/implementation-artifacts/5-3-seal-a-stopped-session-into-a-comparable-run-record.md — R2a, DN1a, DN2a, Hazards]
- [Source: _bmad-output/implementation-artifacts/deferred-work.md — story-5.1 code review, story-5.2 (f), story-5.3 (e), 5.3 code review]
- [Source: src/services/backtest_persistence.py:375-590 — the open-row shape]
- [Source: src/core/live_trade_recorder.py:144-303 — conversions and D-D]
- [Source: src/core/live_session_view.py — the load-only read]
- [Source: CLAUDE.md — Membership-pinned lists, guard lists, size caps]

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`)

### Debug Log References

### Completion Notes List

**Task 0 — PO rulings (2026-10-11): R1a · R2a · R3b, all as recommended.**
- **R1a** — one null-exit row per open Nautilus position on the configured account, for every
  instrument the view lists, strategy-owned and reconciliation-owned alike; quantity is the
  current open quantity.
- **R2a** — `--force` overrides held positions, other-account positions (count only, not
  persisted) and `NO_ENGINE_STATE` (unknown disposition). Still refused under `--force`: an
  unreachable Redis, `UNREADABLE`/`ADAPTER_INCOMPATIBLE`, an unset `TWS_ACCOUNT`.
- **R3b** — the session's own working orders are read from its engine cache (`load_order` joins
  `LOAD_METHODS`) and reported with their disposition; never a refusal, never a cancel.

**What shipped (2026-10-11).**
- `src/models/reconciliation.py`: `OpenLeg`, `WorkingOrder`, `SessionHoldings` (stdlib-only,
  pinned by `TestImportPurity`). `SessionHoldings.__post_init__` pins legs ⇔ view: every
  instrument's legs sum to its `ViewPosition.quantity`, and no leg stands on an unlisted instrument.
- `src/core/live_session_view.py`: `read_session_holdings` shares one logged read path
  (`_read_logged`) with `read_session_view`. The latter's output, records and failure modes are
  unchanged: it converts no leg and loads no order, both pinned. Legs use the recorder's
  `to_price_decimal` / `select_commission` / `unix_nanos_to_utc`. `LOAD_METHODS` widened 4 → 5
  in the constant, its exact pin and a second Redis `MONITOR` proof together.
- `src/services/session_seal_service.py`: `seal_session(..., force=False)`. `_holdings_at_seal`
  applies R2a. `open_trade` builds the backtest-shaped open row through `TradeCreate`, and
  `_record_open_legs` writes it with `insert_trade_if_absent` before `find_session_trades`. One
  function per disposition. `SealReport` gains `forced`, `open_trades_recorded`,
  `positions_on_another_account`, `working_orders` and `working_orders_disposition`, carried into
  both report forms by the payload. No migration, no new table, model or column for results, and
  no change to `EXPECTED_CAPABILITIES`.
- `src/cli/commands/live_seal.py`: `--force` (default off); the reader is `read_session_holdings`;
  the help text says what `--force` does and does not override; `session.sealed` carries `forced`
  and `open_trades_recorded`. The DN1a "is not checked" sentence is replaced by R3b's report.

**Corrections to the drafted Dev Notes, measured in Task 7.**
- D-B over-stated the differences. A backtest's open row has `holding_period_seconds = None` too:
  the positions report gives `duration_ns = None` for an open position. A never-reduced position
  is identical on both paths, column for column, long and short.
- The paths part only on a **partly reduced** position, in exactly `quantity`, `exit_price` and
  `profit_pct`. The backtest keeps `peak_qty` and writes the reduction's `avg_px_close` as an exit
  price with no exit time, a shape 5.2 refuses. The seal row keeps what remains and no exit.
  Pinned in `tests/component/services/test_open_trade_parity.py` and logged in deferred-work.

**Debt dispositions** (recorded in deferred-work, "Deferred from: story-5.5"):
- closed: 5.3 (e), DN2a and DN1a;
- kept by design: the netted-to-zero `EXTERNAL` leg (D-C);
- surfaced, not fixed: story-5.1's two open-leg items and 5.2 (f). The disposition says "outside
  the record's metrics"; the fix stays an epic-level both-sides decision.
- New items: the backtest's partly reduced open row; the limits of the cache's view of working
  orders; a note for 5.4 that `open_positions_at_seal` must not read "unknown" holdings as none;
  reconciliation-owned open rows now present in `trades`.

**Gates (2026-10-11).**
- `ruff check` and `ruff format --check` clean; `make typecheck` reports no issues (122 files);
  the size-cap guard passes.
- unit 3760 passed; component 1996 passed, 16 skipped;
- integration (`--forked`): `tests/integration/db` + session-view Redis + startup-cash Redis +
  session-equity parity, 161 passed.
- Note: a first `test_session_seal.py` run hit schema-creation collisions (`pg_namespace`
  unique violation) while the 5-4 worktree ran `tests/integration -n auto` against the same
  Postgres. It was environmental: the fixture names schemas `test_<worker>`. Re-run alone: 30
  passed.

**Mutation sweep: 15/15 killed** (scratch script outside the repo; original bytes restored in a
`finally`, no `git stash`). It covers:
- refuse-without-force, and the `--force` hint;
- rows written, `client_order_id`, quantity;
- the disposition, and force defaulting off;
- R2a both ways;
- the R3b order filter;
- the zero-net leg filter, and current quantity vs peak;
- `apply` on the seal path;
- `read_session_view`'s record keys, and its never loading an order.

The first M15 attempt survived because the mutation itself was wrong: it fetched `load_order`
without calling it, in the holdings path. Re-aimed at `_read` (the reconcile path), it was killed.

No P-number: the seal is database plus local Redis (NFR33), and the read is proven against a
real Redis. `live seal` and `live start` were never run against a real session.

**Code review (2026-10-11).** Three layers ran in parallel subagents and none failed. 33 raw
findings came down to 22. The PO ruled DN1a, DN2a and DN3a, and patches were batch-applied
(choice 0) test-first: the failing tests were seen red first (17 failed, 63 errors), then made
green. No schema change and no check loosened. Patches applied:
- **DN1a → patch.** `SessionHoldings.unreadable_orders` (stdlib-only).
  - `_working_orders` names an order it cannot rebuild or read instead of raising, and logs
    `reconcile.session_view_order_unreadable`.
  - The read record carries the count. The report has `unreadable_orders` and a disposition part
    saying they "could not be read … check them in TWS". Never a refusal, `--force` or not.
  - Listing the orders at all still fails closed.
- **DN2a → wording.** The unknown-holdings disposition, `--help` and the README tell the operator
  to confirm `REDIS_HOST`/`REDIS_PORT` point at the session's own Redis before forcing.
- **P1.** `holdings_known` in `SealReport` and both report forms. `session.sealed` now also
  carries `holdings_known`, `positions_on_another_account`, and the working-order and
  unreadable-order counts. `session.seal_holdings_unknown` (`HOLDINGS_UNKNOWN_EVENT`) is logged
  after the commit only.
- **P2.** Every disposition names the session's engine cache. The working-orders disposition,
  `--help` and the README say it cannot see a fill or cancel made at IBKR while the session was
  stopped.
- **P3.** The forced disposition says the open trades, "and any P&L already realized on them, are
  outside the record's metrics".
- **P4.** The no-`--force` refusal lists each leg as `instrument quantity (owner)`.
- **P5.** `VIEW_LOAD_METHODS` is defined once and `LOAD_METHODS = (*VIEW_LOAD_METHODS,
  "load_order")`; both are pinned.
- **P6.** Seven routed debt entries in deferred-work are marked dispositioned in place.
- **P7.** The other-account refusal says to check `TWS_ACCOUNT` first.
- **P8.** `_require_ids_fit` refuses by name a `trade_id`/`venue_order_id` longer than its column
  (the bound is read from the column).
- **P9.** An open order with zero `leaves_qty` is not working.
- **P10.** The two disposition tests now read the rendered report.

DN3a and W1–W3 are deferred, and P3's disclosure is in deferred-work ("Deferred from: code review
of 5-5-…").

Gates after the review:
- `ruff check --no-cache` and `ruff format --check` clean; `make typecheck` no issues (122
  files); size caps pass;
- unit 3787 passed; component 1999 passed, 16 skipped;
- integration `tests/integration/db` 149 passed and `tests/integration/core` 110 passed,
  2 skipped (`--forked`, run sequentially, no concurrent integration run).

Review-patch mutation sweep: 9/9 killed (scratch script, originals restored).

### File List

- `src/models/reconciliation.py` — `OpenLeg`, `WorkingOrder`, `SessionHoldings`
- `src/core/live_session_view.py` — `read_session_holdings`, `LOAD_METHODS` 4 → 5
- `src/services/session_seal_service.py` — `force`, open trades, dispositions, report fields
- `src/cli/commands/live_seal.py` — `--force`, holdings reader, help, `session.sealed` fields
- `tests/unit/models/test_reconciliation.py`
- `tests/unit/services/test_session_seal_service.py`
- `tests/unit/cli/commands/test_live_seal_cli.py`
- `tests/component/core/test_live_session_view.py`
- `tests/component/services/test_open_trade_parity.py` (new)
- `tests/integration/core/test_live_session_view_redis.py`
- `tests/integration/db/test_session_seal.py`
- `CLAUDE.md` — `LOAD_METHODS` membership note
- `README.md` — `live seal --force` row and paragraph
- `docs/agent/persistence.md` — the seal's open-position handling
- `docs/agent/architecture.md` — the seal service's role line
- `_bmad-output/implementation-artifacts/deferred-work.md` — story-5.5 section
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 5-5 key only
- `_bmad-output/implementation-artifacts/5-5-handle-open-positions-at-seal-time-explicitly.md`

### Change Log

- 2026-10-11 — Story drafted (bmad-create-story); PO ruled R1a / R2a / R3b.
- 2026-10-11 — Implemented (bmad-dev-story): `live seal --force` records open positions as open
  trades with no exit; working orders reported; DN1a/DN2a closed. Status → review.
- 2026-10-11 — Code review (bmad-code-review): 22 findings. PO ruled DN1a, DN2a and DN3a.
  P1–P10 batch-applied test-first; DN3a, W1–W3 and P3's disclosure deferred; 6 dismissed.
  Status → done.
