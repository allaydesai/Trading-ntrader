# Research: Protocol-Based Contract Testing for Test Doubles

**Research Task 4**: How to ensure test doubles don't diverge from real implementations using Python Protocols?

**Date**: 2025-01-22
**Status**: Complete

## Executive Summary

**Decision**: Implement Protocol-based contract testing using shared test suites that verify both test doubles and real implementations against the same interface contract.

**Approach**:
1. Define `typing.Protocol` classes for all critical interfaces (TradingEngine, Order, Position, Strategy)
2. Create shared contract test base classes that verify Protocol compliance
3. Use `autospec=True` for runtime verification in component tests
4. Apply static type checking (mypy) for compile-time validation
5. Optional: Add runtime Protocol checking with pytest-typeguard in test environment only

**Key Benefit**: Test doubles cannot diverge from real implementations because they're validated against the same Protocol interface both statically (mypy) and at runtime (contract tests + autospec).

---

## 1. Problem Statement

When creating test doubles (TestTradingEngine, TestOrder, TestPosition) to replace heavy Nautilus components, we face the **mock drift problem**:

> **Mock Drift**: Test doubles can become outdated when the real implementation changes, causing tests to pass with incorrect mocks while production code fails.

### Real-World Risk

```python
# Real Nautilus Order interface changes
class Order:
    def submit(self, venue: Venue, timestamp: int) -> OrderId:  # New timestamp param
        ...

# Test double not updated (DANGEROUS!)
class TestOrder:
    def submit(self, venue: Venue) -> OrderId:  # Missing timestamp
        ...

# Test passes but production fails
def test_strategy_submits_order():
    engine = TestTradingEngine()
    strategy.submit_order(TestOrder(...))  # ✓ Test passes
    # But real Order.submit() requires timestamp parameter!
```

---

## 2. Solution: Protocol-Based Contract Testing

### 2.1 Python Protocols (PEP 544)

**What are Protocols?**

Protocols enable **structural subtyping** (duck typing with type safety). A class satisfies a Protocol if it implements the required methods and attributes, without explicit inheritance.

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class TradingEngineProtocol(Protocol):
    """Contract that all trading engines must satisfy."""

    def submit_order(self, order: "OrderProtocol") -> str:
        """Submit order and return order ID."""
        ...

    def get_position(self, symbol: str) -> "PositionProtocol | None":
        """Get current position for symbol."""
        ...

    def get_balance(self) -> Decimal:
        """Get account balance."""
        ...
```

**Key Features**:
- **Structural typing**: Classes don't inherit from Protocol, they just match the signature
- **Static checking**: mypy validates Protocol compliance at compile time
- **Runtime checking**: `@runtime_checkable` enables `isinstance()` checks
- **Flexibility**: Both TestTradingEngine and Nautilus BacktestEngine can satisfy the same Protocol

### 2.2 Contract Test Pattern

**Shared Test Suite**: Create base test classes that verify any implementation satisfies the Protocol.

```python
# tests/component/contracts/test_engine_contract.py
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Protocol
import pytest

class EngineContractTests(ABC):
    """
    Contract tests for trading engine implementations.
    Both test doubles and real engines must pass these tests.
    """

    @abstractmethod
    def create_engine(self) -> TradingEngineProtocol:
        """Factory method - subclasses provide engine implementation."""
        pass

    def test_submit_order_returns_order_id(self):
        """Contract: submit_order must return a string order ID."""
        engine = self.create_engine()
        order = self.create_test_order("BTCUSDT", "BUY", Decimal("0.1"))

        order_id = engine.submit_order(order)

        assert isinstance(order_id, str)
        assert len(order_id) > 0

    def test_get_position_returns_position_or_none(self):
        """Contract: get_position returns Position or None."""
        engine = self.create_engine()

        position = engine.get_position("BTCUSDT")

        # Must be Position type or None
        assert position is None or isinstance(position, PositionProtocol)

    def test_get_balance_returns_positive_decimal(self):
        """Contract: get_balance returns non-negative Decimal."""
        engine = self.create_engine()

        balance = engine.get_balance()

        assert isinstance(balance, Decimal)
        assert balance >= 0

    def test_submit_order_updates_positions(self):
        """Contract: submitting BUY order increases position."""
        engine = self.create_engine()
        order = self.create_test_order("BTCUSDT", "BUY", Decimal("0.5"))

        initial_pos = engine.get_position("BTCUSDT")
        initial_qty = initial_pos.quantity if initial_pos else Decimal(0)

        engine.submit_order(order)

        final_pos = engine.get_position("BTCUSDT")
        assert final_pos is not None
        assert final_pos.quantity == initial_qty + Decimal("0.5")


# Concrete test classes
class TestEngineContractWithTestDouble(EngineContractTests):
    """Verify test double satisfies engine contract."""

    def create_engine(self) -> TradingEngineProtocol:
        return TestTradingEngine()


class TestEngineContractWithNautilus(EngineContractTests):
    """Verify Nautilus BacktestEngine satisfies engine contract."""

    @pytest.mark.integration  # Slower, uses real Nautilus
    def create_engine(self) -> TradingEngineProtocol:
        config = BacktestEngineConfig(minimal=True)
        return NautilusEngineAdapter(BacktestEngine(config))
```

**How This Works**:
1. `EngineContractTests` defines the interface contract as executable tests
2. Test double implementation runs these tests (fast, no Nautilus)
3. Real Nautilus implementation runs the same tests (slower, integration tests)
4. Both must pass - guarantees test double matches real behavior

---

## 3. Implementation Strategies

### 3.1 Static Type Checking (Primary Defense)

**Tool**: mypy (already in project)

**Configuration** (pyproject.toml):
```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
check_untyped_defs = true

# Protocols require this
strict_optional = true
warn_incomplete_stub = true
```

**Usage**:
```python
def process_order(engine: TradingEngineProtocol, order: OrderProtocol) -> str:
    """mypy validates engine satisfies TradingEngineProtocol."""
    return engine.submit_order(order)

# mypy will catch signature mismatches
test_engine = TestTradingEngine()  # Must implement TradingEngineProtocol
process_order(test_engine, order)  # ✓ Type-safe
```

**Pros**:
- Zero runtime overhead
- Catches errors before tests run
- IDE autocomplete support
- Industry standard (mypy)

**Cons**:
- Only validates signatures, not behavior
- Requires strict type hints everywhere

### 3.2 Runtime Protocol Checking (Optional, Test-Only)

**Tool**: pytest-typeguard

**Installation**:
```bash
uv add --dev pytest-typeguard
```

**Configuration** (pytest.ini):
```ini
[pytest]
addopts =
    --typeguard-packages=src.core.strategies,src.services
    --typeguard-packages=tests.component.doubles
```

**How It Works**:
```python
# Automatically validates at runtime during tests
def test_strategy_with_engine():
    engine: TradingEngineProtocol = TestTradingEngine()

    # typeguard validates engine implements all Protocol methods
    # with correct signatures at runtime
    result = engine.submit_order(order)
```

**Pros**:
- Catches runtime signature violations
- No code changes required (pytest plugin)
- Only active in test environment

**Cons**:
- Performance overhead (test slowdown)
- Only validates what's called during tests
- Another dependency

**Recommendation**: Optional - use if Protocol drift becomes a problem despite static checking.

### 3.3 Mock Autospec (Runtime Verification)

**Tool**: unittest.mock with autospec=True

**Pattern**:
```python
from unittest.mock import create_autospec
from nautilus_trader.trading.engine import BacktestEngine

def test_strategy_interacts_correctly_with_engine():
    """Use autospec to ensure mock matches real engine interface."""

    # create_autospec introspects BacktestEngine and creates spec
    mock_engine = create_autospec(BacktestEngine, instance=True)

    strategy = MyStrategy(mock_engine)
    strategy.on_start()

    # Fails if method doesn't exist or wrong signature
    mock_engine.submit_order.assert_called_once_with(...)
```

**Pros**:
- Validates mock matches real class
- Catches method renames, signature changes
- Standard library (no dependencies)

**Cons**:
- Only works with concrete classes (not Protocols directly)
- Requires real class available for introspection
- Can be brittle with C extensions

**Use Case**: Complement to Protocols - use for Nautilus components that can't be easily Protocol-ified.

---

## 4. Recommended Architecture

### 4.1 Protocol Definitions

**File**: `tests/component/protocols.py`

```python
"""Protocol interfaces for trading components."""
from decimal import Decimal
from typing import Protocol, runtime_checkable

@runtime_checkable
class OrderProtocol(Protocol):
    """Contract for order objects."""
    symbol: str
    side: str  # "BUY" | "SELL"
    quantity: Decimal
    price: Decimal | None

    def to_dict(self) -> dict:
        """Serialize order to dict."""
        ...


@runtime_checkable
class PositionProtocol(Protocol):
    """Contract for position objects."""
    symbol: str
    quantity: Decimal
    entry_price: Decimal

    def unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """Calculate unrealized profit/loss."""
        ...


@runtime_checkable
class TradingEngineProtocol(Protocol):
    """Contract for trading engine implementations."""

    def submit_order(self, order: OrderProtocol) -> str:
        """Submit order, return order ID."""
        ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancel order by ID."""
        ...

    def get_position(self, symbol: str) -> PositionProtocol | None:
        """Get position by symbol."""
        ...

    def get_balance(self) -> Decimal:
        """Get account balance."""
        ...

    def get_all_positions(self) -> list[PositionProtocol]:
        """Get all open positions."""
        ...


@runtime_checkable
class StrategyProtocol(Protocol):
    """Contract for trading strategies."""

    def on_bar(self, bar: dict) -> None:
        """Handle new price bar."""
        ...

    def should_enter_position(
        self,
        symbol: str,
        market_data: dict
    ) -> bool:
        """Decide if should enter position."""
        ...

    def calculate_position_size(
        self,
        symbol: str,
        risk_amount: Decimal
    ) -> Decimal:
        """Calculate position size based on risk."""
        ...
```

### 4.2 Contract Test Base Classes

**File**: `tests/component/contracts/test_contracts.py`

```python
"""Contract test base classes - verify Protocol compliance."""
from abc import ABC, abstractmethod
from decimal import Decimal
import pytest

class OrderContractTests(ABC):
    """Shared tests for OrderProtocol implementations."""

    @abstractmethod
    def create_order(self, symbol: str, side: str, qty: Decimal) -> OrderProtocol:
        """Factory for order implementation."""
        pass

    def test_order_has_required_attributes(self):
        order = self.create_order("BTCUSDT", "BUY", Decimal("1.0"))
        assert hasattr(order, "symbol")
        assert hasattr(order, "side")
        assert hasattr(order, "quantity")

    def test_order_to_dict_returns_dict(self):
        order = self.create_order("BTCUSDT", "BUY", Decimal("1.0"))
        result = order.to_dict()
        assert isinstance(result, dict)
        assert "symbol" in result


class PositionContractTests(ABC):
    """Shared tests for PositionProtocol implementations."""

    @abstractmethod
    def create_position(self, symbol: str, qty: Decimal, entry: Decimal) -> PositionProtocol:
        """Factory for position implementation."""
        pass

    def test_position_calculates_pnl(self):
        position = self.create_position("BTCUSDT", Decimal("1.0"), Decimal("50000"))
        pnl = position.unrealized_pnl(Decimal("51000"))
        assert pnl == Decimal("1000")


class EngineContractTests(ABC):
    """Shared tests for TradingEngineProtocol implementations."""

    @abstractmethod
    def create_engine(self) -> TradingEngineProtocol:
        """Factory for engine implementation."""
        pass

    @abstractmethod
    def create_order(self, symbol: str, side: str, qty: Decimal) -> OrderProtocol:
        """Factory for order compatible with this engine."""
        pass

    def test_submit_order_workflow(self):
        """Contract: submit order -> get position -> verify quantity."""
        engine = self.create_engine()
        order = self.create_order("BTCUSDT", "BUY", Decimal("0.5"))

        order_id = engine.submit_order(order)
        assert isinstance(order_id, str)

        position = engine.get_position("BTCUSDT")
        assert position is not None
        assert position.quantity == Decimal("0.5")

    def test_cancel_order_workflow(self):
        """Contract: submit order -> cancel -> verify canceled."""
        engine = self.create_engine()
        order = self.create_order("BTCUSDT", "BUY", Decimal("0.5"))

        order_id = engine.submit_order(order)
        success = engine.cancel_order(order_id)

        assert isinstance(success, bool)


class StrategyContractTests(ABC):
    """Shared tests for StrategyProtocol implementations."""

    @abstractmethod
    def create_strategy(self) -> StrategyProtocol:
        """Factory for strategy implementation."""
        pass

    def test_strategy_decides_entry(self):
        """Contract: should_enter_position returns bool."""
        strategy = self.create_strategy()

        result = strategy.should_enter_position(
            "BTCUSDT",
            {"price": Decimal("50000"), "volume": 1000}
        )

        assert isinstance(result, bool)

    def test_strategy_calculates_size(self):
        """Contract: calculate_position_size returns positive Decimal."""
        strategy = self.create_strategy()

        size = strategy.calculate_position_size("BTCUSDT", Decimal("100"))

        assert isinstance(size, Decimal)
        assert size >= 0
```

### 4.3 Test Double Implementations

**File**: `tests/component/doubles/test_engine.py`

```python
"""Test double implementation for trading engine."""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from tests.component.protocols import (
    TradingEngineProtocol,
    OrderProtocol,
    PositionProtocol,
)

@dataclass
class TestOrder:
    """Lightweight test double for orders."""
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal | None = None
    order_type: str = "MARKET"
    status: str = "PENDING"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "price": str(self.price) if self.price else None,
        }


@dataclass
class TestPosition:
    """Lightweight test double for positions."""
    symbol: str
    quantity: Decimal
    entry_price: Decimal

    def unrealized_pnl(self, current_price: Decimal) -> Decimal:
        return (current_price - self.entry_price) * self.quantity


class TestTradingEngine:
    """
    Lightweight test double for Nautilus BacktestEngine.
    Implements TradingEngineProtocol for type safety.
    """

    def __init__(self, initial_balance: Decimal = Decimal("10000")):
        self.balance: Decimal = initial_balance
        self.positions: dict[str, TestPosition] = {}
        self.orders: dict[str, TestOrder] = {}
        self.order_counter: int = 0
        self.event_log: list[str] = []

    def submit_order(self, order: OrderProtocol) -> str:
        """Submit order and immediately fill (simplified)."""
        self.order_counter += 1
        order_id = f"TEST_{self.order_counter:06d}"

        # Cast to TestOrder for storage (or create new)
        test_order = TestOrder(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=order.price,
        )
        self.orders[order_id] = test_order
        self.event_log.append(f"ORDER_SUBMITTED: {order_id}")

        # Immediately fill for simplicity
        self._fill_order(test_order)

        return order_id

    def _fill_order(self, order: TestOrder) -> None:
        """Simulate order fill."""
        if order.side == "BUY":
            self._update_position(order.symbol, order.quantity, order.price)
        elif order.side == "SELL":
            self._update_position(order.symbol, -order.quantity, order.price)

        order.status = "FILLED"
        self.event_log.append(f"ORDER_FILLED: {order.symbol}")

    def _update_position(
        self,
        symbol: str,
        quantity: Decimal,
        price: Decimal | None
    ) -> None:
        """Update or create position."""
        if symbol in self.positions:
            pos = self.positions[symbol]
            new_qty = pos.quantity + quantity
            if new_qty == 0:
                del self.positions[symbol]
            else:
                pos.quantity = new_qty
        else:
            if quantity != 0:
                self.positions[symbol] = TestPosition(
                    symbol=symbol,
                    quantity=quantity,
                    entry_price=price or Decimal("0"),
                )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel order if not filled."""
        if order_id in self.orders:
            order = self.orders[order_id]
            if order.status == "PENDING":
                order.status = "CANCELED"
                self.event_log.append(f"ORDER_CANCELED: {order_id}")
                return True
        return False

    def get_position(self, symbol: str) -> PositionProtocol | None:
        """Get position by symbol."""
        return self.positions.get(symbol)

    def get_balance(self) -> Decimal:
        """Get account balance."""
        return self.balance

    def get_all_positions(self) -> list[PositionProtocol]:
        """Get all positions."""
        return list(self.positions.values())


# Verify test double satisfies Protocol at static typing level
_engine: TradingEngineProtocol = TestTradingEngine()  # mypy validates
```

### 4.4 Contract Test Implementations

**File**: `tests/component/doubles/test_doubles_contract.py`

```python
"""Verify test doubles satisfy Protocols (unit tests)."""
from decimal import Decimal
import pytest

from tests.component.contracts.test_contracts import (
    OrderContractTests,
    PositionContractTests,
    EngineContractTests,
)
from tests.component.doubles.test_engine import (
    TestOrder,
    TestPosition,
    TestTradingEngine,
)


class TestOrderDoubleContract(OrderContractTests):
    """Verify TestOrder satisfies OrderProtocol."""

    def create_order(self, symbol: str, side: str, qty: Decimal):
        return TestOrder(symbol=symbol, side=side, quantity=qty)


class TestPositionDoubleContract(PositionContractTests):
    """Verify TestPosition satisfies PositionProtocol."""

    def create_position(self, symbol: str, qty: Decimal, entry: Decimal):
        return TestPosition(symbol=symbol, quantity=qty, entry_price=entry)


class TestEngineDoubleContract(EngineContractTests):
    """Verify TestTradingEngine satisfies TradingEngineProtocol."""

    def create_engine(self):
        return TestTradingEngine()

    def create_order(self, symbol: str, side: str, qty: Decimal):
        return TestOrder(symbol=symbol, side=side, quantity=qty)
```

**File**: `tests/integration/test_nautilus_contract.py`

```python
"""Verify Nautilus components satisfy Protocols (integration tests)."""
from decimal import Decimal
import pytest

from tests.component.contracts.test_contracts import EngineContractTests
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig


@pytest.mark.integration
@pytest.mark.forked  # Isolate Nautilus C extensions
class TestNautilusEngineContract(EngineContractTests):
    """Verify Nautilus BacktestEngine satisfies TradingEngineProtocol."""

    def create_engine(self):
        # Create minimal Nautilus engine
        config = BacktestEngineConfig(risk_engine=False)
        return BacktestEngine(config)

    def create_order(self, symbol: str, side: str, qty: Decimal):
        # Create Nautilus order
        # (Implementation depends on Nautilus API)
        ...
```

---

## 5. Comparison of Approaches

| Approach | When Validated | Performance | Setup Effort | Confidence Level |
|----------|---------------|-------------|--------------|------------------|
| **Static typing (mypy)** | Compile-time | Zero overhead | Low (already have mypy) | High for signatures |
| **Contract tests** | Test-time | Fast (unit tests) | Medium (write base classes) | High for behavior |
| **pytest-typeguard** | Test runtime | Slow (adds overhead) | Low (plugin config) | Medium (test coverage dependent) |
| **mock autospec** | Test runtime | Fast | Low (standard library) | Medium (only mocked calls) |

**Recommended Combination**:
1. **Primary**: Static typing (mypy) + Contract tests
2. **Optional**: pytest-typeguard for extra safety
3. **Complement**: autospec for Nautilus components

---

## 6. Benefits of This Approach

### 6.1 Prevents Mock Drift

```python
# Scenario: Real implementation changes
class BacktestEngine:
    def submit_order(self, order: Order, timestamp: int) -> str:  # Added timestamp
        ...

# Static typing catches it immediately
class TestTradingEngine:
    def submit_order(self, order: OrderProtocol) -> str:  # Missing timestamp
        ...

# mypy error:
# error: Signature of "submit_order" incompatible with supertype "TradingEngineProtocol"
```

### 6.2 Documents Interfaces

Protocols serve as **living documentation** of required interfaces:

```python
# Clear contract for what strategies need from engines
@runtime_checkable
class TradingEngineProtocol(Protocol):
    """
    Interface that all trading engines must implement.
    Strategies depend on this interface, not concrete implementations.
    """
    def submit_order(self, order: OrderProtocol) -> str: ...
    def get_position(self, symbol: str) -> PositionProtocol | None: ...
    # ... clear expectations
```

### 6.3 Enables Gradual Adoption

Can introduce Protocols incrementally:

1. Start with critical interfaces (TradingEngine, Order)
2. Add contract tests for test doubles
3. Gradually add Protocols to other components
4. Eventually cover all integration points

### 6.4 Supports Multiple Implementations

Same Protocol works with:
- Test doubles (fast unit tests)
- Nautilus components (integration tests)
- Future implementations (different backtest engines)

---

## 7. Alternatives Considered

### 7.1 Abstract Base Classes (ABC)

**Pattern**:
```python
from abc import ABC, abstractmethod

class TradingEngineABC(ABC):
    @abstractmethod
    def submit_order(self, order: Order) -> str:
        pass
```

**Pros**:
- Familiar to Python developers
- Runtime enforcement via inheritance

**Cons**:
- Requires inheritance (coupling)
- Can't apply to existing classes (like Nautilus components)
- Less flexible than structural typing

**Decision**: Rejected - Protocols preferred for flexibility.

### 7.2 Manual Integration Tests Only

**Pattern**: Just write integration tests with real Nautilus components.

**Pros**:
- Guaranteed accuracy (using real implementation)
- No Protocol overhead

**Cons**:
- Slow test suite (can't run locally quickly)
- Brittle (C extension crashes)
- No fast feedback loop

**Decision**: Rejected - Need fast unit tests with test doubles.

### 7.3 Snapshot Testing

**Pattern**: Record real Nautilus behavior, replay in tests.

**Pros**:
- Captures actual behavior
- Easy to update when real implementation changes

**Cons**:
- Doesn't prevent interface drift
- Brittle (changes break all snapshots)
- Hard to understand test failures

**Decision**: Rejected - Doesn't solve interface contract problem.

### 7.4 Property-Based Testing (Hypothesis)

**Pattern**: Generate random inputs, verify properties hold.

**Pros**:
- Finds edge cases
- Tests behavior, not implementation

**Cons**:
- Doesn't enforce interface contracts
- Slower than unit tests
- Requires property definitions

**Decision**: Complementary - Use for algorithmic logic, but doesn't replace contract tests.

---

## 8. Implementation Notes

### 8.1 Defining Protocols

**Guidelines**:
1. Use `@runtime_checkable` decorator for `isinstance()` support
2. Define minimal interface (YAGNI - don't over-specify)
3. Use type hints for all method signatures
4. Document expected behavior in docstrings
5. Place in shared location (`tests/component/protocols.py`)

**Example**:
```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class OrderProtocol(Protocol):
    """
    Minimal interface for order objects.

    Implementations must provide symbol, side, quantity attributes
    and a to_dict() serialization method.
    """
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: Decimal

    def to_dict(self) -> dict:
        """Serialize order to dictionary."""
        ...
```

### 8.2 Writing Contract Tests

**Pattern**:
```python
class ContractTestBase(ABC):
    """Base class for contract tests."""

    @abstractmethod
    def create_implementation(self):
        """Factory method for implementation."""
        pass

    def test_contract_requirement_1(self):
        """Test first requirement of contract."""
        impl = self.create_implementation()
        # Test behavior

    def test_contract_requirement_2(self):
        """Test second requirement of contract."""
        impl = self.create_implementation()
        # Test behavior


# Concrete tests
class TestDoubleContract(ContractTestBase):
    def create_implementation(self):
        return TestDouble()


class TestRealContract(ContractTestBase):
    @pytest.mark.integration
    def create_implementation(self):
        return RealImplementation()
```

### 8.3 mypy Configuration

**pyproject.toml**:
```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
check_untyped_defs = true
strict_optional = true

# For Protocols
warn_incomplete_stub = true

# Ignore Nautilus (C extensions don't have stubs)
[[tool.mypy.overrides]]
module = "nautilus_trader.*"
ignore_missing_imports = true
```

### 8.4 Runtime Checking (Optional)

**pytest.ini**:
```ini
[pytest]
addopts =
    # Only check our code, not dependencies
    --typeguard-packages=src.core,src.services,tests.component.doubles
```

**When to enable**:
- Initial development (catch Protocol violations early)
- After major refactoring (verify contracts still hold)
- Not in CI (too slow)

---

## 9. Success Criteria

### 9.1 Static Type Safety

✅ mypy passes with strict mode
✅ All Protocol implementations type-check
✅ IDE autocomplete works with Protocols

### 9.2 Contract Test Coverage

✅ All test doubles have contract tests
✅ Contract tests cover critical behaviors
✅ Real implementations pass same contract tests (integration)

### 9.3 Developer Experience

✅ Clear error messages when contract violated
✅ Fast feedback (contract tests run in milliseconds)
✅ Easy to add new test doubles (inherit contract tests)

### 9.4 Maintainability

✅ Protocols document expected interfaces
✅ Changes to real implementation caught by contract tests
✅ Test doubles can't silently diverge from reality

---

## 10. References

### Documentation
- [PEP 544 - Protocols](https://peps.python.org/pep-0544/)
- [Real Python - Python Protocols](https://realpython.com/python-protocol/)
- [typing.Protocol Documentation](https://docs.python.org/3/library/typing.html#typing.Protocol)

### Tools
- [mypy](https://mypy-lang.org/) - Static type checker
- [pytest-typeguard](https://typeguard.readthedocs.io/) - Runtime type checking
- [unittest.mock - autospec](https://docs.python.org/3/library/unittest.mock.html#autospeccing)

### Best Practices
- [Type-Safe Python Tests (2025)](https://www.sebastiansigl.com/blog/type-safe-python-tests-in-the-age-of-ai) - Modern approach to type safety in testing
- [Python Type Checking Guide](https://realpython.com/python-type-checking/) - Comprehensive type checking guide
- [pytest-mock Best Practices](https://pytest-with-eric.com/mocking/pytest-common-mocking-problems/) - Common mocking problems

### Related Patterns
- [Contract Testing](https://martinfowler.com/bliki/ContractTest.html) - Martin Fowler on contract testing
- [Test Double Patterns](https://martinfowler.com/bliki/TestDouble.html) - Mocks, stubs, fakes, dummies

---

## 11. Next Steps

1. **Define Core Protocols** (Phase 1):
   - TradingEngineProtocol
   - OrderProtocol
   - PositionProtocol
   - StrategyProtocol

2. **Create Contract Test Base Classes** (Phase 1):
   - EngineContractTests
   - OrderContractTests
   - PositionContractTests
   - StrategyContractTests

3. **Implement Test Doubles** (Phase 2):
   - TestTradingEngine (with contract tests)
   - TestOrder (with contract tests)
   - TestPosition (with contract tests)

4. **Verify Real Implementations** (Phase 3 - Integration):
   - Nautilus BacktestEngine contract tests
   - Nautilus Order contract tests
   - Nautilus Position contract tests

5. **CI Integration** (Phase 4):
   - Add mypy strict checking to CI
   - Run contract tests on both test doubles and real implementations
   - Optional: pytest-typeguard in nightly builds

---

**Status**: ✅ Research Complete - Ready for Phase 1 Design
**Confidence Level**: High - Approach validated by industry best practices and PEP 544 standard
**Risk Level**: Low - Incremental adoption, backward compatible, aligns with project constitution
