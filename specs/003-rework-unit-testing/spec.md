# Feature Specification: Unit Testing Architecture Refactor

**Feature Branch**: `003-rework-unit-testing`
**Created**: 2025-01-22
**Status**: Draft
**Input**: User description: "Rework unit testing implementation to separate unit tests from integration tests, following trading engine testing philosophy and C extension testing patterns"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pure Business Logic Testing (Priority: P1)

As a developer, I need to test trading logic and algorithms independently from the Nautilus framework so that tests run fast, are reliable, and can validate business rules without dependency on complex C extensions or the trading engine.

**Why this priority**: This is the foundation of the testing pyramid (50% of all tests should be unit tests). Fast, reliable unit tests enable rapid development cycles and provide immediate feedback on business logic correctness. Without this separation, developers are forced to use the full Nautilus engine even for simple logic validation, leading to slow, brittle tests.

**Independent Test**: Can be fully tested by extracting trading logic into pure Python functions (no Nautilus dependencies) and writing standard pytest tests. Delivers value by enabling developers to validate algorithms, position sizing, risk management rules, and trade decisions in milliseconds instead of seconds.

**Acceptance Scenarios**:

1. **Given** a trading strategy with decision logic, **When** I extract the logic into a pure Python class, **Then** I can test it without importing Nautilus components
2. **Given** a position sizing algorithm, **When** I write a unit test for it, **Then** the test executes in under 100ms and requires no database or engine setup
3. **Given** a risk management rule, **When** I test it with various market conditions, **Then** I can run hundreds of test cases in under 1 second

---

### User Story 2 - Component-Level Testing with Test Doubles (Priority: P2)

As a developer, I need to test strategy behavior using lightweight test doubles that mimic the trading engine so that I can verify order submission, position management, and event handling without the complexity and brittleness of the real Nautilus engine.

**Why this priority**: Component tests (25% of test suite) verify that strategies interact correctly with the engine interface while remaining fast and maintainable. This enables testing of integration patterns without the overhead of spinning up the full Nautilus backtesting infrastructure.

**Independent Test**: Can be fully tested by creating test double classes (TestTradingEngine, TestOrder) that implement the engine interface and writing tests that verify strategy behavior. Delivers value by allowing developers to test strategy-engine interactions in seconds instead of minutes.

**Acceptance Scenarios**:

1. **Given** a strategy that submits orders, **When** I test it with a TestTradingEngine double, **Then** I can verify order parameters without real engine initialization
2. **Given** a strategy with multiple execution paths, **When** I test with different market scenarios, **Then** I can capture all submitted orders and verify trading logic
3. **Given** a test suite with 50 component tests, **When** I run them, **Then** they complete in under 10 seconds total

---

### User Story 3 - Integration Testing with Isolated Nautilus Components (Priority: P3)

As a developer, I need to run integration tests that use real Nautilus components in an isolated, controlled environment so that I can validate end-to-end behavior before deployment while maintaining test stability and avoiding flaky tests from C extension issues.

**Why this priority**: Integration tests (20% of test suite) verify that the system works correctly with real Nautilus components. These tests are slower but necessary to catch issues that mocked tests might miss. Process isolation prevents C extension crashes from affecting the entire test suite.

**Independent Test**: Can be fully tested by configuring pytest-xdist with forked subprocess execution and creating minimal Nautilus engine configurations. Delivers value by ensuring production-ready validation while maintaining CI/CD reliability.

**Acceptance Scenarios**:

1. **Given** an integration test that uses Nautilus BacktestEngine, **When** I run it with pytest --forked, **Then** it executes in an isolated subprocess and crashes don't affect other tests
2. **Given** a test suite with 20 integration tests, **When** I run them in parallel with pytest -n 4, **Then** they complete in under 2 minutes
3. **Given** a flaky test that occasionally crashes, **When** I mark it with @pytest.mark.forked, **Then** it runs isolated and doesn't break the test suite

---

### User Story 4 - Test Organization and Discovery (Priority: P4)

As a developer, I need tests organized into clear unit/component/integration/e2e categories with appropriate markers so that I can run different test types independently, optimize CI/CD pipelines, and maintain a healthy test pyramid distribution.

**Why this priority**: Test organization enables efficient development workflows (run fast tests locally, comprehensive tests in CI). Proper categorization ensures the test pyramid is maintained (50% unit, 25% component, 20% integration, 5% e2e).

**Independent Test**: Can be fully tested by restructuring test directories, adding pytest markers, and creating Makefile targets for each test category. Delivers value by enabling developers to run "pytest tests/unit" in seconds for rapid feedback.

**Acceptance Scenarios**:

1. **Given** a restructured test directory, **When** I run "make test-unit", **Then** only pure Python unit tests execute in parallel without subprocess isolation
2. **Given** integration tests marked with @pytest.mark.integration, **When** I run "pytest -m integration", **Then** only integration tests with real Nautilus components execute
3. **Given** the full test suite, **When** I check test distribution, **Then** unit tests comprise approximately 50% of total tests

---

### Edge Cases

- What happens when a test using Nautilus C extensions causes a segmentation fault? (Answer: With pytest-forked, only that test's subprocess crashes, other tests continue)
- How does the system handle async event loop conflicts in parallel tests? (Answer: Each test gets an isolated event loop fixture that cleans up properly)
- What happens when a component test needs to simulate complex market scenarios? (Answer: Use reusable MarketScenario dataclasses that can run with different backends)
- How do we prevent test doubles from diverging from real engine behavior? (Answer: Use contract tests that verify both test doubles and real implementations satisfy the same interface)
- What happens when tests share global state from C extensions? (Answer: Use cleanup fixtures that reset module state and force garbage collection between tests)

## Requirements *(mandatory)*

### Functional Requirements

#### Test Architecture & Separation

- **FR-001**: System MUST separate tests into three distinct categories: unit tests (pure Python, no Nautilus), component tests (test doubles), and integration tests (real Nautilus components)
- **FR-002**: Unit tests MUST NOT import any Nautilus Trader modules or C extension libraries
- **FR-003**: System MUST organize tests in separate directories: `tests/unit/`, `tests/component/`, `tests/integration/`, `tests/e2e/`
- **FR-004**: All tests MUST be marked with appropriate pytest markers: @pytest.mark.unit, @pytest.mark.component, @pytest.mark.integration, @pytest.mark.e2e

#### Pure Business Logic Extraction (Unit Tests)

- **FR-005**: Trading logic (entry/exit decisions, position sizing, risk management) MUST be extracted into pure Python classes with no Nautilus dependencies
- **FR-006**: Pure logic classes MUST accept primitive types (float, Decimal, dict) rather than Nautilus-specific types (QuoteTick, Price)
- **FR-007**: Strategy classes MUST delegate to pure logic classes for all algorithmic decisions
- **FR-008**: Unit tests MUST execute in under 100ms per test and require no database, engine, or external service setup

#### Test Doubles & Component Testing

- **FR-009**: System MUST provide test double classes (TestTradingEngine, TestOrder, TestPosition) that implement core trading interfaces
- **FR-010**: Test doubles MUST capture interactions (submitted orders, position changes, events) for verification in tests
- **FR-011**: Test doubles MUST provide simple state management (order fills, position tracking, balance updates) without C extension complexity
- **FR-012**: Component tests MUST verify strategy behavior by asserting on captured interactions with test doubles

#### Integration Testing with Process Isolation

- **FR-013**: Integration tests using Nautilus components MUST use pytest-forked or pytest-xdist subprocess isolation
- **FR-014**: System MUST configure pytest with --forked flag for integration tests to prevent C extension crashes from affecting other tests
- **FR-015**: Integration tests MUST use minimal Nautilus BacktestEngine configurations with unnecessary components disabled
- **FR-016**: System MUST provide cleanup fixtures that reset module state, clear C extension caches, and force garbage collection between integration tests

#### Async Testing Patterns

- **FR-017**: System MUST provide an event_loop fixture that creates isolated event loops per test and properly cancels all tasks on cleanup
- **FR-018**: Async tests MUST use the isolated event loop fixture to prevent event loop conflicts in parallel execution
- **FR-019**: Async component tests MUST use AsyncMock for async dependencies

#### Parallel Test Execution

- **FR-020**: System MUST support parallel test execution with pytest-xdist using `-n auto` for CPU auto-detection
- **FR-021**: Unit tests MUST run in parallel without subprocess isolation (fast execution)
- **FR-022**: Integration tests MUST run in parallel with subprocess isolation (--forked flag) to handle C extension issues
- **FR-023**: System MUST configure pytest with --max-worker-restart=3 to automatically restart workers after crashes

#### Test Organization & Discovery

- **FR-024**: System MUST provide Makefile targets for running each test category: test-unit, test-component, test-integration, test-e2e, test-all
- **FR-025**: pytest.ini MUST define custom markers for test categorization
- **FR-026**: System MUST provide pytest configuration that separates fast tests (milliseconds) from slow tests (seconds/minutes)
- **FR-027**: CI/CD pipeline MUST run unit tests on every commit, component tests on PR, and integration tests on merge to main

#### Scenario-Based Testing

- **FR-028**: System MUST provide reusable MarketScenario dataclasses for testing strategies with different market conditions
- **FR-029**: Scenarios MUST be runnable with different backends (mock, simple Python, Nautilus) using a ScenarioRunner
- **FR-030**: System MUST provide predefined market scenarios: VOLATILE_MARKET, TRENDING_MARKET, RANGING_MARKET

#### Contract Testing

- **FR-031**: System MUST define protocol interfaces for trading strategies, engines, and data providers
- **FR-032**: System MUST provide contract test base classes that verify both test doubles and real implementations satisfy protocols
- **FR-033**: Contract tests MUST ensure test doubles don't diverge from real implementation behavior

#### State Management & Cleanup

- **FR-034**: System MUST provide an autouse cleanup fixture that removes imported test modules, resets warnings, and forces garbage collection
- **FR-035**: Integration tests MUST capture initial global state and restore it after test completion
- **FR-036**: System MUST clear Nautilus C extension caches between integration tests if available

### Key Entities

- **Pure Logic Class**: Business logic extracted from strategies - algorithms for trade decisions, position sizing, risk management, with no framework dependencies
- **Test Double**: Lightweight mock implementation of trading engine interfaces - captures interactions, provides simple state management, no C extensions
- **Market Scenario**: Reusable test data representing different market conditions - price sequences, expected actions, can run with multiple backends
- **Test Category**: Classification of tests by scope - unit (pure Python), component (test doubles), integration (real Nautilus), e2e (full system)
- **Isolation Fixture**: Test fixture that manages resource cleanup - event loops, module state, C extension caches, subprocess management
- **Contract Protocol**: Interface definition that both test doubles and real implementations must satisfy - ensures test double accuracy

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests (pure Python, no Nautilus) execute in under 100ms each and comprise 50% or more of total test suite
- **SC-002**: Full unit test suite (all pure logic tests) completes in under 5 seconds on a standard development machine
- **SC-003**: Component tests with test doubles execute in under 500ms each and comprise 25% of total test suite
- **SC-004**: Integration tests with real Nautilus components complete in under 2 minutes when run in parallel with 4 workers
- **SC-005**: Developers can run unit tests locally without installing Nautilus C extensions or connecting to databases
- **SC-006**: CI/CD pipeline provides feedback from unit tests within 30 seconds of commit
- **SC-007**: Test suite maintains 80% or higher code coverage for critical trading logic paths
- **SC-008**: Zero test failures due to Nautilus C extension segfaults affecting other tests (process isolation prevents cascade failures)
- **SC-009**: Developers can identify and run specific test categories using Makefile targets (test-unit, test-integration) or pytest markers
- **SC-010**: Test execution time improves by at least 50% compared to current all-integration approach

## Assumptions

1. **Testing Framework**: Assuming pytest is already installed and configured as the primary testing framework
2. **Python Version**: Assuming Python 3.11+ is in use, supporting modern async patterns and type hints
3. **Current Test Quality**: Assuming existing tests have reasonable coverage, we're refactoring structure not rewriting from scratch
4. **Nautilus Usage**: Assuming the project uses Nautilus Trader for backtesting, and strategies currently inherit from Nautilus Strategy base class
5. **Development Environment**: Assuming developers have local development environments with UV package manager configured
6. **CI/CD Exists**: Assuming a CI/CD pipeline exists that can be configured with different pytest commands
7. **Test Data**: Assuming test data can be represented as simple Python structures (dicts, lists) for unit tests
8. **Async Code**: Assuming some tests involve async operations that need event loop management
9. **Parallel Execution**: Assuming test infrastructure can support parallel execution (adequate CPU cores available)
10. **Refactoring Scope**: Assuming we can modify existing strategy code to extract pure logic without breaking production functionality

## Scope

### In Scope

- Restructuring test directory organization (unit/component/integration/e2e folders)
- Extracting trading logic from strategies into pure Python classes
- Creating test double implementations (TestTradingEngine, TestOrder, etc.)
- Configuring pytest with markers, subprocess isolation, and parallel execution
- Implementing cleanup fixtures for state management and C extension cache clearing
- Creating reusable market scenario patterns for strategy testing
- Defining protocol interfaces and contract tests
- Updating Makefile with test category targets
- Configuring pytest.ini with markers and execution settings
- Documenting testing patterns and guidelines for future development
- Migrating existing tests to appropriate categories (unit vs integration)

### Out of Scope

- Rewriting all existing tests from scratch (refactor/move only)
- Adding new trading strategy features or business logic
- Modifying Nautilus Trader framework itself or C extensions
- Setting up new CI/CD infrastructure (only configure existing pipeline)
- Performance tuning of trading algorithms (focus on test performance only)
- Creating new data mocking libraries beyond simple test doubles
- Implementing visual test reports or dashboards
- Modifying production strategy behavior (only extract for testing)
- Database schema changes or migration
- Adding new testing frameworks beyond pytest ecosystem

## Dependencies

### Internal Dependencies

- Existing strategy implementations that need logic extraction
- Current test suite that needs categorization and migration
- Project structure and module organization
- UV package manager configuration (pyproject.toml)
- CI/CD pipeline configuration files

### External Dependencies

- pytest >= 7.4 (already in project)
- pytest-asyncio (for async test support)
- pytest-xdist (for parallel execution with -n flag)
- pytest-forked (for subprocess isolation with --forked flag)
- pytest-mock (for enhanced mocking with mocker fixture)

### Technical Constraints

- Must maintain backward compatibility with existing strategies
- Cannot modify Nautilus Trader framework or C extensions
- Must work within current Python 3.11+ environment
- Must integrate with existing UV dependency management
- Must not increase total test execution time in CI/CD
- Must support both local development (fast unit tests) and CI/CD (comprehensive suite)

## Open Questions

None - all requirements are clearly specified based on documented testing patterns and project constraints.
