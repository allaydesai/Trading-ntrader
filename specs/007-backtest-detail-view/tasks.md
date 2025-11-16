# Tasks: Backtest Detail View & Metrics

**Input**: Design documents from `/specs/007-backtest-detail-view/`
**Prerequisites**: plan.md (tech stack), spec.md (user stories), research.md (decisions), data-model.md (entities), contracts/html-routes.md (endpoints), quickstart.md (test scenarios)

**Tests**: Tests are included in this task list following TDD principles per project constitution.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and file structure

- [X] T001 Create backtest detail view model file at src/api/models/backtest_detail.py
- [X] T002 Create detail page template file at templates/backtests/detail.html
- [X] T003 [P] Create metrics panel partial template at templates/partials/metrics_panel.html
- [X] T004 [P] Create config snapshot partial template at templates/partials/config_snapshot.html
- [X] T005 [P] Create trading summary partial template at templates/partials/trading_summary.html
- [X] T006 [P] Create test file at tests/ui/test_backtest_detail.py
- [X] T007 [P] Create model unit test file at tests/ui/test_backtest_detail_models.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [X] T008 Implement MetricDisplayItem Pydantic model with computed fields (formatted_value, color_class) in src/api/models/backtest_detail.py
- [X] T009 Implement MetricsPanel Pydantic model grouping return/risk/trading metrics in src/api/models/backtest_detail.py
- [X] T010 Implement ConfigurationSnapshot Pydantic model with cli_command computed field in src/api/models/backtest_detail.py
- [X] T011 Implement TradingSummary Pydantic model with has_trades computed field in src/api/models/backtest_detail.py
- [X] T012 Implement BacktestDetailView Pydantic model with all computed fields in src/api/models/backtest_detail.py
- [X] T013 Implement build_metrics_panel() mapping function in src/api/models/backtest_detail.py
- [X] T014 Implement build_configuration() mapping function in src/api/models/backtest_detail.py
- [X] T015 Implement build_trading_summary() mapping function in src/api/models/backtest_detail.py
- [X] T016 Implement to_detail_view() master mapping function in src/api/models/backtest_detail.py
- [X] T017 Add test fixture for sample BacktestRun with PerformanceMetrics in tests/ui/conftest.py
- [X] T018 [P] Unit tests for MetricDisplayItem (formatted_value, color_class) in tests/ui/test_backtest_detail_models.py
- [X] T019 [P] Unit tests for ConfigurationSnapshot (cli_command generation) in tests/ui/test_backtest_detail_models.py
- [X] T020 [P] Unit tests for BacktestDetailView computed fields in tests/ui/test_backtest_detail_models.py
- [X] T021 [P] Unit tests for mapping functions (build_metrics_panel, etc.) in tests/ui/test_backtest_detail_models.py

**Checkpoint**: Foundation ready - all Pydantic models and mapping functions tested. User story implementation can now begin.

---

## Phase 3: User Story 1 - View Complete Backtest Results (Priority: P1)

**Goal**: Display comprehensive performance metrics for a single backtest run with color-coding and tooltips

**Independent Test**: Navigate to `/backtests/{run_id}` and verify all metrics (Sharpe ratio, returns, drawdowns, etc.) are displayed accurately with proper color-coding and tooltip explanations.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T022 [P] [US1] Test GET /backtests/{run_id} returns 200 with valid UUID in tests/ui/test_backtest_detail.py
- [ ] T023 [P] [US1] Test GET /backtests/{run_id} returns 404 for non-existent UUID in tests/ui/test_backtest_detail.py
- [ ] T024 [P] [US1] Test detail page displays all return metrics (Total Return, CAGR, Final Balance) in tests/ui/test_backtest_detail.py
- [ ] T025 [P] [US1] Test detail page displays all risk metrics (Sharpe, Sortino, Max Drawdown, Volatility) in tests/ui/test_backtest_detail.py
- [ ] T026 [P] [US1] Test detail page displays all trading metrics (Total Trades, Win Rate, Profit Factor) in tests/ui/test_backtest_detail.py
- [ ] T027 [P] [US1] Test positive metrics are highlighted in green CSS class in tests/ui/test_backtest_detail.py
- [ ] T028 [P] [US1] Test negative metrics are highlighted in red CSS class in tests/ui/test_backtest_detail.py
- [ ] T029 [P] [US1] Test breadcrumb navigation shows Dashboard > Backtests > Run Details in tests/ui/test_backtest_detail.py

### Implementation for User Story 1

- [ ] T030 [US1] Add get_backtest_by_run_id() method to BacktestService in src/services/backtest_service.py
- [ ] T031 [US1] Implement GET /backtests/{run_id} route handler in src/api/ui/backtests.py
- [ ] T032 [US1] Implement 404 error handling for non-existent run_id in src/api/ui/backtests.py
- [ ] T033 [US1] Create base detail.html template with layout and breadcrumbs at templates/backtests/detail.html
- [ ] T034 [US1] Implement metrics_panel.html partial with categorized metrics display at templates/partials/metrics_panel.html
- [ ] T035 [US1] Add CSS tooltips for each metric with explanatory text in templates/partials/metrics_panel.html
- [ ] T036 [US1] Implement color-coded metric values using Tailwind classes (text-green-400, text-red-400) in templates/partials/metrics_panel.html
- [ ] T037 [US1] Implement trading_summary.html partial showing trade statistics at templates/partials/trading_summary.html
- [ ] T038 [US1] Add structlog logging for detail view route access in src/api/ui/backtests.py

**Checkpoint**: User Story 1 complete - Detail page displays all performance metrics with color-coding and tooltips. Core functionality is independently testable.

---

## Phase 4: User Story 2 - Review Trade History (Priority: P2)

**Goal**: Display trading summary with aggregated statistics (MVP - no individual trade rows due to data model constraints)

**Independent Test**: View trading summary panel showing total trades, winning/losing trades, win rate, average win/loss, profit factor, and expectancy metrics.

### Tests for User Story 2

> **NOTE: This is MVP implementation using aggregated metrics. Individual trade blotter deferred to future enhancement.**

- [ ] T039 [P] [US2] Test trading summary displays total trades count in tests/ui/test_backtest_detail.py
- [ ] T040 [P] [US2] Test trading summary displays winning/losing trade counts in tests/ui/test_backtest_detail.py
- [ ] T041 [P] [US2] Test trading summary displays win rate percentage in tests/ui/test_backtest_detail.py
- [ ] T042 [P] [US2] Test trading summary displays average win/loss amounts in tests/ui/test_backtest_detail.py
- [ ] T043 [P] [US2] Test trading summary handles zero trades gracefully in tests/ui/test_backtest_detail.py

### Implementation for User Story 2

- [ ] T044 [US2] Enhance trading_summary.html with complete statistics grid at templates/partials/trading_summary.html
- [ ] T045 [US2] Add color-coding for trading statistics (positive win rate green, etc.) in templates/partials/trading_summary.html
- [ ] T046 [US2] Handle edge case: zero trades with informative message in templates/partials/trading_summary.html
- [ ] T047 [US2] Add profit factor and expectancy display with tooltips in templates/partials/trading_summary.html

**Checkpoint**: User Story 2 complete - Trading summary displays all aggregated statistics. Note: Full trade blotter with individual trade rows is deferred to future enhancement requiring database schema changes.

---

## Phase 5: User Story 3 - View Backtest Configuration (Priority: P3)

**Goal**: Display immutable backtest configuration with collapsible section and CLI command copy functionality

**Independent Test**: View configuration panel showing all parameters (instrument, dates, capital, strategy settings) and successfully copy CLI command to clipboard.

### Tests for User Story 3

- [ ] T048 [P] [US3] Test configuration section displays instrument symbol in tests/ui/test_backtest_detail.py
- [ ] T049 [P] [US3] Test configuration section displays date range (start/end) in tests/ui/test_backtest_detail.py
- [ ] T050 [P] [US3] Test configuration section displays initial capital in tests/ui/test_backtest_detail.py
- [ ] T051 [P] [US3] Test configuration section displays strategy-specific parameters in tests/ui/test_backtest_detail.py
- [ ] T052 [P] [US3] Test CLI command generation includes all parameters in tests/ui/test_backtest_detail.py
- [ ] T053 [P] [US3] Test configuration section is collapsible (expanded by default) in tests/ui/test_backtest_detail.py

### Implementation for User Story 3

- [ ] T054 [US3] Implement config_snapshot.html partial with collapsible details element at templates/partials/config_snapshot.html
- [ ] T055 [US3] Display all configuration parameters in organized grid layout in templates/partials/config_snapshot.html
- [ ] T056 [US3] Add "Copy CLI Command" button with JavaScript clipboard functionality in templates/partials/config_snapshot.html
- [ ] T057 [US3] Display strategy-specific parameters from config_snapshot JSON in templates/partials/config_snapshot.html
- [ ] T058 [US3] Add visual feedback on successful clipboard copy (toast notification) in templates/partials/config_snapshot.html

**Checkpoint**: User Story 3 complete - Configuration parameters are displayed with collapsible section and CLI command can be copied to clipboard.

---

## Phase 6: User Story 4 - Perform Actions on Backtest (Priority: P4)

**Goal**: Provide action buttons for export, delete, and re-run operations with appropriate confirmations and feedback

**Independent Test**: Click each action button and verify expected behavior (export downloads file, delete shows confirmation, re-run triggers new execution).

### Tests for User Story 4

- [ ] T059 [P] [US4] Test GET /backtests/{run_id}/export returns HTML file download in tests/ui/test_backtest_detail.py
- [ ] T060 [P] [US4] Test DELETE /backtests/{run_id} removes record and redirects in tests/ui/test_backtest_detail.py
- [ ] T061 [P] [US4] Test DELETE /backtests/{run_id} returns 404 for non-existent backtest in tests/ui/test_backtest_detail.py
- [ ] T062 [P] [US4] Test POST /backtests/{run_id}/rerun creates new backtest run in tests/ui/test_backtest_detail.py
- [ ] T063 [P] [US4] Test POST /backtests/{run_id}/rerun returns 404 for non-existent backtest in tests/ui/test_backtest_detail.py
- [ ] T064 [P] [US4] Test export includes correct Content-Disposition header in tests/ui/test_backtest_detail.py

### Implementation for User Story 4

- [ ] T065 [US4] Add delete_backtest() method to BacktestService in src/services/backtest_service.py
- [ ] T066 [US4] Add generate_html_report() method to BacktestService in src/services/backtest_service.py
- [ ] T067 [US4] Add rerun_backtest() method to BacktestService in src/services/backtest_service.py
- [ ] T068 [US4] Implement DELETE /backtests/{run_id} route with HTMX redirect in src/api/ui/backtests.py
- [ ] T069 [US4] Implement GET /backtests/{run_id}/export route for HTML report download in src/api/ui/backtests.py
- [ ] T070 [US4] Implement POST /backtests/{run_id}/rerun route for backtest re-execution in src/api/ui/backtests.py
- [ ] T071 [US4] Add action buttons section (Export, Delete, Re-run) to detail.html template at templates/backtests/detail.html
- [ ] T072 [US4] Implement HTMX delete button with confirmation dialog in templates/backtests/detail.html
- [ ] T073 [US4] Implement export button as download link with proper content-disposition in templates/backtests/detail.html
- [ ] T074 [US4] Implement re-run button with HTMX POST and loading indicator in templates/backtests/detail.html
- [ ] T075 [US4] Add success/error notification toast components in templates/backtests/detail.html
- [ ] T076 [US4] Add structlog logging for all action operations (delete, export, rerun) in src/api/ui/backtests.py

**Checkpoint**: User Story 4 complete - All action buttons functional with proper confirmations, redirects, and error handling.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T077 Run ruff format on all new Python files (src/api/models/backtest_detail.py, tests/)
- [ ] T078 Run ruff check on all new Python files and fix any issues
- [ ] T079 Run mypy type checking on src/api/models/backtest_detail.py
- [ ] T080 [P] Add Google-style docstrings to all public functions in src/api/models/backtest_detail.py
- [ ] T081 [P] Add Google-style docstrings to all route handlers in src/api/ui/backtests.py
- [ ] T082 [P] Add Google-style docstrings to all service methods in src/services/backtest_service.py
- [ ] T083 Run full test suite to ensure no regressions (pytest tests/ -v)
- [ ] T084 Verify test coverage meets 80% minimum for new code (pytest --cov=src/api)
- [ ] T085 Validate quickstart.md manual testing checklist items
- [ ] T086 Performance test: Detail page load under 1 second
- [ ] T087 Performance test: Export generation under 2 seconds
- [ ] T088 Verify dark theme consistency across all new templates

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-6)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2 → P3 → P4)
- **Polish (Phase 7)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P2)**: Can start after Phase 2 - Uses trading_summary.html created in US1
- **User Story 3 (P3)**: Can start after Phase 2 - Independently testable
- **User Story 4 (P4)**: Can start after Phase 2 - Adds actions to detail.html created in US1

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before services (handled in Foundational)
- Services before routes
- Routes before templates
- Core implementation before polish

### Parallel Opportunities

- All Setup tasks T001-T007 can run in parallel (different files)
- Foundational tasks T008-T016 are sequential (same file dependencies)
- Unit tests T018-T021 can run in parallel (different test functions)
- All user story tests marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members

---

## Parallel Example: User Story 1 Tests

```bash
# Launch all US1 tests together (they are independent):
Task: "Test GET /backtests/{run_id} returns 200 with valid UUID"
Task: "Test GET /backtests/{run_id} returns 404 for non-existent UUID"
Task: "Test detail page displays all return metrics"
Task: "Test detail page displays all risk metrics"
Task: "Test detail page displays all trading metrics"
Task: "Test positive metrics are highlighted in green CSS class"
Task: "Test negative metrics are highlighted in red CSS class"
Task: "Test breadcrumb navigation shows Dashboard > Backtests > Run Details"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001-T007)
2. Complete Phase 2: Foundational (T008-T021)
3. Complete Phase 3: User Story 1 (T022-T038)
4. **STOP and VALIDATE**: Test detail page displays all metrics with color-coding and tooltips
5. Deploy/demo if ready - Core value delivered

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Add User Story 2 → Trading summary enhanced
4. Add User Story 3 → Configuration view with CLI copy
5. Add User Story 4 → Action buttons (export, delete, re-run)
6. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (metrics display)
   - Developer B: User Story 3 (configuration view)
   - Developer C: User Story 4 (action buttons)
3. Stories complete and integrate independently
4. User Story 2 requires US1 template base, so schedule accordingly

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing (TDD)
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Individual trade blotter (with sorting/filtering/pagination) deferred to future enhancement requiring database schema changes per research.md
- MVP uses aggregated trading metrics from PerformanceMetrics table
