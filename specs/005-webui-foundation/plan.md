# Implementation Plan: Web UI Foundation

**Branch**: `005-webui-foundation` | **Date**: 2025-11-15 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-webui-foundation/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Build the foundational web interface for NTrader backtesting system including dashboard with summary statistics, persistent navigation, and paginated backtest list. Technical approach uses FastAPI + Jinja2 for server-rendered HTML with HTMX for partial updates and Tailwind CSS for dark theme styling.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, Jinja2, HTMX, Tailwind CSS
**Storage**: PostgreSQL (existing backtest metadata via SQLAlchemy 2.0 async)
**Testing**: pytest with FastAPI TestClient, minimum 80% coverage
**Target Platform**: Desktop web browsers (Linux/macOS/Windows)
**Project Type**: Web application (backend renders HTML, minimal frontend JS)
**Performance Goals**: Dashboard <500ms, backtest list <300ms for 20 results
**Constraints**: Single-user deployment, no authentication in Phase 1
**Scale/Scope**: Support 100+ backtests with pagination, dark mode default

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Evaluation

| Gate | Requirement | Status | Notes |
|------|-------------|--------|-------|
| KISS | Start with simplest working solution | PASS | Server-rendered HTML + HTMX (no SPA/React) |
| YAGNI | Build only what's needed | PASS | Dashboard + list only, no comparison/charts yet |
| TDD | Tests before implementation | PASS | Will write route/template tests first |
| FastAPI-First | Use FastAPI with Pydantic | PASS | FastAPI routers for UI + JSON endpoints |
| Type Safety | Type hints + docstrings | PASS | All route handlers typed |
| UV Only | UV for package management | PASS | Will use `uv add jinja2` etc. |
| Files <500 lines | Modular code | PASS | Separate routers for dashboard, backtests |
| Functions <50 lines | Single responsibility | PASS | Each route handler focused |

**Pre-design gate status: ALL PASS**

## Project Structure

### Documentation (this feature)

```text
specs/005-webui-foundation/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── api/                 # NEW: Web UI layer
│   ├── __init__.py
│   ├── web.py          # Main app with static files mount
│   ├── ui/             # HTML route handlers
│   │   ├── __init__.py
│   │   ├── dashboard.py    # GET / - dashboard page
│   │   └── backtests.py    # GET /backtests - list page
│   └── dependencies.py  # Shared FastAPI dependencies
├── core/                # Existing business logic
├── services/            # Existing services (backtest_query, database_repository)
├── db/                  # Existing DB models (PostgreSQL)
├── models/              # Existing Pydantic models
└── utils/               # Shared utilities

templates/               # NEW: Jinja2 templates
├── base.html           # Layout with nav, footer
├── dashboard.html      # Dashboard page
├── backtests/
│   ├── list.html       # Backtest list page
│   └── list_fragment.html  # HTMX partial for pagination
└── partials/
    ├── nav.html        # Navigation component
    └── footer.html     # Footer component

static/                  # NEW: Frontend assets
├── css/
│   └── styles.css      # Tailwind compiled CSS
└── vendor/
    └── htmx.min.js     # HTMX library

tests/
├── api/                # NEW: Web UI tests
│   ├── test_dashboard.py
│   └── test_backtests.py
├── integration/        # Existing integration tests
└── unit/               # Existing unit tests
```

**Structure Decision**: Extends existing single-project structure by adding `src/api/ui/` for web routes, `templates/` for Jinja2, and `static/` for assets. Maintains existing CLI, services, and DB layers.

## Complexity Tracking

> **No violations identified - all gates pass**

N/A

---

## Post-Design Constitution Re-Check

*Re-evaluation after Phase 1 design artifacts completed*

### Gate Verification

| Gate | Requirement | Status | Evidence |
|------|-------------|--------|----------|
| KISS | Simplest working solution | PASS | Server-rendered HTML, no SPA framework, reuses existing services |
| YAGNI | Only build what's needed | PASS | View models only, no DB changes, no authentication, no charts yet |
| TDD | Tests before implementation | PASS | quickstart.md defines test-first workflow with example tests |
| FastAPI-First | Pydantic + async patterns | PASS | View models are Pydantic, routes use async/await |
| Type Safety | Type hints + docstrings | PASS | All models have typed fields, mapping functions documented |
| UV Only | Package management | PASS | `uv add jinja2` specified in quickstart |
| Files <500 lines | Modular code | PASS | Separate routers (dashboard.py, backtests.py), separate models |
| Functions <50 lines | Single responsibility | PASS | Each route handler focused, mapping functions simple |
| DRY | No duplication | PASS | Shared templates (base.html, nav.html), reuse existing services |
| Fail Fast | Error handling | PASS | Edge cases documented, empty states planned |

### Design Artifact Checklist

- [x] **research.md**: All technical decisions documented with rationale
- [x] **data-model.md**: View models defined with validation rules
- [x] **contracts/**: OpenAPI spec for HTML routes and schemas
- [x] **quickstart.md**: TDD workflow with setup and verification steps

### Potential Risks Identified

1. **Performance**: Dashboard aggregation queries may be slow with large datasets
   - Mitigation: Use existing indexes, monitor query times in tests

2. **Template complexity**: Jinja2 templates could grow large
   - Mitigation: Use partials (nav.html, footer.html), keep logic in Python

3. **Browser compatibility**: HTMX requires modern browser
   - Mitigation: Desktop focus, graceful degradation with standard links

### Final Gate Status: ALL PASS ✓

The design phase is complete and ready for task generation. All constitution principles are upheld, and no violations require justification.

---

## Generated Artifacts Summary

| Artifact | Path | Purpose |
|----------|------|---------|
| Plan | `specs/005-webui-foundation/plan.md` | This file - architecture and decisions |
| Research | `specs/005-webui-foundation/research.md` | Technical decisions with rationale |
| Data Model | `specs/005-webui-foundation/data-model.md` | View models and mappings |
| Contracts | `specs/005-webui-foundation/contracts/` | OpenAPI spec for routes |
| Quickstart | `specs/005-webui-foundation/quickstart.md` | Setup and TDD workflow |

**Next Step**: Run `/speckit.tasks` to generate implementation tasks from this plan.
