# Quickstart: Chart APIs

**Branch**: `008-chart-apis` | **Date**: 2025-11-19

## Prerequisites

1. **Running PostgreSQL** with backtest metadata
2. **Parquet data catalog** with market data
3. **Python environment** with dependencies installed

```bash
# Verify environment
uv sync
uv run python -c "import fastapi; print('FastAPI ready')"
```

---

## Quick Start

### 1. Start the Web Server

```bash
uv run uvicorn src.api.web:app --reload --host 0.0.0.0 --port 8000
```

### 2. Test Endpoints

#### Time Series API
```bash
# Fetch AAPL 1-minute data for January 2024
curl "http://localhost:8000/api/timeseries?symbol=AAPL&start=2024-01-01&end=2024-01-31&timeframe=1_MIN"
```

Expected response:
```json
{
  "symbol": "AAPL",
  "timeframe": "1_MIN",
  "candles": [
    {
      "time": "2024-01-02",
      "open": 185.50,
      "high": 186.00,
      "low": 185.25,
      "close": 185.75,
      "volume": 12345678
    }
  ]
}
```

#### Trade Markers API
```bash
# Fetch trades for a backtest run
curl "http://localhost:8000/api/trades/550e8400-e29b-41d4-a716-446655440000"
```

#### Equity Curve API
```bash
# Fetch equity and drawdown
curl "http://localhost:8000/api/equity/550e8400-e29b-41d4-a716-446655440000"
```

#### Indicators API
```bash
# Fetch indicator series
curl "http://localhost:8000/api/indicators/550e8400-e29b-41d4-a716-446655440000"
```

---

## Development Workflow

### Run Tests (TDD)

```bash
# Run all chart API tests
uv run pytest tests/api/rest/ -v

# Run specific endpoint tests
uv run pytest tests/api/rest/test_timeseries.py -v

# Run with coverage
uv run pytest tests/api/rest/ --cov=src/api/rest --cov-report=term-missing
```

### Code Quality

```bash
# Format
uv run ruff format src/api/rest/ src/api/models/chart*.py

# Lint
uv run ruff check src/api/rest/ src/api/models/chart*.py

# Type check
uv run mypy src/api/rest/ src/api/models/chart*.py
```

---

## API Reference

### Base URL
```
http://localhost:8000/api
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/timeseries` | OHLCV candlestick data |
| GET | `/trades/{run_id}` | Trade markers for backtest |
| GET | `/equity/{run_id}` | Equity curve and drawdown |
| GET | `/indicators/{run_id}` | Indicator series |

### Common Query Parameters

#### Timeseries
- `symbol` (required): Trading symbol (e.g., `AAPL`)
- `start` (required): Start date ISO 8601 (`2024-01-01`)
- `end` (required): End date ISO 8601 (`2024-12-31`)
- `timeframe` (optional): Bar size (`1_MIN`, `5_MIN`, `15_MIN`, `1_HOUR`, `1_DAY`)

#### Backtest Endpoints
- `run_id` (required): UUID of backtest run

---

## Error Handling

### 404 - Not Found

```json
{
  "detail": "Market data not found for AAPL from 2024-01-01 to 2024-01-31",
  "suggestion": "Run: ntrader data fetch --symbol AAPL --start 2024-01-01 --end 2024-01-31"
}
```

### 422 - Validation Error

```json
{
  "detail": [
    {
      "loc": ["query", "end"],
      "msg": "End date must be after start date",
      "type": "value_error"
    }
  ]
}
```

---

## Frontend Integration

### TradingView Lightweight Charts

```javascript
// Fetch candlestick data
const response = await fetch(
  `/api/timeseries?symbol=AAPL&start=2024-01-01&end=2024-01-31&timeframe=1_MIN`
);
const data = await response.json();

// Set data on chart
const candleSeries = chart.addCandlestickSeries();
candleSeries.setData(data.candles);
```

### Trade Markers

```javascript
// Fetch and convert trades to markers
const response = await fetch(`/api/trades/${runId}`);
const data = await response.json();

const markers = data.trades.map(t => ({
  time: t.time,
  position: t.side === "buy" ? "belowBar" : "aboveBar",
  color: t.side === "buy" ? "#22c55e" : "#ef4444",
  shape: t.side === "buy" ? "arrowUp" : "arrowDown",
  text: `${t.side.toUpperCase()} @ ${t.price}`
}));

candleSeries.setMarkers(markers);
```

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NAUTILUS_PATH` | Path to Parquet data catalog | `./data/catalog` |
| `DATABASE_URL` | PostgreSQL connection string | Required |

### Timeframe Mapping

| API Parameter | Nautilus bar_type_spec |
|---------------|------------------------|
| `1_MIN` | `1-MINUTE-LAST` |
| `5_MIN` | `5-MINUTE-LAST` |
| `15_MIN` | `15-MINUTE-LAST` |
| `1_HOUR` | `1-HOUR-LAST` |
| `1_DAY` | `1-DAY-LAST` |

---

## Troubleshooting

### "Market data not found"

1. Check data availability:
   ```bash
   uv run ntrader data list --symbol AAPL
   ```

2. Fetch missing data:
   ```bash
   uv run ntrader data fetch --symbol AAPL --start 2024-01-01 --end 2024-01-31
   ```

### "Backtest not found"

1. List available backtests:
   ```bash
   uv run ntrader backtest list
   ```

2. Verify run_id exists in database

### Performance Issues

- For large date ranges, response may take longer
- Target: <500ms for 100k candles
- Consider reducing date range if slow

---

## OpenAPI Documentation

Access interactive API docs at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## Next Steps

1. Run test suite to verify implementation
2. Check coverage meets 80% minimum
3. Test frontend integration with TradingView charts
4. Monitor performance against success criteria
