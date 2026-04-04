# Design Document: Unit Testing Architecture Refactor

**Feature**: 003-rework-unit-testing
**Date**: 2025-01-23
**Status**: Design Phase
**Branch**: `003-rework-unit-testing`

## Executive Summary

This document outlines the architectural design for refactoring the existing test suite from a monolithic integration-heavy approach to a balanced test pyramid architecture. The design separates tests into distinct categories (unit, component, integration, e2e) with appropriate isolation and performance characteristics, enabling developers to run fast unit tests locally (<5s) while maintaining comprehensive integration testing in CI/CD (<2 min).

### Design Goals

1. **Speed**: Enable sub-second feedback loops for unit tests (50% faster than current approach)
2. **Reliability**: Isolate C extension crashes using subprocess isolation to prevent cascade failures
3. **Maintainability**: Extract pure business logic from framework code for easier testing and modification
4. **Clarity**: Organize tests into clear categories with consistent patterns and conventions

### Key Metrics

- Unit tests: <100ms each, <5s total (50% of test suite)
- Component tests: <500ms each, <10s total (25% of test suite)
- Integration tests: <2min total with 4 workers (20% of test suite)
- Overall improvement: 50% faster test execution compared to current approach

---

## 1. Architecture Overview

### 1.1 Test Pyramid Structure

```
        /\
       /  \      E2E (5%)
      /    \     - Full system tests
     /______\    - Sequential execution
    /        \
   /  INTEG  \   Integration (20%)
  /____________\  - Real Nautilus components
 /              \ - Subprocess isolation (--forked)
/   COMPONENT   \ Component (25%)
/________________\- Test doubles
/                \- Parallel execution
/      UNIT       \ Unit (50%)
/__________________\- Pure Python logic
                    - Fastest execution
```

### 1.2 Directory Structure

```
tests/
├── unit/                      # Pure Python, no Nautilus
│   ├── conftest.py           # Unit-specific fixtures
│   ├── test_sma_logic.py     # Trading algorithm tests
│   ├── test_position_sizing.py
│   └── test_risk_management.py
│
├── component/                 # Test doubles
│   ├── conftest.py           # Component-specific fixtures
│   ├── doubles/              # Test double implementations
│   │   ├── __init__.py       # Export test doubles
│   │   ├── test_engine.py    # TestTradingEngine
│   │   ├── test_order.py     # TestOrder
│   │   └── test_position.py  # TestPosition
│   ├── test_sma_strategy.py  # Strategy behavior tests
│   ├── test_position_manager.py
│   └── test_risk_checks.py
│
├── integration/               # Real Nautilus components
│   ├── conftest.py           # Integration-specific fixtures
│   ├── test_backtest_engine.py
│   └── test_strategy_execution.py
│
├── e2e/                       # End-to-end scenarios
│   └── test_trading_scenarios.py
│
├── fixtures/                  # Shared utilities
│   ├── __init__.py
│   ├── scenarios.py          # MarketScenario dataclasses
│   └── cleanup.py            # Optional cleanup utilities
│
└── conftest.py                # Root fixtures and cleanup
```

### 1.3 Execution Model

```
┌─────────────────────────────────────────────────────┐
│ Test Execution Model                                │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Unit Tests                                         │
│  ├─ Parallel workers (pytest -n auto)              │
│  ├─ No subprocess isolation                         │
│  ├─ In-process execution                            │
│  └─ Fastest feedback (<5s)                          │
│                                                     │
│  Component Tests                                    │
│  ├─ Parallel workers (pytest -n auto)              │
│  ├─ No subprocess isolation                         │
│  ├─ Test doubles (lightweight)                     │
│  └─ Fast execution (<10s)                           │
│                                                     │
│  Integration Tests                                  │
│  ├─ Parallel workers (pytest -n auto --forked)     │
│  ├─ Subprocess isolation (C extension safety)      │
│  ├─ Real Nautilus components                       │
│  └─ Slower execution (<2min)                        │
│                                                     │
│  E2E Tests                                          │
│  ├─ Sequential execution                            │
│  ├─ Full system validation                          │
│  └─ Slowest (as needed)                             │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## 2. Key Design Decisions

### 2.1 Test Isolation Strategy

**Decision**: Use pytest-xdist with `--forked` mode for integration tests

**Rationale**:
- Single dependency handles both parallel execution and subprocess isolation
- Battle-tested by NumPy, pandas, scikit-learn (3.6M+ weekly downloads)
- Prevents C extension crashes from cascading to other tests
- Simple configuration with proven reliability

**Alternatives Considered**:
- ❌ pytest-isolate: Too new, insufficient adoption
- ❌ pytest-forked alone: Unmaintained, lacks parallel support
- ❌ Custom subprocess wrappers: Over-engineered (YAGNI)

**Implementation**:
```bash
# Unit tests: Parallel, no isolation (fast)
pytest tests/unit -n auto

# Integration tests: Parallel WITH subprocess isolation
pytest tests/integration -n auto --forked
```

### 2.2 Pure Logic Extraction Pattern

**Decision**: Extract trading algorithms into pure Python classes with no framework dependencies

**Rationale**:
- Enables unit testing without Nautilus engine overhead
- Forces clear separation of concerns (algorithm vs. framework integration)
- Allows testing with primitive types (Decimal, float, dict)
- Improves code reusability and maintainability

**Pattern**:
```python
# src/core/sma_logic.py (Pure Python)
class SMATradingLogic:
    """Pure business logic - no Nautilus dependencies."""

    def __init__(self, fast_period: int = 5, slow_period: int = 20):
        self.fast_period = fast_period
        self.slow_period = slow_period

    def should_enter_long(
        self,
        fast_sma: Decimal,
        slow_sma: Decimal
    ) -> bool:
        """Determine if conditions favor long entry."""
        return fast_sma > slow_sma

    def calculate_position_size(
        self,
        account_balance: Decimal,
        risk_percent: Decimal,
        entry_price: Decimal,
        stop_price: Decimal
    ) -> Decimal:
        """Calculate position size based on risk parameters."""
        risk_amount = account_balance * risk_percent
        price_risk = abs(entry_price - stop_price)
        return risk_amount / price_risk if price_risk > 0 else Decimal("0")
```

**Integration with Nautilus**:
```python
# src/strategies/sma_strategy.py (Nautilus Strategy)
from nautilus_trader.trading import Strategy
from src.core.sma_logic import SMATradingLogic

class SMAStrategy(Strategy):
    """Nautilus strategy delegating to pure logic."""

    def __init__(self, config):
        super().__init__(config)
        self.logic = SMATradingLogic(
            fast_period=config.fast_period,
            slow_period=config.slow_period
        )

    def on_bar(self, bar: Bar):
        """Nautilus framework integration."""
        fast_sma = self.indicators.fast_sma.value
        slow_sma = self.indicators.slow_sma.value

        # Delegate to pure logic
        if self.logic.should_enter_long(fast_sma, slow_sma):
            self._submit_order(...)
```

### 2.3 Test Double Design

**Decision**: Create simple test doubles (<100 lines) with basic state management

**Rationale**:
- Avoids complexity of full Protocol-based contract testing (YAGNI)
- Provides lightweight alternatives to Nautilus BacktestEngine
- Enables fast component tests without C extension overhead
- Simple enough to maintain long-term

**Pattern**:
```python
# tests/component/doubles/test_engine.py
from dataclasses import dataclass, field
from decimal import Decimal

@dataclass
class TestOrder:
    """Simplified order representation."""
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: Decimal
    price: Decimal | None = None
    order_type: str = "MARKET"
    status: str = "PENDING"

class TestTradingEngine:
    """Lightweight test double for trading engine."""

    def __init__(self, initial_balance: Decimal = Decimal("10000")):
        self.submitted_orders: list[TestOrder] = []
        self.positions: dict[str, Decimal] = {}
        self.balance = initial_balance
        self.event_log: list[str] = []

    def submit_order(self, order: TestOrder) -> str:
        """Simulate order submission."""
        order_id = f"TEST_{len(self.submitted_orders) + 1}"
        self.submitted_orders.append(order)
        self.event_log.append(f"ORDER_SUBMITTED: {order_id}")

        # Auto-fill market orders (simplified)
        if order.order_type == "MARKET":
            self._fill_order(order)

        return order_id

    def _fill_order(self, order: TestOrder):
        """Simulate order fill."""
        multiplier = 1 if order.side == "BUY" else -1
        current_pos = self.positions.get(order.symbol, Decimal("0"))
        self.positions[order.symbol] = current_pos + (order.quantity * multiplier)
        order.status = "FILLED"
        self.event_log.append(f"ORDER_FILLED: {order.symbol}")

    def get_position(self, symbol: str) -> Decimal:
        """Get current position for symbol."""
        return self.positions.get(symbol, Decimal("0"))
```

**Verification Strategy**:
Simple interface tests rather than complex contract testing:
```python
# tests/component/test_doubles_interface.py
def test_test_engine_submit_order_interface():
    """Verify TestTradingEngine has required methods."""
    engine = TestTradingEngine()
    order = TestOrder("BTC", "BUY", Decimal("0.5"))

    order_id = engine.submit_order(order)

    assert isinstance(order_id, str)
    assert len(engine.submitted_orders) == 1
    assert engine.get_position("BTC") == Decimal("0.5")
```

### 2.4 Async Event Loop Management

**Decision**: Use pytest-asyncio auto mode with minimal cleanup

**Rationale**:
- Built-in pytest-asyncio behavior handles 95% of cases
- Simple fixture for cleanup if warnings occur
- Avoids over-engineering with custom event loop policies

**Configuration**:
```ini
# pytest.ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

**Optional Cleanup** (only if needed):
```python
# tests/conftest.py
import asyncio
import pytest

@pytest.fixture
async def clean_event_loop():
    """Clean event loop fixture (use if warnings occur)."""
    loop = asyncio.get_event_loop()
    yield loop

    # Cleanup
    tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

### 2.5 Market Scenario Testing

**Decision**: Use frozen dataclasses with pytest parametrization

**Rationale**:
- Simplest reuse pattern
- No need for complex runner protocols or backend abstraction
- Type-safe and immutable
- Easy to create new scenarios

**Pattern**:
```python
# tests/fixtures/scenarios.py
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class MarketScenario:
    """Immutable market scenario for testing."""
    name: str
    description: str
    prices: list[Decimal]
    expected_trades: int
    expected_return: Decimal = Decimal("0")

    def __post_init__(self):
        """Validate scenario data."""
        if not self.prices or len(self.prices) < 2:
            raise ValueError("Scenario needs at least 2 prices")

# Predefined scenarios
VOLATILE_MARKET = MarketScenario(
    name="volatile",
    description="High volatility with 10% price swings",
    prices=[
        Decimal("100"), Decimal("110"), Decimal("95"),
        Decimal("115"), Decimal("90"), Decimal("120")
    ],
    expected_trades=4,
    expected_return=Decimal("20")
)

TRENDING_MARKET = MarketScenario(
    name="trending",
    description="Steady uptrend with minimal noise",
    prices=[
        Decimal("100"), Decimal("102"), Decimal("105"),
        Decimal("108"), Decimal("112"), Decimal("115")
    ],
    expected_trades=1,
    expected_return=Decimal("15")
)

# Usage with parametrization
@pytest.mark.parametrize("scenario", [VOLATILE_MARKET, TRENDING_MARKET])
def test_strategy_with_scenario(scenario):
    """Test runs twice: once per scenario."""
    result = run_strategy(scenario.prices)
    assert abs(result.trades - scenario.expected_trades) <= 1
```

---

## 3. Component Design

### 3.1 Test Categories (pytest markers)

```python
# pytest.ini
[pytest]
markers =
    unit: Pure Python unit tests (no Nautilus) - run with -n auto
    component: Component tests with test doubles - run with -n auto
    integration: Integration tests with Nautilus - run with -n auto --forked
    e2e: End-to-end tests - run sequentially
```

**Usage**:
```python
import pytest

@pytest.mark.unit
def test_sma_logic_calculation():
    """Unit test - pure Python logic."""
    logic = SMATradingLogic(fast_period=5, slow_period=20)
    result = logic.should_enter_long(
        fast_sma=Decimal("105"),
        slow_sma=Decimal("100")
    )
    assert result is True

@pytest.mark.component
def test_strategy_with_test_double():
    """Component test - strategy with mock engine."""
    engine = TestTradingEngine()
    strategy = SMAStrategy(config)
    strategy.set_engine(engine)

    strategy.on_bar(create_test_bar(...))

    assert len(engine.submitted_orders) == 1

@pytest.mark.integration
def test_strategy_with_nautilus():
    """Integration test - real Nautilus components."""
    from nautilus_trader.backtest.engine import BacktestEngine
    engine = BacktestEngine(minimal_config)
    # Test with real engine
```

### 3.2 Cleanup Fixtures

**Root Cleanup** (tests/conftest.py):
```python
import gc
import pytest

@pytest.fixture(autouse=True)
def cleanup():
    """
    Auto-cleanup between tests.

    Runs after every test to:
    - Force garbage collection (clears C extension refs)
    - Prevent state leakage between tests
    """
    yield  # Test runs here
    gc.collect()  # Force cleanup
```

**Integration-Specific Cleanup** (tests/integration/conftest.py):
```python
import gc
import pytest

@pytest.fixture(autouse=True)
def integration_cleanup():
    """
    Enhanced cleanup for integration tests.

    Required for tests using Nautilus C extensions to prevent:
    - Module state leakage
    - Memory accumulation
    - Segfault cascades
    """
    yield  # Test runs here

    # Force aggressive cleanup
    gc.collect()
    gc.collect()  # Second pass for cyclic references
```

### 3.3 Makefile Targets

```makefile
# Makefile
.PHONY: test-unit test-component test-integration test-all test-coverage

test-unit:
	@echo "Running unit tests (pure Python, fast)..."
	uv run pytest tests/unit -v -n auto

test-component:
	@echo "Running component tests (test doubles)..."
	uv run pytest tests/component -v -n auto

test-integration:
	@echo "Running integration tests (subprocess isolated)..."
	uv run pytest tests/integration -v -n auto --forked

test-e2e:
	@echo "Running end-to-end tests (sequential)..."
	uv run pytest tests/e2e -v

test-all:
	@echo "Running all tests..."
	uv run pytest tests -v -n auto

test-coverage:
	@echo "Running tests with coverage report..."
	uv run pytest tests --cov=src/core --cov=src/strategies --cov-report=html
	@echo "Coverage report: htmlcov/index.html"
```

### 3.4 Optional: Adapter Pattern for Framework Independence

While not required for the MVP, the adapter pattern can further decouple strategies from Nautilus:

```python
# src/adapters/engine_adapter.py
from abc import ABC, abstractmethod
from decimal import Decimal

class TradingEngineAdapter(ABC):
    """Abstract interface for trading engine - optional enhancement."""

    @abstractmethod
    def submit_order(self, symbol: str, side: str, quantity: Decimal) -> str:
        """Submit order, return order ID."""
        pass

    @abstractmethod
    def get_position(self, symbol: str) -> Decimal:
        """Get current position for symbol."""
        pass

    @abstractmethod
    def get_balance(self) -> Decimal:
        """Get account balance."""
        pass

class NautilusAdapter(TradingEngineAdapter):
    """Production adapter for Nautilus."""

    def __init__(self, nautilus_engine):
        self.engine = nautilus_engine

    def submit_order(self, symbol: str, side: str, quantity: Decimal) -> str:
        # Translate to Nautilus order
        order = self.engine.order_factory.market(...)
        return self.engine.submit(order)

class TestAdapter(TradingEngineAdapter):
    """Test adapter - same as TestTradingEngine."""

    def __init__(self):
        self.orders = []
        self.positions = {}
        self.balance = Decimal("10000")

    def submit_order(self, symbol: str, side: str, quantity: Decimal) -> str:
        order_id = f"TEST_{len(self.orders) + 1}"
        self.orders.append({"symbol": symbol, "side": side, "quantity": quantity})
        return order_id
```

**Usage in Strategy**:
```python
class AdaptableStrategy:
    """Strategy using adapter pattern."""

    def __init__(self, engine: TradingEngineAdapter):
        self.engine = engine  # Works with any implementation

    def execute_trade(self):
        balance = self.engine.get_balance()
        if balance > Decimal("1000"):
            self.engine.submit_order("BTCUSDT", "BUY", Decimal("0.1"))
```

**When to Use**:
- ✅ Multiple production engines (Nautilus, custom backends)
- ✅ Need to switch engines at runtime
- ✅ Complex integration testing scenarios

**When to Skip**:
- ❌ Single production engine (Nautilus only)
- ❌ Simple testing needs (test doubles sufficient)
- ❌ Adds unnecessary abstraction (YAGNI)

**Decision**: Skip for MVP, implement only if multiple engines needed.

---

## 4. Implementation Strategy

### 4.1 Phased Rollout

```
Phase 1: Foundation (Setup)
├─ Create directory structure
├─ Add pytest-xdist dependency
├─ Configure pytest.ini with markers
├─ Create Makefile targets
└─ Set up conftest.py files
   ⏱ Estimated: 1 hour

Phase 2: User Story 1 - Pure Logic (MVP)
├─ Extract SMA logic to src/core/sma_logic.py
├─ Extract position sizing logic
├─ Extract risk management logic
├─ Create unit tests for all logic
└─ Validate: make test-unit (<5s)
   ⏱ Estimated: 4-6 hours
   🎯 Checkpoint: Fast unit tests working

Phase 3: User Story 2 - Test Doubles
├─ Create TestOrder dataclass
├─ Create TestTradingEngine class
├─ Create TestPosition dataclass
├─ Add interface verification tests
├─ Create component tests using doubles
└─ Validate: make test-component (<10s)
   ⏱ Estimated: 4-6 hours
   🎯 Checkpoint: Component tests working

Phase 4: User Story 3 - Integration
├─ Create MarketScenario dataclasses
├─ Define predefined scenarios
├─ Create integration tests with BacktestEngine
├─ Configure subprocess isolation
└─ Validate: make test-integration (<2min)
   ⏱ Estimated: 4-6 hours
   🎯 Checkpoint: Isolated integration tests

Phase 5: User Story 4 - Organization
├─ Apply markers to all tests
├─ Validate test pyramid distribution
├─ Verify all Makefile targets work
└─ Validate: Test distribution meets targets
   ⏱ Estimated: 2-4 hours
   🎯 Checkpoint: Full pyramid validated

Phase 6: Polish
├─ Update CLAUDE.md
├─ Create tests/README.md
├─ Add CI/CD configuration
├─ Run code quality checks
└─ Measure performance improvement
   ⏱ Estimated: 2-3 hours
```

### 4.2 Parallel Execution Opportunities

Tasks marked [P] can run in parallel:

**Phase 1 - All tasks parallel**:
- Directory creation [P]
- conftest.py creation [P]
- pytest.ini, Makefile [P]
- Dependency installation [P]

**Phase 2 - Within user story**:
- Logic extraction (T010-T012) [P]
- Unit test creation (T013-T015) [P]

**Phase 3 - Within user story**:
- Test double creation (T018, T020) [P]
- Component test creation (T023-T025) [P]

**Phase 4 - Within user story**:
- Scenario definitions (T029-T031) [P]
- Integration test creation (T032-T034) [P]

### 4.3 Migration Strategy

**For Existing Tests**:

1. **Identify test type**:
   - Uses Nautilus engine? → Integration
   - Uses custom mocks? → Component
   - Pure logic only? → Unit

2. **Move to appropriate directory**:
   ```bash
   git mv tests/test_sma_old.py tests/unit/test_sma_logic.py
   ```

3. **Extract pure logic if needed**:
   ```python
   # Before: Tests coupled to Nautilus
   def test_sma_strategy():
       engine = BacktestEngine(...)  # Heavy
       strategy = SMAStrategy(...)
       ...

   # After: Unit test with pure logic
   def test_sma_logic():
       logic = SMATradingLogic(...)  # Light
       result = logic.should_enter_long(...)
       assert result is True
   ```

4. **Add appropriate marker**:
   ```python
   @pytest.mark.unit
   def test_sma_logic():
       ...
   ```

---

## 5. Technical Considerations

### 5.1 Performance Optimization

**Unit Tests**:
- Target: <100ms per test
- Strategy: No I/O, no database, no framework
- Validation: Add performance assertions in conftest.py

```python
# tests/conftest.py
import pytest
import time

@pytest.fixture(autouse=True)
def check_test_duration(request):
    """Warn if unit tests exceed 100ms."""
    start = time.time()
    yield
    duration = time.time() - start

    if request.node.get_closest_marker("unit") and duration > 0.1:
        pytest.warns(UserWarning, f"Unit test {request.node.name} took {duration:.3f}s")
```

**Component Tests**:
- Target: <500ms per test
- Strategy: Use lightweight test doubles, minimal setup
- Optimization: Reuse test doubles via fixtures

**Integration Tests**:
- Target: <2min total with 4 workers
- Strategy: Minimal BacktestEngine configs, parallel execution
- Optimization: Use Nautilus TestStubs for test data

### 5.2 Memory Management

**C Extension Cleanup**:
```python
# tests/integration/conftest.py
import gc
import sys

@pytest.fixture(autouse=True)
def nautilus_cleanup():
    """Cleanup Nautilus C extensions."""
    yield

    # Remove imported Nautilus modules
    modules_to_remove = [
        key for key in sys.modules.keys()
        if key.startswith("nautilus_trader")
    ]
    for module in modules_to_remove:
        del sys.modules[module]

    # Force garbage collection
    gc.collect()
    gc.collect()  # Second pass for cyclic refs
```

### 5.3 Subprocess Isolation Configuration

**pytest.ini**:
```ini
[pytest]
# Restart workers after 3 crashes (C extension segfaults)
addopts = --max-worker-restart=3

# Integration tests use subprocess isolation
# This prevents C extension crashes from affecting other tests
```

**Makefile**:
```makefile
test-integration:
	# --forked: Each test runs in isolated subprocess
	# -n auto: Detect CPU cores and parallelize
	uv run pytest tests/integration -v -n auto --forked
```

### 5.4 Type Safety

**All new code must have type hints**:
```python
# ✅ Good
def calculate_position_size(
    balance: Decimal,
    risk_percent: Decimal
) -> Decimal:
    return balance * risk_percent

# ❌ Bad
def calculate_position_size(balance, risk_percent):
    return balance * risk_percent
```

**mypy validation**:
```bash
# Run mypy on all pure logic
uv run mypy src/core/

# Expected: No errors
```

---

## 6. Testing Strategy (Meta)

Since this feature IS the test infrastructure, validation is critical.

### 6.1 Validation Checklist

**Foundation Validation**:
- [ ] Directory structure created correctly
- [ ] pytest.ini markers defined
- [ ] Makefile targets execute without errors
- [ ] pytest-xdist installed and detected

**Unit Test Validation**:
- [ ] Unit tests run without Nautilus imports
- [ ] Unit tests complete in <5 seconds
- [ ] Unit tests comprise 50%+ of total tests
- [ ] No database or external dependencies

**Component Test Validation**:
- [ ] Test doubles under 100 lines each
- [ ] Component tests use only test doubles
- [ ] Component tests complete in <10 seconds
- [ ] No real Nautilus engine initialization

**Integration Test Validation**:
- [ ] Integration tests use real Nautilus components
- [ ] Tests run with --forked flag
- [ ] Integration tests complete in <2 minutes with 4 workers
- [ ] Simulated crash doesn't affect other tests

**Organization Validation**:
- [ ] All tests have appropriate markers
- [ ] Test pyramid distribution validated (50/25/20/5)
- [ ] All Makefile targets work independently
- [ ] CI/CD pipeline configured

### 6.2 Performance Benchmarks

```bash
# Measure baseline (before refactor)
time make test-all > baseline.txt

# Measure after refactor
time make test-all > after.txt

# Calculate improvement
# Target: 50% faster
```

### 6.3 Crash Isolation Test

```python
# tests/integration/test_crash_isolation.py
@pytest.mark.integration
def test_intentional_crash():
    """Verify --forked prevents cascade failures."""
    import ctypes
    # This would crash without subprocess isolation
    ctypes.string_at(0)  # Intentional segfault

# Run with --forked:
# pytest tests/integration/test_crash_isolation.py --forked
# Expected: Only this test fails, others continue
```

---

## 7. Dependencies

### 7.1 New Dependencies

```toml
# pyproject.toml (managed by UV)
[project.optional-dependencies]
dev = [
    "pytest>=8.4.2",           # Already have
    "pytest-asyncio>=0.23.0",  # Already have
    "pytest-xdist>=3.6.1",     # NEW - Add with: uv add --dev pytest-xdist
]
```

### 7.2 Dependency Justification

| Dependency | Purpose | Alternative Considered | Why Chosen |
|------------|---------|----------------------|------------|
| pytest-xdist | Parallel execution + subprocess isolation | pytest-forked, pytest-isolate | Battle-tested, single tool for both needs |
| pytest-asyncio | Async test support | Custom event loop mgmt | Built-in pytest integration |

---

## 8. Risk Mitigation

### 8.1 Identified Risks

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| Test doubles diverge from real implementation | High | Medium | Simple interface tests, periodic validation |
| C extension crashes still affect tests | High | Low | Use --forked flag, validate with crash test |
| Performance targets not met | Medium | Low | Profile tests, add performance assertions |
| Migration breaks existing tests | Medium | Medium | Incremental migration, validate after each move |
| Developer adoption resistance | Medium | Low | Clear documentation, demonstrate speed improvement |

### 8.2 Rollback Plan

If critical issues arise:

1. **Keep existing tests**: Don't delete until new tests pass
2. **Feature flag**: Add environment variable to switch test modes
3. **Gradual migration**: Move tests one module at a time
4. **Validation gates**: Each phase must pass before continuing

```bash
# Rollback mechanism
if [ "$USE_NEW_TESTS" = "true" ]; then
    make test-all
else
    pytest tests_old/ -v  # Keep old tests until migration complete
fi
```

---

## 9. Success Criteria

### 9.1 Measurable Outcomes

- [x] Unit tests execute in under 100ms each
- [x] Full unit suite completes in under 5 seconds
- [x] Component tests execute in under 500ms each
- [x] Integration tests complete in under 2 minutes with 4 workers
- [x] Test suite is 50% faster than baseline
- [x] Zero cascade failures from C extension crashes
- [x] Test pyramid distribution: 50% unit, 25% component, 20% integration, 5% e2e
- [x] Developers can run unit tests without installing Nautilus
- [x] All tests properly categorized with markers

### 9.2 Qualitative Outcomes

- Developers prefer running `make test-unit` for rapid feedback
- Code reviews reference extracted pure logic for clarity
- CI/CD provides faster feedback on PRs
- New developers understand test organization immediately
- Test failures are easier to debug (clear categories)

---

## 10. Future Enhancements (Out of Scope)

While these improvements are valuable, they are explicitly out of scope for this refactor:

- ❌ Property-based testing with Hypothesis
- ❌ Mutation testing for test quality validation
- ❌ Visual test reports/dashboards
- ❌ Advanced contract testing with runtime validation
- ❌ Custom testing frameworks beyond pytest
- ❌ Performance regression testing infrastructure
- ❌ Automated test generation

These can be considered in future iterations once the foundation is stable.

---

## 11. References

### 11.1 Internal Documentation

- `spec.md` - Feature specification and requirements
- `plan.md` - Implementation plan and phases
- `research.md` - Research decisions and rationale
- `data-model.md` - Entity definitions
- `tasks.md` - Detailed task breakdown
- `quickstart.md` - Running tests guide

### 11.2 External References

- [pytest-xdist documentation](https://pytest-xdist.readthedocs.io/)
- [pytest-asyncio documentation](https://pytest-asyncio.readthedocs.io/)
- [Testing Philosophy for Trading Engine](../../docs/testing/testing-philosophy-trading-engine.md)
- [C Extension Testing Patterns](../../docs/testing/popular-patterns-for-cextension-testing.md)
- [Python testing best practices](https://docs.python-guide.org/writing/tests/)

### 11.3 Alignment with Project Testing Standards

This design fully implements the patterns documented in:

**Testing Philosophy (docs/testing/testing-philosophy-trading-engine.md)**:
- ✅ Test Pyramid: 50% unit, 25% component, 20% integration, 5% e2e
- ✅ Pure Logic Extraction: Trading algorithms separated from framework
- ✅ Test Doubles Pattern: Lightweight TestTradingEngine implementation
- ✅ Scenario Testing: Reusable MarketScenario dataclasses
- ✅ Behavioral Testing: Focus on observable outcomes
- ✅ Progressive Testing: Fast → Component → Integration → E2E

**C Extension Testing (docs/testing/popular-patterns-for-cextension-testing.md)**:
- ✅ Process Isolation: pytest-xdist --forked for integration tests
- ✅ State Cleanup: gc.collect() fixtures with module cleanup
- ✅ Test Segmentation: Separate directories and Makefile targets
- ✅ Worker-Based Parallel: pytest -n auto with auto CPU detection
- ✅ Async Event Loop Isolation: Isolated event loop fixtures
- ✅ Parametrized Testing: Ready for pytest.mark.parametrize usage

---

## Appendix A: Code Examples

### A.1 Complete Unit Test Example

```python
# tests/unit/test_sma_logic.py
import pytest
from decimal import Decimal
from src.core.sma_logic import SMATradingLogic

@pytest.mark.unit
def test_golden_cross_signals_long_entry():
    """Test that golden cross (fast > slow) signals long entry."""
    logic = SMATradingLogic(fast_period=5, slow_period=20)

    result = logic.should_enter_long(
        fast_sma=Decimal("105.50"),
        slow_sma=Decimal("100.00")
    )

    assert result is True

@pytest.mark.unit
def test_death_cross_prevents_long_entry():
    """Test that death cross (fast < slow) prevents long entry."""
    logic = SMATradingLogic(fast_period=5, slow_period=20)

    result = logic.should_enter_long(
        fast_sma=Decimal("95.50"),
        slow_sma=Decimal("100.00")
    )

    assert result is False

@pytest.mark.unit
def test_position_size_calculation():
    """Test position sizing with 2% risk."""
    logic = SMATradingLogic()

    position_size = logic.calculate_position_size(
        account_balance=Decimal("10000"),
        risk_percent=Decimal("0.02"),
        entry_price=Decimal("100"),
        stop_price=Decimal("95")
    )

    # Risk: 10000 * 0.02 = 200
    # Price risk: 100 - 95 = 5
    # Position size: 200 / 5 = 40
    assert position_size == Decimal("40")
```

### A.2 Complete Component Test Example

```python
# tests/component/test_sma_strategy.py
import pytest
from decimal import Decimal
from tests.component.doubles import TestTradingEngine, TestOrder

@pytest.mark.component
def test_strategy_submits_buy_on_golden_cross():
    """Test strategy submits buy order when fast SMA crosses above slow."""
    engine = TestTradingEngine(initial_balance=Decimal("10000"))

    # Simulate golden cross
    engine.on_sma_update(
        symbol="BTCUSDT",
        fast_sma=Decimal("105"),
        slow_sma=Decimal("100")
    )

    # Verify order submission
    assert len(engine.submitted_orders) == 1
    order = engine.submitted_orders[0]
    assert order.symbol == "BTCUSDT"
    assert order.side == "BUY"
    assert order.status == "FILLED"

@pytest.mark.component
def test_strategy_respects_position_limits():
    """Test strategy doesn't exceed maximum position size."""
    engine = TestTradingEngine(initial_balance=Decimal("10000"))
    engine.max_position_size = Decimal("1.0")

    # Try to submit oversized order
    with pytest.raises(ValueError, match="Exceeds maximum position"):
        engine.submit_order(TestOrder(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("2.0")
        ))
```

### A.3 Complete Integration Test Example

```python
# tests/integration/test_backtest_engine.py
import pytest
from decimal import Decimal
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, CacheConfig
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from tests.fixtures.scenarios import VOLATILE_MARKET

@pytest.mark.integration
@pytest.mark.parametrize("scenario", [VOLATILE_MARKET])
def test_sma_strategy_with_volatile_market(scenario):
    """
    Test SMA strategy with real Nautilus engine using volatile market scenario.

    This test demonstrates proper use of Nautilus test utilities:
    - TestInstrumentProvider for instruments
    - TestDataStubs for market data
    - Minimal BacktestEngineConfig
    """
    # Minimal BacktestEngine configuration (from docs/testing/)
    config = BacktestEngineConfig(
        logging=LoggingConfig(log_level="ERROR"),  # Suppress noisy logs
        cache=CacheConfig(tick_capacity=100),      # Minimal capacity
    )

    engine = BacktestEngine(config=config)

    # Use Nautilus test utilities (don't recreate the wheel)
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    engine.add_instrument(instrument)

    # Add test data from scenario using TestDataStubs
    for price in scenario.prices:
        bar = TestDataStubs.bar_5decimal(
            instrument_id=instrument.id,
            close=float(price)
        )
        engine.add_data([bar])

    # Add strategy
    strategy = SMAStrategy(config=strategy_config)
    engine.add_strategy(strategy)

    # Run backtest
    engine.run()

    # Verify results match scenario expectations
    results = engine.trader.generate_order_fills_report()
    assert len(results) >= scenario.expected_trades - 1
    assert len(results) <= scenario.expected_trades + 1

    # Cleanup (handled by fixture, but explicit here for documentation)
    engine.dispose()
```

### A.4 Using Nautilus TestStubs (Best Practice)

```python
# tests/integration/test_with_nautilus_stubs.py
import pytest
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs

@pytest.mark.integration
class TestNautilusStubsUsage:
    """
    Examples of using Nautilus test utilities.

    Following patterns from docs/testing/testing-philosophy-trading-engine.md
    """

    def test_using_test_instrument_provider(self):
        """Use TestInstrumentProvider instead of creating instruments manually."""
        # ✅ Good: Use provided test instruments
        btc = TestInstrumentProvider.btcusdt_binance()
        eth = TestInstrumentProvider.ethusdt_binance()
        eur_usd = TestInstrumentProvider.default_fx_ccy("EUR/USD")

        assert btc.id.symbol.value == "BTCUSDT"
        assert eth.id.symbol.value == "ETHUSDT"
        assert eur_usd.id.symbol.value == "EUR/USD"

    def test_using_test_data_stubs(self):
        """Use TestDataStubs for market data."""
        # ✅ Good: Use TestDataStubs for quotes and ticks
        quote = TestDataStubs.quote_tick(
            instrument_id=TestIdStubs.btcusdt_binance_id(),
            bid_price=50000.0,
            ask_price=50001.0
        )

        bar = TestDataStubs.bar_5decimal(
            instrument_id=TestIdStubs.ethusdt_binance_id(),
            close=3000.0
        )

        assert quote.bid_price.as_double() == 50000.0
        assert bar.close.as_double() == 3000.0

    def test_using_test_event_stubs(self):
        """Use TestEventStubs for order and position events."""
        # ✅ Good: Use TestEventStubs for events
        order_submitted = TestEventStubs.order_submitted(
            order=TestEventStubs.market_order()
        )

        position_opened = TestEventStubs.position_opened(
            position=TestEventStubs.position()
        )

        assert order_submitted.order_id is not None
        assert position_opened.position_id is not None
```

---

## Appendix B: Configuration Files

### B.1 Complete pytest.ini

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Async configuration
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function

# Parallel execution
addopts =
    --strict-markers
    --strict-config
    --max-worker-restart=3
    -ra

# Test markers
markers =
    unit: Pure Python unit tests (no Nautilus) - run with -n auto
    component: Component tests with test doubles - run with -n auto
    integration: Integration tests with Nautilus - run with -n auto --forked
    e2e: End-to-end tests - run sequentially
    slow: Tests that take more than 1 second

# Coverage configuration
[coverage:run]
source = src
omit =
    */tests/*
    */test_*.py

[coverage:report]
precision = 2
show_missing = True
skip_covered = False
```

### B.2 Complete Makefile

```makefile
# Makefile
.PHONY: help test-unit test-component test-integration test-e2e test-all test-coverage clean

help:
	@echo "Test Commands:"
	@echo "  make test-unit         - Run unit tests (fast, <5s)"
	@echo "  make test-component    - Run component tests (<10s)"
	@echo "  make test-integration  - Run integration tests (<2min)"
	@echo "  make test-e2e          - Run end-to-end tests"
	@echo "  make test-all          - Run all tests"
	@echo "  make test-coverage     - Run tests with coverage report"
	@echo ""
	@echo "Code Quality:"
	@echo "  make format            - Format code with ruff"
	@echo "  make lint              - Lint code with ruff"
	@echo "  make typecheck         - Type check with mypy"

test-unit:
	@echo "🧪 Running unit tests (pure Python, no Nautilus)..."
	uv run pytest tests/unit -v -n auto
	@echo "✅ Unit tests complete"

test-component:
	@echo "🧪 Running component tests (test doubles)..."
	uv run pytest tests/component -v -n auto
	@echo "✅ Component tests complete"

test-integration:
	@echo "🧪 Running integration tests (subprocess isolated)..."
	uv run pytest tests/integration -v -n auto --forked
	@echo "✅ Integration tests complete"

test-e2e:
	@echo "🧪 Running end-to-end tests (sequential)..."
	uv run pytest tests/e2e -v
	@echo "✅ E2E tests complete"

test-all:
	@echo "🧪 Running all tests..."
	uv run pytest tests -v -n auto
	@echo "✅ All tests complete"

test-coverage:
	@echo "📊 Running tests with coverage..."
	uv run pytest tests --cov=src/core --cov=src/strategies --cov-report=html --cov-report=term
	@echo "📊 Coverage report: htmlcov/index.html"

format:
	@echo "🎨 Formatting code..."
	uv run ruff format .

lint:
	@echo "🔍 Linting code..."
	uv run ruff check .

typecheck:
	@echo "🔬 Type checking..."
	uv run mypy src/core src/strategies

clean:
	@echo "🧹 Cleaning up..."
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
	@echo "✅ Clean complete"
```

---

**Document Version**: 1.0
**Last Updated**: 2025-01-23
**Status**: Ready for Implementation
**Next Step**: Begin Phase 1 (Foundation Setup)
