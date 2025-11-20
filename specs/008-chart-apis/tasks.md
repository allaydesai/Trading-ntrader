# Tasks: Chart APIs

**Input**: Design documents from `/specs/008-chart-apis/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi.yaml

**Tests**: Tests are required (TDD approach per project constitution - 80% minimum coverage)

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and REST API directory structure

- [x] T001 Create REST API directory structure in src/api/rest/__init__.py
- [x] T002 [P] Create chart models package with __init__.py in src/api/models/__init__.py (update exports)
- [x] T003 [P] Create test directory structure in tests/api/rest/__init__.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 Create error response models (ErrorDetail, ValidationErrorDetail) in src/api/models/chart_errors.py
- [x] T005 [P] Add DataCatalogService dependency provider in src/api/dependencies.py
- [x] T006 Register REST API routers in src/api/web.py
- [x] T007 [P] Create shared test fixtures for chart API tests in tests/api/rest/conftest.py

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Fetch OHLCV Time Series Data for Price Charts (Priority: P1)

**Goal**: Provide OHLCV candlestick data from Parquet files in TradingView-compatible JSON format

**Independent Test**: Request time series data for a known symbol and date range, verify response contains properly formatted OHLCV candles that TradingView can consume

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [x] T008 [P] [US1] Write contract tests for /api/timeseries endpoint in tests/api/rest/test_timeseries.py
- [x] T009 [P] [US1] Write tests for timeseries validation (invalid date range, missing symbol) in tests/api/rest/test_timeseries.py
- [x] T010 [P] [US1] Write tests for 404 error with CLI suggestion in tests/api/rest/test_timeseries.py

### Implementation for User Story 1

- [x] T011 [P] [US1] Create Candle and TimeseriesResponse Pydantic models in src/api/models/chart_timeseries.py
- [x] T012 [US1] Implement GET /api/timeseries endpoint in src/api/rest/timeseries.py
- [x] T013 [US1] Add timeframe enum and mapping (1_MIN to 1-MINUTE-LAST) in src/api/rest/timeseries.py
- [x] T014 [US1] Add symbol-to-instrument_id conversion (AAPL to AAPL.NASDAQ) in src/api/rest/timeseries.py
- [x] T015 [US1] Add date range validation and error handling in src/api/rest/timeseries.py
- [x] T016 [US1] Add 404 response with CLI fetch suggestion in src/api/rest/timeseries.py

**Checkpoint**: User Story 1 should be fully functional - can fetch and display candlestick charts

---

## Phase 4: User Story 2 - Fetch Trade Markers for Chart Overlay (Priority: P1)

**Goal**: Provide trade entry/exit points with prices and P&L for chart markers

**Independent Test**: Request trade data for a known backtest run ID and verify response contains trade markers with timestamps, sides (buy/sell), prices, quantities, and P&L values

### Tests for User Story 2

- [x] T017 [P] [US2] Write contract tests for GET /api/trades/{run_id} endpoint in tests/api/rest/test_trades.py
- [x] T018 [P] [US2] Write tests for empty trades array (no trades in backtest) in tests/api/rest/test_trades.py
- [x] T019 [P] [US2] Write tests for 404 error when run_id not found in tests/api/rest/test_trades.py

### Implementation for User Story 2

- [x] T020 [P] [US2] Create TradeMarker and TradesResponse Pydantic models in src/api/models/chart_trades.py
- [x] T021 [US2] Implement GET /api/trades/{run_id} endpoint in src/api/rest/trades.py
- [x] T022 [US2] Extract trades from backtest config_snapshot in src/api/rest/trades.py
- [x] T023 [US2] Add trade sorting by timestamp and side validation in src/api/rest/trades.py

**Checkpoint**: User Story 2 should be fully functional - can overlay trade markers on charts

---

## Phase 5: User Story 3 - Fetch Equity Curve and Drawdown Data (Priority: P1)

**Goal**: Provide equity curve values and drawdown percentages for portfolio performance charts

**Independent Test**: Request equity data for a known backtest run ID and verify response contains time series of portfolio values and corresponding drawdown percentages

### Tests for User Story 3

- [x] T024 [P] [US3] Write contract tests for GET /api/equity/{run_id} endpoint in tests/api/rest/test_equity.py
- [x] T025 [P] [US3] Write tests for drawdown calculation accuracy in tests/api/rest/test_equity.py
- [x] T026 [P] [US3] Write tests for 404 error when run_id not found in tests/api/rest/test_equity.py

### Implementation for User Story 3

- [x] T027 [P] [US3] Create EquityPoint, DrawdownPoint, and EquityResponse Pydantic models in src/api/models/chart_equity.py
- [x] T028 [US3] Implement GET /api/equity/{run_id} endpoint in src/api/rest/equity.py
- [x] T029 [US3] Implement drawdown calculation from equity curve in src/api/rest/equity.py
- [x] T030 [US3] Add equity and drawdown array length validation in src/api/rest/equity.py

**Checkpoint**: User Story 3 should be fully functional - can display equity curve and drawdown charts

---

## Phase 6: User Story 4 - Fetch Indicator Series for Chart Overlay (Priority: P2)

**Goal**: Provide indicator values (SMA lines) for chart overlays showing strategy signals

**Independent Test**: Request indicator data for a known backtest run ID and verify response contains named indicator series with time-value pairs that align with OHLCV timestamps

### Tests for User Story 4

- [x] T031 [P] [US4] Write contract tests for GET /api/indicators/{run_id} endpoint in tests/api/rest/test_indicators.py
- [x] T032 [P] [US4] Write tests for empty indicators object (no indicators in strategy) in tests/api/rest/test_indicators.py
- [x] T033 [P] [US4] Write tests for 404 error when run_id not found in tests/api/rest/test_indicators.py

### Implementation for User Story 4

- [x] T034 [P] [US4] Create IndicatorPoint and IndicatorsResponse Pydantic models in src/api/models/chart_indicators.py
- [x] T035 [US4] Implement GET /api/indicators/{run_id} endpoint in src/api/rest/indicators.py
- [x] T036 [US4] Extract indicator series from backtest config_snapshot in src/api/rest/indicators.py
- [x] T037 [US4] Add indicator name validation and sorting by timestamp in src/api/rest/indicators.py

**Checkpoint**: User Story 4 should be fully functional - can overlay indicator lines on charts

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Quality assurance, performance validation, and documentation

- [x] T038 [P] Run ruff format and ruff check on all chart API files
- [x] T039 [P] Run mypy type checking on all chart API files
- [x] T040 Verify all tests pass with 80% minimum coverage
- [ ] T041 [P] Performance validation: Time series <500ms for 100k candles
- [ ] T042 [P] Performance validation: Trades/Equity <200ms, Indicators <300ms
- [ ] T043 Run quickstart.md validation with curl commands

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-6)**: All depend on Foundational phase completion
  - User stories can then proceed in parallel (if staffed)
  - Or sequentially in priority order (P1 → P2)
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational - No dependencies on other stories
- **User Story 2 (P1)**: Can start after Foundational - No dependencies on other stories
- **User Story 3 (P1)**: Can start after Foundational - No dependencies on other stories
- **User Story 4 (P2)**: Can start after Foundational - No dependencies on other stories

### Within Each User Story

- Tests MUST be written and FAIL before implementation
- Models before endpoints
- Core implementation before validation
- Story complete before moving to next priority

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- All Foundational tasks marked [P] can run in parallel (within Phase 2)
- Once Foundational phase completes, all user stories can start in parallel
- All tests for a user story marked [P] can run in parallel
- Models within a story marked [P] can run in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together:
Task: "Write contract tests for /api/timeseries endpoint in tests/api/rest/test_timeseries.py"
Task: "Write tests for timeseries validation in tests/api/rest/test_timeseries.py"
Task: "Write tests for 404 error with CLI suggestion in tests/api/rest/test_timeseries.py"

# Then launch model creation:
Task: "Create Candle and TimeseriesResponse Pydantic models in src/api/models/chart_timeseries.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1-3)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1 (OHLCV Time Series)
4. Complete Phase 4: User Story 2 (Trade Markers)
5. Complete Phase 5: User Story 3 (Equity Curve)
6. **STOP and VALIDATE**: Test all three P1 stories independently
7. Deploy/demo if ready

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → Core chart data available
3. Add User Story 2 → Test independently → Trade visualization enabled
4. Add User Story 3 → Test independently → Portfolio performance visible
5. Add User Story 4 → Test independently → Strategy signals shown
6. Polish → Performance validated → Production ready

### Parallel Team Strategy

With multiple developers:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: User Story 1 (Timeseries)
   - Developer B: User Story 2 (Trades)
   - Developer C: User Story 3 (Equity)
3. All three P1 stories complete in parallel
4. Any developer: User Story 4 (Indicators)
5. Team: Polish phase

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Verify tests fail before implementing (TDD Red-Green-Refactor)
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All four endpoints reuse existing DataCatalogService and BacktestQueryService
