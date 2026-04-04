# Layout Design: Interactive Backtest Lists

**Date**: 2025-11-15
**Purpose**: Visual layout specification for enhanced backtest list page with filtering, sorting, and pagination

---

## Page Structure Overview

```
+----------------------------------------------------------+
|  HEADER (existing nav)                                   |
+----------------------------------------------------------+
|  BREADCRUMBS: Dashboard > Backtests                      |
+----------------------------------------------------------+
|                                                          |
|  CONTENT AREA                                            |
|  +------------------------------------------------------+|
|  |  PAGE HEADER                                         ||
|  |  "Backtest History"                 "X total results"||
|  +------------------------------------------------------+|
|  |                                                      ||
|  |  FILTER PANEL (NEW)                                  ||
|  |  +--------------------------------------------------+||
|  |  | Strategy: [Dropdown ▼]  Instrument: [Input___]  |||
|  |  | Date From: [____]  Date To: [____]  Status: [▼] |||
|  |  | [Apply Filters]               [Clear Filters]   |||
|  |  +--------------------------------------------------+||
|  |                                                      ||
|  |  LOADING INDICATOR (NEW)                             ||
|  |  [Spinner + "Loading backtests..."]                  ||
|  |                                                      ||
|  |  RESULTS TABLE                                       ||
|  |  +--------------------------------------------------+||
|  |  | SORTABLE HEADERS (ENHANCED)                      |||
|  |  | Run ID | Strategy ▼ | Symbol | Date | Return... |||
|  |  |--------------------------------------------------|||
|  |  | [Data rows with click-to-navigate]               |||
|  |  |--------------------------------------------------|||
|  |  | PAGINATION CONTROLS (ENHANCED)                   |||
|  |  | "Page 1 of 5"  [<] [1] [2] [3] [4] [5] [>]     |||
|  |  +--------------------------------------------------+||
|  |                                                      ||
|  |  EMPTY STATE (shown when no results)                 ||
|  |  +--------------------------------------------------+||
|  |  | "No backtests found matching your filters"       |||
|  |  | [Adjust your filters or clear all filters]       |||
|  |  +--------------------------------------------------+||
|  +------------------------------------------------------+|
|                                                          |
+----------------------------------------------------------+
|  FOOTER (existing)                                       |
+----------------------------------------------------------+
```

---

## 1. Filter Panel Layout

### Position
- Above the results table
- Below the page header
- Full width within content area

### Components

```
+--------------------------------------------------------------------------+
| FILTER CONTROLS                                         [Clear Filters]  |
|                                                                          |
| Strategy:                    Instrument:              Status:            |
| [All Strategies        ▼]    [_________]             [All Statuses  ▼]  |
|                                                                          |
| Date From:                   Date To:                                    |
| [YYYY-MM-DD      ]           [YYYY-MM-DD      ]                         |
|                                                                          |
+--------------------------------------------------------------------------+
```

### Filter Control Specifications

#### Strategy Dropdown
- **Type**: `<select>` with options
- **Default**: "All Strategies" (no filter applied)
- **Options**: Dynamically populated from database
- **Behavior**:
  - On change, triggers HTMX request
  - Updates URL parameter: `?strategy=SMA+Crossover`
  - Auto-resets to page 1

#### Instrument Input
- **Type**: `<input type="text">` with `<datalist>` for autocomplete
- **Default**: Empty (no filter applied)
- **Placeholder**: "e.g., AAPL, SPY..."
- **Behavior**:
  - Autocomplete suggestions appear after 2 characters
  - Partial match (case-insensitive)
  - Updates URL parameter: `?instrument=AAPL`
  - Debounced (300ms) to avoid excessive requests

#### Date Range Inputs
- **Type**: `<input type="date">`
- **Default**: Empty (no filter applied)
- **Behavior**:
  - Client-side validation: end >= start
  - Error message displayed below inputs if invalid
  - Updates URL parameters: `?date_from=2024-01-01&date_to=2024-01-31`

#### Status Dropdown
- **Type**: `<select>` with options
- **Default**: "All Statuses" (no filter applied)
- **Options**:
  - All Statuses
  - Success
  - Failed
- **Behavior**:
  - On change, triggers HTMX request
  - Updates URL parameter: `?status=success`

#### Clear Filters Button
- **Type**: `<button>`
- **Label**: "Clear Filters"
- **Position**: Top-right of filter panel
- **Behavior**:
  - Resets all filters to defaults
  - Clears URL parameters
  - Preserves current sort order

### HTMX Integration

```html
<form hx-get="/backtests/fragment"
      hx-target="#backtest-table"
      hx-swap="innerHTML"
      hx-push-url="true"
      hx-indicator="#loading-indicator">
  <!-- Filter inputs here -->
</form>
```

---

## 2. Sortable Table Headers

### Visual Design

```
+------------+-------------+---------+------------+---------+--------+---------+---------+
| Run ID     | Strategy ▼  | Symbol  | Date Range | Return  | Sharpe | Max DD  | Status  | Created  |
+------------+-------------+---------+------------+---------+--------+---------+---------+
              ^
              |
         Sort indicator (shows current sort + direction)
```

### Header States

1. **Not sorted (neutral)**
   - No arrow indicator
   - Hover: Shows cursor-pointer, subtle background change
   - Example: `Symbol`

2. **Sorted descending (active)**
   - Down arrow: `▼` or `↓`
   - Highlighted background
   - Example: `Strategy ▼`

3. **Sorted ascending (active)**
   - Up arrow: `▲` or `↑`
   - Highlighted background
   - Example: `Return ↑`

### Click Behavior

1. Click on unsorted column → Sort descending
2. Click on descending column → Toggle to ascending
3. Click on ascending column → Toggle to descending
4. Any sort change → Reset to page 1

### Sortable Columns

| Column | Sort Key | Default Direction | Database Field |
|--------|----------|-------------------|----------------|
| Strategy | `strategy_name` | DESC | `backtest_runs.strategy_name` |
| Symbol | `instrument_symbol` | DESC | `backtest_runs.instrument_symbol` |
| Return | `total_return` | DESC (best first) | `performance_metrics.total_return` |
| Sharpe | `sharpe_ratio` | DESC (best first) | `performance_metrics.sharpe_ratio` |
| Max DD | `max_drawdown` | ASC (worst first) | `performance_metrics.max_drawdown` |
| Status | `execution_status` | DESC | `backtest_runs.execution_status` |
| Created | `created_at` | DESC (newest first) | `backtest_runs.created_at` |

**Note**: Run ID and Date Range are NOT sortable (composite fields).

### HTMX Header Template

```html
<th scope="col" class="px-6 py-3 text-left cursor-pointer hover:bg-slate-700">
  <a hx-get="/backtests/fragment?sort=sharpe_ratio&order=desc&strategy=..."
     hx-target="#backtest-table"
     hx-swap="innerHTML"
     hx-push-url="true"
     hx-indicator="#loading-indicator"
     class="flex items-center space-x-1">
    <span>Sharpe</span>
    <span class="text-blue-400">▼</span>  <!-- Sort indicator -->
  </a>
</th>
```

---

## 3. Enhanced Pagination Controls

### Visual Design

```
+--------------------------------------------------------------------------+
| Showing 21-40 of 156 results                                             |
|                                                                          |
|                    [<] [1] [2] [3] ... [7] [8] [>]                       |
|                         ^                                                |
|                     Current page (highlighted)                           |
+--------------------------------------------------------------------------+
```

### Components

1. **Results Summary**
   - Format: "Showing X-Y of Z results"
   - Example: "Showing 21-40 of 156 results"
   - Reflects filtered count, not total database count

2. **Previous Button**
   - Label: "Previous" or `<` or `←`
   - Disabled on first page (grayed out, not clickable)
   - Active: Enabled with hover effect

3. **Page Numbers**
   - Show first page, current page ± 2, last page
   - Ellipsis (...) for gaps
   - Current page: Highlighted background (blue)
   - Other pages: Hover effect

4. **Next Button**
   - Label: "Next" or `>` or `→`
   - Disabled on last page
   - Active: Enabled with hover effect

### Pagination Logic

For 156 results with page size 20:
- Total pages: 8
- Page 1: Show [1] [2] [3] ... [8]
- Page 3: Show [1] [2] [3] [4] [5] ... [8]
- Page 5: Show [1] ... [3] [4] [5] [6] [7] [8]
- Page 8: Show [1] ... [6] [7] [8]

### State Preservation

All pagination links must include current filter and sort state:
```
/backtests/fragment?page=3&strategy=SMA+Crossover&sort=sharpe_ratio&order=desc
```

---

## 4. Loading Indicator

### Position
- Fixed position over the table area OR
- Inline at top of table
- Semi-transparent overlay

### Visual

```html
<div id="loading-indicator" class="htmx-indicator">
  <div class="absolute inset-0 bg-slate-900/50 flex items-center justify-center">
    <div class="flex items-center space-x-2">
      <svg class="animate-spin h-5 w-5 text-blue-500">...</svg>
      <span class="text-slate-200">Loading...</span>
    </div>
  </div>
</div>
```

### Behavior
- Appears automatically when HTMX request starts
- Disappears when response received
- Uses HTMX `hx-indicator` attribute

---

## 5. Empty State Messages

### No Backtests (Database Empty)

```
+--------------------------------------------------------------------------+
|                                                                          |
|                         No Backtests Yet                                 |
|                                                                          |
|    You haven't run any backtests yet. Run your first backtest to see   |
|    results here.                                                         |
|                                                                          |
|    Run your first backtest:                                              |
|    $ ntrader backtest run --strategy sma_crossover --symbol AAPL         |
|                                                                          |
+--------------------------------------------------------------------------+
```

### No Matching Results (Filters Applied)

```
+--------------------------------------------------------------------------+
|                                                                          |
|              No backtests match your current filters                     |
|                                                                          |
|    Try adjusting your search criteria:                                   |
|    • Remove some filters                                                 |
|    • Broaden the date range                                              |
|    • Select a different strategy                                         |
|                                                                          |
|                          [Clear All Filters]                             |
|                                                                          |
+--------------------------------------------------------------------------+
```

---

## 6. URL State Persistence

### URL Structure

Base: `/backtests`

With filters:
```
/backtests?strategy=SMA+Crossover&instrument=AAPL&date_from=2024-01-01&date_to=2024-12-31&status=success&sort=sharpe_ratio&order=desc&page=2
```

### Parameter Encoding

| Parameter | Type | Example |
|-----------|------|---------|
| strategy | string (URL encoded) | `SMA%20Crossover` |
| instrument | string | `AAPL` |
| date_from | ISO date | `2024-01-01` |
| date_to | ISO date | `2024-12-31` |
| status | enum | `success` or `failed` |
| sort | enum | `created_at`, `sharpe_ratio`, etc. |
| order | enum | `asc` or `desc` |
| page | int | `2` |

### Behavior

1. **Page Load**: Parse URL params → Apply to FilterState → Render filtered view
2. **Filter Change**: Update FilterState → Serialize to URL → Push to history
3. **Browser Back/Forward**: Detect URL change → Re-apply filters
4. **Share Link**: Copy URL → Recipient sees exact same filtered view
5. **Bookmark**: Save URL → Return to exact filtered view

---

## 7. Responsive Design Considerations

### Desktop (>1024px)
- Full filter panel with inline controls
- All table columns visible
- Pagination numbers visible

### Tablet (768px - 1024px)
- Filter panel stacks vertically
- Table scrolls horizontally
- Pagination simplified

### Mobile (<768px)
- Filter panel collapses to expandable section
- Critical columns only (Strategy, Return, Status)
- Previous/Next buttons only (no page numbers)

---

## 8. Accessibility Requirements

1. **Keyboard Navigation**
   - Tab through all filter controls
   - Enter to apply filters
   - Arrow keys for dropdowns
   - Sortable headers are focusable

2. **Screen Reader Support**
   - ARIA labels on sort indicators: `aria-label="Sort by Sharpe ratio, currently descending"`
   - Live region for filter results: `aria-live="polite"`
   - Form labels associated with inputs

3. **Color Contrast**
   - All text meets WCAG AA standards
   - Sort indicators visible (not color-only)
   - Disabled states clearly distinguishable

---

## 9. Implementation Checklist

### list.html (Main Page)
- [ ] Add filter panel above table
- [ ] Include loading indicator div
- [ ] Add HTMX form wrapper
- [ ] Pass available_strategies to template
- [ ] Pass available_instruments to template
- [ ] Handle empty state for filtered results

### list_fragment.html (HTMX Partial)
- [ ] Make column headers clickable with hx-get
- [ ] Add sort indicators to headers
- [ ] Include all filter params in pagination links
- [ ] Show results count (filtered total)
- [ ] Enhanced pagination with page numbers
- [ ] Add hx-push-url="true" for URL updates

### Styling (Tailwind CSS)
- [ ] Filter panel: `bg-slate-900 rounded-lg p-4`
- [ ] Active sort header: `bg-slate-700`
- [ ] Sort indicator: `text-blue-400`
- [ ] Current page: `bg-blue-600 text-white`
- [ ] Loading overlay: `bg-slate-900/50`
- [ ] Error message: `text-red-400`

---

## 10. Example Complete Page

```html
{% extends "base.html" %}

{% block content %}
<div class="space-y-6">
    <!-- Page Header -->
    <div class="flex justify-between items-center">
        <h1 class="text-2xl font-semibold">Backtest History</h1>
        <span class="text-slate-400">
            {{ filter_state.total_count }} results
        </span>
    </div>

    <!-- Filter Panel -->
    <div class="bg-slate-900 rounded-lg p-4 border border-slate-700">
        <form hx-get="/backtests/fragment"
              hx-target="#backtest-table"
              hx-swap="innerHTML"
              hx-push-url="true"
              hx-indicator="#loading-indicator"
              hx-trigger="change, submit">

            <div class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-4">
                <!-- Strategy Filter -->
                <div>
                    <label class="block text-sm text-slate-400 mb-1">Strategy</label>
                    <select name="strategy" class="w-full bg-slate-800 border-slate-600 rounded">
                        <option value="">All Strategies</option>
                        {% for strategy in available_strategies %}
                        <option value="{{ strategy }}"
                                {% if filter_state.strategy == strategy %}selected{% endif %}>
                            {{ strategy }}
                        </option>
                        {% endfor %}
                    </select>
                </div>

                <!-- Instrument Filter -->
                <div>
                    <label class="block text-sm text-slate-400 mb-1">Instrument</label>
                    <input type="text"
                           name="instrument"
                           value="{{ filter_state.instrument or '' }}"
                           placeholder="e.g., AAPL"
                           list="instruments-list"
                           class="w-full bg-slate-800 border-slate-600 rounded">
                    <datalist id="instruments-list">
                        {% for inst in available_instruments %}
                        <option value="{{ inst }}">
                        {% endfor %}
                    </datalist>
                </div>

                <!-- Date From -->
                <div>
                    <label class="block text-sm text-slate-400 mb-1">From Date</label>
                    <input type="date"
                           name="date_from"
                           value="{{ filter_state.date_from or '' }}"
                           class="w-full bg-slate-800 border-slate-600 rounded">
                </div>

                <!-- Date To -->
                <div>
                    <label class="block text-sm text-slate-400 mb-1">To Date</label>
                    <input type="date"
                           name="date_to"
                           value="{{ filter_state.date_to or '' }}"
                           class="w-full bg-slate-800 border-slate-600 rounded">
                </div>

                <!-- Status Filter -->
                <div>
                    <label class="block text-sm text-slate-400 mb-1">Status</label>
                    <select name="status" class="w-full bg-slate-800 border-slate-600 rounded">
                        <option value="">All Statuses</option>
                        <option value="success" {% if filter_state.status == 'success' %}selected{% endif %}>
                            Success
                        </option>
                        <option value="failed" {% if filter_state.status == 'failed' %}selected{% endif %}>
                            Failed
                        </option>
                    </select>
                </div>
            </div>

            <!-- Hidden sort params to preserve on filter change -->
            <input type="hidden" name="sort" value="{{ filter_state.sort }}">
            <input type="hidden" name="order" value="{{ filter_state.order }}">

            <div class="mt-4 flex justify-end">
                <button type="button"
                        hx-get="/backtests/fragment"
                        hx-target="#backtest-table"
                        hx-swap="innerHTML"
                        hx-push-url="true"
                        class="text-slate-400 hover:text-white mr-4">
                    Clear Filters
                </button>
            </div>
        </form>
    </div>

    <!-- Loading Indicator -->
    <div id="loading-indicator" class="htmx-indicator fixed inset-0 bg-slate-900/50 z-50 flex items-center justify-center">
        <div class="bg-slate-800 rounded-lg p-4 flex items-center space-x-3">
            <svg class="animate-spin h-5 w-5 text-blue-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-slate-200">Loading backtests...</span>
        </div>
    </div>

    <!-- Backtest Table -->
    <div id="backtest-table">
        {% include "backtests/list_fragment.html" %}
    </div>
</div>
{% endblock %}
```

---

## Summary

This layout design provides:
1. **Intuitive filtering** with multiple criteria
2. **Clear visual feedback** for sorting and current state
3. **Responsive pagination** with state preservation
4. **URL-based state** for bookmarking and sharing
5. **Loading indicators** for smooth UX during async operations
6. **Accessible controls** following WCAG guidelines
7. **Progressive enhancement** using HTMX for partial updates

The design builds on the existing Phase 1 structure while adding comprehensive interactivity without full page reloads.
