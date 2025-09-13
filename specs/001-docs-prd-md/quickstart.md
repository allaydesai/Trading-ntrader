# Quickstart Guide: Nautilus Trader Backtesting System

**Version**: 1.0.0  
**Date**: 2025-01-13

## Prerequisites

- Python 3.11+ installed
- Interactive Brokers TWS or IB Gateway (optional, for live data)
- PostgreSQL with TimescaleDB extension
- 2GB available RAM
- 10GB available disk space for historical data

## Installation

```bash
# Clone the repository
git clone https://github.com/your-org/trading-ntrader.git
cd trading-ntrader

# Install UV package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv sync

# Set up environment variables
cp .env.example .env
# Edit .env with your configuration
```

## Configuration

### Environment Variables (.env)
```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/trading_db
REDIS_URL=redis://localhost:6379

# Interactive Brokers (optional)
IBKR_HOST=127.0.0.1
IBKR_PORT=7497  # 7497 for paper, 7496 for live
IBKR_CLIENT_ID=1

# API Settings
API_HOST=0.0.0.0
API_PORT=8000
```

### Database Setup
```bash
# Run database migrations
alembic upgrade head

# Initialize TimescaleDB hypertables
python scripts/init_timescale.py
```

## Quick Start Scenarios

### Scenario 1: Simple Moving Average Crossover Backtest

**Goal**: Test a basic SMA crossover strategy on Apple stock

```bash
# 1. Initialize configuration
ntrader config init

# 2. Connect to data source (IBKR or use CSV)
ntrader data connect --host 127.0.0.1 --port 7497 --paper
# OR import CSV data
ntrader data import --file data/AAPL_2020.csv --instrument AAPL --timeframe DAILY

# 3. Create strategy
ntrader strategy create \
  --name sma_crossover_aapl \
  --type SMA_CROSSOVER \
  --params fast_period=12,slow_period=26

# 4. Run backtest
ntrader backtest run \
  --strategy sma_crossover_aapl \
  --instruments AAPL \
  --start 2020-01-01 \
  --end 2020-12-31 \
  --capital 100000 \
  --position-size 1.0

# 5. View results
ntrader report summary latest

# 6. Generate detailed HTML report
ntrader report generate --backtest latest --format html --output report.html
```

**Expected Output**:
```
Starting backtest for SMA Crossover strategy...
Loading data for AAPL (2020-01-01 to 2020-12-31)...
Initializing portfolio with $100,000...
Running backtest...
[████████████████████████] 100% Complete

Performance Summary:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Total Return:        +15.34%
Sharpe Ratio:        1.45
Max Drawdown:       -8.23%
Win Rate:           55.00%
Total Trades:       20
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Results saved to: ./results/backtest_20250113_143022/
```

### Scenario 2: Using CSV Data Import

**Goal**: Backtest when IBKR connection is unavailable

```bash
# 1. Import CSV data
ntrader data import \
  --file ./market_data/AAPL_2020.csv \
  --instrument AAPL \
  --timeframe 1MIN

# Output
Importing data from AAPL_2020.csv...
Parsing CSV format...
Validating data quality...
Rows processed: 98,280
Date range: 2020-01-02 to 2020-12-31
Data imported successfully to catalog

# 2. Verify imported data
ntrader data verify --instrument AAPL --start 2020-01-01 --end 2020-12-31

# 3. Run backtest with imported data
ntrader backtest run \
  --strategy sma_crossover \
  --instruments AAPL \
  --start 2020-01-01 \
  --end 2020-12-31 \
  --data-source catalog
```

### Scenario 3: Multi-Timeframe Strategy Test

**Goal**: Test strategy using both 1-minute and daily bars

```bash
# 1. Create configuration file for multi-timeframe strategy
cat > multi_tf_config.yaml << EOF
strategy:
  name: momentum_multi_tf
  type: MOMENTUM
  parameters:
    rsi_period: 14
    timeframes: ["1MIN", "DAILY"]
    entry_rsi_low: 30
    exit_rsi_high: 70

instruments:
  - SPY.NYSE
  - QQQ.NASDAQ

period:
  start: "2023-01-01"
  end: "2023-12-31"

portfolio:
  initial_capital: 100000
  position_size_pct: 2.0
EOF

# 2. Run backtest with config
ntrader backtest run --config multi_tf_config.yaml --verbose

# 3. View detailed metrics
ntrader report summary latest --detailed

# Output
╔════════════════════════════════════════╗
║     Multi-Timeframe Backtest Results   ║
╠════════════════════════════════════════╣
║ Strategy: momentum_multi_tf            ║
║ Instruments: SPY, QQQ                  ║
║ Period: 2023-01-01 to 2023-12-31      ║
╠════════════════════════════════════════╣
║ Returns:                               ║
║   Total Return:     +22.4%            ║
║   CAGR:            +22.4%             ║
║   vs Benchmark:     +8.2%             ║
╠════════════════════════════════════════╣
║ Risk Metrics:                          ║
║   Sharpe Ratio:     1.82              ║
║   Sortino Ratio:    2.45              ║
║   Max Drawdown:     -12.3%            ║
║   Volatility:       14.2%             ║
╠════════════════════════════════════════╣
║ Trading Stats:                         ║
║   Total Trades:     156               ║
║   Win Rate:         58%               ║
║   Avg Win/Loss:     1.65              ║
║   Profit Factor:    1.92              ║
╚════════════════════════════════════════╝
```

### Scenario 4: Live IBKR Data Connection

**Goal**: Connect to Interactive Brokers for historical data

```bash
# 1. Start IB Gateway (Docker)
docker run -d \
  --name ib-gateway \
  -p 7497:7497 \
  -e TRADING_MODE=paper \
  -e IBC_USERNAME=$IB_USERNAME \
  -e IBC_PASSWORD=$IB_PASSWORD \
  ghcr.io/gnzsnz/ib-gateway:stable

# 2. Connect to IBKR
ntrader data connect --host 127.0.0.1 --port 7497 --paper

# Output
Connecting to IBKR Gateway at 127.0.0.1:7497...
Connection established
Account: DU1234567 (Paper Trading)
Server Version: 176
Connection ready for data requests

# 3. Fetch historical data
ntrader data fetch \
  --instruments EURUSD,USDJPY \
  --start 2023-01-01 \
  --end 2023-12-31 \
  --timeframe 1HOUR

# Output
Fetching historical data from IBKR...
EURUSD: [████████████████] 100% (8,760 bars)
USDJPY: [████████████████] 100% (8,760 bars)
Data saved to catalog

# 4. Run FX backtest
ntrader backtest run \
  --strategy mean_reversion \
  --instruments EURUSD,USDJPY \
  --start 2023-01-01 \
  --end 2023-12-31 \
  --capital 100000
```

## Validation Tests

### Test 1: Verify Strategy Execution
```python
# tests/integration/test_quickstart.py
import pytest
from src.client import BacktestClient

def test_sma_strategy_generates_trades():
    """Verify SMA strategy generates expected trades."""
    client = BacktestClient("http://localhost:8000")
    
    # Create and run strategy
    strategy = client.create_strategy(
        name="test_sma",
        strategy_type="SMA_CROSSOVER",
        parameters={"fast_period": 10, "slow_period": 20}
    )
    
    backtest = client.run_backtest(
        strategy_id=strategy.id,
        instruments=["TEST.SIM"],
        start_date="2020-01-01",
        end_date="2020-01-31"
    )
    
    result = client.wait_for_completion(backtest.id)
    
    # Validate results
    assert result.status == "COMPLETED"
    assert result.trades_count > 0
    assert result.metrics.returns.total_return is not None
```

### Test 2: Verify Performance Metrics
```python
def test_performance_metrics_calculation():
    """Verify all required metrics are calculated."""
    client = BacktestClient("http://localhost:8000")
    
    result = client.get_backtest("<completed_backtest_id>")
    
    # Check all metrics present
    assert result.metrics.risk.sharpe_ratio is not None
    assert result.metrics.risk.max_drawdown < 0  # Should be negative
    assert 0 <= result.metrics.trading.win_rate <= 1
    assert result.metrics.benchmark.alpha is not None
```

### Test 3: Verify Report Generation
```python
def test_html_report_generation():
    """Verify HTML report contains expected elements."""
    client = BacktestClient("http://localhost:8000")
    
    # Generate report
    report_id = client.generate_report(
        backtest_id="<backtest_id>",
        format="HTML"
    )
    
    # Download and verify
    report_content = client.download_report(report_id)
    
    assert "Equity Curve" in report_content
    assert "Performance Metrics" in report_content
    assert "Trade History" in report_content
    assert "<canvas" in report_content  # Charts present
```

## CLI Commands Reference

```bash
# Strategy management
ntrader strategy list                           # List all strategies
ntrader strategy create --name NAME --type TYPE # Create new strategy
ntrader strategy show STRATEGY_NAME            # Show strategy details
ntrader strategy validate NAME --instrument SYM # Validate strategy

# Backtest execution
ntrader backtest run --strategy NAME --instruments SYM1,SYM2 --start DATE --end DATE
ntrader backtest list                          # List all backtests
ntrader backtest show BACKTEST_ID              # Show backtest details
ntrader backtest compare ID1 ID2               # Compare backtests

# Data management  
ntrader data connect --host HOST --port PORT    # Connect to IBKR
ntrader data fetch --instruments SYMS --start DATE --end DATE
ntrader data import --file FILE --instrument SYM
ntrader data list                              # List available data
ntrader data verify --instrument SYM           # Verify data quality

# Report generation
ntrader report generate --backtest ID --format FORMAT --output FILE
ntrader report summary BACKTEST_ID             # Quick summary
ntrader report trades BACKTEST_ID --output FILE # Export trades

# Configuration
ntrader config init                            # Initialize config
ntrader config show                            # Show configuration
ntrader config set KEY VALUE                   # Set config value

# Interactive mode
ntrader interactive                            # Start interactive session
```

## Performance Benchmarks

Expected performance for typical operations:

| Operation | Expected Time | Data Size |
|-----------|--------------|-----------|
| Load 1 year of 1-min bars | < 2 seconds | ~400K rows |
| Run simple strategy backtest | < 5 seconds | 1 year data |
| Generate HTML report | < 1 second | Any size |
| CSV data import | < 10 seconds | 1M rows |
| IBKR connection | < 3 seconds | N/A |

## Troubleshooting

### Issue: IBKR Connection Fails
```bash
# Check IB Gateway is running
docker ps | grep ib-gateway

# Verify port is accessible
telnet localhost 7497

# Check logs
docker logs ib-gateway
```

### Issue: Slow Backtest Performance
```python
# Enable performance profiling
import cProfile
import pstats

profiler = cProfile.Profile()
profiler.enable()

# Run backtest
client.run_backtest(...)

profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative')
stats.print_stats(10)
```

### Issue: Database Connection Error
```bash
# Check PostgreSQL is running
sudo systemctl status postgresql

# Test connection
psql $DATABASE_URL -c "SELECT 1"

# Check TimescaleDB extension
psql $DATABASE_URL -c "SELECT * FROM timescaledb_information.hypertables"
```

## Next Steps

1. **Explore Sample Strategies**: Check `examples/strategies/` directory
2. **Read API Documentation**: Open http://localhost:8000/docs
3. **Review Performance Reports**: See `examples/reports/` for samples
4. **Join Community**: Visit our Discord/Slack for support

## Support

- Documentation: http://localhost:8000/docs
- Issue Tracker: https://github.com/your-org/trading-ntrader/issues
- Email: support@trading-system.local