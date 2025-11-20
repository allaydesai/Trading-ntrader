# Data Model: Chart APIs

**Branch**: `008-chart-apis` | **Date**: 2025-11-19

## Overview

This document defines the Pydantic models for Chart API request validation and response serialization. All models target TradingView Lightweight Charts compatibility.

---

## 1. Timeseries API Models

### Request Parameters

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `symbol` | str | Yes | - | Non-empty, max 20 chars |
| `start` | datetime | Yes | - | UTC datetime |
| `end` | datetime | Yes | - | Must be > start |
| `timeframe` | str | No | `"1_MIN"` | Enum: `1_MIN`, `5_MIN`, `15_MIN`, `1_HOUR`, `1_DAY` |

### Response Models

```python
class Candle(BaseModel):
    """Single OHLCV candle for TradingView chart."""
    time: str  # ISO 8601 date format: "2023-01-15"
    open: float
    high: float
    low: float
    close: float
    volume: int

class TimeseriesResponse(BaseModel):
    """OHLCV time series response."""
    symbol: str
    timeframe: str
    candles: list[Candle]
```

### Validation Rules

- `high` must be >= `open`, `close`, `low`
- `low` must be <= `open`, `close`, `high`
- `volume` must be >= 0
- Candles must be sorted by `time` ascending

---

## 2. Trades API Models

### Request Parameters

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `run_id` | UUID | Yes | - | Valid UUID format |

### Response Models

```python
class TradeMarker(BaseModel):
    """Single trade marker for chart overlay."""
    time: str  # ISO 8601 date format
    side: str  # "buy" or "sell"
    price: float
    quantity: int
    pnl: float  # Realized P&L for this trade

class TradesResponse(BaseModel):
    """Trade markers response for backtest run."""
    run_id: UUID
    trade_count: int
    trades: list[TradeMarker]
```

### Validation Rules

- `side` must be "buy" or "sell" (enum)
- `price` must be > 0
- `quantity` must be > 0
- Trades sorted by `time` ascending

---

## 3. Equity API Models

### Request Parameters

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `run_id` | UUID | Yes | - | Valid UUID format |

### Response Models

```python
class EquityPoint(BaseModel):
    """Single equity curve point."""
    time: str  # ISO 8601 date format
    value: float  # Portfolio value

class DrawdownPoint(BaseModel):
    """Single drawdown point."""
    time: str  # ISO 8601 date format
    value: float  # Percentage from peak (negative number)

class EquityResponse(BaseModel):
    """Equity curve and drawdown response."""
    run_id: UUID
    equity: list[EquityPoint]
    drawdown: list[DrawdownPoint]
```

### Validation Rules

- `equity.value` must be > 0
- `drawdown.value` must be <= 0 (underwater percentage)
- Points sorted by `time` ascending
- `equity` and `drawdown` arrays must have same length

---

## 4. Indicators API Models

### Request Parameters

| Parameter | Type | Required | Default | Constraints |
|-----------|------|----------|---------|-------------|
| `run_id` | UUID | Yes | - | Valid UUID format |

### Response Models

```python
class IndicatorPoint(BaseModel):
    """Single indicator value point."""
    time: str  # ISO 8601 date format
    value: float

class IndicatorSeries(BaseModel):
    """Named indicator series."""
    name: str
    data: list[IndicatorPoint]

class IndicatorsResponse(BaseModel):
    """Indicator series response for backtest run."""
    run_id: UUID
    indicators: dict[str, list[IndicatorPoint]]  # {"sma_fast": [...], "sma_slow": [...]}
```

### Validation Rules

- Indicator names must be non-empty strings
- Points sorted by `time` ascending

---

## 5. Error Response Models

```python
class ErrorDetail(BaseModel):
    """Error response with actionable suggestions."""
    detail: str
    suggestion: str | None = None  # CLI command hint

class ValidationErrorDetail(BaseModel):
    """Validation error response."""
    detail: str
    errors: list[dict]  # Pydantic validation errors
```

### HTTP Status Codes

| Code | When Used |
|------|-----------|
| 200 | Success (including empty results) |
| 404 | Data or backtest not found |
| 422 | Invalid request parameters |
| 500 | Internal server error |

---

## 6. Entity Relationships

```
BacktestRun (PostgreSQL)
    │
    ├── 1:N TradeMarker (computed from config_snapshot)
    │
    ├── 1:N EquityPoint (computed from trades)
    │
    ├── 1:N DrawdownPoint (computed from equity)
    │
    └── 1:N IndicatorSeries (from config_snapshot)

MarketData (Parquet)
    │
    └── 1:N Candle (time series)
```

---

## 7. Data Source Mapping

| Entity | Source | Access Pattern |
|--------|--------|----------------|
| Candle | Parquet files | `DataCatalogService.query_bars()` |
| TradeMarker | PostgreSQL `backtest_runs.config_snapshot` | `BacktestQueryService.get_backtest_by_id()` |
| EquityPoint | Computed from trades | Calculate from trade sequence |
| DrawdownPoint | Computed from equity | Calculate peak-to-trough |
| IndicatorSeries | PostgreSQL `backtest_runs.config_snapshot` | `BacktestQueryService.get_backtest_by_id()` |

---

## 8. TradingView Format Mapping

### Candlestick Series

```javascript
// TradingView expects:
{ time: "2023-01-15", open: 177.09, high: 177.93, low: 175.86, close: 177.04 }

// Our API returns:
{ "time": "2023-01-15", "open": 177.09, "high": 177.93, "low": 175.86, "close": 177.04, "volume": 1234567 }
```

### Line Series (Equity/Indicators)

```javascript
// TradingView expects:
{ time: "2023-01-15", value: 10500.00 }

// Our API returns:
{ "time": "2023-01-15", "value": 10500.00 }
```

### Series Markers (Trades)

```javascript
// TradingView expects:
{
  time: "2023-01-15",
  position: "belowBar",
  color: "#22c55e",
  shape: "arrowUp",
  text: "BUY"
}

// Frontend transforms our TradeMarker to this format
```

---

## 9. Pydantic Configuration

All models use consistent configuration:

```python
model_config = {
    "json_encoders": {
        datetime: lambda v: v.strftime("%Y-%m-%d"),
        Decimal: float,
        UUID: str,
    },
    "extra": "forbid",  # Fail on unknown fields
}
```

---

## 10. File Organization

```
src/api/models/
├── chart_timeseries.py   # Candle, TimeseriesResponse
├── chart_trades.py       # TradeMarker, TradesResponse
├── chart_equity.py       # EquityPoint, DrawdownPoint, EquityResponse
├── chart_indicators.py   # IndicatorPoint, IndicatorsResponse
└── chart_errors.py       # ErrorDetail, ValidationErrorDetail
```
