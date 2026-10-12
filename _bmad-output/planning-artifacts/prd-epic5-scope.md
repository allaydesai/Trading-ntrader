# PRD Extract — Phase 3, Epic 5 Scope Only

> **Why this file exists:** the harness's traceability gate (`_trace_gate` in
> `bmad_harness.py`) feeds the whole `prd_path` document to a reviewer and demands
> every acceptance criterion in it map to a passing test. The full `prd.md` covers
> all five Phase-3 epics (FR1–FR53); this run implements Epic 5 only (FR40–FR47, plus
> Stories 5.7 and 5.8 added from PR #35's review). Feeding the reviewer the full
> document makes the gate structurally unpassable — it always reports the other
> epics' criteria as unmet. This extract scopes the gate to what this run actually
> builds: Epic 5's functional requirements, the NFRs and architecture requirements
> that govern it, and the 5.1–5.8 acceptance criteria copied verbatim from `epics.md`.
> Precedents: `prd-epic1-scope.md` (2026-08-16), `prd-epic4-scope.md` (2026-09-22).
> See `planning/007-ntrader-p3-epic5-run.md` in the bmad_harness project.

## Scope Boundary

This run delivers **Epic 5: Seal a Session & Compare It Against Its Backtest** only.
Epics 1–4 are `done` on `015-paper-trading` (Epic 4 merged as PR #35 on 2026-09-30;
Story 4.8 closes it): the system connects to IBKR paper behind the two-factor
real-money gate, owns durable sessions that outlive process runs, submits, tracks,
aggregates and persists real orders and trades, and reconciles against the broker at
startup, at runtime and on demand. Nothing outside the `5-*` stories is in scope.

**Standalone deliverable:** a sealed paper session sits next to its backtest in the same
table, comparable at a glance, **with zero UI changes** — a success criterion, not an
omission.

**Scope discipline:** any new metrics table, model, or view invented for paper results is
an FR42 violation and must be rejected in review. The comparison works because there is
only one results vocabulary, not two that are kept in sync.

**What already exists that Epic 5 fills in (do not reinvent):**
- **The schema is complete.** Epic 2's migration `d08dfbd393f0` already added
  `trading_sessions` (with `linked_backtest_run_id`, `sealed_run_id`, `sealed_at`, and the
  `sealed` value of `session_status`), `backtest_runs.run_type` (`String(20)`,
  server default `'backtest'`), and `trades.session_id` with `backtest_run_id` nullable
  under a CHECK constraint. **Epic 5 owns no migration.**
- `SessionStatus.SEALED` exists in `src/models/session.py`; transitions go through
  `SessionService.transition()` only (AR37).
- `ResultsExtractor` lives in `src/core/results_extractor.py` and today takes a
  `BacktestEngine` (Story 5.1 widens it).
- `BacktestPersistenceService` (`src/services/backtest_persistence.py`) owns NaN/Infinity
  validation and `DuplicateRecordError` handling that the seal path reuses verbatim (AR12).
- Live trades are already written incrementally against `session_id` by
  `live_trade_recorder` (Story 3.6); seal backfills their `backtest_run_id`.
- `RuntimeReconciler` (`src/core/live_runtime_reconcile.py`, Story 4.3, extended by Story
  4.8) and `_FailureStreak` exist; Stories 5.7 and 5.8 extend them.
- There is **no** `live seal` CLI command yet (Story 5.3).

## Governing Journeys (from `prd.md`)

### Session vs. Process Run

| Concept | Lifetime | Behavior |
|---|---|---|
| **Process run** | Minutes to a day. Routine, possibly daily. | Start, trade, stop. Leaves open positions untouched at the broker. Never seals. |
| **Session** | Days to weeks. Spans many process runs. | The logical forward test. Accumulates the trade sample. Seals **once**, deliberately, into one comparable run record. |

`stop` ≠ `seal`, and `stop` never flattens.

### Journey 6: Sealing & the Divergence Read

Weeks later. 103 closed trades. Allay seals the session — an explicit act, distinct from
stopping the process. It runs through the same `ResultsExtractor` a backtest uses and lands
as a run record. Allay opens the backtest list: **the paper session is right there among the
backtests**, same table. Selects it and the 2016–2024 backtest of the same strategy. Side by
side, same metric names, same formatting. Win rate 55% → 51%. Profit factor 1.60 → 1.35. Max
drawdown 12% → 18%. Each carries its trade count. At n=103 the win-rate gap is inside
sampling noise; the profit-factor decay is the interesting signal; the drawdown difference is
path-dependent and means less than it looks.

### Journey 5 (the seal half): Session Death & Investigation

After a session dies, Allay fixes the cause and restarts into the same session, **or seals it
with what it has**. Nothing is lost either way, because every completed trade is already
persisted.

### Comparison Integrity (what keeps backtest-vs-paper honest)

- **Starting capital is matched, not normalized.** The IBKR paper account is reset to the
  backtest's starting balance. Percentage metrics then compare directly, with no
  normalization logic anywhere. Both records still store their starting balance.
- **RTH only.** Sessions trade regular trading hours, matching the FirstRate bars the
  backtest consumed.
- **Price provenance differs, knowingly.** Backtest data is FirstRate: split+dividend
  adjusted, consolidated. Live is IBKR: **unadjusted**, SMART-routed. Recorded as a property
  of every session — a permanent, structural divergence source, not a defect to chase.
- **Commission moves from modelled to actual** — a free validation of the fee model.
- **Slippage moves from parameter to reality** — much of what this phase exists to measure.

## Functional Requirements — Seal & Compare (E5)

- **FR40:** Operator can seal a stopped session, ending it permanently and producing a
  results record.
- **FR41:** System can compute a sealed session's performance metrics using the same
  calculation path used for backtests.
- **FR42:** System can persist a sealed session's results in the same form as backtest
  results, without a parallel results structure.
- **FR43:** System can record the closed-trade count with a sealed session's results.
- **FR44:** System can distinguish a paper-trading record from a backtest record.
- **FR45:** System can record a session's execution conditions with its results — starting
  capital, trading-hours policy, price-adjustment basis, and market data tier.
- **FR46:** Operator can view a sealed session in the existing results list, detail, and
  comparison views alongside backtests.
- **FR47:** System can handle open positions at seal time explicitly and report their
  disposition.

Cross-cutting requirements the stories also cite: **FR15** (the existing 2–10 run
comparison view), **FR36** (`live reconcile` stays exact — Story 5.7).

## Non-Functional Requirements Governing Epic 5

- **NFR14:** No artificial trades — no stop, restart, disconnect, reconnect **or seal**
  generates an entry or exit the strategy did not request.
- **NFR32:** Automated tests never touch a real broker — session lifecycle, order-state
  handling, and reconciliation logic are tested against broker doubles across the existing
  unit / component / integration (`--forked`) tiers.
- **NFR33:** Broker-dependent behavior is verified by operator-run procedures in
  `docs/qa/phase3-live-verification.md`. **For the traceability gate:** an acceptance
  criterion whose only possible proof is a live broker (a real unexpressible holding at
  IBKR, a real multi-cycle runtime reconciliation failure) is satisfied by (a) a
  broker-double test of the same logic **and** (b) a documented operator procedure. It is
  not "unmet" because CI cannot run it. Sealing itself is database-only and must be proven
  by automated tests against the test database.
- **NFR35:** Conforms to existing project standards — Python 3.11+ with type hints, `ruff` +
  `mypy` gates, file/function/class size limits (the D4 ratchet), TDD with the established
  test pyramid, >80% coverage on `src/core` + `src/strategies`.

## Architecture Requirements Governing Epic 5

- **AR5:** **The `backtest_runs` row is created only at seal**, never at session creation —
  a pre-created row would surface unfinished sessions in existing list views and violate the
  zero-UI-change criterion. Before seal, `status`/`list` read `trading_sessions` + `trades`
  directly.
- **AR6:** `backtest_runs` gains a `run_type` column (`'backtest'` default | `'paper'`) —
  satisfies FR44 with one column. *(Already migrated.)*
- **AR7:** Execution conditions (FR43/FR45) ride in the existing `config_snapshot` JSONB
  under a `session_conditions` key — no new columns; displayed free by the existing
  config-snapshot panel. Schema: `starting_capital`, `rth_only`, `price_basis`
  (`"firstrate_adjusted"` | `"ibkr_unadjusted_smart"`), `market_data_tier`,
  `closed_trade_count`, `open_positions_at_seal`, `schema_version: 1`.
- **AR8:** `trades` gains a nullable `session_id` FK; `backtest_run_id` becomes **nullable**
  with a CHECK constraint (`backtest_run_id IS NOT NULL OR session_id IS NOT NULL`). Live
  trades write incrementally against `session_id`; seal backfills `backtest_run_id`, after
  which existing UI queries see them unchanged. *(Already migrated.)*
- **AR12:** The seal path reuses `BacktestPersistenceService` NaN/Infinity validation and
  `DuplicateRecordError` handling verbatim.
- **AR29:** CLI reports render Rich tables with `--json`: plain objects, snake_case keys,
  ISO-8601 UTC timestamps, Decimal-as-string for money.
- **AR30:** `seal` **refuses by default when the session has open positions**, listing them
  explicitly. `seal --force` seals anyway: open positions persist as **open trades (null
  exit)** — the shape backtests already use — their disposition is printed in the seal
  report, and they remain at the broker, explicitly reported as no longer owned by any
  session.
- **AR31:** **Sealing never flattens.** Auto-flattening would manufacture artificial exits,
  violating NFR14.
- **AR35:** Seal reconstructs the equity series from starting capital + the session's
  closed-trade P&L sequence (stored in the existing `PerformanceMetrics` JSON field); max
  drawdown / CAGR / Calmar compute from that series exactly as `ResultsExtractor` already
  does. **Recorded limitation:** trade-granularity (not bar-granularity) drawdown — carried in
  `session_conditions` so the comparison read stays honest.
- **AR37:** **Session lifecycle is one state machine in one place** — transitions validated in
  `SessionService.transition()`; CLI and runner request transitions, never set `status`
  directly. Illegal transition → `InvalidSessionTransition`. Grep-enforceable: no direct
  `status =` assignments outside it.
- **AR38:** The runner owns the node; the service owns the record. `LiveSessionRunner` never
  imports SQLAlchemy; `SessionService`/repositories never import Nautilus.
- **AR43:** **Anti-patterns to reject in review:** a second results vocabulary (any new
  metrics table/model for paper); pre-creating `backtest_runs` rows before seal;
  `close_all_positions()` in any lifecycle hook; retry-on-timeout around order submission;
  reading `ibkr_read_only` as the safety control.
- **AR44:** **Explicitly untouched (zero-change success criteria):** `src/api/**`,
  `templates/**`, `src/services/backtest_query.py`, comparison views, `BacktestOrchestrator`,
  all import/catalog commands.

## Debt routed to Epic 5 by earlier stories and retrospectives

Recorded in `deferred-work.md` and `epic-4-retro-2026-09-28.md`. Not acceptance criteria;
the owning story's Dev Agent Record should disposition each one (fix it if it falls inside
the AC, otherwise record why not). Items with no story owner are **not** pulled into a story.

- **Story 5.1:** never read IB `AccountState` balances (`AccountBalance.total` =
  `NetLiquidation`, which the adapter replaces with a literal `400000` under margin stress) as
  cash or equity — the live-host widening must not reuse the backtest path's
  `account_for_venue(...).balance_total(USD)`. Pinned today by
  `TestAdapterCanaries::test_the_summary_keeps_cash_per_currency_and_nautilus_balance_is_not_cash`.
- **Story 5.2 / 5.4:** reconciliation-owned (`EXTERNAL` / `INTERNAL-DIFF`) positions are never
  persisted as trades (Story 3.6 D-D), so a session that absorbed a corporate action or a
  reconciliation correction has economic activity outside the trades table the equity curve is
  built from. A lot held across a split records its round trip at unadjusted prices (a 2:1
  split reads as a ~50% loss). Record it in `session_conditions` so a comparison can attribute
  the difference rather than read it as strategy decay.
- **Story 5.3:** the backtest path's `commissions[0]` (`save_trades_from_positions` takes only
  the first commission entry) — fix both sides together if at all, to keep them comparable;
  whether `--compare-to` / seal should require the linked backtest to be `success` with
  metrics; `SessionStatus` has no terminal-failure state, so a truncated session seals the same
  as a complete one (no fifth status — that needs a migration the phase does not have).
- **Story 5.4:** `open_positions_at_seal` must account for the split's extra shares held by a
  synthetic owner after the strategy exits.
- **Story 5.6:** the side-unaware `profit_pct` formula (a profitable SHORT reads negative) is
  reproduced on both sides for parity; changing it means changing both sides together — and
  AR44 forbids touching the views, so disposition, do not fix. `trades` has no `strategy_id`
  column, so a multi-strategy session's rows cannot be attributed per strategy (AR43 forbids a
  new column without an epic-level decision).
- **Unowned within Epic 5 (named, not in scope):** D6 resubscription after a
  subscription-killing IB error; the `owner_epoch` pre-submit fencing gap (and W1, the runtime
  correction write it leaves unfenced); re-validating a persisted spec on read and
  `SessionSpec.model_copy(update=…)`; `data_catalog.py`'s import-time `load_dotenv()` leak;
  the component-tier fixture-isolation flake class; the globbed forbidden-order-method scan
  over `src/core/live_*.py` (routed to Epic 5 *pre-work*, done by hand before the run).

## Epic 5: Seal a Session & Compare It Against Its Backtest

The payoff. An explicit seal turns a stopped session into an ordinary run record through the same
`ResultsExtractor` a backtest uses, and it appears beside its backtest in the existing comparison
view — same names, same units, zero UI changes.

**Covers:** FR40–FR47 · NFR32, NFR35 · AR5, AR7, AR12, AR30, AR31, AR35, AR43, AR44

### Story 5.1: Compute Metrics for a Live Host Through the Existing Extractor

As the operator,
I want a paper session's metrics computed by the same code that computes a backtest's,
So that there is no parallel metrics vocabulary to validate or keep in sync.

**Acceptance Criteria:**

**Given** `ResultsExtractor`, which today takes a `BacktestEngine`
**When** it is widened
**Then** it accepts either a `BacktestEngine` or a live host exposing the same `portfolio` and
`cache` components (FR41).

**Given** the widening
**When** the diff is reviewed
**Then** the metric math is untouched — the change is confined to how the host's portfolio and cache
are obtained (FR41).

**Given** the existing backtest path
**When** the full backtest test suite runs after the change
**Then** every existing test passes unchanged and backtest results are bit-identical to before.

**Given** any proposal to add a metrics table, model, or view specific to paper results
**When** it appears in review
**Then** it is rejected as an FR42 violation (AR43).

### Story 5.2: Reconstruct a Sealed Session's Equity Curve from Its Trades

As the operator,
I want drawdown and the ratios derived from it computed for a paper session,
So that the comparison covers the path-dependent metrics too, with its limitation stated rather than
hidden.

**Acceptance Criteria:**

**Given** a sealed session has no engine history to draw an equity curve from
**When** seal computes metrics
**Then** it reconstructs the equity series from starting capital plus the session's closed-trade P&L
sequence, stored in the existing `PerformanceMetrics` JSON field (AR35).

**Given** that reconstructed series
**When** max drawdown, CAGR, and Calmar are computed
**Then** they are computed from it by exactly the same code path `ResultsExtractor` already uses
(FR41).

**Given** the series is trade-granular rather than bar-granular
**When** the record is written
**Then** that limitation is recorded in `session_conditions`, so the drawdown comparison is read
honestly (AR35).

**Given** a session with zero closed trades
**When** seal is attempted
**Then** it behaves predictably — either refusing with a clear message or producing a record whose
zero-trade state is explicit — rather than emitting NaN or dividing by zero (AR12).

### Story 5.3: Seal a Stopped Session into a Comparable Run Record

As the operator,
I want an explicit seal that ends the session and produces a results record,
So that a multi-week forward test becomes one comparable unit at a moment I choose.

**Acceptance Criteria:**

**Given** `ntrader live seal <session>`
**When** it is run on a **stopped** session
**Then** it computes metrics, writes one `backtest_runs` row plus its `performance_metrics` row,
backfills `backtest_run_id` on the session's trades, sets `sealed_run_id` and `sealed_at`, and
transitions the session to `sealed` (FR40, FR42, AR8).

**Given** a session that is `running`
**When** seal is attempted
**Then** it is refused — sealing never happens to a live process out from under itself (FR40, AR37).

**Given** a session that is already `sealed`
**When** seal is attempted again
**Then** it is refused, `sealed` being terminal (AR37).

**Given** the created run record
**When** its `run_type` is read
**Then** it is `'paper'`, distinguishing it from a backtest with one column (FR44, AR6).

**Given** the seal path
**When** it writes
**Then** it reuses `BacktestPersistenceService` NaN/Infinity validation and `DuplicateRecordError`
handling verbatim (AR12).

**Given** no `backtest_runs` row existed for the session before this moment
**When** the codebase is inspected
**Then** no path creates one at session creation or start — the row exists only from seal onward
(AR5).

**Given** the seal completes
**When** the report is printed
**Then** it names the run ID created, closed-trade count, open positions and their disposition, and
the execution-conditions summary, in both human and `--json` form with identical fields (AR29).

### Story 5.4: Record the Conditions the Session Actually Ran Under

As the operator,
I want each sealed session to carry the conditions that produced it,
So that I can read a metric difference against its causes and its sampling noise instead of
over-interpreting it.

**Acceptance Criteria:**

**Given** a sealed session
**When** its `config_snapshot` is written
**Then** it contains a `session_conditions` key holding `starting_capital`, `rth_only`, `price_basis`,
`market_data_tier`, `closed_trade_count`, `open_positions_at_seal`, and `schema_version: 1` (FR43,
FR45, AR7).

**Given** `price_basis`
**When** it is set for a paper session
**Then** it records `"ibkr_unadjusted_smart"`, distinct from a backtest's
`"firstrate_adjusted"` — the permanent structural divergence source named up front (FR45).

**Given** the closed-trade count
**When** it is recorded
**Then** it travels with the record so a difference can be read against sampling noise (FR43).

**Given** the run row's own columns
**When** they are populated
**Then** `initial_capital` matches the session's starting capital, `start_date` is the session's
first start, `end_date` is the seal time, and `data_source` identifies the live IBKR source.

**Given** starting capital
**When** the record is written
**Then** the paper session's own starting balance is stored as-is, with no normalization logic
anywhere — comparability comes from matching the accounts, not from arithmetic (AR7).

**Given** the new key
**When** the existing config-snapshot panel renders it
**Then** it displays without any UI change, because it is ordinary JSONB in an existing column (AR7).

### Story 5.5: Handle Open Positions at Seal Time Explicitly

As the operator,
I want sealing with open positions to be a decision I make, never something that happens quietly,
So that I am never surprised by orphaned positions or by exits my strategy did not request.

**Acceptance Criteria:**

**Given** a stopped session with open positions
**When** `live seal` is run without `--force`
**Then** it refuses, listing each open position with its instrument and quantity (FR47, AR30).

**Given** the same session
**When** `live seal --force` is run
**Then** it seals, and each open position is persisted as an **open trade with a null exit** — the
same shape backtests already use for positions open at the end of data (FR47, AR30).

**Given** a `--force` seal
**When** the report is printed
**Then** it states the disposition explicitly: the positions remain at the broker and are no longer
owned by any session (FR47, AR30).

**Given** any seal path
**When** the code is inspected
**Then** nothing flattens, closes, or modifies a position — sealing never manufactures an exit
(AR31, NFR14, AR43).

**Given** a stopped session with no open positions
**When** `live seal` is run
**Then** it seals without requiring `--force`.

### Story 5.6: Read a Paper Session Beside Its Backtest in the Existing Views

As the operator,
I want the sealed session to appear among my backtests and compare against the one it came from,
So that I can finally see how much to discount a backtest — which is the entire point of the phase.

**Acceptance Criteria:**

**Given** a sealed session
**When** the existing results list is opened
**Then** the paper record appears alongside backtests, rendering correctly with no template, route,
or query change (FR46, AR44).

**Given** the sealed record
**When** its detail view is opened
**Then** metrics, trades, and the config snapshot — including `session_conditions` — all render
correctly (FR46).

**Given** the sealed session and the backtest named in its `linked_backtest_run_id`
**When** both are selected in the existing 2–10 run comparison view
**Then** they render side by side with the same metric names, formatting, and units (FR46, FR15).

**Given** the phase's zero-UI-change criterion
**When** the diff for this epic is reviewed
**Then** `src/api/**`, `templates/**`, `src/services/backtest_query.py`, and the comparison views are
untouched (AR44)
**And** any change to them is treated as a success-criterion violation rather than a judgment call.

**Given** the trades backfilled at seal
**When** existing trade queries run
**Then** they return the session's trades through `backtest_run_id` exactly as they do for backtests
(AR8).

### Story 5.7: Name Holdings the Adapter Cannot Express Instead of Refusing or Hiding Them

*(Added 2026-09-30 from PR #35's code review, decision D2 — PO ruling: "same-symbol rule
everywhere, name the rest". D1's runtime half — the symbol-scoped, bounded hold-back — already
shipped on PR #35 and is the mechanism this story extends.)*

As the operator,
I want a holding the IB adapter cannot express — a bond, a warrant, a BAG, a delisted symbol, or a
fractional share from a DRIP — to be named once and otherwise left out of what reconciliation judges,
So that a paper account that happens to hold one can still start, run and reconnect, and the one
disagreement that could mask a strategy's own position is still caught.

**Acceptance Criteria:**

**Given** the broker holds a position the adapter cannot resolve to an instrument (an `IB-CONID-*` row)
whose symbol matches no cached position
**When** the session starts
**Then** the row is logged `reconcile.discrepancy resolution=unresolved` once and the start is **not**
refused (today it refuses `UNRESOLVABLE_DISCREPANCY` forever with the remedy "retry the start").

**Given** the same row while the session runs
**When** a disconnect is recovered
**Then** the row does not withhold the reconnect grant (today `_act` never returns empty while any
unresolved row exists, so orders are withheld for the rest of the session).

**Given** an unresolved row whose symbol **does** match a cached position
**When** either phase judges it
**Then** the existing fail-closed rules apply unchanged: the start is refused; at runtime the same-symbol
"broker 0" row is held back for `HOLD_BACK_CYCLES` and then acted on (PR #35 D1).

**Given** a broker quantity the instrument's size increment cannot express (`+22.5` against the
strategy's `+22`, where `Equity.make_qty` rounds silently to 22)
**When** the session reconciles, at startup or at runtime
**Then** the remainder is logged once per instrument at WARNING, naming both quantities, and the row is
clean at the expressible precision — never `UNRESOLVABLE` at startup and never a silent `reconcile.ok`
over a standing gap at runtime.

**Given** `live reconcile`
**When** it reads the same account
**Then** it stays exact (FR36): the fractional remainder is a discrepancy line and exit `5`.

### Story 5.8: Act on a Persistent Runtime Reconciliation Failure

*(Added 2026-09-30 from PR #35's code review, decision D3 — PO ruling: "withhold after 3, drift
stops". Belongs beside Story 5.4's `session_conditions`, which is where a withheld period should be
recorded.)*

As the operator,
I want a session whose runtime reconciliation keeps failing to stop sending orders, and one whose
adapter has drifted to stop outright,
So that a session never trades for its whole life with no alignment against IBKR just because the
broker read keeps failing quietly.

**Acceptance Criteria:**

**Given** `RuntimeReconciler._run` catches a non-`ReconciliationFailedError` on three consecutive cycles
(`_FailureStreak.count` reaches 3)
**When** the third failure is noted
**Then** trading permission is withdrawn through the connection monitor's existing `RECOVERING` state
(orders withheld, `order.suppressed`), one `reconcile.cycle_failed` is logged at ERROR naming the streak,
and the next clean cycle re-grants through `confirm_state_reestablished` exactly as a reconnect does.

**Given** the failure is a `BrokerStateAdapterError` (adapter drift — e.g. two IB contracts resolving
to one instrument id)
**When** it is first noted
**Then** the session stops with exit `1` on the first occurrence, as startup treats adapter drift; the
hourly throttle does not apply.

**Given** a withheld period
**When** the session is later sealed (Story 5.3)
**Then** the period is recorded in `session_conditions` (Story 5.4) with its start, end and cause.

**Given** the three-cycle threshold
**When** it is pinned
**Then** it is a named constant with a test that fails if a streak of two withholds or a streak of
three does not.
