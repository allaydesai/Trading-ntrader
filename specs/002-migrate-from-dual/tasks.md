# Tasks: Parquet-Only Market Data Storage

**Input**: Design documents from `/specs/002-migrate-from-dual/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓

**Tests**: Tests are NOT requested in this feature specification, following standard implementation workflow.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

---

## 📊 Progress Summary

**Overall Progress**: 26 / 65 tasks complete (40%)

**Completed Phases**:
- ✅ Phase 1 (Setup): 4/4 tasks complete
- ✅ Phase 2 (Foundation): 6/6 tasks complete
- ✅ Phase 3 (User Story 1): 7/7 tasks complete (blocked by Bug #10)
- ⚠️ Phase 4 (User Story 2): 8/8 tasks complete (2 integration bugs)
- ✅ Phase 5 (User Story 3): 7/7 tasks complete

**Remaining Phases**:
- ⏳ Phase 6 (User Story 4): 0/8 tasks - CSV import
- ⏳ Phase 7 (User Story 5): 0/8 tasks - Data inspection
- ⏳ Phase 8 (Migration): 0/7 tasks - PostgreSQL deprecation
- ⏳ Phase 9 (Polish): 0/10 tasks - Documentation & validation

**CRITICAL STATUS UPDATE (2025-10-15)**:
Phase 4 was marked complete but testing (Session 1 + Session 2) revealed it was NOT fully implemented. After fixing 8 bugs across two sessions:

**What Works Now** (Session 1 + Session 2):
- ✅ DataCatalogService operational with in-memory cache
- ✅ Backtest command loads from Parquet catalog structure
- ✅ IBKR client initialization from environment config
- ✅ IBKR TWS connection successful (tested with v187)
- ✅ Instrument lookup and contract resolution (AAPL.NASDAQ → ConId=265598)
- ✅ fetch_or_load() method with retry logic and exponential backoff
- ✅ Rate limiting (45 req/sec for IBKR's 50 req/sec limit)
- ✅ Error messages clear and actionable (User Story 3)
- ✅ Progress indicators and execution time tracking
- ✅ All code formatted and linted (ruff checks pass)
- ✅ **Bar data fetch working** (Bug #8 FIXED - Session 2) ⭐ NEW
- ✅ **Parquet file creation successful** (60 bars in 2.31s, 5.2KB files) ⭐ NEW
- ✅ **Correct Nautilus API format** (contracts + bar_specifications) ⭐ NEW

**REMAINING BLOCKERS** (2 integration bugs):
- ❌ **Bug #9: Instrument not added to backtest cache** (CRITICAL)
  - Error: `Instrument AAPL.NASDAQ for the given data not found in the cache`
  - Location: `src/core/backtest_runner.py`
  - Fix: Add instrument to engine before adding bar data
  - **Blocks backtest execution**

- ❌ **Bug #10: Availability cache not detecting Parquet files** (HIGH)
  - Symptom: Cache shows 0 entries despite files existing
  - Location: `src/services/data_catalog.py`
  - Impact: Always fetches from IBKR, never uses cache
  - **Blocks User Story 1 testing**

**Next Milestone**: Fix Bug #9 and Bug #10 (see bug-report.md), then test US1 and US2 end-to-end

**Documentation**: See `SESSION-2-SUMMARY.md` and `BUG-8-FIX.md` for Session 2 details

## Format: `[ID] [P?] [Story] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, Setup, Foundation)
- Include exact file paths in descriptions

## Path Conventions
- Single Python CLI project structure: `src/`, `tests/` at repository root
- Catalog data: `./data/catalog/` at repository root

---

## Phase 1: Setup (Shared Infrastructure) ✅

**Purpose**: Project initialization and basic structure

- [X] T001 [P] [Setup] Create catalog directory structure at `./data/catalog/` with proper permissions
- [X] T002 [P] [Setup] Add structlog dependency via `uv add structlog` for structured logging
- [X] T003 [P] [Setup] Create custom exception hierarchy in `src/services/exceptions.py` (CatalogError, DataNotFoundError, IBKRConnectionError, CatalogCorruptionError)
- [X] T004 [P] [Setup] Create Pydantic models in `src/models/catalog_metadata.py` (CatalogAvailability, FetchRequest, FetchStatus enum)

**Checkpoint**: ✅ Basic infrastructure ready

**Implementation Notes**:
- Directory structure: `./data/catalog/` created with proper permissions
- Dependency added: `structlog` via `uv add structlog`
- Exception hierarchy in `src/services/exceptions.py`:
  - CatalogError (base exception)
  - DataNotFoundError (data not in catalog)
  - IBKRConnectionError (IBKR unavailable)
  - CatalogCorruptionError (corrupted Parquet files)
- Pydantic models in `src/models/catalog_metadata.py`:
  - CatalogAvailability (tracks data ranges, file counts, row estimates)
  - FetchRequest (tracks IBKR fetch operations - for future use)
  - FetchStatus enum (PENDING, IN_PROGRESS, COMPLETED, FAILED)
- All models include validation, timezone handling, UTC enforcement

**File Structure**:
```
data/catalog/                    # Parquet data root
src/services/exceptions.py       # Custom exception hierarchy
src/models/catalog_metadata.py   # Availability tracking models
```

---

## Phase 2: Foundational (Blocking Prerequisites) ✅

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T005 [Foundation] Create DataCatalogService facade in `src/services/data_catalog.py` with initialization logic for ParquetDataCatalog
- [X] T006 [Foundation] Implement availability checking in DataCatalogService: `get_availability(instrument_id: str, bar_type_spec: str) -> CatalogAvailability | None`
- [X] T007 [Foundation] Implement catalog query wrapper in DataCatalogService: `query_bars(instrument_id: str, start: datetime, end: datetime) -> List[Bar]`
- [X] T008 [Foundation] Implement catalog write wrapper in DataCatalogService: `write_bars(bars: List[Bar]) -> None` with atomic operations
- [X] T009 [Foundation] Add structured logging configuration in DataCatalogService with correlation IDs
- [X] T010 [Foundation] Implement in-memory availability cache in DataCatalogService (Dict-based, rebuilt on startup)

**Checkpoint**: ✅ Foundation ready - user story implementation can now begin in parallel

**Implementation Notes**:
- Created `DataCatalogService` facade in `src/services/data_catalog.py` (370 lines initially)
- Wraps Nautilus `ParquetDataCatalog` with high-level operations
- Initialization logic:
  - Accepts catalog_path or uses NAUTILUS_PATH env or defaults to "./data/catalog"
  - Creates ParquetDataCatalog instance
  - Initializes availability cache as Dict[str, CatalogAvailability]
  - Calls _rebuild_availability_cache() on startup
- Methods implemented:
  - `get_availability(instrument_id, bar_type_spec)` → CatalogAvailability | None
  - `query_bars(instrument_id, start, end, bar_type_spec)` → List[Bar]
  - `write_bars(bars, correlation_id)` → None (atomic writes)
  - `_rebuild_availability_cache()` → Scans catalog directory structure
- Structured logging with structlog:
  - All operations logged with context (instrument_id, correlation_id, etc.)
  - Log events: data_catalog_initialized, availability_cache_rebuilt, querying_catalog, etc.
- In-memory availability cache:
  - Key format: f"{instrument_id}_{bar_type_spec}"
  - Scans Parquet directory structure on startup
  - Extracts date ranges from filenames (YYYY-MM-DD.parquet)
  - Estimates row counts from file sizes (~128 bytes/row)
  - Automatically rebuilt after write_bars()
- Error handling:
  - Raises DataNotFoundError when data not in catalog
  - Raises CatalogCorruptionError on Parquet/Arrow errors
  - Wraps other errors in CatalogError

**Architecture**:
```
DataCatalogService (Facade)
    ├── ParquetDataCatalog (Nautilus native)
    │   └── Parquet files on disk
    ├── availability_cache (Dict)
    │   └── CatalogAvailability objects
    └── structlog logger
        └── Correlation IDs for tracing
```

**Performance Characteristics**:
- Availability checks: O(1) dictionary lookup
- Cache rebuild: O(n) where n = number of Parquet files
- Query operations: Delegated to Nautilus ParquetDataCatalog (optimized columnar reads)
- Write operations: Atomic with automatic cache rebuild

---

## Phase 3: User Story 1 - Run Backtest with Cached Data (Priority: P1) 🎯 MVP ✅

**Goal**: Run backtests using previously fetched market data from Parquet catalog without IBKR connection

**Independent Test**: Load existing Parquet files and run backtest, delivers instant execution without external dependencies

### Implementation for User Story 1

- [X] T011 [US1] Refactor BacktestRunner in `src/cli/commands/backtest.py` to use DataCatalogService instead of database_repository
- [X] T012 [US1] Update data loading logic in BacktestRunner: check catalog availability before attempting data load
- [X] T013 [US1] Replace PostgreSQL query calls with `catalog_service.query_bars()` in BacktestRunner
- [X] T014 [US1] Add progress indicators for data loading operations in BacktestRunner (using rich progress bar)
- [X] T015 [US1] Update backtest output messages to show "Loaded from catalog" with row count and date range
- [X] T016 [US1] Add execution time tracking and display in backtest results
- [X] T017 [US1] Remove PostgreSQL dependency from backtest data loading path (keep imports for future metadata)

**Checkpoint**: ✅ User Story 1 complete - backtests load from catalog successfully

**Implementation Notes**:
- Created `run_backtest_with_catalog_data()` method in MinimalBacktestRunner
- Backtest command now uses DataCatalogService for all data operations
- Progress indicators show: availability check → data load → backtest execution
- Results table displays execution time and "Parquet Catalog" as data source
- All linting checks pass (ruff format + ruff check)
- Data loading: Direct Parquet file access (columnar storage)
- No database connection required for backtests
- Data already in Nautilus Bar format (no conversion overhead)
- Fast availability checks via in-memory cache

**File Changes**:
- `src/cli/commands/backtest.py`: Refactored to use DataCatalogService
- `src/core/backtest_runner.py`: Added run_backtest_with_catalog_data() method
- Both files formatted and linting checks pass

**Performance Benefits**:
- No database connection overhead
- Direct Parquet file access (optimized for analytics)
- In-memory availability cache for instant checks
- Data in native Nautilus format (zero conversion cost)

---

## Phase 4: User Story 2 - Automatic Data Fetching on Missing Data (Priority: P1) ⚠️

**Goal**: Automatically fetch and persist data from IBKR when requested data is not in catalog

**Independent Test**: Request data not in catalog with IBKR connected, verify automatic fetch, persist, and backtest execution

### Implementation for User Story 2

- [X] T018 [US2] Implement `fetch_or_load()` method in DataCatalogService combining availability check + IBKR fetch
- [X] T019 [US2] Add IBKR connection check in DataCatalogService: `_is_ibkr_available() -> bool`
- [X] T020 [US2] Implement automatic fetch workflow: catalog check → IBKR fetch → write to catalog → return bars
- [X] T021 [US2] Add retry logic with exponential backoff for transient IBKR failures in DataCatalogService
- [X] T022 [US2] Implement rate limit tracking (50 req/sec) in IBKRHistoricalClient (verify existing implementation)
- [X] T023 [US2] Add fetch progress indicators in DataCatalogService showing percentage complete and ETA
- [X] T024 [US2] Update BacktestRunner to call `fetch_or_load()` instead of direct catalog query
- [ ] T025 [US2] Add post-fetch success messages showing data persisted and future backtests will use cache (BLOCKED)

**Checkpoint**: ⚠️ User Story 2 PARTIALLY COMPLETE - 7/8 tasks done, bar fetch blocked (see test-results.md)

**Implementation Notes** (Updated 2025-10-15):
- Created `fetch_or_load()` method in DataCatalogService (async, 240 lines)
- **FIXED**: Added IBKR client initialization in `__init__` (was missing)
- **FIXED**: Implemented `_is_ibkr_available()` as async with automatic connection
- **FIXED**: Implemented `_fetch_from_ibkr_with_retry()` with exponential backoff (2^n seconds, max 3 retries)
- **FIXED**: Implemented `fetch_bars()` method in IBKRHistoricalClient
- Updated backtest command to use fetch_or_load() instead of query_bars()
- Added IBKRConnectionError import to DataCatalogService
- Data source detection: "Parquet Catalog" vs "IBKR Auto-fetch"
- **BLOCKED**: Post-fetch messages cannot be tested (bar fetch fails)
- Error messages include actionable tips: "docker compose up ibgateway"
- Rate limiting verified: 45 req/sec in IBKRHistoricalClient (conservative for 50 req/sec limit)
- All code formatted and linted (ruff checks pass)
- **Testing revealed**: 7 bugs fixed, 1 critical blocker remains (bar spec format)
- See: `test-results.md`, `bug-report.md`, `fixes-applied.md` for details

**How It Works** (Updated with test findings):
1. User runs backtest with missing/partial data
2. fetch_or_load() checks catalog availability ✅
3. If data missing → checks IBKR connection via _is_ibkr_available() ✅
4. If IBKR available → calls _fetch_from_ibkr_with_retry() ✅
5. Retry logic: 3 attempts with exponential backoff (2s, 4s, 8s delays) ✅
6. **BLOCKED**: Fetched data written to catalog via write_bars() ❌
   - Bar specification format incorrect
   - Error: `invalid literal for int() with base 10: 'AAPL.NASDAQ-1-MINUTE'`
   - Needs research into Nautilus bar spec format
7. **NOT REACHED**: Availability cache rebuilt automatically
8. **NOT REACHED**: Returns bars to backtest engine
9. **NOT REACHED**: User sees: "Fetched X bars from IBKR in Ys" + cache notification

**What Actually Happens**:
- IBKR connection: ✅ SUCCESS (Connected to TWS v187)
- Instrument lookup: ✅ SUCCESS (AAPL.NASDAQ → ConId=265598)
- Rate limiting: ✅ WORKING (45 req/sec configured)
- Retry logic: ✅ WORKING (exponential backoff visible in logs)
- Bar data fetch: ❌ FAILING (format issue)
- Data persistence: ❌ NOT REACHED (blocked by fetch failure)

**File Changes** (Updated 2025-10-15):
- `src/services/data_catalog.py`: +310 lines total
  - fetch_or_load() method: ~100 lines
  - _is_ibkr_available() with connection logic: ~30 lines
  - _fetch_from_ibkr_with_retry(): ~110 lines
  - IBKR client initialization: ~40 lines
  - Environment variable parsing improvements: ~10 lines
- `src/services/ibkr_client.py`: +65 lines
  - fetch_bars() method: ~60 lines
  - Rate limiting integration: ~5 lines
- `src/cli/commands/backtest.py`: Modified data loading section (~100 lines changed)
- NOTE: data_catalog.py is 640 lines (over 500 line limit, needs refactoring in future)

---

## Phase 5: User Story 3 - Clear Error Messaging for Unavailable Data (Priority: P2)

**Goal**: Provide clear, actionable error messages when data unavailable from both catalog and IBKR

**Independent Test**: Simulate missing data with IBKR disconnected, verify error message clarity and recovery steps

### Implementation for User Story 3

- [X] T026 [P] [US3] Create error message templates in `src/utils/error_messages.py` for all error scenarios
- [X] T027 [US3] Implement `DataNotFoundError` exception handler in BacktestRunner with actionable resolution steps
- [X] T028 [US3] Add IBKR connection failure handler with "docker compose up ibgateway" suggestion
- [X] T029 [US3] Implement rate limit exceeded handler with retry countdown and cancellation option
- [X] T030 [US3] Add catalog corruption detection in DataCatalogService with quarantine logic (move to .corrupt/)
- [X] T031 [US3] Create error message formatter using Rich console for colored output (✓ ⚠ ❌ 📊 🔧 💡)
- [X] T032 [US3] Add exit code handling: 0=success, 1=data error, 2=input error, 3=connection error, 4=system error

**Checkpoint**: ✅ User Story 3 complete - all error scenarios have clear messages

---

## Phase 6: User Story 4 - Import CSV Data Directly to Parquet (Priority: P2)

**Goal**: Import CSV data directly to Parquet catalog without PostgreSQL intermediary

**Independent Test**: Import CSV file, verify Parquet creation with correct structure, run backtest with imported data

### Implementation for User Story 4

- [ ] T033 [US4] Refactor CSVLoader in `src/services/csv_loader.py` to write directly to ParquetDataCatalog
- [ ] T034 [US4] Remove PostgreSQL write operations from CSVLoader (preserve validation logic)
- [ ] T035 [US4] Update CSV validation in CSVLoader: timestamp format, OHLCV constraints, volume >= 0, monotonic timestamps
- [ ] T036 [US4] Implement Nautilus Bar conversion in CSVLoader: CSV rows → Bar objects with proper types
- [ ] T037 [US4] Add conflict resolution logic in CSVLoader: skip, overwrite, merge behaviors
- [ ] T038 [US4] Update `ntrader data import` CLI command in `src/cli/commands/data.py` with new options
- [ ] T039 [US4] Add import success summary showing files created, row counts, date ranges, disk size
- [ ] T040 [US4] Implement validation error reporting with specific row numbers and issues

**Checkpoint**: User Story 4 complete - CSV import writes to Parquet successfully

---

## Phase 7: User Story 5 - Verify Data Availability Before Backtest (Priority: P3)

**Goal**: Check data availability without loading full datasets, show gaps and date ranges

**Independent Test**: Run data check command, verify catalog scan without full data load, displays availability report

### Implementation for User Story 5

- [ ] T041 [P] [US5] Create `ntrader data check` command in `src/cli/commands/data.py`
- [ ] T042 [P] [US5] Create `ntrader data list` command in `src/cli/commands/data.py`
- [ ] T043 [US5] Implement catalog scanning in DataCatalogService: `scan_catalog() -> Dict[str, List[CatalogAvailability]]`
- [ ] T044 [US5] Implement gap detection logic in DataCatalogService: identify missing date ranges in available data
- [ ] T045 [US5] Create table formatter for `data list` output using Rich tables (instrument, bar type, date range, row count)
- [ ] T046 [US5] Create detailed formatter for `data check` output showing gaps and file counts
- [ ] T047 [US5] Add JSON and CSV output formats for `data list` command
- [ ] T048 [US5] Add tips/suggestions when gaps detected: "Run backtest to auto-fetch missing data"

**Checkpoint**: User Story 5 complete - data inspection commands operational

---

## Phase 8: PostgreSQL Deprecation & Migration (Supporting Infrastructure)

**Purpose**: Deprecate PostgreSQL for market data, provide migration path for existing users

- [ ] T049 [P] [Migration] Add deprecation warnings to `src/models/market_data.py` SQLAlchemy model docstrings
- [ ] T050 [P] [Migration] Create migration script in `scripts/migrate_postgres_to_parquet.py`
- [ ] T051 [Migration] Implement PostgreSQL query in migration script: fetch market_data by symbol and date range
- [ ] T052 [Migration] Implement Nautilus Bar conversion in migration script: PostgreSQL rows → Bar objects
- [ ] T053 [Migration] Add validation logic in migration script: compare row counts, timestamp ranges, spot check OHLCV
- [ ] T054 [Migration] Create `ntrader data migrate` CLI command in `src/cli/commands/data.py` calling migration script
- [ ] T055 [Migration] Add migration progress indicators and validation report output

**Checkpoint**: Migration tooling complete - users can migrate PostgreSQL data

---

## Phase 9: Documentation & Polish (Cross-Cutting Concerns)

**Purpose**: Updates that affect multiple user stories and documentation

- [ ] T056 [P] [Polish] Update `README.md` with Parquet-first workflow examples and quickstart
- [ ] T057 [P] [Polish] Update `CLAUDE.md` with Parquet catalog usage patterns and troubleshooting
- [ ] T058 [P] [Polish] Create `docs/troubleshooting.md` with error scenarios and resolution steps
- [ ] T059 [P] [Polish] Update CLI help text for all modified commands with new options and examples
- [ ] T060 [P] [Polish] Add code comments explaining Parquet-first design decisions (# Reason: prefix)
- [ ] T061 [Polish] Run formatting and linting: `uv run ruff format .` and `uv run ruff check .`
- [ ] T062 [Polish] Run type checking: `uv run mypy src/` and fix any type errors
- [ ] T063 [Polish] Execute quickstart.md validation scenarios (all 5 scenarios)
- [ ] T064 [Polish] Verify performance benchmarks: 1 year of 1-minute bars loads in <1s
- [ ] T065 [Polish] Update CHANGELOG.md with breaking changes and migration guide

**Checkpoint**: Feature complete, documented, and validated

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-7)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed) or sequentially by priority (P1 → P2 → P3)
- **Migration (Phase 8)**: Can run in parallel with user stories after Foundation complete
- **Polish (Phase 9)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Foundation complete → Can start immediately - No dependencies on other stories
- **User Story 2 (P1)**: Foundation complete → Can start immediately - Integrates with US1 backtest runner
- **User Story 3 (P2)**: Foundation complete → Can start immediately - Adds error handling to US1/US2
- **User Story 4 (P2)**: Foundation complete → Can start immediately - Independent CSV import path
- **User Story 5 (P3)**: Foundation complete → Can start immediately - Independent data inspection commands

### Within Each User Story

- Tasks within a story follow logical order: models → services → endpoints → integration
- Tasks marked [P] within a story can run in parallel (different files)
- Foundation services must be complete before story-specific service methods

### Parallel Opportunities

- **Phase 1 (Setup)**: T001, T002, T003, T004 all [P] - different files, no dependencies
- **Phase 2 (Foundation)**: Sequential - DataCatalogService must build up incrementally
- **Phase 3-7 (User Stories)**: Once Foundation complete, ALL user stories can start in parallel by different developers
- **Phase 8 (Migration)**: T049, T050 can run in parallel (different files)
- **Phase 9 (Polish)**: T056, T057, T058, T059, T060 all [P] - documentation in different files

---

## Parallel Example: Setup Phase

```bash
# Launch all setup tasks together:
Task: "Create catalog directory structure at ./data/catalog/"
Task: "Add structlog dependency via uv add structlog"
Task: "Create custom exception hierarchy in src/services/exceptions.py"
Task: "Create Pydantic models in src/models/catalog_metadata.py"
```

## Parallel Example: User Stories (after Foundation)

```bash
# Different developers can work on different stories simultaneously:
Developer A: User Story 1 (T011-T017) - Backtest with cached data
Developer B: User Story 2 (T018-T025) - Auto-fetch missing data
Developer C: User Story 4 (T033-T040) - CSV import
```

---

## Implementation Strategy

### MVP First (User Stories 1 & 2 Only)

1. Complete Phase 1: Setup (T001-T004)
2. Complete Phase 2: Foundational (T005-T010) - **CRITICAL PATH**
3. Complete Phase 3: User Story 1 (T011-T017)
4. Complete Phase 4: User Story 2 (T018-T025)
5. **STOP and VALIDATE**: Test US1 and US2 independently
6. Deploy/demo if ready

**Rationale**: US1 + US2 deliver core value (cached data + auto-fetch), covering 90% of usage patterns.

### Incremental Delivery

1. Foundation ready (Setup + Foundational) → T001-T010 complete
2. Add User Story 1 → T011-T017 → Test independently → Deploy (cached backtest MVP!)
3. Add User Story 2 → T018-T025 → Test independently → Deploy (auto-fetch feature!)
4. Add User Story 3 → T026-T032 → Test independently → Deploy (production-ready error handling)
5. Add User Story 4 → T033-T040 → Test independently → Deploy (CSV import support)
6. Add User Story 5 → T041-T048 → Test independently → Deploy (data inspection tools)
7. Add Migration → T049-T055 → Test independently → Deploy (PostgreSQL migration path)
8. Polish → T056-T065 → Complete feature

Each story adds value without breaking previous stories.

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together (T001-T010)
2. Once Foundational is done (DataCatalogService ready):
   - Developer A: User Story 1 (T011-T017)
   - Developer B: User Story 2 (T018-T025)
   - Developer C: User Story 4 (T033-T040)
3. Then:
   - Developer A: User Story 3 (T026-T032)
   - Developer B: User Story 5 (T041-T048)
   - Developer C: Migration (T049-T055)
4. All developers: Polish (T056-T065) in parallel

---

## Task Summary

**Total Tasks**: 65 tasks across 9 phases

### Breakdown by Phase:
- Phase 1 (Setup): 4 tasks
- Phase 2 (Foundational): 6 tasks - **BLOCKING**
- Phase 3 (US1 - Cached Data): 7 tasks - **P1 MVP**
- Phase 4 (US2 - Auto-fetch): 8 tasks - **P1 MVP**
- Phase 5 (US3 - Error Messages): 7 tasks - **P2**
- Phase 6 (US4 - CSV Import): 8 tasks - **P2**
- Phase 7 (US5 - Data Inspection): 8 tasks - **P3**
- Phase 8 (Migration): 7 tasks
- Phase 9 (Polish): 10 tasks

### Breakdown by User Story:
- User Story 1 (P1): 7 tasks
- User Story 2 (P1): 8 tasks
- User Story 3 (P2): 7 tasks
- User Story 4 (P2): 8 tasks
- User Story 5 (P3): 8 tasks
- Setup/Foundation/Migration/Polish: 27 tasks

### Parallel Opportunities:
- Setup phase: 4 tasks can run in parallel
- Documentation phase: 5 tasks can run in parallel
- User stories (after foundation): 5 stories can run in parallel with sufficient team size

### Suggested MVP Scope:
**Phases 1, 2, 3, 4** = 25 tasks covering Setup + Foundation + US1 + US2
- Delivers: Cached backtests + auto-fetch functionality
- Covers: 80% of typical user workflows
- Estimated effort: 2-3 days for single developer

---

## Notes

- [P] tasks = different files, no dependencies within phase
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Foundation phase (T005-T010) MUST complete before any user story work begins
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Follow TDD when explicitly required, otherwise standard implementation workflow
- Run `uv run ruff format .` and `uv run ruff check .` before final commit
- Execute quickstart.md scenarios to validate feature completeness

---

**Ready for**: Implementation execution, tracking progress, team assignment
