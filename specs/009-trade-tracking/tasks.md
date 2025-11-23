# Tasks: Individual Trade Tracking & Equity Curve Generation

**Feature**: 009-trade-tracking
**Input**: Design documents from `/specs/009-trade-tracking/`
**Prerequisites**: plan.md (✅), spec.md (✅), research.md (✅), data-model.md (✅), contracts/ (✅)

**Tests**: This feature follows TDD approach - tests are included for all critical paths with minimum 80% coverage target.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

---

## 📊 Implementation Progress

**Last Updated**: 2025-11-23 (Session 4 Complete)

### Overall Status: User Story 4 COMPLETE ✅ - Drawdown Analysis Fully Implemented, Tested & UI Integrated 🎉

| Phase | Status | Tasks Complete | Notes |
|-------|--------|----------------|-------|
| **Phase 1: Setup** | ✅ COMPLETE | 2/3 (67%) | Database migration & models ready |
| **Phase 2: Foundational** | ✅ COMPLETE | 11/11 (100%) | All data models & migration complete |
| **Phase 3: US1 Tests** | ✅ COMPLETE | 6/6 (100%) | 16 tests passing (12 unit + 4 integration) |
| **Phase 3: US1 Implementation** | ✅ COMPLETE | 5/5 (100%) | Trade capture fully functional |
| **Phase 4: US2** | ✅ COMPLETE | 12/12 (100%) | Equity curve API + UI chart integration complete |
| **Phase 5: US3** | ✅ COMPLETE | 10/10 (100%) | Trade statistics with 8 passing tests |
| **Phase 6: US4** | ✅ COMPLETE | 10/10 (100%) | Drawdown analysis with 6 passing tests + UI integration complete |
| **Phase 7-9: US5-US7** | ⏳ PENDING | 0/35 (0%) | UI Table, Export, Filtering |
| **Phase 10: Polish** | ⏳ PENDING | 0/16 (0%) | Code quality & validation |

**Total Progress**: 56/108 tasks complete (52%)

### 🎯 Key Achievements
- ✅ Database schema created with optimized indexes
- ✅ Complete data model suite (8 Pydantic models)
- ✅ 38/38 tests passing with TDD approach (16 US1 + 8 US2 + 8 US3 + 6 US4)
- ✅ Bulk insert performance: 500 trades in <1s (5x faster than requirement!)
- ✅ Equity curve generation: 1000 trades in <1s (meets performance requirement!)
- ✅ Trade statistics calculation with comprehensive metrics
- ✅ Trade statistics UI with 4-panel responsive grid layout
- ✅ Drawdown analysis: peak-to-trough detection, recovery tracking, multiple periods support
- ✅ Drawdown API endpoint: GET /api/drawdown/{backtest_id} fully functional
- ✅ Drawdown UI: comprehensive display with max drawdown card, ongoing drawdown alert, top 5 drawdown periods
- ✅ Currency formatting with proper thousands separators
- ✅ Decimal precision fix for equity curve calculations
- ✅ All code quality checks passing (ruff format + ruff check)

### 📁 Files Created (Sessions: 2025-11-22 & 2025-11-23)
1. `alembic/versions/34f3c8e99016_add_trades_table_for_individual_trade_.py` - Migration
2. `src/db/models/trade.py` - SQLAlchemy Trade model (147 lines)
3. `src/models/trade.py` - Pydantic models + calculate_trade_metrics() (231 lines)
4. `tests/unit/models/test_trade_models.py` - Unit tests (296 lines, 12 tests)
5. `tests/integration/db/test_trade_persistence.py` - Integration tests (227 lines, 4 tests)
6. `src/services/trade_analytics.py` - Equity curve + trade statistics (323 lines)
7. `tests/unit/services/test_trade_analytics.py` - Unit tests (708 lines, 11 tests)
8. `tests/integration/api/test_trades_api.py` - API integration tests (607 lines, 10 tests)
9. `tests/integration/api/conftest.py` - API test fixtures

### 🔧 Files Modified (2025-11-23 Sessions - US3 & US4)

**User Story 3 (Trade Statistics)**:
- `src/services/trade_analytics.py` - Added calculate_trade_statistics() + streak detection helpers + ROUND_HALF_UP for decimal precision
- `src/api/rest/trades.py` - Added GET /api/statistics/{id} endpoint
- `tests/unit/services/test_trade_analytics.py` - Added 6 unit tests for trade statistics
- `tests/integration/api/test_trades_api.py` - Added 2 integration tests for statistics endpoint
- `templates/backtests/detail.html` - Added trade statistics section with loading states
- `static/js/charts.js` - Added initTradeStatistics() function with 4-panel grid layout + formatCurrency() helper

**User Story 4 (Drawdown Analysis)**:
- `src/services/trade_analytics.py` - Added calculate_drawdowns() function (127 lines) with peak-to-trough detection
- `src/api/rest/trades.py` - Added GET /api/drawdown/{backtest_id} endpoint
- `tests/unit/services/test_trade_analytics.py` - Added 4 unit tests for drawdown calculation
- `tests/integration/api/test_trades_api.py` - Added 2 integration tests for drawdown endpoint
- `templates/backtests/detail.html` - Added Drawdown Analysis section with loading/error states
- `static/js/charts.js` - Added initDrawdownMetrics() function with formatTimestamp() helper, max drawdown card, ongoing drawdown display, top 5 drawdown periods list

### 🐛 Bug Fixes (2025-11-23)
- Fixed Pydantic decimal precision validation error in equity curve generation
- Added ROUND_HALF_UP rounding mode to quantize() operations
- Added currency formatter with Intl.NumberFormat for proper dollar display
- Now displays: -$10,496.02 instead of -$10496.02000000

### 🎯 Next Steps
1. ~~Implement User Story 4 - Calculate Drawdown from Equity Curve (P2)~~ ✅ DONE
2. Implement User Story 5 - View Trades in Backtest Details UI (P2) ← NEXT
3. Implement User Story 6 - Export Trade History (P3)
4. Implement User Story 7 - Filter and Query Trades (P3)

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

## Path Conventions

Project uses single-project structure:
- Source code: `src/` at repository root
- Tests: `tests/` at repository root
- Templates: `src/templates/` for Jinja2 HTML
- Migrations: `src/db/migrations/versions/`

---

## Phase 1: Setup (Shared Infrastructure) ✅ COMPLETE

**Purpose**: Project initialization and database schema setup

- [X] T001 Create database migration for trades table using Alembic
- [X] T002 Add trades relationship to BacktestRun model in src/db/models/backtest.py
- [ ] T003 [P] Create test fixtures for trades in tests/fixtures/trade_fixtures.py (DEFERRED - not needed yet)

---

## Phase 2: Foundational (Blocking Prerequisites) ✅ COMPLETE

**Purpose**: Core data models and infrastructure that ALL user stories depend on

**✅ COMPLETE**: Foundation is ready - user story implementation can now begin!

- [X] T004 [P] Create Trade SQLAlchemy model in src/db/models/trade.py
- [X] T005 [P] Create Pydantic models (TradeBase, TradeCreate, Trade) in src/models/trade.py
- [X] T006 [P] Create calculate_trade_metrics() function in src/models/trade.py
- [X] T007 Create TradeListResponse Pydantic model in src/models/trade.py
- [X] T008 [P] Create EquityCurvePoint Pydantic model in src/models/trade.py
- [X] T009 [P] Create EquityCurveResponse Pydantic model in src/models/trade.py
- [X] T010 [P] Create DrawdownPeriod Pydantic model in src/models/trade.py
- [X] T011 [P] Create DrawdownMetrics Pydantic model in src/models/trade.py
- [X] T012 [P] Create TradeStatistics Pydantic model in src/models/trade.py
- [X] T013 Run database migration to create trades table
- [X] T014 Verify database schema with manual inspection

**Checkpoint**: ✅ Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Capture Individual Trade Executions (Priority: P1) 🎯 MVP

**Goal**: Automatically capture and persist every individual trade execution from Nautilus Trader backtests, enabling trade-by-trade analysis

**Independent Test**: Run a backtest that generates 10 trades, verify database contains all 10 trade records with entry/exit details, profit/loss, and timestamps

### Tests for User Story 1 ✅ COMPLETE

> **✅ TDD COMPLETE**: All tests written first and passing (12 unit + 4 integration = 16 tests)

- [X] T015 [P] [US1] Unit test for TradeCreate validation in tests/unit/models/test_trade_models.py
- [X] T016 [P] [US1] Unit test for calculate_profit_long_position in tests/unit/models/test_trade_models.py
- [X] T017 [P] [US1] Unit test for calculate_profit_short_position in tests/unit/models/test_trade_models.py
- [X] T018 [P] [US1] Unit test for Trade model from_attributes in tests/unit/models/test_trade_models.py
- [X] T019 [US1] Integration test for save_trades_from_fills() in tests/integration/db/test_trade_persistence.py
- [X] T020 [US1] Integration test for bulk trade insertion performance (500+ trades) in tests/integration/db/test_trade_persistence.py

**Test Results**: 16/16 passing ✅ | Bulk insert: 500 trades in <1s (5x faster than requirement!)

### Implementation for User Story 1 ✅ COMPLETE

- [X] T021 [US1] Implement save_trades_from_fills() service in src/services/backtest_persistence.py
- [X] T022 [US1] Add FillReport to Trade conversion logic in src/services/backtest_persistence.py
- [X] T023 [US1] Integrate trade capture into backtest execution workflow (call generate_fills_report())
- [X] T024 [US1] Add error handling for missing commission/fee data
- [X] T025 [US1] Add logging for trade capture operations using structlog

**Checkpoint**: ✅ Backtests automatically save all trades to database with calculated profit/loss

---

## Phase 4: User Story 2 - Generate Equity Curve from Trades (Priority: P1)

**Goal**: Show account balance evolution over time based on actual trade executions, enabling visual performance analysis

**Independent Test**: Run a backtest with 5+ trades, generate equity curve, verify it shows cumulative balance changes at each trade exit starting from initial capital

### Tests for User Story 2 ✅ COMPLETE

> **✅ TDD COMPLETE**: All tests written first and passing (5 unit + 3 integration = 8 tests)

- [X] T026 [P] [US2] Unit test for generate_equity_curve() with empty trades in tests/unit/services/test_trade_analytics.py
- [X] T027 [P] [US2] Unit test for generate_equity_curve() with mixed wins/losses in tests/unit/services/test_trade_analytics.py
- [X] T028 [P] [US2] Unit test for equity curve chronological ordering in tests/unit/services/test_trade_analytics.py
- [X] T029 [US2] Integration test for GET /api/equity-curve/{id} endpoint in tests/integration/api/test_trades_api.py
- [X] T030 [US2] Integration test for equity curve with 1000 trades (performance) in tests/integration/api/test_trades_api.py

**Test Results**: 8/8 passing ✅ | Performance: 1000 trades in <1s (meets requirement!)

### Implementation for User Story 2 ✅ COMPLETE

- [X] T031 [US2] Create trade_analytics.py service file in src/services/
- [X] T032 [US2] Implement generate_equity_curve() function in src/services/trade_analytics.py
- [X] T033 [US2] Add equity curve endpoint to trades router in src/api/rest/trades.py
- [X] T034 [US2] Implement GET /api/equity-curve/{backtest_id} endpoint in src/api/rest/trades.py
- [X] T035 [US2] Register trades router in src/api/web.py (already registered)
- [X] T036 [US2] Add equity curve chart to backtest_detail.html template (updated template with data-backtest-id)
- [X] T037 [US2] Add JavaScript integration for equity curve visualization (updated charts.js to use new API endpoint)

**Checkpoint**: ✅ COMPLETE - Equity curve API and UI integration functional. Chart visualizes account balance evolution based on individual trades.

---

## Phase 5: User Story 3 - Calculate Trade-Based Performance Metrics (Priority: P2)

**Goal**: Calculate detailed trade statistics (win rate, average win/loss, streaks, etc.) to understand trading pattern quality

**Independent Test**: Run a backtest with mixed results, verify calculated metrics match manual calculations (win rate, profit factor, consecutive streaks)

### Tests for User Story 3 ✅ COMPLETE

- [X] T038 [P] [US3] Unit test for calculate_trade_statistics() with no trades in tests/unit/services/test_trade_analytics.py
- [X] T039 [P] [US3] Unit test for win rate calculation in tests/unit/services/test_trade_analytics.py
- [X] T040 [P] [US3] Unit test for profit factor calculation in tests/unit/services/test_trade_analytics.py
- [X] T041 [P] [US3] Unit test for consecutive win/loss streak detection in tests/unit/services/test_trade_analytics.py
- [X] T042 [P] [US3] Unit test for holding period calculations in tests/unit/services/test_trade_analytics.py
- [X] T043 [US3] Integration test for GET /api/statistics/{id} endpoint in tests/integration/api/test_trades_api.py

### Implementation for User Story 3 ✅ COMPLETE

- [X] T044 [US3] Implement calculate_trade_statistics() function in src/services/trade_analytics.py
- [X] T045 [US3] Implement consecutive streak detection algorithm in src/services/trade_analytics.py
- [X] T046 [US3] Implement GET /api/statistics/{id} endpoint in src/api/rest/trades.py
- [X] T047 [US3] Add trade statistics section to backtest_detail.html template and JavaScript initialization

**Checkpoint**: ✅ COMPLETE - Backtest details page displays comprehensive trade statistics below equity curve with 4-panel grid layout showing trade counts, performance metrics, profit/loss details, and streaks/holding periods

---

## Phase 6: User Story 4 - Calculate Drawdown from Equity Curve (Priority: P2) ✅ COMPLETE

**Goal**: Calculate maximum drawdown, drawdown duration, and recovery periods to understand risk profile

**Independent Test**: Create an equity curve with a known drawdown pattern, verify system correctly identifies peak, trough, drawdown percentage, and recovery time

### Tests for User Story 4 ✅ COMPLETE

- [X] T048 [P] [US4] Unit test for calculate_drawdowns() with no drawdown in tests/unit/services/test_trade_analytics.py
- [X] T049 [P] [US4] Unit test for single drawdown period detection in tests/unit/services/test_trade_analytics.py
- [X] T050 [P] [US4] Unit test for multiple drawdown periods in tests/unit/services/test_trade_analytics.py
- [X] T051 [P] [US4] Unit test for ongoing drawdown (not recovered) in tests/unit/services/test_trade_analytics.py
- [X] T052 [US4] Integration test for GET /api/drawdown/{id} endpoint in tests/integration/api/test_trades_api.py (2 tests added)

**Test Results**: 6/6 passing ✅ (4 unit + 2 integration = 6 tests)

### Implementation for User Story 4 ✅ COMPLETE

- [X] T053 [US4] Implement calculate_drawdowns() function in src/services/trade_analytics.py (127 lines)
- [X] T054 [US4] Implement peak-to-trough detection algorithm (integrated in calculate_drawdowns())
- [X] T055 [US4] Implement recovery timestamp calculation (integrated in calculate_drawdowns())
- [X] T056 [US4] Implement GET /api/drawdown/{backtest_id} endpoint in src/api/rest/trades.py
- [x] T057 [US4] Add drawdown metrics section to backtest_detail.html template - Added Drawdown Analysis section with loading states, error handling, and comprehensive display

**Checkpoint**: ✅ COMPLETE - Drawdown API endpoint functional. Returns max drawdown, top drawdown periods, current drawdown status. All tests passing (25/25 total in test suite).

---

## Phase 7: User Story 5 - View Trades in Backtest Details UI (Priority: P2)

**Goal**: Display trades table in backtest details page with pagination, sorting, and visual indicators

**Independent Test**: Run a backtest, navigate to details page, verify trades section displays all trades with pagination controls

### Tests for User Story 5

- [ ] T058 [P] [US5] Integration test for GET /backtests/{id}/trades with pagination in tests/integration/test_trades_api.py
- [ ] T059 [P] [US5] Integration test for trades sorting by entry_timestamp in tests/integration/test_trades_api.py
- [ ] T060 [P] [US5] Integration test for trades sorting by profit_loss in tests/integration/test_trades_api.py
- [ ] T061 [P] [US5] Integration test for page size options (20/50/100) in tests/integration/test_trades_api.py
- [ ] T062 [US5] Integration test for handling zero trades in tests/integration/test_trades_api.py

### Implementation for User Story 5

- [ ] T063 [US5] Implement GET /backtests/{id}/trades endpoint with pagination in src/api/routers/trades.py
- [ ] T064 [US5] Add server-side sorting by entry_timestamp, exit_timestamp, profit_loss in src/api/routers/trades.py
- [ ] T065 [US5] Add query validation for sort_by and sort_order parameters in src/api/routers/trades.py
- [ ] T066 [US5] Create trades table partial template in src/templates/partials/trades_table.html
- [ ] T067 [US5] Add HTMX pagination controls to trades table partial
- [ ] T068 [US5] Add color coding for winning/losing trades (green/red) in trades table
- [ ] T069 [US5] Add trades section to backtest_detail.html with HTMX integration
- [ ] T070 [US5] Add page size selector (20/50/100 trades per page) to UI
- [ ] T071 [US5] Add sortable column headers with HTMX triggers

**Checkpoint**: Backtest details page should display paginated trades table with sorting and color-coded P&L

---

## Phase 8: User Story 6 - Export Trade History (Priority: P3)

**Goal**: Export complete trade history to CSV or JSON for external analysis

**Independent Test**: Run a backtest, export trades to CSV, verify all trade fields are present and correctly formatted

### Tests for User Story 6

- [ ] T072 [P] [US6] Integration test for GET /backtests/{id}/export?format=csv in tests/integration/test_trades_api.py
- [ ] T073 [P] [US6] Integration test for GET /backtests/{id}/export?format=json in tests/integration/test_trades_api.py
- [ ] T074 [P] [US6] Integration test for CSV decimal precision preservation in tests/integration/test_trades_api.py
- [ ] T075 [US6] Integration test for special character handling in CSV in tests/integration/test_trades_api.py

### Implementation for User Story 6

- [ ] T076 [US6] Implement CSV export logic in src/services/trade_analytics.py
- [ ] T077 [US6] Implement JSON export logic in src/services/trade_analytics.py
- [ ] T078 [US6] Implement GET /backtests/{id}/export endpoint in src/api/routers/trades.py
- [ ] T079 [US6] Add CSV content-type and attachment headers to response
- [ ] T080 [US6] Add export button to backtest details page UI
- [ ] T081 [US6] Add format selector (CSV/JSON) to export UI

**Checkpoint**: Users can export trade history to CSV or JSON from backtest details page

---

## Phase 9: User Story 7 - Filter and Query Trades (Priority: P3)

**Goal**: Filter trades by symbol, date range, profit/loss threshold, and holding period

**Independent Test**: Run a backtest with varied trades, filter by win/loss or date range, verify only matching trades are returned

### Tests for User Story 7

- [ ] T082 [P] [US7] Integration test for filtering by instrument_id in tests/integration/test_trades_api.py
- [ ] T083 [P] [US7] Integration test for filtering by min_profit threshold in tests/integration/test_trades_api.py
- [ ] T084 [P] [US7] Integration test for filtering by max_profit threshold in tests/integration/test_trades_api.py
- [ ] T085 [P] [US7] Integration test for multiple filters (AND logic) in tests/integration/test_trades_api.py
- [ ] T086 [US7] Integration test for filter performance with 1000+ trades in tests/integration/test_trades_api.py

### Implementation for User Story 7

- [ ] T087 [US7] Add instrument_id filter parameter to GET /backtests/{id}/trades in src/api/routers/trades.py
- [ ] T088 [US7] Add min_profit and max_profit filter parameters to GET /backtests/{id}/trades in src/api/routers/trades.py
- [ ] T089 [US7] Implement SQLAlchemy query filters for trade filtering in src/api/routers/trades.py
- [ ] T090 [US7] Add filter UI controls to trades section in backtest_detail.html
- [ ] T091 [US7] Add HTMX triggers for filter changes to reload trades table
- [ ] T092 [US7] Add "Clear Filters" button to reset query parameters

**Checkpoint**: Users can filter trades by multiple criteria and see filtered results instantly via HTMX

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T093 [P] Add comprehensive docstrings to all public functions in src/services/trade_analytics.py
- [ ] T094 [P] Add comprehensive docstrings to all API endpoints in src/api/routers/trades.py
- [ ] T095 [P] Run code formatting with ruff format on all new files
- [ ] T096 [P] Run linting with ruff check and fix issues
- [ ] T097 [P] Run type checking with mypy on src/models/trade.py
- [ ] T098 [P] Run type checking with mypy on src/services/trade_analytics.py
- [ ] T099 Run full test suite with coverage report (target: 80%+ on critical paths)
- [ ] T100 Validate against quickstart.md end-to-end workflow
- [ ] T101 [P] Add error handling for backtests with zero trades (display "No trades executed")
- [ ] T102 [P] Add error handling for invalid backtest_run_id (404 responses)
- [ ] T103 Performance test: Bulk insert 500 trades in <5 seconds
- [ ] T104 Performance test: Equity curve generation for 1000 trades in <1 second
- [ ] T105 Performance test: API pagination response in <300ms for 100 trades
- [ ] T106 Performance test: UI page load (first 20 trades + chart) in <2 seconds
- [ ] T107 [P] Update CLAUDE.md with new trade tracking patterns and tech stack additions
- [ ] T108 Create pull request with all changes for code review

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-9)**: All depend on Foundational phase completion
  - User stories can proceed in parallel (if staffed) after foundational work
  - Or sequentially in priority order (P1 → P2 → P3)
- **Polish (Phase 10)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - No dependencies on other stories
- **User Story 2 (P1)**: Can start after Foundational (Phase 2) - Depends on US1 for trade data source
- **User Story 3 (P2)**: Can start after Foundational (Phase 2) - Depends on US1 for trade data source
- **User Story 4 (P2)**: Can start after Foundational (Phase 2) - Depends on US2 for equity curve
- **User Story 5 (P2)**: Can start after Foundational (Phase 2) - Depends on US1 for trade data source
- **User Story 6 (P3)**: Can start after Foundational (Phase 2) - Depends on US1 for trade data source
- **User Story 7 (P3)**: Can start after Foundational (Phase 2) - Depends on US5 for base trades endpoint

### Within Each User Story

- Tests MUST be written and FAIL before implementation (TDD Red phase)
- Unit tests before integration tests
- Models before services
- Services before endpoints
- Core implementation before UI integration
- Story complete before moving to next priority

### Parallel Opportunities

#### Phase 1 (Setup)
- T001, T002, T003 can run in parallel (different files)

#### Phase 2 (Foundational)
- T004-T012 can run in parallel (different Pydantic models)
- T013-T014 must run sequentially (migration execution)

#### User Story 1 Tests (Phase 3)
- T015, T016, T017, T018 can run in parallel (independent unit tests)

#### User Story 2 Tests (Phase 4)
- T026, T027, T028 can run in parallel (independent unit tests)

#### User Story 3 Tests (Phase 5)
- T038, T039, T040, T041, T042 can run in parallel (independent unit tests)

#### User Story 4 Tests (Phase 6)
- T048, T049, T050, T051 can run in parallel (independent unit tests)

#### User Story 5 Tests (Phase 7)
- T058, T059, T060, T061 can run in parallel (independent API tests)

#### User Story 6 Tests (Phase 8)
- T072, T073, T074 can run in parallel (independent export tests)

#### User Story 7 Tests (Phase 9)
- T082, T083, T084, T085 can run in parallel (independent filter tests)

#### Polish (Phase 10)
- T093, T094, T095, T096, T097, T098, T101, T102, T107 can run in parallel (different files)

---

## Parallel Example: User Story 1

```bash
# Launch all unit tests for User Story 1 together (TDD Red phase):
Task: "Unit test for TradeCreate validation in tests/unit/test_trade_models.py"
Task: "Unit test for calculate_profit_long_position in tests/unit/test_trade_models.py"
Task: "Unit test for calculate_profit_short_position in tests/unit/test_trade_models.py"
Task: "Unit test for Trade model from_attributes in tests/unit/test_trade_models.py"

# After tests fail, implement in parallel:
# (Note: These aren't truly parallel since they're in same file, but can be done in rapid succession)
Task: "Create Trade SQLAlchemy model in src/db/models/trade.py"
Task: "Create Pydantic models in src/models/trade.py"
```

---

## Parallel Example: User Story 2

```bash
# Launch all unit tests for User Story 2 together:
Task: "Unit test for generate_equity_curve() with empty trades"
Task: "Unit test for generate_equity_curve() with mixed wins/losses"
Task: "Unit test for equity curve chronological ordering"
```

---

## Implementation Strategy

### MVP First (User Stories 1 & 2 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (Trade Capture)
4. Complete Phase 4: User Story 2 (Equity Curve)
5. **STOP and VALIDATE**: Test US1 & US2 independently, verify equity curve displays
6. Deploy/demo if ready - this is a functional MVP!

**MVP Scope**: At this point, traders can:
- ✅ Automatically capture all trades from backtests
- ✅ View equity curve showing account balance evolution
- ✅ See basic trade data in database

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → **Trade capture working**
3. Add User Story 2 → Test independently → **Equity curve visualization working** (MVP!)
4. Add User Story 3 → Test independently → **Trade statistics available**
5. Add User Story 4 → Test independently → **Drawdown analysis available**
6. Add User Story 5 → Test independently → **UI trades table working**
7. Add User Story 6 → Test independently → **Export functionality working**
8. Add User Story 7 → Test independently → **Filtering capability working**
9. Each story adds value without breaking previous stories

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - **Developer A**: User Story 1 (Trade Capture) - MUST complete first
   - After US1 complete:
     - **Developer A**: User Story 2 (Equity Curve)
     - **Developer B**: User Story 3 (Statistics)
     - **Developer C**: User Story 5 (UI Table)
   - After US2 complete:
     - **Developer D**: User Story 4 (Drawdown) - depends on equity curve
   - After US5 complete:
     - **Developer E**: User Story 7 (Filtering) - depends on base trades endpoint
   - **Developer F**: User Story 6 (Export) - can start after US1

3. Stories complete and integrate independently

---

## Task Summary

**Total Tasks**: 108 tasks across 10 phases

**Breakdown by Phase**:
- Phase 1 (Setup): 3 tasks
- Phase 2 (Foundational): 11 tasks
- Phase 3 (US1 - Trade Capture): 11 tasks (6 tests + 5 implementation)
- Phase 4 (US2 - Equity Curve): 12 tasks (5 tests + 7 implementation)
- Phase 5 (US3 - Statistics): 10 tasks (6 tests + 4 implementation)
- Phase 6 (US4 - Drawdown): 10 tasks (5 tests + 5 implementation)
- Phase 7 (US5 - UI Table): 14 tasks (5 tests + 9 implementation)
- Phase 8 (US6 - Export): 10 tasks (4 tests + 6 implementation)
- Phase 9 (US7 - Filtering): 11 tasks (5 tests + 6 implementation)
- Phase 10 (Polish): 16 tasks

**Parallel Opportunities**: 42 tasks marked [P] can run in parallel within their phase

**Independent Test Criteria**:
- **US1**: Run backtest, verify 10 trades saved to database with profit/loss
- **US2**: Run backtest with 5+ trades, verify equity curve displays balance evolution
- **US3**: Run backtest, verify statistics match manual calculations
- **US4**: Create equity curve, verify drawdown calculations are correct
- **US5**: Navigate to backtest page, verify trades table displays with pagination
- **US6**: Export trades to CSV, verify all fields present
- **US7**: Apply filters, verify only matching trades returned

**Suggested MVP Scope**: User Stories 1 & 2 (Phases 1-4)
- Delivers core value: trade capture + equity curve visualization
- Total MVP tasks: 37 tasks (Setup + Foundational + US1 + US2)
- Estimated MVP effort: ~12-15 hours (per plan.md)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label (US1, US2, etc.) maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Follow TDD: Write tests first, verify they fail, then implement
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All monetary calculations use Decimal type (never float)
- All timestamps stored in UTC with timezone information
- Server-side pagination for scalability with 1000+ trades
- HTMX for responsive UI updates without full page reloads

---

**Status**: ✅ COMPLETE - Ready for implementation via /implement command
