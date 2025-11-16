# Implementation Plan: Backtest Detail View & Metrics

**Branch**: `007-backtest-detail-view` | **Date**: 2025-11-15 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/007-backtest-detail-view/spec.md`

**Note**: This plan builds Phase 3 of the NTrader Web UI - the detail view for individual backtest results with comprehensive metrics display, trade blotter, configuration snapshot, and action buttons.

## Summary

Build a comprehensive detail view for individual backtests accessible via `/backtests/{run_id}` that displays all performance metrics (returns, risk, trading statistics), configuration parameters, trade blotter with sorting/filtering/pagination, and action buttons (export, delete, re-run). The implementation uses FastAPI + Jinja2 for server-side rendering, building on existing Phase 2 foundation (backtest list view).

## Technical Context

**Language/Version**: Python 3.11+ (matches existing codebase)
**Primary Dependencies**: FastAPI 0.109+, Jinja2 3.1+, Pydantic 2.5+, SQLAlchemy 2.0+ (async), HTMX 1.9+, Tailwind CSS
**Storage**: PostgreSQL 16+ (existing backtest_runs and performance_metrics tables)
**Testing**: pytest with pytest-asyncio, FastAPI TestClient, 80% minimum coverage
**Target Platform**: Local development server (single-user tool)
**Project Type**: Web application (server-rendered HTML with minimal JavaScript)
**Performance Goals**: Detail page load <1 second, trade blotter pagination <500ms, export generation <2 seconds
**Constraints**: Pagination required for >100 trades, tooltips for metric explanations, color-coded metrics (green/red)
**Scale/Scope**: Single backtest detail view, trade tables up to 10,000+ rows with pagination

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Check ✅

| Principle | Status | Justification |
|-----------|--------|---------------|
| **I. Simplicity (KISS/YAGNI)** | ✅ PASS | Simple server-rendered HTML with HTMX for interactivity. No complex frontend framework. Features are direct requirements from spec (metrics display, trade blotter, actions). |
| **II. Test-Driven Development** | ✅ PASS | All routes will have pytest unit tests before implementation. Integration tests for database queries. 80%+ coverage target on all new code. |
| **III. FastAPI-First Architecture** | ✅ PASS | Building on existing FastAPI + Jinja2 setup from Phase 1-2. Uses dependency injection for DB sessions. Async/await for all I/O. |
| **IV. Type Safety & Documentation** | ✅ PASS | All new Pydantic models will have type hints. Google-style docstrings required. |
| **V. Dependency Discipline** | ✅ PASS | No new dependencies required. Uses existing FastAPI, Jinja2, HTMX stack from Phase 2. |
| **VI. Fail Fast & Observable** | ✅ PASS | 404 error handling for invalid run_ids. Structured logging for operations. Clear error messages. |
| **VII. DRY & Modular Design** | ✅ PASS | Reuses existing BacktestRun/PerformanceMetrics models. Metrics formatting functions shared across views. |

### Gate Violations

None. All constitution principles are satisfied.

## Project Structure

### Documentation (this feature)

```text
specs/007-backtest-detail-view/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output - trade blotter patterns, tooltip UX
├── data-model.md        # Phase 1 output - BacktestDetailView model
├── quickstart.md        # Phase 1 output - setup and test instructions
├── contracts/           # Phase 1 output - HTML route specifications
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── ui/
│   │   └── backtests.py          # Add detail route handler
│   └── models/
│       └── backtest_detail.py    # NEW: Detail view Pydantic models
├── services/
│   └── backtest_service.py       # Add get_by_run_id, delete, export methods
└── db/
    └── models/
        └── backtest.py           # Existing - no changes needed

templates/
├── backtests/
│   ├── list.html                 # Existing
│   ├── list_fragment.html        # Existing
│   └── detail.html               # NEW: Detail page template
└── partials/
    ├── metrics_panel.html        # NEW: Reusable metrics display
    ├── trade_blotter.html        # NEW: Paginated trade table
    └── config_snapshot.html      # NEW: Configuration display

tests/
└── ui/
    ├── test_backtest_detail.py   # NEW: Detail route tests
    └── test_backtest_models.py   # NEW: Model unit tests
```

**Structure Decision**: Follows existing NTrader Web UI architecture from Phase 1-2. HTML routes in `src/api/ui/`, Pydantic models in `src/api/models/`, Jinja2 templates in `templates/`. Consistent with specification in `docs/NTrader-webui-specification.md`.

## Complexity Tracking

> No violations to justify. Implementation follows established patterns from Phase 1-2.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| N/A | N/A | N/A |
