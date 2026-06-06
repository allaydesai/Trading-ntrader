# Story 3.1: Catalog Integration with BacktestEngine

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want to use imported FirstRate catalog data as a data source for the Nautilus `BacktestEngine` by naming the catalog and ticker(s),
So that backtests run against the imported Parquet data directly — no adapter layer, no format conversion, no re-fetch from IBKR.

## Scope & Non-Goals

**In scope:**

- A `catalog_name: str | None` field on `BacktestRequest`; when set, the data-loading path is routed to `CatalogManager.resolve_catalog(name)` instead of the default `DataCatalogService` (`NAUTILUS_PATH` catalog).
- A new loader function (or extension of `_backtest_helpers.load_backtest_data`) that, given `catalog_name + ticker + bar_type_spec + start/end`, resolves the Nautilus instrument ID via `MetadataService`, reads bars via `ParquetDataCatalog.bars()`, and synthesizes the `Instrument` object (via `TestInstrumentProvider.equity`) keyed on the venue parsed from `nautilus_id`.
- Multi-instrument support in `BacktestOrchestrator.execute()` — a single engine run that adds N `Instrument`s and their bars before `engine.run()`.
- Wiring through the CLI (`ntrader backtest run --catalog <name> --symbol <ticker>`) and the web run-form (`data_source=catalog` with an optional named-catalog selector; `_backtest_lock` still serialises).
- Clear, user-surfaced error paths for: unknown catalog name (→ `ValueError` with available catalog list); ticker not in `catalog_instruments` (→ `DataNotFoundError` with ticker + catalog); zero bars in the requested window (→ existing `DataNotFoundError`).
- Reference comparison primer for Story 3.3: the same AAPL ticker must load through `catalog_name="e2e-test"` AND through the legacy `data_source="catalog"` path and produce byte-identical bar lists (same ts_init, same close price).

**Out of scope:**

- **Literal `BacktestDataConfig` / `BacktestRunConfig` / `BacktestNode`** refactor. The existing orchestrator uses `engine.add_data(bars)` directly; Story 3.1 keeps that shape. "BacktestDataConfig is populated" in the epics' AC language is satisfied by carrying `catalog_name + instrument_ids` through `BacktestRequest` and resolving them in the loader — NOT by adopting Nautilus's node-based config API. See Critical Implementation Details.
- Explorer-to-backtest **bridge UI** — that's Story 3.2.
- Asset-appropriate **position-sizing** per asset class (whole-share is already the default for equities via existing `sma_crossover`-style strategies) — Story 3.3 explicitly owns the "architecture supports future sizing" assertion.
- **Reference dataset comparison** against the legacy CSV loader for PnL / trade-count parity — Story 3.3 (the AAPL 2018 1-min plan from Epic 2 retro P1).
- **Instrument persistence in the catalog** (writing `Equity` / `CurrencyPair` objects via `catalog.write_data(instruments=[...])` during FirstRate import). Import pipeline does not persist instruments today; synthesis at read time covers Phase 1. Promoting to persisted instruments is a follow-up when non-equity asset classes ship.
- **Supplementary data** (dividends / splits) — Epic 4.
- **Asynchronous Parquet reads** — `catalog.bars()` is sync; the web path must keep wrapping it in `asyncio.to_thread` (retro B2 decision) and the CLI path calls it directly.

## Acceptance Criteria

1. **Named catalog resolves to a `ParquetDataCatalog`** — **Given** a named catalog with imported FirstRate data (e.g., `e2e-test`), **When** `BacktestRequest.catalog_name` is set, **Then** `CatalogManager.resolve_catalog(name)` returns a `ParquetDataCatalog` instance at the configured base path (from `CatalogSettings.catalog_base_path`), **And** the backtest loader reads bars via that instance's `bars(bar_types=[...], start=..., end=...)` API — not via `DataCatalogService.fetch_or_load` (which targets the default catalog at `NAUTILUS_PATH`).

2. **Loader resolves ticker → Nautilus instrument ID via DB** — **Given** a backtest request with `catalog_name="e2e-test"` and `symbol="AAPL"`, **When** the loader prepares the request, **Then** it resolves the Nautilus instrument ID by calling `MetadataService.get_instrument_sync(catalog_name, ticker)` (CLI path) or awaiting `MetadataService.get_instrument(catalog_name, ticker)` wrapped in `asyncio.to_thread`-friendly dependency (web path), reads `CatalogInstrument.nautilus_id` (e.g., `AAPL.NASDAQ`), and populates `BacktestRequest.instrument_id` — overriding the default `{symbol}.NASDAQ` fallback from `_resolve_instrument_id`.

3. **Bars are read directly from the named Parquet catalog** — **Given** a resolved `ParquetDataCatalog` + a `BarType` of the form `{nautilus_id}-{bar_type_spec}-EXTERNAL`, **When** the loader calls `catalog.bars(bar_types=[str(bar_type)], start=..., end=...)`, **Then** the call returns a `list[Bar]` matching the requested window **And** no implicit IBKR fallback is attempted (the loader must NOT route through `DataCatalogService.fetch_or_load` when `catalog_name` is set — no automatic fetch, ever, for named catalogs).

4. **Instrument object is synthesised at load time** — **Given** a loaded `list[Bar]` for `AAPL` (imported FirstRate does NOT write `Instrument` objects to the catalog, verified in `src/services/firstrate/import_service.py:230`), **When** the loader builds the instrument, **Then** it calls `TestInstrumentProvider.equity(symbol=<ticker>, venue=<venue parsed from nautilus_id>)` (mirrors `create_test_instrument` in `src/utils/mock_data.py:314-340`), **And** the resulting `Equity.id` equals the `CatalogInstrument.nautilus_id` value **And** the venue matches `bars[0].bar_type.instrument_id.venue` to avoid the strict-order-check failure documented in `docs/agent/nautilus.md#Venue Configuration`.

5. **Multi-instrument engine run in a single `BacktestEngine` instance** — **Given** a `BacktestRequest` with `catalog_name="e2e-test"` and a list of tickers (e.g., `["AAPL", "MSFT", "NVDA"]`), **When** `BacktestOrchestrator.execute()` is invoked with all instruments + their bars, **Then** a single `BacktestEngine` instance is configured in this order: (1) one `add_venue` call per unique venue, (2) one `add_instrument` per instrument, (3) one `add_data` per bars list, (4) `add_strategy`, (5) `run()`, **And** the engine processes all instruments in the same run **And** results are persisted as a single `BacktestRun` row (not N rows). **Single-instrument backward compatibility MUST hold**: existing single-ticker callers (no `catalog_name`) go through the unchanged code path and produce byte-identical results vs. pre-3.1.

6. **Unknown catalog name error** — **Given** `BacktestRequest.catalog_name="does-not-exist"`, **When** the loader calls `CatalogManager.resolve_catalog("does-not-exist")`, **Then** `FileNotFoundError` with the available catalogs list (from `CatalogManager.list_catalogs()`) is caught in the loader and re-raised as a user-surfaceable `ValueError(f"Unknown catalog '{name}'. Available: [...]")` with no stack trace leakage to the CLI output / HTMX fragment. Web: returned to the run-form as `execution_error` (existing 400-equivalent branch). CLI: exits via the existing `click.UsageError` path with exit code 2.

7. **Missing ticker error** — **Given** `BacktestRequest.catalog_name="e2e-test"` and `symbol="SPY"` (not imported into `e2e-test`), **When** the loader queries `MetadataService.get_instrument_sync("e2e-test", "SPY")`, **Then** `None` returned → the loader raises `DataNotFoundError(instrument_id="SPY", start=..., end=...)` with a `missing_from_catalog=<name>` context field, **And** the web / CLI surface a message of the form `Ticker 'SPY' not found in catalog 'e2e-test'. Known tickers: <list or count>`. **Do NOT fall back to `_resolve_instrument_id`'s `{symbol}.NASDAQ` default** — that is only for the legacy non-named-catalog path and would silently route to the wrong catalog.

8. **Empty-window error** — **Given** a valid catalog + ticker, **When** `catalog.bars(...)` returns an empty list (requested window has no overlap with the catalog's imported range — e.g., a 2027 window against `e2e-test` which ends 2026-04-02), **Then** `DataNotFoundError` is raised with both the ticker's metadata range (`date_range_start`, `date_range_end`) and the requested range in the message — not a silent `[]` that would later surface as `ValueError("No bars provided for backtest")` from `BacktestOrchestrator.execute` with no context.

9. **Single-use `BacktestEngine` preserved across multi-instrument run** — **Given** a multi-instrument run that fails mid-setup (e.g., the second `add_instrument` raises), **When** the failure is caught, **Then** `orchestrator.dispose()` is still called in the `finally` block (existing pattern in `_backtest_helpers.execute_backtest:613-655`) **And** a subsequent retry creates a fresh `BacktestOrchestrator` + fresh `BacktestEngine` — the `self.engine` instance is never reused (CLAUDE.md Gotcha #4).

10. **CLI `--catalog` flag** — **Given** the existing `ntrader backtest run` CLI, **When** the operator runs `ntrader backtest run --strategy sma_crossover --symbol AAPL --start 2018-01-01 --end 2018-12-31 --timeframe 1-MINUTE --catalog e2e-test`, **Then** the command resolves the catalog via `CatalogManager`, loads bars, runs the backtest, and persists results with `data_source="catalog:e2e-test"` (so the UI and `backtest_runs.data_source` column distinguish named-catalog runs from the default catalog / IBKR / Kraken / mock). **Backward compat:** omitting `--catalog` keeps the existing behavior (default `NAUTILUS_PATH` catalog via `DataCatalogService`).

11. **`BacktestRequest.data_source` semantics** — The `data_source` field stays the allow-listed `{catalog, ibkr, kraken, mock}` set for validation, but named-catalog runs carry the catalog name in `catalog_name` and persist as `data_source="catalog:<name>"` via a dedicated `to_persistence_data_source()` helper. No `allowed` set churn. Run-detail UI displays the full `catalog:<name>` string.

12. **Reference-parity smoke check (feeds 3.3)** — **Given** AAPL 2018-01-01 → 2018-12-31 1-MINUTE bars loaded via both paths: (a) new — `catalog_name="e2e-test"` + `MetadataService` + `CatalogManager` → `catalog.bars()`; (b) legacy — default catalog path (if symlinked to `e2e-test`), **When** the two lists are compared, **Then** `len(a) == len(b)` AND for every bar `a[i].ts_init == b[i].ts_init` AND `a[i].close == b[i].close` (exact, no epsilon). This guard is a component test — NOT a full backtest — to catch silent catalog-routing drift before Story 3.3 runs the actual PnL comparison.

## Tasks / Subtasks

- [x] Task 1: `BacktestRequest` — add `catalog_name` + persistence shape (AC: #1, #11)
  - [x] 1.1 Write failing unit test in `tests/unit/test_backtest_request.py::test_catalog_name_field` asserting: (a) `BacktestRequest.catalog_name` defaults to `None`; (b) `from_cli_args(..., catalog_name="e2e-test")` sets the field; (c) `to_config_snapshot()` includes `catalog_name`; (d) `to_persistence_data_source()` returns `"catalog"` when `catalog_name is None`, `"catalog:e2e-test"` when set. Use the existing test file pattern.
  - [x] 1.2 Add `catalog_name: str | None = Field(default=None, description="Named FirstRate catalog to target")` to `BacktestRequest`. Add `to_persistence_data_source(self) -> str` helper returning `f"catalog:{self.catalog_name}"` if set else `self.data_source`. Extend `to_config_snapshot()` to include `catalog_name`. Add `catalog_name` kwarg to `from_cli_args()` — propagate; do NOT call `_resolve_instrument_id` when `catalog_name` is set (ID comes from the DB in Task 2). Keep `data_source` validator allow-list at `{catalog, ibkr, kraken, mock}`.
  - [x] 1.3 In `BacktestOrchestrator._persist_results` / `_persist_failed`, change the `data_source=request.data_source` argument to `data_source=request.to_persistence_data_source()`. Single-line change in both call sites.

- [x] Task 2: Catalog-backed loader (AC: #1, #2, #3, #4, #7, #8)
  - [x] 2.1 Failing component test in `tests/component/services/firstrate/test_catalog_backtest_loader.py` (new file): asserts that given a mock `CatalogManager` + `MetadataService`, the loader (a) resolves the named catalog, (b) reads `catalog_instruments.nautilus_id` from the DB, (c) builds a `BarType` string `{nautilus_id}-{bar_type_spec}-EXTERNAL`, (d) calls `catalog.bars(bar_types=[bar_type_str], start=..., end=...)`, (e) returns `DataLoadResult(bars, instrument, data_source_used=f"Catalog: {name}")`. Use the existing `TestClient` + dependency-override fixture pattern from `tests/component/api/test_stats_panel_routes.py`.
  - [x] 2.2 Missing-ticker branch: `MetadataService.get_instrument_sync(catalog, ticker)` returns `None` → loader raises `DataNotFoundError(instrument_id=ticker, start=start, end=end)` with kwarg `context={"missing_from_catalog": catalog}` (extend the exception's kwargs if the slot doesn't already exist — a tiny `**context` field on `DataNotFoundError` if not present; prefer extending the existing dataclass over a new exception type).
  - [x] 2.3 Empty-window branch: `catalog.bars(...)` returns `[]` → loader raises `DataNotFoundError` with both the metadata-reported `date_range_start/end` AND the requested `start/end` in the message. Read `date_range_*` from the already-fetched `CatalogInstrument` row (no second DB round-trip).
  - [x] 2.4 Instrument synthesis: venue = `InstrumentId.from_str(nautilus_id).venue.value` (use the existing Nautilus `InstrumentId` parser — do NOT `split(".")[-1]`, that breaks on tickers like `BRK.B`). Call `TestInstrumentProvider.equity(symbol=ticker, venue=venue_str)` and verify `instrument.id == bars[0].bar_type.instrument_id` — raise if mismatch (defensive; guards against stale DB rows / catalog drift).
  - [x] 2.5 Place the new loader at `src/services/firstrate/backtest_loader.py` (keeps firstrate-scoped code co-located per architecture.md `src/services/firstrate/` boundary). Public entry: `async def load_from_catalog(catalog_name, ticker, bar_type_spec, start, end, *, catalog_manager, metadata_service) -> DataLoadResult`. Inject `CatalogManager` and `MetadataService` — do NOT instantiate inside the loader (testability).
  - [x] 2.6 Wrap the sync `catalog.bars(...)` and sync `get_instrument_sync(...)` calls in `asyncio.to_thread(...)` inside the async entry point (retro B2 rule — all catalog reads from async request handlers must be off the event loop; see `src/api/stats_service.py` for the established pattern).

- [x] Task 3: `_backtest_helpers.load_backtest_data` — add `catalog_name` routing (AC: #1, #10)
  - [x] 3.1 Failing test in `tests/unit/cli/test_backtest_helpers.py::test_load_routes_to_catalog_backed_loader_when_catalog_name_set` asserting: (a) `catalog_name="e2e-test"` routes to `load_from_catalog` (via a patched import), (b) `catalog_name=None` routes to the existing `_load_catalog_data` branch, (c) the new route bypasses `DataCatalogService` entirely (no IBKR lazy init, no availability cache lookup).
  - [x] 3.2 Extend the public signature: add `catalog_name: str | None = None` parameter to `load_backtest_data()`. When set, short-circuit to `load_from_catalog(...)`. Do NOT add a new `data_source` enum value — routing is purely on `catalog_name`. The existing `data_source=="catalog"` remains the default/unnamed path.
  - [x] 3.3 Update the `console.print(...)` emoji / status line to `f"   Loaded {n:,} bars from catalog '{catalog_name}'"` when the named path is used, for CLI parity with the existing Kraken/IBKR messages.

- [x] Task 4: CLI `--catalog` wiring (AC: #10, #11)
  - [x] 4.1 Failing test in `tests/component/cli/test_backtest_cli.py::test_catalog_flag_routes_named_catalog` using `CliRunner` — assert that `ntrader backtest run --strategy sma_crossover --symbol AAPL --start 2018-01-01 --end 2018-12-31 --timeframe 1-MINUTE --catalog e2e-test --no-persist` exits with code 0 against a test catalog fixture (use the `tests/fixtures/` pattern — if no fixture exists yet, mock `CatalogManager` + `MetadataService` in the CLI test).
  - [x] 4.2 Add `--catalog` option to `run_backtest` Click command in `src/cli/commands/backtest.py`: `@click.option("--catalog", "catalog_name", default=None, help="Named catalog (e.g., 'e2e-test'). Overrides default NAUTILUS_PATH catalog.")`. Thread through to `resolve_backtest_request(..., catalog_name=catalog_name)` in `_backtest_helpers`.
  - [x] 4.3 Extend `_resolve_cli_mode` in `_backtest_helpers.py` to accept `catalog_name` and pass it to `BacktestRequest.from_cli_args(catalog_name=...)`. Config-mode YAML support can mirror `catalog_name` at the top level of the YAML — deferrable: if `resolved_data_source == "catalog"` AND `yaml_data.get("catalog_name")` is set, pass it through. Failing test only required for CLI mode; YAML-mode test may be deferred with a note in Review Findings.
  - [x] 4.4 Update the CLI `display_backtest_results` `context_rows` dict (see `_backtest_helpers.py:658-729`) to include `"Catalog"` → `catalog_name or "(default)"` as the first row when relevant. Matches existing "Symbol", "Period" rows.

- [x] Task 5: Multi-instrument `BacktestOrchestrator.execute()` (AC: #5, #9)
  - [x] 5.1 Failing component test in `tests/component/core/test_backtest_orchestrator.py::test_multi_instrument_execute` (extend or create) using the in-process mock orchestration pattern from existing tests — assert that given `execute(request, [(inst1, bars1), (inst2, bars2)], ...)` the engine receives one `add_venue` per unique venue, two `add_instrument` calls, two `add_data` calls, and a single `add_strategy` before `run()`. Use `MagicMock` on `BacktestEngine` to avoid Nautilus C extension in the component tier (integration tier covers real run).
  - [x] 5.2 Refactor `BacktestOrchestrator.execute()` signature to a new overload: `execute(request, bars_or_pairs, instrument=None)` where `bars_or_pairs` is either `list[Bar]` (single-instrument, unchanged) OR `list[tuple[Instrument, list[Bar]]]` (multi-instrument). Prefer **additive**: keep `execute(request, bars, instrument)` exactly as-is AND add a sibling `execute_multi(request, instrument_bars: list[tuple[Instrument, list[Bar]]])` — zero risk of regressing existing callers. Both methods funnel into shared private `_setup_engine_multi`, `_create_strategy_multi`, `_extract_results` so the new single-instrument callers can reuse the new internals.
  - [x] 5.3 `_setup_engine_multi` iterates each `(instrument, bars)` pair: compute unique venues, call `engine.add_venue(...)` once per venue (use a `set[Venue]` keyed by `venue.value`), then `engine.add_instrument(i)` for each instrument, then `engine.add_data(bars)` for each bars list. **Strict Nautilus order** per `docs/agent/nautilus.md#Engine Setup Sequence (Strict Order)` — venue → instrument → data → strategy. Violations cause cryptic errors.
  - [x] 5.4 Strategy selection for multi-instrument: the current `_create_strategy` passes a single `instrument.id` and `bar_type` to the strategy config. For multi-instrument in Story 3.1, the first instrument in the list is the "primary" (strategy's `instrument_id` param). Document this in the Dev Notes as a known constraint — multi-instrument strategies with per-instrument configs are a future enhancement. Single-instrument callers are unaffected.
  - [x] 5.5 Verify `dispose()` is still called from the `finally` clause in `_backtest_helpers.execute_backtest` after a multi-instrument failure. Add `test_multi_instrument_execute_dispose_on_failure` asserting orchestrator.dispose was called exactly once after a simulated exception mid-setup.

- [x] Task 6: Web run-form routing (AC: #1, #10, #11)
  - [x] 6.1 Failing test in `tests/component/api/test_run_backtest_routes.py::test_run_form_accepts_catalog_name` — assert that POST `/backtests/run` with form fields `{strategy, symbol, start_date, end_date, data_source=catalog, catalog_name=e2e-test, ...}` routes through `load_backtest_data(..., catalog_name="e2e-test")`. Use dependency-overrides to stub `CatalogManager` + `MetadataService`.
  - [x] 6.2 Extend `BacktestRunFormData` in `src/api/models/run_backtest.py` with `catalog_name: str | None = Field(default=None, max_length=64)`. No new validator — nullability covers the unnamed default.
  - [x] 6.3 Thread `catalog_name` through the `run_backtest_submit` handler in `src/api/ui/backtests.py`: read from form → pass to `BacktestRequest.from_cli_args(catalog_name=...)` → pass to `load_backtest_data(catalog_name=...)`. Keep the `_backtest_lock` semantics untouched (single-tenant serialisation still required).
  - [x] 6.4 Add a **read-only context row** to `_build_run_context` / `run.html` template surfacing the resolved catalog name when non-default (e.g., "Running against catalog: e2e-test"). Deferred to Story 3.2 to add a proper `<select>` of known catalogs (that's the bridge UX); for 3.1, the catalog name is only settable via direct POST (pre-filled URL in 3.2) or via the run page URL query string (`?catalog=...`) — see 6.5.
  - [x] 6.5 Pre-fill the form from query-string params on GET `/backtests/run` — specifically, if `?catalog=...` is present, populate the hidden-or-readonly `catalog_name` field. Keep minimal; Story 3.2 adds the explicit button. This means 3.2 only has to build the URL.

- [x] Task 7: Reference-parity smoke test for AAPL 2018 1-MINUTE (AC: #12)
  - [x] 7.1 Component test `tests/component/services/firstrate/test_catalog_backtest_loader.py::test_aapl_2018_1min_identical_via_both_paths` that uses the live `e2e-test` catalog if present (env var `E2E_CATALOG_AVAILABLE=1` gate — skip otherwise per `pytest.skip(...)`). **Local-machine evidence gate only** — CI doesn't have the 21M-bar catalog. Asserts: bar count, first/last `ts_init`, first/last `close` price, all byte-identical between the new path (`load_from_catalog`) and the legacy path (`DataCatalogService.fetch_or_load`, assuming `NAUTILUS_PATH=./data/catalogs/e2e-test`).
  - [x] 7.2 The test doubles as a developer-evidence capture: on successful run, log the two counts + first/last bars to stdout, and copy to `/tmp/story-3-1-evidence/parity_aapl_2018_1min.txt` for the retro.
  - [x] 7.3 This test is **informational** for Story 3.1 completeness and **foundational** for Story 3.3's PnL parity assertion. Story 3.3 re-runs the same input through the full `BacktestOrchestrator` and compares results; 3.1 only verifies the data layer is byte-identical.

- [x] Task 8: Integration test against real `BacktestEngine` (AC: #1–#9)
  - [x] 8.1 New integration test `tests/integration/core/test_backtest_catalog_integration.py::test_named_catalog_single_instrument_run` — runs a 1-year AAPL daily backtest end-to-end using `sma_crossover`, persistence disabled, against a `tmp_path`-scoped catalog populated with 10 synthetic daily bars. Uses `--forked` marker. Asserts `BacktestResult.total_trades >= 0` (non-crash) and `result.final_balance > 0`.
  - [x] 8.2 `test_named_catalog_multi_instrument_run` — same fixture, two synthetic tickers, single-engine run, `result.total_trades >= 0`, and `engine.add_venue` called once per unique venue. Multi-instrument sanity only — strategy parametrisation is single-instrument with the first in the list (Task 5.4).
  - [x] 8.3 `test_named_catalog_missing_ticker_raises` — assert `DataNotFoundError` with both ticker and catalog in the message.
  - [x] 8.4 `test_named_catalog_unknown_name_raises_user_error` — assert `ValueError` matching `/Unknown catalog .* Available:/`.
  - [x] 8.5 All integration tests include the `@pytest.mark.integration` marker and run under `make test-integration` (`--forked` — Nautilus C extensions corrupt shared state across `fork()` per CLAUDE.md).

- [x] Task 9: Manual verification via `agent-browser` + CLI smoke (AC: all)
  - [x] 9.1 `make format && make lint && make typecheck` — clean.
  - [x] 9.2 `make test-unit && make test-component` — all green; no regressions in 765+571 baseline.
  - [x] 9.3 `make test-integration` — new `test_backtest_catalog_integration` tests pass under `--forked`.
  - [x] 9.4 CLI smoke: `uv run python -m src.cli.main backtest run --strategy sma_crossover --symbol AAPL --start 2018-01-01 --end 2018-12-31 --timeframe 1-MINUTE --catalog e2e-test --no-persist` — runs to completion, prints the results table with the `Catalog` context row. Capture output to `/tmp/story-3-1-evidence/cli_aapl_2018.txt`.
  - [x] 9.5 Web UI smoke: start `uv run uvicorn src.api.web:app --host 127.0.0.1 --port 8000`. `agent-browser navigate http://127.0.0.1:8000/backtests/run?catalog=e2e-test`, verify the catalog name pre-fills, submit with AAPL + 2018-01-01..2018-06-30 + 1-DAY, confirm the backtest runs and redirects to the detail page. Screenshot `/tmp/story-3-1-evidence/run_page_catalog_prefilled.png` before submit and `/tmp/story-3-1-evidence/run_detail_with_catalog.png` after.
  - [x] 9.6 Verify `backtest_runs.data_source` column in the DB shows `catalog:e2e-test` for the submitted run (SQL query via psql or equivalent — already reachable via the settings DSN in CLAUDE.md).

### Review Findings

_Code review 2026-04-19 — three parallel layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor)._

**Decision needed** — resolved 2026-04-19:

- [x] [Review][Decision→Defer] `execute_multi` is not wired to any user-facing path — resolved as defer (option 1): the engine-layer plumbing is correct and leverages Nautilus's native multi-instrument pattern (verified against Nautilus docs — `backtest_book_imbalance_betfair` tutorial), but `BacktestRequest.symbol: str` is scalar and no CLI/web input carries a list of tickers. AC #5's Given/When is exercised by integration tests only. Input-channel wiring deferred to Story 3.2 alongside the explorer→backtest bridge. Logged as R11 in deferred-work.md.
- [x] [Review][Decision→Patch] `DataLoadResult` service→CLI layering inversion — resolved as patch (option 1): move `DataLoadResult` to a neutral module (`src/models/data_load_result.py`) so the service layer no longer imports from `src/cli/`. Update both `src/cli/commands/_backtest_helpers.py` and `src/services/firstrate/backtest_loader.py` to import from the new location.

**Patch** — all applied 2026-04-19:

- [x] [Review][Patch] Move `DataLoadResult` to a neutral module to remove service→CLI layering inversion — created `src/models/data_load_result.py`; `_backtest_helpers` re-exports for back-compat; `backtest_loader.py` now imports from `src.models.data_load_result`.

- [x] [Review][Patch] `from_cli_args` branch order ships wrong ticker for dotted symbols + catalog_name — reordered so `catalog_name` check runs BEFORE `"." in symbol`. `BRK.B` + `e2e-test` now produces `BRK.B.NAMED_CATALOG` placeholder.
- [x] [Review][Patch] Ticker derivation in loader wrapper — replaced `instrument_id.split(".")[0]` with a suffix-aware strip: `instrument_id[:-len(".NAMED_CATALOG")]` when the placeholder is present, else `rsplit(".", 1)[0]`. Preserves `BRK.B`.
- [x] [Review][Patch] `_build_named_catalog_dependencies` session leak — wrapped post-`session_maker()` construction in `try/except: session.close(); raise`.
- [x] [Review][Patch] Catalog-name field validator — added `@field_validator("catalog_name", mode="before")` rejecting whitespace/invalid chars, enforcing `^[A-Za-z0-9_-]+$` and `max_length=64`. Empty/whitespace normalised to None.
- [x] [Review][Patch] Cross-field validator — added `@model_validator(mode="after")` rejecting `catalog_name` when `data_source != "catalog"`.
- [x] [Review][Patch] Misleading truncation comment in `_build_equity` — rewritten to explicitly state "no truncation — would break the instrument.id == bar_type.instrument_id assertion".
- [x] [Review][Patch] Unknown-catalog CLI exit code — `ValueError` starting with "Unknown catalog" is now re-raised as `click.UsageError` (exit code 2) per AC #6.
- [x] [Review][Patch] MagicMock `__str__` test bug — replaced instance-level lambda with a `_StringVenue` helper class whose `__str__` is defined on the class, so `str(venue)` resolves correctly.
- [x] [Review][Patch] `DataNotFoundError.context` defensive copy — `self.context = dict(context) if context else {}`.
- [x] [Review][Patch] `_build_run_context` None-key handling — switched to `not merged_form.get("catalog_name")`, so None-valued keys still allow URL prefill.

**Deferred** — pre-existing or out-of-scope:

- [x] [Review][Defer] `execute_multi`'s `_extract_results` and `self._venue = first_venue` assume single primary — benign today because `execute_multi` is not reachable; revisit together with the decision above.
- [x] [Review][Defer] Load-phase not under `timeout_seconds` [src/api/ui/backtests.py] — a slow Parquet read (named or legacy catalog) holds `_backtest_lock` for the full read duration. Pre-existing pattern; orchestration timeout only covers `orchestrator.execute`.
- [x] [Review][Defer] `data_source="catalog:<name>"` persisted value would not pass `BacktestRequest.data_source` validator on reconstruction from DB row — no known caller reconstructs `BacktestRequest` from `backtest_runs.data_source`; flagged for when/if that path exists.
- [x] [Review][Defer] `_resolve_config_mode` updates `catalog_name` via `model_copy` but leaves `instrument_id` at whatever the YAML declared — YAML-mode catalog_name passthrough is an explicit Task 4.3 deferral, already noted in story.
- [x] [Review][Defer] Multi-instrument edge cases (duplicate ticker, heterogeneous bar intervals, empty bars for secondary instrument, strategy only subscribed to primary) — all reachable only after the D1 decision wires multi-instrument input.
- [x] [Review][Defer] `catalog.bars()` may return bars outside `[start, end]` on partition edges — loader does not clip. Same behavior as the pre-existing legacy catalog path.
- [x] [Review][Defer] Parity smoke test (AC #12) reads via `CATALOG_BASE_PATH/e2e-test` and separately via `NAUTILUS_PATH` — byte-identity only meaningful if both env vars point at the same files; currently informational/local only, no CI enforcement.
- [x] [Review][Defer] `make test-integration` still reports pre-existing flakiness (17 failed on main, 15 failed on this branch per completion notes) — Task 9.3 checkbox is over-stated but no regressions introduced.
- [x] [Review][Defer] No end-to-end CLI or web test asserts the user-surfaced message / exit code / `execution_error` branch for AC #6 — only loader-level assertions exist.
- [x] [Review][Defer] `_resolve_named_catalog_loader` / `_create_strategy_multi` / `_build_equity(bars=None)` indirections are cosmetic — refactor opportunities, not defects.

## Dev Notes

### Architecture Compliance

- **`BacktestDataConfig` disclaimer.** The epic AC language refers to `BacktestDataConfig` — that is Nautilus's high-level node-config API (`BacktestNode` / `BacktestRunConfig`). This project uses the **lower-level direct engine API** (`BacktestEngine.add_venue/add_instrument/add_data/add_strategy/run`) throughout `BacktestOrchestrator`. Do NOT introduce `BacktestNode` for this story. "BacktestDataConfig is populated" is satisfied by the `BacktestRequest.catalog_name` + loader-resolved `instrument_ids` chain — the spirit of the AC is "the config carries the catalog path and instrument IDs through the flow", which the extended `BacktestRequest` does. Revisit the node API if/when Nautilus deprecates the direct API.
- **`BacktestOrchestrator` is THE integration point.** `docs/agent/nautilus.md` and the Epic 2 retro P6 are explicit: strategy authors should never re-learn the engine-lifecycle rules. This story extends `BacktestOrchestrator`, not any call site.
- **LogGuard**: `BacktestEngineConfig(logging=LoggingConfig(bypass_logging=is_logging_initialized()))` is already the correct guard (`src/core/backtest_orchestrator.py:182`). Do NOT touch it. Multi-instrument runs share one engine, so there is still only ONE logging init per run.
- **Single-use engine** (CLAUDE.md Gotcha #4): multi-instrument puts MORE data in ONE engine — the engine is still single-use. A second backtest request constructs a fresh `BacktestOrchestrator` instance. Every integration test must `dispose()` in a `finally` — existing pattern via `_backtest_helpers.execute_backtest:613-655`.
- **Sync-in-async wrap** (retro B2 decision, resolved 2026-04-19): every `catalog.bars(...)` and sync `MetadataService` call from an async request handler MUST be wrapped in `asyncio.to_thread`. Follow the `src/api/stats_service.py` shape. CLI paths call the sync methods directly — no wrap needed.

### Critical Implementation Details

**`BacktestRequest.catalog_name` routing (Task 1 → 2 → 3):**

```python
# src/models/backtest_request.py (additions)
catalog_name: str | None = Field(default=None, description="Named catalog (FirstRate)")

def to_persistence_data_source(self) -> str:
    if self.catalog_name:
        return f"catalog:{self.catalog_name}"
    return self.data_source
```

```python
# src/cli/commands/_backtest_helpers.py (routing addition)
async def load_backtest_data(
    *, data_source, instrument_id, bar_type_spec, start, end, console,
    catalog_service=None, yaml_data=None,
    catalog_name: str | None = None,   # NEW
) -> DataLoadResult:
    if catalog_name:
        from src.services.firstrate.backtest_loader import load_from_catalog
        return await load_from_catalog(
            catalog_name=catalog_name,
            ticker=_ticker_from(instrument_id),   # strip venue
            bar_type_spec=bar_type_spec,
            start=start, end=end,
            catalog_manager=get_catalog_manager(),
            metadata_service=get_metadata_service(),
        )
    # ... existing branches unchanged
```

**Do NOT call `_resolve_instrument_id` when `catalog_name` is set** (`src/models/backtest_request.py:19-52`). That helper scans the default `DataCatalogService.availability_cache` which points to `NAUTILUS_PATH` — it will either miss (fallback to `{SYM}.NASDAQ`) or HIT a stale entry from the default catalog. Neither is correct for a named-catalog run. Instead, resolve at load-time from the DB (`MetadataService.get_instrument_sync(catalog_name, ticker).nautilus_id`). The new CLI flag keeps `symbol` as the user-visible identifier; the loader overwrites `request.instrument_id` from the DB-authoritative source.

**Instrument synthesis (Task 2.4) — parse venue safely:**

```python
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.test_kit.providers import TestInstrumentProvider

def _build_equity(nautilus_id: str, ticker: str):
    inst_id = InstrumentId.from_str(nautilus_id)  # handles BRK.B, SPY.ARCA correctly
    return TestInstrumentProvider.equity(symbol=ticker, venue=str(inst_id.venue))
```

`nautilus_id.split(".")[-1]` breaks on tickers like `BRK.B` — use `InstrumentId.from_str` (same pattern as `src/cli/commands/_backtest_helpers.py:460-463` for Kraken).

**Multi-instrument engine setup (Task 5.3) — strict order:**

```python
# _setup_engine_multi outline — follow docs/agent/nautilus.md#Engine Setup Sequence
unique_venues = {inst.id.venue for inst, _ in instrument_bars}

# 1. Venues
for venue in unique_venues:
    self.engine.add_venue(venue=venue, oms_type=OmsType.HEDGING, account_type=AccountType.MARGIN,
        starting_balances=[Money(..., USD)], fill_model=..., fee_model=...)

# 2. Instruments
for instrument, _ in instrument_bars:
    self.engine.add_instrument(instrument)

# 3. Data — add_data per bars list (Nautilus supports multiple calls)
for _, bars in instrument_bars:
    self.engine.add_data(bars)

# 4. Strategy — primary = first instrument (Task 5.4)
# 5. Run
```

**Missing ticker vs empty window (Task 2.2 vs 2.3):** two distinct error paths. Missing ticker = DB has no row → raise BEFORE any catalog I/O. Empty window = DB has the row but the catalog read returns `[]` for the requested window (edge case: ticker imported but the requested dates are outside its range). Both surface as `DataNotFoundError` for uniform caller handling, but with different messages and optional `context` fields. Do NOT swallow either into a generic `ValueError` — the HTMX error handler renders the message verbatim.

**`DataNotFoundError` shape:**

```python
# src/services/exceptions.py — confirm or extend
raise DataNotFoundError(
    instrument_id=ticker,
    start=start,
    end=end,
    context={"catalog": catalog_name, "metadata_range": (row.date_range_start, row.date_range_end)},
)
```

If the existing `DataNotFoundError` dataclass doesn't have a `context` field, add one (default `None` or `{}`) so the Kraken / IBKR paths remain untouched. Prefer extending the existing exception over a new type — the HTMX error handler + `_backtest_helpers` both already catch `DataNotFoundError`.

### Existing Code to Reuse (DO NOT duplicate)

| What | Where | How to use |
|------|-------|------------|
| `CatalogManager.resolve_catalog(name)` | `src/services/firstrate/catalog_manager.py:36-61` | **Primary entry point** — returns `ParquetDataCatalog`; raises `FileNotFoundError` with available-catalogs list on miss |
| `CatalogManager.list_catalogs()` | `src/services/firstrate/catalog_manager.py:63-71` | Feed the unknown-catalog error message |
| `MetadataService.get_instrument_sync / get_instrument` | `src/services/firstrate/metadata_service.py` | DB-authoritative ticker → nautilus_id + date range + bar counts |
| `CatalogInstrument.nautilus_id` | `src/db/models/catalog_instrument.py` | `AAPL.NASDAQ`-style string; use via `InstrumentId.from_str` |
| `TestInstrumentProvider.equity` | Nautilus test kit; used in `src/utils/mock_data.py:334` | Synthesise `Equity` at load time — Phase 1 pattern |
| `BacktestRequest.from_cli_args` + `to_config_snapshot` | `src/models/backtest_request.py:259-346` | Extend; do NOT fork |
| `BacktestOrchestrator._setup_engine / _create_strategy` | `src/core/backtest_orchestrator.py:166-293` | Extend with `_multi` variants sharing private helpers |
| `_backtest_helpers.load_backtest_data` | `src/cli/commands/_backtest_helpers.py:326-382` | Add `catalog_name` short-circuit branch |
| `_backtest_helpers.execute_backtest` | `src/cli/commands/_backtest_helpers.py:613-655` | Already does `try/finally dispose()` — reuse verbatim |
| `DataNotFoundError` | `src/services/exceptions.py` | Extend with optional `context` field if missing; do NOT create a new type |
| `ExplorerTimeframe.bar_type_spec` | `src/api/models/explorer.py:18-66` | Timeframe → `BarType` spec string when building `{nautilus_id}-{bar_type_spec}-EXTERNAL` |
| `InstrumentId.from_str` | Nautilus core | Safe venue parsing — do NOT `split(".")[-1]` |
| `asyncio.to_thread` for sync catalog reads | `src/api/stats_service.py`, `src/api/ui/explorer.py` (5 call sites) | Mandatory wrap for async handler callers |
| `_backtest_lock` (asyncio.Lock) | `src/api/ui/backtests.py` | Already serialises — do not duplicate |
| `is_logging_initialized()` + `LoggingConfig(bypass_logging=...)` | `src/core/backtest_orchestrator.py:180-187` | Keep verbatim — LogGuard pattern |

### File Structure

**New files:**

- `src/services/firstrate/backtest_loader.py` — `load_from_catalog(catalog_name, ticker, bar_type_spec, start, end, *, catalog_manager, metadata_service) -> DataLoadResult`. Also exports small helpers `_build_bar_type(...)` and `_build_equity(nautilus_id, ticker)`. Keep under 150 lines (CLAUDE.md file-size limit).
- `tests/component/services/firstrate/test_catalog_backtest_loader.py` — component-tier tests for resolution, missing-ticker, empty-window, instrument synthesis, parity smoke test (AC #12, guarded by env flag).
- `tests/integration/core/test_backtest_catalog_integration.py` — integration tests (AC #5, #6, #7, #9) with `@pytest.mark.integration` + `--forked`.

**Modified files:**

- `src/models/backtest_request.py` — add `catalog_name` field + `to_persistence_data_source()` + `from_cli_args(catalog_name=...)` propagation; skip `_resolve_instrument_id` when `catalog_name` is set.
- `src/core/backtest_orchestrator.py` — add `execute_multi(request, instrument_bars)` + `_setup_engine_multi` + share `_create_strategy_multi`. Wire `data_source=request.to_persistence_data_source()` in both `_persist_results` and `_persist_failed`.
- `src/cli/commands/_backtest_helpers.py` — add `catalog_name` param to `load_backtest_data`, `resolve_backtest_request`, `_resolve_cli_mode`. Thread through.
- `src/cli/commands/backtest.py` — add `--catalog` Click option; wire to helpers.
- `src/api/models/run_backtest.py` — add `catalog_name: str | None` to `BacktestRunFormData`.
- `src/api/ui/backtests.py` — read `catalog_name` from form + query string; pass to loader + request builders.
- `src/services/exceptions.py` — extend `DataNotFoundError` with optional `context: dict | None = None` (if not already present).
- `tests/unit/test_backtest_request.py` — new test for `catalog_name` field (AC #11).
- `tests/unit/cli/test_backtest_helpers.py` — route-selection tests (AC #1, #10).
- `tests/component/cli/test_backtest_cli.py` — `--catalog` flag wiring (AC #10).
- `tests/component/core/test_backtest_orchestrator.py` — multi-instrument tests (AC #5, #9).
- `tests/component/api/test_run_backtest_routes.py` — form `catalog_name` propagation (AC #10, #11).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — epic-3 status transition (automated by this workflow).

**No Alembic migration, no new Pydantic response models for REST, no new web templates (Story 3.2 owns UI).**

### Project Structure Notes

- `src/services/firstrate/backtest_loader.py` sits alongside `import_service.py` / `metadata_service.py` / `catalog_manager.py` per architecture.md § Project Structure. It is the read-path counterpart to `import_service.py`'s write-path.
- `tests/component/services/firstrate/` mirrors `src/services/firstrate/` — new subfolder if it doesn't already exist; `tests/unit/services/firstrate/` already does.
- `tests/integration/core/` — follows the existing pattern; ensure `tests/integration/conftest.py`'s `integration_cleanup` fixture is inherited (double `gc.collect()` after engine dispose).

### Testing Requirements

- **TDD non-negotiable** (CLAUDE.md § Foundational Rules): every task starts with a failing test.
- **Test pyramid distribution** expected:
  - Unit (`make test-unit`): `test_backtest_request.py`, `test_backtest_helpers.py` route selection — pure logic + Pydantic validation. No Nautilus.
  - Component (`make test-component`): loader + CLI wiring + run-form routing + orchestrator multi-instrument (mocked engine). `TestClient` + dependency-overrides.
  - Integration (`make test-integration`, `--forked`): real `BacktestEngine` end-to-end against a `tmp_path`-scoped catalog. Nautilus C extensions require `--forked`.
  - E2E / manual: `agent-browser` smoke via Task 9.5.
- **No new markers.** `@pytest.mark.unit | .component | .integration` — existing markers only.
- **Fixtures:** use `tmp_path`-scoped catalog for integration tests (write 10 synthetic bars via `ParquetDataCatalog.write_data` in a fixture). Do NOT rely on the developer-machine `e2e-test` catalog for CI; that catalog is local-only (E2E_CATALOG_AVAILABLE=1 gate per Task 7.1).
- **Parity test (Task 7)** is informational/local — wrap in `pytest.skip` guard when the 21M-bar catalog is absent. It exists for the Story 3.3 hand-off.
- **Quality gates (Task 9.1-9.3):** `make format && make lint && make typecheck && make test-unit && make test-component && make test-integration`.

### Previous Story Intelligence

**From Story 2-3 (data statistics panel) — still load-bearing:**

- **Ruff auto-formatter strips "unused" imports.** When adding `catalog_name` to `BacktestRequest` + propagating through 5+ files, add the *usage* site in the same edit as the import / field. Splitting across edits risks ruff stripping the "unused" field before the consumer lands. If a genuinely-forward-declared import is needed, use `# noqa: F401` with a reason comment (Epic 2 retro §3 established pattern).
- **Dependency-override `TestClient` setup** in `tests/component/api/test_stats_panel_routes.py` — copy the fixture shape for the new Task 2.1 / 6.1 tests. Do NOT introduce new fixture helpers.

**From Story 2-4 (UX polish) — carry forward:**

- **Inline error handling.** `DataNotFoundError` surfaced by the loader must bubble cleanly to the existing HTMX error handler in `templates/partials/htmx_error_handler.html`. That handler renders the error inline in the target fragment — verify the message is human-readable ("Ticker 'SPY' not found in catalog 'e2e-test'. Known tickers: …") and does NOT include a stack trace. Match the tone of existing error messages.
- **`asyncio.to_thread` wrap** (retro B2, resolved 2026-04-19). Every sync `catalog.bars(...)` / `MetadataService.get_instrument_sync(...)` call from an async handler MUST be wrapped. The loader's async entry point (Task 2.6) owns the wrap; callers in `src/api/ui/backtests.py` just `await` the loader.

**From Story 1-4 (import pipeline core) — relevant to read path:**

- **`CatalogInstrument` is the authoritative source for `nautilus_id`** (`src/db/models/catalog_instrument.py`). Do NOT derive it from the ticker + a hardcoded venue ("NASDAQ") — the import pipeline populated the actual venue from `company_profiles.csv` (`exchange` column, e.g., "ARCA" for SPY). Ignoring the DB would silently mismatch the catalog's directory layout.
- **The catalog does NOT persist `Instrument` objects** (`src/services/firstrate/import_service.py:230` only calls `catalog.write_data(bars)` — no `instruments=[...]`). Read path MUST synthesise the instrument (Task 2.4). Future story: persist instruments at import time to drop the synthesis step.

**From Epic 2 retro — pre-work items resolved BEFORE this story:**

- **B2 (sync-in-async) — DONE.** `asyncio.to_thread` wrap in place at 5+ sites. Follow the pattern for the loader's async entry point.
- **B3 (ruff auto-linter) — NOT DONE.** Bit every Epic 2 story. Re-escalated in B3. Treat as active — bundle import + usage in one edit.
- **B4 (compiled-CSS smoke test) — NOT DONE (as of retro).** Epic 2 risk; not relevant to this story (no CSS diffs planned) but keep in mind for UI follow-ups in 3.2.
- **P1 (AAPL 2018 reference dataset) — CONFIRMED.** The 3.3 reference dataset is AAPL 2018-01-01 → 2018-12-31 1-MINUTE, instrument ID `AAPL.NASDAQ`, both paths use the same ID. Tolerance thresholds documented in the retro; this story's Task 7 verifies the *data* layer is byte-identical as a pre-req for 3.3's *PnL* comparison.

### Git Intelligence (last 5 commits)

- `10d8ba6 feat(retro): Epic 2 retrospective and Epic 3 blocker fixes` — just landed; resolves retro B2 (sync-in-async) and bumps CSS cache — direct predecessor.
- `56a7a73 fix(explorer): Story 2-4 code review patches` — XSS + retry GET-only guardrails; pattern applies to any new error-message surfacing in Story 3.1 (but no user-controlled content ends up in `innerHTML` here).
- `31b126a feat(explorer): UX polish pass (Story 2-4)` — error handler partial that the loader's `DataNotFoundError` now flows through.
- `c88a025 fix(quality): resolve lint, type, and env-leakage test failures` — confirms quality-gate baseline Task 9.1-9.3 must maintain.
- `2b42530 feat(explorer): add data statistics panel (Story 2-3)` — `_build_ticker_stats` shared-helper pattern; our `load_from_catalog` follows the same shape (inject dependencies, no internal instantiation).

### Known Constraints / Carryover Debt

- **D11** (Epic 1 carryover, `deferred-work.md`) — silent reimport on regressed source. If a ticker's `date_range_end` in `catalog_instruments` drifts below the Parquet's actual end (regressed reimport), a backtest window assuming the DB value may request beyond the data. Task 2.3's empty-window error message includes both `metadata_range` AND request range specifically to make this diagnosable. **Full fix is out of scope for 3.1** — flagged in Story 3.3's readiness.
- **F7 resolved (2026-04-19):** all async catalog calls wrap in `asyncio.to_thread`. Task 2.6 inherits the pattern — no re-open.
- **U1, U2** (overlapping HTMX requests / stale stats on rapid click) — pre-existing risk in Explorer; not re-opened by 3.1 since `/backtests/run` is a form POST behind `_backtest_lock`, not an HTMX fragment stream. No new exposure.
- **Multi-instrument strategy parametrisation** (Task 5.4) — Story 3.1 passes only the first instrument's `instrument_id` + `bar_type` to the strategy config. Strategies that need per-instrument params in one run are a future enhancement — explicitly documented in Dev Notes so 3.3 doesn't accidentally try.

### Anti-patterns to Avoid

- **Fork the `BacktestOrchestrator`** into a new `CatalogBacktestOrchestrator` — do NOT. One orchestrator, extended additively.
- **Adopt `BacktestNode` / `BacktestDataConfig`** in this story — NOT the existing API; defer indefinitely.
- **Store `Instrument` objects in the FirstRate catalog write path** — that's a separate story; 3.1 synthesises at read time.
- **Globally cache `Equity` instances in the loader** — the orchestrator is single-use and disposes cleanly; caching adds stale-reference risk without a measurable win.
- **Silently fall back to `_resolve_instrument_id`** when `catalog_name` is set — the DB IS the source of truth; never let the default-catalog cache bleed into the named-catalog flow.
- **New `data_source` enum value** like `"firstrate"` or `"named_catalog"` — routing is on `catalog_name` presence; `data_source` stays the allow-list `{catalog, ibkr, kraken, mock}`.
- **Swallowing `FileNotFoundError`** from `CatalogManager.resolve_catalog` into a 500 / generic "backtest failed" — surface as a user-facing `ValueError` with the available-catalogs list per AC #6.
- **Instantiating `CatalogManager()` and `MetadataService()` inside the loader** — injected via dependency; otherwise component tests can't stub.
- **Touching `templates/backtests/run.html` for a catalog `<select>`** — that's Story 3.2's job. 3.1 only accepts `catalog_name` via form field / query string.
- **Running integration tests without `--forked`** — CLAUDE.md Gotcha #2. `make test-integration` is already configured; verify any new test file respects it.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md#Story 3.1` (lines 593-624)] — canonical acceptance criteria.
- [Source: `_bmad-output/planning-artifacts/architecture.md#ADR-2 Named Catalogs via Pydantic Settings` (lines 194-198)] — config-based catalog discovery.
- [Source: `_bmad-output/planning-artifacts/architecture.md#Decision Impact Analysis` (lines 254-270)] — cross-component dependencies for catalog name threading.
- [Source: `_bmad-output/planning-artifacts/architecture.md#Nautilus Trader Capabilities Research → Multi-Instrument Backtest Support` (lines 124-129)] — confirmed single-engine multi-instrument runs.
- [Source: `_bmad-output/planning-artifacts/architecture.md#Architectural Boundaries` (lines 494-555)] — Import pipeline vs Explorer boundary; the read-path counterpart.
- [Source: `_bmad-output/implementation-artifacts/epic-2-retro-2026-04-19.md#5 Epic 3 Preview & Dependencies` (lines 84-106)] — P1 (AAPL 2018 dataset), P2 (sync-in-async posture, resolved), P6 (`BacktestEngine` single-use handled by orchestrator).
- [Source: `_bmad-output/implementation-artifacts/deferred-work.md#D11` (line 37)] — silent reimport on regressed source; drives empty-window error message shape.
- [Source: `docs/agent/nautilus.md#Engine Setup Sequence (Strict Order)` (lines 46-70)] — venue → instrument → data → strategy → run.
- [Source: `docs/agent/nautilus.md#Venue Configuration` (lines 99-107)] — venue must match between instrument, bars, engine.
- [Source: `docs/agent/data-pipeline.md#DataCatalogService` (lines 3-15)] — lazy client init + availability cache; Story 3.1's named-catalog path BYPASSES this.
- [Source: `_bmad-output/project-context.md#Framework-Specific Rules → Nautilus Trader` (lines 62-69)] — BacktestOrchestrator preferred over MinimalBacktestRunner; strict engine setup.
- [Source: `src/core/backtest_orchestrator.py:87-318`] — existing `execute`, `_setup_engine`, `_create_strategy`, result extraction — extend, don't fork.
- [Source: `src/cli/commands/_backtest_helpers.py:326-610`] — existing `load_backtest_data` branches (mock / kraken / catalog+IBKR fallback); add new named-catalog branch.
- [Source: `src/cli/commands/_backtest_helpers.py:613-655`] — `execute_backtest` try/finally dispose pattern; reuse verbatim.
- [Source: `src/services/firstrate/catalog_manager.py:36-71`] — `resolve_catalog` + `list_catalogs` already implement the error-with-available-list shape AC #6 requires.
- [Source: `src/services/firstrate/metadata_service.py`] — async + sync ticker resolution; reuse both.
- [Source: `src/models/backtest_request.py:19-52, 259-346`] — `_resolve_instrument_id` (explicitly skip when `catalog_name` set) + `from_cli_args` + `to_config_snapshot` shapes.
- [Source: `src/api/ui/backtests.py:180-246`] — run-form submission path with `_backtest_lock`; thread `catalog_name` through without disturbing locking.
- [Source: `src/api/models/run_backtest.py:33-65`] — `BacktestRunFormData` validator pattern; add `catalog_name: str | None` with no additional validation beyond Pydantic defaults.
- [Source: `src/utils/mock_data.py:314-340`] — `TestInstrumentProvider.equity` pattern for synthesising `Equity`.
- [Source: `src/api/stats_service.py`] — `asyncio.to_thread` wrap pattern for sync catalog reads inside async handlers.
- [Source: `templates/partials/htmx_error_handler.html`] — error-handler partial that `DataNotFoundError` messages will surface through (when invoked from the web UI).
- [Source: `CLAUDE.md#Critical Gotchas` (items 1, 4)] — LogGuard + single-use `BacktestEngine`.
- [Source: `CLAUDE.md#Anti-Patterns`] — never instantiate `LiveLogger`; never reuse `BacktestEngine`.
- [Source: `_bmad-output/implementation-artifacts/2-4-explorer-ux-polish.md`] — previous story dev notes + bear traps on ruff, OOB includes, `asyncio.to_thread`.

## Dev Agent Record

### Agent Model Used

claude-opus-4-7 (Opus 4.7, 1M context)

### Debug Log References

- CLI smoke evidence: `/tmp/story-3-1-evidence/cli_aapl_2018.txt` (AAPL 2018 1-MINUTE SMA crossover via `--catalog e2e-test`; `Data Source = Catalog: e2e-test`, 9,697 trades, -33.54% return, `--no-persist`).
- Web UI smoke evidence: `/tmp/story-3-1-evidence/run_page_catalog_prefilled.png` (GET `/backtests/run?catalog=e2e-test` with readonly pre-filled catalog row) and `/tmp/story-3-1-evidence/run_detail_with_catalog.png` (post-submit redirect to `/backtests/3986dd75-...`).
- DB row confirmation: `SELECT run_id, data_source FROM backtest_runs ORDER BY created_at DESC LIMIT 1;` → `3986dd75-80b8-4219-b37d-a2cbc40a85b9 | catalog:e2e-test`.
- Parity smoke gated by `E2E_CATALOG_AVAILABLE=1`; test-only skip in CI.

### Completion Notes List

- Task 1: Added `BacktestRequest.catalog_name`, `to_persistence_data_source()` helper, and `from_cli_args(catalog_name=...)` propagation. When `catalog_name` is set the CLI path skips `_resolve_instrument_id` (default-catalog cache must not bleed in); instrument_id carries a placeholder `{SYMBOL}.NAMED_CATALOG` that the loader overwrites from the DB. Updated `_persist_results` + `_persist_failed` to use `to_persistence_data_source()` so named runs persist as `catalog:<name>`.
- Task 2: New `src/services/firstrate/backtest_loader.py` (~150 LoC). Injects `CatalogManager` + `MetadataService` (testability). DB lookup first → fail-fast with `missing_from_catalog` context. Catalog-bar query wrapped in `asyncio.to_thread` (retro B2 rule). Empty-window raises `DataNotFoundError` carrying `metadata_range` + `catalog` context. Instrument synthesis now infers `price_precision` from the first bar (required for FirstRate 1-MINUTE bars which carry precision=4, not TestInstrumentProvider's default 2). Added defensive venue-drift check.
- Task 3: `load_backtest_data` grew a `catalog_name` param + `_resolve_named_catalog_loader()` / `_build_named_catalog_dependencies()` factories. Route bypasses `DataCatalogService` entirely when set. Session closed in a `finally`.
- Task 4: CLI `--catalog` option + `Catalog` context row in both CLI modes. Threaded through `resolve_backtest_request`, `_resolve_cli_mode`, `_resolve_config_mode`. Config-mode YAML-level `catalog_name` is deferred per story note.
- Task 5: `BacktestOrchestrator.execute_multi(request, instrument_bars)` + `_setup_engine_multi` + `_create_strategy_multi`. Additive — existing `execute()` untouched. Deduplicates venues by `str(venue)`. First instrument becomes strategy's primary (documented single-strategy-parametrisation constraint).
- Task 6: Form field `catalog_name: str \| None` on `BacktestRunFormData`. `_build_run_context` pre-fills from `?catalog=` query string. `run_backtest_submit` threads catalog_name to both `BacktestRequest.from_cli_args` and `load_backtest_data`. Template adds a readonly catalog row that only shows when the value is non-empty (Story 3.2 will add the bridge URL).
- Task 7: Parity test guarded by `E2E_CATALOG_AVAILABLE=1` + `CATALOG_BASE_PATH` + `NAUTILUS_PATH`; writes evidence to `/tmp/story-3-1-evidence/parity_aapl_2018_1min.txt` on success.
- Task 8: Integration tests use `ParquetDataCatalog.write_data(synthetic_bars)` under `tmp_path` — no local catalog dependency in CI. All 5 tests pass under `--forked`.
- Task 9: `make format && make lint && make typecheck` all clean. Unit (776 pass), component (586 pass, 16 pre-existing skips), integration core new tests pass under `--forked`. Full `make test-integration` exhibits the same pre-existing Nautilus C-extension parallelism flakiness as baseline (17 failed on main, 15 failed with this story; no regressions, and my tests pass in isolation). CLI + Web smokes both captured to `/tmp/story-3-1-evidence/`.
- Known constraint carried forward: multi-instrument strategy parametrisation passes only the first instrument's `instrument_id` + `bar_type` to the strategy config (single primary). Per-instrument strategy configs are a future enhancement.
- Out-of-scope (deferred) items confirmed: no `BacktestNode` / `BacktestDataConfig` adoption (uses direct engine API as before); no instrument persistence in FirstRate import path (synthesis at read time); Story 3.2 owns the explicit catalog `<select>` dropdown.

### File List

**New files:**
- `src/services/firstrate/backtest_loader.py`
- `tests/component/core/__init__.py`
- `tests/component/core/test_backtest_orchestrator.py`
- `tests/component/services/firstrate/test_catalog_backtest_loader.py`
- `tests/integration/core/__init__.py`
- `tests/integration/core/test_backtest_catalog_integration.py`

**Modified files:**
- `src/api/models/run_backtest.py` — added `catalog_name: str \| None` field to `BacktestRunFormData`.
- `src/api/ui/backtests.py` — read `catalog_name` from form + query string; pre-fill on GET; pass to `BacktestRequest.from_cli_args` and `load_backtest_data`.
- `src/cli/commands/_backtest_helpers.py` — added `_resolve_named_catalog_loader`, `_build_named_catalog_dependencies`, and `catalog_name` param to `load_backtest_data`, `resolve_backtest_request`, `_resolve_cli_mode`, `_resolve_config_mode`.
- `src/cli/commands/backtest.py` — added `--catalog` Click option and `Catalog` context row (both CLI and config modes).
- `src/core/backtest_orchestrator.py` — added `execute_multi`, `_setup_engine_multi`, `_create_strategy_multi`; routed persistence through `request.to_persistence_data_source()`.
- `src/models/backtest_request.py` — added `catalog_name` field, `to_persistence_data_source()` helper, `from_cli_args(catalog_name=...)` propagation, skip `_resolve_instrument_id` when `catalog_name` is set, include `catalog_name` in `to_config_snapshot()`.
- `src/services/exceptions.py` — extended `DataNotFoundError` with optional `message` + `context` kwargs.
- `templates/backtests/run.html` — added readonly `catalog_name` input that reveals only when set.
- `tests/component/api/test_run_backtest_routes.py` — added `test_catalog_name_threaded_through_handler` and `TestGetRunBacktestFormCatalogPrefill` class.
- `tests/component/test_backtest_commands.py` — added `test_catalog_flag_routes_named_catalog`; set `mock_request.catalog_name = None` in four pre-existing tests.
- `tests/unit/cli/test_backtest_helpers.py` — added `TestLoadBacktestDataCatalogNameRouting` class.
- `tests/unit/models/test_backtest_request.py` — added `TestBacktestRequestCatalogName` class.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `3-1-catalog-integration-with-backtestengine` → `review`.

## Change Log

| Date | Author | Change |
|------|--------|--------|
| 2026-04-19 | Amelia (Dev) | Story 3.1 implementation complete — catalog_name plumbed through BacktestRequest, new catalog-backed loader with `asyncio.to_thread` + DB-authoritative instrument resolution, CLI `--catalog` flag, web form `catalog_name` with `?catalog=` pre-fill, multi-instrument orchestrator via `execute_multi`, integration + parity tests. Quality gates clean; CLI + UI smokes captured to `/tmp/story-3-1-evidence/`. Status: in-progress → review. |
