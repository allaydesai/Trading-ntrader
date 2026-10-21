# Story 5.7: Name Holdings the Adapter Cannot Express Instead of Refusing or Hiding Them

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a holding the IB adapter cannot express — a bond, a warrant, a BAG, a delisted symbol, or a
fractional share from a DRIP — to be named once and otherwise left out of what reconciliation judges,
so that a paper account that happens to hold one can still start, run and reconnect, and the one
disagreement that could mask a strategy's own position is still caught.

*(Added 2026-09-30 from PR #35's code review, decision D2 — PO ruling: "same-symbol rule everywhere,
name the rest". D1's runtime half — the symbol-scoped, bounded hold-back — already shipped on PR #35
and is the mechanism this story extends.)*

## Why this story is shaped the way it is

Read this before designing anything. Everything below was re-measured at drafting (2026-10-07,
installed `nautilus-trader 1.220.0`, head `40e8f1c`, worktree `hx-p3e5-5-7-…`). Wheel paths are
relative to `.venv/lib/python3.11/site-packages/nautilus_trader/`; ripgrep skips `.venv`
(gitignored), so search it with `grep -n` on explicit paths.

**Two different holdings, one ruling.** D2 covers two shapes that today fail in opposite directions:

1. **An unresolved contract** — the broker reader's `IB-CONID-<conId>` fallback row
   (`live_broker_state._to_position`, `instrument_resolved=False`), produced when
   `provider.get_instrument(contract)` raises or returns nothing (a bond, a warrant/rights stub, a
   BAG, a delisted symbol). Today it **refuses every start** (`UNRESOLVABLE_DISCREPANCY`, remedy
   "retry the start") and, at runtime, **withholds the reconnect grant forever** (orders withheld for
   the rest of the session after any disconnect). Both are over-strict: the row can only ever hide
   the holding a cached position of the **same symbol** reads as "broker 0".
2. **A fractional remainder** — a broker quantity finer than the instrument's size increment
   (`+22.5` against a strategy's `+22`, a DRIP). Today it **refuses every start**
   (`UNRESOLVABLE_DISCREPANCY`) but is **inert-and-clean at runtime** (logged `unresolved` once, then
   `reconcile.ok` hourly over a standing 0.5-share gap). One is too strict, the other too quiet.

The ruling: an unresolved row matters only to a same-symbol cached position (startup *and* the
reconnect grant); a fractional remainder is named once and the row is clean at the expressible
precision in the session; `live reconcile` stays exact (exit 5).

### Measured facts — cite, don't re-derive

- **F1 — `Instrument.make_qty` rounds half-even, never truncates.** Against
  `TestInstrumentProvider.equity("NVDA", "NASDAQ")` (`size_precision=0`, `size_increment=1`):
  `make_qty(22.5) → 22`, **`make_qty(23.5) → 24`**, `make_qty(0.5)` **raises** `ValueError`
  ("rounded to zero due to size increment 1 and size precision 0"). So the expressible quantity
  **must be computed in `Decimal` by truncating toward zero**, never by calling `make_qty` — a
  half-even `24` against a broker `23.5` would let a strategy sell a share the account does not hold
  (NFR14). `position_report`'s existing "rounded → `None`" guard stays as defence in depth.
- **F2 — the IB adapter builds its reports at the string's own precision.**
  `adapters/interactive_brokers/execution.py:496` (`generate_position_status_reports`) and `:415`
  (the fabricated per-position `FILLED` order) both use `Quantity.from_str(str(abs(position.quantity)))`
  — a `22.5` report carries precision 1 against a precision-0 instrument. The component test double
  `_IBShapedClient` (`tests/component/core/test_live_startup_reconcile_engine.py:169-184`) does the
  same, so it reproduces production here.
- **F3 — Nautilus's own startup pass over-imports a whole share for a fractional broker
  quantity** (measured 2026-10-07 with the component `_Harness`, the session's own
  `filter_unclaimed_external_orders=True`):

  | Broker | Cached before | After `reconcile_execution_state` (native, inside `node:connect`) |
  |---|---|---|
  | `NVDA 22.5` | strategy `+22` | strategy `+22`, **`INTERNAL-DIFF +1`** (net 23) |
  | `NVDA 23.5` | strategy `+23` | strategy `+23`, **`INTERNAL-DIFF +1`** (net 24) |
  | `NVDA 0.5` | nothing | **`INTERNAL-DIFF +1`** (net 1) |

  Native returns `True` every time. So by the time the `reconcile` phase runs, the cache holds
  **more** than the broker — and today the phase refuses (`position_report` → `None`). With this
  story the phase compares at the expressible precision and **corrects the over-import broker-ward**
  (`INTERNAL-DIFF +1 → 0`, logged `reconcile.discrepancy resolution=broker`), so the strategy resumes
  on its own `+22` with no unowned excess beside it. Pin F3 with a canary (Task 5.1): it fails by
  name if an upgrade stops over-importing.
- **F4 — today's runtime on a fractional remainder.** Broker `22.5`, cache `22` → a `POSITION` row
  (`strategy_contradicted` is `False`: covered) → debounced → `correct()` → `position_report` returns
  `None` → `_Unresolved.log` writes `reconcile.discrepancy resolution=unresolved` (ERROR) once → the
  row is in `inert` → clean → `reconcile.ok` hourly. A *different* fractional remainder later is a
  different row, so it is logged once more; the same one never again.
- **F5 — today's unresolved row.** Startup: `reconcile_at_startup` refuses any `not
  row.broker_resolved` row (`live_startup_reconcile.py:376-378`) before the contradiction check.
  Runtime: `_act` puts every confirmed lookup miss in `outstanding` and never in `inert`
  (`live_runtime_reconcile.py:344-373`), so `_run` never reaches `self._grant(...)` on a reconnect
  cycle and `_ok.clean` never increments — no `connection.restored`, no runtime `reconcile.ok`, for
  as long as the row stands.
- **F6 — `live reconcile` is already exact and does not share the model's comparison.**
  `src/services/reconciliation_service.py::_position_lines` compares `Decimal` quantities line by
  line; it never calls `compare_positions`. A `22.5` broker line against a `22` session line is
  already `matches=False` → `ReconciliationReport.is_clean=False` → `outcome_for` → `DISCREPANCY` →
  exit `5`. **AC #5 is a pin, not a change** — and the expressible-precision helper must never reach
  this path.
- **F7 — size headroom (statement metric, `tests/unit/governance/test_size_caps.py`).**
  `reconcile_at_startup` measures **49 / 50**; `RuntimeReconciler` (class) **90 / 100**;
  `RuntimeReconciler._act` 24; files `live_startup_reconcile.py` 334, `live_runtime_reconcile.py` 331,
  `position_reconciliation.py` 185 (cap 500). `reconcile_at_startup` **cannot grow** without an
  extraction; keep `RuntimeReconciler` ≤ 95 (Story 5.8 is extending the same class in parallel).
- **F8 — four existing tests encode the behaviour this story reverses or sharpens** (change them
  deliberately, saying so in the test's docstring):
  - `tests/unit/core/test_live_startup_reconcile.py::TestEverythingElseResolvesBrokerWard::test_an_unresolved_broker_row_is_refused_without_writing`
    (empty cache, symbol defaults to `IB-CONID-265598`) — now **not** refused (AC #1).
  - `…::TestReviewFindings::test_an_unrepresentable_broker_quantity_is_refused_before_any_write`
    (broker `AAPL 0.5`, `NVDA 1`) — now clean at the expressible precision: AAPL named as a
    remainder, NVDA corrected (AC #4).
  - `…::TestReviewFindings::test_an_unresolved_broker_row_beside_a_strategy_position_is_not_a_contradiction`
    — its `_held(...)` omits `symbol=`, so the row's symbol is `IB-CONID-4815747`, which matches no
    cached position. Under this story that would be inert and the NVDA row refused as contradicted.
    Give it `symbol="NVDA"` (the realistic lookup miss) so it pins AC #3's same-symbol refusal, and
    add the sibling where the symbol differs (refused `STRATEGY_POSITION_CONTRADICTED`).
  - `tests/unit/core/test_live_runtime_reconcile.py::TestUnresolvableRows::test_an_unresolved_broker_row_withholds_the_grant`
    (empty cache) — now the grant **is** issued (AC #2); its fail-closed sibling becomes the
    same-symbol case (AC #3).
  `tests/component/core/test_session_runner_phases.py`'s `"unresolvable"` refusal case is the
  *instrument-not-in-cache* path (`EXTERNAL AAPL +4`, no instrument) — **unchanged**, keep it green.

### Design decisions (disclosed here, not discovered in review)

- **D-A — "matches a cached position" is read literally.** An unresolved row's symbol
  (`symbol_key(row.broker_symbol)`, the D1 normaliser: `BRK B` ≡ `BRK-B` ≡ `BRK.B`) is compared with
  the symbol of **every open position in the cache, any owner** (strategy, `EXTERNAL`,
  `INTERNAL-DIFF`) — `symbol_key(instrument_id.split(".", 1)[0])`. Match → the existing fail-closed
  rules, unchanged (startup refuses `UNRESOLVABLE_DISCREPANCY`; at runtime the row withholds the
  grant and D1's bounded hold-back runs). No match → inert: logged `resolution=unresolved` once,
  never refused, never withholds the grant.
  **Disclosed consequence:** an unresolvable holding that shares a symbol with a cached position the
  broker *also* reports resolved (NVDA stock held by a strategy beside an unresolvable NVDA-symbol
  contract) still refuses the start and still withholds a reconnect grant — the AC's literal rule,
  and the pre-5.7 behaviour for that shape. IB gives warrants/rights their own symbol (`XYZ WS`,
  `XYZ RT` → `XYZWS`, `XYZRT`), and options resolve, so this is expected to be rare. The narrower
  reading (match only a cache row that reads "broker 0") is the PO's call if a live account ever
  meets it — flag it at code review, do not decide it here.
  **PO ruling 2026-10-07 (code review R3): keep the literal reading** — no code change; the open
  question in `deferred-work.md` is closed.
  **PO ruling 2026-10-07 (code review R1): traded symbols count as held.** The "held" set is the
  union of the open cached positions' symbols and the symbols of every instrument the session's
  strategies trade (`held_symbols(cached, traded_instrument_ids)`; the runner passes
  `session_instrument_ids(spec)` — every bar type's instrument — to both `reconcile_at_startup` and
  `RuntimeReconciler`), at startup **and** at runtime (the reconnect grant and D1's `_HoldBack`).
  Without it a FLAT strategy that trades NVDA ignored an unresolvable NVDA-symbol row and could
  buy on top of a holding it cannot see (pre-5.7 this refused). A matching row keeps the existing
  fail-closed rules unchanged. The symbol is everything before the instrument id's **last** dot
  (code review P1: `BRK.B.NYSE` is `BRK.B`, never `BRK`).
- **D-B — an unknown symbol fails closed.** An unresolved row whose `symbol_key` is `""` (no symbol)
  is treated as matching whenever the cache holds any open position. IB always sends a symbol, so
  this is an edge, but "cannot tell which holding" must never read as "matches none".
- **D-C — the expressible quantity truncates toward zero to a whole number of size steps.**
  **PO ruling 2026-10-07 (code review R2): the step is `size_precision`, not `size_increment`** —
  `step = Decimal(1).scaleb(-instrument.size_precision)`. Nautilus `make_qty` rounds to
  `size_precision`; `size_increment` can be coarser (FX, futures, lots), so truncating to it would
  cut an exactly representable quantity (`150` with increment `100` → `100`) and "correct" the
  cache below the broker. Equities are unchanged (precision 0 ≡ increment 1). Renamed to match:
  `size_increments()` → `size_steps()`, `InexpressibleRemainder.size_increment` → `size_step`, and
  the record's field `size_increment` → `size_step`. A lookup that raises `AttributeError` now also
  leaves the row exact (code review P3). The text below is as drafted:
  `expressible = (q / increment).to_integral_value(ROUND_DOWN) * increment`, with `increment =
  instrument.size_increment.as_decimal()` of the instrument **the cache holds** for that id (W4: the
  broker reader's `get_instrument` loads every resolved broker instrument into the cache, so it is
  there). Applied only to **resolved** broker rows whose instrument the cache has; every other row
  is left exact (an unresolved row has no increment; an instrument the cache lacks keeps today's
  path — refused at startup, inert at runtime). An expressible quantity of `0` drops the row from the
  judged state (a `0.5`-only holding reads flat). Truncation is the safe direction both ways: `23.5`
  judges as `23` (a strategy believing `24` is contradicted, never covered), `-22.5` as `-22`.
- **D-D — judged at the expressible precision, reported exact.** Both phases compare, correct and
  re-verify against the **expressible** `BrokerState`; every record that *reports* the broker
  (`reconcile.ok`'s `positions` / `instruments`, `StartupReconciliation.broker`, the resume check's
  `broker_quantity`) keeps the **exact** broker read. The remainder itself is named by a new record,
  and `reconcile.ok` carries it while it stands (D-E), so no `reconcile.ok` is silent over a gap.
- **D-E — one new record, `reconcile.inexpressible_remainder` (WARNING).** Fields: `scope`,
  `instrument_id`, `broker_quantity` (exact), `expressible_quantity`, `remainder`, `size_step`
  (named `size_increment` as drafted; renamed by the code review, R2)
  — strings, like every other quantity field. Not `reconcile.discrepancy`: the row is *clean*, and a
  discrepancy record for a clean row would contradict `reconcile.ok`. `reconcile.ok` (startup and
  runtime) gains `inexpressible_remainders: {instrument_id: remainder}` (`{}` when none).
  `EMITTED_RECONCILE_EVENTS` widens **3 → 4** (CLAUDE.md "Membership-pinned lists": change the
  constant, its both-directions pin and the NFR26 parametrized scan together, and update CLAUDE.md's
  `EMITTED_RECONCILE_EVENTS` entry in the same commit).
- **D-F — "once per instrument" means once per reconciliation, per standing value.** The startup
  phase names each remainder once (`scope=startup`); the running session's cycle names it once more
  on the first cycle that sees it (`scope=runtime`/`reconnect`), then never again while it stands —
  the `_Unresolved` precedent: a remainder whose value changes (another DRIP lands) or that clears
  and returns is a new fact and is named again. No debounce: the remainder is an observation, never
  acted on.
  **PO ruling 2026-10-07 (code review R4): keep** — startup names a remainder once, runtime once
  more on first sighting. "Standing value" is keyed per `(instrument, remainder)` (code review P2):
  a strategy buying a share on a DRIP instrument (22.5 → 23.5) leaves the same 0.5 standing and is
  not named again; 22.5 → 22.75 is.
- **D-G — D1's hold-back counts only matching unresolved rows.** `_HoldBack.masking` today counts
  every unresolved symbol, so a bond that matches nothing would log `reconcile.hold_back_expired`
  after three cycles for a row that never held anything back. Feed it only the rows D-A says match.
  For matching rows nothing changes (AC #3); a matching symbol that stops matching for one
  observation starts over, D1's own "absent from one observation" rule.
- **D-H — Story 4.8's skip-set debt is taken while `_act`'s return is open.** The 4.8 code review
  deferred "AC #5's skip set omits the 'unresolvable' rows — owner: whoever next touches `_act`'s
  return value". This story does: the stale-order check's `skip` becomes **every observed row's
  instrument** (outstanding ∪ inert) plus deferred. One line, one test.

## Acceptance Criteria

Copied verbatim from `_bmad-output/planning-artifacts/epics.md` (Story 5.7) /
`prd-epic5-scope.md`; numbered for traceability.

1. **Given** the broker holds a position the adapter cannot resolve to an instrument (an `IB-CONID-*`
   row) whose symbol matches no cached position
   **When** the session starts
   **Then** the row is logged `reconcile.discrepancy resolution=unresolved` once and the start is
   **not** refused (today it refuses `UNRESOLVABLE_DISCREPANCY` forever with the remedy "retry the
   start").

2. **Given** the same row while the session runs
   **When** a disconnect is recovered
   **Then** the row does not withhold the reconnect grant (today `_act` never returns empty while any
   unresolved row exists, so orders are withheld for the rest of the session).

3. **Given** an unresolved row whose symbol **does** match a cached position
   **When** either phase judges it
   **Then** the existing fail-closed rules apply unchanged: the start is refused; at runtime the
   same-symbol "broker 0" row is held back for `HOLD_BACK_CYCLES` and then acted on (PR #35 D1).

4. **Given** a broker quantity the instrument's size increment cannot express (`+22.5` against the
   strategy's `+22`, where `Equity.make_qty` rounds silently to 22)
   **When** the session reconciles, at startup or at runtime
   **Then** the remainder is logged once per instrument at WARNING, naming both quantities, and the
   row is clean at the expressible precision — never `UNRESOLVABLE` at startup and never a silent
   `reconcile.ok` over a standing gap at runtime.

5. **Given** `live reconcile`
   **When** it reads the same account
   **Then** it stays exact (FR36): the fractional remainder is a discrepancy line and exit `5`.

**Traceability gate note (NFR33):** a real unexpressible holding at IBKR cannot exist in CI. Every
AC is proven against broker doubles (unit) and a real `LiveExecutionEngine` (component), and
Procedure P22 in `docs/qa/phase3-live-verification.md` is the operator half, recorded "defined, not
run".

## Tasks / Subtasks

- [x] **Task 1 — Pure model: expressible precision and the same-symbol predicate** (AC: #1, #3, #4)
  - [x] 1.1 Failing tests first in `tests/unit/models/test_position_reconciliation.py`:
    `expressible_quantity` truncates toward zero (`22.5→22`, **`23.5→23` not 24** — F1, `-22.5→-22`,
    `0.5→0`, `1.005` @ `0.01` → `1.00`, an exact quantity unchanged); a non-positive / non-finite /
    non-`Decimal` increment raises.
  - [x] 1.2 `InexpressibleRemainder` (frozen, slots): `instrument_id`, `broker_quantity`,
    `expressible_quantity`, `size_increment`; `remainder` property; `__post_init__` refuses a zero
    remainder, a non-finite value, and an expressible quantity farther from zero than the broker's.
  - [x] 1.3 `at_expressible_precision(broker, increments) -> tuple[BrokerState, tuple[InexpressibleRemainder, ...]]`:
    quantises every resolved row with a known increment (`dataclasses.replace`), drops a row whose
    expressible quantity is `0`, leaves unresolved / unknown-increment rows exact, keeps `account`,
    `cash`, `retrieved_at`; remainders sorted by instrument. Tests: the D-C table; a flat state is
    returned unchanged; the result is a valid `BrokerState`.
  - [x] 1.4 `held_symbols(cached) -> frozenset[str]` and `may_mask_a_cached_position(row, held) -> bool`
    (unresolved rows only; `symbol_key` matching; D-B: an empty key matches whenever `held` is
    non-empty). Tests: `BRK B`/`BRK-B`/`brk b`/`BRK.B` vs `BRK-B.NYSE`; a synthetic owner counts; a
    resolved row is never "masking"; empty symbol both ways.
  - [x] 1.5 Stdlib only (`decimal.ROUND_DOWN`, `dataclasses.replace`, `collections.abc.Mapping`) —
    the module's existing `TestImportPurity` pin must stay green. Update the module
    docstring ("Compared exactly … no tolerance") to say *what* is exact: the comparison is exact
    over whatever state it is given; the live phases give it the expressible state (Story 5.7).

- [x] **Task 2 — Startup phase** (AC: #1, #3, #4) — `src/core/live_startup_reconcile.py`
  - [x] 2.1 Failing unit tests first (`tests/unit/core/test_live_startup_reconcile.py`, new class
    `TestHoldingsTheAdapterCannotExpress`):
    - AC #1: empty cache + `IB-CONID-265598` (symbol `T 4.5 02/15/36`) → **no refusal**, exactly one
      `reconcile.discrepancy resolution=unresolved` (ERROR) naming it, nothing written,
      `reconcile.ok` emitted; same with a cached strategy `AAPL +5` matched by the broker (an
      unrelated symbol).
    - AC #1 + an unrelated contradiction: the inert row is logged `unresolved` once and **not**
      re-logged `untouched` when another row refuses.
    - AC #3: cache strategy `NVDA +22`, unresolved row symbol `NVDA` → refused
      `UNRESOLVABLE_DISCREPANCY`, nothing written, no "new session" in the message; same for a
      cached `EXTERNAL` owner; the `BRK B` spelling; the D-B empty symbol.
    - AC #4: broker `NVDA 22.5`, strategy `+22` → **no refusal**, one
      `reconcile.inexpressible_remainder` (WARNING) with `broker_quantity="22.5"`,
      `expressible_quantity="22"`, `remainder="0.5"`, `scope="startup"`; no report written;
      `reconcile.ok` carries `inexpressible_remainders={NVDA: "0.5"}`; `result.broker` is the exact
      read (`22.5`).
    - AC #4, F3 shape: cache strategy `+22` **and** `INTERNAL-DIFF +1`, broker `22.5` → corrected
      broker-ward to `22` (one report, quantity 22, exact), `resolution=broker`, clean.
    - AC #4: `AAPL 0.5`-only + `INTERNAL-DIFF +1` → corrected flat; `AAPL 0.5` with nothing cached →
      clean, nothing written, named.
    - AC #4: broker `25.5`, strategy `+22` → corrected to `25` (covered growth, Story 4.7's rule),
      remainder `0.5` named. Broker `23.5`, strategy `+24` → refused `STRATEGY_POSITION_CONTRADICTED`
      (truncation, not half-even — F1).
    - `framework_resolved` does not list a row merely because its broker side was quantised (snapshot
      `+22` vs broker `22.5` → no `resolution=framework`).
    - Rewrite the three F8 startup tests deliberately (docstrings say why).
  - [x] 2.2 `size_increments(cache, broker) -> dict[str, Decimal]` (resolved rows whose instrument the
    cache holds; a lookup that raises `ValueError`/`TypeError` is skipped, i.e. left exact; only a
    positive finite increment is kept) and `expressible_broker(cache, broker)` — shared with the
    runtime cycle, beside `position_report`.
  - [x] 2.3 `log_remainder(log, remainder, *, scope)` beside `log_discrepancy`, emitting
    `INEXPRESSIBLE_REMAINDER_EVENT = "reconcile.inexpressible_remainder"` through `_emit`; add it to
    `EMITTED_RECONCILE_EVENTS` (3 → 4).
  - [x] 2.4 In `reconcile_at_startup`: compute the expressible state right after the read (cash
    changes stay on the exact read), log each remainder, then compare / `_framework_resolved` /
    `_correct` / re-verify against the **expressible** state (missing one turns a clean row into
    `DISCREPANCY_REMAINS` or a false `framework` record). Replace the "any unresolved row refuses"
    block with a helper that logs the inert rows `unresolved` once and drops them, and refuses only
    the matching ones (`UNRESOLVABLE_DISCREPANCY`, `_UNRESOLVED` text amended to say the symbol
    matches a position this session holds). Compute `_framework_resolved` **before** the inert rows
    are dropped from `rows`, or it lists them as framework-resolved.
  - [x] 2.5 `reconcile_at_startup` is at 49/50 (F7): extract (e.g. the covered + framework naming, or
    the read-and-quantise step) so it stays ≤ 50. No baseline entry.
  - [x] 2.6 `StartupReconciliation` gains `inexpressible: tuple[InexpressibleRemainder, ...] = ()`
    (defaulted, last); `_log_ok` adds `inexpressible_remainders`. Docstring of `broker` amended:
    "the broker's exact read; the cache matches it at each instrument's expressible precision".
  - [x] 2.7 Update `TestEveryEmittedReconcileRecordIsPinned`: `_drive_every_path` drives a fractional
    path; the NFR26 parametrized scenario (`test_no_record_carries_a_raw_account`) must emit the new
    name too (add a fractional broker holding to its scenario).

- [x] **Task 3 — Runtime cycle** (AC: #2, #3, #4) — `src/core/live_runtime_reconcile.py`
  - [x] 3.1 Failing unit tests first (`tests/unit/core/test_live_runtime_reconcile.py`):
    - AC #2: empty cache + unresolved row (symbol `T 4.5 02/15/36`) → logged `unresolved` once
      (after the ordinary debounce), `lose_and_recover()` → within a few ticks
      `monitor.state is CONNECTED` and `submission_withheld is False`; a runtime `reconcile.ok` is
      emitted while the row stands; no `reconcile.hold_back_expired` ever (D-G).
    - AC #3: cache `NVDA +22` + unresolved symbol `NVDA` → the grant stays withheld after a
      recovery; the existing D1 tests (`…holds_back_only_the_rows_it_could_mask`,
      `…hold_back_ends_after_the_bound…`, `…starts_the_bound_over`, the `BRK B` spellings,
      `…of_another_symbol_holds_nothing_back`) stay green unchanged.
    - AC #4: broker `22.5`, cache `22` → no `reconcile.discrepancy`, no report, exactly **one**
      `reconcile.inexpressible_remainder` (WARNING, `scope=runtime`) over ≥ 5 cycles; the runtime
      `reconcile.ok` carries `inexpressible_remainders={NVDA: "0.5"}`; a changed broker quantity
      (`22.7`) is named again; cleared then returning is named again.
    - AC #4 on a reconnect cycle: a remainder first seen there is named `scope=reconnect` and does not
      withhold the grant.
    - D-H: a stale-order candidate on an instrument whose row is inert is skipped that cycle.
    - Rewrite `test_an_unresolved_broker_row_withholds_the_grant` deliberately (F8).
  - [x] 3.2 `observe()`: build the expressible state from the cache after the fingerprint check;
    compare against it; `_Observation` gains `expressible: BrokerState`,
    `remainders: tuple[InexpressibleRemainder, ...]`, `held: frozenset[str]` (from the same `cached`
    snapshot). `broker` stays the exact read.
  - [x] 3.3 `_act`: `correct(...)` and `verify_corrected(...)` take `observation.expressible`
    (**not** `broker` — the re-verify against the exact read would raise `DISCREPANCY_REMAINS` and
    stop the session); `_HoldBack.masking` gets only the matching unresolved rows (D-G); inert =
    framework-unresolvable ∪ non-matching lookup misses; the stale-order skip covers every observed
    row (D-H).
  - [x] 3.4 A `_Remainders` tracker (the `_Unresolved` shape) called once per completed observation,
    **before** `_act` can raise; `_OkLog.emit` adds `inexpressible_remainders` from the observation.
  - [x] 3.5 Keep `RuntimeReconciler` ≤ 95 statement lines (module-level helpers, not methods).
    Update the module docstring's "What a confirmed row does" paragraph.

- [x] **Task 4 — `live reconcile` stays exact** (AC: #5)
  - [x] 4.1 `tests/unit/services/test_reconciliation_service.py`: broker `NVDA 22.5` vs session `22`
    → one non-matching line, `difference == Decimal("0.5")`, `is_clean is False`, rendered
    `difference=+0.5 DISCREPANCY`, and `live_reconcile.exit_code_for(report) == 5`.
  - [x] 4.2 `tests/component/core/test_live_reconcile.py`: `run_reconcile` with a broker double
    reading `22.5` and a view of `22` → the report is not clean, one `reconcile.discrepancy` record
    naming `difference="0.5"`, exit 5.
  - [x] 4.3 A source pin that `src/services/reconciliation_service.py` and `src/core/live_reconcile.py`
    never reference `at_expressible_precision` / `expressible_broker` (the exactness is structural).
    One sentence in `reconciliation_service`'s docstring: deliberately not the expressible precision
    (Story 5.7, FR36).

- [x] **Task 5 — Component proofs on a real `LiveExecutionEngine`** (AC: #1, #4)
  - [x] 5.1 `tests/component/core/test_live_startup_reconcile_engine.py`: canary **F3** (the native
    pass over-imports `INTERNAL-DIFF +1` for broker `22.5` / strategy `+22`), then the proof: the
    phase corrects it, `open_positions() == {strategy: 22}`, clean, remainder named, the correcting
    report's quantity is exactly `22`. Same for the `0.5`-only shape (ends flat).
  - [x] 5.2 Startup with an unresolved `IB-CONID-*` row of an unrelated symbol beside a matched strategy
    position → clean, nothing written, the strategy's own position untouched.
  - [x] 5.3 `tests/component/core/test_live_runtime_reconcile_engine.py`: broker `22.5`, strategy
    `+22`, several cycles → no report reaches the engine, `reconcile.ok`, one remainder record.

- [x] **Task 6 — Docs and the operator procedure** (AC: all; NFR33)
  - [x] 6.1 `docs/qa/phase3-live-verification.md`: **Procedure P22** ("holdings the adapter cannot
    express are named, not refused") — P22a `live reconcile` exact (exit 5 on a fractional line),
    P22b `live start` not refused + the records, P22c a reconnect re-grants beside an unrelated
    unresolvable holding; preconditions say the holding must already exist or be arranged **by the
    operator by hand** (no story session submits orders); Result log row "⏳ Defined, not run"; a row
    in the phase-gate evidence index (Reconciliation). If Story 5.8 lands a P22 first, renumber at
    merge (the 4.5/4.7 P19/P20 precedent).
  - [x] 6.2 `docs/agent/nautilus.md` — "Startup Reconciliation" rules and "Runtime Reconciliation"
    item 3: the same-symbol rule, the expressible precision (with F1/F3), the new record.
  - [x] 6.3 `README.md` — the `live start` paragraph (one sentence each: an unresolvable contract of
    an unrelated symbol is named, not refused; a fractional remainder is named and judged at whole
    shares) and the `live reconcile` note (stays exact: a fractional remainder is exit `5`).
  - [x] 6.4 `CLAUDE.md` — the `EMITTED_RECONCILE_EVENTS` entry: four names since Story 5.7.
  - [x] 6.5 `_bmad-output/implementation-artifacts/deferred-work.md` — D2 marked resolved by 5.7;
    the 4.8 code-review "skip set omits the unresolvable rows" item resolved (D-H); a new "Deferred
    from: story-5.7" section for anything left (at least D-A's narrower reading, owner: the PO).

- [x] **Task 7 — Gates** (all)
  - [x] 7.1 `uv run ruff check .`, `uv run ruff format --check .`, `make typecheck`.
  - [x] 7.2 `make test-unit`, `make test-component`; `uv run pytest tests/integration/core --forked`
    (the `_STDLIB_AND_FIRST_PARTY` scan — no new stdlib import is planned in a `live_*` module; if one
    is added, add it there by hand).
  - [x] 7.3 Mutation spot-checks (copy files to a scratchpad, never `git stash`): truncation →
    `make_qty` rounding; drop the "matching" predicate (refuse/withhold every unresolved row); compare
    the re-verify against the exact read; the `_Remainders` tracker logging every cycle. Each must
    turn a test red.

### Review Findings

Code review 2026-10-07 — three layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor),
triaged, decisions ruled by the PO (Allay) the same day. Every patch landed test-first (each new or
strengthened test was run red against the pre-fix code, then green).

- [x] [Review][Decision] R1 (Blind Hunter #1) — a FLAT strategy that trades NVDA ignored an
  unresolvable NVDA-symbol row and could buy on top of a holding it cannot see (pre-5.7 this
  refused) — **RULED: the session's traded instruments' symbols count as held**, at startup and at
  runtime (reconnect grant and D1's `_HoldBack`). `held_symbols(cached, traded_instrument_ids)`;
  `reconcile_at_startup(..., traded_instruments=)` / `RuntimeReconciler(..., traded_instruments=)`;
  the runner passes `session_instrument_ids(spec)` (new, `live_session_node.py`). Tests:
  `TestTradedInstrumentsCountAsHeld` (model), `TestATradedInstrumentCountsAsHeld` (startup),
  `TestATradedInstrumentCountsAsHeldWhileRunning` (runtime),
  `TestTheRunnerHandsReconciliationTheTradedInstruments` (runner, through `runner.run()`). D-A
  amended.
- [x] [Review][Decision] R2 (Edge #2 / Blind #7) — truncating to `size_increment` cuts an exact
  quantity when the increment is coarser than the precision (`150` @ `100` → `100`) — **RULED:
  truncate to `size_precision`** (`Decimal(1).scaleb(-size_precision)`); renamed `size_increments`
  → `size_steps`, `InexpressibleRemainder.size_increment` → `size_step`, record field → `size_step`
  (CLAUDE.md, `docs/qa` P22, `docs/agent/nautilus.md` updated). Tests:
  `TestTheStepIsTheSizePrecision` (precision 0 / increment 100 holding 150 → exact, no correction;
  equity 22.5 → 22 with remainder 0.5, `size_step="1"`). D-C amended.
- [x] [Review][Decision] R3 — D-A's narrower reading ("broker 0" rows only) — **RULED: keep
  literal.** No code change; recorded in D-A; the "owner: the PO" open question in
  `deferred-work.md` closed.
- [x] [Review][Decision] R4 — D-F's two-record cadence (startup once, runtime once more on first
  sighting) — **RULED: keep.** No code change beyond P2; recorded in D-F.
- [x] [Review][Patch] P1 — `symbol_key(instrument_id.split(".", 1)[0])` read `BRK.B.NYSE` as `BRK`
  [`src/models/position_reconciliation.py`] — new `instrument_symbol_key` uses `rsplit(".", 1)`,
  used by `PositionDiscrepancy.symbol_key` and `held_symbols`. Test:
  `test_a_dotted_symbol_keeps_everything_before_the_venue`.
- [x] [Review][Patch] P2 — `_Remainders` de-duplicated on the whole record, so 22.5 → 23.5 (same
  0.5) was named again — P22b's Fail criterion [`src/core/live_runtime_reconcile.py`] — keyed on
  `(instrument_id, remainder)`. Tests: `test_an_unchanged_remainder_is_not_renamed_when_the_strategy_trades`,
  `test_a_remainder_whose_value_changes_is_named_again` (22.75).
- [x] [Review][Patch] P3 — the step lookup caught only `ValueError`/`TypeError`/`OverflowError`;
  an `AttributeError` crashed the phase/tick [`src/core/live_startup_reconcile.py`] — catches
  `AttributeError`; a missing, `None` or negative `size_precision` leaves the row exact. Test:
  `TestAStepLookupThatFailsLeavesTheRowExact` (4 cases).
- [x] [Review][Patch] P4 — runtime `_act` judged inertness from the *debounced* rows, so an
  unrelated unresolved row first seen on a reconnect cycle withheld the grant ≥ 60 s, and a
  flickering one forever (AC #2) [`src/core/live_runtime_reconcile.py`] — every unresolved row in
  the raw observation that matches no held symbol is inert immediately (`_unresolved_rows`). Tests:
  `test_a_row_first_seen_after_the_loss_is_re_granted_on_the_first_cycle` (strengthened from
  `…_once_debounced`: grant on the first post-reconnect tick, `submission_withheld is False`),
  `test_a_flickering_unrelated_row_never_withholds_the_grant`.
- [x] [Review][Patch] P5 — `_UNRESOLVED` claimed a same-symbol cached-position match for D-B's
  no-symbol row too [`src/core/live_startup_reconcile.py`] — split into `_UNRESOLVED_SAME_SYMBOL`
  (cached position **or traded instrument**, per R1) and `_UNRESOLVED_NO_SYMBOL`, composed per the
  refused rows by `_unresolved()`. The instrument-not-in-cache refusal does **not** reuse
  `_UNRESOLVED` (it is `_correct`'s own text) — pinned. Tests:
  `TestEachUnresolvableRefusalSaysWhyTruthfully` (4).
- [x] [Review][Patch] P6 — the unit AC #5 pin lacked Task 4.1's exit code; the structural
  `live_reconcile.py` pin's docstring overclaimed [`tests/unit/services/test_reconciliation_service.py`,
  `tests/component/core/test_live_reconcile.py`] — `live_reconcile.exit_code_for(report) == 5`
  added; docstring narrowed to "the driver's own source never names the helpers". (Test-only: AC #5
  already held, so there was no pre-fix red.)
- [x] [Review][Patch] P7 — `test_a_held_symbol_is_refused_writing_nothing` compared records as a
  set, blind to a duplicate [`tests/unit/core/test_live_startup_reconcile.py`] — compared as a list.
- [x] [Review][Patch] P8 — P22's D6 grep had no word boundaries; P22d grepped before the Gateway
  restart; the `RUNNER_PID=$!` comment was wrong under `uv run`
  [`docs/qa/phase3-live-verification.md`] — `grep -c -w -E "162|10182|366"`; P22d reordered
  (restart, wait for `connection.restored`, then grep); the runner resolved by `pgrep -f` with the
  signal-forwarding note (P7/P9/P21). Still "defined, not run".
- [x] [Review][Patch] P9 — docs/docstrings stating the old behaviour updated in the same change:
  module docstrings of `position_reconciliation.py`, `live_startup_reconcile.py`,
  `live_runtime_reconcile.py`; CLAUDE.md's `EMITTED_RECONCILE_EVENTS` entry; `docs/agent/nautilus.md`
  (startup + runtime); README's `live start` paragraph; P22.
- [x] [Review][Defer] W1 — a same-symbol unresolved row keeps withholding the reconnect grant after
  D1's hold-back expires, for the rest of the session (D2's ruling, AC #3 "unchanged") — deferred
  (owner: the PO / beside Story 5.8)
- [x] [Review][Defer] W2 — an unrelated unresolved row logs at ERROR while the start proceeds and
  `reconcile.ok` reports `discrepancies=0` (severity vs outcome) — deferred (owner: the PO)
- [x] [Review][Defer] W3 — D-H's widened stale-order skip set covers a held-back same-symbol
  instrument indefinitely, so a stranded order on it is never cleared — deferred (owner: Story
  4.8's follow-up / the PO)
- [x] [Review][Defer] W4 — non-equity symbol matching (futures `ESZ5` vs `ES`, FX `EUR/USD` vs
  `EUR`, options vs underlying); Phase 3 trades equities/ETFs — deferred (owner: whichever story
  first adds a non-equity)
- [x] [Review][Defer] W5 — `live reconcile` exits 5 forever on a healthy DRIP account with nothing
  marking the line as an inexpressible remainder (FR36 exactness by design) — deferred (owner: the
  PO)
- [x] [Review][Defer] W6 — a correction can make an inert row count again and refuse
  `DISCREPANCY_REMAINS`; every later start then refuses `UNRESOLVABLE_DISCREPANCY` with no operator
  remedy text (already in the Dev Agent Record's Debug Log) — deferred (owner: the PO, with R3)
- [x] [Review][Defer] W7 — a fractional row whose instrument the cache lacks still refuses
  `UNRESOLVABLE` at startup (story scope boundary) — deferred (owner: whoever first meets it live)

**Summary:** 4 decisions ruled (R1–R4), 9 patches applied (P1–P9; R1 and R2 also landed as code),
7 deferred (W1–W7). Dismissed: the D1-expiry negative-test concern (the five untouched D1 tests
exercise the new path and pass), the P22 four-part split, and the duplicates merged in triage.

## Dev Notes

### The current surface — exact extension points

- `src/models/position_reconciliation.py` — `compare_positions` (unchanged, exact over the state it
  is given), `PositionDiscrepancy.broker_resolved` / `broker_symbol` / `symbol_key`, module-level
  `symbol_key`. New pure helpers go here (stdlib only; Story 4.6's service imports this module).
- `src/core/live_startup_reconcile.py` — `reconcile_at_startup` (lines 328-416), `_to_act_on`,
  `_framework_resolved`, `_correct`, `_log_taken`, `position_report` (shared with runtime),
  `log_discrepancy` (shared with runtime), `_log_ok`, `EMITTED_RECONCILE_EVENTS`, `_UNRESOLVED`.
- `src/core/live_runtime_reconcile.py` — `observe` (390-410), `_Observation`, `RuntimeReconciler._act`
  (340-373), `._run` (311-338, **Story 5.8 is editing this method in parallel** — touch it as little
  as possible), `_Unresolved`, `_HoldBack.masking`, `_OkLog.emit`, `correct`, `verify_corrected`.
- `src/services/reconciliation_service.py` / `src/core/live_reconcile.py` — **read only** (AC #5 is
  a pin).
- `src/core/live_session_resume.py:212` reads `reconciliation.broker.positions` for its
  informational `broker_quantity` — keep `StartupReconciliation.broker` the exact read.

### Scope boundaries — do NOT build

- No change to `live_broker_state.py` (the reader keeps reporting the exact quantity and the
  `IB-CONID-*` fallback) or to `reconciliation_service.py`'s comparison.
- No new `src/core/live_*.py` module (the hand-maintained guard lists: `NODE_FACING_MODULES`,
  `STOP_PATH_MODULES`, `TestImportPurity.MODULES` — CLAUDE.md Anti-Patterns). If one becomes
  unavoidable, add it to every list in the same commit.
- No change to the instrument-not-in-cache path (refused at startup, inert at runtime) — not this AC.
- No order, no flatten, no cache write other than `reconcile_execution_report` (the existing
  `TestTheOnlyEngineMutationIsTheFrameworksOwnEntryPoint` scans must stay green).
- Nothing in `src/api/**`, `templates/**`, `src/services/backtest_query.py`; no migration; no new
  table/column. This story does not touch sealing (Stories 5.1–5.6).
- Do not narrow D-A on your own (feedback memory: literal conditions go to the PO).

### Hazards

- **The exact read leaking into the judgement.** Five call sites compare against the broker state
  (startup: compare, `_framework_resolved`, `_correct`, `_log_taken`, re-verify; runtime: compare,
  `correct`, `verify_corrected`/`_log_taken`). Every one must get the expressible state; a test per
  call site (the F3 shape catches the re-verify ones).
- **`make_qty` is not a truncation** (F1). Never compute the expressible quantity with it.
- **`reconcile_at_startup` is at 49/50** — extract before adding, not after a red size-cap test.
- **The NFR26 parametrized scan runs one scenario for every name in the constant** — its scenario
  must emit the new record or the parametrized case fails "was not emitted by the scenario".
- **structlog `capture_logs`** records `log_level`; assert WARNING as `"warning"`.
- **Parallel Story 5.8** extends `_FailureStreak`/`_run` and probably adds a P22: keep your diff in
  `_run` minimal, and expect a renumber.

### Testing standards summary

Unit tier for the model and both phases with duck-typed doubles (the existing `_Cache` / `_Engine` /
`_World` harnesses); component tier for the real `LiveExecutionEngine` proofs (no `TradingNode`:
the C-logging guard fixture); no integration/db test is needed (no persistence change). TDD: each
subtask's failing test first. Names say the behaviour (`test_an_unresolved_row_of_an_unrelated_symbol_does_not_refuse_the_start`).
`pytestmark = pytest.mark.unit` / `component` as the files already do.

### Project Structure Notes

- Touched: `src/models/position_reconciliation.py`, `src/core/live_startup_reconcile.py`,
  `src/core/live_runtime_reconcile.py`, the docstring of `src/services/reconciliation_service.py`;
  tests beside each; docs listed in Task 6.
- No new modules, no new dependencies, no guard-list edits expected.

### References

- `_bmad-output/planning-artifacts/prd-epic5-scope.md` — Story 5.7 ACs, NFR33's traceability rule.
- `_bmad-output/planning-artifacts/epics.md:1962-2002` — Story 5.7.
- `_bmad-output/implementation-artifacts/deferred-work.md:3739-3765` — PR #35 rulings D1/D2 and
  D2's problem statement; `:3889-3896` — the 4.8 skip-set item (D-H); `:3564-3567` — D-G of 4.5/4.7
  ("any unresolvable broker position refuses every start — kept"), superseded by this story.
- `docs/agent/nautilus.md` §"Startup Reconciliation (Story 4.2)", §"Runtime Reconciliation (Story 4.3)".
- `docs/qa/phase3-live-verification.md` — P17/P18 (startup/runtime procedures), P21 (format).
- CLAUDE.md — "Membership-pinned lists", `EMITTED_RECONCILE_EVENTS`, size caps, guard lists.

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), via the BMAD dev-story workflow in an automated harness
session (worktree `hx-p3e5-5-7-20261007-161809-2`, branch `harness/s5-7-20261007-161809-2`).

### Debug Log References

- **F3 measured at drafting** (`/tmp/probe_frac.py`, the component `_Harness` with
  `_IBShapedClient`): native pass over broker `22.5`/strategy `+22` → `INTERNAL-DIFF +1`;
  `23.5`/`+23` → `+1`; `0.5`/nothing → `+1`. Now pinned by the canary
  `test_f3_the_frameworks_own_pass_over_imports_a_whole_share`.
- **One defect found while going green (Task 2):** the startup re-verify still counted the inert
  unresolved row and refused `DISCREPANCY_REMAINS`. Fixed with `_still_to_answer`, which applies the
  same-symbol rule against the *corrected* cache. That also pins the fail-closed edge where a
  correction puts a same-symbol position in the cache
  (`test_a_correction_that_puts_the_symbol_in_the_cache_makes_the_row_count_again`).
- **Mutation sweep (Task 7.3, `/tmp/mutate_57.py`, files copied and restored, never `git
  stash`): 14/14 killed.** The mutations were half-even rounding; no same-symbol rule; the
  runtime and startup re-verify against the exact read; the startup compare and
  `framework_resolved` against the exact read; the remainder tracker naming every cycle; `live
  reconcile` judging at the expressible precision; the D-H skip set reverted; the D-G hold-back
  counting every unresolved row; inert lookup misses withholding the grant; startup refusing
  every unresolved row; the startup remainder record dropped; the runtime `reconcile.ok` losing
  its remainders.
- **Size caps (statement metric):** `reconcile_at_startup` 49 → 47 (extraction of
  `_name_settled` / `_set_aside_unresolved`); `RuntimeReconciler` 90 → 93 (≤ 95 target);
  `live_startup_reconcile.py` 334 → 397, `live_runtime_reconcile.py` 331 → 357,
  `position_reconciliation.py` 185 → 244. No baseline entry; `test_size_caps.py` green.

### Completion Notes List

- **AC #1 / #3 (startup).** `_set_aside_unresolved` refuses `UNRESOLVABLE_DISCREPANCY` only for an
  unresolved row whose symbol matches a cached position (any owner, the D1 `symbol_key`; an empty
  symbol fails closed, D-B). Any other is logged `resolution=unresolved` once and dropped from the
  judgement. The refusal text now says why the row matters ("under the same symbol as a position
  this session's cache holds").
- **AC #2 / #3 (runtime).** `_act` treats a confirmed lookup miss that masks nothing as inert, so it
  no longer withholds the reconnect grant. A same-symbol miss still withholds it, and D1's bounded
  hold-back is unchanged. D-G: the hold-back counter only sees rows that may mask, so a bond no
  longer logs `reconcile.hold_back_expired` for a hold-back it never held.
- **AC #4 (both phases).** New pure helpers in `position_reconciliation.py`:
  `expressible_quantity` (truncation toward zero, never `make_qty`'s half-even),
  `InexpressibleRemainder` and `at_expressible_precision`. The core helpers
  (`size_increments` / `expressible_broker`) read the increment from the instrument the cache
  holds. Both phases compare, correct and re-verify against the expressible view. Every record
  that reports the broker keeps the exact read (D-D). The new `reconcile.inexpressible_remainder`
  (WARNING) is emitted once per reconciliation per standing value (D-F), and `reconcile.ok` (startup
  and runtime) carries `inexpressible_remainders`. `StartupReconciliation` gained a defaulted
  `inexpressible` field. The F3 over-import is corrected down broker-ward like any net
  disagreement.
- **AC #5.** `live reconcile` was already exact (F6). It is now pinned at unit tier (line,
  render, not clean) and component tier (`run_reconcile` → exit 5, no remainder record), plus a
  structural pin that neither the service nor the driver references the expressible helpers.
- **Membership pin changed deliberately.** `EMITTED_RECONCILE_EVENTS` went 3 → 4, updated together
  with its exact-set pin (`test_the_constant_is_exactly_the_four_names_since_story_5_7`), the
  both-directions scan (`_drive_every_path`), the NFR26 parametrized scenario and CLAUDE.md.
- **Debt dispositioned.** D2 of PR #35 is resolved. The Story 4.8 code-review item "AC #5's skip
  set omits the unresolvable rows" is resolved by D-H, since this story touched `_act`'s return.
  The Story 4.2 "kept" D-G entry is marked superseded. A new "Deferred from: story-5.7" section
  records D-A's literal-reading consequence (owner: the PO), the correction edge, the unchanged
  instrument-not-in-cache path, D-F's two-record cadence, and P22 being defined but not run.
- **Behaviour reversed deliberately (F8), each test docstring says so:**
  `test_an_unresolved_broker_row_no_cached_position_shares_is_named_not_refused`,
  `test_an_inexpressible_broker_quantity_is_named_and_the_rest_corrected` and
  `test_an_unresolved_broker_row_of_a_held_symbol_withholds_the_grant`.
  `…beside_a_strategy_position_is_not_a_contradiction` gained `symbol="NVDA"`.
- **Operator procedure:** P22 was added to `docs/qa/phase3-live-verification.md` with an
  evidence-index row, recorded "⏳ Defined, not run" (it needs a holding only the operator can
  arrange). No `live start`, `live seal` or `live reconcile` was run against a real session, and no
  order was submitted.
- **Gates:** `ruff check .` and `ruff format --check .` clean. `make typecheck` clean, and
  `mypy src/models/position_reconciliation.py` clean. Unit 3449 passed. Component 1937 passed / 16
  skipped (the pre-existing report-command skips). `tests/integration/core --forked` 100 passed /
  2 skipped (the IBKR parity gate). No integration/db test was needed: no persistence change. Zero
  UI or API change, no migration, no new table, column or module, no new dependency.

**Code review round (2026-10-07)** — see "Review Findings" under Tasks/Subtasks for each item.

- **R1 wiring, and the size cap it met.** The traded instruments come from the spec's bar types
  (`session_instrument_ids`, beside `session_strategy_ids` in `live_session_node.py`; lazy
  `BarType` import like `materialise_strategy`'s). The runner passes them at the two construction
  sites — but `LiveSessionRunner` sits exactly at its 417-line ratchet ceiling, so the two new kwarg
  lines were offset **in the same class**, not re-baselined: two pairs of plain attribute
  assignments in `__init__` were combined into tuple assignments, the pattern the same `__init__`
  already uses (`self._deadline, self._trader_started, self._ownership_lost = …`). Class 417 → 417,
  `__init__` 47 → 45, file 492 → 493. `test_size_caps.py` untouched.
- **Runtime shape.** `RuntimeReconciler` keeps the traded symbols as `self._traded` (93 → 94, under
  the ≤ 95 target); the union with the cached symbols happens in the new module-level
  `_unresolved_rows(observation, traded)`, which also carries P4 (raw rows, not debounced) and
  replaces `_may_mask`. `observe()` and `_run` are untouched (Story 5.8's method).
- **Interpretations.** R1's "every instrument the session's strategies trade" is read as every bar
  type of every strategy (the subscription set), not only `bar_types[0]` — a wider set only fails
  closed more often. P1's "everywhere this pattern appears" is read as the reconciliation code;
  the remaining first-dot splits are in backtest code (`backtest_runner.py`, `backtest_request.py`,
  `_backtest_helpers.py`, `nautilus_converter.py`) and were left untouched as out of scope. P5:
  when one refusal names both a symbol-matched row and a no-symbol row, the message states both
  reasons.
- **Gates (review round):** `ruff check .` clean, `ruff format --check .` clean (564 files),
  `make typecheck` clean (120 source files). `make test-unit` **3477 passed**. `make
  test-component` **1940 passed / 16 skipped**. `uv run pytest tests/integration/core -v -n auto
  --forked` **100 passed / 2 skipped**. No flake re-runs were needed. Nothing live was run: no
  `live` command, no diagnostics script, no broker, Docker or alembic.

### File List

- `src/models/position_reconciliation.py` — modified (expressible precision, the same-symbol
  predicate, `InexpressibleRemainder`, `StartupReconciliation.inexpressible`)
- `src/core/live_startup_reconcile.py` — modified (startup phase, shared helpers, new record)
- `src/core/live_runtime_reconcile.py` — modified (runtime cycle, `_Remainders`, D-G, D-H; review:
  R1 traded symbols, P2, P4 `_unresolved_rows`)
- `src/core/live_session_node.py` — modified (code review R1: `session_instrument_ids`)
- `src/core/live_session_runner.py` — modified (code review R1: passes the traded instruments to
  both reconciliations; two `__init__` assignments combined to hold the class at its ceiling)
- `src/services/reconciliation_service.py` — modified (docstring only)
- `tests/unit/models/test_position_reconciliation.py` — modified
- `tests/unit/core/test_live_startup_reconcile.py` — modified
- `tests/unit/core/test_live_runtime_reconcile.py` — modified
- `tests/unit/services/test_reconciliation_service.py` — modified
- `tests/component/core/test_live_startup_reconcile_engine.py` — modified
- `tests/component/core/test_live_runtime_reconcile_engine.py` — modified
- `tests/component/core/test_live_reconcile.py` — modified
- `tests/component/core/test_session_runner_phases.py` — modified (code review R1: runner wiring)
- `docs/qa/phase3-live-verification.md` — modified (Procedure P22, evidence-index row)
- `docs/agent/nautilus.md` — modified
- `README.md` — modified
- `CLAUDE.md` — modified (`EMITTED_RECONCILE_EVENTS` entry)
- `_bmad-output/implementation-artifacts/deferred-work.md` — modified
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — modified (this story's key only)
- `_bmad-output/implementation-artifacts/5-7-name-holdings-the-adapter-cannot-express-instead-of-refusing-or-hiding-them.md` — new

## Change Log

- 2026-10-07 — Story drafted (ready-for-dev). Ultimate context engine analysis completed —
  comprehensive developer guide created. F3 measured at drafting against the component harness.
- 2026-10-07 — Implemented (dev-story): same-symbol rule for unresolved broker rows at startup and
  for the reconnect grant; fractional remainders judged at whole increments and named once;
  `live reconcile` pinned exact; D-G / D-H; P22 defined, not run. Status → review.
- 2026-10-07 — Code review (three layers) applied: R1 traded symbols count as held (startup,
  reconnect grant, D1 hold-back; runner wiring), R2 step = `size_precision` (`size_steps`,
  `size_step`), R3/R4 recorded, P1–P9 patched test-first, W1–W7 deferred to `deferred-work.md`.
  Status → done.
