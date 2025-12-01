# Quickstart Guide: Enhanced Price Plot with Trade Markers

**Feature**: 010-enhanced-price-plot
**Date**: 2025-01-27
**Updated**: 2025-01-30
**Target Audience**: Frontend developers implementing chart enhancements

## Overview

This guide walks through implementing the enhanced price chart with trade markers for the backtest detail page. By the end, you'll have a fully functional chart showing OHLCV candlesticks with trade entry/exit markers and tooltips.

**Current Scope**: Trade markers only (User Story 1)
**Deferred**: Indicator overlays (SMA, Bollinger, RSI) - use external charting tools

## Prerequisites

- TradingView Lightweight Charts library (already included via CDN)
- Existing backtest detail page at `/backtests/{backtest_id}`
- Working API endpoints: `/api/timeseries`, `/api/trades/{run_id}`
- Basic understanding of JavaScript ES6+ and async/await

## Architecture

The chart system is modular, with each file under 500 lines:

```
static/js/
├── charts-core.js      # Core utilities, theme, shared functions
├── charts-price.js     # Price chart with trade markers
├── charts-equity.js    # Equity curve charts
├── charts-statistics.js # Trade statistics and drawdown metrics
└── charts.js           # Main orchestrator (entry point)
```

**Load order is important** - dependencies must load before dependent modules.

## Implementation Guide

### Step 1: Chart Container (HTML Template)

The backtest detail template needs a chart container with data attributes:

```html
<!-- Price Chart Container -->
<div id="price-chart-container"
     data-chart="run-price"
     data-run-id="{{ backtest.id }}"
     data-symbol="{{ backtest.instrument_symbol }}"
     data-start="{{ backtest.start_date }}"
     data-end="{{ backtest.end_date }}"
     class="w-full h-96">
    <div class="chart-loading flex items-center justify-center h-full">
        <span class="text-slate-400">Loading chart...</span>
    </div>
</div>
```

**Required data attributes**:
- `data-chart="run-price"` - Chart type identifier
- `data-run-id` - Backtest run UUID for trade marker API
- `data-symbol` - Trading symbol for timeseries API
- `data-start` - Start date (YYYY-MM-DD)
- `data-end` - End date (YYYY-MM-DD)

### Step 2: Script Loading (Base Template)

Scripts must load in the correct order:

```html
<!-- TradingView Lightweight Charts v5 -->
<script src="https://unpkg.com/lightweight-charts@5.0.0/dist/lightweight-charts.standalone.production.js" defer></script>

<!-- Chart modules (load in order) -->
<script src="{{ url_for('static', path='js/charts-core.js') }}" defer></script>
<script src="{{ url_for('static', path='js/charts-price.js') }}" defer></script>
<script src="{{ url_for('static', path='js/charts-equity.js') }}" defer></script>
<script src="{{ url_for('static', path='js/charts-statistics.js') }}" defer></script>
<script src="{{ url_for('static', path='js/charts.js') }}" defer></script>
```

### Step 3: Trade Marker API Response

The `/api/trades/{run_id}` endpoint returns trades in this format:

```json
{
  "trades": [
    {
      "time": 1704067200,
      "side": "buy",
      "price": 150.25,
      "quantity": 100,
      "pnl": 0
    },
    {
      "time": 1704153600,
      "side": "sell",
      "price": 155.50,
      "quantity": 100,
      "pnl": 525.00
    }
  ]
}
```

**Fields**:
- `time` - Unix timestamp (seconds)
- `side` - "buy" or "sell"
- `price` - Execution price
- `quantity` - Trade quantity
- `pnl` - Profit/loss (0 for entries, calculated for exits)

### Step 4: Trade Marker Rendering

Trade markers are transformed to TradingView format in `charts-price.js`:

```javascript
function createTradeMarkers(trades) {
    return trades.map((t) => ({
        time: t.time,
        position: t.side === "buy" ? "belowBar" : "aboveBar",
        color: t.side === "buy" ? CHART_COLORS.bullish : CHART_COLORS.bearish,
        shape: t.side === "buy" ? "arrowUp" : "arrowDown",
        text: formatTradeTooltip(t),
    }));
}

function formatTradeTooltip(trade) {
    const pnlText = trade.pnl !== 0 ? `\nP&L: $${trade.pnl.toFixed(2)}` : "";
    return `${trade.side.toUpperCase()} @ $${trade.price.toFixed(2)}\nQty: ${trade.quantity}${pnlText}`;
}
```

**Marker properties**:
- Buy entries: Green up arrow below bar
- Sell exits: Red down arrow above bar
- Tooltips show: Side, price, quantity, P&L (for exits)

---

## Testing Scenarios

### Manual Validation

1. **Start the web server**:
   ```bash
   uv run uvicorn src.api.web:app --reload
   ```

2. **Navigate to a backtest detail page** (e.g., `/backtests/1`)

3. **Verify chart renders**:
   - ✅ OHLCV candlesticks visible
   - ✅ Volume histogram at bottom
   - ✅ Trade markers appear at correct positions

4. **Verify trade markers**:
   - ✅ Green up arrows for buy entries
   - ✅ Red down arrows for sell exits
   - ✅ Markers positioned at correct price/time

5. **Verify tooltips**:
   - ✅ Hover over marker shows tooltip
   - ✅ Tooltip shows side, price, quantity
   - ✅ Exit markers show P&L

6. **Verify zoom/pan**:
   - ✅ Chart responds to mouse wheel zoom
   - ✅ Click and drag pans the view
   - ✅ Markers remain anchored during zoom/pan

### Playwright Integration Tests

Tests should be added to `tests/integration/ui/test_enhanced_chart.py`:

```python
import pytest
from playwright.sync_api import Page, expect

def test_chart_renders_with_trade_markers(page: Page, backtest_with_trades: int):
    """Test that chart renders with trade markers"""
    page.goto(f"http://localhost:8000/backtests/{backtest_with_trades}")

    # Wait for chart to render
    chart = page.locator('[data-chart="run-price"]')
    expect(chart).to_be_visible()

    # Check for canvas element (TradingView renders to canvas)
    canvas = chart.locator('canvas')
    expect(canvas).to_be_visible()

def test_buy_markers_display_correctly(page: Page, backtest_with_trades: int):
    """Test that buy markers appear as green up arrows"""
    page.goto(f"http://localhost:8000/backtests/{backtest_with_trades}")
    page.wait_for_selector('[data-chart="run-price"] canvas')
    # TradingView markers are part of canvas rendering
    # Visual validation may require screenshot comparison

def test_marker_tooltips_show_trade_details(page: Page, backtest_with_trades: int):
    """Test that hovering markers shows trade details"""
    page.goto(f"http://localhost:8000/backtests/{backtest_with_trades}")
    page.wait_for_selector('[data-chart="run-price"] canvas')
    # Note: TradingView tooltips may require specific hover coordinates
```

---

## Common Patterns

### Pattern 1: Adding Custom Chart Container

To add a price chart to a new page:

```html
<div id="my-chart"
     data-chart="run-price"
     data-run-id="{{ run_id }}"
     data-symbol="{{ symbol }}"
     data-start="{{ start }}"
     data-end="{{ end }}"
     class="w-full h-96">
    <div class="chart-loading">Loading...</div>
</div>
```

The chart system automatically initializes all `[data-chart]` elements on page load.

### Pattern 2: HTMX Integration

Charts auto-reinitialize after HTMX swaps:

```html
<div hx-get="/api/backtest/{{ id }}/chart-fragment"
     hx-target="#chart-area"
     hx-trigger="revealed">
    <!-- Chart container will be inserted here -->
</div>
```

The `htmx:afterSwap` event handler in `charts.js` reinitializes charts.

### Pattern 3: Custom Error Handling

Override error display by customizing `showError()` in `charts-core.js`:

```javascript
function showError(container, message) {
    hideLoading(container);
    const errorDiv = document.createElement("div");
    errorDiv.className = "error-container";
    errorDiv.innerHTML = `<p>Error: ${message}</p>`;
    container.appendChild(errorDiv);
}
```

---

## Troubleshooting

### Chart doesn't render

**Symptoms**: Chart container is blank or shows loading spinner forever

**Causes & Fixes**:
1. **Missing data attributes**: Ensure all required `data-*` attributes are present
2. **API errors**: Check browser console for fetch errors
3. **Script order**: Verify scripts load in correct order (core → modules → main)
4. **Container size**: Ensure container has explicit height

### Trade markers don't appear

**Symptoms**: Candlesticks render but no markers visible

**Causes & Fixes**:
1. **No trades**: Check `/api/trades/{run_id}` returns data
2. **Timestamp mismatch**: Verify trade times are within chart date range
3. **Time format**: Ensure API returns Unix timestamps (seconds, not milliseconds)

### Tooltips not showing

**Symptoms**: Markers appear but hover doesn't show tooltip

**Causes & Fixes**:
1. **TradingView version**: Ensure using v5+ which supports markers
2. **Marker text**: Check `text` property is set in marker object

---

## Performance Considerations

### Large Datasets

For backtests with 100k+ bars:
- Consider implementing data downsampling for zoomed-out views
- Load full resolution progressively as user zooms in

### Many Trade Markers

For backtests with 1000+ trades:
- Consider marker clustering when zoomed out
- Expand clusters on zoom in

**Note**: These optimizations are marked as future enhancements (T029).

---

## Resources

- **TradingView Lightweight Charts Docs**: https://tradingview.github.io/lightweight-charts/
- **Spec 008**: Chart APIs implementation details
- **Spec 009**: Trade tracking and P&L calculation
- **data-model.md**: Chart entity definitions

---

## Deferred Features

The following were originally planned but deferred to simplify scope:

- **Indicator overlays**: SMA, Bollinger Bands, RSI lines on price chart
- **Indicator visibility toggles**: Show/hide individual indicators
- **Separate indicator panes**: RSI in separate pane below price chart
- **Indicator tooltips**: Hover to see indicator values

**Rationale**: External charting tools (TradingView web, etc.) better serve indicator analysis. Trade markers provide the core insight needed for backtest review.

---

**Implementation Time**: ~4 hours for core functionality
**Testing Time**: ~2 hours for Playwright tests
**Total**: ~6 hours for complete feature delivery
