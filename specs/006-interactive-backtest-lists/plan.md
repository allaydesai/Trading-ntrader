# Implementation Plan: Interactive Backtest Lists

**Branch**: `006-interactive-backtest-lists` | **Date**: 2025-11-15 | **Spec**: `/specs/006-interactive-backtest-lists/spec.md`
**Input**: Feature specification for Phase 2: Interactive Lists from NTrader Web UI Specification

## Summary

Add comprehensive filtering (strategy, instrument, date range, status), sorting (clickable column headers with direction indicators), and URL persistence to the existing backtest list page. Build on Phase 1's basic pagination with HTMX partial updates to create a fully interactive data exploration experience without full page reloads.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI 0.109+, Jinja2, HTMX, Tailwind CSS, SQLAlchemy 2.0 (async), Pydantic 2.5+
**Storage**: PostgreSQL 16+ (existing backtest metadata via SQLAlchemy 2.0 async)
**Testing**: pytest with pytest-asyncio, minimum 80% coverage on critical paths
**Target Platform**: Web browser (modern browsers supporting JavaScript for HTMX)
**Project Type**: Web application (server-rendered with HTMX progressive enhancement)
**Performance Goals**: <200ms filter/sort response time, <300ms page load with 20 results, support 10,000+ backtests via pagination
**Constraints**: Single-user dev tool, no complex auth, sub-100ms network latency assumed
**Scale/Scope**: Single page enhancement with 7 user stories, 18 functional requirements

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gates

1. **KISS/YAGNI**: ✅ PASS - Building only what's specified: filtering, sorting, URL persistence. No speculative features.

2. **TDD (NON-NEGOTIABLE)**: ✅ PASS - All functionality will be implemented test-first:
   - Unit tests for filter/sort query building
   - Integration tests for HTMX endpoints
   - End-to-end tests for URL state persistence

3. **FastAPI-First Architecture**: ✅ PASS - Already using FastAPI with Pydantic models. New endpoints will follow existing patterns in `src/api/ui/backtests.py`.

4. **Type Safety & Documentation**: ✅ PASS - All new functions will have type hints and Google-style docstrings. Mypy validation required.

5. **Dependency Discipline**: ✅ PASS - Using existing dependencies (HTMX, Tailwind, SQLAlchemy). No new packages required.

6. **Fail Fast & Observable**: ✅ PASS - Input validation at boundaries (query params), structured logging with correlation IDs for filter operations.

7. **DRY & Modular Design**: ✅ PASS - Extract filter/sort logic to reusable services, keep functions under 50 lines.

8. **File Size Limits**: ✅ PASS - Current `backtests.py` is 133 lines, plenty of room for new endpoints while staying under 500 lines.

### Potential Concerns

- **Complexity of filter state management**: Mitigated by using simple query string parameters rather than client-side state
- **Performance with large datasets**: Mitigated by database indexes already in place (strategy_name, created_at, id)

## Project Structure

### Documentation (this feature)

```text
specs/006-interactive-backtest-lists/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── ui/
│   │   └── backtests.py         # Enhanced with filter/sort/URL params
│   └── models/
│       ├── backtest_list.py     # Extended with FilterState, SortState
│       └── filter_models.py     # NEW: Filter/sort Pydantic models
├── db/
│   └── repositories/
│       └── backtest_repository.py  # Extended with filtered queries
└── services/
    └── backtest_query.py        # Enhanced with filter/sort logic

templates/
├── backtests/
│   ├── list.html                # Enhanced with filter controls, URL state
│   └── list_fragment.html       # Enhanced with sortable headers, indicators
└── partials/
    └── filter_controls.html     # NEW: Reusable filter form components

tests/
├── api/
│   └── ui/
│       └── test_backtests_filters.py  # NEW: Filter/sort endpoint tests
├── db/
│   └── repositories/
│       └── test_backtest_repository_filters.py  # NEW: Query tests
└── integration/
    └── test_filter_state_persistence.py  # NEW: URL state tests
```

**Structure Decision**: Enhancing existing web application structure. No new top-level directories needed. Filter logic encapsulated in service layer, UI routes handle HTTP concerns only.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

No constitution violations. Implementation follows existing patterns with minimal additional complexity.

## Post-Design Constitution Re-Check

*Re-evaluation after Phase 1 design artifacts completed.*

### Gate Re-Validation

1. **KISS/YAGNI**: ✅ PASS
   - FilterState model is single class with clear purpose
   - No over-engineered state machines or complex patterns
   - Reusing existing HTMX patterns from Phase 1

2. **TDD (NON-NEGOTIABLE)**: ✅ PASS
   - quickstart.md specifies test-first for each component
   - Clear test examples for FilterState validation, repository queries, service layer
   - Integration tests defined for URL state persistence

3. **FastAPI-First Architecture**: ✅ PASS
   - All endpoints use FastAPI Query parameters
   - Pydantic models for automatic validation
   - Consistent with existing backtests.py router

4. **Type Safety & Documentation**: ✅ PASS
   - All new models have comprehensive type hints
   - Google-style docstrings included in data-model.md
   - Enums provide type-safe values for sort columns and directions

5. **Dependency Discipline**: ✅ PASS
   - No new packages required
   - Leveraging existing HTMX, Tailwind, SQLAlchemy stack

6. **Fail Fast & Observable**: ✅ PASS
   - Pydantic validation at API boundary
   - Date range validation with clear error messages
   - Loading indicators for user feedback

7. **DRY & Modular Design**: ✅ PASS
   - FilterState.to_query_params() centralizes URL serialization
   - FilterState.with_sort() encapsulates toggle logic
   - Repository layer abstracts database queries

8. **File Size Limits**: ✅ PASS
   - No single file exceeds 500 lines
   - New filter_models.py will be <200 lines
   - Extensions to backtests.py keep it under 250 lines

### Design Quality Metrics

- **New Models**: 6 (FilterState, SortOrder, ExecutionStatus, SortColumn, SortableColumn, PaginationControl)
- **New Endpoints**: 3 (enhanced /backtests, enhanced /backtests/fragment, /backtests/instruments)
- **Database Changes**: 2 new indexes only (no schema changes)
- **Test Coverage Target**: 80%+ on new code
- **Performance Targets**: <200ms filter response, <300ms page load

### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Performance with large datasets | Low | Medium | Database indexes already planned |
| URL parameter encoding issues | Low | Low | Pydantic handles serialization |
| HTMX state synchronization | Medium | Low | Server is source of truth |
| Date validation edge cases | Low | Low | Comprehensive Pydantic validation |

**Conclusion**: Design passes all constitution gates. Ready for task generation and implementation.
