# Research Report: Unit Testing Architecture Refactor

**Feature**: 003-rework-unit-testing
**Date**: 2025-01-22
**Status**: Complete
**Philosophy**: KISS - Keep It Simple, Stupid

## Overview

This research identified the minimal, essential patterns needed to separate unit tests from integration tests in a Python backtesting system using Nautilus Trader. Every decision prioritizes long-term maintainability over theoretical flexibility.

**Key Sources**:
- `@docs/testing/testing-philosophy-trading-engine.md` (Test Pyramid: 50% unit, 25% component, 20% integration, 5% e2e)
- `@docs/testing/popular-patterns-for-cextension-testing.md` (Battle-tested NumPy/pandas patterns)

---

## Research Question 1: Test Isolation for C Extensions

### Decision: pytest-xdist with --forked mode

**What**: One tool that handles both parallel execution AND subprocess isolation.

**Why**:
- Battle-tested by NumPy, pandas, scikit-learn
- Well-maintained (3.6M+ weekly downloads)
- Simple configuration

**Installation**:
```bash
uv add --dev pytest-xdist
```

**Usage**:
```bash
# Unit tests: Fast parallel, no isolation
pytest tests/unit -n auto

# Integration tests: Parallel WITH subprocess isolation
pytest tests/integration -n auto --forked
```

**Rejected**: pytest-isolate (too new), pytest-forked alone (unmaintained), complex subprocess wrappers (YAGNI)

---

## Research Question 2: Async Event Loop Management

### Decision: pytest-asyncio default behavior

**What**: Built-in pytest-asyncio "auto" mode with minimal cleanup if needed.

**Why**: Default behavior handles 95% of cases. Don't over-engineer.

**Configuration**:
```toml
# pytest.ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

**Cleanup (only if you see warnings)**:
```python
# tests/conftest.py
import asyncio
import pytest

@pytest.fixture
async def clean_event_loop():
    loop = asyncio.get_event_loop()
    yield loop
    tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

**Rejected**: Complex isolation patterns (not needed), custom event loop policies (YAGNI)

---

## Research Question 3: Nautilus Testing Patterns

### Decision: Use Nautilus TestStubs + minimal BacktestEngine configs

**What**: Leverage Nautilus's own test utilities instead of recreating them.

**Why**: Don't reinvent the wheel. Nautilus already provides test infrastructure.

**Key Patterns** (from testing-philosophy-trading-engine.md):

1. **Use TestStubs for test data**:
```python
from nautilus_trader.test_kit.providers import TestInstrumentProvider

# Don't create complex mock instruments
instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
```

2. **Minimal BacktestEngine configuration**:
```python
config = BacktestEngineConfig(
    logging=LoggingConfig(log_level="ERROR"),  # Disable noisy logs
    cache=CacheConfig(tick_capacity=100),  # Minimal capacity
)
```

3. **Simple cleanup with garbage collection**:
```python
# tests/conftest.py
import gc

@pytest.fixture(autouse=True)
def cleanup():
    yield
    gc.collect()  # Force cleanup of C extension objects
```

**Rejected**: Custom test data generators (TestStubs exist), wrapper abstractions (use Nautilus directly), complex cleanup (gc.collect works)

---

## Research Question 4: Test Double Verification

### Decision: Simple interface tests, skip complex contract patterns

**What**: Write basic tests that verify test doubles match real behavior for critical methods.

**Why**: Full contract testing with Protocols is over-engineered. Simple tests catch issues.

**Pattern**:
```python
# tests/component/test_doubles.py
def test_test_engine_submit_order_interface():
    """Verify TestTradingEngine has same interface as real engine."""
    from tests.component.doubles import TestTradingEngine

    engine = TestTradingEngine()
    order_id = engine.submit_order(symbol="BTC", side="BUY", quantity=1)

    assert isinstance(order_id, str)
    assert len(engine.orders) == 1
```

**Rejected**: Python Protocols with runtime checking (too complex), shared contract test base classes (over-engineered), formal interface verification (YAGNI)

---

## Research Question 5: Reusable Test Scenarios

### Decision: Simple dataclasses with fixture parametrization

**What**: Define scenarios as frozen dataclasses, use `@pytest.mark.parametrize` for reuse.

**Why**: Simplest approach that works. No need for runner protocols or backend abstraction.

**Pattern**:
```python
# tests/fixtures/scenarios.py
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class MarketScenario:
    """Reusable market test data."""
    name: str
    prices: list[Decimal]
    expected_trades: int

# Define scenarios
VOLATILE = MarketScenario(
    name="volatile",
    prices=[Decimal("100"), Decimal("110"), Decimal("95"), Decimal("105")],
    expected_trades=3
)

# Usage with parametrization
@pytest.mark.parametrize("scenario", [VOLATILE, TRENDING])
def test_strategy_with_scenario(scenario):
    """Test runs twice: once per scenario."""
    result = run_strategy(scenario.prices)
    assert abs(result.trades - scenario.expected_trades) <= 1
```

**Rejected**: Backend abstraction layers (over-engineered), multiple runner implementations (YAGNI), scenario builders (keep simple)

---

## Summary of Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| **Test Isolation** | pytest-xdist --forked | One tool, battle-tested, simple |
| **Async Loops** | pytest-asyncio auto mode | Built-in behavior works, minimal fixture if needed |
| **Nautilus Patterns** | Use TestStubs, minimal configs | Don't reinvent, leverage existing tools |
| **Test Doubles** | Simple interface tests | Skip complex contracts, keep maintainable |
| **Scenarios** | Dataclass + parametrize | Simplest reuse pattern |

---

## Implementation Notes

### Dependencies to Add
```bash
# Only one new dependency
uv add --dev pytest-xdist>=3.6.1

# Already have these (no changes)
pytest>=8.4.2
pytest-asyncio>=0.23.0
```

### Directory Structure
```
tests/
├── unit/              # Pure Python, -n auto
├── component/         # Test doubles, -n auto
│   └── doubles/       # Simple test double implementations
├── integration/       # Real Nautilus, -n auto --forked
└── fixtures/
    └── scenarios.py   # Simple dataclass scenarios
```

### Make file Targets
```makefile
test-unit:
	pytest tests/unit -v -n auto

test-component:
	pytest tests/component -v -n auto

test-integration:
	pytest tests/integration -v -n auto --forked

test-all:
	pytest tests -v -n auto
```

###pytest Configuration
```ini
# pytest.ini
[pytest]
testpaths = tests
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function

markers =
    unit: Pure Python unit tests (no Nautilus)
    component: Component tests with test doubles
    integration: Integration tests with Nautilus (subprocess isolated)
```

---

## Key Simplifications Applied

### What We're NOT Doing (YAGNI):

❌ pytest-isolate with resource limits
❌ Complex Protocol-based contract testing
❌ Custom event loop isolation frameworks
❌ ScenarioRunner abstraction layers
❌ Sophisticated cleanup fixtures
❌ Custom C extension state management

### What We ARE Doing (Essential):

✅ Directory separation (unit/component/integration)
✅ pytest-xdist for parallel + subprocess isolation
✅ Simple async cleanup with pytest-asyncio
✅ Use Nautilus TestStubs (already exist)
✅ Basic test doubles with simple verification
✅ Dataclass scenarios with parametrization
✅ Basic gc.collect() for cleanup

---

## Validation Criteria

✅ All tests categorized (unit/component/integration)
✅ Unit tests run in parallel without isolation (<5s total)
✅ Integration tests run with subprocess isolation (crashes don't cascade)
✅ Async tests clean up properly (no warnings)
✅ Test doubles are simple (<100 lines each)
✅ No additional testing frameworks needed

---

**Research Complete**: 2025-01-22
**Next Phase**: Design (data-model.md, contracts/, quickstart.md)
**Complexity Level**: Minimal - maintainable long-term
**Dependencies Added**: 1 (pytest-xdist only)
