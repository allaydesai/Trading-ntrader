# Tasks: Web UI Foundation

**Input**: Design documents from `/specs/005-webui-foundation/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Test tasks are included as this project follows TDD principles (80% minimum coverage per constitution).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure for web UI

- [X] T001 Install web dependencies using `uv add jinja2 python-multipart`
- [X] T002 Create template directory structure: templates/, templates/backtests/, templates/partials/
- [X] T003 [P] Create static asset directories: static/css/, static/vendor/
- [X] T004 [P] Create API source directories: src/api/ui/, tests/api/
- [X] T005 [P] Download HTMX library to static/vendor/htmx.min.js
- [X] T006 [P] Create __init__.py files for new Python packages (src/api/__init__.py, src/api/ui/__init__.py)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T007 Create main FastAPI web application in src/api/web.py with static file mounting and template configuration
- [X] T008 Create base HTML template with Tailwind CDN and HTMX includes in templates/base.html
- [X] T009 [P] Create view models package in src/api/models/__init__.py
- [X] T010 [P] Create NavigationState and BreadcrumbItem Pydantic models in src/api/models/navigation.py
- [X] T011 [P] Create EmptyStateMessage Pydantic model in src/api/models/common.py
- [X] T012 Create FastAPI dependencies for database session and templates in src/api/dependencies.py
- [X] T013 Create pytest fixtures for web UI testing in tests/api/conftest.py

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - View Dashboard Summary (Priority: P1) 🎯 MVP

**Goal**: Display dashboard with summary statistics and recent backtest activity

**Independent Test**: Open web interface at root URL, verify summary statistics (total backtests, best Sharpe, worst drawdown) and recent activity list display correctly

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T014 [P] [US1] Test dashboard returns 200 status code in tests/api/test_dashboard.py
- [X] T015 [P] [US1] Test dashboard displays total backtest count in tests/api/test_dashboard.py
- [X] T016 [P] [US1] Test dashboard displays best Sharpe ratio and strategy name in tests/api/test_dashboard.py
- [X] T017 [P] [US1] Test dashboard displays worst max drawdown and strategy name in tests/api/test_dashboard.py
- [X] T018 [P] [US1] Test dashboard displays 5 most recent backtests in tests/api/test_dashboard.py
- [X] T019 [P] [US1] Test dashboard shows empty state when no backtests exist in tests/api/test_dashboard.py
- [X] T020 [P] [US1] Test dashboard includes quick action links in tests/api/test_dashboard.py

### Implementation for User Story 1

- [X] T021 [P] [US1] Create DashboardSummary Pydantic view model in src/api/models/dashboard.py
- [X] T022 [P] [US1] Create RecentBacktestItem Pydantic view model in src/api/models/dashboard.py
- [X] T023 [US1] Extend BacktestQueryService with get_dashboard_stats() method in src/services/backtest_query.py
- [X] T024 [US1] Extend BacktestQueryService with get_recent_activity(limit=5) method in src/services/backtest_query.py
- [X] T025 [US1] Create mapping function to_recent_item() in src/api/models/dashboard.py
- [X] T026 [US1] Create dashboard router with GET / endpoint in src/api/ui/dashboard.py
- [X] T027 [US1] Create dashboard template with stats cards in templates/dashboard.html
- [X] T028 [US1] Add empty state handling for dashboard (no backtests scenario) in templates/dashboard.html
- [X] T029 [US1] Register dashboard router in src/api/web.py
- [X] T030 [US1] Apply dark theme colors to dashboard (bg-slate-950, text-slate-100) in templates/dashboard.html

**Checkpoint**: Dashboard displays summary statistics and recent activity. Can be tested independently by visiting http://localhost:8000/

---

## Phase 4: User Story 2 - Navigate Between Sections (Priority: P1)

**Goal**: Provide persistent navigation menu with active state highlighting and breadcrumb trail

**Independent Test**: Click each navigation link and verify correct page loads with appropriate menu highlighting and breadcrumb updates

### Tests for User Story 2

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T031 [P] [US2] Test navigation bar appears on dashboard page in tests/api/test_navigation.py
- [X] T032 [P] [US2] Test navigation contains links to Dashboard, Backtests, Data, Docs in tests/api/test_navigation.py
- [X] T033 [P] [US2] Test Dashboard link is highlighted when on dashboard page in tests/api/test_navigation.py
- [X] T034 [P] [US2] Test Backtests link is highlighted when on backtests page in tests/api/test_navigation.py
- [X] T035 [P] [US2] Test breadcrumb displays current page location in tests/api/test_navigation.py
- [X] T036 [P] [US2] Test footer appears with version info and docs link in tests/api/test_navigation.py

### Implementation for User Story 2

- [X] T037 [US2] Create navigation partial template with menu items in templates/partials/nav.html
- [X] T038 [US2] Add active page highlighting logic using Jinja2 conditionals in templates/partials/nav.html
- [X] T039 [US2] Create breadcrumb partial template in templates/partials/breadcrumbs.html
- [X] T040 [US2] Create footer partial template with version and links in templates/partials/footer.html
- [X] T041 [US2] Include navigation, breadcrumbs, and footer partials in templates/base.html
- [X] T042 [US2] Update dashboard route to pass NavigationState context in src/api/ui/dashboard.py
- [X] T043 [US2] Apply dark theme to navigation (border-slate-700, hover states) in templates/partials/nav.html
- [X] T044 [US2] Apply dark theme to footer in templates/partials/footer.html

**Checkpoint**: All pages show consistent navigation with active highlighting. Breadcrumbs update based on page context.

---

## Phase 5: User Story 3 - View Backtest List (Priority: P1)

**Goal**: Display paginated table of all backtest runs with key metrics and navigation to detail pages

**Independent Test**: Navigate to /backtests, verify table displays with correct columns, pagination works, and rows are clickable

### Tests for User Story 3

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T045 [P] [US3] Test backtest list returns 200 status code in tests/api/test_backtests.py
- [X] T046 [P] [US3] Test backtest list displays table with correct columns in tests/api/test_backtests.py
- [X] T047 [P] [US3] Test backtest list shows 20 results per page by default in tests/api/test_backtests.py
- [X] T048 [P] [US3] Test backtest list pagination controls appear when >20 results in tests/api/test_backtests.py
- [X] T049 [P] [US3] Test backtest list rows are clickable (navigate to detail) in tests/api/test_backtests.py
- [X] T050 [P] [US3] Test backtest list shows empty state when no backtests in tests/api/test_backtests.py
- [ ] T051 [P] [US3] Test backtest list page loads within 300ms with 100 backtests in tests/api/test_backtests.py
- [X] T052 [P] [US3] Test HTMX fragment endpoint returns partial HTML in tests/api/test_backtests.py

### Implementation for User Story 3

- [X] T053 [P] [US3] Create BacktestListItem Pydantic view model in src/api/models/backtest_list.py
- [X] T054 [P] [US3] Create BacktestListPage Pydantic view model with pagination in src/api/models/backtest_list.py
- [X] T055 [US3] Create mapping function to_list_item() in src/api/models/backtest_list.py
- [X] T056 [US3] Extend BacktestQueryService with paginated list method supporting offset in src/services/backtest_query.py
- [X] T057 [US3] Create backtests router with GET /backtests endpoint in src/api/ui/backtests.py
- [X] T058 [US3] Create backtest list template with table and columns in templates/backtests/list.html
- [X] T059 [US3] Add pagination controls using HTMX hx-get and hx-target in templates/backtests/list.html
- [X] T060 [US3] Create HTMX fragment template for partial table updates in templates/backtests/list_fragment.html
- [X] T061 [US3] Add GET /backtests/fragment endpoint for HTMX partial updates in src/api/ui/backtests.py
- [X] T062 [US3] Add empty state handling for backtest list in templates/backtests/list.html
- [X] T063 [US3] Make table rows clickable with link to future detail page in templates/backtests/list.html
- [X] T064 [US3] Register backtests router in src/api/web.py
- [X] T065 [US3] Apply dark theme to table (alternate row colors, borders) in templates/backtests/list.html
- [X] T066 [US3] Apply color coding: green for positive returns, red for negative in templates/backtests/list.html

**Checkpoint**: Backtest list displays with pagination. HTMX updates table without page reload. Each row links to detail page (placeholder for Phase 2).

---

## Phase 6: User Story 4 - Dark Mode Interface (Priority: P2)

**Goal**: Ensure consistent dark color scheme with proper contrast and accessibility

**Independent Test**: View any page, verify dark background with light text and proper color coding for metrics

### Tests for User Story 4

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T067 [P] [US4] Test all pages have dark background class (bg-slate-950) in tests/api/test_theme.py
- [X] T068 [P] [US4] Test all pages have light text class (text-slate-100) in tests/api/test_theme.py
- [X] T069 [P] [US4] Test positive metrics display in green (text-green-500) in tests/api/test_theme.py
- [X] T070 [P] [US4] Test negative metrics display in red (text-red-500) in tests/api/test_theme.py

### Implementation for User Story 4

- [X] T071 [US4] Configure Tailwind dark theme colors globally in templates/base.html body class
- [X] T072 [US4] Create CSS utilities for metric coloring (positive/negative) in static/css/app.css
- [X] T073 [US4] Verify contrast ratio meets WCAG AA (4.5:1 minimum) in all templates
- [X] T074 [US4] Add hover and focus states with appropriate contrast in templates/base.html

**Checkpoint**: Dark theme applied consistently across all pages with proper accessibility standards.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T075 [P] Add error handling for database connection failures in src/api/ui/dashboard.py
- [X] T076 [P] Add error handling for database connection failures in src/api/ui/backtests.py
- [X] T077 Handle null/missing metric values with "N/A" display in all templates
- [X] T078 Add truncation with ellipsis for long strategy names (max 50 chars) in templates
- [X] T079 [P] Add logging for web route requests using structlog in src/api/ui/dashboard.py
- [X] T080 [P] Add logging for web route requests using structlog in src/api/ui/backtests.py
- [X] T081 Run ruff format and ruff check on all src/api/ files
- [X] T082 Run mypy type checking on src/api/ modules
- [X] T083 Verify test coverage meets 80% threshold for src/api/ with pytest --cov
- [X] T084 Manual verification: Check all success criteria from spec.md
- [X] T085 Update README.md with web UI startup instructions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phases 3-6)**: All depend on Foundational phase completion
  - User stories can proceed in parallel (if staffed)
  - Or sequentially in priority order (US1 → US2 → US3 → US4)
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (Dashboard)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (Navigation)**: Can start after Foundational (Phase 2) - Integrates with US1 but independently testable
- **User Story 3 (Backtest List)**: Can start after Foundational (Phase 2) - Integrates with US2 navigation but independently testable
- **User Story 4 (Dark Mode)**: Can start after Foundational (Phase 2) - Applied across all pages but independently testable

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- View models before services
- Services before route handlers
- Templates after route handlers
- Integration and polish after core implementation
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel (T003, T004, T005, T006)
- All Foundational tasks marked [P] can run in parallel (T009, T010, T011)
- All tests for a user story marked [P] can run in parallel
- View models within a story marked [P] can run in parallel
- Different user stories can be worked on in parallel by different team members
- Error handling tasks in Polish phase can run in parallel (T075, T076)

---

## Parallel Example: User Story 1 - Tests

```bash
# Launch all tests for User Story 1 together (they test different aspects):
Task T014: "Test dashboard returns 200 status code in tests/api/test_dashboard.py"
Task T015: "Test dashboard displays total backtest count in tests/api/test_dashboard.py"
Task T016: "Test dashboard displays best Sharpe ratio and strategy name in tests/api/test_dashboard.py"
Task T017: "Test dashboard displays worst max drawdown and strategy name in tests/api/test_dashboard.py"
Task T018: "Test dashboard displays 5 most recent backtests in tests/api/test_dashboard.py"
Task T019: "Test dashboard shows empty state when no backtests exist in tests/api/test_dashboard.py"
Task T020: "Test dashboard includes quick action links in tests/api/test_dashboard.py"
```

## Parallel Example: User Story 1 - View Models

```bash
# Launch view model creation in parallel:
Task T021: "Create DashboardSummary Pydantic view model in src/api/models/dashboard.py"
Task T022: "Create RecentBacktestItem Pydantic view model in src/api/models/dashboard.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 - Dashboard
4. **STOP and VALIDATE**: Test dashboard independently at http://localhost:8000/
5. Deploy/demo if ready - users can see system overview

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 (Dashboard) → Test independently → MVP with system overview
3. Add User Story 2 (Navigation) → Test independently → Users can navigate
4. Add User Story 3 (Backtest List) → Test independently → Full browsing capability
5. Add User Story 4 (Dark Mode) → Test independently → Professional appearance
6. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (Dashboard)
   - Developer B: User Story 2 (Navigation)
   - Developer C: User Story 3 (Backtest List)
3. Stories complete and integrate independently
4. Developer D: User Story 4 (Dark Mode) - applies across completed stories

---

## Summary Statistics

- **Total Tasks**: 85
- **Phase 1 (Setup)**: 6 tasks
- **Phase 2 (Foundational)**: 7 tasks
- **Phase 3 (US1 - Dashboard)**: 17 tasks (7 tests + 10 implementation)
- **Phase 4 (US2 - Navigation)**: 14 tasks (6 tests + 8 implementation)
- **Phase 5 (US3 - Backtest List)**: 22 tasks (8 tests + 14 implementation)
- **Phase 6 (US4 - Dark Mode)**: 8 tasks (4 tests + 4 implementation)
- **Phase 7 (Polish)**: 11 tasks
- **Parallel Opportunities**: 40+ tasks marked with [P]
- **Suggested MVP Scope**: Setup + Foundational + User Story 1 (30 tasks total)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing (TDD red-green-refactor)
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- Dark theme (US4) can be integrated during other story implementation or as separate phase
- Performance targets: Dashboard <500ms, Backtest list <300ms for 20 results
