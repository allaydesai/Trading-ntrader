# Quickstart: Running Tests

**Feature**: 003-rework-unit-testing
**Date**: 2025-01-22

## Overview

This guide shows you how to run different categories of tests in the refactored test architecture.

---

## Prerequisites

```bash
# Ensure dependencies are installed
uv sync

# Verify pytest is available
uv run pytest --version
```

---

## Running Tests by Category

### Unit Tests (Fast - Recommended for Development)

**What**: Pure Python logic, no Nautilus dependencies
**Speed**: <5 seconds total
**When**: Run constantly during development

```bash
# Run all unit tests in parallel
make test-unit

# Or directly with pytest
uv run pytest tests/unit -v -n auto

# Run specific unit test file
uv run pytest tests/unit/test_sma_logic.py -v
```

**Example Output**:
```
tests/unit/test_sma_logic.py::test_golden_cross_signal PASSED
tests/unit/test_position_sizing.py::test_calculate_size PASSED
tests/unit/test_risk_management.py::test_validate_position_limits PASSED
======================== 84 passed in 0.55s ========================
```

---

### Component Tests (Medium Speed)

**What**: Strategy tests with lightweight test doubles
**Speed**: ~10 seconds total
**When**: Run before committing

```bash
# Run all component tests in parallel
make test-component

# Or directly with pytest
uv run pytest tests/component -v -n auto

# Run specific component test
uv run pytest tests/component/test_sma_strategy.py -v
```

**Example Output**:
```
tests/component/test_sma_strategy.py::test_submits_buy_on_golden_cross PASSED
tests/component/test_position_manager.py::test_enforces_position_limit PASSED
tests/component/test_risk_checks.py::test_validates_max_position_risk PASSED
tests/component/test_doubles_interface.py::test_engine_has_submit_order PASSED
======================== 61 passed in 0.54s ========================
```

---

### Integration Tests (Slower - Use Subprocess Isolation)

**What**: Tests with real Nautilus BacktestEngine
**Speed**: 1-2 minutes with 4 workers
**When**: Run in CI/CD or before PR

```bash
# Run integration tests with subprocess isolation
make test-integration

# Or directly with pytest
uv run pytest tests/integration -v -n auto --forked

# Run single integration test (useful for debugging)
uv run pytest tests/integration/test_backtest_engine.py::test_sma_strategy -v --forked
```

**Example Output**:
```
tests/integration/test_backtest_engine.py::test_engine_initialization PASSED
tests/integration/test_strategy_execution.py::test_strategy_lifecycle PASSED
tests/integration/test_nautilus_stubs_examples.py::test_instrument_provider PASSED
======================== 27 tests (infrastructure ready, needs API fixes) ========================
```

---

### All Tests

**What**: Complete test suite
**Speed**: 2-3 minutes
**When**: Before pushing to main branch

```bash
# Run everything
make test-all

# Or directly with pytest
uv run pytest tests -v -n auto
```

---

## Running Tests by Marker

You can also run tests by pytest markers:

```bash
# Only unit tests
uv run pytest -m unit -v -n auto

# Only integration tests
uv run pytest -m integration -v -n auto --forked

# Exclude slow tests
uv run pytest -m "not slow" -v -n auto

# Run only tests related to trading strategies
uv run pytest -k "strategy" -v -n auto
```

---

## Common Test Commands

### Development Workflow

```bash
# Fast feedback loop (unit tests only)
uv run pytest tests/unit -v -n auto

# After making changes (unit + component)
uv run pytest tests/unit tests/component -v -n auto

# Before committing (all tests)
make test-all
```

### Debugging Failing Tests

```bash
# Run single test with full output
uv run pytest tests/unit/test_sma_logic.py::test_golden_cross -vv

# Run with Python debugger on failure
uv run pytest tests/unit/test_sma_logic.py --pdb

# Show print statements
uv run pytest tests/unit/test_sma_logic.py -s

# Stop at first failure
uv run pytest tests/unit -x -v
```

### Coverage Reports

```bash
# Run with coverage
uv run pytest tests/unit --cov=src/core --cov-report=html

# Open coverage report
open htmlcov/index.html
```

---

## Makefile Targets

The project provides convenient Makefile targets:

```makefile
make test-unit         # Unit tests only (fastest)
make test-component    # Component tests only
make test-integration  # Integration tests only (subprocess isolated)
make test-all          # All tests
make test-coverage     # All tests with coverage report
```

---

## Test Organization

Tests are organized by category:

```
tests/
├── unit/              # Pure Python, no Nautilus
│   ├── test_sma_logic.py           # 29 tests
│   ├── test_position_sizing.py     # 30 tests
│   └── test_risk_management.py     # 25 tests
│
├── component/         # With test doubles
│   ├── test_sma_strategy.py        # 8 tests
│   ├── test_position_manager.py    # 15 tests
│   ├── test_risk_checks.py         # 18 tests
│   ├── test_doubles_interface.py   # 25 tests
│   └── doubles/
│       ├── test_order.py           # TestOrder dataclass
│       ├── test_position.py        # TestPosition with PnL
│       └── test_engine.py          # TestTradingEngine (230 lines)
│
├── integration/       # Real Nautilus components
│   ├── test_backtest_engine.py     # 6 tests
│   ├── test_strategy_execution.py  # 8 tests
│   ├── test_nautilus_stubs_examples.py  # 13 tests
│   └── README.md                   # API compatibility notes
│
└── fixtures/          # Shared test utilities
    └── scenarios.py   # MarketScenario + 3 scenarios
```

---

## Performance Expectations

| Category | Test Count | Actual Time | Parallelization |
|----------|-----------|-------------|-----------------|
| Unit | 84 tests | 0.55s (99% faster!) | Yes (-n auto, 14 workers) |
| Component | 61 tests | 0.54s (95% better than target) | Yes (-n auto, 14 workers) |
| Integration | 27 tests | Infrastructure ready* | Yes with --forked |
| Unit + Component | 145 tests | 1.09s combined | Yes |

**Note**: Integration tests infrastructure complete. Tests need Nautilus API fixes (see tests/integration/README.md)

---

## Troubleshooting

### Tests Running Slow

```bash
# Check if pytest-xdist is installed
uv run pytest --version
# Should show: plugins: xdist-...

# Verify parallel execution is working
uv run pytest tests/unit -v -n auto
# Should show multiple workers: gw0, gw1, gw2, gw3
```

### Integration Tests Crashing

```bash
# Ensure --forked flag is used
uv run pytest tests/integration -v -n auto --forked

# If still crashing, run sequentially to isolate issue
uv run pytest tests/integration -v --forked
```

### Import Errors

```bash
# Sync dependencies
uv sync

# Verify src is in PYTHONPATH
uv run pytest tests/unit -v
```

---

## CI/CD Integration

### GitHub Actions Example

The project uses a multi-job CI/CD workflow with separate test jobs for fast feedback:

```yaml
# .github/workflows/ci.yml (simplified example)
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

jobs:
  unit-tests:
    name: Unit Tests (Fast)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install UV
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Install dependencies
        run: uv sync --dev
      - name: Run unit tests
        run: uv run pytest tests/unit -v -n auto --cov=src

  component-tests:
    name: Component Tests (Test Doubles)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install UV
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Install dependencies
        run: uv sync --dev
      - name: Run component tests
        run: uv run pytest tests/component -v -n auto --cov=src

  integration-tests:
    name: Integration Tests (Real Nautilus)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install UV
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Install dependencies
        run: uv sync --dev
      - name: Run integration tests (subprocess isolated)
        run: uv run pytest tests/integration -v -n auto --forked --cov=src
```

**Key Features**:
- **Parallel jobs**: Unit, component, and integration tests run concurrently
- **Fast feedback**: Unit tests complete in <5s, providing rapid feedback
- **Subprocess isolation**: Integration tests use `--forked` to prevent C extension crashes
- **Coverage tracking**: Each job reports coverage separately

---

## Next Steps

After setting up the test structure:

1. **Extract business logic** from existing strategies into pure Python classes
2. **Create test doubles** for component testing
3. **Migrate existing tests** to appropriate categories
4. **Set up CI/CD** to run tests automatically

---

## Quick Reference

```bash
# Fast development loop
make test-unit

# Before commit
make test-all

# Debug failing test
uv run pytest path/to/test.py::test_name -vv --pdb

# Coverage report
make test-coverage
```

---

**Last Updated**: 2025-01-23 (Phase 5 Complete)
**Related Docs**:
- See `STATUS.md` for quick progress overview
- See `PROGRESS.md` for detailed implementation report
- See `tasks.md` for complete task breakdown (39/89 complete)
- See `tests/integration/README.md` for API compatibility notes
