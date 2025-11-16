# Tasks: Interactive Backtest Lists

**Input**: Design documents from `/specs/006-interactive-backtest-lists/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: TDD approach required per project constitution. Tests are written FIRST, must FAIL before implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root
- Paths assume existing Phase 1 structure from 005-webui-foundation

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create foundational models and database infrastructure needed by all user stories

- [X] T001 [P] Create SortOrder and ExecutionStatus enums in src/api/models/filter_models.py
- [X] T002 [P] Create SortColumn enum in src/api/models/filter_models.py
- [X] T003 Create FilterState Pydantic model with validation in src/api/models/filter_models.py
- [X] T004 [P] Create SortableColumn model in src/api/models/filter_models.py
- [X] T005 [P] Create PaginationControl model in src/api/models/filter_models.py
- [X] T006 Create FilteredBacktestListPage response model in src/api/models/backtest_list.py
- [X] T007 Create Alembic migration for filter indexes in src/db/migrations/versions/

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**WARNING**: No user story work can begin until this phase is complete

- [X] T008 Write unit tests for FilterState validation (date range, page bounds) in tests/api/models/test_filter_models.py
- [X] T009 Write unit tests for FilterState.to_query_params() in tests/api/models/test_filter_models.py
- [X] T010 [P] Write unit tests for FilterState.with_sort() toggle behavior in tests/api/models/test_filter_models.py
- [X] T011 [P] Write unit tests for FilterState.with_page() in tests/api/models/test_filter_models.py
- [X] T012 [P] Write unit tests for FilterState.clear_filters() in tests/api/models/test_filter_models.py
- [ ] T013 Write repository test for get_filtered_backtests() base query in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T014 Write repository test for pagination logic in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T015 Write repository test for get_distinct_strategies() in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T016 [P] Write repository test for get_distinct_instruments() in tests/db/repositories/test_backtest_repository_filters.py
- [X] T017 Implement get_filtered_backtests() base query in src/db/repositories/backtest_repository.py
- [X] T018 Implement get_distinct_strategies() in src/db/repositories/backtest_repository.py
- [X] T019 [P] Implement get_distinct_instruments() in src/db/repositories/backtest_repository.py
- [ ] T020 Write service test for get_filtered_backtest_list_page() in tests/services/test_backtest_query_filters.py
- [ ] T021 Implement get_filtered_backtest_list_page() in src/services/backtest_query.py
- [ ] T022 Run Alembic migration to add database indexes

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Filter Backtests by Strategy (Priority: P1) MVP

**Goal**: Filter backtest list by strategy name via dropdown

**Independent Test**: Select strategy from dropdown, verify only matching backtests appear in table without full page reload

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T023 [P] [US1] Write repository test for filter_by_strategy in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T024 [P] [US1] Write endpoint test for strategy filter parameter in tests/api/ui/test_backtests_filters.py
- [ ] T025 [US1] Write integration test for strategy filter with HTMX fragment in tests/integration/test_filter_state_persistence.py

### Implementation for User Story 1

- [ ] T026 [US1] Add strategy filter to get_filtered_backtests() WHERE clause in src/db/repositories/backtest_repository.py
- [ ] T027 [US1] Update /backtests endpoint to accept strategy query parameter in src/api/ui/backtests.py
- [ ] T028 [US1] Update /backtests/fragment endpoint to accept strategy query parameter in src/api/ui/backtests.py
- [ ] T029 [US1] Add strategy dropdown to filter form in templates/backtests/list.html
- [ ] T030 [US1] Pass available_strategies to template context in src/api/ui/backtests.py
- [ ] T031 [US1] Add Clear Filters button functionality in templates/backtests/list.html
- [ ] T032 [US1] Add empty results message when no backtests match filters in templates/backtests/list_fragment.html

**Checkpoint**: User Story 1 complete - Strategy filtering works independently

---

## Phase 4: User Story 2 - Sort Backtests by Performance Metrics (Priority: P1)

**Goal**: Sort backtest table by any column with clickable headers and direction indicators

**Independent Test**: Click column headers, verify data reorders correctly with visual sort indicator

### Tests for User Story 2

- [ ] T033 [P] [US2] Write repository test for sort by Sharpe descending in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T034 [P] [US2] Write repository test for sort by Total Return ascending in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T035 [P] [US2] Write repository test for sort by Max Drawdown in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T036 [US2] Write endpoint test for sort and order parameters in tests/api/ui/test_backtests_filters.py
- [ ] T037 [US2] Write integration test for sort toggle behavior in tests/integration/test_filter_state_persistence.py

### Implementation for User Story 2

- [ ] T038 [US2] Add sort column/order logic to get_filtered_backtests() in src/db/repositories/backtest_repository.py
- [ ] T039 [US2] Handle JOIN with PerformanceMetrics for metrics sorting in src/db/repositories/backtest_repository.py
- [ ] T040 [US2] Update /backtests endpoint to accept sort and order parameters in src/api/ui/backtests.py
- [ ] T041 [US2] Update /backtests/fragment endpoint to accept sort and order parameters in src/api/ui/backtests.py
- [ ] T042 [US2] Create sortable column headers with hx-get in templates/backtests/list_fragment.html
- [ ] T043 [US2] Add sort direction indicators (arrows) to column headers in templates/backtests/list_fragment.html
- [ ] T044 [US2] Generate sortable column metadata in service layer in src/services/backtest_query.py

**Checkpoint**: User Story 2 complete - Sorting works independently

---

## Phase 5: User Story 3 - Navigate Paginated Results (Priority: P2)

**Goal**: Browse through pages of backtest results with pagination controls

**Independent Test**: Navigate between pages using Previous/Next/Page number buttons, verify correct data on each page

### Tests for User Story 3

- [ ] T045 [P] [US3] Write repository test for pagination offset/limit in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T046 [P] [US3] Write repository test for total count with filters in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T047 [US3] Write endpoint test for page and page_size parameters in tests/api/ui/test_backtests_filters.py
- [ ] T048 [US3] Write integration test for pagination state preservation in tests/integration/test_filter_state_persistence.py

### Implementation for User Story 3

- [ ] T049 [US3] Add offset/limit pagination to get_filtered_backtests() in src/db/repositories/backtest_repository.py
- [ ] T050 [US3] Return total_count for pagination metadata in src/db/repositories/backtest_repository.py
- [ ] T051 [US3] Update /backtests endpoint to accept page and page_size in src/api/ui/backtests.py
- [ ] T052 [US3] Update /backtests/fragment endpoint to accept page and page_size in src/api/ui/backtests.py
- [ ] T053 [US3] Generate PaginationControl list in service layer in src/services/backtest_query.py
- [ ] T054 [US3] Add pagination controls (Previous/Next/Page numbers) in templates/backtests/list_fragment.html
- [ ] T055 [US3] Disable Previous button on first page in templates/backtests/list_fragment.html
- [ ] T056 [US3] Disable Next button on last page in templates/backtests/list_fragment.html
- [ ] T057 [US3] Preserve all filter params in pagination links in templates/backtests/list_fragment.html

**Checkpoint**: User Story 3 complete - Pagination works independently

---

## Phase 6: User Story 4 - Filter by Instrument Symbol (Priority: P2)

**Goal**: Filter backtests by instrument symbol with autocomplete suggestions

**Independent Test**: Type instrument symbol, verify filtered results appear with autocomplete suggestions

### Tests for User Story 4

- [ ] T058 [P] [US4] Write repository test for instrument partial match filter in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T059 [P] [US4] Write repository test for case-insensitive instrument matching in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T060 [US4] Write endpoint test for instrument filter parameter in tests/api/ui/test_backtests_filters.py
- [ ] T061 [US4] Write endpoint test for /backtests/instruments autocomplete in tests/api/ui/test_backtests_filters.py

### Implementation for User Story 4

- [ ] T062 [US4] Add instrument filter (ilike partial match) to get_filtered_backtests() in src/db/repositories/backtest_repository.py
- [ ] T063 [US4] Update /backtests endpoint to accept instrument parameter in src/api/ui/backtests.py
- [ ] T064 [US4] Update /backtests/fragment endpoint to accept instrument parameter in src/api/ui/backtests.py
- [ ] T065 [US4] Create /backtests/instruments endpoint for autocomplete in src/api/ui/backtests.py
- [ ] T066 [US4] Add instrument text input with datalist to filter form in templates/backtests/list.html
- [ ] T067 [US4] Connect instrument input to autocomplete endpoint with HTMX in templates/backtests/list.html

**Checkpoint**: User Story 4 complete - Instrument filtering works independently

---

## Phase 7: User Story 5 - Filter by Date Range (Priority: P2)

**Goal**: Filter backtests by execution date range

**Independent Test**: Set date range, verify only backtests within that period appear

### Tests for User Story 5

- [ ] T068 [P] [US5] Write repository test for date_from filter in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T069 [P] [US5] Write repository test for date_to filter in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T070 [P] [US5] Write repository test for combined date range filter in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T071 [US5] Write endpoint test for date_from and date_to parameters in tests/api/ui/test_backtests_filters.py
- [ ] T072 [US5] Write endpoint test for invalid date range error in tests/api/ui/test_backtests_filters.py

### Implementation for User Story 5

- [ ] T073 [US5] Add date_from filter to get_filtered_backtests() WHERE clause in src/db/repositories/backtest_repository.py
- [ ] T074 [US5] Add date_to filter to get_filtered_backtests() WHERE clause in src/db/repositories/backtest_repository.py
- [ ] T075 [US5] Update /backtests endpoint to accept date_from and date_to in src/api/ui/backtests.py
- [ ] T076 [US5] Update /backtests/fragment endpoint to accept date_from and date_to in src/api/ui/backtests.py
- [ ] T077 [US5] Add date picker inputs to filter form in templates/backtests/list.html
- [ ] T078 [US5] Display validation error message for invalid date range in templates/backtests/list.html

**Checkpoint**: User Story 5 complete - Date range filtering works independently

---

## Phase 8: User Story 6 - Persist Filters in URL (Priority: P3)

**Goal**: Reflect filter/sort settings in URL for bookmarking and sharing

**Independent Test**: Apply filters, copy URL, open in new session, verify same filtered view loads

### Tests for User Story 6

- [ ] T079 [US6] Write integration test for URL parameter encoding in tests/integration/test_filter_state_persistence.py
- [ ] T080 [US6] Write integration test for URL state restoration on page load in tests/integration/test_filter_state_persistence.py
- [ ] T081 [US6] Write integration test for page refresh preserving filters in tests/integration/test_filter_state_persistence.py
- [ ] T082 [US6] Write integration test for Clear Filters resetting URL in tests/integration/test_filter_state_persistence.py

### Implementation for User Story 6

- [ ] T083 [US6] Add hx-push-url="true" to filter form in templates/backtests/list.html
- [ ] T084 [US6] Add hx-push-url="true" to sort headers in templates/backtests/list_fragment.html
- [ ] T085 [US6] Add hx-push-url="true" to pagination controls in templates/backtests/list_fragment.html
- [ ] T086 [US6] Ensure /backtests page restores FilterState from URL params in src/api/ui/backtests.py
- [ ] T087 [US6] Handle invalid URL parameters gracefully (ignore invalid, apply valid) in src/api/ui/backtests.py

**Checkpoint**: User Story 6 complete - URL persistence works independently

---

## Phase 9: User Story 7 - Filter by Backtest Status (Priority: P3)

**Goal**: Filter backtests by execution status (success/failure)

**Independent Test**: Select status, verify only matching backtests appear; combine with other filters

### Tests for User Story 7

- [ ] T088 [P] [US7] Write repository test for status filter in tests/db/repositories/test_backtest_repository_filters.py
- [ ] T089 [US7] Write endpoint test for status filter parameter in tests/api/ui/test_backtests_filters.py
- [ ] T090 [US7] Write integration test for combined filters (status + strategy) in tests/integration/test_filter_state_persistence.py

### Implementation for User Story 7

- [ ] T091 [US7] Add status filter to get_filtered_backtests() WHERE clause in src/db/repositories/backtest_repository.py
- [ ] T092 [US7] Update /backtests endpoint to accept status parameter in src/api/ui/backtests.py
- [ ] T093 [US7] Update /backtests/fragment endpoint to accept status parameter in src/api/ui/backtests.py
- [ ] T094 [US7] Add status dropdown to filter form in templates/backtests/list.html

**Checkpoint**: User Story 7 complete - Status filtering works independently

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T095 [P] Add loading indicator (htmx-indicator) during filter operations in templates/backtests/list.html
- [ ] T096 [P] Add CSS styling for sortable headers (cursor-pointer, hover effects) in templates/backtests/list.html
- [ ] T097 Run full test suite and verify >80% coverage: `uv run pytest --cov=src`
- [ ] T098 Run type checking: `uv run mypy src/`
- [ ] T099 Run linting and formatting: `uv run ruff check . && uv run ruff format .`
- [ ] T100 Performance testing: Verify <200ms filter response with 10,000+ backtests
- [ ] T101 Update README.md with new interactive features documentation
- [ ] T102 Run quickstart.md verification checklist

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion - BLOCKS all user stories
- **User Stories (Phase 3-9)**: All depend on Phase 2 completion
  - User stories can proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 -> P2 -> P3)
- **Polish (Phase 10)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Phase 2 - No dependencies on other stories
- **User Story 2 (P1)**: Can start after Phase 2 - No dependencies on other stories
- **User Story 3 (P2)**: Can start after Phase 2 - No dependencies on other stories
- **User Story 4 (P2)**: Can start after Phase 2 - No dependencies on other stories
- **User Story 5 (P2)**: Can start after Phase 2 - No dependencies on other stories
- **User Story 6 (P3)**: Can start after Phase 2 - Benefits from US1-5 being complete for full testing
- **User Story 7 (P3)**: Can start after Phase 2 - No dependencies on other stories

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Repository layer tests before service layer
- Service layer before endpoints
- Endpoints before templates
- Story complete before moving to next priority

### Parallel Opportunities

- Phase 1: T001, T002, T004, T005 can all run in parallel
- Phase 2: Tests (T008-T016) can mostly run in parallel
- Phase 3+: Within each story, tests marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members
- US1 and US2 are both P1 and can be developed concurrently

---

## Parallel Example: User Story 2 (Sorting)

```bash
# Launch all sorting repository tests in parallel:
Task: "Write repository test for sort by Sharpe descending" (T033)
Task: "Write repository test for sort by Total Return ascending" (T034)
Task: "Write repository test for sort by Max Drawdown" (T035)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (enums, models, migration)
2. Complete Phase 2: Foundational (base repository, service layer tests)
3. Complete Phase 3: User Story 1 (strategy filtering)
4. **STOP and VALIDATE**: Test strategy filtering independently
5. Deploy/demo if ready - users can now filter by strategy!

### Incremental Delivery

1. Complete Setup + Foundational -> Foundation ready
2. Add User Story 1 (Strategy Filter) -> Test independently -> **MVP Demo!**
3. Add User Story 2 (Sorting) -> Test independently -> Deploy/Demo
4. Add User Story 3 (Pagination) -> Test independently -> Deploy/Demo
5. Add User Story 4 (Instrument) -> Test independently -> Deploy/Demo
6. Add User Story 5 (Date Range) -> Test independently -> Deploy/Demo
7. Add User Story 6 (URL Persistence) -> Test independently -> Deploy/Demo
8. Add User Story 7 (Status) -> Test independently -> Deploy/Demo
9. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Phase 2 is done:
   - Developer A: User Story 1 (Strategy Filter)
   - Developer B: User Story 2 (Sorting)
   - Developer C: User Story 3 (Pagination)
3. Then:
   - Developer A: User Story 4 (Instrument)
   - Developer B: User Story 5 (Date Range)
   - Developer C: User Story 6 (URL Persistence)
4. Finally:
   - Any developer: User Story 7 (Status)
5. Stories complete and integrate independently

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing (TDD Red-Green-Refactor)
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
- Total tasks: 102
- Tasks per user story: US1 (10), US2 (12), US3 (13), US4 (10), US5 (11), US6 (9), US7 (7)
- Setup/Foundation: 22 tasks
- Polish: 8 tasks
