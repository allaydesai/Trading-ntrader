# Product Requirements Document: Simple Backtesting Application

**Version**: 1.0  
**Date**: January 2025  
**Product**: Nautilus Trader Backtesting System with IBKR Integration  

---

## 1. Overview & Problem

### Problem Statement
Quantitative traders need a reliable, extensible backtesting framework to validate trading strategies on US equities, ETFs, and spot FX using institutional-grade historical data. Current solutions are either too complex, expensive, or lack proper integration with Interactive Brokers' ecosystem.

### Solution
A lightweight, Python-based backtesting application built on Nautilus Trader that ingests historical data from IBKR, supports custom strategy development, and provides actionable performance metrics—all while maintaining simplicity and incremental extensibility.

### Development Philosophy
Start with a minimal end-to-end flow (single strategy, single instrument) that can be tested immediately, then incrementally add features while maintaining continuous validation through integration testing.

---

## 2. Objectives & Success Metrics

### Primary Objectives
- **Stability First**: Deliver a reliable backtesting engine that produces consistent, reproducible results
- **Simple Start**: Enable backtesting of one strategy on one instrument within first milestone
- **Incremental Growth**: Architecture supports adding features without breaking existing functionality
- **IBKR Ready**: Leverage existing Nautilus-IBKR integration for seamless data access

### Success Metrics
- **MVP**: Successfully backtest a single SMA crossover strategy on AAPL with results visualization
- **Accuracy**: Metrics calculations match manual verification exactly (zero tolerance)
- **Reliability**: 100% of integration tests pass for the complete user journey
- **Extensibility**: Adding a new strategy requires <100 lines of code

---

## 3. Users & Primary Use Cases

### Target Users
- **Primary**: Quantitative developers/traders with Python experience
- **Secondary**: Portfolio managers evaluating systematic strategies

### Use Cases
1. **Strategy Validation**: Test trading logic on historical data before risking capital
2. **Performance Analysis**: Evaluate strategy metrics (Sharpe, drawdown, win rate)
3. **Multi-Timeframe Analysis**: Access 1m, 5m, 1h, 1D bars simultaneously
4. **Comparison Testing**: Run same strategy across different instruments/periods

---

## 4. Scope

### In Scope (Phase 1)
- Single strategy backtesting on individual instruments
- US stocks, ETFs, and major FX pairs (EURUSD, USDJPY, etc.)
- IBKR historical data ingestion via TWS/Gateway
- CSV fallback for data import
- Multiple timeframe support (1m, 5m, 1h, 1D)
- Basic performance metrics and visualizations
- Market and limit orders
- Short selling (simplified, no borrowing costs)
- CLI interface with progress indicators
- HTML/CSV reporting

### Out of Scope (Phase 1)
- Live trading functionality
- Portfolio/multi-strategy testing
- Options, futures, crypto
- Margin/leverage
- Partial fills
- Tax modeling
- Data caching/updates
- Cloud deployment
- Advanced metrics (VaR, Monte Carlo, attribution)

---

## 5. Functional Requirements

### FR1: Data Ingestion
- **FR1.1**: Connect to IBKR TWS/Gateway and authenticate
- **FR1.2**: Request historical bars for specified symbols and timeframes
- **FR1.3**: Handle IBKR rate limits with automatic throttling
- **FR1.4**: Import CSV data as fallback mechanism
- **FR1.5**: Store instrument metadata (tick size, trading hours)
- **FR1.6**: Use IBKR's adjusted prices for corporate actions

### FR2: Strategy Framework
- **FR2.1**: Load strategy from Python module with standardized interface
- **FR2.2**: Support YAML configuration for strategy parameters
- **FR2.3**: Provide access to multiple timeframes within single strategy
- **FR2.4**: Include 3 sample strategies (SMA crossover, mean reversion, momentum)

### FR3: Backtesting Engine
- **FR3.1**: Initialize portfolio with $100,000 USD starting capital
- **FR3.2**: Execute backtests from 2019-01-01 to 2024-12-31 (default)
- **FR3.3**: Process market and limit orders
- **FR3.4**: Apply IBKR commission model + 1bps slippage
- **FR3.5**: Support position sizing at 1% equity risk per trade
- **FR3.6**: Handle FX conversions for non-USD instruments

### FR4: Reporting
- **FR4.1**: Generate equity curve visualization (PNG)
- **FR4.2**: Export trade blotter to CSV
- **FR4.3**: Calculate key metrics: CAGR, Max DD, Sharpe, Sortino, Win Rate
- **FR4.4**: Create summary HTML report
- **FR4.5**: Export metrics to JSON for programmatic access
- **FR4.6**: Compare performance against SPY benchmark

---

## 6. Non-Functional Requirements

### NFR1: Performance
- **NFR1.1**: Complete 5-year backtest on 1-minute data within reasonable time (no hard limit for Phase 1)
- **NFR1.2**: Support datasets up to available system memory

### NFR2: Reliability
- **NFR2.1**: Automatic reconnection on IBKR connection drops
- **NFR2.2**: Comprehensive logging (DEBUG, INFO, WARNING, ERROR levels)
- **NFR2.3**: Separate strategy logs from system logs
- **NFR2.4**: Graceful error handling with informative messages

### NFR3: Maintainability
- **NFR3.1**: Python 3.11+ with full type hints
- **NFR3.2**: Adherence to project coding standards (provided separately)
- **NFR3.3**: Comprehensive unit test coverage (>80%)
- **NFR3.4**: Integration tests for complete user journey
- **NFR3.5**: Full API documentation with examples

### NFR4: Usability
- **NFR4.1**: CLI with intuitive command structure
- **NFR4.2**: Real-time progress bars during backtest execution
- **NFR4.3**: Clear error messages with suggested fixes

---

## 7. Data & Instruments

### Symbol Universe (Default)
- **Equities**: AAPL, MSFT, SPY
- **FX Pairs**: EURUSD, USDJPY

### Data Specifications
- **Timeframes**: 1m, 5m, 1h, 1D bars
- **History Range**: 2019-01-01 to 2024-12-31
- **Adjustments**: Use IBKR's split/dividend adjusted prices
- **FX Rates**: Store USD conversion rates for major pairs
- **Timezone**: America/New_York for all timestamps

### Symbol Mapping
```yaml
equities:
  AAPL: {exchange: NASDAQ, currency: USD, type: STK}
  MSFT: {exchange: NASDAQ, currency: USD, type: STK}
  SPY: {exchange: ARCA, currency: USD, type: ETF}
fx:
  EURUSD: {exchange: IDEALPRO, type: CASH}
  USDJPY: {exchange: IDEALPRO, type: CASH}
```

---

## 8. Backtest Configuration

### Session Management
- **Equities**: Exchange trading hours (9:30 AM - 4:00 PM ET)
- **FX**: 24x5 (Sunday 5 PM - Friday 5 PM ET)

### Cost Model
```yaml
commissions:
  equities:
    fixed_per_share: 0.005  # $0.005 per share
    minimum: 1.00           # $1 minimum
    maximum: 0.005          # 0.5% of trade value max
  fx:
    fixed_bp: 0.20          # 0.2 basis points
slippage:
  all_instruments: 1       # 1 basis point per side
```

### Order Types
- **Market Orders**: Immediate execution at next bar open
- **Limit Orders**: Execute if price touches limit (no queue modeling)
- **Time in Force**: DAY (use Nautilus defaults)

---

## 9. Strategy Plugin Interface

### Base Strategy Specification
```python
from nautilus_trader.trading.strategy import Strategy
from typing import Dict, Any

class UserStrategy(Strategy):
    """Base class for user strategies"""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize with YAML config parameters"""
        super().__init__()
        self.config = config
        
    def on_start(self):
        """Called when backtest starts"""
        # Subscribe to required data
        self.subscribe_bars(
            bar_type=self.config['bar_type'],
            handler=self.on_bar
        )
        
    def on_bar(self, bar):
        """Process each bar"""
        # Strategy logic here
        if self.should_buy(bar):
            self.buy(
                instrument_id=bar.instrument_id,
                quantity=self.calculate_position_size()
            )
    
    def should_buy(self, bar) -> bool:
        """Override with strategy logic"""
        raise NotImplementedError
```

### Example: SMA Crossover
```python
class SMACrossover(UserStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.fast_period = config['fast_period']  # e.g., 20
        self.slow_period = config['slow_period']  # e.g., 50
        
    def should_buy(self, bar) -> bool:
        fast_sma = self.indicators.sma(self.fast_period)
        slow_sma = self.indicators.sma(self.slow_period)
        return fast_sma > slow_sma and self.position == 0
```

---

## 10. Results & Reporting

### Key Performance Indicators
| Metric | Formula | Priority |
|--------|---------|----------|
| CAGR | (Ending Value / Beginning Value)^(1/Years) - 1 | High |
| Max Drawdown | Maximum peak-to-trough decline | High |
| Sharpe Ratio | (Returns - Risk Free Rate) / Std Dev | High |
| Sortino Ratio | (Returns - Target) / Downside Dev | Medium |
| Win Rate | Winning Trades / Total Trades | High |
| Profit Factor | Gross Profits / Gross Losses | Medium |
| Average R | Average Return per Unit Risk | Medium |

### Output Artifacts
1. **equity_curve.png**: Line chart of portfolio value over time
2. **trades.csv**: Timestamp, Symbol, Side, Quantity, Price, PnL
3. **metrics.json**: All calculated metrics in structured format
4. **report.html**: Summary dashboard with charts and tables

### Visualization Priority
- **Must Have**: Equity curve, drawdown chart
- **Nice to Have**: Monthly returns heatmap, trade distribution

---

## 11. Assumptions & Risks

### Assumptions
- IBKR TWS/Gateway is installed and accessible locally
- User has valid IBKR account with market data subscriptions
- Historical data requests won't exceed IBKR's daily limits
- Linux environment with Python 3.11+ available
- Nautilus Trader stable version is compatible with current IBKR API

### Risks
| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| IBKR API changes | Low | High | Pin API versions, monitor changelog |
| Data quality issues | Medium | Medium | Add data validation in Phase 2 |
| Memory overflow on large datasets | Low | High | Document memory requirements |
| Nautilus breaking changes | Low | High | Pin to stable release, extensive testing |

---

## 12. MVP Milestones & vNext

### Phase 1 - MVP (Week 1-4)
1. **Week 1**: Basic IBKR connection and data retrieval for single symbol
2. **Week 2**: Nautilus integration with simple buy/hold strategy
3. **Week 3**: SMA crossover strategy + basic metrics calculation
4. **Week 4**: HTML reporting + equity curve visualization

### Phase 2 - Core Features (Week 5-8)
1. Multiple timeframe support
2. Additional sample strategies (mean reversion, momentum)
3. CSV import functionality
4. Enhanced logging and error handling

### Phase 3 - Advanced Features (Week 9-12)
1. Portfolio/multi-strategy support
2. Parameter optimization
3. Walk-forward analysis
4. Data caching layer

### vNext (Future)
- Options and futures support
- Live trading capability
- Cloud deployment
- Advanced risk metrics (VaR, Monte Carlo)
- Machine learning integration

---

## 13. Open Questions

### Resolved
- ✅ Configuration format: YAML
- ✅ Base currency: USD only
- ✅ Corporate actions: Use IBKR adjusted data
- ✅ Initial strategies: Include SMA, mean reversion, momentum

### Pending Resolution
1. **Signal vs Order Management**: Determine if strategies generate pure signals or manage orders directly (research Nautilus best practices)
2. **IBKR Rate Limits**: Document exact limits and implement appropriate throttling
3. **Commission Structure**: Verify exact IBKR commission tiers for implementation
4. **Performance Baseline**: Identify reference implementation for validation
5. **Coding Standards Document**: Obtain project-specific linting/formatting rules

### Decision Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2025-01 | No partial fills in Phase 1 | Simplify execution logic |
| 2025-01 | YAML for configuration | Human-readable, widely supported |
| 2025-01 | Start with single instrument | Validate end-to-end flow early |
| 2025-01 | No margin in Phase 1 | Reduce complexity |