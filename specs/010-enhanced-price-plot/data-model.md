# Data Model: Enhanced Price Plot

**Feature**: 010-enhanced-price-plot
**Date**: 2025-01-27
**Phase**: 1 (Design & Contracts)

## Overview

This document defines the data entities, transformations, and rendering logic for the enhanced price chart with trade markers and indicator overlays.

## Core Entities

### 1. Chart Configuration

**Purpose**: Encapsulates all settings and data required to initialize and render the enhanced chart

**Properties**:
```typescript
interface ChartConfiguration {
    // Chart container
    containerId: string;              // DOM element ID for chart mount
    height: number;                   // Chart height in pixels (default: 600)

    // Data sources
    runId: string;                    // Backtest run UUID
    symbol: string;                   // Trading symbol (e.g., "AAPL")
    startDate: string;                // ISO 8601 date
    endDate: string;                  // ISO 8601 date
    timeframe: string;                // "1-minute", "5-minute", "1-hour", "1-day"

    // Feature flags
    showTradeMarkers: boolean;        // Enable/disable trade markers (default: true)
    showIndicators: boolean;          // Enable/disable indicator overlays (default: true)
    enableTooltips: boolean;          // Enable marker tooltips (default: true)
}
```

**Validation Rules**:
- `containerId` must reference existing DOM element
- `runId` must be valid UUID
- `startDate` < `endDate`
- `timeframe` must match API enum values

**Example**:
```javascript
const config = {
    containerId: 'chart-container',
    height: 600,
    runId: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    symbol: 'AAPL',
    startDate: '2024-01-02',
    endDate: '2024-01-05',
    timeframe: '1-minute',
    showTradeMarkers: true,
    showIndicators: true,
    enableTooltips: true
};
```

---

### 2. Trade Marker

**Purpose**: Visual representation of a trade entry or exit on the chart

**Properties**:
```typescript
interface TradeMarker {
    time: number;                     // Unix timestamp in seconds
    side: 'buy' | 'sell';             // Trade direction
    price: number;                    // Execution price
    quantity: number;                 // Trade quantity (shares)
    pnl: number;                      // Profit/loss (0 for entries, realized P&L for exits)

    // Rendering properties (derived)
    position: 'aboveBar' | 'belowBar'; // Visual position
    color: string;                     // Hex color code
    shape: 'arrowUp' | 'arrowDown';    // Marker shape
    text: string;                      // Tooltip text
}
```

**Derivation Logic**:
```javascript
function createTradeMarker(trade) {
    const isBuy = trade.side === 'buy';

    return {
        time: trade.time,
        side: trade.side,
        price: trade.price,
        quantity: trade.quantity,
        pnl: trade.pnl,

        // Derived properties
        position: isBuy ? 'belowBar' : 'aboveBar',
        color: isBuy ? '#26a69a' : '#ef5350',  // Green for buy, red for sell
        shape: isBuy ? 'arrowUp' : 'arrowDown',
        text: formatTooltip(trade)
    };
}

function formatTooltip(trade) {
    const side = trade.side.toUpperCase();
    const pnlText = trade.pnl !== 0 ? `\nP&L: ${formatCurrency(trade.pnl)}` : '';
    return `${side} @ ${formatPrice(trade.price)}\nQty: ${trade.quantity}${pnlText}`;
}
```

**State Transitions**: None (immutable after creation)

**Validation Rules**:
- `time` must be within chart time range
- `price` must be positive
- `quantity` must be positive integer
- `pnl` can be positive, negative, or zero

---

### 3. Indicator Series

**Purpose**: Time-series data for a technical indicator with display properties

**Properties**:
```typescript
interface IndicatorSeries {
    name: string;                     // Indicator identifier (e.g., "fast_sma")
    displayName: string;              // Human-readable name (e.g., "Fast SMA (10)")
    type: IndicatorType;              // Indicator category
    color: string;                    // Line color (hex code)
    lineWidth: number;                // Line thickness in pixels
    lineStyle: number;                // 0=solid, 1=dotted, 2=dashed
    data: IndicatorPoint[];           // Time-series data
    pane: 'main' | 'rsi';             // Chart pane location
    visible: boolean;                 // Visibility toggle state
}

type IndicatorType = 'sma' | 'bollinger' | 'rsi' | 'threshold';

interface IndicatorPoint {
    time: number;                     // Unix timestamp in seconds
    value: number;                    // Indicator value
}
```

**Indicator Type Definitions**:

**SMA (Simple Moving Average)**:
```javascript
{
    name: 'fast_sma',
    displayName: 'Fast SMA (10)',
    type: 'sma',
    color: '#2962FF',
    lineWidth: 2,
    lineStyle: 0,  // Solid
    pane: 'main',
    visible: true
}
```

**Bollinger Bands** (3 series):
```javascript
// Upper Band
{
    name: 'upper_band',
    displayName: 'Upper Band',
    type: 'bollinger',
    color: '#787B86',
    lineWidth: 1,
    lineStyle: 2,  // Dashed
    pane: 'main',
    visible: true
}

// Middle Band (SMA)
{
    name: 'middle_band',
    displayName: 'Middle Band',
    type: 'bollinger',
    color: '#2962FF',
    lineWidth: 2,
    lineStyle: 0,  // Solid
    pane: 'main',
    visible: true
}

// Lower Band
{
    name: 'lower_band',
    displayName: 'Lower Band',
    type: 'bollinger',
    color: '#787B86',
    lineWidth: 1,
    lineStyle: 2,  // Dashed
    pane: 'main',
    visible: true
}
```

**RSI (Relative Strength Index)**:
```javascript
// RSI Line
{
    name: 'rsi',
    displayName: 'RSI (14)',
    type: 'rsi',
    color: '#9C27B0',
    lineWidth: 2,
    lineStyle: 0,
    pane: 'rsi',
    visible: true
}

// Overbought Threshold (70)
{
    name: 'rsi_overbought',
    displayName: 'Overbought (70)',
    type: 'threshold',
    color: '#ef5350',
    lineWidth: 1,
    lineStyle: 2,
    pane: 'rsi',
    visible: true
}

// Oversold Threshold (30)
{
    name: 'rsi_oversold',
    displayName: 'Oversold (30)',
    type: 'threshold',
    color: '#26a69a',
    lineWidth: 1,
    lineStyle: 2,
    pane: 'rsi',
    visible: true
}
```

**State Transitions**:
- `visible`: true ↔ false (via toggle controls)

**Validation Rules**:
- `data` array must be sorted by `time` ascending
- `data` points must have `time` within chart range
- `value` must be finite number
- For RSI indicators, `value` must be in range [0, 100]

---

### 4. Chart Pane

**Purpose**: Container for price chart or indicators with independent scaling

**Properties**:
```typescript
interface ChartPane {
    id: string;                       // Pane identifier ('main' or 'rsi')
    height: number;                   // Pane height in pixels
    series: IndicatorSeries[];        // Indicators rendered in this pane
    priceScale: PriceScaleConfig;     // Y-axis configuration
}

interface PriceScaleConfig {
    scaleMargins: {
        top: number;                  // Top margin (0.0-1.0)
        bottom: number;               // Bottom margin (0.0-1.0)
    };
    mode: 'normal' | 'logarithmic';   // Price scale mode
    borderVisible: boolean;           // Show pane border
}
```

**Pane Configurations**:

**Main Pane** (Price + SMA/Bollinger):
```javascript
{
    id: 'main',
    height: 600,
    series: [/* SMA/Bollinger series */],
    priceScale: {
        scaleMargins: {
            top: 0.1,
            bottom: 0.2
        },
        mode: 'normal',
        borderVisible: false
    }
}
```

**RSI Pane** (RSI indicator):
```javascript
{
    id: 'rsi',
    height: 150,
    series: [/* RSI series */],
    priceScale: {
        scaleMargins: {
            top: 0.1,
            bottom: 0.1
        },
        mode: 'normal',  // Fixed range 0-100
        borderVisible: false
    }
}
```

---

## Data Transformations

### API Response → Chart Data

**Input**: `/api/timeseries` response
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

**Output**: TradingView candlestick data (no transformation needed - API already in correct format)

---

**Input**: `/api/trades/{run_id}` response
```json
{
  "run_id": "uuid",
  "trade_count": 2,
  "trades": [
    {
      "time": "2024-01-02T09:30:00Z",
      "side": "buy",
      "price": 150.25,
      "quantity": 100,
      "pnl": 0
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

**Transformation**:
```javascript
function transformTrades(apiResponse) {
    return apiResponse.trades.map(trade => createTradeMarker({
        time: Math.floor(new Date(trade.time).getTime() / 1000),  // ISO string → Unix seconds
        side: trade.side,
        price: trade.price,
        quantity: trade.quantity,
        pnl: trade.pnl
    }));
}
```

---

**Input**: `/api/indicators/{run_id}` response
```json
{
  "run_id": "uuid",
  "indicators": {
    "fast_sma": [
      {"time": "2024-01-02T09:30:00Z", "value": 150.25}
    ],
    "slow_sma": [
      {"time": "2024-01-02T09:30:00Z", "value": 149.80}
    ]
  }
}
```

**Transformation**:
```javascript
function transformIndicators(apiResponse, strategyName) {
    const indicatorConfig = getIndicatorConfig(strategyName);
    const series = [];

    for (const [name, points] of Object.entries(apiResponse.indicators)) {
        const config = indicatorConfig[name];
        if (!config) continue;  // Unknown indicator

        series.push({
            ...config,  // Pre-defined display properties
            data: points.map(p => ({
                time: Math.floor(new Date(p.time).getTime() / 1000),
                value: p.value
            }))
        });
    }

    return series;
}

function getIndicatorConfig(strategyName) {
    const configs = {
        'SMA Crossover': {
            'fast_sma': {
                name: 'fast_sma',
                displayName: 'Fast SMA (10)',
                type: 'sma',
                color: '#2962FF',
                lineWidth: 2,
                lineStyle: 0,
                pane: 'main',
                visible: true
            },
            'slow_sma': {
                name: 'slow_sma',
                displayName: 'Slow SMA (20)',
                type: 'sma',
                color: '#FF6D00',
                lineWidth: 2,
                lineStyle: 0,
                pane: 'main',
                visible: true
            }
        },
        // ... other strategy configs
    };

    return configs[strategyName] || {};
}
```

---

## Rendering Logic

### Chart Initialization Sequence

```
1. Parse ChartConfiguration
2. Create main chart instance
   → Apply TradingView options (theme, layout, grid)
   → Mount to DOM container
3. Add candlestick series
   → Fetch OHLCV data from /api/timeseries
   → Set candlestick data
4. Render trade markers (if enabled)
   → Fetch trades from /api/trades/{run_id}
   → Transform to TradeMarker objects
   → Call candlestickSeries.setMarkers()
5. Render indicators (if enabled)
   → Fetch indicators from /api/indicators/{run_id}
   → Transform to IndicatorSeries objects
   → For each indicator:
      - If pane='main': add to main chart
      - If pane='rsi': create RSI pane, add to RSI chart
6. Attach event listeners
   → Subscribe to visible range changes
   → Subscribe to toggle controls
7. Initial time scale adjustment
   → Fit content to visible range
```

### Marker Clustering (Performance Optimization)

**When**: More than 1000 trade markers

**Logic**:
```javascript
function clusterMarkers(markers, maxMarkers = 1000) {
    if (markers.length <= maxMarkers) return markers;

    // Sample evenly distributed markers
    const step = Math.ceil(markers.length / maxMarkers);
    return markers.filter((_, index) => index % step === 0);
}
```

**Alternative**: Filter by visible time range (implemented via `subscribeVisibleTimeRangeChange`)

---

## Strategy-Specific Indicator Configurations

### SMA Crossover

**Indicators**: Fast SMA, Slow SMA
**Panes**: Main only
**Visualization**: 2 line series overlaid on price chart

### RSI Mean Reversion

**Indicators**: RSI, SMA Trend, Overbought/Oversold thresholds
**Panes**: Main (SMA), RSI (RSI + thresholds)
**Visualization**:
- Main pane: SMA overlay
- RSI pane: RSI line + 2 threshold lines

### Bollinger Reversal

**Indicators**: Upper Band, Middle Band (SMA), Lower Band
**Panes**: Main only
**Visualization**: 3 line series (middle solid, bands dashed)

### SMA Momentum

**Indicators**: Fast SMA, Slow SMA
**Panes**: Main only
**Visualization**: Same as SMA Crossover

---

## Error Handling

### Missing Data Scenarios

| Scenario | Behavior |
|----------|----------|
| No OHLCV data | Display error message: "No price data available for selected date range" |
| No trades | Chart renders without markers (normal for backtests with no trades) |
| No indicators | Chart renders without indicator overlays (strategy may not use indicators) |
| API error | Display error: "Failed to load chart data. Please refresh the page." |

### Data Validation

```javascript
function validateChartData(ohlcv, trades, indicators) {
    const errors = [];

    // OHLCV required
    if (!ohlcv || ohlcv.length === 0) {
        errors.push('OHLCV data is required');
    }

    // Validate trade markers
    if (trades) {
        trades.forEach((trade, i) => {
            if (trade.price <= 0) errors.push(`Trade ${i}: Invalid price`);
            if (trade.quantity <= 0) errors.push(`Trade ${i}: Invalid quantity`);
        });
    }

    // Validate indicator data
    if (indicators) {
        indicators.forEach(ind => {
            if (ind.type === 'rsi') {
                ind.data.forEach((point, i) => {
                    if (point.value < 0 || point.value > 100) {
                        errors.push(`${ind.name} point ${i}: RSI value out of range [0, 100]`);
                    }
                });
            }
        });
    }

    return errors;
}
```

---

## Summary

### Entity Relationships

```
ChartConfiguration
    └─> Main ChartPane
        ├─> Candlestick Series (OHLCV data)
        ├─> Trade Markers (buy/sell arrows)
        └─> Indicator Series (SMA, Bollinger)

    └─> RSI ChartPane (if strategy uses RSI)
        ├─> RSI Series
        ├─> Overbought Threshold Line
        └─> Oversold Threshold Line
```

### Data Flow

```
API Responses → Transformations → TradingView Entities → Rendered Chart
    /timeseries → (no transform) → Candlestick Data
    /trades → ISO→Unix + metadata → Trade Markers
    /indicators → ISO→Unix + config → Indicator Series
```

### Key Constraints

- All timestamps in Unix seconds (TradingView format)
- Indicator data sorted by time ascending
- RSI values must be [0, 100]
- Trade markers filtered by visible range for performance
- Maximum 1000 visible markers to prevent browser lag

---

## Next Steps

1. Verify API contracts in `contracts/` directory
2. Generate `quickstart.md` with implementation examples
3. Proceed to Phase 2: Generate `tasks.md` via `/speckit.tasks` command
