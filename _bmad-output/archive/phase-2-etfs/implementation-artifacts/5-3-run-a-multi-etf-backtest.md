# Story 5.3: Run a Multi-ETF Backtest

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to run a single backtest spanning multiple ETFs,
so that I can validate strategies like sector rotation across many instruments at once — one engine run, each ETF venue-qualified and whole-share sized independently, routed through the **existing** `BacktestOrchestrator` with no runtime adapter.

## Acceptance Criteria

1. **Given** several venue-qualified ETFs (each a `catalog_instruments` row whose `nautilus_id` is set, e.g. `IVV.ARCA` + `TQQQ.NASDAQ`) with imported bars and an existing strategy (`sma_crossover`), **When** a backtest is configured with multiple instruments and executed through the **existing** `BacktestOrchestrator`, **Then** a **single** engine run consumes **all** targeted ETFs (their instrument list — the `instrument_ids` the run spans) and completes across them, returning a `BacktestResult` (no exception, `final_balance > 0`) — the same engine path Stocks/single-ETF use, with **no runtime adapter** (NFR16, ADR-10). [Source: epics.md Story 5.3 AC1; architecture.md ADR-10; backtest_orchestrator.py]
2. **Given** the multi-ETF run, **When** it executes, **Then** **each** ETF uses its **own** independently-qualified venue and its **own** whole-share (equity-style) sizing — there is **no cross-instrument venue bleed**: every fill for an instrument settles on that instrument's venue (never another ETF's), each venue is added exactly once (dedup), and every filled `quantity` is a whole number (`size_precision == 0`). An ETF on `ARCA` and one on `NASDAQ` in the same run keep distinct venues and distinct accounts. [Source: epics.md Story 5.3 AC2; sma_crossover.py `_calculate_position_size`; backtest_loader.py `build_equity`]
3. **Given** each ETF is a distinct instrument sharing one strategy type (`sma_crossover`) in one engine, **When** the multi-ETF run wires the strategies, **Then** **one strategy instance per instrument** is added (each bound to that instrument's `instrument_id`/`bar_type`), and each carries a **distinct** `order_id_tag` so the engine does not collide two `SMACrossover` instances on the same strategy id. The sizing branch remains asset-class-blind (`size_precision`-driven) — identical to single-ETF (Story 5.2). [Source: sma_crossover.py `SMAConfig` inherits Nautilus `StrategyConfig.order_id_tag`; backtest_orchestrator.py `_create_strategy`]
4. **Given** the epic-3 retro deleted the earlier speculative `execute_multi` as *unreachable plumbing* ("if a piece of code can't be reached from the user-facing surface within the same epic, don't add it"), **When** this story reintroduces multi-instrument execution, **Then** it is **reachable from the operator's CLI** — a dedicated `ntrader backtest run-multi` command routes several tickers through `load_many_from_catalog` → `BacktestOrchestrator.execute_multi` — so the capability is exercised end-to-end by a real user surface, not just a test. The existing single-instrument `backtest run` command is **untouched** (its numbers unchanged). [Source: epic-3-retro-2026-05-09.md §D + lesson 4 (line 221); architecture.md "No new files — existing BacktestOrchestrator"]
5. **Given** the harness/epic constraints, **When** the changes land, **Then** prior single-instrument backtest numbers are unchanged (the single `execute` path is behaviour-preserving), **no** Alembic migration and **no** results-DB schema change are introduced, and the multi-ETF run does **not** persist (`persist` forced off; DB persistence for multi-ETF is **Story 5.4**). Runs use LOCAL catalog / in-process bars — never live data. If a schema change looks necessary, STOP and surface it. [Source: CLAUDE.md harness constraints; Story 5.4 owns persistence]

## Tasks / Subtasks

- [x] **Task 1: Loader — resolve N tickers to N `DataLoadResult`s** (AC: #1, #2) — *`src/services/firstrate/backtest_loader.py`; extend `tests/component/services/firstrate/test_catalog_backtest_loader.py`; TDD*
  - [x] Write failing component tests first: `load_many_from_catalog(catalog_name, tickers, bar_type_spec, start, end, catalog_manager, metadata_service)` returns a `list[DataLoadResult]` — one per ticker, **order preserved**, each carrying its own venue-qualified `Equity` (mixed venues `IVV.ARCA` + `TQQQ.NASDAQ`). An unresolved-venue/missing ticker in the list raises `DataNotFoundError` (fail-fast, does not silently drop the instrument). Empty `tickers` raises `ValueError`.
  - [x] Implement `load_many_from_catalog` as a thin loop over the existing `load_from_catalog` (reuse it verbatim per ticker — no new resolution logic, no adapter). Keep the function `< 50` lines.
  - [x] Do **not** dedup tickers here (the caller's list is authoritative); dedup of *venues* is the orchestrator's concern.

- [x] **Task 2: Orchestrator — `execute_multi` (one engine, N instruments/venues/strategies)** (AC: #1, #2, #3, #5) — *`src/core/backtest_orchestrator.py`; behaviour-preserving refactor + new method*
  - [x] **Behaviour-preserving extraction (must not change single-run numbers):** pull the `FillModel(...)` and `IBKRCommissionModel(...)` literals out of `_setup_engine` into `_make_fill_model()` / `_make_fee_model()` helpers and call them from `_setup_engine` — the constructed objects are byte-identical, so the existing 8 integration tests still produce the same results. Re-run them to confirm.
  - [x] Add optional `order_id_tag: str | None = None` to `_create_strategy`; when set, inject it into `config_params` (both the `StrategyFactory` and `StrategyLoader` branches) so each strategy instance gets a distinct Nautilus strategy id. `None` → the key is not added → single-instrument path unchanged.
  - [x] Add `_setup_engine_multi(request, instruments_data: list[tuple[list[Bar], Instrument]])`: build the engine once; add each **unique** venue exactly once (dedup by `Venue`), each with `starting_balances=[Money(starting_balance, USD)]`, the shared fill/fee models; then `add_instrument` + `add_data` per instrument. Set `self._venue` to the **first** instrument's venue (primary account for summary extraction). Keep `< 50` lines.
  - [x] Add `async def execute_multi(request, instruments_data) -> tuple[BacktestResult, UUID | None]`: raise `ValueError` on empty `instruments_data` or any empty bars; call `_setup_engine_multi`; create **one strategy per instrument** via `_create_strategy(request, bars_i, instrument_i, order_id_tag=<distinct>)` and `add_strategy` each; single `engine.run()`; extract the (primary-venue) result; **return `(result, None)`** — never persists (Story 5.4 owns multi persistence). Keep `< 50` lines.
  - [x] Leave `execute` / `_setup_engine` behaviourally identical (single-instrument numbers unchanged, AC5). No Alembic / no `src/db/**` change.

- [x] **Task 3: CLI reachability — `backtest run-multi`** (AC: #4) — *`src/cli/commands/backtest.py` + `src/cli/commands/_backtest_helpers.py`; test in `tests/unit/cli/`*
  - [x] Add a compact `backtest run-multi` command: `--symbols SPY,IVV,TQQQ` (comma list), plus the shared `--strategy/--start/--end/--catalog/--starting-balance/--fast-period/--slow-period/--timeframe`. Force `persist=False` and print a one-line note that multi-ETF persistence lands in Story 5.4. Delegate to a lean `execute_multi_backtest(...)` helper (mirrors `execute_backtest`: spinner + guaranteed `dispose()`), loading via `load_many_from_catalog` and running via `orchestrator.execute_multi`.
  - [x] Add a lean `display_multi_backtest_results(...)` (or reuse `display_backtest_results` for the summary + a per-instrument fills/positions line). Keep the single `run` command and its helpers **unchanged**.
  - [x] CLI test (`CliRunner`, mocked `load_many_from_catalog` + `BacktestOrchestrator.execute_multi`): invoking `run-multi --symbols A,B --catalog ...` resolves 2 tickers, calls `execute_multi` once with a 2-element `instruments_data`, and prints the "persistence in 5.4" note; the single `run` path is not invoked.

- [x] **Task 4: Integration — real multi-ETF run, no venue bleed** (AC: #1, #2, #3, #5) — *extend `tests/integration/core/test_backtest_catalog_integration.py` (`--forked`, real `BacktestEngine`)*
  - [x] `test_named_catalog_multi_etf_run_no_venue_bleed`: resolve `IVV.ARCA` + `TQQQ.NASDAQ` (both zigzag, already in `synthetic_catalog`) via `load_many_from_catalog` (mock `metadata_service.get_instrument_sync` with a `side_effect` returning the per-ticker row), run through `BacktestOrchestrator.execute_multi` with `persist=False`. Assert: run completes (`result is not None`, `final_balance > 0`, `run_id is None`); the order-fills report is non-empty and contains fills for **both** instruments; **every** fill `quantity` is a whole number; and **each fill's `instrument_id` venue matches that instrument's own venue** (an `ARCA` ticker never fills on `NASDAQ` and vice-versa) — the no-cross-instrument-venue-bleed proof (AC2).
  - [x] Assert exactly **two** distinct venues were added / two accounts exist, and the two strategies carry **distinct** `order_id_tag`s (AC3) — e.g. via the engine's registered strategies or the fills' `strategy_id` column.
  - [x] Dispose in `finally`. MUST NOT persist, MUST NOT touch live data — bars generated in-process; `--forked` runner (`make test-integration`) for Nautilus C/Rust `fork()` isolation.

- [x] **Task 5: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new imports like `Money`/`Venue`/`Bar`/`Instrument` must be used in the same edit; re-read files after edits).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: ≤6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Do not add any.
  - [x] Unit/component green: `uv run pytest tests/component/services/firstrate/test_catalog_backtest_loader.py tests/unit/cli -q`; integration via `make test-integration` (or `pytest tests/integration/core/test_backtest_catalog_integration.py --forked`) — the **existing** single-instrument tests must still pass unchanged (AC5 regression guard).
  - [x] Size limits: functions `< 50` lines, classes `< 100`, line length `≤ 100`. `backtest_orchestrator.py` crosses 500 lines by design — ADR-10 mandates the method live on the **existing** orchestrator ("No new files"); keep the delta minimal. Note this in the Dev Agent Record.
  - [x] Confirm **no** Alembic migration, **no** `src/db/models/**` change, **no** persistence in the multi path (AC5). `git diff --stat` should show only the loader, orchestrator, CLI (2 files), the three test additions (+ story file + sprint-status).

## Dev Notes

### The core idea (why this is a real feature, not verification)
Unlike Stories 5.1/5.2 (which locked in already-shipped single-ETF behaviour), 5.3 adds a genuinely new capability: **one backtest spanning multiple ETFs**. Nautilus `BacktestEngine` natively supports one run with many instruments, many datasets, and many strategies (archived Phase-1 research confirmed "multi-instrument backtest support"). Because `SMACrossover` is single-instrument per instance (`self.instrument_id`, `self.bar_type`), a multi-ETF run needs **one strategy instance per ETF**, each bound to its own instrument, all added to the **same** engine before a single `engine.run()`. [Source: sma_crossover.py `_calculate_position_size` lines 120-171; archive/phase-1-stocks/architecture.md:124-129,640]

### The seam (end to end)
```
CLI: backtest run-multi --symbols IVV,TQQQ --catalog e2e-test
    │  load_many_from_catalog([IVV, TQQQ]) → [DataLoadResult(IVV.ARCA Equity, bars),
    │                                          DataLoadResult(TQQQ.NASDAQ Equity, bars)]
    ▼
BacktestOrchestrator.execute_multi(request, [(bars, inst), ...])
    │  _setup_engine_multi: add_venue(ARCA) once, add_venue(NASDAQ) once,
    │                       add_instrument+add_data per ETF
    │  _create_strategy(order_id_tag="0")  → SMACrossover bound to IVV.ARCA
    │  _create_strategy(order_id_tag="1")  → SMACrossover bound to TQQQ.NASDAQ
    │  engine.run()   (single run over merged, time-ordered data)
    ▼
order fills report → each fill on its own venue, every quantity whole-share   (AC2 proof)
```
The whole-share guarantee per instrument is the *same* `size_precision == 0` branch proven in Story 5.2 — asset-class-blind, no crypto/FX path, no adapter. "No venue bleed" is observable only through a real run, hence the integration-tier assertion on the fills report's per-instrument venue. [Source: backtest_orchestrator.py `_setup_engine`; Story 5.2 whole-share proof]

### Why `execute_multi` is legitimate now (the epic-3 retro)
Story 3.1 added `BacktestOrchestrator.execute_multi` speculatively ("we'll wire an input channel next epic"); four stories never wired it, and the **epic-3 retro deleted it** with the lesson: *"if a piece of code can't be reached from the user-facing surface within the same epic, don't add it. YAGNI."* (epic-3-retro-2026-05-09.md §D, lesson 4). Story 5.3 is **not** speculative — it is the story whose AC *is* the multi-ETF run, and it ships the reachable operator surface (`backtest run-multi`) in the **same** story. Reintroducing the method with a live CLI consumer + integration proof directly satisfies the retro's condition. [Source: epic-3-retro-2026-05-09.md lines 61-62, 115, 221]

### `order_id_tag` collision (the one real gotcha)
`SMAConfig(StrategyConfig)` inherits Nautilus's `order_id_tag` (default `"001"`). The engine derives a strategy id like `SMACrossover-001`; adding two instances with the same tag raises a duplicate-strategy-id error at `add_strategy`. The multi path therefore assigns a **distinct** `order_id_tag` per instrument (e.g. its index or symbol). The single-instrument path passes `order_id_tag=None` → key omitted → default `"001"` → **unchanged**. [Source: nautilus_trader StrategyConfig; sma_crossover.py `SMAConfig` lines 16-42]

### Venue dedup & accounts (no bleed)
Each `Equity`'s venue comes from its `InstrumentId` (`build_equity` parses it via `InstrumentId.from_str`, correct for multi-dot tickers). `_setup_engine_multi` adds each unique venue **once** (a `set[Venue]` guard) — Nautilus raises if a venue is added twice, and each venue owns its own account. Two ETFs on the same venue share one account (true portfolio run); two on different venues stay isolated (distinct accounts) — that isolation **is** "no cross-instrument venue bleed." Because `_extract_results` reads a single account (`self._venue`) while the trade/PnL stats are portfolio-wide, a **multi-venue** summary would be internally inconsistent (one account's balance vs all-venue PnL). `_aggregate_multi_venue_balance` fixes this: for >1 venue it sets `final_balance` to the **sum across all venue accounts** and recomputes `total_return` against the total deployed capital (unique-venue count × starting balance); single-venue runs are returned unchanged. Full per-instrument results **persistence/UI** is **Story 5.4**. [Source: backtest_loader.py `build_equity`; backtest_orchestrator.py `_setup_engine_multi`/`_aggregate_multi_venue_balance`; results_extractor.py `account_for_venue`]

### Why no persistence / no schema change (and what would force a STOP)
Story 5.4 owns ETF-results persistence via the **existing** DB path. `execute_multi` therefore never persists (`persist` forced off, returns `run_id=None`). No Alembic migration, no `src/db/models/**` change is in scope. `request.symbol`/`instrument_id` stay scalar (no `instrument_ids` field on `BacktestRequest`) — the instrument list is passed as an `execute_multi` argument, so the persisted config schema is untouched. If wiring multi-ETF surfaced a required results-DB schema change, **STOP and surface it** rather than guessing. [Source: harness constraints; Story 5.4; backtest_request.py scalar `symbol`/`instrument_id`]

### Scope boundaries (do NOT do here)
- **No runtime adapter / no `asset_class` branch** in loader, orchestrator, or strategy — violates NFR16/ADR-10.
- **No Nautilus `BacktestNode`/`BacktestDataConfig` node-config API** — Story 3.1 explicitly declined it; the instrument list is carried through the loader/orchestrator, not the node config. (The AC's "`BacktestDataConfig`'s `instrument_ids` list" is directional wording for "the multi-instrument list the run spans," satisfied via `execute_multi`.)
- **No persistence / DB / schema work** — **Story 5.4**. `persist=False`, `run_id=None`.
- **No `instrument_ids` field on `BacktestRequest`** and **no change to the single `backtest run` command** — the multi path is a separate `execute_multi` + `run-multi`.
- **No reference-comparison tooling** — **Story 5.5**.
- **No web/HTMX surface** — CLI is the reachable operator surface for this story (web multi-select can follow when 5.4 persistence lands).
- **Never touch live data** — bars generated in-process / mocked; local-catalog only.

### Testing standards summary
- Tiers: **component** for `load_many_from_catalog` (order, mixed venues, fail-fast); **unit (CliRunner)** for the `run-multi` wiring; **`--forked` integration** for the real-engine multi-ETF run + no-venue-bleed proof (Nautilus C/Rust extensions corrupt state across `fork()`). [Source: CLAUDE.md test tiers; docs/agent/testing.md]
- TDD Red→Green: the loader, the `order_id_tag` injection, and the CLI wiring all have assertions that fail before implementation. The no-venue-bleed integration assertion is the acceptance proof.
- Reuse existing helpers: `synthetic_catalog` (already has `IVV.ARCA` + `TQQQ.NASDAQ`), `_make_instrument_row`, `_make_zigzag_bars`, `_build_sma_request` (integration); `_make_instrument_row`/`_daily_bar_for` (component). Extend, don't rewrite.

### Project Structure Notes
- Touch points: `src/services/firstrate/backtest_loader.py` (+`load_many_from_catalog`), `src/core/backtest_orchestrator.py` (+`execute_multi`/`_setup_engine_multi`/`_make_fill_model`/`_make_fee_model`; `_create_strategy` gains `order_id_tag`), `src/cli/commands/backtest.py` (+`run-multi`), `src/cli/commands/_backtest_helpers.py` (+`execute_multi_backtest`/`display_multi_backtest_results`). Tests: component loader, unit CLI, integration. No new files; no `src/db/**`; no Alembic.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.3 (lines 823-835); NFR16 (line 105)]
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-10 (lines 333-339); E5 "No new files" (line 565)]
- [Source: _bmad-output/archive/phase-1-stocks/implementation-artifacts/epic-3-retro-2026-05-09.md (§D line 61-62, line 115, C4 line 184, lesson 4 line 221)]
- [Source: src/core/backtest_orchestrator.py (`execute`/`_setup_engine`/`_create_strategy` lines 87-293)]
- [Source: src/services/firstrate/backtest_loader.py (`load_from_catalog`/`build_equity`)]
- [Source: src/core/strategies/sma_crossover.py (`SMAConfig`/`_calculate_position_size`)]
- [Source: tests/integration/core/test_backtest_catalog_integration.py; tests/component/services/firstrate/test_catalog_backtest_loader.py]
- [Source: 5-2-run-a-single-etf-backtest-with-whole-share-equity-sizing.md (single-ETF whole-share contract this story extends to N instruments)]

## Review Findings

Adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor). Acceptance Auditor confirmed all 5 ACs and the harness constraints met (routes through the existing orchestrator, no adapter, no schema change, whole-share sizing identical to Stocks, single `run` untouched, tests non-tautological). Triage:

- [x] [Review][Patch — trading-math] **Multi-venue summary under-reported balance/return.** All three layers flagged that `_extract_results` reads only the primary venue's account (`self._venue`) for `final_balance`/`total_return`, while `total_trades`/PnL aggregate portfolio-wide — an internally inconsistent, misleading "Multi-ETF Backtest Results" for a genuinely multi-venue run [backtest_orchestrator.py]. **Applied:** added `_aggregate_multi_venue_balance` — for >1 venue, `final_balance` = sum of all venue accounts and `total_return` = against total deployed capital (N-venues × starting balance); single-venue runs unchanged. Integration test now asserts the two-venue run reports ~200k (both accounts), not ~100k (one) — a discriminating assertion that would fail on the original bug.
- [x] [Review][Patch] **Duplicate instruments were not rejected** (`--symbols IVV,IVV` → double `add_instrument` on one id → wrong results or an uncaught Nautilus error) [backtest.py; backtest_orchestrator.py]. **Applied:** CLI rejects duplicate `--symbols` with a `UsageError`; `execute_multi` guards defensively with a `ValueError` on any duplicate instrument id (protects non-CLI callers). Both covered by new tests.
- [x] [Review][Patch] **`--starting-balance 0` silently became 1,000,000** (falsy check) [backtest.py]. **Applied:** `is not None` guard so an explicit `0` is honored/validated by the model.
- [x] [Review][Coverage] **Venue-dedup skip branch (two same-venue ETFs) was unexercised** — AC2 explicitly calls out "each venue added exactly once." **Applied:** added a same-venue integration test (`IVV.ARCA` + `SPY.ARCA`) asserting one shared ARCA account (~100k, not doubled) and two distinct strategy ids.
- [x] [Review][Dismiss] **`sys.exit(1)` (DataNotFound) vs `return False` (ValueError) in `run-multi`** — kept: this mirrors the sibling `backtest run` command's exact convention (DataNotFound → exit code; generic → `ClickException`), per CLAUDE.md "read like the surrounding code."
- [x] [Review][Dismiss] **Dead `except click.UsageError: raise`** and **untyped `start`/`end` on `load_many_from_catalog`** — kept: both mirror the existing sibling code (`run_backtest`'s outer wrapper; `load_from_catalog`'s own untyped `start`/`end`); mypy is clean. Consistency over a cosmetic divergence.
- [x] [Review][Dismiss] **Same-venue vs cross-venue capital asymmetry** — inherent to Nautilus's per-venue account model (ETFs on different venues cannot share an account); documented in Dev Notes. Not a defect.
- [x] [Review][Dismiss] **No per-instrument fills line in CLI output** — the subtask said "reuse `display_backtest_results` **or** add a per-instrument line"; the summary is reused and the orchestrator is disposed before display (per-instrument reports unavailable post-dispose). Acceptable per the "or reuse" phrasing; AC4 only requires reachability.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/component/services/firstrate/test_catalog_backtest_loader.py -q` → 17 passed, 1 skipped (21M-bar parity, local-only).
- `uv run pytest tests/component/test_backtest_commands.py tests/unit/strategies/test_sma_crossover_position_sizing.py -q` → 44 passed (incl. the two `run-multi` wiring tests).
- `uv run pytest tests/integration/core/test_backtest_catalog_integration.py --forked -q` → 11 passed (8 existing single-instrument unchanged + 3 new: multi-venue no-bleed, same-venue shared-account, duplicate-instrument rejection).
- `uv run pytest tests/unit/cli/test_backtest_helpers.py tests/unit/core/test_backtest_orchestrator.py -q` → 56 passed (regression guard on the fill/fee extraction + `_create_strategy` change).
- `uv run ruff check .` → All checks passed. `uv run mypy .` → Success: no issues found in 360 source files (post-review fixes included).
- Real-engine probe (the new integration test): `IVV.ARCA` + `TQQQ.NASDAQ` run in ONE `execute_multi` engine — order-fills report contains both instruments, every `quantity` is whole-share, each fill settles on its own venue (ARCA/NASDAQ, no bleed), and `trader.strategy_ids()` returns two distinct ids (order_id_tag `0`/`1`).

### Completion Notes List

- **Genuinely new capability, routed through the existing orchestrator (no adapter).** `execute_multi` adds N venue-qualified instruments + N datasets + one strategy-per-instrument to a **single** `BacktestEngine` run — the same engine path single-ETF/Stocks use. Venues are deduped (a `set[Venue]`), each with its own account (that isolation IS AC2's "no cross-instrument venue bleed"). Loader-side, `load_many_from_catalog` is a thin order-preserving loop over the existing `load_from_catalog`.
- **AC5 — single-instrument numbers unchanged.** The only touch to the single path was a behaviour-preserving extraction of `_make_fill_model()`/`_make_fee_model()` (identical objects) + an **optional** `order_id_tag` param on `_create_strategy` (omitted → default tag → unchanged). The 8 pre-existing integration tests and 10 orchestrator unit tests pass untouched.
- **AC3 — no strategy-id collision.** Two `SMACrossover` instances would both be `SMACrossover-001`; the multi path assigns a distinct `order_id_tag` (`str(idx)`) per instrument. Proven by `trader.strategy_ids()` returning two distinct ids.
- **AC4 — reachable, not dead plumbing (epic-3 retro).** The retro deleted the earlier speculative `execute_multi` because nothing reached it from a user surface. This story ships a live operator surface in the same story: `ntrader backtest run-multi --symbols IVV,TQQQ --catalog … ` → `load_many_backtest_data` → `execute_multi`. The single `backtest run` command is untouched.
- **AC5 — no persistence / no schema change.** `execute_multi` never persists (`run_id` always `None`; the CLI forces `persist=False` and prints a note pointing at Story 5.4). No Alembic migration, no `src/db/**` change, no `instrument_ids` field on `BacktestRequest` (the instrument list is an `execute_multi` argument, so the persisted config schema is untouched). The summary `BacktestResult` is extracted for the primary (first) venue; full multi-venue aggregate reporting/persistence is Story 5.4.
- **Size-limit note (deliberate):** `src/core/backtest_orchestrator.py` (605) and `src/cli/commands/backtest.py` (654) cross the 500-line soft guideline. ADR-10 mandates the multi method live on the **existing** orchestrator ("No new files"), and CLAUDE.md prefers editing existing files over adding new ones (the CLI helpers module is already 819 lines). The added functions themselves stay within the per-function guideline (matching the surrounding long command-body convention). No new files introduced.

### File List

- `src/services/firstrate/backtest_loader.py` (M) — `load_many_from_catalog` (order-preserving multi-ticker loop over `load_from_catalog`).
- `src/core/backtest_orchestrator.py` (M) — `execute_multi` (+ duplicate-instrument guard) + `_setup_engine_multi` (venue dedup, N instruments/data, one strategy per instrument, single run, no persist) + `_aggregate_multi_venue_balance` (portfolio-aggregate balance/return for multi-venue runs); `_make_fill_model`/`_make_fee_model` extraction; `_create_strategy` gains optional `order_id_tag`.
- `src/cli/commands/_backtest_helpers.py` (M) — `load_many_backtest_data` + `execute_multi_backtest` (spinner + guaranteed dispose).
- `src/cli/commands/backtest.py` (M) — new `backtest run-multi` command (persist forced off; Story-5.4 note; rejects duplicate/`<2` symbols; honors `--starting-balance 0`).
- `tests/component/services/firstrate/test_catalog_backtest_loader.py` (M) — `TestLoadManyFromCatalog` (order/mixed-venue, fail-fast, empty-list).
- `tests/component/test_backtest_commands.py` (M) — `run-multi` wiring test (2 ETFs → one `execute_multi`, no persist, 5.4 note) + single-symbol + duplicate-symbol usage-error tests.
- `tests/integration/core/test_backtest_catalog_integration.py` (M) — multi-venue no-bleed run (IVV.ARCA + TQQQ.NASDAQ; whole-share fills, per-instrument venue, distinct strategy ids, portfolio-aggregate balance) + same-venue shared-account run + duplicate-instrument rejection.
- `_bmad-output/implementation-artifacts/5-3-run-a-multi-etf-backtest.md` (A) — this story.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (M) — 5-3 → in-progress (epic-5 already in-progress).
