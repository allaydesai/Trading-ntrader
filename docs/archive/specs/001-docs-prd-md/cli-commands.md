# CLI Interface Design

**Feature**: Nautilus Trader Backtesting System CLI  
**Date**: 2025-01-13  
**Phase**: 1 - CLI Implementation Focus

## Overview

The CLI provides the primary interface for running backtests, managing strategies, and generating reports. The API contracts defined earlier will be implemented in a future phase when the web frontend is developed.

## CLI Architecture

```
ntrader
├── strategy    # Strategy management commands
├── backtest    # Backtest execution commands
├── data        # Data import/export commands
├── report      # Report generation commands
└── config      # Configuration management
```

## Command Structure

### 1. Strategy Commands

```bash
# List available strategies
ntrader strategy list [--type TYPE] [--active]

# Show strategy details
ntrader strategy show STRATEGY_NAME

# Create new strategy from template
ntrader strategy create --name NAME --type TYPE [--params PARAMS_FILE]

# Edit strategy parameters
ntrader strategy edit STRATEGY_NAME --params KEY=VALUE

# Validate strategy
ntrader strategy validate STRATEGY_NAME --instrument SYMBOL

# Export strategy configuration
ntrader strategy export STRATEGY_NAME --output FILE
```

### 2. Backtest Commands

```bash
# Run a backtest
ntrader backtest run \
  --strategy STRATEGY_NAME \
  --instruments SYMBOL1,SYMBOL2 \
  --start DATE \
  --end DATE \
  [--capital AMOUNT] \
  [--position-size PCT] \
  [--data-source ibkr|csv|catalog] \
  [--output-dir DIR]

# Run with configuration file
ntrader backtest run --config backtest.yaml

# List previous backtest results
ntrader backtest list [--strategy NAME] [--status STATUS]

# Show backtest details
ntrader backtest show BACKTEST_ID

# Resume interrupted backtest
ntrader backtest resume BACKTEST_ID

# Compare multiple backtests
ntrader backtest compare ID1 ID2 [ID3...]
```

### 3. Data Commands

```bash
# Connect to IBKR
ntrader data connect --host HOST --port PORT [--paper]

# Fetch historical data from IBKR
ntrader data fetch \
  --instruments SYMBOL1,SYMBOL2 \
  --start DATE \
  --end DATE \
  --timeframe 1MIN|5MIN|1HOUR|DAILY \
  [--output-dir DIR]

# Import CSV data
ntrader data import \
  --file FILE.csv \
  --instrument SYMBOL \
  --timeframe TIMEFRAME

# Export data to CSV
ntrader data export \
  --instrument SYMBOL \
  --start DATE \
  --end DATE \
  --output FILE.csv

# List available data
ntrader data list [--instrument SYMBOL] [--timeframe TF]

# Verify data quality
ntrader data verify --instrument SYMBOL --start DATE --end DATE
```

### 4. Report Commands

```bash
# Generate performance report
ntrader report generate \
  --backtest BACKTEST_ID \
  --format html|csv|json \
  --output FILE

# Quick summary report (stdout)
ntrader report summary BACKTEST_ID

# Generate comparison report
ntrader report compare \
  --backtests ID1,ID2,ID3 \
  --format html \
  --output comparison.html

# Export trades to CSV
ntrader report trades BACKTEST_ID --output trades.csv
```

### 5. Configuration Commands

```bash
# Initialize configuration
ntrader config init

# Show current configuration
ntrader config show

# Set configuration value
ntrader config set KEY VALUE

# Validate configuration
ntrader config validate

# Export configuration
ntrader config export --output config.yaml
```

## Configuration Files

### backtest.yaml
```yaml
strategy:
  name: "SMA_Crossover_v1"
  type: "SMA_CROSSOVER"
  parameters:
    fast_period: 12
    slow_period: 26
    entry_threshold: 0.0001

instruments:
  - AAPL.NASDAQ
  - MSFT.NASDAQ
  - GOOGL.NASDAQ

period:
  start: "2020-01-01"
  end: "2020-12-31"

portfolio:
  initial_capital: 100000
  position_size_pct: 1.0
  commission_model:
    type: "fixed"
    value: 1.0

data:
  source: "ibkr"  # or "csv" or "catalog"
  catalog_path: "./data/catalog"

execution:
  slippage_model: "fixed"
  slippage_bps: 5

output:
  directory: "./results"
  save_trades: true
  save_portfolio_snapshots: true
```

### strategy_params.json
```json
{
  "sma_crossover": {
    "fast_period": 12,
    "slow_period": 26,
    "entry_threshold": 0.0001
  },
  "mean_reversion": {
    "lookback_period": 20,
    "entry_zscore": -2.0,
    "exit_zscore": 0.0
  },
  "momentum": {
    "rsi_period": 14,
    "entry_rsi_low": 30,
    "exit_rsi_high": 70
  }
}
```

## Interactive Mode

```bash
# Start interactive session
ntrader interactive

# Interactive commands
> load strategy sma_crossover
Strategy 'sma_crossover' loaded

> set instruments AAPL,MSFT
Instruments set: AAPL, MSFT

> set period 2020-01-01 2020-12-31
Period set: 2020-01-01 to 2020-12-31

> run backtest
Starting backtest...
[████████████████████████] 100% Complete

Results:
- Total Return: 15.3%
- Sharpe Ratio: 1.45
- Max Drawdown: -8.2%
- Win Rate: 55%

> show trades
Trade #1: AAPL LONG 2020-01-15 Entry: $150.00 Exit: $155.00 PnL: +$500
Trade #2: MSFT SHORT 2020-02-03 Entry: $180.00 Exit: $175.00 PnL: +$500
...

> save report html
Report saved to: results/backtest_20250113_143022.html

> exit
```

## CLI Usage Examples

### Example 1: Simple SMA Backtest
```bash
# Run SMA crossover on Apple stock
ntrader backtest run \
  --strategy sma_crossover \
  --instruments AAPL \
  --start 2020-01-01 \
  --end 2020-12-31

# Output
Starting backtest for SMA Crossover strategy...
Loading data for AAPL (2020-01-01 to 2020-12-31)...
Initializing portfolio with $100,000...
Running backtest...
[████████████████████████] 100% Complete

Performance Summary:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total Return:        +15.34%
CAGR:               +15.34%
Sharpe Ratio:        1.45
Max Drawdown:       -8.23%
Win Rate:           55.00%
Total Trades:       20
Winning Trades:     11
Losing Trades:      9
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Results saved to: ./results/backtest_20250113_143022/
```

### Example 2: Multi-Instrument Backtest
```bash
# Create config file
cat > multi_backtest.yaml << EOF
strategy:
  name: momentum_rsi
  parameters:
    rsi_period: 14
instruments:
  - SPY.NYSE
  - QQQ.NASDAQ
  - IWM.NYSE
period:
  start: "2023-01-01"
  end: "2023-12-31"
EOF

# Run backtest
ntrader backtest run --config multi_backtest.yaml --verbose

# Generate comparison report
ntrader report generate --backtest latest --format html
```

### Example 3: Data Management
```bash
# Connect to IBKR paper account
ntrader data connect --host 127.0.0.1 --port 7497 --paper

# Fetch historical data
ntrader data fetch \
  --instruments EURUSD,GBPUSD,USDJPY \
  --start 2023-01-01 \
  --end 2023-12-31 \
  --timeframe 1HOUR

# Import CSV fallback data
ntrader data import \
  --file ./market_data/SPY_2023.csv \
  --instrument SPY \
  --timeframe DAILY

# Verify data completeness
ntrader data verify --instrument SPY --start 2023-01-01 --end 2023-12-31
```

### Example 4: Strategy Development Workflow
```bash
# 1. Create new strategy from template
ntrader strategy create \
  --name my_mean_reversion \
  --type mean_reversion \
  --params lookback=20,zscore=2.0

# 2. Validate strategy logic
ntrader strategy validate my_mean_reversion --instrument SPY

# 3. Run initial backtest
ntrader backtest run \
  --strategy my_mean_reversion \
  --instruments SPY \
  --start 2022-01-01 \
  --end 2022-12-31

# 4. Optimize parameters
for zscore in 1.5 2.0 2.5 3.0; do
  ntrader strategy edit my_mean_reversion --params entry_zscore=$zscore
  ntrader backtest run --strategy my_mean_reversion --instruments SPY
done

# 5. Compare results
ntrader backtest compare $(ntrader backtest list --last 4 --format ids)

# 6. Generate final report
ntrader report generate --backtest best --format html --output final_report.html
```

## Output Formats

### Console Output (Default)
- Colored terminal output with progress bars
- Summary statistics in tabular format
- Real-time updates during backtest execution

### JSON Output (--json flag)
```json
{
  "backtest_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "strategy": "sma_crossover",
  "instruments": ["AAPL"],
  "period": {
    "start": "2020-01-01",
    "end": "2020-12-31"
  },
  "metrics": {
    "total_return": 0.1534,
    "sharpe_ratio": 1.45,
    "max_drawdown": -0.0823,
    "win_rate": 0.55
  },
  "trades_count": 20
}
```

### File Outputs
- HTML reports with embedded charts (Plotly.js)
- CSV files for trades and portfolio snapshots
- JSON for programmatic consumption
- Pickle files for full backtest state

## Error Handling

```bash
# Verbose error messages
ntrader --debug backtest run --strategy invalid_strategy
ERROR: Strategy 'invalid_strategy' not found
Available strategies:
  - sma_crossover
  - mean_reversion
  - momentum

# Validation before execution
ntrader backtest run --strategy sma --instruments INVALID
ERROR: Instrument 'INVALID' not found in data catalog
Suggestion: Did you mean 'NVDA'?

# Recovery from interruption
ntrader backtest run --strategy sma --instruments AAPL
^C
Backtest interrupted. Resume with:
  ntrader backtest resume 550e8400-e29b-41d4-a716-446655440000
```

## Performance Considerations

The CLI is optimized for:
- Large dataset processing using chunked reading
- Progress indication for long-running operations
- Parallel processing where applicable (multiple instruments)
- Efficient memory usage with streaming data
- Result caching to avoid redundant calculations

## Integration with Notebooks

```python
# Use CLI from Jupyter notebooks
import subprocess
import json

result = subprocess.run(
    ["ntrader", "backtest", "run", "--strategy", "sma", "--json"],
    capture_output=True,
    text=True
)

data = json.loads(result.stdout)
print(f"Sharpe Ratio: {data['metrics']['sharpe_ratio']}")
```