# Research: Chart APIs

**Branch**: `008-chart-apis` | **Date**: 2025-11-19

## Research Summary

This document captures technical research findings for implementing Chart APIs that serve TradingView Lightweight Charts compatible JSON data.

---

## 1. TradingView Lightweight Charts Data Formats

### Decision: Use ISO 8601 date strings for timestamps

**Rationale**: TradingView Lightweight Charts accepts multiple timestamp formats, but ISO 8601 date strings (`"2019-01-16"`) are:
- Most human-readable in JSON responses
- Consistent with existing API patterns
- Supported for all series types

**Alternatives Considered**:
- Unix timestamps (integer seconds): More compact but less readable
- JavaScript Date objects: Not JSON-serializable

### Candlestick/OHLCV Data Format

```json
{
  "candles": [
    {
      "time": "2023-01-15",
      "open": 177.09,
      "high": 177.93,
      "low": 175.86,
      "close": 177.04
    }
  ]
}
```

**Key Points**:
- `time` as ISO 8601 date string
- OHLC values as floats (not Decimal strings)
- Volume included separately for volume charts (optional)
- Data must be sorted chronologically (ascending)

### Line Series Data Format (Equity Curve, Indicators)

```json
{
  "data": [
    {
      "time": "2023-01-15",
      "value": 10500.00
    }
  ]
}
```

**Key Points**:
- Same `time` format as candlesticks
- Single `value` field for line series

### Series Markers Format (Trade Markers)

```json
{
  "markers": [
    {
      "time": "2023-01-15",
      "position": "belowBar",
      "color": "#26a69a",
      "shape": "arrowUp",
      "text": "BUY"
    }
  ]
}
```

**Marker Options**:
- `position`: `"aboveBar"`, `"belowBar"`, `"inBar"`
- `shape`: `"circle"`, `"square"`, `"arrowUp"`, `"arrowDown"`
- `color`: hex color string
- `text`: optional label

---

## 2. Data Source Integration

### Decision: Reuse existing DataCatalogService for OHLCV

**Rationale**: The DataCatalogService already provides optimized Parquet file access via `query_bars()`. No new data access layer needed.

**Key Method**:
```python
bars = service.query_bars(
    instrument_id="AAPL.NASDAQ",
    start=datetime(2023, 1, 1, tzinfo=timezone.utc),
    end=datetime(2023, 12, 31, tzinfo=timezone.utc),
    bar_type_spec="1-MINUTE-LAST"
)
```

**Conversion Required**:
- Nautilus `Bar` objects to TradingView JSON format
- Nanosecond timestamps to ISO 8601 date strings
- Decimal prices to floats

### Decision: Reuse BacktestQueryService for trade/equity data

**Rationale**: BacktestQueryService handles all PostgreSQL queries for backtest metadata via the repository pattern.

**Key Method**:
```python
backtest = await service.get_backtest_by_id(run_id)
# Access: backtest.config_snapshot, backtest.metrics
```

**Trade Data Location**: Trades are stored in `backtest.config_snapshot` JSONB field (need to verify structure).

**Equity Curve**: Computed from backtest results, may need to reconstruct from trade sequence.

---

## 3. Symbol and Timeframe Handling

### Decision: Use Nautilus instrument_id format internally

**Rationale**: Nautilus Trader uses `{SYMBOL}.{VENUE}` format (e.g., `AAPL.NASDAQ`). The API should accept simple symbols and map to this format.

**Symbol Mapping**:
- User provides: `AAPL`
- API converts to: `AAPL.NASDAQ` (default venue)
- Or user provides full: `AAPL.NASDAQ`

### Decision: Use standardized timeframe strings

**Timeframe Mapping**:
| User Input | Nautilus bar_type_spec |
|------------|------------------------|
| `1_MIN` | `1-MINUTE-LAST` |
| `5_MIN` | `5-MINUTE-LAST` |
| `15_MIN` | `15-MINUTE-LAST` |
| `1_HOUR` | `1-HOUR-LAST` |
| `1_DAY` | `1-DAY-LAST` |

**Alternatives Considered**:
- Pass-through Nautilus format: Too verbose for API users
- Numeric only (1, 5, 15): Ambiguous (minutes vs hours)

---

## 4. Error Handling Strategy

### Decision: Use actionable error messages with CLI hints

**Rationale**: Spec requires 404 errors to suggest CLI commands for missing data. This improves user experience for local development context.

**Error Response Format**:
```json
{
  "detail": "Market data not found for AAPL from 2023-01-01 to 2023-12-31",
  "suggestion": "Run: ntrader data fetch --symbol AAPL --start 2023-01-01 --end 2023-12-31"
}
```

### HTTP Status Codes

| Scenario | Code | Message |
|----------|------|---------|
| Invalid parameters | 422 | Validation error details |
| Data not found | 404 | Actionable message + CLI hint |
| Backtest not found | 404 | "Backtest {uuid} not found" |
| Server error | 500 | Generic error message |

---

## 5. Performance Optimization

### Decision: Return data as-is without pagination for initial implementation

**Rationale**:
- Time series data needs to be complete for chart rendering
- Spec requires <500ms for 100k candles
- Parquet columnar format is efficient for range queries

**Alternatives Considered**:
- Streaming response: Adds complexity, TradingView expects complete data
- Pagination: Would require multiple requests to render chart
- Downsampling: Could implement later if needed

### Optimization Techniques

1. **Parquet Columnar Reads**: DataCatalogService already optimized
2. **Eager Loading**: Use selectinload for backtest metrics (existing pattern)
3. **Float Conversion**: Convert Decimal to float in Python (faster JSON serialization)

---

## 6. Volume Handling

### Decision: Include volume in OHLCV response for volume chart support

**Rationale**: TradingView supports volume histogram below candlesticks. Including volume enables this feature.

**Response Format**:
```json
{
  "candles": [
    {
      "time": "2023-01-15",
      "open": 177.09,
      "high": 177.93,
      "low": 175.86,
      "close": 177.04,
      "volume": 1234567
    }
  ]
}
```

---

## 7. Drawdown Calculation

### Decision: Calculate drawdown server-side from equity curve

**Rationale**: Drawdown is derived from equity curve, should be consistent with CLI reports.

**Formula**:
```python
def calculate_drawdown(equity_values: list[float]) -> list[float]:
    drawdowns = []
    peak = equity_values[0]
    for value in equity_values:
        peak = max(peak, value)
        drawdown = (value - peak) / peak * 100  # percentage
        drawdowns.append(drawdown)
    return drawdowns
```

---

## 8. Indicator Series Storage

### Decision: Read indicator values from backtest config_snapshot

**Rationale**: Indicator values are computed during backtest execution and stored in the run's config_snapshot JSONB field.

**Expected Structure** (to verify):
```json
{
  "indicators": {
    "sma_fast": [{"time": "2023-01-15", "value": 175.5}, ...],
    "sma_slow": [{"time": "2023-01-15", "value": 172.3}, ...]
  }
}
```

**Fallback**: If indicators not stored, return empty object with 200 status.

---

## 9. Dependency Injection Pattern

### Decision: Create DataCatalogService dependency provider

**Rationale**: Follow existing pattern in `src/api/dependencies.py` for consistency.

**Implementation**:
```python
def get_data_catalog_service() -> DataCatalogService:
    catalog_path = os.environ.get("NAUTILUS_PATH", "./data/catalog")
    return DataCatalogService(catalog_path=catalog_path)

DataCatalog = Annotated[DataCatalogService, Depends(get_data_catalog_service)]
```

---

## 10. Response Model Design

### Decision: Separate models per endpoint, no shared base

**Rationale**: Each endpoint has distinct response structure. Shared base adds unnecessary complexity.

**Models**:
- `TimeseriesResponse`: candles array + metadata
- `TradesResponse`: trades array + markers array
- `EquityResponse`: equity array + drawdown array
- `IndicatorsResponse`: indicators dict with named series

---

## Open Questions Resolved

1. **Q: How to handle gaps in market data?**
   - A: Return data as-is. TradingView handles gaps gracefully. API doesn't fill gaps.

2. **Q: What about concurrent requests for same large dataset?**
   - A: Parquet reads are independent. No caching for initial implementation.

3. **Q: Timeframe not available?**
   - A: Return 404 with suggestion to fetch data at different timeframe.

---

## References

- TradingView Lightweight Charts: https://github.com/tradingview/lightweight-charts
- Existing DataCatalogService: `src/services/data_catalog.py`
- Existing BacktestQueryService: `src/services/backtest_query.py`
- API Dependencies: `src/api/dependencies.py`
