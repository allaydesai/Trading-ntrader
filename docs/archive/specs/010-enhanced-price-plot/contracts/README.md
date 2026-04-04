# API Contracts for Enhanced Price Plot

**Feature**: 010-enhanced-price-plot
**Date**: 2025-01-27

## Overview

This feature relies on existing REST API endpoints defined in **specs/008-chart-apis**. No new API endpoints required.

## Required API Endpoints

All endpoints are already implemented and tested. See `src/api/rest/` for implementations.

### 1. GET /api/timeseries

**Purpose**: Fetch OHLCV candlestick data for price chart

**Request**:
```
GET /api/timeseries?symbol=AAPL&start=2024-01-02&end=2024-01-05&timeframe=1-minute
```

**Query Parameters**:
- `symbol` (string, required): Trading symbol (e.g., "AAPL")
- `start` (date, required): Start date (ISO 8601)
- `end` (date, required): End date (ISO 8601)
- `timeframe` (enum, optional): Bar timeframe (default: "1-minute")
  - Values: "1-minute", "5-minute", "1-hour", "1-day"

**Response** (200 OK):
```json
{
  "symbol": "AAPL",
  "timeframe": "1-minute",
  "candles": [
    {
      "time": 1609459200,
      "open": 150.25,
      "high": 150.75,
      "low": 150.00,
      "close": 150.50,
      "volume": 1000000
    }
  ]
}
```

**Error Responses**:
- 404: Market data not found
- 422: Validation error (invalid date range, symbol, etc.)

**Implementation**: `src/api/rest/timeseries.py:get_timeseries()`
**Model**: `src/api/models/chart_timeseries.py:TimeseriesResponse`

---

### 2. GET /api/trades/{run_id}

**Purpose**: Fetch trade markers for chart overlay

**Request**:
```
GET /api/trades/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Path Parameters**:
- `run_id` (UUID, required): Backtest run identifier

**Response** (200 OK):
```json
{
  "run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "trade_count": 2,
  "trades": [
    {
      "time": "2024-01-02T09:30:00Z",
      "side": "buy",
      "price": 150.25,
      "quantity": 100,
      "pnl": 0.0
    },
    {
      "time": "2024-01-02T10:00:00Z",
      "side": "sell",
      "price": 151.00,
      "quantity": 100,
      "pnl": 75.00
    }
  ]
}
```

**Error Responses**:
- 404: Backtest run not found
- 422: Validation error (invalid UUID)

**Implementation**: `src/api/rest/trades.py:get_trades()`
**Model**: `src/api/models/chart_trades.py:TradesResponse`

---

### 3. GET /api/indicators/{run_id}

**Purpose**: Fetch indicator time series for chart overlay

**Request**:
```
GET /api/indicators/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Path Parameters**:
- `run_id` (UUID, required): Backtest run identifier

**Response** (200 OK):
```json
{
  "run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "indicators": {
    "fast_sma": [
      {"time": "2024-01-02T09:30:00Z", "value": 150.25},
      {"time": "2024-01-02T09:31:00Z", "value": 150.30}
    ],
    "slow_sma": [
      {"time": "2024-01-02T09:30:00Z", "value": 149.80},
      {"time": "2024-01-02T09:31:00Z", "value": 149.85}
    ]
  }
}
```

**Error Responses**:
- 404: Backtest run not found
- 422: Validation error (invalid UUID)

**Implementation**: `src/api/rest/indicators.py:get_indicators()`
**Model**: `src/api/models/chart_indicators.py:IndicatorsResponse`

---

## Data Models

### Pydantic Response Models

All response models use Pydantic v2 for validation and serialization.

**TimeseriesResponse**:
```python
class Candle(BaseModel):
    time: int  # Unix timestamp in seconds
    open: float
    high: float
    low: float
    close: float
    volume: int

class TimeseriesResponse(BaseModel):
    symbol: str
    timeframe: str
    candles: list[Candle]
```

**TradesResponse**:
```python
class TradeMarker(BaseModel):
    time: str  # ISO 8601 timestamp
    side: Literal['buy', 'sell']
    price: float
    quantity: int
    pnl: float

class TradesResponse(BaseModel):
    run_id: UUID
    trade_count: int
    trades: list[TradeMarker]
```

**IndicatorsResponse**:
```python
class IndicatorPoint(BaseModel):
    time: str  # ISO 8601 timestamp
    value: float

class IndicatorsResponse(BaseModel):
    run_id: UUID
    indicators: dict[str, list[IndicatorPoint]]
```

---

## Frontend Data Transformations

### ISO Timestamp → Unix Seconds

All API responses use ISO 8601 strings for timestamps. TradingView Lightweight Charts requires Unix seconds.

**Transformation**:
```javascript
function isoToUnix(isoString) {
    return Math.floor(new Date(isoString).getTime() / 1000);
}
```

**Applied to**:
- Trade markers: `trade.time` (ISO) → `marker.time` (Unix)
- Indicator points: `point.time` (ISO) → `{time: Unix, value: ...}`

---

## API Usage in Chart Implementation

### Initialization Sequence

```javascript
async function initializeEnhancedChart(config) {
    // 1. Fetch OHLCV data
    const ohlcvResponse = await fetch(
        `/api/timeseries?symbol=${config.symbol}&start=${config.startDate}&end=${config.endDate}&timeframe=${config.timeframe}`
    );
    const ohlcvData = await ohlcvResponse.json();

    // 2. Fetch trade markers (if enabled)
    let tradeMarkers = [];
    if (config.showTradeMarkers) {
        const tradesResponse = await fetch(`/api/trades/${config.runId}`);
        const tradesData = await tradesResponse.json();
        tradeMarkers = transformTrades(tradesData.trades);
    }

    // 3. Fetch indicators (if enabled)
    let indicators = [];
    if (config.showIndicators) {
        const indicatorsResponse = await fetch(`/api/indicators/${config.runId}`);
        const indicatorsData = await indicatorsResponse.json();
        indicators = transformIndicators(indicatorsData.indicators, config.strategyName);
    }

    // 4. Render chart with all data
    renderChart(ohlcvData, tradeMarkers, indicators);
}
```

---

## Error Handling

### API Error Responses

All endpoints return consistent error format:

```json
{
  "detail": "Error message describing the problem"
}
```

**Frontend Error Handling**:
```javascript
async function fetchWithErrorHandling(url) {
    try {
        const response = await fetch(url);

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Unknown error');
        }

        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        displayErrorMessage(`Failed to load chart data: ${error.message}`);
        return null;
    }
}
```

---

## Testing

### Integration Tests

All API endpoints have existing pytest coverage in `tests/integration/api/`:
- `test_timeseries_api.py`
- `test_trades_api.py`
- `test_indicators_api.py`

**Frontend Chart Tests** (to be added in this feature):
- Playwright tests verifying chart renders with API data
- Mock API responses for predictable test scenarios
- Validation of data transformations (ISO → Unix)

---

## References

- **Spec 008**: Chart APIs specification and implementation
- **Implementations**: `src/api/rest/{timeseries.py, trades.py, indicators.py}`
- **Models**: `src/api/models/chart_{timeseries,trades,indicators}.py`
- **Tests**: `tests/integration/api/test_chart_*.py`

---

## Summary

✅ All required API endpoints exist and are tested
✅ Pydantic models provide type safety and validation
✅ Frontend needs only timestamp transformation (ISO → Unix)
✅ Error handling patterns established in existing endpoints

**No new backend API development required for this feature.**
