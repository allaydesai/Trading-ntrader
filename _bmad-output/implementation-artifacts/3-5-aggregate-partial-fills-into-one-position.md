# Story 3.5: Aggregate Partial Fills into One Position

Status: in-progress

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want an order filled in pieces to produce one position at a volume-weighted price,
so that a trade record does not report the first fill's price as though it were the whole entry.

## Why this story is shaped the way it is

This story **introduces the trade recorder and proves its arithmetic**; it writes no database row.
The epic fixes the split: *"this story introduces `live_trade_recorder` with its **aggregation**
logic only, returning a domain trade object. Story 3.6 adds the database write path to the same
module"* (`epics.md:1374-1376`). Every previous Epic 3 story fenced this territory off for it:
3.2 ("**No trade recorder, no DB writes, no fencing column** — Stories 3.5/3.6", 3.2:360-361), 3.3
("`events.position.*` is not subscribed and stays that way — `PositionOpened/Changed/Closed` are
Stories 3.5/3.6's input (the trade recorder)", 3.3:488-491; also `src/core/live_order_path.py:15-17`),
3.4 ("Do not model a partial fill in the in-flight tests; that is 3.5's aggregation plus 4.2's
reconciliation", 3.4:576-580).

Five measured facts, all read from the installed 1.220.0 wheel by fresh-interpreter probes on
2026-09-11 (full citations in Dev Notes), decide the design:

1. **Nautilus already volume-weights, and the position events carry the result.**
   `Position._calculate_avg_px` is `(avg_px*qty + last_px*last_qty) / (qty + last_qty)`
   (`model/position.pyx:757-774`). Measured: fills 40@100 then 60@110 → `Position.avg_px_open ==
   106.0`, and `PositionChanged.avg_px_open == 106.0`, not `100.0`; exits 50@120 then 50@130 →
   `PositionClosed.avg_px_close == 125.0`. A single fill on a side reduces to that fill's price by
   the same formula (AC #4 holds by construction). `avg_px_open`/`avg_px_close` arrive as Python
   **`float`** (`double` in the ctor; `PositionClosed.__doc__` says `Decimal` and is wrong).
2. **No position event carries commission.** `PositionClosed.to_dict()` keys contain no
   `commission*` at all; `realized_pnl` is *net* of commission (measured: two entry fills with
   1.25 + 2.50 commission → `PositionChanged.realized_pnl == -3.75 USD` before any exit).
   `Position.commissions()` **does** sum across fills — `list[Money]`, one entry per currency
   (`model/position.pyx:667-676`; measured 1.25 + 2.50 + 1.00 + 1.00 → `[Money(5.75, USD)]`).
   FR29's "sum charged across all constituent fills" therefore comes from the **`Position`
   object**, looked up by `event.position_id`, never from the event alone and never from an
   in-memory tally of `OrderFilled` events (3.3 measured that such a tally resets across a process
   run, `live_order_path.py:410-413`, and that Nautilus publishes fills it refused to apply,
   commit `1482198`).
3. **A `Position` resets in place when a fill arrives while it is FLAT** — `_events.clear()`,
   `_trade_ids.clear()`, **`self._commissions = {}`**, `peak_qty` zeroed, `opening_order_id`
   replaced (`model/position.pyx:489-501`). Under the live node's NETTING position IDs (the only
   ID this project has ever seen live is `NVDA.NASDAQ-SMACrossover-000`,
   `docs/qa/phase3-live-verification.md:758`) **one `PositionId` hosts successive round trips**.
   Two consequences: the recorder must read `commissions()` **synchronously inside the
   `PositionClosed` dispatch**, never later; and `str(position_id)` alone does not identify a
   trade across legs of the same instrument.
4. **The backtest side already reads exactly these fields.** `BacktestPersistenceService
   .save_trades_from_positions` builds every backtest trade from `avg_px_open`, `avg_px_close`,
   `peak_qty`, `realized_pnl`, `duration_ns`, `ts_opened`, `ts_closed`, `opening_order_id`,
   `closing_order_id`, `entry` and `commissions` (`src/services/backtest_persistence.py:437-526`).
   The PRD's obligation is parity — *"persist paper results in exactly the same shape as backtest
   results so the existing 2–10 run comparison works on them unchanged"* (`prd.md:87-92`) — so the
   recorder reads the **same Nautilus fields with the same conversions**, and the only place it is
   allowed to be *better* is where the backtest path is measurably wrong (it takes
   `commissions[0]` and ignores the rest, `:452-458`; recorded as deferred work, not fixed here).
5. **Position events reach the strategy, but nothing of ours listens.** `Strategy.register()`
   subscribes the bound `handle_event` to `events.order.{id}` **and** `events.position.{id}`
   (`trading/strategy.pyx:314-315`); the exec engine pops `_pending_position_events` *before*
   publishing the order event (`execution/engine.pyx:1171-1184`), which is why Story 2.7's
   `handle_event` wrapper exists and must not be removed (retro AI 12,
   `epic-2-retro-2026-08-28.md:603-606`). Our own `OrderEventObserver` subscribes `events.order*`
   only (`live_session_runner.py:563`). A raise from any msgbus handler ends at Nautilus's silent
   `os._exit(1)` (`live_order_path.py:87-90`), so the recorder is a **second, independent
   subscriber on `events.position*` that never raises** — the observer's exact shape.

What is measured ABSENT today (the story's whole surface):

- No module named `live_trade_recorder` exists anywhere under `src/`.
- Nothing subscribes `events.position*`; a `PositionClosed` today produces one Nautilus log line
  and nothing of ours.
- No code converts a Nautilus position into `TradeBase` (`src/models/trade.py:15-43`) outside the
  backtest's pandas path; there is no `Money.as_decimal()` call in `src/`.
- No test anywhere asserts that a multi-fill position's *recorded* entry price is the
  volume-weighted average rather than the first fill's price. `Position.avg_px_open` is trusted by
  the backtest path with no canary; an upstream change to the formula would go green.

**Two placement decisions are made here, at drafting, and disclosed rather than left for review:**

- **`src/core/live_trade_recorder.py`, not `src/services/`.** `architecture.md:537-538` and `:576`
  draw the module under `src/services/`; every live module shipped in Epics 1–3 lives under
  `src/core/` and `src/services/` contains no `live_*.py` at all. The architecture's *reason* for
  services placement is AR38 ("the ONE bridge: Nautilus events → sync repo"), and the runner
  already honours AR38 the other way round — it holds a `SessionRecordPort` protocol
  (`src/core/live_session_record.py:68`) and the CLI injects the adapter. Story 3.6 will do the
  same for trades (a port on the recorder, the repository adapter injected from the CLI), which is
  what keeps this module inside `TestImportPurity` (`src.db`/`src.services` forbidden). Record the
  deviation in `deferred-work.md` as a one-line architecture amendment, owner: this story.
- **`tests/component/core/test_live_trade_recorder.py`, not `tests/component/…` flat.** AC #5
  names the flat path (`epics.md:1371-1372`, written 2026-08-03 before `tests/component/core/`
  existed); every Epic 1–3 live component suite lives under `core/`. The AC is honoured on
  substance and the path deviation is stated in AC #5's operationalisation and in
  `deferred-work.md`.

## Acceptance Criteria

1. **Given** an order filled across several partial fills at different prices
   **When** the position is formed
   **Then** the entry price is the volume-weighted average across all fills, not the first fill's
   price (FR25).
   *Operationalised: `aggregate_closed_position(position)` returns a `RecordedTrade` whose
   `trade.entry_price` equals the VWAP of the position's opening fills, computed **independently
   in the test** from the `OrderFilled` series in `Decimal` and quantized to 8 dp — for a
   terminating case (40@100.00 + 60@110.00 → `106.00000000`) **and** a non-terminating case
   (100@10.00 + 200@10.01 → `10.00666667`), with explicit assertions that the result equals
   neither the first fill's price nor the last fill's. The value is read from
   `position.avg_px_open` (fact 1) via `Decimal(str(value)).quantize(Decimal("0.00000001"))`, the
   conversion `backtest_persistence.py:489-493` uses, never `Decimal(float)` directly. The
   position-event double is a real `nautilus_trader.model.position.Position` built by applying
   hand-constructed `OrderFilled` events, then `TestEventStubs.position_closed(position)` — the
   3.2 recipe (3.2:486-489) — so the test is also a wheel canary on fact 1.*

2. **Given** a position exited across several partial fills
   **When** the trade is recorded
   **Then** the exit price is likewise volume-weighted across those fills.
   *Operationalised the same way on `trade.exit_price` from `position.avg_px_close` (50@120 +
   50@130 → `125.00000000`; and a non-terminating exit). A **mixed** case — multi-fill entry and
   multi-fill exit in one round trip — asserts both prices, `quantity == peak_qty` (**not**
   `PositionClosed.quantity`, which is `0` on a closed position and would fail the table's
   `CHECK quantity > 0`), `entry_timestamp`/`exit_timestamp` from `ts_opened`/`ts_closed` as
   tz-aware UTC built without float arithmetic, `holding_period_seconds == duration_ns //
   1_000_000_000`, `profit_loss == realized_pnl.as_decimal()` (net of commission, as the backtest
   path stores it), and `profit_pct == ((exit - entry) / entry) * 100` — the backtest formula at
   `backtest_persistence.py:504-507`, reproduced for parity even though it is not side-aware (see
   Hazards #6; routed, not fixed).*

3. **Given** partial fills
   **When** commission is accumulated
   **Then** the trade's commission is the sum charged across all constituent fills, not a single
   fill's charge (FR29).
   *Operationalised: fills carrying explicit, **distinct** commissions (1.25, 2.50 on entry; 1.00,
   1.00 on exit — built directly with `OrderFilled(...)`, because `TestEventStubs.order_filled`
   computes commission on `order.quantity` and yields `0.00 USD` for the stub cash account,
   3.3:538-540) produce `commission_amount == Decimal("5.75")` and `commission_currency ==
   "USD"`, read from `position.commissions()` through `Money.as_decimal()` / `money.currency.code`
   — never `str(money).split()`. Three edge pins: (i) an empty `commissions()` yields
   `Decimal("0")` in the position's settlement currency; (ii) more than one currency present
   yields the **settlement-currency** entry as `commission_amount` and one
   `trade.commission_mixed_currency` warning listing every entry as strings — loud, never a silent
   `[0]`; (iii) **read-time**: applying a fresh opening fill to the same (now FLAT) `Position`
   *before* calling the aggregation gives a different, wrong answer — the test that proves the
   recorder must read inside the `PositionClosed` dispatch (fact 3), and the reason
   `TradeRecorder.handle_position_event` does the lookup and the conversion in the same
   synchronous call.*

4. **Given** a position closed by exactly one fill on each side
   **When** the trade is recorded
   **Then** volume-weighting reduces to the single price, producing the same result as the simple
   case.
   *Operationalised: one fill in, one fill out → `entry_price` and `exit_price` equal the two
   fills' `last_px` exactly (as `Decimal`, 8 dp), `commission_amount` equals the two fills'
   commission sum, `fill_count == 2`. This case is **vacuous on its own** (a reader that returns
   `last_px` passes it); it is load-bearing only beside AC #1/#2, and the mutation sweep (M1, M5)
   proves that pairing.*

5. **Given** the aggregation logic
   **When** it is tested
   **Then** `tests/component/test_live_trade_recorder.py` covers multi-fill entry, multi-fill
   exit, and mixed cases against position-event doubles, with no broker involved (NFR32).
   *Operationalised at `tests/component/core/test_live_trade_recorder.py` (path deviation
   disclosed above), `pytestmark = pytest.mark.component`, under the autouse C-logging guard
   copied verbatim from `test_live_order_path.py:91-101`, never constructing a `TradingNode`,
   `BacktestEngine`, exec client or socket. The doubles are real `Position` objects plus
   `TestEventStubs.position_closed(position)`. The file also pins the **handler contract**:
   `PositionOpened` and `PositionChanged` produce no record and no sink call; `PositionClosed`
   produces exactly one `trade.aggregated` record and one sink call with the `RecordedTrade`; a
   sink that raises, a `cache.position()` that returns `None`, and a position whose
   `avg_px_open` is not convertible each produce one `trade.recorder_failed` record (severity
   `error`, `exc_info=True`) and **no raise** out of the handler (fact 5). Pure `Decimal` helpers
   (`to_price_decimal`, `select_commission`, `unix_nanos_to_utc`) get a unit-tier file with
   hand-rolled doubles and no Nautilus import, per project-context "extract pure logic".*

6. **Given** a running session
   **When** the node subscribes
   **Then** the runner installs the recorder on `events.position*` beside the order observer, so a
   live `PositionClosed` produces a `trade.aggregated` record bound to the session (AR41, `trade.*`
   namespace).
   *Added at drafting so the module is not dead code until 3.6 and so FR25 gets live evidence
   (Task 8). Operationalised: `_phase_subscribe` constructs `TradeRecorder(cache, log)` and
   subscribes through `self._subscribe(POSITION_EVENTS_TOPIC, recorder.handle_position_event)` —
   the helper that records the subscription for teardown (`live_session_runner.py:566-577`) —
   pinned in the same shape as `test_session_runner_order_path.py` pins the order observer. The
   runner diff is budgeted at ≤ 4 raw lines / ≤ 3 statements.*

## Tasks / Subtasks

- [x] Task 0: Standing checks at story start (retro process AI 6)
  - [x] 0.1 Is a Gateway up right now? Record yes/no and, if yes, whether the market is inside
    RTH — Task 8 depends on it and the answer decides whether the story can close as `done` in
    this session or goes to `review` with 8.2 `⏳ not yet run`.
  - [x] 0.2 Baselines re-measured before any edit (creation figures: unit 2441 · component
    1457/16 skipped · integration 284/2 skipped · e2e 1, head `438b0e4`, tree clean, submodule
    `06c00cb`). `make format && make lint && make typecheck` clean before the first edit.
- [x] Task 1: Measure the installed wheel before writing production code (AC: all)
  - [x] 1.1 Fresh-interpreter probe (the 3.2/3.3/3.4 pattern — `subprocess.run`, never
    `Popen`+`PIPE`; the `timeout` argument is itself the assertion; `PYTHONPATH=. uv run python
    <scratchpad script>`, scratch files not committed): build `AAPL_EQUITY =
    TestInstrumentProvider.equity("AAPL", "NASDAQ")`, a real order via `TestExecStubs.market_order`
    with an **explicit** `client_order_id` per order (two stub calls return the same frozen ID,
    3.3:186-189), and four hand-built `OrderFilled` events (explicit distinct `trade_id`s, explicit
    `commission=Money(...)`, `last_qty`/`last_px` per fill — the 19-parameter ctor,
    `model/events/order.pyx:4540-4560`). Apply to `Position(instrument, fill)` then
    `position.apply(fill)`; snapshot `TestEventStubs.position_opened/changed/closed(position)`.
    Re-confirm and **record with output**: `avg_px_open == 106.0`, `avg_px_close == 125.0`,
    `commissions() == [Money(5.75, USD)]`, `type(event.avg_px_open) is float`,
    `PositionClosed.quantity == 0`, `peak_qty == 100`, `event.entry`/`event.side` are enum ints
    (`OrderSide.BUY.name == "BUY"` is the rendering to use), `duration_ns`, `ts_opened`,
    `ts_closed`, `Money.as_decimal()` returns `Decimal`, `money.currency.code == "USD"`,
    `position.event_count`, and that `is_logging_initialized()` is unchanged before/after (the
    component file must stay under the autouse guard).
  - [x] 1.2 Measure the reset-in-place (fact 3): after the closing fill, apply a fresh BUY fill
    with a new `trade_id` to the same `Position`; record `commissions()` (expect only the new
    fill's), `event_count` (expect 1), `opening_order_id` (expect the new order), `peak_qty`.
    This is AC #3 pin (iii)'s fixture.
  - [x] 1.3 Measure how the live node exposes the cache and what position IDs it assigns: in a
    probe that builds **only** config (never a `TradingNode` in the component tier —
    `live_node_builder.py`'s config builder, the `TestExecutionRouting` shape), read the IB exec
    client config's `oms_type`/position-ID policy; confirm the node's cache is reachable as
    `node.cache` or `node.kernel.cache` (`system/kernel.py`), which is what `_phase_subscribe`
    will hand the recorder. Record whether a second round trip on one instrument reuses the
    `PositionId` (expected: yes, NETTING → `{instrument_id}-{strategy_id}`; the live transcript
    at `docs/qa/phase3-live-verification.md:758` shows `NVDA.NASDAQ-SMACrossover-000`). This
    decides `RecordedTrade.trade_key` (Task 5.2).
  - [x] 1.4 Measure the topic and ordering: with a real `MessageBus`, subscribe a recorder double
    to `"events.position*"` and publish a `PositionClosed` on
    `f"events.position.{strategy_id}"` — confirm the wildcard matches (the `ORDER_EVENTS_TOPIC`
    precedent, `live_order_path.py:175`) and that a handler raise propagates out of `publish_c`
    (so the containment in Task 5.4 is not optional). Record every measurement in the Dev Agent
    Record with the probe source.
- [x] Task 2: RED first — the pure arithmetic, unit tier (AC: #1, #2, #3 conversions)
  - [x] 2.1 New `tests/unit/core/test_live_trade_recorder_arithmetic.py`, `@pytest.mark.unit`, **no
    Nautilus import** (the unit tier runs without it; the module under test must therefore be
    importable without `nautilus_trader` — duck-typed `Any`, the `live_order_path.py:250-252`
    discipline). Hand-rolled doubles: a `_Money(amount: Decimal, code: str)` with `as_decimal()`
    and `.currency.code`.
  - [x] 2.2 `to_price_decimal(value: float) -> Decimal`: `100.1 → Decimal("100.10000000")`,
    `10.006666666666667 → Decimal("10.00666667")`; a `float("nan")` raises `ValueError` (the
    `backtest_persistence.py:495-496` trap — `Decimal("NaN").quantize()` raises
    `InvalidOperation`; the recorder converts that into one `trade.recorder_failed`, not a node
    death). RED captured at the moment each test is written.
  - [x] 2.3 `select_commission(commissions, settlement_code) -> tuple[Decimal, str, tuple[str,
    ...]]` (amount, currency, every-entry-as-string for the diagnostic): empty → `(Decimal("0"),
    settlement_code, ())`; one entry → its amount/code; two currencies with the settlement one
    present → the settlement entry plus both strings; two currencies with the settlement one
    absent → the first entry plus both strings (never a raise — the handler must always produce a
    trade).
  - [x] 2.4 `unix_nanos_to_utc(ns: int) -> datetime`: exact (`datetime(1970, 1, 1, tzinfo=UTC) +
    timedelta(microseconds=ns // 1_000)`, no float), tz-aware; `1_000_000_000 →
    1970-01-01T00:00:01+00:00`; sub-microsecond nanos truncate. And `holding_period_seconds`
    is `duration_ns // 1_000_000_000` (integer division — the backtest path's `int(x / 1e9)` is
    float division, kept out of this module; the two agree for every real duration).
- [x] Task 3: RED — the aggregation against real position doubles, component tier (AC: #1–#4)
  - [x] 3.1 New `tests/component/core/test_live_trade_recorder.py`, `pytestmark =
    pytest.mark.component`, autouse C-logging guard copied verbatim from
    `test_live_order_path.py:91-101`. A `_round_trip(entry_fills, exit_fills)` helper that returns
    `(position, PositionClosed)` from `(qty, px, commission)` tuples via the Task 1.1 recipe; each
    fill gets a distinct `TradeId` and each side its own explicit `ClientOrderId`. Put the helper
    in this file — architecture.md:546 names a `TestPositionEvents` double under
    `tests/component/doubles/`, but a real `Position` is a stronger double than a hand-rolled one
    (3.3's precedent, 3.3:494-500) and the existing `doubles/test_position.py::TestPosition` is a
    legacy fake with none of the fields; disclose the deviation.
  - [x] 3.2 `TestEntryPriceIsVolumeWeighted` (AC #1): terminating and non-terminating cases;
    assert equality with the test's own `Decimal` VWAP, and inequality with first and last fill
    prices. `TestExitPriceIsVolumeWeighted` (AC #2): same shape on `exit_price`.
    `TestMixedRoundTrip` (AC #2): every field named in AC #2's operationalisation, plus
    `order_side == "BUY"` for a long and `"SELL"` for a short round trip (a short entered by two
    SELL fills and exited by two BUY fills — `entry` is `OrderSide.SELL`), and `trade_id`,
    `venue_order_id`, `client_order_id` mapped exactly as the backtest path maps them
    (`trade_id = str(position.id)`, `venue_order_id = str(opening_order_id)`, `client_order_id =
    str(closing_order_id)`, `backtest_persistence.py:513-516` — parity, however odd; see Hazards
    #7).
  - [x] 3.3 `TestCommissionIsSummedAcrossFills` (AC #3): the 5.75 case; empty → zero in
    settlement currency; mixed currency → settlement entry + one `trade.commission_mixed_currency`
    record whose `commissions` field is a tuple of strings; **read-time pin (iii)** using the Task
    1.2 fixture — the wrong answer must be *observably* different (assert the mutated read returns
    the new fill's commission, then assert the recorder's answer is the closed leg's).
  - [x] 3.4 `TestSingleFillEachSideIsTheSimpleCase` (AC #4), and the float-artifact pin: a price
    such as `100.1` whose binary repr is not exact must come out as `Decimal("100.10000000")`
    (M8 — `Decimal(float)` gives `100.099999999999994315658113919198513031005859375`).
- [x] Task 4: RED — the handler contract and the runner seam (AC: #5, #6)
  - [x] 4.1 In the same component file, `TestTheHandlerNeverRaises`: a `_StubCache` with
    `position(position_id)`; a recording `sink`; `log = structlog.get_logger("test").bind(session_id=…)`
    with `capture_logs()` (sanctioned against a locally-bound logger, 3.3:522-526; never near the
    runner's contextvars). Cases: `PositionOpened` → no record, no sink call; `PositionChanged` →
    same; `PositionClosed` → exactly one `trade.aggregated` (severity `info`) **and** one sink call
    carrying the `RecordedTrade`, record emitted **after** the sink returns (a sink failure must
    not leave a record claiming success — the observer's commit-after-emit lesson inverted,
    `live_order_path.py:656-659`); sink raises → one `trade.recorder_failed` with `stage="sink"`,
    `position_id`, `error_type`, `exc_info=True`, **no raise**; `cache.position()` returns `None`
    → one `trade.recorder_failed` with `stage="lookup"`, no raise; `avg_px_open` NaN → one
    `trade.recorder_failed` with `stage="aggregate"`, no raise. Assert with `pytest.raises` **not**
    used — call the handler bare inside the test body; a raise fails the test by itself.
  - [x] 4.2 The record apparatus, replicated from `test_live_order_path.py:1134-1203, 1321-1377`:
    `EMITTED_TRADE_EVENTS` pinned as an exact set against a literal **and** derived from the code
    (the recorder's `_dispatch` map drives every entry and the captured record names are compared —
    the two-directional pin CLAUDE.md requires, since the one-directional version was found false
    on 2026-08-30); `TestNoRecordEverCarriesAnAccountId` over every builder (NFR26 — every
    `Position*` event carries `.account_id`, so a naive field dump leaks it); every `Decimal` in a
    record is a `str` (`live_order_path.py:630-633`); `ts_event` present on `trade.aggregated`;
    severity pinned (`trade.aggregated` info, `trade.commission_mixed_currency` warning,
    `trade.recorder_failed` error). Diagnostic names (`trade.recorder_failed`,
    `trade.commission_mixed_currency`) are **outside** `EMITTED_TRADE_EVENTS`, the
    `order.observer_failed` precedent (`live_order_path.py:150-161`).
  - [x] 4.3 Runner seam pin, `tests/component/core/test_session_runner_order_path.py` — extend
    `TestTheRunnerReachesTheOrderPathCallSites` (`:293`), do not fork it: its docstring records
    why a wiring claim must be pinned through a **real `LiveSessionRunner`** over
    `TestLiveNode(run_seconds=0.01)` + `_runner(node)` + `runner.run()`, never by calling the
    helper directly (904 tests stayed green when 3.1's call site was deleted). Assert on the
    node double's `trader.subscriptions` (`tests/component/doubles/test_live_node.py:131, 180-181`,
    the `test_session_runner_phases.py:850` shape for `BAR_TOPIC`): one entry whose topic is
    `POSITION_EVENTS_TOPIC` and whose handler is a bound method of a `TradeRecorder` holding
    `node.cache` (`TestLiveNode.cache`, `:250`) and `runner._log`; and the ordering twin
    (`:870`'s shape): that subscription precedes `add_strategy` on one timeline. Non-vacuity: no
    `POSITION_EVENTS_TOPIC` entry exists in `subscriptions` when `_phase_subscribe` is skipped.
    `_TestCache` has no `position()` today and needs none here — the recorder never calls it
    until a `PositionClosed` arrives, which this double never publishes; if a later test needs
    it, extend the double in `doubles/` and disclose. If an existing test pins the *exact*
    subscription set or count, update that pin deliberately in the same edit and say so in the
    Dev Agent Record — it is a pin doing its job, not a regression.
- [x] Task 5: GREEN — `src/core/live_trade_recorder.py` (AC: all)
  - [x] 5.1 Module docstring in the `live_order_path.py:1-120` register: what it consumes
    (`events.position*`, `PositionClosed` only), what it reads off `Position` and why not the
    event (fact 2), the read-time rule (fact 3), the parity rule (fact 4), the containment rule
    (fact 5), and the 3.6 handover (the `sink` is where the persistence port goes; "no DB write
    here, by design"). **No `nautilus_trader` import** — duck-typed `Any` on every framework
    object; dispatch by `type(event).__name__` as the observer does (`live_order_path.py:451-461`,
    `:483-508`). Imports limited to `dataclasses`, `datetime`, `decimal`, `typing`,
    `collections.abc`, `src.models.trade` — all already in `_STDLIB_AND_FIRST_PARTY`
    (`test_epic1_ac_node.py:449-545`); adding any other stdlib root is a guard-list edit and must
    be disclosed (CLAUDE.md:68-76).
  - [x] 5.2 Public surface: `POSITION_EVENTS_TOPIC = "events.position*"`; `AGGREGATED_EVENT =
    "trade.aggregated"`, `RECORDER_FAILED_EVENT = "trade.recorder_failed"`,
    `COMMISSION_MIXED_EVENT = "trade.commission_mixed_currency"`; `EMITTED_TRADE_EVENTS =
    (AGGREGATED_EVENT,)`; `PRICE_QUANTUM = Decimal("0.00000001")`; the three pure helpers from
    Task 2; `@dataclass(frozen=True) class RecordedTrade` holding `trade: TradeBase`,
    `profit_loss: Decimal`, `profit_pct: Decimal | None`, `holding_period_seconds: int`,
    `position_id: str`, `strategy_id: str`, `fill_count: int`, `trade_key: str` (unique per
    round trip: `f"{position_id}:{closing_order_id}"` if Task 1.3 measures ID reuse, else
    `position_id` — record which and why; it is what 3.6 keys idempotent writes on);
    `aggregate_closed_position(position: Any) -> RecordedTrade` (pure: no log, no cache; raises
    `ValueError` on an unconvertible field — **no new exception class**, retro D3);
    `class TradeRecorder` with `__init__(cache: Any, log: Any, sink: Callable[[RecordedTrade],
    None] | None = None)` and `handle_position_event(event: Any) -> None`;
    `install_trade_recorder(node_cache, log, sink=None) -> TradeRecorder` if it earns its keep
    (a bare constructor call in the runner is fine — do not add a function for symmetry alone).
  - [x] 5.3 `handle_position_event`: whole body in `try`; `type(event).__name__ != "PositionClosed"`
    → return; `position = self._cache.position(event.position_id)`; `None` → `recorder_failed`
    `stage="lookup"` and return; `recorded = aggregate_closed_position(position)`; commission
    diagnostics emitted from the helper's third return value when it has more than one entry;
    `sink(recorded)` if a sink is set; **then** `log.info(AGGREGATED_EVENT, …)` with:
    `position_id`, `instrument_id`, `strategy_id`, `ts_event` (the closing fill's), `trade_key`,
    `entry_price`, `exit_price`, `quantity`, `fill_count`, `commission`, `currency`, `realized_pnl`,
    `holding_period_seconds`, `opening_order_id`, `closing_order_id`, `ts_opened`, `ts_closed`,
    `order_side` — every non-`str` value rendered with `str()`; **no `account_id`**, no `side`
    enum int. `except Exception` → `log.error(RECORDER_FAILED_EVENT, stage=…, position_id=…,
    event_type=…, error_type=…, exc_info=True)`; never re-raise (`KeyboardInterrupt` is a
    `BaseException` and passes through, as in the guard).
  - [x] 5.4 Size budget, decided BEFORE the edit (CLAUDE.md D4): new module budgeted at ≤ 80
    executable statements / one class ≤ 45 statements / every function ≤ 25 — **reserving
    headroom for 3.6's write path in the same module** (the epic mandates the same module; the
    observer reached 87 of 100 in one story). Measure with the 3.3 AST counter after the edit and
    record raw lines + statements + largest class + largest function.
  - [x] 5.5 Runner wiring (AC #6), `src/core/live_session_runner.py:561-563` neighbourhood: import
    `POSITION_EVENTS_TOPIC, TradeRecorder`; field `self._trade_recorder: TradeRecorder | None =
    None` beside `_order_observer` (`:225`); in `_phase_subscribe`: `self._trade_recorder =
    TradeRecorder(<node cache from Task 1.3>, self._log)` and `self._subscribe(POSITION_EVENTS_TOPIC,
    self._trade_recorder.handle_position_event)`. Budget ≤ 4 raw / ≤ 3 statements on a module
    that is a sanctioned over-cap exception (894 raw / 275 statements; `deferred-work.md:1632-1652`)
    — measure and record the delta; do **not** split it (Anti-Patterns: guard-list rot). Add a
    two-line comment in the `:553-559` register saying the recorder attaches after reconciliation,
    so a position already open at attach time is first seen mid-life (the `live_order_path.py:645-651`
    caveat applies identically).
- [x] Task 6: Guard lists — three hand edits, one deliberate non-edit, all in the same commit (AC: #5)
  - [x] 6.1 `NODE_FACING_MODULES` += `"src/core/live_trade_recorder.py"` with an inline reason
    ("Story 3.5 — runs inline inside `MessageBus.publish_c`", the `:59` shape) in
    `tests/unit/core/test_live_node_never_exits.py:41-66`.
  - [x] 6.2 `TestImportPurity.MODULES` += `"src.core.live_trade_recorder"` with the "added on
    creation, not on next discovery" comment (`tests/component/core/test_session_runner_phases.py:1228-1254`).
    This is the list that makes 3.6 take a **port**, not `src.services`/`src.db`.
  - [x] 6.3 `_STDLIB_AND_FIRST_PARTY` (`tests/integration/core/test_epic1_ac_node.py:449-545`):
    expected **zero** additions if Task 5.1's import list holds; run the integration test to prove
    it rather than asserting it. If an addition is needed, add it with the inline reason and say
    so — 3.3 claimed "zero guard-list edits" and was wrong (3.3:394).
  - [x] 6.4 `STOP_PATH_MODULES` and `NEW_OR_MODIFIED_FOR_STOP` (`tests/unit/core/test_live_stop_path_is_inert.py:38-65,
    433-464`): **not** added; write the reason in the same comment block the order path uses
    (`:48-58`) — not a stop-path module, calls no order method, and its record names carry none of
    the AR36 stems by construction (`trade.aggregated`, not `trade.closed`; the `close` stem is
    exactly why that name was not chosen, `:451-464`).
  - [x] 6.5 Automatic scans that pick the new module up with no edit, each run and its pass
    recorded: dependency invariance (`test_live_dependency_invariance.py:39`), the AR24 retry
    scan (`test_order_path_has_no_retry.py:56-68` — trivially satisfied, the recorder submits
    nothing), the client-order-id flag canary (`test_client_order_id_determinism.py:207-221` —
    string constants included, so the two flag names may not appear even in prose), the `.status =`
    scan (`test_session_service.py:569-580` — `RecordedTrade` has no `status` attribute).
- [x] Task 7: Non-change evidence contracts — verified, not assumed (AC: all)
  - [x] 7.1 `git diff --stat` empty for: `src/core/live_order_path.py`,
    `src/core/live_strategy_guard.py`, `src/core/live_node_builder.py`,
    `src/core/live_connection_monitor.py`, `src/core/strategies/**`, `src/models/**` (the domain
    object is `TradeBase` **as it stands**; `TradeCreate.backtest_run_id: int` is 3.6's to widen),
    `src/db/**`, `alembic/**`, `src/services/**`, `src/cli/**`, `tests/component/core/test_live_order_path.py`,
    `test_live_order_recovery.py`, `test_client_order_id_determinism.py`, and the guard-list
    *contents* not named in Task 6. Record each in the Dev Agent Record. The production diff this
    story expects is exactly: one new module, ≤ 4 lines in the runner.
  - [x] 7.2 Head stays `b7c419e2a3d8` — no migration (`trades.session_id` and `chk_trades_owner`
    already exist for 3.6, `src/db/models/trade.py:95-100, 146-155`).
- [ ] Task 8: Live observation on the next RTH transcript — read, do not stage (AC: #4, #6)
  - [x] 8.1 Preconditions are 3.2 Task 8.1's, unchanged: inside RTH; **no other IBKR login**
    (mobile app and client portal included — error 162); the bare non-compose Gateway
    (`READ_ONLY_API: "yes"` on the compose one); Redis up; strategy `sma_crossover` only
    (`fast_period=2, slow_period=3`, the proven fast-crossover tuning). **Use a fresh session**:
    `p7-position-test` is poisoned (stranded `ACCEPTED` order, `deferred-work.md:2309-2325`) and
    `p7-fill-0901` carries a stale `net_position=22` the broker does not hold (routed to 4.2,
    `deferred-work.md:2503-2519`). **Know before starting** that `flatten_position.py --confirm`
    is currently broken by a Nautilus-side regression and fails safe (`deferred-work.md:2475-2501`):
    a position left open at the end of the run stays open at the broker, by design (stop never
    flattens, AR40/NFR14), and there is no working tool to close it on purpose. Say so to the
    operator before the run, not after.
  - [ ] 8.2 ⏳ not yet run. Let the session complete at least one strategy-owned round trip
    (entry fill, exit fill — with `fast_period=2, slow_period=3` a reversal arrives within
    minutes inside RTH), then stop it cleanly. Pass criteria: (i) exactly one `trade.aggregated`
    record follows each `PositionClosed` Nautilus line in the transcript, bound with the
    `session_id`; (ii) for a leg whose `order.filled` records show one entry fill and one exit
    fill, `trade.aggregated`'s `entry_price`/`exit_price` equal those two fills' `last_px` (AC #4,
    live) and its `commission` equals the sum of the two fills' `commission` values (AC #3, live);
    if IBKR happens to fill an order in pieces (several `order.filled` records with the same
    `client_order_id`, `cum_qty` climbing), `entry_price` equals the VWAP of those fills computed
    by hand from the transcript (AC #1, live — opportunistic, not staged); (iii) `fill_count`
    equals the number of distinct `trade_id`s across the leg's `order.filled` records (excluding
    `duplicate=True`); (iv) zero `trade.recorder_failed` records; (v) `grep -c` for `162`, `10182`,
    `366` inspected **before anything else is recorded** (D6's standing rule; every hit inspected —
    timestamp digits and the benign teardown `query cancelled (code: 162)` are false positives,
    3.4:363-368).
  - [ ] 8.3 What this run is **not** evidence for, stated in the result row: a partial fill cannot
    be staged on demand — a 22-share market order on NVDA inside RTH fills whole in milliseconds —
    so AC #1/#2's multi-fill arithmetic is broker-double evidence (NFR32) by design, and the live
    run proves the wiring (AC #6), the degenerate case (AC #4) and the commission sum for the
    single-fill leg (AC #3). Also not evidence: that the recorded trade matches the broker's own
    view — `PositionClosed` reported `side=FLAT` on 2026-09-11 while the broker was short 22
    (`deferred-work.md:2439-2445`); broker truth is Story 4.3's.
  - [x] 8.4 Record as **Procedure P11** in `docs/qa/phase3-live-verification.md` (P10 ends at
    `:1254`), following the house shape exactly: intro (`**Introduced by**` / `**Verifies**`) →
    `### What it does — and does not — do` → `### Preconditions` → `### Command` → `### Expected
    output` → `### Pass criteria` → `### Result log` (`| Date | Operator | Result | Notes |`,
    newest first, `Allay (Claude Code session)`). If the market is closed when the automated tiers
    land, the story goes to `review` with 8.2 marked `⏳ not yet run` (the standing Epic 2 retro
    rule; 3.2 Task 8.4 / 3.3 Task 6.3 / 3.4 Task 8.4 precedent) — **not** `done`.
- [x] Task 9: Mutation sweep, clause matrix, gates, closeout (AC: all)
  - [x] 9.1 Mutations selected **from the AC list, not from what feels fragile**, minimum one per
    AC, scripted break→observe-red→revert naming the killing test each time: (M1/AC1) read
    `position.last_event.last_px` (or the first fill's price) instead of `avg_px_open` →
    `TestEntryPriceIsVolumeWeighted` red **and** `TestSingleFillEachSideIsTheSimpleCase` still
    green (the pair is the proof that AC #4 alone is vacuous); (M2/AC2) same on `avg_px_close` →
    `TestExitPriceIsVolumeWeighted` red; (M3/AC3) `commission = position.last_event.commission`
    instead of `commissions()` → `TestCommissionIsSummedAcrossFills` red; (M4/AC3) take
    `commissions()[0]` unconditionally → the mixed-currency pin red; (M5/AC1+4) divide the weighted
    sum by `fill_count` instead of quantity → multi-fill red, single-fill green; (M6/AC5) remove
    the `try` in `handle_position_event` → `TestTheHandlerNeverRaises` red on the sink-raises
    case; (M7/AC2) read `quantity` instead of `peak_qty` → `TestMixedRoundTrip` red (`quantity ==
    0` fails `TradeBase.quantity > 0` validation — that is the message to expect); (M8/AC1)
    `Decimal(value)` instead of `Decimal(str(value))` → the `100.1` float-artifact pin red; (M9/AC6)
    typo the topic to `"events.positions*"` → the runner seam pin red (Task 1.4's measurement is
    what makes this mutation meaningful); (M10/AC5) emit `trade.aggregated` **before** calling
    the sink → the sink-raises case red (a record must not claim success for a sink that failed);
    (M11/AC5) add a `log.info("position.opened", …)` on `PositionOpened` → the two-directional
    `EMITTED_TRADE_EVENTS` pin red (an unsanctioned namespace cannot land silently). When a
    mutation stays green, inspect the FIXTURE before concluding the guard is missing (2.8's
    mutation #7; 3.3's `trade_id` fixture lesson, 3.3:860-867).
  - [x] 9.2 Clause matrix (3.1's method): decompose each AC into its distinct obligations (AC #3
    alone has four: sum, currency source, empty, mixed/read-time), mutate each in isolation, run the
    plausible guard surface (`test_live_trade_recorder.py`, `test_live_trade_recorder_arithmetic.py`,
    `test_session_runner_order_path.py`, `test_live_node_never_exits.py`,
    `test_session_runner_phases.py::TestImportPurity`, `test_epic1_ac_node.py`), record which named
    test fires. Write it **as the tests are written**, not at closeout (3.3:391).
  - [x] 9.3 Full gates: `make format && make lint && make typecheck`; unit, component, integration
    `--forked`, e2e; Epic 1 acceptance sweep reported in **both units** (acceptance criteria and
    test functions — 3.3:887-889). Baselines at creation: unit 2441 · component 1457/16 skipped ·
    integration 284/2 skipped · e2e 1. Record every count and delta, and the size figures from
    Tasks 5.4/5.5 in a "Size, disclosed per CLAUDE.md D4" paragraph (3.3:891-898 shape).
  - [x] 9.4 `deferred-work.md`, new section "Deferred from: story-3.5", each item with a **named
    owning story**: the backtest path's `commissions[0]` (owner: Story 5.3, the seal path that
    reuses `BacktestPersistenceService`); the side-unaware `profit_pct` formula reproduced for
    parity (owner: Story 5.6, the comparison views — both sides must change together); NETTING
    `PositionId` reuse and the `trade_key` 3.6 must key idempotent writes on (owner: Story 3.6);
    `PositionClosed` for `EXTERNAL`/`INTERNAL-DIFF` positions arriving on the wildcard topic — the
    recorder aggregates them and stamps `strategy_id`; whether 3.6 persists them is a policy
    decision (owner: Story 3.6, with 4.2 consulted); the two placement deviations
    (`architecture.md:537-538, 546, 576` and `epics.md:1371` — docs amendment, owner: this story,
    done in the same commit as a dated note in each file). Then `sprint-status.yaml`. Commit shape
    per Project Structure Notes.

### Review Findings

Code review 2026-09-12 (commit `98119e4`). The three adversarial subagent layers could not run this
session (four launches stalled at the first read, three more were cut off by a session rate limit);
all three layers were executed in the reviewer's own context instead, so the Blind Hunter's
isolation from the spec was **not** preserved. Every finding below was verified by probe or by
reading the installed 1.220.0 wheel, not taken from a layer's prose.

- [x] [Review][Decision] A position flip drops the closed leg's trade — the cache is read after
  Nautilus has already replaced the `Position` under the same NETTING id. `execution/engine.pyx:
  1516-1600` (`_flip_position` closes the original, then calls `_open_position(instrument, None,
  fill_split2)`, which builds a **new** `Position` and `cache.add_position` overwrites the id) and
  `:1171-1186` (position events queue in `_pending_position_events` and publish only after the whole
  fill, flip included). Measured 2026-09-12 against a real `ExecutionEngine` (LONG 50, then SELL
  150): at `PositionClosed` dispatch, `cache.position(event.position_id)` returned the NEW position —
  `is_closed False`, `avg_px_close 0.0` (event: `130.0`), `ts_closed 0`, `closing_order_id None`,
  `commissions() == [2.00 USD]` (closed leg: 1.50), `realized_pnl -2.00 USD` (event: `498.50 USD`).
  `aggregate_closed_position` then fails `TradeBase.exit_price > 0`, one `trade.recorder_failed`
  (`stage="aggregate"`) is logged, and the round trip is never recorded; P11 pass criterion 4 (zero
  `recorder_failed`) fails on any flip. Not reachable by `sma_crossover` since `438b0e4` (a reversal
  only closes), reachable by any strategy that reverses with one oversized order. The module
  docstring's fact 3 ("reads inside one synchronous call") is necessary but not sufficient, and no
  test covers a `PositionClosed` whose cached position is not closed. Options: (1) read every field
  `PositionClosed` snapshots (`avg_px_open/close`, `peak_qty`, `ts_opened/closed`, `duration_ns`,
  `opening/closing_order_id`, `entry`, `realized_pnl`, `strategy_id`) from the **event**, and take
  only `commissions()`/`event_count` from the cache, guarded by `position.is_closed and
  position.closing_order_id == event.closing_order_id`, else emit a distinct warning and record the
  commission as unknown — the leg is recorded with correct prices and PnL in every case; (2) keep
  the `Position` read, detect the stale object with the same guard, and emit a distinct diagnostic
  instead of `recorder_failed` — the trade stays unrecorded but the transcript says why; (3) accept
  as a known limitation, pin it with a test, route to Story 3.6/4.2. In all cases add the
  engine-level flip fixture as a component test (recipe: the review's `probe_flip.py`, kept in the
  session scratchpad; it is ~60 lines against `TestComponentStubs` + `ExecutionEngine`).
- [x] [Review][Patch] `test_empty_commissions_yield_zero_in_settlement_currency` never reaches the
  empty branch — zero-commission fills yield `[Money(0.00, USD)]`, not `[]` (measured), and a real
  `Position` cannot produce an empty list, so AC #3 pin (i) is exercised only by the unit test's
  hand-rolled `[]`. Rename/re-docstring to what it actually pins (a zero-amount commission via the
  single-entry path) [tests/component/core/test_live_trade_recorder.py:287]
- [x] [Review][Patch] `test_mixed_currency_with_settlement_absent_picks_the_first_and_warns` never
  asserts the warning its name promises — dropping the `COMMISSION_MIXED_EVENT` emission on the
  settlement-absent path stays green at component tier
  [tests/component/core/test_live_trade_recorder.py:329]
- [x] [Review][Patch] `TestHoldingPeriodIsIntegerDivision` tests Python's `//`, not the module —
  both assertions are integer literals and cannot fail against any code change; 2 of the 15 counted
  unit tests are vacuous. Delete, or drive `aggregate_closed_position` with a duck-typed position
  double [tests/unit/core/test_live_trade_recorder_arithmetic.py:142-153]
- [x] [Review][Patch] Unused `monkeypatch` parameter
  [tests/component/core/test_session_runner_order_path.py:423]
- [x] [Review][Patch] Runner budget overage disclosed by number but not named — Tasks 5.5/7.1
  budgeted ≤ 4 raw lines / ≤ 3 statements; delivered 8 raw / 4 statements (import, field,
  constructor, subscribe). The Dev Agent Record's "2 new statements inside `_phase_subscribe`"
  counts only the phase body. One sentence naming the overage, per CLAUDE.md D4
  [this file, Dev Agent Record, Task 9.3 paragraph]
- [x] [Review][Defer] A raising sink loses the computed trade from the transcript —
  `trade.aggregated` is emitted only after the sink returns (Task 4.1 / M10, deliberate) and
  `trade.recorder_failed` carries no prices, so under Story 3.6 a database outage leaves no record of
  what was aggregated. `sink` is `None` in production this story
  [src/core/live_trade_recorder.py:289-313] — deferred, owner Story 3.6 (carry the `RecordedTrade`
  fields on the `stage="sink"` failure record, or decide the ordering there)

**Resolutions (2026-09-12, batch-applied, TDD red→green):**

- Decision → **option 1, applied.** `aggregate_closed_position(event, position)` now reads every
  snapshot field from `PositionClosed` and takes only `commissions()`/`event_count` from the cached
  `Position`, and only when the new `position_vouches_for(event, position)` guard holds (closed,
  and closed by the event's own `closing_order_id`). Otherwise the leg is still recorded, with
  `commission_amount`/`commission_currency`/`fill_count` = `None` (unknown, never zero — the
  `trades` columns are nullable) and one new `trade.commission_unavailable` warning naming the
  reason (`"no cached position"` or `"cached position is not the closed leg (re-opened under the
  same id)"`). RED first: `TestAgainstARealExecutionEngine` drives the recorder over a real
  `MessageBus` from a real `ExecutionEngine` (the only fixture that reproduces Nautilus's deferred
  publication and the flip path); its flip case failed on the old code with exactly the predicted
  `ValidationError` → `trade.recorder_failed`, and its plain-round-trip case passed on both, proving
  the guard passes in the normal case. **Two AC #5 pins changed deliberately:** a `cache.position()`
  `None` no longer produces `trade.recorder_failed` — it records the leg with commission unknown
  (`test_a_missing_cached_position_records_the_leg_with_commission_unknown`); and the
  unconvertible-`avg_px_open` case now puts the NaN on the *event* (a proxy class named
  `PositionClosed`, since the dispatch keys on the class name), because the position's price is no
  longer read. A third handler test pins the stale-object guard at the double level. The module
  docstring's facts 2/3 are rewritten to say what was measured. The read-time rule still stands
  (a next-bar re-open resets the closed position in place) — it is necessary, no longer claimed
  sufficient.
- Patches 2–5 applied as written (renamed + re-docstringed the zero-commission test; the
  settlement-absent test now asserts its warning; the two integer-literal tests are replaced by
  `TestAggregateClosedPositionWithDoubles`, six unit tests driving `aggregate_closed_position` and
  `position_vouches_for` with no Nautilus import; the unused fixture parameter removed).
- Patch 6 is the sentence in the Dev Agent Record below.

## Dev Notes

### The trap — read before designing anything

**Do not build a fill tally.** The obvious design — subscribe `OrderFilled`, keep a per-order
`list[(qty, px, commission)]`, compute the VWAP yourself on `PositionClosed` — is wrong three
separate ways, each already measured by a previous story: (a) it resets across a process run, so
a position opened yesterday and closed today records today's fills only — precisely the bug FR25
exists to prevent, and precisely what Story 4.5's "the eventual round trip reads as one trade"
(NFR14, `epics.md:1608-1639`) forbids (`live_order_path.py:410-413`, 3.3:502-505); (b) Nautilus
publishes `OrderFilled` events it has itself refused to apply (duplicate `trade_id`, commit
`1482198`, `execution/engine.pyx:1357-1369` vs `:1174-1177`), so a tally counts fills the
position does not contain; (c) it duplicates arithmetic the wheel already does in `Position`
and the backtest already trusts, so paper and backtest trades would be computed by two different
formulas — the opposite of the PRD's parity obligation (`prd.md:87-92`). **Read the `Position`.**
A restarted process rejoins its own Redis cache (AR10; `architecture.md:243-246`) and Nautilus
rebuilds the `Position` by re-applying its serialised fills, so `avg_px_open` and `commissions()`
are correct across process runs **by construction** — 3.5 gets the cross-run property for free,
and states it as a non-goal-by-construction rather than as handled logic.

**Do not read the `Position` late.** Fact 3: the object resets in place on the next opening fill.
Lookup and conversion happen inside the same synchronous `handle_position_event` call, and AC #3
pin (iii) is the test that would go red if a future "batch the writes" refactor moved the read.

**Do not raise.** A raise from a msgbus handler re-enters `MessageBus.publish_c`
(`common/component.pyx:2754-2757`, no per-handler `try`) and ends at `os._exit(1)` with zero
output (3.3:478-480). The observer's `handle_order_event` is the template (`live_order_path.py:483-508`).

### The current surface — extension points, exact

- `src/core/live_session_runner.py:542-564` `_phase_subscribe` — the seam. `:561-563` constructs
  the `OrderEventObserver` and subscribes `BAR_TOPIC` and `ORDER_EVENTS_TOPIC` through
  `self._subscribe` (`:566-577`), which records into `self._subscriptions` for `_unsubscribe`
  (`:421-424`). Going straight to `self._node.trader.subscribe(...)` is the defect the `:569-573`
  comment records. The node is started at `:507` and reconciliation has run before this phase.
- `src/core/live_order_path.py:346-508` `OrderEventObserver` — the template: `__init__(log, …)`
  (`:441-461`), a closed-set `_dispatch` keyed by `type(event).__name__` (`:451-461`),
  `handle_order_event` with the whole body in `try` and `order.observer_failed` on `except`
  (`:483-508`); `_log_filled` (`:628-695`) is the field vocabulary to stay consistent with
  (`fill_qty`, `cum_qty`, `last_px`, `commission`, `currency`, `trade_id`, `position_id`, all
  `str()`).
- `src/core/live_strategy_guard.py:104-114` — why `handle_event` is wrapped; leave it.
- `src/models/trade.py:15-43` `TradeBase` — the domain object, unchanged: `instrument_id`,
  `trade_id`, `venue_order_id`, `client_order_id`, `order_side` (`^(BUY|SELL)$`), `quantity`
  (`gt=0`, 8 dp), `entry_price` (`gt=0`), `exit_price`, `commission_amount`,
  `commission_currency` (≤ 10), `fees_amount` (default `0.00`), `entry_timestamp`,
  `exit_timestamp`. `TradeCreate.backtest_run_id: int` (`:49`) is required and has no
  `session_id` — it cannot express a session trade and is **3.6's** to widen; 3.5 returns
  `TradeBase` inside `RecordedTrade` and does not touch the file.
- `src/services/backtest_persistence.py:437-526` — the parity reference for every conversion
  (quantize at `:489-493`, `peak_qty` at `:494`, NaN filter at `:495-502`, `profit_pct` at
  `:504-507`, ID mapping at `:513-517`, `fees_amount = 0.00` at `:523`).
- `src/db/models/trade.py:95-100, 146-155` — `session_id`, `chk_trades_owner`, `CHECK quantity >
  0`: already there; nothing for 3.5 to migrate.

### Measured Nautilus 1.220.0 facts — cite, don't re-derive

- Position event constructors and attributes: `model/events/position.pxd:38-86`; classes at
  `position.pyx:38, 255, 498, 764`; `create(position, fill, event_id, ts_init)` at `:441-446`.
  **All three event classes expose the same attribute set** (`avg_px_close`, `ts_closed`,
  `duration_ns`, `closing_order_id`, `unrealized_pnl` all exist on `PositionOpened` and read
  zero/`None`) — branch on the class name, never on attribute presence. `entry`/`side` read as
  enum ints from Python (`entry=1` BUY, `side=2` LONG, `side=1` FLAT); render with `.name`.
- `Position`: `avg_px_open`/`avg_px_close`/`realized_return` are `double`; `realized_pnl` is
  `Money` net of commission; `commissions()` is `list(self._commissions.values())`
  (`position.pyx:667-676`); `peak_qty`, `event_count`, `events`, `trade_ids`, `opening_order_id`,
  `closing_order_id`, `ts_opened`, `ts_closed`, `duration_ns`, `id`, `instrument_id`,
  `strategy_id`, `settlement_currency`/`quote_currency`. Reset-in-place at `:489-501`. Duplicate
  guard `_check_duplicate_trade_id` (`:678-690`) raises `KeyError` on a composite match.
- Measured on 2026-09-11 (probe output in the creation record): 40@100 + 60@110 → `avg_px_open
  106.0`; 50@120 + 50@130 → `avg_px_close 125.0`, `realized_pnl 1900.00 USD` (zero commission);
  with commissions 1.25/2.50/1.00/1.00 → `commissions() == [Money(5.75, USD)]`, `realized_pnl
  1894.25 USD`; `PositionClosed.quantity == 0`, `peak_qty == 100`, `duration_ns == 3_000_000_000`;
  `Money.as_decimal()` → `Decimal`, `money.currency.code` → `"USD"`; `[a for a in
  dir(PositionClosed) if "comm" in a.lower()] == []`.
- `TestEventStubs.position_opened/changed/closed(position)` each take one argument and call
  `create(position, position.last_event, UUID4(), ts_init=0)` (`test_kit/stubs/events.py:413-431`).
  `TestEventStubs.order_filled` derives `trade_id` from the `client_order_id` (two fills on one
  order collide — pass distinct `TradeId`s) and computes commission on `order.quantity` against a
  zero-fee stub cash account — build `OrderFilled` directly when commission matters
  (3.3:538-540, 3.3:860-867).
- `execution/engine.pyx:1171-1184`: pending position events popped before the order publish;
  position events published on `f"events.position.{strategy_id}"` after it. Wildcard subscription
  precedent: `portfolio/portfolio.pyx:187` (`events.order.*`), our `live_order_path.py:175`.
- The only real `Position` construction in the test tree today: `test_live_order_path.py:309`
  (`Position(instrument=instrument, fill=fill)`).

### Scope boundaries — what this story must NOT build

- **No DB write, no repository, no port, no `session_id` plumbing, no `trade.persisted`, no
  migration, no fencing column** — Story 3.6 (`epics.md:1378-1439`; D1 ruled there,
  `deferred-work.md:2090`). The `sink` argument is the handover point and stays `None` in
  production this story.
- **No `order.*` record changes**, no new order-lifecycle names — 3.3 shipped them. `order.submitted`
  still lacks `strategy_id` (`deferred-work.md:2231-2241`) — declined here as 3.4 declined it.
- **No `position.*` records.** `position.*` is not an AR41-sanctioned namespace; a
  `position.opened` record would need an amendment this story has no mandate to make (the
  `live_order_path.py:572-576` reasoning). M11 pins this.
- **No rejection handling** — 3.7 (`architecture.md:548` lists "rejection flow" under this test
  file; the epic's ACs do not, and 3.7 owns FR31/NFR13/NFR24; a rejection is not a trade).
- **No retry, no query-on-ambiguity** — AR24, 3.4's, and the AR24 scan now covers this module.
- **No cross-process-run fill reassembly, no broker-truth check** — 4.2/4.3/4.5's; the recorder
  records what Nautilus's `Position` says.
- **No strategy-file edits, no `src/models/` edits, no `src/services/` edits** (Task 7).
- **No metrics, no equity curve, no second results vocabulary** (AR43): `RecordedTrade` is a
  transport carrying the existing `TradeBase` plus the three derived columns the `trades` table
  already has; it introduces no new column, table or model of results.
- **No accumulator state of any kind** — the recorder holds `cache`, `log`, `sink` and nothing
  else; there is nothing to prune and nothing to persist.

### Hazards (every one bit a previous story, or will)

1. **`Decimal` in a record renders as `"Decimal('5.75')"` in the JSON transcript** and a component
   test cannot see it (`live_order_path.py:630-633`, 3.3:217-220). Every record value is `str()`.
2. **`float("nan")` is truthy and `Decimal("NaN").quantize()` raises `InvalidOperation`**
   (`backtest_persistence.py:472-474, 495-496`). `to_price_decimal` rejects NaN explicitly; the
   handler turns it into one `trade.recorder_failed`.
3. **`Decimal(float)` is not `Decimal(str(float))`** — `100.1` becomes a 51-digit number. M8.
4. **Every `Position*` event carries `account_id`** — NFR26's key-name scan
   (`test_live_order_path.py:1174-1203`) must run over every record builder here too.
5. **The same `PositionId` hosts successive round trips under NETTING** (fact 3). `trade_id =
   str(position_id)` mirrors the backtest mapping and is *not* unique per leg; `trade_key` is,
   and 3.6 must key on it. Say so in the module docstring.
6. **`profit_pct` on the backtest side is `(exit - entry) / entry * 100` regardless of side** — a
   profitable short reads negative. Reproduced for parity; routed to Story 5.6 (Task 9.4). Do not
   "fix" one side alone: the comparison views would then compare unlike quantities.
7. **The backtest ID mapping is odd** — `venue_order_id` holds the *opening client order id* and
   `client_order_id` holds the *closing* one (`backtest_persistence.py:513-516`). Parity means
   reproducing it; `trade.aggregated` additionally carries `opening_order_id`/`closing_order_id`
   under honest names so the transcript is readable.
8. **`PositionClosed` can disagree with the broker** (`side=FLAT` while short 22, 2026-09-11).
   The recorder is not the place to detect that; 4.3 is.
9. **`EXTERNAL`/`INTERNAL-DIFF` positions publish on the same wildcard topic** (reconciliation
   creates them, `live/execution_engine.py:1709-1721`; a `LONG 4 AAPL.NASDAQ` `EXTERNAL` residual
   is open at the broker today, `deferred-work.md:2521-2524`). The recorder aggregates whatever
   closes and stamps `strategy_id`; persistence policy is 3.6's (Task 9.4).
10. **Two `TestExecStubs.market_order` calls share one frozen `client_order_id`** (3.3:186-189);
    pass explicit IDs for the opening and closing orders or `opening_order_id == closing_order_id`
    and the ID-mapping assertions in Task 3.2 are vacuous.
11. **The C-logging autouse guard is order-dependent** — never build a `TradingNode` or
    `BacktestEngine` in the component file (3.3:527-529, 3.4:593-595).
12. **ruff F401 is unfixable-but-blocking** — add each import and its use in one edit
    (CLAUDE.md "Editing with Auto-Linter").
13. **`capture_logs()` never carries a rendered traceback** (`test_epic1_ac_node.py:516-519`);
    assert `exc_info is True` on `trade.recorder_failed`, not on text.
14. **Contextvars are empty on executor threads** (`live_order_path.py:417-420`) — the recorder
    logs only through the `log` it is handed, never `structlog.get_logger()` at call time.

### Testing standards summary

- Tiers: **unit** for the three pure helpers (`tests/unit/core/test_live_trade_recorder_arithmetic.py`,
  no Nautilus import — so the module must import cleanly without `nautilus_trader`); **component**
  for the aggregation against real `Position` doubles, the handler contract, the record apparatus
  (`tests/component/core/test_live_trade_recorder.py`) and the runner seam
  (`tests/component/core/test_session_runner_order_path.py`, extended); the globbed
  **integration** scans run unchanged. No real broker anywhere (NFR32); live tier is observational
  only (Task 8).
- Markers on every test; TDD Red first with **RED evidence captured as each test is written**;
  a pin that is GREEN on first run is recorded as "GREEN on first run: pins measured behaviour"
  and its non-vacuity is carried by the Task 9 mutation, never by a contrived RED (3.4:609-611).
- Every numeric assertion is an exact `Decimal` equality after quantizing to `PRICE_QUANTUM`;
  the expected value is computed in the test from the fill tuples, never copied from the
  recorder's output.
- Anti-tautology twins are mandatory: the single-fill case beside the multi-fill case (M1/M5),
  the read-time pin's "wrong answer is observably different" assertion, the two-directional
  `EMITTED_TRADE_EVENTS` pin, and the runner seam's "absent before the phase" assertion.
- Existing files that must need **zero changes**: `test_live_order_path.py`,
  `test_live_order_recovery.py`, `test_client_order_id_determinism.py`,
  `test_session_runner_phases.py` (except the `TestImportPurity.MODULES` entry). If any goes red,
  a non-change contract is being violated.
- Baselines at story creation (measured 2026-09-11): unit 2441 · component 1457/16 skipped ·
  integration 284/2 skipped · e2e 1. Head `438b0e4`, tree clean, submodule at `06c00cb`.

### Project Structure Notes

- New: `src/core/live_trade_recorder.py`; `tests/unit/core/test_live_trade_recorder_arithmetic.py`;
  `tests/component/core/test_live_trade_recorder.py`; Procedure P11 in
  `docs/qa/phase3-live-verification.md` (Task 8.4).
- Modified: `src/core/live_session_runner.py` (≤ 4 lines, Task 5.5);
  `tests/component/core/test_session_runner_order_path.py` (seam pin);
  `tests/unit/core/test_live_node_never_exits.py` (`NODE_FACING_MODULES` + 1);
  `tests/component/core/test_session_runner_phases.py` (`TestImportPurity.MODULES` + 1);
  `tests/unit/core/test_live_stop_path_is_inert.py` (comment only — the deliberate non-add);
  `_bmad-output/planning-artifacts/architecture.md` and `epics.md` (dated placement notes);
  `_bmad-output/implementation-artifacts/deferred-work.md` (new section); `sprint-status.yaml`;
  this story file.
- NOT modified (evidence contracts, Task 7): `src/core/live_order_path.py`,
  `src/core/live_strategy_guard.py`, `src/core/live_node_builder.py`,
  `src/core/live_connection_monitor.py`, `src/core/strategies/**`, `src/models/**`, `src/db/**`,
  `alembic/**`, `src/services/**`, `src/cli/**`, `_STDLIB_AND_FIRST_PARTY` (expected; proven by
  running the scan), `CLAUDE.md` (the new membership-pinned tuple lives in `src/` and is
  documented in the module docstring; add a CLAUDE.md line only if the review asks).
- Naming: record names `trade.aggregated` (info), `trade.commission_mixed_currency` (warning),
  `trade.recorder_failed` (error) — all `trade.*`, dotted, past tense (AR41-conformant by
  construction); no `close`/`kill`/`halt`/`pause`/`finalize` stem in any operator-facing string
  (AR36). Constants in the `live_order_path.py:134-141` register (`*_EVENT`). Class
  `TradeRecorder`, dataclass `RecordedTrade`, function `aggregate_closed_position` — verb-prefixed
  helpers `to_price_decimal`, `select_commission`, `unix_nanos_to_utc`.
- Commit shape: one `feat(live):` commit carrying src + all test tiers + BMAD artifacts + docs/qa
  together (the 3.1–3.4 precedent); subject is an operator-outcome sentence (e.g. `feat(live):
  record a closed position at its volume-weighted prices and full commission`); no AI references.

### References

- Story source: `_bmad-output/planning-artifacts/epics.md:1343-1376` (ACs `:1350-1372`, the
  split note `:1374-1376`); epic context `:1105-1114`; the 2026-08-28 pre-drafting block
  `:407-439` (item 3 — the wrapper — `:426-431`); Story 3.6 `:1378-1439`; Story 4.5 `:1608-1639`
- Requirements (`epics.md`): FR25 `:78`; FR29 `:82`; FR28 `:81`; NFR14 `:129`; NFR32 `:162`;
  NFR35 `:168`; AR8 `:187`; AR10 `:189`; AR26 `:214`; AR36 `:236`; AR38 `:238`; AR41 `:241`;
  AR43 `:245`. PRD: FR25 `prd.md:839-840`; FR29 `:846`; parity obligation `:87-92`; session vs
  process run `:283-293`
- Architecture: reuse of the `trades` schema `architecture.md:53-56, 106-107`; Decimal money
  `:108-109`; framework handlers, position-closed path `:444-448`; the delta tree (services
  placement) `:530-540`, test doubles `:546-549`; import direction `:569-582`; data flow
  `:604-619`; testing split `:413-416, 470-472`
- Retro: `epic-2-retro-2026-08-28.md` — D1 `:458, 465-470`; D3 no new exception names `:460,
  661-663`; D4 `:461, 488-494`; D6 `:463, 503-507`; AI 12 `:603-606`; process AIs `:561-579`
- Previous stories: `3-4-never-resubmit-an-order-that-is-already-working.md` (partial-fill fence
  `:576-580`; D1 cross-ref `:95, 550`; GREEN-on-first-run rule `:609-611`; mutation method
  `:384-404`); `3-3-track-every-order-through-its-full-lifecycle.md` (position-event fence
  `:487-494`; stub gotchas `:186-189, 538-540, 860-867`; Decimal rendering `:217-220`; publish
  ordering `:478-480`; `capture_logs` rule `:522-526`); `3-2-submit-a-strategys-orders-to-the-broker.md`
  (Position construction recipe `:486-489`; live preconditions Task 8.1 `:151`; fence `:360-361`)
- Deferred work: `deferred-work.md:2083-2133` (D1 table, owner-per-item rule); `:2231-2241`
  (`order.submitted` `strategy_id`); `:2309-2325` (poisoned namespace); `:2353-2367` (IB
  not-found conflation → 4.3); `:2423-2524` (the 2026-09-11 addendum: double-submit, broken
  `--confirm`, stale NVDA cache, residual AAPL); `:1632-1652` (runner over-cap sanction)
- Current code: `src/core/live_session_runner.py:92, 225, 421-424, 542-577`;
  `src/core/live_order_path.py:1-120, 134-175, 346-508, 572-576, 628-695`;
  `src/core/live_strategy_guard.py:104-114`; `src/core/live_session_record.py:68`;
  `src/models/trade.py:15-64`; `src/services/backtest_persistence.py:375-591`;
  `src/db/models/trade.py:79-155`; `src/core/live_exec_avg_px.py` (the `f375ea6` fix — the
  reason the first live fill ever produced a position event at all)
- Wheel (all under `.venv/lib/python3.11/site-packages/nautilus_trader/`):
  `model/position.pxd:35-138`; `model/position.pyx:489-501, 667-690, 757-774`;
  `model/events/position.pxd:38-86`; `model/events/position.pyx:38, 255, 441-446, 498, 764`;
  `model/events/order.pyx:4540-4560` (`OrderFilled` ctor); `execution/engine.pyx:1171-1184,
  1357-1369, 1454-1497`; `trading/strategy.pyx:314-315`; `common/component.pyx:2754-2757`;
  `test_kit/stubs/events.py:413-431` and `order_filled`; `live/execution_engine.py:1709-1721`
- Guard lists: `tests/unit/core/test_live_node_never_exits.py:34, 41-66, 86-91`;
  `tests/unit/core/test_live_stop_path_is_inert.py:38-65, 71-80, 109-135, 433-478`;
  `tests/component/core/test_session_runner_phases.py:1214-1284`;
  `tests/component/core/test_live_dependency_invariance.py:39, 131-172`;
  `tests/unit/core/test_order_path_has_no_retry.py:27-68, 110-120`;
  `tests/integration/core/test_epic1_ac_node.py:65-67, 151-163, 403-445, 449-545`;
  `tests/component/core/test_client_order_id_determinism.py:184-221`;
  `tests/unit/services/test_session_service.py:569-580, 634-651`
- Test harnesses to reuse: `tests/component/core/test_live_order_path.py:58-63, 91-107, 309,
  388-433, 1092-1129, 1134-1203, 1321-1407`; `tests/component/core/test_session_runner_order_path.py`
- Live: `docs/qa/phase3-live-verification.md` (P10 `:1174-1254`; P7's 2026-09-01 row `:758` — the
  only live `PositionOpened` this project has seen); 3.2 Task 8.1 preconditions

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- Task 0.1 (2026-09-11 20:13 EDT): `nc -z 127.0.0.1 4002/4001/7497` — all closed, no Gateway
  reachable. Also outside RTH (Friday 20:13 EDT, after the 16:00 ET close). Task 8's live run
  cannot happen this session; story will land at `review` with 8.2 `⏳ not yet run`, per the
  3.2/3.3/3.4 precedent.
- Task 0.2 (2026-09-11): `git status --short` → only `sprint-status.yaml` (modified) and the new
  story file (untracked) — tree otherwise clean. `git rev-parse HEAD` → `438b0e4` (matches
  creation). Submodule at `06c00cb` (matches). `make format` → 499 files unchanged. `make lint` →
  all checks passed. `make typecheck` → success, 105 source files (pre-existing untyped-def notes
  on `backtest_orchestrator.py`, unrelated). `make test-unit` → 2441 passed. `make test-component`
  → 1457 passed, 16 skipped. `make test-integration` → 284 passed, 2 skipped (Epic 1 acceptance
  sweep: 40/40 criteria). `make test-e2e` → 1 passed. All baselines match the creation figures
  exactly.
- Task 1.1/1.2/1.4 (2026-09-11): fresh-interpreter probe
  (`scratchpad/probe_3_5_task1.py`, `PYTHONPATH=. uv run python`, not committed) confirmed every
  fact the story cites: `avg_px_open == 106.0` (float), `avg_px_close == 125.0`,
  `commissions() == [Money(5.75, USD)]` after the full round trip and `[Money(3.75, USD)]` after
  entries only, `realized_pnl == 1894.25 USD`, `PositionClosed.quantity == 0`, `peak_qty == 100`,
  `duration_ns == 3_000_000_000`, `closed.entry`/`closed.side` are enum ints rendered via `.name`,
  `position.id == "AAPL.NASDAQ-SMACrossover-000"` (NETTING `{instrument_id}-{strategy_id}`),
  `Money.as_decimal()` → `Decimal`, `money.currency.code == "USD"`. Reset-in-place (1.2) confirmed:
  a fresh BUY fill on the closed position reset `commissions()` to `[Money(0.50, USD)]`,
  `event_count` to `1`, `opening_order_id` to the new order. Wildcard subscription (1.4) confirmed
  a `MessageBus` handler on `"events.position*"` receives a `PositionClosed` published on
  `f"events.position.{strategy_id}"`, and a raising handler's exception **propagates out of
  `publish()`** — confirming Fact 5's containment requirement is load-bearing, not theoretical.
  `is_logging_initialized()` unchanged before/after (`False` both times).
- **Correction to a story-cited fact, found by the same probe**: `backtest_persistence.py:495-496`'s
  comment — *"`Decimal("NaN").quantize()` raises `InvalidOperation`"` — is measurably **false**
  under the installed Python's default decimal context. `Decimal("nan").quantize(Decimal("1E-8"))`
  returns `Decimal("NaN")` silently; only an *ordering* comparison on a NaN `Decimal` (e.g. `> 0`)
  raises `InvalidOperation`. `to_price_decimal` does not rely on the comment's claim — it checks
  `value != value` itself before ever constructing a `Decimal`. Recorded in
  `deferred-work.md`'s new "story-3.5" section, owner: whoever next touches that file.
- Task 1.3 (2026-09-11): confirmed via wheel inspection (no `TradingNode` built — component-tier
  discipline) that `TradingNode.cache` (a property on both `live/node.py:146` and
  `system/kernel.py:846`) returns a `CacheFacade`, matching `TestLiveNode.cache`
  (`tests/component/doubles/test_live_node.py:250`, a `_TestCache` with no `position()` method
  today — confirmed unneeded, since no test here drives a real subscribe-then-publish sequence
  through it). `_phase_subscribe` hands the recorder `self._node.cache` directly, the same
  property the runner already reads nowhere else — this is a new read, not a reuse.
- Task 9.1 mutation sweep (2026-09-11), all 11 scripted via `python3 -c` string-replace against
  `src/core/live_trade_recorder.py` (backed up to scratchpad first), each RED then reverted and
  reconfirmed GREEN (`diff` against the backup showed zero drift after the sweep):
  - M1 (AC1, `last_event.last_px` instead of `avg_px_open`): killed
    `TestEntryPriceIsVolumeWeighted` as predicted, but **also** killed
    `TestSingleFillEachSideIsTheSimpleCase`, unlike the story's "AC #4 stays green" prediction —
    `position.last_event` is whichever fill was applied most recently to the whole `Position`
    (the closing leg's, once both legs are applied), not the entry leg's own last fill. Disclosed
    in `deferred-work.md`; does not weaken the sweep since AC #1's own kill is what M1 is for.
  - M2 (AC2, same on `avg_px_close`): killed `TestExitPriceIsVolumeWeighted`, exactly as predicted.
  - M3 (AC3, `last_event.commission` instead of `commissions()`): killed 4 of 5
    `TestCommissionIsSummedAcrossFills` cases (the zero-commission case is insensitive to this
    mutation by construction).
  - M4 (AC3, `commissions()[0]` unconditionally): **stayed green** as first drafted — both
    mixed-currency fixtures put the settlement currency (USD) on the entry leg, which
    `Position.commissions()` — insertion-ordered — also placed at index 0, so the mutant and the
    correct implementation agreed by coincidence. Per the story's own "inspect the fixture" rule,
    fixed by moving USD to the *exit* leg in
    `test_mixed_currency_commission_picks_the_settlement_entry_and_warns`; M4 now kills it. Also
    disclosed in `deferred-work.md`.
  - M5 (AC1+4 analog — no manual VWAP division exists in this design since fact 1 reads
    `Position`'s own VWAP directly, so the closest analog is `peak_qty` → `event_count` on the
    quantity line): killed `TestMixedRoundTrip` (`quantity` assertion), left
    `TestSingleFillEachSideIsTheSimpleCase` green exactly as predicted (that test asserts no
    `quantity` field).
  - M6 (AC5, remove the `try`): killed all three `TestTheHandlerNeverRaises` raising/failure
    cases (sink-raises, cache-miss, NaN) — the exception propagated out of
    `handle_position_event` in every case, exactly as predicted.
  - M7 (AC2, `quantity` instead of `peak_qty`): killed `TestMixedRoundTrip` with the exact
    predicted message — `pydantic_core.ValidationError: quantity ... Input should be greater
    than 0`.
  - M8 (AC1, `Decimal(value)` instead of `Decimal(str(value))`): does **not** kill the component
    tier's "100.10" pin (quantizing `Decimal(100.10)`'s float artifact happens to round back to
    the clean 8 dp value for every price magnitude this story's worked examples use) — the
    unit-tier test was corrected at drafting time to use a higher-magnitude value
    (`1234567.891234565`) that genuinely diverges after quantization, and that test kills M8
    (`Decimal('1234567.89123457') != Decimal('1234567.89123456')`). Disclosed in
    `deferred-work.md` and in the unit test's own docstring.
  - M9 (AC6, typo the topic to `"events.positions*"`): killed by
    `test_session_runner_phases.py::TestSubscribeAndTradingRegisterRealThings
    ::test_subscribe_watches_the_bar_topic_on_the_message_bus`'s hard-coded topic-literal list
    (not by the seam test in `test_session_runner_order_path.py`, which imports the same mutated
    constant for its own comparison and is therefore blind to a typo in the constant's *value* by
    design — it pins that the runner subscribes under whatever the constant says, which is a
    different, complementary claim to pinning the constant's own spelling, the same division the
    order path's `TestEventNameLiteralsArePinned` vs `EMITTED_ORDER_EVENTS` pins already use).
  - M10 (AC5, emit before calling the sink): killed both the ordering assertion
    (`order == ["sink", "log"]`) and the "no record on sink failure" assertion.
  - M11 (AC5, add an unsanctioned `log.info("position.opened", ...)`): killed by the
    two-directional `test_the_emitted_names_are_exactly_emitted_trade_events` pin.
  All 11 (12 counting the M5 substitution) mutations killed by the suite as it stands; two fixture
  corrections made during the sweep (M4, M8's unit test) are the sweep doing its job, not weaknesses
  found and left open.
- Task 9.3 full gates (2026-09-11, after all edits): `make format` → 502 files unchanged;
  `make lint` → all checks passed; `make typecheck` → success, 106 source files (one new: this
  story's module). `make test-unit` → **2458 passed** (2441 + 17: 15 new arithmetic unit tests +
  2 new guard-list parametrized cases from the `NODE_FACING_MODULES`/other membership additions).
  `make test-component` → **1487 passed, 16 skipped** (1457 + 30: 26 new
  `test_live_trade_recorder.py` + 4 new `test_session_runner_order_path.py` seam tests).
  `make test-integration` → **284 passed, 2 skipped**, unchanged (Epic 1 acceptance sweep still
  40/40) — matches Task 7's non-change contract exactly. `make test-e2e` → **1 passed**, unchanged.
  Size (CLAUDE.md D4, disclosed before the edit in Task 5.4/5.5): new module
  `src/core/live_trade_recorder.py` — 338 raw lines / 79 executable statements (budgeted ≤80),
  largest class `TradeRecorder` 29 statements (budgeted ≤45), largest function `_record_closed`
  13 statements (budgeted ≤25). `src/core/live_session_runner.py` diff — 8 raw lines / 2 new
  statements inside `_phase_subscribe` (17 statements now, was 15), module now 902 raw lines
  (was 894, the pre-existing sanctioned over-cap exception,
  `deferred-work.md:1632-1652`) — not split, per CLAUDE.md's guard-list-rot warning.

- **Review patches (2026-09-12), after the batch-apply:** `make format`/`make lint` clean;
  `make typecheck` → success, 106 source files. `make test-unit` → **2462 passed** (2458 + 4: two
  integer-literal tests removed, six `TestAggregateClosedPositionWithDoubles` cases added).
  `make test-component` → **1490 passed, 16 skipped** (1487 + 3: two
  `TestAgainstARealExecutionEngine` cases and the stale-object guard pin; the cache-miss test was
  renamed and re-pinned, not added). Dependency scan, `TestImportPurity`, `NODE_FACING_MODULES`
  and stop-path scans re-run green; **zero guard-list edits** (no new import root — the
  engine-level test imports live inside the test helper, in a test file). Size, disclosed per
  CLAUDE.md D4: `src/core/live_trade_recorder.py` is now **393 raw lines / 90 executable
  statements — over this story's own ≤ 80 budget by 10** (the guard function, the unavailable
  branch and the two `None`-rendering lines); largest class `TradeRecorder` 30, largest function
  `_record_closed` 13, all inside the project caps, which are what CLAUDE.md enforces. The budget
  was set to leave 3.6 headroom in the same module; 3.6 should re-budget from 90, not 80.
  **Runner overage, named (review patch 6):** Tasks 5.5/7.1 budgeted the runner diff at ≤ 4 raw
  lines / ≤ 3 statements; delivered is 8 raw lines / 4 statements (import, field, constructor,
  subscribe — the "2 new statements" figure above counts only `_phase_subscribe`'s body). The
  budget was self-contradictory (it also asked for a two-line comment), but the rule is to name
  an overage, not to explain it away.

### Completion Notes List

- Story delivers exactly what it promised: `src/core/live_trade_recorder.py` (aggregation +
  handler, no DB write), wired into the runner beside the order-path observer, with unit tests
  for the three pure `Decimal` helpers and component tests for the aggregation, handler contract,
  and record apparatus against real `nautilus_trader.model.position.Position` objects — no
  broker, no `TradingNode`, no `BacktestEngine` anywhere in the new test files (verified by the
  autouse C-logging guard on both new component-tier files).
- Two placement deviations from the epic/architecture docs, disclosed at drafting and now also
  dated in `architecture.md` (two spots) and `epics.md` (one spot): the module lives under
  `src/core/`, not `src/services/`; its component test lives under `tests/component/core/`, not
  the flat `tests/component/` path the AC's 2026-08-03 wording named.
  `TestImportPurity.MODULES` (not `src.db`/`src.services`) is what makes the `src/services/`
  deviation load-bearing rather than cosmetic — Story 3.6 must take a port, the same shape
  `SessionRecordPort` already uses.
  `_STDLIB_AND_FIRST_PARTY` needed **zero** additions (Task 6.3) — proven by running
  `tests/integration/core/test_epic1_ac_node.py -k no_new_dependency`, not merely asserted.
  `STOP_PATH_MODULES`/`NEW_OR_MODIFIED_FOR_STOP` deliberately do **not** include the new module
  (Task 6.4) — comments added explaining why in both lists' registers.
- Two measured facts corrected the story's own drafted design before/while writing tests, both
  disclosed in `deferred-work.md`'s new "story-3.5" section and in the Debug Log above: (1) the
  `backtest_persistence.py` comment claiming `Decimal("NaN").quantize()` raises is false — the
  module's `to_price_decimal` checks for NaN itself rather than relying on it; (2) the "100.1"
  float-artifact pin named in the story text does not actually distinguish the correct conversion
  from the M8 mutation after quantizing to 8 dp — a higher-magnitude value that genuinely
  diverges was substituted at the unit tier, with the "100.1" case kept as a plain correctness
  pin. Two mutation-sweep findings additionally corrected the story's own Task 9.1 plan (M1's
  AC #4-stays-green claim; M4's original fixture, fixed) — see the Debug Log's mutation-sweep
  entry for both.
- `Money.as_decimal()` normalizes away trailing zeros (`Money(Decimal("1.00"), USD).as_decimal()
  == Decimal("1")`) — cost two mixed-currency test literals during drafting, fixed by choosing
  amounts without a trailing zero; noted in `deferred-work.md` for the next person building a
  `Money`-based fixture.
- Task 8's live observation (AC #4/#6 live evidence) could not run this session: no IBKR Gateway
  reachable (`nc -z` on 4002/4001/7497 all closed) and outside RTH (Friday 20:13 EDT). Procedure
  P11 is written in full in `docs/qa/phase3-live-verification.md` with result `⏳ not yet run`,
  per the 3.2/3.3/3.4 precedent this project has followed at every prior Epic 3 story — the story
  lands at `review`, not `done`, with only 8.2/8.3 (and Task 8's own parent checkbox) left
  unchecked; every other task and subtask is complete.
- Deferred work routed to named owning stories (`deferred-work.md`, "Deferred from: story-3.5"):
  the backtest path's `commissions[0]` bug (Story 5.3), the side-unaware `profit_pct` formula
  reproduced for parity (Story 5.6), NETTING `PositionId` reuse and the `trade_key` Story 3.6
  must key idempotent writes on (Story 3.6), `PositionClosed` for `EXTERNAL`/`INTERNAL-DIFF`
  positions on the wildcard topic (Story 3.6, with 4.2 consulted), plus the measured corrections
  above.

### File List

**New:**
- `src/core/live_trade_recorder.py`
- `tests/unit/core/test_live_trade_recorder_arithmetic.py`
- `tests/component/core/test_live_trade_recorder.py`

**Modified:**
- `src/core/live_session_runner.py` (+8 lines: import, field, `_phase_subscribe` wiring + comment)
- `tests/component/core/test_session_runner_order_path.py` (new
  `TestTheRunnerReachesTheTradeRecorderCallSite` class, Task 4.3's seam pin)
- `tests/component/core/test_session_runner_phases.py` (`TestImportPurity.MODULES` +1; two
  existing exact-subscription pins updated deliberately —
  `test_subscribe_watches_the_bar_topic_on_the_message_bus`,
  `test_note_bar_is_subscribed_before_any_strategy_is_added`)
- `tests/unit/core/test_live_node_never_exits.py` (`NODE_FACING_MODULES` +1)
- `tests/unit/core/test_live_stop_path_is_inert.py` (comments only — the deliberate non-adds to
  `STOP_PATH_MODULES` and `NEW_OR_MODIFIED_FOR_STOP`)
- `_bmad-output/planning-artifacts/architecture.md` (two dated placement-deviation notes)
- `_bmad-output/planning-artifacts/epics.md` (one dated placement-deviation note)
- `_bmad-output/implementation-artifacts/deferred-work.md` (new "Deferred from: story-3.5" section)
- `docs/qa/phase3-live-verification.md` (new Procedure P11, result `⏳ not yet run`)
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- this story file

**Not modified** (Task 7 non-change evidence contracts, verified via `git status --short`):
`src/core/live_order_path.py`, `src/core/live_strategy_guard.py`, `src/core/live_node_builder.py`,
`src/core/live_connection_monitor.py`, `src/core/strategies/**`, `src/models/**`, `src/db/**`,
`alembic/**` (head stays `b7c419e2a3d8`), `src/services/**`, `src/cli/**`,
`tests/component/core/test_live_order_path.py`, `tests/component/core/test_live_order_recovery.py`,
`tests/component/core/test_client_order_id_determinism.py`, `CLAUDE.md`.

## Change Log

| Date | Change |
| ---- | ------ |
| 2026-09-11 | Created (backlog → ready-for-dev) from `epics.md:1343-1376` against head `438b0e4` (clean, submodule `06c00cb`). Five wheel facts measured by fresh-interpreter probes at drafting (VWAP on `Position`, no commission on any position event, reset-in-place on FLAT re-entry, backtest field parity, unsubscribed `events.position*`). Two placement decisions made and disclosed (`src/core/` over the architecture's `src/services/`; `tests/component/core/` over the AC's flat path). AC #6 (runner wiring + `trade.aggregated`) added at drafting so FR25 gets live evidence in this story rather than dead code until 3.6. Baselines: unit 2441 · component 1457/16 sk · integration 284/2 sk · e2e 1. Standing hazards for the live run carried in from 3.4's closeout: `flatten_position.py --confirm` broken (fails safe), stale-cache sessions not to be reused, residual `LONG 4 AAPL` external. |
| 2026-09-11 | Story implemented: `ready-for-dev` → `in-progress` → `review`, same session as creation. All 9 tasks / 40 subtasks complete except 8.2/8.3 (the live transcript, `⏳ not yet run` — no IBKR Gateway reachable and outside RTH; not blocking per the Epic 2 retro's standing rule, 3.2/3.3/3.4 precedent). Delivers `src/core/live_trade_recorder.py`: three pure `Decimal` helpers (`to_price_decimal`, `select_commission`, `unix_nanos_to_utc`), `aggregate_closed_position` (a real `Position` → `RecordedTrade`, no log/cache/side effect), and `TradeRecorder` (a second, independent `events.position*` subscriber beside the order-path observer, never raising, `sink` staying `None` this story). Wired into the runner at `_phase_subscribe` (+8 lines). Task 1's fresh-interpreter probe corrected one story-cited fact before any test was written: `backtest_persistence.py`'s comment claiming `Decimal("NaN").quantize()` raises `InvalidOperation` is measurably false under the default decimal context (a quiet NaN propagates silently); `to_price_decimal` checks for NaN itself rather than trusting the comment, and does not rely on it. The Task 9 mutation sweep (11 scripted mutations, all killed) found two more corrections to the story's own plan: M1's prediction that AC #4 stays green does not hold (a different, disclosed mechanism reason), and M4 initially stayed green against a fixture that happened to place the correct answer at index 0 by coincidence — fixed by moving it. All measured facts and corrections recorded in `deferred-work.md`'s new "story-3.5" section, including the deferred items owned by Stories 3.6/5.3/5.6/4.2. Gates: format/lint/mypy clean; unit 2441→2458 (+17); component 1457/16sk→1487/16sk (+30); integration 284/2sk unchanged; e2e 1 unchanged — matching Task 7's non-change contract exactly. Two existing exact-subscription pins in `test_session_runner_phases.py` updated deliberately for the new `events.position*` subscription. New module: 338 raw lines / 79 executable statements (budgeted ≤80). Alembic head unchanged at `b7c419e2a3d8`. New Procedure P11 recorded in `docs/qa/phase3-live-verification.md`, result `⏳ not yet run`. |
| 2026-09-12 | Code-reviewed: `review` → `in-progress` (NOT done — Task 8.2's live run is still `⏳ not yet run`; 3.2/3.3 precedent). The three adversarial subagent layers could not run (stalls, then a session rate limit); all three were executed in the reviewer's own context and every finding verified by probe against the installed wheel. **One decision, one HIGH:** a position flip (`execution/engine.pyx:1516-1600`) closes the `Position`, builds a new one under the same NETTING id and only then publishes `PositionClosed`, so the recorder's cache read saw the re-opened leg and dropped the trade with `trade.recorder_failed` — measured against a real `ExecutionEngine`. Resolved as option 1: snapshot fields from the event, commission from a cache position only when it vouches (`position_vouches_for`), else `None` + `trade.commission_unavailable`. Five patches applied (test hygiene, one Dev Record disclosure), one deferred to Story 3.6 (a raising sink loses the computed values from the transcript). Unit 2458→2462, component 1487→1490, module 90 statements (over the story's ≤ 80 budget, disclosed). |
