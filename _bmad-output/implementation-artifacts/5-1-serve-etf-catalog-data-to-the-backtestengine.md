# Story 5.1: Serve ETF Catalog Data to the BacktestEngine

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want imported ETF catalog data served to the Nautilus BacktestEngine as a data source through the existing `BacktestOrchestrator` path,
so that ETFs can be backtested directly from the FirstRate catalog with no adapter layer, and an unresolved-venue ETF can never silently enter a run.

## Acceptance Criteria

1. **Given** an ETF with imported Parquet bars and a venue-qualified `InstrumentId` (a `catalog_instruments` row whose `nautilus_id` is set, e.g. `SPY.ARCA`), **When** a named-catalog backtest is configured for it, **Then** the ETF catalog data is routed through the **existing** `firstrate/backtest_loader.load_from_catalog` → `BacktestOrchestrator` path and consumed by the BacktestEngine with **no runtime adapter** — the same asset-class-agnostic `build_equity` synthesis used for Stocks produces the ETF's `Equity` instrument (NFR16, ADR-10). [Source: epics.md Story 5.1 AC1; architecture.md ADR-10 (lines 333-339); NFR16 (epics.md line 105)]
2. **Given** an ETF whose venue is unresolved (non-backtestable per Story 3.5 — its `catalog_instruments.nautilus_id` is `None`/blank because `InstrumentMapper.sync_qualification` nulled the identity), **When** it is targeted for a backtest, **Then** `load_from_catalog` **fails fast** with a `DataNotFoundError` that explicitly names the venue-unresolved / non-backtestable condition and carries a `venue_unresolved` diagnostic flag in its `context`; the catalog is **never read** (`catalog.bars()` is not called) so the unresolved instrument can never silently enter a run. [Source: epics.md Story 5.1 AC2; instrument_mapper.py `sync_qualification` docstring (lines 141-186); Story 3.5 exclusion contract]
3. **Given** the ETF `Equity` instrument synthesised for a backtest, **When** it is built, **Then** it carries `lot_size = 1` (whole-share, equity-style) and USD currency exactly as for Stocks — establishing the whole-share sizing precondition Story 5.2 depends on, with **no** crypto/FX branching and **no** asset-class-specific instrument type. (Actual order sizing behaviour is verified in Story 5.2; this story only guarantees the instrument is equity-shaped.) [Source: backtest_loader.py `build_equity` (lines 51-88); architecture.md ADR-10 "leveraged/inverse ETFs settle as ordinary shares"]
4. **Given** the existing Stocks named-catalog backtest path, **When** this story's changes land, **Then** prior backtest numbers are unchanged — the only behavioural delta is a clearer message + `context` flag on the already-raising unresolved-`nautilus_id` branch (previously untested), and no change to bar loading, instrument precision, venue derivation, or engine setup. **No** Alembic migration and **no** results-DB schema change are introduced. [Source: CLAUDE.md "BacktestEngine single-use"; harness constraints; architecture.md line 565 "No new files"]

## Tasks / Subtasks

- [x] **Task 1: Harden the unresolved-venue fail-fast in `load_from_catalog`** (AC: #2, #4) — *write the failing component tests in `tests/component/services/firstrate/test_catalog_backtest_loader.py` FIRST (TDD Red→Green)*
  - [x] In `src/services/firstrate/backtest_loader.py`, the existing guard is `if not nautilus_id: raise DataNotFoundError(... message="... has no nautilus_id — DB metadata is incomplete. Re-import ...", context={"missing_from_catalog": catalog_name})`. This branch is **currently untested** and frames the condition as generic data-incompleteness. Rework it so it is an **explicit, unmistakable non-backtestable exclusion**:
    - Message names the condition: e.g. `f"ETF/ticker '{ticker}' in catalog '{catalog_name}' has an unresolved venue (non-backtestable, Story 3.5) — its nautilus_id is unset, so it is excluded from backtests. Resolve its venue (metadata resolution / venue_overrides.csv) and re-import to admit it."`
    - `context` gains `{"venue_unresolved": True, "catalog": catalog_name}` so callers/tests can distinguish an intentional exclusion from a plain missing-ticker (`missing_from_catalog`) or empty-window miss. Keep `instrument_id=ticker`, `start`, `end` as-is.
  - [x] Do **not** branch on `asset_class` anywhere in the loader — ETFs and Stocks share the identical code path (that is the "no runtime adapter" guarantee, NFR16). The gate is purely presence-of-`nautilus_id`, which `sync_qualification` already nulls for unresolved venues.
  - [x] Keep the guard **before** `catalog_manager.resolve_catalog(...)` and `catalog.bars(...)` so an unresolved instrument never touches the filesystem / never enters a run (AC2).

- [x] **Task 2: Component tests — ETF routing with no adapter** (AC: #1, #3) — *these assertions should fail first, then pass (no code change needed for AC1/AC3 if the loader is already asset-class-agnostic; the tests LOCK IN the contract)*
  - [x] In `tests/component/services/firstrate/test_catalog_backtest_loader.py`, extend `_make_instrument_row` to accept an `asset_class` kwarg (default keeps existing tests green) and add an ETF-focused test: an ETF row (`ticker="SPY"`, `nautilus_id="SPY.ARCA"`, `asset_class="ETF"`) → `load_from_catalog` returns a `DataLoadResult` whose `instrument` is a Nautilus `Equity` with `id.symbol.value == "SPY"`, `str(id.venue) == "ARCA"`, `lot_size == 1`, and USD currency. Assert `catalog.bars` was called with `["SPY.ARCA-1-DAY-LAST-EXTERNAL"]` — proving the identical Stocks path serves the ETF. [Source: test file `_make_bar_for`, `_StringVenue` helpers already present]
  - [x] Assert the returned instrument is `isinstance(instrument, Equity)` (import from `nautilus_trader.model.instruments`) — the equity-shape precondition for Story 5.2 whole-share sizing (AC3). No leveraged/inverse special-casing exists.

- [x] **Task 3: Component tests — unresolved-venue ETF fails fast** (AC: #2)
  - [x] Add `test_unresolved_venue_etf_fails_fast_before_catalog_read`: ETF row with `nautilus_id=None` → `pytest.raises(DataNotFoundError)`; assert `exc.context.get("venue_unresolved") is True`, `exc.instrument_id == "SPY"`, the message mentions "unresolved venue"/"non-backtestable", and **`catalog_manager.resolve_catalog.assert_not_called()`** + no `catalog.bars` call (never enters a run).
  - [x] Add a parametrized/second case for `nautilus_id=""` (empty string) — `not nautilus_id` already covers it, but lock it in so a blank identity can't slip through.

- [x] **Task 4: End-to-end routing proof through `BacktestOrchestrator`** (AC: #1, #4) — *component tier, real engine, no persistence*
  - [x] Add a focused test (new module `tests/component/core/test_etf_backtest_routing.py` or extend an existing orchestrator component test if one runs a real engine) that: builds an ETF `Equity` via `backtest_loader.build_equity(nautilus_id="SPY.ARCA", ticker="SPY", bars=<mock/generated ETF bars>)`, generates a small deterministic list of `Bar`s for `SPY.ARCA-1-DAY-LAST-EXTERNAL`, constructs a `BacktestRequest` (persist=False) for `sma_crossover`, and runs `BacktestOrchestrator.execute(request, bars, instrument)`. Assert it returns a `BacktestResult` without raising — proving ETF bars are **consumed by the BacktestEngine with no adapter**. Dispose the orchestrator in a `finally`. [Source: backtest_orchestrator.py `execute`/`_setup_engine`; _backtest_helpers.py `execute_backtest`]
  - [x] Reuse existing test-data helpers where possible (`src/utils/mock_data.py` — see `generate_mock_data_from_yaml`, `create_test_instrument`) rather than hand-rolling bars; the bar `bar_type` must match `SPY.ARCA-1-DAY-LAST-EXTERNAL` and the instrument `id`/precision must match the bars (strict Nautilus check). If a real-engine run is too heavy for the component tier locally, mark it `@pytest.mark.integration` and use the `--forked` runner (`make test-integration`), documenting the choice in Completion Notes.
  - [x] This test MUST NOT persist (no DB write) and MUST NOT touch live data — bars are generated in-process.

- [x] **Task 5: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new imports like `Equity` must be used in the same edit; re-read files after edits).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: ≤6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Do not add any.
  - [x] `uv run pytest tests/component/services/firstrate/test_catalog_backtest_loader.py -q` green, plus the new routing test (`make test-component`, or `make test-integration` if Task 4 is integration-tier).
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`. The loader edit is a message/context tweak; tests are additive.
  - [x] Confirm no Alembic migration and no `src/db/models/**` change were introduced (AC4). `git diff --stat` should show only `backtest_loader.py` + test files (+ this story file + sprint-status).

## Review Findings

Adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor) — **clean**. All four ACs confirmed satisfied; no scope-boundary violations; no blocking/major findings. Two minor observations:

- [x] [Review][Patch] `DataNotFoundError` docstring example didn't mention the new `venue_unresolved`/`catalog` context shape [src/services/exceptions.py:46] — applied: docstring now documents the Story 5.1 unresolved-venue exclusion context.
- [x] [Review][Dismiss] Routing proof calls `load_from_catalog` directly rather than through the `_backtest_helpers.load_backtest_data` dispatch — spec-sanctioned (Task 4 directs the direct-call approach; the dispatch layer is asset-class-agnostic, forwarding only `catalog_name`+`ticker`). No action.

Verified non-issues: the context-key change (`missing_from_catalog` → `venue_unresolved`) breaks no consumer (CLI/web read only `str(e)`; the sibling row-is-None branch still emits `missing_from_catalog`); `Equity.quote_currency == USD` is the correct attribute; both `_make_instrument_row` helpers (component keyword-only, integration positional) are independent and all callers match.

## Dev Notes

### The core idea (why this story exists — and why it is small)
Epic 5 is **reuse-heavy by design** (architecture.md ADR-10, line 565 "No new files"). Story 3.1 already built the named-catalog backtest path — `firstrate/backtest_loader.load_from_catalog` resolves `catalog_name + ticker + bar_type_spec + range` to a `DataLoadResult(bars, instrument)`, and `_backtest_helpers.load_backtest_data` routes any `catalog_name` request through it before handing off to `BacktestOrchestrator.execute`. That path is **asset-class-agnostic**: `build_equity` synthesises a Nautilus `Equity` from the `nautilus_id` regardless of whether the row's `asset_class` is `STOCK` or `ETF`. So ETFs already flow to the BacktestEngine with **no adapter** — exactly NFR16.

Story 5.1's real deliverable is therefore (a) **locking that contract in with tests** so no future change sneaks an ETF-specific adapter branch in, and (b) **hardening the unresolved-venue fail-fast** so AC2 ("an unresolved venue can never silently enter a run") is explicit and verifiable rather than an untested side-effect. [Source: epics.md Story 5.1; backtest_loader.py; _backtest_helpers.py `load_backtest_data` (lines 384-473)]

### The seam (what changes, end to end)
```
catalog_name + ETF ticker (e.g. "SPY")
        │  _backtest_helpers.load_backtest_data (catalog_name set → short-circuit)
        ▼
firstrate/backtest_loader.load_from_catalog
        │  metadata_service.get_instrument_sync(catalog, ticker) → CatalogInstrument row
        │        │
        │        ├─ row is None                → DataNotFoundError (missing_from_catalog)   [existing]
        │        ├─ row.nautilus_id is falsy   → DataNotFoundError (venue_unresolved)  ← HARDENED (Task 1)
        │        └─ nautilus_id set (e.g. "SPY.ARCA")
        │              catalog.bars([SPY.ARCA-{spec}-EXTERNAL], start, end) → bars
        │              build_equity(nautilus_id, ticker, bars) → Equity  ← same as Stocks, no ETF branch (AC1/AC3)
        ▼
DataLoadResult(bars, Equity instrument)
        │  BacktestOrchestrator.execute(request, bars, instrument)
        ▼
BacktestEngine.add_instrument / add_data / run()  → BacktestResult   (AC1/AC4 — no adapter)
```
The **only** production change is the message + `context` on the `nautilus_id`-falsy branch. Everything else is test coverage. [Source: backtest_loader.py lines 91-202]

### Why the gate is `nautilus_id` (not `resolution_status`) — do NOT re-plumb this
`InstrumentMapper.sync_qualification` (the import single-writer) is the exclusion mechanism: when a venue resolves it writes `nautilus_id = "{ticker}.{venue}"`; when the venue is `VENUE_UNRESOLVED` it sets `nautilus_id = None` and leaves the on-disk Parquet untouched (Story 3.5 "bars kept, not dropped"). Its docstring states plainly: *"Nulling the identity is the exclusion mechanism the backtest loader honors (it gates on nautilus_id)."* So the loader gating on `nautilus_id` presence **is** the authoritative backtestable gate at the serving layer. `services/metadata/backtestable.py` derives the *reporting* universe from `instrument_metadata.resolution_status` for the CLI (`metadata backtestable`) — that is a different surface (reporting vs serving) and Story 5.1 must **not** cross-wire the loader to it (that would add a second DB read + a `instrument_metadata` dependency to a hot path and risk drift). Keep the seam as designed. [Source: instrument_mapper.py lines 141-186; backtestable.py module docstring; memory `project_epic3_backtestable_signal`]

### Scope boundaries (do NOT do here)
- **No runtime adapter / no `asset_class` branch** in the loader or orchestrator — that would violate NFR16 and ADR-10. ETFs are served by the identical Equity path.
- **No order-sizing work** — whole-share sizing *behaviour* (leveraged/inverse ETFs settling as ordinary shares) is **Story 5.2**. Here we only guarantee the instrument is equity-shaped (`lot_size=1`, USD).
- **No multi-ETF / `instrument_ids` list** — that is **Story 5.3**.
- **No persistence changes** — DB write path is **Story 5.4**; the routing test runs with `persist=False`. **No Alembic migration, no results-DB schema change** (hard harness constraint — STOP and surface if one appears necessary).
- **No reference-comparison tooling** — **Story 5.5**.
- **No web/CLI surface change** — both `src/api/ui/backtests.py` (line 334) and `src/cli/commands/backtest.py` (line 277) already catch `DataNotFoundError` and surface its message inline, so the hardened fail-fast is handled by existing callers for free. Do not add new UI/CLI handling.
- **Never touch live data** — bars are generated in-process / mocked; the loader is local-catalog only and never falls back to IBKR (Story 3.1 contract).

### Testing standards summary
- Tier: **component** for the loader (matches the existing `test_catalog_backtest_loader.py`), and component or `--forked` **integration** for the real-engine routing proof (Nautilus C/Rust extensions corrupt state across `fork()` — use `make test-integration` if the engine run is at integration tier). [Source: CLAUDE.md test tiers; `docs/agent/testing.md`]
- TDD Red→Green: write the unresolved-venue and ETF-Equity assertions to fail against the current message/context first.
- Reuse the file's existing `_make_instrument_row`, `_make_bar_for`, `_StringVenue` mock helpers; extend, don't rewrite.

### Project Structure Notes
- Touch points: `src/services/firstrate/backtest_loader.py` (one branch), `tests/component/services/firstrate/test_catalog_backtest_loader.py` (additive), and a new/extended routing test. No new production files (architecture.md line 565).
- No conflicts with the unified structure; the loader already lives at the ADR-10-designated location.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.1 (lines 787-801); FR33 (line 68, 205); NFR16 (line 105)]
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-10 (lines 333-339); line 565 (E5 "No new files"); line 602]
- [Source: src/services/firstrate/backtest_loader.py (load_from_catalog lines 91-202, build_equity lines 51-88)]
- [Source: src/services/firstrate/instrument_mapper.py (sync_qualification lines 141-186)]
- [Source: src/cli/commands/_backtest_helpers.py (load_backtest_data lines 384-473, execute_backtest lines 704-746)]
- [Source: src/core/backtest_orchestrator.py (execute/_setup_engine lines 87-225)]
- [Source: tests/component/services/firstrate/test_catalog_backtest_loader.py]
- [Source: memory project_epic3_backtestable_signal — "backtestable gates on catalog_instruments.nautilus_id"]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/component/services/firstrate/test_catalog_backtest_loader.py -q` → 9 passed, 1 skipped (21M-bar parity, local-only).
- `uv run pytest tests/integration/core/test_backtest_catalog_integration.py --forked -q` → 6 passed (incl. the two new ETF cases).
- `uv run ruff check .` → All checks passed. `uv run mypy .` → Success: no issues found in 360 source files.

### Completion Notes List

- **Reuse-heavy, one-line production delta.** Confirmed the named-catalog path (`load_from_catalog` → `build_equity` → `BacktestOrchestrator`) is already asset-class-agnostic: `build_equity` synthesises a Nautilus `Equity` from `nautilus_id` regardless of `asset_class`, so ETFs reach the BacktestEngine with **no runtime adapter** (NFR16 / ADR-10). The only production change is hardening the already-raising unresolved-`nautilus_id` branch into an explicit non-backtestable exclusion (clearer message + `context={"venue_unresolved": True, "catalog": ...}`). No bar-loading, precision, venue, or engine-setup change → prior backtest numbers untouched (AC4).
- **Gate stays on `nautilus_id`** (the serving-layer exclusion mechanism per `sync_qualification`), deliberately NOT re-plumbed to `instrument_metadata.resolution_status` (that is the reporting surface). Avoids a second DB read on the hot path and drift risk.
- **Task 4 tier decision:** the real-engine routing proof lives at **integration tier** (`--forked`) alongside the existing Story 3.1 named-catalog integration test, reusing its `synthetic_catalog` / `_make_daily_bars` / `_build_sma_request` helpers (extended for `SPY.ARCA` as the ETF stand-in). A real `BacktestEngine.run()` needs Nautilus C/Rust `fork()` isolation, so component tier was not used for the engine run. Component tier covers the loader contract (Equity synthesis + fail-fast) with mocks. Runs with `persist=False`; bars generated in-process (no DB write, no live data).
- No Alembic migration and no `src/db/models/**` change (verified via `git diff --stat`).

### File List

- `src/services/firstrate/backtest_loader.py` (M) — hardened unresolved-venue fail-fast (message + `venue_unresolved` context).
- `tests/component/services/firstrate/test_catalog_backtest_loader.py` (M) — `_make_instrument_row` gains `asset_class`/nullable `nautilus_id`; new `TestEtfRoutingNoAdapter` (ETF Equity routing + unresolved-venue + blank-id fail-fast).
- `tests/integration/core/test_backtest_catalog_integration.py` (M) — `synthetic_catalog` adds `SPY.ARCA`; `_make_instrument_row` gains `asset_class`; new ETF single-instrument run + unresolved-venue fail-fast integration tests.
- `_bmad-output/implementation-artifacts/5-1-serve-etf-catalog-data-to-the-backtestengine.md` (A) — this story.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (M) — epic-5 → in-progress, 5-1 → review.
