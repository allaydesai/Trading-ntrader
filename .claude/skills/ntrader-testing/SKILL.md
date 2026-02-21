---
name: ntrader-testing
description: >
  Use when writing tests, choosing test tiers, using test fixtures, or following TDD workflow.
  Covers the test pyramid (unit/component/integration/e2e), available test doubles, fixture
  catalog, and market scenarios.
---

# NTrader Testing Guide

## TDD Workflow (Non-Negotiable)

Every feature starts with a failing test. Follow Red-Green-Refactor:

1. **Red**: Write a failing test that describes the expected behavior
2. **Green**: Write the minimum code to make the test pass
3. **Refactor**: Clean up while keeping tests green

```bash
# Run the specific test tier during development
make test-unit          # Fast, parallel, no Nautilus — use during Red-Green
make test-component     # Test doubles, no C extensions — use for strategy logic
make test-integration   # Real Nautilus, --forked — use for engine behavior
make test-e2e           # Full system, sequential — use for end-to-end flows
```

## Test Tier Selection Guide

| Tier | When to Use | Speed | Nautilus? | Marker |
|------|------------|-------|-----------|--------|
| **Unit** | Pure functions, logic, utilities, models | ~ms | No | `@pytest.mark.unit` |
| **Component** | Strategy logic with test doubles | ~ms | No | `@pytest.mark.component` |
| **Integration** | Real BacktestEngine, C extensions | ~s | Yes | `@pytest.mark.integration` |
| **E2E** | Full CLI/API workflows | ~s | Yes | `@pytest.mark.e2e` |

**Decision tree**:
- Testing pure logic (math, validation, parsing)? -> **Unit**
- Testing strategy signal generation without engine? -> **Component**
- Testing strategy behavior with real BacktestEngine? -> **Integration**
- Testing CLI commands or API endpoints end-to-end? -> **E2E**

## Available Test Doubles

Located in `tests/component/doubles/`:

### TestTradingEngine
Lightweight engine simulator — no C extensions, no Nautilus framework.

```python
from tests.component.doubles import TestTradingEngine
engine = TestTradingEngine(initial_balance=Decimal("10000"))
engine.submit_order(order)
position = engine.get_position("BTCUSDT")
```

With position limits:
```python
engine = TestTradingEngine(
    initial_balance=Decimal("10000"),
    max_position_size=Decimal("1.0"),
)
```

### TestOrder
Simplified order representation:

```python
from tests.component.doubles import TestOrder
order = TestOrder(
    symbol="BTCUSDT",
    side="BUY",
    quantity=Decimal("0.5"),
    order_type="MARKET",
)
```

### TestPosition
Simplified position with PnL:

```python
from tests.component.doubles import TestPosition
position = TestPosition(
    symbol="BTCUSDT",
    quantity=Decimal("0.5"),
    entry_price=Decimal("50000"),
    current_price=Decimal("51000"),
)
# position.unrealized_pnl == Decimal("500")
```

## Fixture Catalog

### Root Fixtures (`tests/conftest.py`)
Shared across all test tiers.

### Component Fixtures (`tests/component/conftest.py`)

| Fixture | Returns | Use For |
|---------|---------|---------|
| `test_engine` | `TestTradingEngine(balance=10000)` | Strategy logic tests |
| `test_engine_with_limits` | `TestTradingEngine(balance=10000, max=1.0)` | Risk limit tests |
| `sma_logic` | `SMATradingLogic(fast=5, slow=20)` | SMA crossover tests |
| `position_sizing_logic` | `PositionSizingLogic()` | Position sizing tests |
| `risk_manager` | `RiskManagementLogic(pos=2%, acct=10%)` | Risk validation tests |
| `sample_test_order` | `TestOrder(BTCUSDT, BUY, 0.5)` | Order handling tests |
| `sample_test_position` | `TestPosition(BTCUSDT, 0.5, 50000, 51000)` | Position tracking tests |

## Market Scenarios

Located in `tests/fixtures/scenarios.py` — predefined price sequences for integration tests:

```python
from tests.fixtures.scenarios import VOLATILE_MARKET, TRENDING_MARKET, RANGING_MARKET
```

| Scenario | Description | Expected Trades | Profitable? |
|----------|------------|----------------|-------------|
| `VOLATILE_MARKET` | High volatility with frequent reversals | ~8 | No (whipsaw) |
| `TRENDING_MARKET` | Steady uptrend, minimal pullbacks | ~2 | Yes |
| `RANGING_MARKET` | Sideways within range, no trend | ~4 | No (choppy) |

Each scenario is a `MarketScenario` dataclass with:
- `prices`: Tuple of Decimal prices
- `expected_trades`: Expected trade count
- `expected_pnl_positive`: Whether strategy should profit

## Integration Test Safety

Integration tests touch Nautilus C/Rust extensions and MUST use `--forked`:

```bash
# Already configured in Makefile
make test-integration  # Uses pytest --forked internally
```

**Double gc.collect() pattern** for cleanup:
```python
import gc

def teardown_method(self):
    if self.engine:
        self.engine.dispose()
    gc.collect()
    gc.collect()  # Second pass for weak references
```

## Pytest Markers

```python
import pytest

@pytest.mark.unit
def test_calculate_something():
    ...

@pytest.mark.component
def test_strategy_signal():
    ...

@pytest.mark.integration
def test_full_backtest():
    ...

@pytest.mark.e2e
def test_cli_command():
    ...
```

## Test File Organization

```
tests/
├── conftest.py              # Root fixtures (shared)
├── unit/                    # Pure logic tests
│   ├── test_sma_logic.py
│   └── test_position_sizing.py
├── component/               # Test double-based tests
│   ├── conftest.py          # Component fixtures
│   ├── doubles/             # TestTradingEngine, TestOrder, TestPosition
│   └── test_strategy_*.py
├── integration/             # Real Nautilus engine tests
│   ├── conftest.py
│   └── test_backtest_*.py
├── e2e/                     # Full system tests
├── api/                     # FastAPI endpoint tests
├── ui/                      # UI route tests
└── fixtures/
    └── scenarios.py         # Market scenarios
```

## Coverage

```bash
make test-coverage  # Covers src/core + src/strategies
```

## Deeper Reference

See `agent_docs/testing.md` for:
- Full fixture specifications
- Nautilus-specific test patterns
- Coverage configuration details
- CI test pipeline setup
