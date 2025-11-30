/**
 * NTrader Price Chart Module
 *
 * Implements candlestick price charts with trade entry/exit markers.
 * Trade markers show buy entries (green up arrows) and sell exits (red down arrows)
 * with tooltips displaying trade details including P&L.
 *
 * Dependencies: charts-core.js (must be loaded first)
 *
 * @module charts-price
 * @version 1.0.0
 */

/**
 * Fetches OHLCV candlestick data from the timeseries API
 *
 * @param {string} symbol - Trading symbol (e.g., "SPY.ARCA")
 * @param {string} start - Start date (YYYY-MM-DD)
 * @param {string} end - End date (YYYY-MM-DD)
 * @param {string} [timeframe="1_DAY"] - Bar timeframe
 * @returns {Promise<Object>} API response with candles array
 * @throws {Error} If API request fails
 */
async function fetchOHLCVData(symbol, start, end, timeframe = "1_DAY") {
    const url = `/api/timeseries?symbol=${symbol}&start=${start}&end=${end}&timeframe=${timeframe}`;
    const response = await fetch(url);

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Failed to load price data");
    }

    return await response.json();
}

/**
 * Fetches trade data for a backtest run
 *
 * @param {string} runId - Backtest run UUID
 * @returns {Promise<Object>} API response with trades array
 */
async function fetchTrades(runId) {
    const response = await fetch(`/api/trades/${runId}`);
    if (!response.ok) {
        return { trades: [] };
    }
    return await response.json();
}

/**
 * Creates candlestick series options
 *
 * @returns {Object} Candlestick series configuration
 */
function getCandlestickOptions() {
    return {
        upColor: CHART_COLORS.bullish,
        downColor: CHART_COLORS.bearish,
        borderVisible: false,
        wickUpColor: CHART_COLORS.bullish,
        wickDownColor: CHART_COLORS.bearish,
    };
}

/**
 * Creates volume series options
 *
 * @returns {Object} Volume histogram series configuration
 */
function getVolumeOptions() {
    return {
        color: CHART_COLORS.volume,
        priceFormat: { type: "volume" },
        priceScaleId: "",
    };
}

/**
 * Transforms raw candle data into candlestick format
 *
 * @param {Array<Object>} candles - Raw candle data from API
 * @returns {Array<Object>} Formatted candlestick data
 */
function formatCandleData(candles) {
    const deduplicated = deduplicateTimeseriesData(candles);
    return deduplicated.map((c) => ({
        time: c.time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
    }));
}

/**
 * Transforms raw candle data into volume histogram format
 *
 * @param {Array<Object>} candles - Raw candle data from API
 * @returns {Array<Object>} Formatted volume data with colors
 */
function formatVolumeData(candles) {
    const deduplicated = deduplicateTimeseriesData(candles);
    return deduplicated.map((c) => ({
        time: c.time,
        value: c.volume,
        color: c.close >= c.open
            ? CHART_COLORS.bullish + "60"
            : CHART_COLORS.bearish + "60",
    }));
}

/**
 * Formats a trade into a tooltip string
 *
 * @param {Object} trade - Trade object with side, price, quantity, pnl
 * @returns {string} Formatted tooltip text
 */
function formatTradeTooltip(trade) {
    const pnlText = trade.pnl !== 0 ? `\nP&L: $${trade.pnl.toFixed(2)}` : "";
    return `${trade.side.toUpperCase()} @ $${trade.price.toFixed(2)}\nQty: ${trade.quantity}${pnlText}`;
}

/**
 * Transforms trades into TradingView marker format
 *
 * @param {Array<Object>} trades - Array of trade objects
 * @returns {Array<Object>} Array of marker objects for TradingView
 */
function createTradeMarkers(trades) {
    return trades.map((t) => ({
        time: t.time,
        position: t.side === "buy" ? "belowBar" : "aboveBar",
        color: t.side === "buy" ? CHART_COLORS.bullish : CHART_COLORS.bearish,
        shape: t.side === "buy" ? "arrowUp" : "arrowDown",
        text: formatTradeTooltip(t),
    }));
}

/**
 * Sets up volume series with proper scale margins
 *
 * @param {IChartApi} chart - Chart instance
 * @returns {ISeriesApi} Volume series instance
 */
function setupVolumeSeries(chart) {
    const volumeSeries = chart.addSeries(
        LightweightCharts.HistogramSeries,
        getVolumeOptions()
    );
    volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
    });
    return volumeSeries;
}

/**
 * Initializes price chart with candlesticks and trade markers
 *
 * Displays OHLCV candlesticks with volume and trade entry/exit markers.
 * Trade markers show buy entries (green up arrows) and sell exits
 * (red down arrows) with tooltips showing trade details including P&L.
 *
 * @param {HTMLElement} container - Chart container element
 * @returns {Promise<void>}
 *
 * @example
 * // Container must have data attributes:
 * // data-run-id="uuid" data-symbol="SPY.ARCA"
 * // data-start="2024-01-01" data-end="2024-12-31"
 * const el = document.querySelector('[data-chart="run-price"]');
 * await initRunPriceChart(el);
 */
async function initRunPriceChart(container) {
    const { runId, symbol, start, end, timeframe = "1_DAY" } = container.dataset;

    try {
        // Fetch data in parallel
        const [timeseriesData, tradesData] = await Promise.all([
            fetchOHLCVData(symbol, start, end, timeframe),
            fetchTrades(runId),
        ]);

        hideLoading(container);

        // Validate data
        if (!timeseriesData.candles || timeseriesData.candles.length === 0) {
            showError(container, "No price data available for this date range");
            return;
        }

        // Create chart and series
        const chart = createChartWithDefaults(container);
        const candlestickSeries = chart.addSeries(
            LightweightCharts.CandlestickSeries,
            getCandlestickOptions()
        );
        candlestickSeries.setData(formatCandleData(timeseriesData.candles));

        // Add timeframe indicator badge
        createTimeframeBadge(container, timeseriesData.timeframe || timeframe);

        // Add volume
        const volumeSeries = setupVolumeSeries(chart);
        volumeSeries.setData(formatVolumeData(timeseriesData.candles));

        // Add trade markers
        if (tradesData.trades && tradesData.trades.length > 0) {
            const markers = createTradeMarkers(tradesData.trades);
            LightweightCharts.createSeriesMarkers(candlestickSeries, markers);
        }

        // Finalize
        chart.timeScale().fitContent();
        createResizeObserver(container, chart);

    } catch (error) {
        console.error("Error initializing price chart:", error);
        showError(container, error.message || "Failed to load chart");
    }
}

/**
 * Initializes data view chart for data catalog page
 *
 * @param {HTMLElement} container - Chart container with data attributes
 * @returns {Promise<void>}
 */
async function initDataViewChart(container) {
    const { symbol, start, end, timeframe = "1_DAY" } = container.dataset;

    try {
        const data = await fetchOHLCVData(symbol, start, end, timeframe);
        hideLoading(container);

        if (!data.candles || data.candles.length === 0) {
            showError(container, "No data available for this selection");
            return;
        }

        const chart = createChartWithDefaults(container);

        // Candlesticks
        const candlestickSeries = chart.addSeries(
            LightweightCharts.CandlestickSeries,
            getCandlestickOptions()
        );
        candlestickSeries.setData(formatCandleData(data.candles));

        // Add timeframe indicator badge
        createTimeframeBadge(container, data.timeframe || timeframe);

        // Volume
        const volumeSeries = setupVolumeSeries(chart);
        volumeSeries.setData(formatVolumeData(data.candles));

        chart.timeScale().fitContent();
        createResizeObserver(container, chart);

    } catch (error) {
        console.error("Error initializing data view chart:", error);
        showError(container, error.message || "Failed to load chart");
    }
}

// Export for global usage
if (typeof window !== "undefined") {
    window.initRunPriceChart = initRunPriceChart;
    window.initDataViewChart = initDataViewChart;
}
