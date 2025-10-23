# CLI Command Contracts: Parquet-Only Market Data

**Date**: 2025-01-13
**Branch**: 002-migrate-from-dual
**Purpose**: Specification for CLI command interfaces and behavior

## Overview

This document specifies CLI command contracts for Parquet-first data operations. Since this is a CLI application (not REST API), contracts define command syntax, options, outputs, and exit codes.

---

## Command: `ntrader backtest run`

**Purpose**: Run backtest with automatic catalog-first data loading

### Syntax
```bash
ntrader backtest run \
  --symbol SYMBOL \
  --start START_DATE \
  --end END_DATE \
  --strategy STRATEGY_NAME \
  [--initial-capital AMOUNT] \
  [--data-source {auto|catalog|ibkr|csv}]
```

### Arguments

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `--symbol` | string | Yes | - | Trading symbol (e.g., AAPL, MSFT) |
| `--start` | date | Yes | - | Start date (YYYY-MM-DD) |
| `--end` | date | Yes | - | End date (YYYY-MM-DD) |
| `--strategy` | string | Yes | - | Strategy name (sma_crossover, rsi_mean_reversion) |
| `--initial-capital` | float | No | 100000.0 | Starting capital in USD |
| `--data-source` | enum | No | auto | Data source: auto (catalog→IBKR), catalog (only), ibkr (force fetch), csv (deprecated) |

### Behavior: Data Source Resolution (auto mode)

```
1. Check Parquet catalog for requested symbol+date range
   ├─ Data available? → Load from catalog → Run backtest
   └─ Data missing?
      ├─ IBKR connected? → Fetch → Persist to catalog → Run backtest
      └─ IBKR unavailable? → Error with resolution steps
```

### Success Output (Data from Catalog)
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
✓ Execution time: 5.2 seconds
```

**Exit Code**: 0

### Success Output (Data Auto-Fetched from IBKR)
```
⚠ Data not found in catalog for TSLA (2024-01-02 to 2024-01-05)
✓ IBKR connection available

Fetching historical data from IBKR...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% (4/4 days)

✓ Fetched 1560 bars from IBKR
✓ Persisted to: ./data/catalog/TSLA.NASDAQ/1-MINUTE-LAST/
✓ Future backtests will use cached data

Running backtest: SMA Crossover (TSLA)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:02

Backtest Results:
  Total Trades:   12
  Win Rate:       50.0%
  Net P&L:        $1,125.75
  ...

✓ Data source: IBKR (auto-fetched and cached)
✓ Execution time: 32.1 seconds (includes 28.5s fetch)
```

**Exit Code**: 0

### Error Output (Data Unavailable)
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
```

**Exit Code**: 1

### Error Output (Invalid Date Range)
```
❌ Invalid date range: end (2024-01-01) before start (2024-12-31)

Usage:
  ntrader backtest run --symbol AAPL --start 2024-01-01 --end 2024-12-31
```

**Exit Code**: 2

---

## Command: `ntrader data check`

**Purpose**: Check data availability without loading full dataset

### Syntax
```bash
ntrader data check --symbol SYMBOL [--bar-type BAR_TYPE]
```

### Arguments

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `--symbol` | string | Yes | - | Trading symbol (e.g., AAPL) |
| `--bar-type` | string | No | all | Bar type specification (e.g., 1-MINUTE-LAST) or "all" |

### Success Output
```
Data Availability: AAPL.NASDAQ

Bar Types:
  ├── 1-MINUTE-LAST
  │   └── Available: 2024-01-02 to 2024-01-31 (21 trading days)
  │       Files: 21 × daily partitions (~50 KB each)
  │       Total rows: ~8,190 bars
  │       Gaps: None
  │
  └── 5-MINUTE-LAST
      └── Available: 2024-01-02 to 2024-01-15 (10 trading days)
          Files: 10 × daily partitions (~12 KB each)
          Total rows: ~780 bars
          Gaps: 2024-01-16 to 2024-01-31 (MISSING)

Summary:
  ✓ Data available for backtests
  ⚠ Incomplete: 5-MINUTE-LAST has gaps

💡 Tip: Fetch missing data with:
  ntrader backtest run --symbol AAPL --start 2024-01-16 --end 2024-01-31 --data-source ibkr
```

**Exit Code**: 0 (even if gaps exist - informational only)

### Error Output (Symbol Not Found)
```
❌ No data found for symbol: NVDA

Available symbols in catalog:
  - AAPL.NASDAQ (1-MINUTE-LAST, 5-MINUTE-LAST)
  - MSFT.NASDAQ (1-MINUTE-LAST)
  - TSLA.NASDAQ (1-MINUTE-LAST)

💡 Fetch data with:
  ntrader backtest run --symbol NVDA --start 2024-01-01 --end 2024-12-31
```

**Exit Code**: 0 (informational, not an error condition)

---

## Command: `ntrader data list`

**Purpose**: List all instruments and bar types in catalog

### Syntax
```bash
ntrader data list [--format {table|json|csv}]
```

### Arguments

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `--format` | enum | No | table | Output format: table, json, csv |

### Success Output (table format)
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
Disk usage: ~2.1 MB (compressed)

💡 Tip: Use 'ntrader data check --symbol AAPL' for detailed analysis
```

**Exit Code**: 0

### Success Output (json format)
```json
{
  "catalog_path": "./data/catalog/",
  "instruments": [
    {
      "instrument_id": "AAPL.NASDAQ",
      "bar_type": "1-MINUTE-LAST",
      "start_date": "2024-01-02T14:30:00Z",
      "end_date": "2024-01-31T21:00:00Z",
      "file_count": 21,
      "total_rows": 8190,
      "disk_size_bytes": 1048576
    },
    {
      "instrument_id": "MSFT.NASDAQ",
      "bar_type": "1-MINUTE-LAST",
      "start_date": "2024-01-02T14:30:00Z",
      "end_date": "2024-01-23T21:00:00Z",
      "file_count": 16,
      "total_rows": 6240,
      "disk_size_bytes": 786432
    }
  ],
  "summary": {
    "total_instruments": 3,
    "total_bar_types": 3,
    "total_rows": 16000,
    "total_disk_bytes": 2097152
  }
}
```

**Exit Code**: 0

---

## Command: `ntrader data import`

**Purpose**: Import CSV data directly to Parquet catalog

### Syntax
```bash
ntrader data import \
  --csv CSV_FILE \
  --symbol SYMBOL \
  --venue VENUE \
  [--bar-type BAR_TYPE] \
  [--tz TIMEZONE] \
  [--on-conflict {skip|overwrite|merge}]
```

### Arguments

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `--csv` | path | Yes | - | Path to CSV file |
| `--symbol` | string | Yes | - | Trading symbol (e.g., AAPL) |
| `--venue` | string | Yes | - | Exchange/venue (e.g., NASDAQ, NYSE) |
| `--bar-type` | string | No | 1-MINUTE-LAST | Bar type specification |
| `--tz` | string | No | UTC | Timezone for CSV timestamps |
| `--on-conflict` | enum | No | skip | Behavior when data exists: skip, overwrite, merge |

### CSV Format Requirements
```csv
symbol,timestamp,open,high,low,close,volume
AAPL,2024-01-02 09:30:00,185.50,186.25,185.30,185.80,125000
AAPL,2024-01-02 09:31:00,185.80,186.00,185.60,185.90,98000
```

**Column Requirements**:
- `symbol`: String, must match --symbol argument
- `timestamp`: ISO 8601 format (YYYY-MM-DD HH:MM:SS)
- `open`, `high`, `low`, `close`: Decimal, precision <= 8 places
- `volume`: Integer >= 0

**Validation Rules**:
- high >= open, high >= close, high >= low
- low <= open, low <= close, low <= high
- close > 0, open > 0
- timestamps monotonically increasing (sorted ASC)

### Success Output
```
Importing CSV data to Parquet catalog...

✓ Validated CSV structure (1500 rows, 7 columns)
✓ Validated data:
  - Timestamp format: OK (ISO 8601)
  - Price ranges: OK (OHLCV constraints met)
  - Volume: OK (all >= 0)
  - Sorting: OK (timestamps monotonic)

✓ Converted to Nautilus Bar format (1500 bars)
✓ Written to Parquet:
  - data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/2024-01-02.parquet (50 KB)
  - data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/2024-01-03.parquet (50 KB)
  - data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/2024-01-04.parquet (50 KB)

Summary:
  Total rows imported: 1500
  Date range: 2024-01-02 09:30:00 to 2024-01-04 16:00:00 (UTC)
  Disk size: 150 KB (compressed)

✓ Data available for backtests
```

**Exit Code**: 0

### Error Output (Validation Failed)
```
❌ CSV validation failed

Errors found:
  Row 45: High (185.20) < Low (185.50) - INVALID
  Row 127: Negative volume (-1000) - INVALID
  Row 203: Timestamp not monotonic (2024-01-02 10:15:00 < previous 2024-01-02 10:20:00)

💡 Fix CSV data and retry import
```

**Exit Code**: 1

### Conflict Behavior

**skip (default)**:
```
⚠ Data already exists for 2024-01-02 (AAPL.NASDAQ/1-MINUTE-LAST)

Skipping 390 rows for 2024-01-02 (use --on-conflict overwrite to replace)

✓ Imported 1110 new rows (2024-01-03, 2024-01-04)
```

**overwrite**:
```
⚠ Data already exists for 2024-01-02, 2024-01-03

Overwriting existing data...
  Deleted: data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/2024-01-02.parquet
  Deleted: data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/2024-01-03.parquet

✓ Imported 1500 rows (all dates)
```

**merge**:
```
⚠ Data overlap detected for 2024-01-02 (partial day)

Merging with existing data...
  Existing: 2024-01-02 09:30:00 to 12:00:00 (150 bars)
  New:      2024-01-02 12:00:00 to 16:00:00 (240 bars)
  Merged:   2024-01-02 09:30:00 to 16:00:00 (390 bars, deduplicated)

✓ Imported 1500 rows, merged 240 with existing data
```

---

## Command: `ntrader data migrate`

**Purpose**: Migrate PostgreSQL market data to Parquet catalog

### Syntax
```bash
ntrader data migrate \
  [--symbol SYMBOL] \
  [--start START_DATE] \
  [--end END_DATE] \
  [--validate]
```

### Arguments

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `--symbol` | string | No | all | Symbol to migrate (or "all") |
| `--start` | date | No | earliest | Start date for migration |
| `--end` | date | No | latest | End date for migration |
| `--validate` | flag | No | false | Validate migrated data (compare row counts, spot checks) |

### Success Output
```
Migrating PostgreSQL market data to Parquet catalog...

Discovering PostgreSQL data...
  Found 3 symbols: AAPL, MSFT, GOOGL
  Date range: 2023-01-01 to 2024-12-31

Migrating AAPL (2023-01-01 to 2024-12-31)...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% (252/252 days)

✓ Exported 98,280 rows from PostgreSQL
✓ Converted to Nautilus Bar format
✓ Written to Parquet: data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/

Validating migration (--validate flag enabled)...
  PostgreSQL rows: 98,280
  Parquet rows:    98,280
  Match: ✓

  Timestamp range:
    PostgreSQL: 2023-01-03 09:30:00 to 2024-12-29 16:00:00
    Parquet:    2023-01-03 09:30:00 to 2024-12-29 16:00:00
    Match: ✓

  Spot check (100 random samples):
    OHLCV values match: ✓

✓ Migration complete for AAPL

Summary:
  Total symbols migrated: 3
  Total rows: 250,000
  Validation: PASSED
  Disk size: ~12 MB (compressed)

⚠ PostgreSQL data preserved (not deleted)
💡 Review migrated data, then manually deprecate PostgreSQL table
```

**Exit Code**: 0

---

## Exit Code Standards

| Code | Meaning | Example |
|------|---------|---------|
| 0 | Success | Backtest ran, data imported, check completed |
| 1 | Data error | Data not found, CSV validation failed |
| 2 | Input error | Invalid arguments, date range backwards |
| 3 | Connection error | IBKR unavailable when required, database connection failed |
| 4 | System error | Disk full, catalog corrupted, permissions error |

---

## Message Formatting Standards

### Success Messages
- Prefix: `✓` (checkmark)
- Color: Green (if terminal supports)
- Format: `✓ {action completed}: {details}`

### Warning Messages
- Prefix: `⚠` (warning sign)
- Color: Yellow
- Format: `⚠ {condition}: {context}`

### Error Messages
- Prefix: `❌` (cross mark)
- Color: Red
- Format: `❌ {problem}: {brief description}`

### Info Messages
- Prefix: `📊` (chart) for data, `🔧` (wrench) for actions, `💡` (lightbulb) for tips
- Color: Blue
- Format: `📊 {information}`

### Progress Bars
- Library: Rich progress bar
- Format: `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 45% (3/7 items)`
- Show: Percentage, current/total, ETA (for >10 second operations)

---

## Contract Testing Requirements

Each command must have integration tests verifying:

1. **Success paths**: Command succeeds with expected output
2. **Error paths**: Command fails gracefully with correct exit code
3. **Edge cases**: Empty catalog, missing arguments, invalid dates
4. **Output format**: Messages match specification (exact format)
5. **Exit codes**: All exit codes correctly returned

### Example Test Structure
```python
# tests/integration/test_cli_backtest_run.py

def test_backtest_run_with_catalog_data_succeeds():
    """Test User Story 1: Backtest loads from catalog."""
    # Arrange: Pre-populate catalog with test data
    # Act: Run command
    # Assert: Exit code 0, output contains "Loaded from catalog"

def test_backtest_run_without_data_and_no_ibkr_fails():
    """Test User Story 3: Clear error when data unavailable."""
    # Arrange: Empty catalog, IBKR disconnected
    # Act: Run command
    # Assert: Exit code 1, output contains resolution steps
```

---

**Ready for**: Implementation (Phase 3), CLI integration tests, User acceptance testing
