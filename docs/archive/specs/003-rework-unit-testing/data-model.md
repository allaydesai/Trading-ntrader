# Data Model: Unit Testing Architecture

**Feature**: 003-rework-unit-testing
**Date**: 2025-01-22
**Philosophy**: Keep entities simple and focused

## Overview

This document defines the minimal set of entities needed for the testing architecture refactor. Each entity serves a clear purpose in separating unit tests from integration tests.

---

## Entity 1: PureLogicClass

**Purpose**: Extract trading algorithms from Nautilus Strategy classes for fast unit testing.

**Attributes**:
- Business logic methods (entry/exit decisions, position sizing, risk management)
- No Nautilus dependencies
- Accepts primitive types (float, Decimal, dict)

**Example**:
```python
class SMATradingLogic:
    """Pure Python trading logic - no Nautilus dependencies."""

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

**Validation Rules**:
- All methods must use type hints
- No imports from nautilus_trader (except test_kit in tests)
- All numeric calculations use Decimal for precision

**State**: Stateless where possible, minimal state when needed

---

## Entity 2: TestDouble

**Purpose**: Lightweight mock of Nautilus trading engine for component tests.

**Attributes**:
- `submitted_orders`: List of orders submitted during test
- `positions`: Dict of current positions by symbol
- `balance`: Current account balance
- `event_log`: List of events for verification

**Example**:
```python
from dataclasses import dataclass, field
from decimal import Decimal

@dataclass
class TestOrder:
    """Simple order representation."""
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

        # Auto-fill market orders
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

**Validation Rules**:
- Keep under 100 lines total
- Use dataclasses for simple data structures
- Don't try to replicate all Nautilus features - just what tests need
- Type hints required

**State Management**: Simple in-memory dictionaries and lists

---

## Entity 3: MarketScenario

**Purpose**: Reusable market test data that can run at different test levels.

**Attributes**:
- `name`: Scenario identifier (str)
- `description`: Human-readable description (str)
- `prices`: Sequence of prices (list[Decimal])
- `expected_trades`: Expected number of trades (int)
- `expected_return`: Expected profit/loss (Decimal)

**Example**:
```python
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
        if not self.prices:
            raise ValueError("Scenario must have at least one price")
        if len(self.prices) < 2:
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

RANGING_MARKET = MarketScenario(
    name="ranging",
    description="Sideways movement between 95-105",
    prices=[
        Decimal("100"), Decimal("102"), Decimal("98"),
        Decimal("103"), Decimal("97"), Decimal("101")
    ],
    expected_trades=3,
    expected_return=Decimal("1")
)
```

**Validation Rules**:
- Use frozen dataclasses (immutable)
- Prices must use Decimal for accuracy
- Validate in __post_init__

**State**: Immutable - scenarios never change

---

## Entity 4: TestCategory

**Purpose**: Organize tests by scope using pytest markers.

**Attributes**:
- `marker`: pytest marker name (unit, component, integration, e2e)
- `description`: What this category tests
- `execution`: How tests run (parallel, isolated, etc.)

**Example**:
```python
# pytest.ini markers
[pytest]
markers =
    unit: Pure Python unit tests (no Nautilus) - run with -n auto
    component: Component tests with test doubles - run with -n auto
    integration: Integration tests with Nautilus - run with -n auto --forked
    e2e: End-to-end tests - run sequentially

# Usage in tests
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
    # Test strategy behavior with test double
    pass

@pytest.mark.integration
def test_strategy_with_nautilus():
    """Integration test - real Nautilus components."""
    from nautilus_trader.backtest.engine import BacktestEngine
    # Test with real engine
    pass
```

**Validation Rules**:
- Always mark tests with category
- One marker per test (don't mix categories)
- Use descriptive test names that include category context

---

## Entity 5: CleanupFixture

**Purpose**: Simple cleanup between tests to prevent state leakage.

**Attributes**:
- `autouse`: Always runs (bool = True)
- `scope`: Per-function cleanup

**Example**:
```python
# tests/conftest.py
import gc
import pytest

@pytest.fixture(autouse=True)
def cleanup():
    """
    Auto-cleanup between tests.

    Runs after every test to:
    - Force garbage collection (clears C extension refs)
    - Prevent state leakage
    """
    yield  # Test runs here
    gc.collect()  # Force cleanup
```

**Validation Rules**:
- Keep fixture simple (just gc.collect())
- Use autouse=True
- Don't add complex state management unless needed

---

## Relationships

```
PureLogicClass
    ↓ used by
Strategy (Nautilus)
    ↓ tested with
TestDouble (component tests)
    ↓ or
Real Nautilus Engine (integration tests)
    ↓ using
MarketScenario (test data)
    ↓ organized by
TestCategory (markers)
    ↓ cleaned by
CleanupFixture
```

---

## File Locations

```
src/core/                       # Pure logic classes live here
├── sma_logic.py                # SMATradingLogic
├── position_sizing.py          # PositionSizingLogic
└── risk_management.py          # RiskManagementLogic

tests/component/doubles/        # Test doubles
├── __init__.py
├── test_engine.py              # TestTradingEngine
└── test_order.py               # TestOrder

tests/fixtures/                 # Shared test utilities
├── __init__.py
├── scenarios.py                # MarketScenario definitions
└── cleanup.py                  # CleanupFixture (if needed beyond conftest)

tests/conftest.py               # Root fixtures (cleanup)
pytest.ini                      # Markers definition
```

---

## Summary

This data model defines **5 simple entities**:

1. **PureLogicClass** - Extract business logic for unit tests
2. **TestDouble** - Mock engine for component tests
3. **MarketScenario** - Reusable test data
4. **TestCategory** - pytest markers for organization
5. **CleanupFixture** - Simple gc.collect() cleanup

**Key Principle**: Each entity does one thing well. No complex abstractions.

**Validation**: All code uses type hints, stays under size limits (100 lines for test doubles), and follows KISS principles.
