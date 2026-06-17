/**
 * NTrader Chart Core Module
 *
 * Core utilities, configuration, and shared functions for all chart types.
 * This module provides the foundation for TradingView Lightweight Charts integration.
 *
 * @module charts-core
 * @version 1.0.0
 */

/**
 * Chart color configuration matching Tailwind dark theme
 * @constant {Object}
 */
const CHART_COLORS = {
    background: "#020617",      // terminal black (slate-950)
    text: "#e5e7eb",            // slate-100
    gridLines: "#1e293b",       // hairline (slate-800)
    bullish: "#4ade80",         // profit green (green-400)
    bearish: "#f87171",         // loss red (red-400)
    equity: "#2563eb",          // action blue (blue-600) — DESIGN.md: equity lines are blue
    drawdown: "#f87171",        // loss red (red-400)
    volume: "#475569",          // slate-600
};

/**
 * Human-readable labels for API timeframe values
 * @constant {Object.<string, string>}
 */
const TIMEFRAME_LABELS = {
    "1_MIN": "1m",
    "5_MIN": "5m",
    "15_MIN": "15m",
    "1_HOUR": "1H",
    "1_DAY": "Daily",
};

/**
 * Creates a TradingView Lightweight Chart with dark theme defaults
 *
 * @param {HTMLElement} container - DOM element to render chart into
 * @returns {IChartApi} TradingView chart instance
 * @throws {Error} If container is null or undefined
 *
 * @example
 * const container = document.getElementById('chart');
 * const chart = createChartWithDefaults(container);
 */
function createChartWithDefaults(container) {
    if (!container) {
        throw new Error("Chart container is required");
    }

    return LightweightCharts.createChart(container, {
        layout: {
            background: { type: "solid", color: CHART_COLORS.background },
            textColor: CHART_COLORS.text,
        },
        grid: {
            vertLines: { color: CHART_COLORS.gridLines },
            horzLines: { color: CHART_COLORS.gridLines },
        },
        // Bar timestamps are true UTC epochs (see src/api/rest/explorer.py); render
        // the axis/crosshair in US exchange time so it matches TradingView and how
        // equity traders read the tape. Intl handles the EDT/EST (DST) switch.
        localization: {
            timeFormatter: (time) => formatExchangeTime(time, true),
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
            borderColor: CHART_COLORS.gridLines,
            tickMarkFormatter: (time) => formatExchangeTime(time, false),
        },
        rightPriceScale: {
            borderColor: CHART_COLORS.gridLines,
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
    });
}

/**
 * Hides the loading spinner element within a container
 *
 * @param {HTMLElement} container - Container with .chart-loading element
 */
function hideLoading(container) {
    const loadingEl = container.querySelector(".chart-loading");
    if (loadingEl) {
        loadingEl.style.display = "none";
    }
}

/**
 * Displays an error message in the chart container
 *
 * @param {HTMLElement} container - Container to show error in
 * @param {string} message - Error message to display
 */
function showError(container, message) {
    hideLoading(container);
    const errorDiv = document.createElement("div");
    errorDiv.className = "flex items-center justify-center h-full text-red-400 text-sm";
    errorDiv.innerHTML = `
        <div class="text-center">
            <svg class="h-8 w-8 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <p>${message}</p>
        </div>
    `;
    container.appendChild(errorDiv);
}

/**
 * Creates a ResizeObserver to handle chart container resizing
 *
 * @param {HTMLElement} container - Container element to observe
 * @param {IChartApi} chart - Chart instance to resize
 * @returns {ResizeObserver} The resize observer instance
 */
function createResizeObserver(container, chart) {
    const resizeObserver = new ResizeObserver(() => {
        chart.applyOptions({
            width: container.clientWidth,
            height: container.clientHeight,
        });
    });
    resizeObserver.observe(container);
    return resizeObserver;
}

/**
 * Formats a currency value for display
 *
 * @param {string|number} value - Numeric value to format
 * @returns {string} Formatted currency string (e.g., "$1,234.56")
 *
 * @example
 * formatCurrency(1234.5678) // Returns "$1,234.57"
 * formatCurrency("1000")    // Returns "$1,000.00"
 */
function formatCurrency(value) {
    const num = parseFloat(value);
    if (isNaN(num)) return "$0.00";
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(num);
}

/**
 * Formats an ISO 8601 timestamp for display
 *
 * @param {string} timestamp - ISO 8601 timestamp string
 * @returns {string} Formatted date/time (e.g., "Jan 15, 2025, 10:30 AM")
 *
 * @example
 * formatTimestamp("2025-01-15T10:30:00Z") // Returns "Jan 15, 2025, 10:30 AM"
 */
function formatTimestamp(timestamp) {
    if (!timestamp) return "N/A";
    const date = new Date(timestamp);
    return date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

/**
 * Formats a Lightweight Charts UTC epoch (seconds) in US exchange time.
 *
 * Bar timestamps stored/served as true UTC; this renders the axis and crosshair
 * in America/New_York (EDT/EST handled automatically) so charts line up with
 * TradingView and the way US equities are quoted.
 *
 * @param {number} time - Unix timestamp in seconds (UTC), as passed to the chart
 * @param {boolean} withDate - Include month/day (true for crosshair, false for axis ticks)
 * @returns {string} e.g. "Apr 27, 09:30" (crosshair) or "09:30" (axis tick)
 */
function formatExchangeTime(time, withDate) {
    const opts = {
        timeZone: "America/New_York",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
    };
    if (withDate) {
        opts.month = "short";
        opts.day = "numeric";
    }
    return new Date(time * 1000).toLocaleString("en-US", opts);
}

/**
 * Deduplicates and sorts time series data by timestamp
 *
 * @param {Array<Object>} data - Array of objects with 'time' property
 * @returns {Array<Object>} Deduplicated and sorted array
 */
function deduplicateTimeseriesData(data) {
    const seenTimes = new Set();
    return data
        .filter((item) => {
            if (seenTimes.has(item.time)) {
                return false;
            }
            seenTimes.add(item.time);
            return true;
        })
        .sort((a, b) => a.time - b.time);
}

/**
 * Creates a timeframe indicator badge in the chart container
 *
 * Displays a small badge in the top-left corner showing the current
 * chart timeframe (e.g., "Daily", "1H", "5m").
 *
 * @param {HTMLElement} container - Chart container element
 * @param {string} timeframe - API timeframe value (e.g., "1_DAY")
 * @returns {HTMLElement} The created badge element
 */
function createTimeframeBadge(container, timeframe) {
    const label = TIMEFRAME_LABELS[timeframe] || timeframe;
    const badge = document.createElement("div");
    badge.className = "absolute top-2 left-2 z-10 px-2 py-1 text-xs font-medium " +
        "bg-slate-800/80 text-slate-300 rounded border border-slate-700";
    badge.textContent = label;
    container.style.position = "relative";
    container.appendChild(badge);
    return badge;
}

/**
 * Formats a price value for the OHLC legend
 *
 * @param {number} value - Price value
 * @returns {string} Price rounded to 2 decimals, or "—" if not finite
 */
function formatOhlcPrice(value) {
    return Number.isFinite(value) ? value.toFixed(2) : "—";
}

/**
 * Attaches a fixed top-left OHLC legend that updates on crosshair move
 *
 * Builds an absolutely-positioned readout showing the Open/High/Low/Close of the
 * bar under the crosshair. TradingView Lightweight Charts has no built-in OHLC
 * legend, so this wires the documented `subscribeCrosshairMove` event and reads
 * the hovered bar via `param.seriesData.get(series)`. When the cursor is off the
 * data, the legend falls back to the most recent bar so it is never empty.
 *
 * @param {HTMLElement} container - Chart container element
 * @param {IChartApi} chart - Chart instance
 * @param {ISeriesApi} candleSeries - Candlestick series to read OHLC from
 * @param {Object} [options] - Display options
 * @param {string} [options.symbol] - Symbol label to prefix the legend
 * @param {string} [options.offsetTop="top-2"] - Tailwind top-* class (use to sit
 *     below an existing timeframe badge, e.g. "top-10")
 * @returns {HTMLElement} The created legend element
 */
function attachOhlcLegend(container, chart, candleSeries, options = {}) {
    const { symbol = "", offsetTop = "top-2" } = options;

    const legend = document.createElement("div");
    legend.className = `absolute ${offsetTop} left-2 z-10 px-2 py-1 text-xs ` +
        "font-mono bg-slate-800/80 text-slate-300 rounded border border-slate-700 " +
        "pointer-events-none whitespace-nowrap";
    container.style.position = "relative";
    container.appendChild(legend);

    const render = (bar) => {
        if (!bar) {
            legend.textContent = "";
            return;
        }
        const up = bar.close >= bar.open;
        const closeColor = up ? CHART_COLORS.bullish : CHART_COLORS.bearish;
        const prefix = symbol ? `<span class="text-slate-100 mr-1">${symbol}</span>` : "";
        legend.innerHTML = prefix +
            `<span class="text-slate-500">O</span> ${formatOhlcPrice(bar.open)} ` +
            `<span class="text-slate-500">H</span> ${formatOhlcPrice(bar.high)} ` +
            `<span class="text-slate-500">L</span> ${formatOhlcPrice(bar.low)} ` +
            `<span class="text-slate-500">C</span> ` +
            `<span style="color: ${closeColor}">${formatOhlcPrice(bar.close)}</span>`;
    };

    const latestBar = () => {
        const data = candleSeries.data();
        return data && data.length > 0 ? data[data.length - 1] : null;
    };

    render(latestBar());

    chart.subscribeCrosshairMove((param) => {
        const bar = (param && param.time) ? param.seriesData.get(candleSeries) : null;
        render(bar || latestBar());
    });

    return legend;
}

// Export for module usage (if using ES modules in future)
if (typeof window !== "undefined") {
    window.CHART_COLORS = CHART_COLORS;
    window.TIMEFRAME_LABELS = TIMEFRAME_LABELS;
    window.createChartWithDefaults = createChartWithDefaults;
    window.hideLoading = hideLoading;
    window.showError = showError;
    window.createResizeObserver = createResizeObserver;
    window.formatCurrency = formatCurrency;
    window.formatTimestamp = formatTimestamp;
    window.deduplicateTimeseriesData = deduplicateTimeseriesData;
    window.createTimeframeBadge = createTimeframeBadge;
    window.formatOhlcPrice = formatOhlcPrice;
    window.attachOhlcLegend = attachOhlcLegend;
}
