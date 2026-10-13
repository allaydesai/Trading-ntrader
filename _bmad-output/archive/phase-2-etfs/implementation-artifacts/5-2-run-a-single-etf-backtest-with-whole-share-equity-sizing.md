# Story 5.2: Run a Single-ETF Backtest with Whole-Share Equity Sizing

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to run a backtest against an imported ETF using an existing strategy with whole-share position sizing,
so that ETFs behave as a first-class, equity-style backtest asset — identical to Stocks, with no crypto/FX sizing rules and no ETF-specific adapter.

## Acceptance Criteria

1. **Given** a venue-qualified ETF (a `catalog_instruments` row whose `nautilus_id` is set, e.g. `SPY.ARCA`) with imported bars and an existing strategy (`sma_crossover`), **When** the operator runs a single-instrument backtest through the **existing** `load_from_catalog` → `BacktestOrchestrator.execute` path, **Then** the run completes against the FirstRate ETF catalog data and returns a `BacktestResult` (no exception, `final_balance > 0`) — the same path Stocks use, with **no runtime adapter** (NFR16, ADR-10). [Source: epics.md Story 5.2 AC1; Story 5.1 routing contract; backtest_orchestrator.py `execute`]
2. **Given** the ETF backtest actually generates orders during the run, **When** those orders are sized by `SMACrossover._calculate_position_size`, **Then** **whole-share (equity-style) sizing** is applied — every filled `quantity` is a whole number (no fractional part) — exactly as for a Stock at the same price/portfolio. The sizing branch is driven purely by the instrument's `size_precision` (0 for `Equity`), so an ETF and a Stock at identical price + portfolio produce the **identical** share count; there is **no `asset_class` branch** and **no crypto/FX fractional path**. [Source: epics.md Story 5.2 AC2; sma_crossover.py `_calculate_position_size` lines 120-171; Story 5.1 AC3 (`lot_size=1`, `size_precision=0`)]
3. **Given** a leveraged or inverse ETF (e.g. `TQQQ`, `SQQQ`), **When** it is served for a backtest and its orders are sized, **Then** it settles as **ordinary shares** using the same whole-share sizing (`size_precision == 0`, `size_increment == 1`) — **not** crypto/FX rules and **no** leverage/inverse special-casing: there is no such concept in the pipeline, a leveraged/inverse ETF is a plain Nautilus `Equity` exactly like `SPY`. A real-engine run of such a ticker produces whole-share fills. [Source: epics.md Story 5.2 AC3; architecture.md ADR-10 "leveraged/inverse ETFs settle as ordinary shares"; backtest_loader.py `build_equity`]
4. **Given** this story is verification/lock-in of already-shipped behaviour (Story 5.1 built the equity-shaped ETF instrument; the strategy sizing is already asset-class-agnostic), **When** its changes land, **Then** prior backtest numbers are unchanged, **no** production code is modified (tests only), **no** Alembic migration and **no** results-DB schema change are introduced, and no persistence occurs (`persist=False`, in-process bars, never live data). If any production change appears necessary, STOP and surface it. [Source: CLAUDE.md harness constraints; architecture.md line 565 "No new files"; Story 5.1 File List — production delta was Story 5.1's, not 5.2's]

## Tasks / Subtasks

- [x] **Task 1: Unit — ETF whole-share sizing == Stocks (no crypto/FX branch)** (AC: #2, #3) — *extend `tests/unit/strategies/test_sma_crossover_position_sizing.py`; TDD — write assertions first*
  - [x] Add a test proving an **ETF and a Stock size identically**: call `SMACrossover._calculate_position_size` twice with `size_precision=0` (the `Equity` value) at the same `close_price`/`portfolio_value`/`position_size_pct` and assert the two `Quantity` results are equal and integer (`"." not in str(qty)`). This locks in AC2 "consistent with Stocks" — the sizing path cannot tell an ETF from a Stock (both are `size_precision=0` equities), which **is** the guarantee.
  - [x] Add a **real-Equity binding** test: build an actual `Equity` via `backtest_loader.build_equity` for a **leveraged** ETF (`TQQQ.NASDAQ`) and an **inverse** ETF (`SQQQ.NASDAQ`) with generated daily bars, read `instrument.size_precision` off the real instrument (assert `== 0`), feed it into `_build_sizing_self`, and assert the resulting quantity is whole-share. Proves the loader's ETF `Equity` (AC3, "settles as ordinary shares") wires straight into the strategy's whole-share branch — no crypto/FX fractional path.
  - [x] Keep the existing `SimpleNamespace` duck-typed `self` pattern (Nautilus `Strategy.cache` is a Cython slot) — extend `_build_sizing_self`, do not rewrite it.

- [x] **Task 2: Component — leveraged/inverse ETF is a plain whole-share Equity** (AC: #3) — *extend `tests/component/services/firstrate/test_catalog_backtest_loader.py` (`TestEtfRoutingNoAdapter` or a sibling class)*
  - [x] Add a parametrized test over leveraged/inverse ETF tickers (`TQQQ.NASDAQ`, `SQQQ.NASDAQ`, and a multi-dot control) asserting `build_equity(...)` yields a Nautilus `Equity` with `size_precision == 0`, `int(size_increment) == 1`, `int(lot_size) == 1`, and USD currency — i.e. ordinary-shares settlement, **no** fractional/crypto precision and **no** leverage/inverse special-casing in the synthesis. [Source: build_equity lines 51-88]
  - [x] Assert the loader still builds the bar_type off `nautilus_id` with **no `asset_class` branch** (the ETF flows through the identical Stocks path) — reuse the existing `_make_bar_for`/`_make_instrument_row` mocks with `asset_class="ETF"`.

- [x] **Task 3: Integration — real single-ETF run produces whole-share fills** (AC: #1, #2, #3) — *extend `tests/integration/core/test_backtest_catalog_integration.py` (`--forked`, real `BacktestEngine`)*
  - [x] Add a **zigzag** daily-bar helper (`_make_zigzag_bars`) so the SMA crossover actually generates multiple orders — a whole-share assertion needs real fills. Add `IVV.ARCA` (plain ETF, zigzag) and `TQQQ.NASDAQ` (leveraged ETF stand-in, zigzag) to the `synthetic_catalog` fixture. **`SPY.ARCA` is left on `_make_daily_bars` (10-bar monotonic)** so the Story 5.1 `len == 10` assertion is unchanged — a dedicated zigzag ticker (`IVV.ARCA`) carries the plain-ETF fills proof instead.
  - [x] `test_named_catalog_etf_single_run_sizes_whole_shares`: run `IVV.ARCA` (plain ETF) through `load_from_catalog` → `BacktestOrchestrator.execute` with `persist=False`; assert the run completes (`result is not None`, `final_balance > 0`, `run_id is None`), then read `orchestrator.engine.trader.generate_order_fills_report()` and assert it is **non-empty** and **every** `quantity` is a whole number (`float(q).is_integer()` / no `.` in the string form). Dispose in `finally`. (AC1 + AC2)
  - [x] `test_named_catalog_leveraged_etf_settles_as_ordinary_shares`: same run for `TQQQ.NASDAQ`; assert the synthesised instrument is an `Equity` with `size_precision == 0`, the run completes, and all fill quantities are whole shares — proving a leveraged/inverse ETF settles as ordinary shares via the identical path (AC3).
  - [x] Both tests MUST NOT persist (no DB write) and MUST NOT touch live data — bars are generated in-process; `--forked` runner (`make test-integration`) for Nautilus C/Rust `fork()` isolation.

- [x] **Task 4: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new imports like `Equity` must be used in the same edit; re-read files after edits).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: ≤6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Do not add any.
  - [x] `uv run pytest tests/unit/strategies/test_sma_crossover_position_sizing.py tests/component/services/firstrate/test_catalog_backtest_loader.py -q` green; the integration additions via `make test-integration` (or `pytest ... --forked`).
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`. All changes are additive test code.
  - [x] Confirm **no production change**, no Alembic migration, no `src/db/models/**` change (AC4). `git diff --stat` should show only the three test files (+ this story file + sprint-status).

## Review Findings

Adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor). All four ACs confirmed covered by executing tests; the diff is verifiably test-only (no `src/`, `alembic/`, or `src/db/models/**` change — AC4 holds). No blocking/major behavioural defects. Findings triaged:

- [x] [Review][Patch] `test_etf_and_stock_size_identically` fed identical literals (`size_precision=0` both sides) → tautological; and no ETF test checked share-count **magnitude**, only integrality [test_sma_crossover_position_sizing.py] — applied: both sides now derive `size_precision` from **real** `build_equity` instruments (SPY.ARCA ETF vs AAPL.NASDAQ stock) and assert the **exact** expected count (714 shares); the parametrized ETF test's vacuous `>= 1` floor replaced with the same exact-magnitude assertion. The suite now catches a sizing-magnitude regression, not just a fractional-shape one.
- [x] [Review][Patch] Leveraged-ETF integration test discarded `run_id` and never asserted `run_id is None` (AC4 no-persistence) unlike its plain-ETF sibling [test_backtest_catalog_integration.py] — applied: `assert run_id is None` added.
- [x] [Review][Dismiss] "Whole-share assertions can't detect the sizing branch flipping (`> 0` → `>= 0`)" — verified non-issue: at `size_precision == 0` **both** branches emit a whole-share `Quantity` (`f"{x:.0f}"` → `Quantity.from_str("714")`, precision 0), so the flip is behaviourally indistinguishable at the output — a property of the code, not a coverage hole. No contortion warranted.
- [x] [Review][Dismiss] "`size_precision==0`/`size_increment==1` are Nautilus invariants, not `build_equity` behaviour" — kept as belt-and-suspenders: the genuine AC3 regression (an ETF synthesised as a **non-`Equity`**/fractional instrument) **is** caught by the `isinstance(..., Equity)` + precision assertions; asserting the framework constant alongside is harmless.

Verified non-issues: `assert not fills.empty` is not flaky — the zigzag closes (`100 + ±5 + 0.1·i`) with fast=2/slow=5 produce the first crossover at bar 6 and repeat every ~3 bars across all 40 bars (fully inside the 2018-01-01…03-01 window); `fills["quantity"]` stringifies precision-0 quantities without a `.` (assertion is well-formed); adding `IVV.ARCA`/`TQQQ.NASDAQ` to `synthetic_catalog` and leaving `SPY.ARCA` on `_make_daily_bars` leaves the Story 5.1 `len == 10` assertions intact (reads are `bar_type`-scoped per `nautilus_id`); `_build_sma_request`'s new `start`/`end` defaults are immutable `datetime`s (no aliasing).

## Dev Notes

### The core idea (why this story is verification-only)
Story 5.1 already made ETFs first-class equity data: `firstrate/backtest_loader.build_equity` synthesises a Nautilus `Equity` from the venue-qualified `nautilus_id` regardless of `asset_class`, carrying `lot_size = 1`, USD, and — critically — `size_precision = 0` (verified: an `Equity` has `size_increment = 1`). Whole-share sizing is then a *consequence*, not new code: `SMACrossover._calculate_position_size` reads `instrument.size_precision` from the cache and branches —

```
size_prec = instrument.size_precision if instrument else 0
if size_prec > 0:          # crypto/FX: fractional quantity at instrument precision
    quantity = Quantity.from_str(f"{float(raw_qty):.{size_prec}f}")
else:                       # equities (incl. ETFs): whole shares
    quantity = Quantity.from_int(max(int(raw_qty), 1))
```

An `Equity` (Stock **or** ETF, including leveraged/inverse) is always `size_precision = 0`, so it always takes the whole-share branch. The path is asset-class-blind — that blindness **is** AC2's "consistent with Stocks" and AC3's "settles as ordinary shares (not crypto/FX rules)". There is no leverage/inverse concept anywhere in the pipeline; `TQQQ` and `SQQQ` are plain `Equity` instruments exactly like `SPY`. So Story 5.2 ships **no production code** — it *locks the contract in* with tests at three tiers so no future change can slip an ETF-specific sizing branch or a fractional path in. [Source: sma_crossover.py lines 140-171; backtest_loader.py build_equity lines 51-88; architecture.md ADR-10]

### The seam (what is exercised, end to end)
```
catalog_name + ETF ticker (SPY / TQQQ)
    │  load_from_catalog → build_equity(nautilus_id) → Equity(size_precision=0, lot_size=1, USD)
    ▼
BacktestOrchestrator.execute(request, bars, Equity)
    │  add_venue / add_instrument(Equity) / add_data(bars) / add_strategy(sma_crossover) / run()
    │        on_bar → crossover → _calculate_position_size
    │              instrument.size_precision == 0 → Quantity.from_int(whole shares)   ← AC2/AC3
    ▼
order fills report → every quantity is a whole number            (real-engine proof)
```
The whole-share guarantee is only *observable* through an actual run, hence the integration-tier fills-report assertion; the unit tier pins the branch logic and the loader→sizing binding; the component tier pins the `Equity` shape for leveraged/inverse tickers. [Source: backtest_orchestrator.py `_setup_engine`/`_create_strategy`; probe confirmed fills report `quantity` column is whole-share]

### Why no production change (and what would force a STOP)
The whole-share branch, the `Equity` synthesis, and the no-adapter routing all pre-date this story (Stories 3.x/5.1). Verified empirically that a real ETF run yields integer fill quantities (`94, 104, 103, …`). If — and only if — a test surfaced a fractional ETF fill, an `asset_class` branch, or a non-`Equity` ETF instrument, that would be a genuine bug requiring a production fix; **STOP and surface it** rather than adding one speculatively. No Alembic migration / results-DB schema change is in scope (Story 5.4 owns persistence). [Source: harness constraints; Story 5.1 Dev Notes]

### Scope boundaries (do NOT do here)
- **No production edits** unless a test proves a real defect (then STOP + surface). This is a lock-in story.
- **No runtime adapter / no `asset_class` branch** in the loader, orchestrator, or strategy — that violates NFR16/ADR-10.
- **No multi-ETF / `instrument_ids` list** — that is **Story 5.3**.
- **No persistence / DB work** — **Story 5.4**; runs use `persist=False`, in-process bars. **No Alembic migration, no results-DB schema change.**
- **No reference-comparison tooling** — **Story 5.5**.
- **No web/CLI surface change** — the existing CLI/web backtest entry points already route ETFs through `load_from_catalog`.
- **Never touch live data** — bars generated in-process / mocked; local-catalog only.

### Testing standards summary
- Tiers: **unit** for the sizing branch + loader→sizing binding; **component** for the leveraged/inverse `Equity` shape; **`--forked` integration** for the real-engine whole-share fills proof (Nautilus C/Rust extensions corrupt state across `fork()`). [Source: CLAUDE.md test tiers; docs/agent/testing.md]
- TDD Red→Green where a new assertion can fail first; the leveraged/inverse and equivalence assertions LOCK IN the contract (may pass immediately — that is expected for a verification story, mirror Story 5.1 Task 2).
- Reuse existing helpers: `_build_sizing_self` (unit), `_make_instrument_row`/`_make_bar_for` (component), `_make_daily_bars`/`synthetic_catalog`/`_build_sma_request` (integration). Extend, don't rewrite.

### Project Structure Notes
- Touch points (tests only): `tests/unit/strategies/test_sma_crossover_position_sizing.py`, `tests/component/services/firstrate/test_catalog_backtest_loader.py`, `tests/integration/core/test_backtest_catalog_integration.py`. No production files (architecture.md line 565 "No new files").

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.2 (lines 803-821); NFR16 (line 105)]
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-10 (lines 333-339); line 565 (E5 "No new files")]
- [Source: src/core/strategies/sma_crossover.py (`_calculate_position_size` lines 120-171)]
- [Source: src/services/firstrate/backtest_loader.py (`build_equity` lines 51-88)]
- [Source: src/core/backtest_orchestrator.py (`execute`/`_setup_engine` lines 87-225)]
- [Source: tests/unit/strategies/test_sma_crossover_position_sizing.py; tests/component/services/firstrate/test_catalog_backtest_loader.py; tests/integration/core/test_backtest_catalog_integration.py]
- [Source: 5-1-serve-etf-catalog-data-to-the-backtestengine.md (Story 5.1 — established equity-shaped ETF instrument + no-adapter routing)]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/unit/strategies/test_sma_crossover_position_sizing.py -q` → 10 passed.
- `uv run pytest tests/component/services/firstrate/test_catalog_backtest_loader.py -q` → 14 passed, 1 skipped (21M-bar parity, local-only).
- `uv run pytest tests/integration/core/test_backtest_catalog_integration.py --forked -q` → 8 passed (incl. the two new whole-share ETF runs).
- `uv run ruff check .` → All checks passed. `uv run mypy .` → Success: no issues found in 360 source files.
- Probe (throwaway, removed): a real `IVV.ARCA`/`SPY.ARCA` ETF run through `BacktestOrchestrator` emits an order-fills report whose `quantity` column is all whole numbers (`94, 104, 103, …`) — confirming whole-share sizing before writing the assertions.

### Completion Notes List

- **Zero production change — verification/lock-in story.** Whole-share ETF sizing was already shipped: Story 5.1's `build_equity` synthesises a Nautilus `Equity` (`size_precision=0`, `size_increment=1`, `lot_size=1`, USD) for any ETF, and `SMACrossover._calculate_position_size` branches purely on `instrument.size_precision` (0 → whole shares via `Quantity.from_int`, >0 → crypto/FX fractional). An `Equity` is always `size_precision=0`, so ETFs — including leveraged/inverse — always take the whole-share branch, identical to Stocks. There is no `asset_class` branch and no leverage/inverse concept anywhere in the pipeline. This story adds tests at three tiers to lock that contract in; `git diff --stat` shows only test files + this story + sprint-status (AC4).
- **"Consistent with Stocks" (AC2) is proven by asset-class blindness.** The unit tier calls the sizing method twice at `size_precision=0` and asserts identical whole-share quantities — the path literally cannot distinguish an ETF from a Stock.
- **Leveraged/inverse settles as ordinary shares (AC3).** `TQQQ`/`SQQQ` are asserted (component tier) to synthesise as plain whole-share `Equity` with no fractional precision; the integration tier runs `TQQQ.NASDAQ` through the real engine and asserts whole-share fills.
- **Real-fills requirement.** Whole-share sizing is only observable through an actual run, so the integration tier reads `trader.generate_order_fills_report()` and asserts every `quantity` is a whole number. Monotonic bars (`_make_daily_bars`) produce no crossover fills, so a `_make_zigzag_bars` helper drives repeated crossovers. `SPY.ARCA` was **left on `_make_daily_bars`** to preserve the Story 5.1 `len == 10` assertion; a new plain-ETF ticker `IVV.ARCA` (zigzag) carries the fills proof, and `TQQQ.NASDAQ` (zigzag) the leveraged proof.
- **No persistence, no live data, no schema change.** All runs use `persist=False` with in-process bars; no Alembic migration, no `src/db/models/**` change (Story 5.4 owns persistence).

### File List

- `tests/unit/strategies/test_sma_crossover_position_sizing.py` (M) — `_daily_bars` helper + `TestEtfWholeShareSizingMatchesStocks` (ETF==Stock equivalence; real leveraged/inverse `Equity` size_precision=0 → whole-share binding).
- `tests/component/services/firstrate/test_catalog_backtest_loader.py` (M) — `_daily_bar_for` helper + `TestLeveragedInverseEtfWholeShareShape` (parametrized `build_equity` whole-share shape for SPY/TQQQ/SQQQ/BRK.B; leveraged ETF served via identical no-branch loader path).
- `tests/integration/core/test_backtest_catalog_integration.py` (M) — `_make_zigzag_bars` helper; `synthetic_catalog` adds `IVV.ARCA` + `TQQQ.NASDAQ`; `_build_sma_request` gains `start`/`end`; two new real-engine whole-share fills tests (plain ETF + leveraged ETF).
- `_bmad-output/implementation-artifacts/5-2-run-a-single-etf-backtest-with-whole-share-equity-sizing.md` (A) — this story.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (M) — 5-2 → review (epic-5 already in-progress).
