# Sample Market Data

This directory contains sample CSV data files for testing the NTrader backtesting system.

## Files

### sample_AAPL.csv
- **Symbol**: AAPL (Apple Inc.)
- **Period**: January 2, 2024, 09:30:00 to 10:20:00 (50 minutes)
- **Frequency**: 1-minute bars
- **Records**: 50 data points
- **Format**: timestamp,open,high,low,close,volume

### Data Characteristics
- Realistic price movements with intraday volatility
- Starting price around $185.25, trending up to ~$191.40, then pulling back
- Volume patterns typical of AAPL trading
- Includes gaps between bars for realistic execution simulation
- OHLC integrity maintained (high >= open,close,low and low <= open,close,high)

## Usage

### Import Sample Data
```bash
# Import AAPL sample data
ntrader data import-csv --file data/sample_AAPL.csv --symbol AAPL
```

### Run Backtest with Sample Data
```bash
# Run SMA crossover strategy on imported data
ntrader backtest run --symbol AAPL --start 2024-01-02 --end 2024-01-02 --fast-period 5 --slow-period 10
```

### Expected Results
With the sample data:
- Fast SMA (5-period) will be more responsive to price changes
- Slow SMA (10-period) will provide trend direction
- The upward trend should generate buy signals
- The pullback at the end may generate exit signals

## Data Quality Notes

1. **Realistic Spreads**: Bid-ask spreads reflected in price movements
2. **Volume Patterns**: Higher volume during price moves, lower during consolidation
3. **Time Alignment**: All timestamps are in market hours (Eastern Time)
4. **Price Precision**: Prices to 2 decimal places (typical for US equities)
5. **No Gaps**: Continuous 1-minute data for consistent backtesting

## Creating Custom Sample Data

To create your own sample data:

1. **Format**: Follow the CSV header: `timestamp,open,high,low,close,volume`
2. **Timestamps**: Use ISO format: `YYYY-MM-DD HH:MM:SS`
3. **OHLC Rules**: Ensure high >= open,close,low and low <= open,close,high
4. **Volume**: Use realistic volumes for the asset class
5. **Symbol**: Import with appropriate symbol name

Example:
```csv
timestamp,open,high,low,close,volume
2024-01-03 09:30:00,191.50,192.00,191.25,191.75,1500000
2024-01-03 09:31:00,191.75,192.25,191.50,192.00,1200000
```

## Testing Strategy

The sample data is designed to test:
- ✅ CSV import functionality
- ✅ Data validation and integrity checks
- ✅ SMA calculation accuracy
- ✅ Signal generation logic
- ✅ Trade execution simulation
- ✅ Performance metrics calculation

## Troubleshooting

### Import Issues
- Check CSV format matches exactly
- Verify timestamps are valid
- Ensure OHLC relationships are correct
- Check for duplicate timestamps

### Backtest Issues
- Verify data was imported successfully: `ntrader data list`
- Check date range covers imported data
- Ensure database connection is working
- Validate SMA periods are reasonable for data length