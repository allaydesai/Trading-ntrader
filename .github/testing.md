# Testing Guide

## Overview

This project uses **pytest** for comprehensive testing with the following structure:

- **Unit Tests**: Individual component testing
- **Integration Tests**: End-to-end workflow testing
- **Trading Tests**: Domain-specific trading system tests
- **Coverage Requirements**: 70%+ coverage enforced in CI

## Test Organization

### Test Markers

Tests are organized using pytest markers:

- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests
- `@pytest.mark.trading` - Trading system specific tests
- `@pytest.mark.slow` - Slow running tests (typically >5 seconds)

### Test Structure

```
tests/
├── test_backtest_runner.py    # Backtest engine tests
├── test_mock_data.py          # Data generation tests
├── test_simple_backtest.py    # End-to-end integration tests
├── test_sma_strategy.py       # Strategy implementation tests
├── test_strategy_model.py     # Model validation tests
└── conftest.py                # Shared fixtures
```

## Running Tests

### Local Development

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src --cov-report=html

# Run specific test categories
uv run pytest -m "unit"           # Only unit tests
uv run pytest -m "integration"    # Only integration tests
uv run pytest -m "trading"        # Only trading tests
uv run pytest -m "not slow"       # Skip slow tests

# Run specific test files
uv run pytest tests/test_strategy_model.py -v

# Run specific test function
uv run pytest tests/test_backtest_runner.py::test_run_sma_backtest -v
```

### CI/CD Pipeline

The GitHub Actions CI pipeline automatically:

1. **Discovers Tests**: Verifies all tests can be found
2. **Runs Test Suite**: Executes all tests with coverage
3. **Generates Reports**: Creates coverage and test result reports
4. **Uploads Artifacts**: Stores coverage reports as artifacts
5. **Enforces Coverage**: Fails if coverage drops below 70%

## Test Configuration

### pytest.ini

Key configuration settings:

- **Test Discovery**: `test_*.py` files, `test_*` functions
- **Coverage Minimum**: 70% enforced in CI
- **Output Format**: Verbose with colored output
- **Warning Filters**: Suppress known Pydantic deprecation warnings

### Coverage Configuration

Coverage settings in CI:
- **Source**: `src/` directory only
- **Reports**: XML (for Codecov), HTML (for artifacts), terminal
- **Fail Threshold**: 70%

## Writing Tests

### Test Naming Convention

```python
def test_function_does_what_when_condition():
    """Test function behavior under specific conditions."""
    # Arrange
    setup_data = create_test_data()

    # Act
    result = function_under_test(setup_data)

    # Assert
    assert result.expected_property == expected_value
```

### Test Categories

#### Unit Tests
```python
def test_backtest_result_creation():
    """Test BacktestResult creation and properties."""
    result = BacktestResult(total_return=1000.0, total_trades=10)
    assert result.win_rate == 60.0
```

#### Integration Tests
```python
@pytest.mark.integration
@pytest.mark.trading
def test_can_run_simple_sma_backtest():
    """End-to-end test for complete backtest workflow."""
    # Test full CLI command execution
    result = subprocess.run([...])
    assert result.returncode == 0
```

#### Trading Tests
```python
@pytest.mark.trading
def test_sma_strategy_initialization():
    """Test SMA strategy proper initialization."""
    strategy = SMACrossover(config=config)
    assert strategy.fast_sma.period == 10
```

## Current Test Coverage

### Test Files (16 tests total)

1. **test_backtest_runner.py** (6 tests)
   - Backtest result handling
   - Engine lifecycle management
   - SMA backtest execution

2. **test_mock_data.py** (4 tests)
   - Instrument creation
   - Data frame generation
   - Bar generation
   - Predictable pattern validation

3. **test_simple_backtest.py** (1 test)
   - End-to-end CLI integration

4. **test_sma_strategy.py** (2 tests)
   - Strategy configuration
   - Strategy initialization

5. **test_strategy_model.py** (3 tests)
   - Parameter validation
   - Strategy creation
   - Status transitions

### Coverage Areas

- ✅ **Core Logic**: Backtest runners, strategies
- ✅ **Data Models**: Pydantic model validation
- ✅ **CLI Interface**: Command execution
- ✅ **Mock Data**: Synthetic data generation
- ✅ **Configuration**: Settings management

## CI Pipeline Details

### Test Job Features

- **Matrix Testing**: Python 3.11 and 3.12
- **Dependency Caching**: Faster builds with UV cache
- **Test Discovery Verification**: Ensures all tests found
- **Coverage Enforcement**: 70% minimum required
- **Artifact Upload**: Coverage reports saved
- **Multiple Report Formats**: XML, HTML, terminal

### Quality Gates

Tests must pass these gates:

1. **Discovery**: All test files found
2. **Execution**: No test failures
3. **Coverage**: >70% code coverage
4. **Performance**: Complete within reasonable time

### Failure Handling

- **Max Failures**: Stop after 5 test failures
- **Short Traceback**: Concise error reporting
- **Coverage Failure**: Fails CI if under 70%
- **Artifact Collection**: Reports saved even on failure

## Troubleshooting

### Common Issues

**Tests not discovered:**
```bash
# Check test discovery
uv run pytest --collect-only tests/
```

**Import errors:**
```bash
# Verify Python path and dependencies
uv sync --dev
```

**Coverage too low:**
```bash
# Generate detailed coverage report
uv run pytest --cov=src --cov-report=html
open htmlcov/index.html
```

**Slow tests timing out:**
```bash
# Run without slow tests
uv run pytest -m "not slow"
```

### Performance Optimization

- Use markers to skip slow tests in development
- Mock external dependencies
- Use fixtures for expensive setup
- Keep test data minimal

## Future Enhancements

Planned testing improvements:

- [ ] Performance benchmarking tests
- [ ] Property-based testing with Hypothesis
- [ ] Database integration tests
- [ ] API endpoint testing
- [ ] Load testing for backtests
- [ ] Memory usage testing

---

This testing infrastructure ensures code quality, prevents regressions, and provides confidence in trading system reliability.