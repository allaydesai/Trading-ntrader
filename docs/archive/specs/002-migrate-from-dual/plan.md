# Implementation Plan: Parquet-Only Market Data Storage

**Branch**: `002-migrate-from-dual` | **Date**: 2025-01-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/Users/allay/dev/Trading-ntrader/specs/002-migrate-from-dual/spec.md`

## Execution Flow (/plan command scope)
```
1. Load feature spec from Input path
   → If not found: ERROR "No feature spec at {path}"
2. Fill Technical Context (scan for NEEDS CLARIFICATION)
   → Detect Project Type from context (web=frontend+backend, mobile=app+api)
   → Set Structure Decision based on project type
3. Evaluate Constitution Check section below
   → If violations exist: Document in Complexity Tracking
   → If no justification possible: ERROR "Simplify approach first"
   → Update Progress Tracking: Initial Constitution Check
4. Execute Phase 0 → research.md
   → If NEEDS CLARIFICATION remain: ERROR "Resolve unknowns"
5. Execute Phase 1 → contracts, data-model.md, quickstart.md, CLAUDE.md
6. Re-evaluate Constitution Check section
   → If new violations: Refactor design, return to Phase 1
   → Update Progress Tracking: Post-Design Constitution Check
7. Plan Phase 2 → Describe task generation approach (DO NOT create tasks.md)
8. STOP - Ready for /tasks command
```

**IMPORTANT**: The /plan command STOPS at step 7. Phases 2-4 are executed by other commands:
- Phase 2: /tasks command creates tasks.md
- Phase 3-4: Implementation execution (manual or via tools)

## Summary

Migrate from dual storage (PostgreSQL + Parquet) to Parquet-only architecture for market data. PostgreSQL will be deprecated for market data storage while preserving it for future metadata use. The system will use Nautilus Trader's ParquetDataCatalog as the single source of truth, automatically fetching missing data from IBKR when needed.

**Key Benefits**: Eliminates data synchronization complexity, reduces storage overhead, maintains single source of truth, enables automatic data fetching workflow.

## Technical Context

**Language/Version**: Python 3.11 (specified in .python-version)
**Primary Dependencies**:
- Nautilus Trader 1.190.0+ with Interactive Brokers adapter
- Pydantic 2.11.9+ for validation
- SQLAlchemy 2.0.43+ for PostgreSQL deprecation phase
- Click 8.2.1+ for CLI commands
- Pandas 2.3.2+ for data manipulation

**Package Manager**: UV (exclusive, never edit pyproject.toml directly)
**Storage**:
- Primary: Parquet files via Nautilus ParquetDataCatalog in ./data/catalog/
- Deprecated: PostgreSQL (market_data table marked for future removal)
- Structure: ./data/catalog/{INSTRUMENT_ID}/{BAR_TYPE}/YYYY-MM-DD.parquet

**Testing**: pytest with minimum 80% coverage (pytest 8.4.2+)
**Target Platform**: Linux/macOS development, Docker container deployment
**Project Type**: CLI application (single backend project)
**Performance Goals**:
- Data load from Parquet: <1s for 1 year of 1-minute bars
- IBKR fetch operations: Respect 50 req/sec rate limit
- Memory usage: <500MB for typical backtest workload

**Constraints**:
- Files <500 lines, functions <50 lines, classes <100 lines
- IBKR rate limit: 50 requests/second (already implemented in IBKRHistoricalClient)
- Disk I/O: Sequential reads from Parquet files (columnar storage)

**Scale/Scope**:
- Support multiple instruments (10-50 typical portfolio)
- Multi-year historical data per instrument
- Daily partitioned Parquet files for efficient range queries
- Metadata tracking for fast availability checks

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Simplicity (KISS/YAGNI)**:
- ✅ Files under 500 lines - Will split services if needed
- ✅ Functions under 50 lines, classes under 100 lines - Enforced by design
- ✅ Using framework directly - Nautilus ParquetDataCatalog used as-is
- ✅ Single data model - Bar objects from Nautilus, no custom DTOs
- ✅ Avoiding clever code - Direct catalog read/write operations
- ✅ Features built only when needed - Core migration only, no premature optimizations

**Test-Driven Development (NON-NEGOTIABLE)**:
- ✅ Tests written BEFORE implementation - Will follow Red-Green-Refactor
- ✅ Each feature starts with failing test - Contract tests first
- ✅ Minimum 80% coverage on critical paths - Required for data catalog operations
- ✅ Using pytest with descriptive test names - test_catalog_*.py convention
- ✅ Test file naming: test_<module>.py - Standard naming enforced
- ✅ FORBIDDEN: Implementation before test - Gate checks in place

**FastAPI Architecture**:
- ⚠️ Not applicable - CLI application, no API endpoints for this feature
- Note: Future REST API will use FastAPI when added

**Type Safety & Documentation**:
- ✅ All functions have type hints (PEP 484) - Required
- ✅ Mypy validation passing - Pre-commit hook configured
- ✅ Google-style docstrings with examples - Template provided
- ✅ Complex logic has inline comments - "# Reason:" prefix enforced
- ✅ README.md for each module - Will update existing docs

**Dependency Management**:
- ✅ Using UV exclusively - Never edit pyproject.toml directly
- ✅ Dependencies actively maintained - Nautilus Trader updated regularly
- ✅ Production dependencies pinned - All versions specified in pyproject.toml
- ✅ Dev/test/prod dependencies separated - [dependency-groups] in pyproject.toml

**Observability & Error Handling**:
- ✅ Structured logging - Will add structlog for catalog operations
- ✅ Correlation IDs for request tracing - Per-backtest run IDs
- ✅ Fail fast with early validation - Data availability checks before loading
- ✅ Custom exception classes - DataNotFoundError, CatalogError hierarchy
- ✅ Never bare except: clauses - Specific exception handling required

**Performance Standards**:
- ✅ Data load time <1s for typical queries - Parquet columnar storage advantage
- ✅ Memory usage <500MB typical - Lazy loading from catalog
- ✅ Async/await for I/O - IBKR client already async

**Initial Assessment**: PASS - All constitutional requirements align with Parquet-first design

## Project Structure

### Documentation (this feature)
```
specs/002-migrate-from-dual/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)
```
# Python Backend Project Structure (per Constitution)
src/
├── services/
│   ├── data_catalog.py         # NEW: Parquet catalog service (facade)
│   ├── data_fetcher.py         # EXISTING: IBKR historical data fetcher
│   ├── database_repository.py  # DEPRECATED: Mark for future removal
│   ├── data_service.py         # REFACTOR: Remove PostgreSQL dependency
│   └── csv_loader.py           # REFACTOR: Write directly to Parquet
├── models/
│   ├── market_data.py          # KEEP: Pydantic models (deprecate SQLAlchemy table)
│   └── catalog_metadata.py     # NEW: Catalog availability tracking
├── cli/
│   └── commands/
│       ├── backtest.py         # REFACTOR: Use catalog for data loading
│       └── data.py             # REFACTOR: Add catalog inspection commands
├── utils/
│   └── data_wrangler.py        # REFACTOR: Remove PostgreSQL operations
└── db/
    └── session.py              # KEEP: For future metadata tracking

tests/                          # Test files mirror src structure
├── test_data_catalog.py        # NEW: Catalog service tests
├── test_csv_loader.py          # REFACTOR: Update for Parquet output
└── integration/
    └── test_backtest_catalog.py # NEW: End-to-end catalog workflow

data/                           # Data directory
└── catalog/                    # Parquet data catalog
    └── {INSTRUMENT_ID}/        # e.g., AAPL.NASDAQ/
        └── {BAR_TYPE}/         # e.g., 1_MIN/
            └── YYYY-MM-DD.parquet # Daily partitions

# Root files
.python-version                 # Python 3.11
pyproject.toml                  # Managed by UV only
README.md                       # UPDATE: Document new Parquet workflow
```

**Structure Decision**: Single CLI application (Option 1) - No web or mobile components

## Phase 0: Outline & Research

**Research Tasks**:

1. **Nautilus ParquetDataCatalog API**:
   - Best practices for catalog initialization and configuration
   - Querying data availability without full data load
   - Handling concurrent read/write operations
   - Metadata management strategies

2. **Parquet Performance Optimization**:
   - Daily vs hourly vs monthly partitioning trade-offs
   - Optimal file sizes for trading data (balance between too many small files vs large files)
   - Column pruning and predicate pushdown for range queries

3. **PostgreSQL to Parquet Migration**:
   - Export strategy for existing market_data table
   - Data validation during migration (checksum/row count verification)
   - Handling timezone conversions consistently

4. **Error Handling Patterns**:
   - IBKR connection failures during auto-fetch
   - Partial data fetch recovery strategies
   - Corrupted Parquet file detection and recovery

5. **Testing Strategies**:
   - Mocking ParquetDataCatalog for unit tests
   - Generating test Parquet files with known data
   - Integration test patterns for IBKR → Parquet → Backtest flow

**Output**: research.md with all technical decisions documented

## Phase 1: Design & Contracts

**Deliverables**:

1. **data-model.md**: Document entities and relationships
   - ParquetCatalog (facade over Nautilus catalog)
   - CatalogMetadata (availability tracking index)
   - Bar (Nautilus native type, no custom DTOs)
   - FetchRequest (data fetching state machine)

2. **contracts/**: No API contracts (CLI application)
   - Instead: CLI command specifications
   - Input/output formats for data commands
   - Error message formats and exit codes

3. **Contract Tests**:
   - CLI command integration tests with expected outputs
   - Test data availability before implementation
   - Verify error messages match specifications

4. **quickstart.md**: Step-by-step validation
   - Import CSV → Parquet
   - Run backtest with cached data
   - Trigger auto-fetch for missing data
   - Inspect catalog availability

5. **CLAUDE.md Update**: Add Parquet-first patterns
   - Use context7 for Nautilus Trader documentation
   - Catalog service usage examples
   - Common troubleshooting procedures

**Output**: All Phase 1 artifacts in specs/002-migrate-from-dual/

## Phase 2: Task Planning Approach

*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy**:
1. Load `/templates/tasks-template.md` as base
2. Generate TDD-ordered tasks from Phase 1 design
3. Each user story → failing integration test task
4. Each service method → unit test task (before implementation)
5. Implementation tasks grouped by dependency layers

**Task Categories**:
- **Infrastructure Tasks** [P]: Create catalog directory structure, add Parquet dependencies
- **Test Skeleton Tasks** [P]: Write failing tests for all new services
- **Core Service Tasks**: Implement ParquetCatalog service (data_catalog.py)
- **Migration Tasks**: CSV loader Parquet output, database deprecation markers
- **Integration Tasks**: Wire catalog into backtest_runner, update CLI commands
- **Validation Tasks**: Run quickstart.md scenarios, performance benchmarks

**Ordering Strategy**:
- Infrastructure → Test skeletons → Core services → Integrations → Validation
- TDD order: Each test task immediately followed by its implementation task
- Mark [P] for parallel execution (independent modules/files)
- Sequential for dependent components (catalog before backtest integration)

**Estimated Output**: 20-25 numbered tasks in tasks.md

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation
*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)
**Phase 4**: Implementation (execute tasks.md following TDD)
**Phase 5**: Validation (run tests, execute quickstart.md, verify migration)

## Complexity Tracking
*Fill ONLY if Constitution Check has violations that must be justified*

No violations requiring justification. Design aligns with constitutional principles:
- Simplicity: Using Nautilus catalog directly, no wrapper complexity
- TDD: All tasks follow test-first approach
- Type Safety: Full type hints, mypy validation
- Dependency Discipline: UV exclusive, pinned versions

## Progress Tracking
*This checklist is updated during execution flow*

**Phase Status**:
- [x] Phase 0: Research complete (/plan command)
- [x] Phase 1: Design complete (/plan command)
- [x] Phase 2: Task planning complete (/plan command - describe approach only)
- [ ] Phase 3: Tasks generated (/tasks command)
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [x] Initial Constitution Check: PASS
- [x] Post-Design Constitution Check: PASS
- [x] All NEEDS CLARIFICATION resolved
- [x] Complexity deviations documented (none required)

**Artifacts Generated**:
- [x] plan.md - Implementation plan with technical context
- [x] research.md - Research findings from Nautilus Trader documentation
- [x] data-model.md - Entity definitions (Bar, InstrumentId, ParquetDataCatalog, etc.)
- [x] quickstart.md - Step-by-step validation scenarios
- [x] contracts/cli_commands.md - CLI command specifications
- [x] CLAUDE.md - Updated with Parquet-first technical context

---
*Based on Python Backend Development Constitution v1.0.1 - See `.specify/memory/constitution.md`*
