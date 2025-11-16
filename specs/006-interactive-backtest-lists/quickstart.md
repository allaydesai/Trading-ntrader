# Quickstart: Interactive Backtest Lists

**Feature**: Phase 2 - Interactive Lists
**Branch**: `006-interactive-backtest-lists`
**Time to First Value**: ~30 minutes (TDD: test first, then implementation)

## Prerequisites

- Phase 1 (Web UI Foundation) is complete and functional
- PostgreSQL database running with backtest data
- Development environment set up with `uv sync`

## Quick Setup

```bash
# Ensure you're on the feature branch
git checkout 006-interactive-backtest-lists

# Sync dependencies (no new packages needed)
uv sync

# Run database migrations (for new indexes)
uv run alembic upgrade head

# Start the development server
uv run uvicorn src.api.web:app --reload --port 8000

# Open in browser
open http://localhost:8000/backtests
```

## Implementation Order (TDD)

### Step 1: Filter State Models (30 min)

**Test First** (`tests/api/models/test_filter_models.py`):
```python
def test_filter_state_default_values():
    """Default FilterState has expected defaults."""
    state = FilterState()
    assert state.sort == SortColumn.CREATED_AT
    assert state.order == SortOrder.DESC
    assert state.page == 1
    assert state.strategy is None

def test_filter_state_date_validation_fails():
    """End date before start date raises error."""
    with pytest.raises(ValidationError):
        FilterState(
            date_from=date(2024, 12, 31),
            date_to=date(2024, 1, 1)
        )
```

**Implement** (`src/api/models/filter_models.py`):
- Create SortOrder, ExecutionStatus, SortColumn enums
- Create FilterState Pydantic model with validation
- Add to_query_params(), with_page(), with_sort(), clear_filters() methods

---

### Step 2: Repository Filter Queries (45 min)

**Test First** (`tests/db/repositories/test_backtest_repository_filters.py`):
```python
async def test_filter_by_strategy(repository, sample_backtests):
    """Filter returns only matching strategy."""
    state = FilterState(strategy="SMA Crossover")
    results, count = await repository.get_filtered_backtests(state)
    assert all(r.strategy_name == "SMA Crossover" for r in results)

async def test_sort_by_sharpe_descending(repository, sample_backtests):
    """Results sorted by Sharpe ratio descending."""
    state = FilterState(sort=SortColumn.SHARPE_RATIO, order=SortOrder.DESC)
    results, count = await repository.get_filtered_backtests(state)
    sharpes = [r.metrics.sharpe_ratio for r in results if r.metrics]
    assert sharpes == sorted(sharpes, reverse=True)
```

**Implement** (`src/db/repositories/backtest_repository.py`):
```python
async def get_filtered_backtests(
    self,
    filter_state: FilterState,
    page_size: int = 20
) -> tuple[list[BacktestRun], int]:
    """Get filtered, sorted, paginated backtests."""
    # Build query with conditional WHERE clauses
    # Apply sorting with JOIN for metrics columns
    # Return results and total count
```

---

### Step 3: Service Layer (30 min)

**Test First** (`tests/services/test_backtest_query_filters.py`):
```python
async def test_get_filtered_list_page(service, sample_backtests):
    """Service returns FilteredBacktestListPage."""
    state = FilterState(strategy="SMA Crossover")
    page = await service.get_filtered_backtest_list_page(state)
    assert isinstance(page, FilteredBacktestListPage)
    assert page.filter_state == state
    assert len(page.available_strategies) > 0
```

**Implement** (`src/services/backtest_query.py`):
```python
async def get_filtered_backtest_list_page(
    self,
    filter_state: FilterState
) -> FilteredBacktestListPage:
    """Get paginated backtest list with filter context."""
    # Call repository
    # Map to view models
    # Get available strategies/instruments for dropdowns
    # Return FilteredBacktestListPage
```

---

### Step 4: UI Endpoints (45 min)

**Test First** (`tests/api/ui/test_backtests_filters.py`):
```python
async def test_filter_preserves_state_in_template(client, sample_backtests):
    """Filter state is passed to template context."""
    response = await client.get("/backtests?strategy=SMA+Crossover")
    assert response.status_code == 200
    assert "SMA Crossover" in response.text
    # Verify selected in dropdown

async def test_fragment_returns_table_only(client, sample_backtests):
    """Fragment endpoint returns only table HTML."""
    response = await client.get("/backtests/fragment?page=1")
    assert response.status_code == 200
    assert "<table" in response.text
    assert "<html>" not in response.text
```

**Implement** (`src/api/ui/backtests.py`):
```python
@router.get("/", response_class=HTMLResponse)
async def backtest_list(
    request: Request,
    service: BacktestService,
    strategy: Optional[str] = None,
    instrument: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[ExecutionStatus] = None,
    sort: SortColumn = SortColumn.CREATED_AT,
    order: SortOrder = SortOrder.DESC,
    page: int = 1,
    page_size: int = 20,
) -> HTMLResponse:
    """Render filtered backtest list page."""
    filter_state = FilterState(
        strategy=strategy,
        instrument=instrument,
        date_from=date_from,
        date_to=date_to,
        status=status,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size
    )
    list_page = await service.get_filtered_backtest_list_page(filter_state)
    # Render template with filter_state in context
```

---

### Step 5: Templates (60 min)

**Manual Testing**: No unit tests for Jinja templates, test via integration.

**Implement** (`templates/backtests/list.html`):
```html
<!-- Add filter controls form -->
<form hx-get="/backtests/fragment"
      hx-target="#backtest-table"
      hx-push-url="true"
      hx-trigger="change delay:300ms, submit">

  <!-- Strategy dropdown -->
  <select name="strategy">
    <option value="">All Strategies</option>
    {% for s in list_page.available_strategies %}
      <option value="{{ s }}" {% if list_page.filter_state.strategy == s %}selected{% endif %}>
        {{ s }}
      </option>
    {% endfor %}
  </select>

  <!-- Instrument input with autocomplete -->
  <input type="text" name="instrument"
         value="{{ list_page.filter_state.instrument or '' }}"
         list="instrument-list"
         placeholder="Symbol...">

  <!-- Date range inputs -->
  <input type="date" name="date_from"
         value="{{ list_page.filter_state.date_from or '' }}">
  <input type="date" name="date_to"
         value="{{ list_page.filter_state.date_to or '' }}">

  <!-- Status dropdown -->
  <select name="status">
    <option value="">All Statuses</option>
    <option value="success" {% if list_page.filter_state.status == 'success' %}selected{% endif %}>Success</option>
    <option value="failed" {% if list_page.filter_state.status == 'failed' %}selected{% endif %}>Failed</option>
  </select>

  <!-- Hidden sort fields (maintained across filter changes) -->
  <input type="hidden" name="sort" value="{{ list_page.filter_state.sort }}">
  <input type="hidden" name="order" value="{{ list_page.filter_state.order }}">

  <button type="submit">Apply Filters</button>
  <button type="button"
          hx-get="/backtests/fragment"
          hx-target="#backtest-table"
          hx-push-url="/backtests">
    Clear Filters
  </button>
</form>

<!-- Loading indicator -->
<div class="htmx-indicator">Loading...</div>

<!-- Table container -->
<div id="backtest-table">
  {% include "backtests/list_fragment.html" %}
</div>
```

**Implement** (`templates/backtests/list_fragment.html`):
```html
<!-- Sortable column headers -->
<th hx-get="/backtests/fragment?sort=sharpe_ratio&order={{ 'asc' if list_page.filter_state.sort == 'sharpe_ratio' and list_page.filter_state.order == 'desc' else 'desc' }}&strategy={{ list_page.filter_state.strategy or '' }}&page=1"
    hx-target="#backtest-table"
    hx-push-url="true"
    class="cursor-pointer">
  Sharpe
  {% if list_page.filter_state.sort == 'sharpe_ratio' %}
    {{ '↑' if list_page.filter_state.order == 'asc' else '↓' }}
  {% endif %}
</th>
```

---

### Step 6: Database Migration (15 min)

**Create migration**:
```bash
uv run alembic revision -m "add_filter_indexes"
```

**Implement** migration:
```python
def upgrade() -> None:
    op.create_index('idx_backtest_runs_instrument', 'backtest_runs', ['instrument_symbol'])
    op.create_index('idx_backtest_runs_status', 'backtest_runs', ['execution_status'])

def downgrade() -> None:
    op.drop_index('idx_backtest_runs_status')
    op.drop_index('idx_backtest_runs_instrument')
```

---

## Verification Checklist

### Functional Tests
- [ ] Filter by strategy shows only matching backtests
- [ ] Filter by instrument (partial match) works
- [ ] Date range filtering works
- [ ] Status filtering works
- [ ] Sorting by any column works
- [ ] Sort direction toggles on repeat click
- [ ] Pagination works with filters applied
- [ ] URL updates with filter parameters
- [ ] Shared URL restores filter state
- [ ] Clear filters resets to defaults
- [ ] Empty results show helpful message
- [ ] Loading indicator appears during requests

### Performance Tests
- [ ] Filter operations complete in <200ms
- [ ] Page loads with 20 results in <300ms
- [ ] System handles 10,000+ backtests efficiently

### Code Quality
- [ ] All tests pass: `uv run pytest`
- [ ] Type checking: `uv run mypy src/`
- [ ] Linting: `uv run ruff check src/`
- [ ] Formatting: `uv run ruff format src/`
- [ ] Coverage >80% on new code

---

## Common Issues

### 1. HTMX not updating URL
**Solution**: Ensure `hx-push-url="true"` is on the form element.

### 2. Sort columns not working for metrics
**Solution**: Join with PerformanceMetrics table in repository query.

### 3. Filter state lost on page change
**Solution**: Include all filter params as hidden inputs in pagination links.

### 4. Date validation not triggering
**Solution**: Use Pydantic `model_validator(mode='after')` for cross-field validation.

---

## Next Steps After Feature Complete

1. Run full test suite: `uv run pytest --cov=src`
2. Update README.md with new features
3. Create PR for code review
4. Consider Phase 3: Backtest Detail View
