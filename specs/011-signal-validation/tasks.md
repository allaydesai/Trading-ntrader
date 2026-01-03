# Tasks: Multi-Condition Signal Validation

**Input**: Design documents from `/specs/011-signal-validation/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: TDD is mandatory per constitution. Tests are included for each user story.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and signal module structure

- [ ] T001 Create signal module directory structure in src/core/signals/
- [ ] T002 [P] Create src/core/signals/__init__.py with module exports
- [ ] T003 [P] Create src/models/signal.py with Pydantic model stubs
- [ ] T004 [P] Create tests/unit/signals/ directory structure

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core data structures that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [ ] T005 Implement ComponentResult frozen dataclass in src/core/signals/evaluation.py
- [ ] T006 Implement SignalEvaluation dataclass with derived properties in src/core/signals/evaluation.py
- [ ] T007 [P] Implement CombinationLogic and ComponentType enums in src/models/signal.py
- [ ] T008 [P] Implement ComponentResultResponse Pydantic model in src/models/signal.py
- [ ] T009 [P] Implement SignalEvaluationResponse Pydantic model in src/models/signal.py
- [ ] T010 Define SignalComponent Protocol in src/core/signals/components.py
- [ ] T011 [P] Unit tests for ComponentResult and SignalEvaluation in tests/unit/signals/test_evaluation.py
- [ ] T012 [P] Unit tests for Pydantic models in tests/unit/signals/test_models.py

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Define and Evaluate Multi-Condition Entry Signals (Priority: P1)

**Goal**: Enable defining multiple entry conditions that evaluate together as a composite signal, showing pass/fail for each condition

**Independent Test**: Configure a strategy with 4 conditions (trend, pattern, Fibonacci, volume), run a backtest, verify each bar produces a signal evaluation with pass/fail for each condition and a final composite result

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T013 [P] [US1] Unit tests for TrendFilterComponent in tests/unit/signals/test_components.py
- [ ] T014 [P] [US1] Unit tests for RSIThresholdComponent in tests/unit/signals/test_components.py
- [ ] T015 [P] [US1] Unit tests for VolumeConfirmComponent in tests/unit/signals/test_components.py
- [ ] T016 [P] [US1] Unit tests for FibonacciLevelComponent in tests/unit/signals/test_components.py
- [ ] T017 [P] [US1] Unit tests for CompositeSignalGenerator AND logic in tests/unit/signals/test_composite.py
- [ ] T018 [P] [US1] Unit tests for CompositeSignalGenerator OR logic in tests/unit/signals/test_composite.py

### Implementation for User Story 1

- [ ] T019 [US1] Implement TrendFilterComponent (Close > SMA) in src/core/signals/components.py
- [ ] T020 [US1] Implement RSIThresholdComponent (RSI comparison) in src/core/signals/components.py
- [ ] T021 [US1] Implement VolumeConfirmComponent (Volume vs average) in src/core/signals/components.py
- [ ] T022 [US1] Implement FibonacciLevelComponent (Price near Fib level) in src/core/signals/components.py
- [ ] T023 [US1] Implement PriceBreakoutComponent (Price vs prev high/low) in src/core/signals/components.py
- [ ] T024 [US1] Implement TimeStopComponent (Bars held limit) in src/core/signals/components.py
- [ ] T025 [US1] Implement CompositeSignalGenerator with AND/OR logic in src/core/signals/composite.py
- [ ] T026 [US1] Add signal strength calculation to CompositeSignalGenerator in src/core/signals/composite.py
- [ ] T027 [US1] Add blocking component identification to CompositeSignalGenerator in src/core/signals/composite.py
- [ ] T028 [US1] Implement ComponentConfig and CompositeSignalConfig Pydantic models in src/models/signal.py

**Checkpoint**: US1 complete - can define and evaluate multi-condition signals with pass/fail per component

---

## Phase 4: User Story 2 - Capture Signal Audit Trail During Backtest (Priority: P2)

**Goal**: Capture and store every signal evaluation during backtesting for post-analysis

**Independent Test**: Run a backtest with 1000 bars, verify complete log of all signal evaluations is available for export to CSV

### Tests for User Story 2

- [ ] T029 [P] [US2] Unit tests for SignalCollector.record() in tests/unit/signals/test_collector.py
- [ ] T030 [P] [US2] Unit tests for SignalCollector.export_csv() in tests/unit/signals/test_collector.py
- [ ] T031 [P] [US2] Unit tests for memory-bounded flush in tests/unit/signals/test_collector.py

### Implementation for User Story 2

- [ ] T032 [US2] Implement SignalCollector with in-memory storage in src/core/signals/collector.py
- [ ] T033 [US2] Add periodic flush to CSV when threshold reached in src/core/signals/collector.py
- [ ] T034 [US2] Implement export_csv() method with flattened format in src/core/signals/collector.py
- [ ] T035 [US2] Add finalize() method to merge CSV chunks on backtest completion in src/core/signals/collector.py

**Checkpoint**: US2 complete - audit trail captured and exportable to CSV

---

## Phase 5: User Story 3 - Identify Blocking Conditions (Priority: P2)

**Goal**: Identify which condition blocked a signal from triggering for parameter tuning

**Independent Test**: Run backtest where signals frequently fail, query which condition blocked most often, verify blocking analysis statistics

### Tests for User Story 3

- [ ] T036 [P] [US3] Unit tests for blocking component tracking in tests/unit/signals/test_analysis.py
- [ ] T037 [P] [US3] Unit tests for blocking rate calculation in tests/unit/signals/test_analysis.py

### Implementation for User Story 3

- [ ] T038 [US3] Implement SignalAnalyzer class skeleton in src/core/signals/analysis.py
- [ ] T039 [US3] Add blocking rate calculation per component in src/core/signals/analysis.py
- [ ] T040 [US3] Add primary_blocker identification in src/core/signals/analysis.py
- [ ] T041 [US3] Implement SignalStatistics dataclass in src/core/signals/analysis.py
- [ ] T042 [US3] Implement BlockingAnalysisResponse Pydantic model in src/models/signal.py

**Checkpoint**: US3 complete - can identify and analyze blocking conditions

---

## Phase 6: User Story 4 - Calculate Signal Strength (Priority: P3)

**Goal**: Show how many conditions passed even when final signal was FALSE for near-miss identification

**Independent Test**: Run backtest, view signal strength values for each bar, verify strength is calculated as ratio of passed/total conditions

### Tests for User Story 4

- [ ] T043 [P] [US4] Unit tests for signal strength calculation in tests/unit/signals/test_analysis.py
- [ ] T044 [P] [US4] Unit tests for near-miss identification in tests/unit/signals/test_analysis.py

### Implementation for User Story 4

- [ ] T045 [US4] Add near-miss filtering to SignalAnalyzer in src/core/signals/analysis.py
- [ ] T046 [US4] Add is_near_miss property to SignalEvaluation in src/core/signals/evaluation.py
- [ ] T047 [US4] Add configurable near_miss_threshold to SignalAnalyzer in src/core/signals/analysis.py

**Checkpoint**: US4 complete - signal strength and near-miss analysis available

---

## Phase 7: User Story 5 - Analyze Component Trigger Rates (Priority: P3)

**Goal**: Show how often each individual condition passes across the entire backtest

**Independent Test**: Run backtest, view summary table showing each condition's trigger rate as percentage of bars where it passed

### Tests for User Story 5

- [ ] T048 [P] [US5] Unit tests for trigger rate calculation in tests/unit/signals/test_analysis.py
- [ ] T049 [P] [US5] Unit tests for SignalStatisticsResponse in tests/unit/signals/test_analysis.py

### Implementation for User Story 5

- [ ] T050 [US5] Add trigger rate calculation per component in src/core/signals/analysis.py
- [ ] T051 [US5] Implement SignalStatisticsResponse Pydantic model in src/models/signal.py
- [ ] T052 [US5] Add get_statistics() method to SignalCollector in src/core/signals/collector.py

**Checkpoint**: US5 complete - trigger rates per component available

---

## Phase 8: REST API Endpoints

**Purpose**: Expose signal analysis via FastAPI endpoints per contracts/openapi.yaml

### Tests for API

- [ ] T053 [P] Integration test for GET /backtests/{id}/signals in tests/integration/test_signal_api.py
- [ ] T054 [P] Integration test for GET /backtests/{id}/signals/statistics in tests/integration/test_signal_api.py
- [ ] T055 [P] Integration test for GET /backtests/{id}/signals/blocking-analysis in tests/integration/test_signal_api.py
- [ ] T056 [P] Integration test for POST /backtests/{id}/signals/export in tests/integration/test_signal_api.py

### Implementation for API

- [ ] T057 Create signals router in src/api/rest/signals.py
- [ ] T058 Implement listSignalEvaluations endpoint in src/api/rest/signals.py
- [ ] T059 Implement getSignalStatistics endpoint in src/api/rest/signals.py
- [ ] T060 Implement getNearMisses endpoint in src/api/rest/signals.py
- [ ] T061 Implement getBlockingAnalysis endpoint in src/api/rest/signals.py
- [ ] T062 Implement exportSignalEvaluations endpoint in src/api/rest/signals.py
- [ ] T063 Implement SignalExportService for async export in src/services/signal_export.py
- [ ] T064 Register signals router in main API application

**Checkpoint**: All API endpoints functional per OpenAPI contract

---

## Phase 9: Database Persistence (Optional)

**Purpose**: Persist signal evaluations to PostgreSQL for querying

- [ ] T065 Create signal_evaluations ORM model in src/db/models/signal_evaluation.py
- [ ] T066 Create Alembic migration for signal_evaluations table
- [ ] T067 Implement SignalEvaluationRepository in src/db/repositories/signal_evaluation.py
- [ ] T068 Update API endpoints to use database persistence

---

## Phase 10: Strategy Integration (User Stories 0, 6, 7, 8)

**Purpose**: Enable existing strategies to use signal validation with entry/exit distinction and trade correlation

### User Story 0 - Strategy Integration (Priority: P0)

**Goal**: Provide a mixin class for existing Nautilus strategies to gain signal validation capabilities

- [ ] T069 [US0] Create SignalValidationMixin base class in src/core/signals/integration.py
- [ ] T070 [US0] Implement create_composite_signal() method in mixin
- [ ] T071 [US0] Implement evaluate_signal() method with automatic audit capture
- [ ] T072 [US0] Add automatic order-signal linking via submit_order() wrapper
- [ ] T073 [P] [US0] Unit tests for SignalValidationMixin in tests/unit/signals/test_integration.py
- [ ] T074 [US0] Integration test: Apply mixin to LarryConnorsRSIMeanRev in tests/integration/test_signal_backtest.py
- [ ] T075 [US0] Performance benchmark: Verify <5% overhead with mixin applied

**Checkpoint**: US0 complete - existing strategies can add signal validation with minimal code changes

### User Story 6 - Exit Signals (Priority: P1)

**Goal**: Support separate entry and exit signal generators with independent analysis

- [ ] T076 [P] [US6] Add SignalType enum (ENTRY, EXIT) to src/models/signal.py
- [ ] T077 [US6] Update SignalEvaluation dataclass to include signal_type field in src/core/signals/evaluation.py
- [ ] T078 [US6] Add exit signal analysis methods to SignalAnalyzer in src/core/signals/analysis.py
- [ ] T079 [P] [US6] Unit tests for exit signal evaluation in tests/unit/signals/test_exit_signals.py
- [ ] T080 [US6] Add exit trigger rate statistics to SignalStatistics

**Checkpoint**: US6 complete - entry and exit signals tracked and analyzed separately

### User Story 7 - Signal-Trade Correlation (Priority: P2)

**Goal**: Link signal evaluations to resulting orders and trades for outcome analysis

- [ ] T081 [US7] Add order_id and trade_id optional fields to SignalEvaluation in src/core/signals/evaluation.py
- [ ] T082 [US7] Implement signal-trade correlation tracking in SignalCollector in src/core/signals/collector.py
- [ ] T083 [US7] Add on_order_filled callback to update trade_id in SignalValidationMixin
- [ ] T084 [P] [US7] Unit tests for signal-trade correlation in tests/unit/signals/test_correlation.py
- [ ] T085 [US7] Add trade-signal lookup API endpoint GET /backtests/{id}/trades/{trade_id}/signal in src/api/rest/signals.py

**Checkpoint**: US7 complete - can trace from trade outcome to triggering signal

### User Story 8 - Config-Driven Signals (Priority: P2)

**Goal**: Define signal components via YAML/Pydantic configuration

- [ ] T086 [P] [US8] Implement SignalComponentConfig Pydantic models with validation in src/models/signal.py
- [ ] T087 [P] [US8] Implement CompositeSignalConfig Pydantic model in src/models/signal.py
- [ ] T088 [US8] Implement config-driven component factory in src/core/signals/factory.py
- [ ] T089 [US8] Update SignalValidationMixin to accept config-driven signal definitions
- [ ] T090 [P] [US8] Unit tests for config-driven component creation in tests/unit/signals/test_factory.py
- [ ] T091 [US8] Update strategy configs schema to support signal_validation section

**Checkpoint**: US8 complete - signal conditions defined via configuration, not hardcoded

### Documentation

- [ ] T092 Update quickstart.md with signal validation integration examples
- [ ] T093 Add signal validation section to strategy development guide

---

## Phase 10.1: CLI Integration (User Story 9 - Run Backtest with Signals)

**Purpose**: Enable running backtests with signal validation via existing CLI

**Goal**: Users can run `ntrader backtest run --enable-signals` to capture signal audit data

### Tests for User Story 9

- [ ] T101 [P] [US9] Unit test for --enable-signals CLI flag parsing in tests/unit/cli/test_backtest_signals.py
- [ ] T102 [P] [US9] Unit test for --signal-export-path CLI option in tests/unit/cli/test_backtest_signals.py
- [ ] T103 [P] [US9] Integration test: Backtest with signals enabled produces CSV export in tests/integration/test_signal_cli.py

### Implementation for User Story 9

- [ ] T104 [US9] Add --enable-signals flag to backtest run command in src/cli/commands/backtest.py
- [ ] T105 [US9] Add --signal-export-path option to backtest run command in src/cli/commands/backtest.py
- [ ] T106 [US9] Update MinimalBacktestRunner to accept optional SignalCollector in src/core/backtest_runner.py
- [ ] T107 [US9] Inject SignalCollector into strategy when signals enabled in src/core/backtest_runner.py
- [ ] T108 [US9] Auto-export signal audit trail on backtest completion in src/core/backtest_runner.py
- [ ] T109 [US9] Display signal summary in CLI results table (total evals, trigger rate, primary blocker)
- [ ] T110 [US9] Persist signal statistics to backtest_runs.config_snapshot for WebUI access

**Checkpoint**: US9 complete - users can run signal-enabled backtests via CLI

---

## Phase 10.2: WebUI Integration (User Story 10 - View Signals in UI)

**Purpose**: Display signal analysis in existing backtest detail page

**Goal**: Backtest detail page shows "Signals" tab with statistics and CSV export

### Tests for User Story 10

- [ ] T111 [P] [US10] Integration test for GET /backtests/{id}/signals endpoint in tests/integration/test_signal_ui.py
- [ ] T112 [P] [US10] Integration test for Signals tab rendering in tests/integration/test_signal_ui.py

### Implementation for User Story 10

- [ ] T113 [US10] Add signal statistics API endpoint GET /api/backtests/{id}/signals/summary in src/api/rest/signals.py
- [ ] T114 [US10] Add signal CSV export endpoint GET /api/backtests/{id}/signals/export in src/api/rest/signals.py
- [ ] T115 [US10] Create signals tab partial template in templates/backtests/partials/signals_tab.html
- [ ] T116 [US10] Update backtest detail view to include Signals tab in templates/backtests/detail.html
- [ ] T117 [US10] Add route handler for signals tab HTMX fragment in src/api/ui/backtests.py
- [ ] T118 [US10] Display "No signal data" state when backtest has no signals

**Checkpoint**: US10 complete - signal analysis visible in WebUI

---

## Phase 10.3: Configuration Validation (User Story 11)

**Purpose**: Validate signal configuration before backtest execution

**Goal**: Catch configuration errors early with clear error messages

### Tests for User Story 11

- [ ] T119 [P] [US11] Unit test for empty component list validation in tests/unit/signals/test_validation.py
- [ ] T120 [P] [US11] Unit test for invalid threshold validation in tests/unit/signals/test_validation.py
- [ ] T121 [P] [US11] Unit test for insufficient data handling in tests/unit/signals/test_validation.py

### Implementation for User Story 11

- [ ] T122 [US11] Add Pydantic validators for CompositeSignalConfig (min 1 component) in src/models/signal.py
- [ ] T123 [US11] Add threshold range validators to component configs in src/models/signal.py
- [ ] T124 [US11] Implement insufficient data detection in component evaluate() methods
- [ ] T125 [US11] Add pre-backtest validation hook in SignalValidationMixin.on_start()

**Checkpoint**: US11 complete - configuration errors fail fast with clear messages

---

## Phase 12: End-to-End Journey Validation

**Purpose**: Validate complete user journey from strategy implementation through analysis

**CRITICAL**: This phase validates the entire feature works as an integrated system

### E2E Journey Test

- [ ] T126 [E2E] Comprehensive end-to-end test in tests/e2e/test_signal_validation_journey.py:
  1. Create strategy class with SignalValidationMixin
  2. Define entry signal (4 components: trend, RSI, volume, Fib)
  3. Define exit signal (2 components: price breakout, time stop)
  4. Run backtest via CLI with --enable-signals
  5. Verify CSV audit trail contains all evaluations
  6. Verify signal statistics match manual calculation
  7. Verify blocking analysis identifies correct bottleneck
  8. Verify WebUI displays signal summary
  9. Verify signal-trade correlation for executed trades

### Edge Case Tests (from spec)

- [ ] T127 [P] [Edge] Test: Backtest with missing/invalid bar data skips evaluation with warning
- [ ] T128 [P] [Edge] Test: Long backtest (100k+ bars) flushes memory correctly
- [ ] T129 [P] [Edge] Test: Strategy with only exit signals (no entry) works correctly

**Checkpoint**: E2E complete - entire user journey validated

---

## Phase 11: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, validation, and final testing

- [ ] T094 [P] Add type hints validation with mypy for src/core/signals/
- [ ] T095 [P] Add structlog logging to signal evaluation path
- [ ] T096 [P] Validate FR-009: Performance benchmark with 10+ conditions per bar
- [ ] T097 [P] Validate FR-010: Memory benchmark with 100k+ bar backtest
- [ ] T098 [P] Validate FR-013: Performance benchmark with mixin (<5% overhead)
- [ ] T099 Run quickstart.md validation scenarios
- [ ] T100 Code cleanup and final review

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Story 1 (Phase 3)**: Depends on Foundational phase completion
- **User Story 2 (Phase 4)**: Depends on Phase 3 (US1) for SignalEvaluation objects
- **User Story 3 (Phase 5)**: Depends on Phase 4 (US2) for collected evaluations
- **User Story 4 (Phase 6)**: Depends on Phase 4 (US2) for analysis
- **User Story 5 (Phase 7)**: Depends on Phase 4 (US2) for analysis
- **API (Phase 8)**: Depends on Phases 3-7 (all core functionality)
- **Database (Phase 9)**: Optional, depends on Phase 8
- **Integration (Phase 10)**: Depends on Phases 3-4 (core signal/collector)
  - **US0 (P0)**: Depends on US1 + US2 - Creates mixin using core signal components
  - **US6 (P1)**: Depends on US0 - Adds entry/exit distinction to mixin
  - **US7 (P2)**: Depends on US0 + US2 - Links signals to trades via collector
  - **US8 (P2)**: Depends on US0 - Adds config-driven component creation
- **CLI Integration (Phase 10.1)**: Depends on US0 + US2 - Enables running backtests with signals
  - **US9 (P1)**: Depends on US0 + SignalCollector - CLI flags and auto-export
- **WebUI Integration (Phase 10.2)**: Depends on US9 + existing WebUI (007-backtest-detail-view)
  - **US10 (P2)**: Depends on US9 (persisted signal data) - Displays signals in UI
- **Validation (Phase 10.3)**: Can run in parallel with Phase 10.1-10.2
  - **US11 (P2)**: Depends on US8 (config models) - Validates configuration
- **E2E Validation (Phase 12)**: Depends on ALL prior phases - Final journey test
- **Polish (Phase 11)**: Depends on all prior phases

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational (Phase 2) - Core evaluation logic
- **US2 (P2)**: Depends on US1 - Uses SignalEvaluation from US1
- **US3 (P2)**: Depends on US2 - Analyzes collected evaluations
- **US4 (P3)**: Depends on US2 - Analyzes signal strength from evaluations
- **US5 (P3)**: Depends on US2 - Calculates rates from evaluations
- **US0 (P0)**: Depends on US1 + US2 - Creates integration layer for strategies
- **US6 (P1)**: Depends on US0 - Extends mixin with entry/exit signal types
- **US7 (P2)**: Depends on US0 + US2 - Adds order/trade correlation
- **US8 (P2)**: Depends on US0 - Adds configuration-driven signal definition
- **US9 (P1)**: Depends on US0 + US2 - CLI integration for running signal-enabled backtests
- **US10 (P2)**: Depends on US9 - WebUI display of signal data
- **US11 (P2)**: Depends on US8 - Configuration validation before execution

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Implementation follows test requirements
- Verify tests pass after implementation

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel
- All tests for a user story marked [P] can run in parallel
- API tests (Phase 8) can run in parallel
- Polish tasks (Phase 11) can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all US1 tests in parallel:
Task: "Unit tests for TrendFilterComponent in tests/unit/signals/test_components.py"
Task: "Unit tests for RSIThresholdComponent in tests/unit/signals/test_components.py"
Task: "Unit tests for VolumeConfirmComponent in tests/unit/signals/test_components.py"
Task: "Unit tests for FibonacciLevelComponent in tests/unit/signals/test_components.py"
Task: "Unit tests for CompositeSignalGenerator AND logic in tests/unit/signals/test_composite.py"
Task: "Unit tests for CompositeSignalGenerator OR logic in tests/unit/signals/test_composite.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 + 2 + 0 + 9)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (signal evaluation)
4. Complete Phase 4: User Story 2 (audit trail)
5. Complete Phase 10 US0: Strategy integration mixin
6. Complete Phase 10.1 US9: CLI integration (--enable-signals)
7. **STOP and VALIDATE**: Run `ntrader backtest run --enable-signals`, verify CSV export and CLI summary
8. This provides core value: users can run signal-enabled backtests end-to-end

### Incremental Delivery

1. MVP (US1 + US2 + US0 + US9): Define, evaluate, run, and export signals via CLI
2. Add US6: Entry/exit signal distinction
3. Add US3: Blocking condition analysis
4. Add US4: Signal strength and near-misses
5. Add US5: Trigger rate statistics
6. Add US7: Signal-trade correlation
7. Add US8: Config-driven signal definition
8. Add US10: WebUI signal display (Signals tab)
9. Add US11: Configuration validation
10. Add API: REST endpoints for programmatic access
11. Run E2E journey test (Phase 12) to validate complete flow
12. Each increment adds analysis capability

### Recommended Execution Order (E2E Journey Focus)

```
Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2) → Phase 10 (US0)
                                                          ↓
                                              Phase 10.1 (US9 - CLI)
                                                          ↓
                                              [MVP CHECKPOINT: Run E2E test]
                                                          ↓
                                              Phase 10.2 (US10 - WebUI)
                                                          ↓
                                              Phase 12 (Full E2E Journey Test)
```

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability (US0-US11)
- TDD is mandatory per constitution - write failing tests first
- Commit after each task or logical group
- Memory management critical for long backtests (flush threshold: 10,000)
- No new dependencies required (uses existing stack)
- US0 is labeled P0 because it's the integration prerequisite, but depends on US1+US2 being complete first
- US9 is critical for E2E journey - without it, users cannot run signal-enabled backtests
- US10 integrates with existing WebUI (007-backtest-detail-view) - minimal new UI code
- Total tasks: T001-T129 (129 tasks across 12 phases)
