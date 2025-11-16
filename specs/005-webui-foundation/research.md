# Research: Web UI Foundation

**Date**: 2025-11-15
**Feature**: 005-webui-foundation

## Research Tasks

### 1. FastAPI + Jinja2 Integration Pattern

**Decision**: Use `Jinja2Templates` class from `fastapi.templating` with new-style `TemplateResponse` syntax

**Rationale**:
- Official FastAPI documentation pattern
- Request-first signature is current best practice (avoids deprecation warnings)
- Direct integration with FastAPI's `StaticFiles` for CSS/JS assets
- Built-in `url_for()` function in templates for route resolution

**Implementation Pattern**:
```python
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"stats": dashboard_stats}
    )
```

**Alternatives Considered**:
- Old-style TemplateResponse (name-first) - Deprecated
- Custom template rendering - Reinvents wheel
- Third-party libraries like Starlette-based solutions - Unnecessary complexity

---

### 2. HTMX Partial Update Pattern for Pagination

**Decision**: Use `hx-get` with `hx-target` and `hx-swap` for partial table updates

**Rationale**:
- Server renders HTML fragments (no client-side JS rendering needed)
- Native browser history integration with `hx-push-url`
- Simple attribute-based approach aligns with KISS principle
- Works seamlessly with Jinja2 fragments

**Implementation Pattern**:
```html
<!-- Pagination controls -->
<div id="backtest-list">
  <table>...</table>
  <div class="pagination">
    <button hx-get="/backtests/page?page=2"
            hx-target="#backtest-list"
            hx-swap="innerHTML">
      Next Page
    </button>
  </div>
</div>
```

**Server returns HTML fragment**:
```html
<!-- list_fragment.html -->
<table>
  {% for bt in backtests %}
  <tr>...</tr>
  {% endfor %}
</table>
<div class="pagination">...</div>
```

**Alternatives Considered**:
- Full page reload - Poor UX, slower
- Client-side pagination with JavaScript - Violates "minimal JS" principle
- Out-of-band swaps - Overcomplication for simple pagination

---

### 3. Existing Database Services Integration

**Decision**: Extend `BacktestQueryService` with aggregation methods for dashboard

**Rationale**:
- Service layer already exists with repository pattern
- Async SQLAlchemy 2.0 compatible
- Includes cursor-based pagination support
- Type-safe with proper relationship loading (selectin)

**Existing Capabilities**:
- `list_recent_backtests(limit, cursor)` - Paginated list (max 1000)
- `find_top_performers(metric, limit)` - Best by Sharpe or return
- `get_backtest_by_id(run_id)` - Single backtest detail

**New Methods Needed**:
```python
# Dashboard aggregates
async def get_dashboard_stats(self) -> DashboardStats:
    """Get aggregate statistics for dashboard."""
    # Total count, best Sharpe, worst drawdown

async def get_recent_activity(self, limit: int = 5) -> List[BacktestRun]:
    """Get most recent backtests for dashboard."""
```

**Alternatives Considered**:
- Create new service layer - Duplicates existing patterns
- Direct repository access from routes - Violates service layer architecture
- Redis caching - YAGNI for single-user deployment

---

### 4. Tailwind CSS Dark Theme Implementation

**Decision**: Use pre-compiled Tailwind CSS with dark color classes

**Rationale**:
- No build pipeline needed (CDN or single compiled CSS file)
- Utility-first classes for rapid development
- Standard dark theme colors (slate-950 background, slate-100 text)
- Meets 4.5:1 contrast ratio requirement for WCAG AA

**Color Scheme**:
```css
/* Base dark theme */
bg-slate-950    /* #020617 - background */
text-slate-100  /* #f1f5f9 - primary text */
text-slate-400  /* #94a3b8 - secondary text */

/* Status colors */
text-green-500  /* #22c55e - positive metrics */
text-red-500    /* #ef4444 - negative metrics */

/* Interactive elements */
bg-blue-600     /* #2563eb - primary buttons */
border-slate-700 /* #334155 - borders */
```

**Alternatives Considered**:
- Build pipeline with npm/PostCSS - Overcomplication for Phase 1
- Plain CSS - More verbose, harder to maintain
- CSS framework (Bootstrap) - Heavier, less customizable

---

### 5. Testing Strategy for Web Routes

**Decision**: Use FastAPI TestClient with pytest for route testing

**Rationale**:
- Constitution mandates TDD with 80% coverage
- TestClient provides synchronous interface for async routes
- Can verify HTML content, status codes, redirects
- Integrates with pytest fixtures for database setup

**Testing Pattern**:
```python
from fastapi.testclient import TestClient
from src.api.web import app

def test_dashboard_renders_summary_stats(db_session_with_data):
    """Test dashboard displays aggregate statistics."""
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "Total Backtests" in response.text
    assert "Best Sharpe" in response.text

def test_backtest_list_pagination(db_session_with_100_backtests):
    """Test pagination returns 20 results per page."""
    client = TestClient(app)
    response = client.get("/backtests")

    assert response.status_code == 200
    assert response.text.count("<tr>") == 21  # header + 20 rows
```

**Alternatives Considered**:
- Playwright E2E tests - Too heavy for Phase 1
- Manual testing - Violates TDD principle
- AsyncClient with httpx - More complex setup needed

---

### 6. Project Structure Decision

**Decision**: Add `src/api/ui/` for web routes alongside existing `src/services/`

**Rationale**:
- Maintains existing project structure
- Clear separation: UI routes vs. business services
- Follows FastAPI router pattern from constitution
- Templates and static files at repository root (standard convention)

**Structure**:
```
src/
├── api/
│   ├── ui/           # NEW: HTML route handlers
│   │   ├── dashboard.py
│   │   └── backtests.py
│   └── dependencies.py  # Shared DI (db session, templates)
├── services/         # EXISTING: Business logic layer
└── db/               # EXISTING: Database models

templates/            # NEW: Jinja2 templates
static/               # NEW: Frontend assets
```

**Alternatives Considered**:
- Separate `frontend/` directory - Adds complexity, not needed for server-rendered
- All routes in single file - Violates modular code principle (files <500 lines)
- Create new FastAPI app instance - Overcomplication, share existing services

---

### 7. Error Handling for Empty States

**Decision**: Handle empty states with template conditionals and placeholder messages

**Rationale**:
- Functional requirement FR-015: graceful empty states
- User-friendly messages guide users on next steps
- No additional backend complexity needed

**Implementation**:
```html
{% if backtests %}
  <table>...</table>
{% else %}
  <div class="empty-state">
    <p>No backtests found.</p>
    <p>Run your first backtest via CLI: <code>ntrader backtest run</code></p>
  </div>
{% endif %}
```

**Error Display**:
```html
{% if error %}
  <div class="error-banner bg-red-900 text-red-100 p-4">
    <p>{{ error.message }}</p>
    {% if error.recovery %}
      <p>Suggestion: {{ error.recovery }}</p>
    {% endif %}
  </div>
{% endif %}
```

**Alternatives Considered**:
- JavaScript-based error handling - Adds client complexity
- Redirect to error page - Poor UX, loses context
- Generic error messages - Not helpful for debugging

---

## Dependencies to Add

```bash
uv add jinja2 python-multipart
# python-multipart needed for form data if used later
```

**Note**: HTMX and Tailwind CSS are frontend assets served via static files (CDN or vendor/).

---

## Summary of Key Decisions

1. **Server-rendered HTML** with FastAPI + Jinja2 (no SPA)
2. **HTMX for interactivity** (pagination, partial updates)
3. **Extend existing services** rather than creating new layers
4. **Pre-compiled Tailwind** for dark theme styling
5. **TestClient-based TDD** for route testing
6. **Modular router structure** in `src/api/ui/`
7. **Template conditionals** for error/empty state handling

All decisions align with KISS/YAGNI principles and Python Backend Development Constitution requirements.
