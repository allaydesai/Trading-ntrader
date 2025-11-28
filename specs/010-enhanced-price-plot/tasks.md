# Tasks: Enhanced Price Plot with Trade Markers and Indicators

**Feature**: 010-enhanced-price-plot
**Branch**: `010-enhanced-price-plot`
**Generated**: 2025-01-27
**Input**: Design documents from `/specs/010-enhanced-price-plot/`

**Scope**: User Stories 1-2 (P1 priorities only)
**Organization**: Tasks are grouped by user story to enable independent implementation and testing

## Format: `- [ ] [ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- All file paths are absolute from repository root

## Path Conventions

This is a **Web Application** with:
- Backend: `src/api/` (FastAPI)
- Frontend: `src/templates/` (Jinja2), `src/static/js/` (JavaScript)
- Tests: `tests/integration/ui/` (Playwright)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and structure for frontend chart enhancement

**Tasks**:

- [ ] T001 Create static assets directory structure at src/static/js/ (if not exists)
- [ ] T002 Verify TradingView Lightweight Charts library is accessible (already included via CDN per plan.md)
- [ ] T003 [P] Verify existing API endpoints are functional: /api/timeseries, /api/trades/{run_id}, /api/indicators/{run_id} (from specs/008-chart-apis)

**Checkpoint**: Directory structure ready, dependencies verified, APIs confirmed working

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No foundational tasks required - all backend APIs already exist from specs/008-chart-apis

**Note**: Backend infrastructure (FastAPI, PostgreSQL, SQLAlchemy) already implemented. This feature is purely frontend JavaScript with existing APIs.

**Checkpoint**: Foundation ready - user story implementation can begin

---

## Phase 3: User Story 1 - View Trade Entry/Exit Markers on Price Chart (Priority: P1) 🎯 MVP

**Goal**: Display visual markers (green upward arrows for buy, red downward arrows for sell) on the price chart at trade execution points with tooltips showing trade details

**Independent Test**: Load a backtest detail page with known trades, verify markers appear at correct timestamps and prices, hover to confirm tooltip displays trade type, price, quantity, and P&L

**Acceptance Criteria**:
- Buy entries shown as upward-pointing green triangles at entry price/timestamp
- Sell exits shown as downward-pointing red triangles at exit price/timestamp
- Tooltips display: type, price, quantity, timestamp, P&L (for exits)
- Markers positioned to avoid overlap for overlapping trades
- Markers remain anchored during zoom/pan operations

### Tests for User Story 1

> **TDD REQUIREMENT**: Write these tests FIRST, ensure they FAIL before implementation

- [ ] T004 [P] [US1] Create Playwright test file tests/integration/ui/test_enhanced_chart.py with test setup
- [ ] T005 [P] [US1] Write test_chart_renders_with_trade_markers() in tests/integration/ui/test_enhanced_chart.py to verify markers appear on chart
- [ ] T006 [P] [US1] Write test_buy_markers_display_correctly() in tests/integration/ui/test_enhanced_chart.py to verify green upward arrows for buy trades
- [ ] T007 [P] [US1] Write test_sell_markers_display_correctly() in tests/integration/ui/test_enhanced_chart.py to verify red downward arrows for sell trades
- [ ] T008 [P] [US1] Write test_marker_tooltips_show_trade_details() in tests/integration/ui/test_enhanced_chart.py to verify tooltip content (type, price, qty, P&L)
- [ ] T009 [P] [US1] Write test_markers_persist_during_zoom_pan() in tests/integration/ui/test_enhanced_chart.py to verify marker positions remain accurate during zoom/pan

### Implementation for User Story 1

- [ ] T010 [US1] Create chart-enhanced.js module skeleton at src/static/js/chart-enhanced.js with initializeEnhancedChart() function
- [ ] T011 [US1] Implement createChartInstance() function in src/static/js/chart-enhanced.js to create TradingView chart with dark theme configuration
- [ ] T012 [US1] Implement fetchOHLCVData() function in src/static/js/chart-enhanced.js to fetch candlestick data from /api/timeseries
- [ ] T013 [US1] Implement fetchTradeMarkers() function in src/static/js/chart-enhanced.js to fetch trades from /api/trades/{run_id}
- [ ] T014 [US1] Implement isoToUnix() helper function in src/static/js/chart-enhanced.js to convert ISO timestamps to Unix seconds
- [ ] T015 [US1] Implement createTradeMarker() function in src/static/js/chart-enhanced.js to transform trade data into TradingView marker format with position/color/shape logic
- [ ] T016 [US1] Implement formatTradeTooltip() function in src/static/js/chart-enhanced.js to generate tooltip text with trade details
- [ ] T017 [US1] Integrate marker rendering in initializeEnhancedChart() by calling candlestickSeries.setMarkers() after fetching trades
- [ ] T018 [US1] Add error handling and displayError() function in src/static/js/chart-enhanced.js for API failures and validation errors
- [ ] T019 [US1] Implement marker clustering logic (clusterMarkers() function) in src/static/js/chart-enhanced.js for backtests with 1000+ trades
- [ ] T020 [US1] Update backtest detail template at src/templates/backtests/detail.html to add chart container div with id="chart-container"
- [ ] T021 [US1] Add script tags to src/templates/backtests/detail.html to load chart-enhanced.js and initialize chart with backtest metadata (runId, symbol, dates)
- [ ] T022 [US1] Add responsive chart sizing logic in src/static/js/chart-enhanced.js to handle window resize events

**Checkpoint**: User Story 1 complete - chart displays OHLCV data with trade markers, tooltips, and zoom/pan support

---

## Phase 4: User Story 2 - Overlay Strategy Indicators on Price Chart (Priority: P1)

**Goal**: Display technical indicators (SMA, Bollinger Bands, RSI) used by the strategy overlaid on the price chart, enabling users to understand what signals drove trading decisions

**Independent Test**: Load a backtest with a known strategy (e.g., SMA Crossover), verify indicator lines appear with correct values, colors, and labels. Confirm RSI appears in separate pane below price chart.

**Acceptance Criteria**:
- SMA indicators rendered as solid lines with distinct colors and labels
- Bollinger Bands rendered as 3 lines (upper/middle/lower) with dashed styling for bands
- RSI indicator rendered in separate pane with overbought (70) and oversold (30) threshold lines
- Indicator tooltips show indicator name and value on hover
- Chart legend identifies each indicator by color and name
- Visibility toggle controls for each individual indicator
- Chart updates immediately when toggles clicked

### Tests for User Story 2

> **TDD REQUIREMENT**: Write these tests FIRST, ensure they FAIL before implementation

- [ ] T023 [P] [US2] Write test_sma_indicators_display() in tests/integration/ui/test_enhanced_chart.py to verify SMA lines appear with correct colors for SMA Crossover strategy
- [ ] T024 [P] [US2] Write test_bollinger_bands_display() in tests/integration/ui/test_enhanced_chart.py to verify 3 Bollinger Band lines with correct styling for Bollinger Reversal strategy
- [ ] T025 [P] [US2] Write test_rsi_indicator_separate_pane() in tests/integration/ui/test_enhanced_chart.py to verify RSI appears in separate pane with threshold lines
- [ ] T026 [P] [US2] Write test_indicator_tooltips() in tests/integration/ui/test_enhanced_chart.py to verify tooltip shows indicator name and value on hover
- [ ] T027 [P] [US2] Write test_indicator_legend_displays() in tests/integration/ui/test_enhanced_chart.py to verify legend identifies indicators by color and name
- [ ] T028 [P] [US2] Write test_indicator_visibility_toggles() in tests/integration/ui/test_enhanced_chart.py to verify toggling indicator visibility updates chart immediately

### Implementation for User Story 2

- [ ] T029 [US2] Implement fetchIndicators() function in src/static/js/chart-enhanced.js to fetch indicator data from /api/indicators/{run_id}
- [ ] T030 [US2] Implement getIndicatorConfig() function in src/static/js/chart-enhanced.js with strategy-specific indicator configurations (SMA Crossover, Bollinger Reversal, RSI Mean Reversion, SMA Momentum)
- [ ] T031 [US2] Implement transformIndicators() function in src/static/js/chart-enhanced.js to transform API response to TradingView format with display properties
- [ ] T032 [US2] Implement renderIndicators() function in src/static/js/chart-enhanced.js to add line series to main chart pane for SMA and Bollinger indicators
- [ ] T033 [US2] Implement createRSIPane() function in src/static/js/chart-enhanced.js to create separate chart instance for RSI indicator with threshold lines
- [ ] T034 [US2] Integrate indicator rendering in initializeEnhancedChart() by calling renderIndicators() after chart initialization
- [ ] T035 [US2] Add indicator visibility toggle controls to src/templates/backtests/detail.html in indicator-controls div
- [ ] T036 [US2] Implement toggleIndicatorVisibility() function in src/static/js/chart-enhanced.js to handle checkbox change events and update series visibility via applyOptions()
- [ ] T037 [US2] Add dynamic toggle control generation in src/static/js/chart-enhanced.js to create checkboxes based on strategy indicators loaded
- [ ] T038 [US2] Handle missing indicator data gracefully by rendering available data with gaps and explanatory tooltips in src/static/js/chart-enhanced.js
- [ ] T039 [US2] Display "No indicators configured" message in src/static/js/chart-enhanced.js when strategy uses no indicators

**Checkpoint**: User Story 2 complete - chart displays indicators with legend, tooltips, and visibility controls

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements affecting both user stories and validation

- [ ] T040 [P] Add comprehensive JSDoc comments to all functions in src/static/js/chart-enhanced.js
- [ ] T041 [P] Implement data downsampling optimization in src/static/js/chart-enhanced.js for zoomed-out views with 100k+ bars
- [ ] T042 [P] Implement progressive data loading in src/static/js/chart-enhanced.js using subscribeVisibleLogicalRangeChange() callback
- [ ] T043 [P] Add client-side data caching (Map) in src/static/js/chart-enhanced.js to avoid redundant API calls
- [ ] T044 Run manual validation against quickstart.md scenarios to verify all patterns work as documented
- [ ] T045 Code cleanup and refactoring: ensure chart-enhanced.js functions are <50 lines and file is <500 lines total
- [ ] T046 Performance testing: verify chart loads <1s for 100k bars, markers load <500ms for 1000 trades, toggles respond <100ms
- [ ] T047 Browser compatibility testing: verify chart works in Chrome 90+, Firefox 88+, Safari 14+

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: No tasks - backend APIs already exist
- **User Story 1 (Phase 3)**: Can start immediately after Setup
- **User Story 2 (Phase 4)**: Can start immediately after Setup (independent of User Story 1)
- **Polish (Phase 5)**: Depends on both User Stories 1 and 2 completion

### User Story Dependencies

- **User Story 1**: Independent - implements trade marker rendering
- **User Story 2**: Independent - implements indicator overlays
- **No cross-story dependencies**: Each story can be completed and tested independently

### Within Each User Story

**Test-Driven Development (TDD) Flow**:

1. **RED**: Write all tests for the story (T004-T009 for US1, T023-T028 for US2)
2. **Verify FAIL**: Run tests and confirm they fail (no implementation yet)
3. **GREEN**: Implement features one test at a time until all tests pass
4. **REFACTOR**: Clean up code while keeping tests green
5. **Story Complete**: All tests passing, story independently functional

**Implementation Order Within Story**:

- Tests BEFORE implementation (TDD)
- Core module functions (fetch, transform) before integration
- Chart initialization before rendering features
- Template updates after JavaScript implementation
- Error handling after happy path

### Parallel Opportunities

**Within User Story 1**:
```bash
# All tests can be written in parallel:
T005 (buy markers test), T006 (sell markers test), T007 (tooltips test), T008 (zoom/pan test)

# These implementation tasks can run in parallel:
T014 (isoToUnix helper), T016 (formatTradeTooltip)
```

**Within User Story 2**:
```bash
# All tests can be written in parallel:
T023 (SMA test), T024 (Bollinger test), T025 (RSI test), T026 (tooltips test), T027 (legend test), T028 (toggles test)

# These implementation tasks can run in parallel:
T038 (missing data handling), T039 (no indicators message)
```

**Across User Stories** (if team has multiple developers):
```bash
# After Phase 1 (Setup) completes:
Developer A: Complete User Story 1 (Phase 3)
Developer B: Complete User Story 2 (Phase 4)

# Stories are independent and can be developed in parallel
```

**Polish Phase** (after stories complete):
```bash
# All polish tasks can run in parallel:
T040 (JSDoc comments), T041 (downsampling), T042 (progressive loading), T043 (caching)
```

---

## Parallel Example: User Story 1 (Trade Markers)

```bash
# Step 1: Launch all tests together (RED phase):
Task T005: "test_chart_renders_with_trade_markers"
Task T006: "test_buy_markers_display_correctly"
Task T007: "test_sell_markers_display_correctly"
Task T008: "test_marker_tooltips_show_trade_details"
Task T009: "test_markers_persist_during_zoom_pan"

# Step 2: Verify all tests fail (no implementation)

# Step 3: Implement core helpers in parallel (GREEN phase):
Task T014: "isoToUnix() helper function"
Task T016: "formatTradeTooltip() function"

# Step 4: Implement sequential tasks (GREEN phase continues):
Task T010: "chart-enhanced.js skeleton"
Task T011: "createChartInstance()"
Task T012: "fetchOHLCVData()"
Task T013: "fetchTradeMarkers()"
Task T015: "createTradeMarker()"
Task T017: "integrate marker rendering"
# ... continue until all tests pass

# Step 5: All tests green - User Story 1 complete!
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

**Fastest Path to Value**:

1. Complete Phase 1: Setup (T001-T003)
2. Complete Phase 3: User Story 1 (T004-T022)
   - Write all tests first (RED)
   - Implement features to pass tests (GREEN)
   - Refactor and optimize (REFACTOR)
3. **STOP and VALIDATE**: Test User Story 1 independently
4. Deploy/demo trade markers feature

**Result**: Users can see trade execution points on price chart with tooltips

### Incremental Delivery (Both User Stories)

**Complete Feature Delivery**:

1. Complete Phase 1: Setup → Foundation ready
2. Complete Phase 3: User Story 1 → Test independently → Deploy/Demo (MVP!)
3. Complete Phase 4: User Story 2 → Test independently → Deploy/Demo
4. Complete Phase 5: Polish → Performance optimization → Final validation

**Result**: Users can see both trade markers AND strategy indicators with full interactivity

### Parallel Team Strategy

**With 2+ Developers**:

1. **All team members**: Complete Phase 1 (Setup) together
2. **Split after Setup**:
   - Developer A: User Story 1 (Trade Markers) - Phase 3
   - Developer B: User Story 2 (Indicators) - Phase 4
3. **Merge and Polish**: Both stories complete independently, then Phase 5 together

**Efficiency Gain**: 2 stories in parallel instead of sequential

---

## Task Summary

- **Total Tasks**: 47
- **Setup Tasks**: 3 (Phase 1)
- **User Story 1 Tasks**: 19 (6 tests + 13 implementation)
- **User Story 2 Tasks**: 17 (6 tests + 11 implementation)
- **Polish Tasks**: 8 (Phase 5)

### Tasks by User Story

- **User Story 1 (Trade Markers)**: 19 tasks (T004-T022)
  - Independent test: Load backtest, verify markers at correct positions
  - Delivers: Visual trade execution points with tooltips

- **User Story 2 (Indicators)**: 17 tasks (T023-T039)
  - Independent test: Load backtest, verify indicator lines with correct values
  - Delivers: Strategy indicator overlays with visibility controls

### Parallel Opportunities Identified

- **12 tasks** can run in parallel (marked with [P])
- **2 user stories** can be developed in parallel after Setup
- **Testing phase**: All tests within a story can be written concurrently

### MVP Scope

**Minimum Viable Product**: User Story 1 only
- **Task Range**: T001-T022 (22 tasks)
- **Delivery**: Trade markers on price chart
- **Time Estimate**: ~6 hours (4 hours implementation + 2 hours testing)

**Complete P1 Scope**: User Stories 1 + 2
- **Task Range**: T001-T047 (47 tasks)
- **Delivery**: Trade markers + indicator overlays with full interactivity
- **Time Estimate**: ~12 hours (8 hours implementation + 4 hours testing)

---

## Format Validation

✅ All tasks follow checklist format: `- [ ] [ID] [P?] [Story?] Description`
✅ All tasks include exact file paths
✅ Tasks organized by user story for independent implementation
✅ TDD workflow enforced: tests before implementation
✅ Dependencies clearly documented
✅ Parallel opportunities identified
✅ MVP scope defined (User Story 1)
✅ Independent test criteria for each story provided

---

## Notes

- **TDD CRITICAL**: All test tasks (T004-T009, T023-T028) MUST be written and fail before implementation
- **[P] marker**: Tasks can run in parallel (different files, no shared state)
- **[Story] label**: Maps task to specific user story (US1, US2)
- **Independent Stories**: Each user story can be completed and deployed separately
- **No Backend Changes**: All work is frontend JavaScript with existing APIs
- **File Size Constraints**: chart-enhanced.js must stay <500 lines, functions <50 lines
- **Performance Targets**: Chart <1s render, markers <500ms load, toggles <100ms response
- **Browser Support**: Chrome 90+, Firefox 88+, Safari 14+
