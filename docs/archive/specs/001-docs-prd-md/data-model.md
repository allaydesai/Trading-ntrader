# Data Model Design

**Feature**: Nautilus Trader Backtesting System  
**Date**: 2025-01-13  
**Phase**: 1 - Design & Contracts

## Core Entities

### 1. TradingStrategy
**Purpose**: Represents trading logic with entry/exit rules and parameters  
**Fields**:
- `id`: UUID - Unique identifier
- `name`: str - Strategy name (e.g., "SMA_Crossover_v1")
- `strategy_type`: Enum - Type of strategy (SMA_CROSSOVER, MEAN_REVERSION, MOMENTUM)
- `parameters`: JSON - Strategy-specific parameters
- `created_at`: datetime - Creation timestamp
- `updated_at`: datetime - Last modification timestamp
- `is_active`: bool - Whether strategy can be used

**Validation Rules**:
- Name must be unique per user
- Parameters must match strategy type schema
- At least one parameter required

**State Transitions**:
- Draft → Active → Archived
- Active strategies can be backtested
- Archived strategies read-only

### 2. Instrument
**Purpose**: Tradeable asset with symbol, exchange, and trading specifications  
**Fields**:
- `id`: str - Nautilus Trader instrument ID (e.g., "AAPL.NASDAQ")
- `symbol`: str - Trading symbol
- `exchange`: str - Exchange name
- `asset_class`: Enum - EQUITY, ETF, FX, FUTURE, OPTION
- `currency`: str - Base currency (USD, EUR, etc.)
- `tick_size`: Decimal - Minimum price increment
- `lot_size`: int - Minimum trade quantity
- `trading_hours`: JSON - Market hours specification
- `margin_requirement`: Decimal - Margin percentage required

**Validation Rules**:
- Symbol must be valid for exchange
- Tick size must be positive
- Currency must be supported (USD, EUR, GBP, JPY)

### 3. MarketData
**Purpose**: Historical price bars with OHLCV data  
**Fields**:
- `instrument_id`: str - Reference to Instrument
- `timestamp`: datetime - Bar timestamp (UTC)
- `open`: Decimal - Opening price
- `high`: Decimal - High price
- `low`: Decimal - Low price  
- `close`: Decimal - Closing price
- `volume`: int - Trading volume
- `timeframe`: Enum - BAR_1MIN, BAR_5MIN, BAR_1HOUR, BAR_DAILY

**Validation Rules**:
- High >= max(open, close)
- Low <= min(open, close)
- Volume >= 0
- Timestamp must be market hours

**Indexes**:
- Composite index on (instrument_id, timeframe, timestamp)
- TimescaleDB hypertable on timestamp

### 4. BacktestConfiguration
**Purpose**: Settings for a backtest run  
**Fields**:
- `id`: UUID - Unique identifier
- `strategy_id`: UUID - Reference to TradingStrategy
- `instrument_ids`: List[str] - Instruments to trade
- `start_date`: date - Backtest start (min: 2019-01-01)
- `end_date`: date - Backtest end (max: 2024-12-31)
- `initial_capital`: Decimal - Starting capital (default: 100000 USD)
- `position_size_pct`: Decimal - Risk per trade (default: 1%)
- `commission_model`: JSON - Commission/slippage settings
- `data_source`: Enum - IBKR, CSV_FILE
- `benchmark_symbol`: str - Comparison benchmark (default: "SPY")

**Validation Rules**:
- End date > start date
- Initial capital >= 1000
- Position size between 0.1% and 10%
- At least 30 days of data required

### 5. Trade
**Purpose**: Individual buy/sell transaction with entry/exit details  
**Fields**:
- `id`: UUID - Unique identifier
- `backtest_result_id`: UUID - Parent backtest reference
- `instrument_id`: str - Traded instrument
- `entry_time`: datetime - Position entry timestamp
- `entry_price`: Decimal - Entry execution price
- `exit_time`: datetime - Position exit timestamp (nullable)
- `exit_price`: Decimal - Exit execution price (nullable)
- `quantity`: int - Number of shares/units
- `side`: Enum - LONG, SHORT
- `commission`: Decimal - Total commission paid
- `slippage`: Decimal - Slippage cost
- `pnl`: Decimal - Profit/loss (nullable until closed)
- `pnl_pct`: Decimal - Percentage return (nullable)

**Validation Rules**:
- Entry price > 0
- Quantity > 0
- Commission >= 0
- PnL calculated only when exit_price set

**State Transitions**:
- Open (exit_time null) → Closed (exit_time set)
- PnL calculated on close

### 6. Portfolio
**Purpose**: Current holdings and cash balance during backtest  
**Fields**:
- `backtest_result_id`: UUID - Parent backtest reference
- `timestamp`: datetime - Portfolio snapshot time
- `cash_balance`: Decimal - Available cash
- `positions`: JSON - Current open positions
- `total_value`: Decimal - Cash + position values
- `margin_used`: Decimal - Margin requirement
- `buying_power`: Decimal - Available for new trades

**Validation Rules**:
- Cash balance >= 0 (can go negative with margin)
- Total value = cash + sum(position values)
- Margin used <= total value * leverage

**Snapshots**:
- Captured at each trade event
- Daily snapshots for reporting

### 7. BacktestResult
**Purpose**: Complete backtest run with performance metrics  
**Fields**:
- `id`: UUID - Unique identifier
- `configuration_id`: UUID - Reference to BacktestConfiguration
- `started_at`: datetime - Execution start time
- `completed_at`: datetime - Execution end time
- `status`: Enum - PENDING, RUNNING, COMPLETED, FAILED
- `error_message`: str - Error details if failed (nullable)
- `trades_count`: int - Total trades executed
- `winning_trades`: int - Profitable trades count
- `losing_trades`: int - Loss-making trades count
- `metrics`: JSON - Performance metrics (see below)

**Performance Metrics JSON Structure**:
```json
{
  "returns": {
    "total_return": 0.15,
    "cagr": 0.12,
    "annualized_return": 0.14
  },
  "risk": {
    "max_drawdown": -0.08,
    "sharpe_ratio": 1.5,
    "sortino_ratio": 2.1,
    "volatility": 0.16
  },
  "trading": {
    "win_rate": 0.55,
    "avg_win": 250.00,
    "avg_loss": -150.00,
    "profit_factor": 1.8,
    "expectancy": 50.00
  },
  "benchmark": {
    "benchmark_return": 0.10,
    "alpha": 0.05,
    "beta": 0.8
  }
}
```

**Validation Rules**:
- Completed results must have all metrics
- Win rate = winning_trades / trades_count
- Sharpe ratio realistic range: -3 to 5

**State Transitions**:
- PENDING → RUNNING → COMPLETED/FAILED
- Cannot modify after COMPLETED

### 8. PerformanceReport
**Purpose**: Generated reports with visualizations  
**Fields**:
- `id`: UUID - Unique identifier
- `backtest_result_id`: UUID - Reference to BacktestResult
- `report_type`: Enum - HTML, CSV, JSON
- `generated_at`: datetime - Generation timestamp
- `file_path`: str - Storage location
- `content_hash`: str - SHA256 of content
- `metadata`: JSON - Report configuration

**Validation Rules**:
- File must exist at file_path
- Content hash must match file
- HTML reports include embedded charts

## Relationships

```
TradingStrategy 1:N BacktestConfiguration
BacktestConfiguration 1:1 BacktestResult
BacktestResult 1:N Trade
BacktestResult 1:N Portfolio (snapshots)
BacktestResult 1:N PerformanceReport
Instrument 1:N MarketData
Instrument 1:N Trade
```

## Database Considerations

### TimescaleDB Hypertables
- `market_data`: Partitioned by month on timestamp
- `portfolio`: Partitioned by day for efficient queries

### Indexes
- `trades`: (backtest_result_id, entry_time)
- `market_data`: (instrument_id, timeframe, timestamp)
- `portfolio`: (backtest_result_id, timestamp)

### Data Retention
- Market data: 5 years rolling window
- Backtest results: 1 year
- Performance reports: 90 days

## API Response Models (Pydantic)

### StrategyResponse
```python
class StrategyResponse(BaseModel):
    id: UUID
    name: str
    strategy_type: StrategyType
    parameters: Dict[str, Any]
    created_at: datetime
    is_active: bool
```

### BacktestStatusResponse
```python
class BacktestStatusResponse(BaseModel):
    id: UUID
    status: BacktestStatus
    progress_pct: float
    estimated_completion: Optional[datetime]
    error_message: Optional[str]
```

### PerformanceMetricsResponse
```python
class PerformanceMetricsResponse(BaseModel):
    total_return: Decimal
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    trades_count: int
    benchmark_comparison: Dict[str, float]
```

## Constraints and Business Rules

1. **Position Sizing**: No single position can exceed 10% of portfolio value
2. **Margin Requirements**: Total margin used cannot exceed 3x account value
3. **Data Availability**: Backtests require minimum 30 trading days of data
4. **Rate Limiting**: IBKR queries limited to 50 requests/second
5. **Concurrent Backtests**: Single backtest at a time per user
6. **Strategy Validation**: Strategies must generate at least 1 signal in test period

## Migration Strategy

Initial migration creates all tables with:
- TimescaleDB extension enabled
- Hypertables for time-series data
- Proper indexes for query performance
- Foreign key constraints
- Check constraints for validation rules