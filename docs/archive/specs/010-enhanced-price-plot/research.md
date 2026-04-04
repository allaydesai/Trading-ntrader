# Research: Enhanced Price Plot with Trade Markers and Indicators

**Feature**: 010-enhanced-price-plot
**Date**: 2025-01-27
**Phase**: 0 (Outline & Research)

## Overview

This document captures research findings for implementing enhanced price charts with trade markers and technical indicator overlays using TradingView Lightweight Charts library.

## Research Areas

### 1. TradingView Lightweight Charts - Trade Markers

**Decision**: Use `createSeries()` with `setMarkers()` API for trade entry/exit points

**Rationale**:
- Built-in marker support with customizable shapes, colors, and positions
- Markers automatically scale and reposition with zoom/pan operations
- Native tooltip support via marker text property
- Performance optimized for thousands of markers

**Key Findings**:
- Marker shapes: `circle`, `square`, `arrowUp`, `arrowDown` (we'll use `arrowUp`/`arrowDown` for buy/sell)
- Position: `aboveBar`, `belowBar`, `inBar` (we'll use `belowBar` for entries, `aboveBar` for exits)
- Colors: Customizable via `color` property (green for buy, red for sell)
- Text: Supports tooltip text via `text` property (we'll show price, quantity, P&L)

**Implementation Pattern**:
```javascript
const markers = trades.map(trade => ({
    time: trade.time,  // Unix timestamp in seconds
    position: trade.side === 'buy' ? 'belowBar' : 'aboveBar',
    color: trade.side === 'buy' ? '#26a69a' : '#ef5350',
    shape: trade.side === 'buy' ? 'arrowUp' : 'arrowDown',
    text: `${trade.side.toUpperCase()} @ ${trade.price}\nQty: ${trade.quantity}\nP&L: ${trade.pnl}`
}));

candlestickSeries.setMarkers(markers);
```

**Alternatives Considered**:
- Custom SVG overlays: Rejected due to complexity and poor performance with zoom/pan
- Separate series for markers: Rejected due to API limitations and poor visual alignment

**References**:
- TradingView Lightweight Charts Markers: https://tradingview.github.io/lightweight-charts/docs/markers
- Best Practices: https://tradingview.github.io/lightweight-charts/tutorials/how_to/markers

---

### 2. TradingView Lightweight Charts - Indicator Overlays

**Decision**: Use separate `LineSeries` for each indicator, with optional `PriceScaleMode` for indicators with different value ranges

**Rationale**:
- Each indicator (SMA, Bollinger Bands, RSI) rendered as independent line series
- SMA and Bollinger Bands overlay on price chart (same scale)
- RSI requires separate pane (different scale: 0-100 vs price in dollars)
- Line series support customizable colors, widths, and styles (solid, dashed)

**Key Findings**:

**SMA Indicators** (Fast SMA, Slow SMA):
```javascript
const fastSmaSeries = chart.addLineSeries({
    color: '#2962FF',  // Blue
    lineWidth: 2,
    title: 'Fast SMA (10)'
});

const slowSmaSeries = chart.addLineSeries({
    color: '#FF6D00',  // Orange
    lineWidth: 2,
    title: 'Slow SMA (20)'
});

// Data format: [{time: 1609459200, value: 150.25}, ...]
fastSmaSeries.setData(fastSmaData);
slowSmaSeries.setData(slowSmaData);
```

**Bollinger Bands** (Upper, Middle, Lower):
```javascript
const upperBandSeries = chart.addLineSeries({
    color: '#787B86',
    lineWidth: 1,
    lineStyle: 2,  // Dashed
    title: 'Upper Band'
});

const middleBandSeries = chart.addLineSeries({
    color: '#2962FF',
    lineWidth: 2,
    title: 'Middle Band (SMA)'
});

const lowerBandSeries = chart.addLineSeries({
    color: '#787B86',
    lineWidth: 1,
    lineStyle: 2,  // Dashed
    title: 'Lower Band'
});
```

**RSI Indicator** (Separate Pane):
```javascript
// Create separate chart instance for RSI pane
const rsiChart = LightweightCharts.createChart(document.getElementById('rsi-container'), {
    height: 150,
    layout: {
        backgroundColor: '#1e222d',
        textColor: '#d1d4dc',
    },
    grid: {
        vertLines: { color: '#2B2B43' },
        horzLines: { color: '#363C4E' },
    },
    rightPriceScale: {
        scaleMargins: {
            top: 0.1,
            bottom: 0.1,
        },
        borderVisible: false,
    },
    timeScale: {
        visible: false,  // Share time scale with main chart
        borderVisible: false,
    },
});

const rsiSeries = rsiChart.addLineSeries({
    color: '#9C27B0',
    lineWidth: 2,
    title: 'RSI (14)'
});

// Add threshold lines at 70 and 30
const overboughtSeries = rsiChart.addLineSeries({
    color: '#ef5350',
    lineWidth: 1,
    lineStyle: 2,  // Dashed
    priceLineVisible: false
});
overboughtSeries.setData(rsiData.map(d => ({time: d.time, value: 70})));

const oversoldSeries = rsiChart.addLineSeries({
    color: '#26a69a',
    lineWidth: 1,
    lineStyle: 2,
    priceLineVisible: false
});
oversoldSeries.setData(rsiData.map(d => ({time: d.time, value: 30})));
```

**Alternatives Considered**:
- Single multi-line series: Rejected due to lack of independent styling and legend support
- Canvas overlays: Rejected due to complexity and poor integration with chart pan/zoom
- Chart.js: Rejected as TradingView Lightweight Charts already integrated

**References**:
- TradingView Line Series: https://tradingview.github.io/lightweight-charts/docs/series-types#line
- Multiple Panes: https://tradingview.github.io/lightweight-charts/tutorials/how_to/multiple-panes
- Series Customization: https://tradingview.github.io/lightweight-charts/docs/api/interfaces/LineSeriesOptions

---

### 3. Performance Optimization for Large Datasets

**Decision**: Implement progressive data loading with client-side downsampling for zoomed-out views

**Rationale**:
- Charts with 100k+ bars can cause browser lag without optimization
- User typically zooms into specific time ranges for analysis
- TradingView Lightweight Charts handles rendering efficiently, but data loading is bottleneck

**Key Findings**:

**Data Downsampling Strategy**:
1. **Initial Load**: Fetch data at lower resolution (e.g., hourly bars instead of 1-minute for multi-year ranges)
2. **Zoom-In**: Detect zoom level via `timeScale().subscribeVisibleLogicalRangeChange()` callback
3. **Fetch Details**: Load higher resolution data for visible range only
4. **Cache**: Store fetched data in client-side Map to avoid redundant API calls

```javascript
let dataCache = new Map();  // key: timeframe, value: data array

chart.timeScale().subscribeVisibleLogicalRangeChange((logicalRange) => {
    if (logicalRange === null) return;

    const visibleBars = logicalRange.to - logicalRange.from;

    // Determine appropriate timeframe
    let timeframe;
    if (visibleBars > 1000) {
        timeframe = '1-hour';  // Zoomed out far
    } else if (visibleBars > 100) {
        timeframe = '5-minute';  // Medium zoom
    } else {
        timeframe = '1-minute';  // Zoomed in close
    }

    // Load data if not cached
    if (!dataCache.has(timeframe)) {
        fetchDataForTimeframe(timeframe).then(data => {
            dataCache.set(timeframe, data);
            candlestickSeries.setData(data);
        });
    }
});
```

**Marker Density Management**:
- For backtests with 1000+ trades, only render markers visible in current viewport
- Filter trades based on visible time range before calling `setMarkers()`

```javascript
function updateVisibleMarkers(visibleRange) {
    const visibleMarkers = allMarkers.filter(m =>
        m.time >= visibleRange.from && m.time <= visibleRange.to
    );
    candlestickSeries.setMarkers(visibleMarkers);
}

chart.timeScale().subscribeVisibleTimeRangeChange((timeRange) => {
    if (timeRange) {
        updateVisibleMarkers(timeRange);
    }
});
```

**Alternatives Considered**:
- Server-side downsampling: Rejected due to increased API complexity and response time
- WebWorkers for client processing: Deferred as P3 feature (not required for P1 scope)
- Virtual scrolling: Not applicable to chart library API

**References**:
- TradingView Performance Tips: https://tradingview.github.io/lightweight-charts/docs/performance
- Large Dataset Handling: https://github.com/tradingview/lightweight-charts/discussions/1089
- Visible Range API: https://tradingview.github.io/lightweight-charts/docs/time-scale

---

### 4. Indicator Data Format and API Integration

**Decision**: Fetch indicator data from existing `/api/indicators/{run_id}` endpoint, transform to TradingView format client-side

**Rationale**:
- Backend already provides indicator data in `config_snapshot.indicators` field
- API returns `{name: string, points: [{time: string, value: float}]}` structure
- Minor transformation needed: convert ISO timestamp strings to Unix seconds

**Key Findings**:

**API Response Format** (from specs/008-chart-apis):
```json
{
  "run_id": "uuid",
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

**Client-Side Transformation**:
```javascript
async function fetchIndicators(runId) {
    const response = await fetch(`/api/indicators/${runId}`);
    const data = await response.json();

    // Transform each indicator to TradingView format
    const transformed = {};
    for (const [name, points] of Object.entries(data.indicators)) {
        transformed[name] = points.map(p => ({
            time: Math.floor(new Date(p.time).getTime() / 1000),  // Convert to Unix seconds
            value: p.value
        }));
    }

    return transformed;
}
```

**Strategy-Specific Indicator Mapping**:
- **SMA Crossover**: `fast_sma`, `slow_sma` → overlay on price chart
- **RSI Mean Reversion**: `rsi`, `sma_trend` → RSI in separate pane, SMA overlay
- **Bollinger Reversal**: `upper_band`, `middle_band`, `lower_band` → overlay on price chart
- **SMA Momentum**: `fast_sma`, `slow_sma` → overlay on price chart

**Alternatives Considered**:
- Pre-transform on backend: Rejected to keep API format-agnostic
- Store indicators in separate database table: Rejected as current JSONB storage is sufficient

---

### 5. Chart Legend and Visibility Controls

**Decision**: Use TradingView built-in legend with custom toggle controls for indicator visibility

**Rationale**:
- TradingView Lightweight Charts provides built-in legend via series `title` property
- For User Story 2 (P1), basic visibility toggles required for each indicator
- Advanced controls (hide all, reset view) deferred to User Story 4 (P3)

**Key Findings**:

**Built-in Legend** (Automatic):
```javascript
const fastSmaSeries = chart.addLineSeries({
    color: '#2962FF',
    lineWidth: 2,
    title: 'Fast SMA (10)',  // Appears in legend automatically
});
```

**Basic Visibility Toggles** (P1 Implementation):
```html
<div class="indicator-controls">
    <label>
        <input type="checkbox" id="toggle-fast-sma" checked>
        <span style="color: #2962FF;">Fast SMA (10)</span>
    </label>
    <label>
        <input type="checkbox" id="toggle-slow-sma" checked>
        <span style="color: #FF6D00;">Slow SMA (20)</span>
    </label>
</div>

<script>
document.getElementById('toggle-fast-sma').addEventListener('change', (e) => {
    fastSmaSeries.applyOptions({
        visible: e.target.checked
    });
});
</script>
```

**Alternatives Considered**:
- Custom legend HTML overlay: Rejected due to complexity and maintenance burden
- Hide/show via `removeSeries()` and re-add: Rejected due to performance impact

**References**:
- Series Visibility: https://tradingview.github.io/lightweight-charts/docs/api/interfaces/SeriesOptionsCommon#visible

---

## Summary of Key Decisions

| Area | Decision | Rationale |
|------|----------|-----------|
| Trade Markers | Use `setMarkers()` API with arrow shapes | Built-in support, good performance, native tooltips |
| Indicator Overlays | Separate `LineSeries` per indicator | Independent styling, built-in legend support |
| RSI Pane | Separate chart instance | Different value scale (0-100 vs price) |
| Performance | Progressive loading + viewport filtering | Smooth UX with large datasets (100k bars, 1000 trades) |
| Data Format | Client-side transformation from API | Keep backend format-agnostic, simple ISO → Unix conversion |
| Legend | Built-in with manual toggles | Minimal code, meets P1 requirements |

## Open Questions (None)

All technical decisions finalized. No remaining unknowns for P1 scope (User Stories 1-2).

---

## Next Steps

Proceed to Phase 1:
1. Generate `data-model.md` - Define chart entities and rendering logic
2. Verify API contracts in `contracts/` (already exist from specs/008)
3. Generate `quickstart.md` - Developer guide for implementing chart enhancements
