# Quickstart Validation: Parquet-Only Market Data

**Date**: 2025-01-13
**Branch**: 002-migrate-from-dual
**Purpose**: Step-by-step validation scenarios for Parquet-first implementation

## Prerequisites

```bash
# Ensure on correct branch
git checkout 002-migrate-from-dual

# Sync dependencies
uv sync

# Verify Python version
python --version  # Should be 3.11+

# Set catalog path (optional, defaults to ./data/catalog)
export NAUTILUS_PATH=/Users/allay/dev/Trading-ntrader/data/catalog
```

---

## Scenario 1: Run Backtest with Cached Data (User Story 1)

**Goal**: Verify backtest loads data from Parquet catalog without IBKR connection

### Setup
```bash
# Ensure test data exists in catalog (from previous CSV imports or IBKR fetches)
ls -la data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/

# Expected output:
# 2024-01-02.parquet
# 2024-01-03.parquet
# ...
```

### Execution
```bash
# Run backtest with cached data (no IBKR connection needed)
ntrader backtest run \
  --symbol AAPL \
  --start 2024-01-02 \
  --end 2024-01-31 \
  --strategy sma_crossover \
  --initial-capital 100000
```

### Expected Output
```
✓ Loading data from Parquet catalog...
✓ Found 21 trading days (2024-01-02 to 2024-01-31)
✓ Loaded 8190 bars from catalog (no IBKR fetch required)

Running backtest: SMA Crossover (AAPL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:05

Backtest Results:
  Total Trades:   45
  Win Rate:       55.6%
  Net P&L:        $3,245.50
  Sharpe Ratio:   1.85
  Max Drawdown:   -8.2%

✓ Data source: Parquet catalog (./data/catalog/)
✓ No IBKR connection required
```

### Validation Checks
- [ ] Backtest starts immediately (no data fetching delay)
- [ ] Output shows "Loaded from catalog"
- [ ] No IBKR connection errors
- [ ] Backtest completes with valid results
- [ ] Time to first bar < 2 seconds

### Troubleshooting
**Error**: `Data not found: AAPL from 2024-01-02 to 2024-01-31`
- **Cause**: No data in catalog for requested range
- **Solution**: Run Scenario 4 (CSV import) or Scenario 2 (auto-fetch)

---

## Scenario 2: Automatic Data Fetching on Missing Data (User Story 2)

**Goal**: Verify system auto-fetches from IBKR when data not in catalog

### Setup
```bash
# Start IBKR Gateway (if not running)
docker compose up -d ibgateway

# Wait for connection (30 seconds)
sleep 30

# Verify IBKR connection
docker compose logs ibgateway | grep "TWS API listening"
```

### Execution
```bash
# Run backtest for symbol NOT in catalog
ntrader backtest run \
  --symbol TSLA \
  --start 2024-01-02 \
  --end 2024-01-05 \
  --strategy sma_crossover \
  --initial-capital 100000
```

### Expected Output
```
⚠ Data not found in catalog for TSLA (2024-01-02 to 2024-01-05)
✓ IBKR connection available

Fetching historical data from IBKR...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 45% (2/4 days)
  Fetching: 2024-01-03
  Rate limit: 47/50 req/sec (within limits)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% (4/4 days)

✓ Fetched 1560 bars from IBKR (4 trading days)
✓ Persisted to Parquet catalog: ./data/catalog/TSLA.NASDAQ/1-MINUTE-LAST/
✓ Future backtests will use cached data

Running backtest: SMA Crossover (TSLA)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:02

Backtest Results:
  Total Trades:   12
  Win Rate:       50.0%
  Net P&L:        $1,125.75
  ...

✓ Data source: IBKR (auto-fetched and cached)
```

### Validation Checks
- [ ] System detects missing data
- [ ] IBKR connection verified before fetch
- [ ] Progress indicator shows fetch progress
- [ ] Rate limit respected (<=50 req/sec)
- [ ] Data persisted to catalog after fetch
- [ ] Backtest runs with fetched data
- [ ] Subsequent runs use cached data (re-run command to verify)

### Troubleshooting
**Error**: `IBKR connection unavailable`
- **Cause**: IBKR Gateway not running or connection failed
- **Solution**: Run `docker compose up ibgateway` and wait 30 seconds

**Error**: `IBKR rate limit exceeded (60 req/sec)`
- **Cause**: Too many concurrent requests
- **Solution**: Wait 60 seconds and retry

---

## Scenario 3: Clear Error Messaging for Unavailable Data (User Story 3)

**Goal**: Verify helpful error messages when data unavailable and no IBKR

### Setup
```bash
# Stop IBKR Gateway to simulate unavailable connection
docker compose stop ibgateway

# Ensure symbol NOT in catalog
rm -rf data/catalog/NVDA.NASDAQ/
```

### Execution
```bash
# Attempt backtest with no data and no IBKR
ntrader backtest run \
  --symbol NVDA \
  --start 2024-01-02 \
  --end 2024-01-31 \
  --strategy sma_crossover
```

### Expected Output
```
❌ Data not found: NVDA from 2024-01-02 to 2024-01-31

📊 Data not in catalog. IBKR connection unavailable.

🔧 Resolution steps:
  1. Connect IBKR Gateway:
     docker compose up ibgateway

  2. Retry backtest:
     ntrader backtest run --symbol NVDA --start 2024-01-02 --end 2024-01-31

  3. Or import CSV data:
     ntrader data import --csv ./nvda-2024.csv

📚 Documentation: docs/troubleshooting.md#data-not-found

Exit code: 1
```

### Validation Checks
- [ ] Error message clearly identifies problem (no data + no IBKR)
- [ ] Provides 3 actionable resolution steps
- [ ] Includes specific command examples (copy-paste ready)
- [ ] Links to relevant documentation
- [ ] Exit code is non-zero (1)

### Troubleshooting
None expected (this scenario tests error messages)

---

## Scenario 4: Import CSV Data Directly to Parquet (User Story 4)

**Goal**: Verify CSV → Parquet import without PostgreSQL

### Setup
```bash
# Create sample CSV file
cat > /tmp/aapl_sample.csv << EOF
symbol,timestamp,open,high,low,close,volume
AAPL,2024-02-01 09:30:00,185.50,186.25,185.30,185.80,125000
AAPL,2024-02-01 09:31:00,185.80,186.00,185.60,185.90,98000
AAPL,2024-02-01 09:32:00,185.90,186.50,185.85,186.40,110000
EOF
```

### Execution
```bash
# Import CSV directly to Parquet
ntrader data import \
  --csv /tmp/aapl_sample.csv \
  --symbol AAPL \
  --venue NASDAQ \
  --bar-type 1-MINUTE-LAST
```

### Expected Output
```
Importing CSV data to Parquet catalog...

✓ Validated CSV structure (3 rows, 7 columns)
✓ Validated data:
  - Timestamp format: OK (ISO 8601)
  - Price ranges: OK (high >= open, close, low)
  - Volume: OK (all >= 0)

✓ Converted to Nautilus Bar format (3 bars)
✓ Written to Parquet: data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/2024-02-01.parquet

Summary:
  Total rows imported: 3
  Date range: 2024-02-01 09:30:00 to 2024-02-01 09:32:00
  File size: 1.2 KB (compressed)

✓ Data available for backtests
```

### Validation Checks
- [ ] CSV file parsed successfully
- [ ] Data validation passes (OHLCV constraints)
- [ ] Parquet file created in correct location
- [ ] File follows naming convention (YYYY-MM-DD.parquet)
- [ ] Data queryable from catalog (run quick query test)

### Query Verification
```bash
# Verify imported data is readable
python << EOF
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.data import Bar
from datetime import datetime, timezone

catalog = ParquetDataCatalog("./data/catalog")
bars = catalog.query(
    Bar,
    identifiers=["AAPL.NASDAQ"],
    start=datetime(2024, 2, 1, 9, 30, tzinfo=timezone.utc).timestamp() * 1e9,
    end=datetime(2024, 2, 1, 9, 35, tzinfo=timezone.utc).timestamp() * 1e9
)
print(f"Query returned {len(bars)} bars")
assert len(bars) == 3, "Expected 3 bars"
print("✓ Import successful and data queryable")
EOF
```

### Troubleshooting
**Error**: `CSV validation failed: Invalid timestamp format`
- **Cause**: Timestamps not in ISO 8601 format
- **Expected**: `YYYY-MM-DD HH:MM:SS` (UTC assumed)
- **Solution**: Fix CSV timestamps or use `--tz` flag

**Error**: `Validation failed: high < low`
- **Cause**: OHLCV data inconsistency
- **Solution**: Review CSV data for errors, correct and re-import

---

## Scenario 5: Verify Data Availability Before Backtest (User Story 5)

**Goal**: Verify catalog inspection commands show availability

### Execution
```bash
# Check data availability for specific symbol
ntrader data check --symbol AAPL

# OR check all symbols in catalog
ntrader data list
```

### Expected Output (check command)
```
Data Availability: AAPL.NASDAQ

Bar Types:
  ├── 1-MINUTE-LAST
  │   └── Available: 2024-01-02 to 2024-01-31 (21 trading days)
  │       Files: 21 × daily partitions (~50 KB each)
  │       Total rows: ~8,190 bars
  │
  └── 5-MINUTE-LAST
      └── Available: 2024-01-02 to 2024-01-15 (10 trading days)
          Files: 10 × daily partitions (~12 KB each)
          Total rows: ~780 bars

Summary:
  ✓ Data available for backtests
  ✓ No gaps detected in 1-MINUTE-LAST
  ⚠ Partial data in 5-MINUTE-LAST (11 days missing)
```

### Expected Output (list command)
```
Catalog Contents: ./data/catalog/

Instruments:
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Instrument     ┃ Bar Type     ┃ Date Range  ┃ Row Count ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ AAPL.NASDAQ    │ 1-MINUTE-LAST│ 2024-01-02  │ ~8,190    │
│                │              │ to          │           │
│                │              │ 2024-01-31  │           │
│ MSFT.NASDAQ    │ 1-MINUTE-LAST│ 2024-01-02  │ ~6,240    │
│                │              │ to          │           │
│                │              │ 2024-01-23  │           │
│ TSLA.NASDAQ    │ 1-MINUTE-LAST│ 2024-01-02  │ ~1,560    │
│                │              │ to          │           │
│                │              │ 2024-01-05  │           │
└────────────────┴──────────────┴─────────────┴───────────┘

Total: 3 instruments, 3 bar types, ~16,000 bars

💡 Tip: Use 'ntrader data check --symbol AAPL' for detailed gaps analysis
```

### Validation Checks
- [ ] List command shows all instruments in catalog
- [ ] Check command shows date ranges without loading full data
- [ ] Gap detection identifies missing date ranges
- [ ] Performance: check command completes in <1 second

### Troubleshooting
**Error**: `Catalog not found at ./data/catalog`
- **Cause**: No catalog directory exists
- **Solution**: Run Scenario 2 or 4 to populate catalog first

---

## Performance Validation

### Load Time Benchmarks

```bash
# Measure data load time for 1 year of 1-minute bars
time python << EOF
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.model.data import Bar
from datetime import datetime, timezone

catalog = ParquetDataCatalog("./data/catalog")
start = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1e9
end = datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp() * 1e9

bars = catalog.query(Bar, identifiers=["AAPL.NASDAQ"], start=start, end=end)
print(f"Loaded {len(bars)} bars")
EOF

# Expected: <1 second for ~100,000 bars (1 year of 1-minute data)
```

### Expected Benchmarks
- **1 month of 1-minute bars** (~8,000 bars): <200ms
- **1 year of 1-minute bars** (~100,000 bars): <1s
- **3 years of 1-minute bars** (~300,000 bars): <3s
- **Memory usage**: <50MB for 1 year of data (3 instruments)

### Validation Checks
- [ ] Load time within targets
- [ ] Memory usage <500MB for typical workload
- [ ] No memory leaks (run multiple times, check RSS)
- [ ] Concurrent reads work (run 3 backtests in parallel)

---

## Clean-Up (Optional)

```bash
# Remove test catalog (WARNING: deletes all data)
rm -rf data/catalog/

# Stop IBKR Gateway
docker compose stop ibgateway

# Unset environment variable
unset NAUTILUS_PATH
```

---

## Success Criteria Checklist

All scenarios must pass for feature acceptance:

- [ ] **Scenario 1**: Backtest loads from catalog in <2s
- [ ] **Scenario 2**: Auto-fetch works, data persists, subsequent runs use cache
- [ ] **Scenario 3**: Error messages clear and actionable
- [ ] **Scenario 4**: CSV import creates valid Parquet files
- [ ] **Scenario 5**: Data check commands work without loading full data
- [ ] **Performance**: Load times within benchmarks
- [ ] **Reliability**: No data corruption, no race conditions

**Ready for**: Implementation (Phase 3), Integration testing, User acceptance
