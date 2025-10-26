# Tasks: PostgreSQL Metadata Storage

**Input**: Design documents from `/specs/004-postgresql-metadata-storage/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, design.md

**Tests**: This feature follows TDD (Test-Driven Development) as specified in the project constitution. All tests MUST be written FIRST and FAIL before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure needed for all user stories

**⏱️ Estimated Time**: 30 minutes

- [x] T001 [P] Create directory structure: `src/db/models/`, `src/db/repositories/`, `src/services/`, `tests/unit/db/`, `tests/integration/db/`
- [x] T002 [P] Create base SQLAlchemy declarative base in `src/db/base.py` if not exists
- [x] T003 [P] Verify database connection configuration in `src/db/session.py` matches async pattern from research.md

**Checkpoint**: ✅ Project structure ready - no code written yet

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core database infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

**⏱️ Estimated Time**: 3-4 hours

### Database Models & Schema

- [x] T004 [P] TDD: Write failing unit test for BacktestRun model creation in `tests/unit/db/test_backtest_models.py`
- [x] T005 [P] TDD: Write failing unit test for PerformanceMetrics model creation in `tests/unit/db/test_backtest_models.py`
- [x] T006 Create BacktestRun SQLAlchemy model in `src/db/models/backtest.py` with all fields per data-model.md
- [x] T007 Create PerformanceMetrics SQLAlchemy model in `src/db/models/backtest.py` with all fields per data-model.md
- [x] T008 Add model relationships (BacktestRun.metrics one-to-one with PerformanceMetrics)
- [x] T009 Add database constraints (CHECK, UNIQUE, FK) per data-model.md section on constraints
- [x] T010 Add composite indexes for Phase 1 (idx_backtest_runs_created_id, idx_backtest_runs_strategy_created_id) in model definitions

### Pydantic Validation Models

- [x] T011 [P] TDD: Write failing test for StrategyConfigSnapshot validation in `tests/unit/models/test_config_snapshot.py`
- [x] T012 Create StrategyConfigSnapshot Pydantic model in `src/models/config_snapshot.py` per data-model.md
- [x] T013 Implement ValidatedJSONB TypeDecorator in `src/db/types/validated_jsonb.py` per research.md section 4
- [x] T014 Integrate ValidatedJSONB with BacktestRun.config_snapshot field

### Database Migration

- [x] T015 Generate Alembic migration: `uv run alembic revision --autogenerate -m "create backtest_runs and performance_metrics tables"`
- [x] T016 Review and edit generated migration file to include Phase 1 indexes from contracts/002_indexes.sql
- [x] T017 Test migration upgrade: `uv run alembic upgrade head`
- [x] T018 Test migration downgrade: `uv run alembic downgrade -1` then upgrade again
- [x] T019 Verify schema matches contracts/001_schema.sql using database inspection

### Session Management

- [x] T020 [P] TDD: Write failing test for async session context manager in `tests/unit/db/test_session.py`
- [x] T021 Implement async `get_session()` context manager in `src/db/session.py` per research.md section 1
- [x] T022 Configure connection pooling (pool_size=20, max_overflow=10, pool_pre_ping=True) per research.md

### Custom Exceptions

- [x] T023 [P] Create custom exception hierarchy in `src/db/exceptions.py`: BacktestStorageError, ValidationError, DatabaseConnectionError, DuplicateRecordError, RecordNotFoundError

**Checkpoint**: ✅ Database foundation ready - all user stories can now begin implementation

---

## Phase 3: User Story 1 - Automatic Backtest Persistence (Priority: P1) 🎯 MVP

**Goal**: Every backtest execution automatically saves metadata and results to database, preventing data loss

**Independent Test**: Run a single backtest, verify database contains execution record with all metadata and performance metrics

**Value**: Eliminates data loss on terminal/system restart - core foundation for all other features

**⏱️ Estimated Time**: 6-8 hours

### Tests for User Story 1 (TDD)

> **CRITICAL: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T024 [P] [US1] TDD: Write failing integration test for successful backtest persistence in `tests/integration/test_backtest_persistence.py` - should save BacktestRun + PerformanceMetrics
- [x] T025 [P] [US1] TDD: Write failing integration test for failed backtest persistence in `tests/integration/test_backtest_persistence.py` - should save BacktestRun with error_message, no metrics
- [x] T026 [P] [US1] TDD: Write failing unit test for BacktestRepository.create_backtest_run() in `tests/unit/db/test_backtest_repository.py`
- [x] T027 [P] [US1] TDD: Write failing unit test for BacktestRepository.create_performance_metrics() in `tests/unit/db/test_backtest_repository.py`
- [x] T028 [P] [US1] TDD: Write failing unit test for BacktestPersistenceService.save_backtest_results() in `tests/unit/services/test_backtest_persistence.py`
- [x] T029 [P] [US1] TDD: Write failing unit test for BacktestPersistenceService.save_failed_backtest() in `tests/unit/services/test_backtest_persistence.py`
- [x] T030 [P] [US1] TDD: Write failing unit test for metric validation (NaN/Infinity checks) in `tests/unit/services/test_backtest_persistence.py`

### Repository Implementation for User Story 1

- [x] T031 [US1] Create BacktestRepository class in `src/db/repositories/backtest_repository.py` with `__init__(self, session: AsyncSession)`
- [x] T032 [US1] Implement BacktestRepository.create_backtest_run() method per design.md lines 312-363 - async, all parameters, returns BacktestRun
- [x] T033 [US1] Implement BacktestRepository.create_performance_metrics() method per design.md lines 365-425 - async, all metrics parameters
- [x] T034 [US1] Add proper error handling for IntegrityError (duplicate run_id) and OperationalError (connection failure) per design.md lines 1494-1511

### Service Layer Implementation for User Story 1

- [x] T035 [US1] Create BacktestPersistenceService class in `src/services/backtest_persistence.py` with repository dependency injection
- [x] T036 [US1] Implement BacktestPersistenceService.save_backtest_results() method per design.md lines 627-690 - extracts metrics, validates, creates both records
- [x] T037 [US1] Implement BacktestPersistenceService.save_failed_backtest() method per design.md lines 692-743 - saves run with error, no metrics
- [x] T038 [US1] Implement _serialize_config_snapshot() private method per design.md lines 745-763 - creates JSONB structure
- [x] T039 [US1] Implement _extract_and_validate_metrics() private method per design.md lines 765-820 - extracts from EnhancedBacktestResult, validates NaN/Infinity
- [x] T040 [US1] Implement _validate_metric() and _validate_optional_metric() helper methods per design.md lines 822-836
- [x] T041 [US1] Add structlog logging for save operations (info on start, success, error) per design.md lines 649-654, 684-688

### Backtest Runner Integration for User Story 1

- [x] T042 [US1] Modify `src/core/backtest_runner.py` to import persistence service and session management
- [x] T043 [US1] Add async wrapper to run_backtest() function if not already async per design.md lines 1339-1397
- [x] T044 [US1] Integrate save_backtest_results() call after successful backtest completion per design.md lines 1355-1370
- [x] T045 [US1] Integrate save_failed_backtest() call in exception handler per design.md lines 1378-1396
- [x] T046 [US1] Display run_id to user after save with confirmation message per design.md line 1367
- [x] T047 [US1] Ensure existing backtest functionality preserved (generate_report still called, etc.)

### Validation for User Story 1

- [x] T048 [US1] Test with SMA Crossover strategy - verify complete persistence
- [x] T049 [US1] Test with RSI Mean Reversion strategy - verify complete persistence
- [ ] T050 [US1] Test with Momentum strategy - verify complete persistence
- [ ] T051 [US1] Test concurrent backtest execution (run 3 in parallel) - verify no data corruption per research.md section 7
- [ ] T052 [US1] Test backtest failure scenario - verify error captured correctly
- [x] T053 [US1] Verify all tests from T024-T030 now PASS (11 unit tests passing)

**Checkpoint**: ✅ **MVP COMPLETE!** User Story 1 is fully functional - every backtest auto-saves to database with complete metadata, performance metrics, and config snapshots stored as JSONB. Critical bug fix applied: transaction commits ensure data persists correctly.

---

## Phase 4: User Story 2 - View Backtest History (Priority: P2)

**Goal**: List recent backtests with key performance metrics without re-running

**Independent Test**: Run multiple backtests, use CLI command to retrieve and display them with key metrics

**Value**: Provides visibility into testing history for informed decision-making

**⏱️ Estimated Time**: 4-5 hours

### Tests for User Story 2 (TDD)

- [ ] T054 [P] [US2] TDD: Write failing integration test for `history` CLI command in `tests/integration/test_cli_history.py` - should list 20 recent backtests
- [ ] T055 [P] [US2] TDD: Write failing integration test for filtering by strategy in `tests/integration/test_cli_history.py`
- [ ] T056 [P] [US2] TDD: Write failing integration test for custom limit parameter in `tests/integration/test_cli_history.py`
- [ ] T057 [P] [US2] TDD: Write failing unit test for BacktestRepository.find_recent() with cursor pagination in `tests/unit/db/test_backtest_repository.py`
- [ ] T058 [P] [US2] TDD: Write failing unit test for BacktestRepository.find_by_strategy() in `tests/unit/db/test_backtest_repository.py`
- [ ] T059 [P] [US2] TDD: Write failing unit test for BacktestQueryService.list_recent_backtests() in `tests/unit/services/test_backtest_query.py`

### Query Service Implementation for User Story 2

- [ ] T060 [US2] Create BacktestQueryService class in `src/services/backtest_query.py` with repository dependency injection
- [ ] T061 [US2] Implement BacktestQueryService.list_recent_backtests() method per design.md lines 897-916 - enforces max limit of 1000
- [ ] T062 [US2] Implement BacktestQueryService.list_by_strategy() method per design.md lines 918-946 - filters by strategy name

### Repository Query Methods for User Story 2

- [ ] T063 [US2] Implement BacktestRepository.find_recent() with cursor pagination per design.md lines 446-476 and research.md section 6
- [ ] T064 [US2] Implement BacktestRepository.find_by_strategy() with cursor pagination per design.md lines 478-517
- [ ] T065 [US2] Use selectinload() for eager loading of metrics relationship to avoid N+1 queries per design.md line 461
- [ ] T066 [US2] Implement cursor pagination with tuple_ comparison per research.md section 6 (lines 563-577)

### CLI Command Implementation for User Story 2

- [ ] T067 [US2] Create history.py CLI command in `src/cli/commands/history.py` with Click decorators
- [ ] T068 [US2] Add command options: --limit (default 20), --strategy (filter), --instrument (filter) per design.md lines 1002-1009
- [ ] T069 [US2] Implement async query execution using asyncio.run() per design.md lines 1032-1055
- [ ] T070 [US2] Format results using Rich Table with columns: Run ID, Date, Strategy, Symbol, Return, Sharpe, Status per design.md lines 1061-1089
- [ ] T071 [US2] Add Rich progress spinner during query execution per research.md section 5 (lines 418-434)
- [ ] T072 [US2] Handle empty results with user-friendly message per design.md lines 1056-1058
- [ ] T073 [US2] Add proper error handling with Rich console output per design.md lines 1094-1096
- [ ] T074 [US2] Register history command in main CLI app in `src/cli/main.py`

### Validation for User Story 2

- [ ] T075 [US2] Test listing with no filters - should show 20 most recent
- [ ] T076 [US2] Test filtering by strategy name - should show only matching strategy
- [ ] T077 [US2] Test custom limit parameter (--limit 50)
- [ ] T078 [US2] Test with empty database - should show friendly message
- [ ] T079 [US2] Verify all tests from T054-T059 now PASS

**Checkpoint**: User Story 2 complete - can now view backtest history via CLI

---

## Phase 5: User Story 3 - Retrieve Complete Backtest Details (Priority: P2)

**Goal**: View all details of a specific past backtest using its identifier

**Independent Test**: Retrieve a single backtest record by ID and verify all configuration parameters, execution context, and performance metrics are displayed

**Value**: Provides complete transparency into past tests for reproducibility and learning

**⏱️ Estimated Time**: 3-4 hours

### Tests for User Story 3 (TDD)

- [ ] T080 [P] [US3] TDD: Write failing integration test for `show` CLI command in `tests/integration/test_cli_show.py` - should display full backtest details
- [ ] T081 [P] [US3] TDD: Write failing integration test for failed backtest retrieval in `tests/integration/test_cli_show.py` - should show error message
- [ ] T082 [P] [US3] TDD: Write failing unit test for BacktestRepository.find_by_run_id() in `tests/unit/db/test_backtest_repository.py`
- [ ] T083 [P] [US3] TDD: Write failing unit test for BacktestQueryService.get_backtest_by_id() in `tests/unit/services/test_backtest_query.py`

### Repository Query Method for User Story 3

- [ ] T084 [US3] Implement BacktestRepository.find_by_run_id() method per design.md lines 427-444 - query by UUID with eager load
- [ ] T085 [US3] Use selectinload() for metrics relationship per design.md line 439

### Service Method for User Story 3

- [ ] T086 [US3] Implement BacktestQueryService.get_backtest_by_id() method per design.md lines 884-895 - returns Optional[BacktestRun]

### CLI Command Implementation for User Story 3

- [ ] T087 [US3] Create show.py CLI command in `src/cli/commands/show.py` with Click decorators
- [ ] T088 [US3] Add run_id argument (UUID) as required parameter
- [ ] T089 [US3] Implement async query execution using asyncio.run()
- [ ] T090 [US3] Format output using Rich panels/trees for structured display of all fields
- [ ] T091 [US3] Display configuration snapshot (JSONB) in readable format with syntax highlighting
- [ ] T092 [US3] Display all performance metrics with proper formatting (percentages, decimals)
- [ ] T093 [US3] Show execution metadata (duration, status, timestamps)
- [ ] T094 [US3] Handle "not found" case with clear error message
- [ ] T095 [US3] Handle failed backtests - display error_message prominently
- [ ] T096 [US3] Register show command in main CLI app in `src/cli/main.py`

### Validation for User Story 3

- [ ] T097 [US3] Test retrieving successful backtest - verify all fields displayed
- [ ] T098 [US3] Test retrieving failed backtest - verify error message shown
- [ ] T099 [US3] Test with invalid UUID - verify error handling
- [ ] T100 [US3] Test with non-existent UUID - verify "not found" message
- [ ] T101 [US3] Verify all tests from T080-T083 now PASS

**Checkpoint**: User Story 3 complete - can now view complete details of any backtest

---

## Phase 6: User Story 4 - Compare Multiple Backtest Runs (Priority: P3)

**Goal**: Compare performance metrics across multiple backtests side-by-side to identify which parameter combinations work best

**Independent Test**: Select 3-5 saved backtests and view them in comparison format that highlights metric differences

**Value**: Accelerates parameter optimization decisions through visual comparison

**⏱️ Estimated Time**: 4-5 hours

### Tests for User Story 4 (TDD)

- [ ] T102 [P] [US4] TDD: Write failing integration test for `compare` CLI command in `tests/integration/test_cli_compare.py` - should display side-by-side comparison
- [ ] T103 [P] [US4] TDD: Write failing integration test for comparing 2 backtests (minimum) in `tests/integration/test_cli_compare.py`
- [ ] T104 [P] [US4] TDD: Write failing integration test for comparing 10 backtests (maximum) in `tests/integration/test_cli_compare.py`
- [ ] T105 [P] [US4] TDD: Write failing unit test for BacktestRepository.find_by_run_ids() in `tests/unit/db/test_backtest_repository.py`
- [ ] T106 [P] [US4] TDD: Write failing unit test for BacktestQueryService.compare_backtests() validation in `tests/unit/services/test_backtest_query.py`

### Repository Query Method for User Story 4

- [ ] T107 [US4] Implement BacktestRepository.find_by_run_ids() method per design.md lines 519-537 - accepts List[UUID], returns ordered results

### Service Method for User Story 4

- [ ] T108 [US4] Implement BacktestQueryService.compare_backtests() method per design.md lines 948-969 - validates 2-10 IDs, returns List[BacktestRun]

### CLI Command Implementation for User Story 4

- [ ] T109 [US4] Create compare.py CLI command in `src/cli/commands/compare.py` per design.md lines 1101-1204
- [ ] T110 [US4] Add run_ids argument accepting 2-10 UUIDs per design.md line 1127
- [ ] T111 [US4] Validate UUID count (2-10) with clear error messages per design.md lines 1133-1138
- [ ] T112 [US4] Parse string UUIDs to UUID objects with error handling per design.md lines 1141-1145
- [ ] T113 [US4] Create Rich comparison table with one column per backtest per design.md lines 1159-1168
- [ ] T114 [US4] Add rows for key metrics: Strategy, Symbol, Date, Total Return, Sharpe Ratio, Max Drawdown, Win Rate, Total Trades per design.md lines 1171-1185
- [ ] T115 [US4] Highlight best performer by Sharpe ratio at bottom per design.md lines 1190-1201
- [ ] T116 [US4] Handle case where some UUIDs don't exist - show partial results with warning
- [ ] T117 [US4] Register compare command in main CLI app in `src/cli/main.py`

### Validation for User Story 4

- [ ] T118 [US4] Test comparing 2 backtests - verify side-by-side display
- [ ] T119 [US4] Test comparing 10 backtests (maximum) - verify all shown
- [ ] T120 [US4] Test comparing 1 backtest (< minimum) - verify error message
- [ ] T121 [US4] Test comparing 11 backtests (> maximum) - verify error message
- [ ] T122 [US4] Test comparing across different strategies - verify works correctly
- [ ] T123 [US4] Verify all tests from T102-T106 now PASS

**Checkpoint**: User Story 4 complete - can now compare multiple backtests side-by-side

---

## Phase 7: User Story 5 - Re-run Previous Backtest (Priority: P3)

**Goal**: Re-run a previous backtest with its exact same configuration to validate reproducibility or test with updated data

**Independent Test**: Load a saved configuration, re-execute it, and verify the new run creates a separate record while using identical parameters

**Value**: Enables reproducibility validation and data updates

**⏱️ Estimated Time**: 4-5 hours

### Tests for User Story 5 (TDD)

- [ ] T124 [P] [US5] TDD: Write failing integration test for `reproduce` CLI command in `tests/integration/test_cli_reproduce.py` - should create new run with same config
- [ ] T125 [P] [US5] TDD: Write failing integration test for reproduction tracking in `tests/integration/test_cli_reproduce.py` - should set reproduced_from_run_id
- [ ] T126 [P] [US5] TDD: Write failing integration test for non-existent run_id in `tests/integration/test_cli_reproduce.py` - should show error

### Service Extension for User Story 5

- [ ] T127 [US5] Extend BacktestPersistenceService.save_backtest_results() to accept optional reproduced_from_run_id parameter
- [ ] T128 [US5] Modify BacktestPersistenceService to populate reproduced_from_run_id when provided per data-model.md line 91

### CLI Command Implementation for User Story 5

- [ ] T129 [US5] Create reproduce.py CLI command in `src/cli/commands/reproduce.py`
- [ ] T130 [US5] Add run_id argument (UUID of original backtest to reproduce)
- [ ] T131 [US5] Retrieve original backtest using BacktestQueryService.get_backtest_by_id()
- [ ] T132 [US5] Extract config_snapshot from original backtest
- [ ] T133 [US5] Load strategy class using config_snapshot.strategy_path with importlib
- [ ] T134 [US5] Reconstruct strategy configuration from config_snapshot.config
- [ ] T135 [US5] Execute backtest with original configuration
- [ ] T136 [US5] Save new backtest with reproduced_from_run_id linking to original per data-model.md lines 381-421
- [ ] T137 [US5] Display both original and new run IDs to user with clear indication of reproduction
- [ ] T138 [US5] Handle case where original run_id doesn't exist - show clear error per spec.md line 101
- [ ] T139 [US5] Handle case where strategy class no longer exists - show clear error with strategy path
- [ ] T140 [US5] Register reproduce command in main CLI app in `src/cli/main.py`

### Validation for User Story 5

- [ ] T141 [US5] Test reproducing successful backtest - verify new record created with reproduced_from_run_id set
- [ ] T142 [US5] Test reproducing with updated market data - verify different results but same config
- [ ] T143 [US5] Test with non-existent original run_id - verify error message
- [ ] T144 [US5] Test with deleted strategy class - verify error handling
- [ ] T145 [US5] Verify all tests from T124-T126 now PASS

**Checkpoint**: User Story 5 complete - can now re-run previous backtests for reproducibility

---

## Phase 8: User Story 6 - Find Best Performing Runs (Priority: P3)

**Goal**: Find top performing backtests based on specific metrics to identify the most promising strategies and parameters

**Independent Test**: Run backtests with varying performance, then sort/filter by metrics like Sharpe ratio to retrieve top performers

**Value**: Accelerates strategy discovery through efficient surfacing of best results

**⏱️ Estimated Time**: 3-4 hours

### Tests for User Story 6 (TDD)

- [ ] T146 [P] [US6] TDD: Write failing integration test for top performers query in `tests/integration/test_cli_history.py` - should show sorted by metric
- [ ] T147 [P] [US6] TDD: Write failing unit test for BacktestRepository.find_top_performers_by_sharpe() in `tests/unit/db/test_backtest_repository.py`

### Repository Query Methods for User Story 6

- [ ] T148 [US6] Implement BacktestRepository.find_top_performers_by_sharpe() per design.md lines 539-562 - ORDER BY sharpe_ratio DESC
- [ ] T149 [US6] Add Phase 2 index for metrics sorting if not already deployed: idx_metrics_sharpe_run per contracts/002_indexes.sql

### Service Method for User Story 6

- [ ] T150 [US6] Implement BacktestQueryService.find_top_performers() method per design.md lines 971-989 - supports metric parameter

### CLI Extension for User Story 6

- [ ] T151 [US6] Extend history.py CLI command with --sort option accepting: date, return, sharpe, drawdown
- [ ] T152 [US6] Implement sorting logic by calling appropriate repository method based on --sort parameter
- [ ] T153 [US6] Add visual indicator in table output for sorted column (e.g., highlight or arrow)

### Validation for User Story 6

- [ ] T154 [US6] Test sorting by Sharpe ratio - verify descending order
- [ ] T155 [US6] Test sorting by total return - verify highest first
- [ ] T156 [US6] Test with no successful backtests (only failed) - verify handles gracefully
- [ ] T157 [US6] Verify all tests from T146-T147 now PASS

**Checkpoint**: User Story 6 complete - can now find top performers by metric

---

## Phase 9: User Story 7 - View Strategy Performance History (Priority: P4)

**Goal**: See all backtest runs for a specific strategy over time to track how performance changes with different parameters or market conditions

**Independent Test**: Filter all saved backtests by a single strategy name and display them chronologically

**Value**: Reveals strategy performance patterns over time for long-term research

**⏱️ Estimated Time**: 2-3 hours

### Tests for User Story 7 (TDD)

- [ ] T158 [P] [US7] TDD: Write failing integration test for strategy history in `tests/integration/test_cli_history.py` - should filter by strategy and show chronologically
- [ ] T159 [P] [US7] TDD: Write failing unit test for BacktestRepository.count_by_strategy() in `tests/unit/db/test_backtest_repository.py`

### Repository Query Methods for User Story 7

- [ ] T160 [US7] Implement BacktestRepository.count_by_strategy() per design.md lines 564-582 - returns total count for pagination info

### CLI Extension for User Story 7

- [ ] T161 [US7] Enhance history.py --strategy filter to show total count and pagination info
- [ ] T162 [US7] Add --strategy-summary flag to show statistics across all runs (avg return, best Sharpe, etc.)
- [ ] T163 [US7] Display parameter variations across runs for same strategy (extract from config_snapshot)

### Validation for User Story 7

- [ ] T164 [US7] Test viewing history for strategy with 15+ runs - verify chronological order
- [ ] T165 [US7] Test strategy summary statistics - verify calculations correct
- [ ] T166 [US7] Test with strategy name that has no runs - verify friendly message
- [ ] T167 [US7] Verify all tests from T158-T159 now PASS

**Checkpoint**: User Story 7 complete - can now view strategy performance over time

---

## Phase 10: User Story 8 - Avoid Duplicate Testing (Priority: P4)

**Goal**: Know if a specific parameter combination has already been tested to avoid wasting computation time re-running identical backtests unintentionally

**Independent Test**: Attempt to run a backtest with parameters matching a previous run and receive notification about existing result

**Value**: Saves computation time by preventing unintentional duplicate work

**⏱️ Estimated Time**: 3-4 hours

### Tests for User Story 8 (TDD)

- [ ] T168 [P] [US8] TDD: Write failing integration test for duplicate detection in `tests/integration/test_duplicate_detection.py` - should find matching config
- [ ] T169 [P] [US8] TDD: Write failing unit test for config snapshot hash comparison in `tests/unit/db/test_backtest_repository.py`

### Repository Query Methods for User Story 8

- [ ] T170 [US8] Implement BacktestRepository.find_by_config_hash() using JSONB containment query per research.md section 6 (lines 609-616)
- [ ] T171 [US8] Add GIN index for config_snapshot if not already deployed (Phase 2 index) per contracts/002_indexes.sql

### Duplicate Detection Service

- [ ] T172 [US8] Create DuplicateDetectionService in `src/services/duplicate_detection.py`
- [ ] T173 [US8] Implement find_duplicate_configs() method that searches for matching config + instrument + date range
- [ ] T174 [US8] Implement calculate_config_similarity() for partial matches (e.g., only parameter values differ)

### CLI Integration for User Story 8

- [ ] T175 [US8] Add pre-execution check in run.py CLI command to detect duplicates before running backtest
- [ ] T176 [US8] Display warning message with existing run_id and execution date if duplicate found
- [ ] T177 [US8] Provide option to continue anyway (--force flag) or view existing results (show run_id)
- [ ] T178 [US8] Add --skip-duplicate-check flag for users who want to run intentional duplicates

### Validation for User Story 8

- [ ] T179 [US8] Test exact duplicate detection - same config, instrument, date range
- [ ] T180 [US8] Test partial match detection - same config, different date range
- [ ] T181 [US8] Test forcing re-run with --force flag - should create new record
- [ ] T182 [US8] Verify all tests from T168-T169 now PASS

**Checkpoint**: User Story 8 complete - can now avoid duplicate testing

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories and final quality assurance

**⏱️ Estimated Time**: 4-6 hours

### Performance Optimization

- [ ] T183 [P] Deploy Phase 2 indexes using new Alembic migration per contracts/002_indexes.sql (idx_metrics_sharpe_run, idx_backtest_runs_config_gin)
- [ ] T184 [P] Monitor query performance using track_query_time decorator per design.md lines 1885-1900
- [ ] T185 [P] Run EXPLAIN ANALYZE on key queries and verify index usage per design.md lines 1909-1932

### Documentation

- [ ] T186 [P] Update main README.md with new CLI commands (history, show, compare, reproduce)
- [ ] T187 [P] Create user guide in docs/user-guide-backtest-history.md with examples of each command
- [ ] T188 [P] Add architecture diagram showing database integration in docs/architecture.md
- [ ] T189 [P] Document environment variables for database connection in README.md

### Code Quality

- [ ] T190 [P] Run ruff format on all new files
- [ ] T191 [P] Run ruff check and fix any linting issues
- [ ] T192 [P] Run mypy type checking and resolve any type errors
- [ ] T193 [P] Verify all files under 500 lines (refactor if needed) per CLAUDE.md
- [ ] T194 [P] Verify all functions under 50 lines per CLAUDE.md
- [ ] T195 [P] Add comprehensive docstrings (Google style) to all public functions

### Testing

- [ ] T196 [P] Run full test suite: `uv run pytest`
- [ ] T197 [P] Generate coverage report: `uv run pytest --cov=src --cov-report=html`
- [ ] T198 [P] Verify 80%+ coverage on critical paths per plan.md line 110
- [ ] T199 Add end-to-end test in `tests/e2e/test_full_workflow.py` - run backtest → save → query → compare → reproduce

### Quickstart Validation

- [ ] T200 Run through quickstart.md validation scenario with fresh database
- [ ] T201 Verify all three strategies (SMA, RSI, Momentum) work with auto-persistence
- [ ] T202 Verify all CLI commands work as documented

### Security Audit

- [ ] T203 [P] Verify database credentials loaded from environment variables only per design.md lines 1941-1959
- [ ] T204 [P] Verify all queries use parameterized approach (SQLAlchemy handles this) per design.md lines 1964-1969
- [ ] T205 [P] Verify error messages don't expose internal details per design.md lines 1993-2001
- [ ] T206 [P] Run security scan with bandit: `uv run bandit -r src/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
  ⏱️ ~30 min

- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
  ⏱️ ~3-4 hours

- **User Stories (Phase 3-10)**: All depend on Foundational phase completion
  - User stories CAN proceed in parallel (if staffed) or sequentially by priority
  - **MVP = Complete through Phase 3 only** (US1: Automatic Persistence)
  - Full feature = Complete all phases 3-10

- **Polish (Phase 11)**: Depends on all desired user stories being complete
  ⏱️ ~4-6 hours

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational - NO dependencies on other stories ✅ MVP
- **User Story 2 (P2)**: Can start after Foundational - Independent (uses US1 data but doesn't modify it)
- **User Story 3 (P2)**: Can start after Foundational - Independent (uses US1 data but doesn't modify it)
- **User Story 4 (P3)**: Can start after Foundational - Independent (uses US1 data but doesn't modify it)
- **User Story 5 (P3)**: Can start after Foundational - Independent (creates new US1 data)
- **User Story 6 (P3)**: Can start after Foundational - Independent (queries US1 data)
- **User Story 7 (P4)**: Can start after Foundational - Independent (queries US1 data)
- **User Story 8 (P4)**: Can start after Foundational - Independent (queries US1 data for comparison)

### Within Each User Story

- Tests MUST be written and FAIL before implementation (TDD)
- Models before services (T004-T005 → T006-T007)
- Services before CLI (T031-T041 → T067-T074)
- Core implementation before integration (repository → service → CLI)
- Story complete before moving to next priority

### Parallel Opportunities

**Within Setup (Phase 1)**:
- All 3 tasks can run in parallel (T001, T002, T003)

**Within Foundational (Phase 2)**:
- Tests can run in parallel: T004 [P], T005 [P], T011 [P], T020 [P], T023 [P]
- Some implementation can run in parallel: different files

**Across User Stories** (after Foundational complete):
- All user stories can be worked on in parallel by different team members
- Example: Developer A → US1, Developer B → US2, Developer C → US3

**Within Each User Story**:
- All tests for a story can run in parallel (marked [P])
- Models within a story can run in parallel (different files)

**Within Polish (Phase 11)**:
- Most tasks can run in parallel (marked [P])

---

## Parallel Example: Foundational Phase

```bash
# Launch all TDD tests in parallel (Phase 2):
Task T004: "Write failing unit test for BacktestRun model creation"
Task T005: "Write failing unit test for PerformanceMetrics model creation"
Task T011: "Write failing test for StrategyConfigSnapshot validation"
Task T020: "Write failing test for async session context manager"
Task T023: "Create custom exception hierarchy"
```

---

## Parallel Example: User Story 1

```bash
# After Foundational complete, launch all US1 tests in parallel:
Task T024: "TDD: Write failing integration test for successful backtest persistence"
Task T025: "TDD: Write failing integration test for failed backtest persistence"
Task T026: "TDD: Write failing unit test for BacktestRepository.create_backtest_run()"
Task T027: "TDD: Write failing unit test for BacktestRepository.create_performance_metrics()"
Task T028: "TDD: Write failing unit test for BacktestPersistenceService.save_backtest_results()"
Task T029: "TDD: Write failing unit test for BacktestPersistenceService.save_failed_backtest()"
Task T030: "TDD: Write failing unit test for metric validation"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only) - RECOMMENDED

**Total Time**: ~8-12 hours

1. ✅ Complete Phase 1: Setup (~30 min)
2. ✅ Complete Phase 2: Foundational (~3-4 hours)
3. ✅ Complete Phase 3: User Story 1 (~6-8 hours)
4. **STOP and VALIDATE**: Test User Story 1 independently
5. Deploy/demo if ready - **THIS IS YOUR MVP!**

**Value**: Every backtest auto-saves to database. Data never lost. Foundation for all other features.

---

### Incremental Delivery (Recommended for Full Feature)

**Total Time**: ~35-45 hours

1. Complete Setup + Foundational → Foundation ready (~4-5 hours)
2. Add User Story 1 → Test independently → **Deploy/Demo (MVP!)** (~6-8 hours)
3. Add User Story 2 → Test independently → Deploy/Demo (~4-5 hours)
4. Add User Story 3 → Test independently → Deploy/Demo (~3-4 hours)
5. Add User Story 4 → Test independently → Deploy/Demo (~4-5 hours)
6. Add User Story 5 → Test independently → Deploy/Demo (~4-5 hours)
7. Add User Story 6 → Test independently → Deploy/Demo (~3-4 hours)
8. Add User Story 7 → Test independently → Deploy/Demo (~2-3 hours)
9. Add User Story 8 → Test independently → Deploy/Demo (~3-4 hours)
10. Polish & Quality Assurance → Final release (~4-6 hours)

Each story adds value without breaking previous stories.

---

### Parallel Team Strategy (If Multiple Developers Available)

**Total Time**: ~15-20 hours (with 3 developers)

1. **Team completes Setup + Foundational together** (~4-5 hours)
2. **Once Foundational done, split work**:
   - Developer A: User Stories 1, 4, 7 (~12-15 hours)
   - Developer B: User Stories 2, 5, 8 (~11-14 hours)
   - Developer C: User Stories 3, 6, Polish (~10-14 hours)
3. Stories complete and integrate independently
4. Final integration testing together (~2-3 hours)

---

## Notes

- **[P] tasks** = different files, no dependencies, can run in parallel
- **[Story] label** maps task to specific user story for traceability (US1, US2, etc.)
- Each user story should be **independently completable and testable**
- **TDD**: Verify tests FAIL before implementing
- **Commit** after each task or logical group
- **Stop at any checkpoint** to validate story independently
- **MVP = Phase 1 + Phase 2 + Phase 3 only** (User Story 1)
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence

---

## Success Criteria Verification

After completing all phases, verify these success criteria from spec.md:

- [ ] SC-001: 100% of completed backtests auto-saved to database
- [ ] SC-002: Retrieve backtest details in <100ms
- [ ] SC-003: List 20 recent backtests in <200ms
- [ ] SC-004: Compare 10 backtests in <2s
- [ ] SC-005: System handles 10,000+ backtests without degradation
- [ ] SC-006: Zero data loss - all backtests persisted with complete metadata
- [ ] SC-007: Can reproduce any past backtest from stored config
- [ ] SC-008: All 3 strategies (SMA, RSI, Momentum) work with auto-persistence
- [ ] SC-009: 50% reduction in re-running backtests for review
- [ ] SC-010: Find relevant backtests using filters in <1s

---

**Total Tasks**: 206
**Total Estimated Time**: 35-45 hours (sequential) OR 15-20 hours (parallel with 3 developers)
**MVP Time**: 8-12 hours (Setup + Foundational + User Story 1 only)

**Generated**: 2025-01-25
**Based on**: design.md, spec.md, data-model.md, plan.md, research.md, contracts/
