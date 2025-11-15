# Quickstart: Web UI Foundation

**Feature**: 005-webui-foundation
**Date**: 2025-11-15

## Prerequisites

Before implementing this feature, ensure:

1. **Python 3.11+** installed and configured
2. **UV package manager** available (`uv --version`)
3. **PostgreSQL database** running with backtest tables migrated
4. **Existing backtest data** (optional but helpful for testing)

## Setup Steps

### 1. Install Dependencies

```bash
# Add web UI dependencies
uv add jinja2 python-multipart

# Sync environment
uv sync
```

### 2. Create Directory Structure

```bash
# Create template directories
mkdir -p templates/backtests templates/partials

# Create static asset directories
mkdir -p static/css static/vendor

# Create API source directories
mkdir -p src/api/ui tests/api
```

### 3. Download Frontend Assets

```bash
# Download HTMX (v2.0.4)
curl -L https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js \
  -o static/vendor/htmx.min.js

# For Tailwind CSS, use CDN in development
# Production: compile with Tailwind CLI or use pre-built
```

### 4. Create Base Configuration

**src/api/web.py** - Main FastAPI app:

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="NTrader Web UI", version="0.1.0")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Include routers (to be created)
# from src.api.ui import dashboard, backtests
# app.include_router(dashboard.router)
# app.include_router(backtests.router, prefix="/backtests")
```

### 5. Verify Database Connection

Ensure the database is accessible using existing configuration:

```bash
# Test database connection via CLI
ntrader health check

# Or directly test the session
python -c "
from src.db.session import get_session
import asyncio

async def test():
    async with get_session() as session:
        print('Database connected')

asyncio.run(test())
"
```

## Development Workflow

### Test-Driven Development (Required)

Follow this pattern for each component:

1. **Write failing test first**:
```bash
# Create test file
touch tests/api/test_dashboard.py

# Write test that fails
# Run to confirm failure
uv run pytest tests/api/test_dashboard.py -v
```

2. **Implement minimal code**:
```bash
# Create implementation
touch src/api/ui/dashboard.py

# Run test again - should pass
uv run pytest tests/api/test_dashboard.py -v
```

3. **Refactor and iterate**

### Running the Web Server

```bash
# Development server with auto-reload
uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000

# Open in browser
open http://127.0.0.1:8000
```

### Running Tests

```bash
# All web UI tests
uv run pytest tests/api/ -v

# With coverage
uv run pytest tests/api/ --cov=src/api --cov-report=html

# Single test file
uv run pytest tests/api/test_dashboard.py -v
```

### Code Quality Checks

```bash
# Format code
uv run ruff format .

# Lint
uv run ruff check .

# Type check
uv run mypy src/api/
```

## Implementation Order

Follow this sequence for best results:

### Phase 1: Foundation (TDD)
1. **Base template** (`templates/base.html`)
   - Test: Response includes nav and footer
2. **Navigation partial** (`templates/partials/nav.html`)
   - Test: Active page highlighted correctly
3. **Dashboard route** (`src/api/ui/dashboard.py`)
   - Test: Returns 200, includes stats
4. **Dashboard service method** (extend `BacktestQueryService`)
   - Test: Aggregates stats correctly

### Phase 2: Backtest List (TDD)
5. **Backtest list route** (`src/api/ui/backtests.py`)
   - Test: Returns 20 results, pagination controls
6. **List fragment for HTMX** (partial template)
   - Test: Fragment endpoint returns table only
7. **Pagination logic**
   - Test: Next/previous pages work correctly

### Phase 3: Polish
8. **Error handling** (empty states, DB errors)
9. **Performance optimization** (query efficiency)
10. **Accessibility** (WCAG AA compliance)

## Example Test Structure

```python
# tests/api/test_dashboard.py
import pytest
from fastapi.testclient import TestClient

from src.api.web import app


@pytest.fixture
def client():
    return TestClient(app)


def test_dashboard_returns_200(client):
    """Dashboard page loads successfully."""
    response = client.get("/")
    assert response.status_code == 200


def test_dashboard_displays_total_backtests(client, db_with_backtests):
    """Dashboard shows total backtest count."""
    response = client.get("/")
    assert "Total Backtests" in response.text
    assert "42" in response.text  # Expected count


def test_dashboard_shows_empty_state_when_no_data(client, empty_db):
    """Dashboard handles no backtests gracefully."""
    response = client.get("/")
    assert "No backtests" in response.text
    assert "ntrader backtest run" in response.text  # CLI suggestion
```

## Common Issues & Solutions

### Template Not Found
```
jinja2.exceptions.TemplateNotFound: base.html
```
**Solution**: Ensure `templates/` directory exists at repository root, not in `src/`.

### Static Files 404
```
404 Not Found: /static/css/styles.css
```
**Solution**: Verify `static/` directory exists and is mounted correctly in `web.py`.

### Database Session Error
```
RuntimeError: Session context not available
```
**Solution**: Use FastAPI dependency injection for database sessions:
```python
from fastapi import Depends
from src.db.session import get_session

@router.get("/")
async def dashboard(session = Depends(get_session)):
    ...
```

### HTMX Not Loading
```
htmx is not defined
```
**Solution**: Check script tag in `base.html`:
```html
<script src="{{ url_for('static', path='/vendor/htmx.min.js') }}" defer></script>
```

## Success Criteria Verification

Run these checks before considering feature complete:

```bash
# 1. All tests pass
uv run pytest tests/api/ -v

# 2. Coverage meets 80% threshold
uv run pytest tests/api/ --cov=src/api --cov-report=term-missing

# 3. No lint errors
uv run ruff check src/api/

# 4. Type checking passes
uv run mypy src/api/

# 5. Performance targets met
# Dashboard: <500ms
# Backtest list: <300ms for 20 results
# (Use browser dev tools to measure)

# 6. Manual verification
# - Navigation highlights work
# - Pagination loads correctly
# - Empty states display properly
# - Dark theme colors applied
```

## Next Steps After Completion

1. **Run `/speckit.tasks` command** to generate implementation tasks
2. **Create feature branch** if not already done
3. **Follow TDD cycle** for each task
4. **Update README.md** with web UI startup instructions
5. **Plan Phase 2** (backtest detail page, charts)

## Resources

- [FastAPI Templates Documentation](https://fastapi.tiangolo.com/advanced/templates/)
- [HTMX Documentation](https://htmx.org/docs/)
- [Tailwind CSS Dark Mode](https://tailwindcss.com/docs/dark-mode)
- [NTrader Developer Guide](../../docs/NTrader_Web_UI–Developer_Guide.md)
