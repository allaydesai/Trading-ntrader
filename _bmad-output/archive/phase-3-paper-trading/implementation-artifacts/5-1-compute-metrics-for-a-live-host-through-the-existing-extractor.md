# Story 5.1: Compute Metrics for a Live Host Through the Existing Extractor

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a paper session's metrics computed by the same code that computes a backtest's,
so that there is no parallel metrics vocabulary to validate or keep in sync.

## Why this story is shaped the way it is

Read this before designing anything. The epic text calls this a "thin widening": `ResultsExtractor`
reads only `engine.portfolio` and `engine.cache`, a `TradingNode` exposes both, so widening the
parameter type looks like the whole job. **It is not, and a literal type-widening would silently
produce wrong numbers for every live host.** Measured at drafting (2026-10-07, installed
`nautilus-trader 1.220.0`, head `40e8f1c`, worktree `hx-p3e5-5-1-…`). Wheel paths are relative to
`.venv/lib/python3.11/site-packages/nautilus_trader/`; ripgrep skips `.venv`, so search it with
`grep -n` on explicit paths.

Three independent defects sit between "accepts a live host" and "computes a live host's metrics
correctly", each measured, not inferred:

1. **A live node's analyzer is never populated.** `PortfolioAnalyzer.calculate_statistics` is
   called in exactly one place in the wheel: `BacktestEngine._log_post_run` (F2), reached from
   `run()` → `end()`. Nothing in `live/` calls it. Every returns- and PnL-based statistic the
   extractor reads (Sharpe, Sortino, profit factor, expectancy, avg/max winner…) and max drawdown
   would come back `None` for a live host.
2. **The analyzer's total-PnL statistics and the extractor's final balance both read the account.**
   `extract_results` takes `final_balance` from `cache.account_for_venue(venue).balance_total(USD)`,
   and `calculate_statistics(account, …)` copies `account.starting_balances()` /
   `account.balances_total()` into the analyzer, from which `PnL (total)` and `PnL% (total)` are
   computed (F3). For an IB account that balance is `NetLiquidation` — or a literal `400000` the
   adapter substitutes under margin stress (Story 4.1 F5). The Epic 4 retro and Story 4.1 Task 8.4
   routed exactly this here: **the live-host widening must never read IB `AccountState` balances
   as cash or equity.**
3. **Live positions are NETTING; every backtest is HEDGING.** Every production backtest venue is
   `OmsType.HEDGING` (F7) — one `Position` per round trip, so `cache.positions_closed()` is the
   trade list. A live IB session is NETTING: a strategy that goes flat and re-enters re-uses its
   position id, and Nautilus moves each earlier closed leg into `cache.position_snapshots()` (F5).
   Measured on a real engine: 6 closed round trips, `positions_closed()` = **0** (F6). The
   extractor's trade count, win/loss count and largest win/loss would undercount by the number of
   re-entries.

The widening therefore has two halves, and **the metric math is in neither** — that is AC #2:

- **Settle the live host** the way `BacktestEngine.end()` settles a finished backtest: prime its
  analyzer from the same position set the engine uses (all positions + snapshots), with balances
  taken from the session's own realized-P&L ledger instead of an account. Measured (F4): on a real
  backtest, `starting + Σ realized_pnl` over that exact set equals `account.balance_total(USD)` **to
  the cent** — the ledger is not an approximation of the backtest's balance, it *is* it.
- **Two dispatch points in `ResultsExtractor`**: where the starting balance comes from, and where
  the final balance comes from. Nothing else in the class changes.

The proof that this is one calculation path, not two (AC #8): on the **same** finished backtest
engine, the backtest path and the live path must produce identical `BacktestResult`s.

### What this story is NOT deciding (routed, with the measured fact that forces it)

**Where the seal's live host comes from is Stories 5.2/5.3's, and F8 constrains it hard.** Position
snapshots live in an in-memory dict only — `Cache.snapshot_position` never writes the database —
and a Redis reload replays a NETTING position to its current leg (`live_session_view.py:16-19`). So
after any process restart, a session's earlier closed legs exist **only in the `trades` table**. A
stopped session's Redis namespace cannot supply its trade sample. 5.2/5.3 will most likely build a
`(portfolio, cache)` pair offline from `trades` rows — a fresh `Cache` + a real `Portfolio` (which
registers the identical statistic set, F9) — and hand it to this story's `LiveHost.settle`. This
story makes that possible and correct; it does not build it.

### Measured facts — cite, don't re-derive

| # | Fact | Where |
|---|---|---|
| F1 | `ResultsExtractor` reads exactly: `engine.cache.account_for_venue(venue)`, `engine.cache.positions_closed()`, `engine.portfolio.analyzer` (`get_performance_stats_returns()`, `get_performance_stats_pnls(currency=USD)`, `returns()`). Nothing else on the host. | `src/core/results_extractor.py:80-149, 199-237` |
| F2 | `calculate_statistics` is called only by `BacktestEngine._log_post_run` (`self._kernel.portfolio.analyzer.calculate_statistics(account, venue_positions)`), with `positions = cache.positions() + cache.position_snapshots()` filtered to the venue; `run()` calls `end()` → `_log_post_run()` when not streaming; `BacktestEngineConfig.run_analysis` defaults `True`. No `live/` module calls it. | `backtest/engine.pyx:1261-1262, 1683-1788`; `backtest/config.py:405`; `grep -rl calculate_statistics` → `analysis/analyzer.py`, `backtest/engine.pyx` only |
| F3 | `calculate_statistics(account, positions)` reads **only** `account.starting_balances()` and `account.balances_total()`, then `add_positions(positions)` → per position `add_trade(position.id, position.realized_pnl)` and `add_return(unix_nanos_to_dt(position.ts_closed), position.realized_return)`. `total_pnl` / `total_pnl_percentage` are `balances_total − starting_balances` (and that over starting × 100). `analyzer.py` is pure Python. | `analysis/analyzer.py:128-194, 226-336` |
| F4 | On a real `BacktestEngine` (sma_crossover 3/5 over the Story 4.4 `CLOSES` path, MARGIN, $100,000): Σ `realized_pnl` over `positions() + position_snapshots()` = **99,229.33** = `balance_total(USD) − 100,000` exactly (`equal? True`). | probe `/tmp/p51/probe_parity.py` (not committed), 2026-10-07 |
| F5 | `Cache.snapshot_position` pickles a copy, sets `copied_position.id = PositionId(f"{id}-{uuid4()}")` and keeps it in `self._position_snapshots` (a dict) — **unique ids**, so the analyzer's `realized_pnls.loc[position_id]` never overwrites, and **no database write**. `position_snapshots()` unpickles them. | `cache/cache.pyx:2219-2252, 4924-4951` |
| F6 | Same probe, NETTING venue: `positions()` = 1 (open, `realized_pnl` 0.00), `position_snapshots()` = 6 (each a closed leg, 16,161.80–16,996.26 USD), `positions_closed()` = **0**, `analyzer.returns()` length 7. | probe, F4 |
| F7 | Every production backtest venue is `OmsType.HEDGING`: `backtest_orchestrator.py:330, 368`; `backtest_runner.py:389, 547, 875, 1271, 1476, 1648`. So the backtest path has never seen a snapshot; F6's undercount cannot occur there. | `grep -n "oms_type\s*=" src/core` |
| F8 | Snapshots are not persisted (F5), and a Redis-loaded NETTING position replays to its current leg — "a NETTING close-then-reopen or flip replays to the current leg, because `Position.apply` resets at FLAT". After a restart, earlier closed legs survive only in `trades` (Story 3.6, one row per `PositionClosed`). | `src/core/live_session_view.py:16-19`; `src/core/live_trade_recorder.py` |
| F9 | `Portfolio.__init__` registers the analyzer's 17 statistics (MaxWinner … LongRatio). Any real `Portfolio` — a backtest kernel's, a `TradingNode`'s, or one built offline over a fresh `Cache` — carries the identical set. | `portfolio/portfolio.pyx:163-179` |
| F10 | `TradingNode.cache` → `self.kernel.cache` (`CacheFacade`); `TradingNode.portfolio` → `self.kernel.portfolio` (`PortfolioFacade`). `BacktestEngine` has the same two properties. | `live/node.py:146-168`; `backtest/engine.pyx:427, 451` |
| F11 | `mypy.ini` sets `[mypy-nautilus_trader.*] ignore_missing_imports = True`; the Cython classes (`BacktestEngine`, `Cache`, `Portfolio`, `Account`) are `Any` to mypy. `calculate_statistics`'s `account: Account` therefore accepts a two-method ledger object with no `cast`. | `mypy.ini`; probe `/tmp/p51/probe_mypy.py` (`reveal_type` → `Any`) |
| F12 | Size caps (`measure_module`, re-run at drafting, matches the checked-in baseline): `results_extractor.py` file **198/500**; `ResultsExtractor` **154** = its baseline ceiling (zero headroom); `extract_results` **85** = its ceiling (zero headroom); `__init__` 9; `extract_equity_curve` 22. Any statement added inside the class fails `test_size_caps.py`. | `tests/unit/governance/test_size_caps.py::SIZE_BASELINE:163-164` |
| F13 | The trade recorder never persists a reconciliation-owned close: `RECONCILIATION_STRATEGY_IDS = frozenset({"EXTERNAL", "INTERNAL-DIFF"})` (Story 3.6 D-D). Its module imports only stdlib + `src.core.live_session_record` + `src.models.trade` — cheap to import from the extractor. | `src/core/live_trade_recorder.py:97-148, 111-118` |
| F14 | The IB adapter's `AccountBalance.total` is `NetLiquidation`, replaced by a literal `400000` under margin stress (`execution.py:814-815`, `# TODO: Bug`), pinned by `TestAdapterCanaries::test_the_summary_keeps_cash_per_currency_and_nautilus_balance_is_not_cash`. | `tests/component/core/test_live_broker_state_adapter.py:393-410`; `deferred-work.md:3233-3238` |

### Design decisions (disclosed here, not discovered in review)

- **D-A — An explicit settled-host type, `LiveHost`, is the only thing that takes the live path.**
  `ResultsExtractor(engine=…)` dispatches on `isinstance(engine, LiveHost)`; everything else —
  `BacktestEngine`, `None`, and every `MagicMock` the existing unit tests pass — runs the existing
  code, unchanged. *Why not "anything that isn't a `BacktestEngine`"*: the existing
  `test_extract_results_no_account` passes a `MagicMock` engine; it would silently start exercising
  the live path and keep passing for the wrong reason, which is exactly the "test that cannot fail"
  hazard. The caller states "this is live" by constructing the type.
- **D-B — `LiveHost.settle(source, *, starting_balance)` is the live analogue of
  `BacktestEngine.end()`.** `source` is anything exposing `portfolio` and `cache` (a
  `ResultsHost` protocol — `TradingNode`, a `BacktestEngine`, or a pair 5.2/5.3 build offline). It
  (1) collects the **legs**: `source.cache.positions() + source.cache.position_snapshots()` — F2's
  exact set, minus the venue filter (a session's cache is its own, and its instruments span
  NASDAQ/NYSE/ARCA) and minus reconciliation-owned positions (D-D); (2) builds the **ledger**:
  starting balance and `starting + Σ realized_pnl` (F4); (3) calls
  `source.portfolio.analyzer.calculate_statistics(ledger, legs)` — the one write `settle` makes, the
  same one `end()` makes; (4) returns a frozen `LiveHost(portfolio, cache=<closed-legs view>,
  starting_balance, final_balance)`.
- **D-C — The ledger stands in for the account, and exposes only what F3 says is read.** A tiny
  frozen object with `starting_balances()` and `balances_total()` returning `{USD: Money}`. It is
  not an `Account` and offers nothing else: if a Nautilus upgrade makes `calculate_statistics` read
  any other account member, settle raises `AttributeError` and the canary test (Task 3.4) goes red
  by name. Not a real `CashAccount`: that class enforces a non-negative balance, and a session that
  lost more than its capital must still produce a record.
- **D-D — Strategy-owned legs only.** A leg whose `str(strategy_id)` is in
  `RECONCILIATION_STRATEGY_IDS` (imported from `live_trade_recorder`, not re-typed — CLAUDE.md's
  duplicated-literal rule) is excluded from the legs, the ledger and the analyzer. The trades table
  already excludes them (Story 3.6 D-D), so a host's metrics and its trades rows can never disagree
  about what a trade is. The economic activity this leaves out is the debt already routed to
  5.2/5.4 (`session_conditions`).
- **D-E — The closed-legs view is the host's `cache`.** The extractor's one remaining cache read is
  `positions_closed()` (F1). `LiveHost.cache` is a frozen view answering exactly that — closed legs
  from both `positions()` and `position_snapshots()` — so F6's NETTING undercount cannot happen and
  `extract_results`' counting loop is zero-diff. It has **no** `account_for_venue`, `account` or
  `accounts`: an account read through a live host is an `AttributeError`, so F14's rule is
  structural, not just tested.
- **D-F — A live host carries its own starting capital; there is no default.** `settle` requires
  `starting_balance: Decimal`. The extractor takes the starting balance from the host; a different
  explicit `starting_balance=` is a `ValueError` (two sources for one number is how a comparison
  goes quietly wrong — and AR7 forbids normalisation). The backtest default
  (`settings.default_balance`) is untouched for every other host.
- **D-G — Non-USD realized P&L refuses.** The extractor reads USD only (`balance_total(USD)`,
  `get_performance_stats_pnls(currency=USD)`). A leg realized in another currency would be summed
  into a USD balance; `settle` raises `ValueError` naming the currency instead. A paper session
  trading US equities cannot reach it; a future one that can is told, not misreported.
- **D-H — The two dispatch points live outside the class.** F12: the class has zero headroom.
  `_starting_balance(host, given, settings)` replaces the 5-line conditional in `__init__` (1 line),
  and `_final_balance(host, venue)` replaces the 5 account lines in `extract_results` (4 lines with
  the `None` guard) — both measured sizes go **down**. The backtest branch of each helper is the
  existing expression, moved, not rewritten.

## Acceptance Criteria

Epic ACs 1–4 are copied verbatim from `_bmad-output/planning-artifacts/prd-epic5-scope.md` (Story
5.1); each is followed by how this story proves it. ACs 5–10 are this story's own, from the routed
debt and the measured facts above.

1. **Given** `ResultsExtractor`, which today takes a `BacktestEngine`
   **When** it is widened
   **Then** it accepts either a `BacktestEngine` or a live host exposing the same `portfolio` and
   `cache` components (FR41).
   *Proof:* `LiveHost.settle(source, starting_balance=…)` accepts any object with `portfolio` and
   `cache` (a `TradingNode`-shaped double, a real `Portfolio` + `Cache` pair, a `BacktestEngine`);
   `ResultsExtractor(engine=<LiveHost>)` returns a populated `BacktestResult` from
   `extract_results()` and a non-empty curve from `extract_equity_curve()`.

2. **Given** the widening
   **When** the diff is reviewed
   **Then** the metric math is untouched — the change is confined to how the host's portfolio and
   cache are obtained (FR41).
   *Proof:* `_safe_float`, `_calculate_max_drawdown`, `_calculate_cagr`, `_calculate_calmar_ratio`,
   the stats-key mapping, the trade-counting loop, `extract_equity_curve` and
   `_build_equity_curve_from_positions` are zero-diff; `ResultsExtractor`'s diff is the `engine`
   annotation plus the two helper calls (D-H). A structural test pins that `extract_results`' body
   contains no live-host branch (the dispatch is confined to the two helpers).

3. **Given** the existing backtest path
   **When** the full backtest test suite runs after the change
   **Then** every existing test passes unchanged and backtest results are bit-identical to before.
   *Proof:* a fingerprint of `BacktestResult.to_dict()` (minus `result_id`) and the equity curve
   from a deterministic HEDGING backtest, **captured from the pre-change code** before any
   production edit (the Story 4.4 precedent), asserted equal after; the existing unit, component and
   integration suites run with zero test edits; `BacktestOrchestrator` is zero-diff (AR44).

4. **Given** any proposal to add a metrics table, model, or view specific to paper results
   **When** it appears in review
   **Then** it is rejected as an FR42 violation (AR43).
   *Proof:* the live path returns the existing `BacktestResult` class (asserted by `type(...) is`);
   no new ORM model, table, column, migration or view; a test pins the ORM table set and the single
   Alembic head `85c949ac0374` so a paper-specific table cannot land silently.

5. **Given** a live host whose cache holds an IB-shaped account (balance total `400000`, the F14
   value)
   **When** its metrics are extracted
   **Then** `final_balance` is the starting balance plus the session's own realized P&L, and
   `total_pnl` / `total_pnl_percentage` are computed from that same ledger — no IB `AccountState`
   balance is read as cash or equity (Story 4.1 F5, routed debt).

6. **Given** a live host, whose analyzer no `TradingNode` ever populates (F2)
   **When** it is settled
   **Then** its analyzer is primed exactly as `BacktestEngine.end()` primes a backtest's — the same
   position set, the same `calculate_statistics` call — so every returns- and PnL-based statistic
   the extractor reads is computed, not `None`.

7. **Given** a NETTING live host whose strategy re-entered an instrument after going flat
   **When** its metrics are extracted
   **Then** every closed leg counts as a trade — the earlier legs in `position_snapshots()` as well
   as a currently-closed position — exactly as a HEDGING backtest's separate positions do (F6).

8. **Given** one finished backtest engine
   **When** its results are extracted through the backtest path and through `LiveHost.settle` with
   the same starting balance
   **Then** the two `BacktestResult`s are identical field for field — one calculation path, not two
   (FR41).

9. **Given** a live host holding a reconciliation-owned position (`EXTERNAL` / `INTERNAL-DIFF`)
   **When** it is settled
   **Then** that position contributes nothing to the trade count, the ledger or the analyzer — the
   same rule the trades table applies (Story 3.6 D-D).

10. **Given** a live host
    **When** it is settled or extracted
    **Then** its starting capital is the one passed to `settle` — never `settings.default_balance`
    — an extractor `starting_balance=` that disagrees with it is a `ValueError`, and a leg realized
    in a currency other than USD is a `ValueError` naming that currency (D-F, D-G).

## Tasks / Subtasks

- [x] **Task 0 — Standing checks (record in the Debug Log)** (AC: #3)
  - [x] 0.1 `git status` clean on the story branch; `uv run python -m tests.unit.governance.test_size_caps --print-baseline | grep results_extractor` shows 154 / 85 (F12).
  - [x] 0.2 Baseline runs, recorded with counts: `make test-unit`, `make test-component`,
    `uv run pytest tests/unit/core/test_results_extractor.py -v`. Any pre-existing failure is
    recorded here **before** editing, so it cannot be mistaken for a regression later.

- [x] **Task 1 — Capture the pre-change fingerprint (the gate for AC #3) — BEFORE any `src/` edit**
  - [x] 1.1 New `tests/integration/core/test_results_extractor_parity.py` (`pytestmark =
    pytest.mark.integration`; runs under `--forked`). Engine harness copied from
    `tests/integration/core/test_warmup_backtest_parity.py:46-131` but with the **production** venue
    shape: `OmsType.HEDGING`, `AccountType.MARGIN`, `starting_balances=[Money(100_000, USD)]`
    (F7). Strategy: `SMACrossover` fast=3/slow=5 over the same `CLOSES` path.
  - [x] 1.2 Run it against the **unmodified** `ResultsExtractor(engine, venue, starting_balance=
    100_000.0)` with `extract_results(start, end)` (fixed UTC datetimes) and
    `extract_equity_curve(start, end)`. Paste `to_dict()` minus `result_id` and the curve into the
    file as constants (`BACKTEST_RESULT_BEFORE`, `EQUITY_CURVE_BEFORE`), with a comment naming the
    commit they were captured at. Floats as exact `repr` literals.
  - [x] 1.3 `test_the_backtest_path_is_bit_identical_to_the_pre_change_code` asserts equality.
    Commit-free check: it passes now; it must still pass after Task 4.

- [x] **Task 2 — Unit tier, red first (AC: #2, #4, #5, #10)** — extend
  `tests/unit/core/test_results_extractor.py`; existing tests untouched.
  - [x] 2.1 `_starting_balance`: backtest host → existing rule (given wins, else
    `settings.default_balance`); `LiveHost` → its own; `LiveHost` + agreeing explicit value → OK;
    `LiveHost` + disagreeing explicit value → `ValueError`.
  - [x] 2.2 `_final_balance`: `LiveHost` → its ledger value, with a host double whose cache raises
    on **any** attribute (proves no account read); backtest host with no account → `None`; with an
    account → `float(balance_total(USD).as_double())`.
  - [x] 2.3 `ResultsExtractor(engine=<LiveHost>)` → `type(result) is BacktestResult` (AC #4).
  - [x] 2.4 Structural pin (AC #2): `inspect.getsource(ResultsExtractor.extract_results)` contains
    neither `LiveHost` nor `isinstance` — the dispatch lives in the two helpers only. Sibling:
    the helpers' source *does* contain `LiveHost` (so the pin is not vacuous).
  - [x] 2.5 ORM/migration pin (AC #4): `Base.metadata.tables` equals the current set (list it
    literally) and the Alembic script directory has the single head `85c949ac0374`. Reuse an
    existing pin if one already exists (`grep -rn "metadata.tables\|get_heads" tests/`) — do not
    duplicate it.

- [x] **Task 3 — Component tier, real Nautilus objects (AC: #1, #5, #6, #7, #9, #10)** — new
  `tests/component/core/test_results_extractor_live_host.py`, `pytestmark =
  pytest.mark.component`, with the autouse `_assert_c_logging_state_is_unchanged` fixture copied
  from `tests/component/core/test_live_trade_recorder.py:65-74`. Never constructs a `TradingNode`
  or `BacktestEngine`.
  - [x] 3.1 Harness: real `Cache(database=None)` + `Portfolio(MessageBus(trader_id, clock), cache,
    clock)` (`test_live_order_path.py:149-156`); positions built from real `OrderFilled` events
    (`test_live_trade_recorder.py:76-162` `_fill`/`_round_trip`/`_open_position` shape), added with
    `cache.add_position(position, OmsType.NETTING)`; NETTING re-entry produced the way the engine
    produces it — `cache.snapshot_position(closed)` then a fresh leg on the same id — **measure in
    the Debug Log** that `positions_closed()` then undercounts (reproduces F6 at this tier), so the
    AC #7 test is known to fail without D-E.
  - [x] 3.2 AC #5: add an IB-shaped margin account to the cache with `balance_total == 400000`;
    extracted `final_balance == starting + Σ realized`, `total_pnl == Σ realized`,
    `total_pnl_percentage == Σ realized / starting × 100`; none equals anything derived from 400000.
  - [x] 3.3 AC #6: after `settle`, `sharpe_ratio`, `sortino_ratio`, `profit_factor`, `expectancy`,
    `avg_win`, `max_winner`, `max_drawdown` are not `None` (with ≥ 2 winners and ≥ 1 loser).
    Sibling proving the test can fail: the same portfolio **un**-settled, read through the
    backtest path with an account present, yields `None` for them (F2).
  - [x] 3.4 Canary (D-C): `PortfolioAnalyzer.calculate_statistics` accepts the ledger, which
    exposes only `starting_balances`/`balances_total` — named `test_the_analyzer_reads_only_two_
    balance_members_from_its_account`, message says what to do if Nautilus changes.
  - [x] 3.5 AC #7: NETTING host with 3 closed legs (2 snapshots + 1 currently-closed) →
    `total_trades == 3`, winners/losers/largest win/largest loss over all three.
  - [x] 3.6 AC #9: an `EXTERNAL`-owned and an `INTERNAL-DIFF`-owned closed position in the same
    cache contribute to neither `total_trades`, the ledger nor the analyzer.
  - [x] 3.7 AC #10: non-USD leg → `ValueError` naming the currency; `settle` with no
    `starting_balance` is a `TypeError` (keyword-only, required).
  - [x] 3.8 AC #1: a `TradingNode`-shaped double (`SimpleNamespace(portfolio=…, cache=…)`) settles;
    `extract_equity_curve()` is non-empty and starts from the host's starting balance.

- [x] **Task 4 — Implement in `src/core/results_extractor.py` (AC: #1–#10)** — after Tasks 2–3 are
  red for the right reason.
  - [x] 4.1 `ResultsHost` (`typing.Protocol`, `portfolio` + `cache` read-only properties).
  - [x] 4.2 `_Ledger` (frozen; `starting: Money`, `final: Money`; the two F3 methods) and its
    builder over the legs (USD-only, D-G; `realized_pnl is None` → contributes nothing).
  - [x] 4.3 `_ClosedLegs` (frozen view; `positions_closed()` only, D-E).
  - [x] 4.4 `LiveHost` (frozen dataclass) + `settle` classmethod (D-B), importing
    `RECONCILIATION_STRATEGY_IDS` from `src.core.live_trade_recorder` (D-D).
  - [x] 4.5 `_starting_balance` and `_final_balance` module helpers; wire them into `__init__` and
    `extract_results` (D-H). `engine` annotation becomes `BacktestEngine | LiveHost | None`.
    Update the class and method docstrings (what a live host is, that `venue` is ignored for one).
  - [x] 4.6 Re-measure (F12 command): `ResultsExtractor` and `extract_results` must be **below**
    154 / 85. Lower their `SIZE_BASELINE` ceilings to the new values only if the guard requires it
    (it does not for shrinking — a ceiling, not an exact value); never raise them.

- [x] **Task 5 — Integration tier, parity (AC: #3, #8)** — in the Task 1 file.
  - [x] 5.1 `test_the_live_path_and_the_backtest_path_agree_on_one_engine`: same finished HEDGING
    engine; extract through the backtest path, then `LiveHost.settle(engine, starting_balance=
    Decimal("100000"))` → extractor → `to_dict()` minus `result_id` identical, equity curves
    identical. Then extract the backtest path **again** after the settle and assert it is still
    equal to the fingerprint (settle re-primes the shared analyzer — prove it re-primes to the
    same state).
  - [x] 5.2 `test_a_netting_engine_counts_every_closed_leg`: the F6 shape (NETTING venue): the live
    path's `total_trades` equals the number of closed legs (snapshots + closed positions) and is
    > `len(engine.cache.positions_closed())`.
  - [x] 5.3 Run `uv run pytest tests/integration/core/test_results_extractor_parity.py --forked -v`
    and the guard-adjacent integration scan `tests/integration/core/test_epic1_ac_node.py` (no
    live module gained an import, so `_STDLIB_AND_FIRST_PARTY` should need no edit — confirm).

- [x] **Task 6 — Docs and routed debt**
  - [x] 6.1 `docs/agent/persistence.md` "Results Extraction": the live host (`LiveHost.settle`), the
    ledger rule (never an IB balance), strategy-owned legs, NETTING legs.
  - [x] 6.2 `docs/agent/nautilus.md:90-97`: one paragraph — a live node's analyzer is never
    populated by Nautilus (F2); `position_snapshots()` holds NETTING legs and is not persisted (F5,
    F8).
  - [x] 6.3 `deferred-work.md`, new "## Deferred from: story-5.1 (2026-10-07)" section:
    (a) **→ Story 5.2/5.3:** F8 — the seal's host cannot come from the Redis namespace; build the
    legs from `trades` rows into a fresh `Cache` + real `Portfolio` (F9) and `settle` that;
    (b) **→ Story 5.2:** `analyzer.returns()` is per-leg realized return keyed by close time, and an
    open leg's `ts_closed` is 0 (a 1970 index entry) — mirrored from the backtest path for parity,
    so 5.2's AR35 reconstruction must decide whether its series is this one;
    (c) Nautilus: `calculate_statistics` calls `self._returns.sort_index()` without assigning
    (a no-op), and `add_positions` appends to `_positions` without a reset, so re-settling a portfolio
    accumulates `_positions` (read only by `get_performance_stats_general`, which the extractor never
    calls) — recorded, not fixed (wheel code);
    (d) the backtest path's `positions_closed()` would undercount on a NETTING venue (F6) — moot
    today because every production venue is HEDGING (F7); recorded so a future NETTING backtest
    does not rediscover it.
  - [x] 6.4 Disposition the routed debt line in the Dev Agent Record: "never read IB `AccountState`
    balances as cash or equity" → **fixed** (AC #5, structural via D-E).

- [x] **Task 7 — Mutation sweep (copy the file to the scratchpad, apply, run the target tests,
  restore by copying back — never `git stash`; record kill/survive)**
  - M1 `_final_balance` for a `LiveHost` reads `cache.account_for_venue(...)` → AC #5 / 2.2 kill;
  - M2 `settle` omits `position_snapshots()` → AC #7 kill;
  - M3 `settle` skips `calculate_statistics` → AC #6 kill;
  - M4 `settle` drops the `RECONCILIATION_STRATEGY_IDS` filter → AC #9 kill;
  - M5 `_starting_balance` for a `LiveHost` returns `settings.default_balance` → AC #10 kill;
  - M6 `_ClosedLegs.positions_closed` returns only non-snapshot legs → AC #7 kill;
  - M7 `_Ledger` final = starting (Σ dropped) → AC #5 / #8 kill;
  - M8 backtest branch of `_final_balance` uses the ledger rule → expected to **survive** the HEDGING
    golden (F4 makes it an equivalent mutant on that data); record it as equivalent, with the reason.

- [x] **Task 8 — Gates**
  - [x] 8.1 `uv run ruff format .` · `uv run ruff check .` · `make typecheck`.
  - [x] 8.2 `make test-unit` · `make test-component` (sequentially — the Epic 4 CPU-starvation
    flake class) · the Task 5.3 integration runs. Record counts against Task 0.2.
  - [x] 8.3 `git diff --stat` shows zero changes under `src/api/`, `templates/`,
    `src/services/backtest_query.py`, `src/core/backtest_orchestrator.py`, `alembic/` (AR44,
    "no migrations").

### Review Findings

Code review 2026-10-07. Three layers: Blind Hunter, Edge Case Hunter (verified each finding with
probes against the real 1.220 `Cache`/`ExecutionEngine`/`BacktestEngine`), and Acceptance
Auditor. The auditor independently re-ran the HEAD extractor and confirmed the golden is genuinely
pre-change. It found all 10 ACs met and every zero-diff constraint held. Triage: 0 decision-needed,
11 patch, 3 defer, 4 dismissed:
- the extractor's import of `live_trade_recorder` — deliberate, under the D-D duplicated-literal rule;
- the epic-5 / `last_updated` sprint-status edits — the harness restricts edits to the 5-1 key;
- two informational notes.

All 11 patches were batch-applied (PO choice 0, 2026-10-07).

**D-B amended by patch 2:** `settle` now primes a *copy* of the source analyzer and writes nothing
to its source. Story text that says "the one write it makes" describes the pre-review design.

- [x] [Review][Patch] **NETTING legs reach the analyzer out of time order, so max drawdown, Calmar
  and the curve's timestamps are wrong for a multi-instrument session** (edge, measured). `cache.positions()`
  sorts by `PositionId` and the snapshots follow, grouped per id. `add_return` keeps insertion
  order (`sort_index()` is a no-op). Measured: AAPL +10%, MSFT −10%, AAPL +50%, AAPL −20% gives
  max drawdown −0.10 instead of −0.20, and curve times `[604800, 777600, 259200, 432000]`. HEDGING
  backtests present legs chronologically, so `settle` must sort them the same way: closed legs by
  `ts_closed`, open legs last. That keeps AC #8 parity. [src/core/results_extractor.py:156-162]
- [x] [Review][Patch] **A settled `LiveHost` is not a snapshot: it shares the source's analyzer and
  holds live `Position` objects, and its `portfolio` still exposes `account()`** (blind+edge+auditor).
  - A fill after `settle` changes the host's trade count while its ledger stays put.
  - A second `settle` on the same source rewrites the first host's `PnL%`.
  - `frozen=True` is shallow.
  - D-E's "no account through a live host" holds for `.cache` only, because `Portfolio.account(venue)` returns the IB account.

  Fix: deep-copy the legs and the analyzer, prime the copy, and expose a portfolio view holding only
  `analyzer`. `settle` then writes nothing to its source. [src/core/results_extractor.py:156-163]
- [x] [Review][Patch] **A raw `TradingNode` passed as `engine=` silently reads IB's `NetLiquidation` /
  `400000`** (blind+auditor). D-A sends every non-`LiveHost` down the backtest path, and the
  `BacktestEngine | LiveHost` annotation is `Any` to mypy (F11). Refuse a `TradingNode` (by type
  name, so the backtest module gains no live import) with a `TypeError` naming `LiveHost.settle`.
  [src/core/results_extractor.py:166-181]
- [x] [Review][Patch] **The node's cache is not a complete record of a NETTING session, yet the
  docs advertise `settle(node)`** (edge+blind, verified in source). Three ways it loses or
  duplicates legs:
  - **A flip in one order** (`SMAMomentum(allow_short=True)`) calls `_open_position(instrument, None, …)`, which overwrites the closed leg with **no snapshot** (`execution/engine.pyx:1440-1442, 1600`; `cache.pyx:2124`). Measured: 33 fills, 32 closed legs, the cache holds 1 open leg, and the ledger reads 100000.00 against an account of 112478.00.
  - **A redelivered closing fill** is snapshotted before `apply` raises `KeyError` (`engine.pyx:1444-1450`), so the leg is counted twice.
  - **A restart** drops the snapshots (F8).

  Make the docs and docstrings honest, pin the flip with an integration canary, and route the
  `trades`-built source to 5.2/5.3. That source is immune to all three: one row per
  `PositionClosed`, `insert_trade_if_absent`. [src/core/results_extractor.py:126-163;
  docs/agent/persistence.md; docs/agent/nautilus.md]
- [x] [Review][Patch] **The starting balance is not validated** (blind+edge).
  - A float `starting_balance` raises a bare `float + Decimal` `TypeError`.
  - Zero is accepted and then hits `ZeroDivisionError` in `total_return`, and a negative value flips the sign of every return.
  - Sub-cent capital is rounded by `Money` in the analyzer but not in `total_return`.
  - The extractor's mismatch check compares a `Decimal` argument to a float exactly, so `Decimal("100000.10")` is refused as "different".

  Validate in `settle` (a `Decimal`, > 0, whole cents) and compare `float(given)`.
  [src/core/results_extractor.py:94, 174]
- [x] [Review][Patch] **The `realized_pnl is None` guard in `_Ledger.over` is dead code and
  inconsistent with `settle`** (blind+edge). `Position.apply` always sets `realized_pnl`
  (`position.pyx:715-718`). The analyzer would raise on a `None` anyway, so the ledger's tolerance
  only implies a guarantee `settle` does not give. Remove the guard and its unit test.
  [src/core/results_extractor.py:86-88]
- [x] [Review][Patch] **The AC #6 test checks 8 of the statistics the extractor reads and leaves
  out `sortino_ratio`**, which Task 3.3 names; it computes 18.64 on this session (auditor). Assert every Nautilus-derived field plus
  `max_drawdown`. [tests/component/core/test_results_extractor_live_host.py:242-266]
- [x] [Review][Patch] **The AC #2 structural pin catches one spelling over a hand-kept list**
  (all three layers). It sees only `ast.Name`, so `module.LiveHost` (an `ast.Attribute`) passes
  unseen. It scans four listed methods rather than every method, and it dropped `isinstance` for
  `__init__`/`extract_results` too, although only `extract_equity_curve` had a pre-existing use.
  Derive the methods from `vars(ResultsExtractor)`, match `Attribute` too, and pin `isinstance`
  absent in `__init__` and `extract_results`. [tests/unit/core/test_results_extractor.py:422-455]
- [x] [Review][Patch] **The component AC #9 test re-types the reconciliation ids**, against the
  duplicated-literal rule (auditor). Parametrize from `RECONCILIATION_STRATEGY_IDS`.
  [tests/component/core/test_results_extractor_live_host.py:323]
- [x] [Review][Patch] **The parity test cannot detect a no-op `settle`, its comment overclaims, and
  the canary primes no positions** (blind).
  - `end()` already primed the shared analyzer, so M3 survives at the integration tier.
  - Add a settle at a different capital (50,000), whose `PnL%` only the new ledger can produce.
  - Re-word the comment for the non-mutating `settle`.
  - Run the canary over real legs. [tests/integration/core/test_results_extractor_parity.py:172-186;
    tests/component/core/test_results_extractor_live_host.py:268-279]
- [x] [Review][Patch] **Dev Agent Record gaps** (auditor): Task 5.3 names the wrong files, and no
  pre-change integration baseline was recorded behind the "330 passed" claim. Correct the record.
  [story file, Dev Agent Record]
- [x] [Review][Defer] **An open leg's return lands at the epoch, and the 1970 daily resample wrecks
  Sharpe, volatility and Sortino on both paths** [src/core/results_extractor.py:162] — deferred,
  pre-existing. Measured: four closed trades give Sharpe 6.61; one open leg makes it 0.12 (vol
  0.77→0.014, Sortino 18.64→0.34). The golden's own Sharpe is only computable because of this.
  AC #3/#8 forbid fixing one side.
- [x] [Review][Defer] **An open leg's entry commission is scored as a loser in the analyzer's PnL
  statistics but not in `losing_trades`** [src/core/results_extractor.py:162] — deferred,
  pre-existing. Measured: `avg_loss` −502→−251.5, `min_loser` −502→−1, expectancy 398→318.2. The
  golden's `min_loser: 0.0` is the same artifact on the backtest side.
- [x] [Review][Defer] **The equity curve compounds per-leg price returns on capital, so its last
  point disagrees with `final_balance`** [src/core/results_extractor.py:199-237] — deferred,
  pre-existing. Backtest golden: 177,094.46 against 159,404.73. Owner: Story 5.2 (AR35).

## Dev Notes

### The current surface — exact extension points

- `src/core/results_extractor.py:36-60` (`__init__`) — the `starting_balance` conditional (lines
  56-60) becomes one `_starting_balance(...)` call.
- `src/core/results_extractor.py:80-87` (`extract_results`) — `account = …`, `if not account`,
  `return BacktestResult()`, `starting_balance = …`, `final_balance = …` become
  `final_balance = _final_balance(self.engine, self.venue)` + `None` guard + `starting_balance`.
  **Everything from line 89 down is untouched.**
- `src/core/backtest_orchestrator.py:475-480, 500-505` — the two existing callers, keyword
  arguments `engine=, venue=, settings=, starting_balance=`. The signature must keep accepting
  exactly that; the orchestrator is zero-diff (AR44).

### Sketch (shape, not a contract — the tests are the contract)

```python
class ResultsHost(Protocol):
    @property
    def portfolio(self) -> Any: ...
    @property
    def cache(self) -> Any: ...


@dataclass(frozen=True)
class LiveHost:
    """A live host settled for results, as ``BacktestEngine.end()`` settles a backtest."""

    portfolio: Any
    cache: _ClosedLegs
    starting_balance: Decimal
    final_balance: Money

    @classmethod
    def settle(cls, source: ResultsHost, *, starting_balance: Decimal) -> "LiveHost":
        legs = [
            p
            for p in [*source.cache.positions(), *source.cache.position_snapshots()]
            if str(p.strategy_id) not in RECONCILIATION_STRATEGY_IDS
        ]
        ledger = _Ledger.over(legs, starting_balance)
        source.portfolio.analyzer.calculate_statistics(ledger, legs)
        return cls(source.portfolio, _ClosedLegs(tuple(legs)), starting_balance, ledger.final)
```

### Scope boundaries — do NOT build

- No seal service, no CLI, no `live seal` (Story 5.3). No `session_conditions` (5.4). No open-
  position disposition (5.5). No equity-curve reconstruction from `trades` rows (5.2) — this story
  only makes `settle` the thing 5.2 will feed.
- No change to any metric formula, stats-key mapping, `BacktestResult`, `BacktestPersistenceService`,
  `PerformanceMetrics`, or any DB model. No migration. No new table, model, column or view (FR42,
  AR43). Zero diff under `src/api/**`, `templates/**`, `src/services/backtest_query.py`,
  `src/core/backtest_orchestrator.py` (AR44).
- No fix to the backtest path's NETTING undercount (F6/F7 — moot, recorded in 6.3d) and no fix to
  Nautilus's `sort_index` no-op (6.3c).
- No broker read, no `TradingNode` construction in any test tier, no Redis read.

### Hazards

- **Zero headroom (F12).** Adding *any* statement inside `ResultsExtractor` fails
  `test_size_caps.py`. D-H's helpers make both measured sizes shrink; re-measure after every edit
  to the class, not at the end.
- **The golden must be captured before the first `src/` edit** (Task 1). Once the code changes,
  "before" no longer exists to re-run — the 4.4 precedent says so in its own docstring.
- **A test that cannot fail** (Epic 2 retro, restated every story since): AC #6 needs its
  un-settled sibling (3.3); AC #7 needs the measured undercount at component tier (3.1); AC #2's
  structural pin needs its non-vacuous sibling (2.4); AC #5 must use a real account in a real
  `Cache`, not a mock whose value is never read.
- **`settle` writes the source's analyzer.** That is its purpose (`end()` does the same), but the
  parity test shares one engine between both paths — Task 5.1's re-extraction after `settle` is
  what proves the write leaves a backtest's own reading unchanged.
- **C logging.** No component test may construct a `TradingNode` or `BacktestEngine`; keep the
  autouse logging-state guard. Real engines are integration-tier and `--forked` only.
- **Import placement.** `src/core/results_extractor.py` is not a `live_*` module, so no guard list
  (`NODE_FACING_MODULES`, `STOP_PATH_MODULES`, `TestImportPurity.MODULES`, `_STDLIB_AND_FIRST_PARTY`)
  applies; it gains a first-party import of `src.core.live_trade_recorder` (stdlib-only module,
  F13). If ruff's F401 bites mid-edit, make the import and its use land in one edit.
- **Money precision.** USD `Money` has precision 2. Build the ledger with `Decimal` arithmetic and
  convert with `float(Money.as_double())` — the backtest path's exact conversion — or AC #8's
  field-for-field equality can drift by a float ulp.

### Testing standards summary

TDD, red recorded first (Tasks 2–3 before Task 4; Task 1 is a characterization golden that passes
before and after by design). Unit: the two dispatch helpers, the `BacktestResult` type, the
structural and ORM pins. Component: real `Cache` + `Portfolio` + `Position` objects, no engine, no
node, the C-logging guard. Integration (`--forked`): real `BacktestEngine` runs — the bit-identical
golden (AC #3), the one-path parity (AC #8), the NETTING leg count (AC #7 end-to-end). No broker, no
Redis, no database: this story is pure computation, so NFR33's operator-procedure clause does not
apply and no P-number is added to `docs/qa/phase3-live-verification.md`.

### Project Structure Notes

- New: `tests/component/core/test_results_extractor_live_host.py`,
  `tests/integration/core/test_results_extractor_parity.py`.
- Modified: `src/core/results_extractor.py`, `tests/unit/core/test_results_extractor.py` (additions
  only), `docs/agent/persistence.md`, `docs/agent/nautilus.md`,
  `_bmad-output/implementation-artifacts/deferred-work.md`; `tests/unit/governance/test_size_caps.py`
  only if Task 4.6 requires it.
- Zero-diff, explicitly: `src/core/backtest_orchestrator.py`, `src/core/backtest_runner.py`,
  `src/models/backtest_result.py`, `src/services/**`, `src/db/**`, `alembic/**`, `src/api/**`,
  `templates/**`, every `src/core/live_*.py`.
- Architecture alignment: `architecture.md:510` names exactly this file as "MOD — widen host: accept
  BacktestEngine or live (portfolio, cache) pair; metric math untouched". `LiveHost` lives beside
  the extractor rather than in a new module (CLAUDE.md "prefer editing existing files"; file 198/500).

### References

- `_bmad-output/planning-artifacts/prd-epic5-scope.md` — Story 5.1 ACs, FR41/FR42, AR43/AR44, the
  routed-debt line for 5.1.
- `_bmad-output/planning-artifacts/architecture.md:54, 89, 106, 510, 588, 626, 701-706` — the
  widening, G4/AR35.
- `_bmad-output/planning-artifacts/prd.md:683-685` — "a live node exposes the same `portfolio` and
  `cache` components … The metric math is untouched."
- `_bmad-output/implementation-artifacts/epic-4-retro-2026-09-28.md:196-199` — "This should be an
  explicit acceptance criterion in 5.1's draft, not inherited tribal knowledge" (→ AC #5).
- `_bmad-output/implementation-artifacts/4-1-read-the-brokers-authoritative-view-of-positions-and-cash.md:459-460`
  (Task 8.4); `deferred-work.md:3233-3238`.
- `_bmad-output/implementation-artifacts/3-6-persist-each-trade-the-moment-it-closes.md` (D-D) and
  `src/core/live_trade_recorder.py:97-148`.
- `tests/integration/core/test_warmup_backtest_parity.py` — the pre-change fingerprint precedent
  and engine harness.
- `docs/agent/persistence.md` "Results Extraction"; `docs/agent/nautilus.md:90-97`.

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`)

### Debug Log References

**Task 0 — standing checks (2026-10-07, head `40e8f1c`).** Tree clean apart from the new story
file and the sprint-status key. `measure_module`: `ResultsExtractor` 154, `extract_results` 85
(= F12). Baselines: `pytest tests/unit -n auto` **3367 passed**; `pytest tests/component`
(sequential) **1928 passed, 16 skipped**. No pre-existing failure.

**Task 1 — pre-change fingerprint.** Captured with the unmodified extractor
(`/tmp/p51/capture_golden.py`, not committed) before any `src/` edit. **Deviation from 1.1:** the
Story 4.4 `CLOSES` path makes every round trip a winner on HEDGING. That leaves `max_drawdown`
0.0, and `profit_factor`, `sortino_ratio` and `calmar_ratio` `None`. A fingerprint that never
exercises a formula cannot prove it unchanged. Used `100 + 6·sin(i/6) + 4·sin(i/2.3)` instead:
16 closed trades, 5 losers, `profit_factor` 14.54, `max_drawdown` −0.0161, `calmar_ratio` 36.81.
The golden passed on the pre-change code (1 passed), then again after Task 4.

**Measured during implementation:**
- NETTING on the same path (real engine): `positions()` 1, `position_snapshots()` 16,
  `positions_closed()` **0**, so the backtest path's `total_trades` is 0. The account balance is
  identical to HEDGING (`total_return` 0.5940473 both). Pinned in
  `TestNettingLegs::test_a_netting_engine_counts_every_closed_leg`.
- Component tier (`/tmp/p51/probe_netting.py`): snapshot then in-place reopen reproduces the
  engine's NETTING shape. Two closed legs give `positions_closed()` = 1. The snapshot id is
  `{id}-{uuid4}`, `realized_pnl` is preserved, `strategy_id` is kept. Constructing a real
  `Portfolio` and an `AccountFactory` margin account leaves C logging uninitialised.
- **An unprimed analyzer is worse than `None`** (Task 3.3 sibling). The returns statistics
  (`sharpe_ratio`, `profit_factor`, `max_drawdown`) read `None`, but the PnL statistics
  (`expectancy`, `avg_win`, `avg_loss`, `max_winner`, `max_loser`) read a silent **0.0**. The
  sibling test pins exactly that.
- Nautilus's returns statistics downsample to daily bins. Component legs are therefore spaced a
  day apart: legs one second apart collapse into one bin, and Sharpe would be `None` for a reason
  unrelated to the code under test.
- AC #2's structural pin is AST-based over method **bodies**. The `engine` annotation
  legitimately names `LiveHost`. `extract_equity_curve` already used
  `isinstance(timestamp, int)` before this story, so `isinstance` alone is not the signal; the
  name `LiveHost` is. The non-vacuous sibling asserts both names appear in the two helpers.
- Task 2.5: no existing ORM-table or Alembic-head pin (`grep -rn "metadata.tables\|get_heads"
  tests/` is empty), so one was added: 8 tables, single head `85c949ac0374`.

**Task 4.6 — sizes after.** File 259/500; `ResultsExtractor` **149** (was 154);
`extract_results` **84** (was 85); `__init__` 5 (was 9). New: `LiveHost` 14, `_Ledger` 20,
`_ClosedLegs` 4, `ResultsHost` 5; largest new function is `_Ledger.over` at 13. The two
`SIZE_BASELINE` ceilings were **lowered** to 149/84 because
`test_regenerating_the_baseline_reproduces_it_exactly` requires the baseline to regenerate
exactly. No ceiling was raised.

**Task 5.3.** `test_results_extractor_parity.py` (`--forked`): 3 passed at dev time (5 after the
review patches). The guard-adjacent scan `test_epic1_ac_node.py`, run with
`test_warmup_backtest_parity.py` (`--forked`): 13 passed. `_STDLIB_AND_FIRST_PARTY` needed no
edit, because the extractor is not a `live_*` module. *(Corrected at code review: this entry
first named only the last two files.)*

**Task 7 — mutation sweep** (`/tmp/p51/mutate.py`: apply to the file, run unit + component +
integration-parity, restore the original bytes; `restored: True`, never `git stash`):

| # | Mutant | Verdict | First kill |
|---|---|---|---|
| M1 | live final balance read through the account path | KILLED | unit `test_a_live_host_reads_its_ledger_and_nothing_else`; integ parity |
| M2 | `settle` omits `position_snapshots()` | KILLED | unit `TestSettle`; integ NETTING |
| M3 | `settle` skips `calculate_statistics` | KILLED | unit `TestSettle` (integ survives — see below) |
| M4 | reconciliation-owned filter dropped | KILLED | unit `TestSettle[EXTERNAL]` (integ survives: no such legs in a backtest) |
| M5 | live starting balance from settings default | KILLED | unit `TestStartingBalanceSource`; integ parity |
| M6 | closed-legs view from `positions()` only | KILLED | unit `TestSettle`; integ NETTING |
| M7 | ledger final = starting | KILLED | unit `TestLedger`; integ parity |
| M8 | disagreeing explicit starting balance accepted | KILLED | unit `test_a_live_host_refuses_a_disagreeing_explicit_value` |
| M9 | non-USD leg summed, not refused | KILLED | unit `test_a_non_usd_leg_refuses_naming_the_currency` |

The integration parity test does **not** kill M3, as expected: the finished engine's analyzer was
already primed by `end()`, so a skipped re-prime leaves the same state. The component tier
(`TestTheAnalyzerIsPrimed`) and the unit tier kill it. The story's planned M8 ("backtest branch
uses the ledger rule") was **not applied**. The backtest branch has no legs to build a ledger
from without settling, which is what AC #8's parity test already does: it proves ledger ==
account on HEDGING (F4), the reason that mutant would be equivalent. M8/M9 above replace it.

**Task 8 — gates.** `ruff format .` (566 unchanged) · `ruff check .` (all passed) ·
`make typecheck` (no issues, 120 files) · `pytest tests/unit -n auto` **3399 passed** (+32) ·
`pytest tests/component` sequential **1942 passed, 16 skipped** (+14) ·
`pytest tests/integration -n auto --forked` **330 passed, 2 skipped**. `git status`: no change
under `src/api/`, `templates/`, `src/services/`, `src/core/backtest_orchestrator.py`,
`alembic/`, `src/db/`.

**No pre-change integration baseline was captured** (code review, auditor finding 7). Task 0.2
listed unit and component only. What the post-change run does show: 0 failures. The only
integration file this story adds is `test_results_extractor_parity.py`, and no other integration
test file changed, so every other integration test passed against the changed extractor. AC #3's
"bit-identical" rests on the pre-change golden, not on that count.

**Code-review patches (batch-applied 2026-10-07).**
- **Patch 1** — `settle` sorts legs by `(is_open, ts_closed)`; component `TestLegsAreReadInTimeOrder`: max drawdown −0.20 (cache order −0.10), curve times monotonic.
- **Patch 2** — `settle` deep-copies the legs and the analyzer and returns `_SettledPortfolio(analyzer)`; it writes nothing to its source. Component `TestASettledHostIsASnapshot`: a fill after settle, a second settle, and the source analyzer untouched.
- **Patch 3** — `_starting_balance` refuses any `TradingNode` (by MRO class name) with a `TypeError`.
- **Patch 4** — module/`settle` docstrings, `persistence.md`, `nautilus.md` and `deferred-work.md` now state that a NETTING node's cache loses legs on flips and restarts and duplicates them on redelivered fills. Integration canary `test_canary_a_netting_flip_leaves_no_closed_leg_in_the_cache`.
- **Patch 5** — `_require_capital`: `Decimal`, finite and > 0, whole cents. The extractor compares `float(given)`.
- **Patch 6** — the dead `None` guard and its test are removed.
- **Patch 7** — AC #6 asserts 20 statistics, with dates so CAGR/Calmar compute.
- **Patch 8** — `_body_references` (`Name` + `Attribute` + string constants) over `EXTRACTOR_METHODS` derived from `vars(ResultsExtractor)`; `isinstance`/`type` pinned absent in `__init__`/`extract_results`. `hasattr` is not pinned: `extract_results` has always probed a position with it.
- **Patch 9** — component AC #9 parametrized from `RECONCILIATION_STRATEGY_IDS`.
- **Patch 10** — integration `test_the_settled_analyzer_carries_the_ledger_not_the_engines_balances` (capital 50,000); comment re-worded; the canary runs over real legs.
- **Patch 11** — this record.

**Post-review mutation sweep** (`/tmp/p51/mutate_v2.py`, same apply/run/restore discipline,
`restored: True`): **18/18 killed**. M1–M9 as before, M8b (exact Decimal/float comparison
restored), and the new M10–M17:
- M10 — legs left in cache order;
- M11 — legs not copied;
- M12 — source analyzer primed in place;
- M13 — `TradingNode` guard removed;
- M14–M16 — each capital check removed;
- M17 — the source portfolio exposed.

M3 is now killed at the **integration** tier as well, by the 50,000-capital test.

**Post-review gates:** `ruff format .` (566 unchanged) · `ruff check .` · `make typecheck` (120
files, no issues) · unit **3415 passed** (+48 over the Task 0 baseline) · component sequential
**1948 passed, 16 skipped** (+20) · integration `--forked` **332 passed, 2 skipped**. Sizes:
file 286/500; `ResultsExtractor` 149, `extract_results` 84 (unchanged from dev; the ceilings stand);
`LiveHost` 21; largest new function `LiveHost.settle` 16.

### Completion Notes List

- **AC #1:** `LiveHost.settle(source, *, starting_balance)` accepts anything exposing `portfolio`
  and `cache`: a `TradingNode`-shaped pair, a real `Cache` + `Portfolio`, or a finished
  `BacktestEngine`. `ResultsExtractor(engine=<LiveHost>)` yields a full `BacktestResult` and
  equity curve.
- **AC #2:** the metric math is zero-diff: `_safe_float`, `_calculate_*`, the stats-key mapping,
  the counting loop, `extract_equity_curve`, `_build_equity_curve_from_positions`. The class diff
  is the `engine` annotation, docstrings, and two helper calls. An AST pin keeps any host branch
  out of the method bodies.
- **AC #3:** the pre-change fingerprint (`BacktestResult` + equity curve, captured at `40e8f1c`)
  is bit-identical after the change. Every existing test passes with zero edits.
  `BacktestOrchestrator` is zero-diff.
- **AC #4:** the live path returns `BacktestResult` itself. No table, model, column, view or
  migration. Pinned: the ORM table set and the single Alembic head.
- **AC #5 (routed debt, Story 4.1 F5 / Task 8.4 — dispositioned FIXED).** A live host's final
  balance and `PnL (total)`/`PnL% (total)` come from the session's realized-P&L ledger. Proven
  against a real cache holding an IB-shaped account at `400000`; the sibling test shows the
  backtest path does read that 400000 from the same cache. It is also structural: the settled
  host's `cache` has no account accessor.
- **AC #6:** `settle` primes the analyzer with F2's position set via `calculate_statistics`,
  exactly as `end()` does.
- **AC #7:** NETTING snapshotted legs count as trades (component: 4 legs where the raw cache
  reports 2; integration: 16 where the raw cache reports 0).
- **AC #8:** on one finished engine the live path and the backtest path produce identical
  `BacktestResult`s and curves. The backtest path still matches the fingerprint after `settle`
  re-primed the shared analyzer.
- **AC #9:** `EXTERNAL`/`INTERNAL-DIFF` legs are excluded from count, ledger and analyzer, via
  the trade recorder's own `RECONCILIATION_STRATEGY_IDS` (imported, not re-typed).
- **AC #10:** a live host's capital is required and is its own. A disagreeing explicit value is
  a `ValueError`, and so is non-USD realized P&L (the error names the currency).
- Routed onward in `deferred-work.md` ("Deferred from: story-5.1"): the seal's host must be built
  from `trades` rows, not Redis (→ 5.2/5.3); the per-leg returns series and the epoch-0 open leg
  (→ 5.2); the pre-existing `time: 0` equity-curve point; `_positions` accumulation; and the
  NETTING backtest undercount (moot: HEDGING only).
- No operator procedure is added: this story touches no broker, Redis or database (NFR33's
  clause does not apply).
- **After code review:**
  - The settled host is a true snapshot: legs and analyzer are copied, the source is never written, and neither its `cache` nor its `portfolio` reaches an account.
  - A raw `TradingNode` is refused, which closes AC #5's last unsettled path.
  - Legs are in time order.
  - Capital is validated.
  - The docs no longer advertise a node's cache as a complete NETTING record.
- Three pre-existing metric artifacts are routed to 5.2/5.5 plus an epic-level both-sides decision (`deferred-work.md`, "code review of story-5.1"): the epoch-0 open leg that wrecks Sharpe, open-leg commission scored as a loser, and a curve that is not the ledger. The most consequential for paper is the first: a session that stopped mid-position reads a near-zero Sharpe.

### File List

- `src/core/results_extractor.py` — modified: `ResultsHost`, `_Ledger`, `_ClosedLegs`,
  `LiveHost` (+ `settle`), `_starting_balance`, `_final_balance`; `ResultsExtractor.__init__` /
  `extract_results` wired to the helpers; docstrings.
- `tests/unit/core/test_results_extractor.py` — modified (additions only; existing tests
  unchanged).
- `tests/component/core/test_results_extractor_live_host.py` — new.
- `tests/integration/core/test_results_extractor_parity.py` — new.
- `tests/unit/governance/test_size_caps.py` — modified: two `SIZE_BASELINE` ceilings lowered
  (154→149, 85→84).
- `docs/agent/persistence.md` — modified: "Live hosts" under Results Extraction.
- `docs/agent/nautilus.md` — modified: analyzer priming and NETTING snapshots.
- `_bmad-output/implementation-artifacts/deferred-work.md` — modified: "Deferred from:
  story-5.1".
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — modified: the 5-1 key only.
- `_bmad-output/implementation-artifacts/5-1-compute-metrics-for-a-live-host-through-the-existing-extractor.md`
  — new (this story).

## Change Log

- 2026-10-07 — Story drafted (create-story): F1–F14 measured against `nautilus-trader 1.220.0`;
  D-A–D-H disclosed.
- 2026-10-07 — Implemented (dev-story): live host settled through the existing extractor. One
  calculation path, proven by a pre-change golden and a same-engine parity test. Never reads an
  IB balance. NETTING legs counted. 9/9 mutants killed. Status → review.
- 2026-10-07 — Code review (bmad-code-review, 3 layers): 11 patch, 3 defer, 4 dismissed. All 11
  patches applied: time-ordered legs, a snapshot `settle` that writes nothing to its source, the
  `TradingNode` refusal, capital validation, honest NETTING-cache docs plus a flip canary, and
  stronger pins. 18/18 mutants killed. Status → done.
