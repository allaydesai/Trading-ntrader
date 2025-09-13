# Implementation Plan: Nautilus Trader Backtesting System with IBKR Integration


**Branch**: `001-docs-prd-md` | **Date**: 2025-01-13 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-docs-prd-md/spec.md`

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
5. Execute Phase 1 → contracts, data-model.md, quickstart.md, agent-specific template file (e.g., `CLAUDE.md` for Claude Code, `.github/copilot-instructions.md` for GitHub Copilot, or `GEMINI.md` for Gemini CLI).
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
Build a production-grade algorithmic trading backtesting system using Nautilus Trader framework with Interactive Brokers data integration. **Phase 1 focuses on CLI implementation** for running backtests, managing strategies, and generating reports. The system enables quantitative traders to validate trading strategies on historical market data with realistic commission/slippage modeling. The REST API defined in contracts will be implemented in a future phase when the web frontend is developed.

## Technical Context
**Language/Version**: Python 3.11+ (specified in .python-version)
**Primary Dependencies**: nautilus_trader[ib], FastAPI, Pydantic, SQLAlchemy, TimescaleDB, pandas, numpy
**Package Manager**: UV (exclusive, never edit pyproject.toml directly)  
**Storage**: PostgreSQL with TimescaleDB extension for time-series data, Redis for caching
**Testing**: pytest with minimum 80% coverage, pytest-asyncio for async tests
**Target Platform**: Linux server with Docker containerization
**Project Type**: single (CLI application first, API deferred to future phase)
**Performance Goals**: <1s backtest initialization, <5s for simple strategies on 1-year data
**Constraints**: <2GB memory for typical backtests, handle IBKR rate limits (50 req/sec)
**Scale/Scope**: Single user system, <500 lines per file, <100 lines per class, support 1M+ data points

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Simplicity (KISS/YAGNI)**:
- Files under 500 lines? (split if approaching limit)
- Functions under 50 lines, classes under 100 lines?
- Using framework directly? (no wrapper classes)
- Single data model? (no DTOs unless serialization differs)
- Avoiding clever code? (obvious > clever)
- Features built only when needed? (no speculation)

**Test-Driven Development (NON-NEGOTIABLE)**:
- Tests written BEFORE implementation? (Red-Green-Refactor)
- Each feature starts with failing test?
- Minimum 80% coverage on critical paths?
- Using pytest with descriptive test names?
- Test file naming: test_<module>.py?
- FORBIDDEN: Implementation before test, skipping RED phase

**FastAPI Architecture**:
- All APIs using FastAPI with Pydantic?
- Automatic OpenAPI documentation?
- Dependency injection for shared logic?
- Async/await for all I/O operations?
- Routers for logical separation?

**Type Safety & Documentation**:
- All functions have type hints (PEP 484)?
- Mypy validation passing?
- Google-style docstrings with examples?
- Complex logic has inline comments with "# Reason:" prefix?
- README.md for each module?

**Dependency Management**:
- Using UV exclusively? (never edit pyproject.toml directly)
- Dependencies actively maintained? (commits within 6 months)
- Production dependencies pinned to specific versions?
- Dev/test/prod dependencies separated?

**Observability & Error Handling**:
- Structured logging with structlog?
- Correlation IDs for request tracing?
- Fail fast with early validation?
- Custom exception classes for domain errors?
- Never bare except: clauses?

**Performance Standards**:
- API response time <200ms for simple, <1s for complex?
- Database queries <100ms single, <500ms lists?
- Memory usage <500MB typical workload?
- Async/await for all I/O operations?

## Project Structure

### Documentation (this feature)
```
specs/[###-feature]/
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
├── api/          # FastAPI routers and endpoints
├── core/         # Core business logic
├── models/       # Pydantic models and schemas
├── services/     # Business services
├── db/           # Database models and migrations
└── utils/        # Shared utilities

tests/            # Test files mirror src structure
├── test_*.py     # Test files with test_ prefix
├── conftest.py   # Pytest fixtures
└── __init__.py

scripts/          # CLI tools and automation
├── __init__.py
└── *.py

docs/             # Documentation and ADRs
├── architecture/
│   └── decisions/
└── technical-debt.md

docker/           # Docker configurations
├── Dockerfile
└── docker-compose.yml

# Root files
.python-version   # Python 3.11+ specification
pyproject.toml    # Managed by UV only
.env.example      # Environment variables template
README.md         # Project overview and setup
```

**Structure Decision**: Option 1 (Single Python Backend Project) - Pure backend service with CLI and API interfaces

## Phase 0: Outline & Research
1. **Extract unknowns from Technical Context** above:
   - For each NEEDS CLARIFICATION → research task
   - For each dependency → best practices task
   - For each integration → patterns task

2. **Generate and dispatch research agents**:
   ```
   For each unknown in Technical Context:
     Task: "Research {unknown} for {feature context}"
   For each technology choice:
     Task: "Find best practices for {tech} in {domain}"
   ```

3. **Consolidate findings** in `research.md` using format:
   - Decision: [what was chosen]
   - Rationale: [why chosen]
   - Alternatives considered: [what else evaluated]

**Output**: research.md with all NEEDS CLARIFICATION resolved

## Phase 1: Design & Contracts
*Prerequisites: research.md complete*

1. **Extract entities from feature spec** → `data-model.md`:
   - Entity name, fields, relationships
   - Validation rules from requirements
   - State transitions if applicable

2. **Generate API contracts** from functional requirements:
   - For each user action → endpoint
   - Use standard REST/GraphQL patterns
   - Output OpenAPI/GraphQL schema to `/contracts/`

3. **Generate contract tests** from contracts:
   - One test file per endpoint
   - Assert request/response schemas
   - Tests must fail (no implementation yet)

4. **Extract test scenarios** from user stories:
   - Each story → integration test scenario
   - Quickstart test = story validation steps

5. **Update agent file incrementally** (O(1) operation):
   - Run `/scripts/bash/update-agent-context.sh claude` for your AI assistant
   - If exists: Add only NEW tech from current plan
   - Preserve manual additions between markers
   - Update recent changes (keep last 3)
   - Keep under 150 lines for token efficiency
   - Output to repository root

**Output**: data-model.md, /contracts/*, failing tests, quickstart.md, agent-specific file

## Phase 2: Task Planning Approach
*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy**:
- Load `/templates/tasks-template.md` as base
- Generate tasks from Phase 1 design docs (contracts, data model, quickstart)
- Each contract endpoint → contract test task [P]
- Each entity → model creation task [P]
- Each user story → integration test task
- Implementation tasks to make tests pass

**Specific Task Categories**:

1. **Database & Models** (Tasks 1-8):
   - Set up PostgreSQL with TimescaleDB
   - Create SQLAlchemy models for each entity
   - Write Alembic migrations
   - Create Pydantic schemas

2. **Test Infrastructure** (Tasks 9-15):
   - Write failing tests for IBKR connection
   - Create strategy validation tests
   - Define performance calculation tests
   - Set up pytest fixtures for trading scenarios

3. **CLI Implementation** (Tasks 16-22):
   - Create Click-based CLI structure
   - Implement strategy commands
   - Build backtest runner commands
   - Add data management commands
   - Create report generation commands

4. **Core Services** (Tasks 23-29):
   - Implement IBKR data client with rate limiting
   - Create CSV data loader
   - Build strategy implementations (SMA, mean reversion, momentum)
   - Develop backtest runner with Nautilus Trader

5. **Integration & Polish** (Tasks 30-35):
   - Add progress bars with Rich library
   - Implement interactive mode
   - Create configuration management
   - Add error handling and recovery
   - Build result caching system

6. **Reporting & Analytics** (Tasks 36-40):
   - Generate HTML reports with embedded charts
   - Create CSV/JSON exporters
   - Calculate performance metrics
   - Implement benchmark comparisons

**Ordering Strategy**:
- TDD order: Tests ALWAYS before implementation
- Dependency order: Database → Models → Services → API
- Mark [P] for parallel execution (independent files)
- Critical path: Database setup → IBKR connection → Backtest engine

**Estimated Output**: 40-45 numbered, ordered tasks in tasks.md

**CLI Focus**: All user interactions through command-line interface
**API Deferred**: REST API endpoints saved for future web frontend phase

**Task Validation**:
- Each task must be completable in <2 hours
- Each task must have clear acceptance criteria
- Test tasks must fail initially (Red phase of TDD)
- Implementation tasks make specific tests pass (Green phase)

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation
*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)  
**Phase 4**: Implementation (execute tasks.md following constitutional principles)  
**Phase 5**: Validation (run tests, execute quickstart.md, performance validation)

## Complexity Tracking
*Fill ONLY if Constitution Check has violations that must be justified*

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |


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

---
*Based on Python Backend Development Constitution v1.0.0 - See `.specify/memory/constitution.md`*