# Story 4.7: Absorb Corporate Actions Through Broker-Authoritative State

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a split or dividend during a multi-week session to be picked up and made visible,
so that a position size changing underneath me is an observation rather than a silent corruption.

## Why this story is shaped the way it is

Read this before designing anything. The epic text reads as if absorption were free: "the broker
is authoritative, so its view simply wins." Today it does not win for the case this story exists
for. Measured at drafting (2026-09-28, installed `nautilus-trader 1.220.0`, head `a42cfc3`). Wheel
paths are relative to `.venv/lib/python3.11/site-packages/nautilus_trader/`. Ripgrep skips `.venv`
(gitignored), so search it with `grep -n` on explicit paths.

**1. Today, a split on a position a strategy holds stops the session.** Stories 4.2 and 4.3 refuse
any row where a strategy's own net differs from the broker's (`strategy_quantity != 0 and
strategy_quantity != broker_quantity`, F1). A 2:1 split turns a strategy's +10 into the broker's
+20, which is exactly that shape. The session refuses to start, or is stopped mid-run. Both
stories disclosed this cost and handed it to this story by name: *"Cost of (A), disclosed: a
corporate action on a held position stops the session. Story 4.7 ('absorbed without manual
intervention') and Story 4.5 (strategy adoption) own relaxing it, with a named test"* (4.3 D-D;
4.2 D-D). So AC #1 is **false today** for the most common corporate action.

**2. Nautilus cannot adjust a strategy's position, so "absorbed" has a fixed meaning.** 1.220.0
has no corporate-action event and no position-adjustment event (grep for `PositionAdjusted` or
`corporate` finds nothing, F2). Every correction the framework makes is a fill attributed to a
synthetic owner (`EXTERNAL` / `INTERNAL-DIFF`). Under NETTING, that fill lands in the synthetic
owner's own position and **never** in the strategy's (F2). So the only honest meaning of "absorbed
from the broker's view" is this: the account's net goes broker-ward through the existing framework
path, and the strategy keeps its own lot. The real question is **when that is safe**. That is
decision D-A, and it goes to the PO.

**3. Cash is already the broker's; what is missing is visibility.** The IB exec client's
account-summary push *is* the session's cash (F7). No local copy can drift from it while the
process runs. A dividend credited overnight is therefore absorbed natively the moment the session
reconnects. But nothing says it happened: the `accountSummary:<acct>` key the previous run left in
Redis is overwritten at connect, with no record of the before value and no timestamp (Story 4.6's
routed debt). That is D-B and D-C.

**4. For an exact split, the framework's reconciliation price comes out as zero.**
`calculate_reconciliation_price` solves `target_qty·target_avg = current_qty·current_avg +
diff·px`. Take 10 @ 200 becoming 20 @ 100: the cost basis is unchanged, so `px = 0`, and the
function returns `None` (F3). The engine then falls back to a quote, then to the current average,
then to a MARKET report with no price. Story 4.2 measured the no-quote, no-price path filling at
`0.0` and returning `True`. Task 1 re-measures it for the split shape, because a `False` return
would stop the session (`RESOLUTION_REFUSED`).

**5. Story 4.5 runs in parallel and owns the hazards next door.**
- A mid-position restart already leaves the synthetic triple: strategy +10, `EXTERNAL` +10,
  `INTERNAL-DIFF` −10 (F6).
- `sma_crossover` closes *every* opposite-side position on its instrument, synthetic ones included
  (F5). That is an NFR14 hazard routed to 4.5.
- A restart after the broker position **shrank** aborts the process inside Nautilus with a Rust
  panic (1.4S, routed to 4.5 as HIGH). A reverse split reaches it.

This story must not touch `src/core/strategies/` (a parallel-edit collision, and 4.5's scope). It
must say plainly which of those hazards its absorption reaches.

### Measured facts — cite, don't re-derive

Task 1 re-measures F3, F8 and F9 against real objects before production code is written.

| # | Fact | Where |
|---|---|---|
| F1 | `PositionDiscrepancy.strategy_contradicted` is `strategy_quantity != 0 and strategy_quantity != broker_quantity`. `strategy_quantity` is the net of **every** non-synthetic owner. `kind` is `strategy_position` when contradicted, else `position`. `compare_positions` emits a row when `local != broker` **or** contradicted. Startup refuses every contradicted row before writing anything; runtime refuses after the 60 s debounce (holding back only rows a lookup miss could be masking). | `src/models/position_reconciliation.py:115-127, 130-168`; `src/core/live_startup_reconcile.py:271-275`; `src/core/live_runtime_reconcile.py:267-293` |
| F2 | There is no corporate-action or position-adjustment type anywhere in the wheel. The NETTING position report's correction is an `OrderStatusReport` reconciled with `is_external=False` onto `INTERNAL-DIFF` (`live/execution_engine.py:1441-1597`, `:1717`). The netting position id is `f"{instrument_id}-{strategy_id}"` of the **fill** (`execution/engine.pyx:1354-1355`). Measured by Story 4.2 (1.1): a flat report against strategy +22 leaves the +22 and adds `INTERNAL-DIFF −22`. | as cited; `4-2-…md:672-690` |
| F3 | `calculate_reconciliation_price` returns `None` when the target average is absent or 0, or when the solved price is `<= 0` (`live/reconciliation.py:62-89`). An exact split therefore gives `None`. The fallbacks, in order: the cached quote's ask/bid; else the current positions' weighted average; else a MARKET `OrderStatusReport` with `avg_px=None` (`live/execution_engine.py:1526-1593`). The call returns `True` either way. With IBKR's commission-inclusive average (4.1 D-G), the solved price is instead a small **positive** number, e.g. 0.10. | as cited |
| F4 | `Strategy.close_position(position)` submits a reduce-only market order for `position.quantity` with `position_id=position.id` (`trading/strategy.pyx:1245-1303`). A fill whose order carries a cached position id goes to **that** position, even under NETTING (`execution/engine.pyx:1266-1286`). | as cited |
| F5 | `sma_crossover` reads `cache.positions(venue, instrument_id)` unfiltered by strategy, and `close_position()`s every opposite-side open position, synthetic ones included. `sma_momentum` reads the portfolio's net (`is_net_long/short/flat`), which includes synthetics. Both are routed to Story 4.5. | `src/core/strategies/sma_crossover.py:224-298`; `sma_momentum.py:125-173`; `deferred-work.md:3347-3362` |
| F6 | **1.4A:** strategy +10 against a broker at +10 leaves the triple S +10, `EXTERNAL` +10, `INTERNAL-DIFF` −10. The IB adapter fabricates a `FILLED` order report keyed `client_order_id = instrument id`, and the framework imports it. **1.4S:** a later restart after the broker **shrank** makes `_generate_order_updated` underflow `leaves_qty`, and the process dies in a Rust panic inside `node:connect`. That is pinned by `TestTheShrunkReEntryAbortCanary` and routed to 4.5 (HIGH). | `4-2-…md:678-690`; `tests/component/core/test_live_startup_reconcile_engine.py:716`; `deferred-work.md:3363-3373` |
| F7 | Cash. The IB exec client's `_on_account_summary` keeps the tags per currency in memory and emits an `AccountState` (whose `total` is `NetLiquidation`, or a literal `400000`, not cash). It also writes `accountSummary:<account>` into the general cache and logs the whole dict at INFO. `read_broker_state` reads `TotalCashValue` from the in-memory dict, which is fresh at connect. | `adapters/interactive_brokers/execution.py:795-846`; `src/core/live_broker_state.py:25-30, 528-560` |
| F8 | The cache as it stands before `run_async`. `ExecutionEngine.load_cache()` runs at kernel init: `cache_general`, `cache_currencies`, `cache_instruments`, `cache_accounts`, `cache_orders`, `cache_positions` (`execution/engine.pyx:700-770`; `system/kernel.py:455-456`). So immediately before `run_async`, `cache.get("accountSummary:<acct>")` should be the **previous run's** summary, and `cache.account(account_id)` its restored account. `capture_local_positions` is already called at exactly that instant. | `src/core/live_session_runner.py:577-580` |
| F9 | Story 4.6's view reads the summary through `adapter.load()` (the general map) and records no time. `LOAD_METHODS = ("keys", "load_position", "load")` is pinned, and so is the absence of any mutator. `CacheDatabaseAdapter.load_account` exists in 1.220.0. The load-only property was measured with Redis `MONITOR` (`tests/integration/core/test_live_session_view_redis.py`). | `src/core/live_session_view.py:88-93, 283-297, 377-395` |
| F10 | Sizes (`measure_module`), with no headroom anywhere on the runner:<br>• `live_session_runner.py`: file **485/500**; `LiveSessionRunner` **412**, baseline 412, so any growth needs a baseline edit with a reason.<br>• `live_startup_reconcile.py` 257, `reconcile_at_startup` 37.<br>• `live_runtime_reconcile.py` 270.<br>• `position_reconciliation.py` 73.<br>• `live_session_view.py` 211, `_read` 14.<br>• `live_reconcile.py` 189, `_discrepancy_fields` 16. | `tests/unit/governance/test_size_caps.py` |
| F11 | `log_discrepancy` writes `scope`, `instrument_id`, `kind`, `resolution`, and `local_quantity` / `strategy_quantity` / `broker_quantity` as `str(Decimal)`, plus `reason` when refused. It logs WARNING for `framework` / `broker`, and ERROR for `refused` / `unresolved`. `_framework_resolved(local_before, …)` is the only thing that makes the framework's own startup correction visible, and it needs the pre-run snapshot. | `src/core/live_startup_reconcile.py:315-326, 433-456` |
| F12 | The current rule is pinned in 9 test files (22 occurrences). Every existing refused case (S +22 against B 0; S +22 against B +10; S −5 against B +5) is **still refused** under D-A (A). No existing test asserts that same-direction growth is refused. That is the missing named test 4.2/4.3 asked for. | `grep -rn "strategy_contradicted\|strategy_position" tests/` |
| F13 | The live-path stdlib scan (`_STDLIB_AND_FIRST_PARTY`, `tests/integration/core/test_epic1_ac_node.py:457`) already lists `datetime`, `json` and `decimal`, so D-B's imports trip nothing. | as cited |

### PO rulings (Allay, 2026-09-28) — binding

- **D-A: (A), the coverage rule.**
  - Refuse only when the broker holds less than the strategy believes, holds nothing, or holds the
    opposite side. Everything else is absorbed broker-ward.
  - The absorbed change is logged once as a reconciliation event naming the instrument and the
    before and after quantities.
  - **Never** adjust, close or flatten the strategy's own position. **Never** write a zero-price
    adjustment fill (option D stays rejected).
  - Reverse splits, cash mergers and symbol changes stay refused, and **the refusal message names
    the likely cause**.
  - Record the costs (split-spanning P&L at unadjusted prices; the reconciliation-owned extra
    shares that are never recorded as a trade) in the Dev Agent Record **and** in the docs.
  - The three-position startup state and `sma_crossover`'s close-every-position hazard are Story
    4.5's. Cross-reference them; do not fix them here.
  - Add a test that a strictly-shrinking, zero, or opposite-side broker quantity is still refused.
- **D-B: (A).**
  - `reconcile.cash_changed` is informational only. It never refuses a start, never blocks a phase,
    and never touches trading permission.
  - It is in the sanctioned `reconcile.*` namespace. **Pin it the way the other emitted-name sets
    are pinned: against the code, not against a second hand-written list** (the
    `TestEveryDispatchedRecordNameIsPinned` precedent).
  - The "before" cash must come from existing state. If it would need a new column or migration,
    **stop and escalate**: Epic 4 owns no schema change.
- **D-C: (A).**
  - `live reconcile` reports "session cash as of <time>" from a read-only Redis account lookup.
  - `load_account` joins the pinned read-only list deliberately, in both the constant and its
    membership pin.
  - Re-run the local Redis read-only check and record the result.
  - `live reconcile` stays report-and-exit, with the existing exit codes.
- **Docs:**
  - Write the NFR33 corporate-action procedure (continuing the P-numbering) as "defined, not run".
  - Record the adjusted-backtest vs unadjusted-live divergence as an expected structural property,
    to be carried into `session_conditions` in Epic 5.

### Design decisions (disclosed here, not discovered in review)

- **D-A — ✅ Ruled (A) (PO, 2026-09-28). When a strategy's own position differs from the broker's,
  what is absorbed?** *(Narrowed at code review, 2026-09-28, PO option 1: strategies on **both
  sides** of one instrument keep the pre-4.7 equality rule — `strategy_mixed_sides`, its own
  cause `CAUSE_MIXED` — because coverage is judged on the strategies' net and a net cannot tell
  whether each lot is covered. The coverage rule below governs single-side rows. Amended here
  by PR #35's code review, P8; the "Resolution" paragraph under the review findings records it.)*
  - **(A) Recommended: the coverage rule.**
    - `strategy_contradicted` becomes: `S != 0` **and** the broker does not cover `S`. "Not
      covered" means `B` is flat, `B` has the opposite sign, or `|B| < |S|`. In code:
      `S != 0 and (S > 0 and B < S or S < 0 and B > S)`.
    - Same-direction growth becomes an ordinary `kind="position"` row. That covers a forward split,
      a stock dividend, and, by the same arithmetic, a manual TWS add.
    - At startup, the framework's own pass (or the phase's correction) moves the net broker-ward
      into a synthetic owner. At runtime, the existing cycle does the same after its debounce.
    - Either way, one `reconcile.discrepancy` names the instrument with before (`local_quantity`)
      and after (`broker_quantity`).
    - The strategy keeps its own lot.
    - Shrink, flat and flip stay refused. That covers a reverse split, a cash merger and a symbol
      change. This is the NFR14 floor.
    - **Why it is safe.** If the strategy closes a covered `S`, the broker is left at `B − S`,
      which has the same sign as `B`. The close can never carry the account through zero into a
      position nobody asked for. That is exactly the hazard D-D was ruled for: "a real SELL 22
      against a flat account".
    - **It is strictly a relaxation.** Every row refused under (A) is refused today.
    - **Costs, disclosed:**
      1. **Trade-record P&L.** A lot held across a split records its round trip at unadjusted
         prices: entry before the split, exit after it. A 2:1 split reads as a ~50 % loss on the
         strategy's lot. The split's extra shares sit in a synthetic position, which the recorder
         never persists (Story 3.6 D-D). This is the price-basis property AC #3 records as
         structural. It is routed to Epic 5 (Story 5.4's `price_basis`, plus a named note that the
         recorded P&L of a split-straddling lot is unadjusted).
      2. **The synthetic-triple hazard.** At startup, absorption can reach the 1.4A state that every
         mid-position restart already reaches (F6), including `sma_crossover`'s close-everything
         hazard (F5). This is not a new hazard class; its fix is Story 4.5's.
      3. **Reverse splits.** A reverse split on a held position still stops the session, so the
         operator must intervene. After a prior mid-position restart, it can also meet 1.4S's
         in-Nautilus abort at the next start (F6, 4.5 HIGH).
      4. **Not corporate-action-specific.** A manual same-direction add in TWS is absorbed rather
         than stopping the session. The record names it either way.
  - **(B) Keep refuse-and-stop, and name the probable cause** in the refusal. AC #1 would then be
    unmet for every strategy-held position, so the PO would have to accept a documented deviation
    from the epic.
  - **(C) Absorb only a change shaped like a corporate action**, meaning the cost basis
    (`qty × avg`) is preserved within a tolerance. IBKR's average is commission-inclusive and
    Nautilus's is not (4.1 D-G), so the signature is never exact. This puts a tolerance into a
    safety decision, against FR36's "no close enough". Not recommended.
  - **(D) Rewrite the strategy's own position** with a strategy-attributed, zero-price adjustment
    fill. This fabricates an order and a fill the strategy never requested, outside the framework's
    reconciliation API. That is the shape Story 4.2's D-D rejected as its (C), and it violates
    NFR14. Rejected.
- **D-B — ✅ Ruled (A) (PO, 2026-09-28). Make a cash change visible (AC #1's "or the account's
  cash").**
  - **(A) Recommended: a startup `reconcile.cash_changed` record.**
    - The pre-run snapshot (D-D) also captures two things: the previous run's last-recorded
      `TotalCashValue` per currency, from the Redis-restored `accountSummary:<acct>` (F8),
      normalised by the **same** `_cash_from` both 4.1 and 4.6 use; and when it was recorded, from
      the restored account's last `AccountState` `ts_event`, or `None`.
    - After the broker read, the `reconcile` phase logs one INFO `reconcile.cash_changed` per
      currency whose value moved. Its fields are `scope="startup"`, `currency`, `before`, `after`,
      `difference`, and `recorded_at` (ISO-8601 UTC, or `None`).
    - A currency present on one side only is logged with that side `None`.
    - It is **not a discrepancy**. Nothing is resolved (the session's cash already *is* the
      broker's push), it never refuses a start, and it never raises.
    - It is not compared at runtime: a running session's cash is the broker's push, so no local
      copy exists to diverge.
    - There is no record when nothing moved, or when no prior cash exists (a fresh session, or
      Redis flushed).
    - A capture failure is contained. The existing `reconcile.local_snapshot_failed` covers it, and
      the phase proceeds with the before value unknown.
  - **(B) No new record.** Cash absorption is native and is only disclosed. AC #2's "visible rather
    than silent" then holds for positions only.
- **D-C — ✅ Ruled (A) (PO, 2026-09-28). Story 4.6's routed debt: "a session's local cash carries
  no timestamp" (`deferred-work.md` → Story 4.7).**
  - **(A) Recommended: close it in `live reconcile`.**
    - `SessionView` gains `cash_recorded_at: datetime | None = None`.
    - `live_session_view` reads it load-only through `CacheDatabaseAdapter.load_account(AccountId)`,
      as the account's last event's `ts_event`.
    - `ReconciliationReport` carries it. Each cash `reconcile.discrepancy kind=cash` record and the
      rendered cash note say **"session cash as of <ts>"**, so a dividend on a stopped session
      reads as "moved since <ts>".
    - `LOAD_METHODS` widens by exactly `load_account`. It stays pinned both ways, and the no-mutator
      scan is unchanged.
    - The Redis `MONITOR` integration test is re-run locally to prove the call is still load-only.
    - A missing account, or an account with no events, is `None` ("unknown"). It is never a
      failure: cash itself is optional there too.
  - **(B) Re-route it**, with the measurement recorded, to Epic 5 or as unowned.
- **D-D — Where the code goes (no ruling needed).**
  - The coverage rule is **one property**, `PositionDiscrepancy.strategy_contradicted`, which both
    phases already read. No caller changes. Its docstrings change in
    `position_reconciliation.py` (the module's "Two questions" paragraph),
    `live_startup_reconcile.py` (the D-D paragraph), `live_runtime_reconcile.py` ("What a confirmed
    row does") and `docs/agent/nautilus.md`.
  - The snapshot (if D-B (A) is ruled):
    - A stdlib-only domain value `LocalSnapshot(positions, cash, cash_recorded_at)` goes in
      `src/models/position_reconciliation.py`.
    - `capture_local_state(node, log) -> LocalSnapshot | None` replaces `capture_local_positions`
      in `live_startup_reconcile.py`, with the same contained-failure contract.
    - `reconcile_at_startup(..., local_before: LocalSnapshot | None)`.
    - **The runner changes zero net lines**: the import name, the attribute's annotation, and the
      call name, each in place. `LiveSessionRunner` stays ≤ 412. Measure it; do not assume.
    - `scripts/diagnostics/live_node_probe.py` is updated to the new name. It is not one of the
      forbidden kill/reconnect/connection-loss scripts.
  - **No new `src/core/live_*.py` module.** If the size budget forces one, the guard lists named in
    CLAUDE.md (`NODE_FACING_MODULES`, `STOP_PATH_MODULES`, `TestImportPurity.MODULES`) are updated
    in the same commit.
  - No strategy file, node-builder setting, phase order or real-money path is touched.
- **D-E — Records (AR41 `reconcile.*`; NFR26).**
  - Positions add **no new event**. AC #2's "explicit reconciliation event" is the existing
    `reconcile.discrepancy`:
    - `resolution="framework"` for a startup change the framework's pass absorbed, with the "before"
      coming from the pre-run snapshot;
    - `resolution="broker"` for one the phase or the runtime cycle corrected.
  - AC #2's before/after are `local_quantity` / `broker_quantity`, and `strategy_quantity` shows the
    strategy's lot is intact.
  - The only new name is `reconcile.cash_changed` (D-B (A)).
  - No raw account appears anywhere. The summary key embeds the raw account, and it is never logged
    or put into an exception.
- **D-F — Documentation (AC #3, AC #4).**
  - **AC #3.** Add a new `docs/agent/nautilus.md` section, "Corporate actions (Story 4.7)". It
    covers:
    - the coverage rule;
    - what a split does to a live session: a price cliff in live bars, the lot's unadjusted P&L,
      and the split shares held by a synthetic owner;
    - that a reverse split stops the session;
    - that cash is absorbed natively and named at startup;
    - the price-basis divergence: FirstRate backtest bars are split- and dividend-adjusted and
      consolidated, while IBKR live is unadjusted and SMART-routed. That divergence is **an expected
      structural property of the comparison, not a defect to chase**, carried into
      `session_conditions.price_basis = "ibkr_unadjusted_smart"` by Story 5.4.
  - Also add a `deferred-work.md` entry routed to Story 5.4 / Epic 5, naming the split-straddling
    P&L artifact.
  - **AC #4.** Add a "Phase-gate evidence index" near the top of
    `docs/qa/phase3-live-verification.md`. It maps each of the five categories to the procedures
    that evidence it and to each one's latest recorded result:
    - connectivity: P1, P5;
    - gate refusal: P2, P5;
    - a complete round trip: P11, P12, P13;
    - restart resume: P10, P14, P17, and Story 4.5's procedure (named, not numbered, because it is
      in flight in parallel);
    - reconciliation: P15, P16, P17, P18, and the new P19.
  - Append **P19** (corporate-action absorption), which is operator-only and opportunistic. Hold a
    position across a real split or dividend ex-date and read the records. It is "defined, not run"
    (a corporate action cannot be staged on demand, and the harness never holds positions). Use the
    Story 4.3 renumbering banner for parallel stories.

## Acceptance Criteria

The epic text (`epics.md:1678-1702`) is in **bold**. The clarifications under each are binding.
Clarifications marked *(if ruled)* apply only if the PO rules for the recommended option; the Dev
Agent Record states which rulings were applied.

1. **Given a corporate action changes a position's size or the account's cash at the broker, When
   reconciliation next runs, Then the change is absorbed from the broker's view without manual
   intervention (FR39).**
   - **Unit (the rule, D-A (A)).** A truth table on `PositionDiscrepancy`:

     | S | B | Contradicted? |
     |---|---|---|
     | +10 | +20 | no, `kind="position"` |
     | −10 | −20 | no |
     | +10 | +5 | **yes** |
     | +10 | 0 | **yes** |
     | +10 | −5 | **yes** |
     | −10 | −5 | **yes** |
     | −10 | +5 | **yes** |
     | +10 | +10 | no (and no row) |

     - `compare_positions` with strategy +10 and `EXTERNAL` +4 against a broker at +14 gives no
       row.
     - The four existing refused scenarios (C, D, the offset, the short) stay green, **unedited**.
   - **Component, real `LiveExecutionEngine` (the Story 4.2 / 4.3 harnesses), startup:**
     - A 2:1 split. The cache has strategy +10 @ 200, and the broker has +20 @ 100 (and separately
       @ 100.05, IBKR-style commission-inclusive).
     - `reconcile_at_startup` returns, and the cache's net is exactly the broker's.
     - The strategy's own position is still +10.
     - There is no `ReconciliationFailedError`, and the only engine mutation is
       `reconcile_execution_report`.
   - **Component, runtime:** the same split, driven through `RuntimeReconciler` across two cycles at
     least 60 s apart, is corrected broker-ward. `on_tick` does not raise, and a later clean cycle
     logs `reconcile.ok`.
   - **The NFR14 floor, same harnesses.** A reverse split (S +10 against B +5) still refuses the
     start with `STRATEGY_POSITION_CONTRADICTED`, and still stops a running session. Nothing is
     written before the refusal.
   - **Through `runner.run()`.** A runtime covered growth leaves the session running. The node is
     not stopped, `mark_stopped` is not called, and the heartbeat keeps ticking. Use the existing
     `test_session_runner_runtime_reconcile.py` harness. *(As proven: the correction is not what
     stops the session — a later runtime cycle comes back clean, nothing is refused or failed —
     and the run then ends the ordinary way, so `mark_stopped` **is** the last record call, from
     `run_seconds`, not from a refusal. The "not called" wording was too strong; the test asserts
     the stop is the ordinary end. PR #35 code review, P8.)*
   - **Cash.** The session's cash is IBKR's push (F7), and no code of this story writes cash
     anywhere. A scan or spy pins that D-B's capture only reads (`cache.get`, `cache.account`).
2. **Given such a change is absorbed, When it is logged, Then it appears as an explicit
   reconciliation event naming the instrument and the before/after quantities — visible rather than
   silent (FR36).**
   - **Startup.** The absorbed split logs exactly one WARNING `reconcile.discrepancy`, with
     `instrument_id="NVDA.NASDAQ"`, `kind="position"`, `resolution` ∈ {`framework`, `broker`} (as
     measured in Task 1), `local_quantity="10"`, `strategy_quantity="10"`, `broker_quantity="20"`,
     `scope="startup"`.
     - With `local_before=None` (snapshot failed), a framework-absorbed change produces no
       `framework` record. The existing `reconcile.local_snapshot_failed` WARNING is what keeps it
       from being silent. Pin that pairing.
   - **Runtime.** The same fields with `resolution="broker"` and `scope="runtime"`, logged once only
     after the re-read proves the correction took.
   - *(if D-B (A) is ruled)* Startup cash:
     - With a restored summary of USD 100000.00 recorded at T, and the broker read at USD 100123.45,
       the log has exactly one INFO `reconcile.cash_changed` with `scope="startup"`,
       `currency="USD"`, `before="100000.00"`, `after="100123.45"`, `difference="123.45"`,
       `recorded_at=T.isoformat()`.
     - Unchanged cash gives none. No restored summary gives none. A currency on one side only gives
       one record with that side `None`.
     - A capture that raises is contained and gives no cash record.
     - The record comes before `reconcile.ok`.
     - It never changes the phase's outcome: a failing log sink does not fail the phase.
   - *(if D-C (A) is ruled)* `live reconcile`:
     - A `SessionView` read from a namespace whose account has events carries `cash_recorded_at`.
     - The cash discrepancy record and the rendered report say "session cash as of <ts>".
     - No account, or no events, reads `None` / "unknown" and is never a failure.
     - `LOAD_METHODS == ("keys", "load_position", "load", "load_account")` is pinned.
     - The no-mutator scan is unchanged.
   - **NFR26.** In every new record and message, `grep` for the raw account is empty. Test with an
     account id whose tail differs from its mask.
3. **Given the divergence between adjusted backtest prices and unadjusted live prices, When the
   phase's documentation is written, Then it is recorded as an expected structural property of the
   comparison, not a defect to chase (carried into `session_conditions` in Epic 5).**
   - Write D-F's `docs/agent/nautilus.md` section, which states the property in those words and
     names `price_basis` / Story 5.4.
   - The Epic 5 routing entry is in `deferred-work.md`.
4. **Given the operator verification procedures, When `docs/qa/phase3-live-verification.md` is
   written, Then it documents the manual procedures for connectivity, gate refusal, a complete round
   trip, restart resume, and reconciliation — the phase-gate evidence that cannot live in CI (NFR33,
   AR22).**
   - The index names each of the five categories, the procedures that evidence it, and each one's
     latest recorded result (✅ / ⏳ defined-not-run), copied from each procedure's own Result log.
   - P19 is appended with preconditions, command, expected output, pass criteria and a Result log
     row. **No CI change and no deployment artifact** (AR22).

## Tasks / Subtasks

- [x] **Task 0 — Standing checks (record in the Debug Log)**
  - [x] 0.1 Head, clean tree; `uv run ruff check .` and `make typecheck` clean before the first edit.
  - [x] 0.2 Record the PO rulings for D-A / D-B / D-C at the top of the Dev Agent Record. Only the
    ruled options are built.
  - [x] 0.3 Re-measure F10 with `measure_module` for every file you will touch.
- [x] **Task 1 — Measure before building (component tier, real objects; results in the Debug Log)**
  - [x] 1.1 Real `LiveExecutionEngine` (the `test_live_startup_reconcile_engine.py` harness). Split
    S +10 @ 200 to broker +20 @ 100:
    - (a) on a cache holding only S;
    - (b) on the 1.4A triple.

    Record, for the correction path: the positions after; which resolution the phase logs; and the
    fill price (F3: an exact split gives `None`, so the fallback is taken).
  - [x] 1.2 The same split on the runtime harness (`test_live_runtime_reconcile_engine.py`): two
    cycles, then record the positions and records.
  - [x] 1.3 *(D-B)* Before `run_async`, on a cache loaded from a database holding a summary and
    an account, confirm that `cache.get("accountSummary:<acct>")` and
    `cache.account(exec_client.account_id).last_event.ts_event` return the stored values. Use the
    integration tier against local Redis if a double cannot hold them.
  - [x] 1.4 *(D-C)* Against local Redis, capturing with `MONITOR`: `load_account` issues only read
    commands, and its `last_event.ts_event` is the last `AccountState`'s. Record the `AccountId`
    string the IB exec client uses.
- [x] **Task 2 — The coverage rule (AC #1, #2; D-A). TDD: the truth table first, red.**
  - [x] 2.1 Unit truth table + the mixed-owner (`EXTERNAL` +4) case, red against today's rule.
  - [x] 2.2 Change `strategy_contradicted`. Existing refused scenarios stay green unedited (F12).
  - [x] 2.3 Update the docstrings (D-D). Update `_REMEDY` / `_CONTRADICTED` wording only if it now
    misdescribes the rule (it names "a strategy's own position disagrees with the broker"; say
    "the broker does not hold").
  - [x] 2.4 (PO ruling) The refusal message names the **likely cause** of a refused row. For a
    shrink, that is "a reverse split, a partial sale outside the session, or a lost fill"; for a
    flat broker, "a cash merger, a symbol change, or a close outside the session"; for the opposite
    side, "a trade outside the session". It is our own text, never broker text (NFR26). It is
    tested at startup and at runtime.
  - [x] 2.5 (PO ruling) The named test: a strictly-shrinking, a zero, and an opposite-side broker
    quantity are each still refused (model, startup and runtime).
- [x] **Task 3 — Startup absorption (AC #1, #2)**
  - [x] 3.1 Component tests from 1.1, as named tests: `test_a_forward_split_on_a_strategy_position_is_absorbed`
    and `test_a_reverse_split_on_a_strategy_position_still_refuses` (the named test 4.2 / 4.3
    asked for).
  - [x] 3.2 The record assertions from AC #2, including the `local_before=None` pairing.
- [x] **Task 4 — Runtime absorption (AC #1, #2)**
  - [x] 4.1 Component tests from 1.2; debounce respected; reverse split still stops.
  - [x] 4.2 Through `runner.run()`: a covered growth does not stop the session.
- [x] **Task 5 — Startup cash record (D-B (A), if ruled)**
  - [x] 5.1 `LocalSnapshot` + unit tests (stdlib-only pin on `position_reconciliation.py` stays
    green).
  - [x] 5.2 `capture_local_state` replacing `capture_local_positions`; contained failure; runner
    edits in place (0 net lines, measured); probe updated; existing tests migrated.
  - [x] 5.3 `reconcile.cash_changed` emission in `reconcile_at_startup` (before `reconcile.ok`); the
    AC #2 cases.
  - [x] 5.4 (PO ruling) The startup phase's emitted record names become one constant
    (`EMITTED_RECONCILE_EVENTS`), pinned **against the code**: drive `reconcile_at_startup` through
    every path, capture the record names, and assert that set equals the constant, in both
    directions. This is the `TestEveryDispatchedRecordNameIsPinned` precedent. Record it in
    CLAUDE.md's membership-pinned discipline notes.
  - [x] 5.5 (PO ruling) Confirm that the "before" cash comes from existing Redis state (the general
    `accountSummary` key and the `accounts:<id>` events). No column, no migration. If it did need
    one, stop and escalate.
- [x] **Task 6 — `live reconcile` cash timestamp (D-C (A), if ruled)**
  - [x] 6.1 `SessionView.cash_recorded_at`, `ReconciliationReport` field, render note, discrepancy
    field — unit + component.
  - [x] 6.2 `LOAD_METHODS` + the load-only pins; re-run
    `tests/integration/core/test_live_session_view_redis.py` locally (`--forked`), record the result.
- [x] **Task 7 — Docs (AC #3)**: the `nautilus.md` section; amend the Startup/Runtime
  Reconciliation sections' "a strategy's own position the broker contradicts" sentences to the
  coverage wording; the `deferred-work.md` Epic 5 entry.
- [x] **Task 8 — Docs (AC #4)**: the evidence index; P19.
- [x] **Task 9 — Debt dispositions (in `deferred-work.md` and the Completion Notes)**
  - [x] 9.1 "Local cash carries no timestamp" (4.6 → 4.7): closed by D-C (A), or re-routed per the
    ruling.
  - [x] 9.2 "Multi-currency cash would be returned partially" (4.1 → 4.7 "or whoever requests
    `$LEDGER`"): nothing here widens the account-summary tags; the startup record inherits the
    same single-currency read. Re-route to "the first change that widens the tags", unchanged
    owner rule, with that statement.
  - [x] 9.3 A new 4.5 note: the covered-growth absorption reaches the 1.4A triple (F5/F6) — add the
    split shape to 4.5's existing hazard entry rather than a new owner.
- [x] **Task 10 — Quality gates**
  - [x] 10.1 `uv run ruff check .`, `uv run ruff format --check .`, `make typecheck`.
  - [x] 10.2 `make test-unit`, `make test-component`, the touched integration files with
    `--forked`; size-cap guard; the guard-list tests; the inert-stop-path scans.
  - [x] 10.3 Mutation sweep (apply, run the targeted tests, restore; record kill/survive): (M1)
    revert the rule to `S != B`; (M2) drop the `|B| < |S|` clause (`S > 0 and B <= 0`); (M3) drop
    the sign clause; (M4) *(D-B)* emit `cash_changed` when unchanged; (M5) *(D-B)* swap before and
    after; (M6) *(D-C)* drop `load_account` from `LOAD_METHODS`.
- [x] **Task 11 — Live evidence (informational, never a gate)**
  - [x] 11.1 If a paper Gateway and the configuration are available, run only a **read-only** check
    (`ntrader live check`, or the P17a probe) and record it. Nothing in this story is observable
    read-only beyond what P15–P17a already cover. Record P19 as "defined, not run" with the reason.
    Never `live start`, never an order, never `--real-money`, `.env` or the diagnostic
    kill/reconnect scripts.

### Review Findings

Code review, 2026-09-28. Three parallel layers (Blind Hunter, Edge Case Hunter, Acceptance
Auditor) produced 44 raw findings. After de-duplication: 0 decision-needed, 19 patch, 4 defer,
1 dismissed. The Acceptance Auditor found **no violation of the PO's binding rulings**, and its
re-runs confirmed the test counts, sizes and scope claims.

- [x] [Review][Patch] **(HIGH) The docs say covered growth can "never carry the account through
  zero", but with the bundled `sma_crossover` it can at startup.** After a startup absorption the
  cache holds the triple S +10, `EXTERNAL` +20, `INTERNAL-DIFF` −10. `sma_crossover`'s unfiltered
  SELL then closes S **and** `EXTERNAL`: 30 shares against a broker at 20, leaving the account
  short 10 (4.5's hazard, which the PO ruled to cross-reference, not fix). The runtime path (S +10,
  `INTERNAL-DIFF` +10) does not oversell. Fix:
  - qualify the safety claim to "a strategy closing its *own* lot";
  - state the startup exception explicitly in `nautilus.md` and the README;
  - add an operator warning to P19's preconditions and correct P19 pass criterion 2.

  [docs/agent/nautilus.md "Why covered growth is safe"; docs/qa/phase3-live-verification.md P19]
- [x] [Review][Patch] **(MEDIUM) Coverage is checked on the aggregate of every strategy, which is
  wrong when strategies hold both sides of one instrument.** With A +10, B −5 and the broker at 7,
  the aggregate of 5 is "covered", yet A closing its 10 crosses the broker through zero. Fall back
  to the pre-4.7 equality rule when an instrument's strategy lots are on both sides. That keeps
  the change strictly a relaxation and leaves the PO's single-side ruling intact.
  [src/models/position_reconciliation.py:141-151, compare_positions]
- [x] [Review][Patch] **(MEDIUM) An unreadable account now fails the whole `live reconcile`.** A
  `load_account` raise becomes `UNREADABLE` (exit 1) over a field that only decorates the cash
  note. The startup twin contains the same failure. Contain it: the time is unknown, a WARNING
  record is logged, and the check still reports (PO note 3: report-and-exit).
  [src/core/live_session_view.py `_read`]
- [x] [Review][Patch] **(MEDIUM) After an absorption, a stop the session caused itself is blamed on
  an outside corporate action.** With S −10 against a synthetic +10 and a flat broker, the net
  agrees, yet the refusal reads "a cash merger, a symbol change…". When the net already matches
  the broker, the likely cause should also name the session's own orders against a
  reconciliation-owned position.
  [src/models/position_reconciliation.py `likely_cause`]
- [x] [Review][Patch] **(MEDIUM) `reconcile.cash_changed` claims more than it knows.** Two gaps:
  - Its "before" is the previous run's last summary *push*. IBKR pushes about every 3 min, so the
    session's own final fills or commissions can appear as "cash that moved while stopped".
  - A start that fails after connecting (a `gate:account` refusal, a failed broker read) has
    already let the exec client overwrite the stored summary, so the next start names nothing.

  Document both in the record's docstring, the README, `nautilus.md` and P19b. P19b's
  `live reconcile`-before-start step is the reliable view.
  [src/core/live_startup_reconcile.py `_log_cash_changes`; README; docs]
- [x] [Review][Patch] **(MEDIUM) Short-side splits are untested at engine tier.** For a
  commission-inclusive short the reconciliation price solves negative and falls back. Add
  real-engine tests for a short forward split at both prices. Also document that for an
  IBKR-style average the synthetic lot's fill price is the small solved price, which keeps the
  combined average equal to the broker's.
  [tests/component/core/test_live_corporate_actions_engine.py; docs/agent/nautilus.md]
- [x] [Review][Patch] **(LOW) The commission-inclusive (@ 100.05) split is not tested at startup**,
  as AC #1 asks. [tests/component/core/test_live_corporate_actions_engine.py]
- [x] [Review][Patch] **(LOW) The runtime stop text says "the broker no longer holds a strategy's
  own position"**, which is false for the shrink and opposite-side shapes. Reword to "does not
  cover". [src/core/live_runtime_reconcile.py `_CONTRADICTED`]
- [x] [Review][Patch] **(LOW) `ReconciliationReport` now accepts `broker_retrieved_at=None`.** The
  shared loop skips `None`. Keep the field required. [src/models/reconciliation.py `__post_init__`]
- [x] [Review][Patch] **(LOW) The cash note is false when session cash is unknown**, and the AC
  wording is "unknown" rather than "an unrecorded time".
  [src/services/reconciliation_service.py `render_report`]
- [x] [Review][Patch] **(LOW) P19's command greps the log immediately after backgrounding
  `live start`**, so the D6 check proves nothing. Wait for `phase=reconcile` to settle first.
  [docs/qa/phase3-live-verification.md P19]
- [x] [Review][Patch] **(LOW) The `runner.run()` split test asserts things that hold by
  construction.** Replace them with assertions AC #1 names: a clean runtime cycle after the
  correction (the session kept ticking), no refusal, and the ordinary end.
  [tests/component/core/test_session_runner_runtime_reconcile.py]
- [x] [Review][Patch] **(LOW) The emitted-name AST scan only sees `_emit(...)` calls.** A direct
  `log.<level>("reconcile.x")` would escape it, and a keyword `event=` would `IndexError`. Add a
  scan of every `reconcile.*` literal in the module.
  [tests/unit/core/test_live_startup_reconcile.py]
- [x] [Review][Patch] **(LOW) The trading-permission scan checks attributes only**
  (`getattr(x, "trading_permitted")` would pass it). Add string literals.
  [tests/unit/core/test_live_startup_reconcile.py]
- [x] [Review][Patch] **(LOW) The snapshot-failure pairing is not pinned in one test**, and the
  runner snapshot test would pass with a failed cash capture. Drive a failed capture into
  `reconcile_at_startup`, and assert no `local_snapshot_failed` in the runner test.
  [tests/unit/core/test_live_startup_reconcile.py; tests/component/core/test_session_runner_phases.py]
- [x] [Review][Patch] **(LOW) The NFR26 check was not run with a raw account whose tail differs from
  its mask**, as AC #2 asks. [tests/unit/core/test_live_startup_reconcile.py]
- [x] [Review][Patch] **(LOW) Stale wording of the old rule remains** in `live start --help`, the
  runner's `_phase_reconcile` docstring and the `STRATEGY_POSITION` comment.
  [src/cli/commands/live.py:365-371; src/core/live_session_runner.py:610;
  src/models/position_reconciliation.py:62]
- [x] [Review][Patch] **(LOW) The Dev Agent Record overstates or omits some facts.** Task 9.3 did
  not edit 4.5's existing entries. The D-A merge surface also includes `likely_cause` and three
  constants. M10 is unexplained. The `live_reconcile` exit-code test's docstring claims "nothing is
  written". [story file; deferred-work.md; tests/component/core/test_live_reconcile.py]
- [x] [Review][Patch] **(LOW) Evidence-index and AC #3 nits.** Cite P7's latest *recorded* result.
  State AC #3's phrase verbatim. `CAUSE_SHRANK` should read "a partial sale outside the session".
  [docs/qa/phase3-live-verification.md; docs/agent/nautilus.md; src/models/position_reconciliation.py]
- [x] [Review][Defer] **`load_account` replays every `AccountState` in `accounts:<id>`**, and its
  cost over a multi-week account is unmeasured on `live reconcile`'s 30 s path. It is the same list
  Nautilus itself loads at every kernel init (`cache_accounts`). [src/core/live_session_view.py
  `_read`] — deferred: P16's operator records `elapsed_ms` against a long-lived account.
- [x] [Review][Defer] **The relaxation also absorbs a broker position that leads or lags the
  session's own fill by more than the debounce.** The shapes: a fill arriving after a reconnect, or
  a lagging IB position read across ≥ 60 s. These used to stop the session and are now corrected,
  then re-corrected, broker-ward. [src/core/live_runtime_reconcile.py `_act`] — deferred: the
  debounce is Story 4.3's design, and strategy/broker ownership is Story 4.5's.
- [x] [Review][Defer] **Test isolation.** The real-engine harnesses end with
  `asyncio.set_event_loop(None)`, so any later file relying on the implicit loop fails depending
  on xdist scheduling. Story 4.7 fixed `test_live_order_recovery.py` locally.
  [tests/component/core/test_live_startup_reconcile_engine.py `_Harness.close`] — deferred,
  pre-existing: a conftest-level fix is the owner's call.
- [x] [Review][Defer] **A currency missing from a partial broker read would be named
  `after=None`.** Latent: only the base currency arrives today (4.1 F7).
  [src/models/position_reconciliation.py `cash_changes`] — deferred: joins the existing
  multi-currency entry, owned by the first change that widens the summary tags.
- *Dismissed (1):* the "new stdlib imports may trip `_STDLIB_AND_FIRST_PARTY`" finding. `json`
  and `datetime` are already listed, and the integration scan is green.

**Resolution (2026-09-28, PO chose option 1: fix automatically).** All 19 patches are applied:
- **Rule.** `strategy_mixed_sides` makes strategies on both sides of one instrument keep the
  pre-4.7 equality rule, with its own cause (`CAUSE_MIXED`). `CAUSE_NET_AGREES` names the
  session's own orders when the net already agrees. `CAUSE_SHRANK` now reads "partial sale".
- **Messages.** The runtime text says "no longer covers"; the stale "contradicts" wording is gone
  from `live start --help`, the runner, `live_cache.py` and `live_session_steady_state.py`.
- **Models.** `ReconciliationReport.broker_retrieved_at` is required again.
- **Cash note.** It says "an unknown time", and has its own wording when no cash was ever
  recorded.
- **`live reconcile`.** An unreadable account is contained: the time reads as unknown and
  `reconcile.session_view_account_unreadable` is logged at WARNING.
- **Real-engine tests.** The startup and runtime split tests are parametrized over long and short
  at exact and commission-inclusive prices (8 cases). A short with a commission-inclusive average
  falls back to the current average, as predicted.
- **Other tests.**
  - the runner split test asserts a clean runtime cycle after the correction;
  - the emitted-name pin adds a `reconcile.*` literal scan and a keyword-safe `_emit` scan;
  - the trading-permission scan checks string literals;
  - the snapshot-failure pairing is one test, and the runner snapshot test asserts no
    `local_snapshot_failed`;
  - NFR26 is tested with `DU7654321` against the `***626` mask.
- **Docs.**
  - `nautilus.md` corrects the safety claim to "its own lot", adds the startup `sma_crossover`
    caveat, the pricing formula for both sides and the cash caveats, and states AC #3's phrase
    verbatim;
  - README is updated to match;
  - P19 gains the ⚠️ precondition and a corrected criterion 2, waits for `phase=reconcile` before
    grepping, and has the cash caveats;
  - the evidence index's P7 row now carries its later rows;
  - `deferred-work.md` records the split path on 4.5's existing `sma_crossover` entry;
  - the Dev Agent Record is corrected (M3, M10, the merge surface, the Task 9.3 claim).
- **A consequence found and fixed.** The longer runtime message moved the CLI's line wrap into a
  phrase `tests/unit/cli/commands/test_live_cli.py` asserted on. That test now reads the output as
  words, and also asserts that the likely cause reaches the operator.
- **Mutants against the review's fixes:** M12, M13 and M14 were all killed (Debug Log).
- **Gates after the fixes:**
  - ruff and format clean; mypy: no issues in 111 source files;
  - unit **3194** passed;
  - component **1834** passed, 16 skipped (`-v -n auto`);
  - integration **325** passed, 2 skipped (`--forked`);
  - size-cap guard 14/14; runner 485 / `LiveSessionRunner` 412, unchanged.

## Dev Notes

### The current surface — exact extension points

- `src/models/position_reconciliation.py:115-117`: `strategy_contradicted`, the single edit point
  for D-A.
- `src/core/live_startup_reconcile.py:195-208` (`capture_local_positions`), `:234-302`
  (`reconcile_at_startup`), `:315-326` (`_framework_resolved`), `:433-456` (`log_discrepancy`),
  `:459-473` (`_log_ok`).
- `src/core/live_runtime_reconcile.py:267-293` (`_act`). **No edit is expected**: it reads the
  property.
- `src/core/live_session_runner.py:136, 261, 579, 620-625`: the snapshot wiring (D-D).
- `src/core/live_session_view.py:93` (`LOAD_METHODS`), `:283-297` (`_read`); `src/models/reconciliation.py:76-107`
  (`SessionView`), `:186-226` (`ReconciliationReport`); `src/services/reconciliation_service.py:96-165`;
  `src/core/live_reconcile.py:363-378` (`_discrepancy_fields`).

### Scope boundaries — do NOT build

- No edit to `src/core/strategies/`. Strategy adoption and the `sma_crossover` unfiltered read are
  Story 4.5's.
- No fix for 1.4S's in-Nautilus abort (4.5 HIGH).
- No runtime cash comparison.
- No `$LEDGER` / multi-currency widening.
- No corporate-action classifier or tolerance.
- No trade-record adjustment.
- No new exit code.
- No phase reorder.
- No node-builder setting change.
- No Epic 5 work (`session_conditions` is only *named*).

### Hazards (each has bitten a previous story, or will)

- **Size caps.** `LiveSessionRunner` is exactly at its 412 ceiling (F10). Measure after every edit.
  A baseline bump needs a one-line reason in the same commit.
- **Guard lists.** A new `src/core/live_*.py` module must be added to the CLAUDE.md lists in the
  same commit. The plan avoids creating one.
- **Duplicated literals need an equality pin.** If any new record name or constant is duplicated
  into a test, pin it by import.
- **A test that cannot fail.** The truth table must be seen red against today's rule before the
  change (Task 2.1). Record it.
- **Parallel Story 4.5.** It may also edit `position_reconciliation.py` or the reconcile modules.
  Keep the D-A diff to the one property, plus docstrings, so an integration merge is trivial. Say
  so in the Completion Notes.
- **P-numbering.** Other Epic 4 stories append procedures too. Use the renumbering banner.
- **NFR26.** The summary key and `AccountId` embed the raw account. Neither may reach a record.

### Testing standards summary

- Unit for the rule and the domain values. Component with the real `LiveExecutionEngine` harnesses
  for absorption. Runner-level through `runner.run()` with the existing doubles. Integration
  (`--forked`, local Redis) only for D-C's load-only proof. Never a real broker (NFR32).
- TDD: every new behaviour is seen failing first.

### Project Structure Notes

- All edits are in existing modules. The only new domain value is `LocalSnapshot`, in
  `src/models/position_reconciliation.py` (stdlib-only, pinned).
- AR38 holds: `src/models` and `src/services` stay free of Nautilus, and the runner never imports
  SQLAlchemy.

### References

- `_bmad-output/planning-artifacts/prd-epic4-scope.md` (Story 4.7; FR39; NFR14, NFR33; AR22, AR41)
- `_bmad-output/planning-artifacts/epics.md:1678-1702`; `prd.md:455-486` (price provenance,
  corporate actions)
- `_bmad-output/planning-artifacts/architecture.md:113, 124`; AR7 (`session_conditions.price_basis`)
- `_bmad-output/implementation-artifacts/4-2-…md` D-C / D-D / D-F; `4-3-…md` D-D; `4-6-…md` D-C,
  Task 11.4
- `deferred-work.md:3258-3262` (multi-currency), `:3289-3292` (cash timestamp), `:3347-3373`
  (4.5's triple, unfiltered read, 1.4S)
- `docs/agent/nautilus.md:294-405`

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), dev-story, 2026-09-28.

### Debug Log References

**PO rulings applied:** D-A (A), D-B (A), D-C (A), with the PO's notes (see "PO rulings" above).

**Task 0** — head `a42cfc3`, clean tree. `uv run ruff check .` gave "All checks passed!".
`make typecheck` gave "Success: no issues found in 111 source files". F10 re-measured, unchanged:
- runner file 485, `LiveSessionRunner` 412;
- `live_startup_reconcile.py` 257, `live_runtime_reconcile.py` 270;
- `position_reconciliation.py` 73, `live_session_view.py` 211, `live_reconcile.py` 189.

**Task 1 — measurements** (real `LiveExecutionEngine` via the Story 4.2 harness; local Redis 1.3/1.4):
- **1.1a** Split on a strategy-only cache (S +10 @ 200, broker +20 @ 100). Nautilus's own pass
  returns `True` and leaves `EXTERNAL +20 @ 100`, `INTERNAL-DIFF −10 @ 200`, S +10 @ 200: net 20,
  equal to the broker.
  - The only remaining row is the strategy check: `local 20, strategy 10, broker 20`. That is
    refused today and not a row under the coverage rule.
  - The pre-run snapshot row (`local 10` → `broker 20`) is what `_framework_resolved` names:
    `resolution="framework"`.
- **1.1b** Split on the 1.4A triple, meaning a second restart after a first mid-position one. The
  imported `EXTERNAL` order is updated 10 → 20 with **no abort** (growth does not reach 1.4S's
  underflow). The result is the same positions as 1.1a.
- **1.2** Runtime correction, no native pass:
  - An exact split (broker 20 @ 100 against S 10 @ 200) gives `reconcile_execution_report` → `True`
    and `INTERNAL-DIFF +10`, priced **200.0**. This is F3's fallback: the solved price is 0, which
    gives `None`, and with no quote it falls back to the current average.
  - An IBKR-style commission-inclusive 100.05 gives `True` and `INTERNAL-DIFF +10 @ 0.1`, the small
    positive solved price.
  - Neither refuses, and in both the strategy's own position stays +10 @ 200.
- **1.2 reverse** S +10 against broker +5 is a row under both rules (contradicted).
- **1.3** Local Redis. After writing an account (with an `AccountState`) and an `accountSummary`
  key through a real adapter, a **fresh** `Cache(database=adapter)` plus `ExecutionEngine.load_cache()`
  holds `cache.get("accountSummary:<acct>")` equal to the stored JSON bytes, and
  `cache.account(IB_ACCOUNT)` with its last event. This is the pre-run "before" the snapshot reads.
  No column and no migration: both are existing Redis keys (PO note (2) satisfied).
- **1.4** `CacheDatabaseAdapter.load_account(AccountId("INTERACTIVE_BROKERS-<acct>"))` returns a
  `MarginAccount` with its events (`last_event.ts_event` is the `AccountState`'s). The
  `TestEventStubs` state's `ts_event` is `0`, so `0` is treated as "not recorded", never the epoch.
  The load-only proof is Task 6.2's `MONITOR` re-run.
- Housekeeping: the two measurement files could not be deleted (`rm` is denied in this
  environment). They were renamed and rewritten as this story's real test files
  (`test_live_corporate_actions_engine.py`, `test_live_startup_cash_redis.py`).

**Task 2.1 — red against today's rule.** The model file was run before the rule changed, with only
the constants and a `likely_cause` stub returning `None`: 15 failed and 48 passed.
- The failures were exactly the two growth rows (`10 → 20` and `−10 → −20`), the three
  growth/mixed-owner `compare_positions` cases, and the ten `likely_cause` cases.
- Every kept-refused row passed, because it was already refused.
- After the change the file went green, with the four existing refused scenarios (C, D, offset,
  short) unedited.

**Task 10.3 — mutation sweep.** Each mutant was applied, the targeted tests run, and the file
restored. Restoration was checked with `cmp` against a pre-sweep copy for the three backed-up
files; M11 was reverted by a reverse Edit.

| # | Mutant | Result |
|---|---|---|
| M1 | Rule back to `S != 0 and S != B` | **killed**: 15 failures across unit, component (real engine) and `runner.run()` |
| M2 | Drop "broker holds fewer" (`S>0 and B<=0 …`) | **killed**: 12, including the PO's named reverse-split tests at startup and at runtime |
| M3 | Size only (`abs(B) < abs(S)`) | **killed**: 3. The original truth table could not have killed it; the rows `10 → −20` and `−10 → +20` were added just before M3 ran, once the gap was spotted |
| M4 | `cash_changes` emits unchanged currencies | **killed**: 4 |
| M5 | Swap `before`/`after` in `reconcile.cash_changed` | **killed**: 3, including through `runner.run()` |
| M6 | `load_account` dropped from `LOAD_METHODS`, still called | **killed**: 2 (the exact pin and the member scan) |
| M7 | Swap the flat and opposite-side likely causes | **killed**: 11 |
| M9 | The snapshot never reads the previous run's cash | **killed**: 10 |
| M11 | `cash_recorded_at` takes the *first* reported event, not the last | **killed**: 2 (the snapshot and the view) |

M8 was not run. Removing the `resolution == "refused"` guard on `likely_cause` is an equivalent
mutant, because a covered (non-refused) row has no cause. **M10 was never defined:** the
numbering skipped it, and nothing was dropped.

**After the code review, three more mutants against the review's own fixes**, each applied,
run and reverted by a reverse Edit:

| # | Mutant | Result |
|---|---|---|
| M12 | Drop the mixed-sides fallback to the equality rule | **killed**: 1 (`test_growth_over_a_mixed_net_is_refused`) |
| M13 | Drop the net-agrees suffix from `likely_cause` | **killed**: 2 |
| M14 | Un-contain the `load_account` read (let it raise) | **killed**: 2 (both parametrized cases) |

**Final gates (after the sweep):**
- `uv run ruff check .`: all passed. `uv run ruff format --check .`: 547 files already formatted.
- `make typecheck`: no issues in 111 source files.
- Unit: 3182 passed.
- Component (`-n auto`): 1828 passed, 16 skipped.
- Integration (`--forked -n auto`, whole tier): 325 passed, 2 skipped. That includes Story 4.6's
  Redis `MONITOR` load-only proof, now asserting the account read, and this story's
  `test_live_startup_cash_redis.py`, both against the local Redis.
- Size (`measure_module`): runner file **485** and `LiveSessionRunner` **412**, both unchanged
  (the D-D in-place edits). `live_startup_reconcile.py` 311 (`reconcile_at_startup` 39),
  `live_session_view.py` 225 (`_read` 23), `live_reconcile.py` 192,
  `position_reconciliation.py` 132. No baseline edit and no allowlist edit.

**A pre-existing test-isolation flake, fixed.** Under `pytest -v -n auto`,
`test_live_order_recovery.py` intermittently failed with "There is no current event loop".
- It reads the implicit current loop, and the Story 4.2/4.3 real-engine harnesses clear it on
  close (`asyncio.set_event_loop(None)`).
- Reproduced deterministically with Story 4.2's `test_live_startup_reconcile_engine.py` ahead of
  it in one process, independent of this story.
- This story's engine file is one more such predecessor, so the file gained an autouse fixture
  giving each test its own loop.
- Separately, the known Story 4.6 timing flake (`test_the_connect_deadline_never_outlives_the_budget`,
  already in `deferred-work.md`) failed once under load and passed alone (46/46).

**Task 11 — live.** `ntrader live check` (read-only, gate-checked) stopped at `Cannot build an
IBKR execution client: TWS_ACCOUNT is not set` in 0.00 s, with `gate:static ok` and no socket
opened. This worktree has no `.env` (confirmed by a file-name glob; nothing was read), and the
charter forbids creating one. P19 is recorded as **defined, not run**.

### Completion Notes List

- **PO rulings applied: D-A (A), D-B (A), D-C (A)**, with every note honoured.
- **D-A — the coverage rule** (`PositionDiscrepancy.strategy_contradicted`, one property that both
  phases read, with no caller change).
  - A strategy's own position is refused only when the broker holds less in its direction, none,
    or the opposite side. Forward splits and stock dividends are absorbed broker-ward. *(Single-side
    rows; strategies on both sides of one instrument keep the equality rule, `CAUSE_MIXED` — the
    code review's narrowing, recorded under D-A above.)*
    - At startup, Nautilus's own pass imports the difference. It is named from the pre-run
      snapshot (`resolution=framework`, before `local_quantity`, after `broker_quantity`).
    - At runtime, the cycle corrects it after its debounce (`resolution=broker`).
  - The strategy's own lot is never adjusted, closed or flattened. The real-engine tests assert
    its quantity, its average price and **its single fill**. No zero-price fill is written.
  - Reverse splits, cash mergers, symbol changes and opposite-side holdings stay refused. The
    refusal names the likely cause in the operator message and in the refused record
    (`likely_cause`). It is our own text (NFR26).
  - The PO's named test: `test_a_reverse_split_on_a_strategy_position_still_refuses` (real
    engine), plus shrinking, zero and opposite-side refusals at model, startup and runtime tier.
  - It is strictly a relaxation: every row refused under it was refused before. As a side effect,
    it removes a latent false stop, where a strategy long beside an `EXTERNAL` long on the same
    instrument would have stopped the session.
- **Costs recorded** (PO note (1)), in `docs/agent/nautilus.md` "Corporate actions" and in
  `deferred-work.md` "Deferred from: story-4.7":
  - a lot held across a split records its round trip at unadjusted prices (~−50 % on a 2:1);
  - the split's extra shares sit in a reconciliation-owned position that is never recorded as a
    trade, and is unmanaged once the strategy exits.
  - The three-position startup state, `sma_crossover`'s close-every-position hazard and 1.4S's
    shrink abort are **cross-referenced to Story 4.5, not fixed**. No file in
    `src/core/strategies/` was touched.
- **D-B — `reconcile.cash_changed`.** It is an INFO record at startup, one per currency that moved
  while the session was stopped, with `before`, `after`, `difference` and `recorded_at`.
  - The before value comes only from existing Redis state (PO note (2)): the restored
    `accountSummary:<acct>` general key and the account's last *reported* `AccountState`.
    Measured against a real Redis; no column and no migration.
  - It is contained end to end. A comparison failure or a failing log sink never blocks the
    phase, and the module has no trading-permission reference, which is scanned.
  - `EMITTED_RECONCILE_EVENTS` is pinned against the code both ways: an AST scan of every `_emit`,
    and the phase driven through every path. The NFR26 scan is parametrized from it.
  - It is recorded in CLAUDE.md's membership-pinned notes.
- **D-C — "session cash as of <time>" in `live reconcile`.** `SessionView.cash_recorded_at` is
  read load-only through `load_account`.
  - `ReconciliationReport.local_cash_recorded_at` carries it, into both the rendered note and the
    cash `reconcile.discrepancy` record.
  - `LOAD_METHODS` became four, in the constant and its exact pin together.
  - The `MONITOR` proof was re-run locally and now asserts the account read is inside it: read
    verbs only, the namespace byte-identical.
  - Still report-and-exit, still exit 5 on a discrepancy (tested).
- **Design detail beyond the story text:** `ACCOUNT_SUMMARY_KEY_PREFIX` moved from
  `live_session_view` to `live_broker_state`, which owns the account-summary normalisation. It is
  re-exported by import, so existing test imports are unchanged. `cash_recorded_at(account)` is a
  new duck-typed helper there, shared by D-B and D-C, with canaries on the real IB exec client and
  the factory's account-id composition.
- **Runner:** zero net lines. The import, annotation and call were renamed in place
  (`capture_local_positions` → `capture_local_state`, `_local_positions` → `_local_state`).
  `scripts/diagnostics/live_node_probe.py` was updated to match.
- **Docs:**
  - AC #3: `docs/agent/nautilus.md` gained "Corporate actions (Story 4.7)", including the
    price-basis property stated as "expected and attributable, not a defect to chase", routed to
    `session_conditions.price_basis` (Story 5.4).
  - AC #4: `docs/qa/phase3-live-verification.md` gained a "Phase-gate evidence index" covering the
    five categories with each procedure's latest result, and Procedure P19 (defined, not run). It
    carries the renumbering banner in case Story 4.5 also appends a P19.
  - README was updated (the refusal wording, corporate actions, `reconcile.cash_changed`,
    "session cash as of").
- **Debt dispositions:**
  - 4.6 → 4.7, "local cash carries no timestamp": **closed** by D-C.
  - 4.1 → 4.7, "multi-currency cash returned partially": **re-affirmed**, with the owner rule
    unchanged, because nothing here widens the tags.
  - New 4.7 entries route the split-P&L artifact and the unmanaged extra shares to Epic 5, and
    add the split path to 4.5's existing hazard entries. (Code review: that last part was not
    true at first. It is now done: the existing `sma_crossover` entry gained the split shapes.)
- **Parallel Story 4.5:** the merge surface in `position_reconciliation.py` was understated at
  first (code review). It is:
  - the rule (`strategy_contradicted`);
  - the new `likely_cause` property, and the `strategy_mixed_sides` field that `compare_positions`
    now fills;
  - five `CAUSE_*` constants;
  - `LocalSnapshot`, `CashChange` and `cash_changes`;
  - the docstrings.

  An integration merge with 4.5 must reconcile any edit it makes to the same class. Its procedure
  is named, not numbered, in the evidence index.
- **Guard lists:** no new `src/core/live_*.py` module. Its only new stdlib imports (`json`,
  `datetime`) were already on `_STDLIB_AND_FIRST_PARTY`, and the integration scan passed.

### File List

**New**
- `_bmad-output/implementation-artifacts/4-7-absorb-corporate-actions-through-broker-authoritative-state.md`
- `tests/component/core/test_live_corporate_actions_engine.py`
- `tests/integration/core/test_live_startup_cash_redis.py`

**Modified: source**
- `src/cli/commands/live.py` (review: `--help` wording)
- `src/core/live_cache.py` (review: docstring)
- `src/core/live_session_steady_state.py` (review: docstring)
- `src/models/position_reconciliation.py`
- `src/models/reconciliation.py`
- `src/core/live_startup_reconcile.py`
- `src/core/live_runtime_reconcile.py`
- `src/core/live_broker_state.py`
- `src/core/live_session_view.py`
- `src/core/live_reconcile.py`
- `src/core/live_session_runner.py`
- `src/services/reconciliation_service.py`
- `scripts/diagnostics/live_node_probe.py`

**Modified: tests**
- `tests/unit/cli/commands/test_live_cli.py` (review: wrap-insensitive message assertions)
- `tests/unit/models/test_position_reconciliation.py`
- `tests/unit/models/test_reconciliation.py`
- `tests/unit/services/test_reconciliation_service.py`
- `tests/unit/core/test_live_startup_reconcile.py`
- `tests/unit/core/test_live_runtime_reconcile.py`
- `tests/component/core/test_live_startup_reconcile_engine.py`
- `tests/component/core/test_live_session_view.py`
- `tests/component/core/test_live_reconcile.py`
- `tests/component/core/test_session_runner_phases.py`
- `tests/component/core/test_session_runner_runtime_reconcile.py`
- `tests/component/core/test_live_order_recovery.py`
- `tests/component/doubles/test_live_node.py`
- `tests/component/scripts/test_live_node_probe_reconcile.py`
- `tests/integration/core/test_live_session_view_redis.py`

**Modified: docs and tracking**
- `CLAUDE.md`
- `README.md`
- `docs/agent/nautilus.md`
- `docs/qa/phase3-live-verification.md`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (4-7 key only)

## Change Log

- 2026-09-28: Story drafted (create-story). Three decisions pending a PO ruling: D-A (absorption
  rule), D-B (startup cash record), D-C (`live reconcile` cash timestamp).
- 2026-09-28: PO ruled D-A (A), D-B (A), D-C (A), with notes (the refusal names the likely cause;
  the emitted-name pin is against the code; no schema change; `live reconcile` stays
  report-and-exit). Tasks 2.4, 2.5, 5.4 and 5.5 added.
- 2026-09-28: Implemented (dev-story):
  - the coverage rule and the likely cause;
  - the `LocalSnapshot` with the previous run's cash, and `reconcile.cash_changed`;
  - `EMITTED_RECONCILE_EVENTS`, pinned against the code;
  - "session cash as of" in `live reconcile` (`load_account`);
  - docs (the nautilus.md corporate-actions and price-basis section, the evidence index, P19,
    README, CLAUDE.md);
  - debt dispositions, and a test-isolation fix in `test_live_order_recovery.py`.

  Every gate is green, and the mutation sweep killed all 9 mutants. Status → review.
- 2026-09-28: Code review. Three parallel layers found 0 decision-needed, 19 patch, 4 defer and
  1 dismissed; the Acceptance Auditor found no violation of the PO's rulings. All 19 patches were
  applied (PO option 1), and three further mutants (M12–M14) were killed. The 4 deferred items are
  in `deferred-work.md`. Status → done.
