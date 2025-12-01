# Bug Report: Trades and Indicators Not Persisting to Database

**Created**: 2025-01-27
**Severity**: High
**Status**: Open
**Affects**: Backtest visualization, trade tracking, indicator display

## Summary

Backtest runs complete successfully and report trade statistics in CLI output, but trades and indicators are **not being persisted** to the PostgreSQL database. This causes:
- Empty charts (no trade markers)
- Empty equity curves
- Missing indicator overlays
- Zero rows in `trades` table

## Environment

- **Branch**: `010-enhanced-price-plot`
- **Database**: PostgreSQL 16+ (`trading_ntrader`)
- **Python**: 3.11+
- **Framework**: Nautilus Trader with FastAPI backend

## Steps to Reproduce

1. Run a backtest with Bollinger Reversal strategy:
   ```bash
   uv run python -m src.cli.main backtest run \
     -s bollinger_reversal \
     -sym SPY.ARCA \
     -st 2020-11-26 \
     -e 2025-11-26 \
     -ts 1000000 \
     -t 1-day
   ```

2. Observe CLI output showing successful execution:
   ```
   ✅ Backtest completed successfully
      Total trades: 86
      Win rate: 50.00%
      Final P&L: $4,291,000.00
   ```

3. Check database for trades:
   ```sql
   SELECT COUNT(*) FROM trades
   WHERE backtest_run_id = (
     SELECT id FROM backtest_runs
     ORDER BY created_at DESC LIMIT 1
   );
   ```

4. Check config_snapshot for trade data:
   ```sql
   SELECT
     run_id,
     jsonb_typeof(config_snapshot->'trades') as trades_type,
     jsonb_typeof(config_snapshot->'indicators') as indicators_type
   FROM backtest_runs
   ORDER BY created_at DESC LIMIT 1;
   ```

## Expected Behavior

After a successful backtest run:

1. **`trades` table** should contain rows for each executed trade:
   - Entry and exit timestamps
   - Prices, quantities
   - Profit/loss calculations
   - Foreign key to `backtest_runs.id`

2. **`config_snapshot` JSONB column** should contain:
   ```json
   {
     "trades": [
       {
         "time": "2021-05-12",
         "side": "buy",
         "price": 405.41,
         "quantity": 11027,
         "pnl": 0.0
       },
       {
         "time": "2021-07-26",
         "side": "sell",
         "price": 437.13,
         "quantity": 11027,
         "pnl": 350000.00
       }
       // ... 84 more trades
     ],
     "indicators": {
       "upper_band": [
         {"time": "2020-11-26", "value": 361.23},
         // ... more data points
       ],
       "middle_band": [...],
       "lower_band": [...]
     }
   }
   ```

3. **Chart APIs** should return populated data:
   - `/api/trades/{run_id}` → 86 trade markers
   - `/api/indicators/{run_id}` → Bollinger Band series
   - `/api/timeseries` → OHLCV data (✅ working)

## Actual Behavior

### Database State

```sql
-- Latest backtest run
run_id: 56b9f19d-a160-410a-9525-44445cf4c059
strategy_name: Bollinger Reversal
execution_status: completed

-- Trades table: EMPTY
SELECT COUNT(*) FROM trades
WHERE backtest_run_id = (SELECT id FROM backtest_runs ORDER BY created_at DESC LIMIT 1);
-- Result: 0

-- Config snapshot: NULL
SELECT
  jsonb_typeof(config_snapshot->'trades') as trades_type,
  jsonb_typeof(config_snapshot->'indicators') as indicators_type
FROM backtest_runs ORDER BY created_at DESC LIMIT 1;
-- Result: trades_type=NULL, indicators_type=NULL
```

### API Responses

```bash
# /api/trades/{run_id}
curl http://localhost:8000/api/trades/56b9f19d-a160-410a-9525-44445cf4c059
{
  "run_id": "56b9f19d-a160-410a-9525-44445cf4c059",
  "trade_count": 0,
  "trades": []
}

# /api/indicators/{run_id}
curl http://localhost:8000/api/indicators/56b9f19d-a160-410a-9525-44445cf4c059
{
  "run_id": "56b9f19d-a160-410a-9525-44445cf4c059",
  "indicators": {}
}
```

### Chart Display

- ✅ OHLCV candlesticks render correctly
- ✅ Volume histogram displays
- ❌ **No trade markers** (empty array)
- ❌ **No indicator overlays** (empty object)
- ❌ **Equity curve shows initial capital only** (no trade points)

## Root Cause Analysis

### Where Trade Persistence Should Happen

The backtest runner (`src/core/backtest_runner.py`) should be persisting trades during or after backtest execution. Based on the codebase structure:

**Spec 009 (trade-tracking)** added:
- `src/db/models/trade.py` - SQLAlchemy Trade model
- `trades` table with foreign key to `backtest_runs.id`
- Trade analytics services

**Expected Flow**:
1. Backtest engine executes trades via Nautilus Trader
2. Trade fills generate events → `OrderFilled` events
3. Backtest runner should capture these events
4. Convert Nautilus trade objects → SQLAlchemy Trade models
5. Persist to `trades` table via database session
6. **OR** serialize trades to `config_snapshot` JSONB for API consumption

### Suspected Missing Components

1. **Trade Event Listener** - No handler capturing `OrderFilled` events
2. **Trade Serialization** - No code converting Nautilus trades to database format
3. **Indicator Extraction** - No logic extracting indicator series from strategy
4. **Config Snapshot Population** - `config_snapshot` JSONB not being populated with trades/indicators

### Files to Investigate

```
src/core/backtest_runner.py          # Main backtest orchestration
src/services/trade_analytics.py       # Trade persistence logic
src/db/repositories/                   # Database write operations
src/strategies/bollinger_reversal.py  # Strategy indicator access
```

## Impact

### Features Affected

1. **Feature 007 (backtest-detail-view)** - Charts display empty
2. **Feature 008 (chart-apis)** - APIs return empty arrays
3. **Feature 009 (trade-tracking)** - Trade table unused
4. **Feature 010 (enhanced-price-plot)** - New chart features have no data to display

### User Experience

- ❌ Users cannot visualize trade execution points
- ❌ Users cannot see indicator overlays
- ❌ Users cannot analyze equity curve progression
- ❌ Trade statistics endpoints return zeroes
- ✅ CLI summary still works (uses in-memory backtest results)

## Workaround

**None available**. This is a blocking issue for all chart-based features.

## Proposed Fix

### Phase 1: Immediate (Trade Persistence)

1. **Add trade event capture** in `BacktestRunner`:
   ```python
   def _persist_trades(self, engine: BacktestEngine, backtest_id: int):
       """Extract and persist trades from backtest engine."""
       # Get trades from engine.trader.cache
       filled_orders = engine.trader.cache.orders_filled()

       # Convert to Trade models and persist
       for order in filled_orders:
           trade = Trade(
               backtest_run_id=backtest_id,
               instrument_id=str(order.instrument_id),
               entry_timestamp=order.ts_event,
               # ... map remaining fields
           )
           session.add(trade)
   ```

2. **Populate config_snapshot** with trades for API compatibility:
   ```python
   config_snapshot = {
       "trades": [
           {
               "time": trade.entry_timestamp.isoformat(),
               "side": trade.order_side.lower(),
               "price": float(trade.entry_price),
               "quantity": int(trade.quantity),
               "pnl": float(trade.profit_loss or 0)
           }
           for trade in trades
       ],
       "indicators": extract_indicators_from_strategy(strategy)
   }
   ```

### Phase 2: Indicator Persistence

1. **Extract indicators from strategy** after backtest:
   ```python
   def _extract_indicators(self, strategy: Strategy) -> dict:
       """Extract indicator series from strategy instance."""
       indicators = {}

       # For Bollinger Reversal
       if hasattr(strategy, 'bb'):
           indicators['upper_band'] = strategy.bb.upper_band_values
           indicators['middle_band'] = strategy.bb.middle_band_values
           indicators['lower_band'] = strategy.bb.lower_band_values

       # For SMA strategies
       if hasattr(strategy, 'sma_fast'):
           indicators['sma_fast'] = strategy.sma_fast.values

       return indicators
   ```

2. **Serialize to config_snapshot** for API consumption

### Phase 3: Testing

1. Run backtest and verify:
   - `trades` table has 86 rows
   - `config_snapshot->trades` has 86 elements
   - `config_snapshot->indicators` has Bollinger Band arrays
   - API endpoints return populated data

2. Verify charts render correctly:
   - Trade markers at correct timestamps/prices
   - Bollinger Bands overlay on price chart
   - Equity curve shows progression

## Database Schema (Reference)

### `trades` table
```sql
CREATE TABLE trades (
    id BIGSERIAL PRIMARY KEY,
    backtest_run_id BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    instrument_id VARCHAR(50) NOT NULL,
    trade_id VARCHAR(100) NOT NULL,
    order_side VARCHAR(10) NOT NULL,
    entry_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    entry_price NUMERIC(20,8) NOT NULL,
    exit_timestamp TIMESTAMP WITH TIME ZONE,
    exit_price NUMERIC(20,8),
    quantity NUMERIC(20,8) NOT NULL,
    profit_loss NUMERIC(20,2),
    profit_pct NUMERIC(10,4),
    commission_amount NUMERIC(20,2),
    holding_period_seconds INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### `backtest_runs.config_snapshot` (JSONB)
```json
{
  "trades": [
    {
      "time": "ISO 8601 date",
      "side": "buy|sell",
      "price": 123.45,
      "quantity": 100,
      "pnl": 0.0
    }
  ],
  "indicators": {
    "upper_band": [{"time": "...", "value": 123.45}],
    "middle_band": [...],
    "lower_band": [...]
  }
}
```

## Related Issues

- Spec 009: trade-tracking feature (added `trades` table but no persistence logic)
- Spec 008: chart-apis feature (APIs work but have no data)
- Spec 010: enhanced-price-plot (charts enhanced but no data to display)

## Testing After Fix

```bash
# 1. Run backtest
uv run python -m src.cli.main backtest run -s bollinger_reversal -sym SPY.ARCA -st 2020-11-26 -e 2025-11-26 -ts 1000000 -t 1-day

# 2. Verify database persistence
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader -c \
  "SELECT COUNT(*) FROM trades WHERE backtest_run_id = (SELECT id FROM backtest_runs ORDER BY created_at DESC LIMIT 1);"
# Expected: 86

# 3. Verify config_snapshot
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader -d trading_ntrader -c \
  "SELECT jsonb_array_length(config_snapshot->'trades') FROM backtest_runs ORDER BY created_at DESC LIMIT 1;"
# Expected: 86

# 4. Test API endpoints
curl http://localhost:8000/api/trades/{run_id} | jq '.trade_count'
# Expected: 86

curl http://localhost:8000/api/indicators/{run_id} | jq '.indicators | keys'
# Expected: ["upper_band", "middle_band", "lower_band"]

# 5. Visual verification
# Open backtest detail page in browser - should show:
# - Trade markers on chart (86 arrows)
# - Bollinger Bands (3 lines)
# - Equity curve progression
```

## Priority

**High** - Blocking multiple features (007, 008, 009, 010). Without trade persistence, all chart visualizations are non-functional.

## Assignment

- [ ] Investigate `src/core/backtest_runner.py` for trade extraction
- [ ] Implement trade persistence to `trades` table
- [ ] Populate `config_snapshot` with trades and indicators
- [ ] Test with Bollinger Reversal strategy
- [ ] Verify chart rendering after fix

---

**Note**: The chart implementation in feature 010 is **working correctly**. The JavaScript code properly requests data from APIs, handles responses, and renders markers/indicators. The issue is entirely in the backend data persistence layer.
