# Implementation Plan: Individual Trade Tracking & Equity Curve Generation

**Branch**: `009-trade-tracking` | **Date**: 2025-01-22 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/009-trade-tracking/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement comprehensive trade tracking for backtests by capturing individual trade executions from Nautilus Trader's FillReport system, persisting them to PostgreSQL, and generating equity curves and performance metrics. This enables traders to analyze trade-by-trade performance, visualize account balance evolution, calculate drawdowns, and understand trading patterns beyond aggregate returns. The implementation leverages Nautilus Trader's built-in reporting capabilities (`generate_fills_report()`, `FillReport` objects) to extract trade data and stores it using SQLAlchemy 2.0 async ORM with proper data validation via Pydantic models.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Nautilus Trader (FillReport, Position APIs), FastAPI 0.109+, SQLAlchemy 2.0 (async), Pydantic 2.5+, Pandas (for report generation)
**Storage**: PostgreSQL 16+ with existing backtest_runs infrastructure (specs/004-postgresql-metadata-storage)
**Testing**: pytest 7.4+ with pytest-asyncio for async database operations, TDD approach with 80% minimum coverage
**Target Platform**: Linux server / macOS development environment
**Project Type**: Web application (backend API + database + web UI integration)
**Performance Goals**:
- Bulk insert 500+ trades in <5 seconds
- Equity curve generation for 1000 trades in <1 second
- Paginated API queries return within 300ms for 100 trades/page
- UI page load (first 20 trades + chart) within 2 seconds

**Constraints**:
- Must integrate with existing Nautilus Trader backtest execution workflow
- Must preserve Decimal precision for financial calculations (no float)
- Must use async/await patterns throughout for consistency with existing codebase
- All timestamps in UTC with nanosecond precision (matching Nautilus Trader)
- Server-side pagination/sorting for scalability with 1000+ trades

**Scale/Scope**:
- Expected 50-500 trades per typical backtest run
- Support up to 5000 trades per backtest for high-frequency strategies
- Database design should handle 100+ backtest runs with trade history
- UI must remain responsive with large datasets via pagination

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### ✅ Simplicity First (KISS/YAGNI)
- **Status**: PASS
- **Justification**: Implementation uses existing Nautilus Trader APIs (`generate_fills_report()`) rather than building custom trade capture. Database schema extends existing `backtest_runs` table with simple foreign key relationship. No speculative features - all functionality directly supports documented user scenarios.

### ✅ Test-Driven Development
- **Status**: PASS
- **Justification**: Feature will follow strict Red-Green-Refactor cycle. Tests written before implementation for:
  - Trade persistence from FillReport data
  - Equity curve calculation logic
  - Drawdown analysis algorithms
  - API endpoint pagination/sorting
  - UI integration with existing backtest details page
- Minimum 80% coverage target on all critical paths

### ✅ FastAPI-First Architecture
- **Status**: PASS
- **Justification**: All new endpoints follow existing FastAPI patterns:
  - `/api/v1/backtests/{run_id}/trades` for paginated trade lists
  - `/api/v1/backtests/{run_id}/equity-curve` for time-series data
  - Pydantic models for request/response validation
  - Async endpoints with dependency injection for database sessions
  - Auto-generated OpenAPI documentation

### ✅ Type Safety & Documentation
- **Status**: PASS
- **Justification**:
  - All functions use PEP 484 type hints with mypy validation
  - Pydantic models for data validation (TradeCreate, TradeResponse, EquityCurvePoint)
  - Google-style docstrings with examples
  - API documentation auto-generated via FastAPI
  - SQLAlchemy models with typed columns

### ✅ Dependency Discipline
- **Status**: PASS
- **Justification**:
  - All dependencies already in use: Nautilus Trader, SQLAlchemy 2.0, FastAPI, Pydantic 2.5+
  - No new external dependencies required
  - Pandas already used for report generation
  - All deps managed via `uv` (never edit pyproject.toml directly)

### ✅ Fail Fast & Observable
- **Status**: PASS
- **Justification**:
  - Input validation at API boundaries via Pydantic
  - Database foreign key constraints enforce referential integrity
  - Structured logging with structlog for trade capture operations
  - Correlation IDs for request tracing
  - Early validation of FillReport data before database insertion

### ✅ DRY & Modular Design
- **Status**: PASS
- **Justification**:
  - Trade persistence logic encapsulated in service layer (BacktestPersistenceService)
  - Equity curve calculation in separate utility module
  - Reusable pagination logic for API endpoints
  - Functions under 50 lines, classes under 100 lines
  - No code duplication - extends existing persistence patterns

### ✅ File Size Limits
- **Status**: PASS
- **Justification**:
  - New files: `src/db/models/trade.py` (~100 lines), `src/services/trade_analytics.py` (~200 lines)
  - Modified files stay well under 500 lines
  - UI components added to existing backtest details template (~50 lines addition)

### ✅ Project Structure Compliance
- **Status**: PASS
- **Justification**: Follows existing structure from constitution:
```
src/
├── api/routers/trades.py          # New FastAPI router
├── models/trade.py                # New Pydantic models
├── services/trade_analytics.py    # New business logic
├── db/models/trade.py             # New SQLAlchemy model
tests/
├── unit/test_trade_analytics.py   # New unit tests
├── integration/test_trades_api.py # New integration tests
```

**Overall Status**: ✅ ALL GATES PASS - No violations requiring justification

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
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
├── api/
│   └── routers/
│       └── trades.py                  # NEW: Trade-related API endpoints
├── models/
│   └── trade.py                       # NEW: Pydantic models for trades
├── services/
│   ├── backtest_persistence.py        # MODIFIED: Add trade capture logic
│   └── trade_analytics.py             # NEW: Equity curve & metrics calculation
├── db/
│   ├── models/
│   │   └── trade.py                   # NEW: SQLAlchemy Trade model
│   └── migrations/
│       └── versions/
│           └── xxx_add_trades_table.py # NEW: Alembic migration
└── templates/
    └── backtest_detail.html           # MODIFIED: Add trades section & chart

tests/
├── unit/
│   ├── test_trade_analytics.py        # NEW: Unit tests for calculations
│   └── test_trade_models.py           # NEW: Unit tests for data models
├── integration/
│   ├── test_trades_api.py             # NEW: API endpoint tests
│   └── test_trade_persistence.py      # NEW: Database integration tests
└── fixtures/
    └── trade_fixtures.py              # NEW: Test data fixtures
```

**Structure Decision**: Web application structure (Option 2) is already in place. This feature extends the existing backend with new modules for trade tracking. The structure follows the established pattern from specs/004-postgresql-metadata-storage and specs/005-webui-foundation, adding trade-specific components to each layer (API, models, services, database, UI).

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations detected. All Constitution Check gates passed without requiring justification.

---

## Phase Completion Summary

### ✅ Phase 0: Research & Architecture (COMPLETED)

**Artifacts Generated**:
- `research.md` - Comprehensive technical decisions and Nautilus Trader integration patterns

**Key Research Findings**:
1. **Trade Capture Strategy**: Use Nautilus Trader's `trader.generate_fills_report()` API after backtest completion
2. **Data Source**: `FillReport` objects provide complete trade data (prices, quantities, commissions, timestamps)
3. **Database Design**: Extend existing `backtest_runs` table with 1:N `trades` relationship
4. **Precision Requirements**: PostgreSQL NUMERIC (Python Decimal) for all financial values
5. **Performance Approach**: Bulk inserts, database indexes, server-side pagination, on-demand calculations

**Technology Validation**: ✅ All required dependencies already in use (no new deps needed)

### ✅ Phase 1: Design & Contracts (COMPLETED)

**Artifacts Generated**:
1. `data-model.md` - Complete entity definitions with:
   - Trade entity (database schema, Pydantic models, validation rules)
   - EquityCurvePoint (computed entity)
   - DrawdownMetrics (computed entity)
   - TradeStatistics (computed entity)
   - Alembic migration script
   - Calculation algorithms for profit/loss, equity curve, drawdowns

2. `contracts/trades-api.yaml` - OpenAPI 3.0 specification with 6 endpoints:
   - `GET /backtests/{id}/trades` - Paginated trade list with sorting/filtering
   - `GET /backtests/{id}/trades/{trade_id}` - Single trade details
   - `GET /backtests/{id}/equity-curve` - Equity curve generation
   - `GET /backtests/{id}/drawdown` - Drawdown metrics
   - `GET /backtests/{id}/statistics` - Trade statistics
   - `GET /backtests/{id}/export` - CSV/JSON export

3. `quickstart.md` - Developer implementation guide with:
   - Step-by-step TDD workflow (8 steps)
   - Database migration instructions
   - Code examples for all layers (models, services, APIs, UI)
   - Testing strategies and validation procedures
   - Troubleshooting guide

**Design Highlights**:
- Trade entity with 20+ fields capturing complete trade lifecycle
- Calculated fields: profit_loss, profit_pct, holding_period_seconds
- 4 database indexes for query optimization
- Pydantic models for validation at API boundaries
- Server-side pagination (20/50/100 trades per page)
- HTMX integration for responsive UI updates

### ✅ Agent Context Updated

- Updated `CLAUDE.md` with new technology stack information
- Added: Nautilus Trader (FillReport, Position APIs), trade tracking patterns
- Preserved manual additions between markers

---

## Ready for Phase 2: Task Generation

**Command to run next**:
```bash
/tasks
```

This will generate `tasks.md` with:
- Dependency-ordered implementation tasks
- Test-first task descriptions
- Acceptance criteria for each task
- Time estimates
- Prerequisites and blockers

**Estimated Implementation Effort**:
- Database layer: 1-2 hours
- Service layer: 2-3 hours
- API endpoints: 2-3 hours
- UI integration: 2-3 hours
- Analytics (equity curve, drawdowns, statistics): 3-4 hours
- Testing & validation: 2-3 hours
- **Total**: ~12-18 hours for complete implementation

**Implementation Order** (recommended):
1. Database migration and models
2. Trade capture from Nautilus Trader
3. Basic API endpoints (list trades)
4. Equity curve generation
5. UI integration (trades table + chart)
6. Analytics endpoints (drawdown, statistics)
7. Advanced features (export, filtering)

---

## Related Specifications

This feature builds upon:
- **specs/004-postgresql-metadata-storage**: Database infrastructure and async SQLAlchemy patterns
- **specs/005-webui-foundation**: FastAPI + Jinja2 + HTMX + Tailwind CSS patterns
- **specs/007-backtest-detail-view**: Existing backtest details page for UI integration
- **specs/008-chart-apis**: Chart integration patterns for equity curve visualization

---

**Plan Status**: ✅ COMPLETE - All gates passed, all Phase 0-1 artifacts generated, ready for task generation
