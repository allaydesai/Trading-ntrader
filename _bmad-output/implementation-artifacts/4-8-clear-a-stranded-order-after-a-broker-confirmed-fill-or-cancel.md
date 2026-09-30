# Story 4.8: Clear a Stranded Order After a Broker-Confirmed Fill or Cancel

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want an order the cache still shows open after IBKR has already resolved it — filled or
cancelled while the session was stopped or disconnected — cleared automatically instead of
staying `ACCEPTED` forever,
so that a session's record of its own orders stays true to the broker, and a namespace never
carries a permanently-stale order into every restart after it.

## Why this story is shaped the way it is

Read this before designing anything. This story was not drafted from epic text: the PO (Allay)
asked for it at the Epic 4 retrospective (2026-09-28), naming two things together — "the stranded
`ACCEPTED` order and the `p7-position-test` stall" — that Stories 4.2–4.7 each measured, disclosed
and routed onward without building a fix. Re-measured at drafting (2026-09-28, installed
`nautilus-trader 1.220.0`, head `f892d56`, worktree `p3-epic4-base`). Wheel paths are relative to
`.venv/lib/python3.11/site-packages/nautilus_trader/`; ripgrep skips `.venv` (gitignored), so
search it with `grep -n` on explicit paths.

**1. Nothing today ever resolves a stranded `ACCEPTED` order — by construction, in three
independent places, all re-verified against the current code, not just cited from a prior
story.** `reconcile_at_startup` inspects and corrects **positions** only
(`cached_positions`/`compare_positions`); it counts `open_orders = len(node.cache.orders_open())`
(F1) but never reads what those orders are or asks the broker about them. Nautilus's own startup
mass-status pass (`generate_mass_status` → `generate_order_status_reports(open_only=False)`,
inside `node:connect`) reconciles only orders IBKR's response actually contains (F2); nothing
iterates the *cache's* open orders looking for one **absent** from that response. And
`RuntimeReconciler` — the one piece of this project's own code that runs for a session's whole
life — deliberately defers only **in-flight** (`SUBMITTED`/`PENDING_*`) orders from its position
comparison (F4); it was built to avoid being blocked by a stranded order, never to resolve one.

**2. Nautilus's native open-order consistency check cannot fix this, in either direction —
re-verified by reading `_check_orders_consistency` and `_reconcile_execution_mass_status`
directly, not just re-citing Story 4.3's F1.** Both only reconcile reports `generate_order_status_reports`
actually returns; a report is never synthesised for a cache-open order the response omits (F2,
F3). Turning `open_check_interval_secs` on would therefore not touch this story's case **at all**
— it would only reopen the *other* direction's defect Story 4.3 measured and the PO ruled off
(1A): a locally-cancelled order IB still lists open gets an `OrderAccepted` republished every
cycle, forever, resetting Story 3.7's refusal streak (F5). So "detect a stranded order" cannot be
built by flipping that setting — the PO's 1A ruling stays exactly as it is (design decision D-B,
below) — it has to be a targeted read of our own, at a site and cadence we choose and log
ourselves, the same shape this project already uses for the avg-px fix and the position-report
suppression (F6, F7): a defensive patch, never a framework toggle.

**3. `reconcile_execution_report` already gives a safe, precedented way to resolve one order
locally — no forbidden order method is needed.** Handing the engine a synthetic
`OrderStatusReport(order_status=CANCELED, ...)` for our own cached order's `client_order_id` runs
through exactly the `_reconcile_order_report` path the position corrections already use (Story
4.2's D-E shape, F8), and it resolves into an `OrderCanceled(reconciliation=True)` event applied
through the engine's own event path (F9) — not a call to `cancel_order`, which is one of
`FORBIDDEN_ORDER_METHODS` (F10) and would, in any case, try to cancel a *live* order at the broker
rather than correct a local record of one that is already gone there. This mirrors almost exactly
how Nautilus resolves a stuck in-flight order itself — `_resolve_inflight_order` synthesises an
`OrderRejected` or `OrderCanceled` and calls `_handle_event`, never a broker call (F11). That is
the precedent this story's fix follows, not invents.

**4. The one IB call that could answer "is this specific order still open at the broker" is the
same method Story 4.3's F3 already flagged as dangerous — `generate_order_status_reports` — and
this story re-verifies the landmine directly rather than trusting the citation.** It ignores
`open_only` entirely, fabricates a `FILLED` report per non-zero position keyed to the
**instrument** id (never a real order id, so it can never be mistaken for one of ours), and
raises `ValueError` while parsing any order with an empty `orderRef` — confirmed directly against
the installed wheel: `ClientOrderId('')` raises `ValueError("'value' string was invalid, was ''")`
(F12). A manual TWS order has no `orderRef`, so **one** manual order anywhere on the account fails
the **entire** call, for every instrument, not just its own. Any fix that calls this method
itself must wrap the whole call and treat any exception as "inconclusive this cycle" — never let
a maintenance read become a new way for something to affect a session's trading. That containment
is a first-class thing this story's tests must exercise, not an incidental try/except.

**5. What this story cannot do: prove or disprove the historical `p7-position-test` live
stall.** That incident happened inside `node:connect`, before any phase of this project's own
code runs (F13) — Nautilus's kernel sequence is engines-connect → reconcile → emulator → portfolio
→ `trader.start()`, one coroutine, and the mass-status pass that would first encounter a stranded
order runs inside it, well before `_phase_reconcile` or any runtime cycle exists. Story 4.3's Task
1.5 already tried to reproduce a stall with a real `LiveExecutionEngine` + `Cache` + `Portfolio`
and could not — "no stall at component tier … a portfolio-initialisation timeout a kernel-less
harness cannot reproduce" is the exact recorded finding (F14). Reproducing it for real needs a
full `NautilusKernel`/`TradingNode`, which this project's own testing discipline forbids
constructing in a component test (the C-logging guard — "No component test may construct a
`TradingNode` or a `LiveDataEngine`", F15) and which this worktree cannot do live either (no
Gateway reachable, no `.env`, by the project's own charter — every prior Epic 4 story recorded the
same). So this story's fix runs strictly **after** whatever happens inside `node:connect` — it can
stop a namespace from ever again carrying a permanently-stale order into a *future* restart, but
it cannot, by construction, prevent or explain a hang that happens before it gets a chance to run.
That is disclosed as a hard limitation, not solved by this story, and is the reason Task 1 gates
production code on an honest attempt to say more than Story 4.3 already could.

**6. Given point 5, the fix is judged by what it can actually close: the stranded order
*record*, for the life of every session from here on.** It resolves within a few runtime cycles
of an order going stale — the same latency discipline Story 4.3 already disclosed for position
corrections (one to two cycles, ~60–120 s) — reusing `RuntimeReconciler`'s existing cadence,
debounce and containment machinery rather than inventing a second one. The genuinely open
question — "did this specific order fill or get cancelled at the broker?" — this story does
**not** try to answer precisely. Story 4.2's PO ruling already rejected re-attributing a synthetic
fill's economics to the correct strategy via cache surgery, and the code-review deferral on Story
4.3 ("an `ACCEPTED` order filled while disconnected … mid-run: not fixed … owner: Epic 4
retrospective") is this exact case, handed to this story by name. Inventing a `FILLED` event here
with fabricated fill economics (price, commission) would be strictly worse than what exists
today — a wrong number presented as a real one. The recommended design resolves every stranded
order the same way, **`CANCELED`**, and discloses plainly that this can be factually wrong for the
"filled while disconnected" case: the position-level correction (already running today,
independently, as `INTERNAL-DIFF`) is what carries the true economic effect broker-ward; this
fix only stops the *order* record from lying forever. This trade-off is put to the PO explicitly
(D-C, below), not decided silently.

### Measured facts — cite, don't re-derive

Task 1 re-measures F4, F12 and the Task 1.5-style stall probe (F14) before production code is
written; everything else here was read directly against the current tree and the installed wheel
at drafting and does not need re-measuring, only re-citing.

| # | Fact | Where |
|---|---|---|
| F1 | `reconcile_at_startup` reads and corrects **positions** only: `cached_positions(cache)` calls `cache.positions_open()` and nothing else order-shaped; the phase's only order-facing line is `open_orders=len(node.cache.orders_open())`, a count with no inspection of what is open or why. | `src/core/live_startup_reconcile.py:245-258, 328-401 (:396 the count)` |
| F2 | `LiveExecutionClient.generate_mass_status` (the startup pass inside `node:connect`) calls `generate_order_status_reports(open_only=False, ...)` and folds the result into `ExecutionMassStatus.order_reports`; `ExecutionEngine._reconcile_execution_mass_status` then iterates `mass_status.order_reports.items()` only — nothing in that loop, or anywhere else in the file, iterates the cache's own open orders looking for one missing from the response. | `.venv/…/live/execution_client.py:434-505`; `.venv/…/live/execution_engine.py:1051, 1068 (the loop)` |
| F3 | `ExecutionEngine._check_orders_consistency` (the continuous-loop twin, off today per Story 4.3's PO ruling 1A) has the identical shape: it reconciles only `report in all_order_reports`, comparing each report's `is_open` against `cache.client_order_ids_open()`. A cache-open order absent from the response produces no report and is never visited by this function either. Re-read directly; unchanged since Story 4.3 measured it. | `.venv/…/live/execution_engine.py:687-755` |
| F4 | `RuntimeReconciler.observe` (Story 4.3) computes `deferred = {order.instrument_id for order in cache.orders_inflight()}` — `SUBMITTED`/`PENDING_*` only — and drops those instruments from the position comparison. An `ACCEPTED` order's instrument is never deferred; its **position** consequence is corrected regardless (Story 4.3's 2A ruling), but nothing in this module reads the order itself. | `src/core/live_runtime_reconcile.py:316-335` |
| F5 | Story 4.3's Task 1.5/1.1b findings, re-confirmed unchanged (same wheel version): (a) a cached `ACCEPTED` order with the broker holding the position: the native pass imports `EXTERNAL`/`INTERNAL-DIFF` and the stranded order stays `ACCEPTED` forever, no stall at component tier; (b) turning the consistency check on republishes `OrderAccepted` forever for a locally-cancelled order IB still lists open, resetting Story 3.7's refusal streak — the reason the PO ruled it off (1A) and it must stay off. | `_bmad-output/implementation-artifacts/4-3-…md:648-660` (Debug Log); `deferred-work.md:3477-3488` |
| F6 | The node builder patches the IB exec client after construction, before the node starts (`install_avg_px_serialization_fix`, `install_position_report_suppression`), both inside `InteractiveBrokersLiveExecClientFactory.create`, both with a load-bearing class name and a loud `AttributeError` if the upstream shape has moved. This is the established "targeted patch, not a framework toggle" seam. | `src/core/live_node_builder.py:623-662` (approx, factory block); `src/core/live_exec_avg_px.py`; `src/core/live_exec_position_reports.py` |
| F7 | `live_exec_position_reports.install_position_report_suppression` and `live_runtime_reconcile.RuntimeReconciler` are the precedent for "our own verified cycle, not the native setting": both are new, small, `live_*`-family modules added in their own creating commit, on `NODE_FACING_MODULES` and `TestImportPurity.MODULES`, neither on `STOP_PATH_MODULES`. | `src/core/live_exec_position_reports.py` (34 lines total); `src/core/live_runtime_reconcile.py` (271 lines total) |
| F8 | `ExecutionEngine.reconcile_execution_report(report)`, given an `OrderStatusReport`, calls `_reconcile_order_report(report, [])`. For a **known** cached order (`self._cache.order(client_order_id)` not `None`) it skips the "external order" path entirely and, for `report.order_status == CANCELED`: `if order.status != CANCELED and order.is_open: … self._generate_order_canceled(order, report)`. `order.is_open` is `True` for `ACCEPTED`. | `.venv/…/live/execution_engine.py:1128 (`_reconcile_order_report`), :1198-1203 (the CANCELED branch)` |
| F9 | `_generate_order_canceled` builds `OrderCanceled(reconciliation=True, instrument_id=report.instrument_id, client_order_id=report.client_order_id, venue_order_id=report.venue_order_id, account_id=report.account_id, ts_event=report.ts_last, …)` and calls `self._handle_event(canceled)` — the same normal event-application path every other reconciliation correction in this codebase already goes through (publish + apply to the cached order), never a broker call. | `.venv/…/live/execution_engine.py:1831-1845` |
| F10 | `FORBIDDEN_ORDER_METHODS = {"close_all_positions", "close_position", "cancel_all_orders", "cancel_order", "submit_order", "submit_order_list"}`, exact-set pinned. `reconcile_execution_report` is **not** in this set — the existing startup/runtime modules already call it freely, and this story's fix calls nothing else order-mutating. | `tests/unit/core/test_live_stop_path_is_inert.py:127-136, 417-426` |
| F11 | `ExecutionEngine._resolve_inflight_order(order)` — the native precedent for "synthesise a reconciliation event locally, never call the broker": `SUBMITTED` → synthetic `OrderRejected(reconciliation=True)`; `PENDING_UPDATE`/`PENDING_CANCEL` → synthetic `OrderCanceled(reconciliation=True)`, both via `self._handle_event(...)`. | `.venv/…/live/execution_engine.py:537-570` |
| F12 | `InteractiveBrokersExecutionClient.generate_order_status_reports` (:374-445) ignores `open_only`; builds one fabricated `FILLED` report per non-zero position (`client_order_id = venue_order_id = instrument.id.value`, :415-433) **then** appends `self._client.get_open_orders(...)` results (:437), each parsed by `_parse_ib_order_to_order_status_report` (:300-372), which does `client_order_id=ClientOrderId(ib_order.orderRef)` (:354). Confirmed directly against the installed wheel: `ClientOrderId('')` raises `ValueError("'value' string was invalid, was ''")`. The parse is `await`ed in a plain `for` loop (:441), so one bad order's exception aborts the whole call — no partial result. | `.venv/…/adapters/interactive_brokers/execution.py:300-372, 374-445`; measured directly via `uv run python -c "ClientOrderId('')"` |
| F13 | `NautilusKernel.start_async` (re-read directly, not just cited from a prior story): `_start_engines()` → `_connect_clients()` → `_await_engines_connected()` → (if `exec_engine.reconciliation`) `_await_execution_reconciliation()` → `_emulator.start()` → `_initialize_portfolio()` → `_await_portfolio_initialization()` → `self._trader.start()` — one coroutine, returning early (trader never starts) on any `False` along the way. This entire sequence runs before `_phase_gate_account`/`_phase_reconcile`/any runtime cycle of ours exists. | `.venv/…/system/kernel.py:990-1027` |
| F14 | Story 4.3 Task 1.5, the only existing attempt to reproduce a stall from a stranded `ACCEPTED` order: "no stall at component tier (the live stall was a portfolio-initialisation timeout a kernel-less harness cannot reproduce)". Never re-attempted since; P17b/P18/P19b (which could reproduce it live) have never been run. | `_bmad-output/implementation-artifacts/4-3-…md:652-656`; `deferred-work.md:3477-3484, 3573-3575` |
| F15 | "No component test may construct a `TradingNode` or a `LiveDataEngine`: keep the autouse `_assert_c_logging_state_is_unchanged` fixture." A real-kernel reproduction attempt is therefore an integration-tier (`--forked`) undertaking at best, and this worktree has no Gateway/`.env` to go further than that. | `_bmad-output/implementation-artifacts/4-3-…md:713-715` (Dev Notes hazards) |
| F16 | Order attributes needed to build a synthetic `OrderStatusReport` are all public on a cached `Order`: `side`, `order_type`, `time_in_force`, `quantity`, `filled_qty`, `avg_px`, `venue_order_id`, `client_order_id`, `instrument_id`, `account_id`, `status`. Confirmed by introspection against the installed wheel (`dir(nautilus_trader.model.orders.base.Order)`). | measured directly, `uv run python -c "..."` |
| F17 | Current sizes (`measure_module`, re-run at drafting, matches the checked-in baseline exactly — `test_regenerating_the_baseline_reproduces_it_exactly` is green): `live_session_runner.py` file **490/500**, `LiveSessionRunner` **416** (baseline); `live_session_steady_state.py` file **260**, `SessionSteadyState` **114** (baseline); `live_runtime_reconcile.py` file **271**, `RuntimeReconciler` class **77** (no baseline entry — under the 100 cap, 23 headroom), largest function 30; `live_startup_reconcile.py` file **318**, largest function 39 (no class/function over cap); `live_exec_position_reports.py` file **34**. | `tests/unit/governance/test_size_caps.py::SIZE_BASELINE`; `uv run python -m tests.unit.governance.test_size_caps --print-baseline` |
| F18 | `get_open_orders` issues `reqOpenOrders` and filters the response to `order.account == account_id`; TWS's own `reqOpenOrders` (not `reqAllOpenOrders`) answers only the connecting client id's own orders (IB API's documented scoping, cited by Story 4.3's F3, not re-derived here) — our session's exec client submitted the order under its own client id, so its own stranded orders round-trip correctly. | `.venv/…/adapters/interactive_brokers/client/order.py:100-131` |
| F19 | An order this adapter submits gets `orderRef = client_order_id.value` at submission (the field mapping used both ways), so `_parse_ib_order_to_order_status_report`'s `ClientOrderId(ib_order.orderRef)` recovers our own generated id exactly — matching against a stranded order's `client_order_id` is not a heuristic, it is exact. | `.venv/…/adapters/interactive_brokers/parsing/execution.py:75` |
| F20 | The current live-verification procedure numbering tops out at **P20** (corporate actions, Story 4.7). No `P21` exists yet. | `docs/qa/phase3-live-verification.md` (`grep -n "^## Procedure P"`) |

### Design decisions (disclosed here, not discovered in review)

- **D-A — Where the fix lives: `RuntimeReconciler`'s existing cycle, not a new startup-phase
  call.** A new module, `src/core/live_stranded_orders.py`, owns the pure/duck-typed detection and
  resolution logic (the `live_exec_position_reports.py` shape: small, focused, no
  `nautilus_trader` import beyond what parsing a report requires). `RuntimeReconciler` composes it
  and calls it once per `SCOPE_RUNTIME` cycle only (not `SCOPE_RECONNECT` — see below), after
  `_act`'s position handling, contained the same way every other step in `_run` already is (AR42:
  an exception here must never escape the tick).
  - **Why not also at startup, inside `reconcile_at_startup`.** Point 5 above already establishes
    that a startup-phase call cannot prevent or explain a `node:connect`-level hang — it runs
    strictly after that returns, exactly where the runtime cycle's first tick also runs, a few tens
    of seconds later. Adding it to `reconcile_at_startup` would (a) grow `live_startup_reconcile.py`
    and the `reconcile` phase's own time budget for a benefit measured in tens of seconds of extra
    latency avoided, and (b) duplicate the wiring, the test surface and the debounce state in two
    places for one concern. `RuntimeReconciler` already owns "verified cycle against the broker,
    debounced, contained" — this is squarely inside that, not a new concern next to it.
  - **Why not on `SCOPE_RECONNECT` cycles.** Reconnect cycles gate the trading-permission grant
    (Story 4.3 D-F); adding a third IB round trip and a new resolution path to that already-narrow,
    already-carefully-ruled sequence is a correctness risk for a speed benefit nobody asked for —
    a stranded order is not urgent in the way "can this session trade again" is. The stale-order
    check runs only while `CONNECTED`, on the same `RUNTIME_RECONCILE_EVERY_TICKS` cadence as
    positions (60 s at AR32's 30 s tick).
  - **Cost, disclosed.** A namespace that is *already* poisoned when this story ships is not healed
    retroactively by a read — it is healed the first time that namespace's session runs long enough
    (past the debounce) with this code deployed. A namespace whose only future is "restart, hit the
    stall inside `node:connect`, never reach a running session" is not helped by this fix at all
    (point 5) — if that is what `p7-position-test` actually was, this story does not close it.

- **D-B — The native open-order consistency check stays off. Not touched, not revisited.**
  Re-stating Story 4.3's PO ruling 1A as a hard constraint of this story, not a design choice
  re-litigated here: `open_check_interval_secs` is `None`, `EXEC_ENGINE_OPEN_CHECK_INTERVAL_SECS`
  is unchanged, and no test in this story may assert anything that would require it to be set. F2
  and F3 above independently confirm the check could not have fixed this story's case even if it
  were on — it is not a live option that got closer or further away, it was never a candidate.

- **D-C — RULED (A) — a stranded order is resolved as `CANCELED`, uniformly — never as a
  fabricated `FILLED`.** PO ruling (Allay, 2026-09-28, Epic 4 retrospective): **(A)**.
  - **Always `CANCELED`.** A stranded order that is confirmed absent from IB's
    open-orders response is reconciled to `CANCELED` regardless of whether it actually filled or
    was cancelled at the broker. The record `reconcile.stale_order_cleared` says so explicitly
    (`"the broker no longer lists this order as open; whether it filled or was cancelled could not
    be determined from here"`), naming instrument, side, quantity, `client_order_id`. If it
    actually filled, the position-level correction already running (Story 4.2/4.3's `INTERNAL-DIFF`
    path) is what carries the true economic effect broker-ward — this fix never invents a fill.
    Cost: the order's own history in the cache/Redis reads "cancelled" for what may really have
    been a fill; nothing downstream (no strategy logic today reads `orders_open` per Story 4.5's
    D-H(a)) acts on that distinction, but an operator reading raw order history by hand would see a
    misleading status.
  - **(B) Rejected — attempt to infer `FILLED` from position evidence.** Compare the broker's
    current position for the order's instrument against what it would be if the order had filled
    at its full remaining quantity; if it matches, reconcile as `FILLED` at the broker's average
    price instead. This needs the broker's position to be attributable to *this specific order*
    rather than to any other order or synthetic correction touching the same instrument in the
    same window — a strong assumption with no way to verify it exactly, and Story 4.2's PO
    ruling already refused exactly this kind of synthetic-fill reattribution once (cache surgery,
    rejected). A wrong `FILLED` with invented commission/price is a worse record than an honest
    `CANCELED` with a disclosed caveat.
  - **(C) Rejected — do not resolve automatically at all, only log and name it.** Safest, but
    leaves the original problem exactly as it is: the order stays `ACCEPTED` forever,
    `reconcile.ok`'s `open_orders` count stays permanently wrong, and nothing closes the story's
    own stated goal.

- **D-D — Detection: a defensive, targeted `generate_order_status_reports(open_only=True)`
  call, never the general check.**
  - `broker_open_order_ids(node, log)` in the new module: finds the IB exec client
    (`find_ib_exec_client`, already imported by `live_runtime_reconcile.py`), calls
    `generate_order_status_reports(GenerateOrderStatusReports(instrument_id=None, start=None,
    end=None, open_only=True, command_id=UUID4(), ts_init=...))` inside a **bare
    `try/except Exception`** (F12's landmine — any failure, including the empty-`orderRef`
    `ValueError`, is "inconclusive," never raised, never a phase/tick failure), and returns the
    `frozenset` of `report.client_order_id` values, or `None` on failure. The fabricated
    per-position `FILLED` rows are harmless noise here: their `client_order_id` is always the
    instrument id string, which a real Nautilus-generated `client_order_id` can never equal (F19).
  - **Skip the call entirely when there is nothing to check** (the common case): if
    `stale_accepted_orders(cache)` is empty this cycle, `broker_open_order_ids` is never called —
    no extra IB round trip on a session with no resting orders, which the built-in strategies never
    place (market orders only). This is the disclosed mitigation for the added per-cycle broker
    request Story 4.3's 60 s cadence was budgeted around (two requests; this adds a conditional
    third).
  - `stale_accepted_orders(cache)`: `cache.orders_open()` minus `cache.orders_inflight()` (by
    `client_order_id`), filtered to orders with a non-`None` `venue_order_id` (an order that never
    reached the broker — `EMULATED`/`RELEASED` — has nothing to ask IB about) — Task 1.1 confirms
    the exact status set this reaches in practice (expected: `ACCEPTED`, `PARTIALLY_FILLED`,
    `TRIGGERED`).

- **D-E — Debounce and safety guards, reusing this project's established idiom, not inventing
  one.**
  - `StaleOrderWatch`, the `_Debounce`/`_OkLog` shape from `live_runtime_reconcile.py`: a stranded
    order acts only once its `client_order_id` is confirmed absent on **two** consecutive
    `SCOPE_RUNTIME` cycles at least `STALE_ORDER_DEBOUNCE_SECONDS = 60.0` apart (its own constant —
    the same value as position debounce today, not coupled to it). A cycle where
    `broker_open_order_ids` returns `None` (inconclusive) or raises resets nothing but also
    confirms nothing — it is simply skipped for this check, logged once per streak like
    `reconcile.cycle_failed` (reusing `_FailureStreak`'s shape, not its instance).
  - An instrument with an **outstanding** position-level issue this cycle (a row `_act` could not
    fully resolve, or a contradicted-strategy row about to stop the session) is skipped for order
    clearing this cycle — order hygiene never runs ahead of position correctness on the same
    instrument. Every other instrument's stale orders are still checked (the same "narrow the
    block" philosophy the code review already ruled for unresolved rows in Story 4.3).
  - Resolution: build an `OrderStatusReport(order_status=CANCELED, ...)` from the cached order's
    own fields (F16 — no guessing: `account_id`, `instrument_id`, `venue_order_id`, `order_side`,
    `order_type`, `time_in_force`, `client_order_id`, `quantity=order.quantity`,
    `filled_qty=order.filled_qty`, `avg_px=order.avg_px`), call
    `engine.reconcile_execution_report(report)`, then re-read the order from the cache and confirm
    `status == CANCELED` before logging `reconcile.stale_order_cleared` — the same "log only once
    the re-read proves it took" discipline `live_runtime_reconcile._log_taken` already uses for
    positions. A framework refusal (`False`/an exception) is logged
    `reconcile.stale_order_clear_failed` (WARNING, contained — never raised into the tick; clearing
    a stale order is hygiene, not a correctness gate, and must never be allowed to stop a session).

- **D-F — Records (AR41), membership-pinned both directions from the start (CLAUDE.md's
  "Membership-pinned lists" discipline, applied here rather than re-learned in a later review).**
  `EMITTED_STALE_ORDER_EVENTS = ("reconcile.stale_order_cleared", "reconcile.stale_order_clear_failed",
  "reconcile.stale_order_check_failed")` — the last for a contained `broker_open_order_ids`
  failure/exception, logged at most once per streak. Pinned by a test that drives every branch of
  the module's own dispatch and compares the captured record names against the tuple, both
  directions — the `EMITTED_ORDER_EVENTS`/`EMITTED_TRADE_EVENTS` precedent, not a second
  hand-written list next to it. `reconcile.stale_order_check_failed` is a diagnostic (the
  `order.observer_failed` precedent), deliberately outside any "resolved this many rows" count.
  NFR26: never a raw account id, never `str(exc)` — only `type(exc).__name__` (the
  `_FailureStreak.note` precedent).

- **D-G — RULED (A) — scope of Task 1's stall-reproduction attempt.** PO ruling (Allay,
  2026-09-28, Epic 4 retrospective): **(A)**.
  - **Do not attempt a full-kernel reproduction in this story.** Task 1 re-runs
    Story 4.3's Task 1.5-shaped probe (real `LiveExecutionEngine` + `Cache` + `Portfolio`, no
    `TradingNode`) once more, to confirm F5(a) is unchanged, and stops there. The story's Debug Log
    states plainly that the live stall remains unreproduced and unexplained, and names the fix's
    real, disclosed scope (D-A's cost paragraph) rather than implying it closes the historical
    incident. The eventual live check is Procedure P21 (Testing section), operator-only.
  - **(B) Rejected — build an integration-tier (`--forked`) harness that constructs a real
    `NautilusKernel`/`TradingNode` against a broker double, specifically to probe for a hang during
    mass-status reconciliation with a pre-seeded stranded order.** Materially more work and risk
    (this project's own discipline forbids a real `TradingNode` at component tier for the
    C-logging guard reason, F15; an integration-tier attempt is a genuinely new kind of test this
    codebase does not yet have), and it might still not reproduce anything — Story 4.3 already
    tried the next-best thing and came up empty. Would only be worth it if the PO wanted the
    question chased further
    than this story's headline fix requires.

## Acceptance Criteria

This story was authorised at the Epic 4 retrospective, not drawn from `epics.md`; the criteria
below are this story's own, covering FR34 (maintain alignment throughout the session), FR35
(resolve in favor of the broker), NFR14 (no artificial trades) and NFR26 (masked, safe records).

1. **Given a cached order whose status is `ACCEPTED` (or `PARTIALLY_FILLED`/`TRIGGERED`) and whose
   `venue_order_id` is set, When the broker's own open-orders response, read defensively, does not
   list it on two consecutive runtime cycles at least `STALE_ORDER_DEBOUNCE_SECONDS` apart, Then
   the order is reconciled to `CANCELED` through `exec_engine.reconcile_execution_report` and one
   `reconcile.stale_order_cleared` WARNING is logged naming the instrument, side, quantity and
   `client_order_id` (FR35).**
   - Real-engine component test (`live_runtime_reconcile.py`'s harness shape): a cached `ACCEPTED`
     order absent from a stubbed `generate_order_status_reports(open_only=True)` response, seen on
     two cycles ≥ 60 s apart (an injected clock), transitions to `CANCELED` in the cache; a single
     sighting changes nothing (debounce, mutation-tested).
   - An order present in the broker's response is never touched, on any number of cycles
     (`order.status` unchanged; no `reconcile_execution_report` call for it).
   - An order with no `venue_order_id` (never reached the broker) is never a candidate.
2. **Given `generate_order_status_reports` raises for any reason (the empty-`orderRef`
   `ValueError`, or anything else), When a runtime cycle runs, Then the cycle is not affected: no
   order is cleared, no exception escapes `RuntimeReconciler.on_tick`, and at most one
   `reconcile.stale_order_check_failed` WARNING is logged per failure streak (never one per
   cycle) (NFR20's "fail closed, never crash" reading applied to hygiene, not correctness).**
   - Unit test with a stub client whose `generate_order_status_reports` raises `ValueError`,
     `TimeoutError` and a bare `Exception`: each is contained; the position-comparison half of the
     same cycle still runs and still corrects/refuses as Story 4.3 already proves (this story adds
     a test, not a change, to that half).
   - Mutation: remove the `try/except` → the target test goes red (a cycle-killing exception).
3. **Given no candidate stale order this cycle, When a runtime cycle runs, Then
   `generate_order_status_reports` is never called (NFR5's spirit: no broker round trip this
   codebase does not need).**
   - `stale_accepted_orders(cache) == ()` on a cycle → the stub client records zero calls to
     `generate_order_status_reports`. Mutation: call it unconditionally → the target test goes red.
4. **Given a resolved-broker-ward order was in fact filled while the session was down or
   disconnected, When it is cleared, Then the record discloses that the outcome could not be
   determined here, and the true position effect is left entirely to the existing position-level
   reconciliation — this story never fabricates a `FILLED` event or an invented price/commission
   (NFR26, the D-C ruling).**
   - `reconcile.stale_order_cleared`'s message/field set is asserted to contain no `avg_px`/`price`
     field implying a fill occurred, and the `AST` scan proves the module constructs exactly one
     `OrderStatusReport.order_status` literal, `CANCELED` (never `FILLED`, never `PARTIALLY_FILLED`)
     — the same "one mutation direction is structurally impossible" style Story 4.3 used for D-D's
     "never the reverse" clause.
5. **Given an instrument with an outstanding position-level discrepancy this cycle (unresolved,
   refused, or about to stop the session), When the stale-order check runs, Then that instrument's
   orders are skipped this cycle, and every other instrument's stale orders are still checked
   (the Story 4.3 code-review "narrow the block" precedent, applied here) (NFR14).**
   - Component test: two instruments, one with a confirmed position discrepancy this cycle and a
     stale order, one clean with its own stale order — only the clean instrument's order clears.
6. **Given the native open-order consistency check, When this story ships, Then it is still off,
   unchanged, and no test in this story requires it on (D-B).**
   - `EXEC_ENGINE_OPEN_CHECK_INTERVAL_SECS is None` still pinned by Story 4.3's existing test,
     unmodified by this story; `git diff` shows no line of `live_node_builder.py`'s open-check
     block touched.
7. **Given the size caps, When this story's changes land, Then every touched file/class/function
   is measured and, if it exceeds its cap, disclosed with a one-line reason in the same commit
   (CLAUDE.md).**
   - `RuntimeReconciler`'s measured size after Task 5's wiring is recorded in the Debug Log; if it
     exceeds 100 executable statements, a baseline entry (or a further split, the `_OkLog`/
     `_FailureStreak` precedent) is added in the same commit, not deferred.

## Tasks / Subtasks

- [ ] **Task 0 — Standing checks (record in the Debug Log)**
  - [ ] 0.1 Head, clean tree; `uv run ruff check .` and `make typecheck` clean before the first
    edit.
  - [ ] 0.2 Baselines: `make test-unit`, `make test-component` (passed/failed/skipped).
  - [ ] 0.3 Re-measure F17 with `measure_module` (`live_runtime_reconcile.py`,
    `RuntimeReconciler`, `live_startup_reconcile.py`, `live_session_runner.py`,
    `live_session_steady_state.py`) — confirm nothing has drifted since drafting.

- [ ] **Task 1 — Measure before building (probes in `/tmp/p48/`, not committed) — the gate**
  - [ ] 1.1 F4/F5(a) re-confirmed: seed a cached `ACCEPTED` order (`venue_order_id` set) against
    Story 4.3's real-engine harness (`test_live_runtime_reconcile_engine.py`'s shape), run a
    runtime cycle with the broker holding the corresponding position, and confirm the order is
    still `ACCEPTED` afterward and no stall occurs at this tier (re-proving F5(a), not assuming
    it). Record the exact statuses `stale_accepted_orders` should target
    (`ACCEPTED`/`PARTIALLY_FILLED`/`TRIGGERED` expected — confirm, do not assume).
  - [ ] 1.2 F12 re-confirmed on a stand-in IB-shaped client: a `generate_order_status_reports`
    call with one order whose `orderRef=""` raises `ValueError` for the **whole** call, not just
    that one order — confirm no partial-result path exists to salvage.
  - [ ] 1.3 D-D's construction: build a synthetic `OrderStatusReport(order_status=CANCELED, ...)`
    from a real cached `Order`'s own fields and hand it to a real `LiveExecutionEngine`'s
    `reconcile_execution_report`; confirm it lands as `OrderCanceled(reconciliation=True)` and the
    order's `status` becomes `CANCELED` — this is D-E's mechanism, proven once here before the
    module is built around it.
  - [ ] 1.4 The stall-reproduction attempt (D-G, RULED (A)): re-run Story 4.3's Task 1.5 probe
    once more, unchanged premise, and record whether anything has changed since (it should not
    have — no code between the two measurements touches this path). State the result plainly in
    the Debug Log: reproduced, or still not reproduced.
  - [ ] **Gate:** a result contradicting D-A/D-D/D-E's premises (e.g. 1.3 shows the engine refuses
    a synthetic `CANCELED` report for an `ACCEPTED` order for a reason not yet understood, or 1.1
    shows a status this design did not anticipate) reopens that decision **before** Task 3.
    Record, and ask before proceeding.

- [x] **Task 2 — PO rulings on D-C and D-G — RULED (Allay, 2026-09-28, Epic 4 retrospective)**
  - [x] 2.1 D-C ruled (A) — always `CANCELED`, disclosed caveat. D-G ruled (A) — re-confirm Story
    4.3's finding, do not attempt a full-kernel reproduction. Both recorded above the decisions
    they amend; Task 3 may proceed.

- [ ] **Task 3 — `src/core/live_stranded_orders.py` (new) — TDD, unit tier first**
  - [ ] 3.1 Red (`tests/unit/core/test_live_stranded_orders.py`, duck-typed doubles, no
    `nautilus_trader` engine): `stale_accepted_orders` filters correctly (status set, in-flight
    exclusion, no-`venue_order_id` exclusion); `broker_open_order_ids` returns `None` and never
    raises on any exception from the stubbed client; the `OrderStatusReport` builder produces a
    `CANCELED` report using only the order's own fields (AC #4's AST scan pinned here too); the
    debounce (`StaleOrderWatch`) acts only on two identical sightings ≥ `STALE_ORDER_DEBOUNCE_SECONDS`
    apart; `EMITTED_STALE_ORDER_EVENTS`'s dispatch-driven, two-directional pin (D-F).
  - [ ] 3.2 Green. Module docstring: owns/does-not-own, Why #1–#6 in miniature, D-B's "stays off"
    restated as a constraint this module does not touch.

- [ ] **Task 4 — Component tier, real engine (AC #1, #2, #3, #4, #5)**
  - [ ] 4.1 Red (`tests/component/core/test_live_stranded_orders_engine.py`, Story 4.2/4.3's
    IB-shaped `_Harness`/`_session_exec_config()` precedent): AC #1's two-cycle clear; AC #2's
    three exception shapes contained; AC #3's zero-call-when-nothing-to-check; AC #4's field-set
    and AST assertions; AC #5's per-instrument skip.
  - [ ] 4.2 Green.

- [ ] **Task 5 — Wire into `RuntimeReconciler` (AC #1 end-to-end, #6, #7)**
  - [ ] 5.1 Red: `RuntimeReconciler`'s `_run` calls the new module's orchestration function once
    per `SCOPE_RUNTIME` cycle only (never `SCOPE_RECONNECT`), contained (AR42 — an exception here
    must not stop position handling or the tick). Extend `test_live_runtime_reconcile_engine.py`
    and/or `test_live_runtime_reconcile.py` rather than duplicating Task 4's proofs.
  - [ ] 5.2 Green. Measure `RuntimeReconciler` immediately after wiring (F17: 77 → ?). If it grows
    past 100, move the tick-level glue to a module-level function first (the `_OkLog`/
    `_FailureStreak` precedent already in this file), then re-measure. Add or update the
    `SIZE_BASELINE` entry in the same commit, with a one-line reason, only if still needed after
    the split.
  - [ ] 5.3 Docstring: `live_runtime_reconcile.py`'s module docstring gains one paragraph on what
    this story adds and where it stops (D-A's cost paragraph, condensed).

- [ ] **Task 6 — Guard lists (same commit as the new module, CLAUDE.md Anti-Patterns)**
  - [ ] 6.1 `src/core/live_stranded_orders.py` added to `NODE_FACING_MODULES`
    (`tests/unit/core/test_live_node_never_exits.py`) and `TestImportPurity.MODULES`
    (`tests/component/core/test_session_runner_phases.py`), each with a reason. **Not** added to
    `STOP_PATH_MODULES` — add a comment there saying why (not reached from teardown; the
    `live_runtime_reconcile`/`live_broker_state` precedent).
  - [ ] 6.2 `FORBIDDEN_ORDER_METHODS`: confirm (do not assume) that `reconcile_execution_report` is
    absent from the set and that the new module calls nothing else order-mutating —
    `test_live_stop_path_is_inert.py`'s existing scan covers this automatically once
    `live_runtime_reconcile.py` (which now transitively reaches the new module) is on
    `LIVE_MODULE_GLOBS`'s glob; confirm it is, by running the suite, not by reading the glob.
  - [ ] 6.3 `_STDLIB_AND_FIRST_PARTY`: **run** `tests/integration/core/test_epic1_ac_node.py`; add
    any new stdlib import by hand if it fails.
  - [ ] 6.4 `test_exit_outcome_markers.py` passes without growing `UNMARKED` (this story raises no
    new typed failure the runner must classify — clearing is contained, never raised).
  - [ ] 6.5 Size caps: `tests/unit/governance/test_size_caps.py` green; any baseline change
    disclosed with a reason in the same commit.

- [ ] **Task 7 — Operator surface, docs, live verification**
  - [ ] 7.1 `README.md`/`live start --help`: one sentence on stranded-order clearing (cadence, what
    is logged, the `CANCELED`-only caveat from D-C).
  - [ ] 7.2 `docs/agent/nautilus.md`: a short addition under "Runtime reconciliation" (F1–F5, D-A
    through D-E) — point readers here from the "Startup reconciliation" section's existing note
    about the stranded-order history.
  - [ ] 7.3 **Procedure P21** in `docs/qa/phase3-live-verification.md`, after P20 (confirm P20 is
    still the highest before appending — F20; renumber if a parallel story also claims P21). It
    needs a session that genuinely strands an order (a resting limit order, manually cancelled in
    TWS while the session is stopped, or disconnected at the right moment) — **not read-only**, so
    it is **defined, not run** by the harness, the same as every other Epic 4 live procedure:
    (a) the order clears to `CANCELED` within two runtime cycles of the session confirming the
    broker no longer lists it, with one `reconcile.stale_order_cleared`; (b) a clean session with
    no resting orders shows zero `generate_order_status_reports` calls attributable to this check
    (AC #3, informational only — not directly observable from logs alone; note this honestly
    rather than overclaiming what the procedure can show); (c) if the operator can arrange a
    restart directly into a poisoned `p7-position-test`-shaped namespace, record whether
    `node:connect` still stalls — this is the only way F14/point 5's open question can ever be
    closed, and it may never be run.

- [ ] **Task 8 — Routed debt (`deferred-work.md`, new "Deferred from: story-4.8")**
  - [ ] 8.1 Strike the "stranded `ACCEPTED` order and the `p7-position-test` stall" item (carried
    since Story 4.2/4.3/4.5) with this story's disposition: the order-record half fixed; the
    `node:connect`-stall half still unconfirmed, owner unchanged (the first operator run of
    P17b/P18/P19b/P21 that reproduces it).
  - [ ] 8.2 Strike the "An `ACCEPTED` order filled while disconnected … mid-run: not fixed" item
    (Story 4.3 review, carried through 4.5) with this story's disposition per the D-C ruling.

- [ ] **Task 9 — Mutation sweep (apply, run the target tests, restore; record kill/survive)**
  M1 debounce removed (act on one sighting). M2 the `try/except` around
  `generate_order_status_reports` removed. M3 the "skip when no candidates" check removed
  (unconditional call). M4 the per-instrument outstanding-issue skip removed. M5 `order_status`
  built as `FILLED` instead of `CANCELED`. M6 the re-read verification removed (log before
  confirming it took). M7 `STOP_PATH_MODULES`/`NODE_FACING_MODULES` membership dropped. M8 the
  `SCOPE_RECONNECT` exclusion removed (runs on reconnect cycles too). M9 the raw exception text
  logged instead of `type(exc).__name__` (NFR26).

- [ ] **Task 10 — Gates**
  - [ ] 10.1 `uv run ruff format .`, `uv run ruff check .`, `make typecheck`.
  - [ ] 10.2 `make test-unit`, `make test-component`, `tests/integration/core/ --forked`.
  - [ ] 10.3 `git diff --stat` shows nothing outside this story's file list touched; the D-B
    zero-diff claim (no `live_node_builder.py` open-check lines) holds.

## Dev Notes

### The current surface — exact extension points

- `src/core/live_runtime_reconcile.py`: `RuntimeReconciler.__init__` (`:195-218`) — compose the
  new module's watch object here; `_run` (`:250-272`) — one contained call after `_act` succeeds,
  gated on `scope == SCOPE_RUNTIME`.
- `src/core/live_startup_reconcile.py`: **not touched** by this story (D-A) — cited only so the
  dev agent does not go looking for a startup-side hook that this story deliberately does not add.
- `src/core/live_broker_state.py`: `find_ib_exec_client` (`:256-268`) — reuse, already imported by
  `live_runtime_reconcile.py`.
- Guard lists: `tests/unit/core/test_live_node_never_exits.py:42-118` (`NODE_FACING_MODULES`),
  `tests/component/core/test_session_runner_phases.py:1621-1700+` (`TestImportPurity.MODULES`),
  `tests/unit/core/test_live_stop_path_is_inert.py:38-58` (`STOP_PATH_MODULES`, and why
  `live_runtime_reconcile.py`/`live_broker_state.py` are absent from it — the precedent this
  story's module follows), `:127-136, 417-426` (`FORBIDDEN_ORDER_METHODS`).
- Existing test infrastructure to reuse, not duplicate:
  - `tests/component/core/test_live_runtime_reconcile_engine.py`: `_session_exec_config()`,
    `_advance()`, the IB-shaped NETTING client — the nearest harness for Task 4's component tests.
  - `tests/component/doubles/test_live_node.py`: `_TestCache.orders_open`/`orders_inflight`/
    `orders()`, `_TestEngine.reconcile_execution_report` (`:246-312`, `:102-140`) — will need a
    stand-in order object exposing `status`/`venue_order_id`/`client_order_id`/side/quantity/
    `filled_qty`/`avg_px`, and `_TestExecClient` will need a stubbable
    `generate_order_status_reports`; both additions must be **defaulted** (`_TestCache`/
    `_TestEngine` are shared by `test_live_check_driver.py`, `test_epic1_ac_cli.py`,
    `test_epic1_ac_data.py` — every addition must leave those green unmodified, the Story 4.3
    hazard note).
  - `tests/component/core/test_live_startup_reconcile_engine.py`: `_IBShapedClient`, the subprocess
    abort-canary shape — the nearest precedent for a stand-in exec client with a stubbable
    `generate_order_status_reports`.

### Scope boundaries — do NOT build

- No startup-phase call (D-A) — `live_startup_reconcile.py` and `_phase_reconcile` are zero-diff.
- No change to `open_check_interval_secs`/the native consistency check (D-B) — a hard constraint,
  not a design choice this story revisits.
- No attempt to infer or fabricate a `FILLED` event (D-C, RULED (A) — always `CANCELED`).
- No cache surgery, no re-attribution of a synthetic fill to a strategy — Story 4.2's PO ruling on
  this stays refused; this story does not reopen it.
- No new exit code, DB column, migration, `SessionRecordPort` method, or CLI surface.
- No change to `RECOVERING`/reconnect-cycle behaviour (D-A) — Story 4.3's D-F grant conditions are
  untouched.

### Hazards (each has bitten a previous Epic 4 story, or will)

- **The F12 landmine is a whole-call failure, not a per-order one.** A defensive wrapper around
  the *whole* `generate_order_status_reports` call is required; wrapping only the parse of one
  order does not help, because the exception is raised and propagates before the caller ever sees
  a partial list (confirmed, Task 1.2).
- **Guard lists after a new module** (CLAUDE.md): added in the creating commit, not discovered
  later. `STOP_PATH_MODULES` exclusion needs its own comment, the `live_broker_state`/
  `live_runtime_reconcile` precedent — do not just leave it silently absent.
- **`RuntimeReconciler`'s 100-cap headroom is only 23 statements** (F17) — budget before editing;
  if the wiring does not fit, move it to a module-level function first (the `_OkLog`/
  `_FailureStreak` shape already in the file), the same discipline Story 4.3's D-I used for the
  runner file.
- **A test that cannot fail** (Epic 2 retro, restated every story since): AC #3's "zero calls"
  needs a sibling proving the stub records calls when there *is* a candidate; AC #2's "contained"
  needs a sibling proving an uncontained exception would have failed the test.
- **`TestLiveNode`'s doubles are shared** — every addition to `_TestCache`/`_TestEngine`/
  `_TestExecClient` must be defaulted so unrelated test files stay green unmodified.
- **The engine's clock is injected in the existing harness; drive it, never sleep**, for the
  debounce proofs (the `live_runtime_reconcile` precedent).

### Testing standards summary

TDD, red recorded first. Unit: the new module's pure/duck-typed logic (filtering, the report
builder, the debounce, the dispatch-driven event pin). Component: the real `LiveExecutionEngine` +
`Cache` (no `TradingNode`, the C-logging guard fixture) against an IB-shaped stand-in client, and
the runner-level wiring through `TestLiveNode`. Integration (`--forked`): only the existing
`test_epic1_ac_node.py` scan, run to confirm `_STDLIB_AND_FIRST_PARTY` needs no edit. **No tier in
this story constructs a real `TradingNode`/`NautilusKernel`** (F15) — the `node:connect`-stall
question (point 5, D-G) is explicitly **not** provable in a dev-story session (NFR32/NFR33's
"informational, never a gate" pattern, same as every prior Epic 4 story's live procedure) and is
named as Procedure **P21**, defined but not run, for the first operator session that can attempt
it.

### Project Structure Notes

- New: `src/core/live_stranded_orders.py`, `tests/unit/core/test_live_stranded_orders.py`,
  `tests/component/core/test_live_stranded_orders_engine.py`.
- Modified: `src/core/live_runtime_reconcile.py` (the composition + one call site + docstring
  paragraph), test doubles (`tests/component/doubles/test_live_node.py`), guard-list files
  (`tests/unit/core/test_live_node_never_exits.py`, `tests/unit/core/test_live_stop_path_is_inert.py`
  — comment only, `tests/component/core/test_session_runner_phases.py`), `tests/unit/governance/
  test_size_caps.py` (only if `RuntimeReconciler` needs a baseline entry after Task 5.2's measure),
  `README.md`, `docs/agent/nautilus.md`, `docs/qa/phase3-live-verification.md` (Procedure P21),
  `deferred-work.md` (new "Deferred from: story-4.8" section).
- Zero-diff, explicitly: `src/core/live_startup_reconcile.py`, `src/core/live_node_builder.py`'s
  open-check block, `src/core/live_session_runner.py`, `src/core/live_session_steady_state.py`,
  `src/models/position_reconciliation.py`, every strategy under `src/core/strategies/`,
  `src/db/**`, `alembic/**`, `src/services/**`, `src/api/**`, `templates/**`.

### References

- Retro origin: PO ruling (Allay, 2026-09-28, Epic 4 retrospective) — "fix the stranded ACCEPTED
  order and the p7-position-test stall" (no `epics.md`/PRD line; this story's own charter).
- `_bmad-output/implementation-artifacts/4-3-keep-runtime-state-aligned-with-the-broker.md`
  (F1–F15, D-A–D-K, Task 1.1b/1.5, the "stays off" 1A ruling; the style/register this story
  matches).
- `_bmad-output/implementation-artifacts/4-5-resume-a-strategy-mid-position.md` (D-A/D-C/D-D, the
  "mid-run: not fixed" disposition this story closes the order-record half of).
- `_bmad-output/implementation-artifacts/deferred-work.md:3406-3410, 3477-3488, 3573-3580, 3520
  (code review of 4.3), 3576-3580 (story-4.5)` — every prior mention of this story's subject.
- `docs/qa/phase3-live-verification.md` — P17 (`:2009`), P18 (`:2110`), P19 (`:2218`), P20
  (`:2364`); the "poisoned namespace" notes at `:1242-1243, 1337-1338, 1428`.
- `docs/agent/nautilus.md` — "Startup reconciliation", "Runtime Reconciliation" sections this
  story extends.

## Change Log

| Date | Change |
| ---- | ------ |
| 2026-09-28 | Story drafted (research + create-story), ready-for-dev. D-C (CANCELED-only vs. inferring FILLED) and D-G (scope of the stall-reproduction attempt) are open PO rulings, recorded above the decisions they gate. Every cited file:line, including F13's kernel sequence, was re-read directly against the current tree/wheel at drafting; none is carried forward unverified. |
| 2026-09-29 | D-C and D-G ruled by Allay (both (A), as recommended): stranded orders always resolve to `CANCELED` with a disclosed caveat, never a fabricated `FILLED`; Task 1 re-confirms Story 4.3's existing stall-reproduction finding and does not attempt a full-kernel harness. Task 2 marked done. No open PO rulings remain — Task 3 may proceed. |
