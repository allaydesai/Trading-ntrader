# Implementation Plan: Backtest Run Page

**Branch**: `013-backtest-run-page` | **Date**: 2026-03-20 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/013-backtest-run-page/spec.md`

## Summary

Add a web UI page that allows users to configure and launch backtests from the browser, replacing the need for CLI usage. The page presents a form with strategy selection (with dynamic parameter inputs), symbol, date range, data source, timeframe, initial capital, and timeout. On submission, the backtest executes synchronously using the existing `BacktestOrchestrator` pipeline, and the user is redirected to the results detail page. The implementation reuses the existing backtest execution pipeline (`BacktestRequest` → `load_backtest_data()` → `BacktestOrchestrator`) and follows the established HTMX/Jinja2 template patterns.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, Jinja2, HTMX, Pydantic, nautilus-trader
**Storage**: PostgreSQL/TimescaleDB (existing — no schema changes)
**Testing**: pytest with markers (unit, component, integration with --forked)
**Target Platform**: Linux/macOS server (uvicorn)
**Project Type**: Web application (server-rendered with HTMX)
**Performance Goals**: Form renders in <200ms; backtest execution time depends on data volume (user-configurable timeout)
**Constraints**: Single concurrent backtest (asyncio.Lock); Nautilus LogGuard single-init; BacktestEngine single-use
**Scale/Scope**: Single-user tool; 3 new routes, 2 new templates, 1 new model file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | PASS | Reuses existing pipeline; no new dependencies; 3 routes in existing file |
| II. TDD (non-negotiable) | PASS | Tests written before implementation per TDD workflow |
| III. FastAPI-First | PASS | New routes use FastAPI router, Pydantic validation, dependency injection |
| IV. Type Safety & Docs | PASS | All new code type-hinted; public functions get docstrings |
| V. Dependency Discipline | PASS | No new dependencies needed — all tools already in the stack |
| VI. Fail Fast & Observable | PASS | Input validation at form boundary; structured error messages; timeout enforcement |
| VII. DRY & Modular | PASS | Reuses BacktestRequest, load_backtest_data, BacktestOrchestrator — zero duplication |

**Post-Phase 1 Re-check**: All gates still PASS. No new dependencies or complexity added beyond spec scope.

## Project Structure

### Documentation (this feature)

```text
specs/013-backtest-run-page/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 research decisions
├── data-model.md        # Phase 1 data model
├── quickstart.md        # Phase 1 quickstart guide
├── contracts/
│   └── endpoints.md     # Phase 1 endpoint contracts
├── checklists/
│   └── requirements.md  # Specification quality checklist
└── tasks.md             # Phase 2 output (via /speckit.tasks)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── ui/
│   │   └── backtests.py          # ADD: 3 route handlers (GET form, POST submit, GET strategy params)
│   └── models/
│       └── run_backtest.py       # NEW: BacktestRunFormData, StrategyOption, StrategyParamField models
├── core/
│   ├── backtest_orchestrator.py  # EXISTING: no changes
│   └── strategy_registry.py     # EXISTING: no changes
├── cli/commands/
│   └── _backtest_helpers.py      # EXISTING: reuse load_backtest_data(), resolve_backtest_request()
└── models/
    └── backtest_request.py       # EXISTING: reuse BacktestRequest.from_cli_args()

templates/
├── backtests/
│   ├── run.html                  # NEW: backtest configuration form page
│   └── partials/
│       └── strategy_params.html  # NEW: HTMX fragment for dynamic strategy parameters
└── partials/
    └── nav.html                  # MODIFY: add "Run Backtest" nav link

tests/
├── unit/api/
│   └── test_run_backtest_models.py    # NEW: form model validation tests
└── component/api/
    └── test_run_backtest_routes.py    # NEW: route handler tests
```

**Structure Decision**: All new code fits within the existing project layout. No new top-level directories. The form data model goes in `src/api/models/` following the existing pattern (`filter_models.py`, `backtest_list.py`). Routes are added to the existing backtests UI router.

## Complexity Tracking

No constitution violations to justify. All complexity is within bounds.
