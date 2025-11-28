# Quickstart Guide: Enhanced Price Plot Implementation

**Feature**: 010-enhanced-price-plot
**Date**: 2025-01-27
**Target Audience**: Frontend developers implementing chart enhancements

## Overview

This guide walks through implementing the enhanced price chart with trade markers and indicator overlays for the backtest detail page. By the end, you'll have a fully functional chart showing OHLCV data, trade entry/exit points, and technical indicators.

## Prerequisites

- TradingView Lightweight Charts library (already included via CDN)
- Existing backtest detail page at `/backtests/{backtest_id}`
- Working API endpoints: `/api/timeseries`, `/api/trades/{run_id}`, `/api/indicators/{run_id}`
- Basic understanding of JavaScript ES6+ and async/await

## Implementation Steps

### Step 1: Create Chart JavaScript Module

**File**: `src/static/js/chart-enhanced.js`

This module will handle all chart rendering logic.

```javascript
/**
 * Enhanced Chart Module for NTrader Backtest Detail Page
 *
 * Renders TradingView Lightweight Charts with:
 * - OHLCV candlestick data
 * - Trade entry/exit markers
 * - Technical indicator overlays (SMA, Bollinger Bands, RSI)
 */

/**
 * Initialize and render the enhanced chart
 *
 * @param {Object} config - Chart configuration
 * @param {string} config.containerId - DOM element ID for chart
 * @param {string} config.runId - Backtest run UUID
 * @param {string} config.symbol - Trading symbol
 * @param {string} config.strategyName - Strategy name for indicator config
 * @param {string} config.startDate - Start date (YYYY-MM-DD)
 * @param {string} config.endDate - End date (YYYY-MM-DD)
 * @param {string} config.timeframe - Bar timeframe (default: "1-minute")
 * @param {boolean} config.showTradeMarkers - Show trade markers (default: true)
 * @param {boolean} config.showIndicators - Show indicators (default: true)
 */
async function initializeEnhancedChart(config) {
    try {
        // 1. Create chart instance
        const chart = createChartInstance(config.containerId);

        // 2. Add candlestick series
        const candlestickSeries = chart.addCandlestickSeries({
            upColor: '#26a69a',
            downColor: '#ef5350',
            borderVisible: false,
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350',
        });

        // 3. Fetch and render OHLCV data
        const ohlcvData = await fetchOHLCVData(config);
        if (!ohlcvData || ohlcvData.candles.length === 0) {
            displayError('No price data available for selected date range');
            return;
        }
        candlestickSeries.setData(ohlcvData.candles);

        // 4. Fetch and render trade markers (if enabled)
        if (config.showTradeMarkers) {
            const markers = await fetchTradeMarkers(config.runId);
            if (markers && markers.length > 0) {
                candlestickSeries.setMarkers(markers);
            }
        }

        // 5. Fetch and render indicators (if enabled)
        if (config.showIndicators) {
            const indicators = await fetchIndicators(config.runId, config.strategyName);
            renderIndicators(chart, indicators);
        }

        // 6. Adjust time scale to fit content
        chart.timeScale().fitContent();

        // 7. Store chart instance for later use
        window.backtestChart = chart;

    } catch (error) {
        console.error('Failed to initialize chart:', error);
        displayError(`Chart initialization failed: ${error.message}`);
    }
}

/**
 * Create TradingView Lightweight Charts instance
 */
function createChartInstance(containerId) {
    const container = document.getElementById(containerId);

    if (!container) {
        throw new Error(`Chart container element '${containerId}' not found`);
    }

    return LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 600,
        layout: {
            backgroundColor: '#1e222d',
            textColor: '#d1d4dc',
        },
        grid: {
            vertLines: { color: '#2B2B43' },
            horzLines: { color: '#363C4E' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: '#485c7b',
        },
        timeScale: {
            borderColor: '#485c7b',
            timeVisible: true,
            secondsVisible: false,
        },
    });
}

/**
 * Fetch OHLCV candlestick data from API
 */
async function fetchOHLCVData(config) {
    const url = `/api/timeseries?symbol=${config.symbol}&start=${config.startDate}&end=${config.endDate}&timeframe=${config.timeframe || '1-minute'}`;

    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Failed to fetch OHLCV data: ${response.statusText}`);
    }

    return await response.json();
}

/**
 * Fetch and transform trade markers
 */
async function fetchTradeMarkers(runId) {
    const url = `/api/trades/${runId}`;

    const response = await fetch(url);
    if (!response.ok) {
        console.warn('No trades found for backtest');
        return [];
    }

    const data = await response.json();

    // Transform trades to TradingView markers
    return data.trades.map(trade => ({
        time: isoToUnix(trade.time),
        position: trade.side === 'buy' ? 'belowBar' : 'aboveBar',
        color: trade.side === 'buy' ? '#26a69a' : '#ef5350',
        shape: trade.side === 'buy' ? 'arrowUp' : 'arrowDown',
        text: formatTradeTooltip(trade),
    }));
}

/**
 * Format trade tooltip text
 */
function formatTradeTooltip(trade) {
    const side = trade.side.toUpperCase();
    const price = trade.price.toFixed(2);
    const pnlText = trade.pnl !== 0 ? `\\nP&L: $${trade.pnl.toFixed(2)}` : '';

    return `${side} @ $${price}\\nQty: ${trade.quantity}${pnlText}`;
}

/**
 * Fetch indicator data from API
 */
async function fetchIndicators(runId, strategyName) {
    const url = `/api/indicators/${runId}`;

    const response = await fetch(url);
    if (!response.ok) {
        console.warn('No indicators found for backtest');
        return {};
    }

    const data = await response.json();

    // Transform and configure indicators based on strategy
    return transformIndicators(data.indicators, strategyName);
}

/**
 * Transform API indicator data to chart format with strategy-specific config
 */
function transformIndicators(apiIndicators, strategyName) {
    const config = getIndicatorConfig(strategyName);
    const indicators = {};

    for (const [name, points] of Object.entries(apiIndicators)) {
        const indicatorConfig = config[name];
        if (!indicatorConfig) continue;

        indicators[name] = {
            ...indicatorConfig,
            data: points.map(p => ({
                time: isoToUnix(p.time),
                value: p.value,
            })),
        };
    }

    return indicators;
}

/**
 * Get indicator display configuration for strategy
 */
function getIndicatorConfig(strategyName) {
    const configs = {
        'SMA Crossover': {
            'fast_sma': {
                displayName: 'Fast SMA',
                color: '#2962FF',
                lineWidth: 2,
                pane: 'main',
            },
            'slow_sma': {
                displayName: 'Slow SMA',
                color: '#FF6D00',
                lineWidth: 2,
                pane: 'main',
            },
        },
        'Bollinger Reversal': {
            'upper_band': {
                displayName: 'Upper Band',
                color: '#787B86',
                lineWidth: 1,
                lineStyle: 2,  // Dashed
                pane: 'main',
            },
            'middle_band': {
                displayName: 'Middle Band',
                color: '#2962FF',
                lineWidth: 2,
                pane: 'main',
            },
            'lower_band': {
                displayName: 'Lower Band',
                color: '#787B86',
                lineWidth: 1,
                lineStyle: 2,  // Dashed
                pane: 'main',
            },
        },
        'RSI Mean Reversion': {
            'rsi': {
                displayName: 'RSI (14)',
                color: '#9C27B0',
                lineWidth: 2,
                pane: 'rsi',
            },
            'sma_trend': {
                displayName: 'SMA Trend',
                color: '#2962FF',
                lineWidth: 2,
                pane: 'main',
            },
        },
        // Add SMA Momentum config if needed
    };

    return configs[strategyName] || {};
}

/**
 * Render indicator series on chart
 */
function renderIndicators(chart, indicators) {
    for (const [name, indicator] of Object.entries(indicators)) {
        if (indicator.pane === 'main') {
            // Add to main chart
            const series = chart.addLineSeries({
                color: indicator.color,
                lineWidth: indicator.lineWidth,
                lineStyle: indicator.lineStyle || 0,  // 0=solid, 2=dashed
                title: indicator.displayName,
            });
            series.setData(indicator.data);
        }
        // RSI pane handling deferred to future implementation
    }
}

/**
 * Convert ISO 8601 timestamp to Unix seconds
 */
function isoToUnix(isoString) {
    return Math.floor(new Date(isoString).getTime() / 1000);
}

/**
 * Display error message to user
 */
function displayError(message) {
    const container = document.getElementById('chart-container');
    if (container) {
        container.innerHTML = `
            <div class="alert alert-error">
                <p>${message}</p>
            </div>
        `;
    }
}

// Export for use in template
window.initializeEnhancedChart = initializeEnhancedChart;
```

### Step 2: Update Backtest Detail Template

**File**: `src/templates/backtests/detail.html`

Add chart container and initialization script.

```html
{% extends "base.html" %}

{% block content %}
<div class="container mx-auto px-4 py-8">
    <!-- Existing backtest metadata -->
    <div class="backtest-metadata mb-8">
        <!-- Strategy, dates, performance summary, etc. -->
    </div>

    <!-- Enhanced Chart Section -->
    <div class="chart-section bg-slate-800 rounded-lg p-6 mb-8">
        <h2 class="text-2xl font-bold text-white mb-4">Price Chart</h2>

        <!-- Chart Container -->
        <div id="chart-container" class="w-full"></div>

        <!-- Indicator Toggle Controls (P1 - Basic toggles) -->
        <div id="indicator-controls" class="mt-4 flex gap-4">
            <!-- Dynamically populated based on strategy -->
        </div>
    </div>

    <!-- Existing trade list, statistics, etc. -->
</div>

<!-- TradingView Lightweight Charts Library -->
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>

<!-- Enhanced Chart Module -->
<script src="{{ url_for('static', path='/js/chart-enhanced.js') }}"></script>

<!-- Chart Initialization -->
<script>
document.addEventListener('DOMContentLoaded', function() {
    initializeEnhancedChart({
        containerId: 'chart-container',
        runId: '{{ backtest.run_id }}',
        symbol: '{{ backtest.instrument_symbol }}',
        strategyName: '{{ backtest.strategy_name }}',
        startDate: '{{ backtest.start_date }}',
        endDate: '{{ backtest.end_date }}',
        timeframe: '1-minute',
        showTradeMarkers: true,
        showIndicators: true,
    });
});
</script>
{% endblock %}
```

### Step 3: Add Static File Route

**File**: `src/api/web.py`

Ensure static file serving is configured (should already exist):

```python
from fastapi.staticfiles import StaticFiles

app.mount("/static", StaticFiles(directory="src/static"), name="static")
```

### Step 4: Test the Implementation

**Manual Testing**:
1. Start the web server: `uvicorn src.api.web:app --reload`
2. Navigate to a backtest detail page (e.g., `/backtests/123`)
3. Verify chart renders with:
   - ✅ OHLCV candlesticks
   - ✅ Trade markers (green arrows for buy, red for sell)
   - ✅ Indicator lines overlaid on price chart
   - ✅ Tooltips appear on marker hover

**Playwright Integration Tests** (to be added):
```python
# tests/integration/ui/test_enhanced_chart.py

import pytest
from playwright.sync_api import Page, expect

def test_chart_renders_with_ohlcv_data(page: Page, backtest_id: int):
    """Test that chart renders OHLCV candlesticks"""
    page.goto(f"http://localhost:8000/backtests/{backtest_id}")

    # Wait for chart to render
    chart_container = page.locator('#chart-container')
    expect(chart_container).to_be_visible()

    # Check for canvas element (TradingView chart)
    canvas = chart_container.locator('canvas')
    expect(canvas).to_be_visible()

def test_trade_markers_display(page: Page, backtest_with_trades: int):
    """Test that trade markers appear on chart"""
    page.goto(f"http://localhost:8000/backtests/{backtest_with_trades}")

    # Wait for chart and markers
    page.wait_for_selector('#chart-container canvas')

    # Verify markers exist in DOM
    # (TradingView renders markers as SVG or Canvas elements)
    # Exact validation depends on library internals
```

---

## Common Patterns

### Pattern 1: Adding a New Indicator Type

**Example**: Adding MACD indicator

1. **Update Indicator Config**:
```javascript
const configs = {
    'MACD Strategy': {
        'macd_line': {
            displayName: 'MACD Line',
            color: '#2962FF',
            lineWidth: 2,
            pane: 'macd',  // Separate pane
        },
        'signal_line': {
            displayName: 'Signal Line',
            color: '#FF6D00',
            lineWidth: 2,
            pane: 'macd',
        },
    },
};
```

2. **Handle New Pane Type**:
```javascript
function renderIndicators(chart, indicators) {
    const macdPane = null;  // Will be created if needed

    for (const [name, indicator] of Object.entries(indicators)) {
        if (indicator.pane === 'macd') {
            // Create MACD pane if doesn't exist
            if (!macdPane) {
                macdPane = createMACDPane();
            }
            const series = macdPane.addLineSeries({/*...*/});
            series.setData(indicator.data);
        }
        // ... existing code
    }
}
```

### Pattern 2: Optimizing for Large Datasets

**Problem**: Chart lags with 100k+ bars

**Solution**: Implement downsampling

```javascript
function downsampleData(data, maxPoints = 10000) {
    if (data.length <= maxPoints) return data;

    const step = Math.ceil(data.length / maxPoints);
    return data.filter((_, index) => index % step === 0);
}

// Apply before setting data
const downsampled = downsampleData(ohlcvData.candles);
candlestickSeries.setData(downsampled);
```

### Pattern 3: Responsive Chart Sizing

**Handle Window Resize**:
```javascript
window.addEventListener('resize', () => {
    const container = document.getElementById('chart-container');
    if (window.backtestChart && container) {
        window.backtestChart.applyOptions({
            width: container.clientWidth
        });
    }
});
```

---

## Troubleshooting

### Issue: Chart doesn't render

**Cause**: Container element not found or has zero dimensions

**Fix**:
- Ensure `<div id="chart-container">` exists in DOM
- Give container explicit height: `<div id="chart-container" style="height: 600px;">`
- Check browser console for errors

### Issue: Markers don't appear

**Cause**: Timestamp format mismatch (ISO string vs Unix seconds)

**Fix**:
- Verify `isoToUnix()` transformation is applied to all trade times
- Check API response format matches expected schema

### Issue: Indicators not showing

**Cause**: Strategy name mismatch or no indicator config

**Fix**:
- Ensure `config.strategyName` matches key in `getIndicatorConfig()`
- Check API returns non-empty `indicators` object
- Verify indicator names match between API response and config

---

## Next Steps

**P1 Implementation** (Current Scope):
1. ✅ Render OHLCV candlesticks
2. ✅ Display trade markers with tooltips
3. ✅ Overlay indicators (SMA, Bollinger Bands)
4. ✅ Basic visibility toggles for indicators

**P2 Features** (Deferred):
- RSI separate pane implementation
- Marker clustering for dense trade datasets
- Progressive data loading for large date ranges

**P3 Features** (Future):
- Advanced visibility controls (hide all, reset view)
- Chart drawing tools
- Custom indicator creation

---

## Resources

- **TradingView Lightweight Charts Docs**: https://tradingview.github.io/lightweight-charts/
- **Spec 008**: Chart APIs implementation details
- **data-model.md**: Complete entity definitions
- **research.md**: Technical decisions and patterns

---

**Implementation Time Estimate**: 4-6 hours for core functionality (P1 scope)

**Testing Time Estimate**: 2-3 hours for Playwright integration tests

**Total**: ~8 hours for complete P1 feature delivery
