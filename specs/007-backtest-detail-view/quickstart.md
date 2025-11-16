# Quickstart: Backtest Detail View & Metrics

**Feature Branch**: `007-backtest-detail-view`
**Date**: 2025-11-15

## Prerequisites

- Python 3.11+ installed
- UV package manager installed
- PostgreSQL 16+ running with NTrader database
- NTrader Web UI Phase 1-2 completed (dashboard and list view working)

## Setup

### 1. Clone and Switch Branch

```bash
cd ~/dev/Trading-ntrader
git fetch origin
git checkout 007-backtest-detail-view
```

### 2. Sync Dependencies

```bash
uv sync
```

No new dependencies are required for this feature.

### 3. Verify Database

Ensure PostgreSQL is running and has backtest data:

```bash
# Check database connection
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader_user -d ntrader_dev -c "SELECT COUNT(*) FROM backtest_runs;"

# Should return count of existing backtests
```

### 4. Start Development Server

```bash
uv run uvicorn src.api.web:app --host 127.0.0.1 --port 8000 --reload
```

### 5. Access the UI

Open browser to: http://127.0.0.1:8000

1. Navigate to Backtests list
2. Click on any backtest row
3. Should see detail page at `/backtests/{run_id}`

---

## Development Workflow (TDD)

### Step 1: Write Failing Test

```bash
# Create test file
touch tests/ui/test_backtest_detail.py

# Write first test
cat > tests/ui/test_backtest_detail.py << 'EOF'
"""Tests for backtest detail view."""
import pytest
from uuid import uuid4
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_detail_page_returns_200_for_valid_run_id(
    client: AsyncClient,
    sample_backtest_in_db
):
    """GET /backtests/{run_id} returns 200 with valid UUID."""
    response = await client.get(f"/backtests/{sample_backtest_in_db.run_id}")
    assert response.status_code == 200
    assert "Run Details" in response.text
EOF

# Run test (should fail - RED phase)
uv run pytest tests/ui/test_backtest_detail.py -v
```

### Step 2: Implement Route

```python
# src/api/ui/backtests.py
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db, get_templates
from src.api.models.backtest_detail import to_detail_view

router = APIRouter(prefix="/backtests", tags=["backtests"])


@router.get("/{run_id}", response_class=HTMLResponse)
async def backtest_detail(
    request: Request,
    run_id: UUID,
    db: AsyncSession = Depends(get_db),
    templates = Depends(get_templates)
):
    """Display backtest detail page."""
    # Implementation here
    pass
```

### Step 3: Run Test Again

```bash
# Should pass (GREEN phase)
uv run pytest tests/ui/test_backtest_detail.py -v
```

### Step 4: Refactor

Clean up code while keeping tests green.

### Step 5: Run Full Test Suite

```bash
# Ensure no regressions
uv run pytest tests/ -v --cov=src --cov-report=term-missing
```

---

## Key Files to Create

### 1. View Models

```bash
touch src/api/models/backtest_detail.py
```

Copy Pydantic models from `data-model.md`.

### 2. Jinja2 Templates

```bash
# Detail page
touch templates/backtests/detail.html

# Reusable partials
touch templates/partials/metrics_panel.html
touch templates/partials/config_snapshot.html
```

### 3. Test Fixtures

```bash
# In tests/conftest.py
touch tests/ui/conftest.py
```

Add fixture for sample backtest with metrics.

---

## Testing Commands

### Run All UI Tests

```bash
uv run pytest tests/ui/ -v
```

### Run with Coverage

```bash
uv run pytest tests/ui/ --cov=src/api --cov-report=html
open htmlcov/index.html
```

### Run Specific Test

```bash
uv run pytest tests/ui/test_backtest_detail.py::test_detail_page_returns_200_for_valid_run_id -v
```

### Type Checking

```bash
uv run mypy src/api/models/backtest_detail.py
```

### Linting

```bash
uv run ruff check src/api/models/backtest_detail.py
uv run ruff format src/api/models/backtest_detail.py
```

---

## Manual Testing Checklist

### Detail Page Display (P1)

- [ ] Navigate to `/backtests/{run_id}` with valid UUID
- [ ] Verify breadcrumb shows: Dashboard > Backtests > Run Details
- [ ] Confirm all return metrics displayed (Total Return, CAGR, Final Balance)
- [ ] Confirm all risk metrics displayed (Sharpe, Sortino, Max Drawdown, Volatility)
- [ ] Confirm all trading metrics displayed (Total Trades, Win Rate, Profit Factor)
- [ ] Verify positive metrics highlighted in green
- [ ] Verify negative metrics highlighted in red
- [ ] Hover over metric name to see tooltip explanation
- [ ] Confirm configuration section shows all parameters
- [ ] Click "Copy CLI Command" and verify clipboard content
- [ ] Collapse/expand configuration section works

### Error Handling

- [ ] Navigate to `/backtests/00000000-0000-0000-0000-000000000000`
- [ ] Verify 404 page with helpful message
- [ ] Confirm navigation back to backtest list works

### Action Buttons (P4)

- [ ] Click "Export Report" - HTML file downloads
- [ ] Click "Delete" - confirmation dialog appears
- [ ] Confirm deletion - redirects to list with success message
- [ ] Click "Re-run Backtest" - new backtest initiated (if implemented)

### Performance

- [ ] Detail page loads in <1 second
- [ ] Export generates in <2 seconds
- [ ] No JavaScript errors in browser console

---

## Common Issues

### 1. Template Not Found

**Error**: `TemplateNotFound: backtests/detail.html`

**Solution**: Ensure templates directory is configured in FastAPI app:

```python
# src/api/web.py
templates = Jinja2Templates(directory="templates")
```

### 2. Database Query Fails

**Error**: `NoResultFound`

**Solution**: Verify backtest exists in database:

```bash
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader_user -d ntrader_dev \
  -c "SELECT run_id FROM backtest_runs LIMIT 5;"
```

### 3. Metrics Not Loading

**Error**: `AttributeError: 'NoneType' object has no attribute 'sharpe_ratio'`

**Solution**: Check metrics relationship is eagerly loaded:

```python
# Use selectinload for one-to-one
from sqlalchemy.orm import selectinload

query = select(BacktestRun).options(selectinload(BacktestRun.metrics))
```

### 4. HTMX Not Working

**Symptom**: Full page reload instead of partial update

**Solution**: Verify HTMX script loaded in base template:

```html
<script src="https://unpkg.com/htmx.org@1.9.10"></script>
```

---

## Next Steps

1. Run `/speckit.tasks` to generate implementation task list
2. Follow TDD workflow for each task
3. Keep test coverage above 80%
4. Update CLAUDE.md if adding new patterns

---

## Resources

- Feature Spec: `specs/007-backtest-detail-view/spec.md`
- Data Models: `specs/007-backtest-detail-view/data-model.md`
- Route Contracts: `specs/007-backtest-detail-view/contracts/html-routes.md`
- NTrader Web UI Spec: `docs/NTrader-webui-specification.md`
- Constitution: `.specify/memory/constitution.md`
