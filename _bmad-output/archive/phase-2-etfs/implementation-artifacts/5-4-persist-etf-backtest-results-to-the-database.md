# Story 5.4: Persist ETF Backtest Results to the Database

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want ETF backtest results persisted to the results database,
so that I can review them in the web UI like any other run — an ETF run is recorded and displayed identically to a Stock run, via the existing DB path, with no new schema.

## Acceptance Criteria

1. **Given** a completed ETF backtest (single or multi) run through the **existing** `load_from_catalog` → `BacktestOrchestrator.execute` path with `persist=True`, **When** it finishes, **Then** a results record is persisted via the **existing** DB path (`BacktestPersistenceService.save_backtest_results` → `BacktestRepository.create_backtest_run` + `create_performance_metrics`) — a `backtest_runs` row plus its one-to-one `performance_metrics` row — and the returned `run_id` is non-`None`. The persisted `backtest_runs.instrument_symbol` carries the ETF ticker (e.g. `IVV`), `execution_status == "success"`, and `data_source == "catalog:<name>"`. [Source: epics.md Story 5.4 AC1; backtest_orchestrator.py `_persist_results` lines 345-421; backtest_persistence.py `save_backtest_results` lines 52-132]
2. **Given** the persisted ETF results, **When** the operator opens the web UI (the read path the detail/list pages use — `BacktestQueryService.get_backtest_by_id` → `BacktestRepository.find_by_run_id`, then `to_detail_view`), **Then** the ETF backtest results are viewable there **consistent with existing run displays**: `get_backtest_by_id(run_id)` returns the `BacktestRun` with its metrics eagerly loaded, and `to_detail_view(run).configuration.instrument_symbol` surfaces the ETF ticker — no asset-class-specific rendering branch. [Source: epics.md Story 5.4 AC2; backtest_query.py `get_backtest_by_id` lines 53-70; backtest_detail.py `to_detail_view` lines 555-588, `build_configuration` lines 488-522]
3. **Given** an ETF run and a Stock run persisted through the **identical** path, **When** their records are compared, **Then** they are the **same shape** — the same tables, the same populated columns, `instrument_symbol` carrying the ticker — with **no `asset_class` / instrument-type / ETF-specific column** anywhere in `backtest_runs`, `performance_metrics`, or `trades`. The persistence + display path is asset-class-blind (the ETF's "ETF-ness" is erased at load time into a plain `Equity` + symbol string); that blindness **is** AC2's "consistent with existing run displays". [Source: db/models/backtest.py `BacktestRun` line 33 / `PerformanceMetrics` line 167; db/models/trade.py; no `asset_class` on the persist/display path — confirmed]
4. **Given** this story persists ETF results via the **existing** DB path, **When** its changes land, **Then** prior backtest numbers are unchanged, **no** production code is modified (tests only), **no** Alembic migration and **no** results-DB schema change are introduced (the `backtest_runs`/`performance_metrics`/`trades` schema already stores everything an ETF run needs). If a schema change appears necessary, **STOP and surface it** rather than adding one. [Source: CLAUDE.md harness constraints; architecture.md line 565 "No new files"; Story 5.1/5.2 File Lists — persist path was always asset-class-agnostic]

## Tasks / Subtasks

- [x] **Task 1: Integration — a real ETF run persists a results record (round-trip)** (AC: #1, #3) — *extend `tests/integration/core/test_backtest_catalog_integration.py` (`--forked`, real `BacktestEngine` + real Postgres). TDD: write the read-back assertions first.*
  - [x] Bring the schema-isolated async DB fixtures into this module: copy the `get_worker_id` helper + `async_test_engine` and `async_session` fixtures from `tests/integration/db/test_backtest_repository.py` (lines 15-70) — a per-xdist-worker `test_<worker>` schema created/dropped around the test, `search_path` set on the session. (These create the tables via `Base.metadata.create_all` — **not** a migration; the results-DB schema is unchanged, AC4.)
  - [x] Parametrize the existing `_build_sma_request` helper to accept `persist: bool = False` (default preserves every current caller) so an ETF request can be built with `persist=True`. Do not otherwise change it.
  - [x] Add `test_named_catalog_etf_run_persists_results`: run `IVV.ARCA` (plain ETF, zigzag bars → real fills so trades/metrics are non-trivial) through `load_from_catalog` → `BacktestOrchestrator.execute(persist=True)`. **`_persist_results` opens its own session via `src.db.session.get_session`**, so `monkeypatch` `src.core.backtest_orchestrator.get_session` with an `@asynccontextmanager` that yields a session on the **test schema** (an `async_sessionmaker` on `async_test_engine` that `SET search_path TO test_<worker>`; commit-visible). Assert `run_id is not None`. Then, with a **separate** read session (the `async_session` fixture), `BacktestRepository(read_session).find_by_run_id(run_id)` and assert: record is not `None`, `instrument_symbol == "IVV"`, `execution_status == "success"`, `data_source == "catalog:e2e-test"`, `strategy_type == "sma_crossover"`, and `run.metrics is not None` (performance_metrics row was written). Dispose the orchestrator in `finally`.
  - [x] **Parity (AC3):** in the same test (or a sibling), also persist a **Stock** (`AAPL.NASDAQ`) through the identical path and assert the two persisted `BacktestRun` records populate the **same** column set — e.g. compare `set(k for k,v in vars(run).items() if not k.startswith("_"))` (or an explicit field allow-list: `instrument_symbol`, `execution_status`, `data_source`, `strategy_type`, `initial_capital`) is identical, and that **neither** record carries any `asset_class`/`is_etf`/instrument-type attribute (`not hasattr(run, "asset_class")`). This locks in "ETF persists identically to a Stock" so a future asset-class branch in the persist path breaks the test.
  - [x] MUST use `persist=True` against the **isolated test schema only** (never the app DB), MUST generate bars in-process, and MUST NOT touch live data. `--forked` runner (`make test-integration`) for Nautilus C/Rust `fork()` isolation.

- [x] **Task 2: Integration — persisted ETF run is viewable via the web read path** (AC: #2, #3) — *same module / fixtures*
  - [x] Add `test_persisted_etf_run_is_viewable_in_web_read_path`: reuse the Task 1 persisted ETF `run_id` (persist an ETF within the test), then exercise the **exact** read the UI uses — `BacktestQueryService(BacktestRepository(read_session)).get_backtest_by_id(run_id)` — and assert it returns the run with `run.metrics` loaded (eager, no lazy-load) and `instrument_symbol == "IVV"`.
  - [x] Assert the presentation mapper renders it consistently: `to_detail_view(run).configuration.instrument_symbol == "IVV"` and `to_detail_view(run).execution_status == "success"` — proving the web detail view surfaces the ETF run with **no** asset-class branch (`build_configuration` only special-cases `data_source == "kraken"`, which a `catalog:*` ETF run never hits, so the ticker renders plainly). `to_detail_view` reads only eager-loaded `run.metrics` + scalar columns (no `.trades` lazy access), so it is safe to call on a `find_by_run_id` result in the async test.
  - [x] Keep this assertion at the query-service + presentation-mapper tier (not a browser) — it proves the same objects the FastAPI route builds; a full browser pass is out of scope for a lock-in story (the route/template are unchanged and already asset-class-agnostic).

- [x] **Task 3: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new imports like `BacktestRepository`, `BacktestQueryService`, `to_detail_view`, `asynccontextmanager`, `text`, `async_sessionmaker` must be used in the same edit; re-read files after edits).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: ≤6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Do not add any.
  - [x] `uv run pytest tests/integration/core/test_backtest_catalog_integration.py --forked -q` green (the existing 8 + the new persistence tests); requires a running Postgres (per `feedback_postgres_setup`).
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`. All changes are additive test code + fixtures.
  - [x] Confirm **no production change**, **no** Alembic migration, **no** `src/db/models/**` change (AC4). `git diff --stat` should show only the one integration test file (+ this story file + sprint-status).

## Review Findings

Adversarial review (Blind Hunter + Edge Case Hunter + Acceptance Auditor). All four ACs confirmed covered by executing assertions; the diff is verifiably test-only (no `src/`, `alembic/`, or `src/db/models/**` change — AC4 holds). Findings triaged:

- [x] [Review][Patch] AC3's "no `asset_class` column" guarantee was asserted only on `BacktestRun`, not on `performance_metrics`/`trades` as the AC text requires [Acceptance Auditor]. Applied: added deterministic schema-level tripwires — `"asset_class" not in {c.name for c in PerformanceMetrics.__table__.columns}` and the same for `Trade.__table__` — so a future asset-class column on either table now breaks the test.
- [x] [Review][Defer → surfaced] **Pre-existing, non-ETF trade-persistence quirk.** The Edge Case Hunter flagged that the test never asserted trade rows persisted (AC1 "results record"). Adding that assertion revealed a **real, pre-existing** behaviour: for a run that ends with an **open** position (the IVV zigzag does), `BacktestPersistenceService.save_trades_from_positions` raises `ValidationError: cannot convert float NaN to integer` (open-position exit fields are NaN), which `_persist_results` **swallows by design** (logs a warning, still commits run + metrics). So the run + `performance_metrics` persist reliably (AC1's "results record"), but **trade rows do not** for open-position runs. This is **not ETF-specific** (it hits any asset class equally) and **not a Story 5.4 regression** — it surfaces now only because 5.1/5.2 ran `persist=False`, so the catalog trade-save path was never exercised before. Fixing it is a production change to a shared path (would alter trade-persistence behaviour for all asset classes) — **out of scope for this test-only lock-in story**; DEFERRED as pre-existing hygiene (candidate for a follow-up, e.g. Story 5.5 verification or an Epic-5 retro item). The test therefore asserts the reliably-persisted run + metrics record and does **not** assert on best-effort trades.
- [x] [Review][Dismiss] "Read-back could miss the second (stock) write under REPEATABLE READ / a pinned snapshot" [Blind Hunter] — verified non-issue: the engine sets no `isolation_level`, PostgreSQL defaults to READ COMMITTED (each SELECT takes a fresh snapshot), and this mirrors the exact schema-isolation fixture already proven in `tests/integration/db/test_backtest_repository.py`. Confirmed by the passing suite (etf read → stock write → stock read all succeed).
- [x] [Review][Dismiss] "`_persisted_columns` parity is data-dependent/fragile given ETF (2mo) vs Stock (10d) windows" [Blind Hunter] — verified deterministic: the only conditionally-null `BacktestRun` columns (`error_message`, `reproduced_from_run_id`, `data_quality_flag`) are `None` for both success runs, and `config_snapshot` cannot diverge because `_validate_config_snapshot` runs it through `StrategyConfigSnapshot(...).model_dump()` which drops `equity_curve`/`config_file_path` (extra="ignore"). Both persist the same non-None column set.
- [x] [Review][Dismiss] "The monkeypatched `get_session` double never commits" [Blind Hunter] — verified: `_persist_results` commits internally (`await session.commit()` at backtest_orchestrator.py:419) before returning, so the double correctly need not; the read-back proves durability.
- [x] [Review][Dismiss] "`to_detail_view` / `run.metrics` could raise `MissingGreenlet` on the async session" [Blind Hunter] — verified: `metrics` and `trades` are `lazy="selectin"` (backtest.py:126,132) and `find_by_run_id` also `selectinload(BacktestRun.metrics)`, so both eager-load during the awaited query; `to_detail_view` touches only `run.metrics` + scalars (never `run.trades`). No lazy IO on attribute access.
- [x] [Review][Dismiss] "`database_url.replace('postgresql://', ...)` no-ops on other URL schemes" [Blind Hunter] — copies the exact, established pattern in `tests/integration/db/test_backtest_repository.py`; the project's `database_url` is `postgresql://`. Not introduced by this story. (The Pyright `Optional.replace` note is IDE-only; the project gate is `mypy .`, which passes clean.)

## Dev Notes

### The core idea (why this story is verification/lock-in)
The persist + display path is **already 100% asset-class-agnostic** — persisting an ETF run requires **zero** production code. `asset_class` lives only in the *loader/metadata* layer (`src/services/firstrate/*`, `src/services/metadata/*`, `catalog_instruments`/`instrument_metadata`); it is **erased at load time** — `load_from_catalog` → `build_equity` synthesises a plain whole-share `Equity`, and everything downstream (`BacktestOrchestrator.execute` → `_persist_results` → `BacktestPersistenceService` → `BacktestRepository` → `backtest_runs`/`performance_metrics`/`trades`) sees only *bars + an `Equity` + a symbol string*. The run record stores `instrument_symbol` (free-text, e.g. `"IVV"`) and nothing about asset class. So Story 5.4 ships **no production code** — it *locks the contract in*: an ETF run persists identically to a Stock and is viewable via the identical web read path, so no future change can slip an asset-class branch into persistence or display. [Source: backtest_orchestrator.py `_persist_results` lines 345-421; db/models/backtest.py; grep for `asset_class` on the persist/display path → none]

### The seam (what is exercised, end to end)
```
catalog_name + ETF ticker (IVV)
    │  load_from_catalog → build_equity(nautilus_id) → Equity (whole-share, USD)
    ▼
BacktestOrchestrator.execute(request, bars, Equity, persist=True)
    │  run()  → _extract_results → result
    │  _persist_results:
    │      async with get_session() as session:          ← app path; monkeypatched to TEST SCHEMA in the test
    │          BacktestRepository(session)
    │          BacktestPersistenceService(repository).save_backtest_results(
    │              instrument_symbol=request.symbol="IVV",
    │              data_source="catalog:e2e-test", ...)   → backtest_runs row  (AC1)
    │              create_performance_metrics(...)        → performance_metrics row (1:1)
    │          save_trades_from_positions(...)            → trades rows
    │          session.commit()
    ▼
run_id (non-None)                                          (AC1)
    │  READ-BACK (web read path):
    │      BacktestQueryService.get_backtest_by_id(run_id) → find_by_run_id (metrics eager)
    │      to_detail_view(run).configuration.instrument_symbol == "IVV"   (AC2)
    ▼
ETF run recorded + displayed identically to a Stock       (AC3 — same tables/columns, no asset_class)
```
[Source: backtest_orchestrator.py lines 138-145, 345-421; backtest_query.py lines 53-70; backtest_detail.py lines 488-522, 555-588]

### Why the monkeypatch on `get_session` (and why it's honest)
`_persist_results` deliberately opens its **own** session (`async with get_session() as session:` at backtest_orchestrator.py:386) rather than accepting an injected one, and it **commits** internally. To make the write land where the test can read it back — and never touch the real app DB — the test replaces `src.core.backtest_orchestrator.get_session` (the name the orchestrator imported at line 33) with an `@asynccontextmanager` that yields a session bound to the per-worker **test schema** (same `async_test_engine`/schema the read session uses). The write commits; a separate read session on the same schema sees it (committed cross-session visibility). This mirrors the schema-isolation pattern already proven in `tests/integration/db/test_backtest_repository.py`. **Note:** `_persist_results` swallows persistence exceptions (logs a warning, does not re-raise) — so `run_id` alone is *not* proof of a DB write; the **read-back** (`find_by_run_id(run_id) is not None`) is the real assertion, and it also catches a silently-failed persist.

### Scope boundaries (do NOT do here)
- **No production edits** unless a test proves a real defect (then STOP + surface). This is a lock-in story; the persist path already handles ETFs.
- **No Alembic migration, no `src/db/models/**` change, no results-DB schema change** — the existing `backtest_runs`/`performance_metrics`/`trades` schema already stores everything an ETF run needs (`instrument_symbol` carries the ticker). **STOP and surface** if one appears necessary (hard harness constraint).
- **No `asset_class` branch** in the orchestrator, persistence service, repository, models, query service, or detail mapper — introducing one would violate NFR16/ADR-10 and this story's own AC3.
- **No new web/CLI surface** — the list/detail routes + templates already render any run agnostically; do not add ETF-specific UI. The web-read assertion stays at the query-service + `to_detail_view` tier (the objects the route builds), not a browser pass.
- **No multi-ETF orchestration work** — Story 5.3 owns the `instrument_ids` list; AC1 says "single or multi", and a single-ETF persist proves the record path, which is identical for a multi-run (still one `backtest_runs` row per run).
- **No reference-comparison tooling** — Story 5.5.
- **Never touch live data** — bars generated in-process; DB writes go to the isolated `test_<worker>` schema only, never the app DB.

### Testing standards summary
- Tier: **`--forked` integration** — a real `BacktestEngine.run()` (Nautilus C/Rust `fork()` isolation) **and** a real Postgres round-trip are both required to prove persistence; component/mocked tiers cannot prove a real DB record. Reuse the schema-isolation fixtures from `tests/integration/db/test_backtest_repository.py`. [Source: CLAUDE.md test tiers; docs/agent/testing.md; feedback_postgres_setup]
- TDD Red→Green: write the `find_by_run_id(run_id)` read-back + parity assertions first (they fail if the run is not persisted); the "no `asset_class` column" and parity assertions LOCK IN the contract and may pass immediately — expected for a verification story (mirror Story 5.1 Task 2 / 5.2).
- Reuse existing helpers: `synthetic_catalog` (already has `IVV.ARCA` + `AAPL.NASDAQ`), `_make_zigzag_bars`, `_make_instrument_row`, `_build_sma_request` (extend for `persist`). Extend, don't rewrite.

### Project Structure Notes
- Touch points (tests only): `tests/integration/core/test_backtest_catalog_integration.py` (additive fixtures + two tests + one `_build_sma_request` kwarg). No production files (architecture.md line 565 "No new files").
- The DB fixtures duplicate the pattern in `tests/integration/db/test_backtest_repository.py`; a shared conftest is **not** required for two tests and would broaden the diff — keep them local to the module (mirror how `test_backtest_repository.py` keeps them local).

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 5.4 (lines 839-853); FR37 (line 72); Epic 5 line 150 "Results persist via the existing DB path"]
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-10; line 565 (E5 "No new files")]
- [Source: src/core/backtest_orchestrator.py (`execute` lines 87-164, `_persist_results` lines 345-421)]
- [Source: src/services/backtest_persistence.py (`save_backtest_results` lines 52-132)]
- [Source: src/db/repositories/backtest_repository.py (`create_backtest_run` lines 48-118, `find_by_run_id` lines 221-240)]
- [Source: src/db/models/backtest.py (`BacktestRun` line 33, `PerformanceMetrics` line 167); src/db/models/trade.py]
- [Source: src/services/backtest_query.py (`get_backtest_by_id` lines 53-70)]
- [Source: src/api/models/backtest_detail.py (`to_detail_view` lines 555-588, `build_configuration` lines 488-522)]
- [Source: src/db/session.py (`get_session` lines 47-54)]
- [Source: tests/integration/core/test_backtest_catalog_integration.py; tests/integration/db/test_backtest_repository.py (schema-isolation fixtures lines 15-70)]
- [Source: 5-1-serve-etf-catalog-data-to-the-backtestengine.md, 5-2-run-a-single-etf-backtest-with-whole-share-equity-sizing.md (Epic 5 no-adapter routing + whole-share sizing precedent)]

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/integration/core/test_backtest_catalog_integration.py --forked -q` → 10 passed (8 existing + the 2 new persistence/round-trip tests), including after the post-review patch (AC3 schema-level tripwires for `performance_metrics`/`trades`).
- `uv run ruff check .` → All checks passed. `uv run mypy .` → Success: no issues found in 360 source files (no new errors vs baseline).
- `git status --short` / `git diff --stat` → only the one integration test file (M) + this story (A) + sprint-status (M). No `src/`, `alembic/`, or `src/db/models/**` change (AC4 holds).

### Completion Notes List

- **Zero production change — verification/lock-in story.** The persist + display path was already 100% asset-class-agnostic: `asset_class` lives only in the loader/metadata layer and is erased at load time — `load_from_catalog` → `build_equity` yields a plain `Equity`, and everything downstream (`BacktestOrchestrator._persist_results` → `BacktestPersistenceService` → `BacktestRepository` → `backtest_runs`/`performance_metrics`/`trades`) sees only bars + an `Equity` + a free-text `instrument_symbol`. An ETF run persists and displays identically to a Stock with no code change. This story locks that in with integration coverage; `git diff --stat` shows only the one test file + this story + sprint-status.
- **Real round-trip is the proof (AC1).** `_persist_results` opens its own session via `src.db.session.get_session` and commits internally — and swallows persistence exceptions — so `run_id is not None` alone is NOT proof. The test monkeypatches `src.core.backtest_orchestrator.get_session` to a schema-isolated write session, then reads the record back with a **separate** session via `BacktestRepository.find_by_run_id(run_id)` and asserts `instrument_symbol == "IVV"`, `execution_status == "success"`, `data_source == "catalog:e2e-test"`, and a 1:1 `performance_metrics` row (`run.metrics is not None`).
- **ETF ↔ Stock parity (AC3).** A Stock (`AAPL.NASDAQ`) is persisted through the identical path; the two `BacktestRun` records populate the **same** non-None column set (`_persisted_columns`), and neither carries an `asset_class` attribute — so a future asset-class branch in the persist path breaks the test.
- **Web-viewable via the exact read path (AC2).** `BacktestQueryService.get_backtest_by_id(run_id)` (what the FastAPI detail route calls) returns the run with metrics eager-loaded, and `to_detail_view(run).configuration.instrument_symbol == "IVV"` — the ETF ticker renders plainly (no `kraken` venue-append, no asset-class branch). Kept at the query-service + presentation-mapper tier (the objects the route builds); a browser pass is out of scope for a lock-in story with an unchanged route/template.
- **No schema change, no migration, no live data.** Table creation in the test uses `Base.metadata.create_all` in a throwaway per-worker `test_<worker>` schema (test scaffolding, not an Alembic migration); DB writes go only to that schema, never the app DB; bars are generated in-process. No `src/db/models/**` change (Story 5.4 persists via the existing schema).
- **Surfaced (deferred) — best-effort trade persistence on open-position runs.** Review probing revealed a pre-existing, non-ETF quirk: `save_trades_from_positions` raises `ValidationError: cannot convert float NaN to integer` for a run ending on an **open** position (exit fields NaN), and `_persist_results` swallows it — so the run + metrics persist but trade rows do not. Not ETF-specific, not a 5.4 regression (surfaces now only because 5.1/5.2 ran `persist=False`). Fixing it is a production change to a shared path (out of this test-only story's scope) — DEFERRED as pre-existing hygiene. See Review Findings. The tests assert the reliably-persisted run + metrics record (AC1's "results record") and do not assert on best-effort trades.

### File List

- `tests/integration/core/test_backtest_catalog_integration.py` (M) — schema-isolation fixtures (`get_worker_id`, `_test_schema_name`, `async_test_engine`, `async_session`) + `_persisted_columns` helper; `_build_sma_request` gains `persist` kwarg; new `TestNamedCatalogEtfPersistence` (ETF persist round-trip + ETF↔Stock parity + web-read-path viewability).
- `_bmad-output/implementation-artifacts/5-4-persist-etf-backtest-results-to-the-database.md` (A) — this story.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (M) — 5-4 → review (epic-5 already in-progress).
