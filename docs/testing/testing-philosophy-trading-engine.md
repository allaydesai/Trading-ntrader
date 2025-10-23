## Testing Philosophy for Trading Engine Features

### The Test Pyramid for Trading Systems

```
         /\
        /  \  E2E Tests (5%)
       /    \ Real trading engine, real data
      /──────\
     /        \ Integration Tests (20%)
    /          \ Real engine, mock data/venues
   /────────────\
  /              \ Component Tests (25%)
 /                \ Isolated subsystems
/──────────────────\
                    Unit Tests (50%)
                    Pure logic, fully mocked
```

## Testing Strategies by Layer

### 1. **Unit Tests - Pure Business Logic (Preferred)**

**Best Practice:** Extract your trading logic from Nautilus dependencies and test it independently.

```python
# ❌ BAD: Tightly coupled to Nautilus
class MyStrategy(Strategy):
    def on_quote_tick(self, tick: QuoteTick):
        if tick.ask_price > self.last_price * 1.01:
            self.submit_order(...)  # Hard to test

# ✅ GOOD: Separated logic
class TradingLogic:
    """Pure Python trading logic, no Nautilus dependencies"""
    
    def should_enter_position(
        self, 
        current_price: float, 
        last_price: float,
        threshold: float = 0.01
    ) -> bool:
        return current_price > last_price * (1 + threshold)
    
    def calculate_position_size(
        self,
        account_balance: float,
        risk_percentage: float,
        stop_distance: float
    ) -> float:
        return (account_balance * risk_percentage) / stop_distance

class MyStrategy(Strategy):
    def __init__(self):
        self.logic = TradingLogic()
    
    def on_quote_tick(self, tick: QuoteTick):
        if self.logic.should_enter_position(
            float(tick.ask_price), 
            float(self.last_price)
        ):
            size = self.logic.calculate_position_size(...)
            self.submit_order(...)

# Now you can easily test the logic
def test_trading_logic():
    logic = TradingLogic()
    assert logic.should_enter_position(101, 100, 0.01) == False
    assert logic.should_enter_position(102, 100, 0.01) == True
```

### 2. **Component Tests - Test Doubles Pattern**

Create lightweight test doubles that mimic Nautilus behavior without the complexity:

```python
# tests/test_doubles/trading_engine.py
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from decimal import Decimal

@dataclass
class TestOrder:
    """Lightweight order representation for testing"""
    symbol: str
    side: str
    quantity: Decimal
    price: Optional[Decimal] = None
    order_type: str = "MARKET"
    status: str = "PENDING"

class TestTradingEngine:
    """
    Lightweight test double for Nautilus TradingEngine
    Captures intent without implementation complexity
    """
    
    def __init__(self):
        self.submitted_orders: List[TestOrder] = []
        self.positions: Dict[str, Decimal] = {}
        self.balance: Decimal = Decimal("10000")
        self.event_log: List[str] = []
        
    def submit_order(self, order: TestOrder) -> str:
        """Simulate order submission"""
        self.submitted_orders.append(order)
        order_id = f"TEST_{len(self.submitted_orders)}"
        self.event_log.append(f"ORDER_SUBMITTED: {order_id}")
        
        # Simulate immediate fill for market orders
        if order.order_type == "MARKET":
            self._fill_order(order)
            
        return order_id
    
    def _fill_order(self, order: TestOrder):
        """Simulate order fill"""
        if order.side == "BUY":
            self.positions[order.symbol] = \
                self.positions.get(order.symbol, Decimal(0)) + order.quantity
        else:
            self.positions[order.symbol] = \
                self.positions.get(order.symbol, Decimal(0)) - order.quantity
        
        order.status = "FILLED"
        self.event_log.append(f"ORDER_FILLED: {order.symbol}")
    
    def get_position(self, symbol: str) -> Decimal:
        return self.positions.get(symbol, Decimal(0))

# Use in tests
def test_strategy_order_submission():
    engine = TestTradingEngine()
    strategy = MyTradingStrategy(engine)
    
    # Trigger strategy logic
    strategy.process_signal("BUY", "BTCUSDT", Decimal("0.1"))
    
    # Verify behavior without Nautilus complexity
    assert len(engine.submitted_orders) == 1
    assert engine.submitted_orders[0].symbol == "BTCUSDT"
    assert engine.get_position("BTCUSDT") == Decimal("0.1")
```

### 3. **Integration Tests - Minimal Nautilus Usage**

When you must use Nautilus components, use their test utilities and minimize scope:

```python
# tests/integration/test_with_nautilus.py
import pytest
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.test_kit.stubs.data import TestDataStubs
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig

class TestStrategyIntegration:
    """Integration tests using Nautilus test utilities"""
    
    @pytest.fixture
    def minimal_engine(self):
        """Create minimal Nautilus engine for testing"""
        config = BacktestEngineConfig(
            trader_id="TESTER-001",
            risk_engine=False,  # Disable unnecessary components
            cache_database=None,  # Use in-memory cache
            data_engine=True,
            exec_engine=True,
        )
        engine = BacktestEngine(config)
        
        # Use test stubs instead of real components
        engine.add_instrument(TestInstrumentProvider.btcusdt_binance())
        
        yield engine
        
        # Cleanup
        engine.dispose()
    
    def test_strategy_with_minimal_engine(self, minimal_engine):
        """Test with minimal real components"""
        strategy = MyStrategy()
        minimal_engine.add_strategy(strategy)
        
        # Add minimal test data
        tick = TestDataStubs.quote_tick(
            instrument_id=TestIdStubs.btcusdt_binance_id(),
            bid_price=50000.0,
            ask_price=50001.0,
        )
        
        minimal_engine.add_data([tick])
        minimal_engine.run()
        
        # Verify results
        assert strategy.portfolio.is_flat()  # or whatever assertion
```

### 4. **Contract Testing Pattern**

Define contracts that your code must fulfill, regardless of implementation:

```python
# tests/contracts/trading_strategy_contract.py
from abc import ABC, abstractmethod
from typing import Protocol

class TradingStrategyContract(Protocol):
    """Contract that all strategies must fulfill"""
    
    def should_enter_position(self, market_data: dict) -> bool: ...
    def calculate_position_size(self, risk_params: dict) -> float: ...
    def get_exit_conditions(self) -> dict: ...

class StrategyContractTest(ABC):
    """Base test class for strategy contracts"""
    
    @abstractmethod
    def create_strategy(self):
        """Factory method for strategy creation"""
        pass
    
    def test_position_entry_contract(self):
        """Test that strategy fulfills entry contract"""
        strategy = self.create_strategy()
        
        # Test with bullish data
        bullish_data = {"price": 100, "volume": 1000, "trend": "up"}
        result = strategy.should_enter_position(bullish_data)
        assert isinstance(result, bool)
        
        # Test with bearish data
        bearish_data = {"price": 100, "volume": 100, "trend": "down"}
        result = strategy.should_enter_position(bearish_data)
        assert isinstance(result, bool)
    
    def test_position_sizing_contract(self):
        """Test that strategy calculates valid sizes"""
        strategy = self.create_strategy()
        
        risk_params = {"balance": 10000, "risk_pct": 0.02}
        size = strategy.calculate_position_size(risk_params)
        
        assert size >= 0
        assert size <= risk_params["balance"] * risk_params["risk_pct"]

# Concrete implementation
class TestMyStrategy(StrategyContractTest):
    def create_strategy(self):
        return MyStrategy()  # Your actual strategy
```

### 5. **Scenario Testing Pattern**

Create reusable scenarios that can run with different backends:

```python
# tests/scenarios/market_scenarios.py
from dataclasses import dataclass
from typing import List, Callable

@dataclass
class MarketScenario:
    """Reusable market scenario"""
    name: str
    initial_price: float
    price_sequence: List[float]
    expected_actions: List[str]
    
class ScenarioRunner:
    """Run scenarios against different backends"""
    
    def run_scenario(
        self,
        scenario: MarketScenario,
        strategy_factory: Callable,
        backend: str = "mock"
    ):
        if backend == "mock":
            return self._run_mock_scenario(scenario, strategy_factory)
        elif backend == "nautilus":
            return self._run_nautilus_scenario(scenario, strategy_factory)
        elif backend == "simple":
            return self._run_simple_scenario(scenario, strategy_factory)
    
    def _run_mock_scenario(self, scenario, strategy_factory):
        """Run with mock engine"""
        engine = TestTradingEngine()
        strategy = strategy_factory(engine)
        
        for price in scenario.price_sequence:
            strategy.on_price_update(price)
        
        return {
            "orders": engine.submitted_orders,
            "positions": engine.positions,
            "events": engine.event_log
        }
    
    def _run_simple_scenario(self, scenario, strategy_factory):
        """Run with simple Python logic only"""
        strategy = strategy_factory(None)
        actions = []
        
        for price in scenario.price_sequence:
            action = strategy.decide_action(price)
            actions.append(action)
        
        return {"actions": actions}

# Define reusable scenarios
VOLATILE_MARKET = MarketScenario(
    name="volatile_market",
    initial_price=100.0,
    price_sequence=[100, 105, 95, 110, 90, 100],
    expected_actions=["HOLD", "SELL", "BUY", "SELL", "BUY", "HOLD"]
)

TRENDING_MARKET = MarketScenario(
    name="trending_up",
    initial_price=100.0,
    price_sequence=[100, 102, 104, 106, 108, 110],
    expected_actions=["BUY", "HOLD", "HOLD", "HOLD", "HOLD", "HOLD"]
)

# Test using scenarios
def test_strategy_in_volatile_market():
    runner = ScenarioRunner()
    
    # Test with mock first (fast)
    result = runner.run_scenario(
        VOLATILE_MARKET,
        lambda engine: MyStrategy(engine),
        backend="mock"
    )
    assert len(result["orders"]) > 0
    
    # Optionally test with Nautilus (slower, more realistic)
    if RUN_INTEGRATION_TESTS:
        result = runner.run_scenario(
            VOLATILE_MARKET,
            lambda engine: MyStrategy(engine),
            backend="nautilus"
        )
```

### 6. **Behavioral Testing Pattern**

Focus on observable behavior rather than implementation:

```python
# tests/behavioral/test_risk_management.py
class TestRiskManagementBehavior:
    """Test risk management behavior, not implementation"""
    
    def test_never_risks_more_than_limit(self):
        """Verify risk limit regardless of implementation"""
        # Arrange
        risk_limit = 0.02  # 2% risk
        account_balance = 10000
        
        # Could use mock or real engine
        engine = create_test_engine(balance=account_balance)
        strategy = MyStrategy(engine, risk_limit=risk_limit)
        
        # Act - simulate various market conditions
        for _ in range(100):
            random_price = random.uniform(50, 150)
            strategy.on_price(random_price)
        
        # Assert - verify risk was controlled
        max_risk_taken = calculate_max_risk(engine.submitted_orders)
        assert max_risk_taken <= account_balance * risk_limit
    
    def test_stops_trading_after_daily_loss_limit(self):
        """Verify daily loss limit behavior"""
        daily_limit = -500  # $500 loss limit
        
        # Use any backend that tracks P&L
        engine = create_test_engine()
        strategy = MyStrategy(engine, daily_loss_limit=daily_limit)
        
        # Simulate losses
        simulate_losing_trades(engine, total_loss=-600)
        
        # Try to trade more
        strategy.on_signal("BUY")
        
        # Verify no new orders after limit
        assert engine.get_orders_after_loss(daily_limit) == []
```

### 7. **Adapter Pattern for Testing**

Create adapters to swap between test and production implementations:

```python
# src/adapters/engine_adapter.py
from abc import ABC, abstractmethod

class EngineAdapter(ABC):
    """Abstract interface for trading engine"""
    
    @abstractmethod
    def submit_order(self, symbol, side, quantity, price=None): 
        pass
    
    @abstractmethod
    def get_position(self, symbol):
        pass
    
    @abstractmethod
    def get_account_balance(self):
        pass

class NautilusEngineAdapter(EngineAdapter):
    """Production adapter for Nautilus"""
    
    def __init__(self, nautilus_engine):
        self.engine = nautilus_engine
    
    def submit_order(self, symbol, side, quantity, price=None):
        # Translate to Nautilus order
        order = self.engine.order_factory.market(...)
        self.engine.submit(order)

class MockEngineAdapter(EngineAdapter):
    """Test adapter with simple implementation"""
    
    def __init__(self):
        self.orders = []
        self.positions = {}
        self.balance = 10000
    
    def submit_order(self, symbol, side, quantity, price=None):
        self.orders.append({
            "symbol": symbol, 
            "side": side, 
            "quantity": quantity
        })

# Your strategy uses the adapter
class MyStrategy:
    def __init__(self, engine: EngineAdapter):
        self.engine = engine  # Works with any implementation
    
    def trade(self):
        balance = self.engine.get_account_balance()
        if balance > 1000:
            self.engine.submit_order("BTCUSDT", "BUY", 0.1)
```

### 8. **Time Travel Testing**

Test time-dependent logic without real delays:

```python
# tests/test_time_dependent.py
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

class TimeController:
    """Control time in tests"""
    
    def __init__(self, start_time=None):
        self.current_time = start_time or datetime.now()
        self.events = []
    
    def advance(self, seconds=0, minutes=0, hours=0):
        """Advance simulated time"""
        self.current_time += timedelta(
            seconds=seconds,
            minutes=minutes,
            hours=hours
        )
        self._trigger_scheduled_events()
    
    def schedule_event(self, time, callback):
        """Schedule event at specific time"""
        self.events.append((time, callback))
    
    def _trigger_scheduled_events(self):
        """Trigger events that should fire"""
        triggered = []
        for time, callback in self.events:
            if time <= self.current_time:
                callback()
                triggered.append((time, callback))
        
        for event in triggered:
            self.events.remove(event)

def test_time_based_strategy():
    """Test strategy with time-based rules"""
    time_controller = TimeController(
        start_time=datetime(2024, 1, 1, 9, 0)  # 9 AM
    )
    
    with patch('datetime.datetime') as mock_datetime:
        mock_datetime.now.return_value = time_controller.current_time
        
        strategy = MyStrategy()
        strategy.set_clock(time_controller)
        
        # Test pre-market behavior
        strategy.on_tick({"price": 100})
        assert strategy.can_trade() == False
        
        # Advance to market open
        time_controller.advance(minutes=30)  # 9:30 AM
        assert strategy.can_trade() == True
        
        # Test end-of-day position closing
        time_controller.advance(hours=6, minutes=30)  # 4 PM
        strategy.close_all_positions()
        assert strategy.get_open_positions() == []
```

## Best Practices Summary

### When to Use Real Nautilus Components

✅ **Use Nautilus when:**
- Testing final integration before deployment
- Validating backtest results
- Testing Nautilus-specific features (order types, venues)
- Creating golden master tests
- Performance benchmarking

❌ **Avoid Nautilus when:**
- Testing pure business logic
- Testing algorithmic correctness
- Running hundreds of test variations
- Testing in CI/CD pipelines (unless necessary)
- Testing error conditions and edge cases

### Making Tests Less Brittle

1. **Decouple Logic from Framework**
   - Extract algorithms into pure functions
   - Use dependency injection
   - Create abstraction layers

2. **Use Test Doubles Appropriately**
   - Mocks for external dependencies
   - Stubs for data providers
   - Fakes for complex systems

3. **Focus on Behavior, Not Implementation**
   - Test what, not how
   - Use contract tests
   - Verify outcomes, not calls

4. **Create Stable Test APIs**
   ```python
   # Instead of testing Nautilus internals
   assert strategy._engine._cache._orders[0].price == 100
   
   # Test through stable interface
   assert strategy.get_order_price(0) == 100
   ```

5. **Use Scenario-Based Testing**
   - Define reusable market scenarios
   - Test same scenarios across different backends
   - Focus on strategy decisions, not engine mechanics

6. **Implement Progressive Testing**
   ```
   Fast Tests (milliseconds) → Run always
   Component Tests (seconds) → Run on commit
   Integration Tests (minutes) → Run on PR
   E2E Tests (hours) → Run nightly
   ```

This approach maintains test intent while dramatically reducing brittleness and execution time. The key is to test your logic, not Nautilus itself - Nautilus has its own comprehensive test suite!