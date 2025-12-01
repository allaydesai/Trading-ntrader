/**
 * NTrader Equity Chart Module
 *
 * Implements equity curve visualization showing account balance over time.
 * Supports both new trade-based equity curves and legacy equity data format.
 *
 * Dependencies: charts-core.js (must be loaded first)
 *
 * @module charts-equity
 * @version 1.0.0
 */

/**
 * Fetches equity curve data from the appropriate API endpoint
 *
 * @param {string} backtestId - Backtest UUID (new format)
 * @param {string} runId - Run UUID (legacy format)
 * @returns {Promise<Object>} Equity curve data
 * @throws {Error} If API request fails
 */
async function fetchEquityData(backtestId, runId) {
    const endpoint = backtestId
        ? `/api/equity-curve/${backtestId}`
        : `/api/equity/${runId}`;

    const response = await fetch(endpoint);

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "Failed to load equity data");
    }

    return await response.json();
}

/**
 * Converts equity curve points to chart format
 *
 * @param {Array<Object>} points - EquityCurvePoint objects with timestamp, balance
 * @returns {Array<Object>} Chart-compatible data with time (Unix) and value
 */
function formatEquityPoints(points) {
    return points.map((point) => {
        const timestamp = new Date(point.timestamp);
        return {
            time: Math.floor(timestamp.getTime() / 1000),
            value: parseFloat(point.balance),
        };
    });
}

/**
 * Creates equity summary watermark text
 *
 * @param {Object} data - Equity curve response data
 * @returns {string} Summary text for watermark display
 */
function createEquitySummary(data) {
    const initial = parseFloat(data.initial_capital).toFixed(2);
    const final = parseFloat(data.final_balance).toFixed(2);
    const returnPct = parseFloat(data.total_return_pct).toFixed(2);
    return `Initial: $${initial} | Final: $${final} | Return: ${returnPct}%`;
}

/**
 * Renders equity chart from new trade-based format
 *
 * @param {HTMLElement} container - Chart container
 * @param {Object} data - Equity curve data with points array
 */
function renderNewFormatEquity(container, data) {
    if (data.points.length === 0) {
        showError(container, "No trades executed - equity curve unavailable");
        return;
    }

    const chart = createChartWithDefaults(container);

    const equitySeries = chart.addSeries(LightweightCharts.LineSeries, {
        color: CHART_COLORS.equity,
        lineWidth: 2,
        title: "Account Balance",
    });

    equitySeries.setData(formatEquityPoints(data.points));

    chart.applyOptions({
        watermark: {
            visible: true,
            fontSize: 12,
            horzAlign: "left",
            vertAlign: "top",
            color: CHART_COLORS.text + "40",
            text: createEquitySummary(data),
        },
    });

    chart.timeScale().fitContent();
    createResizeObserver(container, chart);
}

/**
 * Renders equity chart from legacy format
 *
 * @param {HTMLElement} container - Chart container
 * @param {Object} data - Legacy equity data with equity and drawdown arrays
 */
function renderLegacyFormatEquity(container, data) {
    if (data.equity.length === 0) {
        showError(container, "No equity data available");
        return;
    }

    const chart = createChartWithDefaults(container);

    // Equity line
    const equitySeries = chart.addSeries(LightweightCharts.LineSeries, {
        color: CHART_COLORS.equity,
        lineWidth: 2,
        title: "Equity",
    });

    const equityData = data.equity.map((p) => ({
        time: p.time,
        value: p.value,
    }));
    equitySeries.setData(equityData);

    // Drawdown area (if available)
    if (data.drawdown && data.drawdown.length > 0) {
        const drawdownSeries = chart.addSeries(LightweightCharts.AreaSeries, {
            topColor: CHART_COLORS.bearish + "40",
            bottomColor: CHART_COLORS.bearish + "10",
            lineColor: CHART_COLORS.bearish,
            lineWidth: 1,
            title: "Drawdown %",
            priceScaleId: "drawdown",
        });

        chart.priceScale("drawdown").applyOptions({
            scaleMargins: { top: 0.7, bottom: 0 },
        });

        const drawdownData = data.drawdown.map((p) => ({
            time: p.time,
            value: Math.abs(p.value),
        }));
        drawdownSeries.setData(drawdownData);
    }

    chart.timeScale().fitContent();
    createResizeObserver(container, chart);
}

/**
 * Initializes equity curve chart based on individual trade tracking
 *
 * Supports both new trade-based equity curves (with points array)
 * and legacy format (with equity/drawdown arrays).
 *
 * @param {HTMLElement} container - Chart container element
 * @returns {Promise<void>}
 *
 * @example
 * // Container can have either data-backtest-id (new) or data-run-id (legacy):
 * // data-backtest-id="uuid" OR data-run-id="uuid"
 * const el = document.querySelector('[data-chart="run-equity"]');
 * await initEquityChart(el);
 */
async function initEquityChart(container) {
    const { runId, backtestId } = container.dataset;

    try {
        const data = await fetchEquityData(backtestId, runId);
        hideLoading(container);

        // Route to appropriate renderer based on data format
        if (data.points) {
            renderNewFormatEquity(container, data);
        } else if (data.equity) {
            renderLegacyFormatEquity(container, data);
        } else {
            throw new Error("Invalid equity curve response format");
        }

    } catch (error) {
        console.error("Error initializing equity chart:", error);
        showError(container, error.message || "Failed to load chart");
    }
}

// Export for global usage
if (typeof window !== "undefined") {
    window.initEquityChart = initEquityChart;
}
