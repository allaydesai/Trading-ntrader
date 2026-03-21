# Quickstart: Backtest Run Page

**Date**: 2026-03-20
**Feature**: 013-backtest-run-page

## Prerequisites

- Python 3.11+ with UV
- PostgreSQL running (for backtest persistence)
- `uv sync` completed
- `alembic upgrade head` completed
- Parquet catalog with data (or use "mock" data source)

## Development Setup

```bash
# Ensure on feature branch
git checkout 013-backtest-run-page

# Install dependencies (no new deps needed)
uv sync

# Build Tailwind CSS (if not done)
./scripts/build-css.sh

# Run dev server
uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000
```

## Verify Feature

1. Navigate to `http://127.0.0.1:8000/backtests/run`
2. Select a strategy from the dropdown
3. Enter a symbol (e.g., "AAPL" for catalog data, or any symbol for mock)
4. Set date range and data source
5. Click "Run Backtest"
6. Verify redirect to results detail page

## Run Tests

```bash
# Unit tests for form models and validation
make test-unit

# Component tests for route handlers
make test-component

# Integration tests (requires --forked for Nautilus)
make test-integration
```

## Key Files

| File | Purpose |
|------|---------|
| `src/api/ui/backtests.py` | Route handlers (GET form, POST submit, GET strategy params) |
| `src/api/models/run_backtest.py` | Form data model and strategy view models |
| `templates/backtests/run.html` | Main form template |
| `templates/backtests/partials/strategy_params.html` | HTMX fragment for dynamic strategy params |
| `templates/partials/nav.html` | Updated with "Run Backtest" link |
| `tests/unit/api/test_run_backtest_models.py` | Unit tests for form validation |
| `tests/component/api/test_run_backtest_routes.py` | Route handler tests |
