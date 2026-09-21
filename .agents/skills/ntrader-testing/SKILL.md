---
name: ntrader-testing
description: >
  Use when writing tests, choosing test tiers, using test fixtures, or following TDD workflow.
  Covers the test pyramid (unit/component/integration/e2e), available test doubles, fixture
  catalog, and market scenarios.
---

# NTrader Testing Guide

> Full reference: `docs/agent/testing.md`

## TDD Workflow (Non-Negotiable)

Every feature starts with a failing test. Follow Red-Green-Refactor:

1. **Red**: Write a failing test that describes the expected behavior
2. **Green**: Write the minimum code to make the test pass
3. **Refactor**: Clean up while keeping tests green

## Tier Selection

- Pure logic (math, validation, parsing) -> **Unit** (`make test-unit`)
- Strategy signal generation without engine -> **Component** (`make test-component`)
- Strategy behavior with real BacktestEngine -> **Integration** (`make test-integration`)
- CLI commands or API endpoints end-to-end -> **E2E** (`make test-e2e`)

## Test Doubles

Located in `tests/component/doubles/`. See `docs/agent/testing.md` for full API.

```python
from tests.component.doubles import TestTradingEngine, TestOrder, TestPosition

engine = TestTradingEngine(initial_balance=Decimal("10000"))
order = TestOrder(symbol="BTCUSDT", side="BUY", quantity=Decimal("0.5"), order_type="MARKET")
position = TestPosition(symbol="BTCUSDT", quantity=Decimal("0.5"),
    entry_price=Decimal("50000"), current_price=Decimal("51000"))
```

## Integration Test Safety

Integration tests MUST use `--forked` (already configured in `make test-integration`).

```python
def teardown_method(self):
    if self.engine:
        self.engine.dispose()
    gc.collect()
    gc.collect()  # Second pass for weak references
```

## Pytest Markers

```python
@pytest.mark.unit          # Pure Python, parallelizable
@pytest.mark.component     # Test doubles, parallelizable
@pytest.mark.integration   # Nautilus + DB, needs --forked
@pytest.mark.e2e           # Sequential, full workflow
```

## Key Fixtures

See `docs/agent/testing.md` for fixture catalog, market scenarios, and component fixture reference.
