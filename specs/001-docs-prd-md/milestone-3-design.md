# Milestone 3 Design: Multiple Strategies & Configuration

**Feature**: Nautilus Trader Backtesting System - Multiple Strategies
**Date**: 2025-09-18
**Phase**: Implementation Design for Milestone 3

## Overview

Milestone 3 expands the backtesting system to support multiple trading strategies (Mean Reversion, Momentum) with YAML configuration files, building on the solid foundation of Milestones 1 and 2.

## Architecture Decisions

### 1. Nautilus Trader Strategy Integration

All strategies inherit from `nautilus_trader.trading.strategy.Strategy` and follow Nautilus patterns:

```python
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

class MeanReversionConfig(StrategyConfig):
    instrument_id: InstrumentId
    bar_type: str
    lookback_period: int = 20
    num_std_dev: float = 2.0
    trade_size: Decimal = Decimal("1000000")

class MeanReversionStrategy(Strategy):
    def __init__(self, config: MeanReversionConfig) -> None:
        super().__init__(config)
        # Initialize indicators, state variables

    def on_start(self) -> None:
        # Subscribe to data feeds
        pass

    def on_bar(self, bar: Bar) -> None:
        # Handle incoming bars, generate signals
        pass
```

### 2. Strategy Factory Pattern

Dynamic strategy loading using Python imports:

```python
from importlib import import_module

def create_strategy_class(strategy_path: str):
    """Load strategy class from module path."""
    module_path, class_name = strategy_path.rsplit(":", 1)
    module = import_module(module_path)
    return getattr(module, class_name)
```

### 3. Configuration Format

YAML configurations follow Nautilus `ImportableStrategyConfig` pattern:

```yaml
# mean_reversion_config.yaml
strategy_path: "src.core.strategies.mean_reversion:MeanReversionStrategy"
config_path: "src.core.strategies.mean_reversion:MeanReversionConfig"
config:
  instrument_id: "AAPL.NASDAQ"
  bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"
  lookback_period: 20
  num_std_dev: 2.0
  trade_size: 1000000
```

## Strategy Implementations

### Mean Reversion Strategy

**Logic**: Buy when price crosses below lower Bollinger Band, sell when crosses above upper band

**Indicators**:
- Simple Moving Average (nautilus_trader.indicators.SimpleMovingAverage)
- Standard Deviation (custom or calculate manually)
- Bollinger Bands (custom implementation)

**Entry/Exit Rules**:
- Long Entry: Close < (SMA - num_std_dev * StdDev)
- Short Entry: Close > (SMA + num_std_dev * StdDev)
- Exit: Close returns to SMA ± 0.5 * StdDev

### Momentum Strategy

**Logic**: Buy on oversold conditions (RSI < 30), sell on overbought (RSI > 70)

**Indicators**:
- RSI (custom implementation following Nautilus patterns)

**Entry/Exit Rules**:
- Long Entry: RSI < oversold_threshold (default 30)
- Short Entry: RSI > overbought_threshold (default 70)
- Exit: RSI returns to neutral zone (45-55)

## Data Model Extensions

### Updated Strategy Types

```python
class StrategyType(str, Enum):
    SMA_CROSSOVER = "sma_crossover"
    MEAN_REVERSION = "mean_reversion"  # New
    MOMENTUM = "momentum"              # New

class MeanReversionParameters(BaseModel):
    lookback_period: int = Field(default=20, ge=5, le=100)
    num_std_dev: float = Field(default=2.0, ge=0.5, le=4.0)
    trade_size: Decimal = Field(default=Decimal("1000000"), gt=0)

class MomentumParameters(BaseModel):
    rsi_period: int = Field(default=14, ge=5, le=50)
    oversold_threshold: float = Field(default=30.0, ge=10.0, le=40.0)
    overbought_threshold: float = Field(default=70.0, ge=60.0, le=90.0)
    trade_size: Decimal = Field(default=Decimal("1000000"), gt=0)
```

### BacktestResult Model

```python
class BacktestResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    strategy_id: UUID
    configuration: Dict[str, Any]  # Full strategy config
    start_date: date
    end_date: date
    initial_capital: Decimal
    final_capital: Decimal
    total_return: Decimal
    performance_metrics: Dict[str, Any]  # JSON field with all metrics
    trade_count: int
    created_at: datetime = Field(default_factory=datetime.now)
```

## CLI Extensions

### Strategy Management Commands

```bash
# List available strategies
ntrader strategy list

# Create config template
ntrader strategy create --type mean_reversion --output my_config.yaml

# Validate config file
ntrader strategy validate my_config.yaml

# Run backtest from config
ntrader backtest run-config my_config.yaml
```

## File Structure

```
src/
├── core/
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── sma_crossover.py          # Existing
│   │   ├── mean_reversion.py         # New
│   │   └── momentum.py               # New
│   └── strategy_factory.py           # New
├── models/
│   ├── strategy.py                   # Extended
│   └── backtest_result.py            # New
├── services/
│   └── results_service.py            # New
├── utils/
│   └── config_loader.py              # New
└── cli/
    └── commands/
        └── strategy.py               # New

configs/
└── examples/
    ├── sma_config.yaml               # New
    ├── mean_reversion_config.yaml    # New
    └── momentum_config.yaml          # New

tests/
├── test_mean_reversion.py            # New
├── test_momentum.py                  # New
├── test_strategy_factory.py          # New
├── test_config_loader.py             # New
└── test_milestone_3.py               # New
```

## Testing Strategy

### Unit Tests
- Each strategy tested with mock data
- Configuration validation tests
- Strategy factory loading tests

### Integration Tests
- End-to-end config loading and execution
- Database persistence verification
- CLI command testing

### Test Coverage Requirements
- Minimum 80% coverage on critical paths
- All strategy entry/exit logic covered
- Configuration validation edge cases

## Implementation Order

1. **Strategy Model Extensions** - Add new types and parameters
2. **Mean Reversion Strategy** - Implement with Bollinger Bands
3. **Momentum Strategy** - Implement with RSI indicator
4. **Strategy Factory** - Dynamic loading mechanism
5. **YAML Configuration** - Loader and validation
6. **CLI Commands** - Strategy management interface
7. **BacktestResult Model** - Database persistence
8. **Results Service** - Query and comparison logic
9. **Example Configurations** - Working YAML files
10. **Integration Tests** - End-to-end validation

## Success Criteria

- ✓ Three strategies (SMA, Mean Reversion, Momentum) working
- ✓ All strategies inherit from Nautilus Strategy base class
- ✓ YAML configuration loading functional
- ✓ Strategy factory dynamically loads strategies
- ✓ CLI strategy management commands operational
- ✓ Results persistence to database working
- ✓ All tests passing with 80% coverage
- ✓ Example configurations execute successfully

## Risk Mitigation

1. **Indicator Implementation**: Use Nautilus built-ins where possible, implement custom ones following Nautilus patterns
2. **Configuration Validation**: Extensive Pydantic validation to prevent runtime errors
3. **Strategy Isolation**: Each strategy in separate module to prevent cross-contamination
4. **Backward Compatibility**: Existing SMA strategy and commands remain unchanged

This design ensures robust, extensible strategy implementation following Nautilus Trader best practices.