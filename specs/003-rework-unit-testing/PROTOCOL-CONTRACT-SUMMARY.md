# Protocol-Based Contract Testing - Quick Reference

**Decision**: Use Python Protocols + Shared Contract Tests to ensure test doubles match real implementations.

## TL;DR

1. Define `Protocol` interfaces for critical components (TradingEngine, Order, Position)
2. Create shared contract test base classes that verify both mocks and real implementations
3. Use mypy for static validation + contract tests for behavioral validation
4. Test doubles MUST pass the same contract tests as real implementations

## Example: Trading Engine Protocol

```python
# tests/component/protocols.py
from typing import Protocol, runtime_checkable
from decimal import Decimal

@runtime_checkable
class TradingEngineProtocol(Protocol):
    """Interface all trading engines must implement."""
    
    def submit_order(self, order: "OrderProtocol") -> str:
        """Submit order, return order ID."""
        ...
    
    def get_position(self, symbol: str) -> "PositionProtocol | None":
        """Get position by symbol."""
        ...
    
    def get_balance(self) -> Decimal:
        """Get account balance."""
        ...
```

## Example: Contract Tests

```python
# tests/component/contracts/test_engine_contract.py
from abc import ABC, abstractmethod

class EngineContractTests(ABC):
    """Shared tests for all engine implementations."""
    
    @abstractmethod
    def create_engine(self) -> TradingEngineProtocol:
        """Factory method - subclasses provide implementation."""
        pass
    
    def test_submit_order_returns_order_id(self):
        """Contract: submit_order must return string order ID."""
        engine = self.create_engine()
        order = create_test_order("BTCUSDT", "BUY", Decimal("0.1"))
        
        order_id = engine.submit_order(order)
        
        assert isinstance(order_id, str)
        assert len(order_id) > 0
    
    def test_get_position_workflow(self):
        """Contract: submit BUY order increases position."""
        engine = self.create_engine()
        order = create_test_order("BTCUSDT", "BUY", Decimal("0.5"))
        
        engine.submit_order(order)
        
        position = engine.get_position("BTCUSDT")
        assert position is not None
        assert position.quantity == Decimal("0.5")


# Verify test double
class TestDoubleEngineContract(EngineContractTests):
    def create_engine(self):
        return TestTradingEngine()  # Test double


# Verify real implementation (integration test)
@pytest.mark.integration
class TestNautilusEngineContract(EngineContractTests):
    def create_engine(self):
        return BacktestEngine(config)  # Real Nautilus
```

## How It Prevents Mock Drift

**Scenario**: Real implementation changes
```python
# Real Nautilus adds timestamp parameter
class BacktestEngine:
    def submit_order(self, order: Order, timestamp: int) -> str:
        ...

# Test double not updated
class TestTradingEngine:
    def submit_order(self, order: OrderProtocol) -> str:  # Missing timestamp!
        ...
```

**Protection Layers**:
1. **mypy** (static): Catches signature mismatch at compile time
2. **Contract tests**: Nautilus contract tests fail (can't call with missing param)
3. **Protocol**: IDE shows type error

## Validation Strategy

| Layer | Tool | When | What It Catches |
|-------|------|------|-----------------|
| Static | mypy | Pre-commit | Signature mismatches |
| Unit | Contract tests (test double) | Every commit | Behavioral differences |
| Integration | Contract tests (real impl) | PR/merge | Real implementation changes |

## File Structure

```
tests/
├── component/
│   ├── protocols.py              # Protocol definitions
│   ├── contracts/
│   │   ├── __init__.py
│   │   └── test_contracts.py     # Contract test base classes
│   └── doubles/
│       ├── __init__.py
│       ├── test_engine.py        # TestTradingEngine implementation
│       └── test_doubles_contract.py  # Verify doubles pass contracts
│
└── integration/
    └── test_nautilus_contract.py    # Verify Nautilus passes contracts
```

## Configuration

**pyproject.toml** (mypy):
```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
disallow_untyped_defs = true
strict_optional = true
```

**pytest.ini** (optional runtime checking):
```ini
[pytest]
addopts = --typeguard-packages=tests.component.doubles
```

## Benefits

✅ **Type Safety**: mypy validates Protocol compliance at compile time
✅ **Behavioral Verification**: Contract tests verify behavior matches
✅ **Fast Feedback**: Test double contract tests run in milliseconds
✅ **Documentation**: Protocols document required interfaces
✅ **Flexibility**: Works with any implementation (Nautilus, custom engines, etc.)
✅ **Gradual Adoption**: Can add Protocols incrementally

## Dependencies

**Required**:
- Python 3.11+ (Protocol support)
- mypy (already in project)

**Optional**:
- pytest-typeguard (runtime Protocol checking in tests)

```bash
# No new dependencies for basic approach
# Optional: uv add --dev pytest-typeguard
```

## Key Principles

1. **Shared Tests**: Same contract tests run against mocks AND real implementations
2. **Minimal Protocols**: Define only what's needed (YAGNI)
3. **Static + Runtime**: Combine mypy (fast) with contract tests (thorough)
4. **Document Behavior**: Contract tests serve as executable documentation

## When to Use

✅ Use Protocols for:
- Critical integration points (engine, orders, positions)
- Interfaces that need multiple implementations
- Components frequently mocked in tests

❌ Don't use Protocols for:
- Simple data classes (use dataclasses)
- Internal implementation details
- One-off utilities

## Example Test Double

```python
# tests/component/doubles/test_engine.py
class TestTradingEngine:
    """Lightweight test double implementing TradingEngineProtocol."""
    
    def __init__(self):
        self.orders: dict[str, TestOrder] = {}
        self.positions: dict[str, TestPosition] = {}
        self.balance = Decimal("10000")
        self.order_counter = 0
    
    def submit_order(self, order: OrderProtocol) -> str:
        """Submit order (simplified - immediately fills)."""
        self.order_counter += 1
        order_id = f"TEST_{self.order_counter:06d}"
        self.orders[order_id] = order
        self._fill_order(order)  # Simplified: auto-fill
        return order_id
    
    def get_position(self, symbol: str) -> PositionProtocol | None:
        """Get position by symbol."""
        return self.positions.get(symbol)
    
    def get_balance(self) -> Decimal:
        """Get account balance."""
        return self.balance


# Static type checking ensures it matches Protocol
_check: TradingEngineProtocol = TestTradingEngine()  # mypy validates
```

## Next Steps

1. Define core Protocols (TradingEngine, Order, Position, Strategy)
2. Create contract test base classes
3. Implement test doubles with contract tests (TDD)
4. Add integration tests that verify Nautilus components pass contracts
5. Configure mypy strict mode
6. (Optional) Add pytest-typeguard for extra safety

---

**Full Research**: See `research-protocol-contracts.md` for detailed analysis, alternatives, and implementation guide.
