# Implementation Plan: PostgreSQL Metadata Storage

**Branch**: `004-postgresql-metadata-storage` | **Date**: 2025-01-25 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/004-postgresql-metadata-storage/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Enable users to persist, retrieve, and compare backtesting results across multiple executions through automatic PostgreSQL storage. Every backtest execution will automatically save complete metadata (strategy configuration, date ranges, instruments) and performance metrics (returns, Sharpe ratio, drawdown, trading statistics) to the database. Users can then list, filter, compare, and reproduce past backtests without re-running them, accelerating parameter optimization and strategy research.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: SQLAlchemy 2.0 (async), Alembic, Pydantic 2.5+, asyncpg, PostgreSQL 16+
**Storage**: PostgreSQL 16+ with async connection pooling
**Testing**: pytest with pytest-asyncio for async database tests
**Target Platform**: Linux/macOS server environments
**Project Type**: Single project (CLI extension to existing codebase)
**Performance Goals**:
- <100ms single backtest retrieval by ID
- <200ms list queries for 20 recent backtests
- <2s comparison queries for up to 10 backtests
- Support 10,000+ stored backtest runs without degradation

**Constraints**:
- Must not disrupt existing backtest execution flow
- All three existing strategies (SMA, RSI Mean Reversion, Momentum) must continue working
- No breaking changes to existing CLI commands
- Automatic persistence must be transparent to users

**Scale/Scope**:
- 2 new database tables (backtest_runs, performance_metrics)
- 5-7 new CLI commands for querying and comparison
- Integration with existing backtest_runner.py
- Expected storage: ~10-50 MB for 10,000 runs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### ✅ Test-Driven Development (NON-NEGOTIABLE)
- **Status**: PASS
- **Evidence**: All code will be written test-first following Red-Green-Refactor cycle
- **Plan**: Write failing tests for database models, persistence logic, and CLI commands before implementation

### ✅ Simplicity First (KISS/YAGNI)
- **Status**: PASS
- **Evidence**: Storing only execution metadata and aggregate metrics, not individual trades
- **Design Choices**:
  - No trade-level storage (out of scope)
  - No equity curve snapshots (out of scope)
  - No parameter sweep tracking (out of scope)
  - Simple two-table schema (backtest_runs + performance_metrics)

### ✅ FastAPI-First Architecture
- **Status**: N/A (CLI-only feature, no API endpoints)
- **Evidence**: Feature is CLI-focused with Click framework
- **Note**: If future web interface needed, FastAPI can be added later

### ✅ Type Safety & Documentation
- **Status**: PASS
- **Evidence**:
  - All functions will use type hints (PEP 484)
  - Pydantic models for data validation
  - Google-style docstrings with examples
  - Mypy validation required before commit

### ✅ Dependency Discipline
- **Status**: PASS
- **Evidence**: All dependencies already present in pyproject.toml:
  - SQLAlchemy 2.0.43
  - Alembic 1.16.5
  - asyncpg 0.30.0
  - Pydantic 2.11.9
- **Plan**: No new dependencies required - use UV for any future additions

### ✅ Fail Fast & Observable
- **Status**: PASS
- **Evidence**:
  - Input validation at system boundaries with Pydantic
  - Structured logging with structlog (already in dependencies)
  - Clear error messages for database connection failures
  - Validation of metrics before storage (NaN/Infinity checks)

### ✅ DRY & Modular Design
- **Status**: PASS
- **Evidence**:
  - Functions <50 lines
  - Classes <100 lines
  - Files <500 lines
  - Reusable database session management
  - Shared query utilities

### 🔍 Additional Gates

#### File Size Limits
- **Status**: PASS
- **Plan**: All files will stay under 500 lines by splitting:
  - `src/db/models/backtest.py` - Database models
  - `src/db/repositories/backtest_repository.py` - Data access layer
  - `src/cli/commands/backtest_history.py` - CLI commands

#### Code Quality
- **Status**: PASS
- **Plan**: Pre-commit hooks will enforce:
  - `ruff format .` - Code formatting
  - `ruff check .` - Linting
  - `mypy .` - Type checking
  - 80%+ test coverage on critical paths

#### Security
- **Status**: PASS
- **Evidence**:
  - Database credentials from environment variables only
  - Parameterized queries via SQLAlchemy (prevents SQL injection)
  - No sensitive data in logs
  - Input validation with Pydantic

## Project Structure

### Documentation (this feature)

```text
specs/004-postgresql-metadata-storage/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (database schema DDL)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── models/
│   ├── backtest_result.py      # (existing) Extend for persistence
│   ├── backtest_metadata.py    # (new) Metadata model
│   └── performance_metrics.py  # (new) Metrics model
├── db/
│   ├── __init__.py
│   ├── session.py              # (existing) Database session management
│   ├── models/
│   │   ├── __init__.py
│   │   ├── backtest_run.py     # (new) BacktestRun SQLAlchemy model
│   │   └── metrics.py          # (new) PerformanceMetrics SQLAlchemy model
│   └── repositories/
│       ├── __init__.py
│       └── backtest_repository.py  # (new) Data access layer
├── services/
│   ├── backtest_persistence.py # (new) Persistence service
│   └── backtest_query.py       # (new) Query/retrieval service
├── cli/
│   └── commands/
│       ├── run.py              # (existing) Extend for auto-save
│       ├── history.py          # (new) List/filter backtests
│       ├── compare.py          # (new) Compare backtests
│       └── reproduce.py        # (new) Re-run from stored config
└── core/
    └── backtest_runner.py      # (existing) Extend to trigger persistence

tests/
├── unit/
│   ├── test_backtest_metadata.py
│   ├── test_performance_metrics.py
│   ├── test_backtest_repository.py
│   └── test_persistence_service.py
├── integration/
│   ├── test_database_integration.py
│   ├── test_cli_history.py
│   ├── test_cli_compare.py
│   └── test_cli_reproduce.py
└── contract/
    └── test_database_schema.py

alembic/
├── versions/
│   └── 001_create_backtest_tables.py  # (new) Migration for schema
└── env.py              # (existing) Alembic configuration
```

**Structure Decision**: Single project structure chosen as this feature extends the existing CLI application with database persistence. No web/mobile components required. The existing `src/` directory structure is maintained with new modules for database models, repositories, and services following the established patterns.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | All gates passed |

## Research Topics (Phase 0)

The following areas require research before design can proceed:

### 1. SQLAlchemy Async Best Practices
**Question**: How to properly implement async SQLAlchemy 2.0 with asyncpg for optimal performance?
**Why**: Need to understand connection pooling, session management, and async context managers
**Expected Output**: Patterns for async database operations, session lifecycle, error handling

### 2. Nautilus Trader Backtest Result Structure
**Question**: What is the complete structure of backtest results returned by Nautilus Trader?
**Why**: Need to extract all relevant metrics and metadata for storage
**Expected Output**: List of available metrics, data types, and how to access them

### 3. Alembic Migration Strategy
**Question**: How to create and manage database migrations with Alembic in an async context?
**Why**: Need to version schema changes properly
**Expected Output**: Migration template, rollback strategy, version management

### 4. Configuration Snapshot Serialization
**Question**: How to serialize strategy configurations (YAML/dict) for database storage?
**Why**: Must preserve exact configuration for reproducibility
**Expected Output**: Serialization format (JSONB vs TEXT), validation approach

### 5. CLI Command Design Patterns
**Question**: What are the best practices for Click commands with database interactions?
**Why**: Need to handle database connections properly in CLI context
**Expected Output**: Context management patterns, error handling, progress indicators

### 6. Query Performance Optimization
**Question**: What indexes and query patterns will ensure <100ms retrieval performance?
**Why**: Performance requirements demand optimized queries
**Expected Output**: Index strategy, query patterns, pagination approach

### 7. Concurrent Backtest Handling
**Question**: How to safely handle concurrent backtest executions writing to the database?
**Why**: Users may run multiple backtests simultaneously
**Expected Output**: Transaction isolation strategy, conflict resolution

## Next Steps

This plan document will be completed through the following phases:

1. **Phase 0 (Next)**: Research phase
   - Generate `research.md` with findings for all topics above
   - Resolve all "NEEDS CLARIFICATION" items
   - Document technology decisions and patterns

2. **Phase 1**: Design phase
   - Generate `data-model.md` with database schema
   - Create DDL schema in `contracts/`
   - Generate `quickstart.md` with setup instructions
   - Update agent context with new technologies

3. **Phase 2**: Task generation (separate command)
   - Run `/speckit.tasks` to generate actionable tasks
   - Task breakdown will follow TDD workflow
   - Each task will have clear acceptance criteria

## Implementation Notes

- All database operations must use async/await patterns
- Configuration snapshots stored as JSONB for queryability
- Metrics validated before storage (range checks for NaN/Infinity)
- Soft deletes not required - hard deletes acceptable for backtest records
- Cascade deletes configured: deleting a run deletes its metrics
- Records are immutable once created - no updates allowed
- Each backtest gets a UUID identifier generated at execution time
- Re-runs reference original run ID in metadata
