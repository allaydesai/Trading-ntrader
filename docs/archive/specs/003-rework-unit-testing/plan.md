# Implementation Plan: Unit Testing Architecture Refactor

**Branch**: `003-rework-unit-testing` | **Date**: 2025-01-22 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-rework-unit-testing/spec.md`

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
Refactor existing test suite to implement proper test pyramid architecture with three distinct categories: unit tests (pure Python business logic, no Nautilus dependencies), component tests (using test doubles for lightweight strategy testing), and integration tests (real Nautilus components with process isolation). This refactoring will improve test execution speed by 50%, enable rapid local development with sub-second feedback loops, and prevent C extension crashes from cascading through the test suite.

## Technical Context
**Language/Version**: Python 3.11+ (specified in .python-version)
**Primary Dependencies**: pytest, pytest-asyncio, pytest-xdist, pytest-forked, pytest-mock, nautilus_trader
**Package Manager**: UV (exclusive, never edit pyproject.toml directly)
**Storage**: N/A (test infrastructure refactoring)
**Testing**: pytest with minimum 80% coverage on critical trading logic paths
**Target Platform**: Linux/macOS development environments and GitHub Actions CI/CD
**Project Type**: single (Python backend backtesting system)
**Performance Goals**: Unit tests <100ms each, full unit suite <5s, integration tests <2min with 4 workers
**Constraints**: Test pyramid distribution (50% unit, 25% component, 20% integration, 5% e2e), process isolation for C extensions
**Scale/Scope**: Existing test suite refactoring, <500 lines per file, <100 lines per class, test files mirror src structure

## Constitution Check
*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Simplicity (KISS/YAGNI)**:
- ✅ Files under 500 lines? (test fixtures and test doubles will be modular)
- ✅ Functions under 50 lines, classes under 100 lines? (simple test doubles, focused fixtures)
- ✅ Using framework directly? (pytest plugins used as-is, no wrappers)
- ✅ Single data model? (reusable MarketScenario dataclasses for consistency)
- ✅ Avoiding clever code? (straightforward test doubles, explicit fixture cleanup)
- ✅ Features built only when needed? (only essential test infrastructure, no speculative abstractions)

**Test-Driven Development (NON-NEGOTIABLE)**:
- ✅ Tests written BEFORE implementation? (This IS the test infrastructure - meta-TDD applies: contract tests verify test doubles before use)
- ✅ Each feature starts with failing test? (Contract tests will fail until test doubles implement protocols correctly)
- ✅ Minimum 80% coverage on critical paths? (Applies to production code tested by new structure)
- ✅ Using pytest with descriptive test names? (Enhanced with markers and clear categorization)
- ✅ Test file naming: test_<module>.py? (Maintained in new structure)
- ✅ FORBIDDEN: Implementation before test - Contract tests must verify test doubles match real implementations

**FastAPI Architecture**:
- N/A (No API changes, test infrastructure only)

**Type Safety & Documentation**:
- ✅ All functions have type hints (PEP 484)? (Test doubles and fixtures will be fully typed)
- ✅ Mypy validation passing? (All new code type-checked)
- ✅ Google-style docstrings with examples? (Test fixtures and helpers documented)
- ✅ Complex logic has inline comments with "# Reason:" prefix? (Isolation patterns explained)
- ✅ README.md for each module? (Testing guidelines documentation created)

**Dependency Management**:
- ✅ Using UV exclusively? (uv add --dev for pytest plugins)
- ✅ Dependencies actively maintained? (pytest-xdist, pytest-forked are standard, well-maintained tools)
- ✅ Production dependencies pinned to specific versions? (Test dependencies pinned)
- ✅ Dev/test/prod dependencies separated? (All test tools in dev dependencies)

**Observability & Error Handling**:
- ✅ Structured logging with structlog? (Test output uses pytest's built-in logging)
- ⚠️ Correlation IDs for request tracing? (N/A for tests, but test isolation ensures traceability)
- ✅ Fail fast with early validation? (Cleanup fixtures validate state between tests)
- ✅ Custom exception classes for domain errors? (Test doubles raise meaningful exceptions)
- ✅ Never bare except: clauses? (All exception handling in fixtures is specific)

**Performance Standards**:
- ✅ API response time <200ms for simple, <1s for complex? (N/A, but unit tests <100ms each)
- ✅ Database queries <100ms single, <500ms lists? (N/A, test doubles don't touch DB)
- ✅ Memory usage <500MB typical workload? (Test suite runs within memory constraints)
- ✅ Async/await for all I/O operations? (Async tests use proper event loop fixtures)

## Project Structure

### Documentation (this feature)
```
specs/003-rework-unit-testing/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)
```
# Test Infrastructure Structure (new)
tests/
├── unit/                # Pure Python tests, no Nautilus imports
│   ├── test_logic_*.py  # Trading logic, algorithms, calculations
│   └── conftest.py      # Unit test fixtures
│
├── component/           # Test doubles, strategy behavior verification
│   ├── test_strategy_*.py  # Strategy interaction tests
│   ├── conftest.py      # Component fixtures and test doubles
│   └── doubles/         # Test double implementations
│       ├── __init__.py
│       ├── test_engine.py    # TestTradingEngine
│       ├── test_order.py     # TestOrder
│       └── test_position.py  # TestPosition
│
├── integration/         # Real Nautilus components, subprocess isolated
│   ├── test_backtest_*.py  # BacktestEngine integration tests
│   └── conftest.py      # Integration fixtures with cleanup
│
├── e2e/                 # Full system tests
│   └── test_scenarios_*.py  # End-to-end trading scenarios
│
├── fixtures/            # Shared test utilities
│   ├── __init__.py
│   ├── event_loop.py    # Isolated event loop fixture
│   ├── cleanup.py       # State cleanup fixtures
│   └── scenarios.py     # MarketScenario dataclasses
│
└── conftest.py          # Root pytest configuration and markers

# Existing Structure (unchanged)
src/
├── api/          # FastAPI routers and endpoints
├── core/         # Core business logic (EXTRACT pure logic here)
├── models/       # Pydantic models and schemas
├── services/     # Business services
├── db/           # Database models and migrations
└── utils/        # Shared utilities

# Configuration
pytest.ini           # Pytest markers, test paths, coverage settings
Makefile             # test-unit, test-component, test-integration, test-all targets
.python-version      # Python 3.11+ specification
pyproject.toml       # Managed by UV only
```

**Structure Decision**: Option 1 (Single Python backend project) - This is test infrastructure refactoring, not a web/mobile app.

## Phase 0: Outline & Research

### Unknowns from Technical Context
1. **pytest-forked vs pytest-xdist subprocess isolation**: Which plugin provides better isolation for C extension crashes?
2. **Event loop cleanup patterns**: Best practices for async test isolation with pytest-asyncio
3. **Nautilus C extension state management**: How to safely reset module state between tests
4. **Test double design patterns**: Protocol-based contract testing for test doubles
5. **Market scenario testing patterns**: Reusable scenario design for different backends

### Research Tasks

#### Task 1: Research pytest subprocess isolation options
**Question**: Compare pytest-forked and pytest-xdist for isolating tests with C extension crashes

**Research Focus**:
- pytest-forked capabilities and limitations
- pytest-xdist --forked mode vs pytest-forked
- Worker restart strategies
- Performance implications of subprocess isolation

#### Task 2: Research async event loop isolation patterns
**Question**: Best practices for pytest-asyncio event loop management in parallel tests

**Research Focus**:
- Event loop fixture scopes (function vs module)
- Task cancellation and cleanup
- Parallel test execution with isolated loops
- Conflicts with existing event loops

#### Task 3: Research Nautilus C extension testing patterns
**Question**: How does Nautilus Trader itself test C extensions? What cleanup is required?

**Research Focus**:
- Nautilus Trader test suite structure (from GitHub)
- C extension state management patterns
- Module cache clearing techniques
- Garbage collection strategies

#### Task 4: Research protocol-based contract testing
**Question**: How to ensure test doubles don't diverge from real implementations?

**Research Focus**:
- Python Protocol classes for interfaces
- Contract test patterns
- Test double validation strategies
- Runtime type checking options

#### Task 5: Research market scenario testing patterns
**Question**: Reusable market scenario design that works with multiple backends

**Research Focus**:
- Data-driven testing patterns
- Scenario abstraction layers
- Backend switching strategies
- Test data fixtures best practices

**Output**: `research.md` with decisions, rationales, and alternatives for all 5 research areas

## Phase 1: Design & Contracts

### 1. Data Model Extraction

Extract key entities from spec.md → `data-model.md`:

**Entities**:
1. **PureLogicClass** - Extracted business logic with no framework dependencies
2. **TestDouble** - Mock implementation of trading engine interfaces
3. **MarketScenario** - Reusable test data for market conditions
4. **IsolationFixture** - Cleanup and state management fixtures
5. **ContractProtocol** - Interface definitions for contract testing
6. **TestCategory** - Marker-based test categorization

### 2. API Contracts Generation

Since this is test infrastructure (no REST API), contracts will be:
- **Protocol Interfaces** (Python Protocols for test doubles)
- **Fixture Contracts** (pytest fixture signatures and cleanup guarantees)
- **Marker Contracts** (pytest marker definitions)

Output to `/contracts/`:
- `protocols.py` - Protocol definitions (StrategyProtocol, EngineProtocol, DataProviderProtocol)
- `fixtures.py` - Fixture type signatures and docstrings
- `markers.py` - Pytest marker definitions and usage documentation

### 3. Contract Tests Generation

Contract tests verify test doubles satisfy protocols:
- `test_engine_contract.py` - Verify TestTradingEngine matches EngineProtocol
- `test_strategy_contract.py` - Verify test strategies match StrategyProtocol
- Tests must FAIL initially (no test doubles implemented yet)

### 4. Test Scenarios from User Stories

Extract from User Stories:
- **US1 Scenarios** → `test_unit_pure_logic.py` (failing test examples)
- **US2 Scenarios** → `test_component_test_doubles.py` (failing test examples)
- **US3 Scenarios** → `test_integration_isolated.py` (failing test examples)
- **US4 Scenarios** → Makefile targets validation

### 5. Update CLAUDE.md

Run `.specify/scripts/bash/update-agent-context.sh claude` to:
- Add pytest-xdist, pytest-forked, pytest-asyncio to tech stack
- Update recent changes with testing architecture refactor
- Preserve existing manual additions
- Keep under 150 lines for token efficiency

**Output**:
- `data-model.md` (entity definitions)
- `contracts/protocols.py` (interface definitions)
- `contracts/fixtures.py` (fixture signatures)
- `contracts/markers.py` (marker documentation)
- Failing contract tests in `tests/component/test_contracts.py`
- `quickstart.md` (how to run tests by category)
- `CLAUDE.md` (updated with new dependencies)

## Phase 2: Task Planning Approach
*This section describes what the /tasks command will do - DO NOT execute during /plan*

**Task Generation Strategy**:
1. Load `/templates/tasks-template.md` as base
2. Generate tasks from Phase 1 design docs:
   - Each protocol → contract test task [P]
   - Each test category → directory structure task [P]
   - Each fixture → fixture implementation task
   - Each test double → implementation task (after contract tests)
   - pytest.ini configuration task
   - Makefile targets task
   - Migration tasks (move existing tests to categories)

**Ordering Strategy**:
- **Setup Phase**: Directory structure, pytest.ini, Makefile [P]
- **Protocol Phase**: Define protocols and contract tests [P]
- **Fixture Phase**: Event loop, cleanup, scenario fixtures
- **Test Doubles Phase**: Implement TestEngine, TestOrder, TestPosition (TDD with contract tests)
- **Migration Phase**: Categorize and move existing tests
- **Validation Phase**: Run test suite by category, verify pyramid distribution

**Task Dependencies**:
- Protocols must exist before contract tests
- Contract tests must pass before test doubles are used
- Fixtures must exist before migration
- Migration completes before validation

**Estimated Output**: 30-35 numbered, ordered tasks in tasks.md with clear TDD progression

**Parallel Execution Opportunities**:
- Directory structure tasks [P]
- Protocol definition tasks [P]
- Independent fixture implementations [P]
- Independent test migration tasks (once fixtures exist) [P]

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation
*These phases are beyond the scope of the /plan command*

**Phase 3**: Task execution (/tasks command creates tasks.md)
**Phase 4**: Implementation (execute tasks.md following TDD and constitutional principles)
**Phase 5**: Validation (run tests by category, verify pyramid distribution, measure performance improvements)

## Complexity Tracking
*No constitutional violations identified*

This refactoring strictly adheres to all constitutional principles:
- Simplicity: Direct pytest usage, no wrapper abstractions
- TDD: Contract tests validate test doubles before use
- Type Safety: All fixtures and test doubles fully typed
- Observability: Test isolation ensures clear failure attribution
- Performance: Meets aggressive performance targets (<100ms unit tests)

## Progress Tracking
*This checklist is updated during execution flow*

**Phase Status**:
- [x] Phase 0: Research complete - Simplified approach (see research.md)
- [x] Phase 1: Design complete - Minimal entities defined (data-model.md, quickstart.md)
- [x] Phase 2: Task planning complete - Ready for /tasks command (see below)
- [ ] Phase 3: Tasks generated (/tasks command)
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:
- [x] Initial Constitution Check: PASS
- [x] Post-Design Constitution Check: PASS (kept simple, no violations)
- [x] All NEEDS CLARIFICATION resolved (Technical Context fully specified)
- [x] Complexity deviations documented (None - full compliance via simplification)
- [x] Agent context updated (CLAUDE.md updated with test dependencies)

---
*Based on Python Backend Development Constitution v1.0.1 - See `.specify/memory/constitution.md`*
