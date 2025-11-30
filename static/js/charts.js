/**
 * NTrader Interactive Charts - Main Orchestrator
 *
 * Initializes and coordinates all chart modules for backtest visualization.
 * This file serves as the entry point that loads on page load and after HTMX swaps.
 *
 * Dependencies (load in order before this file):
 *   1. charts-core.js    - Core utilities and theme configuration
 *   2. charts-price.js   - Price chart with trade markers
 *   3. charts-equity.js  - Equity curve charts
 *   4. charts-statistics.js - Trade statistics and drawdown metrics
 *
 * Chart Types:
 *   - run-price: OHLCV candlesticks with trade entry/exit markers
 *   - run-equity: Account balance over time
 *   - data-view: Price data preview (data catalog page)
 *
 * @module charts
 * @version 2.0.0
 */

/**
 * Chart type to initialization function mapping
 * @constant {Object.<string, Function>}
 */
const CHART_INITIALIZERS = {
    "run-price": initRunPriceChart,
    "run-equity": initEquityChart,
    "data-view": initDataViewChart,
};

/**
 * Initializes a single chart element based on its data-chart attribute
 *
 * @param {HTMLElement} element - DOM element with data-chart attribute
 */
function initializeChart(element) {
    const chartType = element.dataset.chart;
    const initializer = CHART_INITIALIZERS[chartType];

    if (initializer) {
        initializer(element);
    } else {
        console.warn(`Unknown chart type: ${chartType}`);
    }
}

/**
 * Initializes all charts on the page
 *
 * Finds all elements with [data-chart] attribute and initializes
 * the appropriate chart type for each.
 */
function initCharts() {
    const chartElements = document.querySelectorAll("[data-chart]");
    chartElements.forEach(initializeChart);
}

/**
 * Handles HTMX swap events by reinitializing charts in swapped content
 *
 * @param {CustomEvent} event - HTMX afterSwap event
 */
function handleHtmxSwap(event) {
    const target = event.target;
    const chartElements = target.querySelectorAll("[data-chart]");
    chartElements.forEach(initializeChart);
}

/**
 * Initializes trade statistics component if present on page
 */
function initializeStatistics() {
    const statsContainer = document.getElementById("trade-statistics");
    if (statsContainer && typeof initTradeStatistics === "function") {
        initTradeStatistics(statsContainer);
    }
}

/**
 * Initializes drawdown metrics component if present on page
 */
function initializeDrawdownMetrics() {
    const drawdownContainer = document.getElementById("drawdown-metrics");
    if (drawdownContainer && typeof initDrawdownMetrics === "function") {
        initDrawdownMetrics(drawdownContainer);
    }
}

/**
 * Main initialization function called on DOMContentLoaded
 *
 * Initializes all charts and statistics components on the page.
 */
function initializeAll() {
    initCharts();
    initializeStatistics();
    initializeDrawdownMetrics();
}

// Initialize on DOM ready
document.addEventListener("DOMContentLoaded", initializeAll);

// Reinitialize after HTMX swaps
document.body.addEventListener("htmx:afterSwap", handleHtmxSwap);
