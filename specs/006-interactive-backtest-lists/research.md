# Research: Interactive Backtest Lists

**Date**: 2025-11-15
**Feature**: Interactive Backtest Lists (Phase 2)

## Research Summary

This document captures technical research and design decisions for implementing interactive filtering, sorting, and URL state persistence for the backtest list page.

---

## 1. HTMX Filter/Sort Patterns

### Decision: Server-side filtering with HTMX form triggers

**Rationale**:
- HTMX's `hx-trigger="change delay:500ms"` provides debounced filtering without custom JavaScript
- Server-side filtering leverages existing PostgreSQL indexes
- URL state synchronization via `hx-push-url="true"` for shareable links
- Follows existing pattern in `backtests.py` fragment endpoint

**Pattern Selected**:
```html
<form hx-get="/backtests/fragment"
      hx-target="#backtest-table"
      hx-trigger="change delay:300ms, submit"
      hx-push-url="true">
  <!-- Filter controls -->
</form>
```

**Alternatives Considered**:
- Client-side filtering with Alpine.js - Rejected: Would require loading all data upfront, poor performance with 10,000+ records
- Full page reload on filter change - Rejected: Poor UX, slower than HTMX partial updates
- Custom JavaScript fetch handlers - Rejected: HTMX already provides this functionality

---

## 2. URL State Management

### Decision: Query string parameters with server-side restoration

**Rationale**:
- FastAPI automatically parses query parameters into typed Pydantic models
- Browser history integration via `hx-push-url="true"`
- Bookmarkable/shareable URLs with all filter state
- Server restores state on page load from URL parameters

**URL Schema**:
```
/backtests?strategy=SMA+Crossover&instrument=AAPL&status=success&sort=sharpe&order=desc&page=1
```

**Parameter Mapping**:
- `strategy`: Filter by strategy name (optional)
- `instrument`: Filter by instrument symbol (optional)
- `date_from`: Start date filter (ISO format, optional)
- `date_to`: End date filter (ISO format, optional)
- `status`: Filter by execution status (optional)
- `sort`: Sort column name (default: created_at)
- `order`: Sort direction (asc/desc, default: desc)
- `page`: Current page number (default: 1)

**Alternatives Considered**:
- Local storage for filter state - Rejected: Not shareable, doesn't survive URL sharing
- Hash fragments (#filter=...) - Rejected: Not sent to server, requires client-side parsing
- Session cookies - Rejected: Not reflected in URL, not shareable

---

## 3. Sortable Column Headers

### Decision: Clickable headers with direction toggle via HTMX

**Rationale**:
- Each column header makes an HTMX GET request with sort parameters
- Toggle logic: Click same column toggles asc/desc, click different column sorts desc first
- Visual indicator (arrow icons) shows current sort state
- Preserves all other filter parameters

**Pattern Selected**:
```html
<th hx-get="/backtests/fragment?sort=sharpe&order=desc&strategy={{ current_strategy }}"
    hx-target="#backtest-table"
    hx-push-url="true"
    class="cursor-pointer">
  Sharpe Ratio
  {% if sort_column == 'sharpe' %}
    {% if sort_order == 'asc' %}↑{% else %}↓{% endif %}
  {% endif %}
</th>
```

**Alternatives Considered**:
- JavaScript-only sorting - Rejected: Only works for current page, not entire dataset
- CSS-only sorting - Not feasible for server-side data
- Click handler with custom fetch - Rejected: HTMX provides this natively

---

## 4. Filter State Pydantic Model

### Decision: Single FilterState model with defaults

**Rationale**:
- Pydantic handles validation and defaults automatically
- Optional fields with None defaults for inactive filters
- Enum for status to ensure valid values
- Easy serialization to URL parameters

**Model Design**:
```python
from enum import Enum
from datetime import date
from typing import Optional
from pydantic import BaseModel, Field

class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"

class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"

class FilterState(BaseModel):
    strategy: Optional[str] = None
    instrument: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    status: Optional[ExecutionStatus] = None
    sort: str = "created_at"
    order: SortOrder = SortOrder.DESC
    page: int = Field(default=1, ge=1)
```

**Alternatives Considered**:
- Separate models for each filter type - Rejected: Over-engineering, harder to serialize
- Dictionary-based state - Rejected: No type safety, no validation
- Class with manual validation - Rejected: Pydantic already provides this

---

## 5. Database Query Building

### Decision: SQLAlchemy query builder with conditional filters

**Rationale**:
- Build query dynamically based on active filters
- Leverage existing indexes (strategy_name, created_at, instrument_symbol)
- Use SQLAlchemy's `case` for null-safe sorting
- Pagination applied after filtering and sorting

**Query Pattern**:
```python
async def get_filtered_backtests(
    self,
    filter_state: FilterState
) -> tuple[list[BacktestRun], int]:
    query = select(BacktestRun)

    # Apply filters conditionally
    if filter_state.strategy:
        query = query.where(BacktestRun.strategy_name == filter_state.strategy)
    if filter_state.instrument:
        query = query.where(BacktestRun.instrument_symbol.ilike(f"%{filter_state.instrument}%"))
    if filter_state.status:
        query = query.where(BacktestRun.execution_status == filter_state.status.value)

    # Date range filtering
    if filter_state.date_from:
        query = query.where(BacktestRun.created_at >= filter_state.date_from)
    if filter_state.date_to:
        query = query.where(BacktestRun.created_at <= filter_state.date_to)

    # Apply sorting
    sort_column = getattr(BacktestRun, filter_state.sort)
    if filter_state.order == SortOrder.DESC:
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    # Get total count before pagination
    count_query = select(func.count()).select_from(query.subquery())

    # Apply pagination
    offset = (filter_state.page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    return results, total_count
```

**Alternatives Considered**:
- Raw SQL queries - Rejected: Less maintainable, no type safety
- ORM-level filtering only - Could work, but query builder is more flexible
- Separate filter functions - Rejected: DRY violation, harder to compose

---

## 6. Instrument Autocomplete

### Decision: Server-side autocomplete with text input debouncing

**Rationale**:
- HTMX can trigger on input with delay for debouncing
- Server returns list of matching symbols from database
- Graceful fallback to manual entry if service unavailable
- Low complexity, no external autocomplete library needed

**Pattern Selected**:
```html
<input type="text"
       name="instrument"
       list="instrument-suggestions"
       hx-get="/backtests/instruments"
       hx-trigger="input changed delay:300ms"
       hx-target="#instrument-suggestions"
       placeholder="Type symbol...">
<datalist id="instrument-suggestions">
  <!-- Populated by HTMX -->
</datalist>
```

**Alternatives Considered**:
- External autocomplete library (select2, etc.) - Rejected: Additional dependency, more JavaScript
- Pre-loaded dropdown with all symbols - Rejected: May have many symbols, slow initial load
- No autocomplete - Acceptable fallback, but hurts UX for spec requirement FR-002

---

## 7. Date Range Validation

### Decision: Client-side HTML5 validation + server-side Pydantic validation

**Rationale**:
- HTML5 `type="date"` provides browser-native date picker
- Client-side validation via JavaScript for immediate feedback
- Server-side Pydantic validator ensures end_date > start_date
- Error messages displayed inline near the date inputs

**Validation Pattern**:
```python
from pydantic import model_validator

class FilterState(BaseModel):
    # ... other fields ...

    @model_validator(mode='after')
    def validate_date_range(self) -> 'FilterState':
        if self.date_from and self.date_to:
            if self.date_to < self.date_from:
                raise ValueError("End date must be after start date")
        return self
```

**Alternatives Considered**:
- Client-only validation - Rejected: Can be bypassed, not secure
- Server-only validation - Works, but poor UX without immediate feedback
- Date picker library - Rejected: HTML5 date input is sufficient for this use case

---

## 8. Performance Optimization

### Decision: Leverage existing database indexes

**Rationale**:
- Existing indexes from Phase 1 (004-postgresql-metadata-storage):
  - `idx_backtest_runs_created_id` on (created_at, id)
  - `idx_backtest_runs_strategy_created_id` on (strategy_name, created_at, id)
- Add new index for instrument filtering
- Query planner will use appropriate index based on filter combination

**Additional Indexes Needed**:
```sql
-- For instrument filtering
CREATE INDEX idx_backtest_runs_instrument ON backtest_runs(instrument_symbol);

-- For status filtering
CREATE INDEX idx_backtest_runs_status ON backtest_runs(execution_status);
```

**Alternatives Considered**:
- Full table scans - Rejected: Poor performance at scale
- Application-level caching - Not needed yet, database is fast enough
- Materialized views - Over-engineering for current requirements

---

## 9. Clear Filters Implementation

### Decision: Reset all parameters to defaults via dedicated action

**Rationale**:
- "Clear Filters" button resets to default state
- Preserves current sort order if desired (optional)
- URL updates to remove all filter parameters
- Simple server-side handling - just use default FilterState

**Pattern**:
```html
<button hx-get="/backtests/fragment"
        hx-target="#backtest-table"
        hx-push-url="/backtests"
        type="button">
  Clear Filters
</button>
```

**Alternatives Considered**:
- JavaScript form reset - Rejected: Doesn't update URL or trigger server request
- Separate endpoint for reset - Rejected: Not needed, just use defaults
- Client-side state clearing - Rejected: Server is source of truth

---

## 10. Loading State Indicator

### Decision: HTMX built-in htmx-indicator class

**Rationale**:
- HTMX automatically manages `.htmx-indicator` visibility during requests
- No custom JavaScript needed
- Shows spinner or "Loading..." text during filter operations
- Automatically hidden when request completes

**Pattern**:
```html
<div class="htmx-indicator">
  <span>Loading...</span>
</div>

<style>
.htmx-indicator {
  display: none;
}
.htmx-request .htmx-indicator {
  display: inline;
}
</style>
```

**Alternatives Considered**:
- Custom loading state management - Rejected: HTMX provides this
- Full page loading overlay - Rejected: Only table updates, not full page
- No loading indicator - Rejected: Poor UX, spec requirement FR-015

---

## Summary

All technical decisions align with:
- **KISS/YAGNI**: Using HTMX's built-in features instead of custom solutions
- **TDD**: All components can be unit tested independently
- **Type Safety**: Pydantic models for validation and serialization
- **Performance**: Database indexes and server-side pagination
- **Existing Patterns**: Following established Phase 1 architecture

No NEEDS CLARIFICATION items remain. Ready for Phase 1 design artifacts.
