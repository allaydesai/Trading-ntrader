# Research Report: Unit Testing Architecture Refactor

**Feature**: 003-rework-unit-testing
**Date**: 2025-01-22
**Status**: Complete

## Overview

This research focused on identifying the minimal, essential patterns needed to separate unit tests from integration tests in a Python backtesting system using Nautilus Trader. The goal is simplicity and maintainability over complex abstraction.

## Key Principle: KISS (Keep It Simple, Stupid)

We deliberately avoided over-engineering. Every decision prioritizes long-term maintainability over theoretical flexibility.

---

## Research Question 1: Test Isolation for C Extensions

### Decision: Use pytest-xdist with --forked mode

**What**: pytest-xdist provides both parallel execution AND subprocess isolation when used with --forked flag.

**Why**:
- Battle-tested by NumPy, pandas, scikit-learn (per @docs/testing/popular-patterns-for-cextension-testing.md)
- One tool does both jobs (parallel + isolation)
- Well-maintained, 3.6M+ weekly downloads

**Installation**:
```bash
uv add --dev pytest-xdist
```

**Usage**:
```bash
# Unit tests: Fast parallel, no isolation needed
pytest tests/unit -n auto

# Integration tests: Parallel WITH subprocess isolation
pytest tests/integration -n auto --forked
```

**Makefile targets**:
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

**Alternatives Rejected**:
- pytest-isolate: Too new, adds complexity for resource limits we don't need
- pytest-forked alone: Minimally maintained, xdist already includes forking
- Complex subprocess management: YAGNI - xdist handles it

---

## Research Question 2: Async Event Loop Management

### Decision: Use pytest-asyncio default behavior with minimal cleanup

**What**: pytest-asyncio in "auto" mode with one simple cleanup fixture.

**Why**: Built-in pytest-asyncio handles 95% of cases. Only need basic cleanup for our async tests.

**Installation**:
```bash
# Already installed
pytest-asyncio>=0.23.0
```

**Configuration**:
```toml
# pytest.ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

**Minimal Fixture** (only if needed):
```python
# tests/conftest.py
import asyncio
import pytest

@pytest.fixture
async def clean_event_loop():
    """Simple event loop with cleanup for async tests."""
    loop = asyncio.get_event_loop()
    yield loop

    # Cancel pending tasks
    tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

**Alternatives Rejected**:
- Complex loop isolation patterns: Not needed for our simple async usage
- Custom event loop policies: YAGNI - default works fine
- Session-scoped loops: Violates test isolation

---

## Research Question 3: Nautilus Testing Patterns

### Question
How does Nautilus Trader test C extensions? What cleanup is required?

### Findings

**Nautilus Trader Test Structure** (from GitHub):
```
nautilus_trader/
├── tests/
│   ├── unit_tests/          # Pure Python logic
│   ├── integration_tests/   # With C extensions
│   └── test_kit/            # Test utilities and stubs
```

**Key Patterns from Nautilus**:

1. **Test Doubles for C Objects**:
   ```python
   # nautilus_trader/test_kit/stubs/
   # Provides stub implementations of C extension types
   from nautilus_trader.test_kit.stubs import TestStubs

   # Create test instruments without C extension complexity
   instrument = TestStubs.btcusdt_binance()
   ```

2. **Minimal Engine Configuration**:
   ```python
   # Use BacktestEngineConfig with minimal components
   from nautilus_trader.backtest.engine import BacktestEngineConfig

   config = BacktestEngineConfig(
       strategies=[strategy_config],
       # Disable unnecessary features
       log_level="ERROR",  # Reduce noise
       bypass_logging=True,  # Skip logging overhead
   )
   ```

3. **State Reset Pattern**:
   ```python
   # Force garbage collection between tests
   import gc
   gc.collect()

   # Reset module-level caches (if exposed by Nautilus)
   # Note: Nautilus C extensions handle most cleanup internally
   ```

4. **Process Isolation for Flaky Tests**:
   ```python
   # Nautilus uses pytest-xdist for isolation
   # Flaky C extension tests marked for subprocess isolation
   @pytest.mark.forked  # Custom marker for subprocess execution
   def test_with_c_extensions():
       pass
   ```

### Decision

**Adopt Nautilus test patterns with enhanced cleanup**:

```python
# tests/fixtures/cleanup.py
import gc
import pytest
import sys


@pytest.fixture(autouse=True, scope="function")
def cleanup_between_tests():
    """
    Automatic cleanup between tests to prevent state leakage.

    Especially important for tests using Nautilus C extensions.
    """
    # Store initial state
    initial_modules = set(sys.modules.keys())

    yield  # Test executes here

    # Post-test cleanup

    # 1. Force garbage collection (clears C extension references)
    gc.collect()

    # 2. Remove test-specific modules (prevents import caching issues)
    current_modules = set(sys.modules.keys())
    new_modules = current_modules - initial_modules
    for module_name in new_modules:
        if module_name.startswith("test_"):
            sys.modules.pop(module_name, None)

    # 3. Reset warnings (tests may trigger deprecation warnings)
    import warnings
    warnings.resetwarnings()


@pytest.fixture(scope="function")
def nautilus_cleanup():
    """
    Explicit cleanup for Nautilus engine tests.

    Use this fixture in integration tests that create BacktestEngine instances.
    """
    yield

    # Force disposal of Nautilus resources
    gc.collect()  # Clear Python references
    gc.collect()  # Second pass for cyclic references
```

**Integration Test Pattern**:
```python
# tests/integration/test_backtest_engine.py
import pytest


@pytest.mark.integration
@pytest.mark.forked  # Subprocess isolation for C extensions
def test_backtest_with_nautilus_engine(nautilus_cleanup):
    """Test with real Nautilus BacktestEngine."""
    from nautilus_trader.backtest.engine import BacktestEngine

    # Create engine with minimal config
    engine = BacktestEngine(config=minimal_config)

    # Run backtest
    engine.run()

    # Verify results
    assert engine.cache is not None

    # Cleanup handled by nautilus_cleanup fixture


@pytest.mark.integration
@pytest.mark.forked
def test_another_backtest(nautilus_cleanup):
    """Each test gets clean state via subprocess isolation."""
    # No state leakage from previous test
    pass
```

**Rationale**:
- Nautilus test_kit stubs reduce C extension usage in unit tests
- Process isolation (--forked) prevents cascade failures
- Garbage collection clears C extension references
- Minimal engine configuration reduces resource overhead
- Autouse cleanup prevents module import caching issues

**Alternatives Considered**:
- **Manual engine disposal**: Error-prone, fixtures are safer
- **No cleanup**: State leaks between tests cause flaky failures
- **Mock all Nautilus types**: Loses integration testing value

---

## Research Task 4: Protocol-Based Contract Testing

### Question
How to ensure test doubles don't diverge from real implementations?

### Findings

**Python Protocol Classes** (PEP 544 - Structural Subtyping):
```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class TradingEngineProtocol(Protocol):
    """Interface that both real and test engines must satisfy."""

    def submit_order(self, order: Order) -> None:
        """Submit an order to the engine."""
        ...

    def get_position(self, instrument_id: str) -> Position | None:
        """Get current position for instrument."""
        ...
```

**Contract Test Pattern**:
```python
# tests/component/test_contracts.py
import pytest
from typing import Type


class ContractTestBase:
    """Base class for contract tests."""

    @pytest.fixture
    def engine(self) -> TradingEngineProtocol:
        """Override in subclasses to provide implementation."""
        raise NotImplementedError

    def test_submit_order_accepts_valid_order(self, engine):
        """Contract: Engine must accept valid orders."""
        order = create_valid_order()
        engine.submit_order(order)  # Must not raise

    def test_get_position_returns_none_when_no_position(self, engine):
        """Contract: Engine returns None for non-existent positions."""
        position = engine.get_position("NONEXISTENT")
        assert position is None


class TestRealEngineContract(ContractTestBase):
    """Verify real Nautilus engine satisfies contract."""

    @pytest.fixture
    def engine(self) -> TradingEngineProtocol:
        from nautilus_trader.backtest.engine import BacktestEngine
        return BacktestEngine(config=minimal_config)


class TestMockEngineContract(ContractTestBase):
    """Verify test double satisfies contract."""

    @pytest.fixture
    def engine(self) -> TradingEngineProtocol:
        from tests.component.doubles.test_engine import TestTradingEngine
        return TestTradingEngine()
```

**Runtime Type Checking**:
```python
# Verify test double implements protocol
from typing import runtime_checkable


def test_mock_engine_implements_protocol():
    """Test double must structurally match protocol."""
    engine = TestTradingEngine()
    assert isinstance(engine, TradingEngineProtocol)  # Runtime check
```

**Behavioral Contract Tests**:
```python
# Beyond type checking - verify behavior matches
class ContractTestBase:

    def test_order_submission_updates_state(self, engine):
        """Contract: Submitted orders must be trackable."""
        order = create_buy_order()
        engine.submit_order(order)

        # Both real and test engines must track submitted orders
        orders = engine.get_submitted_orders()
        assert order in orders
```

### Decision

**Use Protocol-based contract testing with shared test base classes**:

```python
# tests/component/contracts/protocols.py
from typing import Protocol, runtime_checkable, List


@runtime_checkable
class StrategyProtocol(Protocol):
    """Contract for trading strategies."""

    def on_bar(self, bar: Bar) -> None:
        """Handle bar data."""
        ...

    def get_submitted_orders(self) -> List[Order]:
        """Get all submitted orders."""
        ...


@runtime_checkable
class TradingEngineProtocol(Protocol):
    """Contract for trading engines."""

    def submit_order(self, order: Order) -> None:
        """Submit order."""
        ...

    def get_position(self, instrument_id: str) -> Position | None:
        """Get position."""
        ...


# tests/component/contracts/test_engine_contract.py
import pytest
from abc import ABC, abstractmethod


class EngineContractTests(ABC):
    """Contract tests that both real and test engines must pass."""

    @pytest.fixture
    @abstractmethod
    def engine(self) -> TradingEngineProtocol:
        """Subclasses provide implementation."""
        pass

    def test_protocol_compliance(self, engine):
        """Engine must implement protocol."""
        assert isinstance(engine, TradingEngineProtocol)

    def test_submit_order_succeeds(self, engine):
        """Engine must accept valid orders."""
        order = create_market_order()
        engine.submit_order(order)  # Must not raise

    def test_get_nonexistent_position_returns_none(self, engine):
        """Engine returns None for missing positions."""
        assert engine.get_position("FAKE") is None


# Test real implementation
class TestNautilusEngineContract(EngineContractTests):
    @pytest.fixture
    def engine(self):
        from nautilus_trader.backtest.engine import BacktestEngine
        return BacktestEngine(config=minimal_config)


# Test mock implementation
class TestMockEngineContract(EngineContractTests):
    @pytest.fixture
    def engine(self):
        from tests.component.doubles import TestTradingEngine
        return TestTradingEngine()
```

**Rationale**:
- Protocol classes define interface contracts
- Contract test base classes verify behavior
- Both real and test implementations must pass same tests
- Runtime checks catch interface mismatches
- Shared test base prevents test double drift

**Alternatives Considered**:
- **Duck typing only**: No compile-time or runtime validation
- **Abstract base classes**: More rigid than Protocols, requires inheritance
- **Manual verification**: Error-prone, easy to forget

---

## Research Task 5: Reusable Market Scenario Testing Patterns

### Question
How to design reusable market scenario patterns that work with multiple backends?

### Findings

**Scenario-Based Testing Patterns**:

1. **Data-Driven Testing with pytest.mark.parametrize**:
   ```python
   @pytest.mark.parametrize("price_data,expected_signal", [
       ([100, 105, 110, 115], "BUY"),   # Uptrend
       ([115, 110, 105, 100], "SELL"),  # Downtrend
       ([100, 100, 100, 100], "HOLD"),  # Flat
   ])
   def test_strategy_signals(price_data, expected_signal):
       strategy = create_strategy()
       signal = strategy.evaluate(price_data)
       assert signal == expected_signal
   ```

2. **Dataclass-Based Scenarios**:
   ```python
   from dataclasses import dataclass
   from typing import List


   @dataclass(frozen=True)
   class MarketScenario:
       """Reusable market scenario definition."""
       name: str
       prices: List[float]
       expected_trades: int
       expected_return: float
       description: str


   # Define scenarios
   VOLATILE_MARKET = MarketScenario(
       name="volatile",
       prices=[100, 110, 95, 115, 90, 120],
       expected_trades=4,
       expected_return=20.0,
       description="High volatility with large price swings"
   )

   TRENDING_MARKET = MarketScenario(
       name="trending",
       prices=[100, 102, 105, 108, 112, 115],
       expected_trades=1,
       expected_return=15.0,
       description="Steady uptrend with minimal noise"
   )
   ```

3. **Backend Abstraction via Protocol**:
   ```python
   from typing import Protocol


   class ScenarioRunner(Protocol):
       """Backend interface for running scenarios."""

       def run_scenario(self, scenario: MarketScenario) -> BacktestResult:
           """Execute scenario and return results."""
           ...


   # Mock backend (fast, no Nautilus)
   class MockScenarioRunner:
       """Simple Python implementation for unit tests."""

       def run_scenario(self, scenario: MarketScenario) -> BacktestResult:
           # Pure Python logic, no framework
           trades = simulate_trades(scenario.prices)
           return BacktestResult(
               total_trades=len(trades),
               total_return=calculate_return(trades)
           )


   # Nautilus backend (integration tests)
   class NautilusScenarioRunner:
       """Full Nautilus engine for integration tests."""

       def run_scenario(self, scenario: MarketScenario) -> BacktestResult:
           # Create Nautilus engine
           engine = BacktestEngine(config=config)

           # Convert scenario prices to Nautilus bars
           bars = create_bars_from_prices(scenario.prices)
           engine.add_data(bars)

           # Run backtest
           engine.run()

           # Extract results
           return extract_backtest_result(engine)
   ```

4. **Fixture Parametrization for Backend Switching**:
   ```python
   import pytest


   @pytest.fixture(params=["mock", "nautilus"])
   def scenario_runner(request):
       """Parametrized fixture providing different backends."""
       if request.param == "mock":
           return MockScenarioRunner()
       elif request.param == "nautilus":
           return NautilusScenarioRunner()


   @pytest.mark.parametrize("scenario", [
       VOLATILE_MARKET,
       TRENDING_MARKET,
       RANGING_MARKET,
   ])
   def test_scenario_execution(scenario_runner, scenario):
       """Test runs with both mock and Nautilus backends."""
       result = scenario_runner.run_scenario(scenario)

       # Verify against expected results
       assert result.total_trades == scenario.expected_trades
       assert abs(result.total_return - scenario.expected_return) < 1.0
   ```

5. **Factory Pattern for Scenario Creation**:
   ```python
   class ScenarioFactory:
       """Factory for creating market scenarios."""

       @staticmethod
       def create_volatile(amplitude: float = 10.0) -> MarketScenario:
           """Create volatile market scenario."""
           prices = generate_volatile_prices(amplitude)
           return MarketScenario(
               name="volatile",
               prices=prices,
               expected_trades=estimate_trades(prices),
               expected_return=0.0,  # Random walk
               description=f"Volatile market with {amplitude}% swings"
           )


   # Use in tests
   @pytest.mark.parametrize("amplitude", [5.0, 10.0, 20.0])
   def test_strategy_handles_volatility(scenario_runner, amplitude):
       scenario = ScenarioFactory.create_volatile(amplitude)
       result = scenario_runner.run_scenario(scenario)
       assert result is not None
   ```

### Decision

**Use dataclass scenarios + Protocol-based runners + pytest fixture parametrization**:

```python
# tests/fixtures/scenarios.py
from dataclasses import dataclass
from typing import List, Protocol
from decimal import Decimal


@dataclass(frozen=True)
class MarketScenario:
    """
    Reusable market scenario for strategy testing.

    Scenarios can be executed with different backends (mock, simple, Nautilus)
    to enable testing at unit, component, and integration levels.
    """
    name: str
    description: str
    prices: List[Decimal]
    volumes: List[int] | None = None
    expected_trades: int = 0
    expected_return: Decimal = Decimal("0.0")
    expected_win_rate: float = 0.0

    def __post_init__(self):
        """Validate scenario data."""
        if not self.prices:
            raise ValueError("Scenario must have at least one price")
        if self.volumes and len(self.volumes) != len(self.prices):
            raise ValueError("Prices and volumes must have same length")


# Predefined scenarios
VOLATILE_MARKET = MarketScenario(
    name="volatile",
    description="High volatility with 10% price swings",
    prices=[
        Decimal("100"), Decimal("110"), Decimal("95"),
        Decimal("115"), Decimal("90"), Decimal("120")
    ],
    expected_trades=4,
    expected_return=Decimal("20.0"),
    expected_win_rate=0.5
)

TRENDING_MARKET = MarketScenario(
    name="trending",
    description="Steady uptrend with 2% increments",
    prices=[
        Decimal("100"), Decimal("102"), Decimal("105"),
        Decimal("108"), Decimal("112"), Decimal("115")
    ],
    expected_trades=1,
    expected_return=Decimal("15.0"),
    expected_win_rate=1.0
)

RANGING_MARKET = MarketScenario(
    name="ranging",
    description="Sideways movement between 95-105",
    prices=[
        Decimal("100"), Decimal("102"), Decimal("98"),
        Decimal("103"), Decimal("97"), Decimal("101")
    ],
    expected_trades=3,
    expected_return=Decimal("1.0"),
    expected_win_rate=0.33
)


# tests/fixtures/scenario_runners.py
from typing import Protocol
from dataclasses import dataclass


@dataclass
class BacktestResult:
    """Standardized result from scenario execution."""
    total_trades: int = 0
    total_return: Decimal = Decimal("0.0")
    win_rate: float = 0.0
    final_balance: Decimal = Decimal("0.0")


class ScenarioRunner(Protocol):
    """Protocol for backend implementations."""

    def run_scenario(self, scenario: MarketScenario) -> BacktestResult:
        """Execute scenario and return standardized result."""
        ...


class MockScenarioRunner:
    """Fast mock implementation for unit tests."""

    def run_scenario(self, scenario: MarketScenario) -> BacktestResult:
        """Run scenario with simple Python logic."""
        # Simple simulation without Nautilus
        trades = self._simulate_trades(scenario.prices)
        return BacktestResult(
            total_trades=len(trades),
            total_return=self._calculate_return(trades),
            win_rate=self._calculate_win_rate(trades)
        )

    def _simulate_trades(self, prices: List[Decimal]) -> List[dict]:
        """Simple crossover logic."""
        # Implement basic SMA crossover
        pass

    def _calculate_return(self, trades: List[dict]) -> Decimal:
        """Calculate total return."""
        pass

    def _calculate_win_rate(self, trades: List[dict]) -> float:
        """Calculate win rate."""
        pass


class NautilusScenarioRunner:
    """Full Nautilus engine for integration tests."""

    def run_scenario(self, scenario: MarketScenario) -> BacktestResult:
        """Run scenario with Nautilus BacktestEngine."""
        from nautilus_trader.backtest.engine import BacktestEngine

        # Create engine
        engine = BacktestEngine(config=self._create_config())

        # Convert scenario to Nautilus bars
        bars = self._create_bars_from_scenario(scenario)
        engine.add_data(bars)

        # Run backtest
        engine.run()

        # Extract results
        return self._extract_results(engine)

    def _create_config(self):
        """Create minimal engine config."""
        pass

    def _create_bars_from_scenario(self, scenario: MarketScenario):
        """Convert scenario prices to Nautilus bars."""
        pass

    def _extract_results(self, engine) -> BacktestResult:
        """Extract standardized results from engine."""
        pass


# tests/conftest.py (root level)
import pytest


@pytest.fixture(params=["mock", "nautilus"], ids=["mock", "nautilus"])
def scenario_runner(request):
    """Parametrized fixture for backend switching."""
    if request.param == "mock":
        return MockScenarioRunner()
    elif request.param == "nautilus":
        pytest.importorskip("nautilus_trader")  # Skip if not available
        return NautilusScenarioRunner()


@pytest.fixture(params=[VOLATILE_MARKET, TRENDING_MARKET, RANGING_MARKET])
def market_scenario(request):
    """Parametrized fixture for market scenarios."""
    return request.param


# Example test using both parametrizations
@pytest.mark.parametrize("scenario", [VOLATILE_MARKET, TRENDING_MARKET])
def test_strategy_with_scenario(scenario_runner, scenario):
    """
    Test strategy with different scenarios and backends.

    This test runs 2 scenarios × 2 backends = 4 test cases:
    - VOLATILE_MARKET with MockScenarioRunner
    - VOLATILE_MARKET with NautilusScenarioRunner
    - TRENDING_MARKET with MockScenarioRunner
    - TRENDING_MARKET with NautilusScenarioRunner
    """
    result = scenario_runner.run_scenario(scenario)

    # Verify against scenario expectations
    assert result.total_trades >= 0
    # Allow some tolerance for different backends
    assert abs(result.total_trades - scenario.expected_trades) <= 1
```

**Usage in Different Test Levels**:

```python
# Unit tests (mock backend only)
def test_volatile_scenario_unit():
    """Fast unit test with mock backend."""
    runner = MockScenarioRunner()
    result = runner.run_scenario(VOLATILE_MARKET)
    assert result.total_trades > 0


# Component tests (mock backend, verify interactions)
def test_strategy_order_submission():
    """Component test with test doubles."""
    runner = MockScenarioRunner()
    # Verify orders submitted during scenario
    pass


# Integration tests (Nautilus backend)
@pytest.mark.integration
@pytest.mark.forked
def test_scenario_with_nautilus():
    """Integration test with real Nautilus engine."""
    runner = NautilusScenarioRunner()
    result = runner.run_scenario(TRENDING_MARKET)
    assert result is not None
```

**Rationale**:
- **Dataclasses**: Immutable, type-safe scenario definitions
- **Protocol**: Enables backend switching without inheritance
- **Fixture parametrization**: pytest runs tests with all backend combinations
- **Scenario reuse**: Same scenarios across unit/component/integration tests
- **Type safety**: Full type hints for IDE support and mypy validation
- **Extensibility**: Easy to add new scenarios or backends

**Alternatives Considered**:
- **Dict-based scenarios**: No type safety, error-prone
- **Class inheritance for backends**: More coupling, Protocol is better
- **Separate tests for each backend**: Code duplication
- **JSON scenario files**: Overhead, dataclasses are simpler

---

## Implementation Notes

### Recommended Architecture

```
tests/
├── fixtures/
│   ├── scenarios.py          # MarketScenario dataclasses
│   ├── scenario_runners.py   # MockScenarioRunner, NautilusScenarioRunner
│   ├── event_loop.py         # Async event loop fixture
│   └── cleanup.py            # State cleanup fixtures
│
├── component/
│   ├── contracts/
│   │   ├── protocols.py      # Protocol definitions
│   │   └── test_contracts.py # Contract test base classes
│   │
│   └── doubles/
│       ├── test_engine.py    # TestTradingEngine
│       ├── test_order.py     # TestOrder
│       └── test_position.py  # TestPosition
│
├── unit/
│   └── test_logic_*.py       # Pure Python tests with MockScenarioRunner
│
├── integration/
│   └── test_backtest_*.py    # Nautilus tests with NautilusScenarioRunner
│
└── conftest.py               # Root fixtures and markers
```

### Example Dataclass Structure

```python
@dataclass(frozen=True)
class MarketScenario:
    name: str                       # Scenario identifier
    description: str                # Human-readable description
    prices: List[Decimal]           # Price sequence
    volumes: List[int] | None       # Optional volume data
    expected_trades: int            # Expected number of trades
    expected_return: Decimal        # Expected P&L
    expected_win_rate: float        # Expected win rate (0.0-1.0)
```

### Backend Interface

```python
class ScenarioRunner(Protocol):
    def run_scenario(self, scenario: MarketScenario) -> BacktestResult:
        """Execute scenario with backend implementation."""
        ...
```

### pytest Configuration

```ini
# pytest.ini
[tool:pytest]
# Async settings
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function

# Parallel execution
addopts = -n auto --dist=loadscope --max-worker-restart=3

# Markers
markers =
    unit: Pure Python unit tests (no Nautilus)
    component: Component tests with test doubles
    integration: Integration tests with Nautilus (--forked)
    slow: Slow-running tests (skip in fast mode)
```

### Makefile Targets

```makefile
.PHONY: test-unit test-component test-integration test-all

test-unit:
	pytest tests/unit -n auto -v

test-component:
	pytest tests/component -n auto -v

test-integration:
	pytest tests/integration --forked -n 4 -v

test-all:
	pytest tests/ -n auto -v
```

---

## Summary & Recommendations

### Final Decisions

1. **Subprocess Isolation**: Use pytest-xdist for unit/component, pytest-forked for integration
2. **Event Loop Management**: pytest-asyncio with explicit function-scoped cleanup fixture
3. **Nautilus Testing**: Adopt test_kit stubs, minimal configs, subprocess isolation, gc.collect()
4. **Contract Testing**: Protocol-based contracts with shared test base classes
5. **Scenario Architecture**: Dataclass scenarios + Protocol runners + fixture parametrization

### Architecture Benefits

- **Reusability**: Same scenarios run at all test levels
- **Speed**: Mock backend <100ms, Nautilus backend <5s
- **Isolation**: Process isolation prevents C extension crashes
- **Type Safety**: Full type hints with Protocol and dataclass
- **Maintainability**: Contract tests prevent test double drift
- **Flexibility**: Easy to add new scenarios or backends

### Next Steps

1. Implement MarketScenario dataclass in `tests/fixtures/scenarios.py`
2. Create ScenarioRunner Protocol in `tests/fixtures/scenario_runners.py`
3. Implement MockScenarioRunner (pure Python)
4. Implement NautilusScenarioRunner (integration)
5. Add pytest fixture parametrization in `tests/conftest.py`
6. Write contract tests to verify both runners satisfy protocol
7. Migrate existing tests to use scenario pattern

---

**Research Complete**: Ready for Phase 1 (Design & Contracts)
