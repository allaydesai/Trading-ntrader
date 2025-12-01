# Comprehensive QA Test Plan - NTrader Web Application

**Document Version**: 1.0
**Date**: 2025-11-30
**Branch**: `qa/comprehensive-testing-plan`
**Application URL**: http://127.0.0.1:8000
**Status**: Draft

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Application Overview](#application-overview)
3. [Test Scope](#test-scope)
4. [Test Environment](#test-environment)
5. [Test Categories](#test-categories)
6. [Detailed Test Cases](#detailed-test-cases)
7. [UI/UX Validation Checklist](#uiux-validation-checklist)
8. [API Testing](#api-testing)
9. [Data Validation](#data-validation)
10. [Performance Benchmarks](#performance-benchmarks)
11. [Browser Compatibility](#browser-compatibility)
12. [Known Issues & Limitations](#known-issues--limitations)
13. [Test Execution Schedule](#test-execution-schedule)
14. [Sign-off Criteria](#sign-off-criteria)

---

## 1. Executive Summary

This document provides a comprehensive QA test plan for the NTrader algorithmic trading backtesting platform web application. The plan covers functional testing, UI/UX validation, API testing, data accuracy verification, and performance validation.

### Key Testing Objectives:
- ✅ Verify all UI elements render correctly and are functional
- ✅ Validate data accuracy in tables, charts, and metrics
- ✅ Test all interactive features (filters, pagination, HTMX actions)
- ✅ Ensure API endpoints return correct data and status codes
- ✅ Validate chart visualizations and trade markers
- ✅ Test error handling and edge cases
- ✅ Verify responsive design and browser compatibility

---

## 2. Application Overview

### Technology Stack
- **Backend**: FastAPI 0.109+, Python 3.11+
- **Frontend**: Jinja2 templates, HTMX 1.9+, Tailwind CSS
- **Charts**: TradingView Lightweight Charts v5
- **Database**: PostgreSQL 16+ with SQLAlchemy 2.0 (async)
- **Data Source**: Parquet files (Nautilus Trader catalog)

### Core Features Implemented
1. **Dashboard** (`/`) - Summary statistics and recent activity
2. **Backtest List** (`/backtests`) - Paginated, filterable backtest history
3. **Backtest Detail** (`/backtests/{run_id}`) - Comprehensive run analysis with charts
4. **Chart APIs** - 7 REST endpoints for chart data
5. **HTMX Integration** - Dynamic updates without page reloads
6. **Export Functions** - HTML reports, CSV/JSON trade exports

---

## 3. Test Scope

### In Scope
- ✅ All web UI pages (Dashboard, List, Detail)
- ✅ All API endpoints (8 chart APIs + UI routes)
- ✅ HTMX-powered interactions (filters, pagination, delete, rerun)
- ✅ Chart rendering (price chart, equity curve)
- ✅ Data accuracy (metrics, trade data, chart data)
- ✅ UI layout and visual consistency
- ✅ Error handling and edge cases
- ✅ Browser compatibility (Chrome, Firefox, Safari)
- ✅ Responsive design (desktop, tablet, mobile)

### Out of Scope
- ❌ CLI testing (separate test plan)
- ❌ Backend backtesting engine testing (covered by unit tests)
- ❌ Database schema migration testing
- ❌ Load testing / stress testing (future iteration)
- ❌ Security penetration testing (future iteration)

---

## 4. Test Environment

### Server Configuration
```bash
# Start web server
uv run uvicorn src.api.web:app --reload --port 8000

# Access URL
http://127.0.0.1:8000
```

### Test Data Requirements
- Minimum 5 backtests in database with different:
  - Strategies (SMA crossover, Bollinger reversal, etc.)
  - Instruments (SPY.ARCA, AAPL.NASDAQ, etc.)
  - Date ranges
  - Success/failure statuses
  - Trade counts (including one with 0 trades)

### Prerequisites
- PostgreSQL running with backtest_runs and trades tables populated
- Parquet market data files in `data/catalog/`
- All dependencies installed (`uv sync`)

---

## 5. Test Categories

| Category | Priority | Test Count | Owner | Status |
|----------|----------|------------|-------|--------|
| Dashboard UI | High | 12 | QA | Pending |
| Backtest List UI | High | 18 | QA | Pending |
| Backtest Detail UI | Critical | 25 | QA | Pending |
| Chart Rendering | Critical | 15 | QA | Pending |
| API Endpoints | High | 14 | QA | Pending |
| HTMX Interactions | High | 10 | QA | Pending |
| Filters & Pagination | High | 12 | QA | Pending |
| Data Accuracy | Critical | 20 | QA | Pending |
| Error Handling | Medium | 8 | QA | Pending |
| Browser Compatibility | Medium | 6 | QA | Pending |
| Responsive Design | Low | 4 | QA | Pending |
| **TOTAL** | - | **144** | - | - |

---

## 6. Detailed Test Cases

### 6.1 Dashboard Page (`/`)

#### TC-DASH-001: Dashboard Loads Successfully
**Priority**: Critical
**Precondition**: Database has at least 1 backtest
**Steps**:
1. Navigate to `http://127.0.0.1:8000/`
2. Verify page loads without errors (HTTP 200)

**Expected Result**:
- Page renders completely
- No JavaScript console errors
- HTTP 200 status

**Validation Points**:
- [ ] Page title is "NTrader - Dashboard"
- [ ] Navigation bar is visible
- [ ] Footer is visible

---

#### TC-DASH-002: Summary Statistics Display
**Priority**: High
**Precondition**: Database has multiple backtests with varying metrics
**Steps**:
1. Navigate to dashboard
2. Locate the "Summary" or statistics section

**Expected Result**:
- Total Backtests count is accurate
- Best Sharpe Ratio matches highest in database
- Worst Max Drawdown matches lowest in database

**Validation Points**:
- [ ] "Total Backtests" number matches database count
- [ ] "Best Sharpe Ratio" value is correctly formatted (2 decimal places)
- [ ] "Worst Max Drawdown" is negative and formatted as percentage
- [ ] All stat cards are properly styled with Tailwind CSS

---

#### TC-DASH-003: Recent Activity Feed
**Priority**: High
**Precondition**: Database has at least 5 backtests
**Steps**:
1. Navigate to dashboard
2. Locate "Recent Activity" or "Recent Backtests" section

**Expected Result**:
- Shows last 5 backtests ordered by `created_at` DESC
- Each entry displays: strategy, instrument, date, status

**Validation Points**:
- [ ] Exactly 5 entries shown (or fewer if <5 total backtests)
- [ ] Entries are in reverse chronological order
- [ ] Each entry shows strategy name
- [ ] Each entry shows instrument symbol
- [ ] Status badges are color-coded (green for success, red for failed)

---

#### TC-DASH-004: Empty State Handling
**Priority**: Medium
**Precondition**: Database has 0 backtests
**Steps**:
1. Clear all backtests from database
2. Navigate to dashboard

**Expected Result**:
- Page doesn't crash
- Shows appropriate empty state message
- "Total Backtests" shows 0

**Validation Points**:
- [ ] No errors in console
- [ ] Message like "No backtests yet" is displayed
- [ ] CTA button to run first backtest or view docs

---

#### TC-DASH-005: Navigation Links
**Priority**: High
**Steps**:
1. Navigate to dashboard
2. Click "View All Backtests" button

**Expected Result**:
- Navigates to `/backtests` page
- Backtest list page loads successfully

**Validation Points**:
- [ ] "View All Backtests" button is clickable
- [ ] Redirects to correct URL
- [ ] No broken links

---

#### TC-DASH-006: Dashboard Performance
**Priority**: Medium
**Steps**:
1. Navigate to dashboard
2. Measure page load time (DevTools Network tab)

**Expected Result**:
- Page loads in <500ms
- All assets load successfully

**Validation Points**:
- [ ] Initial HTML loads <200ms
- [ ] Static assets (CSS, JS) load successfully
- [ ] No 404 errors for missing resources

---

#### TC-DASH-007: Stats Calculation Accuracy
**Priority**: Critical
**Precondition**: Known backtest data in database
**Steps**:
1. Manually query database for best Sharpe and worst drawdown
2. Compare with dashboard display

**Expected Result**:
- Dashboard values match database query results exactly

**Validation Points**:
- [ ] Best Sharpe Ratio matches `SELECT MAX(sharpe_ratio) FROM backtest_runs`
- [ ] Worst Max Drawdown matches `SELECT MIN(max_drawdown) FROM backtest_runs`
- [ ] Total count matches `SELECT COUNT(*) FROM backtest_runs`

---

#### TC-DASH-008: Dark Mode Theme
**Priority**: Low
**Steps**:
1. Navigate to dashboard
2. Inspect background colors and text colors

**Expected Result**:
- Dark theme is applied
- Text is readable with sufficient contrast

**Validation Points**:
- [ ] Background is dark (not white)
- [ ] Text is light colored
- [ ] Contrast ratio meets WCAG AA standards

---

#### TC-DASH-009: Breadcrumb Navigation
**Priority**: Low
**Steps**:
1. Navigate to dashboard
2. Check for breadcrumb trail

**Expected Result**:
- Breadcrumb shows "Home" or "Dashboard"

**Validation Points**:
- [ ] Breadcrumb is visible
- [ ] Current page is highlighted

---

#### TC-DASH-010: Footer Links
**Priority**: Low
**Steps**:
1. Scroll to bottom of dashboard
2. Verify footer content

**Expected Result**:
- Footer is visible with app name/version

**Validation Points**:
- [ ] Footer text is present
- [ ] Year is current (2025)

---

#### TC-DASH-011: Navigation Active State
**Priority**: Low
**Steps**:
1. Navigate to dashboard
2. Check navigation bar

**Expected Result**:
- "Dashboard" link is highlighted/active

**Validation Points**:
- [ ] Dashboard nav item has active styling
- [ ] Other nav items are not active

---

#### TC-DASH-012: Responsive Layout
**Priority**: Medium
**Steps**:
1. Open dashboard in browser
2. Resize window to mobile width (375px)

**Expected Result**:
- Layout adjusts for mobile
- All elements remain visible and usable

**Validation Points**:
- [ ] No horizontal scrolling
- [ ] Stats stack vertically
- [ ] Navigation collapses appropriately

---

### 6.2 Backtest List Page (`/backtests`)

#### TC-LIST-001: List Page Loads Successfully
**Priority**: Critical
**Precondition**: Database has at least 1 backtest
**Steps**:
1. Navigate to `http://127.0.0.1:8000/backtests`
2. Verify page loads without errors

**Expected Result**:
- Page renders with HTTP 200
- Table displays backtest rows

**Validation Points**:
- [ ] Page title is "Backtests"
- [ ] Table header is visible
- [ ] At least 1 row is displayed

---

#### TC-LIST-002: Table Column Headers
**Priority**: High
**Steps**:
1. Navigate to backtest list
2. Inspect table header row

**Expected Result**:
- All expected columns are present

**Validation Points**:
- [ ] "Run ID" column
- [ ] "Strategy" column
- [ ] "Instrument" column
- [ ] "Date Range" column
- [ ] "Return %" column
- [ ] "Sharpe" column
- [ ] "Max DD %" column
- [ ] "Status" column
- [ ] "Created" column

---

#### TC-LIST-003: Pagination - First Page
**Priority**: High
**Precondition**: Database has >20 backtests
**Steps**:
1. Navigate to `/backtests`
2. Verify first page shows exactly 20 items

**Expected Result**:
- Exactly 20 rows displayed
- Pagination controls visible

**Validation Points**:
- [ ] Row count is 20
- [ ] "Next" button is enabled
- [ ] "Previous" button is disabled
- [ ] Page indicator shows "Page 1"

---

#### TC-LIST-004: Pagination - Navigate to Page 2
**Priority**: High
**Precondition**: Database has >20 backtests
**Steps**:
1. Navigate to `/backtests`
2. Click "Next" or "Page 2" button

**Expected Result**:
- Page 2 loads via HTMX (no full page reload)
- Next 20 items displayed
- URL updates to `?page=2`

**Validation Points**:
- [ ] Table content updates without page flash
- [ ] URL parameter `page=2` is present
- [ ] "Previous" button is now enabled
- [ ] Correct rows displayed (IDs 21-40)

---

#### TC-LIST-005: Filter by Strategy
**Priority**: High
**Precondition**: Database has backtests with different strategies
**Steps**:
1. Navigate to `/backtests`
2. Select "bollinger_reversal" from Strategy dropdown
3. Observe table update

**Expected Result**:
- Table filters to show only Bollinger Reversal backtests
- HTMX triggers update

**Validation Points**:
- [ ] Only rows with strategy="bollinger_reversal" are shown
- [ ] Filter dropdown shows selected value
- [ ] URL includes `?strategy=bollinger_reversal`
- [ ] Page resets to 1

---

#### TC-LIST-006: Filter by Instrument
**Priority**: High
**Precondition**: Database has backtests for multiple instruments
**Steps**:
1. Navigate to `/backtests`
2. Type "SPY" in Instrument filter field
3. Observe table update

**Expected Result**:
- Table filters to SPY-related backtests
- Autocomplete suggestions may appear

**Validation Points**:
- [ ] Only rows with "SPY" in instrument are shown
- [ ] URL includes `?instrument=SPY`
- [ ] Filter triggers after typing (debounce 500ms)

---

#### TC-LIST-007: Filter by Status - Success
**Priority**: High
**Steps**:
1. Navigate to `/backtests`
2. Select "Success" from Status dropdown

**Expected Result**:
- Only successful backtests displayed

**Validation Points**:
- [ ] All rows have green success badge
- [ ] No failed backtests shown
- [ ] URL includes `?status=success`

---

#### TC-LIST-008: Filter by Status - Failed
**Priority**: High
**Precondition**: Database has at least 1 failed backtest
**Steps**:
1. Navigate to `/backtests`
2. Select "Failed" from Status dropdown

**Expected Result**:
- Only failed backtests displayed

**Validation Points**:
- [ ] All rows have red failed badge
- [ ] No successful backtests shown
- [ ] URL includes `?status=failed`

---

#### TC-LIST-009: Filter by Date Range
**Priority**: Medium
**Steps**:
1. Navigate to `/backtests`
2. Set "Date From" to `2024-01-01`
3. Set "Date To" to `2024-12-31`
4. Observe table update

**Expected Result**:
- Only backtests created within 2024 are shown

**Validation Points**:
- [ ] All rows have created_at within specified range
- [ ] URL includes `?date_from=2024-01-01&date_to=2024-12-31`

---

#### TC-LIST-010: Combined Filters
**Priority**: High
**Steps**:
1. Select strategy="sma_crossover"
2. Select instrument="SPY.ARCA"
3. Select status="success"

**Expected Result**:
- Results match ALL filter criteria (AND logic)

**Validation Points**:
- [ ] Only SMA crossover strategy shown
- [ ] Only SPY.ARCA instrument shown
- [ ] Only success status shown
- [ ] URL contains all filter parameters

---

#### TC-LIST-011: Clear Filters Button
**Priority**: Medium
**Steps**:
1. Apply multiple filters
2. Click "Clear Filters" button

**Expected Result**:
- All filters reset to default
- Full list displayed

**Validation Points**:
- [ ] All dropdowns reset to "All" or empty
- [ ] Date fields cleared
- [ ] URL resets to `/backtests` (no query params)
- [ ] Table shows all backtests

---

#### TC-LIST-012: Sort by Created Date DESC (Default)
**Priority**: High
**Steps**:
1. Navigate to `/backtests` (fresh load)
2. Inspect first row

**Expected Result**:
- Most recently created backtest appears first

**Validation Points**:
- [ ] Rows are in descending created_at order
- [ ] Newest backtest is on top

---

#### TC-LIST-013: Sort by Sharpe Ratio DESC
**Priority**: Medium
**Steps**:
1. Navigate to `/backtests`
2. Select "Sharpe Ratio" from Sort dropdown
3. Select "Descending" order

**Expected Result**:
- Backtests sorted by highest Sharpe first

**Validation Points**:
- [ ] Sharpe values decrease from top to bottom
- [ ] URL includes `?sort=sharpe_ratio&order=desc`

---

#### TC-LIST-014: Sort by Max Drawdown ASC
**Priority**: Medium
**Steps**:
1. Select "Max Drawdown" from Sort dropdown
2. Select "Ascending" order

**Expected Result**:
- Least negative drawdown first

**Validation Points**:
- [ ] Max DD values increase (become more negative) from top to bottom
- [ ] URL includes `?sort=max_drawdown&order=asc`

---

#### TC-LIST-015: Color-Coded Returns
**Priority**: Medium
**Precondition**: Database has both positive and negative return backtests
**Steps**:
1. Navigate to `/backtests`
2. Inspect "Return %" column

**Expected Result**:
- Positive returns are green
- Negative returns are red

**Validation Points**:
- [ ] Rows with return > 0 have green text
- [ ] Rows with return < 0 have red text
- [ ] Zero returns have neutral color

---

#### TC-LIST-016: Status Badge Styling
**Priority**: Low
**Steps**:
1. Navigate to `/backtests`
2. Inspect "Status" column

**Expected Result**:
- "success" has green badge
- "failed" has red badge

**Validation Points**:
- [ ] Success badge has green background
- [ ] Failed badge has red background
- [ ] Text is readable on badge background

---

#### TC-LIST-017: Click Row to View Detail
**Priority**: Critical
**Steps**:
1. Navigate to `/backtests`
2. Click on a backtest row (or view detail link)

**Expected Result**:
- Navigates to `/backtests/{run_id}` detail page

**Validation Points**:
- [ ] Detail page loads for correct backtest
- [ ] URL contains correct UUID

---

#### TC-LIST-018: Empty Results After Filter
**Priority**: Medium
**Steps**:
1. Apply filter combination that matches no backtests
2. Observe result

**Expected Result**:
- Empty state message displayed
- No error thrown

**Validation Points**:
- [ ] Message like "No backtests match your filters"
- [ ] No table rows displayed
- [ ] Page doesn't crash

---

### 6.3 Backtest Detail Page (`/backtests/{run_id}`)

#### TC-DETAIL-001: Detail Page Loads for Valid Run ID
**Priority**: Critical
**Precondition**: Database has backtest with known run_id
**Steps**:
1. Navigate to `/backtests/{valid_run_id}`

**Expected Result**:
- Page loads successfully (HTTP 200)
- All sections render

**Validation Points**:
- [ ] Page title includes run ID
- [ ] Header section visible
- [ ] Performance metrics section visible
- [ ] Charts section visible
- [ ] No JavaScript errors

---

#### TC-DETAIL-002: 404 for Invalid Run ID
**Priority**: High
**Steps**:
1. Navigate to `/backtests/invalid-uuid-12345`

**Expected Result**:
- Returns HTTP 404 error
- Error page displayed

**Validation Points**:
- [ ] HTTP 404 status
- [ ] User-friendly error message
- [ ] Link back to backtest list

---

#### TC-DETAIL-003: Header Section - Run Information
**Priority**: High
**Steps**:
1. Navigate to detail page
2. Inspect header section

**Expected Result**:
- Run ID, strategy, execution time, duration, status displayed

**Validation Points**:
- [ ] Run ID matches URL parameter
- [ ] Strategy name is correct
- [ ] Execution time formatted correctly
- [ ] Duration in seconds displayed
- [ ] Status badge shows correct status

---

#### TC-DETAIL-004: Error Message Display (Failed Backtest)
**Priority**: High
**Precondition**: Database has a failed backtest with error_message
**Steps**:
1. Navigate to failed backtest detail page
2. Look for error message section

**Expected Result**:
- Error message is displayed prominently

**Validation Points**:
- [ ] Error message text is visible
- [ ] Error section has warning/error styling (red background)
- [ ] Message content matches database `error_message` field

---

#### TC-DETAIL-005: Action Buttons - Export HTML Report
**Priority**: High
**Steps**:
1. Navigate to detail page
2. Click "Export Report (HTML)" button

**Expected Result**:
- HTML file downloads
- File contains backtest summary

**Validation Points**:
- [ ] File downloads successfully
- [ ] Filename includes run_id or strategy name
- [ ] HTML file opens in browser
- [ ] Contains charts and metrics

---

#### TC-DETAIL-006: Action Buttons - Export Trades CSV
**Priority**: High
**Precondition**: Backtest has >0 trades
**Steps**:
1. Click "Export Trades (CSV)" button

**Expected Result**:
- CSV file downloads
- Contains all trades for this backtest

**Validation Points**:
- [ ] CSV file downloads
- [ ] Filename includes run_id
- [ ] CSV has correct headers
- [ ] Row count matches trade count
- [ ] Data is properly comma-separated

---

#### TC-DETAIL-007: Action Buttons - Export Trades JSON
**Priority**: Medium
**Precondition**: Backtest has >0 trades
**Steps**:
1. Click "Export Trades (JSON)" button

**Expected Result**:
- JSON file downloads
- Contains trade array

**Validation Points**:
- [ ] JSON file downloads
- [ ] Valid JSON syntax
- [ ] Contains array of trade objects
- [ ] Trade count matches database

---

#### TC-DETAIL-008: Action Buttons - Delete Backtest
**Priority**: High
**Steps**:
1. Click "Delete" button
2. Observe confirmation modal

**Expected Result**:
- Confirmation modal appears
- User can confirm or cancel

**Validation Points**:
- [ ] Modal pops up before deletion
- [ ] Modal asks for confirmation
- [ ] "Cancel" button dismisses modal
- [ ] "Confirm Delete" button triggers deletion (returns 200)

---

#### TC-DETAIL-009: Action Buttons - Re-run Backtest
**Priority**: Medium
**Steps**:
1. Click "Re-run" button

**Expected Result**:
- HTTP 202 Accepted response
- Spinner or loading indicator shows

**Validation Points**:
- [ ] Button triggers POST request
- [ ] Returns 202 status
- [ ] Loading spinner appears during request
- [ ] Success message shown (even if not actually re-run yet)

---

#### TC-DETAIL-010: Performance Metrics Panel - Returns
**Priority**: Critical
**Precondition**: Known backtest with calculated metrics
**Steps**:
1. Navigate to detail page
2. Locate "Returns" section in metrics panel

**Expected Result**:
- Total Return, Annualized Return displayed

**Validation Points**:
- [ ] Total Return % matches database
- [ ] Annualized Return % matches database
- [ ] Values formatted to 2 decimal places
- [ ] Positive values are green, negative are red

---

#### TC-DETAIL-011: Performance Metrics Panel - Risk Metrics
**Priority**: Critical
**Steps**:
1. Inspect "Risk" section in metrics panel

**Expected Result**:
- Sharpe Ratio, Max Drawdown, Volatility displayed

**Validation Points**:
- [ ] Sharpe Ratio value correct
- [ ] Max Drawdown % correct (negative value)
- [ ] Volatility % correct
- [ ] Tooltip icons present for explanations

---

#### TC-DETAIL-012: Performance Metrics Panel - Trading Metrics
**Priority**: High
**Steps**:
1. Inspect "Trading" section in metrics panel

**Expected Result**:
- Total Trades, Win Rate, Avg Win/Loss displayed

**Validation Points**:
- [ ] Total Trades count matches database
- [ ] Win Rate % calculated correctly
- [ ] Avg Win and Avg Loss displayed
- [ ] Profit Factor shown

---

#### TC-DETAIL-013: Tooltip Functionality
**Priority**: Low
**Steps**:
1. Hover over tooltip icon next to "Sharpe Ratio"

**Expected Result**:
- Tooltip appears explaining Sharpe Ratio

**Validation Points**:
- [ ] Tooltip text is visible
- [ ] Tooltip is readable
- [ ] Tooltip disappears on mouse leave

---

#### TC-DETAIL-014: Trading Summary Section
**Priority**: Medium
**Steps**:
1. Locate "Trading Summary" section

**Expected Result**:
- Displays key trade statistics

**Validation Points**:
- [ ] Longest winning streak shown
- [ ] Longest losing streak shown
- [ ] Best trade P&L shown
- [ ] Worst trade P&L shown

---

#### TC-DETAIL-015: Price Chart - Loads Successfully
**Priority**: Critical
**Precondition**: Backtest has market data
**Steps**:
1. Scroll to "Price Chart" section
2. Wait for chart to load

**Expected Result**:
- TradingView Lightweight Chart renders
- OHLCV candlesticks displayed

**Validation Points**:
- [ ] Chart canvas is visible (not blank)
- [ ] Candlesticks render
- [ ] X-axis shows dates
- [ ] Y-axis shows prices
- [ ] No errors in console

---

#### TC-DETAIL-016: Price Chart - Trade Entry Markers
**Priority**: Critical
**Precondition**: Backtest has buy trades
**Steps**:
1. Inspect price chart
2. Look for green upward triangle markers

**Expected Result**:
- Green triangles appear below candles at entry points

**Validation Points**:
- [ ] Green upward triangles visible
- [ ] Markers positioned at correct dates
- [ ] Marker count matches number of buy entries
- [ ] Markers are below price candles

---

#### TC-DETAIL-017: Price Chart - Trade Exit Markers
**Priority**: Critical
**Precondition**: Backtest has sell trades
**Steps**:
1. Inspect price chart
2. Look for red downward triangle markers

**Expected Result**:
- Red triangles appear above candles at exit points

**Validation Points**:
- [ ] Red downward triangles visible
- [ ] Markers positioned at correct dates
- [ ] Marker count matches number of sell exits
- [ ] Markers are above price candles

---

#### TC-DETAIL-018: Price Chart - Trade Marker Tooltips
**Priority**: High
**Steps**:
1. Hover mouse over a trade marker

**Expected Result**:
- Tooltip appears with trade details

**Validation Points**:
- [ ] Tooltip shows trade type (BUY/SELL)
- [ ] Tooltip shows price
- [ ] Tooltip shows quantity
- [ ] Tooltip shows timestamp
- [ ] Tooltip shows P&L for exit markers

---

#### TC-DETAIL-019: Price Chart - Zoom and Pan
**Priority**: Medium
**Steps**:
1. Use mouse wheel to zoom in on chart
2. Click and drag to pan

**Expected Result**:
- Chart zooms in/out smoothly
- Chart pans left/right

**Validation Points**:
- [ ] Zoom in increases candle width
- [ ] Zoom out decreases candle width
- [ ] Pan moves time range
- [ ] Trade markers remain accurate during zoom/pan

---

#### TC-DETAIL-020: Price Chart - Volume Bars
**Priority**: Low
**Steps**:
1. Look for volume bars at bottom of price chart

**Expected Result**:
- Volume bars displayed below price

**Validation Points**:
- [ ] Volume bars visible
- [ ] Volume bars aligned with candles

---

#### TC-DETAIL-021: Equity Curve Chart - Loads Successfully
**Priority**: Critical
**Steps**:
1. Scroll to "Equity Curve" section
2. Wait for chart to load

**Expected Result**:
- Line chart displays account balance over time

**Validation Points**:
- [ ] Chart renders without errors
- [ ] Line shows equity progression
- [ ] X-axis shows dates
- [ ] Y-axis shows dollar values
- [ ] Starting value matches initial capital

---

#### TC-DETAIL-022: Equity Curve - Drawdown Visualization
**Priority**: High
**Steps**:
1. Inspect equity curve chart
2. Look for drawdown area/line

**Expected Result**:
- Drawdown shown as separate line or shaded area

**Validation Points**:
- [ ] Drawdown line/area is visible
- [ ] Drawdown is negative (below zero line)
- [ ] Max drawdown point matches metric panel

---

#### TC-DETAIL-023: Trades Table - Pagination
**Priority**: High
**Precondition**: Backtest has >20 trades
**Steps**:
1. Scroll to "Trades" table
2. Observe pagination controls

**Expected Result**:
- First 20 trades displayed
- Pagination controls present

**Validation Points**:
- [ ] Exactly 20 rows per page
- [ ] "Next" button works
- [ ] Page number indicator correct

---

#### TC-DETAIL-024: Trades Table - Column Headers
**Priority**: Medium
**Steps**:
1. Inspect trades table header row

**Expected Result**:
- All relevant columns present

**Validation Points**:
- [ ] "Trade ID" column
- [ ] "Type" (BUY/SELL) column
- [ ] "Entry Time" column
- [ ] "Exit Time" column
- [ ] "Entry Price" column
- [ ] "Exit Price" column
- [ ] "Quantity" column
- [ ] "P&L" column
- [ ] "Return %" column

---

#### TC-DETAIL-025: Trades Table - Data Accuracy
**Priority**: Critical
**Steps**:
1. Pick first trade row
2. Verify values against database

**Expected Result**:
- All values match database exactly

**Validation Points**:
- [ ] Trade ID matches database
- [ ] Entry/exit prices correct
- [ ] Quantity correct
- [ ] P&L calculation correct
- [ ] Return % calculation correct

---

### 6.4 Chart API Endpoints

#### TC-API-001: GET /api/timeseries - Valid Request
**Priority**: Critical
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/timeseries?symbol=SPY.ARCA&start=2024-01-01&end=2024-12-31&timeframe=1_DAY"
```

**Expected Result**:
- HTTP 200 OK
- JSON array of OHLCV objects

**Validation Points**:
- [ ] Status code is 200
- [ ] Response is valid JSON
- [ ] Contains array of candles
- [ ] Each candle has: time, open, high, low, close, volume
- [ ] Date range matches request

---

#### TC-API-002: GET /api/timeseries - Missing Parameters
**Priority**: High
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/timeseries?symbol=SPY.ARCA"
```

**Expected Result**:
- HTTP 422 Unprocessable Entity
- Error message about missing parameters

**Validation Points**:
- [ ] Status code is 422
- [ ] Error response contains field validation details

---

#### TC-API-003: GET /api/timeseries - Invalid Symbol
**Priority**: High
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/timeseries?symbol=INVALID&start=2024-01-01&end=2024-12-31&timeframe=1_DAY"
```

**Expected Result**:
- HTTP 404 Not Found (or 200 with empty array)
- Appropriate error message

**Validation Points**:
- [ ] Returns 404 or empty array
- [ ] Error message indicates symbol not found

---

#### TC-API-004: GET /api/equity/{run_id} - Valid Request
**Priority**: Critical
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/equity/{valid_run_id}"
```

**Expected Result**:
- HTTP 200 OK
- JSON with equity_curve and drawdown arrays

**Validation Points**:
- [ ] Status code is 200
- [ ] Response contains "equity_curve" array
- [ ] Response contains "drawdown" array
- [ ] Each point has timestamp and value

---

#### TC-API-005: GET /api/equity/{run_id} - Invalid Run ID
**Priority**: High
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/equity/invalid-uuid"
```

**Expected Result**:
- HTTP 404 Not Found

**Validation Points**:
- [ ] Status code is 404
- [ ] Error message indicates backtest not found

---

#### TC-API-006: GET /api/trades/{run_id} - Valid Request
**Priority**: Critical
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/trades/{valid_run_id}"
```

**Expected Result**:
- HTTP 200 OK
- JSON array of trade markers

**Validation Points**:
- [ ] Status code is 200
- [ ] Response is array of trades
- [ ] Each trade has: time, position (above/below), color, shape, text

---

#### TC-API-007: GET /api/indicators/{run_id} - Bollinger Strategy
**Priority**: High
**Precondition**: Run ID is for Bollinger Reversal strategy
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/indicators/{bollinger_run_id}"
```

**Expected Result**:
- HTTP 200 OK
- JSON with Bollinger bands

**Validation Points**:
- [ ] Status code is 200
- [ ] Response contains "upper_band", "middle_band", "lower_band"
- [ ] Each band is an array of {time, value}

---

#### TC-API-008: GET /api/indicators/{run_id} - SMA Strategy
**Priority**: High
**Precondition**: Run ID is for SMA Crossover strategy
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/indicators/{sma_run_id}"
```

**Expected Result**:
- HTTP 200 OK
- JSON with SMA lines

**Validation Points**:
- [ ] Status code is 200
- [ ] Response contains "fast_sma" and "slow_sma"
- [ ] Each SMA is an array of {time, value}

---

#### TC-API-009: GET /api/equity-curve/{backtest_id} - Valid Request
**Priority**: High
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/equity-curve/{valid_backtest_id}"
```

**Expected Result**:
- HTTP 200 OK
- JSON array of equity points

**Validation Points**:
- [ ] Status code is 200
- [ ] Array of {timestamp, balance} objects
- [ ] Points are chronological

---

#### TC-API-010: GET /api/statistics/{backtest_id} - Valid Request
**Priority**: Medium
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/statistics/{valid_backtest_id}"
```

**Expected Result**:
- HTTP 200 OK
- JSON with trade statistics

**Validation Points**:
- [ ] Status code is 200
- [ ] Contains: total_trades, winning_trades, losing_trades, win_rate
- [ ] All calculations are correct

---

#### TC-API-011: GET /api/drawdown/{backtest_id} - Valid Request
**Priority**: Medium
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/drawdown/{valid_backtest_id}"
```

**Expected Result**:
- HTTP 200 OK
- JSON with drawdown metrics

**Validation Points**:
- [ ] Status code is 200
- [ ] Contains: max_drawdown, drawdown_duration
- [ ] Values match performance metrics panel

---

#### TC-API-012: GET /api/backtests/{id}/trades - Pagination
**Priority**: High
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/backtests/{id}/trades?page=1&page_size=10"
```

**Expected Result**:
- HTTP 200 OK
- JSON with paginated trades

**Validation Points**:
- [ ] Status code is 200
- [ ] Response contains "data" array (10 items)
- [ ] Response contains "total_count", "page", "page_size"
- [ ] Pagination metadata is accurate

---

#### TC-API-013: GET /api/backtests/{id}/export - CSV Format
**Priority**: High
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/backtests/{id}/export?format=csv"
```

**Expected Result**:
- HTTP 200 OK
- CSV file content

**Validation Points**:
- [ ] Content-Type: text/csv
- [ ] CSV headers present
- [ ] Data is comma-separated

---

#### TC-API-014: GET /api/backtests/{id}/export - JSON Format
**Priority**: High
**Steps**:
```bash
curl "http://127.0.0.1:8000/api/backtests/{id}/export?format=json"
```

**Expected Result**:
- HTTP 200 OK
- JSON array of trades

**Validation Points**:
- [ ] Content-Type: application/json
- [ ] Valid JSON array
- [ ] Contains all trade fields

---

### 6.5 HTMX Interactions

#### TC-HTMX-001: Filter Change Triggers Table Update
**Priority**: High
**Steps**:
1. Open DevTools Network tab
2. Change strategy filter on `/backtests` page
3. Observe network request

**Expected Result**:
- AJAX request to `/backtests/fragment`
- Table updates without page reload

**Validation Points**:
- [ ] Request is XHR/fetch (not full page load)
- [ ] URL includes filter parameter
- [ ] Response is HTML fragment
- [ ] Table content updates in DOM

---

#### TC-HTMX-002: Pagination via HTMX
**Priority**: High
**Steps**:
1. Click "Next" page button
2. Observe no page reload

**Expected Result**:
- HTMX triggers fragment request
- Table updates smoothly

**Validation Points**:
- [ ] No full page reload
- [ ] URL updates with `?page=2`
- [ ] History state is pushed

---

#### TC-HTMX-003: Delete Button Confirmation
**Priority**: High
**Steps**:
1. Click "Delete" on detail page
2. Observe confirmation

**Expected Result**:
- Confirmation modal appears

**Validation Points**:
- [ ] Modal uses HTMX confirm
- [ ] DELETE request only sent after confirmation
- [ ] Modal dismisses on cancel

---

#### TC-HTMX-004: Re-run Button Loading State
**Priority**: Medium
**Steps**:
1. Click "Re-run" button
2. Observe loading indicator

**Expected Result**:
- Button shows loading spinner during request

**Validation Points**:
- [ ] Spinner appears
- [ ] Button is disabled during request
- [ ] Spinner disappears on response

---

#### TC-HTMX-005: Filter Debouncing (Instrument Field)
**Priority**: Medium
**Steps**:
1. Type "SPY" quickly in instrument filter
2. Observe when request fires

**Expected Result**:
- Request fires after 500ms delay, not on each keystroke

**Validation Points**:
- [ ] Debounce prevents excessive requests
- [ ] Single request after typing stops

---

#### TC-HTMX-006: Sort Dropdown Triggers Update
**Priority**: Medium
**Steps**:
1. Change sort field dropdown
2. Observe table update

**Expected Result**:
- HTMX triggers re-sort

**Validation Points**:
- [ ] Table re-orders
- [ ] URL includes `?sort={field}`

---

#### TC-HTMX-007: URL State Preservation
**Priority**: High
**Steps**:
1. Apply filters, go to page 2
2. Copy URL
3. Open URL in new tab

**Expected Result**:
- Filters and page are preserved

**Validation Points**:
- [ ] Filters are applied on load
- [ ] Correct page is displayed
- [ ] State is fully reproducible from URL

---

#### TC-HTMX-008: Back Button Navigation
**Priority**: Medium
**Steps**:
1. Navigate through list pages via HTMX
2. Click browser back button

**Expected Result**:
- HTMX respects history
- Previous page state restores

**Validation Points**:
- [ ] Back button goes to previous page
- [ ] Filters restore correctly

---

#### TC-HTMX-009: Error Handling - Network Failure
**Priority**: Medium
**Steps**:
1. Stop web server
2. Try to filter on `/backtests` page

**Expected Result**:
- User sees error message

**Validation Points**:
- [ ] Error message appears
- [ ] Page doesn't break

---

#### TC-HTMX-010: Trades Table Fragment Load
**Priority**: High
**Steps**:
1. Load detail page
2. Observe trades table load

**Expected Result**:
- Trades table loads via HTMX

**Validation Points**:
- [ ] Initial table render uses fragment
- [ ] Pagination within table uses HTMX

---

### 6.6 Data Validation

#### TC-DATA-001: Sharpe Ratio Calculation
**Priority**: Critical
**Precondition**: Known backtest with calculable Sharpe
**Steps**:
1. Query database for backtest performance metrics
2. Manually calculate: (mean_return - risk_free_rate) / std_dev_return
3. Compare with displayed value

**Expected Result**:
- Displayed Sharpe matches manual calculation

**Validation Points**:
- [ ] Sharpe ratio accurate to 2 decimal places

---

#### TC-DATA-002: Max Drawdown Calculation
**Priority**: Critical
**Steps**:
1. Get equity curve data
2. Calculate max peak-to-trough decline
3. Compare with displayed max drawdown

**Expected Result**:
- Max drawdown % is accurate

**Validation Points**:
- [ ] Max drawdown matches calculation
- [ ] Value is negative (or zero)

---

#### TC-DATA-003: Win Rate Calculation
**Priority**: High
**Steps**:
1. Count winning trades (P&L > 0)
2. Divide by total trades
3. Compare with displayed win rate

**Expected Result**:
- Win rate % matches calculation

**Validation Points**:
- [ ] Win rate = (winning_trades / total_trades) * 100

---

#### TC-DATA-004: Total Return Calculation
**Priority**: Critical
**Steps**:
1. Calculate: (final_equity - initial_capital) / initial_capital * 100
2. Compare with displayed total return

**Expected Result**:
- Total return % is accurate

**Validation Points**:
- [ ] Total return matches formula

---

#### TC-DATA-005: Trade P&L Calculation
**Priority**: Critical
**Steps**:
1. For a single trade, calculate: (exit_price - entry_price) * quantity - commissions
2. Compare with displayed P&L

**Expected Result**:
- P&L matches calculation

**Validation Points**:
- [ ] P&L = (exit_price - entry_price) * quantity - fees

---

#### TC-DATA-006: Annualized Return Calculation
**Priority**: High
**Steps**:
1. Calculate based on total return and time period
2. Compare with displayed annualized return

**Expected Result**:
- Annualized return is accurate

**Validation Points**:
- [ ] Accounts for time period correctly

---

#### TC-DATA-007: Trade Markers Position Accuracy
**Priority**: Critical
**Steps**:
1. Select a trade from trades table
2. Note entry timestamp
3. Locate corresponding marker on price chart

**Expected Result**:
- Marker appears at exact timestamp

**Validation Points**:
- [ ] Marker X-position matches trade timestamp
- [ ] Marker Y-position near entry/exit price

---

#### TC-DATA-008: Equity Curve Start Value
**Priority**: High
**Steps**:
1. Check initial capital in backtest config
2. Verify equity curve starts at this value

**Expected Result**:
- First equity point = initial capital

**Validation Points**:
- [ ] Equity curve starts at initial_capital

---

#### TC-DATA-009: Equity Curve End Value
**Priority**: High
**Steps**:
1. Calculate: initial_capital * (1 + total_return)
2. Verify equity curve ends at this value

**Expected Result**:
- Last equity point = final equity

**Validation Points**:
- [ ] Final equity matches total return

---

#### TC-DATA-010: Date Range Display Accuracy
**Priority**: Medium
**Steps**:
1. Check backtest start_date and end_date in database
2. Verify displayed date range on list/detail pages

**Expected Result**:
- Displayed dates match database exactly

**Validation Points**:
- [ ] Start date matches
- [ ] End date matches
- [ ] Date format is consistent

---

#### TC-DATA-011: Trade Timestamps Timezone
**Priority**: Medium
**Steps**:
1. Check trade timestamps in database (UTC)
2. Verify displayed timestamps

**Expected Result**:
- Timestamps are consistently formatted

**Validation Points**:
- [ ] Timezone is clear (UTC or local)
- [ ] Format is consistent across UI

---

#### TC-DATA-012: Commission Accuracy
**Priority**: High
**Steps**:
1. Verify commission_amount in trades table
2. Check if included in P&L calculation

**Expected Result**:
- Commissions are deducted from P&L

**Validation Points**:
- [ ] Commission reduces P&L correctly

---

#### TC-DATA-013: Quantity Displayed Correctly
**Priority**: Medium
**Steps**:
1. Check trade quantity in database
2. Verify in trades table and chart markers

**Expected Result**:
- Quantity matches across all displays

**Validation Points**:
- [ ] Trades table shows correct quantity
- [ ] Marker tooltip shows correct quantity

---

#### TC-DATA-014: Status Badge Accuracy
**Priority**: High
**Steps**:
1. Check execution_status in database
2. Verify badge on list and detail pages

**Expected Result**:
- Badge reflects database status

**Validation Points**:
- [ ] "success" status shows green badge
- [ ] "failed" status shows red badge

---

#### TC-DATA-015: Instrument Symbol Consistency
**Priority**: Medium
**Steps**:
1. Check instrument_symbol in database
2. Verify across all pages

**Expected Result**:
- Symbol is consistent everywhere

**Validation Points**:
- [ ] List page shows correct symbol
- [ ] Detail page shows correct symbol
- [ ] Chart data uses correct symbol

---

#### TC-DATA-016: Strategy Name Display
**Priority**: Medium
**Steps**:
1. Check strategy_name in database
2. Verify on UI

**Expected Result**:
- Strategy name matches database

**Validation Points**:
- [ ] Name is human-readable (not module path)

---

#### TC-DATA-017: Execution Duration
**Priority**: Low
**Steps**:
1. Check execution_duration_seconds in database
2. Verify displayed duration

**Expected Result**:
- Duration matches database

**Validation Points**:
- [ ] Duration in seconds is accurate
- [ ] Formatted correctly (e.g., "125.3s")

---

#### TC-DATA-018: Created Timestamp
**Priority**: Medium
**Steps**:
1. Check created_at in database
2. Verify on list page

**Expected Result**:
- Created timestamp is accurate

**Validation Points**:
- [ ] Timestamp matches database
- [ ] Formatted in readable way

---

#### TC-DATA-019: Profit Factor Calculation
**Priority**: Medium
**Steps**:
1. Calculate: total_gross_profit / total_gross_loss
2. Compare with displayed profit factor

**Expected Result**:
- Profit factor is accurate

**Validation Points**:
- [ ] Calculation matches formula

---

#### TC-DATA-020: Volatility Calculation
**Priority**: Medium
**Steps**:
1. Calculate standard deviation of returns
2. Compare with displayed volatility

**Expected Result**:
- Volatility matches calculation

**Validation Points**:
- [ ] Volatility is accurate

---

## 7. UI/UX Validation Checklist

### Visual Design

- [ ] **Color Scheme**: Dark theme with consistent colors
- [ ] **Typography**: Readable fonts, consistent sizes
- [ ] **Spacing**: Adequate padding and margins
- [ ] **Alignment**: Elements are properly aligned
- [ ] **Contrast**: Text readable against backgrounds (WCAG AA)

### Navigation

- [ ] **Nav Bar**: Visible on all pages, active state highlighted
- [ ] **Breadcrumbs**: Show current location
- [ ] **Links**: All links work, no 404s
- [ ] **Back Navigation**: Browser back button works

### Interactive Elements

- [ ] **Buttons**: All clickable, have hover states
- [ ] **Forms**: Inputs accept data, validation works
- [ ] **Dropdowns**: Open/close properly, values selectable
- [ ] **Modals**: Open/close, backdrop dismisses

### Charts

- [ ] **Price Chart**: Renders, interactive, responsive
- [ ] **Equity Chart**: Renders, shows trend clearly
- [ ] **Trade Markers**: Visible, positioned correctly
- [ ] **Tooltips**: Appear on hover, contain correct info
- [ ] **Zoom/Pan**: Smooth, maintains marker accuracy

### Tables

- [ ] **Headers**: Clear, descriptive
- [ ] **Rows**: Alternating colors for readability
- [ ] **Pagination**: Controls visible, functional
- [ ] **Sorting**: Visual indicator on sorted column
- [ ] **Empty State**: Shown when no data

### Responsive Design

- [ ] **Desktop (1920px)**: Layout optimal
- [ ] **Laptop (1366px)**: Layout adjusts properly
- [ ] **Tablet (768px)**: Stacks elements correctly
- [ ] **Mobile (375px)**: No horizontal scroll, readable

### Loading States

- [ ] **Initial Load**: Spinner or skeleton screens
- [ ] **HTMX Updates**: Loading indicators
- [ ] **Chart Loading**: Placeholder before render

### Error States

- [ ] **404 Pages**: User-friendly message
- [ ] **API Errors**: Clear error messages
- [ ] **Form Validation**: Inline error messages
- [ ] **Network Errors**: Retry or guidance

---

## 8. API Testing

### API Test Summary

| Endpoint | Method | Test Status | Priority |
|----------|--------|-------------|----------|
| `/api/timeseries` | GET | Pending | Critical |
| `/api/equity/{run_id}` | GET | Pending | Critical |
| `/api/trades/{run_id}` | GET | Pending | Critical |
| `/api/indicators/{run_id}` | GET | Pending | High |
| `/api/equity-curve/{id}` | GET | Pending | High |
| `/api/statistics/{id}` | GET | Pending | Medium |
| `/api/drawdown/{id}` | GET | Pending | Medium |
| `/api/backtests/{id}/trades` | GET | Pending | High |
| `/api/backtests/{id}/export` | GET | Pending | High |
| `/backtests/{run_id}` | DELETE | Pending | High |
| `/backtests/{run_id}/rerun` | POST | Pending | Medium |

### API Test Script Template

```bash
#!/bin/bash
# API Test Script

BASE_URL="http://127.0.0.1:8000"
RUN_ID="f2377345-2cc6-4a18-9f02-aa1ef642e083"  # Replace with valid UUID
BACKTEST_ID="63"  # Replace with valid integer ID

echo "Testing /api/timeseries..."
curl -s -w "\nStatus: %{http_code}\n" \
  "$BASE_URL/api/timeseries?symbol=SPY.ARCA&start=2024-01-01&end=2024-12-31&timeframe=1_DAY"

echo "\n\nTesting /api/equity/{run_id}..."
curl -s -w "\nStatus: %{http_code}\n" "$BASE_URL/api/equity/$RUN_ID"

echo "\n\nTesting /api/trades/{run_id}..."
curl -s -w "\nStatus: %{http_code}\n" "$BASE_URL/api/trades/$RUN_ID"

echo "\n\nTesting /api/indicators/{run_id}..."
curl -s -w "\nStatus: %{http_code}\n" "$BASE_URL/api/indicators/$RUN_ID"

echo "\n\nTesting /api/equity-curve/{id}..."
curl -s -w "\nStatus: %{http_code}\n" "$BASE_URL/api/equity-curve/$BACKTEST_ID"

echo "\n\nTesting /api/statistics/{id}..."
curl -s -w "\nStatus: %{http_code}\n" "$BASE_URL/api/statistics/$BACKTEST_ID"

echo "\n\nTesting /api/drawdown/{id}..."
curl -s -w "\nStatus: %{http_code}\n" "$BASE_URL/api/drawdown/$BACKTEST_ID"

echo "\n\nTesting /api/backtests/{id}/trades with pagination..."
curl -s -w "\nStatus: %{http_code}\n" \
  "$BASE_URL/api/backtests/$BACKTEST_ID/trades?page=1&page_size=10"

echo "\n\nTesting /api/backtests/{id}/export (CSV)..."
curl -s -w "\nStatus: %{http_code}\n" \
  "$BASE_URL/api/backtests/$BACKTEST_ID/export?format=csv" -o /tmp/trades.csv

echo "\n\nDone!"
```

---

## 9. Data Validation

### Critical Data Points to Verify

1. **Performance Metrics**
   - Total Return %
   - Annualized Return %
   - Sharpe Ratio
   - Max Drawdown %
   - Volatility %

2. **Trading Metrics**
   - Total Trades
   - Win Rate %
   - Average Win $
   - Average Loss $
   - Profit Factor

3. **Trade Data**
   - Entry/Exit Prices
   - Quantities
   - P&L
   - Commissions
   - Timestamps

4. **Chart Data**
   - OHLCV values
   - Trade marker positions
   - Equity curve values
   - Indicator values

### Validation Queries

```sql
-- Verify Sharpe Ratio
SELECT run_id, sharpe_ratio FROM backtest_runs WHERE run_id = '{uuid}';

-- Verify Trade Count
SELECT COUNT(*) FROM trades WHERE backtest_run_id = (
  SELECT id FROM backtest_runs WHERE run_id = '{uuid}'
);

-- Verify Win Rate
SELECT
  COUNT(CASE WHEN profit_loss > 0 THEN 1 END)::float / COUNT(*)::float * 100 as win_rate
FROM trades
WHERE backtest_run_id = (SELECT id FROM backtest_runs WHERE run_id = '{uuid}');

-- Verify Total Return
SELECT total_return_pct FROM backtest_runs WHERE run_id = '{uuid}';
```

---

## 10. Performance Benchmarks

### Page Load Times

| Page | Target | Acceptable | Critical |
|------|--------|------------|----------|
| Dashboard | <300ms | <500ms | <1s |
| Backtest List | <400ms | <700ms | <1.5s |
| Backtest Detail | <600ms | <1s | <2s |

### API Response Times

| Endpoint | Target | Acceptable | Critical |
|----------|--------|------------|----------|
| `/api/timeseries` | <100ms | <200ms | <500ms |
| `/api/equity/{run_id}` | <50ms | <100ms | <300ms |
| `/api/trades/{run_id}` | <100ms | <200ms | <500ms |
| `/api/indicators/{run_id}` | <150ms | <300ms | <700ms |

### Chart Rendering

| Chart Type | Target | Acceptable | Critical |
|------------|--------|------------|----------|
| Price Chart (1000 candles) | <500ms | <1s | <2s |
| Price Chart (5000 candles) | <1s | <2s | <4s |
| Equity Curve | <300ms | <500ms | <1s |

### HTMX Updates

| Action | Target | Acceptable |
|--------|--------|------------|
| Filter Change | <200ms | <400ms |
| Pagination | <150ms | <300ms |
| Sort | <200ms | <400ms |

### Measurement Tools

- Chrome DevTools Performance tab
- Network tab for API timing
- Lighthouse for page performance
- `curl` with `-w "@curl-timing.txt"` for API benchmarks

---

## 11. Browser Compatibility

### Supported Browsers

| Browser | Version | Status | Notes |
|---------|---------|--------|-------|
| Chrome | Latest | Primary | Full support |
| Firefox | Latest | Supported | Full support |
| Safari | Latest | Supported | Test on macOS |
| Edge | Latest | Supported | Chromium-based |
| Mobile Safari | iOS 15+ | Supported | Responsive test |
| Chrome Mobile | Latest | Supported | Responsive test |

### Browser-Specific Tests

#### TC-BROWSER-001: Chrome Compatibility
**Steps**:
1. Open all pages in Chrome
2. Test all interactions

**Validation**:
- [ ] All features work
- [ ] Charts render correctly
- [ ] HTMX functions properly
- [ ] No console errors

#### TC-BROWSER-002: Firefox Compatibility
**Steps**:
1. Open all pages in Firefox
2. Test all interactions

**Validation**:
- [ ] All features work
- [ ] Charts render correctly
- [ ] HTMX functions properly
- [ ] No console errors

#### TC-BROWSER-003: Safari Compatibility
**Steps**:
1. Open all pages in Safari (macOS)
2. Test all interactions

**Validation**:
- [ ] All features work
- [ ] Charts render correctly
- [ ] HTMX functions properly
- [ ] No console errors
- [ ] Date pickers work (Safari-specific)

#### TC-BROWSER-004: Edge Compatibility
**Steps**:
1. Open all pages in Edge
2. Test all interactions

**Validation**:
- [ ] All features work
- [ ] Charts render correctly
- [ ] HTMX functions properly

#### TC-BROWSER-005: Mobile Safari (iOS)
**Steps**:
1. Open pages on iPhone/iPad
2. Test touch interactions

**Validation**:
- [ ] Touch gestures work on charts
- [ ] Buttons are tappable
- [ ] Layout is responsive

#### TC-BROWSER-006: Chrome Mobile (Android)
**Steps**:
1. Open pages on Android device
2. Test touch interactions

**Validation**:
- [ ] Touch gestures work
- [ ] Layout is responsive

---

## 12. Known Issues & Limitations

### Known Gaps (From Codebase Exploration)

1. **Re-run Functionality**: Currently returns HTTP 202 but doesn't actually trigger re-execution
2. **Delete Functionality**: Returns HTTP 200 but deletion not fully implemented
3. **Indicator Overlays**: Deferred to future iteration (spec 010)
4. **Trade Clustering**: High-density trade overlap may occur
5. **Data Caching**: No client-side caching for chart data
6. **Multi-Strategy Indicators**: Only handles Bollinger and SMA strategies

### Deferred Features (Not in Scope)

- ❌ Strategy parameter editing
- ❌ Real-time data integration
- ❌ User authentication
- ❌ Multi-user support
- ❌ Custom indicator addition
- ❌ Trade annotation/notes

### Test Environment Limitations

- Local database required
- Sample data may not cover all edge cases
- No mock IBKR data for live testing

---

## 13. Test Execution Schedule

### Phase 1: Functional Testing (Week 1)
- **Day 1-2**: Dashboard & List page tests
- **Day 3-4**: Detail page tests
- **Day 5**: Chart rendering tests

### Phase 2: API & Integration Testing (Week 2)
- **Day 1-2**: API endpoint tests
- **Day 3**: HTMX interaction tests
- **Day 4**: Data validation tests
- **Day 5**: Performance benchmarks

### Phase 3: Cross-Browser & Final Validation (Week 3)
- **Day 1-2**: Browser compatibility tests
- **Day 3**: Responsive design tests
- **Day 4**: Regression testing
- **Day 5**: Final review & documentation

---

## 14. Sign-off Criteria

### Critical Bugs: 0
- No critical bugs blocking core functionality
- All P0/Critical test cases must pass

### High Priority Bugs: <5
- High priority bugs documented and triaged
- Workarounds identified if not fixed

### Test Coverage: >90%
- At least 90% of test cases executed
- All critical and high priority tests completed

### Performance: Within Targets
- All pages load within acceptable timeframes
- API responses within acceptable limits
- Charts render smoothly

### Browser Compatibility: 100%
- All supported browsers tested
- No major rendering issues

### Documentation: Complete
- All bugs logged
- Test results documented
- Known issues list updated

---

## Appendix A: Test Data Setup

### Sample Backtest Creation

```bash
# Create test backtests with variety
uv run python -m src.cli.main backtest run \
  -s sma_crossover -sym SPY.ARCA \
  -st 2024-01-01 -e 2024-06-30 -ts 100000 -t 1-day

uv run python -m src.cli.main backtest run \
  -s bollinger_reversal -sym AAPL.NASDAQ \
  -st 2024-01-01 -e 2024-12-31 -ts 50000 -t 1-day

# Run one that will fail (invalid symbol)
uv run python -m src.cli.main backtest run \
  -s sma_crossover -sym INVALID.ARCA \
  -st 2024-01-01 -e 2024-06-30 -ts 100000 -t 1-day
```

---

## Appendix B: Bug Report Template

### Bug Report Format

```markdown
# BUG-XXX: [Short Description]

**Priority**: Critical | High | Medium | Low
**Status**: Open | In Progress | Fixed | Won't Fix
**Component**: Dashboard | List | Detail | Charts | API
**Found By**: QA Tester Name
**Date Found**: YYYY-MM-DD

## Description
Clear description of the issue

## Steps to Reproduce
1. Step one
2. Step two
3. Step three

## Expected Result
What should happen

## Actual Result
What actually happens

## Environment
- Browser: Chrome 120
- OS: macOS 14
- Server: localhost:8000
- Database: PostgreSQL 16

## Screenshots
[Attach screenshots if applicable]

## Logs
```
[Paste relevant console errors or server logs]
```

## Suggested Fix
[Optional: Your suggestion for fixing the issue]
```

---

## Appendix C: Test Execution Tracking

### Test Execution Sheet

| Test ID | Test Name | Priority | Status | Tester | Date | Notes |
|---------|-----------|----------|--------|--------|------|-------|
| TC-DASH-001 | Dashboard Loads | Critical | ☐ | - | - | - |
| TC-DASH-002 | Summary Stats | High | ☐ | - | - | - |
| ... | ... | ... | ☐ | - | - | - |

**Legend**:
- ☐ Not Started
- ⏳ In Progress
- ✅ Passed
- ❌ Failed
- ⚠️ Blocked

---

## Document Control

**Approvals**:

| Role | Name | Signature | Date |
|------|------|-----------|------|
| QA Lead | | | |
| Dev Lead | | | |
| Product Owner | | | |

**Change Log**:

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-11-30 | QA Team | Initial draft |

---

**End of Document**
